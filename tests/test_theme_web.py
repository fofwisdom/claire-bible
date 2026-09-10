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


def test_render_graph_html_visibility_isolation(tmp_path: Path):
    """비공개 테마는 익명 사용자용 render_graph_html(include_private=False)에서 배제됨을 검증."""
    from claire.store.theme import ThemeManager

    s = Settings(
        db_path=str(tmp_path / "claire.db"),
        vault_path=str(tmp_path / "vault"),
        CLAIRE_MULTI_THEME=True,
    )
    tm = ThemeManager(s)
    tm.define_theme("비밀 연구", description="내부 전용", icon="🔒", is_public=False)

    # 1. 익명 사용자 (include_private=False) -> 비공개 테마 제외되어 공개 테마가 기본 테마 1개뿐이므로 피커 숨김
    anon_html = render_graph_html(s, include_private=False)
    assert '비밀 연구' not in anon_html
    assert 'style="display:none"' in anon_html

    # 2. 인증된 사용자 (include_private=True) -> 비공개 테마 포함 및 피커 표시
    owner_html = render_graph_html(s, include_private=True)
    assert '비밀 연구' in owner_html
    assert 'style="display:inline-flex"' in owner_html
    assert 'id="thememanagebtn"' in owner_html


def test_theme_selector_ux_and_modification_features(tmp_path: Path):
    """테마 선택기의 단일 아이콘/일련번호 미표시 및 웹 UI 테마 변경 기능 탑재 검증."""
    s = Settings(
        db_path=str(tmp_path / "claire.db"),
        vault_path=str(tmp_path / "vault"),
        CLAIRE_MULTI_THEME=True,
    )
    html = render_graph_html(s)

    # 1. 헤더 선택기 옵션에 중복 아이콘(${icon}) 및 일련번호(${idStr})가 포함되지 않음 확인
    assert "function renderThemeSelector()" in html
    # renderThemeSelector 내에서 `<option value="${t.id}" ...>${label}${lockStr}</option>` 패턴으로 렌더링됨
    assert "${label}${lockStr}</option>" in html

    # 2. 테마 관리(openThemeManager) 내에 테마 수정 UI(수정 버튼, 수정 폼) 및 전송 함수 탑재 확인
    assert "showThemeEditForm" in html
    assert "hideThemeEditForm" in html
    assert "updateThemeFromUI" in html
    assert "editthemep-label-" in html
    assert "editthemep-desc-" in html
    assert "editthemep-icon-" in html
    assert "editthemep-pub-" in html
    assert "✏️ 수정" in html

    # 3. 기본 지식베이스(ID 0) 제외 추가 테마 삭제 UI(삭제 버튼 및 deleteThemeFromUI 함수) 탑재 확인
    assert "deleteThemeFromUI" in html
    assert "기본 지식베이스(기본 테마)는 삭제할 수 없습니다" in html
    assert "🗑️ 삭제" in html
    assert "🗑️ 테마 완전 삭제" in html


