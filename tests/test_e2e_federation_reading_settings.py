"""End-to-end test of the reading settings (FEDERATION_REMOTE_READING_DESIGN.md Section 10, unit FP4-2).

A real app in network mode: the admin sets the server values, the owner of an account sets the own retention, and Section 10 rule 1 holds (the effective value is the lower one; an account value above the server value is refused; a later decrease of the server value does not change the account value).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from claire.api import server
from tests.test_federation_console import PUBLIC_URL, _audit_rows, _Client, _isolate, _prepare, _session  # noqa: F401  (_isolate is an autouse fixture)

READING = "/api/admin/federation/reading"
SETTINGS = "/api/federation/settings"


@pytest.fixture
def env(tmp_path):
    settings, alpha, beta = _prepare(tmp_path)
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    with TestClient(app, base_url=PUBLIC_URL) as tc:
        yield SimpleNamespace(client=_Client(tc), settings=settings, alpha=alpha, admin=_session(settings, 1), alpha_session=_session(settings, alpha))


def test_e2e_admin_and_owner_reading_settings(env):
    c = env.client
    # Defaults of Section 10.
    data = c.request("GET", READING, session=env.admin).json()
    assert data["settings"]["retention_days"] == 90 and data["settings"]["following_limit"] == 1000 and data["settings"]["context_fetch"] == "on"
    assert c.request("GET", SETTINGS, session=env.alpha_session).json() == {"retention_days": None, "server_retention_days": 90, "effective_retention_days": 90}

    # The admin changes two values; the change has an audit record.
    res = c.request("PUT", READING, session=env.admin, json={"retention_days": 30, "context_fetch": "off"})
    assert res.status_code == 200, res.text
    assert res.json()["settings"]["retention_days"] == 30 and res.json()["settings"]["context_fetch"] == "off"
    assert (1, "federation.reading.update", {"retention_days": 30, "context_fetch": "off"}) in _audit_rows(env.settings)
    # A value outside its range and an unknown key change nothing.
    assert c.request("PUT", READING, session=env.admin, json={"retention_days": 0}).status_code == 400
    assert c.request("PUT", READING, session=env.admin, json={"retention": 10}).status_code == 400
    assert c.request("GET", READING, session=env.admin).json()["settings"]["retention_days"] == 30
    # A member is not the admin: the admin route is hidden.
    assert c.request("GET", READING, session=env.alpha_session).status_code == 404
    assert c.request("PUT", READING, session=env.alpha_session, json={"retention_days": 10}).status_code == 404

    # The owner: a value above the server value is refused, a lower value is the effective value.
    assert c.request("PUT", SETTINGS, session=env.alpha_session, json={"retention_days": 60}).status_code == 400
    res = c.request("PUT", SETTINGS, session=env.alpha_session, json={"retention_days": 20})
    assert res.json() == {"retention_days": 20, "server_retention_days": 30, "effective_retention_days": 20}
    # The admin decreases the server value: the account value stays, and the effective value is the lower one.
    c.request("PUT", READING, session=env.admin, json={"retention_days": 10})
    assert c.request("GET", SETTINGS, session=env.alpha_session).json() == {"retention_days": 20, "server_retention_days": 10, "effective_retention_days": 10}
    # null returns to the server value; a body outside the model is refused.
    assert c.request("PUT", SETTINGS, session=env.alpha_session, json={"retention_days": None}).json()["effective_retention_days"] == 10
    assert c.request("PUT", SETTINGS, session=env.alpha_session, json={"retention_days": 5, "x": 1}).status_code == 400


def test_console_sections_have_the_fields_of_the_reading_settings(env):
    """The console fields of unit FP4-5: the admin form shows the stored values (sizes in MiB), and the owner section has the retention form."""
    c = env.client
    assert c.request("PUT", READING, session=env.admin, json={"retention_days": 30, "media_image_max_bytes": 40 * 1024 * 1024, "context_fetch": "off"}).status_code == 200
    page = c.request("GET", "/admin/federation", session=env.admin)
    assert page.status_code == 200
    assert 'id="admin-federation-reading-form"' in page.text
    assert 'name="retention_days" class="pref-input" value="30" min="1" max="3650"' in page.text
    assert 'name="media_image_max_bytes" class="pref-input" value="40" min="1" max="100"' in page.text and 'data-reading-scale="1048576"' in page.text
    assert '<option value="off" selected>' in page.text
    # Each setting of Section 10 has one field.
    from claire.store import federation_client

    for key in federation_client.READING_DEFAULTS:
        assert f'name="{key}"' in page.text, key
    owner_page = c.request("GET", "/settings/federation", session=env.alpha_session)
    assert owner_page.status_code == 200 and "data-retention-form" in owner_page.text and 'name="retention_days"' in owner_page.text
    # A member has no admin section.
    assert "admin-federation-reading-form" not in owner_page.text
