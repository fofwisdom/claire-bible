"""1홉 병합(same_subject) — ONEHOP_MERGE_DESIGN.md. 네트워크 없이 mock provider 로 검증.

mock provider 훅(judge_research, extract/provider.py):
  - report/context 에 '무관' → 저품질(게이트 거절, 기존 훅)
  - report/context 에 '별개주제' → same_subject=False(독립 문서로, 기존 동작 유지)
  - 트리거 없으면 same_subject=True(기본) → 새 문서 대신 부모에 병합
"""

from __future__ import annotations

from claire.config import Settings
from claire.ontology.base import Document
from claire.store import db as dbm
from claire.ingest import service as svcmod
from claire.ingest import pipeline as pipemod
from claire.ingest.service import IngestService
from claire.expand.onehop import find_candidates


PARENT = "https://parent.example/post"
SAME_SUBJECT = "https://same.example/repo"     # same_subject=True 기본값 → 병합 대상
OTHER_SUBJECT = "https://other.example/post"   # '별개주제' 훅 → 독립 문서
LOW_QUALITY = "https://low.example/post"       # '무관' 훅 → 게이트 거절


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
    raise RuntimeError(f"unexpected fetch {payload}")


def _svc(monkeypatch, tmp_path):
    s = _mem(monkeypatch, tmp_path)
    _patch_fetch(monkeypatch, _fetch)
    return s, IngestService(s)


def test_same_subject_merges_into_parent_not_new_document(monkeypatch, tmp_path):
    s, svc = _svc(monkeypatch, tmp_path)
    parent = svc.ingest(PARENT, source="cli", expand_max=0)
    res = svc.expand_document(parent.document_id)

    assert res["merged"] == 1
    followed = {f["url"]: f for f in res["followed"]}
    assert followed[SAME_SUBJECT]["merged"] is True
    assert followed[SAME_SUBJECT]["stored"] is False  # 새 문서로 '적재'된 게 아니라 병합

    conn = dbm.connect(s.db_file); dbm.init_db(conn)
    # 새 Document 행이 생기지 않음(핵심 — 목록 뻥튀기 방지)
    assert dbm.find_document_by_canonical_url(conn, SAME_SUBJECT) is None
    # 부모 문서에 흡수됨 — 원문 링크는 extra_sources 로 추적 가능
    assert dbm.find_document_by_extra_source(conn, SAME_SUBJECT) == parent.document_id
    extra = dbm.get_document_extra_sources(conn, parent.document_id)
    assert len(extra) == 1 and extra[0]["url"] == SAME_SUBJECT
    # 부모 본문에 자식 내용이 실제로 합쳐짐(더 풍부한 글)
    row = dbm.get_document_row(conn, parent.document_id)
    assert "github 저장소 본문" in row["raw_text"]
    # 새 expand_queue 항목도 안 생김(재귀 없음)
    assert dbm.pending_expand(conn) == []
    conn.close()


def test_other_subject_still_independent_document(monkeypatch, tmp_path):
    s, svc = _svc(monkeypatch, tmp_path)
    parent = svc.ingest(PARENT, source="cli", expand_max=0)
    res = svc.expand_document(parent.document_id)

    followed = {f["url"]: f for f in res["followed"]}
    assert followed[OTHER_SUBJECT]["merged"] is False
    assert followed[OTHER_SUBJECT]["stored"] is True
    assert res["stored"] == 1

    conn = dbm.connect(s.db_file); dbm.init_db(conn)
    assert dbm.find_document_by_canonical_url(conn, OTHER_SUBJECT) is not None
    conn.close()


def test_low_quality_still_skipped_before_subject_check(monkeypatch, tmp_path):
    s, svc = _svc(monkeypatch, tmp_path)
    parent = svc.ingest(PARENT, source="cli", expand_max=0)
    res = svc.expand_document(parent.document_id)

    followed = {f["url"]: f for f in res["followed"]}
    assert LOW_QUALITY not in [k for k, v in followed.items() if v.get("merged") or v.get("stored")]
    conn = dbm.connect(s.db_file); dbm.init_db(conn)
    assert dbm.find_document_by_canonical_url(conn, LOW_QUALITY) is None
    assert dbm.find_document_by_extra_source(conn, LOW_QUALITY) is None
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
    res = svc.expand_document(parent.document_id)

    assert res["merged"] == 0
    followed = {f["url"]: f for f in res["followed"]}
    assert followed[SAME_SUBJECT]["merged"] is False
    assert followed[SAME_SUBJECT]["error"]

    conn = dbm.connect(s.db_file); dbm.init_db(conn)
    row = dbm.get_document_row(conn, parent.document_id)
    assert row["raw_text"] == original_text  # 병합 시도 이전 상태로 복원
    assert dbm.get_document_extra_sources(conn, parent.document_id) == []
    conn.close()


def test_merged_source_url_not_reproposed_as_candidate(monkeypatch, tmp_path):
    s, svc = _svc(monkeypatch, tmp_path)
    parent = svc.ingest(PARENT, source="cli", expand_max=0)
    svc.expand_document(parent.document_id)  # SAME_SUBJECT 를 부모에 병합

    conn = dbm.connect(s.db_file); dbm.init_db(conn)
    other_parent_doc = _doc(
        "https://another-parent.example/post", "다른 부모 문서",
        links=[SAME_SUBJECT])
    cands = find_candidates(conn, other_parent_doc, limit=5)
    assert SAME_SUBJECT not in cands  # 이미 다른 문서에 병합 출처로 흡수됨
    conn.close()
