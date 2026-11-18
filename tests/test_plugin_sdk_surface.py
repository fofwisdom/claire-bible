"""Snapshot of the plugin SDK surface (INGEST_ADAPTER_PLUGIN_DESIGN.md §7.1).

A change of the snapshot needs a decision: a compatible addition keeps PLUGIN_API_LEVEL; a break needs a new level (and PLUGIN_API_LEVELS of the plugin host changes in the same commit). Write the new snapshot with:

    PYTHONPATH=. python tests/test_plugin_sdk_surface.py --write
"""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

SNAPSHOT = Path(__file__).parent / "fixtures" / "plugin_sdk_surface.json"


def surface() -> dict[str, object]:
    import claire.plugin_sdk as sdk

    result: dict[str, object] = {"PLUGIN_API_LEVEL": sdk.PLUGIN_API_LEVEL}
    for name in sorted(sdk.__all__):
        value = getattr(sdk, name)
        if inspect.isclass(value) and not value.__module__.startswith("claire"):
            # A class of an external library (httpx): only its name, so that a library upgrade does not change the snapshot.
            result[name] = {"class": f"{value.__module__}.{value.__qualname__}"}
        elif inspect.isclass(value):
            methods = {}
            for attr, member in sorted(vars(value).items()):
                if attr.startswith("_") and attr != "__init__":
                    continue
                func = member.__func__ if isinstance(member, (classmethod, staticmethod)) else member
                if callable(func):
                    try:
                        methods[attr] = str(inspect.signature(func))
                    except (TypeError, ValueError):
                        methods[attr] = "?"
            result[name] = {"class": value.__qualname__, "methods": methods}
        elif callable(value):
            try:
                result[name] = {"function": str(inspect.signature(value))}
            except (TypeError, ValueError):
                result[name] = {"function": "?"}
        else:
            result[name] = {"value": repr(value)}
    return result


def test_sdk_surface_matches_snapshot():
    assert SNAPSHOT.is_file(), "write the snapshot (see the module text)"
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert surface() == expected, "the plugin SDK surface changed: decide compatible addition or new level, then write the snapshot"


def test_sdk_imports_only_core_modules_that_exist():
    import claire.plugin_sdk as sdk

    for name in sdk.__all__:
        assert hasattr(sdk, name), name


if __name__ == "__main__" and "--write" in sys.argv:
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(json.dumps(surface(), indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"written: {SNAPSHOT}")
