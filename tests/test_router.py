"""입력 라우팅 — '제목 + 트레일링 링크' 공유 텍스트에서 URL 추출 (이슈5 근본원인)."""

from __future__ import annotations

from claire.ingest.router import classify, extract_shared_url, fetch
from claire.telegram_bot import classify_input


def test_extract_shared_url_trailing_one_line():
    # 모바일 공유: 제목 … 링크(한 줄, 끝에 URL)
    t = "공개용 합성 문서 - 샘플 작성자 https://share.example.com/fixture"
    assert extract_shared_url(t) == "https://share.example.com/fixture"


def test_extract_shared_url_trailing_newline():
    t = "공개용 합성 문서\nhttps://example.com/articles/fixture"
    assert extract_shared_url(t) == "https://example.com/articles/fixture"


def test_extract_shared_url_none_for_plain_memo():
    # URL 없는 순수 메모
    assert extract_shared_url("RAG 파이프라인 정리 필요") is None
    # 본문 중간 링크가 섞인 일반 메모(트레일링 아님) → text 유지
    assert extract_shared_url("이거 https://example.com 봐") is None
    # 이미 bare URL 로 시작 → 공유 추출 아님(기존 경로가 처리)
    assert extract_shared_url("https://example.com/x") is None


def test_classify_routes_shared_text_by_url():
    # 제목+share.google → redirect 종류
    assert classify("제목 어쩌고 https://share.google/AbC") == "redirect"
    # 제목+youtube → youtube
    assert classify("좋은 영상 https://youtube.com/watch?v=x") == "youtube"
    # 순수 텍스트는 그대로 text
    assert classify("그냥 메모") == "text"


def test_classify_input_label_matches_router():
    # 텔레그램 진행 라벨도 동일 규칙으로 공유 링크를 인식
    assert classify_input("제목 https://share.google/AbC") == "redirect"
    assert classify_input("제목 https://youtu.be/x") == "youtube"
    assert classify_input("그냥 메모") == "text"


def test_fetch_routes_shared_text_to_url(monkeypatch):
    # fetch 가 공유 텍스트의 URL 로 라우팅하는지(실제 fetch 는 가짜로 가로채 확인).
    seen = {}
    import claire.ingest.fetchers.web as webmod

    def fake_web(u):
        seen["url"] = u
        from claire.ontology.base import Document
        return Document(url=u, canonical_url=u, raw_text="ok",
                        source_type="web", content_hash="h")

    monkeypatch.setattr(webmod, "fetch_web", fake_web)
    doc = fetch("재밌는 글 제목 https://example.com/real-article")
    assert seen["url"] == "https://example.com/real-article"
    assert doc.url == "https://example.com/real-article"
