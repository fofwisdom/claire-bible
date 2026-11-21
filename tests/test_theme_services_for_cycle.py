"""_theme_services_for_cycle gives the theme ID to each IngestService.

The theme default focus is removed (CT28); the tests of that rule are removed with it.
"""

from __future__ import annotations

import json
import pytest

from claire.cli import _ThemeLoopState, _theme_services_for_cycle
from claire.core.config import Settings
from claire.ingest import pipeline as pipemod
from claire.ingest import service as svcmod
from claire.ingest.service import IngestService
from claire.ontology.base import Document
from claire.store import db as dbm
from claire.store.knowledge_base import KnowledgeBaseManager

PARENT_STANDALONE = "https://parent.example/standalone-post"
CHILD_STANDALONE = "https://other.example/standalone-child"

PARENT_MERGE = "https://parent.example/merge-post"
CHILD_MERGE = "https://same.example/merge-child"


def _doc(url: str, text: str, *, links: list[str] | None = None, title: str | None = None) -> Document:
    meta = {}
    if links is not None:
        meta["links"] = links
    return Document(
        url=url,
        canonical_url=url,
        title=title or url.rsplit("/", 1)[-1],
        raw_text=text,
        source_type="web",
        content_hash=str(hash(text)),
        meta=meta,
    )


def _fetch(payload: str) -> Document:
    body = "유익하고 구체적인 본문 내용입니다. " * 20
    if payload == PARENT_STANDALONE:
        return _doc(PARENT_STANDALONE, "독립 확장 부모 문서 고유 본문 내용입니다. " * 30, links=[CHILD_STANDALONE])
    if payload == CHILD_STANDALONE:
        # '별개주제' -> judge_gate returns same_subject=False -> standalone ingest
        return _doc(CHILD_STANDALONE, "별개주제 — 부모 글이 언급한 독립된 프로젝트 내용. " + body, title="독립 프로젝트")
    if payload == PARENT_MERGE:
        return _doc(PARENT_MERGE, "병합 확장 부모 문서 고유 본문 내용입니다. " * 30, links=[CHILD_MERGE])
    if payload == CHILD_MERGE:
        # No '별개주제' -> judge_gate returns same_subject=True -> merge into parent
        return _doc(CHILD_MERGE, "같은 주제의 추가 출처 본문. " + body, title="동일 주제 부가 출처")
    return _doc(payload, f"일반 문서 본문 ({payload}). " + body, title="일반 문서")


@pytest.fixture
def theme_env(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    data_dir.mkdir(parents=True)
    vault_dir.mkdir(parents=True)

    db_path = str(data_dir / "claire.db")
    vault_path = str(vault_dir)

    settings = Settings(
        CLAIRE_DB_PATH=db_path,
        CLAIRE_VAULT_PATH=vault_path,
        CLAIRE_PROVIDER="mock",
        CLAIRE_MULTI_THEME=True,
    )
    tm = KnowledgeBaseManager(base_settings=settings)
    monkeypatch.setattr(svcmod, "default_fetch", _fetch)
    monkeypatch.setattr(pipemod, "default_fetch", _fetch)
    return tm, settings


def test_theme_services_for_cycle_passes_kb_id(theme_env):
    """_theme_services_for_cycle gives the knowledge base ID to the service factory, and takes only personal knowledge bases (CT10)."""
    tm, base_settings = theme_env
    shared = tm.define_kb("AI", knowledge_type="real")
    state = _ThemeLoopState()

    services = _theme_services_for_cycle(base_settings, state, IngestService)
    kb_ids = [t.id for t, _ in services]
    assert kb_ids == [0] and shared.id not in kb_ids

    svc = services[0][1]
    assert isinstance(svc, IngestService)
    assert svc.kb_id == 0
