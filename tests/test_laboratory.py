"""LABORATORY_DESIGN.md: the run records (``laboratory.runs``, claire.db v47), the measurement M0 (``laboratory.stt_hints``: the interface to yt-dlp and to the STT provider), and the routes of ``/laboratory/*`` and ``/api/admin/laboratory/*``."""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from claire.api import server
from claire.core.config import Settings
from claire.laboratory import runs, stt_hints
from claire.store import accounts as accounts_store
from claire.store import auth_db
from claire.store import db as dbm
from claire.store.secrets import reset_secret_service
from tests.auth_helpers import bearer_headers

PUBLIC_URL = "http://127.0.0.1:8765"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch):
    reset_secret_service()
    for key in list(os.environ):
        if key.startswith(("CLAIRE_", "GEMINI_")):
            monkeypatch.delenv(key, raising=False)
    yield
    reset_secret_service()


def _db(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    db_file = data_dir / "claire.db"
    conn = dbm.connect(db_file)
    try:
        dbm.init_db(conn)
    finally:
        conn.close()
    return db_file


def test_run_records_one_at_a_time_and_a_stale_run_fails(tmp_path: Path) -> None:
    conn = dbm.connect_existing(_db(tmp_path))
    try:
        first = runs.start(conn, kind="stt_hints", params={"url": "https://example.com/v"}, created_by=1, now=1000.0)
        assert first.status == "running"
        with pytest.raises(runs.RunInProgress):
            runs.start(conn, kind="stt_hints", params={}, created_by=1, now=1001.0)
        assert runs.delete(conn, first.id) is False
        runs.finish(conn, first.id, result={"agreement": 0.5}, now=1002.0)
        assert runs.get(conn, first.id).result == {"agreement": 0.5}
        # A run that runs longer than STALE_AFTER_SECONDS stopped with its process: it shows as failed and the next run can start.
        second = runs.start(conn, kind="stt_hints", params={}, created_by=1, now=2000.0)
        third = runs.start(conn, kind="stt_hints", params={}, created_by=1, now=2000.0 + runs.STALE_AFTER_SECONDS + 1)
        assert runs.get(conn, second.id).status == "failed" and runs.get(conn, second.id).error == "interrupted"
        runs.fail(conn, third.id, error="x" * 900)
        assert len(runs.get(conn, third.id).error or "") == 500
        assert [r.id for r in runs.recent(conn)] == [third.id, second.id, first.id]
        assert runs.delete(conn, first.id) is True
    finally:
        conn.close()


class _FakeTranscript(SimpleNamespace):
    pass


def test_measure_transcribes_the_same_audio_without_and_with_hints(tmp_path: Path) -> None:
    calls: list[list[str] | None] = []

    class Provider:
        model = "gemini-3.5-transcribe"

        def transcribe(self, audio, *, language=None, timestamps=True, language_codes=None):
            calls.append(language_codes)
            assert Path(audio).name == "audio.m4a"
            text = "[00:01] 안녕하세요 Claire" if language_codes else "[00:01] annyeonghaseyo Claire"
            return _FakeTranscript(full_text=text, segments=[1])

    def downloader(url, settings, workdir):
        audio = workdir / "audio.m4a"
        audio.write_bytes(b"x")
        return audio, 42.0

    result = stt_hints.measure(SimpleNamespace(), url="https://example.com/v", hints=["ko", "en"], downloader=downloader, provider_factory=lambda s: Provider())
    assert calls == [[], ["ko", "en"]]
    assert result["duration_sec"] == 42.0 and result["model"] == "gemini-3.5-transcribe"
    assert result["variants"]["none"]["scripts"]["latin"] == 1.0
    assert result["variants"]["hints"]["scripts"]["hangul"] == 0.455 and result["variants"]["hints"]["language_codes"] == ["ko", "en"]
    assert 0.0 < result["agreement"] < 1.0
    assert stt_hints.agreement("[00:01] Same text", "[00:09] same   TEXT") == 1.0


def test_start_run_checks_the_input_and_records_the_result_or_the_failure(tmp_path: Path) -> None:
    db_file = _db(tmp_path)
    for url, hints, code in (("ftp://x", ["ko"], "invalid_url"), ("https://example.com/v", ["ko!"], "invalid_languages")):
        with pytest.raises(stt_hints.LabRunError) as err:
            stt_hints.start_run(db_file, SimpleNamespace(), url=url, hints=hints, created_by=1, background=False)
        assert err.value.code == code
    done = stt_hints.start_run(db_file, SimpleNamespace(), url="https://example.com/v", hints=["JA"], created_by=1, measure_fn=lambda s, url, hints: {"agreement": 1.0, "hints": hints}, background=False)
    failed = stt_hints.start_run(db_file, SimpleNamespace(), url="https://example.com/v", hints=["ko"], created_by=1, measure_fn=lambda s, url, hints: (_ for _ in ()).throw(stt_hints.LabRunError("audio_too_long", "400 s")), background=False)
    conn = dbm.connect_existing(db_file)
    try:
        assert runs.get(conn, done.id).status == "done" and runs.get(conn, done.id).result == {"agreement": 1.0, "hints": ["ja", "en"]}
        assert runs.get(conn, done.id).params["hints"] == ["ja", "en"]
        assert (runs.get(conn, failed.id).status, runs.get(conn, failed.id).error) == ("failed", "audio_too_long: 400 s")
    finally:
        conn.close()


def _app(tmp_path: Path, *, gemini_key: str = "") -> SimpleNamespace:
    db_file = _db(tmp_path)
    data_dir = db_file.parent
    conn = dbm.connect_existing(db_file)
    try:
        accounts_store.AccountStore(conn).complete_setup(1, username="admin_lab", display_name="Admin")
        now = time.time()
        conn.execute(
            "INSERT INTO accounts (id, public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) VALUES (2, 223456789066, 'member_lab', 'member', 'active', 'Member', 0, ?, ?, ?)",
            (now, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    auth_db.connect_auth_db(auth_db.auth_db_path(data_dir)).close()
    settings = Settings(_env_file=None, CLAIRE_DB_PATH=str(db_file), CLAIRE_PROVIDER="mock", CLAIRE_ENVIRONMENT="development", CLAIRE_FQDN=PUBLIC_URL, GEMINI_API_KEY=gemini_key)
    admin = {**bearer_headers(db_file, account_id=1, scope="owner"), "Origin": PUBLIC_URL}
    member = {**bearer_headers(db_file, account_id=2, scope="owner"), "Origin": PUBLIC_URL}
    client = TestClient(server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="mock"))), base_url=PUBLIC_URL)
    return SimpleNamespace(client=client, admin=admin, member=member, db_file=db_file)


def test_laboratory_pages_and_api_are_for_the_administrator_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _app(tmp_path, gemini_key="AIzaLaboratoryKey1234567890abcdef")
    monkeypatch.setenv("CLAIRE_DB_PATH", str(env.db_file))
    with env.client as client:
        for path in ("/laboratory", "/laboratory/measurements", "/laboratory/shadows", "/api/admin/laboratory/runs"):
            assert client.get(path, headers=env.member, follow_redirects=False).status_code == 404, path
        assert client.post("/api/admin/laboratory/stt-hints", headers=env.member, json={"url": "https://example.com/v", "hints": ["ko"]}).status_code == 404
        assert client.get("/laboratory", headers=env.admin, follow_redirects=False).headers["location"] == "/laboratory/measurements"

        page = client.get("/laboratory/measurements", headers=env.admin).text
        assert 'id="lab-stt-hints"' in page and 'id="lab-runs"' in page and "data-lab-empty" in page
        assert "/static/js/laboratory.js" in page and "/static/js/console_admin.js" in page and 'data-nav-group="laboratory"' in page
        shadows = client.get("/laboratory/shadows", headers=env.admin).text
        for card in ('id="lab-jev"', 'id="lab-merge"', 'id="admin-expansion-review-host"', 'id="lab-embedding"', 'data-lab-merge-state="off"', 'data-lab-jev-state="off"'):
            assert card in shadows, card
        # The review moved: /admin/limits has no review card.
        assert 'id="admin-expansion-review-host"' not in client.get("/admin/limits", headers=env.admin).text

        # A run with a measurement that returns at once (the yt-dlp download and the STT calls are tested above).
        monkeypatch.setattr(stt_hints, "measure", lambda settings, url, hints: {"agreement": 0.9, "variants": {}, "duration_sec": 10.0, "model": "m"})
        bad = client.post("/api/admin/laboratory/stt-hints", headers=env.admin, json={"url": "nope", "hints": ["ko"]})
        assert (bad.status_code, bad.json()["error"]) == (400, "invalid_url")
        started = client.post("/api/admin/laboratory/stt-hints", headers=env.admin, json={"url": "https://example.com/v", "hints": ["ko"]})
        assert started.status_code == 202, started.text
        run_id = started.json()["run"]["id"]
        for _ in range(50):
            run = client.get(f"/api/admin/laboratory/runs/{run_id}", headers=env.admin).json()["run"]
            if run["status"] != "running":
                break
            time.sleep(0.05)
        assert run["status"] == "done" and run["result"]["agreement"] == 0.9
        listed = client.get("/laboratory/measurements", headers=env.admin).text
        assert f'data-lab-run="{run_id}"' in listed and "data-lab-agreement" in listed and "90.0%" in listed
        assert client.delete(f"/api/admin/laboratory/runs/{run_id}", headers=env.admin).status_code == 200
        assert client.get(f"/api/admin/laboratory/runs/{run_id}", headers=env.admin).status_code == 404


def test_measurement_needs_the_server_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _app(tmp_path)
    monkeypatch.setenv("CLAIRE_DB_PATH", str(env.db_file))
    with env.client as client:
        refused = client.post("/api/admin/laboratory/stt-hints", headers=env.admin, json={"url": "https://example.com/v", "hints": ["ko"]})
        assert (refused.status_code, refused.json()["error"]) == (409, "no_api_key")
        assert "data-lab-no-key" in client.get("/laboratory/measurements", headers=env.admin).text
