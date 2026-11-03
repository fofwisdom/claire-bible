"""테마별 임베딩 모델 고정 (설계 §10.4, P4 L4-a3).

- 첫 저장이 테마를 그 모델로 고정하고, 다른 모델 벡터는 들어가지 않는다.
- 검색은 고정 모델 행만 보고, 질의 모델이 다르면 벡터 결과가 없다.
- 고정되지 않은 레거시 테마는 전과 같다.
- 모델 미기록(``'claire'``) 행 백필은 멱등이다.
- re-embed 가 고정을 새 모델로 바꾼다.
"""

from __future__ import annotations

import os
import sqlite3
import time
from argparse import Namespace
from pathlib import Path

import pytest

from claire.config import get_settings
from claire.extract.resolver import resolve_or_create
from claire.ontology.base import Entity
from claire.retrieval.query import search
from claire.store import db as dbm
from claire.store.theme import ThemeInfo, embed_mismatch
from claire.store.vectors import (
    MOCK_EMBED_MODEL,
    EmbeddingModelMismatch,
    VectorStore,
    pack_vector,
    read_embed_pin,
    resolve_embed_identity,
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    for name in list(os.environ):
        if name.startswith("CLAIRE_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _conn(tmp_path: Path) -> sqlite3.Connection:
    conn = dbm.connect(tmp_path / "claire.db")
    dbm.init_db(conn)
    return conn


def _raw_put(conn: sqlite3.Connection, owner_id: str, vec: list[float], model: str | None, theme_id: int = 0) -> None:
    """고정 규칙을 거치지 않고 행을 넣는다(업그레이드 전 DB 재현용)."""
    conn.execute(
        "INSERT OR REPLACE INTO embeddings(owner_id,theme_id,dim,vector,model,updated_at) VALUES (?,?,?,?,?,?)",
        (owner_id, theme_id, len(vec), pack_vector(vec), model, time.time()),
    )
    conn.commit()


def _models(conn: sqlite3.Connection, theme_id: int = 0) -> dict[str, str | None]:
    return {
        r["owner_id"]: r["model"]
        for r in conn.execute("SELECT owner_id, model FROM embeddings WHERE theme_id=?", (theme_id,))
    }


class _FakeProvider:
    def __init__(self, name: str, embed_model: str, vec: list[float]) -> None:
        self.name = name
        self.embed_model = embed_model
        self._vec = vec

    def embed(self, text: str, *, task_type: str | None = None, title: str | None = None) -> list[float]:
        return list(self._vec)


def test_first_put_pins_theme(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    assert read_embed_pin(conn, 0) is None
    vs = VectorStore(conn, "brute")
    vs.put("e1", [1.0, 0.0], "model-a", embed_provider="gemini")
    pin = read_embed_pin(conn, 0)
    assert pin is not None and pin.model == "model-a" and pin.provider == "gemini"
    # 같은 모델은 계속 받는다. 고정은 바뀌지 않는다.
    vs.put("e2", [0.0, 1.0], "model-a", embed_provider="other")
    assert read_embed_pin(conn, 0) == pin


def test_other_model_vector_is_rejected(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    vs = VectorStore(conn, "brute")
    vs.put("e1", [1.0, 0.0], "model-a")
    with pytest.raises(EmbeddingModelMismatch):
        vs.put("e2", [1.0, 0.0], "model-b")
    # 모델 미기록 라벨도 고정 뒤에는 받지 않는다.
    with pytest.raises(EmbeddingModelMismatch):
        vs.put("e3", [1.0, 0.0], "claire")
    assert _models(conn) == {"e1": "model-a"}


def test_search_ignores_other_model_rows(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    vs = VectorStore(conn, "brute")
    vs.put("pinned", [1.0, 0.0], "model-a")
    _raw_put(conn, "foreign", [1.0, 0.0], "model-b")
    _raw_put(conn, "unknown", [1.0, 0.0], "claire")
    hits = dict(vs.search([1.0, 0.0], limit=10))
    assert set(hits) == {"pinned"}


def test_query_model_mismatch_returns_no_vector_hits(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    vs = VectorStore(conn, "brute")
    vs.put("e1", [1.0, 0.0], "model-a")
    assert vs.search([1.0, 0.0], query_model="model-b") == []
    assert [o for o, _ in vs.search([1.0, 0.0], query_model="model-a")] == ["e1"]


def test_hybrid_search_falls_back_to_fts_on_model_mismatch(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    ent = Entity(type="Tool", name="Ollama", observations=["local runner"], sources=["d1"])
    dbm.upsert_entity(conn, ent)
    vs = VectorStore(conn, "brute")
    vs.put(ent.id, [1.0, 0.0], "model-a")

    same = search(conn, vs, _FakeProvider("gemini", "model-a", [1.0, 0.0]), "Ollama",
                  summarize=False, theme_id=0)
    assert same.hits and "vec" in same.hits[0].via

    other = search(conn, vs, _FakeProvider("openai", "model-b", [1.0, 0.0]), "Ollama",
                   summarize=False, theme_id=0)
    assert other.hits and other.hits[0].entity.id == ent.id
    assert other.hits[0].via == ["fts"]


def test_legacy_unpinned_theme_behaves_as_before(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _raw_put(conn, "a", [1.0, 0.0], "claire")
    _raw_put(conn, "b", [0.9, 0.1], "gemini-embedding-001")
    _raw_put(conn, "c", [0.0, 1.0], None)
    vs = VectorStore(conn, "brute")
    assert read_embed_pin(conn, 0) is None
    hits = [o for o, _ in vs.search([1.0, 0.0], limit=10)]
    assert set(hits) == {"a", "b", "c"}
    # 미고정이면 질의 모델로 거르지 않는다.
    assert {o for o, _ in vs.search([1.0, 0.0], limit=10, query_model="x")} == {"a", "b", "c"}
    # 모델 미기록 라벨로 저장해도 고정하지 않는다.
    vs.put("d", [1.0, 1.0], "claire")
    assert read_embed_pin(conn, 0) is None


def test_first_put_adopts_same_dim_legacy_rows(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _raw_put(conn, "old2", [1.0, 0.0], "claire")
    _raw_put(conn, "old3", [1.0, 0.0, 0.0], "claire")
    vs = VectorStore(conn, "brute")
    vs.put("new", [0.5, 0.5], "model-a")
    assert _models(conn) == {"old2": "model-a", "old3": "claire", "new": "model-a"}
    assert {o for o, _ in vs.search([1.0, 0.0], limit=10)} == {"old2", "new"}


def test_pin_is_per_theme(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    conn.execute(
        "INSERT INTO themes (id, seq, label, created_at, updated_at, account_id) VALUES (7, 7, 'T7', 0, 0, 1)"
    )
    conn.commit()
    VectorStore(conn, "brute", theme_id=0).put("e0", [1.0, 0.0], "model-a")
    VectorStore(conn, "brute", theme_id=7).put("e7", [1.0, 0.0], "model-b")
    assert read_embed_pin(conn, 0).model == "model-a"
    assert read_embed_pin(conn, 7).model == "model-b"


def test_backfill_is_idempotent_and_skips_unpinned(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    conn.execute(
        "INSERT INTO themes (id, seq, label, created_at, updated_at, account_id) VALUES (7, 7, 'T7', 0, 0, 1)"
    )
    conn.execute("UPDATE themes SET embed_model='model-a', embed_provider='gemini' WHERE id=7")
    conn.commit()
    _raw_put(conn, "p1", [1.0, 0.0], "model-a", theme_id=7)
    _raw_put(conn, "p2", [0.0, 1.0], "claire", theme_id=7)
    _raw_put(conn, "p3", [0.0, 1.0, 0.0], "claire", theme_id=7)  # 차원이 달라 다른 모델이 분명
    _raw_put(conn, "u1", [1.0, 0.0], "claire", theme_id=0)       # 미고정 테마: 건드리지 않는다

    assert dbm.backfill_embedding_models(conn) == 1
    conn.commit()
    assert _models(conn, 7) == {"p1": "model-a", "p2": "model-a", "p3": "claire"}
    assert _models(conn, 0) == {"u1": "claire"}

    assert dbm.backfill_embedding_models(conn) == 0
    dbm.init_db(conn)  # 재기동 경로도 멱등
    assert _models(conn, 7) == {"p1": "model-a", "p2": "model-a", "p3": "claire"}
    assert dbm.stored_schema_version(conn) == dbm.SCHEMA_VERSION
    # 미고정 관리자 테마의 검색 결과는 그대로 남는다.
    assert [o for o, _ in VectorStore(conn, "brute").search([1.0, 0.0])] == ["u1"]


def test_backfill_relabels_all_legacy_rows_when_pin_has_no_rows(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _raw_put(conn, "a", [1.0, 0.0], "claire")
    _raw_put(conn, "b", [1.0, 0.0], None)
    conn.execute("UPDATE themes SET embed_model='model-a' WHERE id=0")
    conn.commit()
    dbm.init_db(conn)
    assert _models(conn) == {"a": "model-a", "b": "model-a"}


def test_resolver_records_provider_embed_model(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    vs = VectorStore(conn, "brute")
    provider = _FakeProvider("codex", "gemini-embedding-001", [0.3, 0.7])

    def _embed():
        return provider.embed("frame")

    res = resolve_or_create(conn, vs, name="Brand New", etype="Tool", aliases=[], observations=[],
                            document_id="d1", embed_fn=_embed)
    assert res.created
    assert _models(conn)[res.entity.id] == "gemini-embedding-001"
    pin = read_embed_pin(conn, 0)
    assert pin.model == "gemini-embedding-001" and pin.provider == "gemini"


def test_resolver_skips_vector_on_model_mismatch(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    vs = VectorStore(conn, "brute")
    seed = Entity(type="Tool", name="Seed", observations=[], sources=["d0"])
    dbm.upsert_entity(conn, seed)
    vs.put(seed.id, [1.0, 0.0], "model-a")

    res = resolve_or_create(conn, vs, name="Other Thing", etype="Tool", aliases=[], observations=[],
                            document_id="d1", embed_fn=lambda: [1.0, 0.0], embed_model="model-b")
    # 다른 공간의 질의는 벡터 후보를 내지 않는다(코사인 1.0 이어도 자동 머지 없음).
    assert res.created
    assert res.entity.id not in _models(conn)
    assert _models(conn) == {seed.id: "model-a"}


def test_resolve_embed_identity() -> None:
    assert resolve_embed_identity(_FakeProvider("gemini", "m1", [])) == ("gemini", "m1")
    assert resolve_embed_identity(_FakeProvider("antigravity", "m1", [])).provider == "gemini"

    class _Mock:
        name = "mock"

    assert resolve_embed_identity(_Mock()).model == MOCK_EMBED_MODEL


def test_embed_mismatch_helper() -> None:
    unpinned = ThemeInfo(id=0, seq=0, label="t")
    assert embed_mismatch(unpinned, "gemini", "anything") is False
    pinned = ThemeInfo(id=0, seq=0, label="t", embed_provider="gemini", embed_model="m1")
    assert embed_mismatch(pinned, "gemini", "m1") is False
    assert embed_mismatch(pinned, "gemini", "m2") is True
    assert embed_mismatch(pinned, "openai", "m1") is True
    assert embed_mismatch(pinned, "", "m1") is False
    legacy_provider = ThemeInfo(id=0, seq=0, label="t", embed_model="m1")
    assert embed_mismatch(legacy_provider, "openai", "m1") is False


def test_reembed_replaces_pin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from claire.cli import cmd_reembed

    db_file = tmp_path / "claire.db"
    conn = _conn(tmp_path)
    e1 = Entity(type="Tool", name="ToolA", observations=["Fast"], sources=["d1"])
    e2 = Entity(type="Concept", name="ConceptB", observations=["Key"], sources=["d1"])
    dbm.upsert_entity(conn, e1)
    dbm.upsert_entity(conn, e2)
    vs = VectorStore(conn, "brute")
    vs.put(e1.id, [1.0, 0.0], "old-model", embed_provider="gemini")
    _raw_put(conn, "orphan-old", [0.0, 1.0], "old-model")
    conn.close()

    monkeypatch.setenv("CLAIRE_DB_PATH", str(db_file))
    monkeypatch.setenv("CLAIRE_VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("CLAIRE_PROVIDER", "mock")
    get_settings.cache_clear()

    assert cmd_reembed(Namespace(limit=0, dry_run=True, theme=None)) == 0
    conn = dbm.connect(db_file)
    assert read_embed_pin(conn, 0).model == "old-model"
    conn.close()

    assert cmd_reembed(Namespace(limit=0, dry_run=False, theme=None)) == 0
    conn = dbm.connect(db_file)
    pin = read_embed_pin(conn, 0)
    assert pin.model == MOCK_EMBED_MODEL and pin.provider == "mock"
    assert _models(conn) == {e1.id: MOCK_EMBED_MODEL, e2.id: MOCK_EMBED_MODEL}
    # 새 고정 뒤에는 새 모델 벡터만 검색되고, 옛 모델은 거부된다.
    vs = VectorStore(conn, "brute")
    assert {o for o, _ in vs.search(vs.get(e1.id), limit=10)} == {e1.id, e2.id}
    with pytest.raises(EmbeddingModelMismatch):
        vs.put("late", [1.0, 0.0], "old-model")
    conn.close()
