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


def cmd_doctor(_args) -> int:  # noqa: ANN001
    s = get_settings()
    print("claire doctor")
    print("=" * 40)
    print(f"python            : {sys.version.split()[0]}")
    print(f"provider (config) : {s.provider}")
    print(f"provider (eff*)   : {s.effective_provider}")
    print(f"  gemini key set  : {'yes' if s.gemini_api_key else 'NO (-> mock)'}")
    print(f"telegram token    : {'set' if s.telegram_bot_token else 'NOT set'}")
    print(f"allowed users     : {sorted(s.allowed_user_ids) or 'ALL'}")
    print(f"db path           : {s.db_file}")
    print(f"vault path        : {s.vault_dir}")
    print(f"vector backend cfg: {s.vector_backend}")

    ok, detail = probe_sqlite_vec()
    print(f"sqlite-vec probe  : {'OK' if ok else 'fallback->brute'} ({detail})")

    # provider 실호출 점검 (생성 + 임베딩이 조용히 실패하지 않는지)
    if s.effective_provider == "gemini":
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
    print("doctor: OK")
    return 0


def cmd_stats(_args) -> int:  # noqa: ANN001
    s = get_settings()
    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    c = dbm.counts(conn)
    for k, v in c.items():
        print(f"{k:12s}: {v}")
    conn.close()
    return 0


def cmd_status(_args) -> int:  # noqa: ANN001
    """현황 한눈에: 운영(설정/벡터/모델) · DB(그래프 규모/소스/타입) · 진행(inbox/연결)."""
    from .status import build_status_text

    print(build_status_text(get_settings(), full=True))
    return 0


def cmd_bot(_args) -> int:  # noqa: ANN001
    from .telegram_bot import run_bot

    return run_bot()


def cmd_serve_api(_args) -> int:  # noqa: ANN001
    from .api.server import run_api

    return run_api()


def cmd_replay_failed(args) -> int:  # noqa: ANN001
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


def cmd_recover_run(args) -> int:  # noqa: ANN001
    """[자동복구] error inbox 중 재시도 도래분을 1회 재적재(게이팅·지수백오프)."""
    from .ingest.service import IngestService

    svc = IngestService(get_settings())
    results = svc.recover_failed(
        max_attempts=args.max_attempts, base_delay=args.base_delay, limit=args.limit)
    if not results:
        print("재적재 대상(도래분) 없음.")
        return 0
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
    lines.append("`claire status` 또는 `replay-failed` 로 점검하세요.")
    sent = notify_owner(s.telegram_bot_token, s.notify_chat_id, "\n".join(lines))
    print(f"[recover] 영구실패 알림 {'전송' if sent else '미전송(미설정/실패)'}",
          flush=True)


def cmd_recover_loop(args) -> int:  # noqa: ANN001
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


def cmd_refresh_mark(args) -> int:  # noqa: ANN001
    """갱신 대상 문서를 큐에 등록(기본: 본문 빈약 문서)."""
    from .ingest.service import IngestService

    svc = IngestService(get_settings())
    n = svc.mark_thin_for_refresh(max_len=args.max_len, host=args.host, reason=args.reason)
    print(f"갱신 대상 {n}건 등록 (max_len<{args.max_len}"
          + (f", host={args.host}" if args.host else "") + f", reason={args.reason})")
    return 0


def cmd_refresh_run(args) -> int:  # noqa: ANN001
    """갱신 큐 1회 처리."""
    from .ingest.service import IngestService

    results = IngestService(get_settings()).run_refresh_queue(limit=args.limit)
    if not results:
        print("갱신 대기 항목 없음.")
        return 0
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


def cmd_refresh_loop(args) -> int:  # noqa: ANN001
    """갱신 큐를 interval 초마다 자동 처리(전용 컨테이너용 데몬)."""
    import time

    from .ingest.service import IngestService

    svc = IngestService(get_settings())
    print(f"claire refresh-loop 시작 (interval={args.interval}s, batch={args.batch}). Ctrl+C 종료.", flush=True)
    while True:
        try:
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


def cmd_ingest(args) -> int:  # noqa: ANN001
    from .extract.provider import get_provider
    from .ingest.pipeline import ingest
    from .store.vectors import make_vector_store

    s = get_settings()
    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    provider = get_provider(s)
    vstore = make_vector_store(conn, s.vector_backend)
    print(f"(provider={provider.name})")
    report = ingest(
        args.payload, conn=conn, provider=provider, vstore=vstore,
        vault_dir=s.vault_dir, data_dir=s.data_dir, source="cli",
        expand_max=(0 if args.no_expand else s.expand_max),
    )
    print(report.telegram_summary())

    # 1홉 확장 후보: CLI 에서는 --expand 플래그로 즉시 재귀 적재(상한 내).
    if report.candidates and args.expand:
        print(f"\n[expand] {len(report.candidates)}개 후보 적재 중…")
        for url in report.candidates:
            sub = ingest(url, conn=conn, provider=provider, vstore=vstore,
                         vault_dir=s.vault_dir, data_dir=s.data_dir, source="cli-expand",
                         expand_max=0)  # 2홉 방지
            print(f"  - {url}\n    {sub.telegram_summary().splitlines()[0]}")
    elif report.candidates:
        print("\n[expand] 후보 URL (적재하려면 --expand):")
        for url in report.candidates:
            print(f"  - {url}")

    conn.close()
    return 0 if report.error is None else 1


def cmd_search(args) -> int:  # noqa: ANN001
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


def _prune_backups(bdir, keep: int) -> int:  # noqa: ANN001
    """오래된 스냅샷부터 삭제해 최근 keep 개만 남긴다. 삭제 수 반환."""
    files = sorted(bdir.glob("claire-*.db"))
    excess = files[:-keep] if keep > 0 and len(files) > keep else []
    for f in excess:
        try:
            f.unlink()
        except OSError:
            pass
    return len(excess)


def _do_backup(s, keep: int):  # noqa: ANN001
    """스냅샷 1개 생성 → 복원 가능성 검증(snapshot counts==live) → 보존 정리.

    반환: (dest_path, match: bool, snapshot_counts: dict)
    """
    import time

    # 백업 직전 정본 스키마 보장(다른 모든 진입점과 동일한 init_db; 운영 DB 는 no-op,
    # 첫 실행/빈 DB 만 테이블 생성). 스냅샷이 valid schema 를 담도록.
    _c = dbm.connect(s.db_file)
    dbm.init_db(_c)
    _c.close()
    bdir = s.data_dir / "backups"
    dest = bdir / f"claire-{time.strftime('%Y%m%d-%H%M%S')}.db"
    dbm.backup_database(s.db_file, dest)
    # 파일이 생겼다 != 복원 가능 → 스냅샷을 실제로 열어 row count 를 live 와 대조.
    live = dbm.connect(s.db_file)
    snap = dbm.connect(dest)
    try:
        live_counts, snap_counts = dbm.counts(live), dbm.counts(snap)
    finally:
        live.close()
        snap.close()
    _prune_backups(bdir, keep)
    return dest, (live_counts == snap_counts), snap_counts


def cmd_backup(args) -> int:  # noqa: ANN001
    """DB 스냅샷 1회 + 검증 + 보존 정리."""
    s = get_settings()
    dest, match, counts = _do_backup(s, args.keep)
    print(f"백업 완료: {dest}")
    print(f"  크기 {dest.stat().st_size:,}B · 스냅샷 {counts} · live와 일치={match}")
    return 0 if match else 1


def cmd_backup_loop(args) -> int:  # noqa: ANN001
    """주기적 DB 스냅샷 데몬(전용 컨테이너용)."""
    import time

    s = get_settings()
    print(f"claire backup-loop 시작 (interval={args.interval}s, keep={args.keep}). "
          f"Ctrl+C 종료.", flush=True)
    while True:
        try:
            dest, match, counts = _do_backup(s, args.keep)
            print(f"[backup] {dest.name} · counts={counts} · live일치={match}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[backup] 오류: {e}", flush=True)
        time.sleep(max(60, args.interval))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="claire", description="claire_bible knowledge base")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="check environment").set_defaults(func=cmd_doctor)
    sub.add_parser("status", help="full status: ops / db / progress / connections").set_defaults(func=cmd_status)
    sub.add_parser("stats", help="graph counts only").set_defaults(func=cmd_stats)
    sub.add_parser("bot", help="run telegram bot (long-polling)").set_defaults(func=cmd_bot)
    sub.add_parser("serve-api", help="run local inject API (same ingest path as DM)").set_defaults(func=cmd_serve_api)

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
    pm.add_argument("--reason", default="thin")
    pm.set_defaults(func=cmd_refresh_mark)

    prr = sub.add_parser("refresh-run", help="process refresh queue once")
    prr.add_argument("--limit", type=int, default=0, help="0 = all")
    prr.set_defaults(func=cmd_refresh_run)

    pl = sub.add_parser("refresh-loop", help="run refresh queue on interval (daemon)")
    pl.add_argument("--interval", type=int, default=3600, help="초 (최소 60)")
    pl.add_argument("--batch", type=int, default=5, help="회당 처리 건수")
    pl.set_defaults(func=cmd_refresh_loop)

    pi = sub.add_parser("ingest", help="ingest a single payload")
    pi.add_argument("payload", help="url / text / file path")
    pi.add_argument("--expand", action="store_true",
                    help="also fetch 1-hop related links found in the content")
    pi.add_argument("--no-expand", action="store_true",
                    help="do not even detect expansion candidates")
    pi.set_defaults(func=cmd_ingest)

    ps = sub.add_parser("search", help="hybrid search (FTS+vector) + LLM summary")
    ps.add_argument("query", help="keyword(s) / question")
    ps.add_argument("--limit", type=int, default=8)
    ps.add_argument("--no-summary", action="store_true", help="skip LLM summary")
    ps.set_defaults(func=cmd_search)

    pb = sub.add_parser("backup", help="snapshot DB (VACUUM INTO) + verify restorable + prune")
    pb.add_argument("--keep", type=int, default=7, help="최근 N개 보존")
    pb.set_defaults(func=cmd_backup)

    pbl = sub.add_parser("backup-loop", help="periodic DB snapshot daemon")
    pbl.add_argument("--interval", type=int, default=86400, help="초 (기본 1일)")
    pbl.add_argument("--keep", type=int, default=7)
    pbl.set_defaults(func=cmd_backup_loop)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
