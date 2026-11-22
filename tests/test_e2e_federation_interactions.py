"""End-to-end test of the inbound interactions with an account on the Claire side (FEDERATION_REMOTE_READING_DESIGN.md Sections 6.2 rule 4 and 6.6 rules 1 to 4 and 6, unit FP4-8).

A real app in network mode and the signed internal API of the same files: the sidecar gives timeline events and inbox events, and Claire makes the mention items, counts the reactions to a published document, and decides the automatic notice for a non-public message. The sidecar side of the same contract (the mention in the object, the reactions as inbox events, and the delivery of the notice) is in ``sidecar/fedify/tests/timeline_test.ts``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from claire.api import server
from claire.i18n import catalog
from claire.store import accounts as accounts_store
from claire.store import client_db
from tests.test_e2e_federation_following import _client_rows, _Sidecar
from tests.test_e2e_federation_timeline import CAROL, STRANGER, _actor, _event, _object
from tests.test_federation_console import ALPHA_PUBLIC_ID, BETA_PUBLIC_ID, PUBLIC_URL, _Client, _db, _isolate, _prepare, _session  # noqa: F401  (_isolate is an autouse fixture)

ALPHA_ACTOR = f"{PUBLIC_URL}/ap/actors/{ALPHA_PUBLIC_ID}"
BETA_ACTOR = f"{PUBLIC_URL}/ap/actors/{BETA_PUBLIC_ID}"
OBJECT_ID = 816412345678901234
OBJECT = f"{PUBLIC_URL}/ap/objects/{OBJECT_ID}"
ZED = STRANGER
YURI = "https://other.example/users/yuri"
WALT = "https://third.example/users/walt"
TIMELINE = "/api/federation/timeline"
OBJECTS = "/api/federation/objects"


@pytest.fixture
def env(tmp_path):
    settings, alpha, beta = _prepare(tmp_path)
    _db(settings, lambda conn: conn.execute("UPDATE accounts SET profile_public=1 WHERE id IN (?, ?)", (alpha, beta)))
    # Alpha has one published document in a public theme.
    _db(settings, lambda conn: conn.execute("INSERT INTO knowledge_bases (id, seq, label, is_personal, is_public, created_at, updated_at, account_id, number) VALUES (11, 11, 'alpha theme', 1, 1, 1, 1, ?, 516412345678901234)", (alpha,)))
    _db(settings, lambda conn: conn.execute("INSERT INTO documents (id, kb_id, title, detail, detail_format, hidden, fetched_at) VALUES ('doc-published', 11, 'Published', 'body', 'md', 0, 1)"))
    _db(settings, lambda conn: conn.execute("INSERT INTO federation_objects (public_id, account_id, kb_id, document_id, published_by_account_id, published_at, updated_at) VALUES (?, ?, 11, 'doc-published', ?, 1800000000.0, 1800000000.0)", (OBJECT_ID, alpha, alpha)))
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    with TestClient(app, base_url=PUBLIC_URL) as tc:
        # Alpha follows carol. No account follows the other remote actors.
        client = client_db.connect_client_existing(client_db.client_db_path(settings.db_file))
        try:
            client.execute("INSERT INTO federation_following (account_id, request, actor_iri, state, created_at, updated_at) VALUES (?, ?, ?, 'accepted', 1, 1)", (alpha, CAROL, CAROL))
            client.commit()
        finally:
            client.close()
        yield SimpleNamespace(client=_Client(tc), sidecar=_Sidecar(settings), settings=settings, alpha=alpha, beta=beta, admin=_session(settings, 1), alpha_session=_session(settings, alpha), beta_session=_session(settings, beta))


def _timeline(env, event: dict) -> dict:
    response = env.sidecar.call("POST", "/timeline-events", event)
    assert response.status_code == 200, response.text
    return response.json()


def _inbox(env, type_: str, actor: str, object_iri: str, *, object_type: str | None = None, n: list[int] = [0]) -> dict:
    n[0] += 1
    response = env.sidecar.call("POST", "/inbox-events", {"event_id": f"{actor}#reactions/{n[0]}", "type": type_, "object_type": object_type, "recipient_public_id": None, "actor": _actor(actor), "object_iri": object_iri, "verified_by": "http-signature"})
    assert response.status_code == 200, response.text
    return response.json()


def _mentions(env, where: str = "1=1", params: tuple = ()) -> list[tuple]:
    return _client_rows(env.settings, f"SELECT account_id, object_iri, mention, in_timeline FROM federation_timeline_items WHERE {where} ORDER BY id", params)


def _counters(env) -> tuple[int, int]:
    response = env.client.request("GET", OBJECTS, session=env.alpha_session)
    assert response.status_code == 200, response.text
    [item] = response.json()["items"]
    assert item["id"] == str(OBJECT_ID)
    return item["likes"], item["announces"]


def test_e2e_mentions_and_replies_of_actors_that_are_not_followed(env):
    c = env.client
    # Completion criterion: a public reply of an actor that is not followed to a published document makes one item with mention = 1.
    reply = f"{ZED}/notes/1"
    assert _timeline(env, _event("Create", ZED, _object(reply, ZED, in_reply_to=OBJECT))) == {"decision": "accepted", "reason": None, "items": 1, "notices": []}
    assert _mentions(env) == [(env.alpha, reply, 1, 1)]
    # The same event again makes no second item.
    assert _timeline(env, _event("Create", ZED, _object(reply, ZED, in_reply_to=OBJECT)))["items"] == 0
    assert len(_mentions(env)) == 1
    # A public mention of an account makes one item for that account only.
    mention = f"{ZED}/notes/2"
    assert _timeline(env, _event("Create", ZED, _object(mention, ZED), mentioned=[BETA_ACTOR]))["items"] == 1
    assert _mentions(env, "object_iri=?", (mention,)) == [(env.beta, mention, 1, 1)]
    # A post of a followed actor that mentions the account is a mention too, and it shows in the timeline also when it is a reply to an unknown post.
    followed = f"{CAROL}/notes/1"
    assert _timeline(env, _event("Create", CAROL, _object(followed, CAROL, in_reply_to=f"{YURI}/notes/9"), mentioned=[ALPHA_ACTOR]))["items"] == 1
    assert _mentions(env, "object_iri=?", (followed,)) == [(env.alpha, followed, 1, 1)]
    # A post of a followed actor without a mention is not a mention.
    assert _timeline(env, _event("Create", CAROL, _object(f"{CAROL}/notes/2", CAROL)))["items"] == 1
    assert _mentions(env, "object_iri=?", (f"{CAROL}/notes/2",)) == [(env.alpha, f"{CAROL}/notes/2", 0, 1)]

    # The owner sees the mentions with the filter of the client, and the counts have them.
    page = c.request("GET", f"{TIMELINE}?mention=1", session=env.alpha_session).json()
    assert [(item["object_iri"], item["mention"]) for item in page["items"]] == [(followed, True), (reply, True)]
    assert (page["counts"]["mentions"], page["counts"]["unread_mentions"], page["counts"]["total"]) == (2, 2, 3)
    assert [item["object_iri"] for item in c.request("GET", f"{TIMELINE}?mention=1", session=env.beta_session).json()["items"]] == [mention]

    # An Announce makes no mention item for an account that does not follow the announcer.
    assert _timeline(env, _event("Announce", ZED, _object(f"{YURI}/notes/3", YURI), mentioned=[BETA_ACTOR]))["reason"] == "not_followed"
    # A mention that is not public is not stored (Section 6.6 rule 2).
    assert _timeline(env, _event("Create", ZED, None, object_iri=f"{ZED}/notes/4", public=False, actor_type="Service", mentioned=[BETA_ACTOR]))["reason"] == "not_public"
    # An account that is not an actor gets no item, and an actor of another server in the list is not an account.
    _db(env.settings, lambda conn: conn.execute("UPDATE accounts SET status='suspended' WHERE id=?", (env.beta,)))
    assert _timeline(env, _event("Create", ZED, _object(f"{ZED}/notes/5", ZED), mentioned=[BETA_ACTOR, CAROL, f"{PUBLIC_URL}/ap/actors/999"]))["reason"] == "not_followed"
    _db(env.settings, lambda conn: conn.execute("UPDATE accounts SET status='active' WHERE id=?", (env.beta,)))
    # An account with a private profile is an actor: it gets its mention (FEDERATION_GO_LIVE_DESIGN.md decision GD5).
    _db(env.settings, lambda conn: conn.execute("UPDATE accounts SET profile_public=0 WHERE id=?", (env.beta,)))
    assert _timeline(env, _event("Create", ZED, _object(f"{ZED}/notes/5", ZED), mentioned=[BETA_ACTOR]))["items"] == 1
    _db(env.settings, lambda conn: conn.execute("UPDATE accounts SET profile_public=1 WHERE id=?", (env.beta,)))
    # A domain block applies to a mention.
    c.request("POST", "/api/admin/federation/domain-blocks", session=env.admin, json={"domain": "blocked.example"})
    eve = "https://blocked.example/users/eve"
    assert _timeline(env, _event("Create", eve, _object("https://blocked.example/notes/1", eve, in_reply_to=OBJECT), mentioned=[ALPHA_ACTOR]))["reason"] == "blocked_domain"
    assert len(_mentions(env, "mention=1")) == 4
    # A list above the limit of the contract is refused.
    assert env.sidecar.call("POST", "/timeline-events", _event("Create", ZED, _object(f"{ZED}/notes/6", ZED), mentioned=[ALPHA_ACTOR] * 17)).status_code == 400


def test_e2e_mention_rate_limit_for_each_domain_and_account(env):
    assert env.client.request("PUT", "/api/admin/federation/reading", session=env.admin, json={"mention_rate_per_hour": 2}).status_code == 200
    for n in (1, 2):
        assert _timeline(env, _event("Create", ZED, _object(f"{ZED}/notes/{n}", ZED), mentioned=[ALPHA_ACTOR]))["items"] == 1
    # The third mention of the same domain in one hour is ignored, also from another actor of the domain.
    assert _timeline(env, _event("Create", YURI, _object(f"{YURI}/notes/1", YURI), mentioned=[ALPHA_ACTOR])) == {"decision": "ignored", "reason": "rate_limited", "items": 0, "notices": []}
    # The limit is for each account and each domain.
    assert _timeline(env, _event("Create", YURI, _object(f"{YURI}/notes/2", YURI), mentioned=[ALPHA_ACTOR, BETA_ACTOR])) == {"decision": "accepted", "reason": None, "items": 1, "notices": []}
    assert _timeline(env, _event("Create", WALT, _object(f"{WALT}/notes/1", WALT), mentioned=[ALPHA_ACTOR]))["items"] == 1
    # A followed actor has no limit.
    for n in (1, 2, 3):
        assert _timeline(env, _event("Create", CAROL, _object(f"{CAROL}/notes/{n}", CAROL), mentioned=[ALPHA_ACTOR]))["items"] == 1
    # After one hour the domain can mention the account again.
    client = client_db.connect_client_existing(client_db.client_db_path(env.settings.db_file))
    try:
        client.execute("UPDATE federation_timeline_items SET received_at=received_at-3700 WHERE author_iri=?", (ZED,))
        client.commit()
    finally:
        client.close()
    assert _timeline(env, _event("Create", YURI, _object(f"{YURI}/notes/3", YURI), mentioned=[ALPHA_ACTOR]))["items"] == 1


def test_e2e_like_and_announce_of_a_published_document_with_undo(env):
    assert _counters(env) == (0, 0)
    # A Like and an Announce increase the counters. Claire stores each actor one time: a repeated activity changes nothing.
    assert _inbox(env, "Like", ZED, OBJECT) == {"decision": "accepted", "reply": None, "reason": None}
    assert _inbox(env, "Like", ZED, OBJECT)["decision"] == "accepted"
    assert _inbox(env, "Like", YURI, OBJECT)["decision"] == "accepted"
    assert _inbox(env, "Announce", ZED, OBJECT)["decision"] == "accepted"
    assert _counters(env) == (2, 1)
    assert sorted(_client_rows(env.settings, "SELECT object_public_id, account_id, kind, actor_iri FROM federation_object_reactions")) == [(OBJECT_ID, env.alpha, "announce", ZED), (OBJECT_ID, env.alpha, "like", YURI), (OBJECT_ID, env.alpha, "like", ZED)]
    # These activities make no item.
    assert _client_rows(env.settings, "SELECT COUNT(*) FROM federation_timeline_items") == [(0,)]
    # An Undo of another actor changes nothing. Completion criterion: a Like and its Undo leave the counter at 0.
    assert _inbox(env, "Undo", WALT, OBJECT, object_type="Like")["decision"] == "accepted"
    assert _counters(env) == (2, 1)
    assert _inbox(env, "Undo", ZED, OBJECT, object_type="Like")["decision"] == "accepted"
    assert _inbox(env, "Undo", YURI, OBJECT, object_type="Like")["decision"] == "accepted"
    assert _inbox(env, "Undo", ZED, OBJECT, object_type="Announce")["decision"] == "accepted"
    assert _counters(env) == (0, 0)
    assert _inbox(env, "Undo", ZED, OBJECT, object_type="Like")["decision"] == "accepted"
    assert _counters(env) == (0, 0)

    # An object path without an object, and an object that is not on this server, are not counted.
    assert _inbox(env, "Like", ZED, f"{PUBLIC_URL}/ap/objects/1") == {"decision": "ignored", "reply": None, "reason": "no_object"}
    assert _inbox(env, "Like", ZED, f"{ZED}/notes/1")["reason"] == "unsupported_type"
    # A blocked domain is refused before the reaction.
    env.client.request("POST", "/api/admin/federation/domain-blocks", session=env.admin, json={"domain": "third.example"})
    assert _inbox(env, "Like", WALT, OBJECT)["reason"] == "blocked_domain"
    # An object that is not public now gets no new reaction, and an Undo still removes one.
    assert _inbox(env, "Like", ZED, OBJECT)["decision"] == "accepted"
    _db(env.settings, lambda conn: conn.execute("UPDATE knowledge_bases SET is_public=0 WHERE id=11"))
    assert _inbox(env, "Like", YURI, OBJECT)["reason"] == "no_object"
    assert _inbox(env, "Undo", ZED, OBJECT, object_type="Like")["decision"] == "accepted"
    assert _counters(env) == (0, 0)


def test_e2e_automatic_notice_for_a_non_public_message(env):
    def private(actor: str, n: int, **changes) -> dict:
        return _timeline(env, _event("Create", actor, None, object_iri=f"{actor}/notes/private-{n}", public=False, **{"mentioned": [ALPHA_ACTOR], **changes}))

    def rows() -> list[tuple]:
        return _client_rows(env.settings, "SELECT account_id, sender_iri, count FROM federation_auto_replies ORDER BY account_id, sender_iri")

    english = f"<p>{catalog.t('federation.auto_reply.text', locale='en')}</p>"
    korean = f"<p>{catalog.t('federation.auto_reply.text', locale='ko')}</p>"
    assert english != korean
    # The first non-public message of a sender gets one notice from the account, in the locale of the account. No item and no content is stored.
    assert private(ZED, 1) == {"decision": "ignored", "reason": "not_public", "items": 0, "notices": [{"actor_public_id": str(ALPHA_PUBLIC_ID), "content": english, "language": "en"}]}
    assert _client_rows(env.settings, "SELECT COUNT(*) FROM federation_timeline_items") == [(0,)]
    # The second message in auto_reply_hours gets no notice. Claire counts it.
    assert private(ZED, 2)["notices"] == []
    assert rows() == [(env.alpha, ZED, 2)]
    # Another sender gets its own notice. The account has the Korean locale now.
    _db(env.settings, lambda conn: accounts_store.AccountStore(conn).update_locale(env.alpha, "ko"))
    assert private(YURI, 1)["notices"] == [{"actor_public_id": str(ALPHA_PUBLIC_ID), "content": korean, "language": "ko"}]
    # A message to two accounts gets one notice from each account that is an actor. Beta has no locale: the notice is English.
    _db(env.settings, lambda conn: conn.execute("UPDATE accounts SET status='suspended' WHERE id=?", (env.beta,)))
    assert private(WALT, 1, mentioned=[ALPHA_ACTOR, BETA_ACTOR])["notices"] == [{"actor_public_id": str(ALPHA_PUBLIC_ID), "content": korean, "language": "ko"}]
    _db(env.settings, lambda conn: conn.execute("UPDATE accounts SET status='active' WHERE id=?", (env.beta,)))
    assert private(WALT, 2, mentioned=[ALPHA_ACTOR, BETA_ACTOR])["notices"] == [{"actor_public_id": str(BETA_PUBLIC_ID), "content": english, "language": "en"}]

    # Rules that stop a loop: no notice to a Service, an Application, or an actor of an unknown type, and no notice for a reply to an automatic notice.
    bot = "https://bots.example/users/bot"
    for actor_type in ("Service", "Application", None):
        assert private(bot, 1, actor_type=actor_type)["notices"] == []
    assert private(bot, 2, in_reply_to=f"{ALPHA_ACTOR}#auto-replies/0123456789abcdef0123456789abcdef")["notices"] == []
    assert not [row for row in rows() if row[1] == bot]
    # A message without an account in it, an Update, and a blocked domain get no notice.
    assert private(bot, 3, mentioned=[])["notices"] == []
    assert _timeline(env, _event("Update", bot, None, object_iri=f"{bot}/notes/private-4", public=False, mentioned=[ALPHA_ACTOR]))["notices"] == []
    env.client.request("POST", "/api/admin/federation/domain-blocks", session=env.admin, json={"domain": "bots.example"})
    assert private(bot, 5) == {"decision": "rejected", "reason": "blocked_domain", "items": 0, "notices": []}

    # The client shows the number of the messages only.
    assert env.client.request("GET", TIMELINE, session=env.alpha_session).json()["counts"]["private_messages"] == 5
    assert env.client.request("GET", TIMELINE, session=env.beta_session).json()["counts"]["private_messages"] == 1

    # After auto_reply_hours (the admin value) the sender gets a notice again.
    assert env.client.request("PUT", "/api/admin/federation/reading", session=env.admin, json={"auto_reply_hours": 2}).status_code == 200
    client = client_db.connect_client_existing(client_db.client_db_path(env.settings.db_file))
    try:
        client.execute("UPDATE federation_auto_replies SET last_sent_at=last_sent_at-3600 WHERE sender_iri=?", (ZED,))
        client.commit()
        assert private(ZED, 3)["notices"] == []
        client.execute("UPDATE federation_auto_replies SET last_sent_at=last_sent_at-3700 WHERE sender_iri=?", (ZED,))
        client.commit()
    finally:
        client.close()
    assert len(private(ZED, 4)["notices"]) == 1
    assert (env.alpha, ZED, 4) in rows()
