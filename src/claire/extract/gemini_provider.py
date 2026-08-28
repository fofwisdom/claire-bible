"""Gemini provider — structured 추출 + 임베딩 (Interactions API 기반).

provider.py 의 ExtractionResult 계약(= mock 이 내는 구조)을 그대로 채운다.
google-genai 의 interactions API (response_format) 로 구조화 출력을 강제한다.

rate limit(429)/서버오류(5xx) 보호: 모든 호출은 _call() 을 거쳐 프로세스 전역
throttle(min_interval) + 지수 백오프 재시도를 받는다.
"""

from __future__ import annotations

import re
import threading
import time as _time

from ..ontology.base import Document
from ..ontology.registry import ontology_prompt_block
from .prompts import (
    _MERGED_DETAIL_MIN_CHARS,
    PROMPT_VERSION,
    classify_watch_prompt,
    clean_plain_summary,
    extract_fallback_prompt,
    extract_system_prompt,
    judge_research_prompt,
    judge_same_entity_prompt,
    render_detail_prompt,
    research_prompt,
    select_followups_prompt,
    summarize_search_prompt,
)
from .prompts import (
    doc_to_prompt as _doc_to_prompt,
)
from .prompts import (
    images_block as _images_block,
)
from .provider import (
    ExtractionResult,
    FollowSelection,
    MergeCandidate,
    ResearchJudgement,
    WatchClassification,
    emit_progress,
)

# 프로세스 전역 throttle: 모든 Gemini 호출이 공유하는 최소 간격과 마지막 호출 시각.
_CALL_LOCK = threading.Lock()
_LAST_CALL = [0.0]
_RETRYABLE = (429, 500, 503)


def _retry_delay_from_error(err) -> float | None:
    """에러 메시지에서 권장 retry 지연(초)을 best-effort 로 추출."""
    m = re.search(r"retryDelay['\"]?:\s*['\"]?(\d+(?:\.\d+)?)s", str(err))
    return float(m.group(1)) if m else None


def _is_retryable(err) -> bool:
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


def _is_daily_quota(err) -> bool:
    """장기 소진(일일 quota/결제 크레딧)이라 지금 재시도가 무의미한지. 분당 rate 와 구분.

    보수적: 마커 또는 비정상적으로 큰 retryDelay 일 때만 True. 못 잡으면 기존대로 재시도
    (false-open 최소화 — 오판해 fail-fast 해도 recover-loop 가 긴 호라이즌에 회복).
    """
    msg = str(err).lower()
    if any(m in msg for m in _DAILY_MARKERS):
        return True
    d = _retry_delay_from_error(err)
    return d is not None and d >= _DAILY_RETRY_THRESHOLD


def _extract_output_text(interaction) -> str:
    """Interactions 응답에서 출력 텍스트를 방어적으로 추출."""
    if interaction is None:
        return ""
    if isinstance(interaction, str):
        return interaction
    out = getattr(interaction, "output_text", None)
    if isinstance(out, str) and out:
        return out
    outputs = getattr(interaction, "outputs", None)
    if outputs and len(outputs) > 0:
        last = outputs[-1]
        t = getattr(last, "text", None) or (last.get("text") if isinstance(last, dict) else None)
        if isinstance(t, str) and t:
            return t
    t = getattr(interaction, "text", None)
    if isinstance(t, str):
        return t
    return str(interaction)


def _extract_sources(interaction) -> list[dict]:
    """Interactions 응답에서 grounding 출처 목록을 방어적으로 추출."""
    sources: list[dict] = []
    seen_urls: set[str] = set()

    for step in getattr(interaction, "steps", []) or []:
        for block in getattr(step, "content", []) or []:
            annotations = getattr(block, "annotations", None) or []
            for ann in annotations:
                url = getattr(ann, "url", None) or (ann.get("url") if isinstance(ann, dict) else None)
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    title = getattr(ann, "title", "") or (ann.get("title", "") if isinstance(ann, dict) else "") or ""
                    sources.append({"title": title, "url": url})

    if sources:
        return sources

    # Legacy / GenerateContent candidates grounding_metadata fallback
    try:
        candidates = getattr(interaction, "candidates", None) or []
        if candidates:
            gm = getattr(candidates[0], "grounding_metadata", None)
            for ch in (getattr(gm, "grounding_chunks", None) or []):
                web = getattr(ch, "web", None)
                if web and getattr(web, "uri", None):
                    uri = web.uri
                    if uri not in seen_urls:
                        seen_urls.add(uri)
                        sources.append({"title": getattr(web, "title", "") or "", "url": uri})
    except Exception:  # noqa: BLE001
        pass

    return sources


class GeminiProvider:
    name = "gemini"

    def __init__(self, settings):
        from google import genai

        self._genai = genai
        self.settings = settings
        self.client = genai.Client(api_key=settings.gemini_api_key)
        self.model = settings.gemini_model
        self.effort = getattr(settings, "gemini_effort", "medium")
        self.embed_model = settings.gemini_embed_model
        self.min_interval = settings.gemini_min_interval
        self.max_retries = settings.gemini_max_retries

    def _build_generation_config(self, extra: dict | None = None) -> dict:
        """기본 generation_config 생성 (사고 레벨/thinking_config 포함)."""
        cfg: dict = {"temperature": 0.2}
        if extra:
            cfg.update(extra)
        effort = str(getattr(self, "effort", "") or "").strip().lower()
        if effort in ("low", "medium", "high", "minimal"):
            cfg["thinking_config"] = {"thinking_level": effort.upper()}
        elif effort.isdigit() or (effort.startswith("-") and effort[1:].isdigit()):
            cfg["thinking_config"] = {"thinking_budget": int(effort)}
        elif effort in ("none", "off", "0"):
            cfg["thinking_config"] = {"thinking_budget": 0}
        return cfg

    def _throttle(self) -> None:
        """호출 간 최소 간격 보장(RPM 보호). 전역 락으로 프로세스 내 직렬화."""
        with _CALL_LOCK:
            wait = self.min_interval - (_time.monotonic() - _LAST_CALL[0])
            if wait > 1.0:
                emit_progress(f"RPM 보호 대기 {wait:.0f}s (호출 간격 조절)")
            if wait > 0:
                _time.sleep(wait)
            _LAST_CALL[0] = _time.monotonic()

    def _call(self, fn):
        """throttle + 429/5xx 지수 백오프 재시도로 Gemini 호출을 감싼다."""
        last = None
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                return fn()
            except Exception as e:
                if not _is_retryable(e) or attempt == self.max_retries:
                    raise
                # circuit breaker(프로세스-로컬, 무상태): 일일 quota 소진은 짧은 백오프로
                # 안 풀리므로 max_retries×60s 를 태우지 않고 즉시 raise → raw_inbox error →
                # recover-loop 가 지수백오프(300s~)로 회복 담당.
                if _is_daily_quota(e):
                    emit_progress("일일 quota 소진 감지 — 중단(자동복구 루프가 처리)")
                    raise
                last = e
                delay = _retry_delay_from_error(e) or (2.0 ** attempt) * 3.0
                delay = min(delay, 60.0)
                emit_progress(f"rate limit/서버오류 — {delay:.0f}s 후 재시도 "
                              f"({attempt + 1}/{self.max_retries})")
                _time.sleep(delay)
        if last:
            raise last
        raise RuntimeError("unreachable")

    def extract(self, doc: Document, ontology_block: str | None = None) -> ExtractionResult:
        block = ontology_block or ontology_prompt_block()
        sys = extract_system_prompt(block)
        body = _doc_to_prompt(doc)

        response_format = {
            "type": "text",
            "mime_type": "application/json",
            "schema": ExtractionResult.extraction_json_schema(),
        }
        try:
            interaction = self._call(lambda: self.client.interactions.create(
                model=self.model,
                input=body,
                system_instruction=sys,
                response_format=response_format,
                generation_config=self._build_generation_config(),
                store=False,
            ))
            raw_text = _extract_output_text(interaction)
            result = _coerce(raw_text)
            result.raw_response = raw_text
            result.model = self.model
            result.prompt_version = PROMPT_VERSION
            return result
        except Exception as e:
            # rate limit/서버오류는 재시도 후에도 실패 → 올려서 raw_inbox 에 error 로
            # 남기고 나중에 replay-failed 로 재적재. 그 외(schema 거부 등)는 폴백.
            if _is_retryable(e):
                raise
            return self._extract_json_fallback(sys, body)

    def _extract_json_fallback(self, sys: str, body: str) -> ExtractionResult:
        prompt = extract_fallback_prompt(sys, body)
        interaction = self._call(lambda: self.client.interactions.create(
            model=self.model,
            input=prompt,
            generation_config=self._build_generation_config(),
            store=False,
        ))
        raw_text = _extract_output_text(interaction)
        result = _coerce(raw_text)
        result.raw_response = raw_text
        result.model = self.model
        result.prompt_version = PROMPT_VERSION
        return result

    def embed(self, text: str) -> list[float]:
        from ..config import get_settings

        limit = get_settings().embed_char_budget
        resp = self._call(lambda: self.client.models.embed_content(
            model=self.embed_model, contents=text[:limit] or " "))
        return list(resp.embeddings[0].values)

    def summarize_search(self, query: str, context: str) -> str:
        """검색된 컨텍스트만 사용해 질의에 답한다(인용 포함, 환각 억제, 문어체)."""
        prompt = summarize_search_prompt(query, context)
        interaction = self._call(lambda: self.client.interactions.create(
            model=self.model,
            input=prompt,
            store=False,
        ))
        return _extract_output_text(interaction).strip()

    def render_detail(
        self, doc: Document, format: str = "md", directive: str | None = None
    ) -> str:
        """원문을 한국어 가독 렌더링(MD 또는 ADOC)으로 '편하게 읽을 수 있는 글'로 재구성(요약 아님).

        짧은 summary 와 별개 — 원문을 직접 안 읽어도 핵심·맥락·세부를 파악할 수 있게
        여러 단락(대략 A4 1~2장)으로 푼다. 구조화 추출과 독립된 별도 호출이라 그래프에
        영향 없음.

        [1홉 병합 전용, ONEHOP_MERGE_DESIGN.md §3.3b] doc.meta.extra_sources 가 있으면
        (여러 출처가 합쳐진 문서) 목표 분량을 "A4 2~4장"으로 올리고 각 출처를 빠짐없이
        통합 서술하라고 지시한다. 결과가 너무 짧으면(두 출처를 담기엔 부족) 목표를 2배씩
        최대 2회 재시도(1x→2x→4x) — 정합성 문제가 아니라 품질 보정이라 fail-open(마지막
        결과를 그대로 채택)."""
        body = _doc_to_prompt(doc)
        images = (doc.meta or {}).get("images") or []
        merged = bool((doc.meta or {}).get("extra_sources"))
        dir_val = directive or (doc.meta or {}).get("directive")
        text = self._render_detail_call(
            body, images, merged=merged, scale=1, format=format, directive=dir_val
        )
        if merged:
            for scale in (2, 4):
                if len(text) >= _MERGED_DETAIL_MIN_CHARS:
                    break
                text = self._render_detail_call(
                    body, images, merged=merged, scale=scale, format=format, directive=dir_val
                )
        return text

    def _render_detail_call(
        self,
        body: str,
        images: list,
        *,
        merged: bool,
        scale: int,
        format: str = "md",
        directive: str | None = None,
    ) -> str:
        prompt = render_detail_prompt(
            body, images, merged=merged, scale=scale, format=format, directive=directive
        )
        interaction = self._call(lambda: self.client.interactions.create(
            model=self.model,
            input=prompt,
            store=False,
        ))
        return _extract_output_text(interaction).strip()

    def classify_watch(self, doc: Document) -> dict:
        """[주기 크롤링] 문서가 '주기적으로 내용이 바뀌는 콘텐츠'인지 판단(별도 경량 호출).

        리더보드/벤치마크 순위표/실시간 통계/가격/랭킹 = watch(주기 재크롤 가치).
        뉴스/블로그/논문/일회성 설명 = 1회성. rate limit 등 실패는 위로 raise(호출측이
        비필수로 조용히 무시 — watch 미판단으로 남고 적재는 정상)."""
        body = _doc_to_prompt(doc)[:4000]
        prompt = classify_watch_prompt(body)
        response_format = {
            "type": "text",
            "mime_type": "application/json",
            "schema": WatchClassification.model_json_schema(),
        }
        interaction = self._call(lambda: self.client.interactions.create(
            model=self.model,
            input=prompt,
            response_format=response_format,
            generation_config={"temperature": 0.0},
            store=False,
        ))
        raw_text = _extract_output_text(interaction)
        try:
            return WatchClassification.model_validate_json(raw_text).model_dump()
        except Exception:
            try:
                import json

                return WatchClassification(**json.loads(raw_text)).model_dump()
            except Exception:  # noqa: BLE001
                return {"watch": False, "interval_days": None, "reason": "판정 파싱 실패"}

    def research(self, query: str, context: str) -> dict:
        """맥락 고정 웹 조사(google_search grounding) → 한국어 보고서 + 출처.

        다의어 위험(사용자 요구): 키워드를 일반 의미가 아니라 **주어진 맥락 안에서의
        의미로만** 해석하도록 강제하고, 맥락과 맞는 자료를 못 찾으면 지어내는 대신
        INSUFFICIENT 를 선언하게 한다. 판정(judge_research)은 별도 호출로 이중 방어."""
        prompt = research_prompt(query, context)
        interaction = self._call(lambda: self.client.interactions.create(
            model=self.model,
            input=prompt,
            tools=[{"type": "google_search"}],
            generation_config={"temperature": 0.3},
            store=False,
        ))
        report = _extract_output_text(interaction).strip()
        sources: list[dict] = _extract_sources(interaction)
        return {"report": report, "sources": sources}

    def judge_research(self, query: str, context: str, report: str) -> dict:
        """조사 보고서가 '맥락 내 의미'와 일치하고 품질이 충분한지 별도 판정.

        research 호출과 분리된 fresh 호출(자기 채점 편향 완화). 판정 실패 시 0점
        (fail-closed) — 불확실하면 그래프에 추가하지 않는다."""
        prompt = judge_research_prompt(query, context, report)
        response_format = {
            "type": "text",
            "mime_type": "application/json",
            "schema": ResearchJudgement.model_json_schema(),
        }
        interaction = None
        try:
            interaction = self._call(lambda: self.client.interactions.create(
                model=self.model,
                input=prompt,
                response_format=response_format,
                generation_config={"temperature": 0.0},
                store=False,
            ))
            raw_text = _extract_output_text(interaction)
            return ResearchJudgement.model_validate_json(raw_text).model_dump()
        except Exception as e:
            if _is_retryable(e):
                raise  # rate limit 은 위로 — 호출측이 오류로 안내(자동복구 루프와 정합)
            try:
                import json

                if interaction is not None:
                    raw_text = _extract_output_text(interaction)
                    return ResearchJudgement(**json.loads(raw_text)).model_dump()
            except Exception:  # noqa: BLE001
                pass
            return {"relevance": 0.0, "quality": 0.0, "same_subject": False,
                    "interpretation": "", "reason": f"판정 실패: {e}"}

    def select_followups(self, context: str, candidates: list[dict]) -> list[int]:
        """1홉 자동확장 — 부모 문서 맥락에서 따라갈(파고들) 가치가 있는 링크를 선별."""
        if not candidates:
            return []
        prompt = select_followups_prompt(context, candidates)
        response_format = {
            "type": "text",
            "mime_type": "application/json",
            "schema": FollowSelection.model_json_schema(),
        }
        interaction = None
        try:
            interaction = self._call(lambda: self.client.interactions.create(
                model=self.model,
                input=prompt,
                response_format=response_format,
                generation_config={"temperature": 0.0},
                store=False,
            ))
            raw_text = _extract_output_text(interaction)
            sel = FollowSelection.model_validate_json(raw_text)
        except Exception as e:
            if _is_retryable(e):
                raise  # rate limit 은 위로 — 호출측(expand-loop)이 재시도
            try:
                import json

                if interaction is not None:
                    raw_text = _extract_output_text(interaction)
                    sel = FollowSelection(**json.loads(raw_text))
                else:
                    return []
            except Exception:  # noqa: BLE001
                return []
        n = len(candidates)
        return [i for i in sel.follow if isinstance(i, int) and 0 <= i < n]

    def judge_same_entity(self, mc: MergeCandidate) -> bool:
        """두 엔티티가 동일한 실세계 대상인지 LLM 으로 판정(borderline 후보에만)."""
        prompt = judge_same_entity_prompt(mc)
        try:
            interaction = self._call(lambda: self.client.interactions.create(
                model=self.model,
                input=prompt,
                store=False,
            ))
            text = _extract_output_text(interaction)
            return text.strip().upper().startswith("SAME")
        except Exception:  # noqa: BLE001
            return False  # 판정 실패 시 보수적으로 분리(거짓 병합 방지)


def _images_block(images: list[dict]) -> str:
    """render_detail 프롬프트에 끼울 '후보 이미지' 블록 — LLM 큐레이션 지시.

    fetcher 가 휴리스틱으로 1차 거른 본문 이미지 후보를 번호·alt·캡션과 함께 제시하고,
    이해에 실제로 도움 되는 것만(다이어그램·차트·스크린샷·도식) 적절한 위치에 마크다운
    이미지로 넣게 한다. 장식/로고/아이콘/중복은 빼라고 명시(최종 선별=LLM)."""
    if not images:
        return ""
    listing = "\n".join(
        f"[{i}] url: {('/image?p=' + im['local']) if im.get('local') else im.get('url', '')}\n"
        f"    alt: {im.get('alt', '') or '(없음)'}"
        + (f"\n    caption: {im['caption']}" if im.get("caption") else "")
        for i, im in enumerate(images)
    )
    return (
        "\n[원문에서 수집한 이미지 후보]\n"
        "원칙: **내용 이해에 꼭 필요한 그림만** 넣는다. 글로 설명하기 어려운 정보를 그림이 "
        "직접 전달하고, 그 그림이 없으면 이해가 떨어지는 경우에만 삽입하라 — 즉 구조도·"
        "아키텍처 다이어그램, 데이터 차트/그래프, 알고리즘·플로우 도식, 핵심을 보여주는 "
        "스크린샷 같은 **설명적 그림**. 관련 내용 바로 옆에 마크다운 `![설명](url)` 으로 넣되 "
        "url 은 목록 값을 한 글자도 바꾸지 말고 그대로, alt 설명은 한국어(문어체/명사구)로 달아라.\n"
        "**그리고 이미지 바로 다음 줄(빈 줄 없이)에 그 그림이 무엇을 보여주는지 본문 맥락에 "
        "근거한 한 줄 캡션을 이탤릭(`*...*`)으로 달아라(문어체 서술)** — alt 는 그림이 깨질 때만 보이므로 "
        "실제로 읽히는 설명은 이 캡션이다. 원문 캡션이 있으면 그것을 다듬어 쓰고, 없으면 본문 "
        "맥락으로 설명하되 원문에 없는 사실은 지어내지 마라.\n"
        "다음은 절대 넣지 마라: 대표/히어로/썸네일/소셜카드 이미지, 장식·분위기 사진, "
        "인물·프로필 사진, 로고·아이콘, 본문 이해와 무관하거나 그저 '예쁜' 이미지. **애매하면 "
        "넣지 마라.** 필요한 설명적 그림이 하나도 없으면 한 장도 넣지 않는다(그게 정상이다).\n"
        f"{listing}\n"
    )


def _coerce(text: str | None) -> ExtractionResult:
    import json

    if not text:
        return ExtractionResult()
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(json)?", "", s).strip().rstrip("`").strip()
    result = None
    try:
        result = ExtractionResult.model_validate(json.loads(s))
    except Exception:  # noqa: BLE001
        m = re.search(r"\{.*\}", s, re.DOTALL)
        if m:
            try:
                result = ExtractionResult.model_validate(json.loads(m.group(0)))
            except Exception:  # noqa: BLE001
                pass
    if result is None:
        result = ExtractionResult(summary=clean_plain_summary(s[:300]))

    if result.summary:
        result.summary = clean_plain_summary(result.summary)

    if not result.summary or not result.summary.strip():
        if result.key_claims:
            result.summary = clean_plain_summary(" ".join(result.key_claims[:3]))
        elif result.entities:
            result.summary = f"{', '.join(e.name for e in result.entities[:5])} 등에 관한 자료이다."
        else:
            result.summary = clean_plain_summary(s[:300])
    return result
