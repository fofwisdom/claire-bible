"""온톨로지 레지스트리 — 타입 정의 + 관계 유효성 검증 + provisional 판정.

이 모듈이 "시드 + LLM 확장"의 게이트키퍼다:
- 알려진 타입인지 판정
- 관계의 domain/range 검증
- enum 밖 타입은 거부하지 않고 provisional 로 표시(정보 보존)
"""

from __future__ import annotations

from dataclasses import dataclass

from .types import (
    ENTITY_DESCRIPTIONS,
    RELATION_DESCRIPTIONS,
    RELATION_DOMAIN_RANGE,
    EntityType,
    RelationType,
)

KNOWN_ENTITY_TYPES: set[str] = {t.value for t in EntityType}
KNOWN_RELATION_TYPES: set[str] = {t.value for t in RelationType}


@dataclass
class ValidationResult:
    ok: bool
    provisional: bool
    reason: str = ""


def classify_entity_type(raw: str) -> tuple[str, bool]:
    """(정규화된 타입, provisional?) 반환. enum 매칭은 대소문자 무시."""
    for t in EntityType:
        if raw.strip().casefold() == t.value.casefold():
            return t.value, False
    return raw.strip(), True


def classify_relation_type(raw: str) -> tuple[str, bool]:
    for t in RelationType:
        if raw.strip().casefold() == t.value.casefold():
            return t.value, False
    return raw.strip(), True


def validate_relation(
    rel_type: str, source_type: str, target_type: str
) -> ValidationResult:
    """관계의 domain/range 검증.

    - 미지(provisional) 관계 타입: 통과시키되 provisional=True.
    - 알려진 타입: domain/range 표로 검증. 위반이면 ok=False.
    """
    norm, provisional = classify_relation_type(rel_type)
    if provisional:
        return ValidationResult(ok=True, provisional=True, reason="unknown relation type")

    rt = RelationType(norm)
    domain, rng = RELATION_DOMAIN_RANGE[rt]

    src_norm, src_prov = classify_entity_type(source_type)
    tgt_norm, tgt_prov = classify_entity_type(target_type)

    # provisional 엔티티 타입은 domain/range 검증을 건너뛴다(보수적으로 허용).
    if domain is not None and not src_prov:
        if EntityType(src_norm) not in domain:
            return ValidationResult(
                ok=False,
                provisional=False,
                reason=f"{norm}: source type {src_norm} not in domain",
            )
    if rng is not None and not tgt_prov:
        if EntityType(tgt_norm) not in rng:
            return ValidationResult(
                ok=False,
                provisional=False,
                reason=f"{norm}: target type {tgt_norm} not in range",
            )
    return ValidationResult(ok=True, provisional=False)


def ontology_prompt_block() -> str:
    """LLM 추출 프롬프트에 삽입할 온톨로지 명세 텍스트."""
    lines = ["ENTITY TYPES (choose the single best fit):"]
    for t in EntityType:
        lines.append(f"  - {t.value}: {ENTITY_DESCRIPTIONS[t]}")
    lines.append("")
    lines.append("RELATION TYPES (choose the single best fit):")
    for t in RelationType:
        lines.append(f"  - {t.value}: {RELATION_DESCRIPTIONS[t]}")
    lines.append("")
    lines.append(
        "If and only if NO type fits, you may propose a new snake_case type in the "
        "'proposed_type' field; do NOT fall back to related_to/Note as a dumping ground."
    )
    return "\n".join(lines)
