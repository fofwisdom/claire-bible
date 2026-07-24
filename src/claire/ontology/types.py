"""시드 온톨로지 타입.

advisor 조언에 따라 per-type 클래스를 만들지 않고, 검증되는 문자열 enum + 레지스트리로
관리한다(closed enum + escape hatch). LLM 이 enum 밖 타입을 제안하면 provisional 로 보관한다.

새 타입을 "코드로 승격"하려면 아래 리스트에 한 줄 추가하면 된다 = 사용자가 말한
"관계를 코드 인터페이스로 관리".
"""

from __future__ import annotations

from enum import Enum


class EntityType(str, Enum):
    TOOL = "Tool"
    FRAMEWORK = "Framework"
    MODEL = "Model"
    PAPER = "Paper"
    ARTICLE = "Article"
    REPO = "Repo"
    CONCEPT = "Concept"
    PERSON = "Person"
    ORG = "Org"
    EVENT = "Event"
    NOTE = "Note"


class RelationType(str, Enum):
    IMPLEMENTS = "implements"
    ALTERNATIVE_TO = "alternative_to"
    COMPETES_WITH = "competes_with"
    AUTHORED_BY = "authored_by"
    CITES = "cites"
    PART_OF = "part_of"
    USES = "uses"
    INTEGRATES_WITH = "integrates_with"
    RELATED_TO = "related_to"


# 각 엔티티 타입에 대한 짧은 설명 — LLM 프롬프트와 vault frontmatter 에 쓰인다.
ENTITY_DESCRIPTIONS: dict[EntityType, str] = {
    EntityType.TOOL: "A usable software tool, CLI, app, or product.",
    EntityType.FRAMEWORK: "A library or framework developers build on.",
    EntityType.MODEL: "An ML/AI model or model family.",
    EntityType.PAPER: "An academic paper or formal writeup.",
    EntityType.ARTICLE: "A blog post, news article, or informal writeup.",
    EntityType.REPO: "A source code repository.",
    EntityType.CONCEPT: "An abstract idea, technique, pattern, or topic.",
    EntityType.PERSON: "An individual person.",
    EntityType.ORG: "A company, lab, team, or organization.",
    EntityType.EVENT: "A dated happening: release, announcement, benchmark result.",
    EntityType.NOTE: "A free-form note or keyword the user dropped in.",
}

# 관계 타입의 domain(source 허용 타입) / range(target 허용 타입).
# None = 모든 엔티티 타입 허용. registry 가 이 표로 관계 유효성을 검증한다.
RELATION_DOMAIN_RANGE: dict[RelationType, tuple[set[EntityType] | None, set[EntityType] | None]] = {
    RelationType.IMPLEMENTS: (None, None),
    RelationType.ALTERNATIVE_TO: (None, None),
    RelationType.COMPETES_WITH: (None, None),
    RelationType.AUTHORED_BY: (None, {EntityType.PERSON, EntityType.ORG}),
    RelationType.CITES: (None, None),
    RelationType.PART_OF: (None, None),
    RelationType.USES: (None, None),
    RelationType.INTEGRATES_WITH: (None, None),
    RelationType.RELATED_TO: (None, None),
}

RELATION_DESCRIPTIONS: dict[RelationType, str] = {
    RelationType.IMPLEMENTS: "source implements/realizes target.",
    RelationType.ALTERNATIVE_TO: "source is an alternative/substitute to target.",
    RelationType.COMPETES_WITH: "source competes with target in the same space.",
    RelationType.AUTHORED_BY: "source was created by target (person/org).",
    RelationType.CITES: "source references/cites target.",
    RelationType.PART_OF: "source is a component/part of target.",
    RelationType.USES: "source uses/depends on target.",
    RelationType.INTEGRATES_WITH: "source integrates/interoperates with target.",
    RelationType.RELATED_TO: "generic association (use only when nothing fits).",
}
