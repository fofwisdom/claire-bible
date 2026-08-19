"""Pre-LLM Content Guard 단위 테스트 (tests/test_content_guard.py)."""

from __future__ import annotations

from claire.ingest.fetchers.guard import validate_web_content


def test_empty_content():
    ok, reason = validate_web_content(None, "")
    assert ok is False
    assert reason == "empty_content"


def test_cloudflare_challenge_detection():
    # 300자 이상의 Cloudflare 챌린지 텍스트
    cf_text = (
        "Just a moment... Enable JavaScript and cookies to continue. "
        "DDoS protection by Cloudflare. Ray ID: 89f10a2b3c4d5e6f. "
        "Checking your browser before accessing the website. "
        "Please wait while your request is being verified. "
    ) * 3
    ok, reason = validate_web_content("Just a moment...", cf_text)
    assert ok is False
    assert reason == "blocked: bot_challenge"


def test_captcha_challenge_detection():
    captcha_text = (
        "Security Check Required. Please complete the security check to access this page. "
        "Verify you are human by completing the captcha challenge below. "
    ) * 4
    ok, reason = validate_web_content("Verify you are human", captcha_text)
    assert ok is False
    assert reason == "blocked: bot_challenge"


def test_soft_403_access_denied_detection():
    forbidden_text = (
        "Access Denied. You do not have permission to access this resource on this server. "
        "Error 403: Forbidden. Request blocked by administrative rules. "
    ) * 4
    ok, reason = validate_web_content("403 Forbidden", forbidden_text)
    assert ok is False
    assert reason == "blocked: access_denied"


def test_soft_404_not_found_detection():
    not_found_text = (
        "Page Not Found. The page you are looking for has been removed, "
        "had its name changed, or is temporarily unavailable. "
        "Please check the URL and try again. "
    ) * 3
    ok, reason = validate_web_content("404 - Page Not Found", not_found_text)
    assert ok is False
    assert reason == "low_quality: not_found"


def test_korean_soft_404_detection():
    kr_not_found_text = (
        "요청하신 페이지를 찾을 수 없습니다. "
        "방문하시려는 페이지의 주소가 잘못 입력되었거나, 변경 또는 삭제되어 요청하신 페이지를 찾을 수 없습니다. "
        "입력하신 주소가 정확한지 다시 한번 확인해 주시기 바랍니다. "
    ) * 3
    ok, reason = validate_web_content("페이지를 찾을 수 없습니다", kr_not_found_text)
    assert ok is False
    assert reason == "low_quality: not_found"


def test_paywall_login_gate_detection():
    paywall_text = (
        "Sign in to continue reading this exclusive analysis. "
        "Join LinkedIn to view the full profile and post. "
        "Subscribe to read the full story. "
    ) * 4  # < 800 chars
    ok, reason = validate_web_content("Tech Article", paywall_text)
    assert ok is False
    assert reason == "low_quality: paywall_or_login"


def test_low_alphanumeric_density():
    # 기호나 공백만 가득한 잡음 텍스트
    noise_text = "---===***###!!!???   ...   ,,,   ;;;   :::   " * 15
    ok, reason = validate_web_content("Noise", noise_text)
    assert ok is False
    assert reason == "low_quality: low_alphanumeric_density"


def test_valid_long_article_mentioning_cloudflare_passes():
    # Cloudflare를 기술적으로 설명/분석하는 정상적인 긴 글은 통과해야 함 (False Positive 방지)
    valid_text = (
        "In this in-depth engineering article, we analyze modern web security architectures. "
        "Cloudflare provides reverse proxy and CDN capabilities across global edge data centers. "
        "The system handles DDoS mitigation, caching, and TLS termination efficiently. "
        "We discuss edge computing workers, DNS resolvers, and routing protocols in detail. "
    ) * 40  # > 3000 chars
    ok, reason = validate_web_content("Deep Dive into Edge Networks and CDNs", valid_text)
    assert ok is True
    assert reason is None


def test_valid_article_mentioning_404_error_passes():
    # 404 에러 핸들링을 다루는 정상 개발 기술 블로그 글
    valid_text = (
        "Building robust REST APIs requires proper HTTP status code handling. "
        "When an entity ID does not exist in SQLite database, the controller should return 404 Not Found. "
        "We implement custom exception handlers in FastAPI and Express.js to format JSON error responses. "
        "Logging 404 occurrences helps identify broken links and crawling issues across our service. "
    ) * 15  # > 1500 chars with normal title
    ok, reason = validate_web_content("Best Practices for HTTP Status Code Handling in REST APIs", valid_text)
    assert ok is True
    assert reason is None
