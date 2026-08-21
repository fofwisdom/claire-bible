"""1홉 자동확장 — 후보수집·LLM 선별·store 게이트·재귀가드·enqueue (네트워크 없이).

mock provider 훅:
  - select_followups: url/anchor 에 'skip' → 제외(파고들지=LLM 모사)
  - judge_research: report 에 '무관' → 저품질(쌓을지 게이트 거절)
  - judge_research: report 에 '별개주제' → same_subject=False(독립 문서로 적재 — 트리거
    없으면 기본 True 라 부모에 병합됨. 이 파일은 병합 이전의 '선별+게이트+독립 적재' 배선을
    보는 게 목적이라 GOOD 은 '별개주제' 로 명시 — 병합 자체는 test_onehop_merge.py 참조)
"""

from __future__ import annotations

from claire.config import Settings
from claire.expand.follow import build_candidates
from claire.ingest import pipeline as pipemod
from claire.ingest import service as svcmod
from claire.ingest.service import IngestService
from claire.ontology.base import Document
from claire.store import db as dbm

PARENT = "https://parent.example/post"
GOOD = "https://good.example/a"
SKIP = "https://skip.example/b"
BAD = "https://bad.example/c"


def _patch_fetch(monkeypatch, fn):
    monkeypatch.setattr(svcmod, "default_fetch", fn)
    monkeypatch.setattr(pipemod, "default_fetch", fn)


def _mem(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAIRE_DB_PATH", str(tmp_path / "e.db"))
    monkeypatch.setenv("CLAIRE_VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("CLAIRE_PROVIDER", "mock")
    return Settings()


def _doc(url, text, *, links=None, anchors=None):
    meta = {}
    if links is not None:
        meta["links"] = links
    if anchors is not None:
        meta["link_anchors"] = anchors
    return Document(url=url, canonical_url=url, title=url.rsplit("/", 1)[-1],
                    raw_text=text, source_type="web", content_hash=str(hash(text)),
                    meta=meta)


def _fetch(payload):
    body = "유익하고 구체적인 본문 내용입니다. " * 20
    if payload == PARENT:
        return _doc(PARENT, "부모 문서 본문 " * 30,
                    links=[GOOD, SKIP, BAD],
                    anchors=[{"url": GOOD, "anchor": "좋은 후속 자료"},
                             {"url": SKIP, "anchor": "광고 skip"},
                             {"url": BAD, "anchor": "관련 링크"}])
    if payload == GOOD:
        return _doc(GOOD, "별개주제 — 독립 문서로 남아야 하는 자료. " + body)
    if payload == BAD:
        return _doc(BAD, "무관한 다른 주제의 내용. " * 20)  # judge '무관' → 거절
    if payload == SKIP:
        return _doc(SKIP, body)
    raise RuntimeError(f"unexpected fetch {payload}")


# --- build_candidates ---

def test_build_candidates_prefilter_and_anchors(monkeypatch, tmp_path):
    s = _mem(monkeypatch, tmp_path)
    conn = dbm.connect(s.db_file); dbm.init_db(conn)
    doc = _doc(PARENT, "x",
               links=[GOOD, "https://x.com/junk", "https://site/about", GOOD],
               anchors=[{"url": GOOD, "anchor": "좋은 자료"}])
    cands = build_candidates(conn, doc)
    urls = [c["url"] for c in cands]
    assert GOOD in urls
    assert "https://x.com/junk" not in urls       # _SKIP_HOSTS
    assert "https://site/about" not in urls        # boilerplate path
    assert urls.count(GOOD) == 1                    # dedup
    assert next(c for c in cands if c["url"] == GOOD)["anchor"] == "좋은 자료"
    conn.close()


# --- expand_document: 선별 + 게이트 ---

def test_expand_document_selects_and_gates(monkeypatch, tmp_path):
    s = _mem(monkeypatch, tmp_path)
    _patch_fetch(monkeypatch, _fetch)
    svc = IngestService(s)
    parent = svc.ingest(PARENT, source="cli", expand_max=0)  # 확장 enqueue 없이 부모만
    res = svc.expand_document(parent.document_id)
    assert res["candidates"] == 3
    assert res["selected"] == 2          # SKIP 은 select 에서 제외
    assert res["stored"] == 1            # GOOD 통과
    assert res["skipped"] == 1           # BAD 는 judge '무관' 거절
    # GOOD 가 실제로 그래프에 적재됐는지
    conn = dbm.connect(s.db_file); dbm.init_db(conn)
    assert dbm.find_document_by_canonical_url(conn, GOOD) is not None
    assert dbm.find_document_by_canonical_url(conn, BAD) is None
    conn.close()


# --- enqueue + 재귀가드 ---

def test_auto_expand_enqueues_parent_only(monkeypatch, tmp_path):
    s = _mem(monkeypatch, tmp_path)
    assert s.auto_expand is True
    _patch_fetch(monkeypatch, _fetch)
    svc = IngestService(s)
    # 1차 적재(telegram) → auto_expand 로 부모가 큐에 등록돼야 함
    parent = svc.ingest(PARENT, source="telegram", expand_max=5)
    conn = dbm.connect(s.db_file); dbm.init_db(conn)
    pend = dbm.pending_expand(conn)
    assert [r["document_id"] for r in pend] == [parent.document_id]
    conn.close()

    # 큐 처리 → 부모 done, 자식(onehop)들은 재확장 enqueue 안 됨(깊이 1)
    out = svc.run_expand_queue()
    assert len(out) == 1 and out[0]["stored"] == 1
    conn = dbm.connect(s.db_file); dbm.init_db(conn)
    assert dbm.pending_expand(conn) == []          # 부모 done, 자식 enqueue 없음
    assert dbm.expand_status_counts(conn).get("done") == 1
    conn.close()


def test_auto_expand_off_no_enqueue(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAIRE_AUTO_EXPAND", "0")
    s = _mem(monkeypatch, tmp_path)
    assert s.auto_expand is False
    _patch_fetch(monkeypatch, _fetch)
    svc = IngestService(s)
    svc.ingest(PARENT, source="telegram", expand_max=5)
    conn = dbm.connect(s.db_file); dbm.init_db(conn)
    assert dbm.pending_expand(conn) == []          # 확장 비활성 → enqueue 없음
    conn.close()
