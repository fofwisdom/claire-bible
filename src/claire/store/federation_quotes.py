"""Store functions of quotes (FEP-044f; FEDERATION_REMOTE_READING_DESIGN.md Sections 5.2, 5.4 rules 5 and 6, 6.6 rule 5, and 7.1; unit FP4-9Q).

All tables are in ``client.db`` (``store.client_db``, version 6):

- Outbound: the quote columns of ``federation_notes``. A note with a quote starts as ``pending``. Only an activity of the author of the quoted object changes the state (``apply_quote_answer``, ``revoke_note_quotes``).
- Inbound: ``federation_quote_authorizations``, one row for each quoting object of a remote actor and own object. The row of an approved request is the ``QuoteAuthorization`` at ``/ap/quote-authorizations/{id}``.
- Tasks: the kinds ``quote_request``, ``quote_authorize``, ``quote_reject``, ``quote_revoke`` of ``federation_client_outbox``.

The quote policy of a published document is ``federation_objects.quote_policy`` in ``claire.db`` (version 24, ``store.federation``).

No function in this module commits. The caller owns the transaction.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal

from . import federation_client as client_store
from . import federation_notes as notes_store
from .client_db import QUOTE_POLICIES, QUOTE_STATES

__all__ = [
    "QUOTE_POLICIES",
    "QUOTE_STATES",
    "QUOTE_TASK_KINDS",
    "QuoteAuthorization",
    "add_authorization",
    "apply_quote_answer",
    "count_approved_quotes",
    "count_authorizations",
    "decide_request",
    "find_authorization",
    "get_authorization",
    "list_authorizations",
    "policy_state",
    "recent_request_actors",
    "remove_requests_of_object",
    "revoke_note_quotes",
    "set_authorization_state",
    "set_note_quote_policy",
]

QuoteState = Literal["pending", "approved", "refused", "revoked"]
# The kinds of the client outbox of this unit. The internal endpoint ``interaction-tasks`` gives them with the kinds of unit FP4-9.
QUOTE_TASK_KINDS: tuple[str, ...] = ("quote_request", "quote_authorize", "quote_reject", "quote_revoke")
# The task that tells the quoting actor the state of its request.
_STATE_TASK = {"approved": "quote_authorize", "refused": "quote_reject", "revoked": "quote_revoke"}
_COLUMNS = "id, account_id, object_public_id, quoting_object_iri, quoting_actor_iri, request_iri, state, created_at, updated_at"


@dataclass(frozen=True, slots=True)
class QuoteAuthorization:
    id: int
    account_id: int
    # The own object that is quoted: a note or a published document (one number space, RT7).
    object_public_id: int
    quoting_object_iri: str
    quoting_actor_iri: str
    request_iri: str
    state: QuoteState
    created_at: float
    updated_at: float


def _authorization(row: sqlite3.Row | tuple) -> QuoteAuthorization:
    v = tuple(row)
    return QuoteAuthorization(id=int(v[0]), account_id=int(v[1]), object_public_id=int(v[2]), quoting_object_iri=str(v[3]), quoting_actor_iri=str(v[4]), request_iri=str(v[5]), state=v[6], created_at=float(v[7]), updated_at=float(v[8]))


# --- Outbound: the quote of an own note (Section 5.4 rule 5) ----------------


def set_note_quote_policy(conn: sqlite3.Connection, public_id: int, policy: str, *, now: float) -> bool:
    """Set the quote policy of a note that is not deleted (Section 5.4 rule 6). Return ``True`` when the value changed. ``updated_at`` changes, and ``edited_at`` does not: the text is the same. The caller makes the ``update_note`` task."""
    if policy not in QUOTE_POLICIES:
        raise ValueError(f"unknown quote policy: {policy!r}")
    return conn.execute("UPDATE federation_notes SET quote_policy=?, updated_at=? WHERE public_id=? AND deleted_at IS NULL AND quote_policy!=?", (policy, now, public_id, policy)).rowcount == 1


def _note_changed(conn: sqlite3.Connection, note: notes_store.Note, *, now: float) -> None:
    # The Note document changed (the authorization came, or the quote went): the followers get Update(Note).
    client_store.add_client_task(conn, account_id=note.account_id, kind="update_note", target_iri=None, ref_id=note.public_id, now=now)


def apply_quote_answer(conn: sqlite3.Connection, public_id: int, *, author_iri: str, approved: bool, authorization_iri: str | None, now: float) -> Literal["changed", "same", "no_quote", "wrong_author"]:
    """Apply the ``Accept`` or the ``Reject`` of a quote request to the note (Section 5.4 rule 5). Only the author of the quoted object can answer. An approval needs the IRI of the ``QuoteAuthorization``. A change makes an ``update_note`` task."""
    note = notes_store.get_note(conn, public_id)
    if note is None or note.deleted_at is not None or note.quote_of_iri is None:
        return "no_quote"
    if note.quote_author_iri != author_iri:
        return "wrong_author"
    state = "approved" if approved else "refused"
    authorization = authorization_iri if approved else None
    if approved and authorization is None:
        raise ValueError("an approved quote needs the authorization IRI")
    if note.quote_state == state and note.quote_authorization_iri == authorization:
        return "same"
    conn.execute("UPDATE federation_notes SET quote_state=?, quote_authorization_iri=?, updated_at=? WHERE public_id=?", (state, authorization, now, public_id))
    _note_changed(conn, note, now=now)
    return "changed"


def revoke_note_quotes(conn: sqlite3.Connection, authorization_iri: str, author_iri: str, *, now: float) -> int:
    """The author of a quoted object deleted a ``QuoteAuthorization``: each approved quote with it becomes ``revoked`` (Section 5.4 rule 5). Return the number of notes. Each one gets an ``update_note`` task."""
    rows = conn.execute("SELECT public_id FROM federation_notes WHERE quote_authorization_iri=? AND quote_author_iri=? AND quote_state='approved' AND deleted_at IS NULL", (authorization_iri, author_iri)).fetchall()
    for row in rows:
        conn.execute("UPDATE federation_notes SET quote_state='revoked', quote_authorization_iri=NULL, updated_at=? WHERE public_id=?", (now, int(row[0])))
        note = notes_store.get_note(conn, int(row[0]))
        assert note is not None
        _note_changed(conn, note, now=now)
    return len(rows)


# --- Inbound: requests to quote an own object (Section 6.6 rule 5) ----------


def policy_state(policy: str, *, follower: bool) -> QuoteState:
    """The state that a quote policy gives to a new request (Section 5.4 rule 6)."""
    if policy == "public" or (policy == "followers" and follower):
        return "approved"
    return "pending" if policy == "manual" else "refused"


def get_authorization(conn: sqlite3.Connection, authorization_id: int) -> QuoteAuthorization | None:
    row = conn.execute(f"SELECT {_COLUMNS} FROM federation_quote_authorizations WHERE id=?", (authorization_id,)).fetchone()
    return None if row is None else _authorization(row)


def find_authorization(conn: sqlite3.Connection, object_public_id: int, quoting_object_iri: str) -> QuoteAuthorization | None:
    row = conn.execute(f"SELECT {_COLUMNS} FROM federation_quote_authorizations WHERE object_public_id=? AND quoting_object_iri=?", (object_public_id, quoting_object_iri)).fetchone()
    return None if row is None else _authorization(row)


def _state_task(conn: sqlite3.Connection, row: QuoteAuthorization, state: str, *, now: float) -> None:
    kind = _STATE_TASK.get(state)
    if kind is not None:
        client_store.add_client_task(conn, account_id=row.account_id, kind=kind, target_iri=row.quoting_actor_iri, ref_id=row.id, now=now)


def add_authorization(conn: sqlite3.Connection, *, account_id: int, object_public_id: int, quoting_object_iri: str, quoting_actor_iri: str, request_iri: str, state: QuoteState, now: float) -> QuoteAuthorization:
    """Store a new request with its first state, and make the task of the answer (``quote_authorize`` or ``quote_reject``). A ``pending`` request has no task: the owner decides."""
    cur = conn.execute(
        "INSERT INTO federation_quote_authorizations (account_id, object_public_id, quoting_object_iri, quoting_actor_iri, request_iri, state, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (account_id, object_public_id, quoting_object_iri, quoting_actor_iri, request_iri, state, now, now),
    )
    made = get_authorization(conn, int(cur.lastrowid or 0))
    assert made is not None
    _state_task(conn, made, state, now=now)
    return made


def decide_request(conn: sqlite3.Connection, *, account_id: int, object_public_id: int, quoting_object_iri: str, quoting_actor_iri: str, request_iri: str, policy: str, follower: bool, now: float) -> QuoteAuthorization | None:
    """Apply one ``QuoteRequest`` for an own object and return its row, or ``None`` when another actor has a request for the same quoting object.

    A new request gets the state of the policy. A repeated request keeps a decision that exists (``approved``, ``revoked``, and a request that waits) and gets the answer again. A ``refused`` request becomes ``approved`` when the policy of now approves it, so that a changed policy applies; it does not become ``pending`` again: the owner sees a refused request one time only.
    """
    found = find_authorization(conn, object_public_id, quoting_object_iri)
    if found is None:
        return add_authorization(conn, account_id=account_id, object_public_id=object_public_id, quoting_object_iri=quoting_object_iri, quoting_actor_iri=quoting_actor_iri, request_iri=request_iri, state=policy_state(policy, follower=follower), now=now)
    if found.quoting_actor_iri != quoting_actor_iri:
        return None
    state: str = found.state
    if found.state == "refused" and policy_state(policy, follower=follower) == "approved":
        state = "approved"
    conn.execute("UPDATE federation_quote_authorizations SET request_iri=?, state=?, updated_at=? WHERE id=?", (request_iri, state, now if state != found.state else found.updated_at, found.id))
    row = get_authorization(conn, found.id)
    assert row is not None
    # A revoked authorization answers a new request with a Reject, not with a second Delete.
    _state_task(conn, row, "refused" if state == "revoked" else state, now=now)
    return row


def set_authorization_state(conn: sqlite3.Connection, authorization_id: int, state: QuoteState, *, now: float) -> QuoteAuthorization | None:
    """The decision of the owner (Section 6.6 rule 5): approve or refuse a request that waits (a refused request can also be approved later), or revoke an authorization. Return the row, or ``None`` when the row is not in a state that permits the change. The change makes the task of the answer."""
    permitted = {"approved": ("pending", "refused"), "refused": ("pending",), "revoked": ("approved",)}.get(state)
    if permitted is None:
        raise ValueError(f"the owner cannot set the state {state!r}")
    marks = ",".join("?" for _ in permitted)
    if conn.execute(f"UPDATE federation_quote_authorizations SET state=?, updated_at=? WHERE id=? AND state IN ({marks})", (state, now, authorization_id, *permitted)).rowcount != 1:
        return None
    row = get_authorization(conn, authorization_id)
    assert row is not None
    _state_task(conn, row, state, now=now)
    return row


def list_authorizations(conn: sqlite3.Connection, account_id: int, *, states: tuple[str, ...], limit: int) -> list[QuoteAuthorization]:
    """The requests of the account in the given states, newest first."""
    if limit < 1 or not states:
        raise ValueError("limit must be 1 or more, and one state is necessary")
    marks = ",".join("?" for _ in states)
    return [_authorization(row) for row in conn.execute(f"SELECT {_COLUMNS} FROM federation_quote_authorizations WHERE account_id=? AND state IN ({marks}) ORDER BY id DESC LIMIT ?", (account_id, *states, limit)).fetchall()]


def count_authorizations(conn: sqlite3.Connection, account_id: int, state: QuoteState) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM federation_quote_authorizations WHERE account_id=? AND state=?", (account_id, state)).fetchone()[0])


def count_approved_quotes(conn: sqlite3.Connection, object_public_ids: list[int]) -> dict[int, int]:
    """The number of approved quotes of each given object. An object without one has 0."""
    counts = {public_id: 0 for public_id in object_public_ids}
    if not object_public_ids:
        return counts
    marks = ",".join("?" for _ in object_public_ids)
    for public_id, number in conn.execute(f"SELECT object_public_id, COUNT(*) FROM federation_quote_authorizations WHERE state='approved' AND object_public_id IN ({marks}) GROUP BY object_public_id", object_public_ids):
        counts[int(public_id)] = int(number)
    return counts


def recent_request_actors(conn: sqlite3.Connection, account_id: int, *, since: float) -> list[str]:
    """The quoting actors of the requests that the account got after ``since``, one entry for each request. The caller counts the requests of one remote domain for the rate limit."""
    return [str(r[0]) for r in conn.execute("SELECT quoting_actor_iri FROM federation_quote_authorizations WHERE account_id=? AND created_at>?", (account_id, since))]


def remove_requests_of_object(conn: sqlite3.Connection, quoting_object_iri: str, quoting_actor_iri: str) -> int:
    """The quoting actor deleted its object: its requests and authorizations go. Return the number of rows."""
    return conn.execute("DELETE FROM federation_quote_authorizations WHERE quoting_object_iri=? AND quoting_actor_iri=?", (quoting_object_iri, quoting_actor_iri)).rowcount
