"""Shared helpers and modular BFF route builders extracted from `claire.api.server`."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
import logging
from typing import Any

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import Response

from ..security import read_json_body, request_id


def with_deprecation_headers(
    handler: Callable[[Request], Awaitable[Response]],
    successor: str,
) -> Callable[[Request], Awaitable[Response]]:
    """Wrap a legacy alias handler to emit RFC 8594 `Deprecation: true` and `Link` successor headers."""
    link_value = f'<{successor}>; rel="successor-version"'

    @wraps(handler)
    async def _wrapped(request: Request) -> Response:
        response = await handler(request)
        response.headers["Deprecation"] = "true"
        existing_link = response.headers.get("Link")
        response.headers["Link"] = f"{existing_link}, {link_value}" if existing_link else link_value
        return response

    return _wrapped


async def read_json_object(request: Request) -> dict[str, Any]:
    """Read and validate a JSON object request body."""
    body = await read_json_body(request)
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="json object required")
    return body


def log_operation_failure(logger: logging.Logger, request: Request, operation: str, exc: Exception) -> None:
    """Log safe failure trace fields without leaking secrets."""
    logger.warning(
        "%s failed request_id=%s error_type=%s",
        operation,
        request_id(request),
        type(exc).__name__,
    )


from .client_extraction import build_client_extraction_routes  # noqa: E402
from .providers_limits import build_providers_limits_routes  # noqa: E402
from .public_site import FONTS, build_public_site_routes  # noqa: E402
from .reference import build_reference_routes  # noqa: E402

__all__ = [
    "FONTS",
    "build_client_extraction_routes",
    "build_providers_limits_routes",
    "build_public_site_routes",
    "build_reference_routes",
    "log_operation_failure",
    "read_json_object",
    "with_deprecation_headers",
]
