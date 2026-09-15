"""Fetcher 공통."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from ...ontology.base import Document

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
