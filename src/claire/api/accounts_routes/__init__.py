"""계정 라우트 (설계 §8.4–§8.7, P1 L1-e).

- `setup`: ``/setup``과 ``GET /``의 설정 리다이렉트
- `login`: ``/login``, ``/logout``, ``/api/auth/reauth``
- `admin`: ``/admin/accounts``, ``/api/admin/accounts``
- `me`: ``/me``, ``/api/me``, ``/api/me/password``
- `common`: DB·쿠키·origin·단계 인증·템플릿 공용 헬퍼

`api.server.create_app`이 `build_account_routes`로 라우트를 마운트하고, 접근 규칙은 `api.security.ROUTE_POLICY`에 있다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from .. import security
from . import admin, login, me, setup
from .common import AccountsContext, build_context, prepare_auth_db, require_recent_auth

__all__ = [
    "AccountRoutes",
    "AccountsContext",
    "build_account_routes",
    "prepare_auth_db",
    "require_recent_auth",
]


@dataclass(frozen=True, slots=True)
class AccountRoutes:
    context: AccountsContext
    routes: tuple[Route, ...]

    async def setup_redirect(self, request: Request) -> Response | None:
        """``GET /``에서 부른다. 설정 전 관리자 세션이면 ``/setup``으로 보내는 응답, 아니면 None."""
        return await setup.setup_redirect(request, self.context)


def build_account_routes(settings: Any, config: security.WebRuntimeConfig) -> AccountRoutes:
    ctx = build_context(settings, config)
    routes = (*setup.routes(ctx), *login.routes(ctx), *admin.routes(ctx), *me.routes(ctx))
    return AccountRoutes(context=ctx, routes=routes)
