"""Gemini provider — structured 추출 + 임베딩.

provider.py 의 ExtractionResult 계약(= mock 이 내는 구조)을 그대로 채운다.
google-genai 의 response_schema(Pydantic) 로 구조화 출력을 강제한다.

rate limit(429)/서버오류(5xx) 보호: 모든 호출은 _call() 을 거쳐 프로세스 전역
throttle(min_interval) + 지수 백오프 재시도를 받는다.
"""

from __future__ import annotations

import re
import threading
import time as _time

from ..ontology.base import Document
from ..ontology.registry import ontology_prompt_block
from .provider import ExtractionResult, MergeCandidate

# 추출 프롬프트 버전. _SYS 를 바꾸면 올린다(재적재 시 어떤 프롬프트로 뽑았는지 추적).
PROMPT_VERSION = "extract-v2"

# 프로세스 전역 throttle: 모든 Gemini 호출이 공유하는 최소 간격과 마지막 호출 시각.
_CALL_LOCK = threading.Lock()
_LAST_CALL = [0.0]
_RETRYABLE = (429, 500, 503)

_SYS = """You extract a knowledge-graph fragment from a single source document for a
personal knowledge base about AI/software tools and research.

{ontology}

Rules:
- summary: 1-3 sentences, factual, in the document's language.
- entities: the key things this document is ABOUT (tools, repos, models, people, orgs, concepts...).
- Do NOT create an entity for the publishing platform, source site, news aggregator, or
  forum that merely HOSTS or links to this content (e.g. GeekNews, Hacker News, Reddit,
  a Discourse forum, PyTorch Korea, a personal blog). Include such a site ONLY if the
  document is genuinely ABOUT that platform itself.
- For each entity pick the single best `type` from the list. If truly none fits,
  leave type as your best guess AND set `proposed_type` to a snake_case suggestion.
- relations: typed edges between entities you listed (reference them by exact `name`).
  Use the relation types provided; only set `proposed_type` if none fits.
- Do NOT invent facts not supported by the document.
"""


def _retry_delay_from_error(err) -> float | None:  # noqa: ANN001
    """에러 메시지에서 권장 retry 지연(초)을 best-effort 로 추출."""
    m = re.search(r"retryDelay['\"]?:\s*['\"]?(\d+(?:\.\d+)?)s", str(err))
    return float(m.group(1)) if m else None


def _is_retryable(err) -> bool:  # noqa: ANN001
    """429/5xx 류 일시적 오류인지 판정(라이브러리 비의존, duck-typed)."""
    code = getattr(err, "code", None) or getattr(err, "status_code", None)
    if code in _RETRYABLE:
        return True
    msg = str(err)
    return any(s in msg for s in ("429", "RESOURCE_EXHAUSTED", "503", "500",
                                  "UNAVAILABLE", "rate limit"))


# 짧은 백오프로 안 풀리는(=지금 재시도 무의미한) 429 신호. 분당 rate limit 과 구분.
#  - daily quota 소진: perday/daily
#  - 결제/크레딧 소진: credits depleted / billing / prepayment (실제 관측, 2026-06-09)
_DAILY_MARKERS = ("perday", "per day", "per-day", "daily limit", "daily quota",
                  "credit", "depleted", "billing", "prepayment")
_DAILY_RETRY_THRESHOLD = 120.0  # retryDelay 가 이 이상이면 분당 rate 가 아니라 장기 소진


def _is_daily_quota(err) -> bool:  # noqa: ANN001
    """장기 소진(일일 quota/결제 크레딧)이라 지금 재시도가 무의미한지. 분당 rate 와 구분.

    보수적: 마커 또는 비정상적으로 큰 retryDelay 일 때만 True. 못 잡으면 기존대로 재시도
    (false-open 최소화 — 오판해 fail-fast 해도 recover-loop 가 긴 호라이즌에 회복).
    """
    msg = str(err).lower()
    if any(m in msg for m in _DAILY_MARKERS):
        return True
    d = _retry_delay_from_error(err)
    return d is not None and d >= _DAILY_RETRY_THRESHOLD


class GeminiProvider:
    name = "gemini"

    def __init__(self, settings):  # noqa: ANN001
        from google import genai

        self._genai = genai
        self.settings = settings
        self.client = genai.Client(api_key=settings.gemini_api_key)
        self.model = settings.gemini_model
        self.embed_model = settings.gemini_embed_model
        self.min_interval = settings.gemini_min_interval
        self.max_retries = settings.gemini_max_retries

    def _throttle(self) -> None:
        """호출 간 최소 간격 보장(RPM 보호). 전역 락으로 프로세스 내 직렬화."""
        with _CALL_LOCK:
            wait = self.min_interval - (_time.monotonic() - _LAST_CALL[0])
            if wait > 0:
                _time.sleep(wait)
            _LAST_CALL[0] = _time.monotonic()

    def _call(self, fn):  # noqa: ANN001
        """throttle + 429/5xx 지수 백오프 재시도로 Gemini 호출을 감싼다."""
        last = None
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                return fn()
            except Exception as e:  # noqa: BLE001
                if not _is_retryable(e) or attempt == self.max_retries:
                    raise
                # circuit breaker(프로세스-로컬, 무상태): 일일 quota 소진은 짧은 백오프로
                # 안 풀리므로 max_retries×60s 를 태우지 않고 즉시 raise → raw_inbox error →
                # recover-loop 가 지수백오프(300s~)로 회복 담당.
                if _is_daily_quota(e):
                    raise
                last = e
                delay = _retry_delay_from_error(e) or (2.0 ** attempt) * 3.0
                _time.sleep(min(delay, 60.0))
        if last:
            raise last
        raise RuntimeError("unreachable")

    def extract(self, doc: Document, ontology_block: str | None = None) -> ExtractionResult:
        from google.genai import types as gtypes

        block = ontology_block or ontology_prompt_block()
        sys = _SYS.format(ontology=block)
        body = _doc_to_prompt(doc)

        cfg = gtypes.GenerateContentConfig(
            system_instruction=sys,
            response_mime_type="application/json",
            response_schema=ExtractionResult,
            temperature=0.2,
        )
        try:
            resp = self._call(lambda: self.client.models.generate_content(
                model=self.model, contents=body, config=cfg))
        except Exception as e:  # noqa: BLE001
            # rate limit/서버오류는 재시도 후에도 실패 → 올려서 raw_inbox 에 error 로
            # 남기고 나중에 replay-failed 로 재적재. 그 외(schema 거부 등)는 폴백.
            if _is_retryable(e):
                raise
            return self._extract_json_fallback(sys, body)

        parsed = getattr(resp, "parsed", None)
        result = parsed if isinstance(parsed, ExtractionResult) else _coerce(resp.text)
        result.raw_response = resp.text or ""
        result.model = self.model
        result.prompt_version = PROMPT_VERSION
        return result

    def _extract_json_fallback(self, sys: str, body: str) -> ExtractionResult:
        prompt = (
            sys
            + "\n\nReturn ONLY valid JSON matching this shape:\n"
            + '{"summary":str,"key_claims":[str],'
            '"entities":[{"name":str,"type":str,"aliases":[str],'
            '"observations":[str],"proposed_type":str|null}],'
            '"relations":[{"source":str,"target":str,"type":str,"proposed_type":str|null}]}'
            + "\n\nDOCUMENT:\n"
            + body
        )
        resp = self._call(lambda: self.client.models.generate_content(
            model=self.model, contents=prompt))
        result = _coerce(resp.text)
        result.raw_response = resp.text or ""
        result.model = self.model
        result.prompt_version = PROMPT_VERSION
        return result

    def embed(self, text: str) -> list[float]:
        resp = self._call(lambda: self.client.models.embed_content(
            model=self.embed_model, contents=text[:8000] or " "))
        return list(resp.embeddings[0].values)

    def summarize_search(self, query: str, context: str) -> str:
        """검색된 컨텍스트만 사용해 질의에 답한다(인용 포함, 환각 억제)."""
        prompt = (
            "You answer the user's query using ONLY the knowledge-base context below. "
            "Do not invent facts beyond it. Cite entities in [brackets]. "
            "If the context is insufficient, say so plainly. "
            "Write the answer in Korean (한국어), but keep proper nouns, product/tool "
            "names, and technical terms in their original form (do not transliterate). "
            "Be concise.\n\n"
            f"QUERY: {query}\n\nCONTEXT:\n{context}\n\nANSWER:"
        )
        resp = self._call(lambda: self.client.models.generate_content(
            model=self.model, contents=prompt))
        return (resp.text or "").strip()

    def judge_same_entity(self, mc: MergeCandidate) -> bool:
        """두 엔티티가 동일한 실세계 대상인지 LLM 으로 판정(borderline 후보에만)."""
        prompt = (
            "Decide if these two knowledge-base entries refer to the SAME real-world "
            "entity (e.g. a renamed/aliased tool), NOT merely related ones.\n"
            "Different products in the same space are NOT the same.\n\n"
            f"A: name={mc.new_name!r} type={mc.new_type!r}\n"
            f"   notes={' | '.join(mc.new_observations)[:400]}\n"
            f"B: name={mc.cand_name!r} type={mc.cand_type!r} aliases={mc.cand_aliases}\n"
            f"   notes={' | '.join(mc.cand_observations)[:400]}\n\n"
            "Answer with exactly one word: SAME or DIFFERENT."
        )
        try:
            resp = self._call(lambda: self.client.models.generate_content(
                model=self.model, contents=prompt))
            return (resp.text or "").strip().upper().startswith("SAME")
        except Exception:  # noqa: BLE001
            return False  # 판정 실패 시 보수적으로 분리(거짓 병합 방지)


def _doc_to_prompt(doc: Document) -> str:
    head = []
    if doc.title:
        head.append(f"TITLE: {doc.title}")
    if doc.url:
        head.append(f"URL: {doc.url}")
    head.append(f"SOURCE_TYPE: {doc.source_type}")
    return "\n".join(head) + "\n\nCONTENT:\n" + (doc.raw_text or "")[:12000]


def _coerce(text: str | None) -> ExtractionResult:
    import json

    if not text:
        return ExtractionResult()
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(json)?", "", s).strip().rstrip("`").strip()
    try:
        return ExtractionResult.model_validate(json.loads(s))
    except Exception:  # noqa: BLE001
        m = re.search(r"\{.*\}", s, re.DOTALL)
        if m:
            try:
                return ExtractionResult.model_validate(json.loads(m.group(0)))
            except Exception:  # noqa: BLE001
                pass
    return ExtractionResult(summary=s[:300])
