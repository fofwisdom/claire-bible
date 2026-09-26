"""온톨로지 기반 엔티티 프레임 임베딩 텍스트 생성기.

단순 문자열 연결("name — observation") 대신, 엔티티의 타입, 별칭, 핵심 기능, 상위 도메인 맥락을 구조화된 슬롯으로 엮어 임베딩 벡터가 기능적/온톨로지적 위상을 담도록 지원합니다.
"""

from __future__ import annotations

from typing import Any

from ..ontology.base import Entity


def format_entity_frame(
    ent: Entity | Any,
    max_observations: int = 5,
) -> str:
    """엔티티(또는 ExtractedEntity)를 슬롯 기반 프레임 텍스트로 변환."""
    name = getattr(ent, "name", "")
    etype = getattr(ent, "type", "")
    aliases = getattr(ent, "aliases", []) or []
    observations = getattr(ent, "observations", []) or []

    lines = [
        f"[ENTITY: {name}]",
        f"TYPE: {etype}",
    ]
    if aliases:
        lines.append(f"ALIASES: {', '.join(aliases)}")
    if observations:
        lines.append("OBSERVATIONS:")
        for obs in observations[:max_observations]:
            obs_str = str(obs).strip()
            if obs_str:
                lines.append(f"- {obs_str}")
    return "\n".join(lines)
