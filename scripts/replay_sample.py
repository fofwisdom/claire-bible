#!/usr/bin/env python3
"""sample.md 항목을 로컬 inject API 로 N초 간격 전송 — 적재 검증용.

텔레그램 DM 과 동일한 통로(IngestService)를 API 로 호출한다.
각 항목의 IngestReport 를 JSONL 로그로 남겨 사후 assertion 가능하게 한다.

사용:
  python replay_sample.py --token <TOKEN> --interval 300 --log data/replay.jsonl
  python replay_sample.py --token <TOKEN> --limit 3 --interval 0   # 즉시 3건
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request

_URL = re.compile(r"https?://[^\s]+")


def parse_items(sample_path: str) -> list[str]:
    """sample.md 각 줄에서 적재 대상 추출. URL 있으면 URL, 없으면 의미있는 텍스트."""
    items: list[str] = []
    for line in open(sample_path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        m = _URL.search(line)
        if m:
            items.append(m.group(0).rstrip(".,;"))
        else:
            body = re.sub(r"^\[.*?\]\s*[^:]*:\s*", "", line).strip()
            if len(body) >= 12:
                items.append(body)
    return items


def post_ingest(api: str, token: str, payload: str, expand_max: int = 0) -> dict:
    req = urllib.request.Request(
        f"{api}/ingest",
        data=json.dumps({"payload": payload, "expand_max": expand_max}).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", default="sample.md")
    ap.add_argument("--api", default="http://127.0.0.1:8765")
    ap.add_argument("--token", required=True)
    ap.add_argument("--interval", type=float, default=300.0)
    ap.add_argument("--limit", type=int, default=0, help="0 = all")
    ap.add_argument("--offset", type=int, default=0, help="skip first N items")
    ap.add_argument("--expand-max", type=int, default=0)
    ap.add_argument("--log", default="replay.jsonl")
    args = ap.parse_args()

    items = parse_items(args.sample)
    if args.offset:
        items = items[args.offset:]
    if args.limit:
        items = items[:args.limit]

    print(f"[replay] {len(items)} items, interval={args.interval}s", flush=True)
    logf = open(args.log, "a", encoding="utf-8")
    for i, payload in enumerate(items, 1):
        ts = time.strftime("%H:%M:%S")
        try:
            rep = post_ingest(args.api, args.token, payload, args.expand_max)
            line = {
                "ts": ts, "i": i, "payload": payload[:80],
                "source_type": rep.get("source_type"),
                "duplicate": rep.get("duplicate"),
                "created": rep.get("entities_created"),
                "linked": rep.get("entities_linked"),
                "rel": rep.get("relations_added"),
                "error": rep.get("error"),
            }
        except Exception as e:  # noqa: BLE001
            line = {"ts": ts, "i": i, "payload": payload[:80], "error": str(e)}
        logf.write(json.dumps(line, ensure_ascii=False) + "\n")
        logf.flush()
        print(f"[{ts}] {i}/{len(items)} {line.get('source_type','?')} "
              f"new={line.get('created')} link={line.get('linked')} "
              f"dup={line.get('duplicate')} err={line.get('error')} :: {payload[:55]}",
              flush=True)
        if i < len(items) and args.interval > 0:
            time.sleep(args.interval)
    logf.close()
    print("[replay] done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
