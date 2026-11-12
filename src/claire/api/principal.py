"""요청 주체 (설계 §8.3).

`api.security.AuthenticationMiddleware`가 요청마다 ``Principal`` 하나를 ``request.state``에 둔다. 새 코드는 ``request_principal``을 쓰고, `api.security.request_auth_scope`는 호환용으로 남는다.

§8.3과 다른 점: 단계 인증(§8.5)을 라우트가 DB를 다시 읽지 않고 판정하도록 ``authenticated_at``을 더했다. cookie·x-session 채널에서만 값이 있다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

from starlette.requests import Request

from ..i18n.locales import LocaleCode

PRINCIPAL_STATE_KEY = "claire_principal"
STEP_UP_WINDOW_SECONDS = 15 * 60

PrincipalScope = Literal["owner", "collaborator", "readonly", "anonymous"]
PrincipalChannel = Literal["cookie", "bearer", "x-session", "none"]


class PrincipalMissing(RuntimeError):
    """인증 미들웨어를 거치지 않은 요청에서 ``request_principal``을 불렀다(배선 결함)."""


@dataclass(frozen=True, slots=True)
class Principal:
    account_id: int | None                  # None = 익명
    instance_role: Literal["admin", "member"] | None
    scope: PrincipalScope
    channel: PrincipalChannel
    credential_id: str | None               # 감사 기록용 식별자. 토큰 값이 아니다
    authenticated_at: float | None = None   # 사람이 마지막으로 인증한 시각 (cookie·x-session)
    locale: LocaleCode | None = None        # accounts.locale. None = automatic negotiation (I18N design Section 5.3)

    @property
    def is_anonymous(self) -> bool:
        return self.account_id is None


ANONYMOUS = Principal(
    account_id=None,
    instance_role=None,
    scope="anonymous",
    channel="none",
    credential_id=None,
)


def request_principal(request: Request) -> Principal:
    """미들웨어가 둔 주체. 없으면 ``PrincipalMissing``(익명으로 대체하지 않는다, C1)."""
    principal = getattr(request.state, PRINCIPAL_STATE_KEY, None)
    if not isinstance(principal, Principal):
        raise PrincipalMissing(
            "request has no Principal; AuthenticationMiddleware did not run for this request"
        )
    return principal


def has_recent_auth(principal: Principal, *, now: float | None = None) -> bool:
    """``authenticated_at``이 ``STEP_UP_WINDOW_SECONDS`` 안이면 True. bearer·익명은 항상 False.

    경과 시간이 정확히 ``STEP_UP_WINDOW_SECONDS``이면 아직 안이다. ``authenticated_at``이 ``now``보다 미래이면 시계가 어긋난 것이므로 False다(C1).
    """
    if principal.channel not in ("cookie", "x-session") or principal.authenticated_at is None:
        return False
    current = time.time() if now is None else now
    elapsed = current - principal.authenticated_at
    return 0 <= elapsed <= STEP_UP_WINDOW_SECONDS
