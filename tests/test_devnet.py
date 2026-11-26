"""Tests of the development network: two Claire servers with real sidecars, real TLS, real DNS, and the reverse proxy of production (docs/design/CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md Section 8.4; acceptance steps KA1 to KA3 of KNOWLEDGE_FEDERATION_DESIGN.md Section 14.2).

Run: ``./cb-manuscript dev devnet up --servers a,b``, then ``./cb-manuscript dev devnet test``. The command runs pytest in the toolbox container on the network ``claire-devnet-edge``; the gate ``CB_DEVNET_TEST=1`` and the state file ``CB_DEVNET_STATE`` (the admin credentials of each server) come from it. Without the gate the tests are skipped.

The tests act only through the public routes and the federation paths of the servers, through the gateway: the admin routes with the admin of the state file, the account routes of accounts that the tests make with invitations, and the federation of the sidecars. No test writes a row in a database or calls an internal route. A test makes new names (a random suffix), so that the tests can run again on the same network. A test waits for a state with a time limit, never with a fixed sleep.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

pytestmark = [
    pytest.mark.devnet,
    pytest.mark.skipif(os.environ.get("CB_DEVNET_TEST") != "1", reason="the tests of the development network need CB_DEVNET_TEST=1 (./cb-manuscript dev devnet test)"),
]

# The loops of the sidecar run each 5 or 60 seconds: a wait has three intervals of the slow loop.
WAIT_SECONDS = 200


def _state() -> dict[str, Any]:
    path = os.environ.get("CB_DEVNET_STATE")
    if not path:
        pytest.fail("CB_DEVNET_STATE is not set")
    return json.loads(Path(path).read_text(encoding="utf-8"))


def wait_for(what: str, probe: Callable[[], Any], timeout: float = WAIT_SECONDS) -> Any:
    """Call ``probe`` until it gives a true value; fail after ``timeout`` seconds."""
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        last = probe()
        if last:
            return last
        time.sleep(1)
    pytest.fail(f"time limit: {what} (last value: {last!r})")


class Session:
    """The requests of one account of one server, with the session of a sign-in through ``/login``."""

    def __init__(self, origin: str, client: httpx.Client, username: str) -> None:
        self.origin = origin
        self.client = client
        self.username = username

    def __call__(self, method: str, path: str, body: Any = None, *, data: dict | None = None) -> httpx.Response:
        headers = {} if method in ("GET", "HEAD") else {"Origin": self.origin}
        return self.client.request(method, f"{self.origin}{path}", json=body, data=data, headers=headers)

    def ok(self, method: str, path: str, body: Any = None, *, status: tuple[int, ...] = (200, 201, 202)) -> Any:
        answer = self(method, path, body)
        assert answer.status_code in status, f"{method} {self.origin}{path}: {answer.status_code} {answer.text[:500]}"
        return answer.json() if answer.content else None


def sign_in(origin: str, username: str, password: str) -> Session:
    client = httpx.Client(timeout=60, follow_redirects=False)
    answer = client.post(f"{origin}/login", data={"username": username, "password": password}, headers={"Origin": origin})
    assert answer.status_code == 303, f"login {username}@{origin}: {answer.status_code}"
    return Session(origin, client, username)


@pytest.fixture(scope="module")
def admins() -> dict[str, Session]:
    state = _state()
    return {name: sign_in(entry["origin"], entry["username"], entry["password"]) for name, entry in state.items()}


def _readiness(admin: Session) -> dict[str, dict]:
    return {item["id"]: item for item in admin.ok("GET", "/api/admin/federation")["readiness"]["items"]}


def test_ka1_ka2_readiness_of_both_servers(admins: dict[str, Session]) -> None:
    """KA1 and KA2: on each server each required check passes through the public origin, and GF17 passes. GF15 applies when the server has a shared knowledge base: each server gets one."""
    assert set(admins) == {"a", "b"}
    for name, admin in admins.items():
        if not any(kb.get("is_personal") is False for kb in admin.ok("GET", "/kb").get("knowledge_bases", [])):
            admin.ok("POST", "/kb", {"label": f"Readiness {name.upper()}", "knowledge_type": "real"})

        def failures(admin: Session = admin) -> dict[str, tuple[str, str]]:
            return {key: (item["code"], item["detail"]) for key, item in _readiness(admin).items() if item["level"] == "required" and item["applies"] and not item["passed"]}

        # A new sidecar sends its first heartbeat and its loops make progress some seconds after the start.
        deadline = time.monotonic() + WAIT_SECONDS
        while (failed := failures()) and time.monotonic() < deadline:
            time.sleep(2)
        assert failed == {}, f"server {name}: {failed}"
        items = _readiness(admin)
        assert items["GF15"]["applies"] is True, items["GF15"]
        assert items["GF17"]["passed"] is True, items["GF17"]


def test_de4_a_reads_the_server_of_b_through_the_gateway(admins: dict[str, Session]) -> None:
    """Phase DE4: after the admin of A adds b.claire.test as a known server, the sidecar of A reads NodeInfo and the server actor of B through the gateway with TLS and the DNS of the network."""
    a = admins["a"]
    known = a("POST", "/api/admin/federation/known-servers", {"domain": "b.claire.test"})
    assert known.status_code in (200, 201), known.text

    def read() -> dict | None:
        for server in a.ok("GET", "/api/admin/federation/known-servers")["servers"]:
            if server["domain"] == "b.claire.test" and server["last_read_at"] and server["software_name"]:
                return server
        return None

    server = wait_for("the read of b.claire.test by A", read)
    assert (server["server_actor"], server["software_name"], server["error_count"]) == ("https://b.claire.test/ap/actors/server", "claire-bible", 0), server


# ── KA3: the completion criteria of KF1 to KF5 between A and B ────────────────────────────────

# The subject of the shared knowledge bases. Each name gets the suffix of the run, so that the knowledge bases of an earlier run on the same network are another subject.
CITIES = ["Seoul", "Busan", "Daegu", "Incheon", "Gwangju", "Daejeon", "Ulsan", "Suwon"]


def actor_of(session: Session) -> str:
    """The actor IRI of the account of ``session``, through WebFinger (a public route)."""
    host = session.origin.removeprefix("https://")
    found = session.client.get(f"{session.origin}/.well-known/webfinger", params={"resource": f"acct:{session.username}@{host}"}, headers={"Accept": "application/jrd+json"})
    assert found.status_code == 200, found.text
    return next(link["href"] for link in found.json()["links"] if link.get("rel") == "self")


def add_document(session: Session, title: str, entities: list[str], *, url: str | None = None) -> str:
    """One document of the personal knowledge base with these entities, through the path of the agent client (stage, claim, submit): the test gives the extraction, as an agent does. With ``url`` the server fetches the page (a web document); else the document is a pasted text."""
    text = f"{title}. " + " ".join(f"{name} is a city of Korea." for name in entities)
    staged = session.ok("POST", "/api/v1/extraction/stage", {"url_or_text": url or text, "title": title})
    document = staged["document_id"]
    claim = session.ok("POST", f"/api/v1/extraction/sources/{document}/claim", {"client_id": "devnet-test", "lease_seconds": 120})
    payload = {
        "schema_version": "agent-extract-v1",
        "summary": f"A note about {len(entities)} cities of Korea: " + ", ".join(entities) + ".",
        "detail": f"= {title}\n\n{text}",
        "entities": [{"name": name, "type": "Concept", "aliases": [], "observations": [f"{name} is a city of Korea."]} for name in entities],
        "relations": [],
        "model": "devnet-test",
    }
    done = session.ok("POST", f"/api/v1/extraction/sources/{document}/submit", {"claim_token": claim["claim_token"], "expected_content_hash": staged["content_hash"], "dry_run": False, "payload": payload})
    assert done["committed"] is True, done
    return document


def contribute(session: Session, kb_number: str, document: str) -> dict:
    """The founder contributes a document of its personal knowledge base to a shared knowledge base (a shared knowledge base takes documents only by contribution)."""
    return session.ok("POST", f"/kb/{kb_number}/contributions", {"document": document})


def fill_shared_kb(session: Session, kb_number: str, names: list[str], label: str) -> None:
    """Six documents with three entities each: the judgement gives the knowledge base a signature (at least five counted documents)."""
    for index in range(6):
        contribute(session, kb_number, add_document(session, f"{label} {index}", [names[(index + step) % len(names)] for step in range(3)]))


def known_b(a: Session) -> dict | None:
    return next((server for server in a.ok("GET", "/api/admin/federation/known-servers")["servers"] if server["domain"] == "b.claire.test"), None)


def read_b_again(a: Session) -> None:
    """A reads the /kb collection of B now: the admin of A removes B as a known server and adds it again (a read of a new known server starts at once). The daily read would come later."""
    if known_b(a) is not None:
        a.ok("DELETE", "/api/admin/federation/known-servers", {"domain": "b.claire.test"})
    a.ok("POST", "/api/admin/federation/known-servers", {"domain": "b.claire.test"})


def record_of(a: Session, kb_actor: str) -> dict | None:
    server = known_b(a)
    return None if server is None else next((kb for kb in server.get("kbs", []) if kb["iri"] == kb_actor), None)


def network_mode(admin: Session) -> None:
    """The root page of the network mode (KF2) needs the network mode; the switch needs a server tier (``none`` is valid)."""
    if admin.ok("GET", "/api/admin/server")["mode"] == "network":
        return
    admin.ok("PUT", "/api/admin/server-tier", {"tier": {"provider": "none"}})
    admin.ok("POST", "/api/admin/server/mode", {"mode": "network"})


@pytest.fixture(scope="module")
def world(admins: dict[str, Session]) -> dict[str, Any]:
    """A and B, each with a shared knowledge base about the same cities; A read B, and the judgement of A put Cities of B in the theme of Places."""
    a, b = admins["a"], admins["b"]
    sfx = secrets.token_hex(3)
    names = [f"{name} {sfx}" for name in CITIES]
    network_mode(a)
    cities = b.ok("POST", "/kb", {"label": f"Cities {sfx}", "knowledge_type": "real"})["knowledge_base"]
    fill_shared_kb(b, cities["number"], names, f"Cities {sfx}")
    b.ok("POST", "/api/admin/themes/judge", {})
    places = a.ok("POST", "/kb", {"label": f"Places {sfx}", "knowledge_type": "real"})["knowledge_base"]
    fill_shared_kb(a, places["number"], names, f"Places {sfx}")
    kb_actor = f"https://b.claire.test/ap/kb/{cities['number']}"
    read_b_again(a)
    record = wait_for("the record of Cities on A", lambda: record_of(a, kb_actor))
    a.ok("POST", "/api/admin/themes/judge", {})
    theme = next((t for t in a.ok("GET", "/themes")["themes"] if any(kb.get("number") == places["number"] for kb in a.ok("GET", f"/themes/{t['number']}/")["knowledge_bases"])), None)
    assert theme is not None, "the judgement of A made no theme with Places"
    return {"sfx": sfx, "names": names, "cities": cities, "places": places, "kb_actor": kb_actor, "remote_id": record["id"], "theme": theme["number"]}


def test_ka3_kf2_a_shared_knowledge_base_of_b_is_in_a_theme_and_on_the_root_page_of_a(admins: dict[str, Session], world: dict[str, Any]) -> None:
    """KF2: a shared knowledge base on server B appears in a theme of server A and on the root page of A, without any agreement."""
    a = admins["a"]
    theme = a.ok("GET", f"/themes/{world['theme']}/")
    assert [(kb["remote"], kb["server"], kb["url"]) for kb in theme["knowledge_bases"] if kb["remote"]] == [(True, "b.claire.test", f"https://b.claire.test/kb/{world['cities']['number']}/")], theme["knowledge_bases"]
    root = a.client.get(f"{a.origin}/", headers={"Accept": "text/html"})
    assert root.status_code == 200 and f'href="https://b.claire.test/kb/{world["cities"]["number"]}/"' in root.text


def remote_targets(session: Session, submission: str) -> list[dict]:
    return [target for item in session.ok("GET", f"/submissions/{submission}")["items"] for target in item["targets"] if target.get("remote")]


def decided(session: Session, submission: str) -> list[dict] | None:
    """The remote targets of a submission after the other server answered."""
    targets = remote_targets(session, submission)
    return targets if targets and all(target["state"] != "pending" for target in targets) else None


def pending_of(b: Session, kb_number: str, credit: str, title: str) -> dict | None:
    contributions = b.ok("GET", f"/kb/{kb_number}/contributions?state=pending")["contributions"]
    return next((c for c in contributions if (c.get("remote") or {}).get("credit") == credit and c["title"] == title), None)


def test_ka3_kf4_contributions_across_servers_with_approval_grant_revoke_and_reject(admins: dict[str, Session], world: dict[str, Any]) -> None:
    """KF4: an account of A contributes to a shared knowledge base of B; a reviewer of B approves; the submission page on A shows the decision; after a grant, the next contribution needs no approval; after Revoke, it needs approval again (and a rejection reaches A)."""
    a, b = admins["a"], admins["b"]
    cities, kb_actor, sfx = world["cities"]["number"], world["kb_actor"], world["sfx"]
    a_actor = actor_of(a)
    # 1. The account of A contributes to the theme that has Cities of B: its account actor sends Offer.
    first = add_document(a, f"Offer {sfx} 1", world["names"][:3])
    submission = a.ok("POST", "/submissions", {"documents": [first], "theme": world["theme"]})["submission"]["number"]
    waiting = wait_for("the Offer on B", lambda: pending_of(b, cities, a_actor, f"Offer {sfx} 1"))
    assert waiting["offer"]["server"] == "a.claire.test", waiting
    # 2. The reviewer of B approves: Accept with result reaches the submission page of A.
    b.ok("PATCH", f"/kb/{cities}/contributions/{waiting['number']}", {"action": "approve"})
    targets = wait_for("the Accept on A", lambda: decided(a, submission))
    assert [(t["state"], t["approved"], t["result"]) for t in targets] == [("accepted", True, f"{kb_actor}/documents/{waiting['number']}")], targets

    # 3. The reviewer of B gives the account of A the role collaborator: Grant. The next contribution, a direct choice of Cities, needs no approval.
    grant = b.ok("POST", f"/kb/{cities}/remote-collaborators", {"actor": a_actor})["grant"]
    wait_for("the Grant on A", lambda: [g for g in a.ok("GET", "/api/kb/remote-grants")["grants"] if g["knowledge_base"] == kb_actor and g["state"] == "active"])
    second = add_document(a, f"Offer {sfx} 2", world["names"][1:4])
    direct = a.ok("POST", "/submissions", {"documents": [second], "remote_knowledge_base": world["remote_id"]})["submission"]["number"]
    targets = wait_for("the Accept of the direct contribution on A", lambda: decided(a, direct))
    assert [(t["state"], t["approved"], t["with_grant"]) for t in targets] == [("accepted", True, True)], targets

    # 4. The reviewer of B ends the grant: Revoke. A refuses a direct choice now, and a contribution through the theme waits for approval; the reviewer rejects it with a reason code, and A shows the reason.
    b.ok("DELETE", f"/kb/{cities}/remote-collaborators/{grant['id']}")
    wait_for("the Revoke on A", lambda: [g for g in a.ok("GET", "/api/kb/remote-grants")["grants"] if g["knowledge_base"] == kb_actor and g["state"] == "revoked"])
    third = add_document(a, f"Offer {sfx} 3", world["names"][2:5])
    refused = a("POST", "/submissions", {"documents": [third], "remote_knowledge_base": world["remote_id"]})
    assert (refused.status_code, refused.json()["error"]) == (403, "grant_required"), refused.text
    again = a.ok("POST", "/submissions", {"documents": [third], "theme": world["theme"]})["submission"]["number"]
    waiting = wait_for("the third Offer on B", lambda: pending_of(b, cities, a_actor, f"Offer {sfx} 3"))
    b.ok("PATCH", f"/kb/{cities}/contributions/{waiting['number']}", {"action": "reject", "reason": "off_subject"})
    targets = wait_for("the Reject on A", lambda: decided(a, again))
    assert [(t["state"], t["approved"], t["reason"]) for t in targets] == [("rejected", False, "off_subject")], targets


class _Page:
    """Web pages that the test serves on the network claire-devnet-edge (the documents of KF3 and KF5). The content of a page can change (a revision)."""

    def __init__(self) -> None:
        import http.server
        import socket
        import threading

        pages: dict[str, str] = {}
        self.pages = pages

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - the name of the standard library
                body = pages.get(self.path)
                if body is None:
                    self.send_error(404)
                    return
                data = body.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - the name of the standard library
                return

        self.server = http.server.ThreadingHTTPServer(("0.0.0.0", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        # The address of this container on the network: the one that a connection to the gateway uses.
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect((socket.gethostbyname("a.claire.test"), 443))
        self.address = probe.getsockname()[0]
        probe.close()

    def set(self, path: str, title: str, text: str) -> str:
        """Serve a page; return its URL. The fetcher refuses a thin body, so a page has some paragraphs of text."""
        self.pages[path] = f"<!doctype html><html><head><title>{title}</title></head><body><article><h1>{title}</h1>" + f"<p>{text}</p>" * 12 + "</article></body></html>"
        return f"http://{self.address}:{self.server.server_address[1]}{path}"


def test_ka3_kf3_curation_then_a_revision_and_a_removal_at_the_origin(admins: dict[str, Session], world: dict[str, Any]) -> None:
    """KF3: a revision on B reaches the copy on A without approval; a removal on B hides the copy on A and puts it in the review queue."""
    a, b = admins["a"], admins["b"]
    cities, places, kb_actor, sfx = world["cities"]["number"], world["places"]["number"], world["kb_actor"], world["sfx"]
    page = _Page()
    try:
        # B: a document from a web page, contributed to Cities.
        url = page.set(f"/harbours-{sfx}", f"Harbours {sfx}", "Busan has the largest harbour of Korea, and ships from many countries come to it each day.")
        ingested = b.ok("POST", "/ingest", {"payload": url, "extraction_mode": "provider", "renditions": []})
        assert ingested.get("error") is None, ingested
        root = ingested["document_id"]
        contribute(b, cities, root)
        number = next(c["number"] for c in b.ok("GET", f"/kb/{cities}/contributions")["contributions"] if c["document"] == root or c["title"] == f"Harbours {sfx}")
        document_iri = f"{kb_actor}/documents/{number}"

        # 1. A moderator of A opens Cities of B in Places: the sidecar of A reads the documents collection; then the document with its extraction.
        a.ok("GET", f"/kb/{places}/remote-sources?source={world['remote_id']}")
        wait_for("the documents of Cities on A", lambda: [d for d in (a.ok("GET", f"/kb/{places}/remote-sources?source={world['remote_id']}")["selected"] or {}).get("documents", []) if d["iri"] == document_iri])
        a.ok("POST", f"/kb/{places}/curations", {"remote_source": world["remote_id"], "documents": [document_iri]})
        wait_for("the curation on A", lambda: [c for c in a.ok("GET", f"/kb/{places}/remote-sources")["curations"] if c["document"] == document_iri and c["state"] == "done"])
        copy = next(c for c in a.ok("GET", f"/kb/{places}/contributions")["contributions"] if (c.get("remote") or {}).get("document") == document_iri)
        assert (copy["origin"], copy["state"]) == ("curation", "visible"), copy
        wait_for("the follow of Cities by A", lambda: [s for s in a.ok("GET", f"/kb/{places}/remote-sources")["sources"] if s["id"] == world["remote_id"] and s["follow"] == "accepted"])

        # 2. A revision on B: the page has a new content, and B fetches it again. The knowledge base actor sends Update; the copy on A has the new version without approval.
        page.set(f"/harbours-{sfx}", f"Harbours {sfx} revised", "Incheon has a large harbour too, near the capital, and the airport of the capital is near it.")
        again = b.ok("POST", "/reingest", {"target": root})
        assert again.get("title") == f"Harbours {sfx} revised", again

        def revised() -> dict | None:
            now = next(c for c in a.ok("GET", f"/kb/{places}/contributions")["contributions"] if c["number"] == copy["number"])
            return now if now["document"] != copy["document"] and [r["state"] for r in now.get("revisions", [])][-1:] == ["done"] else None

        now = wait_for("the revision on A", revised)
        assert now["state"] == "visible", now

        # 3. A removal on B: the knowledge base actor sends Delete with the reason code; the copy on A is hidden and waits in the review queue of Places.
        b.ok("POST", f"/kb/{cities}/contributions/{number}/remove", {"reason": "spam"})
        queued = wait_for("the removal on A", lambda: [c for c in a.ok("GET", f"/kb/{places}/contributions?state=hidden")["contributions"] if c["number"] == copy["number"]])
        assert (queued[0]["origin_removed"]["reason"], queued[0]["origin_removed"]["waiting"]) == ("spam", True), queued
    finally:
        page.server.shutdown()


def test_ka3_kf5_a_new_shared_knowledge_base_on_b_gets_the_matching_documents_of_an_account_of_a(admins: dict[str, Session], world: dict[str, Any]) -> None:
    """KF5: with kb_auto_contribution on both servers, a new shared knowledge base on B gets the matching documents of an account of A after a run."""
    a, b = admins["a"], admins["b"]
    sfx, names = world["sfx"], world["names"]
    a_actor = actor_of(a)
    for admin in (a, b):
        assert admin.ok("PUT", "/api/admin/auto-contribution", {"state": "on"})["state"] == "on"
    # The account of A: two web pages of the subject and one of another subject (an automatic rule never sends a pasted text, Section 9.9 rule 2), and an automatic rule with the theme of Places.
    page = _Page()
    try:
        matching = {f"Auto {sfx} {index}": add_document(a, f"Auto {sfx} {index}", names[index : index + 3], url=page.set(f"/auto-{sfx}-{index}", f"Auto {sfx} {index}", f"A guide to {', '.join(names[index : index + 3])}.")) for index in (4, 5)}
        add_document(a, f"Planets {sfx}", [f"Mars {sfx}", f"Jupiter {sfx}", f"Saturn {sfx}"], url=page.set(f"/planets-{sfx}", f"Planets {sfx}", "A guide to Mars, Jupiter, and Saturn."))
    finally:
        page.server.shutdown()
    rule = a.ok("PUT", "/api/auto-rule", {"state": "active", "memberships": False, "mutual": False, "include_uploads": False, "themes": [world["theme"]]})
    assert rule["rule"]["state"] == "active", rule

    # A new shared knowledge base on B about the same subject. A reads the /kb collection of B: the record tells that B accepts automatic contributions.
    towns = b.ok("POST", "/kb", {"label": f"Towns {sfx}", "knowledge_type": "real"})["knowledge_base"]
    fill_shared_kb(b, towns["number"], names, f"Towns {sfx}")
    b.ok("POST", "/api/admin/themes/judge", {})
    towns_actor = f"https://b.claire.test/ap/kb/{towns['number']}"
    read_b_again(a)
    wait_for("the record of Towns on A", lambda: record_of(a, towns_actor))

    # A run on A (the judgement run of the admin runs the rules after it): the account actor sends one automatic Offer for each matching document.
    a.ok("POST", "/api/admin/themes/judge", {})

    def offered() -> list[dict] | None:
        found = [c for c in b.ok("GET", f"/kb/{towns['number']}/contributions?state=pending")["contributions"] if (c.get("remote") or {}).get("credit") == a_actor]
        return found if {c["title"] for c in found} >= set(matching) else None

    pending = wait_for("the automatic offers on Towns of B", offered)
    assert {(c["title"], c["origin"]) for c in pending if c["title"] in matching} == {(title, "auto") for title in matching}, pending
    assert not [c for c in pending if c["title"] == f"Planets {sfx}"]

    # The reviewer of B approves one: the list of automatic contributions of the account of A shows the decision of B.
    approved = next(c for c in pending if c["title"] == f"Auto {sfx} 4")
    b.ok("PATCH", f"/kb/{towns['number']}/contributions/{approved['number']}", {"action": "approve"})

    def accepted() -> dict | None:
        rows = [c for c in a.ok("GET", "/api/contributions?origin=auto")["contributions"] if (c.get("remote_target") or {}).get("actor") == towns_actor]
        return next((row for row in rows if row["document"] == matching[f"Auto {sfx} 4"] and row["state"] == "accepted"), None)

    row = wait_for("the Accept of the automatic contribution on A", accepted)
    assert row["remote_target"]["result"] == f"{towns_actor}/documents/{approved['number']}", row


def test_ka3_kf1_servers_know_each_other_a_blocked_server_gets_404_and_the_single_mode_root(admins: dict[str, Session], world: dict[str, Any]) -> None:
    """KF1: two servers know each other after a contribution or an admin entry; a blocked server gets 404 on the shared-knowledge collections; in single mode / gives 302 to /@{username}/.

    The development network has two servers, so B blocks A (the criterion names a third server). The replay of a failed activity is not in this test: no public route makes an activity fail on purpose (tests/test_knowledge_federation_kf1.py has it)."""
    a, b = admins["a"], admins["b"]
    # A knows B by the admin entry (phase DE4); B knows A by the contributions of KF4 (they ran before this test with the same world).
    known_a = next((server for server in b.ok("GET", "/api/admin/federation/known-servers")["servers"] if server["domain"] == "a.claire.test"), None)
    assert known_a is not None and known_a["server_actor"] == "https://a.claire.test/ap/actors/server", known_a
    # B is in single mode: its root goes to the page of its admin.
    root = b.client.get(f"{b.origin}/")
    assert (root.status_code, root.headers.get("location")) == (302, f"/@{b.username}/"), (root.status_code, root.headers)

    # B blocks A: the signed read of the /kb collection of B by A gets 404 (unit KF1), and A records the failed read.
    started = time.time()
    b.ok("POST", "/api/admin/federation/domain-blocks", {"domain": "a.claire.test", "note": "devnet test of KF1"})
    try:
        read_b_again(a)

        def refused() -> dict | None:
            state = (known_b(a) or {}).get("kb_read") or {}
            return state if (state.get("last_error_at") or 0) >= started else None

        # The sidecar of A records a collection that does not load (here the 404 of B) as collection_unavailable; it does not keep the status.
        state = wait_for("the refused read of B by A", refused)
        assert state["last_error"] == "collection_unavailable", state
    finally:
        b.ok("DELETE", "/api/admin/federation/domain-blocks", {"domain": "a.claire.test"})
    # Without the block, the next read of A gets the collection again.
    read_b_again(a)
    wait_for("the read of B by A after the block", lambda: record_of(a, world["kb_actor"]))
