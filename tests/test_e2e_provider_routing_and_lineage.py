"""End-to-end HTTP workflow tests for admin feature routing, lineage catalog, and member BYOK baseline policy."""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from claire.core import secret_scrub
from claire.api import server
from claire.core.config import Settings
from claire.store import accounts as accounts_store
from claire.store import auth_db, provider_keys
from claire.store import db as dbm
from claire.store.secrets import BYOK_SEAL_COOKIE, clear_volatile_user_seals, reset_secret_service
from claire.store.knowledge_base import KnowledgeBaseManager

PUBLIC_URL = "http://127.0.0.1:8765"
ORIGIN = PUBLIC_URL
OWNER_TOKEN = "owner-lineage-" + "x" * 24
TEST_MASTER_KEY_HEX = "33" * 32
BYOK_CANARY = "canary-e2e-byok-key-901234567890"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("CLAIRE_") or key.startswith("GEMINI_") or key.startswith("TELEGRAM_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAIRE_SECRET_KEY", TEST_MASTER_KEY_HEX)
    reset_secret_service()
    secret_scrub._reset_for_tests()
    monkeypatch.setattr(provider_keys, "verify_key", lambda provider, api_key: (True, "ok"))
    yield
    reset_secret_service()
    secret_scrub._reset_for_tests()


def _create_session(db_file: Path, account_id: int) -> str:
    conn = dbm.connect_existing(db_file)
    try:
        return accounts_store.create_account_session(conn, account_id=account_id, scope="owner", now=time.time())
    finally:
        conn.close()


def test_e2e_admin_routing_lineage_and_member_byok_baseline_flow(tmp_path: Path) -> None:
    """Verify full HTTP E2E flow across admin /providers and member /account/providers."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_file = data_dir / "claire.db"
    conn = dbm.connect(db_file)
    try:
        dbm.init_db(conn)
        accounts_store.AccountStore(conn).complete_setup(1, username="admin_e2e", display_name="Admin")
        now = time.time()
        conn.execute(
            "INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) VALUES (2, 223456789088, 'member_e2e', 'member', 'active', 'Member', 0, ?, ?, ?)",
            (now, now, now),
        )
        accounts_store.AccountStore(conn).set_setting("mode", "network", updated_by=1)
        conn.commit()
    finally:
        conn.close()

    auth = auth_db.connect_auth_db(auth_db.auth_db_path(data_dir))
    try:
        auth_db.set_password(auth, 1, "Correct-Horse-Battery-123!", username="admin_e2e")
    finally:
        auth.close()

    settings = Settings(
        _env_file=None,
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_DATA_DIR=str(data_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL=PUBLIC_URL,
        CLAIRE_INJECT_TOKEN=OWNER_TOKEN,
        CLAIRE_MULTI_THEME=True,
    )
    KnowledgeBaseManager(settings)
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="mock")))

    with TestClient(app, base_url=PUBLIC_URL) as client:
        owner_headers = {"Authorization": f"Bearer {OWNER_TOKEN}", "Origin": ORIGIN}
        admin_sess = _create_session(db_file, 1)
        admin_cookie_headers = {"Cookie": f"claire_session={admin_sess}", "Origin": ORIGIN}
        member_sess = _create_session(db_file, 2)
        member_headers = {"Cookie": f"claire_session={member_sess}", "Origin": ORIGIN}

        # 0. Server-wide /providers rejects the retired host-cli providers. The personal CLI routes of the administrator do not exist (404 for each account).
        cli_reject = client.patch(
            "/providers",
            headers=owner_headers,
            json={"active_provider": "antigravity"},
        )
        assert cli_reject.status_code == 400
        assert client.get("/api/me/personal-cli", headers=member_headers).status_code == 404
        assert client.get("/api/me/personal-cli", headers=admin_cookie_headers).status_code == 404
        assert client.patch("/api/me/personal-cli", headers=admin_cookie_headers, json={"enabled": True, "active_cli": "codex"}).status_code == 404
        assert client.post("/api/me/personal-cli/test", headers=admin_cookie_headers, json={"provider": "codex"}).status_code == 404
        admin_keys_html = client.get("/account/providers", headers={"Cookie": f"claire_session={admin_sess}"})
        assert admin_keys_html.status_code == 200
        assert "admin-personal-cli-host" not in admin_keys_html.text
        client.cookies.clear()

        # 1. Admin configures Google AI Studio key, 3.8 baseline, and a separated summarize slot. The old Jev mode keys have no effect (Jev is shadow only, owner decision 2026-11-20). /providers ignores a server tier: /admin/server (PUT /api/admin/server-tier) edits it.
        patch_resp = client.patch(
            "/providers",
            headers=owner_headers,
            json={
                "active_provider": "gemini",
                "providers": {
                    "gemini": {
                        "api_key": "AIzaAdminKeyForBaseline1234567890",
                        "model": "gemini-3.8-flash",
                        "effort": "medium",
                        "embed_model": "gemini-embedding-001",
                    },
                    "jev": {
                        "enabled": True,
                        "mode": "filter",
                        "filter_threshold": 0.20,
                        "auto_merge_threshold": 0.90,
                    },
                },
                "feature_routing": {
                    "extract_graph": {"provider": "inherit", "model": "", "effort": ""},
                    "summarize": {"provider": "gemini", "model": "gemini-4-argon", "effort": "high"},
                    "render_detail": {"provider": "gemini", "model": "gemini-3.8-flash-high"},
                },
                "server_tier": {
                    "provider": "gemini",
                    "model": "gemini-3.8-flash",
                    "max_effort": "medium",
                    "features": ["expand", "stt"],
                },
            },
        )
        assert patch_resp.status_code == 200, patch_resp.text
        cfg = patch_resp.json()["config"]
        assert not {"mode", "filter_threshold", "auto_merge_threshold"} & set(cfg["providers"]["jev"])
        assert cfg["lineage_summary"]["jev"]["role"] == "shadow"
        assert cfg["feature_routing"]["summarize"]["model"] == "gemini-4-argon"
        assert cfg["server_tier"]["provider"] == "none"
        # What applies now (provider_view): the default model, the summarize override, STT fixed to Google AI Studio.
        assert cfg["effective"]["model"] == "gemini-3.8-flash"
        assert {r["slot"]: r["overridden"] for r in cfg["effective"]["slots"]}["summarize"] is True
        assert cfg["effective"]["stt"]["model"] == "gemini-3.5-transcribe"
        assert "stt" not in cfg["feature_routing"] and "vlm" not in cfg["feature_routing"]
        tier_resp = client.put(
            "/api/admin/server-tier",
            headers=admin_cookie_headers,
            json={"provider": "gemini", "model": "gemini-3.8-flash", "max_effort": "medium", "features": ["expand", "stt"]},
        )
        assert tier_resp.status_code == 200, tier_resp.text
        tier_now = client.get("/api/admin/server-tier", headers=admin_cookie_headers).json()["tier"]
        client.cookies.clear()
        assert (tier_now["provider"], tier_now["model"], set(tier_now["features"])) == ("gemini", "gemini-3.8-flash", {"expand", "stt"})
        assert cfg["lineage_summary"]["effective_slots"]["extract_graph"]["model"] == "gemini-3.8-flash"
        assert cfg["lineage_summary"]["effective_slots"]["summarize"]["model"] == "gemini-4-argon"
        assert cfg["lineage_summary"]["effective_slots"]["render_detail"]["model"] == "gemini-3.8-flash-high"
        assert cfg["lineage_summary"]["effective_slots"]["render_detail"]["effort"] == "high"
        assert len(cfg["lineage_summary"]["architecture_specs"]) >= 4
        assert any(f["id"] == "F2" and f["status"] == "REMOVED" for f in cfg["lineage_summary"]["fallbacks"])
        assert not [f for f in cfg["lineage_summary"]["fallbacks"] if "antigravity" in f["location"] or "codex" in f["location"]]
        assert any(f["id"] == "F10" and f["status"] == "OUT_OF_SCOPE" for f in cfg["lineage_summary"]["fallbacks"])

        # 2. Member fetches /account/providers HTML and /api/me/provider-keys?include_policy=1 without exposing admin routes.
        page_resp = client.get("/account/providers", headers={"Cookie": f"claire_session={member_sess}"})
        assert page_resp.status_code == 200
        # Without a key the member uses the server tier: the page tells it and shows the model of the tier.
        assert 'id="settings-effective"' in page_resp.text
        assert 'data-effective-source="tier"' in page_resp.text
        assert "gemini-3.8-flash" in page_resp.text
        assert "data-byok-model-select" in page_resp.text
        assert "data-byok-effort-select" not in page_resp.text
        assert "/admin/" not in page_resp.text

        policy_resp = client.get("/api/me/provider-keys?include_policy=1", headers={"Cookie": f"claire_session={member_sess}"})
        assert policy_resp.status_code == 200
        policy = policy_resp.json()["model_policy"]
        # The default of a key is the main model for each feature: the feature routing of the server applies to the server jobs only.
        assert policy["admin_defaults"]["extract_graph"]["model"] == "gemini-3.8-flash"
        assert policy["admin_defaults"]["summarize"]["model"] == "gemini-3.8-flash"
        assert policy["admin_defaults"]["render_detail"]["model"] == "gemini-3.8-flash"
        assert "gemini-4-argon-high" in policy["allowed_models"]
        assert "gemini-4-argon" not in policy["allowed_models"]

        # 3. Member is rejected for mock and for an unknown model. A model below the administrator default is accepted: the administrator values are only defaults.
        bad_mock = client.put("/api/me/provider-keys", headers=member_headers, json={"provider": "mock", "api_key": BYOK_CANARY})
        assert bad_mock.status_code == 400
        bad_model = client.put("/api/me/provider-keys", headers=member_headers, json={"provider": "gemini", "api_key": BYOK_CANARY, "model": "mock"})
        assert bad_model.status_code == 400 and bad_model.json()["error"] == "unsupported_model"

        lower = client.put(
            "/api/me/provider-keys",
            headers=member_headers,
            json={"provider": "gemini", "api_key": BYOK_CANARY, "model": "gemini-3.5-flash-lite-medium"},
        )
        assert lower.status_code == 200, lower.text
        assert lower.json()["model_prefs"] == {"model": "gemini-3.5-flash-lite-medium", "effort": "medium"}

        mismatch_eff = client.put(
            "/api/me/provider-keys",
            headers=member_headers,
            json={"provider": "gemini", "api_key": BYOK_CANARY, "model": "gemini-4-argon-high", "effort": "low"},
        )
        assert mismatch_eff.status_code == 400

        # 4. Member registers an enumerated model without separate effort. The key has no browser seal, thus no seal cookie.
        ok_put = client.put(
            "/api/me/provider-keys",
            headers=member_headers,
            json={"provider": "gemini", "api_key": BYOK_CANARY, "model": "gemini-4-argon-high"},
        )
        assert ok_put.status_code == 200, ok_put.text
        assert ok_put.cookies.get(BYOK_SEAL_COOKIE) is None
        byok_page = client.get("/account/providers", headers={"Cookie": f"claire_session={member_sess}"}).text
        assert 'data-effective-source="byok"' in byok_page
        assert "gemini-4-argon-high" in byok_page

        # 4a. A change of the model only (PATCH): no key in the request, the key stays, the page shows the new model.
        bad_patch = client.patch("/api/me/provider-keys", headers=member_headers, json={"provider": "gemini", "model": "mock"})
        assert bad_patch.status_code == 400 and bad_patch.json()["error"] == "unsupported_model"
        model_only = client.patch("/api/me/provider-keys", headers=member_headers, json={"provider": "gemini", "model": "gemini-3.8-flash-low"})
        assert model_only.status_code == 200, model_only.text
        assert model_only.json()["model_prefs"] == {"model": "gemini-3.8-flash-low", "effort": "low"}
        assert "gemini-3.8-flash-low" in client.get("/account/providers", headers={"Cookie": f"claire_session={member_sess}"}).text
        assert client.patch("/api/me/provider-keys", headers=member_headers, json={"provider": "gemini", "model": ""}).json()["model_prefs"] == {}
        assert client.patch("/api/me/provider-keys", headers=admin_cookie_headers, json={"provider": "gemini", "model": ""}).status_code == 404

        # 5. Without any seal in the process memory (as in the Telegram bot process), the key can be decrypted.
        clear_volatile_user_seals()
        client.cookies.clear()
        verify_with_cookie = client.post(
            "/api/me/provider-keys/verify",
            headers={"Cookie": f"claire_session={member_sess}", "Origin": ORIGIN},
            json={"provider": "gemini"},
        )
        assert verify_with_cookie.status_code == 200
        assert verify_with_cookie.json()["ok"] is True
        assert BYOK_CANARY not in verify_with_cookie.text
