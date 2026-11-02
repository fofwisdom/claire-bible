"""USERNAME 규칙 (설계 §8.1).

이 모듈은 DB 없이 판정할 수 있는 규칙(문법, 예약 이름·조각, 인스턴스 예약)만 다룬다. 퇴역(``retired``)과 중복(``taken``)은 `store.accounts.AccountStore`가 같은 ``UsernameRejected``로 거부한다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Literal

RejectReason = Literal["syntax", "reserved", "retired", "taken"]

USERNAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,29}$")

# §8.1-2 정확히 일치하는 예약 이름.
RESERVED_USERNAMES: frozenset[str] = frozenset(
    {
        # 운영·권한
        "admin", "administrator", "root", "sysadmin", "superuser", "owner", "operator",
        "system", "sysop", "moderator", "mod", "staff", "official", "team",
        # 지원·연락
        "support", "help", "info", "contact", "security", "abuse", "postmaster",
        "hostmaster", "webmaster", "noc", "billing",
        # 일반
        "user", "users", "guest", "anonymous", "nobody", "everyone", "all", "public",
        "private", "me", "self", "null", "undefined", "test", "demo",
        # 서비스
        "claire", "api", "www", "mail", "mcp", "bot", "media", "usercontent", "explore",
        "invite",
    }
)

_RESERVED_FRAGMENT_PREFIX = "admin"
_FRAGMENT_SPLIT_RE = re.compile(r"[_0-9]+")


class UsernameRejected(ValueError):
    """USERNAME을 쓸 수 없다. ``reason``만 가입·설정 화면에 보여 준다(§8.1-7)."""

    def __init__(self, reason: RejectReason) -> None:
        super().__init__(reason)
        self.reason: RejectReason = reason


def normalize_username(raw: str) -> str:
    """검사·저장에 쓸 형태로 바꾼다.

    ASCII가 아니면 소문자로 바꾸기 **전에** ``syntax``로 거부한다. ``"\\u212a".lower() == "k"``처럼 비ASCII 문자가 ASCII로 접히는 사칭을 막기 위해서다. 공백을 잘라 내지 않는다(C1).
    """
    if not raw.isascii():
        raise UsernameRejected("syntax")
    return raw.lower()


def username_fragments(name: str) -> tuple[str, ...]:
    """``_``와 숫자로 나눈 비어 있지 않은 조각. 예: ``kim_admin2`` → ``("kim", "admin")``."""
    return tuple(part for part in _FRAGMENT_SPLIT_RE.split(name) if part)


def instance_reserved_labels(hosts: Iterable[str]) -> frozenset[str]:
    """호스트 이름들의 DNS 라벨을 소문자로 모은다. 예: ``claire.example.com`` → ``{"claire", "example", "com"}``.

    포트가 붙어 있으면 떼고, 빈 값은 건너뛴다.
    """
    labels: set[str] = set()
    for host in hosts:
        if not host:
            continue
        name, sep, port = host.rpartition(":")
        if sep and port.isdigit():
            host = name
        for label in host.lower().split("."):
            if label:
                labels.add(label)
    return frozenset(labels)


def validate_username(raw: str, *, instance_reserved: frozenset[str] = frozenset()) -> str:
    """정규화한 USERNAME을 반환하거나 ``UsernameRejected("syntax" | "reserved")``를 낸다.

    순서: 정규화(§8.1-1) → 문법(``USERNAME_RE``, ``__`` 연속·``_``로 끝남 거부) → 예약 이름(§8.1-2) → 예약 조각(§8.1-3: 조각이 예약 이름이거나 ``admin``으로 시작) → 인스턴스 예약(§8.1-4, 정확히 일치).
    """
    name = normalize_username(raw)
    # ``$``는 끝의 개행 앞에서도 맞으므로 fullmatch로 검사한다.
    if USERNAME_RE.fullmatch(name) is None or "__" in name or name.endswith("_"):
        raise UsernameRejected("syntax")
    if name in RESERVED_USERNAMES:
        raise UsernameRejected("reserved")
    for fragment in username_fragments(name):
        if fragment in RESERVED_USERNAMES or fragment.startswith(_RESERVED_FRAGMENT_PREFIX):
            raise UsernameRejected("reserved")
    if name in instance_reserved:
        raise UsernameRejected("reserved")
    return name
