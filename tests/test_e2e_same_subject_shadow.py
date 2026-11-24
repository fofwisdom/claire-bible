"""E2E: the shadow mode of the same-subject source merge, from the setting to the report (docs/implementation/EXPANSION.md §3.1).

The administrator sets the mode "shadow" through the limits API, automatic expansion records the judgements, the administrator gives verdicts through the admin API of the WebUI, and ``claire expand-shadow report`` (read only, for agents) gives precision and recall. The CLI has no verdict command: a verdict is the decision of a person. The mock provider judges: no trigger word = same subject, "별개주제" = different subject.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from claire import cli
from claire.api.server import create_app
from claire.core.config import Settings, get_settings
from claire.ingest import pipeline as pipemod
from claire.ingest import service as svcmod
from claire.ingest.service import IngestService
from claire.ontology.base import Document
from claire.store import db as dbm
from tests.auth_helpers import bearer_headers

PARENT = "https://parent.example/post"
SAME = "https://same.example/repo"
OTHER = "https://other.example/post"
BODY = "유익하고 구체적인 본문 내용입니다. " * 20


def _doc(url: str, text: str, links=None) -> Document:
    return Document(url=url, canonical_url=url, title=url.rsplit("/", 1)[-1], raw_text=text, source_type="web", content_hash=str(hash(text)), meta={"links": links} if links else {})


def _fetch(payload: str) -> Document:
    if payload == PARENT:
        return _doc(PARENT, "부모 문서 본문 " * 30, links=[SAME, OTHER])
    if payload == SAME:
        return _doc(SAME, "그 프로젝트의 github 저장소 본문. " + BODY)
    if payload == OTHER:
        return _doc(OTHER, "별개주제 — 다른 프로젝트. " + BODY)
    raise RuntimeError(f"unexpected fetch {payload}")


def _run(argv: list[str], capsys) -> tuple[int, str]:
    args = cli.build_parser().parse_args(argv)
    code = args.func(args)
    return code, capsys.readouterr().out


def test_shadow_mode_from_setting_to_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_file = data_dir / "claire.db"
    monkeypatch.setenv("CLAIRE_DB_PATH", str(db_file))
    monkeypatch.setenv("CB_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CLAIRE_PROVIDER", "mock")
    for module in (svcmod, pipemod):
        monkeypatch.setattr(module, "default_fetch", _fetch)
    get_settings.cache_clear()
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    conn.close()

    # 1. The server administrator selects the shadow mode. A merge mode is refused.
    settings = Settings(CLAIRE_ENVIRONMENT="development", CLAIRE_DB_PATH=str(db_file), CLAIRE_FQDN="127.0.0.1:8765", provider="mock")
    client = TestClient(create_app(settings), base_url="http://127.0.0.1:8765")
    headers = bearer_headers(db_file, scope="owner")
    assert client.get("/limits", headers=headers).json()["limits"]["same_subject_merge"] == "off"
    assert client.patch("/limits", headers=headers, json={"limits": {"same_subject_merge": "on"}}).status_code == 400
    assert client.patch("/limits", headers=headers, json={"limits": {"same_subject_merge": "shadow"}}).status_code == 200

    # 2. Automatic expansion stores both children and records the two judgements.
    get_settings.cache_clear()
    s = get_settings()
    assert s.same_subject_merge == "shadow"
    svc = IngestService(s)
    parent = svc.ingest(PARENT, source="cli", expand_max=0)
    result = svc.expand_document(parent.document_id)
    assert result["stored"] == 2 and result["shadowed"] == 2

    code, out = _run(["expand-shadow", "list", "--pending", "--json"], capsys)
    listed = json.loads(out)
    assert code == 0 and listed["summary"]["without_verdict"] == 2
    by_url = {r["child_url"]: r for r in listed["records"]}
    assert by_url[SAME]["same_subject"] is True and by_url[OTHER]["same_subject"] is False
    assert by_url[SAME]["parent_title"] == "post" and by_url[SAME]["child_title"] == "repo"

    # 3. The administrator gives the verdicts in the WebUI: the model was right about SAME and wrong about OTHER.
    shadow = client.get("/api/admin/expansion/shadow?pending=1", headers=headers).json()
    assert shadow["mode"] == "shadow" and shadow["report"]["pending"] == 2
    api_ids = {r["child"]["url"]: r["id"] for r in shadow["records"]}
    assert api_ids == {SAME: by_url[SAME]["id"], OTHER: by_url[OTHER]["id"]}
    put = client.put(f"/api/admin/expansion/shadow/{api_ids[SAME]}/verdict", headers=headers, json={"verdict": "same", "note": "official repository"})
    assert put.status_code == 200 and put.json()["record"]["verdict"] == "same" and put.json()["record"]["verdict_note"] == "official repository"
    assert client.put(f"/api/admin/expansion/shadow/{api_ids[OTHER]}/verdict", headers=headers, json={"verdict": "same"}).status_code == 200
    assert client.put("/api/admin/expansion/shadow/9999/verdict", headers=headers, json={"verdict": "same"}).status_code == 404
    assert client.put(f"/api/admin/expansion/shadow/{api_ids[OTHER]}/verdict", headers=headers, json={"verdict": "maybe"}).status_code == 400
    # The CLI of the agents has no verdict command.
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["expand-shadow", "verdict", str(api_ids[SAME]), "same"])
    capsys.readouterr()

    # 4. The report compares the model with the verdicts.
    code, out = _run(["expand-shadow", "report", "--json"], capsys)
    report = json.loads(out)
    assert code == 0
    assert report["mode"] == "shadow"
    assert (report["tp"], report["fp"], report["fn"], report["tn"]) == (1, 0, 1, 0)
    assert report["precision"] == 1.0 and report["recall"] == 0.5 and report["pending"] == 0
    code, out = _run(["expand-shadow", "report"], capsys)
    assert "precision 100.0% · recall 50.0% · accuracy 50.0%" in out
    get_settings.cache_clear()
