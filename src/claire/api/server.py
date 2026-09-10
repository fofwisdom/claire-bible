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
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

PUBLIC_PATHS: tuple[str, ...] = ("/static/",)

from ..config import Settings, get_settings
from ..ingest.report_json import report_to_dict
from ..ingest.service import IngestService, IngestServicePool
from ..store import db as dbm
from ..store.queries import theme_summary
from ..store.theme import ThemeInfo, ThemeManager, get_theme_manager
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


class GateMiddleware:
    """gate 미들웨어 — PUBLIC_PATHS에 등록된 공개 정적 자산(/static/) 등은
    인증 실패로 차단되지 않도록 Starlette 라우터로 통과시키며,
    그 외 경로는 기존 보안 래퍼(wrap_web_app)로 전달한다.
    """

    def __init__(
        self,
        inner: ASGIApp,
        secured: ASGIApp,
        public_paths: tuple[str, ...] = PUBLIC_PATHS,
    ) -> None:
        self.inner = inner
        self.app = secured
        self.public_paths = public_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            path = scope.get("path", "")
            method = str(scope.get("method", "")).upper()
            if any(path.startswith(prefix) for prefix in self.public_paths):
                if method in {"GET", "HEAD"}:
                    await self.inner(scope, receive, send)
                    return
                resp = PlainTextResponse("Method Not Allowed", status_code=405)
                await resp(scope, receive, send)
                return
        await self.app(scope, receive, send)


gate = GateMiddleware


def _resolve_query_func(name: str, default: Any) -> Any:
    """테스트 monkeypatch 및 하위 호환성을 위해 graphview 속성이 패치된 경우 우선 사용한다."""
    try:
        from .. import graphview

        fn = getattr(graphview, name, None)
        if fn is not None and fn is not default:
            return fn
    except ImportError:
        pass
    return default


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
    theme_mgr = ThemeManager(s)
    service_pool = IngestServicePool(s, svc, theme_manager=theme_mgr)
    active_expensive_jobs = 0
    active_anonymous_search_jobs = 0

    def _extract_theme_ref(
        request: Request, body: dict[str, Any] | None = None
    ) -> int | str | None:
        raw = request.query_params.get("theme")
        if raw is not None and str(raw).strip():
            return str(raw).strip()
        if body and body.get("theme") is not None:
            return str(body["theme"]).strip()
        hdr = request.headers.get("x-claire-theme")
        if hdr is not None and hdr.strip():
            return hdr.strip()
        return None

    def _get_theme_ctx(
        request: Request, body: dict[str, Any] | None = None
    ) -> tuple[ThemeInfo, Settings, IngestService]:
        ref = _extract_theme_ref(request, body)
        theme = theme_mgr.get_theme(ref)
        theme_settings = theme_mgr.get_settings_for_theme(theme.id, s)
        theme_svc = service_pool.get_service(theme.id)
        return theme, theme_settings, theme_svc

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
        theme, theme_settings, _ = _get_theme_ctx(request)

        def _counts() -> dict[str, Any]:
            conn = dbm.connect_existing(theme_settings.db_file, readonly=True)
            try:
                try:
                    c = dict(dbm.counts(conn, include_hidden=include_hidden))
                except TypeError:
                    c = dict(dbm.counts(conn))
                c["theme_id"] = theme.id
                c["theme_label"] = theme.label
                return c
            finally:
                conn.close()

        return JSONResponse(await asyncio.to_thread(_counts))

    async def themes_list_route(request: Request) -> JSONResponse:
        include_hidden = request_auth_scope(request) != "anonymous"

        def _get_themes_with_stats() -> dict[str, Any]:
            all_themes = theme_mgr.list_themes()
            result = []
            for t in all_themes:
                abs_db = theme_mgr.get_settings_for_theme(t.id, s).db_file
                t_stats = {"documents": 0, "entities": 0, "relations": 0}
                if abs_db.is_file():
                    try:
                        conn = dbm.connect_existing(abs_db, readonly=True)
                        try:
                            t_stats = theme_summary(conn, include_hidden=include_hidden)
                        finally:
                            conn.close()
                    except Exception:
                        pass
                result.append({
                    "id": t.id,
                    "seq": t.seq,
                    "label": t.label,
                    "description": t.description,
                    "icon": t.icon,
                    "is_default": t.is_default,
                    "stats": t_stats,
                })
            return {"themes": result, "default_theme_id": 0}

        return JSONResponse(await asyncio.to_thread(_get_themes_with_stats))

    async def theme_define_route(request: Request) -> JSONResponse:
        scope = request_auth_scope(request)
        if scope != "owner":
            raise HTTPException(
                status_code=403,
                detail="지식 관리자(owner) 권한이 필요합니다.",
            )
        body = await _json_object(request)
        label = str(body.get("label") or "").strip()
        if not label:
            raise HTTPException(status_code=400, detail="label is required")
        desc = str(body.get("description") or "").strip()
        icon = str(body.get("icon") or "📁").strip()
        try:
            theme = theme_mgr.define_theme(label, description=desc, icon=icon)
            return JSONResponse({"ok": True, "theme": theme.to_dict()}, status_code=201)
        except ValueError as val_err:
            raise HTTPException(status_code=400, detail=str(val_err)) from val_err
        except Exception as exc:
            _log_operation_failure(request, "theme define", exc)
            raise HTTPException(status_code=500, detail="failed to define theme") from exc

    async def theme_update_route(request: Request) -> JSONResponse:
        scope = request_auth_scope(request)
        if scope != "owner":
            raise HTTPException(
                status_code=403,
                detail="지식 관리자(owner) 권한이 필요합니다.",
            )
        body = await _json_object(request)
        theme_id = (
            request.query_params.get("id")
            or (str(body.get("id")) if body.get("id") is not None else None)
            or request.path_params.get("theme_id", "")
        )
        if not theme_id:
            raise HTTPException(status_code=400, detail="id is required")
        label = body.get("label")
        desc = body.get("description")
        icon = body.get("icon")
        try:
            theme = theme_mgr.update_theme(
                theme_id, label=label, description=desc, icon=icon
            )
            return JSONResponse({"ok": True, "theme": theme.to_dict()})
        except KeyError as k_err:
            raise HTTPException(status_code=404, detail=str(k_err)) from k_err
        except ValueError as v_err:
            raise HTTPException(status_code=400, detail=str(v_err)) from v_err
        except Exception as exc:
            _log_operation_failure(request, "theme update", exc)
            raise HTTPException(status_code=500, detail="failed to update theme") from exc

    async def theme_delete_route(request: Request) -> JSONResponse:
        scope = request_auth_scope(request)
        if scope != "owner":
            raise HTTPException(
                status_code=403,
                detail="지식 관리자(owner) 권한이 필요합니다.",
            )
        theme_id = (
            request.query_params.get("id")
            or request.path_params.get("theme_id", "")
        )
        if not theme_id and request.headers.get("content-type") == "application/json":
            try:
                body = await _json_object(request)
                if body and body.get("id") is not None:
                    theme_id = str(body["id"])
            except Exception:
                pass
        if not theme_id:
            raise HTTPException(status_code=400, detail="id is required")
        purge = (
            request.query_params.get("purge", "0").strip().lower()
            in ("1", "true", "yes")
        )
        try:
            deleted = theme_mgr.delete_theme(theme_id, purge=purge)
            return JSONResponse({"ok": True, "deleted": deleted.to_dict()})
        except KeyError as k_err:
            raise HTTPException(status_code=404, detail=str(k_err)) from k_err
        except ValueError as v_err:
            raise HTTPException(status_code=400, detail=str(v_err)) from v_err
        except Exception as exc:
            _log_operation_failure(request, "theme delete", exc)
            raise HTTPException(status_code=500, detail="failed to delete theme") from exc

    async def do_ingest(request: Request) -> JSONResponse:
        body = await _json_object(request)
        theme, _, theme_svc = _get_theme_ctx(request, body)
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

        full_content = bool(body.get("full_content") or body.get("no_truncate") or False)
        effort = str(body.get("effort") or "").strip() or None

        ingest_kwargs: dict[str, Any] = {
            "source": "api",
            "expand_max": expand_max,
        }
        if full_content:
            ingest_kwargs["full_content"] = True
        if effort is not None:
            ingest_kwargs["effort"] = effort
        if format_arg is not None:
            ingest_kwargs["format"] = format_arg
        if directive is not None:
            ingest_kwargs["directive"] = directive
        try:
            report = await _run_expensive(
                theme_svc.ingest,
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
        rep_dict = report_to_dict(report)
        rep_dict["theme_id"] = theme.id
        rep_dict["theme_label"] = theme.label
        return JSONResponse(rep_dict)

    async def do_search(request: Request) -> JSONResponse:
        body = await _json_object(request)
        theme, _, theme_svc = _get_theme_ctx(request, body)
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
                theme_svc.search,
                query,
                include_hidden=include_hidden,
                **search_kwargs,
            )
        except TypeError:
            result = await runner(
                theme_svc.search,
                query,
                **search_kwargs,
            )
        return JSONResponse(
            {
                "query": result.query,
                "mode": mode,
                "answer": result.answer,
                "theme_id": theme.id,
                "theme_label": theme.label,
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
        from ..store.queries import graph_json

        _graph_json = _resolve_query_func("graph_json", graph_json)
        include_hidden = request_auth_scope(request) != "anonymous"
        theme, theme_settings, _ = _get_theme_ctx(request)

        def _graph() -> dict[str, Any]:
            conn = dbm.connect_existing(theme_settings.db_file, readonly=True)
            try:
                try:
                    g = _graph_json(conn, include_hidden=include_hidden)
                except TypeError:
                    g = _graph_json(conn)
                g["theme_id"] = theme.id
                g["theme_label"] = theme.label
                return g
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
        from ..store.queries import documents_list

        _documents_list = _resolve_query_func("documents_list", documents_list)
        include_hidden = request_auth_scope(request) != "anonymous"
        theme, theme_settings, _ = _get_theme_ctx(request)

        def _documents() -> dict[str, Any]:
            conn = dbm.connect_existing(theme_settings.db_file, readonly=True)
            try:
                format_status = dbm.check_format_mismatch(conn, getattr(theme_settings, "render_format", "md"))
                try:
                    docs = _documents_list(conn, limit=300, include_hidden=include_hidden)
                except TypeError:
                    docs = _documents_list(conn)
                return {
                    "documents": docs,
                    "format_status": format_status,
                    "theme_id": theme.id,
                    "theme_label": theme.label,
                }
            finally:
                conn.close()

        return JSONResponse(await asyncio.to_thread(_documents))

    async def node_detail(request: Request) -> JSONResponse:
        from ..store.queries import node_detail as _detail

        _detail = _resolve_query_func("node_detail", _detail)
        node_id = request.query_params.get("id", "")
        if not node_id:
            raise HTTPException(status_code=400, detail="id required")

        include_hidden = request_auth_scope(request) != "anonymous"
        theme, theme_settings, _ = _get_theme_ctx(request)

        def _load() -> dict[str, Any] | None:
            conn = dbm.connect_existing(theme_settings.db_file, readonly=True)
            try:
                try:
                    rep = _detail(conn, node_id, include_hidden=include_hidden)
                except TypeError:
                    rep = _detail(conn, node_id)
                if rep is not None:
                    rep["theme_id"] = theme.id
                    rep["theme_label"] = theme.label
                return rep
            finally:
                conn.close()

        report = await asyncio.to_thread(_load)
        if report is None:
            raise HTTPException(status_code=404, detail="not found")
        return JSONResponse(report)

    async def document_detail_route(request: Request) -> JSONResponse:
        from ..store.queries import document_detail

        _document_detail = _resolve_query_func("document_detail", document_detail)
        document_id = request.query_params.get("id", "")
        if not document_id:
            raise HTTPException(status_code=400, detail="id required")

        include_hidden = request_auth_scope(request) != "anonymous"
        theme, theme_settings, _ = _get_theme_ctx(request)

        def _load() -> dict[str, Any] | None:
            conn = dbm.connect_existing(theme_settings.db_file, readonly=True)
            try:
                # GET은 readonly 사용자에게도 열리므로 열람 상태를 변경하지 않는다.
                try:
                    rep = _document_detail(conn, document_id, include_hidden=include_hidden)
                except TypeError:
                    rep = _document_detail(conn, document_id)
                if rep is not None:
                    rep["theme_id"] = theme.id
                    rep["theme_label"] = theme.label
                return rep
            finally:
                conn.close()

        report = await asyncio.to_thread(_load)
        if report is None:
            raise HTTPException(status_code=404, detail="not found")
        return JSONResponse(report)

    async def document_seen_route(request: Request) -> JSONResponse:
        body = await _json_object(request)
        theme, theme_settings, _ = _get_theme_ctx(request, body)
        document_id = str(body.get("id") or "").strip()
        if not document_id:
            raise HTTPException(status_code=400, detail="id required")

        def _mark() -> bool:
            conn = dbm.connect_existing(theme_settings.db_file)
            try:
                if dbm.get_document_row(conn, document_id) is None:
                    return False
                dbm.set_document_seen(conn, document_id, seen=True)
                return True
            finally:
                conn.close()

        if not await asyncio.to_thread(_mark):
            raise HTTPException(status_code=404, detail="not found")
        resp = {"id": document_id, "seen": True}
        if theme.id != 0:
            resp["theme_id"] = theme.id
        return JSONResponse(resp)

    async def document_pin_route(request: Request) -> JSONResponse:
        body = await _json_object(request)
        theme, theme_settings, _ = _get_theme_ctx(request, body)
        document_id = str(body.get("id") or "").strip()
        if not document_id:
            raise HTTPException(status_code=400, detail="id required")
        pinned = bool(body.get("pinned", True))

        def _pin() -> bool:
            conn = dbm.connect_existing(theme_settings.db_file)
            try:
                return dbm.set_document_pinned(conn, document_id, pinned)
            finally:
                conn.close()

        if not await asyncio.to_thread(_pin):
            raise HTTPException(status_code=404, detail="not found")
        resp = {"id": document_id, "pinned": pinned}
        if theme.id != 0:
            resp["theme_id"] = theme.id
        return JSONResponse(resp)

    async def document_hide_route(request: Request) -> JSONResponse:
        body = await _json_object(request)
        theme, theme_settings, _ = _get_theme_ctx(request, body)
        document_id = str(body.get("id") or "").strip()
        if not document_id:
            raise HTTPException(status_code=400, detail="id required")
        hidden = bool(body.get("hidden", True))

        def _hide() -> bool:
            conn = dbm.connect_existing(theme_settings.db_file)
            try:
                return dbm.set_document_hidden(conn, document_id, hidden)
            finally:
                conn.close()

        if not await asyncio.to_thread(_hide):
            raise HTTPException(status_code=404, detail="not found")
        resp = {"id": document_id, "hidden": hidden}
        if theme.id != 0:
            resp["theme_id"] = theme.id
        return JSONResponse(resp)

    async def document_title_route(request: Request) -> JSONResponse:
        body = await _json_object(request)
        theme, theme_settings, _ = _get_theme_ctx(request, body)
        document_id = str(body.get("id") or "").strip()
        if not document_id:
            raise HTTPException(status_code=400, detail="id required")
        raw_title = body.get("title")
        title = str(raw_title).strip() if raw_title is not None else None

        def _update_title() -> bool:
            conn = dbm.connect_existing(theme_settings.db_file)
            try:
                return dbm.set_document_title(conn, document_id, title)
            finally:
                conn.close()

        if not await asyncio.to_thread(_update_title):
            raise HTTPException(status_code=404, detail="not found")
        resp = {"id": document_id, "title": title}
        if theme.id != 0:
            resp["theme_id"] = theme.id
        return JSONResponse(resp)

    async def synthesize_route(request: Request) -> JSONResponse:
        from ..store.queries import synthesize

        _synthesize = _resolve_query_func("synthesize", synthesize)
        body = await _json_object(request)
        theme, theme_settings, theme_svc = _get_theme_ctx(request, body)
        entity_ids = body.get("node_ids") or []
        if not isinstance(entity_ids, list) or not entity_ids:
            raise HTTPException(status_code=400, detail="node_ids required")
        query = body.get("query")

        def _synthesize_job() -> dict[str, Any]:
            conn = dbm.connect_existing(theme_settings.db_file, readonly=True)
            try:
                res = _synthesize(conn, theme_svc.provider, entity_ids, query)
                res["theme_id"] = theme.id
                res["theme_label"] = theme.label
                return res
            finally:
                conn.close()

        return JSONResponse(await _run_expensive(_synthesize_job))

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
        theme, _, theme_svc = _get_theme_ctx(request, body)
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

        full_content = bool(body.get("full_content") or body.get("no_truncate") or False)
        effort = str(body.get("effort") or "").strip() or None

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
                if full_content:
                    ingest_kwargs["full_content"] = True
                if effort is not None:
                    ingest_kwargs["effort"] = effort
                if format_arg is not None:
                    ingest_kwargs["format"] = format_arg
                if directive is not None:
                    ingest_kwargs["directive"] = directive
                return theme_svc.ingest(
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
                    if theme.id != 0:
                        result["theme_id"] = theme.id
                        result["theme_label"] = theme.label
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

    async def dedup_scan_route(request: Request) -> JSONResponse:
        from ..store.queries import dedup_clusters

        _dedup_clusters = _resolve_query_func("dedup_clusters", dedup_clusters)
        theme, theme_settings, theme_svc = _get_theme_ctx(request)

        def _scan() -> dict[str, Any]:
            scan = theme_svc.dedup_scan()
            conn = dbm.connect_existing(theme_settings.db_file, readonly=True)
            try:
                res = _dedup_clusters(conn, scan)
                if theme.id != 0:
                    res["theme_id"] = theme.id
                    res["theme_label"] = theme.label
                return res
            finally:
                conn.close()

        return JSONResponse(await _run_expensive(_scan))

    async def dedup_merge_route(request: Request) -> JSONResponse:
        body = await _json_object(request)
        theme, _, theme_svc = _get_theme_ctx(request, body)
        keeper = str(body.get("keeper") or "").strip()
        losers = [str(item) for item in (body.get("losers") or []) if item]
        if not keeper or not losers:
            raise HTTPException(status_code=400, detail="keeper and losers required")
        try:
            result = await _run_expensive(
                theme_svc.merge_one_cluster,
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
        theme, theme_settings, _ = _get_theme_ctx(request, body)
        document_id = str(body.get("doc_id") or "").strip()
        if not document_id:
            raise HTTPException(status_code=400, detail="doc_id required")

        def _share() -> str | None:
            conn = dbm.connect_existing(theme_settings.db_file)
            try:
                if dbm.get_document_row(conn, document_id) is None:
                    return None
                return dbm.create_doc_share(conn, document_id)
            finally:
                conn.close()

        token = await asyncio.to_thread(_share)
        if not token:
            raise HTTPException(status_code=404, detail="document not found")
        return JSONResponse({"token": token, "path": "/p?s=" + token, "theme_id": theme.id})

    async def shared_doc_page(request: Request) -> Response:
        from ..graphview import shared_html
        from ..store.queries import document_detail

        _document_detail = _resolve_query_func("document_detail", document_detail)
        token = request.query_params.get("s", "")
        if not dbm.plausible_share_token(token):
            return PlainTextResponse("Not Found", status_code=404)

        def _load() -> dict[str, Any] | None:
            # 1. 모든 테마 DB에서 공유 토큰 자동 검색
            resolved = theme_mgr.resolve_share_token(token)
            if resolved is not None:
                return resolved[2]
            # 2. 기본 DB 폴백
            conn = dbm.connect_existing(s.db_file, readonly=True)
            try:
                document_id = dbm.resolve_doc_share(conn, token)
                if not document_id:
                    return None
                return _document_detail(conn, document_id)
            finally:
                conn.close()

        document = await asyncio.to_thread(_load)
        if document is None:
            return PlainTextResponse("Not Found", status_code=404)

        base_url = ""
        if getattr(s, "public_url", ""):
            base_url = s.public_url.rstrip("/")
        else:
            proto = request.headers.get("x-forwarded-proto") or request.url.scheme
            host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
            if proto and host:
                base_url = f"{proto}://{host}".rstrip("/")
            else:
                base_url = str(request.base_url).rstrip("/")

        return HTMLResponse(
            shared_html(document, s, base_url=base_url, share_token=token)
        )

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

    async def create_support_bundle_route(request: Request) -> JSONResponse:
        from ..support_bundle import (
            DEFAULT_SUPPORT_BUNDLE_DAYS,
            create_support_bundle,
            validate_bundle_days,
        )

        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type in ("application/json", "application/problem+json"):
            body = await _json_object(request)
        else:
            body = {}

        raw_days = body.get("days", DEFAULT_SUPPORT_BUNDLE_DAYS)
        try:
            days = int(raw_days)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="days must be an integer")

        target = body.get("target") or body.get("share") or body.get("doc_id")
        if target is not None:
            target = str(target).strip() or None

        max_ret = getattr(s, "telemetry_retention_days", 30)
        try:
            validate_bundle_days(days, max_ret)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        def _generate() -> dict[str, Any]:
            info = create_support_bundle(s, days=days, target=target)
            return info.to_dict()

        try:
            bundle_dict = await asyncio.to_thread(_generate)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return JSONResponse(bundle_dict)

    async def download_support_bundle_route(request: Request) -> Response:
        from ..support_bundle import get_support_bundle, lookup_support_bundle_by_token

        token = request.query_params.get("token", "").strip()
        if not token or len(token) < 16:
            return PlainTextResponse("Not Found", status_code=404)

        record = await asyncio.to_thread(lookup_support_bundle_by_token, s.data_dir, token)
        if not record:
            return PlainTextResponse("Not Found", status_code=404)

        bundle = await asyncio.to_thread(get_support_bundle, s.data_dir, token)
        if not bundle:
            return JSONResponse(
                {"error": "support bundle has expired and was purged"},
                status_code=410,
            )

        filepath = Path(bundle["filepath"])
        if not filepath.is_file():
            return PlainTextResponse("Not Found", status_code=404)

        return FileResponse(
            str(filepath),
            media_type="application/zstd",
            filename=bundle["filename"],
            headers={
                "Cache-Control": "no-store, private",
                "Content-Disposition": f'attachment; filename="{bundle["filename"]}"',
            },
        )

    @asynccontextmanager
    async def app_lifespan(_app: Starlette):
        from ..support_bundle import purge_expired_bundles

        await asyncio.to_thread(purge_expired_bundles, s.data_dir)
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    static_dir = Path(__file__).resolve().parent.parent / "static"
    static_dir.mkdir(parents=True, exist_ok=True)

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
        Route("/themes", themes_list_route, methods=["GET", "HEAD"]),
        Route("/themes", theme_define_route, methods=["POST"]),
        Route("/themes", theme_update_route, methods=["PATCH"]),
        Route("/themes", theme_delete_route, methods=["DELETE"]),
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
        Route("/support/bundle", create_support_bundle_route, methods=["POST"]),
        Route("/support/bundle", download_support_bundle_route, methods=["GET"]),
        Route("/mcp", mcp_route, methods=["GET", "POST"]),
        Mount("/static", StaticFiles(directory=str(static_dir), check_dir=False), name="static"),
    ]

    app = Starlette(
        debug=False,
        routes=routes,
        # Starlette의 ServerErrorMiddleware 안쪽에서 endpoint 예외를 먼저 정제한다.
        middleware=[Middleware(ErrorBoundaryMiddleware)],
        exception_handlers={HTTPException: _http_error},
        lifespan=app_lifespan,
    )

    def _add_static(prefix: str, path: str | Path, name: str = "static") -> None:
        p = Path(path)
        p.mkdir(parents=True, exist_ok=True)
        norm_prefix = "/" + prefix.strip("/")
        for r in app.router.routes:
            if isinstance(r, Mount) and r.path == norm_prefix:
                return
        app.router.mount(norm_prefix, StaticFiles(directory=str(p), check_dir=False), name=name)

    app.router.add_static = _add_static  # type: ignore[attr-defined]
    app.router.add_static("/static/", path=str(static_dir), name="static")

    secured = wrap_web_app(app, s)
    return GateMiddleware(app, secured, PUBLIC_PATHS)


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
