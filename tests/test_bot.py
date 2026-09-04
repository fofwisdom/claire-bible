"""텔레그램 봇 UX 헬퍼 — reaction 이모지 선택 + 진행 ticker(네트워크/PTB 없이)."""

from __future__ import annotations

from claire.telegram_bot import _run_with_ticker, _settle_status, _status_emoji


def test_status_emoji_maps_result():
    assert _status_emoji(None, False) == "👍"   # 신규/갱신 완료
    assert _status_emoji(None, True) == "👌"    # 중복
    assert _status_emoji("boom", False) == "👎"  # 실패
    assert _status_emoji("boom", True) == "👎"   # error 가 duplicate 보다 우선
    assert _status_emoji(None, False, stt_error="RateLimitError: 429") == "👎"  # STT 실패 시 👎
    assert _status_emoji(None, True, stt_error="RateLimitError: 429") == "👎"   # stt_error 가 duplicate 보다 우선


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


async def test_run_with_ticker_displays_progress_stage():
    import time
    from claire.extract.provider import emit_progress

    class FakeStatus:
        def __init__(self):
            self.edits = []

        async def edit_text(self, t):
            self.edits.append(t)

    def work():
        emit_progress("원문 가져오는 중…")
        time.sleep(0.08)
        emit_progress("구조화 추출·그래프 적재 중…")
        time.sleep(0.08)
        return "done"

    st = FakeStatus()
    out = await _run_with_ticker(st, "테스트 작업", work, interval=0.05)
    assert out == "done"
    assert len(st.edits) > 0
    assert any("• 원문 가져오는 중…" in e or "• 구조화 추출·그래프 적재 중…" in e for e in st.edits)



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

    p, d = parse_message_directive("https://example.com/b —perspective 비즈니스 모델")
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
    assert parse_caption_directive("[초점] 보안 분석 중심") == "보안 분석 중심"
    assert parse_caption_directive("[방향성] 보안 분석 중심") == "보안 분석 중심"
    assert parse_caption_directive("초점: 튜토리얼 관점") == "튜토리얼 관점"


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
    assert "초점: 시스템 아키텍처 및 내부 구조 중심" in summary


def test_ingest_report_telegram_summary_stt_failure():
    from claire.ingest.pipeline import IngestReport

    report = IngestReport(
        document_id="doc_30e5a1923bff",
        title="APPB1629LV",
        source_type="video",
        has_transcript=False,
        stt_error="RateLimitError: 429",
        summary="VMware Cloud Foundation 관리자를 대상으로...",
    )
    summary = report.telegram_summary()
    assert "⚠️ 부분 적재 (STT 전사 실패): APPB1629LV" in summary
    assert "✅ 적재 완료" not in summary
    assert "🎙️ 오디오 STT 전사 실패: 음성 자막이 추출되지 못했습니다. (오류: RateLimitError: 429)" in summary


async def test_settle_status_behavior():
    class FakeStatus:
        def __init__(self):
            self.edited_text = None
            self.markup = None
            self.deleted = False

        async def edit_text(self, text, reply_markup=None):
            self.edited_text = text
            self.markup = reply_markup

        async def delete(self):
            self.deleted = True

    class FakeMsg:
        def __init__(self):
            self.replied_text = None

        async def reply_text(self, text, reply_markup=None):
            self.replied_text = text

    # 1. Normal success without candidates -> status message deleted (no spam)
    st1 = FakeStatus()
    await _settle_status(st1, FakeMsg(), "✅ 적재 완료", [])
    assert st1.deleted is True
    assert st1.edited_text is None

    # 2. STT failure with doc_id -> message preserved, retry button attached, not deleted
    st2 = FakeStatus()
    await _settle_status(
        st2,
        FakeMsg(),
        "⚠️ 부분 적재 (STT 전사 실패)",
        [],
        is_stt_failed=True,
        retry_doc_id="doc_123",
    )
    assert st2.deleted is False
    assert st2.edited_text == "⚠️ 부분 적재 (STT 전사 실패)"
    assert st2.markup is not None
    assert st2.markup.inline_keyboard[0][0].callback_data == "rg:full:doc_123"

    # 3. General error -> message preserved, not deleted
    st3 = FakeStatus()
    await _settle_status(
        st3,
        FakeMsg(),
        "❌ 처리 오류",
        [],
        has_error=True,
    )
    assert st3.deleted is False
    assert st3.edited_text == "❌ 처리 오류"

    # 4. Status edit fails -> falls back to msg.reply_text
    class FailingStatus(FakeStatus):
        async def edit_text(self, text, reply_markup=None):
            raise RuntimeError("Telegram API timeout")

    failing_st = FailingStatus()
    fake_msg = FakeMsg()
    await _settle_status(
        failing_st,
        fake_msg,
        "❌ 처리 오류",
        [],
        has_error=True,
    )
    assert fake_msg.replied_text == "❌ 처리 오류"

    # 5. Both status.edit_text and msg.reply_text fail -> does NOT raise uncaught exception
    class FailingMsg(FakeMsg):
        async def reply_text(self, text, reply_markup=None):
            raise RuntimeError("Telegram network disconnect")

    await _settle_status(
        FailingStatus(),
        FailingMsg(),
        "❌ 처리 오류",
        [],
        has_error=True,
    )

    # 6. Status delete fails -> does NOT raise uncaught exception
    class FailingDeleteStatus(FakeStatus):
        async def delete(self):
            raise RuntimeError("Message already deleted or network down")

    await _settle_status(
        FailingDeleteStatus(),
        FakeMsg(),
        "✅ 적재 완료",
        [],
    )

    # 7. Duplicate with retry_doc_id -> message preserved, buttons attached
    st_dup = FakeStatus()
    await _settle_status(
        st_dup,
        FakeMsg(),
        "♻️ 이미 있는 자료입니다 (dedup): DocTitle",
        [],
        is_duplicate=True,
        retry_doc_id="doc_123",
    )
    assert st_dup.deleted is False
    assert st_dup.edited_text == "♻️ 이미 있는 자료입니다 (dedup): DocTitle"
    assert st_dup.markup is not None
    assert st_dup.markup.inline_keyboard[0][0].callback_data == "rg:det:doc_123"
    assert st_dup.markup.inline_keyboard[1][0].callback_data == "rg:full:doc_123"

    # 8. Duplicate without retry_doc_id -> message preserved without markup
    st_dup2 = FakeStatus()
    await _settle_status(
        st_dup2,
        FakeMsg(),
        "♻️ 이미 있는 자료입니다 (dedup): DocTitle",
        [],
        is_duplicate=True,
    )
    assert st_dup2.deleted is False
    assert st_dup2.edited_text == "♻️ 이미 있는 자료입니다 (dedup): DocTitle"
    assert st_dup2.markup is None



