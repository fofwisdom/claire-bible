"""Principle G1 (docs/PRINCIPLES.md): the web UI shows line icons of static/js/icons.js, not emoji.

The test reads the source files of the web UI (templates, static scripts and styles, locale catalogs, and the console renderers) and finds emoji characters. Plain text symbols (arrows, ✓, ✕, ★, ☆, ▾ and similar) are not emoji and stay permitted.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "src" / "claire"

# Source files that make the web UI.
UI_GLOBS = (
    "templates/**/*.html",
    "static/js/**/*.js",
    "static/css/*.css",
    "static/locales/*.json",
    "api/accounts_routes/*.py",
)

# Visual prototypes of design documents are records, not product pages.
EXCLUDED_DIRS = (PKG / "templates" / "prototype",)

# Emoji: the pictograph planes, the emoji variation selector, and the BMP characters whose default presentation is emoji (Unicode Emoji_Presentation=Yes).
EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF️"
    "⌚⌛⏩-⏬⏰⏳◽◾☔☕♈-♓♿⚓⚡⚪⚫⚽⚾⛄⛅⛎⛔⛪⛲⛳⛵⛺⛽"
    "✅✊✋✨❌❎❓-❕❗➕-➗➰➿⬛⬜⭐⭕]"
)

# A theme icon is user data (an emoji that the owner picks). Its default value may be an emoji on a line that names the icon.
THEME_ICON_DEFAULTS = ("📚", "📁")


def _without_theme_icon_defaults(line: str) -> str:
    if "icon" not in line.lower():
        return line
    for mark in THEME_ICON_DEFAULTS:
        line = line.replace(mark, "")
    return line
# Catalog keys that only the server renders outside a browser (the Telegram bot and the push notifications) are not web UI: a Telegram message has no line icon. The prefixes are the bot and notification prefixes of ``i18n.catalog.SERVER_ONLY_KEY_PREFIXES``.
NOT_WEB_KEY = re.compile(r'^\s*"(bot|notify)\.')
# The default quote template of the outbound post is text of a post, not UI.
POST_TEMPLATE = re.compile(r"quoteTemplate")


def _ui_files() -> list[Path]:
    files: set[Path] = set()
    for pattern in UI_GLOBS:
        files.update(PKG.glob(pattern))
    return sorted(f for f in files if f.is_file() and not any(d in f.parents for d in EXCLUDED_DIRS))


def _violations() -> list[str]:
    found: list[str] = []
    for path in _ui_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not EMOJI.search(line):
                continue
            rest = _without_theme_icon_defaults(line)
            if POST_TEMPLATE.search(rest) or (path.suffix == ".json" and NOT_WEB_KEY.search(rest)):
                continue
            hits = sorted(set(EMOJI.findall(rest)))
            if hits:
                found.append(f"{path.relative_to(ROOT)}:{number}: {' '.join(hits)}")
    return found


def test_ui_files_are_found() -> None:
    files = _ui_files()
    names = {f.name for f in files}
    assert {"icons.js", "app.js", "index.html", "syndication.html", "console_shell.html", "console.py", "ko.json"} <= names
    assert not any("prototype" in f.parts for f in files)


def test_web_ui_has_no_emoji() -> None:
    violations = _violations()
    assert not violations, (
        "Emoji in the web UI (principle G1 in docs/PRINCIPLES.md). Use a line icon of src/claire/static/js/icons.js: "
        "an empty element <span data-icon=\"name\"></span> in markup, ClaireIcons.svg(\"name\") in a script, "
        "or icon_html(\"name\") in api/accounts_routes. Add a new icon to icons.js if no icon fits. "
        "In plain text (textContent, <option>, alert), write the text without the emoji.\n" + "\n".join(violations)
    )


def test_checker_finds_emoji_and_permits_text_symbols() -> None:
    assert EMOJI.search("<h2>🔐 단계 인증</h2>")
    assert EMOJI.search("'⚠️ 경고'")
    assert EMOJI.search("<b>⭐</b>") and EMOJI.search("✅ done") and EMOJI.search("➕ add")
    for symbol in "✓✕★☆↗→↔▾▸●○◐▲▼☰⇄↻♥⚠✎":
        assert not EMOJI.search(symbol), symbol
    assert not EMOJI.search(_without_theme_icon_defaults("const icon = esc(t.icon || '📁');"))
    assert EMOJI.search(_without_theme_icon_defaults("<h2>📁 테마 관리</h2>"))
    assert EMOJI.search(_without_theme_icon_defaults("icon: '📚', title: '🔐'"))
