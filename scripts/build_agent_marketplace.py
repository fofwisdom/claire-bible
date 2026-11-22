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
TOOL_CALL_RE = re.compile(r"(?<![A-Za-z0-9_])([a-z][a-z0-9_]*)\(")
FRONTMATTER_TOOLS_RE = re.compile(r"^tools:\s*\[([^\]]+)\]\s*$", re.MULTILINE)

SECRET_OR_FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("claire bearer token", re.compile(r"cb_[A-Za-z0-9_-]{8,}")),
    ("github classic token", re.compile(r"ghp_[A-Za-z0-9]{10,}")),
    ("github fine-grained token", re.compile(r"github_pat_[A-Za-z0-9_]{10,}")),
    ("private key header", re.compile(r"BEGIN [A-Z ]*PRIVATE KEY")),
    ("hardcoded production domain", re.compile(r"https://claire\.bible\b")),
    ("legacy self-hosted bundle path", re.compile(r"/clients/[a-z0-9-]+/")),
    ("legacy clients subdomain", re.compile(r"\bclients\.<domain>")),
)


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
    return {
        "name": name,
        "repo": repo,
        "owner_name": owner_name,
        "version": version,
        "description": description,
    }


def _validate_client_meta(client_dir: Path) -> dict[str, Any]:
    toml_path = client_dir / "client.toml"
    raw = _load_toml(toml_path)
    ctx = f"clients/{client_dir.name}/client.toml"
    cid = _require_non_empty_str(raw, "id", context=ctx)
    if not ID_RE.fullmatch(cid):
        raise BuildValidationError(f"{ctx}: id must match ^[a-z0-9][a-z0-9-]{{1,63}}$")
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
        if not isinstance(prompts_raw, list) or not (1 <= len(prompts_raw) <= 3) or not all(isinstance(p, str) and p.strip() for p in prompts_raw):
            raise BuildValidationError(f"{ctx}: suggested_prompts must be a list of 1 to 3 non-empty strings")
        suggested_prompts = [p.strip() for p in prompts_raw]
    return {
        "dir": client_dir,
        "id": cid,
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
        fm_match = FRONTMATTER_TOOLS_RE.search(text)
        if fm_match:
            declared = [item.strip() for item in fm_match.group(1).split(",") if item.strip()]
            missing = [name for name in declared if name not in server_tools]
            if missing:
                raise BuildValidationError(f"{rel_path}: frontmatter declares unknown MCP tools: {missing}")
        if Path(rel_path).name in ("claire-extractor.md", "SKILL.md", "AGENTS.md"):
            for match in TOOL_CALL_RE.finditer(text):
                name = match.group(1)
                if name not in server_tools:
                    raise BuildValidationError(f"{rel_path}: calls unknown MCP tool {name!r}")


def _render_top_readme(market: dict[str, str], clients: list[dict[str, Any]]) -> str:
    repo = market["repo"]
    version = market["version"]
    lines = [
        f"# {market['name']}",
        "",
        f"> Automated mirror generated from [fofwisdom/claire-bible](https://github.com/fofwisdom/claire-bible) (`agent_clients/`). Do not edit files in this repository manually; submit changes to `fofwisdom/claire-bible`.",
        "",
        "[한국어 안내](#antigravity) · [English](#antigravity) · [Antigravity GUI 가이드 (KO)](./docs/antigravity.ko.md) · [Antigravity GUI Guide (EN)](./docs/antigravity.en.md) · [Claude 가이드 (KO)](./docs/claude.ko.md) · [Claude Guide (EN)](./docs/claude.en.md)",
        "",
        market["description"],
        "",
        "## Supported Agents",
        "",
    ]
    for client in clients:
        lines.append(f"- **`{client['id']}`** (`{client['target']}`, `v{client['version']}`): {client['description']}")
    lines.extend([
        "",
        '<a id="antigravity"></a>',
        "## Antigravity (GUI 우선 안내 / GUI-First Setup)",
        "",
        "터미널이나 JSON 편집 없이 Antigravity 앱 화면만으로 적용할 수 있습니다.",
        "",
        "- **상세 단계별 가이드 (사진 대신 메뉴명·단축키 기준 친절 안내)**:",
        "  - 한국어: [Antigravity GUI 설치 및 연결 가이드](./docs/antigravity.ko.md)",
        "  - English: [Antigravity GUI Installation & Setup Guide](./docs/antigravity.en.md)",
        f"- **GitHub Releases**: [Download `claire-antigravity-v{version}.zip`](https://github.com/{repo}/releases/latest)",
        "",
        "### 방법 1 (가장 쉬움): Claire 웹에서 복사해 Antigravity 채팅창에 붙여넣기",
        "",
        "1. Claire Bible 웹 **계정 설정**(`/settings/account`)에서 추출 모드를 `client`로 바꿉니다.",
        "2. **MCP 설정**(`/settings/mcp`)에서 **Antigravity 빠른 설치 문구**를 복사해 Antigravity 새 채팅창에 붙여넣습니다.",
        "3. 설치 완료 후 **Customizations -> Installed -> MCP Servers**에서 `claire` 옆 **Authenticate**를 눌러 로그인합니다.",
        "",
        "### 방법 2 (마우스 클릭 직접 설치): `.zip` 내려받아 넣기",
        "",
        f"1. [GitHub Releases](https://github.com/{repo}/releases/latest)에서 `claire-antigravity-v{version}.zip`을 내려받아 압축을 풉니다.",
        "2. OS 파일 탐색기에서 Antigravity 플러그인 폴더(`~/.gemini/config/plugins` 또는 `%USERPROFILE%\\.gemini\\config\\plugins`)에 `claire-antigravity` 폴더를 넣습니다.",
        "3. Antigravity **Customizations -> Installed -> MCP Servers -> Add MCP Server (+)**에서 이름 `claire`와 내 서버의 `/mcp` URL을 입력하고 **Authenticate**를 누릅니다.",
        "",
        '<a id="claude"></a>',
        "## Claude (GUI Marketplace Setup)",
        "",
        "- **상세 가이드**: [한국어 안내](./docs/claude.ko.md) · [English Guide](./docs/claude.en.md)",
        "",
        f"1. Claude **Marketplace management**에 `{repo}` 를 입력해 동기화한 뒤 **`claire-claude`** 를 설치합니다.",
        "2. 커넥터(MCP) 설정에서 내 Claire Bible 서버 `/mcp` URL(`https://<your-claire-server>/mcp`)을 연결하고 OAuth 2.1(`readonly`, `write`)로 로그인합니다.",
        "",
        "<details>",
        "<summary>CLI &amp; Advanced Verification (터미널 / SHA256SUMS 고급 사용자용)</summary>",
        "",
        f"- Claude Code CLI: `/plugin marketplace add {repo}` then install `claire-claude`.",
        f"- Antigravity CLI / checksum check: download `claire-antigravity-v{version}.tar.gz` and `SHA256SUMS` from [GitHub Releases](https://github.com/{repo}/releases/latest), run `sha256sum -c SHA256SUMS --ignore-missing` (macOS: `shasum -a 256 -c SHA256SUMS --ignore-missing`), and extract into `~/.gemini/config/plugins/`.",
        "",
        "</details>",
        "",
        "## Server Authentication Model",
        "",
        "This repository distributes static client instructions, subagents, skills, and templates only. Zero server tokens or secrets are included. Every Claire Bible instance shares these bundles and authenticates dynamically over OAuth 2.1 (`PKCE S256` or `/device`).",
        "",
    ])
    return "\n".join(lines)


def render_release_notes(market: dict[str, str], clients: list[dict[str, Any]]) -> bytes:
    repo = market["repo"]
    version = market["version"]
    ag_version = next((c["version"] for c in clients if c["target"] == "antigravity"), version)
    lines = [
        f"# Claire Bible Agent Plugins v{version}",
        "",
        f"[한국어 상세 가이드 (Antigravity)](https://github.com/{repo}/blob/main/docs/antigravity.ko.md) · [English Setup Guide (Antigravity)](https://github.com/{repo}/blob/main/docs/antigravity.en.md) · [Claude 가이드 (KO)](https://github.com/{repo}/blob/main/docs/claude.ko.md) · [Claude Guide (EN)](https://github.com/{repo}/blob/main/docs/claude.en.md)",
        "",
        "## 어떤 파일을 내려받으면 되나요? / Which file do I need?",
        "",
        "| 사용 중인 앱 / Client | 권장 방법 / Recommended Action | 내려받을 파일 / Asset |",
        "| :--- | :--- | :--- |",
        f"| **Antigravity (GUI)** | Claire `/settings/mcp`에서 빠른 설치 문구를 복사해 채팅창에 붙여넣거나, 아래 `.zip`을 내려받아 플러그인 폴더에 넣으세요. | **`claire-antigravity-v{ag_version}.zip`** |",
        f"| **Claude** | 파일을 내려받을 필요가 없습니다. 마켓플레이스 관리에 `{repo}`를 입력하세요. | 없음 (No download needed) |",
        "",
        "## Antigravity GUI 빠른 적용 3단계",
        "",
        "1. Claire Bible 웹 **계정 설정**(`/settings/account`)에서 추출 모드를 **`client`**로 바꿉니다.",
        f"2. **방법 1 (권장)**: Claire **`/settings/mcp`**에서 **Antigravity 빠른 설치 문구**를 복사해 Antigravity 채팅창에 그대로 붙여넣습니다. (또는 **방법 2**: 위 `claire-antigravity-v{ag_version}.zip`을 압축 해제해 플러그인 폴더에 넣고 **Customizations -> Installed -> MCP Servers -> Add MCP Server (+)**로 `/mcp` URL을 등록합니다.)",
        "3. `claire` 서버 옆 **Authenticate**를 눌러 브라우저에서 OAuth 2.1(`readonly`, `write`) 로그인을 완료합니다.",
        "",
        f"OS별 폴더 열기 방법(`⌘ + ⇧ + G`, `%USERPROFILE%\\.gemini\\config\\plugins`, `Ctrl + H`)과 FAQ는 [Antigravity 한국어 가이드](https://github.com/{repo}/blob/main/docs/antigravity.ko.md) 또는 [English Guide](https://github.com/{repo}/blob/main/docs/antigravity.en.md)에서 확인하세요.",
        "",
        "<details>",
        "<summary>체크섬 검증 및 터미널용 아카이브 (SHA256SUMS &amp; .tar.gz for CLI users)</summary>",
        "",
        "터미널에서 무결성을 검증하려면 `SHA256SUMS`와 `.tar.gz` (또는 `.zip`)를 함께 받은 뒤 실행하세요:",
        "",
        "```bash",
        "sha256sum -c SHA256SUMS --ignore-missing",
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
    extractor_tmpl = (common_dir / "agents" / "claire-extractor.md.tmpl").read_text(encoding="utf-8")
    extract_skill_tmpl = (common_dir / "skills" / "claire-extract" / "SKILL.md.tmpl").read_text(encoding="utf-8")
    query_skill_text = (common_dir / "skills" / "claire-query" / "SKILL.md").read_text(encoding="utf-8")
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

    for client in clients:
        cid = client["id"]
        target = client["target"]
        replacements = {
            "CLIENT_ID": client["client_id"],
            "AGENT_NAME": client["agent_name"],
            "MARKETPLACE_REPO": market["repo"],
            "PLUGIN_ID": cid,
            "VERSION": client["version"],
        }
        plugin_rel_root = f"plugins/{cid}"
        plugin_bundle_files: dict[str, bytes] = {}

        if target == "claude":
            plugin_manifest = {
                "name": cid,
                "version": client["version"],
                "description": client["description"],
                "author": {"name": market["owner_name"]},
                "repository": f"https://github.com/{market['repo']}",
                "license": client["license"],
                "keywords": ["claire-bible", "mcp", "knowledge-graph", "extraction"],
            }
            readme_tmpl_path = client["dir"] / "README.md.tmpl"
            if not readme_tmpl_path.is_file():
                raise BuildValidationError(f"{client['dir']}: missing README.md.tmpl for claude target")
            plugin_bundle_files[".claude-plugin/plugin.json"] = _json_bytes(plugin_manifest)
            plugin_bundle_files["agents/claire-extractor.md"] = _normalize_text(_render_template(extractor_tmpl, replacements))
            plugin_bundle_files["skills/claire-extract/SKILL.md"] = _normalize_text(_render_template(extract_skill_tmpl, replacements))
            plugin_bundle_files["skills/claire-query/SKILL.md"] = _normalize_text(query_skill_text)
            plugin_bundle_files["README.md"] = _normalize_text(_render_template(readme_tmpl_path.read_text(encoding="utf-8"), replacements))
            claude_marketplace_plugins.append({
                "name": cid,
                "source": f"./{plugin_rel_root}",
                "description": client["description"],
                "version": client["version"],
                "license": client["license"],
            })
        elif target == "antigravity":
            mcp_tmpl_path = client["dir"] / "mcp_config.json.tmpl"
            if not mcp_tmpl_path.is_file():
                raise BuildValidationError(f"{client['dir']}: missing mcp_config.json.tmpl for antigravity target")
            readme_tmpl_path = client["dir"] / "README.md.tmpl"
            if not readme_tmpl_path.is_file():
                raise BuildValidationError(f"{client['dir']}: missing README.md.tmpl for antigravity target")
            antigravity_manifest = {
                "name": cid,
                "displayName": client["display_name"],
                "version": client["version"],
                "description": client["description"],
                "author": {"name": market["owner_name"]},
                "homepage": f"https://github.com/{market['repo']}#antigravity",
                "repository": f"https://github.com/{market['repo']}",
                "suggestedPrompts": client["suggested_prompts"],
            }
            plugin_bundle_files["plugin.json"] = _json_bytes(antigravity_manifest)
            plugin_bundle_files["README.md"] = _normalize_text(_render_template(readme_tmpl_path.read_text(encoding="utf-8"), replacements))
            plugin_bundle_files["mcp_config.json.tmpl"] = _normalize_text(mcp_tmpl_path.read_text(encoding="utf-8"))
            plugin_bundle_files["rules/AGENTS.md"] = _normalize_text(_render_template(agents_md_tmpl, replacements))
            plugin_bundle_files["agents/claire-extractor.md"] = _normalize_text(_render_template(extractor_tmpl, replacements))
            plugin_bundle_files["skills/claire-extract/SKILL.md"] = _normalize_text(_render_template(extract_skill_tmpl, replacements))
            plugin_bundle_files["skills/claire-query/SKILL.md"] = _normalize_text(query_skill_text)
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
            "client_id": client["client_id"],
            "target": target,
            "version": client["version"],
            "required_scopes": client["required_scopes"],
            "path": plugin_rel_root,
            "files": file_entries,
        })

        tar_name = f"{cid}-v{client['version']}.tar.gz"
        zip_name = f"{cid}-v{client['version']}.zip"
        release_files[tar_name] = _build_deterministic_tar_gz(cid, plugin_bundle_files)
        release_files[zip_name] = _build_deterministic_zip(cid, plugin_bundle_files)

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
