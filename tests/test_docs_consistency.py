"""Tests that the current-state documents still match the repository.

Scope is the documents that describe the present: ``docs/*.md`` and
``docs/implementation/*.md``. ``docs/design/`` is a historical record and is
not checked here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from claire.config import Settings

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
CURRENT_DOCS = sorted([*DOCS.glob("*.md"), *(DOCS / "implementation").glob("*.md")])
ENV_DOC = DOCS / "implementation" / "ENVIRONMENT_VARIABLES.md"

_LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")
_CODE_PATH = re.compile(r"`((?:src|tests|ops|scripts|e2e)/[\w./-]+)`")
_ENV_ROW = re.compile(r"^\|\s*`([A-Z][A-Z0-9_]+)`\s*\|\s*([^|]*)\|", re.M)

# Variables read outside ``Settings`` (compose, cb-manuscript, deploy scripts).
_INFRA_SOURCES = [
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.dev.yml",
    "cb-manuscript",
    "deploy.sh",
    ".env.example",
    ".env.dev.example",
    ".env.deploy.example",
]

# Rows whose documented default is not the ``Settings`` field default, with the reason.
_DEFAULT_EXEMPT = {
    "CLAIRE_ENVIRONMENT": "required in every env file; the field default is empty",
    "CLAIRE_SUPPORT_BUNDLE_API_URL": "injected by compose",
}


def _rel(p: Path) -> str:
    return str(p.relative_to(ROOT))


@pytest.mark.parametrize("doc", CURRENT_DOCS, ids=_rel)
def test_relative_links_resolve(doc: Path) -> None:
    text = doc.read_text(encoding="utf-8")
    broken = [
        target
        for target in _LINK.findall(text)
        if not target.startswith(("http://", "https://", "mailto:"))
        and not (doc.parent / target).resolve().exists()
    ]
    assert not broken, f"{_rel(doc)}: broken links {broken}"


@pytest.mark.parametrize("doc", CURRENT_DOCS, ids=_rel)
def test_code_paths_exist(doc: Path) -> None:
    text = doc.read_text(encoding="utf-8")
    missing = sorted({p for p in _CODE_PATH.findall(text) if not (ROOT / p.rstrip("/")).exists()})
    assert not missing, f"{_rel(doc)}: paths not in repository {missing}"


def _env_rows() -> dict[str, str]:
    rows: dict[str, str] = {}
    for name, default_cell in _ENV_ROW.findall(ENV_DOC.read_text(encoding="utf-8")):
        rows.setdefault(name, default_cell.strip())
    return rows


def _settings_defaults() -> dict[str, object]:
    return {f.alias: f.default for f in Settings.model_fields.values() if f.alias}


def test_every_setting_is_documented() -> None:
    missing = sorted(set(_settings_defaults()) - set(_env_rows()))
    assert not missing, f"Settings aliases missing from {_rel(ENV_DOC)}: {missing}"


def test_every_documented_variable_exists() -> None:
    sources = [*ROOT.glob("src/**/*.py"), *ROOT.glob("ops/**/*.py")]
    sources += [ROOT / p for p in _INFRA_SOURCES if (ROOT / p).is_file()]
    corpus = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in sources)
    aliases = set(_settings_defaults())
    unknown = sorted(
        name
        for name in _env_rows()
        if name not in aliases and not re.search(rf"\b{name}\b", corpus)
    )
    assert not unknown, f"{_rel(ENV_DOC)} documents variables no code reads: {unknown}"


def _matches(default: object, cell: str) -> bool:
    values = re.findall(r"`([^`]*)`", cell)
    if isinstance(default, bool):
        accepted = {"1", "true", "True"} if default else {"0", "false", "False"}
        return any(v in accepted for v in values)
    if isinstance(default, (int, float)):
        for v in values:
            try:
                if float(v) == float(default):
                    return True
            except ValueError:
                continue
        return False
    if default == "":
        return '""' in values or cell.startswith('`""`')
    return str(default) in values


def test_documented_defaults_match_settings() -> None:
    rows = _env_rows()
    mismatched = []
    for alias, default in sorted(_settings_defaults().items()):
        if alias not in rows or alias in _DEFAULT_EXEMPT:
            continue
        if default is None or not isinstance(default, (bool, int, float, str)):
            continue
        if not _matches(default, rows[alias]):
            mismatched.append(f"{alias}: code={default!r} doc={rows[alias]!r}")
    assert not mismatched, "defaults differ from Settings:\n" + "\n".join(mismatched)
