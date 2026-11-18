"""Handlers of the inbound activities of units KF3 and KF4 (KNOWLEDGE_FEDERATION_DESIGN.md Sections 9.2, 9.3, 10.2, 10.3, 12.1, and 12.2).

``journal.process`` calls one handler for each activity kind, in a savepoint; a handler must not commit and must give the same result for the same activity. The journal did these checks before: the switch ``federation``, the domain block of the actor, the wire contract (``vocabulary.validate_activity``), and that the activity and its actor are on the server of the signer.

| Kind | Actor → recipient | Effect on this server |
| :--- | :--- | :--- |
| ``follow`` | Server actor of another server → a knowledge base actor of this server | The follower is stored (inside the limits of followers); the ``Accept`` goes to the outbox; the server becomes known |
| ``undo_follow`` | The same | The follower is deleted |
| ``accept_follow`` | A knowledge base actor of another server → the server actor of this server | The follow of this server is ``accepted``; each open copy from that actor gets a read again (``remote_documents.request_rechecks``) |
| ``update_knowledge_base`` | A knowledge base actor that this server follows | The record of the remote knowledge base changes |
| ``delete_knowledge_base`` | The same | The record and its theme memberships are deleted; the copies stay; the follow ends |
| ``update_document`` | The same | Each copy of the document gets a revision without approval (Section 10.2), inside the limits of revisions and only for a newer version |
| ``delete_document`` | The same | Each copy is hidden at once and waits in the review queue (Section 10.3, KD1); an offer of this server that the actor accepted with that document gets the state ``removed`` |
| ``offer_contribution`` | An account actor of another server → a knowledge base actor of this server | A contribution with the remote credit: pending, or without approval with a grant in effect (Sections 12.1 and 12.2); an automatic contribution (``claire:automatic``) only while this server accepts automatic contributions (Section 12.3) |
| ``accept_offer``, ``reject_offer`` | A knowledge base actor of another server → an account actor of this server | The offer of this server gets the decision (Section 12.1 rule 3); a ``Reject`` of an automatic offer with ``automatic_not_accepted`` marks the records of that server (Section 12.3) |
| ``grant``, ``revoke`` | The same | The grant of the account is stored, or ends (Section 12.2) |

A handler changes rows only. A revision makes the new copy, the revision row, and the merge job; the job builds the graph after the journal commits (the internal route and the replay route run the jobs that wait).

``install`` puts the handlers in ``journal.HANDLERS``. The import of this module does it; a test that replaced the table calls ``install`` again.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from .. import contributions, reputation
from ..store import kb_collab
from . import access, actors, grants, journal, known_servers, offers, outbox, remote_documents, remote_kbs, vocabulary

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
    """A server actor follows a shared knowledge base of this server. Refusals: ``not_a_server_actor``, ``knowledge_base_not_found``, ``follower_limit`` (the knowledge base has ``kb_followers_limit`` followers), ``domain_follower_limit`` (the server actors of the registrable domain of the actor have ``remote_domain_followers_limit`` follows here, KS2). The server of the actor becomes a known server only when the follow is stored (design Section 6.2 rule 2)."""
    actor = _server_actor_of(entry)
    kb_iri = str(activity["object"])
    number = _local_kb(ctx, kb_iri)
    if actors.get_follower(ctx.conn, number, actor) is None:
        limits = contributions.read_limits(ctx.conn)
        if actors.follower_count(ctx.conn, number) >= limits["kb_followers_limit"]:
            raise journal.HandlerRefusal("follower_limit")
        if actors.follower_count_of_quota(ctx.conn, access.quota_domain(entry.actor_domain)) >= limits["remote_domain_followers_limit"]:
            raise journal.HandlerRefusal("domain_follower_limit")
    actors.add_follower(ctx.conn, number, actor_iri=actor, domain=entry.actor_domain, follow_iri=entry.activity_iri, now=ctx.now)
    known_servers.note_server(ctx.conn, entry.actor_domain, source="remote_curation", now=ctx.now)
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
    """The knowledge base actor accepted the follow of this server. Refusal ``follow_not_found``. A document that the origin removed or revised between the read of a curation and this follow sent nothing to this server: each copy from that actor is read again (``remote_documents.request_rechecks``)."""
    stub = activity["object"]
    if stub["actor"] != f"{ctx.origin}{access.SERVER_ACTOR_PATH}" or not outbox.accept_follow(ctx.conn, entry.actor, follow_iri=str(stub["id"]), now=ctx.now):
        raise journal.HandlerRefusal("follow_not_found")
    remote_documents.request_rechecks(ctx.conn, kb_iri=entry.actor, domain=entry.actor_domain, now=ctx.now)


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
    """A revision from the origin (design Section 10.2). Refusals: ``not_following``; the limits of one document (KS2); ``invalid_time`` (the time of the revision is more than one day after now); ``revision_too_soon`` (a copy got a revision less than ``remote_revision_min_seconds`` ago); ``revision_budget`` (the knowledge base actors of the registrable domain gave ``remote_domain_daily_revisions`` revisions in 24 hours); ``stale_revision`` (each copy has a newer time of the origin: an old ``Update`` that came late)."""
    _followed(ctx, entry)
    article = activity["object"]
    refusal = contributions.remote_article_refusal(article) or contributions.remote_revision_refusal(ctx.conn, source_kb_iri=entry.actor, article=article, quota=access.quota_domain(entry.actor_domain), now=ctx.now)
    if refusal is not None:
        raise journal.HandlerRefusal(refusal)
    _jobs, refusal = contributions.start_remote_revisions(ctx.conn, source_kb_iri=entry.actor, article=article, now=ctx.now)
    if refusal is not None:
        raise journal.HandlerRefusal(refusal)


def delete_document(ctx: journal.HandlerContext, entry: journal.JournalEntry, activity: dict[str, Any]) -> None:
    _followed(ctx, entry)
    contributions.origin_removed(ctx.conn, source_kb_iri=entry.actor, source_iri=str(activity["object"]["id"]), reason=str(activity[vocabulary.REASON_CODE]), now=ctx.now)
    # The document can be the result of an offer of an account of this server: the submission view then shows the removal (the decision stays one of the other server, design Section 6.4).
    offers.result_removed(ctx.conn, kb_iri=entry.actor, result_iri=str(activity["object"]["id"]), reason_code=str(activity[vocabulary.REASON_CODE]), now=ctx.now)


def _for_recipient(entry: journal.JournalEntry, iri: str) -> None:
    """An activity that came to the inbox of one actor must be for that actor. The shared inbox has no owner. Refusal ``recipient_mismatch``."""
    if entry.recipient is not None and entry.recipient != iri:
        raise journal.HandlerRefusal("recipient_mismatch")


def offer_contribution(ctx: journal.HandlerContext, entry: journal.JournalEntry, activity: dict[str, Any]) -> None:
    """An account of another server offers a document to a shared knowledge base of this server (design Section 12.1 rules 2 to 5, Section 12.2 rule 2).

    Refusals (no contribution and no answer): ``knowledge_base_not_found``, ``recipient_mismatch``, ``not_own_document`` (the document is a copy, is not its own root, or is not on the server of the actor), the limits of one document (KS2), ``daily_limit`` (the offers of the domain in 24 hours, KS2).
    A decision of this server about the offer that needs no reviewer gets a ``Reject`` at once, and the row is ``processed``: an automatic contribution (``claire:automatic`` true) while this server accepts none (``automatic_not_accepted``: the switch ``kb_auto_contribution`` is off, Section 12.3 rule 1), an archived knowledge base (``other``), a document that the knowledge base has or removed (``duplicate``), and the pending limits for the remote account and for its domain (``other``; KS3).
    An automatic contribution has the origin ``auto`` on this server; the other rules are the same (Section 12.3 rule 3: this server applies its own pending limits for each remote account and each domain).
    Else the contribution is made. It is ``pending`` with a hidden copy and no graph until a reviewer decides. With a capability that is a grant of this server in effect for the actor and the knowledge base, and a reputation on this server that is not ``low``, it needs no approval: the merge job builds the graph after the journal commits. A capability that is not in effect (ended, revoked, or of another actor or knowledge base) is no reason for a refusal: the offer waits for approval as each other one.
    """
    target = str(activity["target"])
    number = _local_kb(ctx, target)
    _for_recipient(entry, target)
    actor = entry.actor
    article = activity["object"][vocabulary.DOCUMENT]
    if article.get("origin") is not None or article[vocabulary.ROOT] != article["id"] or access.remote_server_domain(str(article["id"]), ctx.origin) != entry.actor_domain:
        raise journal.HandlerRefusal("not_own_document")
    refusal = contributions.remote_article_refusal(article)
    if refusal is not None:
        raise journal.HandlerRefusal(refusal)
    if offers.inbound_of_iri(ctx.conn, entry.activity_iri) is not None:
        return  # a replay of an offer that has its row
    limits = contributions.read_limits(ctx.conn)
    quota = access.quota_domain(entry.actor_domain)
    if offers.daily_offers_of_domain(ctx.conn, quota, now=ctx.now) >= limits["remote_domain_daily_offers"]:
        raise journal.HandlerRefusal("daily_limit")
    capability = activity.get(vocabulary.CAPABILITY)
    kb = ctx.conn.execute("SELECT id, state FROM knowledge_bases WHERE number=?", (number,)).fetchone()
    kb_id = int(kb["id"])

    def _reject(reason_code: str, detail: str) -> None:
        offers.answer_refusal(ctx.conn, offer_iri=entry.activity_iri, actor_iri=actor, domain=entry.actor_domain, kb_number=number, capability_iri=capability, reason_code=reason_code, detail=detail, origin=ctx.origin, now=ctx.now)

    automatic = activity["object"].get(vocabulary.AUTOMATIC) is True
    if automatic and not access.auto_contribution_accepted(ctx.conn):
        return _reject(vocabulary.AUTOMATIC_NOT_ACCEPTED, "kb_auto_contribution_off")
    if kb["state"] != "active":
        return _reject("other", "kb_archived")
    grant_valid = capability is not None and grants.valid(ctx.conn, str(capability), kb_number=number, actor_iri=actor, now=ctx.now)
    settings = reputation.read_settings(ctx.conn)
    level, _rate, _count = reputation.compute_remote(ctx.conn, actor, now=ctx.now, settings=settings)
    # The rules of a collaborator (shared knowledge base design Sections 6.2 rule 2 and 8.6): no approval, unless the reputation on this server is ``low``.
    approved = grant_valid and not reputation.needs_approval(level)
    if not approved:
        account_limit = min(limits["remote_account_pending_limit"], reputation.pending_limit(settings, level))
        if kb_collab.pending_count_remote(ctx.conn, kb_id=kb_id, credit_iri=actor) >= account_limit:
            return _reject("other", "pending_limit")
        if kb_collab.count_pending_among(ctx.conn, offers.pending_contributions_of_domain(ctx.conn, quota)) >= limits["remote_domain_pending_limit"]:
            return _reject("other", "domain_pending_limit")
    try:
        row = contributions.contribute_remote(ctx.conn, kb_id=kb_id, article=article, actor_iri=actor, approved=approved, automatic=automatic, now=ctx.now)
    except contributions.ContributionError as exc:
        if exc.code not in ("contribution_duplicate", "contribution_tombstoned"):
            raise
        return _reject("duplicate", exc.code)
    offers.record_inbound(ctx.conn, offer_iri=entry.activity_iri, actor_iri=actor, domain=entry.actor_domain, kb_number=number, contribution_public_id=int(row["public_id"]), capability_iri=capability, grant_valid=grant_valid, now=ctx.now)
    # The server of the actor becomes a known server only when its offer made a contribution (design Section 6.2 rule 2).
    known_servers.note_server(ctx.conn, entry.actor_domain, source="remote_contribution", now=ctx.now)


def _offer_answer(ctx: journal.HandlerContext, entry: journal.JournalEntry, activity: dict[str, Any], *, accepted: bool) -> None:
    """The knowledge base actor of another server answered an offer of an account of this server. Only the actor that got the offer can answer it. Refusal ``offer_not_found``.

    A ``Reject`` of an automatic offer with ``automatic_not_accepted`` tells that the other server accepts no automatic contributions now (design Section 12.3 rule 1): each record of that server gets this state (``remote_kbs.note_automatic_not_accepted``), so that the next run of an automatic rule sends no offer there until a new read of the records."""
    reason = None if accepted else str(activity[vocabulary.REASON_CODE])
    found = offers.record_decision(
        ctx.conn, offer_iri=str(activity["object"]), kb_iri=entry.actor, accepted=accepted,
        result_iri=str(activity["result"]) if accepted else None, reason_code=reason, now=ctx.now,
    )
    if found is None:
        raise journal.HandlerRefusal("offer_not_found")
    if reason == vocabulary.AUTOMATIC_NOT_ACCEPTED and found["automatic"]:
        remote_kbs.note_automatic_not_accepted(ctx.conn, entry.actor_domain)


def accept_offer(ctx: journal.HandlerContext, entry: journal.JournalEntry, activity: dict[str, Any]) -> None:
    _offer_answer(ctx, entry, activity, accepted=True)


def reject_offer(ctx: journal.HandlerContext, entry: journal.JournalEntry, activity: dict[str, Any]) -> None:
    _offer_answer(ctx, entry, activity, accepted=False)


def grant(ctx: journal.HandlerContext, entry: journal.JournalEntry, activity: dict[str, Any]) -> None:
    """A knowledge base actor of another server gives an account of this server the role ``collaborator`` (design Section 12.2 rule 1). The grant is stored with its term; each later ``Offer`` of the account to that actor carries its IRI. Refusals: ``account_not_found`` (the target is not a federation actor of this server now), ``recipient_mismatch``, ``grant_limit`` (the account has too many grants from the domain, ``grants.RECEIVED_PER_DOMAIN_MAX``)."""
    target = str(activity["target"])
    account_id = actors.local_account_of_actor(ctx.conn, target, ctx.origin)
    if account_id is None:
        raise journal.HandlerRefusal("account_not_found")
    _for_recipient(entry, target)
    stored = grants.receive(
        ctx.conn, iri=entry.activity_iri, kb_iri=entry.actor, domain=entry.actor_domain, account_id=account_id,
        published_at=vocabulary.time_seconds(activity["published"]), end_time=vocabulary.time_seconds(activity["endTime"]), now=ctx.now,
    )
    if stored == "limit":
        raise journal.HandlerRefusal("grant_limit")


def revoke(ctx: journal.HandlerContext, entry: journal.JournalEntry, activity: dict[str, Any]) -> None:
    """The knowledge base actor ends a grant at once (design Section 12.2 rule 3). Only the actor that gave the grant can revoke it. Refusal ``grant_not_found``."""
    if not grants.receive_revoke(ctx.conn, iri=str(activity["object"]), kb_iri=entry.actor, now=ctx.now):
        raise journal.HandlerRefusal("grant_not_found")


HANDLERS: dict[str, journal.Handler] = {
    "follow": follow,
    "undo_follow": undo_follow,
    "accept_follow": accept_follow,
    "update_knowledge_base": update_knowledge_base,
    "delete_knowledge_base": delete_knowledge_base,
    "update_document": update_document,
    "delete_document": delete_document,
    "offer_contribution": offer_contribution,
    "accept_offer": accept_offer,
    "reject_offer": reject_offer,
    "grant": grant,
    "revoke": revoke,
}


def install() -> None:
    """Put each handler of this unit in the dispatch table of the journal. A kind that has this handler does not change; a kind with another handler is an error (``journal.register_handler``)."""
    for kind, handler in HANDLERS.items():
        if journal.HANDLERS.get(kind) is not handler:
            journal.register_handler(kind, handler)


install()
