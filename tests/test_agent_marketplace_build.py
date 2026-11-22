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
    assert [p["name"] for p in marketplace["plugins"]] == ["claire"]
    assert marketplace["plugins"][0]["source"] == "./plugins/claude/claire"

    claude_dir = repo_a / "plugins" / "claude" / "claire"
    claude_plugin = json.loads((claude_dir / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert claude_plugin["name"] == "claire"
    assert claude_plugin["version"] == "0.3.0"
    assert claude_plugin["repository"] == "https://github.com/fofwisdom/claire-agents"
    claude_extractor = (claude_dir / "agents" / "extractor.md").read_text(encoding="utf-8")
    assert "mcp__claire__extract_read" in claude_extractor
    assert "mcp__claire__extract_submit" in claude_extractor
    assert "extract_stage(" in (claude_dir / "skills" / "ingest" / "SKILL.md").read_text(encoding="utf-8")
    assert "extract_read" in (claude_dir / "skills" / "extract" / "SKILL.md").read_text(encoding="utf-8")
    assert "kb_list()" in (claude_dir / "skills" / "query" / "SKILL.md").read_text(encoding="utf-8")
    assert "/claire:ingest" in (claude_dir / "README.md").read_text(encoding="utf-8")
    assert "--scope user" in (claude_dir / "README.md").read_text(encoding="utf-8")

    extractor_fm = builder._frontmatter(claude_extractor)
    assert set(extractor_fm) == {"name", "description", "tools", "model"}
    assert extractor_fm["name"] == "extractor"
    extract_fm = builder._frontmatter((claude_dir / "skills" / "extract" / "SKILL.md").read_text(encoding="utf-8"))
    assert extract_fm["context"] == "fork"
    assert extract_fm["agent"] == "claire:extractor"
    assert extract_fm["allowed-tools"] == extractor_fm["tools"]
    ingest_fm = builder._frontmatter((claude_dir / "skills" / "ingest" / "SKILL.md").read_text(encoding="utf-8"))
    assert ingest_fm["allowed-tools"] == "[mcp__claire__extract_stage, mcp__claire__kb_list, Skill(claire:extract)]"
    query_fm = builder._frontmatter((claude_dir / "skills" / "query" / "SKILL.md").read_text(encoding="utf-8"))
    assert "extract_read" not in query_fm["allowed-tools"]
    assert "contrib_submit" not in query_fm["allowed-tools"]

    hooks_bytes = (claude_dir / "hooks" / "hooks.json").read_bytes()
    assert hooks_bytes == builder.claude_guard_hooks("claire:extractor")
    guard = json.loads(hooks_bytes)["hooks"]["PreToolUse"]
    assert len(guard) == 1 and guard[0]["matcher"] == "^mcp__.*__extract_read$"
    assert guard[0]["hooks"][0]["command"].endswith("exit 2")
    assert not (repo_a / "plugins" / "antigravity" / "claire" / "hooks").exists()
    assert "subagent: true" in (repo_a / "plugins" / "antigravity" / "claire" / "agents" / "claire-extractor.md").read_text(encoding="utf-8")

    ag_dir = repo_a / "plugins" / "antigravity" / "claire"
    ag_plugin = json.loads((ag_dir / "plugin.json").read_text(encoding="utf-8"))
    assert ag_plugin["name"] == "claire"
    assert ag_plugin["displayName"] == "Claire Bible"
    assert ag_plugin["version"] == "0.3.0"
    assert ag_plugin["logo"] == "assets/logo.svg"
    assert (ag_dir / "assets" / "logo.svg").is_file()
    assert not (ag_dir / "mcp_config.json.tmpl").exists()
    assert ag_plugin["homepage"] == "https://github.com/fofwisdom/claire-agents#antigravity"
    assert ag_plugin["repository"] == "https://github.com/fofwisdom/claire-agents"
    assert isinstance(ag_plugin["suggestedPrompts"], list)
    assert len(ag_plugin["suggestedPrompts"]) == 4
    for prompt in ag_plugin["suggestedPrompts"]:
        assert any("가" <= ch <= "힣" for ch in prompt)
        assert any("A" <= ch <= "Z" or "a" <= ch <= "z" for ch in prompt)
    assert "client_id" not in ag_plugin
    assert "required_scopes" not in ag_plugin
    assert "files" not in ag_plugin
    ag_readme = (ag_dir / "README.md").read_text(encoding="utf-8")
    assert "fofwisdom/claire-agents" in ag_readme
    assert "docs/antigravity.ko.md" in ag_readme
    assert "docs/antigravity.en.md" in ag_readme
    assert "MCP Servers -> claire" in ag_readme
    assert "Allow using this MCP tool?" in ag_readme
    assert "Yes, and always allow" in ag_readme

    mcp_tmpl = (repo_a / "templates" / "mcp_config.json.tmpl").read_text(encoding="utf-8")
    assert "{{MCP_URL}}" in mcp_tmpl
    ag_rules = (ag_dir / "rules" / "AGENTS.md").read_text(encoding="utf-8")
    assert "fofwisdom/claire-agents" in ag_rules
    assert "extract_read" in ag_rules
    assert "/clients/" not in ag_rules
    assert "clients.<domain>" not in ag_rules
    assert "extract_spec()" in (ag_dir / "agents" / "claire-extractor.md").read_text(encoding="utf-8")
    assert "claire-ingest" in (ag_dir / "skills" / "claire-ingest" / "SKILL.md").read_text(encoding="utf-8")
    assert "claire-extract" in (ag_dir / "skills" / "claire-extract" / "SKILL.md").read_text(encoding="utf-8")
    assert "claire-query" in (ag_dir / "skills" / "claire-query" / "SKILL.md").read_text(encoding="utf-8")

    for guide_name, expected_heading in (
        ("antigravity.ko.md", "Antigravity GUI 설치 및 연결 가이드"),
        ("antigravity.en.md", "Antigravity GUI Installation & Setup Guide"),
        ("claude.ko.md", "Claude 설치 및 연결 가이드"),
        ("claude.en.md", "Claude Installation & Setup Guide"),
    ):
        guide_a = repo_a / "docs" / guide_name
        guide_b = out_b / "repo" / "docs" / guide_name
        assert guide_a.is_file()
        assert guide_a.read_bytes() == guide_b.read_bytes()
        guide_text = guide_a.read_text(encoding="utf-8")
        assert expected_heading in guide_text
        assert "fofwisdom/claire-agents" in guide_text
        if guide_name.startswith("antigravity."):
            assert "MCP Servers" in guide_text
            assert "Allow using this MCP tool?" in guide_text
            assert "Yes, and always allow" in guide_text
            assert "Add MCP Server (+)" not in guide_text
            assert "claire-antigravity-v0.3.0.zip" in guide_text
        else:
            assert "claude mcp add --transport http --scope user claire https://" in guide_text
            assert "claude plugin install claire --marketplace fofwisdom/claire-agents" in guide_text
            assert "claude mcp login claire" in guide_text
        in_text_block = False
        for line in guide_text.splitlines():
            if line.strip() == "```text":
                in_text_block = True
                continue
            if in_text_block and line.strip() == "```":
                in_text_block = False
                continue
            if in_text_block:
                assert len(line) <= 100, f"{guide_name} text block line too long: {line!r}"

    top_readme = (repo_a / "README.md").read_text(encoding="utf-8")
    assert "docs/antigravity.ko.md" in top_readme
    assert "docs/antigravity.en.md" in top_readme
    assert "docs/claude.ko.md" in top_readme
    assert "docs/claude.en.md" in top_readme

    notes_a = (out_a / "release_notes.md").read_bytes()
    notes_b = (out_b / "release_notes.md").read_bytes()
    assert notes_a == notes_b
    notes_text = notes_a.decode("utf-8")
    assert "claire-antigravity-v0.3.0.zip" in notes_text
    assert "MCP Servers -> claire" in notes_text
    assert "Allow using this MCP tool?" in notes_text
    assert "docs/antigravity.ko.md" in notes_text
    assert "docs/antigravity.en.md" in notes_text
    assert "release_notes.md" not in sums_a.decode("utf-8")

    manifest = json.loads((repo_a / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["repo"] == "fofwisdom/claire-agents"
    assert [p["id"] for p in manifest["plugins"]] == ["claire-antigravity", "claire-claude"]
    assert all(p["plugin_name"] == "claire" for p in manifest["plugins"])
    manifest_ag = next(p for p in manifest["plugins"] if p["id"] == "claire-antigravity")
    assert manifest_ag["client_id"] == "antigravity"
    assert manifest_ag["path"] == "plugins/antigravity/claire"
    assert manifest_ag["required_scopes"] == ["readonly", "write"]
    assert any(f["path"] == "README.md" for f in manifest_ag["files"])
    assert any(f["path"] == "assets/logo.svg" for f in manifest_ag["files"])
    assert any(f["path"] == "skills/claire-ingest/SKILL.md" for f in manifest_ag["files"])

    with tarfile.open(out_a / "releases" / "claire-antigravity-v0.3.0.tar.gz", "r:gz") as tf:
        assert "claire/plugin.json" in tf.getnames()
        assert "claire/assets/logo.svg" in tf.getnames()
        assert "claire/README.md" in tf.getnames()
        assert "claire/skills/claire-ingest/SKILL.md" in tf.getnames()
        assert "claire/mcp_config.json.tmpl" not in tf.getnames()
    with zipfile.ZipFile(out_a / "releases" / "claire-claude-v0.3.0.zip", "r") as zf:
        assert "claire/.claude-plugin/plugin.json" in zf.namelist()
        assert "claire/skills/ingest/SKILL.md" in zf.namelist()
        assert "claire/skills/query/SKILL.md" in zf.namelist()
        assert "claire/agents/extractor.md" in zf.namelist()
        assert "claire/hooks/hooks.json" in zf.namelist()


def test_marketplace_builder_security_and_contract_rejections(tmp_path: Path) -> None:
    sym_src = _copy_source_tree(tmp_path / "sym")
    (sym_src / "bad-link").symlink_to(sym_src / "marketplace.toml")
    with pytest.raises(builder.BuildValidationError, match="symlinks"):
        builder.build_marketplace(sym_src, mcp_tools_file=MCP_TOOLS_FILE)

    exec_src = _copy_source_tree(tmp_path / "exec")
    os.chmod(exec_src / "common" / "skills" / "query" / "SKILL.md.tmpl", 0o755)
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
    ext_tmpl = mcp_bad_src / "common" / "agents" / "extractor.md.tmpl"
    ext_tmpl.write_text(ext_tmpl.read_text(encoding="utf-8") + "\nCall nonexistent_mcp_tool() next.\n", encoding="utf-8")
    with pytest.raises(builder.BuildValidationError, match="nonexistent_mcp_tool"):
        builder.build_marketplace(mcp_bad_src, mcp_tools_file=MCP_TOOLS_FILE)

    legacy_src = _copy_source_tree(tmp_path / "legacy_name")
    q_tmpl = legacy_src / "common" / "skills" / "query" / "SKILL.md.tmpl"
    q_tmpl.write_text(q_tmpl.read_text(encoding="utf-8") + "\nUse claire-antigravity_claire/overview() here.\n", encoding="utf-8")
    with pytest.raises(builder.BuildValidationError, match="legacy double-prefixed server id"):
        builder.build_marketplace(legacy_src, mcp_tools_file=MCP_TOOLS_FILE)


def test_claude_bundle_gates_reject_hooks_frontmatter_and_bad_references(tmp_path: Path) -> None:
    claude_overrides = Path("clients") / "claude" / "overrides"

    hook_src = _copy_source_tree(tmp_path / "hook")
    (hook_src / claude_overrides / "hooks").mkdir(parents=True)
    (hook_src / claude_overrides / "hooks" / "hooks.json").write_text('{"hooks": {}}\n', encoding="utf-8")
    with pytest.raises(builder.BuildValidationError, match="fixed guard"):
        builder.build_marketplace(hook_src, mcp_tools_file=MCP_TOOLS_FILE)

    extra_hook_src = _copy_source_tree(tmp_path / "extra_hook")
    (extra_hook_src / claude_overrides / "hooks").mkdir(parents=True)
    (extra_hook_src / claude_overrides / "hooks" / "more.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(builder.BuildValidationError, match="fixed guard"):
        builder.build_marketplace(extra_hook_src, mcp_tools_file=MCP_TOOLS_FILE)

    ag_hook_src = _copy_source_tree(tmp_path / "ag_hook")
    (ag_hook_src / "clients" / "antigravity" / "overrides" / "hooks").mkdir(parents=True)
    (ag_hook_src / "clients" / "antigravity" / "overrides" / "hooks" / "hooks.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(builder.BuildValidationError, match="hook files are not allowed"):
        builder.build_marketplace(ag_hook_src, mcp_tools_file=MCP_TOOLS_FILE)

    extract_override = claude_overrides / "skills" / "extract" / "SKILL.md.tmpl"

    fm_src = _copy_source_tree(tmp_path / "frontmatter")
    fm_path = fm_src / extract_override
    fm_path.write_text(fm_path.read_text(encoding="utf-8").replace("context: fork\n", "context: fork\npermissionMode: bypassPermissions\n"), encoding="utf-8")
    with pytest.raises(builder.BuildValidationError, match="permissionMode"):
        builder.build_marketplace(fm_src, mcp_tools_file=MCP_TOOLS_FILE)

    agent_src = _copy_source_tree(tmp_path / "agent_ref")
    agent_path = agent_src / extract_override
    agent_path.write_text(agent_path.read_text(encoding="utf-8").replace("agent: {{EXTRACTOR_REF}}", "agent: general-purpose"), encoding="utf-8")
    with pytest.raises(builder.BuildValidationError, match="is not an agent of this bundle"):
        builder.build_marketplace(agent_src, mcp_tools_file=MCP_TOOLS_FILE)

    main_read_src = _copy_source_tree(tmp_path / "main_read")
    main_read_path = main_read_src / extract_override
    main_read_path.write_text(main_read_path.read_text(encoding="utf-8").replace("context: fork\nagent: {{EXTRACTOR_REF}}\n", ""), encoding="utf-8")
    with pytest.raises(builder.BuildValidationError, match="must not declare extract_read"):
        builder.build_marketplace(main_read_src, mcp_tools_file=MCP_TOOLS_FILE)

    skill_src = _copy_source_tree(tmp_path / "skill_ref")
    skill_path = skill_src / extract_override
    skill_path.write_text(skill_path.read_text(encoding="utf-8").replace("allowed-tools: {{EXTRACTOR_TOOLS}}", "allowed-tools: [Skill(claire:missing)]"), encoding="utf-8")
    with pytest.raises(builder.BuildValidationError, match="is not a skill of this bundle"):
        builder.build_marketplace(skill_src, mcp_tools_file=MCP_TOOLS_FILE)
