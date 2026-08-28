"""복원(refresh) 메커니즘 — 큐 + in-place 재적재 (네트워크 없이)."""

from __future__ import annotations

import sqlite3

from claire.config import Settings
from claire.ingest import pipeline as pipemod
from claire.ingest import service as svcmod
from claire.ingest.service import IngestService
from claire.ontology.base import Document
from claire.store import db as dbm


def _patch_fetch(monkeypatch, fn):
    """svc.ingest 는 pipeline.default_fetch 를, refresh_document 는 svcmod.default_fetch 를
    쓰므로 둘 다 패치해야 네트워크 없이 동작한다."""
    monkeypatch.setattr(svcmod, "default_fetch", fn)
    monkeypatch.setattr(pipemod, "default_fetch", fn)


def _mem(monkeypatch, tmp_path):
    db = tmp_path / "r.db"
    monkeypatch.setenv("CLAIRE_DB_PATH", str(db))
    monkeypatch.setenv("CLAIRE_VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("CLAIRE_PROVIDER", "mock")
    return Settings()


# --- db 큐 헬퍼 ---

def test_enqueue_dedup_and_pending(tmp_path):
    conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    assert dbm.enqueue_refresh(conn, document_id="d1", payload="u1", reason="thin") is True
    # 같은 doc 재등록 → 신규 아님(되살림)
    assert dbm.enqueue_refresh(conn, document_id="d1", payload="u1", reason="thin") is False
    pend = dbm.pending_refresh(conn)
    assert len(pend) == 1 and pend[0]["document_id"] == "d1"
    dbm.update_refresh(conn, pend[0]["id"], status="done")
    assert dbm.pending_refresh(conn) == []
    assert dbm.refresh_status_counts(conn) == {"done": 1}


def test_thin_documents_filter():
    conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    dbm.insert_document(conn, Document(id="a", url="https://x.kr/1", raw_text="x"*50,
                                       source_type="web", content_hash="h1"))
    dbm.insert_document(conn, Document(id="b", url="https://x.kr/2", raw_text="y"*5000,
                                       source_type="web", content_hash="h2"))
    dbm.insert_document(conn, Document(id="c", url="https://other.com/3", raw_text="z"*50,
                                       source_type="web", content_hash="h3"))
    thin = dbm.thin_documents(conn, max_len=300)
    assert {r["id"] for r in thin} == {"a", "c"}
    thin_host = dbm.thin_documents(conn, max_len=300, host="x.kr")
    assert {r["id"] for r in thin_host} == {"a"}


# --- service refresh_document (fetch monkeypatched) ---

def test_refresh_updates_in_place(monkeypatch, tmp_path):
    s = _mem(monkeypatch, tmp_path)
    svc = IngestService(s)
    # 초기: thin 문서 적재
    thin_doc = Document(url="https://discuss.x/t/foo/1", title="T", raw_text="짧음",
                        source_type="web", content_hash="old")
    _patch_fetch(monkeypatch, lambda p: thin_doc)
    rep = svc.ingest("https://discuss.x/t/foo/1", source="test")
    doc_id = rep.document_id
    assert rep.document_id

    # 이제 fetch 가 풍부한 본문을 준다(스크래퍼 개선 가정)
    rich = Document(url="https://discuss.x/t/foo/1", title="T rich",
                    raw_text="풍부한 본문 " * 100, source_type="web", content_hash="new")
    _patch_fetch(monkeypatch, lambda p: rich)
    res = svc.refresh_document(doc_id, "https://discuss.x/t/foo/1")
    assert res["status"] == "done"
    assert res["new_len"] > res["old_len"]

    # 같은 id 로 in-place 갱신됐는지(문서 수 안 늘어남)
    conn = dbm.connect(s.db_file); dbm.init_db(conn)
    assert dbm.counts(conn)["documents"] == 1
    row = dbm.get_document_row(conn, doc_id)
    assert row["content_hash"] == "new" and len(row["raw_text"]) > 100
    conn.close()


def test_refresh_nochange_when_same_hash(monkeypatch, tmp_path):
    s = _mem(monkeypatch, tmp_path)
    svc = IngestService(s)
    doc = Document(url="https://x/1", title="T", raw_text="body", source_type="web",
                   content_hash="same")
    _patch_fetch(monkeypatch, lambda p: doc)
    rep = svc.ingest("https://x/1", source="test")
    res = svc.refresh_document(rep.document_id, "https://x/1")
    assert res["status"] == "nochange"


def test_refresh_nochange_backfills_images_and_detail(monkeypatch, tmp_path):
    """본문이 안 바뀌어도(nochange) 재fetch 로 새로 잡힌 이미지와 detail 을 백필한다.

    이미지 수집 이전에 적재된 문서에 이미지/마크다운 detail 을 채우는 핵심 경로(비파괴).
    """
    s = _mem(monkeypatch, tmp_path)
    svc = IngestService(s)
    # 1) 이미지 없이 적재(구버전)
    plain = Document(url="https://x/1", title="T", raw_text="body " * 50,
                     source_type="web", content_hash="same")
    _patch_fetch(monkeypatch, lambda p: plain)
    rep = svc.ingest("https://x/1", source="test")
    did = rep.document_id
    conn = dbm.connect(s.db_file); dbm.init_db(conn)
    dbm.set_document_detail(conn, did, "")           # detail 비움(구버전 상태 모사)
    conn.execute("UPDATE documents SET meta=? WHERE id=?", ("{}", did))  # images 키 없음
    conn.commit(); conn.close()
    assert did in dbm.documents_missing_images(dbm.connect(s.db_file))

    # 2) 같은 본문 + 이미지가 잡히도록 재fetch. 실제 다운로드(네트워크)는 별도 관심사라
    # store.raw.download_images 를 패치해 그대로 통과시킨다(이 테스트는 백필 배선 검증).
    monkeypatch.setattr("claire.store.raw.download_images",
                        lambda data_dir, doc_id, images: images)
    imgs = [{"url": "https://x/diagram.png", "alt": "도식", "caption": ""}]
    withimg = Document(url="https://x/1", title="T", raw_text="body " * 50,
                       source_type="web", content_hash="same", meta={"images": imgs})
    _patch_fetch(monkeypatch, lambda p: withimg)
    res = svc.refresh_document(did, "https://x/1")
    assert res["status"] == "nochange" and res["detail_updated"] is True

    conn = dbm.connect(s.db_file); dbm.init_db(conn)
    row = dbm.get_document_row(conn, did)
    import json
    assert json.loads(row["meta"])["images"] == imgs        # 이미지 보존
    detail = dbm.get_document_detail(conn, did)
    assert detail and "diagram.png" in detail               # mock detail 에 이미지 마크다운
    assert did not in dbm.documents_missing_images(conn)     # 더는 백필 대상 아님
    conn.close()


def test_run_refresh_queue_marks_done(monkeypatch, tmp_path):
    s = _mem(monkeypatch, tmp_path)
    svc = IngestService(s)
    thin = Document(url="https://discuss.x/t/a/1", title="T", raw_text="x",
                    source_type="web", content_hash="o")
    _patch_fetch(monkeypatch, lambda p: thin)
    rep = svc.ingest("https://discuss.x/t/a/1", source="test")
    # 큐 등록
    n = svc.mark_thin_for_refresh(max_len=300)
    assert n == 1
    # 풍부한 본문으로 교체 후 큐 실행
    rich = Document(url="https://discuss.x/t/a/1", title="T2", raw_text="본문 " * 200,
                    source_type="web", content_hash="n")
    _patch_fetch(monkeypatch, lambda p: rich)
    results = svc.run_refresh_queue(limit=10)
    assert len(results) == 1 and results[0]["status"] == "done"
    conn = dbm.connect(s.db_file); dbm.init_db(conn)
    assert dbm.refresh_status_counts(conn) == {"done": 1}
    conn.close()


def test_watch_document_refresh_snapshots_and_unseen(monkeypatch, tmp_path):
    """[주기 크롤링] watch 문서가 내용 변경되면 변경 '전' 원문을 스냅샷으로 보존(시계열) +
    다시 봐야 하니 unseen. 그래프는 최신 in-place(기존 동작)."""
    s = _mem(monkeypatch, tmp_path)
    svc = IngestService(s)
    url = "https://bench.example/leaderboard"
    v1 = Document(url=url, title="Leaderboard", raw_text="순위표 v1: 1위 A 2위 B " * 40,
                  source_type="web", content_hash="hv1")
    _patch_fetch(monkeypatch, lambda p: v1)
    rep = svc.ingest(url, source="web")
    did = rep.document_id
    # watch 켜고(수동 — ③-3 LLM 자동판단과 무관하게 ③-2 단독 검증), 봤다고 표시
    conn = dbm.connect(s.db_file)
    dbm.set_document_watch(conn, did, enabled=True, interval=1.0)
    dbm.set_document_seen(conn, did, seen=True)
    conn.close()
    # 내용이 바뀐 v2 로 재크롤(refresh)
    v2 = Document(url=url, title="Leaderboard", raw_text="순위표 v2: 1위 C 2위 A " * 40,
                  source_type="web", content_hash="hv2")
    _patch_fetch(monkeypatch, lambda p: v2)
    res = svc.refresh_document(did, url)
    assert res["status"] == "done", res
    conn = dbm.connect(s.db_file)
    snaps = dbm.document_snapshots(conn, did)
    assert len(snaps) == 1, snaps                       # 변경 전 상태 1건 보존
    assert "v1" in snaps[0]["raw_text"] and snaps[0]["content_hash"] == "hv1"
    cur = dbm.get_document_row(conn, did)
    assert "v2" in cur["raw_text"]                      # 현재 문서는 최신
    assert cur["seen"] == 0                             # 변경됐으니 미열람
    conn.close()


def test_non_watch_refresh_no_snapshot(monkeypatch, tmp_path):
    """watch 가 아닌 일반 refresh(thin 복원 등)는 스냅샷을 남기지 않는다(기존 동작 보존)."""
    s = _mem(monkeypatch, tmp_path)
    svc = IngestService(s)
    url = "https://blog.example/post"
    v1 = Document(url=url, title="Post", raw_text="본문 v1 " * 40, source_type="web", content_hash="p1")
    _patch_fetch(monkeypatch, lambda p: v1)
    did = svc.ingest(url, source="web").document_id  # watch 미설정(None)
    v2 = Document(url=url, title="Post", raw_text="본문 v2 " * 40, source_type="web", content_hash="p2")
    _patch_fetch(monkeypatch, lambda p: v2)
    svc.refresh_document(did, url)
    conn = dbm.connect(s.db_file)
    assert dbm.document_snapshots(conn, did) == []   # watch 아니면 스냅샷 없음
    conn.close()


def test_enqueue_due_watch(monkeypatch, tmp_path):
    """[주기 크롤링] due 된 watch 문서만 refresh 큐(reason='watch')에 등록 + last_watched_at
    갱신 → 방금 등록한 건 다음 호출에서 due 아님(중복 폭주 방지)."""
    s = _mem(monkeypatch, tmp_path)
    svc = IngestService(s)
    conn = dbm.connect(s.db_file); dbm.init_db(conn)
    dbm.insert_document(conn, Document(id="w1", url="https://b/lb", raw_text="x" * 600,
                                       source_type="web", content_hash="w1"))
    dbm.set_document_watch(conn, "w1", enabled=True, interval=3600.0)
    dbm.insert_document(conn, Document(id="n1", url="https://b/blog", raw_text="y" * 600,
                                       source_type="web", content_hash="n1"))  # watch 아님
    conn.close()
    assert svc.enqueue_due_watch() == 1                 # w1 만 due
    conn = dbm.connect(s.db_file)
    pend = dbm.pending_refresh(conn)
    assert len(pend) == 1 and pend[0]["document_id"] == "w1" and pend[0]["reason"] == "watch"
    assert dbm.get_document_row(conn, "w1")["last_watched_at"] is not None
    conn.close()
    assert svc.enqueue_due_watch() == 0                 # 방금 watched → interval 안 지나 due 아님
