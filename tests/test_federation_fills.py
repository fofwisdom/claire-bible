"""Fill of recent posts after a follow (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.13, phase E10a, contract 1.24, client.db version 13).

The interface between Claire and the sidecar (each endpoint agrees with its contract fixture; the sidecar tests read the same files), the trigger (the ``Accept`` of a follow), and the rules of the store: which posts stay, the copy of a fill of another account, the time between two fills, and the limit of a domain.
"""

from __future__ import annotations

from claire.store import client_db
from claire.store import federation_fills as fills_store
from tests.test_federation_routes import NOW, _isolate, internal  # noqa: F401  (pytest fixtures)
from tests.typing_helpers import need

DAN = "https://remote.example/users/dan"
FRANK = "https://remote.example/users/frank"


def _client(internal):  # noqa: F811
    return client_db.connect_client_existing(client_db.client_db_path(internal.settings.db_file))


def _items(client, account_id: int, author: str) -> list[tuple[str, int, int]]:
    return [(str(r[0]), int(r[1]), int(r[2])) for r in client.execute("SELECT object_iri, in_timeline, filled FROM federation_timeline_items WHERE account_id=? AND author_iri=? ORDER BY object_iri", (account_id, author))]


def test_fill_endpoints_agree_with_the_fixtures_and_store_the_posts_out_of_the_home_stream(internal):  # noqa: F811
    client = _client(internal)
    try:
        assert need(fills_store.request_fill(client, internal.alice, DAN, now=NOW - 10.0)).status == "pending"
        client.commit()
    finally:
        client.close()
    internal.assert_fixture("fill_tasks")
    internal.assert_fixture("fill_objects")
    client = _client(internal)
    try:
        # A post of dan and his reply to his own post stay; his reply to zoe and the post of zoe do not. The items are not in the home stream.
        assert _items(client, internal.alice, DAN) == [(f"{DAN}/statuses/1", 0, 1), (f"{DAN}/statuses/2", 0, 1)]
        fill = need(fills_store.get_fill(client, 1))
        assert (fill.status, fill.stored) == ("finished", 2)
    finally:
        client.close()
    # A later call for a fill that does not wait has no answer.
    assert internal.call({"method": "POST", "path": "/internal/federation/v1/fill-objects", "body": {"task_id": 1, "objects": [], "done": True}}).status_code == 404


def test_the_accept_of_a_follow_asks_for_one_fill(internal):  # noqa: F811
    internal.assert_fixture("inbox_event_accept_follow")
    client = _client(internal)
    try:
        fill = fills_store.request_fill(client, internal.alice, FRANK, now=NOW + 60.0)
        # The Accept made the fill of frank; a second request in 24 hours makes none.
        assert fill is None
        assert [(r[0], r[1]) for r in client.execute("SELECT actor_iri, status FROM federation_fills WHERE account_id=?", (internal.alice,))] == [(FRANK, "pending")]
    finally:
        client.close()


def test_a_fill_of_another_account_in_the_last_hour_is_copied_and_old_posts_are_not_stored(internal):  # noqa: F811
    client = _client(internal)
    try:
        fill = need(fills_store.request_fill(client, internal.alice, DAN, now=NOW))
        post = {"object_iri": f"{DAN}/statuses/7", "url": None, "type": "Note", "author": {"iri": DAN, "name": "Dan", "handle": "@dan@remote.example"}, "name": None, "summary": None, "sensitive": False, "content_html": "<p>x</p>", "language": "en", "published_at": NOW - 100.0, "updated_at": None, "attachments": [], "context": None, "in_reply_to": None, "quote": None}
        old = {**post, "object_iri": f"{DAN}/statuses/6", "published_at": NOW - 40 * 86400.0}
        assert fills_store.store_objects(client, fill, [post, old], followed={DAN}, retention_days=30, now=NOW) == 1
        fills_store.finish(client, fill.id, now=NOW + 5.0)
        # A second account of this server follows dan 10 minutes later: it gets a copy and the sidecar reads nothing.
        other = internal.alice + 1000
        copied = need(fills_store.request_fill(client, other, DAN, now=NOW + 600.0))
        assert (copied.status, copied.stored) == ("copied", 1)
        assert _items(client, other, DAN) == [(f"{DAN}/statuses/7", 0, 1)]
        assert fills_store.lease(client, now=NOW + 601.0, limit=5, account_ids={internal.alice, other}) == []
    finally:
        client.close()


def test_a_domain_with_thirty_fills_in_one_hour_waits(internal):  # noqa: F811
    client = _client(internal)
    try:
        for n in range(fills_store.DOMAIN_HOURLY_LIMIT + 1):
            assert fills_store.request_fill(client, 1000 + n, f"https://busy.example/users/u{n}", now=NOW) is not None
        accounts = {1000 + n for n in range(fills_store.DOMAIN_HOURLY_LIMIT + 1)}
        assert len(fills_store.lease(client, now=NOW, limit=50, account_ids=accounts)) == fills_store.DOMAIN_HOURLY_LIMIT
        # One hour later the last fill gets its turn.
        assert [f.actor_iri for f in fills_store.lease(client, now=NOW + 3601.0, limit=50, account_ids=accounts) if f.actor_iri.endswith(f"u{fills_store.DOMAIN_HOURLY_LIMIT}")] == [f"https://busy.example/users/u{fills_store.DOMAIN_HOURLY_LIMIT}"]
    finally:
        client.close()
