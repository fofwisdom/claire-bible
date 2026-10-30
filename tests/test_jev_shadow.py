"""Jev shadow 평가 단위 테스트 (docs/design/JEV_SHADOW_EVALUATION_DESIGN.md).

검증 항목:
1. 요청 구성: 후보마다 noul 질문 1개, 공식 계약(model/state/questions, Bearer 헤더), 문맥 한도용 절단
2. 응답 파싱: answers.<key>.noul (최상위가 아님), 누락·비유한 값 무시, 0~1 클램프
3. 오류 분류: HTTP 상태, 타임아웃, 비정상 본문 → JevError.status
4. JevShadow: 실패를 예외 없이 기록, 연속 실패 시 회로 차단 및 cooldown 후 복귀
5. resolver 통합: shadow 는 판정을 바꾸지 않고 후보별 jev_noul 과 결정 단위 메타만 남김, 병합 전 후보 상태로 질의
6. FTS 단독 후보의 judge 병합은 score=None
"""

from __future__ import annotations

import concurrent.futures
import json
import sqlite3
from typing import Any

import httpx
import pytest

import claire.extract.jev as jev_mod
from claire.config import Settings
from claire.extract.jev import (
    JevClient,
    JevError,
    JevShadow,
    build_same_entity_request,
    entity_snapshot,
    make_jev_shadow,
    parse_noul_answers,
)
from claire.extract.resolver import resolve_or_create
from claire.ontology.base import Entity
from claire.store import db as dbm
from claire.store.vectors import VectorStore


class _InlineExecutor(concurrent.futures.Executor):
    """워커 스레드 없이 즉시 실행해 테스트를 결정적으로 만든다."""

    def submit(self, fn, /, *args, **kwargs):  # type: ignore[override]
        fut: concurrent.futures.Future = concurrent.futures.Future()
        try:
            fut.set_result(fn(*args, **kwargs))
        except BaseException as exc:  # noqa: BLE001
            fut.set_exception(exc)
        return fut


class _FakeClient:
    """요청을 기록하고, 질문마다 정해진 noul 을 돌려주거나 지정 예외를 던진다."""

    def __init__(self, noul_by_name: dict[str, float] | None = None, error: Exception | None = None):
        self.model = "jev-latest"
        self.timeout = 1.0
        self.noul_by_name = noul_by_name or {}
        self.error = error
        self.requests: list[tuple[Any, dict[str, Any]]] = []

    def systemone(self, state, questions):
        self.requests.append((json.loads(json.dumps(state)), questions))
        if self.error is not None:
            raise self.error
        answers = {
            key: {"type": "noul", "noul": self.noul_by_name.get(state["candidates"][key]["name"], 0.1)}
            for key in questions
        }
        return {"model": "jev-1.14.0", "answers": answers, "usage": {"input_tokens": 321, "output_tokens": 4}}, 42.0


def _shadow(client: Any, **kwargs: Any) -> JevShadow:
    return JevShadow(client, executor=_InlineExecutor(), **kwargs)


def _init_memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    return conn


def _fixed_thresholds(monkeypatch):
    import claire.extract.resolver as resolver_mod

    monkeypatch.setattr(resolver_mod, "_get_tier_thresholds", lambda: (0.93, 0.72, 0.70, 0.55, False))


# --- 1~3. 요청·응답·오류 ---


def test_request_has_one_noul_question_per_candidate_with_criteria():
    new = entity_snapshot("Postgres", "Tool", ["pg"], ["database"])
    cands = [("ent_1", entity_snapshot("PostgreSQL", "Tool")), ("ent_2", entity_snapshot("MySQL", "Tool"))]
    state, questions, keys = build_same_entity_request(new, cands)

    assert keys == {"c1": "ent_1", "c2": "ent_2"}
    assert state["new_entity"]["name"] == "Postgres"
    assert state["candidates"]["c2"]["name"] == "MySQL"
    assert set(questions) == {"c1", "c2"}
    for key, q in questions.items():
        assert q["type"] == "noul"
        assert f"candidates.{key}" in q["instructions"]
        assert set(q["criteria"]) == {"true", "false"}
        assert "versions" in q["criteria"]["false"]


def test_snapshot_truncates_for_context_limit():
    snap = entity_snapshot("X", "Tool", [f"a{i}" for i in range(30)], ["o" * 1000] * 20)
    assert len(snap["aliases"]) == jev_mod.SNAPSHOT_MAX_ALIASES
    assert len(snap["observations"]) == jev_mod.SNAPSHOT_MAX_OBSERVATIONS
    assert all(len(o) == jev_mod.SNAPSHOT_MAX_OBS_CHARS for o in snap["observations"])
    cands = [(f"e{i}", entity_snapshot(f"n{i}", "Tool")) for i in range(40)]
    _, questions, keys = build_same_entity_request(snap, cands)
    assert len(questions) == len(keys) == jev_mod.SHADOW_MAX_CANDIDATES


def test_parse_reads_answers_level_not_top_level():
    keys = {"c1": "ent_1", "c2": "ent_2", "c3": "ent_3", "c4": "ent_4"}
    resp = {
        "model": "jev-1.14.0",
        "c1": {"noul": 0.99},  # 최상위 키는 계약 위반 위치: 읽으면 안 된다
        "answers": {
            "c1": {"type": "noul", "noul": 0.97},
            "c2": {"type": "noul", "noul": 1.7},
            "c3": {"type": "noul", "noul": "nan"},
        },
    }
    assert parse_noul_answers(resp, keys) == {"ent_1": 0.97, "ent_2": 1.0}


def test_client_posts_documented_contract(monkeypatch):
    seen: dict[str, Any] = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, json=json, headers=headers, timeout=timeout)
        body = {"model": "jev-1.14.0", "answers": {"c1": {"type": "noul", "noul": 0.8}},
                "usage": {"input_tokens": 10, "output_tokens": 1}}
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    client = JevClient("test-key", "https://api.typesafe.ai/v1/", timeout=3.0, model="jev-1.14.0")
    data, elapsed = client.systemone({"s": 1}, {"c1": {"type": "noul"}})

    assert seen["url"] == "https://api.typesafe.ai/v1/systemone"
    assert seen["headers"]["Authorization"] == "Bearer test-key"
    assert seen["json"] == {"model": "jev-1.14.0", "state": {"s": 1}, "questions": {"c1": {"type": "noul"}}}
    assert seen["timeout"] == 3.0
    assert data["answers"]["c1"]["noul"] == 0.8
    assert elapsed >= 0.0


@pytest.mark.parametrize(
    ("response", "status"),
    [
        (httpx.Response(401, json={"detail": "bad key"}), "http_401"),
        (httpx.Response(429, json={"detail": "slow down"}), "http_429"),
        (httpx.Response(200, text="not json"), "bad_response"),
        (httpx.Response(200, json={"model": "jev"}), "bad_response"),
    ],
)
def test_client_classifies_errors(monkeypatch, response, status):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: response)
    with pytest.raises(JevError) as info:
        JevClient("k").systemone("s", {"c1": {"type": "noul"}})
    assert info.value.status == status


def test_client_timeout(monkeypatch):
    def boom(*a, **k):
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(httpx, "post", boom)
    with pytest.raises(JevError) as info:
        JevClient("k").systemone("s", {"c1": {"type": "noul"}})
    assert info.value.status == "timeout"


# --- 4. JevShadow ---


def test_shadow_collect_ok():
    shadow = _shadow(_FakeClient({"PostgreSQL": 0.96}))
    call = shadow.submit(entity_snapshot("Postgres", "Tool"), [("ent_1", entity_snapshot("PostgreSQL", "Tool"))])
    meta, scores = shadow.collect(call)
    assert scores == {"ent_1": 0.96}
    assert meta == {
        "engine": "jev", "requested_model": "jev-latest", "questions": 1, "status": "ok",
        "model": "jev-1.14.0", "latency_ms": 42.0, "input_tokens": 321, "answered": 1,
    }


def test_shadow_failure_is_recorded_not_raised_and_opens_circuit():
    now = [1000.0]
    client = _FakeClient(error=JevError("http_401", "bad key", 401))
    shadow = _shadow(client, failure_threshold=2, cooldown_s=60.0, clock=lambda: now[0])
    snap = entity_snapshot("A", "Tool")
    cands = [("ent_1", entity_snapshot("B", "Tool"))]

    for _ in range(2):
        meta, scores = shadow.collect(shadow.submit(snap, cands))
        assert meta["status"] == "http_401" and meta["error"] == "bad key" and scores == {}
    meta, _ = shadow.collect(shadow.submit(snap, cands))
    assert meta["status"] == "circuit_open"
    assert len(client.requests) == 2

    now[0] += 61.0
    client.error = None
    meta, scores = shadow.collect(shadow.submit(snap, cands))
    assert meta["status"] == "ok" and scores == {"ent_1": 0.1}


def test_make_jev_shadow_requires_flag_and_key():
    assert make_jev_shadow(Settings(enable_jev=False, jev_api_key="k")) is None
    assert make_jev_shadow(Settings(enable_jev=True, jev_api_key="  ")) is None
    s = Settings(enable_jev=True, jev_api_key="k", jev_model="jev-1.14.0")
    first = make_jev_shadow(s)
    assert first is not None and first.client.model == "jev-1.14.0"
    assert make_jev_shadow(s) is first  # 회로 차단 상태가 문서 간에 유지되도록 재사용


# --- 5~6. resolver 통합 ---


def _seed(conn, vstore, ent: Entity, vec: list[float]) -> Entity:
    dbm.upsert_entity(conn, ent)
    vstore.put(ent.id, vec, "claire")
    return ent


def test_shadow_records_noul_without_changing_decision(monkeypatch):
    _fixed_thresholds(monkeypatch)
    conn = _init_memory_db()
    vstore = VectorStore(conn, "brute")
    letta = _seed(conn, vstore, Entity(type="Framework", name="Letta", observations=["memory server"],
                                       sources=["doc_1"]), [0.85, 0.5268, 0.0])
    other = _seed(conn, vstore, Entity(type="Framework", name="LlamaIndex", sources=["doc_1"]), [0.75, 0.6614, 0.0])
    # Jev 는 LlamaIndex 를 같다고 우겨도 판정은 LLM judge 가 정한다
    client = _FakeClient({"Letta": 0.30, "LlamaIndex": 0.95})

    res = resolve_or_create(
        conn, vstore,
        name="Agent Memory Server", etype="Framework", aliases=[], observations=["NEW_OBSERVATION"],
        document_id="doc_2", embed_fn=lambda: [1.0, 0.0, 0.0],
        judge_fn=lambda nm, et, obs, cand: cand.name == "Letta", shadow=_shadow(client),
    )

    assert res.entity.id == letta.id
    dec = res.decision
    assert dec.stage == "borderline_llm_judge"
    by_id = {c["id"]: c for c in dec.candidates}
    assert by_id[letta.id]["outcome"] == "judge_same" and by_id[letta.id]["jev_noul"] == 0.30
    assert by_id[other.id]["outcome"] == "not_reached" and by_id[other.id]["jev_noul"] == 0.95
    assert dec.shadow["status"] == "ok" and dec.shadow["questions"] == 2

    # Jev 입력은 병합 전 후보 상태여야 한다 (신규 관측문이 후보 쪽에 섞이면 '같음' 쪽으로 편향)
    (state, _), = client.requests
    cand_obs = [c["observations"] for c in state["candidates"].values()]
    assert ["memory server"] in cand_obs
    assert all("NEW_OBSERVATION" not in obs for obs in cand_obs)
    assert "NEW_OBSERVATION" in letta_obs(conn, letta.id)


def letta_obs(conn, entity_id: str) -> list[str]:
    ent = dbm.get_entity(conn, entity_id)
    assert ent is not None
    return ent.observations


def test_shadow_failure_keeps_resolution_working(monkeypatch):
    _fixed_thresholds(monkeypatch)
    conn = _init_memory_db()
    vstore = VectorStore(conn, "brute")
    _seed(conn, vstore, Entity(type="Tool", name="Podman", sources=["doc_1"]), [0.8, 0.6, 0.0])

    res = resolve_or_create(
        conn, vstore,
        name="Docker", etype="Tool", aliases=[], observations=[], document_id="doc_2",
        embed_fn=lambda: [1.0, 0.0, 0.0], judge_fn=lambda *a: False,
        shadow=_shadow(_FakeClient(error=JevError("timeout"))),
    )

    assert res.created is True
    assert res.decision.shadow["status"] == "timeout"
    assert all("jev_noul" not in c for c in res.decision.candidates)


def test_no_shadow_call_for_deterministic_gate(monkeypatch):
    _fixed_thresholds(monkeypatch)
    conn = _init_memory_db()
    vstore = VectorStore(conn, "brute")
    dbm.upsert_entity(conn, Entity(type="Tool", name="Claude Code", sources=["doc_1"]))
    client = _FakeClient()

    res = resolve_or_create(
        conn, vstore, name="claude code", etype="Tool", aliases=[], observations=[], document_id="doc_2",
        shadow=_shadow(client),
    )

    assert res.decision.stage == "exact_match"
    assert res.decision.shadow is None
    assert client.requests == []


def test_fts_only_judge_merge_has_no_score(monkeypatch):
    _fixed_thresholds(monkeypatch)
    conn = _init_memory_db()
    vstore = VectorStore(conn, "brute")
    target = Entity(type="Tool", name="Strix Halo Platform", sources=["doc_1"])
    dbm.upsert_entity(conn, target)

    res = resolve_or_create(
        conn, vstore, name="Strix Halo", etype="Tool", aliases=[], observations=[], document_id="doc_2",
        judge_fn=lambda *a: True,
    )

    assert res.entity.id == target.id
    assert res.decision.stage == "borderline_llm_judge"
    assert res.decision.score is None
    assert "FTS" in res.decision.reason
    assert res.decision.candidates[0]["origin"] == "fts" and res.decision.candidates[0]["score"] is None
