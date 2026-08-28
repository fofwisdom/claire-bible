"""CLI 스마트 타깃 리졸버 및 명령어별 대상 식별 안전성 테스트."""

from __future__ import annotations

import sqlite3
import pytest

from claire.cli import build_parser, cmd_doc_title, cmd_watch
from claire.config import Settings
from claire.ontology.base import Document
from claire.store import db as dbm


def _setup_test_db(tmp_path) -> tuple[sqlite3.Connection, str]:
    db_file = tmp_path / "claire.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)

    # 2개의 샘플 문서 생성
    conn.execute(
        """
        INSERT INTO documents (id, url, canonical_url, title, raw_text, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "doc-sha-11111111",
            "https://blog.example.com/posts/architecture-2026?utm_source=rss",
            "https://blog.example.com/posts/architecture-2026",
            "System Architecture Overview 2026",
            "Architecture details...",
            1000.0,
        ),
    )
    conn.execute(
        """
        INSERT INTO documents (id, url, canonical_url, title, raw_text, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "doc-sha-22222222",
            "https://news.example.com/releases/v2",
            "https://news.example.com/releases/v2",
            "Release Notes v2.0",
            "Release notes content...",
            2000.0,
        ),
    )
    conn.commit()
    return conn, str(db_file)


def test_resolve_document_targets_priority(tmp_path):
    """4단계 우선순위에 따른 대상 탐색 동작 검증."""
    conn, _ = _setup_test_db(tmp_path)

    # 1. ID 완전 일치
    res_id = dbm.resolve_document_targets(conn, "doc-sha-11111111")
    assert len(res_id) == 1
    assert res_id[0]["id"] == "doc-sha-11111111"
    assert res_id[0]["matched_by"] == "id"

    # 2. 공유 링크 (?s=token) 및 16자리 공유 토큰
    token = dbm.create_doc_share(conn, "doc-sha-11111111")
    res_token = dbm.resolve_document_targets(conn, token)
    assert len(res_token) == 1
    assert res_token[0]["id"] == "doc-sha-11111111"
    assert res_token[0]["is_from_share_token"] is True

    res_share_url = dbm.resolve_document_targets(conn, f"https://myclaire.local/p?s={token}")
    assert len(res_share_url) == 1
    assert res_share_url[0]["id"] == "doc-sha-11111111"
    assert res_share_url[0]["is_from_share_token"] is True

    # 3. URL 및 Canonical URL (프로토콜 누락 도메인 포함)
    res_url = dbm.resolve_document_targets(conn, "blog.example.com/posts/architecture-2026")
    assert len(res_url) == 1
    assert res_url[0]["id"] == "doc-sha-11111111"
    assert res_url[0]["matched_by"] == "url"

    # 4. 제목 부분 검색 (Fallback)
    res_kw = dbm.resolve_document_targets(conn, "Architecture")
    assert len(res_kw) == 1
    assert res_kw[0]["id"] == "doc-sha-11111111"
    assert res_kw[0]["matched_by"] == "pattern"

    conn.close()


def test_resolve_single_document_target_guard(tmp_path):
    """단건 전용 리졸버의 모호성 탐지 및 거부 검증."""
    conn, _ = _setup_test_db(tmp_path)

    # 단건 매칭 -> 성공
    single = dbm.resolve_single_document_target(conn, "doc-sha-22222222")
    assert single is not None
    assert single["id"] == "doc-sha-22222222"

    # 양쪽 문서에 공통으로 걸릴 수 있는 검색어 ("example.com") -> 다건 매칭으로 거부
    with pytest.raises(ValueError) as exc:
        dbm.resolve_single_document_target(conn, "example.com")
    assert "여러 문서(2건)가 일치합니다" in str(exc.value)

    # 존재하지 않는 대상 -> None
    none_res = dbm.resolve_single_document_target(conn, "not-exist-query")
    assert none_res is None

    conn.close()


def test_cli_watch_and_doc_title_smart_targets(tmp_path, monkeypatch, capsys):
    """watch 및 doc-title 명령어에서 URL로 대상 지정 및 단건 갱신 검증."""
    from claire.config import get_settings
    get_settings.cache_clear()

    conn, db_file = _setup_test_db(tmp_path)
    conn.close()

    monkeypatch.setenv("CLAIRE_DB_PATH", db_file)
    get_settings.cache_clear()
    parser = build_parser()

    # 1. URL로 doc-title 변경
    args_title = parser.parse_args(["doc-title", "blog.example.com/posts/architecture-2026", "New Architecture 2027"])
    rc = cmd_doc_title(args_title)
    assert rc == 0
    cap = capsys.readouterr()
    assert "제목 갱신 완료: doc-sha-11111111 → 'New Architecture 2027'" in cap.out

    # 2. URL로 watch 설정
    args_watch = parser.parse_args(["watch", "news.example.com/releases/v2", "--on", "--interval-days", "7"])
    rc = cmd_watch(args_watch)
    assert rc == 0
    cap = capsys.readouterr()
    assert "watch 설정: [doc-sha-2222]" in cap.out
    assert "enabled=True" in cap.out

    # DB 반영 확인
    conn2 = dbm.connect(db_file)
    r1 = dbm.get_document_row(conn2, "doc-sha-11111111")
    assert r1["title"] == "New Architecture 2027"
    r2 = dbm.get_document_row(conn2, "doc-sha-22222222")
    assert r2["watch_enabled"] == 1
    assert r2["watch_interval"] == 7 * 86400
    conn2.close()
