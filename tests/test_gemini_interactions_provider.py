"""Gemini Interactions API provider unit tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import claire.extract.gemini_provider as gp
from claire.ontology.base import Document


def _make_provider():
    p = gp.GeminiProvider.__new__(gp.GeminiProvider)
    p.model = "gemini-3.1-flash-lite"
    p.embed_model = "gemini-embedding-001"
    p.min_interval = 0.0
    p.max_retries = 1
    p.client = MagicMock()
    return p


def test_extract_interactions_success():
    p = _make_provider()
    json_payload = (
        '{"summary":"한국어 요약입니다.",'
        '"key_claims":["주장1"],'
        '"entities":[{"name":"OpenSkill","type":"Tool","aliases":[],"observations":["관찰"]}],'
        '"relations":[{"source":"OpenSkill","target":"AI","type":"used_for"}]}'
    )
    mock_interaction = SimpleNamespace(output_text=json_payload)
    p.client.interactions.create.return_value = mock_interaction

    doc = Document(id="doc1", title="Test Tool", raw_text="OpenSkill is an AI tool.", source_type="text")
    result = p.extract(doc)

    assert result.summary == "한국어 요약입니다."
    assert len(result.entities) == 1
    assert result.entities[0].name == "OpenSkill"
    assert result.entities[0].type == "Tool"
    assert len(result.relations) == 1
    assert result.model == p.model
    assert result.prompt_version == gp.PROMPT_VERSION

    # Verify interactions.create arguments
    p.client.interactions.create.assert_called_once()
    kwargs = p.client.interactions.create.call_args.kwargs
    assert kwargs["model"] == p.model
    assert kwargs["store"] is False
    assert "response_format" in kwargs
    assert kwargs["response_format"]["mime_type"] == "application/json"


def test_extract_interactions_fallback():
    p = _make_provider()
    # First call raises schema validation rejection (non-retryable)
    json_payload = '{"summary":"폴백 요약","key_claims":[],"entities":[],"relations":[]}'
    fallback_interaction = SimpleNamespace(output_text=json_payload)

    p.client.interactions.create.side_effect = [
        ValueError("Invalid schema argument"),
        fallback_interaction,
    ]

    doc = Document(id="doc1", title="Test", raw_text="Content", source_type="text")
    result = p.extract(doc)

    assert result.summary == "폴백 요약"
    assert p.client.interactions.create.call_count == 2


def test_summarize_search():
    p = _make_provider()
    mock_interaction = SimpleNamespace(output_text="[OpenSkill]은 유용한 도구입니다.")
    p.client.interactions.create.return_value = mock_interaction

    answer = p.summarize_search("OpenSkill이란?", "OpenSkill is great")
    assert answer == "[OpenSkill]은 유용한 도구입니다."
    assert p.client.interactions.create.call_args.kwargs["store"] is False


def test_render_detail():
    p = _make_provider()
    mock_interaction = SimpleNamespace(output_text="## 핵심 정리\n\n**OpenSkill**은 강력합니다.")
    p.client.interactions.create.return_value = mock_interaction

    doc = Document(id="doc1", title="Doc", raw_text="Some text", source_type="text")
    detail = p.render_detail(doc)

    assert "## 핵심 정리" in detail
    assert "**OpenSkill**" in detail


def test_classify_watch():
    p = _make_provider()
    mock_interaction = SimpleNamespace(output_text='{"watch":true,"interval_days":7,"reason":"주간 랭킹"}')
    p.client.interactions.create.return_value = mock_interaction

    doc = Document(id="doc1", title="Weekly Leaderboard", raw_text="Rankings table", source_type="text")
    res = p.classify_watch(doc)

    assert res["watch"] is True
    assert res["interval_days"] == 7
    assert res["reason"] == "주간 랭킹"


def test_research_grounding_annotations():
    p = _make_provider()
    annotation = SimpleNamespace(type="url_citation", title="OpenSkill Docs", url="https://example.com/docs")
    block = SimpleNamespace(type="text", annotations=[annotation])
    step = SimpleNamespace(type="model_output", content=[block])
    mock_interaction = SimpleNamespace(
        output_text="OpenSkill에 관한 심층 보고서입니다.",
        steps=[step],
    )
    p.client.interactions.create.return_value = mock_interaction

    res = p.research("OpenSkill", "Context about OpenSkill")
    assert res["report"] == "OpenSkill에 관한 심층 보고서입니다."
    assert len(res["sources"]) == 1
    assert res["sources"][0]["title"] == "OpenSkill Docs"
    assert res["sources"][0]["url"] == "https://example.com/docs"

    kwargs = p.client.interactions.create.call_args.kwargs
    assert kwargs["tools"] == [{"type": "google_search"}]


def test_judge_research():
    p = _make_provider()
    mock_interaction = SimpleNamespace(
        output_text='{"relevance":0.95,"quality":0.9,"same_subject":true,"interpretation":"오픈소스 스킬 도구","reason":"정확함"}'
    )
    p.client.interactions.create.return_value = mock_interaction

    judge = p.judge_research("OpenSkill", "Context", "Report")
    assert judge["relevance"] == 0.95
    assert judge["quality"] == 0.9
    assert judge["same_subject"] is True
    assert judge["interpretation"] == "오픈소스 스킬 도구"


def test_select_followups():
    p = _make_provider()
    mock_interaction = SimpleNamespace(output_text='{"follow":[0,2],"reason":"관련 깊은 문서"}')
    p.client.interactions.create.return_value = mock_interaction

    candidates = [
        {"url": "https://a.com", "anchor": "A"},
        {"url": "https://b.com", "anchor": "B"},
        {"url": "https://c.com", "anchor": "C"},
    ]
    selected = p.select_followups("Parent context", candidates)
    assert selected == [0, 2]


def test_judge_same_entity():
    p = _make_provider()
    p.client.interactions.create.return_value = SimpleNamespace(output_text="SAME")

    mc = gp.MergeCandidate(
        new_name="OpenSkill",
        new_type="Tool",
        cand_name="openskills",
        cand_type="Tool",
    )
    assert p.judge_same_entity(mc) is True

    p.client.interactions.create.return_value = SimpleNamespace(output_text="DIFFERENT")
    assert p.judge_same_entity(mc) is False


def test_embed():
    p = _make_provider()
    mock_resp = SimpleNamespace(embeddings=[SimpleNamespace(values=[0.1, 0.2, 0.3])])
    p.client.models.embed_content.return_value = mock_resp

    vec = p.embed("some text")
    assert vec == [0.1, 0.2, 0.3]
    p.client.models.embed_content.assert_called_once()


def test_build_generation_config_thinking_levels():
    p = _make_provider()

    p.effort = "high"
    cfg = p._build_generation_config()
    assert cfg["thinking_config"] == {"thinking_level": "HIGH"}

    p.effort = "low"
    cfg = p._build_generation_config()
    assert cfg["thinking_config"] == {"thinking_level": "LOW"}

    p.effort = "1024"
    cfg = p._build_generation_config()
    assert cfg["thinking_config"] == {"thinking_budget": 1024}

    p.effort = "0"
    cfg = p._build_generation_config()
    assert cfg["thinking_config"] == {"thinking_budget": 0}

    p.effort = "none"
    cfg = p._build_generation_config()
    assert cfg["thinking_config"] == {"thinking_budget": 0}

