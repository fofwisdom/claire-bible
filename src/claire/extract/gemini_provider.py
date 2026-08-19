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
from .provider import (
    ExtractionResult, FollowSelection, MergeCandidate, ResearchJudgement,
    WatchClassification, emit_progress,
)

# 추출 프롬프트 버전. _SYS 를 바꾸면 올린다(재적재 시 어떤 프롬프트로 뽑았는지 추적).
# v4: summary/observations/key_claims 및 주요 서술 출력에 문어체(서술체: ~한다/~이다/~함) 적용.
PROMPT_VERSION = "extract-v4"

# 프로세스 전역 throttle: 모든 Gemini 호출이 공유하는 최소 간격과 마지막 호출 시각.
_CALL_LOCK = threading.Lock()
_LAST_CALL = [0.0]
_RETRYABLE = (429, 500, 503)

_SYS = """You extract a knowledge-graph fragment from a single source document for a
personal knowledge base about AI/software tools and research.

{ontology}

Rules:
- LANGUAGE & STYLE: write `summary`, every `observations` item, and `key_claims` in Korean
  (한국어) using formal/declarative written style (문어체 / 서술체: e.g. '~한다', '~이다',
  '~함' without conversational honorifics like '~합니다', '~해요'), REGARDLESS of the source
  document's language. Keep proper nouns, product/tool/model names, org names, and technical
  terms in their original form — do NOT transliterate (e.g. "OpenSkill", "arXiv", "LLM agent"
  stay as-is). Entity `name` and `aliases` stay in their canonical original form (usually the
  original language).
- summary: 1-3 factual sentences in Korean written style (문어체: ~한다/~이다).
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


def _extract_output_text(interaction) -> str:  # noqa: ANN001
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


def _extract_sources(interaction) -> list[dict]:  # noqa: ANN001
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
            if wait > 1.0:
                emit_progress(f"RPM 보호 대기 {wait:.0f}s (호출 간격 조절)")
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
        sys = _SYS.format(ontology=block)
        body = _doc_to_prompt(doc)

        response_format = {
            "type": "text",
            "mime_type": "application/json",
            "schema": ExtractionResult.model_json_schema(),
        }
        try:
            interaction = self._call(lambda: self.client.interactions.create(
                model=self.model,
                input=body,
                system_instruction=sys,
                response_format=response_format,
                generation_config={"temperature": 0.2},
                store=False,
            ))
            raw_text = _extract_output_text(interaction)
            result = _coerce(raw_text)
            result.raw_response = raw_text
            result.model = self.model
            result.prompt_version = PROMPT_VERSION
            return result
        except Exception as e:  # noqa: BLE001
            # rate limit/서버오류는 재시도 후에도 실패 → 올려서 raw_inbox 에 error 로
            # 남기고 나중에 replay-failed 로 재적재. 그 외(schema 거부 등)는 폴백.
            if _is_retryable(e):
                raise
            return self._extract_json_fallback(sys, body)

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
        interaction = self._call(lambda: self.client.interactions.create(
            model=self.model,
            input=prompt,
            store=False,
        ))
        raw_text = _extract_output_text(interaction)
        result = _coerce(raw_text)
        result.raw_response = raw_text
        result.model = self.model
        result.prompt_version = PROMPT_VERSION
        return result

    def embed(self, text: str) -> list[float]:
        resp = self._call(lambda: self.client.models.embed_content(
            model=self.embed_model, contents=text[:8000] or " "))
        return list(resp.embeddings[0].values)

    def summarize_search(self, query: str, context: str) -> str:
        """검색된 컨텍스트만 사용해 질의에 답한다(인용 포함, 환각 억제, 문어체)."""
        prompt = (
            "You answer the user's query using ONLY the knowledge-base context below. "
            "Do not invent facts beyond it. Cite entities in [brackets]. "
            "If the context is insufficient, say so plainly. "
            "Write the answer in Korean (한국어) using objective written style (문어체: ~한다/~이다, "
            "do not use conversational honorifics like ~합니다/~해요), but keep proper nouns, "
            "product/tool names, and technical terms in their original form (do not transliterate). "
            "Be concise.\n\n"
            f"QUERY: {query}\n\nCONTEXT:\n{context}\n\nANSWER:"
        )
        interaction = self._call(lambda: self.client.interactions.create(
            model=self.model,
            input=prompt,
            store=False,
        ))
        return _extract_output_text(interaction).strip()

    def render_detail(self, doc: Document) -> str:
        """원문을 한국어 **마크다운**으로 '편하게 읽을 수 있는 글'로 재구성(요약 아님).

        짧은 summary 와 별개 — 원문을 직접 안 읽어도 핵심·맥락·세부를 파악할 수 있게
        여러 단락(대략 A4 1~2장)으로 푼다. 구조화 추출과 독립된 별도 호출이라 그래프에
        영향 없음. UI 가 마크다운을 렌더링하므로:
          · 소제목(##/###)·문단·필요시 불릿으로 구조화하고,
          · **중요한 용어/핵심 주장은 굵게**, ==정말 핵심인 한두 구절은 형광== 으로 강조(남발 금지),
          · 원문에서 수집한 이해에 도움 되는 이미지(다이어그램·차트·스크린샷)는 적절한
            위치에 마크다운 이미지로 삽입하고 바로 아래 본문 맥락 기반 한 줄 캡션(이탤릭)을
            단다(장식/로고/아이콘은 제외 — 큐레이션).
        고유명사/기술 용어는 원문 형태 유지(음차 금지), 없는 사실 금지.

        [1홉 병합 전용, ONEHOP_MERGE_DESIGN.md §3.3b] doc.meta.extra_sources 가 있으면
        (여러 출처가 합쳐진 문서) 목표 분량을 "A4 2~4장"으로 올리고 각 출처를 빠짐없이
        통합 서술하라고 지시한다. 결과가 너무 짧으면(두 출처를 담기엔 부족) 목표를 2배씩
        최대 2회 재시도(1x→2x→4x) — 정합성 문제가 아니라 품질 보정이라 fail-open(마지막
        결과를 그대로 채택)."""
        body = _doc_to_prompt(doc)
        images = (doc.meta or {}).get("images") or []
        merged = bool((doc.meta or {}).get("extra_sources"))
        text = self._render_detail_call(body, images, merged=merged, scale=1)
        if merged:
            for scale in (2, 4):
                if len(text) >= _MERGED_DETAIL_MIN_CHARS:
                    break
                text = self._render_detail_call(body, images, merged=merged, scale=scale)
        return text

    def _render_detail_call(self, body: str, images: list, *, merged: bool, scale: int) -> str:
        if merged:
            length_hint = f"대략 A4 {2 * scale}~{4 * scale}장 분량"
            merge_hint = ("이 문서는 여러 출처가 병합됐다 — 한 출처만 요약하고 끝내지 말고 "
                         "각 출처의 핵심을 빠짐없이 통합해 서술하라.\n")
        else:
            length_hint = "대략 A4 1~2장 분량"
            merge_hint = ""
        prompt = (
            "아래 원문을 한국어 **마크다운**으로 '편하게 읽을 수 있는 글'로 재구성하라. "
            "단순 1~2문장 요약이 아니라, 독자가 원문을 직접 읽지 않아도 핵심 내용·배경 "
            f"맥락·중요한 세부까지 충분히 파악할 수 있도록 여러 단락({length_hint})으로 "
            "풀어 써라.\n\n" + merge_hint +
            "작성 규칙(마크다운):\n"
            "1. 문체 및 어조: 일관된 문어체(서술체: '~한다', '~이다', '~됨')로 서술하라. "
            "대화형 경어체('~합니다', '~해요')나 구어체는 사용하지 않는다.\n"
            "2. 내용이 길면 `##`/`###` 소제목과 문단으로 구조화하고, 나열은 `-` 불릿을 써라. "
            "단락은 빈 줄로 구분.\n"
            "3. 가독성을 위해 **중요한 용어·핵심 주장은 굵게**(`**...**`) 표시하라. 그리고 "
            "정말 빼놓으면 안 되는 한두 구절만 `==형광==`(==로 감쌈)으로 강조하라 — 남발하면 "
            "강조 효과가 사라지니 문단·섹션당 한두 곳으로 아껴 써라.\n"
            "4. 고유명사·제품/도구/모델명·조직명·기술 용어는 원문 형태 그대로 유지하라"
            "(음차/번역 금지: 예 \"arXiv\", \"LLM agent\").\n"
            "5. 원문에 없는 사실은 절대 지어내지 말 것.\n"
            + _images_block(images) +
            f"\n원문:\n{body}\n\n한국어 마크다운:"
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
        prompt = (
            "아래 문서가 '주기적으로 내용이 갱신되어 다시 봐야 가치 있는 콘텐츠'인지 판단하라.\n"
            "- watch=true: 리더보드·벤치마크 순위표·랭킹·실시간 통계·가격/시세·지속 갱신 표 등 "
            "시간이 지나면 내용이 바뀌어 재확인 가치가 있는 것.\n"
            "- watch=false: 뉴스 기사·블로그 글·논문·릴리스 노트·일회성 설명/문서 등 한 번 "
            "적재하면 거의 안 바뀌는 것.\n"
            "watch=true 면 적절한 재확인 주기를 interval_days(정수 일; 매일=1, 매주=7 등)로. "
            "reason 은 한국어 한 문장(문어체: ~임/~함/~다).\n\n"
            f"문서:\n{body}"
        )
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
        prompt = (
            "당신은 개인 지식그래프를 확장하는 리서처다. 사용자가 아래 [맥락]의 자료를 "
            "읽다가 [조사 대상]에 대해 더 알고 싶어한다.\n\n"
            "규칙:\n"
            "1. [조사 대상]은 반드시 [맥락] 안에서의 의미로만 해석하라. 동명의 다른 "
            "대상(다의어)을 다루게 되면 잘못된 지식이 그래프를 오염시킨다. 먼저 맥락 내 "
            "해석을 한 문장으로 명시하고 시작하라.\n"
            "2. 웹 검색으로 사실을 확인하며 조사하라. 맥락과 일치하는 신뢰할 만한 자료를 "
            "찾지 못하면, 지어내지 말고 첫 줄에 INSUFFICIENT 라고만 적고 이유를 한 줄 "
            "덧붙여라.\n"
            "3. 보고서는 한국어 평문 산문(일관된 문어체: ~한다/~이다, 여러 단락, 빈 줄 구분)으로 "
            "작성하라 — 마크다운 소제목(#)·불릿(-)·표 금지. 대화형 경어체(~합니다) 금지. "
            "고유명사·제품/도구/모델명·기술 용어는 원문 형태 유지(음차 금지).\n"
            "4. 핵심 정의 → 맥락과의 관계 → 구체적 사실(수치·날짜·버전 등) 순으로, "
            "지식그래프에 추출할 가치가 있는 내용 위주로 써라.\n\n"
            f"[맥락]\n{context[:8000]}\n\n[조사 대상]\n{query}\n\n[보고서]"
        )
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
        prompt = (
            "지식그래프 추가 게이트 심사. 사용자가 [맥락]을 읽다가 [조사 대상]을 조사해 "
            "[보고서]를 얻었다. 다음을 채점하라.\n"
            "- relevance(0.0~1.0): 보고서가 [맥락] 안에서의 [조사 대상] 의미를 다루는가? 동명의 "
            "다른 대상(다의어)을 다뤘다면 0 에 가깝게. 맥락과 무관한 일반론이면 낮게.\n"
            "- quality(0.0~1.0): 사실이 구체적(수치·날짜·정확한 명칭)이고 신뢰할 만한가? 빈약하거나 "
            "추측성이면 낮게.\n"
            "- same_subject(true/false): 오직 다음 패턴에서만 true — [맥락]은 [조사 대상]을 "
            "소개·인용·언급하는 **2차 서술**(그 회사·프로젝트 본인이 아닌 **제3자**(다른 "
            "매체·커뮤니티·개인)가 쓴 리뷰, 소개 글, 보도 등)이고 [보고서]는 바로 그 대상의 "
            "**1차 원본 자체**(예: 그 프로젝트의 공식 저장소, 공식 문서 원문)다. 즉 [보고서]가 "
            "[맥락]이 말하는 '그것' 자체를 가리킬 때만 true.\n"
            "  [맥락]을 쓴 주체가 [조사 대상]인 회사·프로젝트 **본인**이라면(자사 블로그, "
            "자사 사이트 등에 실린 글) — 설령 소개·사례 형식이라도 [맥락] 자체가 이미 1차 "
            "자료이므로 same_subject 는 원칙적으로 false 다. 같은 회사·제품을 다루는 다른 "
            "공식 자료(일반 문서, 다른 사례, 홈페이지, 저장소 등)는 [맥락]의 원본이 아니라 "
            "**형제 문서**일 뿐이니 병합 대상이 아니다.\n"
            "  나머지(다른 프로젝트, 다른 사건, 같은 회사의 다른 화제, 제3자의 파생 논의 "
            "등)도 모두 false.\n"
            "  예1) true: '[맥락]=GeekNews 가 X 프로젝트를 소개하는 기사(2차 서술)' + "
            "'[보고서]=X 프로젝트의 공식 github 저장소(1차 원본)'\n"
            "  예2) false: '[맥락]=회사 자체 블로그의 특정 고객 사례 글(이미 1차 자료)' + "
            "'[보고서]=같은 회사의 제품 문서 / 홈페이지 / 저장소'(맥락의 원본이 아니라 "
            "형제 문서)\n"
            "- interpretation: 보고서가 [조사 대상]을 어떤 의미로 해석했는지 한 문장(한국어 문어체: ~임/~함/~다).\n"
            "- reason: 채점 근거 한두 문장(한국어 문어체: ~임/~함/~다).\n\n"
            f"[맥락]\n{context[:6000]}\n\n[조사 대상]\n{query}\n\n[보고서]\n{report[:8000]}"
        )
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
        except Exception as e:  # noqa: BLE001
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
        """1홉 자동확장 — 부모 문서 맥락에서 따라갈(파고들) 가치가 있는 링크를 선별.

        '파고들지 여부'를 LLM 이 결정(사용자 요구). 후보를 번호 매겨 제시하고, 지식그래프에
        더할 가치가 있는 것만 인덱스로 돌려받는다. 가치 없으면 빈 목록(과잉수집 억제).
        판정 실패/파싱 실패는 빈 목록(fail-closed) — 불확실하면 파지 않는다."""
        if not candidates:
            return []
        listing = "\n".join(
            f"[{i}] {c.get('anchor') or '(텍스트 없음)'} — {c.get('url', '')}"
            for i, c in enumerate(candidates)
        )
        prompt = (
            "당신은 개인 지식그래프를 키우는 큐레이터다. 사용자가 [부모 문서]를 읽고 "
            "지식으로 적재했다. 그 문서에서 발견된 [외부 링크 후보] 중, 같은 주제를 더 "
            "깊이 알기 위해 **따라가서 함께 적재할 가치가 있는 것**만 골라라.\n\n"
            "규칙:\n"
            "1. 부모 문서의 주제와 직접 관련되고, 그 자체로 실질 내용(논문/문서/글)이 "
            "있을 법한 링크만 고른다.\n"
            "2. 광고·로그인·약관·소셜·플랫폼 홈·태그 목록 등 비콘텐츠, 그리고 주제와 "
            "동떨어진 링크는 제외한다.\n"
            "3. 애매하면 넣지 마라(잘못 적재된 노드가 이후 검색/종합을 오도한다). 가치 "
            "있는 게 하나도 없으면 빈 목록을 반환한다.\n"
            "4. follow 에는 고른 후보의 번호(인덱스)만 담아라.\n\n"
            f"[부모 문서]\n{context[:6000]}\n\n[외부 링크 후보]\n{listing}"
        )
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
        except Exception as e:  # noqa: BLE001
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


# 단일 출처 문서의 LLM 투입 예산. 병합 문서(ONEHOP_MERGE_DESIGN.md §3.3b)는 두 출처를
# 담아야 하니 2배 — 저장(documents.raw_text)은 안 자르고 프롬프트 투입량만 늘린다.
_SINGLE_DOC_CHAR_BUDGET = 12000
_MERGED_DOC_CHAR_BUDGET = _SINGLE_DOC_CHAR_BUDGET * 2
# render_detail 재시도 하한선(ONEHOP_MERGE_DESIGN.md §3.3b) — 이보다 짧으면 두 출처를
# 담기엔 명백히 부족하다고 보고 목표 분량을 올려 재시도.
_MERGED_DETAIL_MIN_CHARS = 1000


def _doc_to_prompt(doc: Document) -> str:
    head = []
    if doc.title:
        head.append(f"TITLE: {doc.title}")
    if doc.url:
        head.append(f"URL: {doc.url}")
    head.append(f"SOURCE_TYPE: {doc.source_type}")
    limit = (_MERGED_DOC_CHAR_BUDGET if (doc.meta or {}).get("extra_sources")
             else _SINGLE_DOC_CHAR_BUDGET)
    return "\n".join(head) + "\n\nCONTENT:\n" + (doc.raw_text or "")[:limit]


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
