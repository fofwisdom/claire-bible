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

def _patch_chain(monkeypatch, *, static=("T", "", [], {}, None),
                 discourse=None, scrapling=(None, "", [], {}), stealth=(None, "")):
    monkeypatch.setattr(web, "_fetch_static", lambda url: static)
    monkeypatch.setattr(web, "_fetch_scrapling", lambda url: scrapling)
    monkeypatch.setattr(web, "_fetch_stealth", lambda url: stealth)
    import claire.ingest.fetchers.discourse as disc
    monkeypatch.setattr(disc, "try_discourse", lambda url: discourse)


def test_static_rich_used_directly(monkeypatch):
    body = "x" * 500
    _patch_chain(monkeypatch, static=("Title", body, ["https://a"], {}, None))
    doc = web.fetch_web("https://example.com/post")
    assert doc.title == "Title"
    assert len(doc.raw_text) == 500
    assert doc.meta["fetch_via"] == "static"


def test_discourse_escalation_when_static_thin(monkeypatch):
    rich = "본문 " * 200  # >300
    _patch_chain(monkeypatch,
                 static=("제목만", "짧음", [], {}, None),
                 discourse=("디스코스 제목", rich, ["https://ref"]))
    doc = web.fetch_web("https://discuss.pytorch.kr/t/foo/1")
    assert doc.meta["fetch_via"] == "discourse"
    assert len(doc.raw_text) >= 300
    assert "ref" in doc.meta["links"][0]


def test_stealth_escalation_when_others_thin(monkeypatch):
    rich = "y" * 400
    _patch_chain(monkeypatch,
                 static=("t", "tiny", [], {}, None),
                 discourse=None,
                 stealth=("stealth title", rich))
    doc = web.fetch_web("https://js-only.example/app")
    assert doc.meta["fetch_via"] == "stealth"
    assert len(doc.raw_text) == 400


def test_thin_guard_raises_when_all_fail(monkeypatch):
    # static 빈약 + discourse 없음 + stealth 빈 → 실패(제목만 적재 방지)
    _patch_chain(monkeypatch,
                 static=("제목만 있음", "73자정도의짧은본문", [], {}, None),
                 discourse=None, stealth=(None, ""))
    with pytest.raises(FetchError):
        web.fetch_web("https://discuss.pytorch.kr/t/thin/999")


def test_min_content_threshold_separates_measured_data():
    # 측정 근거: 정상 최소 ~1296, 실패 73~111 → 300 이 그 사이
    assert 111 < web.MIN_CONTENT < 1296
