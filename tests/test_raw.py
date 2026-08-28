"""재적재(raw preservation) 3-tier 검증 — 알고리즘 변경 시 재생 가능해야 함."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx

from claire.extract.provider import MockProvider
from claire.ingest.pipeline import _guess_kind, ingest
from claire.ontology.base import Document
from claire.store import db as dbm
from claire.store.raw import (
    _MAX_IMAGE_BYTES,
    download_images,
    load_artifact,
    raw_disk_usage,
    save_artifact,
)
from claire.store.vectors import VectorStore


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    return conn


def _fetch(doc):
    return lambda p: doc


def test_guess_kind():
    assert _guess_kind("https://x.com") == "url"
    assert _guess_kind("file:///a/b") == "file"
    assert _guess_kind("just text") == "text"


def test_inbox_recorded_before_processing_even_on_failure():
    conn = _db()
    vstore = VectorStore(conn, "brute")

    def boom(_p):
        raise RuntimeError("net down")

    rep = ingest("https://dead.link", conn=conn, provider=MockProvider(),
                 vstore=vstore, fetch_fn=boom, source="test")
    # 실패해도 inbox 에는 원본이 남아야 재생 가능
    rows = dbm.all_inbox(conn)
    assert len(rows) == 1
    assert rows[0]["payload"] == "https://dead.link"
    assert rows[0]["status"] == "error"
    assert rows[0]["kind"] == "url"
    assert rep.inbox_id == rows[0]["id"]


def test_inbox_status_done_and_duplicate():
    conn = _db()
    vstore = VectorStore(conn, "brute")
    doc = Document(title="D", raw_text="body text", source_type="text", content_hash="h1")
    p = MockProvider()
    r1 = ingest("payload-x", conn=conn, provider=p, vstore=vstore, fetch_fn=_fetch(doc), source="test")
    r2 = ingest("payload-x", conn=conn, provider=p, vstore=vstore, fetch_fn=_fetch(doc), source="test")
    rows = dbm.all_inbox(conn)
    assert len(rows) == 2                       # 원본은 항상 2건 보관
    assert rows[0]["status"] == "done"
    assert rows[1]["status"] == "duplicate"     # 2번째는 dedup
    assert r2.duplicate


def test_extraction_raw_json_stored():
    conn = _db()
    vstore = VectorStore(conn, "brute")
    doc = Document(title="Graphify", url="https://github.com/safishamsi/graphify",
                   raw_text="kg gen", source_type="web", content_hash="hg")
    ingest("u", conn=conn, provider=MockProvider(), vstore=vstore, fetch_fn=_fetch(doc), source="test")
    rows = conn.execute("SELECT * FROM extractions").fetchall()
    assert len(rows) == 1
    assert rows[0]["model"] == "mock"
    assert rows[0]["prompt_version"] == "mock-1"
    assert "graphify" in rows[0]["raw_response"].lower()  # raw JSON 재생용


def test_layer2_artifact_saved_and_loadable(tmp_path: Path):
    conn = _db()
    vstore = VectorStore(conn, "brute")
    doc = Document(title="T", raw_text="the original fetched body", source_type="web",
                   content_hash="ha")
    rep = ingest("u", conn=conn, provider=MockProvider(), vstore=vstore,
                 fetch_fn=_fetch(doc), source="test", data_dir=tmp_path)
    # gzip artifact 가 doc id 로 저장되어 원문 복원 가능
    back = load_artifact(tmp_path, rep.document_id)
    assert back == "the original fetched body"
    usage = raw_disk_usage(tmp_path)
    assert usage["artifacts"] > 0


def test_save_artifact_roundtrip(tmp_path: Path):
    save_artifact(tmp_path, "doc_1", "héllo 안녕 <b>x</b>")
    assert load_artifact(tmp_path, "doc_1") == "héllo 안녕 <b>x</b>"
    assert load_artifact(tmp_path, "missing") is None


class _FakeResp:
    def __init__(self, status_code=200, content=b"", content_type="image/png"):
        self.status_code = status_code
        self.content = content
        self.headers = {"content-type": content_type}


class _FakeHttpxClient:
    """httpx.Client 대역(네트워크 없이) — url→응답 매핑."""
    def __init__(self, responses, *a, **kw):
        self._responses = responses

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url):
        resp = self._responses.get(url)
        if resp is None:
            raise RuntimeError(f"unexpected fetch: {url}")
        return resp


def test_download_images_saves_local_copy(monkeypatch, tmp_path: Path):
    """정상 이미지 응답 → 로컬 파일 저장 + local 경로 부여(사용자 요구 — 외부링크 유실 대비)."""
    responses = {"https://x/a.png": _FakeResp(content=b"PNGBYTES", content_type="image/png")}
    monkeypatch.setattr(httpx, "Client", lambda *a, **kw: _FakeHttpxClient(responses))
    out = download_images(tmp_path, "doc_1", [{"url": "https://x/a.png", "alt": "a"}])
    assert out[0]["local"] == "images/doc_1_0.png"
    assert (tmp_path / "images" / "doc_1_0.png").read_bytes() == b"PNGBYTES"
    assert out[0]["alt"] == "a"  # 기존 필드 보존


def test_download_images_failure_modes_fall_back_to_url(monkeypatch, tmp_path: Path):
    """404·비이미지 컨텐츠타입·용량초과는 각각 원본 url 유지(local 키 없음) — 개별 실패가
    나머지·적재를 막지 않는다."""
    big = b"x" * (_MAX_IMAGE_BYTES + 1)
    responses = {
        "https://x/404.png": _FakeResp(status_code=404),
        "https://x/notimg.png": _FakeResp(content=b"<html>", content_type="text/html"),
        "https://x/big.png": _FakeResp(content=big, content_type="image/png"),
    }
    monkeypatch.setattr(httpx, "Client", lambda *a, **kw: _FakeHttpxClient(responses))
    images = [
        {"url": "https://x/404.png"},
        {"url": "https://x/notimg.png"},
        {"url": "https://x/big.png"},
    ]
    out = download_images(tmp_path, "doc_1", images)
    assert all("local" not in im for im in out)
    assert not (tmp_path / "images").exists() or not any((tmp_path / "images").iterdir())


def test_download_images_network_error_is_caught(monkeypatch, tmp_path: Path):
    """httpx 예외(DNS 실패 등)도 개별 이미지만 원본 url 로 폴백 — 적재를 막지 않는다."""
    class _BoomClient:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url): raise OSError("network unreachable")

    monkeypatch.setattr(httpx, "Client", lambda *a, **kw: _BoomClient())
    out = download_images(tmp_path, "doc_1", [{"url": "https://dead/x.png"}])
    assert "local" not in out[0]
    assert out[0]["url"] == "https://dead/x.png"
