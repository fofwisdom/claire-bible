"""LLM/임베딩 provider 어댑터.

advisor 조언: mock provider 는 단순 placeholder 가 아니라 "추출 JSON 계약의 실행 가능한 스펙"이다. mock 이 내는 구조 = Gemini 가 내야 할 구조. M1 에서 sample.md 로 이 계약을 고정하고, 키 도착 시 gemini provider 가 같은 스키마를 채운다.

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
import inspect
import re
import threading
import time
from typing import Any, Protocol

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
    summary: str = Field(
        default="",
        description=(
            "1-3 factual sentences in Korean written style (문어체: ~한다/~이다) providing an executive summary "
            "of the entire document (core problem, specific technical solution/mechanism, and outcomes/conclusions) "
            "in pure plain text (평문, no AsciiDoc or Markdown markup). Do NOT write an introductory preface, foreword, "
            "or background-only outline."
        ),
    )
    key_claims: list[str] = Field(default_factory=list)
    entities: list[ExtractedEntity] = Field(default_factory=list)
    relations: list[ExtractedRelation] = Field(default_factory=list)
    # [재적재 LLM tier] 모델이 반환한 raw 출력 + 어떤 프롬프트/모델로 뽑았는지.
    raw_response: str = ""
    model: str = ""
    prompt_version: str = ""

    @classmethod
    def extraction_json_schema(cls) -> dict:
        """LLM 구조화 추출에 전달할 JSON Schema (summary, entities, relations 필수화)."""
        schema = cls.model_json_schema()
        schema["required"] = ["summary", "entities", "relations"]
        # 내부 관리용 메타데이터 필드는 LLM 스키마에 노출하지 않음
        props = schema.get("properties", {})
        for meta_field in ("raw_response", "model", "prompt_version"):
            props.pop(meta_field, None)
        return schema


class MergeCandidate(BaseModel):
    """resolver 가 LLM judge 에게 넘기는 후보 비교 정보."""

    new_name: str
    new_type: str
    new_aliases: list[str] = Field(default_factory=list)
    new_observations: list[str] = Field(default_factory=list)
    cand_name: str
    cand_type: str
    cand_aliases: list[str] = Field(default_factory=list)
    cand_observations: list[str] = Field(default_factory=list)


class RelationCandidate(BaseModel):
    """지식 그래프 후보 간 관계 판정 입력 정보 (Phase 2)."""

    entity_a_name: str
    entity_a_type: str
    entity_a_observations: list[str] = Field(default_factory=list)
    entity_a_aliases: list[str] = Field(default_factory=list)
    entity_b_name: str
    entity_b_type: str
    entity_b_observations: list[str] = Field(default_factory=list)
    entity_b_aliases: list[str] = Field(default_factory=list)
    similarity_score: float = 0.0
    context: str | None = None


class RelationJudgement(BaseModel):
    """두 엔티티 간의 관계 성립 여부 및 방향/타입 판정 결과 (Phase 2)."""

    has_relation: bool = False
    relation_type: str | None = None
    direction: str = "forward"  # "forward" (A -> B), "backward" (B -> A), or "bidirectional"
    reason: str = ""
    confidence: float = 1.0


# --- LLM 호출 진행 이벤트(스레드-로컬) ---
# 긴 작업(맥락 조사 등)이 rate limit 대기/재시도 같은 내부 상황을 실시간으로 UI 에
# 흘릴 수 있게 한다. provider 인스턴스는 봇/API 가 공유하므로 인스턴스 속성 대신
# **스레드-로컬** — 콜백을 등록한 스레드의 호출에만 이벤트가 가고 다른 요청과 안 섞인다.
_progress_local = threading.local()


def set_progress_callback(cb) -> None:
    _progress_local.cb = cb


def emit_progress(msg: str) -> None:
    """현재 스레드에 콜백이 등록돼 있으면 진행 메시지 전달(없으면 no-op, 실패 무해)."""
    cb = getattr(_progress_local, "cb", None)
    if cb:
        try:
            cb(msg)
        except Exception:  # noqa: BLE001
            pass


class ResearchJudgement(BaseModel):
    """맥락 조사 보고서 판정 — 그래프 추가 게이트(expand/research).

    relevance: 보고서가 '주어진 맥락 안에서의 그 키워드 의미'를 다루는가(0~1). 다의어가 다른 의미로 새는 오염을 여기서 걸러낸다. quality: 사실성·구체성·출처 충실도(0~1). same_subject: [1홉 병합 전용, docs/implementation/ONEHOP_MERGE.md §3.1] 대상(query/report)이 맥락이 다루는 구체적 대상 그 자체를 가리키는 원출처(예: 맥락이 소개하는 프로젝트의 공식 저장소 자체 — 같은 것을 가리키는 두 창)면 True. 맥락 자체가 이미 특정 회사·제품의 1차 콘텐츠라면, 같은 회사·제품의 다른 공식 페이지(문서/다른 사례/홈페이지)라는 이유만 으로는 True 로 보지 않는다 — 맥락이 다루는 구체적 서사와 실질적으로 같을 때만 True. 맥락과 관련은 있으나 사실상 별개의 소재(다른 프로젝트/사건/화제/제3자 논의)면 False. contextual research(맥락 확장 조사) 호출부는 이 필드를 사용하지 않는다(무시해도 무해).
    """

    relevance: float
    quality: float
    same_subject: bool = True
    interpretation: str = ""  # 맥락 내에서 키워드를 어떤 의미로 해석했는지(한 문장)
    reason: str = ""


class FollowSelection(BaseModel):
    """1홉 자동확장 — 따라갈 후보 링크의 인덱스 목록(LLM 이 선별).

    follow: 입력 후보에서 '지식으로 더 팔 가치가 있다'고 판단한 항목의 0-기반 인덱스. 가치 없으면 빈 목록(= 파고들지 않음). 다의어/잡음/맥락 무관은 제외.
    """

    follow: list[int] = Field(default_factory=list)
    reason: str = ""


class WatchClassification(BaseModel):
    """[주기 크롤링] 이 문서가 '주기적으로 내용이 바뀌는 콘텐츠'인가.

    watch: 리더보드/벤치마크 순위표/실시간 통계/가격/랭킹처럼 지속 갱신되면 True; 뉴스/블로그/논문/일회성 설명/문서면 False. interval_days: watch=True 일 때 재확인 권장 주기(일). 아니면 None.
    """

    watch: bool = False
    interval_days: int | None = None
    reason: str = ""


class Provider(Protocol):
    name: str

    def extract(self, doc: Document, ontology_block: str) -> ExtractionResult: ...

    def embed(
        self, text: str, *, task_type: str | None = None, title: str | None = None
    ) -> list[float]: ...

    def judge_same_entity(
        self, mc: MergeCandidate, *, document_id: str | None = None
    ) -> bool: ...

    def judge_relationship(
        self, rc: RelationCandidate, *, document_id: str | None = None
    ) -> RelationJudgement: ...

    def summarize_search(self, query: str, context: str) -> str: ...

    def summarize_document(self, doc: Document, *, effort: str | None = None) -> str: ...

    def classify_paper(
        self, doc: Document, *, effort: str | None = None
    ) -> tuple[bool, str]: ...

    def classify_watch(self, doc: Document) -> dict: ...

    def research(self, query: str, context: str) -> dict: ...

    def judge_research(self, query: str, context: str, report: str) -> dict: ...

    def select_followups(self, context: str, candidates: list[dict]) -> list[int]: ...

    def render_detail(
        self, doc: Document, format: str = "adoc", focus: str | None = None
    ) -> str: ...

    def compose_presentation(
        self,
        doc: Document,
        *,
        summary: str | None = None,
        focus: str | None = None,
        slide_budget: int = 10,
        theme: str = "night",
        transition: str = "slide",
        effort: str | None = None,
    ) -> str: ...


# --- mock provider ---

_GH_RE = re.compile(r"github\.com/([\w.-]+)/([\w.-]+)")


class MockProvider:
    """결정론적, 키 불필요. 휴리스틱으로 그럴듯한 추출을 만든다.

    목적: 파이프라인 배선과 JSON 계약 검증. 의미적 정확도는 보장 안 함.
    """

    name = "mock"
    EMBED_DIM = 64

    def extract(self, doc: Document, ontology_block: str = "") -> ExtractionResult:
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

    def embed(
        self, text: str, *, task_type: str | None = None, title: str | None = None
    ) -> list[float]:
        """해시 기반 결정론적 의사 임베딩(차원 EMBED_DIM)."""
        h = hashlib.sha256(text.encode("utf-8", "ignore")).digest()
        # 바이트를 차원만큼 순환시켜 [-1,1] 범위로
        vals = []
        for i in range(self.EMBED_DIM):
            b = h[i % len(h)]
            vals.append((b / 127.5) - 1.0)
        return vals

    def judge_same_entity(
        self, mc: MergeCandidate, *, document_id: str | None = None
    ) -> bool:
        """결정론적 휴리스틱(테스트용): 같은 타입 + 이름/별칭 토큰 포함관계면 동일체."""
        if mc.new_type and mc.cand_type and mc.new_type != mc.cand_type:
            return False
        names = {mc.cand_name.casefold(), *(a.casefold() for a in mc.cand_aliases)}
        new_names = {mc.new_name.casefold(), *(a.casefold() for a in mc.new_aliases)}
        return bool(names & new_names)

    def judge_relationship(
        self, rc: RelationCandidate, *, document_id: str | None = None
    ) -> RelationJudgement:
        """결정론적 휴리스틱 / 테스트 훅 지원 (Phase 2)."""
        if hasattr(self, "_judge_rel_hook") and callable(self._judge_rel_hook):
            return self._judge_rel_hook(rc)

        a_obs = " ".join(rc.entity_a_observations).lower()
        b_obs = " ".join(rc.entity_b_observations).lower()
        a_name = rc.entity_a_name.lower()
        b_name = rc.entity_b_name.lower()

        # 개선 관계 (improves)
        if (
            "improves" in a_obs
            or "improves upon" in a_obs
            or "optimized" in a_obs
            or "faster than" in a_obs
        ):
            return RelationJudgement(
                has_relation=True,
                relation_type="improves",
                direction="forward",
                reason=f"{rc.entity_a_name} improves {rc.entity_b_name}",
                confidence=0.85,
            )

        # 파생/기반 관계 (derived_from)
        if "derived from" in a_obs or "based on" in a_obs or "built on" in a_obs:
            return RelationJudgement(
                has_relation=True,
                relation_type="derived_from",
                direction="forward",
                reason=f"{rc.entity_a_name} is derived from {rc.entity_b_name}",
                confidence=0.9,
            )

        # A가 B를 사용/참조
        if b_name in a_obs:
            return RelationJudgement(
                has_relation=True,
                relation_type="uses",
                direction="forward",
                reason=f"{rc.entity_a_name} references {rc.entity_b_name}",
                confidence=0.9,
            )

        # B가 A를 사용/참조
        if a_name in b_obs:
            return RelationJudgement(
                has_relation=True,
                relation_type="uses",
                direction="backward",
                reason=f"{rc.entity_b_name} references {rc.entity_a_name}",
                confidence=0.9,
            )

        return RelationJudgement(has_relation=False, reason="No direct relationship found")

    def summarize_search(self, query: str, context: str) -> str:
        """결정론적 stub — 종합/검색 경로를 mock 으로 테스트 가능하게(실제 정리는 Gemini).

        실제 종합 품질은 실 Gemini 로 검증한다. 여기선 query·context 가 흘러들어가 답으로 나오는지(파이프라인 연결)만 결정론적으로 보장한다."""
        return f"[mock] {query} :: {context[:120]}"

    def summarize_document(self, doc: Document, *, effort: str | None = None) -> str:
        """Return a deterministic plain-text executive summary for a document."""
        return self.extract(doc).summary

    def render_detail(
        self, doc: Document, format: str = "adoc", focus: str | None = None
    ) -> str:
        """결정론적 stub — 문서 가독 렌더링(detail, MD 또는 ADOC) 파이프라인 연결만 보장.

        실제 분량/품질(A4 1~2장 AsciiDoc+강조+이미지 큐레이션)은 실 Gemini 로 검증한다. 여기선 문서 구조·강조·수집 이미지가 detail 로 흘러가는지(배선)만 보장한다."""
        title = (doc.title or doc.url or "untitled").strip()
        text = (doc.raw_text or "").strip()
        images = (doc.meta or {}).get("images") or []
        focus_val = focus
        focus_tag = f" [focus: {focus_val}]" if focus_val else ""
        parts = [f"[mock-detail-adoc]{focus_tag} *{title}*", "", text[:600]]
        if images:
            im = images[0]
            cap = im.get("caption") or im.get("alt") or "그림"
            src = ("/image?p=" + im["local"]) if im.get("local") else im.get("url", "")
            parts += ["", f'image::{src}[{im.get("alt", "")}, title="{cap}"]']
        return "\n".join(parts)

    def compose_presentation(
        self,
        doc: Document,
        *,
        summary: str | None = None,
        focus: str | None = None,
        slide_budget: int = 10,
        theme: str = "night",
        transition: str = "slide",
        effort: str | None = None,
        **kwargs: Any,
    ) -> str:
        """결정론적 stub — 프레젠테이션 AsciiDoc 저작 파이프라인 연결 및 구조 검증용.

        reveal.js 헤더, 2D 섹션(==, ===), 표(|===), 인용([quote]), 발표자 노트([.notes])를 포함한
        완성형 슬라이드 덱을 결정론적으로 생성한다.
        """
        title = (doc.title or doc.url or "Claire Bible Presentation").strip()
        author = (doc.author or "Claire Bible Engine").strip()
        date_str = (doc.published_at or "").strip()
        text = (doc.raw_text or "").strip()
        summary_val = summary or (text[:200] + "..." if len(text) > 200 else text)
        focus_str = f" [초점: {focus}]" if focus else ""

        lines = [
            f"= {title}{focus_str}",
            f":author: {author}",
        ]
        if date_str:
            lines.append(f":revdate: {date_str}")
        lines.extend([
            f":revealjs_theme: {theme}",
            f":revealjs_transition: {transition}",
            ":revealjs_slideNumber: c/t",
            ":revealjs_history: true",
            ":revealjs_hash: true",
            ":revealjs_controls: true",
            ":revealjs_progress: true",
            ":revealjs_center: false",
            ":revealjs_width: 1280",
            ":revealjs_height: 720",
            ":revealjs_margin: 0.04",
            ":revealjs_minScale: 0.2",
            ":revealjs_maxScale: 2.0",
            ":source-highlighter: highlight.js",
            ":icons: font",
            "",
            "== 1. 아젠다 및 개요 (Agenda & Overview)",
            "",
            f"[quote, {author}]",
            f"{summary_val}",
            "",
            "* *배경 및 동기*: 복잡한 시스템 아키텍처의 직관적 파악",
            "* *핵심 과제*: 분산 지식의 정밀 추출 및 #자산화 체계 구축#",
            "* *접근 방식*: #LLM 기반 2D 프레젠테이션# 저작",
            "",
            "[.notes]",
            "--",
            f"* (도입 멘트): 반갑습니다. 오늘 발표할 주제는 '{title}'입니다.",
            f"* (핵심 요약): {summary_val}",
            "* (다음 단계): 이제 본격적인 아키텍처 구조를 살펴보겠습니다.",
            "--",
            "",
            "== 2. 시스템 아키텍처 (Architecture)",
            "",
            "=== 2.1 핵심 파이프라인 구조",
            "",
            "* *입력 레이어*: 원문 데이터 무결성 보존 (`detail`)",
            "* *저작 레이어*: 스토리텔링과 발표자 대본을 포함한 슬라이드 집필 (`presentation_adoc`)",
            "* *컴파일 레이어*: Asciidoctor reveal.js 초고속 렌더링 (`~150ms`)",
            "",
            "[.notes]",
            "--",
            "* (설명): 기존의 기계적 텍스트 분할을 벗어나 독립적인 저작 단계를 도입했습니다.",
            "* (강조): 이를 통해 청중용 화면과 발표자 화면이 완벽히 분리됩니다.",
            "--",
            "",
            "=== 2.2 메커니즘 심층 분석",
            "",
            "[NOTE]",
            "발표 장표는 한 화면당 3~5개의 핵심 불릿으로 가독성을 극대화합니다.",
            "",
            "* 단문 중심의 임팩트 있는 키워드 전달",
            "* 발표자 모드(`S` 키)를 통한 실시간 대본 브리핑",
            "* 수평/수직 2D 내러티브 매핑",
            "",
            "[.notes]",
            "--",
            "* (설명): reveal.js의 발표자 콘솔에서 실시간 타이머와 다음 장표를 확인하며 브리핑할 수 있습니다.",
            "--",
            "",
            "== 3. 비교 분석 및 지표 (Evaluation)",
            "",
            '[cols="1,2,2", options="header"]',
            "|===",
            "| 구분 | 기존 방식 | 신규 LLM 저작 방식",
            "| 슬라이드 구성 | 본문 단순 복사 및 기계적 분할 | 청중 중심의 압축 불릿 및 2D 내러티브",
            "| 발표자 대본 | 없음 (청중과 같은 화면) | [.notes] 전 장표 맞춤형 스크립트",
            "| 컴파일 속도 | 런타임 분할 | AOT 캐싱 및 즉각 컴파일 (~150ms)",
            "|===",
            "",
            "[.notes]",
            "--",
            "* (표 해설): 표에서 보시는 바와 같이, 발표 경험과 가독성 측면에서 근본적인 차이가 발생합니다.",
            "--",
            "",
            "== 4. 주요 결론 및 질의응답 (Conclusion & Q&A)",
            "",
            "* *지식의 자산화*: 단순 텍스트 저장을 넘어 발표 가능한 프레젠테이션으로 전환",
            "* *발표자 친화성*: 완벽한 2화면 브리핑 지원으로 실전 전달력 극대화",
            "* *에이전트 연동*: CLI 및 API를 통한 완전 자동화된 장표 저작",
            "",
            "[.notes]",
            "--",
            "* (마무리): 경청해 주셔서 감사합니다. 질문이 있으시면 편하게 말씀해 주시기 바랍니다.",
            "--",
        ])
        return "\n".join(lines)

    def classify_watch(self, doc: Document) -> dict:
        """결정론 stub — 제목/본문에 순위·벤치 키워드 있으면 watch(주기크롤 판단 배선 검증).

        실제 판단은 Gemini. 여기선 키워드로 watch=True/False 가 흘러 set_document_watch 까지 배선되는지만 보장(테스트). 'leaderboard/벤치/순위/실시간' 등 → watch."""
        blob = ((doc.title or "") + " " + (doc.raw_text or "")).lower()
        kws = ("leaderboard", "benchmark", "ranking", "arena", "리더보드",
               "벤치마크", "순위", "랭킹", "실시간")
        watch = any(k in blob for k in kws)
        return {"watch": watch, "interval_days": 1 if watch else None,
                "reason": "키워드 매칭(mock)" if watch else "1회성으로 판단(mock)"}

    def research(self, query: str, context: str) -> dict:
        """결정론적 stub — 맥락 조사 파이프라인 연결용(실 조사는 Gemini+google_search).

        '조사불가' 포함 쿼리 → INSUFFICIENT(조사 실패 경로 테스트 훅)."""
        if "조사불가" in query:
            return {"report": "INSUFFICIENT", "sources": []}
        return {"report": f"[mock-research] {query} :: {context[:120]}",
                "sources": [{"title": "mock source", "url": "https://example.com/mock"}]}

    def judge_research(self, query: str, context: str, report: str) -> dict:
        """결정론적 stub — 저품질 판정(게이트 거절) 훅: query '모호' 또는 report '무관'.

        '무관' 훅은 1홉 자동확장의 store 게이트(judge_research 재사용) 거절 경로 테스트용. '별개주제' 훅은 게이트는 통과하되 same_subject=False(1홉 병합 분기 테스트용, docs/implementation/ONEHOP_MERGE.md §3.1) — 기본은 True(합쳐도 되는 케이스가 흔함을 모사)."""
        low = ("모호" in query) or ("무관" in report) or ("무관" in context)
        same_subject = "별개주제" not in report and "별개주제" not in context
        return {"relevance": 0.2 if low else 0.92, "quality": 0.2 if low else 0.85,
                "same_subject": same_subject,
                "interpretation": f"[mock] '{query}' 를 맥락 내 의미로 해석",
                "reason": "mock judge"}

    def select_followups(self, context: str, candidates: list[dict]) -> list[int]:
        """결정론적 stub — 1홉 확장에서 따라갈 후보 선별(파고들지 여부=LLM 결정 모사).

        url/anchor 에 'skip' 이 들어간 후보는 제외(= 안 판다), 나머지는 따라간다. 실제 선별 품질은 실 Gemini 로. 여기선 선별→게이트 배선만 결정론적으로 보장한다."""
        out = []
        for i, c in enumerate(candidates):
            blob = f"{c.get('url', '')} {c.get('anchor', '')}".lower()
            if "skip" not in blob:
                out.append(i)
        return out

    def classify_paper(self, doc: Document, *, effort: str | None = None):
        """결정론 stub — 논문 여부 판정 배선 검증 (테스트)."""
        from .classifier import (
            PaperClassificationResult,
            extract_author_heuristic,
            extract_title_heuristic,
            is_dtp_title,
        )

        blob = ((doc.title or "") + " " + (doc.url or "") + " " + (doc.raw_text or "")[:1000]).lower()
        paper_kws = ("arxiv", "paper", "working paper", "nber", "abstract", "논문", "논단", "journal", "conference", "ieee")
        is_paper = any(k in blob for k in paper_kws)
        author = extract_author_heuristic(doc.raw_text or "")
        title = extract_title_heuristic(doc.raw_text or "") if is_dtp_title(doc.title) else None
        return PaperClassificationResult(
            is_paper,
            "keyword match (mock)" if is_paper else "not paper (mock)",
            author=author,
            title=title,
        )



class UnavailableProvider:
    """운영 환경에서 설정한 프로바이더를 쓸 수 없을 때의 자리표시자.

    서비스 기동과 상태 조회는 막지 않고, 실제 호출은 mock 결과 대신 실패시킨다. ``error``(정책 예외)가 있으면 호출마다 같은 종류의
    새 예외를 낸다(멤버 컨텍스트의 ``ProviderNotConfigured``·``ProviderNotAllowed``, 설계 §10.2).
    """

    name = "unavailable"

    def __init__(self, reason: str, *, error: Exception | None = None) -> None:
        self.reason = reason
        self.error = error

    def __getattr__(self, attr: str):
        if attr.startswith("__"):
            raise AttributeError(attr)

        def _fail(*_args, **_kwargs):
            if self.error is not None:
                raise_policy_error(self.error)
            from ..config import ProviderUnavailableError

            raise ProviderUnavailableError(f"LLM 프로바이더를 쓸 수 없다: {self.reason}")

        return _fail


def raise_policy_error(error: Exception):
    """보관해 둔 정책 예외와 같은 종류의 새 예외를 낸다(같은 인스턴스를 거듭 던져 traceback이 쌓이지 않게)."""
    remedy = getattr(error, "remedy", None)
    if remedy is not None:
        raise type(error)(str(error), remedy=remedy)
    raise type(error)(str(error))


def member_policy_unavailable(ctx, kind: str) -> Exception | None:
    """멤버 컨텍스트에서 kind(llm|stt|vlm)를 만들 수 없으면 그 정책 예외, 만들 수 있으면 None.

    순서: 프로바이더 없음(``ProviderNotConfigured``) → 기능 꺼짐(``ProviderNotAllowed``) → host-cli 분류(``ProviderNotAllowed``).
    """
    from ..accounts.provider_policy import (
        ProviderNotAllowed,
        ProviderNotConfigured,
        ProviderPolicyError,
        require_provider_allowed,
    )

    provider = str(getattr(ctx, "provider", "") or "").strip().lower()
    if provider in ("", "none") or not getattr(ctx, "api_key", ""):
        return ProviderNotConfigured(
            f"{kind}: no LLM provider is configured for this account",
            remedy="/me에서 API 키를 등록하거나 관리자에게 서버 등급을 요청한다. 원문 저장과 검색은 계속 동작한다.",
        )
    feature = {"stt": "stt", "vlm": "vlm"}.get(kind)
    if feature is not None and not ctx.feature_allowed(feature):
        return ProviderNotAllowed(
            f"{kind}: feature is not allowed for this account",
            remedy="자기 API 키를 등록하거나 관리자에게 서버 등급에서 이 기능을 켜 달라고 요청한다.",
        )
    try:
        require_provider_allowed(ctx, provider)
    except ProviderPolicyError as exc:
        return exc
    return None


def _accepts_kwarg(fn: Any, kw: str) -> bool:
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return False
    return kw in sig.parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())


def _provider_default_model(settings: Any, provider_name: str, slot: str) -> str:
    if provider_name == "gemini":
        if slot == "embed":
            return str(getattr(settings, "gemini_embed_model", "gemini-embedding-001") or "gemini-embedding-001")
        return str(getattr(settings, "gemini_model", "") or "")
    if provider_name == "antigravity":
        return str(getattr(settings, "agy_model", "") or getattr(settings, "antigravity_model", "") or "")
    if provider_name == "codex":
        return str(getattr(settings, "codex_model", "") or "")
    if provider_name == "mock":
        return "mock-embed-64d" if slot == "embed" else "mock"
    return ""


def _provider_default_effort(settings: Any, provider_name: str) -> str:
    if provider_name == "gemini":
        return str(getattr(settings, "gemini_effort", "") or "")
    if provider_name == "antigravity":
        return str(getattr(settings, "agy_effort", "") or getattr(settings, "antigravity_effort", "") or "")
    if provider_name == "codex":
        return str(getattr(settings, "codex_effort", "") or "")
    return ""


def _resolve_slot_spec(settings: Any, slot: str) -> tuple[str, str, str]:
    from ..accounts.provider_policy import HOST_CLI, classify_provider, context_of

    routes = getattr(settings, "feature_routing", None) or {}
    entry = dict(routes.get(slot) or {}) if isinstance(routes, dict) else {}
    ctx = context_of(settings)
    is_admin_scope = ctx is None or bool(ctx.is_admin)
    pcli = getattr(settings, "personal_cli", None) if is_admin_scope else None
    pcli_enabled = isinstance(pcli, dict) and bool(pcli.get("enabled"))
    pcli_active = str(pcli.get("active_cli") or "").strip().lower() if pcli_enabled else ""
    if pcli_enabled:
        pcli_routes = pcli.get("feature_routing") if isinstance(pcli.get("feature_routing"), dict) else {}
        pcli_slot = pcli_routes.get(slot) if isinstance(pcli_routes.get(slot), dict) else {}
        for k, v in pcli_slot.items():
            if v is not None and v != "" and not (k == "provider" and str(v).strip().lower() == "inherit"):
                entry[k] = v
    member_explicit_provider = False
    if ctx is not None and not ctx.is_admin:
        fm = getattr(ctx, "feature_models", None) or {}
        if isinstance(fm, dict) and isinstance(fm.get(slot), dict):
            m_slot = fm[slot]
            if str(m_slot.get("provider") or "").strip():
                member_explicit_provider = True
            for k, v in m_slot.items():
                if v is not None and v != "":
                    entry[k] = v
    raw_prov = str(entry.get("provider") or "inherit").strip().lower()
    if raw_prov in ("", "inherit"):
        if pcli_enabled and pcli_active in ("antigravity", "codex") and slot not in ("embed", "stt", "vlm"):
            target_prov = pcli_active
        else:
            target_prov = str(getattr(settings, "effective_provider", "") or getattr(settings, "provider", "") or "mock").strip().lower()
    else:
        target_prov = raw_prov
    if ctx is not None and not ctx.is_admin and not member_explicit_provider:
        ctx_prov = str(getattr(ctx, "provider", "") or "").strip().lower()
        if ctx_prov and ctx_prov != "none" and classify_provider(ctx_prov) != HOST_CLI and (classify_provider(target_prov) == HOST_CLI or target_prov == "mock"):
            target_prov = ctx_prov
    target_model = str(entry.get("model") or "").strip() or _provider_default_model(settings, target_prov, slot)
    target_effort = str(entry.get("effort") or "").strip() or _provider_default_effort(settings, target_prov)
    return target_prov, target_model, target_effort


def _has_feature_routing_override(settings: Any) -> bool:
    from ..accounts.provider_policy import FEATURE_SLOTS, context_of

    routes = getattr(settings, "feature_routing", None) or {}
    if isinstance(routes, dict):
        for slot in FEATURE_SLOTS:
            entry = routes.get(slot)
            if not isinstance(entry, dict):
                continue
            prov = str(entry.get("provider") or "inherit").strip().lower()
            mod = str(entry.get("model") or "").strip()
            eff = str(entry.get("effort") or "").strip()
            if prov not in ("", "inherit") or mod or eff:
                return True
    ctx = context_of(settings)
    if ctx is None or ctx.is_admin:
        pcli = getattr(settings, "personal_cli", None)
        if isinstance(pcli, dict) and pcli.get("enabled"):
            p_act = str(pcli.get("active_cli") or "").strip().lower()
            if p_act in ("antigravity", "codex"):
                return True
            p_routes = pcli.get("feature_routing") if isinstance(pcli.get("feature_routing"), dict) else {}
            if any(isinstance(v, dict) and (str(v.get("provider") or "inherit").strip().lower() != "inherit" or v.get("model") or v.get("effort")) for v in p_routes.values()):
                return True
    if ctx is not None and not ctx.is_admin:
        fm = getattr(ctx, "feature_models", None) or {}
        if isinstance(fm, dict) and any(isinstance(v, dict) and any(v.get(k) for k in ("provider", "model", "effort")) for v in fm.values()):
            return True
    return False


def _clone_settings_for_slot(settings: Any, *, slot: str, target_provider: str, target_model: str, target_effort: str) -> Any:
    updates: dict[str, Any] = {"feature_routing": {}, "personal_cli": {}}
    if target_provider and target_provider not in ("unavailable", "none"):
        updates["provider"] = target_provider
    if target_model:
        if target_provider == "gemini":
            if slot == "embed":
                updates["gemini_embed_model"] = target_model
            else:
                updates["gemini_model"] = target_model
        elif target_provider == "antigravity":
            updates["agy_model"] = target_model
            updates["antigravity_model"] = target_model
        elif target_provider == "codex":
            updates["codex_model"] = target_model
    if target_effort:
        if target_provider == "gemini":
            updates["gemini_effort"] = target_effort
        elif target_provider == "antigravity":
            updates["agy_effort"] = target_effort
            updates["antigravity_effort"] = target_effort
        elif target_provider == "codex":
            updates["codex_effort"] = target_effort
    from ..accounts.provider_policy import context_of

    ctx = context_of(settings)
    if hasattr(settings, "model_copy"):
        cloned = settings.model_copy(update=updates)
        if ctx is not None:
            setattr(cloned, "provider_context", ctx)
        return cloned
    from types import SimpleNamespace

    data = dict(vars(settings))
    data.update(updates)
    if hasattr(settings, "effective_provider"):
        data["effective_provider"] = target_provider or getattr(settings, "effective_provider", "mock")
    if ctx is not None:
        data["provider_context"] = ctx
    return SimpleNamespace(**data)


class LineageCollector:
    """Thread-safe collector tracking per-stage provider/model/effort execution and latency."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.events: list[dict[str, Any]] = []
        self.jev: dict[str, Any] = {}

    def record_stage(
        self,
        *,
        stage: str,
        requested_provider: str,
        requested_model: str,
        requested_effort: str,
        final_provider: str,
        final_model: str,
        status: str = "ok",
        duration_ms: float | None = None,
        fallback_occurred: bool = False,
        fallback_reason: str = "",
        detail: str = "",
    ) -> None:
        dur = round(float(duration_ms), 2) if duration_ms is not None else None
        with self._lock:
            for existing in self.events:
                if existing["stage"] == stage and existing["final_provider"] == final_provider and existing["final_model"] == final_model and existing["status"] == status and existing["requested_effort"] == requested_effort:
                    existing["calls"] = int(existing.get("calls", 1)) + 1
                    if dur is not None:
                        prev = existing.get("duration_ms") or 0.0
                        existing["duration_ms"] = round(float(prev) + dur, 2)
                    if detail:
                        existing["detail"] = detail
                    return
            self.events.append({
                "stage": stage,
                "requested_provider": requested_provider,
                "requested_model": requested_model,
                "requested_effort": requested_effort,
                "final_provider": final_provider,
                "final_model": final_model,
                "status": status,
                "duration_ms": dur,
                "calls": 1,
                "fallback_occurred": bool(fallback_occurred),
                "fallback_reason": fallback_reason,
                "detail": detail,
            })

    def set_jev(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self.jev = dict(payload)

    def summarize(self) -> dict[str, Any]:
        with self._lock:
            events = [dict(ev) for ev in self.events]
            jev = dict(self.jev)
        final_models: dict[str, dict[str, Any]] = {}
        fallbacks: list[dict[str, Any]] = []
        for ev in events:
            final_models[ev["stage"]] = {
                "provider": ev["final_provider"],
                "model": ev["final_model"],
                "effort": ev["requested_effort"],
                "status": ev["status"],
                "calls": ev.get("calls", 1),
            }
            if ev.get("fallback_occurred"):
                fallbacks.append(ev)
        return {
            "stages": events,
            "jev": jev,
            "final_output_models": final_models,
            "fallbacks_triggered": fallbacks,
        }


class FeatureRoutedProvider:
    """Provider facade dispatching each pipeline stage to its configured slot provider."""

    def __init__(self, settings: Any) -> None:
        from ..accounts.provider_policy import context_of

        self.settings = settings
        self.lineage = LineageCollector()
        self.provider_context = context_of(settings)
        self.credential_key = getattr(self.provider_context, "credential_key", "admin") if self.provider_context else "admin"
        self.usage_hooks = None
        self._lock = threading.Lock()
        self._slots: dict[str, Provider] = {}
        self._specs: dict[str, tuple[str, str, str]] = {}
        eg_prov, eg_model, eg_effort = self.slot_spec("extract_graph")
        self.name = eg_prov
        self.model = eg_model
        self.effort = eg_effort

    def slot_spec(self, slot: str) -> tuple[str, str, str]:
        with self._lock:
            spec = self._specs.get(slot)
            if spec is None:
                spec = _resolve_slot_spec(self.settings, slot)
                self._specs[slot] = spec
            return spec

    def slot_provider(self, slot: str) -> Provider:
        with self._lock:
            cached = self._slots.get(slot)
            if cached is not None:
                if self.usage_hooks is not None:
                    setattr(cached, "usage_hooks", self.usage_hooks)
                return cached
            target_prov, target_model, target_effort = self._specs.get(slot) or _resolve_slot_spec(self.settings, slot)
            self._specs[slot] = (target_prov, target_model, target_effort)
            slot_settings = _clone_settings_for_slot(self.settings, slot=slot, target_provider=target_prov, target_model=target_model, target_effort=target_effort)
            prov = _build_direct_provider(slot_settings)
            if self.usage_hooks is not None:
                setattr(prov, "usage_hooks", self.usage_hooks)
            self._slots[slot] = prov
            return prov

    @property
    def embed_model(self) -> str:
        _, target_model, _ = self.slot_spec("embed")
        return target_model

    @property
    def embed_provider(self) -> str:
        target_prov, _, _ = self.slot_spec("embed")
        if target_prov in ("codex", "antigravity") and getattr(self.settings, "gemini_api_key", ""):
            return "gemini"
        return target_prov

    def has_embed_capability(self) -> bool:
        prov = self.slot_provider("embed")
        if hasattr(prov, "has_embed_capability"):
            return bool(prov.has_embed_capability())
        pname = getattr(prov, "name", "")
        if pname == "unavailable":
            return False
        return pname not in ("codex", "antigravity") or bool(getattr(self.settings, "gemini_api_key", ""))

    def _call_stage(self, stage: str, fn: Any, *, effort_override: str | None = None, result_model_attr: str | None = None) -> Any:
        req_prov, req_model, req_effort = self.slot_spec(stage)
        eff_label = effort_override if effort_override is not None else req_effort
        prov = self.slot_provider(stage)
        actual_prov = str(getattr(prov, "name", req_prov) or req_prov)
        started = time.perf_counter()
        try:
            out = fn(prov)
        except Exception as exc:
            elapsed = (time.perf_counter() - started) * 1000.0
            self.lineage.record_stage(stage=stage, requested_provider=req_prov, requested_model=req_model, requested_effort=eff_label, final_provider=actual_prov, final_model=req_model, status="error", duration_ms=elapsed, detail=str(exc)[:240])
            raise
        elapsed = (time.perf_counter() - started) * 1000.0
        final_model = (getattr(out, result_model_attr, None) if result_model_attr else None) or getattr(prov, "model", None) or req_model
        self.lineage.record_stage(stage=stage, requested_provider=req_prov, requested_model=req_model, requested_effort=eff_label, final_provider=actual_prov, final_model=str(final_model or ""), status="ok", duration_ms=elapsed)
        return out

    def extract(self, doc: Document, ontology_block: str = "", *, effort: str | None = None) -> ExtractionResult:
        def _invoke(p: Provider) -> ExtractionResult:
            if effort is not None and _accepts_kwarg(p.extract, "effort"):
                return p.extract(doc, ontology_block, effort=effort)
            return p.extract(doc, ontology_block)

        return self._call_stage("extract_graph", _invoke, effort_override=effort, result_model_attr="model")

    def summarize_document(self, doc: Document, *, effort: str | None = None) -> str:
        def _invoke(p: Provider) -> str:
            if hasattr(p, "summarize_document"):
                if effort is not None and _accepts_kwarg(p.summarize_document, "effort"):
                    return p.summarize_document(doc, effort=effort)
                return p.summarize_document(doc)
            return p.extract(doc, "").summary

        return self._call_stage("summarize", _invoke, effort_override=effort)

    def render_detail(self, doc: Document, format: str = "adoc", focus: str | None = None, *, effort: str | None = None) -> str:
        def _invoke(p: Provider) -> str:
            if effort is not None and _accepts_kwarg(p.render_detail, "effort"):
                return p.render_detail(doc, format=format, focus=focus, effort=effort)
            return p.render_detail(doc, format=format, focus=focus)

        return self._call_stage("summarize", _invoke, effort_override=effort)

    def compose_presentation(self, doc: Document, **kwargs: Any) -> str:
        return self._call_stage("summarize", lambda p: p.compose_presentation(doc, **kwargs), effort_override=kwargs.get("effort"))

    def embed(self, text: str, *, task_type: str | None = None, title: str | None = None) -> list[float]:
        req_prov, req_model, _ = self.slot_spec("embed")
        prov = self.slot_provider("embed")
        actual_prov = self.embed_provider or str(getattr(prov, "name", req_prov) or req_prov)
        started = time.perf_counter()
        try:
            out = prov.embed(text, task_type=task_type, title=title)
        except Exception as exc:
            elapsed = (time.perf_counter() - started) * 1000.0
            self.lineage.record_stage(stage="embed", requested_provider=req_prov, requested_model=req_model, requested_effort="", final_provider=actual_prov, final_model=req_model, status="error", duration_ms=elapsed, detail=str(exc)[:240])
            raise
        elapsed = (time.perf_counter() - started) * 1000.0
        self.lineage.record_stage(stage="embed", requested_provider=req_prov, requested_model=req_model, requested_effort="", final_provider=actual_prov, final_model=req_model, status="ok", duration_ms=elapsed)
        return out

    def judge_same_entity(self, mc: MergeCandidate, *, document_id: str | None = None) -> bool:
        def _invoke(p: Provider) -> bool:
            if document_id is not None and _accepts_kwarg(p.judge_same_entity, "document_id"):
                return p.judge_same_entity(mc, document_id=document_id)
            return p.judge_same_entity(mc)

        return self._call_stage("entity_judge", _invoke)

    def judge_relationship(self, rc: RelationCandidate, *, document_id: str | None = None) -> RelationJudgement:
        def _invoke(p: Provider) -> RelationJudgement:
            if document_id is not None and _accepts_kwarg(p.judge_relationship, "document_id"):
                return p.judge_relationship(rc, document_id=document_id)
            return p.judge_relationship(rc)

        return self._call_stage("relation_judge", _invoke)

    def classify_paper(self, doc: Document, *, effort: str | None = None):
        def _invoke(p: Provider):
            if effort is not None and _accepts_kwarg(p.classify_paper, "effort"):
                return p.classify_paper(doc, effort=effort)
            return p.classify_paper(doc)

        return self._call_stage("classify", _invoke, effort_override=effort)

    def classify_watch(self, doc: Document) -> dict:
        return self._call_stage("classify", lambda p: p.classify_watch(doc))

    def research(self, query: str, context: str) -> dict:
        return self._call_stage("research", lambda p: p.research(query, context))

    def judge_research(self, query: str, context: str, report: str) -> dict:
        return self._call_stage("research", lambda p: p.judge_research(query, context, report))

    def select_followups(self, context: str, candidates: list[dict]) -> list[int]:
        return self._call_stage("research", lambda p: p.select_followups(context, candidates))

    def summarize_search(self, query: str, context: str) -> str:
        return self._call_stage("research", lambda p: p.summarize_search(query, context))


def _build_direct_provider(settings) -> Provider:
    from ..accounts.provider_policy import context_of, require_provider_allowed

    ctx = context_of(settings)
    member = ctx is not None and not ctx.is_admin
    if member:
        err = member_policy_unavailable(ctx, "llm")
        if err is not None:
            return UnavailableProvider(str(err), error=err)
    eff = settings.effective_provider
    if eff == "unavailable":
        return UnavailableProvider(settings.provider_unavailable_reason("llm") or "")
    if member:
        require_provider_allowed(ctx, eff)
        require_provider_allowed(ctx, getattr(settings, "provider", ""))
    if eff == "codex":
        from .codex_provider import CodexProvider  # lazy import

        return CodexProvider(settings)
    if eff == "antigravity":
        from .antigravity_provider import AntigravityProvider  # lazy import

        return AntigravityProvider(settings)
    if eff == "gemini":
        from .gemini_provider import GeminiProvider  # lazy import

        return GeminiProvider(settings)
    return MockProvider()


def get_provider(settings, *, feature: str | None = None) -> Provider:
    """Return a Provider instance or FeatureRoutedProvider based on settings and optional feature slot."""
    if feature is not None:
        target_prov, target_model, target_effort = _resolve_slot_spec(settings, feature)
        slot_settings = _clone_settings_for_slot(settings, slot=feature, target_provider=target_prov, target_model=target_model, target_effort=target_effort)
        return _build_direct_provider(slot_settings)
    if _has_feature_routing_override(settings):
        return FeatureRoutedProvider(settings)
    return _build_direct_provider(settings)
