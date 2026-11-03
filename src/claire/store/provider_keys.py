"""계정 프로바이더 키 저장소 (설계 §11, §13.3 ``account_provider_keys``, P4 계약 스텁).

쓰기 전용 비밀이다(B6). 평문은 ``load_active_key``만 돌려주고, 그 밖의 함수와 API는 메타데이터만 다룬다.
AAD는 ``account_id|provider|key_id``이므로 암호문을 다른 행으로 옮기면 복호화가 실패한다.

레인 소유: L4-b.
"""

from __future__ import annotations

import secrets
import sqlite3
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

from claire.secret_scrub import register_secret, scrub_text

if TYPE_CHECKING:
    from claire.store.secrets import SecretService

KeyStatus = Literal["active", "rejected", "unverified"]
FINGERPRINT_PURPOSE = "provider_key_fingerprint"
KEY_STATUSES: Final[tuple[str, ...]] = ("active", "rejected", "unverified")
# 시험 호출에 쓰는 모델. ``Settings.gemini_model`` 기본값과 같다(멤버 키는 관리자 설정을 읽지 않는다).
VERIFY_MODELS: Final[dict[str, str]] = {"gemini": "gemini-3.1-flash-lite"}


class ProviderKeyError(RuntimeError):
    """키 저장·검증 실패. 메시지에 키 원문을 넣지 않는다."""


class ProviderKeyVerifyUnavailable(ProviderKeyError):
    """시험 호출이 키 판정에 이르지 못했다(네트워크 오류·타임아웃·429·5xx 등). 메시지는 스크럽된 문구다.

    키를 거부로 기록하지 않고, 등록이면 저장하지 않는다.
    """


@dataclass(frozen=True, slots=True)
class ProviderKeyMeta:
    """§11.2 응답 필드 그대로. 키의 어떤 문자도 담지 않는다(D8)."""

    provider: str
    fingerprint: str  # 파생 HMAC 앞 8자리
    status: KeyStatus
    created_at: float
    last_verified_at: float | None
    last_used_at: float | None


@dataclass(frozen=True, slots=True)
class ActiveKey:
    """복호화한 키. 프로바이더 호출 직전에만 만든다."""

    provider: str
    fingerprint: str
    api_key: str

    def __repr__(self) -> str:  # 로그·예외에 원문이 찍히지 않게
        return f"ActiveKey(provider={self.provider!r}, fingerprint={self.fingerprint!r})"


def key_aad(account_id: int, provider: str, key_id: str) -> bytes:
    return f"{account_id}|{provider}|{key_id}".encode("utf-8")


def put_key(
    conn: sqlite3.Connection,
    *,
    account_id: int,
    provider: str,
    api_key: str,
    status: KeyStatus,
    secret_service: "SecretService | None" = None,
    now: float | None = None,
) -> ProviderKeyMeta:
    """등록·교체(``UNIQUE(account_id, provider)``). 교체하면 새 ``key_id``와 nonce를 쓴다. 검증은 호출자(라우트)가 먼저 한다."""
    if status not in KEY_STATUSES:
        raise ValueError("invalid key status")
    if not isinstance(api_key, str) or not api_key:
        raise ProviderKeyError("api key must be a non-empty string")
    register_secret(api_key)
    sec = _service(secret_service)
    ts = time.time() if now is None else now
    key_id = secrets.token_hex(16)
    encrypted = sec.encrypt(api_key, key_aad(account_id, provider, key_id))
    fingerprint = sec.compute_fingerprint(api_key, purpose=FINGERPRINT_PURPOSE)[:8]
    verified_at = ts if status == "active" else None
    with conn:
        conn.execute(
            "DELETE FROM account_provider_keys WHERE account_id=? AND provider=?",
            (account_id, provider),
        )
        conn.execute(
            "INSERT INTO account_provider_keys (id, account_id, provider, ciphertext, nonce, key_version, "
            "fingerprint, status, created_at, last_verified_at, last_used_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (
                key_id, account_id, provider, encrypted.ciphertext, encrypted.nonce, encrypted.key_version,
                fingerprint, status, ts, verified_at,
            ),
        )
    return ProviderKeyMeta(
        provider=provider,
        fingerprint=fingerprint,
        status=status,
        created_at=ts,
        last_verified_at=verified_at,
        last_used_at=None,
    )


_META_COLUMNS = "provider, fingerprint, status, created_at, last_verified_at, last_used_at"


def _meta_from_row(row: sqlite3.Row | tuple) -> ProviderKeyMeta:
    provider, fingerprint, status, created_at, last_verified_at, last_used_at = tuple(row)
    return ProviderKeyMeta(
        provider=str(provider),
        fingerprint=str(fingerprint),
        status=status,
        created_at=float(created_at),
        last_verified_at=None if last_verified_at is None else float(last_verified_at),
        last_used_at=None if last_used_at is None else float(last_used_at),
    )


def get_key_meta(conn: sqlite3.Connection, *, account_id: int) -> list[ProviderKeyMeta]:
    rows = conn.execute(
        f"SELECT {_META_COLUMNS} FROM account_provider_keys WHERE account_id=? ORDER BY provider",
        (account_id,),
    ).fetchall()
    return [_meta_from_row(r) for r in rows]


def has_key(conn: sqlite3.Connection, *, account_id: int) -> bool:
    """관리자 목록용 등록 여부(§11.2)."""
    row = conn.execute("SELECT 1 FROM account_provider_keys WHERE account_id=? LIMIT 1", (account_id,)).fetchone()
    return row is not None


def delete_key(conn: sqlite3.Connection, *, account_id: int, provider: str | None = None) -> int:
    """지운 행 수. ``provider``가 None이면 그 계정의 키 전부."""
    with conn:
        if provider is None:
            cur = conn.execute("DELETE FROM account_provider_keys WHERE account_id=?", (account_id,))
        else:
            cur = conn.execute(
                "DELETE FROM account_provider_keys WHERE account_id=? AND provider=?", (account_id, provider)
            )
    return int(cur.rowcount or 0)


def set_key_status(
    conn: sqlite3.Connection,
    *,
    account_id: int,
    provider: str,
    status: KeyStatus,
    verified_at: float | None = None,
) -> None:
    if status not in KEY_STATUSES:
        raise ValueError("invalid key status")
    with conn:
        if verified_at is None:
            conn.execute(
                "UPDATE account_provider_keys SET status=? WHERE account_id=? AND provider=?",
                (status, account_id, provider),
            )
        else:
            conn.execute(
                "UPDATE account_provider_keys SET status=?, last_verified_at=? WHERE account_id=? AND provider=?",
                (status, verified_at, account_id, provider),
            )


def touch_last_used(conn: sqlite3.Connection, *, account_id: int, provider: str, now: float | None = None) -> None:
    ts = time.time() if now is None else now
    with conn:
        conn.execute(
            "UPDATE account_provider_keys SET last_used_at=? WHERE account_id=? AND provider=?",
            (ts, account_id, provider),
        )


def _service(secret_service: "SecretService | None") -> "SecretService":
    if secret_service is not None:
        return secret_service
    from claire.store.secrets import get_secret_service

    return get_secret_service()


def _decrypt_row(row: sqlite3.Row | tuple, *, account_id: int, sec: "SecretService") -> ActiveKey:
    from claire.store.secrets import SecretDecryptionError

    key_id, provider, ciphertext, nonce, key_version, fingerprint = tuple(row)
    try:
        raw = sec.decrypt(
            bytes(ciphertext), bytes(nonce), key_aad(account_id, str(provider), str(key_id)), int(key_version)
        )
    except SecretDecryptionError:
        raise ProviderKeyError("stored provider key could not be decrypted") from None
    try:
        api_key = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ProviderKeyError("stored provider key could not be decrypted") from None
    register_secret(api_key)
    return ActiveKey(provider=str(provider), fingerprint=str(fingerprint), api_key=api_key)


_KEY_COLUMNS = "id, provider, ciphertext, nonce, key_version, fingerprint"


def load_key(
    conn: sqlite3.Connection,
    *,
    account_id: int,
    provider: str,
    secret_service: "SecretService | None" = None,
) -> ActiveKey | None:
    """상태와 무관하게 그 프로바이더 키를 복호화한다. 재검증(``POST /api/me/keys/verify``) 전용이다.

    작업 경로는 ``load_active_key``만 쓴다. 복호화 실패는 ``ProviderKeyError``.
    """
    row = conn.execute(
        f"SELECT {_KEY_COLUMNS} FROM account_provider_keys WHERE account_id=? AND provider=?",
        (account_id, provider),
    ).fetchone()
    if row is None:
        return None
    return _decrypt_row(row, account_id=account_id, sec=_service(secret_service))


def load_active_key(
    conn: sqlite3.Connection,
    *,
    account_id: int,
    secret_service: "SecretService | None" = None,
) -> ActiveKey | None:
    """``status='active'``인 키를 복호화해 돌려준다. 없으면 None. 복호화 실패는 ``ProviderKeyError``(폴백하지 않는다).

    복호화한 값은 ``claire.secret_scrub.register_secret``에 등록한다.
    """
    rows = conn.execute(
        f"SELECT {_KEY_COLUMNS} FROM account_provider_keys WHERE account_id=? AND status='active' ORDER BY provider",
        (account_id,),
    ).fetchall()
    if not rows:
        return None
    # 여러 프로바이더가 생기면 멤버 키 프로바이더 순서를 따른다(지금은 gemini 하나).
    from claire.accounts.provider_policy import MEMBER_KEY_PROVIDERS

    order = {name: i for i, name in enumerate(MEMBER_KEY_PROVIDERS)}
    row = min(rows, key=lambda r: order.get(str(tuple(r)[1]), len(order)))
    return _decrypt_row(row, account_id=account_id, sec=_service(secret_service))


_REJECT_CODES: Final[frozenset[int]] = frozenset({401, 403})
_REJECT_MARKERS: Final[tuple[str, ...]] = (
    "api key not valid",
    "api_key_invalid",
    "invalid api key",
    "api key expired",
    "permission_denied",
    "unauthenticated",
)


def _is_key_rejection(exc: BaseException) -> bool:
    """인증·권한 거부(401/403, API key invalid 계열)만 참. 나머지(네트워크·타임아웃·429·5xx·그 밖)는 거짓."""
    code = getattr(exc, "code", None)
    if isinstance(code, int) and not isinstance(code, bool):
        if code in _REJECT_CODES:
            return True
        if code != 400:
            return False
    text = str(exc).lower()
    return any(marker in text for marker in _REJECT_MARKERS)


def verify_key(provider: str, api_key: str) -> tuple[bool, str]:
    """시험 호출. ``(ok, message)``. ``message``는 ``secret_scrub.scrub_text``를 거친 사용자용 문구다.

    gemini는 ``provider_manager``의 ``count_tokens`` 시험과 같은 방식을 쓴다. 테스트는 이 함수를 monkeypatch한다.
    ``(False, msg)``는 인증·권한 거부일 때만 돌려준다. 판정하지 못한 실패(네트워크 오류·타임아웃·429·5xx 등)는
    ``ProviderKeyVerifyUnavailable``을 낸다.
    """
    register_secret(api_key)
    if provider != "gemini":
        return False, "지원하지 않는 프로바이더입니다."
    model_name = VERIFY_MODELS[provider]
    try:
        from google import genai

        client = genai.Client(api_key=api_key)
        client.models.count_tokens(model=model_name, contents="ping")
    except Exception as exc:  # noqa: BLE001  # 프로바이더 오류는 거부와 판정 불가로 나눈다
        detail = scrub_text(f"{type(exc).__name__}: {exc}", extra=(api_key,))
        if len(detail) > 300:
            detail = detail[:300] + "…"
        if _is_key_rejection(exc):
            return False, scrub_text(f"Gemini API 키 확인 실패: {detail}", extra=(api_key,))
        raise ProviderKeyVerifyUnavailable(
            scrub_text(f"Gemini API 키를 지금 확인할 수 없습니다: {detail}", extra=(api_key,))
        ) from None
    return True, f"Gemini API 키 확인 성공 (모델: {model_name})"
