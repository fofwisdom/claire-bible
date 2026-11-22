#!/usr/bin/env python3
"""Validate agent client sources and build deterministic fofwisdom/claire-agents outputs."""

from __future__ import annotations

import argparse
import ast
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tarfile
import tempfile
import tomllib
from typing import Any
import zipfile

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE_DIR = REPO_ROOT / "agent_clients"
DEFAULT_MCP_TOOLS_FILE = REPO_ROOT / "src" / "claire" / "api" / "mcp_tools.py"

FORBIDDEN_EXTENSIONS: frozenset[str] = frozenset({
    ".py",
    ".pyc",
    ".pyo",
    ".sh",
    ".bash",
    ".zsh",
    ".bat",
    ".cmd",
    ".ps1",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
})

ALLOWED_TARGETS: frozenset[str] = frozenset({"antigravity", "claude", "cursor"})
ALLOWED_SCOPES: frozenset[str] = frozenset({"readonly", "write"})

REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
CLIENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,31}$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+([-.][0-9A-Za-z.]+)?$")
PLACEHOLDER_RE = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")
TOOL_CALL_RE = re.compile(r"(?<![A-Za-z0-9_])((?:mcp__claire__)?[a-z][a-z0-9_]*)\(")
FRONTMATTER_TOOLS_RE = re.compile(r"^tools:\s*\[([^\]]+)\]\s*$", re.MULTILINE)

SECRET_OR_FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("claire bearer token", re.compile(r"cb_[A-Za-z0-9_-]{8,}")),
    ("github classic token", re.compile(r"ghp_[A-Za-z0-9]{10,}")),
    ("github fine-grained token", re.compile(r"github_pat_[A-Za-z0-9_]{10,}")),
    ("private key header", re.compile(r"BEGIN [A-Z ]*PRIVATE KEY")),
    ("hardcoded production domain", re.compile(r"https://claire\.bible\b")),
    ("legacy self-hosted bundle path", re.compile(r"/clients/[a-z0-9-]+/")),
    ("legacy clients subdomain", re.compile(r"\bclients\.<domain>")),
    ("legacy double-prefixed server id", re.compile(r"claire-antigravity_claire")),
    ("legacy mcp tool name", re.compile(r"\b(?:resolve_entity|kb_candidates|kb_submit|stage_source|list_staged_sources|claim_staged_source|renew_staged_claim|get_extraction_spec|get_staged_source|submit_client_extraction)\b")),
)
_LEGACY_CLAUDE_PLUGIN_ID_RE = re.compile(r"\bclaire-claude\b(?!-v\d)")


class BuildValidationError(ValueError):
    """Raised when source validation, schema checks, or security gates fail."""


def _normalize_text(text: str) -> bytes:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.encode("utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _require_non_empty_str(data: dict[str, Any], key: str, *, context: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BuildValidationError(f"{context}: missing or empty string {key!r}")
    return value.strip()


def _validate_source_tree(source_dir: Path) -> None:
    if not source_dir.is_dir():
        raise BuildValidationError(f"source directory does not exist: {source_dir}")
    root = source_dir.resolve()
    for dirpath, dirnames, filenames in os.walk(source_dir, followlinks=False):
        current = Path(dirpath)
        for name in [*dirnames, *filenames]:
            entry = current / name
            st = os.lstat(entry)
            if stat.S_ISLNK(st.st_mode):
                raise BuildValidationError(f"{entry}: symlinks are not allowed in agent_clients")
            resolved = entry.resolve()
            if not resolved.is_relative_to(root):
                raise BuildValidationError(f"{entry}: path escapes source root")
            if stat.S_ISREG(st.st_mode):
                if st.st_mode & 0o111:
                    raise BuildValidationError(f"{entry}: executable mode bits are not allowed")
                if entry.suffix.lower() in FORBIDDEN_EXTENSIONS:
                    raise BuildValidationError(f"{entry}: forbidden file extension {entry.suffix!r}")


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise BuildValidationError(f"missing required TOML file: {path}")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise BuildValidationError(f"{path}: invalid TOML: {exc}") from exc
    if not isinstance(data, dict):
        raise BuildValidationError(f"{path}: TOML root must be a table")
    return data


def _validate_marketplace_meta(source_dir: Path) -> dict[str, str]:
    meta = _load_toml(source_dir / "marketplace.toml")
    name = _require_non_empty_str(meta, "name", context="marketplace.toml")
    if not ID_RE.fullmatch(name):
        raise BuildValidationError("marketplace.toml: name must match ^[a-z0-9][a-z0-9-]{1,63}$")
    repo = _require_non_empty_str(meta, "repo", context="marketplace.toml")
    if not REPO_RE.fullmatch(repo):
        raise BuildValidationError("marketplace.toml: repo must match owner/repo")
    owner_name = _require_non_empty_str(meta, "owner_name", context="marketplace.toml")
    version = _require_non_empty_str(meta, "version", context="marketplace.toml")
    if not SEMVER_RE.fullmatch(version):
        raise BuildValidationError("marketplace.toml: version must match semver")
    description = _require_non_empty_str(meta, "description", context="marketplace.toml")
    return {"name": name, "repo": repo, "owner_name": owner_name, "version": version, "description": description}


def _validate_client_meta(client_dir: Path) -> dict[str, Any]:
    raw = _load_toml(client_dir / "client.toml")
    ctx = f"clients/{client_dir.name}/client.toml"
    cid = _require_non_empty_str(raw, "id", context=ctx)
    if not ID_RE.fullmatch(cid):
        raise BuildValidationError(f"{ctx}: id must match ^[a-z0-9][a-z0-9-]{{1,63}}$")
    plugin_name = _require_non_empty_str(raw, "plugin_name", context=ctx)
    if not ID_RE.fullmatch(plugin_name):
        raise BuildValidationError(f"{ctx}: plugin_name must match ^[a-z0-9][a-z0-9-]{{1,63}}$")
    client_id = _require_non_empty_str(raw, "client_id", context=ctx)
    if not CLIENT_ID_RE.fullmatch(client_id):
        raise BuildValidationError(f"{ctx}: client_id must match ^[a-z0-9][a-z0-9-]{{1,31}}$")
    target = _require_non_empty_str(raw, "target", context=ctx)
    if target not in ALLOWED_TARGETS:
        raise BuildValidationError(f"{ctx}: unsupported target {target!r}")
    name = _require_non_empty_str(raw, "name", context=ctx)
    agent_name = _require_non_empty_str(raw, "agent_name", context=ctx)
    version = _require_non_empty_str(raw, "version", context=ctx)
    if not SEMVER_RE.fullmatch(version):
        raise BuildValidationError(f"{ctx}: version must match semver")
    license_name = _require_non_empty_str(raw, "license", context=ctx)
    description = _require_non_empty_str(raw, "description", context=ctx)
    scopes_raw = raw.get("required_scopes")
    if not isinstance(scopes_raw, list) or not scopes_raw or not all(isinstance(s, str) and s in ALLOWED_SCOPES for s in scopes_raw):
        raise BuildValidationError(f"{ctx}: required_scopes must be a non-empty list subset of {sorted(ALLOWED_SCOPES)}")
    display_name: str | None = None
    suggested_prompts: list[str] = []
    if target == "antigravity":
        display_name = _require_non_empty_str(raw, "display_name", context=ctx)
        prompts_raw = raw.get("suggested_prompts")
        if not isinstance(prompts_raw, list) or not (1 <= len(prompts_raw) <= 4) or not all(isinstance(p, str) and p.strip() for p in prompts_raw):
            raise BuildValidationError(f"{ctx}: suggested_prompts must be a list of 1 to 4 non-empty strings")
        suggested_prompts = [p.strip() for p in prompts_raw]
    return {
        "dir": client_dir,
        "id": cid,
        "plugin_name": plugin_name,
        "client_id": client_id,
        "target": target,
        "name": name,
        "display_name": display_name,
        "agent_name": agent_name,
        "version": version,
        "license": license_name,
        "required_scopes": list(dict.fromkeys(scopes_raw)),
        "description": description,
        "suggested_prompts": suggested_prompts,
    }


def _render_template(template_text: str, replacements: dict[str, str]) -> str:
    rendered = template_text
    for key, value in replacements.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", value)
    return rendered


def _check_rendered_content(rel_path: str, text: str) -> None:
    placeholders = PLACEHOLDER_RE.findall(text)
    if rel_path.endswith("mcp_config.json.tmpl"):
        unexpected = [p for p in placeholders if p != "MCP_URL"]
        if unexpected:
            raise BuildValidationError(f"{rel_path}: unexpected template placeholders {unexpected}")
        if "MCP_URL" not in placeholders:
            raise BuildValidationError(f"{rel_path}: missing required {{{{MCP_URL}}}} placeholder")
    elif placeholders:
        raise BuildValidationError(f"{rel_path}: unresolved placeholders {placeholders}")
    for label, pattern in SECRET_OR_FORBIDDEN_PATTERNS:
        if pattern.search(text):
            raise BuildValidationError(f"{rel_path}: forbidden pattern detected ({label})")
    if rel_path != "manifest.json" and _LEGACY_CLAUDE_PLUGIN_ID_RE.search(text):
        raise BuildValidationError(f"{rel_path}: forbidden pattern detected (legacy claude plugin id)")


def _extract_mcp_tool_names(mcp_tools_file: Path) -> set[str]:
    source = mcp_tools_file.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(mcp_tools_file))
    tools: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "build_mcp_app":
            for inner in ast.walk(node):
                if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for dec in inner.decorator_list:
                        if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                            if isinstance(dec.func.value, ast.Name) and dec.func.value.id == "mcp" and dec.func.attr == "tool":
                                tools.add(inner.name)
    if not tools:
        raise BuildValidationError(f"{mcp_tools_file}: failed to extract any @mcp.tool definitions")
    return tools


def _verify_mcp_contracts(rendered_files: dict[str, bytes], mcp_tools_file: Path) -> None:
    if not mcp_tools_file.is_file():
        return
    server_tools = _extract_mcp_tool_names(mcp_tools_file)
    for rel_path, raw in rendered_files.items():
        if not rel_path.endswith(".md"):
            continue
        text = raw.decode("utf-8")
        is_claude_plugin = rel_path.startswith("plugins/claude/")
        fm_match = FRONTMATTER_TOOLS_RE.search(text)
        if fm_match:
            declared = [item.strip() for item in fm_match.group(1).split(",") if item.strip()]
            normalized_declared: list[str] = []
            for item in declared:
                if is_claude_plugin:
                    if not item.startswith("mcp__claire__"):
                        raise BuildValidationError(f"{rel_path}: claude frontmatter tool {item!r} must use mcp__claire__ prefix")
                    normalized_declared.append(item.removeprefix("mcp__claire__"))
                else:
                    if item.startswith("mcp__"):
                        raise BuildValidationError(f"{rel_path}: non-claude frontmatter tool {item!r} must not use mcp__ prefix")
                    normalized_declared.append(item)
            missing = [name for name in normalized_declared if name not in server_tools]
            if missing:
                raise BuildValidationError(f"{rel_path}: frontmatter declares unknown MCP tools: {missing}")
            if Path(rel_path).name not in ("extractor.md", "claire-extractor.md") and "extract_read" in normalized_declared:
                raise BuildValidationError(f"{rel_path}: non-extractor file must not declare extract_read")
        if Path(rel_path).name in ("extractor.md", "claire-extractor.md", "SKILL.md", "AGENTS.md"):
            for match in TOOL_CALL_RE.finditer(text):
                raw_name = match.group(1)
                bare_name = raw_name.removeprefix("mcp__claire__") if raw_name.startswith("mcp__claire__") else raw_name
                if bare_name not in server_tools:
                    raise BuildValidationError(f"{rel_path}: calls unknown MCP tool {raw_name!r}")


def _render_top_readme(market: dict[str, str], clients: list[dict[str, Any]]) -> str:
    repo = market["repo"]
    version = market["version"]
    ag_client = next((c for c in clients if c["target"] == "antigravity"), clients[0])
    ag_ver = ag_client["version"]
    ag_pkg = f"{ag_client['id']}-v{ag_ver}"
    lines = [
        f"# {market['name']}",
        "",
        f"> Automated mirror generated from [fofwisdom/claire-bible](https://github.com/fofwisdom/claire-bible) (`agent_clients/`). Do not edit files in this repository manually; submit changes to `fofwisdom/claire-bible`.",
        "",
        "[한국어 안내](#antigravity) · [English](#antigravity) · [Antigravity GUI 가이드 (KO)](./docs/antigravity.ko.md) · [Antigravity GUI Guide (EN)](./docs/antigravity.en.md) · [Claude 가이드 (KO)](./docs/claude.ko.md) · [Claude Guide (EN)](./docs/claude.en.md)",
        "",
        market["description"],
        "",
        "## Architecture: Standalone `claire` Server + `claire` Plugin",
        "",
        "- Register your remote Claire Bible `/mcp` endpoint once as a standalone `claire` OAuth 2.1 MCP server so Antigravity exposes `claire/<tool>` and Claude exposes `mcp__claire__<tool>` without double prefixes.",
        "- Install the **`claire`** plugin for your client to add direct URL/file ingest, isolated extraction (`claire-extractor` / `extractor`), and knowledge graph query skills. Upgrading the plugin never overwrites your global server URL.",
        "",
        "## Supported Agents",
        "",
    ]
    for client in clients:
        lines.append(f"- **`{client['plugin_name']}`** (`{client['target']}`, `{client['id']}-v{client['version']}`): {client['description']}")
    lines.extend([
        "",
        '<a id="antigravity"></a>',
        "## Antigravity (GUI-First Setup)",
        "",
        "- **Step-by-step guides**: [한국어 가이드](./docs/antigravity.ko.md) · [English Guide](./docs/antigravity.en.md)",
        f"- **GitHub Releases**: [Download `{ag_pkg}.zip`](https://github.com/{repo}/releases/latest)",
        "",
        "1. In Claire Bible **Account Settings** (`/settings/account`), switch extraction mode to `client`.",
        "2. In **MCP Settings** (`/settings/mcp`), copy the **Antigravity quick setup prompt** and paste it into Antigravity `+ New Conversation` (installs `~/.gemini/config/plugins/claire` and merges `mcpServers.claire` into `~/.gemini/config/mcp_config.json`).",
        "3. Open **`Customizations -> Installed -> MCP Servers -> claire`** and finish OAuth 2.1 login (`readonly`, `write`).",
        "4. On first run when `Allow using this MCP tool?` (`claire/<tool>`) prompts, pick **`3 Yes, and always allow`** -> **`Submit`**.",
        "",
        '<a id="claude"></a>',
        "## Claude (GUI Marketplace Setup)",
        "",
        "- **Step-by-step guides**: [한국어 안내](./docs/claude.ko.md) · [English Guide](./docs/claude.en.md)",
        "",
        f"1. Enter **`{repo}`** in Claude **Marketplace management**, sync, and install **`claire`**.",
        "2. Add your Claire Bible `/mcp` connector named **`claire`** (`https://<your-claire-server>/mcp`) and sign in via OAuth 2.1 (`readonly`, `write`).",
        "3. Use `/claire:ingest`, `/claire:extract`, and `/claire:query`.",
        "",
        "<details>",
        "<summary>CLI &amp; Advanced Verification (Terminal / SHA256SUMS)</summary>",
        "",
        f"- Claude CLI: `/plugin marketplace add {repo}` -> install `claire` -> `claude mcp add --transport http claire https://<your-claire-server>/mcp`.",
        f"- Antigravity CLI / checksum check: download `{ag_pkg}.tar.gz` and `SHA256SUMS` from [GitHub Releases](https://github.com/{repo}/releases/latest), run `sha256sum -c SHA256SUMS --ignore-missing`, extract into `~/.gemini/config/plugins/`, and add `mcpServers.claire` in `~/.gemini/config/mcp_config.json`.",
        "",
        "</details>",
        "",
        "## Server Authentication Model",
        "",
        f"This repository (`v{version}`) distributes static client skills, subagents, rules, and templates only. Zero server tokens or secrets are included. Every Claire Bible instance shares these bundles and authenticates dynamically over OAuth 2.1 (`PKCE S256` or `/device`).",
        "",
    ])
    return "\n".join(lines)


def render_release_notes(market: dict[str, str], clients: list[dict[str, Any]]) -> bytes:
    repo = market["repo"]
    version = market["version"]
    ag_client = next((c for c in clients if c["target"] == "antigravity"), clients[0])
    ag_version = ag_client["version"]
    ag_asset = f"{ag_client['id']}-v{ag_version}.zip"
    ag_tar = f"{ag_client['id']}-v{ag_version}.tar.gz"
    lines = [
        f"# Claire Bible Agent Plugins v{version}",
        "",
        f"[한국어 상세 가이드 (Antigravity)](https://github.com/{repo}/blob/main/docs/antigravity.ko.md) · [English Setup Guide (Antigravity)](https://github.com/{repo}/blob/main/docs/antigravity.en.md) · [Claude 가이드 (KO)](https://github.com/{repo}/blob/main/docs/claude.ko.md) · [Claude Guide (EN)](https://github.com/{repo}/blob/main/docs/claude.en.md)",
        "",
        "## Which file do I need? / 어떤 파일을 내려받으면 되나요?",
        "",
        "| Client / 앱 | Recommended Action / 권장 방법 | Asset / 내려받을 파일 |",
        "| :--- | :--- | :--- |",
        f"| **Antigravity (GUI)** | Claire `/settings/mcp`에서 빠른 설치 문구를 복사해 채팅창에 붙여넣거나 아래 `.zip`을 내려받아 `plugins/claire`로 설치하고 `mcp_config.json`에 `claire` 서버를 병합하세요. | **`{ag_asset}`** |",
        f"| **Claude** | 파일을 내려받을 필요가 없습니다. 마켓플레이스 관리에 `{repo}`를 입력해 **`claire`** 플러그인을 설치하세요. | 없음 (No download needed) |",
        "",
        "## Antigravity Quick Setup (3 Steps)",
        "",
        "1. Switch account extraction mode to **`client`** in Claire `/settings/account`.",
        f"2. Paste the **Antigravity quick setup prompt** from Claire `/settings/mcp` into Antigravity `+ New Conversation` (or extract `{ag_asset}` into `~/.gemini/config/plugins/` as `claire/` and merge `mcpServers.claire` into `~/.gemini/config/mcp_config.json`).",
        "3. Open **`Customizations -> Installed -> MCP Servers -> claire`** to finish OAuth 2.1 login (`readonly`, `write`). When `Allow using this MCP tool?` appears (`claire/<tool>`), pick **`3 Yes, and always allow`** -> **`Submit`**.",
        "",
        "<details>",
        "<summary>Checksums &amp; CLI Archives (SHA256SUMS &amp; .tar.gz)</summary>",
        "",
        "```bash",
        "sha256sum -c SHA256SUMS --ignore-missing",
        f"tar -xzf {ag_tar} -C ~/.gemini/config/plugins",
        "```",
        "",
        "*(macOS: `shasum -a 256 -c SHA256SUMS --ignore-missing`)*",
        "",
        "</details>",
        "",
    ]
    text = "\n".join(lines)
    _check_rendered_content("release_notes.md", text)
    return _normalize_text(text)


def _json_bytes(obj: Any, *, sort_keys: bool = False) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=sort_keys) + "\n").encode("utf-8")


def _build_deterministic_tar_gz(prefix: str, files: dict[str, bytes]) -> bytes:
    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        for rel_path in sorted(files):
            data = files[rel_path]
            info = tarfile.TarInfo(name=f"{prefix}/{rel_path}")
            info.size = len(data)
            info.mode = 0o644
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            tf.addfile(info, io.BytesIO(data))
    gz_buf = io.BytesIO()
    with gzip.GzipFile(fileobj=gz_buf, mode="wb", mtime=0, compresslevel=9) as gz:
        gz.write(tar_buf.getvalue())
    return gz_buf.getvalue()


def _build_deterministic_zip(prefix: str, files: dict[str, bytes]) -> bytes:
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for rel_path in sorted(files):
            info = zipfile.ZipInfo(filename=f"{prefix}/{rel_path}", date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, files[rel_path])
    return zip_buf.getvalue()


def build_marketplace(source_dir: Path, *, mcp_tools_file: Path | None = None) -> tuple[dict[str, bytes], dict[str, bytes]]:
    """Validate source tree and return (repo_files, release_files) mappings."""
    _validate_source_tree(source_dir)
    market = _validate_marketplace_meta(source_dir)

    common_dir = source_dir / "common"
    extractor_tmpl = (common_dir / "agents" / "extractor.md.tmpl").read_text(encoding="utf-8")
    ingest_skill_tmpl = (common_dir / "skills" / "ingest" / "SKILL.md.tmpl").read_text(encoding="utf-8")
    extract_skill_tmpl = (common_dir / "skills" / "extract" / "SKILL.md.tmpl").read_text(encoding="utf-8")
    query_skill_tmpl = (common_dir / "skills" / "query" / "SKILL.md.tmpl").read_text(encoding="utf-8")
    agents_md_tmpl = (common_dir / "rules" / "AGENTS.md.tmpl").read_text(encoding="utf-8")

    clients_root = source_dir / "clients"
    if not clients_root.is_dir():
        raise BuildValidationError(f"missing clients directory: {clients_root}")
    clients: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_client_ids: set[str] = set()
    for entry in sorted(clients_root.iterdir(), key=lambda p: p.name):
        if not entry.is_dir():
            continue
        meta = _validate_client_meta(entry)
        if meta["id"] in seen_ids:
            raise BuildValidationError(f"duplicate plugin id: {meta['id']!r}")
        if meta["client_id"] in seen_client_ids:
            raise BuildValidationError(f"duplicate client_id: {meta['client_id']!r}")
        seen_ids.add(meta["id"])
        seen_client_ids.add(meta["client_id"])
        clients.append(meta)

    if not clients:
        raise BuildValidationError("no client definitions found under agent_clients/clients/")

    repo_files: dict[str, bytes] = {}
    release_files: dict[str, bytes] = {}
    manifest_plugins: list[dict[str, Any]] = []
    claude_marketplace_plugins: list[dict[str, Any]] = []

    mcp_tmpl_path = source_dir / "templates" / "mcp_config.json.tmpl"
    if not mcp_tmpl_path.is_file():
        raise BuildValidationError(f"missing required reference template: {mcp_tmpl_path}")
    mcp_tmpl_text = mcp_tmpl_path.read_text(encoding="utf-8")
    _check_rendered_content("templates/mcp_config.json.tmpl", mcp_tmpl_text)
    repo_files["templates/mcp_config.json.tmpl"] = _normalize_text(mcp_tmpl_text)

    extractor_tools_bare = ["extract_spec", "extract_queue", "extract_claim", "extract_read", "extract_submit", "kb_list", "entity_search", "graph_neighbors"]

    for client in clients:
        cid = client["id"]
        plugin_name = client["plugin_name"]
        target = client["target"]
        if target == "claude":
            ingest_skill = "ingest"
            extract_skill = "extract"
            query_skill = "query"
            extractor_agent = "extractor"
            extractor_path = "agents/extractor.md"
            extractor_tools = "[" + ", ".join(f"mcp__claire__{t}" for t in extractor_tools_bare) + "]"
        else:
            ingest_skill = "claire-ingest"
            extract_skill = "claire-extract"
            query_skill = "claire-query"
            extractor_agent = "claire-extractor"
            extractor_path = "agents/claire-extractor.md"
            extractor_tools = "[" + ", ".join(extractor_tools_bare) + "]"

        replacements = {
            "CLIENT_ID": client["client_id"],
            "AGENT_NAME": client["agent_name"],
            "MARKETPLACE_REPO": market["repo"],
            "PLUGIN_ID": cid,
            "PLUGIN_NAME": plugin_name,
            "VERSION": client["version"],
            "INGEST_SKILL": ingest_skill,
            "EXTRACT_SKILL": extract_skill,
            "QUERY_SKILL": query_skill,
            "EXTRACTOR_AGENT": extractor_agent,
            "EXTRACTOR_PATH": extractor_path,
            "EXTRACTOR_TOOLS": extractor_tools,
        }
        plugin_rel_root = f"plugins/{target}/{plugin_name}"
        plugin_bundle_files: dict[str, bytes] = {}

        readme_tmpl_path = client["dir"] / "README.md.tmpl"
        if not readme_tmpl_path.is_file():
            raise BuildValidationError(f"{client['dir']}: missing README.md.tmpl for {target} target")
        plugin_bundle_files["README.md"] = _normalize_text(_render_template(readme_tmpl_path.read_text(encoding="utf-8"), replacements))
        plugin_bundle_files[extractor_path] = _normalize_text(_render_template(extractor_tmpl, replacements))
        plugin_bundle_files[f"skills/{ingest_skill}/SKILL.md"] = _normalize_text(_render_template(ingest_skill_tmpl, replacements))
        plugin_bundle_files[f"skills/{extract_skill}/SKILL.md"] = _normalize_text(_render_template(extract_skill_tmpl, replacements))
        plugin_bundle_files[f"skills/{query_skill}/SKILL.md"] = _normalize_text(_render_template(query_skill_tmpl, replacements))

        if target == "claude":
            plugin_manifest = {
                "name": plugin_name,
                "version": client["version"],
                "description": client["description"],
                "author": {"name": market["owner_name"]},
                "repository": f"https://github.com/{market['repo']}",
                "license": client["license"],
                "keywords": ["claire-bible", "mcp", "knowledge-graph", "extraction"],
            }
            plugin_bundle_files[".claude-plugin/plugin.json"] = _json_bytes(plugin_manifest)
            claude_marketplace_plugins.append({
                "name": plugin_name,
                "source": f"./{plugin_rel_root}",
                "description": client["description"],
                "version": client["version"],
                "license": client["license"],
            })
        elif target == "antigravity":
            antigravity_manifest = {
                "name": plugin_name,
                "displayName": client["display_name"],
                "version": client["version"],
                "description": client["description"],
                "logo": "assets/logo.svg",
                "author": {"name": market["owner_name"]},
                "homepage": f"https://github.com/{market['repo']}#antigravity",
                "repository": f"https://github.com/{market['repo']}",
                "suggestedPrompts": client["suggested_prompts"],
            }
            plugin_bundle_files["plugin.json"] = _json_bytes(antigravity_manifest)
            plugin_bundle_files["rules/AGENTS.md"] = _normalize_text(_render_template(agents_md_tmpl, replacements))
        else:
            raise BuildValidationError(f"unsupported build adapter for target {target!r}")

        overrides_dir = client["dir"] / "overrides"
        if overrides_dir.is_dir():
            for ov_path in sorted(overrides_dir.rglob("*"), key=lambda p: p.as_posix()):
                if not ov_path.is_file():
                    continue
                rel_ov = ov_path.relative_to(overrides_dir).as_posix()
                raw_text = ov_path.read_text(encoding="utf-8")
                if rel_ov.endswith(".tmpl"):
                    rel_out = rel_ov[:-5]
                    plugin_bundle_files[rel_out] = _normalize_text(_render_template(raw_text, replacements))
                else:
                    plugin_bundle_files[rel_ov] = _normalize_text(raw_text)

        file_entries: list[dict[str, Any]] = []
        for rel_file, data in sorted(plugin_bundle_files.items()):
            repo_rel = f"{plugin_rel_root}/{rel_file}"
            text = data.decode("utf-8")
            _check_rendered_content(repo_rel, text)
            repo_files[repo_rel] = data
            file_entries.append({
                "path": rel_file,
                "sha256": _sha256_bytes(data),
                "size": len(data),
            })

        manifest_plugins.append({
            "id": cid,
            "plugin_name": plugin_name,
            "client_id": client["client_id"],
            "target": target,
            "version": client["version"],
            "required_scopes": client["required_scopes"],
            "path": plugin_rel_root,
            "files": file_entries,
        })

        tar_name = f"{cid}-v{client['version']}.tar.gz"
        zip_name = f"{cid}-v{client['version']}.zip"
        release_files[tar_name] = _build_deterministic_tar_gz(plugin_name, plugin_bundle_files)
        release_files[zip_name] = _build_deterministic_zip(plugin_name, plugin_bundle_files)

    if not claude_marketplace_plugins:
        raise BuildValidationError("at least one claude target plugin is required for .claude-plugin/marketplace.json")

    claude_marketplace = {
        "name": market["name"],
        "owner": {"name": market["owner_name"]},
        "metadata": {
            "description": market["description"],
            "version": market["version"],
        },
        "plugins": claude_marketplace_plugins,
    }
    repo_files[".claude-plugin/marketplace.json"] = _json_bytes(claude_marketplace)
    _check_rendered_content(".claude-plugin/marketplace.json", repo_files[".claude-plugin/marketplace.json"].decode("utf-8"))

    manifest_doc = {
        "name": market["name"],
        "repo": market["repo"],
        "version": market["version"],
        "plugins": sorted(manifest_plugins, key=lambda item: str(item["id"])),
    }
    repo_files["manifest.json"] = _json_bytes(manifest_doc, sort_keys=True)
    _check_rendered_content("manifest.json", repo_files["manifest.json"].decode("utf-8"))

    readme_bytes = _normalize_text(_render_top_readme(market, clients))
    _check_rendered_content("README.md", readme_bytes.decode("utf-8"))
    repo_files["README.md"] = readme_bytes

    docs_dir = source_dir / "docs"
    if not docs_dir.is_dir():
        raise BuildValidationError(f"missing docs directory: {docs_dir}")
    doc_replacements = {
        "MARKETPLACE_REPO": market["repo"],
        "VERSION": market["version"],
    }
    for doc_name in ("antigravity.ko.md.tmpl", "antigravity.en.md.tmpl", "claude.ko.md.tmpl", "claude.en.md.tmpl"):
        doc_path = docs_dir / doc_name
        if not doc_path.is_file():
            raise BuildValidationError(f"missing guide template: {doc_path}")
        rendered_doc = _render_template(doc_path.read_text(encoding="utf-8"), doc_replacements)
        out_rel = f"docs/{doc_name[:-5]}"
        _check_rendered_content(out_rel, rendered_doc)
        repo_files[out_rel] = _normalize_text(rendered_doc)

    if mcp_tools_file is not None:
        _verify_mcp_contracts(repo_files, mcp_tools_file)

    checksum_lines = [f"{_sha256_bytes(release_files[name])}  {name}" for name in sorted(release_files)]
    release_files["SHA256SUMS"] = ("\n".join(checksum_lines) + "\n").encode("utf-8")

    return repo_files, release_files


def build_release_notes(source_dir: Path) -> bytes:
    """Validate source metadata and render GitHub Release notes."""
    _validate_source_tree(source_dir)
    market = _validate_marketplace_meta(source_dir)
    clients_root = source_dir / "clients"
    if not clients_root.is_dir():
        raise BuildValidationError(f"missing clients directory: {clients_root}")
    clients = [_validate_client_meta(entry) for entry in sorted(clients_root.iterdir(), key=lambda p: p.name) if entry.is_dir()]
    if not clients:
        raise BuildValidationError("no client definitions found under agent_clients/clients/")
    return render_release_notes(market, clients)


def write_build_outputs(out_dir: Path, repo_files: dict[str, bytes], release_files: dict[str, bytes], *, release_notes: bytes | None = None) -> None:
    """Write built repo and release outputs cleanly to out_dir."""
    repo_out = out_dir / "repo"
    releases_out = out_dir / "releases"
    for target in (repo_out, releases_out):
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
    for rel_path, data in sorted(repo_files.items()):
        dest = repo_out / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    for filename, data in sorted(release_files.items()):
        dest = releases_out / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    if release_notes is not None:
        (out_dir / "release_notes.md").write_bytes(release_notes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate agent_clients/ and build fofwisdom/claire-agents outputs.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE_DIR, help="Path to agent_clients source directory")
    parser.add_argument("--out", type=Path, default=None, help="Output directory for repo/ and releases/")
    parser.add_argument("--check", action="store_true", help="Run all validation gates and dry-run build")
    parser.add_argument("--mcp-tools-file", type=Path, default=DEFAULT_MCP_TOOLS_FILE, help="Path to mcp_tools.py for MCP contract check")
    args = parser.parse_args(argv)

    if not args.check and args.out is None:
        parser.error("pass --check or --out <dir>")

    try:
        repo_files, release_files = build_marketplace(args.source, mcp_tools_file=args.mcp_tools_file)
        release_notes = build_release_notes(args.source)
        if args.out is not None:
            write_build_outputs(args.out, repo_files, release_files, release_notes=release_notes)
        elif args.check:
            with tempfile.TemporaryDirectory() as tmp_dir:
                write_build_outputs(Path(tmp_dir), repo_files, release_files, release_notes=release_notes)
    except BuildValidationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"OK: built {len(repo_files)} repo files and {len(release_files)} release files from {args.source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
