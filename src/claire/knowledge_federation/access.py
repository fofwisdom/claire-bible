"""One-sided admin controls of the federation of shared knowledge (KNOWLEDGE_FEDERATION_DESIGN.md Sections 6.3 and 14, unit KF1).

Each function reads the controls of ``claire.db`` and gives a decision. No function changes data.

| Control | Rule here |
| :--- | :--- |
| Switch ``federation`` | Off: no exchange of shared knowledge (``exchange_refusal``). |
| Domain block | No exchange with that domain, for reads and for writes (``exchange_refusal``). |
| Switch ``kb_auto_contribution`` | Off: this server accepts no automatic contribution (``auto_contribution_accepted``). |
| Signed GET | A collection of shared knowledge answers only a signed request of a server actor (``collection_access``). |

The server mode is not a condition: the federation of shared knowledge works in single mode and in network mode (design Section 15 rule 5).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from urllib.parse import urlsplit

from ..store import accounts as accounts_store
from ..store import federation as federation_store

__all__ = [
    "SERVER_ACTOR_PATH",
    "CollectionAccess",
    "auto_contribution_accepted",
    "collection_access",
    "exchange_refusal",
    "federation_on",
    "remote_server_domain",
]

# The path of the server actor of each Claire server (FEDERATION.md Section 4.2).
SERVER_ACTOR_PATH = "/ap/actors/server"


def federation_on(conn: sqlite3.Connection) -> bool:
    """True when the switch ``federation`` is ``on``. No row is ``off``."""
    return accounts_store.AccountStore(conn).federation_state() == "on"


def auto_contribution_accepted(conn: sqlite3.Connection) -> bool:
    """True when this server accepts automatic contributions of other servers: the switches ``federation`` and ``kb_auto_contribution`` are ``on`` (design Section 12.3 rule 1)."""
    from .. import auto_contribution  # the import is here so that the store modules of this package do not load the contribution code

    return federation_on(conn) and auto_contribution.switch_on(conn)


def remote_server_domain(iri: str, origin: str) -> str | None:
    """Return the normalized domain of an address of another server, or ``None`` when the address is not permitted.

    The rule is the one of the inbox event contract (FEDERATION.md Section 5.7): the scheme is ``https``; ``http`` is permitted only when the canonical origin of this server uses ``http`` (development). An address with credentials, without a valid host, or on the origin of this server is not permitted.
    """
    try:
        parts = urlsplit(iri)
        credentials = parts.username is not None or parts.password is not None
    except ValueError:
        return None
    if parts.scheme != "https" and not (parts.scheme == "http" and origin.startswith("http://")):
        return None
    if credentials or f"{parts.scheme}://{parts.netloc}".lower() == origin.rstrip("/").lower():
        return None
    return federation_store.iri_host(iri)


def exchange_refusal(conn: sqlite3.Connection, *, actor_iri: str, origin: str) -> str | None:
    """Return the reason that stops an exchange with the server of ``actor_iri``, or ``None`` when the exchange is permitted.

    Reasons, in this sequence: ``federation_off``, ``invalid_actor``, ``blocked_domain``. The rule applies to reads and to writes (KS1).
    """
    if not federation_on(conn):
        return "federation_off"
    domain = remote_server_domain(actor_iri, origin)
    if domain is None:
        return "invalid_actor"
    if federation_store.blocking_domain(conn, domain) is not None:
        return "blocked_domain"
    return None


@dataclass(frozen=True, slots=True)
class CollectionAccess:
    """The decision for one read of a collection of shared knowledge. ``reason`` is ``None`` only when ``allowed`` is true; ``domain`` is the domain of the server that signed, when the address is valid."""

    allowed: bool
    reason: str | None
    domain: str | None


def collection_access(conn: sqlite3.Connection, *, signer: str | None, origin: str) -> CollectionAccess:
    """Decide if a request of another server can read a collection of shared knowledge (design Section 6.3 "Signed GET", KS1).

    ``signer`` is the IRI of the actor whose key the sidecar verified for the request, or ``None`` for a request without a valid signature. Claire does not verify a signature; the caller must be the internal federation API.

    Reasons of a refusal, in this sequence: ``federation_off``, ``unsigned``, ``invalid_actor``, ``not_a_server_actor`` (the signer is not the server actor of its server), ``blocked_domain``. Each refusal gets the same 404 at the HTTP level, so that a blocked server learns nothing.
    """
    if not federation_on(conn):
        return CollectionAccess(False, "federation_off", None)
    if signer is None:
        return CollectionAccess(False, "unsigned", None)
    domain = remote_server_domain(signer, origin)
    if domain is None:
        return CollectionAccess(False, "invalid_actor", None)
    parts = urlsplit(signer)
    if parts.path != SERVER_ACTOR_PATH or parts.query or parts.fragment:
        return CollectionAccess(False, "not_a_server_actor", domain)
    if federation_store.blocking_domain(conn, domain) is not None:
        return CollectionAccess(False, "blocked_domain", domain)
    return CollectionAccess(True, None, domain)
