"""Recent activity across servers for the root page of network mode (KNOWLEDGE_FEDERATION_DESIGN.md Section 8.3 rule 3).

The design gives three kinds. Each item has ``kind``, ``at`` (seconds), and ``server`` (the domain of the other server):

| ``kind`` | Source | Other keys |
| :--- | :--- | :--- |
| ``remote_knowledge_base`` | A new record in the store of remote knowledge bases (``remote_kbs``) | ``label``, ``url`` (its page on its server), ``knowledge_type`` |
| ``accepted_contribution`` | Another server approved an offer of an account of this server (``offers.recent_accepted``) | ``label`` and ``url`` of the knowledge base of the other server (None when this server has no record of it), ``account`` |
| ``curation`` | A reviewer of a shared knowledge base of this server copied documents of a shared knowledge base of another server (``kb_collab.recent_remote_curations``) | ``label`` and ``url`` of the source (None without a record), ``knowledge_base`` (the shared knowledge base of this server) |

Privacy rules (the data is public):

- ``account`` names an account when it is ``active`` and has a USERNAME: ``{"username", "display_name"}`` (FEDERATION_GO_LIVE_DESIGN.md decision GD8). Else ``account`` is None.
- No item has a document or its title: the document of an offer is a document of a personal knowledge base (KD3, KS5).
- An item of a domain that a block applies to now is not in the result.

No function here changes data.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from ...store import accounts as accounts_store
from ...store import federation as federation_store
from ...store import kb_collab
from . import offers, remote_kbs

__all__ = ["KINDS", "recent_activity"]

KINDS = ("remote_knowledge_base", "accepted_contribution", "curation")


def _public_account(conn: sqlite3.Connection, account_id: int | None) -> dict[str, Any] | None:
    """The name of an active account with a USERNAME, or None when the account is deleted or inactive."""
    if account_id is None:
        return None
    try:
        account = accounts_store.AccountStore(conn).get(int(account_id))
    except accounts_store.AccountNotFound:
        return None
    if account.status != "active" or not account.username:
        return None
    return {"username": account.username, "display_name": account.display_name}


def _record_link(conn: sqlite3.Connection, iri: str) -> tuple[str | None, str | None]:
    record = remote_kbs.get_by_iri(conn, iri)
    return (None, None) if record is None else (record.label, record.url)


def recent_activity(conn: sqlite3.Connection, *, local_kb: Callable[[int], dict[str, Any] | None], domain_of: Callable[[str], str | None], limit: int) -> list[dict[str, Any]]:
    """The newest items of the three kinds, the newest first, at most ``limit``.

    ``local_kb`` gives the public reference of a shared knowledge base of this server by its row ID (``{"number", "label", "url"}``), or None when it is not a public shared knowledge base now: then its curations are not in the result. ``domain_of`` gives the domain of an IRI of another server, or None.
    """
    items: list[dict[str, Any]] = []
    for kb in remote_kbs.list_all(conn)[:limit]:
        items.append({"kind": "remote_knowledge_base", "at": kb.first_seen_at, "server": kb.domain, "label": kb.label, "url": kb.url, "knowledge_type": kb.knowledge_type})
    for row in offers.recent_accepted(conn, limit=limit):
        label, url = _record_link(conn, row["kb_iri"])
        items.append({"kind": "accepted_contribution", "at": row["at"], "server": row["domain"], "label": label, "url": url, "account": _public_account(conn, row["account_id"])})
    for row in kb_collab.recent_remote_curations(conn, limit=limit):
        domain = domain_of(row["remote_source_kb_iri"])
        reference = local_kb(row["kb_id"])
        if domain is None or reference is None or federation_store.blocking_domain(conn, domain) is not None:
            continue
        label, url = _record_link(conn, row["remote_source_kb_iri"])
        items.append({"kind": "curation", "at": row["at"], "server": domain, "label": label, "url": url, "knowledge_base": reference})
    items.sort(key=lambda item: -float(item["at"]))
    return items[:limit]
