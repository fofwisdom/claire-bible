"""The opinions of a published object for its public page (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.11 B and D, phase E7a).

``opinions_view`` gives the JSON of the 의견 tab: the opinions with their parent, the counts, and the refresh state of the fetch (the Async Refreshes of Mastodon). The text of a remote post goes to the page as data, not as markup: ``text_segments`` converts the HTML of the remote server to paragraphs of text and ``http``/``https`` links, and the page makes text nodes and links from them (Section 5.11 D2). No other markup, image, or style of a remote server enters the page.
"""

from __future__ import annotations

import sqlite3
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

from ..store import federation_client as client_store
from ..store import federation_notes as notes_store
from ..store import federation_opinions as opinions_store
from ..store import federation_quotes as quotes_store

__all__ = ["REFRESH_RETRY_SECONDS", "opinions_view", "text_segments"]

# The page asks again after this time while a fetch runs (Section 5.11 B6).
REFRESH_RETRY_SECONDS = 3
_BLOCK_TAGS = {"p", "div", "li", "blockquote", "pre", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table", "tr"}
_MAX_TEXT = 20000


class _Segments(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.paragraphs: list[list[dict[str, str]]] = [[]]
        self.link: str | None = None
        self.skip = 0
        self.size = 0

    def _break(self) -> None:
        if self.paragraphs[-1]:
            self.paragraphs.append([])

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "template"):
            self.skip += 1
        elif tag == "br":
            self.paragraphs[-1].append({"text": "\n"})
        elif tag in _BLOCK_TAGS:
            self._break()
        elif tag == "a":
            href = dict(attrs).get("href") or ""
            try:
                scheme = urlsplit(href).scheme.lower()
            except ValueError:
                scheme = ""
            self.link = href if scheme in ("http", "https") and len(href) <= 2048 else None

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "template"):
            self.skip = max(0, self.skip - 1)
        elif tag in _BLOCK_TAGS:
            self._break()
        elif tag == "a":
            self.link = None

    def handle_data(self, data: str) -> None:
        if self.skip or not data or self.size >= _MAX_TEXT:
            return
        text = data[: _MAX_TEXT - self.size]
        self.size += len(text)
        last = self.paragraphs[-1]
        if self.link is not None:
            if last and last[-1].get("href") == self.link:
                last[-1]["text"] += text
            else:
                last.append({"text": text, "href": self.link})
        elif last and "href" not in last[-1] and last[-1]["text"] != "\n":
            last[-1]["text"] += text
        else:
            last.append({"text": text})


def text_segments(html: str) -> list[list[dict[str, str]]]:
    """The paragraphs of a remote post: each paragraph is a list of ``{"text"}`` and ``{"text", "href"}`` segments (``href`` only ``http`` or ``https``). An empty paragraph is not in the list."""
    parser = _Segments()
    parser.feed(html or "")
    parser.close()
    out = []
    for paragraph in parser.paragraphs:
        kept = [segment for segment in paragraph if segment["text"].strip() or segment["text"] == "\n"]
        while kept and kept[0]["text"] == "\n":
            kept.pop(0)
        while kept and kept[-1]["text"] == "\n":
            kept.pop()
        if kept:
            out.append(kept)
    return out


def _local_replies(client: sqlite3.Connection, conn: sqlite3.Connection, object_iri: str, origin: str, actor_ids: set[int]) -> list[dict[str, Any]]:
    """The replies of the accounts of this server (their own notes) to the object."""
    out = []
    for public_id, account_id in notes_store.reply_notes(client, object_iri):
        if account_id not in actor_ids:
            continue
        note = notes_store.get_note(client, public_id)
        row = conn.execute("SELECT public_id, username, display_name FROM accounts WHERE id=?", (account_id,)).fetchone()
        if note is None or row is None:
            continue
        actor = f"{origin}/ap/actors/{row[0]}"
        host = urlsplit(origin).netloc
        out.append({
            "iri": f"{origin}/ap/objects/{public_id}", "parent": object_iri, "kind": "reply", "local": True,
            "author": {"iri": actor, "name": row[2] or row[1], "handle": f"@{row[1]}@{host}"},
            "url": f"{origin}/ap/objects/{public_id}", "summary": note.summary, "sensitive": bool(note.sensitive), "language": note.language,
            "published": note.published_at, "text": text_segments(note.content_html),
        })
    return out


def opinions_view(conn: sqlite3.Connection, client: sqlite3.Connection, *, object_iri: str, object_public_id: int, origin: str, actor_ids: set[int], now: float) -> dict[str, Any]:
    """The JSON of the 의견 tab of one own object. ``conn`` is ``claire.db`` and ``client`` is ``client.db``."""
    opinions: list[dict[str, Any]] = []
    for opinion in opinions_store.opinions_of(client, object_iri):
        opinions.append({
            "iri": opinion.opinion_iri, "parent": opinion.parent_iri, "kind": opinion.kind, "local": False,
            "author": {"iri": opinion.author_iri, "name": opinion.author_name, "handle": opinion.author_handle},
            "url": opinion.url or opinion.opinion_iri, "summary": opinion.summary, "sensitive": opinion.sensitive, "language": opinion.language,
            "published": opinion.published, "text": text_segments(opinion.content_html),
        })
    seen = {item["iri"] for item in opinions}
    opinions += [item for item in _local_replies(client, conn, object_iri, origin, actor_ids) if item["iri"] not in seen]
    opinions.sort(key=lambda item: (item["published"] or 0.0, item["iri"]))
    reactions = client_store.count_object_reactions(client, [object_public_id])[object_public_id]
    counts = {
        "replies": sum(1 for item in opinions if item["kind"] == "reply"),
        "quotes": max(sum(1 for item in opinions if item["kind"] == "quote"), quotes_store.count_approved_quotes(client, [object_public_id])[object_public_id]),
        "likes": reactions["like"],
        "shares": reactions["announce"],
    }
    fetch = opinions_store.get_fetch(client, object_iri)
    refresh = None
    if fetch is not None:
        status = "running" if fetch.status == "pending" else fetch.status
        refresh = {"id": str(fetch.id), "status": status, "retry": REFRESH_RETRY_SECONDS if status == "running" else 0, "result_count": fetch.result_count}
    return {"published": True, "object": object_iri, "opinions": opinions, "counts": counts, "refresh": refresh}
