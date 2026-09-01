"""Codex CLI provider unit and security-contract tests."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from claire.config import Settings
from claire.extract.codex_provider import CodexProvider
from claire.extract.prompts import PROMPT_VERSION
from claire.extract.provider import ExtractionResult, MergeCandidate, get_provider
from claire.ingest.pipeline import ingest
from claire.ontology.base import Document
from claire.ontology.base import Entity
from claire.retrieval.query import search
from claire.store import db as dbm
from claire.store.vectors import VectorStore


def _make_settings(**kwargs) -> Settings:
    defaults = {
        "provider": "codex",
        "codex_bin": "codex",
        "codex_model": "",
        "codex_effort": "medium",
        "codex_timeout": 30.0,
        "codex_max_concurrency": 1,
        "gemini_api_key": "",
    }
    defaults.update(kwargs)
    return Settings(**defaults)


def _make_provider(**kwargs) -> CodexProvider:
    return CodexProvider(_make_settings(**kwargs))


def _has_pair(args: list[str], option: str, value: str) -> bool:
    return any(
        args[index] == option and args[index + 1] == value
        for index in range(len(args) - 1)
    )


def _feature_is_disabled(args: list[str], feature: str) -> bool:
    """Accept the CLI's --disable form or an equivalent explicit config override."""
    if _has_pair(args, "--disable", feature):
        return True
    return any(
        feature in arg and arg.rsplit("=", 1)[-1].strip().lower() == "false"
        for arg in args
    )


def _successful_cli_output(
    output: str,
    *,
    captured: dict | None = None,
):
    """Return a subprocess.run side effect that emulates --output-last-message."""

    def _run(args, **kwargs):
        args = list(args)
        output_path = Path(args[args.index("--output-last-message") + 1])
        if captured is not None:
            captured["args"] = args
            captured["kwargs"] = kwargs
            captured["cwd"] = Path(kwargs["cwd"])
            captured["cwd_entries"] = list(Path(kwargs["cwd"]).iterdir())
            captured["output_path"] = output_path
            if "--output-schema" in args:
                schema_path = Path(args[args.index("--output-schema") + 1])
                captured["schema_path"] = schema_path
                captured["schema"] = json.loads(schema_path.read_text(encoding="utf-8"))
        output_path.write_text(output, encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return _run


def test_effective_provider_resolution_and_alias():
    with patch(
        "claire.config.find_codex_executable", return_value="/usr/local/bin/codex"
    ):
        assert _make_settings(provider="codex").effective_provider == "codex"
        assert _make_settings(provider="codex-cli").effective_provider == "codex"

    with patch("claire.config.find_codex_executable", return_value=None):
        assert _make_settings(provider="codex").effective_provider == "mock"
        assert _make_settings(provider="codex-cli").effective_provider == "mock"


def test_get_provider_factory():
    with patch(
        "claire.config.find_codex_executable", return_value="/usr/local/bin/codex"
    ):
        provider = get_provider(_make_settings())

    assert isinstance(provider, CodexProvider)
    assert provider.name == "codex"


@patch("claire.extract.codex_provider.subprocess.run")
def test_run_cli_uses_stdin_isolated_temp_files_and_sanitized_environment(mock_run):
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
    }
    captured: dict = {}
    mock_run.side_effect = _successful_cli_output(
        json.dumps({"answer": "ok"}), captured=captured
    )
    provider = _make_provider(
        codex_model="gpt-5.6-luna",
        codex_effort="low",
        codex_timeout=17.5,
    )
    parent_env = {
        "PATH": "/safe/bin",
        "HOME": "/safe/home",
        "CODEX_HOME": "/safe/codex-home",
        "OPENAI_API_KEY": "openai-secret",
        "HTTPS_PROXY": "https://proxy.example",
        "SSL_CERT_FILE": "/safe/cert.pem",
        "CLAIRE_PROVIDER": "codex",
        "CLAIRE_INJECT_TOKEN": "claire-secret",
        "TELEGRAM_BOT_TOKEN": "telegram-secret",
        "GEMINI_API_KEY": "gemini-secret",
        "UNRELATED_SECRET": "unrelated-secret",
    }

    with patch.dict(os.environ, parent_env, clear=True):
        result = provider._run_cli(
            "prompt containing a private document",
            schema=schema,
            effort="high",
        )

    assert result == {"answer": "ok"}

    args = captured["args"]
    kwargs = captured["kwargs"]
    assert "prompt containing a private document" not in args
    assert kwargs["input"].endswith("prompt containing a private document")
    assert "exec" in args
    assert "resume" not in args
    assert "fork" not in args
    assert "--ephemeral" in args
    assert "--ignore-user-config" in args
    assert "--ignore-rules" in args
    assert "--skip-git-repo-check" in args
    assert _has_pair(args, "--sandbox", "read-only")
    assert _has_pair(args, "--color", "never")
    assert _has_pair(args, "--model", "gpt-5.6-luna") or _has_pair(
        args, "-m", "gpt-5.6-luna"
    )
    assert _has_pair(args, "--ask-for-approval", "never") or _has_pair(
        args, "-a", "never"
    ) or any("approval_policy" in arg and "never" in arg for arg in args)
    assert any(
        "model_reasoning_effort" in arg and "high" in arg for arg in args
    )
    assert "--search" not in args
    assert "--dangerously-bypass-approvals-and-sandbox" not in args

    for feature in (
        "shell_tool",
        "unified_exec",
        "plugins",
        "apps",
        "memories",
        "multi_agent",
    ):
        assert _feature_is_disabled(args, feature), feature
    # These capabilities are removed and disabled in the supported Codex CLI.
    # Do not attempt to re-enable them under a strict-config invocation.
    assert not _has_pair(args, "--enable", "apply_patch_freeform")
    assert not _has_pair(args, "--enable", "tool_search")

    child_env = kwargs["env"]
    for allowed in (
        "PATH",
        "HOME",
        "CODEX_HOME",
        "OPENAI_API_KEY",
        "HTTPS_PROXY",
        "SSL_CERT_FILE",
    ):
        assert child_env[allowed] == parent_env[allowed]
    for forbidden in (
        "CLAIRE_PROVIDER",
        "CLAIRE_INJECT_TOKEN",
        "TELEGRAM_BOT_TOKEN",
        "GEMINI_API_KEY",
        "UNRELATED_SECRET",
    ):
        assert forbidden not in child_env

    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert kwargs["check"] is False
    assert kwargs["timeout"] == 17.5

    temp_cwd = captured["cwd"]
    output_path = captured["output_path"]
    schema_path = captured["schema_path"]
    assert captured["cwd_entries"] == []
    assert output_path.parent == temp_cwd.parent
    assert schema_path.parent == temp_cwd.parent
    assert captured["schema"] == schema
    assert not temp_cwd.parent.exists()
    assert not temp_cwd.exists()
    assert not output_path.exists()
    assert not schema_path.exists()


@patch("claire.extract.codex_provider.subprocess.run")
def test_extract_structured_output_metadata_and_default_model(mock_run):
    payload = {
        "summary": "한국어 요약이다.",
        "key_claims": ["클레어는 지식베이스이다."],
        "entities": [
            {
                "name": "Claire",
                "type": "Tool",
                "aliases": [],
                "observations": ["개인 지식베이스"],
                "proposed_type": None,
            }
        ],
        "relations": [],
    }
    captured: dict = {}
    mock_run.side_effect = _successful_cli_output(json.dumps(payload), captured=captured)
    provider = _make_provider(codex_model="")
    doc = Document(
        id="doc-1",
        title="Claire Bible",
        raw_text="클레어는 개인 지식베이스이다.",
        source_type="text",
    )

    result = provider.extract(doc, effort="high")

    assert isinstance(result, ExtractionResult)
    assert result.summary == "한국어 요약이다."
    assert result.entities[0].name == "Claire"
    assert result.model == "codex-cli-default"
    assert result.prompt_version == PROMPT_VERSION
    assert json.loads(result.raw_response)["summary"] == "한국어 요약이다."
    assert captured["schema"] == ExtractionResult.extraction_json_schema()
    assert "--model" not in captured["args"]
    assert "-m" not in captured["args"]
    assert any(
        "model_reasoning_effort" in arg and "high" in arg
        for arg in captured["args"]
    )


def test_extract_fills_empty_summary_from_key_claims():
    provider = _make_provider()
    provider._run_cli = MagicMock(
        return_value={
            "summary": "",
            "key_claims": ["첫 번째 핵심 주장이다.", "두 번째 핵심 주장이다."],
            "entities": [],
            "relations": [],
        }
    )
    doc = Document(
        id="doc-2",
        title="빈 요약",
        raw_text="본문",
        source_type="text",
    )

    result = provider.extract(doc)

    assert result.summary == "첫 번째 핵심 주장이다. 두 번째 핵심 주장이다."
    assert provider._run_cli.call_args.kwargs["schema"] == (
        ExtractionResult.extraction_json_schema()
    )
    assert provider._run_cli.call_args.kwargs.get("web_search", False) is False


def test_extract_normalizes_schema_validation_failure():
    provider = _make_provider()
    provider._run_cli = MagicMock(
        return_value={
            "summary": "요약",
            "key_claims": [],
            "entities": "not-a-list",
            "relations": [],
        }
    )
    doc = Document(
        id="doc-invalid",
        title="잘못된 구조",
        raw_text="본문",
        source_type="text",
    )

    with pytest.raises(RuntimeError, match="schema validation"):
        provider.extract(doc)


def test_text_and_structured_provider_methods_follow_existing_contract():
    provider = _make_provider()
    doc = Document(
        id="doc-3",
        title="Weekly Leaderboard",
        raw_text="Rankings and source text",
        source_type="text",
    )

    provider._run_cli = MagicMock(return_value="검색 결과 종합")
    assert provider.summarize_search("질의", "근거") == "검색 결과 종합"
    assert provider._run_cli.call_args.kwargs.get("schema") is None
    assert provider._run_cli.call_args.kwargs.get("web_search", False) is False

    provider._run_cli = MagicMock(return_value="## 개요\n\n상세 내용")
    assert provider.render_detail(doc) == "## 개요\n\n상세 내용"
    assert provider._run_cli.call_args.kwargs.get("schema") is None
    assert provider._run_cli.call_args.kwargs.get("web_search", False) is False

    provider._run_cli = MagicMock(
        return_value={"is_paper": True, "reason": "연구 논문 형식"}
    )
    assert provider.classify_paper(doc, effort="low") == (
        True,
        "연구 논문 형식",
    )
    assert provider._run_cli.call_args.kwargs["schema"]["type"] == "object"
    assert provider._run_cli.call_args.kwargs["effort"] == "low"
    assert provider._run_cli.call_args.kwargs.get("web_search", False) is False

    provider._run_cli = MagicMock(
        return_value={
            "watch": True,
            "interval_days": 7,
            "reason": "주간 순위표",
        }
    )
    assert provider.classify_watch(doc) == {
        "watch": True,
        "interval_days": 7,
        "reason": "주간 순위표",
    }
    assert provider._run_cli.call_args.kwargs["schema"]["type"] == "object"
    assert provider._run_cli.call_args.kwargs.get("web_search", False) is False

    provider._run_cli = MagicMock(
        return_value={
            "relevance": 0.94,
            "quality": 0.88,
            "same_subject": True,
            "interpretation": "같은 도구",
            "reason": "근거가 일치함",
        }
    )
    judgement = provider.judge_research("Claire", "맥락", "보고서")
    assert judgement["relevance"] == 0.94
    assert judgement["quality"] == 0.88
    assert judgement["same_subject"] is True
    assert provider._run_cli.call_args.kwargs["schema"]["type"] == "object"
    assert provider._run_cli.call_args.kwargs.get("web_search", False) is False

    candidates = [
        {"url": "https://example.com/0", "anchor": "0"},
        {"url": "https://example.com/1", "anchor": "1"},
        {"url": "https://example.com/2", "anchor": "2"},
    ]
    provider._run_cli = MagicMock(
        return_value={"follow": [0, 2, 9, -1], "reason": "관련 링크"}
    )
    assert provider.select_followups("맥락", candidates) == [0, 2]
    assert provider._run_cli.call_args.kwargs["schema"]["type"] == "object"
    assert provider._run_cli.call_args.kwargs.get("web_search", False) is False

    mc = MergeCandidate(
        new_name="Claire",
        new_type="Tool",
        cand_name="Claire Bible",
        cand_type="Tool",
    )
    provider._run_cli = MagicMock(return_value={"same": True})
    assert provider.judge_same_entity(mc) is True
    assert provider._run_cli.call_args.kwargs["schema"]["type"] == "object"
    assert provider._run_cli.call_args.kwargs.get("web_search", False) is False

    provider._run_cli = MagicMock(return_value={"same": False})
    assert provider.judge_same_entity(mc) is False


@patch("claire.extract.codex_provider.subprocess.run")
def test_research_is_the_only_method_that_enables_native_web_search(mock_run):
    report = (
        "조사 보고서이다.\n\n"
        "[공식 문서](https://example.com/docs)\n"
        "추가 자료: https://example.com/reference"
    )
    captured: dict = {}
    mock_run.side_effect = _successful_cli_output(report, captured=captured)
    provider = _make_provider()

    result = provider.research("Claire", "Claire 관련 맥락")

    assert result["report"] == report
    assert {source["url"] for source in result["sources"]} == {
        "https://example.com/docs",
        "https://example.com/reference",
    }
    assert "--search" in captured["args"]
    assert "--output-schema" not in captured["args"]

    non_research: dict = {}
    mock_run.side_effect = _successful_cli_output("검색 종합", captured=non_research)
    assert provider.summarize_search("질의", "근거") == "검색 종합"
    assert "--search" not in non_research["args"]


@patch("claire.extract.codex_provider.subprocess.run")
def test_run_cli_normalizes_timeout(mock_run):
    mock_run.side_effect = subprocess.TimeoutExpired(cmd=["codex"], timeout=30.0)
    provider = _make_provider()

    with pytest.raises(RuntimeError, match="timed out"):
        provider._run_cli("prompt")


@patch("claire.extract.codex_provider.subprocess.run")
def test_run_cli_normalizes_nonzero_exit_and_redacts_secrets(mock_run):
    mock_run.return_value = SimpleNamespace(
        returncode=7,
        stdout="",
        stderr=(
            "authentication failed for sk-test-openai-secret and "
            "claire-inject-secret"
        ),
    )
    provider = _make_provider()
    secret_env = {
        "PATH": "/safe/bin",
        "HOME": "/safe/home",
        "OPENAI_API_KEY": "sk-test-openai-secret",
        "CLAIRE_INJECT_TOKEN": "claire-inject-secret",
    }

    with patch.dict(os.environ, secret_env, clear=True):
        with pytest.raises(RuntimeError) as exc_info:
            provider._run_cli("prompt")

    message = str(exc_info.value)
    assert "7" in message
    assert "sk-test-openai-secret" not in message
    assert "claire-inject-secret" not in message


@pytest.mark.parametrize(
    ("output", "schema", "expected"),
    [
        ("   \n", None, "empty"),
        ("not valid JSON", {"type": "object"}, "JSON"),
    ],
)
@patch("claire.extract.codex_provider.subprocess.run")
def test_run_cli_rejects_empty_or_invalid_output(mock_run, output, schema, expected):
    mock_run.side_effect = _successful_cli_output(output)
    provider = _make_provider()

    with pytest.raises(RuntimeError, match=expected):
        provider._run_cli("prompt", schema=schema)


def test_embed_delegates_to_gemini_provider_when_key_exists():
    settings = _make_settings(gemini_api_key="gemini-key")
    provider = CodexProvider(settings)

    with patch("claire.extract.gemini_provider.GeminiProvider") as gemini_cls:
        gemini_cls.return_value.embed.return_value = [0.1, 0.2, 0.3]
        vector = provider.embed("embedding input")

    assert vector == [0.1, 0.2, 0.3]
    gemini_cls.assert_called_once_with(settings)
    gemini_cls.return_value.embed.assert_called_once_with("embedding input")


def test_embed_without_gemini_key_fails_instead_of_returning_hash_vector():
    provider = _make_provider(gemini_api_key="")

    with pytest.raises(RuntimeError, match="Gemini|embedding|FTS"):
        provider.embed("embedding input")


def test_missing_gemini_key_keeps_hybrid_search_on_fts_candidates():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    dbm.upsert_entity(
        conn,
        Entity(
            type="Tool",
            name="Codex FTS Entity",
            observations=["Codex provider fallback search"],
        ),
    )
    provider = _make_provider(gemini_api_key="")

    result = search(
        conn,
        VectorStore(conn, "brute"),
        provider,
        "Codex",
        summarize=False,
        mode="hybrid",
    )

    assert [hit.entity.name for hit in result.hits] == ["Codex FTS Entity"]
    assert result.hits[0].via == ["fts"]
    assert dbm.counts(conn)["embeddings"] == 0
    conn.close()


def test_ingest_skips_vector_storage_when_codex_embedding_is_unavailable(tmp_path):
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    provider = _make_provider(gemini_api_key="")
    provider.extract = MagicMock(
        return_value=ExtractionResult(
            summary="FTS 전용 적재이다.",
            entities=[
                {
                    "name": "No Hash Vector",
                    "type": "Tool",
                    "observations": ["embedding unavailable"],
                }
            ],
            relations=[],
            model="codex-cli-default",
            prompt_version=PROMPT_VERSION,
        )
    )
    provider.render_detail = MagicMock(return_value="상세 본문")
    provider.classify_watch = MagicMock(
        return_value={"watch": False, "interval_days": None, "reason": "고정 문서"}
    )
    document = Document(
        id="codex-no-vector-doc",
        title="No hash vector",
        raw_text="Codex embedding fallback test body",
        source_type="text",
        url="https://example.com/codex-no-vector",
    )

    report = ingest(
        document.url,
        conn=conn,
        provider=provider,
        vstore=VectorStore(conn, "brute"),
        vault_dir=tmp_path / "vault",
        prefetched=document,
    )

    assert report.error is None
    assert report.entities_created == 1
    assert dbm.counts(conn)["embeddings"] == 0
    conn.close()
