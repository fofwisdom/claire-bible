"""IngestService — 적재의 단일 진입점(모듈화).

텔레그램 DM · 로컬 inject API · CLI 가 모두 이 클래스를 통해 적재한다 =
사용자가 말한 "내가 DM 던지는 것과 동일한 통로". provider 는 1회 생성해 보유하고,
DB 커넥션은 호출마다 짧게 연다(WAL + busy_timeout 로 다중 프로세스 안전).
"""

from __future__ import annotations

from pathlib import Path

from ..config import Settings
from ..extract.provider import get_provider
from ..store import db as dbm
from ..store.vectors import make_vector_store
from .pipeline import IngestReport, extract_resolve_store, ingest
from .router import fetch as default_fetch


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
        prefetched: "Document | None" = None,
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
        try:
            return ingest(
                payload, conn=conn, provider=self.provider, vstore=vstore,
                vault_dir=self.s.vault_dir, data_dir=self.s.data_dir,
                expand_max=em, source=source, user_id=user_id, chat_id=chat_id,
                inbox_kind=inbox_kind, file_ref=file_ref, file_name=file_name,
                inbox_id=inbox_id, prefetched=prefetched, auto_expand=auto,
            )
        finally:
            conn.close()

    def expand_document(self, document_id: str, *, limit: int | None = None) -> dict:
        """[1홉 자동확장] 부모 문서의 링크를 LLM 이 선별→fetch→판정→통과 시 적재.

        파고들지(select_followups)·쌓을지(judge_research 게이트) 모두 LLM 결정.
        깊이는 1 고정: 자식 적재는 source='onehop:*' + expand_max=0 → 재확장 안 됨.
        반환: {document_id, candidates, selected, stored, skipped, followed[...]}
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
                  "selected": 0, "stored": 0, "skipped": 0, "followed": []}
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
            rel, qual = 1.0, 1.0
            if judge:
                report = f"{child.title or ''}\n{(child.raw_text or '')[:4000]}"
                j = judge(doc.title or "", context, report)
                rel = float(j.get("relevance") or 0.0)
                qual = float(j.get("quality") or 0.0)
            if not passes_gate(rel, qual):
                result["skipped"] += 1
                result["followed"].append({"url": url, "stored": False,
                                           "relevance": rel, "quality": qual})
                continue
            rep = self.ingest(url, source=f"onehop:{document_id}", expand_max=0,
                              prefetched=child)
            stored = rep.error is None and not rep.duplicate
            if stored:
                result["stored"] += 1
            result["followed"].append({"url": url, "stored": stored,
                                       "title": rep.title, "duplicate": rep.duplicate,
                                       "relevance": rel, "quality": qual,
                                       "error": rep.error})
        return result

    def run_expand_queue(self, *, limit: int = 0) -> list[dict]:
        """[1홉 자동확장] 대기열의 pending 문서를 처리(expand-loop 데몬이 호출).

        각 문서를 expand_document 로 확장하고 done/error 로 마킹. 반환: 처리 요약 목록.
        """
        import json as _json

        conn = dbm.connect(self.s.db_file)
        dbm.init_db(conn)
        rows = dbm.pending_expand(conn, limit=limit)
        conn.close()

        out: list[dict] = []
        for row in rows:
            try:
                res = self.expand_document(row["document_id"])
            except Exception as e:  # noqa: BLE001
                conn2 = dbm.connect(self.s.db_file)
                try:
                    dbm.update_expand(conn2, row["id"], status="error", error=str(e))
                finally:
                    conn2.close()
                out.append({"document_id": row["document_id"], "error": str(e)})
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

    def refresh_document(self, document_id: str, payload: str) -> dict:
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

            try:
                doc = default_fetch(payload)
            except Exception as e:  # noqa: BLE001
                return {"status": "error", "document_id": document_id, "error": str(e)}

            if doc.content_hash == old["content_hash"]:
                return {"status": "nochange", "document_id": document_id,
                        "old_len": old_len, "new_len": len(doc.raw_text)}

            # 같은 id 로 갱신(신규가 아니라 복원이므로 sources 연결 유지).
            doc.id = document_id
            dbm.update_document_content(
                conn, document_id, title=doc.title, raw_text=doc.raw_text,
                content_hash=doc.content_hash, fetched_at=doc.fetched_at)
            try:
                from ..store.raw import save_artifact

                save_artifact(self.s.data_dir, document_id, doc.raw_text)
            except Exception:  # noqa: BLE001
                pass

            report = IngestReport(document_id=document_id)
            ok, err = extract_resolve_store(
                conn, self.provider, vstore, doc, report, vault_dir=self.s.vault_dir)
            if not ok:
                return {"status": "error", "document_id": document_id, "error": err}
            return {"status": "done", "document_id": document_id,
                    "old_len": old_len, "new_len": len(doc.raw_text),
                    "entities_created": report.entities_created,
                    "entities_linked": report.entities_linked}
        finally:
            conn.close()

    def run_refresh_queue(self, *, limit: int = 0) -> list[dict]:
        """대기열의 pending 항목을 처리. 각 결과로 큐 상태 갱신. 결과 리스트 반환."""
        conn = dbm.connect(self.s.db_file)
        dbm.init_db(conn)
        rows = dbm.pending_refresh(conn, limit=limit)
        conn.close()
        out = []
        for row in rows:
            res = self.refresh_document(row["document_id"], row["payload"])
            conn2 = dbm.connect(self.s.db_file)
            try:
                st = res["status"] if res["status"] in ("done", "nochange") else "error"
                dbm.update_refresh(conn2, row["id"], status=st, error=res.get("error"))
            finally:
                conn2.close()
            out.append({**res, "queue_id": row["id"]})
        return out

    def mark_thin_for_refresh(
        self, *, max_len: int = 300, host: str | None = None, reason: str = "thin"
    ) -> int:
        """본문 빈약 문서를 갱신 대상으로 등록. 등록(신규+되살림) 건수 반환."""
        conn = dbm.connect(self.s.db_file)
        dbm.init_db(conn)
        try:
            rows = dbm.thin_documents(conn, max_len=max_len, host=host)
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

    def reextract_all(self, *, rebuild: bool = True, limit: int = 0) -> dict:
        """저장된 raw_text 로 전체 문서를 재추출(프롬프트 변경 반영 — 예: 한글화).

        rebuild=True: 먼저 그래프(엔티티/관계/임베딩/추출)를 비우고 처음부터 재구축한다.
        _merge 는 observations 를 *추가*하므로, 비우지 않으면 기존(영문)+신규(한글)가 섞인다.
        documents·raw_inbox·artifact 는 보존하므로 입력은 그대로. **파괴적** — 호출 전
        백업 필수(CLI 가 강제). 문서당 Gemini 1회(quota). 오래된 문서부터(원래 적재 순서
        에 가깝게) 처리해 first-seen canonical 수렴을 원래와 맞춘다.
        """
        conn = dbm.connect(self.s.db_file)
        dbm.init_db(conn)
        vstore = make_vector_store(conn, self.s.vector_backend)
        try:
            rows = dbm.documents_timeline(conn, limit or 1000000)
            ids = [r["id"] for r in rows][::-1]  # 오래된 것부터
            if limit:
                ids = ids[:limit]
            if rebuild:
                dbm.reset_graph(conn)
            out = {"docs": 0, "ok": 0, "failed": 0, "errors": []}
            for did in ids:
                doc = dbm.get_document(conn, did)
                if doc is None:
                    continue
                out["docs"] += 1
                report = IngestReport(document_id=did)
                ok, err = extract_resolve_store(
                    conn, self.provider, vstore, doc, report,
                    vault_dir=self.s.vault_dir)
                if ok:
                    out["ok"] += 1
                else:
                    out["failed"] += 1
                    out["errors"].append({"document_id": did, "error": err})
            return out
        finally:
            conn.close()

    def backfill_details(self, *, limit: int = 0, force: bool = False) -> dict:
        """detail(한국어 가독 렌더링)이 없는 기존 문서를 채운다 — **비파괴적**.

        그래프(엔티티/관계)를 건드리지 않고 documents.detail 컬럼만 채우므로 reextract 의
        reset_graph/rebuild 가 불필요(advisor). 문서당 Gemini 1회(quota). force=True 면
        이미 있는 detail 도 재생성. 반환: {docs, ok, skipped}."""
        from .pipeline import ensure_document_detail

        conn = dbm.connect(self.s.db_file)
        dbm.init_db(conn)
        try:
            ids = (dbm.documents_missing_detail(conn, limit) if not force
                   else [r["id"] for r in dbm.documents_timeline(conn, limit or 1000000)])
            out = {"docs": len(ids), "ok": 0, "skipped": 0}
            for did in ids:
                doc = dbm.get_document(conn, did)
                if doc is None:
                    out["skipped"] += 1
                    continue
                if ensure_document_detail(conn, self.provider, doc, force=force):
                    out["ok"] += 1
                else:
                    out["skipped"] += 1
            return out
        finally:
            conn.close()

    def search(self, query: str, *, limit: int = 8, summarize: bool = True):
        from ..retrieval.query import search as _search

        conn = dbm.connect(self.s.db_file)
        dbm.init_db(conn)
        vstore = make_vector_store(conn, self.s.vector_backend)
        try:
            return _search(conn, vstore, self.provider, query,
                           limit=limit, summarize=summarize)
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

    def recover_failed(
        self, *, max_attempts: int = 5, base_delay: float = 300.0, limit: int = 0
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
        for row in rows:
            # 핵심 분기: extract 단계에서 실패한 행은 document 가 이미 적재돼 있어(=
            # document_id 보유) full re-ingest 하면 dedup 에 걸려 추출이 영영 안 된다.
            # 그 경우 기존 문서로 extract 만 재실행(dedup/nochange 우회)한다.
            # document_id 가 없으면 fetch 단계에서 실패한 것 → full re-ingest.
            if row["document_id"]:
                rep = self._retry_extract(row["document_id"], row["id"])
            else:
                payload = row["file_ref"] or row["payload"]
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
