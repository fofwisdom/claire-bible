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
        from .extract.provider import get_provider
        import shutil

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
    lines.append("텔레그램 `/failed` 로 목록 확인 후 `/retry <번호>` 로 재시도하세요.")
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
    n = svc.mark_thin_for_refresh(
        max_len=args.max_len, host=args.host, reason=args.reason,
        include_partial=args.include_partial)
    print(f"갱신 대상 {n}건 등록 (max_len<{args.max_len}"
          + (f", host={args.host}" if args.host else "")
          + (", +partial" if args.include_partial else "")
          + f", reason={args.reason})")
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


def cmd_expand_run(args) -> int:  # noqa: ANN001
    """1홉 자동확장 큐 1회 처리."""
    from .ingest.service import IngestService

    results = IngestService(get_settings()).run_expand_queue(limit=args.limit)
    if not results:
        print("확장 대기 항목 없음.")
        return 0
    for r in results:
        if r.get("error"):
            print(f"  ❌ {r['document_id']}: {str(r['error'])[:60]}")
        else:
            print(f"  🔗 {r['document_id']}: 후보 {r['candidates']}·선별 {r['selected']}"
                  f"·적재 {r['stored']}·스킵 {r['skipped']}")
    print(f"처리 {len(results)}건, 적재 {sum(r.get('stored',0) for r in results)}")
    return 0


def cmd_expand_loop(args) -> int:  # noqa: ANN001
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


def cmd_reextract(args) -> int:  # noqa: ANN001
    """저장된 raw_text 로 전체 문서를 재추출(프롬프트 변경 반영, 예: 한글화)."""
    from .ingest.service import IngestService

    s = get_settings()
    svc = IngestService(s)
    print(f"(provider={svc.provider.name}) 재추출 시작"
          f"{' (rebuild)' if not args.no_rebuild else ''}…", flush=True)
    out = svc.reextract_all(rebuild=not args.no_rebuild, limit=args.limit)
    print(f"재추출 완료: 문서 {out['docs']} · 성공 {out['ok']} · 실패 {out['failed']}")
    for e in out["errors"][:20]:
        print(f"  - 실패 {e['document_id']}: {e['error']}")
    return 0 if out["failed"] == 0 else 1


def cmd_backfill_detail(args) -> int:  # noqa: ANN001
    """detail(한국어 가독 렌더링)이 없는 문서를 채운다 — 비파괴적(그래프 불변).

    reextract 와 달리 reset_graph/rebuild 없이 documents.detail 만 채운다(advisor).
    문서당 Gemini 1회(quota). --force 면 기존 detail 도 재생성.
    """
    from .ingest.service import IngestService

    s = get_settings()
    svc = IngestService(s)
    print(f"(provider={svc.provider.name}) detail 백필 시작"
          f"{' (force)' if args.force else ''}…", flush=True)
    out = svc.backfill_details(limit=args.limit, force=args.force)
    print(f"백필 완료: 대상 {out['docs']} · 생성 {out['ok']} · 건너뜀 {out['skipped']}")
    return 0


def cmd_backfill_images(args) -> int:  # noqa: ANN001
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


def cmd_watch(args) -> int:  # noqa: ANN001
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


def cmd_dedup_scan(args) -> int:  # noqa: ANN001
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


def cmd_recanonicalize(args) -> int:  # noqa: ANN001
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


def cmd_dedup_merge(args) -> int:  # noqa: ANN001
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


def cmd_health(args) -> int:  # noqa: ANN001
    """시스템 건강 상태를 JSON 으로 출력. degraded(주의 필요) 또는 db 실패 시 비0 종료."""
    import json

    from .extract.provider import get_provider
    from .health import health_report

    s = get_settings()
    rep = health_report(s, get_provider(s).name)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0 if rep["ok"] and not rep.get("degraded") else 1


def cmd_liveness(_args) -> int:  # noqa: ANN001
    """DB 접근과 현재 스키마만 확인하는 경량 상태를 출력한다."""
    import json

    from .health import liveness_report

    s = get_settings()
    rep = liveness_report(s)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0 if rep["ok"] else 1


def cmd_migrate(_args) -> int:  # noqa: ANN001
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

    sub.add_parser("doctor", help="check environment").set_defaults(func=cmd_doctor)
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
    pi.set_defaults(func=cmd_ingest)

    ps = sub.add_parser("search", help="hybrid search (FTS+vector) + LLM summary")
    ps.add_argument("query", help="keyword(s) / question")
    ps.add_argument("--limit", type=int, default=8)
    ps.add_argument("--no-summary", action="store_true", help="skip LLM summary")
    ps.set_defaults(func=cmd_search)

    pre = sub.add_parser("reextract",
                         help="re-extract all docs from stored raw_text (apply prompt change, e.g. Korean)")
    pre.add_argument("--no-rebuild", action="store_true",
                     help="merge into existing graph instead of wiping first (may mix old/new)")
    pre.add_argument("--limit", type=int, default=0, help="cap number of docs (0=all)")
    pre.set_defaults(func=cmd_reextract)

    pbd = sub.add_parser("backfill-detail",
                         help="fill Korean readable 'detail' for docs missing it (non-destructive)")
    pbd.add_argument("--limit", type=int, default=0, help="cap number of docs (0=all)")
    pbd.add_argument("--force", action="store_true", help="regenerate even if detail exists")
    pbd.set_defaults(func=cmd_backfill_detail)

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

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
