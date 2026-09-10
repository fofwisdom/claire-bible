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


def test_theme_web_single_mode_inlining_and_cls_prevention(tmp_path: Path):
    """싱글 테마 모드: CLS 방지(display:none 인라인) 및 0-RTT 설정 주입 검증."""
    s = Settings(
        db_path=str(tmp_path / "claire.db"),
        vault_path=str(tmp_path / "vault"),
        CLAIRE_MULTI_THEME=False,
    )
    html = render_graph_html(s)
    assert 'themeMode: \'single\'' in html
    assert 'id="theme-picker-wrap" title="지식 테마 선택 (데이터베이스 전환)" style="display:none"' in html
    assert '기본 지식베이스' in html


def test_theme_web_multi_mode_inlining(tmp_path: Path):
    """멀티 테마 모드: 2개 이상 테마 존재 시 인라인 렌더링 및 디스플레이 활성화 검증."""
    from claire.store.theme import ThemeManager

    s = Settings(
        db_path=str(tmp_path / "claire.db"),
        vault_path=str(tmp_path / "vault"),
        CLAIRE_MULTI_THEME=True,
    )
    tm = ThemeManager(s)
    tm.define_theme("AI 및 로보틱스", description="인공지능 연구", icon="🤖")

    html = render_graph_html(s)
    assert 'themeMode: \'multi\'' in html
    assert 'id="theme-picker-wrap" title="지식 테마 선택 (데이터베이스 전환)" style="display:inline-flex"' in html
    assert 'AI 및 로보틱스' in html
    assert '🤖' in html
