"""End-to-end HTTP workflow tests for admin feature routing, lineage catalog, and member BYOK baseline policy."""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from claire import secret_scrub
from claire.api import server
from claire.config import Settings
from claire.store import accounts as accounts_store
from claire.store import auth_db, provider_keys
from claire.store import db as dbm
from claire.store.secrets import BYOK_SEAL_COOKIE, clear_volatile_user_seals, reset_secret_service
from claire.store.theme import ThemeManager

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
    """Verify full HTTP E2E flow across admin /providers and member /settings/keys."""
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
    ThemeManager(settings)
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="mock")))

    with TestClient(app, base_url=PUBLIC_URL) as client:
        owner_headers = {"Authorization": f"Bearer {OWNER_TOKEN}", "Origin": ORIGIN}
        member_sess = _create_session(db_file, 2)
        member_headers = {"Cookie": f"claire_session={member_sess}", "Origin": ORIGIN}

        # 1. Admin configures Gemini key, 3.8 baseline, separated summarize slot, embed_task_type, and JEV filter mode.
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
                        "embed_task_type": "RETRIEVAL_DOCUMENT",
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
                },
            },
        )
        assert patch_resp.status_code == 200, patch_resp.text
        cfg = patch_resp.json()["config"]
        assert cfg["providers"]["gemini"]["embed_task_type"] == "RETRIEVAL_DOCUMENT"
        assert cfg["providers"]["jev"]["mode"] == "filter"
        assert cfg["feature_routing"]["summarize"]["model"] == "gemini-4-argon"
        assert cfg["lineage_summary"]["effective_slots"]["extract_graph"]["model"] == "gemini-3.8-flash"
        assert cfg["lineage_summary"]["effective_slots"]["summarize"]["model"] == "gemini-4-argon"
        assert any(f["id"] == "F1" and f["status"] == "REMOVED" for f in cfg["lineage_summary"]["fallbacks"])
        assert any(f["id"] == "F10" and f["status"] == "OUT_OF_SCOPE" for f in cfg["lineage_summary"]["fallbacks"])

        # 2. Member fetches /settings/keys HTML and /api/me/keys?include_policy=1 without exposing admin routes.
        page_resp = client.get("/settings/keys", headers={"Cookie": f"claire_session={member_sess}"})
        assert page_resp.status_code == 200
        assert "data-split-key-notice" in page_resp.text
        assert "data-byok-model-select" in page_resp.text
        assert "/admin/" not in page_resp.text

        policy_resp = client.get("/api/me/keys?include_policy=1", headers={"Cookie": f"claire_session={member_sess}"})
        assert policy_resp.status_code == 200
        policy = policy_resp.json()["model_policy"]
        assert policy["admin_baseline"]["extract_graph"]["model"] == "gemini-3.8-flash"
        assert policy["admin_baseline"]["summarize"]["model"] == "gemini-4-argon"
        assert policy["split_key_enabled"] is True

        # 3. Member is rejected when trying mock or a weaker model than admin baseline.
        bad_mock = client.put("/api/me/keys", headers=member_headers, json={"provider": "mock", "api_key": BYOK_CANARY})
        assert bad_mock.status_code == 400

        below_base = client.put(
            "/api/me/keys",
            headers=member_headers,
            json={"provider": "gemini", "api_key": BYOK_CANARY, "model": "gemini-3.5-flash-lite", "effort": "medium"},
        )
        assert below_base.status_code == 400
        assert below_base.json()["error"] == "model_below_admin_baseline"

        # 4. Member registers an equal-or-higher model and receives a split-key seal cookie.
        ok_put = client.put(
            "/api/me/keys",
            headers=member_headers,
            json={"provider": "gemini", "api_key": BYOK_CANARY, "model": "gemini-4-argon", "effort": "high"},
        )
        assert ok_put.status_code == 200, ok_put.text
        seal_cookie = ok_put.cookies.get(BYOK_SEAL_COOKIE)
        assert seal_cookie

        # 5. Clearing in-memory volatile seals fails admin-only decryption, while member cookie restores verify.
        clear_volatile_user_seals()
        verify_with_cookie = client.post(
            "/api/me/keys/verify",
            headers={"Cookie": f"claire_session={member_sess}; {BYOK_SEAL_COOKIE}={seal_cookie}", "Origin": ORIGIN},
            json={"provider": "gemini"},
        )
        assert verify_with_cookie.status_code == 200
        assert verify_with_cookie.json()["ok"] is True
        assert BYOK_CANARY not in verify_with_cookie.text
