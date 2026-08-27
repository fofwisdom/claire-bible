"""MCP(Model Context Protocol) 지원 — 에이전트용 read-only 그래프 탐색 툴.

설계 근거: docs/origin/design/MCP_SUPPORT.md. 인증은 이 모듈이 아니라 server.py / security.py의
게이트 미들웨어가 담당(무토큰/미인증 요청은 /mcp 자체가 존재하지 않는 것처럼 404) — 여기
등록된 툴은 전부 read-only이고 owner/readonly 세션 둘 다 동일하게 접근 가능
(v1, docs/origin/design/MCP_SUPPORT.md — 쓰기 툴이 생기는 다음 마일스톤에서 스코프 구분 도입 필요).

**중요**: `IngestService.search`(`retrieval.query.search`)는 `summarize=False`
여도 벡터 검색을 위해 `provider.embed(query)`를 무조건 호출한다(Gemini 호출).
MCP `search` 툴은 그래서 그 함수를 재사용하지 않고 `db.fts_search`만 직접
써서 Gemini 호출 0을 보장한다(docs/origin/design/MCP_SUPPORT.md 원칙).

각 툴은 `_xxx_impl(conn, ...)` 순수 함수 + `@mcp.tool()` 얇은 커넥션 래퍼로
나뉜다 — impl 함수는 `sqlite3.Connection`을 직접 받아 테스트에서 in-memory
DB로 바로 부를 수 있다(test_mcp_tools.py, test_graphview.py와 동일 패턴).
"""

from __future__ import annotations

import sqlite3
from collections import deque
from datetime import datetime, timezone
from urllib.parse import urlparse

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from .. import graphview
from ..ontology.base import normalize_name
from ..store import db as dbm

MAX_CONTEXT_ENTITIES = 10
MAX_PATH_HOPS = 6
_MAX_PATH_VISITED = 2000
MAX_DOCUMENTS = 100
DEFAULT_DOCUMENTS_LIMIT = 30
MAX_NODE_DOCUMENTS = 10


def _entity_brief(ent) -> dict:
    return {"id": ent.id, "name": ent.name, "type": ent.type}


def _iso_utc(ts: float | None) -> str | None:
    """epoch(초) -> ISO8601 문자열, 타임존 명시(UTC, +00:00) — 에이전트가 어느
    시간대에서 왔는지 모르니 서버가 임의 지역(KST 등)을 가정하지 않고 항상
    명확한 오프셋을 준다. 변환은 호출한 쪽에서."""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _parse_since(s: str | None) -> float | None:
    """'YYYY-MM-DD' 또는 전체 ISO8601(오프셋 포함/'Z' 포함)을 epoch(초)로.
    타임존 없는 문자열은 UTC로 간주(서버 저장값과 동일 기준)."""
    if not s:
        return None
    text = s.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def resolve_entity_impl(conn: sqlite3.Connection, name: str) -> dict:
    norm = normalize_name(name)
    ents = dbm.find_entities_by_name_or_alias(conn, norm)
    if not ents:
        ids = dbm.fts_search(conn, name, limit=5)
        ents = [e for e in (dbm.get_entity(conn, i) for i in ids) if e]
    return {"matches": [{**_entity_brief(e), "aliases": e.aliases} for e in ents]}


def search_impl(
    conn: sqlite3.Connection,
    query: str,
    entity_type: str | None = None,
    near_ids: list[str] | None = None,
    limit: int = 8,
) -> dict:
    # entity_type/near_ids 필터는 FTS 랭킹 뒤에 파이썬에서 거른다 — 후보 풀이
    # 너무 좁으면(headroom) "필터 통과작이 상위 후보에 없어서" 실제로는 있는
    # 매치를 조용히 0건으로 보고하는 거짓음성이 생긴다 — 필터를
    # 쓸 때는 후보 풀을 넉넉히 넓힌다(이 규모의 FTS 쿼리는 비용이 무시할 만함).
    headroom = max(limit * 4, 20)
    if entity_type or near_ids:
        headroom = max(headroom, 200)
    ids = dbm.fts_search(conn, query, limit=headroom)
    ents = [e for e in (dbm.get_entity(conn, i) for i in ids) if e]
    if entity_type:
        ents = [e for e in ents if e.type == entity_type]
    if near_ids:
        allowed: set[str] = set(near_ids)
        for seed in near_ids:
            for r in dbm.neighbors(conn, seed):
                allowed.add(r.target_id)
                allowed.add(r.source_id)
        ents = [e for e in ents if e.id in allowed]
    total = len(ents)
    ents = ents[:limit]
    return {
        "hits": [{**_entity_brief(e), "rank": i + 1} for i, e in enumerate(ents)],
        "truncated": total > len(ents),
        "omitted": max(0, total - len(ents)),
    }


def neighbors_impl(
    conn: sqlite3.Connection,
    entity_ids: str | list[str],
    exclude_ids: list[str] | None = None,
    limit: int = 50,
) -> dict:
    seeds = [entity_ids] if isinstance(entity_ids, str) else list(entity_ids)
    seen_ids = set(seeds) | set(exclude_ids or [])
    found: dict[str, dict] = {}
    for seed in seeds:
        for r in dbm.neighbors(conn, seed):
            out = r.source_id == seed
            other_id = r.target_id if out else r.source_id
            if other_id in seen_ids:
                continue
            entry = found.setdefault(
                other_id,
                {
                    "id": other_id,
                    "name": None,
                    "type": None,
                    "via": [],
                },
            )
            entry["via"].append({"from": seed, "rel": r.type, "dir": "out" if out else "in"})
    out_list = []
    for oid, entry in found.items():
        other = dbm.get_entity(conn, oid)
        if other is None:
            continue
        entry["name"] = other.name
        entry["type"] = other.type
        entry["degree"] = len(dbm.neighbors(conn, oid))
        out_list.append(entry)
    out_list.sort(key=lambda x: x["degree"], reverse=True)
    total = len(out_list)
    out_list = out_list[:limit]
    return {
        "neighbors": out_list,
        "truncated": total > len(out_list),
        "omitted": max(0, total - len(out_list)),
    }


def path_impl(
    conn: sqlite3.Connection,
    from_id: str,
    to_id: str,
    max_hops: int = 4,
) -> dict:
    max_hops = max(1, min(max_hops, MAX_PATH_HOPS))
    if from_id == to_id:
        ent = dbm.get_entity(conn, from_id)
        if ent is None:
            return {"found": False, "reason": "from_id not found"}
        return {"found": True, "path": [_entity_brief(ent)], "relations": []}

    # node_id -> (prev_node_id, rel_type, dir)
    parent: dict[str, tuple[str, str, str]] = {}
    visited = {from_id}
    q = deque([from_id])
    depth = {from_id: 0}
    visited_count = 0
    while q:
        cur = q.popleft()
        if depth[cur] >= max_hops:
            continue
        for r in dbm.neighbors(conn, cur):
            out = r.source_id == cur
            other_id = r.target_id if out else r.source_id
            if other_id in visited:
                continue
            visited.add(other_id)
            parent[other_id] = (cur, r.type, "out" if out else "in")
            depth[other_id] = depth[cur] + 1
            if other_id == to_id:
                q.clear()
                break
            q.append(other_id)
            visited_count += 1
            if visited_count > _MAX_PATH_VISITED:
                return {"found": False, "reason": "search space too large"}

    if to_id not in parent and to_id != from_id:
        return {"found": False, "hops_tried": max_hops}

    chain = [to_id]
    rels = []
    cur = to_id
    while cur != from_id:
        prev, rel_type, direction = parent[cur]
        rels.append({"type": rel_type, "dir": direction})
        chain.append(prev)
        cur = prev
    chain.reverse()
    rels.reverse()
    nodes = []
    for nid in chain:
        ent = dbm.get_entity(conn, nid)
        if ent is None:
            return {"found": False, "reason": "path node vanished"}
        nodes.append(_entity_brief(ent))
    return {"found": True, "path": nodes, "relations": rels}


def context_impl(
    conn: sqlite3.Connection,
    entity_ids: list[str],
    compact: bool = True,
) -> dict:
    truncated = len(entity_ids) > MAX_CONTEXT_ENTITIES
    capped = entity_ids[:MAX_CONTEXT_ENTITIES]
    text, names = graphview.synthesis_context(conn, capped, compact=compact)
    return {
        "context": text,
        "entities": names,
        "truncated": truncated,
        "omitted": max(0, len(entity_ids) - len(capped)),
    }


def overview_impl(conn: sqlite3.Connection) -> dict:
    return {
        "entity_types": [
            {"type": t, "count": n} for t, n in dbm.entity_type_counts(conn)
        ],
        "source_types": [
            {"source_type": t, "count": n} for t, n in dbm.source_type_counts(conn)
        ],
        "hubs": [
            {"name": n, "type": t, "degree": d}
            for n, t, d in dbm.top_connected_entities(conn, limit=10)
        ],
        "most_corroborated": [
            {"name": n, "type": t, "source_count": c}
            for n, t, c in dbm.most_merged_entities(conn, limit=10)
        ],
    }


def document_impl(conn: sqlite3.Connection, document_id: str) -> dict:
    # graphview.document_detail 자체는 부작용이 없다(안읽음 마커는 웹 핸들러에서만 변경)
    rep = graphview.document_detail(conn, document_id)
    if rep is None:
        return {"error": "not found"}
    rep = dict(rep)
    rep["fetched_at"] = _iso_utc(rep.get("fetched_at"))
    return rep


def node_impl(conn: sqlite3.Connection, entity_id: str, full: bool = False) -> dict:
    rep = graphview.node_detail(conn, entity_id)
    if rep is None:
        return {"error": "not found"}
    rep = dict(rep)
    docs = rep.get("documents", [])
    total = len(docs)
    capped = docs[:MAX_NODE_DOCUMENTS]
    out = []
    for d in capped:
        d = dict(d)
        d["fetched_at"] = _iso_utc(d.get("fetched_at"))
        if not full:
            d.pop("detail", None)
        out.append(d)
    rep["documents"] = out
    rep["documents_truncated"] = total > len(out)
    rep["documents_omitted"] = max(0, total - len(out))
    return rep


def documents_impl(
    conn: sqlite3.Connection,
    limit: int = DEFAULT_DOCUMENTS_LIMIT,
    since: str | None = None,
    query: str | None = None,
) -> dict:
    limit = max(1, min(limit, MAX_DOCUMENTS))
    try:
        since_ts = _parse_since(since)
    except ValueError:
        return {"error": "since must be YYYY-MM-DD or ISO8601", "got": since}
    items = graphview.documents_list(conn, limit=limit, since=since_ts, query=query)
    total = dbm.documents_count(conn, since=since_ts, query=query)
    for d in items:
        d["fetched_at"] = _iso_utc(d.get("fetched_at"))
    return {
        "documents": items,
        "truncated": total > len(items),
        "omitted": max(0, total - len(items)),
    }


def build_mcp_app(s: Any):
    """`/mcp` 엔드포인트에 물릴 Starlette ASGI 앱을 생성한다."""

    def _conn() -> sqlite3.Connection:
        db_file = getattr(s, "db_file", getattr(s, "db_path", "data/claire.db"))
        conn = dbm.connect(db_file)
        dbm.init_db(conn)
        return conn

    mcp = MCPServer("claire", version="0.1.0")

    @mcp.tool()
    async def resolve_entity(name: str) -> dict:
        """이름(또는 별칭) 문자열로 엔티티를 찾는다 — 탐색 루프의 진입점.
        ID를 이미 알고 있다면 이 툴 대신 node/neighbors를 바로 쓸 것."""
        conn = _conn()
        try:
            return resolve_entity_impl(conn, name)
        finally:
            conn.close()

    @mcp.tool()
    async def search(
        query: str,
        entity_type: str | None = None,
        near_ids: list[str] | None = None,
        limit: int = 8,
    ) -> dict:
        """전문(FTS) 검색. LLM 호출 없음(raw hits만, 요약은 호출한 에이전트가
        직접 함). entity_type으로 타입 필터, near_ids를 주면 그 노드들의
        1홉 이웃 범위 안에서만 찾는다(지금 탐색 중인 프론티어를 좁혀 검색할
        때 사용 — resolve_entity/neighbors로 얻은 id를 그대로 넘기면 됨).
        결과가 limit을 넘으면 truncated=true(0건이라고 '매치 없음'으로
        오인하지 말 것 — omitted 확인)."""
        conn = _conn()
        try:
            return search_impl(conn, query, entity_type, near_ids, limit)
        finally:
            conn.close()

    @mcp.tool()
    async def neighbors(
        entity_ids: str | list[str],
        exclude_ids: list[str] | None = None,
        limit: int = 50,
    ) -> dict:
        """주어진 엔티티(들)의 1홉 이웃을 합집합으로 반환 — 탐색 루프의 핵심
        단계. 여러 id를 한 번에 넘기면 그 전체 프론티어를 한 번에 넓힌다.
        exclude_ids에 지금까지 방문한 id를 넣어 순환을 피할 것(안 넣으면
        이미 본 노드가 계속 돌아올 수 있음). 결과는 degree(전역 연결 수)
        내림차순 — 상위일수록 더 파볼 가치가 있는 허브. limit을 넘으면
        truncated=true, omitted에 잘린 개수가 실림(0으로 오인하지 말 것)."""
        conn = _conn()
        try:
            return neighbors_impl(conn, entity_ids, exclude_ids, limit)
        finally:
            conn.close()

    @mcp.tool()
    async def path(from_id: str, to_id: str, max_hops: int = 4) -> dict:
        """두 엔티티 사이의 최단 경로(무방향 BFS). 'A와 B가 왜 연결돼있나'에
        직접 답한다 — neighbors를 반복 호출해 스스로 경로를 찾을 필요 없음."""
        conn = _conn()
        try:
            return path_impl(conn, from_id, to_id, max_hops)
        finally:
            conn.close()

    @mcp.tool()
    async def context(entity_ids: list[str], compact: bool = True) -> dict:
        """선택한 엔티티(들)에 대해 알려진 것 전부(관찰+연결+출처요약)를
        결정론적으로(LLM 미사용) 조립해 반환 — 탐색 루프의 마지막 단계에서만
        부를 것(먼저 resolve_entity/neighbors/search로 관심 노드를 충분히
        좁힌 다음). 최대 10개까지만 처리하며 넘으면 잘라내고 truncated=true.
        compact=True(기본)면 관찰을 앞 3개로 줄이고 출처요약을 생략해
        가볍게, 정말 전체가 필요하면 compact=False."""
        conn = _conn()
        try:
            return context_impl(conn, entity_ids, compact)
        finally:
            conn.close()

    @mcp.tool()
    async def overview() -> dict:
        """지식베이스 전체의 자기서술적 요약 — 엔티티 타입 분포, 핵심 허브
        (연결 많은 순), 여러 출처에서 수렴된(신뢰도 높은) 엔티티, 문서
        소스타입 분포. 검색어를 뭘로 시작할지 모를 때 이 툴을 가장 먼저
        부를 것."""
        conn = _conn()
        try:
            return overview_impl(conn)
        finally:
            conn.close()

    @mcp.tool()
    async def node(entity_id: str, full: bool = False) -> dict:
        """엔티티 하나의 상세(모든 observations + 소스 문서 요약 + 타입 있는
        1홉 이웃). id를 이미 알고 있을 때 씀 — 이름만 있으면 resolve_entity
        먼저. 소스 문서는 최신 10개까지만(초과 시 documents_truncated=true,
        documents_omitted에 잘린 개수) — 문서 본문 전체가 아니라 요약만
        포함(허브 엔티티는 소스가 수십 개라 본문 전체를 다 넣으면 응답이
        터진다). 특정 문서의 본문 전문이 필요하면 document(document_id)를
        따로 호출할 것. full=True면 이 10개 문서에 한해 본문 전문도 포함(주의:
        허브 엔티티에서 쓰면 응답이 매우 커질 수 있음). fetched_at은
        ISO8601(UTC, 타임존 명시)."""
        conn = _conn()
        try:
            return node_impl(conn, entity_id, full=full)
        finally:
            conn.close()

    @mcp.tool()
    async def documents(
        limit: int = DEFAULT_DOCUMENTS_LIMIT,
        since: str | None = None,
        query: str | None = None,
    ) -> dict:
        """최신순 문서 목록(제목·요약·출처타입·안읽음/즐겨찾기 상태,
        fetched_at은 ISO8601 UTC). limit 최대 100(그 이상 요청해도 잘림).
        since(예: '2026-08-01' 또는 전체 ISO8601)로 그 시각 이후만, query로
        제목/URL 부분일치 검색 — 전체를 다 훑지 말고 좁혀서 찾을 것. limit을
        넘으면 truncated=true, omitted에 잘린 개수(0으로 오인 금지)."""
        conn = _conn()
        try:
            return documents_impl(conn, limit=limit, since=since, query=query)
        finally:
            conn.close()

    @mcp.tool()
    async def document(document_id: str) -> dict:
        """문서 하나의 상세(제목·요약·자세히읽기 전문·원문 URL·fetched_at은
        ISO8601 UTC). 사람용 웹 핸들러와 달리 안읽음(seen) 상태를 바꾸지
        않는다(읽기전용 원칙)."""
        conn = _conn()
        try:
            return document_impl(conn, document_id)
        finally:
            conn.close()

    @mcp.tool()
    async def stats() -> dict:
        """지식베이스 규모(문서/엔티티/관계/임베딩 개수 등)."""
        conn = _conn()
        try:
            return dbm.counts(conn)
        finally:
            conn.close()

    port = getattr(s, "inject_port", 8765)
    hosts = {"localhost", "127.0.0.1", f"localhost:{port}", f"127.0.0.1:{port}"}
    public_url = getattr(s, "public_url", "")
    if public_url:
        host = urlparse(public_url).hostname
        if host:
            hosts.add(host)

    return mcp.streamable_http_app(
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(allowed_hosts=sorted(hosts)),
    )
