"""순수 쿼리 및 데이터 접근 계층 — DB 질의 및 API/MCP/뷰용 데이터 직렬화.

UI/템플릿 렌더링에 독립적이며, 사이드 이펙트 없이 읽기 전용 데이터를 가공해 반환한다.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from typing import Any

from . import db as dbm


def graph_json(conn: sqlite3.Connection, include_hidden: bool = True) -> dict:
    """엔티티/관계를 vis.js network 형식(nodes/edges)으로. dangling edge 는 제외.

    각 노드에 degree(연결 수)를 실어 UI 가 degree-centrality 임계로 핵심 서브그래프만
    표시할 수 있게 한다(전체 N개 렌더 → 큰 그래프의 가시성/스케일 문제 해소).
    include_hidden=False 면 숨김 문서 전용 엔티티 및 엣지를 제외한다.
    """
    ents = dbm.all_entities(conn)
    rels = dbm.all_relations(conn)
    hidden_doc_ids = set() if include_hidden else dbm.hidden_document_ids(conn)

    visible_nodes = []
    visible_ent_ids = set()
    for e in ents:
        sources = e.sources or []
        if not include_hidden and hidden_doc_ids:
            if sources:
                valid_sources = [s for s in sources if s not in hidden_doc_ids]
                if not valid_sources:
                    # 숨김 문서에서만 나온 엔티티는 제외
                    continue
                node_sources = valid_sources
            else:
                node_sources = sources
        else:
            node_sources = sources

        visible_ent_ids.add(e.id)
        visible_nodes.append({
            "id": e.id,
            "label": e.name,
            "group": e.type,
            "sources": node_sources,  # 문서 기반 필터용(문서 클릭 → 그 문서 엔티티만)
            # 관찰 첫 줄 — hover 시 마우스 위치 커스텀 팝업이 쓴다.
            "obs": (e.observations[0][:200] if e.observations else ""),
        })

    # 양 끝 노드가 모두 존재하는 관계만(고아 엣지는 vis.js 가 유령 노드를 만들어 깨짐).
    edges = [
        {
            "id": f"e{i}",
            "from": r.source_id,
            "to": r.target_id,
            "label": r.type,
            "arrows": "to",
            "dashes": r.provisional,
        }
        for i, r in enumerate(
            r for r in rels if r.source_id in visible_ent_ids and r.target_id in visible_ent_ids
        )
    ]
    deg: Counter = Counter()
    for e in edges:
        deg[e["from"]] += 1
        deg[e["to"]] += 1

    for n in visible_nodes:
        n["degree"] = deg.get(n["id"], 0)

    max_degree = max((n["degree"] for n in visible_nodes), default=0)
    return {
        "nodes": visible_nodes,
        "edges": edges,
        "stats": {
            "entities": len(visible_nodes),
            "relations": len(edges),
            "max_degree": max_degree,
        },
    }


def node_detail(
    conn: sqlite3.Connection,
    entity_id: str,
    include_hidden: bool = True,
) -> dict | None:
    """한 노드의 '쓸 수 있는 지식': 전체 observations + 소스 문서(제목·요약·URL) +
    타입 있는 이웃. 패널에 그대로 펼친다. 없으면 None.
    """
    ent = dbm.get_entity(conn, entity_id)
    if ent is None:
        return None

    hidden_doc_ids = set() if include_hidden else dbm.hidden_document_ids(conn)
    if not include_hidden and hidden_doc_ids and ent.sources:
        valid_sources = [s for s in ent.sources if s not in hidden_doc_ids]
        if not valid_sources:
            return None

    neighbors = []
    for r in dbm.neighbors(conn, entity_id):
        out = r.source_id == entity_id
        other = dbm.get_entity(conn, r.target_id if out else r.source_id)
        if other:
            if not include_hidden and hidden_doc_ids and other.sources:
                if not any(s not in hidden_doc_ids for s in other.sources):
                    continue
            neighbors.append({
                "id": other.id,
                "name": other.name,
                "type": other.type,
                "rel": r.type,
                "dir": "out" if out else "in",
                "provisional": r.provisional,
            })

    documents = []
    for did in ent.sources:
        if not include_hidden and did in hidden_doc_ids:
            continue
        row = dbm.get_document_row(conn, did)
        if row:
            documents.append({
                "id": did,
                "title": row["title"] or "(제목 없음)",
                "url": row["url"],
                "summary": dbm.latest_extraction_summary(conn, did) or "",
                # 가독 렌더(여러 단락) — 패널에서 '상세'로 펼친다.
                "detail": dbm.get_document_detail(conn, did) or "",
                "detail_format": dbm.get_document_detail_format(conn, did),
                "detail_html": dbm.get_document_detail_html(conn, did) or "",
                # 원시 epoch(초) — MCP 등 API 소비자용.
                "fetched_at": row["fetched_at"],
            })

    return {
        "id": ent.id,
        "name": ent.name,
        "type": ent.type,
        "aliases": ent.aliases,
        "observations": ent.observations,
        "provisional": ent.provisional,
        "neighbors": neighbors,
        "documents": documents,
    }


def document_detail(
    conn: sqlite3.Connection,
    document_id: str,
    include_hidden: bool = True,
) -> dict | None:
    """한 문서(article)의 우측 패널용 상세 — 제목·출처·요약·상세(detail). 없으면 None.

    좌측 문서를 고르면 그래프 강조에 더해 우측에 이 요약/상세를 펼친다(노드 클릭 없이
    문서 자체를 읽게). 노드 목록은 클라이언트가 graph 의 node.sources 로 계산하므로
    여기선 싣지 않는다(중복 전송 방지).
    """
    row = dbm.get_document_row(conn, document_id)
    if row is None:
        return None
    if not include_hidden and bool(row["hidden"]):
        return None
    ents = dbm.document_entities(conn, document_id)
    nodes = [
        {
            "id": e.id,
            "label": e.name,
            "group": e.type,
            "observations": e.observations or [],
        }
        for e in ents
    ]
    raw_meta = None
    try:
        raw_meta = row["meta"]
    except (IndexError, KeyError):
        raw_meta = None
    meta_dict: dict = {}
    if raw_meta:
        try:
            meta_dict = json.loads(raw_meta)
        except Exception:
            meta_dict = {}

    raw_text = None
    try:
        raw_text = row["raw_text"]
    except (IndexError, KeyError):
        raw_text = None

    is_stt = bool(
        meta_dict.get("is_stt", False)
        or meta_dict.get("stt_applied", False)
        or meta_dict.get("stt", False)
        or (
            isinstance(meta_dict.get("transcript_segments"), list)
            and len(meta_dict["transcript_segments"]) > 0
        )
        or (raw_text and ("[영상 음성 전사 (STT)]" in raw_text or "[음성 전사 (STT)]" in raw_text))
    )

    stt_data = dbm.extract_stt_transcript(raw_text, meta_dict) if is_stt else None
    stt_transcript = stt_data["text"] if stt_data else ""
    stt_segments = stt_data["segments"] if stt_data else []
    stt_truncated = bool(stt_data["stt_truncated"]) if stt_data else False

    return {
        "id": document_id,
        "title": row["title"] or "(제목 없음)",
        "url": row["url"],
        "source_type": row["source_type"],
        "summary": dbm.latest_extraction_summary(conn, document_id) or "",
        "detail": dbm.get_document_detail(conn, document_id) or "",
        "detail_format": dbm.get_document_detail_format(conn, document_id),
        "detail_html": dbm.get_document_detail_html(conn, document_id) or "",
        "hidden": bool(row["hidden"]),
        # [1홉 병합, ONEHOP_MERGE_DESIGN.md] 이 문서에 흡수된 부가 출처(예: GeekNews 글에
        # 병합된 그 프로젝트의 github). 원문 링크 계보를 UI 에서 추적 가능하게.
        "extra_sources": dbm.get_document_extra_sources(conn, document_id),
        # 원시 epoch(초) — MCP 등 API 소비자용(웹 UI는 이 필드 안 씀).
        "fetched_at": row["fetched_at"],
        "nodes": nodes,
        "raw_truncated": bool(meta_dict.get("raw_truncated", False)),
        "appendix_truncated": bool(meta_dict.get("appendix_truncated", False)),
        "references_truncated": bool(meta_dict.get("references_truncated", False)),
        "pdf_parser_requested": meta_dict.get("pdf_parser_requested"),
        "pdf_parser_used": meta_dict.get("pdf_parser_used"),
        "pdf_parser_fallback": bool(meta_dict.get("pdf_parser_fallback", False)),
        "pdf_parser_fallback_reason": meta_dict.get("pdf_parser_fallback_reason"),
        "presentation_pdf": meta_dict.get("presentation_pdf") or {},
        "presentation_pdfs": meta_dict.get("presentation_pdfs") or [],
        "author": (
            row["author"]
            or meta_dict.get("author")
            or (
                (meta_dict.get("biblio") or {}).get("author")
                if isinstance(meta_dict.get("biblio"), dict)
                else None
            )
        ),
        "published_at": (
            row["published_at"]
            or meta_dict.get("published_at")
            or (
                (meta_dict.get("biblio") or {}).get("published_at")
                if isinstance(meta_dict.get("biblio"), dict)
                else None
            )
        ),
        "biblio": meta_dict.get("biblio") or {},
        "orig_chars": meta_dict.get("orig_chars"),
        "raw_chars": meta_dict.get("raw_chars"),
        "directive": meta_dict.get("directive"),
        "is_stt": is_stt,
        "stt_transcript": stt_transcript,
        "transcript_segments": stt_segments,
        "stt_truncated": stt_truncated,
        "stt_orig_chars": meta_dict.get("stt_orig_chars") or meta_dict.get("orig_chars"),
        "stt_raw_chars": meta_dict.get("stt_raw_chars") or meta_dict.get("raw_chars"),
        "meta": meta_dict,
    }


def dedup_clusters(conn: sqlite3.Connection, scan: dict) -> dict:
    """dedup_scan 결과를 웹 UI 용으로 보강 — 각 문서의 제목·URL·본문길이·적재시각 + keeper 추천.

    scan(=svc.dedup_scan)은 ids/urls/titles/score 만 준다. UI 가 '무엇을 유지할지' 고르게
    각 문서 메타를 채우고, 기본 keeper(=최장 본문, 동률이면 최초 적재)를 표시한다 —
    service.dedup_merge 의 keeper 선정과 동일 규칙(웹/CLI 일관).
    """
    out_clusters = []
    for c in scan.get("clusters", []):
        docs = []
        for did in c["ids"]:
            row = dbm.get_document_row(conn, did)
            if row is None:
                continue
            docs.append({
                "id": did,
                "title": row["title"] or "(제목 없음)",
                "url": row["url"],
                "len": len(row["raw_text"] or ""),
                "fetched_at": row["fetched_at"],
            })
        if len(docs) < 2:
            continue
        # keeper = 최장 본문(가장 완전), 동률이면 최초 적재(fetched_at 작은 쪽).
        keeper = max(docs, key=lambda d: (d["len"], -(d["fetched_at"] or 0.0)))["id"]
        out_clusters.append({"score": c.get("score"), "keeper": keeper, "docs": docs})
    return {"documents": scan.get("documents", 0), "clusters": out_clusters}


def documents_list(
    conn: sqlite3.Connection,
    limit: int = 100,
    since: float | None = 0,
    include_hidden: bool = False,
    query: str = "",
    **kwargs: Any,
) -> list[dict]:
    """좌측 문서 패널용 — 최신순 문서(제목·요약·출처타입·시각)."""
    if "since" in kwargs:
        since = kwargs["since"]
    if "include_hidden" in kwargs:
        include_hidden = kwargs["include_hidden"]
    if "query" in kwargs:
        query = kwargs["query"]
    if "limit" in kwargs:
        limit = kwargs["limit"]

    since_filter = None if (since is None or since == 0) else since
    query_filter = query if query else None

    out = []
    for r in dbm.documents_timeline(
        conn,
        limit,
        since=since_filter,
        query=query_filter,
        include_hidden=include_hidden,
    ):
        out.append({
            "id": r["id"],
            "title": r["title"] or "(제목 없음)",
            "url": r["url"],
            "source_type": r["source_type"],
            "fetched_at": r["fetched_at"],
            "seen": r["seen"],                  # 0=미열람(unread) → UI 아이콘
            "watch": r["watch_enabled"],        # 1=주기 크롤링 대상 → UI 아이콘
            "pinned": r["pinned"],              # 1=즐겨찾기 → 목록 상단 고정 섹션
            "hidden": r["hidden"],               # 1=숨김 → 기본 목록에서 제외
            "summary": dbm.latest_extraction_summary(conn, r["id"]) or "",
        })
    return out


def synthesis_context(
    conn: sqlite3.Connection,
    entity_ids: list[str],
    compact: bool = False,
    include_hidden: bool = True,
) -> tuple[str, list[str]]:
    """선택 노드들의 지식(관찰·연결·출처요약)을 LLM 종합용 컨텍스트 텍스트로 조립.

    결정론적(LLM 없음) — 이 텍스트가 summarize_search 의 근거가 된다. (context, names).
    compact=True (MCP 용): 관찰은 앞 3개로 자르고 출처요약은 생략해 에이전트의
    컨텍스트 윈도우를 아낀다(docs/origin/design/MCP_SUPPORT.md 참고).
    """
    blocks: list[str] = []
    names: list[str] = []
    hidden_doc_ids = set() if include_hidden else dbm.hidden_document_ids(conn)
    for eid in entity_ids:
        ent = dbm.get_entity(conn, eid)
        if ent is None:
            continue
        if not include_hidden and hidden_doc_ids and ent.sources:
            if not any(s not in hidden_doc_ids for s in ent.sources):
                continue
        names.append(ent.name)
        parts = [f"## {ent.name} ({ent.type})"]
        if ent.aliases:
            parts.append("별칭: " + ", ".join(ent.aliases))
        if ent.observations:
            obs = ent.observations[:3] if compact else ent.observations
            parts.append("관찰: " + " ".join(obs))
        rels = []
        for r in dbm.neighbors(conn, eid):
            out = r.source_id == eid
            other = dbm.get_entity(conn, r.target_id if out else r.source_id)
            if other:
                if not include_hidden and hidden_doc_ids and other.sources:
                    if not any(s not in hidden_doc_ids for s in other.sources):
                        continue
                rels.append(f"{r.type} {'→' if out else '←'} {other.name}")
        if rels:
            parts.append("연결: " + ", ".join(rels[:12]))
        if not compact:
            for did in ent.sources:
                if not include_hidden and did in hidden_doc_ids:
                    continue
                summ = dbm.latest_extraction_summary(conn, did)
                if summ:
                    parts.append(f"출처요약: {summ}")
        blocks.append("\n".join(parts))
    return "\n\n".join(blocks), names


def synthesize(
    conn: sqlite3.Connection,
    provider: Any,
    entity_ids: list[str],
    query: str | None = None,
) -> dict:
    """선택 노드들을 아우르는 종합 지식 문서(인용 포함, 한국어)를 생성.

    summarize_search 재사용(검색 정리와 동일 경로) — 컨텍스트는 그래프(관찰·연결·출처요약).
    비용(LLM 호출)이 있으므로 호출측(API)에서 토큰 인증 + 명시적 액션으로만 부른다.
    """
    context, names = synthesis_context(conn, entity_ids)
    if not context:
        return {"error": "유효한 노드가 없습니다"}
    if not hasattr(provider, "summarize_search"):
        return {"error": "이 provider 는 종합을 지원하지 않습니다"}
    q = query or f"선택한 항목들({', '.join(names)})을 아우르는 핵심 지식을 정리해줘."
    answer = provider.summarize_search(q, context)
    return {"answer": answer, "entities": names, "query": q}
