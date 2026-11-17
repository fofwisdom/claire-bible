"""Antigravity CLI (agy) 기반 Provider 어댑터.

Gemini API 직접 호출 대신 로컬에 인증된 `agy` CLI를 비대화형(`-p`) 모드로 호출하여 지식그래프 구조화 추출, 요약, 상세 렌더링, 판정, 웹 리서치를 수행한다.
"""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import re
import subprocess
import tempfile
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
    is_preface_summary,
    judge_relationship_prompt,
    judge_research_prompt,
    judge_same_entity_prompt,
    render_detail_prompt,
    research_prompt,
    select_followups_prompt,
    summarize_document_prompt,
    summarize_search_prompt,
)
from .prompts import (
    doc_to_prompt as _doc_to_prompt,
)
from .provider import (
    ExtractionResult,
    FollowSelection,
    MergeCandidate,
    RelationCandidate,
    RelationJudgement,
    ResearchJudgement,
    WatchClassification,
    emit_progress,
)

logger = logging.getLogger(__name__)

# URL 추출용 정규식 (마크다운 링크 및 일반 URL)
# agy 하위 프로세스에 넘기는 환경변수 allowlist. agy는 호스트 로그인을 쓰므로 GEMINI_API_KEY·CLAIRE_* 등 비밀을 상속하지 않는다(§11.3).
_AGY_ENV_ALLOWLIST = frozenset({
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "TERM",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_CACHE_HOME",
    "XDG_STATE_HOME",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "TMPDIR",
    "TMP",
    "TEMP",
})


def _subprocess_env() -> dict[str, str]:
    """agy 실행과 호스트 로그인·TLS·프록시에 필요한 최소 환경만 자식에게 전달한다."""
    env = {name: value for name, value in os.environ.items() if name in _AGY_ENV_ALLOWLIST}
    env.setdefault("PATH", os.defpath)
    return env


_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s\)]+)\)")
_RAW_URL_RE = re.compile(r"https?://[^\s\)\"\'>]+")


class AntigravityProvider:
    """Antigravity CLI (`agy`) 기반 Provider."""

    name = "antigravity"
    EMBED_DIM = 64

    def __init__(self, settings: Any):
        from ..accounts.provider_policy import context_of, require_provider_allowed
        from ..config import find_agy_executable

        # host-cli 분류: 멤버 컨텍스트면 만들지 않는다(팩토리와 2중 방어, 설계 §10.1).
        require_provider_allowed(context_of(settings), self.name)
        self.settings = settings
        raw_bin = getattr(settings, "agy_bin", "agy")
        self.agy_bin = find_agy_executable(raw_bin) or raw_bin
        self.model = getattr(settings, "agy_model", "gemini-3.8-flash-medium")
        self.effort = getattr(settings, "agy_effort", "medium")
        self.timeout = float(getattr(settings, "agy_timeout", 180.0))
        self.detail_timeout = float(getattr(settings, "agy_detail_timeout", 300.0))
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
        allow_tools: bool = False,
        timeout: float | None = None,
    ) -> Any:
        """agy CLI를 서브프로세스로 실행하고 결과를 반환한다."""
        if not allow_tools:
            suppression_header = (
                "[SYSTEM DIRECTIVE: DIRECT INFERENCE ONLY]\n"
                "Do NOT invoke any tools, search files, read repository paths, or run shell commands.\n"
                "Generate the requested output immediately in a single response turn based solely on "
                "the provided prompt context and internal knowledge.\n\n"
            )
            prompt = suppression_header + prompt

        clean_cwd: Path | None = None
        if not allow_tools:
            clean_cwd = Path(tempfile.gettempdir()) / "claire_agy_clean"
            try:
                clean_cwd.mkdir(parents=True, exist_ok=True)
            except Exception:
                clean_cwd = None
        target_cwd = str(clean_cwd) if clean_cwd else None

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
            # 모델명에 이미 -xhigh, -high, -medium, -low 접미사가 있거나 별도 --effort를 받지 않는 모델이면 생략
            from ..accounts.provider_policy import is_agy_fixed_effort_model, supported_efforts_for

            model_has_effort = is_agy_fixed_effort_model(self.model)
            eff = str(effort or self.effort or "").strip().lower()
            if eff and not model_has_effort:
                allowed = supported_efforts_for("antigravity", self.model)
                if eff not in allowed:
                    raise ValueError(f"Unsupported effort '{eff}' for Antigravity model '{self.model}'. Supported options: {', '.join(allowed)}")
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

        effective_timeout = float(timeout) if timeout is not None else self.timeout

        with self._sem:
            emit_progress(f"Antigravity CLI 호출 ({self.model})")
            try:
                proc = subprocess.run(
                    cmd,
                    input=stdin_data,
                    capture_output=True,
                    text=True,
                    timeout=effective_timeout,
                    cwd=target_cwd,
                    env=_subprocess_env(),
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
                        timeout=effective_timeout,
                        cwd=target_cwd,
                        env=_subprocess_env(),
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
                    error_message=f"agy CLI timed out after {effective_timeout}s",
                )
                logger.error("agy CLI invocation timed out after %ss", effective_timeout)
                raise RuntimeError(f"agy CLI timed out after {effective_timeout}s") from e
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
            if "is not recognized as a known model" in err_msg:
                raise RuntimeError(f"agy CLI model '{self.model}' is not supported by installed agy CLI (update host agy or select a supported model in /admin/providers): {err_msg[:600]}")
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

        if result.summary:
            result.summary = clean_plain_summary(result.summary)

        if not result.summary or not result.summary.strip():
            result.summary = ""

        result.model = self.model
        result.prompt_version = PROMPT_VERSION
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
            google_block_reason="NONE" if verdict == "REAL_LLM" else "EMPTY_OR_SHORT_SUMMARY",
            summary_verdict=verdict,
            output_snippet=result.summary[:500] if result.summary else None,
        )
        return result

    def embed(
        self, text: str, *, task_type: str | None = None, title: str | None = None
    ) -> list[float]:
        """Generate embedding via Gemini when key is set, else raise error."""
        if not getattr(self.settings, "gemini_api_key", None):
            from ..config import ProviderUnavailableError

            raise ProviderUnavailableError(
                "Antigravity CLI has no embedding endpoint and GEMINI_API_KEY is empty"
            )

        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.settings.gemini_api_key)
        embed_model = getattr(self.settings, "gemini_embed_model", "gemini-embedding-001")
        limit = getattr(self.settings, "embed_char_budget", 8000)
        tt = task_type or "RETRIEVAL_DOCUMENT"
        embed_cfg_kwargs: dict[str, Any] = {"task_type": tt}
        if title and tt == "RETRIEVAL_DOCUMENT":
            embed_cfg_kwargs["title"] = title
        config = types.EmbedContentConfig(**embed_cfg_kwargs)
        resp = client.models.embed_content(
            model=embed_model,
            contents=text[:limit] or " ",
            config=config,
        )
        return list(resp.embeddings[0].values)

    def summarize_search(self, query: str, context: str) -> str:
        """검색된 컨텍스트만 사용해 질의에 답한다(인용 포함, 환각 억제, 문어체)."""
        prompt = summarize_search_prompt(query, context)
        res = self._run_cli(
            prompt, output_format="text", call_type="summarize_search", allow_tools=False
        )
        return str(res).strip()

    def summarize_document(self, doc: Document, *, effort: str | None = None) -> str:
        """Generate a concise plain-text Korean executive summary for a document."""
        prompt = summarize_document_prompt(doc)
        target_effort = effort or self.effort
        doc_id = getattr(doc, "id", None)
        res = self._run_cli(
            prompt,
            output_format="text",
            effort=target_effort,
            call_type="summarize_document",
            document_id=doc_id,
            allow_tools=False,
        )
        return clean_plain_summary(str(res).strip()).strip()

    def render_detail(
        self,
        doc: Document,
        format: str = "adoc",
        focus: str | None = None,
        *,
        effort: str | None = None,
    ) -> str:
        """원문을 가독 렌더(MD 또는 ADOC)로 '편하게 읽을 수 있는 글'로 재구성."""
        body = _doc_to_prompt(doc)
        images = (doc.meta or {}).get("images") or []
        merged = bool((doc.meta or {}).get("extra_sources"))
        focus_val = focus or (doc.meta or {}).get("focus")
        doc_id = getattr(doc, "id", None)
        target_effort = effort or self.effort

        text = self._render_detail_call(
            body,
            images,
            merged=merged,
            scale=1,
            format=format,
            focus=focus_val,
            effort=target_effort,
            document_id=doc_id,
            timeout=self.detail_timeout,
        )

        if merged and text:
            for scale in (2, 4):
                if len(text) >= _MERGED_DETAIL_MIN_CHARS:
                    break
                try:
                    text = self._render_detail_call(
                        body,
                        images,
                        merged=merged,
                        scale=scale,
                        format=format,
                        focus=focus_val,
                        effort=target_effort,
                        document_id=doc_id,
                        timeout=self.detail_timeout,
                    )
                except Exception:
                    break
        return text

    def _render_detail_call(
        self,
        body: str,
        images: list,
        *,
        merged: bool,
        scale: int,
        format: str = "adoc",
        focus: str | None = None,
        effort: str | None = None,
        document_id: str | None = None,
        timeout: float | None = None,
    ) -> str:
        prompt = render_detail_prompt(
            body, images, merged=merged, scale=scale, format=format, focus=focus
        )
        eff_timeout = timeout if timeout is not None else self.detail_timeout
        res = self._run_cli(
            prompt,
            output_format="text",
            effort=effort,
            call_type="render_detail",
            document_id=document_id,
            timeout=eff_timeout,
        )
        return str(res).strip()

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
    ) -> str:
        """원문을 기반으로 reveal.js 전용 AsciiDoc 슬라이드 덱과 발표자 노트를 집필."""
        from .prompts import clean_code_fence, compose_presentation_prompt_adoc

        body = (doc.raw_text or "").strip()
        if not body:
            body = _doc_to_prompt(doc)
        title = (doc.title or doc.url or "Claire Bible Presentation").strip()
        author = doc.author
        published_at = doc.published_at
        focus_val = focus or (doc.meta or {}).get("focus")
        images_val = (doc.meta or {}).get("images")
        doc_id = getattr(doc, "id", None)

        prompt = compose_presentation_prompt_adoc(
            title=title,
            detail=body,
            summary=summary,
            author=author,
            published_at=published_at,
            focus=focus_val,
            slide_budget=slide_budget,
            theme=theme,
            transition=transition,
            images=images_val,
        )

        res = self._run_cli(
            prompt, output_format="text", effort=effort, call_type="compose_presentation", document_id=doc_id, allow_tools=False
        )
        return clean_code_fence(str(res).strip())

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
                effort="low",
                call_type="classify_watch",
                document_id=getattr(doc, "id", None),
                allow_tools=False,
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
                call_type="research",
                allow_tools=True,
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
            data = self._run_cli(
                prompt,
                json_schema=schema,
                output_format="json",
                effort="medium",
                call_type="judge_research",
                allow_tools=False,
            )
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
            data = self._run_cli(
                prompt,
                json_schema=schema,
                output_format="json",
                effort="low",
                call_type="select_followups",
                allow_tools=False,
            )
            if isinstance(data, dict):
                sel = FollowSelection.model_validate(data)
            else:
                sel = FollowSelection.model_validate_json(str(data))
        except Exception as e:
            logger.warning("select_followups parsing failed: %s", e)
            return []

        n = len(candidates)
        return [i for i in sel.follow if isinstance(i, int) and 0 <= i < n]

    def judge_same_entity(
        self, mc: MergeCandidate, *, document_id: str | None = None
    ) -> bool:
        """두 엔티티가 동일한 실세계 대상인지 LLM 판정."""
        prompt = judge_same_entity_prompt(mc)
        try:
            res = self._run_cli(
                prompt,
                output_format="text",
                effort="low",
                call_type="judge_same_entity",
                document_id=document_id,
                allow_tools=False,
            )
            return str(res).strip().upper().startswith("SAME")
        except Exception as e:
            logger.warning("judge_same_entity call failed: %s", e)
            return False

    def judge_relationship(
        self, rc: RelationCandidate, *, document_id: str | None = None
    ) -> RelationJudgement:
        """두 엔티티 간의 유의미한 온톨로지 관계 성립 여부 및 방향/타입을 LLM으로 판정 (Phase 2)."""
        prompt = judge_relationship_prompt(rc)
        schema = RelationJudgement.model_json_schema()
        try:
            data = self._run_cli(
                prompt,
                json_schema=schema,
                output_format="json",
                effort="medium",
                call_type="judge_relationship",
                document_id=document_id,
                allow_tools=False,
            )
            if isinstance(data, dict):
                return RelationJudgement.model_validate(data)
            return RelationJudgement.model_validate_json(str(data))
        except Exception as e:
            logger.warning("judge_relationship parsing failed: %s", e)
            return RelationJudgement(has_relation=False, reason=f"판정 실패: {e}")
