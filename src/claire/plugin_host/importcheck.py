"""Static import check of a plugin (INGEST_ADAPTER_PLUGIN_DESIGN.md PL-D10).

It reads the Python files of a plugin with ``ast`` and does not execute them. Files under ``tests/`` are not checked: the server never imports them. The check is static: it does not stop code that hides its imports.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

# Modules that open network connections. A plugin imports one only when it names it in ``permissions.network_modules``.
NETWORK_MODULES: frozenset[str] = frozenset(
    {
        "aiohttp", "curl_cffi", "ftplib", "http.client", "httpx", "playwright", "requests", "scrapling",
        "smtplib", "socket", "ssl", "urllib.request", "urllib3", "websocket", "websockets", "yt_dlp",
    }
)

# Modules and functions that start processes. A plugin uses them only when ``permissions.executables`` is not empty.
PROCESS_MODULES: frozenset[str] = frozenset({"subprocess", "pty", "multiprocessing", "asyncio.subprocess"})
PROCESS_OS_FUNCTIONS: frozenset[str] = frozenset(
    {"system", "popen", "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe",
     "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe", "posix_spawn", "posix_spawnp", "fork", "forkpty"}
)

SDK_MODULE = "claire.plugin_sdk"


@dataclass(frozen=True)
class Finding:
    file: str
    line: int
    kind: str  # "core_import" | "network_module" | "process" | "syntax"
    name: str

    def text(self) -> str:
        what = {
            "core_import": "imports a restricted package or core module other than claire.plugin_sdk",
            "network_module": "imports a network module that permissions.network_modules does not name",
            "process": "starts processes, but permissions.executables is empty",
            "syntax": "cannot be parsed",
        }.get(self.kind, self.kind)
        return f"{self.file}:{self.line}: {what}: {self.name}"


def _matches(name: str, modules: frozenset[str]) -> str | None:
    for module in modules:
        if name == module or name.startswith(module + "."):
            return module
    return None


def _declared(name: str, declared: tuple[str, ...]) -> bool:
    return any(name == d or name.startswith(d + ".") for d in declared)


def check_source(source: str, filename: str, *, network_modules: tuple[str, ...] = (), executables: tuple[str, ...] = ()) -> list[Finding]:
    tree = ast.parse(source, filename=filename)
    findings: list[Finding] = []

    def check_module(name: str, line: int) -> None:
        if name == "claire" or name.startswith("claire."):
            if not (name == SDK_MODULE or name.startswith(SDK_MODULE + ".")):
                findings.append(Finding(filename, line, "core_import", name))
            return
        if name in ("bible", "claire_bible") or name.startswith(("bible.", "claire_bible.")):
            findings.append(Finding(filename, line, "core_import", name))
            return
        network = _matches(name, NETWORK_MODULES)
        if network is not None and not _declared(name, network_modules) and not _declared(network, network_modules):
            findings.append(Finding(filename, line, "network_module", name))
        if _matches(name, PROCESS_MODULES) is not None and not executables:
            findings.append(Finding(filename, line, "process", name))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                check_module(alias.name, node.lineno)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue  # a module of the same plugin
            module = node.module or ""
            check_module(module, node.lineno)
            for alias in node.names:
                full = f"{module}.{alias.name}" if module else alias.name
                if module == "os" and alias.name in PROCESS_OS_FUNCTIONS and not executables:
                    findings.append(Finding(filename, node.lineno, "process", full))
                elif module == "asyncio" and alias.name == "subprocess" and not executables:
                    findings.append(Finding(filename, node.lineno, "process", full))
                elif module == "urllib" and alias.name == "request":
                    check_module("urllib.request", node.lineno)
                elif module == "http" and alias.name == "client":
                    check_module("http.client", node.lineno)
        elif isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == "os" and node.attr in PROCESS_OS_FUNCTIONS and not executables:
                findings.append(Finding(filename, node.lineno, "process", f"os.{node.attr}"))
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "__import__":
                findings.append(Finding(filename, node.lineno, "core_import", "__import__()"))
            if isinstance(func, ast.Attribute) and func.attr == "__import__":
                owner = func.value.id if isinstance(func.value, ast.Name) else "*"
                findings.append(Finding(filename, node.lineno, "core_import", f"{owner}.__import__()"))
            if isinstance(func, ast.Attribute) and func.attr == "import_module":
                findings.append(Finding(filename, node.lineno, "core_import", "importlib.import_module()"))
    return findings


def check_folder(folder: Path, *, network_modules: tuple[str, ...] = (), executables: tuple[str, ...] = ()) -> list[Finding]:
    """Check each ``*.py`` file of the folder of a plugin, without ``tests/``."""
    findings: list[Finding] = []
    for path in sorted(folder.rglob("*.py")):
        rel = path.relative_to(folder)
        if rel.parts and rel.parts[0] in ("tests", "__pycache__"):
            continue
        try:
            source = path.read_text(encoding="utf-8")
            findings.extend(check_source(source, rel.as_posix(), network_modules=network_modules, executables=executables))
        except (SyntaxError, UnicodeDecodeError) as exc:
            findings.append(Finding(rel.as_posix(), getattr(exc, "lineno", 0) or 0, "syntax", str(exc)))
    return findings
