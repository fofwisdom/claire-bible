"""Graph bundle export and import module.

This module exports graph topology snapshots without vector embeddings or secret credentials.
Each bundle contains manifest.json and clean graph_export.db with entities, relations, and sanitized document metadata.
Format v2 adds review_* tables with the resolution pipeline's decision evidence (graph_bundle_review); importers skip them.
Active bundles expire after 24 hours (GRAPH_BUNDLE_TTL_SECONDS).
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import secrets
import shutil
import sqlite3
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import zstandard as zstd

from .config import ROOT, Settings, get_settings
from .graph_bundle_review import count_review_tables, export_review_evidence
from .store import db as dbm

logger = logging.getLogger(__name__)

GRAPH_BUNDLE_TTL_SECONDS = 24 * 3600  # 86400 seconds (24 hours)
_GRAPH_BUNDLE_TOKEN_PREFIX = "gb3_"
GRAPH_BUNDLE_FORMAT_VERSION = 2
_BUNDLE_REGISTRY_DIRNAME = ".registry"
_BUNDLE_REGISTRY_FORMAT_VERSION = 1

_EXCLUDED_TABLES = frozenset({
    "embeddings",
    "oauth_clients",
    "oauth_tokens",
    "oauth_codes",
    "oauth_auth_requests",
    "raw_inbox",
    "vault.db",
    "telemetry.db",
    "proposals",
    "jobs",
    "extractions",
    "refresh_queue",
    "document_snapshots",
    "purged_tombstones",
    "document_presentations",
    "entities_fts",
    "entities_fts_data",
    "entities_fts_idx",
    "entities_fts_content",
    "entities_fts_docsize",
    "entities_fts_config",
})


@dataclass(frozen=True)
class GraphBundleInfo:
    """Graph bundle metadata structure."""

    bundle_id: str
    token: str
    filename: str
    filepath: Path
    theme_id: int
    entities_count: int
    relations_count: int
    documents_count: int
    size_bytes: int
    created_at: float
    expires_at: float
    download_url: str
    review: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert bundle info to a dictionary."""
        return {
            "bundle_id": self.bundle_id,
            "download_url": self.download_url,
            "token": self.token,
            "filename": self.filename,
            "filepath": str(self.filepath),
            "theme_id": self.theme_id,
            "entities_count": self.entities_count,
            "relations_count": self.relations_count,
            "documents_count": self.documents_count,
            "size_bytes": self.size_bytes,
            "created_at": datetime.fromtimestamp(self.created_at, timezone.utc).isoformat(),
            "expires_at": datetime.fromtimestamp(self.expires_at, timezone.utc).isoformat(),
            "review": dict(self.review),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "GraphBundleInfo":
        """Restore graph bundle info from dictionary."""

        def _epoch(raw: Any) -> float:
            if isinstance(raw, (int, float)):
                return float(raw)
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()

        return cls(
            bundle_id=str(value["bundle_id"]),
            token=str(value["token"]),
            filename=str(value["filename"]),
            filepath=Path(str(value.get("filepath") or "")),
            theme_id=int(value.get("theme_id", 0)),
            entities_count=int(value.get("entities_count", 0)),
            relations_count=int(value.get("relations_count", 0)),
            documents_count=int(value.get("documents_count", 0)),
            size_bytes=int(value["size_bytes"]),
            created_at=_epoch(value["created_at"]),
            expires_at=_epoch(value["expires_at"]),
            download_url=str(value["download_url"]),
            review=dict(value.get("review") or {}),
        )


def get_graph_bundles_dir(data_dir: Path | str | None = None) -> Path:
    """Return the directory path for graph bundles."""
    if data_dir is None:
        data_dir = get_settings().data_dir
    p = Path(data_dir) / "graph_bundles"
    p.mkdir(parents=True, mode=0o700, exist_ok=True)
    return p


def _bundle_registry_path(data_dir: Path | str | None) -> Path:
    """Return the registry directory path."""
    return get_graph_bundles_dir(data_dir) / _BUNDLE_REGISTRY_DIRNAME


def _bundle_token_digest(token: str) -> str:
    """Compute SHA-256 digest of token string."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _bundle_sidecar_path(data_dir: Path | str | None, token: str) -> Path:
    """Return the sidecar json path for the token."""
    return _bundle_registry_path(data_dir) / f"{_bundle_token_digest(token)}.json"


def _validated_archive_path(data_dir: Path | str | None, record: dict[str, Any]) -> Path | None:
    """Verify that the archive file exists directly in graph_bundles directory."""
    filename = str(record.get("filename") or "")
    filepath = str(record.get("filepath") or "")
    if not filename or not filepath or Path(filename).name != filename:
        return None
    bundles_dir = get_graph_bundles_dir(data_dir).resolve(strict=False)
    candidate = Path(filepath).resolve(strict=False)
    if candidate.parent != bundles_dir or candidate.name != filename:
        return None
    return candidate


def _write_bundle_sidecar(
    data_dir: Path | str | None,
    *,
    token: str,
    record: dict[str, Any],
) -> Path:
    """Write sidecar metadata atomically to registry."""
    registry_dir = _bundle_registry_path(data_dir)
    registry_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    registry_dir.chmod(0o700)
    sidecar_path = _bundle_sidecar_path(data_dir, token)
    payload = {
        "registry_format_version": _BUNDLE_REGISTRY_FORMAT_VERSION,
        "token_sha256": _bundle_token_digest(token),
        **{key: value for key, value in record.items() if key != "token"},
    }
    descriptor, tmp_name = tempfile.mkstemp(prefix=".graph-bundle-", dir=registry_dir)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, sidecar_path)
        sidecar_path.chmod(0o600)
        directory_fd = os.open(registry_dir, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return sidecar_path


def _read_bundle_sidecar(
    data_dir: Path | str | None,
    token: str,
) -> dict[str, Any] | None:
    """Read and validate bundle sidecar metadata."""
    sidecar_path = _bundle_sidecar_path(data_dir, token)
    if not sidecar_path.is_file():
        return None
    try:
        raw = json.loads(sidecar_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        if raw.get("registry_format_version") != _BUNDLE_REGISTRY_FORMAT_VERSION:
            return None
        if not secrets.compare_digest(
            str(raw.get("token_sha256") or ""), _bundle_token_digest(token)
        ):
            return None
        record = {
            key: raw.get(key)
            for key in (
                "bundle_id",
                "filename",
                "filepath",
                "theme_id",
                "entities_count",
                "relations_count",
                "documents_count",
                "size_bytes",
                "created_at",
                "expires_at",
            )
        }
        if (
            not isinstance(record["bundle_id"], str)
            or not isinstance(record["created_at"], (int, float))
            or not isinstance(record["expires_at"], (int, float))
            or _validated_archive_path(data_dir, record) is None
        ):
            return None
        return record
    except (OSError, ValueError, TypeError):
        return None


def _delete_bundle_sidecar(data_dir: Path | str | None, token: str) -> None:
    """Delete sidecar metadata file."""
    try:
        _bundle_sidecar_path(data_dir, token).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Failed to delete graph bundle sidecar: %s", type(exc).__name__)


def _add_tar_bytes(tar: tarfile.TarFile, arcname: str, data: bytes) -> None:
    """Add memory bytes to tar archive."""
    ti = tarfile.TarInfo(name=arcname)
    ti.size = len(data)
    ti.mtime = int(time.time())
    ti.mode = 0o644
    tar.addfile(ti, io.BytesIO(data))


def lookup_graph_bundle_by_token(
    data_dir: Path | str | None,
    token: str,
) -> dict[str, Any] | None:
    """Find graph bundle metadata by download token."""
    if not isinstance(token, str) or not token.startswith(_GRAPH_BUNDLE_TOKEN_PREFIX):
        return None
    return _read_bundle_sidecar(data_dir, token)


def get_graph_bundle(
    data_dir: Path | str | None,
    token: str,
    now_epoch: float | None = None,
) -> dict[str, Any] | None:
    """Get active graph bundle by token. Delete and return None if expired."""
    now = now_epoch if now_epoch is not None else time.time()
    record = lookup_graph_bundle_by_token(data_dir, token)
    if not record:
        return None

    if record["expires_at"] <= now:
        filepath = _validated_archive_path(data_dir, record)
        if filepath is not None:
            filepath.unlink(missing_ok=True)
        _delete_bundle_sidecar(data_dir, token)
        return None

    filepath = _validated_archive_path(data_dir, record)
    if filepath is None or not filepath.is_file():
        _delete_bundle_sidecar(data_dir, token)
        return None

    record["filepath"] = str(filepath)
    return record


def list_active_graph_bundles(
    data_dir: Path | str | None,
    now_epoch: float | None = None,
) -> list[dict[str, Any]]:
    """List all active graph bundles."""
    registry_dir = _bundle_registry_path(data_dir)
    if not registry_dir.is_dir():
        return []
    now = now_epoch if now_epoch is not None else time.time()
    active_bundles: list[dict[str, Any]] = []

    for sidecar in registry_dir.glob("*.json"):
        try:
            raw = json.loads(sidecar.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                continue
            expires_at = raw.get("expires_at")
            if not isinstance(expires_at, (int, float)) or expires_at <= now:
                continue
            archive_path = _validated_archive_path(data_dir, raw)
            if archive_path is None or not archive_path.is_file():
                continue
            active_bundles.append(raw)
        except Exception:
            continue

    active_bundles.sort(key=lambda item: float(item.get("created_at") or 0.0), reverse=True)
    return active_bundles


def purge_expired_graph_bundles(
    data_dir: Path | str | None,
    now_epoch: float | None = None,
) -> int:
    """Delete all expired graph bundles and orphan files."""
    now = now_epoch if now_epoch is not None else time.time()
    purged_count = 0
    registry_dir = _bundle_registry_path(data_dir)
    cutoff = now - GRAPH_BUNDLE_TTL_SECONDS

    if registry_dir.is_dir():
        for sidecar in registry_dir.glob("*.json"):
            archive_path: Path | None = None
            expired = False
            try:
                raw = json.loads(sidecar.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    archive_path = _validated_archive_path(data_dir, raw)
                    expires_at = raw.get("expires_at")
                    expired = isinstance(expires_at, (int, float)) and expires_at <= now
                if not expired:
                    expired = sidecar.stat().st_mtime <= cutoff
                if expired:
                    if archive_path is not None and archive_path.is_file():
                        archive_path.unlink(missing_ok=True)
                        purged_count += 1
                    sidecar.unlink(missing_ok=True)
            except Exception as exc:
                logger.warning("Failed to clean graph bundle sidecar %s: %s", sidecar, exc)

    bundles_dir = get_graph_bundles_dir(data_dir)
    if bundles_dir.is_dir():
        for item in bundles_dir.glob("graph_bundle_*.tar.zst"):
            try:
                st = item.stat()
                if st.st_mtime <= cutoff:
                    item.unlink(missing_ok=True)
                    purged_count += 1
            except Exception as exc:
                logger.warning("Failed to clean graph bundle file %s: %s", item, exc)

    return purged_count


def create_graph_bundle(
    settings: Settings | Any | None = None,
    theme_id: int | str = 0,
    request_context: dict[str, Any] | None = None,
    output_path: str | Path | None = None,
    now_epoch: float | None = None,
) -> GraphBundleInfo:
    """Create a compressed graph data bundle with entities, relations, and documents."""
    s = settings or get_settings()
    data_dir = getattr(s, "data_dir", Path("data"))
    now = now_epoch if now_epoch is not None else time.time()
    expires_at = now + GRAPH_BUNDLE_TTL_SECONDS

    # Purge expired bundles first
    purge_expired_graph_bundles(data_dir, now_epoch=now)

    # Resolve theme id integer
    try:
        resolved_theme_id = int(theme_id)
    except (ValueError, TypeError):
        resolved_theme_id = 0

    # Resolve theme database path
    db_file: Path | None = None
    if getattr(s, "multi_theme", False) or resolved_theme_id != 0:
        try:
            from .store.theme import get_theme_manager
            tm = get_theme_manager(s)
            theme_info = tm.get_theme(resolved_theme_id)
            db_file = Path(theme_info.db_path)
        except Exception as exc:
            logger.warning("Theme lookup failed for %s (%s). Fall back to settings.db_file.", theme_id, exc)

    if db_file is None:
        db_file = getattr(s, "db_file", None) or Path(getattr(s, "db_path", "data/claire.db"))

    if not db_file.is_absolute():
        db_file = ROOT / db_file

    if not db_file.is_file():
        raise FileNotFoundError(f"Source database file not found at {db_file}")

    # Open source database in read-only non-blocking mode
    source_uri = db_file.resolve().as_uri() + "?mode=ro"
    source_conn = sqlite3.connect(source_uri, uri=True, timeout=5.0)
    source_conn.row_factory = sqlite3.Row
    source_conn.execute("PRAGMA query_only=ON;")
    source_conn.execute("PRAGMA busy_timeout=5000;")

    # Generate bundle identifiers
    ts_str = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%d_%H%M%S")
    rand_hex = secrets.token_hex(4)
    bundle_id = f"gb_{ts_str}_{rand_hex}"
    filename = f"graph_bundle_{bundle_id}.tar.zst"
    bundles_dir = get_graph_bundles_dir(data_dir)
    archive_path = bundles_dir / filename

    entities_count = 0
    relations_count = 0
    documents_count = 0
    entity_ids: set[str] = set()
    document_ids: set[str] = set()

    with tempfile.TemporaryDirectory() as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        export_db_path = tmp_dir / "graph_export.db"

        # Create export SQLite database
        export_conn = sqlite3.connect(str(export_db_path))
        export_conn.row_factory = sqlite3.Row

        # Create export schema
        export_conn.execute(
            """
            CREATE TABLE entities (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                name TEXT NOT NULL,
                norm_name TEXT,
                aliases TEXT,
                props TEXT,
                observations TEXT,
                sources TEXT,
                provisional INTEGER DEFAULT 0,
                created_at REAL,
                updated_at REAL
            );
            """
        )
        export_conn.execute(
            """
            CREATE TABLE relations (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                confidence REAL DEFAULT 1.0,
                props TEXT,
                sources TEXT,
                provisional INTEGER DEFAULT 0,
                created_at REAL
            );
            """
        )
        export_conn.execute(
            """
            CREATE TABLE documents (
                id TEXT PRIMARY KEY,
                title TEXT,
                url TEXT,
                source_type TEXT,
                created_at REAL
            );
            """
        )

        # Create indices
        export_conn.execute("CREATE INDEX IF NOT EXISTS idx_entities_id_type_name ON entities(id, type, name);")
        export_conn.execute("CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);")
        export_conn.execute("CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);")

        export_conn.execute("CREATE INDEX IF NOT EXISTS idx_relations_id_src_tgt_type ON relations(id, source_id, target_id, type);")
        export_conn.execute("CREATE INDEX IF NOT EXISTS idx_relations_src ON relations(source_id);")
        export_conn.execute("CREATE INDEX IF NOT EXISTS idx_relations_tgt ON relations(target_id);")
        export_conn.execute("CREATE INDEX IF NOT EXISTS idx_relations_type ON relations(type);")

        export_conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_id ON documents(id);")

        # Discover tables in source database
        source_tables = {
            r[0] for r in source_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }

        # Export entities
        if "entities" in source_tables:
            src_cols = {r["name"] for r in source_conn.execute("PRAGMA table_info(entities)").fetchall()}
            has_norm = "norm_name" in src_cols
            rows_ent = []
            for r in source_conn.execute("SELECT * FROM entities"):
                rows_ent.append((
                    r["id"],
                    r["type"],
                    r["name"],
                    r["norm_name"] if has_norm else r["name"],
                    r["aliases"] if "aliases" in src_cols else None,
                    r["props"] if "props" in src_cols else None,
                    r["observations"] if "observations" in src_cols else None,
                    r["sources"] if "sources" in src_cols else None,
                    r["provisional"] if "provisional" in src_cols else 0,
                    r["created_at"] if "created_at" in src_cols else 0.0,
                    r["updated_at"] if "updated_at" in src_cols else 0.0,
                ))
            export_conn.executemany(
                """
                INSERT INTO entities (
                    id, type, name, norm_name, aliases, props, observations, sources, provisional, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows_ent,
            )
            entities_count = len(rows_ent)
            entity_ids = {row[0] for row in rows_ent}

        # Export relations
        if "relations" in source_tables:
            src_cols = {r["name"] for r in source_conn.execute("PRAGMA table_info(relations)").fetchall()}
            rows_rel = []
            for r in source_conn.execute("SELECT * FROM relations"):
                rows_rel.append((
                    r["id"],
                    r["type"],
                    r["source_id"],
                    r["target_id"],
                    r["confidence"] if "confidence" in src_cols else 1.0,
                    r["props"] if "props" in src_cols else None,
                    r["sources"] if "sources" in src_cols else None,
                    r["provisional"] if "provisional" in src_cols else 0,
                    r["created_at"] if "created_at" in src_cols else 0.0,
                ))
            export_conn.executemany(
                """
                INSERT INTO relations (
                    id, type, source_id, target_id, confidence, props, sources, provisional, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows_rel,
            )
            relations_count = len(rows_rel)

        # Export sanitized documents metadata (no raw_text)
        if "documents" in source_tables:
            src_cols = {r["name"] for r in source_conn.execute("PRAGMA table_info(documents)").fetchall()}
            has_created = "created_at" in src_cols
            has_fetched = "fetched_at" in src_cols
            rows_doc = []
            for r in source_conn.execute("SELECT * FROM documents"):
                created_val = 0.0
                if has_created and r["created_at"] is not None:
                    try:
                        created_val = float(r["created_at"])
                    except (ValueError, TypeError):
                        created_val = 0.0
                elif has_fetched and r["fetched_at"] is not None:
                    try:
                        created_val = float(r["fetched_at"])
                    except (ValueError, TypeError):
                        created_val = 0.0

                rows_doc.append((
                    r["id"],
                    r["title"] if "title" in src_cols else "",
                    r["url"] if "url" in src_cols else "",
                    r["source_type"] if "source_type" in src_cols else "",
                    created_val,
                ))
            export_conn.executemany(
                """
                INSERT INTO documents (
                    id, title, url, source_type, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                rows_doc,
            )
            documents_count = len(rows_doc)
            document_ids = {row[0] for row in rows_doc}

        # Review evidence for engine adoption reviews (TypeSafe AI Jev): baseline decisions, candidate pairs, judge telemetry
        review_summary = export_review_evidence(
            source_conn,
            export_conn,
            settings=s,
            data_dir=data_dir,
            entity_ids=entity_ids,
            document_ids=document_ids,
            now_epoch=now,
        )

        export_conn.commit()
        export_conn.close()
        source_conn.close()

        # Compute SHA-256 digest of export database
        hasher = hashlib.sha256()
        with open(export_db_path, "rb") as f_db:
            while chunk := f_db.read(65536):
                hasher.update(chunk)
        export_sha256 = hasher.hexdigest()

        # Build manifest
        manifest_data = {
            "bundle_version": GRAPH_BUNDLE_FORMAT_VERSION,
            "bundle_id": bundle_id,
            "theme_id": resolved_theme_id,
            "created_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
            "created_at_epoch": now,
            "expires_at": datetime.fromtimestamp(expires_at, timezone.utc).isoformat(),
            "expires_at_epoch": expires_at,
            "entities_count": entities_count,
            "relations_count": relations_count,
            "documents_count": documents_count,
            "graph_export_sha256": export_sha256,
            "format": "sqlite",
            "review": review_summary,
        }
        manifest_bytes = json.dumps(manifest_data, indent=2, ensure_ascii=False).encode("utf-8")

        # Compress bundle archive with zstandard level 3
        root_arcname = f"graph_bundle_{bundle_id}"
        cctx = zstd.ZstdCompressor(level=3)
        with open(archive_path, "wb") as f_out:
            with cctx.stream_writer(f_out) as zstd_writer:
                with tarfile.open(fileobj=zstd_writer, mode="w|") as tar:
                    _add_tar_bytes(tar, f"{root_arcname}/manifest.json", manifest_bytes)
                    with open(export_db_path, "rb") as f_in_db:
                        db_bytes = f_in_db.read()
                    _add_tar_bytes(tar, f"{root_arcname}/graph_export.db", db_bytes)

    size_bytes = archive_path.stat().st_size

    # Copy to output path if specified
    if output_path is not None:
        target_out = Path(output_path).resolve()
        target_out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(archive_path, target_out)

    # Generate token and register sidecar
    token = f"{_GRAPH_BUNDLE_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
    sidecar_record = {
        "bundle_id": bundle_id,
        "token": token,
        "filename": filename,
        "filepath": str(archive_path),
        "theme_id": resolved_theme_id,
        "size_bytes": size_bytes,
        "entities_count": entities_count,
        "relations_count": relations_count,
        "documents_count": documents_count,
        "created_at": now,
        "expires_at": expires_at,
    }
    _write_bundle_sidecar(data_dir, token=token, record=sidecar_record)

    public_url = getattr(s, "public_url", "http://127.0.0.1:8765").rstrip("/")
    download_url = f"{public_url}/graph/bundle?token={token}"

    return GraphBundleInfo(
        bundle_id=bundle_id,
        token=token,
        filename=filename,
        filepath=archive_path,
        theme_id=resolved_theme_id,
        entities_count=entities_count,
        relations_count=relations_count,
        documents_count=documents_count,
        size_bytes=size_bytes,
        created_at=now,
        expires_at=expires_at,
        download_url=download_url,
        review=review_summary,
    )


def import_graph_bundle(target_db_path: Path | str, bundle_path: Path | str) -> dict[str, Any]:
    """Import graph bundle archive into target database."""
    bundle_file = Path(bundle_path).resolve()
    if not bundle_file.is_file():
        raise FileNotFoundError(f"Graph bundle archive not found at {bundle_path}")

    target_file = Path(target_db_path).resolve()
    target_file.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)

        # Decompress archive
        dctx = zstd.ZstdDecompressor()
        with open(bundle_file, "rb") as f_in:
            with dctx.stream_reader(f_in) as zstd_reader:
                with tarfile.open(fileobj=zstd_reader, mode="r|") as tar:
                    tar.extractall(path=tmp_dir)

        # Locate manifest.json and graph_export.db
        manifest_candidates = list(tmp_dir.rglob("manifest.json"))
        db_candidates = list(tmp_dir.rglob("graph_export.db"))

        if not manifest_candidates or not db_candidates:
            raise ValueError("Corrupt graph bundle: missing manifest.json or graph_export.db")

        manifest_file = manifest_candidates[0]
        export_db_file = db_candidates[0]

        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))

        # Verify checksum
        hasher = hashlib.sha256()
        with open(export_db_file, "rb") as f_chk:
            while chunk := f_chk.read(65536):
                hasher.update(chunk)
        computed_sha256 = hasher.hexdigest()
        expected_sha256 = manifest.get("graph_export_sha256")
        if expected_sha256 and not secrets.compare_digest(computed_sha256, expected_sha256):
            raise ValueError("Graph bundle database checksum verification failed")

        # Open export db read-only
        export_uri = export_db_file.resolve().as_uri() + "?mode=ro"
        export_conn = sqlite3.connect(export_uri, uri=True)
        export_conn.row_factory = sqlite3.Row
        export_conn.execute("PRAGMA query_only=ON;")

        # Open target db
        target_conn = sqlite3.connect(str(target_file))
        target_conn.row_factory = sqlite3.Row

        # Ensure target database has standard schema
        dbm.init_db(target_conn)

        # Inspect target columns
        target_ent_cols = {r["name"] for r in target_conn.execute("PRAGMA table_info(entities)").fetchall()}
        target_has_norm = "norm_name" in target_ent_cols

        # 1. Import entities
        ent_rows = export_conn.execute("SELECT * FROM entities").fetchall()
        entities_imported = 0
        if ent_rows:
            batch_ent = []
            for r in ent_rows:
                norm_val = r["norm_name"] if ("norm_name" in r.keys() and r["norm_name"]) else r["name"]
                batch_ent.append((
                    r["id"],
                    r["type"],
                    r["name"],
                    norm_val if target_has_norm else None,
                    r["aliases"],
                    r["props"],
                    r["observations"],
                    r["sources"],
                    r["provisional"],
                    r["created_at"],
                    r["updated_at"],
                ))
            if target_has_norm:
                target_conn.executemany(
                    """
                    INSERT OR REPLACE INTO entities (
                        id, type, name, norm_name, aliases, props, observations, sources, provisional, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    batch_ent,
                )
            else:
                target_conn.executemany(
                    """
                    INSERT OR REPLACE INTO entities (
                        id, type, name, aliases, props, observations, sources, provisional, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [(b[0], b[1], b[2], b[4], b[5], b[6], b[7], b[8], b[9], b[10]) for b in batch_ent],
                )
            entities_imported = len(batch_ent)

        # 2. Import relations
        rel_rows = export_conn.execute("SELECT * FROM relations").fetchall()
        relations_imported = 0
        if rel_rows:
            batch_rel = []
            for r in rel_rows:
                batch_rel.append((
                    r["id"],
                    r["type"],
                    r["source_id"],
                    r["target_id"],
                    r["confidence"],
                    r["props"],
                    r["sources"],
                    r["provisional"],
                    r["created_at"],
                ))
            target_conn.executemany(
                """
                INSERT OR REPLACE INTO relations (
                    id, type, source_id, target_id, confidence, props, sources, provisional, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                batch_rel,
            )
            relations_imported = len(batch_rel)

        # 3. Import documents metadata
        doc_rows = export_conn.execute("SELECT * FROM documents").fetchall()
        documents_imported = 0
        if doc_rows:
            target_doc_cols = {r["name"] for r in target_conn.execute("PRAGMA table_info(documents)").fetchall()}
            batch_doc = []
            for r in doc_rows:
                created_val = r["created_at"] or 0.0
                batch_doc.append((
                    r["id"],
                    r["title"],
                    r["url"],
                    r["source_type"],
                    created_val,
                ))
            if "fetched_at" in target_doc_cols:
                target_conn.executemany(
                    """
                    INSERT OR REPLACE INTO documents (
                        id, title, url, source_type, fetched_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    batch_doc,
                )
            else:
                target_conn.executemany(
                    """
                    INSERT OR REPLACE INTO documents (
                        id, title, url, source_type, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    batch_doc,
                )
            documents_imported = len(batch_doc)

        # review_* tables are bundle-only evidence, never merged into the target graph
        review_tables = count_review_tables(export_conn)

        target_conn.commit()
        target_conn.close()
        export_conn.close()

    return {
        "bundle_id": manifest.get("bundle_id"),
        "theme_id": manifest.get("theme_id", 0),
        "entities_count": entities_imported,
        "relations_count": relations_imported,
        "documents_count": documents_imported,
        "target_db_path": str(target_file),
        "bundle_version": manifest.get("bundle_version", 1),
        "review_tables": review_tables,
    }
