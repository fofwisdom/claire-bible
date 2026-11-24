"""Subject judgement and document match (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Sections 8.2, 8.3, 8.5; unit CT-5).

Completion criteria: two shared knowledge bases with overlapping entities and the same type are in one theme; with different types they are not; adjustments survive a new run; theme numbers stay across runs; a document with the entities of a theme matches that theme.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from claire.collaboration import contributions
from claire.collaboration import theme_judgement as tj
from claire.core.config import Settings
from claire.ontology.base import normalize_name
from claire.store import db as dbm
from claire.store import themes as themes_store
from claire.store.knowledge_base import KnowledgeBaseManager

KOREA = ["Seoul", "Busan", "Incheon", "Daegu", "Han River", "Jeju"]
SPACE = ["Mars", "Jupiter", "Saturn", "Voyager", "Apollo", "Hubble"]


@pytest.fixture
def env(tmp_path: Path):
    (tmp_path / "data").mkdir()
    settings = Settings(
        CLAIRE_DB_PATH=str(tmp_path / "data" / "claire.db"),
        CLAIRE_VAULT_PATH=str(tmp_path / "vault"),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_FQDN="127.0.0.1:8765",
        CLAIRE_INJECT_TOKEN="owner-" + "s" * 32,
    )
    conn = dbm.connect(settings.db_file)
    dbm.init_db(conn)
    conn.execute("INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, updated_at) VALUES (2, 902, 'ann', 'member', 'active', 'Ann', 1, 1, 1)")
    conn.commit()
    conn.close()
    return settings, KnowledgeBaseManager(settings)


def _fill(settings: Settings, kb_id: int, names: list[str], *, documents: int = 6, prefix: str = "") -> list[str]:
    """Give a knowledge base ``documents`` visible documents; each entity of ``names`` has all of them as sources, and the first names have more weight."""
    conn = dbm.connect(settings.db_file)
    try:
        doc_ids = [f"doc_{prefix or kb_id}_{i}" for i in range(documents)]
        for doc_id in doc_ids:
            conn.execute("INSERT INTO documents (id, kb_id, title, raw_text, hidden) VALUES (?, ?, ?, 'text', 0)", (doc_id, kb_id, doc_id))
        for rank, name in enumerate(names):
            sources = doc_ids[: max(1, documents - rank)]
            conn.execute(
                "INSERT INTO entities (id, kb_id, type, name, norm_name, aliases, sources, created_at, updated_at) VALUES (?, ?, 'Concept', ?, ?, '[]', ?, 1, 1)",
                (f"ent_{prefix or kb_id}_{rank}", kb_id, name, normalize_name(name), json.dumps(sources)),
            )
        conn.commit()
        return doc_ids
    finally:
        conn.close()


def _run(settings: Settings) -> tj.RunReport:
    conn = dbm.connect(settings.db_file)
    try:
        report = tj.run_judgement(conn)
        conn.commit()
        return report
    finally:
        conn.close()


def _themes(settings: Settings) -> dict[int, dict]:
    """Active themes: number → label, type, and the member knowledge bases (not the excluded rows)."""
    conn = dbm.connect_existing(settings.db_file, readonly=True)
    try:
        out = {}
        for theme in themes_store.list_themes(conn):
            if theme["state"] != "active":
                continue
            out[theme["number"]] = {"id": theme["id"], "label": theme["label"], "type": theme["knowledge_type"], "members": sorted(m["kb_id"] for m in themes_store.theme_members(conn, theme["id"]))}
        return out
    finally:
        conn.close()


def test_pure_functions() -> None:
    a = tj.Signature(1, "real", True, {("Concept", "seoul"): 3.0, ("Concept", "busan"): 1.0})
    b = tj.Signature(2, "real", True, {("Concept", "seoul"): 30.0, ("Concept", "busan"): 10.0})
    c = tj.Signature(3, "real", True, {("Concept", "mars"): 2.0})
    idf = tj.idf_table([a, b, c])
    # A key of each signature still counts (the design formula ln(N/df) gives 0 for it).
    assert idf[("Concept", "seoul")] > 0 and idf[("Concept", "mars")] > idf[("Concept", "seoul")]
    # The size of a knowledge base does not change the similarity; other subjects have none.
    assert tj.similarity(a, b, idf) == pytest.approx(1.0) and tj.similarity(a, c, idf) == 0.0
    assert tj.weighted_jaccard({}, {}) == 0.0 and tj.cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    # Average linkage: a and b merge, c stays alone; a high threshold merges nothing.
    assert tj.cluster([a, b, c], idf, 0.25) == [[1, 2], [3]]
    assert tj.cluster([a, c], tj.idf_table([a, c]), 0.25) == [[1], [3]]


def test_same_subject_and_type_make_one_theme_and_the_numbers_stay(env) -> None:
    settings, manager = env
    korea_a = manager.define_kb("Korea A", account_id=1, knowledge_type="real").id
    korea_b = manager.define_kb("Korea B", account_id=2, knowledge_type="real").id
    space = manager.define_kb("Space", account_id=1, knowledge_type="real").id
    korea_fiction = manager.define_kb("Korea stories", account_id=2, knowledge_type="fictional").id
    small = manager.define_kb("Small", account_id=1, knowledge_type="real").id
    _fill(settings, korea_a, KOREA)
    _fill(settings, korea_b, KOREA[:5] + ["Gwangju"], documents=12)
    _fill(settings, space, SPACE)
    _fill(settings, korea_fiction, KOREA)
    _fill(settings, small, KOREA, documents=3)  # fewer than 5 documents: no signature

    report = _run(settings)
    themes = _themes(settings)
    by_members = {tuple(t["members"]): number for number, t in themes.items()}
    # The two knowledge bases about Korea with the type ``real`` are in one theme; the other subject and the other type are not.
    assert set(by_members) == {(korea_a, korea_b), (space,), (korea_fiction,)}
    assert (report.signatures, report.themes_new, report.themes_active) == (4, 3, 3)
    korea_theme = themes[by_members[(korea_a, korea_b)]]
    assert (korea_theme["type"], korea_theme["label"]) == ("real", "Seoul · Busan")
    assert themes[by_members[(korea_fiction,)]]["type"] == "fictional"
    # The label and the top entities come from the summed weights.
    conn = dbm.connect_existing(settings.db_file, readonly=True)
    try:
        stored = themes_store.get_theme(conn, korea_theme["id"])
        assert json.loads(stored["top_entities"])[:3] == ["Seoul", "Busan", "Incheon"]
        assert ["Concept", "gwangju"] in json.loads(stored["signature"])
        assert {r["kb_id"] for r in themes_store.load_signatures(conn)} == {korea_a, korea_b, space, korea_fiction}
    finally:
        conn.close()

    # A second run changes nothing: the same numbers, the same members, no new theme.
    again = _run(settings)
    assert _themes(settings) == themes and again.themes_new == 0
    # A new knowledge base about the same subject joins the theme; the number of the theme stays.
    korea_c = manager.define_kb("Korea C", account_id=1, knowledge_type="real").id
    _fill(settings, korea_c, KOREA)
    assert _run(settings).themes_new == 0
    after = _themes(settings)
    assert after[by_members[(korea_a, korea_b)]]["members"] == [korea_a, korea_b, korea_c] and set(after) == set(themes)


def test_admin_adjustments_survive_a_new_run(env) -> None:
    settings, manager = env
    korea_a = manager.define_kb("Korea A", account_id=1, knowledge_type="real").id
    korea_b = manager.define_kb("Korea B", account_id=1, knowledge_type="real").id
    space = manager.define_kb("Space", account_id=1, knowledge_type="real").id
    for kb_id, names in ((korea_a, KOREA), (korea_b, KOREA), (space, SPACE)):
        _fill(settings, kb_id, names)
    _run(settings)
    themes = {tuple(t["members"]): t for t in _themes(settings).values()}
    korea_theme, space_theme = themes[(korea_a, korea_b)]["id"], themes[(space,)]["id"]

    conn = dbm.connect(settings.db_file)
    try:
        # Rename with a lock, pin Space to the theme of Korea, and exclude Korea B from it.
        themes_store.update_theme(conn, korea_theme, label="Korean geography", label_locked=1)
        themes_store.set_admin_member(conn, theme_id=korea_theme, kb_id=space, pinned=True, excluded=False)
        themes_store.set_admin_member(conn, theme_id=korea_theme, kb_id=korea_b, pinned=False, excluded=True)
        conn.commit()
    finally:
        conn.close()
    for _ in range(2):
        _run(settings)
        now = {t["id"]: t for t in _themes(settings).values()}
        # The label stays; the pinned knowledge base stays and is also in its computed theme; the excluded one is not added again.
        assert now[korea_theme]["label"] == "Korean geography"
        assert now[korea_theme]["members"] == [korea_a, space]
        assert now[space_theme]["members"] == [space]
        other = [t for t in now.values() if t["members"] == [korea_b]]
        assert len(other) == 1 and other[0]["id"] not in (korea_theme, space_theme)


def test_personal_knowledge_base_attaches_and_never_makes_a_theme(env) -> None:
    settings, manager = env
    korea = manager.define_kb("Korea", account_id=1, knowledge_type="real").id
    fiction = manager.define_kb("Korea stories", account_id=1, knowledge_type="fictional").id
    personal = manager.define_kb("Ann home", account_id=2, is_personal=True, is_public=True).id
    _fill(settings, korea, KOREA)
    _fill(settings, fiction, KOREA, prefix="f")
    _fill(settings, personal, KOREA)
    _fill(settings, 0, SPACE, prefix="admin")  # the personal knowledge base of the admin: another subject
    _run(settings)
    themes = {t["type"]: t for t in _themes(settings).values()}
    # A public personal knowledge base attaches to the ``real`` theme of its subject, and to no ``fictional`` theme. No theme has only personal knowledge bases.
    assert themes["real"]["members"] == [korea, personal] and themes["fictional"]["members"] == [fiction]
    assert len(_themes(settings)) == 2
    # A private personal knowledge base does not take part.
    manager.update_kb(personal, is_public=False)
    _run(settings)
    assert {t["type"]: t["members"] for t in _themes(settings).values()} == {"real": [korea], "fictional": [fiction]}


def test_document_with_the_entities_of_a_theme_matches_it(env) -> None:
    settings, manager = env
    korea = manager.define_kb("Korea", account_id=1, knowledge_type="real").id
    space = manager.define_kb("Space", account_id=1, knowledge_type="real").id
    myths = manager.define_kb("Myths", account_id=1, knowledge_type="fictional").id
    _fill(settings, korea, KOREA)
    _fill(settings, space, SPACE)
    _fill(settings, myths, ["Zeus", "Hera", "Olympus", "Athena", "Apollo God"])
    personal = manager.define_kb("Ann home", account_id=2, is_personal=True, is_public=False).id
    conn = dbm.connect(settings.db_file)
    try:
        documents = {"korea": ["Seoul", "Busan", "Incheon"], "myth": ["Zeus", "Hera", "Olympus", "Cooking"], "mixed": ["Seoul", "Cooking", "Gardening", "Chess"], "short": ["Seoul", "Busan"]}
        for doc_id in documents:
            conn.execute("INSERT INTO documents (id, kb_id, title, raw_text, hidden) VALUES (?, ?, ?, 'text', 0)", (doc_id, personal, doc_id))
        names = sorted({n for ns in documents.values() for n in ns})
        for i, name in enumerate(names):
            sources = [d for d, ns in documents.items() if name in ns]
            conn.execute("INSERT INTO entities (id, kb_id, type, name, norm_name, aliases, sources, created_at, updated_at) VALUES (?, ?, 'Concept', ?, ?, '[]', ?, 1, 1)", (f"ent_p_{i}", personal, name, normalize_name(name), json.dumps(sources)))
        conn.commit()
        report = tj.run_judgement(conn)
        conn.commit()
        themes = {tuple(m["kb_id"] for m in themes_store.theme_members(conn, t["id"])): t for t in themes_store.list_themes(conn)}
        matches = {d: themes_store.get_document_match(conn, d) for d in documents}
        # The document with the entities of a theme matches that theme; a fiction-like document matches a ``fictional`` theme.
        assert (matches["korea"]["theme_id"], matches["korea"]["knowledge_type"], matches["korea"]["score"]) == (themes[(korea,)]["id"], "real", pytest.approx(1.0))
        assert (matches["myth"]["theme_id"], matches["myth"]["knowledge_type"]) == (themes[(myths,)]["id"], "fictional")
        # Below the threshold, and with fewer than 3 keys: no match.
        assert matches["mixed"]["theme_id"] is None and matches["short"]["theme_id"] is None
        assert report.document_matches == 2 and themes_store.fiction_like_documents(conn, personal) == {"myth"}

        # The match of one new document, as the ingest job computes it after the extraction.
        conn.execute("INSERT INTO documents (id, kb_id, title, raw_text, hidden) VALUES ('new', ?, 'new', 'text', 0)", (personal,))
        for i, name in enumerate(SPACE[:3]):
            conn.execute("INSERT INTO entities (id, kb_id, type, name, norm_name, aliases, sources, created_at, updated_at) VALUES (?, ?, 'Concept', ?, ?, '[]', '[\"new\"]', 1, 1)", (f"ent_n_{i}", personal, name, normalize_name(name)))
        match = tj.match_document(conn, kb_id=personal, document_id="new")
        assert match.theme_id == themes[(space,)]["id"] and themes_store.get_document_match(conn, "new")["theme_id"] == match.theme_id

        # Seed entities (rule 5): the part of the seed names that the document has.
        matcher = tj.DocumentMatcher.load(conn)
        keys = tj.document_keys(conn, personal, "korea")
        assert matcher.seed_score(keys, ["Seoul", "Busan"]) == pytest.approx(1.0)
        assert 0.0 < matcher.seed_score(keys, ["Seoul", "Mars"]) < 1.0 and matcher.seed_score(keys, ["Mars"]) == 0.0 and matcher.seed_score(keys, []) == 0.0
    finally:
        conn.close()


def test_settings_and_schedule(env) -> None:
    settings, manager = env
    conn = dbm.connect(settings.db_file)
    try:
        assert tj.read_settings(conn) == tj.SETTINGS
        tj.write_setting(conn, "theme_match_threshold", 0.4, updated_by=1)
        assert tj.read_settings(conn)["theme_match_threshold"] == 0.4
        for key, value in (("unknown", 0.5), ("theme_match_threshold", 1.5), ("theme_match_threshold", True)):
            with pytest.raises(ValueError):
                tj.write_setting(conn, key, value, updated_by=1)
        # A run is due at first, not after a run, again after a day, and when a knowledge base has 20 new visible documents.
        assert tj.run_is_due(conn) is True
        tj.run_judgement(conn)
        conn.commit()
        assert tj.run_is_due(conn) is False and tj.run_is_due(conn, now=time.time() + tj.RUN_INTERVAL_SECONDS + 1) is True
    finally:
        conn.close()
    kb = manager.define_kb("Busy", account_id=1, knowledge_type="real").id
    _fill(settings, kb, KOREA, documents=tj.RUN_NEW_DOCUMENTS)
    conn = dbm.connect(settings.db_file)
    try:
        assert tj.run_is_due(conn) is True
    finally:
        conn.close()


# --- routes and pages (Sections 8.3 and 8.4) -------------------------------------------------------------------------


def test_e2e_theme_pages_and_admin_adjustments(env) -> None:
    from starlette.testclient import TestClient

    from claire.api.server import create_app
    from claire.store import accounts as accounts_store
    from claire.store.token_store import get_token_store, reset_token_stores

    reset_token_stores()
    settings, manager = env
    conn = dbm.connect(settings.db_file)
    try:
        conn.execute("UPDATE accounts SET username='admin', status='active', profile_public=1 WHERE id=1")
        accounts_store.AccountStore(conn).set_setting("mode", "network", updated_by=1)
    finally:
        conn.close()
    korea_a = manager.define_kb("Korea A", account_id=1, knowledge_type="real")
    korea_b = manager.define_kb("Korea B", account_id=2, knowledge_type="real")
    space = manager.define_kb("Space", account_id=1, knowledge_type="real")
    myths = manager.define_kb("Myths", account_id=1, knowledge_type="fictional")
    for kb, names in ((korea_a, KOREA), (korea_b, KOREA), (space, SPACE), (myths, ["Zeus", "Hera", "Olympus", "Athena", "Ares"])):
        _fill(settings, kb.id, names)
    tokens = get_token_store(data_dir=settings.data_dir)
    admin = {"Authorization": f"Bearer {tokens.create_api_token(name='admin', scope='owner', account_id=1)['token']}"}
    member = {"Authorization": f"Bearer {tokens.create_api_token(name='ann', scope='owner', account_id=2)['token']}"}
    try:
        with TestClient(create_app(settings=settings), base_url="http://127.0.0.1:8765", follow_redirects=False) as client:
            # Only the admin starts a run and sees the admin view.
            assert client.post("/api/admin/themes/judge", headers=member).status_code == 404
            report = client.post("/api/admin/themes/judge", headers=admin).json()["report"]
            assert (report["signatures"], report["themes_active"]) == (4, 3)
            overview = client.get("/api/admin/themes", headers=admin).json()
            assert overview["settings"] == {**tj.SETTINGS, **contributions.LIMITS} and overview["last_run_at"] is not None
            by_label = {t["label"]: t for t in overview["themes"]}
            korea, cosmos, legends = by_label["Seoul · Busan"], by_label["Mars · Jupiter"], by_label["Zeus · Hera"]

            # The collection and the page of a theme, for each reader.
            listed = client.get("/themes").json()["themes"]
            assert {t["number"] for t in listed} == {korea["number"], cosmos["number"], legends["number"]}
            assert [t["number"] for t in client.get("/themes?knowledge_type=fictional").json()["themes"]] == [legends["number"]]
            assert client.get(f"/themes/{korea['number']}").status_code == 308  # the path without the last slash
            page = client.get(f"/themes/{korea['number']}/").json()
            assert (page["label"], page["knowledge_type"], page["shared_knowledge_bases"], page["top_entities"][:2]) == ("Seoul · Busan", "real", 2, ["Seoul", "Busan"])
            assert sorted(kb["label"] for kb in page["knowledge_bases"]) == ["Korea A", "Korea B"] and "pinned" not in page["knowledge_bases"][0]
            assert page["knowledge_bases"][0]["url"].endswith(f"/kb/{ids_number(korea_a)}/")
            html_page = client.get(f"/themes/{korea['number']}/", headers={"Accept": "text/html"})
            # The HTML form is the page of the public views; its script shows the JSON form of the same path.
            assert html_page.status_code == 200 and "<title>Seoul · Busan" in html_page.text and 'data-public-view="theme_detail"' in html_page.text and f'"ref": "{korea["number"]}"' in html_page.text
            assert client.get("/themes/999999/", headers={"Accept": "text/html"}).status_code == 404
            # The root page of network mode shows the themes first.
            root = client.get("/", headers={"Accept": "text/html"})
            assert root.status_code == 200 and f'/themes/{korea["number"]}/' in root.text and "Mars · Jupiter" in root.text
            # An unknown number, and an inner path of a theme: 404.
            assert client.get("/themes/999999/").status_code == 404
            assert client.get(f"/themes/{korea['number']}/documents").status_code == 404

            # Adjustments (Section 8.3): rename with a lock, pin, exclude; each one stays after a new run.
            renamed = client.patch(f"/api/admin/themes/{korea['number']}", json={"label": "Korean geography"}, headers=admin).json()["theme"]
            assert (renamed["label"], renamed["label_locked"]) == ("Korean geography", True)
            member_url = f"/api/admin/themes/{korea['number']}/knowledge-bases"
            assert client.put(f"{member_url}/{ids_number(space)}", json={"membership": "pinned"}, headers=admin).status_code == 200
            assert client.put(f"{member_url}/{ids_number(korea_b)}", json={"membership": "excluded"}, headers=admin).status_code == 200
            # A theme has one knowledge type.
            assert client.put(f"{member_url}/{ids_number(myths)}", json={"membership": "pinned"}, headers=admin).json() == {"error": "knowledge_type_mismatch"}
            assert client.put(f"{member_url}/{ids_number(space)}", json={"membership": "maybe"}, headers=admin).json() == {"error": "membership_invalid"}
            client.post("/api/admin/themes/judge", headers=admin)
            page = client.get(f"/themes/{korea['number']}/").json()
            assert (page["label"], sorted(kb["label"] for kb in page["knowledge_bases"])) == ("Korean geography", ["Korea A", "Space"])
            admin_view = {t["number"]: t for t in client.get("/api/admin/themes", headers=admin).json()["themes"]}[korea["number"]]
            assert {kb["label"]: (kb["pinned"], kb["excluded"]) for kb in admin_view["knowledge_bases"]} == {"Korea A": (False, False), "Space": (True, False), "Korea B": (False, True)}

            # Split: the admin makes a theme and pins a knowledge base to it. Merge: the page of the merged theme gives 308.
            made = client.post("/api/admin/themes", json={"label": "Planets", "knowledge_type": "real"}, headers=admin)
            assert made.status_code == 201
            planets = made.json()["theme"]
            assert client.put(f"/api/admin/themes/{planets['number']}/knowledge-bases/{ids_number(space)}", json={"membership": "pinned"}, headers=admin).status_code == 200
            merged = client.post(f"/api/admin/themes/{cosmos['number']}/merge", json={"into": planets["number"]}, headers=admin)
            assert merged.status_code == 200, merged.text
            old = client.get(f"/themes/{cosmos['number']}/")
            assert (old.status_code, old.headers["location"]) == (308, f"/themes/{planets['number']}/")
            assert client.post(f"/api/admin/themes/{legends['number']}/merge", json={"into": planets["number"]}, headers=admin).json() == {"error": "knowledge_type_mismatch"}
            client.post("/api/admin/themes/judge", headers=admin)
            assert client.get(f"/themes/{cosmos['number']}/").status_code == 308
            assert [kb["label"] for kb in client.get(f"/themes/{planets['number']}/").json()["knowledge_bases"]] == ["Space"]

            # Thresholds are admin settings.
            changed = client.put("/api/admin/themes/settings", json={"theme_match_threshold": 0.9}, headers=admin)
            assert changed.json()["settings"]["theme_match_threshold"] == 0.9
            for body in ({}, {"other": 0.5}, {"theme_match_threshold": 2}):
                assert client.put("/api/admin/themes/settings", json=body, headers=admin).json() == {"error": "settings_invalid"}
    finally:
        reset_token_stores()


def ids_number(kb) -> str:
    from claire.core import ids

    return ids.format_public_id(kb.number)
