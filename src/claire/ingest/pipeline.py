"""인제스트 파이프라인 — payload 를 받아 그래프에 적재하는 전체 흐름.

  payload → fetch(router) → dedup → insert document → provider.extract → 엔티티 해소/머지(+임베딩) → 관계 검증/적재 → IngestReport

fetch_fn 을 주입 가능하게 하여 네트워크 없이 테스트한다.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import copy
import json
import re
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..accounts.provider_policy import ProviderPolicyError, context_of, split_gemini_model_and_effort
from ..extract.provider import LineageCollector, Provider, RelationCandidate, _accepts_kwarg, emit_progress
from ..extract.rendition_templates import ADDITIONAL_RENDITION_TEMPLATES, PRIMARY_RENDITION_TEMPLATE
from ..extract.resolver import _call_provider_embed, resolve_or_create
from ..ontology.base import Document, Entity, Relation
from ..ontology.registry import (
    classify_entity_type,
    classify_relation_type,
    validate_relation,
)
from ..store import db as dbm
from ..store import document_renditions as renditions_store
from ..store.graph import GraphStore
from ..core.secret_scrub import scrub_exception, scrub_text
from ..store.vectors import VectorStore, is_kb_active_space_eos, read_embed_pin, read_kb_embedding_spaces, resolve_embed_identity
from .active import active_job, clear_active_ingest, clear_job_cancel_request, get_job_cancel_request, purge_active_matrix, set_active_ingest, update_active_ingest
from .fetchers.base import bound_fetch_settings
from .router import fetch as default_fetch


def error_text(exc: BaseException, prefix: str = "") -> str:
    """기록용 오류 문자열(설계 §11.3). 정책 예외는 ``code: 메시지 (복구: …)``, 나머지는 비밀을 지운 ``str(exc)``."""
    if isinstance(exc, ProviderPolicyError):
        text = f"{exc.code}: {scrub_exception(exc)}"
        remedy = str(getattr(exc, "remedy", "") or "").strip()
        if remedy:
            text += f" (복구: {scrub_text(remedy)})"
        return text
    return f"{prefix}{scrub_exception(exc)}"


def provider_effort(provider: Any, default: str = "medium") -> str:
    """프로바이더의 기본 effort 문자열. 자리표시자(``UnavailableProvider``)처럼 문자열이 아니면 ``default``."""
    value = getattr(provider, "effort", default)
    return value if isinstance(value, str) and value else default


def _effective_provider_effort(provider: Any, requested_effort: str | None, *, slot: str = "extract_graph") -> str:
    """Return effective effort, using any fixed effort encoded in a Gemini model name."""
    slot_spec_fn = getattr(provider, "slot_spec", None)
    spec = slot_spec_fn(slot) if callable(slot_spec_fn) else None
    if isinstance(spec, (tuple, list)) and len(spec) >= 2:
        prov_name, model_name = spec[0], spec[1]
    else:
        prov_name = getattr(provider, "name", "")
        model_name = getattr(provider, "model", "")
    if str(prov_name or "").strip().lower() == "gemini" and isinstance(model_name, str):
        _, fixed_eff = split_gemini_model_and_effort(model_name)
        if fixed_eff is not None:
            return fixed_eff
    return requested_effort or provider_effort(provider)


def _record_classify_watch_lineage(
    conn: sqlite3.Connection,
    provider: Any,
    doc: Document,
    report: IngestReport,
    *,
    duration_ms: float,
    kb_id: int,
) -> None:
    """Record classify_watch stage in doc.meta['model_lineage'] and persist doc.meta."""
    slot_spec_fn = getattr(provider, "slot_spec", None)
    spec = slot_spec_fn("classify") if callable(slot_spec_fn) else None
    if isinstance(spec, (tuple, list)) and len(spec) >= 3:
        cw_prov = str(spec[0] or "?")
        cw_model = str(spec[1] or cw_prov)
        cw_req_eff: str | None = str(spec[2] or "") or None
    else:
        cw_prov = str(getattr(provider, "name", "?") or "?")
        raw_model = getattr(provider, "model", "")
        cw_model = str(raw_model if isinstance(raw_model, str) and raw_model else cw_prov)
        cw_req_eff = None
    cw_eff = _effective_provider_effort(provider, cw_req_eff, slot="classify")
    existing = doc.meta.get("model_lineage") if isinstance(doc.meta, dict) else None
    collector = LineageCollector()
    if isinstance(existing, dict):
        collector.events = [dict(ev) for ev in (existing.get("stages") or []) if isinstance(ev, dict)]
        collector.jev = dict(existing.get("jev") or {})
    collector.record_stage(
        stage="classify_watch",
        requested_provider=cw_prov,
        requested_model=cw_model,
        requested_effort=cw_eff,
        final_provider=cw_prov,
        final_model=cw_model,
        status="ok",
        duration_ms=duration_ms,
    )
    updated_lineage = collector.summarize()
    if doc.meta is None:
        doc.meta = {}
    doc.meta["model_lineage"] = updated_lineage
    report.model_lineage = updated_lineage
    if getattr(doc, "id", None):
        dbm.update_document_meta(conn, doc.id, doc.meta, kb_id=kb_id)


def resolve_job_settings(settings: Any = None, provider: Any = None) -> Any:
    """작업 설정. 넘겨받은 값 → 작업이 묶은 값 → (작업 밖 호스트 운영자 경로) 전역 설정.

    멤버 작업은 ``IngestService``가 계정 컨텍스트 사본을 넘기거나 묶으므로 전역 설정(관리자 키)에 닿지 않는다.
    """
    if settings is not None:
        return settings
    from .fetchers.base import _FETCH_SETTINGS

    bound = _FETCH_SETTINGS.get()
    if bound is not None:
        return bound
    from ..core.config import get_settings

    return get_settings()


@dataclass
class IngestReport:
    document_id: str | None = None
    title: str | None = None
    source_type: str = ""
    duplicate: bool = False
    updated: bool = False   # 같은 canonical_url 의 내용 갱신(in-place, 신규 아님)
    partial: bool = False
    summary: str = ""
    entities_created: int = 0
    entities_linked: int = 0   # 기존 노드에 머지된 수 (= "연결" 성공)
    relations_added: int = 0
    relations_rejected: int = 0
    cross_relations_added: int = 0  # Phase 2: 전역 지식 그래프 횡단 엣지 연결 수
    cross_linked_relations: list[str] = field(default_factory=list)  # e.g. ["vLLM -> PagedAttention (uses)"]
    proposals: int = 0
    error: str | None = None
    inbox_id: int | None = None
    new_entity_names: list[str] = field(default_factory=list)
    linked_entity_names: list[str] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)  # 링크 확장 후보 URL
    focus: str | None = None
    full_content: bool = False
    effort: str | None = None
    has_transcript: bool | None = None
    is_stt: bool = False
    stt_error: str | None = None
    pdf_parser_requested: str | None = None
    pdf_parser_used: str | None = None
    pdf_parser_fallback: bool = False
    pdf_parser_fallback_reason: str | None = None
    pdf_encoding_flaw_detected: bool = False
    pdf_encoding_flaws: list[str] = field(default_factory=list)
    pdf_is_scanned: bool = False
    presentation_pdfs: int = 0
    presentation_pdf_chars: int = 0
    presentation_pdf_parsers: list[str] = field(default_factory=list)
    heatmap_matrix: dict | None = None
    has_decision_stream: bool = False
    composite_ingest: bool = False
    detail_rendered: bool = True
    detail_error: str | None = None
    summary_rendered: bool = True
    summary_error: str | None = None
    vlm_transcribed: bool = False
    vlm_images_count: int = 0
    vlm_model: str | None = None
    vlm_language: str | None = None
    stt_language: str | None = None
    caption_language: str | None = None
    model_lineage: dict[str, Any] = field(default_factory=dict)
    # Campaign notices (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 8.7): see ``claire.campaign``.
    notices: list[dict[str, Any]] = field(default_factory=list)
    staged: bool = False
    extraction_status: str = "extracted"
    content_hash: str | None = None
    raw_chars: int = 0
    message: str | None = None
    cancelled: bool = False

    def notice_lines(self, *, locale: str | None = "en") -> list[str]:
        """The texts of the campaign notices. ``locale`` None: the active locale."""
        from ..collaboration.campaign import notice_text

        return [notice_text(notice, locale=locale) for notice in self.notices]

    def telegram_summary(self) -> str:
        if self.error:
            return f"❌ 적재 실패: {self.error}"
        if self.duplicate:
            return f"♻️ 이미 있는 자료입니다 (dedup): {self.title or self.document_id}"
        if self.staged:
            head = "📥 원문 임시 저장 (Agent Client 추출 대기)"
            parts = [f"{head}: {self.title or self.document_id}"]
            if self.message:
                parts.append(self.message)
            if self.raw_chars:
                parts.append(f"문자 수: {self.raw_chars:,}")
            return "\n".join(parts)

        is_stt_failed = bool(
            self.stt_error
            or (self.source_type == "video" and self.has_transcript is False and self.stt_error)
        )
        is_detail_failed = not self.detail_rendered
        is_summary_failed = not self.summary_rendered

        if is_stt_failed and is_summary_failed and is_detail_failed:
            head = "⚠️ 부분 적재 (STT 전사, 초록 및 본문 생성 실패)"
        elif is_stt_failed and is_summary_failed:
            head = "⚠️ 부분 적재 (STT 전사 및 초록 생성 실패)"
        elif is_stt_failed and is_detail_failed:
            head = "⚠️ 부분 적재 (STT 전사 및 본문 생성 실패)"
        elif is_summary_failed and is_detail_failed:
            head = "⚠️ 부분 적재 (초록 및 본문 생성 실패)"
        elif is_stt_failed:
            head = "⚠️ 부분 적재 (STT 전사 실패)"
        elif is_summary_failed:
            head = "⚠️ 부분 적재 (초록 생성 실패)"
        elif is_detail_failed:
            head = "⚠️ 부분 적재 (본문 가독 상세 생성 실패)"
        elif self.pdf_parser_fallback:
            req_raw = (self.pdf_parser_requested or "pypdfium2").lower()
            req = "PyPDFium2" if req_raw in ("default", "pypdfium2") else (self.pdf_parser_requested or "PyPDFium2").capitalize()
            if req == "Pypdf":
                req = "PyPDF"
            used = (self.pdf_parser_used or "PyPDF").capitalize()
            if used == "Pypdfium2":
                used = "PyPDFium2"
            elif used == "Pypdf":
                used = "PyPDF"
            head = f"⚠️ PDF 파서 대체 적재 ({req} 실패 → {used})"
        elif self.updated:
            head = "🔄 자료 업데이트(내용 변경 반영)"
        else:
            head = "✅ 적재 완료"

        parts = [f"{head}: {self.title or self.document_id}"]
        if self.pdf_parser_fallback:
            req_raw = (self.pdf_parser_requested or "pypdfium2").lower()
            req = "PyPDFium2" if req_raw in ("default", "pypdfium2") else (self.pdf_parser_requested or "PyPDFium2").capitalize()
            if req == "Pypdf":
                req = "PyPDF"
            used = (self.pdf_parser_used or "PyPDF").capitalize()
            if used == "Pypdfium2":
                used = "PyPDFium2"
            elif used == "Pypdf":
                used = "PyPDF"
            reason = self.pdf_parser_fallback_reason or f"{req} 런타임 오류"
            parts.append(f"📄 {req} 파서 실패로 {used}가 대체 사용되었습니다. (사유: {reason})")
        if self.pdf_encoding_flaw_detected:
            flaws_str = ", ".join(self.pdf_encoding_flaws) if self.pdf_encoding_flaws else "텍스트 레이어 결함"
            parts.append(f"⚠️ PDF 텍스트 인코딩 결함 감지 (사유: {flaws_str}) — LLM 추출 품질에 영향을 줄 수 있습니다.")
        if self.pdf_is_scanned:
            parts.append("📷 스캔본(이미지 위주) PDF 감지 — 텍스트 레이어가 희소하여 OCR 변환이 권장됩니다.")
        if is_stt_failed:
            err_detail = f" (오류: {self.stt_error})" if self.stt_error else ""
            parts.append(f"🎙️ 오디오 STT 전사 실패: 음성 자막이 추출되지 못했습니다.{err_detail}")
        if is_summary_failed:
            parts.append("📝 초록(summary)이 생성되지 못했습니다. 봇에서 초록 재생성을 요청할 수 있습니다.")
        if is_detail_failed:
            err_detail = f" (오류: {self.detail_error})" if self.detail_error else ""
            parts.append(f"📄 본문 가독 상세(detail)가 생성되지 못했습니다 (초록 및 지식 그래프만 보존됨).{err_detail} 봇에서 재생성을 요청할 수 있습니다.")
        if self.presentation_pdfs:
            presentation_details = [f"{self.presentation_pdfs}개"]
            if self.presentation_pdf_chars:
                presentation_details.append(f"{self.presentation_pdf_chars:,}자")
            if self.presentation_pdf_parsers:
                presentation_details.append("/".join(self.presentation_pdf_parsers))
            bundle_icon = "🎙️⚡📄" if self.is_stt else "🔤⚡📄"
            bundle_label = "STT×PDF" if self.is_stt else "CC×PDF"
            parts.append(
                f"{bundle_icon} {bundle_label} 포함 ("
                + " · ".join(presentation_details)
                + ")"
            )

        meta_badges = []
        from .source_types import is_media_source_type

        if not self.presentation_pdfs and is_media_source_type(self.source_type):
            if self.has_transcript and not self.is_stt:
                cc_lang = f" [{self.caption_language}]" if self.caption_language else ""
                meta_badges.append(f"🔤 영상 자막(CC){cc_lang}")
            elif self.is_stt and not is_stt_failed:
                stt_lang = f" [{self.stt_language}]" if self.stt_language else ""
                meta_badges.append(f"🎙️ 음성 전사(STT){stt_lang}")
        if self.composite_ingest and not self.presentation_pdfs:
            meta_badges.append("📎⚡🌐 첨부+링크 복합 적재")
        if self.vlm_transcribed:
            vlm_cnt = f" ({self.vlm_images_count}장)" if self.vlm_images_count else ""
            vlm_lang = f" [{self.vlm_language}]" if self.vlm_language else ""
            meta_badges.append(f"🖼️ 이미지 전사(VLM){vlm_cnt}{vlm_lang}")
        if self.full_content:
            meta_badges.append("🌐 원문 무절단 수집")
        if self.effort:
            meta_badges.append(f"🧠 추론: {self.effort}")
        if meta_badges:
            parts.append(" · ".join(meta_badges))
        if self.focus:
            parts.append(f"초점: {self.focus}")
        if self.partial and not is_stt_failed:
            parts.append("⚠️ 부분 처리(partial)")
        if not is_summary_failed:
            parts.append(f"초록: {self.summary[:300]}")
        cross_note = f" (횡단 {self.cross_relations_added})" if self.cross_relations_added else ""
        parts.append(
            f"엔티티 신규 {self.entities_created} · 기존 {self.entities_linked} · "
            f"관계 {self.relations_added}{cross_note}"
        )
        if self.linked_entity_names:
            parts.append("연결됨: " + ", ".join(self.linked_entity_names[:8]))
        if self.cross_linked_relations:
            parts.append("🌐 지식망 연결: " + ", ".join(self.cross_linked_relations[:5]))
        if self.proposals:
            parts.append(f"새 타입 제안 {self.proposals}건 기록")
        if self.candidates:
            parts.append(f"🔗 관련 링크 {len(self.candidates)}개 발견 — 가져올까요?")
        final_models = (self.model_lineage or {}).get("final_output_models") or {}
        if final_models:
            non_mock = [f"{k}:{v.get('provider')}/{v.get('model')}" for k, v in final_models.items() if v.get("provider") and v.get("provider") != "mock" and v.get("model")]
            distinct_models = {v.get("model") for v in final_models.values() if v.get("model")}
            if non_mock and len(distinct_models) > 1:
                parts.append("🧬 모델 계보: " + " · ".join(non_mock[:5]))
        return "\n".join(parts)


STAGED_INGEST_MESSAGE = "Raw content saved; queued for Agent Client extraction"


def _fetch_settings_for_staging(settings_obj: Any) -> Any:
    """Disable server VLM and STT flags so client staging never invokes server models."""
    if settings_obj is None:
        return None
    if hasattr(settings_obj, "model_copy"):
        return settings_obj.model_copy(update={"enable_vlm_transcription": False, "enable_video_transcription": False})
    try:
        cloned = copy.copy(settings_obj)
        setattr(cloned, "enable_vlm_transcription", False)
        setattr(cloned, "enable_video_transcription", False)
        return cloned
    except Exception:
        return settings_obj


def stage_raw(
    payload: str,
    *,
    conn: sqlite3.Connection,
    fetch_fn: Callable[[str], Document] | None = None,
    data_dir: Path | None = None,
    source: str = "cli",
    user_id: int | None = None,
    chat_id: int | None = None,
    inbox_kind: str | None = None,
    file_ref: str | None = None,
    file_name: str | None = None,
    inbox_id: int | None = None,
    prefetched: Document | None = None,
    focus: str | None = None,
    full_content: bool = True,
    kb_id: int | None = None,
    theme_label: str | None = None,
    settings: Any = None,
    provider_context: Any = None,
    account_id: int | None = None,
    force: bool = False,
) -> IngestReport:
    """Fetch, normalize, and stage untruncated raw content without invoking any server LLM provider."""
    return _ingest_core(
        payload,
        conn=conn,
        provider=None,
        vstore=None,
        fetch_fn=fetch_fn,
        expand_max=0,
        data_dir=data_dir,
        source=source,
        user_id=user_id,
        chat_id=chat_id,
        inbox_kind=inbox_kind,
        file_ref=file_ref,
        file_name=file_name,
        inbox_id=inbox_id,
        prefetched=prefetched,
        auto_expand=False,
        format=None,
        focus=focus,
        effort=None,
        full_content=bool(full_content),
        kb_id=kb_id,
        theme_label=theme_label,
        settings=settings,
        provider_context=provider_context,
        account_id=account_id,
        stage_only=True,
        force=bool(force),
    )


def ingest(
    payload: str,
    *,
    conn: sqlite3.Connection,
    provider: Provider | None = None,
    vstore: VectorStore | None = None,
    fetch_fn: Callable[[str], Document] | None = None,
    expand_max: int = 0,
    data_dir: Path | None = None,
    source: str = "cli",
    user_id: int | None = None,
    chat_id: int | None = None,
    inbox_kind: str | None = None,
    file_ref: str | None = None,
    file_name: str | None = None,
    inbox_id: int | None = None,
    prefetched: Document | None = None,
    auto_expand: bool = False,
    format: str | None = None,
    focus: str | None = None,
    effort: str | None = None,
    full_content: bool = False,
    kb_id: int | None = None,
    theme_label: str | None = None,
    settings: Any = None,
    provider_context: Any = None,
    account_id: int | None = None,
    extraction_mode: str = "provider",
) -> IngestReport:
    """Ingest a payload. ``extraction_mode="client"`` stages the raw content for an agent client with zero server LLM calls (``extraction_mode.py``)."""
    from .extraction_mode import parse_extraction_mode

    stage_only = parse_extraction_mode(extraction_mode) == "client"
    tid = kb_id if kb_id is not None else getattr(conn, "kb_id", 0)
    job_settings = resolve_job_settings(settings, provider)
    ctx = provider_context if provider_context is not None else context_of(job_settings)
    effective_account_id = account_id if account_id is not None else getattr(ctx, "account_id", None)
    # AED9: client staging always preserves untruncated raw content so the external client does not lose source text.
    effective_full_content = True if stage_only else full_content
    report = _ingest_core(
        payload,
        conn=conn,
        provider=provider,
        vstore=vstore,
        fetch_fn=fetch_fn,
        expand_max=expand_max,
        data_dir=data_dir,
        source=source,
        user_id=user_id,
        chat_id=chat_id,
        inbox_kind=inbox_kind,
        file_ref=file_ref,
        file_name=file_name,
        inbox_id=inbox_id,
        prefetched=prefetched,
        auto_expand=auto_expand,
        format=format,
        focus=focus,
        effort=effort,
        full_content=effective_full_content,
        kb_id=tid,
        theme_label=theme_label,
        settings=settings,
        provider_context=provider_context,
        account_id=effective_account_id,
        stage_only=stage_only,
    )
    if stage_only and report.document_id and not report.error and getattr(report, "message", "") == STAGED_INGEST_MESSAGE:
        # A person, a feed, or the Telegram bot started this ingest, and the source now waits for an agent client: tell the account (PWA_NOTIFICATION_DESIGN.md kind ``agent_source_staged``). ``stage_raw`` (the agent stages its own source) does not come here.
        main_file = next((row[2] for row in conn.execute("PRAGMA database_list").fetchall() if row[1] == "main" and row[2]), None)
        if main_file:
            from ..notifications import events as notification_events

            notification_events.agent_source_staged(main_file, kb_id=int(tid))
    return report


def _clear_stale_additional_renditions(conn: sqlite3.Connection, document_id: str, kb_id: int) -> None:
    """Remove additional renditions when raw source text changes on an existing document."""
    for template in ADDITIONAL_RENDITION_TEMPLATES:
        renditions_store.delete_additional(conn, document_id, kb_id, template)


def _consume_cancel(data_dir: Path | None, job: Any) -> dict[str, Any] | None:
    """Read the cross-process cancel marker for the active job when present."""
    if job is None:
        return None
    job_id = getattr(job, "job_id", None)
    if not job_id:
        return None
    target_dir = data_dir if data_dir is not None else getattr(job, "data_dir", None)
    return get_job_cancel_request(target_dir, str(job_id))


def _cancel_before_document(
    conn: sqlite3.Connection,
    data_dir: Path | None,
    inbox_id: int | None,
    payload: str,
    kb_id: int,
    *,
    tombstone: bool,
    reason: str,
    doc: Document | None = None,
) -> None:
    """Delete the inbox row before any document write and optionally record a tombstone."""
    del data_dir
    if inbox_id is not None:
        conn.execute("DELETE FROM raw_inbox WHERE id=? AND kb_id=?", (inbox_id, kb_id))
        conn.commit()
    if tombstone and ((payload and payload.strip()) or doc is not None):
        clean_payload = (payload or "").strip()
        url_val = doc.url if doc is not None and doc.url else None
        canon_val = doc.canonical_url if doc is not None and doc.canonical_url else None
        content_hash_val = doc.content_hash if doc is not None and doc.content_hash else None
        if url_val is None and clean_payload.startswith(("http://", "https://")):
            from .normalize import canonicalize_url

            url_val = clean_payload
            canon_val = canon_val or canonicalize_url(clean_payload)
        if url_val or canon_val or content_hash_val:
            import secrets

            identifier = f"inbox:{inbox_id}" if inbox_id is not None else f"cancel:{secrets.token_hex(6)}"
            dbm.record_tombstone(
                conn,
                identifier,
                reason=reason,
                url=url_val,
                canonical_url=canon_val,
                content_hash=content_hash_val,
                kb_id=kb_id,
            )


def _compensate_cancelled_document(
    conn: sqlite3.Connection,
    data_dir: Path | None,
    doc_id: str,
    inbox_id: int | None,
    kb_id: int,
    *,
    tombstone: bool,
    reason: str,
) -> None:
    """Purge a newly created document and its partial graph writes without VACUUM."""
    target_dir = Path(data_dir) if data_dir is not None else Path("data")
    dbm.purge_document_cascade(
        conn,
        target_dir,
        [doc_id],
        reason=reason,
        dry_run=False,
        tombstone=tombstone,
        kb_id=kb_id,
        heal=False,
        vacuum=False,
    )
    purge_active_matrix(target_dir, doc_id)
    if inbox_id is not None:
        conn.execute("DELETE FROM raw_inbox WHERE id=? AND kb_id=?", (inbox_id, kb_id))
        conn.commit()


def _complete_server_extraction(
    *,
    conn: sqlite3.Connection,
    provider: Provider,
    vstore: VectorStore,
    doc: Document,
    report: IngestReport,
    inbox_id: int,
    tid: int,
    data_dir: Path | None,
    source: str,
    expand_max: int,
    auto_expand: bool,
    format: str | None,
    focus: str | None,
    effort: str | None,
    full_content: bool,
    job_settings: Any,
    run_watch_and_expand: bool,
    job: Any = None,
    newly_created_doc: bool = True,
) -> IngestReport:
    """Run server-side extraction, graph storage, optional watch/expand, and mark the document extracted."""
    def _abort_cancelled(cancel_req: dict[str, Any] | None, *, extraction_ran: bool) -> IngestReport:
        req = cancel_req or {}
        tombstone = bool(req.get("tombstone"))
        reason = str(req.get("reason") or "queue_running_cancel")
        if newly_created_doc:
            _compensate_cancelled_document(conn, data_dir, doc.id, inbox_id, tid, tombstone=tombstone, reason=reason)
        else:
            if extraction_ran:
                dbm.rollback_document_graph_contributions(conn, [doc.id], prune_orphans=True, kb_id=tid)
            if data_dir is not None:
                purge_active_matrix(data_dir, doc.id)
            if inbox_id is not None:
                conn.execute("DELETE FROM raw_inbox WHERE id=? AND kb_id=?", (inbox_id, tid))
                conn.commit()
        report.cancelled = True
        report.error = "cancelled_by_user"
        if job is not None:
            job.update(stage="cancelled", msg="중단 및 역연산 소각 완료")
            clear_job_cancel_request(data_dir, job.job_id)
        return report

    pre_cancel = _consume_cancel(data_dir, job)
    if pre_cancel is not None:
        return _abort_cancelled(pre_cancel, extraction_ran=False)

    if data_dir is not None:
        try:
            from ..store.raw import save_artifact

            save_artifact(data_dir, doc.id, doc.raw_text)
        except Exception:  # noqa: BLE001
            pass
    _download_doc_images(conn, doc, data_dir, kb_id=tid)

    before_extract_cancel = _consume_cancel(data_dir, job)
    if before_extract_cancel is not None:
        return _abort_cancelled(before_extract_cancel, extraction_ran=False)

    cancel_cb = (lambda: _consume_cancel(data_dir, job)) if job is not None else None
    try:
        ok, err = extract_resolve_store(conn, provider, vstore, doc, report, format=format, focus=focus, effort=effort, full_content=full_content, kb_id=tid, settings=job_settings, cancel_check=cancel_cb)
    except ProviderPolicyError as e:
        ok, err = False, error_text(e)
    if not ok and err == "__cancelled__":
        return _abort_cancelled(_consume_cancel(data_dir, job), extraction_ran=True)
    if not ok:
        report.error = err
        dbm.update_inbox(conn, inbox_id, status="error", document_id=doc.id, error=err, kb_id=tid)
        return report
    post_extract_cancel = _consume_cancel(data_dir, job)
    if post_extract_cancel is not None:
        return _abort_cancelled(post_extract_cancel, extraction_ran=True)
    dbm.mark_document_extraction_status(conn, doc.id, "extracted", kb_id=tid, clear_claim=True)
    report.extraction_status = "extracted"
    if run_watch_and_expand:
        if not doc.partial and not source.startswith(("expand:", "recover", "replay", "refresh", "research")):
            emit_progress("주기 갱신 콘텐츠 여부 판단 중…")
            cw_started = time.perf_counter()
            if ensure_watch_classification(conn, provider, doc, kb_id=tid):
                cw_ms = (time.perf_counter() - cw_started) * 1000.0
                _record_classify_watch_lineage(conn, provider, doc, report, duration_ms=cw_ms, kb_id=tid)
        if expand_max > 0 and not doc.partial:
            if auto_expand:
                dbm.enqueue_expand(conn, doc.id, kb_id=tid)
            else:
                from ..expand.proposal import find_candidates

                report.candidates = find_candidates(conn, doc, limit=expand_max, kb_id=tid)
    pre_done_cancel = _consume_cancel(data_dir, job)
    if pre_done_cancel is not None:
        return _abort_cancelled(pre_done_cancel, extraction_ran=True)
    dbm.update_inbox(conn, inbox_id, status="done", document_id=doc.id, kb_id=tid)
    return report


def _ingest_core(
    payload: str,
    *,
    conn: sqlite3.Connection,
    provider: Provider | None,
    vstore: VectorStore | None,
    fetch_fn: Callable[[str], Document] | None,
    expand_max: int,
    data_dir: Path | None,
    source: str,
    user_id: int | None,
    chat_id: int | None,
    inbox_kind: str | None,
    file_ref: str | None,
    file_name: str | None,
    inbox_id: int | None,
    prefetched: Document | None,
    auto_expand: bool,
    format: str | None,
    focus: str | None,
    effort: str | None,
    full_content: bool,
    kb_id: int | None,
    theme_label: str | None,
    settings: Any,
    provider_context: Any,
    account_id: int | None,
    stage_only: bool,
    force: bool = False,
) -> IngestReport:
    report = IngestReport()
    report.full_content = full_content
    report.effort = None if stage_only else effort
    if fetch_fn is None:
        fetch_fn = default_fetch

    tid = kb_id if kb_id is not None else getattr(conn, "kb_id", 0)
    tlabel = theme_label or ("기본 지식베이스" if tid == 0 else "")
    job_settings = resolve_job_settings(settings, provider)
    ctx = provider_context if provider_context is not None else context_of(job_settings)
    del account_id

    if inbox_id is None:
        kind = inbox_kind or _guess_kind(payload)
        inbox_id = dbm.log_inbox(
            conn, source=source, payload=payload, kind=kind,
            user_id=user_id, chat_id=chat_id, file_name=file_name, file_ref=file_ref,
            kb_id=tid, extraction_mode=("client" if stage_only else "provider"),
        )
    report.inbox_id = inbox_id

    bind_fn = getattr(provider, "bind_lineage", None)
    lineage_ctx = (
        bind_fn()
        if (not stage_only and isinstance(getattr(provider, "lineage", None), LineageCollector) and callable(bind_fn))
        else contextlib.nullcontext()
    )

    with active_job(
        data_dir,
        kind="ingest",
        payload=payload,
        kb_id=tid,
        theme_label=tlabel,
        title=prefetched.title if prefetched else None,
        source=source,
        inbox_id=inbox_id,
    ) as job, lineage_ctx:
        from ..extract.provider import _progress_local, set_progress_callback

        prev_cb = getattr(_progress_local, "cb", None)

        def _active_progress_hook(msg: Any) -> None:
            if isinstance(msg, dict):
                if msg.get("stage") == "heatmap_matrix":
                    job.update(
                        stage="heatmap_matrix",
                        heatmap_matrix=msg.get("matrix"),
                    )
                elif msg.get("msg"):
                    job.update(
                        msg=str(msg.get("msg")),
                        stage=str(msg.get("stage") or "progress"),
                    )
            elif msg:
                job.update(msg=str(msg))
            if prev_cb:
                try:
                    prev_cb(msg)
                except Exception:
                    pass

        set_progress_callback(_active_progress_hook)
        try:
            def _abort_pre_doc(cancel_req: dict[str, Any], *, cancelled_doc: Document | None = None) -> IngestReport:
                _cancel_before_document(
                    conn,
                    data_dir,
                    inbox_id,
                    payload,
                    tid,
                    tombstone=bool(cancel_req.get("tombstone")),
                    reason=str(cancel_req.get("reason") or "queue_running_cancel"),
                    doc=cancelled_doc,
                )
                report.cancelled = True
                report.error = "cancelled_by_user"
                job.update(stage="cancelled", msg="사용자 요청으로 취소됨")
                clear_job_cancel_request(data_dir, job.job_id)
                return report

            pre_fetch_cancel = _consume_cancel(data_dir, job)
            if pre_fetch_cancel is not None:
                return _abort_pre_doc(pre_fetch_cancel)

            fetch_settings_scope = _fetch_settings_for_staging(job_settings) if stage_only else settings
            try:
                if prefetched is None:
                    emit_progress("원문 가져오는 중…")
                    with bound_fetch_settings(fetch_settings_scope):
                        try:
                            doc = fetch_fn(payload, full_content=full_content)
                        except TypeError:
                            doc = fetch_fn(payload)
                else:
                    doc = prefetched
                if doc and doc.title:
                    update_active_ingest(data_dir, title=doc.title)
            except Exception as e:  # noqa: BLE001
                report.error = error_text(e)
                dbm.update_inbox(conn, inbox_id, status="error", error=report.error, kb_id=tid)
                return report

            if not doc.content_hash:
                from .normalize import content_hash as compute_content_hash

                doc.content_hash = compute_content_hash(doc.raw_text or doc.title or "")

            raw_chars_val = len(doc.raw_text or "")
            if doc.meta is None:
                doc.meta = {}
            if full_content:
                doc.meta["full_content"] = True
            if effort and not stage_only:
                doc.meta["applied_effort"] = effort

            if not stage_only and doc.source_type in ("pdf", "odt"):
                from ..extract.classifier import reconcile_pdf_metadata

                try:
                    reconcile_pdf_metadata(doc, job_settings, provider=provider)
                except ProviderPolicyError:
                    pass

            report.source_type = doc.source_type
            report.partial = doc.partial
            report.title = doc.title
            report.focus = focus
            report.content_hash = doc.content_hash
            report.raw_chars = raw_chars_val
            if doc.meta:
                if "has_transcript" in doc.meta:
                    report.has_transcript = bool(doc.meta.get("has_transcript"))
                if "is_stt" in doc.meta or "stt_applied" in doc.meta or "stt" in doc.meta:
                    report.is_stt = bool(
                        doc.meta.get("is_stt")
                        or doc.meta.get("stt_applied")
                        or doc.meta.get("stt")
                    )
                if "stt_error" in doc.meta:
                    report.stt_error = doc.meta.get("stt_error")
                if "pdf_parser_requested" in doc.meta:
                    report.pdf_parser_requested = doc.meta.get("pdf_parser_requested")
                if "pdf_parser_used" in doc.meta:
                    report.pdf_parser_used = doc.meta.get("pdf_parser_used")
                if "pdf_parser_fallback" in doc.meta:
                    report.pdf_parser_fallback = bool(doc.meta.get("pdf_parser_fallback"))
                if "pdf_parser_fallback_reason" in doc.meta:
                    report.pdf_parser_fallback_reason = doc.meta.get("pdf_parser_fallback_reason")
                if "pdf_encoding_flaw_detected" in doc.meta:
                    report.pdf_encoding_flaw_detected = bool(doc.meta.get("pdf_encoding_flaw_detected"))
                if "pdf_encoding_flaws" in doc.meta:
                    report.pdf_encoding_flaws = list(doc.meta.get("pdf_encoding_flaws") or [])
                if "pdf_is_scanned" in doc.meta:
                    report.pdf_is_scanned = bool(doc.meta.get("pdf_is_scanned"))
                presentation_items = doc.meta.get("presentation_pdfs") or []
                if isinstance(presentation_items, list):
                    report.presentation_pdfs = len(presentation_items)
                    report.presentation_pdf_chars = sum(
                        int(item.get("raw_chars") or 0)
                        for item in presentation_items
                        if isinstance(item, dict)
                    )
                    report.presentation_pdf_parsers = list(
                        dict.fromkeys(
                            str(item.get("parser_used"))
                            for item in presentation_items
                            if isinstance(item, dict) and item.get("parser_used")
                        )
                    )
                presentation_primary = doc.meta.get("presentation_pdf") or {}
                if isinstance(presentation_primary, dict):
                    if presentation_primary.get("status") == "available" and not report.presentation_pdfs:
                        report.presentation_pdfs = 1
                    if presentation_primary.get("parser_fallback"):
                        report.pdf_parser_fallback = True
                        report.pdf_parser_fallback_reason = presentation_primary.get(
                            "parser_fallback_reason"
                        )
                if doc.meta.get("composite_ingest"):
                    report.composite_ingest = True
                if "vlm_transcribed" in doc.meta:
                    report.vlm_transcribed = bool(doc.meta.get("vlm_transcribed"))
                if "vlm_images_count" in doc.meta:
                    report.vlm_images_count = int(doc.meta.get("vlm_images_count") or 0)
                if "vlm_model" in doc.meta:
                    report.vlm_model = doc.meta.get("vlm_model")
                if "vlm_language" in doc.meta:
                    report.vlm_language = doc.meta.get("vlm_language")
                if "stt_language" in doc.meta:
                    report.stt_language = doc.meta.get("stt_language")
                if "caption_language" in doc.meta:
                    report.caption_language = doc.meta.get("caption_language")
            if focus and focus.strip():
                doc.meta["focus"] = focus.strip()

            if stage_only and force:
                dbm.remove_tombstones(conn, url=doc.url, canonical_url=doc.canonical_url, content_hash=doc.content_hash, kb_id=tid)

            if dbm.is_tombstoned(conn, url=doc.url, canonical_url=doc.canonical_url, content_hash=doc.content_hash, kb_id=tid):
                report.error = "tombstoned: document was previously purged"
                dbm.update_inbox(conn, inbox_id, status="error", error=report.error, kb_id=tid)
                return report

            post_fetch_cancel = _consume_cancel(data_dir, job)
            if post_fetch_cancel is not None:
                return _abort_pre_doc(post_fetch_cancel, cancelled_doc=doc)

            if doc.source_type == "pdf" and doc.canonical_url:
                bundled_id = dbm.find_document_by_extra_source(conn, doc.canonical_url, kb_id=tid)
                if bundled_id:
                    if stage_only and force:
                        if not _store_doc_attachments_or_report(conn, inbox_id, doc, bundled_id, data_dir, report, kb_id=tid):
                            return report
                        doc.meta["renditions_stale"] = True
                        dbm.update_document_content(
                            conn, bundled_id, title=doc.title, raw_text=doc.raw_text,
                            content_hash=doc.content_hash, fetched_at=doc.fetched_at,
                            source_type=doc.source_type, partial=doc.partial, meta=doc.meta, kb_id=tid,
                            extraction_status="pending",
                            raw_chars=raw_chars_val,
                            clear_claim=True,
                        )
                        if data_dir is not None:
                            try:
                                from ..store.raw import save_artifact

                                save_artifact(data_dir, bundled_id, doc.raw_text)
                            except Exception:  # noqa: BLE001
                                pass
                        _download_doc_images(conn, doc, data_dir, kb_id=tid)
                        report.document_id = bundled_id
                        report.updated = True
                        report.duplicate = False
                        report.staged = True
                        report.extraction_status = "pending"
                        report.message = STAGED_INGEST_MESSAGE
                        dbm.update_inbox(conn, inbox_id, status="staged", document_id=bundled_id, kb_id=tid)
                        return report
                    report.document_id = bundled_id
                    report.duplicate = True
                    dbm.update_inbox(conn, inbox_id, status="duplicate", document_id=bundled_id, kb_id=tid)
                    return report

            _link_existing_presentation_documents(conn, doc, kb_id=tid)

            # Dedup 1: exact content_hash match.
            existing = dbm.find_document_by_hash(conn, doc.content_hash, kb_id=tid)
            if existing:
                existing_row = dbm.get_document_row(conn, existing, kb_id=tid)
                existing_status = str(existing_row["extraction_status"] or "extracted") if existing_row is not None and "extraction_status" in existing_row.keys() else "extracted"
                same_canonical = bool(
                    existing_row
                    and doc.canonical_url
                    and existing_row["canonical_url"] == doc.canonical_url
                )
                old_meta: dict = {}
                old_presentation_state: tuple = ()
                if same_canonical and existing_row is not None:
                    try:
                        old_meta = json.loads(existing_row["meta"] or "{}")
                    except Exception:
                        old_meta = {}
                    old_presentation_state = _presentation_state(old_meta)
                    preserve_presentation_history(old_meta, doc)
                if not _store_doc_attachments_or_report(conn, inbox_id, doc, existing, data_dir, report, kb_id=tid):
                    return report
                if stage_only and force:
                    doc.meta["renditions_stale"] = True
                    dbm.update_document_content(
                        conn, existing, title=doc.title, raw_text=doc.raw_text,
                        content_hash=doc.content_hash, fetched_at=doc.fetched_at,
                        source_type=doc.source_type, partial=doc.partial, meta=doc.meta, kb_id=tid,
                        extraction_status="pending",
                        raw_chars=raw_chars_val,
                        clear_claim=True,
                    )
                    if data_dir is not None:
                        try:
                            from ..store.raw import save_artifact

                            save_artifact(data_dir, existing, doc.raw_text)
                        except Exception:  # noqa: BLE001
                            pass
                    _download_doc_images(conn, doc, data_dir, kb_id=tid)
                    report.document_id = existing
                    report.updated = True
                    report.duplicate = False
                    report.staged = True
                    report.extraction_status = "pending"
                    report.message = STAGED_INGEST_MESSAGE
                    dbm.update_inbox(conn, inbox_id, status="staged", document_id=existing, kb_id=tid)
                    return report
                if same_canonical and _presentation_state(doc.meta or {}) != old_presentation_state:
                    dbm.update_document_meta(conn, existing, doc.meta, kb_id=tid)
                    report.document_id = existing
                    report.updated = True
                    report.duplicate = False
                    if stage_only and existing_status == "pending":
                        report.staged = True
                        report.extraction_status = "pending"
                        report.message = STAGED_INGEST_MESSAGE
                        dbm.update_inbox(conn, inbox_id, status="staged", document_id=existing, kb_id=tid)
                    else:
                        dbm.update_inbox(conn, inbox_id, status="done", document_id=existing, kb_id=tid)
                    return report
                if not stage_only and existing_status in ("pending", "failed"):
                    if provider is None or vstore is None:
                        raise RuntimeError("provider and vstore are required for server extraction")
                    doc_obj = dbm.get_document(conn, existing, kb_id=tid) or doc
                    doc_obj.meta = doc.meta or doc_obj.meta
                    report.document_id = existing
                    report.updated = True
                    report.duplicate = False
                    report.title = doc_obj.title or doc.title
                    return _complete_server_extraction(
                        conn=conn,
                        provider=provider,
                        vstore=vstore,
                        doc=doc_obj,
                        report=report,
                        inbox_id=inbox_id,
                        tid=tid,
                        data_dir=data_dir,
                        source=source,
                        expand_max=expand_max,
                        auto_expand=auto_expand,
                        format=format,
                        focus=focus,
                        effort=effort,
                        full_content=full_content,
                        job_settings=job_settings,
                        run_watch_and_expand=True,
                        job=job,
                        newly_created_doc=False,
                    )
                if not stage_only and focus and focus.strip():
                    doc_obj = dbm.get_document(conn, existing, kb_id=tid)
                    if doc_obj:
                        dbm.set_document_focus(conn, existing, focus.strip(), kb_id=tid)
                        detail_ok = ensure_document_detail(
                            conn, provider, doc_obj, format=format, focus=focus.strip(), force=True, kb_id=tid,
                            settings=job_settings,
                        )
                        if not detail_ok:
                            report.detail_rendered = False
                            report.detail_error = "초점 가독 상세(detail) 재생성 실패"
                        report.document_id = existing
                        report.updated = True
                        report.duplicate = False
                        report.title = doc_obj.title or doc.title
                        dbm.update_inbox(conn, inbox_id, status="done", document_id=existing, kb_id=tid)
                        return report

                report.document_id = existing
                report.duplicate = True
                report.extraction_status = existing_status
                if stage_only and existing_status == "pending":
                    report.staged = True
                    report.message = STAGED_INGEST_MESSAGE
                dbm.update_inbox(conn, inbox_id, status="duplicate", document_id=existing, kb_id=tid)
                return report

            # Dedup 2: same canonical_url with changed content_hash updates in-place.
            same_url = dbm.find_document_by_canonical_url(conn, doc.canonical_url, kb_id=tid)
            if same_url:
                pre_update_cancel = _consume_cancel(data_dir, job)
                if pre_update_cancel is not None:
                    return _abort_pre_doc(pre_update_cancel, cancelled_doc=doc)
                doc.id = same_url
                old_row = dbm.get_document_row(conn, same_url, kb_id=tid)
                if old_row is not None:
                    try:
                        old_meta = json.loads(old_row["meta"] or "{}")
                    except Exception:
                        old_meta = {}
                    preserve_presentation_history(old_meta, doc)
                if not _store_doc_attachments_or_report(conn, inbox_id, doc, same_url, data_dir, report, kb_id=tid):
                    return report
                if stage_only:
                    doc.meta["renditions_stale"] = True
                dbm.update_document_content(
                    conn, same_url, title=doc.title, raw_text=doc.raw_text,
                    content_hash=doc.content_hash, fetched_at=doc.fetched_at,
                    source_type=doc.source_type, partial=doc.partial, meta=doc.meta, kb_id=tid,
                    extraction_status=("pending" if stage_only else None),
                    raw_chars=raw_chars_val,
                    clear_claim=True,
                )
                if not stage_only:
                    _clear_stale_additional_renditions(conn, same_url, tid)
                    conn.commit()
                report.document_id = same_url
                report.updated = True
                if stage_only:
                    if data_dir is not None:
                        try:
                            from ..store.raw import save_artifact

                            save_artifact(data_dir, same_url, doc.raw_text)
                        except Exception:  # noqa: BLE001
                            pass
                    _download_doc_images(conn, doc, data_dir, kb_id=tid)
                    report.staged = True
                    report.extraction_status = "pending"
                    report.message = STAGED_INGEST_MESSAGE
                    dbm.update_inbox(conn, inbox_id, status="staged", document_id=same_url, kb_id=tid)
                    return report
                if provider is None or vstore is None:
                    raise RuntimeError("provider and vstore are required for server extraction")
                return _complete_server_extraction(
                    conn=conn,
                    provider=provider,
                    vstore=vstore,
                    doc=doc,
                    report=report,
                    inbox_id=inbox_id,
                    tid=tid,
                    data_dir=data_dir,
                    source=source,
                    expand_max=expand_max,
                    auto_expand=auto_expand,
                    format=format,
                    focus=focus,
                    effort=effort,
                    full_content=full_content,
                    job_settings=job_settings,
                    run_watch_and_expand=False,
                    job=job,
                    newly_created_doc=False,
                )

            # Dedup 3: near-duplicate MinHash check.
            near = dbm.near_duplicate_document(conn, doc, kb_id=tid)
            if near:
                near_id, score = near
                near_row = dbm.get_document_row(conn, near_id, kb_id=tid)
                near_status = str(near_row["extraction_status"] or "extracted") if near_row is not None and "extraction_status" in near_row.keys() else "extracted"
                if stage_only and force:
                    if near_row is not None:
                        try:
                            old_near_meta = json.loads(near_row["meta"] or "{}")
                        except Exception:
                            old_near_meta = {}
                        preserve_presentation_history(old_near_meta, doc)
                    if not _store_doc_attachments_or_report(conn, inbox_id, doc, near_id, data_dir, report, kb_id=tid):
                        return report
                    doc.meta["renditions_stale"] = True
                    dbm.update_document_content(
                        conn, near_id, title=doc.title, raw_text=doc.raw_text,
                        content_hash=doc.content_hash, fetched_at=doc.fetched_at,
                        source_type=doc.source_type, partial=doc.partial, meta=doc.meta, kb_id=tid,
                        extraction_status="pending",
                        raw_chars=raw_chars_val,
                        clear_claim=True,
                    )
                    if data_dir is not None:
                        try:
                            from ..store.raw import save_artifact

                            save_artifact(data_dir, near_id, doc.raw_text)
                        except Exception:  # noqa: BLE001
                            pass
                    _download_doc_images(conn, doc, data_dir, kb_id=tid)
                    report.document_id = near_id
                    report.updated = True
                    report.duplicate = False
                    report.staged = True
                    report.extraction_status = "pending"
                    report.message = STAGED_INGEST_MESSAGE
                    dbm.update_inbox(conn, inbox_id, status="staged", document_id=near_id, kb_id=tid)
                    return report
                report.document_id = near_id
                report.duplicate = True
                report.extraction_status = near_status
                if near_status == "pending":
                    report.staged = True
                    report.message = STAGED_INGEST_MESSAGE
                dbm.update_inbox(conn, inbox_id, status="duplicate", document_id=near_id, kb_id=tid)
                return report

            if not stage_only:
                emit_progress("구조화 추출·그래프 적재 중…")
            if ctx is not None and not getattr(ctx, "is_admin", True):
                from ..accounts import server_tier

                try:
                    server_tier.check_document_quota(conn, ctx)
                except ProviderPolicyError as e:
                    report.error = error_text(e)
                    dbm.update_inbox(conn, inbox_id, status="error", error=report.error, kb_id=tid)
                    return report
            pre_insert_cancel = _consume_cancel(data_dir, job)
            if pre_insert_cancel is not None:
                return _abort_pre_doc(pre_insert_cancel, cancelled_doc=doc)
            if not _store_doc_attachments_or_report(conn, inbox_id, doc, doc.id, data_dir, report, kb_id=tid):
                return report
            dbm.insert_document(
                conn,
                doc,
                kb_id=tid,
                extraction_status=("pending" if stage_only else "extracted"),
                raw_chars=raw_chars_val,
            )
            report.document_id = doc.id

            if stage_only:
                if data_dir is not None:
                    try:
                        from ..store.raw import save_artifact

                        save_artifact(data_dir, doc.id, doc.raw_text)
                    except Exception:  # noqa: BLE001
                        pass
                _download_doc_images(conn, doc, data_dir, kb_id=tid)
                report.staged = True
                report.extraction_status = "pending"
                report.message = STAGED_INGEST_MESSAGE
                dbm.update_inbox(conn, inbox_id, status="staged", document_id=doc.id, kb_id=tid)
                return report

            if provider is None or vstore is None:
                raise RuntimeError("provider and vstore are required for server extraction")
            return _complete_server_extraction(
                conn=conn,
                provider=provider,
                vstore=vstore,
                doc=doc,
                report=report,
                inbox_id=inbox_id,
                tid=tid,
                data_dir=data_dir,
                source=source,
                expand_max=expand_max,
                auto_expand=auto_expand,
                format=format,
                focus=focus,
                effort=effort,
                full_content=full_content,
                job_settings=job_settings,
                run_watch_and_expand=True,
                job=job,
                newly_created_doc=True,
            )

        finally:
            set_progress_callback(prev_cb)
            try:
                from .report_json import report_to_dict

                rep_dict = report_to_dict(report) if report else None
            except Exception:
                rep_dict = None
            if report and report.title:
                job.update(title=report.title, doc_id=report.document_id, result=rep_dict)
            elif rep_dict:
                job.update(doc_id=report.document_id if report else None, result=rep_dict)
            clear_active_ingest(
                data_dir,
                result=rep_dict,
                error=report.error if report else None,
            )

def _download_doc_images(conn: sqlite3.Connection, doc: Document, data_dir: Path | None, kb_id: int | None = None) -> None:
    """본문 이미지 후보를 로컬로 내려받아 보존(사용자 요구 — 외부 사이트/링크가 나중에 사라지면 문서에 남는 게 깨진 이미지 링크뿐이라 저장해 둬야 함). ingest 신규/in-place 갱신·refresh 가 공유. doc.meta['images'] 를 local 경로 포함 형태로 갱신 + DB 반영. 이미지 후보 없거나 data_dir 없으면 조용히 스킵(개별 다운로드 실패는 raw.download_images 가 원본 url 로 이미 폴백)."""
    images = (doc.meta or {}).get("images")
    if not images or data_dir is None:
        return
    from ..store.raw import download_images

    enriched = download_images(data_dir, doc.id, images)
    doc.meta["images"] = enriched
    dbm.set_document_images(conn, doc.id, enriched, kb_id=kb_id)


def _store_required_doc_attachments(doc: Document, data_dir: Path | None) -> list[str]:
    """필수 첨부를 DB 쓰기 전에 보존한다. 저장 위치가 없으면 부분 적재를 거부한다."""
    required = [attachment for attachment in doc.attachments if attachment.required]
    if not required:
        return []
    if data_dir is None:
        raise RuntimeError("data directory is required for source attachments")
    from ..store.raw import save_document_attachments

    return save_document_attachments(data_dir, doc)


def _store_doc_attachments_or_report(
    conn: sqlite3.Connection,
    inbox_id: int,
    doc: Document,
    document_id: str,
    data_dir: Path | None,
    report: IngestReport,
    kb_id: int | None = None,
) -> bool:
    doc.id = document_id
    try:
        _store_required_doc_attachments(doc, data_dir)
        return True
    except Exception as exc:  # noqa: BLE001
        report.error = f"attachment_store_failed: {type(exc).__name__}: {scrub_exception(exc)}"
        dbm.update_inbox(
            conn,
            inbox_id,
            status="error",
            document_id=document_id if dbm.get_document_row(conn, document_id, kb_id=kb_id) else None,
            error=report.error,
            kb_id=kb_id,
        )
        return False


def _presentation_items(meta: dict) -> list[dict]:
    items = meta.get("presentation_pdfs")
    if isinstance(items, list):
        return [dict(item) for item in items if isinstance(item, dict)]
    primary = meta.get("presentation_pdf")
    if isinstance(primary, dict) and primary.get("status") == "available":
        return [dict(primary)]
    return []


def _presentation_state(meta: dict) -> tuple:
    """추출 텍스트가 같아도 원본 PDF 버전·저장 상태 변경을 감지한다."""
    return tuple(
        (
            item.get("content_sha256"),
            item.get("artifact_path"),
            item.get("public_url"),
        )
        for item in _presentation_items(meta)
    )


def preserve_presentation_history(old_meta: dict, doc: Document) -> None:
    """PDF 교체 시 이전 URL·해시·아티팩트 경로를 append-only history에 보존한다."""
    if not doc.meta:
        return
    history = [
        dict(item)
        for item in (old_meta.get("presentation_history") or [])
        if isinstance(item, dict)
    ]
    new_hashes = {
        item.get("content_sha256") for item in _presentation_items(doc.meta)
    }
    history_hashes = {item.get("content_sha256") for item in history}
    for item in _presentation_items(old_meta):
        digest = item.get("content_sha256")
        if not digest or digest in new_hashes or digest in history_hashes:
            continue
        history.append(
            {
                "public_url": item.get("public_url"),
                "content_sha256": digest,
                "text_sha256": item.get("text_sha256"),
                "artifact_path": item.get("artifact_path"),
                "byte_length": item.get("byte_length"),
                "replaced_at": time.time(),
            }
        )
        history_hashes.add(digest)
    if history:
        doc.meta["presentation_history"] = history


def _link_existing_presentation_documents(
    conn: sqlite3.Connection,
    doc: Document,
    kb_id: int | None = None,
) -> None:
    """이미 독립 적재된 PDF는 삭제·흡수하지 않고 영상 메타데이터에서 참조한다."""
    if doc.source_type != "video" or not doc.meta:
        return
    linked: list[str] = []
    for item in _presentation_items(doc.meta):
        url = item.get("public_url")
        if not url:
            continue
        existing_id = dbm.find_document_by_canonical_url(conn, url, kb_id=kb_id)
        if existing_id and existing_id != doc.id and existing_id not in linked:
            linked.append(existing_id)
    if linked:
        doc.meta["presentation_document_ids"] = linked
        doc.meta["presentation_document_id"] = linked[0]


def validate_extractable_content(doc: Document) -> tuple[bool, str | None]:
    """문서가 LLM 구조화 추출 및 가독 상세 생성을 수행하기에 충분한 실질 콘텐츠를 가졌는지 사전 검증."""
    if not doc.raw_text or not doc.raw_text.strip():
        return False, "본문 내용이 비어 있습니다 (empty_content)"

    raw = doc.raw_text.strip()

    # 1. 비디오 문서: 자막/STT나 프레젠테이션 첨부 없이 플레이스홀더만 있는 경우 차단
    if doc.source_type in ("video", "youtube"):
        meta = doc.meta or {}
        has_transcript = meta.get("has_transcript")
        has_pres = bool(
            meta.get("presentation_pdf")
            or meta.get("presentation_pdfs")
            or meta.get("presentation_document_id")
        )
        if has_transcript is False and not has_pres:
            stt_err = str(meta.get("stt_error") or "").strip()
            if stt_err:
                return (
                    False,
                    f"영상 음성 전사(STT)에 실패하여 본문 추출을 중단합니다 (video_stt_failed: {stt_err})",
                )
            if doc.source_type == "video":
                # 플레이스홀더 문구 및 메타데이터 블록 제거 후 실질 본문 길이 확인
                cleaned = re.sub(r"\[영상 자막\]\s*\([^\)]+\)", "", raw)
                cleaned = re.sub(r"재생 시간:[^\n]+", "", cleaned)
                cleaned = re.sub(r"\[태그\][^\n]+", "", cleaned)
                cleaned = cleaned.strip()
                if len(cleaned) < 250:
                    return (
                        False,
                        "영상 자막/STT 또는 프레젠테이션 내용이 없어 본문 추출이 불가능합니다 (video_transcript_missing)",
                    )

    # 2. 웹 문서: 저품질/UI 쉘/차단 가드 검증
    if doc.source_type in ("web", "custom"):
        from .fetchers.guard import validate_web_content

        ok, reason = validate_web_content(doc.title, raw)
        if not ok:
            return False, f"저품질 또는 비정상 콘텐츠 차단 (content_guard: {reason})"

    return True, None


def extract_resolve_store(
    conn: sqlite3.Connection,
    provider: Provider,
    vstore: VectorStore,
    doc: Document,
    report: IngestReport,
    *,
    format: str | None = None,
    focus: str | None = None,
    effort: str | None = None,
    full_content: bool = False,
    on_progress: Callable[[str, str], None] | None = None,
    kb_id: int | None = None,
    settings: Any = None,
    before_store: Callable[[], None] | None = None,
    cancel_check: Callable[[], dict[str, Any] | None] | None = None,
) -> tuple[bool, str | None]:
    """문서 1건의 추출→엔티티 해소/머지→관계 검증/적재.

    ingest(신규 적재)와 refresh(복원 재적재)가 공유한다. doc.id 기준으로 동작하므로 refresh 시 같은 id 로 호출하면 기존 엔티티 sources 에 누적된다(연결 보존). 추출 실패 시 (False, error). 성공 시 (True, None).

    ``before_store``: called after the LLM extraction (and the separate summary) is successful, before the first write of the new result. A regeneration removes the old graph of the document here, so a failed LLM call keeps the old graph.
    """
    valid, guard_err = validate_extractable_content(doc)
    if not valid:
        return False, f"insufficient_content: {guard_err}"

    has_provider_lineage = isinstance(getattr(provider, "lineage", None), LineageCollector)
    bind_fn = getattr(provider, "bind_lineage", None)
    has_bound_fn = getattr(provider, "has_bound_lineage", None)
    need_bind = has_provider_lineage and callable(bind_fn) and not (callable(has_bound_fn) and has_bound_fn())
    if need_bind:
        with bind_fn():
            return _extract_resolve_store_impl(
                conn, provider, vstore, doc, report,
                format=format, focus=focus, effort=effort, full_content=full_content,
                on_progress=on_progress, kb_id=kb_id, settings=settings, before_store=before_store,
                cancel_check=cancel_check,
            )
    return _extract_resolve_store_impl(
        conn, provider, vstore, doc, report,
        format=format, focus=focus, effort=effort, full_content=full_content,
        on_progress=on_progress, kb_id=kb_id, settings=settings, before_store=before_store,
        cancel_check=cancel_check,
    )


def _extract_resolve_store_impl(
    conn: sqlite3.Connection,
    provider: Provider,
    vstore: VectorStore,
    doc: Document,
    report: IngestReport,
    *,
    format: str | None = None,
    focus: str | None = None,
    effort: str | None = None,
    full_content: bool = False,
    on_progress: Callable[[str, str], None] | None = None,
    kb_id: int | None = None,
    settings: Any = None,
    before_store: Callable[[], None] | None = None,
    cancel_check: Callable[[], dict[str, Any] | None] | None = None,
) -> tuple[bool, str | None]:
    tid = kb_id if kb_id is not None else getattr(conn, "kb_id", 0)
    settings = resolve_job_settings(settings, provider)
    eff = effort
    if eff is None:
        if doc.source_type in ("pdf", "odt"):
            classification = (doc.meta or {}).get("paper_classification")
            if classification and isinstance(classification, dict):
                is_paper = classification.get("is_paper", False)
                reason = classification.get("reason", "")
            else:
                from ..extract.classifier import reconcile_pdf_metadata

                try:
                    res = reconcile_pdf_metadata(doc, settings, provider=provider)
                except ProviderPolicyError as e:
                    return False, error_text(e)
                is_paper, reason = res[:2]
            if is_paper and len(doc.raw_text or "") >= settings.pdf_paper_threshold_chars:
                eff = settings.pdf_paper_effort or "high"
            else:
                eff = settings.pdf_default_effort or provider_effort(provider)
        else:
            eff = provider_effort(provider)
    eff = _effective_provider_effort(provider, eff, slot="extract_graph")

    if doc.meta is None:
        doc.meta = {}
    doc.meta["applied_effort"] = eff
    if full_content:
        doc.meta["full_content"] = True
    report.effort = eff
    if full_content:
        report.full_content = True
    if doc.meta:
        if "pdf_parser_requested" in doc.meta:
            report.pdf_parser_requested = doc.meta.get("pdf_parser_requested")
        if "pdf_parser_used" in doc.meta:
            report.pdf_parser_used = doc.meta.get("pdf_parser_used")
        if "pdf_parser_fallback" in doc.meta:
            report.pdf_parser_fallback = bool(doc.meta.get("pdf_parser_fallback"))
        if "pdf_parser_fallback_reason" in doc.meta:
            report.pdf_parser_fallback_reason = doc.meta.get("pdf_parser_fallback_reason")
        if "pdf_encoding_flaw_detected" in doc.meta:
            report.pdf_encoding_flaw_detected = bool(doc.meta.get("pdf_encoding_flaw_detected"))
        if "pdf_encoding_flaws" in doc.meta:
            report.pdf_encoding_flaws = list(doc.meta.get("pdf_encoding_flaws") or [])
        if "pdf_is_scanned" in doc.meta:
            report.pdf_is_scanned = bool(doc.meta.get("pdf_is_scanned"))
        if "vlm_transcribed" in doc.meta:
            report.vlm_transcribed = bool(doc.meta.get("vlm_transcribed"))
        if "vlm_images_count" in doc.meta:
            report.vlm_images_count = int(doc.meta.get("vlm_images_count") or 0)
        if "vlm_model" in doc.meta:
            report.vlm_model = doc.meta.get("vlm_model")
        if "vlm_language" in doc.meta:
            report.vlm_language = doc.meta.get("vlm_language")
        if "stt_language" in doc.meta:
            report.stt_language = doc.meta.get("stt_language")
        if "caption_language" in doc.meta:
            report.caption_language = doc.meta.get("caption_language")

    if getattr(doc, "id", None):
        try:
            dbm.update_document_meta(conn, doc.id, doc.meta, kb_id=tid)
        except Exception:
            pass


    provider_lineage = getattr(provider, "lineage", None)
    has_provider_lineage = isinstance(provider_lineage, LineageCollector)
    lineage = provider_lineage if has_provider_lineage else LineageCollector()
    if not has_provider_lineage and isinstance(doc.meta.get("paper_classification"), dict):
        p_name = str(getattr(provider, "name", "?") or "?")
        raw_p_model = getattr(provider, "model", "")
        p_model = raw_p_model if isinstance(raw_p_model, str) and raw_p_model else p_name
        classify_eff = _effective_provider_effort(provider, getattr(settings, "pdf_classifier_effort", "low"), slot="classify")
        lineage.record_stage(stage="classify", requested_provider=p_name, requested_model=p_model, requested_effort=classify_eff, final_provider=p_name, final_model=p_model, status="ok")
    if doc.meta.get("vlm_transcribed") and doc.meta.get("vlm_model"):
        lineage.record_stage(stage="vlm", requested_provider=str(doc.meta.get("vlm_provider") or "gemini"), requested_model=str(doc.meta.get("vlm_model") or ""), requested_effort="", final_provider=str(doc.meta.get("vlm_provider") or "gemini"), final_model=str(doc.meta.get("vlm_model") or ""), status="ok")

    if on_progress:
        pname = getattr(provider, "name", "?")
        on_progress("LLM 초록 및 엔티티/관계 구조화 추출", f"provider={pname}, effort={eff}")
    eff_badge = f" ({eff})" if eff else ""
    emit_progress(f"LLM 초록 및 엔티티/관계 구조화 추출{eff_badge}")

    extract_started = time.perf_counter()
    try:
        if _accepts_kwarg(provider.extract, "effort"):
            result = provider.extract(doc, None, effort=eff)  # type: ignore[arg-type]
        else:
            result = provider.extract(doc, None)  # type: ignore[arg-type]
    except Exception as e:  # noqa: BLE001
        return False, error_text(e, prefix="extract failed: ")
    extract_ms = (time.perf_counter() - extract_started) * 1000.0
    if not has_provider_lineage:
        p_name = str(getattr(provider, "name", "?") or "?")
        raw_res_model = getattr(result, "model", "")
        raw_p_model = getattr(provider, "model", "")
        p_model = str((raw_res_model if isinstance(raw_res_model, str) and raw_res_model else "") or (raw_p_model if isinstance(raw_p_model, str) and raw_p_model else "") or p_name)
        lineage.record_stage(stage="extract_graph", requested_provider=p_name, requested_model=p_model, requested_effort=eff, final_provider=p_name, final_model=p_model, status="ok", duration_ms=extract_ms)

    # Decouple summary generation when the summarize slot overrides extract_graph.
    separate_summarize = False
    sum_effort = eff
    slot_spec_fn = getattr(provider, "slot_spec", None)
    if callable(slot_spec_fn):
        eg_spec = slot_spec_fn("extract_graph")
        sum_spec = slot_spec_fn("summarize")
        if isinstance(eg_spec, (tuple, list)) and isinstance(sum_spec, (tuple, list)) and len(sum_spec) >= 3:
            separate_summarize = eg_spec != sum_spec
            sum_effort = _effective_provider_effort(provider, sum_spec[2] or eff, slot="summarize")
    if separate_summarize:
        if on_progress:
            on_progress("LLM 초록 별도 생성", f"provider={sum_spec[0]}, model={sum_spec[1]}")
        sum_started = time.perf_counter()
        try:
            if _accepts_kwarg(provider.summarize_document, "effort"):
                sum_text = provider.summarize_document(doc, effort=sum_effort)  # type: ignore[attr-defined]
            else:
                sum_text = provider.summarize_document(doc)  # type: ignore[attr-defined]
        except Exception as e:  # noqa: BLE001
            return False, error_text(e, prefix="summarize failed: ")
        result.summary = (sum_text or "").strip()
    elif not has_provider_lineage:
        p_name = str(getattr(provider, "name", "?") or "?")
        raw_res_model = getattr(result, "model", "")
        raw_p_model = getattr(provider, "model", "")
        p_model = str((raw_res_model if isinstance(raw_res_model, str) and raw_res_model else "") or (raw_p_model if isinstance(raw_p_model, str) and raw_p_model else "") or p_name)
        lineage.record_stage(stage="summarize", requested_provider=p_name, requested_model=p_model, requested_effort=eff, final_provider=p_name, final_model=p_model, status="ok" if (result.summary and result.summary.strip()) else "empty", duration_ms=extract_ms)

    if cancel_check is not None and cancel_check() is not None:
        return False, "__cancelled__"

    if before_store is not None:
        before_store()

    report.summary = result.summary
    if not (result.summary and result.summary.strip()):
        report.summary_rendered = False
        report.summary_error = "초록 추출 실패 또는 빈 초록"
    else:
        report.summary_rendered = True

    # [LLM tier] 모델 원본 출력 보관(후처리만 바꿔도 재호출 없이 재생).
    if result.raw_response:
        dbm.log_extraction(
            conn, document_id=doc.id, provider=getattr(provider, "name", "?"),
            model=result.model, prompt_version=result.prompt_version,
            raw_response=result.raw_response,
            kb_id=tid,
        )

    # 가독 렌더(detail) — 구조화 추출과 독립된 별도 LLM 호출. 그래프와 무관해
    # 실패해도 적재를 깨지 않는다(조용히 건너뜀). refresh/reextract 도 같은 경로라 갱신됨.
    detail_effort = eff
    if effort is None and callable(slot_spec_fn):
        detail_spec = slot_spec_fn("render_detail")
        if isinstance(detail_spec, (tuple, list)) and len(detail_spec) >= 3 and detail_spec[2]:
            detail_effort = detail_spec[2]
    detail_effort = _effective_provider_effort(provider, detail_effort, slot="render_detail")

    if on_progress:
        on_progress("LLM 가독 상세(detail) 렌더링 생성", f"format={format or '기본'}, effort={detail_effort}")
    emit_progress(f"LLM 가독 상세(detail) 렌더링 생성{eff_badge}")

    if cancel_check is not None and cancel_check() is not None:
        return False, "__cancelled__"

    detail_err_box: list[str] = []
    detail_started = time.perf_counter()
    detail_ok = ensure_document_detail(
        conn,
        provider,
        doc,
        force=True,
        format=format,
        focus=focus,
        effort=detail_effort,
        full_content=full_content,
        out_error=detail_err_box,
        kb_id=tid,
        settings=settings,
    )
    detail_ms = (time.perf_counter() - detail_started) * 1000.0
    if not has_provider_lineage:
        p_name = str(getattr(provider, "name", "?") or "?")
        raw_p_model = getattr(provider, "model", "")
        p_model = raw_p_model if isinstance(raw_p_model, str) and raw_p_model else p_name
        lineage.record_stage(stage="render_detail", requested_provider=p_name, requested_model=p_model, requested_effort=detail_effort, final_provider=p_name, final_model=p_model, status="ok" if detail_ok else "error", duration_ms=detail_ms, detail=(detail_err_box[0][:240] if (not detail_ok and detail_err_box) else ""))
    if not detail_ok:
        report.detail_rendered = False
        err_detail = detail_err_box[0] if detail_err_box else "타임아웃 또는 모델 오류"
        report.detail_error = f"가독 상세(detail) 생성 실패 ({err_detail})"

    _judge_method = getattr(provider, "judge_same_entity", None)

    def _judge_fn(nm, et, obs, cand, aliases=None):
        if _judge_method is None:
            return False
        from ..extract.provider import MergeCandidate

        mc = MergeCandidate(
            new_name=nm, new_type=et, new_aliases=list(aliases or []), new_observations=obs,
            cand_name=cand.name, cand_type=cand.type,
            cand_aliases=cand.aliases, cand_observations=cand.observations,
        )
        try:
            return _judge_method(mc, document_id=doc.id)
        except TypeError:
            return _judge_method(mc)

    from ..extract.jev import make_jev_shadow

    # Jev has no decision role: it scores the same candidates at the same time and only records the result (JEV_SHADOW_EVALUATION_DESIGN.md, owner decision 2026-11-20)
    jev_shadow = make_jev_shadow(settings)
    embed_pin = resolve_embed_identity(provider, settings)
    kb_pin = read_embed_pin(conn, tid)
    active_sid, target_sid = read_kb_embedding_spaces(conn, tid)
    active_eos = is_kb_active_space_eos(conn, tid)
    eff_embed_model = embed_pin.model
    eff_embed_provider = embed_pin.provider
    if kb_pin is not None and kb_pin.model and target_sid and target_sid != active_sid and not active_eos:
        eff_embed_model = kb_pin.model
        eff_embed_provider = kb_pin.provider or embed_pin.provider
    has_embed = bool(provider.has_embed_capability()) if hasattr(provider, "has_embed_capability") else True

    name_to_id: dict[str, str] = {}
    touched_entities = []
    resolution_results = []
    total_entities = len(result.entities)

    if on_progress:
        on_progress("지식 그래프 엔티티 해소/병합", f"추출된 엔티티 {total_entities}개")
    emit_progress(f"지식 그래프 엔티티 해소/병합 ({total_entities}개)")

    def _flush_partial_decisions() -> None:
        partial_decisions = [
            getattr(r, "decision", None)
            for r in resolution_results
            if getattr(r, "decision", None) is not None
        ]
        if partial_decisions and getattr(doc, "id", None):
            from ..extract.decision import attach_resolution_meta

            attach_resolution_meta(doc, partial_decisions)
            dbm.update_document_meta(conn, doc.id, doc.meta, kb_id=tid)
            conn.commit()

    if cancel_check is not None and cancel_check() is not None:
        _flush_partial_decisions()
        return False, "__cancelled__"

    embed_calls = [0]
    embed_ms = [0.0]
    for idx_e, ee in enumerate(result.entities, 1):
        if cancel_check is not None and cancel_check() is not None:
            _flush_partial_decisions()
            return False, "__cancelled__"
        etype, prov = classify_entity_type(ee.type)
        if ee.proposed_type:
            dbm.log_proposal(conn, "entity_type", ee.proposed_type,
                             context=ee.name, document_id=doc.id, kb_id=tid)
            report.proposals += 1

        embed_callable = None
        if has_embed and not active_eos:
            def _embed(ee=ee, _model=eff_embed_model):
                from ..extract.frame import format_entity_frame

                frame_text = format_entity_frame(ee)
                t0 = time.perf_counter()
                try:
                    return _call_provider_embed(provider, frame_text, title=ee.name, model=_model if _model and _model != embed_pin.model else None)
                finally:
                    embed_calls[0] += 1
                    embed_ms[0] += (time.perf_counter() - t0) * 1000.0

            embed_callable = _embed

        def _on_judge_candidate(name1: str, name2: str):
            if on_progress:
                on_progress("엔티티 LLM 동일체 판정", f"[{idx_e}/{total_entities}] '{name1}' ↔ '{name2}'")

        res = resolve_or_create(
            conn, vstore,
            name=ee.name, etype=etype, aliases=ee.aliases,
            observations=ee.observations, document_id=doc.id,
            embed_fn=embed_callable, judge_fn=_judge_fn, provisional=prov,
            on_judge=_on_judge_candidate, shadow=jev_shadow,
            kb_id=tid,
            embed_model=eff_embed_model,
            embed_provider=eff_embed_provider,
            provider=provider if has_embed else None,
        )
        ent, created = res.entity, res.created
        resolution_results.append(res)
        name_to_id[ee.name] = ent.id
        touched_entities.append(ent)
        if created:
            report.entities_created += 1
            report.new_entity_names.append(ent.name)
        else:
            report.entities_linked += 1
            report.linked_entity_names.append(ent.name)

        if getattr(res, "decision", None) is not None:
            dec = res.decision
            emit_progress({
                "stage": "decision",
                "entity": dec.entity,
                "decision": dec.decision,
                "candidate": dec.candidate,
                "score": dec.score,
                "reason": dec.reason,
            })

    # Decision Stream (방안 B: documents.meta["resolution_log"]) 및 Heatmap Matrix 산출
    decisions = [
        getattr(r, "decision", None)
        for r in resolution_results
        if getattr(r, "decision", None) is not None
    ]
    if decisions:
        from ..extract.decision import attach_resolution_meta

        attach_resolution_meta(doc, decisions)
        dbm.update_document_meta(conn, doc.id, doc.meta, kb_id=tid)
        report.has_decision_stream = True
        if jev_shadow is not None:
            from ..store import pair_review

            pair_review.enqueue_results(conn, kb_id=tid, document_id=doc.id, results=resolution_results)

    if name_to_id:
        from ..extract.decision import evaluate_decision_matrix

        candidate_map: dict[str, Any] = {}
        for dec in decisions or []:
            if dec.candidate and dec.candidate not in candidate_map:
                cand_ent = None
                if dec.target_entity_id:
                    cand_ent = dbm.get_entity(conn, dec.target_entity_id, kb_id=tid)
                if not cand_ent:
                    for found in dbm.find_entities_by_name_or_alias(conn, dec.candidate, kb_id=tid):
                        cand_ent = found
                        break
                candidate_map[dec.candidate] = cand_ent if cand_ent else dec.candidate

        for res in resolution_results:
            rel_cands = (getattr(res, "relational_candidates", []) or []) + (getattr(res, "multihop_candidates", []) or [])
            for owner_id, _ in rel_cands:
                if len(candidate_map) >= 30:
                    break
                cand_ent = dbm.get_entity(conn, owner_id, kb_id=tid)
                if cand_ent and cand_ent.name not in candidate_map:
                    candidate_map[cand_ent.name] = cand_ent

        all_cands = dbm.all_entities(conn, kb_id=tid)
        for cand in all_cands:
            if len(candidate_map) >= 30:
                break
            if cand.name not in candidate_map:
                candidate_map[cand.name] = cand

        final_candidates = list(candidate_map.values())[:30] if candidate_map else (all_cands[:30] if all_cands else list(name_to_id.keys()))
        mat_data = evaluate_decision_matrix(
            settings=settings,
            document_id=doc.id,
            extracted_entities=list(name_to_id.keys()),
            candidate_entities=final_candidates,
            decisions=decisions or [],
        )
        report.heatmap_matrix = mat_data.to_dict()
        doc.meta["heatmap_matrix"] = report.heatmap_matrix
        dbm.update_document_meta(conn, doc.id, doc.meta, kb_id=tid)
        emit_progress({
            "stage": "heatmap_matrix",
            "matrix": report.heatmap_matrix,
        })

    if cancel_check is not None and cancel_check() is not None:
        _flush_partial_decisions()
        return False, "__cancelled__"

    if on_progress:
        on_progress("관계(Relation) 검증 및 적재", f"총 {len(result.relations)}개 관계")
    emit_progress(f"관계(Relation) 검증 및 적재 ({len(result.relations)}개)")

    for er in result.relations:
        src_id = name_to_id.get(er.source)
        tgt_id = name_to_id.get(er.target)
        if not src_id or not tgt_id or src_id == tgt_id:
            report.relations_rejected += 1
            continue
        rtype, _ = classify_relation_type(er.type)
        if er.proposed_type:
            dbm.log_proposal(conn, "relation_type", er.proposed_type,
                             context=f"{er.source}->{er.target}", document_id=doc.id, kb_id=tid)
            report.proposals += 1
        src = dbm.get_entity(conn, src_id, kb_id=tid)
        tgt = dbm.get_entity(conn, tgt_id, kb_id=tid)
        vr = validate_relation(rtype, src.type if src else "", tgt.type if tgt else "")
        if not vr.ok:
            report.relations_rejected += 1
            continue
        rel = Relation(type=rtype, source_id=src_id, target_id=tgt_id,
                       sources=[doc.id], provisional=vr.provisional)
        before = dbm.counts(conn, kb_id=tid)["relations"]
        dbm.upsert_relation(conn, rel, kb_id=tid)
        if dbm.counts(conn, kb_id=tid)["relations"] > before:
            report.relations_added += 1

    # Phase 2: 지식 그래프 전역 관계(Edge) 자동 형성 파이프라인
    # ResolutionResult.relational_candidates를 소비하여 횡단형 엣지 수립
    enable_linking = getattr(settings, "enable_relation_linking", True)
    judge_rel_method = getattr(provider, "judge_relationship", None)
    if enable_linking and judge_rel_method is not None and resolution_results:
        gstore = GraphStore(conn, kb_id=tid)
        max_per_ent = getattr(settings, "max_relation_judges_per_entity", 3)
        max_per_doc = getattr(settings, "max_relation_judges_per_doc", 10)

        cross_candidates: list[tuple[Entity, str, float]] = []
        for r_res in resolution_results:
            e_node = r_res.entity
            for cand_id, score in r_res.relational_candidates[:max_per_ent]:
                if cand_id and cand_id != e_node.id:
                    cross_candidates.append((e_node, cand_id, score))

        seen_pairs = set()
        unique_candidates: list[tuple[Entity, str, float]] = []
        for e_node, cand_id, score in cross_candidates:
            pair_key = (min(e_node.id, cand_id), max(e_node.id, cand_id))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            if gstore.has_edge(e_node.id, cand_id, bidirectional=True):
                continue
            unique_candidates.append((e_node, cand_id, score))

        unique_candidates.sort(key=lambda x: x[2], reverse=True)
        eval_candidates = unique_candidates[:max_per_doc]

        if eval_candidates:
            if cancel_check is not None and cancel_check() is not None:
                _flush_partial_decisions()
                return False, "__cancelled__"
            if on_progress:
                on_progress("전역 지식 관계(Cross-link) 판정", f"후보 {len(eval_candidates)}쌍 평가")
            emit_progress(f"전역 지식 관계(Cross-link) 판정 ({len(eval_candidates)}쌍)")

            eval_items: list[tuple[Entity, Any, float]] = []
            for e_node, cand_id, score in eval_candidates:
                cand = dbm.get_entity(conn, cand_id, kb_id=tid)
                if cand is not None:
                    eval_items.append((e_node, cand, score))

            bind_fn = getattr(provider, "bind_lineage", None)

            def _evaluate_candidate(item: tuple[Entity, Any, float]):
                e_node, cand, score = item
                if on_progress:
                    on_progress(
                        "관계 판정기 질의",
                        f"'{e_node.name}' ↔ '{cand.name}' (유사도 {score:.2f})",
                    )
                rc = RelationCandidate(
                    entity_a_name=e_node.name,
                    entity_a_type=e_node.type,
                    entity_a_observations=e_node.observations[:5],
                    entity_a_aliases=e_node.aliases[:5],
                    entity_b_name=cand.name,
                    entity_b_type=cand.type,
                    entity_b_observations=cand.observations[:5],
                    entity_b_aliases=cand.aliases[:5],
                    similarity_score=score,
                    context=f"문서: {doc.title or doc.id}\n초록: {report.summary[:300]}",
                )
                ctx_mgr = bind_fn(lineage) if (has_provider_lineage and callable(bind_fn)) else contextlib.nullcontext()
                with ctx_mgr:
                    try:
                        try:
                            judgement = judge_rel_method(rc, document_id=doc.id)
                        except TypeError:
                            judgement = judge_rel_method(rc)
                    except Exception:  # noqa: BLE001
                        judgement = None
                return (e_node, cand, judgement)

            rel_judge_started = time.perf_counter()
            max_workers = min(len(eval_items), max(1, getattr(provider, "max_concurrency", 2)))
            if max_workers > 1 and len(eval_items) > 1:
                with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                    judged_results = list(executor.map(_evaluate_candidate, eval_items))
            else:
                judged_results = [_evaluate_candidate(item) for item in eval_items]
            rel_judge_ms = (time.perf_counter() - rel_judge_started) * 1000.0
            if not has_provider_lineage and eval_items:
                p_name = str(getattr(provider, "name", "?") or "?")
                raw_p_model = getattr(provider, "model", "")
                p_model = raw_p_model if isinstance(raw_p_model, str) and raw_p_model else p_name
                rj_eff = _effective_provider_effort(provider, eff, slot="relation_judge")
                lineage.record_stage(stage="relation_judge", requested_provider=p_name, requested_model=p_model, requested_effort=rj_eff, final_provider=p_name, final_model=p_model, status="ok", duration_ms=rel_judge_ms, calls=len(eval_items))

            for e_node, cand, judgement in judged_results:
                if judgement and judgement.has_relation and judgement.relation_type:
                    rtype, _ = classify_relation_type(judgement.relation_type)
                    if judgement.direction == "backward":
                        s_id, t_id = cand.id, e_node.id
                        s_name, t_name = cand.name, e_node.name
                    else:
                        s_id, t_id = e_node.id, cand.id
                        s_name, t_name = e_node.name, cand.name

                    added_rel = gstore.add_edge(
                        s_id,
                        t_id,
                        rtype,
                        confidence=judgement.confidence,
                        sources=[doc.id],
                    )
                    if added_rel is not None:
                        report.relations_added += 1
                        report.cross_relations_added += 1
                        report.cross_linked_relations.append(f"{s_name} -> {t_name} ({rtype})")
                        if cand not in touched_entities:
                            touched_entities.append(cand)
                        if judgement.direction == "bidirectional":
                            gstore.add_edge(
                                t_id,
                                s_id,
                                rtype,
                                confidence=judgement.confidence,
                                sources=[doc.id],
                            )

    if not has_provider_lineage:
        p_name = str(getattr(provider, "name", "?") or "?")
        raw_p_model = getattr(provider, "model", "")
        p_model = raw_p_model if isinstance(raw_p_model, str) and raw_p_model else p_name
        if has_embed and embed_calls[0] > 0:
            lineage.record_stage(stage="embed", requested_provider=embed_pin.provider or p_name, requested_model=embed_pin.model or p_model, requested_effort="", final_provider=embed_pin.provider or p_name, final_model=embed_pin.model or p_model, status="ok", duration_ms=embed_ms[0], calls=embed_calls[0])
        if decisions:
            all_cands = [c for d in decisions for c in (getattr(d, "candidates", None) or [])]
            judge_cands = [c for c in all_cands if c.get("outcome") in ("judge_same", "judge_different")]
            if judge_cands:
                ej_eff = _effective_provider_effort(provider, eff, slot="entity_judge")
                lineage.record_stage(stage="entity_judge", requested_provider=p_name, requested_model=p_model, requested_effort=ej_eff, final_provider=p_name, final_model=p_model, status="ok", duration_ms=sum(float(c.get("judge_ms") or 0.0) for c in judge_cands), calls=len(judge_cands))
    if decisions:
        shadows = [getattr(d, "shadow", None) for d in decisions if getattr(d, "shadow", None)]
        last_shadow = shadows[-1] if shadows else {}
        lineage.set_jev({
            "enabled": bool(jev_shadow is not None),
            "role": "shadow",
            "evaluated": len(shadows),
            "status": last_shadow.get("status", "disabled" if jev_shadow is None else "idle"),
            "model": last_shadow.get("model", getattr(settings, "jev_model", "")),
        })
    model_lineage = lineage.summarize()
    doc.meta["model_lineage"] = model_lineage
    report.model_lineage = model_lineage
    if getattr(doc, "id", None):
        if cancel_check is not None and cancel_check() is not None:
            _flush_partial_decisions()
            return False, "__cancelled__"
        dbm.update_document_meta(conn, doc.id, doc.meta, kb_id=tid)
        _start_contribution_revisions(conn, settings, kb_id=tid, document_id=doc.id)
        _match_document_theme(conn, kb_id=tid, document_id=doc.id)
        from ..collaboration.campaign import notices_for_ingest

        report.notices = notices_for_ingest(conn, kb_id=tid, document_id=doc.id)
        conn.commit()
        _auto_contribute_document(conn, settings, kb_id=tid, document_id=doc.id)

    return True, None


def _auto_contribute_document(conn: sqlite3.Connection, settings: Any, *, kb_id: int, document_id: str) -> None:
    """After the extraction of a new document of a personal knowledge base, the automatic rule of its account runs for this document (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 9.9 rule 4). The merge jobs run after the service job. A failure here never fails the ingest. The canonical origin of the settings lets the run send offers to shared knowledge bases of other servers (KNOWLEDGE_FEDERATION_DESIGN.md Section 12.3)."""
    try:
        row = conn.execute("SELECT is_personal, account_id FROM knowledge_bases WHERE id=?", (int(kb_id),)).fetchone()
        if row is None or not row[0] or row[1] is None:
            return
        from ..collaboration import auto_contribution

        from ..collaboration.contributions import canonical_origin

        auto_contribution.run_account(conn, int(row[1]), origin=None if settings is None else canonical_origin(settings), documents=[document_id])
    except Exception as exc:  # noqa: BLE001
        import logging

        conn.rollback()
        logging.getLogger("claire.auto_contribution").warning("automatic contribution of document %s failed: %s", document_id, type(exc).__name__)


def _match_document_theme(conn: sqlite3.Connection, *, kb_id: int, document_id: str) -> None:
    """The theme match of a document of a personal knowledge base, after its extraction (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 8.5 rule 7). It makes no LLM call. A failure here never fails the ingest."""
    try:
        row = conn.execute("SELECT is_personal FROM knowledge_bases WHERE id=?", (int(kb_id),)).fetchone()
        if row is None or not row[0] or conn.execute("SELECT 1 FROM themes WHERE state='active' LIMIT 1").fetchone() is None:
            return
        from ..collaboration import theme_judgement

        theme_judgement.match_document(conn, kb_id=kb_id, document_id=document_id)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        import logging

        logging.getLogger("claire.theme_judgement").warning("theme match of document %s failed: %s", document_id, type(exc).__name__)


def _start_contribution_revisions(conn: sqlite3.Connection, settings: Any, *, kb_id: int, document_id: str) -> None:
    """The document has a new extraction. When it is the root of contributions and its content changed, their revisions start (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 9.5). The merge jobs run after the service job (``IngestService``). A failure here never fails the ingest."""
    try:
        if conn.execute("SELECT 1 FROM kb_contributions WHERE root_document_id=? LIMIT 1", (document_id,)).fetchone() is None:
            return
        from ..collaboration import contributions

        contributions.start_revisions(conn, Path(settings.data_dir), root_kb_id=kb_id, root_document_id=document_id)
    except Exception as exc:  # noqa: BLE001
        import logging

        logging.getLogger("claire.contributions").warning("revisions of document %s did not start: %s", document_id, type(exc).__name__)


def ensure_document_detail(
    conn: sqlite3.Connection,
    provider: Provider,
    doc: Document,
    *,
    force: bool = False,
    format: str | None = None,
    focus: str | None = None,
    effort: str | None = None,
    full_content: bool = False,
    out_error: list[str] | None = None,
    kb_id: int | None = None,
    settings: Any = None,
) -> bool:
    """문서의 가독 렌더(detail)를 생성·저장. **그래프와 독립**(별도 LLM 호출).

    The body is the 대표 정리본: the run uses the rendition template ``standard`` and records the template version in ``document_renditions`` (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.1 rule 4). The other renditions use ``render_additional_rendition``.

    신규 적재(extract_resolve_store)와 기존 문서 백필이 공유하는 단일 경로. detail 컬럼만 채우므로 엔티티/관계를 건드리지 않는다 → reset_graph/rebuild 없이 백필 가능(advisor). 이미 있으면(force=False) 건너뛰고, 생성 실패는 조용히 False(적재 실패로 번지지 않음).
    """
    if full_content:
        if doc.meta is None:
            doc.meta = {}
        doc.meta["full_content"] = True

    # RD23: ingestion writes AsciiDoc bodies only. A format from the document meta or the settings cannot select Markdown.
    fmt = "adoc"

    valid, guard_err = validate_extractable_content(doc)
    if not valid:
        if out_error is not None:
            out_error.append(f"insufficient_content: {guard_err}")
        return False

    # CT28: a stored focus (doc.meta["focus"]) is data only; no render reads it.
    focus_val = focus

    tid = kb_id if kb_id is not None else getattr(conn, "kb_id", 0)

    if not force:
        existing_detail = dbm.get_document_detail(conn, doc.id, kb_id=tid)
        existing_fmt = dbm.get_document_detail_format(conn, doc.id, kb_id=tid)
        # A stored focus is compared only when a caller gives a focus; no entry point gives one (CT28).
        same_focus = focus_val is None or dbm.get_document_focus(conn, doc.id, kb_id=tid) == focus_val
        if existing_detail and existing_fmt == fmt and same_focus:
            return False

    render = getattr(provider, "render_detail", None)
    if render is None:
        if out_error is not None:
            out_error.append("provider does not support render_detail")
        return False

    try:
        eff = _body_effort(provider, doc, effort=effort, settings=settings)
    except ProviderPolicyError as e:
        if out_error is not None:
            out_error.append(error_text(e))
        return False

    try:
        text = _render_body(render, doc, template=PRIMARY_RENDITION_TEMPLATE, focus=focus_val, effort=eff)
    except Exception as e:  # noqa: BLE001
        import logging

        logging.getLogger("claire.pipeline").warning(
            "ensure_document_detail failed for doc_id=%s: %s", doc.id, error_text(e)
        )
        if out_error is not None:
            out_error.append(error_text(e))
        return False
    if text and text.strip():
        dbm.set_document_detail(conn, doc.id, text.strip(), format=fmt, kb_id=tid)
        renditions_store.record_primary(conn, doc.id, tid, lineage=render_lineage(provider))
        if focus_val is not None:
            if doc.meta is None:
                doc.meta = {}
            doc.meta["focus"] = focus_val
            dbm.set_document_focus(conn, doc.id, focus_val, kb_id=tid)
        return True
    if out_error is not None:
        out_error.append("empty detail output returned by model")
    return False


def _body_effort(provider: Any, doc: Document, *, effort: str | None, settings: Any) -> str:
    """The effort of a body run: the given effort, else the PDF rule (a paper above the threshold gets the paper effort), else the effort of the slot ``render_detail``. ``ProviderPolicyError`` passes to the caller."""
    eff = effort
    if eff is None:
        if doc.source_type in ("pdf", "odt"):
            from ..extract.classifier import reconcile_pdf_metadata

            settings = resolve_job_settings(settings, provider)
            classification = (doc.meta or {}).get("paper_classification")
            if classification and isinstance(classification, dict):
                is_paper = classification.get("is_paper", False)
            else:
                is_paper = reconcile_pdf_metadata(doc, settings, provider=provider).is_paper
            if is_paper and len(doc.raw_text or "") >= settings.pdf_paper_threshold_chars:
                eff = settings.pdf_paper_effort or "high"
            else:
                eff = settings.pdf_default_effort or provider_effort(provider)
        else:
            slot_spec_fn = getattr(provider, "slot_spec", None)
            spec = slot_spec_fn("render_detail") if callable(slot_spec_fn) else None
            slot_eff = spec[2] if isinstance(spec, (tuple, list)) and len(spec) >= 3 else ""
            eff = slot_eff or provider_effort(provider)
    return _effective_provider_effort(provider, eff, slot="render_detail")


def render_additional_rendition(
    conn: sqlite3.Connection,
    provider: Provider,
    doc: Document,
    *,
    template: str,
    kb_id: int,
    effort: str | None = None,
    settings: Any = None,
    out_error: list[str] | None = None,
) -> bool:
    """Make the rendition ``template`` (not ``standard``) of a stored document from its stored raw text, and store its body (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.3 rules 1 and 2). It fetches nothing and costs one model call. A failure gives ``False`` and an error text; the document and its other renditions do not change."""
    if template == PRIMARY_RENDITION_TEMPLATE:
        raise ValueError("the 대표 정리본 uses ensure_document_detail")
    valid, guard_err = validate_extractable_content(doc)
    if not valid:
        if out_error is not None:
            out_error.append(f"insufficient_content: {guard_err}")
        return False
    render = getattr(provider, "render_detail", None)
    if render is None:
        if out_error is not None:
            out_error.append("provider does not support render_detail")
        return False
    try:
        eff = _body_effort(provider, doc, effort=effort, settings=settings)
        text = _render_body(render, doc, template=template, focus=None, effort=eff)
    except Exception as e:  # noqa: BLE001
        if out_error is not None:
            out_error.append(error_text(e))
        return False
    if not (text and text.strip()):
        if out_error is not None:
            out_error.append("empty detail output returned by model")
        return False
    renditions_store.store_additional(conn, doc.id, kb_id, template=template, detail=text.strip(), lineage=render_lineage(provider))
    return True


def render_lineage(provider: Any) -> dict[str, str]:
    """The provider and the model of a body run, as the slot ``render_detail`` names them (the requested model)."""
    slot_spec_fn = getattr(provider, "slot_spec", None)
    spec = slot_spec_fn("render_detail") if callable(slot_spec_fn) else None
    if isinstance(spec, (tuple, list)) and len(spec) >= 2 and spec[1]:
        return {"provider": str(spec[0] or ""), "model": str(spec[1])}
    raw_model = getattr(provider, "model", "")
    return {"provider": str(getattr(provider, "name", "") or ""), "model": raw_model if isinstance(raw_model, str) else ""}


def _render_body(render: Callable[..., str], doc: Document, *, template: str, focus: str | None, effort: str | None) -> str:
    """One body run of one rendition template. A provider without the argument ``effort`` gets none."""
    if _accepts_kwarg(render, "effort"):
        return render(doc, format="adoc", focus=focus, template=template, effort=effort)
    return render(doc, format="adoc", focus=focus, template=template)


def ensure_watch_classification(
    conn: sqlite3.Connection, provider: Provider, doc: Document, kb_id: int | None = None,
) -> bool:
    """[주기 재확인] 변하는 콘텐츠(벤치/순위 등)인지 LLM 판단 → watch 설정. 비필수(별도 호출).

    신규 1차 적재에만 호출(비용 통제 — 호출측 source 게이트). rate limit 등 실패는 조용히 False(적재 막지 않음 — watch 미판단으로 남고 나중에 수동/재판단 가능)."""
    fn = getattr(provider, "classify_watch", None)
    if fn is None:
        return False
    try:
        res = fn(doc)
    except Exception:  # noqa: BLE001
        return False
    if not isinstance(res, dict):
        return False
    days = res.get("interval_days")
    interval = float(days) * 86400 if days else None
    reason = ("llm: " + (res.get("reason") or ""))[:200]
    tid = kb_id if kb_id is not None else getattr(conn, "kb_id", 0)
    dbm.set_document_watch(conn, doc.id, enabled=bool(res.get("watch")),
                           interval=interval, reason=reason, kb_id=tid)
    return True


def _guess_kind(payload: str) -> str:
    """inbound payload 종류 추정(raw_inbox.kind 용)."""
    t = (payload or "").strip().lower()
    if t.startswith("http://") or t.startswith("https://"):
        return "url"
    if t.startswith("file://"):
        return "file"
    return "text"
