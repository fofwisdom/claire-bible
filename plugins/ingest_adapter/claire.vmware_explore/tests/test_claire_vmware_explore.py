"""VMware Explore 자막·Presentation PDF 복합 적재 테스트 (project plugin claire.vmware_explore)."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from claire.core.config import Settings
from claire.ontology.base import Document, SourceAttachment
from claire.plugin_sdk import PresentationExtract, PresentationFetchError
from claire.plugin_sdk.testing import load_plugin

FOLDER = Path(__file__).resolve().parents[1]
MOD = "claire_plugins.ingest_adapter.claire_vmware_explore.adapter"
_NAMES = (
    "PresentationCandidate",
    "PresentationDiscovery",
    "discover_presentations",
    "download_presentation",
    "fetch_vmware_explore",
    "rendered_session_is_ready",
    "select_presentation_candidates",
    "vmware_explore_video_id",
)

VIDEO_URL = "https://www.vmware.com/explore/video/6403820644112"
PDF_URL = "https://static.rainfocus.com/event/session/presrevpdf/APPB1222LV.pdf"
PDF_BYTES = b"%PDF-synthetic-presentation"


@pytest.fixture(autouse=True)
def plugin(monkeypatch):
    loaded = load_plugin(FOLDER)
    loaded.install(monkeypatch)
    module = loaded.module("adapter")
    for name in _NAMES:
        monkeypatch.setitem(globals(), name, getattr(module, name))
    return loaded


def _rendered_html(*, with_presentation: bool) -> str:
    presentation = ""
    if with_presentation:
        presentation = f"""
        <div class="presentation-details">
          <h2>Presentation PDF</h2>
          <a href="{PDF_URL}">Download</a>
        </div>
        """
    return f"""
    <html><body data-video-id="6403820644112">
      <h1>VMware Explore Session</h1>
      <nav><span>Details</span><span>Speakers</span><span>Share</span></nav>
      <p>{'session details ' * 20}</p>
      {presentation}
    </body></html>
    """


def _attachment(data: bytes = PDF_BYTES) -> SourceAttachment:
    return SourceAttachment(
        kind="presentation_pdf",
        source_url=PDF_URL,
        canonical_url=PDF_URL,
        filename="APPB1222LV.pdf",
        media_type="application/pdf",
        byte_length=len(data),
        content_sha256=hashlib.sha256(data).hexdigest(),
        content=data,
    )


def _extract(data: bytes = PDF_BYTES, text: str = "Presentation body") -> PresentationExtract:
    attachment = _attachment(data)
    return PresentationExtract(
        attachment=attachment,
        text=text,
        extracted_title="Speaker Names",
        links=[],
        parser_requested="pypdf",
        parser_used="pypdf",
        parser_fallback=False,
        parser_fallback_reason=None,
        orig_chars=len(text),
        raw_chars=len(text),
        truncated=False,
    )


def _video_doc() -> Document:
    body = (
        "발표자/채널: VMware\n\n"
        "[영상 자막]\nCaption body\n\n"
        "[영상 설명]\nSession description"
    )
    return Document(
        url=VIDEO_URL,
        canonical_url=VIDEO_URL,
        title="VMware Explore Session",
        raw_text=body,
        source_type="video",
        content_hash="video-only",
        meta={
            "has_transcript": True,
            "transcript_source": "manual_caption",
            "caption_language": "en-US",
            "caption_content_hash": "caption-hash",
            "orig_chars": len(body),
            "raw_chars": len(body),
        },
    )


def test_vmware_target_and_exact_presentation_dom_selection():
    assert vmware_explore_video_id(VIDEO_URL) == "6403820644112"
    assert vmware_explore_video_id("http://www.vmware.com/explore/video/1") is None
    assert vmware_explore_video_id("https://evil.example/explore/video/1") is None

    html = _rendered_html(with_presentation=True) + """
    <div><h2>Presentation PDF</h2><a href="https://evil.example/noise.pdf">noise</a></div>
    <div class="presentation-details"><h2>Other file</h2><a href="https://evil.example/other.pdf">other</a></div>
    """
    selected = select_presentation_candidates(html, base_url=VIDEO_URL)
    assert [item.url for item in selected] == [PDF_URL]


def test_discovery_requires_rendered_ready_state_before_absent():
    static = discover_presentations(
        VIDEO_URL,
        static_html=_rendered_html(with_presentation=True),
        render_fn=lambda _url: pytest.fail("static candidate must skip rendering"),
    )
    assert static.status == "available"
    assert static.rendered is False

    available = discover_presentations(
        VIDEO_URL,
        static_html="<html><body>shell</body></html>",
        render_fn=lambda _url: _rendered_html(with_presentation=True),
    )
    assert available.status == "available"
    assert available.rendered is True

    absent_html = _rendered_html(with_presentation=False)
    assert rendered_session_is_ready(absent_html, "6403820644112") is True
    absent = discover_presentations(
        VIDEO_URL,
        static_html="",
        render_fn=lambda _url: absent_html,
    )
    assert absent.status == "absent"

    failed = discover_presentations(
        VIDEO_URL,
        static_html="",
        render_fn=lambda _url: "<html><body>loading</body></html>",
    )
    assert failed.status == "discovery_failed"
    assert failed.error == "session_not_ready"


def test_discovery_requests_presentation_tab_interaction(monkeypatch):
    called = {}

    def fake_renderer(url, **kwargs):
        called["url"] = url
        called.update(kwargs)
        return _rendered_html(with_presentation=True)

    monkeypatch.setattr(
        MOD + ".render_html_cdp",
        fake_renderer,
    )
    discovery = discover_presentations(VIDEO_URL, static_html="")
    assert discovery.status == "available"
    assert called == {"url": VIDEO_URL, "click_tab_label": "Presentation"}


class _FakeResponse:
    def __init__(self, *, status=200, headers=None, chunks=None):
        self.status_code = status
        self.headers = headers or {}
        self._chunks = chunks or []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def iter_bytes(self):
        yield from self._chunks


class _FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def stream(self, method, _url):
        assert method == "GET"
        return self.responses.pop(0)


def _client_factory(responses):
    return lambda **_kwargs: _FakeClient(responses)




def test_download_validates_pdf_and_discards_query_credentials():
    candidate = PresentationCandidate(url=PDF_URL + "?token=secret")
    response = _FakeResponse(
        headers={
            "content-type": "application/pdf; charset=binary",
            "content-length": str(len(PDF_BYTES)),
        },
        chunks=[PDF_BYTES[:8], PDF_BYTES[8:]],
    )
    attachment = download_presentation(
        candidate,
        Settings(_env_file=None),
        client_factory=_client_factory([response]),
    )
    assert attachment.content == PDF_BYTES
    assert attachment.source_url == PDF_URL
    assert "secret" not in str(attachment)
    assert "secret" not in str(attachment.model_dump())


@pytest.mark.parametrize(
    "response",
    [
        _FakeResponse(headers={"content-type": "text/html"}, chunks=[PDF_BYTES]),
        _FakeResponse(headers={"content-type": "application/pdf"}, chunks=[b"not-pdf"]),
        _FakeResponse(
            headers={"content-type": "application/pdf", "content-length": "999"},
            chunks=[PDF_BYTES],
        ),
    ],
)
def test_download_rejects_invalid_or_oversized_responses(response):
    settings = Settings(_env_file=None, presentation_pdf_max_bytes=32)
    with pytest.raises(PresentationFetchError) as exc_info:
        download_presentation(
            PresentationCandidate(url=PDF_URL + "?token=must-not-leak"),
            settings,
            client_factory=_client_factory([response]),
            )
    assert "must-not-leak" not in str(exc_info.value)


def test_download_revalidates_redirect_host():
    response = _FakeResponse(
        status=302,
        headers={"location": "https://127.0.0.1/private.pdf"},
    )
    with pytest.raises(PresentationFetchError, match="host is not allowed"):
        download_presentation(
            PresentationCandidate(url=PDF_URL),
            Settings(_env_file=None),
            client_factory=_client_factory([response]),
            )


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("http://static.rainfocus.com/file.pdf", "HTTPS URL"),
        ("https://user:pass@static.rainfocus.com/file.pdf", "user information"),
        ("https://evil.example/file.pdf", "host is not allowed"),
    ],
)
def test_download_rejects_unsafe_destination(url, message):
    with pytest.raises(PresentationFetchError, match=message):
        download_presentation(
            PresentationCandidate(url=url),
            Settings(_env_file=None),
            client_factory=_client_factory([]),
        )


def test_download_enforces_streaming_limit_without_content_length():
    response = _FakeResponse(
        headers={"content-type": "application/pdf"},
        chunks=[b"%PDF-", b"x" * 32],
    )
    with pytest.raises(PresentationFetchError, match="exceeds size limit"):
        download_presentation(
            PresentationCandidate(url=PDF_URL),
            Settings(_env_file=None, presentation_pdf_max_bytes=16),
            client_factory=_client_factory([response]),
            )


def test_fetch_video_uses_caption_and_presentation_without_stt(monkeypatch):
    valid_vtt = (
        "WEBVTT\n\n00:00.000 --> 00:02.000\n"
        "Publisher caption contains enough spoken words"
    )

    class FakeYDL:
        def __init__(self, _options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download=False):
            assert download is False
            return {
                "title": "VMware Session",
                "subtitles": {"en-US": [{"data": valid_vtt, "ext": "vtt"}]},
            }

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYDL))
    monkeypatch.setattr(
        MOD + ".discover_presentations",
        lambda _url: PresentationDiscovery(
            status="available", candidates=[PresentationCandidate(PDF_URL)]
        ),
    )
    monkeypatch.setattr(
        MOD + ".download_presentation",
        lambda *_args, **_kwargs: _attachment(),
    )
    monkeypatch.setattr(
        MOD + ".extract_presentation",
        lambda *_args, **_kwargs: _extract(text="PDF slide facts"),
    )
    monkeypatch.setattr(
        "claire.ingest.fetchers.video.get_transcript_provider",
        lambda *_args, **_kwargs: pytest.fail("STT must not run when CC exists"),
    )
    doc = fetch_vmware_explore(
        VIDEO_URL,
        settings=Settings(
            _env_file=None,
            enable_video_transcription=True,
            ytdlp_extractor_args="",
            preferred_languages="en",
        ),
    )
    assert doc.meta["transcript_source"] == "manual_caption"
    assert doc.meta["presentation_pdf"]["status"] == "available"
    assert "Publisher caption contains enough spoken words" in doc.raw_text
    assert "PDF slide facts" in doc.raw_text


def test_fetch_video_combines_stt_and_presentation_when_caption_absent(monkeypatch):
    from claire.extract.transcript.base import TranscriptResult, TranscriptSegment

    class CaptionlessYDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download=False):
            assert download is False
            return {"title": "VMware Session", "duration": 5.0}

        def download(self, _urls):
            outtmpl = Path(self.options["outtmpl"])
            (outtmpl.parent / "audio.mp4").write_bytes(b"synthetic audio")

    class FakeSTT:
        def transcribe(self, _audio_path, **_kwargs):
            return TranscriptResult(
                full_text="Generated STT transcript",
                segments=[
                    TranscriptSegment(
                        start_sec=0.0,
                        end_sec=5.0,
                        text="Generated STT transcript",
                    )
                ],
                duration_sec=5.0,
                provider="mock",
            )

    monkeypatch.setitem(
        sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=CaptionlessYDL)
    )
    monkeypatch.setattr(
        MOD + ".discover_presentations",
        lambda _url: PresentationDiscovery(
            status="available", candidates=[PresentationCandidate(PDF_URL)]
        ),
    )
    monkeypatch.setattr(
        MOD + ".download_presentation",
        lambda *_args, **_kwargs: _attachment(),
    )
    monkeypatch.setattr(
        MOD + ".extract_presentation",
        lambda *_args, **_kwargs: _extract(text="PDF slide facts"),
    )
    monkeypatch.setattr(
        "claire.ingest.fetchers.video.find_ffmpeg_executable",
        lambda _configured: "/bin/ffmpeg",
    )
    monkeypatch.setattr(
        "claire.ingest.fetchers.video.get_transcript_provider",
        lambda *_args, **_kwargs: FakeSTT(),
    )
    doc = fetch_vmware_explore(
        VIDEO_URL,
        settings=Settings(
            _env_file=None,
            enable_video_transcription=True,
            ytdlp_extractor_args="",
        ),
    )
    assert doc.meta["transcript_source"] == "stt"
    assert doc.meta["presentation_pdf"]["status"] == "available"
    assert "[영상 음성 전사 (STT)]" in doc.raw_text
    assert "Generated STT transcript" in doc.raw_text
    assert "PDF slide facts" in doc.raw_text


def test_advertised_presentation_failure_stops_before_media(monkeypatch):
    class NeverCalledYDL:
        def __init__(self, _options):
            pytest.fail("media path must not start after required PDF failure")

    monkeypatch.setitem(
        sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=NeverCalledYDL)
    )
    monkeypatch.setattr(
        MOD + ".discover_presentations",
        lambda _url: PresentationDiscovery(
            status="available", candidates=[PresentationCandidate(PDF_URL)]
        ),
    )
    monkeypatch.setattr(
        MOD + ".download_presentation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            PresentationFetchError("download_failed", "fixture failure")
        ),
    )
    with pytest.raises(PresentationFetchError, match="download_failed"):
        fetch_vmware_explore(VIDEO_URL, settings=Settings(_env_file=None))




def test_router_sends_vmware_explore_to_the_plugin(plugin):
    from claire.ingest.router import classify

    assert classify(VIDEO_URL) == "vmware_explore"
    assert classify("https://www.vmware.com/explore/catalog") != "vmware_explore"


def test_brightcove_player_url(plugin):
    url = plugin.module("adapter").brightcove_player_url("6403821753112")
    assert url == "https://players.brightcove.net/6164421911001/default_default/index.html?videoId=6403821753112"
