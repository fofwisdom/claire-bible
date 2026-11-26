"""계정 라우트 (설계 §8.4–§8.7, P1 L1-e).

- `setup`: ``/setup``과 ``GET /``의 설정 리다이렉트
- `login`: ``/login``, ``/logout``, ``/api/auth/reauth``
- `admin`: ``/admin/accounts``, ``/api/admin/accounts``
- `me`: ``/me``, ``/api/me``, ``/api/me/password``
- `keys`: ``/api/me/provider-keys``, ``/api/me/provider-keys/verify`` (네트워크 모드 멤버, §11)
- `features`: 유저 단위 기능 ``/api/me/telegram``, ``/api/support/bundles*``, ``/api/me/data-bundles``, ``/api/me/backups*``, ``/api/admin/account-backups*``
- `federation`: ``/api/admin/federation``, ``/api/admin/federation/domain-blocks``, ``/admin/federation``, ``/api/federation/status``, ``/api/federation/followers`` (Fedify sidecar design Sections 9.2, 11, and 12)
- `knowledge_federation`: ``/api/admin/federation/known-servers``, ``/api/admin/federation/journal`` (KNOWLEDGE_FEDERATION_DESIGN.md Sections 6.2 and 13)
- `notifications`: notifications and Web Push of the account ``/api/me/notifications*``, ``/api/me/push*`` (PWA_NOTIFICATION_DESIGN.md PN5)
- `feeds`: account feeds ``/api/feeds``, ``/api/feeds/poll``, ``/api/feeds/opml``, ``/api/feeds/items`` (FEDERATION_REMOTE_READING_DESIGN.md Section 1.3 rule 7)
- `storage`: object storage settings ``/api/admin/storage``, ``/api/admin/storage/test`` (FEDERATION_REMOTE_READING_DESIGN.md Section 9.2)
- `common`: DB·쿠키·origin·단계 인증·템플릿 공용 헬퍼

`api.server.create_app`이 `build_account_routes`로 라우트를 마운트하고, 접근 규칙은 `api.security.ROUTE_POLICY`에 있다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from .. import security
from . import admin, console, features, federation, feeds, idp, server, invites, keys, login, me, setup, storage
from . import laboratory
from . import plugins
from . import federation_notes
from . import federation_quotes
from . import opinion_actions
from . import opinion_moderation
from . import rendition_marks
from . import knowledge_federation
from . import notifications as notification_routes
from .common import AccountsContext, build_context, prepare_auth_db, prepare_client_db, require_recent_auth

__all__ = [
    "AccountRoutes",
    "AccountsContext",
    "build_account_routes",
    "prepare_auth_db",
    "prepare_client_db",
    "require_recent_auth",
]


@dataclass(frozen=True, slots=True)
class AccountRoutes:
    context: AccountsContext
    routes: tuple[Route, ...]

    async def setup_redirect(self, request: Request) -> Response | None:
        """``GET /``에서 부른다. 설정 전 관리자 세션이면 ``/setup``으로 보내는 응답, 아니면 None."""
        return await setup.setup_redirect(request, self.context)


def build_account_routes(
    settings: Any,
    config: security.WebRuntimeConfig,
    *,
    settings_getter: Callable[[], Any] | None = None,
) -> AccountRoutes:
    ctx = build_context(settings, config, settings_getter=settings_getter)
    routes = (
        *setup.routes(ctx),
        *login.routes(ctx),
        *admin.routes(ctx),
        *me.routes(ctx),
        *server.routes(ctx),
        *federation.routes(ctx),
        *federation_notes.routes(ctx),
        *federation_quotes.routes(ctx),
        *rendition_marks.routes(ctx),
        *opinion_moderation.routes(ctx),
        *opinion_actions.routes(ctx),
        *knowledge_federation.routes(ctx),
        *idp.routes(ctx),
        *invites.routes(ctx),
        *keys.routes(ctx),
        *features.routes(ctx),
        *notification_routes.routes(ctx),
        *feeds.routes(ctx),
        *storage.routes(ctx),
        *plugins.routes(ctx),
        *laboratory.routes(ctx),
        *console.routes(ctx),
    )
    return AccountRoutes(context=ctx, routes=routes)
