"""Unit tests for IngestLimitManager, I/O persistence, and module interfaces."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from claire.api.server import create_app
from claire.config import Settings, get_settings
from claire.ingest_limit_manager import (
    DEFAULT_INGEST_LIMITS_CONFIG,
    MIGRATED_LIMIT_ENV_VARS,
    IngestLimitManager,
    comment_out_env_file_limits,
    get_ingest_limit_manager,
)


def test_comment_out_env_file_limits(tmp_path: Path):
    """Test I/O operation commenting out migrated environment variables in env file."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "CLAIRE_ENVIRONMENT=production\n"
        "CB_API_PORT=8765\n"
        "CLAIRE_RAW_CHAR_BUDGET=15000\n"
        "CLAIRE_PDF_MAX_EXTRACT_CHARS=60000\n"
        "CLAIRE_PDF_PARSER=docling\n"
        "# Already commented\n"
        "# CLAIRE_EXTRACT_CHAR_BUDGET=20000\n"
        "CLAIRE_INJECT_TOKEN=test-token-12345678901234567890\n",
        encoding="utf-8",
    )

    count = comment_out_env_file_limits(env_file, MIGRATED_LIMIT_ENV_VARS)
    assert count == 3

    lines = env_file.read_text(encoding="utf-8").splitlines()
    assert "CLAIRE_ENVIRONMENT=production" in lines
    assert "CB_API_PORT=8765" in lines
    assert "CLAIRE_INJECT_TOKEN=test-token-12345678901234567890" in lines
    assert "# Already commented" in lines

    commented_budget = [l for l in lines if "CLAIRE_RAW_CHAR_BUDGET=15000" in l]
    assert len(commented_budget) == 1
    assert commented_budget[0].startswith("# CLAIRE_RAW_CHAR_BUDGET=15000")
    assert "Migrated to ingest_limits.json" in commented_budget[0]

    commented_pdf = [l for l in lines if "CLAIRE_PDF_MAX_EXTRACT_CHARS=60000" in l]
    assert len(commented_pdf) == 1
    assert commented_pdf[0].startswith("# CLAIRE_PDF_MAX_EXTRACT_CHARS=60000")

    commented_parser = [l for l in lines if "CLAIRE_PDF_PARSER=docling" in l]
    assert len(commented_parser) == 1
    assert commented_parser[0].startswith("# CLAIRE_PDF_PARSER=docling")


def test_ingest_limit_manager_auto_migration(tmp_path: Path):
    """Test auto-migration of env variables to ingest_limits.json upon manager initialization."""
    data_dir = tmp_path / "data"
    env_file = tmp_path / ".env"
    env_file.write_text(
        "CLAIRE_RAW_CHAR_BUDGET=25000\n"
        "CLAIRE_PDF_MAX_EXTRACT_CHARS=70000\n"
        "CLAIRE_PDF_PARSER=docling\n"
        "CLAIRE_PDF_PAPER_EFFORT=medium\n"
        "CLAIRE_SLICING_STRATEGY=strict\n"
        "CB_API_PORT=8765\n",
        encoding="utf-8",
    )

    manager = IngestLimitManager(
        data_dir=data_dir,
        auto_migrate=True,
        env_files=[env_file],
    )

    assert manager.registry_path.is_file()
    cfg = manager.get_config()
    assert cfg["limits"]["raw_char_budget"] == 25000
    assert cfg["limits"]["pdf_max_extract_chars"] == 70000
    assert cfg["limits"]["pdf_parser"] == "docling"
    assert cfg["limits"]["pdf_paper_effort"] == "medium"
    assert cfg["limits"]["slicing_strategy"] == "strict"

    env_content = env_file.read_text(encoding="utf-8")
    assert "# CLAIRE_RAW_CHAR_BUDGET=25000" in env_content
    assert "# CLAIRE_PDF_PARSER=docling" in env_content
    assert "CB_API_PORT=8765" in env_content


def test_ingest_limit_manager_validation(tmp_path: Path):
    """Test input validation for update_config in IngestLimitManager."""
    data_dir = tmp_path / "data"
    manager = IngestLimitManager(data_dir=data_dir, auto_migrate=False)

    with pytest.raises(ValueError, match="raw_char_budget must be greater than or equal to 0"):
        manager.update_config({"limits": {"raw_char_budget": -100}})

    with pytest.raises(ValueError, match="presentation_pdf_max_bytes must be greater than 0"):
        manager.update_config({"limits": {"presentation_pdf_max_bytes": 0}})

    with pytest.raises(ValueError, match="slicing_strategy must be one of"):
        manager.update_config({"limits": {"slicing_strategy": "unknown_strategy"}})

    with pytest.raises(ValueError, match="pdf_parser must be one of"):
        manager.update_config({"limits": {"pdf_parser": "invalid_parser"}})

    with pytest.raises(ValueError, match="pdf_paper_effort must be one of"):
        manager.update_config({"limits": {"pdf_paper_effort": "ultra"}})

    updated = manager.update_config({
        "limits": {
            "raw_char_budget": 12345,
            "slicing_strategy": "strict",
            "pdf_parser": "docling",
            "pdf_exclude_appendix": False,
        }
    })
    assert updated["limits"]["raw_char_budget"] == 12345
    assert updated["limits"]["slicing_strategy"] == "strict"
    assert updated["limits"]["pdf_parser"] == "docling"
    assert updated["limits"]["pdf_exclude_appendix"] is False


def test_settings_loads_from_ingest_limits_json(tmp_path: Path):
    """Test that Settings properly loads limits from ingest_limits.json."""
    data_dir = tmp_path / "data"
    limits_file = data_dir / "ingest_limits.json"
    data_dir.mkdir(parents=True, exist_ok=True)
    limits_file.write_text(
        json.dumps({
            "version": 1,
            "limits": {
                "raw_char_budget": 33333,
                "pdf_max_extract_chars": 77777,
                "slicing_strategy": "strict",
                "expand_max": 9,
            },
            "updated_at": 1000.0,
        }),
        encoding="utf-8",
    )

    settings = Settings(db_path=str(data_dir / "claire.db"))
    assert settings.raw_char_budget == 33333
    assert settings.pdf_max_extract_chars == 77777
    assert settings.slicing_strategy == "strict"
    assert settings.expand_max == 9


def test_api_limits_endpoints(tmp_path: Path):
    """Test /limits and /ingest-limits GET and PATCH endpoints."""
    owner_token = "owner-secret-token-123456789012345678"
    db_file = tmp_path / "claire.db"
    settings = Settings(
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_INJECT_TOKEN=owner_token,
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_VAULT_PATH=str(tmp_path / "vault"),
        CLAIRE_PUBLIC_URL="http://127.0.0.1:8765",
        anonymous_readonly=False,
    )

    app = create_app(settings)
    client = TestClient(app, base_url="http://127.0.0.1:8765")

    # 1. Unauthenticated request -> 404 (stealth mode)
    resp = client.get("/limits")
    assert resp.status_code == 404

    # 2. Authenticated GET -> 200
    headers = {"Authorization": f"Bearer {owner_token}"}
    resp = client.get("/limits", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "limits" in data
    assert data["limits"]["raw_char_budget"] == 20000

    # 3. Authenticated PATCH with invalid data -> 400
    resp = client.patch(
        "/limits",
        headers=headers,
        json={"limits": {"raw_char_budget": -50}},
    )
    assert resp.status_code == 400

    # 4. Authenticated PATCH with valid data -> 200
    resp = client.patch(
        "/limits",
        headers=headers,
        json={
            "limits": {
                "raw_char_budget": 45000,
                "pdf_max_extract_chars": 88000,
                "slicing_strategy": "strict",
            }
        },
    )
    assert resp.status_code == 200
    res_json = resp.json()
    assert res_json["ok"] is True
    assert res_json["config"]["limits"]["raw_char_budget"] == 45000
    assert res_json["config"]["limits"]["pdf_max_extract_chars"] == 88000
    assert res_json["config"]["limits"]["slicing_strategy"] == "strict"

    # Verify persistence to disk
    limits_file = tmp_path / "ingest_limits.json"
    assert limits_file.is_file()
    saved_raw = json.loads(limits_file.read_text(encoding="utf-8"))
    assert saved_raw["limits"]["raw_char_budget"] == 45000

    # 5. Verify alias endpoint /ingest-limits also returns updated config
    resp_alias = client.get("/ingest-limits", headers=headers)
    assert resp_alias.status_code == 200
    assert resp_alias.json()["limits"]["raw_char_budget"] == 45000
