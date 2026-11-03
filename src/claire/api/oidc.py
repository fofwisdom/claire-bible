"""OIDC and GitHub OAuth authentication helper module.

This module provides OpenID Connect (OIDC) client tools and a GitHub OAuth adapter.
It supports PKCE parameter generation and validation, JWK key conversion, signature verification, token exchange, and ID token validation.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa, utils
import httpx

from ..store.auth_db import IdpProviderMeta

log = logging.getLogger("claire.api.oidc")


class OidcError(Exception):
    """Base exception for all OIDC errors."""


class OidcDiscoveryError(OidcError):
    """Exception raised when discovery or JWKS retrieval fails."""


class OidcTokenError(OidcError):
    """Exception raised when token exchange fails."""


class OidcVerificationError(OidcError):
    """Exception raised when token verification or claim validation fails."""


@dataclass(frozen=True, slots=True)
class ExternalIdentity:
    """External identity information obtained from an IdP."""

    iss: str
    sub: str
    email: str | None = None
    email_verified: bool = False
    display_name: str | None = None


def generate_pkce_pair() -> tuple[str, str]:
    """Generate a random PKCE verifier and its S256 code challenge."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def verify_pkce(code_verifier: str, code_challenge: str) -> bool:
    """Verify that a code verifier matches the S256 code challenge."""
    if not code_verifier or not code_challenge:
        return False
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return hmac.compare_digest(computed, code_challenge)


def _base64url_decode(data: str) -> bytes:
    """Decode an unpadded base64url string into bytes."""
    clean = data.rstrip("=")
    padding_needed = (4 - len(clean) % 4) % 4
    return base64.urlsafe_b64decode((clean + ("=" * padding_needed)).encode("ascii"))


def jwk_to_public_key(jwk: dict[str, Any]) -> rsa.RSAPublicKey | ec.EllipticCurvePublicKey:
    """Convert an RSA or EC JWK dictionary to a cryptography public key."""
    kty = jwk.get("kty")
    if kty == "RSA":
        n_raw = jwk.get("n")
        e_raw = jwk.get("e")
        if not n_raw or not e_raw:
            raise OidcVerificationError("RSA JWK is missing required 'n' or 'e' parameters")
        n = int.from_bytes(_base64url_decode(str(n_raw)), byteorder="big")
        e = int.from_bytes(_base64url_decode(str(e_raw)), byteorder="big")
        return rsa.RSAPublicNumbers(e=e, n=n).public_key()
    elif kty == "EC":
        crv = jwk.get("crv")
        if crv != "P-256":
            raise OidcVerificationError(f"Unsupported EC curve '{crv}', only 'P-256' is supported")
        x_raw = jwk.get("x")
        y_raw = jwk.get("y")
        if not x_raw or not y_raw:
            raise OidcVerificationError("EC JWK is missing required 'x' or 'y' parameters")
        x = int.from_bytes(_base64url_decode(str(x_raw)), byteorder="big")
        y = int.from_bytes(_base64url_decode(str(y_raw)), byteorder="big")
        return ec.EllipticCurvePublicNumbers(x=x, y=y, curve=ec.SECP256R1()).public_key()
    else:
        raise OidcVerificationError(f"Unsupported key type '{kty}'")


def verify_signature(
    signing_input: bytes,
    signature_bytes: bytes,
    alg: str,
    key: rsa.RSAPublicKey | ec.EllipticCurvePublicKey | dict[str, Any],
) -> None:
    """Verify a cryptographic signature using an RSA or EC public key."""
    pub_key = jwk_to_public_key(key) if isinstance(key, dict) else key

    try:
        if alg == "RS256":
            if not isinstance(pub_key, rsa.RSAPublicKey):
                raise OidcVerificationError("RS256 algorithm requires an RSA public key")
            pub_key.verify(signature_bytes, signing_input, padding.PKCS1v15(), hashes.SHA256())
        elif alg == "ES256":
            if not isinstance(pub_key, ec.EllipticCurvePublicKey):
                raise OidcVerificationError("ES256 algorithm requires an EC public key")
            if len(signature_bytes) == 64:
                r = int.from_bytes(signature_bytes[:32], byteorder="big")
                s = int.from_bytes(signature_bytes[32:], byteorder="big")
                der_sig = utils.encode_dss_signature(r, s)
            else:
                der_sig = signature_bytes
            pub_key.verify(der_sig, signing_input, ec.ECDSA(hashes.SHA256()))
        else:
            raise OidcVerificationError(f"Unsupported signing algorithm '{alg}'")
    except (InvalidSignature, ValueError) as err:
        raise OidcVerificationError(f"Signature verification failed: {err}") from err


class OidcClient:
    """OpenID Connect client for authorization, token exchange, and token verification."""

    def __init__(
        self,
        provider: IdpProviderMeta,
        client_secret: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.provider = provider
        self.client_secret = client_secret or provider.client_secret or ""
        self._http_client = http_client
        self._discovery_doc: dict[str, Any] | None = None
        self._discovery_expires_at: float = 0.0
        self._jwks: dict[str, Any] | None = None
        self._jwks_expires_at: float = 0.0

    async def _get(self, url: str, headers: dict[str, str] | None = None) -> httpx.Response:
        """Execute an HTTP GET request using the configured client or a transient client."""
        if self._http_client is not None:
            return await self._http_client.get(url, headers=headers)
        async with httpx.AsyncClient(timeout=10.0) as client:
            return await client.get(url, headers=headers)

    async def _post(
        self,
        url: str,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Execute an HTTP POST request using the configured client or a transient client."""
        if self._http_client is not None:
            return await self._http_client.post(url, data=data, headers=headers)
        async with httpx.AsyncClient(timeout=10.0) as client:
            return await client.post(url, data=data, headers=headers)

    async def get_discovery_doc(self) -> dict[str, Any]:
        """Fetch and cache the OpenID provider configuration document."""
        now = time.time()
        if self._discovery_doc is not None and now < self._discovery_expires_at:
            return self._discovery_doc

        issuer = self.provider.issuer.rstrip("/")
        if not issuer:
            raise OidcDiscoveryError("Provider issuer URL is empty")
        discovery_url = f"{issuer}/.well-known/openid-configuration"

        try:
            resp = await self._get(discovery_url, headers={"Accept": "application/json"})
            if resp.status_code != 200:
                raise OidcDiscoveryError(f"Discovery endpoint returned status {resp.status_code}: {resp.text}")
            data = resp.json()
            if not isinstance(data, dict):
                raise OidcDiscoveryError("Discovery document is not a JSON object")
        except httpx.HTTPError as err:
            raise OidcDiscoveryError(f"HTTP error while fetching discovery document: {err}") from err
        except Exception as err:
            if isinstance(err, OidcDiscoveryError):
                raise
            raise OidcDiscoveryError(f"Failed to read discovery document: {err}") from err

        self._discovery_doc = data
        self._discovery_expires_at = now + 86400.0
        return self._discovery_doc

    async def get_jwks(self, force_refresh: bool = False) -> dict[str, Any]:
        """Fetch and cache the JSON Web Key Set (JWKS)."""
        now = time.time()
        if not force_refresh and self._jwks is not None and now < self._jwks_expires_at:
            return self._jwks

        discovery = await self.get_discovery_doc()
        jwks_uri = discovery.get("jwks_uri")
        if not jwks_uri:
            raise OidcDiscoveryError("Discovery document does not contain 'jwks_uri'")

        try:
            resp = await self._get(str(jwks_uri), headers={"Accept": "application/json"})
            if resp.status_code != 200:
                raise OidcDiscoveryError(f"JWKS endpoint returned status {resp.status_code}: {resp.text}")
            jwks_data = resp.json()
            if not isinstance(jwks_data, dict) or "keys" not in jwks_data:
                raise OidcDiscoveryError("JWKS response is not a valid JSON object with 'keys'")
        except httpx.HTTPError as err:
            raise OidcDiscoveryError(f"HTTP error while fetching JWKS: {err}") from err
        except Exception as err:
            if isinstance(err, OidcDiscoveryError):
                raise
            raise OidcDiscoveryError(f"Failed to read JWKS: {err}") from err

        self._jwks = jwks_data
        self._jwks_expires_at = now + 3600.0
        return self._jwks

    async def create_authorization_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        nonce: str,
        code_challenge: str,
    ) -> str:
        """Build the authorization URL with query parameters."""
        discovery = await self.get_discovery_doc()
        auth_endpoint = discovery.get("authorization_endpoint")
        if not auth_endpoint:
            raise OidcDiscoveryError("Discovery document does not contain 'authorization_endpoint'")

        scopes = " ".join(self.provider.scopes) if self.provider.scopes else "openid profile email"
        params: dict[str, str] = {
            "response_type": "code",
            "client_id": self.provider.client_id,
            "redirect_uri": redirect_uri,
            "scope": scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        if self.provider.hosted_domain:
            params["hd"] = self.provider.hosted_domain

        url_parts = urlparse(str(auth_endpoint))
        merged_query = parse_qsl(url_parts.query) + list(params.items())
        return urlunparse(url_parts._replace(query=urlencode(merged_query)))

    async def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> dict[str, Any]:
        """Exchange an authorization code for tokens at the token endpoint."""
        discovery = await self.get_discovery_doc()
        token_endpoint = discovery.get("token_endpoint")
        if not token_endpoint:
            raise OidcDiscoveryError("Discovery document does not contain 'token_endpoint'")

        payload = {
            "grant_type": "authorization_code",
            "client_id": self.provider.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        }
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        try:
            resp = await self._post(str(token_endpoint), data=payload, headers=headers)
            if resp.status_code != 200:
                raise OidcTokenError(f"Token endpoint returned status {resp.status_code}: {resp.text}")
            data = resp.json()
            if not isinstance(data, dict):
                raise OidcTokenError("Token endpoint returned non-dict JSON response")
            return data
        except httpx.HTTPError as err:
            raise OidcTokenError(f"HTTP error during token exchange: {err}") from err
        except Exception as err:
            if isinstance(err, OidcTokenError):
                raise
            raise OidcTokenError(f"Failed to parse token response: {err}") from err

    async def verify_id_token(self, id_token: str, *, expected_nonce: str) -> ExternalIdentity:
        """Verify an ID token signature, validate standard claims, and return the identity."""
        parts = id_token.split(".")
        if len(parts) != 3:
            raise OidcVerificationError("ID token must have three segments separated by dots")

        header_b64, payload_b64, signature_b64 = parts

        try:
            header_bytes = _base64url_decode(header_b64)
            header = json.loads(header_bytes.decode("utf-8"))
            payload_bytes = _base64url_decode(payload_b64)
            payload = json.loads(payload_bytes.decode("utf-8"))
            signature_bytes = _base64url_decode(signature_b64)
        except Exception as err:
            raise OidcVerificationError(f"Failed to decode ID token segments: {err}") from err

        alg = header.get("alg")
        if alg not in ("RS256", "ES256"):
            raise OidcVerificationError(f"Unsupported algorithm '{alg}' in ID token header")

        kid = header.get("kid")
        jwks = await self.get_jwks(force_refresh=False)
        keys = jwks.get("keys", [])

        target_key: dict[str, Any] | None = None
        if kid is not None:
            for k in keys:
                if k.get("kid") == kid:
                    target_key = k
                    break
            if target_key is None:
                # Force refresh JWKS once on missing kid
                jwks = await self.get_jwks(force_refresh=True)
                for k in jwks.get("keys", []):
                    if k.get("kid") == kid:
                        target_key = k
                        break
                if target_key is None:
                    raise OidcVerificationError(f"Key with kid '{kid}' not found in provider JWKS")
        else:
            expected_kty = "RSA" if alg == "RS256" else "EC"
            matching = [k for k in keys if k.get("kty") == expected_kty]
            if len(matching) == 1:
                target_key = matching[0]
            elif not matching:
                jwks = await self.get_jwks(force_refresh=True)
                matching = [k for k in jwks.get("keys", []) if k.get("kty") == expected_kty]
                if len(matching) == 1:
                    target_key = matching[0]
                elif not matching:
                    raise OidcVerificationError(f"No JWK key with kty='{expected_kty}' found in JWKS")
                else:
                    raise OidcVerificationError("Multiple keys match algorithm and token has no 'kid'")
            else:
                raise OidcVerificationError("Multiple keys match algorithm and token has no 'kid'")

        signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
        verify_signature(signing_input, signature_bytes, alg, target_key)

        iss = payload.get("iss")
        if not iss or str(iss).rstrip("/") != self.provider.issuer.rstrip("/"):
            raise OidcVerificationError(f"Issuer mismatch: expected '{self.provider.issuer}', got '{iss}'")

        aud = payload.get("aud")
        if isinstance(aud, str):
            if aud != self.provider.client_id:
                raise OidcVerificationError(f"Audience mismatch: expected '{self.provider.client_id}', got '{aud}'")
        elif isinstance(aud, list):
            if self.provider.client_id not in aud:
                raise OidcVerificationError(f"Audience mismatch: expected '{self.provider.client_id}' in {aud}")
        else:
            raise OidcVerificationError("Missing or invalid 'aud' claim in ID token")

        azp = payload.get("azp")
        if azp is not None and azp != self.provider.client_id:
            raise OidcVerificationError(f"Authorized party mismatch: expected '{self.provider.client_id}', got '{azp}'")

        now = time.time()
        exp = payload.get("exp")
        if exp is None or not isinstance(exp, (int, float)):
            raise OidcVerificationError("Missing or invalid 'exp' claim in ID token")
        if exp + 60.0 < now:
            raise OidcVerificationError("ID token has expired")

        iat = payload.get("iat")
        if iat is None or not isinstance(iat, (int, float)):
            raise OidcVerificationError("Missing or invalid 'iat' claim in ID token")
        if iat - 60.0 > now:
            raise OidcVerificationError("ID token issued in the future")

        nonce = payload.get("nonce")
        if nonce != expected_nonce:
            raise OidcVerificationError(f"Nonce mismatch: expected '{expected_nonce}', got '{nonce}'")

        if self.provider.hosted_domain:
            hd = payload.get("hd")
            if hd != self.provider.hosted_domain:
                raise OidcVerificationError(f"Hosted domain mismatch: expected '{self.provider.hosted_domain}', got '{hd}'")

        if self.provider.tenant_id:
            tid = payload.get("tid")
            if tid != self.provider.tenant_id:
                raise OidcVerificationError(f"Tenant ID mismatch: expected '{self.provider.tenant_id}', got '{tid}'")

        sub = payload.get("sub")
        if not sub:
            raise OidcVerificationError("Missing 'sub' claim in ID token")

        email = payload.get("email")
        email_verified = bool(payload.get("email_verified", False))
        display_name = payload.get("name") or payload.get("preferred_username")

        return ExternalIdentity(
            iss=str(iss),
            sub=str(sub),
            email=str(email) if email is not None else None,
            email_verified=email_verified,
            display_name=str(display_name) if display_name is not None else None,
        )


class GitHubAdapter:
    """OAuth 2.0 adapter for GitHub identity provider."""

    def __init__(
        self,
        provider: IdpProviderMeta,
        client_secret: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.provider = provider
        self.client_secret = client_secret or provider.client_secret or ""
        self._http_client = http_client

    async def _get(self, url: str, headers: dict[str, str] | None = None) -> httpx.Response:
        """Execute an HTTP GET request."""
        if self._http_client is not None:
            return await self._http_client.get(url, headers=headers)
        async with httpx.AsyncClient(timeout=10.0) as client:
            return await client.get(url, headers=headers)

    async def _post(
        self,
        url: str,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Execute an HTTP POST request."""
        if self._http_client is not None:
            return await self._http_client.post(url, data=data, headers=headers)
        async with httpx.AsyncClient(timeout=10.0) as client:
            return await client.post(url, data=data, headers=headers)

    def create_authorization_url(self, *, redirect_uri: str, state: str) -> str:
        """Return GitHub authorization URL with required scopes."""
        params = {
            "client_id": self.provider.client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": "read:user user:email",
        }
        return f"https://github.com/login/oauth/authorize?{urlencode(params)}"

    async def exchange_code_and_get_identity(self, *, code: str, redirect_uri: str) -> ExternalIdentity:
        """Exchange code for an access token and retrieve GitHub user identity."""
        token_url = "https://github.com/login/oauth/access_token"
        payload = {
            "client_id": self.provider.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        }
        headers = {"Accept": "application/json"}

        try:
            token_resp = await self._post(token_url, data=payload, headers=headers)
            if token_resp.status_code != 200:
                raise OidcTokenError(f"GitHub token endpoint returned status {token_resp.status_code}: {token_resp.text}")
            token_data = token_resp.json()
            if not isinstance(token_data, dict):
                raise OidcTokenError("GitHub token response is not a valid JSON object")
            if "error" in token_data:
                err_code = token_data.get("error", "unknown_error")
                err_desc = token_data.get("error_description")
                detail = f"{err_code}: {err_desc}" if err_desc else str(err_code)
                raise OidcTokenError(f"GitHub token exchange returned error: {detail}")
            access_token = token_data.get("access_token")
            if not access_token:
                raise OidcTokenError("GitHub access token missing in token response")
        except httpx.HTTPError as err:
            raise OidcTokenError(f"HTTP error during GitHub token exchange: {err}") from err
        except Exception as err:
            if isinstance(err, OidcTokenError):
                raise
            raise OidcTokenError(f"Failed to parse GitHub token response: {err}") from err

        api_headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
        }

        try:
            user_resp = await self._get("https://api.github.com/user", headers=api_headers)
            if user_resp.status_code != 200:
                raise OidcTokenError(f"GitHub user endpoint returned status {user_resp.status_code}: {user_resp.text}")
            user_data = user_resp.json()
            if not isinstance(user_data, dict) or "id" not in user_data:
                raise OidcVerificationError("GitHub user data missing required 'id' attribute")
            sub = str(user_data["id"])
            display_name = user_data.get("name") or user_data.get("login")
        except httpx.HTTPError as err:
            raise OidcTokenError(f"HTTP error while retrieving GitHub user data: {err}") from err
        except Exception as err:
            if isinstance(err, (OidcTokenError, OidcVerificationError)):
                raise
            raise OidcVerificationError(f"Failed to read GitHub user data: {err}") from err

        email: str | None = None
        email_verified = False

        try:
            emails_resp = await self._get("https://api.github.com/user/emails", headers=api_headers)
            if emails_resp.status_code == 200:
                emails_list = emails_resp.json()
                if isinstance(emails_list, list):
                    primary_verified = next((e for e in emails_list if isinstance(e, dict) and e.get("primary") is True and e.get("verified") is True), None)
                    if primary_verified is not None:
                        email = primary_verified.get("email")
                        email_verified = True
                    else:
                        any_verified = next((e for e in emails_list if isinstance(e, dict) and e.get("verified") is True), None)
                        if any_verified is not None:
                            email = any_verified.get("email")
                            email_verified = True
                        else:
                            primary_any = next((e for e in emails_list if isinstance(e, dict) and e.get("primary") is True), None)
                            if primary_any is not None:
                                email = primary_any.get("email")
                                email_verified = bool(primary_any.get("verified", False))
        except Exception as err:
            log.warning("Could not fetch emails for GitHub user %s: %s", sub, err)

        if not email:
            email = user_data.get("email")
            email_verified = False

        return ExternalIdentity(
            iss="https://github.com",
            sub=sub,
            email=str(email) if email is not None else None,
            email_verified=email_verified,
            display_name=str(display_name) if display_name is not None else None,
        )


__all__ = [
    "ExternalIdentity",
    "GitHubAdapter",
    "IdpProviderMeta",
    "OidcClient",
    "OidcDiscoveryError",
    "OidcError",
    "OidcTokenError",
    "OidcVerificationError",
    "generate_pkce_pair",
    "jwk_to_public_key",
    "verify_pkce",
    "verify_signature",
]
