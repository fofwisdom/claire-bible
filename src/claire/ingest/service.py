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
    ) -> IngestReport:
        """단건 적재. (블로킹 — 호출측에서 스레드 오프로드).

        inbox_id 가 주어지면 새 raw_inbox 행을 만들지 않고 기존 행을 재사용(자동복구용).
        """
        conn = dbm.connect(self.s.db_file)
        dbm.init_db(conn)
        vstore = make_vector_store(conn, self.s.vector_backend)
        em = self.s.expand_max if expand_max is None else expand_max
        try:
            return ingest(
                payload, conn=conn, provider=self.provider, vstore=vstore,
                vault_dir=self.s.vault_dir, data_dir=self.s.data_dir,
                expand_max=em, source=source, user_id=user_id, chat_id=chat_id,
                inbox_kind=inbox_kind, file_ref=file_ref, file_name=file_name,
                inbox_id=inbox_id,
            )
        finally:
            conn.close()

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
