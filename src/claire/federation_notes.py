"""Text of an own note (FEDERATION_REMOTE_READING_DESIGN.md Section 5.4 rules 3 and 12, RS10, unit FP4-9).

The owner enters plain text. This module makes the HTML of the note: paragraphs, line breaks, links, mentions (``@name@host``), and hashtags (``#tag``). Each character of the text is escaped, and ``nh3`` cleans the result with a small allowlist. The HTML has no remote resource and no script.

The module has no I/O and knows no table.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from urllib.parse import quote

import nh3

__all__ = [
    "NOTE_MAX_MENTIONS",
    "NOTE_MAX_TAGS",
    "NOTE_SUMMARY_MAX_CHARS",
    "NoteTextError",
    "RenderedNote",
    "hashtag_href",
    "normalize_handle",
    "normalize_language",
    "normalize_text",
    "render_note",
]

# The largest number of different mentions and hashtags of one note. A mention is a delivery to a remote server (a known spam pattern).
NOTE_MAX_MENTIONS = 16
NOTE_MAX_TAGS = 32
NOTE_SUMMARY_MAX_CHARS = 500

_URL = r"https?://[^\s<>\"']+"
_MENTION = r"(?<![\w/@.])@[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?"
_HASHTAG = r"(?<![\w/&#])#\w{1,100}"
_TOKEN = re.compile(f"(?P<url>{_URL})|(?P<mention>{_MENTION})|(?P<tag>{_HASHTAG})")
_LANGUAGE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8}){0,3}$")
_HANDLE = re.compile(r"^@?([A-Za-z0-9_][A-Za-z0-9_.-]{0,63})@([A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?)$")
# Characters that end a sentence after an address are not a part of the address.
_URL_TRAILING = ".,;:!?)]}'\""


class NoteTextError(ValueError):
    """The text of a note is not valid. ``code`` is the error code of the route."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class RenderedNote:
    html: str
    # The handles in the text, ``@name@host`` with a lowercase host, in the sequence of the text, each one time.
    mentions: tuple[str, ...]
    # The hashtags in the text without ``#``, in the sequence of the text, each one time (the comparison ignores case).
    tags: tuple[str, ...]


def normalize_text(text: str) -> str:
    """Line ends as ``\\n``, no control character except a line end and a tab, no space at the ends."""
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = "".join(ch for ch in cleaned if ch in "\n\t" or ord(ch) >= 32 and ord(ch) != 127)
    return cleaned.strip()


def normalize_handle(value: str) -> str | None:
    """``@name@host`` with a lowercase host, or ``None`` when the value is not a handle."""
    match = _HANDLE.match(value.strip())
    return None if match is None else f"@{match.group(1)}@{match.group(2).lower()}"


def normalize_language(value: str) -> str | None:
    """A language tag (BCP 47, short form), or ``None`` when the value is not one."""
    text = value.strip()
    return text if _LANGUAGE.match(text) else None


def hashtag_href(origin: str, tag: str) -> str:
    """The address of a hashtag in the HTML and in the ``Hashtag`` tag. Claire has no page for a hashtag yet: the address identifies the tag only."""
    return f"{origin}/tags/{quote(tag.lower(), safe='')}"


def _line_html(line: str, origin: str, mention_iris: dict[str, str | None], mentions: dict[str, None], tags: dict[str, str]) -> str:
    parts: list[str] = []
    position = 0
    for match in _TOKEN.finditer(line):
        token = match.group(0)
        end = match.end()
        if match.lastgroup == "url":
            while token and token[-1] in _URL_TRAILING:
                token = token[:-1]
                end -= 1
            if len(token) <= len("https://"):
                continue
            parts.append(html.escape(line[position : match.start()], quote=False))
            address = html.escape(token, quote=True)
            parts.append(f'<a href="{address}">{address}</a>')
        elif match.lastgroup == "mention":
            handle = normalize_handle(token)
            if handle is None:
                continue
            parts.append(html.escape(line[position : match.start()], quote=False))
            mentions.setdefault(handle, None)
            label = html.escape(handle, quote=False)
            iri = mention_iris.get(handle)
            parts.append(f'<span class="h-card"><a href="{html.escape(iri, quote=True)}" class="u-url mention">{label}</a></span>' if iri else f'<span class="h-card">{label}</span>')
        else:
            name = token[1:]
            # A number is not a hashtag (for example "#1").
            if name.isdigit():
                continue
            parts.append(html.escape(line[position : match.start()], quote=False))
            tags.setdefault(name.lower(), name)
            parts.append(f'<a href="{html.escape(hashtag_href(origin, name), quote=True)}" class="mention hashtag">#{html.escape(name, quote=False)}</a>')
        position = end
    parts.append(html.escape(line[position:], quote=False))
    return "".join(parts)


def render_note(text: str, *, origin: str, mention_iris: dict[str, str | None] | None = None, quote_url: str | None = None) -> RenderedNote:
    """Make the HTML of a note from its plain text (Section 5.4 rule 3).

    ``mention_iris`` gives the actor IRI of a handle that Claire knows. A mention with an IRI is a link; a mention without one is text, and the sidecar resolves it for the ``Mention`` tag. ``origin`` is the canonical origin of this instance (hashtag addresses).

    ``quote_url`` is the address of the object that the note quotes (Section 5.4 rule 5, unit FP4-9Q). The HTML gets a last paragraph with a link to it (class ``quote-inline``), for a reader that does not show quotes; the paragraph stays in all quote states.

    Raises ``NoteTextError``: ``empty_content``, ``too_many_mentions``, ``too_many_tags``.
    """
    body = normalize_text(text)
    if not body:
        raise NoteTextError("empty_content")
    known = mention_iris or {}
    mentions: dict[str, None] = {}
    tags: dict[str, str] = {}
    paragraphs = []
    for block in re.split(r"\n{2,}", body):
        lines = [_line_html(line, origin, known, mentions, tags) for line in block.split("\n")]
        paragraphs.append(f"<p>{'<br>'.join(lines)}</p>")
    if len(mentions) > NOTE_MAX_MENTIONS:
        raise NoteTextError("too_many_mentions")
    if len(tags) > NOTE_MAX_TAGS:
        raise NoteTextError("too_many_tags")
    if quote_url:
        address = html.escape(quote_url, quote=True)
        paragraphs.append(f'<p class="quote-inline">RE: <a href="{address}">{address}</a></p>')
    # RS10: the allowlist has no element that loads a resource. nh3 adds the rel value to each link.
    cleaned = nh3.clean("".join(paragraphs), tags={"p", "br", "a", "span"}, attributes={"a": {"href", "class"}, "span": {"class"}, "p": {"class"}}, url_schemes={"http", "https"}, link_rel="nofollow noopener noreferrer")
    return RenderedNote(html=cleaned, mentions=tuple(mentions), tags=tuple(tags.values()))
