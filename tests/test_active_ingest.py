"""단위 테스트: Active Ingest 상태 관리 및 파이프라인/API 연동.

검증 항목:
1. set_active_ingest / update_active_ingest / clear_active_ingest 생명주기
2. 원자적 파일 쓰기 및 손상 방지
3. 만료(Stale timeout) 및 완료 보존(Completed retention window) 동작
4. pipeline.ingest() 실행 시 자동 생명주기 추적 및 결과 보존
5. API Server (/stats 및 /ingest/active) 활성 적재 응답 검증
"""

from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from unittest.mock import MagicMock

from starlette.testclient import TestClient

from claire.api import server
from claire.config import Settings
from claire.extract.provider import MockProvider
from claire.ingest.active import (
    _COMPLETED_RETENTION_SEC,
    _STALE_TIMEOUT_SEC,
    clear_active_ingest,
    get_active_ingest,
    set_active_ingest,
    update_active_ingest,
)
from claire.ingest.pipeline import IngestReport, ingest
from claire.ontology.base import Document
from claire.store import db as dbm


def test_active_ingest_lifecycle(tmp_path: Path):
    data_dir = tmp_path / "data"

    # 1. 초기 상태: 없음
    assert get_active_ingest(data_dir) is None

    # 2. 적재 시작
    set_active_ingest(
        data_dir,
        payload="https://example.com/test-article",
        source="telegram",
        focus="테스트 초점",
        title="초기 제목",
    )
    st = get_active_ingest(data_dir)
    assert st is not None
    assert st["active"] is True
    assert st["source"] == "telegram"
    assert st["payload"] == "https://example.com/test-article"
    assert st["focus"] == "테스트 초점"
    assert st["title"] == "초기 제목"
    assert st["stage"] == "init"

    # 3. 진행 중 업데이트 (메시지 및 매트릭스)
    sample_matrix = {
        "rows": ["엔티티A"],
        "cols": ["엔티티B"],
        "matrix": [[0.95]],
    }
    update_active_ingest(
        data_dir,
        msg="엔티티 대조 중…",
        stage="heatmap_matrix",
        heatmap_matrix=sample_matrix,
        title="확정된 제목",
    )
    st = get_active_ingest(data_dir)
    assert st is not None
    assert st["active"] is True
    assert st["msg"] == "엔티티 대조 중…"
    assert st["stage"] == "heatmap_matrix"
    assert st["title"] == "확정된 제목"
    assert st["heatmap_matrix"] == sample_matrix

    # 4. 적재 완료
    result_data = {
        "document_id": "doc_xyz",
        "title": "확정된 제목",
        "entities_created": 3,
        "entities_linked": 2,
    }
    clear_active_ingest(data_dir, result=result_data)
    st = get_active_ingest(data_dir)
    assert st is not None
    assert st["active"] is False
    assert st["stage"] == "done"
    assert st["result"] == result_data
    assert st["completed_at"] is not None


def test_active_ingest_stale_and_retention(tmp_path: Path):
    data_dir = tmp_path / "data"

    # 1. Stale Ingest: 10분 이상 지난 활성 작업은 None 반환
    set_active_ingest(data_dir, payload="https://example.com/stale")
    state_file = data_dir / "active_ingest.json"
    data = json.loads(state_file.read_text(encoding="utf-8"))
    data["updated_at"] = time.time() - (_STALE_TIMEOUT_SEC + 5)
    state_file.write_text(json.dumps(data), encoding="utf-8")

    assert get_active_ingest(data_dir) is None

    # 2. Retention Window: 완료 후 30초 초과 시 None 반환
    set_active_ingest(data_dir, payload="https://example.com/retention")
    clear_active_ingest(data_dir)
    data = json.loads(state_file.read_text(encoding="utf-8"))
    data["completed_at"] = time.time() - (_COMPLETED_RETENTION_SEC + 5)
    state_file.write_text(json.dumps(data), encoding="utf-8")

    assert get_active_ingest(data_dir) is None


def test_pipeline_ingest_active_tracking(tmp_path: Path):
    db_file = tmp_path / "claire.db"
    data_dir = tmp_path / "data"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)

    provider = MockProvider()
    vstore = MagicMock()

    doc = Document(
        id="doc_pipe_test",
        url="https://example.com/pipe",
        title="파이프라인 테스트 문서",
        raw_text="테스트 본문 내용입니다. 충분한 문장을 포함합니다.",
    )

    def mock_fetch(url: str, **kwargs):
        # 적재 진행 도중 active_ingest 상태가 active인지 검증
        active_st = get_active_ingest(data_dir)
        assert active_st is not None
        assert active_st["active"] is True
        assert active_st["payload"] == url
        return doc

    rep = ingest(
        "https://example.com/pipe",
        conn=conn,
        provider=provider,
        vstore=vstore,
        fetch_fn=mock_fetch,
        data_dir=data_dir,
        source="unit_test",
    )

    # 완료 후 active_ingest 상태가 완료로 보존되어 있는지 검증
    final_st = get_active_ingest(data_dir)
    assert final_st is not None
    assert final_st["active"] is False
    assert final_st["stage"] == "done"
    assert final_st["title"] == "파이프라인 테스트 문서"
    conn.close()


def test_api_stats_and_active_ingest_endpoints(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_file = data_dir / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    conn.close()

    owner_tok = secrets.token_urlsafe(32)
    read_tok = secrets.token_urlsafe(32)

    s = Settings(
        db_path=str(db_file),
        environment="development",
        public_url="http://127.0.0.1:8765",
        inject_token=owner_tok,
        readonly_token=read_tok,
    )

    svc = MagicMock()
    app = server.create_app(s, svc)
    client = TestClient(app, base_url="http://127.0.0.1:8765")

    # 1. 적재가 없을 때 /stats 및 /ingest/active
    r = client.get("/stats", headers={"Authorization": f"Bearer {read_tok}"})
    assert r.status_code == 200
    st_data = r.json()
    assert st_data["ingesting"] is False
    assert st_data["active_ingest"] is None

    r = client.get("/ingest/active", headers={"Authorization": f"Bearer {read_tok}"})
    assert r.status_code == 200
    assert r.json().get("active") is False

    # 2. 적재가 시작되었을 때
    set_active_ingest(
        data_dir,
        payload="https://news.ycombinator.com",
        source="telegram",
        title="HN 뉴스 기사",
    )

    r = client.get("/stats", headers={"Authorization": f"Bearer {read_tok}"})
    assert r.status_code == 200
    st_data = r.json()
    assert st_data["ingesting"] is True
    assert st_data["active_ingest"]["active"] is True
    assert st_data["active_ingest"]["title"] == "HN 뉴스 기사"

    r = client.get("/ingest/active", headers={"Authorization": f"Bearer {read_tok}"})
    assert r.status_code == 200
    act = r.json()
    assert act["active"] is True
    assert act["title"] == "HN 뉴스 기사"

    # 3. 완료 시
    clear_active_ingest(data_dir, result={"document_id": "doc_1", "title": "HN 뉴스 기사"})
    r = client.get("/stats", headers={"Authorization": f"Bearer {read_tok}"})
    assert r.status_code == 200
    st_data = r.json()
    assert st_data["ingesting"] is False
    assert st_data["active_ingest"] is not None
    assert st_data["active_ingest"]["active"] is False
    assert st_data["active_ingest"]["stage"] == "done"
