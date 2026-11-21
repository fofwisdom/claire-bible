"""Build, determinism, security, and MCP contract tests for scripts/build_agent_marketplace.py."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tarfile
import zipfile

import pytest

from scripts import build_agent_marketplace as builder

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_CLIENTS_DIR = REPO_ROOT / "agent_clients"
MCP_TOOLS_FILE = REPO_ROOT / "src" / "claire" / "api" / "mcp_tools.py"


def _copy_source_tree(dst: Path) -> Path:
    target = dst / "agent_clients"
    shutil.copytree(AGENT_CLIENTS_DIR, target)
    for path in target.rglob("*"):
        if path.is_file():
            os.chmod(path, 0o644)
        elif path.is_dir():
            os.chmod(path, 0o755)
    return target


def test_marketplace_cli_build_outputs_and_determinism(tmp_path: Path) -> None:
    out_a = tmp_path / "run_a"
    out_b = tmp_path / "run_b"

    rc_a = builder.main(["--source", str(AGENT_CLIENTS_DIR), "--out", str(out_a), "--check", "--mcp-tools-file", str(MCP_TOOLS_FILE)])
    rc_b = builder.main(["--source", str(AGENT_CLIENTS_DIR), "--out", str(out_b), "--check", "--mcp-tools-file", str(MCP_TOOLS_FILE)])
    assert rc_a == 0 and rc_b == 0

    sums_a = (out_a / "releases" / "SHA256SUMS").read_bytes()
    sums_b = (out_b / "releases" / "SHA256SUMS").read_bytes()
    assert sums_a == sums_b

    for tar_path in sorted((out_a / "releases").glob("*.tar.gz")):
        assert tar_path.read_bytes() == (out_b / "releases" / tar_path.name).read_bytes()
    for zip_path in sorted((out_a / "releases").glob("*.zip")):
        assert zip_path.read_bytes() == (out_b / "releases" / zip_path.name).read_bytes()

    repo_a = out_a / "repo"
    marketplace = json.loads((repo_a / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    assert marketplace["name"] == "claire-agents"
    assert [p["name"] for p in marketplace["plugins"]] == ["claire-claude"]
    assert marketplace["plugins"][0]["source"] == "./plugins/claire-claude"

    claude_dir = repo_a / "plugins" / "claire-claude"
    claude_plugin = json.loads((claude_dir / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert claude_plugin["name"] == "claire-claude"
    assert claude_plugin["repository"] == "https://github.com/fofwisdom/claire-agents"
    assert 'client_id="claude"' in (claude_dir / "agents" / "claire-extractor.md").read_text(encoding="utf-8")
    assert 'client_id="claude"' in (claude_dir / "skills" / "claire-extract" / "SKILL.md").read_text(encoding="utf-8")
    assert "fofwisdom/claire-agents" in (claude_dir / "README.md").read_text(encoding="utf-8")

    ag_dir = repo_a / "plugins" / "claire-antigravity"
    ag_plugin = json.loads((ag_dir / "plugin.json").read_text(encoding="utf-8"))
    assert ag_plugin["name"] == "claire-antigravity"
    assert ag_plugin["client_id"] == "antigravity"
    assert ag_plugin["required_scopes"] == ["readonly", "write"]
    mcp_tmpl = (ag_dir / "mcp_config.json.tmpl").read_text(encoding="utf-8")
    assert "{{MCP_URL}}" in mcp_tmpl
    ag_rules = (ag_dir / "rules" / "AGENTS.md").read_text(encoding="utf-8")
    assert "fofwisdom/claire-agents" in ag_rules
    assert "/clients/" not in ag_rules
    assert "clients.<domain>" not in ag_rules
    assert 'client_id="antigravity"' in (ag_dir / "agents" / "claire-extractor.md").read_text(encoding="utf-8")
    assert 'client_id="antigravity"' in (ag_dir / "skills" / "claire-extract" / "SKILL.md").read_text(encoding="utf-8")

    manifest = json.loads((repo_a / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["repo"] == "fofwisdom/claire-agents"
    assert [p["id"] for p in manifest["plugins"]] == ["claire-antigravity", "claire-claude"]

    with tarfile.open(out_a / "releases" / "claire-antigravity-v0.1.0.tar.gz", "r:gz") as tf:
        assert "claire-antigravity/plugin.json" in tf.getnames()
        assert "claire-antigravity/mcp_config.json.tmpl" in tf.getnames()
    with zipfile.ZipFile(out_a / "releases" / "claire-claude-v0.1.0.zip", "r") as zf:
        assert "claire-claude/.claude-plugin/plugin.json" in zf.namelist()
        assert "claire-claude/skills/claire-query/SKILL.md" in zf.namelist()


def test_marketplace_builder_security_and_contract_rejections(tmp_path: Path) -> None:
    sym_src = _copy_source_tree(tmp_path / "sym")
    (sym_src / "bad-link").symlink_to(sym_src / "marketplace.toml")
    with pytest.raises(builder.BuildValidationError, match="symlinks"):
        builder.build_marketplace(sym_src, mcp_tools_file=MCP_TOOLS_FILE)

    exec_src = _copy_source_tree(tmp_path / "exec")
    os.chmod(exec_src / "common" / "skills" / "claire-query" / "SKILL.md", 0o755)
    with pytest.raises(builder.BuildValidationError, match="executable"):
        builder.build_marketplace(exec_src, mcp_tools_file=MCP_TOOLS_FILE)

    py_src = _copy_source_tree(tmp_path / "pyfile")
    (py_src / "common" / "hook.py").write_text("print('bad')\n", encoding="utf-8")
    with pytest.raises(builder.BuildValidationError, match="forbidden file extension"):
        builder.build_marketplace(py_src, mcp_tools_file=MCP_TOOLS_FILE)

    secret_src = _copy_source_tree(tmp_path / "secret")
    readme_tmpl = secret_src / "clients" / "claude" / "README.md.tmpl"
    readme_tmpl.write_text(readme_tmpl.read_text(encoding="utf-8") + "\ncb_secret_token_12345\n", encoding="utf-8")
    with pytest.raises(builder.BuildValidationError, match="claire bearer token"):
        builder.build_marketplace(secret_src, mcp_tools_file=MCP_TOOLS_FILE)

    placeholder_src = _copy_source_tree(tmp_path / "placeholder")
    ph_readme = placeholder_src / "clients" / "claude" / "README.md.tmpl"
    ph_readme.write_text(ph_readme.read_text(encoding="utf-8") + "\n{{UNKNOWN_VAR}}\n", encoding="utf-8")
    with pytest.raises(builder.BuildValidationError, match="unresolved placeholders"):
        builder.build_marketplace(placeholder_src, mcp_tools_file=MCP_TOOLS_FILE)

    mcp_bad_src = _copy_source_tree(tmp_path / "bad_mcp")
    ext_tmpl = mcp_bad_src / "common" / "agents" / "claire-extractor.md.tmpl"
    ext_tmpl.write_text(ext_tmpl.read_text(encoding="utf-8") + "\nCall nonexistent_mcp_tool() next.\n", encoding="utf-8")
    with pytest.raises(builder.BuildValidationError, match="nonexistent_mcp_tool"):
        builder.build_marketplace(mcp_bad_src, mcp_tools_file=MCP_TOOLS_FILE)
