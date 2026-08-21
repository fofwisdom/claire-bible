"""엔티티 해소 (entity resolution) — 핵심 가치 "기존 그래프와의 연결".

M3 설계(advisor 반영):
  1) 정규화 이름 exact match → 머지 (임베딩 호출 없음)
  2) 별칭(aliases) 일치 → 머지 (임베딩 호출 없음)
  3) miss 일 때만 embed_fn() 1회 호출 → 후보 수집(vector + FTS)
       - cosine ≥ AUTO_MERGE: 확신 → 자동 머지
       - CANDIDATE_FLOOR ≤ cosine < AUTO_MERGE 또는 FTS 후보: borderline
         → judge_fn 으로 LLM 동일성 판정(게이팅: 후보 상한 MAX_JUDGE)
  4) 없으면 신규 + 임베딩 저장

코사인 임계 자체가 지렛대가 아니다(같은 분야 다른 제품이 0.8+ 로 붙음).
판정의 최종 권한은 LLM judge 에 둔다. judge_fn 이 없으면 AUTO_MERGE 만 적용.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable

from ..ontology.base import Entity, normalize_name
from ..store import db as dbm
from ..store.vectors import VectorStore

# 확신 임계: 이 이상이면 judge 없이 자동 머지.
AUTO_MERGE = 0.93
# 후보 하한: 이 이상이면 judge 에게 보낼 borderline 후보.
CANDIDATE_FLOOR = 0.72
# judge 호출 상한(엔티티당) — 비용/지연 통제.
MAX_JUDGE = 3
# 약어 동의어 수렴 최소 길이. 2글자(AI/ML 등)는 충돌 위험이 커 결정론적 매칭에서 제외.
ACRONYM_MIN = 3


def _acronym_of(name: str) -> str:
    """멀티워드 이름의 이니셜 약어. 'Model Context Protocol' -> 'MCP'. 단어<2면 ''."""
    words = [w for w in re.split(r"[\s\-]+", name.strip()) if w]
    if len(words) < 2:
        return ""
    return "".join(w[0] for w in words).upper()


def _is_acronym_token(name: str) -> bool:
    """이름 자체가 약어 토큰인가('MCP' 처럼 길이>=3 의 전부 대문자 알파벳)."""
    s = name.strip()
    return len(s) >= ACRONYM_MIN and s.isalpha() and s.isupper()

EmbedFn = Callable[[], list[float]]
# (new_name, new_type, new_observations, candidate) -> 동일체?
JudgeFn = Callable[[str, str, list[str], Entity], bool]


def resolve_or_create(
    conn: sqlite3.Connection,
    vstore: VectorStore,
    *,
    name: str,
    etype: str,
    aliases: list[str],
    observations: list[str],
    document_id: str,
    embed_fn: EmbedFn | None = None,
    judge_fn: JudgeFn | None = None,
    provisional: bool = False,
) -> tuple[Entity, bool]:
    """기존 엔티티에 머지하거나 신규 생성. (entity, created?) 반환."""
    norm = normalize_name(name)

    # 1) 새 이름이 기존 name 또는 기존 alias 와 일치 (임베딩 불필요)
    for cand in dbm.find_entities_by_name_or_alias(conn, norm):
        return _merge(conn, cand, aliases, observations, document_id), False

    # 2) 새 별칭이 기존 name 또는 기존 alias 와 일치 (임베딩 불필요)
    for alias in aliases:
        for cand in dbm.find_entities_by_name_or_alias(conn, normalize_name(alias)):
            return _merge(conn, cand, aliases + [name], observations, document_id), False

    # 2.5) 약어 ↔ 풀네임 결정론적 수렴 (임베딩 불필요, quota 0)
    #   같은 타입 + 이니셜 정확 일치 + 약어 길이>=3 일 때만(다른 타입/2글자는 거짓병합 위험).
    #   예: "MCP" <-> "Model Context Protocol". judge/임베딩 없이 결정론적으로 붙인다.
    acr_self = _acronym_of(name)          # 새 이름이 멀티워드면 그 약어
    new_is_acr = _is_acronym_token(name)  # 새 이름 자체가 약어 토큰인가
    if (acr_self and len(acr_self) >= ACRONYM_MIN) or new_is_acr:
        target_acr = name.strip().upper()
        for cand in dbm.all_entities(conn):
            if cand.type != etype:
                continue
            cand_names = [cand.name, *cand.aliases]
            # 새=풀네임, 기존=약어  /  새=약어, 기존=풀네임
            hit = (
                (acr_self and len(acr_self) >= ACRONYM_MIN
                 and any(normalize_name(n) == normalize_name(acr_self) for n in cand_names))
                or (new_is_acr and any(_acronym_of(n) == target_acr for n in cand_names))
            )
            if hit:
                return _merge(conn, cand, aliases + [name], observations, document_id), False

    # 3) miss → 이제서야 임베딩 1회 생성
    embedding = embed_fn() if embed_fn else None

    # 후보 수집: vector(점수 있음) + FTS(점수 없음, 토큰 겹침)
    scored: dict[str, float] = {}
    if embedding:
        for owner_id, score in vstore.search(embedding, limit=8):
            if score >= CANDIDATE_FLOOR:
                scored[owner_id] = score
    for eid in dbm.fts_search(conn, name, limit=8):
        scored.setdefault(eid, 0.0)  # FTS-only 후보는 점수 0 → judge 대상

    # 점수 높은 순. AUTO_MERGE 이상은 즉시 머지.
    ordered = sorted(scored.items(), key=lambda kv: kv[1], reverse=True)
    judged = 0
    for owner_id, score in ordered:
        cand = dbm.get_entity(conn, owner_id)
        if cand is None:
            continue
        if score >= AUTO_MERGE:
            merged = _merge(conn, cand, aliases + [name], observations, document_id)
            return merged, False
        # borderline → LLM judge (게이팅)
        if judge_fn is not None and judged < MAX_JUDGE:
            judged += 1
            if judge_fn(name, etype, observations, cand):
                merged = _merge(conn, cand, aliases + [name], observations, document_id)
                return merged, False

    # 4) 신규
    ent = Entity(
        type=etype,
        name=name,
        aliases=sorted(set(aliases)),
        observations=list(dict.fromkeys(observations)),
        sources=[document_id],
        provisional=provisional,
    )
    dbm.upsert_entity(conn, ent)
    if embedding:
        vstore.put(ent.id, embedding, model="claire")
    return ent, True


def _merge(
    conn: sqlite3.Connection,
    cand: Entity,
    aliases: list[str],
    observations: list[str],
    document_id: str,
) -> Entity:
    changed = False
    for a in aliases:
        if a and a != cand.name and a not in cand.aliases:
            cand.aliases.append(a)
            changed = True
    for o in observations:
        if o and o not in cand.observations:
            cand.observations.append(o)
            changed = True
    if document_id not in cand.sources:
        cand.sources.append(document_id)
        changed = True
    if changed:
        dbm.upsert_entity(conn, cand)
    return cand
