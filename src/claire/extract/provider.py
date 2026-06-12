"""LLM/임베딩 provider 어댑터.

advisor 조언: mock provider 는 단순 placeholder 가 아니라 "추출 JSON 계약의 실행 가능한
스펙"이다. mock 이 내는 구조 = Gemini 가 내야 할 구조. M1 에서 sample.md 로 이 계약을
고정하고, 키 도착 시 gemini provider 가 같은 스키마를 채운다.

ExtractionResult 스키마 (provider 가 채워야 할 계약):
{
  "summary": str,
  "key_claims": [str, ...],
  "entities": [
     {"name": str, "type": str, "aliases": [str], "observations": [str],
      "proposed_type": str|null}
  ],
  "relations": [
     {"source": str, "target": str, "type": str, "proposed_type": str|null}
     # source/target 는 같은 결과 내 entity name 으로 참조
  ]
}
"""

from __future__ import annotations

import hashlib
import re
from typing import Protocol

from pydantic import BaseModel, Field

from ..ontology.base import Document


class ExtractedEntity(BaseModel):
    name: str
    type: str
    aliases: list[str] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)
    proposed_type: str | None = None


class ExtractedRelation(BaseModel):
    source: str  # entity name within this result
    target: str
    type: str
    proposed_type: str | None = None


class ExtractionResult(BaseModel):
    summary: str = ""
    key_claims: list[str] = Field(default_factory=list)
    entities: list[ExtractedEntity] = Field(default_factory=list)
    relations: list[ExtractedRelation] = Field(default_factory=list)
    # [재적재 LLM tier] 모델이 반환한 raw 출력 + 어떤 프롬프트/모델로 뽑았는지.
    raw_response: str = ""
    model: str = ""
    prompt_version: str = ""


class MergeCandidate(BaseModel):
    """resolver 가 LLM judge 에게 넘기는 후보 비교 정보."""

    new_name: str
    new_type: str
    new_observations: list[str] = Field(default_factory=list)
    cand_name: str
    cand_type: str
    cand_aliases: list[str] = Field(default_factory=list)
    cand_observations: list[str] = Field(default_factory=list)


class ResearchJudgement(BaseModel):
    """맥락 조사 보고서 판정 — 그래프 추가 게이트(expand/research).

    relevance: 보고서가 '주어진 맥락 안에서의 그 키워드 의미'를 다루는가(0~1).
      다의어가 다른 의미로 새는 오염을 여기서 걸러낸다.
    quality: 사실성·구체성·출처 충실도(0~1).
    """

    relevance: float
    quality: float
    interpretation: str = ""  # 맥락 내에서 키워드를 어떤 의미로 해석했는지(한 문장)
    reason: str = ""


class Provider(Protocol):
    name: str

    def extract(self, doc: Document, ontology_block: str) -> ExtractionResult: ...

    def embed(self, text: str) -> list[float]: ...

    def judge_same_entity(self, mc: MergeCandidate) -> bool: ...


# --- mock provider ---

_GH_RE = re.compile(r"github\.com/([\w.-]+)/([\w.-]+)")


class MockProvider:
    """결정론적, 키 불필요. 휴리스틱으로 그럴듯한 추출을 만든다.

    목적: 파이프라인 배선과 JSON 계약 검증. 의미적 정확도는 보장 안 함.
    """

    name = "mock"
    EMBED_DIM = 64

    def extract(self, doc: Document, ontology_block: str) -> ExtractionResult:
        title = (doc.title or "").strip()
        text = doc.raw_text or ""
        entities: list[ExtractedEntity] = []
        relations: list[ExtractedRelation] = []

        primary_name = title or (doc.url or "untitled")
        # source_type → entity type 휴리스틱
        m = _GH_RE.search(doc.url or "") or _GH_RE.search(text)
        if doc.source_type == "youtube":
            etype = "Article"
        elif m:
            etype = "Repo"
            primary_name = title or f"{m.group(1)}/{m.group(2)}"
        elif doc.source_type == "text":
            etype = "Note"
        else:
            etype = "Article"

        obs = []
        if doc.url:
            obs.append(f"source url: {doc.url}")
        if text:
            obs.append(text[:280])
        entities.append(
            ExtractedEntity(name=primary_name, type=etype, observations=obs)
        )

        # github owner → Org/Person 관계
        if m:
            owner = m.group(1)
            entities.append(ExtractedEntity(name=owner, type="Org"))
            relations.append(
                ExtractedRelation(source=primary_name, target=owner, type="authored_by")
            )

        summary = (text[:200] + "…") if len(text) > 200 else text
        if not summary:
            summary = f"[mock] {primary_name}"
        result = ExtractionResult(
            summary=summary,
            key_claims=[primary_name] if primary_name else [],
            entities=entities,
            relations=relations,
            model="mock",
            prompt_version="mock-1",
        )
        result.raw_response = result.model_dump_json(
            exclude={"raw_response", "model", "prompt_version"}
        )
        return result

    def embed(self, text: str) -> list[float]:
        """해시 기반 결정론적 의사 임베딩(차원 EMBED_DIM)."""
        h = hashlib.sha256(text.encode("utf-8", "ignore")).digest()
        # 바이트를 차원만큼 순환시켜 [-1,1] 범위로
        vals = []
        for i in range(self.EMBED_DIM):
            b = h[i % len(h)]
            vals.append((b / 127.5) - 1.0)
        return vals

    def judge_same_entity(self, mc: "MergeCandidate") -> bool:
        """결정론적 휴리스틱(테스트용): 같은 타입 + 이름/별칭 토큰 포함관계면 동일체."""
        if mc.new_type and mc.cand_type and mc.new_type != mc.cand_type:
            return False
        names = {mc.cand_name.casefold(), *(a.casefold() for a in mc.cand_aliases)}
        return mc.new_name.casefold() in names

    def summarize_search(self, query: str, context: str) -> str:
        """결정론적 stub — 종합/검색 경로를 mock 으로 테스트 가능하게(실제 정리는 Gemini).

        실제 종합 품질은 실 Gemini 로 검증한다. 여기선 query·context 가 흘러들어가
        답으로 나오는지(파이프라인 연결)만 결정론적으로 보장한다."""
        return f"[mock] {query} :: {context[:120]}"

    def render_detail(self, doc: Document) -> str:
        """결정론적 stub — 문서 가독 렌더링(detail) 파이프라인 연결만 보장.

        실제 분량/품질(A4 1~2장)은 실 Gemini 로 검증한다."""
        title = (doc.title or doc.url or "untitled").strip()
        text = (doc.raw_text or "").strip()
        return f"[mock-detail] {title}\n\n{text[:600]}"

    def research(self, query: str, context: str) -> dict:
        """결정론적 stub — 맥락 조사 파이프라인 연결용(실 조사는 Gemini+google_search).

        '조사불가' 포함 쿼리 → INSUFFICIENT(조사 실패 경로 테스트 훅)."""
        if "조사불가" in query:
            return {"report": "INSUFFICIENT", "sources": []}
        return {"report": f"[mock-research] {query} :: {context[:120]}",
                "sources": [{"title": "mock source", "url": "https://example.com/mock"}]}

    def judge_research(self, query: str, context: str, report: str) -> dict:
        """결정론적 stub — '모호' 포함 쿼리는 저품질 판정(게이트 거절 경로 테스트 훅)."""
        low = "모호" in query
        return {"relevance": 0.2 if low else 0.92, "quality": 0.2 if low else 0.85,
                "interpretation": f"[mock] '{query}' 를 맥락 내 의미로 해석",
                "reason": "mock judge"}


def get_provider(settings) -> Provider:  # noqa: ANN001
    """effective_provider 에 따라 provider 인스턴스 반환.

    gemini 백엔드는 키 도착 후 구현 예정. 현재는 항상 mock.
    """
    eff = settings.effective_provider
    if eff == "gemini":
        # TODO(M2): GeminiProvider 구현. 키 도착 전까지 도달하지 않음.
        from .gemini_provider import GeminiProvider  # lazy import

        return GeminiProvider(settings)
    return MockProvider()
