"""동일 주제 출처 병합(same_subject) — docs/implementation/EXPANSION.md §3. 네트워크 없이 mock provider 로 검증.

자동 확장은 병합하지 않는다(소유자 결정, 2026-11-18): 모드 "off"(기본)는 판정을 버리고, 모드 "shadow"는 판정을 same_subject_shadow 에 기록한다. 두 모드 모두 자식을 독립 문서로 적재한다. 병합 함수(merge_source_into_document)는 부르는 코드가 없지만 남아 있으므로 직접 부르는 테스트로 검증한다.

mock provider 훅(judge_gate, extract/provider.py):
  - report/context 에 '무관' → 저품질(게이트 거절)
  - report/context 에 '별개주제' → same_subject=False
  - 트리거 없으면 same_subject=True(기본)
"""

from __future__ import annotations

from claire.config import Settings
from claire.expand.proposal import find_candidates
from claire.ingest import pipeline as pipemod
from claire.ingest import service as svcmod
from claire.ingest.service import IngestService
from claire.ontology.base import Document
from claire.store import db as dbm

PARENT = "https://parent.example/post"
SAME_SUBJECT = "https://same.example/repo"     # same_subject=True 기본값 → 병합 대상
OTHER_SUBJECT = "https://other.example/post"   # '별개주제' 훅 → 독립 문서
LOW_QUALITY = "https://low.example/post"       # '무관' 훅 → 게이트 거절
EXISTING_DUP = "https://existing.example/already-ingested"  # SAME_SUBJECT 와 내용(content_hash) 동일 — 이미 독립 문서로 존재


def _patch_fetch(monkeypatch, fn):
    monkeypatch.setattr(svcmod, "default_fetch", fn)
    monkeypatch.setattr(pipemod, "default_fetch", fn)


def _mem(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAIRE_DB_PATH", str(tmp_path / "e.db"))
    monkeypatch.setenv("CLAIRE_VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("CLAIRE_PROVIDER", "mock")
    return Settings()


def _doc(url, text, *, links=None, title=None):
    meta = {}
    if links is not None:
        meta["links"] = links
    return Document(url=url, canonical_url=url, title=title or url.rsplit("/", 1)[-1],
                    raw_text=text, source_type="web", content_hash=str(hash(text)),
                    meta=meta)


def _fetch(payload):
    body = "유익하고 구체적인 본문 내용입니다. " * 20
    if payload == PARENT:
        return _doc(PARENT, "부모 문서 본문 " * 30,
                    links=[SAME_SUBJECT, OTHER_SUBJECT, LOW_QUALITY])
    if payload == SAME_SUBJECT:
        return _doc(SAME_SUBJECT, "그 프로젝트의 github 저장소 본문. " + body,
                    title="같은 주제의 github 저장소")
    if payload == OTHER_SUBJECT:
        return _doc(OTHER_SUBJECT, "별개주제 — 부모 글이 언급한 완전히 다른 프로젝트. " + body,
                    title="별개 프로젝트")
    if payload == LOW_QUALITY:
        return _doc(LOW_QUALITY, "무관한 다른 주제의 내용. " * 20)
    if payload == EXISTING_DUP:
        # SAME_SUBJECT 와 완전히 동일한 본문(= 동일 content_hash) — URL 만 다른 "같은 자료의
        # 다른 입구"(실사고: twclid 같은 트래킹 파라미터로 canonicalize_url 이 못 거른 경우).
        return _doc(EXISTING_DUP, "그 프로젝트의 github 저장소 본문. " + body,
                    title="같은 주제의 github 저장소")
    raise RuntimeError(f"unexpected fetch {payload}")


def _svc(monkeypatch, tmp_path, mode="off"):
    s = _mem(monkeypatch, tmp_path).model_copy(update={"same_subject_merge": mode})
    _patch_fetch(monkeypatch, _fetch)
    return s, IngestService(s)


def _merge(s, svc, parent_id, url):
    """Call the merge function as the old expansion branch did (no caller in the service now)."""
    from claire.store.vectors import make_vector_store

    conn = dbm.connect(s.db_file); dbm.init_db(conn)
    try:
        vstore = make_vector_store(conn, s.vector_backend, kb_id=svc.kb_id)
        parent_full = dbm.get_document(conn, parent_id, kb_id=svc.kb_id)
        return pipemod.merge_source_into_document(
            conn, svc.provider, vstore, parent_full, _fetch(url),
            data_dir=s.data_dir, format=s.render_format, kb_id=svc.kb_id, settings=s)
    finally:
        conn.close()


def test_off_mode_stores_each_child_as_its_own_document(monkeypatch, tmp_path):
    s, svc = _svc(monkeypatch, tmp_path)
    assert s.same_subject_merge == "off"
    parent = svc.ingest(PARENT, source="cli", expand_max=0)
    res = svc.expand_document(parent.document_id)

    assert "merged" not in res
    assert res["stored"] == 2 and res["shadowed"] == 0
    followed = {f["url"]: f for f in res["followed"]}
    assert followed[SAME_SUBJECT]["stored"] is True
    assert "same_subject" not in followed[SAME_SUBJECT]

    conn = dbm.connect(s.db_file); dbm.init_db(conn)
    # A same-subject child is a new document, and the parent does not change.
    assert dbm.find_document_by_canonical_url(conn, SAME_SUBJECT) is not None
    assert dbm.get_document_extra_sources(conn, parent.document_id) == []
    assert "github 저장소 본문" not in dbm.get_document_row(conn, parent.document_id)["raw_text"]
    assert dbm.list_same_subject_shadow(conn) == []
    # The children use the new source name and do not go into the queue again (depth 1).
    sources = {r["source"] for r in conn.execute("SELECT source FROM raw_inbox WHERE payload IN (?, ?)", (SAME_SUBJECT, OTHER_SUBJECT))}
    assert sources == {f"expand:{parent.document_id}"}
    assert dbm.pending_expand(conn) == []
    conn.close()


def test_shadow_mode_records_the_judgement_and_stores_each_child(monkeypatch, tmp_path):
    s, svc = _svc(monkeypatch, tmp_path, mode="shadow")
    parent = svc.ingest(PARENT, source="cli", expand_max=0)
    res = svc.expand_document(parent.document_id)

    assert res["stored"] == 2 and res["shadowed"] == 2
    followed = {f["url"]: f for f in res["followed"]}
    assert followed[SAME_SUBJECT]["same_subject"] is True
    assert followed[OTHER_SUBJECT]["same_subject"] is False

    conn = dbm.connect(s.db_file); dbm.init_db(conn)
    rows = {r["child_url"]: r for r in dbm.list_same_subject_shadow(conn)}
    assert set(rows) == {SAME_SUBJECT, OTHER_SUBJECT}  # the gate refused LOW_QUALITY: no record
    same = rows[SAME_SUBJECT]
    assert same["same_subject"] == 1 and same["parent_document_id"] == parent.document_id
    assert same["child_document_id"] == dbm.find_document_by_canonical_url(conn, SAME_SUBJECT)
    assert same["relevance"] > 0 and same["quality"] > 0
    assert rows[OTHER_SUBJECT]["same_subject"] == 0
    # Shadow mode does not merge.
    assert dbm.get_document_extra_sources(conn, parent.document_id) == []
    conn.close()


def test_merge_mode_value_is_refused(monkeypatch, tmp_path):
    import pytest
    from pydantic import ValidationError

    from claire.ingest_limit_manager import IngestLimitManager

    with pytest.raises(ValidationError, match="same_subject_merge must be 'off' or 'shadow'"):
        _mem(monkeypatch, tmp_path).model_validate({"same_subject_merge": "on"})
    manager = IngestLimitManager(data_dir=tmp_path / "limits", auto_migrate=False)
    with pytest.raises(ValueError, match="same_subject_merge must be one of"):
        manager.update_config({"limits": {"same_subject_merge": "on"}})
    assert manager.update_config({"limits": {"same_subject_merge": "shadow"}})["limits"]["same_subject_merge"] == "shadow"


def test_low_quality_still_skipped_before_subject_check(monkeypatch, tmp_path):
    s, svc = _svc(monkeypatch, tmp_path, mode="shadow")
    parent = svc.ingest(PARENT, source="cli", expand_max=0)
    res = svc.expand_document(parent.document_id)

    followed = {f["url"]: f for f in res["followed"]}
    assert followed[LOW_QUALITY]["stored"] is False
    conn = dbm.connect(s.db_file); dbm.init_db(conn)
    assert dbm.find_document_by_canonical_url(conn, LOW_QUALITY) is None
    assert dbm.find_document_by_extra_source(conn, LOW_QUALITY) is None
    conn.close()


def test_merge_function_puts_the_child_into_the_parent(monkeypatch, tmp_path):
    s, svc = _svc(monkeypatch, tmp_path)
    parent = svc.ingest(PARENT, source="cli", expand_max=0)
    m = _merge(s, svc, parent.document_id, SAME_SUBJECT)

    assert m["merged"] is True
    conn = dbm.connect(s.db_file); dbm.init_db(conn)
    assert dbm.find_document_by_canonical_url(conn, SAME_SUBJECT) is None
    assert dbm.find_document_by_extra_source(conn, SAME_SUBJECT) == parent.document_id
    assert "github 저장소 본문" in dbm.get_document_row(conn, parent.document_id)["raw_text"]
    conn.close()


def test_merge_failure_rolls_back_to_pre_merge_snapshot(monkeypatch, tmp_path):
    s, svc = _svc(monkeypatch, tmp_path)
    parent = svc.ingest(PARENT, source="cli", expand_max=0)
    conn = dbm.connect(s.db_file); dbm.init_db(conn)
    original_text = dbm.get_document_row(conn, parent.document_id)["raw_text"]
    conn.close()

    def boom(doc, ontology_block=None):
        raise RuntimeError("simulated extract failure")

    monkeypatch.setattr(svc.provider, "extract", boom)
    m = _merge(s, svc, parent.document_id, SAME_SUBJECT)

    assert m["merged"] is False and m["error"]
    conn = dbm.connect(s.db_file); dbm.init_db(conn)
    row = dbm.get_document_row(conn, parent.document_id)
    assert row["raw_text"] == original_text  # 병합 시도 이전 상태로 복원
    assert dbm.get_document_extra_sources(conn, parent.document_id) == []
    conn.close()


def test_merged_source_url_not_reproposed_as_candidate(monkeypatch, tmp_path):
    s, svc = _svc(monkeypatch, tmp_path)
    parent = svc.ingest(PARENT, source="cli", expand_max=0)
    _merge(s, svc, parent.document_id, SAME_SUBJECT)  # 예전에 병합된 출처(데이터에 남아 있을 수 있음)

    conn = dbm.connect(s.db_file); dbm.init_db(conn)
    other_parent_doc = _doc(
        "https://another-parent.example/post", "다른 부모 문서",
        links=[SAME_SUBJECT])
    cands = find_candidates(conn, other_parent_doc, limit=5)
    assert SAME_SUBJECT not in cands  # 이미 다른 문서에 병합 출처로 흡수됨
    conn.close()


def test_merge_function_refuses_a_child_that_exists_as_a_document(monkeypatch, tmp_path):
    """2026-07-14 실사고 재현: child 가 canonicalize_url 로 못 거른 URL 변형 등으로 이미 독립 문서로 존재하면(content_hash 일치) 병합하지 않는다."""
    s, svc = _svc(monkeypatch, tmp_path)
    existing = svc.ingest(EXISTING_DUP, source="cli", expand_max=0)
    assert not existing.error and not existing.duplicate
    parent = svc.ingest(PARENT, source="cli", expand_max=0)
    m = _merge(s, svc, parent.document_id, SAME_SUBJECT)

    assert m["merged"] is False and "duplicate" in m["error"]
    conn = dbm.connect(s.db_file); dbm.init_db(conn)
    assert "github 저장소 본문" not in dbm.get_document_row(conn, parent.document_id)["raw_text"]
    assert dbm.get_document_extra_sources(conn, parent.document_id) == []
    assert dbm.find_document_by_canonical_url(conn, EXISTING_DUP) == existing.document_id
    conn.close()
