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
    """/web: 버튼 승인 단계 없이 즉시 발급된 토큰이 곧바로 유효."""
    conn = _mem()
    tok = dbm.create_session(conn, ttl=100)
    assert tok and dbm.validate_session(conn, tok) is True
    assert dbm.validate_session(conn, "wrong-token") is False


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
