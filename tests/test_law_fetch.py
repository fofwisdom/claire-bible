"""law.go.kr 어댑터 단위 테스트."""

from __future__ import annotations

import httpx
import pytest

from claire.ingest.fetchers.law import (
    _extract_from_reader,
    _is_law_kr_url,
    try_law_kr,
)


def test_is_law_kr_url():
    assert _is_law_kr_url("https://www.law.go.kr/법령/인공지능기본법") is True
    assert _is_law_kr_url("http://law.go.kr/LSW/lsInfoP.do?lsiSeq=123") is True
    assert _is_law_kr_url("https://subdomain.law.go.kr/path") is True
    assert _is_law_kr_url("https://example.com/law.go.kr") is False
    assert _is_law_kr_url("https://google.com") is False
    assert _is_law_kr_url("") is False


def test_extract_from_reader_cleans_noise_and_extracts_title():
    html_content = """
    <html>
    <head><title>국가법령정보센터</title></head>
    <body>
      <input type="hidden" id="lsNm" value="인공지능 발전과 신뢰 기반 조성 등에 관한 기본법" />
      <div id="contentBody">
        <ul class="cont_icon">
          <li><a>판례</a></li>
          <li><a>연혁</a></li>
          <li><a>규제</a></li>
        </ul>
        <div class="byl_pop">팝업 노이즈</div>
        <div class="fileSaveLayer">다운로드 레이어</div>
        <h2>인공지능 발전과 신뢰 기반 조성 등에 관한 기본법</h2>
        <p>제1조(목적) 이 법은 인공지능의 건전한 발전과 신뢰 기반 조성에 필요한 기본적인 사항을 규정함을 목적으로 한다.</p>
        <p>제2조(정의) 이 법에서 사용하는 용어의 뜻은 다음과 같다. 1. "인공지능"이란 인간이 가진 지적 능력을 전자적 방법으로 구현한 것을 말한다.</p>
      </div>
    </body>
    </html>
    """
    res = _extract_from_reader(html_content, base_url="https://www.law.go.kr")
    assert res is not None
    title, text, links, anchors, images = res
    assert title == "인공지능 발전과 신뢰 기반 조성 등에 관한 기본법"
    assert "판례" not in text
    assert "연혁" not in text
    assert "팝업 노이즈" not in text
    assert "다운로드 레이어" not in text
    assert "제1조(목적)" in text
    assert "제2조(정의)" in text


def test_try_law_kr_non_law_returns_none():
    assert try_law_kr("https://example.com/article/123") is None


def test_try_law_kr_outer_iframe_resolution(monkeypatch):
    outer_html = """
    <html>
    <head><title>인공지능기본법</title></head>
    <body>
      <iframe src="/LSW//lsInfoP.do?lsiSeq=282791&chrClsCd=010202&efYd=20260721"></iframe>
    </body>
    </html>
    """
    reader_html = """
    <html>
    <body>
      <input type="hidden" id="lsNm" value="인공지능 발전과 신뢰 기반 조성 등에 관한 기본법" />
      <div id="contentBody">
        <h2>인공지능 발전과 신뢰 기반 조성 등에 관한 기본법</h2>
        <p>제1조(목적) 이 법은 인공지능의 발전을 촉진하고 신뢰를 구축함을 목적으로 한다. """ + ("상세조문내용 " * 20) + """</p>
      </div>
    </body>
    </html>
    """

    def mock_get(client, url, *args, **kwargs):
        url_str = str(url)
        if "lsInfoR.do" in url_str:
            return httpx.Response(200, text=reader_html, request=httpx.Request("GET", url_str))
        if "법령" in url_str:
            return httpx.Response(200, text=outer_html, request=httpx.Request("GET", url_str))
        return httpx.Response(404, request=httpx.Request("GET", url_str))

    monkeypatch.setattr(httpx.Client, "get", mock_get)

    res = try_law_kr("https://www.law.go.kr/법령/인공지능기본법")
    assert res is not None
    title, text, links, anchors, images = res
    assert title == "인공지능 발전과 신뢰 기반 조성 등에 관한 기본법"
    assert "제1조(목적)" in text
    assert len(text) >= 100


def test_try_law_kr_popup_url_conversion(monkeypatch):
    reader_html = """
    <html>
    <body>
      <input type="hidden" id="admRulNm" value="개인정보의 안전성 확보조치 기준" />
      <div id="contentBody">
        <h2>개인정보의 안전성 확보조치 기준</h2>
        <p>제1조(목적) 이 기준은 개인정보의 안전성 확보를 목적으로 한다. """ + ("상세내용 " * 30) + """</p>
      </div>
    </body>
    </html>
    """

    def mock_get(client, url, *args, **kwargs):
        url_str = str(url)
        if "admRulInfoR.do" in url_str:
            return httpx.Response(200, text=reader_html, request=httpx.Request("GET", url_str))
        return httpx.Response(404, request=httpx.Request("GET", url_str))

    monkeypatch.setattr(httpx.Client, "get", mock_get)

    res = try_law_kr("https://www.law.go.kr/LSW/admRulInfoP.do?admRulSeq=2100000281400")
    assert res is not None
    title, text, links, anchors, images = res
    assert title == "개인정보의 안전성 확보조치 기준"
    assert "안전성 확보를 목적으로 한다" in text


def test_try_law_kr_js_redirect_handling(monkeypatch):
    redirect_html = """
    <html>
    <head>
      <script>location.href="https://www.law.go.kr/법령/지능정보화기본법";</script>
    </head>
    </html>
    """
    outer_html = """
    <html>
    <head><title>지능정보화기본법</title></head>
    <body>
      <iframe src="/LSW//lsInfoP.do?lsiSeq=268535"></iframe>
    </body>
    </html>
    """
    reader_html = """
    <html>
    <body>
      <input type="hidden" id="lsNm" value="지능정보화 기본법" />
      <div>
        <p>제1조(목적) 지능정보사회의 구현을 목적으로 한다. """ + ("상세조문 " * 25) + """</p>
      </div>
    </body>
    </html>
    """

    def mock_get(client, url, *args, **kwargs):
        url_str = str(url)
        if url_str.startswith("http://www.law.go.kr"):
            return httpx.Response(200, text=redirect_html, request=httpx.Request("GET", url_str))
        if "lsInfoR.do" in url_str:
            return httpx.Response(200, text=reader_html, request=httpx.Request("GET", url_str))
        if "지능정보화기본법" in url_str:
            return httpx.Response(200, text=outer_html, request=httpx.Request("GET", url_str))
        return httpx.Response(404, request=httpx.Request("GET", url_str))

    monkeypatch.setattr(httpx.Client, "get", mock_get)

    res = try_law_kr("http://www.law.go.kr/법령/지능정보화기본법")
    assert res is not None
    title, text, links, anchors, images = res
    assert title == "지능정보화 기본법"
    assert "지능정보사회의 구현" in text
