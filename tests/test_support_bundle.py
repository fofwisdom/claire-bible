"""Support Bundle 생성, zstd 압축, 공유 링크 문서 추적, 6시간 자동 파기 및 API/CLI 테스트."""

from __future__ import annotations

import io
import json
import sqlite3
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import zstandard as zstd
from starlette.testclient import TestClient

from claire.api import server
from claire.cli import build_parser
from claire.ontology.base import Document
from claire.store import db as dbm
from claire.store.telemetry import (
    DEFAULT_TELEMETRY_RETENTION_DAYS,
    connect_telemetry,
    record_telemetry,
)
from claire.support_bundle import (
    DEFAULT_SUPPORT_BUNDLE_DAYS,
    SUPPORT_BUNDLE_TTL_SECONDS,
    create_support_bundle,
    get_support_bundle,
    list_active_support_bundles,
    purge_expired_bundles,
    sanitize_sensitive_data,
    validate_bundle_days,
)

OWNER_TOKEN = "owner-" + ("o" * 32)
READONLY_TOKEN = "readonly-" + ("r" * 32)
OWNER_HEADERS = {"Authorization": f"Bearer {OWNER_TOKEN}"}


@dataclass
class StubSettings:
    db_file: Path
    data_dir: Path
    environment: str = "development"
    public_url: str = "http://127.0.0.1:8765"
    inject_token: str = OWNER_TOKEN
    readonly_token: str = READONLY_TOKEN
    cors_allowed_origins: str = ""
    anonymous_readonly: bool = False
    telemetry_retention_days: int = 30
    inject_host: str = "127.0.0.1"
    inject_port: int = 8765
    provider: str = "mock"

    @property
    def effective_provider(self) -> str:
        return "mock"

    def model_dump(self) -> dict[str, Any]:
        return {
            "environment": self.environment,
            "public_url": self.public_url,
            "inject_token": self.inject_token,
            "readonly_token": self.readonly_token,
            "telemetry_retention_days": self.telemetry_retention_days,
            "secret_api_key": "super_secret_12345",
        }


def _seed_db(db_path: Path) -> None:
    conn = dbm.connect(db_path)
    dbm.init_db(conn)
    doc = Document(
        id="doc_test_123",
        url="https://example.com/ai/article",
        canonical_url="https://example.com/ai/article",
        title="AI Evaluation Article",
        raw_text="Detailed analysis of AI benchmarks and evaluation methodologies.",
        summary="A summary of AI benchmarks.",
        source_type="web",
        content_hash="hash_ai_123",
    )
    dbm.insert_document(conn, doc)
    now = time.time()
    conn.execute(
        """
        INSERT INTO raw_inbox (received_at, source, kind, payload, document_id, status, attempts, error)
        VALUES (?, 'test', 'url', ?, ?, 'done', 0, NULL)
        """,
        (now, doc.url, doc.id),
    )
    conn.execute(
        """
        INSERT INTO raw_inbox (received_at, source, kind, payload, document_id, status, attempts, error)
        VALUES (?, 'test', 'url', 'https://example.com/failed', 'doc_err_999', 'error', 3, 'Timeout fetching page')
        """,
        (now,),
    )
    conn.commit()
    conn.close()


def _seed_telemetry(data_dir: Path) -> None:
    record_telemetry(
        data_dir,
        document_id="doc_test_123",
        provider="antigravity",
        model="gemini-3.7-flash",
        call_type="extract_json",
        exit_code=0,
        duration_ms=450,
        status="SUCCESS",
        google_block_reason="NONE",
        summary_verdict="REAL_LLM",
    )
    record_telemetry(
        data_dir,
        document_id="doc_err_999",
        provider="antigravity",
        model="gemini-3.7-flash",
        call_type="extract_json",
        exit_code=1,
        duration_ms=120,
        status="BLOCKED",
        google_block_reason="RECITATION",
        summary_verdict="RAW_SLICE_200",
        error_message="Recitation check triggered",
    )


def test_support_bundle_default_days_and_retention_boundary():
    assert DEFAULT_SUPPORT_BUNDLE_DAYS == 1
    assert validate_bundle_days(1) == 1
    assert validate_bundle_days(14, max_retention_days=14) == 14
    assert validate_bundle_days(30, max_retention_days=30) == 30

    with pytest.raises(ValueError, match="cannot exceed telemetry retention limit"):
        validate_bundle_days(31, max_retention_days=30)

    with pytest.raises(ValueError, match="at least 1"):
        validate_bundle_days(0)

    with pytest.raises(ValueError, match="at least 1"):
        validate_bundle_days(-5)


def test_support_bundle_sensitive_data_sanitization():
    raw = {
        "inject_token": "secret_token_val",
        "api_key": "my_gemini_key",
        "password": "db_password",
        "nested": {
            "auth_bearer": "Bearer eyJhbGciOi...",
            "normal_field": "public_data",
        },
        "items": ["safe_string", "Bearer secret_jwt_token_here"],
    }
    sanitized = sanitize_sensitive_data(raw)
    assert sanitized["inject_token"] == "***REDACTED***"
    assert sanitized["api_key"] == "***REDACTED***"
    assert sanitized["password"] == "***REDACTED***"
    assert sanitized["nested"]["auth_bearer"] == "***REDACTED***"
    assert sanitized["nested"]["normal_field"] == "public_data"
    assert sanitized["items"][0] == "safe_string"
    assert sanitized["items"][1] == "Bearer ***REDACTED***"


def test_support_bundle_creation_and_zstd_archive(tmp_path: Path):
    s = StubSettings(db_file=tmp_path / "claire.db", data_dir=tmp_path)
    _seed_db(s.db_file)
    _seed_telemetry(s.data_dir)

    # 기본 1일 번들 생성
    info = create_support_bundle(s, days=1)
    assert info.days_covered == 1
    assert info.filepath.is_file()
    assert info.filepath.suffix == ".zst"
    assert info.size_bytes > 0
    assert info.expires_at - info.created_at == SUPPORT_BUNDLE_TTL_SECONDS
    assert f"token={info.token}" in info.download_url

    # zstd 압축 해제 및 tar 파일 내용 검증
    dctx = zstd.ZstdDecompressor()
    decompressed_data = dctx.decompress(info.filepath.read_bytes(), max_output_size=50_000_000)
    tar = tarfile.open(fileobj=io.BytesIO(decompressed_data), mode="r:")
    names = tar.getnames()

    # 필수 파일 존재 검증
    assert any(n.endswith("manifest.json") for n in names)
    assert any(n.endswith("diagnostics/system.json") for n in names)
    assert any(n.endswith("diagnostics/config_sanitized.json") for n in names)
    assert any(n.endswith("telemetry/telemetry_records.jsonl") for n in names)
    assert any(n.endswith("telemetry/telemetry_stats.json") for n in names)
    assert any(n.endswith("pipeline/inbox_summary.json") for n in names)
    assert any(n.endswith("pipeline/failed_items.json") for n in names)
    assert any(n.endswith("pipeline/shares_index.json") for n in names)
    assert any(n.endswith("pipeline/db_integrity.json") for n in names)

    # Manifest 내용 파싱
    manifest_name = [n for n in names if n.endswith("manifest.json")][0]
    manifest_data = json.loads(tar.extractfile(manifest_name).read().decode("utf-8"))
    assert manifest_data["bundle_id"] == info.bundle_id
    assert manifest_data["ttl_hours"] == 6.0
    assert manifest_data["days_covered"] == 1

    # Sanitized config 검증
    config_name = [n for n in names if n.endswith("config_sanitized.json")][0]
    config_data = json.loads(tar.extractfile(config_name).read().decode("utf-8"))
    assert config_data["inject_token"] == "***REDACTED***"
    assert config_data["secret_api_key"] == "***REDACTED***"

    # Telemetry records 검증
    records_name = [n for n in names if n.endswith("telemetry_records.jsonl")][0]
    rec_lines = tar.extractfile(records_name).read().decode("utf-8").strip().splitlines()
    assert len(rec_lines) >= 2


def test_support_bundle_share_link_tracking(tmp_path: Path):
    s = StubSettings(db_file=tmp_path / "claire.db", data_dir=tmp_path)
    _seed_db(s.db_file)
    _seed_telemetry(s.data_dir)

    # 공유 링크 토큰 생성
    conn = dbm.connect(s.db_file)
    share_token = dbm.create_doc_share(conn, "doc_test_123")
    conn.close()
    assert share_token and len(share_token) == 16

    # 1. 공유 링크 URL로 번들 생성 (/p?s=token)
    share_url = f"https://example.com/p?s={share_token}"
    info = create_support_bundle(s, days=1, target=share_url)
    assert info.target_doc_id == "doc_test_123"
    assert info.target_matched_by == "share_token"

    # 번들 아카이브 내 tracked_document 검증
    dctx = zstd.ZstdDecompressor()
    decompressed_data = dctx.decompress(info.filepath.read_bytes(), max_output_size=50_000_000)
    tar = tarfile.open(fileobj=io.BytesIO(decompressed_data), mode="r:")
    names = tar.getnames()

    assert any(n.endswith("tracked_document/target_resolution.json") for n in names)
    assert any(n.endswith("tracked_document/document_detail.json") for n in names)
    assert any(n.endswith("tracked_document/extractions.json") for n in names)
    assert any(n.endswith("tracked_document/inbox_record.json") for n in names)
    assert any(n.endswith("tracked_document/telemetry_history.jsonl") for n in names)

    res_name = [n for n in names if n.endswith("tracked_document/target_resolution.json")][0]
    res_data = json.loads(tar.extractfile(res_name).read().decode("utf-8"))
    assert res_data["document_id"] == "doc_test_123"
    assert res_data["matched_by"] == "share_token"
    assert res_data["is_from_share_token"] is True
    assert "latest_summary" in res_data

    doc_detail_name = [n for n in names if n.endswith("tracked_document/document_detail.json")][0]
    doc_detail_data = json.loads(tar.extractfile(doc_detail_name).read().decode("utf-8"))
    assert "extractions" in doc_detail_data
    assert "latest_summary" in doc_detail_data

    # 2. shares_index.json에 생성된 공유 링크가 인덱싱되어 있는지 확인
    shares_name = [n for n in names if n.endswith("pipeline/shares_index.json")][0]
    shares_data = json.loads(tar.extractfile(shares_name).read().decode("utf-8"))
    assert any(item["token"] == share_token for item in shares_data)


def test_support_bundle_auto_purge_after_6_hours(tmp_path: Path):
    s = StubSettings(db_file=tmp_path / "claire.db", data_dir=tmp_path)
    _seed_db(s.db_file)

    now = time.time()
    info = create_support_bundle(s, days=1, now_epoch=now)
    assert info.filepath.is_file()

    # 현재 시점에서는 활성 상태로 조회됨
    active = list_active_support_bundles(s.data_dir, now_epoch=now)
    assert len(active) == 1
    assert active[0]["bundle_id"] == info.bundle_id

    bundle = get_support_bundle(s.data_dir, info.token, now_epoch=now)
    assert bundle is not None

    # 5시간 59분 경과: 아직 만료되지 않음
    almost_expired = now + (SUPPORT_BUNDLE_TTL_SECONDS - 60)
    assert get_support_bundle(s.data_dir, info.token, now_epoch=almost_expired) is not None
    assert info.filepath.is_file()

    # 6시간 1초 경과: 만료 시점 도달
    expired_time = now + SUPPORT_BUNDLE_TTL_SECONDS + 1
    # get_support_bundle 호출 시 만료를 감지하고 파일을 자동 파기함
    purged_bundle = get_support_bundle(s.data_dir, info.token, now_epoch=expired_time)
    assert purged_bundle is None
    assert not info.filepath.is_file()

    # active 목록에서도 제거됨
    active_after = list_active_support_bundles(s.data_dir, now_epoch=expired_time)
    assert len(active_after) == 0

    # purge_expired_bundles 직접 호출 테스트
    info2 = create_support_bundle(s, days=1, now_epoch=now)
    assert info2.filepath.is_file()
    purged_count = purge_expired_bundles(s.data_dir, now_epoch=now + SUPPORT_BUNDLE_TTL_SECONDS + 10)
    assert purged_count >= 1
    assert not info2.filepath.is_file()


def test_support_bundle_api_endpoints(tmp_path: Path):
    s = StubSettings(db_file=tmp_path / "claire.db", data_dir=tmp_path)
    _seed_db(s.db_file)
    _seed_telemetry(s.data_dir)

    app = server.create_app(s)
    with TestClient(app, base_url=s.public_url) as client:
        # 1. 인증 없는 POST /support/bundle -> 404 (Claire의 fail-closed 보안 설계)
        res = client.post("/support/bundle", json={"days": 1})
        assert res.status_code == 404


        # 2. 정상 생성 POST /support/bundle (Owner 헤더)
        res = client.post("/support/bundle", headers=OWNER_HEADERS, json={"days": 1})
        assert res.status_code == 200
        data = res.json()
        assert "bundle_id" in data
        assert "token" in data
        assert "download_url" in data
        token = data["token"]

        # 3. 보관 기한 초과 days 요청 -> 400 Bad Request
        res = client.post("/support/bundle", headers=OWNER_HEADERS, json={"days": 40})
        assert res.status_code == 400
        assert "cannot exceed telemetry retention limit" in res.json()["error"]

        # 4. GET /support/bundle?token={token} 정상 다운로드 (Public/Token 기반)
        res = client.get(f"/support/bundle?token={token}")
        assert res.status_code == 200
        assert res.headers["content-type"] == "application/zstd"
        assert "attachment; filename=" in res.headers["content-disposition"]
        assert len(res.content) > 0

        # 5. 잘못된 토큰 -> 404
        res = client.get("/support/bundle?token=non_existent_token_123456789")
        assert res.status_code == 404

        # 6. 토큰 누락 -> 404
        res = client.get("/support/bundle")
        assert res.status_code == 404


def test_support_bundle_cli(tmp_path: Path, monkeypatch, capsys):
    db_file = tmp_path / "claire.db"
    _seed_db(db_file)
    _seed_telemetry(tmp_path)

    monkeypatch.setenv("CLAIRE_DB_PATH", str(db_file))
    monkeypatch.setenv("CLAIRE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CLAIRE_INJECT_TOKEN", OWNER_TOKEN)

    parser = build_parser()

    # 1. 번들 생성 CLI
    args = parser.parse_args(["support-bundle", "--days", "1"])
    ret = args.func(args)
    assert ret == 0
    out = capsys.readouterr().out
    assert "Support Bundle 생성 완료" in out
    assert "zstd 압축" in out

    # 2. 번들 목록 조회 CLI
    args_list = parser.parse_args(["support-bundle", "--list"])
    ret = args_list.func(args_list)
    assert ret == 0
    out_list = capsys.readouterr().out
    assert "Bundle ID" in out_list

    # 3. 보관 기한 초과 CLI (days=35) -> 에러 코드 2
    args_invalid = parser.parse_args(["support-bundle", "--days", "35"])
    ret = args_invalid.func(args_invalid)
    assert ret == 2
    err_out = capsys.readouterr().err
    assert "cannot exceed telemetry retention limit" in err_out

    # 4. 파기 CLI
    args_purge = parser.parse_args(["support-bundle", "--purge"])
    ret = args_purge.func(args_purge)
    assert ret == 0
    out_purge = capsys.readouterr().out
    assert "파기 완료" in out_purge
