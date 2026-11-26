"""Vocabulary and wire contract of the federation of shared knowledge (KNOWLEDGE_FEDERATION_DESIGN.md Sections 9 to 12, unit KF0).

The module has four parts:

- The identifiers: the context IRI, the namespace, and each term name (decision KD9).
- The context document and the vocabulary page. The two are made from the table ``TERMS``. Claire never gets the context from the network (Section 9.1 rule 2).
- The builders. A builder gets plain dictionaries and IRIs, and gives one object or one activity in the wire form. A builder does not make an IRI and reads no table.
- The validators. A validator gets a value that came from another server and gives its kind, or raises ``ContractError`` with an error code. A necessary field that is absent is an error. No validator fills a default.

Claire servers read the compact names of the wire form directly. They use no JSON-LD processor. Thus the wire form is fixed: ``to`` is an array, a Claire term has the prefix ``claire:``, and a time is an ISO 8601 text with a time zone.

Rules that are not in this module: the scheme rule for addresses (``http`` only in development), domain blocks, size limits, and HTML sanitization. The inbox of unit KF1 applies them.
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from ...ontology.base import normalize_name

__all__ = [
    "ACCEPTS_AUTOMATIC",
    "ACTIVITY_KINDS",
    "AUTOMATIC",
    "AUTOMATIC_NOT_ACCEPTED",
    "AS2_CONTEXT",
    "AS2_TERMS",
    "CLAIRE_CONTEXT",
    "CLAIRE_CONTEXT_V2",
    "CLAIRE_NS",
    "CONTEXT_V2_LOCAL_PATH",
    "CONTEXT_LOCAL_PATH",
    "CONTEXT_MEDIA_TYPE",
    "ERROR_CODES",
    "FORGEFED_NS",
    "KNOWLEDGE_TYPES",
    "LIMIT_REACHED",
    "MAX_SIGNATURE_ENTITIES",
    "OBJECT_KINDS",
    "REASON_CODES",
    "REJECT_REASON_CODES",
    "ROLE_COLLABORATOR",
    "TERMS",
    "TERMS_V2",
    "VOCABULARY_LOCAL_PATH",
    "WIRE_CONTEXT",
    "WIRE_CONTEXT_V2",
    "ContractError",
    "build_accept_follow",
    "build_accept_offer",
    "build_contribution",
    "build_delete_document",
    "build_delete_knowledge_base",
    "build_delete_rendition",
    "build_document",
    "build_extraction",
    "build_follow",
    "build_grant",
    "build_knowledge_base",
    "build_offer",
    "build_reject_offer",
    "build_revoke",
    "build_undo_follow",
    "build_update_document",
    "build_update_knowledge_base",
    "context_document",
    "context_document_v2",
    "context_json",
    "context_json_v2",
    "is_content_hash",
    "is_iri",
    "iso_time",
    "term_iri",
    "validate_activity",
    "validate_object",
    "vocabulary_page",
    "with_context",
]

# ── Identifiers (decision KD9) ───────────────────────────────────────────────

AS2_CONTEXT = "https://www.w3.org/ns/activitystreams"
CLAIRE_CONTEXT = "https://w3id.org/claire-bible/ns/v1"
CLAIRE_NS = "https://w3id.org/claire-bible/ns#"
FORGEFED_NS = "https://forgefed.org/ns#"
WIRE_CONTEXT = (AS2_CONTEXT, CLAIRE_CONTEXT)
# Version 2 (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.5): the terms of version 1 and the terms of renditions and +1. The document of version 1 does not change.
CLAIRE_CONTEXT_V2 = "https://w3id.org/claire-bible/ns/v2"
WIRE_CONTEXT_V2 = (AS2_CONTEXT, CLAIRE_CONTEXT_V2)

# Each Claire server serves the documents at these local paths (Section 9.1 rules 1 and 3).
CONTEXT_LOCAL_PATH = "/ns/v1"
CONTEXT_V2_LOCAL_PATH = "/ns/v2"
VOCABULARY_LOCAL_PATH = "/ns"
CONTEXT_MEDIA_TYPE = "application/ld+json"

# Types.
KNOWLEDGE_BASE = "claire:KnowledgeBase"
EXTRACTION = "claire:Extraction"
CONTRIBUTION = "claire:Contribution"
GRANT = "Grant"
REVOKE = "Revoke"

# Properties.
KNOWLEDGE_TYPE = "claire:knowledgeType"
DOCUMENT_COUNT = "claire:documentCount"
ENTITY_COUNT = "claire:entityCount"
RELATION_COUNT = "claire:relationCount"
SIGNATURE = "claire:signature"
DOCUMENTS = "claire:documents"
CONTENT_HASH = "claire:contentHash"
ROOT = "claire:root"
EXTRACTION_OF = "claire:extraction"
KEY_CLAIMS = "claire:keyClaims"
ENTITIES = "claire:entities"
RELATIONS = "claire:relations"
DOCUMENT = "claire:document"
REASON_CODE = "claire:reasonCode"
CAPABILITY = "capability"
# Unit KF5 (Section 12.3): the mark of an automatic contribution, and the record of a knowledge base tells if its server accepts automatic contributions now.
AUTOMATIC = "claire:automatic"
ACCEPTS_AUTOMATIC = "claire:acceptsAutomatic"

# The one role that a grant can give (Section 12.2).
ROLE_COLLABORATOR = "claire:collaborator"

KNOWLEDGE_TYPES = ("real", "fictional")
REASON_CODES = ("off_subject", "wrong_knowledge_type", "duplicate", "low_quality", "spam", "other")
# The reason of a ``Reject`` of an automatic contribution: the server of the knowledge base accepts no automatic contributions now (Section 12.3 rule 1). It is not a reason of the reviewers, so a ``Delete`` cannot have it.
AUTOMATIC_NOT_ACCEPTED = "automatic_not_accepted"
# The reason of a ``Reject`` that a limit of the server of the knowledge base gave, not its reviewers: the pending contributions of the account or of its domain are at the limit now (KS3). The sender can offer the document again later. It is not a reason of the reviewers, so a ``Delete`` cannot have it.
LIMIT_REACHED = "limit_reached"
REJECT_REASON_CODES = (*REASON_CODES, AUTOMATIC_NOT_ACCEPTED, LIMIT_REACHED)
MAX_SIGNATURE_ENTITIES = 256
# The largest count of the wire form: the largest integer that each JSON reader keeps without a change (2^53), and that SQLite stores.
MAX_COUNT = 2**53
# The length of one IRI of the wire form (the limit of the inbox journal for an activity IRI).
MAX_IRI_LENGTH = 2048
# The identifiers in ``sameAs`` of one entity, and the length of one identifier.
MAX_SAME_AS = 16
MAX_SAME_AS_LENGTH = 100

_XSD_COUNT = "http://www.w3.org/2001/XMLSchema#nonNegativeInteger"

# One row for each term. ``definition`` is the entry of the term in the context document; ``None`` is a term that the prefix ``claire`` expands and that needs no entry. ``text`` is the definition for the vocabulary page.
TERMS: tuple[dict[str, Any], ...] = (
    {"term": KNOWLEDGE_BASE, "kind": "class", "base": "Group", "definition": None, "text": "A shared knowledge base: a knowledge base that has no owner, that is always public, and that accounts build together. It is an actor. Servers that hold copies of its documents follow it."},
    {"term": EXTRACTION, "kind": "class", "base": "Object", "definition": None, "text": "The entities and the relations that a server found in one document. A receiver builds its graph from them and does not send the document to a language model."},
    {"term": CONTRIBUTION, "kind": "class", "base": "Object", "definition": None, "text": "One document with its extraction that an account offers to one shared knowledge base."},
    {"term": GRANT, "kind": "class", "base": "Activity", "definition": FORGEFED_NS + "Grant", "text": "An activity that gives a role in a resource to an actor until an end time. The term is the one of the ForgeFed vocabulary. In Claire, a shared knowledge base gives the role claire:collaborator to an account of another server."},
    {"term": REVOKE, "kind": "class", "base": "Activity", "definition": FORGEFED_NS + "Revoke", "text": "An activity that ends a grant immediately. The term is the one of the ForgeFed vocabulary."},
    {"term": KNOWLEDGE_TYPE, "kind": "property", "definition": {"@id": KNOWLEDGE_TYPE}, "text": "The knowledge type of a shared knowledge base: \"real\" for knowledge about the real world, or \"fictional\" for knowledge about an invented world."},
    {"term": DOCUMENT_COUNT, "kind": "property", "definition": {"@id": DOCUMENT_COUNT, "@type": _XSD_COUNT}, "text": "The number of visible documents in a shared knowledge base."},
    {"term": ENTITY_COUNT, "kind": "property", "definition": {"@id": ENTITY_COUNT, "@type": _XSD_COUNT}, "text": "The number of entities in a shared knowledge base."},
    {"term": RELATION_COUNT, "kind": "property", "definition": {"@id": RELATION_COUNT, "@type": _XSD_COUNT}, "text": "The number of relations in a shared knowledge base."},
    {"term": SIGNATURE, "kind": "property", "definition": {"@id": SIGNATURE, "@type": "@json"}, "text": "The subject signature of a shared knowledge base, as one JSON value. The key \"entities\" has at most 256 rows [type, norm_name, weight]: the entities that the most documents have. The key \"visible_documents\" is the number of documents that were counted. The key \"computed_at\" is the time of the calculation. A server compares signatures to put knowledge bases with the same subject in one theme."},
    {"term": DOCUMENTS, "kind": "property", "definition": {"@id": DOCUMENTS, "@type": "@id"}, "text": "The collection of the documents of a shared knowledge base."},
    {"term": CONTENT_HASH, "kind": "property", "definition": {"@id": CONTENT_HASH}, "text": "The SHA-256 of the source text of a document, as 64 lowercase hexadecimal characters. A different value for the same document is a revision."},
    {"term": ROOT, "kind": "property", "definition": {"@id": ROOT, "@type": "@id"}, "text": "The root document: the document from which a chain of copies started. Revisions come from it. A root document refers to itself."},
    {"term": EXTRACTION_OF, "kind": "property", "definition": {"@id": EXTRACTION_OF, "@type": "@id"}, "text": "The extraction of a document."},
    {"term": KEY_CLAIMS, "kind": "property", "definition": {"@id": KEY_CLAIMS, "@container": "@list"}, "text": "The main statements of a document, as plain text, in sequence."},
    {"term": ENTITIES, "kind": "property", "definition": {"@id": ENTITIES, "@type": "@json"}, "text": "The entities of an extraction, as one JSON array. Each entity has the keys \"name\", \"type\", \"norm_name\" (the name in its normal form; \"type\" and \"norm_name\" together are the key of the entity), \"aliases\", and \"observations\". The key \"sameAs\" is optional: identifiers of the same thing at an authority, for example \"wd:Q42\" (Wikidata), an ISBN, or an ORCID. The key \"proposed_type\" is optional."},
    {"term": RELATIONS, "kind": "property", "definition": {"@id": RELATIONS, "@type": "@json"}, "text": "The relations of an extraction, as one JSON array. Each relation has the keys \"source\" and \"target\" (each one the \"name\" of an entity of the same extraction) and \"type\". The key \"proposed_type\" is optional."},
    {"term": DOCUMENT, "kind": "property", "definition": {"@id": DOCUMENT, "@type": "@id"}, "text": "The document of a contribution."},
    {"term": REASON_CODE, "kind": "property", "definition": {"@id": REASON_CODE}, "text": "The reason of the reviewers for a rejection or a removal: \"off_subject\", \"wrong_knowledge_type\", \"duplicate\", \"low_quality\", \"spam\", or \"other\". A rejection of an automatic contribution can also have \"automatic_not_accepted\": the server of the knowledge base accepts no automatic contributions now. A rejection of a contribution can also have \"limit_reached\": the contributions of the account or of its server that wait for a decision are at a limit of the server of the knowledge base now. With these two values the account can offer the document again later."},
    {"term": AUTOMATIC, "kind": "property", "definition": {"@id": AUTOMATIC}, "text": "true in a contribution that the automatic rule of the account selected. A server that accepts no automatic contributions rejects such a contribution. A contribution without this term is one that the account selected itself."},
    {"term": ACCEPTS_AUTOMATIC, "kind": "property", "definition": {"@id": ACCEPTS_AUTOMATIC}, "text": "true when the server of a shared knowledge base accepts automatic contributions now, false when it rejects them. A server reads it before it sends an automatic contribution."},
    {"term": CAPABILITY, "kind": "property", "definition": {"@id": FORGEFED_NS + "capability", "@type": "@id"}, "text": "The grant that permits an activity. The term is the one of the ForgeFed vocabulary. In Claire, an offer of a remote collaborator refers to its grant."},
    {"term": ROLE_COLLABORATOR, "kind": "individual", "definition": None, "text": "The role of an account whose contributions to a shared knowledge base need no approval."},
)

# Version 2: renditions (정리본) and +1 (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Sections 5.5 and 5.12). The rows have the form of ``TERMS``.
RENDITION_BODY = "claire:RenditionBody"
PLUS_ONE = "claire:PlusOne"
RENDITION = "claire:rendition"
RENDITION_OF = "claire:renditionOf"
RENDITIONS = "claire:renditions"
TEMPLATE = "claire:template"
TEMPLATE_VERSION = "claire:templateVersion"
SOURCE_KEY = "claire:sourceKey"
SOURCE_ID_VERSION = "claire:sourceIdVersion"
BODY = "claire:body"
BODY_HASH = "claire:bodyHash"
PLUS_ONES = "claire:plusOnes"
SERVERS = "claire:servers"
SCORE = "claire:score"
TEMPLATES = ("standard", "concise", "academic")

TERMS_V2: tuple[dict[str, Any], ...] = (
    {"term": RENDITION_BODY, "kind": "class", "base": "Object", "definition": None, "text": "The body of a rendition: the compiled HTML in \"content\" and the AsciiDoc text in \"source\". The origin gives it only to the server actor of another Claire server, with a valid HTTP signature."},
    {"term": PLUS_ONE, "kind": "class", "base": "Activity", "definition": None, "text": "A +1 of an account for a rendition. Only Claire servers send it and read it."},
    {"term": RENDITION, "kind": "property", "definition": {"@id": RENDITION, "@type": "@id"}, "text": "The rendition of a rendition body."},
    {"term": RENDITION_OF, "kind": "property", "definition": {"@id": RENDITION_OF, "@type": "@id"}, "text": "The document object of a rendition that is not the primary rendition. The document object is also the primary rendition."},
    {"term": RENDITIONS, "kind": "property", "definition": {"@id": RENDITIONS, "@type": "@id"}, "text": "The ordered collection of the published renditions of a document, the primary rendition first."},
    {"term": TEMPLATE, "kind": "property", "definition": {"@id": TEMPLATE}, "text": "The rendition template of a rendition: \"standard\", \"concise\", or \"academic\". A new template is a new value; a value never changes its meaning."},
    {"term": TEMPLATE_VERSION, "kind": "property", "definition": {"@id": TEMPLATE_VERSION}, "text": "The version of the rendition template of a rendition, for example \"standard-v1\". \"pre-template\" is a body that was made before the templates and counts as \"standard\"."},
    {"term": SOURCE_KEY, "kind": "property", "definition": {"@id": SOURCE_KEY}, "text": "The SHA-256 of the source identifier of a document (a DOI, an arXiv address, or a normalized web address), as 64 lowercase hexadecimal characters. Documents with the same key have the same source."},
    {"term": SOURCE_ID_VERSION, "kind": "property", "definition": {"@id": SOURCE_ID_VERSION, "@type": _XSD_COUNT}, "text": "The version of the rules that made the source identifier of a source key."},
    {"term": BODY, "kind": "property", "definition": {"@id": BODY, "@type": "@id"}, "text": "The address of the body of a rendition (a claire:RenditionBody)."},
    {"term": BODY_HASH, "kind": "property", "definition": {"@id": BODY_HASH}, "text": "The SHA-256 of the AsciiDoc text of a rendition body, as 64 lowercase hexadecimal characters. A reader compares it before it gets the body again. It is not claire:contentHash, which is the hash of the source text."},
    {"term": PLUS_ONES, "kind": "property", "definition": {"@id": PLUS_ONES}, "text": "The collection of the +1 marks of a rendition, with \"totalItems\", claire:servers, and claire:score."},
    {"term": SERVERS, "kind": "property", "definition": {"@id": SERVERS, "@type": _XSD_COUNT}, "text": "The number of servers of the accounts that gave a +1 to a rendition."},
    {"term": SCORE, "kind": "property", "definition": {"@id": SCORE, "@type": _XSD_COUNT}, "text": "The +1 count of a rendition with the limit for one server."},
)

# The ActivityStreams 2.0 terms that the wire form uses. The ActivityStreams context defines them.
AS2_TERMS = frozenset({
    "id", "type", "actor", "object", "target", "result", "context", "origin", "to", "name", "summary", "content", "url", "attributedTo", "published", "updated", "endTime", "inbox", "outbox", "followers", "formerType",
    "Group", "Article", "Object", "Tombstone", "Follow", "Accept", "Reject", "Undo", "Update", "Delete", "Offer",
})

OBJECT_KINDS = ("knowledge_base", "document", "extraction", "contribution")
# ``delete_rendition`` (version 2, DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.4 rule 4): the ``Delete`` of a rendition object for the server actors that read its body.
ACTIVITY_KINDS = ("follow", "accept_follow", "undo_follow", "update_knowledge_base", "delete_knowledge_base", "update_document", "delete_document", "offer_contribution", "accept_offer", "reject_offer", "grant", "revoke", "delete_rendition")

ERROR_CODES = (
    "not_an_object",  # The value is not a JSON object.
    "missing_context",  # A top-level value has no ``@context`` array with the two context IRIs.
    "missing_field",  # A necessary field is absent.
    "invalid_field",  # A field has a wrong JSON type or a wrong form.
    "unknown_field",  # A row of a JSON value (entity, relation, signature) has a key outside the contract.
    "invalid_iri",  # A field that must be an absolute ``http`` or ``https`` IRI is not one.
    "invalid_time",  # A time is not an ISO 8601 text with a time zone, or an end time is not after its start.
    "unsupported_type",  # The type of the value is not in the vocabulary.
    "unsupported_object",  # The object of an activity is not one that this activity type can have.
    "actor_mismatch",  # Two fields that must name the same actor are different.
    "origin_mismatch",  # An IRI is not on the server of the actor that is the authority for it.
    "target_mismatch",  # The target of an offer is not the knowledge base of its contribution.
    "missing_recipient",  # ``to`` does not have the actor that must get the activity.
    "invalid_knowledge_type",
    "invalid_reason_code",
    "invalid_role",
    "invalid_content_hash",
    "signature_too_large",
    "unknown_relation_entity",  # A relation refers to a name that no entity of the extraction has.
)


class ContractError(ValueError):
    """A value is outside the wire contract. ``code`` is one of ``ERROR_CODES``; ``path`` is the field, with dots."""

    def __init__(self, code: str, path: str) -> None:
        super().__init__(f"{code} at {path or '(root)'}")
        self.code = code
        self.path = path


# ── Context document and vocabulary page ─────────────────────────────────────


def term_iri(term: str) -> str:
    """Give the full IRI of a term of ``TERMS`` or ``TERMS_V2``."""
    for row in (*TERMS, *TERMS_V2):
        if row["term"] == term:
            definition = row["definition"]
            if definition is None:
                return CLAIRE_NS + term.removeprefix("claire:")
            target = definition if isinstance(definition, str) else definition["@id"]
            return CLAIRE_NS + target.removeprefix("claire:") if target.startswith("claire:") else target
    raise KeyError(term)


def context_document() -> dict[str, Any]:
    """Give the context document of version 1. The document of ``v1`` never changes; a new term goes to a new version (Section 9.1 rule 2)."""
    context: dict[str, Any] = {"@version": 1.1, "claire": CLAIRE_NS}
    for row in TERMS:
        if row["definition"] is not None:
            context[row["term"]] = row["definition"]
    return {"@context": context}


def context_json() -> str:
    """Give the text of the file ``static/ns/v1.jsonld``: the body of ``/ns/v1``."""
    return json.dumps(context_document(), indent=2, ensure_ascii=False) + "\n"


def context_document_v2() -> dict[str, Any]:
    """Give the context document of version 2: the entries of version 1 and the entries of ``TERMS_V2``. A document with this context needs no other Claire context."""
    document = context_document()
    for row in TERMS_V2:
        if row["definition"] is not None:
            document["@context"][row["term"]] = row["definition"]
    return document


def context_json_v2() -> str:
    """Give the text of the file ``static/ns/v2.jsonld``: the body of ``/ns/v2``."""
    return json.dumps(context_document_v2(), indent=2, ensure_ascii=False) + "\n"


_KIND_TITLES = (("class", "Types"), ("property", "Properties"), ("individual", "Values"))


def vocabulary_page() -> str:
    """Give the text of the file ``static/ns/index.html``: the page of the vocabulary terms at ``/ns``. Each term has an anchor, so ``/ns#root`` shows the term ``claire:root``."""
    parts = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>Claire Bible vocabulary</title>",
        "<style>body{font-family:system-ui,sans-serif;line-height:1.5;max-width:60rem;margin:2rem auto;padding:0 1rem}table{border-collapse:collapse;width:100%}th,td{border:1px solid #8884;padding:.4rem .6rem;text-align:left;vertical-align:top}code{font-family:ui-monospace,monospace}</style>",
        "</head>",
        "<body>",
        "<h1>Claire Bible vocabulary</h1>",
        "<p>Claire Bible servers use these terms when they exchange shared knowledge with ActivityPub. The terms are for Claire Bible only.</p>",
        f"<p>Namespace: <code>{html.escape(CLAIRE_NS)}</code> (prefix <code>claire</code>). JSON-LD context: <a href=\"{html.escape(CLAIRE_CONTEXT)}\"><code>{html.escape(CLAIRE_CONTEXT)}</code></a>. The context of version 1 does not change. A new term goes to a new version: version 2 (<a href=\"{html.escape(CLAIRE_CONTEXT_V2)}\"><code>{html.escape(CLAIRE_CONTEXT_V2)}</code></a>) has the terms of version 1 and the terms of renditions and +1.</p>",
        f"<p>A document uses this context together with the ActivityStreams context <code>{html.escape(AS2_CONTEXT)}</code>. The terms <code>Grant</code>, <code>Revoke</code>, and <code>capability</code> have the identifiers of the ForgeFed vocabulary (<code>{html.escape(FORGEFED_NS)}</code>). Claire Bible claims no conformance to ForgeFed.</p>",
    ]
    for kind, title in _KIND_TITLES:
        parts.append(f"<h2>{title}</h2>")
        parts.append("<table>")
        parts.append("<tr><th>Term</th><th>Identifier</th>" + ("<th>Base type</th>" if kind == "class" else "") + "<th>Since</th><th>Definition</th></tr>")
        for row, since in [*((r, "v1") for r in TERMS), *((r, "v2") for r in TERMS_V2)]:
            if row["kind"] != kind:
                continue
            iri = term_iri(row["term"])
            anchor = iri.rsplit("#", 1)[1]
            base = f"<td><code>{html.escape(row['base'])}</code></td>" if kind == "class" else ""
            parts.append(f'<tr id="{html.escape(anchor)}"><td><code>{html.escape(row["term"])}</code></td><td><code>{html.escape(iri)}</code></td>{base}<td>{since}</td><td>{html.escape(row["text"])}</td></tr>')
        parts.append("</table>")
    parts += ["</body>", "</html>"]
    return "\n".join(parts) + "\n"


# ── Field checks ─────────────────────────────────────────────────────────────


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError("not_an_object" if not path else "invalid_field", path)
    return value


def _need(obj: dict[str, Any], key: str, path: str) -> Any:
    if key not in obj:
        raise ContractError("missing_field", _join(path, key))
    return obj[key]


def _text(obj: dict[str, Any], key: str, path: str, *, empty: bool) -> str:
    value = _need(obj, key, path)
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ContractError("invalid_field", _join(path, key))
    return value


def _count(obj: dict[str, Any], key: str, path: str) -> int:
    value = _need(obj, key, path)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > MAX_COUNT:
        raise ContractError("invalid_field", _join(path, key))
    return value


def _is_iri(value: Any) -> bool:
    if not isinstance(value, str) or len(value) > MAX_IRI_LENGTH or value != value.strip() or any(ch.isspace() for ch in value):
        return False
    try:
        parts = urlsplit(value)
        host = parts.hostname
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(host) and parts.username is None and parts.password is None


def _iri(obj: dict[str, Any], key: str, path: str) -> str:
    value = _need(obj, key, path)
    if not _is_iri(value):
        raise ContractError("invalid_iri", _join(path, key))
    return value


def _optional_iri(obj: dict[str, Any], key: str, path: str) -> str | None:
    return _iri(obj, key, path) if key in obj else None


def _server(iri: str) -> str:
    """The origin of an IRI as it is written: the scheme and the authority. Two IRIs with another scheme are on two servers."""
    parts = urlsplit(iri)
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"


def _same_server(iri: str, authority: str, path: str) -> None:
    if _server(iri) != _server(authority):
        raise ContractError("origin_mismatch", path)


def _parse_time(value: Any, path: str) -> datetime:
    if not isinstance(value, str):
        raise ContractError("invalid_time", path)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ContractError("invalid_time", path) from exc
    if parsed.tzinfo is None:
        raise ContractError("invalid_time", path)
    return parsed


def _time(obj: dict[str, Any], key: str, path: str) -> datetime:
    return _parse_time(_need(obj, key, path), _join(path, key))


def _types(obj: dict[str, Any], path: str) -> tuple[str, ...]:
    value = _need(obj, "type", path)
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, list) or not values or not all(isinstance(v, str) and v for v in values):
        raise ContractError("invalid_field", _join(path, "type"))
    return tuple(values)


def _text_list(value: Any, path: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ContractError("invalid_field", path)
    return value


def _sealed(row: dict[str, Any], allowed: tuple[str, ...], path: str) -> None:
    for key in row:
        if key not in allowed:
            raise ContractError("unknown_field", _join(path, str(key)))


def _check_context(value: dict[str, Any]) -> None:
    """The ``@context`` array has the ActivityStreams context and the Claire context of version 1 or of version 2 (version 2 has each term of version 1)."""
    context = value.get("@context")
    if not isinstance(context, list) or not any(all(iri in context for iri in wire) for wire in (WIRE_CONTEXT, WIRE_CONTEXT_V2)):
        raise ContractError("missing_context", "@context")


def _iso(seconds: Any, path: str) -> str:
    """Make the wire text of a time from the seconds of a table row (UTC, no part of a second)."""
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        raise ContractError("invalid_time", path)
    return datetime.fromtimestamp(int(seconds), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def time_seconds(value: Any) -> float:
    """The seconds of a time of the wire form (for example ``published`` and ``endTime`` of a ``Grant``). ``ContractError`` with ``invalid_time`` for each other value."""
    return _parse_time(value, "").timestamp()


def is_content_hash(value: Any) -> bool:
    """True when the value is a ``claire:contentHash`` of the wire form: 64 lowercase hexadecimal characters."""
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


def is_iri(value: Any) -> bool:
    """True when the value is an absolute ``http`` or ``https`` IRI of the wire form."""
    return _is_iri(value)


def iso_time(seconds: float) -> str:
    """The wire text of a time from the seconds of a table row."""
    return _iso(seconds, "time")


# ── Validators: objects ──────────────────────────────────────────────────────

_SIGNATURE_KEYS = ("entities", "visible_documents", "computed_at")
_ENTITY_KEYS = ("name", "type", "norm_name", "aliases", "observations", "sameAs", "proposed_type")
_RELATION_KEYS = ("source", "target", "type", "proposed_type")
_HASH = re.compile(r"[0-9a-f]{64}")


def _check_signature(value: Any, path: str) -> None:
    if not isinstance(value, dict):
        raise ContractError("invalid_field", path)
    _sealed(value, _SIGNATURE_KEYS, path)
    entities = _need(value, "entities", path)
    if not isinstance(entities, list):
        raise ContractError("invalid_field", _join(path, "entities"))
    if len(entities) > MAX_SIGNATURE_ENTITIES:
        raise ContractError("signature_too_large", _join(path, "entities"))
    for index, row in enumerate(entities):
        ok = isinstance(row, list) and len(row) == 3 and isinstance(row[0], str) and bool(row[0]) and isinstance(row[1], str) and bool(row[1]) and isinstance(row[2], (int, float)) and not isinstance(row[2], bool) and row[2] > 0
        if not ok:
            raise ContractError("invalid_field", f"{path}.entities.{index}")
    _count(value, "visible_documents", path)
    _time(value, "computed_at", path)


def _check_knowledge_base(obj: dict[str, Any], path: str) -> None:
    for key in ("id", "inbox", "outbox", "followers", "url", DOCUMENTS):
        _iri(obj, key, path)
    for key in ("inbox", "outbox", "followers", DOCUMENTS):
        _same_server(obj[key], obj["id"], _join(path, key))
    _text(obj, "name", path, empty=False)
    _text(obj, "summary", path, empty=True)
    if _need(obj, KNOWLEDGE_TYPE, path) not in KNOWLEDGE_TYPES:
        raise ContractError("invalid_knowledge_type", _join(path, KNOWLEDGE_TYPE))
    for key in (DOCUMENT_COUNT, ENTITY_COUNT, RELATION_COUNT):
        _count(obj, key, path)
    if SIGNATURE in obj:
        _check_signature(obj[SIGNATURE], _join(path, SIGNATURE))
    if ACCEPTS_AUTOMATIC in obj and not isinstance(obj[ACCEPTS_AUTOMATIC], bool):
        raise ContractError("invalid_field", _join(path, ACCEPTS_AUTOMATIC))
    if "updated" in obj:
        _time(obj, "updated", path)


def _check_extraction(obj: dict[str, Any], path: str) -> None:
    _text(obj, "summary", path, empty=True)
    _text_list(_need(obj, KEY_CLAIMS, path), _join(path, KEY_CLAIMS))
    entities = _need(obj, ENTITIES, path)
    relations = _need(obj, RELATIONS, path)
    if not isinstance(entities, list):
        raise ContractError("invalid_field", _join(path, ENTITIES))
    if not isinstance(relations, list):
        raise ContractError("invalid_field", _join(path, RELATIONS))
    names: set[str] = set()
    for index, entity in enumerate(entities):
        at = f"{_join(path, ENTITIES)}.{index}"
        if not isinstance(entity, dict):
            raise ContractError("invalid_field", at)
        _sealed(entity, _ENTITY_KEYS, at)
        for key in ("name", "type", "norm_name"):
            _text(entity, key, at, empty=False)
        for key in ("aliases", "observations"):
            _text_list(_need(entity, key, at), _join(at, key))
        if "sameAs" in entity:
            same_as = entity["sameAs"]
            if not isinstance(same_as, list) or not same_as or len(same_as) > MAX_SAME_AS or not all(isinstance(v, str) and ":" in v and len(v) <= MAX_SAME_AS_LENGTH and not any(ch.isspace() for ch in v) for v in same_as):
                raise ContractError("invalid_field", _join(at, "sameAs"))
        if "proposed_type" in entity:
            _text(entity, "proposed_type", at, empty=False)
        names.add(entity["name"])
    for index, relation in enumerate(relations):
        at = f"{_join(path, RELATIONS)}.{index}"
        if not isinstance(relation, dict):
            raise ContractError("invalid_field", at)
        _sealed(relation, _RELATION_KEYS, at)
        for key in ("source", "target", "type"):
            _text(relation, key, at, empty=False)
        if "proposed_type" in relation:
            _text(relation, "proposed_type", at, empty=False)
        for key in ("source", "target"):
            if relation[key] not in names:
                raise ContractError("unknown_relation_entity", _join(at, key))


def _check_document(obj: dict[str, Any], path: str, *, extraction_necessary: bool) -> None:
    identifier = _iri(obj, "id", path)
    _text(obj, "name", path, empty=False)
    _text(obj, "content", path, empty=True)
    _optional_iri(obj, "url", path)
    _optional_iri(obj, "attributedTo", path)
    _iri(obj, ROOT, path)
    origin = _optional_iri(obj, "origin", path)
    if origin == identifier:
        raise ContractError("invalid_field", _join(path, "origin"))
    content_hash = _need(obj, CONTENT_HASH, path)
    if not isinstance(content_hash, str) or _HASH.fullmatch(content_hash) is None:
        raise ContractError("invalid_content_hash", _join(path, CONTENT_HASH))
    _time(obj, "published", path)
    if "updated" in obj:
        _time(obj, "updated", path)
    if EXTRACTION_OF in obj:
        at = _join(path, EXTRACTION_OF)
        extraction = obj[EXTRACTION_OF]
        if not isinstance(extraction, dict):
            raise ContractError("invalid_field", at)
        if _object_kind(extraction, at) != "extraction":
            raise ContractError("unsupported_type", _join(at, "type"))
        _check_extraction(extraction, at)
    elif extraction_necessary:
        raise ContractError("missing_field", _join(path, EXTRACTION_OF))


def _check_contribution(obj: dict[str, Any], path: str) -> None:
    _optional_iri(obj, "id", path)
    actor = _iri(obj, "attributedTo", path)
    _iri(obj, "context", path)
    at = _join(path, DOCUMENT)
    document = _need(obj, DOCUMENT, path)
    if not isinstance(document, dict):
        raise ContractError("invalid_field", at)
    if _object_kind(document, at) != "document":
        raise ContractError("unsupported_type", _join(at, "type"))
    _check_document(document, at, extraction_necessary=True)
    if document.get("attributedTo") != actor:
        raise ContractError("actor_mismatch", _join(at, "attributedTo"))
    if AUTOMATIC in obj and not isinstance(obj[AUTOMATIC], bool):
        raise ContractError("invalid_field", _join(path, AUTOMATIC))


def _object_kind(obj: dict[str, Any], path: str) -> str:
    types = _types(obj, path)
    if KNOWLEDGE_BASE in types and "Group" in types:
        return "knowledge_base"
    if EXTRACTION in types and "Object" in types:
        return "extraction"
    if CONTRIBUTION in types and "Object" in types:
        return "contribution"
    if types == ("Article",):
        return "document"
    raise ContractError("unsupported_type", _join(path, "type"))


def _validate_object(obj: dict[str, Any], path: str) -> str:
    kind = _object_kind(obj, path)
    if kind == "knowledge_base":
        _check_knowledge_base(obj, path)
    elif kind == "extraction":
        _check_extraction(obj, path)
    elif kind == "contribution":
        _check_contribution(obj, path)
    else:
        _check_document(obj, path, extraction_necessary=False)
    return kind


def validate_object(value: Any, *, top_level: bool) -> str:
    """Check one object of the vocabulary and give its kind (one of ``OBJECT_KINDS``).

    ``top_level`` is true for a document that came alone (the answer of a GET): it must have the ``@context`` array. An object in an activity has no ``@context``. A key outside the contract has no effect, because other contexts can add terms (for example the keys of an actor). The rows of a JSON value (entity, relation, signature) are sealed: a key outside the contract is an error.
    """
    obj = _mapping(value, "")
    if top_level:
        _check_context(obj)
    return _validate_object(obj, "")


# ── Validators: activities ───────────────────────────────────────────────────


def _recipients(activity: dict[str, Any]) -> list[str]:
    value = _need(activity, "to", "")
    if not isinstance(value, list) or not value:
        raise ContractError("invalid_field", "to")
    for index, iri in enumerate(value):
        if not _is_iri(iri):
            raise ContractError("invalid_iri", f"to.{index}")
    return value


def _must_get(recipients: list[str], actor: str) -> None:
    if actor not in recipients:
        raise ContractError("missing_recipient", "to")


def _embedded(activity: dict[str, Any]) -> dict[str, Any]:
    value = _need(activity, "object", "")
    if not isinstance(value, dict):
        raise ContractError("unsupported_object", "object")
    return value


def _reference(activity: dict[str, Any]) -> str:
    value = _need(activity, "object", "")
    if isinstance(value, dict):
        raise ContractError("unsupported_object", "object")
    if not _is_iri(value):
        raise ContractError("invalid_iri", "object")
    return value


def _follow_stub(activity: dict[str, Any]) -> dict[str, Any]:
    follow = _embedded(activity)
    if _types(follow, "object") != ("Follow",):
        raise ContractError("unsupported_object", "object.type")
    for key in ("id", "actor", "object"):
        _iri(follow, key, "object")
    _same_server(follow["id"], follow["actor"], "object.id")
    return follow


def _tombstone(activity: dict[str, Any], actor: str) -> str:
    tombstone = _embedded(activity)
    if _types(tombstone, "object") != ("Tombstone",):
        raise ContractError("unsupported_object", "object.type")
    identifier = _iri(tombstone, "id", "object")
    former = _need(tombstone, "formerType", "object")
    if former == "Article" and TEMPLATE in tombstone:
        # A rendition object (version 2): an account actor deletes it for the server actors that read its body. No reason code: the owner removed it.
        _same_server(identifier, actor, "object.id")
        if tombstone[TEMPLATE] not in TEMPLATES:
            raise ContractError("invalid_field", f"object.{TEMPLATE}")
        if REASON_CODE in activity:
            raise ContractError("unknown_field", REASON_CODE)
        return "delete_rendition"
    if former == "Article":
        _same_server(identifier, actor, "object.id")
        if _need(activity, REASON_CODE, "") not in REASON_CODES:
            raise ContractError("invalid_reason_code", REASON_CODE)
        return "delete_document"
    if former == KNOWLEDGE_BASE:
        if identifier != actor:
            raise ContractError("actor_mismatch", "object.id")
        return "delete_knowledge_base"
    raise ContractError("unsupported_object", "object.formerType")


def validate_activity(value: Any) -> str:
    """Check one activity of Section 9.3 and give its kind (one of ``ACTIVITY_KINDS``).

    The checks are the ones that need only the activity: the form of each field, and the agreement of its fields (the actor, the server of each IRI, the recipient). The checks that need data of this server are not here: the signature of the sender, a domain block, if the knowledge base exists, and if a grant is in effect.
    """
    activity = _mapping(value, "")
    _check_context(activity)
    identifier = _iri(activity, "id", "")
    types = _types(activity, "")
    if len(types) != 1:
        raise ContractError("unsupported_type", "type")
    kind = types[0]
    actor = _iri(activity, "actor", "")
    _same_server(identifier, actor, "id")
    recipients = _recipients(activity)

    if kind == "Follow":
        _must_get(recipients, _reference(activity))
        return "follow"
    if kind == "Undo":
        follow = _follow_stub(activity)
        if follow["actor"] != actor:
            raise ContractError("actor_mismatch", "object.actor")
        _must_get(recipients, follow["object"])
        return "undo_follow"
    if kind == "Accept":
        if isinstance(_need(activity, "object", ""), dict):
            follow = _follow_stub(activity)
            if follow["object"] != actor:
                raise ContractError("actor_mismatch", "object.object")
            _must_get(recipients, follow["actor"])
            return "accept_follow"
        _reference(activity)
        _same_server(_iri(activity, "result", ""), actor, "result")
        return "accept_offer"
    if kind == "Reject":
        _reference(activity)
        if _need(activity, REASON_CODE, "") not in REJECT_REASON_CODES:
            raise ContractError("invalid_reason_code", REASON_CODE)
        return "reject_offer"
    if kind == "Update":
        obj = _embedded(activity)
        try:
            object_kind = _object_kind(obj, "object")
        except ContractError as exc:
            raise ContractError("unsupported_object", "object.type") from exc
        if object_kind == "knowledge_base":
            _check_knowledge_base(obj, "object")
            if obj["id"] != actor:
                raise ContractError("actor_mismatch", "object.id")
            # The time of the change of the record: the receiver refuses an ``Update`` that is not newer than the record that it has.
            _time(obj, "updated", "object")
            return "update_knowledge_base"
        if object_kind == "document":
            _check_document(obj, "object", extraction_necessary=True)
            _same_server(obj["id"], actor, "object.id")
            return "update_document"
        raise ContractError("unsupported_object", "object.type")
    if kind == "Delete":
        return _tombstone(activity, actor)
    if kind == "Offer":
        obj = _embedded(activity)
        try:
            object_kind = _object_kind(obj, "object")
        except ContractError as exc:
            raise ContractError("unsupported_object", "object.type") from exc
        if object_kind != "contribution":
            raise ContractError("unsupported_object", "object.type")
        _check_contribution(obj, "object")
        if obj["attributedTo"] != actor:
            raise ContractError("actor_mismatch", "object.attributedTo")
        target = _iri(activity, "target", "")
        if obj["context"] != target:
            raise ContractError("target_mismatch", "target")
        _must_get(recipients, target)
        capability = _optional_iri(activity, CAPABILITY, "")
        if capability is not None:
            _same_server(capability, target, CAPABILITY)
        return "offer_contribution"
    if kind == GRANT:
        if _need(activity, "object", "") != ROLE_COLLABORATOR:
            raise ContractError("invalid_role", "object")
        if _iri(activity, "context", "") != actor:
            raise ContractError("actor_mismatch", "context")
        _must_get(recipients, _iri(activity, "target", ""))
        if _time(activity, "endTime", "") <= _time(activity, "published", ""):
            raise ContractError("invalid_time", "endTime")
        return "grant"
    if kind == REVOKE:
        _same_server(_reference(activity), actor, "object")
        return "revoke"
    raise ContractError("unsupported_type", "type")


# ── Builders: objects ────────────────────────────────────────────────────────


def with_context(obj: dict[str, Any]) -> dict[str, Any]:
    """Give a copy of an object with the ``@context`` array first: the form of a document that goes alone."""
    return {"@context": list(WIRE_CONTEXT), **{k: v for k, v in obj.items() if k != "@context"}}


def build_knowledge_base(kb: dict[str, Any], iris: dict[str, str]) -> dict[str, Any]:
    """Make the ``claire:KnowledgeBase`` of a shared knowledge base.

    ``kb``: ``label``, ``description``, ``knowledge_type``, ``documents``, ``entities``, ``relations`` (the three counts), ``signature``, ``accepts_automatic`` (a bool: the server accepts automatic contributions now, Section 12.3), and ``updated`` (seconds: the time of the last change of the label, the description, the knowledge type, or ``accepts_automatic`` that the followers got; ``None`` when no follower got the record yet: then the object has no ``updated``). The signature is ``None`` for a knowledge base that has no signature, else a dictionary with ``entities`` (rows ``[type, norm_name, weight]``), ``visible_documents``, and ``computed_at`` (seconds). The centroid of the embeddings does not go to other servers.
    ``iris``: ``id``, ``url`` (the page), ``inbox``, ``outbox``, ``followers``, and ``documents`` (the collection).
    """
    obj: dict[str, Any] = {
        "id": _need(iris, "id", "iris"),
        "type": ["Group", KNOWLEDGE_BASE],
        "name": _need(kb, "label", "kb"),
        "summary": _need(kb, "description", "kb"),
        "url": _need(iris, "url", "iris"),
        "inbox": _need(iris, "inbox", "iris"),
        "outbox": _need(iris, "outbox", "iris"),
        "followers": _need(iris, "followers", "iris"),
        DOCUMENTS: _need(iris, "documents", "iris"),
        KNOWLEDGE_TYPE: _need(kb, "knowledge_type", "kb"),
        DOCUMENT_COUNT: _need(kb, "documents", "kb"),
        ENTITY_COUNT: _need(kb, "entities", "kb"),
        RELATION_COUNT: _need(kb, "relations", "kb"),
        ACCEPTS_AUTOMATIC: _need(kb, "accepts_automatic", "kb"),
    }
    if _need(kb, "updated", "kb") is not None:
        obj["updated"] = _iso(kb["updated"], "kb.updated")
    signature = _need(kb, "signature", "kb")
    if signature is not None:
        signature = _mapping(signature, "kb.signature")
        obj[SIGNATURE] = {
            "entities": [list(row) for row in _need(signature, "entities", "kb.signature")],
            "visible_documents": _need(signature, "visible_documents", "kb.signature"),
            "computed_at": _iso(_need(signature, "computed_at", "kb.signature"), "kb.signature.computed_at"),
        }
    _validate_object(obj, "")
    return obj


def build_extraction(result: dict[str, Any], authority_ids: dict[str, list[str]]) -> dict[str, Any]:
    """Make the ``claire:Extraction`` of one document.

    ``result``: the stored extraction (the JSON of ``extractions.raw_response``): ``summary``, ``key_claims``, ``entities`` (``name``, ``type``, ``aliases``, ``observations``, and ``proposed_type`` that can be absent or null), and ``relations`` (``source``, ``target``, ``type``, ``proposed_type``). The builder adds ``norm_name`` with the rule of this server (``normalize_name``).
    ``authority_ids``: for the name of an entity, its identifiers at authorities (decision KD7). An entity that has no identifier is not in the dictionary and gets no ``sameAs``.
    """
    entities = []
    for index, entity in enumerate(_need(result, "entities", "result")):
        at = f"result.entities.{index}"
        entity = _mapping(entity, at)
        name = _need(entity, "name", at)
        if not isinstance(name, str):
            raise ContractError("invalid_field", _join(at, "name"))
        row: dict[str, Any] = {"name": name, "type": _need(entity, "type", at), "norm_name": normalize_name(name), "aliases": list(_need(entity, "aliases", at)), "observations": list(_need(entity, "observations", at))}
        if name in authority_ids:
            row["sameAs"] = list(authority_ids[name])
        if entity.get("proposed_type") is not None:
            row["proposed_type"] = entity["proposed_type"]
        entities.append(row)
    relations = []
    for index, relation in enumerate(_need(result, "relations", "result")):
        at = f"result.relations.{index}"
        relation = _mapping(relation, at)
        row = {key: _need(relation, key, at) for key in ("source", "target", "type")}
        if relation.get("proposed_type") is not None:
            row["proposed_type"] = relation["proposed_type"]
        relations.append(row)
    obj = {"type": ["Object", EXTRACTION], "summary": _need(result, "summary", "result"), KEY_CLAIMS: list(_need(result, "key_claims", "result")), ENTITIES: entities, RELATIONS: relations}
    _validate_object(obj, "")
    return obj


def build_document(doc: dict[str, Any], iris: dict[str, str | None], extraction: dict[str, Any] | None) -> dict[str, Any]:
    """Make the ``Article`` of one document.

    ``doc``: ``title``, ``content_html`` (the HTML that the caller made clean), ``canonical_url`` (``None`` or an empty text for a document that has no URL: then the object has no ``url``), ``content_hash``, ``published`` (seconds), and ``updated`` (seconds, or ``None``).
    ``iris``: ``id``; ``attributed_to`` (the actor with the credit, or ``None`` for a deleted account); ``origin`` (the object that this document was copied from, or ``None`` for a document that is not a copy); ``root`` (the root document; for a root document, its own ``id``).
    ``extraction``: the result of ``build_extraction``, or ``None``. An ``Update`` and a contribution must have it.
    """
    obj: dict[str, Any] = {"id": _need(iris, "id", "iris"), "type": "Article", "name": _need(doc, "title", "doc"), "content": _need(doc, "content_html", "doc")}
    if _need(doc, "canonical_url", "doc"):
        obj["url"] = doc["canonical_url"]
    if _need(iris, "attributed_to", "iris") is not None:
        obj["attributedTo"] = iris["attributed_to"]
    if _need(iris, "origin", "iris") is not None:
        obj["origin"] = iris["origin"]
    obj[ROOT] = _need(iris, "root", "iris")
    obj[CONTENT_HASH] = _need(doc, "content_hash", "doc")
    obj["published"] = _iso(_need(doc, "published", "doc"), "doc.published")
    if _need(doc, "updated", "doc") is not None:
        obj["updated"] = _iso(doc["updated"], "doc.updated")
    if extraction is not None:
        obj[EXTRACTION_OF] = extraction
    _validate_object(obj, "")
    return obj


def build_contribution(*, document: dict[str, Any], attributed_to: str, knowledge_base: str, contribution_id: str | None, automatic: bool) -> dict[str, Any]:
    """Make a ``claire:Contribution``: ``document`` (the result of ``build_document``, with its extraction) for the shared knowledge base ``knowledge_base``.

    ``attributed_to`` is the account actor that contributes; the document must have the same actor in ``attributedTo``. ``contribution_id`` is ``None`` in an offer (the target gives the IRI in ``result``); the target gives its own IRI when it serves the contribution.
    ``automatic``: the automatic rule of the account selected the document (Section 12.3). Only then the contribution has ``claire:automatic`` (``true``).
    """
    obj: dict[str, Any] = {} if contribution_id is None else {"id": contribution_id}
    obj.update({"type": ["Object", CONTRIBUTION], "attributedTo": attributed_to, "context": knowledge_base, DOCUMENT: document})
    if automatic:
        obj[AUTOMATIC] = True
    _validate_object(obj, "")
    return obj


# ── Builders: activities ─────────────────────────────────────────────────────


def _activity(fields: dict[str, Any]) -> dict[str, Any]:
    activity = {"@context": list(WIRE_CONTEXT), **fields}
    validate_activity(activity)
    return activity


def _stub(follow: dict[str, Any]) -> dict[str, Any]:
    return {"id": _need(follow, "id", "follow"), "type": "Follow", "actor": _need(follow, "actor", "follow"), "object": _need(follow, "object", "follow")}


def build_follow(*, activity_id: str, server_actor: str, knowledge_base: str) -> dict[str, Any]:
    """Make the ``Follow`` of a knowledge base actor by the server actor of a holder or of a server that has the knowledge base in a theme (Section 9.2 rule 2)."""
    return _activity({"id": activity_id, "type": "Follow", "actor": server_actor, "object": knowledge_base, "to": [knowledge_base]})


def build_accept_follow(*, activity_id: str, follow: dict[str, Any]) -> dict[str, Any]:
    """Make the ``Accept`` of a ``Follow`` by the knowledge base actor. ``follow`` is the activity that came."""
    stub = _stub(follow)
    return _activity({"id": activity_id, "type": "Accept", "actor": stub["object"], "object": stub, "to": [stub["actor"]]})


def build_undo_follow(*, activity_id: str, follow: dict[str, Any]) -> dict[str, Any]:
    """Make the ``Undo`` of a ``Follow`` by the server actor. ``follow`` is the activity that was sent."""
    stub = _stub(follow)
    return _activity({"id": activity_id, "type": "Undo", "actor": stub["actor"], "object": stub, "to": [stub["object"]]})


def build_update_knowledge_base(*, activity_id: str, knowledge_base: dict[str, Any]) -> dict[str, Any]:
    """Make the ``Update`` of the record of a shared knowledge base for its followers. ``knowledge_base`` is the result of ``build_knowledge_base`` with ``updated``: the receiver refuses an ``Update`` that is not newer than its record."""
    return _activity({"id": activity_id, "type": "Update", "actor": _need(knowledge_base, "id", "knowledge_base"), "object": knowledge_base, "to": [_need(knowledge_base, "followers", "knowledge_base")]})


def build_delete_knowledge_base(*, activity_id: str, knowledge_base: str, followers: str) -> dict[str, Any]:
    """Make the ``Delete`` of a shared knowledge base for its followers."""
    return _activity({"id": activity_id, "type": "Delete", "actor": knowledge_base, "object": {"id": knowledge_base, "type": "Tombstone", "formerType": KNOWLEDGE_BASE}, "to": [followers]})


def build_update_document(*, activity_id: str, knowledge_base: str, followers: str, document: dict[str, Any]) -> dict[str, Any]:
    """Make the ``Update`` of a document for the followers of its knowledge base: a revision (Section 10.2). ``document`` is the result of ``build_document`` with its extraction."""
    return _activity({"id": activity_id, "type": "Update", "actor": knowledge_base, "object": document, "to": [followers]})


def build_delete_document(*, activity_id: str, knowledge_base: str, followers: str, document: str, reason_code: str) -> dict[str, Any]:
    """Make the ``Delete`` of a document for the followers of its knowledge base: a removal at the origin, with the reason code of the reviewers (Section 10.3)."""
    return _activity({"id": activity_id, "type": "Delete", "actor": knowledge_base, "object": {"id": document, "type": "Tombstone", "formerType": "Article"}, REASON_CODE: reason_code, "to": [followers]})


def build_delete_rendition(*, activity_id: str, account_actor: str, rendition: str, template: str, readers: list[str]) -> dict[str, Any]:
    """Make the ``Delete`` of a rendition object for the server actors that read its body (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.4 rule 4). The object of a document is its 대표 정리본, with the template ``standard``. The activity has the context of version 2."""
    activity = {"@context": list(WIRE_CONTEXT_V2), "id": activity_id, "type": "Delete", "actor": account_actor, "object": {"id": rendition, "type": "Tombstone", "formerType": "Article", TEMPLATE: template}, "to": list(readers)}
    validate_activity(activity)
    return activity


def build_offer(*, activity_id: str, contribution: dict[str, Any], capability: str | None) -> dict[str, Any]:
    """Make the ``Offer`` of a contribution by an account actor to a knowledge base actor (Section 12.1). ``contribution`` is the result of ``build_contribution``: it gives the actor and the target. ``capability`` is the IRI of the grant of a remote collaborator, or ``None`` for an account that has no grant (Section 12.2 rule 2)."""
    target = _need(contribution, "context", "contribution")
    fields = {"id": activity_id, "type": "Offer", "actor": _need(contribution, "attributedTo", "contribution"), "object": contribution, "target": target, "to": [target]}
    if capability is not None:
        fields[CAPABILITY] = capability
    return _activity(fields)


def build_accept_offer(*, activity_id: str, knowledge_base: str, offer: str, offer_actor: str, result: str) -> dict[str, Any]:
    """Make the ``Accept`` of an offer by the knowledge base actor. ``offer`` is the IRI of the ``Offer``; ``result`` is the IRI of the contribution on the server of the knowledge base."""
    return _activity({"id": activity_id, "type": "Accept", "actor": knowledge_base, "object": offer, "result": result, "to": [offer_actor]})


def build_reject_offer(*, activity_id: str, knowledge_base: str, offer: str, offer_actor: str, reason_code: str) -> dict[str, Any]:
    """Make the ``Reject`` of an offer by the knowledge base actor, with the reason code of the reviewers, or ``AUTOMATIC_NOT_ACCEPTED`` for an automatic contribution that the server does not accept now (Section 12.3)."""
    return _activity({"id": activity_id, "type": "Reject", "actor": knowledge_base, "object": offer, REASON_CODE: reason_code, "to": [offer_actor]})


def build_grant(*, activity_id: str, knowledge_base: str, account_actor: str, published: float, end_time: float) -> dict[str, Any]:
    """Make the ``Grant`` of the role ``claire:collaborator`` to an account of another server (Section 12.2 rule 1). The two times are seconds; the caller sets the end time. ``activity_id`` is the IRI that later offers have in ``capability``."""
    return _activity({"id": activity_id, "type": GRANT, "actor": knowledge_base, "context": knowledge_base, "target": account_actor, "object": ROLE_COLLABORATOR, "published": _iso(published, "published"), "endTime": _iso(end_time, "end_time"), "to": [account_actor]})


def build_revoke(*, activity_id: str, knowledge_base: str, account_actor: str, grant: str) -> dict[str, Any]:
    """Make the ``Revoke`` of a grant (Section 12.2 rule 3). ``grant`` is the IRI of the ``Grant``."""
    return _activity({"id": activity_id, "type": REVOKE, "actor": knowledge_base, "object": grant, "to": [account_actor]})
