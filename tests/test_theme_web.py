from __future__ import annotations

from pathlib import Path
from starlette.testclient import TestClient

from claire.api import server
from claire.config import Settings
from claire.graphview import render_graph_html


def test_theme_web_ui_elements_in_template_and_graph_html(tmp_path: Path):
    s = Settings(
        db_path=str(tmp_path / "claire.db"),
        vault_path=str(tmp_path / "vault"),
    )
    html = render_graph_html(s)

    # 1. Header theme selector
    assert 'id="theme-picker-wrap"' in html
    assert 'id="theme-select"' in html
    assert 'id="theme-curr-icon"' in html
    assert 'onchange="switchKnowledgeTheme(this.value)"' in html

    # 2. Ingest form theme selector
    assert 'id="ingtheme"' in html
    assert "renderThemeOptions" in html

    # 3. JavaScript theme management functions
    assert "async function fetchThemes()" in html
    assert "function renderThemeSelector()" in html
    assert "async function switchKnowledgeTheme(" in html
    assert "async function reloadThemeData(" in html
    assert "async function loadThemeDocuments()" in html
    assert "X-Claire-Theme" in html
    assert "claireKnowledgeTheme" in html

    # 4. CSS styling
    assert ".theme-picker-wrap" in html
    assert ".theme-select-control" in html
    assert ".theme-select" in html
    assert ".ingest-theme-select" in html


def test_theme_web_root_endpoint_serves_selector(tmp_path: Path):
    s = Settings(
        db_path=str(tmp_path / "claire.db"),
        vault_path=str(tmp_path / "vault"),
    )
    app = server.create_app(s)
    with TestClient(app, base_url=s.public_url) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert 'id="theme-select"' in response.text
        assert "switchKnowledgeTheme" in response.text
