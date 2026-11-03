"""Unit tests for OIDC core utilities and GitHub adapter.

Tests module interfaces, external cryptographic libraries, and HTTP transport interactions.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa, utils

from claire.api.oidc import (
    ExternalIdentity,
    GitHubAdapter,
    IdpProviderMeta,
    OidcClient,
    OidcDiscoveryError,
    OidcTokenError,
    OidcVerificationError,
    generate_pkce_pair,
    jwk_to_public_key,
    verify_pkce,
    verify_signature,
)


def _rsa_to_jwk(pub_key: rsa.RSAPublicKey, kid: str = "test-rsa-key") -> dict[str, str]:
    """Export an RSA public key to a JWK dict."""
    pn = pub_key.public_numbers()
    n_bytes = pn.n.to_bytes((pn.n.bit_length() + 7) // 8, byteorder="big")
    e_bytes = pn.e.to_bytes((pn.e.bit_length() + 7) // 8, byteorder="big")
    n_b64 = base64.urlsafe_b64encode(n_bytes).decode("ascii").rstrip("=")
    e_b64 = base64.urlsafe_b64encode(e_bytes).decode("ascii").rstrip("=")
    return {"kty": "RSA", "alg": "RS256", "use": "sig", "kid": kid, "n": n_b64, "e": e_b64}


def _ec_to_jwk(pub_key: ec.EllipticCurvePublicKey, kid: str = "test-ec-key") -> dict[str, str]:
    """Export an EC P-256 public key to a JWK dict."""
    pn = pub_key.public_numbers()
    x_bytes = pn.x.to_bytes(32, byteorder="big")
    y_bytes = pn.y.to_bytes(32, byteorder="big")
    x_b64 = base64.urlsafe_b64encode(x_bytes).decode("ascii").rstrip("=")
    y_b64 = base64.urlsafe_b64encode(y_bytes).decode("ascii").rstrip("=")
    return {"kty": "EC", "crv": "P-256", "alg": "ES256", "use": "sig", "kid": kid, "x": x_b64, "y": y_b64}


def _make_jwt(
    payload: dict[str, Any],
    priv_key: Any,
    *,
    alg: str = "RS256",
    kid: str = "test-rsa-key",
) -> str:
    """Create a signed JWT string using an RSA or EC private key."""
    header = {"alg": alg, "typ": "JWT", "kid": kid}
    h_b64 = base64.urlsafe_b64encode(json.dumps(header).encode("utf-8")).decode("ascii").rstrip("=")
    p_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii").rstrip("=")
    signing_input = f"{h_b64}.{p_b64}".encode("ascii")

    if alg == "RS256":
        sig = priv_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    elif alg == "ES256":
        der_sig = priv_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
        r, s = utils.decode_dss_signature(der_sig)
        sig = r.to_bytes(32, byteorder="big") + s.to_bytes(32, byteorder="big")
    else:
        raise ValueError(f"Unsupported test alg: {alg}")

    s_b64 = base64.urlsafe_b64encode(sig).decode("ascii").rstrip("=")
    return f"{h_b64}.{p_b64}.{s_b64}"


def test_pkce_generation_and_validation() -> None:
    """Test PKCE generation produces a valid verifier and matching S256 challenge."""
    verifier, challenge = generate_pkce_pair()
    assert len(verifier) >= 43
    assert len(challenge) >= 43
    assert verify_pkce(verifier, challenge) is True

    # Bad verifier or corrupted challenge
    assert verify_pkce("wrong-verifier", challenge) is False
    assert verify_pkce(verifier, "wrong-challenge") is False
    assert verify_pkce("", challenge) is False
    assert verify_pkce(verifier, "") is False


def test_jwk_rsa_conversion_and_signature_verification() -> None:
    """Test RSA JWK conversion and cryptographic RS256 signature verification."""
    priv_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_key = priv_key.public_key()
    jwk = _rsa_to_jwk(pub_key)

    converted_key = jwk_to_public_key(jwk)
    assert isinstance(converted_key, rsa.RSAPublicKey)

    data = b"message-to-sign"
    sig = priv_key.sign(data, padding.PKCS1v15(), hashes.SHA256())

    # Verify with converted key and JWK dict
    verify_signature(data, sig, "RS256", converted_key)
    verify_signature(data, sig, "RS256", jwk)

    # Tampered data or signature must raise OidcVerificationError
    with pytest.raises(OidcVerificationError, match="Signature verification failed"):
        verify_signature(b"tampered-data", sig, "RS256", converted_key)


def test_jwk_ec_conversion_and_signature_verification() -> None:
    """Test EC JWK conversion and cryptographic ES256 raw signature verification."""
    priv_key = ec.generate_private_key(ec.SECP256R1())
    pub_key = priv_key.public_key()
    jwk = _ec_to_jwk(pub_key)

    converted_key = jwk_to_public_key(jwk)
    assert isinstance(converted_key, ec.EllipticCurvePublicKey)

    data = b"message-to-sign-with-ec"
    der_sig = priv_key.sign(data, ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(der_sig)
    raw_sig = r.to_bytes(32, byteorder="big") + s.to_bytes(32, byteorder="big")

    verify_signature(data, raw_sig, "ES256", converted_key)
    verify_signature(data, raw_sig, "ES256", jwk)

    # Corrupted signature must raise OidcVerificationError
    corrupted_sig = bytearray(raw_sig)
    corrupted_sig[0] ^= 0xFF
    with pytest.raises(OidcVerificationError, match="Signature verification failed"):
        verify_signature(data, bytes(corrupted_sig), "ES256", converted_key)


def test_jwk_conversion_invalid_inputs() -> None:
    """Test that unsupported or malformed JWK inputs raise OidcVerificationError."""
    with pytest.raises(OidcVerificationError, match="Unsupported key type"):
        jwk_to_public_key({"kty": "OCT"})

    with pytest.raises(OidcVerificationError, match="missing required 'n' or 'e'"):
        jwk_to_public_key({"kty": "RSA", "n": "abc"})

    with pytest.raises(OidcVerificationError, match="Unsupported EC curve"):
        jwk_to_public_key({"kty": "EC", "crv": "P-384", "x": "abc", "y": "def"})

    with pytest.raises(OidcVerificationError, match="missing required 'x' or 'y'"):
        jwk_to_public_key({"kty": "EC", "crv": "P-256", "x": "abc"})


@pytest.mark.asyncio
async def test_oidc_client_discovery_and_caching() -> None:
    """Test discovery document retrieval and 24-hour cache behavior."""
    discovery_calls = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal discovery_calls
        if request.url.path == "/.well-known/openid-configuration":
            discovery_calls += 1
            return httpx.Response(200, json={
                "issuer": "https://idp.example.com",
                "authorization_endpoint": "https://idp.example.com/auth",
                "token_endpoint": "https://idp.example.com/token",
                "jwks_uri": "https://idp.example.com/jwks",
            })
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    provider = IdpProviderMeta(
        id="test-provider",
        type="oidc",
        display_name="Test Provider",
        client_id="client-123",
        issuer="https://idp.example.com",
    )
    oidc = OidcClient(provider=provider, client_secret="secret-abc", http_client=client)

    doc1 = await oidc.get_discovery_doc()
    assert doc1["authorization_endpoint"] == "https://idp.example.com/auth"
    assert discovery_calls == 1

    # Second call should use cache
    doc2 = await oidc.get_discovery_doc()
    assert doc2["token_endpoint"] == "https://idp.example.com/token"
    assert discovery_calls == 1


@pytest.mark.asyncio
async def test_oidc_client_authorization_url_building() -> None:
    """Test authorization URL creation with query parameters and optional hosted domain."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "issuer": "https://accounts.google.com",
            "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_endpoint": "https://oauth2.googleapis.com/token",
            "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    provider = IdpProviderMeta(
        id="google",
        type="oidc",
        display_name="Google",
        client_id="google-client-id",
        issuer="https://accounts.google.com",
        scopes=("openid", "email", "profile"),
        hosted_domain="example.org",
    )
    oidc = OidcClient(provider=provider, client_secret="secret", http_client=client)

    url = await oidc.create_authorization_url(
        redirect_uri="https://app.example.com/auth/oidc/google/callback",
        state="state-xyz",
        nonce="nonce-123",
        code_challenge="challenge-s256",
    )

    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "accounts.google.com"
    qs = parse_qs(parsed.query)
    assert qs["client_id"] == ["google-client-id"]
    assert qs["redirect_uri"] == ["https://app.example.com/auth/oidc/google/callback"]
    assert qs["state"] == ["state-xyz"]
    assert qs["nonce"] == ["nonce-123"]
    assert qs["code_challenge"] == ["challenge-s256"]
    assert qs["code_challenge_method"] == ["S256"]
    assert qs["hd"] == ["example.org"]


@pytest.mark.asyncio
async def test_oidc_client_exchange_code() -> None:
    """Test token endpoint code exchange via POST request."""
    posted_data: dict[str, str] = {}

    def mock_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(200, json={
                "issuer": "https://idp.example.com",
                "authorization_endpoint": "https://idp.example.com/auth",
                "token_endpoint": "https://idp.example.com/token",
                "jwks_uri": "https://idp.example.com/jwks",
            })
        if request.url.path == "/token":
            body = request.read().decode("utf-8")
            posted_data.update(parse_qs(body))
            return httpx.Response(200, json={"id_token": "mock.jwt.token", "access_token": "access-123"})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    provider = IdpProviderMeta(
        id="test-provider",
        type="oidc",
        display_name="Test Provider",
        client_id="client-id-abc",
        issuer="https://idp.example.com",
    )
    oidc = OidcClient(provider=provider, client_secret="my-secret", http_client=client)

    result = await oidc.exchange_code(
        code="auth-code-789",
        redirect_uri="https://app.example.com/callback",
        code_verifier="verifier-xyz",
    )

    assert result["id_token"] == "mock.jwt.token"
    assert posted_data["grant_type"] == ["authorization_code"]
    assert posted_data["client_id"] == ["client-id-abc"]
    assert posted_data["client_secret"] == ["my-secret"]
    assert posted_data["code"] == ["auth-code-789"]
    assert posted_data["code_verifier"] == ["verifier-xyz"]


@pytest.mark.asyncio
async def test_oidc_client_verify_id_token_success() -> None:
    """Test full ID token signature verification and claim validation with RS256 and ES256."""
    rsa_priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    rsa_jwk = _rsa_to_jwk(rsa_priv.public_key(), kid="rsa-key-1")

    ec_priv = ec.generate_private_key(ec.SECP256R1())
    ec_jwk = _ec_to_jwk(ec_priv.public_key(), kid="ec-key-1")

    def mock_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(200, json={
                "issuer": "https://idp.example.com",
                "authorization_endpoint": "https://idp.example.com/auth",
                "token_endpoint": "https://idp.example.com/token",
                "jwks_uri": "https://idp.example.com/jwks",
            })
        if request.url.path == "/jwks":
            return httpx.Response(200, json={"keys": [rsa_jwk, ec_jwk]})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    provider = IdpProviderMeta(
        id="test-provider",
        type="oidc",
        display_name="Test Provider",
        client_id="my-client-app",
        issuer="https://idp.example.com",
    )
    oidc = OidcClient(provider=provider, client_secret="secret", http_client=client)

    now = time.time()
    rsa_claims = {
        "iss": "https://idp.example.com",
        "sub": "user-sub-1001",
        "aud": "my-client-app",
        "azp": "my-client-app",
        "exp": now + 300,
        "iat": now - 10,
        "nonce": "test-nonce-1",
        "email": "user@example.com",
        "email_verified": True,
        "name": "Jane Doe",
    }
    rsa_token = _make_jwt(rsa_claims, rsa_priv, alg="RS256", kid="rsa-key-1")

    identity = await oidc.verify_id_token(rsa_token, expected_nonce="test-nonce-1")
    assert isinstance(identity, ExternalIdentity)
    assert identity.iss == "https://idp.example.com"
    assert identity.sub == "user-sub-1001"
    assert identity.email == "user@example.com"
    assert identity.email_verified is True
    assert identity.display_name == "Jane Doe"

    # Now verify ES256 token
    ec_claims = {
        "iss": "https://idp.example.com",
        "sub": "user-sub-ec-2002",
        "aud": "my-client-app",
        "exp": now + 300,
        "iat": now - 5,
        "nonce": "test-nonce-2",
        "email": "ecuser@example.com",
        "email_verified": True,
        "preferred_username": "ec_expert",
    }
    ec_token = _make_jwt(ec_claims, ec_priv, alg="ES256", kid="ec-key-1")

    ec_identity = await oidc.verify_id_token(ec_token, expected_nonce="test-nonce-2")
    assert ec_identity.sub == "user-sub-ec-2002"
    assert ec_identity.display_name == "ec_expert"


@pytest.mark.asyncio
async def test_oidc_client_verify_id_token_validation_failures() -> None:
    """Test claim validation errors: expired, future iat, nonce, issuer, and audience mismatch."""
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = _rsa_to_jwk(priv.public_key(), kid="k1")

    def mock_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(200, json={
                "issuer": "https://idp.example.com",
                "jwks_uri": "https://idp.example.com/jwks",
            })
        if request.url.path == "/jwks":
            return httpx.Response(200, json={"keys": [jwk]})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    provider = IdpProviderMeta(
        id="test",
        type="oidc",
        display_name="Test",
        client_id="client-target",
        issuer="https://idp.example.com",
        hosted_domain="company.com",
        tenant_id="tenant-999",
    )
    oidc = OidcClient(provider=provider, client_secret="secret", http_client=client)

    now = time.time()
    base_claims = {
        "iss": "https://idp.example.com",
        "sub": "sub-1",
        "aud": "client-target",
        "exp": now + 300,
        "iat": now - 5,
        "nonce": "nonce-correct",
        "hd": "company.com",
        "tid": "tenant-999",
    }

    # Expired token
    expired_claims = dict(base_claims, exp=now - 120)
    tok = _make_jwt(expired_claims, priv, kid="k1")
    with pytest.raises(OidcVerificationError, match="has expired"):
        await oidc.verify_id_token(tok, expected_nonce="nonce-correct")

    # Future iat
    future_claims = dict(base_claims, iat=now + 120)
    tok = _make_jwt(future_claims, priv, kid="k1")
    with pytest.raises(OidcVerificationError, match="issued in the future"):
        await oidc.verify_id_token(tok, expected_nonce="nonce-correct")

    # Nonce mismatch
    tok = _make_jwt(base_claims, priv, kid="k1")
    with pytest.raises(OidcVerificationError, match="Nonce mismatch"):
        await oidc.verify_id_token(tok, expected_nonce="nonce-wrong")

    # Issuer mismatch
    bad_iss = dict(base_claims, iss="https://other.example.com")
    tok = _make_jwt(bad_iss, priv, kid="k1")
    with pytest.raises(OidcVerificationError, match="Issuer mismatch"):
        await oidc.verify_id_token(tok, expected_nonce="nonce-correct")

    # Audience mismatch
    bad_aud = dict(base_claims, aud="wrong-audience")
    tok = _make_jwt(bad_aud, priv, kid="k1")
    with pytest.raises(OidcVerificationError, match="Audience mismatch"):
        await oidc.verify_id_token(tok, expected_nonce="nonce-correct")

    # Hosted domain mismatch
    bad_hd = dict(base_claims, hd="other-domain.com")
    tok = _make_jwt(bad_hd, priv, kid="k1")
    with pytest.raises(OidcVerificationError, match="Hosted domain mismatch"):
        await oidc.verify_id_token(tok, expected_nonce="nonce-correct")

    # Tenant ID mismatch
    bad_tid = dict(base_claims, tid="tenant-wrong")
    tok = _make_jwt(bad_tid, priv, kid="k1")
    with pytest.raises(OidcVerificationError, match="Tenant ID mismatch"):
        await oidc.verify_id_token(tok, expected_nonce="nonce-correct")

    # Authorized party (azp) mismatch
    bad_azp = dict(base_claims, azp="some-other-app")
    tok = _make_jwt(bad_azp, priv, kid="k1")
    with pytest.raises(OidcVerificationError, match="Authorized party mismatch"):
        await oidc.verify_id_token(tok, expected_nonce="nonce-correct")

    # Missing sub claim
    bad_sub = dict(base_claims)
    bad_sub.pop("sub")
    tok = _make_jwt(bad_sub, priv, kid="k1")
    with pytest.raises(OidcVerificationError, match="Missing 'sub' claim"):
        await oidc.verify_id_token(tok, expected_nonce="nonce-correct")

    # Tampered signature bytes
    valid_tok = _make_jwt(base_claims, priv, kid="k1")
    parts = valid_tok.split(".")
    tampered_sig = base64.urlsafe_b64encode(b"wrong-sig-bytes" * 5).decode().rstrip("=")
    corrupted_token = f"{parts[0]}.{parts[1]}.{tampered_sig}"
    with pytest.raises(OidcVerificationError, match="Signature verification failed"):
        await oidc.verify_id_token(corrupted_token, expected_nonce="nonce-correct")

    # Audience as list
    list_aud_claims = dict(base_claims, aud=["other-app", "client-target"])
    tok = _make_jwt(list_aud_claims, priv, kid="k1")
    res = await oidc.verify_id_token(tok, expected_nonce="nonce-correct")
    assert res.sub == "sub-1"

    bad_list_aud_claims = dict(base_claims, aud=["other-app", "yet-another-app"])
    tok = _make_jwt(bad_list_aud_claims, priv, kid="k1")
    with pytest.raises(OidcVerificationError, match="Audience mismatch"):
        await oidc.verify_id_token(tok, expected_nonce="nonce-correct")

    # Malformed JWT structure
    with pytest.raises(OidcVerificationError, match="must have three segments"):
        await oidc.verify_id_token("invalid.token", expected_nonce="nonce-correct")


@pytest.mark.asyncio
async def test_oidc_client_kid_rotation_and_recovery() -> None:
    """Test that a missing kid triggers a JWKS force refresh and successfully validates rotated keys."""
    key_v1 = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_v2 = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk_v1 = _rsa_to_jwk(key_v1.public_key(), kid="kid-v1")
    jwk_v2 = _rsa_to_jwk(key_v2.public_key(), kid="kid-v2")

    jwks_call_count = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal jwks_call_count
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(200, json={
                "issuer": "https://idp.example.com",
                "jwks_uri": "https://idp.example.com/jwks",
            })
        if request.url.path == "/jwks":
            jwks_call_count += 1
            if jwks_call_count == 1:
                # Initial JWKS only has old key v1
                return httpx.Response(200, json={"keys": [jwk_v1]})
            # Rotated JWKS has key v2
            return httpx.Response(200, json={"keys": [jwk_v1, jwk_v2]})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    provider = IdpProviderMeta(
        id="test",
        type="oidc",
        display_name="Test",
        client_id="app-1",
        issuer="https://idp.example.com",
    )
    oidc = OidcClient(provider=provider, client_secret="secret", http_client=client)

    now = time.time()
    claims = {
        "iss": "https://idp.example.com",
        "sub": "user-rotated",
        "aud": "app-1",
        "exp": now + 300,
        "iat": now - 5,
        "nonce": "nonce-rot",
    }
    token_v2 = _make_jwt(claims, key_v2, kid="kid-v2")

    # Initial call loads jwk_v1. Missing kid-v2 triggers refresh to load jwk_v2.
    identity = await oidc.verify_id_token(token_v2, expected_nonce="nonce-rot")
    assert identity.sub == "user-rotated"
    assert jwks_call_count == 2

    # If key still missing after refresh, raise error
    token_unknown = _make_jwt(claims, key_v2, kid="kid-does-not-exist")
    with pytest.raises(OidcVerificationError, match="not found in provider JWKS"):
        await oidc.verify_id_token(token_unknown, expected_nonce="nonce-rot")


@pytest.mark.asyncio
async def test_github_adapter_authorization_url_and_identity_exchange() -> None:
    """Test GitHub adapter authorization URL generation and identity exchange."""
    provider = IdpProviderMeta(
        id="github",
        type="github",
        display_name="GitHub",
        client_id="gh-client-id",
        enabled=True,
    )
    adapter = GitHubAdapter(provider=provider, client_secret="gh-secret")

    # 1. Authorization URL
    auth_url = adapter.create_authorization_url(
        redirect_uri="https://claire.example.com/auth/oidc/github/callback",
        state="gh-state-123",
    )
    parsed = urlparse(auth_url)
    assert parsed.netloc == "github.com"
    assert parsed.path == "/login/oauth/authorize"
    qs = parse_qs(parsed.query)
    assert qs["client_id"] == ["gh-client-id"]
    assert qs["redirect_uri"] == ["https://claire.example.com/auth/oidc/github/callback"]
    assert qs["state"] == ["gh-state-123"]
    assert qs["scope"] == ["read:user user:email"]

    # 2. Exchange code with mocked HTTP transport
    def mock_handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "github.com" and request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "gho_test_token_abc"})
        if request.url.host == "api.github.com" and request.url.path == "/user":
            auth_h = request.headers.get("authorization", "")
            if auth_h != "Bearer gho_test_token_abc":
                return httpx.Response(401, json={"message": "Bad credentials"})
            return httpx.Response(200, json={
                "id": 987654,
                "login": "octocat",
                "name": "Mona Lisa Octocat",
                "email": "octocat@github.com",
            })
        if request.url.host == "api.github.com" and request.url.path == "/user/emails":
            return httpx.Response(200, json=[
                {"email": "unverified@example.com", "primary": False, "verified": False},
                {"email": "primary@example.com", "primary": True, "verified": True},
            ])
        return httpx.Response(404)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    adapter_with_mock = GitHubAdapter(provider=provider, client_secret="gh-secret", http_client=mock_client)

    identity = await adapter_with_mock.exchange_code_and_get_identity(
        code="temp-github-code",
        redirect_uri="https://claire.example.com/auth/oidc/github/callback",
    )

    assert isinstance(identity, ExternalIdentity)
    assert identity.iss == "https://github.com"
    assert identity.sub == "987654"
    assert identity.display_name == "Mona Lisa Octocat"
    assert identity.email == "primary@example.com"
    assert identity.email_verified is True

    # 3. GitHub user fallback when user/emails has no verified email
    def mock_unverified_handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "github.com" and request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "gho_token_unverified"})
        if request.url.host == "api.github.com" and request.url.path == "/user":
            return httpx.Response(200, json={"id": 112233, "login": "testuser", "name": None, "email": "fallback@example.com"})
        if request.url.host == "api.github.com" and request.url.path == "/user/emails":
            return httpx.Response(200, json=[{"email": "unverified@example.com", "primary": True, "verified": False}])
        return httpx.Response(404)

    mock_client2 = httpx.AsyncClient(transport=httpx.MockTransport(mock_unverified_handler))
    adapter2 = GitHubAdapter(provider=provider, client_secret="gh-secret", http_client=mock_client2)
    identity2 = await adapter2.exchange_code_and_get_identity(
        code="code-2",
        redirect_uri="https://claire.example.com/callback",
    )
    assert identity2.sub == "112233"
    assert identity2.display_name == "testuser"
    assert identity2.email == "unverified@example.com"
    assert identity2.email_verified is False


@pytest.mark.asyncio
async def test_github_adapter_error_handling() -> None:
    """Test GitHub adapter error handling on invalid code or error response."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"error": "bad_verification_code", "error_description": "The code passed is incorrect or expired."})
        return httpx.Response(404)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    provider = IdpProviderMeta(
        id="github",
        type="github",
        display_name="GitHub",
        client_id="gh-client-id",
    )
    adapter = GitHubAdapter(provider=provider, client_secret="gh-secret", http_client=mock_client)

    with pytest.raises(OidcTokenError, match="bad_verification_code"):
        await adapter.exchange_code_and_get_identity(
            code="expired-code",
            redirect_uri="https://claire.example.com/callback",
        )
