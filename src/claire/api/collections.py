"""2단계 컬렉션과 커서 페이지네이션 (설계 §7.4–§7.6).

루트(``OrderedCollection``)와 페이지(``OrderedCollectionPage``)로 나눈다. 번호는 JSON에서 10진 문자열이다(§7.1 c). ActivityStreams 어휘를 빌렸지만 연합 엔드포인트가 아니므로 ``@context``를 싣지 않는다.

이 모듈은 저장소를 모른다. 호출자가 커서 조건으로 조회한 항목과 가시 범위 개수를 넘긴다.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from starlette.exceptions import HTTPException
from starlette.requests import Request

from .. import ids

__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "CollectionQuery",
    "collection_cache_headers",
    "collection_etag",
    "collection_page",
    "collection_root",
    "parse_collection_query",
    "wants_html",
]

DEFAULT_LIMIT = 20
MAX_LIMIT = 80


@dataclass(frozen=True, slots=True)
class CollectionQuery:
    """``?page=true``와 커서. 커서는 많아야 하나다(둘 이상이면 400, C1)."""

    page: bool
    max_id: int | None
    min_id: int | None
    since_id: int | None
    limit: int


def parse_collection_query(query: Mapping[str, str] | Any) -> CollectionQuery:
    """쿼리를 검증한다. 커서 둘 이상, 번호 형식 오류(``ids.parse_public_id``), ``limit`` 범위 밖(1–80)은 ``HTTPException(400)``."""
    params = dict(query) if hasattr(query, "items") else {}
    page = str(params.get("page", "")).strip().lower() == "true"

    cursors = {
        "max_id": params.get("max_id"),
        "min_id": params.get("min_id"),
        "since_id": params.get("since_id"),
    }
    non_empty = {k: str(v).strip() for k, v in cursors.items() if v is not None and str(v).strip() != ""}
    if len(non_empty) > 1:
        raise HTTPException(
            status_code=400,
            detail="at most one cursor parameter (max_id, min_id, since_id) is allowed",
        )

    parsed_cursors: dict[str, int | None] = {"max_id": None, "min_id": None, "since_id": None}
    for k, v in non_empty.items():
        try:
            parsed_cursors[k] = ids.parse_public_id(v)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"invalid {k} format: {exc}") from None

    limit_raw = params.get("limit")
    if limit_raw is None or str(limit_raw).strip() == "":
        limit = DEFAULT_LIMIT
    else:
        try:
            limit = int(str(limit_raw).strip())
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="limit must be an integer between 1 and 80") from None
        if not (1 <= limit <= MAX_LIMIT):
            raise HTTPException(status_code=400, detail=f"limit must be between 1 and {MAX_LIMIT}")

    return CollectionQuery(
        page=page,
        max_id=parsed_cursors["max_id"],
        min_id=parsed_cursors["min_id"],
        since_id=parsed_cursors["since_id"],
        limit=limit,
    )


def wants_html(request: Request) -> bool:
    """§6.5 결정론적 협상. ``GET``이고 ``Sec-Fetch-Dest: document``이거나, ``Sec-Fetch-Dest``가 없고 ``Accept`` 첫 항목이 ``text/html``이면 True."""
    if request.method != "GET":
        return False
    sec_dest = request.headers.get("sec-fetch-dest")
    if sec_dest == "document":
        return True
    if not sec_dest:
        accept = request.headers.get("accept", "")
        if accept:
            first = accept.split(",")[0].strip().split(";")[0].strip().lower()
            if first == "text/html":
                return True
    return False


def collection_root(*, collection_url: str, total_items: int) -> dict[str, Any]:
    """루트 응답. ``first``는 ``{collection_url}?page=true``."""
    return {
        "id": collection_url,
        "type": "OrderedCollection",
        "totalItems": int(total_items),
        "first": f"{collection_url}?page=true",
    }


def collection_page(
    *,
    collection_url: str,
    query: CollectionQuery,
    items: Sequence[Mapping[str, Any]],
    item_number: str,
    is_first_page: bool,
) -> dict[str, Any]:
    """페이지 응답. ``items``는 번호 내림차순이다(``min_id``는 호출자가 오름차순으로 조회해 뒤집어 넘긴다).

    ``item_number``는 각 항목에서 커서 번호(문자열)를 꺼낼 키다. ``next``는 ``len(items) == limit``일 때만, ``prev``는 첫 페이지가 아닐 때만 싣는다. 모든 링크는 절대 URL이다.
    """
    parts = ["page=true"]
    if query.max_id is not None:
        parts.append(f"max_id={ids.format_public_id(query.max_id)}")
    elif query.min_id is not None:
        parts.append(f"min_id={ids.format_public_id(query.min_id)}")
    elif query.since_id is not None:
        parts.append(f"since_id={ids.format_public_id(query.since_id)}")
    if query.limit != DEFAULT_LIMIT:
        parts.append(f"limit={query.limit}")

    page_id = f"{collection_url}?{'&'.join(parts)}"
    page: dict[str, Any] = {
        "id": page_id,
        "type": "OrderedCollectionPage",
        "partOf": collection_url,
        "orderedItems": list(items),
    }

    limit_suffix = f"&limit={query.limit}" if query.limit != DEFAULT_LIMIT else ""
    if len(items) == query.limit:
        last_num = str(items[-1][item_number])
        page["next"] = f"{collection_url}?page=true&max_id={last_num}{limit_suffix}"

    if not is_first_page and len(items) > 0:
        first_num = str(items[0][item_number])
        page["prev"] = f"{collection_url}?page=true&min_id={first_num}{limit_suffix}"

    return page


def collection_etag(parts: Sequence[str]) -> str:
    """보이는 항목의 번호와 갱신 시각으로 만든 강한 ETag(따옴표 포함)."""
    raw = "\n".join(parts).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()[:32]
    return f'"{digest}"'


def collection_cache_headers(*, anonymous: bool, etag: str) -> dict[str, str]:
    """§7.5. 익명 ``public, max-age=60``, 인증 ``private, no-cache``. ``Vary: Cookie, Authorization, Sec-Fetch-Dest, Accept``. ``immutable``은 쓰지 않는다."""
    return {
        "Cache-Control": "public, max-age=60" if anonymous else "private, no-cache",
        "ETag": etag,
        "Vary": "Cookie, Authorization, Sec-Fetch-Dest, Accept",
    }
