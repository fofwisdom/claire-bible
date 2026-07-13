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
from .provider import (
    ExtractionResult, FollowSelection, MergeCandidate, ResearchJudgement,
    WatchClassification, emit_progress,
)

# 추출 프롬프트 버전. _SYS 를 바꾸면 올린다(재적재 시 어떤 프롬프트로 뽑았는지 추적).
# v3: summary/observations/key_claims 를 한국어로(고유명사 원문 유지) — 사용자 요구.
PROMPT_VERSION = "extract-v3"

# 프로세스 전역 throttle: 모든 Gemini 호출이 공유하는 최소 간격과 마지막 호출 시각.
_CALL_LOCK = threading.Lock()
_LAST_CALL = [0.0]
_RETRYABLE = (429, 500, 503)

_SYS = """You extract a knowledge-graph fragment from a single source document for a
personal knowledge base about AI/software tools and research.

{ontology}

Rules:
- LANGUAGE: write `summary`, every `observations` item, and `key_claims` in Korean
  (한국어), REGARDLESS of the source document's language. Keep proper nouns, product/
  tool/model names, org names, and technical terms in their original form — do NOT
  transliterate (e.g. "OpenSkill", "arXiv", "LLM agent" stay as-is). Entity `name` and
  `aliases` stay in their canonical original form (usually the original language).
- summary: 1-3 factual sentences in Korean.
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
        고유명사/기술 용어는 원문 형태 유지(음차 금지), 없는 사실 금지."""
        body = _doc_to_prompt(doc)
        images = (doc.meta or {}).get("images") or []
        prompt = (
            "아래 원문을 한국어 **마크다운**으로 '편하게 읽을 수 있는 글'로 재구성하라. "
            "단순 1~2문장 요약이 아니라, 독자가 원문을 직접 읽지 않아도 핵심 내용·배경 "
            "맥락·중요한 세부까지 충분히 파악할 수 있도록 여러 단락(대략 A4 1~2장 분량)으로 "
            "풀어 써라.\n\n"
            "작성 규칙(마크다운):\n"
            "1. 내용이 길면 `##`/`###` 소제목과 문단으로 구조화하고, 나열은 `-` 불릿을 써라. "
            "단락은 빈 줄로 구분.\n"
            "2. 가독성을 위해 **중요한 용어·핵심 주장은 굵게**(`**...**`) 표시하라. 그리고 "
            "정말 빼놓으면 안 되는 한두 구절만 `==형광==`(==로 감쌈)으로 강조하라 — 남발하면 "
            "강조 효과가 사라지니 문단·섹션당 한두 곳으로 아껴 써라.\n"
            "3. 고유명사·제품/도구/모델명·조직명·기술 용어는 원문 형태 그대로 유지하라"
            "(음차/번역 금지: 예 \"arXiv\", \"LLM agent\").\n"
            "4. 원문에 없는 사실은 절대 지어내지 말 것.\n"
            + _images_block(images) +
            f"\n원문:\n{body}\n\n한국어 마크다운:"
        )
        resp = self._call(lambda: self.client.models.generate_content(
            model=self.model, contents=prompt))
        return (resp.text or "").strip()

    def classify_watch(self, doc: Document) -> dict:
        """[주기 크롤링] 문서가 '주기적으로 내용이 바뀌는 콘텐츠'인지 판단(별도 경량 호출).

        리더보드/벤치마크 순위표/실시간 통계/가격/랭킹 = watch(주기 재크롤 가치).
        뉴스/블로그/논문/일회성 설명 = 1회성. rate limit 등 실패는 위로 raise(호출측이
        비필수로 조용히 무시 — watch 미판단으로 남고 적재는 정상)."""
        from google.genai import types as gtypes

        body = _doc_to_prompt(doc)[:4000]
        prompt = (
            "아래 문서가 '주기적으로 내용이 갱신되어 다시 봐야 가치 있는 콘텐츠'인지 판단하라.\n"
            "- watch=true: 리더보드·벤치마크 순위표·랭킹·실시간 통계·가격/시세·지속 갱신 표 등 "
            "시간이 지나면 내용이 바뀌어 재확인 가치가 있는 것.\n"
            "- watch=false: 뉴스 기사·블로그 글·논문·릴리스 노트·일회성 설명/문서 등 한 번 "
            "적재하면 거의 안 바뀌는 것.\n"
            "watch=true 면 적절한 재확인 주기를 interval_days(정수 일; 매일=1, 매주=7 등)로. "
            "reason 은 한국어 한 문장.\n\n"
            f"문서:\n{body}"
        )
        cfg = gtypes.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=WatchClassification,
            temperature=0.0,
        )
        resp = self._call(lambda: self.client.models.generate_content(
            model=self.model, contents=prompt, config=cfg))
        parsed = getattr(resp, "parsed", None)
        if isinstance(parsed, WatchClassification):
            return parsed.model_dump()
        try:
            import json

            return WatchClassification(**json.loads(resp.text or "")).model_dump()
        except Exception:  # noqa: BLE001
            return {"watch": False, "interval_days": None, "reason": "판정 파싱 실패"}

    def research(self, query: str, context: str) -> dict:
        """맥락 고정 웹 조사(google_search grounding) → 한국어 보고서 + 출처.

        다의어 위험(사용자 요구): 키워드를 일반 의미가 아니라 **주어진 맥락 안에서의
        의미로만** 해석하도록 강제하고, 맥락과 맞는 자료를 못 찾으면 지어내는 대신
        INSUFFICIENT 를 선언하게 한다. 판정(judge_research)은 별도 호출로 이중 방어."""
        from google.genai import types as gtypes

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
            "3. 보고서는 한국어 평문 산문(여러 단락, 빈 줄 구분)으로 작성하라 — 마크다운 "
            "소제목(#)·불릿(-)·표 금지. 고유명사·제품/도구/모델명·기술 용어는 원문 형태 "
            "유지(음차 금지).\n"
            "4. 핵심 정의 → 맥락과의 관계 → 구체적 사실(수치·날짜·버전 등) 순으로, "
            "지식그래프에 추출할 가치가 있는 내용 위주로 써라.\n\n"
            f"[맥락]\n{context[:8000]}\n\n[조사 대상]\n{query}\n\n[보고서]"
        )
        cfg = gtypes.GenerateContentConfig(
            tools=[gtypes.Tool(google_search=gtypes.GoogleSearch())],
            temperature=0.3,
        )
        resp = self._call(lambda: self.client.models.generate_content(
            model=self.model, contents=prompt, config=cfg))
        report = (resp.text or "").strip()
        sources: list[dict] = []
        try:  # grounding 출처(있으면) — SDK 구조 변화에 방어적으로 접근
            gm = resp.candidates[0].grounding_metadata
            for ch in (getattr(gm, "grounding_chunks", None) or []):
                web = getattr(ch, "web", None)
                if web and getattr(web, "uri", None):
                    sources.append({"title": getattr(web, "title", "") or "",
                                    "url": web.uri})
        except Exception:  # noqa: BLE001
            pass
        return {"report": report, "sources": sources}

    def judge_research(self, query: str, context: str, report: str) -> dict:
        """조사 보고서가 '맥락 내 의미'와 일치하고 품질이 충분한지 별도 판정.

        research 호출과 분리된 fresh 호출(자기 채점 편향 완화). 판정 실패 시 0점
        (fail-closed) — 불확실하면 그래프에 추가하지 않는다."""
        from google.genai import types as gtypes

        prompt = (
            "지식그래프 추가 게이트 심사. 사용자가 [맥락]을 읽다가 [조사 대상]을 조사해 "
            "[보고서]를 얻었다. 다음을 0.0~1.0 으로 채점하라.\n"
            "- relevance: 보고서가 [맥락] 안에서의 [조사 대상] 의미를 다루는가? 동명의 "
            "다른 대상(다의어)을 다뤘다면 0 에 가깝게. 맥락과 무관한 일반론이면 낮게.\n"
            "- quality: 사실이 구체적(수치·날짜·정확한 명칭)이고 신뢰할 만한가? 빈약하거나 "
            "추측성이면 낮게.\n"
            "- interpretation: 보고서가 [조사 대상]을 어떤 의미로 해석했는지 한 문장(한국어).\n"
            "- reason: 채점 근거 한두 문장(한국어).\n\n"
            f"[맥락]\n{context[:6000]}\n\n[조사 대상]\n{query}\n\n[보고서]\n{report[:8000]}"
        )
        cfg = gtypes.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ResearchJudgement,
            temperature=0.0,
        )
        try:
            resp = self._call(lambda: self.client.models.generate_content(
                model=self.model, contents=prompt, config=cfg))
        except Exception as e:  # noqa: BLE001
            if _is_retryable(e):
                raise  # rate limit 은 위로 — 호출측이 오류로 안내(자동복구 루프와 정합)
            return {"relevance": 0.0, "quality": 0.0, "interpretation": "",
                    "reason": f"판정 실패: {e}"}
        parsed = getattr(resp, "parsed", None)
        if isinstance(parsed, ResearchJudgement):
            return parsed.model_dump()
        try:
            import json

            return ResearchJudgement(**json.loads(resp.text or "")).model_dump()
        except Exception:  # noqa: BLE001
            return {"relevance": 0.0, "quality": 0.0, "interpretation": "",
                    "reason": "판정 응답 파싱 실패"}

    def select_followups(self, context: str, candidates: list[dict]) -> list[int]:
        """1홉 자동확장 — 부모 문서 맥락에서 따라갈(파고들) 가치가 있는 링크를 선별.

        '파고들지 여부'를 LLM 이 결정(사용자 요구). 후보를 번호 매겨 제시하고, 지식그래프에
        더할 가치가 있는 것만 인덱스로 돌려받는다. 가치 없으면 빈 목록(과잉수집 억제).
        판정 실패/파싱 실패는 빈 목록(fail-closed) — 불확실하면 파지 않는다."""
        from google.genai import types as gtypes

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
        cfg = gtypes.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=FollowSelection,
            temperature=0.0,
        )
        try:
            resp = self._call(lambda: self.client.models.generate_content(
                model=self.model, contents=prompt, config=cfg))
        except Exception as e:  # noqa: BLE001
            if _is_retryable(e):
                raise  # rate limit 은 위로 — 호출측(expand-loop)이 재시도
            return []
        parsed = getattr(resp, "parsed", None)
        sel = parsed if isinstance(parsed, FollowSelection) else None
        if sel is None:
            try:
                import json

                sel = FollowSelection(**json.loads(resp.text or ""))
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
            resp = self._call(lambda: self.client.models.generate_content(
                model=self.model, contents=prompt))
            return (resp.text or "").strip().upper().startswith("SAME")
        except Exception:  # noqa: BLE001
            return False  # 판정 실패 시 보수적으로 분리(거짓 병합 방지)


def _images_block(images: list[dict]) -> str:
    """render_detail 프롬프트에 끼울 '후보 이미지' 블록 — LLM 큐레이션 지시.

    fetcher 가 휴리스틱으로 1차 거른 본문 이미지 후보를 번호·alt·캡션과 함께 제시하고,
    이해에 실제로 도움 되는 것만(다이어그램·차트·스크린샷·도식) 적절한 위치에 마크다운
    이미지로 넣게 한다. 장식/로고/아이콘/중복은 빼라고 명시(최종 선별=LLM)."""
    if not images:
        return ""
    # 로컬로 내려받은 사본이 있으면(사용자 요구 — 원본 사이트/링크 유실 대비) 그 서빙
    # 경로를, 없으면(다운로드 실패 등) 원본 url 로 폴백 — LLM 은 이 값을 그대로 베껴 쓴다.
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
        "url 은 목록 값을 한 글자도 바꾸지 말고 그대로, alt 설명은 한국어로 달아라.\n"
        "**그리고 이미지 바로 다음 줄(빈 줄 없이)에 그 그림이 무엇을 보여주는지 본문 맥락에 "
        "근거한 한 줄 캡션을 이탤릭(`*...*`)으로 달아라** — alt 는 그림이 깨질 때만 보이므로 "
        "실제로 읽히는 설명은 이 캡션이다. 원문 캡션이 있으면 그것을 다듬어 쓰고, 없으면 본문 "
        "맥락으로 설명하되 원문에 없는 사실은 지어내지 마라.\n"
        "다음은 절대 넣지 마라: 대표/히어로/썸네일/소셜카드 이미지, 장식·분위기 사진, "
        "인물·프로필 사진, 로고·아이콘, 본문 이해와 무관하거나 그저 '예쁜' 이미지. **애매하면 "
        "넣지 마라.** 필요한 설명적 그림이 하나도 없으면 한 장도 넣지 않는다(그게 정상이다).\n"
        f"{listing}\n"
    )


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
