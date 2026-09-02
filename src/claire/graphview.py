"""읽기전용 그래프 시각화 — vis.js 용 데이터 변환 + 정적 HTML 페이지.

ASGI 웹 서비스(Starlette/Uvicorn)가 /graph(JSON)·/node·/documents·/synthesize·/research 로 노출한다.
정본 DB 를 읽고, 종합(synthesize)·맥락조사(research)만 LLM 비용이 있어 인증 뒤에 둔다.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter

from .store import db as dbm


def graph_json(conn: sqlite3.Connection, include_hidden: bool = True) -> dict:
    """엔티티/관계를 vis.js network 형식(nodes/edges)으로. dangling edge 는 제외.

    각 노드에 degree(연결 수)를 실어 UI 가 degree-centrality 임계로 핵심 서브그래프만
    표시할 수 있게 한다(전체 N개 렌더 → 큰 그래프의 가시성/스케일 문제 해소).
    include_hidden=False 면 숨김 문서 전용 엔티티 및 엣지를 제외한다."""
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
        {"id": f"e{i}", "from": r.source_id, "to": r.target_id, "label": r.type,
         "arrows": "to", "dashes": r.provisional}
        for i, r in enumerate(
            r for r in rels if r.source_id in visible_ent_ids and r.target_id in visible_ent_ids)
    ]
    deg: Counter = Counter()
    for e in edges:
        deg[e["from"]] += 1
        deg[e["to"]] += 1

    for n in visible_nodes:
        n["degree"] = deg.get(n["id"], 0)

    max_degree = max((n["degree"] for n in visible_nodes), default=0)
    return {"nodes": visible_nodes, "edges": edges,
            "stats": {"entities": len(visible_nodes), "relations": len(edges),
                      "max_degree": max_degree}}


def node_detail(conn: sqlite3.Connection, entity_id: str, include_hidden: bool = True) -> dict | None:
    """한 노드의 '쓸 수 있는 지식': 전체 observations + 소스 문서(제목·요약·URL) +
    타입 있는 이웃. 패널에 그대로 펼친다. 없으면 None."""
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
                "id": other.id, "name": other.name, "type": other.type,
                "rel": r.type, "dir": "out" if out else "in",
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
        "id": ent.id, "name": ent.name, "type": ent.type,
        "aliases": ent.aliases, "observations": ent.observations,
        "provisional": ent.provisional,
        "neighbors": neighbors, "documents": documents,
    }


def document_detail(conn: sqlite3.Connection, document_id: str, include_hidden: bool = True) -> dict | None:
    """한 문서(article)의 우측 패널용 상세 — 제목·출처·요약·상세(detail). 없으면 None.

    좌측 문서를 고르면 그래프 강조에 더해 우측에 이 요약/상세를 펼친다(노드 클릭 없이
    문서 자체를 읽게). 노드 목록은 클라이언트가 graph 의 node.sources 로 계산하므로
    여기선 싣지 않는다(중복 전송 방지)."""
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
        or (isinstance(meta_dict.get("transcript_segments"), list) and len(meta_dict["transcript_segments"]) > 0)
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


def documents_list(
    conn: sqlite3.Connection, limit: int = 300, *,
    since: float | None = None, query: str | None = None,
    include_hidden: bool = True,
) -> list[dict]:
    """좌측 문서 패널용 — 최신순 문서(제목·요약·출처타입·시각)."""
    out = []
    for r in dbm.documents_timeline(conn, limit, since=since, query=query, include_hidden=include_hidden):
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
    컨텍스트 윈도우를 아낀다(docs/origin/design/MCP_SUPPORT.md 참고)."""
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


def synthesize(conn, provider, entity_ids: list[str], query: str | None = None) -> dict:
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


# 고정 버전의 vis.js 9/Markdown 정화 라이브러리 기반 단일 페이지.
GRAPH_HTML = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Claire Bible — 지식 그래프</title>
<meta name="mobile-web-app-capable" content="yes"/>
<meta name="apple-mobile-web-app-capable" content="yes"/>
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"/>
<meta name="apple-mobile-web-app-title" content="Claire Bible"/>
<meta name="application-name" content="Claire Bible"/>
<link rel="icon" type="image/svg+xml" href="/favicon.svg"/>
<link rel="icon" type="image/png" sizes="192x192" href="/icon?p=android-chrome-192x192.png"/>
<link rel="icon" type="image/png" sizes="512x512" href="/icon?p=android-chrome-512x512.png"/>
<link rel="alternate icon" href="/favicon.ico"/>
<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png"/>
<link rel="manifest" href="/manifest.json"/>
<link rel="mask-icon" href="/favicon.svg" color="#00ffaa"/>
<meta name="theme-color" content="#0e1116"/>
<!-- __GA_TAG__ -->
<!-- Google Fonts (Noto Sans KR, Noto Serif KR) -->
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700&family=Noto+Serif+KR:wght@400;700&display=swap"/>
<link rel="stylesheet" href="https://unpkg.com/katex@0.16.11/dist/katex.min.css" integrity="sha384-nB0miv6/jRmo5UMMR1wu3Gz6NLsoTkbqJghGIsx//Rlm+ZU03BU6SQNC66uf4l5+" crossorigin="anonymous"/>
<script src="https://unpkg.com/vis-network@9.1.11/standalone/umd/vis-network.min.js" integrity="sha384-60H6/hL99pRYjWacRdebxM1T2R6jvWyd9GVAb7d4fp9BSfv4f0i5sWjkprnnG0cz" crossorigin="anonymous"></script>
<script src="https://unpkg.com/marked@4.3.0/marked.min.js" integrity="sha384-QsSpx6a0USazT7nK7w8qXDgpSAPhFsb2XtpoLFQ5+X2yFN6hvCKnwEzN8M5FWaJb" crossorigin="anonymous"></script>
<script src="https://unpkg.com/dompurify@3.1.6/dist/purify.min.js" integrity="sha384-+VfUPEb0PdtChMwmBcBmykRMDd+v6D/oFmB3rZM/puCMDYcIvF968OimRh4KQY9a" crossorigin="anonymous"></script>
<script src="https://unpkg.com/katex@0.16.11/dist/katex.min.js" integrity="sha384-7zkQWkzuo3B5mTepMUcHkMB5jZaolc2xDwL6VFqjFALcbeS9Ggm/Yr2r3Dy4lfFg" crossorigin="anonymous"></script>
<script src="https://unpkg.com/katex@0.16.11/dist/contrib/auto-render.min.js" integrity="sha384-43gviWU0YVjaDtb/GhzOouOXtZMP/7XUzwPTstBeZFe/+rCMvRwr4yROQP43s0Xk" crossorigin="anonymous"></script>
<script>
  // 깜빡임 방지: 페인트 전에 저장된 테마를 documentElement 에 적용. 기본값=light(사용자 요구).
  (function(){ try{ var t=localStorage.getItem('claireTheme')||'light';
    document.documentElement.setAttribute('data-theme', t); }catch(e){
    document.documentElement.setAttribute('data-theme','light'); } })();

  // 클라이언트 로딩 상태 감시 (안전 워치독): 네트워크 지연, CDN 차단 또는 런타임 예외 발생 시에도
  // '권한 확인 중', '문서 로딩…', '로딩…' 텍스트가 영구 고착되지 않도록 2.5초 내 강제 해제/정리한다.
  window.__CLAIRE_CLEAR_LOADING = function(reason) {
    try {
      var auth = document.getElementById('authstate');
      if (auth && (auth.textContent.indexOf('확인') !== -1 || auth.textContent.indexOf('로딩') !== -1)) {
        auth.textContent = '👁️ 익명 읽기전용' + (reason ? ' (' + reason + ')' : '');
      }
      var dl = document.getElementById('doclist');
      if (dl && (dl.innerHTML.indexOf('문서 로딩…') !== -1 || dl.textContent.indexOf('문서 로딩') !== -1)) {
        dl.innerHTML = (typeof doclistToolbarHtml==='function'?doclistToolbarHtml():'')+
          '<p class="hint" style="padding:10px">문서 목록 조회 지연 (새로고침 권장)</p>';
      }
      var st = document.getElementById('stat');
      if (st && (st.textContent.indexOf('로딩…') !== -1 || st.textContent.indexOf('확인') !== -1)) {
        st.textContent = reason ? '상태: ' + reason : '준비 완료';
      }
      var sk = document.getElementById('searchkind');
      if (sk && sk.textContent.indexOf('확인') !== -1) {
        sk.textContent = 'Full-Text Search';
      }
    } catch (_) {}
  };
  window.addEventListener('error', function(e) {
    console.warn('Claire global error:', e.error || e.message);
    window.__CLAIRE_CLEAR_LOADING('오류');
  });
  window.addEventListener('unhandledrejection', function(e) {
    console.warn('Claire unhandled rejection:', e.reason);
    window.__CLAIRE_CLEAR_LOADING('지연');
  });
  setTimeout(function() {
    window.__CLAIRE_CLEAR_LOADING('');
  }, 2500);
</script>
<style>
  /* --- CJK (한국어) Web Fonts (docs.asciidoctor.org) --- */
  @font-face{
    font-family:'Noto Sans KR';
    font-style:normal;
    font-weight:400;
    font-display:swap;
    src:local('Noto Sans KR Regular'),local('Noto Sans KR'),local('NotoSansKR-Regular'),
        url('/fonts/NotoSansKR-Regular.woff2') format('woff2');
  }
  @font-face{
    font-family:'Noto Sans KR';
    font-style:normal;
    font-weight:700;
    font-display:swap;
    src:local('Noto Sans KR Bold'),local('Noto Sans KR'),local('NotoSansKR-Bold'),
        url('/fonts/NotoSansKR-Bold.woff2') format('woff2');
  }
  @font-face{
    font-family:'Noto Serif KR';
    font-style:normal;
    font-weight:400;
    font-display:swap;
    src:local('Noto Serif KR Regular'),local('Noto Serif KR'),local('NotoSerifKR-Regular'),
        url('/fonts/NotoSerifKR-Regular.woff2') format('woff2');
  }
  @font-face{
    font-family:'Noto Serif KR';
    font-style:normal;
    font-weight:700;
    font-display:swap;
    src:local('Noto Serif KR Bold'),local('Noto Serif KR'),local('NotoSerifKR-Bold'),
        url('/fonts/NotoSerifKR-Bold.woff2') format('woff2');
  }
  @font-face{
    font-family:'D2Coding';
    font-style:normal;
    font-weight:400;
    font-display:swap;
    src:local('D2Coding'),local('D2 Coding'),
        url('/fonts/D2Coding.woff2') format('woff2');
  }
  @font-face{
    font-family:'D2Coding';
    font-style:normal;
    font-weight:700;
    font-display:swap;
    src:local('D2Coding Bold'),local('D2 Coding Bold'),
        url('/fonts/D2CodingBold.woff2') format('woff2');
  }
  /* 라이트 기본(:root) + 다크 옵션([data-theme=dark]). 색은 전부 CSS 변수로 — vis 캔버스
     색만 JS(THEMES)로 따로 갱신(캔버스는 CSS 변수가 안 닿음). */
  :root{
    --bg:#ffffff; --fg:#1f2328; --muted:#656d76; --bar-bg:#f6f8fa; --border:#d0d7de;
    --panel-bg:#f6f8fa; --docs-bg:#f6f8fa; --accent:#0969da; --accent2:#1a7f37;
    --chip-bg:#eaeef2; --hover:#eef1f4; --active:#ddf4ff; --net-bg:#ffffff;
    --card-bg:#ffffff; --detail-bg:#ffffff; --mark-bg:#fff8c5; --mark-fg:#633c01;
    --btn-bg:#1f883d; --btn-fg:#ffffff; --sec-bg:#eaeef2; --sec-fg:#24292f;
    --rel:#9a6700; --nodebtn-hover:#dde3ea; --shadow:rgba(31,35,40,.28);
    --detail-width:360px; --detail-compact-width:56px;
  }
  [data-theme="dark"]{
    --bg:#0e1116; --fg:#d7dbe0; --muted:#8b949e; --bar-bg:#161b22; --border:#2a2f37;
    --panel-bg:#10151c; --docs-bg:#10151c; --accent:#58a6ff; --accent2:#7ee787;
    --chip-bg:#1f2937; --hover:#161b22; --active:#1f2937; --net-bg:#0e1116;
    --card-bg:#161b22; --detail-bg:#0e1116; --mark-bg:#4d3800; --mark-fg:#ffdf5d;
    --btn-bg:#238636; --btn-fg:#ffffff; --sec-bg:#30363d; --sec-fg:#d7dbe0;
    --rel:#d29922; --nodebtn-hover:#2a3344; --shadow:rgba(1,4,9,.6);
  }
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;font-family:'Noto Sans KR','Noto Sans Korean',system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--fg);word-break:keep-all;overflow-wrap:break-word}
  body{min-height:100vh;min-height:100dvh;display:flex;flex-direction:column;overflow:hidden}
  .sr-only{position:absolute!important;width:1px!important;height:1px!important;padding:0!important;
    margin:-1px!important;overflow:hidden!important;clip:rect(0,0,0,0)!important;
    white-space:nowrap!important;border:0!important}
  :focus-visible{outline:3px solid var(--accent);outline-offset:2px}
  #bar{position:relative;z-index:30;display:flex;align-items:center;justify-content:space-between;gap:8px;padding:7px 12px;
    min-height:48px;background:var(--bar-bg);border-bottom:1px solid var(--border);
    font-size:13px;white-space:nowrap}
  /* 상태 및 안내 배너 시스템 (ClaireStatusBanner) */
  #format-warn-banner{display:none;position:relative;align-items:center;justify-content:space-between;gap:10px;padding:8px 14px;
    font-size:12.5px;line-height:1.45;z-index:20;border-bottom:1px solid var(--border);box-shadow:0 1px 3px rgba(0,0,0,.08)}
  #format-warn-banner.banner-warning{background:#fff8e6;color:#873800;border-bottom-color:#ffd591}
  [data-theme="dark"] #format-warn-banner.banner-warning{background:#2b1d0c;color:#ffc069;border-bottom-color:#593815}
  #format-warn-banner.banner-info{background:#e6f4ff;color:#0958d9;border-bottom-color:#91caff}
  [data-theme="dark"] #format-warn-banner.banner-info{background:#111d2c;color:#69b1ff;border-bottom-color:#153450}
  #format-warn-banner.banner-success{background:#f6ffed;color:#135200;border-bottom-color:#b7eb8f}
  [data-theme="dark"] #format-warn-banner.banner-success{background:#162312;color:#95de64;border-bottom-color:#274916}
  #format-warn-banner.banner-error{background:#fff1f0;color:#a8071a;border-bottom-color:#ffa39e}
  [data-theme="dark"] #format-warn-banner.banner-error{background:#2c1215;color:#ff7875;border-bottom-color:#58181c}
  .status-banner-content{display:flex;align-items:center;flex-wrap:wrap;gap:6px 10px;flex:1;min-width:0}
  .status-banner-badge{display:inline-flex;align-items:center;gap:4px;font-weight:700;white-space:nowrap;
    padding:1px 6px;border-radius:4px;background:rgba(0,0,0,.06);font-size:11.5px}
  [data-theme="dark"] .status-banner-badge{background:rgba(255,255,255,.1)}
  .status-banner-msg{word-break:keep-all;overflow-wrap:break-word}
  #format-warn-banner code{background:rgba(0,0,0,.07);padding:2px 6px;border-radius:4px;font-family:'D2Coding','D2 Coding','SFMono-Regular',Menlo,Monaco,Consolas,'Liberation Mono',monospace;font-size:11.5px;color:inherit;border:1px solid rgba(0,0,0,.08)}
  [data-theme="dark"] #format-warn-banner code{background:rgba(255,255,255,.08);border-color:rgba(255,255,255,.12)}
  .status-banner-actions{display:flex;align-items:center;gap:6px;flex-shrink:0}
  .banner-act-btn{background:rgba(0,0,0,.06);color:inherit;border:1px solid rgba(0,0,0,.15);border-radius:4px;
    padding:3px 8px;font-size:11.5px;font-weight:600;cursor:pointer;line-height:1.2;white-space:nowrap;transition:background .15s ease}
  .banner-act-btn:hover{background:rgba(0,0,0,.12);border-color:rgba(0,0,0,.25)}
  [data-theme="dark"] .banner-act-btn{background:rgba(255,255,255,.1);border-color:rgba(255,255,255,.2)}
  [data-theme="dark"] .banner-act-btn:hover{background:rgba(255,255,255,.18);border-color:rgba(255,255,255,.3)}
  #format-warn-banner .close-btn{background:transparent;border:0;color:inherit;font-size:15px;cursor:pointer;line-height:1;padding:2px 6px;border-radius:4px;opacity:.75}
  #format-warn-banner .close-btn:hover{opacity:1;background:rgba(0,0,0,.08)}
  [data-theme="dark"] #format-warn-banner .close-btn:hover{background:rgba(255,255,255,.1)}
  #bar .brand{font-weight:700;letter-spacing:-.01em;cursor:pointer;user-select:none;transition:opacity .15s ease}
  #bar .brand:hover{opacity:.85}
  #bar b{color:var(--accent2)}
  #netsearch{padding:8px 18px;border-bottom:1px solid var(--border);background:var(--panel-bg);flex-shrink:0;min-height:48px;box-sizing:border-box;display:flex;align-items:center}
  #barsearch{display:flex;align-items:center;gap:6px;width:100%}
  #barsearch input{flex:1;min-width:0}
  #viewoptions{display:flex;align-items:center;gap:6px}
  #authstate{display:inline-flex;align-items:center;justify-content:center;height:28px;min-height:28px;padding:0 8px;
    font-size:12px;color:var(--muted);background:var(--sec-bg);border:1px solid var(--border);
    border-radius:4px;white-space:nowrap;user-select:none;box-sizing:border-box}
  #morebtn{display:none;background:var(--sec-bg);color:var(--sec-fg);border:1px solid var(--border);
    border-radius:6px;cursor:pointer;font-size:18px;line-height:1}
  #morebtn:hover{background:var(--hover)}
  #themebtn{display:inline-flex;align-items:center;justify-content:center;height:28px;min-height:28px;
    background:transparent;color:var(--fg);border:1px solid var(--border);padding:0 8px;font-size:14px;
    border-radius:4px;cursor:pointer;box-sizing:border-box;transition:background .15s ease}
  #themebtn:hover{background:var(--hover)}
  #worktabs{display:none}
  #drawerbackdrop{display:none;position:fixed;inset:0;z-index:52;background:rgba(0,0,0,.45);opacity:0;pointer-events:none;transition:opacity .2s ease}
  #wrap{position:relative;display:grid;grid-template-columns:280px minmax(420px,1fr) var(--detail-width,360px);
    flex:1;min-height:0;overflow:hidden;transition:grid-template-columns .2s ease}
  body.detail-compact #wrap,
  #wrap.detail-compact{grid-template-columns:280px minmax(420px,1fr) var(--detail-compact-width,56px)}
  /* #netwrap 이 위치 기준자, #net 은 vis.Network 컨테이너(vis 가 init 시 innerHTML 을
     지우므로 — 확인됨 — #zoomctl 은 #net *밖*, 형제로 둬야 살아남는다). */
  .workspace-pane{min-width:0;min-height:0}
  #centerwrap{position:relative;display:flex;flex-direction:column;min-width:0;min-height:0;overflow:hidden;background:var(--bg)}
  #netwrap{position:relative;width:100%;height:100%;display:none;flex-direction:column;min-width:0;min-height:0;background:var(--net-bg)}
  #net{width:100%;flex:1;min-height:0;background:var(--net-bg)}
  /* 모바일 그래프 안에서 문서를 연속 전환하는 보조 탐색. 자료/그래프의 주 계층은
     그대로 두고, 좁은 화면에서만 현재 그래프 필터를 바꾸는 지역 컨트롤로 노출한다. */
  #graphdocnav{display:none}
  /* 최소 연결 차수(degree) 필터: zoomctl 좌측에 수직 배치 */
  #degctl{position:absolute;right:62px;bottom:14px;display:flex;flex-direction:column;align-items:center;
    justify-content:space-between;width:44px;height:240px;padding:8px 4px;border-radius:22px;
    border:1px solid var(--border);background:var(--sec-bg);color:var(--sec-fg);opacity:.92;
    box-shadow:0 2px 8px var(--shadow);z-index:5;box-sizing:border-box;user-select:none;gap:4px}
  #degctl:hover{opacity:1}
  #degctl .deg-label{font-size:11px;line-height:1.2;font-weight:600;color:var(--sec-fg);
    text-align:center;white-space:nowrap;margin-bottom:2px;cursor:default}
  #degctl .deg-label b{font-size:12px;color:var(--accent)}
  .deg-presets{display:flex;flex-direction:column;gap:3px;width:100%;align-items:center}
  .deg-preset-btn{width:32px;height:20px;padding:0;font-size:10px;font-weight:600;border-radius:4px;
    border:1px solid var(--border);background:var(--card-bg);color:var(--muted);cursor:pointer;line-height:18px;text-align:center}
  .deg-preset-btn:hover{color:var(--fg);border-color:var(--accent2)}
  .deg-preset-btn.active{background:var(--accent);color:#fff;border-color:var(--accent)}
  /* 모바일 핀치줌 대체 */
  #zoomctl{position:absolute;right:14px;bottom:14px;display:flex;flex-direction:column;gap:7px;z-index:5}
  #zoomctl button{width:40px;height:40px;border-radius:50%;border:1px solid var(--border);
    background:var(--sec-bg);color:var(--sec-fg);font-size:19px;line-height:1;cursor:pointer;opacity:.9}
  #zoomctl button:active{opacity:1;background:var(--hover)}
  #graphnotice{position:absolute;left:50%;top:42px;z-index:6;display:none;max-width:min(520px,80%);
    transform:translateX(-50%);padding:7px 11px;border:1px solid var(--accent);
    border-radius:18px;background:var(--card-bg);box-shadow:0 4px 16px var(--shadow);font-size:12px}
  #graphnotice.on{display:block}
  #docs{width:280px;display:flex;flex-direction:column;background:var(--docs-bg);border-right:1px solid var(--border);font-size:13px}
  #docs .dhead{padding:8px 10px;border-bottom:1px solid var(--border);flex-shrink:0}
  .docq-search-row{display:flex;align-items:center;gap:6px;width:100%}
  .docq-search-row input#docq{flex:1;min-width:0}
  #advsearchbtn{display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;padding:0;
    border-radius:4px;flex-shrink:0;cursor:pointer;background:var(--sec-bg);border:1px solid var(--border);
    color:var(--sec-fg);font-size:13px;line-height:1;transition:background .15s ease, border-color .15s ease}
  #advsearchbtn:hover{background:var(--hover)}
  #advsearchbtn.active{background:var(--hover);border-color:var(--accent2);color:var(--accent2)}
  .adv-search-pane{margin-top:6px;padding:8px;border:1px solid var(--border);border-radius:6px;
    background:var(--panel-bg);display:flex;flex-direction:column;gap:6px}
  .adv-search-pane[hidden]{display:none !important}
  .adv-search-body{display:flex;align-items:center;justify-content:space-between;gap:6px;flex-wrap:wrap}
  .adv-search-option{font-size:12px;display:inline-flex;align-items:center;gap:4px;cursor:pointer;user-select:none}
  .auth-required-badge{font-size:10px;padding:1px 5px;border-radius:3px;background:var(--sec-bg);border:1px solid var(--border);color:var(--muted);white-space:nowrap;user-select:none}
  .adv-search-hint{font-size:11px;color:var(--muted);line-height:1.3;margin:0}
  .docsearch-stat-row{display:flex;align-items:center;justify-content:space-between;padding:4px 2px 0;font-size:11.5px;color:var(--muted);min-height:18px;line-height:1.4}
  /* 즐겨찾기(고정) 섹션 */
  #pinnedhead{padding:5px 10px;font-size:11.5px;color:var(--muted);background:rgba(227,179,65,.18);flex-shrink:0}
  #pinnedlist{max-height:32%;overflow-y:auto;flex-shrink:0;border-bottom:2px solid var(--border);
    background:rgba(227,179,65,.10)}
  #doclist{flex:1;min-height:120px;overflow-y:auto}
  .doclist-toolbar{position:sticky;top:0;z-index:2;padding:6px 10px;background:var(--docs-bg);border-bottom:1px solid var(--border)}
  .doclist-toolbar select#desclines{width:100%;font-size:12px;padding:3px 6px;border:1px solid var(--border);border-radius:4px;background:var(--sec-bg);color:var(--fg);cursor:pointer}
  .doclist-toolbar select#desclines:focus{outline:none;border-color:var(--accent2)}
  .docitem{min-height:38px;padding:8px 10px;border-bottom:1px solid var(--border);cursor:pointer;position:relative;overflow:hidden}
  .docitem:hover{background:var(--hover)}
  .docitem.active{background:var(--active);border-left:3px solid var(--accent2)}
  .docitem.hidden-doc{opacity:.55}
  .docitem .doctitle-line{display:flex;align-items:flex-start;gap:4px;line-height:1.35}
  .docitem b{font-size:13.5px;line-height:1.35;flex:1;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;
    overflow:hidden;word-break:break-word}
  .docitem .st{color:var(--muted);font-size:11px;margin-left:4px;flex-shrink:0}
  .docitem.unread{border-left:3px solid var(--accent2)} .docitem.unread b{font-weight:700}
  .docitem .ubadge{color:var(--accent2);font-size:10px;margin-right:2px;vertical-align:middle;flex-shrink:0}
  .docitem .wbadge{font-size:11px;margin-right:2px;flex-shrink:0}
  .docitem .docpin-btn{background:none;border:0;padding:0 2px 0 0;font-size:13px;cursor:pointer;line-height:1.2;opacity:.7;color:var(--fg);flex-shrink:0}
  .docitem .docpin-btn:hover{opacity:1}
  .docitem .docpin-btn.pinned{opacity:1;color:#e3b341}
  .docitem .docpin-icon{font-size:13px;line-height:1.2;margin-right:2px;flex-shrink:0}
  .docitem .docpin-icon.pinned{color:#e3b341}
  .docitem p{margin:.25em 0 0;color:var(--muted);font-size:12px;line-height:1.45;overflow:hidden;
    display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical}
  #docs.lc0 .docitem p{display:none}
  #docs.lc3 .docitem p,#docs.lc4 .docitem p{-webkit-line-clamp:3}
  #showhidden{display:block;padding:8px 10px;font-size:12px;color:var(--fg);cursor:pointer;
    text-align:center;border-top:1px solid var(--border);background:var(--sec-bg)}
  #showhidden:hover{background:var(--hover)}

  body:not([data-auth-scope="owner"]) #synthbtn,
  body:not([data-auth-scope="owner"]) #synthchips,
  body:not([data-auth-scope="owner"]) #addbtn,
  body:not([data-auth-scope="owner"]) #dedupbtn,
  body:not([data-auth-scope="owner"]) .redit,
  body:not([data-auth-scope="owner"]) .rshare{display:none!important}

  /* 우측 사이드 패널 (#detailpane): 데스크톱은 3열 고정, 태블릿/모바일은 오른쪽 슬라이드 드로어 */
  #detailpane{width:var(--detail-width,360px);display:flex;flex-direction:column;background:var(--panel-bg);
    border-left:1px solid var(--border);min-height:0;overflow:hidden;transition:width .2s ease}
  #detailhead{display:flex;align-items:center;justify-content:space-between;min-height:48px;
    padding:4px 12px;border-bottom:1px solid var(--border);background:var(--bar-bg);flex:none}
  #detailhead .detailhead-tools{display:flex;align-items:center;gap:4px}
  #detailtogglebtn{min-height:34px;min-width:34px;background:var(--sec-bg);color:var(--sec-fg);border:1px solid var(--border);
    border-radius:6px;cursor:pointer;font-size:14px;display:inline-flex;align-items:center;justify-content:center;transition:transform .2s ease}
  #detailtogglebtn:hover{background:var(--hover);border-color:var(--accent)}
  #detailclose{min-height:36px;min-width:36px;background:var(--sec-bg);color:var(--sec-fg);border:1px solid var(--border);border-radius:6px;cursor:pointer;font-size:15px;display:none}
  #drawerscroll{flex:1;min-height:0;overflow-y:auto;overscroll-behavior:contain;padding:12px 14px;font-size:13px;line-height:1.5;display:flex;flex-direction:column}
  #drawerfooter{margin-top:auto;padding-top:14px;border-top:1px solid var(--border);display:flex;align-items:center}
  #repolink:hover{background:var(--hover);border-color:var(--accent)}
  #moremenu{display:flex;flex-direction:column;gap:8px;padding-bottom:0;margin-bottom:10px}
  #moremenu .tool-row{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
  #moremenu .action-btn-row{display:flex;flex-wrap:wrap;gap:4px;align-items:center}
  #moremenu .sys-row{display:flex;flex-wrap:wrap;align-items:center;gap:6px;font-size:11px;color:var(--muted);margin-top:2px}
  #moremenu .menu-section{display:flex;flex-direction:column;gap:8px;padding-top:8px;padding-bottom:12px;margin-top:2px;border-top:1px solid var(--border)}
  #moremenu .menu-section-head{display:flex;align-items:center}
  #moremenu .menu-section-title{font-size:12px;font-weight:700;color:var(--accent2);letter-spacing:.02em}
  #stat{color:var(--muted)}
  #synthchips{display:flex;gap:4px;overflow:hidden;max-width:100%;flex-wrap:wrap}
  #synthchips .chip{background:var(--chip-bg);border-radius:10px;padding:1px 7px;font-size:11px;cursor:pointer}
  #legendbar{display:flex;flex-wrap:wrap;gap:10px;padding:4px 12px;min-height:27px;
    background:var(--panel-bg);border-bottom:1px solid var(--border);font-size:11px;color:var(--muted)}
  #legendbar i{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:3px;vertical-align:middle}
  #legendbar .lgsep{margin-left:6px;opacity:.7}
  #legendbar .reltog{cursor:pointer;padding:1px 7px;border-radius:9px;background:var(--chip-bg);
    border:1px solid var(--border);color:var(--fg);font-size:11px}
  #legendbar .reltog.off{opacity:.4;text-decoration:line-through}
  #detailpane button.on{outline:2px solid var(--accent2);outline-offset:1px}

  /* 우측 메뉴 아이콘 전용 컴팩트 레일 모드 (aside#detailpane aria-hidden="false" 상태에서 축소 전환) */
  body.detail-compact #detailpane,
  #detailpane.compact-rail{width:var(--detail-compact-width,56px)}
  body.detail-compact #detailpane #detailhead,
  #detailpane.compact-rail #detailhead{padding:4px 6px;justify-content:center}
  body.detail-compact #detailpane #detailhead strong,
  #detailpane.compact-rail #detailhead strong{display:none}
  body.detail-compact #detailpane #detailtogglebtn,
  #detailpane.compact-rail #detailtogglebtn{transform:rotate(180deg)}
  body.detail-compact #detailpane #detailclose,
  #detailpane.compact-rail #detailclose{display:none}
  body.detail-compact #detailpane #drawerscroll,
  #detailpane.compact-rail #drawerscroll{padding:10px 4px;overflow-x:hidden}
  body.detail-compact #detailpane #moremenu,
  #detailpane.compact-rail #moremenu{border-bottom:0;padding-bottom:0;margin-bottom:0;align-items:center;gap:6px}
  body.detail-compact #detailpane #moremenu .menu-section,
  #detailpane.compact-rail #moremenu .menu-section{padding-bottom:0;margin-bottom:0;padding-top:6px;margin-top:4px;border-top:1px solid var(--border);align-items:center;width:100%}
  body.detail-compact #detailpane #moremenu .menu-section-head,
  #detailpane.compact-rail #moremenu .menu-section-head{display:none}
  body.detail-compact #detailpane #moremenu .tool-row,
  #detailpane.compact-rail #moremenu .tool-row{display:none}
  body.detail-compact #detailpane #moremenu .sys-row,
  #detailpane.compact-rail #moremenu .sys-row{display:none}
  body.detail-compact #detailpane #moremenu .action-btn-row,
  #detailpane.compact-rail #moremenu .action-btn-row{flex-direction:column;align-items:center;gap:8px;width:100%}
  body.detail-compact #detailpane #moremenu .action-btn-row button,
  #detailpane.compact-rail #moremenu .action-btn-row button{width:42px;height:42px;padding:0;display:inline-flex;align-items:center;justify-content:center;font-size:18px}
  body.detail-compact #detailpane #moremenu .btn-label,
  #detailpane.compact-rail #moremenu .btn-label{display:none}
  body.detail-compact #detailpane #moremenu .btn-icon,
  #detailpane.compact-rail #moremenu .btn-icon{font-size:18px;line-height:1}
  body.detail-compact #detailpane #panel,
  #detailpane.compact-rail #panel{display:none}
  body.detail-compact #detailpane #drawerfooter,
  #detailpane.compact-rail #drawerfooter{display:none}

  #panel{font-size:13px;line-height:1.5}
  #panel:not(:empty){border-top:1px solid var(--border);padding-top:10px;margin-top:10px}
  #panel h2{margin:.2em 0;font-size:18px} #panel h2 small{color:var(--muted);font-size:12px;font-weight:normal}
  #panel h3{margin:1em 0 .3em;font-size:13px;color:var(--accent2);border-top:1px solid var(--border);padding-top:6px}
  #panel ul{margin:.2em 0;padding-left:18px} #panel li{margin:.25em 0}
  #panel .doc{margin:.5em 0;padding:6px 8px;background:var(--card-bg);border-radius:5px}
  #panel .doc p{margin:.3em 0 0;color:var(--fg)} #panel a{color:var(--accent);text-decoration:none}
  #panel .doc p.src{margin-top:.45em}
  #panel .docmeta, #reader .docmeta, #rbody .docmeta, .docmeta{color:var(--muted);font-size:12px;margin:.1em 0 .6em;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}
  .docmeta .docmeta-tags{display:inline-flex;align-items:center;gap:6px;margin-left:auto;flex-wrap:wrap}
  .docmeta .trunc-tag{display:inline-flex;align-items:center;gap:4px;color:#d29922;background:rgba(210,153,34,0.12);border:1px solid rgba(210,153,34,0.3);border-radius:10px;padding:1px 7px;font-size:11px;cursor:help;white-space:nowrap;line-height:1.4}
  .docmeta .trunc-tag.trunc-appendix, .docmeta .trunc-tag-appendix{color:#3fb950;background:rgba(63,185,80,0.12);border:1px solid rgba(63,185,80,0.3)}
  .docmeta .directive-tag{display:inline-flex;align-items:center;gap:4px;color:var(--accent2,#58a6ff);background:rgba(88,166,255,0.12);border:1px solid rgba(88,166,255,0.3);border-radius:10px;padding:1px 7px;font-size:11px;cursor:help;white-space:nowrap;line-height:1.4}
  .docmeta .stt-tag{display:inline-flex;align-items:center;gap:4px;color:#a371f7;background:rgba(163,113,247,0.12);border:1px solid rgba(163,113,247,0.3);border-radius:10px;padding:1px 7px;font-size:11px;cursor:help;white-space:nowrap;line-height:1.4}
  .docmeta .trunc-tag.trunc-stt{color:#f0883e;background:rgba(240,136,62,0.12);border:1px solid rgba(240,136,62,0.35)}
  .docmeta a.stt-link{color:var(--accent);text-decoration:none;margin-left:8px;font-weight:500;cursor:pointer}
  .docmeta a.stt-link:hover{text-decoration:underline}
  .stt-trunc-banner{background:rgba(240,136,62,0.12);border:1px solid rgba(240,136,62,0.35);color:var(--fg);border-radius:6px;padding:10px 14px;margin:0 0 14px;font-size:13px;line-height:1.5}
  .stt-trunc-banner strong{color:#f0883e}
  .stt-trunc-banner code{background:var(--chip-bg);border:1px solid var(--border);border-radius:4px;padding:2px 6px;font-size:11.5px;font-family:'D2Coding','D2 Coding',monospace}
  /* STT 전사 열기 모달 */
  #sttmodal{position:fixed;top:0;left:0;right:0;bottom:0;z-index:70;background:rgba(0,0,0,0.65);display:none;align-items:center;justify-content:center;padding:max(16px,env(safe-area-inset-top)) 16px max(16px,env(safe-area-inset-bottom));backdrop-filter:blur(4px);box-sizing:border-box}
  #sttmodal.open{display:flex!important}
  .sttsheet{background:var(--bg);color:var(--fg);width:min(880px,96vw);height:min(840px,90dvh);border:1px solid var(--border);border-radius:12px;box-shadow:0 16px 48px var(--shadow);display:flex;flex-direction:column;overflow:hidden}
  .stthead{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 18px;border-bottom:1px solid var(--border);background:var(--bar-bg);flex-shrink:0}
  .stthead h2{margin:0;font-size:16px;display:flex;align-items:center;gap:6px;color:var(--fg)}
  .sttmeta{color:var(--muted);font-size:12px;margin:3px 0 0}
  .stttools{display:flex;align-items:center;gap:6px;flex-shrink:0}
  .stttools button{background:var(--sec-bg);color:var(--sec-fg);border:1px solid var(--border);border-radius:6px;font-size:12px;padding:5px 10px;cursor:pointer;display:inline-flex;align-items:center;gap:4px;transition:background .15s ease,border-color .15s ease}
  .stttools button:hover{background:var(--hover);border-color:var(--accent)}
  .stttools .sttclose{font-size:16px;padding:4px 9px;line-height:1}
  .sttsearchbar{display:flex;align-items:center;gap:10px;padding:8px 18px;border-bottom:1px solid var(--border);background:var(--card-bg);flex-shrink:0}
  .sttsearchbar input{flex:1;min-width:0;height:32px;padding:0 10px;background:var(--input-bg,var(--bg));color:var(--fg);border:1px solid var(--border);border-radius:6px;font-size:13px}
  .sttsearchbar .sttcount{font-size:12px;color:var(--muted);white-space:nowrap}
  .sttbody{flex:1;overflow-y:auto;padding:16px 20px;min-height:0;line-height:1.65;font-size:14px;overscroll-behavior:contain}
  .stt-line{display:flex;gap:12px;margin-bottom:8px;padding:4px 6px;border-radius:4px;transition:background .1s ease}
  .stt-line:hover{background:var(--hover)}
  .stt-line.highlight{background:rgba(234,179,8,0.15)}
  .stt-ts{font-family:'D2Coding','D2 Coding',monospace;font-size:12px;color:var(--accent);background:var(--chip-bg);border:1px solid var(--border);border-radius:4px;padding:1px 6px;height:fit-content;flex-shrink:0;user-select:none;cursor:pointer}
  .stt-ts:hover{background:var(--active);border-color:var(--accent)}
  .stt-text{flex:1;word-break:break-word;color:var(--fg)}
  .stt-text mark{background:#ffe066;color:#111;border-radius:2px;padding:0 2px}
  [data-theme="dark"] .stt-text mark{background:#b28b00;color:#fff}
  #panel .readbtn{background:var(--accent);color:#fff;border:0;border-radius:4px;padding:3px 10px;font-size:12.5px;cursor:pointer;margin:.2em 0}
  #panel .dochide-row{margin:.6em 0 .4em}
  #panel .dochide-label{font-size:12px;display:inline-flex;align-items:center;gap:6px;cursor:pointer;color:var(--muted)}
  #panel .dochide-label:hover{color:var(--fg)}
  #panel .dochide-label input[type=checkbox]{margin:0;width:auto;cursor:pointer}
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
  #fslider{writing-mode:vertical-lr;direction:rtl;-webkit-appearance:slider-vertical;
    width:16px;flex:1;min-height:80px;margin:0;padding:0;cursor:pointer;accent-color:var(--accent);background:transparent}
  /* ==형광== 강조 — render_detail/요약이 LLM 으로 표시한 핵심 구절(마크다운 후 <mark>). */
  mark{background:var(--mark-bg);color:var(--mark-fg);padding:0 .15em;border-radius:2px}
  /* 노드 hover 및 모바일 탭 요약 팝업 */
  #nodepop{position:fixed;z-index:70;max-width:min(340px,calc(100vw - 24px));background:var(--card-bg);color:var(--fg);
    border:1px solid var(--border);border-radius:7px;box-shadow:0 6px 22px var(--shadow);
    padding:8px 11px;font-size:12px;line-height:1.45;pointer-events:auto;display:none}
  #nodepop b{font-size:13px} #nodepop .pt{color:var(--muted);font-size:11px}
  #nodepop .po{margin-top:.4em} #nodepop i{display:inline-block;width:8px;height:8px;
    border-radius:50%;margin-right:5px;vertical-align:middle}
  #nodepop .psrc{margin-top:.55em;border-top:1px solid var(--border);padding-top:.45em}
  #nodepop .ptt{font-weight:600;color:var(--accent);margin-bottom:.25em}
  #nodepop .psb{color:var(--muted);font-size:11px;line-height:1.45}
  #nodepop .pact{margin-top:.5em;text-align:right;border-top:1px dashed var(--border);padding-top:.35em}
  #nodepop .pact button{background:transparent;color:var(--accent);border:0;padding:2px 4px;font-size:11px;cursor:pointer;font-weight:600}
  #nodepop .phead{display:flex;justify-content:space-between;align-items:flex-start;gap:6px}
  #nodepop .pclose{background:transparent;border:0;color:var(--muted);cursor:pointer;padding:0 2px;font-size:13px;line-height:1}
  body.detail-open #nodepop,
  body.drawer-open #nodepop,
  body.reader-open #nodepop{display:none!important}
  #panel input[type=radio]{width:auto;vertical-align:middle}
  /* --- 마크다운 상세(읽기 팝업 + 패널 detail) --- */
  .md{line-height:1.75;font-size:14px;word-break:keep-all;overflow-wrap:break-word}
  #reader .rbody .md{font-size:var(--read-fs,16px)}
  .md h1{font-size:1.5em} .md h2{font-size:1.3em;margin:1.1em 0 .4em;border-bottom:1px solid var(--border);padding-bottom:.2em}
  .md h3{font-size:1.12em;margin:1em 0 .35em;color:var(--fg);border:0}
  .md p{margin:.6em 0} .md ul,.md ol{margin:.5em 0;padding-left:1.5em} .md li{margin:.3em 0}
  .md strong{color:var(--fg)} .md a{color:var(--accent)}
  .md img{max-width:100%;height:auto;display:block;margin:.8em auto;border-radius:6px;border:1px solid var(--border)}
  .md em{color:var(--muted)} .md blockquote{margin:.6em 0;padding:.2em .9em;border-left:3px solid var(--border);color:var(--muted);font-family:'Noto Serif KR','Noto Serif Korean',Georgia,'Times New Roman',serif}
  .md code{background:var(--chip-bg);padding:.1em .35em;border-radius:3px;font-size:.9em;font-family:'D2Coding','D2 Coding','SFMono-Regular',Menlo,Monaco,Consolas,'Liberation Mono',monospace}
  .md pre{background:var(--card-bg);border:1px solid var(--border);border-radius:6px;padding:.8em;overflow-x:auto;max-width:100%;box-sizing:border-box}
  .md pre code{background:transparent;padding:0;border-radius:0;font-size:inherit;font-family:'D2Coding','D2 Coding','SFMono-Regular',Menlo,Monaco,Consolas,'Liberation Mono',monospace}
  .md table{border-collapse:collapse;margin:.6em 0;width:100%;max-width:100%;display:block;overflow-x:auto;box-sizing:border-box} .md th,.md td{border:1px solid var(--border);padding:.35em .65em}
  .md th{background:var(--chip-bg);font-weight:600}
  .md table td ul,.md table td ol{padding-left:1.2em;margin:.2em 0}
  .md table td li{margin:.15em 0}
  .md li > p{margin:.3em 0}
  .md li > p:first-child{margin-top:0}
  .md li > p:last-child{margin-bottom:0}
  /* --- AsciiDoc & Markdown 확장 스타일 --- */
  .md .admonitionblock{margin:1em 0;border-left:4px solid var(--accent);background:var(--card-bg);border-radius:6px;padding:.6em 1em}
  .md .admonitionblock.note{border-left-color:var(--accent)}
  .md .admonitionblock.important{border-left-color:#8250df}
  [data-theme="dark"] .md .admonitionblock.important{border-left-color:#a371f7}
  .md .admonitionblock.tip{border-left-color:var(--accent2)}
  .md .admonitionblock.warning{border-left-color:#cf222e}
  [data-theme="dark"] .md .admonitionblock.warning{border-left-color:#f85149}
  .md .admonitionblock.caution{border-left-color:var(--rel)}
  .md .admonitionblock .title,.md .admonitionblock td.icon{font-weight:700;margin-bottom:.3em;text-transform:uppercase;font-size:.85em;letter-spacing:.03em;color:var(--muted)}
  .md .quoteblock{margin:1.1em 0;padding:.6em 1.1em;border-left:3px solid var(--accent);background:var(--card-bg);border-radius:0 6px 6px 0}
  .md .quoteblock blockquote{margin:0;padding:0;border:none;color:var(--fg);font-family:'Noto Serif KR','Noto Serif Korean',Georgia,'Times New Roman',serif}
  .md .quoteblock .attribution{margin-top:.4em;font-size:.85em;color:var(--muted);text-align:right}
  .md .colist{margin:.5em 0;padding-left:1.2em;font-size:.9em;font-family:'D2Coding','D2 Coding','SFMono-Regular',Menlo,Monaco,Consolas,'Liberation Mono',monospace}
  .md .conum{display:inline-block;background:var(--accent);color:#fff;border-radius:50%;width:18px;height:18px;line-height:18px;text-align:center;font-size:11px;font-weight:bold;margin-right:4px;vertical-align:middle;font-family:'D2Coding','D2 Coding',monospace}
  .md .imageblock{margin:1em auto;text-align:center}
  .md .imageblock img{max-width:100%;height:auto;display:block;margin:0 auto;border-radius:6px;border:1px solid var(--border)}
  .md .imageblock .title{font-size:.85em;color:var(--muted);margin-top:.4em;font-style:italic}
  .md .math{font-family:'KaTeX_Math','Cambria Math','STIX Two Math','DejaVu Math TeX Gyre',Cambria,Georgia,serif;font-style:italic;color:var(--fg)}
  .md .math.inline{padding:0 .25em;background:var(--chip-bg);border-radius:3px;font-size:1.02em}
  .md .math.inline code{background:transparent;padding:0;font-family:inherit;font-size:inherit}
  .md .mathblock{margin:1em 0;padding:.8em 1.2em;background:var(--card-bg);border:1px solid var(--border);border-radius:6px;text-align:center;overflow-x:auto}
  .md .mathblock pre.math{background:transparent;border:0;padding:0;margin:0;display:inline-block;text-align:left;font-family:'KaTeX_Math','Cambria Math','STIX Two Math','DejaVu Math TeX Gyre',Cambria,Georgia,serif;font-size:1.08em}
  .md a.xref{color:var(--accent);text-decoration:none;border-bottom:1px dashed var(--accent);cursor:pointer;transition:border-color .15s ease}
  .md a.xref:hover{border-bottom-style:solid}
  .md :target{animation:target-highlight 2s ease-out;border-radius:4px}
  @keyframes target-highlight{0%{background-color:rgba(56,139,253,.25)}100%{background-color:transparent}}
  .md .lead{font-size:1.1em;line-height:1.6;font-weight:500;color:var(--fg)}

  /* --- 중앙 크게 읽기 (2단 보기 및 기본 중앙 패널 / 모바일 읽기) --- */
  #reader{width:100%;height:100%;display:flex;flex-direction:column;min-width:0;min-height:0;overflow:hidden;background:var(--bg);--read-fs:16px}
  #reader .sheet{background:var(--bg);color:var(--fg);width:100%;height:100%;min-width:0;min-height:0;border-radius:0;border:0;box-shadow:none;padding:0;display:flex;flex-direction:column;overflow:hidden}
  #reader .head{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:8px 18px;border-bottom:1px solid var(--border);background:var(--bar-bg);position:sticky;top:0;z-index:1;min-height:48px}
  #reader .head h1{margin:0;font-size:16px;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  #reader .head .rmeta{color:var(--muted);font-size:11.5px;margin-left:6px;font-weight:normal}
  #reader .rtools{display:flex;align-items:center;gap:6px;flex-shrink:0}
  #reader .rzoom{display:flex;align-items:center;gap:2px}
  #reader .rzoom button{background:var(--sec-bg);color:var(--sec-fg);border:1px solid var(--border);border-radius:4px;font-size:13px;line-height:1;padding:4px 8px;cursor:pointer}
  #reader .rzoom .fsv{color:var(--muted);font-size:11.5px;min-width:24px;text-align:center}
  #reader .rclose{background:var(--sec-bg);color:var(--sec-fg);border:1px solid var(--border);border-radius:4px;font-size:15px;line-height:1;padding:4px 8px;cursor:pointer;display:none}
  #reader .sharebox{display:none;margin:10px 24px 0;padding:8px 12px;background:var(--active);border:1px solid var(--accent);border-radius:6px;font-size:12px;gap:8px;align-items:center}
  #reader .sharebox.on{display:flex} #reader .sharebox input{flex:1;min-width:0}
  #reader .sharebox button{background:var(--accent);color:#fff;border:0;border-radius:4px;padding:3px 9px;font-size:12px;cursor:pointer}
  #reader .head .redit,#reader .head .rshare{background:var(--sec-bg);color:var(--sec-fg);border:1px solid var(--border);border-radius:4px;font-size:13px;line-height:1;padding:4px 8px;cursor:pointer;display:inline-flex;align-items:center;gap:4px;height:28px;box-sizing:border-box}
  #barsearch #openreaderbtn,
  #reader .head #opengraphbtn{
    width:104px;min-width:104px;height:28px;box-sizing:border-box;
    display:inline-flex;align-items:center;justify-content:center;gap:4px;
    padding:0 8px;border:1px solid var(--border);border-radius:4px;
    background:var(--sec-bg);color:var(--sec-fg);font-size:12.5px;
    line-height:1;cursor:pointer;flex-shrink:0;white-space:nowrap;user-select:none;
    transition:background .15s ease,border-color .15s ease
  }
  #barsearch #openreaderbtn:hover,#reader .head #opengraphbtn:hover{background:var(--hover);border-color:var(--accent)}
  #reader .rbody{padding:16px 28px max(28px,env(safe-area-inset-bottom));overflow-y:auto;overflow-x:hidden;overscroll-behavior:contain;flex:1;min-height:0;min-width:0;max-width:100%;box-sizing:border-box}
  #reader .rsection{color:var(--muted);font-size:11px;letter-spacing:.04em;text-transform:uppercase;margin:1.2em 0 .2em}

  /* 2단 보기 및 데스크톱: 크게 읽기가 기본 노출, 그래프는 호출 시에만 노출 */
  body:not([data-center-view="graph"]) #netwrap{display:none!important}
  body:not([data-center-view="graph"]) #reader{display:flex!important}
  body[data-center-view="graph"] #reader{display:none!important}
  body[data-center-view="graph"] #netwrap{display:flex!important}

  /* 중앙 화면 모드에 따른 고유 표시 */
  body[data-center-view="graph"] #pathbtn{display:inline-flex!important}
  body:not([data-center-view="graph"]) #pathbtn{display:none!important}

  /* 데스크톱/노트북 (중간 폭): 1100px 이하에서는 우측 패널을 drawer 로 (2단 보기 지원, 하단 바 비사용) */
  @media (max-width:1100px){
    #wrap{grid-template-columns:280px minmax(0,1fr)}
    #morebtn{display:inline-flex!important}

    #detailpane{position:fixed;top:0;right:0;bottom:0;
      z-index:55;width:min(400px,82vw);height:auto;max-height:none;
      transform:translateX(105%);visibility:hidden;pointer-events:none;
      border:1px solid var(--border);border-bottom:0;border-radius:0;
      box-shadow:-12px 0 32px var(--shadow);
      transition:transform .2s ease,visibility 0s linear .2s}
    body.detail-open #detailpane,
    body.drawer-open #detailpane{transform:translateX(0);visibility:visible;pointer-events:auto;
      transition:transform .2s ease}
    body.detail-open #drawerbackdrop,
    body.drawer-open #drawerbackdrop{display:block;opacity:1;pointer-events:auto}
    #detailhead{display:flex;border-radius:0}
    #detailclose{display:inline-flex!important}
    #detailtogglebtn{display:none!important}
  }

  /* 모바일 화면 (720px 이하 - 1단 보기):
     - 최상단: 브랜드 로고 및 테마 토글만 남김 (햄버거 메뉴는 하단 바로 통합)
     - 최하단: 📑 · 📊 · 🔎 · ☰ 탭 배치 (z-index: 60, 1단 보기에서 하단 바 사용)
     - 자료 화면: 아이템 탭 시 크게 읽기 호출
  */
  @media (max-width:720px){
    #bar{min-height:50px;padding:max(6px,env(safe-area-inset-top)) 12px 6px;justify-content:space-between}
    #bar .brand{font-size:14px}
    #morebtn{display:none!important}
    #viewoptions select{font-size:13px;padding:4px 6px}
    #viewoptions #authstate{min-height:38px;height:38px;padding:0 8px;font-size:12px}
    #viewoptions #themebtn{min-width:38px;min-height:38px;height:38px;padding:4px 8px}

    #wrap{display:grid;grid-template-columns:1fr;grid-template-rows:1fr;overflow:hidden;
      padding-bottom:calc(54px + env(safe-area-inset-bottom))}
    .workspace-pane,#centerwrap{grid-area:1/1;visibility:hidden!important;pointer-events:none}
    body[data-active-pane="docs"] #docs{visibility:visible!important;pointer-events:auto}
    body[data-active-pane="graph"] #centerwrap, body[data-active-pane="graph"] #netwrap{visibility:visible!important;pointer-events:auto}
    #docs{position:relative;inset:auto;width:100%;height:100%;
      max-width:none;transform:none;box-shadow:none;border:0;display:flex}
    #legendbar{flex-wrap:nowrap;overflow-x:auto;min-height:36px;padding:7px 10px;scrollbar-width:thin}
    #graphdocnav{position:relative;display:flex;align-items:center;gap:4px;min-height:52px;
      padding:4px 8px;background:var(--panel-bg);border-bottom:1px solid var(--border);z-index:12}
    #graphdocnav>button{min-width:44px;min-height:44px;padding:4px 10px;background:var(--sec-bg);
      color:var(--sec-fg);border:1px solid var(--border)}
    #graphdocpick{display:flex;align-items:center;justify-content:space-between;gap:8px;flex:1;
      min-width:0;text-align:left}
    #graphdoclabel{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    #graphdocmenu{position:absolute;left:8px;right:8px;top:calc(100% + 4px);z-index:20;
      max-height:min(52dvh,420px);padding:8px;background:var(--card-bg);border:1px solid var(--border);
      border-radius:9px;box-shadow:0 12px 32px var(--shadow)}
    #graphdocmenu[hidden]{display:none}
    #graphdocq{width:100%;min-height:44px;margin-bottom:6px}
    #graphdoclist{max-height:calc(min(52dvh,420px) - 66px);overflow-y:auto;overscroll-behavior:contain}
    .graphdocoption{display:block;width:100%;min-height:44px;padding:8px 10px;text-align:left;
      background:transparent;color:var(--fg);border:0;border-bottom:1px solid var(--border);
      border-radius:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .graphdocoption:hover,.graphdocoption[aria-current="true"]{background:var(--active)}
    .graphdocoption small{color:var(--muted);margin-left:6px}
    #graphdocempty{padding:12px 10px;color:var(--muted)}

    /* 하단 내비게이션 바: 1단 보기(720px 이하) 모바일 최하단에 고정 (z-index: 60) */
    #worktabs{display:flex;position:fixed;bottom:0;left:0;right:0;z-index:60;
      height:calc(54px + env(safe-area-inset-bottom));padding-bottom:env(safe-area-inset-bottom);
      background:var(--bar-bg);border-top:1px solid var(--border);
      align-items:stretch;justify-content:space-around;padding-left:12px;padding-right:12px}
    #worktabs button{flex:1;min-height:48px;background:transparent;color:var(--muted);
      border:0;border-radius:6px;display:flex;align-items:center;justify-content:center;
      padding:4px 0;-webkit-tap-highlight-color:transparent}
    #worktabs button .bnav-icon{font-size:24px;line-height:1}
    #worktabs button[aria-selected="true"]{color:var(--accent)}
    #worktabs button:active{background:var(--hover)}

    /* 모바일 크게 읽기 모달 */
    #reader{position:fixed!important;top:0!important;left:0!important;right:0!important;bottom:calc(54px + env(safe-area-inset-bottom))!important;height:calc(100% - 54px - env(safe-area-inset-bottom))!important;max-height:calc(100% - 54px - env(safe-area-inset-bottom))!important;width:100%!important;max-width:100%!important;min-width:0!important;min-height:0!important;box-sizing:border-box!important;overflow:hidden!important;background:var(--shadow)!important;display:none!important;visibility:hidden!important;pointer-events:none!important;z-index:45!important;padding:0!important}
    #reader.open,body.reader-open #reader{display:flex!important;visibility:visible!important;pointer-events:auto!important}
    #reader .sheet{height:100%!important;max-height:100%!important;width:100%!important;max-width:100%!important;min-width:0!important;min-height:0!important;border:0!important;border-radius:0!important;display:flex!important;flex-direction:column!important;overflow:hidden!important;box-sizing:border-box!important}
    #reader .head{padding:max(10px,env(safe-area-inset-top)) 12px 10px!important}
    #reader .head h1{font-size:18px!important}
    #reader .rzoom button,#reader .redit,#reader .rshare,#reader .rclose,#reader #opengraphbtn{min-width:44px!important;min-height:44px!important}
    #reader .rclose{display:inline-flex!important}
    #reader .rbody{padding:8px 16px max(24px,env(safe-area-inset-bottom))!important;overflow-y:auto!important;overflow-x:hidden!important;min-width:0!important;max-width:100%!important;box-sizing:border-box!important;flex:1!important;min-height:0!important}

    /* 모바일 우측 드로어 */
    #detailpane{position:fixed;top:0;right:0;bottom:calc(54px + env(safe-area-inset-bottom));
      z-index:55;width:min(340px,86vw);height:auto;max-height:none;
      transform:translateX(105%);visibility:hidden;pointer-events:none;
      border:1px solid var(--border);border-bottom:0;border-radius:0;
      box-shadow:-8px 0 24px var(--shadow);
      transition:transform .2s ease,visibility 0s linear .2s}
    #detailclose{min-width:44px;min-height:44px}
    #drawerscroll{padding:14px 16px max(16px,env(safe-area-inset-bottom))}
    #panel .hint br{display:none}
    #degctl{right:calc(max(12px,env(safe-area-inset-right)) + 52px);bottom:max(12px,env(safe-area-inset-bottom));
      width:46px;height:252px;border-radius:23px;padding:8px 4px}
    .deg-preset-btn{width:34px;height:22px;font-size:10.5px;line-height:20px}
    #zoomctl{right:max(12px,env(safe-area-inset-right));bottom:max(12px,env(safe-area-inset-bottom))}
    #zoomctl button{width:44px;height:44px}
    #barsearch #openreaderbtn{min-height:44px;min-width:44px;font-size:14px;padding:4px 10px}
    input,select,button{font-size:16px}
    .docitem{min-height:54px;padding:10px 12px}
    .docitem b{font-size:15.5px;line-height:1.35}
    .docitem p{font-size:13.5px;line-height:1.45;margin-top:4px}
    .docitem .st{font-size:12px}
    .docitem .ubadge{font-size:11px}
    .docitem .wbadge{font-size:12px}
    .docitem .docpin-btn{min-width:32px;min-height:32px;font-size:16px}
    .dday{font-size:12.5px;padding:4px 12px}
    #pinnedhead{font-size:12px;padding:6px 12px}
    #showhidden{font-size:13px;padding:10px 12px}
  }
  @media (prefers-reduced-motion:reduce){
    *{animation-duration:.001ms!important;animation-iteration-count:1!important;transition-duration:.001ms!important}
    #wrap,#detailpane,#detailtogglebtn{transition:none!important}
  }
</style></head>
<body class="ro" data-auth-scope="unknown" data-active-pane="graph" data-center-view="graph">
<header id="bar">
  <span class="brand" role="button" tabindex="0" onclick="resetHome()" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();resetHome();}" title="전체 지식 그래프 보기" aria-label="전체 지식 그래프 보기" style="display:inline-flex;align-items:center;gap:6px"><img src="/favicon.svg" width="20" height="20" alt="" aria-hidden="true" style="display:inline-block;vertical-align:middle;filter:drop-shadow(0 0 4px rgba(0,255,170,0.5))"/>Claire Bible</span>
  <div id="viewoptions">
    <span id="authstate">⏳ 권한 확인 중</span>
    <button id="themebtn" title="라이트/다크 전환" aria-label="라이트/다크 전환" onclick="toggleTheme()">🌙</button>
    <button id="morebtn" class="hamburg-btn" aria-expanded="false" aria-controls="detailpane"
      aria-label="도구 더보기" title="도구 더보기" onclick="toggleDrawer()">☰</button>
  </div>
</header>
<div id="format-warn-banner" class="status-banner banner-warning" role="status" aria-live="polite">
  <div class="status-banner-content">
    <span id="format-warn-badge" class="status-banner-badge">
      <span id="format-warn-icon">⚠️</span>
      <span id="format-warn-title">상태 알림</span>
    </span>
    <span id="format-warn-text" class="status-banner-msg"></span>
  </div>
  <div class="status-banner-actions">
    <button id="format-warn-actbtn" type="button" class="banner-act-btn" style="display:none" onclick="ClaireStatusBanner.handleAction()"></button>
    <button class="close-btn" onclick="ClaireStatusBanner.hide()" title="닫기" aria-label="안내 닫기">✕</button>
  </div>
</div>
<div id="drawerbackdrop" onclick="closeDrawer()" aria-hidden="true"></div>
<div id="wrap">
  <aside id="docs" class="workspace-pane" role="tabpanel" aria-labelledby="tab-docs" tabindex="0">
    <div class="dhead">
      <div class="docq-search-row">
        <label class="sr-only" for="docq">자료 검색</label>
        <input id="docq" placeholder="문서 검색(제목·요약)" oninput="docSearchActive=true;renderDocs(this.value)"/>
        <button id="advsearchbtn" type="button" class="sec" onclick="toggleAdvSearch()" title="고급 검색" aria-label="고급 검색" aria-expanded="false" aria-controls="advsearchpane">
          <span class="btn-icon">⚙️</span>
        </button>
      </div>
      <div id="advsearchpane" class="adv-search-pane" hidden aria-hidden="true">
        <div class="adv-search-body">
          <label class="adv-search-option" id="fts-opt-wrap" title="SQLite FTS5 기반 BM25">
            <input type="checkbox" id="sem" style="width:auto" disabled/>
            <span id="searchkind">Full-Text Search</span>
          </label>
          <label class="adv-search-option" id="semantic-opt-wrap" title="FTS + AI RRF 기반 벡터 하이브리드">
            <input type="checkbox" id="semchk" style="width:auto" disabled/>
            <span id="semkind">Semantic Search</span>
            <span id="sembadge" class="auth-required-badge">🔒 인증 필요</span>
          </label>
        </div>
        <p id="advsearchhint" class="adv-search-hint">체크 시 DB 전체 지식베이스를 검색합니다 (검색어 입력 후 Enter).</p>
      </div>
      <div class="docsearch-stat-row">
        <span id="stat" role="status" aria-live="polite">로딩…</span>
      </div>
    </div>
    <div id="pinnedhead" style="display:none">⭐ 즐겨찾기</div>
    <div id="pinnedlist"></div>
    <div id="doclist">
      <div class="doclist-toolbar">
        <select id="desclines" onchange="setDescLines(this.value)" title="목록 설명 줄수" aria-label="목록 설명 줄수">
          <option value="0">제목만 표시</option>
          <option value="3">요약 표시</option>
        </select>
      </div>
      <p class="hint" style="padding:10px">문서 로딩…</p>
    </div>
    <div id="showhidden" style="display:none" onclick="toggleShowHidden()"></div>
    <div id="hiddenlist"></div>
  </aside>
  <div id="centerwrap">
    <section id="netwrap" class="workspace-pane" role="tabpanel" aria-labelledby="tab-graph" tabindex="0">
      <div id="netsearch">
        <div id="barsearch">
          <label class="sr-only" for="q">그래프 검색</label>
          <input id="q" placeholder="그래프 노드 검색" oninput="onSearchInput(this.value)"/>
          <button id="openreaderbtn" class="sec" onclick="setCenterView('reader')" title="문서 본문 읽기로 전환" aria-label="본문 읽기"><span class="btn-icon">📖</span> <span class="btn-label">본문 읽기</span></button>
        </div>
      </div>
      <div id="legendbar" aria-label="그래프 범례와 관계 필터"></div>
      <div id="graphdocnav" aria-label="그래프 자료 전환">
        <button id="graphdocprev" onclick="stepGraphDoc(-1)" title="이전 자료" aria-label="이전 자료">‹</button>
        <button id="graphdocpick" aria-haspopup="dialog" aria-expanded="false"
          aria-controls="graphdocmenu" onclick="toggleGraphDocPicker()">
          <span id="graphdoclabel">전체 그래프</span><span aria-hidden="true">⌄</span>
        </button>
        <button id="graphdocnext" onclick="stepGraphDoc(1)" title="다음 자료" aria-label="다음 자료">›</button>
        <div id="graphdocmenu" role="dialog" aria-label="그래프에서 볼 자료 선택"
          aria-hidden="true" inert hidden>
          <label class="sr-only" for="graphdocq">자료 검색</label>
          <input id="graphdocq" placeholder="문서 검색(제목·요약)"
            oninput="renderGraphDocPicker(this.value)"/>
          <div id="graphdoclist"></div>
        </div>
      </div>
      <div id="net" aria-label="지식 그래프"></div>
      <div id="graphnotice" role="status" aria-live="polite"></div>
      <div id="degctl" aria-label="최소 연결 수 필터">
        <label for="fslider" class="deg-label" title="최소 연결 수">≥<b id="fmin">0</b></label>
        <div id="degpresets" class="deg-presets" aria-label="밀집도 빠른 설정">
          <button type="button" class="deg-preset-btn" data-deg="5" onclick="setDeg(5)" title="주요 허브 (5+)">5+</button>
          <button type="button" class="deg-preset-btn" data-deg="2" onclick="setDeg(2)" title="핵심 노드 (2+)">2+</button>
          <button type="button" class="deg-preset-btn" data-deg="1" onclick="setDeg(1)" title="연결된 노드 (1+)">1+</button>
          <button type="button" class="deg-preset-btn active" data-deg="0" onclick="setDeg(0)" title="전체 노드 (0+)">0+</button>
        </div>
        <input id="fslider" type="range" orient="vertical" min="0" max="0" value="0" oninput="setDeg(this.value)" aria-label="최소 연결 수" title="최소 연결 수 조절"/>
      </div>
      <div id="zoomctl" aria-label="그래프 카메라">
        <button onclick="zoomBtn(1)" title="확대" aria-label="그래프 확대">+</button>
        <button onclick="zoomBtn(-1)" title="축소" aria-label="그래프 축소">−</button>
        <button onclick="fitGraphContext()" title="강조된 항목 맞춤" aria-label="강조된 항목 맞춤">⌖</button>
        <button onclick="resetGraphCamera()" title="전체 그래프 맞춤" aria-label="전체 그래프 맞춤">↺</button>
      </div>
    </section>
    <div id="reader" role="dialog" aria-modal="true" aria-labelledby="rtitle" aria-hidden="true"
      onclick="if(event.target===this && mobileMQ.matches)closeReader()">
      <div class="sheet" tabindex="-1">
        <div class="head">
          <h1 id="rtitle">문서를 선택하세요</h1>
          <div class="rtools">
            <div class="rzoom">
              <button onclick="setReadFS(-2)" title="글자 작게" aria-label="글자 작게">A−</button>
              <span class="fsv" id="rfs">16</span>
              <button onclick="setReadFS(2)" title="글자 크게" aria-label="글자 크게">A+</button>
            </div>
            <button class="redit" id="reditbtn" onclick="editDocTitle()" title="제목 수정" aria-label="제목 수정">✏️</button>
            <button class="rshare" onclick="shareDoc()" title="공유 링크 만들기" aria-label="공유 링크 만들기">🔗</button>
            <button id="opengraphbtn" class="sec" onclick="openDocGraph(curReaderDoc||activeDoc)" title="현재 선택된 자료를 지식 그래프로 보기" aria-label="그래프 보기"><span class="btn-icon">📊</span> <span class="btn-label">그래프 보기</span></button>
            <button class="rclose" onclick="closeReader()" title="닫기(ESC)" aria-label="읽기 닫기">✕</button>
          </div>
        </div>
        <div class="sharebox" id="sharebox"></div>
        <div class="rbody" id="rbody">
          <p class="hint" style="padding:20px;text-align:center">왼쪽 목록에서 문서를 선택하면 본문이 표시됩니다.</p>
        </div>
      </div>
    </div>
    <div id="sttmodal" class="sttmodal" role="dialog" aria-modal="true" aria-labelledby="stttitle" style="display:none" onclick="if(event.target===this)closeSttReader()">
      <div class="sttsheet" tabindex="-1">
        <div class="stthead">
          <div>
            <h2 id="stttitle">🎙️ 음성 전사 (STT)</h2>
            <p class="sttmeta" id="sttmeta"></p>
          </div>
          <div class="stttools">
            <button class="sec" onclick="copySttText(false)" title="전사 텍스트만 복사">📋 텍스트 복사</button>
            <button class="sec" onclick="copySttText(true)" title="타임스탬프 포함 복사">⏱️ 타임스탬프 복사</button>
            <button class="rclose sttclose" onclick="closeSttReader()" title="닫기(ESC)" aria-label="전사 닫기">✕</button>
          </div>
        </div>
        <div class="sttsearchbar">
          <input id="sttq" placeholder="전사 내용 검색 (단어 또는 타임스탬프)..." oninput="filterSttLines(this.value)"/>
          <span id="sttcount" class="sttcount"></span>
        </div>
        <div id="sttbody" class="sttbody"></div>
      </div>
    </div>
  </div>
  <aside id="detailpane" role="region" aria-label="문맥 상세" tabindex="-1">
    <div id="detailhead">
      <strong>메뉴 &amp; 상세</strong>
      <div class="detailhead-tools">
        <button id="detailtogglebtn" type="button" onclick="toggleDetailCompact()" title="우측 메뉴 축소/펼치기" aria-label="우측 메뉴 축소/펼치기">›</button>
        <button id="detailclose" onclick="closeDrawer()" aria-label="상세 닫기">✕</button>
      </div>
    </div>
    <div id="drawerscroll">
      <div id="moremenu" aria-label="도구 및 그래프 설정">
        <div class="action-btn-row">
          <button id="addbtn" class="sec" onclick="openIngest()" title="URL·텍스트를 그래프에 적재" aria-label="적재"><span class="btn-icon">➕</span> <span class="btn-label">적재</span></button>
          <button id="dedupbtn" class="sec" onclick="openDedup()" title="근사 중복 문서를 찾아 병합" aria-label="중복정리"><span class="btn-icon">♻️</span> <span class="btn-label">중복정리</span></button>
        </div>
        <div id="graph-section" class="menu-section" aria-label="그래프 및 문서 도구">
          <div class="menu-section-head">
            <span class="menu-section-title" id="menu-section-title">문서와 그래프</span>
          </div>
          <div class="action-btn-row">
            <button id="pathbtn" class="sec" onclick="togglePathMode()" title="두 노드 사이 연결 경로 찾기" aria-label="경로"><span class="btn-icon">🔗</span> <span class="btn-label">경로</span></button>
            <button id="synthbtn" onclick="synth()" title="종합 (0)"><span class="btn-icon">🧩</span> <span class="btn-label">종합 (0)</span></button>
            <span id="synthchips"></span>
          </div>
        </div>
      </div>
      <div id="panel"></div>
      <div id="drawerfooter">
        <a id="repolink" class="sec" href="__SOURCE_BASE_URL__" target="_blank" rel="noopener noreferrer" title="소스 리포지토리 (__GITHUB_REPOSITORY__)" aria-label="GitHub 리포지토리" style="display:inline-flex;align-items:center;gap:4px;text-decoration:none;padding:3px 8px;border:1px solid var(--border);border-radius:4px;font-size:12px;color:var(--sec-fg);background:var(--sec-bg)"><span class="btn-icon">🐙</span> <span class="btn-label">GitHub</span></a>
      </div>
    </div>
  </aside>
</div>
<nav id="worktabs" role="tablist" aria-label="작업 영역">
  <button id="tab-docs" role="tab" aria-selected="false" aria-controls="docs" data-pane="docs" onclick="revealWorkspace('docs')" title="자료" aria-label="자료">
    <span class="bnav-icon">📑</span>
  </button>
  <button id="tab-graph" role="tab" aria-selected="true" aria-controls="netwrap" data-pane="graph" onclick="revealWorkspace('graph')" title="그래프" aria-label="그래프">
    <span class="bnav-icon">📊</span>
  </button>
  <button id="tab-search" onclick="focusMobileSearch()" title="검색" aria-label="검색">
    <span class="bnav-icon">🔎</span>
  </button>
  <button id="tab-menu" class="hamburg-btn" aria-expanded="false" aria-controls="detailpane" onclick="toggleDrawer()" title="메뉴" aria-label="메뉴">
    <span class="bnav-icon">☰</span>
  </button>
</nav>
<!-- 노드 hover 시 마우스 위치에 뜨는 작은 요약 팝업(우측 패널 미리보기 대체). -->
<div id="nodepop"></div>
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

// --- 반응형 환경 감지 & 작업영역 상태 (최상단 안전 선언) ---
const mobileMQ = window.matchMedia('(max-width:720px)');
const compactMQ = window.matchMedia('(max-width:1100px)');
const toolbarMQ = window.matchMedia('(max-width:1500px)');
const reducedMotionMQ = window.matchMedia('(prefers-reduced-motion:reduce)');
const paneNames=['docs','graph'];
let activePane='graph', detailOpen=false, centerView='graph', drawerOpen=false;
let detailReturnFocus=null, docSearchActive=false;
let graphCamera = null, preservingGraphCamera = false, netBusy = false;
let isDraggingNode = false, settleTimer = null;
let lastNetSize = {w:0, h:0};
let allTypes = [], allRelTypes = [], allDocs = [];
let net = null, allNodes = null, allEdges = null;
let curMinDeg = 0, activeDoc = null, highlightSet = null, selectedNodeId = null, hoverTimer = null;
let lastSelectedDocId = null;
try{
  const savedLastDoc = localStorage.getItem('claireLastDoc');
  if(savedLastDoc) lastSelectedDocId = savedLastDoc;
}catch(_){}

function recordSelectedDoc(id){
  if(!id) return;
  lastSelectedDocId = id;
  try{ localStorage.setItem('claireLastDoc', id); }catch(_){}
}

// --- 웹 표준 History API 기반 모바일 뒤로가기/내비게이션 관리 ---
let isPoppingHistory = false;
let lastPushedHistory = null;

function getActiveModalName(){
  const r = document.getElementById('reader');
  const gdm = document.getElementById('graphdocmenu');
  if(mobileMQ.matches && r && r.classList.contains('open')){
    return 'reader';
  }
  if((compactMQ.matches || mobileMQ.matches) && (drawerOpen || detailOpen)){
    return 'drawer';
  }
  if(gdm && !gdm.hidden){
    return 'graphdocmenu';
  }
  return null;
}

function getAppHistorySnapshot(){
  return {
    pane: activePane || 'docs',
    modal: getActiveModalName(),
    docId: curReaderDoc || activeDoc || null,
    nodeId: selectedNodeId || null,
  };
}

function pushAppHistory(patch = {}){
  if(isPoppingHistory) return;
  if(typeof window === 'undefined' || !window.history || typeof window.history.pushState !== 'function') return;
  const base = getAppHistorySnapshot();
  const next = Object.assign({}, base, patch);
  if(lastPushedHistory &&
     lastPushedHistory.pane === next.pane &&
     lastPushedHistory.modal === next.modal &&
     lastPushedHistory.docId === next.docId &&
     lastPushedHistory.nodeId === next.nodeId){
    return;
  }
  lastPushedHistory = next;
  try{ window.history.pushState(next, ''); }catch(_){}
  if(typeof window.gtag === 'function'){
    try{
      let virtPath = '/';
      if(next.modal === 'reader' && next.docId){
        virtPath = '/doc/' + next.docId;
      } else if(next.nodeId){
        virtPath = '/node/' + next.nodeId;
      } else if(next.pane && next.pane !== 'graph'){
        virtPath = '/' + next.pane;
      }
      window.gtag('event', 'page_view', {
        page_title: document.title,
        page_location: window.location.origin + virtPath
      });
    }catch(_){}
  }
}

function replaceAppHistory(patch = {}){
  if(isPoppingHistory) return;
  if(typeof window === 'undefined' || !window.history || typeof window.history.replaceState !== 'function') return;
  const base = getAppHistorySnapshot();
  const next = Object.assign({}, base, patch);
  lastPushedHistory = next;
  try{ window.history.replaceState(next, ''); }catch(_){}
}

function docWithMostNodes(){
  if(!allDocs || !allDocs.length) return null;
  const counts = new Map();
  if(allNodes){
    allNodes.forEach(n=>{
      if(n.hidden || (typeof n.id==='string' && n.id.indexOf('cl_')===0)) return;
      (n.sources||[]).forEach(docId=>{
        counts.set(docId, (counts.get(docId)||0) + 1);
      });
    });
  }
  let bestDoc = null, maxCount = -1;
  const visibleDocs = allDocs.filter(d => d.hidden !== 1);
  const targetDocs = visibleDocs.length ? visibleDocs : allDocs;
  for(const d of targetDocs){
    const count = counts.get(d.id) || 0;
    if(count > maxCount){
      maxCount = count;
      bestDoc = d;
    }
  }
  return bestDoc ? bestDoc.id : (targetDocs[0]?.id || null);
}

function getRecentDocId(){
  let target = activeDoc || curReaderDoc || lastSelectedDocId;
  if(!target){
    try{
      const saved = localStorage.getItem('claireLastDoc');
      if(saved) target = saved;
    }catch(_){}
  }
  if(target && allDocs && allDocs.length){
    const exists = allDocs.find(d => d.id === target && d.hidden !== 1);
    if(exists) return exists.id;
  }
  return null;
}
let clusterEdges = null, clusterAnchor = null, searchDebounce = null;
let currentSearchSeq = 0, currentSearchAbort = null;
function cancelServerSearch(){
  if(currentSearchAbort){
    try{ currentSearchAbort.abort(); }catch(_){}
    currentSearchAbort = null;
  }
}
let synthSet = new Set();
let AUTH_SCOPE='unknown'; let READONLY=true;
let relFilter = null;
let pathMode = false, pathPicks = [], pathNodes = null, pathEdges = null;
let edgeLabelsByZoom = false, selectedEdgeIds = new Set();
let graphStabilized = false;
let detailCompact = false;
try{
  const savedDetailCompact = localStorage.getItem('claireDetailCompact');
  if(savedDetailCompact === 'true') detailCompact = true;
}catch(_){}

function toggleDetailCompact(force){
  if(typeof force === 'boolean'){
    detailCompact = force;
  }else{
    detailCompact = !document.body.classList.contains('detail-compact');
  }
  try{ localStorage.setItem('claireDetailCompact', detailCompact ? 'true' : 'false'); }catch(_){}
  syncWorkspaceLayout();
}

const paneEls = {
  get docs(){ return document.getElementById('docs'); },
  get graph(){ return document.getElementById('netwrap'); }
};
const paneTabs = {
  get docs(){ return document.getElementById('tab-docs'); },
  get graph(){ return document.getElementById('tab-graph') || document.getElementById('tab-docs'); }
};

const panel = document.getElementById('panel');
function canWrite(){ return AUTH_SCOPE==='owner'; }
// 함수로 둔 이유: READONLY 는 /whoami 가 비동기로 확정하므로, 호출 시점 기준으로
// 종합 안내 줄을 넣을지 뺄지 판단해야 한다(고정 문자열이면 초기 로드 시점 값에 박제됨).
function defaultHint(){
  const synthLine = canWrite()
    ? '• <b>Ctrl+클릭</b> 또는 상세의 <b>➕ 종합에 추가</b>로 여러 노드를 모아 종합<br>'
    : '';
  return '<p class="hint">노드를 클릭하면 관찰·출처 문서·연결이 표시됩니다.<br><br>'+synthLine+
    '• 다른 노드에 <b>1.5초</b> 올리면 마우스 옆에 <b>요약 팝업</b>(더 끌면 출처 문서까지)<br>'+
    '• <b>자료</b>에서 문서를 고르면 그래프가 강조되고, <b>📖</b>로 크게 읽습니다.<br>'+
    '• 상단 <b>⋯ 도구</b>의 <b>🌙/🌞</b>로 라이트·다크를 전환합니다.</p>';
}
if(panel) panel.innerHTML = defaultHint();
function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}

// --- 노드 hover 및 모바일 탭 요약 팝업(마우스/탭 위치) — fetch 없이 클라 데이터(allNodes)만 쓴다 ---
// vis hoverNode 이벤트는 진입 위치를 안 주므로 #net 위 mousemove 로 커서 좌표를 추적해 둔다.
let mouseXY={x:0,y:0};
function canShowNodePop(id){
  const isMobile = (compactMQ && compactMQ.matches) || (mobileMQ && mobileMQ.matches);
  if(document.body.classList.contains('detail-open') || document.body.classList.contains('reader-open') || document.body.classList.contains('drawer-open')) return false;
  if(isMobile && (drawerOpen || detailOpen)) return false;
  return true;
}
window.addEventListener('pointerdown', e=>{
  const np = document.getElementById('nodepop');
  if(np && np.style.display!=='none' && !np.contains(e.target) && !e.target.closest('#net')){
    hideNodePop();
  }
}, {passive:true});
const netEl = document.getElementById('net');
if(netEl) netEl.addEventListener('mousemove', e=>{ mouseXY.x=e.clientX; mouseXY.y=e.clientY; });
const nodepop = document.getElementById('nodepop');
let popReqId=null;        // 현재 팝업이 다루는 노드 id — 늦게 온 fetch 응답(stale) 무시용
let popExpandTimer=null;  // '좀 더 기다리면' 출처 문서를 펼치는 타이머
// id 의 요약 팝업을 (x,y) 위치에 띄운다. 좌표 생략 시 #net 의 커서 추적값(mouseXY) 사용
// → 그래프 hover 는 좌표 없이, 우측 '이 문서의 노드' hover 는 버튼 진입 좌표를 넘긴다.
// 1단계: 클라 데이터(이름·타입·연결수+관찰 첫 줄)로 즉시. 2단계: node fetch 로 관찰 3개.
// 3단계: 더 끌면 출처 문서 1건(제목+글)을 덧붙인다(점진적 공개, 사용자 요구).
function showNodePop(id, x, y){
  if(!canShowNodePop(id)){ hideNodePop(); return; }
  const n=allNodes&&allNodes.get(id); if(!n){ hideNodePop(); return; }
  popReqId=id; clearTimeout(popExpandTimer);
  const px = x==null?mouseXY.x:x, py = y==null?mouseXY.y:y;
  nodepop.dataset.x=px; nodepop.dataset.y=py;   // fetch 보강 후 재배치에 쓰려고 보관
  const c=TYPE_COLORS[n.group]||'#8b949e';
  const isMobile = (compactMQ && compactMQ.matches) || (mobileMQ && mobileMQ.matches);
  const head='<div class=phead><div><b>'+esc(n.label)+'</b> <span class=pt>'+esc(n.group||'')+'</span></div>'+
    '<button type="button" class=pclose onclick="event.stopPropagation();hideNodePop()" aria-label="닫기">✕</button></div>'+
    '<div class=pt><i style="background:'+c+'"></i>연결 '+(n.degree||0)+'개</div>';
  const foot=isMobile?'<div class=pact><button type="button" onclick="event.stopPropagation();hideNodePop();openDetailPane()">자세히 보기 ›</button></div>':'';
  nodepop.innerHTML=head+(n.obs?'<div class=po>'+esc(n.obs)+'</div>':'')+foot;
  nodepop.style.display='block';
  positionPop(px, py);                  // 표시 후(폭/높이 확정) 화면 밖으로 안 나가게 배치
  fetch('node?id='+encodeURIComponent(id)).then(r=>r.json()).then(d=>{
    if(!canShowNodePop(id) || popReqId!==id || nodepop.style.display==='none' || !d || d.error) return;  // 이미 떠났거나 상세 열렸으면 무시
    const obs=(d.observations||[]).slice(0,3);   // 관찰 최대 3개(설명이 너무 적던 문제)
    const base=head + obs.map(o=>'<div class=po>'+esc((o||'').slice(0,200))+'</div>').join('');
    nodepop.innerHTML=base+foot; positionPop(+nodepop.dataset.x, +nodepop.dataset.y);
    const docs=d.documents||[];
    if(docs.length){                    // 좀 더 머물면 출처 문서 1건을 덧붙임
      popExpandTimer=setTimeout(()=>{
        if(!canShowNodePop(id) || popReqId!==id || nodepop.style.display==='none') return;
        nodepop.innerHTML=base+popSource(docs[0])+foot;
        positionPop(+nodepop.dataset.x, +nodepop.dataset.y);
      }, 1400);
    }
  }).catch(()=>{});
}
// 팝업 하단의 출처 문서 한 건 — 제목 + 글(요약 우선, 없으면 상세 앞부분) 일부.
function popSource(d){
  const body=((d.summary||d.detail||'').replace(/\\s+/g,' ').trim());
  return '<div class=psrc><div class=ptt>📄 '+esc(d.title||'(제목 없음)')+'</div>'+
    (body?'<div class=psb>'+esc(body.slice(0,240))+(body.length>240?'…':'')+'</div>':'')+'</div>';
}
function positionPop(x, y){
  const pad=14, pw=nodepop.offsetWidth || 280, ph=nodepop.offsetHeight || 120;
  let nx=x+pad, ny=y+pad;
  const isMobile = (compactMQ && compactMQ.matches) || (mobileMQ && mobileMQ.matches);
  if(isMobile){
    if(nx + pw > window.innerWidth - 8){
      nx = Math.max(12, window.innerWidth - pw - 12);
    }
    if(ny + ph > window.innerHeight - 56){
      ny = Math.max(50, y - pad - ph);
    }
  } else {
    if(nx+pw > window.innerWidth-4) nx=x-pad-pw;     // 오른쪽 넘치면 커서 왼쪽으로
    if(ny+ph > window.innerHeight-4) ny=y-pad-ph;    // 아래 넘치면 커서 위로
  }
  nodepop.style.left=Math.max(4,nx)+'px'; nodepop.style.top=Math.max(4,ny)+'px';
}
function hideNodePop(){
  clearTimeout(hoverTimer); hoverTimer=null;
  popReqId=null; clearTimeout(popExpandTimer); popExpandTimer=null;
  if(nodepop) nodepop.style.display='none';
}

// 타입별 노드 그룹 색(테마별 테두리). 테마 전환 시 다시 만들어 setOptions 로 적용.
function buildGroups(){ const g={}, th=T();
  allTypes.forEach(t=>{ const c=TYPE_COLORS[t]||'#8b949e';
    g[t]={color:{background:c,border:th.nodeBorder,highlight:{background:c,border:th.lit},hover:{background:c,border:th.lit}}}; });
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

// 마크다운/AsciiDoc → 안전한 HTML. DOMPurify 로 스크랩 본문 유래 위험 태그 제거.
const DOMPURIFY_OPTS = {
  ADD_ATTR: ['target', 'aria-hidden', 'data-math', 'style', 'xmlns', 'display', 'class'],
  ADD_TAGS: ['mark', 'math', 'semantics', 'mrow', 'mi', 'mo', 'mn', 'msup', 'msub', 'msubsup', 'mfrac', 'munder', 'mover', 'munderover', 'mtable', 'mtr', 'mtd', 'mtext', 'mspace', 'mpadded', 'mphantom', 'annotation', 'span']
};
function renderMarkdown(src){
  if(!src) return '';
  const raw=String(src);
  const fallback=()=>esc(raw).replace(/\\\\r?\\\\n/g,'<br>');
  const parser=window.marked, purifier=window.DOMPurify;
  if(!parser||!purifier||typeof purifier.sanitize!=='function'||
     (typeof parser.parse!=='function'&&typeof parser!=='function')) return fallback();
  try{
    const s=raw.replace(/==([^=]+?)==/g,'<mark>$1</mark>');
    const html=typeof parser.parse==='function'?parser.parse(s):parser(s);
    return purifier.sanitize(html, DOMPURIFY_OPTS);
  }catch(e){ return fallback(); }
}

function splitTableCells(line){
  var raw=(line||'').trim();
  if(!raw.startsWith('|')) return [raw];
  raw=raw.substring(1);
  if(raw.endsWith('|') && !raw.endsWith('\\\\|')){
    raw=raw.substring(0, raw.length - 1);
  }
  var placeholder='\uE000';
  var parts=raw.replace(/\\\\\\|/g, placeholder).split('|');
  return parts.map(function(p){
    return p.replace(new RegExp(placeholder, 'g'), '|').trim();
  });
}
function parseColsAttr(text){
  if(!text) return null;
  var m=text.match(/cols=["']?([^"'\\]]+)["']?/i);
  var colsVal=m ? m[1].trim() : text.replace(/[\\[\\]]/g, '').trim();
  var starM=colsVal.match(/^(\\d+)\\*/);
  if(starM) return parseInt(starM[1], 10);
  if(colsVal.indexOf(',') !== -1){
    var parts=colsVal.split(',').filter(function(p){ return p.trim().length > 0; });
    var total=0;
    for(var i=0; i<parts.length; i++){
      var sm=parts[i].trim().match(/^(\\d+)\\*/);
      if(sm) total += parseInt(sm[1], 10);
      else total += 1;
    }
    return total > 0 ? total : null;
  }
  if(/^\\d+$/.test(colsVal)) return parseInt(colsVal, 10);
  return null;
}
function parseCellSpec(specStr){
  var res={colspan:1, rowspan:1, align:null, style:null};
  if(!specStr) return res;
  var spec=specStr.trim();
  var mSpan=spec.match(/(\\\\d+)?\\\\.(\\\\d+)\\\\+/);
  if(mSpan){
    if(mSpan[1]) res.colspan=parseInt(mSpan[1], 10);
    if(mSpan[2]) res.rowspan=parseInt(mSpan[2], 10);
  }else{
    var mCol=spec.match(/(?<!\\\\.)(\\\\d+)\\\\+/);
    if(mCol) res.colspan=parseInt(mCol[1], 10);
    var mRow=spec.match(/\\\\.(\\\\d+)\\\\+/);
    if(mRow) res.rowspan=parseInt(mRow[1], 10);
    var mDup=spec.match(/^(\\\\d+)\\\\*$/);
    if(mDup) res.colspan=parseInt(mDup[1], 10);
  }
  if(spec.indexOf('^') !== -1) res.align='center';
  else if(spec.indexOf('>') !== -1) res.align='right';
  else if(spec.indexOf('<') !== -1) res.align='left';
  var mStyle=spec.match(/([a-z])(?=\\\\|$)/i);
  if(mStyle) res.style=mStyle[1].toLowerCase();
  return res;
}
function extractCellsAndCols(tableLines, explicitCols){
  var placeholder='\uE000';
  var cellTokenRe=/(?:^|(?<=\\s))((?:\\d*\\.?\\d+\\+|\\d+\\*)?[\\^<>]?[a-z]?|[\\^<>]?[a-z]?)\\|/g;
  var cells=[];
  var firstLineCols=null;
  var firstBlockCols=0;
  var inFirstBlock=true;

  for(var i=0; i<tableLines.length; i++){
    var raw=tableLines[i].trim();
    if(!raw){
      if(inFirstBlock && cells.length > 0) inFirstBlock=false;
      continue;
    }
    var safe=raw.replace(/\\\\\\|/g, placeholder);
    var matches=[];
    var m;
    cellTokenRe.lastIndex=0;
    while((m=cellTokenRe.exec(safe)) !== null){
      matches.push({index: m.index, spec: m[1] || '', length: m[0].length});
    }

    if(matches.length === 0 || matches[0].index > 0){
      if(cells.length > 0 && matches.length === 0){
        cells[cells.length - 1].text += '\\n' + raw.replace(/\\\\\\|/g, '|');
        continue;
      }else if(matches.length === 0){
        var specObj=parseCellSpec('');
        cells.push({text: raw.replace(/\\\\\\|/g, '|'), spec: '', colspan: specObj.colspan, rowspan: specObj.rowspan, align: specObj.align, style: specObj.style});
        if(inFirstBlock) firstBlockCols += specObj.colspan;
        continue;
      }
    }

    var lineCellsCount=0;
    for(var j=0; j<matches.length; j++){
      var cur=matches[j];
      var spec=(cur.spec || '').trim();
      var specObj=parseCellSpec(spec);
      var startPos=cur.index + cur.length;
      var endPos=(j + 1 < matches.length) ? matches[j + 1].index : safe.length;
      var cellText=safe.substring(startPos, endPos).trim().replace(new RegExp(placeholder, 'g'), '|');
      cells.push({text: cellText, spec: spec, colspan: specObj.colspan, rowspan: specObj.rowspan, align: specObj.align, style: specObj.style});
      lineCellsCount += specObj.colspan;
      if(inFirstBlock) firstBlockCols += specObj.colspan;
    }
    if(firstLineCols === null && lineCellsCount > 0){
      firstLineCols=lineCellsCount;
    }
  }

  var numCols=explicitCols;
  if(!numCols || numCols <= 0){
    if(firstLineCols && firstLineCols > 1) numCols=firstLineCols;
    else if(firstBlockCols > 1) numCols=firstBlockCols;
    else numCols=1;
  }
  return {cells: cells, numCols: numCols};
}
function parseAdocTableRows(tableLines, explicitCols){
  var res=extractCellsAndCols(tableLines, explicitCols);
  var cells=res.cells;
  var numCols=res.numCols;
  if(!cells || cells.length === 0) return [];
  if(numCols <= 0) numCols=1;

  var rows=[];
  var cellIdx=0;
  var occupied=[];
  for(var c=0; c<numCols; c++) occupied.push(0);

  while(cellIdx < cells.length){
    var rowCells=[];
    var col=0;
    while(col < numCols && cellIdx < cells.length){
      if(occupied[col] > 0){
        occupied[col]--;
        col++;
        continue;
      }
      var cell=cells[cellIdx++];
      rowCells.push(cell);
      if(cell.rowspan > 1){
        for(var spanC=0; spanC<cell.colspan; spanC++){
          if(col + spanC < numCols){
            occupied[col + spanC]=cell.rowspan - 1;
          }
        }
      }
      col += cell.colspan;
    }
    while(col < numCols){
      if(occupied[col] > 0) occupied[col]--;
      col++;
    }
    if(rowCells.length > 0) rows.push(rowCells);
  }
  return rows;
}
function renderTableHtml(tableLines, blockMeta, anchorId){
  var explicitCols=parseColsAttr(blockMeta.cols || '');
  var rows=parseAdocTableRows(tableLines, explicitCols);
  if(!rows || rows.length === 0) return '';
  var idAttr=anchorId ? ' id=\"' + esc(anchorId) + '\"' : '';
  var tHtml='<table' + idAttr + '>';
  if(blockMeta.title) tHtml += '<caption>' + esc(blockMeta.title) + '</caption>';

  function renderCell(cell, tag){
    var attrs=[];
    if(cell.rowspan > 1) attrs.push('rowspan=\"' + cell.rowspan + '\"');
    if(cell.colspan > 1) attrs.push('colspan=\"' + cell.colspan + '\"');
    if(cell.align) attrs.push('style=\"text-align:' + cell.align + '\"');
    var attrStr=attrs.length > 0 ? ' ' + attrs.join(' ') : '';
    var text=(cell.text || '').trim();
    var innerHtml='';
    if(cell.style==='a' || (text.indexOf('\\n')!==-1 && /(?:^|\\n)\\s*[\\*\\-\\.]\\s+/.test(text))){
      innerHtml=convertAsciidocToHtml(text);
      if(innerHtml.startsWith('<p>') && innerHtml.endsWith('</p>') && (innerHtml.match(/<p>/g)||[]).length===1 && innerHtml.indexOf('\\n')===-1){
        innerHtml=innerHtml.substring(3, innerHtml.length-4);
      }
    }else if(text.indexOf('\\n\\n')!==-1){
      innerHtml=convertAsciidocToHtml(text);
    }else{
      var rawLines=text.split('\\n');
      var parts=rawLines.map(function(l){ return inlineAdocFormat(l); });
      innerHtml=parts.join(' ');
    }
    return '<' + tag + attrStr + '>' + innerHtml + '</' + tag + '>';
  }

  tHtml += '<thead><tr>' + rows[0].map(function(c){ return renderCell(c, 'th'); }).join('') + '</tr></thead>';
  if(rows.length > 1){
    tHtml += '<tbody>';
    for(var r=1; r<rows.length; r++){
      tHtml += '<tr>' + rows[r].map(function(c){ return renderCell(c, 'td'); }).join('') + '</tr>';
    }
    tHtml += '</tbody>';
  }
  tHtml += '</table>';
  return tHtml;
}

function inlineAdocFormat(text){
  if(!text) return '';
  var s=esc(text);
  var codeSpans=[];
  s=s.replace(/`(?![\\s])([^`\\n]+?)(?<![\\s])`/g, function(_, m1){
    codeSpans.push('<code>'+m1+'</code>');
    return '\\x00ADOCCODE'+(codeSpans.length-1)+'\\x00';
  });
  s=s.replace(/\\+\\+(?![\\s])([^\\+\\n]+?)(?<![\\s])\\+\\+/g, function(_, m1){
    codeSpans.push('<code>'+m1+'</code>');
    return '\\x00ADOCCODE'+(codeSpans.length-1)+'\\x00';
  });
  var varSpans=[];
  s=s.replace(/(?<![\\w\\\\\\$])\\$[A-Z_][A-Za-z0-9_]*\\b/g, function(m){
    varSpans.push(m);
    return '\\x00ADOCVAR'+(varSpans.length-1)+'\\x00';
  });
  s=s.replace(/(?<![\\w\\\\\\$])\\$\\{[A-Za-z0-9_]+\\}/g, function(m){
    varSpans.push(m);
    return '\\x00ADOCVAR'+(varSpans.length-1)+'\\x00';
  });
  s=s.replace(/(?<![\\w\\\\\\$])\\$\\d+(?:,\\d{3})*(?:\\.\\d+)?\\b/g, function(m){
    varSpans.push(m);
    return '\\x00ADOCVAR'+(varSpans.length-1)+'\\x00';
  });
  var mathSpans=[];
  s=s.replace(/(stem|latexmath|asciimath):\\\\[(.*?)\\\\]/gi, function(_, kind, content){
    mathSpans.push('<span class=\"math inline\" data-math=\"'+kind.toLowerCase()+'\"><code>'+content+'</code></span>');
    return '\\x00ADOCMATH'+(mathSpans.length-1)+'\\x00';
  });
  s=s.replace(/\\\\\\((.*?)\\\\\\)/g, function(_, content){
    mathSpans.push('<span class=\"math inline\" data-math=\"latex\"><code>'+content+'</code></span>');
    return '\\x00ADOCMATH'+(mathSpans.length-1)+'\\x00';
  });
  s=s.replace(/\\$\\$([^\\$]+?)\\$\\$/g, function(_, content){
    mathSpans.push('<span class=\"math inline\" data-math=\"latex\"><code>'+content+'</code></span>');
    return '\\x00ADOCMATH'+(mathSpans.length-1)+'\\x00';
  });
  s=s.replace(/(?<![\\w\\\\\\$])\\$([^\\$\\n]+?)\\$(?![\\w\\$])/g, function(_, content){
    mathSpans.push('<span class=\"math inline\" data-math=\"latex\"><code>'+content+'</code></span>');
    return '\\x00ADOCMATH'+(mathSpans.length-1)+'\\x00';
  });
  var linkSpans=[];
  s=s.replace(/(https?:\\/\\/[^\\s\\[\\]]+)\\[(.*?)\\]/g, function(_, u, l){
    linkSpans.push('<a href=\"'+u+'\" target=\"_blank\" rel=\"noopener\">'+l+'</a>');
    return '\\x00ADOCLINK'+(linkSpans.length-1)+'\\x00';
  });
  s=s.replace(/(?<!href=\")(https?:\\/\\/[^\\s<>\"\\'\\)]+)/g, function(_, u){
    linkSpans.push('<a href=\"'+u+'\" target=\"_blank\" rel=\"noopener\">'+u+'</a>');
    return '\\x00ADOCLINK'+(linkSpans.length-1)+'\\x00';
  });
  s=s.replace(/&lt;&lt;([a-zA-Z0-9_\\-\\.\\:\\/]+)(?:,\\s*([^&]+?))?&gt;&gt;/g, function(_, a, l){
    var label=(l||a).trim();
    linkSpans.push('<a href=\"#'+a.trim()+'\" class=\"xref\">'+label+'</a>');
    return '\\x00ADOCLINK'+(linkSpans.length-1)+'\\x00';
  });
  s=s.replace(/xref:([a-zA-Z0-9_\\-\\.\\:\\/]+)\\[(.*?)\\]/gi, function(_, a, l){
    var label=(l||a).trim();
    linkSpans.push('<a href=\"#'+a.trim()+'\" class=\"xref\">'+label+'</a>');
    return '\\x00ADOCLINK'+(linkSpans.length-1)+'\\x00';
  });
  s=s.replace(/\\[\\[([a-zA-Z0-9_\\-\\.\\:\\/]+)\\]\\]/g, '<a id=\"$1\" class=\"anchor\"></a>');
  s=s.replace(/\\s+\\+\\s*$/g, '<br>');
  s=s.replace(/(?<!#)#(?![\\s#])([^#\\n]+?)(?<![\\s#])#(?!#)/g, '<mark>$1</mark>');
  s=s.replace(/\\*\\*(?![\\s\\*])([^*\\n]+?)(?<![\\s\\*])\\*\\*/g, '<strong>$1</strong>');
  s=s.replace(/(?<!\\*)\\*(?![\\s\\*])([^*\\n]+?)(?<![\\s\\*])\\*(?!\\*)/g, '<strong>$1</strong>');
  s=s.replace(/__(?![\\s_])([^_\\n]+?)(?<![\\s_])__/g, '<em>$1</em>');
  s=s.replace(/(?<!_)_(?![\\s_])([^_\\n]+?)(?<![\\s_])_(?!_)/g, '<em>$1</em>');
  s=s.replace(/\\^(?![\\s\\^])([^\\^\\n]+?)(?<![\\s\\^])\\^/g, '<sup>$1</sup>');
  s=s.replace(/~(?![\\s~])([^~\\n]+?)(?<![\\s~])~/g, '<sub>$1</sub>');
  for(var i=0; i<linkSpans.length; i++) s=s.replace('\\x00ADOCLINK'+i+'\\x00', linkSpans[i]);
  for(var j=0; j<codeSpans.length; j++) s=s.replace('\\x00ADOCCODE'+j+'\\x00', codeSpans[j]);
  for(var k=0; k<mathSpans.length; k++) s=s.replace('\\x00ADOCMATH'+k+'\\x00', mathSpans[k]);
  for(var v=0; v<varSpans.length; v++) s=s.replace('\\x00ADOCVAR'+v+'\\x00', varSpans[v]);
  return s;
}

// 자체 완결형 경량 AsciiDoc 렌더러 (외부 CDN/루비런타임 의존성 제로, 번개같은 로딩 속도)
function convertAsciidocToHtml(raw){
  if(!raw) return '';
  const NL=String.fromCharCode(10);
  const lines=String(raw).split(NL);
  const out=[];
  let inBlock=null;
  let blockMeta={};
  let blockLines=[];

  var listStack=[];
  var inItem=false;
  var inContinuation=false;
  var pendingContinuation=false;
  var continuationLines=[];
  var pendingMeta=null;
  var pendingBlockLines=[];
  var normalPLines=[];
  var pendingAnchor=null;

  function closeItem(){
    if(inItem){ inItem=false; return ['</li>']; }
    return [];
  }
  function flushList(){
    if(listStack.length===0) return;
    out.push.apply(out, closeItem());
    while(listStack.length>0){
      var entry=listStack.pop();
      out.push('</'+entry.tag+'>');
      if(listStack.length>0) out.push('</li>');
    }
  }
  function adjustListLevel(tag, level){
    if(listStack.length===0){
      out.push('<'+tag+'>');
      listStack.push({tag:tag, level:level});
      return;
    }
    var top=listStack[listStack.length-1];
    if(level > top.level){
      out.push('<'+tag+'>');
      listStack.push({tag:tag, level:level});
    }else if(level < top.level){
      out.push.apply(out, closeItem());
      while(listStack.length>0 && listStack[listStack.length-1].level > level){
        var e=listStack.pop();
        out.push('</'+e.tag+'>');
        if(listStack.length>0) out.push('</li>');
      }
      if(listStack.length>0 && listStack[listStack.length-1].tag !== tag){
        var old=listStack.pop();
        out.push('</'+old.tag+'>');
        out.push('<'+tag+'>');
        listStack.push({tag:tag, level:level});
      }
    }else{
      if(top.tag !== tag){
        out.push.apply(out, closeItem());
        listStack.pop();
        out.push('</'+top.tag+'>');
        out.push('<'+tag+'>');
        listStack.push({tag:tag, level:level});
      }else{
        out.push.apply(out, closeItem());
      }
    }
  }

  function formatParagraphLines(pLines){
    if(!pLines || pLines.length===0) return '';
    var formattedParts=pLines.map(function(l){ return inlineAdocFormat(l); });
    var res='';
    for(var i=0; i<formattedParts.length; i++){
      var p=formattedParts[i];
      if(i===0){ res=p; }
      else{
        if(res.endsWith('<br>')) res += p;
        else res += ' ' + p;
      }
    }
    return res;
  }

  function flushPendingSingleBlock(){
    if(!pendingMeta || (pendingMeta.kind!=='quote' && pendingMeta.kind!=='admonition')) return;
    if(pendingBlockLines.length===0){ pendingMeta=null; return; }
    var anchorAttr=pendingAnchor ? ' id=\"'+esc(pendingAnchor)+'\"' : '';
    pendingAnchor=null;
    var pContent=formatParagraphLines(pendingBlockLines);
    if(pendingMeta.kind==='quote'){
      var attrText=pendingMeta.author?esc(pendingMeta.author)+(pendingMeta.source?' — '+esc(pendingMeta.source):''):'';
      out.push('<div class=\"quoteblock\"' + anchorAttr + '><blockquote><p>'+pContent+'</p></blockquote>'+(attrText?'<div class=\"attribution\">'+attrText+'</div>':'')+'</div>');
    }else if(pendingMeta.kind==='admonition'){
      var admType=(pendingMeta.type||'NOTE').toLowerCase();
      var admTitle=esc(pendingMeta.type||'NOTE');
      out.push('<div class=\"admonitionblock '+admType+'\"' + anchorAttr + '><div class=\"title\">'+admTitle+'</div><div class=\"content\"><p>'+pContent+'</p></div></div>');
    }
    pendingMeta=null;
    pendingBlockLines=[];
  }

  function flushContinuation(){
    if(inContinuation && continuationLines.length>0){
      var anchorAttr=pendingAnchor ? ' id=\"'+esc(pendingAnchor)+'\"' : '';
      pendingAnchor=null;
      out.push('<p' + anchorAttr + '>'+formatParagraphLines(continuationLines)+'</p>');
    }
    inContinuation=false;
    pendingContinuation=false;
    continuationLines=[];
  }

  function flushNormalP(){
    if(normalPLines.length>0){
      flushList();
      var anchorAttr=pendingAnchor ? ' id=\"'+esc(pendingAnchor)+'\"' : '';
      pendingAnchor=null;
      out.push('<p' + anchorAttr + '>'+formatParagraphLines(normalPLines)+'</p>');
      normalPLines=[];
    }
  }

  function flushBlock(){
    if(!inBlock) return;
    var anchorAttr=pendingAnchor ? ' id=\"'+esc(pendingAnchor)+'\"' : '';
    pendingAnchor=null;

    if(inBlock==='quote'){
      var qParagraphs=[];
      var currP=[];
      for(var b=0; b<blockLines.length; b++){
        var bl=blockLines[b];
        if(!bl.trim()){
          if(currP.length>0){ qParagraphs.push(formatParagraphLines(currP)); currP=[]; }
        }else{ currP.push(bl); }
      }
      if(currP.length>0) qParagraphs.push(formatParagraphLines(currP));
      var qContent=qParagraphs.map(function(p){ return '<p>'+p+'</p>'; }).join('');
      var attr='';
      if(blockMeta.author||blockMeta.source){
        attr='<div class=\"attribution\">'+esc(blockMeta.author||'')+
             (blockMeta.source?' — '+esc(blockMeta.source):'')+'</div>';
      }
      out.push('<div class=\"quoteblock\"' + anchorAttr + '><blockquote>'+qContent+'</blockquote>'+attr+'</div>');
    }else if(inBlock==='admonition'){
      var admParagraphs=[];
      var currP=[];
      for(var b=0; b<blockLines.length; b++){
        var bl=blockLines[b];
        if(!bl.trim()){
          if(currP.length>0){ admParagraphs.push(formatParagraphLines(currP)); currP=[]; }
        }else{ currP.push(bl); }
      }
      if(currP.length>0) admParagraphs.push(formatParagraphLines(currP));
      var admContent=admParagraphs.map(function(p){ return '<p>'+p+'</p>'; }).join('');
      var type=(blockMeta.type||'NOTE').toLowerCase();
      out.push('<div class=\"admonitionblock '+esc(type)+'\"' + anchorAttr + '><div class=\"title\">'+
               esc(blockMeta.type||'NOTE')+'</div><div class=\"content\">'+admContent+'</div></div>');
    }else if(inBlock==='code'){
      var codeText=esc(blockLines.join(NL)).replace(/&lt;(\\d+)&gt;/g,'<span class=\"conum\">&lt;$1&gt;</span>');
      out.push('<div class=\"listingblock\"' + anchorAttr + '><div class=\"content\"><pre><code class=\"language-'+esc(blockMeta.lang||'')+'\">'+codeText+'</code></pre></div></div>');
    }else if(inBlock==='math'){
      var mathText=esc(blockLines.join(NL));
      var mType=esc(blockMeta.type||'latex');
      out.push('<div class=\"mathblock display\"' + anchorAttr + ' data-math=\"'+mType+'\"><div class=\"content\"><pre class=\"math\"><code>'+mathText+'</code></pre></div></div>');
    }else if(inBlock==='table'){
      var tblHtml=renderTableHtml(blockLines, blockMeta, anchorAttr.replace(' id=\"', '').replace('\"', ''));
      if(tblHtml) out.push(tblHtml);
    }
    inBlock=null; blockMeta={}; blockLines=[];
  }

  function matchList(trimmed){
    var mStar=trimmed.match(/^(\\*{1,5})\\s+(.+)$/);
    if(mStar) return {tag:'ul', level:mStar[1].length, text:mStar[2]};
    var mHyphen=trimmed.match(/^-\\s+(.+)$/);
    if(mHyphen) return {tag:'ul', level:1, text:mHyphen[1]};
    var mDot=trimmed.match(/^(\\.{1,5})\\s+(.+)$/);
    if(mDot) return {tag:'ol', level:mDot[1].length, text:mDot[2]};
    var mNum=trimmed.match(/^\\d+[\\.\\)]\\s+(.+)$/);
    if(mNum) return {tag:'ol', level:1, text:mNum[1]};
    return null;
  }

  function extractHeadingAnchor(hText){
    var m=hText.match(/\\[#([a-zA-Z0-9_\\-\\.\\:\\/]+)\\]|\\[\\[([a-zA-Z0-9_\\-\\.\\:\\/]+)\\]\\]/);
    if(m){
      var anc=m[1]||m[2];
      var clean=(hText.substring(0, m.index)+hText.substring(m.index+m[0].length)).trim();
      return {text:clean, anchor:anc};
    }
    var anc2=pendingAnchor;
    pendingAnchor=null;
    return {text:hText, anchor:anc2};
  }

  for(var i=0; i<lines.length; i++){
    var line=lines[i];
    var trimmed=line.trim();

    if(!inBlock){
      var anchorM=trimmed.match(/^\\[#([a-zA-Z0-9_\\-\\.\\:\\/]+)\\]$/) || trimmed.match(/^\\[\\[([a-zA-Z0-9_\\-\\.\\:\\/]+)\\]\\]$/);
      if(anchorM){
        flushNormalP(); flushContinuation(); flushPendingSingleBlock();
        pendingAnchor=anchorM[1].trim();
        continue;
      }

      var qm=trimmed.match(/^\\[quote(?:,\\s*([^,\\]]+))?(?:,\\s*([^\\]]+))?\\]/i);
      if(qm){
        flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList();
        pendingMeta={kind:'quote', author:qm[1]?qm[1].trim():'', source:qm[2]?qm[2].trim():''};
        pendingBlockLines=[];
        continue;
      }
      var am=trimmed.match(/^\\[(NOTE|IMPORTANT|TIP|WARNING|CAUTION)\\]/i);
      if(am){
        flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList();
        pendingMeta={kind:'admonition', type:am[1].toUpperCase()};
        pendingBlockLines=[];
        continue;
      }
      var sm=trimmed.match(/^\\[source(?:,\\s*([a-zA-Z0-9_-]+))?\\]/i);
      if(sm){
        flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList();
        pendingMeta={kind:'code', lang:sm[1]?sm[1].trim():''};
        continue;
      }
      var mathM=trimmed.match(/^\\[(latexmath|stem|asciimath)\\]$/i);
      if(mathM){
        flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList();
        pendingMeta={kind:'math', type:mathM[1].toLowerCase()};
        continue;
      }
      var tm=trimmed.match(/^\\[(.*cols.*|.*header.*|\\d+\\*|[0-9,]+)\\]$/i);
      if(tm){
        flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList();
        if(pendingMeta && pendingMeta.kind==='table'){
          pendingMeta.cols=tm[1];
        }else{
          pendingMeta={kind:'table', cols:tm[1]};
        }
        continue;
      }
      var titleM=trimmed.match(/^\\.([^\\.\\s].*)$/);
      if(titleM){
        flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList();
        if(pendingMeta && pendingMeta.kind==='table'){
          pendingMeta.title=titleM[1].trim();
        }else{
          pendingMeta={kind:'table', title:titleM[1].trim()};
        }
        continue;
      }

      if(trimmed==='____'){
        flushNormalP(); flushContinuation(); flushList();
        inBlock='quote';
        blockMeta=(pendingMeta&&pendingMeta.kind==='quote')?pendingMeta:{};
        pendingMeta=null; pendingBlockLines=[]; blockLines=[];
        continue;
      }
      if(trimmed==='===='){
        flushNormalP(); flushContinuation(); flushList();
        inBlock='admonition';
        blockMeta=(pendingMeta&&pendingMeta.kind==='admonition')?pendingMeta:{type:'NOTE'};
        pendingMeta=null; pendingBlockLines=[]; blockLines=[];
        continue;
      }
      if(trimmed==='----'){
        flushNormalP(); flushContinuation(); flushList();
        if(pendingMeta && pendingMeta.kind==='math'){
          inBlock='math';
          blockMeta=pendingMeta;
        }else{
          inBlock='code';
          blockMeta=(pendingMeta&&pendingMeta.kind==='code')?pendingMeta:{};
        }
        pendingMeta=null; pendingBlockLines=[]; blockLines=[];
        continue;
      }
      if(trimmed==='++++'){
        flushNormalP(); flushContinuation(); flushList();
        inBlock='math';
        blockMeta=(pendingMeta&&pendingMeta.kind==='math')?pendingMeta:{kind:'math', type:'latex'};
        pendingMeta=null; pendingBlockLines=[]; blockLines=[];
        continue;
      }
      if(trimmed==='|==='){
        flushNormalP(); flushContinuation(); flushList();
        inBlock='table';
        blockMeta=(pendingMeta&&pendingMeta.kind==='table')?pendingMeta:{};
        pendingMeta=null; pendingBlockLines=[]; blockLines=[];
        continue;
      }

      var imgMatch=trimmed.match(/^image::([^\\[]+)\\[([^,\\]]*)(?:,\\s*title=(?:\"([^\"]*)\"|'([^']*)'|([^\\]]*)))?\\]/);
      if(imgMatch){
        flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList();
        var src=imgMatch[1].trim();
        var alt=imgMatch[2]?imgMatch[2].trim():'';
        var cap=imgMatch[3]||imgMatch[4]||imgMatch[5]||'';
        var anchorAttr=pendingAnchor ? ' id=\"'+esc(pendingAnchor)+'\"' : '';
        pendingAnchor=null;
        out.push('<div class=\"imageblock\"' + anchorAttr + '><img src=\"'+esc(src)+'\" alt=\"'+esc(alt)+'\">'+
                 (cap?'<div class=\"title\">'+esc(cap)+'</div>':'')+'</div>');
        continue;
      }
      var colMatch=trimmed.match(/^<(\\d+)>\\s*(.+)/);
      if(colMatch){
        flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList();
        out.push('<div class=\"colist\"><span class=\"conum\">&lt;'+colMatch[1]+'&gt;</span> '+inlineAdocFormat(colMatch[2])+'</div>');
        continue;
      }
      if(/^'{3,}$/.test(trimmed)){
        flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList();
        var anchorAttr=pendingAnchor ? ' id=\"'+esc(pendingAnchor)+'\"' : '';
        pendingAnchor=null;
        out.push('<hr' + anchorAttr + '>');
        continue;
      }
      var h1Match=trimmed.match(/^=\\s+(.+)$/);
      if(h1Match){ flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList(); var hInfo=extractHeadingAnchor(h1Match[1]); var idAttr=hInfo.anchor?' id=\"'+esc(hInfo.anchor)+'\"':''; out.push('<h1'+idAttr+'>'+inlineAdocFormat(hInfo.text)+'</h1>'); continue; }
      var h2Match=trimmed.match(/^==\\s+(.+)$/);
      if(h2Match){ flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList(); var hInfo=extractHeadingAnchor(h2Match[1]); var idAttr=hInfo.anchor?' id=\"'+esc(hInfo.anchor)+'\"':''; out.push('<h2'+idAttr+'>'+inlineAdocFormat(hInfo.text)+'</h2>'); continue; }
      var h3Match=trimmed.match(/^===\\s+(.+)$/);
      if(h3Match){ flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList(); var hInfo=extractHeadingAnchor(h3Match[1]); var idAttr=hInfo.anchor?' id=\"'+esc(hInfo.anchor)+'\"':''; out.push('<h3'+idAttr+'>'+inlineAdocFormat(hInfo.text)+'</h3>'); continue; }
      var h4Match=trimmed.match(/^====\\s+(.+)$/);
      if(h4Match){ flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList(); var hInfo=extractHeadingAnchor(h4Match[1]); var idAttr=hInfo.anchor?' id=\"'+esc(hInfo.anchor)+'\"':''; out.push('<h4'+idAttr+'>'+inlineAdocFormat(hInfo.text)+'</h4>'); continue; }

      var attrMatch=trimmed.match(/^:[a-zA-Z0-9_-]+:\\s*(.*)$/);
      if(attrMatch){ continue; }

      var singleAdm=trimmed.match(/^(NOTE|TIP|IMPORTANT|WARNING|CAUTION):\\s*(.+)$/i);
      if(singleAdm){
        flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList();
        var admType=singleAdm[1].toUpperCase();
        var anchorAttr=pendingAnchor ? ' id=\"'+esc(pendingAnchor)+'\"' : '';
        pendingAnchor=null;
        out.push('<div class=\"admonitionblock '+admType.toLowerCase()+'\"' + anchorAttr + '><div class=\"title\">'+esc(admType)+'</div><div class=\"content\"><p>'+inlineAdocFormat(singleAdm[2])+'</p></div></div>');
        continue;
      }

      if(trimmed==='+'){
        if(inItem){
          flushContinuation();
          pendingContinuation=true;
        }
        continue;
      }

      var listM=matchList(trimmed);
      if(listM){
        flushNormalP(); flushContinuation(); pendingContinuation=false; flushPendingSingleBlock();
        var itemText=inlineAdocFormat(listM.text);
        adjustListLevel(listM.tag, listM.level);
        out.push('<li>'+itemText);
        inItem=true;
        inContinuation=false;
        continue;
      }

      if(!trimmed){
        if(inContinuation){
          flushContinuation();
        }
        flushNormalP(); flushPendingSingleBlock();
        continue;
      }

      if(pendingMeta && (pendingMeta.kind==='quote' || pendingMeta.kind==='admonition')){
        pendingBlockLines.push(trimmed);
        continue;
      }

      if((pendingContinuation || inContinuation) && inItem){
        pendingContinuation=false;
        inContinuation=true;
        continuationLines.push(trimmed);
        continue;
      }

      normalPLines.push(trimmed);
    }else{
      if(inBlock==='quote'&&trimmed==='____') flushBlock();
      else if(inBlock==='admonition'&&trimmed==='====') flushBlock();
      else if(inBlock==='code'&&trimmed==='----') flushBlock();
      else if(inBlock==='math'&&(trimmed==='++++'||trimmed==='----')) flushBlock();
      else if(inBlock==='table'&&trimmed==='|===') flushBlock();
      else{
        blockLines.push(line);
      }
    }
  }
  flushBlock();
  flushNormalP();
  flushContinuation();
  flushPendingSingleBlock();
  flushList();
  return out.join(NL);
}

function renderAsciidoc(src){
  if(!src) return '';
  const raw=String(src);
  const purifier=window.DOMPurify;
  try{
    const html = convertAsciidocToHtml(raw);
    if(purifier && typeof purifier.sanitize==='function'){
      return purifier.sanitize(html, DOMPURIFY_OPTS);
    }
    return html;
  }catch(_){
    return renderMarkdown(raw);
  }
}

function applyMathRendering(container){
  if(!container) return;
  if(typeof renderMathInElement === 'function'){
    try{
      renderMathInElement(container, {
        delimiters: [
          {left: '$$', right: '$$', display: true},
          {left: '$', right: '$', display: false},
          {left: '\\\\(', right: '\\\\)', display: false},
          {left: '\\\\[', right: '\\\\]', display: true}
        ],
        ignoredTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code', 'option'],
        throwOnError: false
      });
    }catch(_){}
  }
  if(typeof katex !== 'undefined'){
    try{
      container.querySelectorAll('.mathblock').forEach(function(mb){
        var codeEl = mb.querySelector('pre.math code, pre.math');
        if(codeEl && !mb.getAttribute('data-katex-rendered')){
          var text = (codeEl.textContent || '').trim();
          if(text){
            try{
              mb.innerHTML = '';
              katex.render(text, mb, { displayMode: true, throwOnError: false });
              mb.setAttribute('data-katex-rendered', 'true');
            }catch(_){}
          }
        }
      });
      container.querySelectorAll('.math.inline').forEach(function(mi){
        var codeEl = mi.querySelector('code') || mi;
        if(codeEl && !mi.getAttribute('data-katex-rendered')){
          var text = (codeEl.textContent || '').trim();
          if(text){
            try{
              var span = document.createElement('span');
              katex.render(text, span, { displayMode: false, throwOnError: false });
              mi.parentNode.replaceChild(span, mi);
            }catch(_){}
          }
        }
      });
    }catch(_){}
  }
}

function isAsciidoc(src, format){
  if(format){
    const fmt=String(format).toLowerCase().trim();
    if(fmt==='adoc'||fmt==='asciidoc') return true;
    if(fmt==='md'||fmt==='markdown') return false;
  }
  if(!src) return false;
  const s=String(src);
  return /(?:^|\\n)\\[(NOTE|TIP|IMPORTANT|WARNING|CAUTION|quote|source)[^\\]]*\\]|(?:^|\\n)\\|===|(?:^|\\n)image::/m.test(s);
}

function renderContent(src, format){
  if(!src) return '';
  if(isAsciidoc(src, format)){
    return renderAsciidoc(src);
  }
  return renderMarkdown(src);
}

// 목록 설명 줄수(0/2/4) — 브라우저에 기억. 문서 많아지면 제목/설명이 height 를 너무
// 차지한다는 피드백(사용자 지적) → #docs 에 lc0/lc4 클래스로 CSS line-clamp 토글(기본 2줄).
let descLines = 3;
try{ const v=parseInt(localStorage.getItem('claireDescLines')); if(v===0||v===3||v===4||v===2) descLines=(v===0?0:3); }catch(e){}
function doclistToolbarHtml(){
  return '<div class="doclist-toolbar">'+
    '<select id="desclines" onchange="setDescLines(this.value)" title="목록 설명 줄수" aria-label="목록 설명 줄수">'+
    '<option value="0"'+(descLines===0?' selected':'')+'>제목만 표시</option>'+
    '<option value="3"'+(descLines===3?' selected':'')+'>요약 표시</option>'+
    '</select></div>';
}
function applyDescLines(){
  const docs=document.getElementById('docs'); if(!docs) return;
  docs.classList.remove('lc0','lc3','lc4');
  if(descLines===0) docs.classList.add('lc0');
  else docs.classList.add('lc3');
  const sel=document.getElementById('desclines'); if(sel) sel.value=String(descLines===0?0:3);
}
function setDescLines(v){
  descLines = parseInt(v)===0 ? 0 : 3;
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

// 중앙 읽기 — 좌측 문서의 '읽기' 버튼/노드 상세의 📖 로 연다(nav 와 분리, 사용자 요구).
let curReaderDoc=null;   // 현재 읽기 팝업의 문서 id(🔗 공유 링크 생성 대상)
let curReaderDocData=null; // 현재 읽기 팝업의 문서 객체
let readerReturnFocus=null, readerReturnDocId=null;
function setReaderBackgroundInert(on){
  if(mobileMQ.matches){
    const el=document.getElementById('bar'); if(el) el.inert=on;
  }
}
function readerFocusable(){
  return [...document.querySelectorAll('#reader button:not([disabled]),#reader a[href],'+
    '#reader input:not([disabled]),#reader [tabindex]:not([tabindex="-1"])')]
    .filter(el=>el.getClientRects().length>0);
}
function handleReaderKey(e){
  const r=document.getElementById('reader');
  if(!r.classList.contains('open')) return false;
  if(e.key==='Escape'){ e.preventDefault(); closeReader(false, true); return true; }
  if(e.key!=='Tab') return false;
  const items=readerFocusable();
  if(!items.length){ e.preventDefault(); r.querySelector('.sheet').focus(); return true; }
  const first=items[0], last=items[items.length-1];
  if(e.shiftKey && (document.activeElement===first || document.activeElement===r.querySelector('.sheet'))){
    e.preventDefault(); last.focus();
  }else if(!e.shiftKey && document.activeElement===last){
    e.preventDefault(); first.focus();
  }
  return true;
}
async function markDocumentSeen(docId){
  if(!canWrite()) return;
  try{
    const r=await fetch('document/seen',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({id:docId})});
    if(r.status===401||r.status===404){ expireWriteAccess(); return; }
    if(!r.ok) return;
    const dc=allDocs && allDocs.find(d=>d.id===docId);
    if(dc && dc.seen!==1){ dc.seen=1; renderDocs(document.getElementById('docq').value); }
  }catch(_){}
}
function setCenterView(mode){
  centerView = (mode==='graph' ? 'graph' : 'reader');
  document.body.dataset.centerView = centerView;
  const mt = document.getElementById('menu-section-title');
  if(mt){ mt.textContent = (centerView==='graph' ? '그래프 도구' : '문서와 그래프'); }
  if(centerView==='graph'){
    graphCamera = null;
    requestAnimationFrame(()=>{
      relayout(true);
      applyTouchMode();
      if(activeDoc){
        const docId = activeDoc;
        const ids = [];
        allNodes && allNodes.forEach(n => {
          if(!n.hidden && (n.sources || []).includes(docId)) ids.push(n.id);
        });
        if(ids.length) net && net.selectNodes(ids);
        if(!ids.length) resetGraphCamera(); else cameraToNodes(ids);
      } else {
        fitGraphContext();
      }
    });
  }
}
function openDocGraph(docId){
  const targetId = docId || activeDoc || curReaderDoc || null;
  if(targetId){
    activeDoc = targetId;
    curReaderDoc = targetId;
    recordSelectedDoc(targetId);
  }
  setCenterView('graph');
  revealWorkspace('graph');
  if(targetId) setActiveDoc(targetId);
  if(mobileMQ.matches && typeof closeReader === 'function'){
    closeReader(false, false);
  }
  if(drawerOpen || detailOpen){
    if(compactMQ.matches || mobileMQ.matches) closeDrawer(false, false);
  }
}
function openReader(docId, pushHist=true){
  hideNodePop();
  if(!docId && allDocs && allDocs.length) docId = allDocs[0].id;
  if(!docId) return;
  curReaderDoc=docId;
  activeDoc=docId;
  recordSelectedDoc(docId);
  selectedNodeId=null;                          // 문서 모드로 전환 — 노드 inspect 해제
  readerReturnFocus=document.activeElement;
  readerReturnDocId=docId;
  const sb=document.getElementById('sharebox'); if(sb){ sb.className='sharebox'; sb.innerHTML=''; }  // 이전 공유링크 닫기
  applyReadFS();   // 저장된 글자 크기 적용
  document.getElementById('rtitle').textContent='문서 불러오는 중…';
  document.getElementById('rbody').innerHTML='';
  if(panel) panel.innerHTML='<p class=hint>문서 불러오는 중…</p>';
  const r=document.getElementById('reader');
  r.setAttribute('aria-hidden','false');
  r.setAttribute('aria-busy','true');
  if(drawerOpen || detailOpen){
    if(compactMQ.matches || mobileMQ.matches) closeDrawer(false, false);
  }
  if(mobileMQ.matches){
    r.classList.add('open');
    document.body.classList.add('reader-open');
    setReaderBackgroundInert(true);
    if(pushHist) pushAppHistory({ modal: 'reader', docId: docId });
    requestAnimationFrame(()=>r.querySelector('.sheet')?.focus());
  } else {
    setCenterView('reader');
    renderDocs(document.getElementById('docq').value);
  }
  applyView();
  if(activeDoc){
    const targetDocId=activeDoc;
    requestAnimationFrame(()=>{ if(net && activeDoc===targetDocId){
      const ids=[]; allNodes.forEach(n=>{ if(!n.hidden && (n.sources||[]).includes(targetDocId)) ids.push(n.id); });
      if(ids.length) net.selectNodes(ids);
      if(!ids.length) resetGraphCamera(); else cameraToNodes(ids);
    }});
  }
  fetch('document?id='+encodeURIComponent(docId)).then(x=>x.json()).then(dc=>{
    if(!dc || dc.error){
      document.getElementById('rbody').innerHTML='<p class=hint>문서를 찾을 수 없습니다.</p>';
      r.setAttribute('aria-busy','false');
      if(activeDoc===docId && panel) panel.innerHTML='<p class=hint>문서를 찾을 수 없습니다.</p>';
      return;
    }
    renderReader(dc);
    if(activeDoc===docId) renderDocPanel(dc);
    markDocumentSeen(docId);
  }).catch(()=>{
    document.getElementById('rbody').innerHTML='<p class=hint>문서 로드 실패.</p>';
    r.setAttribute('aria-busy','false');
    if(activeDoc===docId && panel) panel.innerHTML='<p class=hint>문서 로드 실패.</p>';
  });
}
function docMetaHtml(dc){
  if(!dc) return '';
  const hasUrl = !!dc.url;
  const isTrunc = !!(dc.raw_truncated || (dc.meta && dc.meta.raw_truncated));
  const isAppTrunc = isTrunc && !!(dc.appendix_truncated || (dc.meta && dc.meta.appendix_truncated));
  const directive = (dc.directive || (dc.meta && dc.meta.directive) || '').trim();
  const isStt = !!(dc.is_stt || (dc.meta && (dc.meta.is_stt || dc.meta.stt_applied || dc.meta.stt)));
  const isSttTrunc = isStt && !!(dc.stt_truncated || (dc.meta && dc.meta.stt_truncated) || isTrunc);
  if(!hasUrl && !isTrunc && !directive && !isStt) return '';
  let h='<p class=docmeta>';
  if(hasUrl){
    h+='<a href="'+esc(dc.url)+'" target=_blank rel=noopener>↗ 원문 열기</a>';
    if(isStt){
      h+=' <a href="#" class="stt-link" onclick="openSttReader();return false;" title="음성 인식(STT) 전사 텍스트 열기">↗ 전사 열기</a>';
    }
  } else {
    if(isStt){
      h+='<a href="#" class="stt-link" onclick="openSttReader();return false;" title="음성 인식(STT) 전사 텍스트 열기">↗ 전사 열기</a>';
    } else {
      h+='<span></span>';
    }
  }
  let tags=[];
  if(directive){
    const dispDir = directive.length > 25 ? directive.slice(0, 25) + '…' : directive;
    tags.push('<span class="directive-tag" title="적재 시 지정한 초점: '+esc(directive)+'">🎯 '+esc(dispDir)+'</span>');
  }
  if(isStt){
    tags.push('<span class="directive-tag stt-tag" title="음성 인식(STT)을 적용하여 작성한 문서">🎙️ STT</span>');
  }
  if(isAppTrunc){
    const orig=(dc.orig_chars || (dc.meta && dc.meta.orig_chars)) || 0;
    const raw=(dc.raw_chars || (dc.meta && dc.meta.raw_chars)) || 0;
    let tip = '원문의 부록(Appendix) 부분을 절단한 문서';
    let label='✂️ 원문 일부 절단';
    if(orig > 0 && raw > 0){
      tip+=' (원문: '+orig.toLocaleString()+'자 → 적재: '+raw.toLocaleString()+'자)';
      label+=' ('+raw.toLocaleString()+' / '+orig.toLocaleString()+'자)';
    } else if(raw > 0){
      label+=' ('+raw.toLocaleString()+'자)';
    }
    tags.push('<span class="trunc-tag trunc-appendix" title="'+esc(tip)+'">'+esc(label)+'</span>');
  } else if(isSttTrunc){
    const orig=(dc.stt_orig_chars || (dc.meta && dc.meta.stt_orig_chars) || dc.orig_chars || (dc.meta && dc.meta.orig_chars)) || 0;
    const raw=(dc.stt_raw_chars || (dc.meta && dc.meta.stt_raw_chars) || dc.raw_chars || (dc.meta && dc.meta.raw_chars)) || 0;
    let tip = '음성 전사(STT) 전문이 일부 절단된 상태에서 본문(상세)이 작성된 문서';
    let label = '✂️ STT 일부 절단';
    if(orig > 0 && raw > 0){
      tip += ' (원문: '+orig.toLocaleString()+'자 → 적재: '+raw.toLocaleString()+'자)';
      label += ' ('+raw.toLocaleString()+' / '+orig.toLocaleString()+'자)';
    } else if(raw > 0){
      label += ' ('+raw.toLocaleString()+'자)';
    }
    tags.push('<span class="trunc-tag trunc-stt" title="'+esc(tip)+'">'+esc(label)+'</span>');
  } else if(isTrunc){
    const orig=(dc.orig_chars || (dc.meta && dc.meta.orig_chars)) || 0;
    const raw=(dc.raw_chars || (dc.meta && dc.meta.raw_chars)) || 0;
    let tip = '글자 수 상한으로 원문 일부를 절단한 문서';
    let label='✂️ 원문 일부 절단';
    if(orig > 0 && raw > 0){
      tip+=' (원문: '+orig.toLocaleString()+'자 → 적재: '+raw.toLocaleString()+'자)';
      label+=' ('+raw.toLocaleString()+' / '+orig.toLocaleString()+'자)';
    } else if(raw > 0){
      label+=' ('+raw.toLocaleString()+'자)';
    }
    tags.push('<span class="trunc-tag" title="'+esc(tip)+'">'+esc(label)+'</span>');
  }
  if(tags.length){
    h+='<span class="docmeta-tags">'+tags.join(' ')+'</span>';
  }
  h+='</p>';
  return h;
}
function renderReader(dc){
  curReaderDocData=dc;
  if(dc && dc.title){
    document.title = dc.title + ' — Claire Bible';
  } else {
    document.title = '문서 — Claire Bible';
  }
  document.getElementById('rtitle').innerHTML = esc(dc.title||'(제목 없음)')
    + (dc.source_type?' <span class=rmeta>'+esc(dc.source_type)+'</span>':'');
  let h='';
  h+=docMetaHtml(dc);
  const isStt = !!(dc.is_stt || (dc.meta && (dc.meta.is_stt || dc.meta.stt_applied || dc.meta.stt)));
  const isSttTrunc = isStt && !!(dc.stt_truncated || (dc.meta && dc.meta.stt_truncated) || dc.raw_truncated || (dc.meta && dc.meta.raw_truncated));
  if(isSttTrunc){
    h+='<div class="stt-trunc-banner">⚠️ <strong>음성 전사(STT) 일부 절단 안내</strong>: 전체 전사 내용 중 일부만 반영된 상태에서 본문(상세)이 작성되었습니다. 전체 재전사 명령: <code>claire video-reprocess --doc-id '+esc(dc.id||'')+' --apply --full-content</code></div>';
  }
  h+=extraSourcesHtml(dc);
  const directive = (dc.directive || (dc.meta && dc.meta.directive) || '').trim();
  if(directive){
    h+='<div class="rsection">초점</div><div class="md" style="margin-bottom:.8em">🎯 <strong>'+esc(directive)+'</strong></div>';
  }
  if(dc.summary) h+='<div class=rsection>요약</div><div class="md">'+renderContent(dc.summary, dc.detail_format)+'</div>';
  if(dc.detail_html){
    const purifier=window.DOMPurify;
    const cleanHtml=(purifier && typeof purifier.sanitize==='function')?purifier.sanitize(dc.detail_html, DOMPURIFY_OPTS):dc.detail_html;
    h+='<div class=rsection>상세</div><div class="md">'+cleanHtml+'</div>';
  }else if(dc.detail){
    h+='<div class=rsection>상세</div><div class="md">'+renderContent(dc.detail, dc.detail_format)+'</div>';
  }
  if(!dc.summary && !dc.detail && !dc.detail_html) h+='<p class=hint>문서에 요약/상세 내용이 없습니다.</p>';
  const body=document.getElementById('rbody'); body.innerHTML=h; body.scrollTop=0;
  applyMathRendering(body);
  document.getElementById('reader').setAttribute('aria-busy','false');
  if(typeof window.gtag === 'function' && dc && dc.id){
    try{
      window.gtag('event', 'page_view', {
        page_title: document.title,
        page_location: window.location.origin + '/doc/' + dc.id
      });
      window.gtag('event', 'select_content', {
        content_type: 'document',
        item_id: dc.id
      });
    }catch(_){}
  }
}

// --- STT 전사 열기 모달 뷰어 제어 ---
let curSttData = null;
let curSttFilter = '';

function openSttReader(docId){
  const did = docId || curReaderDoc || (curReaderDocData && curReaderDocData.id);
  if(!did && !curReaderDocData) return;

  function _render(dc){
    if(!dc) return;
    curSttData = dc;
    const modal = document.getElementById('sttmodal');
    if(!modal) return;

    if(!dc.is_stt && !(dc.meta && (dc.meta.is_stt || dc.meta.transcript_segments))){
      alert('음성 전사(STT) 데이터가 없는 문서입니다.');
      return;
    }

    const titleEl = document.getElementById('stttitle');
    if(titleEl) titleEl.textContent = '🎙️ 음성 전사 (STT) — ' + (dc.title || '(제목 없음)');

    const metaEl = document.getElementById('sttmeta');
    let metaTxt = '';
    const dur = (dc.meta && dc.meta.duration_sec) || dc.duration_sec || 0;
    if(dur > 0){
      const m = Math.floor(dur / 60);
      const s = Math.floor(dur % 60);
      metaTxt += '재생 시간: ' + m + '분 ' + s + '초';
    }
    const segs = dc.transcript_segments || (dc.meta && dc.meta.transcript_segments) || [];
    if(segs.length > 0){
      metaTxt += (metaTxt ? ' · ' : '') + '총 ' + segs.length.toLocaleString() + '개 발화 구간';
    }
    if(metaEl) metaEl.textContent = metaTxt;

    const input = document.getElementById('sttq');
    if(input) input.value = '';
    curSttFilter = '';

    renderSttLines();
    modal.classList.add('open');
    modal.style.display = 'flex';
    document.body.classList.add('stt-modal-open');
    if(input) requestAnimationFrame(()=>input.focus());
  }

  if(curReaderDocData && (!docId || curReaderDocData.id === docId) && (curReaderDocData.stt_transcript || curReaderDocData.transcript_segments)){
    _render(curReaderDocData);
  } else {
    fetch('document?id=' + encodeURIComponent(did)).then(r=>r.json()).then(dc=>{
      _render(dc);
    }).catch(e=>{
      alert('전사 데이터를 불러오지 못했습니다: ' + e);
    });
  }
}

function closeSttReader(){
  const modal = document.getElementById('sttmodal');
  if(!modal) return;
  modal.classList.remove('open');
  modal.style.display = 'none';
  document.body.classList.remove('stt-modal-open');
}

function renderSttLines(){
  const body = document.getElementById('sttbody');
  const countEl = document.getElementById('sttcount');
  if(!body || !curSttData) return;

  const dc = curSttData;
  let segs = dc.transcript_segments || (dc.meta && dc.meta.transcript_segments) || [];
  let rawText = dc.stt_transcript || '';

  let h = '';
  const isTrunc = !!(dc.stt_truncated || (dc.meta && dc.meta.stt_truncated));
  if(isTrunc){
    h += '<div class="stt-trunc-banner">⚠️ <strong>전사 일부 절단 상태</strong>: 글자 수 상한 또는 오디오 구간 누락으로 인해 음성 전사의 일부만 반영되었습니다. 전체 내용을 복원하려면 <code>claire video-reprocess --doc-id ' + esc(dc.id||'') + ' --apply --full-content</code>를 실행하십시오.</div>';
  }

  const q = (curSttFilter || '').toLowerCase().trim();
  let matchCount = 0;
  let totalCount = 0;

  if(segs && segs.length > 0){
    totalCount = segs.length;
    segs.forEach(s => {
      const startF = s.start_sec != null ? s.start_sec : (s.start != null ? s.start : 0.0);
      const totalSec = Math.floor(startF);
      const hrs = Math.floor(totalSec / 3600);
      const mins = Math.floor((totalSec % 3600) / 60);
      const secs = totalSec % 60;
      const ts = hrs > 0 ? (String(hrs).padStart(2,'0')+':'+String(mins).padStart(2,'0')+':'+String(secs).padStart(2,'0')) : (String(mins).padStart(2,'0')+':'+String(secs).padStart(2,'0'));
      const txt = String(s.text || '');

      const isMatch = !q || txt.toLowerCase().includes(q) || ts.includes(q);
      if(isMatch){
        matchCount++;
        let dispTxt = esc(txt);
        if(q){
          let ltxt = txt.toLowerCase(), lq = q.toLowerCase();
          let idx = 0, out = '';
          while(true){
            let next = ltxt.indexOf(lq, idx);
            if(next === -1){ out += esc(txt.slice(idx)); break; }
            out += esc(txt.slice(idx, next)) + '<mark>' + esc(txt.slice(next, next + q.length)) + '</mark>';
            idx = next + q.length;
          }
          dispTxt = out;
        }
        h += '<div class="stt-line' + (q ? ' highlight' : '') + '">';
        h += '<span class="stt-ts" title="클릭하여 타임스탬프 복사" data-ts="' + esc(ts) + '" onclick="copyTimestamp(this.dataset.ts)">[' + esc(ts) + ']</span>';
        h += '<span class="stt-text">' + dispTxt + '</span>';
        h += '</div>';
      }
    });
  } else if(rawText) {
    const lines = rawText.split('\\n');
    totalCount = lines.length;
    lines.forEach(line => {
      const trimmed = line.trim();
      if(!trimmed) return;
      const isMatch = !q || trimmed.toLowerCase().includes(q);
      if(isMatch){
        matchCount++;
        let dispTxt = esc(trimmed);
        if(q){
          let ltxt = trimmed.toLowerCase(), lq = q.toLowerCase();
          let idx = 0, out = '';
          while(true){
            let next = ltxt.indexOf(lq, idx);
            if(next === -1){ out += esc(trimmed.slice(idx)); break; }
            out += esc(trimmed.slice(idx, next)) + '<mark>' + esc(trimmed.slice(next, next + q.length)) + '</mark>';
            idx = next + q.length;
          }
          dispTxt = out;
        }
        h += '<div class="stt-line' + (q ? ' highlight' : '') + '"><span class="stt-text">' + dispTxt + '</span></div>';
      }
    });
  } else {
    h = '<p class="hint">저장된 전사 내용이 없습니다.</p>';
  }

  body.innerHTML = h;
  if(countEl){
    if(q){
      countEl.textContent = matchCount.toLocaleString() + ' / ' + totalCount.toLocaleString() + '개 일치';
    } else {
      countEl.textContent = totalCount > 0 ? totalCount.toLocaleString() + '개 항목' : '';
    }
  }
}

function filterSttLines(val){
  curSttFilter = val;
  renderSttLines();
}

function copyTimestamp(ts){
  if(navigator.clipboard && navigator.clipboard.writeText){
    navigator.clipboard.writeText('[' + ts + ']').catch(()=>{});
  }
}

function copySttText(withTs){
  if(!curSttData) return;
  const dc = curSttData;
  const segs = dc.transcript_segments || (dc.meta && dc.meta.transcript_segments) || [];
  let out = '';
  if(segs && segs.length > 0){
    const lines = segs.map(s => {
      const startF = s.start_sec != null ? s.start_sec : (s.start != null ? s.start : 0.0);
      const totalSec = Math.floor(startF);
      const hrs = Math.floor(totalSec / 3600);
      const mins = Math.floor((totalSec % 3600) / 60);
      const secs = totalSec % 60;
      const ts = hrs > 0 ? (String(hrs).padStart(2,'0')+':'+String(mins).padStart(2,'0')+':'+String(secs).padStart(2,'0')) : (String(mins).padStart(2,'0')+':'+String(secs).padStart(2,'0'));
      const txt = String(s.text || '').trim();
      return withTs ? ('[' + ts + '] ' + txt) : txt;
    });
    out = lines.join('\\n');
  } else if(dc.stt_transcript) {
    out = dc.stt_transcript;
    if(!withTs){
      out = out.replace(/^\\[\\d{1,2}:\\d{2}(?::\\d{2})?\\]\\s*/gm, '');
    }
  }
  if(out){
    navigator.clipboard.writeText(out).then(()=>{
      alert('클립보드에 전사 내용이 복사되었습니다.');
    }).catch(()=>{
      alert('복사에 실패했습니다.');
    });
  }
}

if(typeof window !== 'undefined' && typeof window.addEventListener === 'function'){
  window.addEventListener('keydown', function(e){
    if(e.key === 'Escape'){
      const m = document.getElementById('sttmodal');
      if(m && m.classList.contains('open')){
        e.stopPropagation();
        closeSttReader();
      }
    }
  });
}
// [1홉 병합, ONEHOP_MERGE_DESIGN.md] 이 문서에 흡수된 부가 출처 목록(원문 링크 계보).
function extraSourcesHtml(dc){
  const es=dc.extra_sources||[];
  if(!es.length) return '';
  return '<div class=rsection>병합된 출처 ('+es.length+')</div><ul class=srclist>'+
    es.map(s=>'<li><a href="'+esc(s.url||'')+'" target=_blank rel=noopener>'+
      esc(s.title||s.url||'')+'</a></li>').join('')+'</ul>';
}
function closeReader(focus=false){
  if(typeof window !== 'undefined' && window.history && window.history.state && window.history.state.modal === 'reader' && !isPoppingHistory){
    window.history.back();
    return;
  }
  document.title = 'Claire Bible — 지식 그래프';
  hideNodePop();
  const r=document.getElementById('reader');
  r.classList.remove('open'); r.setAttribute('aria-hidden','true'); r.setAttribute('aria-busy','false');
  document.body.classList.remove('reader-open');
  setReaderBackgroundInert(false); syncWorkspaceLayout();
  const sb=document.getElementById('sharebox'); if(sb) sb.className='sharebox';
  let target=readerReturnFocus && readerReturnFocus.isConnected ? readerReturnFocus : null;
  if(!target && readerReturnDocId){
    target=[...document.querySelectorAll('[data-read-doc]')].find(el=>el.dataset.readDoc===readerReturnDocId);
  }
  if(!target) target=mobileMQ.matches ? paneTabs[activePane] : document.getElementById('q');
  readerReturnFocus=null; readerReturnDocId=null;
  replaceAppHistory({ modal: getActiveModalName() });
  if(focus && target) requestAnimationFrame(()=>target.focus());
  else requestAnimationFrame(()=>{ if(target) target.focus(); });
  if(typeof window.gtag === 'function'){
    try{
      window.gtag('event', 'page_view', {
        page_title: document.title,
        page_location: window.location.origin + '/'
      });
    }catch(_){}
  }
}

// --- 문서 공유 핫링크 — 세션 토큰(nginx 통과)과 별개의, 이 문서만 여는 읽기전용 링크 ---
// /share 가 공유 토큰을 발급(인증 필요) → /p?s=token 은 비인증으로 그 문서만 보여준다.
async function editDocTitle(){
  if(!canWrite() || !curReaderDoc) return;
  const current = (curReaderDocData && curReaderDocData.title && curReaderDocData.title !== '(제목 없음)') ? curReaderDocData.title : '';
  const newTitle = prompt('새 제목을 입력하세요:', current);
  if(newTitle === null) return;
  const trimmed = newTitle.trim();
  try{
    const r = await fetch('document/title', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: curReaderDoc, title: trimmed})
    });
    if(r.status === 401 || r.status === 404){
      expireWriteAccess();
      alert('세션 만료 또는 권한 없음 — 텔레그램 /web 으로 다시 접속하세요');
      return;
    }
    if(!r.ok){
      const err = await r.json().catch(()=>({}));
      alert('제목 변경 실패: ' + (err.detail || err.error || ('HTTP ' + r.status)));
      return;
    }
    const d = await r.json();
    const updatedTitle = d.title || '(제목 없음)';
    if(curReaderDocData) curReaderDocData.title = d.title;
    document.getElementById('rtitle').innerHTML = esc(updatedTitle)
      + (curReaderDocData && curReaderDocData.source_type ? ' <span class=rmeta>' + esc(curReaderDocData.source_type) + '</span>' : '');
    const dc = allDocs && allDocs.find(x => x.id === curReaderDoc);
    if(dc){
      dc.title = d.title;
      renderDocs(document.getElementById('docq') ? document.getElementById('docq').value : '');
    }
  }catch(e){
    alert('제목 변경 실패: ' + String(e));
  }
}
async function shareDoc(){
  if(!canWrite() || !curReaderDoc) return;
  const sb=document.getElementById('sharebox');
  sb.className='sharebox on'; sb.innerHTML='<span class=pt>공유 링크 생성 중…</span>';
  try{
    const r=await fetch('share',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({doc_id:curReaderDoc})});
    if(r.status===401||r.status===404){ expireWriteAccess();
      sb.innerHTML='<span class=pt>세션 만료 — 텔레그램 /web 으로 다시 접속하세요</span>'; return; }
    const d=await r.json();
    if(d.error||!d.path){ sb.innerHTML='<span class=pt>공유 실패: '+esc(d.error||'알 수 없음')+'</span>'; return; }
    const url=location.origin+d.path;
    let copied=false;
    try{ await navigator.clipboard.writeText(url); copied=true; }catch(_){}
    if(typeof window.gtag === 'function' && curReaderDoc){
      try{ window.gtag('event', 'share', { method: 'link', content_type: 'document', item_id: curReaderDoc }); }catch(_){}
    }
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
function relayout(force=false){ if(!net) return;
  const el=document.getElementById('net'); if(!el) return;
  const r=el.getBoundingClientRect();
  if(r.width<=0 || r.height<=0) return;
  const isFirstVisibleSize = (lastNetSize.w <= 0 || lastNetSize.h <= 0);
  if(netBusy && !force && !isFirstVisibleSize) return;
  if(!force && !isFirstVisibleSize && Math.abs(r.width-lastNetSize.w)<1 && Math.abs(r.height-lastNetSize.h)<1) return;
  lastNetSize = {w:r.width, h:r.height};
  net.setSize(r.width+'px', r.height+'px'); net.redraw(); }
if(window.ResizeObserver){ new ResizeObserver(()=>relayout()).observe(document.getElementById('net')); }
window.addEventListener('resize', ()=>relayout());
window.addEventListener('orientationchange', ()=>setTimeout(()=>relayout(), 300));

function graphAnimation(value=true){ return reducedMotionMQ.matches ? false : value; }
function rememberGraphCamera(){
  if(!net || preservingGraphCamera || netBusy) return;
  graphCamera={position:net.getViewPosition(),scale:net.getScale()};
}
function relayoutPreservingCamera(){
  if(!net || netBusy) return;
  const el=document.getElementById('net');
  if(!el) return;
  const r=el.getBoundingClientRect();
  if(r.width<=0 || r.height<=0) return;
  const sizeChanged = Math.abs(r.width-lastNetSize.w)>=2 || Math.abs(r.height-lastNetSize.h)>=2;
  const saved=graphCamera;
  if(sizeChanged){
    preservingGraphCamera=true;
    relayout(true);
    preservingGraphCamera=false;
    if(saved && net){
      net.moveTo({position:saved.position,scale:saved.scale,animation:false});
    }
  }
  rememberGraphCamera();
}
function syncWorkspaceLayout(){
  document.body.dataset.activePane=activePane;
  document.body.dataset.centerView=centerView;
  paneNames.forEach(name=>{
    const selected=name===activePane;
    const tab=paneTabs[name];
    if(tab){
      tab.setAttribute('aria-selected', selected?'true':'false');
      tab.tabIndex=selected?0:-1;
    }
    const el=paneEls[name];
    if(el){
      if(mobileMQ.matches){
        el.setAttribute('aria-hidden', selected?'false':'true');
        el.inert=!selected;
      }else{
        el.setAttribute('aria-hidden','false');
        el.inert=false;
      }
    }
  });
  const isMobileOrCompact = compactMQ.matches || mobileMQ.matches;
  const isDrawerActive = isMobileOrCompact && (drawerOpen || detailOpen);
  document.body.classList.toggle('detail-open', isMobileOrCompact && detailOpen);
  document.body.classList.toggle('drawer-open', isDrawerActive);
  const moreBtn = document.getElementById('morebtn');
  if(moreBtn) moreBtn.setAttribute('aria-expanded', isDrawerActive?'true':'false');
  const menuBtn = document.getElementById('tab-menu');
  if(menuBtn) menuBtn.setAttribute('aria-expanded', (isMobileOrCompact && drawerOpen)?'true':'false');

  const dp = document.getElementById('detailpane');
  let detailVisible = false;
  if(dp){
    if(compactMQ.matches || mobileMQ.matches){
      dp.setAttribute('aria-hidden', isDrawerActive?'false':'true');
      dp.inert=!isDrawerActive;
      detailVisible = isDrawerActive;
    }else{
      dp.setAttribute('aria-hidden','false');
      dp.inert=false;
      detailVisible = true;
    }
  }

  // aside#detailpane의 aria-hidden이 false일 때 (또는 detailCompact 설정 시)
  // 우측 메뉴를 아이콘만 보여주는 컴팩트 레일 모드로 전환
  const isCompact = !mobileMQ.matches && detailVisible && detailCompact;
  document.body.classList.toggle('detail-compact', isCompact);
  const wrapEl = document.getElementById('wrap');
  if(wrapEl) wrapEl.classList.toggle('detail-compact', isCompact);
  if(dp) dp.classList.toggle('compact-rail', isCompact);
  const toggleBtn = document.getElementById('detailtogglebtn');
  if(toggleBtn){
    toggleBtn.setAttribute('aria-expanded', isCompact ? 'false' : 'true');
    toggleBtn.title = isCompact ? '우측 메뉴 펼치기' : '우측 메뉴 축소(아이콘 모드)';
    toggleBtn.setAttribute('aria-label', isCompact ? '우측 메뉴 펼치기' : '우측 메뉴 축소(아이콘 모드)');
  }

  if(activePane==='graph'){
    requestAnimationFrame(()=>{ relayoutPreservingCamera(); applyTouchMode(); });
  }
}
function revealWorkspace(name, focusTab=false){
  hideNodePop();
  if(!paneNames.includes(name)) return;
  if(activePane==='graph' && !netBusy) rememberGraphCamera();
  if(activePane !== name) pushAppHistory({ pane: name, modal: null });
  activePane=name;
  const r=document.getElementById('reader');
  if(r && r.classList.contains('open') && typeof closeReader==='function') closeReader();
  if(name==='graph'){
    setCenterView('graph');
    if(!activeDoc){
      fitGraphContext();
    }
  }
  if(name==='docs'){
    docSearchActive=false;
    const q=document.getElementById('docq');
    if(q && q.value){ q.value=''; }
    renderDocs('');
  }
  detailOpen=false;
  drawerOpen=false;
  detailReturnFocus=null;
  if(name!=='graph') closeGraphDocPicker();
  closeToolsMenu();
  syncWorkspaceLayout();
  if(focusTab && mobileMQ.matches && paneTabs[name]) paneTabs[name].focus();
}
function openDetailPane(){
  hideNodePop();
  detailReturnFocus=document.activeElement;
  detailOpen=true;
  drawerOpen=true;
  detailCompact=false;
  try{ localStorage.setItem('claireDetailCompact', 'false'); }catch(_){}
  if((compactMQ.matches || mobileMQ.matches)) pushAppHistory({ modal: 'drawer' });
  closeGraphDocPicker();
  syncWorkspaceLayout();
}
function closeDetailPane(){
  if(typeof window !== 'undefined' && window.history && window.history.state && window.history.state.modal === 'drawer' && !isPoppingHistory){
    window.history.back();
    return;
  }
  hideNodePop();
  detailOpen=false;
  drawerOpen=false;
  syncWorkspaceLayout();
  let target=detailReturnFocus && detailReturnFocus.isConnected ? detailReturnFocus : null;
  if(!target) target=(compactMQ.matches || mobileMQ.matches) ? (document.getElementById('tab-menu') || paneTabs[activePane] || document.getElementById('tab-docs')) : paneEls[activePane];
  detailReturnFocus=null;
  replaceAppHistory({ modal: getActiveModalName() });
  requestAnimationFrame(()=>{ if(target) target.focus(); });
}
function openDrawer(){
  hideNodePop();
  detailReturnFocus=document.activeElement;
  drawerOpen=true;
  detailOpen=true;
  if((compactMQ.matches || mobileMQ.matches)) pushAppHistory({ modal: 'drawer' });
  closeGraphDocPicker();
  syncWorkspaceLayout();
}
function closeDrawer(focus=false){
  if(typeof window !== 'undefined' && window.history && window.history.state && window.history.state.modal === 'drawer' && !isPoppingHistory){
    window.history.back();
    return;
  }
  hideNodePop();
  drawerOpen=false;
  detailOpen=false;
  syncWorkspaceLayout();
  let target=detailReturnFocus && detailReturnFocus.isConnected ? detailReturnFocus : null;
  if(!target) target=(compactMQ.matches || mobileMQ.matches) ? (document.getElementById('tab-menu') || paneTabs[activePane] || document.getElementById('tab-docs')) : document.getElementById('morebtn');
  detailReturnFocus=null;
  replaceAppHistory({ modal: getActiveModalName() });
  if(focus && target) requestAnimationFrame(()=>target.focus());
}
function toggleDrawer(){
  if(drawerOpen || detailOpen) closeDrawer(true);
  else openDrawer();
}
function openGraphFromDrawer(){
  openDocGraph(activeDoc || curReaderDoc || (mobileMQ.matches ? (getRecentDocId() || docWithMostNodes()) : null));
}
function focusMobileSearch(){
  revealWorkspace('docs');
  docSearchActive=true;
  const q=document.getElementById('docq');
  if(q){ q.value=''; renderDocs(''); q.focus(); }
}
function closeToolsMenu(focus=false){ closeDrawer(focus); }
function toggleToolsMenu(){ toggleDrawer(); }
function graphSelectableDocs(){
  const visible=allDocs.filter(dc=>dc.hidden!==1);
  return visible.filter(dc=>dc.pinned===1).concat(visible.filter(dc=>dc.pinned!==1));
}
function syncGraphDocNav(){
  const docs=graphSelectableDocs();
  const dc=activeDoc ? allDocs.find(item=>item.id===activeDoc) : null;
  const pick=document.getElementById('graphdocpick');
  document.getElementById('graphdoclabel').textContent=dc ? dc.title : '전체 그래프';
  pick.setAttribute('aria-label',dc ? '자료 전환, 현재 '+dc.title : '자료 선택, 현재 전체 그래프');
  const current=docs.findIndex(item=>item.id===activeDoc);
  const usable=current>=0 && docs.length>1;
  const prev=document.getElementById('graphdocprev'), next=document.getElementById('graphdocnext');
  prev.disabled=!usable; next.disabled=!usable;
  if(usable){
    const prevDoc=docs[(current-1+docs.length)%docs.length];
    const nextDoc=docs[(current+1)%docs.length];
    prev.setAttribute('aria-label','이전 자료: '+prevDoc.title);
    next.setAttribute('aria-label','다음 자료: '+nextDoc.title);
    prev.title='이전 자료: '+prevDoc.title;
    next.title='다음 자료: '+nextDoc.title;
  }else{
    prev.setAttribute('aria-label','이전 자료');
    next.setAttribute('aria-label','다음 자료');
    prev.title='이전 자료'; next.title='다음 자료';
  }
  const menu=document.getElementById('graphdocmenu');
  if(!menu.hidden) renderGraphDocPicker(document.getElementById('graphdocq').value);
}
function renderGraphDocPicker(filter=''){
  const q=filter.trim().toLowerCase();
  const docs=graphSelectableDocs().filter(dc=>
    !q || ((dc.title||'')+' '+(dc.summary||'')).toLowerCase().includes(q));
  let h='<button id="graphdocall" class="graphdocoption" data-graph-doc=""'+
    (!activeDoc?' aria-current="true"':'')+'>전체 그래프</button>';
  h+=docs.map(dc=>'<button class="graphdocoption" data-graph-doc="'+esc(dc.id)+'"'+
      (dc.id===activeDoc?' aria-current="true"':'')+'>'+
      (dc.pinned===1?'⭐ ':'')+esc(dc.title||'(제목 없음)')+
      (dc.source_type?'<small>'+esc(dc.source_type)+'</small>':'')+'</button>').join('');
  if(!docs.length) h+='<div id="graphdocempty">검색 결과가 없습니다.</div>';
  document.getElementById('graphdoclist').innerHTML=h;
}
function openGraphDocPicker(pushHist=true){
  const menu=document.getElementById('graphdocmenu'), q=document.getElementById('graphdocq');
  if(drawerOpen || detailOpen){
    closeDrawer(false, false);
  }
  q.value='';
  renderGraphDocPicker();
  menu.hidden=false;
  menu.inert=false;
  menu.setAttribute('aria-hidden','false');
  document.getElementById('graphdocpick').setAttribute('aria-expanded','true');
  if(pushHist) pushAppHistory({ modal: 'graphdocmenu' });
  requestAnimationFrame(()=>q.focus());
}
function closeGraphDocPicker(focus=true){
  if(typeof window !== 'undefined' && window.history && window.history.state && window.history.state.modal === 'graphdocmenu' && !isPoppingHistory){
    window.history.back();
    return;
  }
  const menu=document.getElementById('graphdocmenu');
  if(menu.hidden) return;
  menu.hidden=true;
  menu.inert=true;
  menu.setAttribute('aria-hidden','true');
  document.getElementById('graphdocpick').setAttribute('aria-expanded','false');
  replaceAppHistory({ modal: getActiveModalName() });
  if(focus) requestAnimationFrame(()=>document.getElementById('graphdocpick').focus());
}
function toggleGraphDocPicker(){
  const menu=document.getElementById('graphdocmenu');
  if(menu.hidden) openGraphDocPicker(); else closeGraphDocPicker(true);
}
function chooseGraphDoc(id){
  closeGraphDocPicker();
  setActiveDoc(id||null);
  requestAnimationFrame(()=>document.getElementById('graphdocpick').focus());
}
function stepGraphDoc(delta){
  const docs=graphSelectableDocs();
  const current=docs.findIndex(dc=>dc.id===activeDoc);
  if(current<0 || docs.length<2) return;
  const next=(current+delta+docs.length)%docs.length;
  chooseGraphDoc(docs[next].id);
}
const gdl = document.getElementById('graphdoclist');
if(gdl){
  gdl.addEventListener('click',e=>{
    const button=e.target.closest('[data-graph-doc]');
    if(button) chooseGraphDoc(button.dataset.graphDoc);
  });
}
syncGraphDocNav();
const wt = document.getElementById('worktabs');
if(wt){
  wt.addEventListener('click',e=>{
    const b=e.target.closest('[role=tab]'); if(b && b.dataset.pane) revealWorkspace(b.dataset.pane);
  });
  wt.addEventListener('keydown',e=>{
    const b=e.target.closest('[role=tab]'); if(!b) return;
    let i=paneNames.indexOf(b.dataset.pane);
    if(e.key==='ArrowRight') i=(i+1)%paneNames.length;
    else if(e.key==='ArrowLeft') i=(i+paneNames.length-1)%paneNames.length;
    else if(e.key==='Home') i=0;
    else if(e.key==='End') i=paneNames.length-1;
    else return;
    e.preventDefault(); revealWorkspace(paneNames[i],true);
  });
}
document.addEventListener('pointerdown',e=>{
  const bar=document.getElementById('bar');
  const pane=document.getElementById('detailpane');
  const bnav=document.getElementById('worktabs');
  if((drawerOpen||detailOpen) && pane && (!bar||!bar.contains(e.target)) && (!bnav||!bnav.contains(e.target)) && !pane.contains(e.target)) closeDrawer(false, true);
  const nav=document.getElementById('graphdocnav');
  const gdm=document.getElementById('graphdocmenu');
  if(gdm && !gdm.hidden && nav && !nav.contains(e.target)) closeGraphDocPicker(false, true);
});
document.addEventListener('keydown',e=>{
  if(e.key==='Escape' && (drawerOpen||detailOpen)){
    e.preventDefault(); closeDrawer(true, true);
  }
});
function responsiveChanged(){ closeToolsMenu(); syncWorkspaceLayout(); }
mobileMQ.addEventListener('change',responsiveChanged);
compactMQ.addEventListener('change',responsiveChanged);
toolbarMQ.addEventListener('change',responsiveChanged);
syncWorkspaceLayout();

// 단일-pane 그래프는 페이지 스크롤과 경쟁하지 않는다. vis/hammer가 그래프의 양방향
// pan을 처리하도록 touch-action:none을 유지하고, 다른 pane은 inert로 입력을 받지 않는다.
function applyTouchMode(){
  document.querySelectorAll('#net, #net div, #net canvas').forEach(el=>{ el.style.touchAction='none'; });
}
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
  net.moveTo({scale:scale, animation:graphAnimation({duration:150})});
}
function cameraToNodes(ids){
  if(!net || !ids.length) return;
  if(ids.length===1) net.focus(ids[0],{scale:1.2,animation:graphAnimation(true)});
  else net.fit({nodes:ids,animation:graphAnimation(true)});
}
function fitGraphContext(){
  if(!net || !allNodes) return;
  let ids=[];
  if(pathNodes && pathNodes.size) ids=[...pathNodes];
  else if(selectedNodeId) ids=[selectedNodeId];
  else{
    allNodes.forEach(n=>{
      if(n.hidden || (typeof n.id==='string' && n.id.indexOf('cl_')===0)) return;
      let match=true;
      if(activeDoc) match=match && (n.sources||[]).includes(activeDoc);
      if(highlightSet) match=match && highlightSet.has(n.id);
      if(match) ids.push(n.id);
    });
  }
  if(!ids.length) resetGraphCamera(); else cameraToNodes(ids);
}
function resetGraphCamera(){
  if(!net || !allNodes) return;
  const ids=[];
  allNodes.forEach(n=>{
    if(!n.hidden && !(typeof n.id==='string' && n.id.indexOf('cl_')===0)) ids.push(n.id);
  });
  if(ids.length) net.fit({nodes:ids,animation:graphAnimation(true)});
}

fetch('graph').then(r=>{ if(!r.ok) throw new Error('graph fetch HTTP '+r.status); return r.json(); }).then(d=>{
  if(typeof vis !== 'undefined' && vis.DataSet && vis.Network){
    const rawNodes = ((d && d.nodes) || []).map(n => ({
      ...n,
      size: nodeRadius(n.degree),
      font: { size: nodeFontSize(n.degree) }
    }));
    allNodes = new vis.DataSet(rawNodes);
    allEdges = new vis.DataSet((d && d.edges) || []);
    if(!d || !d.nodes || !d.nodes.length){ graphStabilized=true; }
    const totalCount = rawNodes.length;
    let initialDeg = 0;
    if(totalCount >= 200){
      initialDeg = 2;
    } else if(totalCount >= 80){
      initialDeg = 1;
    } else {
      initialDeg = 0;
    }
    curMinDeg = initialDeg;
    const sl = document.getElementById('fslider');
    if(sl){ sl.max = (d && d.stats && d.stats.max_degree) || 0; sl.value = initialDeg; }
    const fmin = document.getElementById('fmin');
    if(fmin) fmin.textContent = initialDeg;
    updateDegPresets();
    allTypes=[...new Set(rawNodes.map(n=>n.group))].sort();
    allRelTypes=[...new Set(((d && d.edges) || []).map(e=>e.label).filter(Boolean))].sort();
    renderLegend();
    const th=T();
    const netBg = (typeof getComputedStyle==='function'?getComputedStyle(document.documentElement).getPropertyValue('--net-bg').trim():'')||'#ffffff';
    const opts = {
      nodes:{shape:'dot',size:14,font:{color:th.nodeFont,size:13},borderWidth:1,borderWidthSelected:3},
      edges:{color:{color:th.edge,highlight:th.edgeHi},
        font:{color:th.nodeFont,size:0,strokeWidth:3,strokeColor:netBg},smooth:false},
      groups:buildGroups(),
      physics:getPhysicsOpts(totalCount),
      interaction:{hover:true,tooltipDelay:120,multiselect:true,zoomView:false}
    };
    const netEl = document.getElementById('net');
    if(netEl) net = new vis.Network(netEl, {nodes:allNodes, edges:allEdges}, opts);
    isDraggingNode = false;
    clearTimeout(settleTimer);
    settleTimer = setTimeout(()=>{
      if(net && !isDraggingNode){
        net.setOptions({physics:false});
      }
    }, 2500);
    requestAnimationFrame(()=>{ relayout(); setTimeout(relayout, 300); });
    applyTouchMode();
    setupWheelZoom();
  } else {
    const st = document.getElementById('stat');
    if(st) st.textContent = '데이터 준비 완료';
  }
  // pane 전환·주소창 접힘 등으로 viewport가 바뀌어도 fit 애니메이션 중 setSize가
  // 카메라를 흔들지 않게 한다. 드래그/애니메이션 중엔 relayout
  // 을 미루고, 크기 변화가 실제로 없으면 아예 스킵(불필요한 setSize+redraw 로 인한 churn 방지).
  // animationFinished 에만 기대면 발화 안 되는 경우(실측: 0개 노드 fit 등) netBusy 가 영원히
  // true 로 굳어 relayout 이 죽는 더 나쁜 회귀가 됨 → 항상 풀리는 타임아웃을 안전망으로 병행.
  let busyTimer = null;
  function markBusy(ms){ netBusy = true; clearTimeout(busyTimer); busyTimer = setTimeout(()=>{netBusy=false;}, ms); }
  function getAnimDuration(opts){
    if(!opts || !opts.animation) return 0;
    if(typeof opts.animation==='object' && typeof opts.animation.duration==='number') return opts.animation.duration;
    return 1000;
  }
  if(net){
    net.on('dragStart', p => {
      netBusy = true;
      hideNodePop();
      if(p && p.nodes && p.nodes.length){
        isDraggingNode = true;
        clearTimeout(settleTimer);
        net.setOptions({physics:true});
      }
    });
    net.on('dragEnd', () => {
      netBusy = false;
      rememberGraphCamera();
      if(isDraggingNode){
        isDraggingNode = false;
        setTimeout(()=>{
          if(net && !isDraggingNode) net.setOptions({physics:false});
        }, 800);
      }
    });
    net.on('animationFinished', () => {
      netBusy = false;
      clearTimeout(busyTimer);
      const show=net.getScale()>=1.45;
      if(show!==edgeLabelsByZoom){ edgeLabelsByZoom=show; applyView(); }
      rememberGraphCamera();
    });
    net.on('stabilized', ()=>{
      graphStabilized=true;
      if(!netBusy) rememberGraphCamera();
    });
    const _fit = net.fit.bind(net), _focus = net.focus.bind(net), _moveTo = net.moveTo.bind(net);
    net.fit = (opts) => {
      const dur = getAnimDuration(opts);
      if(dur > 0) markBusy(dur + 350);
      return _fit(opts);
    };
    net.focus = (id, opts) => {
      const dur = getAnimDuration(opts);
      if(dur > 0) markBusy(dur + 350);
      return _focus(id, opts);
    };
    net.moveTo = (opts) => {
      const dur = getAnimDuration(opts);
      if(dur > 0) markBusy(dur + 350);
      return _moveTo(opts);
    };
    net.on('click', p => {
      if(!p.nodes.length){
        // 빈 캔버스 클릭: inspect 만 해제하고 검색(라벨/의미) 강조 선택은 유지(이슈4).
        // vis 가 내부적으로 선택을 비우므로 그 뒤에 검색 선택을 다시 적용한다.
        hideNodePop();
        selectedNodeId=null;
        applyView();
        if(highlightSet && highlightSet.size) setTimeout(restoreSelection, 0);
        return;
      }
      const id=p.nodes[0], ev=p.event.srcEvent;
      if(pathMode){ hideNodePop(); pickPathNode(id); return; }                // 경로 모드: 클릭으로 시작/끝 노드 지정
      if(ev && (ev.ctrlKey||ev.metaKey) && canWrite()){ hideNodePop(); toggleSynth(id); } // owner만 종합 수집
      else {
        const isMobile = (compactMQ && compactMQ.matches) || (mobileMQ && mobileMQ.matches);
        let px = null, py = null;
        if(ev){
          if(ev.clientX != null && ev.clientY != null){
            px = ev.clientX; py = ev.clientY;
          } else if(ev.changedTouches && ev.changedTouches[0]){
            px = ev.changedTouches[0].clientX; py = ev.changedTouches[0].clientY;
          } else if(ev.touches && ev.touches[0]){
            px = ev.touches[0].clientX; py = ev.touches[0].clientY;
          }
        }
        if(px == null || py == null){
          try {
            const netRect = netEl ? netEl.getBoundingClientRect() : { left: 0, top: 0 };
            if(p.pointer && p.pointer.DOM){
              px = p.pointer.DOM.x + netRect.left;
              py = p.pointer.DOM.y + netRect.top;
            } else {
              const domPos = net.canvasToDOM(net.getPosition(id));
              px = domPos.x + netRect.left;
              py = domPos.y + netRect.top;
            }
          } catch(_) {}
        }
        if(px == null || py == null){
          px = mouseXY.x; py = mouseXY.y;
        }
        if(isMobile){
          // 모바일: 탭 시 마우스 롤오버 요약 팝업 표시 및 노드 선택
          // 이미 팝업이 떠 있는 동일 노드를 다시 탭하면 상세 서랍을 열어준다
          if(selectedNodeId === id && nodepop && nodepop.style.display !== 'none'){
            hideNodePop();
            openDetailPane();
          } else {
            selectedNodeId=id;
            loadNode(id, false);
            showNodePop(id, px, py);
          }
        } else {
          hideNodePop();
          selectedNodeId=id;
          loadNode(id, true);
          openDetailPane();
        }
      }
    });
    // hover → 1.5초 뒤 마우스 위치에 작은 요약 팝업(우측 패널은 안 건드림 — 난잡함 해소, 사용자 요구).
    // 우측 패널은 클릭(inspect)일 때만 바뀐다 → hover 가 패널/선택을 흔들지 않아 복원 로직도 불필요.
    net.on('hoverNode', p => {
      const isMobile = (compactMQ && compactMQ.matches) || (mobileMQ && mobileMQ.matches);
      if(isMobile) return;
      clearTimeout(hoverTimer);
      if(!canShowNodePop(p.node)) return;
      hoverTimer=setTimeout(()=>showNodePop(p.node), 1500);
    });
    net.on('blurNode', () => {
      const isMobile = (compactMQ && compactMQ.matches) || (mobileMQ && mobileMQ.matches);
      if(!isMobile) hideNodePop();
    });
    net.on('hold', () => {
      const isMobile = (compactMQ && compactMQ.matches) || (mobileMQ && mobileMQ.matches);
      if(!isMobile) hideNodePop();
    });
    net.on('dragStart', hideNodePop);   // 드래그/줌 중엔 팝업 숨김(커서를 따라다니지 않게)
    net.on('selectEdge', p=>{ selectedEdgeIds=new Set(p.edges||[]); applyView(); });
    net.on('deselectEdge', ()=>{ selectedEdgeIds.clear(); applyView(); });
    let zoomDebounceTimer = null;
    net.on('zoom', ()=>{
      hideNodePop();
      if(!netBusy){
        rememberGraphCamera();
        clearTimeout(zoomDebounceTimer);
        zoomDebounceTimer = setTimeout(()=>{
          if(!net) return;
          const show=net.getScale()>=1.45;
          if(show!==edgeLabelsByZoom){ edgeLabelsByZoom=show; applyView(); }
        }, 120);
      }
    });
  }
  applyView();
  if(activeDoc && !selectedNodeId){
    if(curReaderDocData && curReaderDocData.id===activeDoc) renderDocPanel(curReaderDocData);
    else loadDocPanel(activeDoc);
  }
}).catch(err => {
  console.warn('graph load error:', err);
  const st=document.getElementById('stat');
  if(st && st.textContent.indexOf('로딩')!==-1) st.textContent='그래프 준비 완료';
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
document.addEventListener('keydown', e=>{
  if(handleReaderKey(e)) return;
  if(e.key!=='Escape') return;
  const bar=document.getElementById('bar');
  if(!document.getElementById('graphdocmenu').hidden){ closeGraphDocPicker(true, true); return; }
  if(bar.classList.contains('tools-open')){ closeToolsMenu(true, true); return; }
  if(document.body.classList.contains('detail-open')){ closeDetailPane(true); return; }
  clearSelections(); });

function loadNode(id, hidePop=true){
  if(hidePop) hideNodePop();
  if(net) net.selectNodes([id]);   // 클릭 inspect — hover 는 더 이상 패널을 안 쓴다(팝업으로 분리)
  applyView();                     // 선택 노드 주변 엣지만 강조하고 관계 라벨을 펼친다
  fetch('node?id='+encodeURIComponent(id)).then(r=>r.json()).then(renderPanel);
}
function renderPanel(d){
  if(!d || d.error){ panel.innerHTML='<p class=hint>노드를 찾을 수 없습니다.</p>'; return; }
  const inSet = synthSet.has(d.id);
  // 문서를 고른 상태에서 노드로 들어왔으면 문서 패널로 한 번에 돌아갈 링크.
  let h = activeDoc ? '<span class=backlink onclick="loadDocPanel(activeDoc)">← 문서로 돌아가기</span>' : '';
  h+='<h2>'+esc(d.name)+' <small>'+esc(d.type)+(d.provisional?' ⚠️provisional':'')+'</small></h2>';
  // readonly(/webro) 세션은 종합(/synthesize)이 서버에서 막혀있어 버튼 자체를 안 그림.
  if(canWrite()) h+='<button class="sec" onclick="addToSynth(\\''+d.id+'\\')">'+(inSet?'✓ 종합 목록에 있음':'➕ 종합에 추가')+'</button>';
  if(d.aliases.length) h+='<p class=al>별칭: '+d.aliases.map(esc).join(', ')+'</p>';
  if(d.observations.length){ h+='<h3>관찰 · 주장</h3><ul>'+
    d.observations.map(o=>'<li>'+esc(o)+'</li>').join('')+'</ul>'; }
  if(d.documents.length){ h+='<h3>출처 문서 ('+d.documents.length+')</h3>';
    // 설명(summary) → 📖 본문 보기(중앙 리더로 열기) → 원문 링크 순.
    d.documents.forEach(dc=>{ h+='<div class=doc><b>'+esc(dc.title)+'</b>'+
      (dc.summary?'<p>'+esc(dc.summary)+'</p>':'')+
      ((dc.detail||dc.summary)?'<button class=readbtn data-read-doc="'+esc(dc.id)+
        '" onclick="openReader(\\''+dc.id+'\\')">📖 본문 보기</button>':'')+
      (dc.url?'<p class=src><a href="'+esc(dc.url)+'" target=_blank rel=noopener>↗ 원문 열기</a></p>':'')+
      '</div>'; }); }
  if(d.neighbors.length){ h+='<h3>연결 ('+d.neighbors.length+')</h3><ul>';
    d.neighbors.forEach(n=>{ const ar=n.dir=='out'?'→':'←';
      h+='<li><span class=rel>'+esc(n.rel)+'</span> '+ar+
         ' <a href="#" onclick="loadNode(\\''+n.id+'\\');return false">'+esc(n.name)+
         '</a> <small>'+esc(n.type)+'</small></li>'; }); h+='</ul>'; }
  // 맥락 확장 조사 — 읽다가 더 알고 싶은 키워드/문장을 지금 맥락으로 조사해 그래프 확장.
  // readonly 는 /research 도 서버에서 막혀있어 입력창·버튼 자체를 안 그림.
  if(canWrite()) h+='<h3>🔬 더 알아보기</h3>'+
    '<div class=research><input id="rq" placeholder="더 알고 싶은 키워드/문장" '+
    'onkeydown="if(event.key===\\'Enter\\')doResearch()"/>'+
    '<button onclick="doResearch()">조사</button></div>'+
    '<p class=al>지금 보는 맥락에 맞춰 웹 조사 → 맥락 일치·품질 통과 시 그래프에 추가됩니다.</p>';
  panel.innerHTML=h;
  if(typeof window.gtag === 'function' && d && d.id){
    try{
      window.gtag('event', 'select_content', {
        content_type: 'node',
        item_id: d.id
      });
    }catch(_){}
  }
}

// --- 맥락 확장 조사: 조사(grounding)→판정 게이트→통과 시 그래프 적재(서버) ---
// 서버가 NDJSON 스트림으로 진행 이벤트({stage,msg})를 흘리고 마지막 줄이
// {done:true, result:{...}} — 마냥 기다리지 않고 단계·rate limit 상황을 실시간 표시(피드백).
async function doResearch(){
  if(!canWrite()) return;
  const q=((document.getElementById('rq')||{}).value||'').trim();
  if(!q){ alert('조사할 키워드/문장을 입력하세요.'); return; }
  if(!selectedNodeId && !activeDoc){ alert('노드를 선택하거나 문서를 연 뒤 조사하세요.'); return; }
  const backId=selectedNodeId;
  panel.innerHTML='<h2>🔬 조사: '+esc(q)+'</h2><p class="al" id="relapsed">시작…</p><ul id="rprog"></ul>';
  openDetailPane();
  const t0=Date.now();
  const timer=setInterval(()=>{ const el=document.getElementById('relapsed');
    if(el) el.textContent='⏱ 경과 '+Math.round((Date.now()-t0)/1000)+'s'; else clearInterval(timer); },1000);
  let result=null;
  try{
    const r=await fetch('research',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({query:q, node_id:selectedNodeId, doc_id:activeDoc})});
    if(r.status===401||r.status===404){ clearInterval(timer); expireWriteAccess();
      panel.innerHTML='<p class=hint>세션 만료 — 텔레그램 /web 으로 다시 접속하세요</p>'; return; }
    if(!r.ok){ clearInterval(timer); let d={};
      try{ d=await r.json(); }catch(_){}
      panel.innerHTML='<p class=hint>조사 요청 실패: '+esc(d.error||('HTTP '+r.status))+'</p>'; return; }
    if(!r.body) throw new Error('스트림 본문이 없습니다');
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
  if(!canWrite()) return;
  panel.innerHTML='<h2>➕ 자료 적재</h2>'+
    '<p class=al>URL 또는 메모 텍스트를 입력하고 보내면 AI가 내용을 분석하고 구조화하여 그래프 및 맞춤형 가독 본문으로 적재합니다.</p>'+
    '<p class=al style="color:var(--text-dim, #888);font-size:0.9em;margin:.4em 0;line-height:1.4">💡 <b>본문 작성 초점(Focus) 지정:</b><br>'+
    'URL을 입력한 후 <b>줄바꿈 두 번(엔터 2회)</b> 뒤에 원하는 작성 초점(예: 시스템 아키텍처 중심, 초보자 튜토리얼 관점 등)을 입력하면 맞춤형으로 본문이 작성됩니다.</p>'+
    '<textarea id="ingin" rows="5" style="width:100%;box-sizing:border-box" '+
    'placeholder="https://example.com/article&#10;&#10;시스템 아키텍처 및 내부 구조 중심 (줄바꿈 2번 후 초점 입력)"></textarea>'+
    '<div style="margin:.5em 0"><button onclick="runIngest()">보내기</button></div>';
  openDetailPane();
  const ta=document.getElementById('ingin'); if(ta) ta.focus();
}
async function runIngest(){
  if(!canWrite()) return;
  const ta=document.getElementById('ingin');
  const rawText=((ta||{}).value||'').trim();
  if(!rawText){ alert('적재할 URL 또는 텍스트를 입력하세요.'); return; }
  let payload = rawText;
  let directive = null;
  const sepIdx = rawText.search(/\\r?\\n\\s*\\r?\\n/);
  if(sepIdx !== -1){
    const firstPart = rawText.slice(0, sepIdx).trim();
    const secondPart = rawText.slice(sepIdx).trim();
    if(firstPart && secondPart){
      payload = firstPart;
      directive = secondPart;
    }
  }
  let labelText = '시작…';
  if(directive){
    labelText = '시작… (초점: ' + (directive.length > 20 ? directive.slice(0, 20) + '…' : directive) + ')';
  }
  panel.innerHTML='<h2>➕ 적재 중</h2><p class="al" id="ielapsed">' + esc(labelText) + '</p><ul id="iprog"></ul>';
  openDetailPane();
  const t0=Date.now();
  const timer=setInterval(()=>{ const el=document.getElementById('ielapsed');
    if(el) el.textContent='⏱ 경과 '+Math.round((Date.now()-t0)/1000)+'s' + (directive ? ' (초점: ' + esc(directive.length > 15 ? directive.slice(0, 15) + '…' : directive) + ')' : ''); else clearInterval(timer); },1000);
  let result=null;
  try{
    const bodyObj = {payload: payload};
    if(directive) bodyObj.directive = directive;
    const r=await fetch('ingest-stream',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(bodyObj)});
    if(r.status===401||r.status===404){ clearInterval(timer); expireWriteAccess();
      panel.innerHTML='<p class=hint>세션 만료 — 텔레그램 /web 으로 다시 접속하세요</p>'; return; }
    if(!r.ok){ clearInterval(timer); let d={};
      try{ d=await r.json(); }catch(_){}
      panel.innerHTML='<p class=hint>적재 요청 실패: '+esc(d.error||('HTTP '+r.status))+'</p>'; return; }
    if(!r.body) throw new Error('스트림 본문이 없습니다');
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
  if(d.directive) h+='<p class=al><b>초점:</b> '+esc(d.directive)+'</p>';
  if(!d.duplicate) h+='<p class=al>노드 신규 '+(d.entities_created||0)+' · 기존연결 '+
    (d.entities_linked||0)+' · 관계 '+(d.relations_added||0)+'</p>';
  if(d.summary) h+='<div class=synth>'+esc(d.summary)+'</div>';
  if(d.document_id) h+='<p><a href="#" onclick="selectDoc(\\''+d.document_id+'\\');return false">문서 보기 →</a></p>';
  panel.innerHTML=h;
  refreshGraph();   // 신규 노드/엣지·문서목록 즉시 반영(새로고침 없이)
}

// --- 중복 문서 정리: 근사중복 클러스터를 찾아(/dedup/scan) 유지문서를 골라 병합(/dedup/merge) ---
// 병합 직전 정본은 서버가 내부 checkpoint로 보존한다(파괴적 작업 안전장치).
// keeper(유지) 외 문서는 참조(엔티티/관계 sources 등)를 keeper 로 재배치한 뒤 삭제.
let dedupClusters=[];
async function openDedup(){
  if(!canWrite()) return;
  panel.innerHTML='<h2>♻️ 중복 문서 정리</h2><p class="al">근사 중복 검사 중… '+
    '<small>(문서가 많으면 잠시 걸립니다)</small></p>';
  openDetailPane();
  let d;
  try{
    const r=await fetch('dedup/scan',{method:'POST'});
    if(r.status===401||r.status===404){ expireWriteAccess();
      panel.innerHTML='<p class=hint>세션 만료 — 텔레그램 /web 으로 다시 접속하세요</p>'; return; }
    d=await r.json();
  }catch(e){ panel.innerHTML='<h2>♻️ 중복 문서 정리</h2><p class=hint>검사 실패: '+esc(String(e))+'</p>'; return; }
  renderDedup(d);
}
function renderDedup(d){
  if(!canWrite()) return;
  if(d.error){ panel.innerHTML='<h2>♻️ 중복 문서 정리</h2><p class=hint>오류: '+esc(d.error)+'</p>'; return; }
  dedupClusters=d.clusters||[];
  let h='<h2>♻️ 중복 문서 정리</h2>';
  h+='<p class=al>검사 '+(d.documents||0)+'개 · 근사중복 클러스터 <b>'+dedupClusters.length+'</b>개</p>';
  if(!dedupClusters.length){ h+='<p class=al>근사 중복 문서가 없습니다. ✅</p>'; panel.innerHTML=h; return; }
  h+='<p class=al><small>유지할 문서를 고르고 병합하세요. 나머지는 유지문서로 합쳐지고 참조는 보존됩니다(병합 전 내부 체크포인트 생성).</small></p>';
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
  if(!canWrite()) return;
  const c=dedupClusters[ci]; if(!c) return;
  const sel=document.querySelector('input[name="keep'+ci+'"]:checked');
  const keeper=sel?sel.value:c.keeper;
  const losers=c.docs.map(x=>x.id).filter(id=>id!==keeper);
  if(!losers.length){ alert('합칠 문서가 없습니다.'); return; }
  if(!confirm(losers.length+'개 문서를 유지문서로 합칩니다. 계속할까요?\\n(병합 전 내부 체크포인트를 생성합니다)')) return;
  panel.innerHTML='<h2>♻️ 병합 중…</h2><p class=al>체크포인트 생성 후 참조 재배치 중…</p>';
  try{
    const r=await fetch('dedup/merge',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({keeper:keeper, losers:losers})});
    if(r.status===401||r.status===404){ expireWriteAccess();
      panel.innerHTML='<p class=hint>세션 만료 — 텔레그램 /web 으로 다시 접속하세요</p>'; return; }
    const d=await r.json();
    if(!canWrite()) return;
    if(d.error){ panel.innerHTML='<h2>♻️ 병합</h2><p class=hint>오류: '+esc(d.error)+'</p>'; return; }
    let h='<h2>✅ 병합 완료</h2>';
    h+='<p class=al>문서 '+(d.deleted||0)+'개를 합쳤습니다. 엔티티 '+(d.entities_repointed||0)+
      ' · 관계 '+(d.relations_repointed||0)+' 참조 재배치.</p>';
    if(d.checkpoint) h+='<p class=al><small>내부 체크포인트: '+esc(d.checkpoint)+'</small></p>';
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
    d.nodes.forEach(n=>{
      const r = nodeRadius(n.degree), fs = nodeFontSize(n.degree);
      if(allNodes.get(n.id))
        changed.push({id:n.id, degree:n.degree, size:r, font:{size:fs}, sources:n.sources, obs:n.obs});
      else
        added.push({...n, size:r, font:{size:fs}});
    });
    if(changed.length) allNodes.update(changed);
    if(added.length) allNodes.add(added);
    d.edges.forEach(e=>{ if(!allEdges.get(e.id)) allEdges.add(e); });
    document.getElementById('fslider').max = d.stats.max_degree;
    updateDegPresets();
    applyView();
    if(activeDoc && curReaderDocData && curReaderDocData.id === activeDoc){
      renderDocPanel(curReaderDocData);
    }
  });
  fetch('documents').then(r=>r.json()).then(d=>{ allDocs=d.documents||[];
    renderDocs(document.getElementById('docq').value); });
}

// 전체 리로드 없이 새 글/엔티티/관계 반영 — 가벼운 주기 폴링. /stats 문서/엔티티/관계 개수 확인하고
// 바뀐 경우에만 refreshGraph()(append-only 병합) 실행 — 안 바뀌면 아무 요청도 안 함.
let lastStatsSig = null;
async function pollForUpdates(){
  try{
    const r = await fetch('stats');
    if(!r.ok) return;               // 401 등이면 조용히 다음 틱에 재시도
    const d = await r.json();
    const sig = [d.documents, d.entities, d.relations].join(':');
    if(lastStatsSig===null){ lastStatsSig = sig; return; }  // 최초 틱=기준값만 기록
    if(sig !== lastStatsSig){
      lastStatsSig = sig;
      refreshGraph();
      if(activeDoc){
        fetch('document?id='+encodeURIComponent(activeDoc)).then(x=>x.json()).then(dc=>{
          if(dc && !dc.error && activeDoc===dc.id){
            curReaderDocData=dc;
            renderDocPanel(dc);
          }
        }).catch(()=>{});
      }
    }
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
      return '<button type=button class="reltog'+(on?'':' off')+'" onclick="toggleRel('+i+
        ')" aria-pressed="'+(on?'true':'false')+'" title="이 관계만/제외 토글">'+esc(t)+'</button>';
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

function nodeRadius(deg){
  const d = deg || 0;
  if(d <= 1) return 10;
  if(d === 2) return 12;
  return Math.min(26, Math.round(11 + Math.sqrt(d) * 2.2));
}

function nodeFontSize(deg){
  const d = deg || 0;
  if(d <= 1) return 11;
  if(d === 2) return 12;
  return Math.min(15, Math.round(11 + Math.log2(d + 1)));
}

function getPhysicsOpts(nodeCount){
  const count = nodeCount || 0;
  let grav = -12000, cg = 0.12, spring = 150, overlap = 0.8;
  if(count >= 500){
    grav = -35000;
    cg = 0.04;
    spring = 220;
    overlap = 1.0;
  } else if(count >= 200){
    grav = -25000;
    cg = 0.06;
    spring = 190;
    overlap = 0.9;
  } else if(count >= 80){
    grav = -18000;
    cg = 0.09;
    spring = 170;
    overlap = 0.8;
  }
  return {
    solver: 'barnesHut',
    barnesHut: {
      gravitationalConstant: grav,
      centralGravity: cg,
      springLength: spring,
      springConstant: 0.03,
      damping: 0.4,
      avoidOverlap: overlap
    },
    minVelocity: 0.75,
    maxVelocity: 50,
    timestep: 0.5,
    stabilization: { iterations: 150 }
  };
}

function updateDegPresets(){
  const btns = document.querySelectorAll('.deg-preset-btn');
  btns.forEach(b => {
    const d = parseInt(b.getAttribute('data-deg'), 10);
    b.classList.toggle('active', curMinDeg === d);
  });
}

function setDeg(v){
  curMinDeg = +v;
  const sl = document.getElementById('fslider');
  if(sl && +sl.value !== curMinDeg) sl.value = curMinDeg;
  const fm = document.getElementById('fmin');
  if(fm) fm.textContent = v;
  updateDegPresets();
  applyView();
}
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
  const netBg=(typeof getComputedStyle==='function'?getComputedStyle(document.documentElement).getPropertyValue('--net-bg').trim():'')||'#ffffff';
  const pathActive = !!(pathNodes && pathNodes.size);
  const hasFilter = activeDoc || highlightSet || pathActive;
  const nodeUpdates=[], matchedNodes=new Set();
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
    if(match) matchedNodes.add(n.id);
    const r = nodeRadius(n.degree);
    const fs = nodeFontSize(n.degree);
    nodeUpdates.push({id:n.id, hidden:false, size:r,
      font:{size:fs, color:th.nodeFont},
      opacity: match?1:DIM, borderWidth: lit?3:1,
      color:{background:c, border: lit?th.lit:th.nodeBorder,
             highlight:{background:c, border:th.lit},
             hover:{background:c, border: lit?th.lit:th.nodeBorder}}});
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
    const incident = !!(selectedNodeId && (e.from===selectedNodeId || e.to===selectedNodeId));
    const selected = selectedEdgeIds.has(e.id);
    const contextEdge = !hasFilter || (matchedNodes.has(e.from) && matchedNodes.has(e.to));
    const muted = (selectedNodeId && !incident) || (hasFilter && !contextEdge);
    const labelOn = onPath || incident || selected || edgeLabelsByZoom;
    edgeUpdates.push({id:e.id, hidden: !visible, width:onPath?4:(incident||selected?2:1),
      color:onPath ? {color:th.lit,highlight:th.lit,opacity:1}
        : {color:th.edge,highlight:th.edgeHi,opacity:muted ? 0.08 : 1},
      font:{size:labelOn?10:0,color:th.nodeFont,strokeWidth:3,
        strokeColor:netBg}});
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
  else { pathNodes=null; pathEdges=null; setGraphNotice(''); applyView(); panel.innerHTML=''; }
}
function setGraphNotice(text){
  const el=document.getElementById('graphnotice');
  el.textContent=text||''; el.classList.toggle('on',!!text);
}
function showPathHint(){
  const n=pathPicks.length;
  panel.innerHTML='<h2>🔗 경로 찾기</h2><p class=al>'+
    (n===0?'시작 노드를 클릭하세요.':'끝 노드를 클릭하세요. <small>(시작: '+esc(nodeLabel(pathPicks[0]))+')</small>')+
    '</p><p class=al><small>관계 필터가 켜져 있으면 그 관계만 따라 경로를 찾습니다.</small></p>';
  setGraphNotice(n===0?'경로 시작 노드를 선택하세요':'경로 끝 노드를 선택하세요');
  setCenterView('graph');
  revealWorkspace('graph');
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
    setGraphNotice('');
    panel.innerHTML='<h2>🔗 경로</h2><p class=hint>두 노드 사이 연결 경로가 없습니다'+
      (relFilter?' (현재 관계 필터 기준)':'')+'.</p>'+
      '<p class=al><a href="#" onclick="restartPath();return false">다시 찾기</a></p>';
    openDetailPane();
    return; }
  pathNodes=new Set(); pathEdges=new Set();
  const order=[]; let cur=b;
  while(cur!==undefined){ order.push(cur); pathNodes.add(cur);
    if(prevE[cur]!==undefined) pathEdges.add(prevE[cur]);
    if(cur===a) break; cur=prev[cur]; }
  order.reverse();
  setGraphNotice('');
  applyView();
  if(net) net.fit({nodes:[...pathNodes], animation:graphAnimation(true)});
  panel.innerHTML='<h2>🔗 경로 <small>'+(order.length-1)+'단계</small></h2>'+
    '<p class=al>'+order.map(id=>esc(nodeLabel(id))).join(' → ')+'</p>'+
    '<p class=al><a href="#" onclick="restartPath();return false">다른 경로</a> · '+
    '<a href="#" onclick="clearPath();return false">해제</a></p>';
  openDetailPane();
}
function restartPath(){ pathMode=true; pathPicks=[];
  const b=document.getElementById('pathbtn'); if(b) b.classList.add('on'); showPathHint(); }
function clearPath(){ pathMode=false; pathPicks=[]; pathNodes=null; pathEdges=null;
  const b=document.getElementById('pathbtn'); if(b) b.classList.remove('on');
  setGraphNotice(''); applyView(); panel.innerHTML=''; revealWorkspace('graph'); }

// --- 좌측 문서 패널(일자별 그룹) ---
function dayOf(ts){ if(!ts) return '(날짜 미상)';
  const d=new Date(ts*1000);
  return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0'); }
// 문서 하나의 목록 아이템 HTML — 즐겨찾기/일반/숨김 목록이 모두 공유(중복 방지).
// 클릭=문서 열기, 제목 좌측의 별표(⭐/☆)는 stopPropagation으로 즐겨찾기 토글.
function docItemHtml(dc){
  const unread = dc.seen===0, watching = dc.watch===1, pinned = dc.pinned===1, hid = dc.hidden===1;
  const pinBtn = canWrite()
    ? '<button class="docpin-btn'+(pinned?' pinned':'')+'" title="'+(pinned?'즐겨찾기 해제':'즐겨찾기에 추가')+
      '" aria-label="'+(pinned?'즐겨찾기 해제':'즐겨찾기에 추가')+
      '" onclick="event.stopPropagation();togglePin(\\''+dc.id+'\\','+(!pinned)+')">'+(pinned?'⭐':'☆')+'</button>'
    : (pinned ? '<span class="docpin-icon pinned" title="즐겨찾기">⭐</span>' : '');
  return '<div class="docitem'+(dc.id===activeDoc?' active':'')+(unread?' unread':'')+(hid?' hidden-doc':'')+
    '" onclick="selectDoc(\\''+dc.id+'\\')">'+
    '<div class=doctitle-line>'+
    pinBtn+
    (watching?'<span class=wbadge title="주기 갱신 추적(watch)">🔄</span>':'')+
    (unread?'<span class=ubadge title="아직 안 본 문서">●</span>':'')+
    '<b>'+esc(dc.title)+'</b><span class=st>'+esc(dc.source_type||'')+'</span>'+
    '</div>'+
    (dc.summary?'<p>'+esc(dc.summary.slice(0,110))+'</p>':'')+'</div>';
}
let showHidden = false;
function toggleShowHidden(){ if(!canWrite()) return; showHidden=!showHidden; renderDocs(); }
function renderDocs(filter){
  const q = (filter !== undefined ? filter : (document.getElementById('docq') ? document.getElementById('docq').value : '')).trim().toLowerCase();
  if(docSearchActive && q){
    cancelServerSearch();
    currentSearchSeq++;
  }
  if(docSearchActive && !q){
    const ph = document.getElementById('pinnedhead'); if(ph) ph.style.display = 'none';
    const pl = document.getElementById('pinnedlist'); if(pl) pl.innerHTML = '';
    const dl = document.getElementById('doclist'); if(dl) dl.innerHTML = doclistToolbarHtml() + '<p class="hint" style="padding:16px 12px;text-align:center">🔎 검색어를 입력하세요.</p>';
    const sh = document.getElementById('showhidden'); if(sh) sh.style.display = 'none';
    const hl = document.getElementById('hiddenlist'); if(hl) hl.innerHTML = '';
    syncGraphDocNav();
    return;
  }
  const match = dc => !q || (dc.title+' '+(dc.summary||'')).toLowerCase().includes(q);
  // 숨김(hidden)은 기본 목록·즐겨찾기 양쪽에서 제외(목록 전용 숨김, 그래프는 안 건드림).
  const visible = allDocs.filter(dc=> dc.hidden!==1 && match(dc));
  const pinned = visible.filter(dc=>dc.pinned===1);
  const rest = visible.filter(dc=>dc.pinned!==1);
  const hiddenDocs = allDocs.filter(dc=> dc.hidden===1 && match(dc));

  const ph = document.getElementById('pinnedhead');
  if(ph){
    ph.style.display = pinned.length ? '' : 'none';
    ph.textContent = '⭐ 즐겨찾기 (' + pinned.length + ')';
  }
  const pl = document.getElementById('pinnedlist'); if(pl) pl.innerHTML = pinned.map(docItemHtml).join('');

  const dl = document.getElementById('doclist');
  if(dl){
    dl.innerHTML = doclistToolbarHtml() + (rest.length
      ? (()=>{ let html='', curDay=null;
          rest.forEach(dc=>{ const day=dayOf(dc.fetched_at);
            if(day!==curDay){ html+='<div class=dday>'+day+'</div>'; curDay=day; }
            html+=docItemHtml(dc); });
          return html; })()
      : '<p class=hint style="padding:10px">문서 없음</p>');
  }

  const sh=document.getElementById('showhidden');
  const hl=document.getElementById('hiddenlist');
  // readonly 는 숨기기/해제 버튼이 없어 이 구간이 눌러도 소용없는 관리용 UI — 아예 숨김.
  if(!canWrite() || !hiddenDocs.length){
    if(sh) sh.style.display='none';
    if(hl) hl.innerHTML='';
  }else{
    if(sh){
      sh.style.display='';
      sh.textContent = (showHidden?'▲ ':'▼ ')+'🙈 숨김 '+hiddenDocs.length+'개 '+(showHidden?'접기':'보기');
    }
    if(hl) hl.innerHTML = showHidden ? hiddenDocs.map(docItemHtml).join('') : '';
  }
  const st = document.getElementById('stat');
  if(st){
    if(q){
      st.textContent = visible.length + '개 발견';
    } else {
      st.innerHTML = (allDocs ? allDocs.length : 0) + '개 문서' +
        (allNodes && allNodes.length ? ' (' + allNodes.length + ' 엔티티)' : '');
    }
  }
  syncGraphDocNav();
}
// 즐겨찾기/숨기기 토글 — 낙관적 갱신(즉시 반영) 후 서버 반영, 실패하면 되돌림.
async function togglePin(id, val){
  if(!canWrite()) return false;
  const d=allDocs.find(x=>x.id===id); if(d) d.pinned = val?1:0;
  renderDocs(document.getElementById('docq').value);
  try{
    const r=await fetch('document/pin',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({id:id, pinned:val})});
    if(r.status===401||r.status===404) expireWriteAccess();
    if(!r.ok && d){ d.pinned = val?0:1; renderDocs(document.getElementById('docq').value); }
  }catch(e){ if(d){ d.pinned = val?0:1; renderDocs(document.getElementById('docq').value); } }
  return true;
}
async function toggleHide(id, val){
  if(!canWrite()) return false;
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
    if(r.status===401||r.status===404) expireWriteAccess();
    if(!r.ok && d){ d.hidden = val?0:1; renderDocs(document.getElementById('docq').value); }
  }catch(e){ if(d){ d.hidden = val?0:1; renderDocs(document.getElementById('docq').value); } }
  return true;
}
// 상세 패널의 숨기기 체크박스 — toggleHide 결과(컨펌 취소 여부)를 보고 체크박스 및 라벨 갱신.
async function panelToggleHide(id, val){
  if(!canWrite()) return;
  const ok = await toggleHide(id, val);
  const chk = document.getElementById('panelhidechk');
  const lbl = document.getElementById('panelhidelabel');
  if(!ok){
    if(chk) chk.checked = !val;
    return;
  }
  if(chk) chk.checked = !!val;
  if(lbl) lbl.textContent = val ? '🙈 숨김 처리됨' : '목록에서 숨기기';
}
function resetHome(){
  cancelServerSearch();
  currentSearchSeq++;
  hideNodePop();
  closeDrawer();
  toggleAdvSearch(false);
  docSearchActive = false;
  const dq = document.getElementById('docq');
  if(dq && dq.value){
    dq.value = '';
  }
  renderDocs('');
  const q = document.getElementById('q');
  if(q && q.value){
    q.value = '';
  }
  const sem = document.getElementById('sem');
  if(sem && sem.checked){
    sem.checked = false;
  }
  const semchk = document.getElementById('semchk');
  if(semchk && semchk.checked){
    semchk.checked = false;
  }
  updateSearchModeUI();
  clearTimeout(searchDebounce);
  highlightSet = null;
  unclusterEdges();
  if(pathMode) clearPath();
  if(net){
    try{ net.unselectAll(); }catch(_){}
  }
  resetGraphCamera();
  panel.innerHTML = defaultHint();
  activeDoc = null;
  selectedNodeId = null;
  curReaderDoc = null;
  setCenterView('graph');
  revealWorkspace('graph', false, true);
  renderDocs();
  applyView();
  resetGraphCamera();
  syncGraphDocNav();
  document.title = 'Claire Bible — 지식 그래프';
  if(mobileMQ.matches){
    closeReader(false, false);
  }
  if(typeof window.gtag === 'function'){
    try{
      window.gtag('event', 'page_view', {
        page_title: document.title,
        page_location: window.location.origin + '/'
      });
    }catch(_){}
  }
}

function selectDoc(id){
  recordSelectedDoc(id);
  openReader(id);
}
function setActiveDoc(id){
  activeDoc = id;
  if(id){
    curReaderDoc = id;
    recordSelectedDoc(id);
  }
  selectedNodeId=null;                          // 문서 모드로 전환 — 노드 inspect 해제
  renderDocs(document.getElementById('docq').value);
  applyView();
  if(activeDoc){
    const docId=activeDoc;
    requestAnimationFrame(()=>{ if(net && activeDoc===docId){
      if(centerView==='graph') relayout(true);
      // 전체 fit 이 아니라 그 문서의 노드들만 화면에 차게 — 최적 줌/위치로 이동(피드백).
      const ids=[]; allNodes.forEach(n=>{ if(!n.hidden && (n.sources||[]).includes(docId)) ids.push(n.id); });
      if(ids.length) net.selectNodes(ids);
      if(!ids.length) resetGraphCamera(); else cameraToNodes(ids);
    }});
    loadDocPanel(activeDoc);    // wide rail/후속 문맥 작업을 위해 내용만 준비
  } else {
    if(net) net.unselectAll();
    resetGraphCamera();
    panel.innerHTML = defaultHint();             // 해제 시 기본 힌트로 복원
  }
}

// 좌측 문서 선택 시 우측 패널: 문서 요약 + 상세 + '이 문서의 노드' 버튼.
function loadDocPanel(id){
  if(curReaderDocData && curReaderDocData.id===id){
    renderDocPanel(curReaderDocData);
    markDocumentSeen(id);
    return;
  }
  panel.innerHTML='<p class=hint>문서 불러오는 중…</p>';
  fetch('document?id='+encodeURIComponent(id)).then(r=>r.json()).then(dc=>{
    if(activeDoc!==id) return;                  // 그 사이 다른 문서/노드로 이동했으면 무시
    if(!dc || dc.error){ panel.innerHTML='<p class=hint>문서를 찾을 수 없습니다.</p>'; return; }
    curReaderDocData=dc;
    renderDocPanel(dc);
    markDocumentSeen(id);
  }).catch(()=>{ panel.innerHTML='<p class=hint>문서 로드 실패.</p>'; });
}
// 한 문서(article)에 속한 노드 — dc.nodes(서버 실시간 DB 조회) 우선, allNodes fallback.
function docNodes(docId, dc){
  if(dc && Array.isArray(dc.nodes) && dc.nodes.length > 0){
    const out = dc.nodes.map(n => {
      const existing = (allNodes && typeof allNodes.get === 'function') ? allNodes.get(n.id) : null;
      return {
        id: n.id,
        label: n.label || n.name || n.id,
        group: n.group || n.type || 'Concept',
        degree: existing ? (existing.degree || 0) : 0,
        sources: existing ? (existing.sources || [docId]) : [docId],
        obs: n.observations || (existing ? existing.obs : []),
      };
    });
    out.sort((a,b)=> (b.degree||0)-(a.degree||0));
    return out;
  }
  const out=[]; if(!allNodes) return out;
  allNodes.forEach(n=>{ if((n.sources||[]).includes(docId)) out.push(n); });
  out.sort((a,b)=> (b.degree||0)-(a.degree||0));   // 중심성 높은 노드부터(핵심이 위로)
  return out;
}
function renderDocPanel(dc){
  curReaderDocData=dc;
  let h='<h2>'+esc(dc.title)+' <small>'+esc(dc.source_type||'')+'</small></h2>';
  h+=docMetaHtml(dc);
  h+=extraSourcesHtml(dc);
  const directive = (dc.directive || (dc.meta && dc.meta.directive) || '').trim();
  if(directive){
    h+='<div style="margin:.4em 0 .6em;padding:6px 8px;background:var(--card-bg);border:1px solid var(--border);border-radius:5px;font-size:12px"><b style="color:var(--accent2)">🎯 초점:</b> '+esc(directive)+'</div>';
  }
  // 숨기기 — 상세 패널의 FTS 스타일 체크박스로(사용자 요구).
  if(canWrite()){
    h+='<div class=dochide-row><label class=dochide-label>'+
      '<input type="checkbox" id="panelhidechk" '+(dc.hidden===1?'checked':'')+
      ' onchange="panelToggleHide(\\''+dc.id+'\\',this.checked)">'+
      '<span id="panelhidelabel">'+(dc.hidden===1?'🙈 숨김 처리됨':'목록에서 숨기기')+'</span>'+
      '</label></div>';
  }
  if(dc.summary) h+='<h3>요약</h3><div class=synth>'+esc(dc.summary)+'</div>';
  // 이 문서의 노드 버튼 — 요약 바로 아래(피드백). 누르면 그래프에서 그 노드로 이동(nav).
  const ns=docNodes(dc.id, dc);
  h+='<h3>이 문서의 지식 노드 ('+ns.length+')</h3>';
  if(ns.length){ h+='<div class=nodebtns>'+ ns.map(n=>{
      const c=TYPE_COLORS[n.group]||'#8b949e';
      return '<button class=nodebtn title="'+esc(n.group||'')+'" onmouseenter="peekNode(event,\\''+n.id+'\\')" '+
        'onmouseleave="leaveNode()" onclick="focusNode(\\''+n.id+'\\')">'+
        '<i style="background:'+c+'"></i>'+esc(n.label)+'</button>'; }).join('')+'</div>';
  } else { h+='<p class=al>이 문서에서 추출된 노드가 없습니다.</p>'; }
  if(!dc.summary && !dc.detail) h+='<p class=al>문서에 요약/상세 내용이 없습니다.</p>';
  panel.innerHTML=h;

  if(dc && Array.isArray(dc.nodes) && dc.nodes.length > 0){
    const missing = allNodes && typeof allNodes.get === 'function' && dc.nodes.some(n => !allNodes.get(n.id));
    if(missing && typeof refreshGraph === 'function'){
      refreshGraph();
    }
  }
}
// 노드 버튼 클릭 → 그래프에서 그 노드로 카메라 이동 + 선택 + 우측은 노드 상세로 전환.
// activeDoc 은 유지 → 노드 상세 상단의 '← 문서로' 로 문서 패널에 즉시 복귀 가능.
function focusNode(id, pushHist=true){
  selectedNodeId=id;
  loadNode(id);
  setCenterView('graph');
  revealWorkspace('graph', false, pushHist);
  if(pushHist) pushAppHistory({ pane: 'graph', nodeId: id, modal: (compactMQ.matches || mobileMQ.matches) ? 'drawer' : null });
  if(net && !isDraggingNode){
    clearTimeout(settleTimer);
    net.setOptions({physics:false});
  }
  requestAnimationFrame(()=>{
    if(net){
      relayout(true);
      net.selectNodes([id]);
      net.focus(id,{scale:1.3,animation:graphAnimation(true)});
    }
  });
}

// --- 종합 수집(synthSet) — inspect(클릭)와 분리 ---
function toggleSynth(id){ if(!canWrite()) return; if(synthSet.has(id)) synthSet.delete(id); else synthSet.add(id); renderChips(); }
function addToSynth(id){ if(!canWrite()) return; synthSet.add(id); renderChips(); if(id===selectedNodeId) loadNode(id); }
function renderChips(){
  const box=document.getElementById('synthchips');
  box.innerHTML=[...synthSet].map(id=>{ const n=allNodes&&allNodes.get(id);
    return '<span class=chip onclick="toggleSynth(\\''+id+'\\')" title="제거">'+esc(n?n.label:id)+' ✕</span>'; }).join('');
  const sb=document.getElementById('synthbtn');
  if(sb){
    sb.innerHTML='<span class="btn-icon">🧩</span> <span class="btn-label">종합 ('+synthSet.size+')</span>';
    sb.title='종합 ('+synthSet.size+')';
  }
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
  if(vis.length===1) net.focus(vis[0],{scale:1.1,animation:graphAnimation(true)});
  else net.fit({nodes:vis, animation:graphAnimation(true)});
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
  net.setOptions({physics:true});
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
  if(net && !isDraggingNode) net.setOptions({physics:false});
}
// 우측 '이 문서의 노드' 버튼 hover — 요약 팝업(1.5초 머물 시). 클릭 시 focusNode 로 카메라 이동.
function peekNode(ev, id){
  clearTimeout(hoverTimer);
  if(!canShowNodePop(id)) return;
  const x=ev.clientX, y=ev.clientY;
  hoverTimer=setTimeout(()=>showNodePop(id, x, y), 1500);
}
function leaveNode(){ clearTimeout(hoverTimer); hideNodePop(); }
// 타이핑마다 즉시 검색하면 매 키 입력에 강조+물리 클러스터링이 돌아 무겁고 출렁인다.
// 디바운스: 입력이 멈춘 뒤(350ms) 한 번만 실행. 단 검색창을 비우면 즉시 해제(반응성).
function onSearchInput(v){
  const sem=document.getElementById('sem');
  const semchk=document.getElementById('semchk');
  if((sem && sem.checked) || (semchk && semchk.checked)) return;   // 고급검색(FTS/Semantic)은 엔터로만
  cancelServerSearch();
  currentSearchSeq++;
  clearTimeout(searchDebounce);
  if(!v.trim()){ hl(''); return; }                     // 비우기 → 즉시 강조/클러스터 해제
  searchDebounce=setTimeout(()=>hl(v), 550);
}
// 라벨 검색: 매치 강조 + 나머지 dim(문서 선택과 동일 방식). 색칠 대신 highlightSet+applyView.
function hl(q){
  if(!allNodes) return;
  cancelServerSearch();
  currentSearchSeq++;
  q=q.trim().toLowerCase();
  if(!q){ highlightSet=null; applyView(); unclusterEdges();
    if(net){ net.unselectAll(); net.fit({animation:graphAnimation(true)}); } return; }
  if(typeof window.gtag === 'function'){
    try{ window.gtag('event', 'search', { search_term: q, search_mode: 'label' }); }catch(_){}
  }
  const matches=[];
  allNodes.forEach(n=>{ if(n.label.toLowerCase().includes(q)) matches.push(n.id); });
  highlightSet = new Set(matches);
  applyView();
  clusterMatches(matches, ()=>fitToMatches(matches));   // 결과를 점차 뭉치게 한 뒤 한눈에 fit
}
function toggleAdvSearch(force){
  const pane = document.getElementById('advsearchpane');
  const btn = document.getElementById('advsearchbtn');
  if(!pane || !btn) return;
  const isHidden = force !== undefined ? !force : !pane.hidden;
  pane.hidden = isHidden;
  pane.setAttribute('aria-hidden', String(isHidden));
  btn.setAttribute('aria-expanded', String(!isHidden));
  btn.classList.toggle('active', !isHidden);
}
const semEl=document.getElementById('sem');
const semchkEl=document.getElementById('semchk');
if(semEl){
  semEl.addEventListener('change',e=>{
    if(e.target.checked && semchkEl) semchkEl.checked = false;
    cancelServerSearch();
    currentSearchSeq++;
    if(e.target.checked) hl('');
  });
}
if(semchkEl){
  semchkEl.addEventListener('change',e=>{
    if(e.target.checked && semEl) semEl.checked = false;
    cancelServerSearch();
    currentSearchSeq++;
    if(e.target.checked) hl('');
  });
}
const docqEl=document.getElementById('docq');
if(docqEl){
  docqEl.addEventListener('keydown',e=>{
    if(e.key!=='Enter') return;
    const sem=document.getElementById('sem');
    const semchk=document.getElementById('semchk');
    if((sem && sem.checked) || (semchk && semchk.checked)){ doSemantic(); }
  });
}
const qEl=document.getElementById('q');
if(qEl){
  qEl.addEventListener('keydown',e=>{
    if(e.key!=='Enter') return;
    const sem=document.getElementById('sem');
    const semchk=document.getElementById('semchk');
    if((sem && sem.checked) || (semchk && semchk.checked)){ doSemantic(); }
    else { cancelServerSearch(); currentSearchSeq++; clearTimeout(searchDebounce); hl(e.target.value);
           revealWorkspace('graph');
           if(net){ const m=net.getSelectedNodes(); if(m.length) loadNode(m[0]); } }
  });
  qEl.addEventListener('focus', e=> e.target.select());
}
function doSemantic(){
  revealWorkspace('graph');
  const docqVal = (document.getElementById('docq') ? document.getElementById('docq').value : '').trim();
  const qVal = (document.getElementById('q') ? document.getElementById('q').value : '').trim();
  const qv = docqVal || qVal;
  semanticSearch(qv);
}

// --- 인증 상태 표시 ---
// 첫 페인트는 unknown/read-only이며, /whoami가 exact owner를 확인한 경우에만 쓰기 UI를
// 승격한다. 버튼 숨김과 별개로 모든 쓰기 함수도 canWrite()를 확인한다.
function updateSearchModeUI(){
  const sem=document.getElementById('sem');
  const kind=document.getElementById('searchkind');
  const semchk=document.getElementById('semchk');
  const sembadge=document.getElementById('sembadge');
  const semwrap=document.getElementById('semantic-opt-wrap');
  const unknown=AUTH_SCOPE==='unknown';
  const isAnon=AUTH_SCOPE==='anonymous';

  if(sem){
    sem.disabled=unknown;
    if(unknown) sem.checked=false;
  }
  if(kind) kind.textContent = 'Full-Text Search';

  if(semchk){
    semchk.disabled = unknown || isAnon;
    if(unknown || isAnon) semchk.checked = false;
  }
  if(sembadge){
    sembadge.style.display = isAnon ? '' : 'none';
  }
  const ftswrap=document.getElementById('fts-opt-wrap');
  if(ftswrap){
    ftswrap.title = 'SQLite FTS5 기반 BM25';
  }
  if(semwrap){
    semwrap.style.opacity = isAnon ? '0.65' : '1';
    semwrap.title = isAnon
      ? 'FTS + AI RRF 기반 벡터 하이브리드 (인증 필요)'
      : 'FTS + AI RRF 기반 벡터 하이브리드';
  }
}
function setAccessScope(scope, reason){
  AUTH_SCOPE = ['owner','readonly','anonymous'].includes(scope) ? scope : 'unknown';
  READONLY = !canWrite();
  document.body.dataset.authScope=AUTH_SCOPE;
  document.body.classList.toggle('ro', READONLY);
  const label=document.getElementById('authstate');
  if(label) label.textContent =
    AUTH_SCOPE==='owner' ? '🔒 인증됨' :
    AUTH_SCOPE==='readonly' ? '👁️ 읽기전용' :
    AUTH_SCOPE==='anonymous' ? '👁️ 익명 읽기전용' :
    reason==='expired' ? '🔓 쓰기 세션 만료 — /web 재접속' : '⚠️ 권한 확인 실패';
  if(!canWrite()){
    synthSet.clear();
    showHidden=false;
    renderChips();
    // owner 전용 동적 UI를 네트워크 재조회보다 먼저 제거한다. 뒤늦게 도착하는
    // node/document 응답도 render 시점의 canWrite()로 다시 판정한다.
    panel.innerHTML=defaultHint();
  }
  updateSearchModeUI();
  renderDocs();
  if(selectedNodeId) loadNode(selectedNodeId);
  else if(activeDoc) loadDocPanel(activeDoc);
  else panel.innerHTML=defaultHint();
}
function expireWriteAccess(){ setAccessScope('unknown','expired'); }
async function synth(){
  if(!canWrite()) return;
  const ids=[...synthSet];
  if(!ids.length){ alert('종합할 노드를 먼저 모으세요 — Ctrl+클릭 또는 상세의 "➕ 종합에 추가".'); return; }
  panel.innerHTML='<p class=hint>🧩 '+ids.length+'개 노드 종합 중… (LLM 호출)</p>';
  openDetailPane();
  // 인증은 claire_session 쿠키(/web 진입)로 자동 전송됨 — 별도 헤더 불필요.
  fetch('synthesize',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({node_ids:ids})})
   .then(r=> { if(r.status===401||r.status===404){ expireWriteAccess(); return {error:'세션 만료 — 텔레그램 /web 으로 다시 접속하세요'}; } return r.json(); })
   .then(d=>{
     if(d.error){ panel.innerHTML='<p class=hint>오류: '+esc(d.error)+'</p>'; return; }
     let h='<h2>🧩 종합 지식 <small>'+d.entities.length+'개 노드</small></h2>';
     h+='<div class=synth>'+esc(d.answer)+'</div>';
     h+='<p class=al>대상: '+d.entities.map(esc).join(', ')+'</p>';
     panel.innerHTML=h;
   }).catch(e=>{ panel.innerHTML='<p class=hint>요청 실패: '+esc(String(e))+'</p>'; });
}
async function semanticSearch(q){
  q=(q||'').trim(); if(!q) return;
  cancelServerSearch();
  const seq = ++currentSearchSeq;
  let abortController = null;
  if(typeof AbortController !== 'undefined'){
    abortController = new AbortController();
    currentSearchAbort = abortController;
  }
  const semchk=document.getElementById('semchk');
  const isSemantic = semchk && semchk.checked && AUTH_SCOPE !== 'anonymous';
  const searchMode = isSemantic ? 'hybrid' : 'fts';
  if(typeof window.gtag === 'function'){
    try{ window.gtag('event', 'search', { search_term: q, search_mode: searchMode }); }catch(_){}
  }
  const requestedMode = isSemantic ? 'Semantic Search' : 'Full-Text Search';
  const statEl = document.getElementById('stat');
  if(statEl) statEl.textContent='🔎 '+requestedMode+' 중…';
  let r;
  try{
    const reqOpts = {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({query:q, summarize:false, limit:12, mode:searchMode})
    };
    if(abortController) reqOpts.signal = abortController.signal;
    r=await fetch('search', reqOpts);
  }
  catch(e){
    if(abortController && abortController.signal.aborted) return;
    if(seq !== currentSearchSeq) return;
    if(statEl) statEl.textContent='검색 실패';
    return;
  }
  if(seq !== currentSearchSeq) return;
  if(r.status===401||r.status===404){ expireWriteAccess(); if(statEl) statEl.textContent='세션 만료 — /web 으로 재접속'; return; }
  if(r.status===429){ if(statEl) statEl.textContent='검색 요청이 많습니다 — 잠시 후 다시 시도하세요'; return; }
  let d={}; try{ d=await r.json(); }catch(_){}
  if(seq !== currentSearchSeq) return;
  if(!r.ok){ if(statEl) statEl.textContent='검색 실패: HTTP '+r.status; return; }
  const ids=(d.hits||[]).map(h=>h.id).filter(Boolean);
  highlightSet = new Set(ids);   // 라벨 검색과 동일하게 강조+dim 방식 사용
  applyView();
  clusterMatches(ids, ()=>fitToMatches(ids));   // 의미검색 결과도 점차 뭉치게 + 한눈에 fit
  const actualMode=(d.mode==='fts'||searchMode==='fts')?'Full-Text Search':'Semantic Search';
  if(statEl){
    statEl.textContent=ids.length
      ? '🔎 '+actualMode+': '+ids.length+'개'
      : '🔎 '+actualMode+': 결과 없음';
  }
}

// --- 고유 상태 및 안내 배너 시스템 (ClaireStatusBanner) ---
const ClaireStatusBanner = (function(){
  const presets = {
    format_mismatch: {
      level: 'warning',
      icon: '⚠️',
      title: '렌더링 포맷 불일치',
      render: function(data){
        const cfg = (data.configured || 'adoc').toUpperCase();
        const other = (data.configured === 'adoc' ? 'md' : 'adoc').toUpperCase();
        const count = data.mismatched || data.mismatched_docs || 0;
        return '.env 설정은 <b>' + cfg + '</b>이나, DB 문서 중 <b>' + count + '개</b>가 <b>' + other + '</b> 포맷입니다. <code>./cb-manuscript app format-migrate --apply</code> 실행이 필요합니다.';
      }
    },
    format_missing: {
      level: 'info',
      icon: 'ℹ️',
      title: '가독 렌더링 미생성',
      render: function(data){
        const count = data.missing_detail_docs || 0;
        return '전체 문서 중 <b>' + count + '개</b>의 상세(detail)가 아직 생성되지 않았습니다. <code>./cb-manuscript app format-migrate --apply</code> 실행으로 자동 생성할 수 있습니다.';
      }
    },
    format_ok: {
      level: 'success',
      icon: '✅',
      title: '포맷 동기화 완료',
      render: function(data){
        const cfg = (data.configured || 'adoc').toUpperCase();
        const total = data.total_docs || data.matching_docs || 0;
        return '모든 문서(' + total + '건)가 목표 포맷(<b>' + cfg + '</b>)으로 정상 렌더링되고 있습니다.';
      }
    },
    readonly_mode: {
      level: 'info',
      icon: '🔒',
      title: '읽기 전용 모드',
      render: function(){
        return '현재 게스트(읽기 전용) 권한으로 접속 중입니다. 지식그래프 탐색 및 검색이 가능합니다.';
      }
    },
    network_error: {
      level: 'error',
      icon: '⚡',
      title: '연결 확인 필요',
      render: function(data){
        return (data && data.message) || '서버와의 통신이 원활하지 않습니다. 네트워크 연결을 확인해 주세요.';
      },
      actionLabel: '🔄 새로고침',
      action: function(){
        if (typeof window !== 'undefined' && window.location && window.location.reload) {
          window.location.reload();
        }
      }
    },
    refresh_pending: {
      level: 'warning',
      icon: '⏳',
      title: '데이터 갱신 대기',
      render: function(data){
        const count = (data && data.count) || '일부';
        return '문서 갱신 작업(' + count + '건)이 대기열에 등록되어 백그라운드 처리 중입니다.';
      }
    },
    custom_notice: {
      level: 'info',
      icon: '📢',
      title: '안내',
      render: function(data){
        return (data && data.message) || '';
      }
    }
  };

  let activeStatus = null;
  let activeData = null;

  return {
    presets: presets,
    register: function(key, def){
      presets[key] = def;
    },
    show: function(presetKey, data, options){
      const banner = document.getElementById('format-warn-banner');
      const text = document.getElementById('format-warn-text');
      const badge = document.getElementById('format-warn-badge');
      const iconEl = document.getElementById('format-warn-icon');
      const titleEl = document.getElementById('format-warn-title');
      const actBtn = document.getElementById('format-warn-actbtn');
      if(!banner || !text) return;

      const preset = presets[presetKey] || presets.custom_notice;
      const opts = Object.assign({}, preset, options || {});
      const pData = data || {};
      activeStatus = presetKey;
      activeData = pData;

      banner.className = 'status-banner banner-' + (opts.level || 'warning');
      if (iconEl) iconEl.textContent = opts.icon || 'ℹ️';
      if (titleEl) titleEl.textContent = opts.title || '안내';
      if (badge && opts.title === '') badge.style.display = 'none';
      else if (badge) badge.style.display = 'inline-flex';

      const html = typeof opts.render === 'function' ? opts.render(pData) : (opts.message || '');
      text.innerHTML = html;

      if (actBtn) {
        if (opts.actionLabel && typeof opts.action === 'function') {
          actBtn.textContent = opts.actionLabel;
          actBtn.style.display = 'inline-block';
        } else {
          actBtn.style.display = 'none';
        }
      }

      banner.style.display = 'flex';
    },
    hide: function(){
      const banner = document.getElementById('format-warn-banner');
      if (banner) banner.style.display = 'none';
      activeStatus = null;
      activeData = null;
    },
    handleAction: function(){
      if (!activeStatus) return;
      const preset = presets[activeStatus];
      const actBtn = document.getElementById('format-warn-actbtn');
      if (preset && typeof preset.action === 'function') {
        preset.action(activeData, actBtn);
      }
    },
    getStatus: function(){
      return { status: activeStatus, data: activeData };
    }
  };
})();

// documents와 /whoami를 병렬로 읽되, scope가 확정되기 전 렌더는 항상 read-only다.
syncThemeBtn();   // 저장된 테마에 맞춰 🌙/🌞 라벨 동기화(테마 자체는 head 인라인에서 선적용)
fetch('documents').then(r=>{ if(!r.ok) throw new Error('documents fetch failed: HTTP '+r.status); return r.json(); }).then(d=>{
  allDocs=(d && d.documents)||[];
  renderDocs();
  if(d && d.format_status){
    const fs=d.format_status;
    if(fs.needs_migration){
      if((fs.mismatched || fs.mismatched_docs || 0) > 0){
        ClaireStatusBanner.show('format_mismatch', fs);
      } else if((fs.missing_detail_docs || 0) > 0){
        ClaireStatusBanner.show('format_missing', fs);
      }
    }
  }
}).catch(e=>{
  allDocs=[];
  const dl=document.getElementById('doclist');
  if(dl) dl.innerHTML=doclistToolbarHtml()+'<p class="hint" style="padding:10px">문서 로드 실패</p>';
});
fetch('whoami').then(r=>{ if(!r.ok) throw new Error('whoami failed'); return r.json(); }).then(d=>{
  setAccessScope(d.scope);
}).catch(()=>{ setAccessScope('unknown','failed'); });

// --- 브라우저 히스토리 (뒤로가기 / 앞으로가기) 내비게이션 핸들러 ---
window.addEventListener('popstate', e => {
  isPoppingHistory = true;
  try{
    const state = e.state || { pane: 'graph', modal: null, docId: null, nodeId: null };
    lastPushedHistory = state;
    const r = document.getElementById('reader');
    const gdm = document.getElementById('graphdocmenu');

    // 1. 모달 닫기
    if(state.modal !== 'reader' && r && r.classList.contains('open')){
      closeReader(false, false);
    }
    if(state.modal !== 'drawer' && (drawerOpen || detailOpen)){
      closeDrawer(false, false);
    }
    if(state.modal !== 'graphdocmenu' && gdm && !gdm.hidden){
      closeGraphDocPicker(true, false);
    }

    // 2. 작업 영역(pane) 전환
    if(state.pane && state.pane !== activePane){
      revealWorkspace(state.pane, false, false);
    }

    // 3. 모달 열기
    if(state.modal === 'reader' && state.docId){
      if(!r || !r.classList.contains('open') || curReaderDoc !== state.docId){
        openReader(state.docId, false);
      }
    } else if(state.modal === 'drawer'){
      if(!drawerOpen && !detailOpen){
        openDrawer(false);
      }
    } else if(state.modal === 'graphdocmenu'){
      if(gdm && gdm.hidden){
        openGraphDocPicker(false);
      }
    }

    // 4. 노드 포커스
    if(state.pane === 'graph' && state.nodeId && state.nodeId !== selectedNodeId){
      focusNode(state.nodeId, false);
    }
  }finally{
    isPoppingHistory = false;
  }
});

// 초기 베이스 히스토리 엔트리 등록
replaceAppHistory({ pane: activePane || 'graph', modal: getActiveModalName() });

// 읽기전용 디버그 핸들(테스트/Playwright 검증용 — closure 상태 관찰). 부작용 없음.
window.claireDebug = {
  get sel(){ return net ? net.getSelectedNodes() : []; },
  get highlight(){ return highlightSet ? [...highlightSet] : null; },
  get selected(){ return selectedNodeId; },
  get activeDoc(){ return activeDoc; },
  get synth(){ return [...synthSet]; },
  get authScope(){ return AUTH_SCOPE; },
  get canWrite(){ return canWrite(); },
  get statusBanner(){ return ClaireStatusBanner; },
  get activeBannerStatus(){ return ClaireStatusBanner.getStatus(); },
  positions(ids){ return net ? net.getPositions(ids) : {}; },
  visibleNodePoints(){
    if(!net || !allNodes) return [];
    return allNodes.getIds().filter(id=>{
      const n=allNodes.get(id);
      return n && !n.hidden && !(typeof id==='string' && id.indexOf('cl_')===0);
    }).map(id=>({id:id,...net.canvasToDOM(net.getPosition(id))}));
  },
  get scale(){ return net ? net.getScale() : null; },
  get viewpos(){ return net ? net.getViewPosition() : null; },
  get clustered(){ return clusterEdges; },
  get activePane(){ return activePane; },
  get detailOpen(){ return detailOpen || drawerOpen; },
  get drawerOpen(){ return drawerOpen; },
  get toolsOpen(){ return drawerOpen || detailOpen; },
  get readerOpen(){ return document.getElementById('reader').classList.contains('open'); },
  get docSearchActive(){ return docSearchActive; },
  get stabilized(){ return graphStabilized; },
  get detailCompact(){ return document.body.classList.contains('detail-compact'); },
  toggleDetailCompact: toggleDetailCompact,
  docWithMostNodes: docWithMostNodes,
  getRecentDocId: getRecentDocId,
  get lastSelectedDocId(){ return lastSelectedDocId; },
  get sourceBaseUrl(){ return '__SOURCE_BASE_URL__'; },
  get githubRepository(){ return '__GITHUB_REPOSITORY__'; },
};
</script></body></html>
"""


# --- 공유 핫링크용 경량 읽기 페이지(/p?s=token) — 인증/그래프 없이 문서 1개만 보여준다. ---
# 데이터는 <script type=application/json> 에 임베드(라운드트립 1회)하고 클라가 마크다운 렌더.
# GRAPH_HTML 과 독립(공유 토큰은 세션과 분리되어야 하므로 UI/JS 도 섞지 않는다).
_SHARED_HTML = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>__TITLE__ — Claire Bible</title>
<meta name="mobile-web-app-capable" content="yes"/>
<meta name="apple-mobile-web-app-capable" content="yes"/>
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"/>
<meta name="apple-mobile-web-app-title" content="Claire Bible"/>
<meta name="application-name" content="Claire Bible"/>
<link rel="icon" type="image/svg+xml" href="/favicon.svg"/>
<link rel="icon" type="image/png" sizes="192x192" href="/icon?p=android-chrome-192x192.png"/>
<link rel="icon" type="image/png" sizes="512x512" href="/icon?p=android-chrome-512x512.png"/>
<link rel="alternate icon" href="/favicon.ico"/>
<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png"/>
<link rel="manifest" href="/manifest.json"/>
<link rel="mask-icon" href="/favicon.svg" color="#00ffaa"/>
<meta name="theme-color" content="#0e1116"/>
<!-- __GA_TAG__ -->
<!-- Google Fonts (Noto Sans KR, Noto Serif KR) -->
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700&family=Noto+Serif+KR:wght@400;700&display=swap"/>
<link rel="stylesheet" href="https://unpkg.com/katex@0.16.11/dist/katex.min.css" integrity="sha384-nB0miv6/jRmo5UMMR1wu3Gz6NLsoTkbqJghGIsx//Rlm+ZU03BU6SQNC66uf4l5+" crossorigin="anonymous"/>
<script src="https://unpkg.com/marked@4.3.0/marked.min.js" integrity="sha384-QsSpx6a0USazT7nK7w8qXDgpSAPhFsb2XtpoLFQ5+X2yFN6hvCKnwEzN8M5FWaJb" crossorigin="anonymous"></script>
<script src="https://unpkg.com/dompurify@3.1.6/dist/purify.min.js" integrity="sha384-+VfUPEb0PdtChMwmBcBmykRMDd+v6D/oFmB3rZM/puCMDYcIvF968OimRh4KQY9a" crossorigin="anonymous"></script>
<script src="https://unpkg.com/katex@0.16.11/dist/katex.min.js" integrity="sha384-7zkQWkzuo3B5mTepMUcHkMB5jZaolc2xDwL6VFqjFALcbeS9Ggm/Yr2r3Dy4lfFg" crossorigin="anonymous"></script>
<script src="https://unpkg.com/katex@0.16.11/dist/contrib/auto-render.min.js" integrity="sha384-43gviWU0YVjaDtb/GhzOouOXtZMP/7XUzwPTstBeZFe/+rCMvRwr4yROQP43s0Xk" crossorigin="anonymous"></script>
<style>
  /* --- CJK (한국어) Web Fonts (docs.asciidoctor.org) --- */
  @font-face{
    font-family:'Noto Sans KR';
    font-style:normal;
    font-weight:400;
    font-display:swap;
    src:local('Noto Sans KR Regular'),local('Noto Sans KR'),local('NotoSansKR-Regular'),
        url('/fonts/NotoSansKR-Regular.woff2') format('woff2');
  }
  @font-face{
    font-family:'Noto Sans KR';
    font-style:normal;
    font-weight:700;
    font-display:swap;
    src:local('Noto Sans KR Bold'),local('Noto Sans KR'),local('NotoSansKR-Bold'),
        url('/fonts/NotoSansKR-Bold.woff2') format('woff2');
  }
  @font-face{
    font-family:'Noto Serif KR';
    font-style:normal;
    font-weight:400;
    font-display:swap;
    src:local('Noto Serif KR Regular'),local('Noto Serif KR'),local('NotoSerifKR-Regular'),
        url('/fonts/NotoSerifKR-Regular.woff2') format('woff2');
  }
  @font-face{
    font-family:'Noto Serif KR';
    font-style:normal;
    font-weight:700;
    font-display:swap;
    src:local('Noto Serif KR Bold'),local('Noto Serif KR'),local('NotoSerifKR-Bold'),
        url('/fonts/NotoSerifKR-Bold.woff2') format('woff2');
  }
  @font-face{
    font-family:'D2Coding';
    font-style:normal;
    font-weight:400;
    font-display:swap;
    src:local('D2Coding'),local('D2 Coding'),
        url('/fonts/D2Coding.woff2') format('woff2');
  }
  @font-face{
    font-family:'D2Coding';
    font-style:normal;
    font-weight:700;
    font-display:swap;
    src:local('D2Coding Bold'),local('D2 Coding Bold'),
        url('/fonts/D2CodingBold.woff2') format('woff2');
  }
  :root{--bg:#ffffff;--fg:#1f2328;--muted:#656d76;--border:#d0d7de;--accent:#0969da;
    --accent2:#1a7f37;--card-bg:#f6f8fa;--chip-bg:#eaeef2;--mark-bg:#fff8c5;--mark-fg:#633c01}
  @media (prefers-color-scheme:dark){:root{--bg:#0e1116;--fg:#d7dbe0;--muted:#8b949e;
    --border:#2a2f37;--accent:#58a6ff;--accent2:#7ee787;--card-bg:#161b22;--chip-bg:#1f2937;
    --mark-bg:#4d3800;--mark-fg:#ffdf5d}}
  html,body{margin:0;background:var(--bg);color:var(--fg);font-family:'Noto Sans KR','Noto Sans Korean',system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;word-break:keep-all;overflow-wrap:break-word}
  .wrap{max-width:780px;margin:0 auto;padding:28px 18px 80px}
  h1{font-size:24px;margin:.2em 0}
  h1 .rmeta{color:var(--muted);font-size:13px;margin-left:8px;font-weight:normal}
  .docmeta{color:var(--muted);font-size:12px;margin:.1em 0 .6em;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}
  .docmeta a{color:var(--accent);text-decoration:none}
  .docmeta .docmeta-tags{display:inline-flex;align-items:center;gap:6px;margin-left:auto;flex-wrap:wrap}
  .docmeta .trunc-tag{display:inline-flex;align-items:center;gap:4px;color:#d29922;background:rgba(210,153,34,0.12);border:1px solid rgba(210,153,34,0.3);border-radius:10px;padding:1px 7px;font-size:11px;cursor:help;white-space:nowrap;line-height:1.4}
  .docmeta .trunc-tag.trunc-appendix, .docmeta .trunc-tag-appendix{color:#3fb950;background:rgba(63,185,80,0.12);border:1px solid rgba(63,185,80,0.3)}
  .docmeta .directive-tag{display:inline-flex;align-items:center;gap:4px;color:var(--accent2,#58a6ff);background:rgba(88,166,255,0.12);border:1px solid rgba(88,166,255,0.3);border-radius:10px;padding:1px 7px;font-size:11px;cursor:help;white-space:nowrap;line-height:1.4}
  .docmeta .stt-tag{display:inline-flex;align-items:center;gap:4px;color:#a371f7;background:rgba(163,113,247,0.12);border:1px solid rgba(163,113,247,0.3);border-radius:10px;padding:1px 7px;font-size:11px;cursor:help;white-space:nowrap;line-height:1.4}
  .docmeta .trunc-tag.trunc-stt{color:#f0883e;background:rgba(240,136,62,0.12);border:1px solid rgba(240,136,62,0.35)}
  .docmeta a.stt-link{color:var(--accent);text-decoration:none;margin-left:8px;font-weight:500;cursor:pointer}
  .docmeta a.stt-link:hover{text-decoration:underline}
  .stt-trunc-banner{background:rgba(240,136,62,0.12);border:1px solid rgba(240,136,62,0.35);color:var(--fg);border-radius:6px;padding:10px 14px;margin:0 0 14px;font-size:13px;line-height:1.5}
  .stt-trunc-banner strong{color:#f0883e}
  .stt-trunc-banner code{background:var(--chip-bg);border:1px solid var(--border);border-radius:4px;padding:2px 6px;font-size:11.5px;font-family:'D2Coding','D2 Coding',monospace}
  /* STT 전사 열기 모달 */
  #sttmodal{position:fixed;top:0;left:0;right:0;bottom:0;z-index:70;background:rgba(0,0,0,0.65);display:none;align-items:center;justify-content:center;padding:max(16px,env(safe-area-inset-top)) 16px max(16px,env(safe-area-inset-bottom));backdrop-filter:blur(4px);box-sizing:border-box}
  #sttmodal.open{display:flex!important}
  .sttsheet{background:var(--bg);color:var(--fg);width:min(880px,96vw);height:min(840px,90dvh);border:1px solid var(--border);border-radius:12px;box-shadow:0 16px 48px var(--shadow);display:flex;flex-direction:column;overflow:hidden}
  .stthead{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 18px;border-bottom:1px solid var(--border);background:var(--bar-bg);flex-shrink:0}
  .stthead h2{margin:0;font-size:16px;display:flex;align-items:center;gap:6px;color:var(--fg)}
  .sttmeta{color:var(--muted);font-size:12px;margin:3px 0 0}
  .stttools{display:flex;align-items:center;gap:6px;flex-shrink:0}
  .stttools button{background:var(--sec-bg);color:var(--sec-fg);border:1px solid var(--border);border-radius:6px;font-size:12px;padding:5px 10px;cursor:pointer;display:inline-flex;align-items:center;gap:4px;transition:background .15s ease,border-color .15s ease}
  .stttools button:hover{background:var(--hover);border-color:var(--accent)}
  .stttools .sttclose{font-size:16px;padding:4px 9px;line-height:1}
  .sttsearchbar{display:flex;align-items:center;gap:10px;padding:8px 18px;border-bottom:1px solid var(--border);background:var(--card-bg);flex-shrink:0}
  .sttsearchbar input{flex:1;min-width:0;height:32px;padding:0 10px;background:var(--input-bg,var(--bg));color:var(--fg);border:1px solid var(--border);border-radius:6px;font-size:13px}
  .sttsearchbar .sttcount{font-size:12px;color:var(--muted);white-space:nowrap}
  .sttbody{flex:1;overflow-y:auto;padding:16px 20px;min-height:0;line-height:1.65;font-size:14px;overscroll-behavior:contain}
  .stt-line{display:flex;gap:12px;margin-bottom:8px;padding:4px 6px;border-radius:4px;transition:background .1s ease}
  .stt-line:hover{background:var(--hover)}
  .stt-line.highlight{background:rgba(234,179,8,0.15)}
  .stt-ts{font-family:'D2Coding','D2 Coding',monospace;font-size:12px;color:var(--accent);background:var(--chip-bg);border:1px solid var(--border);border-radius:4px;padding:1px 6px;height:fit-content;flex-shrink:0;user-select:none;cursor:pointer}
  .stt-ts:hover{background:var(--active);border-color:var(--accent)}
  .stt-text{flex:1;word-break:break-word;color:var(--fg)}
  .stt-text mark{background:#ffe066;color:#111;border-radius:2px;padding:0 2px}
  [data-theme="dark"] .stt-text mark{background:#b28b00;color:#fff}
  .meta{color:var(--muted);font-size:13px;margin:.2em 0 1.2em}
  .meta a{color:var(--accent);text-decoration:none}
  .sec{color:var(--muted);font-size:11px;letter-spacing:.04em;text-transform:uppercase;margin:1.4em 0 .3em}
  .brand{color:var(--accent2);font-weight:600;font-size:12px}
  .foot{margin-top:2.5em;padding-top:1em;border-top:1px solid var(--border);color:var(--muted);font-size:12px}
  mark{background:var(--mark-bg);color:var(--mark-fg);padding:0 .15em;border-radius:2px}
  .md{line-height:1.75;font-size:16px;word-break:keep-all;overflow-wrap:break-word}
  .md h2{font-size:1.3em;margin:1.1em 0 .4em;border-bottom:1px solid var(--border);padding-bottom:.2em}
  .md h3{font-size:1.12em;margin:1em 0 .35em} .md p{margin:.6em 0}
  .md ul,.md ol{margin:.5em 0;padding-left:1.5em} .md li{margin:.3em 0}
  .md a{color:var(--accent)} .md img{max-width:100%;height:auto;display:block;margin:.8em auto;border-radius:6px;border:1px solid var(--border)}
  .md blockquote{margin:.6em 0;padding:.2em .9em;border-left:3px solid var(--border);color:var(--muted);font-family:'Noto Serif KR','Noto Serif Korean',Georgia,'Times New Roman',serif}
  .md code{background:var(--chip-bg);padding:.1em .35em;border-radius:3px;font-size:.9em;font-family:'D2Coding','D2 Coding','SFMono-Regular',Menlo,Monaco,Consolas,'Liberation Mono',monospace}
  .md pre{background:var(--card-bg);border:1px solid var(--border);border-radius:6px;padding:.8em;overflow-x:auto;max-width:100%;box-sizing:border-box}
  .md pre code{background:transparent;padding:0;border-radius:0;font-size:inherit;font-family:'D2Coding','D2 Coding','SFMono-Regular',Menlo,Monaco,Consolas,'Liberation Mono',monospace}
  .md table{border-collapse:collapse;margin:.6em 0;width:100%;max-width:100%;display:block;overflow-x:auto;box-sizing:border-box} .md th,.md td{border:1px solid var(--border);padding:.35em .65em}
  .md th{background:var(--chip-bg);font-weight:600}
  .md table td ul,.md table td ol{padding-left:1.2em;margin:.2em 0}
  .md table td li{margin:.15em 0}
  .md li > p{margin:.3em 0}
  .md li > p:first-child{margin-top:0}
  .md li > p:last-child{margin-bottom:0}
  /* --- AsciiDoc & Markdown 확장 스타일 --- */
  .md .admonitionblock{margin:1em 0;border-left:4px solid var(--accent);background:var(--card-bg);border-radius:6px;padding:.6em 1em}
  .md .admonitionblock.note{border-left-color:var(--accent)}
  .md .admonitionblock.important{border-left-color:#8250df}
  @media (prefers-color-scheme:dark){.md .admonitionblock.important{border-left-color:#a371f7}}
  .md .admonitionblock.tip{border-left-color:var(--accent2)}
  .md .admonitionblock.warning{border-left-color:#cf222e}
  @media (prefers-color-scheme:dark){.md .admonitionblock.warning{border-left-color:#f85149}}
  .md .admonitionblock.caution{border-left-color:var(--rel, #9a6700)}
  .md .admonitionblock .title,.md .admonitionblock td.icon{font-weight:700;margin-bottom:.3em;text-transform:uppercase;font-size:.85em;letter-spacing:.03em;color:var(--muted)}
  .md .quoteblock{margin:1.1em 0;padding:.6em 1.1em;border-left:3px solid var(--accent);background:var(--card-bg);border-radius:0 6px 6px 0}
  .md .quoteblock blockquote{margin:0;padding:0;border:none;color:var(--fg);font-family:'Noto Serif KR','Noto Serif Korean',Georgia,'Times New Roman',serif}
  .md .quoteblock .attribution{margin-top:.4em;font-size:.85em;color:var(--muted);text-align:right}
  .md .colist{margin:.5em 0;padding-left:1.2em;font-size:.9em;font-family:'D2Coding','D2 Coding','SFMono-Regular',Menlo,Monaco,Consolas,'Liberation Mono',monospace}
  .md .conum{display:inline-block;background:var(--accent);color:#fff;border-radius:50%;width:18px;height:18px;line-height:18px;text-align:center;font-size:11px;font-weight:bold;margin-right:4px;vertical-align:middle;font-family:'D2Coding','D2 Coding',monospace}
  .md .imageblock{margin:1em auto;text-align:center}
  .md .imageblock img{max-width:100%;height:auto;display:block;margin:0 auto;border-radius:6px;border:1px solid var(--border)}
  .md .imageblock .title{font-size:.85em;color:var(--muted);margin-top:.4em;font-style:italic}
  .md .math{font-family:'KaTeX_Math','Cambria Math','STIX Two Math','DejaVu Math TeX Gyre',Cambria,Georgia,serif;font-style:italic;color:var(--fg)}
  .md .math.inline{padding:0 .25em;background:var(--chip-bg);border-radius:3px;font-size:1.02em}
  .md .math.inline code{background:transparent;padding:0;font-family:inherit;font-size:inherit}
  .md .mathblock{margin:1em 0;padding:.8em 1.2em;background:var(--card-bg);border:1px solid var(--border);border-radius:6px;text-align:center;overflow-x:auto}
  .md .mathblock pre.math{background:transparent;border:0;padding:0;margin:0;display:inline-block;text-align:left;font-family:'KaTeX_Math','Cambria Math','STIX Two Math','DejaVu Math TeX Gyre',Cambria,Georgia,serif;font-size:1.08em}
  .md a.xref{color:var(--accent);text-decoration:none;border-bottom:1px dashed var(--accent);cursor:pointer;transition:border-color .15s ease}
  .md a.xref:hover{border-bottom-style:solid}
  .md :target{animation:target-highlight 2s ease-out;border-radius:4px}
  @keyframes target-highlight{0%{background-color:rgba(56,139,253,.25)}100%{background-color:transparent}}
  .md .lead{font-size:1.1em;line-height:1.6;font-weight:500;color:var(--fg)}
<body><div class="wrap" id="wrap"></div>
<div id="sttmodal" class="sttmodal" role="dialog" aria-modal="true" aria-labelledby="stttitle" style="display:none" onclick="if(event.target===this)closeSttReader()">
  <div class="sttsheet" tabindex="-1">
    <div class="stthead">
      <div>
        <h2 id="stttitle">🎙️ 음성 전사 (STT)</h2>
        <p class="sttmeta" id="sttmeta"></p>
      </div>
      <div class="stttools">
        <button class="sec" onclick="copySttText(false)" title="전사 텍스트만 복사">📋 텍스트 복사</button>
        <button class="sec" onclick="copySttText(true)" title="타임스탬프 포함 복사">⏱️ 타임스탬프 복사</button>
        <button class="rclose sttclose" onclick="closeSttReader()" title="닫기(ESC)" aria-label="전사 닫기">✕</button>
      </div>
    </div>
    <div class="sttsearchbar">
      <input id="sttq" placeholder="전사 내용 검색 (단어 또는 타임스탬프)..." oninput="filterSttLines(this.value)"/>
      <span id="sttcount" class="sttcount"></span>
    </div>
    <div id="sttbody" class="sttbody"></div>
  </div>
</div>
<script id="docdata" type="application/json">__DATA__</script>
<script>
function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
const DOMPURIFY_OPTS = {
  ADD_ATTR: ['target', 'aria-hidden', 'data-math', 'style', 'xmlns', 'display', 'class'],
  ADD_TAGS: ['mark', 'math', 'semantics', 'mrow', 'mi', 'mo', 'mn', 'msup', 'msub', 'msubsup', 'mfrac', 'munder', 'mover', 'munderover', 'mtable', 'mtr', 'mtd', 'mtext', 'mspace', 'mpadded', 'mphantom', 'annotation', 'span']
};
function renderMarkdown(src){
  if(!src) return '';
  const raw=String(src);
  const fallback=()=>esc(raw).replace(/\\\\r?\\\\n/g,'<br>');
  const parser=window.marked, purifier=window.DOMPurify;
  if(!parser||!purifier||typeof purifier.sanitize!=='function'||
     (typeof parser.parse!=='function'&&typeof parser!=='function')) return fallback();
  try{
    const s=raw.replace(/==([^=]+?)==/g,'<mark>$1</mark>');
    const html=typeof parser.parse==='function'?parser.parse(s):parser(s);
    return purifier.sanitize(html, DOMPURIFY_OPTS);
  }catch(e){ return fallback(); }
}
function splitTableCells(line){
  var raw=(line||'').trim();
  if(!raw.startsWith('|')) return [raw];
  raw=raw.substring(1);
  if(raw.endsWith('|') && !raw.endsWith('\\\\|')){
    raw=raw.substring(0, raw.length - 1);
  }
  var placeholder='\uE000';
  var parts=raw.replace(/\\\\\\|/g, placeholder).split('|');
  return parts.map(function(p){
    return p.replace(new RegExp(placeholder, 'g'), '|').trim();
  });
}
function parseColsAttr(text){
  if(!text) return null;
  var m=text.match(/cols=["']?([^"'\\]]+)["']?/i);
  var colsVal=m ? m[1].trim() : text.replace(/[\\[\\]]/g, '').trim();
  var starM=colsVal.match(/^(\\d+)\\*/);
  if(starM) return parseInt(starM[1], 10);
  if(colsVal.indexOf(',') !== -1){
    var parts=colsVal.split(',').filter(function(p){ return p.trim().length > 0; });
    var total=0;
    for(var i=0; i<parts.length; i++){
      var sm=parts[i].trim().match(/^(\\d+)\\*/);
      if(sm) total += parseInt(sm[1], 10);
      else total += 1;
    }
    return total > 0 ? total : null;
  }
  if(/^\\d+$/.test(colsVal)) return parseInt(colsVal, 10);
  return null;
}
function parseCellSpec(specStr){
  var res={colspan:1, rowspan:1, align:null, style:null};
  if(!specStr) return res;
  var spec=specStr.trim();
  var mSpan=spec.match(/(\\\\d+)?\\\\.(\\\\d+)\\\\+/);
  if(mSpan){
    if(mSpan[1]) res.colspan=parseInt(mSpan[1], 10);
    if(mSpan[2]) res.rowspan=parseInt(mSpan[2], 10);
  }else{
    var mCol=spec.match(/(?<!\\\\.)(\\\\d+)\\\\+/);
    if(mCol) res.colspan=parseInt(mCol[1], 10);
    var mRow=spec.match(/\\\\.(\\\\d+)\\\\+/);
    if(mRow) res.rowspan=parseInt(mRow[1], 10);
    var mDup=spec.match(/^(\\\\d+)\\\\*$/);
    if(mDup) res.colspan=parseInt(mDup[1], 10);
  }
  if(spec.indexOf('^') !== -1) res.align='center';
  else if(spec.indexOf('>') !== -1) res.align='right';
  else if(spec.indexOf('<') !== -1) res.align='left';
  var mStyle=spec.match(/([a-z])(?=\\\\|$)/i);
  if(mStyle) res.style=mStyle[1].toLowerCase();
  return res;
}
function extractCellsAndCols(tableLines, explicitCols){
  var placeholder='\uE000';
  var cellTokenRe=/(?:^|(?<=\\s))((?:\\d*\\.?\\d+\\+|\\d+\\*)?[\\^<>]?[a-z]?|[\\^<>]?[a-z]?)\\|/g;
  var cells=[];
  var firstLineCols=null;
  var firstBlockCols=0;
  var inFirstBlock=true;

  for(var i=0; i<tableLines.length; i++){
    var raw=tableLines[i].trim();
    if(!raw){
      if(inFirstBlock && cells.length > 0) inFirstBlock=false;
      continue;
    }
    var safe=raw.replace(/\\\\\\|/g, placeholder);
    var matches=[];
    var m;
    cellTokenRe.lastIndex=0;
    while((m=cellTokenRe.exec(safe)) !== null){
      matches.push({index: m.index, spec: m[1] || '', length: m[0].length});
    }

    if(matches.length === 0 || matches[0].index > 0){
      if(cells.length > 0 && matches.length === 0){
        cells[cells.length - 1].text += '\\n' + raw.replace(/\\\\\\|/g, '|');
        continue;
      }else if(matches.length === 0){
        var specObj=parseCellSpec('');
        cells.push({text: raw.replace(/\\\\\\|/g, '|'), spec: '', colspan: specObj.colspan, rowspan: specObj.rowspan, align: specObj.align, style: specObj.style});
        if(inFirstBlock) firstBlockCols += specObj.colspan;
        continue;
      }
    }

    var lineCellsCount=0;
    for(var j=0; j<matches.length; j++){
      var cur=matches[j];
      var spec=(cur.spec || '').trim();
      var specObj=parseCellSpec(spec);
      var startPos=cur.index + cur.length;
      var endPos=(j + 1 < matches.length) ? matches[j + 1].index : safe.length;
      var cellText=safe.substring(startPos, endPos).trim().replace(new RegExp(placeholder, 'g'), '|');
      cells.push({text: cellText, spec: spec, colspan: specObj.colspan, rowspan: specObj.rowspan, align: specObj.align, style: specObj.style});
      lineCellsCount += specObj.colspan;
      if(inFirstBlock) firstBlockCols += specObj.colspan;
    }
    if(firstLineCols === null && lineCellsCount > 0){
      firstLineCols=lineCellsCount;
    }
  }

  var numCols=explicitCols;
  if(!numCols || numCols <= 0){
    if(firstLineCols && firstLineCols > 1) numCols=firstLineCols;
    else if(firstBlockCols > 1) numCols=firstBlockCols;
    else numCols=1;
  }
  return {cells: cells, numCols: numCols};
}
function parseAdocTableRows(tableLines, explicitCols){
  var res=extractCellsAndCols(tableLines, explicitCols);
  var cells=res.cells;
  var numCols=res.numCols;
  if(!cells || cells.length === 0) return [];
  if(numCols <= 0) numCols=1;

  var rows=[];
  var cellIdx=0;
  var occupied=[];
  for(var c=0; c<numCols; c++) occupied.push(0);

  while(cellIdx < cells.length){
    var rowCells=[];
    var col=0;
    while(col < numCols && cellIdx < cells.length){
      if(occupied[col] > 0){
        occupied[col]--;
        col++;
        continue;
      }
      var cell=cells[cellIdx++];
      rowCells.push(cell);
      if(cell.rowspan > 1){
        for(var spanC=0; spanC<cell.colspan; spanC++){
          if(col + spanC < numCols){
            occupied[col + spanC]=cell.rowspan - 1;
          }
        }
      }
      col += cell.colspan;
    }
    while(col < numCols){
      if(occupied[col] > 0) occupied[col]--;
      col++;
    }
    if(rowCells.length > 0) rows.push(rowCells);
  }
  return rows;
}
function renderTableHtml(tableLines, blockMeta, anchorId){
  var explicitCols=parseColsAttr(blockMeta.cols || '');
  var rows=parseAdocTableRows(tableLines, explicitCols);
  if(!rows || rows.length === 0) return '';
  var idAttr=anchorId ? ' id=\"' + esc(anchorId) + '\"' : '';
  var tHtml='<table' + idAttr + '>';
  if(blockMeta.title) tHtml += '<caption>' + esc(blockMeta.title) + '</caption>';

  function renderCell(cell, tag){
    var attrs=[];
    if(cell.rowspan > 1) attrs.push('rowspan=\"' + cell.rowspan + '\"');
    if(cell.colspan > 1) attrs.push('colspan=\"' + cell.colspan + '\"');
    if(cell.align) attrs.push('style=\"text-align:' + cell.align + '\"');
    var attrStr=attrs.length > 0 ? ' ' + attrs.join(' ') : '';
    var text=(cell.text || '').trim();
    var innerHtml='';
    if(cell.style==='a' || (text.indexOf('\\n')!==-1 && /(?:^|\\n)\\s*[\\*\\-\\.]\\s+/.test(text))){
      innerHtml=convertAsciidocToHtml(text);
      if(innerHtml.startsWith('<p>') && innerHtml.endsWith('</p>') && (innerHtml.match(/<p>/g)||[]).length===1 && innerHtml.indexOf('\\n')===-1){
        innerHtml=innerHtml.substring(3, innerHtml.length-4);
      }
    }else if(text.indexOf('\\n\\n')!==-1){
      innerHtml=convertAsciidocToHtml(text);
    }else{
      var rawLines=text.split('\\n');
      var parts=rawLines.map(function(l){ return inlineAdocFormat(l); });
      innerHtml=parts.join(' ');
    }
    return '<' + tag + attrStr + '>' + innerHtml + '</' + tag + '>';
  }

  tHtml += '<thead><tr>' + rows[0].map(function(c){ return renderCell(c, 'th'); }).join('') + '</tr></thead>';
  if(rows.length > 1){
    tHtml += '<tbody>';
    for(var r=1; r<rows.length; r++){
      tHtml += '<tr>' + rows[r].map(function(c){ return renderCell(c, 'td'); }).join('') + '</tr>';
    }
    tHtml += '</tbody>';
  }
  tHtml += '</table>';
  return tHtml;
}

function inlineAdocFormat(text){
  if(!text) return '';
  var s=esc(text);
  var codeSpans=[];
  s=s.replace(/`(?![\\s])([^`\\n]+?)(?<![\\s])`/g, function(_, m1){
    codeSpans.push('<code>'+m1+'</code>');
    return '\\x00ADOCCODE'+(codeSpans.length-1)+'\\x00';
  });
  s=s.replace(/\\+\\+(?![\\s])([^\\+\\n]+?)(?<![\\s])\\+\\+/g, function(_, m1){
    codeSpans.push('<code>'+m1+'</code>');
    return '\\x00ADOCCODE'+(codeSpans.length-1)+'\\x00';
  });
  var varSpans=[];
  s=s.replace(/(?<![\\w\\\\\\$])\\$[A-Z_][A-Za-z0-9_]*\\b/g, function(m){
    varSpans.push(m);
    return '\\x00ADOCVAR'+(varSpans.length-1)+'\\x00';
  });
  s=s.replace(/(?<![\\w\\\\\\$])\\$\\{[A-Za-z0-9_]+\\}/g, function(m){
    varSpans.push(m);
    return '\\x00ADOCVAR'+(varSpans.length-1)+'\\x00';
  });
  s=s.replace(/(?<![\\w\\\\\\$])\\$\\d+(?:,\\d{3})*(?:\\.\\d+)?\\b/g, function(m){
    varSpans.push(m);
    return '\\x00ADOCVAR'+(varSpans.length-1)+'\\x00';
  });
  var mathSpans=[];
  s=s.replace(/(stem|latexmath|asciimath):\\\\[(.*?)\\\\]/gi, function(_, kind, content){
    mathSpans.push('<span class=\"math inline\" data-math=\"'+kind.toLowerCase()+'\"><code>'+content+'</code></span>');
    return '\\x00ADOCMATH'+(mathSpans.length-1)+'\\x00';
  });
  s=s.replace(/\\\\\\((.*?)\\\\\\)/g, function(_, content){
    mathSpans.push('<span class=\"math inline\" data-math=\"latex\"><code>'+content+'</code></span>');
    return '\\x00ADOCMATH'+(mathSpans.length-1)+'\\x00';
  });
  s=s.replace(/\\$\\$([^\\$]+?)\\$\\$/g, function(_, content){
    mathSpans.push('<span class=\"math inline\" data-math=\"latex\"><code>'+content+'</code></span>');
    return '\\x00ADOCMATH'+(mathSpans.length-1)+'\\x00';
  });
  s=s.replace(/(?<![\\w\\\\\\$])\\$([^\\$\\n]+?)\\$(?![\\w\\$])/g, function(_, content){
    mathSpans.push('<span class=\"math inline\" data-math=\"latex\"><code>'+content+'</code></span>');
    return '\\x00ADOCMATH'+(mathSpans.length-1)+'\\x00';
  });
  var linkSpans=[];
  s=s.replace(/(https?:\\/\\/[^\\s\\[\\]]+)\\[(.*?)\\]/g, function(_, u, l){
    linkSpans.push('<a href=\"'+u+'\" target=\"_blank\" rel=\"noopener\">'+l+'</a>');
    return '\\x00ADOCLINK'+(linkSpans.length-1)+'\\x00';
  });
  s=s.replace(/(?<!href=\")(https?:\\/\\/[^\\s<>\"\\'\\)]+)/g, function(_, u){
    linkSpans.push('<a href=\"'+u+'\" target=\"_blank\" rel=\"noopener\">'+u+'</a>');
    return '\\x00ADOCLINK'+(linkSpans.length-1)+'\\x00';
  });
  s=s.replace(/&lt;&lt;([a-zA-Z0-9_\\-\\.\\:\\/]+)(?:,\\s*([^&]+?))?&gt;&gt;/g, function(_, a, l){
    var label=(l||a).trim();
    linkSpans.push('<a href=\"#'+a.trim()+'\" class=\"xref\">'+label+'</a>');
    return '\\x00ADOCLINK'+(linkSpans.length-1)+'\\x00';
  });
  s=s.replace(/xref:([a-zA-Z0-9_\\-\\.\\:\\/]+)\\[(.*?)\\]/gi, function(_, a, l){
    var label=(l||a).trim();
    linkSpans.push('<a href=\"#'+a.trim()+'\" class=\"xref\">'+label+'</a>');
    return '\\x00ADOCLINK'+(linkSpans.length-1)+'\\x00';
  });
  s=s.replace(/\\[\\[([a-zA-Z0-9_\\-\\.\\:\\/]+)\\]\\]/g, '<a id=\"$1\" class=\"anchor\"></a>');
  s=s.replace(/\\s+\\+\\s*$/g, '<br>');
  s=s.replace(/(?<!#)#(?![\\s#])([^#\\n]+?)(?<![\\s#])#(?!#)/g, '<mark>$1</mark>');
  s=s.replace(/\\*\\*(?![\\s\\*])([^*\\n]+?)(?<![\\s\\*])\\*\\*/g, '<strong>$1</strong>');
  s=s.replace(/(?<!\\*)\\*(?![\\s\\*])([^*\\n]+?)(?<![\\s\\*])\\*(?!\\*)/g, '<strong>$1</strong>');
  s=s.replace(/__(?![\\s_])([^_\\n]+?)(?<![\\s_])__/g, '<em>$1</em>');
  s=s.replace(/(?<!_)_(?![\\s_])([^_\\n]+?)(?<![\\s_])_(?!_)/g, '<em>$1</em>');
  s=s.replace(/\\^(?![\\s\\^])([^\\^\\n]+?)(?<![\\s\\^])\\^/g, '<sup>$1</sup>');
  s=s.replace(/~(?![\\s~])([^~\\n]+?)(?<![\\s~])~/g, '<sub>$1</sub>');
  for(var i=0; i<linkSpans.length; i++) s=s.replace('\\x00ADOCLINK'+i+'\\x00', linkSpans[i]);
  for(var j=0; j<codeSpans.length; j++) s=s.replace('\\x00ADOCCODE'+j+'\\x00', codeSpans[j]);
  for(var k=0; k<mathSpans.length; k++) s=s.replace('\\x00ADOCMATH'+k+'\\x00', mathSpans[k]);
  for(var v=0; v<varSpans.length; v++) s=s.replace('\\x00ADOCVAR'+v+'\\x00', varSpans[v]);
  return s;
}

// 자체 완결형 경량 AsciiDoc 렌더러 (외부 CDN/루비런타임 의존성 제로, 번개같은 로딩 속도)
function convertAsciidocToHtml(raw){
  if(!raw) return '';
  const NL=String.fromCharCode(10);
  const lines=String(raw).split(NL);
  const out=[];
  let inBlock=null;
  let blockMeta={};
  let blockLines=[];

  var listStack=[];
  var inItem=false;
  var inContinuation=false;
  var pendingContinuation=false;
  var continuationLines=[];
  var pendingMeta=null;
  var pendingBlockLines=[];
  var normalPLines=[];
  var pendingAnchor=null;

  function closeItem(){
    if(inItem){ inItem=false; return ['</li>']; }
    return [];
  }
  function flushList(){
    if(listStack.length===0) return;
    out.push.apply(out, closeItem());
    while(listStack.length>0){
      var entry=listStack.pop();
      out.push('</'+entry.tag+'>');
      if(listStack.length>0) out.push('</li>');
    }
  }
  function adjustListLevel(tag, level){
    if(listStack.length===0){
      out.push('<'+tag+'>');
      listStack.push({tag:tag, level:level});
      return;
    }
    var top=listStack[listStack.length-1];
    if(level > top.level){
      out.push('<'+tag+'>');
      listStack.push({tag:tag, level:level});
    }else if(level < top.level){
      out.push.apply(out, closeItem());
      while(listStack.length>0 && listStack[listStack.length-1].level > level){
        var e=listStack.pop();
        out.push('</'+e.tag+'>');
        if(listStack.length>0) out.push('</li>');
      }
      if(listStack.length>0 && listStack[listStack.length-1].tag !== tag){
        var old=listStack.pop();
        out.push('</'+old.tag+'>');
        out.push('<'+tag+'>');
        listStack.push({tag:tag, level:level});
      }
    }else{
      if(top.tag !== tag){
        out.push.apply(out, closeItem());
        listStack.pop();
        out.push('</'+top.tag+'>');
        out.push('<'+tag+'>');
        listStack.push({tag:tag, level:level});
      }else{
        out.push.apply(out, closeItem());
      }
    }
  }

  function formatParagraphLines(pLines){
    if(!pLines || pLines.length===0) return '';
    var formattedParts=pLines.map(function(l){ return inlineAdocFormat(l); });
    var res='';
    for(var i=0; i<formattedParts.length; i++){
      var p=formattedParts[i];
      if(i===0){ res=p; }
      else{
        if(res.endsWith('<br>')) res += p;
        else res += ' ' + p;
      }
    }
    return res;
  }

  function flushPendingSingleBlock(){
    if(!pendingMeta || (pendingMeta.kind!=='quote' && pendingMeta.kind!=='admonition')) return;
    if(pendingBlockLines.length===0){ pendingMeta=null; return; }
    var anchorAttr=pendingAnchor ? ' id=\"'+esc(pendingAnchor)+'\"' : '';
    pendingAnchor=null;
    var pContent=formatParagraphLines(pendingBlockLines);
    if(pendingMeta.kind==='quote'){
      var attrText=pendingMeta.author?esc(pendingMeta.author)+(pendingMeta.source?' — '+esc(pendingMeta.source):''):'';
      out.push('<div class=\"quoteblock\"' + anchorAttr + '><blockquote><p>'+pContent+'</p></blockquote>'+(attrText?'<div class=\"attribution\">'+attrText+'</div>':'')+'</div>');
    }else if(pendingMeta.kind==='admonition'){
      var admType=(pendingMeta.type||'NOTE').toLowerCase();
      var admTitle=esc(pendingMeta.type||'NOTE');
      out.push('<div class=\"admonitionblock '+admType+'\"' + anchorAttr + '><div class=\"title\">'+admTitle+'</div><div class=\"content\"><p>'+pContent+'</p></div></div>');
    }
    pendingMeta=null;
    pendingBlockLines=[];
  }

  function flushContinuation(){
    if(inContinuation && continuationLines.length>0){
      var anchorAttr=pendingAnchor ? ' id=\"'+esc(pendingAnchor)+'\"' : '';
      pendingAnchor=null;
      out.push('<p' + anchorAttr + '>'+formatParagraphLines(continuationLines)+'</p>');
    }
    inContinuation=false;
    pendingContinuation=false;
    continuationLines=[];
  }

  function flushNormalP(){
    if(normalPLines.length>0){
      flushList();
      var anchorAttr=pendingAnchor ? ' id=\"'+esc(pendingAnchor)+'\"' : '';
      pendingAnchor=null;
      out.push('<p' + anchorAttr + '>'+formatParagraphLines(normalPLines)+'</p>');
      normalPLines=[];
    }
  }

  function flushBlock(){
    if(!inBlock) return;
    var anchorAttr=pendingAnchor ? ' id=\"'+esc(pendingAnchor)+'\"' : '';
    pendingAnchor=null;

    if(inBlock==='quote'){
      var qParagraphs=[];
      var currP=[];
      for(var b=0; b<blockLines.length; b++){
        var bl=blockLines[b];
        if(!bl.trim()){
          if(currP.length>0){ qParagraphs.push(formatParagraphLines(currP)); currP=[]; }
        }else{ currP.push(bl); }
      }
      if(currP.length>0) qParagraphs.push(formatParagraphLines(currP));
      var qContent=qParagraphs.map(function(p){ return '<p>'+p+'</p>'; }).join('');
      var attr='';
      if(blockMeta.author||blockMeta.source){
        attr='<div class=\"attribution\">'+esc(blockMeta.author||'')+
             (blockMeta.source?' — '+esc(blockMeta.source):'')+'</div>';
      }
      out.push('<div class=\"quoteblock\"' + anchorAttr + '><blockquote>'+qContent+'</blockquote>'+attr+'</div>');
    }else if(inBlock==='admonition'){
      var admParagraphs=[];
      var currP=[];
      for(var b=0; b<blockLines.length; b++){
        var bl=blockLines[b];
        if(!bl.trim()){
          if(currP.length>0){ admParagraphs.push(formatParagraphLines(currP)); currP=[]; }
        }else{ currP.push(bl); }
      }
      if(currP.length>0) admParagraphs.push(formatParagraphLines(currP));
      var admContent=admParagraphs.map(function(p){ return '<p>'+p+'</p>'; }).join('');
      var type=(blockMeta.type||'NOTE').toLowerCase();
      out.push('<div class=\"admonitionblock '+esc(type)+'\"' + anchorAttr + '><div class=\"title\">'+
               esc(blockMeta.type||'NOTE')+'</div><div class=\"content\">'+admContent+'</div></div>');
    }else if(inBlock==='code'){
      var codeText=esc(blockLines.join(NL)).replace(/&lt;(\\d+)&gt;/g,'<span class=\"conum\">&lt;$1&gt;</span>');
      out.push('<div class=\"listingblock\"' + anchorAttr + '><div class=\"content\"><pre><code class=\"language-'+esc(blockMeta.lang||'')+'\">'+codeText+'</code></pre></div></div>');
    }else if(inBlock==='math'){
      var mathText=esc(blockLines.join(NL));
      var mType=esc(blockMeta.type||'latex');
      out.push('<div class=\"mathblock display\"' + anchorAttr + ' data-math=\"'+mType+'\"><div class=\"content\"><pre class=\"math\"><code>'+mathText+'</code></pre></div></div>');
    }else if(inBlock==='table'){
      var tblHtml=renderTableHtml(blockLines, blockMeta, anchorAttr.replace(' id=\"', '').replace('\"', ''));
      if(tblHtml) out.push(tblHtml);
    }
    inBlock=null; blockMeta={}; blockLines=[];
  }

  function matchList(trimmed){
    var mStar=trimmed.match(/^(\\*{1,5})\\s+(.+)$/);
    if(mStar) return {tag:'ul', level:mStar[1].length, text:mStar[2]};
    var mHyphen=trimmed.match(/^-\\s+(.+)$/);
    if(mHyphen) return {tag:'ul', level:1, text:mHyphen[1]};
    var mDot=trimmed.match(/^(\\.{1,5})\\s+(.+)$/);
    if(mDot) return {tag:'ol', level:mDot[1].length, text:mDot[2]};
    var mNum=trimmed.match(/^\\d+[\\.\\)]\\s+(.+)$/);
    if(mNum) return {tag:'ol', level:1, text:mNum[1]};
    return null;
  }

  function extractHeadingAnchor(hText){
    var m=hText.match(/\\[#([a-zA-Z0-9_\\-\\.\\:\\/]+)\\]|\\[\\[([a-zA-Z0-9_\\-\\.\\:\\/]+)\\]\\]/);
    if(m){
      var anc=m[1]||m[2];
      var clean=(hText.substring(0, m.index)+hText.substring(m.index+m[0].length)).trim();
      return {text:clean, anchor:anc};
    }
    var anc2=pendingAnchor;
    pendingAnchor=null;
    return {text:hText, anchor:anc2};
  }

  for(var i=0; i<lines.length; i++){
    var line=lines[i];
    var trimmed=line.trim();

    if(!inBlock){
      var anchorM=trimmed.match(/^\\[#([a-zA-Z0-9_\\-\\.\\:\\/]+)\\]$/) || trimmed.match(/^\\[\\[([a-zA-Z0-9_\\-\\.\\:\\/]+)\\]\\]$/);
      if(anchorM){
        flushNormalP(); flushContinuation(); flushPendingSingleBlock();
        pendingAnchor=anchorM[1].trim();
        continue;
      }

      var qm=trimmed.match(/^\\[quote(?:,\\s*([^,\\]]+))?(?:,\\s*([^\\]]+))?\\]/i);
      if(qm){
        flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList();
        pendingMeta={kind:'quote', author:qm[1]?qm[1].trim():'', source:qm[2]?qm[2].trim():''};
        pendingBlockLines=[];
        continue;
      }
      var am=trimmed.match(/^\\[(NOTE|IMPORTANT|TIP|WARNING|CAUTION)\\]/i);
      if(am){
        flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList();
        pendingMeta={kind:'admonition', type:am[1].toUpperCase()};
        pendingBlockLines=[];
        continue;
      }
      var sm=trimmed.match(/^\\[source(?:,\\s*([a-zA-Z0-9_-]+))?\\]/i);
      if(sm){
        flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList();
        pendingMeta={kind:'code', lang:sm[1]?sm[1].trim():''};
        continue;
      }
      var mathM=trimmed.match(/^\\[(latexmath|stem|asciimath)\\]$/i);
      if(mathM){
        flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList();
        pendingMeta={kind:'math', type:mathM[1].toLowerCase()};
        continue;
      }
      var tm=trimmed.match(/^\\[(.*cols.*|.*header.*|\\d+\\*|[0-9,]+)\\]$/i);
      if(tm){
        flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList();
        if(pendingMeta && pendingMeta.kind==='table'){
          pendingMeta.cols=tm[1];
        }else{
          pendingMeta={kind:'table', cols:tm[1]};
        }
        continue;
      }
      var titleM=trimmed.match(/^\\.([^\\.\\s].*)$/);
      if(titleM){
        flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList();
        if(pendingMeta && pendingMeta.kind==='table'){
          pendingMeta.title=titleM[1].trim();
        }else{
          pendingMeta={kind:'table', title:titleM[1].trim()};
        }
        continue;
      }

      if(trimmed==='____'){
        flushNormalP(); flushContinuation(); flushList();
        inBlock='quote';
        blockMeta=(pendingMeta&&pendingMeta.kind==='quote')?pendingMeta:{};
        pendingMeta=null; pendingBlockLines=[]; blockLines=[];
        continue;
      }
      if(trimmed==='===='){
        flushNormalP(); flushContinuation(); flushList();
        inBlock='admonition';
        blockMeta=(pendingMeta&&pendingMeta.kind==='admonition')?pendingMeta:{type:'NOTE'};
        pendingMeta=null; pendingBlockLines=[]; blockLines=[];
        continue;
      }
      if(trimmed==='----'){
        flushNormalP(); flushContinuation(); flushList();
        if(pendingMeta && pendingMeta.kind==='math'){
          inBlock='math';
          blockMeta=pendingMeta;
        }else{
          inBlock='code';
          blockMeta=(pendingMeta&&pendingMeta.kind==='code')?pendingMeta:{};
        }
        pendingMeta=null; pendingBlockLines=[]; blockLines=[];
        continue;
      }
      if(trimmed==='++++'){
        flushNormalP(); flushContinuation(); flushList();
        inBlock='math';
        blockMeta=(pendingMeta&&pendingMeta.kind==='math')?pendingMeta:{kind:'math', type:'latex'};
        pendingMeta=null; pendingBlockLines=[]; blockLines=[];
        continue;
      }
      if(trimmed==='|==='){
        flushNormalP(); flushContinuation(); flushList();
        inBlock='table';
        blockMeta=(pendingMeta&&pendingMeta.kind==='table')?pendingMeta:{};
        pendingMeta=null; pendingBlockLines=[]; blockLines=[];
        continue;
      }

      var imgMatch=trimmed.match(/^image::([^\\[]+)\\[([^,\\]]*)(?:,\\s*title=(?:\"([^\"]*)\"|'([^']*)'|([^\\]]*)))?\\]/);
      if(imgMatch){
        flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList();
        var src=imgMatch[1].trim();
        var alt=imgMatch[2]?imgMatch[2].trim():'';
        var cap=imgMatch[3]||imgMatch[4]||imgMatch[5]||'';
        var anchorAttr=pendingAnchor ? ' id=\"'+esc(pendingAnchor)+'\"' : '';
        pendingAnchor=null;
        out.push('<div class=\"imageblock\"' + anchorAttr + '><img src=\"'+esc(src)+'\" alt=\"'+esc(alt)+'\">'+
                 (cap?'<div class=\"title\">'+esc(cap)+'</div>':'')+'</div>');
        continue;
      }
      var colMatch=trimmed.match(/^<(\\d+)>\\s*(.+)/);
      if(colMatch){
        flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList();
        out.push('<div class=\"colist\"><span class=\"conum\">&lt;'+colMatch[1]+'&gt;</span> '+inlineAdocFormat(colMatch[2])+'</div>');
        continue;
      }
      if(/^'{3,}$/.test(trimmed)){
        flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList();
        var anchorAttr=pendingAnchor ? ' id=\"'+esc(pendingAnchor)+'\"' : '';
        pendingAnchor=null;
        out.push('<hr' + anchorAttr + '>');
        continue;
      }
      var h1Match=trimmed.match(/^=\\s+(.+)$/);
      if(h1Match){ flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList(); var hInfo=extractHeadingAnchor(h1Match[1]); var idAttr=hInfo.anchor?' id=\"'+esc(hInfo.anchor)+'\"':''; out.push('<h1'+idAttr+'>'+inlineAdocFormat(hInfo.text)+'</h1>'); continue; }
      var h2Match=trimmed.match(/^==\\s+(.+)$/);
      if(h2Match){ flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList(); var hInfo=extractHeadingAnchor(h2Match[1]); var idAttr=hInfo.anchor?' id=\"'+esc(hInfo.anchor)+'\"':''; out.push('<h2'+idAttr+'>'+inlineAdocFormat(hInfo.text)+'</h2>'); continue; }
      var h3Match=trimmed.match(/^===\\s+(.+)$/);
      if(h3Match){ flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList(); var hInfo=extractHeadingAnchor(h3Match[1]); var idAttr=hInfo.anchor?' id=\"'+esc(hInfo.anchor)+'\"':''; out.push('<h3'+idAttr+'>'+inlineAdocFormat(hInfo.text)+'</h3>'); continue; }
      var h4Match=trimmed.match(/^====\\s+(.+)$/);
      if(h4Match){ flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList(); var hInfo=extractHeadingAnchor(h4Match[1]); var idAttr=hInfo.anchor?' id=\"'+esc(hInfo.anchor)+'\"':''; out.push('<h4'+idAttr+'>'+inlineAdocFormat(hInfo.text)+'</h4>'); continue; }

      var attrMatch=trimmed.match(/^:[a-zA-Z0-9_-]+:\\s*(.*)$/);
      if(attrMatch){ continue; }

      var singleAdm=trimmed.match(/^(NOTE|TIP|IMPORTANT|WARNING|CAUTION):\\s*(.+)$/i);
      if(singleAdm){
        flushNormalP(); flushContinuation(); flushPendingSingleBlock(); flushList();
        var admType=singleAdm[1].toUpperCase();
        var anchorAttr=pendingAnchor ? ' id=\"'+esc(pendingAnchor)+'\"' : '';
        pendingAnchor=null;
        out.push('<div class=\"admonitionblock '+admType.toLowerCase()+'\"' + anchorAttr + '><div class=\"title\">'+esc(admType)+'</div><div class=\"content\"><p>'+inlineAdocFormat(singleAdm[2])+'</p></div></div>');
        continue;
      }

      if(trimmed==='+'){
        if(inItem){
          flushContinuation();
          pendingContinuation=true;
        }
        continue;
      }

      var listM=matchList(trimmed);
      if(listM){
        flushNormalP(); flushContinuation(); pendingContinuation=false; flushPendingSingleBlock();
        var itemText=inlineAdocFormat(listM.text);
        adjustListLevel(listM.tag, listM.level);
        out.push('<li>'+itemText);
        inItem=true;
        inContinuation=false;
        continue;
      }

      if(!trimmed){
        if(inContinuation){
          flushContinuation();
        }
        flushNormalP(); flushPendingSingleBlock();
        continue;
      }

      if(pendingMeta && (pendingMeta.kind==='quote' || pendingMeta.kind==='admonition')){
        pendingBlockLines.push(trimmed);
        continue;
      }

      if((pendingContinuation || inContinuation) && inItem){
        pendingContinuation=false;
        inContinuation=true;
        continuationLines.push(trimmed);
        continue;
      }

      normalPLines.push(trimmed);
    }else{
      if(inBlock==='quote'&&trimmed==='____') flushBlock();
      else if(inBlock==='admonition'&&trimmed==='====') flushBlock();
      else if(inBlock==='code'&&trimmed==='----') flushBlock();
      else if(inBlock==='math'&&(trimmed==='++++'||trimmed==='----')) flushBlock();
      else if(inBlock==='table'&&trimmed==='|===') flushBlock();
      else{
        blockLines.push(line);
      }
    }
  }
  flushBlock();
  flushNormalP();
  flushContinuation();
  flushPendingSingleBlock();
  flushList();
  return out.join(NL);
}

function renderAsciidoc(src){
  if(!src) return '';
  const raw=String(src);
  const purifier=window.DOMPurify;
  try{
    const html = convertAsciidocToHtml(raw);
    if(purifier && typeof purifier.sanitize==='function'){
      return purifier.sanitize(html, DOMPURIFY_OPTS);
    }
    return html;
  }catch(_){
    return renderMarkdown(raw);
  }
}

function applyMathRendering(container){
  if(!container) return;
  if(typeof renderMathInElement === 'function'){
    try{
      renderMathInElement(container, {
        delimiters: [
          {left: '$$', right: '$$', display: true},
          {left: '$', right: '$', display: false},
          {left: '\\\\(', right: '\\\\)', display: false},
          {left: '\\\\[', right: '\\\\]', display: true}
        ],
        ignoredTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code', 'option'],
        throwOnError: false
      });
    }catch(_){}
  }
  if(typeof katex !== 'undefined'){
    try{
      container.querySelectorAll('.mathblock').forEach(function(mb){
        var codeEl = mb.querySelector('pre.math code, pre.math');
        if(codeEl && !mb.getAttribute('data-katex-rendered')){
          var text = (codeEl.textContent || '').trim();
          if(text){
            try{
              mb.innerHTML = '';
              katex.render(text, mb, { displayMode: true, throwOnError: false });
              mb.setAttribute('data-katex-rendered', 'true');
            }catch(_){}
          }
        }
      });
      container.querySelectorAll('.math.inline').forEach(function(mi){
        var codeEl = mi.querySelector('code') || mi;
        if(codeEl && !mi.getAttribute('data-katex-rendered')){
          var text = (codeEl.textContent || '').trim();
          if(text){
            try{
              var span = document.createElement('span');
              katex.render(text, span, { displayMode: false, throwOnError: false });
              mi.parentNode.replaceChild(span, mi);
            }catch(_){}
          }
        }
      });
    }catch(_){}
  }
}
function isAsciidoc(src, format){
  if(format){
    const fmt=String(format).toLowerCase().trim();
    if(fmt==='adoc'||fmt==='asciidoc') return true;
    if(fmt==='md'||fmt==='markdown') return false;
  }
  if(!src) return false;
  const s=String(src);
  return /(?:^|\\n)\\[(NOTE|TIP|IMPORTANT|WARNING|CAUTION|quote|source)[^\\]]*\\]|(?:^|\\n)\\|===|(?:^|\\n)image::/m.test(s);
}
function renderContent(src, format){
  if(!src) return '';
  if(isAsciidoc(src, format)){
    return renderAsciidoc(src);
  }
  return renderMarkdown(src);
}
function docMetaHtml(dc){
  if(!dc) return '';
  const hasUrl = !!dc.url;
  const isTrunc = !!(dc.raw_truncated || (dc.meta && dc.meta.raw_truncated));
  const isAppTrunc = isTrunc && !!(dc.appendix_truncated || (dc.meta && dc.meta.appendix_truncated));
  const directive = (dc.directive || (dc.meta && dc.meta.directive) || '').trim();
  const isStt = !!(dc.is_stt || (dc.meta && (dc.meta.is_stt || dc.meta.stt_applied || dc.meta.stt)));
  const isSttTrunc = isStt && !!(dc.stt_truncated || (dc.meta && dc.meta.stt_truncated) || isTrunc);
  if(!hasUrl && !isTrunc && !directive && !isStt) return '';
  let h='<p class=docmeta>';
  if(hasUrl){
    h+='<a href="'+esc(dc.url)+'" target=_blank rel=noopener>↗ 원문 열기</a>';
    if(isStt){
      h+=' <a href="#" class="stt-link" onclick="openSttReader();return false;" title="음성 인식(STT) 전사 텍스트 열기">↗ 전사 열기</a>';
    }
  } else {
    if(isStt){
      h+='<a href="#" class="stt-link" onclick="openSttReader();return false;" title="음성 인식(STT) 전사 텍스트 열기">↗ 전사 열기</a>';
    } else {
      h+='<span></span>';
    }
  }
  let tags=[];
  if(directive){
    const dispDir = directive.length > 25 ? directive.slice(0, 25) + '…' : directive;
    tags.push('<span class="directive-tag" title="적재 시 지정한 초점: '+esc(directive)+'">🎯 '+esc(dispDir)+'</span>');
  }
  if(isStt){
    tags.push('<span class="directive-tag stt-tag" title="음성 인식(STT)을 적용하여 작성한 문서">🎙️ STT</span>');
  }
  if(isAppTrunc){
    const orig=(dc.orig_chars || (dc.meta && dc.meta.orig_chars)) || 0;
    const raw=(dc.raw_chars || (dc.meta && dc.meta.raw_chars)) || 0;
    let tip = '원문의 부록(Appendix) 부분을 절단한 문서';
    let label='✂️ 원문 일부 절단';
    if(orig > 0 && raw > 0){
      tip+=' (원문: '+orig.toLocaleString()+'자 → 적재: '+raw.toLocaleString()+'자)';
      label+=' ('+raw.toLocaleString()+' / '+orig.toLocaleString()+'자)';
    } else if(raw > 0){
      label+=' ('+raw.toLocaleString()+'자)';
    }
    const tagClass = isAppTrunc ? 'trunc-tag trunc-appendix' : 'trunc-tag';
    tags.push('<span class="'+tagClass+'" title="'+esc(tip)+'">'+esc(label)+'</span>');
  } else if(isSttTrunc){
    const orig=(dc.stt_orig_chars || (dc.meta && dc.meta.stt_orig_chars) || dc.orig_chars || (dc.meta && dc.meta.orig_chars)) || 0;
    const raw=(dc.stt_raw_chars || (dc.meta && dc.meta.stt_raw_chars) || dc.raw_chars || (dc.meta && dc.meta.raw_chars)) || 0;
    let tip = '음성 전사(STT) 전문이 일부 절단된 상태에서 본문(상세)이 작성된 문서';
    let label = '✂️ STT 일부 절단';
    if(orig > 0 && raw > 0){
      tip += ' (원문: '+orig.toLocaleString()+'자 → 적재: '+raw.toLocaleString()+'자)';
      label += ' ('+raw.toLocaleString()+' / '+orig.toLocaleString()+'자)';
    } else if(raw > 0){
      label += ' ('+raw.toLocaleString()+'자)';
    }
    tags.push('<span class="trunc-tag trunc-stt" title="'+esc(tip)+'">'+esc(label)+'</span>');
  } else if(isTrunc){
    const orig=(dc.orig_chars || (dc.meta && dc.meta.orig_chars)) || 0;
    const raw=(dc.raw_chars || (dc.meta && dc.meta.raw_chars)) || 0;
    let tip = '글자 수 상한으로 원문 일부를 절단한 문서';
    let label='✂️ 원문 일부 절단';
    if(orig > 0 && raw > 0){
      tip+=' (원문: '+orig.toLocaleString()+'자 → 적재: '+raw.toLocaleString()+'자)';
      label+=' ('+raw.toLocaleString()+' / '+orig.toLocaleString()+'자)';
    } else if(raw > 0){
      label+=' ('+raw.toLocaleString()+'자)';
    }
    const tagClass = isAppTrunc ? 'trunc-tag trunc-appendix' : 'trunc-tag';
    tags.push('<span class="'+tagClass+'" title="'+esc(tip)+'">'+esc(label)+'</span>');
  }
  if(tags.length){
    h+='<span class="docmeta-tags">'+tags.join(' ')+'</span>';
  }
  h+='</p>';
  return h;
}
const dc=JSON.parse(document.getElementById('docdata').textContent||'{}');
let h='<div class=brand>Claire Bible · 공유 문서</div>';
h+='<h1>'+esc(dc.title||'(제목 없음)')+(dc.source_type?' <span class=rmeta>'+esc(dc.source_type)+'</span>':'')+'</h1>';
h+=docMetaHtml(dc);
const isStt = !!(dc.is_stt || (dc.meta && (dc.meta.is_stt || dc.meta.stt_applied || dc.meta.stt)));
const isSttTrunc = isStt && !!(dc.stt_truncated || (dc.meta && dc.meta.stt_truncated) || dc.raw_truncated || (dc.meta && dc.meta.raw_truncated));
if(isSttTrunc){
  h+='<div class="stt-trunc-banner">⚠️ <strong>음성 전사(STT) 일부 절단 안내</strong>: 전체 전사 내용 중 일부만 반영된 상태에서 본문(상세)이 작성되었습니다. 전체 재전사 명령: <code>claire video-reprocess --doc-id '+esc(dc.id||'')+' --apply --full-content</code></div>';
}
if((dc.extra_sources||[]).length){
  h+='<div class=sec>병합된 출처 ('+dc.extra_sources.length+')</div><ul class=srclist>'+
    dc.extra_sources.map(s=>'<li><a href="'+esc(s.url||'')+'" target=_blank rel=noopener>'+
      esc(s.title||s.url||'')+'</a></li>').join('')+'</ul>';
}
const directive = (dc.directive || (dc.meta && dc.meta.directive) || '').trim();
if(directive){
  h+='<div class=sec>초점</div><div class="md" style="margin-bottom:.8em">🎯 <strong>'+esc(directive)+'</strong></div>';
}
if(dc.summary){ h+='<div class=sec>요약</div><div class="md">'+renderContent(dc.summary, dc.detail_format)+'</div>'; }
if(dc.detail_html){
  const purifier=window.DOMPurify;
  const cleanHtml=(purifier && typeof purifier.sanitize==='function')?purifier.sanitize(dc.detail_html, DOMPURIFY_OPTS):dc.detail_html;
  h+='<div class=sec>상세</div><div class="md">'+cleanHtml+'</div>';
}else if(dc.detail){
  h+='<div class=sec>상세</div><div class="md">'+renderContent(dc.detail, dc.detail_format)+'</div>';
}
if(!dc.summary && !dc.detail && !dc.detail_html){ h+='<p class=meta>문서에 요약/상세 내용이 없습니다.</p>'; }
h+='<div class=foot>이 링크는 이 문서 하나만 읽기 전용으로 공유합니다.</div>';
document.getElementById('wrap').innerHTML=h;
applyMathRendering(document.getElementById('wrap'));

// --- 공유 페이지 STT 전사 열기 모달 뷰어 제어 ---
let curSttData = dc;
let curSttFilter = '';

function openSttReader(docId){
  const modal = document.getElementById('sttmodal');
  if(!modal) return;
  curSttData = dc;

  if(!dc.is_stt && !(dc.meta && (dc.meta.is_stt || dc.meta.transcript_segments))){
    alert('음성 전사(STT) 데이터가 없는 문서입니다.');
    return;
  }

  const titleEl = document.getElementById('stttitle');
  if(titleEl) titleEl.textContent = '🎙️ 음성 전사 (STT) — ' + (dc.title || '(제목 없음)');

  const metaEl = document.getElementById('sttmeta');
  let metaTxt = '';
  const dur = (dc.meta && dc.meta.duration_sec) || dc.duration_sec || 0;
  if(dur > 0){
    const m = Math.floor(dur / 60);
    const s = Math.floor(dur % 60);
    metaTxt += '재생 시간: ' + m + '분 ' + s + '초';
  }
  const segs = dc.transcript_segments || (dc.meta && dc.meta.transcript_segments) || [];
  if(segs.length > 0){
    metaTxt += (metaTxt ? ' · ' : '') + '총 ' + segs.length.toLocaleString() + '개 발화 구간';
  }
  if(metaEl) metaEl.textContent = metaTxt;

  const input = document.getElementById('sttq');
  if(input) input.value = '';
  curSttFilter = '';

  renderSttLines();
  modal.classList.add('open');
  modal.style.display = 'flex';
  if(input) requestAnimationFrame(()=>input.focus());
}

function closeSttReader(){
  const modal = document.getElementById('sttmodal');
  if(!modal) return;
  modal.classList.remove('open');
  modal.style.display = 'none';
}

function renderSttLines(){
  const body = document.getElementById('sttbody');
  const countEl = document.getElementById('sttcount');
  if(!body || !curSttData) return;

  let segs = dc.transcript_segments || (dc.meta && dc.meta.transcript_segments) || [];
  let rawText = dc.stt_transcript || '';

  let h = '';
  const isTrunc = !!(dc.stt_truncated || (dc.meta && dc.meta.stt_truncated));
  if(isTrunc){
    h += '<div class="stt-trunc-banner">⚠️ <strong>전사 일부 절단 상태</strong>: 글자 수 상한 또는 오디오 구간 누락으로 인해 음성 전사의 일부만 반영되었습니다. 전체 내용을 복원하려면 <code>claire video-reprocess --doc-id ' + esc(dc.id||'') + ' --apply --full-content</code>를 실행하십시오.</div>';
  }

  const q = (curSttFilter || '').toLowerCase().trim();
  let matchCount = 0;
  let totalCount = 0;

  if(segs && segs.length > 0){
    totalCount = segs.length;
    segs.forEach(s => {
      const startF = s.start_sec != null ? s.start_sec : (s.start != null ? s.start : 0.0);
      const totalSec = Math.floor(startF);
      const hrs = Math.floor(totalSec / 3600);
      const mins = Math.floor((totalSec % 3600) / 60);
      const secs = totalSec % 60;
      const ts = hrs > 0 ? (String(hrs).padStart(2,'0')+':'+String(mins).padStart(2,'0')+':'+String(secs).padStart(2,'0')) : (String(mins).padStart(2,'0')+':'+String(secs).padStart(2,'0'));
      const txt = String(s.text || '');

      const isMatch = !q || txt.toLowerCase().includes(q) || ts.includes(q);
      if(isMatch){
        matchCount++;
        let dispTxt = esc(txt);
        if(q){
          let ltxt = txt.toLowerCase(), lq = q.toLowerCase();
          let idx = 0, out = '';
          while(true){
            let next = ltxt.indexOf(lq, idx);
            if(next === -1){ out += esc(txt.slice(idx)); break; }
            out += esc(txt.slice(idx, next)) + '<mark>' + esc(txt.slice(next, next + q.length)) + '</mark>';
            idx = next + q.length;
          }
          dispTxt = out;
        }
        h += '<div class="stt-line' + (q ? ' highlight' : '') + '">';
        h += '<span class="stt-ts" title="클릭하여 타임스탬프 복사" data-ts="' + esc(ts) + '" onclick="copyTimestamp(this.dataset.ts)">[' + esc(ts) + ']</span>';
        h += '<span class="stt-text">' + dispTxt + '</span>';
        h += '</div>';
      }
    });
  } else if(rawText) {
    const lines = rawText.split('\\n');
    totalCount = lines.length;
    lines.forEach(line => {
      const trimmed = line.trim();
      if(!trimmed) return;
      const isMatch = !q || trimmed.toLowerCase().includes(q);
      if(isMatch){
        matchCount++;
        let dispTxt = esc(trimmed);
        if(q){
          let ltxt = trimmed.toLowerCase(), lq = q.toLowerCase();
          let idx = 0, out = '';
          while(true){
            let next = ltxt.indexOf(lq, idx);
            if(next === -1){ out += esc(trimmed.slice(idx)); break; }
            out += esc(trimmed.slice(idx, next)) + '<mark>' + esc(trimmed.slice(next, next + q.length)) + '</mark>';
            idx = next + q.length;
          }
          dispTxt = out;
        }
        h += '<div class="stt-line' + (q ? ' highlight' : '') + '"><span class="stt-text">' + dispTxt + '</span></div>';
      }
    });
  } else {
    h = '<p class="hint">저장된 전사 내용이 없습니다.</p>';
  }

  body.innerHTML = h;
  if(countEl){
    if(q){
      countEl.textContent = matchCount.toLocaleString() + ' / ' + totalCount.toLocaleString() + '개 일치';
    } else {
      countEl.textContent = totalCount > 0 ? totalCount.toLocaleString() + '개 항목' : '';
    }
  }
}

function filterSttLines(val){
  curSttFilter = val;
  renderSttLines();
}

function copyTimestamp(ts){
  if(navigator.clipboard && navigator.clipboard.writeText){
    navigator.clipboard.writeText('[' + ts + ']').catch(()=>{});
  }
}

function copySttText(withTs){
  let segs = dc.transcript_segments || (dc.meta && dc.meta.transcript_segments) || [];
  let out = '';
  if(segs && segs.length > 0){
    const lines = segs.map(s => {
      const startF = s.start_sec != null ? s.start_sec : (s.start != null ? s.start : 0.0);
      const totalSec = Math.floor(startF);
      const hrs = Math.floor(totalSec / 3600);
      const mins = Math.floor((totalSec % 3600) / 60);
      const secs = totalSec % 60;
      const ts = hrs > 0 ? (String(hrs).padStart(2,'0')+':'+String(mins).padStart(2,'0')+':'+String(secs).padStart(2,'0')) : (String(mins).padStart(2,'0')+':'+String(secs).padStart(2,'0'));
      const txt = String(s.text || '').trim();
      return withTs ? ('[' + ts + '] ' + txt) : txt;
    });
    out = lines.join('\\n');
  } else if(dc.stt_transcript) {
    out = dc.stt_transcript;
    if(!withTs){
      out = out.replace(/^\\[\\d{1,2}:\\d{2}(?::\\d{2})?\\]\\s*/gm, '');
    }
  }
  if(out){
    navigator.clipboard.writeText(out).then(()=>{
      alert('클립보드에 전사 내용이 복사되었습니다.');
    }).catch(()=>{
      alert('복사에 실패했습니다.');
    });
  }
}

if(typeof window !== 'undefined' && typeof window.addEventListener === 'function'){
  window.addEventListener('keydown', function(e){
    if(e.key === 'Escape'){
      const m = document.getElementById('sttmodal');
      if(m && m.classList.contains('open')){
        e.stopPropagation();
        closeSttReader();
      }
    }
  });
}
if(typeof window.gtag === 'function' && dc && dc.id){
  try{
    window.gtag('event', 'select_content', {
      content_type: 'shared_document',
      item_id: dc.id
    });
  }catch(_){}
}
</script></body></html>
"""


def render_ga_tag(measurement_id: str, doc_id: str = "") -> str:
    """Google Analytics 4 (GA4 / gtag.js) 태그 스니펫을 생성한다.

    측정 ID가 없거나 유효하지 않으면 빈 문자열을 반환한다.
    URL 쿼리 파라미터(?t=..., ?s=...) 유출을 방지하기 위해 page_location을
    origin + pathname (또는 /p/<doc_id>)으로 정제하여 전송한다."""
    import re

    cleaned_id = str(measurement_id or "").strip()
    if not cleaned_id or not re.fullmatch(r"^[A-Za-z0-9_-]+$", cleaned_id):
        return ""
    clean_doc_id = str(doc_id or "").strip()
    if clean_doc_id:
        loc_expr = f"window.location.origin + '/p/{clean_doc_id}'"
    else:
        loc_expr = "window.location.origin + window.location.pathname"
    return (
        f'<!-- Google Analytics (GA4) -->\n'
        f'<script async src="https://www.googletagmanager.com/gtag/js?id={cleaned_id}"></script>\n'
        f'<script>\n'
        f'  window.dataLayer = window.dataLayer || [];\n'
        f'  function gtag(){{dataLayer.push(arguments);}}\n'
        f'  gtag("js", new Date());\n'
        f'  gtag("config", "{cleaned_id}", {{\n'
        f'    page_location: {loc_expr},\n'
        f'    cookie_domain: window.location.hostname,\n'
        f'    cookie_flags: "SameSite=Lax;Secure"\n'
        f'  }});\n'
        f'</script>'
    )


def shared_html(doc: dict, settings: Any = None) -> str:
    """공유 문서 1개를 임베드한 경량 읽기 페이지 HTML. doc = document_detail() 결과.

    문서 데이터를 JSON 으로 <script> 에 임베드한다 — `</script>`·`<` 등이 스크립트를
    조기 종료/주입하지 못하게 HTML 특수문자를 \\uXXXX 로 이스케이프(스크랩 본문 유래)."""
    import json as _json

    if settings is None:
        from .config import get_settings

        s = get_settings()
    else:
        s = settings

    ga_id = getattr(
        s,
        "effective_ga_measurement_id",
        getattr(s, "ga_measurement_id", ""),
    )
    doc_id = str((doc or {}).get("id", "") or "").strip()
    ga_tag = render_ga_tag(ga_id, doc_id=doc_id)

    data = _json.dumps(doc, ensure_ascii=False)
    data = data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    title = (doc.get("title") or "공유 문서").replace("<", "").replace(">", "")
    return (
        _SHARED_HTML.replace("__DATA__", data)
        .replace("__TITLE__", title)
        .replace("<!-- __GA_TAG__ -->", ga_tag)
    )


def render_graph_html(settings: Any = None) -> str:
    """Settings 의 저장소 변수 및 GA 설정을 반영하여 완성된 그래프 HTML 을 반환한다."""
    if settings is None:
        from .config import get_settings

        s = get_settings()
    else:
        s = settings
    repo = getattr(
        s,
        "effective_github_repository",
        getattr(s, "github_repository", "fofwisdom/claire-bible"),
    )
    base_url = getattr(
        s,
        "effective_source_base_url",
        getattr(s, "source_base_url", f"https://github.com/{repo}"),
    )
    if not base_url:
        base_url = f"https://github.com/{repo}"
    ga_id = getattr(
        s,
        "effective_ga_measurement_id",
        getattr(s, "ga_measurement_id", ""),
    )
    ga_tag = render_ga_tag(ga_id)
    return (
        GRAPH_HTML.replace("__SOURCE_BASE_URL__", base_url)
        .replace("__GITHUB_REPOSITORY__", repo)
        .replace("<!-- __GA_TAG__ -->", ga_tag)
    )

