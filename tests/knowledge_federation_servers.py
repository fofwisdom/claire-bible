"""Test servers for the federation of shared knowledge (KNOWLEDGE_FEDERATION_DESIGN.md unit KF1).

One ``KfServer`` is one Claire server with its own database: the public app (``server.create_app``) and the internal app of the sidecar (``create_internal_federation_app``) with a signed client. The servers are ``https://a.example`` and ``https://b.example`` of the contract fixtures in ``tests/fixtures/knowledge_federation/``, and ``https://c.example``.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from starlette.testclient import TestClient

from claire.api import federation_routes, server
from claire.api.federation_internal import INTERNAL_PREFIX, SIGNATURE_HEADER, TIMESTAMP_HEADER, create_internal_federation_app, ensure_service_key, load_service_key, service_key_path, sign_request
from claire.store import accounts as accounts_store
from claire.store import db as dbm

FIXTURES = Path(__file__).parent / "fixtures" / "knowledge_federation"
# The number of the shared knowledge base of B in the fixtures (``https://b.example/ap/kb/1846000000000000100``).
B_KB_NUMBER = 1846000000000000100
ALICE = "https://a.example/ap/actors/1846000000000000001"


def fixture(kind: str, name: str) -> dict[str, Any]:
    """One contract fixture: ``kind`` is ``activities``, ``objects``, or ``invalid``."""
    return json.loads((FIXTURES / kind / f"{name}.json").read_text(encoding="utf-8"))


def server_actor(origin: str) -> str:
    return f"{origin}/ap/actors/server"


@dataclass
class _Settings:
    db_file: Path
    data_dir: Path
    public_url: str
    fqdn: str
    environment: str = "production"
    inject_token: str = ""
    readonly_token: str = ""
    cors_allowed_origins: str = ""
    anonymous_readonly: bool = True
    multi_theme: bool = True
    trusted_proxies: str = "10.0.0.0/8,fd00::/8"
    telemetry_retention_days: int = 30


class KfServer:
    """One test server. ``now`` is the clock of the internal app; the store functions get the time of the request."""

    def __init__(self, tmp_path: Path, origin: str, *, username: str, federation: str = "on") -> None:
        directory = tmp_path / origin.split("//", 1)[1]
        directory.mkdir(parents=True)
        self.origin = origin
        self.username = username
        self.settings = _Settings(db_file=directory / "claire.db", data_dir=directory, public_url=origin + "/", fqdn=origin.split("//", 1)[1])
        conn = dbm.connect(self.settings.db_file)
        try:
            dbm.init_db(conn)
            store = accounts_store.AccountStore(conn)
            # Single mode (the default): the federation of shared knowledge works in single mode (design Section 15 rule 5).
            store.complete_setup(1, username=username, display_name=username)
            store.set_federation_state(federation, updated_by=1)
            conn.commit()
        finally:
            conn.close()
        self.app = server.create_app(self.settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
        self.client = TestClient(self.app, base_url=origin)
        ensure_service_key(service_key_path(self.settings.data_dir))
        self._key = load_service_key(service_key_path(self.settings.data_dir))
        self.now = time.time()
        self.internal_client = TestClient(create_internal_federation_app(self.settings, routes=federation_routes.federation_internal_routes(self.settings, now=lambda: self.now), now=lambda: self.now))

    # --- Database ---

    def db(self, fn: Any) -> Any:
        conn = dbm.connect_existing(self.settings.db_file)
        try:
            result = fn(conn)
            conn.commit()
            return result
        finally:
            conn.close()

    def admin_session(self, *, authenticated_ago: float = 0.0) -> str:
        return self.db(lambda conn: accounts_store.create_account_session(conn, account_id=1, scope="owner", now=time.time() - authenticated_ago))

    def add_shared_kb(self, number: int, label: str, *, knowledge_type: str = "real") -> None:
        """A shared knowledge base: no owner, public, with a knowledge type (shared knowledge base design Section 11.2)."""
        now = time.time()
        self.db(lambda conn: conn.execute(
            "INSERT INTO knowledge_bases (seq, label, description, is_personal, is_public, created_at, updated_at, account_id, number, created_by_account_id, knowledge_type) VALUES (?, ?, ?, 0, 1, ?, ?, NULL, ?, 1, ?)",
            (number % 1_000_000, label, f"About {label}.", now, now, number, knowledge_type),
        ))

    # --- Public app ---

    def request(self, method: str, path: str, *, session: str | None = None, origin: str | None = "same", **kwargs: Any):
        """One request to the public app. A request that changes data has the ``Origin`` of this server, unless ``origin`` is ``None``."""
        headers = dict(kwargs.pop("headers", {}) or {})
        if session is not None:
            headers["Cookie"] = f"claire_session={session}"
        if method not in {"GET", "HEAD"} and origin is not None:
            headers["Origin"] = self.origin if origin == "same" else origin
        self.client.cookies.clear()
        response = self.client.request(method, path, headers=headers, follow_redirects=False, **kwargs)
        self.client.cookies.clear()
        return response

    # --- Internal app (the sidecar is the caller) ---

    def internal(self, method: str, path: str, body: Any = None):
        """One signed request to the internal federation API, as the sidecar sends it. ``path`` is under ``INTERNAL_PREFIX`` and can have a query."""
        raw = b"" if body is None else json.dumps(body).encode()
        target = f"{INTERNAL_PREFIX}{path}"
        stamp = str(int(self.now))
        headers = {TIMESTAMP_HEADER: stamp, SIGNATURE_HEADER: sign_request(self._key, method, target, stamp, raw)}
        if body is not None:
            headers["Content-Type"] = "application/json"
        return self.internal_client.request(method, target, content=raw, headers=headers)

    def deliver(self, activity: dict[str, Any], *, signer: str, recipient: str | None, verified_by: str = "http-signature"):
        """Simulate the sidecar: it verified the signature of ``signer`` and hands the activity JSON unchanged to Claire."""
        return self.internal("POST", "/knowledge-activities", {"activity": activity, "signer": signer, "verified_by": verified_by, "recipient": recipient})
