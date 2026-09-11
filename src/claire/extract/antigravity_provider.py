"""Antigravity CLI (agy) 기반 Provider 어댑터.

Gemini API 직접 호출 대신 로컬에 인증된 `agy` CLI를 비대화형(`-p`) 모드로 호출하여
지식그래프 구조화 추출, 요약, 상세 렌더링, 판정, 웹 리서치를 수행한다.
"""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from ..ontology.base import Document
from ..ontology.registry import ontology_prompt_block
from ..store.telemetry import (
    diagnose_google_block,
    evaluate_summary_verdict,
    record_telemetry,
)
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

    def _get_log_file(self) -> str:
        data_dir = getattr(self.settings, "data_dir", None)
        if data_dir:
            log_dir = Path(data_dir) / "logs"
            try:
                log_dir.mkdir(parents=True, exist_ok=True)
                return str(log_dir / "agy.log")
            except Exception:
                pass
        return "/tmp/agy.log"

    def _run_cli(
        self,
        prompt: str,
        *,
        json_schema: dict | None = None,
        output_format: str = "json",
        dangerously_skip_permissions: bool = True,
        effort: str | None = None,
        call_type: str = "cli",
        document_id: str | None = None,
    ) -> Any:
        """agy CLI를 서브프로세스로 실행하고 결과를 반환한다."""
        def _build_cmd(*, use_stdin: bool) -> list[str]:
            cmd = [self.agy_bin]
            if not use_stdin:
                cmd.extend(["-p", prompt])
            cmd.extend([
                "--output-format",
                output_format,
                "--disable-slash-commands",
                "--log-file",
                self._get_log_file(),
            ])
            if self.model:
                cmd.extend(["--model", self.model])
            # 모델명에 이미 -high, -medium, -low 접미사가 포함된 경우 --effort 전달 시 agy CLI 충돌 방지
            model_has_effort = any(
                str(self.model).endswith(f"-{suf}") for suf in ("high", "medium", "low")
            )
            eff = effort or self.effort
            if eff and not model_has_effort:
                cmd.extend(["--effort", eff])
            if dangerously_skip_permissions:
                cmd.append("--dangerously-skip-permissions")
            if json_schema and output_format == "json":
                cmd.extend(["--json-schema", json.dumps(json_schema)])
            return cmd

        start_t = time.monotonic()
        prompt_bytes = len(prompt.encode("utf-8", errors="replace"))
        input_chars = len(prompt)
        use_stdin = prompt_bytes > 40_000
        delivery_mode = "stdin" if use_stdin else "argv"
        cmd = _build_cmd(use_stdin=use_stdin)
        stdin_data = prompt if use_stdin else None
        proc = None
        exit_code = None
        stdout = ""
        stderr = ""

        with self._sem:
            emit_progress(f"Antigravity CLI 호출 ({self.model})")
            try:
                proc = subprocess.run(
                    cmd,
                    input=stdin_data,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    check=False,
                )
                exit_code = proc.returncode
                stdout = proc.stdout.strip()
                stderr = (proc.stderr or "").strip()
            except OSError as e:
                # 예상치 못한 E2BIG(Argument list too long) 발생 시 stdin 방식으로 즉시 재시도
                if getattr(e, "errno", None) == errno.E2BIG and not use_stdin:
                    logger.warning("agy CLI argument list too long (E2BIG), retrying via stdin")
                    use_stdin = True
                    delivery_mode = "stdin"
                    cmd = _build_cmd(use_stdin=True)
                    proc = subprocess.run(
                        cmd,
                        input=prompt,
                        capture_output=True,
                        text=True,
                        timeout=self.timeout,
                        check=False,
                    )
                    exit_code = proc.returncode
                    stdout = proc.stdout.strip()
                    stderr = (proc.stderr or "").strip()
                else:
                    duration_ms = int((time.monotonic() - start_t) * 1000)
                    record_telemetry(
                        getattr(self.settings, "data_dir", None),
                        document_id=document_id,
                        provider=self.name,
                        model=self.model,
                        call_type=call_type,
                        input_chars=input_chars,
                        input_bytes=prompt_bytes,
                        delivery_mode=delivery_mode,
                        exit_code=None,
                        duration_ms=duration_ms,
                        status="CLI_ERROR",
                        google_block_reason="ENV_MISSING" if getattr(e, "errno", None) == errno.ENOENT else "UNKNOWN",
                        error_message=str(e),
                    )
                    logger.error("agy CLI execution error: %s", e)
                    raise
            except subprocess.TimeoutExpired as e:
                duration_ms = int((time.monotonic() - start_t) * 1000)
                record_telemetry(
                    getattr(self.settings, "data_dir", None),
                    document_id=document_id,
                    provider=self.name,
                    model=self.model,
                    call_type=call_type,
                    input_chars=input_chars,
                    input_bytes=prompt_bytes,
                    delivery_mode=delivery_mode,
                    exit_code=None,
                    duration_ms=duration_ms,
                    status="TIMEOUT",
                    google_block_reason="TIMEOUT",
                    error_message=f"agy CLI timed out after {self.timeout}s",
                )
                logger.error("agy CLI invocation timed out after %ss", self.timeout)
                raise RuntimeError(f"agy CLI timed out after {self.timeout}s") from e
            except Exception as e:
                duration_ms = int((time.monotonic() - start_t) * 1000)
                record_telemetry(
                    getattr(self.settings, "data_dir", None),
                    document_id=document_id,
                    provider=self.name,
                    model=self.model,
                    call_type=call_type,
                    input_chars=input_chars,
                    input_bytes=prompt_bytes,
                    delivery_mode=delivery_mode,
                    exit_code=None,
                    duration_ms=duration_ms,
                    status="CLI_ERROR",
                    google_block_reason="UNKNOWN",
                    error_message=str(e),
                )
                logger.error("agy CLI execution error: %s", e)
                raise

        duration_ms = int((time.monotonic() - start_t) * 1000)
        google_block = diagnose_google_block(exit_code, stderr, stdout)

        if exit_code != 0:
            err_msg = (stderr or stdout).strip()
            record_telemetry(
                getattr(self.settings, "data_dir", None),
                document_id=document_id,
                provider=self.name,
                model=self.model,
                call_type=call_type,
                input_chars=input_chars,
                input_bytes=prompt_bytes,
                delivery_mode=delivery_mode,
                exit_code=exit_code,
                duration_ms=duration_ms,
                status="CLI_ERROR",
                google_block_reason=google_block,
                error_message=err_msg[:1000],
                output_snippet=stdout[:500] if stdout else None,
            )
            logger.error("agy CLI returned non-zero code %d: %s", exit_code, err_msg)
            raise RuntimeError(f"agy CLI failed (code {exit_code}): {err_msg[:300]}")

        if output_format == "text":
            record_telemetry(
                getattr(self.settings, "data_dir", None),
                document_id=document_id,
                provider=self.name,
                model=self.model,
                call_type=call_type,
                input_chars=input_chars,
                input_bytes=prompt_bytes,
                delivery_mode=delivery_mode,
                exit_code=0,
                duration_ms=duration_ms,
                status="SUCCESS",
                google_block_reason=google_block,
                output_snippet=stdout[:500] if stdout else None,
            )
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
                    record_telemetry(
                        getattr(self.settings, "data_dir", None),
                        document_id=document_id,
                        provider=self.name,
                        model=self.model,
                        call_type=call_type,
                        input_chars=input_chars,
                        input_bytes=prompt_bytes,
                        delivery_mode=delivery_mode,
                        exit_code=0,
                        duration_ms=duration_ms,
                        status="PARSE_ERROR",
                        google_block_reason=google_block,
                        error_message=f"Failed to parse agy JSON output: {stdout[:200]}",
                    )
                    raise RuntimeError(f"Failed to parse agy JSON output: {stdout[:200]}") from e
            else:
                record_telemetry(
                    getattr(self.settings, "data_dir", None),
                    document_id=document_id,
                    provider=self.name,
                    model=self.model,
                    call_type=call_type,
                    input_chars=input_chars,
                    input_bytes=prompt_bytes,
                    delivery_mode=delivery_mode,
                    exit_code=0,
                    duration_ms=duration_ms,
                    status="PARSE_ERROR",
                    google_block_reason=google_block,
                    error_message=f"Invalid JSON from agy: {stdout[:200]}",
                )
                raise RuntimeError(f"Invalid JSON from agy: {stdout[:200]}")

        if isinstance(payload, dict):
            # 1) structured_output이 존재하면 내부 도구 에러/경고로 인한 status=ERROR와 무관하게 우선 채택
            if "structured_output" in payload and payload["structured_output"] is not None:
                record_telemetry(
                    getattr(self.settings, "data_dir", None),
                    document_id=document_id,
                    provider=self.name,
                    model=self.model,
                    call_type=call_type,
                    input_chars=input_chars,
                    input_bytes=prompt_bytes,
                    delivery_mode=delivery_mode,
                    exit_code=0,
                    duration_ms=duration_ms,
                    status="SUCCESS",
                    google_block_reason="NONE",
                    output_snippet=stdout[:500] if stdout else None,
                )
                return payload["structured_output"]

            # 2) response 문자열이 있으면 JSON 파싱 시도
            if "response" in payload and isinstance(payload["response"], str):
                resp_str = payload["response"].strip()
                if json_schema:
                    try:
                        parsed_resp = json.loads(resp_str)
                        record_telemetry(
                            getattr(self.settings, "data_dir", None),
                            document_id=document_id,
                            provider=self.name,
                            model=self.model,
                            call_type=call_type,
                            input_chars=input_chars,
                            input_bytes=prompt_bytes,
                            delivery_mode=delivery_mode,
                            exit_code=0,
                            duration_ms=duration_ms,
                            status="SUCCESS",
                            google_block_reason="NONE",
                            output_snippet=stdout[:500] if stdout else None,
                        )
                        return parsed_resp
                    except Exception:
                        m = re.search(r"\{.*\}", resp_str, re.DOTALL)
                        if m:
                            try:
                                parsed_m = json.loads(m.group(0))
                                record_telemetry(
                                    getattr(self.settings, "data_dir", None),
                                    document_id=document_id,
                                    provider=self.name,
                                    model=self.model,
                                    call_type=call_type,
                                    input_chars=input_chars,
                                    input_bytes=prompt_bytes,
                                    delivery_mode=delivery_mode,
                                    exit_code=0,
                                    duration_ms=duration_ms,
                                    status="SUCCESS",
                                    google_block_reason="NONE",
                                    output_snippet=stdout[:500] if stdout else None,
                                )
                                return parsed_m
                            except Exception:
                                pass
                elif not payload.get("status") or payload.get("status") == "SUCCESS":
                    record_telemetry(
                        getattr(self.settings, "data_dir", None),
                        document_id=document_id,
                        provider=self.name,
                        model=self.model,
                        call_type=call_type,
                        input_chars=input_chars,
                        input_bytes=prompt_bytes,
                        delivery_mode=delivery_mode,
                        exit_code=0,
                        duration_ms=duration_ms,
                        status="SUCCESS",
                        google_block_reason="NONE",
                        output_snippet=stdout[:500] if stdout else None,
                    )
                    return resp_str

            # 3) 구조화된 결과나 유효 응답이 없고 status가 에러인 경우 예외 발생
            status_val = payload.get("status")
            if status_val and status_val != "SUCCESS":
                google_block = diagnose_google_block(0, str(payload), str(payload), status=status_val)
                record_telemetry(
                    getattr(self.settings, "data_dir", None),
                    document_id=document_id,
                    provider=self.name,
                    model=self.model,
                    call_type=call_type,
                    input_chars=input_chars,
                    input_bytes=prompt_bytes,
                    delivery_mode=delivery_mode,
                    exit_code=0,
                    duration_ms=duration_ms,
                    status="BLOCKED" if google_block != "NONE" else "CLI_ERROR",
                    google_block_reason=google_block,
                    error_message=f"agy CLI returned status={status_val}: {str(payload)[:300]}",
                )
                raise RuntimeError(f"agy CLI returned status={status_val}: {payload}")

        record_telemetry(
            getattr(self.settings, "data_dir", None),
            document_id=document_id,
            provider=self.name,
            model=self.model,
            call_type=call_type,
            input_chars=input_chars,
            input_bytes=prompt_bytes,
            delivery_mode=delivery_mode,
            exit_code=0,
            duration_ms=duration_ms,
            status="SUCCESS",
            google_block_reason="NONE",
            output_snippet=stdout[:500] if stdout else None,
        )
        return payload

    def extract(
        self,
        doc: Document,
        ontology_block: str | None = None,
        *,
        effort: str | None = None,
    ) -> ExtractionResult:
        """지식그래프 구조화 추출 (JSON Schema 강제)."""
        extract_start_t = time.monotonic()
        block = ontology_block or ontology_prompt_block()
        sys = extract_system_prompt(block)
        body = _doc_to_prompt(doc)
        prompt = f"{sys}\n\nDOCUMENT:\n{body}"

        schema = ExtractionResult.extraction_json_schema()
        fallback_used = False
        try:
            data = self._run_cli(
                prompt,
                json_schema=schema,
                output_format="json",
                effort=effort,
                call_type="extract_json",
                document_id=getattr(doc, "id", None),
            )
            if isinstance(data, dict):
                result = ExtractionResult.model_validate(data)
            else:
                result = _coerce(str(data))
        except Exception as e:
            fallback_used = True
            logger.warning("agy structured extraction fallback: %s", e)
            # 폴백: 일반 텍스트 모드로 JSON 요청
            fallback_prompt = (
                f"{prompt}\n\nReturn ONLY valid JSON matching this schema:\n"
                f"{json.dumps(schema)}"
            )
            raw_text = self._run_cli(
                fallback_prompt,
                output_format="text",
                effort=effort,
                call_type="extract_text_fallback",
                document_id=getattr(doc, "id", None),
            )
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

        verdict = evaluate_summary_verdict(result.summary, doc.raw_text, provider_name=self.name)
        total_duration = int((time.monotonic() - extract_start_t) * 1000)
        record_telemetry(
            getattr(self.settings, "data_dir", None),
            document_id=getattr(doc, "id", None),
            provider=self.name,
            model=self.model,
            call_type="extract_summary",
            input_chars=len(doc.raw_text or ""),
            duration_ms=total_duration,
            status="SUCCESS" if verdict == "REAL_LLM" else "DEGRADED",
            google_block_reason="NONE" if verdict == "REAL_LLM" else ("FALLBACK_TEXT" if fallback_used else "FALLBACK_DETECTED"),
            summary_verdict=verdict,
            output_snippet=result.summary[:500] if result.summary else None,
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
        self,
        doc: Document,
        format: str = "md",
        directive: str | None = None,
        *,
        effort: str | None = None,
    ) -> str:
        """원문을 가독 렌더(MD 또는 ADOC)로 '편하게 읽을 수 있는 글'로 재구성."""
        body = _doc_to_prompt(doc)
        images = (doc.meta or {}).get("images") or []
        merged = bool((doc.meta or {}).get("extra_sources"))
        dir_val = directive or (doc.meta or {}).get("directive")
        doc_id = getattr(doc, "id", None)
        text = self._render_detail_call(
            body, images, merged=merged, scale=1, format=format, directive=dir_val, effort=effort, document_id=doc_id
        )
        if merged:
            for scale in (2, 4):
                if len(text) >= _MERGED_DETAIL_MIN_CHARS:
                    break
                text = self._render_detail_call(
                    body, images, merged=merged, scale=scale, format=format, directive=dir_val, effort=effort, document_id=doc_id
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
        effort: str | None = None,
        document_id: str | None = None,
    ) -> str:
        prompt = render_detail_prompt(
            body, images, merged=merged, scale=scale, format=format, directive=directive
        )
        res = self._run_cli(
            prompt, output_format="text", effort=effort, call_type="render_detail", document_id=document_id
        )
        return str(res).strip()

    def classify_paper(
        self, doc: Document, *, effort: str | None = None
    ):
        """학술 논문(Research/Working Paper 등) 여부 판정 (경량 호출)."""
        from .classifier import PaperClassificationResult
        from .prompts import classify_paper_prompt

        author_hint = doc.author if doc.author else None
        prompt = classify_paper_prompt(doc.title or "", doc.raw_text or "", author_hint=author_hint)
        eff = effort or getattr(self.settings, "pdf_classifier_effort", "low")
        schema = {
            "type": "object",
            "properties": {
                "is_paper": {"type": "boolean"},
                "reason": {"type": "string"},
                "author": {"type": ["string", "null"]},
                "title": {"type": ["string", "null"]},
            },
            "required": ["is_paper", "reason"],
        }
        try:
            data = self._run_cli(
                prompt,
                json_schema=schema,
                output_format="json",
                effort=eff,
                call_type="classify_paper",
                document_id=getattr(doc, "id", None),
            )
            parsed = data if isinstance(data, dict) else json.loads(str(data))
            return PaperClassificationResult(
                bool(parsed.get("is_paper", False)),
                str(parsed.get("reason", "")),
                author=parsed.get("author"),
                title=parsed.get("title"),
            )
        except Exception as e:  # noqa: BLE001
            return PaperClassificationResult(False, f"classify_paper failed: {e}")

    def classify_watch(self, doc: Document) -> dict:
        """[주기 크롤링] 문서가 '주기적으로 내용이 바뀌는 콘텐츠'인지 판단."""
        body = _doc_to_prompt(doc)[:4000]
        prompt = classify_watch_prompt(body)
        schema = WatchClassification.model_json_schema()
        try:
            data = self._run_cli(
                prompt,
                json_schema=schema,
                output_format="json",
                call_type="classify_watch",
                document_id=getattr(doc, "id", None),
            )
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
