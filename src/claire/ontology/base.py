"""온톨로지의 코어 데이터 모델 (Pydantic v2).

advisor 조언: per-type 클래스 대신 단일 Entity/Relation + 검증되는 type 필드.
LLM 이 enum 밖 타입을 내면 provisional=True 로 보관(정보 손실 없음), 나중에 코드로 승격.
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Any

from pydantic import BaseModel, Field
from slugify import slugify

from .types import EntityType, RelationType


def _now() -> float:
    return time.time()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def normalize_name(name: str) -> str:
    """엔티티 해소용 정규화 키. 대소문자/공백 차이 + 양끝 구두점 노이즈를 흡수.

    추출 시 흔히 끼는 양끝 괄호/따옴표/마침표(예: "Scrapling]" → "scrapling")를
    벗겨 같은 대상이 갈라지지 않게 한다. 내부 구두점(GPT-4 의 '-')은 보존.
    """
    s = re.sub(r"\s+", " ", name.strip().casefold())
    return s.strip("\"'`[](){}<>.,;:!?·…")


class Entity(BaseModel):
    id: str = Field(default_factory=lambda: new_id("ent"))
    type: str  # EntityType.value 이거나, provisional 인 경우 자유 문자열
    name: str
    aliases: list[str] = Field(default_factory=list)
    props: dict[str, Any] = Field(default_factory=dict)
    observations: list[str] = Field(default_factory=list)  # 자료에서 누적된 관찰/주장
    sources: list[str] = Field(default_factory=list)  # document id 들
    provisional: bool = False  # type 이 시드 enum 밖이면 True
    created_at: float = Field(default_factory=_now)
    updated_at: float = Field(default_factory=_now)

    @property
    def norm_name(self) -> str:
        return normalize_name(self.name)

    @property
    def slug(self) -> str:
        return slugify(self.name) or self.id

    def is_known_type(self) -> bool:
        return self.type in {t.value for t in EntityType}


class Relation(BaseModel):
    id: str = Field(default_factory=lambda: new_id("rel"))
    type: str  # RelationType.value 이거나 provisional 자유 문자열
    source_id: str
    target_id: str
    props: dict[str, Any] = Field(default_factory=dict)
    sources: list[str] = Field(default_factory=list)
    confidence: float = 1.0
    provisional: bool = False
    created_at: float = Field(default_factory=_now)

    def is_known_type(self) -> bool:
        return self.type in {t.value for t in RelationType}


class Document(BaseModel):
    """fetcher 가 산출하는 정규화된 소스 문서."""

    id: str = Field(default_factory=lambda: new_id("doc"))
    url: str | None = None
    canonical_url: str | None = None
    title: str | None = None
    author: str | None = None
    published_at: str | None = None
    fetched_at: float = Field(default_factory=_now)
    raw_text: str = ""
    source_type: str = "web"  # web | youtube | file | text | redirect | xcom
    content_hash: str = ""
    lang: str | None = None
    partial: bool = False  # 부분 처리(예: x.com oEmbed 제목만)
    meta: dict[str, Any] = Field(default_factory=dict)
