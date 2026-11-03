"""컬렉션 계약 검증 (설계 §7.4–§7.6, §18.6).

루트·페이지 형식, 커서 의미, 커서 둘 이상 400, 절대 링크, 가시 범위 totalItems, 캐시·Vary 헤더, HTML 협상.
"""

from __future__ import annotations

import pytest
from starlette.exceptions import HTTPException
from starlette.requests import Request

from claire import ids
from claire.api import collections


def _make_request(method: str = "GET", headers: dict[str, str] | None = None) -> Request:
    raw_headers = [
        (k.lower().encode("latin-1"), v.encode("latin-1"))
        for k, v in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "method": method,
        "headers": raw_headers,
        "path": "/themes",
    }
    return Request(scope)


def test_parse_collection_query_defaults():
    q = collections.parse_collection_query({})
    assert q.page is False
    assert q.max_id is None
    assert q.min_id is None
    assert q.since_id is None
    assert q.limit == 20


def test_parse_collection_query_page_and_limit():
    valid_id = str(ids.MIN_CURRENT_NUMBER + 1000)
    q = collections.parse_collection_query({"page": "true", "limit": "50", "max_id": valid_id})
    assert q.page is True
    assert q.limit == 50
    assert q.max_id == ids.MIN_CURRENT_NUMBER + 1000


def test_parse_collection_query_multiple_cursors_rejected():
    valid_id = str(ids.MIN_CURRENT_NUMBER + 1000)
    with pytest.raises(HTTPException) as exc_info:
        collections.parse_collection_query({"max_id": valid_id, "min_id": valid_id})
    assert exc_info.value.status_code == 400
    assert "at most one cursor" in exc_info.value.detail


def test_parse_collection_query_invalid_cursor_rejected():
    with pytest.raises(HTTPException) as exc_info:
        collections.parse_collection_query({"max_id": "not-a-number"})
    assert exc_info.value.status_code == 400


def test_parse_collection_query_limit_bounds():
    with pytest.raises(HTTPException) as exc_info:
        collections.parse_collection_query({"limit": "0"})
    assert exc_info.value.status_code == 400

    with pytest.raises(HTTPException) as exc_info:
        collections.parse_collection_query({"limit": "81"})
    assert exc_info.value.status_code == 400

    with pytest.raises(HTTPException) as exc_info:
        collections.parse_collection_query({"limit": "abc"})
    assert exc_info.value.status_code == 400


def test_wants_html_negotiation():
    # 1. Sec-Fetch-Dest: document
    req = _make_request("GET", {"sec-fetch-dest": "document"})
    assert collections.wants_html(req) is True

    # 2. Accept: text/html first item
    req = _make_request("GET", {"accept": "text/html,application/xhtml+xml,application/xml;q=0.9"})
    assert collections.wants_html(req) is True

    # 3. Accept: application/json first item
    req = _make_request("GET", {"accept": "application/json, text/html"})
    assert collections.wants_html(req) is False

    # 4. POST method
    req = _make_request("POST", {"sec-fetch-dest": "document"})
    assert collections.wants_html(req) is False


def test_collection_root_format():
    root = collections.collection_root(
        collection_url="https://claire.example.com/@alice/themes",
        total_items=3,
    )
    assert root == {
        "id": "https://claire.example.com/@alice/themes",
        "type": "OrderedCollection",
        "totalItems": 3,
        "first": "https://claire.example.com/@alice/themes?page=true",
    }


def test_collection_page_format():
    col_url = "https://claire.example.com/@alice/themes"
    num1 = ids.MIN_CURRENT_NUMBER + 1000
    num2 = num1 + 10
    items = [
        {"number": str(num2), "label": "T2"},
        {"number": str(num1), "label": "T1"},
    ]
    query = collections.CollectionQuery(
        page=True, max_id=None, min_id=None, since_id=None, limit=2
    )

    page = collections.collection_page(
        collection_url=col_url,
        query=query,
        items=items,
        item_number="number",
        is_first_page=False,
    )
    assert page["id"] == f"{col_url}?page=true&limit=2"
    assert page["type"] == "OrderedCollectionPage"
    assert page["partOf"] == col_url
    assert page["orderedItems"] == items
    assert page["next"] == f"{col_url}?page=true&max_id={num1}&limit=2"
    assert page["prev"] == f"{col_url}?page=true&min_id={num2}&limit=2"


def test_collection_etag_and_cache_headers():
    etag1 = collections.collection_etag(["100:12345", "101:12346"])
    etag2 = collections.collection_etag(["100:12345", "101:12346"])
    etag3 = collections.collection_etag(["100:12345"])
    assert etag1 == etag2
    assert etag1 != etag3
    assert etag1.startswith('"') and etag1.endswith('"')

    anon_headers = collections.collection_cache_headers(anonymous=True, etag=etag1)
    assert anon_headers["Cache-Control"] == "public, max-age=60"
    assert anon_headers["ETag"] == etag1
    assert anon_headers["Vary"] == "Cookie, Authorization, Sec-Fetch-Dest, Accept"

    auth_headers = collections.collection_cache_headers(anonymous=False, etag=etag1)
    assert auth_headers["Cache-Control"] == "private, no-cache"
    assert auth_headers["ETag"] == etag1
