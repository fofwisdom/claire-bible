from __future__ import annotations

from pathlib import Path
from starlette.testclient import TestClient

from claire.api import server
from claire.config import Settings
from claire.graphview import render_graph_html

# 테마 관리 UI는 루트 상세 메뉴에서 빠지고 콘솔(/settings/themes, /admin/themes)이 쓰는 공유 모듈에만 있다.
MANAGE_PANELS_JS = (Path(__file__).resolve().parents[1] / "src" / "claire" / "static" / "js" / "manage_panels.js").read_text(encoding="utf-8")


def test_theme_web_ui_elements_in_template_and_graph_html(tmp_path: Path):
    s = Settings(
        db_path=str(tmp_path / "claire.db"),
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_FQDN="127.0.0.1",
    )
    html = render_graph_html(s)

    # 1. Header theme selector
    assert 'id="theme-picker-wrap"' in html
    assert 'id="theme-select"' in html
    assert 'id="theme-curr-icon"' in html
    assert 'id="theme-plain-display"' in html
    assert 'id="theme-plain-icon"' in html
    assert 'id="theme-plain-label"' in html
    assert 'onchange="switchKnowledgeTheme(this.value)"' in html

    # 2. Ingest form theme selector
    assert 'id="ingtheme"' not in html  # ingest writes only to the personal knowledge base (CT23)
    assert "renderThemeOptions" in html

    # 3. JavaScript theme management functions
    assert "async function fetchThemes()" in html
    assert "function renderThemeSelector()" in html
    assert "async function switchKnowledgeTheme(" in html
    assert "async function reloadThemeData(" in html
    assert "async function loadThemeDocuments()" in html
    assert "X-Claire-Theme" not in html  # the path selects the knowledge base (CT23)
    assert "claireKnowledgeTheme" not in html

    # 4. CSS styling
    assert ".theme-picker-wrap" in html
    assert ".theme-select-control" in html
    assert ".theme-plain-display" in html
    assert ".theme-select" in html
    assert ".ingest-theme-select" in html


def test_theme_web_root_endpoint_serves_selector(tmp_path: Path):
    s = Settings(
        db_path=str(tmp_path / "claire.db"),
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_FQDN="127.0.0.1",
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
    assert 'id="theme-picker-wrap" title="Select the knowledge theme (change the database)" style="display:none"' in html
    assert '기본 지식베이스' in html


def test_theme_web_multi_mode_inlining(tmp_path: Path):
    """멀티 테마 모드: 2개 이상 테마 존재 시 인라인 렌더링 및 디스플레이 활성화 검증."""
    from claire.store.knowledge_base import KnowledgeBaseManager

    s = Settings(
        db_path=str(tmp_path / "claire.db"),
        vault_path=str(tmp_path / "vault"),
        CLAIRE_MULTI_THEME=True,
    )
    tm = KnowledgeBaseManager(s)
    tm.define_kb("AI 및 로보틱스", description="인공지능 연구", icon="🤖", knowledge_type="real")

    html = render_graph_html(s)
    assert 'themeMode: \'multi\'' in html
    assert 'id="theme-picker-wrap" title="Select the knowledge theme (change the database)" style="display:inline-flex"' in html
    assert 'AI 및 로보틱스' in html
    assert '🤖' in html


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

    # 2. 테마 관리(콘솔 공유 모듈 renderThemeManager) 내에 테마 수정 UI(수정 버튼, 수정 폼) 및 전송 함수 탑재 확인
    assert "openThemeManager" not in html
    html = MANAGE_PANELS_JS
    assert "showThemeEditForm" in html
    assert "hideThemeEditForm" in html
    assert "updateThemeFromUI" in html
    assert "editthemep-label-" in html
    assert "editthemep-desc-" in html
    assert "editthemep-icon-" in html
    assert "editthemep-pub-" in html
    assert '<span data-icon="edit"></span> ${esc(trKey("console.js.panels.themes.edit"))}' in html

    # 3. 기본 지식베이스(ID 0) 제외 추가 테마 삭제 UI(삭제 버튼 및 deleteThemeFromUI 함수) 탑재 확인
    assert "deleteThemeFromUI" in html
    assert 'alert(trKey("console.js.panels.themes.default_no_delete"))' in html
    assert '<span data-icon="trash"></span> ${esc(trKey("common.action.delete"))}' in html
    assert '<span data-icon="trash"></span> ${esc(trKey("console.js.panels.themes.delete_full"))}' in html


def test_theme_web_knowledge_manager_display(tmp_path: Path):
    """테마 관리에서 테마 지식베이스의 '지식 관리자' 표시 및 UI 요소 검증."""
    from claire.store.knowledge_base import KnowledgeBaseManager

    s = Settings(
        db_path=str(tmp_path / "claire.db"),
        vault_path=str(tmp_path / "vault"),
        CLAIRE_MULTI_THEME=True,
        CLAIRE_SORCERER="default_owner",
    )
    tm = KnowledgeBaseManager(s)
    tm.define_kb("일반 테마", knowledge_type="real")  # 기본 관리자 상속
    tm.define_kb("특화 테마", sorcerer="specialist_lee", knowledge_type="real")  # 개별 관리자 지정

    graph_html = render_graph_html(s, include_private=True)
    html = MANAGE_PANELS_JS

    # 1. 테마 관리 UI 렌더링 내 지식 관리자 표시 로직 탑재 확인
    assert '${esc(trKey("console.js.panels.themes.manager_prefix"))} ${esc(effMgr)}' in html

    # 2. 테마 설정 옵션 상세 뷰(theme-options-view)에 지식 관리자 항목 확인
    assert '<div>• <b>${esc(trKey("console.js.panels.themes.opt_manager"))}</b>:' in html

    # 3. 테마 수정 폼 및 새 테마 추가 폼에 지식 관리자 입력 필드 확인
    assert "editthemep-sorcerer-" in html
    assert 'id="newthemep-sorcerer"' in html

    # 4. updateDrawerManager 함수를 통해 테마 선택/전환 시 drawerfooter의 지식 관리자 실시간 업데이트 로직 탑재 확인
    assert "function updateDrawerManager(currentTheme)" in graph_html
    assert "updateDrawerManager(current)" in graph_html



