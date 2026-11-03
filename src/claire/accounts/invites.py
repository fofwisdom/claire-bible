"""Invitation code generation, normalization, and validation rules."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from typing import Final, Literal

# Crockford Base32 alphabet: 32 chars, excludes I, L, O, U
CROCKFORD_ALPHABET: Final[str] = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
INVITE_CODE_LENGTH: Final[int] = 8
INVITE_CODE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9A-HJKMNP-TV-Z]{8}$")

MIN_MAX_USES: Final[int] = 1
MAX_MAX_USES: Final[int] = 5
MIN_EXPIRATION_SECONDS: Final[int] = 30 * 60  # 30 minutes
MAX_EXPIRATION_SECONDS: Final[int] = 30 * 86400  # 30 days


class InviteError(Exception):
    """Base exception for invitation errors."""


class InviteValidationError(InviteError):
    """Raised when an invitation code or parameters fail validation."""


class InviteNotFoundError(InviteError):
    """Raised when an invitation code does not exist (404)."""


class InviteInactiveError(InviteError):
    """Raised when an invitation code is expired, exhausted, or revoked (410)."""

    def __init__(self, reason: Literal["expired", "exhausted", "revoked"]) -> None:
        super().__init__(f"Invitation is inactive: {reason}")
        self.reason = reason


@dataclass(frozen=True, slots=True)
class InvitationInfo:
    id: int
    public_id: int
    code_masked: str  # or plaintext when viewing
    created_by: int
    created_at: float
    expires_at: float
    max_uses: int
    uses_count: int
    reserved_username: str | None
    note: str
    revoked_at: float | None
    revoked_by: int | None
    code: str = ""

    @property
    def is_active(self) -> bool:
        import time
        now = time.time()
        return (
            self.revoked_at is None
            and self.uses_count < self.max_uses
            and self.expires_at > now
        )

    @property
    def status(self) -> Literal["active", "expired", "exhausted", "revoked"]:
        import time
        if self.revoked_at is not None:
            return "revoked"
        if self.uses_count >= self.max_uses:
            return "exhausted"
        if self.expires_at <= time.time():
            return "expired"
        return "active"


def normalize_invite_code(code: str) -> str:
    """Normalize invite code: case-insensitive, O->0, I/L->1, strip whitespace and hyphens."""
    normalized = "".join(code.split()).upper().replace("-", "")
    normalized = normalized.replace("O", "0").replace("I", "1").replace("L", "1")
    return normalized


def validate_invite_code_syntax(normalized_code: str) -> bool:
    """Check whether a normalized invite code conforms to the 8-char Crockford Base32 pattern."""
    return bool(INVITE_CODE_PATTERN.match(normalized_code))


def generate_invite_code() -> str:
    """Generate a cryptographically random 8-character Crockford Base32 code (40 bits of entropy)."""
    return "".join(secrets.choice(CROCKFORD_ALPHABET) for _ in range(INVITE_CODE_LENGTH))


def mask_invite_code(code: str) -> str:
    """Mask invite code for safe display."""
    norm = normalize_invite_code(code)
    if len(norm) < 4:
        return "****"
    return f"{norm[:2]}****{norm[-2:]}"


def validate_invite_params(max_uses: int, expires_in_seconds: float, reserved_username: str | None = None) -> None:
    """Validate invitation creation parameters."""
    if not (MIN_MAX_USES <= max_uses <= MAX_MAX_USES):
        raise InviteValidationError(f"max_uses must be between {MIN_MAX_USES} and {MAX_MAX_USES}")
    if not (MIN_EXPIRATION_SECONDS <= expires_in_seconds <= MAX_EXPIRATION_SECONDS):
        raise InviteValidationError(f"expires_in must be between {MIN_EXPIRATION_SECONDS}s and {MAX_EXPIRATION_SECONDS}s")
    if reserved_username is not None and max_uses != 1:
        raise InviteValidationError("reserved_username is only allowed when max_uses=1")
