import json
from unittest.mock import patch
import pytest

from claire import cli
from claire.config import Settings
from claire.store.theme import ThemeManager


@pytest.fixture
def cli_theme_env(tmp_path, monkeypatch):
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
    )
    monkeypatch.setattr("claire.cli.get_settings", lambda: settings)
    tm = ThemeManager(settings)
    monkeypatch.setattr("claire.store.theme.get_theme_manager", lambda s=None: tm)
    return settings, tm


def test_cli_theme_list(cli_theme_env, capsys):
    ret = cli.main(["theme", "list"])
    assert ret == 0
    captured = capsys.readouterr().out
    assert "Claire 지식베이스 테마 목록" in captured
    assert "기본 지식베이스" in captured


def test_cli_theme_define_update_delete(cli_theme_env, capsys):
    settings, tm = cli_theme_env

    # 1. 테마 정의
    ret = cli.main(["theme", "define", "--label", "개발 및 시스템", "--desc", "소프트웨어 아키텍처", "--icon", "💻"])
    assert ret == 0
    captured = capsys.readouterr().out
    assert "새 테마 #1 정의 완료" in captured
    assert "개발 및 시스템" in captured

    # 2. 테마 목록 JSON 출력 확인
    ret = cli.main(["theme", "list", "--json"])
    assert ret == 0
    raw_json = capsys.readouterr().out
    data = json.loads(raw_json)
    themes = data["themes"]
    assert len(themes) == 2
    t1 = next(t for t in themes if t["id"] == 1)
    assert t1["label"] == "개발 및 시스템"
    assert t1["icon"] == "💻"

    # 3. 테마 레이블 및 아이콘 수정
    ret = cli.main(["theme", "update", "1", "--label", "엔지니어링 & AI", "--icon", "🚀"])
    assert ret == 0
    captured = capsys.readouterr().out
    assert "메타데이터 수정 완료" in captured

    # 4. stats with -t
    ret = cli.main(["stats", "-t", "1"])
    assert ret == 0
    captured = capsys.readouterr().out
    assert "엔지니어링 & AI" in captured

    # 5. 테마 삭제 (purge)
    ret = cli.main(["theme", "delete", "1", "--purge", "--yes"])
    assert ret == 0
    captured = capsys.readouterr().out
    assert "삭제 완료" in captured

    # 6. 목록에서 제거되었는지 확인
    tm.reload()
    assert 1 not in [t.id for t in tm.list_themes()]
