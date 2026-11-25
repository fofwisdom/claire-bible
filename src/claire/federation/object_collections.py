"""Collections of an own published object: ``replies``, ``likes``, and ``shares`` (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.11 A, phase E6).

The forms follow Mastodon, so that a server that crawls reply trees (Mastodon 4.5 and later) can read the thread of a Claire object:

- ``replies``: a ``Collection`` whose embedded ``first`` page has the replies of the author of the object (at most ``REPLIES_FIRST_PAGE_SIZE``), and whose ``next`` goes to pages with the other replies.
- ``likes`` and ``shares``: a ``Collection`` with ``totalItems`` and no items. Who liked or boosted is not public.

The builders have no database access and no network access.
"""

from __future__ import annotations

from typing import Any

__all__ = ["AS2_CONTEXT", "COLLECTION_NAMES", "REPLIES_FIRST_PAGE_SIZE", "REPLIES_PAGE_SIZE", "count_collection", "replies_collection", "replies_page"]

AS2_CONTEXT = "https://www.w3.org/ns/activitystreams"
COLLECTION_NAMES: tuple[str, ...] = ("replies", "likes", "shares")
REPLIES_FIRST_PAGE_SIZE = 5
REPLIES_PAGE_SIZE = 20


def count_collection(iri: str, total: int) -> dict[str, Any]:
    """A collection that gives only the number of its items."""
    return {"@context": AS2_CONTEXT, "id": iri, "type": "Collection", "totalItems": int(total)}


def _later(self_replies: list[str], other_replies: list[str]) -> list[str]:
    # The replies after the first page: the replies of the author that the first page does not have, then the replies of other actors.
    return [*self_replies[REPLIES_FIRST_PAGE_SIZE:], *other_replies]


def _page_iri(iri: str, cursor: int) -> str:
    return f"{iri}?page=true" if cursor == 0 else f"{iri}?page=true&cursor={cursor}"


def replies_collection(iri: str, self_replies: list[str], other_replies: list[str]) -> dict[str, Any]:
    """The ``replies`` collection with its first page. ``self_replies`` are the replies of the author of the object, ``other_replies`` those of other actors; each list is in the order of the pages."""
    first: dict[str, Any] = {"type": "CollectionPage", "partOf": iri, "items": self_replies[:REPLIES_FIRST_PAGE_SIZE]}
    if _later(self_replies, other_replies):
        first["next"] = _page_iri(iri, 0)
    return {"@context": AS2_CONTEXT, "id": iri, "type": "Collection", "totalItems": len(self_replies) + len(other_replies), "first": first}


def replies_page(iri: str, self_replies: list[str], other_replies: list[str], cursor: int) -> dict[str, Any] | None:
    """One page of the replies after the first page. ``cursor`` is the number of replies before the page. ``None`` when the page has no item and is not the page of cursor 0."""
    later = _later(self_replies, other_replies)
    if cursor < 0 or (cursor > 0 and cursor >= len(later)):
        return None
    page: dict[str, Any] = {"@context": AS2_CONTEXT, "id": _page_iri(iri, cursor), "type": "CollectionPage", "partOf": iri, "items": later[cursor : cursor + REPLIES_PAGE_SIZE]}
    if cursor + REPLIES_PAGE_SIZE < len(later):
        page["next"] = _page_iri(iri, cursor + REPLIES_PAGE_SIZE)
    return page
