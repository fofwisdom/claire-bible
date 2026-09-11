"""멀티 테마 환경에서의 Support Bundle 생성, 대상 문서 식별 및 진단 수집 테스트."""

from __future__ import annotations

import io
import json
import sqlite3
import tarfile
from pathlib import Path
from typing import Any

import pytest
import zstandard as zstd

from claire.config import Settings
from claire.ontology.base import Document, Entity, Relation
from claire.store import db as dbm
from claire.store.theme import ThemeManager
from claire.support_bundle import create_support_bundle


def _seed_doc(
    db_path: str | Path,
    doc_id: str,
    title: str,
    url: str,
    share_token: str | None = None,
) -> None:
    conn = dbm.connect(Path(db_path))
    dbm.init_db(conn)
    doc = Document(
        id=doc_id,
        url=url,
        canonical_url=url,
        title=title,
        raw_text=f"Raw text for {title}",
        summary=f"Summary for {title}",
        source_type="video",
        content_hash=f"hash_{doc_id}",
    )
    dbm.insert_document(conn, doc)

    # Entity / Relation
    e = Entity(
        id=f"ent_{doc_id}",
        type="Concept",
        name=f"Entity {title}",
        sources=[doc_id],
    )
    dbm.upsert_entity(conn, e)

    if share_token:
        import time

        conn.execute(
            "INSERT INTO doc_shares(token, document_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (share_token, doc_id, time.time(), None),
        )
        conn.commit()

    conn.close()


def _read_tar_zst(archive_path: Path) -> dict[str, bytes]:
    dctx = zstd.ZstdDecompressor()
    with open(archive_path, "rb") as f_in:
        with dctx.stream_reader(f_in) as reader:
            with tarfile.open(fileobj=reader, mode="r|") as tar:
                members: dict[str, bytes] = {}
                for member in tar:
                    if member.isfile():
                        f = tar.extractfile(member)
                        if f:
                            members[member.name] = f.read()
                return members


def test_support_bundle_resolves_theme_document_by_share_url(tmp_path):
    """테마 1에 존재하는 공유 링크를 target으로 Support Bundle 생성 시 정상 인식 및 패키징 검증."""
    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    data_dir.mkdir(parents=True)
    vault_dir.mkdir(parents=True)

    db_path = data_dir / "claire.db"
    settings = Settings(
        CLAIRE_DB_PATH=str(db_path),
        CLAIRE_VAULT_PATH=str(vault_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_MULTI_THEME=True,
        CLAIRE_PUBLIC_URL="https://cb.netspheres.org",
    )

    # 1. 기본 테마(0) DB 초기화
    conn0 = dbm.connect(db_path)
    dbm.init_db(conn0)
    conn0.close()

    # 2. 테마 1 생성
    tm = ThemeManager(base_settings=settings)
    theme1 = tm.define_theme(label="스타레일 테마", icon="🚂")
    theme1_db = Path(theme1.db_path)

    # 3. 테마 1에만 문서 및 공유 토큰 적재
    doc_id = "doc_079d7e4df7ac"
    share_token = "xnvm57jmgyhpby5b"
    share_url = f"https://cb.netspheres.org/p?s={share_token}"
    _seed_doc(
        theme1_db,
        doc_id=doc_id,
        title="로빈 서머레토 캐릭터 PV",
        url="https://www.youtube.com/watch?v=2GCE286miGs",
        share_token=share_token,
    )

    # 4. 공유 URL을 target으로 Support Bundle 생성
    info = create_support_bundle(settings, days=1, target=share_url)

    assert info.target_doc_id == doc_id
    assert info.target_matched_by == "share_token"
    assert info.target_theme_id == 1
    assert info.filepath.is_file()

    # 5. 생성된 아카이브 내용물 상세 검증
    files = _read_tar_zst(info.filepath)
    root_prefix = f"support_bundle_{info.bundle_id[-8:]}"

    # 5-1. manifest.json
    manifest = json.loads(files[f"{root_prefix}/manifest.json"].decode("utf-8"))
    assert manifest["target"]["document_id"] == doc_id
    assert manifest["target"]["theme_id"] == 1
    assert manifest["target"]["theme_label"] == "스타레일 테마"

    # 5-2. tracked_document/target_resolution.json
    res = json.loads(files[f"{root_prefix}/tracked_document/target_resolution.json"].decode("utf-8"))
    assert res["document_id"] == doc_id
    assert res["theme_id"] == 1
    assert res["is_from_share_token"] is True

    # 5-3. tracked_document/document_detail.json (테마 1 DB에서 정상 추출)
    detail = json.loads(files[f"{root_prefix}/tracked_document/document_detail.json"].decode("utf-8"))
    assert detail["document"] is not None
    assert detail["document"]["id"] == doc_id
    assert detail["document"]["title"] == "로빈 서머레토 캐릭터 PV"
    assert len(detail["shares"]) == 1
    # document_detail.json은 sanitize_sensitive_data가 적용되어 token 필드가 마스킹됨
    assert detail["shares"][0]["token"] == "***REDACTED***"

    # 5-4. pipeline/shares_index.json (테마 1의 공유 링크도 포함)
    shares = json.loads(files[f"{root_prefix}/pipeline/shares_index.json"].decode("utf-8"))
    found_share = next((s for s in shares if s["document_id"] == doc_id), None)
    assert found_share is not None
    assert found_share["token"] == "***REDACTED***"
    assert len(found_share["token_sha256"]) == 64
    assert found_share["document_id"] == doc_id
    assert found_share["theme_id"] == 1

    # 5-5. pipeline/db_integrity.json (멀티 테마 무결성 검사 포함)
    integrity = json.loads(files[f"{root_prefix}/pipeline/db_integrity.json"].decode("utf-8"))
    assert integrity["claire_db_quick_check"] == "ok"
    assert "themes" in integrity
    assert "1" in integrity["themes"]
    assert integrity["themes"]["1"]["quick_check"] == "ok"
    assert integrity["themes"]["1"]["counts"]["documents"] == 1


def test_support_bundle_resolves_theme_document_by_doc_id(tmp_path):
    """테마 1에 존재하는 doc_id를 target으로 넘겼을 때 정상 인식 검증."""
    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    data_dir.mkdir(parents=True)
    vault_dir.mkdir(parents=True)

    db_path = data_dir / "claire.db"
    settings = Settings(
        CLAIRE_DB_PATH=str(db_path),
        CLAIRE_VAULT_PATH=str(vault_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_MULTI_THEME=True,
    )

    conn0 = dbm.connect(db_path)
    dbm.init_db(conn0)
    conn0.close()

    tm = ThemeManager(base_settings=settings)
    theme1 = tm.define_theme(label="연구자료", icon="🔬")
    theme1_db = Path(theme1.db_path)

    doc_id = "doc_research_999"
    _seed_doc(
        theme1_db,
        doc_id=doc_id,
        title="양자 컴퓨팅 연구 논문",
        url="https://arxiv.org/abs/2609.99999",
    )

    info = create_support_bundle(settings, days=1, target=doc_id)
    assert info.target_doc_id == doc_id
    assert info.target_matched_by == "id"
    assert info.target_theme_id == 1


def test_support_bundle_target_not_observed_is_explicit(tmp_path):
    """미관측 target도 전역 진단 번들을 만들되 임의 문서로 해석하지 않는다."""
    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    data_dir.mkdir(parents=True)
    vault_dir.mkdir(parents=True)

    db_path = data_dir / "claire.db"
    settings = Settings(
        CLAIRE_DB_PATH=str(db_path),
        CLAIRE_VAULT_PATH=str(vault_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_MULTI_THEME=True,
    )

    conn0 = dbm.connect(db_path)
    dbm.init_db(conn0)
    conn0.close()

    non_existent = "https://cb.netspheres.org/p?s=non_existent_token"
    info = create_support_bundle(settings, days=1, target=non_existent)
    assert info.target_doc_id is None
    assert info.target_resolution_status == "not_observed"


def test_support_bundle_survives_corrupt_theme_registry(tmp_path):
    """레지스트리가 손상돼도 기본 DB와 오류 증거를 포함한 번들은 생성한다."""
    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    data_dir.mkdir(parents=True)
    vault_dir.mkdir(parents=True)
    db_path = data_dir / "claire.db"
    settings = Settings(
        CLAIRE_DB_PATH=str(db_path),
        CLAIRE_VAULT_PATH=str(vault_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_MULTI_THEME=True,
    )
    conn = dbm.connect(db_path)
    dbm.init_db(conn)
    conn.close()
    registry_path = data_dir / "themes.json"
    registry_path.write_text("{not-json", encoding="utf-8")

    info = create_support_bundle(settings, target="doc_missing")

    assert info.filepath.is_file()
    assert info.target_resolution_status == "collector_error"
    assert registry_path.read_text(encoding="utf-8") == "{not-json"
    files = _read_tar_zst(info.filepath)
    root_prefix = f"support_bundle_{info.bundle_id[-8:]}"
    warnings = json.loads(
        files[f"{root_prefix}/diagnostics/collector_warnings.json"].decode("utf-8")
    )
    warning_codes = {item["code"] for item in warnings}
    assert "THEME_REGISTRY_UNAVAILABLE" in warning_codes
    assert "TARGET_RESOLUTION_FAILED" in warning_codes
    health = json.loads(files[f"{root_prefix}/pipeline/health.json"].decode("utf-8"))
    assert health["liveness"]["ok"] is False
    assert health["health"]["ok"] is False
    integrity = json.loads(
        files[f"{root_prefix}/pipeline/db_integrity.json"].decode("utf-8")
    )
    assert integrity["claire_db_quick_check"] == "ok"


def test_support_bundle_single_theme_mode(tmp_path):
    """CLAIRE_MULTI_THEME=False 인 단일 테마 모드에서의 하위 호환성 검증."""
    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    data_dir.mkdir(parents=True)
    vault_dir.mkdir(parents=True)

    db_path = data_dir / "claire.db"
    settings = Settings(
        CLAIRE_DB_PATH=str(db_path),
        CLAIRE_VAULT_PATH=str(vault_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_MULTI_THEME=False,
    )

    doc_id = "doc_single_001"
    token = "23456789abcdefgh"
    _seed_doc(
        db_path,
        doc_id=doc_id,
        title="단일 모드 테스트",
        url="https://example.com/single",
        share_token=token,
    )

    info = create_support_bundle(settings, days=1, target=token)
    assert info.target_doc_id == doc_id
    assert info.target_theme_id == 0

    files = _read_tar_zst(info.filepath)
    root_prefix = f"support_bundle_{info.bundle_id[-8:]}"
    shares = json.loads(files[f"{root_prefix}/pipeline/shares_index.json"].decode("utf-8"))
    item = next(s for s in shares if s["document_id"] == doc_id)
    assert item["token"] == "***REDACTED***"
    assert len(item["token_sha256"]) == 64
