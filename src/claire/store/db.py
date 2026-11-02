"""SQLite 정본 스토어 — 스키마 + 마이그레이션 + 기본 CRUD.

정본은 이 단일 파일. 벡터는 store/vectors.py(sqlite-vec auto / brute fallback), 키워드는 FTS5.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import time
from pathlib import Path

from ..ids import ID_SALT_BYTES, generate_id_salt, legacy_timestamp_id
from ..ontology.base import Document, Entity, Relation

SCHEMA_VERSION = 15
SCHEMA_LINEAGE = "claire-bible/common"
RETIRED_SCHEMA_VERSIONS = frozenset({12})
_RETIRED_SUPPORT_SCHEMA_VERSION = 12
_RETIRED_SUPPORT_RECOVERY_TARGET = 11
logger = logging.getLogger(__name__)

# v15 이관이 만드는 관리자 계정(설계 §8.2–§8.4). `store.accounts.ADMIN_ACCOUNT_ID`가 이 값을 다시 내보낸다.
ADMIN_ACCOUNT_ID = 1
# 관리자 행 public_id의 상위 48비트(밀리초). 이관을 다시 실행해도 같은 번호가 나오도록 고정한다(A3).
# 값은 설계 결정일 2026-11-02T00:00:00Z이며, 번호가 2^56보다 크다(§7.2·§7.3).
ADMIN_LEGACY_PUBLIC_ID_UNIX_MS = 1_793_577_600_000

_LOCAL_SUPPORT_V12_COLUMNS = {
    "ingest_attempts": (
        "id", "inbox_id", "attempt_number", "status", "document_id", "error",
        "recorded_at",
    ),
    "fetch_attempts": (
        "id", "trace_id", "inbox_id", "document_id", "stage_order", "stage",
        "started_at", "duration_ms", "status", "http_status", "input_url",
        "effective_url", "content_type", "response_bytes", "usable", "guard_error",
        "error_type", "error_message", "metadata", "snapshot_path",
        "snapshot_sha256", "snapshot_original_bytes", "snapshot_stored_bytes",
        "snapshot_truncated",
    ),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seq INTEGER UNIQUE,
    email TEXT UNIQUE,
    display_name TEXT,
    role TEXT DEFAULT 'user',
    status TEXT NOT NULL DEFAULT 'active',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    approved_at REAL,
    delete_after REAL,
    quota_json TEXT
);

CREATE TABLE IF NOT EXISTS themes (
    id INTEGER PRIMARY KEY,
    seq INTEGER UNIQUE,
    label TEXT NOT NULL,
    description TEXT DEFAULT '',
    icon TEXT DEFAULT '📁',
    db_path TEXT DEFAULT '',
    is_default INTEGER DEFAULT 0,
    is_public INTEGER DEFAULT 1,
    is_collaborator_accessible INTEGER DEFAULT 1,
    default_focus TEXT DEFAULT '',
    fqdn TEXT DEFAULT '',
    ga_measurement_id TEXT DEFAULT '',
    sorcerer TEXT DEFAULT '',
    presentation_enabled INTEGER DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS theme_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    theme_id INTEGER NOT NULL REFERENCES themes(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'member',
    created_at REAL NOT NULL,
    UNIQUE(theme_id, user_id)
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    theme_id INTEGER NOT NULL DEFAULT 0 REFERENCES themes(id) ON DELETE CASCADE,
    url TEXT,
    canonical_url TEXT,
    title TEXT,
    author TEXT,
    published_at TEXT,
    fetched_at REAL,
    raw_text TEXT,
    source_type TEXT,
    content_hash TEXT,
    lang TEXT,
    partial INTEGER DEFAULT 0,
    meta TEXT,
    minhash TEXT,
    detail TEXT,
    detail_format TEXT DEFAULT 'md',
    detail_html TEXT,
    seen INTEGER DEFAULT 1,
    watch_enabled INTEGER,
    watch_interval REAL,
    last_watched_at REAL,
    watch_reason TEXT,
    pinned INTEGER DEFAULT 0,
    hidden INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents(content_hash);
CREATE INDEX IF NOT EXISTS idx_documents_canon ON documents(canonical_url);

CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    theme_id INTEGER NOT NULL DEFAULT 0 REFERENCES themes(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    norm_name TEXT NOT NULL,
    aliases TEXT,
    props TEXT,
    observations TEXT,
    sources TEXT,
    provisional INTEGER DEFAULT 0,
    created_at REAL,
    updated_at REAL
);
CREATE INDEX IF NOT EXISTS idx_entities_norm ON entities(norm_name);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);

CREATE TABLE IF NOT EXISTS relations (
    id TEXT PRIMARY KEY,
    theme_id INTEGER NOT NULL DEFAULT 0 REFERENCES themes(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    props TEXT,
    sources TEXT,
    confidence REAL DEFAULT 1.0,
    provisional INTEGER DEFAULT 0,
    created_at REAL,
    UNIQUE(theme_id, type, source_id, target_id)
);
CREATE INDEX IF NOT EXISTS idx_relations_src ON relations(source_id);
CREATE INDEX IF NOT EXISTS idx_relations_tgt ON relations(target_id);

CREATE TABLE IF NOT EXISTS embeddings (
    owner_id TEXT,
    theme_id INTEGER NOT NULL DEFAULT 0 REFERENCES themes(id) ON DELETE CASCADE,
    dim INTEGER,
    vector BLOB,
    model TEXT,
    updated_at REAL,
    PRIMARY KEY (owner_id, theme_id)
);

CREATE TABLE IF NOT EXISTS proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    theme_id INTEGER NOT NULL DEFAULT 0 REFERENCES themes(id) ON DELETE CASCADE,
    kind TEXT,                   -- 'entity_type' | 'relation_type'
    proposed TEXT,
    context TEXT,
    document_id TEXT,
    created_at REAL
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    theme_id INTEGER NOT NULL DEFAULT 0 REFERENCES themes(id) ON DELETE CASCADE,
    payload TEXT,
    source_type TEXT,
    status TEXT DEFAULT 'pending',   -- pending | done | error
    error TEXT,
    created_at REAL,
    updated_at REAL
);

-- [재적재 Layer 1] inbound 원본 보관. 받은 그대로 append-only, 영구.
-- 이것만으로 전체 파이프라인을 처음부터 재생할 수 있다(작고 저렴).
CREATE TABLE IF NOT EXISTS raw_inbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    theme_id INTEGER NOT NULL DEFAULT 0 REFERENCES themes(id) ON DELETE CASCADE,
    received_at REAL,
    source TEXT,                  -- 'telegram' | 'cli' | 'test'
    user_id INTEGER,
    chat_id INTEGER,
    kind TEXT,                    -- 'text' | 'url' | 'file' | 'document'
    payload TEXT,                 -- 원문 텍스트/URL (그대로)
    file_name TEXT,              -- document 인 경우 원본 파일명
    file_ref TEXT,               -- 보관된 원본 파일 경로(data/raw/files/...)
    document_id TEXT,            -- 처리 결과 document 연결(있으면)
    status TEXT DEFAULT 'received',  -- received | done | duplicate | error | failed
    error TEXT,
    -- [자동복구] error 행을 recover-loop 가 지수백오프로 재적재. attempts 가 상한에
    -- 도달하면 status='failed'(영구실패)로 굳혀 무한재시도를 막는다.
    attempts INTEGER DEFAULT 0,
    last_attempt REAL,
    next_retry_at REAL
);
CREATE INDEX IF NOT EXISTS idx_raw_inbox_status ON raw_inbox(status);

-- [재적재 LLM tier] 모델이 반환한 raw 출력 보관. 후처리만 바뀌면 재호출 없이 재생.
CREATE TABLE IF NOT EXISTS extractions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    theme_id INTEGER NOT NULL DEFAULT 0 REFERENCES themes(id) ON DELETE CASCADE,
    document_id TEXT,
    provider TEXT,
    model TEXT,
    prompt_version TEXT,
    raw_response TEXT,           -- 모델이 반환한 원본 JSON 문자열
    created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_extractions_doc ON extractions(document_id);

-- [복원 메커니즘] 갱신 대기열. 알고리즘/스크래퍼가 바뀌면 대상 문서를 여기 등록하고,
-- refresh-loop(주기 실행)이 원본 payload 로 재fetch→재추출→문서 in-place 갱신.
CREATE TABLE IF NOT EXISTS refresh_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    theme_id INTEGER NOT NULL DEFAULT 0 REFERENCES themes(id) ON DELETE CASCADE,
    document_id TEXT,             -- 갱신 대상 문서(없으면 신규 적재)
    payload TEXT NOT NULL,        -- 재적재용 원본(url/text) — raw_inbox/documents 에서
    reason TEXT,                  -- 'thin' | 'algo-change' | 'manual'
    status TEXT DEFAULT 'pending',-- pending | done | nochange | error
    attempts INTEGER DEFAULT 0,
    last_attempt REAL,
    error TEXT,
    created_at REAL,
    updated_at REAL,
    UNIQUE(theme_id, document_id)
);
CREATE INDEX IF NOT EXISTS idx_refresh_status ON refresh_queue(status);

-- [웹 UI 인증] 텔레그램 버튼 승인 기반 세션. API 가 nonce 발급(버튼 전송) → 봇이
-- 콜백에서 승인+세션토큰 기록 → 웹이 poll 로 토큰 수령. 2프로세스(API·봇) 공유 상태라
-- in-memory 가 아니라 이 테이블에 둔다.
CREATE TABLE IF NOT EXISTS auth_sessions (
    nonce TEXT PRIMARY KEY,
    session_token TEXT,
    approved INTEGER DEFAULT 0,
    created_at REAL,
    expires_at REAL,
    scope TEXT DEFAULT 'owner'
);

-- [문서 공유 핫링크] 세션 토큰(claire_session, 전체 UI 인증)과 **완전 분리**된, 문서 1개의
-- 읽기 뷰만 비인증으로 열어주는 공유 토큰. 유출돼도 그 문서 1개만 노출(읽기전용). 발급은
-- 인증된 UI 에서만(/share), 열람은 게이트 예외(/p?s=token)로 토큰 자체가 인증을 대신한다.
CREATE TABLE IF NOT EXISTS doc_shares (
    token TEXT PRIMARY KEY,
    theme_id INTEGER NOT NULL DEFAULT 0 REFERENCES themes(id) ON DELETE CASCADE,
    document_id TEXT NOT NULL,
    created_at REAL,
    expires_at REAL          -- NULL=무기한. 향후 만료 정책 시 사용.
);
CREATE INDEX IF NOT EXISTS idx_doc_shares_doc ON doc_shares(document_id);

-- [OAuth 2.1 인가 서버] MCP 클라이언트(Gemini 등)와의 연동을 위한 독립 OAuth 상태 테이블.
CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id TEXT PRIMARY KEY,
    client_secret TEXT,
    client_name TEXT,
    redirect_uris TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_codes (
    code TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    code_challenge TEXT NOT NULL,
    code_challenge_method TEXT DEFAULT 'S256',
    scope TEXT DEFAULT 'readonly',
    expires_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_tokens (
    token TEXT PRIMARY KEY,
    refresh_token TEXT UNIQUE,
    client_id TEXT NOT NULL,
    scope TEXT DEFAULT 'readonly',
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_oauth_tokens_refresh ON oauth_tokens(refresh_token);

CREATE TABLE IF NOT EXISTS oauth_auth_requests (
    nonce TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    code_challenge TEXT NOT NULL,
    code_challenge_method TEXT DEFAULT 'S256',
    scope TEXT DEFAULT 'readonly',
    state TEXT,
    approved INTEGER DEFAULT 0,
    code TEXT,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);

-- [1홉 자동확장] 적재한 문서에서 따라갈 링크를 LLM 이 선별→판정→적재하는 백그라운드
-- 대기열. refresh_queue 와 같은 패턴(전용 expand-loop 컨테이너가 주기 처리).
CREATE TABLE IF NOT EXISTS expand_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    theme_id INTEGER NOT NULL DEFAULT 0 REFERENCES themes(id) ON DELETE CASCADE,
    document_id TEXT,              -- 확장 출발(부모) 문서
    status TEXT DEFAULT 'pending', -- pending | done | error
    attempts INTEGER DEFAULT 0,
    last_attempt REAL,
    error TEXT,
    result TEXT,                   -- 처리 요약 JSON(선별/적재/스킵 수)
    created_at REAL,
    updated_at REAL,
    UNIQUE(theme_id, document_id)
);
CREATE INDEX IF NOT EXISTS idx_expand_status ON expand_queue(status);

-- [주기 크롤링] watch 대상 문서가 재크롤 시 '내용이 바뀌었을 때' 변경 '전' 원문을 시계열로
-- 보존(데이터 보존 협약 — 벤치/순위처럼 변하는 콘텐츠의 추세를 살림). 그래프(엔티티)는
-- 최신 in-place 갱신만 하고, 과거 상태는 여기 원문으로만 남는다(추세는 나중에 스냅샷 종합).
CREATE TABLE IF NOT EXISTS document_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    theme_id INTEGER NOT NULL DEFAULT 0 REFERENCES themes(id) ON DELETE CASCADE,
    document_id TEXT NOT NULL,     -- 어느 문서의 과거 상태인지
    captured_at REAL,              -- 이 스냅샷(=변경 직전 상태)을 보존한 시각
    content_hash TEXT,             -- 그 시점 본문 해시
    title TEXT,
    raw_text TEXT,                 -- 변경 전 원문(시계열 보존)
    meta TEXT
);
CREATE INDEX IF NOT EXISTS idx_snapshots_doc ON document_snapshots(document_id, captured_at);

-- [Asciidoctor reveal.js 프레젠테이션 AOT 메타데이터 및 캐시 추적]
CREATE TABLE IF NOT EXISTS document_presentations (
    document_id TEXT,
    theme_id INTEGER NOT NULL DEFAULT 0 REFERENCES themes(id) ON DELETE CASCADE,
    content_hash TEXT NOT NULL,
    adoc_hash TEXT,
    cache_key TEXT NOT NULL,
    theme TEXT DEFAULT 'night',
    transition TEXT DEFAULT 'slide',
    slide_count INTEGER DEFAULT 0,
    file_path TEXT NOT NULL,
    file_size INTEGER DEFAULT 0,
    status TEXT DEFAULT 'ready',
    error_message TEXT,
    presentation_adoc TEXT,
    authoring_provider TEXT,
    authoring_model TEXT,
    prompt_version TEXT DEFAULT 'pres-v2',
    compose_duration_ms INTEGER DEFAULT 0,
    compile_duration_ms INTEGER DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY(document_id, theme_id),
    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_doc_pres_status ON document_presentations(status);
CREATE INDEX IF NOT EXISTS idx_doc_pres_key ON document_presentations(cache_key);

-- FTS5 키워드 인덱스 (엔티티 이름 + 관찰). content 테이블과 분리된 standalone FTS.
CREATE VIRTUAL TABLE IF NOT EXISTS entities_fts USING fts5(
    entity_id UNINDEXED,
    name,
    body
);

-- [소각 툼스톤] 폐기/소각된 오염 문서의 지문(URL/해시)을 초경량 보존하여 재수집 및 재생산 영구 차단.
CREATE TABLE IF NOT EXISTS purged_tombstones (
    id TEXT,
    theme_id INTEGER NOT NULL DEFAULT 0 REFERENCES themes(id) ON DELETE CASCADE,
    url TEXT,
    canonical_url TEXT,
    content_hash TEXT,
    reason TEXT NOT NULL,
    purged_at REAL NOT NULL,
    PRIMARY KEY(id, theme_id)
);
CREATE INDEX IF NOT EXISTS idx_tombstones_canon ON purged_tombstones(canonical_url);
CREATE INDEX IF NOT EXISTS idx_tombstones_hash ON purged_tombstones(content_hash);

-- [신디케이션 연합/연계 계정 및 발행 파이프라인]
CREATE TABLE IF NOT EXISTS syndication_accounts (
    id TEXT PRIMARY KEY,
    theme_id INTEGER NOT NULL DEFAULT 0 REFERENCES themes(id) ON DELETE CASCADE,
    protocol TEXT NOT NULL,
    server_url TEXT NOT NULL,
    software TEXT,
    server_version TEXT,
    missing_features_json TEXT DEFAULT '[]',
    handle TEXT NOT NULL,
    remote_account_id TEXT,
    auth_kind TEXT NOT NULL,
    scopes TEXT NOT NULL,
    token_enc BLOB,
    client_registration_json TEXT,
    publish_enabled INTEGER DEFAULT 0,
    max_chars INTEGER,
    streaming_url TEXT,
    health_status TEXT DEFAULT 'ok',
    consecutive_failures INTEGER DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(theme_id, protocol, server_url, remote_account_id)
);

CREATE TABLE IF NOT EXISTS syndication_targets (
    id TEXT PRIMARY KEY,
    theme_id INTEGER NOT NULL DEFAULT 0 REFERENCES themes(id) ON DELETE CASCADE,
    protocol TEXT NOT NULL,
    account_id TEXT REFERENCES syndication_accounts(id) ON DELETE CASCADE,
    source_kind TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    title TEXT NOT NULL,
    folder TEXT DEFAULT '',
    poll_interval_sec INTEGER DEFAULT 900,
    auto_ingest_threshold INTEGER,
    default_focus TEXT DEFAULT '',
    etag TEXT,
    last_modified TEXT,
    cursor TEXT,
    health_status TEXT DEFAULT 'ok',
    consecutive_failures INTEGER DEFAULT 0,
    last_polled_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(theme_id, protocol, account_id, source_kind, source_ref)
);

CREATE TABLE IF NOT EXISTS syndication_items (
    id TEXT PRIMARY KEY,
    theme_id INTEGER NOT NULL DEFAULT 0 REFERENCES themes(id) ON DELETE CASCADE,
    target_id TEXT REFERENCES syndication_targets(id) ON DELETE SET NULL,
    via_account_id TEXT REFERENCES syndication_accounts(id) ON DELETE SET NULL,
    protocol TEXT NOT NULL,
    external_id TEXT NOT NULL,
    remote_status_id TEXT,
    at_cid TEXT,
    canonical_url TEXT,
    title TEXT,
    author TEXT,
    raw_content TEXT,
    source_tags_json TEXT DEFAULT '[]',
    content_hash TEXT,
    published_at TEXT,
    collected_at REAL NOT NULL,
    unread INTEGER DEFAULT 1,
    starred INTEGER DEFAULT 0,
    affinity_score REAL DEFAULT 0.0,
    ingest_state TEXT DEFAULT 'unlinked',
    linked_doc_id TEXT,
    outbound_state TEXT DEFAULT 'none',
    UNIQUE(theme_id, protocol, external_id)
);
CREATE INDEX IF NOT EXISTS idx_synd_items_theme_collected ON syndication_items(theme_id, collected_at DESC);
CREATE INDEX IF NOT EXISTS idx_synd_items_theme_canon ON syndication_items(theme_id, canonical_url);
CREATE INDEX IF NOT EXISTS idx_synd_items_theme_doc ON syndication_items(theme_id, linked_doc_id);

CREATE TABLE IF NOT EXISTS syndication_dispatches (
    id TEXT PRIMARY KEY,
    theme_id INTEGER NOT NULL DEFAULT 0 REFERENCES themes(id) ON DELETE CASCADE,
    target_theme_id INTEGER DEFAULT 0,
    item_id TEXT REFERENCES syndication_items(id) ON DELETE SET NULL,
    account_id TEXT REFERENCES syndication_accounts(id) ON DELETE CASCADE,
    document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
    target_url TEXT,
    mode TEXT NOT NULL DEFAULT 'post',
    status TEXT NOT NULL DEFAULT 'pending',
    remote_ref TEXT,
    payload TEXT,
    error TEXT,
    dispatched_by_user_id INTEGER,
    applied_focus TEXT,
    dispatched_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_synd_dispatches_theme ON syndication_dispatches(theme_id, status);
"""


def checkpoint_database(src: str | Path, dest: str | Path) -> Path:
    """내부 안전장치용 SQLite checkpoint를 단일 파일로 복제(VACUUM INTO).

    이는 웹 병합 같은 앱 내부 파괴 작업의 근거리 checkpoint일 뿐, data/raw와 복원 절차를 포함하는 운영 backup이 아니다. 운영 backup은 cb-manuscript가 소유한다. `VACUUM INTO`는 WAL을 반영한 트랜잭션 일관 DB snapshot을 만든다.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(src)
    try:
        conn.execute("VACUUM INTO ?", (str(dest),))
    finally:
        conn.close()
    return dest


def reset_graph(conn: sqlite3.Connection, theme_id: int | None = None) -> None:
    """추출 산출물(엔티티/관계/임베딩/추출/제안/FTS)을 비운다 — documents·raw_inbox· artifact 는 보존. 저장된 raw_text 로부터 그래프를 깨끗이 재구축(reextract)할 때 사용.

    문서/원본을 남기므로 재추출의 입력은 그대로다. 파괴적이라 호출 전 백업 권장 (CLI reextract 가 강제). FTS 는 트리거가 아니라 수동 관리라 함께 비운다.
    """
    if theme_id is not None:
        conn.execute(
            "DELETE FROM entities_fts WHERE entity_id IN (SELECT id FROM entities WHERE theme_id=?)",
            (theme_id,),
        )
        for tbl in ("entities", "relations", "embeddings", "extractions", "proposals"):
            conn.execute(f"DELETE FROM {tbl} WHERE theme_id=?", (theme_id,))
    else:
        for tbl in ("entities", "entities_fts", "relations", "embeddings",
                    "extractions", "proposals"):
            conn.execute(f"DELETE FROM {tbl}")
    conn.commit()


def connect(db_path: str | Path) -> sqlite3.Connection:
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    # 봇 프로세스와 inject API 프로세스가 같은 DB 에 쓰므로 잠금 대기 허용.
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def connect_existing(
    db_path: str | Path, *, readonly: bool = False
) -> sqlite3.Connection:
    """이미 초기화된 DB를 열되 journal mode나 파일 시스템을 변경하지 않는다.

    API 요청 경로에서 사용한다. ``mode=rw``/``mode=ro``로 누락된 DB를 암묵적으로 만들지 않으며, WAL 설정과 schema migration은 프로세스 시작 시 한 번만 수행한다.
    """

    mode = "ro" if readonly else "rw"
    uri = Path(db_path).resolve().as_uri() + f"?mode={mode}"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")
    if readonly:
        conn.execute("PRAGMA query_only=ON;")
    return conn


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    """기존 테이블에 컬럼이 없으면 ADD COLUMN(idempotent 마이그레이션).

    `CREATE TABLE IF NOT EXISTS` 는 이미 존재하는 테이블에 새 컬럼을 더하지 못하므로,
    기존 운영 DB(데이터 보존)에 스키마 변경을 안전하게 적용하는 단일 경로.
    """
    if column in _column_names(conn, table):
        return
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    except sqlite3.OperationalError as e:
        # 다중 프로세스(bot/api/refresh/recover)가 동시에 init_db→마이그레이션 시
        # 한쪽만 ALTER 에 성공하고 나머지는 'duplicate column'(TOCTOU). 이미 추가된
        # 것이므로 무시한다 = 멱등. 그 외 오류는 진짜 문제이니 전파.
        if "duplicate column" not in str(e).lower():
            raise


def _migrate(conn: sqlite3.Connection) -> None:
    """버전 무관하게 멱등으로 적용 가능한 컬럼 추가(낮은 버전 DB 업그레이드)."""
    # v4: raw_inbox 자동복구 메타데이터. (컬럼 의존 인덱스는 컬럼 추가 뒤에 생성)
    _ensure_column(conn, "raw_inbox", "attempts", "INTEGER DEFAULT 0")
    _ensure_column(conn, "raw_inbox", "last_attempt", "REAL")
    _ensure_column(conn, "raw_inbox", "next_retry_at", "REAL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_inbox_retry "
                 "ON raw_inbox(status, next_retry_at)")
    # v5: 문서 가독 렌더(detail) — 짧은 summary 와 별개의 '여러 단락' 상세 재구성.
    # 구조화 추출과 독립된 별도 LLM 호출로 채운다(그래프 rebuild 없이 백필 가능).
    _ensure_column(conn, "documents", "detail", "TEXT")
    # v6: 근사 중복 탐지용 MinHash 서명(JSON). content_hash/canonical_url 을 비껴가는
    # "같은 글 다른 입구"(arxiv 버전 접미사 등)를 잡는 3차 dedup 게이트. 백필=dedup-scan.
    _ensure_column(conn, "documents", "minhash", "TEXT")
    # v7: 주기 크롤링 + 미열람 표시.
    #  - seen: 문서를 한 번이라도 열어봤는지(0=미열람/unread, 1=봄). 기존 문서는 DEFAULT 1
    #    (='이제부터' 적용 — 과거 적재분은 이미 본 것으로 간주). 신규 적재는 insert 시 0 명시.
    #  - watch_*: 변하는 콘텐츠(벤치/순위 등) 주기 재크롤 대상 여부·주기·이력. enabled NULL=미판단
    #    (LLM 자동판단 전), 0=off, 1=on. interval=재크롤 주기(초). reason='llm:...'|'manual'.
    _ensure_column(conn, "documents", "seen", "INTEGER DEFAULT 1")
    _ensure_column(conn, "documents", "watch_enabled", "INTEGER")
    _ensure_column(conn, "documents", "watch_interval", "REAL")
    _ensure_column(conn, "documents", "last_watched_at", "REAL")
    _ensure_column(conn, "documents", "watch_reason", "TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_watch "
                 "ON documents(watch_enabled, last_watched_at)")
    # v8: 문서 즐겨찾기(좌측 목록 상단 고정) + 숨기기(목록에서만 제외 — 그래프 엔티티/관계는
    # 그대로 유지, 사용자 결정). 둘 다 기본 0(안 켜짐).
    _ensure_column(conn, "documents", "pinned", "INTEGER DEFAULT 0")
    _ensure_column(conn, "documents", "hidden", "INTEGER DEFAULT 0")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_hidden ON documents(hidden)")
    # v9: 세션 scope(owner|readonly) — /webro 로 발급하는 읽기전용 웹 링크(텔레그램에서
    # 클릭해 바로 열리는 링크가 필요하다는 요구; 기존 CLAIRE_READONLY_TOKEN 은 헤더 전용이라
    # URL 링크로 못 씀). 기존 행은 DEFAULT 'owner' 로 자동 채워져 기존 /web 세션 동작 그대로.
    _ensure_column(conn, "auth_sessions", "scope", "TEXT DEFAULT 'owner'")
    # v10: 문서 detail 가독 렌더링 포맷 (md: 마크다운, adoc: AsciiDoc). 기본값 'md'.
    _ensure_column(conn, "documents", "detail_format", "TEXT DEFAULT 'md'")
    # v11: 문서 AOT 사전 컴파일된 HTML (Antora 스타일 사전 렌더링).
    _ensure_column(conn, "documents", "detail_html", "TEXT")
    # v11: 소각 툼스톤 테이블 (오염 문서 재유입 영구 차단).
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS purged_tombstones (
            id TEXT PRIMARY KEY,
            url TEXT,
            canonical_url TEXT,
            content_hash TEXT,
            reason TEXT NOT NULL,
            purged_at REAL NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tombstones_canon ON purged_tombstones(canonical_url)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tombstones_hash ON purged_tombstones(content_hash)")
    # v13: Asciidoctor reveal.js 프레젠테이션 AOT 메타데이터 및 캐시 추적 테이블
    # v14: 프레젠테이션 LLM 저작(Composition) 소스코드 및 메타데이터 지원
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS document_presentations (
            document_id TEXT PRIMARY KEY,
            content_hash TEXT NOT NULL,
            adoc_hash TEXT,
            cache_key TEXT NOT NULL,
            theme TEXT DEFAULT 'night',
            transition TEXT DEFAULT 'slide',
            slide_count INTEGER DEFAULT 0,
            file_path TEXT NOT NULL,
            file_size INTEGER DEFAULT 0,
            status TEXT DEFAULT 'ready',
            error_message TEXT,
            presentation_adoc TEXT,
            authoring_provider TEXT,
            authoring_model TEXT,
            prompt_version TEXT DEFAULT 'pres-v2',
            compose_duration_ms INTEGER DEFAULT 0,
            compile_duration_ms INTEGER DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_doc_pres_status ON document_presentations(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_doc_pres_key ON document_presentations(cache_key)")
    # v14/v15 컬럼 멱등 마이그레이션
    _ensure_column(conn, "document_presentations", "presentation_adoc", "TEXT")
    _ensure_column(conn, "document_presentations", "adoc_hash", "TEXT")
    _ensure_column(conn, "document_presentations", "authoring_provider", "TEXT")
    _ensure_column(conn, "document_presentations", "authoring_model", "TEXT")
    _ensure_column(conn, "document_presentations", "prompt_version", "TEXT DEFAULT 'pres-v2'")
    _ensure_column(conn, "document_presentations", "compose_duration_ms", "INTEGER DEFAULT 0")

    # v15 / pres-v2: Purge all legacy pre-commit / fallback presentation records and disk files
    _pres_purge_key = "presentation_legacy_purged_v2"
    if _stored_meta_value(conn, _pres_purge_key) != "1":
        table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='document_presentations'"
        ).fetchone()
        if table_exists is not None:
            try:
                rows = conn.execute(
                    "SELECT file_path FROM document_presentations"
                ).fetchall()
                for r in rows:
                    fp_str = r["file_path"] if r else None
                    if fp_str:
                        fp = Path(fp_str)
                        try:
                            if fp.is_file():
                                fp.unlink(missing_ok=True)
                            adoc_fp = fp.with_suffix(".adoc")
                            if adoc_fp.is_file():
                                adoc_fp.unlink(missing_ok=True)
                        except OSError:
                            pass
                conn.execute("DELETE FROM document_presentations")
            except Exception:
                pass
        try:
            db_row = conn.execute("PRAGMA database_list").fetchone()
            db_file_str = db_row["file"] if db_row else None
            if db_file_str:
                pres_dir = Path(db_file_str).parent / "presentations"
                if pres_dir.is_dir():
                    for f in pres_dir.glob("*.html"):
                        f.unlink(missing_ok=True)
                    for f in pres_dir.glob("*.adoc"):
                        f.unlink(missing_ok=True)
        except Exception:
            pass
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            (_pres_purge_key, "1"),
        )
    # v14: users, themes, theme_members, and syndication infrastructure
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seq INTEGER UNIQUE,
            email TEXT UNIQUE,
            display_name TEXT,
            role TEXT DEFAULT 'user',
            status TEXT NOT NULL DEFAULT 'active',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            approved_at REAL,
            delete_after REAL,
            quota_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS themes (
            id INTEGER PRIMARY KEY,
            seq INTEGER UNIQUE,
            label TEXT NOT NULL,
            description TEXT DEFAULT '',
            icon TEXT DEFAULT '📁',
            db_path TEXT DEFAULT '',
            is_default INTEGER DEFAULT 0,
            is_public INTEGER DEFAULT 1,
            is_collaborator_accessible INTEGER DEFAULT 1,
            default_focus TEXT DEFAULT '',
            fqdn TEXT DEFAULT '',
            ga_measurement_id TEXT DEFAULT '',
            sorcerer TEXT DEFAULT '',
            presentation_enabled INTEGER DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS theme_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            theme_id INTEGER NOT NULL REFERENCES themes(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role TEXT NOT NULL DEFAULT 'member',
            created_at REAL NOT NULL,
            UNIQUE(theme_id, user_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS syndication_accounts (
            id TEXT PRIMARY KEY,
            theme_id INTEGER NOT NULL DEFAULT 0 REFERENCES themes(id) ON DELETE CASCADE,
            protocol TEXT NOT NULL,
            server_url TEXT NOT NULL,
            software TEXT,
            server_version TEXT,
            missing_features_json TEXT DEFAULT '[]',
            handle TEXT NOT NULL,
            remote_account_id TEXT,
            auth_kind TEXT NOT NULL,
            scopes TEXT NOT NULL,
            token_enc BLOB,
            client_registration_json TEXT,
            publish_enabled INTEGER DEFAULT 0,
            max_chars INTEGER,
            streaming_url TEXT,
            health_status TEXT DEFAULT 'ok',
            consecutive_failures INTEGER DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE(theme_id, protocol, server_url, remote_account_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS syndication_targets (
            id TEXT PRIMARY KEY,
            theme_id INTEGER NOT NULL DEFAULT 0 REFERENCES themes(id) ON DELETE CASCADE,
            protocol TEXT NOT NULL,
            account_id TEXT REFERENCES syndication_accounts(id) ON DELETE CASCADE,
            source_kind TEXT NOT NULL,
            source_ref TEXT NOT NULL,
            title TEXT NOT NULL,
            folder TEXT DEFAULT '',
            poll_interval_sec INTEGER DEFAULT 900,
            auto_ingest_threshold INTEGER,
            default_focus TEXT DEFAULT '',
            etag TEXT,
            last_modified TEXT,
            cursor TEXT,
            health_status TEXT DEFAULT 'ok',
            consecutive_failures INTEGER DEFAULT 0,
            last_polled_at REAL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE(theme_id, protocol, account_id, source_kind, source_ref)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS syndication_items (
            id TEXT PRIMARY KEY,
            theme_id INTEGER NOT NULL DEFAULT 0 REFERENCES themes(id) ON DELETE CASCADE,
            target_id TEXT REFERENCES syndication_targets(id) ON DELETE SET NULL,
            via_account_id TEXT REFERENCES syndication_accounts(id) ON DELETE SET NULL,
            protocol TEXT NOT NULL,
            external_id TEXT NOT NULL,
            remote_status_id TEXT,
            at_cid TEXT,
            canonical_url TEXT,
            title TEXT,
            author TEXT,
            raw_content TEXT,
            source_tags_json TEXT DEFAULT '[]',
            content_hash TEXT,
            published_at TEXT,
            collected_at REAL NOT NULL,
            unread INTEGER DEFAULT 1,
            starred INTEGER DEFAULT 0,
            affinity_score REAL DEFAULT 0.0,
            ingest_state TEXT DEFAULT 'unlinked',
            linked_doc_id TEXT,
            outbound_state TEXT DEFAULT 'none',
            UNIQUE(theme_id, protocol, external_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS syndication_dispatches (
            id TEXT PRIMARY KEY,
            theme_id INTEGER NOT NULL DEFAULT 0 REFERENCES themes(id) ON DELETE CASCADE,
            target_theme_id INTEGER DEFAULT 0,
            item_id TEXT REFERENCES syndication_items(id) ON DELETE SET NULL,
            account_id TEXT REFERENCES syndication_accounts(id) ON DELETE CASCADE,
            document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
            target_url TEXT,
            mode TEXT NOT NULL DEFAULT 'post',
            status TEXT NOT NULL DEFAULT 'pending',
            remote_ref TEXT,
            payload TEXT,
            error TEXT,
            dispatched_by_user_id INTEGER,
            applied_focus TEXT,
            dispatched_at REAL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )

    # v14: Add theme_id column to all knowledge, queue, and cache tables
    _theme_partitioned_tables = (
        "documents",
        "entities",
        "relations",
        "embeddings",
        "raw_inbox",
        "extractions",
        "proposals",
        "jobs",
        "refresh_queue",
        "expand_queue",
        "document_snapshots",
        "document_presentations",
        "doc_shares",
        "purged_tombstones",
        "syndication_accounts",
        "syndication_targets",
        "syndication_items",
        "syndication_dispatches",
    )
    for tbl in _theme_partitioned_tables:
        _ensure_column(conn, tbl, "theme_id", "INTEGER NOT NULL DEFAULT 0")

    _ensure_column(conn, "syndication_dispatches", "target_theme_id", "INTEGER DEFAULT 0")
    _ensure_column(conn, "syndication_dispatches", "dispatched_by_user_id", "INTEGER")
    _ensure_column(conn, "syndication_dispatches", "applied_focus", "TEXT")

    # v14: Insert default theme #0 into themes if empty
    conn.execute(
        "INSERT OR IGNORE INTO themes (id, seq, label, description, is_default, is_public, is_collaborator_accessible, created_at, updated_at) "
        "VALUES (0, 0, '기본 지식베이스', '일반 수집 자료 및 기본 지식', 1, 1, 0, unixepoch(), unixepoch())"
    )

    # v14: Composite indexes with theme_id
    conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_theme_hash ON documents(theme_id, content_hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_theme_canon ON documents(theme_id, canonical_url)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_theme_watch ON documents(theme_id, watch_enabled, last_watched_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_theme_hidden ON documents(theme_id, hidden)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entities_theme_norm ON entities(theme_id, norm_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entities_theme_type ON entities(theme_id, type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_relations_theme_src ON relations(theme_id, source_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_relations_theme_tgt ON relations(theme_id, target_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_theme ON embeddings(theme_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_inbox_theme_status ON raw_inbox(theme_id, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_inbox_theme_retry ON raw_inbox(theme_id, status, next_retry_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_extractions_theme_doc ON extractions(theme_id, document_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_proposals_theme_doc ON proposals(theme_id, document_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_theme_status ON jobs(theme_id, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_refresh_theme_status ON refresh_queue(theme_id, status)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_refresh_queue_theme_doc ON refresh_queue(theme_id, document_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_expand_theme_status ON expand_queue(theme_id, status)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_expand_queue_theme_doc ON expand_queue(theme_id, document_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_theme_doc ON document_snapshots(theme_id, document_id, captured_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_doc_pres_theme_status ON document_presentations(theme_id, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_doc_pres_theme_key ON document_presentations(theme_id, cache_key)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_doc_pres_theme_doc ON document_presentations(document_id, theme_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_doc_shares_theme_doc ON doc_shares(theme_id, document_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tombstones_theme_canon ON purged_tombstones(theme_id, canonical_url)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tombstones_theme_hash ON purged_tombstones(theme_id, content_hash)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tombstones_theme_id ON purged_tombstones(id, theme_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_synd_dispatches_theme ON syndication_dispatches(theme_id, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_synd_items_theme_collected ON syndication_items(theme_id, collected_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_synd_items_theme_canon ON syndication_items(theme_id, canonical_url)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_synd_items_theme_doc ON syndication_items(theme_id, linked_doc_id)")

    # documents.meta -> focus 자동 마이그레이션 및 레거시 키 영구 소각
    _legacy_key = "dir" + "ective"
    conn.execute(
        f"""
        UPDATE documents
        SET meta = json_set(
            json_remove(meta, '$.{_legacy_key}'),
            '$.focus',
            json_extract(meta, '$.{_legacy_key}')
        )
        WHERE meta IS NOT NULL
          AND json_valid(meta) = 1
          AND json_extract(meta, '$.{_legacy_key}') IS NOT NULL
          AND json_extract(meta, '$.focus') IS NULL
        """
    )
    conn.execute(
        f"""
        UPDATE documents
        SET meta = json_remove(meta, '$.{_legacy_key}')
        WHERE meta IS NOT NULL
          AND json_valid(meta) = 1
          AND json_extract(meta, '$.{_legacy_key}') IS NOT NULL
        """
    )
    for row in conn.execute(
        "SELECT id, meta FROM documents WHERE meta LIKE ?",
        (f'%"{_legacy_key}"%',),
    ).fetchall():
        try:
            m = json.loads(row["meta"] or "{}")
            if _legacy_key in m:
                if "focus" not in m or not m["focus"]:
                    m["focus"] = m[_legacy_key]
                del m[_legacy_key]
                conn.execute(
                    "UPDATE documents SET meta=? WHERE id=?",
                    (json.dumps(m, ensure_ascii=False), row["id"]),
                )
        except Exception:
            pass

    _migrate_v15_accounts(conn)


# v15에서 account_id를 받는 자격증명 테이블. NULL은 v15 이전 행이며 관리자(1)의 것이다(§8.3).
_V15_CREDENTIAL_TABLES = ("auth_sessions", "oauth_codes", "oauth_tokens", "oauth_auth_requests")


def _check_single_default_theme_per_account(conn: sqlite3.Connection) -> None:
    """``idx_themes_account_default``를 만들 수 없는 상태면 이관 전에 실패한다(C1).

    계정마다 ``is_default=1`` 테마가 하나여야 한다. 둘 이상이면 어느 쪽이 기본인지 추측하지 않는다.
    """
    account_expr = "COALESCE(account_id, ?)" if "account_id" in _column_names(conn, "themes") else "?"
    rows = conn.execute(
        f"SELECT {account_expr} AS acc, GROUP_CONCAT(id) AS ids, COUNT(*) AS n "
        "FROM themes WHERE is_default = 1 GROUP BY acc HAVING COUNT(*) > 1",
        (ADMIN_ACCOUNT_ID,),
    ).fetchall()
    if rows:
        detail = "; ".join(f"account_id={r['acc']} themes.id=[{r['ids']}]" for r in rows)
        raise RuntimeError(
            "schema v15 migration stopped: more than one default theme (themes.is_default=1) "
            f"for one account: {detail}. Keep is_default=1 on exactly one theme per account, "
            "then run the migration again."
        )


def _ensure_id_salt(conn: sqlite3.Connection, now: float) -> bytes:
    """``instance_settings.id_salt``가 없을 때만 만든다(hex). 있으면 그대로 읽는다."""
    conn.execute(
        "INSERT OR IGNORE INTO instance_settings (key, value, updated_at, updated_by) VALUES ('id_salt', ?, ?, NULL)",
        (generate_id_salt().hex(), now),
    )
    row = conn.execute("SELECT value FROM instance_settings WHERE key='id_salt'").fetchone()
    try:
        salt = bytes.fromhex(str(row["value"]))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("instance_settings.id_salt is not valid hex") from exc
    if len(salt) != ID_SALT_BYTES:
        raise RuntimeError(f"instance_settings.id_salt must be {ID_SALT_BYTES} bytes, got {len(salt)}")
    return salt


def _migrate_v15_accounts(conn: sqlite3.Connection) -> None:
    """v15 (설계 §13.1): 계정, 퇴역 이름, 인스턴스 설정, 테마·자격증명의 계정 귀속. 멱등(A3).

    v14의 ``users`` 테이블은 그대로 두고 쓰지 않는다.
    """
    _check_single_default_theme_per_account(conn)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id INTEGER UNIQUE NOT NULL,
            username TEXT UNIQUE,
            role TEXT NOT NULL CHECK (role IN ('admin', 'member')),
            status TEXT NOT NULL,
            display_name TEXT NOT NULL DEFAULT '',
            profile_public INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            activated_at REAL,
            delete_after REAL,
            updated_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_single_admin ON accounts(role) WHERE role = 'admin'"
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS accounts_username_immutable
        BEFORE UPDATE OF username ON accounts
        WHEN OLD.username IS NOT NULL AND NEW.username IS NOT OLD.username
        BEGIN SELECT RAISE(ABORT, 'username is immutable'); END
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS username_tombstones (
            username TEXT PRIMARY KEY,
            account_id INTEGER NOT NULL,
            was_public INTEGER NOT NULL DEFAULT 0,
            retired_at REAL NOT NULL,
            released_at REAL,
            released_by INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS instance_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at REAL NOT NULL,
            updated_by INTEGER
        )
        """
    )

    _ensure_column(conn, "themes", "account_id", "INTEGER REFERENCES accounts(id)")
    _ensure_column(conn, "auth_sessions", "account_id", "INTEGER")
    _ensure_column(conn, "auth_sessions", "authenticated_at", "REAL")
    for tbl in ("oauth_codes", "oauth_tokens", "oauth_auth_requests"):
        _ensure_column(conn, tbl, "account_id", "INTEGER")

    now = time.time()
    salt = _ensure_id_salt(conn, now)

    # 관리자 행(id 1)이 없을 때만 만든다. 여러 프로세스가 동시에 이관해도 INSERT OR IGNORE 뒤 검증으로 하나만 남는다.
    conn.execute(
        "INSERT OR IGNORE INTO accounts "
        "(id, public_id, username, role, status, display_name, profile_public, created_at, updated_at) "
        "VALUES (?, ?, NULL, 'admin', 'setup_required', '', 0, ?, ?)",
        (
            ADMIN_ACCOUNT_ID,
            legacy_timestamp_id(
                "accounts", salt=salt, unix_ms=ADMIN_LEGACY_PUBLIC_ID_UNIX_MS, row_id=ADMIN_ACCOUNT_ID
            ),
            now,
            now,
        ),
    )
    admin = conn.execute("SELECT role FROM accounts WHERE id=?", (ADMIN_ACCOUNT_ID,)).fetchone()
    if admin is None or admin["role"] != "admin":
        raise RuntimeError(
            f"schema v15 migration stopped: accounts.id={ADMIN_ACCOUNT_ID} must be the admin account "
            f"(found {dict(admin) if admin is not None else 'no row'})"
        )

    conn.execute("UPDATE themes SET account_id=? WHERE account_id IS NULL", (ADMIN_ACCOUNT_ID,))
    for tbl in _V15_CREDENTIAL_TABLES:
        conn.execute(f"UPDATE {tbl} SET account_id=? WHERE account_id IS NULL", (ADMIN_ACCOUNT_ID,))

    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_themes_account_default ON themes(account_id) WHERE is_default = 1"
    )


def _stored_meta_value(conn: sqlite3.Connection, key: str) -> str | None:
    meta_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
    ).fetchone()
    if meta_exists is None:
        return None
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return str(row["value"]) if row is not None else None


def stored_schema_version(conn: sqlite3.Connection) -> int | None:
    """Return an existing schema version without creating or changing anything."""
    raw = _stored_meta_value(conn, "schema_version")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"invalid schema_version: {raw!r}") from exc


def stored_schema_lineage(conn: sqlite3.Connection) -> str | None:
    """Return the Git-independent common schema lineage without mutation."""
    return _stored_meta_value(conn, "schema_lineage")


def _validate_migration_source(
    version: int | None, lineage: str | None
) -> None:
    if version in RETIRED_SCHEMA_VERSIONS:
        raise RuntimeError(
            "schema v12 is retired; run the origin v12-to-v11 withdrawal "
            "before migrating to the common v13 schema"
        )
    if version is not None and version > SCHEMA_VERSION:
        raise RuntimeError(
            "database schema is newer than this code: "
            f"actual={version}, expected<={SCHEMA_VERSION}"
        )
    if lineage is not None and lineage != SCHEMA_LINEAGE:
        raise RuntimeError(
            "database schema lineage does not match this project: "
            f"actual={lineage!r}, expected={SCHEMA_LINEAGE!r}"
        )
    if version is not None and version >= 13 and lineage is None:
        raise RuntimeError(
            f"schema v{version} is missing its common lineage marker; "
            "refusing to guess compatibility"
        )


def require_current_schema(conn: sqlite3.Connection) -> int:
    """Validate the current common version and lineage without mutation."""
    version = stored_schema_version(conn)
    lineage = stored_schema_lineage(conn)
    if version != SCHEMA_VERSION:
        raise RuntimeError(
            "schema_version mismatch: "
            f"actual={version}, expected={SCHEMA_VERSION}"
        )
    if lineage != SCHEMA_LINEAGE:
        raise RuntimeError(
            "schema_lineage mismatch: "
            f"actual={lineage!r}, expected={SCHEMA_LINEAGE!r}"
        )
    return version


def _table_columns(conn: sqlite3.Connection, table: str) -> tuple[str, ...]:
    return tuple(str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})"))


def _is_local_support_v12(conn: sqlite3.Connection) -> bool:
    return all(
        _table_columns(conn, table) == columns
        for table, columns in _LOCAL_SUPPORT_V12_COLUMNS.items()
    )


def _database_path(conn: sqlite3.Connection) -> Path:
    row = conn.execute("PRAGMA database_list").fetchone()
    if row is None or not row["file"]:
        raise RuntimeError("cannot resolve SQLite database path for v12 rollback")
    return Path(str(row["file"])).resolve()


def _write_durable_export_file(path: Path, payload: bytes) -> str:
    """새 export 파일을 0600으로 쓰고 데이터 블록을 디스크에 동기화한다."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    path.chmod(0o600)
    return hashlib.sha256(payload).hexdigest()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _export_local_support_v12(conn: sqlite3.Connection) -> Path:
    """잠긴 v12 행을 권한 제한 JSONL로 내구성 있게 보존한다."""
    if not conn.in_transaction:
        raise RuntimeError("v12 export requires an active database transaction")
    db_path = _database_path(conn)
    path_key = hashlib.sha256(str(db_path).encode("utf-8")).hexdigest()[:12]
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    migrations_dir = db_path.parent / "raw" / "migrations"
    export_dir = (
        migrations_dir /
        f"support-diagnostics-v12-to-v11-{stamp}-{path_key}-{secrets.token_hex(4)}"
    )
    migrations_dir.mkdir(parents=True, exist_ok=True)
    export_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
    export_dir.chmod(0o700)
    try:
        counts: dict[str, int] = {}
        files: dict[str, dict[str, str | int]] = {}
        for table in _LOCAL_SUPPORT_V12_COLUMNS:
            rows = [
                dict(row)
                for row in conn.execute(f"SELECT * FROM {table} ORDER BY id")
            ]
            counts[table] = len(rows)
            output = export_dir / f"{table}.jsonl"
            payload = "".join(
                json.dumps(row, ensure_ascii=False) + "\n" for row in rows
            ).encode("utf-8")
            digest = _write_durable_export_file(output, payload)
            files[output.name] = {
                "rows": len(rows),
                "bytes": len(payload),
                "sha256": digest,
            }
        manifest = export_dir / "manifest.json"
        manifest_payload = (
            json.dumps(
                {
                    "migration": "local-support-v12-to-v11",
                    "source_database": db_path.name,
                    "from_schema_version": _RETIRED_SUPPORT_SCHEMA_VERSION,
                    "to_schema_version": _RETIRED_SUPPORT_RECOVERY_TARGET,
                    "created_at": time.time(),
                    "row_counts": counts,
                    "files": files,
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n"
        )
        _write_durable_export_file(manifest, manifest_payload.encode("utf-8"))
        _fsync_directory(export_dir)
        _fsync_directory(migrations_dir)
    except Exception:
        shutil.rmtree(export_dir, ignore_errors=True)
        _fsync_directory(migrations_dir)
        raise
    return export_dir


def _restore_local_support_v12_to_v11(conn: sqlite3.Connection) -> Path:
    """이번 Support Bundle 확장으로 생성된 v12만 보존 후 v11로 되돌린다."""
    try:
        conn.execute("BEGIN IMMEDIATE")
        if stored_schema_version(conn) != _RETIRED_SUPPORT_SCHEMA_VERSION:
            raise RuntimeError("database schema changed during v12 rollback")
        if not _is_local_support_v12(conn):
            raise RuntimeError(
                "database schema v12 does not match the retired Support Bundle "
                "extension; refusing automatic rollback"
            )
        export_dir = _export_local_support_v12(conn)
        conn.execute("DROP INDEX IF EXISTS idx_fetch_attempts_document")
        conn.execute("DROP INDEX IF EXISTS idx_fetch_attempts_inbox")
        conn.execute("DROP INDEX IF EXISTS idx_ingest_attempts_inbox")
        conn.execute("DROP TABLE fetch_attempts")
        conn.execute("DROP TABLE ingest_attempts")
        conn.execute(
            "UPDATE meta SET value=? WHERE key='schema_version'",
            (str(_RETIRED_SUPPORT_RECOVERY_TARGET),),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    logger.warning(
        "Restored local Support Bundle schema v12 to v11; diagnostics exported to %s",
        export_dir,
    )
    return export_dir


def init_db(conn: sqlite3.Connection) -> None:
    existing_version = stored_schema_version(conn)
    existing_lineage = stored_schema_lineage(conn)
    if existing_lineage is not None and existing_lineage != SCHEMA_LINEAGE:
        _validate_migration_source(existing_version, existing_lineage)
    if existing_version == _RETIRED_SUPPORT_SCHEMA_VERSION:
        _restore_local_support_v12_to_v11(conn)
        existing_version = stored_schema_version(conn)
        existing_lineage = stored_schema_lineage(conn)
    _validate_migration_source(existing_version, existing_lineage)

    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.execute(
        "INSERT INTO meta(key, value) VALUES ('schema_lineage', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (SCHEMA_LINEAGE,),
    )
    conn.commit()
    require_current_schema(conn)


def migrate_multi_db_to_single_db(
    conn: sqlite3.Connection,
    data_dir: Path | str,
) -> dict[str, Any]:
    """Migrate multiple physical theme databases to unified single database.

    Follows ASD-STE100 guidelines:
    - Reads theme definitions from themes.json into themes table.
    - Searches for theme directories with claire.db files.
    - Attaches each database and copies table rows with the theme_id set to seq.
    - Renames copied claire.db files to claire.db.migrated.
    """
    base_dir = Path(data_dir)
    themes_json_path = base_dir / "themes.json"
    themes_dir = base_dir / "themes"

    migrated_themes: list[int] = []
    migrated_tables: dict[int, list[str]] = {}

    # 1. Import themes.json if present
    if themes_json_path.is_file():
        try:
            raw = json.loads(themes_json_path.read_text(encoding="utf-8"))
            theme_entries = raw.get("themes", []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
            now = time.time()
            for t in theme_entries:
                raw_id = t.get("id")
                try:
                    tid = int(raw_id)
                except (ValueError, TypeError):
                    tid = int(t.get("seq", 0))
                seq = int(t.get("seq", tid))
                label = str(t.get("label", f"Theme {seq}"))
                desc = str(t.get("description", ""))
                icon = str(t.get("icon", "📁"))
                db_p = str(t.get("db_path", ""))
                is_def = int(bool(t.get("is_default", tid == 0)))
                is_pub = int(bool(t.get("is_public", True)))
                is_collab = int(bool(t.get("is_collaborator_accessible", True)))
                default_focus = str(t.get("default_focus", ""))
                fqdn = str(t.get("fqdn", ""))
                ga_id = str(t.get("ga_measurement_id", t.get("ga_id", "")))
                sorcerer = str(t.get("sorcerer", t.get("knowledge_manager", "")))
                pres_enabled = int(bool(t.get("presentation_enabled", False)))
                c_at = float(t.get("created_at", now))
                u_at = float(t.get("updated_at", now))
                # themes.json 항목에 account_id가 없으면 v15 이전의 관리자 테마다(§13.1).
                # 이미 있는 행의 소유자는 ON CONFLICT에서 바꾸지 않는다.
                owner_id = int(t.get("account_id", ADMIN_ACCOUNT_ID))
                conn.execute(
                    """
                    INSERT INTO themes (
                        id, seq, label, description, icon, db_path, is_default, is_public,
                        is_collaborator_accessible, default_focus, fqdn, ga_measurement_id,
                        sorcerer, presentation_enabled, created_at, updated_at, account_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        seq = excluded.seq,
                        label = excluded.label,
                        description = excluded.description,
                        icon = excluded.icon,
                        db_path = excluded.db_path,
                        is_default = excluded.is_default,
                        is_public = excluded.is_public,
                        is_collaborator_accessible = excluded.is_collaborator_accessible,
                        default_focus = excluded.default_focus,
                        fqdn = excluded.fqdn,
                        ga_measurement_id = excluded.ga_measurement_id,
                        sorcerer = excluded.sorcerer,
                        presentation_enabled = excluded.presentation_enabled,
                        updated_at = excluded.updated_at
                    """,
                    (tid, seq, label, desc, icon, db_p, is_def, is_pub, is_collab,
                     default_focus, fqdn, ga_id, sorcerer, pres_enabled, c_at, u_at, owner_id),
                )
            conn.commit()
        except Exception as e:
            logger.warning("Failed to import themes.json during migration: %s", e)

    # 2. Search themes directory for claire.db files
    if themes_dir.is_dir():
        partitioned_tables = (
            "documents",
            "entities",
            "relations",
            "embeddings",
            "raw_inbox",
            "extractions",
            "proposals",
            "jobs",
            "refresh_queue",
            "expand_queue",
            "document_snapshots",
            "document_presentations",
            "doc_shares",
            "purged_tombstones",
            "syndication_accounts",
            "syndication_targets",
            "syndication_items",
            "syndication_dispatches",
        )

        autoincrement_tables = {
            "raw_inbox",
            "proposals",
            "jobs",
            "extractions",
            "refresh_queue",
            "expand_queue",
            "document_snapshots",
        }

        subdirs = sorted(
            [d for d in themes_dir.iterdir() if d.is_dir()],
            key=lambda p: (0 if p.name.isdigit() else 1, int(p.name) if p.name.isdigit() else p.name),
        )

        for subdir in subdirs:
            src_db_file = subdir / "claire.db"
            if not src_db_file.is_file():
                continue

            if subdir.name.isdigit():
                seq = int(subdir.name)
                row = conn.execute(
                    "SELECT id FROM themes WHERE id=? OR seq=? LIMIT 1",
                    (seq, seq),
                ).fetchone()
                theme_id = int(row["id"]) if row else seq
            else:
                row = conn.execute(
                    "SELECT id, seq FROM themes WHERE label=? OR db_path LIKE ? LIMIT 1",
                    (subdir.name, f"%{subdir.name}%"),
                ).fetchone()
                theme_id = int(row["id"]) if row else (int(row["seq"]) if row else 0)
                seq = int(row["seq"]) if row else theme_id

            # Ensure theme record exists in themes table
            conn.execute(
                """
                INSERT OR IGNORE INTO themes (id, seq, label, description, is_default, is_public, is_collaborator_accessible, created_at, updated_at, account_id)
                VALUES (?, ?, ?, '', ?, 1, 1, unixepoch(), unixepoch(), ?)
                """,
                # v15 이전 물리 테마 DB는 관리자 것이다(§8.4-1).
                (theme_id, seq, f"Theme {seq}" if seq != 0 else "기본 지식베이스", 1 if seq == 0 else 0,
                 ADMIN_ACCOUNT_ID),
            )
            conn.commit()

            # Attach external database
            conn.execute("ATTACH DATABASE ? AS src_db", (str(src_db_file.resolve()),))
            try:
                copied_tables: list[str] = []
                for tbl in partitioned_tables:
                    src_exists = conn.execute(
                        "SELECT 1 FROM src_db.sqlite_master WHERE type='table' AND name=?", (tbl,)
                    ).fetchone()
                    if not src_exists:
                        continue

                    src_cols = {r["name"] for r in conn.execute(f"PRAGMA src_db.table_info({tbl})").fetchall()}
                    main_cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({tbl})").fetchall()}
                    if tbl in autoincrement_tables:
                        common_cols = [c for c in src_cols if c in main_cols and c not in ("theme_id", "id")]
                    else:
                        common_cols = [c for c in src_cols if c in main_cols and c != "theme_id"]
                    if not common_cols:
                        continue

                    cols_str = ", ".join(common_cols)
                    conn.execute(
                        f"INSERT OR IGNORE INTO main.{tbl} (theme_id, {cols_str}) "
                        f"SELECT {theme_id}, {cols_str} FROM src_db.{tbl}"
                    )
                    copied_tables.append(tbl)

                # Sync FTS if entities were imported
                has_fts = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='entities_fts'").fetchone()
                if has_fts:
                    try:
                        src_has_fts = conn.execute("SELECT 1 FROM src_db.sqlite_master WHERE type='table' AND name='entities_fts'").fetchone()
                        if src_has_fts:
                            conn.execute(
                                """
                                INSERT INTO main.entities_fts (entity_id, name, body)
                                SELECT entity_id, name, body FROM src_db.entities_fts
                                WHERE entity_id NOT IN (SELECT entity_id FROM main.entities_fts)
                                """
                            )
                        else:
                            conn.execute(
                                """
                                INSERT INTO main.entities_fts (entity_id, name, body)
                                SELECT e.id, e.name, e.observations
                                FROM src_db.entities e
                                WHERE e.id NOT IN (SELECT entity_id FROM main.entities_fts)
                                """
                            )
                    except Exception as fts_err:
                        logger.warning("FTS sync warning during theme migration: %s", fts_err)

                conn.commit()
                migrated_themes.append(theme_id)
                migrated_tables[theme_id] = copied_tables
                try:
                    conn.execute("PRAGMA src_db.wal_checkpoint(TRUNCATE)")
                except Exception:
                    pass
            finally:
                conn.execute("DETACH DATABASE src_db")

            # Rename copied database file
            migrated_file = subdir / "claire.db.migrated"
            src_db_file.rename(migrated_file)
            wal_file = subdir / "claire.db-wal"
            shm_file = subdir / "claire.db-shm"
            if wal_file.is_file():
                wal_file.unlink(missing_ok=True)
            if shm_file.is_file():
                shm_file.unlink(missing_ok=True)

    # Sync FTS if entities were imported
    has_fts = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='entities_fts'").fetchone()
    if has_fts:
        try:
            conn.execute(
                """
                INSERT INTO entities_fts(entity_id, name, body)
                SELECT e.id, e.name, e.observations
                FROM entities e
                WHERE e.id NOT IN (SELECT entity_id FROM entities_fts)
                """
            )
            conn.commit()
        except Exception:
            pass

    return {
        "migrated_themes": migrated_themes,
        "migrated_tables": migrated_tables,
    }


# --- documents ---

def _document_minhash_json(doc: Document) -> str | None:
    """문서의 (제목+본문) MinHash 서명을 JSON 으로. 토큰 없으면 None."""
    from ..ingest.normalize import minhash_signature

    sig = minhash_signature((doc.title or "") + " " + (doc.raw_text or ""))
    return json.dumps(sig) if sig else None


def insert_document(conn: sqlite3.Connection, doc: Document, theme_id: int = 0) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO documents
        (id,theme_id,url,canonical_url,title,author,published_at,fetched_at,raw_text,
         source_type,content_hash,lang,partial,meta,minhash,seen)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
        (
            doc.id, theme_id, doc.url, doc.canonical_url, doc.title, doc.author,
            doc.published_at, doc.fetched_at, doc.raw_text, doc.source_type,
            doc.content_hash, doc.lang, int(doc.partial), json.dumps(doc.meta),
            _document_minhash_json(doc),
        ),
    )
    conn.commit()


def near_duplicate_document(
    conn: sqlite3.Connection, doc: Document, *,
    threshold: float = 0.90, min_len: int = 500, exclude_id: str | None = None,
    theme_id: int | None = None,
) -> tuple[str, float] | None:
    """근사 중복 게이트(3차): MinHash Jaccard 추정이 임계 이상인 기존 문서를 찾는다.

    content_hash(완전일치)·canonical_url 를 비껴간 "같은 글 다른 입구"를 잡는다. **보수적**(데이터 보존): 짧은(<min_len) 문서·partial 은 양쪽 다 제외해 false-positive 를 막고(특히 x.com 트윗), 임계 0.90 은 실측 마진(진짜중복 0.97+ vs 별개 ≤0.36) 안. 반환: (가장 유사한 문서 id, 추정 유사도) 또는 None.
    """
    from ..ingest.normalize import minhash_estimate, minhash_signature

    if doc.partial or len(doc.raw_text or "") < min_len:
        return None
    sig = minhash_signature((doc.title or "") + " " + (doc.raw_text or ""))
    if not sig:
        return None
    q = "SELECT id, minhash FROM documents WHERE minhash IS NOT NULL AND partial=0 AND length(raw_text) >= ?"
    params: list[Any] = [min_len]
    if theme_id is not None:
        q += " AND theme_id=?"
        params.append(theme_id)
    rows = conn.execute(q, params).fetchall()
    best_id, best_score = None, 0.0
    for r in rows:
        if exclude_id and r["id"] == exclude_id:
            continue
        try:
            other = json.loads(r["minhash"])
        except (TypeError, ValueError):
            continue
        score = minhash_estimate(sig, other)
        if score > best_score:
            best_id, best_score = r["id"], score
    if best_id is not None and best_score >= threshold:
        return best_id, best_score
    return None


def find_document_by_hash(conn: sqlite3.Connection, content_hash: str, theme_id: int | None = None) -> str | None:
    if not content_hash:
        return None
    if theme_id is not None:
        row = conn.execute(
            "SELECT id FROM documents WHERE content_hash=? AND theme_id=? LIMIT 1",
            (content_hash, theme_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT id FROM documents WHERE content_hash=? LIMIT 1", (content_hash,)
        ).fetchone()
    return row["id"] if row else None


def get_document_row(conn: sqlite3.Connection, document_id: str, theme_id: int | None = None) -> sqlite3.Row | None:
    if theme_id is not None:
        return conn.execute(
            "SELECT * FROM documents WHERE id=? AND theme_id=?", (document_id, theme_id)
        ).fetchone()
    return conn.execute(
        "SELECT * FROM documents WHERE id=?", (document_id,)
    ).fetchone()


# --- [주기 크롤링] 스냅샷(시계열) · 열람(seen) · watch 설정 ---

def save_document_snapshot(
    conn: sqlite3.Connection, document_id: str, *, captured_at: float,
    content_hash: str | None, title: str | None, raw_text: str | None,
    meta: dict | None = None, theme_id: int = 0,
) -> None:
    """watch 문서의 '변경 전' 상태를 시계열로 보존(데이터 보존 — 추세 살림)."""
    conn.execute(
        "INSERT INTO document_snapshots(theme_id,document_id,captured_at,content_hash,title,raw_text,meta) "
        "VALUES(?,?,?,?,?,?,?)",
        (theme_id, document_id, captured_at, content_hash, title, raw_text,
         json.dumps(meta) if meta is not None else None),
    )
    conn.commit()


def document_snapshots(
    conn: sqlite3.Connection, document_id: str, limit: int = 0, theme_id: int | None = None,
) -> list[sqlite3.Row]:
    """한 문서의 과거 스냅샷(최신순)."""
    q = ("SELECT id,document_id,captured_at,content_hash,title,raw_text "
         "FROM document_snapshots WHERE document_id=?")
    params: list[Any] = [document_id]
    if theme_id is not None:
        q += " AND theme_id=?"
        params.append(theme_id)
    q += " ORDER BY captured_at DESC"
    if limit:
        q += f" LIMIT {int(limit)}"
    return conn.execute(q, params).fetchall()


def set_document_seen(conn: sqlite3.Connection, document_id: str, seen: bool = True, theme_id: int | None = None) -> None:
    """문서 열람 상태 설정(UI 에서 열어보면 seen=1, watch 갱신 시 0=다시 봐야 함)."""
    if theme_id is not None:
        conn.execute("UPDATE documents SET seen=? WHERE id=? AND theme_id=?",
                     (1 if seen else 0, document_id, theme_id))
    else:
        conn.execute("UPDATE documents SET seen=? WHERE id=?",
                     (1 if seen else 0, document_id))
    conn.commit()


def set_document_watch(
    conn: sqlite3.Connection, document_id: str, *, enabled: bool | None,
    interval: float | None = None, reason: str | None = None, theme_id: int | None = None,
) -> None:
    """watch(주기 재크롤) 설정 — LLM 자동판단 또는 사용자 수동 on/off. enabled None=미판단."""
    where = "WHERE id=?"
    params: list[Any] = [None if enabled is None else (1 if enabled else 0), interval, reason, document_id]
    if theme_id is not None:
        where += " AND theme_id=?"
        params.append(theme_id)
    conn.execute(
        f"UPDATE documents SET watch_enabled=?, watch_interval=COALESCE(?,watch_interval), "
        f"watch_reason=COALESCE(?,watch_reason) {where}",
        params,
    )
    conn.commit()


def watch_due_documents(
    conn: sqlite3.Connection, now: float, *, default_interval: float, limit: int = 0, theme_id: int | None = None,
) -> list[sqlite3.Row]:
    """재크롤할 때가 된 watch 문서: enabled=1 AND (last_watched_at NULL 또는 now - last_watched_at >= interval). interval 없으면 default_interval 적용."""
    q = ("SELECT id, url FROM documents WHERE watch_enabled=1 AND url IS NOT NULL AND ("
         "last_watched_at IS NULL OR ? - last_watched_at >= COALESCE(watch_interval, ?)) ")
    params: list[Any] = [now, default_interval]
    if theme_id is not None:
        q += " AND theme_id=? "
        params.append(theme_id)
    q += "ORDER BY COALESCE(last_watched_at, 0)"
    if limit:
        q += f" LIMIT {int(limit)}"
    return conn.execute(q, params).fetchall()


def mark_document_watched(conn: sqlite3.Connection, document_id: str, when: float, theme_id: int | None = None) -> None:
    """watch 재크롤을 수행한 시각 기록(다음 due 계산 기준)."""
    if theme_id is not None:
        conn.execute("UPDATE documents SET last_watched_at=? WHERE id=? AND theme_id=?", (when, document_id, theme_id))
    else:
        conn.execute("UPDATE documents SET last_watched_at=? WHERE id=?", (when, document_id))
    conn.commit()


def find_document_by_canonical_url(
    conn: sqlite3.Connection, canonical_url: str | None, theme_id: int | None = None,
) -> str | None:
    """같은 canonical_url 의 기존 문서 id(최신 fetched_at). 내용 갱신 감지용."""
    if not canonical_url:
        return None
    if theme_id is not None:
        row = conn.execute(
            "SELECT id FROM documents WHERE canonical_url=? AND theme_id=? ORDER BY fetched_at DESC LIMIT 1",
            (canonical_url, theme_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT id FROM documents WHERE canonical_url=? ORDER BY fetched_at DESC LIMIT 1",
            (canonical_url,),
        ).fetchone()
    return row["id"] if row else None


def _documents_filter(
    since: float | None,
    query: str | None,
    include_hidden: bool = True,
    theme_id: int | None = None,
) -> tuple[str, list]:
    """documents_timeline/documents_count 공용 WHERE 절 빌더."""
    where, params = [], []
    if theme_id is not None:
        where.append("theme_id = ?")
        params.append(theme_id)
    if not include_hidden:
        where.append("hidden = 0")
    if since is not None:
        where.append("fetched_at >= ?")
        params.append(since)
    if query:
        where.append("(title LIKE ? OR url LIKE ?)")
        like = f"%{query}%"
        params.extend([like, like])
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    return where_sql, params


def documents_timeline(
    conn: sqlite3.Connection,
    limit: int = 300,
    *,
    since: float | None = None,
    query: str | None = None,
    include_hidden: bool = True,
    theme_id: int | None = None,
) -> list[sqlite3.Row]:
    """문서를 최신 적재순으로(좌측 문서 패널용). summary 는 호출측에서 붙인다.

    since/query 는 MCP `documents` 툴이 "전체를 다 훑지 않고 좁혀서 찾을" 수 있게 추가된 선택적 필터(기본 None, 기존 웹 UI 호출은 동작 그대로). include_hidden=False 면 hidden=0 인 공개 문서만 조회한다."""
    where_sql, params = _documents_filter(since, query, include_hidden=include_hidden, theme_id=theme_id)
    params.append(limit)
    return conn.execute(
        f"SELECT id, title, url, source_type, fetched_at, seen, watch_enabled, "
        f"pinned, hidden, (detail IS NOT NULL AND trim(detail) != '') AS has_detail, "
        f"coalesce(detail_format, 'md') AS detail_format FROM documents {where_sql} "
        f"ORDER BY fetched_at DESC, id DESC LIMIT ?",
        params,
    ).fetchall()


def documents_count(
    conn: sqlite3.Connection,
    *,
    since: float | None = None,
    query: str | None = None,
    include_hidden: bool = True,
    theme_id: int | None = None,
) -> int:
    """documents_timeline과 동일한 필터의 총 개수(잘림 여부 판단용)."""
    where_sql, params = _documents_filter(since, query, include_hidden=include_hidden, theme_id=theme_id)
    return conn.execute(
        f"SELECT COUNT(*) c FROM documents {where_sql}", params
    ).fetchone()["c"]


def hidden_document_ids(conn: sqlite3.Connection, theme_id: int | None = None) -> set[str]:
    """숨김 처리된(hidden=1) 모든 문서의 ID 집합을 반환한다."""
    if theme_id is not None:
        rows = conn.execute("SELECT id FROM documents WHERE hidden=1 AND theme_id=?", (theme_id,)).fetchall()
    else:
        rows = conn.execute("SELECT id FROM documents WHERE hidden=1").fetchall()
    return {row["id"] for row in rows}


def set_document_pinned(conn: sqlite3.Connection, document_id: str, pinned: bool, theme_id: int | None = None) -> bool:
    """즐겨찾기 토글. 존재하지 않는 id 면 False."""
    if theme_id is not None:
        cur = conn.execute(
            "UPDATE documents SET pinned=? WHERE id=? AND theme_id=?", (1 if pinned else 0, document_id, theme_id))
    else:
        cur = conn.execute(
            "UPDATE documents SET pinned=? WHERE id=?", (1 if pinned else 0, document_id))
    conn.commit()
    return cur.rowcount > 0


def set_document_hidden(conn: sqlite3.Connection, document_id: str, hidden: bool, theme_id: int | None = None) -> bool:
    """숨기기 토글(목록 전용 — 그래프 엔티티/관계는 안 건드림). 존재하지 않는 id 면 False."""
    if theme_id is not None:
        cur = conn.execute(
            "UPDATE documents SET hidden=? WHERE id=? AND theme_id=?", (1 if hidden else 0, document_id, theme_id))
    else:
        cur = conn.execute(
            "UPDATE documents SET hidden=? WHERE id=?", (1 if hidden else 0, document_id))
    conn.commit()
    return cur.rowcount > 0


def set_document_title(conn: sqlite3.Connection, document_id: str, title: str | None, theme_id: int | None = None) -> bool:
    """문서 제목 갱신 및 MinHash 서명 재계산. 존재하지 않는 id 면 False."""
    from ..ingest.normalize import minhash_signature

    if theme_id is not None:
        row = conn.execute("SELECT raw_text FROM documents WHERE id=? AND theme_id=?", (document_id, theme_id)).fetchone()
    else:
        row = conn.execute("SELECT raw_text FROM documents WHERE id=?", (document_id,)).fetchone()
    if row is None:
        return False

    raw_text = row["raw_text"] or ""
    clean_title = (title or "").strip() or None
    sig = minhash_signature((clean_title or "") + " " + raw_text)
    sig_json = json.dumps(sig) if sig else None

    if theme_id is not None:
        cur = conn.execute(
            "UPDATE documents SET title=?, minhash=? WHERE id=? AND theme_id=?",
            (clean_title, sig_json, document_id, theme_id),
        )
    else:
        cur = conn.execute(
            "UPDATE documents SET title=?, minhash=? WHERE id=?",
            (clean_title, sig_json, document_id),
        )
    conn.commit()
    return cur.rowcount > 0


def get_document(conn: sqlite3.Connection, document_id: str, theme_id: int | None = None) -> Document | None:
    """documents 행을 Document 모델로 복원(자동복구의 extract 재시도 등에서 사용)."""
    row = get_document_row(conn, document_id, theme_id=theme_id)
    if row is None:
        return None
    return Document(
        id=row["id"], url=row["url"], canonical_url=row["canonical_url"],
        title=row["title"], author=row["author"], published_at=row["published_at"],
        fetched_at=row["fetched_at"] or 0.0, raw_text=row["raw_text"] or "",
        source_type=row["source_type"] or "web", content_hash=row["content_hash"] or "",
        lang=row["lang"], partial=bool(row["partial"]),
        meta=json.loads(row["meta"] or "{}"),
    )


# --- entities ---

def upsert_entity(conn: sqlite3.Connection, ent: Entity, theme_id: int = 0) -> None:
    ent.updated_at = time.time()
    conn.execute(
        """INSERT OR REPLACE INTO entities
        (id,theme_id,type,name,norm_name,aliases,props,observations,sources,provisional,
         created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            ent.id, theme_id, ent.type, ent.name, ent.norm_name,
            json.dumps(ent.aliases, ensure_ascii=False), json.dumps(ent.props, ensure_ascii=False),
            json.dumps(ent.observations, ensure_ascii=False), json.dumps(ent.sources, ensure_ascii=False),
            int(ent.provisional), ent.created_at, ent.updated_at,
        ),
    )
    # FTS 갱신 (형태소 및 표면어 토큰 색인)
    from ..retrieval.tokenizer import get_search_tokenizer

    tokenizer = get_search_tokenizer()
    tokenizer.add_user_words([ent.name, *ent.aliases])
    raw_body = " \n".join(ent.observations) + " " + " ".join(ent.aliases)
    morph_tokens = tokenizer.extract_search_tokens(f"{ent.name} {raw_body}")
    body = f"{raw_body}\n{' '.join(morph_tokens)}" if morph_tokens else raw_body
    conn.execute("DELETE FROM entities_fts WHERE entity_id=?", (ent.id,))
    conn.execute(
        "INSERT INTO entities_fts(entity_id,name,body) VALUES (?,?,?)",
        (ent.id, ent.name, body),
    )
    conn.commit()


def get_entity(conn: sqlite3.Connection, entity_id: str, theme_id: int | None = None) -> Entity | None:
    if theme_id is not None:
        row = conn.execute("SELECT * FROM entities WHERE id=? AND theme_id=?", (entity_id, theme_id)).fetchone()
    else:
        row = conn.execute("SELECT * FROM entities WHERE id=?", (entity_id,)).fetchone()
    return _row_to_entity(row) if row else None


def find_entities_by_norm(conn: sqlite3.Connection, norm_name: str, theme_id: int | None = None) -> list[Entity]:
    if theme_id is not None:
        rows = conn.execute(
            "SELECT * FROM entities WHERE norm_name=? AND theme_id=?", (norm_name, theme_id)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM entities WHERE norm_name=?", (norm_name,)
        ).fetchall()
    return [_row_to_entity(r) for r in rows]


def find_entities_by_name_or_alias(
    conn: sqlite3.Connection, norm_name: str, theme_id: int | None = None
) -> list[Entity]:
    """norm_name 정확 매칭(인덱스) + alias 정확 매칭(스캔).

    alias 는 JSON 컬럼이라 정규화 비교가 필요해 alias 후보만 LIKE 로 좁힌 뒤 Python 에서 정확 비교한다. 규모가 커지면 alias 테이블로 인덱싱할 것.
    """
    from ..ontology.base import normalize_name

    target = normalize_name(norm_name)
    out: dict[str, Entity] = {}
    if theme_id is not None:
        norm_rows = conn.execute("SELECT * FROM entities WHERE norm_name=? AND theme_id=?", (target, theme_id)).fetchall()
    else:
        norm_rows = conn.execute("SELECT * FROM entities WHERE norm_name=?", (target,)).fetchall()
    for r in norm_rows:
        e = _row_to_entity(r)
        out[e.id] = e
    # alias 매칭: aliases JSON 문자열에 후보 토큰이 들어간 행만 1차 필터
    like_raw = f"%{target}%"
    escaped_target = json.dumps(target, ensure_ascii=True)[1:-1]
    if escaped_target != target:
        if theme_id is not None:
            rows = conn.execute(
                "SELECT * FROM entities WHERE (aliases LIKE ? COLLATE NOCASE OR aliases LIKE ? COLLATE NOCASE) AND theme_id=?",
                (like_raw, f"%{escaped_target}%", theme_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM entities WHERE aliases LIKE ? COLLATE NOCASE OR aliases LIKE ? COLLATE NOCASE",
                (like_raw, f"%{escaped_target}%"),
            ).fetchall()
    else:
        if theme_id is not None:
            rows = conn.execute(
                "SELECT * FROM entities WHERE aliases LIKE ? COLLATE NOCASE AND theme_id=?",
                (like_raw, theme_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM entities WHERE aliases LIKE ? COLLATE NOCASE",
                (like_raw,),
            ).fetchall()
    for r in rows:
        e = _row_to_entity(r)
        if e.id in out:
            continue
        if target in {normalize_name(a) for a in e.aliases}:
            out[e.id] = e
    return list(out.values())


def all_entities(conn: sqlite3.Connection, theme_id: int | None = None) -> list[Entity]:
    if theme_id is not None:
        rows = conn.execute("SELECT * FROM entities WHERE theme_id=?", (theme_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM entities").fetchall()
    return [_row_to_entity(r) for r in rows]


def document_entities(conn: sqlite3.Connection, document_id: str, theme_id: int | None = None) -> list[Entity]:
    """한 문서에서 유래된(sources 에 document_id 가 포함된) 엔티티 목록."""
    if theme_id is not None:
        rows = conn.execute(
            "SELECT * FROM entities WHERE sources LIKE ? AND theme_id=?",
            (f"%{document_id}%", theme_id),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM entities WHERE sources LIKE ?",
            (f"%{document_id}%",),
        ).fetchall()
    out = []
    for r in rows:
        ent = _row_to_entity(r)
        if document_id in (ent.sources or []):
            out.append(ent)
    return out


def _row_to_entity(row: sqlite3.Row) -> Entity:
    return Entity(
        id=row["id"], type=row["type"], name=row["name"],
        aliases=json.loads(row["aliases"] or "[]"),
        props=json.loads(row["props"] or "{}"),
        observations=json.loads(row["observations"] or "[]"),
        sources=json.loads(row["sources"] or "[]"),
        provisional=bool(row["provisional"]),
        created_at=row["created_at"] or 0.0,
        updated_at=row["updated_at"] or 0.0,
    )


def select_entity_primary_label(
    conn: sqlite3.Connection,
    entity_id: str,
    new_name: str,
    theme_id: int | None = None,
) -> Entity:
    """기존 별칭(aliases) 중 하나를 선택하여 대표 레이블(name)로 승격/전환.

    - new_name 은 반드시 대상 엔티티의 aliases 에 존재해야 한다 (임의 생성 차단).
    - 기존 name 은 aliases 목록으로 이동하여 보존된다 (해소 및 참조 오염 방지).
    - entities 및 entities_fts 가 원자적으로 동기화된다.
    """
    from ..ontology.base import normalize_name

    ent = get_entity(conn, entity_id, theme_id=theme_id)
    if ent is None:
        cands = find_entities_by_name_or_alias(conn, entity_id, theme_id=theme_id)
        if len(cands) == 1:
            ent = cands[0]
        elif len(cands) > 1:
            raise ValueError(f"Ambiguous entity identifier '{entity_id}', matches multiple entities")
        else:
            raise KeyError(f"Entity not found: {entity_id}")

    cleaned_new = new_name.strip()
    if not cleaned_new:
        raise ValueError("New primary label cannot be empty")

    norm_target = normalize_name(cleaned_new)
    if norm_target == normalize_name(ent.name):
        return ent

    matched_alias: str | None = None
    for a in ent.aliases:
        if normalize_name(a) == norm_target:
            matched_alias = a
            break

    if matched_alias is None:
        raise ValueError(
            f"'{new_name}'은(는) 엔티티 '{ent.name}'의 기존 별칭 목록에 존재하지 않습니다: {ent.aliases}"
        )

    old_name = ent.name
    new_aliases = [
        a for a in ent.aliases
        if normalize_name(a) != norm_target and normalize_name(a) != normalize_name(old_name)
    ]
    new_aliases.append(old_name)

    ent.name = matched_alias
    ent.aliases = sorted(set(new_aliases))
    upsert_entity(conn, ent, theme_id=0 if theme_id is None else theme_id)
    return ent


def batch_select_primary_labels(
    conn: sqlite3.Connection,
    mappings: dict[str, str],
    *,
    dry_run: bool = False,
    theme_id: int | None = None,
) -> dict[str, Any]:
    """대량 매핑(dict: target_id_or_name -> chosen_alias)으로 대표 레이블 일괄 전환.

    dry_run=True 시 실제 DB 수정 없이 시뮬레이션 결과(성공/실패/변경전후)를 반환. dry_run=False 시 트랜잭션 내에서 일괄 적용.
    """
    from ..ontology.base import normalize_name

    plan: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for target, chosen in mappings.items():
        chosen_clean = str(chosen or "").strip()
        if not chosen_clean:
            errors.append({"target": target, "chosen": chosen, "error": "빈 문자열은 대표 레이블이 될 수 없습니다."})
            continue

        ent = get_entity(conn, target, theme_id=theme_id)
        if ent is None:
            cands = find_entities_by_name_or_alias(conn, target, theme_id=theme_id)
            if len(cands) == 1:
                ent = cands[0]
            elif len(cands) > 1:
                errors.append({"target": target, "chosen": chosen, "error": f"'{target}' 식별자가 여러 엔티티에 일치합니다."})
                continue
            else:
                errors.append({"target": target, "chosen": chosen, "error": f"엔티티를 찾을 수 없습니다: '{target}'"})
                continue

        norm_chosen = normalize_name(chosen_clean)
        if norm_chosen == normalize_name(ent.name):
            continue

        matched_alias = None
        for a in ent.aliases:
            if normalize_name(a) == norm_chosen:
                matched_alias = a
                break

        if matched_alias is None:
            errors.append({
                "target": target,
                "entity_id": ent.id,
                "current_name": ent.name,
                "chosen": chosen,
                "error": f"선택한 '{chosen}'이(가) 기존 별칭 {ent.aliases}에 존재하지 않습니다."
            })
            continue

        old_name = ent.name
        new_aliases = [
            a for a in ent.aliases
            if normalize_name(a) != norm_chosen and normalize_name(a) != normalize_name(old_name)
        ]
        new_aliases.append(old_name)
        new_aliases = sorted(set(new_aliases))

        plan.append({
            "id": ent.id,
            "old_name": old_name,
            "new_name": matched_alias,
            "aliases_before": list(ent.aliases),
            "aliases_after": new_aliases,
        })

    if not dry_run and plan:
        with conn:
            for item in plan:
                e = get_entity(conn, item["id"], theme_id=theme_id)
                if e:
                    e.name = item["new_name"]
                    e.aliases = item["aliases_after"]
                    upsert_entity(conn, e, theme_id=0 if theme_id is None else theme_id)

    return {
        "dry_run": dry_run,
        "total": len(mappings),
        "planned": len(plan),
        "failed": len(errors),
        "results": plan,
        "errors": errors,
    }


# --- relations ---

def upsert_relation(conn: sqlite3.Connection, rel: Relation, theme_id: int = 0) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO relations
        (id,theme_id,type,source_id,target_id,props,sources,confidence,provisional,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            rel.id, theme_id, rel.type, rel.source_id, rel.target_id,
            json.dumps(rel.props, ensure_ascii=False), json.dumps(rel.sources, ensure_ascii=False),
            rel.confidence, int(rel.provisional), rel.created_at,
        ),
    )
    conn.commit()


def _row_to_relation(r: sqlite3.Row) -> Relation:
    return Relation(
        id=r["id"], type=r["type"], source_id=r["source_id"],
        target_id=r["target_id"], props=json.loads(r["props"] or "{}"),
        sources=json.loads(r["sources"] or "[]"),
        confidence=r["confidence"], provisional=bool(r["provisional"]),
        created_at=r["created_at"] or 0.0,
    )


def neighbors(conn: sqlite3.Connection, entity_id: str, theme_id: int | None = None) -> list[Relation]:
    if theme_id is not None:
        rows = conn.execute(
            "SELECT * FROM relations WHERE (source_id=? OR target_id=?) AND theme_id=?",
            (entity_id, entity_id, theme_id),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM relations WHERE source_id=? OR target_id=?",
            (entity_id, entity_id),
        ).fetchall()
    return [_row_to_relation(r) for r in rows]


def all_relations(conn: sqlite3.Connection, theme_id: int | None = None) -> list[Relation]:
    if theme_id is not None:
        rows = conn.execute("SELECT * FROM relations WHERE theme_id=?", (theme_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM relations").fetchall()
    return [_row_to_relation(r) for r in rows]


def document_relations(conn: sqlite3.Connection, document_id: str, theme_id: int | None = None) -> list[Relation]:
    """한 문서에서 유래된(sources 에 document_id 가 포함된) 관계 목록."""
    if theme_id is not None:
        rows = conn.execute(
            "SELECT * FROM relations WHERE sources LIKE ? AND theme_id=?",
            (f"%{document_id}%", theme_id),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM relations WHERE sources LIKE ?",
            (f"%{document_id}%",),
        ).fetchall()
    out = []
    for r in rows:
        rel = _row_to_relation(r)
        if document_id in (rel.sources or []):
            out.append(rel)
    return out


def has_relation_between(conn: sqlite3.Connection, id1: str, id2: str, theme_id: int | None = None) -> bool:
    """id1과 id2 사이에 어떤 방향이든 관계가 존재하는지 확인."""
    if theme_id is not None:
        row = conn.execute(
            """SELECT 1 FROM relations
            WHERE ((source_id=? AND target_id=?) OR (source_id=? AND target_id=?)) AND theme_id=?
            LIMIT 1""",
            (id1, id2, id2, id1, theme_id),
        ).fetchone()
    else:
        row = conn.execute(
            """SELECT 1 FROM relations
            WHERE (source_id=? AND target_id=?) OR (source_id=? AND target_id=?)
            LIMIT 1""",
            (id1, id2, id2, id1),
        ).fetchone()
    return row is not None


def add_edge(
    conn: sqlite3.Connection,
    source_id: str,
    target_id: str,
    rel_type: str,
    *,
    confidence: float = 1.0,
    props: dict | None = None,
    sources: list[str] | None = None,
    provisional: bool = False,
    theme_id: int = 0,
) -> Relation | None:
    """GraphStore를 통한 엣지 추가/갱신 편의 함수."""
    from .graph import GraphStore

    return GraphStore(conn, theme_id=theme_id).add_edge(
        source_id=source_id,
        target_id=target_id,
        rel_type=rel_type,
        confidence=confidence,
        props=props,
        sources=sources,
        provisional=provisional,
    )


# --- proposals ---

def log_proposal(
    conn: sqlite3.Connection, kind: str, proposed: str, context: str,
    document_id: str | None = None, theme_id: int = 0,
) -> None:
    conn.execute(
        "INSERT INTO proposals(theme_id,kind,proposed,context,document_id,created_at) "
        "VALUES (?,?,?,?,?,?)",
        (theme_id, kind, proposed, context, document_id, time.time()),
    )
    conn.commit()


def document_proposals(conn: sqlite3.Connection, document_id: str, theme_id: int | None = None) -> list[dict[str, Any]]:
    """한 문서에서 제안된 신규 엔티티/관계 타입 목록."""
    if theme_id is not None:
        rows = conn.execute(
            "SELECT * FROM proposals WHERE document_id = ? AND theme_id = ? ORDER BY id ASC",
            (document_id, theme_id),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM proposals WHERE document_id = ? ORDER BY id ASC",
            (document_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# --- raw preservation (재적재용) ---

def log_inbox(
    conn: sqlite3.Connection,
    *,
    source: str,
    payload: str,
    kind: str,
    user_id: int | None = None,
    chat_id: int | None = None,
    file_name: str | None = None,
    file_ref: str | None = None,
    theme_id: int = 0,
) -> int:
    """[Layer 1] inbound 원본 기록. 처리 전에 무조건 먼저 호출. row id 반환."""
    cur = conn.execute(
        "INSERT INTO raw_inbox(theme_id,received_at,source,user_id,chat_id,kind,payload,"
        "file_name,file_ref,status) VALUES (?,?,?,?,?,?,?,?,?, 'received')",
        (theme_id, time.time(), source, user_id, chat_id, kind, payload, file_name, file_ref),
    )
    conn.commit()
    return int(cur.lastrowid)


def update_inbox(
    conn: sqlite3.Connection, inbox_id: int, *,
    status: str, document_id: str | None = None, error: str | None = None,
    theme_id: int | None = None,
) -> None:
    if theme_id is not None:
        conn.execute(
            "UPDATE raw_inbox SET status=?, document_id=?, error=? WHERE id=? AND theme_id=?",
            (status, document_id, error, inbox_id, theme_id),
        )
    else:
        conn.execute(
            "UPDATE raw_inbox SET status=?, document_id=?, error=? WHERE id=?",
            (status, document_id, error, inbox_id),
        )
    conn.commit()


def all_inbox(conn: sqlite3.Connection, theme_id: int | None = None) -> list[sqlite3.Row]:
    if theme_id is not None:
        return conn.execute("SELECT * FROM raw_inbox WHERE theme_id=? ORDER BY id", (theme_id,)).fetchall()
    return conn.execute("SELECT * FROM raw_inbox ORDER BY id").fetchall()


def inbox_by_status(conn: sqlite3.Connection, status: str, theme_id: int | None = None) -> list[sqlite3.Row]:
    if theme_id is not None:
        return conn.execute(
            "SELECT * FROM raw_inbox WHERE status=? AND theme_id=? ORDER BY id", (status, theme_id)
        ).fetchall()
    return conn.execute(
        "SELECT * FROM raw_inbox WHERE status=? ORDER BY id", (status,)
    ).fetchall()


def inbox_by_statuses(
    conn: sqlite3.Connection, statuses: tuple[str, ...] | list[str], theme_id: int | None = None
) -> list[sqlite3.Row]:
    if not statuses:
        return []
    placeholders = ",".join("?" for _ in statuses)
    params = list(statuses)
    query = f"SELECT * FROM raw_inbox WHERE status IN ({placeholders})"
    if theme_id is not None:
        query += " AND theme_id=?"
        params.append(theme_id)
    query += " ORDER BY id"
    return conn.execute(query, tuple(params)).fetchall()


def get_inbox(conn: sqlite3.Connection, inbox_id: int, theme_id: int | None = None) -> sqlite3.Row | None:
    if theme_id is not None:
        return conn.execute(
            "SELECT * FROM raw_inbox WHERE id=? AND theme_id=?", (inbox_id, theme_id)
        ).fetchone()
    return conn.execute(
        "SELECT * FROM raw_inbox WHERE id=?", (inbox_id,)
    ).fetchone()


get_raw_inbox = get_inbox


def pending_inbox(conn: sqlite3.Connection, limit: int = 0, theme_id: int | None = None) -> list[sqlite3.Row]:
    q = "SELECT * FROM raw_inbox WHERE status IN ('received', 'pending')"
    params: list[Any] = []
    if theme_id is not None:
        q += " AND theme_id=?"
        params.append(theme_id)
    q += " ORDER BY id"
    if limit:
        q += f" LIMIT {int(limit)}"
    return conn.execute(q, params).fetchall()


def inbox_failures(conn: sqlite3.Connection, limit: int = 10, theme_id: int | None = None) -> list[sqlite3.Row]:
    """error/failed(영구실패) 항목을 최신순으로(텔레그램 /failed 용)."""
    q = "SELECT * FROM raw_inbox WHERE status IN ('error','failed')"
    params: list[Any] = []
    if theme_id is not None:
        q += " AND theme_id=?"
        params.append(theme_id)
    q += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return conn.execute(q, params).fetchall()


def due_for_recovery(
    conn: sqlite3.Connection, *, max_attempts: int, now: float | None = None,
    limit: int = 0, theme_id: int | None = None,
) -> list[sqlite3.Row]:
    """자동 재적재 대상: status='error' AND attempts<max AND 재시도시각 도래.

    next_retry_at 이 NULL(아직 한 번도 시도 안 함)이거나 now 이하인 행만. attempts 가 상한에 도달한 행은 recover 가 'failed' 로 굳히므로 여기 다시 안 잡힌다.
    """
    now = time.time() if now is None else now
    q = "SELECT * FROM raw_inbox WHERE status='error' AND attempts < ? AND (next_retry_at IS NULL OR next_retry_at <= ?)"
    params: list[Any] = [max_attempts, now]
    if theme_id is not None:
        q += " AND theme_id=?"
        params.append(theme_id)
    q += " ORDER BY id"
    if limit:
        q += f" LIMIT {int(limit)}"
    return conn.execute(q, params).fetchall()


def record_recovery_attempt(
    conn: sqlite3.Connection, inbox_id: int, *, status: str,
    document_id: str | None = None, error: str | None = None,
    next_retry_at: float | None = None, now: float | None = None,
    theme_id: int | None = None,
) -> None:
    """재적재 1회 시도 결과 기록(attempts+1, last_attempt, next_retry_at 갱신)."""
    now = time.time() if now is None else now
    where = "WHERE id=?"
    params: list[Any] = [status, document_id, error, now, next_retry_at, inbox_id]
    if theme_id is not None:
        where += " AND theme_id=?"
        params.append(theme_id)
    conn.execute(
        f"UPDATE raw_inbox SET status=?, document_id=?, error=?, "
        f"attempts=attempts+1, last_attempt=?, next_retry_at=? {where}",
        params,
    )
    conn.commit()


# --- status / 집계 (claire status 용) ---

def inbox_status_counts(conn: sqlite3.Connection, theme_id: int | None = None) -> dict[str, int]:
    """raw_inbox 의 status 별 개수."""
    q = "SELECT status, COUNT(*) n FROM raw_inbox"
    params: list[Any] = []
    if theme_id is not None:
        q += " WHERE theme_id=?"
        params.append(theme_id)
    q += " GROUP BY status"
    rows = conn.execute(q, tuple(params)).fetchall()
    return {r["status"]: r["n"] for r in rows}


def entity_type_counts(conn: sqlite3.Connection, limit: int = 12, theme_id: int | None = None) -> list[tuple[str, int]]:
    """엔티티 타입별 개수(많은 순)."""
    q = "SELECT type, COUNT(*) n FROM entities"
    params: list[Any] = []
    if theme_id is not None:
        q += " WHERE theme_id=?"
        params.append(theme_id)
    q += " GROUP BY type ORDER BY n DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(q, tuple(params)).fetchall()
    return [(r["type"], r["n"]) for r in rows]


def source_type_counts(conn: sqlite3.Connection, theme_id: int | None = None) -> list[tuple[str, int]]:
    """문서 소스 타입별 개수(web/youtube/pdf/text...)."""
    q = "SELECT source_type, COUNT(*) n FROM documents"
    params: list[Any] = []
    if theme_id is not None:
        q += " WHERE theme_id=?"
        params.append(theme_id)
    q += " GROUP BY source_type ORDER BY n DESC"
    rows = conn.execute(q, tuple(params)).fetchall()
    return [(r["source_type"], r["n"]) for r in rows]


def top_connected_entities(
    conn: sqlite3.Connection, limit: int = 8, theme_id: int | None = None
) -> list[tuple[str, str, int]]:
    """연결(degree) 많은 엔티티 상위 — (name, type, degree)."""
    where_e = "WHERE e.theme_id=?" if theme_id is not None else ""
    on_r = " AND r.theme_id=e.theme_id" if theme_id is not None else ""
    params: list[Any] = [theme_id, limit] if theme_id is not None else [limit]
    rows = conn.execute(
        f"""
        SELECT e.name, e.type, COUNT(r.id) deg
        FROM entities e
        LEFT JOIN relations r ON (r.source_id = e.id OR r.target_id = e.id){on_r}
        {where_e}
        GROUP BY e.id ORDER BY deg DESC LIMIT ?
        """,
        params,
    ).fetchall()
    return [(r["name"], r["type"], r["deg"]) for r in rows]


def most_merged_entities(
    conn: sqlite3.Connection, limit: int = 8, theme_id: int | None = None
) -> list[tuple[str, str, int]]:
    """여러 자료에서 수렴된(sources 많은) 엔티티 — (name, type, source_count).

    sources 는 JSON 배열 문자열이라 Python 에서 길이를 센다.
    """
    q = "SELECT name, type, sources FROM entities"
    params: list[Any] = []
    if theme_id is not None:
        q += " WHERE theme_id=?"
        params.append(theme_id)
    rows = conn.execute(q, tuple(params)).fetchall()
    out = []
    for r in rows:
        try:
            n = len(json.loads(r["sources"] or "[]"))
        except Exception:  # noqa: BLE001
            n = 0
        if n >= 2:
            out.append((r["name"], r["type"], n))
    out.sort(key=lambda x: x[2], reverse=True)
    return out[:limit]


def last_inbox_activity(conn: sqlite3.Connection, theme_id: int | None = None) -> float | None:
    """가장 최근 inbound 수신 시각(epoch). 없으면 None."""
    q = "SELECT MAX(received_at) m FROM raw_inbox"
    params: list[Any] = []
    if theme_id is not None:
        q += " WHERE theme_id=?"
        params.append(theme_id)
    row = conn.execute(q, tuple(params)).fetchone()
    return row["m"] if row and row["m"] else None


def log_extraction(
    conn: sqlite3.Connection, *,
    document_id: str, provider: str, model: str,
    prompt_version: str, raw_response: str,
    theme_id: int = 0,
) -> None:
    """[LLM tier] 모델 원본 출력 보관."""
    conn.execute(
        "INSERT INTO extractions(theme_id,document_id,provider,model,prompt_version,"
        "raw_response,created_at) VALUES (?,?,?,?,?,?,?)",
        (theme_id, document_id, provider, model, prompt_version, raw_response, time.time()),
    )
    conn.commit()


def create_auth_nonce(conn: sqlite3.Connection, *, ttl: float = 600.0) -> str:
    """웹 접속 승인 요청 nonce 생성(승인 대기 ttl초). 추측 불가 토큰."""
    nonce = secrets.token_urlsafe(16)
    now = time.time()
    # P1 싱글 모드에서 이 진입점은 관리자 전용이라 ADMIN_ACCOUNT_ID를 쓴다. P3 L3-e에서 명시 인자로 바꾼다.
    conn.execute(
        "INSERT INTO auth_sessions(nonce,approved,created_at,expires_at,account_id) VALUES (?,0,?,?,?)",
        (nonce, now, now + ttl, ADMIN_ACCOUNT_ID))
    conn.commit()
    return nonce


def approve_auth_nonce(
    conn: sqlite3.Connection, nonce: str, *, session_ttl: float = 86400.0
) -> str | None:
    """[봇 콜백] 미승인·미만료 nonce 를 승인하고 세션 토큰 발급(만료 갱신). 없으면 None."""
    row = conn.execute(
        "SELECT expires_at FROM auth_sessions WHERE nonce=? AND approved=0", (nonce,)
    ).fetchone()
    if row is None or row["expires_at"] < time.time():
        return None
    token = secrets.token_urlsafe(32)
    now = time.time()
    # 텔레그램 버튼 승인은 사람이 방금 인증한 시점이다(단계 인증 §8.5).
    conn.execute(
        "UPDATE auth_sessions SET approved=1, session_token=?, expires_at=?, authenticated_at=? WHERE nonce=?",
        (token, now + session_ttl, now, nonce))
    conn.commit()
    return token


def poll_auth_nonce(conn: sqlite3.Connection, nonce: str) -> str | None:
    """[웹 폴링] 승인됐으면 세션 토큰 반환(미승인/만료면 None)."""
    row = conn.execute(
        "SELECT session_token, approved, expires_at FROM auth_sessions WHERE nonce=?",
        (nonce,)).fetchone()
    if row and row["approved"] and row["expires_at"] >= time.time():
        return row["session_token"]
    return None


# 웹 세션 슬라이딩 수명(초). 남은 수명이 절반 아래로 내려갔을 때만 갱신해
# 인증 요청마다 SQLite writer lock을 잡지 않는다.
SESSION_TTL = 7 * 86400.0


# 공유 링크용 토큰 알파벳: 헷갈리는 0/o/1/l 제외.
_TOKEN_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"

def _short_token(n: int) -> str:
    return "".join(secrets.choice(_TOKEN_ALPHABET) for _ in range(n))


# token_urlsafe(24)가 만드는 기존 nonce 승인 세션까지 허용하는 하한. 예전 /web 직접
# 세션(12자)은 외부 hostname 배포 경계에서는 너무 짧으므로 배포 즉시 무효로 취급한다.
MIN_SESSION_TOKEN_LENGTH = 32
MAX_SESSION_TOKEN_LENGTH = 128
_SESSION_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def plausible_session_token(token: str) -> bool:
    """DB를 열기 전에 적용할 세션 토큰의 값싼 형식 경계."""

    return bool(
        MIN_SESSION_TOKEN_LENGTH <= len(token or "") <= MAX_SESSION_TOKEN_LENGTH
        and _SESSION_TOKEN_RE.fullmatch(token)
    )


def revoke_all_sessions(conn: sqlite3.Connection) -> int:
    """모든 세션/대기 nonce 무효화. 반환: 삭제 행 수."""
    n = conn.execute("DELETE FROM auth_sessions").rowcount
    conn.commit()
    return n


def create_session(
    conn: sqlite3.Connection, *, ttl: float = SESSION_TTL, scope: str = "owner"
) -> str:
    """[/web, /webro] **scope 별 단일 활성 세션**: 같은 scope 의 기존 세션만 revoke 하고 추측 저항성이 충분한 새 토큰 1개를 발급 — owner(/web)와 readonly(/webro)는 서로 다른 scope 라 독립적으로 공존한다(읽기전용 링크를 공유해도 내 소유자 세션은 안 끊김, 그 반대도 마찬가지).

    토큰은 링크의 전체값만 인정한다. 발급 즉시 같은 scope 의 이전 링크/쿠키는 무효(다음 /web 한 번이 곧 '이전 owner 세션 전부 로그아웃', scope 가 다르면 서로 안 건드림). nonce=토큰(PK)."""
    # P1 싱글 모드에서 이 진입점은 관리자 전용이라 ADMIN_ACCOUNT_ID를 쓴다. P3 L3-e에서 명시 인자로 바꾼다.
    account_id = ADMIN_ACCOUNT_ID
    conn.execute("DELETE FROM auth_sessions WHERE scope=? AND account_id=?", (scope, account_id))
    token = secrets.token_urlsafe(32)
    now = time.time()
    # 발급은 사람이 방금 인증한 시점이다(authenticated_at, 단계 인증 §8.5).
    conn.execute(
        "INSERT INTO auth_sessions(nonce,session_token,approved,created_at,expires_at,scope,account_id,authenticated_at) "
        "VALUES (?,?,1,?,?,?,?,?)", (token, token, now, now + ttl, scope, account_id, now))
    conn.commit()
    return token


def exchange_session_token(
    conn: sqlite3.Connection,
    token: str,
    *,
    ttl: float = SESSION_TTL,
    scopes: tuple[str, ...] = ("owner",),
) -> tuple[str, str] | None:
    """owner URL bootstrap token을 한 번만 소비하고 cookie용 세션으로 회전한다.

    조회 뒤 UPDATE에 이전 token과 만료 조건을 다시 넣는다. 동시 요청이 같은 URL을 사용해도 한 요청만 rowcount=1을 얻고 나머지는 실패한다.
    """

    if not plausible_session_token(token) or not scopes:
        return None
    placeholders = ",".join("?" for _ in scopes)
    now = time.time()
    row = conn.execute(
        f"SELECT scope FROM auth_sessions WHERE session_token=? "
        f"AND approved=1 AND expires_at>=? AND scope IN ({placeholders})",
        (token, now, *scopes),
    ).fetchone()
    if row is None:
        return None

    rotated = secrets.token_urlsafe(32)
    # 부트스트랩 링크 교환은 사람이 방금 인증한 시점이다. account_id는 발급한 행의 값을 그대로 둔다.
    result = conn.execute(
        f"UPDATE auth_sessions SET session_token=?, expires_at=?, authenticated_at=? "
        f"WHERE session_token=? AND approved=1 AND expires_at>=? "
        f"AND scope IN ({placeholders})",
        (rotated, now + ttl, now, token, now, *scopes),
    )
    conn.commit()
    if result.rowcount != 1:
        return None
    return str(row["scope"]), rotated


def validate_session_scope(
    conn: sqlite3.Connection,
    token: str,
    *,
    ttl: float = SESSION_TTL,
    scopes: tuple[str, ...] = ("owner", "collaborator", "readonly"),
) -> str | None:
    """유효한 전체 세션 토큰의 scope를 반환한다.

    남은 수명이 ``ttl / 2``보다 짧을 때만 슬라이딩 만료를 연장한다.
    """
    if not plausible_session_token(token) or not scopes:
        return None
    ph = ",".join("?" for _ in scopes)
    row = conn.execute(
        f"SELECT expires_at, scope FROM auth_sessions WHERE session_token=? "
        f"AND approved=1 AND scope IN ({ph})",
        (token, *scopes),
    ).fetchone()
    now = time.time()
    if not (row and row["expires_at"] >= now):
        return None
    if row["expires_at"] < now + (ttl / 2):
        conn.execute(
            "UPDATE auth_sessions SET expires_at=? WHERE session_token=?",
            (now + ttl, token),
        )
        conn.commit()
    return str(row["scope"])


def validate_session(
    conn: sqlite3.Connection, token: str, *, ttl: float = SESSION_TTL,
    scopes: tuple[str, ...] = ("owner",),
) -> bool:
    """세션 토큰이 유효(승인됨 + 미만료 + scopes 중 하나)한가.

    기본은 scope='owner' 만 인정(기존 전체-쓰기 게이트 동작 그대로 — 하위호환). 읽기전용 게이트는 scopes=("owner","readonly") 로 호출해 두 scope 모두 인정한다(owner 세션으로도 당연히 읽을 수 있어야 하므로)."""
    return validate_session_scope(
        conn, token, ttl=ttl, scopes=scopes
    ) is not None


# --- OAuth 2.1 Storage & Validation ---

OAUTH_ACCESS_TOKEN_TTL: float = 30 * 86400.0  # 30일
OAUTH_REFRESH_TOKEN_TTL: float = 365 * 86400.0  # 1년


def register_oauth_client(
    conn: sqlite3.Connection,
    client_id: str,
    client_secret: str | None,
    client_name: str | None,
    redirect_uris: list[str],
) -> None:
    """OAuth 클라이언트 등록(RFC 7591 Dynamic Client Registration)."""
    now = time.time()
    conn.execute(
        """INSERT OR REPLACE INTO oauth_clients
        (client_id, client_secret, client_name, redirect_uris, created_at)
        VALUES (?, ?, ?, ?, ?)""",
        (client_id, client_secret, client_name, json.dumps(redirect_uris), now),
    )
    conn.commit()


def get_oauth_client(conn: sqlite3.Connection, client_id: str) -> dict[str, Any] | None:
    """OAuth 클라이언트 조회."""
    row = conn.execute(
        "SELECT client_id, client_secret, client_name, redirect_uris, created_at FROM oauth_clients WHERE client_id=?",
        (client_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "client_id": row["client_id"],
        "client_secret": row["client_secret"],
        "client_name": row["client_name"],
        "redirect_uris": json.loads(row["redirect_uris"] or "[]"),
        "created_at": row["created_at"],
    }


def create_oauth_code(
    conn: sqlite3.Connection,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    code_challenge_method: str = "S256",
    scope: str = "readonly",
    *,
    ttl: float = 300.0,
) -> str:
    """일회용 인가 코드(Authorization Code) 생성 (수명 5분)."""
    code = secrets.token_urlsafe(32)
    now = time.time()
    # P1 싱글 모드에서 이 진입점은 관리자 전용이라 ADMIN_ACCOUNT_ID를 쓴다. P3 L3-e에서 명시 인자로 바꾼다.
    conn.execute(
        """INSERT INTO oauth_codes
        (code, client_id, redirect_uri, code_challenge, code_challenge_method, scope, expires_at, account_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (code, client_id, redirect_uri, code_challenge, code_challenge_method, scope, now + ttl,
         ADMIN_ACCOUNT_ID),
    )
    conn.commit()
    return code


def consume_oauth_code(
    conn: sqlite3.Connection,
    code: str,
    client_id: str,
    redirect_uri: str,
) -> dict[str, Any] | None:
    """인가 코드 단일 소비(Atomic Consumption). 유효하면 정보 반환 후 즉시 삭제."""
    now = time.time()
    row = conn.execute(
        """SELECT client_id, redirect_uri, code_challenge, code_challenge_method, scope, expires_at, account_id
        FROM oauth_codes WHERE code=? AND client_id=? AND redirect_uri=? AND expires_at>=?""",
        (code, client_id, redirect_uri, now),
    ).fetchone()
    if row is None:
        return None
    data = dict(row)
    conn.execute("DELETE FROM oauth_codes WHERE code=?", (code,))
    conn.commit()
    return data


def create_oauth_token(
    conn: sqlite3.Connection,
    client_id: str,
    scope: str = "readonly",
    *,
    access_ttl: float = OAUTH_ACCESS_TOKEN_TTL,
    refresh_ttl: float = OAUTH_REFRESH_TOKEN_TTL,
) -> tuple[str, str]:
    """OAuth Access Token 및 Refresh Token 발급 및 영구 격리 저장."""
    access_token = secrets.token_urlsafe(32)
    refresh_token = secrets.token_urlsafe(32)
    now = time.time()
    # P1 싱글 모드에서 이 진입점은 관리자 전용이라 ADMIN_ACCOUNT_ID를 쓴다. P3 L3-e에서 명시 인자로 바꾼다.
    conn.execute(
        """INSERT INTO oauth_tokens
        (token, refresh_token, client_id, scope, created_at, expires_at, account_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (access_token, refresh_token, client_id, scope, now, now + access_ttl, ADMIN_ACCOUNT_ID),
    )
    conn.commit()
    return access_token, refresh_token


def refresh_oauth_token(
    conn: sqlite3.Connection,
    refresh_token: str,
    client_id: str | None = None,
    *,
    access_ttl: float = OAUTH_ACCESS_TOKEN_TTL,
) -> tuple[str, str, str] | None:
    """Refresh Token 회전(Rotation) 및 신규 Access/Refresh Token 발급."""
    now = time.time()
    query = "SELECT token, refresh_token, client_id, scope, expires_at FROM oauth_tokens WHERE refresh_token=?"
    params: list[Any] = [refresh_token]
    if client_id:
        query += " AND client_id=?"
        params.append(client_id)
    row = conn.execute(query, params).fetchone()
    if row is None:
        return None

    new_access = secrets.token_urlsafe(32)
    new_refresh = secrets.token_urlsafe(32)
    # 같은 행을 회전하므로 account_id는 발급 때 값이 그대로 남는다.
    cur = conn.execute(
        """UPDATE oauth_tokens
        SET token=?, refresh_token=?, expires_at=?
        WHERE refresh_token=?""",
        (new_access, new_refresh, now + access_ttl, refresh_token),
    )
    conn.commit()
    if cur.rowcount != 1:
        return None
    return new_access, new_refresh, str(row["scope"])


def validate_oauth_token(
    conn: sqlite3.Connection,
    token: str,
) -> str | None:
    """OAuth Access Token 검증. 유효한 경우 scope 문자열(예: 'readonly') 반환."""
    if not token or len(token) < 20:
        return None
    now = time.time()
    row = conn.execute(
        "SELECT scope, expires_at FROM oauth_tokens WHERE token=? AND expires_at>=?",
        (token, now),
    ).fetchone()
    if row is None:
        return None
    return str(row["scope"])


def create_oauth_auth_request(
    conn: sqlite3.Connection,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    code_challenge_method: str = "S256",
    scope: str = "readonly",
    state: str | None = None,
    *,
    nonce: str | None = None,
    ttl: float = 600.0,
) -> str:
    """텔레그램 푸시 승인을 위한 인증 요청(nonce) 발급 (10분 유효)."""
    nonce = nonce or secrets.token_urlsafe(24)
    now = time.time()
    conn.execute(
        """INSERT INTO oauth_auth_requests
        (nonce, client_id, redirect_uri, code_challenge, code_challenge_method, scope, state, approved, created_at, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
        (nonce, client_id, redirect_uri, code_challenge, code_challenge_method, scope, state, now, now + ttl),
    )
    conn.commit()
    return nonce


def get_oauth_auth_request(
    conn: sqlite3.Connection,
    nonce: str,
) -> dict[str, Any] | None:
    """인증 요청 상태 조회."""
    now = time.time()
    row = conn.execute(
        "SELECT nonce, client_id, redirect_uri, code_challenge, code_challenge_method, scope, state, approved, code, expires_at "
        "FROM oauth_auth_requests WHERE nonce=? AND expires_at>=?",
        (nonce, now),
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def approve_oauth_auth_request(
    conn: sqlite3.Connection,
    nonce: str,
) -> str | None:
    """텔레그램 봇 또는 웹 승인 버튼에 의한 인가 요청 승인 -> 인가 코드(code) 발급 및 저장."""
    req = get_oauth_auth_request(conn, nonce)
    if not req or req["approved"]:
        return None
    code = create_oauth_code(
        conn,
        client_id=req["client_id"],
        redirect_uri=req["redirect_uri"],
        code_challenge=req["code_challenge"],
        code_challenge_method=req["code_challenge_method"],
        scope=req["scope"],
    )
    # P1 싱글 모드에서 이 진입점은 관리자 전용이라 ADMIN_ACCOUNT_ID를 쓴다. P3 L3-e에서 명시 인자로 바꾼다.
    conn.execute(
        "UPDATE oauth_auth_requests SET approved=1, code=?, account_id=? WHERE nonce=?",
        (code, ADMIN_ACCOUNT_ID, nonce),
    )
    conn.commit()
    return code


def deny_oauth_auth_request(
    conn: sqlite3.Connection,
    nonce: str,
) -> bool:
    """인가 요청 거부 -> 요청 삭제."""
    cur = conn.execute("DELETE FROM oauth_auth_requests WHERE nonce=?", (nonce,))
    conn.commit()
    return cur.rowcount > 0


# --- API and MCP Persistent Access Tokens (Decoupled File Store) ---


def _data_dir_from_conn(conn: Any) -> Path | None:
    try:
        if conn is not None and hasattr(conn, "execute"):
            cur = conn.execute("PRAGMA database_list")
            row = cur.fetchone()
            if row and len(row) >= 3 and row[2]:
                return Path(row[2]).parent
    except Exception:
        pass
    return None


def create_api_token(
    conn: Any = None,
    name: str = "",
    scope: str = "readonly",
    expires_in_days: int | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """Create a persistent API and MCP access token in the decoupled token store."""
    from .token_store import get_token_store

    store = get_token_store(data_dir=_data_dir_from_conn(conn))
    return store.create_api_token(name=name, scope=scope, expires_in_days=expires_in_days, token=token)


def list_api_tokens(
    conn: Any = None,
    include_revoked: bool = False,
) -> list[dict[str, Any]]:
    """List API and MCP access tokens from the decoupled token store."""
    from .token_store import get_token_store

    store = get_token_store(data_dir=_data_dir_from_conn(conn))
    return store.list_api_tokens(include_revoked=include_revoked)


def get_api_token(
    conn: Any = None,
    token_or_id: str | int = "",
) -> dict[str, Any] | None:
    """Find a token by id, exact value, or prefix from the decoupled token store."""
    from .token_store import get_token_store

    store = get_token_store(data_dir=_data_dir_from_conn(conn))
    return store.get_api_token(token_or_id)


def revoke_api_token(
    conn: Any = None,
    token_or_id: str | int = "",
) -> bool:
    """Revoke an API and MCP access token immediately."""
    from .token_store import get_token_store

    store = get_token_store(data_dir=_data_dir_from_conn(conn))
    return store.revoke_api_token(token_or_id)


def validate_api_token(
    conn: Any = None,
    token: str = "",
) -> dict[str, Any] | None:
    """Validate token against the decoupled in-memory/file token store."""
    from .token_store import get_token_store

    store = get_token_store(data_dir=_data_dir_from_conn(conn))
    return store.validate_api_token(token)


def migrate_env_tokens(
    conn: Any = None,
    env_inject_token: str = "",
    env_readonly_token: str = "",
    env_collab_token: str = "",
) -> list[dict[str, Any]]:
    """Migrate environment variable tokens into the decoupled token store."""
    from .token_store import get_token_store

    store = get_token_store(data_dir=_data_dir_from_conn(conn))
    return store.migrate_env_tokens(
        env_inject_token=env_inject_token,
        env_readonly_token=env_readonly_token,
        env_collab_token=env_collab_token,
    )


def ensure_default_owner_token(
    conn: Any = None,
    fallback_token: str = "",
) -> dict[str, Any]:
    """Ensure at least one active owner token exists in the decoupled token store."""
    from .token_store import get_token_store

    store = get_token_store(data_dir=_data_dir_from_conn(conn))
    return store.ensure_default_owner_token(fallback_token=fallback_token)





# 공유 토큰은 16자(약 79bit)이며 비인증 문서 1개에만 제한된다. 프리픽스 입력 편의가
# 필요 없어 전체 일치만 허용한다(전체 UI 세션과 보안 모델이 다름).
_SHARE_TOKEN_LEN = 16


def plausible_share_token(token: str) -> bool:
    """공개 공유 토큰을 DB 조회 전에 정확한 길이와 알파벳으로 거른다."""

    return bool(
        len(token or "") == _SHARE_TOKEN_LEN
        and all(char in _TOKEN_ALPHABET for char in token)
    )


def create_doc_share(conn: sqlite3.Connection, document_id: str,
                     *, ttl: float | None = None,
                     reuse_existing: bool = True,
                     theme_id: int = 0) -> str:
    """문서 1개의 읽기 공유 토큰을 발급(세션과 분리). ttl=None 이면 무기한.

    reuse_existing=True 이면 문서에 이미 유효한(미만료) 토큰이 있는 경우 이를 재사용하여 URL 파편화를 방지하고 분석/참조수 집계를 단일 정규 URL로 통합한다."""
    now = time.time()
    if reuse_existing:
        row = conn.execute(
            "SELECT token, expires_at FROM doc_shares "
            "WHERE document_id=? AND theme_id=? AND (expires_at IS NULL OR expires_at > ?) "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (document_id, theme_id, now),
        ).fetchone()
        if row and row["token"]:
            return str(row["token"])

    token = _short_token(_SHARE_TOKEN_LEN)
    expires = (now + ttl) if ttl else None
    conn.execute(
        "INSERT INTO doc_shares(token, theme_id, document_id, created_at, expires_at) "
        "VALUES (?,?,?,?,?)", (token, theme_id, document_id, now, expires))
    conn.commit()
    return token


def resolve_doc_share(conn: sqlite3.Connection, token: str, theme_id: int | None = None) -> str | None:
    """공유 토큰 → document_id(미만료). 없거나 만료면 None. 전체 일치만(프리픽스 불가)."""
    t = token or ""
    if not plausible_share_token(t):
        return None
    if theme_id is not None:
        row = conn.execute(
            "SELECT document_id, expires_at FROM doc_shares WHERE token=? AND theme_id=?", (t, theme_id)).fetchone()
    else:
        row = conn.execute(
            "SELECT document_id, expires_at FROM doc_shares WHERE token=?", (t,)).fetchone()
    if not row:
        return None
    if row["expires_at"] is not None and row["expires_at"] < time.time():
        return None
    return row["document_id"]


def list_public_sitemap_documents(
    conn: sqlite3.Connection,
    *,
    ensure_shares: bool = True,
    theme_id: int | None = None,
) -> list[dict[str, Any]]:
    """사이트맵 색인을 위한 비숨김(hidden=0) 공개 문서 및 공유 토큰 목록을 조회한다.

    ensure_shares=True 이고 토큰이 없는 문서가 있으면 영구 공유 토큰을 생성한다.
    (만약 DB가 readonly 모드라면 기존 유효 토큰만 반환한다.)
    """
    now = time.time()
    doc_query = "SELECT id, fetched_at, published_at FROM documents WHERE hidden = 0"
    doc_params: list[Any] = []
    if theme_id is not None:
        doc_query += " AND theme_id = ?"
        doc_params.append(theme_id)
    doc_query += " ORDER BY fetched_at DESC"
    docs = conn.execute(doc_query, tuple(doc_params)).fetchall()
    if not docs:
        return []

    share_query = "SELECT token, document_id, expires_at FROM doc_shares WHERE (expires_at IS NULL OR expires_at > ?)"
    share_params: list[Any] = [now]
    if theme_id is not None:
        share_query += " AND theme_id = ?"
        share_params.append(theme_id)
    share_query += " ORDER BY created_at DESC, rowid DESC"
    share_rows = conn.execute(share_query, tuple(share_params)).fetchall()

    shares_by_doc: dict[str, str] = {}
    for r in share_rows:
        doc_id = str(r["document_id"])
        if doc_id not in shares_by_doc and r["token"]:
            shares_by_doc[doc_id] = str(r["token"])

    results: list[dict[str, Any]] = []
    missing_docs: list[sqlite3.Row] = []

    for d in docs:
        doc_id = str(d["id"])
        tok = shares_by_doc.get(doc_id)
        if tok:
            results.append({
                "token": tok,
                "document_id": doc_id,
                "fetched_at": d["fetched_at"],
                "published_at": d["published_at"],
            })
        else:
            missing_docs.append(d)

    if missing_docs and ensure_shares:
        try:
            for d in missing_docs:
                doc_id = str(d["id"])
                tok = create_doc_share(conn, doc_id, reuse_existing=True, theme_id=theme_id if theme_id is not None else 0)
                results.append({
                    "token": tok,
                    "document_id": doc_id,
                    "fetched_at": d["fetched_at"],
                    "published_at": d["published_at"],
                })
        except sqlite3.OperationalError:
            pass

    return results


def resolve_document_targets(
    conn: sqlite3.Connection,
    target: str | None = None,
    *,
    doc_id: str | None = None,
    url: str | None = None,
    canonical_url: str | None = None,
    token: str | None = None,
    pattern: str | None = None,
    limit: int = 50,
    theme_id: int | None = None,
) -> list[dict[str, Any]]:
    """입력값(target, doc_id, url, token, pattern 등)을 4단계 표준에 따라 분석하여 문서 목록을 반환."""
    from urllib.parse import parse_qs, urlsplit
    from ..ingest.normalize import canonicalize_url

    results: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def _add_doc(row: sqlite3.Row | dict[str, Any], matched_by: str, is_from_share_token: bool = False) -> None:
        d = dict(row) if not isinstance(row, dict) else row
        did = d["id"]
        if did not in seen_ids:
            seen_ids.add(did)
            results.append({
                "id": did,
                "theme_id": d.get("theme_id", 0),
                "url": d.get("url"),
                "canonical_url": d.get("canonical_url"),
                "title": d.get("title"),
                "content_hash": d.get("content_hash"),
                "fetched_at": d.get("fetched_at"),
                "matched_by": matched_by,
                "is_from_share_token": is_from_share_token,
            })

    # 1. 명시적 doc_id
    if doc_id:
        q = "SELECT id, theme_id, url, canonical_url, title, content_hash, fetched_at FROM documents WHERE id=?"
        params: list[Any] = [doc_id]
        if theme_id is not None:
            q += " AND theme_id=?"
            params.append(theme_id)
        row = conn.execute(q, tuple(params)).fetchone()
        if row:
            _add_doc(row, matched_by="doc_id")

    # 2. 명시적 token
    if token:
        shared_id = resolve_doc_share(conn, token, theme_id=theme_id)
        if not shared_id:
            q_share = "SELECT document_id FROM doc_shares WHERE token=?"
            p_share: list[Any] = [token]
            if theme_id is not None:
                q_share += " AND theme_id=?"
                p_share.append(theme_id)
            row_share = conn.execute(q_share, tuple(p_share)).fetchone()
            if row_share:
                shared_id = row_share["document_id"]
        if shared_id:
            q = "SELECT id, theme_id, url, canonical_url, title, content_hash, fetched_at FROM documents WHERE id=?"
            params = [shared_id]
            if theme_id is not None:
                q += " AND theme_id=?"
                params.append(theme_id)
            row = conn.execute(q, tuple(params)).fetchone()
            if row:
                _add_doc(row, matched_by="token", is_from_share_token=True)

    # 3. 명시적 url / canonical_url
    if url:
        c_url = canonicalize_url(url)
        q = "SELECT id, theme_id, url, canonical_url, title, content_hash, fetched_at FROM documents WHERE (url=? OR canonical_url=?)"
        params = [url, c_url]
        if theme_id is not None:
            q += " AND theme_id=?"
            params.append(theme_id)
        rows = conn.execute(q, tuple(params)).fetchall()
        for r in rows:
            _add_doc(r, matched_by="url")

    if canonical_url:
        q = "SELECT id, theme_id, url, canonical_url, title, content_hash, fetched_at FROM documents WHERE canonical_url=?"
        params = [canonical_url]
        if theme_id is not None:
            q += " AND theme_id=?"
            params.append(theme_id)
        rows = conn.execute(q, tuple(params)).fetchall()
        for r in rows:
            _add_doc(r, matched_by="canonical_url")

    # 4. 명시적 pattern
    if pattern:
        patt = f"%{pattern}%"
        q = "SELECT id, theme_id, url, canonical_url, title, content_hash, fetched_at FROM documents WHERE (id LIKE ? OR url LIKE ? OR canonical_url LIKE ? OR title LIKE ?)"
        params = [patt, patt, patt, patt]
        if theme_id is not None:
            q += " AND theme_id=?"
            params.append(theme_id)
        q += " LIMIT ?"
        params.append(limit)
        rows = conn.execute(q, tuple(params)).fetchall()
        for r in rows:
            _add_doc(r, matched_by="pattern")

    # 5. 스마트 단일 입력 (target) 해석 (4단계 우선순위)
    if target and not results:
        raw_target = target.strip()

        # 5-1. 정확한 ID 일치
        q = "SELECT id, theme_id, url, canonical_url, title, content_hash, fetched_at FROM documents WHERE id=?"
        params = [raw_target]
        if theme_id is not None:
            q += " AND theme_id=?"
            params.append(theme_id)
        row = conn.execute(q, tuple(params)).fetchone()
        if row:
            _add_doc(row, matched_by="id")
            return results

        # 5-2. 공유 링크 (?s=token) 또는 16자리 공유 토큰
        resolved_token = None
        if "?" in raw_target:
            parsed = urlsplit(raw_target)
            qs = parse_qs(parsed.query)
            if "s" in qs and qs["s"]:
                resolved_token = qs["s"][0]
        elif raw_target.startswith("?s="):
            resolved_token = raw_target[3:]
        elif plausible_share_token(raw_target):
            resolved_token = raw_target

        if resolved_token:
            shared_id = resolve_doc_share(conn, resolved_token, theme_id=theme_id)
            if not shared_id:
                q_share = "SELECT document_id FROM doc_shares WHERE token=?"
                p_share = [resolved_token]
                if theme_id is not None:
                    q_share += " AND theme_id=?"
                    p_share.append(theme_id)
                row_share = conn.execute(q_share, tuple(p_share)).fetchone()
                if row_share:
                    shared_id = row_share["document_id"]
            if shared_id:
                q = "SELECT id, theme_id, url, canonical_url, title, content_hash, fetched_at FROM documents WHERE id=?"
                params = [shared_id]
                if theme_id is not None:
                    q += " AND theme_id=?"
                    params.append(theme_id)
                row = conn.execute(q, tuple(params)).fetchone()
                if row:
                    _add_doc(row, matched_by="share_token", is_from_share_token=True)
                    return results

        # 5-3. URL 및 Canonical URL 일치
        test_url = raw_target if raw_target.startswith(("http://", "https://")) else f"https://{raw_target}"
        c_url = canonicalize_url(test_url)
        c_raw = canonicalize_url(raw_target)

        q = """
            SELECT id, theme_id, url, canonical_url, title, content_hash, fetched_at
            FROM documents
            WHERE (url = ? OR canonical_url = ? OR url = ? OR canonical_url = ? OR url = ? OR canonical_url = ?)
        """
        params = [raw_target, raw_target, test_url, c_url, test_url.replace("https://", "http://"), c_raw]
        if theme_id is not None:
            q += " AND theme_id = ?"
            params.append(theme_id)
        rows = conn.execute(q, tuple(params)).fetchall()
        for r in rows:
            _add_doc(r, matched_by="url")

        if results:
            return results

        # 5-4. 제목 / URL 부분 검색 (Fallback)
        patt = f"%{raw_target}%"
        q = """
            SELECT id, theme_id, url, canonical_url, title, content_hash, fetched_at
            FROM documents
            WHERE (title LIKE ? OR url LIKE ? OR canonical_url LIKE ? OR id LIKE ?)
        """
        params = [patt, patt, patt, patt]
        if theme_id is not None:
            q += " AND theme_id = ?"
            params.append(theme_id)
        q += " ORDER BY fetched_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(q, tuple(params)).fetchall()
        for r in rows:
            _add_doc(r, matched_by="pattern")

    return results


def resolve_single_document_target(
    conn: sqlite3.Connection,
    target: str | None = None,
    *,
    doc_id: str | None = None,
    url: str | None = None,
    token: str | None = None,
    theme_id: int | None = None,
) -> dict[str, Any] | None:
    """단일 문서를 요구하는 명령어에서 안전하게 1건을 식별. 다건 매칭 시 ValueError 발생."""
    targets = resolve_document_targets(conn, target, doc_id=doc_id, url=url, token=token, limit=5, theme_id=theme_id)
    if not targets:
        return None
    if len(targets) == 1:
        return targets[0]

    # 정확 일치(id 또는 token)가 1건만 있다면 그것을 우선
    exact = [t for t in targets if t["matched_by"] in ("id", "doc_id", "token", "share_token")]
    if len(exact) == 1:
        return exact[0]

    matched_summary = ", ".join(f"[{t['id'][:8]}] {t.get('title') or t.get('url') or ''}" for t in targets[:3])
    raise ValueError(
        f"입력값 '{target or doc_id or url}'에 여러 문서({len(targets)}건)가 일치합니다 ({matched_summary}...). "
        "정확한 문서 ID 또는 전체 URL을 입력해 주십시오."
    )


def latest_extraction_summary(conn: sqlite3.Connection, document_id: str, theme_id: int | None = None) -> str | None:
    """문서의 최신 추출 결과에서 summary 를 꺼낸다(documents 엔 summary 컬럼이 없고 extractions.raw_response = ExtractionResult JSON 에 들어있다). 노드 상세 패널용."""
    from ..extract.prompts import clean_plain_summary

    if theme_id is not None:
        row = conn.execute(
            "SELECT raw_response FROM extractions WHERE document_id=? AND theme_id=? ORDER BY id DESC LIMIT 1",
            (document_id, theme_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT raw_response FROM extractions WHERE document_id=? ORDER BY id DESC LIMIT 1",
            (document_id,),
        ).fetchone()
    summary = None
    key_claims = None
    if row and row["raw_response"]:
        raw_val = row["raw_response"].strip()
        try:
            parsed = json.loads(raw_val)
            if isinstance(parsed, dict):
                summary = parsed.get("summary")
                key_claims = parsed.get("key_claims")
            elif isinstance(parsed, str) and parsed.strip():
                summary = parsed
        except (ValueError, AttributeError):
            if raw_val and not raw_val.startswith("{") and not raw_val.startswith("["):
                summary = raw_val
            else:
                summary = None
                key_claims = None
    if summary and isinstance(summary, str) and summary.strip():
        cleaned = clean_plain_summary(summary)
        if cleaned:
            return cleaned

    return None


def set_document_detail(
    conn: sqlite3.Connection,
    document_id: str,
    detail: str,
    format: str = "md",
    html: str | None = None,
    theme_id: int | None = None,
) -> None:
    """문서의 가독 렌더(detail), 포맷(detail_format), 사전 컴파일 HTML(detail_html)을 저장."""
    from ..extract.prompts import remove_leading_original_link

    fmt = (format or "md").strip().lower()
    if fmt in ("asciidoc", "adoc"):
        fmt = "adoc"
    else:
        fmt = "md"

    detail = remove_leading_original_link(detail)
    if html is None and detail:
        from ..render import render_to_html

        html_content = render_to_html(detail, format=fmt)
    else:
        html_content = html or ""

    if theme_id is not None:
        conn.execute(
            "UPDATE documents SET detail=?, detail_format=?, detail_html=? WHERE id=? AND theme_id=?",
            (detail, fmt, html_content, document_id, theme_id),
        )
        conn.execute(
            "UPDATE document_presentations SET status='stale', updated_at=strftime('%s', 'now') WHERE document_id=? AND theme_id=?",
            (document_id, theme_id),
        )
    else:
        conn.execute(
            "UPDATE documents SET detail=?, detail_format=?, detail_html=? WHERE id=?",
            (detail, fmt, html_content, document_id),
        )
        conn.execute(
            "UPDATE document_presentations SET status='stale', updated_at=strftime('%s', 'now') WHERE document_id=?",
            (document_id,),
        )
    conn.commit()


def get_document_presentation(conn: sqlite3.Connection, document_id: str, theme_id: int | None = None) -> dict | None:
    """문서의 프레젠테이션 메타데이터를 반환. 없으면 None."""
    query = """
        SELECT document_id, content_hash, adoc_hash, cache_key, theme, transition, slide_count,
               file_path, file_size, status, error_message, presentation_adoc,
               authoring_provider, authoring_model, prompt_version,
               compose_duration_ms, compile_duration_ms,
               created_at, updated_at
        FROM document_presentations
        WHERE document_id = ?
    """
    params: list[Any] = [document_id]
    if theme_id is not None:
        query += " AND theme_id = ?"
        params.append(theme_id)
    row = conn.execute(query, tuple(params)).fetchone()
    if row is None:
        return None
    return dict(row)


def get_presentation_adoc(conn: sqlite3.Connection, document_id: str, theme_id: int | None = None) -> str | None:
    """문서의 프레젠테이션 AsciiDoc 소스코드 반환. 없으면 None."""
    query = "SELECT presentation_adoc FROM document_presentations WHERE document_id = ?"
    params: list[Any] = [document_id]
    if theme_id is not None:
        query += " AND theme_id = ?"
        params.append(theme_id)
    row = conn.execute(query, tuple(params)).fetchone()
    if row is None or not row["presentation_adoc"]:
        return None
    return str(row["presentation_adoc"])


def save_presentation_adoc(
    conn: sqlite3.Connection,
    document_id: str,
    presentation_adoc: str,
    *,
    content_hash: str,
    adoc_hash: str,
    authoring_provider: str | None = None,
    authoring_model: str | None = None,
    prompt_version: str = "pres-v2",
    compose_duration_ms: int = 0,
    slide_count: int = 0,
    theme: str = "night",
    transition: str = "slide",
    cache_key: str = "",
    file_path: str = "",
    status: str = "authored",
    theme_id: int = 0,
) -> None:
    """LLM이 저작한 프레젠테이션 AsciiDoc 원문 및 메타데이터를 저장하거나 갱신."""
    now = time.time()
    conn.execute(
        """
        INSERT INTO document_presentations (
            document_id, theme_id, content_hash, adoc_hash, cache_key, theme, transition, slide_count,
            file_path, file_size, status, presentation_adoc,
            authoring_provider, authoring_model, prompt_version,
            compose_duration_ms, compile_duration_ms,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(document_id, theme_id) DO UPDATE SET
            content_hash = excluded.content_hash,
            adoc_hash = excluded.adoc_hash,
            cache_key = excluded.cache_key,
            theme = excluded.theme,
            transition = excluded.transition,
            slide_count = excluded.slide_count,
            file_path = excluded.file_path,
            status = excluded.status,
            presentation_adoc = excluded.presentation_adoc,
            authoring_provider = excluded.authoring_provider,
            authoring_model = excluded.authoring_model,
            prompt_version = excluded.prompt_version,
            compose_duration_ms = excluded.compose_duration_ms,
            updated_at = excluded.updated_at
        """,
        (
            document_id, theme_id, content_hash, adoc_hash, cache_key, theme, transition, slide_count,
            file_path, 0, status, presentation_adoc,
            authoring_provider, authoring_model, prompt_version,
            compose_duration_ms, 0,
            now, now,
        ),
    )
    conn.commit()


def save_document_presentation(
    conn: sqlite3.Connection,
    document_id: str,
    content_hash: str,
    cache_key: str,
    file_path: str,
    file_size: int,
    theme: str = "night",
    transition: str = "slide",
    slide_count: int = 0,
    status: str = "ready",
    error_message: str | None = None,
    presentation_adoc: str | None = None,
    adoc_hash: str | None = None,
    authoring_provider: str | None = None,
    authoring_model: str | None = None,
    prompt_version: str = "pres-v2",
    compose_duration_ms: int = 0,
    compile_duration_ms: int = 0,
    theme_id: int = 0,
) -> None:
    """프레젠테이션 메타데이터를 저장하거나 갱신."""
    now = time.time()
    conn.execute(
        """
        INSERT INTO document_presentations (
            document_id, theme_id, content_hash, adoc_hash, cache_key, theme, transition, slide_count,
            file_path, file_size, status, error_message, presentation_adoc,
            authoring_provider, authoring_model, prompt_version,
            compose_duration_ms, compile_duration_ms,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(document_id, theme_id) DO UPDATE SET
            content_hash = excluded.content_hash,
            adoc_hash = COALESCE(excluded.adoc_hash, document_presentations.adoc_hash),
            cache_key = excluded.cache_key,
            theme = excluded.theme,
            transition = excluded.transition,
            slide_count = excluded.slide_count,
            file_path = excluded.file_path,
            file_size = excluded.file_size,
            status = excluded.status,
            error_message = excluded.error_message,
            presentation_adoc = COALESCE(excluded.presentation_adoc, document_presentations.presentation_adoc),
            authoring_provider = COALESCE(excluded.authoring_provider, document_presentations.authoring_provider),
            authoring_model = COALESCE(excluded.authoring_model, document_presentations.authoring_model),
            prompt_version = COALESCE(excluded.prompt_version, document_presentations.prompt_version),
            compose_duration_ms = CASE WHEN excluded.compose_duration_ms > 0 THEN excluded.compose_duration_ms ELSE document_presentations.compose_duration_ms END,
            compile_duration_ms = excluded.compile_duration_ms,
            updated_at = excluded.updated_at
        """,
        (
            document_id, theme_id, content_hash, adoc_hash, cache_key, theme, transition, slide_count,
            file_path, file_size, status, error_message, presentation_adoc,
            authoring_provider, authoring_model, prompt_version,
            compose_duration_ms, compile_duration_ms,
            now, now,
        ),
    )
    conn.commit()


def update_document_presentation_status(
    conn: sqlite3.Connection,
    document_id: str,
    status: str,
    error_message: str | None = None,
    theme_id: int | None = None,
) -> None:
    """프레젠테이션 컴파일 상태만 갱신."""
    now = time.time()
    where = "WHERE document_id = ?"
    params: list[Any] = [status, error_message, now, document_id]
    if theme_id is not None:
        where += " AND theme_id = ?"
        params.append(theme_id)
    conn.execute(
        f"""
        UPDATE document_presentations
        SET status = ?, error_message = ?, updated_at = ?
        {where}
        """,
        tuple(params),
    )
    conn.commit()


def invalidate_document_presentation(conn: sqlite3.Connection, document_id: str, theme_id: int | None = None) -> bool:
    """문서의 프레젠테이션 상태를 'stale'로 변경."""
    now = time.time()
    where = "WHERE document_id = ? AND status != 'stale'"
    params: list[Any] = [now, document_id]
    if theme_id is not None:
        where += " AND theme_id = ?"
        params.append(theme_id)
    cur = conn.execute(
        f"""
        UPDATE document_presentations
        SET status = 'stale', updated_at = ?
        {where}
        """,
        tuple(params),
    )
    conn.commit()
    return cur.rowcount > 0


def delete_document_presentation(conn: sqlite3.Connection, document_id: str, theme_id: int | None = None) -> bool:
    """문서의 프레젠테이션 레코드 삭제."""
    where = "WHERE document_id = ?"
    params: list[Any] = [document_id]
    if theme_id is not None:
        where += " AND theme_id = ?"
        params.append(theme_id)
    cur = conn.execute(
        f"DELETE FROM document_presentations {where}",
        tuple(params),
    )
    conn.commit()
    return cur.rowcount > 0


def get_document_detail(conn: sqlite3.Connection, document_id: str, theme_id: int | None = None) -> str | None:
    """문서의 detail(가독 렌더 원본 텍스트). 없으면 None."""
    query = "SELECT detail FROM documents WHERE id=?"
    params: list[Any] = [document_id]
    if theme_id is not None:
        query += " AND theme_id=?"
        params.append(theme_id)
    row = conn.execute(query, tuple(params)).fetchone()
    return (row["detail"] if row else None) or None


def get_document_detail_format(conn: sqlite3.Connection, document_id: str, theme_id: int | None = None) -> str:
    """문서의 detail_format('md' 또는 'adoc'). 없으면 'md'."""
    query = "SELECT detail_format FROM documents WHERE id=?"
    params: list[Any] = [document_id]
    if theme_id is not None:
        query += " AND theme_id=?"
        params.append(theme_id)
    row = conn.execute(query, tuple(params)).fetchone()
    return (row["detail_format"] if row and row["detail_format"] else "md")


def get_document_detail_html(conn: sqlite3.Connection, document_id: str, theme_id: int | None = None) -> str | None:
    """문서의 detail_html(AOT 사전 컴파일된 HTML). 없으면 detail 로부터 실시간 생성 및 캐싱."""
    query = "SELECT detail, detail_format, detail_html FROM documents WHERE id=?"
    params: list[Any] = [document_id]
    if theme_id is not None:
        query += " AND theme_id=?"
        params.append(theme_id)
    row = conn.execute(query, tuple(params)).fetchone()
    if not row:
        return None
    if row["detail_html"]:
        return row["detail_html"]
    if row["detail"] and row["detail"].strip():
        from ..render import render_to_html

        fmt = row["detail_format"] or "md"
        rendered = render_to_html(row["detail"], format=fmt)
        if rendered:
            try:
                upd_query = "UPDATE documents SET detail_html=? WHERE id=?"
                upd_params: list[Any] = [rendered, document_id]
                if theme_id is not None:
                    upd_query += " AND theme_id=?"
                    upd_params.append(theme_id)
                conn.execute(upd_query, tuple(upd_params))
                conn.commit()
            except Exception:  # noqa: BLE001
                pass
            return rendered
    return None


def recompile_all_detail_html(conn: sqlite3.Connection, doc_id: str | None = None, theme_id: int | None = None) -> int:
    """모든 문서(또는 지정된 doc_id)의 detail_html을 현재 AOT 렌더러로 재컴파일하여 DB에 갱신."""
    from ..extract.prompts import remove_leading_original_link
    from ..render import render_to_html

    query = "SELECT id, detail, detail_format FROM documents WHERE detail IS NOT NULL AND trim(detail) != ''"
    params: list[Any] = []
    if doc_id:
        query += " AND id=?"
        params.append(doc_id)
    if theme_id is not None:
        query += " AND theme_id=?"
        params.append(theme_id)
    rows = conn.execute(query, tuple(params)).fetchall()
    count = 0
    for r in rows:
        fmt = r["detail_format"] or "md"
        detail = remove_leading_original_link(r["detail"])
        html_out = render_to_html(detail, format=fmt)
        upd = "UPDATE documents SET detail=?, detail_html=? WHERE id=?"
        upd_params: list[Any] = [detail, html_out, r["id"]]
        if theme_id is not None:
            upd += " AND theme_id=?"
            upd_params.append(theme_id)
        conn.execute(upd, tuple(upd_params))
        count += 1
    conn.commit()
    return count


def documents_missing_detail(conn: sqlite3.Connection, limit: int = 0, theme_id: int | None = None) -> list[str]:
    """detail 이 비어있는 문서 id(최신순). 백필 대상."""
    q = "SELECT id FROM documents WHERE (detail IS NULL OR detail='')"
    params: list[Any] = []
    if theme_id is not None:
        q += " AND theme_id=?"
        params.append(theme_id)
    q += " ORDER BY fetched_at DESC"
    if limit:
        q += f" LIMIT {int(limit)}"
    return [r["id"] for r in conn.execute(q, tuple(params)).fetchall()]


def documents_needing_detail_format(
    conn: sqlite3.Connection,
    target_format: str,
    limit: int = 0,
    theme_id: int | None = None,
) -> list[str]:
    """목표 포맷(target_format)으로 detail 생성이 필요한 문서 id(최신순).

    1) detail 이 비어있거나 NULL인 문서 2) detail 은 있으나 detail_format 이 target_format 과 다른 문서 (이미 target_format 으로 일치하는 문서는 제외)
    """
    fmt = (target_format or "md").strip().lower()
    fmt = "adoc" if fmt in ("asciidoc", "adoc") else "md"
    q = (
        "SELECT id FROM documents WHERE (detail IS NULL OR trim(detail)='' "
        "OR lower(coalesce(detail_format, 'md')) != ?)"
    )
    params: list[Any] = [fmt]
    if theme_id is not None:
        q += " AND theme_id=?"
        params.append(theme_id)
    q += " ORDER BY fetched_at DESC"
    if limit:
        q += f" LIMIT {int(limit)}"
    return [r["id"] for r in conn.execute(q, tuple(params)).fetchall()]


def documents_with_tables(
    conn: sqlite3.Connection,
    limit: int = 0,
    check_detail: bool = True,
    theme_id: int | None = None,
) -> list[dict[str, Any]]:
    """raw_text 또는 detail 에 표(Table)가 포함된 문서 목록을 반환.

    반환: list of {id, title, canonical_url, raw_tables_count, detail_tables_count, total_tables, table_preview}
    """
    from ..extract.table_budget import extract_tables_from_text

    # SQLite LIKE 1차 프리필터 (빠른 스캔 축약)
    q = """
        SELECT id, title, canonical_url, raw_text, detail, fetched_at
        FROM documents
        WHERE ((raw_text LIKE '%|%' OR raw_text LIKE '%<table%')
           OR (detail LIKE '%|%' OR detail LIKE '%<table%'))
    """
    params: list[Any] = []
    if theme_id is not None:
        q += " AND theme_id=?"
        params.append(theme_id)
    q += " ORDER BY fetched_at DESC"
    rows = conn.execute(q, tuple(params)).fetchall()

    results: list[dict[str, Any]] = []
    for r in rows:
        raw_text = r["raw_text"] or ""
        detail_text = (r["detail"] or "") if check_detail else ""
        _, raw_tables = extract_tables_from_text(raw_text)
        _, detail_tables = extract_tables_from_text(detail_text) if check_detail else (detail_text, [])

        total_tables = len(raw_tables) + len(detail_tables)
        if total_tables > 0:
            preview = ""
            if raw_tables:
                preview = raw_tables[0].strip()[:200]
            elif detail_tables:
                preview = detail_tables[0].strip()[:200]
            results.append({
                "id": r["id"],
                "title": r["title"] or "(제목 없음)",
                "canonical_url": r["canonical_url"],
                "raw_tables_count": len(raw_tables),
                "detail_tables_count": len(detail_tables),
                "total_tables": total_tables,
                "table_preview": preview,
            })
            if limit and len(results) >= limit:
                break
    return results


def get_format_status(
    conn: sqlite3.Connection,
    configured_format: str,
    *,
    sample_limit: int = 10,
    theme_id: int | None = None,
) -> dict[str, Any]:
    """DB 내 문서들의 포맷 상태(총 문서, 포맷 일치, 포맷 불일치, detail 누락)를 상세 진단."""
    target = (configured_format or "md").strip().lower()
    target = "adoc" if target in ("asciidoc", "adoc") else "md"

    where_base = ""
    params_base: list[Any] = []
    if theme_id is not None:
        where_base = "WHERE theme_id=?"
        params_base = [theme_id]

    row_total_docs = conn.execute(f"SELECT COUNT(*) as c FROM documents {where_base}", tuple(params_base)).fetchone()
    total_docs = row_total_docs["c"] if row_total_docs else 0

    q_match = "SELECT COUNT(*) as c FROM documents WHERE detail IS NOT NULL AND trim(detail) != '' AND lower(coalesce(detail_format, 'md')) = ?"
    p_match: list[Any] = [target]
    if theme_id is not None:
        q_match += " AND theme_id=?"
        p_match.append(theme_id)
    row_matching = conn.execute(q_match, tuple(p_match)).fetchone()
    matching_docs = row_matching["c"] if row_matching else 0

    q_mismatch = "SELECT COUNT(*) as c FROM documents WHERE detail IS NOT NULL AND trim(detail) != '' AND lower(coalesce(detail_format, 'md')) != ?"
    p_mismatch: list[Any] = [target]
    if theme_id is not None:
        q_mismatch += " AND theme_id=?"
        p_mismatch.append(theme_id)
    row_mismatched = conn.execute(q_mismatch, tuple(p_mismatch)).fetchone()
    mismatched_docs = row_mismatched["c"] if row_mismatched else 0

    q_miss = "SELECT COUNT(*) as c FROM documents WHERE detail IS NULL OR trim(detail) = ''"
    p_miss: list[Any] = []
    if theme_id is not None:
        q_miss += " AND theme_id=?"
        p_miss.append(theme_id)
    row_missing = conn.execute(q_miss, tuple(p_miss)).fetchone()
    missing_detail_docs = row_missing["c"] if row_missing else 0

    total_with_detail = matching_docs + mismatched_docs
    target_docs = mismatched_docs + missing_detail_docs

    missing_docs_sample: list[dict[str, Any]] = []
    if missing_detail_docs > 0 and sample_limit > 0:
        q_samp = "SELECT id, title, url, source_type, fetched_at FROM documents WHERE (detail IS NULL OR trim(detail) = '')"
        p_samp: list[Any] = []
        if theme_id is not None:
            q_samp += " AND theme_id=?"
            p_samp.append(theme_id)
        q_samp += " ORDER BY fetched_at DESC, id DESC LIMIT ?"
        p_samp.append(sample_limit)
        rows = conn.execute(q_samp, tuple(p_samp)).fetchall()
        missing_docs_sample = [dict(r) for r in rows]

    mismatched_docs_sample: list[dict[str, Any]] = []
    if mismatched_docs > 0 and sample_limit > 0:
        q_samp = "SELECT id, title, url, coalesce(detail_format, 'md') AS detail_format, source_type, fetched_at FROM documents WHERE detail IS NOT NULL AND trim(detail) != '' AND lower(coalesce(detail_format, 'md')) != ?"
        p_samp = [target]
        if theme_id is not None:
            q_samp += " AND theme_id=?"
            p_samp.append(theme_id)
        q_samp += " ORDER BY fetched_at DESC, id DESC LIMIT ?"
        p_samp.append(sample_limit)
        rows = conn.execute(q_samp, tuple(p_samp)).fetchall()
        mismatched_docs_sample = [dict(r) for r in rows]

    return {
        "configured": target,
        "target_format": target,
        "total_docs": total_docs,
        "total_with_detail": total_with_detail,
        "matching_docs": matching_docs,
        "mismatched": mismatched_docs,
        "mismatched_docs": mismatched_docs,
        "missing_detail_docs": missing_detail_docs,
        "target_docs": target_docs,
        "needs_migration": (target_docs > 0),
        "missing_docs_sample": missing_docs_sample,
        "mismatched_docs_sample": mismatched_docs_sample,
    }


def check_format_mismatch(
    conn: sqlite3.Connection,
    configured_format: str,
    *,
    sample_limit: int = 10,
    theme_id: int | None = None,
) -> dict[str, Any]:
    """설정된 포맷(Settings.render_format)과 DB에 저장된 detail 포맷 불일치 여부를 진단."""
    return get_format_status(conn, configured_format, sample_limit=sample_limit, theme_id=theme_id)


# --- refresh queue (복원 메커니즘) ---

def enqueue_refresh(
    conn: sqlite3.Connection, *, document_id: str | None, payload: str, reason: str,
    theme_id: int = 0,
) -> bool:
    """갱신 대기열에 등록. document_id 중복이면 pending 으로 되살린다. 신규면 True."""
    now = time.time()
    if document_id:
        row = conn.execute(
            "SELECT id FROM refresh_queue WHERE document_id=? AND theme_id=?", (document_id, theme_id)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE refresh_queue SET status='pending', payload=?, reason=?, "
                "updated_at=? WHERE document_id=? AND theme_id=?",
                (payload, reason, now, document_id, theme_id),
            )
            conn.commit()
            return False
    conn.execute(
        "INSERT INTO refresh_queue(theme_id,document_id,payload,reason,status,created_at,updated_at)"
        " VALUES (?,?,?,?, 'pending', ?, ?)",
        (theme_id, document_id, payload, reason, now, now),
    )
    conn.commit()
    return True


def pending_refresh(conn: sqlite3.Connection, limit: int = 0, theme_id: int | None = None) -> list[sqlite3.Row]:
    q = "SELECT * FROM refresh_queue WHERE status='pending'"
    params: list[Any] = []
    if theme_id is not None:
        q += " AND theme_id=?"
        params.append(theme_id)
    q += " ORDER BY id"
    if limit:
        q += f" LIMIT {int(limit)}"
    return conn.execute(q, tuple(params)).fetchall()


def update_refresh(
    conn: sqlite3.Connection, rid: int, *, status: str, error: str | None = None,
    theme_id: int | None = None,
) -> None:
    where = "WHERE id=?"
    params: list[Any] = [status, error, time.time(), time.time(), rid]
    if theme_id is not None:
        where += " AND theme_id=?"
        params.append(theme_id)
    conn.execute(
        f"UPDATE refresh_queue SET status=?, error=?, attempts=attempts+1, "
        f"last_attempt=?, updated_at=? {where}",
        tuple(params),
    )
    conn.commit()


def refresh_status_counts(conn: sqlite3.Connection, theme_id: int | None = None) -> dict[str, int]:
    if theme_id is not None:
        rows = conn.execute(
            "SELECT status, COUNT(*) n FROM refresh_queue WHERE theme_id=? GROUP BY status", (theme_id,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT status, COUNT(*) n FROM refresh_queue GROUP BY status"
        ).fetchall()
    return {r["status"]: r["n"] for r in rows}


# --- 1홉 자동확장 큐 (expand_queue) — refresh_queue 와 동일 패턴 ---

def enqueue_expand(conn: sqlite3.Connection, document_id: str, theme_id: int = 0) -> bool:
    """문서를 1홉 확장 대기열에 등록. 이미 있으면 pending 으로 되살린다(중복 무해). 신규면 True."""
    now = time.time()
    row = conn.execute(
        "SELECT id FROM expand_queue WHERE document_id=? AND theme_id=?", (document_id, theme_id)
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE expand_queue SET status='pending', updated_at=? WHERE document_id=? AND theme_id=?",
            (now, document_id, theme_id),
        )
        conn.commit()
        return False
    conn.execute(
        "INSERT INTO expand_queue(theme_id,document_id,status,created_at,updated_at)"
        " VALUES (?,?, 'pending', ?, ?)",
        (theme_id, document_id, now, now),
    )
    conn.commit()
    return True


def pending_expand(conn: sqlite3.Connection, limit: int = 0, theme_id: int | None = None) -> list[sqlite3.Row]:
    q = "SELECT * FROM expand_queue WHERE status='pending'"
    params: list[Any] = []
    if theme_id is not None:
        q += " AND theme_id=?"
        params.append(theme_id)
    q += " ORDER BY id"
    if limit:
        q += f" LIMIT {int(limit)}"
    return conn.execute(q, tuple(params)).fetchall()


def update_expand(
    conn: sqlite3.Connection, eid: int, *, status: str,
    error: str | None = None, result: str | None = None,
    theme_id: int | None = None,
) -> None:
    where = "WHERE id=?"
    params: list[Any] = [status, error, result, time.time(), time.time(), eid]
    if theme_id is not None:
        where += " AND theme_id=?"
        params.append(theme_id)
    conn.execute(
        f"UPDATE expand_queue SET status=?, error=?, result=?, attempts=attempts+1, "
        f"last_attempt=?, updated_at=? {where}",
        tuple(params),
    )
    conn.commit()


def expand_status_counts(conn: sqlite3.Connection, theme_id: int | None = None) -> dict[str, int]:
    if theme_id is not None:
        rows = conn.execute(
            "SELECT status, COUNT(*) n FROM expand_queue WHERE theme_id=? GROUP BY status", (theme_id,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT status, COUNT(*) n FROM expand_queue GROUP BY status"
        ).fetchall()
    return {r["status"]: r["n"] for r in rows}


def thin_documents(
    conn: sqlite3.Connection, *, max_len: int, host: str | None = None,
    include_partial: bool = False,
    theme_id: int | None = None,
) -> list[sqlite3.Row]:
    """본문이 빈약한 문서. host 지정 시 해당 호스트만.

    기본은 non-partial 만. include_partial=True 면 partial 노드(예: 구버전 'x.com post' — 본문 스크랩 없이 URL 만 보관)도 포함해 재fetch 대상으로 잡는다.
    """
    q = ("SELECT id, url, title, length(raw_text) L FROM documents "
         "WHERE length(raw_text) < ?")
    args: list[Any] = [max_len]
    if not include_partial:
        q += " AND partial=0"
    if host:
        q += " AND url LIKE ?"
        args.append(f"%{host}%")
    if theme_id is not None:
        q += " AND theme_id=?"
        args.append(theme_id)
    return conn.execute(q, tuple(args)).fetchall()


def update_document_content(
    conn: sqlite3.Connection, doc_id: str, *,
    title: str | None, raw_text: str, content_hash: str, fetched_at: float,
    source_type: str | None = None, partial: bool | None = None,
    meta: dict | None = None,
    theme_id: int | None = None,
) -> None:
    """문서 본문을 in-place 갱신(복원). id 는 유지하여 엔티티 sources 연결 보존.

    source_type/partial 을 주면 함께 갱신한다(구버전 partial 'x.com post' 가 본문 스크랩에 성공해 정식 문서가 될 때 플래그 정합을 맞추기 위함). meta 를 주면 함께 갱신(재fetch 로 새로 수집된 본문 이미지 등을 보존)."""
    from ..ingest.normalize import minhash_signature

    sig = minhash_signature((title or "") + " " + (raw_text or ""))
    cols = ["title=?", "raw_text=?", "content_hash=?", "fetched_at=?", "minhash=?"]
    vals: list = [title, raw_text, content_hash, fetched_at,
                  json.dumps(sig) if sig else None]
    if source_type is not None:
        cols.append("source_type=?")
        vals.append(source_type)
    if partial is not None:
        cols.append("partial=?")
        vals.append(1 if partial else 0)
    if meta is not None:
        cols.append("meta=?")
        vals.append(json.dumps(meta))
    where = "WHERE id=?"
    vals.append(doc_id)
    if theme_id is not None:
        where += " AND theme_id=?"
        vals.append(theme_id)
    conn.execute(f"UPDATE documents SET {', '.join(cols)} {where}", tuple(vals))
    conn.commit()


def set_document_images(conn: sqlite3.Connection, doc_id: str, images: list[dict], theme_id: int | None = None) -> None:
    """문서 meta 에 본문 이미지 목록만 갱신(다른 meta 키 보존). 재fetch 백필용."""
    q = "SELECT meta FROM documents WHERE id=?"
    params: list[Any] = [doc_id]
    if theme_id is not None:
        q += " AND theme_id=?"
        params.append(theme_id)
    row = conn.execute(q, tuple(params)).fetchone()
    if row is None:
        return
    meta = json.loads(row["meta"] or "{}")
    meta["images"] = images or []
    upd = "UPDATE documents SET meta=? WHERE id=?"
    upd_params: list[Any] = [json.dumps(meta), doc_id]
    if theme_id is not None:
        upd += " AND theme_id=?"
        upd_params.append(theme_id)
    conn.execute(upd, tuple(upd_params))
    conn.commit()


# --- 1홉 병합 출처(extra_sources) — ONEHOP_MERGE_DESIGN.md. 신규 컬럼 없이 documents.meta
# JSON 재사용(set_document_images 와 동일 패턴) — 같은 주제의 부가 출처(예: GeekNews 글이
# 발견한 그 프로젝트의 github)를 새 Document 로 안 만들고 부모 문서에 흡수할 때, 원문 링크
# 계보를 보존하기 위한 목록.

def set_document_extra_sources(conn: sqlite3.Connection, doc_id: str, sources: list[dict], theme_id: int | None = None) -> None:
    """문서 meta 에 병합된 부가 출처 목록만 갱신(다른 meta 키 보존)."""
    q = "SELECT meta FROM documents WHERE id=?"
    params: list[Any] = [doc_id]
    if theme_id is not None:
        q += " AND theme_id=?"
        params.append(theme_id)
    row = conn.execute(q, tuple(params)).fetchone()
    if row is None:
        return
    meta = json.loads(row["meta"] or "{}")
    meta["extra_sources"] = sources or []
    upd = "UPDATE documents SET meta=? WHERE id=?"
    upd_params: list[Any] = [json.dumps(meta), doc_id]
    if theme_id is not None:
        upd += " AND theme_id=?"
        upd_params.append(theme_id)
    conn.execute(upd, tuple(upd_params))
    conn.commit()


def get_document_extra_sources(conn: sqlite3.Connection, doc_id: str, theme_id: int | None = None) -> list[dict]:
    q = "SELECT meta FROM documents WHERE id=?"
    params: list[Any] = [doc_id]
    if theme_id is not None:
        q += " AND theme_id=?"
        params.append(theme_id)
    row = conn.execute(q, tuple(params)).fetchone()
    if row is None:
        return []
    return json.loads(row["meta"] or "{}").get("extra_sources") or []


def set_document_focus(conn: sqlite3.Connection, doc_id: str, focus: str | None, theme_id: int | None = None) -> None:
    """문서 meta에 가독 렌더링 작성 초점(focus)을 기록/갱신(다른 meta 키 보존)."""
    q = "SELECT meta FROM documents WHERE id=?"
    params: list[Any] = [doc_id]
    if theme_id is not None:
        q += " AND theme_id=?"
        params.append(theme_id)
    row = conn.execute(q, tuple(params)).fetchone()
    if row is None:
        return
    meta = json.loads(row["meta"] or "{}")
    if focus and focus.strip():
        meta["focus"] = focus.strip()
    else:
        meta.pop("focus", None)
    meta.pop("dir" + "ective", None)
    upd = "UPDATE documents SET meta=? WHERE id=?"
    upd_params: list[Any] = [json.dumps(meta, ensure_ascii=False), doc_id]
    if theme_id is not None:
        upd += " AND theme_id=?"
        upd_params.append(theme_id)
    conn.execute(upd, tuple(upd_params))
    conn.commit()


def get_document_focus(conn: sqlite3.Connection, doc_id: str, theme_id: int | None = None) -> str | None:
    """문서 meta에서 가독 렌더링 작성 초점(focus) 조회."""
    q = "SELECT meta FROM documents WHERE id=?"
    params: list[Any] = [doc_id]
    if theme_id is not None:
        q += " AND theme_id=?"
        params.append(theme_id)
    row = conn.execute(q, tuple(params)).fetchone()
    if row is None:
        return None
    meta = json.loads(row["meta"] or "{}")
    return meta.get("focus")


def update_document_meta(conn: sqlite3.Connection, doc_id: str, meta: dict, theme_id: int | None = None) -> None:
    """문서 meta 를 갱신(기존 키 유지하며 meta 내용 병합 반영)."""
    q = "SELECT meta FROM documents WHERE id=?"
    params: list[Any] = [doc_id]
    if theme_id is not None:
        q += " AND theme_id=?"
        params.append(theme_id)
    row = conn.execute(q, tuple(params)).fetchone()
    if row is None:
        return
    current = json.loads(row["meta"] or "{}")
    current.update(meta)
    upd = "UPDATE documents SET meta=? WHERE id=?"
    upd_params: list[Any] = [json.dumps(current), doc_id]
    if theme_id is not None:
        upd += " AND theme_id=?"
        upd_params.append(theme_id)
    conn.execute(upd, tuple(upd_params))
    conn.commit()


def purge_document_matrix(conn: sqlite3.Connection, doc_id: str, theme_id: int | None = None) -> bool:
    """문서 meta 에서 heatmap_matrix 키를 물리적으로 삭제(소각)."""
    q = "SELECT meta FROM documents WHERE id=?"
    params: list[Any] = [doc_id]
    if theme_id is not None:
        q += " AND theme_id=?"
        params.append(theme_id)
    row = conn.execute(q, tuple(params)).fetchone()
    if row is None:
        return False
    current = json.loads(row["meta"] or "{}")
    if "heatmap_matrix" in current:
        del current["heatmap_matrix"]
        upd = "UPDATE documents SET meta=? WHERE id=?"
        upd_params: list[Any] = [json.dumps(current, ensure_ascii=False), doc_id]
        if theme_id is not None:
            upd += " AND theme_id=?"
            upd_params.append(theme_id)
        conn.execute(upd, tuple(upd_params))
        conn.commit()
    return True


def find_document_by_extra_source(conn: sqlite3.Connection, canonical_url: str | None, theme_id: int | None = None) -> str | None:
    """이미 어떤 문서에 병합 출처로 흡수된 canonical_url 인지 — 1홉 후보 재제안 방지용 (병합 경로는 새 Document 행을 안 만들어 documents.canonical_url 색인으로는 못 잡음).

    documents.meta 는 색인 없는 JSON 이라 파이썬 측 스캔 — near_duplicate_document 와 동일한 절충(개인용 규모라 전체 스캔으로 충분히 빠름). 대략적인 LIKE 로 후보를 먼저 좁혀 스캔 대상을 줄인다."""
    if not canonical_url:
        return None
    q = "SELECT id, meta FROM documents WHERE meta LIKE '%extra_sources%'"
    params: list[Any] = []
    if theme_id is not None:
        q += " AND theme_id=?"
        params.append(theme_id)
    rows = conn.execute(q, tuple(params)).fetchall()
    for r in rows:
        for s in (json.loads(r["meta"] or "{}").get("extra_sources") or []):
            if s.get("canonical_url") == canonical_url:
                return r["id"]
    return None


def documents_missing_images(conn: sqlite3.Connection, limit: int = 0, theme_id: int | None = None) -> list[str]:
    """본문 이미지가 아직 없는(재fetch 안 한) 문서 id — 최신순. 이미지 백필 대상.

    meta 에 'images' 키 자체가 없는(이미지 수집 전 적재) url 보유 문서만. 빈 목록([])은 '재fetch 했으나 콘텐츠 이미지가 없었음'이라 재대상에서 제외(불필요한 재호출 방지)."""
    q = "SELECT id, url, meta FROM documents"
    params: list[Any] = []
    if theme_id is not None:
        q += " WHERE theme_id=?"
        params.append(theme_id)
    q += " ORDER BY fetched_at DESC, rowid DESC"
    rows = conn.execute(q, tuple(params)).fetchall()
    out: list[str] = []
    for r in rows:
        if not r["url"]:
            continue  # url 없는 순수 텍스트는 재fetch 불가
        meta = json.loads(r["meta"] or "{}")
        if "images" not in meta:
            out.append(r["id"])
        if limit and len(out) >= limit:
            break
    return out


def documents_missing_minhash(conn: sqlite3.Connection, limit: int = 0, theme_id: int | None = None) -> list[str]:
    """minhash 가 비어있는 문서 id(백필 대상). partial/짧은 글도 채워 비교 모집단 일관."""
    q = "SELECT id FROM documents WHERE minhash IS NULL"
    params: list[Any] = []
    if theme_id is not None:
        q += " AND theme_id=?"
        params.append(theme_id)
    if limit:
        q += f" LIMIT {int(limit)}"
    return [r["id"] for r in conn.execute(q, tuple(params)).fetchall()]


def set_document_minhash(conn: sqlite3.Connection, doc_id: str, sig_json: str | None, theme_id: int | None = None) -> None:
    upd = "UPDATE documents SET minhash=? WHERE id=?"
    params: list[Any] = [sig_json, doc_id]
    if theme_id is not None:
        upd += " AND theme_id=?"
        params.append(theme_id)
    conn.execute(upd, tuple(params))
    conn.commit()


def _repoint_sources_json(conn: sqlite3.Connection, table: str,
                          keeper_id: str, losers: set[str],
                          theme_id: int | None = None) -> int:
    """entities/relations 의 sources(JSON 배열)에서 loser id 를 keeper 로 치환·dedupe.

    근사중복 문서 병합 시 호출. 순서 보존하며 중복 제거. 변경된 행 수 반환.
    """
    changed = 0
    q = f"SELECT id, sources FROM {table}"
    params: list[Any] = []
    if theme_id is not None:
        q += " WHERE theme_id=?"
        params.append(theme_id)
    for r in conn.execute(q, tuple(params)).fetchall():
        try:
            srcs = json.loads(r["sources"] or "[]")
        except (TypeError, ValueError):
            continue
        if not any(s in losers for s in srcs):
            continue
        new: list[str] = []
        for s in srcs:
            s2 = keeper_id if s in losers else s
            if s2 not in new:
                new.append(s2)
        upd = f"UPDATE {table} SET sources=? WHERE id=?"
        upd_params: list[Any] = [json.dumps(new), r["id"]]
        if theme_id is not None:
            upd += " AND theme_id=?"
            upd_params.append(theme_id)
        conn.execute(upd, tuple(upd_params))
        changed += 1
    return changed


def merge_documents(conn: sqlite3.Connection, keeper_id: str,
                    loser_ids: list[str], theme_id: int | None = None) -> dict:
    """[파괴적] 근사중복 문서를 keeper 로 합치고 loser 문서 행을 삭제한다.

    데이터 보존: 삭제 전에 loser 를 가리키는 **모든 참조**(엔티티/관계 sources, proposals·extractions·raw_inbox 의 document_id)를 keeper 로 재배치한다. 큐 (refresh/expand)는 document_id UNIQUE 제약이 있어 loser 행은 삭제(전이적 작업이라 재생성 가능). 단일 트랜잭션으로 원자 처리. 반환: 재배치/삭제 카운트.
    """
    losers = {x for x in loser_ids if x and x != keeper_id}
    if not losers:
        return {"keeper": keeper_id, "losers": [], "deleted": 0}
    ph = ",".join("?" * len(losers))
    lo = list(losers)
    out = {"keeper": keeper_id, "losers": lo}
    try:
        out["entities_repointed"] = _repoint_sources_json(conn, "entities", keeper_id, losers, theme_id=theme_id)
        out["relations_repointed"] = _repoint_sources_json(conn, "relations", keeper_id, losers, theme_id=theme_id)
        where_theme = f" AND theme_id={theme_id}" if theme_id is not None else ""
        out["proposals"] = conn.execute(
            f"UPDATE proposals SET document_id=? WHERE document_id IN ({ph}){where_theme}",
            (keeper_id, *lo)).rowcount
        out["extractions"] = conn.execute(
            f"UPDATE extractions SET document_id=? WHERE document_id IN ({ph}){where_theme}",
            (keeper_id, *lo)).rowcount
        out["inbox"] = conn.execute(
            f"UPDATE raw_inbox SET document_id=? WHERE document_id IN ({ph}){where_theme}",
            (keeper_id, *lo)).rowcount
        conn.execute(f"DELETE FROM refresh_queue WHERE document_id IN ({ph}){where_theme}", lo)
        conn.execute(f"DELETE FROM expand_queue WHERE document_id IN ({ph}){where_theme}", lo)
        out["deleted"] = conn.execute(
            f"DELETE FROM documents WHERE id IN ({ph}){where_theme}", lo).rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return out


# --- search ---

def _fts_query(query: str) -> str:
    """자유 텍스트를 안전한 FTS5 MATCH 식으로 변환.

    다국어/한국어 형태소 분석(kiwipiepy)을 적용하여 실질 형태소 및 표면어를 추출하고
    각 토큰을 큰따옴표로 감싸 OR 로 잇는다(부분 매칭 지향).
    """
    if not query:
        return ""
    from ..retrieval.tokenizer import get_search_tokenizer

    toks = get_search_tokenizer().extract_search_tokens(query)
    if not toks:
        return ""
    return " OR ".join(f'"{t}"' for t in toks)


def fts_search(conn: sqlite3.Connection, query: str, limit: int = 20, theme_id: int | None = None) -> list[str]:
    """Search entity FTS and return list of entity IDs."""
    match = _fts_query(query)
    if not match:
        return []
    try:
        if theme_id is not None:
            rows = conn.execute(
                "SELECT f.entity_id FROM entities_fts f "
                "JOIN entities e ON f.entity_id = e.id "
                "WHERE e.theme_id = ? AND entities_fts MATCH ? "
                "ORDER BY rank LIMIT ?",
                (theme_id, match, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT entity_id FROM entities_fts WHERE entities_fts MATCH ? "
                "ORDER BY rank LIMIT ?",
                (match, limit),
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [r["entity_id"] for r in rows]


def sync_user_dictionary(conn: sqlite3.Connection) -> int:
    """DB 내의 기존 엔티티 이름과 별칭을 형태소 분석기 사용자 사전에 일괄 등록."""
    try:
        rows = conn.execute("SELECT name, aliases FROM entities").fetchall()
    except sqlite3.OperationalError:
        return 0
    from ..retrieval.tokenizer import get_search_tokenizer

    tokenizer = get_search_tokenizer()
    words: list[str] = []
    for r in rows:
        words.append(r["name"])
        try:
            aliases = _json.loads(r["aliases"] or "[]")
            words.extend(aliases)
        except Exception:
            pass
    return tokenizer.add_user_words(words)


def counts(
    conn: sqlite3.Connection,
    include_hidden: bool = True,
    include_oauth: bool = False,
    theme_id: int | None = None,
) -> dict[str, int]:
    tables = [
        "documents",
        "entities",
        "relations",
        "embeddings",
        "proposals",
        "jobs",
        "raw_inbox",
        "extractions",
        "refresh_queue",
        "purged_tombstones",
    ]
    if include_oauth:
        tables.extend([
            "oauth_clients",
            "oauth_tokens",
            "oauth_codes",
            "oauth_auth_requests",
        ])
    partitioned_tables = {
        "documents", "entities", "relations", "embeddings", "proposals",
        "jobs", "raw_inbox", "extractions", "refresh_queue", "purged_tombstones",
        "expand_queue", "document_snapshots", "document_presentations", "doc_shares",
    }
    out = {}
    for tbl in tables:
        has_t = conn.execute(f"SELECT 1 FROM sqlite_master WHERE type='table' AND name='{tbl}'").fetchone()
        if not has_t:
            continue
        if tbl == "documents":
            q = "SELECT COUNT(*) c FROM documents WHERE 1=1"
            params: list[Any] = []
            if not include_hidden:
                q += " AND hidden=0"
            if theme_id is not None:
                q += " AND theme_id=?"
                params.append(theme_id)
            out[tbl] = conn.execute(q, tuple(params)).fetchone()["c"]
        elif theme_id is not None and tbl in partitioned_tables:
            out[tbl] = conn.execute(f"SELECT COUNT(*) c FROM {tbl} WHERE theme_id=?", (theme_id,)).fetchone()["c"]
        else:
            out[tbl] = conn.execute(f"SELECT COUNT(*) c FROM {tbl}").fetchone()["c"]
    return out


def diagnose_graph(conn: sqlite3.Connection, theme_id: int | None = None) -> dict[str, Any]:
    """지식그래프 및 DB 무결성 상태 진단."""
    import json as _json
    import re as _re

    # 1. 고아 관계 (dangling relations: source_id 또는 target_id 가 entities 에 없음)
    dangling_query = """
        SELECT r.id, r.type, r.source_id, r.target_id
        FROM relations r
        WHERE (r.source_id NOT IN (SELECT id FROM entities)
           OR r.target_id NOT IN (SELECT id FROM entities))
    """
    dangling_params: list[Any] = []
    if theme_id is not None:
        dangling_query += " AND r.theme_id = ?"
        dangling_params.append(theme_id)
    dangling_rels = conn.execute(dangling_query, tuple(dangling_params)).fetchall()

    # 2. 유효하지 않은 문서 참조 (stale sources in entities & relations)
    doc_q = "SELECT id FROM documents"
    doc_p: list[Any] = []
    if theme_id is not None:
        doc_q += " WHERE theme_id = ?"
        doc_p.append(theme_id)
    doc_ids = {row[0] for row in conn.execute(doc_q, tuple(doc_p)).fetchall()}

    stale_entity_sources = []
    ghost_entities = []
    ent_q = "SELECT id, name, sources FROM entities"
    ent_p: list[Any] = []
    if theme_id is not None:
        ent_q += " WHERE theme_id = ?"
        ent_p.append(theme_id)
    entities_rows = conn.execute(ent_q, tuple(ent_p)).fetchall()

    for r in entities_rows:
        try:
            srcs = _json.loads(r["sources"] or "[]")
        except Exception:
            srcs = []
        valid_srcs = [s for s in srcs if s in doc_ids]
        if len(valid_srcs) != len(srcs):
            stale_entity_sources.append(
                {"id": r["id"], "name": r["name"], "invalid_sources": [s for s in srcs if s not in doc_ids]}
            )
        # 출처 문서가 없는 엔티티는 관계 유무와 관계없이 고아(Ghost)로 판정
        if len(valid_srcs) == 0:
            ghost_entities.append({"id": r["id"], "name": r["name"]})

    stale_relation_sources = []
    rel_q = "SELECT id, type, sources FROM relations"
    rel_p: list[Any] = []
    if theme_id is not None:
        rel_q += " WHERE theme_id = ?"
        rel_p.append(theme_id)
    for r in conn.execute(rel_q, tuple(rel_p)).fetchall():
        try:
            srcs = _json.loads(r["sources"] or "[]")
        except Exception:
            srcs = []
        valid_srcs = [s for s in srcs if s in doc_ids]
        if len(valid_srcs) != len(srcs):
            stale_relation_sources.append(
                {"id": r["id"], "type": r["type"], "invalid_sources": [s for s in srcs if s not in doc_ids]}
            )

    # 3. 고아 임베딩
    emb_q = "SELECT owner_id FROM embeddings WHERE owner_id NOT IN (SELECT id FROM entities)"
    emb_p: list[Any] = []
    if theme_id is not None:
        emb_q += " AND theme_id = ?"
        emb_p.append(theme_id)
    orphan_embeddings = conn.execute(emb_q, tuple(emb_p)).fetchall()

    # 4. 임베딩 누락 엔티티
    miss_q = "SELECT id, name FROM entities WHERE id NOT IN (SELECT owner_id FROM embeddings)"
    miss_p: list[Any] = []
    if theme_id is not None:
        miss_q += " AND theme_id = ?"
        miss_p.append(theme_id)
    missing_embeddings = conn.execute(miss_q, tuple(miss_p)).fetchall()

    # 5. FTS 동기화 여부
    total_entities = len(entities_rows)
    has_fts = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='entities_fts'").fetchone()
    total_fts = conn.execute("SELECT COUNT(*) FROM entities_fts").fetchone()[0] if has_fts else 0
    fts_desync = (theme_id is None) and (total_entities != total_fts)

    # 6. ADOC 오염 요약 검사
    def _is_corrupted(s: str | None) -> bool:
        if not s:
            return False
        pats = (
            r"(?:^|\n)={1,5}\s+",
            r"(?:^|\n)\[(?:NOTE|TIP|IMPORTANT|WARNING|CAUTION|quote|source)[^\]]*\]",
            r"(?:^|\n)\|===",
            r"(?:^|\n):[a-zA-Z0-9_-]+:",
            r"link:https?://",
        )
        return any(_re.search(p, s, _re.MULTILINE) for p in pats)

    corrupted_summaries = []
    has_ext = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='extractions'").fetchone()
    if has_ext:
        ext_q = "SELECT document_id, raw_response FROM extractions"
        ext_p: list[Any] = []
        if theme_id is not None:
            ext_q += " WHERE theme_id = ?"
            ext_p.append(theme_id)
        ext_q += " ORDER BY id DESC"
        for r in conn.execute(ext_q, tuple(ext_p)).fetchall():
            try:
                summ = _json.loads(r["raw_response"] or "{}").get("summary", "")
            except Exception:
                summ = ""
            if _is_corrupted(summ) and r["document_id"] not in [c["document_id"] for c in corrupted_summaries]:
                corrupted_summaries.append(
                    {"document_id": r["document_id"], "summary_preview": (summ[:100] + "...") if len(summ) > 100 else summ}
                )

    # 7. 툼스톤(소각 지문) 및 위반 검사
    has_tomb = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='purged_tombstones'"
    ).fetchone()
    tomb_q = "SELECT COUNT(*) FROM purged_tombstones"
    tomb_p: list[Any] = []
    if theme_id is not None:
        tomb_q += " WHERE theme_id = ?"
        tomb_p.append(theme_id)
    purged_tombstones_count = conn.execute(tomb_q, tuple(tomb_p)).fetchone()[0] if has_tomb else 0
    tombstone_violations = []
    if has_tomb and purged_tombstones_count > 0:
        viol_q = """
            SELECT d.id, d.url FROM documents d
            JOIN purged_tombstones t ON (d.id = t.id
               OR (d.canonical_url = t.canonical_url AND t.canonical_url IS NOT NULL)
               OR (d.content_hash = t.content_hash AND t.content_hash IS NOT NULL))
        """
        viol_p: list[Any] = []
        if theme_id is not None:
            viol_q += " WHERE d.theme_id = ? AND t.theme_id = ?"
            viol_p.extend([theme_id, theme_id])
        tombstone_violations = [
            dict(r) for r in conn.execute(viol_q, tuple(viol_p)).fetchall()
        ]

    is_healthy = (
        len(dangling_rels) == 0
        and len(stale_entity_sources) == 0
        and len(stale_relation_sources) == 0
        and len(ghost_entities) == 0
        and len(orphan_embeddings) == 0
        and len(tombstone_violations) == 0
        and not fts_desync
    )

    rel_cnt_q = "SELECT COUNT(*) FROM relations"
    rel_cnt_p: list[Any] = []
    if theme_id is not None:
        rel_cnt_q += " WHERE theme_id = ?"
        rel_cnt_p.append(theme_id)

    return {
        "is_healthy": is_healthy,
        "total_documents": len(doc_ids),
        "total_entities": total_entities,
        "total_relations": conn.execute(rel_cnt_q, tuple(rel_cnt_p)).fetchone()[0],
        "dangling_relations": [dict(r) for r in dangling_rels],
        "dangling_relations_count": len(dangling_rels),
        "stale_entity_sources_count": len(stale_entity_sources),
        "stale_entity_sources": stale_entity_sources[:50],
        "stale_relation_sources_count": len(stale_relation_sources),
        "stale_relation_sources": stale_relation_sources[:50],
        "ghost_entities_count": len(ghost_entities),
        "ghost_entities": ghost_entities[:50],
        "orphan_embeddings_count": len(orphan_embeddings),
        "missing_embeddings_count": len(missing_embeddings),
        "fts_desync": fts_desync,
        "fts_count": total_fts,
        "corrupted_summaries_count": len(corrupted_summaries),
        "corrupted_summaries": corrupted_summaries,
        "purged_tombstones_count": purged_tombstones_count,
        "tombstone_violations_count": len(tombstone_violations),
        "tombstone_violations": tombstone_violations[:50],
    }


def heal_graph(
    conn: sqlite3.Connection,
    progress_callback: Callable[[str], None] | None = None,
    theme_id: int | None = None,
) -> dict[str, int]:
    """지식그래프 참조 무결성 및 인덱스를 원클릭으로 자동 수복."""
    import json as _json

    healed: dict[str, int] = {
        "dangling_relations_removed": 0,
        "stale_entity_sources_cleaned": 0,
        "stale_relation_sources_cleaned": 0,
        "ghost_entities_pruned": 0,
        "orphan_embeddings_removed": 0,
        "fts_reindexed": 0,
    }

    if progress_callback:
        progress_callback("문서 및 엔티티 출처 참조 정제 중...")

    doc_q = "SELECT id FROM documents"
    doc_p: list[Any] = []
    if theme_id is not None:
        doc_q += " WHERE theme_id = ?"
        doc_p.append(theme_id)
    doc_ids = {row[0] for row in conn.execute(doc_q, tuple(doc_p)).fetchall()}

    # 0. 문서가 0건인 경우: 온톨로지 성립 근거가 전무하므로 지식 그래프 전량 소각
    if not doc_ids:
        ent_q = "SELECT COUNT(*) FROM entities"
        rel_q = "SELECT COUNT(*) FROM relations"
        emb_q = "SELECT COUNT(*) FROM embeddings"
        cnt_p = (theme_id,) if theme_id is not None else ()
        where_th = " WHERE theme_id = ?" if theme_id is not None else ""
        ent_count = conn.execute(ent_q + where_th, cnt_p).fetchone()[0]
        rel_count = conn.execute(rel_q + where_th, cnt_p).fetchone()[0]
        emb_count = conn.execute(emb_q + where_th, cnt_p).fetchone()[0]
        reset_graph(conn, theme_id=theme_id)
        healed["ghost_entities_pruned"] = ent_count
        healed["dangling_relations_removed"] = rel_count
        healed["orphan_embeddings_removed"] = emb_count
        healed["fts_reindexed"] = 0
        return healed

    # 1. 엔티티 stale sources 정제 및 고아 엔티티 선별
    ent_q = "SELECT id, name, sources FROM entities"
    ent_p: list[Any] = []
    if theme_id is not None:
        ent_q += " WHERE theme_id = ?"
        ent_p.append(theme_id)
    entities_rows = conn.execute(ent_q, tuple(ent_p)).fetchall()
    ghost_ids = []
    for r in entities_rows:
        try:
            srcs = _json.loads(r["sources"] or "[]")
        except Exception:
            srcs = []
        valid_srcs = [s for s in srcs if s in doc_ids]
        if len(valid_srcs) != len(srcs):
            upd = "UPDATE entities SET sources=? WHERE id=?"
            upd_p = [_json.dumps(valid_srcs, ensure_ascii=False), r["id"]]
            if theme_id is not None:
                upd += " AND theme_id=?"
                upd_p.append(theme_id)
            conn.execute(upd, tuple(upd_p))
            healed["stale_entity_sources_cleaned"] += 1
        # 출처 문서가 완전히 소멸한 엔티티는 관계 유무에 관계없이 고아로 판정
        if len(valid_srcs) == 0:
            ghost_ids.append(r["id"])

    # 2. 고아 엔티티 삭제
    if ghost_ids:
        for gid in ghost_ids:
            if theme_id is not None:
                conn.execute("DELETE FROM entities WHERE id=? AND theme_id=?", (gid, theme_id))
                conn.execute("DELETE FROM entities_fts WHERE entity_id=?", (gid,))
                conn.execute("DELETE FROM embeddings WHERE owner_id=? AND theme_id=?", (gid, theme_id))
            else:
                conn.execute("DELETE FROM entities WHERE id=?", (gid,))
                conn.execute("DELETE FROM entities_fts WHERE entity_id=?", (gid,))
                conn.execute("DELETE FROM embeddings WHERE owner_id=?", (gid,))
        healed["ghost_entities_pruned"] = len(ghost_ids)

    # 3. 관계 stale sources 정제
    rel_q = "SELECT id, sources FROM relations"
    rel_p: list[Any] = []
    if theme_id is not None:
        rel_q += " WHERE theme_id = ?"
        rel_p.append(theme_id)
    for r in conn.execute(rel_q, tuple(rel_p)).fetchall():
        try:
            srcs = _json.loads(r["sources"] or "[]")
        except Exception:
            srcs = []
        valid_srcs = [s for s in srcs if s in doc_ids]
        if len(valid_srcs) != len(srcs):
            upd = "UPDATE relations SET sources=? WHERE id=?"
            upd_p = [_json.dumps(valid_srcs, ensure_ascii=False), r["id"]]
            if theme_id is not None:
                upd += " AND theme_id=?"
                upd_p.append(theme_id)
            conn.execute(upd, tuple(upd_p))
            healed["stale_relation_sources_cleaned"] += 1

    # 4. 고아/출처 소멸 관계 연쇄 삭제 (dangling relations & ungrounded relations)
    del_rels_q = """
        DELETE FROM relations
        WHERE (source_id NOT IN (SELECT id FROM entities)
           OR target_id NOT IN (SELECT id FROM entities)
           OR sources = '[]'
           OR sources IS NULL)
    """
    del_rels_p: list[Any] = []
    if theme_id is not None:
        del_rels_q += " AND theme_id = ?"
        del_rels_p.append(theme_id)
    del_rels = conn.execute(del_rels_q, tuple(del_rels_p))
    healed["dangling_relations_removed"] = del_rels.rowcount if del_rels.rowcount > 0 else 0

    # 5. 고아 임베딩 삭제
    del_emb_q = "DELETE FROM embeddings WHERE owner_id NOT IN (SELECT id FROM entities)"
    del_emb_p: list[Any] = []
    if theme_id is not None:
        del_emb_q += " AND theme_id = ?"
        del_emb_p.append(theme_id)
    del_emb = conn.execute(del_emb_q, tuple(del_emb_p))
    healed["orphan_embeddings_removed"] = del_emb.rowcount if del_emb.rowcount > 0 else 0

    # 6. FTS 재색인 (theme_id=None 일 때 전체 rebuild, theme_id 지정 시 해당 테마 entity FTS 갱신)
    has_fts = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='entities_fts'").fetchone()
    if has_fts:
        curr_entities_q = "SELECT id, name, aliases, observations FROM entities"
        curr_p: list[Any] = []
        if theme_id is not None:
            curr_entities_q += " WHERE theme_id = ?"
            curr_p.append(theme_id)
        curr_entities = conn.execute(curr_entities_q, tuple(curr_p)).fetchall()
        if progress_callback:
            progress_callback(f"FTS5 전문 검색 색인 일괄 재구축 중 ({len(curr_entities)}개 엔티티)...")
        if theme_id is not None:
            conn.execute("DELETE FROM entities_fts WHERE entity_id IN (SELECT id FROM entities WHERE theme_id=?)", (theme_id,))
        else:
            conn.execute("DELETE FROM entities_fts")
        from ..retrieval.tokenizer import get_search_tokenizer

        tokenizer = get_search_tokenizer()
        parsed_entities = []
        all_user_words: list[str] = []
        for r in curr_entities:
            try:
                aliases = _json.loads(r["aliases"] or "[]")
            except Exception:
                aliases = []
            try:
                obs = _json.loads(r["observations"] or "[]")
            except Exception:
                obs = []
            all_user_words.append(r["name"])
            all_user_words.extend(aliases)
            parsed_entities.append((r["id"], r["name"], aliases, obs))

        # 사용자 단어 사전 일괄 등록 (반복 호출 시 형태소 분석 모델 재빌드 오버헤드 O(N) -> O(1) 방지)
        tokenizer.add_user_words(all_user_words)

        fts_rows = []
        for eid, name, aliases, obs in parsed_entities:
            raw_body = " \n".join(obs) + " " + " ".join(aliases)
            morph_tokens = tokenizer.extract_search_tokens(f"{name} {raw_body}")
            body = f"{raw_body}\n{' '.join(morph_tokens)}" if morph_tokens else raw_body
            fts_rows.append((eid, name, body))

        conn.executemany(
            "INSERT INTO entities_fts(entity_id,name,body) VALUES (?,?,?)",
            fts_rows,
        )
        healed["fts_reindexed"] = len(fts_rows)

    conn.commit()
    return healed


def is_tombstoned(
    conn: sqlite3.Connection,
    url: str | None = None,
    canonical_url: str | None = None,
    content_hash: str | None = None,
    theme_id: int | None = None,
) -> bool:
    """주어진 URL, Canonical URL, 또는 Content Hash가 툼스톤(소각된 지문)에 존재하는지 확인."""
    has_tbl = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='purged_tombstones'"
    ).fetchone()
    if not has_tbl:
        return False

    conditions = []
    params: list[Any] = []
    if url:
        conditions.append("url = ?")
        params.append(url)
    if canonical_url:
        conditions.append("canonical_url = ?")
        params.append(canonical_url)
    if content_hash:
        conditions.append("content_hash = ?")
        params.append(content_hash)

    if not conditions:
        return False

    query = f"SELECT 1 FROM purged_tombstones WHERE ({' OR '.join(conditions)})"
    if theme_id is not None:
        query += " AND theme_id = ?"
        params.append(theme_id)
    query += " LIMIT 1"
    row = conn.execute(query, tuple(params)).fetchone()
    return row is not None


def remove_tombstones(
    conn: sqlite3.Connection,
    target: str | None = None,
    *,
    doc_id: str | None = None,
    url: str | None = None,
    canonical_url: str | None = None,
    pattern: str | None = None,
    theme_id: int | None = None,
) -> dict[str, Any]:
    """등록된 소각 툼스톤을 해제(삭제)하여 문서 재수집 및 재적재를 허용."""
    from ..ingest.normalize import canonicalize_url

    has_tbl = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='purged_tombstones'"
    ).fetchone()
    if not has_tbl:
        return {"removed_count": 0, "removed_tombstones": [], "reset_inbox_count": 0}

    conditions = []
    params: list[Any] = []

    def _add_url_clause(u: str):
        cu = canonicalize_url(u)
        conditions.append("(url = ? OR canonical_url = ? OR url = ? OR canonical_url = ?)")
        params.extend([u, u, cu, cu])

    if doc_id:
        conditions.append("id = ?")
        params.append(doc_id)
    if url:
        _add_url_clause(url)
    if canonical_url:
        _add_url_clause(canonical_url)
    if pattern:
        p_str = f"%{pattern}%"
        conditions.append("(id LIKE ? OR url LIKE ? OR canonical_url LIKE ? OR reason LIKE ?)")
        params.extend([p_str, p_str, p_str, p_str])

    if target:
        if target.startswith("doc_"):
            conditions.append("id = ?")
            params.append(target)
        elif target.startswith("http://") or target.startswith("https://"):
            _add_url_clause(target)
        else:
            p_str = f"%{target}%"
            conditions.append("(id = ? OR url = ? OR canonical_url = ? OR url LIKE ? OR canonical_url LIKE ?)")
            params.extend([target, target, target, p_str, p_str])

    if not conditions:
        return {"removed_count": 0, "removed_tombstones": [], "reset_inbox_count": 0}

    where_clause = "(" + " OR ".join(conditions) + ")"
    if theme_id is not None:
        where_clause += " AND theme_id = ?"
        params.append(theme_id)

    matched = conn.execute(
        f"SELECT id, theme_id, url, canonical_url, content_hash, reason, purged_at FROM purged_tombstones WHERE {where_clause}",
        params,
    ).fetchall()
    matched_tombstones = [dict(r) for r in matched]
    if not matched_tombstones:
        return {"removed_count": 0, "removed_tombstones": [], "reset_inbox_count": 0}

    matched_ids = [r["id"] for r in matched_tombstones]
    ph = ",".join("?" for _ in matched_ids)
    del_q = f"DELETE FROM purged_tombstones WHERE id IN ({ph})"
    del_p: list[Any] = list(matched_ids)
    if theme_id is not None:
        del_q += " AND theme_id = ?"
        del_p.append(theme_id)
    conn.execute(del_q, tuple(del_p))

    reset_count = 0
    has_inbox = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='raw_inbox'"
    ).fetchone()
    if has_inbox:
        for t in matched_tombstones:
            candidates = set(filter(None, [t.get("url"), t.get("canonical_url"), t.get("id")]))
            t_theme = t.get("theme_id", 0)
            for cand in candidates:
                upd_inbox = """
                    UPDATE raw_inbox
                    SET status = 'pending', error = NULL, attempts = 0
                    WHERE status IN ('error', 'failed', 'stalled')
                      AND (payload = ? OR payload LIKE ?)
                      AND error LIKE '%tombstone%'
                """
                inbox_p: list[Any] = [cand, f"%{cand}%"]
                if theme_id is not None or t_theme is not None:
                    upd_inbox += " AND theme_id = ?"
                    inbox_p.append(theme_id if theme_id is not None else t_theme)
                cur = conn.execute(upd_inbox, tuple(inbox_p))
                reset_count += cur.rowcount

    conn.commit()
    return {
        "removed_count": len(matched_tombstones),
        "removed_tombstones": matched_tombstones,
        "reset_inbox_count": reset_count,
    }


def record_tombstone(
    conn: sqlite3.Connection,
    identifier: str,
    *,
    reason: str = "manual_tombstone",
    url: str | None = None,
    canonical_url: str | None = None,
    content_hash: str | None = None,
    theme_id: int = 0,
) -> None:
    now = time.time()
    conn.execute(
        """
        INSERT OR REPLACE INTO purged_tombstones (id, theme_id, url, canonical_url, content_hash, reason, purged_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (identifier, theme_id, url, canonical_url, content_hash, reason, now),
    )
    conn.commit()


tombstone = record_tombstone


def rollback_document_graph_contributions(
    conn: sqlite3.Connection,
    target_ids: list[str],
    *,
    prune_orphans: bool = False,
    theme_id: int | None = None,
) -> dict[str, int]:
    """Undo entity merges and optionally prune orphan graph nodes for the specified documents."""
    if not target_ids:
        return {"rolled_back_merges": 0, "pruned_entities": 0, "pruned_relations": 0}

    from ..extract.decision import get_resolution_log_from_meta, rollback_resolution

    rolled_back_merges = 0
    pruned_entities = 0
    pruned_relations = 0

    for did in target_ids:
        doc_q = "SELECT meta FROM documents WHERE id=?"
        doc_p: list[Any] = [did]
        if theme_id is not None:
            doc_q += " AND theme_id=?"
            doc_p.append(theme_id)
        row = conn.execute(doc_q, tuple(doc_p)).fetchone()
        if not row or not row["meta"]:
            continue
        try:
            meta = json.loads(row["meta"]) if isinstance(row["meta"], str) else row["meta"]
        except Exception:
            continue
        decisions = get_resolution_log_from_meta(meta)
        for decision in reversed(decisions):
            if decision.decision == "MERGE" and not getattr(decision, "rolled_back", False):
                if rollback_resolution(conn, decision):
                    rolled_back_merges += 1

    if prune_orphans:
        target_set = set(target_ids)
        ent_q = "SELECT id, sources FROM entities"
        ent_p: list[Any] = []
        if theme_id is not None:
            ent_q += " WHERE theme_id=?"
            ent_p.append(theme_id)
        for r in conn.execute(ent_q, tuple(ent_p)).fetchall():
            try:
                srcs = json.loads(r["sources"] or "[]")
            except Exception:
                srcs = []
            remaining = [s for s in srcs if s not in target_set]
            if len(remaining) != len(srcs):
                if not remaining:
                    del_ent = "DELETE FROM entities WHERE id=?"
                    del_ent_p: list[Any] = [r["id"]]
                    if theme_id is not None:
                        del_ent += " AND theme_id=?"
                        del_ent_p.append(theme_id)
                    conn.execute(del_ent, tuple(del_ent_p))
                    conn.execute("DELETE FROM entities_fts WHERE entity_id=?", (r["id"],))
                    del_emb = "DELETE FROM embeddings WHERE owner_id=?"
                    del_emb_p: list[Any] = [r["id"]]
                    if theme_id is not None:
                        del_emb += " AND theme_id=?"
                        del_emb_p.append(theme_id)
                    conn.execute(del_emb, tuple(del_emb_p))
                    pruned_entities += 1
                else:
                    upd_ent = "UPDATE entities SET sources=? WHERE id=?"
                    upd_ent_p: list[Any] = [json.dumps(remaining, ensure_ascii=False), r["id"]]
                    if theme_id is not None:
                        upd_ent += " AND theme_id=?"
                        upd_ent_p.append(theme_id)
                    conn.execute(upd_ent, tuple(upd_ent_p))

        rel_q = "SELECT id, sources FROM relations"
        rel_p: list[Any] = []
        if theme_id is not None:
            rel_q += " WHERE theme_id=?"
            rel_p.append(theme_id)
        for r in conn.execute(rel_q, tuple(rel_p)).fetchall():
            try:
                srcs = json.loads(r["sources"] or "[]")
            except Exception:
                srcs = []
            remaining = [s for s in srcs if s not in target_set]
            if len(remaining) != len(srcs):
                if not remaining:
                    del_rel = "DELETE FROM relations WHERE id=?"
                    del_rel_p: list[Any] = [r["id"]]
                    if theme_id is not None:
                        del_rel += " AND theme_id=?"
                        del_rel_p.append(theme_id)
                    conn.execute(del_rel, tuple(del_rel_p))
                    pruned_relations += 1
                else:
                    upd_rel = "UPDATE relations SET sources=? WHERE id=?"
                    upd_rel_p: list[Any] = [json.dumps(remaining, ensure_ascii=False), r["id"]]
                    if theme_id is not None:
                        upd_rel += " AND theme_id=?"
                        upd_rel_p.append(theme_id)
                    conn.execute(upd_rel, tuple(upd_rel_p))

        del_dang = """
            DELETE FROM relations
            WHERE (source_id NOT IN (SELECT id FROM entities)
               OR target_id NOT IN (SELECT id FROM entities)
               OR sources = '[]'
               OR sources IS NULL)
        """
        del_dang_p: list[Any] = []
        if theme_id is not None:
            del_dang += " AND theme_id=?"
            del_dang_p.append(theme_id)
        del_rels = conn.execute(del_dang, tuple(del_dang_p))
        if del_rels.rowcount and del_rels.rowcount > 0:
            pruned_relations += del_rels.rowcount

        conn.commit()

    return {"rolled_back_merges": rolled_back_merges, "pruned_entities": pruned_entities, "pruned_relations": pruned_relations}


def purge_document_cascade(
    conn: sqlite3.Connection,
    data_dir: Path,
    target_ids: list[str],
    reason: str = "manual_purge",
    dry_run: bool = False,
    tombstone: bool = True,
    progress_callback: Callable[[str], None] | None = None,
    theme_id: int | None = None,
) -> dict[str, Any]:
    """오염 문서를 L1/L2/DB/그래프/디스크에서 원자적으로 연쇄 소각."""
    import time
    from .raw import _artifacts_dir, _images_dir, artifact_paths

    if isinstance(target_ids, Path) and isinstance(reason, (list, tuple, set)):
        target_ids = list(reason)
        reason = "manual_purge"

    if not target_ids:
        return {"purged_count": 0, "target_documents": [], "tombstone_recorded": tombstone}

    ph = ",".join("?" for _ in target_ids)
    doc_q = f"SELECT id, theme_id, url, canonical_url, title, content_hash, fetched_at FROM documents WHERE id IN ({ph})"
    doc_p: list[Any] = list(target_ids)
    if theme_id is not None:
        doc_q += " AND theme_id = ?"
        doc_p.append(theme_id)

    # 1. 대상 문서 상세 조회
    docs = conn.execute(doc_q, tuple(doc_p)).fetchall()
    target_docs = [dict(d) for d in docs]
    matched_ids = [d["id"] for d in target_docs]

    if not matched_ids:
        return {"purged_count": 0, "target_documents": [], "tombstone_recorded": tombstone}

    ph_matched = ",".join("?" for _ in matched_ids)

    # 2. 로컬 디스크 파일 경로 탐색
    art_dir = _artifacts_dir(data_dir)
    img_dir = _images_dir(data_dir)
    attachments_root = data_dir / "raw" / "attachments"
    unlinked_candidates: list[Path] = []

    for did in matched_ids:
        for art in artifact_paths(data_dir, did):
            unlinked_candidates.append(art)
        for img in img_dir.glob(f"{did}_*"):
            if img.is_file():
                unlinked_candidates.append(img)
        attachment_dir = attachments_root / did
        if attachment_dir.exists():
            for attachment in attachment_dir.rglob("*"):
                if attachment.is_file():
                    unlinked_candidates.append(attachment)

    if dry_run:
        # Dry-run 보고서 반환
        return {
            "dry_run": True,
            "purged_count": len(matched_ids),
            "target_documents": target_docs,
            "disk_files_count": len(unlinked_candidates),
            "disk_files": [str(p) for p in unlinked_candidates[:50]],
            "tombstone_recorded": tombstone,
        }

    # 3. 실제 소각 실행 (DB 트랜잭션)
    if progress_callback:
        progress_callback("L1 인박스 및 DB 레코드 원자적 연쇄 삭제 중...")

    stats: dict[str, Any] = {
        "dry_run": False,
        "purged_count": len(matched_ids),
        "target_documents": target_docs,
        "tombstone_recorded": tombstone,
        "purged_tombstones_registered": len(matched_ids) if tombstone else 0,
    }

    stats["graph_rollback"] = rollback_document_graph_contributions(conn, matched_ids, prune_orphans=False, theme_id=theme_id)

    try:
        conn.execute("BEGIN IMMEDIATE")
        now = time.time()
        if tombstone:
            for d in target_docs:
                row_theme = theme_id if theme_id is not None else d.get("theme_id", 0)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO purged_tombstones (id, theme_id, url, canonical_url, content_hash, reason, purged_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (d["id"], row_theme, d.get("url"), d.get("canonical_url"), d.get("content_hash"), reason, now),
                )

        where_th = f" AND theme_id = {theme_id}" if theme_id is not None else ""
        # 8개 DB 테이블 연쇄 삭제
        stats["deleted_expand_queue"] = conn.execute(
            f"DELETE FROM expand_queue WHERE document_id IN ({ph_matched}){where_th}", matched_ids
        ).rowcount
        stats["deleted_refresh_queue"] = conn.execute(
            f"DELETE FROM refresh_queue WHERE document_id IN ({ph_matched}){where_th}", matched_ids
        ).rowcount
        stats["deleted_doc_shares"] = conn.execute(
            f"DELETE FROM doc_shares WHERE document_id IN ({ph_matched}){where_th}", matched_ids
        ).rowcount
        stats["deleted_document_snapshots"] = conn.execute(
            f"DELETE FROM document_snapshots WHERE document_id IN ({ph_matched}){where_th}", matched_ids
        ).rowcount
        stats["deleted_extractions"] = conn.execute(
            f"DELETE FROM extractions WHERE document_id IN ({ph_matched}){where_th}", matched_ids
        ).rowcount
        stats["deleted_proposals"] = conn.execute(
            f"DELETE FROM proposals WHERE document_id IN ({ph_matched}){where_th}", matched_ids
        ).rowcount
        stats["deleted_raw_inbox"] = conn.execute(
            f"DELETE FROM raw_inbox WHERE document_id IN ({ph_matched}){where_th}", matched_ids
        ).rowcount
        stats["deleted_documents"] = conn.execute(
            f"DELETE FROM documents WHERE id IN ({ph_matched}){where_th}", matched_ids
        ).rowcount

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    # 4. 물리 파일 Unlink
    if progress_callback:
        progress_callback(f"물리 아티팩트 및 첨부 디스크 파일 삭제 중 ({len(unlinked_candidates)}개 파일)...")

    unlinked_count = 0
    for p in unlinked_candidates:
        try:
            if p.is_file():
                p.unlink()
                unlinked_count += 1
        except Exception:
            pass
    for did in matched_ids:
        attachment_dir = attachments_root / did
        if attachment_dir.exists():
            try:
                shutil.rmtree(attachment_dir)
            except Exception:
                pass
    stats["disk_files_unlinked"] = unlinked_count

    # 5. 지식그래프 참조 무결성 수복
    if progress_callback:
        progress_callback("지식그래프 참조 무결성 수복 및 FTS 재구축 중...")

    heal_stats = heal_graph(conn, progress_callback=progress_callback, theme_id=theme_id)
    stats["graph_healed"] = heal_stats

    # 6. DB 공간 회수 (VACUUM)
    if progress_callback:
        progress_callback("데이터베이스 물리 압축(VACUUM) 및 용량 회수 중...")

    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM")
        stats["vacuum_executed"] = True
    except Exception:
        stats["vacuum_executed"] = False

    return stats


def audit_residuals(
    conn: sqlite3.Connection,
    data_dir: Path,
    pattern_or_id: str | None = None,
    theme_id: int | None = None,
) -> dict[str, Any]:
    """오염 잔재 0건 여부 및 시스템 스토리지 무결성 전수 감사."""
    from .raw import _artifacts_dir, _images_dir

    report: dict[str, Any] = {
        "pattern": pattern_or_id or "",
        "clean": True,
        "matching_documents_count": 0,
        "matching_documents": [],
        "matching_inbox_count": 0,
        "matching_extractions_count": 0,
        "matching_snapshots_count": 0,
        "matching_entity_sources_count": 0,
        "matching_relation_sources_count": 0,
        "matching_disk_artifacts_count": 0,
        "matching_disk_images_count": 0,
        "purged_tombstones_count": 0,
        "tombstone_violations_count": 0,
        "freelist_pages": 0,
        "reclaimable_bytes": 0,
    }

    # 1. 툼스톤 통계
    has_tomb = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='purged_tombstones'"
    ).fetchone()
    if has_tomb:
        tomb_q = "SELECT COUNT(*) FROM purged_tombstones"
        tomb_p: list[Any] = []
        if theme_id is not None:
            tomb_q += " WHERE theme_id = ?"
            tomb_p.append(theme_id)
        report["purged_tombstones_count"] = conn.execute(tomb_q, tuple(tomb_p)).fetchone()[0]
        viol_q = """
            SELECT d.id, d.url FROM documents d
            JOIN purged_tombstones t ON (d.id = t.id 
               OR (d.canonical_url = t.canonical_url AND t.canonical_url IS NOT NULL)
               OR (d.content_hash = t.content_hash AND t.content_hash IS NOT NULL))
        """
        viol_p: list[Any] = []
        if theme_id is not None:
            viol_q += " WHERE d.theme_id = ? AND t.theme_id = ?"
            viol_p.extend([theme_id, theme_id])
        violations = conn.execute(viol_q, tuple(viol_p)).fetchall()
        report["tombstone_violations_count"] = len(violations)
        if len(violations) > 0:
            report["clean"] = False

    if pattern_or_id:
        patt = f"%{pattern_or_id}%"
        # 2. documents 매칭
        doc_q = "SELECT id, url, title FROM documents WHERE (id LIKE ? OR url LIKE ? OR canonical_url LIKE ? OR title LIKE ? OR raw_text LIKE ?)"
        doc_p: list[Any] = [patt, patt, patt, patt, patt]
        if theme_id is not None:
            doc_q += " AND theme_id = ?"
            doc_p.append(theme_id)
        docs = conn.execute(doc_q, tuple(doc_p)).fetchall()
        report["matching_documents_count"] = len(docs)
        report["matching_documents"] = [dict(d) for d in docs[:20]]

        # 3. raw_inbox 매칭
        inbox_q = "SELECT COUNT(*) FROM raw_inbox WHERE (document_id LIKE ? OR payload LIKE ?)"
        inbox_p: list[Any] = [patt, patt]
        if theme_id is not None:
            inbox_q += " AND theme_id = ?"
            inbox_p.append(theme_id)
        report["matching_inbox_count"] = conn.execute(inbox_q, tuple(inbox_p)).fetchone()[0]

        # 4. extractions 매칭
        ext_q = "SELECT COUNT(*) FROM extractions WHERE (document_id LIKE ? OR raw_response LIKE ?)"
        ext_p: list[Any] = [patt, patt]
        if theme_id is not None:
            ext_q += " AND theme_id = ?"
            ext_p.append(theme_id)
        report["matching_extractions_count"] = conn.execute(ext_q, tuple(ext_p)).fetchone()[0]

        # 5. snapshots 매칭
        snap_q = "SELECT COUNT(*) FROM document_snapshots WHERE (document_id LIKE ? OR raw_text LIKE ?)"
        snap_p: list[Any] = [patt, patt]
        if theme_id is not None:
            snap_q += " AND theme_id = ?"
            snap_p.append(theme_id)
        report["matching_snapshots_count"] = conn.execute(snap_q, tuple(snap_p)).fetchone()[0]

        # 6. entities / relations sources 매칭
        ent_q = "SELECT COUNT(*) FROM entities WHERE sources LIKE ?"
        ent_p: list[Any] = [patt]
        if theme_id is not None:
            ent_q += " AND theme_id = ?"
            ent_p.append(theme_id)
        report["matching_entity_sources_count"] = conn.execute(ent_q, tuple(ent_p)).fetchone()[0]

        rel_q = "SELECT COUNT(*) FROM relations WHERE sources LIKE ?"
        rel_p: list[Any] = [patt]
        if theme_id is not None:
            rel_q += " AND theme_id = ?"
            rel_p.append(theme_id)
        report["matching_relation_sources_count"] = conn.execute(rel_q, tuple(rel_p)).fetchone()[0]

        # 7. 디스크 파일 매칭
        art_dir = _artifacts_dir(data_dir)
        img_dir = _images_dir(data_dir)
        art_matches = list(art_dir.glob(f"*{pattern_or_id}*"))
        img_matches = list(img_dir.glob(f"*{pattern_or_id}*"))
        report["matching_disk_artifacts_count"] = len(art_matches)
        report["matching_disk_images_count"] = len(img_matches)

        total_matches = (
            len(docs)
            + report["matching_inbox_count"]
            + report["matching_extractions_count"]
            + report["matching_snapshots_count"]
            + report["matching_entity_sources_count"]
            + report["matching_relation_sources_count"]
            + len(art_matches)
            + len(img_matches)
        )
        if total_matches > 0:
            report["clean"] = False

    # 8. DB Freelist 측정
    freelist_count = conn.execute("PRAGMA freelist_count").fetchone()[0]
    page_size = conn.execute("PRAGMA page_size").fetchone()[0]
    report["freelist_pages"] = freelist_count
    report["reclaimable_bytes"] = freelist_count * page_size

    return report


# --- 원문 절단(Truncation) 탐지 및 메타데이터 소급 갱신 ---

def scan_truncation_status(
    conn: sqlite3.Connection,
    doc_id: str | None = None,
    theme_id: int | None = None,
) -> dict:
    """데이터베이스 내 문서들의 원문 절단(슬라이싱) 현황 및 메타데이터 기록 상태 스캔.

    - content_hash 불일치: 수집 당시 원문 전체로 계산된 hash != DB에 저장된 raw_text hash
    - 산문(Prose) 20,000자 상한: 표를 제외한 본문 글자 수가 20,000자에 도달
    - raw_truncated 메타데이터 기록 여부 분류
    """
    from ..extract.table_budget import extract_tables_from_text
    from ..ingest.normalize import content_hash

    where_clauses = []
    params: list[Any] = []
    if doc_id:
        where_clauses.append("id = ?")
        params.append(doc_id)
    if theme_id is not None:
        where_clauses.append("theme_id = ?")
        params.append(theme_id)

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    rows = conn.execute(
        f"SELECT id, title, url, source_type, raw_text, content_hash, meta "
        f"FROM documents {where_sql} ORDER BY fetched_at DESC, id DESC",
        tuple(params),
    ).fetchall()

    total = len(rows)
    intact_count = 0
    recorded_truncated = 0
    unmarked_truncated = 0
    unmarked_items = []

    for r in rows:
        meta_raw = r["meta"]
        meta = {}
        if meta_raw:
            try:
                meta = json.loads(meta_raw)
            except Exception:
                meta = {}

        raw_text = r["raw_text"] or ""
        raw_len = len(raw_text)
        src_type = r["source_type"] or "web"

        # 1. content_hash 재계산
        if src_type in ("youtube", "text"):
            calc_hash = content_hash(raw_text)
        else:
            calc_hash = content_hash(r["title"] or "", raw_text)

        hash_mismatch = bool(r["content_hash"] and calc_hash != r["content_hash"])

        # 2. 산문(Prose) 상한 검사
        from ..config import get_settings

        settings = get_settings()
        budget = settings.pdf_max_extract_chars if src_type == "pdf" else settings.raw_char_budget
        tables, prose_parts = extract_tables_from_text(raw_text)
        prose_len = sum(len(p) for p in prose_parts)
        is_limit = (budget > 0 and (prose_len == budget or raw_len == budget)) or (src_type != "pdf" and (prose_len == 20000 or raw_len == 20000))

        # 3. 메타데이터 기록 상태
        is_meta_truncated = bool(meta.get("raw_truncated"))
        is_actually_truncated = hash_mismatch or is_limit or is_meta_truncated

        reasons = []
        if hash_mismatch:
            reasons.append("hash_mismatch (원문 > 적재본)")
        if is_limit:
            reasons.append(f"{budget}char_limit (산문 {prose_len}자 / 전체 {raw_len}자)")

        if is_meta_truncated:
            recorded_truncated += 1
        elif is_actually_truncated:
            unmarked_truncated += 1
            unmarked_items.append({
                "id": r["id"],
                "title": r["title"] or "(제목 없음)",
                "url": r["url"] or "",
                "source_type": src_type,
                "raw_len": raw_len,
                "prose_len": prose_len,
                "table_count": len(tables),
                "hash_mismatch": hash_mismatch,
                "is_limit": is_limit,
                "reasons": reasons,
                "meta": meta,
            })
        else:
            intact_count += 1

    return {
        "total_documents": total,
        "intact_count": intact_count,
        "recorded_truncated_count": recorded_truncated,
        "unmarked_truncated_count": unmarked_truncated,
        "unmarked_items": unmarked_items,
    }


def backfill_truncation_metadata(
    conn: sqlite3.Connection,
    *,
    doc_id: str | None = None,
    force: bool = False,
    mark_refresh: bool = False,
    theme_id: int | None = None,
) -> dict:
    """메타데이터를 누락한 절단 문서의 documents.meta에 raw_truncated를 소급 갱신."""
    from ..extract.table_budget import extract_tables_from_text
    from ..ingest.normalize import content_hash

    scan = scan_truncation_status(conn, doc_id=doc_id, theme_id=theme_id)
    targets = scan["unmarked_items"]

    if force:
        where_clauses = []
        params: list[Any] = []
        if doc_id:
            where_clauses.append("id = ?")
            params.append(doc_id)
        if theme_id is not None:
            where_clauses.append("theme_id = ?")
            params.append(theme_id)
        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        rows = conn.execute(
            f"SELECT id, title, url, source_type, raw_text, content_hash, meta "
            f"FROM documents {where_sql}",
            tuple(params),
        ).fetchall()
        targets = []
        for r in rows:
            meta = {}
            if r["meta"]:
                try:
                    meta = json.loads(r["meta"])
                except Exception:
                    meta = {}
            raw_text = r["raw_text"] or ""
            src_type = r["source_type"] or "web"

            if src_type in ("youtube", "text"):
                calc_hash = content_hash(raw_text)
            else:
                calc_hash = content_hash(r["title"] or "", raw_text)
            hash_mismatch = bool(r["content_hash"] and calc_hash != r["content_hash"])
            tables, prose_parts = extract_tables_from_text(raw_text)
            prose_len = sum(len(p) for p in prose_parts)
            from ..config import get_settings

            settings = get_settings()
            budget = settings.pdf_max_extract_chars if src_type == "pdf" else settings.raw_char_budget
            is_limit = (prose_len == budget) or (len(raw_text) == budget)
            is_trunc = hash_mismatch or is_limit
            targets.append({
                "id": r["id"],
                "title": r["title"] or "(제목 없음)",
                "url": r["url"] or "",
                "source_type": src_type,
                "raw_len": len(raw_text),
                "is_truncated": is_trunc,
                "meta": meta,
            })

    updated_count = 0
    refreshed_count = 0

    for item in targets:
        doc_id_val = item["id"]
        meta = item["meta"]
        raw_len = item.get("raw_len", len(meta.get("raw_text", "")))
        is_trunc = item.get("is_truncated", True)

        meta["raw_truncated"] = is_trunc
        meta["raw_chars"] = raw_len
        if "orig_chars" not in meta and not is_trunc:
            meta["orig_chars"] = raw_len

        upd = "UPDATE documents SET meta=? WHERE id=?"
        upd_p: list[Any] = [json.dumps(meta, ensure_ascii=False), doc_id_val]
        if theme_id is not None:
            upd += " AND theme_id=?"
            upd_p.append(theme_id)
        conn.execute(upd, tuple(upd_p))
        updated_count += 1

        if mark_refresh and is_trunc:
            payload = item.get("url") or item.get("title") or doc_id_val
            enqueue_refresh(conn, document_id=doc_id_val, payload=payload, reason="truncated_backfill", theme_id=theme_id if theme_id is not None else 0)
            refreshed_count += 1

    conn.commit()
    return {
        "scanned_total": scan["total_documents"],
        "updated_count": updated_count,
        "refreshed_count": refreshed_count,
        "items": targets,
    }


def extract_stt_transcript(raw_text: str | None, meta: dict | None = None) -> dict | None:
    """STT가 적용된 문서에 한해 순수 음성 전사 전문과 세그먼트 데이터를 추출 (저작권 보호: 비-STT 문서는 None 반환)."""
    meta_dict = meta or {}
    raw_str = raw_text or ""

    is_stt = bool(
        meta_dict.get("is_stt")
        or meta_dict.get("stt_applied")
        or meta_dict.get("stt")
        or (isinstance(meta_dict.get("transcript_segments"), list) and len(meta_dict["transcript_segments"]) > 0)
        or ("[영상 음성 전사 (STT)]" in raw_str)
        or ("[영상 자막 / 음성 전사 (STT)]" in raw_str)
        or ("[음성 전사 (STT)]" in raw_str)
    )
    if not is_stt:
        return None

    segments = meta_dict.get("transcript_segments") or []
    text_body = ""

    for marker in ["[영상 음성 전사 (STT)]", "[영상 자막 / 음성 전사 (STT)]", "[음성 전사 (STT)]"]:
        if marker in raw_str:
            after = raw_str.split(marker, 1)[1].lstrip("\r\n")
            for end_marker in ["\n\n[영상 설명]", "\n\n[태그]", "\n\n[부록]", "\n\n[출처]"]:
                if end_marker in after:
                    after = after.split(end_marker, 1)[0]
            text_body = after.strip()
            break

    if not text_body and segments:
        lines = []
        for s in segments:
            start_f = float(s.get("start_sec", s.get("start", 0.0)))
            total_sec = int(start_f)
            h = total_sec // 3600
            m = (total_sec % 3600) // 60
            sec = total_sec % 60
            ts = f"{h:02d}:{m:02d}:{sec:02d}" if h > 0 else f"{m:02d}:{sec:02d}"
            txt = str(s.get("text", "")).strip()
            lines.append(f"[{ts}] {txt}")
        text_body = "\n".join(lines)

    if not text_body and not segments:
        return None

    duration_sec = float(meta_dict.get("duration_sec") or 0.0)
    last_end = 0.0
    if segments:
        last_end = float(segments[-1].get("end_sec", segments[-1].get("end", 0.0)))
    duration_gap = bool(duration_sec > 0 and last_end > 0 and (duration_sec - last_end > 120.0))

    stt_truncated = bool(
        meta_dict.get("stt_truncated")
        or meta_dict.get("raw_truncated")
        or duration_gap
    )

    return {
        "text": text_body,
        "segments": segments,
        "duration_sec": duration_sec,
        "is_stt": True,
        "stt_truncated": stt_truncated,
    }


def scan_stt_documents(
    conn: sqlite3.Connection,
    doc_id: str | None = None,
    theme_id: int | None = None,
) -> dict:
    """데이터베이스 내 문서들 중 STT(음성 인식/전사) 적용 문서 및 절단/본문 작성 상태 스캔.

    판정 기준:
    1. STT 적용 감지: meta.is_stt 플래그, transcript_segments 존재, 오디오 캐시 사용, STT 본문 헤더
    2. STT 절단(Truncation) 감지:
       - 글자 수 상한 슬라이싱 (stt_truncated, raw_truncated, orig_chars > raw_chars)
       - 재생 시간 갭 (duration_sec 대비 마지막 세그먼트 간 120초 이상 차이)
    3. 본문 작성(Detail Written) 연계:
       - 절단된 STT 상태에서 LLM 상세 본문(detail)이 이미 작성되었는지 여부 확인
    """
    where_clauses = []
    params: list[Any] = []
    if doc_id:
        where_clauses.append("id = ?")
        params.append(doc_id)
    if theme_id is not None:
        where_clauses.append("theme_id = ?")
        params.append(theme_id)

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    rows = conn.execute(
        f"SELECT id, title, url, source_type, raw_text, meta, detail "
        f"FROM documents {where_sql} ORDER BY fetched_at DESC, id DESC",
        tuple(params),
    ).fetchall()

    total = len(rows)
    stt_documents = []
    unmarked_items = []
    recorded_stt = 0
    stt_truncated_items = []
    stt_truncated_with_detail_items = []

    for r in rows:
        meta_raw = r["meta"]
        meta = {}
        if meta_raw:
            try:
                meta = json.loads(meta_raw)
            except Exception:
                meta = {}

        raw_text = r["raw_text"] or ""
        raw_len = len(raw_text)
        src_type = r["source_type"] or "web"
        detail = r["detail"] or ""
        has_detail = bool(detail and len(detail.strip()) > 0)

        # 1. 명시적 메타 플래그
        is_meta_stt = bool(meta.get("is_stt") or meta.get("stt_applied") or meta.get("stt"))

        # 2. STT 전사 세그먼트 존재 여부 (STT 실행 시에만 타임스탬프 세그먼트가 생성됨)
        segments = meta.get("transcript_segments")
        has_segments = bool(isinstance(segments, list) and len(segments) > 0)

        # 3. 비디오 오디오 캐시 사용 여부 (오디오 다운로드 및 STT 파이프라인에서만 생성됨)
        has_audio_cache = bool(
            (meta.get("video_cached") or meta.get("video_cache_used"))
            and meta.get("has_transcript")
        )

        # 4. 명시적 STT 본문 헤더
        has_stt_header = (
            "[영상 음성 전사 (STT)]" in raw_text
            or "[영상 자막 / 음성 전사 (STT)]" in raw_text
            or "[음성 전사 (STT)]" in raw_text
        )

        is_stt_detected = is_meta_stt or has_segments or has_audio_cache or has_stt_header

        reasons = []
        if is_meta_stt:
            reasons.append("meta.is_stt")
        if has_segments:
            reasons.append(f"transcript_segments ({len(segments)} segments)")
        if has_audio_cache:
            reasons.append("video audio cache with transcript")
        if has_stt_header:
            reasons.append("raw_text STT header")

        if is_stt_detected:
            # 복합 절단 조건 판정
            duration_sec = float(meta.get("duration_sec") or 0.0)
            last_seg_end = 0.0
            if has_segments:
                last_seg_end = float(segments[-1].get("end_sec", segments[-1].get("end", 0.0)))

            duration_gap = bool(duration_sec > 0 and last_seg_end > 0 and (duration_sec - last_seg_end > 120.0))
            is_sliced = bool(
                meta.get("stt_truncated")
                or meta.get("raw_truncated")
                or (meta.get("orig_chars", 0) > meta.get("raw_chars", 0) > 0)
                or (meta.get("stt_orig_chars", 0) > meta.get("stt_raw_chars", 0) > 0)
            )
            is_stt_truncated = bool(is_sliced or duration_gap)

            trunc_reasons = []
            if is_sliced:
                orig = meta.get("stt_orig_chars") or meta.get("orig_chars") or 0
                raw = meta.get("stt_raw_chars") or meta.get("raw_chars") or raw_len
                if orig > raw > 0:
                    trunc_reasons.append(f"char_limit (원문 {orig:,}자 → 적재 {raw:,}자)")
                else:
                    trunc_reasons.append("char_limit (글자수 상한 슬라이싱)")
            if duration_gap:
                d_min = int(duration_sec // 60)
                s_min = int(last_seg_end // 60)
                trunc_reasons.append(f"duration_gap (전체 {d_min}분 중 {s_min}분까지만 전사됨)")

            item_info = {
                "id": r["id"],
                "title": r["title"] or "(제목 없음)",
                "url": r["url"] or "",
                "source_type": src_type,
                "is_meta_stt": is_meta_stt,
                "reasons": reasons,
                "meta": meta,
                "is_stt_truncated": is_stt_truncated,
                "stt_trunc_reasons": trunc_reasons,
                "has_detail": has_detail,
                "duration_sec": duration_sec,
                "last_seg_end": last_seg_end,
            }
            stt_documents.append(item_info)
            if is_meta_stt:
                recorded_stt += 1
            else:
                unmarked_items.append(item_info)

            if is_stt_truncated:
                stt_truncated_items.append(item_info)
                if has_detail:
                    stt_truncated_with_detail_items.append(item_info)

    return {
        "total_documents": total,
        "stt_detected_count": len(stt_documents),
        "recorded_stt_count": recorded_stt,
        "unmarked_stt_count": len(unmarked_items),
        "unmarked_items": unmarked_items,
        "stt_documents": stt_documents,
        "stt_truncated_count": len(stt_truncated_items),
        "stt_truncated_with_detail_count": len(stt_truncated_with_detail_items),
        "stt_intact_count": len(stt_documents) - len(stt_truncated_items),
        "stt_truncated_items": stt_truncated_items,
        "stt_truncated_with_detail_items": stt_truncated_with_detail_items,
    }


def backfill_stt_metadata(
    conn: sqlite3.Connection,
    *,
    doc_id: str | None = None,
    force: bool = False,
    theme_id: int | None = None,
) -> dict:
    """STT가 적용되었으나 메타데이터(is_stt, stt_truncated)가 누락된 문서에 메타데이터를 소급 갱신."""
    scan = scan_stt_documents(conn, doc_id=doc_id, theme_id=theme_id)
    targets = scan["stt_documents"] if force else scan["unmarked_items"]

    updated_count = 0
    for item in targets:
        doc_id_val = item["id"]
        meta = item["meta"]
        meta["is_stt"] = True
        meta["stt"] = True
        meta["stt_truncated"] = bool(item.get("is_stt_truncated"))

        upd = "UPDATE documents SET meta=? WHERE id=?"
        upd_p: list[Any] = [json.dumps(meta, ensure_ascii=False), doc_id_val]
        if theme_id is not None:
            upd += " AND theme_id=?"
            upd_p.append(theme_id)
        conn.execute(upd, tuple(upd_p))
        updated_count += 1

    conn.commit()
    return {
        "scanned_total": scan["total_documents"],
        "stt_detected_count": scan["stt_detected_count"],
        "stt_truncated_count": scan["stt_truncated_count"],
        "stt_truncated_with_detail_count": scan["stt_truncated_with_detail_count"],
        "updated_count": updated_count,
        "items": targets,
    }


# --- syndication dispatches ---


def create_syndication_dispatch(
    conn: sqlite3.Connection,
    *,
    theme_id: int = 0,
    target_theme_id: int | None = None,
    document_id: str | None = None,
    item_id: str | None = None,
    account_id: str | None = None,
    target_url: str | None = None,
    mode: str = "cross_theme",
    status: str = "pending",
    remote_ref: str | None = None,
    payload: str | None = None,
    error: str | None = None,
    dispatched_by_user_id: int | None = None,
    applied_focus: str | None = None,
    dispatched_at: float | None = None,
    dispatch_id: str | None = None,
) -> str:
    """Create a new syndication dispatch record."""
    did = dispatch_id or f"disp_{secrets.token_hex(12)}"
    now = time.time()
    disp_at = dispatched_at
    if status == "done" and disp_at is None:
        disp_at = now
    conn.execute(
        """INSERT INTO syndication_dispatches
        (id, theme_id, target_theme_id, item_id, account_id, document_id,
         target_url, mode, status, remote_ref, payload, error,
         dispatched_by_user_id, applied_focus, dispatched_at, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            did,
            theme_id,
            target_theme_id if target_theme_id is not None else 0,
            item_id,
            account_id,
            document_id,
            target_url,
            mode,
            status,
            remote_ref,
            payload,
            error,
            dispatched_by_user_id,
            applied_focus,
            disp_at,
            now,
            now,
        ),
    )
    conn.commit()
    return did


def get_syndication_dispatch(
    conn: sqlite3.Connection,
    dispatch_id: str,
    *,
    theme_id: int | None = None,
) -> dict[str, Any] | None:
    """Retrieve a syndication dispatch record by ID."""
    query = "SELECT * FROM syndication_dispatches WHERE id = ?"
    params: list[Any] = [dispatch_id]
    if theme_id is not None:
        query += " AND (theme_id = ? OR target_theme_id = ?)"
        params.extend([theme_id, theme_id])
    row = conn.execute(query, tuple(params)).fetchone()
    if row is None:
        return None
    return dict(row)


def update_syndication_dispatch(
    conn: sqlite3.Connection,
    dispatch_id: str,
    *,
    status: str | None = None,
    error: str | None = None,
    remote_ref: str | None = None,
    dispatched_at: float | None = None,
    applied_focus: str | None = None,
) -> bool:
    """Update an existing syndication dispatch record."""
    clauses: list[str] = ["updated_at = ?"]
    now = time.time()
    params: list[Any] = [now]
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
        if status == "done" and dispatched_at is None:
            dispatched_at = now
    if error is not None:
        clauses.append("error = ?")
        params.append(error)
    if remote_ref is not None:
        clauses.append("remote_ref = ?")
        params.append(remote_ref)
    if dispatched_at is not None:
        clauses.append("dispatched_at = ?")
        params.append(dispatched_at)
    if applied_focus is not None:
        clauses.append("applied_focus = ?")
        params.append(applied_focus)
    params.append(dispatch_id)
    cur = conn.execute(
        f"UPDATE syndication_dispatches SET {', '.join(clauses)} WHERE id = ?",
        tuple(params),
    )
    conn.commit()
    return cur.rowcount > 0


def list_syndication_dispatches(
    conn: sqlite3.Connection,
    *,
    theme_id: int | None = None,
    target_theme_id: int | None = None,
    document_id: str | None = None,
    status: str | None = None,
    mode: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List syndication dispatch records matching given criteria."""
    query = "SELECT * FROM syndication_dispatches"
    conditions: list[str] = []
    params: list[Any] = []
    if theme_id is not None:
        conditions.append("theme_id = ?")
        params.append(theme_id)
    if target_theme_id is not None:
        conditions.append("target_theme_id = ?")
        params.append(target_theme_id)
    if document_id is not None:
        conditions.append("document_id = ?")
        params.append(document_id)
    if status is not None:
        conditions.append("status = ?")
        params.append(status)
    if mode is not None:
        conditions.append("mode = ?")
        params.append(mode)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = conn.execute(query, tuple(params)).fetchall()
    return [dict(row) for row in rows]
