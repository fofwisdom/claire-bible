"""Static prototype host role (federation remote reading design, RD20).

Unit tests cover the call to the Public Suffix List library (``registrable_domain``), the module interfaces (``HostRoles``, ``dev_listeners``), and the file I/O (package copy against design copy). The end-to-end test goes through ``create_app`` and the full ASGI stack.
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
from claire.api.prototype_host import DEV_PROTOTYPE_PORT, PROTOTYPE_PAGE, prototype_authority, registrable_domain

REPO_ROOT = Path(__file__).resolve().parent.parent
DESIGN_COPY = REPO_ROOT / "docs" / "design" / "prototypes" / "federation_client_prototype.html"
OWNER_TOKEN = "owner-" + "o" * 32
APP_HOST = "127.0.0.1:8765"
PROTO_HOST = f"127.0.0.1:{DEV_PROTOTYPE_PORT}"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("CLAIRE_", "CB_")):
            monkeypatch.delenv(name, raising=False)


def test_package_copy_is_identical_to_design_copy():
    """The Docker image has only the package copy. A change to one copy without the other fails here."""
    assert PROTOTYPE_PAGE.is_file() and DESIGN_COPY.is_file()
    assert PROTOTYPE_PAGE.read_bytes() == DESIGN_COPY.read_bytes()


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("cb.netspheres.org", "netspheres.org"),
        ("claire.example.co.kr", "example.co.kr"),
        ("a.b.claire.example.co.kr", "example.co.kr"),
        ("netspheres.org", "netspheres.org"),
        ("CB.Netspheres.ORG.", "netspheres.org"),
        ("cb.netspheres.org:443", "netspheres.org"),
        ("co.kr", None),
        ("127.0.0.1", None),
        ("127.0.0.1:8766", None),
        ("[::1]:8766", None),
        ("::1", None),
        ("localhost", None),
        ("example", None),
        ("claire.internal", None),
        ("", None),
    ],
)
def test_registrable_domain_uses_public_suffix_list(host, expected):
    assert registrable_domain(host) == expected


def test_public_suffix_list_is_the_bundled_snapshot(monkeypatch):
    """The library reads its bundled list file. A network call at run time fails this test."""
    import socket

    import claire.api.prototype_host as prototype_host

    def _no_network(*_args, **_kwargs):
        raise AssertionError("network access")

    monkeypatch.setattr(socket, "create_connection", _no_network)
    monkeypatch.setattr(socket.socket, "connect", _no_network)
    prototype_host._public_suffix_list.cache_clear()
    assert registrable_domain("x.example.co.kr") == "example.co.kr"


@pytest.mark.parametrize(
    ("app", "environment", "expected"),
    [
        ("cb.netspheres.org", "production", "prototype.netspheres.org"),
        ("claire.example.co.kr", "production", "prototype.example.co.kr"),
        ("localhost", "production", None),
        ("203.0.113.5", "production", None),
        ("127.0.0.1:8766", "development", f"127.0.0.1:{DEV_PROTOTYPE_PORT}"),
        ("localhost:8766", "development", None),
        ("0.0.0.0:8766", "development", None),
    ],
)
def test_prototype_authority_is_computed_from_app_host(app, environment, expected):
    assert prototype_authority(app, environment) == expected


def _role_settings(**overrides) -> SimpleNamespace:
    values = dict(cors_allowed_origins="", site_domain="", usercontent_fqdn="", media_fqdn="")
    values.update(overrides)
    return SimpleNamespace(**values)


def test_host_roles_compute_prototype_host():
    dev = HostRoles.from_settings(_role_settings(), environment="development", app_authority=APP_HOST)
    assert dev.prototype == PROTO_HOST and dev.role_of(PROTO_HOST) == "prototype"
    assert dev.origin("prototype") == f"http://{PROTO_HOST}"
    prod = HostRoles.from_settings(_role_settings(), environment="production", app_authority="cb.netspheres.org")
    assert prod.role_of("prototype.netspheres.org") == "prototype"
    assert prod.role_of("prototype.cb.netspheres.org") is None
    # A role host that is the computed name keeps its role. The environment then has no prototype host.
    taken = HostRoles.from_settings(_role_settings(site_domain="netspheres.org", usercontent_fqdn="prototype.netspheres.org"), environment="production", app_authority="cb.netspheres.org")
    assert taken.prototype is None and taken.role_of("prototype.netspheres.org") == "usercontent"
    assert HostRoles.from_settings(_role_settings(), environment="production", app_authority="localhost").prototype is None
    # The prototype origin is never in the CORS allow list.
    with pytest.raises(ValueError):
        HostRoles.from_settings(_role_settings(cors_allowed_origins=f"http://{PROTO_HOST}"), environment="development", app_authority=APP_HOST)


def test_dev_listeners_open_the_prototype_port():
    settings = SimpleNamespace(environment="development", inject_host="127.0.0.1", inject_port=8765)
    assert dev_listeners(settings, prototype=PROTO_HOST) == (("127.0.0.1", 8765), ("127.0.0.1", DEV_PROTOTYPE_PORT))
    assert dev_listeners(settings) == (("127.0.0.1", 8765),)


def _create_app(tmp_path: Path):
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
    )
    return create_app(settings=settings)


def test_e2e_prototype_host_serves_static_page_only(tmp_path):
    app = _create_app(tmp_path)
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


def test_e2e_other_hosts_are_refused(tmp_path):
    """Only the computed prototype host has the role. Another port or name is refused as before (421)."""
    app = _create_app(tmp_path)
    with TestClient(app, base_url=f"http://{PROTO_HOST}") as client:
        for host in ("127.0.0.1:8770", "localhost:8769", "prototype.netspheres.org", "prototype.localhost"):
            res = client.get(f"http://{host}/")
            assert res.status_code == 421, host
            assert "content-security-policy" not in res.headers or "connect-src 'none'" not in res.headers["content-security-policy"], host
    assert security.lookup_route_rule("GET", "/prototype/syndication") is None
