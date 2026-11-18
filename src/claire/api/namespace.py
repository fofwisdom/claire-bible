"""URI 네임스페이스 (설계 §6, Rev. 2.3 2단계 분리).

요청 경로의 접두(``/@{username}``, ``/themes/{번호}``)를 두 단계로 처리한다(§6.7).

1. ``PrefixParserMiddleware`` (CORS 앞): ``raw_path`` 정규화·거부(§6.4)와 구문 접두 분리만 한다. DB를 읽지 않는다. ``scope``를 얕은 복사해 ``path``를 남은 경로로 바꾸고, 떼어 낸 접두를 ``ParsedPrefix``로 요청 상태에 둔다. 그래서 바깥의 ``SafeAccessLog``는 원래 경로를 기록하고, 안쪽의 CORS·인증은 ``ROUTE_POLICY``를 정규 경로로 찾는다.
2. ``NamespaceResolverMiddleware`` (``AuthenticationMiddleware`` 바로 뒤): ``Principal``이 정해진 뒤 계정·테마를 DB에서 찾고 N1·N2'·N3·N4·N5를 판정해 ``ResolvedNamespace``를 요청 상태에 둔다. 한 번 정한 값은 요청이 끝날 때까지 바뀌지 않는다(N6).

라우트 코드는 ``request_namespace``만 읽는다. 링크는 ``namespace_link``·``account_home_path``·``theme_base_path``로 만든다(§6.6).
"""

from __future__ import annotations

import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, quote

from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from .. import ids
from ..accounts.username_policy import USERNAME_RE
from ..store import accounts as accounts_store
from ..store import db as dbm
from ..store import kb_collab
from ..store.accounts import Account, ServerMode
from .principal import PRINCIPAL_STATE_KEY, Principal

__all__ = [
    "NAMESPACE_STATE_KEY",
    "PARSED_PREFIX_STATE_KEY",
    "PREFIX_STATE_KEY",
    "NamespaceDirectory",
    "NamespaceMissing",
    "NamespaceResolverMiddleware",
    "ParsedPrefix",
    "PathRedirect",
    "PathRejected",
    "PrefixParserMiddleware",
    "ResolvedNamespace",
    "SqliteNamespaceDirectory",
    "account_home_path",
    "namespace_link",
    "parse_request_path",
    "request_namespace",
    "shell_bootstrap",
    "kb_base_path",
    "theme_base_path",
]

# ``ParsedPrefix`` (1단계 결과)
PARSED_PREFIX_STATE_KEY = "claire_parsed_prefix"
# 떼어 낸 접두 문자열(§6.3 N2). 링크 생성용. 접두가 없으면 ""
PREFIX_STATE_KEY = "claire_namespace_prefix"
# ``ResolvedNamespace`` (2단계 결과)
NAMESPACE_STATE_KEY = "claire_namespace"
# 2단계가 함께 두는 부가 값(내부용): 네임스페이스 계정의 ``public_id``(shell_bootstrap)와 고정 테마의 GA 측정 ID(CSP, §5.3)
NAMESPACE_EXTRA_STATE_KEY = "claire_namespace_extra"


class PathRejected(Exception):
    """정규화 규칙(§6.4)이나 접두 문법(§6.1)에 맞지 않는 경로. ``status``는 400 또는 404다."""

    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


class PathRedirect(Exception):
    """정규 표기로 308 리다이렉트할 경로(대문자 USERNAME, 끝 슬래시 누락). ``location``은 query를 포함한 상대 경로다."""

    def __init__(self, location: str) -> None:
        super().__init__(location)
        self.location = location


class NamespaceMissing(RuntimeError):
    """``NamespaceResolverMiddleware``를 거치지 않은 요청에서 ``request_namespace``를 불렀다(배선 결함, C1)."""


@dataclass(frozen=True, slots=True)
class ParsedPrefix:
    """1단계 구문 분리 결과. DB를 보지 않았으므로 계정·테마가 실제로 있는지는 모른다."""

    prefix: str                 # 떼어 낸 접두(예: "/@alice/themes/116412345678901234"). 없으면 ""
    username: str | None        # 소문자로 정규화한 USERNAME. ``/@`` 접두가 없으면 None
    theme_number: int | None    # ``/themes/{번호}`` 접두의 번호(옛 링크: 308만 한다). 없으면 None
    inner_path: str             # 남은 경로. 비어 있으면 "/"
    kb_number: int | None = None  # ``/kb/{번호}`` 접두의 번호(루트에서만). 공유 지식 베이스를 고정한다(설계 §7)


@dataclass(frozen=True, slots=True)
class ResolvedNamespace:
    """2단계 판정 결과(§6.3). 라우트·``_get_theme_ctx``가 읽는 유일한 네임스페이스 값이다."""

    mode: ServerMode
    # 네임스페이스 계정. ``system`` 표면과 네트워크 모드 루트 ``GET /``(디렉터리)에서만 None이다.
    account_id: int | None
    # 링크에 쓸 USERNAME. In single mode it is the USERNAME of the admin under ``/@{username}/`` and None at a root path (KNOWLEDGE_FEDERATION_DESIGN.md Section 15; rule S9 is withdrawn).
    username: str | None
    # 경로가 고정한 테마의 내부 ID. 고정하지 않았으면 None
    pinned_kb_id: int | None
    # 링크 접두(§6.6). 싱글 모드 루트 경로는 "", 그 밖에는 두 모드 모두 "/@{u}"
    prefix: str
    # 요청 경로에 접두가 실제로 있었는가(N4·N5 판정과 canonical 링크용)
    via_prefix: bool


class NamespaceDirectory(Protocol):
    """``NamespaceResolverMiddleware``가 쓰는 조회 계층. 메모리 캐시와 변경 시 무효화는 구현 몫이다(§6.7)."""

    def mode(self) -> ServerMode: ...

    def admin(self) -> Account: ...

    def account_by_username(self, username: str) -> Account | None: ...

    def account(self, account_id: int) -> Account: ...

    def tombstone_was_public(self, username: str) -> bool | None:
        """퇴역 이름이면 ``was_public``, 퇴역 이름이 아니면 None(§8.9 410 판정)."""
        ...

    def theme_by_number(self, number: int) -> Any | None:
        """``store.knowledge_base.KnowledgeBaseInfo`` 또는 None."""
        ...

    def can_view_theme(self, principal: Principal, theme: Any) -> bool:
        """공개 테마이거나 본인 소유·위임 범위면 True(N2', §9.2)."""
        ...


# --- 1단계: 구문 분리 (§6.1, §6.4) -----------------------------------------

# 64비트 시간 정렬 번호(§7.2). 앞자리 0은 받지 않는다(표기 하나, §6.4).
_THEME_NUMBER_RE = re.compile(r"[1-9][0-9]{0,18}")
_THEMES_SEGMENT = "/themes/"
_KB_SEGMENT = "/kb/"
# Location에 다시 실을 때 인코딩하지 않는 문자. ``%``는 넣지 않는다(디코딩된 경로를 다시 인코딩하므로 ``%25``가 되어야 한다).
_PATH_SAFE = "/:@!$&'()*+,;=-._~"
_QUERY_SAFE = _PATH_SAFE + "%?"
# 네트워크 모드 루트 ``GET /``에서 관리자 네임스페이스로 옮길 SPA 상태 쿼리(O8, §6.2)
_SPA_QUERY_KEYS = frozenset({"doc", "node", "theme"})


def _valid_username(name: str) -> bool:
    """§8.1-1 문법(``USERNAME_RE``, ``__`` 연속·``_``로 끝남 거부). 예약 이름은 보지 않는다(경로 해석은 있는 계정만 찾는다)."""
    return (
        name.isascii()
        and USERNAME_RE.fullmatch(name) is not None
        and "__" not in name
        and not name.endswith("_")
    )


def _check_segments(path: str) -> None:
    """빈 세그먼트(끝 슬래시 하나는 제외)와 ``.``·``..`` 세그먼트를 400으로 거부한다."""
    segments = path.split("/")[1:]
    last = len(segments) - 1
    for index, segment in enumerate(segments):
        if segment == "" and index != last:
            raise PathRejected(400, "empty path segment")
        if segment in (".", ".."):
            raise PathRejected(400, "dot path segment")


def _with_query(location: str, query_string: bytes) -> str:
    if query_string:
        return location + "?" + quote(query_string.decode("latin-1"), safe=_QUERY_SAFE)
    return location


def _location(path: str, query_string: bytes) -> str:
    return _with_query(quote(path, safe=_PATH_SAFE), query_string)


def parse_request_path(path: str, raw_path: bytes | None, query_string: bytes = b"") -> ParsedPrefix:
    """§6.1 문법과 §6.4 정규화를 적용한다. DB를 읽지 않는다.

    - ``raw_path``는 ``b"?"`` 앞까지만 본다. ``%2F``·``%2f``, ``.``·``..`` 세그먼트, 빈 세그먼트(``//``)는 ``PathRejected(400)``.
    - ``/@`` 뒤 이름이 문법(§8.1-1)에 맞지 않으면 ``PathRejected(404)``. 대문자는 ``PathRedirect``(소문자).
    - ``/themes/`` 뒤가 숫자가 아니거나 앞자리 0이면 ``PathRejected(404)``. ``/themes``(정확히)는 접두가 아니다.
    - HTML 셸 기준 경로(``/@u``, ``/@u/themes/{n}``, ``/themes/{n}``)의 끝 슬래시 누락은 ``PathRedirect``.
    """
    path = path or "/"
    if not path.startswith("/"):
        raise PathRejected(404, "path is not absolute")
    if raw_path is not None:
        raw = bytes(raw_path).split(b"?", 1)[0]
        if b"%2f" in raw.lower():
            raise PathRejected(400, "encoded slash")
        _check_segments(raw.decode("latin-1"))
    # ``%2e%2e``처럼 인코딩된 점 세그먼트는 디코딩된 경로에서 잡는다.
    _check_segments(path)

    rest = path
    username: str | None = None
    redirect = False
    if rest.startswith("/@"):
        end = rest.find("/", 2)
        name = rest[2:] if end < 0 else rest[2:end]
        rest = "" if end < 0 else rest[end:]
        lowered = name.lower() if name.isascii() else name
        if not _valid_username(lowered):
            raise PathRejected(404, "username syntax")
        username = lowered
        redirect = lowered != name or rest == ""

    number: int | None = None
    number_text = ""
    if rest.startswith(_THEMES_SEGMENT):
        start = len(_THEMES_SEGMENT)
        end = rest.find("/", start)
        number_text = rest[start:] if end < 0 else rest[start:end]
        after = "" if end < 0 else rest[end:]
        if _THEME_NUMBER_RE.fullmatch(number_text) is None or int(number_text) > ids.MAX_NUMBER:
            raise PathRejected(404, "theme number syntax")
        number = int(number_text)
        rest = after
        redirect = redirect or rest == ""

    # ``/kb/{number}`` pins a shared knowledge base. Only at the root: a shared knowledge base has no owner namespace (design Section 7). ``/kb`` (exactly) is not a prefix.
    kb_number: int | None = None
    if username is None and number is None and rest.startswith(_KB_SEGMENT):
        start = len(_KB_SEGMENT)
        end = rest.find("/", start)
        number_text = rest[start:] if end < 0 else rest[start:end]
        after = "" if end < 0 else rest[end:]
        if _THEME_NUMBER_RE.fullmatch(number_text) is None or int(number_text) > ids.MAX_NUMBER:
            raise PathRejected(404, "knowledge base number syntax")
        kb_number = int(number_text)
        rest = after
        redirect = redirect or rest == ""

    prefix = ("" if username is None else "/@" + username) + (
        "" if number is None else _THEMES_SEGMENT + number_text
    )
    if kb_number is not None:
        prefix = _KB_SEGMENT + number_text
    inner = rest or "/"
    if redirect:
        raise PathRedirect(_location(prefix + inner, query_string))
    return ParsedPrefix(prefix=prefix, username=username, theme_number=number, inner_path=inner, kb_number=kb_number)


def _inner_raw_path(raw_path: Any, prefix: str, inner_path: str) -> bytes:
    if isinstance(raw_path, (bytes, bytearray)):
        raw = bytes(raw_path).split(b"?", 1)[0]
        encoded = prefix.encode("ascii")
        if raw.startswith(encoded):
            return raw[len(encoded):] or b"/"
    return quote(inner_path, safe=_PATH_SAFE + "%").encode("ascii")


async def _send(response: Response, scope: Scope, receive: Receive, send: Send) -> None:
    await response(scope, receive, send)


def _not_found() -> Response:
    return PlainTextResponse("Not Found", status_code=404)


class PrefixParserMiddleware:
    """1단계(§6.7). ``CORSPolicyMiddleware`` 바깥, ``HostRouter``의 app 하위 앱 안쪽에 둔다."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # 상태 dict는 원본 scope에 먼저 만든다. 얕은 복사가 같은 dict를 나눠 가져 바깥 SafeAccessLog도 결과를 본다.
        state = scope.setdefault("state", {})
        query_string = scope.get("query_string", b"") or b""
        try:
            parsed = parse_request_path(
                str(scope.get("path") or "/"), scope.get("raw_path"), query_string
            )
        except PathRejected as exc:
            text = "Bad Request" if exc.status == 400 else "Not Found"
            await _send(PlainTextResponse(text, status_code=exc.status), scope, receive, send)
            return
        except PathRedirect as exc:
            await _send(Response(status_code=308, headers={"Location": exc.location}), scope, receive, send)
            return
        state[PARSED_PREFIX_STATE_KEY] = parsed
        state[PREFIX_STATE_KEY] = parsed.prefix
        if not parsed.prefix:
            await self.app(scope, receive, send)
            return
        # N2: 제자리에서 고치지 않는다. root_path는 건드리지 않아 Mount("/static") 등이 그대로 동작한다.
        inner = dict(scope)
        inner["path"] = parsed.inner_path
        if "raw_path" in scope and scope["raw_path"] is not None:
            inner["raw_path"] = _inner_raw_path(scope["raw_path"], parsed.prefix, parsed.inner_path)
        await self.app(inner, receive, send)


# --- 2단계: 계정·테마 판정 (§6.3) -----------------------------------------


def _spa_query(query_string: bytes) -> bool:
    query = parse_qs(query_string.decode("latin-1"), keep_blank_values=True)
    return any(key in query for key in _SPA_QUERY_KEYS)


@dataclass(frozen=True, slots=True)
class _Outcome:
    namespace: ResolvedNamespace
    public_id: int | None = None
    ga_measurement_id: str | None = None


class NamespaceResolverMiddleware:
    """2단계(§6.7). ``AuthenticationMiddleware`` 바로 안쪽, 라우터 바깥에 둔다."""

    def __init__(self, app: ASGIApp, directory: NamespaceDirectory) -> None:
        self.app = app
        self.directory = directory

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        state = scope.setdefault("state", {})
        outcome = self.resolve(scope)
        if isinstance(outcome, Response):
            await _send(outcome, scope, receive, send)
            return
        state[NAMESPACE_STATE_KEY] = outcome.namespace
        state[NAMESPACE_EXTRA_STATE_KEY] = {
            "public_id": outcome.public_id,
            "ga_measurement_id": outcome.ga_measurement_id,
        }
        await self.app(scope, receive, send)

    # 판정은 동기다. 디렉터리는 메모리 캐시라 보통 stat 두 번이고, 변경 뒤 첫 요청만 작은 표를 다시 읽는다.
    def resolve(self, scope: Scope) -> _Outcome | Response:
        from .security import lookup_route_rule

        state = scope.get("state", {})
        parsed = state.get(PARSED_PREFIX_STATE_KEY)
        if not isinstance(parsed, ParsedPrefix):
            raise NamespaceMissing("PrefixParserMiddleware did not run for this request")
        principal = state.get(PRINCIPAL_STATE_KEY)
        if not isinstance(principal, Principal):
            raise NamespaceMissing("AuthenticationMiddleware did not set a Principal")
        method = str(scope.get("method", "")).upper()
        rule = lookup_route_rule(method, str(scope.get("path") or "/"))
        if rule is None:
            return _not_found()
        directory = self.directory
        mode = directory.mode()
        if mode not in rule.modes:
            return _not_found()
        has_prefix = bool(parsed.prefix)
        if rule.surface == "system":
            if has_prefix:
                return _not_found()  # N5
            return _Outcome(ResolvedNamespace(mode, None, None, None, "", False))
        if rule.surface == "any" and not has_prefix:
            return self._any_outcome(mode, principal, has_prefix=False)
        if rule.surface == "root" and not has_prefix:
            return self._root(mode, scope)
        if rule.surface == "collection":
            # SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 7: at the root, the collection of the server has no namespace account; under ``/@{u}/`` it is the collection of ``u``. A pinned knowledge base has no collection.
            if not has_prefix:
                return _Outcome(ResolvedNamespace(mode, None, None, None, "", False))
            if parsed.kb_number is not None:
                return _not_found()
        return self._namespaced(mode, parsed, principal, scope, any_surface=rule.surface == "any")

    def _admin_public_id(self) -> int | None:
        try:
            return self.directory.account(accounts_store.ADMIN_ACCOUNT_ID).public_id
        except accounts_store.AccountNotFound:
            return None

    def _single_mode_username(self) -> str | None:
        """The USERNAME of the admin when single mode has the namespace ``/@{username}/`` (KNOWLEDGE_FEDERATION_DESIGN.md Section 15): the admin account is ``active`` and has a USERNAME. ``None`` before the first setup is complete."""
        try:
            admin = self.directory.admin()
        except accounts_store.AccountNotFound:
            return None
        if admin.status != "active" or not admin.username:
            return None
        return admin.username

    def _any_outcome(self, mode: ServerMode, principal: Principal, *, has_prefix: bool) -> _Outcome:
        """``any``: 접두를 무시하고 자격증명 계정을 쓴다(§9.1)."""
        username: str | None = None
        public_id: int | None = None
        if principal.account_id is not None:
            try:
                account = self.directory.account(principal.account_id)
            except accounts_store.AccountNotFound:
                account = None
            if account is not None:
                public_id = account.public_id
                username = account.username if mode == "network" else None
        return _Outcome(
            ResolvedNamespace(mode, principal.account_id, username, None, "", has_prefix),
            public_id=public_id,
        )

    def _root(self, mode: ServerMode, scope: Scope) -> _Outcome | Response:
        """접두 없는 ``GET /``(§9.1 ``root``)."""
        if mode == "single":
            # KNOWLEDGE_FEDERATION_DESIGN.md Section 15 rule 2: single mode has no root page. ``/`` gives a temporary redirect (302) to ``/@{username}/`` of the admin; it is temporary because the meaning of ``/`` depends on the server mode.
            username = self._single_mode_username()
            if username is not None:
                return Response(status_code=302, headers={"Location": _location(f"/@{username}/", scope.get("query_string", b"") or b""), "Cache-Control": "no-store"})
            # The admin has no USERNAME (the first setup is not complete, Section 15 rule 3): the route gives ``/setup`` to the admin credential and the page "setup not complete" to each other request.
            return _Outcome(
                ResolvedNamespace(mode, accounts_store.ADMIN_ACCOUNT_ID, None, None, "", False),
                public_id=self._admin_public_id(),
            )
        # 네트워크 모드: 디렉터리 화면은 P3(O8). SPA 상태 쿼리가 있으면 관리자 네임스페이스로 옮긴다(§4.3-2).
        query_string = scope.get("query_string", b"") or b""
        if not _spa_query(query_string):
            return _not_found()
        try:
            admin = self.directory.admin()
        except accounts_store.AccountNotFound:
            return _not_found()
        if admin.username is None:
            return _not_found()
        return _redirect(_location(f"/@{admin.username}/", query_string))

    def _namespaced(
        self,
        mode: ServerMode,
        parsed: ParsedPrefix,
        principal: Principal,
        scope: Scope,
        *,
        any_surface: bool,
    ) -> _Outcome | Response:
        directory = self.directory
        query_string = scope.get("query_string", b"") or b""
        owner: Account | None = None
        if parsed.kb_number is not None:
            return self._kb_path(mode, parsed, principal, query_string, any_surface=any_surface)
        if parsed.username is not None:
            if mode == "single":
                # KNOWLEDGE_FEDERATION_DESIGN.md Section 15: in single mode ``/@{username}/…`` is the namespace of the admin. Each other name gets 404. Rule S9 (no USERNAME in single mode) is withdrawn.
                if self._single_mode_username() != parsed.username:
                    return _not_found()
            owner = directory.account_by_username(parsed.username)
            if owner is None or owner.status != "active":
                if directory.tombstone_was_public(parsed.username) is True:
                    return PlainTextResponse("Gone", status_code=410)
                return _not_found()
            # N1: 본인이 아니면(관리자 포함) 공개 프로필만 보인다. Single mode has no profile rule: the knowledge base of the admin was at the root for each reader, and it is at ``/@{username}/`` now.
            if mode == "network" and principal.account_id != owner.id and not owner.profile_public:
                return _not_found()
        elif parsed.theme_number is not None:
            # Old link ``/themes/{number}/…`` with the number of a knowledge base (design Section 7): 308 only.
            return self._old_theme_link(mode, parsed, principal, query_string)
        elif mode == "network" and not any_surface:
            # N3: 접두 없는 namespaced는 자격증명 계정. 익명은 CLAIRE_ANONYMOUS_READONLY=1이어도 404.
            if principal.account_id is None:
                return _not_found()
            try:
                owner = directory.account(principal.account_id)
            except accounts_store.AccountNotFound:
                return _not_found()
            if owner.username is None:
                return _not_found()

        if parsed.theme_number is not None:
            # Old link ``/@{u}/themes/{number}/…`` (design Section 7): 308 only.
            return self._old_theme_link(mode, parsed, principal, query_string)
        if any_surface:
            # 접두를 검증한 뒤 무시한다(존재를 숨기는 404는 위에서 같다).
            return self._any_outcome(mode, principal, has_prefix=bool(parsed.prefix))

        if mode == "single" and owner is None:
            # KNOWLEDGE_FEDERATION_DESIGN.md Section 15 rule 2: a root path of the personal knowledge base gets 308 to ``/@{username}/…``. Only a read (GET, HEAD) gets the redirect: a client that sends a write to a root path does not always follow a redirect, and the write keeps the admin namespace as before. Before the first setup is complete there is no USERNAME and no redirect.
            username = self._single_mode_username()
            if username is not None and str(scope.get("method", "")).upper() in ("GET", "HEAD"):
                return _redirect(_location(f"/@{username}{scope.get('path') or '/'}", query_string))
            # 접두 없는 싱글 모드 namespaced: 관리자 네임스페이스(N3).
            return _Outcome(
                ResolvedNamespace(mode, accounts_store.ADMIN_ACCOUNT_ID, None, None, "", False),
                public_id=self._admin_public_id(),
            )

        assert owner is not None and owner.username is not None
        return _Outcome(
            ResolvedNamespace(mode, owner.id, owner.username, None, f"/@{owner.username}", bool(parsed.prefix)),
            public_id=owner.public_id,
        )

    def _personal_path(self, mode: ServerMode, kb: Any, principal: Principal, inner_path: str, query_string: bytes) -> Response:
        """308 to the path of a personal knowledge base when the requester can read it, else the 404 that hides it (design Section 7)."""
        if not self.directory.can_view_theme(principal, kb):
            return _not_found()
        if mode == "single":
            if kb.account_id != accounts_store.ADMIN_ACCOUNT_ID:
                return _not_found()
            username = self._single_mode_username()
            return _redirect(_location(inner_path if username is None else f"/@{username}{inner_path}", query_string))
        try:
            owner = self.directory.account(int(kb.account_id))
        except accounts_store.AccountNotFound:
            return _not_found()
        if owner.status != "active" or owner.username is None:
            return _not_found()
        # 리다이렉트가 비공개 프로필의 USERNAME을 드러내지 않게 한다(N1과 같은 기준).
        if principal.account_id != owner.id and not owner.profile_public:
            return _not_found()
        return _redirect(_location(f"/@{owner.username}{inner_path}", query_string))

    def _kb_path(self, mode: ServerMode, parsed: ParsedPrefix, principal: Principal, query_string: bytes, *, any_surface: bool) -> _Outcome | Response:
        """``/kb/{number}/…``: a shared knowledge base is pinned with no namespace account; the number of a personal knowledge base gets 308 to its own path (design Section 7)."""
        kb = self.directory.theme_by_number(int(parsed.kb_number or 0))
        if kb is None:
            return _not_found()
        if kb.is_personal:
            return self._personal_path(mode, kb, principal, parsed.inner_path, query_string)
        if any_surface:
            return self._any_outcome(mode, principal, has_prefix=True)
        public_id: int | None = None
        if principal.account_id is not None:
            try:
                public_id = self.directory.account(principal.account_id).public_id
            except accounts_store.AccountNotFound:
                public_id = None
        return _Outcome(
            ResolvedNamespace(mode, None, None, kb.id, parsed.prefix, True),
            public_id=public_id,
            ga_measurement_id=str(getattr(kb, "ga_measurement_id", "") or ""),
        )

    def _old_theme_link(self, mode: ServerMode, parsed: ParsedPrefix, principal: Principal, query_string: bytes) -> Response:
        """Old links ``/themes/{number}/…`` and ``/@{u}/themes/{number}/…``: shared → 308 ``/kb/{number}/…``; personal → 308 to its path when readable, else 404."""
        kb = self.directory.theme_by_number(int(parsed.theme_number or 0))
        if kb is None:
            return self._theme_page(mode, parsed, query_string)
        if kb.is_personal:
            return self._personal_path(mode, kb, principal, parsed.inner_path, query_string)
        return _redirect(_location(f"{_KB_SEGMENT}{parsed.theme_number}{parsed.inner_path}", query_string))

    def _theme_page(self, mode: ServerMode, parsed: ParsedPrefix, query_string: bytes) -> _Outcome | Response:
        """``/themes/{number}/`` with the number of a theme (design Section 7): the page of the theme, without a namespace account. A merged theme gets 308 to the theme it was merged into; an empty or unknown number gets 404. A theme is not a partition: it has no inner paths, and no ``/@{u}/`` form."""
        lookup = getattr(self.directory, "theme_route", None)
        if lookup is None or parsed.username is not None or parsed.inner_path != "/":
            return _not_found()
        route = lookup(int(parsed.theme_number or 0))
        if route is None:
            return _not_found()
        state, merged_into = route
        if state == "merged" and merged_into is not None:
            return _redirect(_location(f"{_THEMES_SEGMENT}{merged_into}/", query_string))
        if state != "active":
            return _not_found()
        return _Outcome(ResolvedNamespace(mode, None, None, None, parsed.prefix, True))


def _redirect(location: str) -> Response:
    return Response(status_code=308, headers={"Location": location})


# --- 디렉터리 구현 ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Snapshot:
    mode: ServerMode = "single"
    admin: Account | None = None
    by_id: dict[int, Account] | None = None
    by_username: dict[str, Account] | None = None
    tombstones: dict[str, bool] | None = None
    themes_by_number: dict[int, Any] | None = None


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


class SqliteNamespaceDirectory:
    """``NamespaceDirectory`` 구현(§6.7). ``claire.db``의 계정·퇴역 이름·테마 번호·모드를 메모리에 두고, DB 파일(본 파일과 ``-wal``)이 바뀌면 다시 읽는다(``SqliteLegacyHostDirectory``와 같은 방식).

    DB가 없거나 표가 아직 없으면(v15 이전) 싱글 모드에 계정·테마가 없는 것으로 본다. ``server_settings.mode``가 알 수 없는 값이면 ``RuntimeError``다(C1).
    """

    def __init__(self, db_file: str | Path | None) -> None:
        self.db_file = None if db_file is None else Path(db_file)
        self._lock = threading.Lock()
        self._signature: tuple[Any, ...] | None = None
        self._snapshot = _Snapshot()

    def _file_signature(self) -> tuple[tuple[Any, ...], bool]:
        if self.db_file is None:
            return (None,), True
        return dbm.db_file_signature(self.db_file)

    def _load(self) -> _Snapshot:
        if self.db_file is None or not self.db_file.is_file():
            return _Snapshot()
        from ..store.knowledge_base import _row_to_theme

        # 읽기 전용 URI로 직접 연다. 파일을 만들지도, journal 모드를 바꾸지도 않는다(``dbm.connect_existing``과 같은 조건).
        conn = sqlite3.connect(self.db_file.resolve().as_uri() + "?mode=ro", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout=5000;")
            tables = {
                str(row[0])
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            store = accounts_store.AccountStore(conn)
            mode: ServerMode = "single"
            if "server_settings" in tables:
                mode = store.server_mode()
            accounts = store.list_accounts() if "accounts" in tables else []
            tombstones: dict[str, bool] = {}
            if "username_tombstones" in tables:
                for row in conn.execute(
                    "SELECT username, was_public FROM username_tombstones WHERE released_at IS NULL"
                ).fetchall():
                    tombstones[str(row[0])] = bool(row[1])
            themes: dict[int, Any] = {}
            if "knowledge_bases" in tables and "number" in _table_columns(conn, "knowledge_bases"):
                for row in conn.execute("SELECT * FROM knowledge_bases WHERE number IS NOT NULL").fetchall():
                    theme = _row_to_theme(row, default_db_path=str(self.db_file))
                    # A row that is neither personal nor shared is unavailable: each route gives 404 (design Section 12.2 step 3).
                    if kb_collab.kind_violation_reason(theme.is_personal, theme.account_id, theme.is_public, theme.knowledge_type):
                        continue
                    if theme.number is not None:
                        themes[int(theme.number)] = theme
        finally:
            conn.close()
        return _Snapshot(
            mode=mode,
            admin=next((a for a in accounts if a.role == "admin"), None),
            by_id={a.id: a for a in accounts},
            by_username={a.username: a for a in accounts if a.username is not None},
            tombstones=tombstones,
            themes_by_number=themes,
        )

    def _current(self) -> _Snapshot:
        with self._lock:
            signature, trusted = self._file_signature()
            if signature != self._signature:
                self._snapshot = self._load()
                self._signature = signature if trusted else None
            return self._snapshot

    def invalidate(self) -> None:
        with self._lock:
            self._signature = None

    def mode(self) -> ServerMode:
        return self._current().mode

    def admin(self) -> Account:
        admin = self._current().admin
        if admin is None:
            raise accounts_store.AccountNotFound("admin")
        return admin

    def account_by_username(self, username: str) -> Account | None:
        return (self._current().by_username or {}).get(username)

    def account(self, account_id: int) -> Account:
        account = (self._current().by_id or {}).get(account_id)
        if account is None:
            raise accounts_store.AccountNotFound(account_id)
        return account

    def tombstone_was_public(self, username: str) -> bool | None:
        return (self._current().tombstones or {}).get(username)

    def theme_by_number(self, number: int) -> Any | None:
        return (self._current().themes_by_number or {}).get(number)

    def theme_route(self, number: int) -> tuple[str, int | None] | None:
        """The state of the theme with this number and, for a merged theme, the number of the theme at the end of its merge chain. None: no theme has the number."""
        if self.db_file is None or not self.db_file.is_file():
            return None
        conn = sqlite3.connect(f"file:{self.db_file}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT id, state, merged_into_theme_id, number FROM themes WHERE number=?", (int(number),)).fetchone()
            if row is None:
                return None
            state, target, seen = str(row[1]), None, {row[0]}
            while row is not None and row[1] == "merged" and row[2] is not None:
                row = conn.execute("SELECT id, state, merged_into_theme_id, number FROM themes WHERE id=?", (row[2],)).fetchone()
                if row is None or row[0] in seen:
                    break
                seen.add(row[0])
                target = int(row[3])
            return state, target
        except sqlite3.OperationalError:
            return None  # the table is not there yet (a database before version 28)
        finally:
            conn.close()

    def can_view_theme(self, principal: Principal, theme: Any) -> bool:
        if bool(getattr(theme, "is_public", False)):
            return True
        # A private knowledge base is personal: only its owner reads it (design Section 6.5).
        return principal.account_id is not None and principal.account_id == getattr(theme, "account_id", None)


# --- 라우트·링크 헬퍼 (§6.6) -----------------------------------------------


def request_namespace(request: Request) -> ResolvedNamespace:
    """미들웨어가 둔 네임스페이스. 없으면 ``NamespaceMissing``(관리자 네임스페이스로 대체하지 않는다, C1)."""
    namespace = request.scope.get("state", {}).get(NAMESPACE_STATE_KEY)
    if not isinstance(namespace, ResolvedNamespace):
        raise NamespaceMissing(
            "request has no ResolvedNamespace; NamespaceResolverMiddleware did not run for this request"
        )
    return namespace


def account_home_path(mode: ServerMode, username: str | None) -> str:
    """계정 홈 경로(§6.6). 싱글 모드는 ``/``, 네트워크 모드는 ``/@{username}/``. 네트워크 모드에서 username이 없으면 ``ValueError``."""
    if mode == "single":
        return "/"
    if mode != "network":
        raise ValueError(f"unknown server mode: {mode!r}")
    if not username:
        raise ValueError("network mode account home needs a username")
    return f"/@{username}/"


def kb_base_path(number: int) -> str:
    """The pinned path of a shared knowledge base: ``/kb/{number}/`` in each mode (design Section 7)."""
    return f"{_KB_SEGMENT}{ids.format_public_id(number)}/"


def theme_base_path(mode: ServerMode, username: str | None, number: int) -> str:
    """테마 고정 경로(§6.2). 싱글 모드 ``/themes/{n}/``, 네트워크 모드 ``/@{username}/themes/{n}/``."""
    text = ids.format_public_id(number)
    if mode == "single":
        return f"{_THEMES_SEGMENT}{text}/"
    if mode != "network":
        raise ValueError(f"unknown server mode: {mode!r}")
    if not username:
        raise ValueError("network mode theme path needs a username")
    return f"/@{username}{_THEMES_SEGMENT}{text}/"


def _public_origin(request: Request) -> str:
    app = request.scope.get("app")
    config = getattr(getattr(app, "state", None), "runtime_config", None)
    origin = getattr(config, "public_origin", None)
    if isinstance(origin, str) and origin:
        return origin.rstrip("/")
    return str(request.base_url).rstrip("/")


def namespace_link(request: Request, inner_path: str, *, absolute: bool = True) -> str:
    """``public_origin + namespace.prefix + inner_path``(§6.6). ``inner_path``는 "/"로 시작한다."""
    if not inner_path.startswith("/"):
        raise ValueError("inner_path must start with '/'")
    path = request_namespace(request).prefix + inner_path
    return _public_origin(request) + path if absolute else path


def shell_bootstrap(request: Request) -> dict[str, str]:
    """HTML 셸에 주입할 부트스트랩 값(§6.4 WebUI 기준 분리).

    키는 정확히 다음 셋이다. 템플릿은 ``<meta name="claire-{key}" content="...">``로 싣는다.
    - ``api-base``: 테마·네임스페이스 기준 API 경로(끝 슬래시 포함). 예: ``/``, ``/themes/1164…/``, ``/@alice/``
    - ``root-base``: 서버 루트(``/``). ``any``·``system`` 표면 호출용
    - ``storage-key``: ``localStorage`` 키 접두(계정 ``public_id`` 기반, 예: ``a116412345678901234``). USERNAME을 쓰지 않는다(S9)

    네임스페이스 계정을 모르면(``system`` 표면, 계정 행이 없는 DB) ``storage-key``는 ``anon``이다.
    """
    namespace = request_namespace(request)
    extra = request.scope.get("state", {}).get(NAMESPACE_EXTRA_STATE_KEY) or {}
    public_id = extra.get("public_id")
    return {
        "api-base": namespace.prefix + "/",
        "root-base": "/",
        "storage-key": f"a{public_id}" if public_id is not None else "anon",
    }
