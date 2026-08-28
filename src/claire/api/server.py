"""Claire HTTP API와 graph UI를 제공하는 ASGI 애플리케이션.

Starlette는 라우팅/응답 계층만 담당하고 Uvicorn은 단일 worker로 실행한다. 외부
Reverse Proxy는 TLS와 public hostname을 담당하지만, 애플리케이션은 forwarded
header를 신뢰하지 않고 설정된 public URL의 authority를 직접 검증한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
from starlette.routing import Route
from starlette.types import ASGIApp

from ..config import Settings, get_settings
from ..ingest.report_json import report_to_dict
from ..ingest.service import IngestService
from ..store import db as dbm
from .mcp_tools import build_mcp_app
from .security import (
    ErrorBoundaryMiddleware,
    WebRuntimeConfig,
    read_json_body,
    request_auth_scope,
    request_id,
    wrap_web_app,
)

log = logging.getLogger("claire.api")
_IMAGE_PATH_RE = re.compile(
    r"^images/[A-Za-z0-9_.-]+\.(?:jpg|jpeg|png|webp|gif)$"
)
_STATIC_ICONS_DIR = Path(__file__).resolve().parent.parent / "static" / "icons"
_ICON_FILENAME_RE = re.compile(
    r"^[A-Za-z0-9_.-]+\.(?:png|svg|ico|json|xml|webmanifest)$"
)
_STATIC_FONTS_DIR = Path(__file__).resolve().parent.parent / "static" / "fonts"
_FONT_FILENAME_RE = re.compile(r"^[A-Za-z0-9_.-]+\.woff2$")
FONTS = (
    "NotoSansKR-Regular.woff2",
    "NotoSansKR-Bold.woff2",
    "NotoSerifKR-Regular.woff2",
    "NotoSerifKR-Bold.woff2",
    "D2Coding.woff2",
    "D2CodingBold.woff2",
)
_MAX_SEARCH_QUERY_LENGTH = 2000
_MAX_SEARCH_RESULTS = 50
_MAX_ANONYMOUS_SEARCH_RESULTS = 20
_MAX_EXPENSIVE_JOBS = 4
_MAX_ANONYMOUS_SEARCH_JOBS = 4
_PROGRESS_QUEUE_SIZE = 64


async def _http_error(_request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, str) else "request failed"
    return JSONResponse(
        {"error": detail},
        status_code=exc.status_code,
        headers=exc.headers,
    )


async def _json_object(request: Request) -> dict[str, Any]:
    body = await read_json_body(request)
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="json object required")
    return body


def _consume_task_result(task: asyncio.Task[Any]) -> None:
    """연결이 끊어진 뒤 끝나는 worker task의 예외를 회수한다."""

    try:
        task.exception()
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass


def _log_operation_failure(request: Request, operation: str, exc: Exception) -> None:
    """비밀이 섞일 수 있는 예외 문자열은 기록하지 않고 추적 정보만 남긴다."""

    log.warning(
        "%s failed request_id=%s error_type=%s",
        operation,
        request_id(request),
        type(exc).__name__,
    )


def create_app(
    settings: Settings | None = None,
    service: IngestService | Any | None = None,
) -> ASGIApp:
    """설정과 서비스를 주입할 수 있는 ASGI app factory."""

    s = settings or get_settings()
    # 서비스/provider를 만들기 전에 외부 노출 설정을 fail-closed로 검증한다.
    WebRuntimeConfig.from_settings(s)
    # Schema migration과 WAL 설정은 요청마다 반복하지 않고 프로세스 시작 시 한 번만 한다.
    conn = dbm.connect(s.db_file)
    try:
        dbm.init_db(conn)
    finally:
        conn.close()
    svc = service or IngestService(s)
    active_expensive_jobs = 0
    active_anonymous_search_jobs = 0

    def _reserve_expensive_job() -> None:
        nonlocal active_expensive_jobs
        if active_expensive_jobs >= _MAX_EXPENSIVE_JOBS:
            raise HTTPException(status_code=503, detail="server is busy")
        active_expensive_jobs += 1

    def _release_expensive_job(_task: asyncio.Task[Any] | None = None) -> None:
        nonlocal active_expensive_jobs
        active_expensive_jobs -= 1

    async def _run_expensive(func: Any, /, *args: Any, **kwargs: Any) -> Any:
        _reserve_expensive_job()
        try:
            task = asyncio.create_task(asyncio.to_thread(func, *args, **kwargs))
        except BaseException:
            _release_expensive_job()
            raise
        task.add_done_callback(_release_expensive_job)
        task.add_done_callback(_consume_task_result)
        # 요청이 끊겨도 sync worker는 중단할 수 없다. task를 shield해 실제 worker가
        # 끝날 때까지 admission slot이 유지되게 한다.
        return await asyncio.shield(task)

    def _reserve_anonymous_search_job() -> None:
        nonlocal active_anonymous_search_jobs
        if active_anonymous_search_jobs >= _MAX_ANONYMOUS_SEARCH_JOBS:
            raise HTTPException(
                status_code=429,
                detail="too many search requests",
                headers={"Retry-After": "1"},
            )
        active_anonymous_search_jobs += 1

    def _release_anonymous_search_job(
        _task: asyncio.Task[Any] | None = None,
    ) -> None:
        nonlocal active_anonymous_search_jobs
        active_anonymous_search_jobs -= 1

    async def _run_anonymous_search(
        func: Any,
        /,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        _reserve_anonymous_search_job()
        try:
            task = asyncio.create_task(asyncio.to_thread(func, *args, **kwargs))
        except BaseException:
            _release_anonymous_search_job()
            raise
        task.add_done_callback(_release_anonymous_search_job)
        task.add_done_callback(_consume_task_result)
        return await asyncio.shield(task)

    async def health(_request: Request) -> JSONResponse:
        from ..health import liveness_report

        report = await asyncio.to_thread(liveness_report, s)
        ok = bool(report.get("ok"))
        return JSONResponse({"ok": ok}, status_code=200 if ok else 503)

    async def whoami(request: Request) -> JSONResponse:
        scope = request_auth_scope(request)
        if scope not in {"owner", "readonly", "anonymous"}:
            raise HTTPException(status_code=401, detail="authentication required")
        return JSONResponse({"scope": scope})

    async def stats(request: Request) -> JSONResponse:
        include_hidden = request_auth_scope(request) != "anonymous"

        def _counts() -> dict[str, int]:
            conn = dbm.connect_existing(s.db_file, readonly=True)
            try:
                try:
                    return dbm.counts(conn, include_hidden=include_hidden)
                except TypeError:
                    return dbm.counts(conn)
            finally:
                conn.close()

        return JSONResponse(await asyncio.to_thread(_counts))

    async def do_ingest(request: Request) -> JSONResponse:
        body = await _json_object(request)
        payload = str(body.get("payload") or "").strip()
        if not payload:
            raise HTTPException(status_code=400, detail="payload required")
        expand_max = body.get("expand_max")
        format_arg = str(body.get("format") or "").strip() or None
        directive = (
            str(body.get("focus") or body.get("orientation") or body.get("directive") or "").strip() or None
        )
        if not directive:
            from ..telegram_bot import parse_message_directive

            payload, parsed_dir = parse_message_directive(payload)
            if parsed_dir:
                directive = parsed_dir

        ingest_kwargs: dict[str, Any] = {
            "source": "api",
            "expand_max": expand_max,
        }
        if format_arg is not None:
            ingest_kwargs["format"] = format_arg
        if directive is not None:
            ingest_kwargs["directive"] = directive
        try:
            report = await _run_expensive(
                svc.ingest,
                payload,
                **ingest_kwargs,
            )
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            # 원본은 raw_inbox에 남아 replay-failed로 복구할 수 있다.
            _log_operation_failure(request, "ingest", exc)
            return JSONResponse(
                {"error": "ingest failed", "ok": False},
                status_code=500,
            )
        return JSONResponse(report_to_dict(report))

    async def do_search(request: Request) -> JSONResponse:
        body = await _json_object(request)
        query = str(body.get("query") or "").strip()
        if not query:
            raise HTTPException(status_code=400, detail="query required")
        if len(query) > _MAX_SEARCH_QUERY_LENGTH:
            raise HTTPException(status_code=400, detail="query is too long")
        try:
            limit = int(body.get("limit", 8))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="limit must be an integer") from exc
        scope = request_auth_scope(request)
        include_hidden = scope != "anonymous"
        requested_mode = str(body.get("mode") or "").strip().lower()
        if scope == "anonymous":
            mode = "fts"
            limit = max(1, min(_MAX_ANONYMOUS_SEARCH_RESULTS, limit))
            summarize = False
            runner = _run_anonymous_search
        elif scope in {"owner", "readonly"}:
            if requested_mode == "fts":
                mode = "fts"
                limit = max(1, min(_MAX_SEARCH_RESULTS, limit))
                summarize = False
                runner = _run_anonymous_search
            elif requested_mode in {"", "hybrid", "semantic"}:
                mode = "hybrid"
                limit = max(1, min(_MAX_SEARCH_RESULTS, limit))
                summarize = scope == "owner" and bool(body.get("summarize", True))
                runner = _run_expensive
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"unsupported search mode: {requested_mode}",
                )
        else:
            raise HTTPException(status_code=401, detail="authentication required")

        search_kwargs: dict[str, Any] = {
            "limit": limit,
            "summarize": summarize,
            "mode": mode,
        }
        try:
            result = await runner(
                svc.search,
                query,
                include_hidden=include_hidden,
                **search_kwargs,
            )
        except TypeError:
            result = await runner(
                svc.search,
                query,
                **search_kwargs,
            )
        return JSONResponse(
            {
                "query": result.query,
                "mode": mode,
                "answer": result.answer,
                "hits": [
                    {
                        "id": hit.entity.id,
                        "type": hit.entity.type,
                        "name": hit.entity.name,
                        "via": hit.via,
                        "score": hit.score,
                    }
                    for hit in result.hits
                ],
            }
        )

    async def graph_data(request: Request) -> JSONResponse:
        from ..graphview import graph_json

        include_hidden = request_auth_scope(request) != "anonymous"

        def _graph() -> dict[str, Any]:
            conn = dbm.connect_existing(s.db_file, readonly=True)
            try:
                try:
                    return graph_json(conn, include_hidden=include_hidden)
                except TypeError:
                    return graph_json(conn)
            finally:
                conn.close()

        return JSONResponse(await asyncio.to_thread(_graph))

    async def graph_ui(_request: Request) -> HTMLResponse:
        from ..graphview import render_graph_html

        return HTMLResponse(render_graph_html(s))

    async def favicon_ico_route(_request: Request) -> Response:
        path = _STATIC_ICONS_DIR / "favicon.ico"
        if not path.is_file():
            return PlainTextResponse("Not Found", status_code=404)
        return FileResponse(path, media_type="image/x-icon")

    async def favicon_svg_route(_request: Request) -> Response:
        path = _STATIC_ICONS_DIR / "favicon.svg"
        if not path.is_file():
            return PlainTextResponse("Not Found", status_code=404)
        return FileResponse(path, media_type="image/svg+xml")

    async def apple_touch_route(_request: Request) -> Response:
        path = _STATIC_ICONS_DIR / "apple-touch-icon.png"
        if not path.is_file():
            return PlainTextResponse("Not Found", status_code=404)
        return FileResponse(path, media_type="image/png")

    async def manifest_route(_request: Request) -> Response:
        path = _STATIC_ICONS_DIR / "manifest.json"
        if not path.is_file():
            return PlainTextResponse("Not Found", status_code=404)
        return FileResponse(path, media_type="application/manifest+json")

    async def browserconfig_route(_request: Request) -> Response:
        path = _STATIC_ICONS_DIR / "browserconfig.xml"
        if not path.is_file():
            return PlainTextResponse("Not Found", status_code=404)
        return FileResponse(path, media_type="application/xml")

    async def icon_file_route(request: Request) -> Response:
        rel = request.query_params.get("p", "") or request.query_params.get("name", "")
        if not _ICON_FILENAME_RE.fullmatch(rel):
            return PlainTextResponse("Not Found", status_code=404)
        path = _STATIC_ICONS_DIR / rel
        if not path.is_file():
            return PlainTextResponse("Not Found", status_code=404)
        media_type = None
        if rel.endswith(".png"):
            media_type = "image/png"
        elif rel.endswith(".svg"):
            media_type = "image/svg+xml"
        elif rel.endswith(".ico"):
            media_type = "image/x-icon"
        elif rel.endswith(".json") or rel.endswith(".webmanifest"):
            media_type = "application/manifest+json"
        elif rel.endswith(".xml"):
            media_type = "application/xml"
        return FileResponse(path, media_type=media_type)

    def _create_font_file_handler(name: str) -> Any:
        async def _font_route(_request: Request) -> Response:
            path = _STATIC_FONTS_DIR / name
            if not path.is_file():
                return PlainTextResponse("Not Found", status_code=404)
            return FileResponse(
                path,
                media_type="font/woff2",
                headers={"Cache-Control": "public, max-age=31536000, immutable"},
            )

        return _font_route

    async def font_file_route(request: Request) -> Response:
        rel = request.query_params.get("p", "") or request.query_params.get("name", "")
        if not _FONT_FILENAME_RE.fullmatch(rel):
            return PlainTextResponse("Not Found", status_code=404)
        path = _STATIC_FONTS_DIR / rel
        if not path.is_file():
            return PlainTextResponse("Not Found", status_code=404)
        return FileResponse(
            path,
            media_type="font/woff2",
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    async def image_route(request: Request) -> Response:
        rel = request.query_params.get("p", "")
        if not _IMAGE_PATH_RE.fullmatch(rel):
            return PlainTextResponse("Not Found", status_code=404)
        path = s.data_dir / rel
        if not path.is_file():
            return PlainTextResponse("Not Found", status_code=404)
        return FileResponse(path)

    async def documents_list_route(request: Request) -> JSONResponse:
        from ..graphview import documents_list

        include_hidden = request_auth_scope(request) != "anonymous"

        def _documents() -> dict[str, Any]:
            conn = dbm.connect_existing(s.db_file, readonly=True)
            try:
                format_status = dbm.check_format_mismatch(conn, getattr(s, "render_format", "md"))
                try:
                    docs = documents_list(conn, include_hidden=include_hidden)
                except TypeError:
                    docs = documents_list(conn)
                return {
                    "documents": docs,
                    "format_status": format_status,
                }
            finally:
                conn.close()

        return JSONResponse(await asyncio.to_thread(_documents))

    async def node_detail(request: Request) -> JSONResponse:
        from ..graphview import node_detail as _detail

        node_id = request.query_params.get("id", "")
        if not node_id:
            raise HTTPException(status_code=400, detail="id required")

        include_hidden = request_auth_scope(request) != "anonymous"

        def _load() -> dict[str, Any] | None:
            conn = dbm.connect_existing(s.db_file, readonly=True)
            try:
                try:
                    return _detail(conn, node_id, include_hidden=include_hidden)
                except TypeError:
                    return _detail(conn, node_id)
            finally:
                conn.close()

        report = await asyncio.to_thread(_load)
        if report is None:
            raise HTTPException(status_code=404, detail="not found")
        return JSONResponse(report)

    async def document_detail_route(request: Request) -> JSONResponse:
        from ..graphview import document_detail

        document_id = request.query_params.get("id", "")
        if not document_id:
            raise HTTPException(status_code=400, detail="id required")

        include_hidden = request_auth_scope(request) != "anonymous"

        def _load() -> dict[str, Any] | None:
            conn = dbm.connect_existing(s.db_file, readonly=True)
            try:
                # GET은 readonly 사용자에게도 열리므로 열람 상태를 변경하지 않는다.
                try:
                    return document_detail(conn, document_id, include_hidden=include_hidden)
                except TypeError:
                    return document_detail(conn, document_id)
            finally:
                conn.close()

        report = await asyncio.to_thread(_load)
        if report is None:
            raise HTTPException(status_code=404, detail="not found")
        return JSONResponse(report)

    async def document_seen_route(request: Request) -> JSONResponse:
        body = await _json_object(request)
        document_id = str(body.get("id") or "").strip()
        if not document_id:
            raise HTTPException(status_code=400, detail="id required")

        def _mark() -> bool:
            conn = dbm.connect_existing(s.db_file)
            try:
                if dbm.get_document_row(conn, document_id) is None:
                    return False
                dbm.set_document_seen(conn, document_id, seen=True)
                return True
            finally:
                conn.close()

        if not await asyncio.to_thread(_mark):
            raise HTTPException(status_code=404, detail="not found")
        return JSONResponse({"id": document_id, "seen": True})

    async def document_pin_route(request: Request) -> JSONResponse:
        body = await _json_object(request)
        document_id = str(body.get("id") or "").strip()
        if not document_id:
            raise HTTPException(status_code=400, detail="id required")
        pinned = bool(body.get("pinned", True))

        def _pin() -> bool:
            conn = dbm.connect_existing(s.db_file)
            try:
                return dbm.set_document_pinned(conn, document_id, pinned)
            finally:
                conn.close()

        if not await asyncio.to_thread(_pin):
            raise HTTPException(status_code=404, detail="not found")
        return JSONResponse({"id": document_id, "pinned": pinned})

    async def document_hide_route(request: Request) -> JSONResponse:
        body = await _json_object(request)
        document_id = str(body.get("id") or "").strip()
        if not document_id:
            raise HTTPException(status_code=400, detail="id required")
        hidden = bool(body.get("hidden", True))

        def _hide() -> bool:
            conn = dbm.connect_existing(s.db_file)
            try:
                return dbm.set_document_hidden(conn, document_id, hidden)
            finally:
                conn.close()

        if not await asyncio.to_thread(_hide):
            raise HTTPException(status_code=404, detail="not found")
        return JSONResponse({"id": document_id, "hidden": hidden})

    async def document_title_route(request: Request) -> JSONResponse:
        body = await _json_object(request)
        document_id = str(body.get("id") or "").strip()
        if not document_id:
            raise HTTPException(status_code=400, detail="id required")
        raw_title = body.get("title")
        title = str(raw_title).strip() if raw_title is not None else None

        def _update_title() -> bool:
            conn = dbm.connect_existing(s.db_file)
            try:
                return dbm.set_document_title(conn, document_id, title)
            finally:
                conn.close()

        if not await asyncio.to_thread(_update_title):
            raise HTTPException(status_code=404, detail="not found")
        return JSONResponse({"id": document_id, "title": title})

    async def synthesize_route(request: Request) -> JSONResponse:
        from ..graphview import synthesize

        body = await _json_object(request)
        entity_ids = body.get("node_ids") or []
        if not isinstance(entity_ids, list) or not entity_ids:
            raise HTTPException(status_code=400, detail="node_ids required")
        query = body.get("query")

        def _synthesize() -> dict[str, Any]:
            conn = dbm.connect_existing(s.db_file, readonly=True)
            try:
                return synthesize(conn, svc.provider, entity_ids, query)
            finally:
                conn.close()

        return JSONResponse(await _run_expensive(_synthesize))

    async def research_route(request: Request) -> StreamingResponse:
        from ..expand.research import contextual_research

        body = await _json_object(request)
        query = str(body.get("query") or "").strip()
        if not query:
            raise HTTPException(status_code=400, detail="query required")
        if len(query) > _MAX_SEARCH_QUERY_LENGTH:
            raise HTTPException(status_code=400, detail="query is too long")
        node_id = body.get("node_id") or None
        document_id = body.get("doc_id") or None

        _reserve_expensive_job()
        loop = asyncio.get_running_loop()
        events: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=_PROGRESS_QUEUE_SIZE
        )

        def _enqueue_progress(event: dict[str, Any]) -> None:
            if not events.full():
                events.put_nowait(event)

        def on_progress(event: dict[str, Any]) -> None:
            try:
                loop.call_soon_threadsafe(_enqueue_progress, event)
            except RuntimeError:
                # 서버 종료 뒤 끝난 sync worker의 늦은 진행 알림은 버린다.
                pass

        try:
            task = asyncio.create_task(
                asyncio.to_thread(
                    contextual_research,
                    s,
                    svc.provider,
                    query=query,
                    node_id=node_id,
                    doc_id=document_id,
                    progress=on_progress,
                )
            )
        except BaseException:
            _release_expensive_job()
            raise
        task.add_done_callback(_release_expensive_job)
        task.add_done_callback(_consume_task_result)

        async def stream() -> AsyncIterator[bytes]:
            try:
                while True:
                    if task.done() and events.empty():
                        await asyncio.sleep(0.01)
                        if events.empty():
                            break
                    try:
                        event = await asyncio.wait_for(events.get(), timeout=0.1)
                    except asyncio.TimeoutError:
                        continue
                    yield (json.dumps(event, ensure_ascii=False) + "\n").encode()
                try:
                    result = task.result()
                except Exception as exc:  # noqa: BLE001
                    _log_operation_failure(request, "research", exc)
                    result = {"error": "research failed", "ok": False}
                yield (
                    json.dumps(
                        {"done": True, "result": result},
                        ensure_ascii=False,
                    )
                    + "\n"
                ).encode()
            finally:
                if task.done():
                    _consume_task_result(task)

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    async def ingest_stream_route(request: Request) -> StreamingResponse:
        from ..extract.provider import set_progress_callback

        body = await _json_object(request)
        payload = str(body.get("payload") or "").strip()
        if not payload:
            raise HTTPException(status_code=400, detail="payload required")
        expand_max = body.get("expand_max")
        format_arg = str(body.get("format") or "").strip() or None
        directive = (
            str(body.get("focus") or body.get("orientation") or body.get("directive") or "").strip() or None
        )
        if not directive:
            from ..telegram_bot import parse_message_directive

            payload, parsed_dir = parse_message_directive(payload)
            if parsed_dir:
                directive = parsed_dir

        _reserve_expensive_job()
        loop = asyncio.get_running_loop()
        events: asyncio.Queue[dict[str, str]] = asyncio.Queue(
            maxsize=_PROGRESS_QUEUE_SIZE
        )

        def _enqueue_progress(event: dict[str, str]) -> None:
            if not events.full():
                events.put_nowait(event)

        def on_progress(message: str) -> None:
            try:
                loop.call_soon_threadsafe(
                    _enqueue_progress,
                    {"stage": "work", "msg": message},
                )
            except RuntimeError:
                pass

        def _run() -> Any:
            set_progress_callback(on_progress)
            try:
                ingest_kwargs: dict[str, Any] = {
                    "source": "web",
                    "expand_max": expand_max,
                }
                if format_arg is not None:
                    ingest_kwargs["format"] = format_arg
                if directive is not None:
                    ingest_kwargs["directive"] = directive
                return svc.ingest(
                    payload,
                    **ingest_kwargs,
                )
            finally:
                set_progress_callback(None)

        try:
            task = asyncio.create_task(asyncio.to_thread(_run))
        except BaseException:
            _release_expensive_job()
            raise
        task.add_done_callback(_release_expensive_job)
        task.add_done_callback(_consume_task_result)

        async def stream() -> AsyncIterator[bytes]:
            try:
                while True:
                    if task.done() and events.empty():
                        await asyncio.sleep(0.01)
                        if events.empty():
                            break
                    try:
                        event = await asyncio.wait_for(events.get(), timeout=0.1)
                    except asyncio.TimeoutError:
                        continue
                    yield (json.dumps(event, ensure_ascii=False) + "\n").encode()
                try:
                    result = report_to_dict(task.result())
                except Exception as exc:  # noqa: BLE001
                    _log_operation_failure(request, "ingest stream", exc)
                    result = {"error": "ingest failed", "ok": False}
                yield (
                    json.dumps(
                        {"done": True, "result": result},
                        ensure_ascii=False,
                    )
                    + "\n"
                ).encode()
            finally:
                if task.done():
                    _consume_task_result(task)

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    async def dedup_scan_route(_request: Request) -> JSONResponse:
        from ..graphview import dedup_clusters

        def _scan() -> dict[str, Any]:
            scan = svc.dedup_scan()
            conn = dbm.connect_existing(s.db_file, readonly=True)
            try:
                return dedup_clusters(conn, scan)
            finally:
                conn.close()

        return JSONResponse(await _run_expensive(_scan))

    async def dedup_merge_route(request: Request) -> JSONResponse:
        body = await _json_object(request)
        keeper = str(body.get("keeper") or "").strip()
        losers = [str(item) for item in (body.get("losers") or []) if item]
        if not keeper or not losers:
            raise HTTPException(status_code=400, detail="keeper and losers required")
        try:
            result = await _run_expensive(
                svc.merge_one_cluster,
                keeper,
                losers,
            )
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            _log_operation_failure(request, "dedup merge", exc)
            return JSONResponse(
                {"error": "dedup merge failed"},
                status_code=500,
            )
        return JSONResponse(result)

    async def create_share_route(request: Request) -> JSONResponse:
        body = await _json_object(request)
        document_id = str(body.get("doc_id") or "").strip()
        if not document_id:
            raise HTTPException(status_code=400, detail="doc_id required")

        def _share() -> str | None:
            conn = dbm.connect_existing(s.db_file)
            try:
                if dbm.get_document_row(conn, document_id) is None:
                    return None
                return dbm.create_doc_share(conn, document_id)
            finally:
                conn.close()

        token = await asyncio.to_thread(_share)
        if not token:
            raise HTTPException(status_code=404, detail="document not found")
        return JSONResponse({"token": token, "path": "/p?s=" + token})

    async def shared_doc_page(request: Request) -> Response:
        from ..graphview import document_detail, shared_html

        token = request.query_params.get("s", "")
        if not dbm.plausible_share_token(token):
            return PlainTextResponse("Not Found", status_code=404)

        def _load() -> dict[str, Any] | None:
            conn = dbm.connect_existing(s.db_file, readonly=True)
            try:
                document_id = dbm.resolve_doc_share(conn, token)
                if not document_id:
                    return None
                return document_detail(conn, document_id)
            finally:
                conn.close()

        document = await asyncio.to_thread(_load)
        if document is None:
            return PlainTextResponse("Not Found", status_code=404)
        return HTMLResponse(shared_html(document, s))

    mcp_app = build_mcp_app(s)

    async def mcp_route(request: Request) -> Response:
        response_meta: dict = {}
        chunks: list[bytes] = []

        async def send(message: dict) -> None:
            if message["type"] == "http.response.start":
                response_meta["status"] = message["status"]
                response_meta["headers"] = message.get("headers", [])
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))

        await mcp_app(request.scope, request.receive, send)
        resp = Response(
            content=b"".join(chunks),
            status_code=response_meta.get("status", 500),
        )
        for k, v in response_meta.get("headers", []):
            name = k.decode("latin-1")
            if name.lower() == "content-length":
                continue
            resp.headers[name] = v.decode("latin-1")
        return resp

    @asynccontextmanager
    async def app_lifespan(_app: Starlette):
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    routes = [
        Route("/health", health, methods=["GET"]),
        Route("/favicon.ico", favicon_ico_route, methods=["GET"]),
        Route("/favicon.svg", favicon_svg_route, methods=["GET"]),
        Route("/apple-touch-icon.png", apple_touch_route, methods=["GET"]),
        Route("/apple-touch-icon-precomposed.png", apple_touch_route, methods=["GET"]),
        Route("/manifest.json", manifest_route, methods=["GET"]),
        Route("/site.webmanifest", manifest_route, methods=["GET"]),
        Route("/browserconfig.xml", browserconfig_route, methods=["GET"]),
        Route("/icon", icon_file_route, methods=["GET"]),
        Route("/font", font_file_route, methods=["GET"]),
        *(
            Route(f"/fonts/{font_name}", _create_font_file_handler(font_name), methods=["GET"])
            for font_name in FONTS
        ),
        Route("/whoami", whoami, methods=["GET"]),
        Route("/stats", stats, methods=["GET"]),
        Route("/ingest", do_ingest, methods=["POST"]),
        Route("/ingest-stream", ingest_stream_route, methods=["POST"]),
        Route("/search", do_search, methods=["POST"]),
        Route("/", graph_ui, methods=["GET"]),
        Route("/graph", graph_data, methods=["GET"]),
        Route("/node", node_detail, methods=["GET"]),
        Route("/documents", documents_list_route, methods=["GET"]),
        Route("/image", image_route, methods=["GET"]),
        Route("/document", document_detail_route, methods=["GET"]),
        Route("/document/seen", document_seen_route, methods=["POST"]),
        Route("/document/pin", document_pin_route, methods=["POST"]),
        Route("/document/hide", document_hide_route, methods=["POST"]),
        Route("/document/title", document_title_route, methods=["POST"]),
        Route("/synthesize", synthesize_route, methods=["POST"]),
        Route("/research", research_route, methods=["POST"]),
        Route("/dedup/scan", dedup_scan_route, methods=["POST"]),
        Route("/dedup/merge", dedup_merge_route, methods=["POST"]),
        Route("/share", create_share_route, methods=["POST"]),
        Route("/p", shared_doc_page, methods=["GET"]),
        Route("/mcp", mcp_route, methods=["GET", "POST"]),
    ]
    app = Starlette(
        debug=False,
        routes=routes,
        # Starlette의 ServerErrorMiddleware 안쪽에서 endpoint 예외를 먼저 정제한다.
        middleware=[Middleware(ErrorBoundaryMiddleware)],
        exception_handlers={HTTPException: _http_error},
        lifespan=app_lifespan,
    )
    return wrap_web_app(app, s)


def run_api() -> int:
    s = get_settings()
    try:
        import uvicorn
    except Exception as exc:  # noqa: BLE001
        print(f"Uvicorn 미설치: {exc}\n  uv sync 후 다시 시도하세요.")
        return 2

    try:
        runtime = WebRuntimeConfig.from_settings(s)
        app = create_app(s)
    except ValueError as exc:
        print(f"웹 서비스 설정 오류: {exc}")
        return 2

    logging.basicConfig(level=logging.INFO)
    print(
        "Claire ASGI 웹 서비스 시작: "
        f"{runtime.public_origin} "
        f"(listen={s.inject_host}:{s.inject_port}, env={runtime.environment})"
    )
    uvicorn.run(
        app,
        host=s.inject_host,
        port=s.inject_port,
        workers=1,
        access_log=False,
        proxy_headers=False,
        server_header=False,
        limit_concurrency=64,
        timeout_keep_alive=5,
    )
    return 0
