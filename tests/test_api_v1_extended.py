"""Integration and E2E tests for `API-3` `/api/v1/*` endpoints via Starlette + `wrap_web_app`."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from claire.api import security
from claire.api.accounts_routes import prepare_auth_db, prepare_client_db
from claire.api.v1 import V1_ROUTE_SPECS, build_v1_routes, generate_v1_openapi_json, generate_v1_openapi_spec, generate_v1_openapi_yaml
from claire.api.v1.errors import v1_exception_handlers
from claire.core import ids
from claire.core.config import Settings
from claire.extract.provider import get_provider
from claire.ingest.pipeline import IngestReport
from claire.ontology.base import Document
from claire.store import accounts as accounts_store, client_db, db as dbm, feeds as feeds_store, permalinks
from claire.store.knowledge_base import KnowledgeBaseManager
from tests.auth_helpers import issue_oauth_token, issue_session_token


class _FakeIngestService:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.provider = get_provider(settings)
        self.provider_context = None
        self.provider_error = None
        self.ingest_calls: list[tuple[str, dict[str, Any]]] = []

    def ingest(self, payload: str, **kwargs: Any) -> IngestReport:
        self.ingest_calls.append((payload, dict(kwargs)))
        mode = kwargs.get("extraction_mode", "provider")
        return IngestReport(
            document_id="doc_feed_ingest1",
            title=f"Feed: {payload[:32]}",
            source_type="feed",
            summary="Feed summary",
            entities_created=1,
            staged=(mode == "client"),
            extraction_status="pending" if mode == "client" else "extracted",
            raw_chars=len(payload),
        )


class _FakeServicePool:
    def __init__(self, svc: _FakeIngestService) -> None:
        self._svc = svc
        self.shared_provider = svc.provider

    def get_service(self, _kb_id: int, *, acting_account_id: int | None = None) -> _FakeIngestService:
        del acting_account_id
        return self._svc


@dataclass
class _ExtWorld:
    client: TestClient
    settings: Settings
    fake_svc: _FakeIngestService
    admin_session: str
    alice_session: str
    alice_stale_session: str
    bob_session: str
    alice_oauth_write: str
    alice_oauth_readonly: str
    bob_oauth_write: str
    alice_public_id: str
    bob_public_id: str
    alice_personal_number: str
    bob_personal_number: str
    shared_public_number: str
    alice_doc_id: str
    bob_doc_id: str
    feed_item_id: int


def _make_settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        _env_file=None,
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_FQDN="http://127.0.0.1:8765",
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CLAIRE_PLUGINS_DIR=str(plugins_dir),
        CLAIRE_CORS_ALLOWED_ORIGINS="",
        CLAIRE_DATA_LIFECYCLE="purgeable",
        CLAIRE_PROVIDER="mock",
    )


@pytest.fixture()
def ext_world(tmp_path: Path) -> Iterator[_ExtWorld]:
    s = _make_settings(tmp_path)

    conn = dbm.connect(s.db_file)
    try:
        dbm.init_db(conn)
    finally:
        conn.close()
    prepare_auth_db(s.data_dir)
    prepare_client_db(s.db_file)

    mgr = KnowledgeBaseManager(s)
    conn = dbm.connect_existing(s.db_file)
    try:
        acct_store = accounts_store.AccountStore(conn)
        acct_store.complete_setup(1, username="adminuser", display_name="Admin")
        admin = acct_store.admin()
        alice = acct_store.create_member(username="alice", display_name="Alice")
        bob = acct_store.create_member(username="bob", display_name="Bob")
        acct_store.set_setting("mode", "network", updated_by=admin.id)

        admin_id = admin.id
        alice_id = alice.id
        bob_id = bob.id

        alice_kb = mgr.define_kb("Alice Personal", is_personal=True, is_public=True, account_id=alice.id)
        bob_kb = mgr.define_kb("Bob Personal", is_personal=True, is_public=False, account_id=bob.id)
        shared_kb = mgr.define_kb("Shared Collab", description="Shared", knowledge_type="real", is_public=True, account_id=alice.id)

        alice_doc = "doc_aaaaaaaaaaaa"
        bob_doc = "doc_bbbbbbbbbbbb"
        for did, title, kb_id in (
            (alice_doc, "Alice Public Article", alice_kb.id),
            (bob_doc, "Bob Private Article", bob_kb.id),
        ):
            dbm.insert_document(
                conn,
                Document(
                    id=did,
                    title=title,
                    url=f"https://example.test/{did}",
                    canonical_url=f"https://example.test/{did}",
                    source_type="web",
                    raw_text=f"Source content for {title}.",
                    fetched_at=150.0,
                ),
                kb_id=kb_id,
            )
            dbm.set_document_detail(conn, did, f"= {title}\n\nPublished body text.", format="adoc", html=f"<p>{title}</p>", kb_id=kb_id)
            dbm.log_extraction(
                conn,
                document_id=did,
                provider="mock",
                model="mock",
                prompt_version="v1",
                raw_response=json.dumps({"summary": f"Summary of {title}"}),
                kb_id=kb_id,
            )
            permalinks.ensure_document(conn, did, kb_id)
        conn.commit()
    finally:
        conn.close()

    cconn = client_db.connect_client_existing(client_db.client_db_path(s.db_file))
    try:
        feed, _ = feeds_store.add_feed(cconn, alice.id, "https://example.test/feed.xml", title="Example Feed", folder="Tech", now=100.0)
        cconn.execute(
            "INSERT INTO feed_items (account_id, feed_id, item_key, url, title, summary, fetched_at, unread, starred) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0)",
            (alice.id, int(feed["id"]), "guid-1", "https://example.test/post-1", "Feed Post 1", "Summary 1", 101.0),
        )
        feed_item_id = int(cconn.execute("SELECT last_insert_rowid()").fetchone()[0])
        cconn.commit()
    finally:
        cconn.close()

    admin_session = issue_session_token(s, account_id=admin_id, scope="owner")
    alice_session = issue_session_token(s, account_id=alice_id, scope="owner")
    alice_stale_session = issue_session_token(s, account_id=alice_id, scope="owner", authenticated_ago=3600.0)
    bob_session = issue_session_token(s, account_id=bob_id, scope="owner")
    alice_oauth_write = issue_oauth_token(s, account_id=alice_id, scope="write", client_id="agent-client-1")
    alice_oauth_readonly = issue_oauth_token(s, account_id=alice_id, scope="readonly", client_id="reader-client-1")
    bob_oauth_write = issue_oauth_token(s, account_id=bob_id, scope="write", client_id="agent-client-2")

    fake_svc = _FakeIngestService(s)
    fake_pool = _FakeServicePool(fake_svc)
    v1_routes = build_v1_routes(s, service=fake_svc, theme_manager=mgr, service_pool=fake_pool)
    inner = Starlette(routes=v1_routes, exception_handlers=v1_exception_handlers())
    wrapped = security.wrap_web_app(inner, s, theme_manager=mgr)
    client = TestClient(wrapped, base_url="http://127.0.0.1:8765")
    yield _ExtWorld(
        client=client,
        settings=s,
        fake_svc=fake_svc,
        admin_session=admin_session,
        alice_session=alice_session,
        alice_stale_session=alice_stale_session,
        bob_session=bob_session,
        alice_oauth_write=alice_oauth_write,
        alice_oauth_readonly=alice_oauth_readonly,
        bob_oauth_write=bob_oauth_write,
        alice_public_id=ids.format_public_id(alice.public_id),
        bob_public_id=ids.format_public_id(bob.public_id),
        alice_personal_number=ids.format_public_id(int(alice_kb.number or 0)),
        bob_personal_number=ids.format_public_id(int(bob_kb.number or 0)),
        shared_public_number=ids.format_public_id(int(shared_kb.number or 0)),
        alice_doc_id=alice_doc,
        bob_doc_id=bob_doc,
        feed_item_id=feed_item_id,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _cookie(token: str) -> dict[str, str]:
    return {"Cookie": f"claire_session={token}", "Origin": "http://127.0.0.1:8765"}


def test_shared_kb_collaboration_members_contributions_and_step_up(ext_world: _ExtWorld) -> None:
    kb = ext_world.shared_public_number

    # Collaboration routes reject personal KBs with 404.
    r_pers = ext_world.client.get("/api/v1/kbs/personal/contributions", headers=_bearer(ext_world.alice_oauth_readonly))
    assert r_pers.status_code == 404

    # Bob requests to join (approval policy -> 201 pending).
    r_join = ext_world.client.post(f"/api/v1/kbs/{kb}/members", headers=_cookie(ext_world.bob_session))
    assert r_join.status_code == 201
    assert r_join.json()["membership"]["state"] == "pending"

    # Bob cannot contribute yet while pending (403).
    ext_world.client.cookies.clear()
    r_denied_contrib = ext_world.client.post(
        f"/api/v1/kbs/{kb}/contributions",
        json={"document_id": ext_world.bob_doc_id},
        headers=_bearer(ext_world.bob_oauth_write),
    )
    assert r_denied_contrib.status_code == 403

    # Alice (moderator) approves Bob's membership.
    r_approve = ext_world.client.patch(
        f"/api/v1/kbs/{kb}/members/{ext_world.bob_public_id}",
        json={"action": "approve"},
        headers=_cookie(ext_world.alice_session),
    )
    assert r_approve.status_code == 200
    assert r_approve.json()["member"]["state"] == "active"

    # Role change requires recent session sign-in (step-up). Stale session fails with 401 reauth_required.
    ext_world.client.cookies.clear()
    r_stale = ext_world.client.patch(
        f"/api/v1/kbs/{kb}/members/{ext_world.bob_public_id}",
        json={"role": "collaborator"},
        headers=_cookie(ext_world.alice_stale_session),
    )
    assert r_stale.status_code == 401
    assert r_stale.json()["error"]["code"] == "reauth_required"

    # Fresh session succeeds in promoting Bob to collaborator.
    ext_world.client.cookies.clear()
    r_promote = ext_world.client.patch(
        f"/api/v1/kbs/{kb}/members/{ext_world.bob_public_id}",
        json={"role": "collaborator", "reason": "trusted_editor"},
        headers=_cookie(ext_world.alice_session),
    )
    assert r_promote.status_code == 200
    assert r_promote.json()["member"]["role"] == "collaborator"

    # Alice lists members (moderator sees full list with pagination).
    r_members = ext_world.client.get(f"/api/v1/kbs/{kb}/members?limit=1", headers=_cookie(ext_world.alice_session))
    assert r_members.status_code == 200
    assert r_members.json()["total"] == 2
    assert r_members.json()["next_cursor"] is not None

    # Bob contributes his personal document using OAuth 2.1 write scope.
    ext_world.client.cookies.clear()
    r_contrib = ext_world.client.post(
        f"/api/v1/kbs/{kb}/contributions",
        json={"document_id": ext_world.bob_doc_id},
        headers=_bearer(ext_world.bob_oauth_write),
    )
    assert r_contrib.status_code == 201
    c_num = r_contrib.json()["contribution"]["number"]

    # List contributions.
    r_list_c = ext_world.client.get(f"/api/v1/kbs/{kb}/contributions", headers=_bearer(ext_world.alice_oauth_readonly))
    assert r_list_c.status_code == 200
    assert r_list_c.json()["total"] >= 1

    # Moderator hides then removes contribution; last moderator cannot leave (409).
    r_hide = ext_world.client.patch(
        f"/api/v1/kbs/{kb}/contributions/{c_num}",
        json={"action": "hide"},
        headers=_cookie(ext_world.alice_session),
    )
    assert r_hide.status_code == 200
    assert r_hide.json()["contribution"]["state"] == "hidden"

    r_remove = ext_world.client.post(
        f"/api/v1/kbs/{kb}/contributions/{c_num}/remove",
        json={"reason": "off_subject"},
        headers=_cookie(ext_world.alice_session),
    )
    assert r_remove.status_code == 200
    assert r_remove.json()["removed"] is True

    r_mod_leave = ext_world.client.delete(f"/api/v1/kbs/{kb}/members", headers=_cookie(ext_world.alice_session))
    assert r_mod_leave.status_code == 409
    assert r_mod_leave.json()["error"]["code"] == "last_moderator"

    # Bob leaves cleanly.
    ext_world.client.cookies.clear()
    r_bob_leave = ext_world.client.delete(f"/api/v1/kbs/{kb}/members", headers=_cookie(ext_world.bob_session))
    assert r_bob_leave.status_code == 200
    assert r_bob_leave.json()["left"] is True


def test_client_extraction_ct4_isolation_and_lifecycle(ext_world: _ExtWorld) -> None:
    # Public spec requires no credentials.
    r_spec = ext_world.client.get("/api/v1/extraction/spec")
    assert r_spec.status_code == 200
    assert r_spec.json()["schema_version"] == "agent-extract-v1"

    # Readonly token cannot access write-only extraction stage (403 scope_insufficient via ROUTE_POLICY).
    r_ro = ext_world.client.post(
        "/api/v1/extraction/stage",
        json={"url_or_text": "Note for staging"},
        headers=_bearer(ext_world.alice_oauth_readonly),
    )
    assert r_ro.status_code == 403

    # CT4: attempting to stage into a shared KB is rejected with 403 kb_ingest_by_contribution.
    r_foreign_shared = ext_world.client.post(
        f"/api/v1/extraction/stage?kb={ext_world.shared_public_number}",
        json={"url_or_text": "Should fail"},
        headers=_bearer(ext_world.alice_oauth_write),
    )
    assert r_foreign_shared.status_code == 403
    assert r_foreign_shared.json()["error"]["code"] == "kb_ingest_by_contribution"

    # CT4: attempting to stage into Bob's personal KB is hidden as 404.
    r_foreign_pers = ext_world.client.post(
        f"/api/v1/extraction/stage?kb={ext_world.bob_personal_number}",
        json={"url_or_text": "Should fail"},
        headers=_bearer(ext_world.alice_oauth_write),
    )
    assert r_foreign_pers.status_code == 404

    # Stage text in Alice's personal KB.
    r_stage = ext_world.client.post(
        "/api/v1/extraction/stage",
        json={"url_or_text": "Kubernetes uses etcd for cluster state storage.", "title": "K8s Storage"},
        headers=_bearer(ext_world.alice_oauth_write),
    )
    assert r_stage.status_code == 200
    staged_doc = r_stage.json()["document_id"]
    content_hash = r_stage.json()["content_hash"]

    # List staged sources.
    r_list = ext_world.client.get("/api/v1/extraction/sources?status=pending", headers=_bearer(ext_world.alice_oauth_write))
    assert r_list.status_code == 200
    assert any(item["document_id"] == staged_doc for item in r_list.json()["items"])

    # Claim, renew, read nonce-framed content, dry-run submit, and commit submit.
    r_claim = ext_world.client.post(
        f"/api/v1/extraction/sources/{staged_doc}/claim",
        json={"client_id": "agent-1", "lease_seconds": 120},
        headers=_bearer(ext_world.alice_oauth_write),
    )
    assert r_claim.status_code == 200
    claim_token = r_claim.json()["claim_token"]

    r_renew = ext_world.client.post(
        f"/api/v1/extraction/sources/{staged_doc}/renew",
        json={"claim_token": claim_token, "lease_seconds": 240},
        headers=_bearer(ext_world.alice_oauth_write),
    )
    assert r_renew.status_code == 200

    r_src = ext_world.client.get(
        f"/api/v1/extraction/sources/{staged_doc}?claim_token={claim_token}",
        headers=_bearer(ext_world.alice_oauth_write),
    )
    assert r_src.status_code == 200
    assert "<untrusted_source_document" in r_src.json()["framed_content"]

    extraction_payload = {
        "schema_version": "agent-extract-v1",
        "summary": "Kubernetes stores cluster state in etcd.",
        "detail": "= K8s Storage\n\nKubernetes uses etcd for distributed state.",
        "entities": [
            {"name": "Kubernetes", "type": "Tool", "aliases": ["K8s"], "observations": ["Container orchestrator"]},
            {"name": "etcd", "type": "Tool", "aliases": [], "observations": ["Distributed key-value store"]},
        ],
        "relations": [{"source": "Kubernetes", "target": "etcd", "type": "uses", "confidence": 0.9}],
        "model": "gemini-3.8-flash",
    }
    r_dry = ext_world.client.post(
        f"/api/v1/extraction/sources/{staged_doc}/submit",
        json={"claim_token": claim_token, "expected_content_hash": content_hash, "dry_run": True, "payload": extraction_payload},
        headers=_bearer(ext_world.alice_oauth_write),
    )
    assert r_dry.status_code == 200
    assert r_dry.json()["dry_run"] is True

    r_commit = ext_world.client.post(
        f"/api/v1/extraction/sources/{staged_doc}/submit",
        json={"claim_token": claim_token, "expected_content_hash": content_hash, "dry_run": False, "payload": extraction_payload},
        headers=_bearer(ext_world.alice_oauth_write),
    )
    assert r_commit.status_code == 200
    assert r_commit.json()["committed"] is True
    assert r_commit.json()["extraction_origin"] == "agent_client"


def test_federation_and_feeds_endpoints(ext_world: _ExtWorld) -> None:
    # Federation status requires owner scope.
    r_write_status = ext_world.client.get("/api/v1/federation/status", headers=_bearer(ext_world.alice_oauth_write))
    assert r_write_status.status_code == 404

    r_status = ext_world.client.get("/api/v1/federation/status", headers=_cookie(ext_world.alice_session))
    assert r_status.status_code == 200
    assert r_status.json()["handle"] == "@alice@127.0.0.1:8765"
    assert r_status.json()["is_actor"] is True

    # Publish personal public KB document and retract it.
    ext_world.client.cookies.clear()
    r_pub = ext_world.client.post(
        "/api/v1/federation/publish",
        json={"kb": "personal", "document_id": ext_world.alice_doc_id},
        headers=_bearer(ext_world.alice_oauth_write),
    )
    assert r_pub.status_code == 202
    obj_id = r_pub.json()["object"]["id"]

    r_retract = ext_world.client.post(
        "/api/v1/federation/retract",
        json={"object_id": obj_id},
        headers=_bearer(ext_world.alice_oauth_write),
    )
    assert r_retract.status_code == 202
    assert r_retract.json()["retracted"] is True

    # Feeds CRUD, items list/patch, and feed item ingest.
    r_feeds = ext_world.client.get("/api/v1/feeds", headers=_cookie(ext_world.alice_session))
    assert r_feeds.status_code == 200
    assert r_feeds.json()["total"] == 1

    r_add_feed = ext_world.client.post(
        "/api/v1/feeds",
        json={"url": "https://example.test/rss.xml", "title": "Second Feed", "folder": "News"},
        headers=_cookie(ext_world.alice_session),
    )
    assert r_add_feed.status_code == 201
    new_feed_id = r_add_feed.json()["feed"]["id"]

    r_items = ext_world.client.get("/api/v1/feeds/items?unread=true", headers=_cookie(ext_world.alice_session))
    assert r_items.status_code == 200
    assert len(r_items.json()["items"]) == 1

    r_mark = ext_world.client.patch(
        "/api/v1/feeds/items",
        json={"item_ids": [ext_world.feed_item_id], "starred": True},
        headers=_cookie(ext_world.alice_session),
    )
    assert r_mark.status_code == 200
    assert r_mark.json()["changed"] == 1

    # Ingest feed item with extraction_mode="client".
    r_ingest = ext_world.client.post(
        f"/api/v1/feeds/items/{ext_world.feed_item_id}/ingest",
        json={"extraction_mode": "client"},
        headers=_cookie(ext_world.alice_session),
    )
    assert r_ingest.status_code == 200
    assert r_ingest.json()["report"]["staged"] is True
    assert r_ingest.json()["linked"]["document_id"] == "doc_feed_ingest1"

    r_del_feed = ext_world.client.delete(
        f"/api/v1/feeds?feed_id={new_feed_id}",
        headers=_cookie(ext_world.alice_session),
    )
    assert r_del_feed.status_code == 200
    assert r_del_feed.json()["removed"] is True


def test_oauth_grants_support_bundles_and_full_openapi_parity(ext_world: _ExtWorld) -> None:
    # Account support bundles: Alice can manage account bundles, gets 404 for server bundles.
    r_srv_forbidden = ext_world.client.post(
        "/api/v1/support/bundles",
        json={"scope": "server", "days": 1},
        headers=_cookie(ext_world.alice_session),
    )
    assert r_srv_forbidden.status_code == 404

    r_acct_bundle = ext_world.client.post(
        "/api/v1/support/bundles",
        json={"scope": "account", "days": 1},
        headers=_cookie(ext_world.alice_session),
    )
    assert r_acct_bundle.status_code == 201
    acct_bid = r_acct_bundle.json()["bundle"]["bundle_id"]

    r_list_bundles = ext_world.client.get("/api/v1/support/bundles", headers=_cookie(ext_world.alice_session))
    assert r_list_bundles.status_code == 200
    assert any(b["bundle_id"] == acct_bid for b in r_list_bundles.json()["items"])

    r_del_bundle = ext_world.client.delete(
        f"/api/v1/support/bundles/{acct_bid}",
        headers=_cookie(ext_world.alice_session),
    )
    assert r_del_bundle.status_code == 200
    assert r_del_bundle.json()["deleted"] is True

    # Admin can create and delete a server-scope support bundle.
    ext_world.client.cookies.clear()
    r_srv_bundle = ext_world.client.post(
        "/api/v1/support/bundles",
        json={"scope": "server", "days": 1},
        headers=_cookie(ext_world.admin_session),
    )
    assert r_srv_bundle.status_code == 201
    srv_bid = r_srv_bundle.json()["bundle"]["bundle_id"]
    assert ext_world.client.delete(f"/api/v1/support/bundles/{srv_bid}", headers=_cookie(ext_world.admin_session)).status_code == 200

    # OAuth grants list & revoke.
    ext_world.client.cookies.clear()
    r_grants = ext_world.client.get("/api/v1/me/oauth/grants", headers=_cookie(ext_world.alice_session))
    assert r_grants.status_code == 200
    grants = r_grants.json()["items"]
    assert len(grants) == 2

    r_revoke = ext_world.client.delete(
        f"/api/v1/me/oauth/grants?grant_id={grants[0]['grant_id']}",
        headers=_cookie(ext_world.alice_session),
    )
    assert r_revoke.status_code == 200
    assert r_revoke.json()["revoked"] >= 1

    # Verify full OpenAPI 3.1 parity across all API-1/2/3 routes + system PyYAML validity.
    spec = generate_v1_openapi_spec()
    ops = [op for path_item in spec["paths"].values() for op in path_item.values()]
    assert len(ops) == len(V1_ROUTE_SPECS)
    assert all(bool(op.get("tags")) for op in ops)

    yaml_text = generate_v1_openapi_yaml()
    json_text = generate_v1_openapi_json()
    repo_root = str(Path(__file__).resolve().parents[1])
    proc = subprocess.run(
        ["python3", "-c", "import json, sys, yaml; print(json.dumps(yaml.safe_load(sys.stdin.read()), sort_keys=True))"],
        input=yaml_text,
        text=True,
        capture_output=True,
        check=True,
        cwd=repo_root,
    )
    assert json.loads(proc.stdout) == json.loads(json_text)
