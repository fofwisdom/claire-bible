"""Static prototype host role (federation remote reading design, RD20).

Unit tests cover the module interfaces (``HostRoles``, ``dev_listeners``) and the file I/O (package copy against design copy). The end-to-end test goes through ``create_app`` and the full ASGI stack.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from claire.api import security
from claire.api.dev_listeners import dev_listeners
from claire.api.host_router import HostRoles
from claire.api.prototype_host import PROTOTYPE_PAGE

REPO_ROOT = Path(__file__).resolve().parent.parent
DESIGN_COPY = REPO_ROOT / "docs" / "design" / "prototypes" / "federation_client_prototype.html"
OWNER_TOKEN = "owner-" + "o" * 32
APP_HOST = "127.0.0.1:8765"
PROTO_HOST = "127.0.0.1:8769"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("CLAIRE_", "CB_")):
            monkeypatch.delenv(name, raising=False)


def test_package_copy_is_identical_to_design_copy():
    """The Docker image has only the package copy. A change to one copy without the other fails here."""
    assert PROTOTYPE_PAGE.is_file() and DESIGN_COPY.is_file()
    assert PROTOTYPE_PAGE.read_bytes() == DESIGN_COPY.read_bytes()


def _role_settings(**overrides) -> SimpleNamespace:
    values = dict(
        environment="development",
        public_url=f"http://{APP_HOST}",
        cors_allowed_origins="",
        inject_token=OWNER_TOKEN,
        readonly_token="",
        anonymous_readonly=False,
        db_file=None,
        site_domain="",
        usercontent_fqdn="",
        media_fqdn="",
        prototype_fqdn="",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _roles(settings: SimpleNamespace, environment: str = "development", app: str = APP_HOST) -> HostRoles:
    return HostRoles.from_settings(settings, environment=environment, app_authority=app)


def test_host_roles_read_prototype_setting():
    roles = _roles(_role_settings(prototype_fqdn=PROTO_HOST))
    assert roles.role_of(PROTO_HOST) == "prototype"
    assert roles.origin("prototype") == f"http://{PROTO_HOST}"
    empty = _roles(_role_settings())
    assert empty.prototype is None and empty.role_of(PROTO_HOST) is None and empty.origin("prototype") is None
    prod = _roles(
        _role_settings(environment="production", site_domain="example.com", prototype_fqdn="prototype.claire.example.com"),
        environment="production",
        app="claire.example.com",
    )
    assert prod.role_of("prototype.claire.example.com") == "prototype"


@pytest.mark.parametrize(
    "overrides",
    [
        {"prototype_fqdn": "127.0.0.1:8765"},  # same port as app
        {"prototype_fqdn": "127.0.0.1:8767", "usercontent_fqdn": "127.0.0.1:8767"},  # same as another role
        {"prototype_fqdn": PROTO_HOST, "cors_allowed_origins": f"http://{PROTO_HOST}"},  # role origin in the CORS allow list
    ],
)
def test_host_roles_refuse_invalid_prototype_setting(overrides):
    with pytest.raises(ValueError):
        _roles(_role_settings(**overrides))


def test_dev_listeners_open_the_prototype_port():
    settings = SimpleNamespace(environment="development", inject_host="127.0.0.1", inject_port=8766, prototype_fqdn="127.0.0.1:8769")
    assert dev_listeners(settings) == (("127.0.0.1", 8766), ("127.0.0.1", 8769))


def _create_app(tmp_path: Path, prototype_fqdn: str):
    from claire.api.server import create_app
    from claire.config import Settings

    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    data_dir.mkdir()
    vault_dir.mkdir()
    settings = Settings(
        _env_file=None,
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CLAIRE_VAULT_PATH=str(vault_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL=f"http://{APP_HOST}",
        CLAIRE_INJECT_TOKEN=OWNER_TOKEN,
        CLAIRE_ANONYMOUS_READONLY=True,
        CLAIRE_PROTOTYPE_FQDN=prototype_fqdn,
    )
    return create_app(settings=settings)


def test_e2e_prototype_host_serves_static_page_only(tmp_path):
    app = _create_app(tmp_path, PROTO_HOST)
    page = PROTOTYPE_PAGE.read_bytes()
    with TestClient(app, base_url=f"http://{PROTO_HOST}") as proto:
        cookie = {"Cookie": f"claire_session={'s' * 43}", "Authorization": f"Bearer {OWNER_TOKEN}"}
        res = proto.get("/", headers=cookie)
        assert res.status_code == 200
        assert res.content == page
        assert res.headers["content-type"].startswith("text/html")
        assert "set-cookie" not in res.headers
        assert res.headers["x-robots-tag"] == "noindex, nofollow"
        csp = res.headers["content-security-policy"]
        for directive in ("default-src 'none'", "script-src 'unsafe-inline'", "style-src 'unsafe-inline'", "connect-src 'none'", "frame-ancestors 'none'"):
            assert directive in csp, directive
        assert "access-control-allow-origin" not in res.headers
        head = proto.head("/")
        assert head.status_code == 200 and head.content == b""
        for path in ("/api/syndication/dispatches", "/api/admin/instance", "/health", "/static/js/app.js", "/prototype/syndication", "/syndication", "/index.html"):
            other = proto.get(path, headers=cookie)
            assert other.status_code == 404, path
            assert "set-cookie" not in other.headers
            assert other.headers["x-robots-tag"] == "noindex, nofollow"
        assert proto.post("/", headers=cookie).status_code == 404
        # The same application on the app host: the old prototype path is 404 like any path not in ROUTE_POLICY.
        owner = {"Authorization": f"Bearer {OWNER_TOKEN}"}
        assert proto.get(f"http://{APP_HOST}/prototype/syndication", headers=owner).status_code == 404
        assert proto.get(f"http://{APP_HOST}/health").status_code == 200


def test_e2e_prototype_host_refused_without_setting(tmp_path):
    app = _create_app(tmp_path, "")
    with TestClient(app, base_url=f"http://{PROTO_HOST}") as proto:
        assert proto.get("/").status_code == 421
    assert security.lookup_route_rule("GET", "/prototype/syndication") is None
