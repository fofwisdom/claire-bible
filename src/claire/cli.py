"""claire CLI 진입점.

  claire doctor          환경/벡터백엔드/DB 점검
  claire bot             텔레그램 봇 실행 (long-polling)
  claire stats           그래프 통계
  claire ingest <text>   (M1 stub) 단건 적재

M0 에서는 doctor / bot(echo) / stats 가 동작한다.
"""

from __future__ import annotations

import argparse
import sys

from .config import get_settings
from .store import db as dbm
from .store.vectors import probe_sqlite_vec


def cmd_preflight(_args) -> int:
    s = get_settings()
    print("claire preflight")
    print("=" * 40)
    print(f"python            : {sys.version.split()[0]}")
    print(f"github repository : {s.effective_github_repository}")
    print(f"source base url   : {s.effective_source_base_url}")
    print(f"provider (config) : {s.provider}")
    print(f"provider (eff*)   : {s.effective_provider}")
    print(f"  gemini key set  : {'yes' if s.gemini_api_key else 'NO (-> mock)'}")
    print(f"telegram token    : {'set' if s.telegram_bot_token else 'NOT set'}")
    print(f"allowed users     : {sorted(s.allowed_user_ids) or 'ALL'}")
    print(f"db path           : {s.db_file}")
    print(f"vault path        : {s.vault_dir}")
    print(f"vector backend cfg: {s.vector_backend}")
    print(f"data lifecycle    : {s.data_lifecycle} (purge_allowed={'YES' if s.is_purge_allowed else 'NO'})")
    anonymous_status = (
        "ENABLED (full knowledge base is public)"
        if s.anonymous_readonly
        else "disabled"
    )
    print(f"anonymous readonly: {anonymous_status}")

    ok, detail = probe_sqlite_vec()
    print(f"sqlite-vec probe  : {'OK' if ok else 'fallback->brute'} ({detail})")

    # provider 실호출 점검 (생성 + 임베딩이 조용히 실패하지 않는지)
    if s.effective_provider == "antigravity":
        import shutil

        from .extract.provider import get_provider

        bin_path = shutil.which(s.agy_bin)
        print(f"agy binary        : {bin_path or 'NOT found'}")
        print(f"agy model         : {s.agy_model} (effort={s.agy_effort})")
        try:
            prov = get_provider(s)
            v = prov.embed("claire embedding probe")
            print(f"agy embed         : OK (dim={len(v)})")
        except Exception as e:  # noqa: BLE001
            print(f"agy embed         : FAIL ({type(e).__name__}: {str(e)[:120]})")
    elif s.effective_provider == "gemini":
        from .extract.provider import get_provider

        try:
            prov = get_provider(s)
            v = prov.embed("claire embedding probe")
            print(f"gemini embed      : OK (model={s.gemini_embed_model}, dim={len(v)})")
        except Exception as e:  # noqa: BLE001
            print(f"gemini embed      : FAIL ({type(e).__name__}: {str(e)[:120]})")
            print("  -> 의미 기반 엔티티 해소가 비활성화됩니다. 임베딩 모델명을 확인하세요.")
    else:
        print(f"provider          : {s.effective_provider} (no embed probe)")

    # DB 점검 (생성/초기화)
    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    c = dbm.counts(conn)
    print(f"db counts         : {c}")
    conn.close()
    print("=" * 40)
    print("preflight: OK")
    return 0


def cmd_doctor(args) -> int:
    """지식그래프 및 DB 무결성 진단 및 자동 수복."""
    s = get_settings()
    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    try:
        do_heal = getattr(args, "heal", False) or getattr(args, "apply", False) or getattr(args, "repair", False)
        if do_heal:
            print("claire doctor: [Auto-Heal] 지식그래프 무결성 수복 시작...")
            healed = dbm.heal_graph(conn)
            print("=" * 50)
            print(f"• 고아 관계 삭제             : {healed['dangling_relations_removed']} 건")
            print(f"• 엔티티 출처 참조 정제       : {healed['stale_entity_sources_cleaned']} 건")
            print(f"• 관계 출처 참조 정제         : {healed['stale_relation_sources_cleaned']} 건")
            print(f"• 고아/유령 엔티티 정리       : {healed['ghost_entities_pruned']} 건")
            print(f"• 고아 임베딩 삭제           : {healed['orphan_embeddings_removed']} 건")
            print(f"• FTS 전문 색인 재구축        : {healed['fts_reindexed']} 건")
            print("=" * 50)
            print("doctor: 수복 완료 (Graph is fully healed!)")
            return 0

        report = dbm.diagnose_graph(conn)
        if getattr(args, "json", False):
            import json

            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["is_healthy"] else 1

        print("claire doctor: 지식그래프 및 DB 무결성 진단 보고서")
        print("=" * 50)
        print(f"• 전체 문서 수               : {report['total_documents']} 건")
        print(f"• 전체 엔티티 수             : {report['total_entities']} 건")
        print(f"• 전체 관계 수               : {report['total_relations']} 건")
        print("-" * 50)
        print(
            f"• 고아 관계 (Dangling)       : {report['dangling_relations_count']} 건"
            + (" [!]" if report["dangling_relations_count"] else " [✓]")
        )
        print(
            f"• 엔티티 유효하지 않은 출처 : {report['stale_entity_sources_count']} 건"
            + (" [!]" if report["stale_entity_sources_count"] else " [✓]")
        )
        print(
            f"• 관계 유효하지 않은 출처   : {report['stale_relation_sources_count']} 건"
            + (" [!]" if report["stale_relation_sources_count"] else " [✓]")
        )
        print(
            f"• 고아/유령 엔티티           : {report['ghost_entities_count']} 건"
            + (" [!]" if report["ghost_entities_count"] else " [✓]")
        )
        print(
            f"• 고아 임베딩                : {report['orphan_embeddings_count']} 건"
            + (" [!]" if report["orphan_embeddings_count"] else " [✓]")
        )
        print(f"• 임베딩 누락 엔티티         : {report['missing_embeddings_count']} 건")
        print(f"• FTS 색인 불일치 여부       : {'[!] 불일치' if report['fts_desync'] else '[✓] 동기화됨'}")
        if report.get("purged_tombstones_count"):
            print(f"• 등록된 소각 툼스톤         : {report['purged_tombstones_count']} 건")
        if report.get("tombstone_violations_count"):
            print(
                f"• 툼스톤 위반 (부활 문서)    : {report['tombstone_violations_count']} 건 [!] (claire purge 로 재소각 필요)"
            )
        if report["corrupted_summaries_count"]:
            print(
                f"• ADOC 문법 잔존 요약       : {report['corrupted_summaries_count']} 건 [!] (claire regenerate 로 재생성 가능)"
            )
        print("=" * 50)

        if report["is_healthy"]:
            print("doctor: OK (지식그래프 및 DB 무결성이 완벽합니다)")
            return 0
        else:
            print("[!] 그래프 무결성 문제가 발견되었습니다.")
            print("    자동 수복을 실행하려면 다음 명령을 실행하십시오:")
            print("    claire doctor --heal")
            return 0
    finally:
        conn.close()


def cmd_purge(args) -> int:
    """오염된 레거시 문서를 툼스톤 등록과 함께 원자적으로 연쇄 소각."""
    s = get_settings()
    if not s.is_purge_allowed:
        print("claire purge: [오류] 데이터 소각이 정책에 의해 차단되었습니다.", file=sys.stderr)
        print("현재 환경의 데이터 수명주기(CLAIRE_DATA_LIFECYCLE)가 'append-only' 모드로 설정되어 있습니다.", file=sys.stderr)
        print("오염 데이터 소각을 활성화하려면 .env 파일에 다음 설정을 적용하십시오:", file=sys.stderr)
        print("  CLAIRE_DATA_LIFECYCLE=purgeable", file=sys.stderr)
        print("  (또는 CLAIRE_ALLOW_PURGE=1)", file=sys.stderr)
        return 1

    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    try:
        # 대상 문서 식별
        target_ids: list[str] = []
        target = getattr(args, "target", None)
        doc_id = getattr(args, "doc_id", None)
        url = getattr(args, "url", None)
        pattern = getattr(args, "pattern", None)
        canonical_url = getattr(args, "canonical_url", None)

        if doc_id:
            target_ids.append(doc_id)
        if target:
            if target.startswith(("http://", "https://")):
                url = target
            else:
                target_ids.append(target)
        if url:
            from .ingest.normalize import canonicalize_url

            c_url = canonicalize_url(url)
            rows = conn.execute(
                "SELECT id FROM documents WHERE url=? OR canonical_url=?", (url, c_url)
            ).fetchall()
            for r in rows:
                if r["id"] not in target_ids:
                    target_ids.append(r["id"])
        if canonical_url:
            rows = conn.execute(
                "SELECT id FROM documents WHERE canonical_url=?", (canonical_url,)
            ).fetchall()
            for r in rows:
                if r["id"] not in target_ids:
                    target_ids.append(r["id"])
        if pattern:
            patt = f"%{pattern}%"
            rows = conn.execute(
                "SELECT id FROM documents WHERE id LIKE ? OR url LIKE ? OR canonical_url LIKE ? OR title LIKE ? OR raw_text LIKE ?",
                (patt, patt, patt, patt, patt),
            ).fetchall()
            for r in rows:
                if r["id"] not in target_ids:
                    target_ids.append(r["id"])

        if not target_ids:
            print("claire purge: 소각할 대상 문서를 찾을 수 없습니다.")
            return 0

        force = getattr(args, "force", False) or getattr(args, "apply", False) or getattr(args, "yes", False)
        reason = getattr(args, "reason", "manual_purge") or "manual_purge"

        if not force:
            report = dbm.purge_document_cascade(
                conn, data_dir=s.data_dir, vault_dir=s.vault_dir, target_ids=target_ids, reason=reason, dry_run=True
            )
            if getattr(args, "json", False):
                import json

                print(json.dumps(report, ensure_ascii=False, indent=2))
                return 0

            print("claire purge: [Dry-Run] 소각 대상 분석 보고서 (실제 삭제 안 됨)")
            print("=" * 60)
            print(f"• 소각 대상 문서 수         : {report['purged_count']} 건")
            print(f"• 삭제 대상 디스크 파일     : {report['disk_files_count']} 개")
            print("-" * 60)
            print("대상 문서 목록:")
            for d in report["target_documents"]:
                print(f"  - [{d['id']}] {d.get('title') or '(제목 없음)'} ({d.get('url') or 'no-url'})")
            print("=" * 60)
            print("[안내] 실제 소각 및 DB 물리 압축(VACUUM)을 실행하려면 --force 옵션을 추가하십시오:")
            print("  claire purge --force " + " ".join(f"'{did}'" for did in target_ids))
            return 0

        # 실행
        print("claire purge: [소각 시작] 원자적 연쇄 소각 및 지식그래프 정화 중...")
        report = dbm.purge_document_cascade(
            conn, data_dir=s.data_dir, vault_dir=s.vault_dir, target_ids=target_ids, reason=reason, dry_run=False
        )
        if getattr(args, "json", False):
            import json

            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0

        print("=" * 60)
        print(f"• 소각된 문서 수 (DB)       : {report['deleted_documents']} 건")
        print(f"• 등록된 툼스톤 (재유입방지): {report['purged_count']} 건")
        print(f"• 삭제된 L1 인박스 레코드   : {report.get('deleted_raw_inbox', 0)} 건")
        print(f"• 삭제된 L2 디스크 파일     : {report['disk_files_unlinked']} 개")
        gh = report.get("graph_healed", {})
        print(f"• 정제된 엔티티 출처        : {gh.get('stale_entity_sources_cleaned', 0)} 건")
        print(f"• 소각된 고아 엔티티        : {gh.get('ghost_entities_pruned', 0)} 건")
        print(f"• 소각된 고아 관계          : {gh.get('dangling_relations_removed', 0)} 건")
        print(f"• 소각된 고아 임베딩        : {gh.get('orphan_embeddings_removed', 0)} 건")
        print(f"• FTS 재구축                : {gh.get('fts_reindexed', 0)} 건")
        print(f"• DB VACUUM (용량 회수)     : {'완료' if report.get('vacuum_executed') else '스킵/실패'}")
        print("=" * 60)
        print("[✓] 오염 데이터가 시스템 전체에서 완전히 소각되었습니다.")
        return 0
    finally:
        conn.close()


def cmd_audit(args) -> int:
    """오염 잔재 0건 여부 및 시스템 무결성 전수 감사."""
    s = get_settings()
    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    try:
        pattern = getattr(args, "target", None) or getattr(args, "pattern", None)
        report = dbm.audit_residuals(conn, data_dir=s.data_dir, pattern_or_id=pattern)
        if getattr(args, "json", False):
            import json

            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["clean"] else 1

        print("claire audit: 시스템 무결성 및 잔재 전수 감사 보고서")
        print("=" * 60)
        if pattern:
            print(f"• 검사 패턴/키워드          : '{pattern}'")
            print(f"• 잔존 문서 수              : {report['matching_documents_count']} 건" + (" [!]" if report["matching_documents_count"] else " [✓] 0건"))
            print(f"• 잔존 L1 인박스            : {report['matching_inbox_count']} 건" + (" [!]" if report["matching_inbox_count"] else " [✓] 0건"))
            print(f"• 잔존 L2 디스크 아티팩트   : {report['matching_disk_artifacts_count']} 개" + (" [!]" if report["matching_disk_artifacts_count"] else " [✓] 0개"))
            print(f"• 잔존 이미지 파일          : {report['matching_disk_images_count']} 개" + (" [!]" if report["matching_disk_images_count"] else " [✓] 0개"))
            print(f"• 엔티티 sources 잔존 참조  : {report['matching_entity_sources_count']} 건" + (" [!]" if report["matching_entity_sources_count"] else " [✓] 0건"))
            print(f"• 관계 sources 잔존 참조    : {report['matching_relation_sources_count']} 건" + (" [!]" if report["matching_relation_sources_count"] else " [✓] 0건"))
            print("-" * 60)
        print(f"• 등록된 툼스톤 수          : {report['purged_tombstones_count']} 건")
        print(f"• 툼스톤 위반 (부활된 문서) : {report['tombstone_violations_count']} 건" + (" [!]" if report["tombstone_violations_count"] else " [✓] 0건"))
        reclaim_kb = report["reclaimable_bytes"] / 1024
        print(f"• DB Freelist (미회수 공간) : {report['freelist_pages']} pages ({reclaim_kb:.1f} KB)")
        print("=" * 60)
        if report["clean"]:
            print("[✓] 클린: 오염 데이터의 잔재가 발견되지 않았습니다.")
            return 0
        else:
            print("[!] 경고: 오염 잔재 또는 툼스톤 위반 항목이 검출되었습니다.")
            return 1
    finally:
        conn.close()


def cmd_stats(_args) -> int:
    s = get_settings()
    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    c = dbm.counts(conn)
    for k, v in c.items():
        print(f"{k:12s}: {v}")
    conn.close()
    return 0


def cmd_status(_args) -> int:
    """현황 한눈에: 운영(설정/벡터/모델) · DB(그래프 규모/소스/타입) · 진행(inbox/연결)."""
    from .status import build_status_text

    print(build_status_text(get_settings(), full=True))
    return 0


def cmd_queue(args) -> int:
    """비동기 큐 (inbox / refresh / expand) 상태 및 대기/실패 항목 조회."""
    import json
    from .status import build_queue_dashboard

    s = get_settings()
    queue_name = getattr(args, "name", None)
    limit = getattr(args, "limit", 20) or 20

    if getattr(args, "json", False):
        conn = dbm.connect(s.db_file)
        dbm.init_db(conn)
        try:
            data = {
                "inbox": dbm.inbox_status_counts(conn),
                "refresh": dbm.refresh_status_counts(conn),
                "expand": dbm.expand_status_counts(conn),
            }
            print(json.dumps(data, ensure_ascii=False, indent=2))
            return 0
        finally:
            conn.close()

    print(build_queue_dashboard(s, queue_name=queue_name, limit=limit))
    return 0


def cmd_repo(_args) -> int:
    """소스 리포지토리 정보와 접근 URL 출력."""
    s = get_settings()
    print(f"Repository : {s.effective_github_repository}")
    print(f"Source URL : {s.effective_source_base_url}")
    return 0


def cmd_bot(_args) -> int:
    from .telegram_bot import run_bot

    return run_bot()


def cmd_serve_api(_args) -> int:
    from .api.server import run_api

    return run_api()


def cmd_replay_failed(args) -> int:
    from .ingest.service import IngestService

    s = get_settings()
    svc = IngestService(s)
    results = svc.replay_failed(limit=args.limit)
    if not results:
        print("재적재할 실패 항목 없음.")
        return 0
    ok = sum(1 for _, r in results if r.error is None and not r.duplicate)
    for inbox_id, r in results:
        status = "OK" if r.error is None else f"ERR:{r.error[:50]}"
        print(f"  inbox#{inbox_id}: {status}")
    print(f"재적재 {len(results)}건 중 성공 {ok}")
    return 0


def cmd_recover_run(args) -> int:
    """[자동복구] error inbox 중 재시도 도래분을 1회 재적재(게이팅·지수백오프)."""
    from .ingest.service import IngestService
    from .progress import track_batch_progress

    s = get_settings()
    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    due_count = len(dbm.due_for_recovery(conn, max_attempts=args.max_attempts, limit=args.limit))
    conn.close()

    if due_count == 0:
        print("재적재 대상(도래분) 없음.")
        return 0

    svc = IngestService(s)
    try:
        with track_batch_progress("수신 실패 자동 복구", due_count) as reporter:
            results = svc.recover_failed(
                max_attempts=args.max_attempts, base_delay=args.base_delay, limit=args.limit,
                reporter=reporter)
            reporter.print_summary()
    except KeyboardInterrupt:
        return 130

    for r in results:
        if r["status"] in ("done", "duplicate"):
            print(f"  ✅ inbox#{r['inbox_id']}: {r['status']}")
        elif r["status"] == "failed":
            print(f"  ⛔ inbox#{r['inbox_id']}: 영구실패 {str(r.get('error',''))[:50]}")
        else:
            print(f"  ↻ inbox#{r['inbox_id']}: 재시도예약 {str(r.get('error',''))[:50]}")
    ok = sum(1 for r in results if r["status"] in ("done", "duplicate"))
    print(f"처리 {len(results)}건 중 복구 {ok}")
    return 0


def _alert_permanent_failures(failed_items: list[dict]) -> None:
    """영구실패 inbox 를 소유자에게 텔레그램으로 알림(미설정/실패해도 루프는 계속)."""
    from .notify import notify_owner

    s = get_settings()
    lines = [f"⛔ claire 자동복구 영구실패 {len(failed_items)}건 (재시도 상한 도달)"]
    for r in failed_items[:5]:
        lines.append(f"• inbox#{r['inbox_id']}: {str(r.get('error', ''))[:100]}")
    if len(failed_items) > 5:
        lines.append(f"… 외 {len(failed_items) - 5}건")
    lines.append("텔레그램 `/failed` 로 목록 확인 후 `/retry <번호>` 로 재시도하세요.")
    sent = notify_owner(s.telegram_bot_token, s.notify_chat_id, "\n".join(lines))
    print(f"[recover] 영구실패 알림 {'전송' if sent else '미전송(미설정/실패)'}",
          flush=True)


def cmd_recover_loop(args) -> int:
    """error inbox 를 interval 초마다 자동 재적재(전용 컨테이너용 데몬)."""
    import time

    from .ingest.service import IngestService

    svc = IngestService(get_settings())
    print(f"claire recover-loop 시작 (interval={args.interval}s, batch={args.batch}, "
          f"max_attempts={args.max_attempts}). Ctrl+C 종료.", flush=True)
    while True:
        try:
            results = svc.recover_failed(
                max_attempts=args.max_attempts, base_delay=args.base_delay,
                limit=args.batch)
            if results:
                ok = sum(1 for r in results if r["status"] in ("done", "duplicate"))
                failed_items = [r for r in results if r["status"] == "failed"]
                print(f"[recover] {len(results)}건 시도, 복구 {ok}, 영구실패 "
                      f"{len(failed_items)}", flush=True)
                # 영구실패(=재시도 상한 도달)는 사람이 봐야 하는 신호 → 소유자 DM 경보.
                if failed_items:
                    _alert_permanent_failures(failed_items)
            else:
                print("[recover] 재적재 대상 없음, 대기", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[recover] 오류: {e}", flush=True)
        time.sleep(max(60, args.interval))


def cmd_refresh_mark(args) -> int:
    """갱신 대상 문서를 큐에 등록(기본: 본문 빈약 문서)."""
    from .ingest.service import IngestService

    svc = IngestService(get_settings())
    n = svc.mark_thin_for_refresh(
        max_len=args.max_len, host=args.host, reason=args.reason,
        include_partial=args.include_partial)
    print(f"갱신 대상 {n}건 등록 (max_len<{args.max_len}"
          + (f", host={args.host}" if args.host else "")
          + (", +partial" if args.include_partial else "")
          + f", reason={args.reason})")
    return 0


def cmd_refresh_run(args) -> int:
    """갱신 큐 1회 처리."""
    from .ingest.service import IngestService
    from .progress import track_batch_progress

    s = get_settings()
    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    rows = dbm.pending_refresh(conn, limit=args.limit)
    conn.close()

    if not rows:
        print("갱신 대기 항목 없음.")
        return 0

    svc = IngestService(s)
    try:
        with track_batch_progress("문서 갱신 큐 처리", len(rows)) as reporter:
            results = svc.run_refresh_queue(limit=args.limit, reporter=reporter)
            reporter.print_summary()
    except KeyboardInterrupt:
        return 130

    for r in results:
        if r["status"] == "done":
            print(f"  ✅ {r['document_id']}: {r['old_len']}→{r['new_len']}자 "
                  f"(신규 {r.get('entities_created',0)}·연결 {r.get('entities_linked',0)})")
        elif r["status"] == "nochange":
            print(f"  = {r['document_id']}: 변화 없음({r['new_len']}자)")
        else:
            print(f"  ❌ {r['document_id']}: {r.get('error','')[:60]}")
    done = sum(1 for r in results if r["status"] == "done")
    print(f"처리 {len(results)}건 중 갱신 {done}")
    return 0


def cmd_refresh_loop(args) -> int:
    """갱신 큐를 interval 초마다 자동 처리(전용 컨테이너용 데몬)."""
    import time

    from .ingest.service import IngestService

    svc = IngestService(get_settings())
    print(f"claire refresh-loop 시작 (interval={args.interval}s, batch={args.batch}). Ctrl+C 종료.", flush=True)
    while True:
        try:
            due = svc.enqueue_due_watch(limit=args.batch)  # 주기 크롤링: due watch → 큐 등록
            if due:
                print(f"[refresh] watch 재크롤 {due}건 큐 등록", flush=True)
            results = svc.run_refresh_queue(limit=args.batch)
            if results:
                done = sum(1 for r in results if r["status"] == "done")
                print(f"[refresh] {len(results)}건 처리, 갱신 {done}", flush=True)
            else:
                # 큐가 비어도 살아있음을 알리는 heartbeat(로그 가시성 확보).
                print("[refresh] 큐 비어있음, 대기", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[refresh] 오류: {e}", flush=True)
        time.sleep(max(60, args.interval))


def _notify_expansion(results: list[dict]) -> None:
    """1홉 자동확장으로 새 지식이 쌓였으면 소유자에게 텔레그램 알림(실패해도 루프 계속)."""
    from .notify import notify_owner

    stored = [r for r in results if r.get("stored")]
    if not stored:
        return
    s = get_settings()
    total = sum(r["stored"] for r in stored)
    lines = [f"🔗 1홉 자동확장: {total}개 링크를 따라가 지식에 추가했습니다."]
    for r in stored[:5]:
        for f in r.get("followed", []):
            if f.get("stored"):
                lines.append(f"• {f.get('title') or f.get('url')}")
    sent = notify_owner(s.telegram_bot_token, s.notify_chat_id, "\n".join(lines[:12]))
    print(f"[expand] 적재 알림 {'전송' if sent else '미전송(미설정/실패)'}", flush=True)


def cmd_expand_run(args) -> int:
    """1홉 자동확장 큐 1회 처리."""
    from .ingest.service import IngestService
    from .progress import track_batch_progress

    s = get_settings()
    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    rows = dbm.pending_expand(conn, limit=args.limit)
    conn.close()

    if not rows:
        print("확장 대기 항목 없음.")
        return 0

    svc = IngestService(s)
    try:
        with track_batch_progress("1홉 자동확장 큐 처리", len(rows)) as reporter:
            results = svc.run_expand_queue(limit=args.limit, reporter=reporter)
            reporter.print_summary()
    except KeyboardInterrupt:
        return 130

    for r in results:
        if r.get("error"):
            print(f"  ❌ {r.get('document_id', '?')}: {str(r['error'])[:60]}")
        else:
            print(f"  🔗 {r.get('document_id', '?')}: 후보 {r.get('candidates', 0)}·선별 {r.get('selected', 0)}"
                  f"·적재 {r.get('stored', 0)}·스킵 {r.get('skipped', 0)}")
    print(f"처리 {len(results)}건, 적재 {sum(r.get('stored',0) for r in results)}")
    return 0


def cmd_expand_loop(args) -> int:
    """1홉 자동확장 큐를 interval 초마다 자동 처리(전용 컨테이너용 데몬)."""
    import time

    from .ingest.service import IngestService

    svc = IngestService(get_settings())
    print(f"claire expand-loop 시작 (interval={args.interval}s, batch={args.batch}). "
          "Ctrl+C 종료.", flush=True)
    while True:
        try:
            results = svc.run_expand_queue(limit=args.batch)
            if results:
                stored = sum(r.get("stored", 0) for r in results)
                print(f"[expand] {len(results)}건 처리, 적재 {stored}", flush=True)
                _notify_expansion(results)
            else:
                print("[expand] 큐 비어있음, 대기", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[expand] 오류: {e}", flush=True)
        time.sleep(max(60, args.interval))


def cmd_ingest(args) -> int:
    from .extract.provider import get_provider
    from .ingest.pipeline import ingest
    from .store.vectors import make_vector_store

    s = get_settings()
    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    provider = get_provider(s)
    vstore = make_vector_store(conn, s.vector_backend)
    print(f"(provider={provider.name})")
    directive = getattr(args, "orientation", None) or getattr(args, "directive", None)
    report = ingest(
        args.payload, conn=conn, provider=provider, vstore=vstore,
        vault_dir=s.vault_dir, data_dir=s.data_dir, source="cli",
        expand_max=(0 if args.no_expand else s.expand_max),
        format=getattr(args, "format", None),
        directive=directive,
    )
    print(report.telegram_summary())

    # 1홉 확장 후보: CLI 에서는 --expand 플래그로 즉시 재귀 적재(상한 내).
    if report.candidates and args.expand:
        print(f"\n[expand] {len(report.candidates)}개 후보 적재 중…")
        for url in report.candidates:
            sub = ingest(url, conn=conn, provider=provider, vstore=vstore,
                         vault_dir=s.vault_dir, data_dir=s.data_dir, source="cli-expand",
                         expand_max=0, format=getattr(args, "format", None),
                         directive=directive)  # 2홉 방지
            print(f"  - {url}\n    {sub.telegram_summary().splitlines()[0]}")
    elif report.candidates:
        print("\n[expand] 후보 URL (적재하려면 --expand):")
        for url in report.candidates:
            print(f"  - {url}")

    conn.close()
    return 0 if report.error is None else 1


def cmd_reextract(args) -> int:
    """저장된 raw_text 로 전체 또는 표(Table) 포함 문서를 재추출(프롬프트 변경 반영, 예: 한글화, 표 보존)."""
    from .ingest.service import IngestService
    from .progress import track_batch_progress

    s = get_settings()
    svc = IngestService(s)
    tables_only = getattr(args, "tables", False) or getattr(args, "has_tables", False)

    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    if tables_only:
        table_docs = dbm.documents_with_tables(conn, limit=args.limit, check_detail=False)
        total_docs = len(table_docs)
    else:
        total_docs = len(dbm.documents_timeline(conn, args.limit or 1000000))
    conn.close()

    if total_docs == 0:
        print("재추출 대상 문서 없음.")
        return 0

    try:
        with track_batch_progress(f"문서 재추출 ({svc.provider.name})", total_docs) as reporter:
            out = svc.reextract_all(
                rebuild=not args.no_rebuild,
                limit=args.limit,
                format=getattr(args, "format", None),
                tables_only=tables_only,
                reporter=reporter,
            )
            reporter.print_summary()
    except KeyboardInterrupt:
        return 130

    print(f"재추출 완료: 문서 {out['docs']} · 성공 {out['ok']} · 실패 {out['failed']}")
    for e in out["errors"][:20]:
        print(f"  - 실패 {e['document_id']}: {e['error']}")
    return 0 if out["failed"] == 0 else 1


def cmd_backfill_detail(args) -> int:
    """detail(한국어 가독 렌더링)이 없는 문서 또는 표(Table) 포함 문서를 채운다 — 비파괴적(그래프 불변)."""
    from .ingest.service import IngestService
    from .progress import track_batch_progress

    s = get_settings()
    svc = IngestService(s)
    tables_only = getattr(args, "tables", False) or getattr(args, "has_tables", False)
    fmt = getattr(args, "format", None) or s.render_format

    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    if tables_only:
        target_ids = [d["id"] for d in dbm.documents_with_tables(conn, limit=args.limit, check_detail=True)]
    elif args.force:
        target_ids = [r["id"] for r in dbm.documents_timeline(conn, args.limit or 1000000)]
    else:
        target_ids = dbm.documents_needing_detail_format(conn, fmt, args.limit)
    conn.close()

    total_docs = len(target_ids)
    if total_docs == 0:
        print("detail 백필 대상 문서 없음.")
        return 0

    directive = getattr(args, "orientation", None) or getattr(args, "directive", None)
    try:
        with track_batch_progress(f"detail 백필 ({svc.provider.name})", total_docs) as reporter:
            out = svc.backfill_details(
                limit=args.limit,
                force=args.force,
                format=fmt,
                directive=directive,
                tables_only=tables_only,
                reporter=reporter,
            )
            reporter.print_summary()
    except KeyboardInterrupt:
        return 130

    print(f"백필 완료: 대상 {out['docs']} · 생성 {out['ok']} · 건너뜀 {out['skipped']}")
    return 0


def cmd_backfill_summary(args) -> int:
    """요약(summary)이 비어있거나 누락된 기존 문서의 요약을 채운다 — 비파괴적."""
    from .ingest.service import IngestService
    from .progress import track_batch_progress

    s = get_settings()
    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    total_docs = len(dbm.documents_timeline(conn, args.limit or 1000000))
    conn.close()

    if total_docs == 0:
        print("요약 백필 대상 문서 없음.")
        return 0

    svc = IngestService(s)
    try:
        with track_batch_progress("요약(summary) 백필", total_docs) as reporter:
            out = svc.backfill_summaries(limit=args.limit, reporter=reporter)
            reporter.print_summary()
    except KeyboardInterrupt:
        return 130

    print(f"요약 백필 완료: 전체 {out['docs']} · 신규/갱신 {out['filled']} · 기존 유지 {out['already_had']}")
    return 0


def cmd_regenerate(args) -> int:
    """문서 파생 데이터(요약, 본문 등)를 선택적으로 재생성(기본 dry-run, --force 로 적용)."""
    import json

    from .ingest.service import IngestService
    from .progress import track_batch_progress

    s = get_settings()
    svc = IngestService(s)

    target = getattr(args, "target", None)
    token = getattr(args, "token", None)
    doc_id = getattr(args, "doc_id", None)
    summary = getattr(args, "summary", False)
    detail = getattr(args, "detail", False)
    graph = getattr(args, "graph", False)
    all_comp = getattr(args, "all", False)
    corrupted = getattr(args, "corrupted", False)
    tables = getattr(args, "tables", False) or getattr(args, "has_tables", False)
    refetch = getattr(args, "refetch", False)
    force = getattr(args, "force", False)
    effort = getattr(args, "effort", None)
    fmt = getattr(args, "format", None)
    directive = getattr(args, "orientation", None) or getattr(args, "directive", None)

    if not force:
        # Dry-run 진단
        res = svc.regenerate_components(
            target=target,
            token=token,
            doc_id=doc_id,
            summary=summary,
            detail=detail,
            graph=graph,
            all_components=all_comp,
            corrupted_summary=corrupted,
            tables=tables,
            refetch=refetch,
            force=False,
            effort=effort,
            format=fmt,
            directive=directive,
        )

        if getattr(args, "json", False):
            print(json.dumps(res, ensure_ascii=False, indent=2))
            return 0

        if res.get("error"):
            print(f"[!] {res['error']}")
            return 0

        print("claire: [Dry-Run] 문서 컴포넌트 재생성 진단")
        print("=" * 60)
        print(f"• 탐지/선택된 대상 문서 수 : {res['count']}건")
        for idx, t in enumerate(res["targets"], 1):
            print(f"[{idx}] {t['title']} ({t['document_id']})")
            if t.get("canonical_url"):
                print(f"    URL: {t['canonical_url']}")
            if t.get("is_error_page"):
                print("    [!] 원문이 SSL/보안 오류 화면으로 감지됨 (--refetch 함께 사용 권장)")
            if t.get("summary_corrupted"):
                print("    [!] 요약 내 ADOC/마크업 문법 잔존 감지")
            if t.get("total_tables"):
                print(f"    [📊] 표 {t['total_tables']}개 감지 (원문: {t.get('raw_tables_count',0)}개, 본문: {t.get('detail_tables_count',0)}개)")
                if t.get("table_preview"):
                    first_line = t["table_preview"].splitlines()[0]
                    print(f"         표 미리보기: {first_line}")
            summ_preview = (t['current_summary'][:150] + '...') if len(t['current_summary']) > 150 else t['current_summary']
            print(f"    현재 요약: {summ_preview}")
            print(f"    적용 예정 작업: {', '.join(t['actions'])}")
        print("=" * 60)
        print("실제 재생성 및 DB 덮어쓰기를 실행하려면 --force 플래그를 추가하십시오:")
        print("  claire regenerate [target] --all --force [--refetch] [--effort <level>]")
        return 0

    # Force 실행: 사전 진단으로 대상 수 특정 후 실시간 진행률 추적
    diag = svc.regenerate_components(
        target=target,
        token=token,
        doc_id=doc_id,
        summary=summary,
        detail=detail,
        graph=graph,
        all_components=all_comp,
        corrupted_summary=corrupted,
        tables=tables,
        refetch=refetch,
        force=False,
        effort=effort,
        format=fmt,
        directive=directive,
    )
    if diag.get("error"):
        print(f"[!] {diag['error']}")
        return 1

    total_targets = diag.get("count", 0)
    try:
        with track_batch_progress("문서 컴포넌트 재생성", total_targets) as reporter:
            res = svc.regenerate_components(
                target=target,
                token=token,
                doc_id=doc_id,
                summary=summary,
                detail=detail,
                graph=graph,
                all_components=all_comp,
                corrupted_summary=corrupted,
                tables=tables,
                refetch=refetch,
                force=True,
                effort=effort,
                format=fmt,
                directive=directive,
                reporter=reporter,
            )
            reporter.print_summary()
    except KeyboardInterrupt:
        return 130

    if getattr(args, "json", False):
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    print("claire: 문서 컴포넌트 재생성 완료")
    print("=" * 60)
    print(f"• 갱신된 문서 수 : {res['count']}건")
    print(f"• 사용 Provider  : {res.get('provider')} (model: {res.get('model')}, effort: {res.get('effort')})")
    for idx, t in enumerate(res["targets"], 1):
        print(f"[{idx}] {t['title']} ({t['document_id']})")
        if t.get("refetched"):
            print(f"    원문 재수집: 완료 ({t.get('new_len', 0)}자)")
        elif t.get("refetch_error"):
            print(f"    [!] 원문 재수집 실패: {t.get('refetch_error')}")
        if t.get("total_tables"):
            print(f"    표 보존/추출 : 총 {t['total_tables']}개 표 반영")
        if t.get("entities_created") or t.get("entities_linked"):
            created = t.get("entities_created", 0)
            linked = t.get("entities_linked", 0)
            names = (t.get("new_entity_names") or []) + (t.get("linked_entity_names") or [])
            preview = ", ".join(names[:6]) + ("..." if len(names) > 6 else "")
            print(f"    노드 추출  : 신규 {created}건 · 연결 {linked}건 ({preview})")
        if t.get("relations_added"):
            print(f"    관계 적재  : {t.get('relations_added')}건")
        if t.get("new_summary"):
            print(f"    새 요약: {t['new_summary']}")
    print("=" * 60)
    return 0
    return 0


def cmd_format_status(args) -> int:
    """문서 본문 렌더링 포맷(detail_format) 진단 현황을 출력."""
    import json

    s = get_settings()
    target_format = getattr(args, "format", None) or s.render_format
    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    try:
        status = dbm.get_format_status(conn, target_format)
        if getattr(args, "json", False):
            print(json.dumps(status, ensure_ascii=False, indent=2))
        else:
            fmt_label = status["target_format"].upper()
            print(f"목표 포맷        : {fmt_label}")
            print(f"전체 문서 수    : {status['total_docs']}건")
            print(f"  - 목표 포맷 일치: {status['matching_docs']}건")
            print(f"  - 포맷 불일치  : {status['mismatched_docs']}건")
            print(f"  - detail 누락 : {status['missing_detail_docs']}건")
            print(f"마이그레이션 대상: {status['target_docs']}건 (needs_migration={status['needs_migration']})")
        return 0
    finally:
        conn.close()


def cmd_format_migrate(args) -> int:
    """문서 렌더링 포맷(detail) 마이그레이션 진단 및 적용 (기본: dry-run, --apply 로 적용)."""
    import sys
    from .ingest.service import IngestService

    s = get_settings()
    target_format = getattr(args, "format", None) or s.render_format
    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    try:
        status = dbm.get_format_status(conn, target_format)
    finally:
        conn.close()

    fmt_label = status["target_format"].upper()
    other_label = "MD" if status["target_format"] == "adoc" else "ADOC"

    if getattr(args, "json", False):
        import json

        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0

    print("claire: [포맷 마이그레이션 진단 현황]")
    print("=" * 60)
    print(f"• 설정된 목표 포맷 (.env)  : {fmt_label} (CLAIRE_RENDER_FORMAT={status['target_format']})")
    print(f"• 전체 문서 수             : {status['total_docs']} 건")
    print(f"  - 목표 포맷 일치         : {status['matching_docs']} 건 ({fmt_label})")
    print(f"  - 포맷 불일치 (변환 대상) : {status['mismatched_docs']} 건 ({other_label})")
    print(f"  - 본문(detail) 누락     : {status['missing_detail_docs']} 건")
    print(f"• 총 마이그레이션 대상     : {status['target_docs']} 건")
    print("=" * 60)

    apply = getattr(args, "apply", False)
    dry_run = getattr(args, "dry_run", False)

    if not apply or dry_run:
        print("\n[안내] 기본 Dry-Run 모드로 실행되어 실제 변경을 적용하지 않았습니다.")
        print("실제 마이그레이션을 적용하려면 --apply 옵션을 사용하십시오:")
        print(f"  claire format-migrate --apply --format {status['target_format']}")
        return 0

    if status["non_target_docs"] == 0:
        print(f"\n[✓] 모든 문서가 이미 목표 포맷({fmt_label})입니다. 마이그레이션이 필요하지 않습니다.")
        return 0

    confirmed = getattr(args, "yes", False)
    if not confirmed:
        if sys.stdin.isatty():
            try:
                answer = input(f"\n위 계획대로 포맷 마이그레이션을 진행하시겠습니까? [y/N]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\n마이그레이션 작업이 취소되었습니다.")
                return 0
            if answer not in ("y", "yes"):
                print("마이그레이션 작업이 취소되었습니다.")
                return 0
        else:
            print("\n비대화형 환경에서는 --yes (-y) 옵션을 명시하여 실행하십시오:")
            print(f"  claire format-migrate --apply --yes --format {status['target_format']}")
            return 2

    svc = IngestService(s)
    from .progress import track_batch_progress

    try:
        with track_batch_progress(f"포맷 마이그레이션 ({fmt_label})", status["non_target_docs"]) as reporter:
            out = svc.backfill_details(limit=0, force=False, format=status["target_format"], reporter=reporter)
            reporter.print_summary()
    except KeyboardInterrupt:
        return 130

    print(f"[✓] 포맷 마이그레이션 완료: 대상 {out['docs']} · 생성 {out['ok']} · 건너뜀 {out['skipped']}")
    return 0


def cmd_recompile_html(args) -> int:
    """모든 문서의 detail_html을 현재 AOT 사전 렌더러로 재컴파일(LLM 호출 없음)."""
    s = get_settings()
    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    try:
        count = dbm.recompile_all_detail_html(conn)
        print(f"AOT HTML 재컴파일 완료: 총 {count}건 문서 갱신")
        return 0
    finally:
        conn.close()


def cmd_backfill_images(args) -> int:
    """본문 이미지가 없는 기존 문서를 재fetch 대상(refresh 큐)으로 등록.

    실제 재fetch·이미지 수집·detail 재생성은 refresh-loop(claire_refresh 컨테이너)가
    **며칠에 걸쳐 천천히** 처리한다(quota 부담 분산). 본문 안 바뀐 문서는 그래프 불변.
    """
    from .ingest.service import IngestService

    s = get_settings()
    svc = IngestService(s)
    n = svc.mark_all_for_image_backfill(limit=args.limit)
    print(f"이미지 백필 등록: {n}건 (refresh 큐). refresh-loop 가 천천히 재fetch 처리합니다.")
    return 0


def cmd_watch(args) -> int:
    """[주기 크롤링] 문서 watch(주기 재크롤) 수동 on/off·주기·목록·상태.

    watch 대상은 refresh-loop 가 주기적으로 재fetch → 내용 바뀌면 변경 전 원문을 스냅샷
    보존 + 그래프 최신 갱신 + unseen. LLM 자동판단을 사람이 덮어쓸 때 사용."""
    from .store import db as dbm

    s = get_settings()
    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    if args.list:
        rows = conn.execute(
            "SELECT id,title,watch_interval,last_watched_at,watch_reason "
            "FROM documents WHERE watch_enabled=1 ORDER BY COALESCE(last_watched_at,0)"
        ).fetchall()
        if not rows:
            print("watch 문서 없음")
        for r in rows:
            iv = f"{(r['watch_interval'] or 0) / 86400:.1f}일" if r["watch_interval"] else "기본주기"
            print(f"  {r['id']}  주기={iv}  {r['title'] or ''}  [{r['watch_reason'] or ''}]")
        return 0
    if not args.document_id:
        print("document_id 가 필요합니다 (또는 --list)")
        return 1
    if args.on and args.off:
        print("--on 과 --off 는 동시에 쓸 수 없습니다")
        return 1
    enabled = True if args.on else (False if args.off else None)
    interval = float(args.interval_days) * 86400 if args.interval_days else None
    if enabled is None and interval is None:
        r = dbm.get_document_row(conn, args.document_id)
        if r is None:
            print("문서 없음")
            return 1
        print(f"watch_enabled={r['watch_enabled']} interval={r['watch_interval']} "
              f"last_watched_at={r['last_watched_at']} reason={r['watch_reason']}")
        return 0
    dbm.set_document_watch(conn, args.document_id, enabled=enabled,
                           interval=interval, reason="manual")
    print(f"watch 설정: {args.document_id} enabled={enabled} interval_days={args.interval_days}")
    return 0


def cmd_doc_title(args) -> int:
    """문서 제목 갱신 및 MinHash 서명 재계산."""
    s = get_settings()
    conn = dbm.connect_existing(s.db_file)
    try:
        ok = dbm.set_document_title(conn, args.document_id, args.title)
        if not ok:
            print(f"문서 없음: {args.document_id}")
            return 1
        print(f"제목 갱신 완료: {args.document_id} → '{args.title}'")
        return 0
    finally:
        conn.close()


def cmd_dedup_scan(args) -> int:
    """[진단·비파괴] 근사 중복(near-duplicate) 클러스터를 보고만 한다(병합 안 함).

    minhash 백필 후 임계 이상으로 묶이는 문서쌍을 클러스터로 출력. content_hash·
    canonical_url 을 비껴간 "같은 글 다른 입구"(arxiv 버전 접미사 등)를 찾는다.
    """
    from .ingest.service import IngestService

    s = get_settings()
    svc = IngestService(s)
    out = svc.dedup_scan(threshold=args.threshold, min_len=args.min_len)
    print(f"검사 문서 {out['documents']} · 근사중복 클러스터 {len(out['clusters'])}개 "
          f"(임계 {args.threshold}, 최소길이 {args.min_len})")
    for i, c in enumerate(out["clusters"], 1):
        print(f"\n[{i}] 유사도 {c['score']} · {len(c['ids'])}개 문서")
        for did, url, title in zip(c["ids"], c["urls"], c["titles"]):
            print(f"    {did}  {url or '(url 없음)'}  | {title}")
    if not out["clusters"]:
        print("근사중복 없음.")
    return 0


def cmd_recanonicalize(args) -> int:
    """기존 문서 canonical_url 을 현재 규칙으로 재계산(비파괴). arxiv 버전 정규화 등 반영.

    기본 적용, --dry-run 으로 변경 예정만 본다. 같은 자료의 변형이 같은 canonical 로
    수렴 → 이후 dedup-merge 가 깨끗한 URL 을 keeper 로 남긴다.
    """
    from .ingest.service import IngestService

    s = get_settings()
    svc = IngestService(s)
    out = svc.recanonicalize_documents(apply=not args.dry_run)
    mode = "변경 예정(dry-run)" if args.dry_run else "재계산 적용"
    print(f"{mode}: 문서 {out['docs']} · 변경 {out['changed']}")
    for sm in out["samples"]:
        print(f"    {sm['id']}  {sm['from']}  →  {sm['to']}")
    return 0


def cmd_dedup_merge(args) -> int:
    """근사중복 클러스터를 각각 1개로 병합. 기본은 **계획만(dry-run)**, --apply 로 실행.

    keeper = 최장 본문(동률이면 최초 적재). loser 참조는 keeper 로 재배치 후 삭제.
    """
    from .ingest.service import IngestService

    s = get_settings()
    svc = IngestService(s)
    out = svc.dedup_merge(threshold=args.threshold, min_len=args.min_len, apply=args.apply)
    mode = "병합 실행" if out["applied"] else "계획(dry-run, --apply 로 실행)"
    print(f"{mode}: 클러스터 {out['merged']}개")
    for i, c in enumerate(out["clusters"], 1):
        print(f"\n[{i}] 유사도 {c['score']}")
        print(f"    유지 keeper {c['keeper']}  {c['keeper_url']}")
        for d, u in zip(c["losers"], c["loser_urls"]):
            print(f"    삭제 loser  {d}  {u}")
        if "result" in c:
            r = c["result"]
            print(f"    → 재배치 엔티티{r.get('entities_repointed',0)}·"
                  f"관계{r.get('relations_repointed',0)}·inbox{r.get('inbox',0)} · "
                  f"삭제 {r.get('deleted',0)}")
    if not out["clusters"]:
        print("병합할 근사중복 없음.")
    return 0


def cmd_search(args) -> int:
    from .extract.provider import get_provider
    from .retrieval.query import search
    from .store.vectors import make_vector_store

    s = get_settings()
    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    provider = get_provider(s)
    vstore = make_vector_store(conn, s.vector_backend)
    result = search(conn, vstore, provider, args.query,
                    limit=args.limit, summarize=not args.no_summary)
    if result.answer:
        print(result.answer)
        print()
    if not result.hits:
        print("(no matches)")
    for h in result.hits:
        obs = h.entity.observations[0][:70] if h.entity.observations else ""
        via = "+".join(h.via)
        print(f"- [{h.entity.type}] {h.entity.name}  ({via})  {obs}")
    conn.close()
    return 0


def cmd_health(args) -> int:
    """시스템 건강 상태를 JSON 으로 출력. degraded(주의 필요) 또는 db 실패 시 비0 종료."""
    import json

    from .extract.provider import get_provider
    from .health import health_report

    s = get_settings()
    rep = health_report(s, get_provider(s).name)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0 if rep["ok"] and not rep.get("degraded") else 1


def cmd_liveness(_args) -> int:
    """DB 접근과 현재 스키마만 확인하는 경량 상태를 출력한다."""
    import json

    from .health import liveness_report

    s = get_settings()
    rep = liveness_report(s)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0 if rep["ok"] else 1


def cmd_migrate(_args) -> int:
    """DB 스키마를 명시적으로 초기화/업그레이드하고 버전을 검증한다."""
    from .health import require_current_schema

    s = get_settings()
    conn = None
    try:
        conn = dbm.connect(s.db_file)
        dbm.init_db(conn)
        version = require_current_schema(conn)
    except Exception as e:  # noqa: BLE001
        print(f"migrate: error: {e}", file=sys.stderr)
        return 1
    finally:
        if conn is not None:
            conn.close()
    print(f"schema_version={version} expected={dbm.SCHEMA_VERSION}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="claire", description="Claire Bible knowledge base")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser(
        "preflight",
        help="check environment, python, provider, and settings",
    ).set_defaults(func=cmd_preflight)

    doc_p = sub.add_parser(
        "doctor",
        help="knowledge graph & DB integrity diagnosis and auto-repair",
        description="Diagnose knowledge graph and DB integrity issues, with one-click auto-healing.",
    )
    doc_p.add_argument(
        "--heal",
        "--apply",
        "--repair",
        action="store_true",
        dest="heal",
        help="Auto-repair detected graph integrity issues (dangling relations, orphan entities, FTS sync)",
    )
    doc_p.add_argument(
        "--json",
        action="store_true",
        help="Output diagnosis report in JSON format",
    )
    doc_p.set_defaults(func=cmd_doctor)
    sub.add_parser("health", help="system health json (db/queues/inbox)").set_defaults(func=cmd_health)
    sub.add_parser(
        "liveness",
        help="read-only DB/schema liveness json (degraded does not fail)",
    ).set_defaults(func=cmd_liveness)
    sub.add_parser(
        "migrate",
        help="initialize/upgrade DB schema once and verify schema_version",
    ).set_defaults(func=cmd_migrate)
    sub.add_parser("status", help="full status: ops / db / progress / connections").set_defaults(func=cmd_status)
    sub.add_parser("repo", help="print source repository information and URL").set_defaults(func=cmd_repo)
    sub.add_parser("stats", help="graph counts only").set_defaults(func=cmd_stats)
    sub.add_parser("bot", help="run telegram bot (long-polling)").set_defaults(func=cmd_bot)
    sub.add_parser(
        "serve-api",
        help="run ASGI web service and API (same ingest path as DM)",
    ).set_defaults(func=cmd_serve_api)

    pr = sub.add_parser("replay-failed", help="re-ingest raw_inbox rows with status=error")
    pr.add_argument("--limit", type=int, default=0, help="0 = all")
    pr.set_defaults(func=cmd_replay_failed)

    prc = sub.add_parser("recover-run", help="auto-recover due error inbox once (gated, backoff)")
    prc.add_argument("--limit", type=int, default=0, help="0 = all due")
    prc.add_argument("--max-attempts", type=int, default=5, help="이 횟수 도달 시 영구실패")
    prc.add_argument("--base-delay", type=float, default=300.0, help="지수백오프 기준 초")
    prc.set_defaults(func=cmd_recover_run)

    prl = sub.add_parser("recover-loop", help="auto-recover error inbox on interval (daemon)")
    prl.add_argument("--interval", type=int, default=600, help="초 (최소 60)")
    prl.add_argument("--batch", type=int, default=5, help="회당 처리 건수")
    prl.add_argument("--max-attempts", type=int, default=5)
    prl.add_argument("--base-delay", type=float, default=300.0)
    prl.set_defaults(func=cmd_recover_loop)

    pm = sub.add_parser("refresh-mark", help="queue thin/host docs for re-scrape")
    pm.add_argument("--max-len", type=int, default=300, help="본문 길이 임계(미만이면 대상)")
    pm.add_argument("--host", default=None, help="특정 호스트만 (예: discuss.pytorch.kr)")
    pm.add_argument("--include-partial", action="store_true",
                    help="partial 노드(구버전 'x.com post' 등)도 재fetch 대상에 포함")
    pm.add_argument("--reason", default="thin")
    pm.set_defaults(func=cmd_refresh_mark)

    prr = sub.add_parser("refresh-run", help="process refresh queue once")
    prr.add_argument("--limit", type=int, default=0, help="0 = all")
    prr.set_defaults(func=cmd_refresh_run)

    pl = sub.add_parser("refresh-loop", help="run refresh queue on interval (daemon)")
    pl.add_argument("--interval", type=int, default=3600, help="초 (최소 60)")
    pl.add_argument("--batch", type=int, default=5, help="회당 처리 건수")
    pl.set_defaults(func=cmd_refresh_loop)

    pxr = sub.add_parser("expand-run", help="process 1-hop auto-expand queue once")
    pxr.add_argument("--limit", type=int, default=0, help="0 = all")
    pxr.set_defaults(func=cmd_expand_run)

    pxl = sub.add_parser("expand-loop", help="run 1-hop auto-expand queue on interval (daemon)")
    pxl.add_argument("--interval", type=int, default=900, help="초 (최소 60)")
    pxl.add_argument("--batch", type=int, default=3, help="회당 처리 건수")
    pxl.set_defaults(func=cmd_expand_loop)

    pi = sub.add_parser("ingest", help="ingest a single payload")
    pi.add_argument("payload", help="url / text / file path")
    pi.add_argument("--expand", action="store_true",
                    help="also fetch 1-hop related links found in the content")
    pi.add_argument("--no-expand", action="store_true",
                    help="do not even detect expansion candidates")
    pi.add_argument("--format", choices=["md", "adoc"], default=None,
                    help="detail render format (md or adoc, default: config CLAIRE_RENDER_FORMAT)")
    pi.add_argument("--orientation", "--directive", default=None,
                    help="content perspective or directive for detail rendering (e.g. '시스템 아키텍처 중심')")
    pi.set_defaults(func=cmd_ingest)

    ps = sub.add_parser("search", help="hybrid search (FTS+vector) + LLM summary")
    ps.add_argument("query", help="keyword(s) / question")
    ps.add_argument("--limit", type=int, default=8)
    ps.add_argument("--no-summary", action="store_true", help="skip LLM summary")
    ps.set_defaults(func=cmd_search)

    pre = sub.add_parser("reextract",
                         help="re-extract all docs from stored raw_text (apply prompt change, e.g. Korean, table preservation)")
    pre.add_argument("--no-rebuild", action="store_true",
                     help="merge into existing graph instead of wiping first (may mix old/new)")
    pre.add_argument("--tables", "--has-tables", action="store_true", dest="tables",
                     help="only re-extract documents that contain markdown, asciidoc, or html tables")
    pre.add_argument("--limit", type=int, default=0, help="cap number of docs (0=all)")
    pre.add_argument("--format", choices=["md", "adoc"], default=None,
                     help="detail render format (md or adoc)")
    pre.set_defaults(func=cmd_reextract)

    pbd = sub.add_parser("backfill-detail",
                         help="fill Korean readable 'detail' for docs missing it or containing tables (non-destructive)")
    pbd.add_argument("--limit", type=int, default=0, help="cap number of docs (0=all)")
    pbd.add_argument("--force", action="store_true", help="regenerate even if detail exists")
    pbd.add_argument("--tables", "--has-tables", action="store_true", dest="tables",
                     help="only backfill/regenerate documents that contain tables")
    pbd.add_argument("--format", choices=["md", "adoc"], default=None,
                     help="detail render format (md or adoc)")
    pbd.add_argument("--orientation", "--directive", default=None,
                     help="content perspective or directive for detail rendering")
    pbd.set_defaults(func=cmd_backfill_detail)

    pbs = sub.add_parser("backfill-summary",
                         help="fill missing summaries in extractions (non-destructive)")
    pbs.add_argument("--limit", type=int, default=0, help="cap number of docs (0=all)")
    pbs.set_defaults(func=cmd_backfill_summary)

    preg = sub.add_parser("regenerate",
                          help="selectively regenerate document summary/detail (default: dry-run, requires --force)")
    preg.add_argument("target", nargs="?", default=None,
                      help="document ID, share token, or share URL (/p?s=token)")
    preg.add_argument("--token", default=None, help="specific share token")
    preg.add_argument("--doc-id", default=None, help="specific document ID")
    preg.add_argument("--summary", action="store_true", help="regenerate summary only (default if no component given)")
    preg.add_argument("--detail", action="store_true", help="regenerate detail readable text only")
    preg.add_argument("--graph", action="store_true", help="re-extract entities and relations, updating knowledge graph and vault")
    preg.add_argument("--all", action="store_true", help="regenerate all components (summary, detail, graph nodes)")
    preg.add_argument("--corrupted", action="store_true",
                      help="automatically scan and target all docs with corrupted ADOC syntax in summary")
    preg.add_argument("--tables", "--has-tables", action="store_true", dest="tables",
                      help="automatically scan and target all docs containing markdown/adoc/html tables")
    preg.add_argument("--refetch", action="store_true",
                      help="re-fetch document content from URL before regenerating summary/detail")
    preg.add_argument("--force", action="store_true", help="execute LLM regeneration and overwrite DB (required to apply)")
    preg.add_argument("--effort", default=None, help="reasoning effort level (e.g. low, medium, high)")
    preg.add_argument("--format", choices=["md", "adoc"], default=None, help="detail format (md or adoc)")
    preg.add_argument("--orientation", "--directive", default=None, help="content perspective or directive for detail rendering")
    preg.add_argument("--json", action="store_true", help="output result in JSON format")
    preg.set_defaults(func=cmd_regenerate)

    # Alias: summary-regenerate
    psum = sub.add_parser("summary-regenerate",
                          help="alias for 'regenerate --summary'")
    psum.add_argument("target", nargs="?", default=None, help="document ID, share token, or share URL")
    psum.add_argument("--token", default=None, help="specific share token")
    psum.add_argument("--doc-id", default=None, help="specific document ID")
    psum.add_argument("--corrupted", action="store_true", help="scan all docs with corrupted ADOC syntax")
    psum.add_argument("--tables", "--has-tables", action="store_true", dest="tables",
                      help="automatically scan and target all docs containing tables")
    psum.add_argument("--refetch", action="store_true", help="re-fetch document content from URL before regenerating")
    psum.add_argument("--force", action="store_true", help="execute LLM regeneration and overwrite DB")
    psum.add_argument("--effort", default=None, help="reasoning effort level (low, medium, high)")
    psum.add_argument("--json", action="store_true", help="output in JSON format")
    psum.set_defaults(func=lambda args: setattr(args, "summary", True) or cmd_regenerate(args))

    pfs = sub.add_parser("format-status",
                         help="check document render format distribution and migration status")
    pfs.add_argument("--format", choices=["md", "adoc"], default=None,
                     help="target render format (default: config CLAIRE_RENDER_FORMAT)")
    pfs.add_argument("--json", action="store_true", help="output in json format")
    pfs.set_defaults(func=cmd_format_status)

    pfm = sub.add_parser(
        "format-migrate",
        help="inspect and selectively migrate document render formats (default: dry-run, requires --apply)",
    )
    pfm.add_argument("--format", choices=["md", "adoc"], default=None,
                     help="target render format (default: config CLAIRE_RENDER_FORMAT)")
    pfm.add_argument("--apply", action="store_true", help="apply format migration (default: dry-run only)")
    pfm.add_argument("--dry-run", action="store_true", help="dry-run inspection without changes (default)")
    pfm.add_argument("--yes", "-y", action="store_true", help="confirm without interactive prompt")
    pfm.add_argument("--json", action="store_true", help="output in json format")
    pfm.set_defaults(func=cmd_format_migrate)

    prc = sub.add_parser("recompile-html",
                         help="recompile detail_html for all documents using latest AOT renderer (zero LLM calls)")
    prc.set_defaults(func=cmd_recompile_html)

    pbi = sub.add_parser(
        "backfill-images",
        help="queue docs missing body images for slow re-fetch (refresh-loop drains)")
    pbi.add_argument("--limit", type=int, default=0, help="cap number of docs (0=all)")
    pbi.set_defaults(func=cmd_backfill_images)

    pw = sub.add_parser("watch", help="주기 크롤링 watch on/off·주기·목록(수동)")
    pw.add_argument("document_id", nargs="?", help="대상 문서 id (생략+--list 면 목록)")
    pw.add_argument("--on", action="store_true", help="watch 켜기")
    pw.add_argument("--off", action="store_true", help="watch 끄기")
    pw.add_argument("--interval-days", type=float, default=None, help="재확인 주기(일)")
    pw.add_argument("--list", action="store_true", help="watch 문서 목록")
    pw.set_defaults(func=cmd_watch)

    pdt = sub.add_parser("doc-title", help="update document title and recompute minhash signature")
    pdt.add_argument("document_id", help="대상 문서 id")
    pdt.add_argument("title", help="새 제목")
    pdt.set_defaults(func=cmd_doc_title)

    pds = sub.add_parser("dedup-scan",
                         help="report near-duplicate document clusters (MinHash, non-destructive)")
    pds.add_argument("--threshold", type=float, default=0.90,
                     help="Jaccard 추정 임계(0~1, 기본 0.90)")
    pds.add_argument("--min-len", type=int, default=500, dest="min_len",
                     help="이 길이 미만 문서는 비교 제외(false-positive 방지)")
    pds.set_defaults(func=cmd_dedup_scan)

    prc2 = sub.add_parser("recanonicalize",
                          help="recompute canonical_url with current rules (e.g. arxiv versions)")
    prc2.add_argument("--dry-run", action="store_true", help="변경 예정만 출력")
    prc2.set_defaults(func=cmd_recanonicalize)

    pdm = sub.add_parser("dedup-merge",
                         help="merge near-duplicate clusters into one doc each (dry-run unless --apply)")
    pdm.add_argument("--threshold", type=float, default=0.90)
    pdm.add_argument("--min-len", type=int, default=500, dest="min_len")
    pdm.add_argument("--apply", action="store_true", help="실제 병합(파괴적). 미지정 시 계획만.")
    pdm.set_defaults(func=cmd_dedup_merge)

    ppg = sub.add_parser(
        "purge",
        help="atomically purge legacy/corrupted documents with tombstones, disk unlink, graph heal & vacuum",
    )
    ppg.add_argument("target", nargs="?", default=None, help="target document ID or URL to purge")
    ppg.add_argument("--doc-id", default=None, help="specific document ID to purge")
    ppg.add_argument("--url", default=None, help="specific URL to purge")
    ppg.add_argument("--canonical-url", default=None, help="specific canonical URL to purge")
    ppg.add_argument("--pattern", default=None, help="search pattern across document id/url/title/text")
    ppg.add_argument("--reason", default="manual_purge", help="reason recorded in tombstone registry")
    ppg.add_argument("--force", action="store_true", help="execute actual purge and disk compaction")
    ppg.add_argument("--apply", action="store_true", help="alias for --force")
    ppg.add_argument("-y", "--yes", action="store_true", help="alias for --force")
    ppg.add_argument("--json", action="store_true", help="output result in JSON format")
    ppg.set_defaults(func=cmd_purge)

    pau = sub.add_parser(
        "audit",
        help="verify zero residuals and inspect storage/tombstones across DB and disk",
    )
    pau.add_argument("target", nargs="?", default=None, help="search keyword, URL, or ID to audit")
    pau.add_argument("--pattern", default=None, help="search pattern across DB and disk files")
    pau.add_argument("--json", action="store_true", help="output result in JSON format")
    pau.set_defaults(func=cmd_audit)

    pq = sub.add_parser("queue", help="inspect asynchronous queues (inbox, refresh, expand)")
    pq.add_argument("action", nargs="?", default="status", choices=["status", "list"], help="status or list")
    pq.add_argument("--name", choices=["inbox", "refresh", "expand"], default=None, help="filter specific queue")
    pq.add_argument("--limit", type=int, default=20, help="limit items shown (default 20)")
    pq.add_argument("--json", action="store_true", help="output in json format")
    pq.set_defaults(func=cmd_queue)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n[!] 사용자에 의해 작업이 중단되었습니다.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
