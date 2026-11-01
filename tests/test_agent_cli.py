import json
import subprocess
import sys
import pytest
from pydantic import ValidationError

from claire.api.agent import AgentOutput


def test_agent_output_schema_strictly_sealed():
    # extra="forbid" verification
    with pytest.raises(ValidationError):
        AgentOutput(
            status="success",
            exit_code=0,
            hallucinated_speculative_field="should fail",
        )

    # valid output
    out = AgentOutput(
        status="success",
        exit_code=0,
        data={"some_key": "some_val"},
    )
    assert out.status == "success"
    assert out.exit_code == 0
    assert out.data == {"some_key": "some_val"}


def test_cli_json_flag_returns_single_valid_json():
    # Run `claire repo --json` as a subprocess
    res = subprocess.run(
        [sys.executable, "-m", "claire.cli", "repo", "--json"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    # stdout should parse cleanly as AgentOutput
    parsed = json.loads(res.stdout)
    agent_out = AgentOutput.model_validate(parsed)
    assert agent_out.status == "success"
    assert agent_out.exit_code == 0
    # Human readable text should go to stderr
    assert "Repository :" in res.stderr
    assert "Source URL :" in res.stderr


def test_cli_queue_json_contains_data():
    res = subprocess.run(
        [sys.executable, "-m", "claire.cli", "queue", "--json"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    parsed = json.loads(res.stdout)
    agent_out = AgentOutput.model_validate(parsed)
    assert agent_out.status == "success"
    assert agent_out.exit_code == 0
    assert isinstance(agent_out.data, dict)
    assert "inbox" in agent_out.data


def test_cli_unified_audit():
    # Test unified audit --check all --json
    res = subprocess.run(
        [sys.executable, "-m", "claire.cli", "audit", "--check", "all", "--json"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    parsed = json.loads(res.stdout)
    agent_out = AgentOutput.model_validate(parsed)
    assert agent_out.status == "success"
    assert "graph" in agent_out.data
    assert "residuals" in agent_out.data


def test_cli_unified_reprocess_dry_run():
    # Test unified reprocess --dry-run --json
    res = subprocess.run(
        [sys.executable, "-m", "claire.cli", "reprocess", "--dry-run", "--json"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    parsed = json.loads(res.stdout)
    agent_out = AgentOutput.model_validate(parsed)
    assert agent_out.status == "success"
    assert "dry_run" in agent_out.data


def test_cli_auth_share_doc():
    import sqlite3
    from pathlib import Path
    from claire.store import db as dbm

    db_file = Path("data/claire.db")
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    row = conn.execute("SELECT id FROM documents LIMIT 1").fetchone()
    if row:
        doc_id = row[0]
    else:
        doc_id = "doc_ea88a62a"
        conn.execute(
            "INSERT OR IGNORE INTO documents (id, title, url, fetched_at) VALUES (?, ?, ?, ?)",
            (doc_id, "Test Doc", "https://example.com/test", 1000.0),
        )
        conn.commit()
    conn.close()

    # 1. Test auth
    res = subprocess.run(
        [sys.executable, "-m", "claire.cli", "auth", "--scope", "readonly", "--json"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    agent_out = AgentOutput.model_validate(json.loads(res.stdout))
    assert agent_out.status == "success"
    assert agent_out.data["scope"] == "readonly"
    assert "token" in agent_out.data
    assert "url" in agent_out.data

    # 2. Test share with valid document
    res = subprocess.run(
        [sys.executable, "-m", "claire.cli", "share", doc_id, "--json"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    agent_out = AgentOutput.model_validate(json.loads(res.stdout))
    assert agent_out.status == "success"
    assert "share_token" in agent_out.data
    assert "share_url" in agent_out.data

    # 3. Test doc pin / unpin
    res = subprocess.run(
        [sys.executable, "-m", "claire.cli", "doc", doc_id, "--pin", "--json"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    agent_out = AgentOutput.model_validate(json.loads(res.stdout))
    assert agent_out.status == "success"
    assert agent_out.data["pinned"] is True

    # restore unpin
    res = subprocess.run(
        [sys.executable, "-m", "claire.cli", "doc", doc_id, "--unpin", "--json"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    agent_out = AgentOutput.model_validate(json.loads(res.stdout))
    assert agent_out.status == "success"
    assert agent_out.data["pinned"] is False


def test_cli_unrecognized_argument_fails():
    """알 수 없거나 오타가 발생한 옵션(-no-tombstone 등) 입력 시 즉시 2번 코드로 실패하고 중단되는지 검증."""
    res = subprocess.run(
        [sys.executable, "-m", "claire.cli", "purge", "-no-tombstone", "https://example.com/test"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 2
    assert "unrecognized arguments: -no-tombstone" in res.stderr
    assert "claire purge: [Dry-Run]" not in res.stdout



