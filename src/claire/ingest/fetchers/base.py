"""Fetcher 공통."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from ...ontology.base import Document

# 적재 작업이 묶어 둔 설정(테마 소유 계정 컨텍스트를 입힌 사본, 설계 §10.2). 라우터(``router.fetch``)가 설정을 넘기지 않으므로
# fetcher는 이 값을 쓴다. 멤버 작업이 관리자 전역 설정(키·STT·VLM)으로 새지 않게 하는 경로다.
_FETCH_SETTINGS: ContextVar[Any] = ContextVar("claire_fetch_settings", default=None)


@contextmanager
def bound_fetch_settings(settings: Any) -> Iterator[Any]:
    """이 블록 안의 fetch가 ``settings``를 쓰게 한다. ``None``이면 아무것도 묶지 않는다."""
    if settings is None:
        yield None
        return
    token = _FETCH_SETTINGS.set(settings)
    try:
        yield settings
    finally:
        _FETCH_SETTINGS.reset(token)


def fetch_settings(settings: Any = None, fallback: Any = None) -> Any:
    """fetcher가 쓸 설정. 넘겨받은 값 → 작업이 묶은 값 → (작업 밖 호스트 운영자 경로만) 전역 설정.

    ``fallback``은 전역 설정 함수(fetcher 모듈의 ``get_settings``)다. 작업이 설정을 묶었으면 절대 부르지 않는다.
    """
    if settings is not None:
        return settings
    bound = _FETCH_SETTINGS.get()
    if bound is not None:
        return bound
    if fallback is None:
        from ...config import get_settings as fallback

    return fallback()


def attach_usage_hooks(provider: Any, settings: Any) -> Any:
    """멤버 컨텍스트이면 API 프로바이더(STT·VLM)에 예산 훅을 붙인다. 관리자·컨텍스트 없음은 그대로 둔다."""
    if provider is None:
        return provider
    from ...accounts.provider_policy import context_of

    ctx = context_of(settings) if settings is not None else None
    if ctx is None or ctx.is_admin or not hasattr(provider, "usage_hooks"):
        return provider
    from ...accounts import server_tier
    from ...store import auth_db

    try:
        provider.usage_hooks = server_tier.usage_hooks_for(
            ctx, auth_db_path=auth_db.auth_db_path(settings.data_dir)
        )
    except AttributeError:
        pass
    return provider

class FetchError(Exception):
    """fetch 실패. 메시지는 사용자에게 보고된다."""

@dataclass
class WebAdapterResult:
    title: str | None
    text: str | None
    links: list[str]
    anchors: dict[str, str]
    images: list[dict[str, Any]]
    is_pdf: bool = False
    doc_type: str = "web"
    parser_info: dict[str, Any] | None = None
    biblio: dict[str, Any] | None = None

class BaseFetcher(ABC):
    @classmethod
    @abstractmethod
    def can_handle(cls, url: str) -> bool:
        pass

    @classmethod
    @abstractmethod
    def fetch(cls, url: str, **kwargs: Any) -> Document:
        pass

class BaseWebAdapter(ABC):
    @classmethod
    @abstractmethod
    def name(cls) -> str:
        pass

    @classmethod
    @abstractmethod
    def try_fetch(cls, url: str, **kwargs: Any) -> WebAdapterResult | None:
        pass
