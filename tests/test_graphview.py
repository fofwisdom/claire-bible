"""그래프 뷰어 데이터 변환 — vis.js nodes/edges + 고아 엣지 제외."""

from __future__ import annotations

import sqlite3

from claire.graphview import (
    graph_json, node_detail, documents_list, synthesis_context, synthesize, GRAPH_HTML,
)
from claire.extract.provider import MockProvider
from claire.ontology.base import Document, Entity, Relation
from claire.store import db as dbm


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    return conn


def test_graph_json_nodes_edges():
    conn = _db()
    dbm.upsert_entity(conn, Entity(id="e1", type="Tool", name="Claude Code",
                                   observations=["CLI coding agent"], sources=["d"]))
    dbm.upsert_entity(conn, Entity(id="e2", type="Org", name="Anthropic", sources=["d"]))
    dbm.upsert_relation(conn, Relation(id="r1", type="authored_by",
                                       source_id="e1", target_id="e2", sources=["d"]))
    g = graph_json(conn)
    assert g["stats"]["entities"] == 2 and g["stats"]["relations"] == 1
    ids = {n["id"] for n in g["nodes"]}
    assert ids == {"e1", "e2"}
    n1 = next(n for n in g["nodes"] if n["id"] == "e1")
    assert n1["label"] == "Claude Code" and n1["group"] == "Tool"
    assert n1["obs"].startswith("CLI coding agent")  # hover 팝업용 관찰 첫 줄(vis 기본 title 툴팁 대체)
    assert n1["degree"] == 1  # e1-e2 연결 1개
    assert n1["sources"] == ["d"]  # 문서 기반 필터용
    e = g["edges"][0]
    assert e["from"] == "e1" and e["to"] == "e2" and e["label"] == "authored_by"
    assert "id" in e  # 필터 토글용 엣지 id


def test_graph_json_degree_centrality():
    """degree(연결 수) 계산 — 필터/스케일의 기준. 허브일수록 높다."""
    conn = _db()
    for i in range(4):
        dbm.upsert_entity(conn, Entity(id=f"e{i}", type="Tool", name=f"N{i}", sources=["d"]))
    # e0 을 허브로: e0-e1, e0-e2, e0-e3
    for i in (1, 2, 3):
        dbm.upsert_relation(conn, Relation(id=f"r{i}", type="uses",
                                           source_id="e0", target_id=f"e{i}", sources=["d"]))
    g = graph_json(conn)
    deg = {n["id"]: n["degree"] for n in g["nodes"]}
    assert deg["e0"] == 3 and deg["e1"] == 1
    assert g["stats"]["max_degree"] == 3


def test_graph_json_excludes_dangling_edges():
    """양 끝 노드가 다 있는 관계만 — 고아 엣지는 vis.js 유령 노드를 만들어 깨진다."""
    conn = _db()
    dbm.upsert_entity(conn, Entity(id="e1", type="Tool", name="A", sources=["d"]))
    # target 이 존재하지 않는 관계
    dbm.upsert_relation(conn, Relation(id="r1", type="uses",
                                       source_id="e1", target_id="ghost", sources=["d"]))
    g = graph_json(conn)
    assert g["stats"]["entities"] == 1
    assert g["edges"] == [] and g["stats"]["relations"] == 0


def test_graph_html_self_contained_markers():
    # 페이지가 graph/node/documents 를 fetch 하고 vis.js 를 로드하는지(렌더 진입점 존재).
    assert "fetch('graph')" in GRAPH_HTML
    assert "fetch('documents')" in GRAPH_HTML
    assert "vis-network" in GRAPH_HTML
    assert "id=\"net\"" in GRAPH_HTML
    assert "loadNode" in GRAPH_HTML and "id=\"panel\"" in GRAPH_HTML   # 상세 패널
    assert "id=\"docs\"" in GRAPH_HTML and "selectDoc" in GRAPH_HTML    # 좌측 문서 패널
    assert "id=\"legendbar\"" in GRAPH_HTML and "TYPE_COLORS" in GRAPH_HTML  # 색 범례
    assert "hoverNode" in GRAPH_HTML and "blurNode" in GRAPH_HTML       # hover 미리보기
    assert ", 1000)" in GRAPH_HTML                                      # hover 1초
    # 인증은 claire_session 쿠키(/web 진입)로 자동 전송 — 페이지가 로드됐다는 것 자체가 인증됨.
    assert "synthesize" in GRAPH_HTML                                   # 종합 POST 경로
    assert "세션 만료" in GRAPH_HTML                                    # 만료 시 /web 재접속 안내
    assert "semanticSearch" in GRAPH_HTML and "id=\"sem\"" in GRAPH_HTML  # 의미검색 토글
    assert "id=\"searchbtn\"" in GRAPH_HTML                             # 의미검색 버튼
    assert "synthSet" in GRAPH_HTML and "addToSynth" in GRAPH_HTML      # 종합 수집(inspect와 분리)
    assert "id=\"authstate\"" in GRAPH_HTML and "setAuth" in GRAPH_HTML  # 인증 상태 표시
    assert "opacity" in GRAPH_HTML and "dday" in GRAPH_HTML             # dim + 일자 그룹
    # 읽기는 중앙 마크다운 팝업(nav 와 분리) — 좌측/패널 '읽기' 버튼이 openReader 호출
    assert "openReader" in GRAPH_HTML and "id=\"reader\"" in GRAPH_HTML  # 중앙 읽기 팝업
    assert "setReadFS" in GRAPH_HTML and "claireReadFS" in GRAPH_HTML and "rzoom" in GRAPH_HTML  # 글자 크기 조절(A-/A+)
    assert "renderMarkdown" in GRAPH_HTML and "marked" in GRAPH_HTML and "DOMPurify" in GRAPH_HTML  # 마크다운 렌더+살균
    assert "readbtn" in GRAPH_HTML and "stopPropagation" in GRAPH_HTML   # 읽기 버튼=nav 와 분리
    assert "<mark>" in GRAPH_HTML                                       # ==형광== 강조 렌더
    assert "data-theme" in GRAPH_HTML and "toggleTheme" in GRAPH_HTML and "claireTheme" in GRAPH_HTML  # 라이트 기본+다크 토글
    assert "relayout" in GRAPH_HTML and "orientationchange" in GRAPH_HTML  # 모바일 캔버스 리사이즈
    assert "pan-y" in GRAPH_HTML and "mobileScrollTo" in GRAPH_HTML     # 모바일 스크롤 트랩 해소(협조적 제스처)
    assert "#ffffff" in GRAPH_HTML and "borderWidthSelected" in GRAPH_HTML  # 선택 노드 흰 테두리
    assert "nodes:ids" in GRAPH_HTML                                    # 문서 선택 → 해당 노드들로 fit
    assert "doResearch" in GRAPH_HTML and "fetch('research'" in GRAPH_HTML  # 맥락 확장 조사
    assert "refreshGraph" in GRAPH_HTML                                 # 조사 후 무리로드 그래프 갱신
    assert "getReader" in GRAPH_HTML and "rprog" in GRAPH_HTML          # NDJSON 스트림 진행 표시
    assert "renderResearchResult" in GRAPH_HTML                         # 진행/결과 렌더 분리
    assert "borderWidth: lit?3:1" in GRAPH_HTML                         # 강조(문서/검색) 흰 테두리
    assert "auth/request" not in GRAPH_HTML                             # 레거시 nonce 트리거 제거(이슈3)


def test_documents_list_newest_first_with_summary(_unused=None):
    conn = _db()
    dbm.insert_document(conn, Document(id="d1", url="https://x/1", title="첫 문서",
                                       raw_text=".", source_type="web", content_hash="h1"))
    dbm.insert_document(conn, Document(id="d2", url="https://x/2", title="둘째 문서",
                                       raw_text=".", source_type="youtube", content_hash="h2"))
    # fetched_at 으로 최신순 정렬되게 d2 를 더 나중으로
    conn.execute("UPDATE documents SET fetched_at=100 WHERE id='d1'")
    conn.execute("UPDATE documents SET fetched_at=200 WHERE id='d2'")
    conn.commit()
    dbm.log_extraction(conn, document_id="d1", provider="mock", model="m",
                       prompt_version="v", raw_response='{"summary":"첫 요약"}')

    docs = documents_list(conn)
    assert [d["id"] for d in docs] == ["d2", "d1"]   # 최신순
    assert docs[1]["title"] == "첫 문서" and docs[1]["summary"] == "첫 요약"
    assert docs[0]["source_type"] == "youtube"


def test_node_detail_assembles_knowledge():
    """노드 상세 = 전체 observations + 소스 문서(제목·요약) + 타입 있는 이웃."""
    conn = _db()
    # 문서 + 추출(summary 보관)
    dbm.insert_document(conn, Document(id="d1", url="https://x/1", title="MCP 소개",
                                       raw_text="...", source_type="web", content_hash="h"))
    dbm.log_extraction(conn, document_id="d1", provider="mock", model="m",
                       prompt_version="v", raw_response='{"summary":"MCP는 표준 프로토콜"}')
    dbm.upsert_entity(conn, Entity(id="e1", type="Concept", name="MCP",
                                   aliases=["Model Context Protocol"],
                                   observations=["LLM 컨텍스트 표준"], sources=["d1"]))
    dbm.upsert_entity(conn, Entity(id="e2", type="Tool", name="Claude Code", sources=["d1"]))
    dbm.upsert_relation(conn, Relation(id="r1", type="uses",
                                       source_id="e2", target_id="e1", sources=["d1"]))

    d = node_detail(conn, "e1")
    assert d["name"] == "MCP" and d["type"] == "Concept"
    assert d["aliases"] == ["Model Context Protocol"]
    assert d["observations"] == ["LLM 컨텍스트 표준"]
    # 소스 문서: 제목 + extractions 에서 꺼낸 summary
    assert len(d["documents"]) == 1
    assert d["documents"][0]["title"] == "MCP 소개"
    assert d["documents"][0]["summary"] == "MCP는 표준 프로토콜"
    # 이웃: Claude Code 가 e1 을 uses (e1 입장에선 incoming)
    assert len(d["neighbors"]) == 1
    n = d["neighbors"][0]
    assert n["name"] == "Claude Code" and n["rel"] == "uses" and n["dir"] == "in"


def test_node_detail_missing_returns_none():
    assert node_detail(_db(), "nope") is None


def test_node_detail_includes_document_detail():
    """설명(summary)과 별개로 '자세히 읽기'용 detail(여러 단락)이 문서에 실린다(이슈2).

    detail 은 구조화 추출과 독립된 별도 컬럼/LLM 호출 → 백필 시 그래프(엔티티) 불변."""
    from claire.ingest.pipeline import ensure_document_detail

    conn = _db()
    doc = Document(id="d1", url="https://x/1", title="MCP 소개",
                   raw_text="원문 본문 " * 50, source_type="web", content_hash="h")
    dbm.insert_document(conn, doc)
    dbm.upsert_entity(conn, Entity(id="e1", type="Concept", name="MCP", sources=["d1"]))

    assert node_detail(conn, "e1")["documents"][0]["detail"] == ""   # 백필 전엔 빈값
    assert ensure_document_detail(conn, MockProvider(), doc, force=True) is True
    d = node_detail(conn, "e1")
    assert d["documents"][0]["detail"].startswith("[mock-detail]")
    assert dbm.counts(conn)["entities"] == 1            # 그래프는 그대로(detail 만 채움)


def test_documents_missing_detail_skips_filled():
    """백필 대상은 detail 빈 문서만 — 이미 채운 건 재호출 안 함(quota 절약)."""
    conn = _db()
    dbm.insert_document(conn, Document(id="d1", url="u1", title="A", raw_text="x",
                                       source_type="web", content_hash="h1"))
    dbm.insert_document(conn, Document(id="d2", url="u2", title="B", raw_text="y",
                                       source_type="web", content_hash="h2"))
    assert set(dbm.documents_missing_detail(conn)) == {"d1", "d2"}
    dbm.set_document_detail(conn, "d1", "이미 있음")
    assert dbm.documents_missing_detail(conn) == ["d2"]
    assert dbm.get_document_detail(conn, "d1") == "이미 있음"


def _seed_two(conn):
    dbm.insert_document(conn, Document(id="d1", url="https://x/1", title="MCP 문서",
                                       raw_text=".", source_type="web", content_hash="h"))
    dbm.log_extraction(conn, document_id="d1", provider="mock", model="m",
                       prompt_version="v", raw_response='{"summary":"MCP는 컨텍스트 표준"}')
    dbm.upsert_entity(conn, Entity(id="e1", type="Concept", name="MCP",
                                   aliases=["Model Context Protocol"],
                                   observations=["LLM 컨텍스트 표준"], sources=["d1"]))
    dbm.upsert_entity(conn, Entity(id="e2", type="Tool", name="Claude Code",
                                   observations=["CLI agent"], sources=["d1"]))
    dbm.upsert_relation(conn, Relation(id="r1", type="uses",
                                       source_id="e2", target_id="e1", sources=["d1"]))


def test_synthesis_context_assembles_knowledge():
    """종합 컨텍스트(결정론적): 선택 노드의 관찰·연결·출처요약을 모은다."""
    conn = _db()
    _seed_two(conn)
    ctx, names = synthesis_context(conn, ["e1", "e2"])
    assert names == ["MCP", "Claude Code"]
    assert "LLM 컨텍스트 표준" in ctx          # 관찰
    assert "Model Context Protocol" in ctx     # 별칭
    assert "uses" in ctx                        # 연결
    assert "MCP는 컨텍스트 표준" in ctx         # 출처요약
    # 존재하지 않는 id 는 무시
    ctx2, names2 = synthesis_context(conn, ["e1", "ghost"])
    assert names2 == ["MCP"]


def test_synthesize_routes_context_through_provider():
    """종합 경로 연결: context 가 provider.summarize_search 로 흘러가 답이 나온다(mock)."""
    conn = _db()
    _seed_two(conn)
    out = synthesize(conn, MockProvider(), ["e1", "e2"])
    assert "error" not in out
    assert out["entities"] == ["MCP", "Claude Code"]
    # mock 은 query::context 를 반환 → 컨텍스트(관찰)가 답에 포함됨
    assert "LLM 컨텍스트 표준" in out["answer"]


def test_synthesize_empty_selection():
    assert "error" in synthesize(_db(), MockProvider(), [])
    assert "error" in synthesize(_db(), MockProvider(), ["ghost"])
