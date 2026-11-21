"""Unit and end-to-end tests for AE1b fixed agent_client bundle delivery and host isolation."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from starlette.testclient import TestClient

from claire.accounts.username_policy import RESERVED_USERNAMES, UsernameRejected, validate_username
from claire.api import clients_host, server
from claire.api.host_router import HostRoles
from claire.plugin_host import runtime as plugin_rt
from claire.plugin_host.files import folder_digest_detail
from claire.plugin_host.service import get_agent_client, switch_plugin
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from claire.store.secrets import SecretService, reset_secret_service
from claire.store.token_store import get_token_store, reset_token_stores

APP_HOST = "127.0.0.1:8765"
CLIENTS_HOST = f"127.0.0.1:{clients_host.DEV_CLIENTS_PORT}"
APP_ORIGIN = f"http://{APP_HOST}"
CLIENTS_ORIGIN = f"http://{CLIENTS_HOST}"
OWNER_TOKEN = "owner-" + "o" * 32
READONLY_TOKEN = "readonly-" + "r" * 32
CORE_COMMIT = "b8694402b6ee0ce08f5af1a6af2853bb911575d5"

SAMPLE_FILES: dict[str, str] = {
    "mcp_config.json.tmpl": '{"mcpServers":{"claire":{"url":"{{MCP_URL}}"}}}',
    "rules/AGENTS.md": "# Agent Rules\nDo not modify unrelated files.\n",
    "skills/extract/SKILL.md": "# Extract Skill\nFollow schema constraints.\n",
}


@dataclass
class _Settings:
    db_file: Path
    data_dir: Path
    vault_dir: Path
    plugins_dir: Path
    secret_service: SecretService | None = None
    environment: str = "development"
    public_url: str = APP_ORIGIN
    inject_token: str = OWNER_TOKEN
    readonly_token: str = READONLY_TOKEN
    cors_allowed_origins: str = ""
    anonymous_readonly: bool = False
    multi_theme: bool = True
    site_domain: str = ""
    usercontent_fqdn: str = ""
    media_fqdn: str = ""


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    for key in list(os.environ):
        if key.startswith(("CLAIRE_", "CB_")):
            monkeypatch.delenv(key, raising=False)
    plugin_rt.runtime().invalidate()
    reset_token_stores()
    reset_secret_service()
    yield
    plugin_rt.runtime().invalidate()
    reset_token_stores()
    reset_secret_service()


def _harden_tree(root: Path) -> None:
    st = os.lstat(root)
    if stat.S_ISLNK(st.st_mode):
        return
    if stat.S_ISDIR(st.st_mode):
        os.chmod(root, 0o755)
        for entry in root.iterdir():
            _harden_tree(entry)
    elif stat.S_ISREG(st.st_mode):
        os.chmod(root, 0o644)


def _install_sample_client_plugin(
    plugin_folder: Path,
    db_file: Path,
    settings: Any,
    *,
    plugin_id: str = "thirdparty.sample_client",
    client_id: str = "sample-client",
    files: dict[str, str] | None = None,
    enable: bool = True,
) -> Path:
    plugin_folder.mkdir(parents=True, exist_ok=True)
    bundle_files = files or dict(SAMPLE_FILES)
    folder = plugin_folder / "agent_client" / plugin_id
    folder.mkdir(parents=True, exist_ok=True)
    for rel, content in bundle_files.items():
        target = folder / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    manifest_toml = (
        "manifest = 1\n"
        f"id = {json.dumps(plugin_id)}\n"
        'type = "agent_client"\n'
        "revision = 1\n"
        f'core_commit = "{CORE_COMMIT}"\n'
        "api = 1\n"
        f"client_id = {json.dumps(client_id)}\n"
        'required_scopes = ["readonly", "write"]\n'
        f"files = {json.dumps(list(bundle_files))}\n"
        'license = "MIT"\n'
        'maturity = "stable"\n\n'
        "[text.en]\n"
        'name = "Sample Client"\n'
        'description = "Sample agent client bundle for tests."\n\n'
        "[text.ko]\n"
        'name = "샘플 클라이언트"\n'
        'description = "테스트용 에이전트 클라이언트 번들."\n'
    )
    (folder / "plugin.toml").write_text(manifest_toml, encoding="utf-8")
    _harden_tree(plugin_folder)
    digest, _hashes = folder_digest_detail(folder, check_owner=True)
    if enable:
        switch_plugin(plugin_id, enabled=True, digest=digest, actor=1, via="test", settings=settings)
    plugin_rt.runtime().invalidate()
    return folder


def _build_env(tmp_path: Path, *, anonymous_readonly: bool = False, public_url: str = APP_ORIGIN, environment: str = "development", install_second_client: bool = False) -> SimpleNamespace:
    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    plugin_folder = data_dir / "plugins"
    db_file = data_dir / "claire.db"
    data_dir.mkdir(parents=True, exist_ok=True)
    vault_dir.mkdir(parents=True, exist_ok=True)
    plugin_folder.mkdir(parents=True, exist_ok=True)
    sec_svc = SecretService(master_key=b"a" * 32)
    settings = _Settings(
        db_file=db_file,
        data_dir=data_dir,
        vault_dir=vault_dir,
        plugins_dir=plugin_folder,
        secret_service=sec_svc,
        environment=environment,
        public_url=public_url,
        anonymous_readonly=anonymous_readonly,
    )
    conn = dbm.connect(db_file)
    try:
        dbm.init_db(conn)
        conn.execute(
            "UPDATE accounts SET username='owner_admin', display_name='Owner Admin', status='active', public_id=100000000000000001 WHERE id=1"
        )
        conn.commit()
    finally:
        conn.close()

    plugin_dir = _install_sample_client_plugin(plugin_folder, db_file, settings)
    second_dir = None
    if install_second_client:
        second_dir = _install_sample_client_plugin(
            plugin_folder,
            db_file,
            settings,
            plugin_id="thirdparty.other_client",
            client_id="other-client",
            files={"README.md": "# Other\n"},
        )
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    client = TestClient(app, base_url=public_url)
    return SimpleNamespace(
        settings=settings,
        app=app,
        client=client,
        db_file=db_file,
        data_dir=data_dir,
        plugin_dir=plugin_dir,
        second_dir=second_dir,
        secret_service=sec_svc,
    )


def _assert_clients_security_headers(headers) -> None:
    assert headers.get("cache-control") == "no-store"
    assert headers.get("x-content-type-options") == "nosniff"
    assert headers.get("content-security-policy") == "default-src 'none'; sandbox"
    assert headers.get("referrer-policy") == "no-referrer"
    assert headers.get("cross-origin-resource-policy") == "same-origin"
    assert "set-cookie" not in headers


# --- Unit tests: module interfaces, I/O, and external libraries ---


def test_clients_username_reserved():
    assert "clients" in RESERVED_USERNAMES
    for candidate in ("clients", "CLIENTS", "clients_test"):
        with pytest.raises(UsernameRejected, match="reserved"):
            validate_username(candidate)


@pytest.mark.parametrize(
    ("app_authority", "environment", "site_domain", "expected"),
    [
        ("127.0.0.1:8765", "development", None, "127.0.0.1:8770"),
        ("192.0.2.10:8766", "development", None, "192.0.2.10:8770"),
        ("0.0.0.0:8765", "development", None, None),
        ("localhost:8765", "development", None, None),
        ("cb.netspheres.org", "production", None, "clients.netspheres.org"),
        ("claire.example.co.kr", "production", "example.co.kr", "clients.example.co.kr"),
        ("sub.claire.example.com", "production", "claire.example.com", "clients.example.com"),
        ("localhost", "production", None, None),
    ],
)
def test_clients_authority_uses_psl_and_dev_port(app_authority, environment, site_domain, expected):
    assert clients_host.clients_authority(app_authority, environment, site_domain=site_domain) == expected


def test_host_roles_compute_clients_host_and_reject_cors_and_collision():
    dev_roles = HostRoles.from_settings(
        SimpleNamespace(cors_allowed_origins="", site_domain="", usercontent_fqdn="", media_fqdn=""),
        environment="development",
        app_authority=APP_HOST,
    )
    assert dev_roles.clients == CLIENTS_HOST
    assert dev_roles.role_of(CLIENTS_HOST) == "clients"
    assert dev_roles.origin("clients") == CLIENTS_ORIGIN

    prod_roles = HostRoles.from_settings(
        SimpleNamespace(cors_allowed_origins="", site_domain="example.co.kr", usercontent_fqdn="", media_fqdn=""),
        environment="production",
        app_authority="claire.example.co.kr",
    )
    assert prod_roles.clients == "clients.example.co.kr"
    assert prod_roles.role_of("clients.example.co.kr") == "clients"
    assert prod_roles.origin("clients") == "https://clients.example.co.kr"

    taken = HostRoles.from_settings(
        SimpleNamespace(cors_allowed_origins="", site_domain="example.com", usercontent_fqdn="clients.example.com", media_fqdn=""),
        environment="production",
        app_authority="cb.example.com",
    )
    assert taken.clients is None
    assert taken.role_of("clients.example.com") == "usercontent"

    with pytest.raises(ValueError, match="CLAIRE_CORS_ALLOWED_ORIGINS"):
        HostRoles.from_settings(
            SimpleNamespace(cors_allowed_origins=CLIENTS_ORIGIN, site_domain="", usercontent_fqdn="", media_fqdn=""),
            environment="development",
            app_authority=APP_HOST,
        )


def test_delivery_mode_persistence_and_validation(tmp_path):
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    try:
        dbm.init_db(conn)
        assert clients_host.read_delivery_mode(conn) == "uri_path"
        roles = HostRoles.from_settings(
            SimpleNamespace(cors_allowed_origins="", site_domain="", usercontent_fqdn="", media_fqdn=""),
            environment="development",
            app_authority=APP_HOST,
        )
        assert clients_host.write_delivery_mode(conn, "fqdn", updated_by=1, roles=roles) == "fqdn"
        assert clients_host.read_delivery_mode(conn) == "fqdn"
        with pytest.raises(ValueError):
            clients_host.write_delivery_mode(conn, "custom", updated_by=1, roles=roles)
        no_clients_roles = HostRoles.from_settings(
            SimpleNamespace(cors_allowed_origins="", site_domain="", usercontent_fqdn="", media_fqdn=""),
            environment="production",
            app_authority="localhost",
        )
        with pytest.raises(ValueError):
            clients_host.write_delivery_mode(conn, "fqdn", updated_by=1, roles=no_clients_roles)
    finally:
        conn.close()


def test_bundle_token_binding_and_expiry():
    svc = SecretService(master_key=b"k" * 32)
    token = clients_host.create_bundle_token(42, "sample-client", "bundle.tar.gz", expires_in=60, secret_service=svc, now=1000.0)
    assert clients_host.verify_bundle_token(token, client_id="sample-client", target_file="bundle.tar.gz", secret_service=svc, now=1059.0) == 42
    assert clients_host.verify_bundle_token(token, client_id="sample-client", target_file="bundle.tar.gz", secret_service=svc, now=1060.0) == 42
    assert clients_host.verify_bundle_token(token, client_id="sample-client", target_file="bundle.tar.gz", secret_service=svc, now=1061.0) is None
    assert clients_host.verify_bundle_token(token, client_id="other-client", target_file="bundle.tar.gz", secret_service=svc, now=1010.0) is None
    assert clients_host.verify_bundle_token(token, client_id="sample-client", target_file="manifest.json", secret_service=svc, now=1010.0) is None
    tampered = "99." + token.split(".", 1)[1]
    assert clients_host.verify_bundle_token(tampered, client_id="sample-client", target_file="bundle.tar.gz", secret_service=svc, now=1010.0) is None


def test_deterministic_tar_gz_and_manifest_sha256_from_disk(tmp_path):
    env = _build_env(tmp_path, anonymous_readonly=True)
    status = get_agent_client("sample-client", settings=env.settings, fresh=True)
    assert status is not None
    archive_one = clients_host.build_bundle_tar_gz(status)
    archive_two = clients_host.build_bundle_tar_gz(status)
    assert archive_one == archive_two

    manifest = json.loads(clients_host.build_manifest_bytes(status, delivery_mode="uri_path").decode("utf-8"))
    assert manifest["client_id"] == "sample-client"
    assert manifest["delivery_mode"] == "uri_path"
    manifest_hashes = {item["path"]: item["sha256"] for item in manifest["files"]}

    with tarfile.open(fileobj=io.BytesIO(archive_one), mode="r:gz") as tf:
        names = [member.name for member in tf.getmembers()]
        assert names == sorted(SAMPLE_FILES)
        for member in tf.getmembers():
            assert member.uid == 0 and member.gid == 0 and member.mtime == 0 and member.mode == 0o644
            extracted = tf.extractfile(member)
            assert extracted is not None
            data = extracted.read()
            assert data == SAMPLE_FILES[member.name].encode("utf-8")
            assert hashlib.sha256(data).hexdigest() == manifest_hashes[member.name]


# --- End-to-end tests ---


def test_e2e_delivery_mode_isolation_auth_and_console_surfaces(tmp_path):
    env = _build_env(tmp_path, anonymous_readonly=False, install_second_client=True)
    client = env.client

    # Create authenticated admin session with recent authentication for console routes and API toggles.
    conn = dbm.connect_existing(env.db_file)
    try:
        admin_session = accounts_store.create_account_session(conn, account_id=1, scope="owner", now=time.time())
        member = accounts_store.AccountStore(conn).create_member(username="memberone", display_name="Member One")
        conn.execute("UPDATE accounts SET status='active' WHERE id=?", (member.id,))
        now = time.time()
        conn.execute(
            "INSERT INTO knowledge_bases (id, seq, number, label, description, icon, is_personal, is_public, account_id, db_path, default_focus, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'folder', 1, 1, ?, ?, '', ?, ?)",
            (20, 20, (member.id << 56) | 20, "Member KB", "Member personal KB", member.id, str(env.db_file), now, now),
        )
        conn.commit()
        member_session = accounts_store.create_account_session(conn, account_id=member.id, scope="owner", now=time.time())
    finally:
        conn.close()

    api_token = get_token_store(data_dir=env.data_dir).create_token(name="bundle-reader", scope="readonly", account_id=1)["token"]
    admin_headers = {"Cookie": f"claire_session={admin_session}", "Origin": APP_ORIGIN}
    member_headers = {"Cookie": f"claire_session={member_session}", "Origin": APP_ORIGIN}

    # Separate clients avoid implicit cookie jar carry-over across roles.
    anon_client = TestClient(env.app, base_url=APP_ORIGIN)
    admin_client = TestClient(env.app, base_url=APP_ORIGIN)
    member_client = TestClient(env.app, base_url=APP_ORIGIN)

    # 1. /api/admin/server and /admin/server expose default uri_path mode and read-only URLs without custom host/path inputs.
    admin_api = admin_client.get("/api/admin/server", headers=admin_headers)
    assert admin_api.status_code == 200
    admin_json = admin_api.json()
    assert admin_json["agent_client_delivery_mode"] == "uri_path"
    assert admin_json["agent_client_delivery"]["active_base_url"] == f"{APP_ORIGIN}/clients"
    assert admin_json["agent_client_delivery"]["fqdn_origin"] == CLIENTS_ORIGIN

    admin_html = admin_client.get("/admin/server", headers=admin_headers).text
    assert 'id="agent-client-delivery"' in admin_html
    assert 'name="agent_client_delivery_mode"' in admin_html
    assert "claire-delivery-hostname" not in admin_html
    assert 'name="hostname"' not in admin_html and 'name="path"' not in admin_html
    assert member_client.get("/api/admin/server", headers=member_headers).status_code == 404

    # 2. In uri_path mode: app host serves /clients/{client_id}/..., while clients host returns 404 even with Bearer/cookies.
    unauth = anon_client.get(f"{APP_ORIGIN}/clients/sample-client/manifest.json")
    assert unauth.status_code == 401
    _assert_clients_security_headers(unauth.headers)

    ro_resp = anon_client.get(f"{APP_ORIGIN}/clients/sample-client/manifest.json", headers={"Authorization": f"Bearer {READONLY_TOKEN}"})
    assert ro_resp.status_code == 200
    _assert_clients_security_headers(ro_resp.headers)
    manifest_data = ro_resp.json()
    assert manifest_data["client_id"] == "sample-client"
    assert manifest_data["delivery_mode"] == "uri_path"

    head_resp = anon_client.head(f"{APP_ORIGIN}/clients/sample-client/bundle.tar.gz", headers={"Authorization": f"Bearer {api_token}"})
    assert head_resp.status_code == 200
    assert head_resp.content == b""
    assert int(head_resp.headers["content-length"]) > 0
    _assert_clients_security_headers(head_resp.headers)

    # Cookie session is accepted in uri_path mode.
    cookie_file = anon_client.get(f"{APP_ORIGIN}/clients/sample-client/files/rules/AGENTS.md", headers={"Cookie": f"claire_session={member_session}"})
    assert cookie_file.status_code == 200
    assert cookie_file.text == SAMPLE_FILES["rules/AGENTS.md"]
    _assert_clients_security_headers(cookie_file.headers)

    # Path traversal and undeclared files fail closed.
    for bad_path in (
        f"{APP_ORIGIN}/clients/sample-client/files/plugin.toml",
        f"{APP_ORIGIN}/clients/unknown-client/manifest.json",
    ):
        bad_resp = anon_client.get(bad_path, headers={"Authorization": f"Bearer {READONLY_TOKEN}"})
        assert bad_resp.status_code == 404, bad_path
        _assert_clients_security_headers(bad_resp.headers)
    assert anon_client.get(f"{APP_ORIGIN}/clients/sample-client/files/../plugin.toml", headers={"Authorization": f"Bearer {READONLY_TOKEN}"}).status_code in {400, 404}
    assert anon_client.get(f"{APP_ORIGIN}/clients/sample-client/files/%2e%2e/plugin.toml", headers={"Authorization": f"Bearer {READONLY_TOKEN}"}).status_code in {400, 404}

    # clients host MUST return 404 in uri_path mode and NEVER set cookies.
    clients_in_uri = anon_client.get(
        f"{CLIENTS_ORIGIN}/sample-client/manifest.json",
        headers={"Authorization": f"Bearer {READONLY_TOKEN}", "Cookie": f"claire_session={admin_session}"},
    )
    assert clients_in_uri.status_code == 404
    _assert_clients_security_headers(clients_in_uri.headers)

    # 3. /settings/mcp renders resolved delivery URLs and signed 60s ?t= bundle links for authenticated users.
    mcp_resp = member_client.get("/settings/mcp", headers=member_headers)
    assert mcp_resp.status_code == 200
    mcp_html = mcp_resp.text
    assert "/admin/" not in mcp_html
    assert 'id="pref-mcp-client-bundles"' in mcp_html
    assert 'data-delivery-mode="uri_path"' in mcp_html

    signed_manifest_match = re.search(r'href="([^"]+/sample-client/manifest\.json\?t=[^"]+)"', mcp_html)
    signed_bundle_match = re.search(r'href="([^"]+/sample-client/bundle\.tar\.gz\?t=[^"]+)"', mcp_html)
    signed_file_match = re.search(r'href="([^"]+/sample-client/files/rules/AGENTS\.md\?t=[^"]+)"', mcp_html)
    assert signed_manifest_match and signed_bundle_match and signed_file_match

    # Signed links work without Bearer or Cookie headers.
    dl_bundle = anon_client.get(signed_bundle_match.group(1))
    assert dl_bundle.status_code == 200
    _assert_clients_security_headers(dl_bundle.headers)
    with tarfile.open(fileobj=io.BytesIO(dl_bundle.content), mode="r:gz") as tf:
        assert sorted(m.name for m in tf.getmembers()) == sorted(SAMPLE_FILES)

    # Swapping signed token across target_file or client_id fails with 401.
    token_param = parse_qs(urlparse(signed_manifest_match.group(1)).query)["t"][0]
    assert anon_client.get(f"{APP_ORIGIN}/clients/sample-client/bundle.tar.gz?t={token_param}").status_code == 401
    assert anon_client.get(f"{APP_ORIGIN}/clients/other-client/manifest.json?t={token_param}").status_code == 401

    # 4. AED3 enforcement: reject custom hostname/path payloads and step-up checks, then switch to fqdn mode.
    bad_custom = admin_client.post(
        "/api/admin/server/client-delivery",
        headers=admin_headers,
        json={"agent_client_delivery_mode": "fqdn", "hostname": "evil.example.com"},
    )
    assert bad_custom.status_code == 400
    assert member_client.post("/api/admin/server/client-delivery", headers=member_headers, json={"agent_client_delivery_mode": "fqdn"}).status_code == 404
    assert anon_client.post(
        "/api/admin/server/client-delivery",
        headers={"Authorization": f"Bearer {OWNER_TOKEN}"},
        json={"agent_client_delivery_mode": "fqdn"},
    ).status_code == 403

    switch_ok = admin_client.post(
        "/api/admin/server/client-delivery",
        headers=admin_headers,
        json={"agent_client_delivery_mode": "fqdn"},
    )
    assert switch_ok.status_code == 200
    assert switch_ok.json()["agent_client_delivery_mode"] == "fqdn"
    assert switch_ok.json()["agent_client_delivery"]["active_base_url"] == CLIENTS_ORIGIN

    # 5. In fqdn mode: /clients/... on app host returns 404 even for valid admin session / Bearer / signed URL!
    for app_url in (
        f"{APP_ORIGIN}/clients/sample-client/manifest.json",
        f"{APP_ORIGIN}/clients/sample-client/bundle.tar.gz",
        signed_bundle_match.group(1),
    ):
        app_denied = anon_client.get(app_url, headers={"Authorization": f"Bearer {OWNER_TOKEN}", "Cookie": f"claire_session={admin_session}"})
        assert app_denied.status_code == 404, app_url
        _assert_clients_security_headers(app_denied.headers)

    # /settings/mcp now mints links against CLIENTS_ORIGIN!
    mcp_fqdn_resp = member_client.get("/settings/mcp", headers=member_headers)
    assert mcp_fqdn_resp.status_code == 200
    assert 'data-delivery-mode="fqdn"' in mcp_fqdn_resp.text
    fqdn_signed_bundle = re.search(rf'href="({re.escape(CLIENTS_ORIGIN)}/sample-client/bundle\.tar\.gz\?t=[^"]+)"', mcp_fqdn_resp.text)
    assert fqdn_signed_bundle is not None

    # On clients host: cookie session is stripped by HostRouter and rejected (401), while Bearer and minted ?t= succeed cleanly!
    cookie_only_fqdn = anon_client.get(f"{CLIENTS_ORIGIN}/sample-client/manifest.json", headers={"Cookie": f"claire_session={admin_session}"})
    assert cookie_only_fqdn.status_code == 401
    _assert_clients_security_headers(cookie_only_fqdn.headers)
    assert anon_client.get(f"{CLIENTS_ORIGIN}/sample-client/manifest.json", headers={"X-Session": admin_session}).status_code == 401

    bearer_fqdn = anon_client.get(
        f"{CLIENTS_ORIGIN}/sample-client/manifest.json",
        headers={"Authorization": f"Bearer {api_token}", "Cookie": f"claire_session={admin_session}"},
    )
    assert bearer_fqdn.status_code == 200
    assert bearer_fqdn.json()["delivery_mode"] == "fqdn"
    _assert_clients_security_headers(bearer_fqdn.headers)

    signed_fqdn_dl = anon_client.get(
        fqdn_signed_bundle.group(1),
        headers={"Cookie": f"claire_session={admin_session}"},
    )
    assert signed_fqdn_dl.status_code == 200
    _assert_clients_security_headers(signed_fqdn_dl.headers)

    traversal_fqdn = anon_client.get(
        f"{CLIENTS_ORIGIN}/sample-client/files/%2e%2e/plugin.toml",
        headers={"Authorization": f"Bearer {api_token}"},
    )
    assert traversal_fqdn.status_code == 404
    _assert_clients_security_headers(traversal_fqdn.headers)

    # 6. Suspending the bound member account invalidates previously minted ?t= tokens immediately.
    conn = dbm.connect_existing(env.db_file)
    try:
        conn.execute("UPDATE accounts SET status='suspended' WHERE id=?", (member.id,))
        conn.commit()
    finally:
        conn.close()
    assert anon_client.get(fqdn_signed_bundle.group(1)).status_code == 401

    # 7. Admin HTML form on /admin/server can switch delivery mode back to uri_path cleanly without JS.
    form_switch = admin_client.post(
        "/admin/server",
        headers=admin_headers,
        data={"agent_client_delivery_mode": "uri_path"},
        follow_redirects=False,
    )
    assert form_switch.status_code == 303
    assert admin_client.get("/api/admin/server", headers=admin_headers).json()["agent_client_delivery_mode"] == "uri_path"


def test_e2e_anonymous_readonly_allows_unauthenticated_bundle_read_only_in_active_mode(tmp_path):
    env = _build_env(tmp_path, anonymous_readonly=True)
    assert env.client.get(f"{APP_ORIGIN}/clients/sample-client/manifest.json").status_code == 200
    assert env.client.get(f"{CLIENTS_ORIGIN}/sample-client/manifest.json").status_code == 404
