"""Handlers of the inbound activities of unit KF3 (KNOWLEDGE_FEDERATION_DESIGN.md Sections 9.2, 9.3, 10.2, and 10.3).

``journal.process`` calls one handler for each activity kind, in a savepoint; a handler must not commit and must give the same result for the same activity. The journal did these checks before: the switch ``federation``, the domain block of the actor, the wire contract (``vocabulary.validate_activity``), and that the activity and its actor are on the server of the signer.

| Kind | Actor → recipient | Effect on this server |
| :--- | :--- | :--- |
| ``follow`` | Server actor of another server → a knowledge base actor of this server | The follower is stored; the ``Accept`` goes to the outbox |
| ``undo_follow`` | The same | The follower is deleted |
| ``accept_follow`` | A knowledge base actor of another server → the server actor of this server | The follow of this server is ``accepted`` |
| ``update_knowledge_base`` | A knowledge base actor that this server follows | The record of the remote knowledge base changes |
| ``delete_knowledge_base`` | The same | The record and its theme memberships are deleted; the copies stay; the follow ends |
| ``update_document`` | The same | Each copy of the document gets a revision without approval (Section 10.2) |
| ``delete_document`` | The same | Each copy is hidden at once and waits in the review queue (Section 10.3, KD1) |

A handler changes rows only. A revision makes the new copy, the revision row, and the merge job; the job builds the graph after the journal commits (the internal route and the replay route run the jobs that wait).

``install`` puts the handlers in ``journal.HANDLERS``. The import of this module does it; a test that replaced the table calls ``install`` again.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from .. import contributions
from . import access, actors, journal, outbox, remote_kbs, vocabulary

__all__ = ["HANDLERS", "install"]


def _local_kb(ctx: journal.HandlerContext, iri: str) -> int:
    """The number of the shared knowledge base of this server whose actor has the IRI. Refusal ``knowledge_base_not_found`` for each other IRI."""
    number = actors.local_number(iri, ctx.origin)
    if number is None or actors.shared_kb(ctx.conn, number) is None:
        raise journal.HandlerRefusal("knowledge_base_not_found")
    return number


def _server_actor_of(entry: journal.JournalEntry) -> str:
    """The actor of the activity when it is the server actor of its server (design Section 9.2 rule 5). Refusal ``not_a_server_actor`` for each other actor."""
    parts = urlsplit(entry.actor)
    if parts.path != access.SERVER_ACTOR_PATH or parts.query or parts.fragment:
        raise journal.HandlerRefusal("not_a_server_actor")
    return entry.actor


def _followed(ctx: journal.HandlerContext, entry: journal.JournalEntry) -> dict[str, Any]:
    """The follow of this server for the knowledge base actor that sent the activity. Refusal ``not_following``: this server takes a change only from an actor that it follows."""
    follow = outbox.follow_of(ctx.conn, entry.actor)
    if follow is None:
        raise journal.HandlerRefusal("not_following")
    return follow


def follow(ctx: journal.HandlerContext, entry: journal.JournalEntry, activity: dict[str, Any]) -> None:
    actor = _server_actor_of(entry)
    kb_iri = str(activity["object"])
    number = _local_kb(ctx, kb_iri)
    actors.add_follower(ctx.conn, number, actor_iri=actor, domain=entry.actor_domain, follow_iri=entry.activity_iri, now=ctx.now)
    # The record that this follower has is the one of now: a later change goes out as an ``Update``.
    obj = actors.knowledge_base_object(ctx.conn, number, origin=ctx.origin)
    assert obj is not None
    ctx.conn.execute("INSERT INTO kf_kb_actor_state (kb_number, record_digest, updated_at) VALUES (?, ?, ?) ON CONFLICT(kb_number) DO NOTHING", (number, actors.record_digest(obj), ctx.now))
    accept = vocabulary.build_accept_follow(activity_id=outbox.accept_activity_iri(kb_iri, entry.activity_iri), follow=activity)
    outbox.enqueue(ctx.conn, accept, signer=str(number), recipients=[actor], origin=ctx.origin, now=ctx.now)


def undo_follow(ctx: journal.HandlerContext, entry: journal.JournalEntry, activity: dict[str, Any]) -> None:
    actor = _server_actor_of(entry)
    number = actors.local_number(str(activity["object"]["object"]), ctx.origin)
    # An ``Undo`` for a follow that this server does not have changes nothing: the result is the one that the sender wants.
    if number is not None:
        actors.remove_follower(ctx.conn, number, actor)


def accept_follow(ctx: journal.HandlerContext, entry: journal.JournalEntry, activity: dict[str, Any]) -> None:
    stub = activity["object"]
    if stub["actor"] != f"{ctx.origin}{access.SERVER_ACTOR_PATH}" or not outbox.accept_follow(ctx.conn, entry.actor, follow_iri=str(stub["id"]), now=ctx.now):
        raise journal.HandlerRefusal("follow_not_found")


def update_knowledge_base(ctx: journal.HandlerContext, entry: journal.JournalEntry, activity: dict[str, Any]) -> None:
    _followed(ctx, entry)
    refusal = remote_kbs.apply_update(ctx.conn, activity["object"], domain=entry.actor_domain, origin=ctx.origin, now=ctx.now)
    if refusal is not None:
        raise journal.HandlerRefusal(refusal)


def delete_knowledge_base(ctx: journal.HandlerContext, entry: journal.JournalEntry, activity: dict[str, Any]) -> None:
    _followed(ctx, entry)
    remote_kbs.delete_record(ctx.conn, entry.actor)
    # The actor is gone: the follow ends without an ``Undo``. The copies of its documents stay, with their origin (design Section 10.1).
    outbox.drop_follow(ctx.conn, entry.actor)


def update_document(ctx: journal.HandlerContext, entry: journal.JournalEntry, activity: dict[str, Any]) -> None:
    _followed(ctx, entry)
    article = activity["object"]
    refusal = contributions.remote_article_refusal(article)
    if refusal is not None:
        raise journal.HandlerRefusal(refusal)
    contributions.start_remote_revisions(ctx.conn, source_kb_iri=entry.actor, article=article, now=ctx.now)


def delete_document(ctx: journal.HandlerContext, entry: journal.JournalEntry, activity: dict[str, Any]) -> None:
    _followed(ctx, entry)
    contributions.origin_removed(ctx.conn, source_kb_iri=entry.actor, source_iri=str(activity["object"]["id"]), reason=str(activity[vocabulary.REASON_CODE]), now=ctx.now)


HANDLERS: dict[str, journal.Handler] = {
    "follow": follow,
    "undo_follow": undo_follow,
    "accept_follow": accept_follow,
    "update_knowledge_base": update_knowledge_base,
    "delete_knowledge_base": delete_knowledge_base,
    "update_document": update_document,
    "delete_document": delete_document,
}


def install() -> None:
    """Put each handler of this unit in the dispatch table of the journal. A kind that has this handler does not change; a kind with another handler is an error (``journal.register_handler``)."""
    for kind, handler in HANDLERS.items():
        if journal.HANDLERS.get(kind) is not handler:
            journal.register_handler(kind, handler)


install()
