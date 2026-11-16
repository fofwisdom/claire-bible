"""Entity language of an account: selection of the label that a viewer sees.

The stored main label (``entities.name``) does not change. When the viewer has an entity language, the responses show the first name of the entity (main label, then aliases in the stored order) that is in that language. When no name is in that language, the responses show the stored main label. A viewer without an entity language always sees the stored main label.

The language of a name is decided from its script only:

- ``ko``: the name has a Hangul letter.
- ``ja``: the name has a kana letter, or it has a Han letter and no Hangul letter.
- ``zh``: the name has a Han letter and no kana or Hangul letter.
- ``en``: the name has a Latin letter and no letter of a different script.
"""

from __future__ import annotations

from typing import Any

ENTITY_LANGUAGES: tuple[str, ...] = ("en", "ko", "ja", "zh")
ENTITY_LANGUAGE_SET: frozenset[str] = frozenset(ENTITY_LANGUAGES)


def parse_entity_language(value: Any) -> str | None:
    """``None`` and ``""`` give ``None`` (no entity language). A value that is not an exact supported code raises ``ValueError("invalid_entity_language")``."""
    if value is None or value == "":
        return None
    if isinstance(value, str) and value in ENTITY_LANGUAGE_SET:
        return value
    raise ValueError("invalid_entity_language")


def _scripts(text: str) -> set[str]:
    found: set[str] = set()
    for ch in text:
        if not ch.isalpha():
            continue
        cp = ord(ch)
        if 0xAC00 <= cp <= 0xD7A3 or 0x1100 <= cp <= 0x11FF or 0x3130 <= cp <= 0x318F:
            found.add("hangul")
        elif 0x3040 <= cp <= 0x30FF or 0x31F0 <= cp <= 0x31FF or 0xFF66 <= cp <= 0xFF9F:
            found.add("kana")
        elif 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF or 0xF900 <= cp <= 0xFAFF or 0x20000 <= cp <= 0x2FA1F or cp in (0x3005, 0x3007):
            found.add("han")
        elif cp < 0x0250 or 0x1E00 <= cp <= 0x1EFF or 0xFF21 <= cp <= 0xFF5A:
            found.add("latin")
        else:
            found.add("other")
    return found


def matches_language(text: str, language: str) -> bool:
    scripts = _scripts(text)
    if language == "ko":
        return "hangul" in scripts
    if language == "ja":
        return "kana" in scripts or ("han" in scripts and "hangul" not in scripts)
    if language == "zh":
        return "han" in scripts and "kana" not in scripts and "hangul" not in scripts
    if language == "en":
        return scripts == {"latin"}
    raise ValueError("invalid_entity_language")


def display_label(name: str, aliases: list[str], language: str | None) -> str:
    """The label that a viewer with ``language`` sees."""
    if language is None or matches_language(name, language):
        return name
    for alias in aliases:
        if alias and matches_language(alias, language):
            return alias
    return name


def display_names(name: str, aliases: list[str], language: str | None) -> tuple[str, list[str]]:
    """``(label, other names)``. When the label is an alias, the stored main label is the first of the other names."""
    label = display_label(name, aliases, language)
    if label == name:
        return name, list(aliases)
    return label, [name] + [a for a in aliases if a != label]
