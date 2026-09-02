"""Antigravity CLI provider unit tests."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from claire.config import Settings
from claire.extract.antigravity_provider import AntigravityProvider
from claire.extract.provider import MergeCandidate, get_provider
from claire.ontology.base import Document


def _make_settings(**kwargs) -> Settings:
    defaults = {
        "provider": "antigravity",
        "agy_bin": "agy",
        "agy_model": "gemini-3.7-flash",
        "agy_effort": "medium",
        "agy_timeout": 30.0,
        "agy_max_concurrency": 2,
    }
    defaults.update(kwargs)
    return Settings(**defaults)


def test_effective_provider_resolution():
    with patch("claire.config.find_agy_executable", return_value="/usr/local/bin/agy"):
        s = _make_settings(provider="antigravity")
        assert s.effective_provider == "antigravity"

        s_alias = _make_settings(provider="agy")
        assert s_alias.effective_provider == "antigravity"

    with patch("claire.config.find_agy_executable", return_value=None):
        s_missing = _make_settings(provider="antigravity")
        assert s_missing.effective_provider == "mock"


def test_get_provider_factory():
    s = _make_settings(provider="antigravity")
    with patch("claire.config.find_agy_executable", return_value="/usr/bin/agy"):
        prov = get_provider(s)
        assert isinstance(prov, AntigravityProvider)
        assert prov.name == "antigravity"


@patch("subprocess.run")
def test_extract_structured_success(mock_run):
    payload = {
        "status": "SUCCESS",
        "structured_output": {
            "summary": "한국어 요약입니다.",
            "key_claims": ["주장1"],
            "entities": [
                {
                    "name": "Antigravity",
                    "type": "Tool",
                    "aliases": ["agy"],
                    "observations": ["CLI 도구"],
                    "proposed_type": None,
                }
            ],
            "relations": [
                {
                    "source": "Antigravity",
                    "target": "AI",
                    "type": "used_for",
                    "proposed_type": None,
                }
            ],
        },
    }
    mock_run.return_value = SimpleNamespace(
        returncode=0,
        stdout=json.dumps(payload),
        stderr="",
    )

    s = _make_settings()
    prov = AntigravityProvider(s)
    doc = Document(id="doc1", title="AGY", raw_text="Antigravity is a tool.", source_type="text")

    result = prov.extract(doc)
    assert result.summary == "한국어 요약입니다."
    assert len(result.entities) == 1
    assert result.entities[0].name == "Antigravity"
    assert result.entities[0].type == "Tool"
    assert len(result.relations) == 1
    assert result.model == prov.model

    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert "agy" in cmd[0]
    assert "-p" in cmd
    assert "--output-format" in cmd
    assert "json" in cmd
    assert "--json-schema" in cmd
    assert "--log-file" in cmd


@patch("subprocess.run")
def test_extract_structured_success_despite_error_status(mock_run):
    """agy 내부 툴 차단 등으로 status='ERROR'가 반환되어도 structured_output이 있으면 성공."""
    payload = {
        "status": "ERROR",
        "error": "permission check failed for read_file",
        "structured_output": {
            "summary": "한국어 요약입니다.",
            "key_claims": ["주장1"],
            "entities": [
                {
                    "name": "Antigravity",
                    "type": "Tool",
                    "aliases": ["agy"],
                    "observations": ["CLI 도구"],
                    "proposed_type": None,
                }
            ],
            "relations": [],
        },
    }
    mock_run.return_value = SimpleNamespace(
        returncode=0,
        stdout=json.dumps(payload),
        stderr="",
    )

    s = _make_settings()
    prov = AntigravityProvider(s)
    doc = Document(id="doc1", title="AGY", raw_text="Antigravity is a tool.", source_type="text")

    result = prov.extract(doc)
    assert result.summary == "한국어 요약입니다."
    assert len(result.entities) == 1
    assert result.entities[0].name == "Antigravity"
    # fallback으로 넘어가지 않고 1회 호출로 바로 성공
    assert mock_run.call_count == 1


@patch("subprocess.run")
def test_extract_fallback_when_structured_fails(mock_run):
    fallback_json = json.dumps({
        "summary": "폴백 요약",
        "key_claims": [],
        "entities": [],
        "relations": [],
    })
    mock_run.side_effect = [
        RuntimeError("CLI structured error"),
        SimpleNamespace(returncode=0, stdout=fallback_json, stderr=""),
    ]

    s = _make_settings()
    prov = AntigravityProvider(s)
    doc = Document(id="doc1", title="Test", raw_text="Some text", source_type="text")

    result = prov.extract(doc)
    assert result.summary == "폴백 요약"
    assert mock_run.call_count == 2


@patch("subprocess.run")
def test_summarize_search(mock_run):
    mock_run.return_value = SimpleNamespace(
        returncode=0,
        stdout="[Antigravity]는 강력한 개발 도구입니다.",
        stderr="",
    )
    prov = AntigravityProvider(_make_settings())
    ans = prov.summarize_search("Antigravity란?", "Antigravity info")
    assert ans == "[Antigravity]는 강력한 개발 도구입니다."
    cmd = mock_run.call_args[0][0]
    assert "--output-format" in cmd
    assert "text" in cmd


@patch("subprocess.run")
def test_render_detail(mock_run):
    mock_run.return_value = SimpleNamespace(
        returncode=0,
        stdout="## 개요\n\n**Antigravity**는 최신 AI CLI입니다.",
        stderr="",
    )
    prov = AntigravityProvider(_make_settings())
    doc = Document(id="doc1", title="Doc", raw_text="Content", source_type="text")
    detail = prov.render_detail(doc)
    assert "## 개요" in detail
    assert "**Antigravity**" in detail


@patch("subprocess.run")
def test_classify_watch(mock_run):
    payload = {
        "status": "SUCCESS",
        "structured_output": {
            "watch": True,
            "interval_days": 1,
            "reason": "실시간 랭킹 순위표",
        },
    }
    mock_run.return_value = SimpleNamespace(
        returncode=0,
        stdout=json.dumps(payload),
        stderr="",
    )
    prov = AntigravityProvider(_make_settings())
    doc = Document(id="doc1", title="Leaderboard", raw_text="Rankings", source_type="text")
    res = prov.classify_watch(doc)
    assert res["watch"] is True
    assert res["interval_days"] == 1
    assert res["reason"] == "실시간 랭킹 순위표"


@patch("subprocess.run")
def test_research_with_markdown_citations(mock_run):
    report_text = (
        "Antigravity는 Google의 차세대 AI 플랫폼입니다.\n\n"
        "자세한 정보는 [공식 문서](https://antigravity.google/docs) 및 "
        "https://github.com/google-antigravity 를 참고하십시오."
    )
    mock_run.return_value = SimpleNamespace(
        returncode=0,
        stdout=report_text,
        stderr="",
    )
    prov = AntigravityProvider(_make_settings())
    res = prov.research("Antigravity", "Context")

    assert "Antigravity는 Google의 차세대 AI 플랫폼입니다." in res["report"]
    urls = [s["url"] for s in res["sources"]]
    assert "https://antigravity.google/docs" in urls
    assert "https://github.com/google-antigravity" in urls


@patch("subprocess.run")
def test_judge_research(mock_run):
    payload = {
        "status": "SUCCESS",
        "structured_output": {
            "relevance": 0.95,
            "quality": 0.90,
            "same_subject": True,
            "interpretation": "Antigravity AI 플랫폼",
            "reason": "맥락과 완벽히 일치",
        },
    }
    mock_run.return_value = SimpleNamespace(
        returncode=0,
        stdout=json.dumps(payload),
        stderr="",
    )
    prov = AntigravityProvider(_make_settings())
    res = prov.judge_research("Antigravity", "Context", "Report")
    assert res["relevance"] == 0.95
    assert res["quality"] == 0.90
    assert res["same_subject"] is True


@patch("subprocess.run")
def test_select_followups(mock_run):
    payload = {
        "status": "SUCCESS",
        "structured_output": {
            "follow": [0, 2],
            "reason": "0번과 2번 링크가 핵심 자료임",
        },
    }
    mock_run.return_value = SimpleNamespace(
        returncode=0,
        stdout=json.dumps(payload),
        stderr="",
    )
    prov = AntigravityProvider(_make_settings())
    cands = [
        {"url": "https://example.com/doc1", "anchor": "Doc 1"},
        {"url": "https://example.com/terms", "anchor": "Terms"},
        {"url": "https://example.com/doc2", "anchor": "Doc 2"},
    ]
    indices = prov.select_followups("Context", cands)
    assert indices == [0, 2]


@patch("subprocess.run")
def test_judge_same_entity(mock_run):
    mock_run.return_value = SimpleNamespace(
        returncode=0,
        stdout="SAME",
        stderr="",
    )
    prov = AntigravityProvider(_make_settings())
    mc = MergeCandidate(
        new_name="AGY",
        new_type="Tool",
        new_observations=[],
        cand_name="Antigravity",
        cand_type="Tool",
        cand_aliases=[],
        cand_observations=[],
    )
    assert prov.judge_same_entity(mc) is True

    mock_run.return_value = SimpleNamespace(
        returncode=0,
        stdout="DIFFERENT",
        stderr="",
    )
    assert prov.judge_same_entity(mc) is False


def test_embed_deterministic_hash():
    prov = AntigravityProvider(_make_settings(gemini_api_key=""))
    v1 = prov.embed("hello world")
    v2 = prov.embed("hello world")
    v3 = prov.embed("another text")

    assert len(v1) == AntigravityProvider.EMBED_DIM
    assert v1 == v2
    assert v1 != v3


@patch("subprocess.run")
def test_cli_timeout_handling(mock_run):
    mock_run.side_effect = subprocess.TimeoutExpired(cmd=["agy"], timeout=30.0)
    prov = AntigravityProvider(_make_settings())

    with pytest.raises(RuntimeError) as exc_info:
        prov._run_cli("test prompt")
    assert "timed out" in str(exc_info.value)


def test_extraction_json_schema_required():
    from claire.extract.provider import ExtractionResult

    schema = ExtractionResult.extraction_json_schema()
    assert "required" in schema
    assert "summary" in schema["required"]
    assert "entities" in schema["required"]
    assert "relations" in schema["required"]


@patch("subprocess.run")
def test_effort_flag_omitted_for_model_with_effort_suffix(mock_run):
    mock_run.return_value = SimpleNamespace(
        returncode=0,
        stdout="response text",
        stderr="",
    )
    s = _make_settings(agy_model="gemini-3.7-flash-high", agy_effort="medium")
    prov = AntigravityProvider(s)
    prov._run_cli("test prompt", output_format="text")

    cmd = mock_run.call_args[0][0]
    assert "--model" in cmd
    assert "gemini-3.7-flash-high" in cmd
    # model 명에 -high 가 있으므로 --effort 는 전달되지 않아야 함
    assert "--effort" not in cmd


@patch("subprocess.run")
def test_extract_fallback_summary_when_empty(mock_run):
    payload = {
        "status": "SUCCESS",
        "structured_output": {
            "summary": "",
            "key_claims": ["클레어바이블은 개인 지식베이스이다."],
            "entities": [
                {
                    "name": "Claire",
                    "type": "Tool",
                    "aliases": [],
                    "observations": [],
                    "proposed_type": None,
                }
            ],
            "relations": [],
        },
    }
    mock_run.return_value = SimpleNamespace(
        returncode=0,
        stdout=json.dumps(payload),
        stderr="",
    )
    s = _make_settings()
    prov = AntigravityProvider(s)
    doc = Document(id="doc1", title="Claire Bible", raw_text="클레어바이블 본문 텍스트입니다.", source_type="text")

    res = prov.extract(doc)
    # 빈 summary가 key_claims로 fallback되어 채워짐
    assert res.summary == "클레어바이블은 개인 지식베이스이다."


def test_latest_extraction_summary_fallbacks(tmp_path):
    from claire.store import db as dbm

    conn = dbm.connect(tmp_path / "test.db")
    dbm.init_db(conn)

    # 1) extractions 에 summary 가 있는 정상 문서
    doc1 = Document(id="doc1", title="Doc 1", raw_text="본문 1", source_type="text")
    dbm.insert_document(conn, doc1)
    dbm.log_extraction(
        conn, document_id="doc1", provider="mock", model="mock", prompt_version="v1",
        raw_response=json.dumps({"summary": "정상 요약 1", "key_claims": [], "entities": [], "relations": []}),
    )
    assert dbm.latest_extraction_summary(conn, "doc1") == "정상 요약 1"

    # 2) extractions 은 없으나 detail 이 있는 문서 -> detail 첫 단락 fallback
    doc2 = Document(id="doc2", title="Doc 2", raw_text="본문 2", source_type="text")
    dbm.insert_document(conn, doc2)
    dbm.set_document_detail(conn, "doc2", "= 문서 제목\n\n이것은 상세 내용의 첫 번째 단락입니다.\n\n두 번째 단락")
    assert dbm.latest_extraction_summary(conn, "doc2") == "이것은 상세 내용의 첫 번째 단락입니다."

    # 3) extractions 도 detail 도 없으나 raw_text 가 있는 문서 -> raw_text fallback
    doc3 = Document(id="doc3", title="Doc 3", raw_text="원문 본문 텍스트입니다.", source_type="text")
    dbm.insert_document(conn, doc3)
    assert dbm.latest_extraction_summary(conn, "doc3") == "원문 본문 텍스트입니다."


def test_backfill_summaries_service(tmp_path):
    from claire.config import Settings
    from claire.ingest.service import IngestService
    from claire.store import db as dbm

    db_file = tmp_path / "test_backfill.db"
    s = Settings(
        provider="mock",
        db_path=str(db_file),
        vault_path=str(tmp_path / "vault"),
    )
    svc = IngestService(s)

    conn = dbm.connect(s.db_file)
    dbm.init_db(conn)
    # summary 가 없는 문서 생성
    doc = Document(id="doc_missing", title="Missing Summary Doc", raw_text="원문 텍스트 요약용 내용입니다.", source_type="text")
    dbm.insert_document(conn, doc)
    conn.close()

    res = svc.backfill_summaries()
    assert res["docs"] == 1
    assert res["filled"] == 1

    conn = dbm.connect(s.db_file)
    summ = dbm.latest_extraction_summary(conn, "doc_missing")
    assert summ is not None
    assert "원문 텍스트" in summ
    conn.close()
