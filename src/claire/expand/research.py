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
                        doc_id: str | None = None,
                        progress=None) -> dict:  # noqa: ANN001
    """조사→판정→(통과 시)적재 전체 흐름. 블로킹 — 호출측에서 스레드 오프로드.

    progress: Callable[[dict], None] | None — 단계 이벤트 {stage, msg} 를 실시간 전달
    (API 가 NDJSON 스트림으로 UI 에 흘림). provider 내부의 rate limit 대기/재시도도
    스레드-로컬 콜백(set_progress_callback)으로 같은 채널에 합류한다.

    반환 dict: {query, context_focus, report, sources, relevance, quality,
    interpretation, reason, added, verdict, ingest?} — 게이트 미달이어도 보고서는
    반환해 사용자가 읽을 수 있게 한다(추가만 보류).
    """
    from ..extract.provider import set_progress_callback

    def _p(stage: str, msg: str) -> None:
        if progress:
            try:
                progress({"stage": stage, "msg": msg})
            except Exception:  # noqa: BLE001
                pass

    query = (query or "").strip()
    if not query:
        return {"error": "조사할 키워드/문장이 비었습니다"}
    if not (hasattr(provider, "research") and hasattr(provider, "judge_research")):
        return {"error": "이 provider 는 맥락 조사를 지원하지 않습니다"}

    conn = dbm.connect(settings.db_file)
    dbm.init_db(conn)
    set_progress_callback(lambda m: _p("llm", m))  # rate limit 대기 등 내부 이벤트
    try:
        context, focus = build_context(conn, node_id, doc_id)
        if not context:
            return {"error": "맥락이 없습니다 — 노드를 선택하거나 문서를 연 뒤 조사하세요"}
        _p("context", f"맥락 구성 — {focus} ({len(context)}자)")

        _p("research", "웹 검색 + 조사 중(Gemini grounding)…")
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
        _p("research", f"조사 완료 — 보고서 {len(report)}자 · 출처 {len(sources)}개")

        _p("judge", "판정 중 — 맥락 일치·품질 채점(별도 LLM 호출)…")
        judge = provider.judge_research(query, context, report)
        rel = float(judge.get("relevance") or 0.0)
        qual = float(judge.get("quality") or 0.0)
        base.update({"relevance": rel, "quality": qual,
                     "interpretation": judge.get("interpretation", ""),
                     "reason": judge.get("reason", "")})
        _p("judge", f"판정 완료 — 맥락일치 {rel:.2f} · 품질 {qual:.2f}")
        if rel < RELEVANCE_MIN or qual < QUALITY_MIN:
            base["verdict"] = (f"보류 — 게이트 미달(맥락일치 {rel:.2f}/{RELEVANCE_MIN}, "
                               f"품질 {qual:.2f}/{QUALITY_MIN}). 그래프에 추가하지 않음")
            return base

        # 게이트 통과 → 일반 파이프라인으로 적재(추출·해소·관계·vault·inbox 보존 공유).
        # 문서 본문에 맥락(focus)·해석을 함께 넣어 추출이 원 맥락 엔티티와 자연 연결되게
        # 하고, 출처 URL 목록도 본문에 보존한다(research 문서 자체엔 단일 url 이 없음).
        _p("ingest", "게이트 통과 — 그래프 적재 중(추출→엔티티 해소→관계→임베딩)…")
        text = _research_doc_text(query, focus, judge.get("interpretation", ""),
                                  report, sources)
        # 적재 파이프라인의 세부 단계 emit("원문 가져오는 중" 등)은 웹 적재(ingest-stream)
        # UX 용이라 research 진행 스트림엔 노이즈 — research 는 위/아래 자체 ingest 메시지로
        # 표시하므로 여기선 콜백을 꺼 그 메시지가 끼어들지 않게 한다(진행 이벤트 계약 유지).
        set_progress_callback(None)
        ing = _ingest_report_doc(settings, provider, conn, query, text)
        base["added"] = ing.get("error") is None
        base["ingest"] = ing
        base["verdict"] = ("그래프에 추가됨" if base["added"]
                           else f"적재 실패: {ing.get('error')}")
        if base["added"]:
            _p("ingest", f"적재 완료 — 신규 {ing['entities_created']} · "
                         f"기존연결 {ing['entities_linked']} · 관계 {ing['relations_added']}")
        return base
    finally:
        set_progress_callback(None)
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
                       query: str, text: str, *, source_type: str = "research",
                       source: str = "research", title: str | None = None) -> dict:
    from ..ingest.normalize import content_hash
    from ..ingest.pipeline import ingest as run_ingest
    from ..ontology.base import Document
    from ..store.vectors import make_vector_store

    doc = Document(
        title=title or f"조사: {query[:60]}",
        raw_text=text,
        source_type=source_type,
        content_hash=content_hash(text),
    )
    vstore = make_vector_store(conn, settings.vector_backend)
    # payload 에 보고서 전문을 보존(Layer-1 데이터 보존) — 적재 실패 시 raw_inbox 에서
    # replay 가능(텍스트로 재적재돼도 지식은 유지됨).
    rep = run_ingest(
        text, conn=conn, provider=provider, vstore=vstore,
        vault_dir=settings.vault_dir, data_dir=settings.data_dir,
        expand_max=0, source=source, fetch_fn=lambda _p: doc,
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


# --- 종합(synthesis) → 조사계획 → 다중 웹조사 → 문서합성 → 적재 -----------------
#
# 사용자 흐름(2026-07-21 요구): 그래프 UI 에서 "🧩 종합"으로 여러 노드를 요약한
# 뒤, 그 답변을 더 깊고 정확하게 만들 하위 조사질문 계획을 세워 사용자에게
# 보여주고(비용이 드는 실제 웹조사 전에 확인/수정 기회) → 승인된 질문들로 위
# contextual_research() 와 같은 조사(grounding)+판정 게이트를 하나씩 통과시킨 뒤
# → 통과분을 하나의 고품질 문서로 합성 → 그래프에 적재.


def plan_research_from_synthesis(settings, provider, *, entity_ids: list[str],  # noqa: ANN001
                                 synth_answer: str) -> dict:
    """종합 결과를 바탕으로 하위 조사질문 계획(단일 LLM 호출, 웹검색·적재 없음).

    반환: {questions:[{question,rationale}], entities:[name,...]} 또는 {error}.
    """
    if not entity_ids:
        return {"error": "entity_ids required"}
    if not hasattr(provider, "plan_research"):
        return {"error": "이 provider 는 조사계획을 지원하지 않습니다"}

    conn = dbm.connect(settings.db_file)
    dbm.init_db(conn)
    try:
        from ..graphview import synthesis_context

        context, names = synthesis_context(conn, entity_ids)
        if not context:
            return {"error": "선택된 노드를 찾을 수 없습니다"}
        n = max(1, min(settings.research_plan_max, 10))  # 안전 상한(비용 폭주 방지)
        questions = provider.plan_research(context, synth_answer or "", n)
        return {"questions": questions, "entities": names}
    finally:
        conn.close()


def run_planned_research(settings, provider, *, entity_ids: list[str],  # noqa: ANN001
                         synth_answer: str, questions: list[str],
                         progress=None) -> dict:
    """승인된 하위질문들을 조사→판정 게이트→(통과분만 모아) 합성→적재.

    일부 질문이 게이트 탈락해도 나머지 통과분으로 진행한다(오염된 하나 때문에
    전체를 버리지 않음). 전부 탈락하면 실패 반환(added=False, error).

    progress: contextual_research() 와 동일한 Callable[[dict], None] | None —
    {stage, msg} 단계 이벤트(NDJSON 스트림용). provider 내부 rate-limit 대기도
    스레드-로컬 콜백으로 같은 채널에 합류한다.

    반환: {questions_total, passed, rejected, document, added, ingest?, error?}
    """
    from ..extract.provider import set_progress_callback

    def _p(stage: str, msg: str) -> None:
        if progress:
            try:
                progress({"stage": stage, "msg": msg})
            except Exception:  # noqa: BLE001
                pass

    entity_ids = entity_ids or []
    questions = [q.strip() for q in (questions or []) if q.strip()]
    if not entity_ids or not questions:
        return {"error": "entity_ids/questions required", "added": False}
    if not (hasattr(provider, "research") and hasattr(provider, "judge_research")
            and hasattr(provider, "compose_document")):
        return {"error": "이 provider 는 다단계 조사를 지원하지 않습니다", "added": False}

    conn = dbm.connect(settings.db_file)
    dbm.init_db(conn)
    set_progress_callback(lambda m: _p("llm", m))
    try:
        from ..graphview import synthesis_context

        context, names = synthesis_context(conn, entity_ids)
        if not context:
            return {"error": "선택된 노드를 찾을 수 없습니다", "added": False}

        passed: list[dict] = []
        rejected: list[dict] = []
        total = len(questions)
        for i, q in enumerate(questions, 1):
            _p("research", f"({i}/{total}) 조사 중: {q}")
            r = provider.research(q, context)
            report = (r.get("report") or "").strip()
            sources = r.get("sources") or []
            if not report or report.splitlines()[0].strip().upper().startswith("INSUFFICIENT"):
                rejected.append({"question": q, "reason": "조사 불충분 — 신뢰할 자료를 찾지 못함"})
                continue
            judge = provider.judge_research(q, context, report)
            rel = float(judge.get("relevance") or 0.0)
            qual = float(judge.get("quality") or 0.0)
            if rel < RELEVANCE_MIN or qual < QUALITY_MIN:
                rejected.append({"question": q,
                                 "reason": f"게이트 미달(맥락일치 {rel:.2f}/품질 {qual:.2f})"})
                continue
            passed.append({"question": q, "report": report, "sources": sources,
                           "relevance": rel, "quality": qual})
            _p("research", f"({i}/{total}) 통과 — 맥락일치 {rel:.2f} · 품질 {qual:.2f}")

        base = {"questions_total": total, "passed": passed, "rejected": rejected,
               "added": False}
        if not passed:
            base["error"] = "모든 하위질문이 게이트를 통과하지 못했습니다"
            return base

        _p("compose", f"통과 {len(passed)}건 종합 문서 작성 중…")
        composed = provider.compose_document(context, synth_answer or "", passed)
        base["document"] = composed

        all_sources: list[dict] = []
        seen: set[str] = set()
        for r in passed:
            for s in r.get("sources", []):
                u = s.get("url")
                if u and u not in seen:
                    seen.add(u)
                    all_sources.append(s)

        text = _compose_doc_text(composed, all_sources)
        # ingest 세부 단계 메시지가 끼어들지 않게(contextual_research 와 동일 관례).
        set_progress_callback(None)
        _p("ingest", "게이트 통과 — 그래프 적재 중(추출→엔티티 해소→관계→임베딩)…")
        ing = _ingest_report_doc(
            settings, provider, conn, composed.get("title") or "조사 합성", text,
            source_type="synthesis_research", source="synthesis-research",
            title=composed.get("title"),
        )
        base["added"] = ing.get("error") is None
        base["ingest"] = ing
        if base["added"]:
            _p("ingest", f"적재 완료 — 신규 {ing['entities_created']} · "
                         f"기존연결 {ing['entities_linked']} · 관계 {ing['relations_added']}")
        else:
            base["error"] = f"적재 실패: {ing.get('error')}"
        return base
    finally:
        set_progress_callback(None)
        conn.close()


def _compose_doc_text(composed: dict, sources: list[dict]) -> str:
    body = composed.get("body") or ""
    if sources:
        body += "\n\n출처:\n" + "\n".join(
            f"- {s.get('title') or s.get('url')}: {s.get('url')}" for s in sources[:20])
    return body
