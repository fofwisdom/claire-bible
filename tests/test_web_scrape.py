"""웹 fetcher fallback 체인 + thin-guard + Discourse 어댑터 (네트워크 없이)."""

from __future__ import annotations

import pytest

import claire.ingest.fetchers.web as web
from claire.ingest.fetchers.base import FetchError
from claire.ingest.fetchers.discourse import _topic_json_url, _strip_html


# --- Discourse 어댑터 ---

def test_topic_json_url():
    assert _topic_json_url("https://discuss.pytorch.kr/t/foo-bar/10427") == \
        "https://discuss.pytorch.kr/t/foo-bar/10427.json"
    # 글 번호가 뒤에 붙어도 토픽 base 로
    assert _topic_json_url("https://discuss.pytorch.kr/t/foo/10427/3?u=x") == \
        "https://discuss.pytorch.kr/t/foo/10427.json"
    # 토픽 형태 아니면 None
    assert _topic_json_url("https://example.com/article/123") is None
    assert _topic_json_url("https://github.com/a/b") is None


def test_strip_html():
    text, links = _strip_html('<p>Hello <a href="https://x.com/p">x</a> world</p>')
    assert "Hello" in text and "world" in text
    assert links == ["https://x.com/p"]


def test_strip_html_removes_image_meta_noise():
    # Discourse lightbox 가 남기는 이미지 치수/용량 메타는 제거, 본문은 보존
    html = (
        '<p>본문 시작 내용</p>'
        '<div class="lightbox-wrapper"><a class="lightbox" href="https://i/big.jpg">'
        '<img src="https://i/thumb.jpg" alt="cap">'
        '<div class="meta"><span class="filename">cap.jpg</span>'
        '<span class="informations">1536×1024 229 KB</span></div></a></div>'
        '<p>본문 끝 내용</p>'
    )
    text, _ = _strip_html(html)
    assert "본문 시작 내용" in text and "본문 끝 내용" in text
    assert "1536×1024" not in text
    assert "229 KB" not in text
    assert "cap.jpg" not in text  # 메타 노드 제거


def test_strip_html_multiroot_safe():
    # <p>...</p><div>... 처럼 루트가 여러 개여도 전부 파싱
    html = "<p>첫째 문단</p><div>둘째 블록</div>"
    text, _ = _strip_html(html)
    assert "첫째 문단" in text and "둘째 블록" in text


# --- fetch_web fallback 체인 + thin-guard ---

def _patch_chain(monkeypatch, *, static=("T", "", [], {}, None, None, []),
                 discourse=None, scrapling=(None, "", [], {}, []),
                 cdp=(None, "", [], {}, [])):
    monkeypatch.setattr(web, "_fetch_static", lambda url: static)
    monkeypatch.setattr(web, "_fetch_scrapling", lambda url: scrapling)
    monkeypatch.setattr(web, "_fetch_cdp", lambda url: cdp)
    import claire.ingest.fetchers.discourse as disc
    monkeypatch.setattr(disc, "try_discourse", lambda url: discourse)


def test_static_rich_used_directly(monkeypatch):
    body = "x" * 500
    _patch_chain(monkeypatch, static=("Title", body, ["https://a"], {}, None, None, []))
    doc = web.fetch_web("https://example.com/post")
    assert doc.title == "Title"
    assert len(doc.raw_text) == 500
    assert doc.meta["fetch_via"] == "static"


def test_canonical_uses_effective_url(monkeypatch):
    """dedup 핵심: 서버 redirect 이후 도달 URL(resp.url)로 canonical 을 잡는다.

    입력은 추적파라미터·www 가 붙은 형태지만 effective 가 깨끗한 정규형이면
    그쪽으로 수렴 → 다른 입구로 들어온 같은 글이 같은 canonical 을 얻는다.
    """
    body = "x" * 500
    eff = "https://example.com/real-article"
    _patch_chain(monkeypatch, static=("T", body, [], {}, None, eff, []))
    doc = web.fetch_web("https://www.example.com/r?utm_source=tw&fbclid=123")
    assert doc.canonical_url == "https://example.com/real-article"
    assert doc.meta["effective_url"] == eff


def test_canonical_falls_back_to_input_when_no_effective(monkeypatch):
    """static 이 effective 를 못 주면(scrapling 으로 본문 확보 등) 입력 url 로 폴백."""
    rich = "y" * 400
    _patch_chain(monkeypatch,
                 static=(None, "", [], {}, "http 403", None, []),
                 scrapling=("스텔스 제목", rich, [], {}, []))
    doc = web.fetch_web("https://openai.com/index/foo/")
    assert doc.meta["fetch_via"] == "scrapling"
    # 입력 url 폴백이되 canonicalize 는 적용(끝슬래시 제거).
    assert doc.canonical_url == "https://openai.com/index/foo"


def test_discourse_escalation_when_static_thin(monkeypatch):
    rich = "본문 " * 200  # >300
    _patch_chain(monkeypatch,
                 static=("제목만", "짧음", [], {}, None, None, []),
                 discourse=("디스코스 제목", rich, ["https://ref"]))
    doc = web.fetch_web("https://discuss.pytorch.kr/t/foo/1")
    assert doc.meta["fetch_via"] == "discourse"
    assert len(doc.raw_text) >= 300
    assert "ref" in doc.meta["links"][0]


def test_cdp_escalation_when_others_thin(monkeypatch):
    rich = "y" * 400
    _patch_chain(monkeypatch,
                 static=("t", "tiny", [], {}, None, None, []),
                 discourse=None,
                 cdp=("cdp title", rich, [], {}, []))
    doc = web.fetch_web("https://js-only.example/app")
    assert doc.meta["fetch_via"] == "cdp"
    assert len(doc.raw_text) == 400


def test_thin_guard_raises_when_all_fail(monkeypatch):
    # static 빈약 + discourse 없음 + cdp 빈 → 실패(제목만 적재 방지)
    _patch_chain(monkeypatch,
                 static=("제목만 있음", "73자정도의짧은본문", [], {}, None, None, []),
                 discourse=None, cdp=(None, "", [], {}, []))
    with pytest.raises(FetchError):
        web.fetch_web("https://discuss.pytorch.kr/t/thin/999")


def test_min_content_threshold_separates_measured_data():
    # 측정 근거: 정상 최소 ~1296, 실패 73~111 → 300 이 그 사이
    assert 111 < web.MIN_CONTENT < 1296


# --- 본문 이미지 수집(휴리스틱) ---

def test_extract_images_keeps_content_drops_noise():
    """다이어그램/스크린샷 같은 콘텐츠 이미지는 남기고 로고·아이콘·아바타·1x1 추적픽셀은 거른다."""
    html = (
        '<html><head>'
        '<meta property="og:image" content="https://cdn.example.com/hero.png">'
        '</head><body>'
        '<nav><img src="https://cdn.example.com/logo.svg" alt="logo"></nav>'
        '<article>'
        '<p>본문</p>'
        '<figure><img src="/img/diagram.png" alt="아키텍처 다이어그램">'
        '<figcaption>그림 1. 전체 구조</figcaption></figure>'
        '<img src="https://cdn.example.com/avatar.jpg" alt="user avatar">'
        '<img src="https://t.example.com/pixel.gif" width="1" height="1">'
        '<img src="https://cdn.example.com/icon-share.png" alt="share">'
        '<img src="https://cdn.example.com/screenshot.png" alt="실행 화면" '
        'width="800" height="600">'
        '</article></body></html>'
    )
    _, _, _, _, _, images = web._extract_html(html, base_url="https://example.com/post")
    urls = [im["url"] for im in images]
    # 상대경로는 절대경로화 + 콘텐츠 이미지 보존
    assert "https://example.com/img/diagram.png" in urls
    assert "https://cdn.example.com/screenshot.png" in urls
    assert "https://cdn.example.com/hero.png" in urls          # og:image 대표
    # 잡음 제거
    assert all("logo" not in u for u in urls)        # nav 안(제거) + logo 패턴
    assert all("avatar" not in u for u in urls)
    assert all("pixel" not in u for u in urls)       # 1x1 추적픽셀
    assert all("icon" not in u for u in urls)
    # 캡션/alt 보존(LLM 배치 단서)
    diagram = next(im for im in images if im["url"].endswith("diagram.png"))
    assert diagram["alt"] == "아키텍처 다이어그램"
    assert "전체 구조" in diagram["caption"]


def test_fetch_web_carries_images_into_meta(monkeypatch):
    body = "본문 " * 200
    imgs = [{"url": "https://x/d.png", "alt": "도식", "caption": ""}]
    _patch_chain(monkeypatch, static=("T", body, [], {}, None, None, imgs))
    doc = web.fetch_web("https://example.com/post")
    assert doc.meta["images"] == imgs
