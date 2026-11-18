"""Discourse 어댑터 (project plugin claire.discourse), 네트워크 없이."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from claire.plugin_sdk.testing import load_plugin

FOLDER = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def plugin(monkeypatch):
    loaded = load_plugin(FOLDER)
    module = loaded.module("adapter")
    for name in ("_strip_html", "_topic_json_url", "try_discourse"):
        monkeypatch.setitem(globals(), name, getattr(module, name))
    return loaded


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




def test_try_discourse_reads_the_topic_json(plugin, monkeypatch):
    data = {"title": "Topic", "post_stream": {"posts": [{"cooked": "<p>" + "Body text. " * 40 + '<a href="https://ref.example/a">r</a></p>', "username": "a"}]}}
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json=data)

    plugin.mock_network(monkeypatch, handler)
    title, text, links = try_discourse("https://forum.example/t/topic/12/3")
    assert seen == ["https://forum.example/t/topic/12.json"]
    assert title == "Topic" and "Body text." in text and links == ["https://ref.example/a"]
    assert try_discourse("https://forum.example/about") is None
