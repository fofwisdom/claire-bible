"""텔레그램 봇 UX 헬퍼 — reaction 이모지 선택 + 진행 ticker(네트워크/PTB 없이)."""

from __future__ import annotations

from claire.telegram_bot import _run_with_ticker, _status_emoji


def test_status_emoji_maps_result():
    assert _status_emoji(None, False) == "👍"   # 신규/갱신 완료
    assert _status_emoji(None, True) == "🤔"    # 중복
    assert _status_emoji("boom", False) == "👎"  # 실패
    assert _status_emoji("boom", True) == "👎"   # error 가 duplicate 보다 우선


async def test_run_with_ticker_returns_work_result():
    """work(블로킹)의 반환값을 그대로 돌려주고, ticker 가 깔끔히 정리되어야 한다."""
    class FakeStatus:
        def __init__(self):
            self.edits = []

        async def edit_text(self, t):
            self.edits.append(t)

    st = FakeStatus()
    out = await _run_with_ticker(st, "label", lambda: {"ok": 1})
    assert out == {"ok": 1}


async def test_run_with_ticker_propagates_exception():
    class FakeStatus:
        async def edit_text(self, t):
            pass

    def boom():
        raise RuntimeError("work failed")

    try:
        await _run_with_ticker(FakeStatus(), "l", boom)
        raised = False
    except RuntimeError:
        raised = True
    assert raised  # work 예외는 호출측으로 전파(핸들러가 👎 처리)


def test_parse_message_directive():
    from claire.telegram_bot import parse_message_directive

    # 1. Flag style (ASCII, em-dash, en-dash, aliases)
    p, d = parse_message_directive("https://example.com/a --orientation 시스템 아키텍처 중심")
    assert p == "https://example.com/a"
    assert d == "시스템 아키텍처 중심"

    # em-dash (—) flag: 모바일 스마트 대시 변환 지원
    p, d = parse_message_directive("https://example.com/a.pdf —orientation Key Activities, Key Partners")
    assert p == "https://example.com/a.pdf"
    assert d == "Key Activities, Key Partners"

    # en-dash (–) flag
    p, d = parse_message_directive("https://example.com/a.pdf –orientation The 9 Building Blocks")
    assert p == "https://example.com/a.pdf"
    assert d == "The 9 Building Blocks"

    p, d = parse_message_directive("https://example.com/b --directive 보안 취약점 관점")
    assert p == "https://example.com/b"
    assert d == "보안 취약점 관점"

    p, d = parse_message_directive("https://example.com/b —관점 비즈니스 모델")
    assert p == "https://example.com/b"
    assert d == "비즈니스 모델"

    p, d = parse_message_directive("https://example.com/c -o 초보자 튜토리얼")
    assert p == "https://example.com/c"
    assert d == "초보자 튜토리얼"

    p, d = parse_message_directive("https://example.com/c —o 초보자 튜토리얼")
    assert p == "https://example.com/c"
    assert d == "초보자 튜토리얼"

    # 2. Pipe separator style (주 문법: | 및 ｜)
    p, d = parse_message_directive("https://example.com/e | 비즈니스 모델 중심")
    assert p == "https://example.com/e"
    assert d == "비즈니스 모델 중심"

    p, d = parse_message_directive("https://example.com/e|비즈니스 모델 중심")
    assert p == "https://example.com/e"
    assert d == "비즈니스 모델 중심"

    p, d = parse_message_directive("https://example.com/e ｜ 전각 파이프 방향성")
    assert p == "https://example.com/e"
    assert d == "전각 파이프 방향성"

    p, d = parse_message_directive("/regenerate doc_123456789012 | 보안 취약점 분석 관점")
    assert p == "/regenerate doc_123456789012"
    assert d == "보안 취약점 분석 관점"

    # Dash separators (호환: ASCII --, em-dash —, en-dash –)
    p, d = parse_message_directive("https://example.com/d -- 핵심 알고리즘 중심")
    assert p == "https://example.com/d"
    assert d == "핵심 알고리즘 중심"

    p, d = parse_message_directive("https://example.com/d.pdf — Key Activities, Key Partners, Key Resources")
    assert p == "https://example.com/d.pdf"
    assert d == "Key Activities, Key Partners, Key Resources"

    # 3. Bracket / hashtag / colon prefix
    p, d = parse_message_directive("https://example.com/f\n[방향성] 성능 최적화 관점")
    assert p == "https://example.com/f"
    assert d == "성능 최적화 관점"

    p, d = parse_message_directive("https://example.com/g\n#방향 실습 예제 중심")
    assert p == "https://example.com/g"
    assert d == "실습 예제 중심"

    p, d = parse_message_directive("https://example.com/h\n초점: 데이터 파이프라인 수명주기")
    assert p == "https://example.com/h"
    assert d == "데이터 파이프라인 수명주기"

    # 4. Multi-line URL + plain text directive (줄바꿈 1번은 사고 방지 유지, 2번째 줄바꿈/빈 줄에서 분리)
    # 4-1. 줄바꿈 1번(태그 없음) -> 단순 사고/메모로 보고 분리하지 않음
    p, d = parse_message_directive("https://example.com/i\n단순 메모 텍스트")
    assert p == "https://example.com/i\n단순 메모 텍스트"
    assert d is None

    # 4-2. 빈 줄(2번째 줄바꿈)이 있는 경우 -> 명백한 의도로 보고 URL과 방향성 텍스트 분리
    p, d = parse_message_directive("https://example.com/i\n\n이 문서는 초보자를 위한 상세 튜토리얼 관점으로 작성해줘")
    assert p == "https://example.com/i"
    assert d == "이 문서는 초보자를 위한 상세 튜토리얼 관점으로 작성해줘"

    p, d = parse_message_directive(
        "https://4952096.fs1.hubspotusercontent-na1.net/hubfs/4952096/Assets%20-%20Downloads/business-model-generation-book-preview-2010-1.pdf\n\n"
        "Key Activities, Key Partners, Key Resources, Cost Structure, Customer Relationships, Customer Segments, Value Propositions, Channels, Revenue Streams"
    )
    assert p == "https://4952096.fs1.hubspotusercontent-na1.net/hubfs/4952096/Assets%20-%20Downloads/business-model-generation-book-preview-2010-1.pdf"
    assert d == "Key Activities, Key Partners, Key Resources, Cost Structure, Customer Relationships, Customer Segments, Value Propositions, Channels, Revenue Streams"

    # 5. Plain text memo with tag
    p, d = parse_message_directive("회의록 메모 본문\n방향성: 액션 아이템 중심")
    assert p == "회의록 메모 본문"
    assert d == "액션 아이템 중심"

    # 6. Plain URL / text without directive
    p, d = parse_message_directive("https://example.com/plain")
    assert p == "https://example.com/plain"
    assert d is None

    p, d = parse_message_directive("단순한 메모 텍스트")
    assert p == "단순한 메모 텍스트"
    assert d is None

    p, d = parse_message_directive("")
    assert p == ""
    assert d is None


def test_parse_caption_directive():
    from claire.telegram_bot import parse_caption_directive

    assert parse_caption_directive(None) is None
    assert parse_caption_directive("") is None
    assert parse_caption_directive("시스템 아키텍처 중심") == "시스템 아키텍처 중심"
    assert parse_caption_directive("[방향성] 보안 분석 중심") == "보안 분석 중심"
    assert parse_caption_directive("방향: 튜토리얼 관점") == "튜토리얼 관점"


def test_ingest_report_telegram_summary_with_directive():
    from claire.ingest.pipeline import IngestReport

    report = IngestReport(
        document_id="doc_1",
        title="테스트 문서",
        summary="요약 내용입니다.",
        directive="시스템 아키텍처 및 내부 구조 중심",
    )
    summary = report.telegram_summary()
    assert "✅ 적재 완료: 테스트 문서" in summary
    assert "초점/방향성: 시스템 아키텍처 및 내부 구조 중심" in summary

