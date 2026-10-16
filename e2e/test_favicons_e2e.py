"""End-to-End (E2E) test for common favicon and application branding across all web endpoints.

Simulates the complete user and client journey:
1. Boot the server in production mode with an isolated database.
2. Visit the main application homepage (/) -> verify presence of authoritative vector and raster favicon tags.
3. Visit the API documentation interface (/reference) -> verify identical favicon tags and Scalar data-configuration binding.
4. Visit the OAuth authorization screen (/oauth/authorize) -> verify common favicon tags.
5. Fetch all referenced favicon and web manifest assets -> verify valid status 200 and correct Content-Type.
6. Verify extensibility: dynamic and future HTML endpoints automatically receive the identical branding suite.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from claire.api import server
from claire.config import Settings
from claire.presentation.favicon import (
    APPLICATION_NAME,
    FAVICON_ICO_PATH,
    FAVICON_SVG_PATH,
    ICON_PNG_192_PATH,
    MANIFEST_PATH,
    inject_favicon_head_tags,
)
from claire.store import db as dbm


@pytest.fixture
def favicon_e2e_app(tmp_path: Path):
    db_file = tmp_path / "claire_favicon_e2e.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    conn.close()

    settings = Settings(
        _env_file=None,
        db_path=str(db_file),
        data_path=str(tmp_path),
        vault_path=str(tmp_path / "vault"),
        environment="production",
        public_url="https://claire.example.com",
        fqdn="claire.example.com",
        inject_token="owner-token-secret-12345678901234",
        readonly_token="readonly-token-secret-123456789012",
        anonymous_readonly=True,
        multi_theme=False,
        site_name="Claire Bible",
    )
    service = SimpleNamespace(provider=SimpleNamespace(name="stub"))
    app = server.create_app(settings, service)
    client = TestClient(app, base_url="https://claire.example.com")
    return client


def test_common_favicon_e2e_journey(favicon_e2e_app: TestClient) -> None:
    client = favicon_e2e_app

    # 1. Homepage (/)
    home_resp = client.get("/")
    assert home_resp.status_code == 200
    home_html = home_resp.text
    assert f'href="{FAVICON_SVG_PATH}"' in home_html
    assert f'href="{FAVICON_ICO_PATH}"' in home_html
    assert f'href="{ICON_PNG_192_PATH}"' in home_html
    assert f'href="{MANIFEST_PATH}"' in home_html
    assert f'content="{APPLICATION_NAME}"' in home_html

    # 2. API Reference documentation (/reference)
    docs_resp = client.get("/reference")
    assert docs_resp.status_code == 200
    docs_html = docs_resp.text
    assert f'href="{FAVICON_SVG_PATH}"' in docs_html
    assert f'href="{FAVICON_ICO_PATH}"' in docs_html
    assert f'href="{ICON_PNG_192_PATH}"' in docs_html
    assert f'href="{MANIFEST_PATH}"' in docs_html

    # Verify Scalar data-configuration matches homepage favicon
    marker = "data-configuration='"
    assert marker in docs_html
    start = docs_html.index(marker) + len(marker)
    end = docs_html.index("'>", start)
    scalar_cfg = json.loads(docs_html[start:end])
    assert scalar_cfg.get("favicon") == FAVICON_SVG_PATH

    # 3. OAuth authorization screen (/oauth/authorize)
    oauth_resp = client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": "test-client-id",
            "redirect_uri": "https://claire.example.com/callback",
            "state": "state-xyz",
            "code_challenge": "challenge-xyz",
            "code_challenge_method": "S256",
        },
    )
    assert oauth_resp.status_code == 200
    oauth_html = oauth_resp.text
    assert f'href="{FAVICON_SVG_PATH}"' in oauth_html
    assert f'href="{FAVICON_ICO_PATH}"' in oauth_html
    assert f'href="{MANIFEST_PATH}"' in oauth_html

    # 4. Verify all asset endpoints return valid payload and correct media type
    assets_to_verify = [
        (FAVICON_SVG_PATH, "image/svg+xml"),
        (FAVICON_ICO_PATH, "image/x-icon"),
        ("/apple-touch-icon.png", "image/png"),
        (ICON_PNG_192_PATH, "image/png"),
        (MANIFEST_PATH, "application/manifest+json"),
    ]
    for asset_path, expected_mime in assets_to_verify:
        asset_resp = client.get(asset_path)
        assert asset_resp.status_code == 200, f"Failed to fetch asset: {asset_path}"
        assert expected_mime in asset_resp.headers.get("content-type", "")
        assert len(asset_resp.content) > 0, f"Empty content for asset: {asset_path}"

    # 5. Extensibility verification for future project features
    custom_extension_page = "<!doctype html><html><head><title>Future Tool</title></head><body><h1>Extension</h1></body></html>"
    enriched_page = inject_favicon_head_tags(custom_extension_page)
    assert f'href="{FAVICON_SVG_PATH}"' in enriched_page
    assert f'href="{FAVICON_ICO_PATH}"' in enriched_page
    assert f'href="{MANIFEST_PATH}"' in enriched_page
