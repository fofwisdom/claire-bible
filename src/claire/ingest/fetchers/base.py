"""Fetcher 공통."""

from __future__ import annotations


class FetchError(Exception):
    """fetch 실패. 메시지는 사용자에게 보고된다."""
