"""Unit tests for manual persistent token retirement (ENV-4 / ER7)."""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from claire import cli
from claire.api import security
from claire.api.accounts_routes.common import prepare_auth_db
from claire.api.server import create_app
from claire.core.config import Settings
from tests.auth_helpers import bearer_headers


def test_prepare_auth_db_archives_legacy_api_tokens_without_reading(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """prepare_auth_db renames data/auth/api_tokens.json to api_tokens.json.retired atomically without parsing it."""
    auth_dir = tmp_path / "auth"
    auth_dir.mkdir(parents=True)
    legacy_path = auth_dir / "api_tokens.json"
    retired_path = auth_dir / "api_tokens.json.retired"
    legacy_bytes = b"{not-json-unparsed-legacy-secret"
    legacy_path.write_bytes(legacy_bytes)

    with caplog.at_level(logging.INFO):
        prepare_auth_db(tmp_path)

    assert not legacy_path.exists()
    assert retired_path.is_file()
    assert retired_path.read_bytes() == legacy_bytes
    assert any("/account/mcp" in rec.getMessage() for rec in caplog.records)

    # Idempotent when api_tokens.json.retired already exists: never overwrite existing archive.
    legacy_path.write_bytes(b"new-leftover")
    prepare_auth_db(tmp_path)
    assert legacy_path.is_file()
    assert retired_path.read_bytes() == legacy_bytes


def test_manual_token_routes_and_credentials_are_retired(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify /tokens*, /account/tokens, /admin/tokens, /settings/tokens return 404 and manual/env tokens are rejected."""
    monkeypatch.setenv("CLAIRE_INJECT_TOKEN", "legacy-env-owner-" + ("o" * 32))
    monkeypatch.setenv("CLAIRE_READONLY_TOKEN", "legacy-env-ro-" + ("r" * 32))
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    auth_dir = data_dir / "auth"
    auth_dir.mkdir()
    (auth_dir / "api_tokens.json").write_text('{"tokens":[{"id":1,"token":"cb_' + ("x" * 32) + '","scope":"owner"}]}', encoding="utf-8")

    s = Settings(
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_FQDN="http://127.0.0.1:8765",
    )
    assert not hasattr(s, "inject_token")
    assert not hasattr(s, "readonly_token")

    app = create_app(s, SimpleNamespace(provider=SimpleNamespace(name="mock")))
    assert not (auth_dir / "api_tokens.json").exists()
    assert (auth_dir / "api_tokens.json.retired").is_file()

    owner_hdr = bearer_headers(s.db_file, account_id=1, scope="owner")
    oauth_ro = bearer_headers(s.db_file, account_id=1, scope="readonly", oauth=True)

    with TestClient(app, base_url=s.public_url) as client:
        for method, path in (
            ("GET", "/tokens"),
            ("POST", "/tokens"),
            ("DELETE", "/tokens"),
            ("POST", "/tokens/revoke"),
            ("GET", "/account/tokens"),
            ("GET", "/admin/tokens"),
            ("GET", "/settings/tokens"),
        ):
            assert client.request(method, path, headers=owner_hdr).status_code == 404, (method, path)

        for legacy_tok in ("cb_" + ("x" * 32), "legacy-env-owner-" + ("o" * 32), "legacy-env-ro-" + ("r" * 32)):
            hdr = {"Authorization": f"Bearer {legacy_tok}"}
            assert client.get("/whoami", headers=hdr).status_code == 404
            assert client.post("/mcp", headers=hdr, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status_code == 401

        assert client.get("/whoami", headers=oauth_ro).json()["scope"] == "readonly"
        assert client.get("/whoami", headers=owner_hdr).json()["scope"] == "owner"

    assert ("GET", "/tokens") not in security.ROUTE_POLICY


def test_cli_token_subcommand_removed() -> None:
    """Verify `claire token` CLI subcommand is no longer registered."""
    assert not hasattr(cli, "cmd_token")
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["token", "list"])
