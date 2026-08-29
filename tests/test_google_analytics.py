"""Google Analytics 4 (GA4) 연동 및 분석 추적 검증."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from claire.api import security
from claire.config import Settings
from claire.graphview import GRAPH_HTML, render_ga_tag, render_graph_html, shared_html
from claire.store import db as dbm


def test_ga_measurement_id_config_validation():
    """GA 측정 ID 포맷 유효성 검사."""
    # 유효한 포맷들
    s1 = Settings(CLAIRE_GA_MEASUREMENT_ID="G-ABC123456")
    assert s1.effective_ga_measurement_id == "G-ABC123456"

    s2 = Settings(CLAIRE_GA_MEASUREMENT_ID="GTM-XYZ789")
    assert s2.effective_ga_measurement_id == "GTM-XYZ789"

    s3 = Settings(CLAIRE_GA_MEASUREMENT_ID="UA-12345-1")
    assert s3.effective_ga_measurement_id == "UA-12345-1"

    # 빈 값 또는 공백
    s_empty = Settings(CLAIRE_GA_MEASUREMENT_ID="   ")
    assert s_empty.effective_ga_measurement_id == ""

    # 유효하지 않은 특수문자나 인젝션 시도 -> ValueError
    with pytest.raises(ValueError, match="CLAIRE_GA_MEASUREMENT_ID"):
        Settings(CLAIRE_GA_MEASUREMENT_ID="G-123<script>alert(1)</script>")

    with pytest.raises(ValueError, match="CLAIRE_GA_MEASUREMENT_ID"):
        Settings(CLAIRE_GA_MEASUREMENT_ID="G-123 456")


def test_render_ga_tag_helper():
    """render_ga_tag 스니펫 생성, 쿠키 격리 및 URL 정제 검증."""
    # 비어있을 때
    assert render_ga_tag("") == ""
    assert render_ga_tag("   ") == ""
    assert render_ga_tag("<invalid>") == ""

    # 유효한 ID 설정 시 (기본)
    tag = render_ga_tag("G-TEST1234")
    assert "https://www.googletagmanager.com/gtag/js?id=G-TEST1234" in tag
    assert "gtag(\"config\", \"G-TEST1234\"" in tag
    # 쿼리스트링/토큰 유출 방지를 위한 page_location 정제 검증
    assert "page_location: window.location.origin + window.location.pathname" in tag
    assert "cookie_domain: window.location.hostname" in tag
    assert "cookie_flags: \"SameSite=Lax;Secure\"" in tag

    # 문서 ID 지정 시
    tag_doc = render_ga_tag("G-TEST1234", doc_id="doc-42")
    assert "page_location: window.location.origin + '/p/doc-42'" in tag_doc


def test_render_graph_html_with_and_without_ga():
    """메인 UI HTML 렌더링 시 GA 태그 주입 여부 검증."""
    # GA 미설정 시
    s_no_ga = SimpleNamespace(
        effective_github_repository="fofwisdom/claire-bible",
        effective_source_base_url="https://github.com/fofwisdom/claire-bible",
        effective_ga_measurement_id="",
    )
    html_no_ga = render_graph_html(s_no_ga)
    assert "<!-- __GA_TAG__ -->" not in html_no_ga
    assert "googletagmanager.com" not in html_no_ga

    # GA 설정 시
    s_ga = SimpleNamespace(
        effective_github_repository="fofwisdom/claire-bible",
        effective_source_base_url="https://github.com/fofwisdom/claire-bible",
        effective_ga_measurement_id="G-TRACK99",
    )
    html_ga = render_graph_html(s_ga)
    assert "<!-- __GA_TAG__ -->" not in html_ga
    assert "https://www.googletagmanager.com/gtag/js?id=G-TRACK99" in html_ga
    assert "G-TRACK99" in html_ga


def test_shared_html_with_and_without_ga():
    """공유 문서 읽기 페이지 HTML 렌더링 시 GA 태그 주입 여부 검증."""
    doc = {"id": "doc-test-1", "title": "공유 테스트 문서"}

    # GA 미설정 시
    s_no_ga = SimpleNamespace(
        effective_ga_measurement_id="",
    )
    html_no_ga = shared_html(doc, s_no_ga)
    assert "<!-- __GA_TAG__ -->" not in html_no_ga
    assert "googletagmanager.com" not in html_no_ga
    assert "공유 테스트 문서" in html_no_ga

    # GA 설정 시
    s_ga = SimpleNamespace(
        effective_ga_measurement_id="G-SHARED42",
    )
    html_ga = shared_html(doc, s_ga)
    assert "<!-- __GA_TAG__ -->" not in html_ga
    assert "https://www.googletagmanager.com/gtag/js?id=G-SHARED42" in html_ga
    assert "G-SHARED42" in html_ga
    assert "select_content" in html_ga
    assert "page_location: window.location.origin + '/p/doc-test-1'" in html_ga


def test_graph_html_contains_search_and_share_event_handlers():
    """메인 UI JS 코드에 search, share, page_view 이벤트 핸들러가 포함되어 있는지 검증."""
    assert "window.gtag('event', 'search'" in GRAPH_HTML
    assert "window.gtag('event', 'share'" in GRAPH_HTML
    assert "window.gtag('event', 'page_view'" in GRAPH_HTML
    assert "window.gtag('event', 'select_content'" in GRAPH_HTML
    assert "document.title = dc.title + ' — Claire Bible'" in GRAPH_HTML


def test_dynamic_csp_policy_headers():
    """GA 설정 유무에 따른 Content-Security-Policy 헤더 동적 생성 검증."""
    # 1. GA 미설정 시: strict 'self'
    csp_default = security._build_content_security_policy("")
    assert "https://www.googletagmanager.com" not in csp_default
    assert "connect-src 'self';" in csp_default

    # 2. GA 설정 시: googletagmanager, google-analytics 및 doubleclick 허용
    csp_ga = security._build_content_security_policy("G-SAMPLE123")
    assert "https://www.googletagmanager.com" in csp_ga
    assert "https://*.google-analytics.com" in csp_ga
    assert "https://google-analytics.com" in csp_ga
    assert "https://*.analytics.google.com" in csp_ga
    assert "https://analytics.google.com" in csp_ga
    assert "https://*.googletagmanager.com" in csp_ga
    assert "https://stats.g.doubleclick.net" in csp_ga
    assert "https://*.doubleclick.net" in csp_ga


@pytest.mark.asyncio
async def test_wrapped_web_app_csp_response_header(tmp_path: Path):
    """실제 ASGI 미들웨어 체인 응답 헤더에 동적 CSP 가 정상 전달되는지 검증."""
    async def _dummy_endpoint(_request: Request) -> PlainTextResponse:
        return PlainTextResponse("OK")

    inner_app = Starlette(routes=[Route("/", _dummy_endpoint, methods=["GET"])])

    # GA 설정된 settings
    s = SimpleNamespace(
        environment="development",
        public_url="http://192.0.2.10:8766",
        cors_allowed_origins="",
        inject_token="owner-" + "o" * 32,
        readonly_token="",
        anonymous_readonly=True,
        db_file=tmp_path / "claire.db",
        ga_measurement_id="G-MIDDLEWARE1",
    )

    app = security.wrap_web_app(inner_app, s)

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [
            (b"host", b"192.0.2.10:8766"),
        ],
    }

    received_headers: list[tuple[bytes, bytes]] = []

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        if message["type"] == "http.response.start":
            received_headers.extend(message.get("headers", []))

    await app(scope, receive, send)

    headers_dict = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in received_headers}
    csp = headers_dict.get("content-security-policy", "")
    assert "https://www.googletagmanager.com" in csp
    assert "https://*.google-analytics.com" in csp


def test_doc_share_token_reuse():
    """공유 링크 생성 시 동일 문서에 대해 기존 활성 토큰을 재사용하는지 검증."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)

    # 1. 문서 등록
    conn.execute(
        "INSERT INTO documents(id, url, title, fetched_at) VALUES (?,?,?,?)",
        ("doc-1", "https://example.com/1", "Test Doc 1", time.time()),
    )
    conn.commit()

    # 2. 첫 번째 공유 링크 발급
    token1 = dbm.create_doc_share(conn, "doc-1")
    assert dbm.plausible_share_token(token1)

    # 3. 동일 문서에 대해 다시 발급 -> 동일 토큰 반환 (재사용)
    token2 = dbm.create_doc_share(conn, "doc-1")
    assert token2 == token1

    # 4. 다른 문서 발급 -> 새 토큰 반환
    conn.execute(
        "INSERT INTO documents(id, url, title, fetched_at) VALUES (?,?,?,?)",
        ("doc-2", "https://example.com/2", "Test Doc 2", time.time()),
    )
    conn.commit()
    token_doc2 = dbm.create_doc_share(conn, "doc-2")
    assert token_doc2 != token1

    # 5. 만료된 토큰이 있는 경우 -> 새 토큰 발급
    # 강제로 token1 만료시킴
    conn.execute(
        "UPDATE doc_shares SET expires_at = ? WHERE token = ?",
        (time.time() - 100, token1),
    )
    conn.commit()
    token3 = dbm.create_doc_share(conn, "doc-1")
    assert token3 != token1

    # 6. reuse_existing=False 명시 시 -> 새 토큰 발급
    token4 = dbm.create_doc_share(conn, "doc-1", reuse_existing=False)
    assert token4 != token3

    conn.close()
