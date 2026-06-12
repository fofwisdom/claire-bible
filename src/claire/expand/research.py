"""맥락 확장 조사 — 읽던 맥락에 맞춰 키워드/문장을 조사해 그래프를 확장한다.

사용자 흐름: 그래프 UI 에서 노드/문서를 읽다가 더 알고 싶은 키워드·문장을 입력
→ provider.research(웹 검색 grounding)가 **맥락 내 의미로 고정**해 조사
→ provider.judge_research(별도 호출)가 맥락 일치도(relevance)·품질(quality)을 채점
→ 두 임계 모두 통과할 때만 일반 ingest 파이프라인(추출→해소→관계→vault)으로 적재.

다의어 방어(사용자 요구)는 이중: ① 조사 프롬프트가 맥락 내 해석을 강제하고 맥락
불일치 시 INSUFFICIENT 선언, ② 독립 판정자가 relevance 를 채점해 게이트. 판정
실패는 0점(fail-closed) — 불확실하면 추가하지 않는다(보고서는 보여주되 보류).
"""

from __future__ import annotations

import sqlite3

from ..store import db as dbm

# 그래프 추가 게이트 임계. relevance(맥락 일치)가 quality 보다 엄격 — 오염이 빈약보다
# 해롭다(잘못 들어간 다의어 노드는 이후 모든 검색/종합을 오도).
RELEVANCE_MIN = 0.7
QUALITY_MIN = 0.6


def build_context(conn: sqlite3.Connection, node_id: str | None = None,
                  doc_id: str | None = None) -> tuple[str, str]:
    """사용자가 보고 있던 것(선택 노드 + 활성 문서)을 조사용 맥락 텍스트로.

    반환: (context, focus) — focus 는 맥락의 대표 이름(보고서 헤더·UI 표기용).
    """
    parts: list[str] = []
    focus = ""
    if node_id:
        from ..graphview import synthesis_context

        ctx, names = synthesis_context(conn, [node_id])
        if ctx:
            parts.append(ctx)
            focus = names[0]
    if doc_id:
        row = dbm.get_document_row(conn, doc_id)
        if row:
            summ = dbm.latest_extraction_summary(conn, doc_id) or ""
            detail = dbm.get_document_detail(conn, doc_id) or ""
            parts.append(f"읽고 있던 문서: {row['title'] or '(제목 없음)'}\n"
                         f"요약: {summ}\n{detail[:2000]}")
            focus = focus or (row["title"] or "")
    return "\n\n".join(parts), focus


def contextual_research(settings, provider, *, query: str,  # noqa: ANN001
                        node_id: str | None = None,
                        doc_id: str | None = None) -> dict:
    """조사→판정→(통과 시)적재 전체 흐름. 블로킹 — 호출측에서 스레드 오프로드.

    반환 dict: {query, context_focus, report, sources, relevance, quality,
    interpretation, reason, added, verdict, ingest?} — 게이트 미달이어도 보고서는
    반환해 사용자가 읽을 수 있게 한다(추가만 보류).
    """
    query = (query or "").strip()
    if not query:
        return {"error": "조사할 키워드/문장이 비었습니다"}
    if not (hasattr(provider, "research") and hasattr(provider, "judge_research")):
        return {"error": "이 provider 는 맥락 조사를 지원하지 않습니다"}

    conn = dbm.connect(settings.db_file)
    dbm.init_db(conn)
    try:
        context, focus = build_context(conn, node_id, doc_id)
        if not context:
            return {"error": "맥락이 없습니다 — 노드를 선택하거나 문서를 연 뒤 조사하세요"}

        r = provider.research(query, context)
        report = (r.get("report") or "").strip()
        sources = r.get("sources") or []
        base = {"query": query, "context_focus": focus, "report": report,
                "sources": sources, "added": False}
        if not report or report.splitlines()[0].strip().upper().startswith("INSUFFICIENT"):
            base["verdict"] = "조사 불충분 — 맥락과 일치하는 신뢰할 만한 자료를 찾지 못함"
            base.update({"relevance": 0.0, "quality": 0.0, "interpretation": "",
                         "reason": report or "빈 응답"})
            return base

        judge = provider.judge_research(query, context, report)
        rel = float(judge.get("relevance") or 0.0)
        qual = float(judge.get("quality") or 0.0)
        base.update({"relevance": rel, "quality": qual,
                     "interpretation": judge.get("interpretation", ""),
                     "reason": judge.get("reason", "")})
        if rel < RELEVANCE_MIN or qual < QUALITY_MIN:
            base["verdict"] = (f"보류 — 게이트 미달(맥락일치 {rel:.2f}/{RELEVANCE_MIN}, "
                               f"품질 {qual:.2f}/{QUALITY_MIN}). 그래프에 추가하지 않음")
            return base

        # 게이트 통과 → 일반 파이프라인으로 적재(추출·해소·관계·vault·inbox 보존 공유).
        # 문서 본문에 맥락(focus)·해석을 함께 넣어 추출이 원 맥락 엔티티와 자연 연결되게
        # 하고, 출처 URL 목록도 본문에 보존한다(research 문서 자체엔 단일 url 이 없음).
        text = _research_doc_text(query, focus, judge.get("interpretation", ""),
                                  report, sources)
        ing = _ingest_report_doc(settings, provider, conn, query, text)
        base["added"] = ing.get("error") is None
        base["ingest"] = ing
        base["verdict"] = ("그래프에 추가됨" if base["added"]
                           else f"적재 실패: {ing.get('error')}")
        return base
    finally:
        conn.close()


def _research_doc_text(query: str, focus: str, interpretation: str,
                       report: str, sources: list[dict]) -> str:
    head = [f"[맥락 조사] {query}"]
    if focus:
        head.append(f"조사 맥락: {focus} 을(를) 읽던 중 확장 조사")
    if interpretation:
        head.append(f"맥락 내 해석: {interpretation}")
    body = "\n".join(head) + "\n\n" + report
    if sources:
        body += "\n\n출처:\n" + "\n".join(
            f"- {s.get('title') or s.get('url')}: {s.get('url')}" for s in sources[:10])
    return body


def _ingest_report_doc(settings, provider, conn: sqlite3.Connection,  # noqa: ANN001
                       query: str, text: str) -> dict:
    from ..ingest.normalize import content_hash
    from ..ingest.pipeline import ingest as run_ingest
    from ..ontology.base import Document
    from ..store.vectors import make_vector_store

    doc = Document(
        title=f"조사: {query[:60]}",
        raw_text=text,
        source_type="research",
        content_hash=content_hash(text),
    )
    vstore = make_vector_store(conn, settings.vector_backend)
    # payload 에 보고서 전문을 보존(Layer-1 데이터 보존) — 적재 실패 시 raw_inbox 에서
    # replay 가능(텍스트로 재적재돼도 지식은 유지됨).
    rep = run_ingest(
        text, conn=conn, provider=provider, vstore=vstore,
        vault_dir=settings.vault_dir, data_dir=settings.data_dir,
        expand_max=0, source="research", fetch_fn=lambda _p: doc,
    )
    return {
        "document_id": rep.document_id, "error": rep.error,
        "duplicate": rep.duplicate, "summary": rep.summary,
        "entities_created": rep.entities_created,
        "entities_linked": rep.entities_linked,
        "relations_added": rep.relations_added,
        "new_entity_names": rep.new_entity_names,
        "linked_entity_names": rep.linked_entity_names,
    }
