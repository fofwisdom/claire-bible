"""그래프 뷰어 데이터 변환 — vis.js nodes/edges + 고아 엣지 제외."""

from __future__ import annotations

import re
import sqlite3

from claire.extract.provider import MockProvider
from claire.graphview import (
    GRAPH_HTML,
    documents_list,
    graph_json,
    node_detail,
    render_graph_html,
    shared_html,
    synthesis_context,
    synthesize,
)
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
    assert ", 1500)" in GRAPH_HTML                                      # hover 1.5초
    # 첫 페인트는 fail-closed이고 /whoami가 owner를 확인한 뒤에만 쓰기 UI를 승격한다.
    assert "synthesize" in GRAPH_HTML                                   # 종합 POST 경로
    assert "세션 만료" in GRAPH_HTML                                    # 만료 시 /web 재접속 안내
    assert "semanticSearch" in GRAPH_HTML and "id=\"sem\"" in GRAPH_HTML  # 의미검색 토글
    assert "id=\"advsearchbtn\"" in GRAPH_HTML                          # 고급검색 버튼
    assert "synthSet" in GRAPH_HTML and "addToSynth" in GRAPH_HTML      # 종합 수집(inspect와 분리)
    assert "id=\"authstate\"" in GRAPH_HTML and "setAccessScope" in GRAPH_HTML
    assert '<body class="ro" data-auth-scope="unknown" data-active-pane="docs">' in GRAPH_HTML
    assert "let AUTH_SCOPE='unknown';" in GRAPH_HTML
    assert "let READONLY=true;" in GRAPH_HTML
    assert "function canWrite(){ return AUTH_SCOPE==='owner'; }" in GRAPH_HTML
    assert "setAccessScope(d.scope)" in GRAPH_HTML
    assert "semchk.disabled = unknown || isAnon" in GRAPH_HTML
    assert "👁️ 익명 읽기전용" in GRAPH_HTML
    assert "let READONLY=false" not in GRAPH_HTML
    assert "setAuth('authed')" not in GRAPH_HTML
    for guarded_write in (
        "async function markDocumentSeen(docId){\n  if(!canWrite()) return;",
        "async function shareDoc(){\n  if(!canWrite() || !curReaderDoc) return;",
        "async function doResearch(){\n  if(!canWrite()) return;",
        "function openIngest(){\n  if(!canWrite()) return;",
        "async function runIngest(){\n  if(!canWrite()) return;",
        "async function openDedup(){\n  if(!canWrite()) return;",
        "function renderDedup(d){\n  if(!canWrite()) return;",
        "async function runDedupMerge(ci){\n  if(!canWrite()) return;",
        "function toggleShowHidden(){ if(!canWrite()) return;",
        "async function togglePin(id, val){\n  if(!canWrite()) return false;",
        "async function toggleHide(id, val){\n  if(!canWrite()) return false;",
        "async function panelToggleHide(id, val){\n  if(!canWrite()) return;",
        "function toggleSynth(id){ if(!canWrite()) return;",
        "function addToSynth(id){ if(!canWrite()) return;",
        "async function synth(){\n  if(!canWrite()) return;",
    ):
        assert guarded_write in GRAPH_HTML
    assert (
        "if(!canWrite()){\n"
        "    synthSet.clear();\n"
        "    showHidden=false;\n"
        "    renderChips();\n"
        "    // owner 전용 동적 UI를 네트워크 재조회보다 먼저 제거한다."
    ) in GRAPH_HTML
    assert GRAPH_HTML.count("r.status===401||r.status===404") >= 10
    assert GRAPH_HTML.index("if(r.status===429)") < GRAPH_HTML.index(
        "let d={}; try{ d=await r.json(); }catch(_){}",
        GRAPH_HTML.index("async function semanticSearch"),
    )
    assert "opacity" in GRAPH_HTML and "dday" in GRAPH_HTML             # dim + 일자 그룹
    # 읽기는 중앙 마크다운 팝업(nav 와 분리) — 노드 패널의 출처 문서 '본문 보기' 버튼이 openReader 호출
    assert "openReader" in GRAPH_HTML and "id=\"reader\"" in GRAPH_HTML  # 중앙 읽기 뷰
    assert "📖 본문 보기" in GRAPH_HTML
    assert "📖 크게 읽기" not in GRAPH_HTML  # 중복되거나 모호한 '크게 읽기' 라벨 제거
    assert "setReadFS" in GRAPH_HTML and "claireReadFS" in GRAPH_HTML and "rzoom" in GRAPH_HTML  # 글자 크기 조절(A-/A+)
    assert "renderMarkdown" in GRAPH_HTML and "marked" in GRAPH_HTML and "DOMPurify" in GRAPH_HTML  # 마크다운 렌더+살균
    assert "readbtn" in GRAPH_HTML and "stopPropagation" in GRAPH_HTML   # 읽기 버튼=nav 와 분리
    assert "<mark>" in GRAPH_HTML                                       # ==형광== 강조 렌더
    assert "data-theme" in GRAPH_HTML and "toggleTheme" in GRAPH_HTML and "claireTheme" in GRAPH_HTML  # 라이트 기본+다크 토글
    assert "relayout" in GRAPH_HTML and "orientationchange" in GRAPH_HTML  # 모바일 캔버스 리사이즈
    # 모바일 primary nav는 자료/그래프뿐이며 상세는 선택에 종속된 context sheet다.
    assert 'id="worktabs" role="tablist"' in GRAPH_HTML
    assert 'id="tab-docs" role="tab"' in GRAPH_HTML
    assert 'id="tab-graph"' not in GRAPH_HTML
    assert GRAPH_HTML.count('role="tabpanel"') == 2
    assert 'id="tab-detail"' not in GRAPH_HTML
    assert 'id="detailpane" role="region" aria-label="문맥 상세"' in GRAPH_HTML
    assert "function revealWorkspace" in GRAPH_HTML and "data-active-pane" in GRAPH_HTML
    assert "function openDetailPane()" in GRAPH_HTML and "let activePane='docs', detailOpen=false" in GRAPH_HTML
    assert "const paneNames=['docs','graph'];" in GRAPH_HTML
    assert "mobileScrollTo" not in GRAPH_HTML and "scrollIntoView" not in GRAPH_HTML
    assert "const mobileMQ = window.matchMedia('(max-width:720px)')" in GRAPH_HTML
    assert "const compactMQ = window.matchMedia('(max-width:1100px)')" in GRAPH_HTML
    assert "fitGraphContext" in GRAPH_HTML and "resetGraphCamera" in GRAPH_HTML
    # 모바일 그래프의 지역 자료 전환기: 자료 탭으로 왕복하지 않고 검색/이전/다음으로
    # activeDoc을 바꾸되, 자료·그래프의 주 탭 계층과 reader 역할은 그대로 유지한다.
    assert 'id="graphdocnav" aria-label="그래프 자료 전환"' in GRAPH_HTML
    assert 'id="graphdocpick" aria-haspopup="dialog" aria-expanded="false"' in GRAPH_HTML
    assert 'id="graphdocmenu" role="dialog" aria-label="그래프에서 볼 자료 선택"' in GRAPH_HTML
    assert 'aria-hidden="true" inert hidden' in GRAPH_HTML
    assert "function renderGraphDocPicker" in GRAPH_HTML
    assert "function stepGraphDoc" in GRAPH_HTML and "function setActiveDoc" in GRAPH_HTML
    assert "if(current<0 || docs.length<2) return;" in GRAPH_HTML
    assert "font:{color:th.nodeFont,size:0" in GRAPH_HTML             # 관계 라벨은 기본 숨김
    assert "edgeLabelsByZoom" in GRAPH_HTML                           # 확대/선택/경로에서만 라벨 공개
    # reader는 실제 modal 의미·focus trap/복원·배경 inert를 갖는다.
    assert 'role="dialog" aria-modal="true" aria-labelledby="rtitle" aria-hidden="true"' in GRAPH_HTML
    assert "handleReaderKey" in GRAPH_HTML and "setReaderBackgroundInert" in GRAPH_HTML
    assert "readerReturnFocus" in GRAPH_HTML and "data-read-doc" in GRAPH_HTML
    assert "#ffffff" in GRAPH_HTML and "borderWidthSelected" in GRAPH_HTML  # 선택 노드 흰 테두리
    assert "nodes:ids" in GRAPH_HTML                                    # 문서 선택 → 해당 노드들로 fit
    assert "doResearch" in GRAPH_HTML and "fetch('research'" in GRAPH_HTML  # 맥락 확장 조사
    assert "refreshGraph" in GRAPH_HTML                                 # 조사 후 무리로드 그래프 갱신
    assert "getReader" in GRAPH_HTML and "rprog" in GRAPH_HTML          # NDJSON 스트림 진행 표시
    assert GRAPH_HTML.count("if(!r.ok)") >= 2                          # 503 등 비-NDJSON 오류 처리
    assert "renderResearchResult" in GRAPH_HTML                         # 진행/결과 렌더 분리
    assert "borderWidth: lit?3:1" in GRAPH_HTML                         # 강조(문서/검색) 흰 테두리
    assert "d.checkpoint" in GRAPH_HTML and "내부 체크포인트" in GRAPH_HTML
    # 터치스크린 및 상세/읽기 뷰 오픈 시 롤오버 팝업 화면 가림 방지
    assert "body.detail-open #nodepop" in GRAPH_HTML and "body.reader-open #nodepop" in GRAPH_HTML
    assert "@media (hover:none)" in GRAPH_HTML
    assert "function canShowNodePop()" in GRAPH_HTML and "isTouchActive()" in GRAPH_HTML
    assert "clearTimeout(hoverTimer); hoverTimer=null;" in GRAPH_HTML
    assert "window.addEventListener('touchstart'" in GRAPH_HTML
    assert "window.addEventListener('pointerdown'" in GRAPH_HTML
    # aside#detailpane(우측 메뉴) 활성화 시 아이콘 모드 전환 및 컴팩트 레일
    assert "--detail-compact-width" in GRAPH_HTML and "detail-compact" in GRAPH_HTML
    assert "id=\"detailtogglebtn\"" in GRAPH_HTML and "toggleDetailCompact" in GRAPH_HTML
    assert "compact-rail" in GRAPH_HTML and "btn-label" in GRAPH_HTML


def test_right_menu_compact_icon_mode_markers():
    """aside#detailpane(우측 메뉴) 활성화 시 메뉴 아이콘 모드 지원 검증."""
    assert "--detail-width:360px; --detail-compact-width:56px;" in GRAPH_HTML
    assert "body.detail-compact #detailpane" in GRAPH_HTML
    assert "#detailpane.compact-rail" in GRAPH_HTML
    assert "body.detail-compact #detailpane #moremenu .btn-label" in GRAPH_HTML
    assert "toggleDetailCompact" in GRAPH_HTML
    assert "claireDetailCompact" in GRAPH_HTML


def test_right_menu_graph_section_markers():
    """우측 메뉴 내 '그래프' 전용 섹션 분리, 그래프 전환 단추 및 섹션 상단 1px 구분선 스타일 검증."""
    assert 'id="graph-section"' in GRAPH_HTML
    assert 'class="menu-section-title">그래프<' in GRAPH_HTML
    assert "#moremenu .menu-section" in GRAPH_HTML
    assert "border-top:1px solid var(--border)" in GRAPH_HTML
    assert "#moremenu .menu-section-title" in GRAPH_HTML
    assert "#moremenu .menu-section-head" in GRAPH_HTML
    assert 'id="opengraphbtn"' in GRAPH_HTML
    assert "openDocGraph(activeDoc||curReaderDoc)" in GRAPH_HTML
    assert "openDocGraph(docId)" in GRAPH_HTML


def test_favorite_and_hide_ui_markers():
    """좌측 패널 제목 좌측 즐겨찾기 버튼 및 우측 상세 패널 숨기기 체크박스 검증."""
    # 좌측 패널: 제목 줄 좌측 별표 버튼
    assert "doctitle-line" in GRAPH_HTML
    assert "docpin-btn" in GRAPH_HTML
    assert "⭐ 즐겨찾기 (" in GRAPH_HTML
    # 우측 패널: FTS 스타일 숨기기 체크박스
    assert "dochide-row" in GRAPH_HTML and "dochide-label" in GRAPH_HTML
    assert 'id="panelhidechk"' in GRAPH_HTML
    assert 'id="panelhidelabel"' in GRAPH_HTML
    assert "panelToggleHide" in GRAPH_HTML



def test_browser_dependencies_are_pinned_with_sri_and_markdown_fails_closed():
    expected = (
        (
            "https://unpkg.com/vis-network@9.1.11/standalone/umd/"
            "vis-network.min.js",
            "sha384-60H6/hL99pRYjWacRdebxM1T2R6jvWyd9GVAb7d4fp9BSfv4f0i5sWjkprnnG0cz",
        ),
        (
            "https://unpkg.com/marked@4.3.0/marked.min.js",
            "sha384-QsSpx6a0USazT7nK7w8qXDgpSAPhFsb2XtpoLFQ5+X2yFN6hvCKnwEzN8M5FWaJb",
        ),
        (
            "https://unpkg.com/dompurify@3.1.6/dist/purify.min.js",
            "sha384-+VfUPEb0PdtChMwmBcBmykRMDd+v6D/oFmB3rZM/puCMDYcIvF968OimRh4KQY9a",
        ),
    )
    shared = shared_html({"title": "x"})
    for url, integrity in expected:
        assert url in GRAPH_HTML
        assert integrity in GRAPH_HTML
        if "vis-network" not in url:
            assert url in shared
            assert integrity in shared
    assert 'crossorigin="anonymous"' in GRAPH_HTML
    assert "https://unpkg.com/vis-network/standalone/" not in GRAPH_HTML
    for page in (GRAPH_HTML, shared):
        assert "if(!parser||!purifier" in page
        assert "const fallback=()=>esc(raw).replace" in page
        assert "return window.DOMPurify?" not in page


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
    assert d["documents"][0]["detail"].startswith("[mock-detail-adoc]")
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


def test_render_graph_html_default():
    html = render_graph_html()
    assert "https://github.com/fofwisdom/claire-bible" in html
    assert "fofwisdom/claire-bible" in html
    assert '<span class="brand"' in html
    assert 'onclick="resetHome()"' in html
    assert 'function resetHome()' in html
    assert 'id="repolink"' in html
    assert "get sourceBaseUrl(){ return 'https://github.com/fofwisdom/claire-bible'; }" in html
    assert "get githubRepository(){ return 'fofwisdom/claire-bible'; }" in html


def test_render_graph_html_custom_settings():
    from claire.config import Settings

    settings = Settings(
        GITHUB_REPOSITORY="custom-team/my-kb",
        SOURCE_BASE_URL="https://custom.git/custom-team/my-kb",
        _env_file=None,
    )
    html = render_graph_html(settings)
    assert 'href="https://custom.git/custom-team/my-kb"' in html
    assert 'title="custom-team/my-kb (GitHub)"' in html or 'custom-team/my-kb' in html
    assert "get sourceBaseUrl(){ return 'https://custom.git/custom-team/my-kb'; }" in html
    assert "get githubRepository(){ return 'custom-team/my-kb'; }" in html


def test_code_block_css_resets():
    """인라인 코드(.md code) 스타일이 블록 코드(.md pre code)에 오염되지 않도록 transparent 리셋 CSS 검증."""
    from claire.graphview import _SHARED_HTML, render_graph_html

    main_html = render_graph_html()
    assert ".md pre code{background:transparent;padding:0;border-radius:0;font-size:inherit;" in main_html
    assert ".md pre code{background:transparent;padding:0;border-radius:0;font-size:inherit;" in _SHARED_HTML


def test_advanced_search_ui_components():
    """고급 검색 아이콘 버튼, 확장 패널, 모드 라벨 및 툴팁 UI 요소 검증."""
    from claire.graphview import GRAPH_HTML

    assert 'id="advsearchbtn"' in GRAPH_HTML
    assert 'id="advsearchpane"' in GRAPH_HTML
    assert 'id="semchk"' in GRAPH_HTML
    assert 'id="sembadge"' in GRAPH_HTML
    assert '인증 필요' in GRAPH_HTML
    assert 'SQLite FTS5 기반 BM25' in GRAPH_HTML
    assert 'FTS + AI RRF 기반 벡터 하이브리드' in GRAPH_HTML
    assert 'function toggleAdvSearch' in GRAPH_HTML
    assert 'Full-Text Search' in GRAPH_HTML
    assert 'Semantic Search' in GRAPH_HTML


def test_doclist_desclines_toolbar():
    """'제목만 표시' 및 '요약 표시' 선택기가 doclist 최상단 툴바에 배치되었는지 검증."""
    from claire.graphview import GRAPH_HTML

    # advsearchpane 안에 desclines가 없어야 함
    adv_pane_match = re.search(r'<div id="advsearchpane"[^>]*>(.*?)</div>\s*</div>', GRAPH_HTML, re.DOTALL)
    assert adv_pane_match is not None
    assert 'id="desclines"' not in adv_pane_match.group(1)

    # doclist 안의 최상단에 .doclist-toolbar 와 id="desclines" 가 위치해야 함
    doclist_match = re.search(r'<div id="doclist">\s*<div class="doclist-toolbar">\s*<select id="desclines"', GRAPH_HTML)
    assert doclist_match is not None
    assert '<option value="0">제목만 표시</option>' in GRAPH_HTML
    assert '<option value="3">요약 표시</option>' in GRAPH_HTML
    assert 'doclistToolbarHtml' in GRAPH_HTML
    assert '.doclist-toolbar{position:sticky;top:0' in GRAPH_HTML

