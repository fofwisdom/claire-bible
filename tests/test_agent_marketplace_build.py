"""Build, determinism, security, and MCP contract tests for scripts/build_agent_marketplace.py."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
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
    assert marketplace["metadata"]["version"] == "0.4.3"
    assert [p["name"] for p in marketplace["plugins"]] == ["claire"]
    assert marketplace["plugins"][0]["source"] == "./plugins/claude/claire"

    claude_dir = repo_a / "plugins" / "claude" / "claire"
    claude_plugin = json.loads((claude_dir / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert claude_plugin["name"] == "claire"
    assert claude_plugin["version"] == "0.4.3"
    assert claude_plugin["repository"] == "https://github.com/fofwisdom/claire-agents"
    assert sorted(p.name for p in (claude_dir / "skills").iterdir()) == ["extract-runner", "ingest", "query"]

    claude_extractor = (claude_dir / "agents" / "extractor.md").read_text(encoding="utf-8")
    assert "mcp__claire__extract_read" in claude_extractor
    assert "mcp__claire__extract_submit" in claude_extractor
    assert "force=True" in claude_extractor

    claude_ingest = (claude_dir / "skills" / "ingest" / "SKILL.md").read_text(encoding="utf-8")
    assert "extract_stage(" in claude_ingest
    assert "extract_queue(" in claude_ingest
    assert "force=True" in claude_ingest
    assert "claire:extract-runner" in claude_ingest
    assert "/claire:extract" not in claude_ingest

    claude_runner = (claude_dir / "skills" / "extract-runner" / "SKILL.md").read_text(encoding="utf-8")
    assert "claire:extractor" in claude_runner
    assert "extract_queue(" in claude_runner
    assert len(claude_runner.splitlines()) <= 20

    assert "kb_list()" in (claude_dir / "skills" / "query" / "SKILL.md").read_text(encoding="utf-8")
    claude_readme = (claude_dir / "README.md").read_text(encoding="utf-8")
    assert "/claire:ingest" in claude_readme
    assert "--scope user" in claude_readme
    assert "/claire:extract" not in claude_readme

    extractor_fm = builder._frontmatter(claude_extractor)
    assert set(extractor_fm) == {"name", "description", "tools", "model"}
    assert extractor_fm["name"] == "extractor"

    runner_fm = builder._frontmatter(claude_runner)
    assert runner_fm["name"] == "extract-runner"
    assert runner_fm["user-invocable"] == "false"
    assert runner_fm["context"] == "fork"
    assert runner_fm["agent"] == "claire:extractor"
    assert runner_fm["allowed-tools"] == extractor_fm["tools"]

    ingest_fm = builder._frontmatter(claude_ingest)
    assert ingest_fm["argument-hint"] == '"[url | file path | note | document_id]"'
    assert ingest_fm["allowed-tools"] == "[mcp__claire__extract_stage, mcp__claire__extract_queue, mcp__claire__kb_list, Skill(claire:extract-runner)]"

    query_fm = builder._frontmatter((claude_dir / "skills" / "query" / "SKILL.md").read_text(encoding="utf-8"))
    assert "extract_read" not in query_fm["allowed-tools"]
    assert "contrib_submit" not in query_fm["allowed-tools"]

    hooks_bytes = (claude_dir / "hooks" / "hooks.json").read_bytes()
    assert hooks_bytes == builder.claude_guard_hooks("claire:extractor")
    guard = json.loads(hooks_bytes)["hooks"]["PreToolUse"]
    assert len(guard) == 1 and guard[0]["matcher"] == "^mcp__.*__extract_read$"
    assert "Use /claire:ingest, or give the document_id to claire:extractor." in guard[0]["hooks"][0]["command"]
    assert "/claire:extract" not in guard[0]["hooks"][0]["command"]
    assert guard[0]["hooks"][0]["command"].endswith("exit 2")
    assert not (repo_a / "plugins" / "antigravity" / "claire" / "hooks").exists()
    assert "subagent: true" in (repo_a / "plugins" / "antigravity" / "claire" / "agents" / "claire-extractor.md").read_text(encoding="utf-8")

    ag_dir = repo_a / "plugins" / "antigravity" / "claire"
    ag_plugin = json.loads((ag_dir / "plugin.json").read_text(encoding="utf-8"))
    assert ag_plugin["name"] == "claire"
    assert ag_plugin["displayName"] == "Claire Bible"
    assert ag_plugin["version"] == "0.4.3"
    assert ag_plugin["logo"] == "assets/logo.svg"
    assert (ag_dir / "assets" / "logo.svg").is_file()
    assert not (ag_dir / "mcp_config.json.tmpl").exists()
    assert ag_plugin["homepage"] == "https://github.com/fofwisdom/claire-agents#antigravity"
    assert ag_plugin["repository"] == "https://github.com/fofwisdom/claire-agents"
    assert isinstance(ag_plugin["suggestedPrompts"], list)
    assert len(ag_plugin["suggestedPrompts"]) == 3
    for prompt in ag_plugin["suggestedPrompts"]:
        assert any("가" <= ch <= "힣" for ch in prompt)
        assert any("A" <= ch <= "Z" or "a" <= ch <= "z" for ch in prompt)
    assert "client_id" not in ag_plugin
    assert "required_scopes" not in ag_plugin
    assert "files" not in ag_plugin
    assert sorted(p.name for p in (ag_dir / "skills").iterdir()) == ["claire-ingest", "claire-query"]

    ag_readme = (ag_dir / "README.md").read_text(encoding="utf-8")
    assert "fofwisdom/claire-agents" in ag_readme
    assert "docs/antigravity.ko.md" in ag_readme
    assert "docs/antigravity.en.md" in ag_readme
    assert "MCP Servers -> claire" in ag_readme
    assert "Allow using this MCP tool?" in ag_readme
    assert "Yes, and always allow" in ag_readme
    assert not re.search(r"\bclaire-extract(?![a-z0-9_-])", ag_readme)

    mcp_tmpl = (repo_a / "templates" / "mcp_config.json.tmpl").read_text(encoding="utf-8")
    assert "{{MCP_URL}}" in mcp_tmpl
    ag_rules = (ag_dir / "rules" / "AGENTS.md").read_text(encoding="utf-8")
    assert "fofwisdom/claire-agents" in ag_rules
    assert "extract_read" in ag_rules
    assert "gemini-3.8-flash" in ag_rules
    assert "/clients/" not in ag_rules
    assert "clients.<domain>" not in ag_rules
    assert "extract_spec()" in (ag_dir / "agents" / "claire-extractor.md").read_text(encoding="utf-8")
    ag_extractor = (ag_dir / "agents" / "claire-extractor.md").read_text(encoding="utf-8")
    ag_extractor_fm = builder._frontmatter(ag_extractor)
    assert ag_extractor_fm["subagent"] == "true"
    assert ag_extractor_fm["mainAgent"] == "false"
    assert ag_extractor_fm["tools"] == "[send_message]"
    assert ag_extractor_fm["inheritMcp"] == "true"
    assert 'ServerName: "claire"' in ag_extractor
    assert "call_mcp_tool" in ag_extractor
    assert "gemini-3.8-flash" in ag_extractor
    ag_ingest = (ag_dir / "skills" / "claire-ingest" / "SKILL.md").read_text(encoding="utf-8")
    assert "claire-ingest" in ag_ingest
    assert "extract_queue(" in ag_ingest
    assert "force=True" in ag_ingest
    assert "force=True" in ag_extractor
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
        assert "/claire:extract" not in guide_text
        assert "claire-extract " not in guide_text
        if guide_name.startswith("antigravity."):
            assert "MCP Servers" in guide_text
            assert "Allow using this MCP tool?" in guide_text
            assert "Yes, and always allow" in guide_text
            assert "Add MCP Server (+)" not in guide_text
            assert "claire-antigravity-v0.4.3.zip" in guide_text
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
    assert "claire-ingest" in top_readme and "claire-query" in top_readme
    assert "/claire:ingest" in top_readme and "/claire:query" in top_readme
    assert "3 starter suggested prompts" in top_readme
    assert "/claire:extract" not in top_readme

    notes_a = (out_a / "release_notes.md").read_bytes()
    notes_b = (out_b / "release_notes.md").read_bytes()
    assert notes_a == notes_b
    notes_text = notes_a.decode("utf-8")
    assert "claire-antigravity-v0.4.3.zip" in notes_text
    assert "MCP Servers -> claire" in notes_text
    assert "Allow using this MCP tool?" in notes_text
    assert "docs/antigravity.ko.md" in notes_text
    assert "docs/antigravity.en.md" in notes_text
    assert "3 starter suggested prompts" in notes_text
    assert "/claire:ingest" in notes_text and "/claire:query" in notes_text
    assert "/claire:extract" not in notes_text
    assert "release_notes.md" not in sums_a.decode("utf-8")

    manifest = json.loads((repo_a / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["repo"] == "fofwisdom/claire-agents"
    assert manifest["version"] == "0.4.3"
    assert [p["id"] for p in manifest["plugins"]] == ["claire-antigravity", "claire-claude"]
    assert all(p["plugin_name"] == "claire" for p in manifest["plugins"])
    manifest_ag = next(p for p in manifest["plugins"] if p["id"] == "claire-antigravity")
    assert manifest_ag["client_id"] == "antigravity"
    assert manifest_ag["path"] == "plugins/antigravity/claire"
    assert manifest_ag["required_scopes"] == ["readonly", "write"]
    assert any(f["path"] == "README.md" for f in manifest_ag["files"])
    assert any(f["path"] == "assets/logo.svg" for f in manifest_ag["files"])
    assert any(f["path"] == "skills/claire-ingest/SKILL.md" for f in manifest_ag["files"])

    with tarfile.open(out_a / "releases" / "claire-antigravity-v0.4.3.tar.gz", "r:gz") as tf:
        assert "claire/plugin.json" in tf.getnames()
        assert "claire/assets/logo.svg" in tf.getnames()
        assert "claire/README.md" in tf.getnames()
        assert "claire/skills/claire-ingest/SKILL.md" in tf.getnames()
        assert "claire/skills/claire-query/SKILL.md" in tf.getnames()
        assert "claire/skills/claire-extract/SKILL.md" not in tf.getnames()
        assert "claire/mcp_config.json.tmpl" not in tf.getnames()
    with zipfile.ZipFile(out_a / "releases" / "claire-claude-v0.4.3.zip", "r") as zf:
        assert "claire/.claude-plugin/plugin.json" in zf.namelist()
        assert "claire/skills/ingest/SKILL.md" in zf.namelist()
        assert "claire/skills/extract-runner/SKILL.md" in zf.namelist()
        assert "claire/skills/query/SKILL.md" in zf.namelist()
        assert "claire/skills/extract/SKILL.md" not in zf.namelist()
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


def test_bundle_conformance_and_forked_runner_gates(tmp_path: Path) -> None:
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

    runner_tmpl_rel = Path("common") / "runner" / "SKILL.md.tmpl"

    fm_src = _copy_source_tree(tmp_path / "frontmatter")
    fm_path = fm_src / runner_tmpl_rel
    fm_path.write_text(fm_path.read_text(encoding="utf-8").replace("context: fork\n", "context: fork\npermissionMode: bypassPermissions\n"), encoding="utf-8")
    with pytest.raises(builder.BuildValidationError, match="permissionMode"):
        builder.build_marketplace(fm_src, mcp_tools_file=MCP_TOOLS_FILE)

    agent_src = _copy_source_tree(tmp_path / "agent_ref")
    agent_path = agent_src / runner_tmpl_rel
    agent_path.write_text(agent_path.read_text(encoding="utf-8").replace("agent: {{EXTRACTOR_REF}}", "agent: general-purpose"), encoding="utf-8")
    with pytest.raises(builder.BuildValidationError, match="is not an agent of this bundle"):
        builder.build_marketplace(agent_src, mcp_tools_file=MCP_TOOLS_FILE)

    main_read_src = _copy_source_tree(tmp_path / "main_read")
    main_read_path = main_read_src / runner_tmpl_rel
    main_read_path.write_text(main_read_path.read_text(encoding="utf-8").replace("context: fork\nagent: {{EXTRACTOR_REF}}\n", ""), encoding="utf-8")
    with pytest.raises(builder.BuildValidationError, match="must set context: fork|must not declare extract_read"):
        builder.build_marketplace(main_read_src, mcp_tools_file=MCP_TOOLS_FILE)

    invocable_src = _copy_source_tree(tmp_path / "invocable_fork")
    invocable_path = invocable_src / runner_tmpl_rel
    invocable_path.write_text(invocable_path.read_text(encoding="utf-8").replace("user-invocable: false\n", ""), encoding="utf-8")
    with pytest.raises(builder.BuildValidationError, match="user-invocable: false"):
        builder.build_marketplace(invocable_src, mcp_tools_file=MCP_TOOLS_FILE)

    extra_skill_src = _copy_source_tree(tmp_path / "extra_skill")
    (extra_skill_src / claude_overrides / "skills" / "extra").mkdir(parents=True)
    (extra_skill_src / claude_overrides / "skills" / "extra" / "SKILL.md.tmpl").write_text("---\nname: extra\ndescription: extra\n---\n", encoding="utf-8")
    with pytest.raises(builder.BuildValidationError, match="bundle skills"):
        builder.build_marketplace(extra_skill_src, mcp_tools_file=MCP_TOOLS_FILE)

    skill_src = _copy_source_tree(tmp_path / "skill_ref")
    skill_path = skill_src / runner_tmpl_rel
    skill_path.write_text(skill_path.read_text(encoding="utf-8").replace("allowed-tools: {{EXTRACTOR_TOOLS}}", "allowed-tools: [Skill(claire:missing)]"), encoding="utf-8")
    with pytest.raises(builder.BuildValidationError, match="is not a skill of this bundle"):
        builder.build_marketplace(skill_src, mcp_tools_file=MCP_TOOLS_FILE)


def test_adapter_contract_suggested_prompts_and_retired_skill_gates(tmp_path: Path) -> None:
    missing_adapter_src = _copy_source_tree(tmp_path / "missing_adapter")
    claude_toml = missing_adapter_src / "clients" / "claude" / "client.toml"
    claude_toml.write_text(claude_toml.read_text(encoding="utf-8").split("[adapter]")[0], encoding="utf-8")
    with pytest.raises(builder.BuildValidationError, match=r"missing required \[adapter\] table"):
        builder.build_marketplace(missing_adapter_src, mcp_tools_file=MCP_TOOLS_FILE)

    bad_adapter_key_src = _copy_source_tree(tmp_path / "bad_adapter_key")
    ag_toml = bad_adapter_key_src / "clients" / "antigravity" / "client.toml"
    ag_toml.write_text(ag_toml.read_text(encoding="utf-8") + 'extra_field = "nope"\n', encoding="utf-8")
    with pytest.raises(builder.BuildValidationError, match=r"unknown \[adapter\] keys"):
        builder.build_marketplace(bad_adapter_key_src, mcp_tools_file=MCP_TOOLS_FILE)

    bad_handoff_src = _copy_source_tree(tmp_path / "bad_handoff")
    bad_claude_toml = bad_handoff_src / "clients" / "claude" / "client.toml"
    bad_claude_toml.write_text(bad_claude_toml.read_text(encoding="utf-8").replace('internal_runner_skill = "extract-runner"\n', ""), encoding="utf-8")
    with pytest.raises(builder.BuildValidationError, match="internal_runner_skill"):
        builder.build_marketplace(bad_handoff_src, mcp_tools_file=MCP_TOOLS_FILE)

    bad_mode_src = _copy_source_tree(tmp_path / "bad_mode")
    bad_mode_toml = bad_mode_src / "clients" / "antigravity" / "client.toml"
    bad_mode_toml.write_text(bad_mode_toml.read_text(encoding="utf-8").replace('guard_mode = "rules"', 'guard_mode = "invalid"'), encoding="utf-8")
    with pytest.raises(builder.BuildValidationError, match=r"\[adapter\]\.guard_mode"):
        builder.build_marketplace(bad_mode_src, mcp_tools_file=MCP_TOOLS_FILE)

    bad_mcp_mode_src = _copy_source_tree(tmp_path / "bad_mcp_mode")
    bad_mcp_mode_toml = bad_mcp_mode_src / "clients" / "antigravity" / "client.toml"
    bad_mcp_mode_toml.write_text(bad_mcp_mode_toml.read_text(encoding="utf-8").replace('extractor_mcp_mode = "inherit_mcp"', 'extractor_mcp_mode = "invalid"'), encoding="utf-8")
    with pytest.raises(builder.BuildValidationError, match=r"\[adapter\]\.extractor_mcp_mode"):
        builder.build_marketplace(bad_mcp_mode_src, mcp_tools_file=MCP_TOOLS_FILE)

    too_many_prompts_src = _copy_source_tree(tmp_path / "four_prompts")
    four_toml = too_many_prompts_src / "clients" / "antigravity" / "client.toml"
    four_toml.write_text(four_toml.read_text(encoding="utf-8").replace("]\n\n[adapter]", '  "네 번째 프롬프트 (Fourth prompt)",\n]\n\n[adapter]'), encoding="utf-8")
    with pytest.raises(builder.BuildValidationError, match="1 to 3 non-empty strings"):
        builder.build_marketplace(too_many_prompts_src, mcp_tools_file=MCP_TOOLS_FILE)

    retired_skill_src = _copy_source_tree(tmp_path / "retired_skill")
    q_tmpl = retired_skill_src / "common" / "skills" / "query" / "SKILL.md.tmpl"
    q_tmpl.write_text(q_tmpl.read_text(encoding="utf-8") + "\nSee /claire:extract for queue items.\n", encoding="utf-8")
    with pytest.raises(builder.BuildValidationError, match="retired extract skill reference"):
        builder.build_marketplace(retired_skill_src, mcp_tools_file=MCP_TOOLS_FILE)

    retired_doc_src = _copy_source_tree(tmp_path / "retired_doc")
    doc_tmpl = retired_doc_src / "docs" / "antigravity.en.md.tmpl"
    doc_tmpl.write_text(doc_tmpl.read_text(encoding="utf-8") + "\nRun claire-extract next.\n", encoding="utf-8")
    with pytest.raises(builder.BuildValidationError, match="retired extract skill reference"):
        builder.build_marketplace(retired_doc_src, mcp_tools_file=MCP_TOOLS_FILE)

    retired_notes_src = _copy_source_tree(tmp_path / "retired_notes")
    claude_notes_toml = retired_notes_src / "clients" / "claude" / "client.toml"
    claude_notes_toml.write_text(claude_notes_toml.read_text(encoding="utf-8").replace("/claire:ingest", "/claire:extract"), encoding="utf-8")
    with pytest.raises(builder.BuildValidationError, match="retired extract skill reference"):
        builder.build_marketplace(retired_notes_src, mcp_tools_file=MCP_TOOLS_FILE)
    with pytest.raises(builder.BuildValidationError, match="retired extract skill reference"):
        builder._check_rendered_content("release_notes.md", "Use /claire:extract next.\n")
    with pytest.raises(builder.BuildValidationError, match="retired extract skill reference"):
        builder._check_rendered_content("README.md", "Use claire-extract next.\n")
