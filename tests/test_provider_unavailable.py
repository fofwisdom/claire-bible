"""운영 환경에서는 쓸 수 없는 프로바이더를 mock 으로 대체하지 않는다 (PRINCIPLES C2)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from claire.config import ProviderUnavailableError, Settings
from claire.extract.provider import MockProvider, UnavailableProvider, get_provider
from claire.extract.transcript.factory import UnavailableTranscriptProvider, get_transcript_provider
from claire.extract.transcript.mock_stt import MockTranscriptProvider
from claire.extract.vision.factory import UnavailableVisionProvider, get_vision_provider
from claire.extract.vision.mock_vision import MockVisionProvider


def _settings(environment: str, **kwargs) -> Settings:
    return Settings(environment=environment, gemini_api_key="", **kwargs)


@pytest.mark.parametrize("provider", ["gemini", "openai"])
def test_llm_falls_back_to_mock_only_in_development(provider):
    dev = _settings("development", provider=provider)
    assert dev.effective_provider == "mock"
    assert isinstance(get_provider(dev), MockProvider)

    prod = _settings("production", provider=provider)
    assert prod.effective_provider == "unavailable"
    assert isinstance(get_provider(prod), UnavailableProvider)


def test_openai_without_implementation_names_the_gap():
    s = _settings("production", provider="openai")
    assert s.effective_provider == "unavailable"
    assert "구현이 없다" in (s.provider_unavailable_reason("llm") or "")


def test_unavailable_llm_fails_on_call_with_reason():
    prov = get_provider(_settings("production", provider="gemini"))
    assert prov.name == "unavailable"
    with pytest.raises(ProviderUnavailableError, match="GEMINI_API_KEY"):
        prov.extract(None, "")
    with pytest.raises(ProviderUnavailableError):
        prov.embed(["text"])


def test_missing_agy_binary_is_unavailable_in_production():
    with patch("claire.config.find_agy_executable", return_value=None):
        s = _settings("production", provider="antigravity")
        assert s.effective_provider == "unavailable"
        assert "agy" in (s.provider_unavailable_reason("llm") or "")


def test_explicit_mock_is_allowed_in_production():
    s = _settings("production", provider="mock")
    assert s.effective_provider == "mock"
    assert s.provider_unavailable_reason("llm") is None


def test_stt_without_key_fails_in_production():
    dev = _settings("development", enable_video_transcription=True, stt_provider="gemini")
    assert isinstance(get_transcript_provider(dev), MockTranscriptProvider)

    prod = _settings("production", enable_video_transcription=True, stt_provider="gemini")
    assert prod.effective_stt_provider == "unavailable"
    stt = get_transcript_provider(prod)
    assert isinstance(stt, UnavailableTranscriptProvider)
    with pytest.raises(ProviderUnavailableError, match="STT"):
        stt.transcribe("audio.m4a")


@pytest.mark.parametrize("vlm_provider", ["gemini", "unknown-vlm"])
def test_vlm_unavailable_fails_in_production(vlm_provider):
    dev = _settings("development", enable_vlm_transcription=True, vlm_provider=vlm_provider)
    assert isinstance(get_vision_provider(dev), MockVisionProvider)

    prod = _settings("production", enable_vlm_transcription=True, vlm_provider=vlm_provider)
    vlm = get_vision_provider(prod)
    assert isinstance(vlm, UnavailableVisionProvider)
    with pytest.raises(ProviderUnavailableError, match="VLM"):
        vlm.transcribe_images(["https://example.com/a.png"])


def test_disabled_features_report_no_unavailable_reason():
    s = _settings("production", enable_video_transcription=False, enable_vlm_transcription=False)
    assert s.provider_unavailable_reason("stt") is None
    assert s.provider_unavailable_reason("vlm") is None


def test_antigravity_embedding_does_not_fake_vectors_in_production():
    from claire.extract.antigravity_provider import AntigravityProvider

    dev = AntigravityProvider(_settings("development", provider="antigravity"))
    assert len(dev.embed("text")) == dev.EMBED_DIM

    prod = AntigravityProvider(_settings("production", provider="antigravity"))
    with pytest.raises(ProviderUnavailableError, match="embedding"):
        prod.embed("text")
