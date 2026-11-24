"""Lifecycle, membership, roles, and moderation of a shared knowledge base (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 10; unit CT-3).

Completion criteria: join with ``open`` and ``approval``, approve, ban, leave, the last-moderator rule, give and remove ``collaborator`` with step-up, and the admin assignment of moderators.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from tests.kb_paths import step_up_headers

from claire.core import ids
from claire.api.server import create_app
from claire.core.config import Settings
from claire.store import accounts as accounts_store
from claire.store import auth_db
from claire.store import db as dbm
from claire.store import kb_collab
from claire.store.knowledge_base import KnowledgeBaseManager, validate_seed_entities
from claire.store.token_store import get_token_store, reset_token_stores

PUBLIC_URL = "http://127.0.0.1:8765"
OWNER_TOKEN = "owner-" + ("s" * 32)
ACCOUNTS = {"admin": 1, "ann": 2, "ben": 3, "cho": 4, "dee": 5}


@pytest.fixture(autouse=True)
def _fresh_token_stores():
    reset_token_stores()
    yield
    reset_token_stores()


@pytest.fixture
def world(tmp_path: Path):
    """A network-mode server: the admin, and the active accounts ann, ben, cho, dee. ann founds the shared knowledge base ``Cities``."""
    (tmp_path / "data").mkdir(parents=True)
    settings = Settings(
        CLAIRE_DB_PATH=str(tmp_path / "data" / "claire.db"),
        CLAIRE_VAULT_PATH=str(tmp_path / "vault"),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_FQDN=PUBLIC_URL,
        CLAIRE_INJECT_TOKEN=OWNER_TOKEN,
    )
    conn = dbm.connect(settings.db_file)
    try:
        dbm.init_db(conn)
        now = time.time()
        for username, account_id in ACCOUNTS.items():
            conn.execute(
                "INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, updated_at) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET username=excluded.username, role=excluded.role, status=excluded.status, display_name=excluded.display_name, profile_public=excluded.profile_public",
                (account_id, (1 << 57) + account_id, username, "admin" if account_id == 1 else "member", username.capitalize(), 0 if username == "dee" else 1, now, now),
            )
        accounts_store.AccountStore(conn).set_setting("mode", "network", updated_by=1)
        conn.commit()
    finally:
        conn.close()
    tokens = get_token_store(data_dir=settings.data_dir)
    bearer = {name: {"Authorization": f"Bearer {tokens.create_api_token(name=name, scope='owner', account_id=account_id)['token']}"} for name, account_id in ACCOUNTS.items()}
    public = {name: ids.format_public_id((1 << 57) + account_id) for name, account_id in ACCOUNTS.items()}
    with TestClient(create_app(settings=settings), base_url=PUBLIC_URL, follow_redirects=False) as client:
        made = client.post("/kb", json={"label": "Cities", "knowledge_type": "real", "seed_entities": ["Seoul", "Busan"]}, headers=bearer["ann"])
        assert made.status_code == 201, made.text
        kb = made.json()["knowledge_base"]

        def session(name: str, method: str, path: str, **kwargs):
            """One request with a session of ``name`` that has a recent sign-in (step-up passes)."""
            headers = step_up_headers(settings, account_id=ACCOUNTS[name])
            client.cookies.clear()
            response = client.request(method, path, headers=headers, **kwargs)
            client.cookies.clear()
            return response

        yield SimpleNamespace(settings=settings, client=client, bearer=bearer, public=public, kb=kb, base=f"/kb/{kb['number']}", session=session)


def _membership(world, name: str) -> tuple[str, str] | None:
    conn = dbm.connect_existing(world.settings.db_file, readonly=True)
    try:
        row = kb_collab.get_membership(conn, world.kb["id"], ACCOUNTS[name])
    finally:
        conn.close()
    return None if row is None else (row["role"], row["state"])


def _log(world) -> list[tuple[int | None, str, str]]:
    conn = dbm.connect_existing(world.settings.db_file, readonly=True)
    try:
        return [(r["actor_account_id"], r["action"], r["target"]) for r in kb_collab.list_moderation_log(conn, world.kb["id"])]
    finally:
        conn.close()


def _set(world, **values) -> None:
    response = world.client.patch(f"{world.base}/", json=values, headers=world.bearer["ann"])
    assert response.status_code == 200, response.text


# --- Section 10.1: lifecycle ----------------------------------------------------------------------------------------


def test_create_and_settings_of_a_shared_knowledge_base(world) -> None:
    client, kb = world.client, world.kb
    assert (kb["account_id"], kb["created_by_account_id"], kb["knowledge_type"], kb["join_policy"], kb["state"], kb["seed_entities"]) == (None, 2, "real", "approval", "active", ["Seoul", "Busan"])
    assert _membership(world, "ann") == ("moderator", "active")
    # The body of a create: the knowledge type is required; the join policy and the state are not for a create; at most 20 seed entities.
    for body, error in (
        ({"label": "x"}, "knowledge_type_required"),
        ({"label": "x", "knowledge_type": "real", "join_policy": "open"}, "join_policy_not_at_create"),
        ({"label": "x", "knowledge_type": "real", "seed_entities": [f"e{i}" for i in range(21)]}, "seed_entities_too_many"),
        ({"label": "x", "knowledge_type": "real", "seed_entities": "Seoul"}, "seed_entities_invalid"),
        ({"label": "x", "knowledge_type": "real", "is_public": False}, "a shared knowledge base is always public"),
        ({"label": 5, "knowledge_type": "real"}, "label_invalid"),
    ):
        refused = client.post("/kb", json=body, headers=world.bearer["ben"])
        assert (refused.status_code, refused.json()) == (400, {"error": error}), body
    # The old routes are gone.
    assert client.post("/themes", json={"label": "x", "knowledge_type": "real"}, headers=world.bearer["ben"]).status_code == 404
    assert client.patch("/themes", json={"id": kb["number"], "label": "x"}, headers=world.bearer["ann"]).status_code == 404

    # Settings: the capability ``settings`` (moderator or admin).
    assert client.patch(f"{world.base}/", json={"label": "x"}, headers=world.bearer["ben"]).json() == {"error": "kb_capability_required"}
    changed = client.patch(f"{world.base}/", json={"label": "World cities", "join_policy": "open", "seed_entities": ["Tokyo"], "knowledge_type": "fictional"}, headers=world.bearer["ann"])
    assert changed.status_code == 200, changed.text
    updated = changed.json()["knowledge_base"]
    assert (updated["label"], updated["join_policy"], updated["seed_entities"], updated["knowledge_type"]) == ("World cities", "open", ["Tokyo"], "fictional")
    for body, error in (({"join_policy": "closed"}, "join_policy_invalid"), ({"knowledge_type": "x"}, "knowledge_type_invalid"), ({"state": "gone"}, "state_invalid"), ({"is_public": False}, "a shared knowledge base is always public"), ({"is_public": "no"}, "is_public_invalid")):
        refused = client.patch(f"{world.base}/", json=body, headers=world.bearer["ann"])
        assert (refused.status_code, refused.json()) == (400, {"error": error}), body
    # A setting of a shared knowledge base is not for a personal one.
    manager = KnowledgeBaseManager(world.settings)
    manager.define_kb("Ann home", account_id=2, is_personal=True)
    personal = client.patch("/@ann/", json={"join_policy": "open"}, headers=world.bearer["ann"])
    assert (personal.status_code, personal.json()) == (400, {"error": "kb_shared_only"})
    assert client.patch("/@ann/", json={"label": "Ann notes"}, headers=world.bearer["ann"]).json()["knowledge_base"]["label"] == "Ann notes"
    assert client.patch("/@ann/", json={"label": "x"}, headers=world.bearer["ben"]).status_code == 404
    assert (2, "settings", "join_policy,knowledge_type,label,seed_entities") in _log(world)


def test_seed_entities_validation() -> None:
    assert validate_seed_entities([" Seoul ", "Busan", "Seoul"]) == ("Seoul", "Busan")
    for bad in (["", "x"], [1], "Seoul", ["x" * 201]):
        with pytest.raises(ValueError, match="seed_entities_invalid"):
            validate_seed_entities(bad)


def test_archived_knowledge_base_is_read_only(world) -> None:
    client = world.client
    _set(world, join_policy="open")
    assert client.post(f"{world.base}/membership", headers=world.bearer["ben"]).status_code == 201
    _set(world, state="archived")
    assert client.get(f"{world.base}/documents").status_code == 200
    # No join and no maintenance; a member can leave; the moderator can unarchive.
    assert client.post(f"{world.base}/membership", headers=world.bearer["cho"]).json() == {"error": "kb_archived"}
    assert client.post(f"{world.base}/dedup/merge", json={"keep": "a", "merge": "b"}, headers=world.bearer["ann"]).json() == {"error": "kb_archived"}
    assert client.delete(f"{world.base}/membership", headers=world.bearer["ben"]).status_code == 200
    _set(world, state="active")
    assert client.post(f"{world.base}/membership", headers=world.bearer["cho"]).status_code == 201


def test_delete_needs_the_admin_step_up_and_apply(world) -> None:
    client = world.client
    # A moderator has no ``delete``; the admin needs a session with a recent sign-in.
    assert world.session("ann", "DELETE", f"{world.base}/?apply=true").json() == {"error": "kb_capability_required"}
    assert client.delete(f"{world.base}/?apply=true", headers=world.bearer["admin"]).json() == {"error": "step_up_requires_session"}
    plan = world.session("admin", "DELETE", f"{world.base}/")
    assert (plan.status_code, plan.json()["applied"], plan.json()["would_delete"]["members"]) == (200, False, 1)
    assert world.session("admin", "DELETE", f"{world.base}/?apply=yes").json() == {"error": "invalid_apply"}
    assert client.get(f"{world.base}/documents").status_code == 200
    done = world.session("admin", "DELETE", f"{world.base}/?apply=true")
    assert (done.status_code, done.json()["applied"]) == (200, True)
    assert client.get(f"{world.base}/documents").status_code == 404
    auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(world.settings.data_dir))
    try:
        assert [(a["actor_account_id"], a["action"]) for a in auth_db.list_admin_audit(auth_conn)] == [(1, "kb_delete")]
    finally:
        auth_conn.close()


# --- Section 10.2: join, leave, roles -------------------------------------------------------------------------------


def test_join_with_approval_approve_and_leave(world) -> None:
    client, join = world.client, f"{world.base}/membership"
    # ``approval`` (the policy of a new knowledge base): a pending member.
    requested = client.post(join, headers=world.bearer["ben"])
    assert (requested.status_code, requested.json()["membership"]["state"], requested.json()["membership"]["role"]) == (201, "pending", "member")
    assert client.post(join, headers=world.bearer["ben"]).status_code == 200  # a second request changes nothing
    assert _membership(world, "ben") == ("member", "pending")
    # An anonymous request, a ``write`` token, and a personal knowledge base have no join.
    assert client.post(join).status_code == 404
    write = {"Authorization": f"Bearer {get_token_store(data_dir=world.settings.data_dir).create_api_token(name='w', scope='write', account_id=4)['token']}"}
    assert client.post(join, headers=write).json()["error"] == "scope_insufficient"

    # Each reader gets the active members; the pending entry goes only to an account with ``people``.
    public_view = client.get(f"{world.base}/members").json()
    assert [(m["account"], m["username"], m["role"], m["state"]) for m in public_view["members"]] == [(world.public["ann"], "ann", "moderator", "active")]
    people_view = client.get(f"{world.base}/members", headers=world.bearer["ann"]).json()["members"]
    assert sorted((m["username"], m["state"]) for m in people_view) == [("ann", "active"), ("ben", "pending")]
    assert [m["username"] for m in client.get(f"{world.base}/members", headers=world.bearer["ben"]).json()["members"]] == ["ann"]

    # Approve: only an account with ``people``; only a pending membership.
    member_url = f"{world.base}/members/{world.public['ben']}"
    assert client.patch(member_url, json={"action": "approve"}, headers=world.bearer["cho"]).json() == {"error": "kb_capability_required"}
    approved = client.patch(member_url, json={"action": "approve"}, headers=world.bearer["ann"])
    assert (approved.status_code, approved.json()["member"]["state"]) == (200, "active")
    assert client.patch(member_url, json={"action": "approve"}, headers=world.bearer["ann"]).json() == {"error": "not_pending"}
    assert _membership(world, "ben") == ("member", "active")

    # Leave; a pending member withdraws its request the same way.
    assert client.delete(join, headers=world.bearer["ben"]).status_code == 200
    assert _membership(world, "ben") is None
    assert client.delete(join, headers=world.bearer["ben"]).json() == {"error": "membership_not_found"}
    assert client.post(join, headers=world.bearer["cho"]).status_code == 201
    assert client.delete(join, headers=world.bearer["cho"]).status_code == 200
    assert (2, "approve", world.public["ben"]) in _log(world)


def test_join_with_open_ban_and_remove(world) -> None:
    client, join = world.client, f"{world.base}/membership"
    _set(world, join_policy="open")
    joined = client.post(join, headers=world.bearer["ben"])
    assert (joined.status_code, joined.json()["membership"]["state"]) == (201, "active")
    # Decision GD8: active accounts with a USERNAME show username in network-mode member lists regardless of ``profile_public``.
    assert client.post(join, headers=world.bearer["dee"]).status_code == 201
    listed = {m["account"]: m for m in client.get(f"{world.base}/members").json()["members"]}
    assert listed[world.public["dee"]]["username"] == "dee" and listed[world.public["dee"]]["display_name"] == "Dee"

    # Ban: no step-up (moderators must act fast). A banned account reads, cannot join again, and cannot leave.
    ben_url = f"{world.base}/members/{world.public['ben']}"
    banned = client.patch(ben_url, json={"action": "ban", "reason": "spam"}, headers=world.bearer["ann"])
    assert (banned.status_code, banned.json()["member"]["state"]) == (200, "banned")
    assert client.get(f"{world.base}/documents", headers=world.bearer["ben"]).status_code == 200
    for method in ("POST", "DELETE"):
        assert client.request(method, join, headers=world.bearer["ben"]).json() == {"error": "kb_capability_required"}, method
    assert world.public["ben"] not in {m["account"] for m in client.get(f"{world.base}/members").json()["members"]}
    # An account without a membership can be banned too.
    assert client.patch(f"{world.base}/members/{world.public['cho']}", json={"action": "ban"}, headers=world.bearer["ann"]).json()["member"]["state"] == "banned"

    # Remove deletes the membership (also a ban): the account can join again.
    removed = client.patch(ben_url, json={"action": "remove"}, headers=world.bearer["ann"])
    assert (removed.status_code, removed.json()["member"]) == (200, None)
    assert client.patch(ben_url, json={"action": "remove"}, headers=world.bearer["ann"]).json() == {"error": "membership_not_found"}
    assert client.post(join, headers=world.bearer["ben"]).status_code == 201

    # Bodies and targets that the route refuses.
    for body, error in (({}, "action_or_role_required"), ({"action": "ban", "role": "member"}, "action_or_role_required"), ({"action": "promote"}, "action_invalid"), ({"role": "moderator"}, "role_invalid"), ({"action": "ban", "reason": 5}, "reason_invalid")):
        refused = client.patch(ben_url, json=body, headers=world.bearer["ann"])
        assert (refused.status_code, refused.json()) == (400, {"error": error}), body
    assert client.patch(f"{world.base}/members/{world.public['ann']}", json={"action": "ban"}, headers=world.bearer["ann"]).json() == {"error": "self_not_allowed"}
    for unknown in ("1", "not-a-number", ids.format_public_id((1 << 57) + 99)):
        assert client.patch(f"{world.base}/members/{unknown}", json={"action": "ban"}, headers=world.bearer["ann"]).json() == {"error": "account_not_found"}
    assert [(a, t) for _actor, a, t in _log(world) if a in ("ban", "remove")] == [("ban", world.public["ben"]), ("ban", world.public["cho"]), ("remove", world.public["ben"])]


def test_collaborator_role_needs_step_up_and_an_active_member(world) -> None:
    client = world.client
    _set(world, join_policy="open")
    ben_url = f"{world.base}/members/{world.public['ben']}"
    give = {"role": "collaborator"}
    # A bearer credential never changes a role (design Section 6.8), also the token of the moderator or of the admin.
    for name in ("ann", "admin"):
        refused = client.patch(ben_url, json=give, headers=world.bearer[name])
        assert (refused.status_code, refused.json()) == (403, {"error": "step_up_requires_session"}), name
    # Rule 4: a collaborator must be an active member first.
    assert world.session("ann", "PATCH", ben_url, json=give).json() == {"error": "not_active_member"}
    assert client.post(f"{world.base}/membership", headers=world.bearer["ben"]).status_code == 201
    given = world.session("ann", "PATCH", ben_url, json=give)
    assert (given.status_code, given.json()["member"]["role"]) == (200, "collaborator")
    # The collaborator has ``maintain`` and ``review`` now, but no ``people`` and no ``settings``.
    assert client.post(f"{world.base}/document/hide", json={"id": "no-such-document", "hidden": True}, headers=world.bearer["ben"]).status_code != 403
    assert client.patch(f"{world.base}/members/{world.public['cho']}", json={"action": "ban"}, headers=world.bearer["ben"]).json() == {"error": "kb_capability_required"}
    assert client.patch(f"{world.base}/", json={"label": "x"}, headers=world.bearer["ben"]).json() == {"error": "kb_capability_required"}
    # To remove the role makes it a member again.
    back = world.session("ann", "PATCH", ben_url, json={"role": "member"})
    assert (back.status_code, back.json()["member"]["role"], back.json()["member"]["state"]) == (200, "member", "active")
    assert client.post(f"{world.base}/document/hide", json={"id": "no-such-document", "hidden": True}, headers=world.bearer["ben"]).json() == {"error": "kb_capability_required"}
    assert [(actor, action) for actor, action, _target in _log(world) if action.startswith("role_")] == [(2, "role_collaborator"), (2, "role_member")]


def test_admin_assigns_moderators_and_the_last_moderator_rule(world) -> None:
    client = world.client
    moderators = f"/api/admin/kb/{world.kb['number']}/moderators"
    # The last moderator cannot leave.
    assert client.delete(f"{world.base}/membership", headers=world.bearer["ann"]).json() == {"error": "last_moderator"}
    # Only the admin gives the role, and only with step-up.
    assert world.session("ann", "PUT", f"{moderators}/{world.public['ben']}").status_code == 404
    assert client.put(f"{moderators}/{world.public['ben']}", headers=world.bearer["admin"]).json() == {"error": "step_up_requires_session"}
    given = world.session("admin", "PUT", f"{moderators}/{world.public['ben']}")
    assert (given.status_code, given.json()["member"]["role"], given.json()["member"]["state"]) == (200, "moderator", "active")
    assert _membership(world, "ben") == ("moderator", "active")
    # A moderator cannot change another moderator; the collaborator route does not change a moderator.
    assert client.patch(f"{world.base}/members/{world.public['ben']}", json={"action": "ban"}, headers=world.bearer["ann"]).json() == {"error": "moderator_protected"}
    assert world.session("ann", "PATCH", f"{world.base}/members/{world.public['ben']}", json={"role": "member"}).json() == {"error": "moderator_protected"}
    # With two moderators one can leave.
    assert client.delete(f"{world.base}/membership", headers=world.bearer["ann"]).status_code == 200
    assert client.delete(f"{world.base}/membership", headers=world.bearer["ben"]).json() == {"error": "last_moderator"}
    # The admin removes the role: the account is a member again, and the knowledge base has no active moderator (the admin moderates it).
    removed = world.session("admin", "DELETE", f"{moderators}/{world.public['ben']}")
    assert (removed.status_code, removed.json()["member"]["role"]) == (200, "member")
    assert world.session("admin", "DELETE", f"{moderators}/{world.public['ben']}").json() == {"error": "not_a_moderator"}
    listed = {kb["number"]: kb for kb in client.get("/kb", headers=world.bearer["admin"]).json()["knowledge_bases"]}
    assert listed[world.kb["number"]]["active_moderators"] == 0
    assert client.patch(f"{world.base}/members/{world.public['ben']}", json={"action": "ban"}, headers=world.bearer["admin"]).json()["member"]["state"] == "banned"
    # Unknown knowledge base or account: 404.
    assert world.session("admin", "PUT", f"/api/admin/kb/999/moderators/{world.public['ben']}").status_code == 404
    assert world.session("admin", "PUT", f"{moderators}/12345").json() == {"error": "account_not_found"}

    assert [(actor, action, target) for actor, action, target in _log(world) if action.startswith("moderator_")] == [(1, "moderator_give", world.public["ben"]), (1, "moderator_remove", world.public["ben"])]
    auth_conn = auth_db.connect_auth_db(auth_db.auth_db_path(world.settings.data_dir))
    try:
        audit = [(a["actor_account_id"], a["action"], a["target_account_id"]) for a in reversed(auth_db.list_admin_audit(auth_conn))]
    finally:
        auth_conn.close()
    assert audit == [(1, "kb_moderator_give", 3), (1, "kb_moderator_remove", 3)]


def test_suspended_moderator_gives_no_right_and_does_not_count(world) -> None:
    """Design Section 6.7 rule 5: a suspended account keeps its role in storage, but the role gives no right."""
    conn = dbm.connect(world.settings.db_file)
    try:
        conn.execute("UPDATE accounts SET status='suspended' WHERE id=2")
        conn.commit()
        assert kb_collab.active_moderators(conn, world.kb["id"]) == []
        assert kb_collab.get_membership(conn, world.kb["id"], 2)["role"] == "moderator"
    finally:
        conn.close()
    assert world.client.patch(f"{world.base}/", json={"label": "x"}, headers=world.bearer["ann"]).status_code in (401, 403, 404)


def _theme_rows(world) -> tuple[int, int, int]:
    """The theme rows of the knowledge base: memberships, signatures, judged counts."""
    conn = dbm.connect_existing(world.settings.db_file, readonly=True)
    try:
        kb_id = world.kb["id"]
        return tuple(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE kb_id=?", (kb_id,)).fetchone()[0] for table in ("theme_knowledge_bases", "kb_signatures", "kb_judged_counts"))
    finally:
        conn.close()


def _put_in_a_theme(world, knowledge_type: str) -> None:
    """Give the knowledge base a theme of ``knowledge_type``, a signature, and a judged count, as a judgement run does."""
    from claire.store import themes as themes_store

    conn = dbm.connect(world.settings.db_file)
    try:
        kb_id = world.kb["id"]
        theme = themes_store.create_theme(conn, label=f"Theme {knowledge_type}", knowledge_type=knowledge_type)
        themes_store.add_theme_member(conn, theme_id=theme["id"], kb_id=kb_id, source="computed", score=0.9)
        themes_store.save_signature(conn, kb_id=kb_id, knowledge_type=knowledge_type, embed_model="mock", entities="[]", centroid=None, visible_documents=3)
        themes_store.replace_judged_counts(conn, {kb_id: 3})
        conn.commit()
    finally:
        conn.close()


def test_moderator_and_admin_correct_the_knowledge_type(world) -> None:
    """A moderator or the admin corrects a wrong knowledge type (for example a type that a migration set for all knowledge bases). The theme rows of the old type go, so the next judgement run judges the knowledge base again."""
    client = world.client
    _put_in_a_theme(world, "real")
    assert _theme_rows(world) == (1, 1, 1)

    # A non-member cannot change the type.
    refused = client.patch(f"{world.base}/", json={"knowledge_type": "fictional"}, headers=world.bearer["ben"])
    assert (refused.status_code, refused.json()) == (403, {"error": "kb_capability_required"})
    assert _theme_rows(world) == (1, 1, 1)

    # The same type is no change: the theme rows stay.
    assert client.patch(f"{world.base}/", json={"knowledge_type": "real"}, headers=world.bearer["ann"]).status_code == 200
    assert _theme_rows(world) == (1, 1, 1)

    # The moderator changes the type.
    changed = client.patch(f"{world.base}/", json={"knowledge_type": "fictional"}, headers=world.bearer["ann"])
    assert changed.status_code == 200, changed.text
    assert changed.json()["knowledge_base"]["knowledge_type"] == "fictional"
    assert _theme_rows(world) == (0, 0, 0)

    # The admin, who is not a member, changes it back.
    _put_in_a_theme(world, "fictional")
    restored = client.patch(f"{world.base}/", json={"knowledge_type": "real"}, headers=world.bearer["admin"])
    assert restored.status_code == 200, restored.text
    assert restored.json()["knowledge_base"]["knowledge_type"] == "real"
    assert _theme_rows(world) == (0, 0, 0)
    log = _log(world)
    assert (2, "settings", "knowledge_type") in log
    assert (1, "settings", "knowledge_type") in log
