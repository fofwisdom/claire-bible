"""x.com fetcher — URL 파싱 + 트윗 JSON → Document 빌드 (네트워크 없이)."""

from __future__ import annotations

import pytest

from claire.ingest.fetchers.base import FetchError
from claire.ingest.fetchers.xcom import (
    _build_document,
    _make_title,
    parse_status,
)

# --- URL 파싱 ---

def test_parse_status_basic():
    assert parse_status("https://x.com/jack/status/20") == ("jack", "20")
    assert parse_status("https://x.com/jack/status/20?s=20&t=ab") == ("jack", "20")
    assert parse_status("https://twitter.com/Interior/status/46344042414") == (
        "Interior", "46344042414")


def test_parse_status_reserved_path():
    # /i/web/status/... 의 'i','web' 은 핸들이 아니라 예약 경로 → screen=None
    assert parse_status("https://x.com/i/web/status/20") == (None, "20")
    assert parse_status("https://x.com/i/status/20") == (None, "20")


def test_parse_status_no_status():
    # 프로필·검색 등 status id 없음
    assert parse_status("https://x.com/jack") == (None, None)
    assert parse_status("https://x.com/search?q=ai") == (None, None)


# --- 제목 합성 ---

def test_make_title_author_plus_first_line():
    title = _make_title("jack (@jack)", "just setting up my twttr", {})
    assert title == "jack (@jack): just setting up my twttr"


def test_make_title_long_body_truncated():
    body = "가" * 200
    title = _make_title("@u", body, {})
    assert title.endswith("…")
    assert len(title) <= 100


def test_make_title_skips_url_only_first_line():
    # 첫 줄이 URL·핸들뿐이면 본문 전체를 평탄화해 요약으로 사용
    body = "@someone\nhttps://t.co/x\n실제 의미 있는 본문 내용"
    title = _make_title("작성자", body, {})
    assert "실제 의미 있는 본문" in title


def test_make_title_fallback_when_empty_body():
    assert _make_title("@u", "", {}) == "@u 의 트윗"
    assert _make_title("", "", {}) == "x.com 트윗"


# --- Document 빌드 ---

def _tweet(**over):
    base = {
        "text": "AI 안전성에 대한 생각",
        "author": {"name": "홍길동", "screen_name": "gildong"},
        "created_timestamp": 1142974214,
        "lang": "ko",
        "likes": 10,
        "retweets": 3,
        "url": "https://twitter.com/gildong/status/99",
    }
    base.update(over)
    return base


def test_build_document_basic():
    d = _build_document("https://x.com/gildong/status/99", _tweet())
    assert d.source_type == "xcom"
    assert d.partial is False
    assert d.title == "홍길동 (@gildong): AI 안전성에 대한 생각"
    assert d.author == "홍길동 (@gildong)"
    assert d.raw_text == "AI 안전성에 대한 생각"
    assert d.lang == "ko"
    assert d.published_at.startswith("2006-03-21")
    assert d.meta["screen_name"] == "gildong"
    assert d.meta["stats"]["likes"] == 10


def test_build_document_merges_quote():
    tw = _tweet(quote={"author": {"screen_name": "orig"}, "text": "원본 주장"})
    d = _build_document("https://x.com/gildong/status/99", tw)
    assert "원본 주장" in d.raw_text
    assert "@orig" in d.raw_text


def test_build_document_marks_reply():
    tw = _tweet(replying_to="someone")
    d = _build_document("https://x.com/gildong/status/99", tw)
    assert "@someone" in d.raw_text


def test_build_document_includes_image_alt():
    tw = _tweet(media={"photos": [{"altText": "그래프 스크린샷"}]})
    d = _build_document("https://x.com/gildong/status/99", tw)
    assert "그래프 스크린샷" in d.raw_text


def test_build_document_empty_body_raises():
    tw = _tweet(text="", media={})
    with pytest.raises(FetchError):
        _build_document("https://x.com/gildong/status/99", tw)
