"""Collections of an own published object: ``replies``, ``likes``, and ``shares`` (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.11 A, phase E6).

The forms follow Mastodon, so that a server that crawls reply trees (Mastodon 4.5 and later) can read the thread of a Claire object:

- ``replies``: a ``Collection`` whose embedded ``first`` page has the replies of the author of the object (at most ``REPLIES_FIRST_PAGE_SIZE``), and whose ``next`` goes to pages with the other replies.
- ``likes`` and ``shares``: a ``Collection`` with ``totalItems`` and no items. Who liked or boosted is not public.

The builders have no database access and no network access.
"""

from __future__ import annotations

from typing import Any

__all__ = ["AS2_CONTEXT", "COLLECTION_NAMES", "REPLIES_FIRST_PAGE_SIZE", "REPLIES_PAGE_SIZE", "count_collection", "plus_ones_collection", "rendition_body_document", "renditions_collection", "source_index", "replies_collection", "replies_page"]

AS2_CONTEXT = "https://www.w3.org/ns/activitystreams"
COLLECTION_NAMES: tuple[str, ...] = ("replies", "likes", "shares", "renditions", "plus-ones")
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


def renditions_collection(iri: str, items: list[str]) -> dict[str, Any]:
    """The collection ``claire:renditions`` of a document object (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.5): the published rendition objects, the document object (the 대표 정리본) first, then the template order. No pages: a document has at most three renditions."""
    from .knowledge.vocabulary import WIRE_CONTEXT_V2

    return {"@context": list(WIRE_CONTEXT_V2), "id": iri, "type": "OrderedCollection", "totalItems": len(items), "orderedItems": list(items)}


def rendition_body_document(iri: str, rendition_iri: str, *, html: str, source: str, body_hash: str) -> dict[str, Any]:
    """The body representation of a rendition (Section 5.5): the compiled body (``content``, cleaned by the caller) and the AsciiDoc text (``source``)."""
    from .knowledge.vocabulary import BODY_HASH, RENDITION, RENDITION_BODY, WIRE_CONTEXT_V2

    return {"@context": list(WIRE_CONTEXT_V2), "id": iri, "type": RENDITION_BODY, RENDITION: rendition_iri, "mediaType": "text/html", "content": html, "source": {"mediaType": "text/asciidoc", "content": source}, BODY_HASH: body_hash}


def source_index(iri: str, items: list[str]) -> dict[str, Any]:
    """The source index of one source key (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.4 rule 3): the published rendition objects of this server, each document object followed by its rendition objects."""
    from .knowledge.vocabulary import WIRE_CONTEXT_V2

    return {"@context": list(WIRE_CONTEXT_V2), "id": iri, "type": "OrderedCollection", "totalItems": len(items), "orderedItems": list(items)}


def plus_ones_collection(iri: str, marks: dict[str, int]) -> dict[str, Any]:
    """The collection ``claire:plusOnes`` of a rendition object (Section 5.12 rule 7): the counts only. Who gave a +1 is not public."""
    from .knowledge.vocabulary import SCORE, SERVERS, WIRE_CONTEXT_V2

    return {"@context": list(WIRE_CONTEXT_V2), "id": iri, "type": "Collection", "totalItems": marks["total"], SERVERS: marks["servers"], SCORE: marks["score"]}
