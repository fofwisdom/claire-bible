"""재적재(raw preservation) 3-tier 검증 — 알고리즘 변경 시 재생 가능해야 함."""

from __future__ import annotations

import gzip
import sqlite3
from pathlib import Path
from unittest.mock import patch

import httpx

from claire.extract.provider import MockProvider
from claire.ingest.pipeline import _guess_kind, ingest
from claire.ontology.base import Document
from claire.store import db as dbm
from claire.store.raw import (
    _MAX_IMAGE_BYTES,
    _get_compressor,
    _get_decompressor,
    artifact_paths,
    download_images,
    load_artifact,
    migrate_artifacts,
    raw_disk_usage,
    remove_artifact,
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
    saved_path = save_artifact(tmp_path, "doc_1", "héllo 안녕 <b>x</b>")
    assert saved_path.endswith("doc_1.txt.zst")
    assert (tmp_path / "raw" / "artifacts" / "doc_1.txt.zst").exists()
    assert load_artifact(tmp_path, "doc_1") == "héllo 안녕 <b>x</b>"
    assert load_artifact(tmp_path, "missing") is None


def test_legacy_gzip_transparent_load(tmp_path: Path):
    """레거시 .txt.gz 파일만 존재하는 경우에도 투명하게 로드되어야 함."""
    art_dir = tmp_path / "raw" / "artifacts"
    art_dir.mkdir(parents=True, exist_ok=True)
    gz_file = art_dir / "legacy_doc.txt.gz"
    with gzip.open(gz_file, "wt", encoding="utf-8") as f:
        f.write("legacy gzip raw text content")

    # .txt.zst 가 없어도 .txt.gz 로 투명하게 로드
    assert not (art_dir / "legacy_doc.txt.zst").exists()
    loaded = load_artifact(tmp_path, "legacy_doc")
    assert loaded == "legacy gzip raw text content"


def test_corrupted_zst_fallback_to_gzip(tmp_path: Path):
    """손상된 .txt.zst 파일이 존재할 경우 경고 로깅 후 유효한 .txt.gz 로 안전하게 폴백."""
    art_dir = tmp_path / "raw" / "artifacts"
    art_dir.mkdir(parents=True, exist_ok=True)

    # 손상된 zst 파일
    zst_file = art_dir / "fallback_doc.txt.zst"
    zst_file.write_bytes(b"corrupted-non-zstd-garbage-data")

    # 정상 레거시 gz 파일
    gz_file = art_dir / "fallback_doc.txt.gz"
    with gzip.open(gz_file, "wt", encoding="utf-8") as f:
        f.write("fallback valid content from gzip")

    # load_artifact 호출 시 손상된 zst 예외를 잡고 gz 로 폴백
    loaded = load_artifact(tmp_path, "fallback_doc")
    assert loaded == "fallback valid content from gzip"


def test_atomic_write_and_legacy_cleanup(tmp_path: Path):
    """신규 zst 저장 성공 시 레거시 .txt.gz는 원자적으로 정리되고 임시 파일이 남지 않아야 함."""
    art_dir = tmp_path / "raw" / "artifacts"
    art_dir.mkdir(parents=True, exist_ok=True)

    # 1. 레거시 gz 생성
    gz_file = art_dir / "doc_atomic.txt.gz"
    with gzip.open(gz_file, "wt", encoding="utf-8") as f:
        f.write("old version in gzip")
    assert gz_file.exists()

    # 2. 신규 zst 저장
    save_artifact(tmp_path, "doc_atomic", "new version in zstd")
    zst_file = art_dir / "doc_atomic.txt.zst"
    assert zst_file.exists()
    # 기존 gz는 삭제되어야 함
    assert not gz_file.exists()
    # 임시 파일(.tmp-)이 남지 않아야 함
    tmp_files = list(art_dir.glob(".tmp-*"))
    assert len(tmp_files) == 0
    # 새 내용 로드 확인
    assert load_artifact(tmp_path, "doc_atomic") == "new version in zstd"


def test_atomic_write_failure_preserves_legacy(tmp_path: Path):
    """신규 zst 저장 중 오류 발생 시 임시 파일 정리 및 레거시 .gz 무손실 보존."""
    art_dir = tmp_path / "raw" / "artifacts"
    art_dir.mkdir(parents=True, exist_ok=True)

    gz_file = art_dir / "doc_fail.txt.gz"
    with gzip.open(gz_file, "wt", encoding="utf-8") as f:
        f.write("precious legacy content")

    # os.replace 시점에 인위적 I/O 오류 주입
    with patch("os.replace", side_effect=OSError("simulated disk full")):
        try:
            save_artifact(tmp_path, "doc_fail", "failed text")
        except OSError:
            pass

    # 기존 gz가 여전히 온전히 보존되어야 함 (무손실 원칙)
    assert gz_file.exists()
    assert load_artifact(tmp_path, "doc_fail") == "precious legacy content"
    # 임시 파일이 정리되었는지 확인
    tmp_files = list(art_dir.glob(".tmp-*"))
    assert len(tmp_files) == 0


def test_artifact_paths_and_remove_artifact(tmp_path: Path):
    """artifact_paths 와 remove_artifact 라이프사이클 헬퍼 검증."""
    art_dir = tmp_path / "raw" / "artifacts"
    art_dir.mkdir(parents=True, exist_ok=True)

    zst = art_dir / "target_doc.txt.zst"
    gz = art_dir / "target_doc.txt.gz"
    zst.write_bytes(b"zst")
    gz.write_bytes(b"gz")

    paths = artifact_paths(tmp_path, "target_doc")
    assert set(paths) == {zst, gz}

    removed = remove_artifact(tmp_path, "target_doc")
    assert set(removed) == {zst, gz}
    assert not zst.exists()
    assert not gz.exists()
    assert artifact_paths(tmp_path, "target_doc") == []


def test_migrate_artifacts_dry_run_and_apply(tmp_path: Path):
    """migrate_artifacts 의 dry-run, apply, SHA-256 검증 및 반복 실행 멱등성 검증."""
    art_dir = tmp_path / "raw" / "artifacts"
    art_dir.mkdir(parents=True, exist_ok=True)

    doc_data = {
        "mig_1": "첫 번째 문서 내용입니다. " * 20,
        "mig_2": "두 번째 마이그레이션 대상 문서입니다. " * 30,
        "mig_3": "세 번째 문서입니다.",
    }

    for did, text in doc_data.items():
        with gzip.open(art_dir / f"{did}.txt.gz", "wt", encoding="utf-8") as f:
            f.write(text)

    # 1. Dry-run 실행 (apply=False)
    dry_res = migrate_artifacts(tmp_path, apply=False, level=3)
    assert dry_res["dry_run"] is True
    assert dry_res["total"] == 3
    assert dry_res["migrated"] == 3
    assert dry_res["already_zst"] == 0
    assert dry_res["errors"] == 0
    assert dry_res["bytes_before"] > 0
    assert dry_res["bytes_after"] > 0
    # 파일 상태 불변 확인
    for did in doc_data:
        assert (art_dir / f"{did}.txt.gz").exists()
        assert not (art_dir / f"{did}.txt.zst").exists()

    # 2. Apply 실행 (apply=True)
    apply_res = migrate_artifacts(tmp_path, apply=True, level=3)
    assert apply_res["dry_run"] is False
    assert apply_res["total"] == 3
    assert apply_res["migrated"] == 3
    assert apply_res["already_zst"] == 0
    assert apply_res["errors"] == 0

    # 변환된 zst 파일 확인 및 원문과 완벽 일치 확인, gz 파일 제거 확인
    for did, orig_text in doc_data.items():
        assert not (art_dir / f"{did}.txt.gz").exists()
        assert (art_dir / f"{did}.txt.zst").exists()
        assert load_artifact(tmp_path, did) == orig_text

    # 3. 멱등성 검증: 다시 실행 시 already_zst 가 카운트되어야 함
    repeat_res = migrate_artifacts(tmp_path, apply=True, level=3)
    assert repeat_res["total"] == 3
    assert repeat_res["already_zst"] == 3
    assert repeat_res["migrated"] == 0
    assert repeat_res["errors"] == 0


def test_migrate_artifacts_sha256_corruption_safety(tmp_path: Path):
    """손상된 .gz 파일 발견 시 오류 기록 및 원본 보존 검증."""
    art_dir = tmp_path / "raw" / "artifacts"
    art_dir.mkdir(parents=True, exist_ok=True)

    bad_gz = art_dir / "bad_doc.txt.gz"
    bad_gz.write_bytes(b"not-a-valid-gzip-header-junk")

    res = migrate_artifacts(tmp_path, apply=True, stop_on_error=False)
    assert res["total"] == 1
    assert res["migrated"] == 0
    assert res["errors"] == 1
    assert len(res["error_details"]) == 1
    assert res["error_details"][0]["doc_id"] == "bad_doc"
    # 손상된 원본이라도 임의 삭제하지 않고 보존
    assert bad_gz.exists()
    assert not (art_dir / "bad_doc.txt.zst").exists()


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


def test_thread_local_context_caching_and_isolation():
    """스레드 로컬 zstd 컨텍스트가 단일 스레드 내에서는 재사용되고, 다른 스레드 간에는 격리되는지 검증."""
    import concurrent.futures

    c1 = _get_compressor(3)
    c2 = _get_compressor(3)
    d1 = _get_decompressor()
    d2 = _get_decompressor()

    # 동일 스레드 내에서는 인스턴스가 100% 동일하게 캐싱 재사용되어야 함
    assert c1 is c2
    assert d1 is d2

    # 레벨이 다른 경우 별도 인스턴스 생성
    c_lvl5 = _get_compressor(5)
    assert c_lvl5 is not c1

    # 별도 스레드에서는 스레드 안전성을 위해 별개의 C-API 컨텍스트를 소유해야 함
    def worker():
        other_c = _get_compressor(3)
        other_d = _get_decompressor()
        return id(other_c), id(other_d)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        future = executor.submit(worker)
        other_c_id, other_d_id = future.result()

    assert id(c1) != other_c_id
    assert id(d1) != other_d_id


def test_concurrent_save_and_load_artifact(tmp_path: Path):
    """다중 스레드 동시 save_artifact 및 load_artifact 수행 시 C-API 동시성 크래시(SIGSEGV)나 데이터 불일치가 없어야 함."""
    import concurrent.futures

    art_dir = tmp_path / "raw" / "artifacts"
    art_dir.mkdir(parents=True, exist_ok=True)

    def worker_task(i: int) -> bool:
        doc_id = f"concurrent_doc_{i}"
        payload = f"Payload for concurrent worker {i} with special chars: 안녕 <b>x</b> {i * 7}" * 10
        saved_path = save_artifact(tmp_path, doc_id, payload)
        loaded = load_artifact(tmp_path, doc_id)
        return loaded == payload and saved_path.endswith(f"{doc_id}.txt.zst")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(worker_task, range(50)))

    assert all(results)
    assert len(list(art_dir.glob("*.txt.zst"))) == 50


def test_migrate_artifacts_batch_fsync(tmp_path: Path):
    """migrate_artifacts 의 batch_fsync_interval 옵션 동작 및 마이그레이션 정상 완료 검증."""
    art_dir = tmp_path / "raw" / "artifacts"
    art_dir.mkdir(parents=True, exist_ok=True)

    for i in range(15):
        did = f"batch_doc_{i}"
        with gzip.open(art_dir / f"{did}.txt.gz", "wt", encoding="utf-8") as f:
            f.write(f"Batch content {i} " * 20)

    # batch_fsync_interval=5 로 배치 플러시 테스트
    res = migrate_artifacts(tmp_path, apply=True, level=3, batch_fsync_interval=5)
    assert res["total"] == 15
    assert res["migrated"] == 15
    assert res["errors"] == 0
    assert len(list(art_dir.glob("*.txt.zst"))) == 15
    assert len(list(art_dir.glob("*.txt.gz"))) == 0
    for i in range(15):
        assert load_artifact(tmp_path, f"batch_doc_{i}") == f"Batch content {i} " * 20

