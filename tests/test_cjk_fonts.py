"""CJK (Korean) font support and Asciidoctor typography validation tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import pytest
from starlette.testclient import TestClient

from claire.api.server import create_app
from claire.config import Settings
from claire.graphview import GRAPH_HTML, render_graph_html, shared_html

OWNER_TOKEN = "owner-" + ("o" * 32)
READONLY_TOKEN = "readonly-" + ("r" * 32)


@dataclass
class StubSettings:
    db_file: Path
    data_dir: Path
    environment: str = "development"
    public_url: str = "http://127.0.0.1:8765"
    inject_token: str = OWNER_TOKEN
    readonly_token: str = READONLY_TOKEN
    cors_allowed_origins: str = ""
    anonymous_readonly: bool = True


class StubService:
    def __init__(self) -> None:
        self.provider = SimpleNamespace(name="stub")


def test_cjk_font_files_exist():
    """Verify that all required woff2 font files exist in static/fonts directory."""
    fonts_dir = Path(__file__).resolve().parent.parent / "src" / "claire" / "static" / "fonts"
    assert fonts_dir.is_dir(), f"Fonts directory not found: {fonts_dir}"

    expected_fonts = [
        "NotoSansKR-Regular.woff2",
        "NotoSansKR-Bold.woff2",
        "NotoSerifKR-Regular.woff2",
        "NotoSerifKR-Bold.woff2",
        "D2Coding.woff2",
        "D2CodingBold.woff2",
    ]

    for font_name in expected_fonts:
        font_path = fonts_dir / font_name
        assert font_path.is_file(), f"Font file missing: {font_name}"
        assert font_path.stat().st_size > 10000, f"Font file too small or corrupted: {font_name}"


def test_cjk_fonts_served_by_api(tmp_path: Path):
    """Verify that the ASGI server serves woff2 fonts with correct media type and caching headers."""
    db_file = tmp_path / "test.db"
    settings = StubSettings(
        db_file=db_file,
        data_dir=tmp_path / "data",
    )
    service = StubService()
    app = create_app(settings, service)
    with TestClient(app, base_url=settings.public_url) as client:
        # 1. Path-based route: /fonts/{filename}
        resp_sans = client.get("/fonts/NotoSansKR-Regular.woff2")
        assert resp_sans.status_code == 200
        assert "font/woff2" in resp_sans.headers.get("content-type", "")
        assert "immutable" in resp_sans.headers.get("cache-control", "")
        assert len(resp_sans.content) > 10000

        resp_serif = client.get("/fonts/NotoSerifKR-Regular.woff2")
        assert resp_serif.status_code == 200
        assert "font/woff2" in resp_serif.headers.get("content-type", "")

        resp_d2 = client.get("/fonts/D2Coding.woff2")
        assert resp_d2.status_code == 200
        assert "font/woff2" in resp_d2.headers.get("content-type", "")

        # 2. Query-based route: /font?p={filename}
        resp_q = client.get("/font?p=D2CodingBold.woff2")
        assert resp_q.status_code == 200
        assert "font/woff2" in resp_q.headers.get("content-type", "")

        # 3. Security guards: non-woff2 extension, traversal, non-existent
        assert client.get("/fonts/secret.txt").status_code == 404
        assert client.get("/fonts/../icons/favicon.ico").status_code == 404
        assert client.get("/font?p=../../etc/passwd").status_code == 404
        assert client.get("/fonts/NonExistentFont.woff2").status_code == 404


def test_graph_html_cjk_typography_and_font_faces(tmp_path: Path):
    """Verify that GRAPH_HTML contains Google Fonts integration, CJK font-faces and typography hierarchy."""
    settings = Settings(
        db_path=str(tmp_path / "test.db"),
        vault_dir=str(tmp_path / "vault"),
        data_dir=str(tmp_path / "data"),
    )
    html_content = render_graph_html(settings)

    # Google Fonts preconnect and stylesheet links
    assert "https://fonts.googleapis.com" in html_content
    assert "https://fonts.gstatic.com" in html_content
    assert "Noto+Sans+KR" in html_content
    assert "Noto+Serif+KR" in html_content

    # @font-face declarations
    assert "font-family:'Noto Sans KR'" in html_content or "font-family: 'Noto Sans KR'" in html_content
    assert "font-family:'Noto Serif KR'" in html_content or "font-family: 'Noto Serif KR'" in html_content
    assert "font-family:'D2Coding'" in html_content or "font-family: 'D2Coding'" in html_content
    assert "url('/fonts/NotoSansKR-Regular.woff2')" in html_content or "url(/fonts/NotoSansKR-Regular.woff2)" in html_content
    assert "url('/fonts/NotoSerifKR-Regular.woff2')" in html_content or "url(/fonts/NotoSerifKR-Regular.woff2)" in html_content
    assert "url('/fonts/D2Coding.woff2')" in html_content or "url(/fonts/D2Coding.woff2)" in html_content

    # Typography rules
    assert "Noto Sans KR" in html_content
    assert "Noto Serif KR" in html_content
    assert "D2Coding" in html_content
    assert "word-break:keep-all" in html_content or "word-break: keep-all" in html_content
    assert "overflow-wrap:break-word" in html_content or "overflow-wrap: break-word" in html_content


def test_shared_html_cjk_typography_and_font_faces():
    """Verify that shared_html standalone page contains Google Fonts integration, CJK font-faces and typography hierarchy."""
    doc = {
        "id": "doc-1",
        "title": "CJK 타이포그래피 테스트",
        "url": "https://example.com/cjk",
        "source_type": "article",
        "summary": "한국어 글꼴 렌더링",
        "detail": "[quote, 저자]\n____\n인용문 본문\n____\n\n[source,python]\n----\nprint('hello')\n----",
        "detail_format": "adoc",
        "detail_html": '<div class="quoteblock"><blockquote><p>인용문 본문</p></blockquote></div>',
    }
    html_out = shared_html(doc)

    # Google Fonts preconnect and stylesheet links
    assert "https://fonts.googleapis.com" in html_out
    assert "https://fonts.gstatic.com" in html_out
    assert "Noto+Sans+KR" in html_out
    assert "Noto+Serif+KR" in html_out

    # @font-face declarations
    assert "Noto Sans KR" in html_out
    assert "Noto Serif KR" in html_out
    assert "D2Coding" in html_out
    assert "/fonts/NotoSansKR-Regular.woff2" in html_out
    assert "/fonts/NotoSerifKR-Regular.woff2" in html_out
    assert "/fonts/D2Coding.woff2" in html_out

    # CJK word breaking & font family assignments
    assert "word-break:keep-all" in html_out or "word-break: keep-all" in html_out
    assert "overflow-wrap:break-word" in html_out or "overflow-wrap: break-word" in html_out
