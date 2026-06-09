"""엔티티 해소 eval 하니스 (M3) — 라벨된 머지 케이스.

advisor 조언: "better" 가 vibe 가 되지 않도록, *합쳐져야 할 쌍*과 *절대 합치면 안 될
쌍*을 명시적으로 라벨링한다. 회귀(공격적 머지로 서로 다른 도구가 뭉개짐)를 잡는다.

여기서는 임베딩을 직접 주입해 결정론적으로 검증한다(네트워크/토큰 불필요).
실제 Gemini 임베딩/판정 품질은 별도 옵트인 스크립트로 측정.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

import pytest

from claire.ontology.base import Entity
from claire.store import db as dbm
from claire.store.vectors import VectorStore
from claire.extract.resolver import resolve_or_create, AUTO_MERGE, CANDIDATE_FLOOR


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    return conn


@dataclass
class SeedEnt:
    name: str
    type: str
    vec: list[float]
    aliases: list[str] = field(default_factory=list)


@dataclass
class ResCase:
    label: str
    seed: list[SeedEnt]
    new_name: str
    new_type: str
    new_vec: list[float] | None
    judge_returns: bool          # judge_fn 이 borderline 에서 반환할 값
    expect_merge: bool           # 기존과 합쳐져야 하는가
    expect_embed_calls: int      # 임베딩 호출 횟수(0 = exact/alias 단축)
    new_aliases: list[str] = field(default_factory=list)


# --- 라벨된 케이스들 (이 데이터셋이 곧 해소 품질의 기준) ---
CASES: list[ResCase] = [
    ResCase(
        label="exact-name-case-insensitive (Claude Code/claude code)",
        seed=[SeedEnt("Claude Code", "Tool", [1, 0, 0, 0])],
        new_name="claude code", new_type="Tool", new_vec=[0, 1, 0, 0],
        judge_returns=False, expect_merge=True, expect_embed_calls=0,
    ),
    ResCase(
        label="alias-match (Letta alias MemGPT <- MemGPT)",
        seed=[SeedEnt("Letta", "Framework", [1, 0, 0, 0], aliases=["MemGPT"])],
        new_name="MemGPT", new_type="Framework", new_vec=[0, 1, 0, 0],
        judge_returns=False, expect_merge=True, expect_embed_calls=0,
    ),
    ResCase(
        label="auto-merge high cosine (near-identical embedding)",
        seed=[SeedEnt("Scrapling", "Framework", [1.0, 0.0, 0.0, 0.0])],
        new_name="Scrapling lib", new_type="Framework", new_vec=[0.99, 0.01, 0.0, 0.0],
        judge_returns=False, expect_merge=True, expect_embed_calls=1,
    ),
    ResCase(
        label="borderline + judge SAME -> merge (synonym)",
        seed=[SeedEnt("Letta", "Framework", [1.0, 0.0, 0.0, 0.0])],
        new_name="Agent Memory Server", new_type="Framework", new_vec=[0.8, 0.6, 0.0, 0.0],
        judge_returns=True, expect_merge=True, expect_embed_calls=1,
    ),
    ResCase(
        label="borderline + judge DIFFERENT -> separate (rival tool)",
        seed=[SeedEnt("Letta", "Framework", [1.0, 0.0, 0.0, 0.0])],
        new_name="LlamaIndex", new_type="Framework", new_vec=[0.8, 0.6, 0.0, 0.0],
        judge_returns=False, expect_merge=False, expect_embed_calls=1,
    ),
    ResCase(
        label="no candidate (low cosine, no name overlap) -> new",
        seed=[SeedEnt("Letta", "Framework", [1.0, 0.0, 0.0, 0.0])],
        new_name="Mimalloc", new_type="Tool", new_vec=[0.0, 0.0, 1.0, 0.0],
        judge_returns=False, expect_merge=False, expect_embed_calls=1,
    ),
    # --- 약어 동의어 수렴 (결정론적, 임베딩 불필요) ---
    ResCase(
        label="acronym: MCP <- Model Context Protocol (full seeded, acronym new)",
        seed=[SeedEnt("Model Context Protocol", "Concept", [1, 0, 0, 0])],
        new_name="MCP", new_type="Concept", new_vec=[0, 1, 0, 0],
        judge_returns=False, expect_merge=True, expect_embed_calls=0,
    ),
    ResCase(
        label="acronym reverse: Model Context Protocol <- MCP (acronym seeded)",
        seed=[SeedEnt("MCP", "Concept", [1, 0, 0, 0])],
        new_name="Model Context Protocol", new_type="Concept", new_vec=[0, 1, 0, 0],
        judge_returns=False, expect_merge=True, expect_embed_calls=0,
    ),
    ResCase(
        label="acronym MUST NOT merge across different types",
        seed=[SeedEnt("Model Context Protocol", "Concept", [1, 0, 0, 0])],
        new_name="MCP", new_type="Tool", new_vec=[0, 0, 1, 0],  # 타입 다름 → 분리
        judge_returns=False, expect_merge=False, expect_embed_calls=1,
    ),
    ResCase(
        label="2-letter acronym MUST NOT auto-converge (AI ambiguous)",
        seed=[SeedEnt("Artificial Intelligence", "Concept", [1, 0, 0, 0])],
        new_name="AI", new_type="Concept", new_vec=[0, 0, 1, 0],  # 2글자 → 결정론 제외
        judge_returns=False, expect_merge=False, expect_embed_calls=1,
    ),
]


@pytest.mark.parametrize("case", CASES, ids=[c.label for c in CASES])
def test_resolution_case(case: ResCase):
    conn = _db()
    vstore = VectorStore(conn, "brute")

    # seed 기존 그래프
    seed_ids = {}
    for se in case.seed:
        ent = Entity(type=se.type, name=se.name, aliases=se.aliases,
                     observations=[f"seed {se.name}"], sources=["doc_seed"])
        dbm.upsert_entity(conn, ent)
        vstore.put(ent.id, se.vec, "test")
        seed_ids[se.name] = ent.id

    calls = {"embed": 0, "judge": 0}

    def embed_fn():
        calls["embed"] += 1
        return case.new_vec

    def judge_fn(nm, et, obs, cand):
        calls["judge"] += 1
        return case.judge_returns

    before = dbm.counts(conn)["entities"]
    ent, created = resolve_or_create(
        conn, vstore,
        name=case.new_name, etype=case.new_type, aliases=case.new_aliases,
        observations=[f"new {case.new_name}"], document_id="doc_new",
        embed_fn=embed_fn, judge_fn=judge_fn,
    )
    after = dbm.counts(conn)["entities"]

    if case.expect_merge:
        assert not created, f"{case.label}: should have merged"
        assert after == before, f"{case.label}: entity count should not grow"
    else:
        assert created, f"{case.label}: should have created new"
        assert after == before + 1, f"{case.label}: should add one entity"

    assert calls["embed"] == case.expect_embed_calls, (
        f"{case.label}: embed calls {calls['embed']} != {case.expect_embed_calls} "
        "(exact/alias hits must NOT embed)"
    )


def test_thresholds_sane():
    assert CANDIDATE_FLOOR < AUTO_MERGE <= 1.0


def test_judge_only_on_miss_not_on_exact():
    """exact 매치 시 judge 도 embed 도 호출되면 안 된다(비용)."""
    conn = _db()
    vstore = VectorStore(conn, "brute")
    e = Entity(type="Tool", name="Kimi Code", observations=["cli"])
    dbm.upsert_entity(conn, e)

    calls = {"embed": 0, "judge": 0}
    resolve_or_create(
        conn, vstore, name="kimi code", etype="Tool", aliases=[],
        observations=["again"], document_id="d2",
        embed_fn=lambda: (calls.__setitem__("embed", calls["embed"] + 1), [1, 0])[1],
        judge_fn=lambda *a: (calls.__setitem__("judge", calls["judge"] + 1), False)[1],
    )
    assert calls == {"embed": 0, "judge": 0}
