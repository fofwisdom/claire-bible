"""웹 수집 단계별 진단과 정제 DOM 스냅샷 보존.

수집 성공 여부와 무관하게 pipeline의 ``raw_inbox.id``에 trace를 연결한다. 원본 HTML의
정확한 해시와 크기는 보존하되, 디스크에는 폼 값·스크립트·토큰성 속성을 제거한 DOM만
zstd로 압축해 저장한다.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import os
import re
import secrets
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import zstandard

FETCH_SNAPSHOT_MAX_BYTES = 2 * 1024 * 1024
_REDACTED = "***REDACTED***"
_SENSITIVE_RE = re.compile(
    r"(?:token|secret|password|passwd|api[_-]?key|auth|cookie|credential|csrf|xsrf|"
    r"session|signature|access[_-]?key|refresh[_-]?key)",
    re.IGNORECASE,
)
_SENSITIVE_QUERY_KEYS = {
    "s", "token", "access_token", "refresh_token", "api_key", "key", "auth",
    "code", "state", "signature", "sig", "session", "password", "share",
}


@dataclass
class _Snapshot:
    stage_index: int
    html: str
    original_sha256: str
    original_bytes: int
    truncated: bool


@dataclass
class FetchTraceSession:
    data_dir: Path
    inbox_id: int
    trace_id: str = field(default_factory=lambda: f"ft_{secrets.token_hex(12)}")
    stages: list[dict[str, Any]] = field(default_factory=list)
    snapshots: list[_Snapshot] = field(default_factory=list)

    def record(self, stage: str, **values: Any) -> int:
        row = {
            "stage_order": len(self.stages) + 1,
            "stage": stage,
            "started_at": values.pop("started_at", time.time()),
            **values,
        }
        self.stages.append(row)
        return len(self.stages) - 1

    def annotate(self, stage: str, **values: Any) -> None:
        for row in reversed(self.stages):
            if row.get("stage") == stage:
                row.update(values)
                return
        self.record(stage, **values)

    def capture_html(self, stage: str, html: str) -> None:
        raw = (html or "").encode("utf-8", errors="replace")
        if not raw:
            return
        self.annotate(stage, response_bytes=len(raw))
        truncated = len(raw) > FETCH_SNAPSHOT_MAX_BYTES
        limited = raw[:FETCH_SNAPSHOT_MAX_BYTES].decode("utf-8", errors="replace")
        sanitized = sanitize_html_snapshot(limited)
        stage_index = next(
            (i for i in range(len(self.stages) - 1, -1, -1)
             if self.stages[i].get("stage") == stage),
            len(self.stages) - 1,
        )
        self.snapshots.append(
            _Snapshot(
                stage_index=stage_index,
                html=sanitized,
                original_sha256=hashlib.sha256(raw).hexdigest(),
                original_bytes=len(raw),
                truncated=truncated,
            )
        )

    def persist(
        self,
        conn,
        *,
        document_id: str | None = None,
        status: str,
        error: str | None = None,
    ) -> None:
        snapshot_by_stage: dict[int, dict[str, Any]] = {}
        for snapshot in self.snapshots:
            stage = self.stages[snapshot.stage_index]
            safe_stage = re.sub(r"[^a-z0-9_-]+", "-", str(stage["stage"]).lower()).strip("-")
            relative = Path("raw") / "fetch" / str(self.inbox_id) / self.trace_id / (
                f"{stage['stage_order']:02d}_{safe_stage}.html.zst"
            )
            destination = self.data_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            compressed = zstandard.ZstdCompressor(level=3, write_checksum=True).compress(
                snapshot.html.encode("utf-8")
            )
            fd, temp_name = tempfile.mkstemp(
                prefix=".tmp-fetch-", suffix=".zst", dir=destination.parent
            )
            temp_path = Path(temp_name)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(compressed)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp_path, destination)
            except Exception:
                temp_path.unlink(missing_ok=True)
                raise
            snapshot_by_stage[snapshot.stage_index] = {
                "snapshot_path": str(relative),
                "snapshot_sha256": snapshot.original_sha256,
                "snapshot_original_bytes": snapshot.original_bytes,
                "snapshot_stored_bytes": len(compressed),
                "snapshot_truncated": 1 if snapshot.truncated else 0,
            }

        for index, stage in enumerate(self.stages):
            metadata = dict(stage.get("metadata", {}) or {})
            if error and index == len(self.stages) - 1 and not stage.get("error_message"):
                stage["error_message"] = error
            snapshot_info = snapshot_by_stage.get(index, {})
            conn.execute(
                """
                INSERT INTO fetch_attempts(
                    trace_id,inbox_id,document_id,stage_order,stage,started_at,
                    duration_ms,status,http_status,input_url,effective_url,content_type,
                    response_bytes,usable,guard_error,error_type,error_message,metadata,
                    snapshot_path,snapshot_sha256,snapshot_original_bytes,
                    snapshot_stored_bytes,snapshot_truncated
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    self.trace_id,
                    self.inbox_id,
                    document_id,
                    stage.get("stage_order", index + 1),
                    stage.get("stage"),
                    stage.get("started_at"),
                    stage.get("duration_ms"),
                    stage.get("status", status),
                    stage.get("http_status"),
                    stage.get("input_url"),
                    stage.get("effective_url"),
                    stage.get("content_type"),
                    stage.get("response_bytes"),
                    None if stage.get("usable") is None else int(bool(stage.get("usable"))),
                    stage.get("guard_error"),
                    stage.get("error_type"),
                    stage.get("error_message"),
                    json.dumps(metadata, ensure_ascii=False),
                    snapshot_info.get("snapshot_path"),
                    snapshot_info.get("snapshot_sha256"),
                    snapshot_info.get("snapshot_original_bytes"),
                    snapshot_info.get("snapshot_stored_bytes"),
                    snapshot_info.get("snapshot_truncated", 0),
                ),
            )
        conn.commit()


_ACTIVE_TRACE: contextvars.ContextVar[FetchTraceSession | None] = contextvars.ContextVar(
    "claire_fetch_trace", default=None
)


@contextmanager
def fetch_trace(data_dir: Path, inbox_id: int) -> Iterator[FetchTraceSession]:
    session = FetchTraceSession(Path(data_dir), inbox_id)
    token = _ACTIVE_TRACE.set(session)
    try:
        yield session
    finally:
        _ACTIVE_TRACE.reset(token)


def record_fetch_stage(stage: str, **values: Any) -> None:
    session = _ACTIVE_TRACE.get()
    if session is not None:
        session.record(stage, **values)


def annotate_fetch_stage(stage: str, **values: Any) -> None:
    session = _ACTIVE_TRACE.get()
    if session is not None:
        session.annotate(stage, **values)


def capture_fetch_html(stage: str, html: str) -> None:
    session = _ACTIVE_TRACE.get()
    if session is not None:
        session.capture_html(stage, html)


def sanitize_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        query = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            query.append((key, _REDACTED if key.lower() in _SENSITIVE_QUERY_KEYS or _SENSITIVE_RE.search(key) else value))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    except Exception:
        return url


def sanitize_html_snapshot(html: str) -> str:
    """DOM 구조를 유지하면서 실행 코드와 인증·폼 값을 제거한다."""
    try:
        from lxml import html as lh

        tree = lh.fromstring(html)
        for node in tree.xpath("//script"):
            node.text = "/* redacted by Claire fetch diagnostics */"
            for child in list(node):
                node.remove(child)
        for node in tree.iter():
            tag = str(getattr(node, "tag", "")).lower()
            for attr, value in list(node.attrib.items()):
                attr_lower = attr.lower()
                if _SENSITIVE_RE.search(attr_lower):
                    node.attrib[attr] = _REDACTED
                elif attr_lower in {"href", "src", "action", "formaction"}:
                    node.attrib[attr] = sanitize_url(value)
                elif attr_lower == "value" and tag in {"input", "button", "option"}:
                    node.attrib[attr] = _REDACTED
            if tag == "textarea" and node.text:
                node.text = _REDACTED
            if tag == "meta":
                marker = " ".join(
                    str(node.attrib.get(key, "")) for key in ("name", "property", "http-equiv")
                )
                if _SENSITIVE_RE.search(marker) and "content" in node.attrib:
                    node.attrib["content"] = _REDACTED
        return lh.tostring(tree, encoding="unicode", method="html")
    except Exception:
        # 파싱 불가능한 응답에서도 스크립트와 대표적인 값 패턴은 노출하지 않는다.
        text = re.sub(
            r"(?is)<script\b[^>]*>.*?</script>",
            "<script>/* redacted by Claire fetch diagnostics */</script>",
            html,
        )
        return re.sub(
            r"(?i)(value\s*=\s*[\"'])(.*?)([\"'])",
            rf"\1{_REDACTED}\3",
            text,
        )
