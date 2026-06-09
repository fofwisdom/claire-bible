"""SQLite 정본 스토어 — 스키마 + 마이그레이션 + 기본 CRUD.

정본은 이 단일 파일. vault(.md)는 export-only 투영(store/vault.py).
벡터는 store/vectors.py(sqlite-vec auto / brute fallback), 키워드는 FTS5.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

from ..ontology.base import Document, Entity, Relation

SCHEMA_VERSION = 4

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
    meta TEXT
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

-- FTS5 키워드 인덱스 (엔티티 이름 + 관찰). content 테이블과 분리된 standalone FTS.
CREATE VIRTUAL TABLE IF NOT EXISTS entities_fts USING fts5(
    entity_id UNINDEXED,
    name,
    body
);
"""


def backup_database(src: str | Path, dest: str | Path) -> Path:
    """SQLite 정본을 일관된 단일 파일 스냅샷으로 복제(VACUUM INTO).

    `VACUUM INTO` 는 WAL 을 반영한 트랜잭션 일관 스냅샷을 만들어, 봇/API 가 라이브로
    쓰는 중에도 안전하다(파일 복사처럼 찢긴 상태를 뜨지 않음). 정본을 읽기만 하므로
    새 실패 모드가 없다. dest 는 존재하지 않아야 한다(타임스탬프 파일명 권장).
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(src)
    try:
        conn.execute("VACUUM INTO ?", (str(dest),))
    finally:
        conn.close()
    return dest


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


def init_db(conn: sqlite3.Connection) -> None:
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

def insert_document(conn: sqlite3.Connection, doc: Document) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO documents
        (id,url,canonical_url,title,author,published_at,fetched_at,raw_text,
         source_type,content_hash,lang,partial,meta)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            doc.id, doc.url, doc.canonical_url, doc.title, doc.author,
            doc.published_at, doc.fetched_at, doc.raw_text, doc.source_type,
            doc.content_hash, doc.lang, int(doc.partial), json.dumps(doc.meta),
        ),
    )
    conn.commit()


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


def thin_documents(
    conn: sqlite3.Connection, *, max_len: int, host: str | None = None
) -> list[sqlite3.Row]:
    """본문이 빈약한(non-partial) 문서. host 지정 시 해당 호스트만."""
    q = ("SELECT id, url, title, length(raw_text) L FROM documents "
         "WHERE partial=0 AND length(raw_text) < ?")
    args: list = [max_len]
    if host:
        q += " AND url LIKE ?"
        args.append(f"%{host}%")
    return conn.execute(q, args).fetchall()


def update_document_content(
    conn: sqlite3.Connection, doc_id: str, *,
    title: str | None, raw_text: str, content_hash: str, fetched_at: float,
) -> None:
    """문서 본문을 in-place 갱신(복원). id 는 유지하여 엔티티 sources 연결 보존."""
    conn.execute(
        "UPDATE documents SET title=?, raw_text=?, content_hash=?, fetched_at=? WHERE id=?",
        (title, raw_text, content_hash, fetched_at, doc_id),
    )
    conn.commit()


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


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    out = {}
    for tbl in ("documents", "entities", "relations", "embeddings", "proposals",
                "jobs", "raw_inbox", "extractions", "refresh_queue"):
        out[tbl] = conn.execute(f"SELECT COUNT(*) c FROM {tbl}").fetchone()["c"]
    return out
