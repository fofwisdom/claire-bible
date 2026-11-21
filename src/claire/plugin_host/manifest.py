"""``plugin.toml``: the declarative manifest of a plugin (docs/implementation/INGESTION.md §2.2).

The server reads it without an import of plugin code (PL-D4). An unknown key, a missing required key, or a value of the wrong type is a ``ManifestError`` (the state ``invalid``).
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..ingest.hosts import is_valid_pattern
from . import MANIFEST_VERSIONS, PLUGIN_TYPES, TYPE_INGEST_ADAPTER

MANIFEST_FILE = "plugin.toml"

ID_RE = re.compile(r"^[a-z][a-z0-9]*\.[a-z][a-z0-9_]*$")
ENTRY_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
SOURCE_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
ENTRY_RE = re.compile(r"^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)*:[a-z_][a-z0-9_]*$")
DISTRIBUTION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
EXECUTABLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")

MATURITIES = ("experimental", "stable", "deprecated")
ENTRY_KINDS = ("fetcher", "web_adapter")
PROVIDER_KINDS = ("stt", "vlm")

_TOP_KEYS = {
    "manifest", "id", "type", "revision", "core_commit", "api", "entry", "license",
    "maturity", "maintainers", "text", "provides", "permissions", "requires",
}
_REQUIRED_KEYS = {"manifest", "id", "type", "revision", "core_commit", "api", "entry", "license", "maturity", "text"}
_PROVIDE_KEYS = {"kind", "name", "priority", "hosts", "source_types"}
_PERMISSION_KEYS = {"network", "private_network", "network_modules", "executables", "providers"}
_REQUIRE_KEYS = {"distributions", "executables"}
_TEXT_KEYS = {"name", "description"}


class ManifestError(ValueError):
    """The manifest cannot be used. The message tells the cause."""


def python_name(plugin_id: str) -> str:
    """The Python name of a plugin: the id with ``.`` changed to ``_`` (§6.1 rule 1).

    The publisher has no ``_`` and an id has exactly two parts, so two ids never give the same name.
    """
    return plugin_id.replace(".", "_")


def publisher_of(plugin_id: str) -> str:
    return plugin_id.split(".", 1)[0]


@dataclass(frozen=True)
class Provide:
    kind: str
    name: str
    priority: int
    hosts: tuple[str, ...]
    source_types: tuple[str, ...] = ()

    def key(self) -> tuple[str, str, int, tuple[str, ...]]:
        return (self.kind, self.name, self.priority, tuple(sorted(self.hosts)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "priority": self.priority,
            "hosts": list(self.hosts),
            "source_types": list(self.source_types),
        }


@dataclass(frozen=True)
class Permissions:
    network: tuple[str, ...] = ()
    private_network: bool = False
    network_modules: tuple[str, ...] = ()
    executables: tuple[str, ...] = ()
    providers: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "network": list(self.network),
            "private_network": self.private_network,
            "network_modules": list(self.network_modules),
            "executables": list(self.executables),
            "providers": list(self.providers),
        }


@dataclass(frozen=True)
class Requires:
    distributions: tuple[str, ...] = ()
    executables: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"distributions": list(self.distributions), "executables": list(self.executables)}


@dataclass(frozen=True)
class Manifest:
    manifest: int
    id: str
    type: str
    revision: int
    core_commit: str
    api: int
    entry: str
    license: str
    maturity: str
    maintainers: tuple[str, ...]
    text: dict[str, dict[str, str]]
    provides: tuple[Provide, ...]
    permissions: Permissions = field(default_factory=Permissions)
    requires: Requires = field(default_factory=Requires)

    @property
    def entry_module(self) -> str:
        return self.entry.split(":", 1)[0]

    @property
    def entry_function(self) -> str:
        return self.entry.split(":", 1)[1]

    def text_for(self, locale: str) -> dict[str, str]:
        """``[text.<locale>]``, else ``[text.en]``: the same lookup as the catalogs (§6.1 rule 9)."""
        return self.text.get(locale) or self.text["en"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest": self.manifest,
            "id": self.id,
            "type": self.type,
            "revision": self.revision,
            "core_commit": self.core_commit,
            "api": self.api,
            "entry": self.entry,
            "license": self.license,
            "maturity": self.maturity,
            "maintainers": list(self.maintainers),
            "text": {lang: dict(values) for lang, values in self.text.items()},
            "provides": [p.to_dict() for p in self.provides],
            "permissions": self.permissions.to_dict(),
            "requires": self.requires.to_dict(),
        }


def _check_keys(where: str, data: dict[str, Any], allowed: set[str], required: set[str] = frozenset()) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ManifestError(f"{where}: unknown key {unknown[0]!r}")
    missing = sorted(required - set(data))
    if missing:
        raise ManifestError(f"{where}: missing key {missing[0]!r}")


def _int(where: str, value: Any, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ManifestError(f"{where}: must be an integer")
    if minimum is not None and value < minimum:
        raise ManifestError(f"{where}: must be {minimum} or more")
    return value


def _str(where: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{where}: must be a text that is not empty")
    return value


def _strings(where: str, value: Any, pattern: re.Pattern[str] | None = None) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ManifestError(f"{where}: must be a list of texts")
    if len(set(value)) != len(value):
        raise ManifestError(f"{where}: has a duplicate value")
    if pattern is not None:
        for v in value:
            if not pattern.fullmatch(v):
                raise ManifestError(f"{where}: value {v!r} is not valid")
    return tuple(value)


def _hosts(where: str, value: Any) -> tuple[str, ...]:
    hosts = _strings(where, value)
    for host in hosts:
        if not is_valid_pattern(host):
            raise ManifestError(f"{where}: host pattern {host!r} is not valid (exact host, '*.' and host, or '*')")
    return hosts


def parse_manifest(data: dict[str, Any]) -> Manifest:
    """Make a ``Manifest`` from the TOML data. Raise ``ManifestError``."""
    _check_keys("manifest", data, _TOP_KEYS, _REQUIRED_KEYS)

    version = _int("manifest", data["manifest"])
    if version not in MANIFEST_VERSIONS:
        raise ManifestError(f"manifest: version {version} is not supported (supported: {list(MANIFEST_VERSIONS)})")
    plugin_id = _str("id", data["id"])
    if not ID_RE.fullmatch(plugin_id):
        raise ManifestError("id: must be <publisher>.<name> (publisher [a-z][a-z0-9]*, name snake_case)")
    plugin_type = _str("type", data["type"])
    if plugin_type not in PLUGIN_TYPES:
        raise ManifestError(f"type: {plugin_type!r} is not a known plugin type")
    if plugin_type != TYPE_INGEST_ADAPTER:
        raise ManifestError(f"type: {plugin_type!r} is not supported")
    revision = _int("revision", data["revision"], minimum=1)
    core_commit = _str("core_commit", data["core_commit"])
    if not COMMIT_RE.fullmatch(core_commit):
        raise ManifestError("core_commit: must be the full 40-character commit hash in lowercase")
    api = _int("api", data["api"], minimum=1)
    entry = _str("entry", data["entry"])
    if not ENTRY_RE.fullmatch(entry):
        raise ManifestError("entry: must be <module>:<function>")

    license_expr = _str("license", data["license"])
    maturity = _str("maturity", data["maturity"])
    if maturity not in MATURITIES:
        raise ManifestError(f"maturity: must be one of {list(MATURITIES)}")
    maintainers = _strings("maintainers", data.get("maintainers", []))

    text_raw = data["text"]
    if not isinstance(text_raw, dict) or "en" not in text_raw:
        raise ManifestError("text: [text.en] is required")
    text: dict[str, dict[str, str]] = {}
    for lang, values in text_raw.items():
        where = f"text.{lang}"
        if not isinstance(values, dict):
            raise ManifestError(f"{where}: must be a table")
        _check_keys(where, values, _TEXT_KEYS, _TEXT_KEYS)
        text[str(lang)] = {"name": _str(f"{where}.name", values["name"]), "description": _str(f"{where}.description", values["description"])}

    provides_raw = data.get("provides", [])
    if not isinstance(provides_raw, list):
        raise ManifestError("provides: must be an array of tables")
    provides: list[Provide] = []
    for index, item in enumerate(provides_raw):
        where = f"provides[{index}]"
        if not isinstance(item, dict):
            raise ManifestError(f"{where}: must be a table")
        _check_keys(where, item, _PROVIDE_KEYS, {"kind", "name", "priority", "hosts"})
        kind = _str(f"{where}.kind", item["kind"])
        if kind not in ENTRY_KINDS:
            raise ManifestError(f"{where}.kind: must be one of {list(ENTRY_KINDS)}")
        name = _str(f"{where}.name", item["name"])
        if not ENTRY_NAME_RE.fullmatch(name):
            raise ManifestError(f"{where}.name: must be snake_case")
        hosts = _hosts(f"{where}.hosts", item["hosts"])
        if not hosts:
            raise ManifestError(f"{where}.hosts: must not be empty")
        provides.append(
            Provide(
                kind=kind,
                name=name,
                priority=_int(f"{where}.priority", item["priority"]),
                hosts=hosts,
                source_types=_strings(f"{where}.source_types", item.get("source_types", []), SOURCE_TYPE_RE),
            )
        )
    names = [(p.kind, p.name) for p in provides]
    if len(set(names)) != len(names):
        raise ManifestError("provides: two entries of one kind have the same name")

    perm_raw = data.get("permissions", {})
    if not isinstance(perm_raw, dict):
        raise ManifestError("permissions: must be a table")
    _check_keys("permissions", perm_raw, _PERMISSION_KEYS)
    private_network = perm_raw.get("private_network", False)
    if not isinstance(private_network, bool):
        raise ManifestError("permissions.private_network: must be true or false")
    providers = _strings("permissions.providers", perm_raw.get("providers", []))
    for provider in providers:
        if provider not in PROVIDER_KINDS:
            raise ManifestError(f"permissions.providers: {provider!r} is not one of {list(PROVIDER_KINDS)}")
    permissions = Permissions(
        network=_hosts("permissions.network", perm_raw.get("network", [])),
        private_network=private_network,
        network_modules=_strings("permissions.network_modules", perm_raw.get("network_modules", []), MODULE_RE),
        executables=_strings("permissions.executables", perm_raw.get("executables", []), EXECUTABLE_RE),
        providers=providers,
    )

    req_raw = data.get("requires", {})
    if not isinstance(req_raw, dict):
        raise ManifestError("requires: must be a table")
    _check_keys("requires", req_raw, _REQUIRE_KEYS)
    requires = Requires(
        distributions=_strings("requires.distributions", req_raw.get("distributions", []), DISTRIBUTION_RE),
        executables=_strings("requires.executables", req_raw.get("executables", []), EXECUTABLE_RE),
    )

    return Manifest(
        manifest=version,
        id=plugin_id,
        type=plugin_type,
        revision=revision,
        core_commit=core_commit,
        api=api,
        entry=entry,
        license=license_expr,
        maturity=maturity,
        maintainers=maintainers,
        text=text,
        provides=tuple(provides),
        permissions=permissions,
        requires=requires,
    )


def read_manifest(folder: Path) -> Manifest:
    """Read and parse ``<folder>/plugin.toml``. Raise ``ManifestError``."""
    path = folder / MANIFEST_FILE
    if not path.is_file():
        raise ManifestError(f"{MANIFEST_FILE} is missing")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ManifestError(f"{MANIFEST_FILE} cannot be read: {exc}") from exc
    return parse_manifest(data)
