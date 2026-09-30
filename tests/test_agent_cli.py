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
        [sys.executable, "-m", "claire.cli", "share", "doc_ea88a62a", "--json"],
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
        [sys.executable, "-m", "claire.cli", "doc", "doc_ea88a62a", "--pin", "--json"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    agent_out = AgentOutput.model_validate(json.loads(res.stdout))
    assert agent_out.status == "success"
    assert agent_out.data["pinned"] is True

    # restore unpin
    res = subprocess.run(
        [sys.executable, "-m", "claire.cli", "doc", "doc_ea88a62a", "--unpin", "--json"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    agent_out = AgentOutput.model_validate(json.loads(res.stdout))
    assert agent_out.status == "success"
    assert agent_out.data["pinned"] is False


