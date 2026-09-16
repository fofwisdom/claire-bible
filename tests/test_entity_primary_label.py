"""Unit and integration tests for selecting entity primary representative label from existing aliases."""

import json
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from claire.api import security, server
from claire.ontology.base import Entity
from claire.store import db as dbm


def test_select_entity_primary_label_success(tmp_path: Path):
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)

    # 노드 생성: 대표명='비련의 애도자', 별칭=['비애의 연극인', 'Mourning Actors']
    ent = Entity(
        id="ent_test_1",
        type="Faction",
        name="비련의 애도자",
        aliases=["비애의 연극인", "Mourning Actors"],
        observations=["환락의 에이언즈 아하를 따르지 않는 비극의 집단"],
        sources=["doc_1"],
    )
    dbm.upsert_entity(conn, ent)

    # 대표 레이블을 기존 별칭인 '비애의 연극인'으로 전환
    updated = dbm.select_entity_primary_label(conn, "ent_test_1", "비애의 연극인")

    assert updated.name == "비애의 연극인"
    assert updated.norm_name == "비애의 연극인"
    # 이전 대표 레이블인 '비련의 애도자'가 별칭으로 보존되어야 함
    assert "비련의 애도자" in updated.aliases
    assert "Mourning Actors" in updated.aliases
    # 새 대표명이 된 '비애의 연극인'은 별칭 목록에서 제거되어야 함
    assert "비애의 연극인" not in updated.aliases

    # DB에서 다시 조회하여 영속화 확인
    reloaded = dbm.get_entity(conn, "ent_test_1")
    assert reloaded is not None
    assert reloaded.name == "비애의 연극인"
    assert "비련의 애도자" in reloaded.aliases

    # FTS 검색 동기화 확인
    cur = conn.execute("SELECT entity_id, name FROM entities_fts WHERE entities_fts MATCH ?", ("연극인",))
    rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0][1] == "비애의 연극인"

    conn.close()


def test_select_entity_primary_label_by_name(tmp_path: Path):
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)

    ent = Entity(
        id="ent_test_2",
        type="Tool",
        name="FA2",
        aliases=["FlashAttention-2", "FlashAttention2"],
    )
    dbm.upsert_entity(conn, ent)

    # ID 대신 현재 이름으로 호출해도 성공
    updated = dbm.select_entity_primary_label(conn, "FA2", "FlashAttention-2")
    assert updated.name == "FlashAttention-2"
    assert "FA2" in updated.aliases
    assert "FlashAttention2" in updated.aliases

    conn.close()


def test_select_entity_primary_label_rejects_non_existing_alias(tmp_path: Path):
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)

    ent = Entity(
        id="ent_test_3",
        type="Concept",
        name="정의",
        aliases=["별칭A", "별칭B"],
    )
    dbm.upsert_entity(conn, ent)

    # 존재하지 않는 임의 별칭은 거부 (ValueError)
    with pytest.raises(ValueError, match="기존 별칭 목록에 존재하지 않습니다"):
        dbm.select_entity_primary_label(conn, "ent_test_3", "임의의_새_이름")

    # DB의 원래 상태가 유지되는지 확인
    reloaded = dbm.get_entity(conn, "ent_test_3")
    assert reloaded.name == "정의"
    assert reloaded.aliases == ["별칭A", "별칭B"]

    conn.close()


def test_batch_select_primary_labels(tmp_path: Path):
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)

    ent1 = Entity(id="ent_b1", type="Concept", name="구이름1", aliases=["신이름1", "기타"])
    ent2 = Entity(id="ent_b2", type="Concept", name="구이름2", aliases=["신이름2"])
    dbm.upsert_entity(conn, ent1)
    dbm.upsert_entity(conn, ent2)

    mappings = {
        "ent_b1": "신이름1",
        "구이름2": "신이름2",
        "non_existent": "신이름X",
        "ent_b1_invalid": "미등록별칭",
    }

    # 1. Dry Run 테스트
    sim = dbm.batch_select_primary_labels(conn, mappings, dry_run=True)
    assert sim["dry_run"] is True
    assert sim["total"] == 4
    assert sim["planned"] == 2
    assert sim["failed"] == 2

    # DB는 여전히 변경되지 않았어야 함
    assert dbm.get_entity(conn, "ent_b1").name == "구이름1"
    assert dbm.get_entity(conn, "ent_b2").name == "구이름2"

    # 2. Actual Apply 테스트
    res = dbm.batch_select_primary_labels(conn, mappings, dry_run=False)
    assert res["dry_run"] is False
    assert res["planned"] == 2

    # DB에 정상 적용 확인
    assert dbm.get_entity(conn, "ent_b1").name == "신이름1"
    assert "구이름1" in dbm.get_entity(conn, "ent_b1").aliases
    assert dbm.get_entity(conn, "ent_b2").name == "신이름2"
    assert "구이름2" in dbm.get_entity(conn, "ent_b2").aliases

    conn.close()


def test_api_primary_label_endpoint(tmp_path: Path):
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)

    ent = Entity(
        id="ent_api_1",
        type="Character",
        name="구이름",
        aliases=["선택할별칭", "다른별칭"],
    )
    dbm.upsert_entity(conn, ent)
    conn.close()

    owner_token = "owner-" + ("o" * 32)
    readonly_token = "readonly-" + ("r" * 32)

    class StubSettings:
        def __init__(self):
            self.db_file = db_file
            self.data_dir = tmp_path
            self.environment = "development"
            self.public_url = "http://127.0.0.1:8765"
            self.inject_token = owner_token
            self.readonly_token = readonly_token
            self.cors_allowed_origins = ""
            self.anonymous_readonly = False
            self.multi_theme = False

    class StubService:
        def __init__(self):
            self.provider = None

    settings = StubSettings()
    app = server.create_app(settings, StubService())
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    readonly_headers = {"Authorization": f"Bearer {readonly_token}"}

    with TestClient(security.wrap_web_app(app, settings), base_url=settings.public_url) as client:
        # 1. Readonly 사용자는 fail-closed(404) 또는 401/403 거부되어야 함
        r_ro = client.post(
            "/entity/primary-label",
            json={"id": "ent_api_1", "name": "선택할별칭"},
            headers=readonly_headers,
        )
        assert r_ro.status_code in (401, 403, 404)

        # 2. 존재하지 않는 별칭 지정 시 400 반환
        r_bad = client.post(
            "/entity/primary-label",
            json={"id": "ent_api_1", "name": "존재하지않는별칭"},
            headers=owner_headers,
        )
        assert r_bad.status_code == 400

        # 3. 정상 별칭 지정 시 성공 및 갱신된 노드 상세 반환
        r_ok = client.post(
            "/entity/primary-label",
            json={"id": "ent_api_1", "name": "선택할별칭"},
            headers=owner_headers,
        )
        assert r_ok.status_code == 200
        data = r_ok.json()
        assert data["name"] == "선택할별칭"
        assert "구이름" in data["aliases"]
        assert "선택할별칭" not in data["aliases"]


def test_cli_entity_select_and_batch(tmp_path: Path, monkeypatch, capsys):
    from claire.cli import main

    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)

    ent = Entity(
        id="ent_cli_1",
        type="Tool",
        name="OldTool",
        aliases=["NewTool", "AltTool"],
    )
    dbm.upsert_entity(conn, ent)
    conn.close()

    # Settings mock
    from claire.config import Settings
    s = Settings(
        CLAIRE_DATA_DIR=str(tmp_path),
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_PROVIDER="mock",
        CLAIRE_MULTI_THEME=False,
    )
    monkeypatch.setattr("claire.cli.get_settings", lambda: s)

    # 1. CLI select-label Dry Run
    rc = main(["entity", "select-label", "ent_cli_1", "--to", "NewTool", "--dry-run"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "dry-run" in captured.out
    assert "OldTool" in captured.out

    # 2. CLI select-label Apply
    rc = main(["entity", "select-label", "ent_cli_1", "--to", "NewTool", "--apply"])
    assert rc == 0

    # DB 확인
    conn = dbm.connect(db_file)
    check_ent = dbm.get_entity(conn, "ent_cli_1")
    assert check_ent.name == "NewTool"
    assert "OldTool" in check_ent.aliases
    conn.close()

    # 3. CLI batch-labels Apply
    rules_file = tmp_path / "rules.json"
    rules_file.write_text(json.dumps({"NewTool": "AltTool"}), encoding="utf-8")

    rc = main(["entity", "batch-labels", "--rules", str(rules_file), "--apply"])
    assert rc == 0

    conn = dbm.connect(db_file)
    check_ent2 = dbm.get_entity(conn, "ent_cli_1")
    assert check_ent2.name == "AltTool"
    assert "NewTool" in check_ent2.aliases
    conn.close()
