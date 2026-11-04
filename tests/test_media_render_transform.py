import pytest
from types import SimpleNamespace

from claire.render.media_transform import (
    extract_media_key,
    media_url,
    transform_media_urls_in_html,
)
from claire.store.secrets import SecretService


def test_extract_media_key():
    assert extract_media_key("images/diagram.png") == "diagram.png"
    assert extract_media_key("/images/diagram.png") == "diagram.png"
    assert extract_media_key("/image?p=images/sub/diagram.png") == "sub/diagram.png"
    assert extract_media_key("p/images/slide1.webp") == "slide1.webp"
    assert extract_media_key("media/opaque_hash.png") == "opaque_hash.png"

    # Path traversal and external URLs ignored
    assert extract_media_key("../evil.png") is None
    assert extract_media_key("images/../../evil.png") is None
    assert extract_media_key("https://cdn.example.com/images/ext.png") is None
    assert extract_media_key("data:image/png;base64,xxxx") is None


def test_media_url_resolution_with_and_without_media_host():
    sec = SecretService(b"t" * 32)
    roles_with_media = SimpleNamespace(scheme="https", app="claire.example.com", media="media.example.com")
    roles_app_only = SimpleNamespace(scheme="http", app="127.0.0.1:8766", media=None)

    # 1. With media host - public
    url_pub = media_url("images/hero.jpg", roles=roles_with_media, is_public=True)
    assert url_pub == "https://media.example.com/media/hero.jpg"

    # 2. With media host - private
    url_priv = media_url(
        "images/secret.png",
        roles=roles_with_media,
        is_public=False,
        account_id=5,
        theme_id=10,
        visibility_epoch=2,
        secret_service=sec,
    )
    assert url_priv.startswith("https://media.example.com/media/secret.png?")
    assert "account_id=5" in url_priv
    assert "theme_id=10" in url_priv
    assert "visibility_epoch=2" in url_priv
    assert "token=" in url_priv

    # 3. Without media host (fallback to app host) - public
    url_app_pub = media_url("images/hero.jpg", roles=roles_app_only, is_public=True)
    assert url_app_pub == "/image?p=images/hero.jpg"

    # 4. Without media host - private
    url_app_priv = media_url(
        "images/secret.png",
        roles=roles_app_only,
        is_public=False,
        account_id=5,
        theme_id=10,
        visibility_epoch=2,
        secret_service=sec,
    )
    assert url_app_priv.startswith("/image?p=images/secret.png&")
    assert "token=" in url_app_priv


def test_transform_media_urls_in_html():
    roles = SimpleNamespace(scheme="https", app="claire.example.com", media="media.example.com")
    raw_html = """
    <div class="content">
      <img src="images/chart.png" alt="Chart">
      <img src="/image?p=images/photo.jpg" class="responsive">
      <img src="https://external.com/logo.png" alt="Ext">
      <img src="data:image/svg+xml;base64,PHN2Zz4=" alt="Inline">
      <section data-background-image="images/slide_bg.webp">
        <p style="background: url('images/bg_pattern.png')">Text</p>
      </section>
      <video src="images/clip.mp4" controls></video>
    </div>
    """

    transformed = transform_media_urls_in_html(raw_html, roles=roles, is_public=True)

    # Internal images rewritten to media host
    assert 'src="https://media.example.com/media/chart.png"' in transformed
    assert 'src="https://media.example.com/media/photo.jpg"' in transformed
    assert 'data-background-image="https://media.example.com/media/slide_bg.webp"' in transformed
    assert "url('https://media.example.com/media/bg_pattern.png')" in transformed
    assert 'src="https://media.example.com/media/clip.mp4"' in transformed

    # External and inline images preserved untouched
    assert 'src="https://external.com/logo.png"' in transformed
    assert 'src="data:image/svg+xml;base64,PHN2Zz4="' in transformed
