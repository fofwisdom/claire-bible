"""웹 UI 텔레그램 세션 인증 — negative path 가 곧 기능(모두 통과면 무가치).

핵심: 미승인/만료 거부 + 2프로세스(봇 승인 / API 검증) DB 핸드오프.
"""

from __future__ import annotations

import sqlite3
import time

from claire.store import db as dbm


def _mem():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    return conn


def test_unapproved_nonce_yields_no_token():
    conn = _mem()
    nonce = dbm.create_auth_nonce(conn)
    assert dbm.poll_auth_nonce(conn, nonce) is None        # 아직 승인 안 됨
    assert dbm.validate_session(conn, "anything") is False  # 임의 토큰 거부
    assert dbm.validate_session(conn, "") is False


def test_approve_issues_and_validates_token():
    conn = _mem()
    nonce = dbm.create_auth_nonce(conn)
    tok = dbm.approve_auth_nonce(conn, nonce)
    assert tok
    assert dbm.poll_auth_nonce(conn, nonce) == tok          # 웹 폴링이 토큰 수령
    assert dbm.validate_session(conn, tok) is True          # 비용 엔드포인트 통과


def test_two_connection_handoff(tmp_path):
    """봇 프로세스(connA)가 승인 → API 프로세스(connB)가 검증. happy path 를 한
    커넥션에서만 보면 2프로세스 핸드오프 버그가 가려진다(advisor)."""
    db = str(tmp_path / "auth.db")
    a = dbm.connect(db); dbm.init_db(a)
    nonce = dbm.create_auth_nonce(a)
    tok = dbm.approve_auth_nonce(a, nonce)
    a.close()

    b = dbm.connect(db); dbm.init_db(b)
    try:
        assert dbm.validate_session(b, tok) is True
        assert dbm.poll_auth_nonce(b, nonce) == tok
    finally:
        b.close()


def test_expired_session_rejected():
    conn = _mem()
    tok = dbm.approve_auth_nonce(conn, dbm.create_auth_nonce(conn))
    conn.execute("UPDATE auth_sessions SET expires_at=? WHERE session_token=?",
                 (time.time() - 1, tok))
    conn.commit()
    assert dbm.validate_session(conn, tok) is False         # 만료 → 거부


def test_expired_nonce_cannot_be_approved():
    conn = _mem()
    nonce = dbm.create_auth_nonce(conn, ttl=-1)             # 이미 만료된 nonce
    assert dbm.approve_auth_nonce(conn, nonce) is None


def test_unknown_nonce_cannot_be_approved():
    assert dbm.approve_auth_nonce(_mem(), "does-not-exist") is None


def test_double_approve_is_noop():
    """이미 승인된 nonce 재승인 시도 → None(approved=0 조건). 첫 토큰만 유효."""
    conn = _mem()
    nonce = dbm.create_auth_nonce(conn)
    tok1 = dbm.approve_auth_nonce(conn, nonce)
    tok2 = dbm.approve_auth_nonce(conn, nonce)
    assert tok1 and tok2 is None
    assert dbm.validate_session(conn, tok1) is True


# --- /web 즉시 발급 세션 + 슬라이딩 만료 ---

def test_create_session_validates_immediately():
    """/web: 버튼 승인 단계 없이 즉시 발급된 토큰이 곧바로 유효 + 짧고 타이핑 쉬운 코드."""
    conn = _mem()
    tok = dbm.create_session(conn, ttl=100)
    assert tok and dbm.validate_session(conn, tok) is True
    assert dbm.validate_session(conn, "wrong-token") is False
    assert len(tok) <= 12                                    # 짧은 코드(수동 입력용)
    assert all(c in "23456789abcdefghjkmnpqrstuvwxyz" for c in tok)  # 헷갈리는 문자 없음


def test_create_session_single_active_revokes_previous():
    """B 모델: 새 발급이 이전 토큰을 전부 무효화(단일 활성). 프리픽스 매칭 아님(전체 일치)."""
    conn = _mem()
    t1 = dbm.create_session(conn)
    t2 = dbm.create_session(conn)
    assert t1 != t2
    assert dbm.validate_session(conn, t1) is False          # 이전 토큰 죽음
    assert dbm.validate_session(conn, t2) is True            # 최신만 유효
    # 프리픽스만 맞는 가짜 토큰은 거부(전체 일치 요구 — 보안)
    assert dbm.validate_session(conn, t2[:7]) is False
    assert dbm.revoke_all_sessions(conn) == 1               # 남은 1개 revoke
    assert dbm.validate_session(conn, t2) is False


def test_session_sliding_extends_expiry():
    """유효 접속(검증)마다 만료가 now+ttl 로 연장(슬라이딩) — 활성 사용 중 재인증 없음."""
    conn = _mem()
    tok = dbm.create_session(conn, ttl=100)
    # 곧 만료되게 강제
    conn.execute("UPDATE auth_sessions SET expires_at=? WHERE session_token=?",
                 (time.time() + 1, tok))
    conn.commit()
    before = conn.execute(
        "SELECT expires_at FROM auth_sessions WHERE session_token=?", (tok,)).fetchone()[0]
    assert dbm.validate_session(conn, tok, ttl=1000) is True   # 접속 → 연장
    after = conn.execute(
        "SELECT expires_at FROM auth_sessions WHERE session_token=?", (tok,)).fetchone()[0]
    assert after > before + 500                                # now+1000 으로 밀림


def test_expired_session_not_revived_by_validate():
    """이미 만료된 세션은 검증에서 되살아나지 않는다(만료 후 슬라이딩 금지)."""
    conn = _mem()
    tok = dbm.create_session(conn, ttl=100)
    conn.execute("UPDATE auth_sessions SET expires_at=? WHERE session_token=?",
                 (time.time() - 1, tok))
    conn.commit()
    assert dbm.validate_session(conn, tok) is False


# --- /web 진입: 링크는 길게, 수동 입력은 짧게(프리픽스 해소). 사용자 요구(이슈4) ---

def test_resolve_prefix_returns_full_token():
    """7자+ 프리픽스로 단일 활성 세션을 해소하면 **전체 토큰**을 돌려준다(쿠키엔 전체 저장)."""
    conn = _mem()
    full = dbm.create_session(conn)
    assert len(full) >= dbm.MIN_TOKEN_PREFIX + 1            # 링크는 프리픽스보다 길다
    assert dbm.resolve_session_prefix(conn, full) == full   # 전체 입력도 OK
    assert dbm.resolve_session_prefix(conn, full[:7]) == full  # 7자 프리픽스 → 전체


def test_resolve_prefix_rejects_short_and_bogus():
    """6자 이하·알파벳 외 문자(LIKE 와일드카드 주입)·빈 입력은 거부."""
    conn = _mem()
    full = dbm.create_session(conn)
    assert dbm.resolve_session_prefix(conn, full[:6]) is None    # 너무 짧음
    assert dbm.resolve_session_prefix(conn, "") is None
    assert dbm.resolve_session_prefix(conn, "%%%%%%%") is None    # % 와일드카드 주입 차단
    assert dbm.resolve_session_prefix(conn, full[:6] + "_") is None  # _ 와일드카드 차단


def test_resolve_prefix_no_session_returns_none():
    assert dbm.resolve_session_prefix(_mem(), "abcdefg") is None


def test_resolve_prefix_slides_expiry():
    """진입 해소도 슬라이딩 연장(접속 시점부터 다시 7일)."""
    conn = _mem()
    full = dbm.create_session(conn, ttl=100)
    conn.execute("UPDATE auth_sessions SET expires_at=? WHERE session_token=?",
                 (time.time() + 1, full))
    conn.commit()
    assert dbm.resolve_session_prefix(conn, full[:7], ttl=1000) == full
    after = conn.execute(
        "SELECT expires_at FROM auth_sessions WHERE session_token=?", (full,)).fetchone()[0]
    assert after > time.time() + 500

    # 쿠키 검증은 여전히 전체 일치만(프리픽스 거부 — 보안 불변)
    assert dbm.validate_session(conn, full[:7]) is False
    assert dbm.validate_session(conn, full) is True
