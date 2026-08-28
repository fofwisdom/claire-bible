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
from .gemini_provider import _coerce
from .prompts import (
    _MERGED_DETAIL_MIN_CHARS,
    PROMPT_VERSION,
    classify_watch_prompt,
    clean_plain_summary,
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
        self.model = getattr(settings, "agy_model", "gemini-3.7-flash")
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
        dangerously_skip_permissions: bool = True,
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
        # 모델명에 이미 -high, -medium, -low 접미사가 포함된 경우 --effort 전달 시 agy CLI 충돌 방지
        model_has_effort = any(
            str(self.model).endswith(f"-{suf}") for suf in ("high", "medium", "low")
        )
        if self.effort and not model_has_effort:
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
            # 1) structured_output이 존재하면 내부 도구 에러/경고로 인한 status=ERROR와 무관하게 우선 채택
            if "structured_output" in payload and payload["structured_output"] is not None:
                return payload["structured_output"]

            # 2) response 문자열이 있으면 JSON 파싱 시도
            if "response" in payload and isinstance(payload["response"], str):
                resp_str = payload["response"].strip()
                if json_schema:
                    try:
                        return json.loads(resp_str)
                    except Exception:
                        m = re.search(r"\{.*\}", resp_str, re.DOTALL)
                        if m:
                            try:
                                return json.loads(m.group(0))
                            except Exception:
                                pass
                elif not payload.get("status") or payload.get("status") == "SUCCESS":
                    return resp_str

            # 3) 구조화된 결과나 유효 응답이 없고 status가 에러인 경우 예외 발생
            status = payload.get("status")
            if status and status != "SUCCESS":
                raise RuntimeError(f"agy CLI returned status={status}: {payload}")

        return payload

    def extract(self, doc: Document, ontology_block: str | None = None) -> ExtractionResult:
        """지식그래프 구조화 추출 (JSON Schema 강제)."""
        block = ontology_block or ontology_prompt_block()
        sys = extract_system_prompt(block)
        body = _doc_to_prompt(doc)
        prompt = f"{sys}\n\nDOCUMENT:\n{body}"

        schema = ExtractionResult.extraction_json_schema()
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

        # 요약 평문 정제 및 비어있는 경우 방어적 보강
        if result.summary:
            result.summary = clean_plain_summary(result.summary)

        if not result.summary or not result.summary.strip():
            if result.key_claims:
                result.summary = clean_plain_summary(" ".join(result.key_claims[:3]))
            elif result.entities:
                result.summary = f"{', '.join(e.name for e in result.entities[:5])} 등에 관한 자료이다."
            elif doc.raw_text:
                fallback_txt = (doc.raw_text or "").strip()
                result.summary = clean_plain_summary((fallback_txt[:200] + "…") if len(fallback_txt) > 200 else fallback_txt)
            elif doc.title:
                result.summary = f"{doc.title}에 관한 자료이다."

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
                limit = getattr(self.settings, "embed_char_budget", 8000)
                resp = client.models.embed_content(model=embed_model, contents=text[:limit] or " ")
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
        """검색된 컨텍스트만 사용해 질의에 답한다(인용 포함, 환각 억제, 문어체)."""
        prompt = summarize_search_prompt(query, context)
        res = self._run_cli(prompt, output_format="text")
        return str(res).strip()

    def render_detail(
        self, doc: Document, format: str = "md", directive: str | None = None
    ) -> str:
        """원문을 한국어 가독 렌더링(MD 또는 ADOC)으로 '편하게 읽을 수 있는 글'로 재구성."""
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
        res = self._run_cli(prompt, output_format="text")
        return str(res).strip()

    def classify_watch(self, doc: Document) -> dict:
        """[주기 크롤링] 문서가 '주기적으로 내용이 바뀌는 콘텐츠'인지 판단."""
        body = _doc_to_prompt(doc)[:4000]
        prompt = classify_watch_prompt(body)
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
        prompt = research_prompt(query, context)
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
        prompt = judge_research_prompt(query, context, report)
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
        prompt = select_followups_prompt(context, candidates)
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
        prompt = judge_same_entity_prompt(mc)
        try:
            res = self._run_cli(prompt, output_format="text")
            return str(res).strip().upper().startswith("SAME")
        except Exception as e:
            logger.warning("judge_same_entity call failed: %s", e)
            return False
