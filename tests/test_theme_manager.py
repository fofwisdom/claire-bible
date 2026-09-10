import tempfile
import shutil
from pathlib import Path
import pytest

from claire.config import Settings
from claire.store.theme import ThemeManager, ThemeInfo


@pytest.fixture
def temp_theme_env(tmp_path):
    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    data_dir.mkdir(parents=True)
    vault_dir.mkdir(parents=True)

    db_path = str(data_dir / "claire.db")
    vault_path = str(vault_dir)

    settings = Settings(
        CLAIRE_DB_PATH=db_path,
        CLAIRE_VAULT_PATH=vault_path,
        CLAIRE_PROVIDER="mock",
        CLAIRE_MULTI_THEME=True,
    )
    manager = ThemeManager(base_settings=settings)
    yield manager, data_dir, vault_dir


def test_theme_manager_single_mode(tmp_path):
    """CLAIRE_MULTI_THEME=False 모드에서 themes.json 미생성 및 변경 연산 차단 검증."""
    data_dir = tmp_path / "single_data"
    vault_dir = tmp_path / "single_vault"
    data_dir.mkdir(parents=True)
    vault_dir.mkdir(parents=True)

    settings = Settings(
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CLAIRE_VAULT_PATH=str(vault_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_MULTI_THEME=False,
    )
    manager = ThemeManager(base_settings=settings)
    themes = manager.list_themes()
    assert len(themes) == 1
    assert themes[0].id == 0
    assert themes[0].label == "기본 지식베이스"

    # 싱글 모드에서는 디스크에 themes.json을 생성하지 않음 (0-Disk I/O)
    registry_file = data_dir / "themes.json"
    assert not registry_file.exists()

    with pytest.raises(RuntimeError, match="멀티 테마 모드가 비활성화되어 있습니다"):
        manager.define_theme("새 테마")

    with pytest.raises(RuntimeError, match="멀티 테마 모드가 비활성화되어 있습니다"):
        manager.update_theme(0, label="수정")

    with pytest.raises(RuntimeError, match="멀티 테마 모드가 비활성화되어 있습니다"):
        manager.delete_theme(0)


def test_default_theme_initialization(temp_theme_env):
    manager, data_dir, _ = temp_theme_env
    themes = manager.list_themes()
    assert len(themes) == 1
    t0 = themes[0]
    assert t0.id == 0
    assert t0.seq == 0
    assert t0.is_default is True
    assert t0.label == "기본 지식베이스"
    assert "claire.db" in t0.db_path


def test_define_new_theme_uses_sequence(temp_theme_env):
    manager, data_dir, vault_dir = temp_theme_env
    t1 = manager.define_theme("기술 및 AI", description="테크 연구", icon="💻")
    assert t1.id == 1
    assert t1.seq == 1
    assert t1.label == "기술 및 AI"
    assert t1.is_default is False
    # 물리 경로는 순수 일련번호 '1'을 사용해야 함
    assert "themes/1/claire.db" in t1.db_path.replace("\\", "/")
    assert "themes/1" in t1.vault_path.replace("\\", "/")

    # 두 번째 테마 정의 시 seq 2 발급
    t2 = manager.define_theme("철학 및 인문학", icon="🏛️")
    assert t2.id == 2
    assert t2.seq == 2
    assert "themes/2/claire.db" in t2.db_path.replace("\\", "/")


def test_update_theme_label_does_not_change_folder(temp_theme_env):
    manager, _, _ = temp_theme_env
    t1 = manager.define_theme("초기 레이블", icon="📁")
    orig_db_path = t1.db_path
    orig_vault_path = t1.vault_path

    # 레이블 및 아이콘 수정
    updated = manager.update_theme(1, label="수정된 새 레이블", icon="🤖")
    assert updated.label == "수정된 새 레이블"
    assert updated.icon == "🤖"
    # 물리 폴더 경로는 절대 변경되지 않아야 함
    assert updated.db_path == orig_db_path
    assert updated.vault_path == orig_vault_path

    # get_theme 으로 조회 시에도 레이블로 찾을 수 있음
    found_by_label = manager.get_theme("수정된 새 레이블")
    assert found_by_label.id == 1
    found_by_id = manager.get_theme(1)
    assert found_by_id.label == "수정된 새 레이블"


def test_cannot_delete_default_theme(temp_theme_env):
    manager, _, _ = temp_theme_env
    with pytest.raises(ValueError, match="기본 테마.*삭제할 수 없습니다"):
        manager.delete_theme(0)


def test_delete_and_purge_custom_theme(temp_theme_env):
    manager, _, _ = temp_theme_env
    t1 = manager.define_theme("임시 테마")
    assert manager.get_theme(t1.id).id == t1.id

    manager.delete_theme(t1.id, purge=True)
    assert len(manager.list_themes()) == 1
    # 삭제된 ID 조회 시 기본 테마 0 반환 (strict=False)
    assert manager.get_theme(t1.id).id == 0


def test_theme_visibility_define_and_update(temp_theme_env):
    """지식 관리자의 테마별 공개/비공개 설정 및 조회 격리 검증."""
    manager, _, _ = temp_theme_env

    # 1. 비공개 테마 생성 (is_public=False)
    t1 = manager.define_theme("비공개 전략", is_public=False)
    assert t1.id == 1
    assert t1.is_public is False

    # 2. 공개 테마 생성 (is_public=True)
    t2 = manager.define_theme("공개 브리핑", is_public=True)
    assert t2.id == 2
    assert t2.is_public is True

    # 3. include_private=True 시 전체 조회
    all_themes = manager.list_themes(include_private=True)
    assert len(all_themes) == 3
    assert any(t.id == 1 and not t.is_public for t in all_themes)
    assert any(t.id == 2 and t.is_public for t in all_themes)

    # 4. include_private=False 시 비공개 테마(t1) 배제
    public_themes = manager.list_themes(include_private=False)
    assert len(public_themes) == 2
    assert not any(t.id == 1 for t in public_themes)
    assert any(t.id == 2 for t in public_themes)

    # 5. get_theme 에서 include_private=False 동작
    with pytest.raises(KeyError, match="비공개 테마"):
        manager.get_theme(1, strict=True, include_private=False)

    # strict=False 시 기본 공개 테마로 fallback
    fallback = manager.get_theme(1, strict=False, include_private=False)
    assert fallback.id == 0

    # 6. 테마 공개 여부 업데이트 (비공개 -> 공개)
    updated = manager.update_theme(1, is_public=True)
    assert updated.is_public is True
    public_themes_after = manager.list_themes(include_private=False)
    assert len(public_themes_after) == 3
    assert any(t.id == 1 for t in public_themes_after)
