"""인제스트 파이프라인 — payload 를 받아 그래프에 적재하는 전체 흐름.

  payload → fetch(router) → dedup → insert document
          → provider.extract → 엔티티 해소/머지(+임베딩) → 관계 검증/적재
          → vault export → IngestReport

fetch_fn 을 주입 가능하게 하여 네트워크 없이 테스트한다.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..extract.provider import Provider, emit_progress
from ..extract.resolver import resolve_or_create
from ..ontology.base import Document, Relation
from ..ontology.registry import (
    classify_entity_type,
    classify_relation_type,
    validate_relation,
)
from ..store import db as dbm
from ..store.vault import export_entities
from ..store.vectors import VectorStore
from .router import fetch as default_fetch


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
    proposals: int = 0
    error: str | None = None
    inbox_id: int | None = None
    new_entity_names: list[str] = field(default_factory=list)
    linked_entity_names: list[str] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)  # 1홉 확장 후보 URL
    directive: str | None = None
    full_content: bool = False
    effort: str | None = None
    has_transcript: bool | None = None
    stt_error: str | None = None
    pdf_parser_fallback: bool = False
    pdf_parser_fallback_reason: str | None = None
    presentation_pdfs: int = 0
    presentation_pdf_chars: int = 0
    presentation_pdf_parsers: list[str] = field(default_factory=list)

    def telegram_summary(self) -> str:
        if self.error:
            return f"❌ 적재 실패: {self.error}"
        if self.duplicate:
            return f"♻️ 이미 있는 자료입니다 (dedup): {self.title or self.document_id}"

        is_stt_failed = bool(
            self.stt_error
            or (self.source_type == "video" and self.has_transcript is False and self.stt_error)
        )
        if is_stt_failed:
            head = "⚠️ 부분 적재 (STT 전사 실패)"
        elif self.pdf_parser_fallback:
            head = "⚠️ PDF 파서 대체 적재 (Docling 실패 → PyPDF)"
        elif self.updated:
            head = "🔄 자료 업데이트(내용 변경 반영)"
        else:
            head = "✅ 적재 완료"

        parts = [f"{head}: {self.title or self.document_id}"]
        if self.pdf_parser_fallback:
            reason = self.pdf_parser_fallback_reason or "Docling 런타임 오류"
            parts.append(f"📄 Docling 레이아웃 파서 실패로 PyPDF가 대체 사용되었습니다. (사유: {reason})")
        if is_stt_failed:
            err_detail = f" (오류: {self.stt_error})" if self.stt_error else ""
            parts.append(f"🎙️ 오디오 STT 전사 실패: 음성 자막이 추출되지 못했습니다.{err_detail}")
        if self.presentation_pdfs:
            presentation_details = [f"{self.presentation_pdfs}개"]
            if self.presentation_pdf_chars:
                presentation_details.append(f"{self.presentation_pdf_chars:,}자")
            if self.presentation_pdf_parsers:
                presentation_details.append("/".join(self.presentation_pdf_parsers))
            parts.append(
                "📎 Presentation PDF 포함 ("
                + " · ".join(presentation_details)
                + ")"
            )

        meta_badges = []
        if self.full_content:
            meta_badges.append("🌐 원문 무절단 수집")
        if self.effort:
            meta_badges.append(f"🧠 추론: {self.effort}")
        if meta_badges:
            parts.append(" · ".join(meta_badges))
        if self.directive:
            parts.append(f"초점: {self.directive}")
        if self.partial and not is_stt_failed:
            parts.append("⚠️ 부분 처리(partial)")
        parts.append(f"요약: {self.summary[:300]}")
        parts.append(
            f"노드 신규 {self.entities_created} · 기존연결 {self.entities_linked} · "
            f"관계 {self.relations_added}"
        )
        if self.linked_entity_names:
            parts.append("연결됨: " + ", ".join(self.linked_entity_names[:8]))
        if self.proposals:
            parts.append(f"새 타입 제안 {self.proposals}건 기록")
        if self.candidates:
            parts.append(f"🔗 관련 링크 {len(self.candidates)}개 발견 — 가져올까요?")
        return "\n".join(parts)


def ingest(
    payload: str,
    *,
    conn: sqlite3.Connection,
    provider: Provider,
    vstore: VectorStore,
    vault_dir: Path | None = None,
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
    directive: str | None = None,
    effort: str | None = None,
    full_content: bool = False,
) -> IngestReport:
    report = IngestReport()
    report.full_content = full_content
    report.effort = effort
    # None 이면 호출 시점에 모듈 전역 default_fetch 를 조회(monkeypatch/교체 반영).
    if fetch_fn is None:
        fetch_fn = default_fetch

    # [Layer 1] 처리 전에 inbound 원본을 무조건 기록(실패해도 재생 가능).
    # inbox_id 가 주어지면(자동복구 재적재) 새 행을 만들지 않고 기존 행을 재사용 =
    # 재시도가 inbox 행을 폭증시키지 않고 같은 행의 status 만 갱신(멱등).
    if inbox_id is None:
        kind = inbox_kind or _guess_kind(payload)
        inbox_id = dbm.log_inbox(
            conn, source=source, payload=payload, kind=kind,
            user_id=user_id, chat_id=chat_id, file_name=file_name, file_ref=file_ref,
        )
    report.inbox_id = inbox_id

    try:
        # prefetched: 1홉 확장이 판정용으로 이미 가져온 Document 재사용(중복 fetch 방지).
        if prefetched is None:
            emit_progress("원문 가져오는 중…")  # 콜백 미설정 시 no-op(웹 스트림 적재만 표시)
            try:
                doc = fetch_fn(payload, full_content=full_content)
            except TypeError:
                doc = fetch_fn(payload)
        else:
            doc = prefetched
    except Exception as e:  # noqa: BLE001
        report.error = str(e)
        dbm.update_inbox(conn, inbox_id, status="error", error=str(e))
        return report

    if full_content:
        if doc.meta is None:
            doc.meta = {}
        doc.meta["full_content"] = True
    if effort:
        if doc.meta is None:
            doc.meta = {}
        doc.meta["applied_effort"] = effort

    report.source_type = doc.source_type
    report.partial = doc.partial
    report.title = doc.title
    report.directive = directive
    if doc.meta:
        if "has_transcript" in doc.meta:
            report.has_transcript = bool(doc.meta.get("has_transcript"))
        if "stt_error" in doc.meta:
            report.stt_error = doc.meta.get("stt_error")
        if "pdf_parser_fallback" in doc.meta:
            report.pdf_parser_fallback = bool(doc.meta.get("pdf_parser_fallback"))
        if "pdf_parser_fallback_reason" in doc.meta:
            report.pdf_parser_fallback_reason = doc.meta.get("pdf_parser_fallback_reason")
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
    if directive and directive.strip():
        if doc.meta is None:
            doc.meta = {}
        doc.meta["directive"] = directive.strip()

    # 0. 소각 툼스톤(Tombstone) 검사: 소각된 오염 데이터(URL/해시)는 재수집 원천 차단
    if dbm.is_tombstoned(conn, url=doc.url, canonical_url=doc.canonical_url, content_hash=doc.content_hash):
        report.error = "tombstoned: document was previously purged"
        dbm.update_inbox(conn, inbox_id, status="error", error=report.error)
        return report

    # 직접 PDF URL이 이미 VMware 비디오 문서의 Presentation 출처로 포함됐으면
    # 동일 원문을 독립 문서로 다시 만들지 않는다.
    if doc.source_type == "pdf" and doc.canonical_url:
        bundled_id = dbm.find_document_by_extra_source(conn, doc.canonical_url)
        if bundled_id:
            report.document_id = bundled_id
            report.duplicate = True
            dbm.update_inbox(conn, inbox_id, status="duplicate", document_id=bundled_id)
            return report

    _link_existing_presentation_documents(conn, doc)

    # dedup ① 내용 완전 동일(content_hash 일치)
    existing = dbm.find_document_by_hash(conn, doc.content_hash)
    if existing:
        existing_row = dbm.get_document_row(conn, existing)
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
        if not _store_doc_attachments_or_report(
            conn, inbox_id, doc, existing, data_dir, report
        ):
            return report
        if same_canonical and _presentation_state(doc.meta or {}) != old_presentation_state:
            dbm.update_document_meta(conn, existing, doc.meta)
            report.document_id = existing
            report.updated = True
            report.duplicate = False
            dbm.update_inbox(conn, inbox_id, status="done", document_id=existing)
            return report
        # 사용자가 새 directive(초점)를 명시적으로 지정한 경우:
        # 단순 중복 스킵하지 않고, 해당 문서의 초점을 갱신하고 가독 상세(detail)를 즉시 재생성(재적재)
        if directive and directive.strip():
            doc_obj = dbm.get_document(conn, existing)
            if doc_obj:
                dbm.set_document_directive(conn, existing, directive.strip())
                ensure_document_detail(
                    conn, provider, doc_obj, format=format, directive=directive.strip(), force=True
                )
                report.document_id = existing
                report.updated = True
                report.duplicate = False
                report.title = doc_obj.title or doc.title
                dbm.update_inbox(conn, inbox_id, status="done", document_id=existing)
                return report

        report.document_id = existing
        report.duplicate = True
        dbm.update_inbox(conn, inbox_id, status="duplicate", document_id=existing)
        return report

    # dedup ② 같은 canonical_url 인데 content_hash 가 다름 → 같은 자료의 *내용 갱신*.
    #   새 문서를 만들지(중복 노드) 않고, 건너뛰지도(갱신 유실) 않고, 기존 문서를 in-place
    #   갱신한다(엔티티 sources 연결 보존 = refresh_document 과 같은 의미).
    same_url = dbm.find_document_by_canonical_url(conn, doc.canonical_url)
    if same_url:
        doc.id = same_url
        old_row = dbm.get_document_row(conn, same_url)
        if old_row is not None:
            try:
                old_meta = json.loads(old_row["meta"] or "{}")
            except Exception:
                old_meta = {}
            preserve_presentation_history(old_meta, doc)
        if not _store_doc_attachments_or_report(
            conn, inbox_id, doc, same_url, data_dir, report
        ):
            return report
        dbm.update_document_content(
            conn, same_url, title=doc.title, raw_text=doc.raw_text,
            content_hash=doc.content_hash, fetched_at=doc.fetched_at,
            source_type=doc.source_type, partial=doc.partial, meta=doc.meta)
        report.document_id = same_url
        report.updated = True
        if data_dir is not None:
            try:
                from ..store.raw import save_artifact

                save_artifact(data_dir, same_url, doc.raw_text)
            except Exception:  # noqa: BLE001
                pass
        _download_doc_images(conn, doc, data_dir)
        ok, err = extract_resolve_store(
            conn, provider, vstore, doc, report, vault_dir=vault_dir, format=format, directive=directive, effort=effort, full_content=full_content)
        if not ok:
            report.error = err
            dbm.update_inbox(conn, inbox_id, status="error",
                             document_id=same_url, error=err)
            return report
        dbm.update_inbox(conn, inbox_id, status="done", document_id=same_url)
        return report

    # dedup ③ 근사 중복(near-duplicate). content_hash·canonical_url 을 비껴간 "같은 글
    #   다른 입구"(arxiv 버전 접미사, 동적요소 차이 등)를 MinHash 유사도로 잡는다.
    #   보수적(데이터 보존): 충분히 긴 비-partial 문서만, 높은 임계 → 별개 글 오병합 방지.
    near = dbm.near_duplicate_document(conn, doc)
    if near:
        near_id, score = near
        report.document_id = near_id
        report.duplicate = True
        dbm.update_inbox(conn, inbox_id, status="duplicate", document_id=near_id)
        return report

    emit_progress("구조화 추출·그래프 적재 중…")
    if not _store_doc_attachments_or_report(
        conn, inbox_id, doc, doc.id, data_dir, report
    ):
        return report
    dbm.insert_document(conn, doc)
    report.document_id = doc.id

    # [Layer 2] fetched 원본 텍스트를 gzip artifact 로 보관(재추출용).
    if data_dir is not None:
        try:
            from ..store.raw import save_artifact

            save_artifact(data_dir, doc.id, doc.raw_text)
        except Exception:  # noqa: BLE001
            pass  # 보관 실패가 본 파이프라인을 막지 않도록
    _download_doc_images(conn, doc, data_dir)

    # 추출 → 해소 → 관계 → vault (ingest/refresh 공용)
    ok, err = extract_resolve_store(
        conn, provider, vstore, doc, report, vault_dir=vault_dir, format=format, directive=directive, effort=effort, full_content=full_content)
    if not ok:
        report.error = err
        dbm.update_inbox(conn, inbox_id, status="error", document_id=doc.id, error=err)
        return report

    # 주기 크롤링 watch 판단(LLM, 비용 1콜) — 1차 신규 적재에만. onehop 자식/research 문서/
    # 복구·갱신 재적재는 제외(부적절·낭비). 실패는 조용히(watch 미판단으로 남음).
    if not doc.partial and not source.startswith(
            ("onehop", "recover", "replay", "refresh", "research")):
        emit_progress("주기 갱신 콘텐츠 여부 판단 중…")
        ensure_watch_classification(conn, provider, doc)

    # 1홉 확장. auto_expand 면 백그라운드 대기열에 등록(LLM 이 선별·판정·적재; expand-loop).
    # 아니면 기존 동작: 후보만 제안(텔레그램 confirm 버튼). 내부 연결은 위에서 이미 자동.
    if expand_max > 0 and not doc.partial:
        if auto_expand:
            dbm.enqueue_expand(conn, doc.id)
        else:
            from ..expand.onehop import find_candidates

            report.candidates = find_candidates(conn, doc, limit=expand_max)

    dbm.update_inbox(conn, inbox_id, status="done", document_id=doc.id)
    return report


def _download_doc_images(conn: sqlite3.Connection, doc: Document, data_dir: Path | None) -> None:
    """본문 이미지 후보를 로컬로 내려받아 보존(사용자 요구 — 외부 사이트/링크가 나중에
    사라지면 문서에 남는 게 깨진 이미지 링크뿐이라 저장해 둬야 함). ingest 신규/in-place
    갱신·refresh 가 공유. doc.meta['images'] 를 local 경로 포함 형태로 갱신 + DB 반영.
    이미지 후보 없거나 data_dir 없으면 조용히 스킵(개별 다운로드 실패는 raw.download_images
    가 원본 url 로 이미 폴백)."""
    images = (doc.meta or {}).get("images")
    if not images or data_dir is None:
        return
    from ..store.raw import download_images

    enriched = download_images(data_dir, doc.id, images)
    doc.meta["images"] = enriched
    dbm.set_document_images(conn, doc.id, enriched)


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
) -> bool:
    doc.id = document_id
    try:
        _store_required_doc_attachments(doc, data_dir)
        return True
    except Exception as exc:  # noqa: BLE001
        report.error = f"attachment_store_failed: {type(exc).__name__}: {exc}"
        dbm.update_inbox(
            conn,
            inbox_id,
            status="error",
            document_id=document_id if dbm.get_document_row(conn, document_id) else None,
            error=report.error,
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
) -> None:
    """이미 독립 적재된 PDF는 삭제·흡수하지 않고 영상 메타데이터에서 참조한다."""
    if doc.source_type != "video" or not doc.meta:
        return
    linked: list[str] = []
    for item in _presentation_items(doc.meta):
        url = item.get("public_url")
        if not url:
            continue
        existing_id = dbm.find_document_by_canonical_url(conn, url)
        if existing_id and existing_id != doc.id and existing_id not in linked:
            linked.append(existing_id)
    if linked:
        doc.meta["presentation_document_ids"] = linked
        doc.meta["presentation_document_id"] = linked[0]


def extract_resolve_store(
    conn: sqlite3.Connection,
    provider: Provider,
    vstore: VectorStore,
    doc: Document,
    report: IngestReport,
    *,
    vault_dir: Path | None = None,
    format: str | None = None,
    directive: str | None = None,
    effort: str | None = None,
    full_content: bool = False,
    on_progress: Callable[[str, str], None] | None = None,
) -> tuple[bool, str | None]:
    """문서 1건의 추출→엔티티 해소/머지→관계 검증/적재→vault export.

    ingest(신규 적재)와 refresh(복원 재적재)가 공유한다. doc.id 기준으로 동작하므로
    refresh 시 같은 id 로 호출하면 기존 엔티티 sources 에 누적된다(연결 보존).
    추출 실패 시 (False, error). 성공 시 (True, None).
    """
    from ..config import get_settings
    from ..extract.classifier import classify_paper

    settings = get_settings()
    eff = effort
    if eff is None:
        if doc.source_type == "pdf":
            # 무료 어댑터 우선 최저 effort('low')로 논문 여부 1차 판정
            is_paper, reason = classify_paper(doc, settings)
            if doc.meta is None:
                doc.meta = {}
            doc.meta["paper_classification"] = {
                "is_paper": is_paper,
                "reason": reason,
                "raw_chars": len(doc.raw_text or ""),
            }
            if is_paper and len(doc.raw_text or "") >= settings.pdf_paper_threshold_chars:
                eff = settings.pdf_paper_effort or "high"
            else:
                eff = settings.pdf_default_effort or getattr(provider, "effort", "medium")
        else:
            eff = getattr(provider, "effort", "medium")

    if doc.meta is None:
        doc.meta = {}
    doc.meta["applied_effort"] = eff
    if full_content:
        doc.meta["full_content"] = True
    report.effort = eff
    if full_content:
        report.full_content = True
    if doc.meta:
        if "pdf_parser_fallback" in doc.meta:
            report.pdf_parser_fallback = bool(doc.meta.get("pdf_parser_fallback"))
        if "pdf_parser_fallback_reason" in doc.meta:
            report.pdf_parser_fallback_reason = doc.meta.get("pdf_parser_fallback_reason")

    if getattr(doc, "id", None):
        try:
            dbm.update_document_meta(conn, doc.id, doc.meta)
        except Exception:
            pass

    if on_progress:
        pname = getattr(provider, "name", "?")
        on_progress("LLM 요약 및 엔티티/관계 구조화 추출", f"provider={pname}, effort={eff}")

    try:
        try:
            result = provider.extract(doc, None, effort=eff)  # type: ignore[arg-type]
        except TypeError:
            result = provider.extract(doc, None)  # type: ignore[arg-type]
    except Exception as e:  # noqa: BLE001
        return False, f"extract failed: {e}"
    report.summary = result.summary

    # [LLM tier] 모델 원본 출력 보관(후처리만 바꿔도 재호출 없이 재생).
    if result.raw_response:
        dbm.log_extraction(
            conn, document_id=doc.id, provider=getattr(provider, "name", "?"),
            model=result.model, prompt_version=result.prompt_version,
            raw_response=result.raw_response,
        )

    # 가독 렌더(detail) — 구조화 추출과 독립된 별도 LLM 호출. 그래프와 무관해
    # 실패해도 적재를 깨지 않는다(조용히 건너뜀). refresh/reextract 도 같은 경로라 갱신됨.
    if on_progress:
        on_progress("LLM 가독 상세(detail) 렌더링 생성", f"format={format or '기본'}, effort={eff}")

    ensure_document_detail(
        conn, provider, doc, force=True, format=format, directive=directive, effort=eff, full_content=full_content
    )

    _judge_method = getattr(provider, "judge_same_entity", None)

    def _judge_fn(nm, et, obs, cand):
        if _judge_method is None:
            return False
        from ..extract.provider import MergeCandidate

        return _judge_method(MergeCandidate(
            new_name=nm, new_type=et, new_observations=obs,
            cand_name=cand.name, cand_type=cand.type,
            cand_aliases=cand.aliases, cand_observations=cand.observations,
        ))

    name_to_id: dict[str, str] = {}
    touched_entities = []
    total_entities = len(result.entities)

    if on_progress:
        on_progress("지식 그래프 엔티티 해소/병합", f"추출된 엔티티 {total_entities}개")

    for idx_e, ee in enumerate(result.entities, 1):
        etype, prov = classify_entity_type(ee.type)
        if ee.proposed_type:
            dbm.log_proposal(conn, "entity_type", ee.proposed_type,
                             context=ee.name, document_id=doc.id)
            report.proposals += 1

        def _embed(ee=ee):
            try:
                return provider.embed(ee.name + " — " + " ".join(ee.observations)[:500])
            except Exception:  # noqa: BLE001
                return None

        def _on_judge_candidate(name1: str, name2: str):
            if on_progress:
                on_progress("엔티티 LLM 동일체 판정", f"[{idx_e}/{total_entities}] '{name1}' ↔ '{name2}'")

        ent, created = resolve_or_create(
            conn, vstore,
            name=ee.name, etype=etype, aliases=ee.aliases,
            observations=ee.observations, document_id=doc.id,
            embed_fn=_embed, judge_fn=_judge_fn, provisional=prov,
            on_judge=_on_judge_candidate,
        )
        name_to_id[ee.name] = ent.id
        touched_entities.append(ent)
        if created:
            report.entities_created += 1
            report.new_entity_names.append(ent.name)
        else:
            report.entities_linked += 1
            report.linked_entity_names.append(ent.name)

    if on_progress:
        on_progress("관계(Relation) 검증 및 적재", f"총 {len(result.relations)}개 관계")

    for er in result.relations:
        src_id = name_to_id.get(er.source)
        tgt_id = name_to_id.get(er.target)
        if not src_id or not tgt_id or src_id == tgt_id:
            report.relations_rejected += 1
            continue
        rtype, _ = classify_relation_type(er.type)
        if er.proposed_type:
            dbm.log_proposal(conn, "relation_type", er.proposed_type,
                             context=f"{er.source}->{er.target}", document_id=doc.id)
            report.proposals += 1
        src = dbm.get_entity(conn, src_id)
        tgt = dbm.get_entity(conn, tgt_id)
        vr = validate_relation(rtype, src.type if src else "", tgt.type if tgt else "")
        if not vr.ok:
            report.relations_rejected += 1
            continue
        rel = Relation(type=rtype, source_id=src_id, target_id=tgt_id,
                       sources=[doc.id], provisional=vr.provisional)
        before = dbm.counts(conn)["relations"]
        dbm.upsert_relation(conn, rel)
        if dbm.counts(conn)["relations"] > before:
            report.relations_added += 1

    if vault_dir is not None and touched_entities:
        if on_progress:
            on_progress("Vault 마크다운 동기화", f"{len(touched_entities)}개 노드")
        neighbor_ids = set()
        for ent in touched_entities:
            for r in dbm.neighbors(conn, ent.id):
                neighbor_ids.add(r.source_id)
                neighbor_ids.add(r.target_id)
        export_set = {e.id: e for e in touched_entities}
        for nid in neighbor_ids:
            if nid not in export_set:
                ne = dbm.get_entity(conn, nid)
                if ne:
                    export_set[nid] = ne
        export_entities(conn, vault_dir, list(export_set.values()))

    return True, None


def merge_source_into_document(
    conn: sqlite3.Connection,
    provider: Provider,
    vstore: VectorStore,
    parent: Document,
    child: Document,
    *,
    vault_dir: Path | None = None,
    data_dir: Path | None = None,
    format: str | None = None,
) -> dict:
    """[1홉 병합, ONEHOP_MERGE_DESIGN.md] 같은 주제의 부가 출처(child)를 parent 문서에
    흡수 — 새 Document/expand_queue 항목을 만드는 대신 parent.raw_text 뒤에 별도 출처
    섹션으로 append 하고 **같은 doc.id** 로 재추출한다(엔티티는 resolve_or_create 가 병합된
    본문 기준으로 기존 노드에 관찰을 누적, render_detail 도 합쳐진 상세로 재생성돼 실제로
    더 풍부한 글이 된다).

    저장은 원문 보존 협약대로 자르지 않는다 — LLM 프롬프트 투입량 상한(2배)은
    `_doc_to_prompt`(gemini_provider.py) 쪽에서 doc.meta.extra_sources 유무로 자동 분기.

    실패(주로 provider.extract 의 rate-limit/quota)는 **스냅샷→복원**으로 병합 시도 이전
    상태로 되돌린다(§3.3a — db.py 각 함수가 즉시 commit 하는 구조라 진짜 SQL 트랜잭션은
    이번 범위에서 무리, 가벼운 대안으로 결정). 엔티티/관계 루프 도중 실패해 일부가 이미
    커밋된 경우까지는 못 되돌린다 — 이건 ingest()/refresh_document() 도 이미 안고 있는
    기존 리스크와 동급이라 이번 범위에서 별도로 고치지 않는다.

    반환: {"merged": bool, "document_id"?, "report"?: IngestReport, "error"?: str}
    """
    original = dbm.get_document_row(conn, parent.id)
    if original is None:
        return {"merged": False, "error": "parent document not found"}

    # 병합 전 dedup 체크 — child 가 이미 다른 독립 문서로 존재하면(canonicalize_url 이 못
    # 거른 트래킹 파라미터 변형 등) 병합하지 않는다. ingest() 의 dedup①②③(pipeline.py:127,
    # 137, 166)과 동일한 우선순위, 대상만 parent 제외.
    existing_id = (
        dbm.find_document_by_hash(conn, child.content_hash)
        or dbm.find_document_by_canonical_url(conn, child.canonical_url)
    )
    if existing_id is None:
        near = dbm.near_duplicate_document(conn, child, exclude_id=parent.id)
        existing_id = near[0] if near else None
    if existing_id and existing_id != parent.id:
        return {"merged": False,
                "error": f"duplicate: already exists as document {existing_id}"}

    original_meta = json.loads(original["meta"] or "{}")

    extra_sources = list(original_meta.get("extra_sources") or [])
    extra_sources.append({
        "url": child.url, "canonical_url": child.canonical_url,
        "title": child.title, "source_type": child.source_type,
        "added_at": time.time(),
    })
    header = f"\n\n---\n[추가 출처: {child.title or child.url}]\n{child.url or ''}\n\n"
    merged_text = (parent.raw_text or "") + header + (child.raw_text or "")
    new_meta = {**original_meta, "extra_sources": extra_sources}

    from .normalize import content_hash as _content_hash

    try:
        dbm.update_document_content(
            conn, parent.id, title=parent.title, raw_text=merged_text,
            content_hash=_content_hash(merged_text), fetched_at=time.time(),
            meta=new_meta)
        parent.raw_text = merged_text
        parent.meta = new_meta
        if data_dir is not None:
            try:
                from ..store.raw import save_artifact

                save_artifact(data_dir, parent.id, merged_text)
            except Exception:  # noqa: BLE001
                pass
        report = IngestReport(document_id=parent.id, title=parent.title, updated=True)
        ok, err = extract_resolve_store(
            conn, provider, vstore, parent, report, vault_dir=vault_dir, format=format)
        if not ok:
            raise RuntimeError(err)
        return {"merged": True, "document_id": parent.id, "report": report}
    except Exception as e:  # noqa: BLE001 — 실패 시 병합 이전 상태로 복원(스냅샷 롤백)
        dbm.update_document_content(
            conn, parent.id, title=original["title"], raw_text=original["raw_text"],
            content_hash=original["content_hash"], fetched_at=original["fetched_at"],
            meta=original_meta)
        return {"merged": False, "error": str(e)}


def ensure_document_detail(
    conn: sqlite3.Connection,
    provider: Provider,
    doc: Document,
    *,
    force: bool = False,
    format: str | None = None,
    directive: str | None = None,
    effort: str | None = None,
    full_content: bool = False,
) -> bool:
    """문서의 가독 렌더(detail)를 생성·저장. **그래프와 독립**(별도 LLM 호출).

    신규 적재(extract_resolve_store)와 기존 문서 백필이 공유하는 단일 경로. detail 컬럼만
    채우므로 엔티티/관계를 건드리지 않는다 → reset_graph/rebuild 없이 백필 가능(advisor).
    이미 있으면(force=False) 건너뛰고, 생성 실패는 조용히 False(적재 실패로 번지지 않음).
    """
    if full_content:
        if doc.meta is None:
            doc.meta = {}
        doc.meta["full_content"] = True

    fmt = format or (doc.meta or {}).get("format") or (doc.meta or {}).get("render_format")
    if not fmt:
        from ..config import get_settings

        fmt = get_settings().render_format
    fmt = (fmt or "md").strip().lower()
    if fmt in ("asciidoc", "adoc"):
        fmt = "adoc"
    else:
        fmt = "md"

    dir_val = directive if directive is not None else (doc.meta or {}).get("directive")

    if not force:
        existing_detail = dbm.get_document_detail(conn, doc.id)
        existing_fmt = dbm.get_document_detail_format(conn, doc.id)
        existing_dir = (doc.meta or {}).get("directive") or dbm.get_document_directive(conn, doc.id)
        if existing_detail and existing_fmt == fmt and existing_dir == dir_val:
            return False

    render = getattr(provider, "render_detail", None)
    if render is None:
        return False

    eff = effort
    if eff is None:
        if doc.source_type == "pdf":
            from ..config import get_settings
            from ..extract.classifier import classify_paper

            settings = get_settings()
            classification = (doc.meta or {}).get("paper_classification")
            if classification and isinstance(classification, dict):
                is_paper = classification.get("is_paper", False)
            else:
                is_paper, reason = classify_paper(doc, settings)
                if doc.meta is None:
                    doc.meta = {}
                doc.meta["paper_classification"] = {
                    "is_paper": is_paper,
                    "reason": reason,
                    "raw_chars": len(doc.raw_text or ""),
                }
            if is_paper and len(doc.raw_text or "") >= settings.pdf_paper_threshold_chars:
                eff = settings.pdf_paper_effort or "high"
            else:
                eff = settings.pdf_default_effort or getattr(provider, "effort", "medium")
        else:
            eff = getattr(provider, "effort", "medium")

    try:
        try:
            text = render(doc, format=fmt, directive=dir_val, effort=eff)
        except TypeError:
            try:
                text = render(doc, format=fmt, directive=dir_val)
            except TypeError:
                try:
                    text = render(doc, format=fmt)
                except TypeError:
                    text = render(doc)
    except Exception as e:  # noqa: BLE001
        import logging

        logging.getLogger("claire.pipeline").warning(
            "ensure_document_detail failed for doc_id=%s: %s", doc.id, e
        )
        return False
    if text and text.strip():
        dbm.set_document_detail(conn, doc.id, text.strip(), format=fmt)
        if dir_val is not None:
            if doc.meta is None:
                doc.meta = {}
            doc.meta["directive"] = dir_val
            dbm.set_document_directive(conn, doc.id, dir_val)
        return True
    return False


def ensure_watch_classification(
    conn: sqlite3.Connection, provider: Provider, doc: Document
) -> bool:
    """[주기 크롤링] 변하는 콘텐츠(벤치/순위 등)인지 LLM 판단 → watch 설정. 비필수(별도 호출).

    신규 1차 적재에만 호출(비용 통제 — 호출측 source 게이트). rate limit 등 실패는 조용히
    False(적재 막지 않음 — watch 미판단으로 남고 나중에 수동/재판단 가능)."""
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
    dbm.set_document_watch(conn, doc.id, enabled=bool(res.get("watch")),
                           interval=interval, reason=reason)
    return True


def _guess_kind(payload: str) -> str:
    """inbound payload 종류 추정(raw_inbox.kind 용)."""
    t = (payload or "").strip().lower()
    if t.startswith("http://") or t.startswith("https://"):
        return "url"
    if t.startswith("file://"):
        return "file"
    return "text"
