"""Unit tests for theme database-level default_focus universal rule.

Verifies:
- Standalone 1-hop expansion document (same_subject=False) in a theme with default_focus:
  child document is saved with focus equal to theme's default_focus in documents.meta
  and detail rendering.
- Merged 1-hop expansion document (same_subject=True) in a theme with default_focus:
  parent document detail re-rendering uses theme's default_focus.
- Theme 0 (default theme) 1-hop expansion:
  focus remains None.
- Explicit focus override:
  if caller explicitly provides focus, it overrides default_focus.
- Effective default_focus fallback lookup via ThemeManager when settings default_focus is unset.
- _theme_services_for_cycle propagates theme_id to IngestService.
"""

from __future__ import annotations

import json
import pytest

from claire.cli import _ThemeLoopState, _theme_services_for_cycle
from claire.config import Settings
from claire.ingest import pipeline as pipemod
from claire.ingest import service as svcmod
from claire.ingest.service import IngestService
from claire.ontology.base import Document
from claire.store import db as dbm
from claire.store.theme import ThemeManager

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
        # '별개주제' -> judge_research returns same_subject=False -> standalone ingest
        return _doc(CHILD_STANDALONE, "별개주제 — 부모 글이 언급한 독립된 프로젝트 내용. " + body, title="독립 프로젝트")
    if payload == PARENT_MERGE:
        return _doc(PARENT_MERGE, "병합 확장 부모 문서 고유 본문 내용입니다. " * 30, links=[CHILD_MERGE])
    if payload == CHILD_MERGE:
        # No '별개주제' -> judge_research returns same_subject=True -> merge into parent
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
    tm = ThemeManager(base_settings=settings)
    monkeypatch.setattr(svcmod, "default_fetch", _fetch)
    monkeypatch.setattr(pipemod, "default_fetch", _fetch)
    return tm, settings


def test_standalone_expansion_inherits_theme_default_focus(theme_env):
    """Standalone 1-hop expansion document (same_subject=False) in a theme with default_focus."""
    tm, base_settings = theme_env
    theme = tm.define_theme("AI Research", default_focus="인공지능 에이전트 및 분산 시스템 아키텍처 관점")
    theme_settings = tm.get_settings_for_theme(theme.id, base_settings)
    assert theme_settings.theme_id == theme.id
    assert theme_settings.default_focus == "인공지능 에이전트 및 분산 시스템 아키텍처 관점"

    svc = IngestService(theme_settings, theme_id=theme.id)
    assert svc.theme_id == theme.id
    assert svc.get_effective_default_focus() == "인공지능 에이전트 및 분산 시스템 아키텍처 관점"

    parent_rep = svc.ingest(PARENT_STANDALONE, source="cli", expand_max=0)
    assert not parent_rep.error

    res = svc.expand_document(parent_rep.document_id)
    assert res["stored"] == 1
    assert res["merged"] == 0

    conn = dbm.connect(theme_settings.db_file)
    child_id = dbm.find_document_by_canonical_url(conn, CHILD_STANDALONE)
    assert child_id is not None

    focus_val = dbm.get_document_focus(conn, child_id)
    assert focus_val == "인공지능 에이전트 및 분산 시스템 아키텍처 관점"

    row = dbm.get_document_row(conn, child_id)
    meta = json.loads(row["meta"] or "{}")
    assert meta.get("focus") == "인공지능 에이전트 및 분산 시스템 아키텍처 관점"

    detail = row["detail"]
    assert detail is not None
    assert "[focus: 인공지능 에이전트 및 분산 시스템 아키텍처 관점]" in detail
    conn.close()


def test_merged_expansion_applies_theme_default_focus(theme_env):
    """Merged 1-hop expansion document (same_subject=True) in a theme with default_focus."""
    tm, base_settings = theme_env
    theme = tm.define_theme("Security", default_focus="취약점 및 보안 위협 분석 관점")
    theme_settings = tm.get_settings_for_theme(theme.id, base_settings)

    svc = IngestService(theme_settings, theme_id=theme.id)
    parent_rep = svc.ingest(PARENT_MERGE, source="cli", expand_max=0)
    assert not parent_rep.error

    res = svc.expand_document(parent_rep.document_id)
    assert res["merged"] == 1
    assert res["stored"] == 0

    conn = dbm.connect(theme_settings.db_file)
    focus_val = dbm.get_document_focus(conn, parent_rep.document_id)
    assert focus_val == "취약점 및 보안 위협 분석 관점"

    row = dbm.get_document_row(conn, parent_rep.document_id)
    meta = json.loads(row["meta"] or "{}")
    assert meta.get("focus") == "취약점 및 보안 위협 분석 관점"

    detail = row["detail"]
    assert detail is not None
    assert "[focus: 취약점 및 보안 위협 분석 관점]" in detail
    conn.close()


def test_theme_zero_expansion_focus_remains_none(theme_env):
    """Theme 0 (default theme) 1-hop expansion: focus remains None."""
    tm, base_settings = theme_env
    theme0_settings = tm.get_settings_for_theme(0, base_settings)
    assert theme0_settings.theme_id == 0
    assert theme0_settings.default_focus == ""

    svc = IngestService(theme0_settings, theme_id=0)
    assert svc.get_effective_default_focus() is None

    # 1. Standalone expansion in theme 0
    parent_rep = svc.ingest(PARENT_STANDALONE, source="cli", expand_max=0)
    res = svc.expand_document(parent_rep.document_id)
    assert res["stored"] == 1

    conn = dbm.connect(theme0_settings.db_file)
    child_id = dbm.find_document_by_canonical_url(conn, CHILD_STANDALONE)
    assert child_id is not None
    assert dbm.get_document_focus(conn, child_id) is None
    row = dbm.get_document_row(conn, child_id)
    meta = json.loads(row["meta"] or "{}")
    assert meta.get("focus") is None
    assert "[focus:" not in (row["detail"] or "")
    conn.close()

    # 2. Merged expansion in theme 0
    parent_merge_rep = svc.ingest(PARENT_MERGE, source="cli", expand_max=0)
    res_merge = svc.expand_document(parent_merge_rep.document_id)
    assert res_merge["merged"] == 1

    conn = dbm.connect(theme0_settings.db_file)
    assert dbm.get_document_focus(conn, parent_merge_rep.document_id) is None
    row_merge = dbm.get_document_row(conn, parent_merge_rep.document_id)
    meta_merge = json.loads(row_merge["meta"] or "{}")
    assert meta_merge.get("focus") is None
    assert "[focus:" not in (row_merge["detail"] or "")
    conn.close()


def test_explicit_focus_overrides_theme_default_focus(theme_env):
    """Explicit focus override: if caller explicitly provides focus, it overrides default_focus."""
    tm, base_settings = theme_env
    theme = tm.define_theme("Robotics", default_focus="로보틱스 하드웨어 관점")
    theme_settings = tm.get_settings_for_theme(theme.id, base_settings)

    svc = IngestService(theme_settings, theme_id=theme.id)
    explicit = "소프트웨어 알고리즘 및 시뮬레이션 관점"

    rep = svc.ingest(
        "https://example.com/robot",
        source="cli",
        expand_max=0,
        focus=explicit,
    )
    assert not rep.error

    conn = dbm.connect(theme_settings.db_file)
    focus_val = dbm.get_document_focus(conn, rep.document_id)
    assert focus_val == explicit
    assert focus_val != theme.default_focus

    row = dbm.get_document_row(conn, rep.document_id)
    meta = json.loads(row["meta"] or "{}")
    assert meta.get("focus") == explicit
    assert f"[focus: {explicit}]" in (row["detail"] or "")
    conn.close()


def test_effective_default_focus_fallback_to_theme_manager(theme_env):
    """When settings.default_focus is empty, get_effective_default_focus falls back to ThemeManager."""
    tm, base_settings = theme_env
    theme = tm.define_theme("Bio", default_focus="생물정보학 및 단백질 구조 관점")
    bare_settings = base_settings.model_copy(update={"theme_id": theme.id, "default_focus": ""})
    svc = IngestService(bare_settings, theme_id=theme.id)
    assert svc.default_focus == ""
    assert svc.get_effective_default_focus() == "생물정보학 및 단백질 구조 관점"
    assert svc.default_focus == "생물정보학 및 단백질 구조 관점"


def test_theme_services_for_cycle_passes_theme_id(theme_env):
    """Verify _theme_services_for_cycle passes theme_id to service_factory."""
    tm, base_settings = theme_env
    t1 = tm.define_theme("AI", default_focus="인공지능 모델 관점")
    state = _ThemeLoopState()

    services = _theme_services_for_cycle(base_settings, state, IngestService)
    theme_ids = [t.id for t, _ in services]
    assert t1.id in theme_ids

    svc_map = {t.id: s for t, s in services}
    t1_svc = svc_map[t1.id]
    assert isinstance(t1_svc, IngestService)
    assert t1_svc.theme_id == t1.id
    assert t1_svc.get_effective_default_focus() == "인공지능 모델 관점"
