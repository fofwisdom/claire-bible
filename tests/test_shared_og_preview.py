"""공유 문서 웹 미리보기(Open Graph / Twitter Cards / 지식 노드 태그) 테스트.

텔레그램, 마스토돈, 슬랙, 디스코드, X 등 소셜 미디어 및 메신저 크롤러를 위한
메타태그 생성, 텍스트 정제, 이미지 폴백, XSS 방어 및 보안 격리를 검증한다.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from claire.api.server import create_app
from claire.config import Settings
from claire.graphview import (
    extract_knowledge_node_tags,
    render_open_graph_tags,
    resolve_preview_image,
    sanitize_preview_text,
    shared_html,
)
from claire.store import db as dbm


def test_sanitize_preview_text_comprehensive():
    """AsciiDoc, LaTeX, Markdown, HTML 및 특수 매크로가 순수 평문으로 정제되는지 검증."""
    # 1. AsciiDoc stem 및 latexmath
    s1 = "경제 모형 stem:[\\lambda_o = \\alpha + \\beta] 및 latexmath:[E = mc^2] 분석"
    assert sanitize_preview_text(s1) == "경제 모형 \\lambda_o = \\alpha + \\beta 및 E = mc^2 분석"

    # 2. LaTeX $...$ 및 $$...$$
    s2 = "함수 $f(x) = x^2$ 및 $$\\int_0^1 f(x)dx$$ 결과"
    assert sanitize_preview_text(s2) == "함수 f(x) = x^2 및 \\int_0^1 f(x)dx 결과"

    # 3. Markdown 서식 및 링크 (헤딩 기호 제거 후 제목 보존)
    s3 = "### 헤더 3\n\n이 문서는 **굵은 글씨**와 *기울임*, `인라인 코드` 및 [공식 문서](https://example.com)를 포함합니다."
    assert sanitize_preview_text(s3) == "헤더 3 이 문서는 굵은 글씨와 기울임, 인라인 코드 및 공식 문서를 포함합니다."

    # 4. AsciiDoc 링크 및 이미지
    s4 = "참고: https://example.com[웹사이트] 및 image:logo.png[로고 이미지] 확인"
    assert sanitize_preview_text(s4) == "참고: 웹사이트 및 로고 이미지 확인"

    # 5. Admonition 라벨 및 인용
    s5 = "NOTE: 이것은 중요한 참고 사항입니다.\n> 인용된 문장입니다."
    assert sanitize_preview_text(s5) == "이것은 중요한 참고 사항입니다. 인용된 문장입니다."

    # 6. HTML 태그 및 엔티티
    s6 = "<p>HTML <strong>태그</strong> &amp; 엔티티 테스트</p>"
    assert sanitize_preview_text(s6) == "HTML 태그 & 엔티티 테스트"

    # 7. 빈 문자열 및 None
    assert sanitize_preview_text("") == ""
    assert sanitize_preview_text(None) == ""

    # 8. 길이 제한 및 말줄임표(…)
    long_text = "이것은 매우 긴 문장입니다. " * 30
    cleaned_long = sanitize_preview_text(long_text, max_chars=50)
    assert len(cleaned_long) <= 55
    assert cleaned_long.endswith("…")


def test_resolve_preview_image_fallback_and_priorities():
    """대표 이미지 선정 우선순위 및 512x512 브랜드 아이콘 폴백 검증."""
    base_url = "https://claire.example.com"

    # 1. 문서 자체 로컬 이미지 (meta.images)
    doc_local = {
        "meta": {
            "images": [{"local": "images/doc_123_0.png", "alt": "도표"}]
        }
    }
    img, card = resolve_preview_image(doc_local, base_url=base_url)
    assert img == "https://claire.example.com/image?p=images/doc_123_0.png"
    assert card == "summary_large_image"

    # 2. 문서 원격 이미지 (meta.images)
    doc_remote = {
        "meta": {
            "images": [{"url": "https://cdn.example.com/photo.jpg"}]
        }
    }
    img, card = resolve_preview_image(doc_remote, base_url=base_url)
    assert img == "https://cdn.example.com/photo.jpg"
    assert card == "summary_large_image"

    # 3. 본문 detail 내 마크다운 이미지
    doc_detail_md = {
        "detail": "본문 내용 중 ![다이어그램](https://images.example.com/diag.png) 포함"
    }
    img, card = resolve_preview_image(doc_detail_md, base_url=base_url)
    assert img == "https://images.example.com/diag.png"
    assert card == "summary_large_image"

    # 4. 이미지 전무 시 512x512 브랜드 아이콘 폴백
    doc_empty = {"title": "이미지 없는 문서"}
    img, card = resolve_preview_image(doc_empty, base_url=base_url)
    assert img == "https://claire.example.com/icon?p=android-chrome-512x512.png"
    assert card == "summary"

    # 5. base_url 없는 로컬 환경
    img_no_base, _ = resolve_preview_image(doc_empty, base_url="")
    assert img_no_base == "/icon?p=android-chrome-512x512.png"


def test_extract_knowledge_node_tags():
    """지식 노드 엔티티 추출 및 중복 제거 검증."""
    # 딕셔너리 형태 노드
    doc_dict = {
        "nodes": [
            {"id": "1", "label": "CQRS", "group": "pattern"},
            {"id": "2", "label": "이벤트 소싱", "group": "pattern"},
            {"id": "3", "name": "Kafka", "group": "tech"},
            {"id": "4", "label": "CQRS"},  # 중복
        ]
    }
    tags = extract_knowledge_node_tags(doc_dict)
    assert tags == ["CQRS", "이벤트 소싱", "Kafka"]

    # 문자열 형태 노드
    doc_str = {"nodes": ["FastAPI", "SQLite", "FastAPI"]}
    assert extract_knowledge_node_tags(doc_str) == ["FastAPI", "SQLite"]

    # 비어있는 경우
    assert extract_knowledge_node_tags({}) == []
    assert extract_knowledge_node_tags({"nodes": None}) == []


def test_render_open_graph_tags_structure_and_escaping():
    """Open Graph 및 Twitter Card 메타태그 출력 및 XSS 방어 검증."""
    base_url = "https://claire.example.com"
    doc = {
        "title": 'CQRS & "이벤트 소싱" <속성테스트>',
        "summary": 'Kafka & SQLite를 활용한 "고성능" 분산 아키텍처 패턴 <script>alert(1)</script>',
        "author": 'Martin Fowler <저자>',
        "published_at": "2026-08-15",
        "nodes": [{"label": "CQRS"}, {"label": "이벤트 소싱"}],
        "meta": {"images": [{"local": "images/arch.png"}]},
    }

    tags_html = render_open_graph_tags(doc, base_url=base_url, share_token="tok_12345")

    # 1. 필수 OGP 태그 검증
    assert '<meta property="og:site_name" content="Claire Bible"/>' in tags_html
    assert '<meta property="og:type" content="article"/>' in tags_html
    assert '<meta property="og:url" content="https://claire.example.com/p?s=tok_12345"/>' in tags_html
    assert '<meta property="og:image" content="https://claire.example.com/image?p=images/arch.png"/>' in tags_html

    # 2. XSS 및 속성 탈출 방어 (quote=True)
    assert '<script>' not in tags_html
    assert 'alert(1)' not in tags_html  # script 블록 전체 제거
    assert '&quot;이벤트 소싱&quot;' in tags_html
    assert '&quot;고성능&quot;' in tags_html
    assert '&amp;' in tags_html
    assert '&lt;속성테스트&gt;' in tags_html

    # 3. 지식 노드 태그 (keywords 및 article:tag)
    assert '<meta name="keywords" content="CQRS, 이벤트 소싱"/>' in tags_html
    assert '<meta property="article:tag" content="CQRS"/>' in tags_html
    assert '<meta property="article:tag" content="이벤트 소싱"/>' in tags_html

    # 4. 저자 및 발행일
    assert '<meta property="article:author" content="Martin Fowler &lt;저자&gt;"/>' in tags_html
    assert '<meta property="article:published_time" content="2026-08-15"/>' in tags_html

    # 5. Twitter Card
    assert '<meta name="twitter:card" content="summary_large_image"/>' in tags_html
    assert '<meta name="twitter:title" content="CQRS &amp; &quot;이벤트 소싱&quot; &lt;속성테스트&gt;"/>' in tags_html

    # 6. Canonical
    assert '<link rel="canonical" href="https://claire.example.com/p?s=tok_12345"/>' in tags_html


def test_shared_html_integration():
    """shared_html() 실행 시 완성된 HTML 내에 OG 태그가 주입되는지 검증."""
    doc = {
        "title": "공유 페이지 미리보기 테스트",
        "summary": "마스토돈과 텔레그램 미리보기 지원을 검증합니다.",
        "nodes": [{"label": "미리보기"}],
    }

    html_out = shared_html(doc, base_url="https://claire.example.com", share_token="test_tok")

    assert '<meta property="og:site_name" content="Claire Bible"/>' in html_out
    assert '<meta property="og:title" content="공유 페이지 미리보기 테스트"/>' in html_out
    assert '<meta property="og:url" content="https://claire.example.com/p?s=test_tok"/>' in html_out
    assert '<meta property="article:tag" content="미리보기"/>' in html_out
    assert '<!-- __OG_TAGS__ -->' not in html_out


def test_shared_doc_page_endpoint_crawler_and_security(tmp_path: Path):
    """서버 /p?s= 엔드포인트 크롤러 봇 요청 및 보안 검증."""
    db_file = tmp_path / "claire.db"
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)

    # 1. 공개 문서 등록 및 공유 토큰 생성
    conn.execute(
        "INSERT INTO documents (id, title, url, raw_text, source_type, fetched_at, hidden) "
        "VALUES ('doc_pub', '공개 아키텍처 문서', 'https://example.com/pub', '본문 내용', 'web', 1000.0, 0)"
    )
    # 지식 노드 연결 (정상 JSON 파라미터 바인딩)
    conn.execute(
        "INSERT INTO entities (id, name, norm_name, type, sources) VALUES (?, ?, ?, ?, ?)",
        ("e1", "분산처리", "분산처리", "concept", json.dumps(["doc_pub"])),
    )
    conn.commit()

    token_pub = dbm.create_doc_share(conn, "doc_pub")

    # 2. 비공개(hidden=1) 문서도 공유 토큰 발급 시 해당 단일 문서 뷰어로 읽기 가능
    conn.execute(
        "INSERT INTO documents (id, title, url, raw_text, source_type, fetched_at, hidden) "
        "VALUES ('doc_priv', '개별 공유된 문서', 'https://example.com/priv', '개별 내용', 'web', 1000.0, 1)"
    )
    conn.commit()
    token_priv = dbm.create_doc_share(conn, "doc_priv")
    conn.close()

    s = Settings(
        db_path=str(db_file),
        public_url="https://bible.example.com",
    )
    app = create_app(s)
    client = TestClient(app, base_url="https://bible.example.com")

    # 3. 텔레그램 크롤러 시뮬레이션 요청 (TelegramBot User-Agent)
    headers_telegram = {"User-Agent": "TelegramBot (like TwitterBot)"}
    resp_pub = client.get(f"/p?s={token_pub}", headers=headers_telegram)
    assert resp_pub.status_code == 200
    assert "text/html" in resp_pub.headers.get("content-type", "")
    body = resp_pub.text

    # 텔레그램용 OG 메타태그 검증
    assert '<meta property="og:site_name" content="Claire Bible"/>' in body
    assert '<meta property="og:title" content="공개 아키텍처 문서"/>' in body
    assert f'<meta property="og:url" content="https://bible.example.com/p?s={token_pub}"/>' in body
    assert '<meta property="og:image" content="https://bible.example.com/icon?p=android-chrome-512x512.png"/>' in body
    assert '<meta property="article:tag" content="분산처리"/>' in body

    # 4. 마스토돈 크롤러 시뮬레이션 요청 (Mastodon User-Agent)
    headers_mastodon = {"User-Agent": "Mastodon/4.2.0 (+https://mastodon.social/)"}
    resp_masto = client.get(f"/p?s={token_pub}", headers=headers_mastodon)
    assert resp_masto.status_code == 200
    assert '<meta name="twitter:card" content="summary"/>' in resp_masto.text

    # 5. 개별 공유 토큰을 가진 문서는 200 반환 (단일 문서 전용 공유)
    resp_priv = client.get(f"/p?s={token_priv}")
    assert resp_priv.status_code == 200
    assert "개별 공유된 문서" in resp_priv.text

    # 6. 유효하지 않은 공유 토큰 404 차단
    resp_invalid = client.get("/p?s=nonexistent_token_123")
    assert resp_invalid.status_code == 404
