"""개발 실행의 listen 주소 계산 (설계 §5.1 개발 환경 포트 분리 배선, O16).

운영과 컨테이너는 ``(CLAIRE_INJECT_HOST, CLAIRE_INJECT_PORT)`` 하나만 연다. 개발 컨테이너는
``CLAIRE_INJECT_HOST=0.0.0.0``이고, ``docker-compose.dev.yml``이 역할 호스트 포트를 같은
컨테이너 포트로 연결한다.

uv로 로컬에서 ``claire serve-api``를 띄우면 그 연결이 없다. 그래서 ``development``이고
``CLAIRE_USERCONTENT_FQDN``·``CLAIRE_MEDIA_FQDN``이 ``IPv4:포트``이며 그 IPv4가
``CLAIRE_INJECT_HOST``와 문자 그대로 같을 때만 그 포트도 같은 프로세스에서 연다. 요청이
어느 포트로 들어오든 ``HostRouter``가 ``Host`` 헤더로 역할을 고른다.

역할 호스트 값의 형식 검증은 ``HostRoles.from_settings``가 기동 때 한다. 여기서는 조건에 맞지
않는 값을 추가 리스너로 삼지 않을 뿐이다.
"""

from __future__ import annotations

import ipaddress
from typing import Any

__all__ = ["Listener", "dev_listeners"]

Listener = tuple[str, int]

_ROLE_HOST_FIELDS = ("usercontent_fqdn", "media_fqdn")


def dev_listeners(settings: Any) -> tuple[Listener, ...]:
    """설정에서 listen할 ``(host, port)`` 목록을 계산한다. 첫 항목은 항상 app 리스너다."""
    host = str(getattr(settings, "inject_host", ""))
    port = int(getattr(settings, "inject_port"))
    listeners: list[Listener] = [(host, port)]
    if str(getattr(settings, "environment", "") or "") != "development":
        return tuple(listeners)
    for field in _ROLE_HOST_FIELDS:
        role_port = _same_ipv4_port(str(getattr(settings, field, "") or ""), host)
        if role_port is not None and (host, role_port) not in listeners:
            listeners.append((host, role_port))
    return tuple(listeners)


def _same_ipv4_port(authority: str, inject_host: str) -> int | None:
    """``authority``가 ``{inject_host}:포트``(inject_host는 구체적인 IPv4)이면 그 포트."""
    ip, sep, raw_port = authority.rpartition(":")
    if not sep or ip != inject_host:
        return None
    try:
        address = ipaddress.IPv4Address(ip)
    except ValueError:
        return None
    if address.is_unspecified or not raw_port.isascii() or not raw_port.isdigit():
        return None
    port = int(raw_port)
    if not 1 <= port <= 65535:
        return None
    return port
