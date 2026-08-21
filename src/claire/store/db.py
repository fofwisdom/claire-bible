"""SQLite 정본 스토어 — 스키마 + 마이그레이션 + 기본 CRUD.

정본은 이 단일 파일. vault(.md)는 export-only 투영(store/vault.py).
벡터는 store/vectors.py(sqlite-vec auto / brute fallback), 키워드는 FTS5.
"""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
import time
from pathlib import Path

from ..ontology.base import Document, Entity, Relation

SCHEMA_VERSION = 10

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
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
    detail_html TEXT
);
CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents(content_hash);
CREATE INDEX IF NOT EXISTS idx_documents_canon ON documents(canonical_url);

CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
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
    type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    props TEXT,
    sources TEXT,
    confidence REAL DEFAULT 1.0,
    provisional INTEGER DEFAULT 0,
    created_at REAL,
    UNIQUE(type, source_id, target_id)
);
CREATE INDEX IF NOT EXISTS idx_relations_src ON relations(source_id);
CREATE INDEX IF NOT EXISTS idx_relations_tgt ON relations(target_id);

CREATE TABLE IF NOT EXISTS embeddings (
    owner_id TEXT PRIMARY KEY,   -- entity id (또는 청크 id)
    dim INTEGER,
    vector BLOB,
    model TEXT,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT,                   -- 'entity_type' | 'relation_type'
    proposed TEXT,
    context TEXT,
    document_id TEXT,
    created_at REAL
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    document_id TEXT UNIQUE,      -- 갱신 대상 문서(없으면 신규 적재)
    payload TEXT NOT NULL,        -- 재적재용 원본(url/text) — raw_inbox/documents 에서
    reason TEXT,                  -- 'thin' | 'algo-change' | 'manual'
    status TEXT DEFAULT 'pending',-- pending | done | nochange | error
    attempts INTEGER DEFAULT 0,
    last_attempt REAL,
    error TEXT,
    created_at REAL,
    updated_at REAL
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
    expires_at REAL
);

-- [문서 공유 핫링크] 세션 토큰(claire_session, 전체 UI 인증)과 **완전 분리**된, 문서 1개의
-- 읽기 뷰만 비인증으로 열어주는 공유 토큰. 유출돼도 그 문서 1개만 노출(읽기전용). 발급은
-- 인증된 UI 에서만(/share), 열람은 게이트 예외(/p?s=token)로 토큰 자체가 인증을 대신한다.
CREATE TABLE IF NOT EXISTS doc_shares (
    token TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    created_at REAL,
    expires_at REAL          -- NULL=무기한. 향후 만료 정책 시 사용.
);
CREATE INDEX IF NOT EXISTS idx_doc_shares_doc ON doc_shares(document_id);

-- [1홉 자동확장] 적재한 문서에서 따라갈 링크를 LLM 이 선별→판정→적재하는 백그라운드
-- 대기열. refresh_queue 와 같은 패턴(전용 expand-loop 컨테이너가 주기 처리).
CREATE TABLE IF NOT EXISTS expand_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT UNIQUE,       -- 확장 출발(부모) 문서
    status TEXT DEFAULT 'pending', -- pending | done | error
    attempts INTEGER DEFAULT 0,
    last_attempt REAL,
    error TEXT,
    result TEXT,                   -- 처리 요약 JSON(선별/적재/스킵 수)
    created_at REAL,
    updated_at REAL
);
CREATE INDEX IF NOT EXISTS idx_expand_status ON expand_queue(status);

-- [주기 크롤링] watch 대상 문서가 재크롤 시 '내용이 바뀌었을 때' 변경 '전' 원문을 시계열로
-- 보존(데이터 보존 협약 — 벤치/순위처럼 변하는 콘텐츠의 추세를 살림). 그래프(엔티티)는
-- 최신 in-place 갱신만 하고, 과거 상태는 여기 원문으로만 남는다(추세는 나중에 스냅샷 종합).
CREATE TABLE IF NOT EXISTS document_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL,     -- 어느 문서의 과거 상태인지
    captured_at REAL,              -- 이 스냅샷(=변경 직전 상태)을 보존한 시각
    content_hash TEXT,             -- 그 시점 본문 해시
    title TEXT,
    raw_text TEXT,                 -- 변경 전 원문(시계열 보존)
    meta TEXT
);
CREATE INDEX IF NOT EXISTS idx_snapshots_doc ON document_snapshots(document_id, captured_at);

-- FTS5 키워드 인덱스 (엔티티 이름 + 관찰). content 테이블과 분리된 standalone FTS.
CREATE VIRTUAL TABLE IF NOT EXISTS entities_fts USING fts5(
    entity_id UNINDEXED,
    name,
    body
);
"""


def checkpoint_database(src: str | Path, dest: str | Path) -> Path:
    """내부 안전장치용 SQLite checkpoint를 단일 파일로 복제(VACUUM INTO).

    이는 웹 병합 같은 앱 내부 파괴 작업의 근거리 checkpoint일 뿐, data/raw·vault와
    복원 절차를 포함하는 운영 backup이 아니다. 운영 backup은 cb-manuscript가 소유한다.
    `VACUUM INTO`는 WAL을 반영한 트랜잭션 일관 DB snapshot을 만든다.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(src)
    try:
        conn.execute("VACUUM INTO ?", (str(dest),))
    finally:
        conn.close()
    return dest


def reset_graph(conn: sqlite3.Connection) -> None:
    """추출 산출물(엔티티/관계/임베딩/추출/제안/FTS)을 비운다 — documents·raw_inbox·
    artifact 는 보존. 저장된 raw_text 로부터 그래프를 깨끗이 재구축(reextract)할 때 사용.

    문서/원본을 남기므로 재추출의 입력은 그대로다. 파괴적이라 호출 전 백업 권장
    (CLI reextract 가 강제). FTS 는 트리거가 아니라 수동 관리라 함께 비운다.
    """
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

    API 요청 경로에서 사용한다. ``mode=rw``/``mode=ro``로 누락된 DB를 암묵적으로
    만들지 않으며, WAL 설정과 schema migration은 프로세스 시작 시 한 번만 수행한다.
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
    # v5: 문서 한국어 가독 렌더링(detail) — 짧은 summary 와 별개의 '여러 단락' 본문 재구성.
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


def stored_schema_version(conn: sqlite3.Connection) -> int | None:
    """Return an existing schema version without creating or changing anything."""

    meta_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
    ).fetchone()
    if meta_exists is None:
        return None
    row = conn.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()
    if row is None:
        return None
    try:
        return int(row["value"])
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"invalid schema_version: {row['value']!r}") from exc


def init_db(conn: sqlite3.Connection) -> None:
    existing_version = stored_schema_version(conn)
    if existing_version is not None and existing_version > SCHEMA_VERSION:
        raise RuntimeError(
            "database schema is newer than this code: "
            f"actual={existing_version}, expected<={SCHEMA_VERSION}"
        )

    conn.executescript(SCHEMA)
    _migrate(conn)
    cur = conn.execute("SELECT value FROM meta WHERE key='schema_version'")
    row = cur.fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
    else:
        conn.execute(
            "UPDATE meta SET value=? WHERE key='schema_version'",
            (str(SCHEMA_VERSION),),
        )
    conn.commit()


# --- documents ---

def _document_minhash_json(doc: Document) -> str | None:
    """문서의 (제목+본문) MinHash 서명을 JSON 으로. 토큰 없으면 None."""
    from ..ingest.normalize import minhash_signature

    sig = minhash_signature((doc.title or "") + " " + (doc.raw_text or ""))
    return json.dumps(sig) if sig else None


def insert_document(conn: sqlite3.Connection, doc: Document) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO documents
        (id,url,canonical_url,title,author,published_at,fetched_at,raw_text,
         source_type,content_hash,lang,partial,meta,minhash,seen)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
        (
            doc.id, doc.url, doc.canonical_url, doc.title, doc.author,
            doc.published_at, doc.fetched_at, doc.raw_text, doc.source_type,
            doc.content_hash, doc.lang, int(doc.partial), json.dumps(doc.meta),
            _document_minhash_json(doc),
        ),
    )
    conn.commit()


def near_duplicate_document(
    conn: sqlite3.Connection, doc: Document, *,
    threshold: float = 0.90, min_len: int = 500, exclude_id: str | None = None,
) -> tuple[str, float] | None:
    """근사 중복 게이트(3차): MinHash Jaccard 추정이 임계 이상인 기존 문서를 찾는다.

    content_hash(완전일치)·canonical_url 를 비껴간 "같은 글 다른 입구"를 잡는다.
    **보수적**(데이터 보존): 짧은(<min_len) 문서·partial 은 양쪽 다 제외해 false-positive
    를 막고(특히 x.com 트윗), 임계 0.90 은 실측 마진(진짜중복 0.97+ vs 별개 ≤0.36) 안.
    반환: (가장 유사한 문서 id, 추정 유사도) 또는 None.
    """
    from ..ingest.normalize import minhash_estimate, minhash_signature

    if doc.partial or len(doc.raw_text or "") < min_len:
        return None
    sig = minhash_signature((doc.title or "") + " " + (doc.raw_text or ""))
    if not sig:
        return None
    rows = conn.execute(
        "SELECT id, minhash FROM documents "
        "WHERE minhash IS NOT NULL AND partial=0 AND length(raw_text) >= ?",
        (min_len,),
    ).fetchall()
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


def find_document_by_hash(conn: sqlite3.Connection, content_hash: str) -> str | None:
    if not content_hash:
        return None
    row = conn.execute(
        "SELECT id FROM documents WHERE content_hash=? LIMIT 1", (content_hash,)
    ).fetchone()
    return row["id"] if row else None


def get_document_row(conn: sqlite3.Connection, document_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM documents WHERE id=?", (document_id,)
    ).fetchone()


# --- [주기 크롤링] 스냅샷(시계열) · 열람(seen) · watch 설정 ---

def save_document_snapshot(
    conn: sqlite3.Connection, document_id: str, *, captured_at: float,
    content_hash: str | None, title: str | None, raw_text: str | None,
    meta: dict | None = None,
) -> None:
    """watch 문서의 '변경 전' 상태를 시계열로 보존(데이터 보존 — 추세 살림)."""
    conn.execute(
        "INSERT INTO document_snapshots(document_id,captured_at,content_hash,title,raw_text,meta) "
        "VALUES(?,?,?,?,?,?)",
        (document_id, captured_at, content_hash, title, raw_text,
         json.dumps(meta) if meta is not None else None),
    )
    conn.commit()


def document_snapshots(
    conn: sqlite3.Connection, document_id: str, limit: int = 0
) -> list[sqlite3.Row]:
    """한 문서의 과거 스냅샷(최신순)."""
    q = ("SELECT id,document_id,captured_at,content_hash,title,raw_text "
         "FROM document_snapshots WHERE document_id=? ORDER BY captured_at DESC")
    if limit:
        q += f" LIMIT {int(limit)}"
    return conn.execute(q, (document_id,)).fetchall()


def set_document_seen(conn: sqlite3.Connection, document_id: str, seen: bool = True) -> None:
    """문서 열람 상태 설정(UI 에서 열어보면 seen=1, watch 갱신 시 0=다시 봐야 함)."""
    conn.execute("UPDATE documents SET seen=? WHERE id=?",
                 (1 if seen else 0, document_id))
    conn.commit()


def set_document_watch(
    conn: sqlite3.Connection, document_id: str, *, enabled: bool | None,
    interval: float | None = None, reason: str | None = None,
) -> None:
    """watch(주기 재크롤) 설정 — LLM 자동판단 또는 사용자 수동 on/off. enabled None=미판단."""
    conn.execute(
        "UPDATE documents SET watch_enabled=?, watch_interval=COALESCE(?,watch_interval), "
        "watch_reason=COALESCE(?,watch_reason) WHERE id=?",
        (None if enabled is None else (1 if enabled else 0), interval, reason, document_id),
    )
    conn.commit()


def watch_due_documents(
    conn: sqlite3.Connection, now: float, *, default_interval: float, limit: int = 0
) -> list[sqlite3.Row]:
    """재크롤할 때가 된 watch 문서: enabled=1 AND (last_watched_at NULL 또는
    now - last_watched_at >= interval). interval 없으면 default_interval 적용."""
    q = ("SELECT id, url FROM documents WHERE watch_enabled=1 AND url IS NOT NULL AND ("
         "last_watched_at IS NULL OR ? - last_watched_at >= COALESCE(watch_interval, ?)) "
         "ORDER BY COALESCE(last_watched_at, 0)")
    if limit:
        q += f" LIMIT {int(limit)}"
    return conn.execute(q, (now, default_interval)).fetchall()


def mark_document_watched(conn: sqlite3.Connection, document_id: str, when: float) -> None:
    """watch 재크롤을 수행한 시각 기록(다음 due 계산 기준)."""
    conn.execute("UPDATE documents SET last_watched_at=? WHERE id=?", (when, document_id))
    conn.commit()


def find_document_by_canonical_url(
    conn: sqlite3.Connection, canonical_url: str | None
) -> str | None:
    """같은 canonical_url 의 기존 문서 id(최신 fetched_at). 내용 갱신 감지용."""
    if not canonical_url:
        return None
    row = conn.execute(
        "SELECT id FROM documents WHERE canonical_url=? ORDER BY fetched_at DESC LIMIT 1",
        (canonical_url,),
    ).fetchone()
    return row["id"] if row else None


def _documents_filter(
    since: float | None,
    query: str | None,
    include_hidden: bool = True,
) -> tuple[str, list]:
    """documents_timeline/documents_count 공용 WHERE 절 빌더."""
    where, params = [], []
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
) -> list[sqlite3.Row]:
    """문서를 최신 적재순으로(좌측 문서 패널용). summary 는 호출측에서 붙인다.

    since/query 는 MCP `documents` 툴이 "전체를 다 훑지 않고 좁혀서 찾을" 수
    있게 추가된 선택적 필터(기본 None, 기존 웹 UI 호출은 동작 그대로).
    include_hidden=False 면 hidden=0 인 공개 문서만 조회한다."""
    where_sql, params = _documents_filter(since, query, include_hidden=include_hidden)
    params.append(limit)
    return conn.execute(
        f"SELECT id, title, url, source_type, fetched_at, seen, watch_enabled, "
        f"pinned, hidden FROM documents {where_sql} "
        f"ORDER BY fetched_at DESC, id DESC LIMIT ?",
        params,
    ).fetchall()


def documents_count(
    conn: sqlite3.Connection,
    *,
    since: float | None = None,
    query: str | None = None,
    include_hidden: bool = True,
) -> int:
    """documents_timeline과 동일한 필터의 총 개수(잘림 여부 판단용)."""
    where_sql, params = _documents_filter(since, query, include_hidden=include_hidden)
    return conn.execute(
        f"SELECT COUNT(*) c FROM documents {where_sql}", params
    ).fetchone()["c"]


def hidden_document_ids(conn: sqlite3.Connection) -> set[str]:
    """숨김 처리된(hidden=1) 모든 문서의 ID 집합을 반환한다."""
    rows = conn.execute("SELECT id FROM documents WHERE hidden=1").fetchall()
    return {row["id"] for row in rows}


def set_document_pinned(conn: sqlite3.Connection, document_id: str, pinned: bool) -> bool:
    """즐겨찾기 토글. 존재하지 않는 id 면 False."""
    cur = conn.execute(
        "UPDATE documents SET pinned=? WHERE id=?", (1 if pinned else 0, document_id))
    conn.commit()
    return cur.rowcount > 0


def set_document_hidden(conn: sqlite3.Connection, document_id: str, hidden: bool) -> bool:
    """숨기기 토글(목록 전용 — 그래프 엔티티/관계는 안 건드림). 존재하지 않는 id 면 False."""
    cur = conn.execute(
        "UPDATE documents SET hidden=? WHERE id=?", (1 if hidden else 0, document_id))
    conn.commit()
    return cur.rowcount > 0


def set_document_title(conn: sqlite3.Connection, document_id: str, title: str | None) -> bool:
    """문서 제목 갱신 및 MinHash 서명 재계산. 존재하지 않는 id 면 False."""
    from ..ingest.normalize import minhash_signature

    row = conn.execute("SELECT raw_text FROM documents WHERE id=?", (document_id,)).fetchone()
    if row is None:
        return False

    raw_text = row["raw_text"] or ""
    clean_title = (title or "").strip() or None
    sig = minhash_signature((clean_title or "") + " " + raw_text)
    sig_json = json.dumps(sig) if sig else None

    cur = conn.execute(
        "UPDATE documents SET title=?, minhash=? WHERE id=?",
        (clean_title, sig_json, document_id),
    )
    conn.commit()
    return cur.rowcount > 0


def get_document(conn: sqlite3.Connection, document_id: str) -> Document | None:
    """documents 행을 Document 모델로 복원(자동복구의 extract 재시도 등에서 사용)."""
    row = get_document_row(conn, document_id)
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

def upsert_entity(conn: sqlite3.Connection, ent: Entity) -> None:
    ent.updated_at = time.time()
    conn.execute(
        """INSERT OR REPLACE INTO entities
        (id,type,name,norm_name,aliases,props,observations,sources,provisional,
         created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            ent.id, ent.type, ent.name, ent.norm_name,
            json.dumps(ent.aliases), json.dumps(ent.props),
            json.dumps(ent.observations), json.dumps(ent.sources),
            int(ent.provisional), ent.created_at, ent.updated_at,
        ),
    )
    # FTS 갱신
    body = " \n".join(ent.observations) + " " + " ".join(ent.aliases)
    conn.execute("DELETE FROM entities_fts WHERE entity_id=?", (ent.id,))
    conn.execute(
        "INSERT INTO entities_fts(entity_id,name,body) VALUES (?,?,?)",
        (ent.id, ent.name, body),
    )
    conn.commit()


def get_entity(conn: sqlite3.Connection, entity_id: str) -> Entity | None:
    row = conn.execute("SELECT * FROM entities WHERE id=?", (entity_id,)).fetchone()
    return _row_to_entity(row) if row else None


def find_entities_by_norm(conn: sqlite3.Connection, norm_name: str) -> list[Entity]:
    rows = conn.execute(
        "SELECT * FROM entities WHERE norm_name=?", (norm_name,)
    ).fetchall()
    return [_row_to_entity(r) for r in rows]


def find_entities_by_name_or_alias(
    conn: sqlite3.Connection, norm_name: str
) -> list[Entity]:
    """norm_name 정확 매칭(인덱스) + alias 정확 매칭(스캔).

    alias 는 JSON 컬럼이라 정규화 비교가 필요해 alias 후보만 LIKE 로 좁힌 뒤
    Python 에서 정확 비교한다. 규모가 커지면 alias 테이블로 인덱싱할 것.
    """
    from ..ontology.base import normalize_name

    out: dict[str, Entity] = {}
    for r in conn.execute("SELECT * FROM entities WHERE norm_name=?", (norm_name,)):
        e = _row_to_entity(r)
        out[e.id] = e
    # alias 매칭: aliases JSON 문자열에 후보 토큰이 들어간 행만 1차 필터
    like = f'%{norm_name}%'
    for r in conn.execute(
        "SELECT * FROM entities WHERE aliases LIKE ? COLLATE NOCASE", (like,)
    ):
        e = _row_to_entity(r)
        if e.id in out:
            continue
        if norm_name in {normalize_name(a) for a in e.aliases}:
            out[e.id] = e
    return list(out.values())


def all_entities(conn: sqlite3.Connection) -> list[Entity]:
    rows = conn.execute("SELECT * FROM entities").fetchall()
    return [_row_to_entity(r) for r in rows]


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


# --- relations ---

def upsert_relation(conn: sqlite3.Connection, rel: Relation) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO relations
        (id,type,source_id,target_id,props,sources,confidence,provisional,created_at)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            rel.id, rel.type, rel.source_id, rel.target_id,
            json.dumps(rel.props), json.dumps(rel.sources),
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


def neighbors(conn: sqlite3.Connection, entity_id: str) -> list[Relation]:
    rows = conn.execute(
        "SELECT * FROM relations WHERE source_id=? OR target_id=?",
        (entity_id, entity_id),
    ).fetchall()
    return [_row_to_relation(r) for r in rows]


def all_relations(conn: sqlite3.Connection) -> list[Relation]:
    return [_row_to_relation(r) for r in conn.execute("SELECT * FROM relations").fetchall()]


# --- proposals ---

def log_proposal(
    conn: sqlite3.Connection, kind: str, proposed: str, context: str,
    document_id: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO proposals(kind,proposed,context,document_id,created_at) "
        "VALUES (?,?,?,?,?)",
        (kind, proposed, context, document_id, time.time()),
    )
    conn.commit()


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
) -> int:
    """[Layer 1] inbound 원본 기록. 처리 전에 무조건 먼저 호출. row id 반환."""
    cur = conn.execute(
        "INSERT INTO raw_inbox(received_at,source,user_id,chat_id,kind,payload,"
        "file_name,file_ref,status) VALUES (?,?,?,?,?,?,?,?, 'received')",
        (time.time(), source, user_id, chat_id, kind, payload, file_name, file_ref),
    )
    conn.commit()
    return int(cur.lastrowid)


def update_inbox(
    conn: sqlite3.Connection, inbox_id: int, *,
    status: str, document_id: str | None = None, error: str | None = None,
) -> None:
    conn.execute(
        "UPDATE raw_inbox SET status=?, document_id=?, error=? WHERE id=?",
        (status, document_id, error, inbox_id),
    )
    conn.commit()


def all_inbox(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM raw_inbox ORDER BY id").fetchall()


def inbox_by_status(conn: sqlite3.Connection, status: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM raw_inbox WHERE status=? ORDER BY id", (status,)
    ).fetchall()


def get_inbox(conn: sqlite3.Connection, inbox_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM raw_inbox WHERE id=?", (inbox_id,)
    ).fetchone()


def inbox_failures(conn: sqlite3.Connection, limit: int = 10) -> list[sqlite3.Row]:
    """error/failed(영구실패) 항목을 최신순으로(텔레그램 /failed 용)."""
    return conn.execute(
        "SELECT * FROM raw_inbox WHERE status IN ('error','failed') "
        "ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()


def due_for_recovery(
    conn: sqlite3.Connection, *, max_attempts: int, now: float | None = None,
    limit: int = 0,
) -> list[sqlite3.Row]:
    """자동 재적재 대상: status='error' AND attempts<max AND 재시도시각 도래.

    next_retry_at 이 NULL(아직 한 번도 시도 안 함)이거나 now 이하인 행만. attempts 가
    상한에 도달한 행은 recover 가 'failed' 로 굳히므로 여기 다시 안 잡힌다.
    """
    now = time.time() if now is None else now
    return conn.execute(
        "SELECT * FROM raw_inbox WHERE status='error' AND attempts < ? "
        "AND (next_retry_at IS NULL OR next_retry_at <= ?) ORDER BY id"
        + (" LIMIT ?" if limit else ""),
        ((max_attempts, now, limit) if limit else (max_attempts, now)),
    ).fetchall()


def record_recovery_attempt(
    conn: sqlite3.Connection, inbox_id: int, *, status: str,
    document_id: str | None = None, error: str | None = None,
    next_retry_at: float | None = None, now: float | None = None,
) -> None:
    """재적재 1회 시도 결과 기록(attempts+1, last_attempt, next_retry_at 갱신)."""
    now = time.time() if now is None else now
    conn.execute(
        "UPDATE raw_inbox SET status=?, document_id=?, error=?, "
        "attempts=attempts+1, last_attempt=?, next_retry_at=? WHERE id=?",
        (status, document_id, error, now, next_retry_at, inbox_id),
    )
    conn.commit()


# --- status / 집계 (claire status 용) ---

def inbox_status_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """raw_inbox 의 status 별 개수."""
    rows = conn.execute(
        "SELECT status, COUNT(*) n FROM raw_inbox GROUP BY status"
    ).fetchall()
    return {r["status"]: r["n"] for r in rows}


def entity_type_counts(conn: sqlite3.Connection, limit: int = 12) -> list[tuple[str, int]]:
    """엔티티 타입별 개수(많은 순)."""
    rows = conn.execute(
        "SELECT type, COUNT(*) n FROM entities GROUP BY type ORDER BY n DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [(r["type"], r["n"]) for r in rows]


def source_type_counts(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    """문서 소스 타입별 개수(web/youtube/pdf/text...)."""
    rows = conn.execute(
        "SELECT source_type, COUNT(*) n FROM documents GROUP BY source_type ORDER BY n DESC"
    ).fetchall()
    return [(r["source_type"], r["n"]) for r in rows]


def top_connected_entities(
    conn: sqlite3.Connection, limit: int = 8
) -> list[tuple[str, str, int]]:
    """연결(degree) 많은 엔티티 상위 — (name, type, degree)."""
    rows = conn.execute(
        """
        SELECT e.name, e.type, COUNT(r.id) deg
        FROM entities e
        LEFT JOIN relations r ON r.source_id = e.id OR r.target_id = e.id
        GROUP BY e.id ORDER BY deg DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [(r["name"], r["type"], r["deg"]) for r in rows]


def most_merged_entities(
    conn: sqlite3.Connection, limit: int = 8
) -> list[tuple[str, str, int]]:
    """여러 자료에서 수렴된(sources 많은) 엔티티 — (name, type, source_count).

    sources 는 JSON 배열 문자열이라 Python 에서 길이를 센다.
    """
    rows = conn.execute("SELECT name, type, sources FROM entities").fetchall()
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


def last_inbox_activity(conn: sqlite3.Connection) -> float | None:
    """가장 최근 inbound 수신 시각(epoch). 없으면 None."""
    row = conn.execute("SELECT MAX(received_at) m FROM raw_inbox").fetchone()
    return row["m"] if row and row["m"] else None


def log_extraction(
    conn: sqlite3.Connection, *,
    document_id: str, provider: str, model: str,
    prompt_version: str, raw_response: str,
) -> None:
    """[LLM tier] 모델 원본 출력 보관."""
    conn.execute(
        "INSERT INTO extractions(document_id,provider,model,prompt_version,"
        "raw_response,created_at) VALUES (?,?,?,?,?,?)",
        (document_id, provider, model, prompt_version, raw_response, time.time()),
    )
    conn.commit()


def create_auth_nonce(conn: sqlite3.Connection, *, ttl: float = 600.0) -> str:
    """웹 접속 승인 요청 nonce 생성(승인 대기 ttl초). 추측 불가 토큰."""
    nonce = secrets.token_urlsafe(16)
    now = time.time()
    conn.execute(
        "INSERT INTO auth_sessions(nonce,approved,created_at,expires_at) VALUES (?,0,?,?)",
        (nonce, now, now + ttl))
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
    conn.execute(
        "UPDATE auth_sessions SET approved=1, session_token=?, expires_at=? WHERE nonce=?",
        (token, now + session_ttl, nonce))
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
    """[/web, /webro] **scope 별 단일 활성 세션**: 같은 scope 의 기존 세션만 revoke 하고
    추측 저항성이 충분한 새 토큰 1개를 발급 — owner(/web)와 readonly(/webro)는 서로 다른 scope 라 독립적으로
    공존한다(읽기전용 링크를 공유해도 내 소유자 세션은 안 끊김, 그 반대도 마찬가지).

    토큰은 링크의 전체값만 인정한다. 발급 즉시 같은 scope 의 이전 링크/쿠키는
    무효(다음 /web 한 번이 곧 '이전 owner 세션 전부 로그아웃', scope 가 다르면 서로
    안 건드림). nonce=토큰(PK)."""
    conn.execute("DELETE FROM auth_sessions WHERE scope=?", (scope,))
    token = secrets.token_urlsafe(32)
    now = time.time()
    conn.execute(
        "INSERT INTO auth_sessions(nonce,session_token,approved,created_at,expires_at,scope) "
        "VALUES (?,?,1,?,?,?)", (token, token, now, now + ttl, scope))
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

    조회 뒤 UPDATE에 이전 token과 만료 조건을 다시 넣는다. 동시 요청이 같은 URL을
    사용해도 한 요청만 rowcount=1을 얻고 나머지는 실패한다.
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
    result = conn.execute(
        f"UPDATE auth_sessions SET session_token=?, expires_at=? "
        f"WHERE session_token=? AND approved=1 AND expires_at>=? "
        f"AND scope IN ({placeholders})",
        (rotated, now + ttl, token, now, *scopes),
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
    scopes: tuple[str, ...] = ("owner", "readonly"),
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

    기본은 scope='owner' 만 인정(기존 전체-쓰기 게이트 동작 그대로 — 하위호환). 읽기전용
    게이트는 scopes=("owner","readonly") 로 호출해 두 scope 모두 인정한다(owner 세션으로도
    당연히 읽을 수 있어야 하므로)."""
    return validate_session_scope(
        conn, token, ttl=ttl, scopes=scopes
    ) is not None


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
                     *, ttl: float | None = None) -> str:
    """문서 1개의 읽기 공유 토큰을 발급(세션과 분리). ttl=None 이면 무기한.

    같은 문서에 여러 번 발급해도 매번 새 토큰(서로 독립적으로 철회 가능하도록 단순 추가)."""
    token = _short_token(_SHARE_TOKEN_LEN)
    now = time.time()
    expires = (now + ttl) if ttl else None
    conn.execute(
        "INSERT INTO doc_shares(token, document_id, created_at, expires_at) "
        "VALUES (?,?,?,?)", (token, document_id, now, expires))
    conn.commit()
    return token


def resolve_doc_share(conn: sqlite3.Connection, token: str) -> str | None:
    """공유 토큰 → document_id(미만료). 없거나 만료면 None. 전체 일치만(프리픽스 불가)."""
    t = token or ""
    if not plausible_share_token(t):
        return None
    row = conn.execute(
        "SELECT document_id, expires_at FROM doc_shares WHERE token=?", (t,)).fetchone()
    if not row:
        return None
    if row["expires_at"] is not None and row["expires_at"] < time.time():
        return None
    return row["document_id"]


def latest_extraction_summary(conn: sqlite3.Connection, document_id: str) -> str | None:
    """문서의 최신 추출 결과에서 summary 를 꺼낸다(documents 엔 summary 컬럼이 없고
    extractions.raw_response = ExtractionResult JSON 에 들어있다). 노드 상세 패널용."""
    row = conn.execute(
        "SELECT raw_response FROM extractions WHERE document_id=? ORDER BY id DESC LIMIT 1",
        (document_id,),
    ).fetchone()
    if not row or not row["raw_response"]:
        return None
    try:
        return json.loads(row["raw_response"]).get("summary") or None
    except (ValueError, AttributeError):
        return None


def set_document_detail(
    conn: sqlite3.Connection,
    document_id: str,
    detail: str,
    format: str = "md",
    html: str | None = None,
) -> None:
    """문서의 한국어 가독 렌더링(detail), 포맷(detail_format), 사전 컴파일 HTML(detail_html)을 저장."""
    fmt = (format or "md").strip().lower()
    if fmt in ("asciidoc", "adoc"):
        fmt = "adoc"
    else:
        fmt = "md"

    if html is None and detail and detail.strip():
        from ..render import render_to_html

        html_content = render_to_html(detail, format=fmt)
    else:
        html_content = html or ""

    conn.execute(
        "UPDATE documents SET detail=?, detail_format=?, detail_html=? WHERE id=?",
        (detail, fmt, html_content, document_id),
    )
    conn.commit()


def get_document_detail(conn: sqlite3.Connection, document_id: str) -> str | None:
    """문서의 detail(한국어 가독 렌더링 원본 텍스트). 없으면 None."""
    row = conn.execute(
        "SELECT detail FROM documents WHERE id=?", (document_id,)).fetchone()
    return (row["detail"] if row else None) or None


def get_document_detail_format(conn: sqlite3.Connection, document_id: str) -> str:
    """문서의 detail_format('md' 또는 'adoc'). 없으면 'md'."""
    row = conn.execute(
        "SELECT detail_format FROM documents WHERE id=?", (document_id,)).fetchone()
    return (row["detail_format"] if row and row["detail_format"] else "md")


def get_document_detail_html(conn: sqlite3.Connection, document_id: str) -> str | None:
    """문서의 detail_html(AOT 사전 컴파일된 HTML). 없으면 detail 로부터 실시간 생성 및 캐싱."""
    row = conn.execute(
        "SELECT detail, detail_format, detail_html FROM documents WHERE id=?",
        (document_id,),
    ).fetchone()
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
                conn.execute(
                    "UPDATE documents SET detail_html=? WHERE id=?",
                    (rendered, document_id),
                )
                conn.commit()
            except Exception:  # noqa: BLE001
                pass
            return rendered
    return None


def recompile_all_detail_html(conn: sqlite3.Connection) -> int:
    """모든 문서의 detail_html을 현재 AOT 렌더러로 재컴파일하여 DB에 갱신."""
    from ..render import render_to_html

    rows = conn.execute(
        "SELECT id, detail, detail_format FROM documents WHERE detail IS NOT NULL AND trim(detail) != ''"
    ).fetchall()
    count = 0
    for r in rows:
        fmt = r["detail_format"] or "md"
        html_out = render_to_html(r["detail"], format=fmt)
        conn.execute(
            "UPDATE documents SET detail_html=? WHERE id=?",
            (html_out, r["id"]),
        )
        count += 1
    conn.commit()
    return count


def documents_missing_detail(conn: sqlite3.Connection, limit: int = 0) -> list[str]:
    """detail 이 비어있는 문서 id(최신순). 백필 대상."""
    q = ("SELECT id FROM documents WHERE detail IS NULL OR detail='' "
         "ORDER BY fetched_at DESC")
    if limit:
        q += f" LIMIT {int(limit)}"
    return [r["id"] for r in conn.execute(q).fetchall()]


def documents_needing_detail_format(
    conn: sqlite3.Connection,
    target_format: str,
    limit: int = 0,
) -> list[str]:
    """목표 포맷(target_format)으로 detail 생성이 필요한 문서 id(최신순).

    1) detail 이 비어있거나 NULL인 문서
    2) detail 은 있으나 detail_format 이 target_format 과 다른 문서
    (이미 target_format 으로 일치하는 문서는 제외)
    """
    fmt = (target_format or "md").strip().lower()
    fmt = "adoc" if fmt in ("asciidoc", "adoc") else "md"
    q = (
        "SELECT id FROM documents WHERE detail IS NULL OR trim(detail)='' "
        "OR lower(coalesce(detail_format, 'md')) != ? "
        "ORDER BY fetched_at DESC"
    )
    if limit:
        q += f" LIMIT {int(limit)}"
    return [r["id"] for r in conn.execute(q, (fmt,)).fetchall()]


def get_format_status(
    conn: sqlite3.Connection,
    configured_format: str,
) -> dict[str, Any]:
    """DB 내 문서들의 포맷 상태(총 문서, 포맷 일치, 포맷 불일치, detail 누락)를 상세 진단."""
    target = (configured_format or "md").strip().lower()
    target = "adoc" if target in ("asciidoc", "adoc") else "md"

    row_total_docs = conn.execute("SELECT COUNT(*) as c FROM documents").fetchone()
    total_docs = row_total_docs["c"] if row_total_docs else 0

    row_matching = conn.execute(
        "SELECT COUNT(*) as c FROM documents WHERE detail IS NOT NULL AND trim(detail) != '' AND lower(coalesce(detail_format, 'md')) = ?",
        (target,),
    ).fetchone()
    matching_docs = row_matching["c"] if row_matching else 0

    row_mismatched = conn.execute(
        "SELECT COUNT(*) as c FROM documents WHERE detail IS NOT NULL AND trim(detail) != '' AND lower(coalesce(detail_format, 'md')) != ?",
        (target,),
    ).fetchone()
    mismatched_docs = row_mismatched["c"] if row_mismatched else 0

    row_missing = conn.execute(
        "SELECT COUNT(*) as c FROM documents WHERE detail IS NULL OR trim(detail) = ''"
    ).fetchone()
    missing_detail_docs = row_missing["c"] if row_missing else 0

    total_with_detail = matching_docs + mismatched_docs
    target_docs = mismatched_docs + missing_detail_docs

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
    }


def check_format_mismatch(
    conn: sqlite3.Connection,
    configured_format: str,
) -> dict[str, Any]:
    """설정된 포맷(Settings.render_format)과 DB에 저장된 detail 포맷 불일치 여부를 진단."""
    return get_format_status(conn, configured_format)


# --- refresh queue (복원 메커니즘) ---

def enqueue_refresh(
    conn: sqlite3.Connection, *, document_id: str | None, payload: str, reason: str
) -> bool:
    """갱신 대기열에 등록. document_id 중복이면 pending 으로 되살린다. 신규면 True."""
    now = time.time()
    if document_id:
        row = conn.execute(
            "SELECT id FROM refresh_queue WHERE document_id=?", (document_id,)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE refresh_queue SET status='pending', payload=?, reason=?, "
                "updated_at=? WHERE document_id=?",
                (payload, reason, now, document_id),
            )
            conn.commit()
            return False
    conn.execute(
        "INSERT INTO refresh_queue(document_id,payload,reason,status,created_at,updated_at)"
        " VALUES (?,?,?, 'pending', ?, ?)",
        (document_id, payload, reason, now, now),
    )
    conn.commit()
    return True


def pending_refresh(conn: sqlite3.Connection, limit: int = 0) -> list[sqlite3.Row]:
    q = "SELECT * FROM refresh_queue WHERE status='pending' ORDER BY id"
    if limit:
        q += f" LIMIT {int(limit)}"
    return conn.execute(q).fetchall()


def update_refresh(
    conn: sqlite3.Connection, rid: int, *, status: str, error: str | None = None
) -> None:
    conn.execute(
        "UPDATE refresh_queue SET status=?, error=?, attempts=attempts+1, "
        "last_attempt=?, updated_at=? WHERE id=?",
        (status, error, time.time(), time.time(), rid),
    )
    conn.commit()


def refresh_status_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, COUNT(*) n FROM refresh_queue GROUP BY status"
    ).fetchall()
    return {r["status"]: r["n"] for r in rows}


# --- 1홉 자동확장 큐 (expand_queue) — refresh_queue 와 동일 패턴 ---

def enqueue_expand(conn: sqlite3.Connection, document_id: str) -> bool:
    """문서를 1홉 확장 대기열에 등록. 이미 있으면 pending 으로 되살린다(중복 무해). 신규면 True."""
    now = time.time()
    row = conn.execute(
        "SELECT id FROM expand_queue WHERE document_id=?", (document_id,)
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE expand_queue SET status='pending', updated_at=? WHERE document_id=?",
            (now, document_id),
        )
        conn.commit()
        return False
    conn.execute(
        "INSERT INTO expand_queue(document_id,status,created_at,updated_at)"
        " VALUES (?, 'pending', ?, ?)",
        (document_id, now, now),
    )
    conn.commit()
    return True


def pending_expand(conn: sqlite3.Connection, limit: int = 0) -> list[sqlite3.Row]:
    q = "SELECT * FROM expand_queue WHERE status='pending' ORDER BY id"
    if limit:
        q += f" LIMIT {int(limit)}"
    return conn.execute(q).fetchall()


def update_expand(
    conn: sqlite3.Connection, eid: int, *, status: str,
    error: str | None = None, result: str | None = None,
) -> None:
    conn.execute(
        "UPDATE expand_queue SET status=?, error=?, result=?, attempts=attempts+1, "
        "last_attempt=?, updated_at=? WHERE id=?",
        (status, error, result, time.time(), time.time(), eid),
    )
    conn.commit()


def expand_status_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, COUNT(*) n FROM expand_queue GROUP BY status"
    ).fetchall()
    return {r["status"]: r["n"] for r in rows}


def thin_documents(
    conn: sqlite3.Connection, *, max_len: int, host: str | None = None,
    include_partial: bool = False,
) -> list[sqlite3.Row]:
    """본문이 빈약한 문서. host 지정 시 해당 호스트만.

    기본은 non-partial 만. include_partial=True 면 partial 노드(예: 구버전 'x.com
    post' — 본문 스크랩 없이 URL 만 보관)도 포함해 재fetch 대상으로 잡는다.
    """
    q = ("SELECT id, url, title, length(raw_text) L FROM documents "
         "WHERE length(raw_text) < ?")
    if not include_partial:
        q += " AND partial=0"
    args: list = [max_len]
    if host:
        q += " AND url LIKE ?"
        args.append(f"%{host}%")
    return conn.execute(q, args).fetchall()


def update_document_content(
    conn: sqlite3.Connection, doc_id: str, *,
    title: str | None, raw_text: str, content_hash: str, fetched_at: float,
    source_type: str | None = None, partial: bool | None = None,
    meta: dict | None = None,
) -> None:
    """문서 본문을 in-place 갱신(복원). id 는 유지하여 엔티티 sources 연결 보존.

    source_type/partial 을 주면 함께 갱신한다(구버전 partial 'x.com post' 가 본문
    스크랩에 성공해 정식 문서가 될 때 플래그 정합을 맞추기 위함). meta 를 주면 함께
    갱신(재fetch 로 새로 수집된 본문 이미지 등을 보존)."""
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
    vals.append(doc_id)
    conn.execute(f"UPDATE documents SET {', '.join(cols)} WHERE id=?", vals)
    conn.commit()


def set_document_images(conn: sqlite3.Connection, doc_id: str, images: list[dict]) -> None:
    """문서 meta 에 본문 이미지 목록만 갱신(다른 meta 키 보존). 재fetch 백필용."""
    row = conn.execute("SELECT meta FROM documents WHERE id=?", (doc_id,)).fetchone()
    if row is None:
        return
    meta = json.loads(row["meta"] or "{}")
    meta["images"] = images or []
    conn.execute("UPDATE documents SET meta=? WHERE id=?", (json.dumps(meta), doc_id))
    conn.commit()


# --- 1홉 병합 출처(extra_sources) — ONEHOP_MERGE_DESIGN.md. 신규 컬럼 없이 documents.meta
# JSON 재사용(set_document_images 와 동일 패턴) — 같은 주제의 부가 출처(예: GeekNews 글이
# 발견한 그 프로젝트의 github)를 새 Document 로 안 만들고 부모 문서에 흡수할 때, 원문 링크
# 계보를 보존하기 위한 목록.

def set_document_extra_sources(conn: sqlite3.Connection, doc_id: str, sources: list[dict]) -> None:
    """문서 meta 에 병합된 부가 출처 목록만 갱신(다른 meta 키 보존)."""
    row = conn.execute("SELECT meta FROM documents WHERE id=?", (doc_id,)).fetchone()
    if row is None:
        return
    meta = json.loads(row["meta"] or "{}")
    meta["extra_sources"] = sources or []
    conn.execute("UPDATE documents SET meta=? WHERE id=?", (json.dumps(meta), doc_id))
    conn.commit()


def get_document_extra_sources(conn: sqlite3.Connection, doc_id: str) -> list[dict]:
    row = conn.execute("SELECT meta FROM documents WHERE id=?", (doc_id,)).fetchone()
    if row is None:
        return []
    return json.loads(row["meta"] or "{}").get("extra_sources") or []


def find_document_by_extra_source(conn: sqlite3.Connection, canonical_url: str | None) -> str | None:
    """이미 어떤 문서에 병합 출처로 흡수된 canonical_url 인지 — 1홉 후보 재제안 방지용
    (병합 경로는 새 Document 행을 안 만들어 documents.canonical_url 색인으로는 못 잡음).

    documents.meta 는 색인 없는 JSON 이라 파이썬 측 스캔 — near_duplicate_document 와
    동일한 절충(개인용 규모라 전체 스캔으로 충분히 빠름). 대략적인 LIKE 로 후보를 먼저
    좁혀 스캔 대상을 줄인다."""
    if not canonical_url:
        return None
    rows = conn.execute(
        "SELECT id, meta FROM documents WHERE meta LIKE '%extra_sources%'"
    ).fetchall()
    for r in rows:
        for s in (json.loads(r["meta"] or "{}").get("extra_sources") or []):
            if s.get("canonical_url") == canonical_url:
                return r["id"]
    return None


def documents_missing_images(conn: sqlite3.Connection, limit: int = 0) -> list[str]:
    """본문 이미지가 아직 없는(재fetch 안 한) 문서 id — 최신순. 이미지 백필 대상.

    meta 에 'images' 키 자체가 없는(이미지 수집 전 적재) url 보유 문서만. 빈 목록([])은
    '재fetch 했으나 콘텐츠 이미지가 없었음'이라 재대상에서 제외(불필요한 재호출 방지)."""
    rows = conn.execute(
        "SELECT id, url, meta FROM documents ORDER BY fetched_at DESC, rowid DESC"
    ).fetchall()
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


def documents_missing_minhash(conn: sqlite3.Connection, limit: int = 0) -> list[str]:
    """minhash 가 비어있는 문서 id(백필 대상). partial/짧은 글도 채워 비교 모집단 일관."""
    q = "SELECT id FROM documents WHERE minhash IS NULL"
    if limit:
        q += f" LIMIT {int(limit)}"
    return [r["id"] for r in conn.execute(q).fetchall()]


def set_document_minhash(conn: sqlite3.Connection, doc_id: str, sig_json: str | None) -> None:
    conn.execute("UPDATE documents SET minhash=? WHERE id=?", (sig_json, doc_id))
    conn.commit()


def _repoint_sources_json(conn: sqlite3.Connection, table: str,
                          keeper_id: str, losers: set[str]) -> int:
    """entities/relations 의 sources(JSON 배열)에서 loser id 를 keeper 로 치환·dedupe.

    근사중복 문서 병합 시 호출. 순서 보존하며 중복 제거. 변경된 행 수 반환.
    """
    changed = 0
    for r in conn.execute(f"SELECT id, sources FROM {table}").fetchall():
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
        conn.execute(f"UPDATE {table} SET sources=? WHERE id=?",
                     (json.dumps(new), r["id"]))
        changed += 1
    return changed


def merge_documents(conn: sqlite3.Connection, keeper_id: str,
                    loser_ids: list[str]) -> dict:
    """[파괴적] 근사중복 문서를 keeper 로 합치고 loser 문서 행을 삭제한다.

    데이터 보존: 삭제 전에 loser 를 가리키는 **모든 참조**(엔티티/관계 sources,
    proposals·extractions·raw_inbox 의 document_id)를 keeper 로 재배치한다. 큐
    (refresh/expand)는 document_id UNIQUE 제약이 있어 loser 행은 삭제(전이적 작업이라
    재생성 가능). 단일 트랜잭션으로 원자 처리. 반환: 재배치/삭제 카운트.
    """
    losers = {x for x in loser_ids if x and x != keeper_id}
    if not losers:
        return {"keeper": keeper_id, "losers": [], "deleted": 0}
    ph = ",".join("?" * len(losers))
    lo = list(losers)
    out = {"keeper": keeper_id, "losers": lo}
    try:
        out["entities_repointed"] = _repoint_sources_json(conn, "entities", keeper_id, losers)
        out["relations_repointed"] = _repoint_sources_json(conn, "relations", keeper_id, losers)
        out["proposals"] = conn.execute(
            f"UPDATE proposals SET document_id=? WHERE document_id IN ({ph})",
            (keeper_id, *lo)).rowcount
        out["extractions"] = conn.execute(
            f"UPDATE extractions SET document_id=? WHERE document_id IN ({ph})",
            (keeper_id, *lo)).rowcount
        out["inbox"] = conn.execute(
            f"UPDATE raw_inbox SET document_id=? WHERE document_id IN ({ph})",
            (keeper_id, *lo)).rowcount
        conn.execute(f"DELETE FROM refresh_queue WHERE document_id IN ({ph})", lo)
        conn.execute(f"DELETE FROM expand_queue WHERE document_id IN ({ph})", lo)
        out["deleted"] = conn.execute(
            f"DELETE FROM documents WHERE id IN ({ph})", lo).rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return out


# --- search ---

_FTS_TOKEN = re.compile(r"[0-9A-Za-z가-힣]+")


def _fts_query(query: str) -> str:
    """자유 텍스트를 안전한 FTS5 MATCH 식으로 변환.

    FTS5 는 `/ . : -` 등을 연산자로 해석해 syntax error 를 낸다. 영숫자/한글
    토큰만 추출해 각각 "큰따옴표"로 감싸고 OR 로 잇는다(부분 매칭 지향).
    """
    toks = _FTS_TOKEN.findall(query or "")
    return " OR ".join(f'"{t}"' for t in toks)


def fts_search(conn: sqlite3.Connection, query: str, limit: int = 20) -> list[str]:
    """엔티티 FTS 검색 → entity_id 리스트(랭크 순)."""
    match = _fts_query(query)
    if not match:
        return []
    try:
        rows = conn.execute(
            "SELECT entity_id FROM entities_fts WHERE entities_fts MATCH ? "
            "ORDER BY rank LIMIT ?",
            (match, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [r["entity_id"] for r in rows]


def counts(conn: sqlite3.Connection, include_hidden: bool = True) -> dict[str, int]:
    out = {}
    for tbl in ("documents", "entities", "relations", "embeddings", "proposals",
                "jobs", "raw_inbox", "extractions", "refresh_queue"):
        if tbl == "documents" and not include_hidden:
            out[tbl] = conn.execute("SELECT COUNT(*) c FROM documents WHERE hidden=0").fetchone()["c"]
        else:
            out[tbl] = conn.execute(f"SELECT COUNT(*) c FROM {tbl}").fetchone()["c"]
    return out
