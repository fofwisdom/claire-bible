"""Unified error envelope and exception boundary for `/api/v1/*` (AR4, PRINCIPLES B1)."""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any

from pydantic import ValidationError
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ...accounts import provider_policy as policy
from ...core.secret_scrub import scrub_text
from ..security import InvalidJSONBody, RequestBodyTooLarge, request_id as get_request_id
from .schemas import ErrorDetail, ErrorEnvelope

log = logging.getLogger("claire.api.v1")

POLICY_ERROR_STATUS: dict[str, int] = {
    policy.ProviderNotAllowed.code: 403,
    policy.ProviderBudgetExceeded.code: 429,
    policy.ProviderNotConfigured.code: 409,
    policy.ProviderKeyRejected.code: 409,
}

_SNAKE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class V1Error(Exception):
    """Structured error raised inside `/api/v1/*` handlers."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | list[Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = int(status_code)
        self.code = _normalize_code(code)
        self.message = str(message)
        self.details = details
        self.headers = dict(headers) if headers else None


def _normalize_code(raw: str, *, fallback: str = "request_failed") -> str:
    candidate = re.sub(r"[^a-z0-9]+", "_", raw.strip().lower()).strip("_")
    if candidate and _SNAKE_RE.match(candidate):
        return candidate
    return fallback


def not_found_error(message: str = "Resource not found") -> V1Error:
    """Return a standard existence-hiding 404 error (PRINCIPLES B1)."""
    return V1Error(404, "not_found", message)


def error_payload(
    code: str,
    message: str,
    *,
    request_id: str,
    details: dict[str, Any] | list[Any] | None = None,
) -> dict[str, Any]:
    """Build and validate a sealed v1 error envelope dictionary."""
    envelope = ErrorEnvelope(
        error=ErrorDetail(
            code=_normalize_code(code),
            message=message,
            request_id=request_id or "-",
            details=details,
        )
    )
    return envelope.model_dump(exclude_none=True)


def error_response(
    request: Request | None,
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | list[Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Create a Starlette JSONResponse containing the unified v1 error envelope."""
    req_id = get_request_id(request) if request is not None else "-"
    return JSONResponse(
        error_payload(code, message, request_id=req_id, details=details),
        status_code=status_code,
        headers=headers,
    )


def validation_error(
    exc: ValidationError,
    *,
    status_code: int = 422,
    code: str = "validation_error",
    message: str = "Request validation failed",
) -> V1Error:
    """Convert a Pydantic ValidationError into a V1Error with field details."""
    items = [
        {
            "loc": [str(part) for part in err.get("loc", ())],
            "message": str(err.get("msg", "invalid value")),
            "type": str(err.get("type", "value_error")),
        }
        for err in exc.errors()
    ]
    return V1Error(status_code, code, message, details={"errors": items})


def _http_exception_code(exc: HTTPException) -> str:
    if exc.status_code == 404:
        return "not_found"
    if isinstance(exc.detail, str) and _SNAKE_RE.match(exc.detail.strip()):
        return exc.detail.strip()
    if exc.status_code == 400:
        return "bad_request"
    if exc.status_code == 401:
        return "authentication_required"
    if exc.status_code == 403:
        return "forbidden"
    if exc.status_code == 409:
        return "conflict"
    return "request_failed"


def exception_to_v1_response(request: Request, exc: Exception) -> JSONResponse:
    """Translate an exception into a unified v1 error envelope response."""
    if isinstance(exc, V1Error):
        return error_response(
            request,
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
            headers=exc.headers,
        )
    if isinstance(exc, InvalidJSONBody):
        msg = exc.detail if isinstance(exc.detail, str) else "Invalid JSON body"
        return error_response(request, status_code=400, code="invalid_json", message=msg)
    if isinstance(exc, RequestBodyTooLarge):
        return error_response(request, status_code=413, code="payload_too_large", message="Request body too large")
    if isinstance(exc, policy.ProviderPolicyError):
        status = POLICY_ERROR_STATUS.get(exc.code, 403)
        remedy = scrub_text(exc.remedy or "")
        details = {"remedy": remedy} if remedy else None
        return error_response(
            request,
            status_code=status,
            code=exc.code,
            message=remedy or exc.code,
            details=details,
        )
    if isinstance(exc, ValidationError):
        v1_exc = validation_error(exc)
        return error_response(
            request,
            status_code=v1_exc.status_code,
            code=v1_exc.code,
            message=v1_exc.message,
            details=v1_exc.details,
        )
    if isinstance(exc, HTTPException):
        detail_text = exc.detail if isinstance(exc.detail, str) else "Request failed"
        if exc.status_code == 404:
            detail_text = "Resource not found"
        return error_response(
            request,
            status_code=exc.status_code,
            code=_http_exception_code(exc),
            message=detail_text,
            headers=dict(exc.headers) if exc.headers else None,
        )
    log.warning("v1 request failed request_id=%s error_type=%s", get_request_id(request), type(exc).__name__)
    return error_response(request, status_code=500, code="internal_error", message="Internal server error")


def v1_endpoint(
    handler: Callable[[Request], Awaitable[Response]],
) -> Callable[[Request], Awaitable[Response]]:
    """Wrap an async Starlette endpoint so all exceptions emit the v1 error envelope."""

    @wraps(handler)
    async def _wrapped(request: Request) -> Response:
        try:
            return await handler(request)
        except Exception as exc:  # noqa: BLE001
            return exception_to_v1_response(request, exc)

    return _wrapped


def v1_exception_handlers() -> dict[Any, Any]:
    """Return Starlette exception handlers for standalone `/api/v1/*` test apps."""

    async def _handle(request: Request, exc: Exception) -> JSONResponse:
        return exception_to_v1_response(request, exc)

    return {
        V1Error: _handle,
        HTTPException: _handle,
        InvalidJSONBody: _handle,
        RequestBodyTooLarge: _handle,
        ValidationError: _handle,
        policy.ProviderPolicyError: _handle,
    }
