"""/admin/plugins and /api/admin/plugins (INGEST_ADAPTER_PLUGIN_DESIGN.md §10.1, §10.2; S2, S3, S8)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from claire.api import server
from claire.plugin_host import runtime as rt
from tests.test_federation_console import PUBLIC_URL, _audit_rows, _Client, _prepare, _session, _Settings
from tests.test_plugin_host import lock_project, make_plugin

API = "/api/admin/plugins"


@dataclass
class _PluginSettings(_Settings):
    plugins_dir: Path = Path("/nonexistent")


@pytest.fixture
def env(tmp_path, monkeypatch):
    base, alpha, _beta = _prepare(tmp_path)
    settings = _PluginSettings(**{**base.__dict__, "plugins_dir": tmp_path / "var" / "plugins"})
    project = tmp_path / "app" / "plugins"
    project.mkdir(parents=True)
    settings.plugins_dir.mkdir(parents=True)
    make_plugin(project, "claire.alpha", hosts=["alpha.test"])
    lock_project(project)
    make_plugin(settings.plugins_dir, "example.alt", name="alt", hosts=["alpha.test"])
    monkeypatch.setattr(rt, "project_plugin_folder", lambda: project)
    monkeypatch.setattr(rt, "server_db_file", lambda s=None: settings.db_file)
    rt.runtime().invalidate()
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    with TestClient(app, base_url=PUBLIC_URL) as tc:
        yield SimpleNamespace(client=_Client(tc), settings=settings, admin=_session(settings, 1), stale_admin=_session(settings, 1, authenticated_ago=3600), member=_session(settings, alpha))
    rt.runtime().invalidate()


def _plugins(env):
    response = env.client.request("GET", API, session=env.admin)
    assert response.status_code == 200
    return {p["id"]: p for p in response.json()["plugins"]}


def test_only_the_administrator_sees_plugins(env):
    assert env.client.request("GET", API, session=env.member).status_code == 404
    assert env.client.request("GET", "/admin/plugins", session=env.member).status_code == 404
    page = env.client.request("GET", "/admin/plugins", session=env.admin)
    assert page.status_code == 200
    assert 'data-plugin-switch="claire.alpha"' in page.text and 'data-plugin-switch="example.alt"' in page.text
    assert "<caption>" in page.text and 'id="admin-plugin-dialog"' in page.text


def test_switch_on_needs_step_up_digest_and_exact_replace(env):
    plugins = _plugins(env)
    assert plugins["claire.alpha"]["state"] == "enabled"
    alt = plugins["example.alt"]
    assert alt["state"] == "new" and [o["id"] for o in alt["overlap"]] == ["claire.alpha"]

    put = lambda body, session=env.admin: env.client.request("PUT", f"{API}/example.alt", session=session, json=body)  # noqa: E731
    assert put({"enabled": True, "digest": alt["digest"], "replace": ["claire.alpha"]}, session=env.stale_admin).status_code == 401
    assert put({"enabled": True, "replace": ["claire.alpha"]}).json()["error"] == "digest_required"
    assert put({"enabled": True, "digest": "sha256:old", "replace": ["claire.alpha"]}).json()["error"] == "plugin_changed"
    refused = put({"enabled": True, "digest": alt["digest"]})
    assert refused.status_code == 409 and refused.json()["error"] == "plugin_overlap" and refused.json()["enabled"] == ["claire.alpha"]
    assert put({"enabled": True, "digest": alt["digest"], "other": 1}).status_code == 400

    ok = put({"enabled": True, "digest": alt["digest"], "replace": ["claire.alpha"]})
    assert ok.status_code == 200 and ok.json()["replaced"] == ["claire.alpha"]
    plugins = _plugins(env)
    assert plugins["claire.alpha"]["state"] == "disabled" and plugins["example.alt"]["state"] == "enabled"
    actions = [(action, (detail or {}).get("plugin_id"), (detail or {}).get("replaced_by")) for _actor, action, detail in _audit_rows(env.settings)]
    assert ("plugin.disable", "claire.alpha", "example.alt") in actions and ("plugin.enable", "example.alt", None) in actions


def test_switch_off_needs_no_step_up(env):
    response = env.client.request("PUT", f"{API}/claire.alpha", session=env.stale_admin, json={"enabled": False})
    assert response.status_code == 200
    assert _plugins(env)["claire.alpha"]["state"] == "disabled"
    assert env.client.request("PUT", f"{API}/claire.missing", session=env.admin, json={"enabled": False}).status_code == 404
    assert env.client.request("DELETE", f"{API}/claire.alpha", session=env.admin).json()["error"] == "plugin_folder_exists"
