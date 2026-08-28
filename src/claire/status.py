"""현황 텍스트 생성 — CLI(claire status)와 텔레그램(/status)이 공유한다.

build_status_text(full=True) = CLI용 상세, full=False = 텔레그램용 요약.
"""

from __future__ import annotations

import time

from .config import Settings
from .store import db as dbm
from .store.raw import raw_disk_usage


def _mb(b: int) -> str:
    return f"{b / 1e6:.2f}MB"


def build_status_text(settings: Settings, *, full: bool = True) -> str:
    s = settings
    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    c = dbm.counts(conn)
    lines: list[str] = []

    lines.append("📊 Claire Bible status")

    # 운영
    lines.append("[운영]")
    lines.append(f"  repo     : {s.effective_github_repository} ({s.effective_source_base_url})")
    if s.effective_provider == "antigravity":
        lines.append(f"  provider : {s.effective_provider} "
                     f"(model={s.agy_model}, effort={s.agy_effort})")
    else:
        lines.append(f"  provider : {s.effective_provider} "
                     f"(gen={s.gemini_model}, embed={s.gemini_embed_model})")
    if full:
        lines.append(f"  telegram : {'set' if s.telegram_bot_token else 'NOT set'} "
                     f"· allowed {sorted(s.allowed_user_ids) or 'ALL'}")
        lines.append(f"  inject   : {s.inject_host}:{s.inject_port} "
                     f"(token {'set' if s.inject_token else 'NONE'})")
        lines.append(f"  vector   : {s.vector_backend}")
        lines.append(f"  db       : {s.db_file}")

    # DB / 그래프
    lines.append("[DB / 그래프]")
    lines.append(f"  docs {c['documents']} · entities {c['entities']} · "
                 f"relations {c['relations']} · embeds {c['embeddings']}")
    if c["proposals"]:
        lines.append(f"  새 타입 제안 대기 {c['proposals']}")
    usage = raw_disk_usage(s.data_dir)
    lines.append(f"  raw 보관 : artifacts {_mb(usage['artifacts'])} · files {_mb(usage['files'])} "
                 f"· images {_mb(usage['images'])}")
    src = dbm.source_type_counts(conn)
    if src:
        lines.append("  소스 : " + ", ".join(f"{t}={n}" for t, n in src))
    if full:
        etypes = dbm.entity_type_counts(conn)
        if etypes:
            lines.append("  타입 : " + ", ".join(f"{t}={n}" for t, n in etypes))

    # 진행 / inbox
    lines.append("[진행 / inbox]")
    inbox = dbm.inbox_status_counts(conn)
    total_in = sum(inbox.values())
    lines.append(f"  받은 쿼리 {total_in}  "
                 f"({', '.join(f'{k}={v}' for k, v in sorted(inbox.items())) or '-'})")
    last = dbm.last_inbox_activity(conn)
    if last:
        ago = int(time.time() - last)
        unit = f"{ago}초 전" if ago < 120 else f"{ago // 60}분 전"
        lines.append(f"  최근 수신 : {time.strftime('%m-%d %H:%M', time.localtime(last))} ({unit})")
    errs = dbm.inbox_by_status(conn, "error")
    if errs:
        lines.append(f"  ⚠️ 실패 {len(errs)}건 (claire replay-failed 로 재적재 가능)")
        if full:
            for r in errs[:5]:
                lines.append(f"    - #{r['id']} {(r['payload'] or '')[:46]} :: {(r['error'] or '')[:48]}")

    # 갱신 큐(복원)
    rq = dbm.refresh_status_counts(conn)
    if rq:
        lines.append("[갱신 큐]")
        lines.append("  " + ", ".join(f"{k}={v}" for k, v in sorted(rq.items())))

    # 자동확장 큐(1홉)
    eq = dbm.expand_status_counts(conn)
    if eq:
        lines.append("[자동확장 큐]")
        lines.append("  " + ", ".join(f"{k}={v}" for k, v in sorted(eq.items())))

    # 연결
    lines.append("[연결]")
    merged = dbm.most_merged_entities(conn, limit=(12 if full else 6))
    if merged:
        lines.append("  수렴 노드(여러 자료에서 등장):")
        for name, typ, n in merged:
            lines.append(f"    - [{typ}] {name} (자료 {n})")
    else:
        lines.append("  수렴 노드 없음")
    if full:
        top = dbm.top_connected_entities(conn)
        if top and any(d > 0 for _, _, d in top):
            lines.append("  허브 노드(연결 많은 순):")
            for name, typ, deg in top:
                if deg > 0:
                    lines.append(f"    - [{typ}] {name} (연결 {deg})")

    conn.close()
    return "\n".join(lines)


def build_queue_dashboard(
    settings: Settings,
    *,
    queue_name: str | None = None,
    limit: int = 20,
) -> str:
    """모든 비동기 큐의 대기/실패/진행 현황을 집약한 대시보드 텍스트 생성."""
    s = settings
    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)

    lines: list[str] = [
        "📋 Claire 비동기 큐 & 작업 대시보드",
        "=" * 64,
    ]

    now = time.time()

    # 1. 수신 및 자동복구 큐 (raw_inbox)
    if queue_name in (None, "inbox", "raw_inbox"):
        inbox_counts = dbm.inbox_status_counts(conn)
        total_inbox = sum(inbox_counts.values())
        due_recovery = len(dbm.due_for_recovery(conn, max_attempts=5, limit=0))
        lines.append(f"\n[1] 📥 수신 및 자동복구 큐 (raw_inbox) - 총 {total_inbox}건")
        lines.append(f"    상태 분포 : " + (", ".join(f"{k}={v}" for k, v in sorted(inbox_counts.items())) or "비어있음"))
        if due_recovery > 0:
            lines.append(f"    재시도 도래: {due_recovery}건 (즉시 복구 가능: ./cb-manuscript app recover-run)")

        err_rows = conn.execute(
            "SELECT id, status, payload, error, attempts, next_retry_at, document_id "
            "FROM raw_inbox WHERE status IN ('error', 'failed') ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        if err_rows:
            lines.append(f"    최근 실패/에러 항목 ({len(err_rows)}건):")
            for r in err_rows:
                pl = (r["payload"] or "")[:40]
                err = (r["error"] or "")[:40]
                nra = r["next_retry_at"]
                retry_in = f"{int(nra - now)}초 후" if (nra and nra > now) else "즉시"
                st_icon = "⛔" if r["status"] == "failed" else "↻"
                lines.append(f"      {st_icon} #{r['id']} [{r['status']}] 시도 {r['attempts']}회 (재시도: {retry_in})")
                lines.append(f"         페이로드: {pl}")
                if err:
                    lines.append(f"         오류원인: {err}")

    # 2. 문서 갱신 큐 (refresh_queue)
    if queue_name in (None, "refresh", "refresh_queue"):
        ref_counts = dbm.refresh_status_counts(conn)
        total_ref = sum(ref_counts.values())
        lines.append(f"\n[2] 🔄 문서 갱신 큐 (refresh_queue) - 총 {total_ref}건")
        lines.append(f"    상태 분포 : " + (", ".join(f"{k}={v}" for k, v in sorted(ref_counts.items())) or "비어있음"))

        ref_pending = conn.execute(
            "SELECT id, document_id, payload, reason, status, attempts, error "
            "FROM refresh_queue WHERE status IN ('pending', 'error') ORDER BY id ASC LIMIT ?",
            (limit,),
        ).fetchall()
        if ref_pending:
            lines.append(f"    대기/에러 항목 ({len(ref_pending)}건):")
            for r in ref_pending:
                err_str = f" :: 오류: {r['error'][:35]}" if r["error"] else ""
                lines.append(f"      • #{r['id']} doc_id={r['document_id']} reason={r['reason']} status={r['status']}{err_str}")
                if r["payload"]:
                    lines.append(f"        URL: {r['payload'][:55]}")

    # 3. 1홉 자동확장 큐 (expand_queue)
    if queue_name in (None, "expand", "expand_queue"):
        exp_counts = dbm.expand_status_counts(conn)
        total_exp = sum(exp_counts.values())
        lines.append(f"\n[3] 🔗 1홉 자동확장 큐 (expand_queue) - 총 {total_exp}건")
        lines.append(f"    상태 분포 : " + (", ".join(f"{k}={v}" for k, v in sorted(exp_counts.items())) or "비어있음"))

        exp_pending = conn.execute(
            "SELECT id, document_id, status, attempts, error "
            "FROM expand_queue WHERE status IN ('pending', 'error') ORDER BY id ASC LIMIT ?",
            (limit,),
        ).fetchall()
        if exp_pending:
            lines.append(f"    대기/에러 항목 ({len(exp_pending)}건):")
            for r in exp_pending:
                err_str = f" :: 오류: {r['error'][:35]}" if r["error"] else ""
                lines.append(f"      • #{r['id']} doc_id={r['document_id']} status={r['status']} (시도 {r['attempts']}회){err_str}")

    conn.close()

    lines.append("\n" + "-" * 64)
    lines.append("💡 [큐 처리 실행 안내]")
    lines.append("  • 갱신 큐 1회 즉시 처리 : ./cb-manuscript app refresh-run")
    lines.append("  • 확장 큐 1회 즉시 처리 : ./cb-manuscript app expand-run")
    lines.append("  • 실패 수신 1회 복구    : ./cb-manuscript app recover-run")
    lines.append("  • 실패 수신 전량 재적재 : ./cb-manuscript app replay-failed")
    lines.append("=" * 64)

    return "\n".join(lines)
