"""Gemini Interactions API provider unit tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

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


def test_extract_interactions_raises_without_fallback():
    p = _make_provider()
    p.client.interactions.create.side_effect = ValueError("Invalid schema argument")

    doc = Document(id="doc1", title="Test", raw_text="Content", source_type="text")
    with pytest.raises(ValueError, match="Invalid schema argument"):
        p.extract(doc)

    assert p.client.interactions.create.call_count == 1


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


def test_judge_gate():
    p = _make_provider()
    mock_interaction = SimpleNamespace(
        output_text='{"relevance":0.95,"quality":0.9,"same_subject":true,"interpretation":"오픈소스 스킬 도구","reason":"정확함"}'
    )
    p.client.interactions.create.return_value = mock_interaction

    judge = p.judge_gate("OpenSkill", "Context", "Report")
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
    """The interactions API takes ``thinking_level`` (lowercase) directly in ``generation_config``; it has no ``thinking_config`` and no budget."""
    p = _make_provider()

    p.effort = "high"
    cfg = p._build_generation_config()
    assert cfg["thinking_level"] == "high"
    assert "thinking_config" not in cfg

    p.effort = "low"
    cfg = p._build_generation_config()
    assert cfg["thinking_level"] == "low"

    for budget in ("1024", "0", "none", "-1"):
        p.effort = budget
        with pytest.raises(ValueError, match="no thinking budget"):
            p._build_generation_config()


def test_generation_config_matches_interactions_sdk_type():
    """The keys of the config are fields of the SDK type ``GenerationConfigParam`` of the interactions API (the server rejects other keys with 400)."""
    from typing import get_type_hints

    from google.genai._gaos.types.interactions.generationconfig import GenerationConfigParam

    p = _make_provider()
    p.effort = "medium"
    cfg = p._build_generation_config()
    assert set(cfg) <= set(get_type_hints(GenerationConfigParam))


def test_build_generation_config_rejects_unsupported_effort_without_fallback():
    p = _make_provider()

    p.model = "gemini-4-argon"
    p.effort = "max"
    with pytest.raises(ValueError, match="Unsupported effort"):
        p._build_generation_config()

    p.effort = "none"
    with pytest.raises(ValueError, match="Unsupported effort"):
        p._build_generation_config()

    p.model = "gemini-3-pro-preview"
    p.effort = "medium"
    with pytest.raises(ValueError, match="Unsupported effort"):
        p._build_generation_config()


def test_fixed_effort_gemini_model_resolves_api_model_and_thinking_level():
    p = _make_provider()
    p.model = "gemini-3.8-flash-medium"
    p.effort = "high"  # Should be overridden by the effort encoded in the model name.

    assert p._api_model() == "gemini-3.8-flash"
    assert p._embedded_effort() == "medium"
    cfg = p._build_generation_config()
    assert cfg["thinking_level"] == "medium"

    json_payload = '{"summary":"요약","key_claims":[],"entities":[],"relations":[]}'
    p.client.interactions.create.return_value = SimpleNamespace(output_text=json_payload)
    doc = Document(id="doc1", title="Test", raw_text="Content", source_type="text")
    res = p.extract(doc)
    assert res.model == "gemini-3.8-flash-medium"
    kwargs = p.client.interactions.create.call_args.kwargs
    assert kwargs["model"] == "gemini-3.8-flash"
    assert kwargs["generation_config"]["thinking_level"] == "medium"

    # Task calls without explicit override also use embedded effort and stripped API model.
    p.client.interactions.create.reset_mock()
    p.client.interactions.create.return_value = SimpleNamespace(output_text="SAME")
    mc = gp.MergeCandidate(new_name="A", new_type="Tool", cand_name="B", cand_type="Tool")
    assert p.judge_same_entity(mc) is True
    judge_kwargs = p.client.interactions.create.call_args.kwargs
    assert judge_kwargs["model"] == "gemini-3.8-flash"
    assert judge_kwargs["generation_config"]["thinking_level"] == "medium"


def test_embed_v2_prompt_routing_and_config_contract():
    """Verify gemini-embedding-2 encodes task instruction in prompt and omits unsupported EmbedContentConfig fields."""
    p = _make_provider()
    p.embed_model = "gemini-embedding-2"
    p.client.models.embed_content.return_value = SimpleNamespace(embeddings=[SimpleNamespace(values=[0.5, 0.5])])

    vec_doc = p.embed("Vector body text", task_type="RETRIEVAL_DOCUMENT", title="Spec")
    assert vec_doc == [0.5, 0.5]
    doc_kwargs = p.client.models.embed_content.call_args.kwargs
    assert doc_kwargs["model"] == "gemini-embedding-2"
    assert doc_kwargs["contents"] == "title: Spec | text: Vector body text"
    assert doc_kwargs["config"].output_dimensionality == 3072
    assert doc_kwargs["config"].task_type is None
    assert doc_kwargs["config"].title is None

    p.client.models.embed_content.reset_mock()
    p.embed("search query", task_type="RETRIEVAL_QUERY")
    q_kwargs = p.client.models.embed_content.call_args.kwargs
    assert q_kwargs["contents"] == "task: search result | query: search query"
    assert q_kwargs["config"].output_dimensionality == 3072
    assert q_kwargs["config"].task_type is None


def test_embed_v1_param_normalizes_non_3072_and_fails_closed_on_retired():
    """Verify gemini-embedding-001 passes SDK task_type/title, normalizes reduced dim, and retired models fail closed."""
    p = _make_provider()
    p.embed_model = "gemini-embedding-001"
    p.client.models.embed_content.return_value = SimpleNamespace(embeddings=[SimpleNamespace(values=[3.0, 4.0])])

    vec = p.embed("Doc text", task_type="RETRIEVAL_DOCUMENT", title="Alpha", output_dimensionality=768)
    assert pytest.approx(vec, rel=1e-6) == [0.6, 0.8]
    kwargs = p.client.models.embed_content.call_args.kwargs
    assert kwargs["model"] == "gemini-embedding-001"
    assert kwargs["contents"] == "Doc text"
    assert kwargs["config"].task_type == "RETRIEVAL_DOCUMENT"
    assert kwargs["config"].title == "Alpha"
    assert kwargs["config"].output_dimensionality == 768

    for retired in ("gemini-embedding-2-preview", "text-embedding-004", "gemini-embedding-exp"):
        with pytest.raises(ValueError, match="Unsupported"):
            p.embed("text", model=retired)


def test_embed_many_and_batch_job_use_sdk_content_and_batch_contract():
    """Verify embed_many wraps items in Content and embed_batch_job calls client.batches.create_embeddings."""
    from google.genai import types

    p = _make_provider()
    p.embed_model = "gemini-embedding-2"
    p.client.models.embed_content.return_value = SimpleNamespace(embeddings=[SimpleNamespace(values=[1.0, 0.0]), SimpleNamespace(values=[0.0, 1.0])])

    vecs = p.embed_many(["First", "Second"], task_type="RETRIEVAL_DOCUMENT", titles=["T1", "T2"])
    assert vecs == [[1.0, 0.0], [0.0, 1.0]]
    many_kwargs = p.client.models.embed_content.call_args.kwargs
    assert all(isinstance(c, types.Content) for c in many_kwargs["contents"])
    assert many_kwargs["contents"][0].parts[0].text == "title: T1 | text: First"
    assert many_kwargs["config"].output_dimensionality == 3072

    pending = SimpleNamespace(name="batches/job-1", state="JOB_STATE_PENDING", dest=None)
    done = SimpleNamespace(
        name="batches/job-1",
        state="JOB_STATE_SUCCEEDED",
        dest=SimpleNamespace(inlined_embed_content_responses=[SimpleNamespace(error=None, response=SimpleNamespace(embedding=SimpleNamespace(values=[0.2, 0.4]))), SimpleNamespace(error=None, response=SimpleNamespace(embedding=SimpleNamespace(values=[0.6, 0.8])))]),
    )
    p.client.batches.create_embeddings.return_value = pending
    p.client.batches.get.return_value = done

    b_vecs = p.embed_batch_job(["Item A", "Item B"], task_type="RETRIEVAL_DOCUMENT", titles=["A", "B"], poll_interval_s=0.0)
    assert b_vecs == [[0.2, 0.4], [0.6, 0.8]]
    create_kwargs = p.client.batches.create_embeddings.call_args.kwargs
    assert create_kwargs["model"] == "gemini-embedding-2"
    assert isinstance(create_kwargs["src"], types.EmbeddingsBatchJobSource)
    assert create_kwargs["src"].inlined_requests.config.output_dimensionality == 3072

