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
    """/web: 버튼 승인 단계 없이 즉시 발급된 전체 토큰이 곧바로 유효하다."""
    conn = _mem()
    tok = dbm.create_session(conn, ttl=100)
    assert tok and dbm.validate_session(conn, tok) is True
    assert dbm.validate_session(conn, "wrong-token") is False
    assert len(tok) >= dbm.MIN_SESSION_TOKEN_LENGTH


def test_bootstrap_exchange_rotates_once_and_preserves_scope():
    conn = _mem()
    bootstrap = dbm.create_session(conn, scope="owner")

    exchanged = dbm.exchange_session_token(conn, bootstrap)

    assert exchanged is not None
    scope, cookie_session = exchanged
    assert scope == "owner"
    assert cookie_session != bootstrap
    assert dbm.exchange_session_token(conn, bootstrap) is None
    assert dbm.validate_session_scope(conn, bootstrap) is None
    assert dbm.validate_session_scope(conn, cookie_session) == "owner"


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


# --- /web 진입: 전체 세션 토큰만 허용 ---

def test_session_prefix_and_legacy_short_token_are_rejected():
    conn = _mem()
    full = dbm.create_session(conn)
    assert dbm.validate_session(conn, full[:12]) is False

    now = time.time()
    legacy = "abcdefghjkmn"
    conn.execute(
        "INSERT INTO auth_sessions"
        "(nonce,session_token,approved,created_at,expires_at,scope) "
        "VALUES (?,?,1,?,?,?)",
        (legacy, legacy, now, now + 1000, "owner"),
    )
    conn.commit()
    assert dbm.validate_session(conn, legacy) is False


def test_validate_session_scope_returns_resolved_scope():
    conn = _mem()
    owner = dbm.create_session(conn, scope="owner")
    readonly = dbm.create_session(conn, scope="readonly")
    assert dbm.validate_session_scope(conn, owner) == "owner"
    assert dbm.validate_session_scope(conn, readonly) == "readonly"


# --- /webro 읽기전용 세션(scope) — owner(/web)와 독립적으로 공존 ---

def test_readonly_scope_rejected_by_owner_only_gate():
    """readonly 세션은 기본(owner 전용) validate_session 게이트를 통과 못 한다 —
    쓰기 라우트(_authed)가 실수로 읽기전용 세션을 받아들이면 안 되므로."""
    conn = _mem()
    ro = dbm.create_session(conn, scope="readonly")
    assert dbm.validate_session(conn, ro) is False
    assert dbm.validate_session(conn, ro, scopes=("owner", "readonly")) is True  # 읽기 게이트는 통과


def test_owner_scope_also_passes_read_gate():
    """owner 세션은 당연히 읽기 게이트(scopes=owner+readonly)도 통과."""
    conn = _mem()
    owner = dbm.create_session(conn, scope="owner")
    assert dbm.validate_session(conn, owner) is True
    assert dbm.validate_session(conn, owner, scopes=("owner", "readonly")) is True


def test_readonly_and_owner_sessions_coexist_independently():
    """서로 다른 scope 는 서로의 재발급에 영향받지 않는다(같은 scope 만 단일 활성)."""
    conn = _mem()
    owner = dbm.create_session(conn, scope="owner")
    ro1 = dbm.create_session(conn, scope="readonly")
    assert dbm.validate_session(conn, owner) is True             # 아직 안 건드림
    assert dbm.validate_session(conn, ro1, scopes=("owner", "readonly")) is True

    ro2 = dbm.create_session(conn, scope="readonly")              # readonly 재발급
    assert dbm.validate_session(conn, owner) is True              # owner 는 그대로
    assert dbm.validate_session(conn, ro1, scopes=("owner", "readonly")) is False  # 이전 ro 죽음
    assert dbm.validate_session(conn, ro2, scopes=("owner", "readonly")) is True

    owner2 = dbm.create_session(conn, scope="owner")              # owner 재발급
    assert dbm.validate_session(conn, owner) is False             # 이전 owner 죽음
    assert dbm.validate_session(conn, owner2) is True
    assert dbm.validate_session(conn, ro2, scopes=("owner", "readonly")) is True  # readonly 는 안 건드림
