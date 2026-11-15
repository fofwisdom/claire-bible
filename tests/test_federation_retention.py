"""Retention run of the timeline items (FEDERATION_REMOTE_READING_DESIGN.md Section 10 rules 1, 2, and 4, unit FP4-10): the store function against ``client.db`` (I/O) and the command ``claire federation prune``."""

from __future__ import annotations

import json
from types import SimpleNamespace

from claire import cli, media_proxy
from claire.store import client_db
from claire.store import db as dbm
from claire.store import federation_client as client_store

DAY = 86400.0
NOW = 1000 * DAY


def _item(conn, account: int, iri: str, *, age_days: float, in_timeline: int = 1, starred: int = 0, linked: str | None = None, reply_to: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO federation_timeline_items (account_id, object_iri, object_type, author_iri, author_json, content_html, received_at, in_timeline, starred, linked_doc_id, in_reply_to_iri) VALUES (?, ?, 'Note', 'https://r.example/u', '{}', '<p>x</p>', ?, ?, ?, ?, ?)",
        (account, iri, NOW - age_days * DAY, in_timeline, starred, linked, reply_to),
    )
    return int(cur.lastrowid)


def test_retention_rules(tmp_path):
    conn = client_db.connect_client_db(tmp_path / "client.db")
    try:
        old = _item(conn, 1, "https://r.example/old", age_days=100)
        fresh = _item(conn, 1, "https://r.example/fresh", age_days=10)
        starred = _item(conn, 1, "https://r.example/starred", age_days=100, starred=1)
        ingested = _item(conn, 1, "https://r.example/ingested", age_days=100, linked="doc-1")
        # A chain of parents: the top two have ``in_timeline`` = 0 and stay because a fresh reply is below them. A parent of an old reply goes.
        top = _item(conn, 1, "https://r.example/top", age_days=200, in_timeline=0)
        middle = _item(conn, 1, "https://r.example/middle", age_days=150, in_timeline=0, reply_to="https://r.example/top")
        _item(conn, 1, "https://r.example/reply", age_days=5, reply_to="https://r.example/middle")
        lone_parent = _item(conn, 1, "https://r.example/lone", age_days=200, in_timeline=0)
        old_reply = _item(conn, 1, "https://r.example/old-reply", age_days=120, reply_to="https://r.example/lone")
        # An own note that is not deleted keeps its item.
        conn.execute("INSERT INTO federation_notes (public_id, account_id, content_text, content_html, published_at, updated_at) VALUES (77, 1, 'mine', '<p>mine</p>', 1, 1)")
        own = _item(conn, 1, "https://claire.example/ap/objects/77", age_days=300)
        # The second account has a shorter retention of its own; a value above the instance value has no effect.
        conn.execute("INSERT INTO federation_account_settings (account_id, retention_days, updated_at) VALUES (2, 7, 1), (3, 3650, 1)")
        short = _item(conn, 2, "https://r.example/a2", age_days=10)
        long = _item(conn, 3, "https://r.example/a3", age_days=100)
        conn.commit()

        expired = client_store.expired_item_ids(conn, instance_days=90, now=NOW)
        assert expired == sorted([old, lone_parent, old_reply, short, long])
        assert not {fresh, starred, ingested, top, middle, own} & set(expired)
        # Without ``apply`` nothing changes; with it the items go, and a second run finds nothing.
        assert client_store.retention_run(conn, instance_days=90, now=NOW, apply=False) == {"retention_days": 90, "expired_items": 5}
        assert conn.execute("SELECT COUNT(*) FROM federation_timeline_items").fetchone()[0] == 12
        assert client_store.retention_run(conn, instance_days=90, now=NOW, apply=True)["expired_items"] == 5
        assert conn.execute("SELECT COUNT(*) FROM federation_timeline_items").fetchone()[0] == 7
        assert client_store.retention_run(conn, instance_days=90, now=NOW, apply=True)["expired_items"] == 0
    finally:
        conn.close()


def test_prune_command_counts_and_deletes_the_old_items(tmp_path, monkeypatch, capsys):
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    conn.close()
    client = client_db.connect_client_db(client_db.client_db_path(db_file))
    try:
        client.execute("INSERT INTO federation_timeline_items (account_id, object_iri, object_type, author_iri, author_json, content_html, received_at) VALUES (1, 'https://r.example/1', 'Note', 'https://r.example/u', '{}', '<p>x</p>', 1), (1, 'https://r.example/2', 'Note', 'https://r.example/u', '{}', '<p>x</p>', 4102444800)")
        client.commit()
    finally:
        client.close()
    settings = SimpleNamespace(db_file=db_file, data_dir=tmp_path, secret_service=None)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    assert media_proxy.run_retention(settings, apply=False) == {"retention_days": 90, "expired_items": 1}

    def _prune(*options: str) -> dict:
        args = cli.build_parser().parse_args(["federation", "prune", "--json", *options])
        assert args.func(args) == 0
        return json.loads(capsys.readouterr().out)

    assert _prune()["expired_items"] == 1 and len(_rows(db_file)) == 2
    done = _prune("--apply")
    assert done["applied"] is True and done["expired_items"] == 1 and _rows(db_file) == ["https://r.example/2"]


def _rows(db_file) -> list[str]:
    client = client_db.connect_client_existing(client_db.client_db_path(db_file), readonly=True)
    try:
        return [row[0] for row in client.execute("SELECT object_iri FROM federation_timeline_items ORDER BY id")]
    finally:
        client.close()
