"""읽기전용 그래프 시각화 — vis.js 용 데이터 변환 + 정적 HTML 페이지.

로컬 inject API(aiohttp)가 /graph(JSON)·/node·/documents·/synthesize·/research 로 노출한다.
정본 DB 를 읽고, 종합(synthesize)·맥락조사(research)만 LLM 비용이 있어 인증 뒤에 둔다.
"""

from __future__ import annotations

import sqlite3
from collections import Counter

from .store import db as dbm


def graph_json(conn: sqlite3.Connection) -> dict:
    """엔티티/관계를 vis.js network 형식(nodes/edges)으로. dangling edge 는 제외.

    각 노드에 degree(연결 수)를 실어 UI 가 degree-centrality 임계로 핵심 서브그래프만
    표시할 수 있게 한다(전체 N개 렌더 → 큰 그래프의 가시성/스케일 문제 해소)."""
    ents = dbm.all_entities(conn)
    rels = dbm.all_relations(conn)
    ent_ids = {e.id for e in ents}
    # 양 끝 노드가 모두 존재하는 관계만(고아 엣지는 vis.js 가 유령 노드를 만들어 깨짐).
    edges = [
        {"id": f"e{i}", "from": r.source_id, "to": r.target_id, "label": r.type,
         "arrows": "to", "dashes": r.provisional}
        for i, r in enumerate(
            r for r in rels if r.source_id in ent_ids and r.target_id in ent_ids)
    ]
    deg: Counter = Counter()
    for e in edges:
        deg[e["from"]] += 1
        deg[e["to"]] += 1
    nodes = [
        {
            "id": e.id,
            "label": e.name,
            "group": e.type,
            "degree": deg.get(e.id, 0),
            "sources": e.sources,  # 문서 기반 필터용(문서 클릭 → 그 문서 엔티티만)
            # 관찰 첫 줄 — hover 시 마우스 위치 커스텀 팝업이 쓴다. vis 기본 title 툴팁은
            # 일부러 안 쓴다(스타일 제약·중복). 그래서 'title' 이 아니라 'obs' 로 보낸다.
            "obs": (e.observations[0][:200] if e.observations else ""),
        }
        for e in ents
    ]
    max_degree = max((n["degree"] for n in nodes), default=0)
    return {"nodes": nodes, "edges": edges,
            "stats": {"entities": len(nodes), "relations": len(edges),
                      "max_degree": max_degree}}


def node_detail(conn: sqlite3.Connection, entity_id: str) -> dict | None:
    """한 노드의 '쓸 수 있는 지식': 전체 observations + 소스 문서(제목·요약·URL) +
    타입 있는 이웃. 패널에 그대로 펼친다. 없으면 None."""
    ent = dbm.get_entity(conn, entity_id)
    if ent is None:
        return None

    neighbors = []
    for r in dbm.neighbors(conn, entity_id):
        out = r.source_id == entity_id
        other = dbm.get_entity(conn, r.target_id if out else r.source_id)
        if other:
            neighbors.append({
                "id": other.id, "name": other.name, "type": other.type,
                "rel": r.type, "dir": "out" if out else "in",
                "provisional": r.provisional,
            })

    documents = []
    for did in ent.sources:
        row = dbm.get_document_row(conn, did)
        if row:
            documents.append({
                "id": did,
                "title": row["title"] or "(제목 없음)",
                "url": row["url"],
                "summary": dbm.latest_extraction_summary(conn, did) or "",
                # 한국어 가독 렌더링(여러 단락) — 패널에서 '자세히 읽기'로 펼친다.
                "detail": dbm.get_document_detail(conn, did) or "",
            })

    return {
        "id": ent.id, "name": ent.name, "type": ent.type,
        "aliases": ent.aliases, "observations": ent.observations,
        "provisional": ent.provisional,
        "neighbors": neighbors, "documents": documents,
    }


def document_detail(conn: sqlite3.Connection, document_id: str) -> dict | None:
    """한 문서(article)의 우측 패널용 상세 — 제목·출처·요약·자세히읽기(detail). 없으면 None.

    좌측 문서를 고르면 그래프 강조에 더해 우측에 이 요약/전문을 펼친다(노드 클릭 없이
    문서 자체를 읽게). 노드 목록은 클라이언트가 graph 의 node.sources 로 계산하므로
    여기선 싣지 않는다(중복 전송 방지)."""
    row = dbm.get_document_row(conn, document_id)
    if row is None:
        return None
    return {
        "id": document_id,
        "title": row["title"] or "(제목 없음)",
        "url": row["url"],
        "source_type": row["source_type"],
        "summary": dbm.latest_extraction_summary(conn, document_id) or "",
        "detail": dbm.get_document_detail(conn, document_id) or "",
        "hidden": bool(row["hidden"]),
        # [1홉 병합, ONEHOP_MERGE_DESIGN.md] 이 문서에 흡수된 부가 출처(예: GeekNews 글에
        # 병합된 그 프로젝트의 github). 원문 링크 계보를 UI 에서 추적 가능하게.
        "extra_sources": dbm.get_document_extra_sources(conn, document_id),
    }


def dedup_clusters(conn: sqlite3.Connection, scan: dict) -> dict:
    """dedup_scan 결과를 웹 UI 용으로 보강 — 각 문서의 제목·URL·본문길이·적재시각 + keeper 추천.

    scan(=svc.dedup_scan)은 ids/urls/titles/score 만 준다. UI 가 '무엇을 유지할지' 고르게
    각 문서 메타를 채우고, 기본 keeper(=최장 본문, 동률이면 최초 적재)를 표시한다 —
    service.dedup_merge 의 keeper 선정과 동일 규칙(웹/CLI 일관)."""
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


def documents_list(conn: sqlite3.Connection, limit: int = 300) -> list[dict]:
    """좌측 문서 패널용 — 최신순 문서(제목·요약·출처타입·시각)."""
    out = []
    for r in dbm.documents_timeline(conn, limit):
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


def synthesis_context(conn: sqlite3.Connection, entity_ids: list[str]) -> tuple[str, list[str]]:
    """선택 노드들의 지식(관찰·연결·출처요약)을 LLM 종합용 컨텍스트 텍스트로 조립.

    결정론적(LLM 없음) — 이 텍스트가 summarize_search 의 근거가 된다. (context, names)."""
    blocks: list[str] = []
    names: list[str] = []
    for eid in entity_ids:
        ent = dbm.get_entity(conn, eid)
        if ent is None:
            continue
        names.append(ent.name)
        parts = [f"## {ent.name} ({ent.type})"]
        if ent.aliases:
            parts.append("별칭: " + ", ".join(ent.aliases))
        if ent.observations:
            parts.append("관찰: " + " ".join(ent.observations))
        rels = []
        for r in dbm.neighbors(conn, eid):
            out = r.source_id == eid
            other = dbm.get_entity(conn, r.target_id if out else r.source_id)
            if other:
                rels.append(f"{r.type} {'→' if out else '←'} {other.name}")
        if rels:
            parts.append("연결: " + ", ".join(rels[:12]))
        for did in ent.sources:
            summ = dbm.latest_extraction_summary(conn, did)
            if summ:
                parts.append(f"출처요약: {summ}")
        blocks.append("\n".join(parts))
    return "\n\n".join(blocks), names


def synthesize(conn, provider, entity_ids: list[str], query: str | None = None) -> dict:  # noqa: ANN001
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


# 버전을 고정한 vis-network 기반 단일 페이지. /graph·/node·/documents·/synthesize·/research 사용.
GRAPH_HTML = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>claire_bible — 지식 그래프</title>
<script src="https://unpkg.com/vis-network@10.1.0/standalone/umd/vis-network.min.js"
 integrity="sha384-Kp7cMaDnHOrgpE8FT6l7tUuGIo7kBcBVcttockpXN/whrsQBcy9ZcpKmr/1a/nMo"
 crossorigin="anonymous" referrerpolicy="no-referrer"></script>
<script src="https://unpkg.com/marked@4.3.0/marked.min.js"
 integrity="sha384-QsSpx6a0USazT7nK7w8qXDgpSAPhFsb2XtpoLFQ5+X2yFN6hvCKnwEzN8M5FWaJb"
 crossorigin="anonymous" referrerpolicy="no-referrer"></script>
<script src="https://unpkg.com/dompurify@3.1.6/dist/purify.min.js"
 integrity="sha384-+VfUPEb0PdtChMwmBcBmykRMDd+v6D/oFmB3rZM/puCMDYcIvF968OimRh4KQY9a"
 crossorigin="anonymous" referrerpolicy="no-referrer"></script>
<script>
  // 깜빡임 방지: 페인트 전에 저장된 테마를 documentElement 에 적용. 기본값=light(사용자 요구).
  (function(){ try{ var t=localStorage.getItem('claireTheme')||'light';
    document.documentElement.setAttribute('data-theme', t); }catch(e){
    document.documentElement.setAttribute('data-theme','light'); } })();
</script>
<style>
  /* 라이트 기본(:root) + 다크 옵션([data-theme=dark]). 색은 전부 CSS 변수로 — vis 캔버스
     색만 JS(THEMES)로 따로 갱신(캔버스는 CSS 변수가 안 닿음). */
  :root{
    --bg:#ffffff; --fg:#1f2328; --muted:#656d76; --bar-bg:#f6f8fa; --border:#d0d7de;
    --panel-bg:#f6f8fa; --docs-bg:#f6f8fa; --accent:#0969da; --accent2:#1a7f37;
    --chip-bg:#eaeef2; --hover:#eef1f4; --active:#ddf4ff; --net-bg:#ffffff;
    --card-bg:#ffffff; --detail-bg:#ffffff; --mark-bg:#fff8c5; --mark-fg:#633c01;
    --btn-bg:#1f883d; --btn-fg:#ffffff; --sec-bg:#eaeef2; --sec-fg:#24292f;
    --rel:#9a6700; --nodebtn-hover:#dde3ea; --shadow:rgba(31,35,40,.28);
  }
  [data-theme="dark"]{
    --bg:#0e1116; --fg:#d7dbe0; --muted:#8b949e; --bar-bg:#161b22; --border:#2a2f37;
    --panel-bg:#10151c; --docs-bg:#10151c; --accent:#58a6ff; --accent2:#7ee787;
    --chip-bg:#1f2937; --hover:#161b22; --active:#1f2937; --net-bg:#0e1116;
    --card-bg:#161b22; --detail-bg:#0e1116; --mark-bg:#4d3800; --mark-fg:#ffdf5d;
    --btn-bg:#238636; --btn-fg:#ffffff; --sec-bg:#30363d; --sec-fg:#d7dbe0;
    --rel:#d29922; --nodebtn-hover:#2a3344; --shadow:rgba(1,4,9,.6);
  }
  html,body{margin:0;height:100%;font-family:system-ui,sans-serif;background:var(--bg);color:var(--fg)}
  #bar{display:flex;align-items:center;gap:6px;padding:6px 12px;background:var(--bar-bg);border-bottom:1px solid var(--border);font-size:13px;white-space:nowrap}
  #bar .brand{font-weight:600}
  #bar b{color:var(--accent2)}
  .spacer{flex:1}
  #stat{color:var(--muted);text-align:right}
  #authstate{padding:2px 7px;border:1px solid var(--border);border-radius:4px}
  #themebtn{background:transparent;color:var(--fg);border:1px solid var(--border);padding:3px 8px;font-size:14px}
  #themebtn:hover{background:var(--hover)}
  #synthchips{display:flex;gap:4px;overflow:hidden;max-width:280px}
  #synthchips .chip{background:var(--chip-bg);border-radius:10px;padding:1px 7px;font-size:11px;cursor:pointer}
  #legendbar{display:flex;flex-wrap:wrap;gap:10px;padding:4px 12px;background:var(--panel-bg);border-bottom:1px solid var(--border);font-size:11px;color:var(--muted)}
  #legendbar i{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:3px;vertical-align:middle}
  #legendbar .lgsep{margin-left:6px;opacity:.7}
  #legendbar .reltog{cursor:pointer;padding:1px 7px;border-radius:9px;background:var(--chip-bg);border:1px solid var(--border);color:var(--fg)}
  #legendbar .reltog.off{opacity:.4;text-decoration:line-through}
  #bar button.on{outline:2px solid var(--accent2);outline-offset:1px}
  #wrap{display:flex;height:calc(100% - 68px)}
  /* #netwrap 이 위치 기준자, #net 은 vis.Network 컨테이너(vis 가 init 시 innerHTML 을
     지우므로 — 확인됨 — #zoomctl 은 #net *밖*, 형제로 둬야 살아남는다). */
  /* overflow:hidden 필수 — 원래 없었음. #netwrap 은 position:relative(포지션 기준자)라
     캔버스가 자기 박스보다 커지는 어떤 상황(리사이즈 타이밍 레이스 등)에서도 그 오버플로우가
     "positioned 요소는 z-index:auto 라도 non-positioned 형제 위에 그려진다"는 스태킹 규칙
     때문에 무조건 #docs(문서목록) 위로 새어나갔다(사용자 제보: 즐겨찾기 클릭 직후 리스트가
     "투명해짐"— 실은 투명이 아니라 그래프가 그 위에 덮어 그려진 것, canvas.height 를 강제로
     키워 실측 재현). 뷰포트 역할의 컨테이너는 자기 콘텐츠를 항상 잘라내는 게 맞다. */
  #netwrap{flex:1;min-width:0;position:relative;overflow:hidden}
  #net{width:100%;height:100%;background:var(--net-bg)}
  /* 모바일 핀치줌 대체 — zoomView:false(Mac 휠 점프 수정, 2026-06-24)가 vis-network 의
     핀치줌도 함께 꺼버려(같은 옵션이 관장) 터치로는 줌 방법이 없었다(회귀). 커스텀 핀치
     제스처는 vis 내부 hammer 인스턴스와 이벤트 충돌 위험이 있어, 확실히 동작하는 +/- 버튼으로
     대체(사용자도 "터치나 버튼추가나" 로 버튼을 대안으로 제시함). */
  #zoomctl{position:absolute;right:14px;bottom:14px;display:none;flex-direction:column;gap:6px;z-index:5}
  #zoomctl button{width:36px;height:36px;border-radius:50%;border:1px solid var(--border);
    background:var(--sec-bg);color:var(--sec-fg);font-size:19px;line-height:1;cursor:pointer;opacity:.9}
  #zoomctl button:active{opacity:1;background:var(--hover)}
  @media (max-width:820px){ #zoomctl{display:flex} }
  /* transform:translateZ(0) — 사용자 제보: 스크롤·그래프 팬 중 문서 목록이 순간 투명해짐.
     JS 로 opacity/배경을 바꾸는 코드는 없음(확인됨) — #docs 가 바쁘게 다시 그려지는
     <canvas>(#net)와 나란한 형제라, 모바일 브라우저(주로 iOS Safari 계열)의 GPU 합성 중
     인접 요소가 잠깐 페인트가 빠지는 알려진 부류의 글리치로 추정. 별도 컴포지팅 레이어로
     강제 승격해 캔버스 리페인트의 페인트 경계를 분리하는 표준 완화책(원인 미확정, 가설). */
  #docs{width:280px;display:flex;flex-direction:column;background:var(--docs-bg);border-right:1px solid var(--border);font-size:12px;transform:translateZ(0)}
  #docs .dhead{padding:8px 10px;border-bottom:1px solid var(--border);flex-shrink:0}
  /* 모바일 바텀시트(#docs) 드래그 핸들 — 데스크톱 3분할 레이아웃에선 의미 없으니 숨김.
     실제 드래그 감지는 attachDragSheet(el) 가 #docs 전체에 걸려있어 이 막대 자체엔 별도
     이벤트 리스너가 필요 없다(순전히 "여길 끌 수 있다"는 시각적 신호, 사용자 요구). */
  #draghandle{display:none}
  /* 즐겨찾기(고정) 섹션 — 많아져도 패널을 다 잡아먹지 않게 최대높이+스크롤(사용자 요구:
     '즐찾 많아질 때 대비'), 일반 목록도 min-height 로 항상 일정 공간 확보. */
  /* 즐겨찾기 구간은 본문 목록(#doclist, --docs-bg 그대로)과 배경을 다르게 — 얇은 테두리
     하나뿐이던 첫 시도도, 그 다음 --sec-bg 도 회색 계열끼리라 밝기만 살짝 달라 여전히
     눈에 안 띈다는 피드백(라이트 테마에서 특히). 밝기 대신 색상(hue) 자체를 별 아이콘의
     금색(#e3b341)으로 옅게 우려내 — 저채도 tint 라 위에 얹히는 기본 글자색(--fg/--muted)
     명암비는 거의 안 바뀌어 가독성 걱정 없이 확실히 구별된다. */
  #pinnedhead{padding:4px 10px;font-size:10px;color:var(--muted);background:rgba(227,179,65,.18);flex-shrink:0}
  #pinnedlist{max-height:32%;overflow-y:auto;flex-shrink:0;border-bottom:2px solid var(--border);
    background:rgba(227,179,65,.10)}
  #doclist{flex:1;min-height:120px;overflow-y:auto}
  /* 모바일에서만 쓰는 즐겨찾기/전체 탭(아래 @media 참고) — 데스크톱 사이드바는 세로 공간이
     넉넉해 즐찾+전체 동시 노출이 안 좁으므로 그대로 두고 숨김. */
  #doctabs{display:none;border-bottom:1px solid var(--border)}
  #doctabs button{flex:1;border-radius:0;font-size:11.5px;padding:7px 0}
  #doctabs button.sec{background:transparent;color:var(--muted)}
  #doctabs button.active{color:var(--fg);font-weight:600;box-shadow:inset 0 -2px var(--accent)}
  .dday{position:sticky;top:0;background:var(--bar-bg);color:var(--accent2);font-size:11px;padding:3px 10px;border-bottom:1px solid var(--border);z-index:1}
  .docitem{min-height:34px;padding:7px 78px 7px 10px;border-bottom:1px solid var(--border);cursor:pointer;position:relative;overflow:hidden}
  .docitem:hover{background:var(--hover)}
  .docitem.active{background:var(--active);border-left:3px solid var(--accent2)}
  .docitem.hidden-doc{opacity:.55}
  /* 제목은 아무리 길어도 2줄로 고정 — 안 그러면 즐겨찾기 등 높이가 빠듯한 구간에서
     아이템이 한없이 늘어나 넘쳐 보인다는 피드백(사용자 지적). */
  .docitem b{font-size:12px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;
    overflow:hidden;word-break:break-word}
  .docitem .st{color:var(--muted);font-size:10px;margin-left:6px}
  .docitem.unread{border-left:3px solid var(--accent2)} .docitem.unread b{font-weight:700}
  .docitem .ubadge{color:var(--accent2);font-size:9px;margin-right:4px;vertical-align:middle}
  .docitem .wbadge{font-size:10px;margin-right:2px}
  /* 설명 줄수 — 기본 2줄, #docs 에 붙는 lc0/lc2/lc4 클래스로 토글(사용자 요구, descToggle 참조). */
  .docitem p{margin:.2em 0 0;color:var(--muted);font-size:11px;overflow:hidden;
    display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
  #docs.lc0 .docitem p{display:none}
  #docs.lc4 .docitem p{-webkit-line-clamp:4}
  /* 좌측 문서의 액션 버튼(즐겨찾기·읽기) — 클릭=nav 와 분리. readbtn(📖)은 docactions
     밖의 형제 요소(모바일에서 아이템 전체 높이를 차지하는 리빌 버튼으로 절대배치하려면
     그래야 함, 아래 모바일 미디어쿼리 참조) — 데스크톱/기본값에서는 여기서 원래
     자리(즐찾 버튼 왼쪽)로 직접 위치를 잡아준다. */
  .docitem .docactions{position:absolute;top:6px;right:34px;display:flex;gap:3px}
  .docitem .readbtn{position:absolute;top:6px;right:7px}
  .docitem .rblabel{display:none}    /* 데스크톱 = 아이콘만(작은 자리); 모바일 리빌 배지에서만 텍스트로 교체 */
  .docitem .actbtn{background:var(--sec-bg);color:var(--sec-fg);
    border:1px solid var(--border);border-radius:4px;padding:1px 6px;font-size:11px;cursor:pointer;opacity:.85}
  .docitem .actbtn:hover{opacity:1;border-color:var(--accent)}
  .docitem .actbtn.pinned{opacity:1;color:#e3b341}
  #showhidden{display:block;padding:7px 10px;font-size:11.5px;color:var(--fg);cursor:pointer;
    text-align:center;border-top:1px solid var(--border);background:var(--sec-bg)}
  #showhidden:hover{background:var(--hover)}
  /* 읽기전용 세션(/webro) — 눌러도 서버가 404 내는 쓰기 UI 를 아예 안 보여준다(사용자
     요구). 동적으로 그리는 버튼(문서 즐겨찾기/숨기기·노드의 "종합에 추가"·조사 입력창)은
     JS 의 READONLY 분기에서 애초에 렌더 안 함 — 여기선 정적 스켈레톤 버튼만. */
  body.ro #synthbtn, body.ro #addbtn, body.ro #dedupbtn, body.ro .rshare{display:none!important}
  /* word-break 은 상속 속성 — #panel 전체에 걸어 요약(.synth)·관찰(li)·출처(.doc p)·
     병합출처(.srclist) 등 하위 요소 전부를 한 번에 커버한다. 예전엔 .md/.docitem b 처럼
     오버플로우 신고가 들어온 곳만 개별로 word-break 를 붙여왔는데, URL 등 공백 없는 긴
     토큰이 낀 요약(.synth)엔 그게 없어 모바일에서 패널 밖으로 글자가 삐져나가던 버그
     (사용자 제보: 글 선택 시 레이아웃 깨짐) — 개별 땜질 대신 컨테이너 레벨로 고정. */
  #panel{width:360px;overflow:auto;padding:14px 16px;background:var(--panel-bg);border-left:1px solid var(--border);font-size:13px;line-height:1.5;word-break:break-word;transform:translateZ(0)}
  /* #panelclose/#panelpeek 는 모바일 전용 오버레이 버튼(아래 @media 안에서만 position:fixed +
     조건부 display:block 부여). 데스크톱 기본값이 빠져있어 #wrap 의 평범한 flex 자식으로
     그대로 노출됐었다 — 버튼 전역 기본색(녹색)까지 그대로 남아 "우측의 의미 없는 녹색
     버튼 2개"로 보였음(사용자 제보, 2026-07-21). 데스크톱에서는 항상 숨김. */
  #panelclose,#panelpeek{display:none}
  #panel h2{margin:.2em 0;font-size:18px} #panel h2 small{color:var(--muted);font-size:12px;font-weight:normal}
  #panel h3{margin:1em 0 .3em;font-size:13px;color:var(--accent2);border-bottom:1px solid var(--border);padding-bottom:2px}
  #panel ul{margin:.2em 0;padding-left:18px} #panel li{margin:.25em 0}
  #panel .doc{margin:.5em 0;padding:6px 8px;background:var(--card-bg);border-radius:5px}
  #panel .doc p{margin:.3em 0 0;color:var(--fg)} #panel a{color:var(--accent);text-decoration:none}
  #panel .doc p.src{margin-top:.45em}
  #panel .docmeta{color:var(--muted);font-size:11px;margin:.1em 0 .6em}
  #panel .readbtn{background:var(--accent);color:#fff;border:0;border-radius:4px;padding:3px 10px;font-size:12px;cursor:pointer;margin:.2em 0}
  /* 숨기기 — 목록이 아니라 상세 패널에 텍스트 버튼으로(사용자 요구). 되돌리기 번거로운
     방향(컨펌 필요)이라 튀지 않게 링크 스타일로 옅게, hover 시에만 강조. */
  #panel .hidetextbtn{background:none;border:0;color:var(--muted);font-size:11.5px;cursor:pointer;
    padding:0;margin:.3em 0;text-decoration:underline;text-underline-offset:2px}
  #panel .hidetextbtn:hover{color:var(--danger,#e5534b)}
  #panel .nodebtns{display:flex;flex-wrap:wrap;gap:5px;margin:.3em 0}
  #panel .nodebtn{background:var(--chip-bg);color:var(--fg);border:1px solid var(--border);border-radius:12px;
    padding:3px 9px;font-size:11.5px;cursor:pointer;max-width:100%;overflow:hidden;
    text-overflow:ellipsis;white-space:nowrap}
  #panel .nodebtn:hover{background:var(--nodebtn-hover);border-color:var(--accent2)}
  #panel .nodebtn i{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px;vertical-align:middle}
  #panel .backlink{display:inline-block;margin:0 0 .6em;color:var(--accent);font-size:12px;cursor:pointer}
  #panel .rel{color:var(--rel);font-size:11px} #panel .al{color:var(--muted)}
  #panel .hint{color:var(--muted);margin-top:1em}
  #panel .synth{white-space:pre-wrap;background:var(--card-bg);border:1px solid var(--border);border-radius:5px;padding:10px;margin:.4em 0;line-height:1.6}
  #panel .research{display:flex;gap:6px;margin:.4em 0}
  #panel .research input{flex:1;min-width:0}
  #panel .meter{color:var(--muted);font-size:11px}
  #rprog{margin:.3em 0;padding-left:18px} #rprog li{margin:.3em 0;color:var(--muted);font-size:12px}
  input{background:var(--bg);color:var(--fg);border:1px solid var(--border);border-radius:4px;padding:3px 8px;font-size:13px}
  #q{width:150px}
  button{background:var(--btn-bg);color:var(--btn-fg);border:0;border-radius:4px;padding:4px 10px;cursor:pointer;font-size:13px}
  button:hover{filter:brightness(1.08)}
  button.sec{background:var(--sec-bg);color:var(--sec-fg)}
  #fslider{width:100px;vertical-align:middle}
  /* ==형광== 강조 — render_detail/요약이 LLM 으로 표시한 핵심 구절(마크다운 후 <mark>). */
  mark{background:var(--mark-bg);color:var(--mark-fg);padding:0 .15em;border-radius:2px}
  /* 노드 hover 팝업 — 마우스 위치에 작게 띄우는 요약(우측 패널 미리보기 대체, 사용자 요구).
     클라 데이터(이름·타입·연결수·관찰 첫 줄)만 써서 fetch 없이 즉시. pointer-events:none 으로
     커서/그래프 조작을 방해하지 않는다. */
  #nodepop{position:fixed;z-index:60;max-width:340px;background:var(--card-bg);color:var(--fg);
    border:1px solid var(--border);border-radius:7px;box-shadow:0 6px 22px var(--shadow);
    padding:8px 11px;font-size:12px;line-height:1.45;pointer-events:none;display:none}
  #nodepop b{font-size:13px} #nodepop .pt{color:var(--muted);font-size:11px}
  #nodepop .po{margin-top:.4em} #nodepop i{display:inline-block;width:8px;height:8px;
    border-radius:50%;margin-right:5px;vertical-align:middle}
  /* hover 를 좀 더 끌면 fetch 로 채우는 출처 문서 한 건(제목+글 일부) — 점진적 공개. */
  #nodepop .psrc{margin-top:.55em;border-top:1px solid var(--border);padding-top:.45em}
  #nodepop .ptt{font-weight:600;color:var(--accent);margin-bottom:.25em}
  #nodepop .psb{color:var(--muted);font-size:11px;line-height:1.45}
  /* 중복정리 패널의 유지문서 라디오 — 전역 input 폭(150px)이 라디오까지 늘리지 않게. */
  #panel input[type=radio]{width:auto;vertical-align:middle}
  /* --- 마크다운 본문(읽기 팝업 + 패널 detail) --- */
  .md{line-height:1.75;font-size:14px;word-break:break-word}
  #reader .rbody .md{font-size:var(--read-fs,16px)}   /* 읽기 팝업은 A−/A+ 로 글자 크기 조절 */
  .md h1{font-size:1.5em} .md h2{font-size:1.3em;margin:1.1em 0 .4em;border-bottom:1px solid var(--border);padding-bottom:.2em}
  .md h3{font-size:1.12em;margin:1em 0 .35em;color:var(--fg);border:0}
  .md p{margin:.6em 0} .md ul,.md ol{margin:.5em 0;padding-left:1.5em} .md li{margin:.3em 0}
  .md strong{color:var(--fg)} .md a{color:var(--accent)}
  .md img{max-width:100%;height:auto;display:block;margin:.8em auto;border-radius:6px;border:1px solid var(--border)}
  .md em{color:var(--muted)} .md blockquote{margin:.6em 0;padding:.2em .9em;border-left:3px solid var(--border);color:var(--muted)}
  .md code{background:var(--chip-bg);padding:.1em .35em;border-radius:3px;font-size:.9em}
  .md pre{background:var(--card-bg);border:1px solid var(--border);border-radius:6px;padding:.8em;overflow:auto}
  .md table{border-collapse:collapse;margin:.6em 0} .md th,.md td{border:1px solid var(--border);padding:.3em .6em}
  /* --- 중앙 읽기 팝업(모달) — 좌측 문서의 '읽기' 버튼으로 연다(nav 와 분리, 사용자 요구) --- */
  #reader{position:fixed;inset:0;background:var(--shadow);display:none;z-index:50;
    align-items:flex-start;justify-content:center;padding:2.5vh 14px;overflow:auto;--read-fs:16px}
  #reader.open{display:flex}
  #reader .sheet{background:var(--bg);color:var(--fg);max-width:1120px;width:100%;border-radius:10px;
    border:1px solid var(--border);box-shadow:0 12px 40px var(--shadow);padding:0 0 28px;
    max-height:95vh;display:flex;flex-direction:column;box-sizing:border-box}
  #reader .rhead{display:flex;align-items:flex-start;gap:10px;padding:16px 24px;border-bottom:1px solid var(--border);
    position:sticky;top:0;background:var(--bg);border-radius:10px 10px 0 0;z-index:1}
  #reader .rhead h1{margin:0;font-size:22px;flex:1} #reader .rhead .rmeta{color:var(--muted);font-size:12px;margin-top:.3em;font-weight:normal}
  /* 글자 크기 조절(A−/A+) — 읽기 편의, 설정은 브라우저에 기억. */
  #reader .rzoom{display:flex;align-items:center;gap:2px}
  #reader .rzoom button{background:var(--sec-bg);color:var(--sec-fg);border:0;border-radius:6px;
    font-size:14px;line-height:1;padding:6px 9px;cursor:pointer}
  #reader .rzoom .fsv{color:var(--muted);font-size:11px;min-width:30px;text-align:center}
  #reader .rclose{background:var(--sec-bg);color:var(--sec-fg);border:0;border-radius:6px;font-size:18px;
    line-height:1;padding:5px 11px;cursor:pointer}
  /* 읽기 팝업의 공유 링크 영역 — 🔗 누르면 생성한 핫링크를 보여주고 복사. */
  #reader .sharebox{display:none;margin:10px 24px 0;padding:8px 12px;background:var(--active);
    border:1px solid var(--accent);border-radius:6px;font-size:12px;gap:8px;align-items:center}
  #reader .sharebox.on{display:flex} #reader .sharebox input{flex:1;min-width:0}
  #reader .sharebox button{background:var(--accent);color:#fff;border:0;border-radius:4px;
    padding:3px 9px;font-size:12px;cursor:pointer}
  #reader .rhead .rshare{background:var(--sec-bg);color:var(--sec-fg);border:0;border-radius:6px;
    font-size:15px;line-height:1;padding:5px 10px;cursor:pointer}
  #reader .rbody{padding:10px 32px 0;overflow:auto}
  #reader .rsection{color:var(--muted);font-size:11px;letter-spacing:.04em;text-transform:uppercase;margin:1.2em 0 .2em}
  /* 모바일/좁은 화면: 가로 3분할 대신 세로 스택(그래프 먼저). 모든 기능 터치로 도달 가능. */
  @media (max-width:820px){
    #bar{white-space:normal;flex-wrap:wrap;gap:4px 8px}
    #bar .spacer{display:none}
    #q{width:38vw;min-width:120px}
    /* 사용자 제보: 화면 하단에 빈 공간이 생김. 원인 — #docs 를 아래에서
       position:absolute 로 빼면서 #wrap 의 flow 높이가 #netwrap 하나(고정 58vh)로만
       결정되게 됐는데, 헤더(#bar+#legendbar, 특히 관계 타입 칩이 여러 줄로 줄바꿈되는
       #legendbar)의 실제 높이는 기기·화면폭마다 달라서 "58vh + 헤더" 합이 실제 화면
       높이보다 짧아지면 그 차이만큼 화면 맨 아래가 빈다. 고정 vh 대신 body 를 세로
       flex 로 만들고 #wrap 이 남는 공간을 전부 차지(flex:1)하게 바꿔 헤더가 몇 줄이든
       그래프가 항상 나머지 전체를 채우게 한다. */
    body{display:flex;flex-direction:column}
    /* position:relative — #docs 를 이 박스 기준으로 절대배치(아래)하기 위한 기준자.
       flex:1 — body 안에서 헤더(#bar+#legendbar) 를 뺀 나머지 전부를 차지. */
    #wrap{flex-direction:column;flex:1;min-height:0;position:relative}
    /* #docs 가 아래에서 position:absolute 로 빠지므로 #wrap 의 flow 높이는 사실상
       #netwrap 하나로 결정된다 — flex:1 로 #wrap 전체(=헤더 뺀 나머지 화면)를 그대로
       채운다. min-height 는 극단적으로 헤더가 길어질 때의 최소 방어선. */
    #netwrap{order:-1;flex:1;min-height:260px;width:100%}
    /* 목록(#docs) 전체 개편(2026-07-15, 사용자 요구) — 탭하면 바로 문서가 열리던 것 대신
       "탭→목록이 그래프 위로 슬라이드 업(그래프는 위쪽에 적당히 남음)→그 상태에서 글
       탭하면 📖 버튼 리빌→그거 눌러야 크게읽기" 2단계 흐름으로. #wrap 기준 position:absolute
       오버레이라 #netwrap 자체 크기는 절대 안 건드림(그래프 리사이즈 버그 계열 재발 방지 —
       #2/#3/#4 세 버그의 공통 원인이었음). 퍼센트는 #wrap(=#netwrap과 같은 높이) 기준이라
       헤더 높이가 기기마다 달라도 항상 같은 비율로 그래프가 남는다(collapsed 62%,
       listopen 21% 남김). */
    #docs{width:auto;position:absolute;left:0;right:0;bottom:0;z-index:10;overflow:hidden;
      height:38%;transition:height .28s ease;border-right:none;
      border-top:1px solid var(--border);box-shadow:0 -4px 14px var(--shadow)}
    /* 사용자 요구: 목록을 끌어올린 뒤 "다시 내릴 수 있음"을 알려주는 손잡이가 없었음 —
       리스트 레이아웃 최상단에 잡을 수 있는 막대(pill) 표식 추가. collapsed 상태에선
       #docs 전체가 이미 드래그 대상이라 이 막대도 자동으로 같이 끌린다. listopen
       상태에선 #docs 자체 드래그가 꺼지므로(목록 내부 스크롤 보존) #draghandle 에
       별도 드래그-접기 리스너를 붙여둔다(아래 attachDragSheet(#draghandle,…) 참고,
       2026-07-22) — touch-action:none 은 그 리스너가 pointermove 8px 문턱 전에
       브라우저 기본 제스처(pull-to-refresh)로 새는 걸 막는다. */
    #draghandle{display:flex;justify-content:center;padding:7px 0 3px;flex-shrink:0;touch-action:none}
    #draghandle::before{content:'';width:36px;height:4px;border-radius:2px;background:var(--border)}
    body.listopen #docs{height:79%}
    #docs .dhead{position:static}
    /* #zoomctl(+/−) 이 #netwrap 바닥에 붙어있는데, 이제 #docs 가 그 자리를 덮으므로
       collapsed 높이(38%) 위로 들어올려 항상 눌리게(펼친 상태에서만 같이 덮이는 건 허용
       — 그때는 그래프를 탭해 목록부터 접는 게 자연스러운 동선). %는 #netwrap 자기 높이
       기준(포함 블록)이라 #docs 의 %와 같은 값으로 맞아떨어진다. */
    #zoomctl{bottom:calc(38% + 14px)}
    /* 즐찾:전체 4:6 동시분할(예전 방식)이 "이중으로 나와서 좁다"는 피드백(2026-07-21) —
       탭으로 분리해 활성 탭 쪽이 남는 공간을 전부 쓰게 바꾼다. 즐찾이 없으면(.haspinned
       없음) 탭 자체를 안 보여주고 #doclist 가 공간을 전부 쓴다(base #doclist{flex:1}).
       #pinnedhead("⭐ 즐겨찾기" 라벨)는 탭 라벨과 중복이라 모바일 탭 모드에선 계속 숨김
       — 대신 #pinnedlist 가 활성 탭일 때 화면 전체를 쓴다. */
    #docs.haspinned #doctabs{display:flex}
    #docs.haspinned #pinnedhead,#docs.haspinned #pinnedlist{display:none}
    #docs.haspinned #doclist{min-height:0}
    #docs.haspinned.tab-pinned #pinnedlist{display:block;flex:1;max-height:none;min-height:0;border-bottom:0}
    #docs.haspinned.tab-pinned #doclist{display:none}
    /* 피드백: ⭐(즐찾)이 우측 상단에 있으면 리빌 배지(📖, 바로 아래)와 손가락 하나
       폭 안에 붙어있어 손가락이 두꺼우면 오탭하기 쉽다 — 모바일에서만 ⭐을 아이템
       좌측으로 옮겨 우측은 리빌 배지 전용 공간으로 비운다(데스크톱은 마우스라
       정밀하므로 원래 자리 유지). */
    .docitem{padding-left:32px}
    .docitem .docactions{position:absolute;top:50%;left:6px;right:auto;transform:translateY(-50%)}
    /* 📖(크게읽기) 버튼만 리빌 대상 — ⭐(즐찾)은 사용자 요구대로 항상 그대로 노출.
       기본은 오른쪽 밖으로 밀어둔 채 숨기고, 탭한 아이템(.revealed)만 슬라이드 인. */
    /* 피드백(#9-1): "하이라이트가 안 예쁘다" — 아이템 우측 전체 높이를 차지하는 각진
       단색 스트립(아이콘만)이 원인으로 지목됨. 사용자 확인: "그냥 텍스트로, 예쁘게" —
       아이콘 대신 텍스트 라벨로, 모서리를 둥글게 + 그림자를 줘서 떠 있는 배지처럼.
       top:26px 로 ⭐(즐찾, .docactions, top:6px 근방)와 안 겹치게 그 아래부터 차지. */
    .docitem .readbtn{position:absolute;top:26px;right:6px;bottom:6px;width:64px;
      display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:600;
      background:var(--accent);color:#fff;border:0;border-radius:10px;
      box-shadow:0 2px 8px var(--shadow);
      transform:translateX(130%);transition:transform .2s ease;z-index:2}
    .docitem .readbtn .rbicon{display:none}     /* 모바일 리빌 배지 = 텍스트 전용(아이콘 숨김) */
    .docitem .readbtn .rblabel{display:block}   /* base 는 데스크톱용으로 숨겨둠 — 모바일만 노출 */
    .docitem.revealed .readbtn{transform:translateX(0)}
    /* 피드백: collapsed(펼치기 전) 상태에서 목록을 스와이프하면 내부 스크롤로 먹혀
       확장(listopen) 트리거가 씹힘 — collapsed 동안은 두 리스트 자체 스크롤을 꺼서
       모든 터치가 확장으로만 가게 한다(펼친 뒤엔 base 의 overflow-y:auto 복귀). */
    body:not(.listopen) #pinnedlist, body:not(.listopen) #doclist{overflow:hidden}
    /* 실기기 터치로 재현 확인된 버그(2026-07-20, 사용자 제보 "슬라이드 완전히 고장") —
       attachDragSheet 는 pointermove 8px 문턱을 넘은 뒤에야 preventDefault 를 부르는데,
       touch-action 이 기본값(auto)이면 그 전에 브라우저가 이미 세로 스크롤 제스처로
       판정해버려(스펙상 방향이 한 번 잠기면 이후 preventDefault 는 효과 없음) 실제
       touchmove 델타의 대부분이 우리 JS 에 안 옴 — 합성 PointerEvent(dispatchEvent) 로만
       검증했던 이전 세션들은 브라우저의 실제 터치 파이프라인을 안 거쳐 이 클래스의 버그를
       못 잡았다(CDP Input.dispatchTouchEvent 로 실측 재현: 220px 실제 스와이프 중 19px만
       반영됨). collapsed(드래그 활성 구간)에서만 touch-action:none 으로 브라우저의 기본
       제스처 판정 자체를 꺼서 모든 델타가 우리 코드로 오게 한다 — #net 캔버스에 이미 쓰던
       것과 같은 해법(위 applyTouchMode 주석 참고). listopen 이후엔 내부 리스트 스크롤이
       필요하므로 원래 값(auto)으로 복귀. */
    body:not(.listopen) #docs{touch-action:none}
    /* 그래프를 접어 공간을 나눠쓰던 방식(#2) 대신 — 세 버그(그래프 접기 실패·오버플로우·
       리사이즈 고착)가 전부 "캔버스 렌더 크기를 CSS 박스와 매번 동기화해야 한다"는 같은
       취약점에서 나왔다는 걸 확인한 뒤, 사용자 제안으로 전면 개편(2026-07-15) — 그래프는
       항상 58vh 그대로 두고, 문서/노드 상세는 화면 전체를 덮는 슬라이드 오버레이로 분리.
       그래프 캔버스 크기를 아예 안 건드리므로 이 버그 계열이 구조적으로 사라진다. */
    /* box-sizing:border-box 필수 — 데스크톱 기본 규칙(위 #panel)의 padding:14px 16px
       (좌우 32px)이 여기선 top 만 재정의되고 좌/우/하는 그대로 상속되는데, content-box(전역
       기본값)에서는 padding 이 width 에 더해져 실렌더 너비가 422px(뷰포트 390px + 32px)로
       튀어나간다. peek 상태의 "26px 만 걸쳐 보이기"는 이 실제 너비 기준
       translateX(calc(100% - 26px)) 로 계산되므로, 너비가 32px 더 크면 그 계산도 같이
       밀려 걸친 조각이 뷰포트 밖으로 완전히 벗어나 화면에 안 보이고 탭/드래그도 안 먹힘
       (실측 확인: elementFromPoint 로 뷰포트 안 어디서도 #panel 을 못 찾음 — peek 조각이
       유령이었던 셈). border-box 로 padding 을 100% 안에 포함시켜 실제 렌더 너비를
       뷰포트와 정확히 맞춘다. */
    /* 사용자 제보(2026-07-24): 아무것도 선택 안 한 "완전히 접힌" 상태엔 패널이 화면
       밖(translateX(100%))으로 완전히 사라져 끌어서 열 손잡이 자체가 없었다 — 이제
       기본 상태를 "26px 만 걸쳐 보이기"로 두어(peek 과 같은 모양) 문서/노드 선택
       여부와 무관하게 항상 화살표 손잡이가 보이고 왼쪽으로 끌면 열리게 한다.
       body.reading(완전히 펼침) 일 때만 전체 노출로 덮어쓴다. */
    #panel{width:100%;position:fixed;inset:0;z-index:40;border:0;padding-top:52px;
      box-sizing:border-box;touch-action:pan-y;
      transform:translateX(calc(100% - 26px));transition:transform .25s ease;box-shadow:-6px 0 20px var(--shadow)}
    body.reading #panel{transform:translateX(0)}
    /* 데스크톱 기본값(위 #panelclose,#panelpeek{display:none})을 모바일에서 되돌려 켠다 —
       reading(전체 오픈) 이 아닌 한(idle·peek 공통) 항상 보이게. */
    #panelpeek{display:block;position:fixed;top:50%;right:0;z-index:42;
      transform:translateY(-50%);width:26px;height:56px;border-radius:8px 0 0 8px;
      border:1px solid var(--border);border-right:0;background:var(--accent);color:#fff;
      font-size:15px;line-height:1;cursor:pointer}
    body.reading #panelpeek{display:none}
    #panel .hint br{display:none}
    #panelclose{display:none;position:fixed;top:8px;right:8px;z-index:41;width:36px;height:36px;
      border-radius:50%;border:1px solid var(--border);background:var(--sec-bg);color:var(--sec-fg);
      font-size:16px;line-height:1;cursor:pointer}
    body.reading #panelclose{display:block}
    /* 사용자 제보(2026-07-22): 크게읽기 하단이 잘림 — 모바일 브라우저는 주소창
       표시/숨김에 따라 실제 보이는 높이가 100vh 보다 작아질 수 있다(흔한 모바일 웹
       버그). 동적 뷰포트 단위(100dvh, 지원 브라우저에서 실제 보이는 높이 기준)를
       우선 적용하고 100vh 는 폴백으로 남겨둔다. */
    #reader{padding:0} #reader .sheet{max-height:100vh;max-height:100dvh;border-radius:0;
      height:100vh;height:100dvh}
    /* 홈 인디케이터 등 세이프에어리어가 있는 기기에서 마지막 줄이 가려지지 않게
       하단 여백을 추가로 확보. */
    #reader .rbody{padding:8px 16px calc(8px + env(safe-area-inset-bottom))}
    /* 사용자 제보: 크게읽기(#reader) 헤더에서 제목(h1, flex:1)이 A−/A+/🔗/✕ 버튼들과
       한 줄에서 폭을 나눠 쓰다 보니 좁은 화면에선 버튼 4개(약 200px)+패딩을 뺀 나머지가
       너무 좁아져 제목이 짓눌림. flex-wrap 으로 제목을 자기 줄에 단독 배치(basis 100%)하고
       버튼들은 다음 줄로 내려 분리 — 버튼 줄은 우측 정렬 유지. */
    #reader .rhead{flex-wrap:wrap}
    #reader .rhead h1{flex:0 0 100%}
    #reader .rhead .rzoom{margin-left:auto}
  }
</style></head>
<body>
<div id="bar">
  <span class="brand">claire_bible</span>
  <input id="q" placeholder="검색(엔터)" oninput="onSearchInput(this.value)"/>
  <label style="font-size:12px"><input type="checkbox" id="sem" style="width:auto"/> 의미</label>
  <button id="searchbtn" class="sec" onclick="doSemantic()" style="display:none">🔎 의미검색</button>
  <span id="synthchips"></span>
  <button id="synthbtn" onclick="synth()">🧩 종합 (0)</button>
  <button id="addbtn" class="sec" onclick="openIngest()" title="URL·텍스트를 그래프에 적재">➕ 적재</button>
  <button id="dedupbtn" class="sec" onclick="openDedup()" title="근사 중복 문서를 찾아 병합">♻️ 중복정리</button>
  <button id="pathbtn" class="sec" onclick="togglePathMode()" title="두 노드 사이 연결 경로 찾기">🔗 경로</button>
  <label>연결 ≥ <b id="fmin">0</b> <input id="fslider" type="range" min="0" max="0" value="0" oninput="setDeg(this.value)"/></label>
  <span class="spacer"></span>
  <button id="themebtn" title="라이트/다크 전환" onclick="toggleTheme()">🌙</button>
  <span id="authstate">🔒 인증됨</span>
  <span id="stat">로딩…</span>
</div>
<div id="legendbar"></div>
<div id="wrap">
  <div id="docs"><div id="draghandle" aria-hidden="true"></div><div class="dhead"><input id="docq" placeholder="문서 검색(제목·요약)" oninput="renderDocs(this.value)" style="width:92%"/>
    <select id="desclines" onchange="setDescLines(this.value)" title="목록 설명 줄수" style="width:92%;margin-top:5px;font-size:11px">
      <option value="0">설명 0줄(제목만)</option>
      <option value="2">설명 2줄</option>
      <option value="4">설명 4줄</option>
    </select></div>
    <div id="doctabs">
      <button class="sec active" data-tab="all" onclick="setDocTab('all')">📄 전체</button>
      <button class="sec" data-tab="pinned" onclick="setDocTab('pinned')">⭐ 즐겨찾기</button>
    </div>
    <div id="pinnedhead" style="display:none">⭐ 즐겨찾기</div>
    <div id="pinnedlist"></div>
    <div id="doclist"><p class="hint" style="padding:10px">문서 로딩…</p></div>
    <div id="showhidden" style="display:none" onclick="toggleShowHidden()"></div>
    <div id="hiddenlist"></div></div>
  <div id="netwrap">
    <div id="net"></div>
    <div id="zoomctl">
      <button onclick="zoomBtn(1)" title="확대">+</button>
      <button onclick="zoomBtn(-1)" title="축소">−</button>
    </div>
  </div>
  <div id="panel"></div>
  <!-- 모바일 전용 — #panel 이 슬라이드 오버레이일 때만 CSS 로 보임(body.reading). 데스크톱
       나란히 배치에선 항상 숨김(닫을 필요 자체가 없음). -->
  <button id="panelclose" onclick="closePanelOrPeek()" title="닫기">✕</button>
  <!-- 모바일에서 body.reading(전체 오픈)이 아닌 한 항상 보이는 손잡이(사용자 제보,
       2026-07-24: 아무것도 선택 안 한 상태엔 끌어서 열 진입점 자체가 없었음) — 탭하거나
       왼쪽으로 끌면 열림(#panel 드래그 핸들러 참고). -->
  <button id="panelpeek" onclick="openPeekPanel()" title="상세 보기">◂</button>
</div>
<!-- 노드 hover 시 마우스 위치에 뜨는 작은 요약 팝업(우측 패널 미리보기 대체). -->
<div id="nodepop"></div>
<!-- 중앙 읽기 팝업: 좌측 문서의 '읽기' 버튼/노드 상세의 📖 로 연다(마크다운·이미지 렌더). -->
<div id="reader" onclick="if(event.target===this)closeReader()">
  <div class="sheet">
    <div class="rhead"><h1 id="rtitle"></h1>
      <div class="rzoom">
        <button onclick="setReadFS(-2)" title="글자 작게">A−</button>
        <span class="fsv" id="rfs">16</span>
        <button onclick="setReadFS(2)" title="글자 크게">A+</button>
      </div>
      <button class="rshare" onclick="shareDoc()" title="공유 링크 만들기">🔗</button>
      <button class="rclose" onclick="closeReader()" title="닫기(ESC)">✕</button></div>
    <div class="sharebox" id="sharebox"></div>
    <div class="rbody" id="rbody"></div>
  </div>
</div>
<script>
const TYPE_COLORS = {Tool:'#1f6feb',Framework:'#8957e5',Model:'#bf3989',Paper:'#bf8700',
  Article:'#1a7f37',Repo:'#1b7c83',Concept:'#cf222e',Person:'#bc4c00',Org:'#9a6700',
  Event:'#0969da',Note:'#6e7781'};
const DIM = 0.16;
// vis 캔버스는 CSS 변수가 안 닿아 테마별 색을 JS 로 직접 갱신한다(노드 글자/엣지/테두리).
const THEMES = {
  light:{nodeFont:'#1f2328', edge:'#c4ccd6', edgeHi:'#1a7f37', nodeBorder:'#d0d7de', lit:'#0969da'},
  dark: {nodeFont:'#d7dbe0', edge:'#3a4250', edgeHi:'#7ee787', nodeBorder:'#2a2f37', lit:'#ffffff'},
};
function curTheme(){ return document.documentElement.getAttribute('data-theme')==='dark'?'dark':'light'; }
function T(){ return THEMES[curTheme()] || THEMES.light; }
let allTypes=[];
let net, allNodes, allEdges, allDocs=[];
let curMinDeg=0, activeDoc=null, highlightSet=null, selectedNodeId=null, hoverTimer=null;
let clusterEdges=null;   // 검색 결과를 뭉치게 한 임시 spring 엣지 id 들 — 해제 시 제거
let clusterAnchor=null;  // 검색 시 중앙 앵커 노드 id(매칭은 끌고 비매칭은 밀어냄) — 해제 시 제거
let searchDebounce=null; // 라벨검색 디바운스 타이머 — 타이핑 멈춘 뒤에만 검색 실행
let synthSet=new Set();
// 종합→조사계획 확인 UI 상태(사용자 요구, 2026-07-21) — 계획은 사용자가 체크박스로
// 확인/수정한 뒤에만 실제 웹조사(비용 발생)로 넘어간다.
let synthPlanEntityIds=[], synthPlanAnswer='', synthPlanEntityNames=[], synthPlanQuestions=[];
let READONLY=false;   // /whoami 로 확정(아래 init) — true 면 쓰기 UI(적재/종합/조사/즐겨찾기/숨기기/공유) 렌더 안 함
let allRelTypes=[], relFilter=null;          // 관계 타입 필터: null=전체, Set=선택 타입만 표시
let pathMode=false, pathPicks=[], pathNodes=null, pathEdges=null;  // 2노드 경로 하이라이트(전용 모드)
let revealedDocId=null;   // 모바일: 목록 확장 상태에서 탭한 문서 — 그 아이템의 📖 버튼만 슬라이드 인
const panel = document.getElementById('panel');
// --- 뒤로가기 스택 — 모바일 목록 확장·읽기 팝업처럼 슬라이드/오버레이로 여는 UI 를
// 브라우저 뒤로가기로도 닫을 수 있게(사용자 요구). 여는 쪽은 pushUIState 로 history 에
// 쌓고, in-app 컨트롤(✕·배경 탭 등)로 닫을 땐 직접 안 닫고 closeTopUIState 로 history.back()
// 을 호출해 popstate 를 발생시킨다 — 이렇게 해야 브라우저 히스토리와 내부 스택이 항상
// 일치한다(직접 닫으면 다음 뒤로가기가 엉뚱한 걸 닫아버림).
let uiStack = [];
function pushUIState(name){
  if(uiStack[uiStack.length-1]===name) return;   // 중복 오픈 방지
  uiStack.push(name);
  history.pushState({claireUI:uiStack.length}, '');
}
function closeTopUIState(name){
  if(uiStack.length && uiStack[uiStack.length-1]===name) history.back();
}
window.addEventListener('popstate', ()=>{
  const name = uiStack.pop();
  if(name==='reader') closeReaderUI();
  else if(name==='list') collapseListUI();
  else if(name==='panel') closePanel();
});
// 우측 패널(peek/reading)이 닫혀있는 상태에서 열릴 때만 뒤로가기 스택에 쌓는다(사용자
// 제보, 2026-07-24: 목록+패널을 같이 펼친 뒤 뒤로가기 1번에 둘 다 닫히던 버그 — 패널
// 오픈이 history 항목이 아니었던 게 원인). 이미 peek/reading 이면(다른 문서/노드로
// 전환하는 경우) 중복 push 하지 않는다 — 열림 자체는 이미 스택에 있으므로.
function openMobilePanelUI(){
  if(!mobileMQ.matches) return;
  const b=document.body.classList;
  if(!b.contains('peek') && !b.contains('reading')) pushUIState('panel');
}
// 모바일 목록(#docs) 확장 상태를 실제로 접는 쪽 — popstate(뒤로가기)와 그래프 영역 탭
// 양쪽에서 closeTopUIState('list') 를 거쳐 여기로 들어온다.
function collapseListUI(){
  document.body.classList.remove('listopen','peek','reading');
  revealedDocId = null;
  activeDoc = null;    // #9-5: 목록 접힘 = 문서 선택 해제 — 그래프 하이라이트도 같이 꺼야 함
  panel.innerHTML = defaultHint();
  applyView();
  renderDocs(document.getElementById('docq').value);
}
// 함수로 둔 이유: READONLY 는 /whoami 가 비동기로 확정하므로, 호출 시점 기준으로
// 종합 안내 줄을 넣을지 뺄지 판단해야 한다(고정 문자열이면 초기 로드 시점 값에 박제됨).
function defaultHint(){
  const synthLine = READONLY ? ''
    : '• <b>Ctrl+클릭</b> 또는 상세의 <b>➕ 종합에 추가</b>로 여러 노드를 모아 종합<br>';
  return '<p class="hint">노드를 클릭하면 관찰·출처 문서·연결이 표시됩니다.<br><br>'+synthLine+
    '• 다른 노드에 <b>1.5초</b> 올리면 마우스 옆에 <b>요약 팝업</b>(더 끌면 출처 문서까지)<br>'+
    '• 좌측 문서를 <b>클릭</b>하면 그래프에서 강조(nav), <b>📖</b> 버튼을 누르면 <b>크게 읽기(팝업)</b><br>'+
    '• 우측 위 <b>🌙/🌞</b> 로 라이트·다크 전환</p>';
}
panel.innerHTML = defaultHint();
// #panel 이 기본 힌트가 아닌 실제 내용(문서/노드 상세 등)으로 채워지면 body.reading 을
// 건다 — 모바일 CSS(@media max-width:820px)가 이걸 보고 #panel 을 화면 전체 슬라이드
// 오버레이로 밀어 넣는다(그래프 크기는 항상 고정, 절대 안 건드림 — 예전엔 그래프 높이를
// 접는 방식이었는데 캔버스 리사이즈 동기화가 계속 깨져 사용자 제안으로 전면 개편,
// 2026-07-15). 패널을 채우는 코드 경로가 여러 곳(selectDoc/loadNode/synth/research/
// dedup/ingest…)이라 매번 호출부를 늘리는 대신 #panel 자체를 관찰 — 새 경로가 생겨도
// 자동으로 커버된다.
function syncReadingState(){
  document.body.classList.toggle('reading', panel.innerHTML !== defaultHint());
}
new MutationObserver(syncReadingState).observe(panel, {childList: true, subtree: true, characterData: true});
// 모바일 "찔끔" 상태(body.peek)에서 그 살짝 보이는 #panel 조각을 탭하면 전체 오픈
// (피드백: 우측에 걸쳐 보이다가 터치하면 노드설명 패널이 나오는 식).
panel.addEventListener('click', ()=>{ if(!document.body.classList.contains('reading')) openPeekPanel(); });
function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}

// --- 노드 hover 요약 팝업(마우스 위치) — fetch 없이 클라 데이터(allNodes)만 쓴다 ---
// vis hoverNode 이벤트는 진입 위치를 안 주므로 #net 위 mousemove 로 커서 좌표를 추적해 둔다.
let mouseXY={x:0,y:0};
document.getElementById('net').addEventListener('mousemove', e=>{ mouseXY.x=e.clientX; mouseXY.y=e.clientY; });
const nodepop = document.getElementById('nodepop');
let popReqId=null;        // 현재 팝업이 다루는 노드 id — 늦게 온 fetch 응답(stale) 무시용
let popExpandTimer=null;  // '좀 더 기다리면' 출처 문서를 펼치는 타이머
// id 의 요약 팝업을 (x,y) 위치에 띄운다. 좌표 생략 시 #net 의 커서 추적값(mouseXY) 사용
// → 그래프 hover 는 좌표 없이, 우측 '이 문서의 노드' hover 는 버튼 진입 좌표를 넘긴다.
// 1단계: 클라 데이터(이름·타입·연결수+관찰 첫 줄)로 즉시. 2단계: node fetch 로 관찰 3개.
// 3단계: 더 끌면 출처 문서 1건(제목+글)을 덧붙인다(점진적 공개, 사용자 요구).
function showNodePop(id, x, y){
  const n=allNodes&&allNodes.get(id); if(!n){ hideNodePop(); return; }
  popReqId=id; clearTimeout(popExpandTimer);
  const px = x==null?mouseXY.x:x, py = y==null?mouseXY.y:y;
  nodepop.dataset.x=px; nodepop.dataset.y=py;   // fetch 보강 후 재배치에 쓰려고 보관
  const c=TYPE_COLORS[n.group]||'#8b949e';
  const head='<b>'+esc(n.label)+'</b> <span class=pt>'+esc(n.group||'')+'</span>'+
    '<div class=pt><i style="background:'+c+'"></i>연결 '+(n.degree||0)+'개</div>';
  nodepop.innerHTML=head+(n.obs?'<div class=po>'+esc(n.obs)+'</div>':'');
  nodepop.style.display='block';
  positionPop(px, py);                  // 표시 후(폭/높이 확정) 화면 밖으로 안 나가게 배치
  fetch('node?id='+encodeURIComponent(id)).then(r=>r.json()).then(d=>{
    if(popReqId!==id || nodepop.style.display==='none' || !d || d.error) return;  // 이미 떠났으면 무시
    const obs=(d.observations||[]).slice(0,3);   // 관찰 최대 3개(설명이 너무 적던 문제)
    const base=head + obs.map(o=>'<div class=po>'+esc((o||'').slice(0,200))+'</div>').join('');
    nodepop.innerHTML=base; positionPop(+nodepop.dataset.x, +nodepop.dataset.y);
    const docs=d.documents||[];
    if(docs.length){                    // 좀 더 머물면 출처 문서 1건을 덧붙임
      popExpandTimer=setTimeout(()=>{
        if(popReqId!==id || nodepop.style.display==='none') return;
        nodepop.innerHTML=base+popSource(docs[0]);
        positionPop(+nodepop.dataset.x, +nodepop.dataset.y);
      }, 1400);
    }
  }).catch(()=>{});
}
// 팝업 하단의 출처 문서 한 건 — 제목 + 글(요약 우선, 없으면 전문 앞부분) 일부.
function popSource(d){
  const body=((d.summary||d.detail||'').replace(/\s+/g,' ').trim());
  return '<div class=psrc><div class=ptt>📄 '+esc(d.title||'(제목 없음)')+'</div>'+
    (body?'<div class=psb>'+esc(body.slice(0,240))+(body.length>240?'…':'')+'</div>':'')+'</div>';
}
function positionPop(x, y){
  const pad=14, pw=nodepop.offsetWidth, ph=nodepop.offsetHeight;
  let nx=x+pad, ny=y+pad;
  if(nx+pw > window.innerWidth-4) nx=x-pad-pw;     // 오른쪽 넘치면 커서 왼쪽으로
  if(ny+ph > window.innerHeight-4) ny=y-pad-ph;    // 아래 넘치면 커서 위로
  nodepop.style.left=Math.max(4,nx)+'px'; nodepop.style.top=Math.max(4,ny)+'px';
}
function hideNodePop(){ popReqId=null; clearTimeout(popExpandTimer); nodepop.style.display='none'; }

// 타입별 노드 그룹 색(테마별 테두리). 테마 전환 시 다시 만들어 setOptions 로 적용.
function buildGroups(){ const g={}, th=T();
  allTypes.forEach(t=>{ const c=TYPE_COLORS[t]||'#8b949e';
    g[t]={color:{background:c,border:th.nodeBorder,highlight:{background:c,border:th.lit}}}; });
  return g; }
function syncThemeBtn(){ const b=document.getElementById('themebtn');
  if(b) b.textContent = curTheme()==='dark'?'🌞':'🌙'; }
// 라이트/다크 전환 — localStorage 영속 + vis 캔버스 색(글자/엣지/테두리) 즉시 갱신.
function toggleTheme(){ const next = curTheme()==='dark'?'light':'dark';
  document.documentElement.setAttribute('data-theme', next);
  try{ localStorage.setItem('claireTheme', next); }catch(e){}
  syncThemeBtn();
  if(net){ const th=T();
    net.setOptions({nodes:{font:{color:th.nodeFont}},
      edges:{color:{color:th.edge,highlight:th.edgeHi},font:{color:th.nodeFont}},
      groups:buildGroups()});
    applyView(); }   // 노드별 테두리(lit/normal)도 새 테마 색으로 다시 칠함
}

// 마크다운 → 안전한 HTML. ==형광== 은 <mark> 로(LLM 이 표시한 핵심 강조). DOMPurify 로
// 스크랩 본문 유래 스크립트/위험 태그를 제거(이미지·강조·링크는 허용).
function renderMarkdown(src){
  if(!src) return '';
  let s = String(src).replace(/==([^=\\n]+)==/g, '<mark>$1</mark>');
  if(!window.DOMPurify) return esc(String(src)).replace(/\\n/g,'<br>');
  let html;
  try{ html = (window.marked ? (marked.parse ? marked.parse(s) : marked(s)) : esc(s)); }
  catch(e){ html = esc(s).replace(/\\n/g,'<br>'); }
  return DOMPurify.sanitize(html, {ADD_ATTR:['target']});
}

// 목록 설명 줄수(0/2/4) — 브라우저에 기억. 문서 많아지면 제목/설명이 height 를 너무
// 차지한다는 피드백(사용자 지적) → #docs 에 lc0/lc4 클래스로 CSS line-clamp 토글(기본 2줄).
let descLines = 2;
try{ const v=parseInt(localStorage.getItem('claireDescLines')); if(v===0||v===2||v===4) descLines=v; }catch(e){}
function applyDescLines(){
  const docs=document.getElementById('docs'); if(!docs) return;
  docs.classList.remove('lc0','lc4');
  if(descLines===0) docs.classList.add('lc0');
  else if(descLines===4) docs.classList.add('lc4');
  const sel=document.getElementById('desclines'); if(sel) sel.value=String(descLines);
}
function setDescLines(v){
  descLines = parseInt(v)===0 ? 0 : (parseInt(v)===4 ? 4 : 2);
  try{ localStorage.setItem('claireDescLines', descLines); }catch(e){}
  applyDescLines();
}
applyDescLines();

// 읽기 글자 크기(A−/A+) — 브라우저에 기억. 팝업의 --read-fs 변수로 .md 본문에 적용.
let readFS = 16;
try{ const v=parseInt(localStorage.getItem('claireReadFS')); if(v>=12 && v<=28) readFS=v; }catch(e){}
function applyReadFS(){
  const r=document.getElementById('reader'); if(r) r.style.setProperty('--read-fs', readFS+'px');
  const v=document.getElementById('rfs'); if(v) v.textContent=readFS;
}
function setReadFS(delta){
  readFS = Math.max(12, Math.min(28, readFS + delta));
  try{ localStorage.setItem('claireReadFS', readFS); }catch(e){}
  applyReadFS();
}

// 중앙 읽기 팝업 — 좌측 문서의 '읽기' 버튼/노드 상세의 📖 로 연다(nav 와 분리, 사용자 요구).
let curReaderDoc=null;   // 현재 읽기 팝업의 문서 id(🔗 공유 링크 생성 대상)
function openReader(docId){
  const r=document.getElementById('reader');
  curReaderDoc=docId;
  const sb=document.getElementById('sharebox'); if(sb){ sb.className='sharebox'; sb.innerHTML=''; }  // 이전 공유링크 닫기
  applyReadFS();   // 저장된 글자 크기 적용
  document.getElementById('rtitle').textContent='문서 불러오는 중…';
  document.getElementById('rbody').innerHTML='';
  r.classList.add('open');
  pushUIState('reader');
  fetch('document?id='+encodeURIComponent(docId)).then(x=>x.json()).then(dc=>{
    if(!dc || dc.error){ document.getElementById('rbody').innerHTML='<p class=hint>문서를 찾을 수 없습니다.</p>'; return; }
    renderReader(dc);
  }).catch(()=>{ document.getElementById('rbody').innerHTML='<p class=hint>문서 로드 실패.</p>'; });
}
function renderReader(dc){
  document.getElementById('rtitle').innerHTML = esc(dc.title||'(제목 없음)')
    + (dc.source_type?' <span class=rmeta>'+esc(dc.source_type)+'</span>':'');
  let h='';
  if(dc.url) h+='<p class=docmeta><a href="'+esc(dc.url)+'" target=_blank rel=noopener>↗ 원문 열기</a></p>';
  h+=extraSourcesHtml(dc);
  if(dc.summary) h+='<div class=rsection>요약</div><div class="md">'+renderMarkdown(dc.summary)+'</div>';
  if(dc.detail) h+='<div class=rsection>자세히 읽기</div><div class="md">'+renderMarkdown(dc.detail)+'</div>';
  if(!dc.summary && !dc.detail) h+='<p class=hint>이 문서의 요약/전문이 아직 없습니다.</p>';
  const body=document.getElementById('rbody'); body.innerHTML=h; body.scrollTop=0;
}
// [1홉 병합, ONEHOP_MERGE_DESIGN.md] 이 문서에 흡수된 부가 출처 목록(원문 링크 계보).
function extraSourcesHtml(dc){
  const es=dc.extra_sources||[];
  if(!es.length) return '';
  return '<div class=rsection>병합된 출처 ('+es.length+')</div><ul class=srclist>'+
    es.map(s=>'<li><a href="'+esc(s.url||'')+'" target=_blank rel=noopener>'+
      esc(s.title||s.url||'')+'</a></li>').join('')+'</ul>';
}
// closeReader 는 ✕/배경 탭/ESC 세 곳에서 호출 — 직접 안 닫고 스택을 거친다(popstate 로
// closeReaderUI 가 실제로 닫음). 실제 DOM 정리는 closeReaderUI 로 분리.
function closeReader(){ closeTopUIState('reader'); }
function closeReaderUI(){ document.getElementById('reader').classList.remove('open');
  const sb=document.getElementById('sharebox'); if(sb) sb.className='sharebox'; }

// --- 문서 공유 핫링크 — 세션 토큰(nginx 통과)과 별개의, 이 문서만 여는 읽기전용 링크 ---
// /share 가 공유 토큰을 발급(인증 필요) → /p?s=token 은 비인증으로 그 문서만 보여준다.
async function shareDoc(){
  if(!curReaderDoc) return;
  const sb=document.getElementById('sharebox');
  sb.className='sharebox on'; sb.innerHTML='<span class=pt>공유 링크 생성 중…</span>';
  try{
    const r=await fetch('share',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({doc_id:curReaderDoc})});
    if(r.status===401||r.status===404){ setAuth('idle');
      sb.innerHTML='<span class=pt>세션 만료 — 텔레그램 /web 으로 다시 접속하세요</span>'; return; }
    const d=await r.json();
    if(d.error||!d.path){ sb.innerHTML='<span class=pt>공유 실패: '+esc(d.error||'알 수 없음')+'</span>'; return; }
    const url=location.origin+d.path;
    let copied=false;
    try{ await navigator.clipboard.writeText(url); copied=true; }catch(_){}
    sb.innerHTML='<input id="shareurl" readonly value="'+esc(url)+'" onclick="this.select()"/>'+
      '<button onclick="copyShare()">'+(copied?'✓ 복사됨':'복사')+'</button>';
  }catch(e){ sb.innerHTML='<span class=pt>공유 실패: '+esc(String(e))+'</span>'; }
}
function copyShare(){
  const i=document.getElementById('shareurl'); if(!i) return;
  i.select();
  (navigator.clipboard?navigator.clipboard.writeText(i.value):Promise.reject())
    .then(()=>{ const b=document.querySelector('#sharebox button'); if(b) b.textContent='✓ 복사됨'; })
    .catch(()=>{ try{ document.execCommand('copy'); const b=document.querySelector('#sharebox button'); if(b) b.textContent='✓ 복사됨'; }catch(_){} });
}
// 캔버스를 #net 박스의 실제 픽셀 크기에 맞춰 재설정(이슈1: 모바일에서 vis 가 생성 시점의
// 미해결 높이로 캔버스를 150px 로 잡아 상단 일부만 차지하던 버그). '100%' 는 flex/auto
// 체인에서 안 먹어서 getBoundingClientRect 의 실측 px 로 강제한다. ResizeObserver 가
// 레이아웃 확정·세로스택 전환·회전 시점마다(초기 1회 포함) 다시 맞춘다.
let netBusy = false;                 // 드래그/fit·moveTo 애니메이션 중 true(위 net 생성부에서 배선)
let lastNetSize = {w:0, h:0};
function relayout(){ if(!net || netBusy) return;
  const el=document.getElementById('net'); const r=el.getBoundingClientRect();
  if(r.width<=0 || r.height<=0) return;
  // 크기 변화가 없으면 스킵 — 불필요한 setSize+redraw(churn)가 애니메이션과 겹칠 여지를 줄인다.
  if(Math.abs(r.width-lastNetSize.w)<1 && Math.abs(r.height-lastNetSize.h)<1) return;
  lastNetSize = {w:r.width, h:r.height};
  net.setSize(r.width+'px', r.height+'px'); net.redraw(); }
if(window.ResizeObserver){ new ResizeObserver(relayout).observe(document.getElementById('net')); }
window.addEventListener('resize', relayout);
window.addEventListener('orientationchange', ()=>setTimeout(relayout, 300));

// 모바일 판정 — CSS @media(max-width:820px) 세로 스택과 같은 기준을 공유.
const mobileMQ = window.matchMedia('(max-width:820px)');
// 이슈(2026-06-12): 세로 스택에서 그래프(58vh)+문서목록(34vh)이 화면을 덮는데,
// vis(hammer)가 캔버스에 touch-action:none 을 깔아 한 손가락 스와이프를 전부 그래프
// 팬으로 소비 → #panel(내용)까지 페이지 스크롤로 내려갈 방법이 없었다.
// 지도앱식 협조적 제스처로 분리: 모바일에선 touch-action:pan-y — 세로 스와이프는
// 브라우저(페이지 스크롤), 가로 드래그·핀치·탭은 hammer(그래프 조작·노드 선택).
// hammer 가 인라인 style 로 none 을 박으므로 CSS 가 아니라 JS 로 덮어써야 한다.
function applyTouchMode(){
  document.querySelectorAll('#net, #net div, #net canvas').forEach(el=>{
    el.style.touchAction = mobileMQ.matches ? 'pan-y' : 'none';
  });
}
mobileMQ.addEventListener('change', applyTouchMode);
// 노드 탭→내용 확인이 주 흐름인데 #panel 이 화면 밖(맨 아래)이라, 명시적 액션 후
// 결과 위치로 자동 스크롤해 준다(모바일만). hover 미리보기에는 적용하지 않는다.
function mobileScrollTo(id){ if(mobileMQ.matches) document.getElementById(id).scrollIntoView({behavior:'smooth'}); }
// 목록이 확장된(listopen) 상태에서 남겨둔 그래프 영역을 만지면 목록을 다시 접는다(사용자
// 요구). 예전엔 'click'(탭에만 반응, 드래그는 통과)으로 가로챘는데 — 브라우저는 pointer가
// down→up 사이 일정 거리 이상 움직이면 click 을 안 쏘고 드래그로 처리하므로, 좁게 남은
// 그래프 스트립에서 드래그(팬 제스처)는 그대로 vis(hammer)로 새어 들어가 카메라가 엉뚱한
// 위치로 튀는 문제가 있었다(#9-4). pointerdown 을 capture 단계에서 가로채면 드래그
// "시작" 시점에 바로 잡아 hammer 가 이벤트 자체를 아예 못 받게 된다 — 좁은 스트립에서
// 의미 있는 팬은 어차피 불가능하므로 listopen 중엔 이 영역의 pan/드래그를 통째로 막는
// 게 제품적으로도 맞다.
document.getElementById('netwrap').addEventListener('pointerdown', e=>{
  if(mobileMQ.matches && document.body.classList.contains('listopen')){
    e.stopPropagation();
    e.preventDefault();
    // list 위에 panel(peek/reading)이 겹쳐 열려있으면 뒤로가기 스택 맨 위는 'panel'이라
    // closeTopUIState('list')가 조용히 no-op 된다(2026-07-24, 목록+패널 동시 닫힘 수정의
    // 부작용 방지) — 스택 맨 위 아무거나 하나 닫는다(뒤로가기 1번과 동일 동작).
    if(uiStack.length) history.back();
  }
}, true);
// --- 드래그-추종 바텀시트/사이드패널(사용자 요구, #9-2/3) — 지금까지는 탭으로만
// 스냅 열림/닫힘 했는데, 손가락을 따라 실시간으로 따라오다가 놓으면 스냅되는 진짜
// 드래그 제스처를 원함(표준 바텀시트/사이드드로어 패턴). 축(세로/가로)·크기 계산만
// 다르고 나머지(추적→스냅→트랜지션 on/off→탭 오인 방지)는 동일해 공유 헬퍼로 뽑음.
// "이동 거리 50% 넘으면 열림, 아니면 원위치" 정도로 단순하게(TODO 권장 수준).
let dragSheetJustDragged = null;   // 드래그 직후 같은 타깃에서 발생하는 click 1회를 흡수(탭 오인 방지)
document.addEventListener('click', e=>{
  if(dragSheetJustDragged && (e.target===dragSheetJustDragged || dragSheetJustDragged.contains(e.target))){
    e.stopPropagation(); e.preventDefault(); dragSheetJustDragged=null;
  }
}, true);
function attachDragSheet(el, opts){
  // opts: axis:'x'|'y', enabled()->bool, getMetrics()->임의 객체, onMove(delta,metrics), onEnd(delta,metrics)
  let startX=0, startY=0, dragging=false, metrics=null, down=false;
  el.addEventListener('pointerdown', e=>{
    if(!mobileMQ.matches || (opts.enabled && !opts.enabled())) return;
    startX=e.clientX; startY=e.clientY; dragging=false; metrics=null; down=true;
  });
  el.addEventListener('pointermove', e=>{
    // down 가드(2026-07-22 수정): pointerdown 없이 들어온 pointermove(예: 클릭 전
    // 커서 이동)를 startX/Y 기본값(0,0) 기준 델타로 계산하면 큰 값이 나와 탭 하나가
    // 드래그로 오인되는 버그가 있었다(el.style.height/transition 이 그대로 눌어붙어
    // listopen 이 되어도 실제 높이가 안 바뀜) — down 이 true(실제 눌림 중)일 때만 처리.
    if(!down || !mobileMQ.matches || (opts.enabled && !opts.enabled())) return;
    const d = opts.axis==='x' ? e.clientX-startX : e.clientY-startY;
    if(!dragging){
      if(Math.abs(d) < 8) return;    // 작은 흔들림은 탭으로 취급(오드래그 방지)
      dragging = true; metrics = opts.getMetrics(); el.style.transition='none';
    }
    e.preventDefault();
    opts.onMove(d, metrics);
  }, {passive:false});
  window.addEventListener('pointerup', e=>{
    down = false;
    if(!dragging) return;
    const d = opts.axis==='x' ? e.clientX-startX : e.clientY-startY;
    dragging = false; el.style.transition='';
    opts.onEnd(d, metrics);
    metrics = null;
    dragSheetJustDragged = el;
    setTimeout(()=>{ if(dragSheetJustDragged===el) dragSheetJustDragged=null; }, 0);
  });
}
// 목록(#docs) 세로 드래그 — collapsed 상태에서만(펼친 뒤엔 그래프 탭/뒤로가기로 닫는 기존
// 동선 유지, 목록 내부는 그때부터 자체 스크롤). 높이를 손가락에 맞춰 실시간 갱신하다가
// 놓으면 50% 기준으로 완전히 펼치거나 원위치 — #wrap 기준 %(38%/79%)를 px 로 환산해 계산.
attachDragSheet(document.getElementById('docs'), {
  axis:'y',
  enabled: () => !document.body.classList.contains('listopen'),
  getMetrics(){
    const wrapH = document.getElementById('wrap').getBoundingClientRect().height;
    return { collapsedPx: wrapH*0.38, openPx: wrapH*0.79,
             startH: document.getElementById('docs').getBoundingClientRect().height };
  },
  onMove(dy, m){   // dy>0 = 손가락이 아래로(줄어듦), dy<0 = 위로(늘어남)
    const h = Math.min(m.openPx, Math.max(m.collapsedPx, m.startH - dy));
    document.getElementById('docs').style.height = h+'px';
  },
  onEnd(dy, m){
    document.getElementById('docs').style.height = '';   // 인라인 제거 → CSS 클래스 기준으로 복귀
    const h = Math.min(m.openPx, Math.max(m.collapsedPx, m.startH - dy));
    const ratio = (h - m.collapsedPx) / (m.openPx - m.collapsedPx);
    if(ratio > 0.5){ pushUIState('list'); document.body.classList.add('listopen'); }
  }
});
// 목록이 펼쳐진(listopen) 뒤 #draghandle 을 잡고 끌어내려 다시 접기(사용자 제보,
// 2026-07-22): 위 #docs 드래그는 listopen 이 되면 꺼지므로(내부 리스트 자체 스크롤을
// 살려주려고) 그 상태에서 손잡이를 잡고 내리면 우리 코드가 아예 반응을 안 해 터치가
// 그대로 브라우저 기본 제스처(pull-to-refresh)로 넘어가 페이지가 새로고침되던 버그.
// #draghandle 은 리스트 아이템을 포함하지 않는 작은 전용 영역이라 여기만 별도로
// listopen 중에도 활성화해도 목록 스크롤과 충돌하지 않는다.
attachDragSheet(document.getElementById('draghandle'), {
  axis:'y',
  enabled: () => document.body.classList.contains('listopen'),
  getMetrics(){
    const wrapH = document.getElementById('wrap').getBoundingClientRect().height;
    return { collapsedPx: wrapH*0.38, openPx: wrapH*0.79,
             startH: document.getElementById('docs').getBoundingClientRect().height };
  },
  onMove(dy, m){
    const h = Math.min(m.openPx, Math.max(m.collapsedPx, m.startH - dy));
    document.getElementById('docs').style.height = h+'px';
  },
  onEnd(dy, m){
    document.getElementById('docs').style.height = '';
    const h = Math.min(m.openPx, Math.max(m.collapsedPx, m.startH - dy));
    const ratio = (h - m.collapsedPx) / (m.openPx - m.collapsedPx);
    if(ratio <= 0.5) closeTopUIState('list');
  }
});
// 패널(#panel) 가로 드래그 — 걸침(peek 이거나 아무것도 선택 안 한 기본 상태) 에서
// 왼쪽으로 끌면 열리고, reading(전체 오픈) 상태에서 오른쪽으로 끌면 다시 걸침으로.
// translateX 를 손가락에 맞춰 실시간 갱신. 기본 상태도 항상 26px 걸쳐 보이므로
// (2026-07-24, 손잡이 상시 노출) enabled 제한 없이 모바일에서 항상 동작.
attachDragSheet(document.getElementById('panel'), {
  axis:'x',
  getMetrics(){
    const W = document.getElementById('panel').getBoundingClientRect().width;
    const isReading = document.body.classList.contains('reading');
    return { W, start: isReading ? 0 : (W-26) };
  },
  onMove(dx, m){
    const t = Math.min(m.W, Math.max(0, m.start + dx));
    document.getElementById('panel').style.transform = 'translateX('+t+'px)';
  },
  onEnd(dx, m){
    document.getElementById('panel').style.transform = '';   // 인라인 제거 → CSS 클래스 기준 복귀
    const t = Math.min(m.W, Math.max(0, m.start + dx));
    const wasReading = document.body.classList.contains('reading');
    if(t < m.W*0.5){    // 절반 이상 열림 쪽 — 완전히 펼침
      if(!wasReading) openPeekPanel();
    } else {            // 절반 이상 닫힘 쪽 — 걸침 상태로(CSS 기본값이 이미 26px 걸침이라
                        // reading 만 벗기면 됨 — revealedDocId 없는 idle 상태를 'peek'로
                        // 잘못 표시하지 않게 peek 클래스는 안 건드린다)
      if(wasReading) document.body.classList.remove('reading');
    }
  }
});
// 휠 줌 평탄화: vis 기본 줌은 wheel deltaY '크기'에 비례해 한 이벤트로 여러 단계 점프한다.
// Mac 트랙패드/매직마우스는 한 제스처에 큰 deltaY 를 모멘텀으로 연속 발사 → "한꺼번에 확대"
// (사용자 보고). 그래서 zoomView:false 로 두고, 스크롤 '거리'를 누적해 일정량(STEP_DELTA)마다
// 한 스텝씩, 프레임당 최대 1스텝만 포인터(커서) 중심으로 적용한다 — 플랫폼 무관하게 균일하고
// 모멘텀 폭주는 시간축으로 펼쳐져 점프가 사라진다.
function setupWheelZoom(){
  const cont=document.getElementById('net');
  const STEP_DELTA=80, FACTOR=1.12, MIN=0.05, MAX=5, CAP=STEP_DELTA*3;
  let accum=0, px=0, py=0, raf=0;
  function zoomAt(factor){  // 커서 아래 그래프 좌표가 고정되도록 scale 변경 후 view 보정
    const rect=cont.getBoundingClientRect(), dom={x:px-rect.left, y:py-rect.top};
    const before=net.DOMtoCanvas(dom);
    const scale=Math.max(MIN, Math.min(MAX, net.getScale()*factor));
    const vp=net.getViewPosition();
    net.moveTo({scale:scale, animation:false});
    const after=net.DOMtoCanvas(dom);
    net.moveTo({position:{x:vp.x+(before.x-after.x), y:vp.y+(before.y-after.y)}, scale:scale, animation:false});
  }
  function flush(){
    raf=0;
    if(Math.abs(accum)>=STEP_DELTA){           // 프레임당 한 스텝만 → 모멘텀을 시간축으로 펼침
      const expand=accum<0;                    // 위로 스크롤(deltaY<0)=확대
      accum+=expand?STEP_DELTA:-STEP_DELTA;
      zoomAt(expand?FACTOR:1/FACTOR);
    }
    if(Math.abs(accum)>=STEP_DELTA) raf=requestAnimationFrame(flush);  // 남으면 다음 프레임
  }
  cont.addEventListener('wheel', e=>{
    if(mobileMQ.matches && !e.ctrlKey) return; // 모바일 세로 스크롤(pan-y) 보존
    e.preventDefault();
    accum+=e.deltaY; px=e.clientX; py=e.clientY;
    accum=Math.max(-CAP, Math.min(CAP, accum)); // 모멘텀 폭주 상한(손 떼면 곧 멈춤)
    if(!raf) raf=requestAnimationFrame(flush);
  }, {passive:false});
}
// 모바일 +/- 줌 버튼 — 화면 중심 기준(커서 개념이 없으니 뷰 중심 고정, moveTo 가 position
// 생략 시 현재 중심을 유지한다).
function zoomBtn(dir){
  if(!net) return;
  const scale=Math.max(0.05, Math.min(5, net.getScale()*(dir>0?1.25:1/1.25)));
  net.moveTo({scale:scale, animation:{duration:150}});
}

fetch('graph').then(r=>r.json()).then(d=>{
  allNodes = new vis.DataSet(d.nodes);
  allEdges = new vis.DataSet(d.edges);
  const sl = document.getElementById('fslider'); sl.max = d.stats.max_degree; sl.value = 0;
  allTypes=[...new Set(d.nodes.map(n=>n.group))].sort();
  allRelTypes=[...new Set(d.edges.map(e=>e.label).filter(Boolean))].sort();
  renderLegend();
  const th=T();
  // 선택/강조 노드는 테마별 강조 테두리(다크=흰색, 라이트=파랑) — dim 만으론 안 띄어서(피드백).
  const opts = {
    nodes:{shape:'dot',size:14,font:{color:th.nodeFont,size:13},borderWidth:1,borderWidthSelected:3},
    edges:{color:{color:th.edge,highlight:th.edgeHi},font:{color:th.nodeFont,size:10},smooth:false},
    groups:buildGroups(),
    physics:{stabilization:{iterations:200},barnesHut:{gravitationalConstant:-8000,springLength:120}},
    // 휠 줌은 커스텀(setupWheelZoom)으로 — vis 기본은 deltaY 크기 비례라 Mac 모멘텀에서
    // 한 번에 여러 단계 점프(사용자 보고). hideEdgesOnZoom/hideEdgesOnDrag: 사용자
    // 제보(2026-07-24, 폰에서 확대 시 저프레임)로 실측(605노드/659엣지, CPU 4x 스로틀,
    // CDP 프로파일) — barnesHut 물리 상시 실행이 주범이었고(위 stabilizationIterationsDone
    // 참고, 껐더니 평균 5.4→21.7 FPS) 그다음으로 줌 중 매 프레임 엣지 660개를 전부
    // 다시 그리는 비용이 남아있어(옵션 켜기 전/후 A-B 비교, 확대 스크립트 20회 반복
    // 실측) 평균 21.7→28.5 FPS, 최악 프레임 2283ms→667ms 로 추가 개선 확인.
    interaction:{hover:true,tooltipDelay:120,multiselect:true,zoomView:false,
      hideEdgesOnZoom:true,hideEdgesOnDrag:true}
  };
  net = new vis.Network(document.getElementById('net'), {nodes:allNodes, edges:allEdges}, opts);
  // 사용자 제보(2026-07-24, 폰에서 확대 시 프레임이 한자리수): barnesHut 물리 시뮬레이션이
  // 초기 안정화(stabilization) 이후에도 physics:true 라 매 애니메이션 프레임마다 계속
  // 돌고 있었다 — 실측(로컬 mock DB 605노드/659엣지 + Chrome CPU 4x 스로틀 + CDP
  // CPU 프로파일)으로 확인: 확대 중 평균 5.4 FPS, 샘플링된 CPU 시간의 ~32%가
  // _calculateForces/_getForceContributions/_placeInTree(barnesHut 반발력 계산)에 쓰이고
  // 있었다. 안정화가 끝나면 물리를 꺼서 그 비용을 없앤다 — 노드 드래그는 physics 와
  // 무관하게 그대로 동작(드래그는 직접 좌표를 옮길 뿐 이웃 노드 재배치가 필요 없다면
  // 오히려 덜 산만해서 UX 개선). 검색 결과 뭉치기(clusterMatches)만 예외적으로 물리가
  // 필요해 그쪽에서 임시로 다시 켰다 끈다(아래 clusterMatches/unclusterEdges 참고).
  net.once('stabilizationIterationsDone', () => net.setOptions({physics:false}));
  // 모바일/세로스택: vis 가 생성 시점의 #net 높이로 캔버스 backing store 를 잡아 레이아웃이
  // 늦게 확정되면 캔버스가 상단 일부만 차지(이슈1). 레이아웃 확정 후 컨테이너 크기로 강제
  // 재설정 + 회전/리사이즈에도 다시 맞춘다.
  requestAnimationFrame(()=>{ relayout(); setTimeout(relayout, 300); });
  applyTouchMode();   // hammer 가 박은 touch-action:none 을 모바일에선 pan-y 로 덮어씀
  setupWheelZoom();   // 휠 줌 평탄화(Mac 모멘텀 대응) — vis 기본 zoomView 대체
  // 모바일 팬 리셋 방어(이슈: 문서선택→fit() 애니메이션 직후 mobileScrollTo 의 페이지
  // 스무스스크롤 중 주소창 접힘→뷰포트 리사이즈→relayout()이 fit 애니메이션 도중 끼어들어
  // 카메라가 깨진 채로 다음 터치팬이 시작되는 것으로 추정). 드래그/애니메이션 중엔 relayout
  // 을 미루고, 크기 변화가 실제로 없으면 아예 스킵(불필요한 setSize+redraw 로 인한 churn 방지).
  // animationFinished 에만 기대면 발화 안 되는 경우(실측: 0개 노드 fit 등) netBusy 가 영원히
  // true 로 굳어 relayout 이 죽는 더 나쁜 회귀가 됨 → 항상 풀리는 타임아웃을 안전망으로 병행.
  let busyTimer = null;
  function markBusy(ms){ netBusy = true; clearTimeout(busyTimer); busyTimer = setTimeout(()=>{netBusy=false;}, ms); }
  // dragStart 도 markBusy 로 — 기존엔 여기만 안전망 타임아웃 없이 dragEnd 만 믿었는데,
  // 이 앱은 모바일에서 touch-action:pan-y 로 세로 스와이프를 일부러 페이지 스크롤에
  // 양보한다(applyTouchMode) — 즉 vis(hammer) 의 드래그가 dragEnd 없이 브라우저 스크롤에
  // 가로채여 중간에 끊기는 게 정상 시나리오. 이러면 netBusy 가 영원히 true 로 굳어 그
  // 뒤로는 문서 선택/해제 등 어떤 리사이즈도 캔버스에 반영 안 됨(사용자 제보: 문서
  // 선택→해제해도 그래프 영역이 안 돌아옴). 드래그 중엔 어차피 #netwrap 크기가 안
  // 바뀌므로 짧은 타임아웃으로 풀어도 안전(재측정해도 크기 그대로면 relayout 자체가
  // no-op).
  net.on('dragStart', () => markBusy(2000));
  net.on('dragEnd', () => { netBusy = false; clearTimeout(busyTimer); });
  net.on('animationFinished', () => { netBusy = false; clearTimeout(busyTimer); clearFitBusy(); });
  // #9-4(b): fit() 이 카메라를 애니메이션으로 옮기는 도중 사용자가 드래그팬을 시작하면
  // vis 내부에서 애니메이션과 사용자 입력이 겹쳐 카메라가 엉뚱한 위치로 튀는 것으로
  // 추정(실기기 재현은 못 함 — 아래 markBusy 의 dragStart 처럼 타이밍 레이스라 로컬
  // Chromium 으로 재현이 어려운 부류). fit/moveTo(애니메이션) 진행 중엔 짧게
  // dragView 를 꺼서 애니메이션과 사용자 팬이 아예 겹치지 않게 하는 방어책 — 캔버스가
  // 실제로 움직이는 700ms 짧은 구간뿐이라 체감상 팬이 씹히는 느낌은 거의 없다.
  let fitBusyTimer=null;
  function clearFitBusy(){ clearTimeout(fitBusyTimer); fitBusyTimer=null; net.setOptions({interaction:{dragView:true}}); }
  function markFitBusy(ms){ net.setOptions({interaction:{dragView:false}}); clearTimeout(fitBusyTimer); fitBusyTimer=setTimeout(clearFitBusy, ms); }
  const _fit = net.fit.bind(net), _moveTo = net.moveTo.bind(net);
  net.fit = (opts) => { markBusy(700); markFitBusy(700); return _fit(opts); };
  net.moveTo = (opts) => { if(opts && opts.animation){ markBusy(700); markFitBusy(700); } return _moveTo(opts); };
  net.on('click', p => {
    if(!p.nodes.length){
      // 빈 캔버스 클릭: inspect 만 해제하고 검색(라벨/의미) 강조 선택은 유지(이슈4).
      // vis 가 내부적으로 선택을 비우므로 그 뒤에 검색 선택을 다시 적용한다.
      selectedNodeId=null;
      if(highlightSet && highlightSet.size) setTimeout(restoreSelection, 0);
      return;
    }
    const id=p.nodes[0], ev=p.event.srcEvent;
    if(pathMode){ pickPathNode(id); return; }                // 경로 모드: 클릭으로 시작/끝 노드 지정
    if(ev && (ev.ctrlKey||ev.metaKey)){ toggleSynth(id); }   // Ctrl/Cmd+클릭 = 종합 수집(선택과 분리)
    else { selectedNodeId=id; loadNode(id); mobileScrollTo('panel'); }  // 일반 클릭/탭 = 상세 inspect
  });
  // hover → 1.5초 뒤 마우스 위치에 작은 요약 팝업(우측 패널은 안 건드림 — 난잡함 해소, 사용자 요구).
  // 우측 패널은 클릭(inspect)일 때만 바뀐다 → hover 가 패널/선택을 흔들지 않아 복원 로직도 불필요.
  net.on('hoverNode', p => {
    if(mobileMQ.matches) return;   // 터치엔 대응하는 blurNode 가 안 올 수 있음 — 데스크톱 hover 전용(2026-07-24)
    clearTimeout(hoverTimer);
    hoverTimer=setTimeout(()=>showNodePop(p.node), 1500); });
  net.on('blurNode', () => { clearTimeout(hoverTimer); hideNodePop(); });
  net.on('dragStart', hideNodePop);   // 드래그/줌 중엔 팝업 숨김(커서를 따라다니지 않게)
  net.on('zoom', hideNodePop);
  applyView();
});

// 검색 강조(highlightSet)와 inspect(selectedNodeId)를 vis 시각 선택으로 복원.
// hover/blur·빈클릭으로 vis 가 선택을 비워도 검색 결과 선택이 사라지지 않게 한다(이슈4).
function restoreSelection(){
  if(!net) return;
  let ids = highlightSet ? [...highlightSet] : [];
  if(selectedNodeId && !ids.includes(selectedNodeId)) ids = ids.concat(selectedNodeId);
  if(ids.length) net.selectNodes(ids); else net.unselectAll();
}
// 검색·inspect 모두 해제(ESC / 검색창 비우기). synthSet(종합 수집)은 보존.
function clearSelections(){
  highlightSet=null; selectedNodeId=null;
  const q=document.getElementById('q'); if(q) q.value='';
  if(net) net.unselectAll();
  unclusterEdges();   // 검색으로 뭉치게 한 임시 spring 엣지 제거 → 물리가 원래대로
  applyView();
}
document.addEventListener('keydown', e=>{ if(e.key!=='Escape') return;
  const r=document.getElementById('reader');
  if(r && r.classList.contains('open')){ closeReader(); return; }   // 팝업 먼저 닫기
  clearSelections(); });

function loadNode(id){
  if(net) net.selectNodes([id]);   // 클릭 inspect — hover 는 더 이상 패널을 안 쓴다(팝업으로 분리)
  openMobilePanelUI();   // 패널이 닫혀있었으면 뒤로가기 스택에 쌓기(그래프 노드 탭 진입 경로)
  fetch('node?id='+encodeURIComponent(id)).then(r=>r.json()).then(renderPanel);
}
function renderPanel(d){
  if(!d || d.error){ panel.innerHTML='<p class=hint>노드를 찾을 수 없습니다.</p>'; return; }
  const inSet = synthSet.has(d.id);
  // 문서를 고른 상태에서 노드로 들어왔으면 문서 패널로 한 번에 돌아갈 링크.
  let h = activeDoc ? '<span class=backlink onclick="loadDocPanel(activeDoc)">← 문서로 돌아가기</span>' : '';
  h+='<h2>'+esc(d.name)+' <small>'+esc(d.type)+(d.provisional?' ⚠️provisional':'')+'</small></h2>';
  // readonly(/webro) 세션은 종합(/synthesize)이 서버에서 막혀있어 버튼 자체를 안 그림.
  if(!READONLY) h+='<button class="sec" onclick="addToSynth(\\''+d.id+'\\')">'+(inSet?'✓ 종합 목록에 있음':'➕ 종합에 추가')+'</button>';
  if(d.aliases.length) h+='<p class=al>별칭: '+d.aliases.map(esc).join(', ')+'</p>';
  if(d.observations.length){ h+='<h3>관찰 · 주장</h3><ul>'+
    d.observations.map(o=>'<li>'+esc(o)+'</li>').join('')+'</ul>'; }
  if(d.documents.length){ h+='<h3>출처 문서 ('+d.documents.length+')</h3>';
    // 설명(summary) → 📖 읽기(중앙 팝업, 마크다운·이미지) → 원문 링크 순(사용자 요구).
    d.documents.forEach(dc=>{ h+='<div class=doc><b>'+esc(dc.title)+'</b>'+
      (dc.summary?'<p>'+esc(dc.summary)+'</p>':'')+
      ((dc.detail||dc.summary)?'<button class=readbtn onclick="openReader(\\''+dc.id+'\\')">📖 크게 읽기</button>':'')+
      (dc.url?'<p class=src><a href="'+esc(dc.url)+'" target=_blank rel=noopener>↗ 원문 열기</a></p>':'')+
      '</div>'; }); }
  if(d.neighbors.length){ h+='<h3>연결 ('+d.neighbors.length+')</h3><ul>';
    d.neighbors.forEach(n=>{ const ar=n.dir=='out'?'→':'←';
      h+='<li><span class=rel>'+esc(n.rel)+'</span> '+ar+
         ' <a href="#" onclick="loadNode(\\''+n.id+'\\');return false">'+esc(n.name)+
         '</a> <small>'+esc(n.type)+'</small></li>'; }); h+='</ul>'; }
  // 맥락 확장 조사 — 읽다가 더 알고 싶은 키워드/문장을 지금 맥락으로 조사해 그래프 확장.
  // readonly 는 /research 도 서버에서 막혀있어 입력창·버튼 자체를 안 그림.
  if(!READONLY) h+='<h3>🔬 더 알아보기</h3>'+
    '<div class=research><input id="rq" placeholder="더 알고 싶은 키워드/문장" '+
    'onkeydown="if(event.key===\\'Enter\\')doResearch()"/>'+
    '<button onclick="doResearch()">조사</button></div>'+
    '<p class=al>지금 보는 맥락에 맞춰 웹 조사 → 맥락 일치·품질 통과 시 그래프에 추가됩니다.</p>';
  panel.innerHTML=h;
}

// --- 맥락 확장 조사: 조사(grounding)→판정 게이트→통과 시 그래프 적재(서버) ---
// 서버가 NDJSON 스트림으로 진행 이벤트({stage,msg})를 흘리고 마지막 줄이
// {done:true, result:{...}} — 마냥 기다리지 않고 단계·rate limit 상황을 실시간 표시(피드백).
async function doResearch(){
  const q=((document.getElementById('rq')||{}).value||'').trim();
  if(!q){ alert('조사할 키워드/문장을 입력하세요.'); return; }
  if(!selectedNodeId && !activeDoc){ alert('노드를 선택하거나 문서를 연 뒤 조사하세요.'); return; }
  const backId=selectedNodeId;
  panel.innerHTML='<h2>🔬 조사: '+esc(q)+'</h2><p class="al" id="relapsed">시작…</p><ul id="rprog"></ul>';
  mobileScrollTo('panel');
  const t0=Date.now();
  const timer=setInterval(()=>{ const el=document.getElementById('relapsed');
    if(el) el.textContent='⏱ 경과 '+Math.round((Date.now()-t0)/1000)+'s'; else clearInterval(timer); },1000);
  let result=null;
  try{
    const r=await fetch('research',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({query:q, node_id:selectedNodeId, doc_id:activeDoc})});
    if(r.status===401||r.status===404){ clearInterval(timer); setAuth('idle');
      panel.innerHTML='<p class=hint>세션 만료 — 텔레그램 /web 으로 다시 접속하세요</p>'; return; }
    const reader=r.body.getReader(), dec=new TextDecoder(); let buf='';
    while(true){
      const {done,value}=await reader.read(); if(done) break;
      buf+=dec.decode(value,{stream:true});
      let i; while((i=buf.indexOf('\\n'))>=0){
        const line=buf.slice(0,i).trim(); buf=buf.slice(i+1);
        if(!line) continue;
        let ev; try{ ev=JSON.parse(line); }catch(_){ continue; }
        if(ev.done){ result=ev.result; continue; }
        const ul=document.getElementById('rprog');
        if(ul){ const li=document.createElement('li'); li.className='al';
          li.textContent=(ev.stage==='llm'?'⏳ ':'• ')+(ev.msg||'');
          ul.appendChild(li); }
      }
    }
  }catch(e){ clearInterval(timer);
    panel.innerHTML='<p class=hint>요청 실패: '+esc(String(e))+'</p>'; return; }
  clearInterval(timer);
  if(!result){ panel.innerHTML='<p class=hint>응답이 끊겼습니다 — 잠시 후 다시 시도하세요.</p>'; return; }
  renderResearchResult(result, backId);
}
function renderResearchResult(d, backId){
  if(d.error){ panel.innerHTML='<p class=hint>오류: '+esc(d.error)+'</p>'; return; }
  let h='<h2>🔬 조사: '+esc(d.query)+(d.context_focus?' <small>'+esc(d.context_focus)+' 맥락</small>':'')+'</h2>';
  h+='<p class=al>'+(d.added?'✅ ':'⏸ ')+esc(d.verdict||'')+
    ' <span class=meter>맥락일치 '+(d.relevance!=null?(+d.relevance).toFixed(2):'-')+
    ' · 품질 '+(d.quality!=null?(+d.quality).toFixed(2):'-')+'</span></p>';
  if(d.interpretation) h+='<p class=al>해석: '+esc(d.interpretation)+'</p>';
  if(d.report) h+='<div class=synth>'+esc(d.report)+'</div>';
  if((d.sources||[]).length){ h+='<h3>출처</h3><ul>'+d.sources.map(s=>
    '<li><a href="'+esc(s.url)+'" target=_blank>'+esc(s.title||s.url)+'</a></li>').join('')+'</ul>'; }
  if(d.added && d.ingest){
    h+='<p class=al>그래프 반영: 신규 '+d.ingest.entities_created+' · 기존연결 '+
      d.ingest.entities_linked+' · 관계 '+d.ingest.relations_added+'</p>';
    refreshGraph();
  } else if(d.reason){ h+='<p class=al>판정: '+esc(d.reason)+'</p>'; }
  if(backId) h+='<p><a href="#" onclick="loadNode(\\''+backId+'\\');return false">← 노드로 돌아가기</a></p>';
  panel.innerHTML=h;
}

// --- 웹 적재: URL/텍스트를 그래프에 적재(서버 /ingest-stream, /research 와 동일 NDJSON 스트리밍) ---
// 텔레그램 DM 과 같은 통로(svc.ingest, source='web') — 관련 링크 1홉 자동확장도 동일하게 동작.
function openIngest(){
  panel.innerHTML='<h2>➕ 자료 적재</h2>'+
    '<p class=al>URL · 메모 텍스트 · "제목 URL" 공유문구를 붙여넣고 보내면 적재됩니다. '+
    '관련 링크는 백그라운드에서 자동으로 따라가 함께 쌓입니다.</p>'+
    '<textarea id="ingin" rows="4" style="width:100%;box-sizing:border-box" '+
    'placeholder="https://example.com/article   또는   메모 텍스트"></textarea>'+
    '<div style="margin:.5em 0"><button onclick="runIngest()">보내기</button></div>';
  mobileScrollTo('panel');
  const ta=document.getElementById('ingin'); if(ta) ta.focus();
}
async function runIngest(){
  const ta=document.getElementById('ingin');
  const payload=((ta||{}).value||'').trim();
  if(!payload){ alert('적재할 URL 또는 텍스트를 입력하세요.'); return; }
  panel.innerHTML='<h2>➕ 적재 중</h2><p class="al" id="ielapsed">시작…</p><ul id="iprog"></ul>';
  mobileScrollTo('panel');
  const t0=Date.now();
  const timer=setInterval(()=>{ const el=document.getElementById('ielapsed');
    if(el) el.textContent='⏱ 경과 '+Math.round((Date.now()-t0)/1000)+'s'; else clearInterval(timer); },1000);
  let result=null;
  try{
    const r=await fetch('ingest-stream',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({payload:payload})});
    if(r.status===401||r.status===404){ clearInterval(timer); setAuth('idle');
      panel.innerHTML='<p class=hint>세션 만료 — 텔레그램 /web 으로 다시 접속하세요</p>'; return; }
    const reader=r.body.getReader(), dec=new TextDecoder(); let buf='';
    while(true){
      const {done,value}=await reader.read(); if(done) break;
      buf+=dec.decode(value,{stream:true});
      let i; while((i=buf.indexOf('\\n'))>=0){
        const line=buf.slice(0,i).trim(); buf=buf.slice(i+1);
        if(!line) continue;
        let ev; try{ ev=JSON.parse(line); }catch(_){ continue; }
        if(ev.done){ result=ev.result; continue; }
        const ul=document.getElementById('iprog');
        if(ul){ const li=document.createElement('li'); li.className='al';
          li.textContent='• '+(ev.msg||''); ul.appendChild(li); }
      }
    }
  }catch(e){ clearInterval(timer);
    panel.innerHTML='<p class=hint>요청 실패: '+esc(String(e))+'</p>'; return; }
  clearInterval(timer);
  if(!result){ panel.innerHTML='<p class=hint>응답이 끊겼습니다 — 잠시 후 다시 시도하세요.</p>'; return; }
  renderIngestResult(result);
}
function renderIngestResult(d){
  if(d.error){ panel.innerHTML='<h2>➕ 적재</h2><p class=hint>오류: '+esc(d.error)+'</p>'+
    '<p class=al>원본은 보관되어 자동복구(recover) 대상이 됩니다.</p>'; return; }
  let h='<h2>'+(d.duplicate?'♻️ 이미 있는 자료':(d.updated?'🔄 내용 갱신':'✅ 적재 완료'))+'</h2>';
  h+='<p class=al><b>'+esc(d.title||d.document_id||'(제목 없음)')+'</b>'+(d.partial?' <small>⚠️ 부분 처리</small>':'')+'</p>';
  if(!d.duplicate) h+='<p class=al>노드 신규 '+(d.entities_created||0)+' · 기존연결 '+
    (d.entities_linked||0)+' · 관계 '+(d.relations_added||0)+'</p>';
  if(d.summary) h+='<div class=synth>'+esc(d.summary)+'</div>';
  if(d.document_id) h+='<p><a href="#" onclick="selectDoc(\\''+d.document_id+'\\');return false">문서 보기 →</a></p>';
  panel.innerHTML=h;
  refreshGraph();   // 신규 노드/엣지·문서목록 즉시 반영(새로고침 없이)
}

// --- 중복 문서 정리: 근사중복 클러스터를 찾아(/dedup) 유지문서를 골라 병합(/dedup/merge) ---
// 병합 직전 정본은 서버가 자동 백업한다(파괴적 작업 안전장치). keeper(유지) 외 문서는
// 참조(엔티티/관계 sources 등)를 keeper 로 재배치한 뒤 삭제 → 데이터 보존.
let dedupClusters=[];
async function openDedup(){
  panel.innerHTML='<h2>♻️ 중복 문서 정리</h2><p class="al">근사 중복 검사 중… '+
    '<small>(문서가 많으면 잠시 걸립니다)</small></p>';
  mobileScrollTo('panel');
  let d;
  try{
    const r=await fetch('dedup');
    if(r.status===401||r.status===404){ setAuth('idle');
      panel.innerHTML='<p class=hint>세션 만료 — 텔레그램 /web 으로 다시 접속하세요</p>'; return; }
    d=await r.json();
  }catch(e){ panel.innerHTML='<h2>♻️ 중복 문서 정리</h2><p class=hint>검사 실패: '+esc(String(e))+'</p>'; return; }
  renderDedup(d);
}
function renderDedup(d){
  if(d.error){ panel.innerHTML='<h2>♻️ 중복 문서 정리</h2><p class=hint>오류: '+esc(d.error)+'</p>'; return; }
  dedupClusters=d.clusters||[];
  let h='<h2>♻️ 중복 문서 정리</h2>';
  h+='<p class=al>검사 '+(d.documents||0)+'개 · 근사중복 클러스터 <b>'+dedupClusters.length+'</b>개</p>';
  if(!dedupClusters.length){ h+='<p class=al>근사 중복 문서가 없습니다. ✅</p>'; panel.innerHTML=h; return; }
  h+='<p class=al><small>유지할 문서를 고르고 병합하세요. 나머지는 유지문서로 합쳐지고 참조는 보존됩니다(병합 전 자동 백업).</small></p>';
  dedupClusters.forEach((c,ci)=>{
    h+='<div class=doc><p class=al>유사도 '+(c.score!=null?(+c.score).toFixed(2):'-')+'</p>';
    c.docs.forEach(dc=>{
      h+='<label style="display:block;margin:.3em 0">'+
        '<input type=radio name="keep'+ci+'" value="'+esc(dc.id)+'"'+(dc.id===c.keeper?' checked':'')+'> '+
        '<b>'+esc(dc.title||'(제목 없음)')+'</b> <small class=al>'+(dc.len||0)+'자</small>'+
        (dc.url?'<br><small class=al style="margin-left:1.5em;word-break:break-all">'+esc(dc.url)+'</small>':'')+
        '</label>';
    });
    h+='<button class=readbtn onclick="runDedupMerge('+ci+')">선택한 문서로 병합</button></div>';
  });
  panel.innerHTML=h;
}
async function runDedupMerge(ci){
  const c=dedupClusters[ci]; if(!c) return;
  const sel=document.querySelector('input[name="keep'+ci+'"]:checked');
  const keeper=sel?sel.value:c.keeper;
  const losers=c.docs.map(x=>x.id).filter(id=>id!==keeper);
  if(!losers.length){ alert('합칠 문서가 없습니다.'); return; }
  if(!confirm(losers.length+'개 문서를 유지문서로 합칩니다. 계속할까요?\\n(병합 전 자동 백업됩니다)')) return;
  panel.innerHTML='<h2>♻️ 병합 중…</h2><p class=al>백업 후 참조 재배치 중…</p>';
  try{
    const r=await fetch('dedup/merge',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({keeper:keeper, losers:losers})});
    if(r.status===401||r.status===404){ setAuth('idle');
      panel.innerHTML='<p class=hint>세션 만료 — 텔레그램 /web 으로 다시 접속하세요</p>'; return; }
    const d=await r.json();
    if(d.error){ panel.innerHTML='<h2>♻️ 병합</h2><p class=hint>오류: '+esc(d.error)+'</p>'; return; }
    let h='<h2>✅ 병합 완료</h2>';
    h+='<p class=al>문서 '+(d.deleted||0)+'개를 합쳤습니다. 엔티티 '+(d.entities_repointed||0)+
      ' · 관계 '+(d.relations_repointed||0)+' 참조 재배치.</p>';
    if(d.backup) h+='<p class=al><small>백업: '+esc(d.backup)+'</small></p>';
    h+='<p><a href="#" onclick="openDedup();return false">← 다시 검사</a></p>';
    panel.innerHTML=h;
    refreshGraph();   // 문서목록·그래프(병합으로 줄어든 sources) 갱신
  }catch(e){ panel.innerHTML='<h2>♻️ 병합</h2><p class=hint>요청 실패: '+esc(String(e))+'</p>'; }
}

// 조사로 그래프가 늘어난 뒤 새로고침 없이 신규 노드/엣지·문서목록을 반영.
// 엣지 id 는 rowid 순 enumerate(append-only)라 기존 id 는 안정 — 신규만 add.
function refreshGraph(){
  fetch('graph').then(r=>r.json()).then(d=>{
    // applyView 와 동일한 이유(그래프가 클수록 개별 update() 호출이 선형으로 느려짐)로
    // 갱신분을 모아 한 번에 반영 — 신규 노드(add)는 기존 add 도 이미 배열을 받으므로 그대로.
    const changed=[], added=[];
    d.nodes.forEach(n=>{ if(allNodes.get(n.id))
      changed.push({id:n.id, degree:n.degree, sources:n.sources, obs:n.obs});
      else added.push(n); });
    if(changed.length) allNodes.update(changed);
    if(added.length) allNodes.add(added);
    d.edges.forEach(e=>{ if(!allEdges.get(e.id)) allEdges.add(e); });
    document.getElementById('fslider').max = d.stats.max_degree;
    applyView();
  });
  fetch('documents').then(r=>r.json()).then(d=>{ allDocs=d.documents||[];
    renderDocs(document.getElementById('docq').value); });
}

// 전체 리로드 없이 새 글 반영(사용자 요구) — 가벼운 주기 폴링. /stats 문서 개수만 확인하고
// 바뀐 경우에만 refreshGraph()(append-only 병합) 실행 — 안 바뀌면 아무 요청도 안 함.
let lastDocCount = null;
async function pollForUpdates(){
  try{
    const r = await fetch('stats');
    if(!r.ok) return;               // 401 등이면 조용히 다음 틱에 재시도
    const d = await r.json();
    if(lastDocCount===null){ lastDocCount = d.documents; return; }  // 최초 틱=기준값만 기록
    if(d.documents !== lastDocCount){ lastDocCount = d.documents; refreshGraph(); }
  }catch(e){ /* 네트워크 일시 오류 — 다음 틱에 재시도 */ }
}
setInterval(pollForUpdates, 25000);

// 단일 가시 규칙: degree(스케일)=hidden, 강조 필터(문서 선택 + 검색)=비매치 dim.
// 문서/라벨검색/의미검색이 모두 같은 강조 방식을 공유한다(시각 언어 통일).
// 범례 = 노드 타입 색(비클릭) + 관계 타입 토글 칩(클릭=필터). off 표시 타입은 그래프에서 숨김.
function renderLegend(){
  const nodeleg = allTypes.map(t=>
    '<span><i style="background:'+(TYPE_COLORS[t]||'#8b949e')+'"></i>'+esc(t)+'</span>').join('');
  let rel='';
  if(allRelTypes.length){
    rel = '<span class=lgsep>관계:</span> ' + allRelTypes.map((t,i)=>{
      const on = !relFilter || relFilter.has(t);
      return '<span class="reltog'+(on?'':' off')+'" onclick="toggleRel('+i+')" title="이 관계만/제외 토글">'+esc(t)+'</span>';
    }).join(' ');
  }
  document.getElementById('legendbar').innerHTML = nodeleg + rel;
}
function toggleRel(i){
  const t=allRelTypes[i]; if(t===undefined) return;
  if(!relFilter){ relFilter=new Set([t]); }            // 첫 클릭(진입): 찍은 관계만 표시
  else if(relFilter.has(t)){ relFilter.delete(t);      // 켜진 것 다시 끄기
    if(relFilter.size===0) relFilter=null; }           // 다 끄면 전체로 복귀(빈 화면 방지)
  else { relFilter.add(t);                             // 다른 관계도 추가로 보기(누적)
    if(relFilter.size===allRelTypes.length) relFilter=null; }  // 전부 켜지면 필터 해제(=전체)
  renderLegend(); applyView();
}
function nodeLabel(id){ const n=allNodes&&allNodes.get(id); return n?n.label:id; }

function setDeg(v){ curMinDeg=+v; document.getElementById('fmin').textContent=v; applyView(); }
// 노드/엣지 개수가 늘수록 클릭마다 체감 지연이 커지던 원인: 아래 두 루프가 예전엔
// DataSet.update() 를 노드/엣지마다 하나씩(수백~천 회) 개별 호출했다 — vis DataSet 은
// update 호출마다 change 이벤트를 쏘고 Network 가 그때마다 리드로우를 예약해, 그래프가
// 자랄수록(현재 엔티티 500+·관계 500+, 매일 증가) 선형으로 느려졌다. update() 는 배열을
// 통째로 넘기면 이벤트/리드로우가 1회로 묶인다 — 계산은 그대로 하되 실제 반영만 모아서
// 한 번에 한다(문서 선택·검색·필터·degree 슬라이더 전부 이 함수를 타므로 공통 개선).
function applyView(){
  if(!allNodes) return;
  let shown=0, emph=0;
  const th=T();
  const pathActive = !!(pathNodes && pathNodes.size);
  const hasFilter = activeDoc || highlightSet || pathActive;
  const nodeUpdates=[];
  allNodes.forEach(n=>{
    if(typeof n.id==='string' && n.id.indexOf('cl_')===0) return;  // 검색 중앙 앵커는 안 건드림(숨김 유지)
    if(n.degree < curMinDeg){ nodeUpdates.push({id:n.id, hidden:true}); return; }
    let match = true;
    if(pathActive){ match = pathNodes.has(n.id); }   // 경로 모드: 경로 노드만 강조(다른 필터보다 우선)
    else {
      if(activeDoc) match = match && (n.sources||[]).includes(activeDoc);
      if(highlightSet) match = match && highlightSet.has(n.id);  // 검색(라벨/의미) 강조 집합
    }
    // 강조(문서 선택·검색·경로) 매치 노드는 흰 굵은 테두리 — dim 만으론 안 띄어서(피드백).
    // 노드별 color 가 group 색을 덮으므로 background/highlight 를 같이 명시해 유지한다.
    const lit = hasFilter && match, c = TYPE_COLORS[n.group]||'#8b949e';
    nodeUpdates.push({id:n.id, hidden:false, opacity: match?1:DIM, borderWidth: lit?3:1,
      color:{background:c, border: lit?th.lit:th.nodeBorder,
             highlight:{background:c, border:th.lit}}});
    shown++; if(match) emph++;
  });
  if(nodeUpdates.length) allNodes.update(nodeUpdates);  // 1회 배치(개별 호출 대신)
  // 엣지 가시성은 방금 갱신된 노드 hidden 상태를 봐야 하므로 노드 배치 반영 뒤에 계산.
  const edgeUpdates=[];
  allEdges.forEach(e=>{
    if(typeof e.id==='string' && e.id.indexOf('cl_')===0) return;  // 임시 클러스터 spring 엣지는 안 건드림(물리 유지)
    const f=allNodes.get(e.from), t=allNodes.get(e.to);
    let visible = !!(f && t && !f.hidden && !t.hidden);
    if(relFilter && !relFilter.has(e.label)) visible=false;       // 관계 타입 필터
    const onPath = !!(pathEdges && pathEdges.has(e.id));          // 경로 엣지 강조
    edgeUpdates.push({id:e.id, hidden: !visible, width: onPath?4:1,
      color: onPath ? {color:th.lit, highlight:th.lit} : {color:th.edge, highlight:th.edgeHi}});
  });
  if(edgeUpdates.length) allEdges.update(edgeUpdates);  // 1회 배치
  document.getElementById('stat').innerHTML =
    '표시 <b>'+shown+'</b>/'+allNodes.length
    + (curMinDeg>0?' · 연결≥'+curMinDeg:'')
    + (relFilter?' · 관계 '+relFilter.size+'/'+allRelTypes.length:'')
    + (pathActive?' · 🔗경로 '+pathNodes.size+'노드':(hasFilter?' · 강조 '+emph+'개':''));
}

// --- 2노드 경로 하이라이트(전용 모드): 🔗 경로 → 시작/끝 노드 클릭 → 최단경로(BFS) 강조 ---
function togglePathMode(){
  pathMode=!pathMode; pathPicks=[];
  const b=document.getElementById('pathbtn'); if(b) b.classList.toggle('on', pathMode);
  if(pathMode) showPathHint();
  else { pathNodes=null; pathEdges=null; applyView(); panel.innerHTML=''; }
}
function showPathHint(){
  const n=pathPicks.length;
  panel.innerHTML='<h2>🔗 경로 찾기</h2><p class=al>'+
    (n===0?'시작 노드를 클릭하세요.':'끝 노드를 클릭하세요. <small>(시작: '+esc(nodeLabel(pathPicks[0]))+')</small>')+
    '</p><p class=al><small>관계 필터가 켜져 있으면 그 관계만 따라 경로를 찾습니다.</small></p>';
  mobileScrollTo('panel');
}
function pickPathNode(id){
  pathPicks.push(id);
  if(pathPicks.length>=2) computePath(pathPicks[0], pathPicks[1]);
  else showPathHint();
}
function computePath(a, b){
  // 무방향 BFS — 전체 그래프 기준(relFilter 켜져 있으면 그 관계만). 클러스터 spring 엣지 제외.
  const adj={};
  allEdges.forEach(e=>{
    if(typeof e.id==='string' && e.id.indexOf('cl_')===0) return;
    if(relFilter && !relFilter.has(e.label)) return;
    (adj[e.from]=adj[e.from]||[]).push([e.to,e.id]);
    (adj[e.to]=adj[e.to]||[]).push([e.from,e.id]);
  });
  const prev={}, prevE={}, seen=new Set([a]); let q=[a];
  while(q.length){ const u=q.shift(); if(u===b) break;
    (adj[u]||[]).forEach(([v,eid])=>{ if(!seen.has(v)){ seen.add(v); prev[v]=u; prevE[v]=eid; q.push(v); } }); }
  pathPicks=[];
  if(!seen.has(b)){ pathNodes=null; pathEdges=null; applyView();
    panel.innerHTML='<h2>🔗 경로</h2><p class=hint>두 노드 사이 연결 경로가 없습니다'+
      (relFilter?' (현재 관계 필터 기준)':'')+'.</p>'+
      '<p class=al><a href="#" onclick="restartPath();return false">다시 찾기</a></p>';
    return; }
  pathNodes=new Set(); pathEdges=new Set();
  const order=[]; let cur=b;
  while(cur!==undefined){ order.push(cur); pathNodes.add(cur);
    if(prevE[cur]!==undefined) pathEdges.add(prevE[cur]);
    if(cur===a) break; cur=prev[cur]; }
  order.reverse();
  applyView();
  if(net) net.fit({nodes:[...pathNodes], animation:true});
  panel.innerHTML='<h2>🔗 경로 <small>'+(order.length-1)+'단계</small></h2>'+
    '<p class=al>'+order.map(id=>esc(nodeLabel(id))).join(' → ')+'</p>'+
    '<p class=al><a href="#" onclick="restartPath();return false">다른 경로</a> · '+
    '<a href="#" onclick="clearPath();return false">해제</a></p>';
  mobileScrollTo('panel');
}
function restartPath(){ pathMode=true; pathPicks=[];
  const b=document.getElementById('pathbtn'); if(b) b.classList.add('on'); showPathHint(); }
function clearPath(){ pathMode=false; pathPicks=[]; pathNodes=null; pathEdges=null;
  const b=document.getElementById('pathbtn'); if(b) b.classList.remove('on'); applyView(); panel.innerHTML=''; }

// --- 좌측 문서 패널(일자별 그룹) ---
function dayOf(ts){ if(!ts) return '(날짜 미상)';
  const d=new Date(ts*1000);
  return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0'); }
// 문서 하나의 목록 아이템 HTML — 즐겨찾기/일반/숨김 목록이 모두 공유(중복 방지).
// 클릭=그래프 nav(필터·이동), 액션 버튼 2종(⭐즐겨찾기·📖읽기)은 stopPropagation.
// 숨기기는 목록이 아니라 우측 상세 패널의 텍스트 버튼으로(사용자 요구, panelHideBtn 참조).
function docItemHtml(dc){
  const unread = dc.seen===0, watching = dc.watch===1, pinned = dc.pinned===1, hid = dc.hidden===1;
  // readonly(/webro) 세션은 즐겨찾기가 서버가 404 내는 쓰기라 버튼 자체를 안 그린다
  // (사용자 요구 — 눌러도 안 되는 버튼이 남아있으면 안 됨).
  const pinBtn = READONLY ? '' : '<button class="actbtn'+(pinned?' pinned':'')+'" title="'+(pinned?'즐겨찾기 해제':'즐겨찾기에 추가')+
    '" onclick="event.stopPropagation();togglePin(\\''+dc.id+'\\','+(!pinned)+')">'+(pinned?'⭐':'☆')+'</button>';
  return '<div class="docitem'+(dc.id===activeDoc?' active':'')+(unread?' unread':'')+(hid?' hidden-doc':'')+
    (dc.id===revealedDocId?' revealed':'')+
    '" onclick="selectDoc(\\''+dc.id+'\\')">'+
    '<div class=docactions>'+pinBtn+'</div>'+
    // readbtn 은 docactions 밖의 형제 요소 — 모바일에서 아이템 우측 전체 높이를 차지하는
    // 큰 리빌 버튼으로 절대배치하려면 .docitem 기준으로 top/right/bottom 이 걸려야 하는데,
    // docactions 안에 있으면 그 박스(position:absolute) 가 포함 블록이 되어버려 어긋난다.
    '<button class="actbtn readbtn" title="크게 읽기" onclick="event.stopPropagation();openReader(\\''+dc.id+'\\')">'+
    '<span class=rbicon>📖</span><span class=rblabel>크게읽기</span></button>'+
    (watching?'<span class=wbadge title="주기 갱신 추적(watch)">🔄</span>':'')+
    (unread?'<span class=ubadge title="아직 안 본 문서">●</span>':'')+
    '<b>'+esc(dc.title)+'</b><span class=st>'+esc(dc.source_type||'')+'</span>'+
    (dc.summary?'<p>'+esc(dc.summary.slice(0,110))+'</p>':'')+'</div>';
}
let showHidden = false;
function toggleShowHidden(){ showHidden=!showHidden; renderDocs(document.getElementById('docq').value); }
// 모바일 전용 즐찾/전체 탭 전환 — #docs.tab-pinned 클래스로 CSS(위 @media) 가 표시를 가른다.
function setDocTab(tab){
  document.getElementById('docs').classList.toggle('tab-pinned', tab==='pinned');
  document.querySelectorAll('#doctabs button').forEach(b=>
    b.classList.toggle('active', b.dataset.tab===tab));
}
function renderDocs(filter){
  const q=(filter||'').trim().toLowerCase();
  const match = dc => !q || (dc.title+' '+dc.summary).toLowerCase().includes(q);
  // 숨김(hidden)은 기본 목록·즐겨찾기 양쪽에서 제외(목록 전용 숨김, 그래프는 안 건드림).
  const visible = allDocs.filter(dc=> dc.hidden!==1 && match(dc));
  const pinned = visible.filter(dc=>dc.pinned===1);
  const rest = visible.filter(dc=>dc.pinned!==1);
  const hiddenDocs = allDocs.filter(dc=> dc.hidden===1 && match(dc));

  document.getElementById('pinnedhead').style.display = pinned.length ? '' : 'none';
  document.getElementById('pinnedlist').innerHTML = pinned.map(docItemHtml).join('');
  // 모바일 목록 확장 시 즐찾/전체 탭(#doctabs) 노출 트리거 — 즐찾 없으면 탭 자체를 숨기고
  // 일반목록이 공간을 전부 씀(빈 40% 안 남게).
  document.getElementById('docs').classList.toggle('haspinned', pinned.length > 0);
  if(!pinned.length) setDocTab('all');   // 즐찾이 비면 즐찾 탭에 머물러 있지 않게 리셋

  document.getElementById('doclist').innerHTML = rest.length
    ? (()=>{ let html='', curDay=null;
        rest.forEach(dc=>{ const day=dayOf(dc.fetched_at);
          if(day!==curDay){ html+='<div class=dday>'+day+'</div>'; curDay=day; }
          html+=docItemHtml(dc); });
        return html; })()
    : '<p class=hint style="padding:10px">문서 없음</p>';

  const sh=document.getElementById('showhidden');
  // readonly 는 숨기기/해제 버튼이 없어 이 구간이 눌러도 소용없는 관리용 UI — 아예 숨김.
  if(READONLY || !hiddenDocs.length){ sh.style.display='none'; document.getElementById('hiddenlist').innerHTML=''; }
  else{
    sh.style.display='';
    sh.textContent = (showHidden?'▲ ':'▼ ')+'🙈 숨김 '+hiddenDocs.length+'개 '+(showHidden?'접기':'보기');
    document.getElementById('hiddenlist').innerHTML = showHidden ? hiddenDocs.map(docItemHtml).join('') : '';
  }
}
// 즐겨찾기/숨기기 토글 — 낙관적 갱신(즉시 반영) 후 서버 반영, 실패하면 되돌림.
async function togglePin(id, val){
  const d=allDocs.find(x=>x.id===id); if(d) d.pinned = val?1:0;
  renderDocs(document.getElementById('docq').value);
  try{
    const r=await fetch('document/pin',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({id:id, pinned:val})});
    if(!r.ok && d){ d.pinned = val?0:1; renderDocs(document.getElementById('docq').value); }
  }catch(e){ if(d){ d.pinned = val?0:1; renderDocs(document.getElementById('docq').value); } }
}
async function toggleHide(id, val){
  // 숨기는 방향(val=true)만 컨펌 — 오클릭 방지(사용자 지적) + 되돌리는 방법을 그 자리에서
  // 안내(어디서 다시 꺼내는지 몰라 헤매지 않게). 숨김 해제(val=false)는 안전한 방향이라
  // 컨펌 없이 즉시. 반환값 = 실제로 적용됐는지(컨펌 취소 시 false — 호출측 버튼 갱신 판단용).
  if(val && !confirm('이 문서를 목록에서 숨길까요?\\n\\n그래프 엔티티는 그대로 남고, 문서 '+
      '목록 맨 아래 "🙈 숨김 N개 보기"를 누르면 언제든 다시 꺼내(숨김 해제) 볼 수 있습니다.')){
    return false;
  }
  const d=allDocs.find(x=>x.id===id); if(d) d.hidden = val?1:0;
  renderDocs(document.getElementById('docq').value);
  try{
    const r=await fetch('document/hide',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({id:id, hidden:val})});
    if(!r.ok && d){ d.hidden = val?0:1; renderDocs(document.getElementById('docq').value); }
  }catch(e){ if(d){ d.hidden = val?0:1; renderDocs(document.getElementById('docq').value); } }
  return true;
}
// 상세 패널의 숨기기 텍스트 버튼 — toggleHide 결과(컨펌 취소 여부)를 보고 버튼 라벨만 갱신.
async function panelToggleHide(id, val){
  const ok = await toggleHide(id, val);
  if(!ok) return;
  const btn = document.getElementById('panelhidebtn');
  if(btn){
    btn.textContent = val ? '숨김 해제' : '숨기기';
    btn.setAttribute('onclick', "panelToggleHide('"+id+"',"+(!val)+")");
  }
}
// 모바일: 문서 상세 패널(#panel) 대신 목록 확장(listopen) + 아이템 탭으로 📖 버튼
// 리빌 + 크게읽기 흐름으로 개편(사용자 요구, 2026-07-15) — 그래프 캔버스는 이 흐름에서
// 절대 안 건드린다(리사이즈 버그 계열 재발 방지). 데스크톱은 기존 우측 패널 그대로 유지.
// 문서 소속 노드들로 화면 이동(전체 fit 이 아니라 그 문서의 노드만 화면에 차게) —
// 데스크톱·모바일 selectDoc 양쪽에서 공유(피드백: 모바일 리빌 시 이게 빠져있었음).
function fitToDocNodes(id){
  if(!net) return;
  const ids=[]; allNodes.forEach(n=>{ if(!n.hidden && (n.sources||[]).includes(id)) ids.push(n.id); });
  const opts = ids.length ? {nodes:ids} : {};
  // 사용자 제보(2026-07-22): 모바일에서 목록을 펼친(listopen) 채 문서를 고르면
  // net.fit() 이 #net 캔버스 "전체" 기준으로 중앙을 잡는데, 실제로는 그 캔버스의
  // 아래쪽 상당 부분(목록 79%)이 #docs 오버레이에 가려 안 보인다 — 결국 노드가
  // 화면에 안 보이는 위치(목록 뒤)로 이동해버림. 캔버스 전체 기준 fit(즉시)을 먼저
  // 하고, 실제로 보이는(목록에 안 가려진 위쪽) 영역의 중앙으로 보정 오프셋을
  // 애니메이션으로 적용한다(Playwright 로 오프셋 없이/있이 노드 DOM 좌표를 직접
  // 비교해 검증 — 오프셋 없으면 목록 뒤(y≈364, 보이는 영역은 0~148)에 묻히고,
  // 오프셋 적용 후 보이는 영역 중앙(y≈79)으로 옮겨짐).
  if(mobileMQ.matches && document.body.classList.contains('listopen')){
    net.fit({...opts, animation:false});
    const netwrapH = document.getElementById('netwrap').getBoundingClientRect().height;
    const coveredPx = document.getElementById('docs').getBoundingClientRect().height;
    const shiftUp = netwrapH/2 - (netwrapH-coveredPx)/2;
    net.moveTo({position: net.getViewPosition(), scale: net.getScale(),
      offset:{x:0, y:-shiftUp}, animation:{duration:300, easingFunction:'easeInOutQuad'}});
  } else {
    net.fit({...opts, animation:true});
  }
}
function selectDoc(id){
  if(mobileMQ.matches){
    if(!document.body.classList.contains('listopen')){
      // 목록이 접혀있으면 첫 탭은 확장만 — 아직 아무것도 선택 안 함.
      pushUIState('list');
      document.body.classList.add('listopen');
      return;
    }
    const d0 = allDocs && allDocs.find(d=>d.id===id);
    if(d0) d0.seen=1;
    if(revealedDocId===id){
      // 같은 아이템 재탭 = 리빌·peek 패널 전부 접기 — history.back() 을 거쳐
      // popstate 가 closePanel() 로 실제 정리(뒤로가기 스택과 항상 일치시키려고).
      closeTopUIState('panel');
    } else {
      openMobilePanelUI();   // 패널이 닫혀있었으면 뒤로가기 스택에 쌓기(사용자 제보, 2026-07-24)
      revealedDocId = id;
      activeDoc = id;      // #9-5: 데스크톱 selectDoc 과 동일하게 activeDoc 세팅 — applyView() 의
                            // 노드 색 하이라이트(dim/lit)가 activeDoc 만 보므로 이게 없으면 모바일에서
                            // 그래프 강조가 전혀 안 먹혔다(근본원인: revealedDocId 를 activeDoc 과
                            // 병렬로 도입하며 activeDoc 을 참조하는 기존 로직들이 모바일에서 조용히 무동작).
      fitToDocNodes(id);                    // 그래프를 이 문서 노드로 이동(피드백 복원)
      document.body.classList.remove('reading');
      document.body.classList.add('peek');  // 우측에 상세 패널 "찔끔" — openPeekPanel 로 완전히 열림
    }
    applyView();          // #9-5: activeDoc 변경을 그래프 노드 강조에 반영
    renderDocs(document.getElementById('docq').value);
    return;
  }
  const d0 = allDocs && allDocs.find(d=>d.id===id);
  if(d0) d0.seen=1;                             // 열람 → unread 해제(낙관적; 서버는 /document 가 처리)
  activeDoc = (activeDoc===id ? null : id);     // 같은 문서 재클릭 → 해제
  selectedNodeId=null;                          // 문서 모드로 전환 — 노드 inspect 해제
  renderDocs(document.getElementById('docq').value);
  applyView();
  if(activeDoc){
    fitToDocNodes(activeDoc);
    loadDocPanel(activeDoc);    // 우측 패널: 요약·자세히읽기·노드 버튼
    mobileScrollTo('panel');
  } else {
    panel.innerHTML = defaultHint();             // 해제 시 기본 힌트로 복원
  }
}
// 모바일: 우측에 26px 걸쳐 보이는 패널(문서 revealed 상태 또는 아무것도 선택 안 한
// idle 상태 둘 다 같은 모양) 탭/당김 → 전체 오픈. 문서 내용은 여기서 처음 로드
// (불필요한 fetch 방지 — 걸침 상태에선 아직 안 부름).
function openPeekPanel(){
  openMobilePanelUI();   // idle(닫힌) 상태에서 처음 여는 거면 뒤로가기 스택에 쌓기(2026-07-24)
  document.body.classList.remove('peek');
  if(revealedDocId) loadDocPanel(revealedDocId);   // panel.innerHTML 변경 → MutationObserver 가 body.reading 을 켠다
  else document.body.classList.add('reading');     // 선택된 문서 없으면 기본 힌트를 그대로 전체 오픈으로
}
// 모바일 슬라이드 오버레이의 ✕ 닫기. 문서가 아직 선택된 채(peek 대상 있음)면 완전히
// 안 닫고 "찔끔" 상태로 되돌아간다(다시 탭하면 재오픈) — 노드 클릭발 오픈이거나
// 데스크톱이면 기존처럼 완전히 닫는다.
function closePanelOrPeek(){
  if(mobileMQ.matches && revealedDocId){
    document.body.classList.remove('reading');
    document.body.classList.add('peek');
  } else {
    closeTopUIState('panel');   // popstate → closePanel() 이 실제 정리(뒤로가기 스택과 일치)
  }
}
// panel.innerHTML 변경이 MutationObserver(syncReadingState)를 타고 body.reading 을
// 벗겨내 CSS 트랜지션으로 슬라이드 아웃된다(데스크톱은 애초에 버튼이 안 보임).
function closePanel(){
  activeDoc = null; selectedNodeId = null; revealedDocId = null;
  document.body.classList.remove('peek');
  renderDocs(document.getElementById('docq').value);
  applyView();
  panel.innerHTML = defaultHint();
}

// 좌측 문서 선택 시 우측 패널: 문서 요약 + 자세히 읽기 + '이 문서의 노드' 버튼.
function loadDocPanel(id){
  panel.innerHTML='<p class=hint>문서 불러오는 중…</p>';
  fetch('document?id='+encodeURIComponent(id)).then(r=>r.json()).then(dc=>{
    // 그 사이 다른 문서/노드로 이동했으면 무시(stale 응답 가드) — 데스크톱은 activeDoc,
    // 모바일 peek 흐름은 revealedDocId 로 "지금 보고 있는 문서"를 추적(둘 다 안 쓰면
    // 버그: activeDoc 만 보면 모바일에서 항상 null!==id 라 응답이 영원히 버려짐).
    if(activeDoc!==id && revealedDocId!==id) return;
    if(!dc || dc.error){ panel.innerHTML='<p class=hint>문서를 찾을 수 없습니다.</p>'; return; }
    renderDocPanel(dc);
  }).catch(()=>{ panel.innerHTML='<p class=hint>문서 로드 실패.</p>'; });
}
// 한 문서(article)에 속한 노드 = graph node.sources 에 그 문서 id 가 든 노드(클라 계산).
function docNodes(docId){
  const out=[]; if(!allNodes) return out;
  allNodes.forEach(n=>{ if((n.sources||[]).includes(docId)) out.push(n); });
  out.sort((a,b)=> (b.degree||0)-(a.degree||0));   // 중심성 높은 노드부터(핵심이 위로)
  return out;
}
function renderDocPanel(dc){
  let h='<h2>'+esc(dc.title)+' <small>'+esc(dc.source_type||'')+'</small></h2>';
  if(dc.url) h+='<p class=docmeta><a href="'+esc(dc.url)+'" target=_blank rel=noopener>↗ 원문 열기</a></p>';
  h+=extraSourcesHtml(dc);
  // 읽기는 중앙 팝업(마크다운·이미지)으로 — 그래프 nav 와 분리(사용자 요구).
  if(dc.summary||dc.detail) h+='<button class=readbtn onclick="openReader(\\''+dc.id+'\\')">📖 크게 읽기</button>';
  // 숨기기 — 목록이 아니라 상세 패널에 텍스트 버튼으로(사용자 요구, 목록에선 오클릭 유발).
  if(!READONLY) h+='<div><button id=panelhidebtn class=hidetextbtn onclick="panelToggleHide(\\''+dc.id+'\\','+(!dc.hidden)+')">'+
    (dc.hidden?'숨김 해제':'숨기기')+'</button></div>';
  if(dc.summary) h+='<h3>요약</h3><div class=synth>'+esc(dc.summary)+'</div>';
  // 이 문서의 노드 버튼 — 요약 바로 아래(피드백). 누르면 그래프에서 그 노드로 이동(nav).
  const ns=docNodes(dc.id);
  h+='<h3>이 문서의 노드 ('+ns.length+')</h3>';
  if(ns.length){ h+='<div class=nodebtns>'+ ns.map(n=>{
      const c=TYPE_COLORS[n.group]||'#8b949e';
      return '<button class=nodebtn title="'+esc(n.group||'')+'" onmouseenter="peekNode(event,\\''+n.id+'\\')" '+
        'onmouseleave="leaveNode()" onclick="focusNode(\\''+n.id+'\\')">'+
        '<i style="background:'+c+'"></i>'+esc(n.label)+'</button>'; }).join('')+'</div>';
  } else { h+='<p class=al>이 문서에서 추출된 노드가 없습니다.</p>'; }
  if(!dc.summary && !dc.detail) h+='<p class=al>이 문서의 요약/전문이 아직 없습니다.</p>';
  panel.innerHTML=h;
}
// 노드 버튼 클릭 → 그래프에서 그 노드로 카메라 이동 + 선택 + 우측은 노드 상세로 전환.
// activeDoc 은 유지 → 노드 상세 상단의 '← 문서로' 로 문서 패널에 즉시 복귀 가능.
function focusNode(id){
  selectedNodeId=id;
  if(net){ net.selectNodes([id]); net.focus(id,{scale:1.3,animation:true}); }
  loadNode(id);
  mobileScrollTo('net');
}

// --- 종합 수집(synthSet) — inspect(클릭)와 분리 ---
function toggleSynth(id){ if(synthSet.has(id)) synthSet.delete(id); else synthSet.add(id); renderChips(); }
function addToSynth(id){ synthSet.add(id); renderChips(); if(id===selectedNodeId) loadNode(id); }
function renderChips(){
  const box=document.getElementById('synthchips');
  box.innerHTML=[...synthSet].map(id=>{ const n=allNodes&&allNodes.get(id);
    return '<span class=chip onclick="toggleSynth(\\''+id+'\\')" title="제거">'+esc(n?n.label:id)+' ✕</span>'; }).join('');
  document.getElementById('synthbtn').textContent='🧩 종합 ('+synthSet.size+')';
}

// --- 검색: 즉시 라벨 매칭(기본) vs 의미검색 버튼(체크 시) ---
// 검색 결과(라벨·의미)를 한눈에 — 선택 노드들이 모두 화면에 들어오게 zoom/위치 조절.
// 단일 결과는 fit 이 과확대되므로 focus(고정 scale). 여러 결과는 fit({nodes})로 전부 보이게.
// degree 슬라이더로 숨겨진 매치는 제외(보이는 것만 카메라 대상; selectDoc 과 동일 방식).
function fitToMatches(ids){
  if(!net || !ids.length) return;
  net.selectNodes(ids);
  const vis = ids.filter(id=>{ const n=allNodes && allNodes.get(id); return n && !n.hidden; });
  if(!vis.length) return;
  if(vis.length===1) net.focus(vis[0],{scale:1.1,animation:true});
  else net.fit({nodes:vis, animation:true});
}
// 검색 결과를 '점차 뭉치게' — physics 를 켠 채로 보이지 않는 중앙 앵커를 쓴다.
//   · 매칭 노드 → 중앙 앵커에 spring 엣지(인력): 가운데로 끌려와 한 덩어리로 모인다.
//   · 앵커는 큰 mass·고정 → barnesHut 반발이 *모든* 노드를 밀어내지만, 매칭은 spring 이
//     반발을 이겨 중앙에 붙들리고, 비매칭은 반발만 받아 중앙에서 바깥으로 밀려난다.
// 위치를 강제로 옮기지 않으므로 physics ON 유지(드래그·관성 그대로). 검색 해제 시 앵커와
// 엣지만 빼면 물리가 원래 레이아웃으로 되돌린다(unclusterEdges).
const ANCHOR_MASS=60;   // 앵커 반발 세기(비매칭 밀어냄). ↑ 강하게 밀어냄
const CENTER_LEN=55;    // 매칭~중앙 spring 길이. ↓ 가운데로 더 바싹
function clusterMatches(ids, done){
  unclusterEdges();
  const vs=(ids||[]).filter(id=>{ const n=allNodes&&allNodes.get(id); return n && !n.hidden; });
  if(!net || !allEdges || vs.length<2){ if(done) done(); return; }
  net.setOptions({physics:true});  // 안정화 후 꺼둔 물리(성능, 2026-07-24)를 뭉치는 동안만 다시 켠다
  const c=net.getViewPosition();   // 현재 화면 중앙 좌표에 앵커를 둔다
  allNodes.add({id:'cl_anchor', x:c.x, y:c.y, fixed:true, physics:true, hidden:true,
                mass:ANCHOR_MASS, label:'', shape:'dot', size:1});
  clusterAnchor='cl_anchor';
  const eids=[];
  const newEdges=vs.map((id,i)=>{
    const eid='cl_'+i; eids.push(eid);
    return {id:eid, from:'cl_anchor', to:id, color:{opacity:0}, width:0, length:CENTER_LEN,
            physics:true, smooth:false};
  });
  allEdges.add(newEdges);
  clusterEdges=eids;
  // 물리가 뭉치는 동안 기다렸다 한눈에 fit(여러 결과면 fit, 1개면 focus).
  if(done) setTimeout(done, 900);
}
// 검색 해제 시 임시 앵커·spring 엣지 제거 → 물리가 원래 레이아웃으로 자연 복귀.
function unclusterEdges(){
  if(clusterEdges && allEdges && clusterEdges.length){
    try{ allEdges.remove(clusterEdges); }catch(e){}
  }
  if(clusterAnchor && allNodes){ try{ allNodes.remove(clusterAnchor); }catch(e){} }
  clusterEdges=null; clusterAnchor=null;
  if(net) net.setOptions({physics:false});  // 뭉치기 끝 — 성능을 위해 다시 끔(2026-07-24)
}
// 우측 '이 문서의 노드' 버튼 hover — 그래프뷰를 그 노드로 부드럽게 이동(선택/상세는 안 바꿈).
// 우측 '이 문서의 노드' hover — 그래프 카메라를 그 노드로 옮기고(기존), 1.5초 머물면
// 그래프 hover 와 같은 요약 팝업을 버튼 진입 위치에 띄운다(사용자 요구). leave 시 취소.
function peekNode(ev, id){
  // 모바일: onclick=focusNode() 가 탭 한 번에 이미 그래프 이동+전체 노드 상세(패널)로
  // 넘어가므로, mouseenter 로 짜인 이 hover 미리보기 팝업은 같은 정보를 중복해서 보여줄
  // 뿐 아니라 터치엔 mouseleave 가 안 와서 안 닫히는 버그였다(사용자 제보, 2026-07-24) —
  // 데스크톱 전용으로 한정.
  if(mobileMQ.matches) return;
  if(net) net.focus(id,{scale:1.2,animation:{duration:400,easingFunction:'easeInOutQuad'}});
  clearTimeout(hoverTimer);
  const x=ev.clientX, y=ev.clientY;
  hoverTimer=setTimeout(()=>showNodePop(id, x, y), 1500);
}
function leaveNode(){ clearTimeout(hoverTimer); hideNodePop(); }
// 타이핑마다 즉시 검색하면 매 키 입력에 강조+물리 클러스터링이 돌아 무겁고 출렁인다.
// 디바운스: 입력이 멈춘 뒤(350ms) 한 번만 실행. 단 검색창을 비우면 즉시 해제(반응성).
function onSearchInput(v){
  if(document.getElementById('sem').checked) return;   // 의미검색은 버튼/엔터로만
  clearTimeout(searchDebounce);
  if(!v.trim()){ hl(''); return; }                     // 비우기 → 즉시 강조/클러스터 해제
  searchDebounce=setTimeout(()=>hl(v), 550);
}
// 라벨 검색: 매치 강조 + 나머지 dim(문서 선택과 동일 방식). 색칠 대신 highlightSet+applyView.
function hl(q){
  if(!allNodes) return;
  q=q.trim().toLowerCase();
  if(!q){ highlightSet=null; applyView(); unclusterEdges(); if(net){ net.unselectAll(); net.fit({animation:true}); } return; }
  const matches=[];
  allNodes.forEach(n=>{ if(n.label.toLowerCase().includes(q)) matches.push(n.id); });
  highlightSet = new Set(matches);
  applyView();
  clusterMatches(matches, ()=>fitToMatches(matches));   // 결과를 점차 뭉치게 한 뒤 한눈에 fit
}
document.getElementById('sem').addEventListener('change',e=>{
  document.getElementById('searchbtn').style.display = e.target.checked?'':'none';
  if(e.target.checked) hl('');   // 즉시 라벨강조 해제(의미검색은 버튼으로만)
});
document.getElementById('q').addEventListener('keydown',e=>{
  if(e.key!=='Enter') return;
  if(document.getElementById('sem').checked){ doSemantic(); }
  else { clearTimeout(searchDebounce); hl(e.target.value);   // 대기 중 디바운스를 즉시 확정
         const m=net.getSelectedNodes(); if(m.length) loadNode(m[0]); }
});
// 입력창 포커스 시 기존 검색어 전체 선택 → 바로 새로 타이핑 가능(GOALS ④).
document.getElementById('q').addEventListener('focus', e=> e.target.select());
function doSemantic(){ semanticSearch(document.getElementById('q').value); }

// --- 인증 상태 표시 ---
// 인증은 /web 링크가 설정한 httponly 쿠키로 처리된다. 이 페이지가 로드됐다는 것 자체가
// 인증됨을 뜻한다(미인증이면 게이트가 404). 클릭해도 별도 승인 요청을 만들지 않는다 —
// 예전 nonce 승인 플로우가 쿠키 인증과 무관하게 '승인 요청 만료' 텔레그램 스팸을
// 유발했다(이슈3). 쿠키가 만료되면(7일 미사용) synthesize/검색이 401 → 아래에서 안내.
function setAuth(state){
  document.getElementById('authstate').textContent =
    state==='idle' ? '🔓 세션 만료 — /web 재접속' :
    state==='readonly' ? '👁️ 읽기전용' : '🔒 인증됨';
}
async function synth(){
  const ids=[...synthSet];
  if(!ids.length){ alert('종합할 노드를 먼저 모으세요 — Ctrl+클릭 또는 상세의 "➕ 종합에 추가".'); return; }
  panel.innerHTML='<p class=hint>🧩 '+ids.length+'개 노드 종합 중… (LLM 호출)</p>';
  mobileScrollTo('panel');   // 진행·결과가 화면 밖(맨 아래)에 그려지므로
  // 인증은 claire_session 쿠키(/web 진입)로 자동 전송됨 — 별도 헤더 불필요.
  fetch('synthesize',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({node_ids:ids})})
   .then(r=> { if(r.status===401||r.status===404){ setAuth('idle'); return {error:'세션 만료 — 텔레그램 /web 으로 다시 접속하세요'}; } return r.json(); })
   .then(d=>{
     if(d.error){ panel.innerHTML='<p class=hint>오류: '+esc(d.error)+'</p>'; return; }
     synthPlanEntityIds=ids; synthPlanAnswer=d.answer; synthPlanEntityNames=d.entities;
     renderSynthAnswer();
   }).catch(e=>{ panel.innerHTML='<p class=hint>요청 실패: '+esc(String(e))+'</p>'; });
}
// synth() 결과 화면 렌더 — 조사계획에서 "취소"로 돌아올 때도 재사용(재요청 없이,
// 이미 받아둔 synthPlanAnswer/synthPlanEntityNames 로 그린다 — LLM 재호출 낭비 방지).
function renderSynthAnswer(){
  let h='<h2>🧩 종합 지식 <small>'+synthPlanEntityNames.length+'개 노드</small></h2>';
  h+='<div class=synth>'+esc(synthPlanAnswer)+'</div>';
  h+='<p class=al>대상: '+synthPlanEntityNames.map(esc).join(', ')+'</p>';
  // readonly(/webro) 세션은 /synthesize/plan 도 서버에서 막혀있어 버튼 자체를 안 그림.
  if(!READONLY) h+='<div style="margin-top:.6em"><button onclick="openSynthPlan()">'+
    '🧭 조사계획 세우기</button></div>';
  panel.innerHTML=h;
}

// --- 종합 → 조사계획 확인(체크박스) → 승인된 질문 실행조사 → 문서합성 → 적재 ---
// 사용자 요구(2026-07-21): 종합 결과만으로 끝나지 않고, 그걸 바탕으로 조사계획을
// 세워 웹으로 심화조사한 뒤 고품질 문서를 만들어 그래프에 적재. 비용이 드는 실제
// 웹조사 전에 계획을 사용자가 체크박스로 확인/제외할 수 있게(원클릭 전자동 아님).
async function openSynthPlan(){
  panel.innerHTML='<p class=hint>🧭 조사계획 세우는 중… (LLM 호출)</p>';
  mobileScrollTo('panel');
  let d;
  try{
    const r=await fetch('synthesize/plan',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({node_ids:synthPlanEntityIds, synth_answer:synthPlanAnswer})});
    if(r.status===401||r.status===404){ setAuth('idle');
      panel.innerHTML='<p class=hint>세션 만료 — 텔레그램 /web 으로 다시 접속하세요</p>'; return; }
    d=await r.json();
  }catch(e){ panel.innerHTML='<p class=hint>계획 요청 실패: '+esc(String(e))+'</p>'; return; }
  renderSynthPlan(d);
}
function renderSynthPlan(d){
  if(d.error){ panel.innerHTML='<p class=hint>오류: '+esc(d.error)+'</p>'; return; }
  synthPlanQuestions=(d.questions||[]).map(q=>({question:q.question, rationale:q.rationale, checked:true}));
  if(!synthPlanQuestions.length){
    panel.innerHTML='<p class=hint>조사할 만한 하위질문을 찾지 못했습니다.</p>'; return;
  }
  let h='<h2>🧭 조사계획</h2><p class=al>체크한 질문만 실제 웹조사(비용 발생)로 넘어갑니다.</p><ul id="planlist">';
  synthPlanQuestions.forEach((q,i)=>{
    h+='<li style="margin:.5em 0;list-style:none"><label><input type=checkbox checked '+
      'onchange="togglePlanQ('+i+')"> <b>'+esc(q.question)+'</b></label>'+
      (q.rationale?'<div class=al style="margin-left:1.6em">'+esc(q.rationale)+'</div>':'')+'</li>';
  });
  h+='</ul><button onclick="runSynthResearch()">이대로 조사 시작</button> '+
    '<button class="sec" onclick="renderSynthAnswer()">취소</button>';
  panel.innerHTML=h;
}
function togglePlanQ(i){ if(synthPlanQuestions[i]) synthPlanQuestions[i].checked=!synthPlanQuestions[i].checked; }
async function runSynthResearch(){
  const qs=synthPlanQuestions.filter(q=>q.checked).map(q=>q.question);
  if(!qs.length){ alert('최소 한 개는 체크하세요.'); return; }
  panel.innerHTML='<h2>🧭 조사 진행 중</h2><p class="al" id="srelapsed">시작…</p><ul id="srprog"></ul>';
  mobileScrollTo('panel');
  const t0=Date.now();
  const timer=setInterval(()=>{ const el=document.getElementById('srelapsed');
    if(el) el.textContent='⏱ 경과 '+Math.round((Date.now()-t0)/1000)+'s'; else clearInterval(timer); },1000);
  let result=null;
  try{
    const r=await fetch('synthesize/research',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({node_ids:synthPlanEntityIds, synth_answer:synthPlanAnswer, questions:qs})});
    if(r.status===401||r.status===404){ clearInterval(timer); setAuth('idle');
      panel.innerHTML='<p class=hint>세션 만료 — 텔레그램 /web 으로 다시 접속하세요</p>'; return; }
    const reader=r.body.getReader(), dec=new TextDecoder(); let buf='';
    while(true){
      const {done,value}=await reader.read(); if(done) break;
      buf+=dec.decode(value,{stream:true});
      let i; while((i=buf.indexOf('\\n'))>=0){
        const line=buf.slice(0,i).trim(); buf=buf.slice(i+1);
        if(!line) continue;
        let ev; try{ ev=JSON.parse(line); }catch(_){ continue; }
        if(ev.done){ result=ev.result; continue; }
        const ul=document.getElementById('srprog');
        if(ul){ const li=document.createElement('li'); li.className='al';
          li.textContent=(ev.stage==='llm'?'⏳ ':'• ')+(ev.msg||''); ul.appendChild(li); }
      }
    }
  }catch(e){ clearInterval(timer);
    panel.innerHTML='<p class=hint>요청 실패: '+esc(String(e))+'</p>'; return; }
  clearInterval(timer);
  if(!result){ panel.innerHTML='<p class=hint>응답이 끊겼습니다 — 잠시 후 다시 시도하세요.</p>'; return; }
  renderSynthResearchResult(result);
}
function renderSynthResearchResult(d){
  let h='<h2>🧭 조사 결과</h2>';
  if(d.error && !d.document) h+='<p class=hint>'+esc(d.error)+'</p>';
  h+='<p class=al>질문 '+(d.questions_total||0)+'개 중 통과 '+((d.passed||[]).length)+'개'+
    (d.added?' · ✅ 그래프에 추가됨':'')+'</p>';
  (d.rejected||[]).forEach(r=>{ h+='<p class=al>⏸ '+esc(r.question)+' — '+esc(r.reason||'게이트 미달')+'</p>'; });
  if(d.document) h+='<h3>'+esc(d.document.title||'조사 합성 문서')+'</h3><div class=synth>'+esc(d.document.body||'')+'</div>';
  if(d.added && d.ingest){
    h+='<p class=al>그래프 반영: 신규 '+d.ingest.entities_created+' · 기존연결 '+
      d.ingest.entities_linked+' · 관계 '+d.ingest.relations_added+'</p>';
    refreshGraph();
    if(d.ingest.document_id) h+='<p><a href="#" onclick="selectDoc(\\''+d.ingest.document_id+'\\');return false">문서 보기 →</a></p>';
  }
  panel.innerHTML=h;
}
async function semanticSearch(q){
  q=(q||'').trim(); if(!q) return;
  document.getElementById('stat').innerHTML='🔎 의미검색 중…';
  let r;
  try{ r=await fetch('search',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({query:q, summarize:false, limit:12})}); }
  catch(e){ document.getElementById('stat').textContent='검색 실패'; return; }
  if(r.status===401||r.status===404){ setAuth('idle'); document.getElementById('stat').textContent='세션 만료 — /web 으로 재접속'; return; }
  const d=await r.json();
  const ids=(d.hits||[]).map(h=>h.id).filter(Boolean);
  highlightSet = new Set(ids);   // 라벨 검색과 동일하게 강조+dim 방식 사용
  applyView();
  clusterMatches(ids, ()=>fitToMatches(ids));   // 의미검색 결과도 점차 뭉치게 + 한눈에 fit
  if(!ids.length){ document.getElementById('stat').textContent='🔎 의미검색: 결과 없음'; }
}

// 이 페이지가 로드됐다는 것 자체가 인증됨을 의미(미인증이면 게이트가 404). 쿠키 기반.
// owner 인지 readonly(/webro) 인지는 /whoami 로 별도 확인 — readonly 면 눌러도 서버가
// 404 내는 쓰기 버튼(적재/종합/중복정리/공유/즐겨찾기/숨기기/조사)을 아예 안 그린다
// (사용자 요구: 죽은 버튼이 남아있으면 안 됨). documents fetch 와 병렬로 요청하고,
// 확정되면 renderDocs 를 다시 호출해(멱등) 즐겨찾기/숨기기 버튼 유무를 바로잡는다.
setAuth('authed');
syncThemeBtn();   // 저장된 테마에 맞춰 🌙/🌞 라벨 동기화(테마 자체는 head 인라인에서 선적용)
fetch('documents').then(r=>r.json()).then(d=>{ allDocs=d.documents||[]; renderDocs(); });
fetch('whoami').then(r=>r.json()).then(d=>{
  READONLY = d.scope!=='owner';
  document.body.classList.toggle('ro', READONLY);
  if(READONLY){ setAuth('readonly'); renderDocs(document.getElementById('docq').value); }
}).catch(()=>{});

// 읽기전용 디버그 핸들(테스트/Playwright 검증용 — closure 상태 관찰). 부작용 없음.
window.claireDebug = {
  get sel(){ return net ? net.getSelectedNodes() : []; },
  get highlight(){ return highlightSet ? [...highlightSet] : null; },
  get selected(){ return selectedNodeId; },
  get synth(){ return [...synthSet]; },
  positions(ids){ return net ? net.getPositions(ids) : {}; },
  get scale(){ return net ? net.getScale() : null; },
  get viewpos(){ return net ? net.getViewPosition() : null; },
  get clustered(){ return clusterEdges; },
};
</script></body></html>
"""


# --- 공유 핫링크용 경량 읽기 페이지(/p?s=token) — 인증/그래프 없이 문서 1개만 보여준다. ---
# 데이터는 <script type=application/json> 에 임베드(라운드트립 1회)하고 클라가 마크다운 렌더.
# GRAPH_HTML 과 독립(공유 토큰은 세션과 분리되어야 하므로 UI/JS 도 섞지 않는다).
_SHARED_HTML = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>__TITLE__ — claire_bible</title>
<script src="https://unpkg.com/marked@4.3.0/marked.min.js"
 integrity="sha384-QsSpx6a0USazT7nK7w8qXDgpSAPhFsb2XtpoLFQ5+X2yFN6hvCKnwEzN8M5FWaJb"
 crossorigin="anonymous" referrerpolicy="no-referrer"></script>
<script src="https://unpkg.com/dompurify@3.1.6/dist/purify.min.js"
 integrity="sha384-+VfUPEb0PdtChMwmBcBmykRMDd+v6D/oFmB3rZM/puCMDYcIvF968OimRh4KQY9a"
 crossorigin="anonymous" referrerpolicy="no-referrer"></script>
<style>
  :root{--bg:#ffffff;--fg:#1f2328;--muted:#656d76;--border:#d0d7de;--accent:#0969da;
    --accent2:#1a7f37;--card-bg:#f6f8fa;--chip-bg:#eaeef2;--mark-bg:#fff8c5;--mark-fg:#633c01}
  @media (prefers-color-scheme:dark){:root{--bg:#0e1116;--fg:#d7dbe0;--muted:#8b949e;
    --border:#2a2f37;--accent:#58a6ff;--accent2:#7ee787;--card-bg:#161b22;--chip-bg:#1f2937;
    --mark-bg:#4d3800;--mark-fg:#ffdf5d}}
  html,body{margin:0;background:var(--bg);color:var(--fg);font-family:system-ui,sans-serif}
  .wrap{max-width:780px;margin:0 auto;padding:28px 18px 80px}
  h1{font-size:24px;margin:.2em 0} .meta{color:var(--muted);font-size:13px;margin:.2em 0 1.2em}
  .meta a{color:var(--accent);text-decoration:none}
  .sec{color:var(--muted);font-size:11px;letter-spacing:.04em;text-transform:uppercase;margin:1.4em 0 .3em}
  .brand{color:var(--accent2);font-weight:600;font-size:12px}
  .foot{margin-top:2.5em;padding-top:1em;border-top:1px solid var(--border);color:var(--muted);font-size:12px}
  mark{background:var(--mark-bg);color:var(--mark-fg);padding:0 .15em;border-radius:2px}
  .md{line-height:1.75;font-size:16px;word-break:break-word}
  .md h2{font-size:1.3em;margin:1.1em 0 .4em;border-bottom:1px solid var(--border);padding-bottom:.2em}
  .md h3{font-size:1.12em;margin:1em 0 .35em} .md p{margin:.6em 0}
  .md ul,.md ol{margin:.5em 0;padding-left:1.5em} .md li{margin:.3em 0}
  .md a{color:var(--accent)} .md img{max-width:100%;height:auto;display:block;margin:.8em auto;border-radius:6px;border:1px solid var(--border)}
  .md blockquote{margin:.6em 0;padding:.2em .9em;border-left:3px solid var(--border);color:var(--muted)}
  .md code{background:var(--chip-bg);padding:.1em .35em;border-radius:3px;font-size:.9em}
  .md pre{background:var(--card-bg);border:1px solid var(--border);border-radius:6px;padding:.8em;overflow:auto}
  .md table{border-collapse:collapse;margin:.6em 0} .md th,.md td{border:1px solid var(--border);padding:.3em .6em}
</style></head>
<body><div class="wrap" id="wrap"></div>
<script id="docdata" type="application/json">__DATA__</script>
<script>
function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function renderMarkdown(src){
  if(!src) return '';
  let s=String(src).replace(/==([^=\\n]+)==/g,'<mark>$1</mark>');
  if(!window.DOMPurify) return esc(String(src)).replace(/\\n/g,'<br>');
  let html; try{ html=(window.marked?(marked.parse?marked.parse(s):marked(s)):esc(s)); }
  catch(e){ html=esc(s).replace(/\\n/g,'<br>'); }
  return DOMPurify.sanitize(html,{ADD_ATTR:['target']});
}
const dc=JSON.parse(document.getElementById('docdata').textContent||'{}');
let h='<div class=brand>claire_bible · 공유 문서</div>';
h+='<h1>'+esc(dc.title||'(제목 없음)')+'</h1>';
h+='<div class=meta>'+(dc.source_type?esc(dc.source_type):'')+
  (dc.url?' · <a href="'+esc(dc.url)+'" target=_blank rel=noopener>↗ 원문 열기</a>':'')+'</div>';
if((dc.extra_sources||[]).length){
  h+='<div class=sec>병합된 출처 ('+dc.extra_sources.length+')</div><ul class=srclist>'+
    dc.extra_sources.map(s=>'<li><a href="'+esc(s.url||'')+'" target=_blank rel=noopener>'+
      esc(s.title||s.url||'')+'</a></li>').join('')+'</ul>';
}
if(dc.summary){ h+='<div class=sec>요약</div><div class="md">'+renderMarkdown(dc.summary)+'</div>'; }
if(dc.detail){ h+='<div class=sec>자세히 읽기</div><div class="md">'+renderMarkdown(dc.detail)+'</div>'; }
if(!dc.summary && !dc.detail){ h+='<p class=meta>이 문서의 요약/전문이 아직 없습니다.</p>'; }
h+='<div class=foot>이 링크는 이 문서 하나만 읽기 전용으로 공유합니다.</div>';
document.getElementById('wrap').innerHTML=h;
</script></body></html>
"""


def shared_html(doc: dict) -> str:
    """공유 문서 1개를 임베드한 경량 읽기 페이지 HTML. doc = document_detail() 결과.

    문서 데이터를 JSON 으로 <script> 에 임베드한다 — `</script>`·`<` 등이 스크립트를
    조기 종료/주입하지 못하게 HTML 특수문자를 \\uXXXX 로 이스케이프(스크랩 본문 유래)."""
    import json as _json

    data = _json.dumps(doc, ensure_ascii=False)
    data = data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    title = (doc.get("title") or "공유 문서").replace("<", "").replace(">", "")
    return _SHARED_HTML.replace("__DATA__", data).replace("__TITLE__", title)
