"""Tests for graph bundle review evidence (format v2 review_* tables).

See docs/design/GRAPH_BUNDLE_REVIEW_EVIDENCE_DESIGN.md.
"""

from __future__ import annotations

import builtins
import hashlib
import json
import math
import sqlite3
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import zstandard as zstd

import claire.graph_bundle_review as review_mod
from claire.extract.provider import MockProvider
from claire.graph_bundle import create_graph_bundle, import_graph_bundle
from claire.ontology.base import Entity
from claire.store import db as dbm
from claire.store.telemetry import connect_telemetry, get_telemetry_db_path
from claire.store.vectors import looks_like_hash_fallback, pack_vector, unpack_vector

JEV_SECRET = "jev-secret-key-123"
GEMINI_SECRET = "gemini-secret-456"
URL_SECRET = "url-password-789"


@dataclass
class ReviewStubSettings:
    db_file: Path
    data_dir: Path
    environment: str = "development"
    public_url: str = "http://127.0.0.1:8765"
    multi_theme: bool = False
    provider: str = "antigravity"
    enable_jev: bool = True
    jev_api_key: str = JEV_SECRET
    jev_base_url: str = f"https://user:{URL_SECRET}@api.typesafe.ai/v1?key={URL_SECRET}"
    gemini_api_key: str = GEMINI_SECRET
    gemini_embed_model: str = "text-embedding-004"

    @property
    def effective_provider(self) -> str:
        return "mock"


def _unit(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec]


def _seed(db_path: Path) -> dict[str, str]:
    conn = dbm.connect(db_path)
    dbm.init_db(conn)
    ents = {
        "a": Entity(type="Tool", name="Docker", sources=["doc_1"]),
        "b": Entity(type="Tool", name="Docker Engine", sources=["doc_1"]),
        "c": Entity(type="Tool", name="Podman", sources=["doc_1"]),
        "d": Entity(type="Concept", name="Unrelated", sources=["doc_1"]),
        "h": Entity(type="Tool", name="HashOnly", sources=["doc_1"]),
        "x": Entity(type="Tool", name="OtherDim", sources=["doc_1"]),
    }
    for ent in ents.values():
        dbm.upsert_entity(conn, ent)
    vectors = {
        "a": [1.0, 0.0, 0.0, 0.0],
        "b": _unit([0.95, 0.3122, 0.0, 0.0]),
        "c": _unit([0.8, 0.6, 0.0, 0.0]),
        "d": [0.0, 0.0, 1.0, 0.0],
        "h": MockProvider().embed("HashOnly"),
        "x": [1.0, 0.0, 0.0],
    }
    for key, vec in vectors.items():
        conn.execute(
            "INSERT INTO embeddings (owner_id, dim, vector, model, updated_at) VALUES (?, ?, ?, ?, ?)",
            (ents[key].id, len(vec), pack_vector(vec), "claire", 1.0),
        )
    # Embedding of a non-exported owner (chunk id) must be ignored
    conn.execute(
        "INSERT INTO embeddings (owner_id, dim, vector, model, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("chunk_1", 4, pack_vector([1.0, 0.0, 0.0, 0.0]), "claire", 1.0),
    )

    resolution_log = [
        {
            "entity": "Docker",
            "stage": "no_match",
            "decision": "CREATE_NEW",
            "target_entity_id": ents["a"].id,
            "reason": "No candidate satisfied safe gating criteria.",
            "timestamp": 1790000000.0,
            "candidates": [
                {"id": ents["c"].id, "origin": "vector", "score": 0.8, "outcome": "judge_different",
                 "name": "Podman", "type": "Tool", "cand_obs_count": 0, "judge_ms": 1250.0},
            ],
            "input_snapshot": {"name": "Docker", "type": "Tool", "aliases": [], "observations": ["obs"],
                               "provisional": False},
            "gate_params": {"auto": 0.93, "borderline": 0.72, "relational": 0.7, "max_judge": 3,
                            "adaptive_center": False, "embedding_dim": 4, "embedding_hash_fallback": False},
        },
        {
            "entity": "docker",
            "stage": "exact_match",
            "decision": "MERGE",
            "candidate": "Docker",
            "score": 1.0,
            "target_entity_id": ents["a"].id,
            "rollback_payload": {"added_aliases": ["docker"], "added_observations": []},
            "timestamp": 1790000001.0,
        },
    ]
    meta = {
        "resolution_log": resolution_log,
        "heatmap_matrix": {"generated_by": "jev", "rows": ["Docker"], "cols": ["Docker", "Podman"],
                           "matrix": [[0.99, 0.6]], "threshold_auto_merge": 0.93,
                           "threshold_borderline": 0.72, "created_at": 1790000002.0},
        "unrelated_meta_key": "MUST_NOT_BE_EXPORTED",
    }
    conn.execute(
        "INSERT INTO documents (id, url, title, fetched_at, raw_text, source_type, meta) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("doc_1", "https://example.com/1", "Doc 1", 1790000000.0, "RAW_TEXT_MUST_NOT_BE_EXPORTED", "web",
         json.dumps(meta)),
    )
    conn.execute(
        "INSERT INTO documents (id, url, title, fetched_at, raw_text, source_type, meta) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("doc_2", "https://example.com/2", "Doc 2", 1790000100.0, "raw", "web", json.dumps({"other": 1})),
    )
    conn.commit()
    conn.close()
    return {key: ent.id for key, ent in ents.items()}


def _seed_telemetry(data_dir: Path) -> None:
    conn = connect_telemetry(get_telemetry_db_path(data_dir))
    rows = [
        (1790000000.0, "doc_1", "antigravity", "gemini-3.7-flash", "judge_same_entity", 900, 1250, "SUCCESS",
         "SNIPPET_MUST_NOT_BE_EXPORTED", "ERROR_MUST_NOT_BE_EXPORTED"),
        (1790000005.0, "doc_1", "antigravity", "gemini-3.7-flash", "judge_relationship", 1500, 13000, "SUCCESS",
         None, None),
        (1790000006.0, "doc_1", "antigravity", "gemini-3.7-flash", "extract_json", 50000, 9000, "SUCCESS",
         None, None),
        (1790000007.0, "doc_other_theme", "antigravity", "gemini-3.7-flash", "judge_same_entity", 800, 1100,
         "SUCCESS", None, None),
        (1790000008.0, None, "antigravity", "gemini-3.7-flash", "judge_same_entity", 700, 2000, "SUCCESS",
         None, None),
        (1790000009.0, None, "antigravity", "gemini-3.7-flash", "judge_same_entity", 600, 4000, "SUCCESS",
         None, None),
        (1790000010.0, None, "antigravity", "gemini-3.7-flash", "judge_same_entity", 600, 9999, "TIMEOUT",
         None, None),
    ]
    conn.executemany(
        "INSERT INTO provider_telemetry (timestamp, document_id, provider, model, call_type, input_chars, "
        "duration_ms, status, output_snippet, error_message) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def _extract(archive: Path, dest: Path) -> tuple[dict[str, Any], Path]:
    manifest: dict[str, Any] | None = None
    db_path = dest / "graph_export.db"
    with open(archive, "rb") as f_in:
        with zstd.ZstdDecompressor().stream_reader(f_in) as reader:
            with tarfile.open(fileobj=reader, mode="r|") as tar:
                for member in tar:
                    f = tar.extractfile(member)
                    assert f is not None
                    if member.name.endswith("manifest.json"):
                        manifest = json.loads(f.read().decode("utf-8"))
                    elif member.name.endswith("graph_export.db"):
                        db_path.write_bytes(f.read())
    assert manifest is not None
    return manifest, db_path


@pytest.fixture()
def exported(tmp_path: Path) -> dict[str, Any]:
    db_file = tmp_path / "claire.db"
    ids = _seed(db_file)
    _seed_telemetry(tmp_path)
    info = create_graph_bundle(ReviewStubSettings(db_file=db_file, data_dir=tmp_path), theme_id=0)
    out = tmp_path / "out"
    out.mkdir()
    manifest, db_path = _extract(info.filepath, out)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    yield {"info": info, "manifest": manifest, "db_path": db_path, "conn": conn, "ids": ids, "tmp": tmp_path}
    conn.close()


def _meta(conn: sqlite3.Connection, key: str) -> Any:
    row = conn.execute("SELECT value FROM review_meta WHERE key=?", (key,)).fetchone()
    assert row is not None, key
    return json.loads(row["value"])


def test_manifest_v2_and_checksum_cover_review_tables(exported: dict[str, Any]) -> None:
    manifest = exported["manifest"]
    assert manifest["bundle_version"] == 2
    review = manifest["review"]
    assert review["schema_version"] == 1
    assert review["decisions"] == 2
    assert review["decisions_with_candidates"] == 1
    assert review["heatmaps"] == 1
    assert review["candidate_pairs_status"] == "ok"
    assert review["judge_calls"] == 2
    assert exported["info"].review == review
    assert exported["info"].to_dict()["review"] == review
    digest = hashlib.sha256(exported["db_path"].read_bytes()).hexdigest()
    assert manifest["graph_export_sha256"] == digest


def test_decisions_and_heatmaps_flattened(exported: dict[str, Any]) -> None:
    conn = exported["conn"]
    rows = conn.execute("SELECT * FROM review_decisions ORDER BY seq").fetchall()
    assert [r["stage"] for r in rows] == ["no_match", "exact_match"]
    traced = json.loads(rows[0]["candidates"])
    assert traced[0]["outcome"] == "judge_different"
    assert traced[0]["judge_ms"] == 1250.0
    assert json.loads(rows[0]["input_snapshot"])["observations"] == ["obs"]
    assert json.loads(rows[0]["gate_params"])["auto"] == 0.93
    assert rows[1]["candidates"] is None
    assert json.loads(rows[1]["rollback_payload"])["added_aliases"] == ["docker"]

    heat = conn.execute("SELECT * FROM review_heatmaps").fetchall()
    assert len(heat) == 1
    assert heat[0]["generated_by"] == "jev"
    assert heat[0]["rows_count"] == 1 and heat[0]["cols_count"] == 2

    coverage = _meta(conn, "coverage")
    assert coverage["documents"] == 2
    assert coverage["documents_with_resolution_log"] == 1
    assert coverage["candidate_outcomes"] == {"judge_different": 1}
    assert coverage["heatmaps_by_generator"] == {"jev": 1}


def test_candidate_pairs_exclude_hash_fallback_and_other_dims(exported: dict[str, Any]) -> None:
    conn = exported["conn"]
    ids = exported["ids"]
    pairs = {
        (r["entity_a"], r["entity_b"]): (r["score"], r["band"])
        for r in conn.execute("SELECT * FROM review_candidate_pairs")
    }

    def key(x: str, y: str) -> tuple[str, str]:
        return tuple(sorted((ids[x], ids[y])))  # type: ignore[return-value]

    assert set(pairs) == {key("a", "b"), key("a", "c"), key("b", "c")}
    score_ab, band_ab = pairs[key("a", "b")]
    assert score_ab == pytest.approx(0.95, abs=1e-3)
    assert band_ab == "auto_merge"
    assert pairs[key("a", "c")] == (pytest.approx(0.8, abs=1e-3), "borderline")
    involved = {e for pair in pairs for e in pair}
    assert ids["h"] not in involved and ids["x"] not in involved and ids["d"] not in involved

    emb = _meta(conn, "embeddings")
    assert emb["hash_fallback"] == 1
    assert emb["pair_dim"] == 4
    assert emb["used_for_pairs"] == 4
    assert emb["excluded_other_dim"] == 1
    assert emb["with_embedding"] == 6
    meta_pairs = _meta(conn, "candidate_pairs")
    assert meta_pairs["status"] == "ok"
    assert meta_pairs["backend"] == "numpy"
    assert meta_pairs["by_band"] == {"auto_merge": 2, "borderline": 1}


def test_judge_calls_theme_isolated_and_whitelisted(exported: dict[str, Any]) -> None:
    conn = exported["conn"]
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(review_judge_calls)")}
    assert "output_snippet" not in cols and "error_message" not in cols
    rows = conn.execute("SELECT * FROM review_judge_calls ORDER BY id").fetchall()
    assert [(r["document_id"], r["call_type"]) for r in rows] == [
        ("doc_1", "judge_same_entity"),
        ("doc_1", "judge_relationship"),
    ]
    assert rows[0]["duration_ms"] == 1250 and rows[0]["input_chars"] == 900

    # Unattributed (document_id NULL) successful calls leave only as aggregates; other themes' rows not at all
    judge_meta = _meta(conn, "judge_calls")
    assert judge_meta["unattributed_success"] == {
        "judge_same_entity": {
            "count": 2,
            "duration_ms_p50": 4000,
            "duration_ms_p95": 4000,
            "duration_ms_max": 4000,
            "input_chars_p50": 700,
            "input_chars_p95": 700,
        }
    }


def test_no_secrets_or_raw_text_in_bundle(exported: dict[str, Any]) -> None:
    blob = exported["db_path"].read_bytes() + json.dumps(exported["manifest"]).encode("utf-8")
    for needle in (JEV_SECRET, GEMINI_SECRET, URL_SECRET, "RAW_TEXT_MUST_NOT_BE_EXPORTED",
                   "MUST_NOT_BE_EXPORTED", "SNIPPET_MUST_NOT_BE_EXPORTED"):
        assert needle.encode("utf-8") not in blob, needle
    pipeline = _meta(exported["conn"], "pipeline")
    assert pipeline["jev_api_key_configured"] is True
    assert pipeline["gemini_api_key_configured"] is True
    assert pipeline["enable_jev"] is True
    assert pipeline["jev_base_url"] == "https://api.typesafe.ai/v1"
    assert pipeline["effective_provider"] == "mock"


def test_import_skips_review_tables(exported: dict[str, Any]) -> None:
    target = exported["tmp"] / "target.db"
    result = import_graph_bundle(target, exported["info"].filepath)
    assert result["bundle_version"] == 2
    assert result["review_tables"]["review_decisions"] == 2
    assert result["entities_count"] == 6
    conn = sqlite3.connect(str(target))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert not any(name.startswith("review_") for name in tables)


def test_python_fallback_matches_numpy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ids = [f"e{i}" for i in range(6)]
    vectors = [_unit([1.0, 0.1 * i, 0.05 * i * i, 0.0]) for i in range(6)]
    expected = review_mod._top_k_pairs_numpy(ids, vectors, k=3, floor=0.55, center=True)
    actual = review_mod._top_k_pairs_python(ids, vectors, k=3, floor=0.55, center=True)
    assert set(actual) == set(expected)
    for key, score in expected.items():
        assert actual[key] == pytest.approx(score, abs=1e-5)


def test_pairs_skipped_without_numpy_above_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def _no_numpy(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "numpy":
            raise ImportError("numpy disabled for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_numpy)
    monkeypatch.setattr(review_mod, "PURE_PYTHON_PAIR_LIMIT", 2)
    db_file = tmp_path / "claire.db"
    _seed(db_file)
    info = create_graph_bundle(ReviewStubSettings(db_file=db_file, data_dir=tmp_path), theme_id=0)
    assert info.review["candidate_pairs_status"] == "skipped"
    assert info.review["candidate_pairs"] == 0

    monkeypatch.setattr(review_mod, "PURE_PYTHON_PAIR_LIMIT", 400)
    info = create_graph_bundle(ReviewStubSettings(db_file=db_file, data_dir=tmp_path), theme_id=0)
    assert info.review["candidate_pairs_status"] == "ok"
    assert info.review["candidate_pairs"] == 3
    out = tmp_path / "out"
    out.mkdir()
    _, db_path = _extract(info.filepath, out)
    conn = sqlite3.connect(str(db_path))
    backend = json.loads(conn.execute("SELECT value FROM review_meta WHERE key='candidate_pairs'").fetchone()[0])
    conn.close()
    assert backend["backend"] == "python"


def test_section_failure_does_not_abort_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("telemetry unavailable")

    monkeypatch.setattr(review_mod, "_export_judge_calls", _boom)
    db_file = tmp_path / "claire.db"
    _seed(db_file)
    info = create_graph_bundle(ReviewStubSettings(db_file=db_file, data_dir=tmp_path), theme_id=0)
    assert info.entities_count == 6
    assert info.review["judge_calls_status"] == "error"
    assert info.review["decisions"] == 2


def test_missing_telemetry_reported(tmp_path: Path) -> None:
    db_file = tmp_path / "claire.db"
    _seed(db_file)
    info = create_graph_bundle(ReviewStubSettings(db_file=db_file, data_dir=tmp_path), theme_id=0)
    assert info.review["judge_calls_status"] == "missing"


def test_looks_like_hash_fallback() -> None:
    hashed = MockProvider().embed("anything")
    assert looks_like_hash_fallback(hashed) is True
    assert looks_like_hash_fallback(unpack_vector(pack_vector(hashed))) is True
    assert looks_like_hash_fallback(_unit([float(i % 7) - 3.0 for i in range(64)])) is False
    assert looks_like_hash_fallback([0.5] * 32) is False
    assert looks_like_hash_fallback(None) is False
