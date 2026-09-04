"""VMware Explore 자막·Presentation PDF 복합 적재 테스트."""

from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from claire.config import Settings, get_settings
from claire.extract.prompts import doc_to_prompt
from claire.extract.provider import MockProvider
from claire.graphview import GRAPH_HTML, document_detail
from claire.ingest.fetchers.pdf import PdfExtractResult
from claire.ingest.fetchers.presentation_vmware_explore import (
    PresentationCandidate,
    PresentationDiscovery,
    PresentationExtract,
    PresentationFetchError,
    compose_video_presentations,
    discover_presentations,
    download_presentation,
    extract_presentation,
    rendered_session_is_ready,
    select_presentation_candidates,
    vmware_explore_video_id,
)
from claire.ingest.fetchers.video import fetch_video
from claire.ingest.fetchers.web import render_html_cdp
from claire.ingest.pipeline import ingest
from claire.ontology.base import Document, SourceAttachment
from claire.store import db as dbm
from claire.store.raw import load_artifact, raw_disk_usage, save_document_attachments
from claire.store.vectors import VectorStore

VIDEO_URL = "https://www.vmware.com/explore/video/6403820644112"
PDF_URL = "https://static.rainfocus.com/event/session/presrevpdf/APPB1222LV.pdf"
PDF_BYTES = b"%PDF-synthetic-presentation"


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
        biblio={},
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


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    return conn


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
        "claire.ingest.fetchers.presentation_vmware_explore.render_html_cdp",
        fake_renderer,
    )
    discovery = discover_presentations(VIDEO_URL, static_html="")
    assert discovery.status == "available"
    assert called == {"url": VIDEO_URL, "click_tab_label": "Presentation"}


def test_shared_renderer_clicks_exact_role_tab(monkeypatch):
    clicked = []

    class FakeLocator:
        def count(self):
            return 1

        def click(self, *, timeout):
            assert timeout == 1000
            clicked.append("Presentation")

    class FakePage:
        def wait_for_timeout(self, milliseconds):
            assert milliseconds == 0

        def get_by_role(self, role, *, name, exact):
            assert (role, name, exact) == ("tab", "Presentation", True)
            return FakeLocator()

    class FakeDynamicFetcher:
        @classmethod
        def fetch(cls, url, **kwargs):
            assert url == VIDEO_URL
            assert kwargs["headless"] is True
            kwargs["page_action"](FakePage())
            return SimpleNamespace(
                body=_rendered_html(with_presentation=True).encode("utf-8")
            )

    monkeypatch.setattr("scrapling.fetchers.DynamicFetcher", FakeDynamicFetcher)
    rendered = render_html_cdp(
        VIDEO_URL,
        wait_seconds=0,
        click_tab_label="Presentation",
        interaction_timeout_seconds=1,
        post_click_wait_seconds=0,
    )
    assert clicked == ["Presentation"]
    assert "presentation-details" in rendered


def test_shared_renderer_keeps_ready_dom_when_tab_is_absent(monkeypatch):
    class MissingLocator:
        def count(self):
            return 0

        def click(self, **_kwargs):
            pytest.fail("missing tab must not be clicked")

    class FakePage:
        def wait_for_timeout(self, _milliseconds):
            return None

        def get_by_role(self, role, *, name, exact):
            assert (role, name, exact) == ("tab", "Presentation", True)
            return MissingLocator()

    class FakeDynamicFetcher:
        @classmethod
        def fetch(cls, _url, **kwargs):
            kwargs["page_action"](FakePage())
            return SimpleNamespace(
                body=_rendered_html(with_presentation=False).encode("utf-8")
            )

    monkeypatch.setattr("scrapling.fetchers.DynamicFetcher", FakeDynamicFetcher)
    rendered = render_html_cdp(VIDEO_URL, click_tab_label="Presentation")
    assert rendered_session_is_ready(rendered, "6403820644112") is True


def test_shared_renderer_rejects_failed_visible_tab_interaction(monkeypatch):
    class BrokenLocator:
        def count(self):
            return 1

        def click(self, **_kwargs):
            raise RuntimeError("click failed")

    class FakePage:
        def wait_for_timeout(self, _milliseconds):
            return None

        def get_by_role(self, _role, *, name, exact):
            assert (name, exact) == ("Presentation", True)
            return BrokenLocator()

    class SwallowingDynamicFetcher:
        @classmethod
        def fetch(cls, _url, **kwargs):
            try:
                kwargs["page_action"](FakePage())
            except RuntimeError:
                pass
            return SimpleNamespace(
                body=_rendered_html(with_presentation=False).encode("utf-8")
            )

    monkeypatch.setattr(
        "scrapling.fetchers.DynamicFetcher", SwallowingDynamicFetcher
    )
    assert render_html_cdp(VIDEO_URL, click_tab_label="Presentation") == ""


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


def _public_resolver(*_args, **_kwargs):
    return [(2, 1, 6, "", ("8.8.8.8", 443))]


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
        resolver=_public_resolver,
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
            resolver=_public_resolver,
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
            resolver=_public_resolver,
        )


@pytest.mark.parametrize(
    ("url", "resolver", "message"),
    [
        ("http://static.rainfocus.com/file.pdf", _public_resolver, "HTTPS URL"),
        ("https://user:pass@static.rainfocus.com/file.pdf", _public_resolver, "user information"),
        ("https://evil.example/file.pdf", _public_resolver, "host is not allowed"),
        (
            PDF_URL,
            lambda *_args, **_kwargs: [(2, 1, 6, "", ("127.0.0.1", 443))],
            "non-public destination",
        ),
    ],
)
def test_download_rejects_unsafe_destination(url, resolver, message):
    with pytest.raises(PresentationFetchError, match=message):
        download_presentation(
            PresentationCandidate(url=url),
            Settings(_env_file=None),
            client_factory=_client_factory([]),
            resolver=resolver,
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
            resolver=_public_resolver,
        )


def test_extract_reuses_pdf_parser_without_dropping_appendix(monkeypatch):
    text = "Main slides\n\nAppendix A\nAppendix slides"
    result = PdfExtractResult(
        "Speaker Names", text, [], {}, None, [], {}, parser_requested="pypdf"
    )
    monkeypatch.setattr(
        "claire.ingest.fetchers.presentation_vmware_explore.extract_pdf_bytes",
        lambda *_args, **_kwargs: result,
    )
    extracted = extract_presentation(
        _attachment(),
        Settings(_env_file=None, pdf_max_extract_chars=1000),
    )
    assert "Appendix slides" in extracted.text
    assert extracted.extracted_title == "Speaker Names"


def test_compose_keeps_one_video_identity_and_both_source_boundaries():
    original = _video_doc()
    original_chars = original.meta["orig_chars"]
    extracted = _extract(text="Slide facts")
    doc = compose_video_presentations(original, [extracted])
    assert doc.source_type == "video"
    assert doc.canonical_url == VIDEO_URL
    assert doc.raw_text.index("[영상 자막]") < doc.raw_text.index("[발표자료 PDF")
    assert doc.raw_text.index("[발표자료 PDF") < doc.raw_text.index("[영상 설명]")
    assert "Caption body" in doc.raw_text
    assert "Slide facts" in doc.raw_text
    assert doc.meta["presentation_pdf"]["extracted_title"] == "Speaker Names"
    assert doc.meta["extra_sources"][0]["canonical_url"] == PDF_URL
    assert [item["kind"] for item in doc.meta["content_components"]] == [
        "transcript",
        "presentation_pdf",
    ]
    wrapper_chars = len(doc.raw_text) - original_chars - len(extracted.text)
    assert doc.meta["orig_chars"] == original_chars + extracted.orig_chars + wrapper_chars
    assert doc.meta["presentation_pdf"]["biblio"] == {}
    dumped = doc.model_dump()
    assert "attachments" not in dumped
    assert PDF_BYTES not in str(dumped).encode()


def test_compose_rejects_pdf_only_partial_bundle():
    doc = _video_doc()
    doc.meta["has_transcript"] = False
    with pytest.raises(PresentationFetchError, match="bundle_incomplete_media"):
        compose_video_presentations(doc, [_extract()])


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
        "claire.ingest.fetchers.video.discover_presentations",
        lambda _url: PresentationDiscovery(
            status="available", candidates=[PresentationCandidate(PDF_URL)]
        ),
    )
    monkeypatch.setattr(
        "claire.ingest.fetchers.video.download_presentation",
        lambda *_args, **_kwargs: _attachment(),
    )
    monkeypatch.setattr(
        "claire.ingest.fetchers.video.extract_presentation",
        lambda *_args, **_kwargs: _extract(text="PDF slide facts"),
    )
    monkeypatch.setattr(
        "claire.ingest.fetchers.video.get_transcript_provider",
        lambda *_args, **_kwargs: pytest.fail("STT must not run when CC exists"),
    )
    doc = fetch_video(
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
        "claire.ingest.fetchers.video.discover_presentations",
        lambda _url: PresentationDiscovery(
            status="available", candidates=[PresentationCandidate(PDF_URL)]
        ),
    )
    monkeypatch.setattr(
        "claire.ingest.fetchers.video.download_presentation",
        lambda *_args, **_kwargs: _attachment(),
    )
    monkeypatch.setattr(
        "claire.ingest.fetchers.video.extract_presentation",
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
    doc = fetch_video(
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
        "claire.ingest.fetchers.video.discover_presentations",
        lambda _url: PresentationDiscovery(
            status="available", candidates=[PresentationCandidate(PDF_URL)]
        ),
    )
    monkeypatch.setattr(
        "claire.ingest.fetchers.video.download_presentation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            PresentationFetchError("download_failed", "fixture failure")
        ),
    )
    with pytest.raises(PresentationFetchError, match="download_failed"):
        fetch_video(VIDEO_URL, settings=Settings(_env_file=None))


def test_pipeline_stores_attachment_before_document_and_reports_it(tmp_path: Path):
    conn = _db()
    doc = compose_video_presentations(_video_doc(), [_extract()])
    report = ingest(
        VIDEO_URL,
        conn=conn,
        provider=MockProvider(),
        vstore=VectorStore(conn, "brute"),
        fetch_fn=lambda _payload: doc,
        data_dir=tmp_path,
        source="test",
    )
    assert report.error is None
    assert report.presentation_pdfs == 1
    stored = dbm.get_document(conn, report.document_id)
    rel_path = stored.meta["presentation_pdf"]["artifact_path"]
    assert rel_path.startswith("raw/attachments/")
    assert (tmp_path / rel_path).read_bytes() == PDF_BYTES
    assert load_artifact(tmp_path, report.document_id) == doc.raw_text
    assert "🔤+📄 CC+PDF 포함" in report.telegram_summary()
    assert "📎" not in report.telegram_summary()
    assert "17자" in report.telegram_summary()
    assert "pypdf" in report.telegram_summary()


def test_attachment_storage_is_idempotent_and_rolls_back_partial_batch(tmp_path: Path):
    doc = compose_video_presentations(_video_doc(), [_extract()])
    first = save_document_attachments(tmp_path, doc)
    second = save_document_attachments(tmp_path, doc)
    assert first == second
    assert len(list((tmp_path / "raw" / "attachments").rglob("*.pdf"))) == 1

    invalid = _attachment(b"%PDF-invalid-second")
    invalid.content_sha256 = "0" * 64
    batch = Document(
        id="doc_batch",
        attachments=[_attachment(b"%PDF-first"), invalid],
    )
    with pytest.raises(ValueError, match="content hash mismatch"):
        save_document_attachments(tmp_path, batch)
    assert not list(
        (tmp_path / "raw" / "attachments" / "doc_batch").rglob("*.pdf")
    )


def test_pipeline_refuses_required_attachment_without_storage():
    conn = _db()
    doc = compose_video_presentations(_video_doc(), [_extract()])
    report = ingest(
        VIDEO_URL,
        conn=conn,
        provider=MockProvider(),
        vstore=VectorStore(conn, "brute"),
        fetch_fn=lambda _payload: doc,
        data_dir=None,
        source="test",
    )
    assert report.error.startswith("attachment_store_failed")
    assert dbm.counts(conn)["documents"] == 0


def test_direct_pdf_is_duplicate_of_bundled_extra_source(tmp_path: Path):
    conn = _db()
    video = compose_video_presentations(_video_doc(), [_extract()])
    first = ingest(
        VIDEO_URL,
        conn=conn,
        provider=MockProvider(),
        vstore=VectorStore(conn, "brute"),
        fetch_fn=lambda _payload: video,
        data_dir=tmp_path,
        source="test",
    )
    pdf = Document(
        url=PDF_URL,
        canonical_url=PDF_URL,
        title="Presentation",
        raw_text="Presentation body",
        source_type="pdf",
        content_hash="independent-pdf-hash",
    )
    second = ingest(
        PDF_URL,
        conn=conn,
        provider=MockProvider(),
        vstore=VectorStore(conn, "brute"),
        fetch_fn=lambda _payload: pdf,
        source="test",
    )
    assert second.duplicate is True
    assert second.document_id == first.document_id
    assert dbm.counts(conn)["documents"] == 1


def test_same_text_new_pdf_binary_preserves_version_history(tmp_path: Path):
    conn = _db()
    first_doc = compose_video_presentations(_video_doc(), [_extract()])
    first = ingest(
        VIDEO_URL,
        conn=conn,
        provider=MockProvider(),
        vstore=VectorStore(conn, "brute"),
        fetch_fn=lambda _payload: first_doc,
        data_dir=tmp_path,
        source="test",
    )
    first_stored = dbm.get_document(conn, first.document_id)
    first_meta = first_stored.meta["presentation_pdf"]

    changed_bytes = b"%PDF-new-binary-with-same-text"
    changed_doc = compose_video_presentations(
        _video_doc(), [_extract(changed_bytes)]
    )
    second = ingest(
        VIDEO_URL,
        conn=conn,
        provider=MockProvider(),
        vstore=VectorStore(conn, "brute"),
        fetch_fn=lambda _payload: changed_doc,
        data_dir=tmp_path,
        source="test",
    )
    assert second.document_id == first.document_id
    assert second.updated is True
    assert second.duplicate is False

    stored = dbm.get_document(conn, first.document_id)
    current = stored.meta["presentation_pdf"]
    assert current["content_sha256"] != first_meta["content_sha256"]
    assert stored.meta["presentation_history"][0]["content_sha256"] == first_meta["content_sha256"]
    assert (tmp_path / first_meta["artifact_path"]).exists()
    assert (tmp_path / current["artifact_path"]).exists()


def test_prompt_budget_preserves_transcript_and_pdf(monkeypatch):
    monkeypatch.setenv("CLAIRE_MERGED_EXTRACT_CHAR_BUDGET", "30")
    monkeypatch.setenv("CLAIRE_SLICING_STRATEGY", "strict")
    get_settings.cache_clear()
    raw = "prefix" + ("A" * 100) + ("B" * 100)
    doc = Document(
        title="Composite",
        raw_text=raw,
        source_type="video",
        meta={
            "extra_sources": [{"canonical_url": PDF_URL}],
            "content_components": [
                {"kind": "transcript", "start": 6, "end": 106},
                {"kind": "presentation_pdf", "start": 106, "end": 206},
            ],
        },
    )
    prompt = doc_to_prompt(doc)
    content = prompt.split("CONTENT:\n", 1)[1]
    assert content.count("A") >= 10
    assert content.count("B") >= 10
    assert len(content.replace("\n", "")) <= 30
    get_settings.cache_clear()


def test_prompt_invalid_component_metadata_falls_back_to_normal_slicing(monkeypatch):
    monkeypatch.setenv("CLAIRE_EXTRACT_CHAR_BUDGET", "12")
    monkeypatch.setenv("CLAIRE_SLICING_STRATEGY", "strict")
    get_settings.cache_clear()
    doc = Document(
        raw_text="abcdefghijklmnop",
        source_type="video",
        meta={"content_components": [{"kind": "bad"}, "not-a-dict"]},
    )
    content = doc_to_prompt(doc).split("CONTENT:\n", 1)[1]
    assert content == "abcdefghijkl"
    get_settings.cache_clear()


def test_graphview_exposes_presentation_metadata():
    conn = _db()
    doc = compose_video_presentations(_video_doc(), [_extract()])
    dbm.insert_document(conn, doc)
    detail = document_detail(conn, doc.id)
    assert detail["presentation_pdf"]["public_url"] == PDF_URL
    assert detail["presentation_pdfs"][0]["parser_used"] == "pypdf"
    assert "↗ Presentation PDF" in GRAPH_HTML
    assert "CC+PDF" in GRAPH_HTML
    assert "STT+PDF" in GRAPH_HTML
    assert "🔤+📄" in GRAPH_HTML
    assert "🎙️+📄" in GRAPH_HTML
    assert "📎 Presentation PDF" not in GRAPH_HTML


def test_graphview_doc_meta_html_renders_cc_and_stt_bundles():
    import json
    import subprocess
    import shutil

    if shutil.which("node") is None:
        pytest.skip("Node.js is not installed on the system")

    start = GRAPH_HTML.index("function docMetaHtml(dc){")
    end = GRAPH_HTML.index("function renderReader(dc){")
    fn = GRAPH_HTML[start:end]

    js = f"""
    function esc(s){{ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }}
    {fn}

    const ccDoc = {{
        url: '{VIDEO_URL}',
        is_stt: false,
        presentation_pdf: {{
            status: 'available',
            public_url: '{PDF_URL}',
            raw_chars: 25183,
            parser_used: 'pypdf',
            artifact_path: 'raw/attachments/APPB1222LV.pdf'
        }}
    }};
    const sttDoc = {{
        url: '{VIDEO_URL}',
        is_stt: true,
        presentation_pdf: {{
            status: 'available',
            public_url: '{PDF_URL}',
            raw_chars: 25183,
            parser_used: 'pypdf',
            artifact_path: 'raw/attachments/APPB1222LV.pdf'
        }}
    }};
    console.log(JSON.stringify({{
        cc: docMetaHtml(ccDoc),
        stt: docMetaHtml(sttDoc)
    }}));
    """
    res = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True)
    out = json.loads(res.stdout)

    # CC 검증: '🔤+📄 CC+PDF' 라벨, 클립 아이콘 제외, 자막 툴팁, 중복 STT 태그 부재
    assert "🔤+📄 CC+PDF (25,183자 · pypdf)" in out["cc"]
    assert "📎" not in out["cc"]
    assert "원본 PDF가 영상 자막과 함께 적재됨" in out["cc"]
    assert "stt-tag" not in out["cc"]

    # STT 검증: '🎙️+📄 STT+PDF' 라벨, 클립 아이콘 제외, STT 툴팁, 전사 열기 링크 포함, stt-tag 스타일링
    assert "🎙️+📄 STT+PDF (25,183자 · pypdf)" in out["stt"]
    assert "📎" not in out["stt"]
    assert "원본 PDF가 영상 음성 전사(STT)과 함께 적재됨" in out["stt"]
    assert "stt-tag" in out["stt"]
    assert "↗ 전사 열기" in out["stt"]
    assert "🎙️ STT" not in out["stt"]  # 중복 분리 태그 방지


def test_purge_removes_presentation_attachment(tmp_path: Path):
    conn = _db()
    doc = compose_video_presentations(_video_doc(), [_extract()])
    report = ingest(
        VIDEO_URL,
        conn=conn,
        provider=MockProvider(),
        vstore=VectorStore(conn, "brute"),
        fetch_fn=lambda _payload: doc,
        data_dir=tmp_path,
        source="test",
    )
    stored = dbm.get_document(conn, report.document_id)
    attachment_path = tmp_path / stored.meta["presentation_pdf"]["artifact_path"]
    assert attachment_path.exists()
    purged = dbm.purge_document_cascade(
        conn,
        data_dir=tmp_path,
        vault_dir=None,
        target_ids=[report.document_id],
        dry_run=False,
    )
    assert purged["disk_files_unlinked"] >= 2
    assert not attachment_path.exists()
