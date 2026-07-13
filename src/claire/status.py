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

    lines.append("📊 claire_bible status")

    # 운영
    lines.append("[운영]")
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
