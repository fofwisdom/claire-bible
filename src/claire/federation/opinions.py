"""The opinions of a published object for its public page (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.11 B and D, phase E7a).

``opinions_view`` gives the JSON of the 의견 tab: the opinions with their parent, the counts, and the refresh state of the fetch (the Async Refreshes of Mastodon). The text of a remote post goes to the page as data, not as markup: ``text_segments`` converts the HTML of the remote server to paragraphs of text and ``http``/``https`` links, and the page makes text nodes and links from them (Section 5.11 D2). No other markup, image, or style of a remote server enters the page.
"""

from __future__ import annotations

import sqlite3
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

from ..store import federation as federation_store
from ..store import federation_client as client_store
from ..store import federation_notes as notes_store
from ..store import federation_opinions as opinions_store
from ..store import federation_quotes as quotes_store

__all__ = ["ACTIVE_SECONDS", "REFRESH_MAX_OBJECTS", "REFRESH_RETRY_SECONDS", "active_objects", "daily_refresh", "opinions_view", "purge_gone_objects", "text_segments"]

# The page asks again after this time while a fetch runs (Section 5.11 B6).
REFRESH_RETRY_SECONDS = 3
# The scheduled refresh (Section 5.11 E): the objects with activity in the last 7 days, 200 at most in one run.
ACTIVE_SECONDS = 7 * 24 * 3600.0
REFRESH_MAX_OBJECTS = 200
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


def _local_replies(client: sqlite3.Connection, conn: sqlite3.Connection, parents: list[str], origin: str, actor_ids: set[int]) -> list[dict[str, Any]]:
    """The replies of the accounts of this server (their own notes) to the object or to one of its opinions (``parents``), oldest first."""
    out = []
    host = urlsplit(origin).netloc
    for start in range(0, len(parents), 500):
        part = parents[start:start + 500]
        rows = client.execute(f"SELECT public_id, account_id, in_reply_to_iri FROM federation_notes WHERE in_reply_to_iri IN ({', '.join('?' for _ in part)}) AND deleted_at IS NULL ORDER BY public_id", part).fetchall()
        for public_id, account_id, parent in rows:
            if int(account_id) not in actor_ids:
                continue
            note = notes_store.get_note(client, int(public_id))
            row = conn.execute("SELECT public_id, username, display_name FROM accounts WHERE id=?", (int(account_id),)).fetchone()
            if note is None or row is None:
                continue
            actor = f"{origin}/ap/actors/{row[0]}"
            out.append({
                "iri": f"{origin}/ap/objects/{public_id}", "parent": str(parent), "kind": "reply", "local": True,
                "author": {"iri": actor, "name": row[2] or row[1], "handle": f"@{row[1]}@{host}"},
                "url": f"{origin}/ap/objects/{public_id}", "summary": note.summary, "sensitive": bool(note.sensitive), "language": note.language,
                "published": note.published_at, "text": text_segments(note.content_html),
            })
    return out


def opinions_view(conn: sqlite3.Connection, client: sqlite3.Connection, *, object_iri: str, object_public_id: int | None, origin: str, actor_ids: set[int], now: float, moderator: bool = False, frame_base: str | None = None) -> dict[str, Any]:
    """The JSON of the 의견 tab of one own object. ``conn`` is ``claire.db`` and ``client`` is ``client.db``. A ``moderator`` (the author of the object or a server admin, Section 5.11 D5) also gets the hidden opinions, each with ``hidden`` true, so that the page can show them again. With ``frame_base`` (the path of the opinions route of the page), each remote opinion has ``frame_url``: its formatted body and attachments in a frame (Section 5.11 D2)."""
    opinions: list[dict[str, Any]] = []
    for opinion in opinions_store.opinions_of(client, object_iri, include_hidden=moderator):
        opinions.append({
            "iri": opinion.opinion_iri, "parent": opinion.parent_iri, "kind": opinion.kind, "local": False, "hidden": opinion.hidden_at is not None,
            "author": {"iri": opinion.author_iri, "name": opinion.author_name, "handle": opinion.author_handle},
            "url": opinion.url or opinion.opinion_iri, "summary": opinion.summary, "sensitive": opinion.sensitive, "language": opinion.language,
            "published": opinion.published, "text": text_segments(opinion.content_html),
            "attachments": len(opinion.attachments),
            "frame_url": None if frame_base is None else f"{frame_base}/{opinions_store.opinion_key(opinion.opinion_iri)}",
        })
    seen = {item["iri"] for item in opinions}
    parents = [object_iri, *(item["iri"] for item in opinions if not item.get("hidden"))]
    opinions += [item for item in _local_replies(client, conn, parents, origin, actor_ids) if item["iri"] not in seen]
    opinions.sort(key=lambda item: (item["published"] or 0.0, item["iri"]))
    shown = [item for item in opinions if not item.get("hidden")]
    quoted = sum(1 for item in shown if item["kind"] == "quote")
    # An object of another server (``object_public_id`` None, Section 5.11 C2): this server does not have its likes and boosts.
    reactions = None if object_public_id is None else client_store.count_object_reactions(client, [object_public_id])[object_public_id]
    counts = {
        "replies": sum(1 for item in shown if item["kind"] == "reply"),
        "quotes": quoted if object_public_id is None else max(quoted, quotes_store.count_approved_quotes(client, [object_public_id])[object_public_id]),
        "likes": None if reactions is None else reactions["like"],
        "shares": None if reactions is None else reactions["announce"],
    }
    fetch = opinions_store.get_fetch(client, object_iri)
    refresh = None
    if fetch is not None:
        status = "running" if fetch.status == "pending" else fetch.status
        refresh = {"id": str(fetch.id), "status": status, "retry": REFRESH_RETRY_SECONDS if status == "running" else 0, "result_count": fetch.result_count}
    return {"published": True, "object": object_iri, "opinions": opinions, "counts": counts, "refresh": refresh, "moderation": {"url": "/api/federation/opinions/hide"} if moderator else None}


def purge_gone_objects(conn: sqlite3.Connection, client: sqlite3.Connection, *, origin: str) -> int:
    """Delete the opinions and the fetch of each object that is gone (Section 5.11 C4): an own object without its row (a retract, a gone rendition, a purge), and an object of another server that is not a cached rendition now (rule C2). Return the number of deleted opinions. The caller commits ``client``."""
    prefix = f"{origin.rstrip('/')}/ap/objects/"
    removed = 0
    for object_iri in opinions_store.object_iris(client):
        if object_iri.startswith(prefix):
            number = object_iri[len(prefix):]
            gone = not (number.isascii() and number.isdigit()) or federation_store.owning_object(conn, int(number)) is None
        else:
            gone = conn.execute("SELECT 1 FROM remote_renditions WHERE iri=?", (object_iri,)).fetchone() is None
        if gone:
            removed += opinions_store.delete_for_object(client, object_iri)
    return removed


def active_objects(conn: sqlite3.Connection, client: sqlite3.Connection, *, origin: str, now: float) -> list[tuple[str, float]]:
    """The own published objects that got a delivered reply, a like, or a quote request in the last 7 days (Section 5.11 E), the newest activity first, ``REFRESH_MAX_OBJECTS`` at most: (the object address, the time of its publication)."""
    since = now - ACTIVE_SECONDS
    prefix = f"{origin.rstrip('/')}/ap/objects/"
    last: dict[int, float] = {}

    def seen(number: int, at: float) -> None:
        last[number] = max(at, last.get(number, at))

    for iri, at in client.execute("SELECT in_reply_to_iri, MAX(received_at) FROM federation_timeline_items WHERE substr(in_reply_to_iri, 1, ?)=? AND received_at>=? GROUP BY in_reply_to_iri", (len(prefix), prefix, since)).fetchall():
        number = str(iri)[len(prefix):]
        if str(iri).startswith(prefix) and number.isascii() and number.isdigit():
            seen(int(number), float(at))
    for number, at in client.execute("SELECT object_public_id, MAX(received_at) FROM federation_object_reactions WHERE kind='like' AND received_at>=? GROUP BY object_public_id", (since,)).fetchall():
        seen(int(number), float(at))
    for number, at in client.execute("SELECT object_public_id, MAX(created_at) FROM federation_quote_authorizations WHERE created_at>=? GROUP BY object_public_id", (since,)).fetchall():
        seen(int(number), float(at))
    out: list[tuple[str, float]] = []
    for number, _at in sorted(last.items(), key=lambda pair: (-pair[1], pair[0])):
        owning = federation_store.owning_object(conn, number)
        if owning is None:
            continue
        document, rendition = owning
        out.append((f"{prefix}{number}", rendition.created_at if rendition is not None else document.published_at))
        if len(out) >= REFRESH_MAX_OBJECTS:
            break
    return out


def daily_refresh(conn: sqlite3.Connection, client: sqlite3.Connection, *, origin: str, now: float) -> int:
    """Ask for a fetch of the opinions of each active object (Section 5.11 E) with the conditions of rule B2. Return the number of fetches that wait after the run. The caller commits ``client``."""
    return sum(1 for object_iri, published in active_objects(conn, client, origin=origin, now=now) if opinions_store.request_fetch(client, object_iri, own=True, object_published=published, now=now) is not None)