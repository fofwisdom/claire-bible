"""Antigravity CLI (agy) 기반 Provider 어댑터.

Gemini API 직접 호출 대신 로컬에 인증된 `agy` CLI를 비대화형(`-p`) 모드로 호출하여
지식그래프 구조화 추출, 요약, 상세 렌더링, 판정, 웹 리서치를 수행한다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import threading
from typing import Any

from ..ontology.base import Document
from ..ontology.registry import ontology_prompt_block
from .gemini_provider import (
    PROMPT_VERSION,
    _SYS,
    _coerce,
    _doc_to_prompt,
    _images_block,
    _MERGED_DETAIL_MIN_CHARS,
)
from .provider import (
    ExtractionResult,
    FollowSelection,
    MergeCandidate,
    ResearchJudgement,
    WatchClassification,
    emit_progress,
)

logger = logging.getLogger(__name__)

# URL 추출용 정규식 (마크다운 링크 및 일반 URL)
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s\)]+)\)")
_RAW_URL_RE = re.compile(r"https?://[^\s\)\"\'>]+")


class AntigravityProvider:
    """Antigravity CLI (`agy`) 기반 Provider."""

    name = "antigravity"
    EMBED_DIM = 64

    def __init__(self, settings: Any):
        from ..config import find_agy_executable

        self.settings = settings
        raw_bin = getattr(settings, "agy_bin", "agy")
        self.agy_bin = find_agy_executable(raw_bin) or raw_bin
        self.model = getattr(settings, "agy_model", "gemini-3.6-flash-high")
        self.effort = getattr(settings, "agy_effort", "medium")
        self.timeout = float(getattr(settings, "agy_timeout", 120.0))
        self.max_concurrency = int(getattr(settings, "agy_max_concurrency", 2))
        self._sem = threading.Semaphore(max(1, self.max_concurrency))

    def _run_cli(
        self,
        prompt: str,
        *,
        json_schema: dict | None = None,
        output_format: str = "json",
        dangerously_skip_permissions: bool = False,
    ) -> Any:
        """agy CLI를 서브프로세스로 실행하고 결과를 반환한다."""
        cmd = [
            self.agy_bin,
            "-p",
            prompt,
            "--output-format",
            output_format,
            "--disable-slash-commands",
            "--log-file",
            "/tmp/agy.log",
        ]
        if self.model:
            cmd.extend(["--model", self.model])
        if self.effort:
            cmd.extend(["--effort", self.effort])
        if dangerously_skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        if json_schema and output_format == "json":
            cmd.extend(["--json-schema", json.dumps(json_schema)])

        with self._sem:
            emit_progress(f"Antigravity CLI 호출 ({self.model})")
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as e:
                logger.error("agy CLI invocation timed out after %ss", self.timeout)
                raise RuntimeError(f"agy CLI timed out after {self.timeout}s") from e
            except Exception as e:
                logger.error("agy CLI execution error: %s", e)
                raise

        if proc.returncode != 0:
            err_msg = (proc.stderr or proc.stdout or "").strip()
            logger.error("agy CLI returned non-zero code %d: %s", proc.returncode, err_msg)
            raise RuntimeError(f"agy CLI failed (code {proc.returncode}): {err_msg[:300]}")

        stdout = proc.stdout.strip()

        if output_format == "text":
            return stdout

        # JSON 출력 파싱
        try:
            payload = json.loads(stdout)
        except Exception:
            # 출력 래퍼 파싱 실패 시 텍스트 내 JSON 블록 추출 시도
            m = re.search(r"\{.*\}", stdout, re.DOTALL)
            if m:
                try:
                    payload = json.loads(m.group(0))
                except Exception as e:
                    raise RuntimeError(f"Failed to parse agy JSON output: {stdout[:200]}") from e
            else:
                raise RuntimeError(f"Invalid JSON from agy: {stdout[:200]}")

        if isinstance(payload, dict):
            status = payload.get("status")
            if status and status != "SUCCESS":
                raise RuntimeError(f"agy CLI returned status={status}: {payload}")
            if "structured_output" in payload and payload["structured_output"] is not None:
                return payload["structured_output"]
            if "response" in payload and isinstance(payload["response"], str):
                resp_str = payload["response"].strip()
                if json_schema:
                    try:
                        return json.loads(resp_str)
                    except Exception:
                        m = re.search(r"\{.*\}", resp_str, re.DOTALL)
                        if m:
                            return json.loads(m.group(0))
                return resp_str

        return payload

    def extract(self, doc: Document, ontology_block: str | None = None) -> ExtractionResult:
        """지식그래프 구조화 추출 (JSON Schema 강제)."""
        block = ontology_block or ontology_prompt_block()
        sys = _SYS.format(ontology=block)
        body = _doc_to_prompt(doc)
        prompt = f"{sys}\n\nDOCUMENT:\n{body}"

        schema = ExtractionResult.model_json_schema()
        try:
            data = self._run_cli(prompt, json_schema=schema, output_format="json")
            if isinstance(data, dict):
                result = ExtractionResult.model_validate(data)
            else:
                result = _coerce(str(data))
        except Exception as e:
            logger.warning("agy structured extraction fallback: %s", e)
            # 폴백: 일반 텍스트 모드로 JSON 요청
            fallback_prompt = (
                f"{prompt}\n\nReturn ONLY valid JSON matching this schema:\n"
                f"{json.dumps(schema)}"
            )
            raw_text = self._run_cli(fallback_prompt, output_format="text")
            result = _coerce(str(raw_text))

        result.model = self.model
        result.prompt_version = PROMPT_VERSION
        if not result.raw_response:
            result.raw_response = result.model_dump_json(
                exclude={"raw_response", "model", "prompt_version"}
            )
        return result

    def embed(self, text: str) -> list[float]:
        """임베딩 생성 (Gemini API 키 존재 시 Gemini embed, 아니면 결정론적 해시 벡터)."""
        if getattr(self.settings, "gemini_api_key", None):
            try:
                from google import genai

                client = genai.Client(api_key=self.settings.gemini_api_key)
                embed_model = getattr(self.settings, "gemini_embed_model", "gemini-embedding-001")
                resp = client.models.embed_content(model=embed_model, contents=text[:8000] or " ")
                return list(resp.embeddings[0].values)
            except Exception as e:
                logger.warning("Gemini embedding fallback to deterministic hash: %s", e)

        # 해시 기반 결정론적 의사 임베딩 (차원 EMBED_DIM)
        h = hashlib.sha256(text.encode("utf-8", "ignore")).digest()
        vals = []
        for i in range(self.EMBED_DIM):
            b = h[i % len(h)]
            vals.append((b / 127.5) - 1.0)
        return vals

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
        res = self._run_cli(prompt, output_format="text")
        return str(res).strip()

    def render_detail(self, doc: Document) -> str:
        """원문을 한국어 마크다운으로 '편하게 읽을 수 있는 글'로 재구성."""
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
            merge_hint = (
                "이 문서는 여러 출처가 병합됐다 — 한 출처만 요약하고 끝내지 말고 "
                "각 출처의 핵심을 빠짐없이 통합해 서술하라.\n"
            )
        else:
            length_hint = "대략 A4 1~2장 분량"
            merge_hint = ""
        prompt = (
            "아래 원문을 한국어 **마크다운**으로 '편하게 읽을 수 있는 글'로 재구성하라. "
            "단순 1~2문장 요약이 아니라, 독자가 원문을 직접 읽지 않아도 핵심 내용·배경 "
            f"맥락·중요한 세부까지 충분히 파악할 수 있도록 여러 단락({length_hint})으로 "
            "풀어 써라.\n\n"
            + merge_hint
            + "작성 규칙(마크다운):\n"
            "1. 내용이 길면 `##`/`###` 소제목과 문단으로 구조화하고, 나열은 `-` 불릿을 써라. "
            "단락은 빈 줄로 구분.\n"
            "2. 가독성을 위해 **중요한 용어·핵심 주장은 굵게**(`**...**`) 표시하라. 그리고 "
            "정말 빼놓으면 안 되는 한두 구절만 `==형광==`(==로 감쌈)으로 강조하라 — 남발하면 "
            "강조 효과가 사라지니 문단·섹션당 한두 곳으로 아껴 써라.\n"
            "3. 고유명사·제품/도구/모델명·조직명·기술 용어는 원문 형태 그대로 유지하라"
            '(음차/번역 금지: 예 "arXiv", "LLM agent").\n'
            "4. 원문에 없는 사실은 절대 지어내지 말 것.\n"
            + _images_block(images)
            + f"\n원문:\n{body}\n\n한국어 마크다운:"
        )
        res = self._run_cli(prompt, output_format="text")
        return str(res).strip()

    def classify_watch(self, doc: Document) -> dict:
        """[주기 크롤링] 문서가 '주기적으로 내용이 바뀌는 콘텐츠'인지 판단."""
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
        schema = WatchClassification.model_json_schema()
        try:
            data = self._run_cli(prompt, json_schema=schema, output_format="json")
            if isinstance(data, dict):
                return WatchClassification.model_validate(data).model_dump()
            return WatchClassification.model_validate_json(str(data)).model_dump()
        except Exception as e:
            logger.warning("classify_watch parsing failed: %s", e)
            return {"watch": False, "interval_days": None, "reason": "판정 파싱 실패"}

    def research(self, query: str, context: str) -> dict:
        """맥락 고정 웹 조사(agy 에이전트 검색 도구 활용) -> 한국어 보고서 + 출처."""
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
            "4. 핵심 정의 -> 맥락과의 관계 -> 구체적 사실(수치·날짜·버전 등) 순으로, "
            "지식그래프에 추출할 가치가 있는 내용 위주로 써라.\n\n"
            f"[맥락]\n{context[:8000]}\n\n[조사 대상]\n{query}\n\n[보고서]"
        )
        try:
            res = self._run_cli(
                prompt,
                output_format="text",
                dangerously_skip_permissions=True,
            )
            report = str(res).strip()
        except Exception as e:
            logger.error("agy research failed: %s", e)
            return {"report": "INSUFFICIENT (조사 호출 실패)", "sources": []}

        # 보고서 본문에서 출처 추출
        sources: list[dict] = []
        seen_urls: set[str] = set()
        for title, url in _MD_LINK_RE.findall(report):
            if url not in seen_urls:
                seen_urls.add(url)
                sources.append({"title": title.strip(), "url": url.strip()})
        for url in _RAW_URL_RE.findall(report):
            if url not in seen_urls:
                seen_urls.add(url)
                sources.append({"title": "", "url": url.strip()})

        return {"report": report, "sources": sources}

    def judge_research(self, query: str, context: str, report: str) -> dict:
        """조사 보고서 품질 및 맥락 일치도 판정."""
        prompt = (
            "지식그래프 추가 게이트 심사. 사용자가 [맥락]을 읽다가 [조사 대상]을 조사해 "
            "[보고서]를 얻었다. 다음을 채점하라.\n"
            "- relevance(0.0~1.0): 보고서가 [맥락] 안에서의 [조사 대상] 의미를 다루는가? 동명의 "
            "다른 대상(다의어)을 다뤘다면 0 에 가깝게. 맥락과 무관한 일반론이면 낮게.\n"
            "- quality(0.0~1.0): 사실이 구체적(수치·날짜·정확한 명칭)이고 신뢰할 만한가? 빈약하거나 "
            "추측성이면 낮게.\n"
            "- same_subject(true/false): 오직 다음 패턴에서만 true — [맥락]은 [조사 대상]을 "
            "소개·인용·언급하는 2차 서술이고 [보고서]는 바로 그 대상의 1차 원본 자체(공식 저장소/문서)일 때만 true.\n"
            "- interpretation: 보고서가 [조사 대상]을 어떤 의미로 해석했는지 한 문장(한국어).\n"
            "- reason: 채점 근거 한두 문장(한국어).\n\n"
            f"[맥락]\n{context[:6000]}\n\n[조사 대상]\n{query}\n\n[보고서]\n{report[:8000]}"
        )
        schema = ResearchJudgement.model_json_schema()
        try:
            data = self._run_cli(prompt, json_schema=schema, output_format="json")
            if isinstance(data, dict):
                return ResearchJudgement.model_validate(data).model_dump()
            return ResearchJudgement.model_validate_json(str(data)).model_dump()
        except Exception as e:
            logger.warning("judge_research parsing failed: %s", e)
            return {
                "relevance": 0.0,
                "quality": 0.0,
                "same_subject": False,
                "interpretation": "",
                "reason": f"판정 실패: {e}",
            }

    def select_followups(self, context: str, candidates: list[dict]) -> list[int]:
        """1홉 자동확장 — 부모 문서 맥락에서 따라갈 가치가 있는 링크 선별."""
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
            "3. 애매하면 넣지 마라. 가치 있는 게 하나도 없으면 빈 목록을 반환한다.\n"
            "4. follow 에는 고른 후보의 번호(인덱스)만 담아라.\n\n"
            f"[부모 문서]\n{context[:6000]}\n\n[외부 링크 후보]\n{listing}"
        )
        schema = FollowSelection.model_json_schema()
        try:
            data = self._run_cli(prompt, json_schema=schema, output_format="json")
            if isinstance(data, dict):
                sel = FollowSelection.model_validate(data)
            else:
                sel = FollowSelection.model_validate_json(str(data))
        except Exception as e:
            logger.warning("select_followups parsing failed: %s", e)
            return []

        n = len(candidates)
        return [i for i in sel.follow if isinstance(i, int) and 0 <= i < n]

    def judge_same_entity(self, mc: MergeCandidate) -> bool:
        """두 엔티티가 동일한 실세계 대상인지 LLM 판정."""
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
            res = self._run_cli(prompt, output_format="text")
            return str(res).strip().upper().startswith("SAME")
        except Exception as e:
            logger.warning("judge_same_entity call failed: %s", e)
            return False
