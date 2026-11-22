"""Web Push protocol: message encryption (RFC 8291, content coding ``aes128gcm`` of RFC 8188) and the VAPID authorization header (RFC 8292).

- This module has no network call and no database call. ``build_request`` gives the headers and the body of one push request; the sender (``sender.py``) sends it.
- The push service does not see the content of a message: the browser gave the public key and the authentication secret of the subscription, and only that browser can decrypt the body.
- The VAPID key pair is one ECDSA P-256 key pair for the server (``store/notifications.py`` keeps it). The public key is the ``applicationServerKey`` of each subscription.
"""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

__all__ = [
    "MAX_PLAINTEXT_BYTES",
    "PushRequest",
    "WebPushError",
    "b64u_decode",
    "b64u_encode",
    "build_request",
    "encrypt_payload",
    "endpoint_audience",
    "generate_vapid_private_key",
    "load_vapid_private_key",
    "vapid_authorization",
    "vapid_public_key",
]

# RFC 8188: one record of 4096 bytes. The header is 86 bytes (salt 16, record size 4, key ID length 1, key ID 65); the record has the padding delimiter (1 byte) and the tag (16 bytes). Push services accept a body of 4096 bytes.
RECORD_SIZE: Final[int] = 4096
MAX_PLAINTEXT_BYTES: Final[int] = RECORD_SIZE - 86 - 17
# RFC 8292 Section 2: ``exp`` must not be more than 24 hours after the request.
VAPID_LIFETIME_SECONDS: Final[int] = 12 * 3600
_UNCOMPRESSED_POINT_BYTES: Final[int] = 65
_AUTH_SECRET_BYTES: Final[int] = 16
URGENCIES: Final[tuple[str, ...]] = ("very-low", "low", "normal", "high")


class WebPushError(ValueError):
    """A key, a subscription value, or a payload that this module cannot use."""


@dataclass(frozen=True, slots=True)
class PushRequest:
    """One push request: ``POST endpoint`` with ``headers`` and ``body``."""

    endpoint: str
    headers: dict[str, str]
    body: bytes


def b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64u_decode(text: str) -> bytes:
    """Decode base64url text without padding. Browsers give subscription keys in this form; the standard alphabet is also accepted."""
    if not isinstance(text, str) or not text:
        raise WebPushError("base64url text is necessary")
    cleaned = text.strip().replace("+", "-").replace("/", "_").rstrip("=")
    try:
        return base64.urlsafe_b64decode(cleaned + "=" * (-len(cleaned) % 4))
    except (ValueError, TypeError) as exc:
        raise WebPushError("text is not base64url") from exc


def generate_vapid_private_key() -> str:
    """A new P-256 private key as base64url text of the 32-byte scalar."""
    key = ec.generate_private_key(ec.SECP256R1())
    return b64u_encode(key.private_numbers().private_value.to_bytes(32, "big"))


def load_vapid_private_key(private_b64u: str) -> ec.EllipticCurvePrivateKey:
    raw = b64u_decode(private_b64u)
    if len(raw) != 32:
        raise WebPushError("a P-256 private key has 32 bytes")
    try:
        return ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())
    except ValueError as exc:
        raise WebPushError("the private key is not a P-256 scalar") from exc


def _public_point(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def vapid_public_key(private_b64u: str) -> str:
    """The public key of a VAPID private key: base64url text of the uncompressed point (65 bytes). This is the ``applicationServerKey`` of ``PushManager.subscribe``."""
    return b64u_encode(_public_point(load_vapid_private_key(private_b64u).public_key()))


def _load_ua_public(p256dh: str) -> tuple[ec.EllipticCurvePublicKey, bytes]:
    raw = b64u_decode(p256dh)
    if len(raw) != _UNCOMPRESSED_POINT_BYTES or raw[0] != 0x04:
        raise WebPushError("p256dh is not an uncompressed P-256 point")
    try:
        return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw), raw
    except ValueError as exc:
        raise WebPushError("p256dh is not a point of the P-256 curve") from exc


def check_subscription_keys(p256dh: str, auth: str) -> None:
    """Raise ``WebPushError`` when the keys of a subscription cannot encrypt a message."""
    _load_ua_public(p256dh)
    if len(b64u_decode(auth)) != _AUTH_SECRET_BYTES:
        raise WebPushError("the authentication secret has 16 bytes")


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def encrypt_payload(plaintext: bytes, *, p256dh: str, auth: str, salt: bytes | None = None, sender_key: ec.EllipticCurvePrivateKey | None = None) -> bytes:
    """Encrypt ``plaintext`` for one subscription (RFC 8291 Section 3.4). The result is the full body with the ``aes128gcm`` header.

    ``salt`` and ``sender_key`` are random for each message. A test gives them to compare the result with the example of RFC 8291 Section 5.
    """
    if len(plaintext) > MAX_PLAINTEXT_BYTES:
        raise WebPushError(f"the payload has {len(plaintext)} bytes; the limit is {MAX_PLAINTEXT_BYTES}")
    ua_public, ua_public_raw = _load_ua_public(p256dh)
    auth_secret = b64u_decode(auth)
    if len(auth_secret) != _AUTH_SECRET_BYTES:
        raise WebPushError("the authentication secret has 16 bytes")
    if salt is None:
        salt = os.urandom(16)
    elif len(salt) != 16:
        raise WebPushError("the salt has 16 bytes")
    if sender_key is None:
        sender_key = ec.generate_private_key(ec.SECP256R1())
    sender_public_raw = _public_point(sender_key.public_key())
    ecdh_secret = sender_key.exchange(ec.ECDH(), ua_public)
    ikm = _hkdf(auth_secret, ecdh_secret, b"WebPush: info\x00" + ua_public_raw + sender_public_raw, 32)
    content_key = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    # 0x02 is the padding delimiter of the last record (RFC 8188 Section 2).
    record = AESGCM(content_key).encrypt(nonce, plaintext + b"\x02", None)
    header = salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(sender_public_raw)]) + sender_public_raw
    return header + record


def endpoint_audience(endpoint: str) -> str:
    """The origin of a push endpoint: the ``aud`` claim of the VAPID token. Only ``https`` is accepted (RFC 8030 Section 8)."""
    parts = urlsplit(endpoint)
    if parts.scheme != "https" or not parts.hostname:
        raise WebPushError("a push endpoint is an https URL")
    return f"https://{parts.netloc}"


def _jwt_part(value: dict[str, object]) -> str:
    return b64u_encode(json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8"))


def vapid_authorization(private_b64u: str, *, audience: str, subject: str, now: float | None = None) -> str:
    """The value of the ``Authorization`` header (RFC 8292 Section 3): ``vapid t=<JWT>, k=<public key>``. The JWT has the algorithm ES256 and the claims ``aud``, ``exp``, and ``sub``."""
    if not subject:
        raise WebPushError("the VAPID subject is necessary")
    key = load_vapid_private_key(private_b64u)
    issued = time.time() if now is None else now
    signing_input = f"{_jwt_part({'alg': 'ES256', 'typ': 'JWT'})}.{_jwt_part({'aud': audience, 'exp': int(issued) + VAPID_LIFETIME_SECONDS, 'sub': subject})}"
    r, s = decode_dss_signature(key.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256())))
    signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return f"vapid t={signing_input}.{b64u_encode(signature)}, k={b64u_encode(_public_point(key.public_key()))}"


def build_request(*, endpoint: str, p256dh: str, auth: str, payload: bytes, vapid_private_key: str, subject: str, ttl: int, urgency: str, topic: str | None = None, now: float | None = None) -> PushRequest:
    """One push request for one subscription (RFC 8030 Section 5).

    ``ttl`` is the time in seconds that the push service keeps the message for a browser that is not connected. ``topic`` (32 characters of the base64url alphabet at most) makes a new message replace a waiting message with the same topic.
    """
    if urgency not in URGENCIES:
        raise WebPushError(f"urgency must be one of {URGENCIES}")
    if ttl < 0:
        raise WebPushError("ttl must be 0 or more")
    headers = {
        "Authorization": vapid_authorization(vapid_private_key, audience=endpoint_audience(endpoint), subject=subject, now=now),
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": str(int(ttl)),
        "Urgency": urgency,
    }
    if topic is not None:
        if not topic or len(topic) > 32 or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for c in topic):
            raise WebPushError("topic must have 1 to 32 characters of the base64url alphabet")
        headers["Topic"] = topic
    return PushRequest(endpoint=endpoint, headers=headers, body=encrypt_payload(payload, p256dh=p256dh, auth=auth))
