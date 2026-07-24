"""share.google 등 JS 리다이렉트 타겟 추출 (네트워크 없이)."""

from __future__ import annotations

from claire.ingest.fetchers.redirect import _extract_target, _is_share_host


def test_is_share_host():
    assert _is_share_host("share.google")
    assert _is_share_host("share.example.com")
    assert not _is_share_host("discuss.pytorch.kr")
    assert not _is_share_host("github.com")


def test_extract_target_prefers_canonical():
    html = (
        '<link rel="canonical" href="https://discuss.pytorch.kr/t/foo/10">'
        '<a href="https://play.google.com/x">app</a>'
    )
    assert _extract_target(html) == "https://discuss.pytorch.kr/t/foo/10"


def test_extract_target_first_external_when_no_canonical():
    # google 인프라/스토어 도메인은 건너뛰고 첫 실제 외부 URL
    html = (
        'blah https://www.google.com/search?q=x '
        'https://play.google.com/store '
        'https://discuss.pytorch.kr/t/bar/20 '
        'https://apps.apple.com/app'
    )
    assert _extract_target(html) == "https://discuss.pytorch.kr/t/bar/20"


def test_extract_target_none_when_only_infra():
    html = "https://www.google.com/a https://gstatic.com/b"
    assert _extract_target(html) is None


def test_extract_target_strips_trailing_punct():
    html = 'see "https://example.com/page".'
    assert _extract_target(html) == "https://example.com/page"
