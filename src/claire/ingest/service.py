"""IngestService — 적재의 단일 진입점(모듈화).

텔레그램 DM · 로컬 inject API · CLI 가 모두 이 클래스를 통해 적재한다 =
사용자가 말한 "내가 DM 던지는 것과 동일한 통로". provider 는 1회 생성해 보유하고,
DB 커넥션은 호출마다 짧게 연다(WAL + busy_timeout 로 다중 프로세스 안전).
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from ..config import Settings
from ..extract.provider import get_provider
from ..retrieval.query import SearchMode
from ..store import db as dbm
from ..store.vectors import make_vector_store
from .pipeline import (
    IngestReport,
    extract_resolve_store,
    ingest,
    merge_source_into_document,
)
from .router import fetch as default_fetch


@contextmanager
def _item_context(
    reporter: Any | None,
    on_progress: Callable[[str, str], None] | None,
    index: int,
    item_id: str,
    title: str = "",
    url: str = "",
):
    """ProgressReporter 또는 on_progress 콜백을 공통 인터페이스로 래핑."""
    if reporter is not None and hasattr(reporter, "item"):
        with reporter.item(index, item_id, title=title, url=url) as step_cb:
            yield step_cb
    else:
        def step_cb(stage: str, detail: str = "") -> None:
            if on_progress is not None:
                try:
                    on_progress(stage, detail)
                except Exception:
                    pass

        yield step_cb


class IngestService:
    def __init__(self, settings: Settings):
        self.s = settings
        self.provider = get_provider(settings)

    def ingest(
        self,
        payload: str,
        *,
        source: str,
        expand_max: int | None = None,
        user_id: int | None = None,
        chat_id: int | None = None,
        inbox_kind: str | None = None,
        file_ref: str | None = None,
        file_name: str | None = None,
        inbox_id: int | None = None,
        prefetched: Document | None = None,
        format: str | None = None,
        directive: str | None = None,
        effort: str | None = None,
    ) -> IngestReport:
        """단건 적재. (블로킹 — 호출측에서 스레드 오프로드).

        inbox_id 가 주어지면 새 raw_inbox 행을 만들지 않고 기존 행을 재사용(자동복구용).
        prefetched 가 주어지면 fetch 를 건너뛰고 그 Document 로 적재(1홉 확장의 중복 fetch 방지).
        """
        conn = dbm.connect(self.s.db_file)
        dbm.init_db(conn)
        vstore = make_vector_store(conn, self.s.vector_backend)
        em = self.s.expand_max if expand_max is None else expand_max
        # 1홉 자동확장 enqueue 게이트: 사용자 설정 ON + 1차 적재(자식/복구/갱신 재적재 제외).
        auto = (self.s.auto_expand and em > 0
                and not source.startswith(("onehop", "recover", "replay", "refresh")))
        fmt = format or self.s.render_format
        try:
            return ingest(
                payload, conn=conn, provider=self.provider, vstore=vstore,
                vault_dir=self.s.vault_dir, data_dir=self.s.data_dir,
                expand_max=em, source=source, user_id=user_id, chat_id=chat_id,
                inbox_kind=inbox_kind, file_ref=file_ref, file_name=file_name,
                inbox_id=inbox_id, prefetched=prefetched, auto_expand=auto,
                format=fmt, directive=directive, effort=effort,
            )
        finally:
            conn.close()

    def expand_document(self, document_id: str, *, limit: int | None = None) -> dict:
        """[1홉 자동확장] 부모 문서의 링크를 LLM 이 선별→fetch→판정→통과 시 적재.

        파고들지(select_followups)·쌓을지(judge_research 게이트) 모두 LLM 결정.
        게이트 통과 후 same_subject(judge_research 확장 필드, ONEHOP_MERGE_DESIGN.md §3.1)로
        한 번 더 갈린다: True 면 새 문서를 안 만들고 부모에 흡수(병합, §3.2~3.3),
        False 면 기존처럼 독립 문서로 적재(부모 글이 언급한 별개 소재인 경우 — 정보 보존).
        깊이는 1 고정: 독립 적재 자식은 source='onehop:*' + expand_max=0 → 재확장 안 됨.
        병합은 애초에 새 Document/expand_queue 항목을 안 만들어 재귀 위험이 이중으로 없음.
        반환: {document_id, candidates, selected, stored, merged, skipped, followed[...]}
        """
        from ..expand.follow import build_candidates, build_parent_context, passes_gate

        cap = self.s.expand_max if limit is None else limit
        conn = dbm.connect(self.s.db_file)
        dbm.init_db(conn)
        try:
            doc = dbm.get_document(conn, document_id)
            if doc is None:
                return {"document_id": document_id, "error": "document not found"}
            context = build_parent_context(conn, doc)
            candidates = build_candidates(conn, doc)
        finally:
            conn.close()

        result = {"document_id": document_id, "candidates": len(candidates),
                  "selected": 0, "stored": 0, "merged": 0, "skipped": 0, "followed": []}
        if not candidates:
            return result

        # 1) 파고들지 = LLM. select 없으면(구 provider) 확장 안 함(보수적).
        select = getattr(self.provider, "select_followups", None)
        idxs = select(context, candidates) if select else []
        chosen = [candidates[i] for i in idxs][:cap]
        result["selected"] = len(chosen)

        judge = getattr(self.provider, "judge_research", None)
        for c in chosen:
            url = c["url"]
            try:
                child = default_fetch(url)  # 판정용 1회 fetch (적재 시 prefetched 재사용)
            except Exception as e:  # noqa: BLE001
                result["skipped"] += 1
                result["followed"].append({"url": url, "stored": False, "error": str(e)})
                continue
            # 2) 쌓을지 = LLM(research 게이트 재사용). judge 없으면 통과로 간주.
            rel, qual, same_subject = 1.0, 1.0, False
            if judge:
                report = f"{child.title or ''}\n{(child.raw_text or '')[:4000]}"
                j = judge(doc.title or "", context, report)
                rel = float(j.get("relevance") or 0.0)
                qual = float(j.get("quality") or 0.0)
                same_subject = bool(j.get("same_subject", False))
            if not passes_gate(rel, qual):
                result["skipped"] += 1
                result["followed"].append({"url": url, "stored": False,
                                           "relevance": rel, "quality": qual})
                continue

            if same_subject:
                # 3a) 같은 주제의 부가 출처 = 새 문서 대신 부모에 흡수(§3.2~3.3).
                conn2 = dbm.connect(self.s.db_file)
                dbm.init_db(conn2)
                try:
                    inbox_id = dbm.log_inbox(
                        conn2, source=f"onehop:{document_id}", payload=url, kind="url")
                    vstore = make_vector_store(conn2, self.s.vector_backend)
                    parent_full = dbm.get_document(conn2, document_id)
                    m = merge_source_into_document(
                        conn2, self.provider, vstore, parent_full, child,
                        vault_dir=self.s.vault_dir, data_dir=self.s.data_dir,
                        format=self.s.render_format)
                    if m.get("merged"):
                        dbm.update_inbox(conn2, inbox_id, status="done",
                                         document_id=document_id)
                    else:
                        dbm.update_inbox(conn2, inbox_id, status="error",
                                         document_id=document_id, error=m.get("error"))
                finally:
                    conn2.close()
                merged = bool(m.get("merged"))
                if merged:
                    result["merged"] += 1
                else:
                    result["skipped"] += 1
                result["followed"].append({"url": url, "stored": False, "merged": merged,
                                           "title": child.title, "relevance": rel,
                                           "quality": qual, "error": m.get("error")})
                continue

            # 3b) 별개 소재 = 기존처럼 독립 문서로 적재(정보 보존).
            rep = self.ingest(url, source=f"onehop:{document_id}", expand_max=0,
                              prefetched=child)
            stored = rep.error is None and not rep.duplicate
            if stored:
                result["stored"] += 1
            result["followed"].append({"url": url, "stored": stored, "merged": False,
                                       "title": rep.title, "duplicate": rep.duplicate,
                                       "relevance": rel, "quality": qual,
                                       "error": rep.error})
        return result

    def run_expand_queue(
        self,
        *,
        limit: int = 0,
        reporter: Any | None = None,
        on_progress: Callable[[str, str], None] | None = None,
    ) -> list[dict]:
        """[1홉 자동확장] 대기열의 pending 문서를 처리(expand-loop 데몬이 호출).

        각 문서를 expand_document 로 확장하고 done/error 로 마킹. 반환: 처리 요약 목록.
        """
        import json as _json

        conn = dbm.connect(self.s.db_file)
        dbm.init_db(conn)
        rows = dbm.pending_expand(conn, limit=limit)
        conn.close()

        out: list[dict] = []
        for idx, row in enumerate(rows, 1):
            did = row["document_id"]
            with _item_context(reporter, on_progress, idx, f"expand#{row['id']}:{did}", title=f"1홉 확장 ({did})") as step_cb:
                step_cb("1홉 자동확장 링크 선별 및 판정 중...")
                try:
                    res = self.expand_document(did)
                except Exception as e:  # noqa: BLE001
                    conn2 = dbm.connect(self.s.db_file)
                    try:
                        dbm.update_expand(conn2, row["id"], status="error", error=str(e))
                    finally:
                        conn2.close()
                    out.append({"document_id": did, "error": str(e)})
                    continue
                status = "error" if res.get("error") else "done"
                conn2 = dbm.connect(self.s.db_file)
                try:
                    dbm.update_expand(conn2, row["id"], status=status,
                                      error=res.get("error"), result=_json.dumps(res)[:2000])
                finally:
                    conn2.close()
                out.append(res)
        return out

    def refresh_document(
        self,
        document_id: str,
        payload: str,
        *,
        format: str | None = None,
        on_progress: Callable[[str, str], None] | None = None,
    ) -> dict:
        """[복원] 한 문서를 원본 payload 로 재fetch→재추출하여 in-place 갱신.

        - 새 content_hash 가 기존과 같으면 'nochange'(내용 동일 → 재추출 생략).
        - 다르면 documents 행을 같은 id 로 갱신(엔티티 sources 연결 보존) + 새 artifact
          보관 + 재추출/해소/관계/vault. 새로 잡힌 엔티티는 기존 그래프에 누적된다.
        반환: {status, document_id, old_len, new_len, ...}
        """
        if not document_id:
            return {"status": "error", "document_id": document_id,
                    "error": "document_id required"}
        conn = dbm.connect(self.s.db_file)
        dbm.init_db(conn)
        vstore = make_vector_store(conn, self.s.vector_backend)
        try:
            old = dbm.get_document_row(conn, document_id)
            if old is None:
                return {"status": "error", "document_id": document_id,
                        "error": "document not found"}
            old_len = len(old["raw_text"]) if old["raw_text"] else 0

            if on_progress:
                on_progress("원문 재수집 (Refetching from payload)...", payload[:40])

            try:
                doc = default_fetch(payload)
            except Exception as e:  # noqa: BLE001
                return {"status": "error", "document_id": document_id, "error": str(e)}

            doc.id = document_id
            if doc.content_hash == old["content_hash"]:
                # 본문은 그대로지만, 재fetch 로 새로 수집된 본문 이미지를 로컬로 내려받아
                # 보존(외부 사이트/링크 삭제 대비, 사용자 요구)하고 meta 에 반영한 뒤
                # detail(마크다운 가독 렌더)을 재생성한다 — 이미지/강조 백필 경로.
                # 그래프(엔티티)는 건드리지 않음(비파괴).
                from .pipeline import _download_doc_images, ensure_document_detail

                _download_doc_images(conn, doc, self.s.data_dir)
                imgs = (doc.meta or {}).get("images")
                fmt = format or self.s.render_format
                detail_updated = ensure_document_detail(
                    conn, self.provider, doc, force=True, format=fmt)
                return {"status": "nochange", "document_id": document_id,
                        "old_len": old_len, "new_len": len(doc.raw_text),
                        "detail_updated": detail_updated,
                        "images": len(imgs or [])}

            # watch 대상이면 변경 '전' 원문을 스냅샷으로 보존(시계열 — 벤치/순위 추세 살림,
            # 데이터 보존 협약) + 내용이 바뀌었으니 다시 봐야 함 → unseen. 그래프(엔티티)는
            # 아래 in-place 갱신으로 최신만 유지, 과거 상태는 스냅샷에만 남는다.
            if old["watch_enabled"] == 1:
                import time as _time
                dbm.save_document_snapshot(
                    conn, document_id, captured_at=_time.time(),
                    content_hash=old["content_hash"], title=old["title"],
                    raw_text=old["raw_text"])
                dbm.set_document_seen(conn, document_id, seen=False)
            # 내용이 바뀌었으니 새 이미지 후보도 로컬로 내려받아 doc.meta 에 반영(사용자 요구).
            from .pipeline import _download_doc_images

            _download_doc_images(conn, doc, self.s.data_dir)
            # 같은 id 로 갱신(신규가 아니라 복원이므로 sources 연결 유지). meta(이미지 포함) 보존.
            dbm.update_document_content(
                conn, document_id, title=doc.title, raw_text=doc.raw_text,
                content_hash=doc.content_hash, fetched_at=doc.fetched_at,
                source_type=doc.source_type, partial=doc.partial, meta=doc.meta)
            try:
                from ..store.raw import save_artifact

                save_artifact(self.s.data_dir, document_id, doc.raw_text)
            except Exception:  # noqa: BLE001
                pass

            fmt = format or self.s.render_format
            report = IngestReport(document_id=document_id)
            ok, err = extract_resolve_store(
                conn, self.provider, vstore, doc, report, vault_dir=self.s.vault_dir, format=fmt,
                on_progress=on_progress)
            if not ok:
                return {"status": "error", "document_id": document_id, "error": err}
            return {"status": "done", "document_id": document_id,
                    "old_len": old_len, "new_len": len(doc.raw_text),
                    "entities_created": report.entities_created,
                    "entities_linked": report.entities_linked}
        finally:
            conn.close()

    def run_refresh_queue(
        self,
        *,
        limit: int = 0,
        reporter: Any | None = None,
        on_progress: Callable[[str, str], None] | None = None,
    ) -> list[dict]:
        """대기열의 pending 항목을 처리. 각 결과로 큐 상태 갱신. 결과 리스트 반환."""
        conn = dbm.connect(self.s.db_file)
        dbm.init_db(conn)
        rows = dbm.pending_refresh(conn, limit=limit)
        conn.close()
        out = []
        for idx, row in enumerate(rows, 1):
            did = row["document_id"]
            payload = row["payload"]
            with _item_context(reporter, on_progress, idx, f"refresh#{row['id']}:{did}", title=f"갱신 ({did})", url=payload) as step_cb:
                res = self.refresh_document(did, payload, on_progress=step_cb)
                conn2 = dbm.connect(self.s.db_file)
                try:
                    st = res["status"] if res["status"] in ("done", "nochange") else "error"
                    dbm.update_refresh(conn2, row["id"], status=st, error=res.get("error"))
                finally:
                    conn2.close()
                out.append({**res, "queue_id": row["id"]})
        return out

    def enqueue_due_watch(self, *, limit: int = 0) -> int:
        """[주기 크롤링] 재크롤할 때가 된 watch 문서를 refresh 큐에 등록(reason='watch').

        watch_due = enabled=1 AND (last_watched_at NULL 또는 now-last >= interval). 등록 후
        last_watched_at=now 로 갱신해 다음 due 를 미룬다(큐 대기 중 중복은 enqueue_refresh 의
        document_id UNIQUE 가 막음). 처리(refresh_document)는 run_refresh_queue 가 하며,
        watch 문서가 변했으면 거기서 스냅샷 보존 + unseen. 신규 등록 건수 반환."""
        import time

        conn = dbm.connect(self.s.db_file)
        dbm.init_db(conn)
        try:
            now = time.time()
            default_iv = self.s.watch_interval_days * 86400
            rows = dbm.watch_due_documents(conn, now, default_interval=default_iv, limit=limit)
            n = 0
            for r in rows:
                if dbm.enqueue_refresh(conn, document_id=r["id"], payload=r["url"],
                                       reason="watch"):
                    n += 1
                dbm.mark_document_watched(conn, r["id"], now)
            return n
        finally:
            conn.close()

    def mark_thin_for_refresh(
        self, *, max_len: int = 300, host: str | None = None, reason: str = "thin",
        include_partial: bool = False,
    ) -> int:
        """본문 빈약 문서를 갱신 대상으로 등록. 등록(신규+되살림) 건수 반환.

        include_partial=True 면 구버전 partial 노드('x.com post' 등)도 재fetch 대상.
        """
        conn = dbm.connect(self.s.db_file)
        dbm.init_db(conn)
        try:
            rows = dbm.thin_documents(
                conn, max_len=max_len, host=host, include_partial=include_partial)
            n = 0
            for r in rows:
                payload = r["url"]
                if not payload:
                    continue  # url 없는 순수 텍스트는 재fetch 불가 → 스킵
                dbm.enqueue_refresh(conn, document_id=r["id"], payload=payload, reason=reason)
                n += 1
            return n
        finally:
            conn.close()

    def mark_all_for_image_backfill(self, *, limit: int = 0) -> int:
        """본문 이미지가 없는(이미지 수집 이전 적재) 문서를 재fetch 대상으로 등록.

        기존 claire_refresh 컨테이너(주기·소량 처리)가 큐를 **며칠에 걸쳐 천천히** 드레인
        하며 각 문서를 재fetch → 이미지 수집 + detail(마크다운/강조) 재생성한다. 본문이
        안 바뀐 문서는 그래프 불변(비파괴), 바뀐 문서만 재추출(refresh 본래 동작). 등록 수 반환.
        """
        conn = dbm.connect(self.s.db_file)
        dbm.init_db(conn)
        try:
            ids = dbm.documents_missing_images(conn, limit=limit)
            n = 0
            for did in ids:
                row = dbm.get_document_row(conn, did)
                if not row or not row["url"]:
                    continue
                dbm.enqueue_refresh(conn, document_id=did, payload=row["url"],
                                    reason="image-backfill")
                n += 1
            return n
        finally:
            conn.close()

    def reextract_all(
        self,
        *,
        rebuild: bool = True,
        limit: int = 0,
        format: str | None = None,
        tables_only: bool = False,
        reporter: Any | None = None,
        on_progress: Callable[[str, str], None] | None = None,
    ) -> dict:
        """저장된 raw_text 로 전체 문서를 재추출(프롬프트 변경 반영 — 예: 한글화, 표 보존).

        tables_only=True: raw_text 에 표(Table)가 포함된 문서만 선별하여 재추출.
        rebuild=True: 먼저 그래프(엔티티/관계/임베딩/추출)를 비우고 처음부터 재구축한다.
        _merge 는 observations 를 *추가*하므로, 비우지 않으면 기존(영문)+신규(한글)가 섞인다.
        documents·raw_inbox·artifact 는 보존하므로 입력은 그대로. **파괴적**이므로
        호출자가 서비스 정지와 복구 계획을 책임진다. 문서당 Gemini 1회(quota).
        오래된 문서부터(원래 적재 순서에 가깝게) 처리해 first-seen canonical 수렴을
        원래와 맞춘다.
        """
        conn = dbm.connect(self.s.db_file)
        dbm.init_db(conn)
        vstore = make_vector_store(conn, self.s.vector_backend)
        fmt = format or self.s.render_format
        try:
            if tables_only:
                table_docs = dbm.documents_with_tables(conn, limit=limit, check_detail=False)
                ids = [d["id"] for d in table_docs][::-1]
            else:
                rows = dbm.documents_timeline(conn, limit or 1000000)
                ids = [r["id"] for r in rows][::-1]  # 오래된 것부터
                if limit:
                    ids = ids[:limit]
            if rebuild:
                dbm.reset_graph(conn)
            out = {"docs": 0, "ok": 0, "failed": 0, "errors": []}
            for idx, did in enumerate(ids, 1):
                doc = dbm.get_document(conn, did)
                if doc is None:
                    continue
                out["docs"] += 1
                report = IngestReport(document_id=did)
                with _item_context(reporter, on_progress, idx, did, title=doc.title or "", url=doc.canonical_url or doc.url or "") as step_cb:
                    ok, err = extract_resolve_store(
                        conn, self.provider, vstore, doc, report,
                        vault_dir=self.s.vault_dir, format=fmt, on_progress=step_cb)
                    if ok:
                        out["ok"] += 1
                    else:
                        out["failed"] += 1
                        out["errors"].append({"document_id": did, "error": err})
            return out
        finally:
            conn.close()

    def backfill_details(
        self,
        *,
        limit: int = 0,
        force: bool = False,
        format: str | None = None,
        directive: str | None = None,
        tables_only: bool = False,
        reporter: Any | None = None,
        on_progress: Callable[[str, str], None] | None = None,
    ) -> dict:
        """detail(한국어 가독 렌더링)이 없거나 포맷이 다른 기존 문서를 채운다 — **비파괴적**.

        tables_only=True: raw_text 또는 detail 에 표(Table)가 포함된 문서만 선별 백필.
        그래프(엔티티/관계)를 건드리지 않고 documents.detail 컬럼만 채우므로 reextract 의
        reset_graph/rebuild 가 불필요(advisor). 문서당 Gemini 1회(quota). force=True 면
        이미 있는 detail 도 재생성. 반환: {docs, ok, skipped}."""
        from .pipeline import ensure_document_detail

        conn = dbm.connect(self.s.db_file)
        dbm.init_db(conn)
        fmt = format or self.s.render_format
        try:
            if tables_only:
                table_docs = dbm.documents_with_tables(conn, limit=limit, check_detail=True)
                ids = [d["id"] for d in table_docs]
            elif force:
                ids = [r["id"] for r in dbm.documents_timeline(conn, limit or 1000000)]
                if limit:
                    ids = ids[:limit]
            else:
                ids = dbm.documents_needing_detail_format(conn, fmt, limit)
            out = {"docs": len(ids), "ok": 0, "skipped": 0}
            for idx, did in enumerate(ids, 1):
                doc = dbm.get_document(conn, did)
                if doc is None:
                    out["skipped"] += 1
                    continue
                with _item_context(reporter, on_progress, idx, did, title=doc.title or "", url=doc.canonical_url or doc.url or "") as step_cb:
                    step_cb("가독 본문(detail) 렌더링 생성 중", f"format={fmt}")
                    if ensure_document_detail(
                        conn, self.provider, doc, force=force or tables_only, format=fmt, directive=directive
                    ):
                        out["ok"] += 1
                    else:
                        out["skipped"] += 1
            return out
        finally:
            conn.close()

    def backfill_summaries(
        self,
        *,
        limit: int = 0,
        reporter: Any | None = None,
        on_progress: Callable[[str, str], None] | None = None,
    ) -> dict:
        """extractions 에 요약이 비어있거나 누락된 기존 문서의 요약을 채운다 — **비파괴적**."""
        import json as _json

        from ..extract.prompts import PROMPT_VERSION, clean_plain_summary, is_corrupted_summary

        conn = dbm.connect(self.s.db_file)
        dbm.init_db(conn)
        try:
            rows = dbm.documents_timeline(conn, limit or 1000000)
            out = {"docs": len(rows), "filled": 0, "already_had": 0}
            for idx, r in enumerate(rows, 1):
                did = r["id"]
                doc = dbm.get_document(conn, did)
                title = doc.title if doc else ""
                with _item_context(reporter, on_progress, idx, did, title=title) as step_cb:
                    ext = conn.execute(
                        "SELECT id, raw_response, provider, model, prompt_version FROM extractions WHERE document_id=? ORDER BY id DESC LIMIT 1",
                        (did,),
                    ).fetchone()
                    existing_summary = None
                    if ext and ext["raw_response"]:
                        try:
                            existing_summary = _json.loads(ext["raw_response"]).get("summary")
                        except Exception:
                            existing_summary = None
                    if existing_summary and existing_summary.strip():
                        cleaned = clean_plain_summary(existing_summary)
                        if is_corrupted_summary(existing_summary) and cleaned != existing_summary.strip():
                            if ext:
                                try:
                                    data = _json.loads(ext["raw_response"])
                                except Exception:
                                    data = {}
                                data["summary"] = cleaned
                                new_raw = _json.dumps(data, ensure_ascii=False)
                                conn.execute("UPDATE extractions SET raw_response=? WHERE id=?", (new_raw, ext["id"]))
                                conn.commit()
                                step_cb("손상된 요약 정리 완료")
                                out["filled"] += 1
                                continue
                        out["already_had"] += 1
                        continue

                    # summary 가 없으면 fallback 추출
                    step_cb("요약 추출 및 보강 중")
                    summary = dbm.latest_extraction_summary(conn, did)
                    if not summary or not summary.strip():
                        if doc and doc.raw_text:
                            summary = clean_plain_summary(doc.raw_text) or ((doc.raw_text[:200] + "…") if len(doc.raw_text) > 200 else doc.raw_text)
                        elif doc and doc.title:
                            summary = f"{doc.title}에 관한 자료이다."
                        else:
                            summary = "(요약 없음)"
                    else:
                        summary = clean_plain_summary(summary)

                    if ext:
                        # 기존 extractions raw_response 갱신
                        try:
                            data = _json.loads(ext["raw_response"])
                        except Exception:
                            data = {}
                        data["summary"] = summary
                        new_raw = _json.dumps(data, ensure_ascii=False)
                        conn.execute("UPDATE extractions SET raw_response=? WHERE id=?", (new_raw, ext["id"]))
                        conn.commit()
                        out["filled"] += 1
                    else:
                        # extractions 레코드 신규 삽입
                        data = {"summary": summary, "key_claims": [], "entities": [], "relations": []}
                        new_raw = _json.dumps(data, ensure_ascii=False)
                        dbm.log_extraction(
                            conn, document_id=did, provider=getattr(self.provider, "name", "backfill"),
                            model=getattr(self.provider, "model", "fallback"), prompt_version=PROMPT_VERSION,
                            raw_response=new_raw,
                        )
                        out["filled"] += 1
            return out
        finally:
            conn.close()

    def regenerate_components(
        self,
        *,
        target: str | None = None,
        token: str | None = None,
        doc_id: str | None = None,
        summary: bool = False,
        detail: bool = False,
        graph: bool = False,
        all_components: bool = False,
        corrupted_summary: bool = False,
        corrupted_detail: bool = False,
        tables: bool = False,
        refetch: bool = False,
        force: bool = False,
        effort: str | None = None,
        format: str | None = None,
        directive: str | None = None,
        reporter: Any | None = None,
        on_progress: Callable[[str, str], None] | None = None,
    ) -> dict:
        """문서의 특정 컴포넌트(요약, 본문, 그래프 등)를 선별적으로 재생성(비파괴적/선택적 덮어쓰기).

        - target: URL (/p?s=token), 공유 토큰, 또는 document_id.
        - summary: True 면 요약만 LLM 재추출하여 extractions 갱신 (엔티티/관계 보존).
        - detail: True 면 한국어 가독 렌더링 본문만 재컴파일/재생성.
        - all_components: True 면 요약과 본문 모두 재생성.
        - corrupted_summary: True 면 전체 DB에서 ADOC/마크업 문법이 잔존한 요약만 자동 탐지.
        - tables: True 면 원문(raw_text) 또는 본문(detail)에 표가 포함된 문서만 자동 탐지하여 일괄 대상 지정.
        - refetch: True 면 원본 URL에서 웹 문서를 새로 스크랩하여 본문 갱신 후 재생성.
        - force: False(기본) 면 dry-run 진단만 수행하고 DB 변경 없음. True 면 실제 DB 덮어쓰기.
        - effort: LLM 사고/추론 레벨 (low, medium, high 등) 즉석 재정의.
        - directive: 가독 렌더링 본문 작성 초점(focus) 지침.
        """
        import json as _json
        import re as _re
        from urllib.parse import parse_qs, urlsplit

        from ..extract.prompts import PROMPT_VERSION, clean_plain_summary, is_corrupted_summary
        from ..extract.table_budget import extract_tables_from_text
        from .pipeline import ensure_document_detail

        # 컴포넌트 기본값 결정 (아무것도 지정 안 했으면 summary 를 기본 대상으로 간주)
        do_graph = graph or all_components or refetch
        do_summary = summary or all_components or do_graph
        do_detail = detail or all_components or do_graph
        if not do_summary and not do_detail and not do_graph:
            do_summary = True

        conn = dbm.connect(self.s.db_file)
        dbm.init_db(conn)

        def _is_corrupted_adoc(text: str | None) -> bool:
            return is_corrupted_summary(text)

        def _clean_summary(text: str | None) -> str:
            return clean_plain_summary(text)

        def _is_error_page(title: str | None, text: str | None) -> bool:
            t = (title or "").lower()
            txt = (text or "").lower()
            if any(p in t for p in ["privacy error", "your connection is not private", "net::err_"]):
                return True
            if any(p in txt for p in ["err_cert_authority_invalid", "err_cert_common_name_invalid", "net::err_cert_"]):
                return True
            return False

        try:
            # 1. 대상 document_id 목록 해소
            target_ids: list[str] = []
            resolved_token = token

            if target or token or doc_id:
                matched = dbm.resolve_document_targets(
                    conn,
                    target=target,
                    token=token,
                    doc_id=doc_id,
                )
                for m in matched:
                    if m["id"] not in target_ids:
                        target_ids.append(m["id"])

            # 대상이 특정되지 않고 tables, corrupted_summary 이거나 전체 스캔인 경우
            if tables:
                table_docs = dbm.documents_with_tables(conn, limit=0, check_detail=True)
                for td in table_docs:
                    did = td["id"]
                    if did not in target_ids:
                        target_ids.append(did)
            elif corrupted_summary or (not target_ids and not target and not token and not doc_id):
                rows = conn.execute(
                    "SELECT document_id, raw_response FROM extractions ORDER BY id DESC"
                ).fetchall()
                seen_docs: set[str] = set()
                for r in rows:
                    did = r["document_id"]
                    if did in seen_docs:
                        continue
                    seen_docs.add(did)
                    raw_resp = r["raw_response"] or ""
                    try:
                        curr_summ = _json.loads(raw_resp).get("summary", "")
                    except Exception:
                        curr_summ = ""
                    if _is_corrupted_adoc(curr_summ) and did not in target_ids:
                        target_ids.append(did)

            if not target_ids:
                error_msg = "No matching documents found."
                if tables:
                    error_msg = "No documents containing tables detected in database."
                elif not target and not token and not doc_id:
                    error_msg = "No corrupted summaries detected in database."
                return {
                    "dry_run": not force,
                    "count": 0,
                    "targets": [],
                    "error": error_msg,
                }

            # 2. 각 대상에 대해 진단 (Dry-run) 또는 실행 (Force)
            targets_info = []
            old_effort = getattr(self.provider, "effort", None)
            if effort:
                self.provider.effort = effort

            try:
                for idx, did in enumerate(target_ids, 1):
                    doc = dbm.get_document(conn, did)
                    if not doc:
                        continue

                    ext_row = conn.execute(
                        "SELECT raw_response FROM extractions WHERE document_id=? ORDER BY id DESC LIMIT 1",
                        (did,),
                    ).fetchone()
                    raw_summ = ""
                    if ext_row and ext_row["raw_response"]:
                        try:
                            raw_summ = _json.loads(ext_row["raw_response"]).get("summary", "")
                        except Exception:
                            raw_summ = ""
                    curr_summary = dbm.latest_extraction_summary(conn, did) or ""
                    curr_detail = dbm.get_document_detail(conn, did) or ""
                    curr_fmt = dbm.get_document_detail_format(conn, did) or ""
                    is_corrupted = _is_corrupted_adoc(raw_summ) or _is_corrupted_adoc(curr_summary)
                    is_err = _is_error_page(doc.title, doc.raw_text) or _is_error_page(None, curr_summary)

                    _, raw_tables = extract_tables_from_text(doc.raw_text or "")
                    _, detail_tables = extract_tables_from_text(curr_detail)
                    total_tables = len(raw_tables) + len(detail_tables)
                    table_preview = ""
                    if raw_tables:
                        table_preview = raw_tables[0].strip()
                    elif detail_tables:
                        table_preview = detail_tables[0].strip()

                    info: dict = {
                        "document_id": did,
                        "title": doc.title or "(제목 없음)",
                        "canonical_url": doc.canonical_url,
                        "current_summary": curr_summary,
                        "summary_corrupted": is_corrupted,
                        "is_error_page": is_err,
                        "current_detail_format": curr_fmt,
                        "total_tables": total_tables,
                        "raw_tables_count": len(raw_tables),
                        "detail_tables_count": len(detail_tables),
                        "table_preview": table_preview,
                        "actions": [],
                    }
                    if refetch:
                        info["actions"].append("refetch_content")
                    if do_graph:
                        info["actions"].append("extract_and_link_graph_nodes")
                    if do_summary and not do_graph:
                        info["actions"].append("regenerate_summary")
                    if do_detail and not do_graph:
                        info["actions"].append("regenerate_detail")

                    if not force:
                        targets_info.append(info)
                        continue

                    # --- 실제 실행 (Force Overwrite) ---
                    with _item_context(
                        reporter,
                        on_progress,
                        idx,
                        did,
                        title=doc.title or "(제목 없음)",
                        url=doc.canonical_url or doc.url or "",
                    ) as step_cb:
                        if refetch:
                            step_cb("원문 재수집 (Refetching from source)...", doc.url or doc.canonical_url or "")
                            fetch_payload = doc.url or doc.canonical_url
                            if fetch_payload:
                                from .pipeline import _download_doc_images
                                from ..store.raw import save_artifact

                                try:
                                    new_doc = default_fetch(fetch_payload)
                                    new_doc.id = did
                                    _download_doc_images(conn, new_doc, self.s.data_dir)
                                    dbm.update_document_content(
                                        conn,
                                        did,
                                        title=new_doc.title,
                                        raw_text=new_doc.raw_text,
                                        content_hash=new_doc.content_hash,
                                        fetched_at=new_doc.fetched_at,
                                        source_type=new_doc.source_type,
                                        partial=new_doc.partial,
                                        meta=new_doc.meta,
                                    )
                                    try:
                                        save_artifact(self.s.data_dir, did, new_doc.raw_text)
                                    except Exception:
                                        pass
                                    doc = new_doc
                                    info["refetched"] = True
                                    info["title"] = new_doc.title or "(제목 없음)"
                                    info["new_len"] = len(new_doc.raw_text)
                                except Exception as e:
                                    info["refetch_error"] = str(e)

                        target_fmt = format or self.s.render_format

                        if do_graph:
                            # 엔티티/노드 추출, 지식 그래프 해소/적재, 벡터 임베딩, 관계 적재, Vault 동기화
                            from ..store.vectors import make_vector_store
                            from .pipeline import IngestReport, extract_resolve_store

                            vstore = make_vector_store(conn, self.s.vector_backend)
                            report = IngestReport(document_id=did)
                            ok, err = extract_resolve_store(
                                conn,
                                self.provider,
                                vstore,
                                doc,
                                report,
                                vault_dir=self.s.vault_dir,
                                format=target_fmt,
                                on_progress=step_cb,
                            )
                            if not ok:
                                info["error"] = err
                            else:
                                info["new_summary"] = _clean_summary(report.summary)
                                info["entities_created"] = report.entities_created
                                info["entities_linked"] = report.entities_linked
                                info["new_entity_names"] = report.new_entity_names
                                info["linked_entity_names"] = report.linked_entity_names
                                info["relations_added"] = report.relations_added
                                info["detail_format"] = target_fmt

                                if self.s.auto_expand and self.s.expand_max > 0 and not doc.partial:
                                    dbm.enqueue_expand(conn, did)
                        else:
                            if do_summary:
                                # 요약만 단독 재추출 (엔티티/관계 보존)
                                step_cb("요약 LLM 재추출 중...", f"provider={getattr(self.provider, 'name', '?')}")
                                extract_res = self.provider.extract(doc)
                                new_summary = _clean_summary(extract_res.summary)
                                info["new_summary"] = new_summary

                                # extractions 테이블 in-place 갱신
                                ext_row = conn.execute(
                                    "SELECT id, raw_response FROM extractions WHERE document_id=? ORDER BY id DESC LIMIT 1",
                                    (did,),
                                ).fetchone()
                                if ext_row:
                                    try:
                                        data = _json.loads(ext_row["raw_response"] or "{}")
                                    except Exception:
                                        data = {}
                                    data["summary"] = new_summary
                                    new_raw = _json.dumps(data, ensure_ascii=False)
                                    conn.execute(
                                        "UPDATE extractions SET raw_response=? WHERE id=?",
                                        (new_raw, ext_row["id"]),
                                    )
                                else:
                                    data = {"summary": new_summary, "key_claims": [], "entities": [], "relations": []}
                                    new_raw = _json.dumps(data, ensure_ascii=False)
                                    dbm.log_extraction(
                                        conn,
                                        document_id=did,
                                        provider=getattr(self.provider, "name", "regenerate"),
                                        model=getattr(self.provider, "model", "fallback"),
                                        prompt_version=PROMPT_VERSION,
                                        raw_response=new_raw,
                                    )
                                conn.commit()

                            if do_detail:
                                step_cb("가독 본문(detail) 렌더링 생성 중...", f"format={target_fmt}")
                                ensure_document_detail(
                                    conn,
                                    self.provider,
                                    doc,
                                    force=True,
                                    format=target_fmt,
                                    directive=directive,
                                )
                                info["detail_format"] = target_fmt
                                if directive:
                                    info["directive"] = directive

                    info["updated"] = True
                    targets_info.append(info)

                return {
                    "dry_run": not force,
                    "count": len(targets_info),
                    "targets": targets_info,
                    "provider": getattr(self.provider, "name", "unknown"),
                    "model": getattr(self.provider, "model", "unknown"),
                    "effort": getattr(self.provider, "effort", "default"),
                }
            finally:
                if old_effort is not None:
                    self.provider.effort = old_effort
        finally:
            conn.close()

    def backfill_minhashes(self, *, limit: int = 0) -> dict:
        """minhash 가 비어있는 기존 문서에 서명을 채운다 — **비파괴**(컬럼만 채움).

        근사 중복 게이트(dedup ③)가 기존 문서와도 비교하려면 모든 문서에 서명이 있어야
        한다. 신규 적재는 자동 저장되지만 v6 이전 문서는 비어 있어 1회 백필이 필요하다.
        그래프/추출/Gemini 호출 없음. 반환: {docs, filled}."""
        import json as _json

        from .normalize import minhash_signature

        conn = dbm.connect(self.s.db_file)
        dbm.init_db(conn)
        try:
            ids = dbm.documents_missing_minhash(conn, limit)
            out = {"docs": len(ids), "filled": 0}
            for did in ids:
                doc = dbm.get_document(conn, did)
                if doc is None:
                    continue
                sig = minhash_signature((doc.title or "") + " " + (doc.raw_text or ""))
                dbm.set_document_minhash(conn, did, _json.dumps(sig) if sig else None)
                if sig:
                    out["filled"] += 1
            return out
        finally:
            conn.close()

    def dedup_scan(self, *, threshold: float = 0.90, min_len: int = 500) -> dict:
        """[진단·비파괴] 기존 문서 중 근사 중복 클러스터를 보고만 한다(병합 안 함).

        먼저 minhash 를 백필한 뒤, 임계 이상으로 묶이는 문서쌍을 모아 클러스터로 반환.
        실제 정리(엔티티 sources 재배치 + 중복 문서 삭제)는 파괴적이라 별도 결정/명령으로
        남긴다. 반환: {documents, clusters:[{ids, urls, score}...]}."""
        import json as _json

        from .normalize import minhash_estimate

        self.backfill_minhashes()
        conn = dbm.connect(self.s.db_file)
        dbm.init_db(conn)
        try:
            rows = conn.execute(
                "SELECT id, canonical_url, title, length(raw_text) ln, minhash "
                "FROM documents WHERE minhash IS NOT NULL AND partial=0 "
                "AND length(raw_text) >= ? ORDER BY fetched_at", (min_len,)
            ).fetchall()
            sigs = []
            for r in rows:
                try:
                    sigs.append((r, _json.loads(r["minhash"])))
                except (TypeError, ValueError):
                    continue
            # union-find 로 임계 이상 쌍을 클러스터링.
            parent = {r["id"]: r["id"] for r, _ in sigs}

            def find(x):
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x

            pair_scores: dict[tuple[str, str], float] = {}
            for i in range(len(sigs)):
                ri, si = sigs[i]
                for j in range(i + 1, len(sigs)):
                    rj, sj = sigs[j]
                    score = minhash_estimate(si, sj)
                    if score >= threshold:
                        pair_scores[(ri["id"], rj["id"])] = score
                        parent[find(ri["id"])] = find(rj["id"])
            groups: dict[str, list] = {}
            meta = {r["id"]: r for r, _ in sigs}
            for r, _ in sigs:
                groups.setdefault(find(r["id"]), []).append(r["id"])
            clusters = []
            for ids in groups.values():
                if len(ids) < 2:
                    continue
                best = max((s for (a, b), s in pair_scores.items()
                            if a in ids and b in ids), default=0.0)
                clusters.append({
                    "ids": ids,
                    "urls": [meta[i]["canonical_url"] for i in ids],
                    "titles": [(meta[i]["title"] or "")[:60] for i in ids],
                    "score": round(best, 3),
                })
            clusters.sort(key=lambda c: c["score"], reverse=True)
            return {"documents": len(sigs), "clusters": clusters}
        finally:
            conn.close()

    def merge_one_cluster(self, keeper: str, losers: list[str]) -> dict:
        """[웹 UI 단일 클러스터 병합] keeper 로 losers 를 합치고 loser 의 artifact 도 정리.

        dedup_merge 가 '스캔으로 찾은 모든 클러스터'를 한 번에 처리하는 데 비해, 이건 웹에서
        사용자가 클러스터/유지문서를 골라 1건만 병합하는 통로. **파괴적**이므로 병합 직전
        정본을 내부 checkpoint(VACUUM INTO)로 저장한다. checkpoint 생성에 실패하면 병합을
        시작하지 않는다. 이는 cb-manuscript가 관리하는 운영 백업과 별개다.
        반환: db.merge_documents 결과 + {checkpoint: 경로|None}."""
        import time as _time

        losers = [d for d in losers if d and d != keeper]
        if not losers:
            return {"merged": 0, "deleted": 0, "checkpoint": None}
        ts = _time.strftime("%Y%m%d-%H%M%S")
        nonce = _time.time_ns() % 1_000_000_000
        dest = (
            self.s.data_dir
            / "checkpoints"
            / f"pre-webmerge-{ts}-{nonce:09d}.db"
        )
        checkpoint_path = str(dbm.checkpoint_database(self.s.db_file, dest))
        conn = dbm.connect(self.s.db_file)
        dbm.init_db(conn)
        try:
            res = dbm.merge_documents(conn, keeper, losers)
        finally:
            conn.close()
        for d in losers:                       # keeper 로 옮긴 loser 의 원문 artifact 정리
            try:
                p = self.s.data_dir / "raw" / "artifacts" / f"{d}.txt.gz"
                if p.exists():
                    p.unlink()
            except Exception:  # noqa: BLE001
                pass
        res["checkpoint"] = checkpoint_path
        return res

    def recanonicalize_documents(self, *, apply: bool = True) -> dict:
        """기존 문서의 canonical_url 을 현재 규칙으로 재계산 — **비파괴**(URL 열만 갱신).

        canonicalize_url 규칙이 좋아지면(예: arxiv 버전 정규화) 이미 적재된 문서는 옛
        canonical 을 그대로 들고 있어 같은 자료가 갈라진 채 남는다. 이 백필이 정렬한다.
        apply=False 면 변경 예정만 보고. 반환: {docs, changed, samples[...]}.
        """
        from .normalize import canonicalize_url

        conn = dbm.connect(self.s.db_file)
        dbm.init_db(conn)
        try:
            rows = conn.execute(
                "SELECT id, url, canonical_url FROM documents "
                "WHERE url IS NOT NULL").fetchall()
            out = {"docs": len(rows), "changed": 0, "samples": []}
            for r in rows:
                new = canonicalize_url(r["url"])
                if new and new != r["canonical_url"]:
                    out["changed"] += 1
                    if len(out["samples"]) < 20:
                        out["samples"].append(
                            {"id": r["id"], "from": r["canonical_url"], "to": new})
                    if apply:
                        conn.execute("UPDATE documents SET canonical_url=? WHERE id=?",
                                     (new, r["id"]))
            if apply:
                conn.commit()
            return out
        finally:
            conn.close()

    def dedup_merge(self, *, threshold: float = 0.90, min_len: int = 500,
                    apply: bool = False) -> dict:
        """근사중복 클러스터를 각각 1개 문서로 병합. **apply=False 면 계획만(비파괴)**.

        keeper 선정 = 가장 긴 본문(가장 완전) → 동률이면 최초 적재(first-seen). 나머지는
        loser 로 keeper 에 참조 재배치 후 삭제(db.merge_documents). apply=True 면 loser 의
        artifact 파일도 정리한다. **파괴적이므로 호출자가 복구 계획을 책임진다.** 반환:
        {clusters:[{keeper, losers, ...}], merged}."""
        scan = self.dedup_scan(threshold=threshold, min_len=min_len)
        conn = dbm.connect(self.s.db_file)
        dbm.init_db(conn)
        plans: list[dict] = []
        try:
            for c in scan["clusters"]:
                rows = {r["id"]: r for r in (
                    dbm.get_document_row(conn, did) for did in c["ids"]) if r}
                if len(rows) < 2:
                    continue
                # keeper = 최장 본문, 동률이면 최초 적재(fetched_at 작은 쪽).
                def _key(did):
                    r = rows[did]
                    return (len(r["raw_text"] or ""), -(r["fetched_at"] or 0.0))
                keeper = max(rows, key=_key)
                losers = [d for d in rows if d != keeper]
                plan = {"keeper": keeper, "losers": losers, "score": c["score"],
                        "keeper_url": rows[keeper]["canonical_url"],
                        "loser_urls": [rows[d]["canonical_url"] for d in losers]}
                if apply:
                    res = dbm.merge_documents(conn, keeper, losers)
                    plan["result"] = res
                    for d in losers:
                        try:
                            p = self.s.data_dir / "raw" / "artifacts" / f"{d}.txt.gz"
                            if p.exists():
                                p.unlink()
                        except Exception:  # noqa: BLE001
                            pass
                plans.append(plan)
            return {"clusters": plans, "merged": len(plans), "applied": apply}
        finally:
            conn.close()

    def search(
        self,
        query: str,
        *,
        limit: int = 8,
        summarize: bool = True,
        mode: SearchMode = "hybrid",
        include_hidden: bool = True,
    ):
        from ..retrieval.query import search as _search

        if mode not in {"hybrid", "fts"}:
            raise ValueError(f"unsupported search mode: {mode}")
        if mode == "fts" and summarize:
            raise ValueError("fts search does not support summaries")

        conn = dbm.connect_existing(self.s.db_file, readonly=True)
        try:
            if mode == "fts":
                return _search(
                    conn,
                    None,
                    None,
                    query,
                    limit=limit,
                    summarize=summarize,
                    mode=mode,
                    include_hidden=include_hidden,
                )
            vstore = make_vector_store(conn, self.s.vector_backend)
            return _search(
                conn,
                vstore,
                self.provider,
                query,
                limit=limit,
                summarize=summarize,
                mode=mode,
                include_hidden=include_hidden,
            )
        finally:
            conn.close()

    def replay_failed(self, *, limit: int = 0):
        """raw_inbox 의 status='error' 행을 원본 payload 로 재적재.

        재적재 요구의 실현: 알고리즘/quota 문제로 실패한 항목을 보관된 원본에서 재생.
        (payload 가 보관 파일 경로면 그 파일을, URL/text 면 그대로 다시 fetch)
        반환: [(inbox_id, IngestReport), ...]
        """
        conn = dbm.connect(self.s.db_file)
        dbm.init_db(conn)
        rows = dbm.inbox_by_status(conn, "error")
        conn.close()
        if limit:
            rows = rows[:limit]
        out = []
        for row in rows:
            payload = row["file_ref"] or row["payload"]
            rep = self.ingest(
                payload, source="replay-failed",
                inbox_kind=row["kind"], file_name=row["file_name"],
                file_ref=row["file_ref"],
            )
            out.append((row["id"], rep))
        return out

    def _retry_extract(self, document_id: str, inbox_id: int) -> IngestReport:
        """[자동복구] extract 단계에서 실패해 문서만 적재된 행을 재추출.

        re-fetch 없이(원본 raw_text 가 이미 DB 에 있음) extract→해소→관계→vault 만
        다시 돌린다. dedup/nochange 가드를 모두 우회 = 429 등으로 추출만 막혔던 케이스의
        진짜 복구. 성공하면 inbox 를 done 으로 갱신.
        """
        conn = dbm.connect(self.s.db_file)
        dbm.init_db(conn)
        vstore = make_vector_store(conn, self.s.vector_backend)
        report = IngestReport(document_id=document_id)
        try:
            doc = dbm.get_document(conn, document_id)
            if doc is None:
                report.error = "document not found"
                return report
            report.source_type = doc.source_type
            report.partial = doc.partial
            report.title = doc.title
            ok, err = extract_resolve_store(
                conn, self.provider, vstore, doc, report, vault_dir=self.s.vault_dir)
            if ok:
                dbm.update_inbox(conn, inbox_id, status="done", document_id=document_id)
            else:
                report.error = err
            return report
        finally:
            conn.close()

    def list_failures(self, *, limit: int = 10) -> list[dict]:
        """error/failed inbox 최신순 요약(텔레그램 /failed 용)."""
        conn = dbm.connect(self.s.db_file)
        dbm.init_db(conn)
        try:
            rows = dbm.inbox_failures(conn, limit=limit)
        finally:
            conn.close()
        return [
            {"id": r["id"], "status": r["status"], "kind": r["kind"],
             "attempts": r["attempts"], "document_id": r["document_id"],
             "payload": (r["file_ref"] or r["payload"] or "")[:120],
             "error": (r["error"] or "")[:200]}
            for r in rows
        ]

    def retry_inbox(self, inbox_id: int) -> IngestReport:
        """[수동 재시도] 텔레그램 /retry 등에서 특정 inbox 건 하나를 즉시 재적재.

        recover_failed 의 자동 게이팅(attempts 상한·백오프)을 무시하고 사용자가 명시적으로
        요청한 1건만 처리한다. document_id 가 있으면(=extract 단계 실패) 재추출만, 없으면
        fetch 부터 다시. 실패해도 영구실패로 굳히지 않고 status='error' 로 남겨 다음 자동/수동
        재시도 기회를 유지한다(사용자가 직접 재시도했다는 사실만으로 상한을 확정짓지 않음).
        """
        conn = dbm.connect(self.s.db_file)
        dbm.init_db(conn)
        row = dbm.get_inbox(conn, inbox_id)
        conn.close()
        if row is None:
            rep = IngestReport()
            rep.error = f"inbox#{inbox_id} 없음"
            return rep

        if row["document_id"]:
            rep = self._retry_extract(row["document_id"], inbox_id)
        else:
            payload = row["file_ref"] or row["payload"]
            rep = self.ingest(
                payload, source="manual-retry", inbox_id=inbox_id,
                inbox_kind=row["kind"], file_name=row["file_name"],
                file_ref=row["file_ref"],
            )
        if rep.error:
            conn2 = dbm.connect(self.s.db_file)
            try:
                conn2.execute(
                    "UPDATE raw_inbox SET status='error', error=? WHERE id=?",
                    (rep.error, inbox_id))
                conn2.commit()
            finally:
                conn2.close()
        return rep

    def recover_failed(
        self,
        *,
        max_attempts: int = 5,
        base_delay: float = 300.0,
        limit: int = 0,
        reporter: Any | None = None,
        on_progress: Callable[[str, str], None] | None = None,
    ) -> list[dict]:
        """[자동복구] error inbox 중 재시도 시각이 도래한 항목을 자동 재적재.

        replay_failed 가 *수동 전량* 재적재라면, 이쪽은 데몬(recover-loop)이 부르는
        *게이팅된 자동* 경로:
          - 대상: status='error' AND attempts<max AND now>=next_retry_at (없으면 즉시).
          - 성공/duplicate: ingest 가 같은 inbox 행을 done/duplicate 로 갱신(멱등).
          - 실패: attempts+1, next_retry_at = now + base_delay·2^attempts(지수백오프).
            attempts 가 max 에 도달하면 status='failed'(영구실패)로 굳혀 무한재시도 차단.
        반환: [{inbox_id, status, error?}, ...]
        """
        import time as _time

        conn = dbm.connect(self.s.db_file)
        dbm.init_db(conn)
        rows = dbm.due_for_recovery(conn, max_attempts=max_attempts, limit=limit)
        conn.close()

        out: list[dict] = []
        for idx, row in enumerate(rows, 1):
            with _item_context(
                reporter,
                on_progress,
                idx,
                f"inbox#{row['id']}",
                title=f"수신 복구 ({row['document_id'] or row['kind']})",
                url=str(row['payload'] or ''),
            ) as step_cb:
                # 핵심 분기: extract 단계에서 실패한 행은 document 가 이미 적재돼 있어(=
                # document_id 보유) full re-ingest 하면 dedup 에 걸려 추출이 영영 안 된다.
                # 그 경우 기존 문서로 extract 만 재실행(dedup/nochange 우회)한다.
                # document_id 가 없으면 fetch 단계에서 실패한 것 → full re-ingest.
                if row["document_id"]:
                    step_cb("기존 적재 문서 추출 재실행 중...")
                    rep = self._retry_extract(row["document_id"], row["id"])
                else:
                    payload = row["file_ref"] or row["payload"]
                    step_cb("원문 재수집 및 파이프라인 재실행 중...")
                    rep = self.ingest(
                        payload, source="recover", inbox_id=row["id"],
                        inbox_kind=row["kind"], file_name=row["file_name"],
                        file_ref=row["file_ref"],
                    )
                if rep.error is None:
                    # ingest 가 이미 done/duplicate 로 갱신함 → due 에 다시 안 잡힘.
                    out.append({"inbox_id": row["id"],
                                "status": "duplicate" if rep.duplicate else "done"})
                    continue
                attempts_now = (row["attempts"] or 0) + 1
                if attempts_now >= max_attempts:
                    final, nra = "failed", None
                else:
                    final = "error"
                    nra = _time.time() + base_delay * (2 ** (row["attempts"] or 0))
                conn2 = dbm.connect(self.s.db_file)
                try:
                    dbm.record_recovery_attempt(
                        conn2, row["id"], status=final,
                        document_id=rep.document_id, error=rep.error, next_retry_at=nra)
                finally:
                    conn2.close()
                out.append({"inbox_id": row["id"], "status": final, "error": rep.error})
        return out

    def save_inbound_file(self, inbox_seq: int, src_path: Path, name: str) -> str:
        """텔레그램 등으로 받은 원본 파일을 raw/files 에 영구 보관."""
        from ..store.raw import save_raw_file

        return save_raw_file(self.s.data_dir, inbox_seq, src_path, name)

    def scan_truncation_status(self, doc_id: str | None = None) -> dict:
        """데이터베이스 내 문서들의 원문 절단 및 메타데이터 현황 스캔."""
        conn = dbm.connect(self.s.db_file)
        dbm.init_db(conn)
        try:
            return dbm.scan_truncation_status(conn, doc_id=doc_id)
        finally:
            conn.close()

    def backfill_truncation(
        self,
        *,
        doc_id: str | None = None,
        force: bool = False,
        mark_refresh: bool = False,
    ) -> dict:
        """메타데이터 누락 절단 문서에 raw_truncated 소급 갱신."""
        conn = dbm.connect(self.s.db_file)
        dbm.init_db(conn)
        try:
            return dbm.backfill_truncation_metadata(
                conn, doc_id=doc_id, force=force, mark_refresh=mark_refresh
            )
        finally:
            conn.close()

