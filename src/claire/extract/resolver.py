"""엔티티 해소 (entity resolution) — 핵심 가치 "기존 그래프와의 연결".

M3 설계(advisor 반영):
  1) 정규화 이름 exact match → 머지 (임베딩 호출 없음) 2) 별칭(aliases) 일치 → 머지 (임베딩 호출 없음) 3) miss 일 때만 embed_fn() 1회 호출 → 후보 수집(vector + FTS)
       - cosine ≥ AUTO_MERGE: 확신 → 자동 머지
       - CANDIDATE_FLOOR ≤ cosine < AUTO_MERGE 또는 FTS 후보: borderline
         → judge_fn 으로 LLM 동일성 판정(게이팅: 후보 상한 MAX_JUDGE) 4) 없으면 신규 + 임베딩 저장

코사인 임계 자체가 지렛대가 아니다(같은 분야 다른 제품이 0.8+ 로 붙음). 판정의 최종 권한은 LLM judge 에 둔다. judge_fn 이 없으면 AUTO_MERGE 만 적용.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable

from ..ontology.base import Entity, normalize_name
from ..store import db as dbm
from ..store.vectors import VectorStore
from .decision import ResolutionDecision

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


class ResolutionResult(tuple):
    """(Entity, created) 튜플과 100% 하위 호환되며, 4-티어 대역별 후보 정보 및 의사결정 레코드를 함께 제공."""

    entity: Entity
    created: bool
    relational_candidates: list[tuple[str, float]]
    multihop_candidates: list[tuple[str, float]]
    decision: ResolutionDecision | None

    def __new__(
        cls,
        entity: Entity,
        created: bool,
        relational_candidates: list[tuple[str, float]] | None = None,
        multihop_candidates: list[tuple[str, float]] | None = None,
        decision: ResolutionDecision | None = None,
    ):
        instance = super().__new__(cls, (entity, created))
        instance.entity = entity
        instance.created = created
        instance.relational_candidates = relational_candidates or []
        instance.multihop_candidates = multihop_candidates or []
        instance.decision = decision
        return instance


def _get_tier_thresholds() -> tuple[float, float, float, float, bool]:
    try:
        from ..config import get_settings

        s = get_settings()
        return (
            getattr(s, "sim_tier_auto_merge", AUTO_MERGE),
            getattr(s, "sim_tier_borderline", CANDIDATE_FLOOR),
            getattr(s, "sim_tier_relational", 0.70),
            getattr(s, "sim_tier_multihop", 0.55),
            getattr(s, "vector_adaptive_centering", False),
        )
    except Exception:
        return (AUTO_MERGE, CANDIDATE_FLOOR, 0.70, 0.55, False)


_VERSION_RE = re.compile(r"\b\d+(?:\.\d+)*\b")


def passes_hard_invariants(
    new_name: str,
    new_type: str,
    cand_name: str,
    cand_type: str,
    provisional: bool = False,
) -> bool:
    """Gate 2: 온톨로지 불변식 및 절대 차단 검증. 위반 시 False 반환."""
    # 1) 타입 불일치 차단 (임시 타입 제외)
    if new_type != cand_type and not provisional:
        return False

    # 2) 2글자 모호 약어(AI, ML, OS 등) 자동 병합 배제
    n1_strip = new_name.strip()
    n2_strip = cand_name.strip()
    if (len(n1_strip) <= 2 and n1_strip.isalpha() and n1_strip.isupper()) or (
        len(n2_strip) <= 2 and n2_strip.isalpha() and n2_strip.isupper()
    ):
        if normalize_name(new_name) != normalize_name(cand_name):
            return False

    # 3) 버전/릴리즈 토큰 충돌 검사 (예: vSphere 8.0 vs vSphere 7.0)
    v1 = _VERSION_RE.findall(new_name)
    v2 = _VERSION_RE.findall(cand_name)
    if v1 and v2 and v1 != v2:
        return False

    return True


def _execute_reversible_merge(
    conn: sqlite3.Connection,
    cand: Entity,
    name: str,
    aliases: list[str],
    observations: list[str],
    document_id: str,
    *,
    stage: str,
    score: float | None = None,
    reason: str = "",
) -> tuple[Entity, ResolutionDecision]:
    """엔티티를 안전하게 병합하고 롤백 페이로드가 포함된 ResolutionDecision을 생성."""
    changed = False
    added_aliases: list[str] = []
    for a in aliases:
        if a and a != cand.name and a not in cand.aliases:
            cand.aliases.append(a)
            added_aliases.append(a)
            changed = True

    added_observations: list[str] = []
    for o in observations:
        if o and o not in cand.observations:
            cand.observations.append(o)
            added_observations.append(o)
            changed = True

    added_source = None
    if document_id not in cand.sources:
        cand.sources.append(document_id)
        added_source = document_id
        changed = True

    if changed:
        dbm.upsert_entity(conn, cand)

    rollback_payload = {
        "target_entity_id": cand.id,
        "added_aliases": added_aliases,
        "added_observations": added_observations,
        "added_source": added_source,
    }

    decision = ResolutionDecision(
        entity=name,
        stage=stage,
        decision="MERGE",
        candidate=cand.name,
        score=score,
        reason=reason,
        target_entity_id=cand.id,
        rollback_payload=rollback_payload,
    )
    return cand, decision


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
    on_judge: Callable[[str, str], None] | None = None,
) -> ResolutionResult:
    """기존 엔티티에 머지하거나 신규 생성 (5단계 Multi-Tier Safe Gating 적용). (entity, created?) 반환."""
    norm = normalize_name(name)
    tier_auto, tier_borderline, tier_relational, tier_multihop, adaptive_center = (
        _get_tier_thresholds()
    )

    # 1) Gate 1: 새 이름이 기존 name 또는 기존 alias 와 일치 (임베딩 불필요)
    for cand in dbm.find_entities_by_name_or_alias(conn, norm):
        if passes_hard_invariants(name, etype, cand.name, cand.type, provisional):
            cand_m, dec = _execute_reversible_merge(
                conn, cand, name, aliases, observations, document_id,
                stage="exact_match", score=1.0, reason="Exact normalized name match",
            )
            return ResolutionResult(cand_m, False, decision=dec)

    # 2) Gate 1: 새 별칭이 기존 name 또는 기존 alias 와 일치 (임베딩 불필요)
    for alias in aliases:
        if not alias:
            continue
        for cand in dbm.find_entities_by_name_or_alias(conn, normalize_name(alias)):
            if passes_hard_invariants(name, etype, cand.name, cand.type, provisional):
                cand_m, dec = _execute_reversible_merge(
                    conn, cand, name, aliases + [name], observations, document_id,
                    stage="alias_match", score=1.0, reason=f"Matched registered alias '{alias}'",
                )
                return ResolutionResult(cand_m, False, decision=dec)

    # 2.5) Gate 1: 약어 ↔ 풀네임 결정론적 수렴 (임베딩 불필요, quota 0)
    acr_self = _acronym_of(name)
    new_is_acr = _is_acronym_token(name)
    if (acr_self and len(acr_self) >= ACRONYM_MIN) or new_is_acr:
        target_acr = name.strip().upper()
        for cand in dbm.all_entities(conn):
            if not passes_hard_invariants(name, etype, cand.name, cand.type, provisional):
                continue
            cand_names = [cand.name, *cand.aliases]
            hit = (
                (acr_self and len(acr_self) >= ACRONYM_MIN
                 and any(normalize_name(n) == normalize_name(acr_self) for n in cand_names))
                or (new_is_acr and any(_acronym_of(n) == target_acr for n in cand_names))
            )
            if hit:
                cand_m, dec = _execute_reversible_merge(
                    conn, cand, name, aliases + [name], observations, document_id,
                    stage="acronym_match", score=1.0, reason="Deterministic acronym match",
                )
                return ResolutionResult(cand_m, False, decision=dec)

    # 3) Gate 3: miss → 이제서야 임베딩 1회 생성
    embedding = embed_fn() if embed_fn else None

    # 후보 수집: vector(점수 있음) + FTS(점수 없음, 토큰 겹침)
    scored: dict[str, float] = {}
    relational_candidates: list[tuple[str, float]] = []
    multihop_candidates: list[tuple[str, float]] = []

    if embedding:
        for owner_id, score in vstore.search(
            embedding, limit=16, adaptive_center=adaptive_center
        ):
            if score >= tier_borderline:
                scored[owner_id] = score
            elif score >= tier_relational:
                relational_candidates.append((owner_id, score))
            elif score >= tier_multihop:
                multihop_candidates.append((owner_id, score))

    for eid in dbm.fts_search(conn, name, limit=8):
        scored.setdefault(eid, 0.0)

    # 점수 높은 순. tier_auto 이상은 즉시 머지.
    ordered = sorted(scored.items(), key=lambda kv: kv[1], reverse=True)
    judged = 0
    for owner_id, score in ordered:
        cand = dbm.get_entity(conn, owner_id)
        if cand is None:
            continue

        # Gate 2: 온톨로지 불변식 검증 (타입, 버전 충돌 등)
        if not passes_hard_invariants(name, etype, cand.name, cand.type, provisional):
            if score >= tier_relational:
                relational_candidates.append((owner_id, score))
            elif score >= tier_multihop:
                multihop_candidates.append((owner_id, score))
            continue

        # Gate 3: 점수 대역 판정 (초고신뢰도 자동 병합)
        if score >= tier_auto:
            cand_m, dec = _execute_reversible_merge(
                conn, cand, name, aliases + [name], observations, document_id,
                stage="high_confidence_vector", score=score,
                reason=f"Vector similarity {score:.4f} >= auto-merge threshold {tier_auto}",
            )
            return ResolutionResult(
                cand_m, False, relational_candidates, multihop_candidates, decision=dec
            )

        # Gate 4: 경계선(borderline) → System 2 LLM judge (게이팅)
        if judge_fn is not None and judged < MAX_JUDGE:
            judged += 1
            if on_judge is not None:
                try:
                    on_judge(name, cand.name)
                except Exception:
                    pass
            if judge_fn(name, etype, observations, cand):
                cand_m, dec = _execute_reversible_merge(
                    conn, cand, name, aliases + [name], observations, document_id,
                    stage="borderline_llm_judge", score=score,
                    reason=f"Judge confirmed SAME at score {score:.4f}",
                )
                return ResolutionResult(
                    cand_m, False, relational_candidates, multihop_candidates, decision=dec
                )
            else:
                # DIFFERENT 판정된 후보: 버리지 않고 Tier 3 직접 관계 후보로 보존
                relational_candidates.append((owner_id, score))
        elif score >= tier_relational:
            relational_candidates.append((owner_id, score))

    # Gate 5: 미병합 시 신규 생성
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

    new_dec = ResolutionDecision(
        entity=name,
        stage="no_match",
        decision="CREATE_NEW",
        target_entity_id=ent.id,
        reason="No candidate satisfied safe gating criteria.",
    )
    return ResolutionResult(ent, True, relational_candidates, multihop_candidates, decision=new_dec)


def _merge(
    conn: sqlite3.Connection,
    cand: Entity,
    aliases: list[str],
    observations: list[str],
    document_id: str,
) -> Entity:
    """하위 호환성을 위한 내부 헬퍼 (단일 엔티티 반환)."""
    cand_m, _ = _execute_reversible_merge(
        conn, cand, cand.name, aliases, observations, document_id, stage="merge"
    )
    return cand_m

