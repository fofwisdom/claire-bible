"""단위 테스트: Multi-Tier Safe Gating 해소기, 롤백 메커니즘 및 파이프라인 연동 검증.

검증 항목:
1. passes_hard_invariants: 타입 불일치, 2글자 모호 약어, 버전 충돌 차단
2. _execute_reversible_merge: 롤백 페이로드 생성 및 의사결정 기록
3. rollback_resolution: 롤백 페이로드를 사용한 엔티티 상태 무손실 원복
4. resolve_or_create: 5단계 게이트별(exact, alias, acronym, vector, judge, new) Decision 생성
5. pipeline 통합: 문서 적재 시 documents.meta["resolution_log"] 보관 및 document_detail 조회 검증
"""

from __future__ import annotations

import sqlite3

from claire.extract.decision import ResolutionDecision, get_resolution_log_from_meta, rollback_resolution
from claire.extract.resolver import (
    ResolutionResult,
    _execute_reversible_merge,
    passes_hard_invariants,
    resolve_or_create,
)
from claire.ingest.pipeline import IngestReport, ingest
from claire.ontology.base import Document, Entity
from claire.store import db as dbm
from claire.store.queries import document_detail
from claire.store.vectors import VectorStore


def _init_memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    return conn


def test_passes_hard_invariants():
    # 1) 타입 검증
    assert passes_hard_invariants("Docker", "Tool", "Podman", "Tool") is True
    assert passes_hard_invariants("Claude 3.5 Sonnet", "Model", "Claude Code", "Tool") is False
    # provisional 타입은 허용
    assert passes_hard_invariants("NewThing", "CustomType", "OldThing", "Tool", provisional=True) is True

    # 2) 2글자 약어 검증
    assert passes_hard_invariants("AI", "Concept", "Artificial Intelligence", "Concept") is False
    assert passes_hard_invariants("MCP", "Concept", "Model Context Protocol", "Concept") is True

    # 3) 버전 토큰 충돌 검증
    assert passes_hard_invariants("vSphere 8.0", "Product", "vSphere 7.0", "Product") is False
    assert passes_hard_invariants("ESXi 8.0 U2", "Product", "ESXi 7.0", "Product") is False
    assert passes_hard_invariants("vSphere 8.0", "Product", "vSphere 8.0", "Product") is True


def test_execute_reversible_merge_and_rollback():
    conn = _init_memory_db()
    ent = Entity(
        type="Tool",
        name="Letta",
        aliases=["MemGPT_Original"],
        observations=["Initial memory server"],
        sources=["doc_init"],
    )
    dbm.upsert_entity(conn, ent)

    # Reversible Merge 실행
    merged_ent, decision = _execute_reversible_merge(
        conn,
        cand=ent,
        name="Letta CLI",
        aliases=["MemGPT_Alias_New"],
        observations=["Updated observation"],
        document_id="doc_new",
        stage="exact_match",
        score=1.0,
        reason="Test merge",
    )

    assert "MemGPT_Alias_New" in merged_ent.aliases
    assert "Updated observation" in merged_ent.observations
    assert "doc_new" in merged_ent.sources
    assert decision.decision == "MERGE"
    assert decision.target_entity_id == ent.id
    assert decision.rollback_payload is not None
    assert decision.rollback_payload["added_aliases"] == ["MemGPT_Alias_New"]
    assert decision.rollback_payload["added_observations"] == ["Updated observation"]
    assert decision.rollback_payload["added_source"] == "doc_new"

    # Rollback 실행: 원래 상태로 복원되는지 검증
    ok = rollback_resolution(conn, decision)
    assert ok is True

    restored = dbm.get_entity(conn, ent.id)
    assert restored is not None
    assert "MemGPT_Alias_New" not in restored.aliases
    assert "MemGPT_Original" in restored.aliases
    assert "Updated observation" not in restored.observations
    assert "Initial memory server" in restored.observations
    assert "doc_new" not in restored.sources
    assert "doc_init" in restored.sources


def test_resolve_or_create_safe_gating_verdicts():
    conn = _init_memory_db()
    vstore = VectorStore(conn, "brute")

    # 1. Gate 1: Exact match
    e1 = Entity(type="Tool", name="Claude Code", aliases=[], observations=[], sources=["doc_1"])
    dbm.upsert_entity(conn, e1)

    res1 = resolve_or_create(
        conn, vstore,
        name="claude code", etype="Tool", aliases=[], observations=[], document_id="doc_2",
    )
    assert res1.created is False
    assert res1.entity.id == e1.id
    assert res1.decision is not None
    assert res1.decision.stage == "exact_match"
    assert res1.decision.decision == "MERGE"

    # 2. Gate 2: Hard invariant 차단 (버전 충돌)
    e2 = Entity(type="Product", name="vSphere 7.0", aliases=[], observations=[], sources=["doc_1"])
    dbm.upsert_entity(conn, e2)
    vstore.put(e2.id, [1.0, 0.0], "claire")

    # 높은 벡터 유사도(1.0)를 제공하더라도 버전이 8.0 vs 7.0으로 충돌하므로 병합 금지
    res2 = resolve_or_create(
        conn, vstore,
        name="vSphere 8.0", etype="Product", aliases=[], observations=[], document_id="doc_2",
        embed_fn=lambda: [1.0, 0.0],
    )
    assert res2.created is True  # 독립 노드로 생성됨
    assert res2.decision is not None
    assert res2.decision.decision == "CREATE_NEW"

    # 3. Gate 4: LLM Judge 경계선 판정
    e3 = Entity(type="Framework", name="Letta", aliases=[], observations=["Agent memory"], sources=["doc_1"])
    dbm.upsert_entity(conn, e3)
    vstore.put(e3.id, [1.0, 0.0, 0.0], "claire")

    # borderline 점수(0.8) & judge가 SAME 반환
    res3 = resolve_or_create(
        conn, vstore,
        name="Agent Memory Server", etype="Framework", aliases=[], observations=[], document_id="doc_3",
        embed_fn=lambda: [0.8, 0.6, 0.0],
        judge_fn=lambda nm, et, obs, cand: True,
    )
    assert res3.created is False
    assert res3.entity.id == e3.id
    assert res3.decision is not None
    assert res3.decision.stage == "borderline_llm_judge"
    assert res3.decision.decision == "MERGE"


def test_pipeline_decision_stream_and_heatmap_integration(tmp_path):
    conn = _init_memory_db()
    vstore = VectorStore(conn, "brute")

    from claire.extract.provider import MockProvider

    provider = MockProvider()

    doc = Document(
        url="https://example.com/claire-arch",
        title="Claire Architecture",
        raw_text="A knowledge graph from a corpus with Claude Code CLI and Letta framework.",
        source_type="web",
        content_hash="hash-arch-001",
    )

    def _fetch_doc(url, *args, **kwargs):
        return doc

    report = ingest(
        "https://example.com/claire-arch",
        conn=conn,
        provider=provider,
        vstore=vstore,
        vault_dir=tmp_path / "vault",
        fetch_fn=_fetch_doc,
    )

    assert report.error is None
    assert report.document_id is not None
    assert report.has_decision_stream is True
    assert report.heatmap_matrix is not None
    assert "matrix" in report.heatmap_matrix

    # DB에 documents.meta["resolution_log"]가 안전하게 보관되었는지 검증
    doc_row = dbm.get_document(conn, report.document_id)
    assert doc_row is not None
    assert doc_row.meta is not None
    assert doc_row.meta["has_decision_stream"] is True
    decisions = get_resolution_log_from_meta(doc_row.meta)
    assert len(decisions) > 0

    # document_detail API 쿼리에서도 정상 노출되는지 검증
    detail = document_detail(conn, report.document_id)
    assert detail is not None
    assert detail["has_decision_stream"] is True
    assert len(detail["resolution_log"]) == len(decisions)


def test_rollback_resolution_api_route(tmp_path):
    from starlette.testclient import TestClient
    from claire.api import server
    from claire.config import Settings
    from claire.ingest.service import IngestService

    db_path = tmp_path / "claire.db"
    owner_token = "owner-" + ("o" * 32)
    settings = Settings(
        db_path=str(db_path),
        data_dir=tmp_path / "data",
        inject_token=owner_token,
        environment="development",
        public_url="http://127.0.0.1:8765",
    )
    svc = IngestService(settings)
    app = server.create_app(settings, svc)
    client = TestClient(app, base_url=settings.public_url)

    # 1. 시드 엔티티 및 문서 적재
    conn = dbm.connect(db_path)
    try:
        dbm.init_db(conn)
        ent = Entity(
            type="Tool",
            name="Claude Code",
            aliases=["Claude-CLI"],
            observations=["Base tool observation"],
            sources=["doc_base"],
        )
        dbm.upsert_entity(conn, ent)

        doc = Document(
            id="doc_merge_test",
            url="https://example.com/test",
            title="Test Doc",
            source_type="web",
            raw_text="Test",
            meta={
                "has_decision_stream": True,
                "resolution_log": [
                    {
                        "entity": "Claude Code CLI",
                        "stage": "exact_match",
                        "decision": "MERGE",
                        "candidate": "Claude Code",
                        "score": 1.0,
                        "target_entity_id": ent.id,
                        "rollback_payload": {
                            "added_aliases": ["Claude Code CLI"],
                            "added_observations": ["New observation from doc"],
                            "added_source": "doc_merge_test",
                        },
                    }
                ],
            },
        )
        dbm.insert_document(conn, doc)
        # 병합된 상태 시뮬레이션
        ent.aliases.append("Claude Code CLI")
        ent.observations.append("New observation from doc")
        ent.sources.append("doc_merge_test")
        dbm.upsert_entity(conn, ent)
        conn.commit()
    finally:
        conn.close()

    # 2. 롤백 API 호출 (성공 케이스)
    resp = client.post(
        "/resolution/rollback",
        json={"document_id": "doc_merge_test", "entity": "Claude Code CLI"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["entity"] == "Claude Code CLI"

    # 3. 롤백 후 엔티티 상태 검증: 추가된 alias 및 observation이 원자적으로 제거되었는지 확인
    with dbm.connect(db_path) as conn:
        restored = dbm.get_entity(conn, ent.id)
        assert restored is not None
        assert "Claude Code CLI" not in restored.aliases
        assert "Claude-CLI" in restored.aliases
        assert "New observation from doc" not in restored.observations
        assert "Base tool observation" in restored.observations
        assert "doc_merge_test" not in restored.sources

        # doc.meta["resolution_log"]도 ROLLED_BACK으로 갱신되었는지 확인
        doc_row = dbm.get_document(conn, "doc_merge_test")
        assert doc_row is not None
        log_item = doc_row.meta["resolution_log"][0]
        assert log_item["decision"] == "ROLLED_BACK"
        assert log_item["rolled_back"] is True
        assert "롤백됨" in log_item["reason"]

    # 4. 이미 롤백된 대상에 대해 재호출 시 적절히 거부되는지 확인
    resp2 = client.post(
        "/resolution/rollback",
        json={"document_id": "doc_merge_test", "entity": "Claude Code CLI"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["ok"] is False


def test_ingest_stream_emits_heatmap_and_decision_events(tmp_path):
    import json
    from types import SimpleNamespace
    from starlette.testclient import TestClient
    from claire.api import server
    from claire.config import Settings
    from claire.extract.provider import emit_progress

    class StreamingTestService:
        def __init__(self, s):
            self.settings = s
            self.provider = SimpleNamespace(name="mock")

        def ingest(self, payload, **kwargs):
            # 1. 진행 메시지
            emit_progress("원문 분석 중…")
            # 2. 실시간 의사결정 이벤트 방출
            emit_progress({
                "stage": "decision",
                "entity": "vSphere 8",
                "decision": "CREATE_NEW",
                "candidate": "vSphere 7",
                "score": 0.85,
                "reason": "버전 불일치 불변식 차단",
            })
            # 3. 실시간 히트맵 매트릭스 방출
            emit_progress({
                "stage": "heatmap_matrix",
                "matrix": {
                    "document_id": "doc_stream_test",
                    "rows": ["vSphere 8"],
                    "cols": ["vSphere 7"],
                    "matrix": [[0.85]],
                },
            })
            return IngestReport(
                document_id="doc_stream_test",
                entities_created=1,
                has_decision_stream=True,
                heatmap_matrix={
                    "document_id": "doc_stream_test",
                    "rows": ["vSphere 8"],
                    "cols": ["vSphere 7"],
                    "matrix": [[0.85]],
                },
            )

    db_path = tmp_path / "claire.db"
    owner_token = "owner-" + ("o" * 32)
    settings = Settings(
        db_path=str(db_path),
        data_dir=tmp_path / "data",
        inject_token=owner_token,
        environment="development",
        public_url="http://127.0.0.1:8765",
    )
    svc = StreamingTestService(settings)
    app = server.create_app(settings, svc)
    client = TestClient(app, base_url=settings.public_url)

    resp = client.post(
        "/ingest-stream",
        json={"payload": "https://example.com/test"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert resp.status_code == 200
    events = [json.loads(line) for line in resp.text.splitlines() if line.strip()]

    # 스트림 내 이벤트 타입 검증
    stages = [ev.get("stage") for ev in events if "stage" in ev]
    assert "work" in stages
    assert "decision" in stages
    assert "heatmap_matrix" in stages

    decision_ev = next(ev for ev in events if ev.get("stage") == "decision")
    assert decision_ev["entity"] == "vSphere 8"
    assert decision_ev["decision"] == "CREATE_NEW"

    matrix_ev = next(ev for ev in events if ev.get("stage") == "heatmap_matrix")
    assert matrix_ev["matrix"]["document_id"] == "doc_stream_test"
    assert matrix_ev["matrix"]["matrix"] == [[0.85]]

    done_ev = next(ev for ev in events if ev.get("done") is True)
    assert done_ev["result"]["document_id"] == "doc_stream_test"
    assert done_ev["result"]["has_decision_stream"] is True
    assert done_ev["result"]["heatmap_matrix"] is not None


def test_get_resolution_decisions_global_api_route(tmp_path):
    from starlette.testclient import TestClient
    from claire.api import server
    from claire.config import Settings
    from claire.ingest.service import IngestService

    db_path = tmp_path / "claire.db"
    readonly_token = "read-" + ("r" * 32)
    owner_token = "owner-" + ("o" * 32)
    settings = Settings(
        db_path=str(db_path),
        data_dir=tmp_path / "data",
        inject_token=owner_token,
        readonly_token=readonly_token,
        environment="development",
        public_url="http://127.0.0.1:8765",
    )
    svc = IngestService(settings)
    app = server.create_app(settings, svc)
    client = TestClient(app, base_url=settings.public_url)

    # 1. 2개 문서에 각각 다른 엔티티 해소 결정 시드
    with dbm.connect(db_path) as conn:
        dbm.init_db(conn)
        d1 = Document(
            id="doc_alpha",
            url="https://example.com/alpha",
            title="Alpha Architecture",
            fetched_at=1700000000,
            meta={
                "has_decision_stream": True,
                "resolution_log": [
                    {
                        "entity": "Kubernetes",
                        "stage": "exact_match",
                        "decision": "MERGE",
                        "candidate": "K8s",
                        "score": 1.0,
                        "reason": "정규화 일치",
                    }
                ],
            },
        )
        d2 = Document(
            id="doc_beta",
            url="https://example.com/beta",
            title="Beta Hypervisor",
            fetched_at=1700001000,
            meta={
                "has_decision_stream": True,
                "resolution_log": [
                    {
                        "entity": "ESXi 8",
                        "stage": "hard_invariant",
                        "decision": "CREATE_NEW",
                        "candidate": "ESXi 7",
                        "score": 0.88,
                        "reason": "메이저 버전 충돌 불변식 차단",
                    }
                ],
            },
        )
        dbm.insert_document(conn, d1)
        dbm.insert_document(conn, d2)
        conn.commit()

    # 2. GET /resolution/decisions 조회 (Read 권한으로 접근 가능)
    resp = client.get(
        "/resolution/decisions?limit=10",
        headers={"Authorization": f"Bearer {readonly_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    decisions = data["decisions"]
    assert len(decisions) == 2

    # 전역 집계 검증: 각 결정에 출처 문서 정보가 온전히 포함되었는지 확인
    entities = {d["entity"] for d in decisions}
    assert "Kubernetes" in entities
    assert "ESXi 8" in entities

    k8s_dec = next(d for d in decisions if d["entity"] == "Kubernetes")
    assert k8s_dec["document_id"] == "doc_alpha"
    assert k8s_dec["document_title"] == "Alpha Architecture"
    assert k8s_dec["decision"] == "MERGE"

    esxi_dec = next(d for d in decisions if d["entity"] == "ESXi 8")
    assert esxi_dec["document_id"] == "doc_beta"
    assert esxi_dec["document_title"] == "Beta Hypervisor"
    assert esxi_dec["decision"] == "CREATE_NEW"



