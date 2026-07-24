#!/usr/bin/env python3
"""sample.md 항목을 로컬 inject API 로 N초 간격 전송 — 적재 검증용.

텔레그램 DM 과 동일한 통로(IngestService)를 API 로 호출한다.
각 항목의 IngestReport 를 JSONL 로그로 남겨 사후 assertion 가능하게 한다.

사용:
  # 기본: CLAIRE_INJECT_TOKEN 환경변수를 안전한 방식으로 미리 주입
  python replay_sample.py --interval 300
  python replay_sample.py --token-file /run/secrets/claire_inject_token --limit 3 --interval 0

`--token`도 호환을 위해 남아 있지만 토큰이 프로세스 목록과 셸 기록에 노출될 수 있어
환경변수나 권한이 제한된 토큰 파일을 우선한다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

_URL = re.compile(r"https?://[^\s]+")
_BEARER = re.compile(r"(?i)\bBearer\s+\S+")
_REDACTED = "[redacted]"
_TOKEN_ENV = "CLAIRE_INJECT_TOKEN"


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
    parts = urlsplit(api)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("--api must be an http(s) URL with a host")
    req = urllib.request.Request(
        f"{api}/ingest",
        data=json.dumps({"payload": payload, "expand_max": expand_max}).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"},
        method="POST",
    )
    # scheme/host를 위에서 제한했으므로 file/custom URL handler에 도달하지 않는다.
    with urllib.request.urlopen(req, timeout=180) as resp:  # nosec B310
        return json.loads(resp.read())


def _load_token(
    cli_token: str | None,
    token_file: str | None,
    environ: dict[str, str] | None = None,
) -> str:
    """환경변수/파일에서 토큰을 읽고, 기존 CLI 토큰은 호출자가 경고 후 전달한다."""
    if cli_token is not None:
        token = cli_token.strip()
    elif token_file:
        try:
            token = Path(token_file).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as e:
            raise ValueError(f"토큰 파일을 읽을 수 없습니다: {e}") from e
    else:
        token = (environ if environ is not None else os.environ).get(_TOKEN_ENV, "").strip()

    if not token:
        raise ValueError(
            f"{_TOKEN_ENV} 환경변수 또는 --token-file로 토큰을 제공하세요."
        )
    if any(ch.isspace() for ch in token):
        raise ValueError("토큰에는 공백이나 줄바꿈을 포함할 수 없습니다.")
    return token


def _redact_error(error: object) -> str | None:
    """오류 진단은 남기되 URL·bearer 값과 긴 응답 본문은 로그에 기록하지 않는다."""
    if error is None:
        return None
    text = _BEARER.sub("Bearer [redacted]", str(error))
    text = _URL.sub("[redacted-url]", text)
    return text[:240]


def _log_entry(
    ts: str,
    index: int,
    *,
    report: dict | None = None,
    error: object = None,
) -> dict:
    """입력 원문 없이 재생 결과만 기록한다."""
    line = {"ts": ts, "i": index, "payload": _REDACTED}
    if report is not None:
        line.update({
            "source_type": report.get("source_type"),
            "duplicate": report.get("duplicate"),
            "created": report.get("entities_created"),
            "linked": report.get("entities_linked"),
            "rel": report.get("relations_added"),
            "error": _redact_error(report.get("error")),
        })
    else:
        line["error"] = _redact_error(error)
    return line


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", default="sample.md")
    ap.add_argument("--api", default="http://127.0.0.1:8765")
    token_group = ap.add_mutually_exclusive_group()
    token_group.add_argument(
        "--token",
        help="deprecated: 프로세스 목록에 노출될 수 있음; 환경변수/--token-file 권장",
    )
    token_group.add_argument(
        "--token-file",
        help=f"{_TOKEN_ENV} 대신 읽을 권한 제한 토큰 파일",
    )
    ap.add_argument("--interval", type=float, default=300.0)
    ap.add_argument("--limit", type=int, default=0, help="0 = all")
    ap.add_argument("--offset", type=int, default=0, help="skip first N items")
    ap.add_argument("--expand-max", type=int, default=0)
    ap.add_argument("--log", default="data/replay.jsonl")
    args = ap.parse_args()

    if args.token is not None:
        print(
            "[replay] 경고: --token은 프로세스 목록/셸 기록에 노출될 수 있습니다. "
            f"{_TOKEN_ENV} 또는 --token-file을 사용하세요.",
            file=sys.stderr,
        )
    try:
        token = _load_token(args.token, args.token_file)
    except ValueError as e:
        ap.error(str(e))

    items = parse_items(args.sample)
    if args.offset:
        items = items[args.offset:]
    if args.limit:
        items = items[:args.limit]

    print(f"[replay] {len(items)} items, interval={args.interval}s", flush=True)
    log_path = Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    if hasattr(os, "fchmod"):
        os.fchmod(log_fd, 0o600)
    with os.fdopen(log_fd, "a", encoding="utf-8") as logf:
        for i, payload in enumerate(items, 1):
            ts = time.strftime("%H:%M:%S")
            try:
                rep = post_ingest(args.api, token, payload, args.expand_max)
                line = _log_entry(ts, i, report=rep)
            except Exception as e:  # noqa: BLE001
                line = _log_entry(ts, i, error=e)
            logf.write(json.dumps(line, ensure_ascii=False) + "\n")
            logf.flush()
            print(f"[{ts}] {i}/{len(items)} {line.get('source_type','?')} "
                  f"new={line.get('created')} link={line.get('linked')} "
                  f"dup={line.get('duplicate')} err={line.get('error')}",
                  flush=True)
            if i < len(items) and args.interval > 0:
                time.sleep(args.interval)
    print("[replay] done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
