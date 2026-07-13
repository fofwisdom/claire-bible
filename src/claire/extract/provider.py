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
import threading
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


# --- LLM 호출 진행 이벤트(스레드-로컬) ---
# 긴 작업(맥락 조사 등)이 rate limit 대기/재시도 같은 내부 상황을 실시간으로 UI 에
# 흘릴 수 있게 한다. provider 인스턴스는 봇/API 가 공유하므로 인스턴스 속성 대신
# **스레드-로컬** — 콜백을 등록한 스레드의 호출에만 이벤트가 가고 다른 요청과 안 섞인다.
_progress_local = threading.local()


def set_progress_callback(cb) -> None:  # noqa: ANN001 — Callable[[str], None] | None
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

    relevance: 보고서가 '주어진 맥락 안에서의 그 키워드 의미'를 다루는가(0~1).
      다의어가 다른 의미로 새는 오염을 여기서 걸러낸다.
    quality: 사실성·구체성·출처 충실도(0~1).
    same_subject: [1홉 병합 전용, ONEHOP_MERGE_DESIGN.md §3.1] 대상(query/report)이 맥락이
      다루는 것 **그 자체**(공식 저장소·공식 문서·공식 사이트 등 1차 출처)에 관한 내용이면
      True, 맥락과 관련은 있으나 사실상 별개의 소재(다른 프로젝트/사건/제3자 논의)면 False.
      contextual research(맥락 확장 조사) 호출부는 이 필드를 사용하지 않는다(무시해도 무해).
    """

    relevance: float
    quality: float
    same_subject: bool = True
    interpretation: str = ""  # 맥락 내에서 키워드를 어떤 의미로 해석했는지(한 문장)
    reason: str = ""


class FollowSelection(BaseModel):
    """1홉 자동확장 — 따라갈 후보 링크의 인덱스 목록(LLM 이 선별).

    follow: 입력 후보에서 '지식으로 더 팔 가치가 있다'고 판단한 항목의 0-기반 인덱스.
      가치 없으면 빈 목록(= 파고들지 않음). 다의어/잡음/맥락 무관은 제외.
    """

    follow: list[int] = Field(default_factory=list)
    reason: str = ""


class WatchClassification(BaseModel):
    """[주기 크롤링] 이 문서가 '주기적으로 내용이 바뀌는 콘텐츠'인가.

    watch: 리더보드/벤치마크 순위표/실시간 통계/가격/랭킹처럼 지속 갱신되면 True;
      뉴스/블로그/논문/일회성 설명/문서면 False.
    interval_days: watch=True 일 때 재확인 권장 주기(일). 아니면 None.
    """

    watch: bool = False
    interval_days: int | None = None
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
        """결정론적 stub — 문서 가독 렌더링(detail, 마크다운) 파이프라인 연결만 보장.

        실제 분량/품질(A4 1~2장 마크다운+강조+이미지 큐레이션)은 실 Gemini 로 검증한다.
        여기선 마크다운 구조·강조·수집 이미지가 detail 로 흘러가는지(배선)만 보장한다."""
        title = (doc.title or doc.url or "untitled").strip()
        text = (doc.raw_text or "").strip()
        images = (doc.meta or {}).get("images") or []
        parts = [f"[mock-detail] **{title}**", "", text[:600]]
        if images:  # 수집된 첫 이미지를 마크다운 + 캡션(이탤릭) 으로 끼워 보존/캡션 배선을 드러냄
            im = images[0]
            cap = im.get("caption") or im.get("alt") or "그림"
            src = ("/image?p=" + im["local"]) if im.get("local") else im.get("url", "")
            parts += ["", f"![{im.get('alt', '')}]({src})", f"*{cap}*"]
        return "\n".join(parts)

    def classify_watch(self, doc: Document) -> dict:
        """결정론 stub — 제목/본문에 순위·벤치 키워드 있으면 watch(주기크롤 판단 배선 검증).

        실제 판단은 Gemini. 여기선 키워드로 watch=True/False 가 흘러 set_document_watch 까지
        배선되는지만 보장(테스트). 'leaderboard/벤치/순위/실시간' 등 → watch."""
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

        '무관' 훅은 1홉 자동확장의 store 게이트(judge_research 재사용) 거절 경로 테스트용.
        '별개주제' 훅은 게이트는 통과하되 same_subject=False(1홉 병합 분기 테스트용,
        ONEHOP_MERGE_DESIGN.md §3.1) — 기본은 True(합쳐도 되는 케이스가 흔함을 모사)."""
        low = ("모호" in query) or ("무관" in report) or ("무관" in context)
        same_subject = "별개주제" not in report and "별개주제" not in context
        return {"relevance": 0.2 if low else 0.92, "quality": 0.2 if low else 0.85,
                "same_subject": same_subject,
                "interpretation": f"[mock] '{query}' 를 맥락 내 의미로 해석",
                "reason": "mock judge"}

    def select_followups(self, context: str, candidates: list[dict]) -> list[int]:
        """결정론적 stub — 1홉 확장에서 따라갈 후보 선별(파고들지 여부=LLM 결정 모사).

        url/anchor 에 'skip' 이 들어간 후보는 제외(= 안 판다), 나머지는 따라간다.
        실제 선별 품질은 실 Gemini 로. 여기선 선별→게이트 배선만 결정론적으로 보장한다."""
        out = []
        for i, c in enumerate(candidates):
            blob = f"{c.get('url', '')} {c.get('anchor', '')}".lower()
            if "skip" not in blob:
                out.append(i)
        return out


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
