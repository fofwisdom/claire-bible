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
        CLAIRE_MULTI_THEME=True,
    )
    monkeypatch.setattr("claire.cli.get_settings", lambda: settings)
    tm = ThemeManager(settings)
    monkeypatch.setattr("claire.store.theme.get_theme_manager", lambda s=None: tm)
    return settings, tm


def test_cli_theme_single_mode(tmp_path, monkeypatch, capsys):
    """CLAIRE_MULTI_THEME=False 상태에서 CLI 명령 방어 및 단일 테마 목록 출력 검증."""
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
    monkeypatch.setattr("claire.cli.get_settings", lambda: settings)
    tm = ThemeManager(settings)
    monkeypatch.setattr("claire.store.theme.get_theme_manager", lambda s=None: tm)

    # 1. list 명령 -> 안내문구 및 기본 지식베이스 출력
    ret = cli.main(["theme", "list"])
    assert ret == 0
    captured = capsys.readouterr().out
    assert "싱글 테마 모드" in captured
    assert "기본 지식베이스" in captured
    assert "CLAIRE_MULTI_THEME=1" in captured

    # 2. define 명령 -> 차단 및 에러 메시지
    ret = cli.main(["theme", "define", "--label", "금지된 테마"])
    assert ret == 1
    err = capsys.readouterr().err
    assert "멀티 테마 모드가 비활성화되어 있습니다" in err

    # 3. update 명령 -> 차단 및 에러 메시지
    ret = cli.main(["theme", "update", "0", "--label", "수정 시도"])
    assert ret == 1
    err = capsys.readouterr().err
    assert "멀티 테마 모드가 비활성화되어 있습니다" in err

    # 4. delete 명령 -> 차단 및 에러 메시지
    ret = cli.main(["theme", "delete", "1"])
    assert ret == 1
    err = capsys.readouterr().err
    assert "멀티 테마 모드가 비활성화되어 있습니다" in err

    # 5. stats --theme=1 -> 경고 후 기본 테마 진행
    ret = cli.main(["stats", "-t", "1"])
    assert ret == 0
    err = capsys.readouterr().err
    assert "--theme=1 옵션이 무시되고 기본 테마가 사용됩니다" in err


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


def test_cli_theme_visibility_flags(cli_theme_env, capsys):
    """CLI에서 --public / --private 옵션으로 테마 공개 여부 정의 및 수정 검증."""
    settings, tm = cli_theme_env

    # 1. 비공개 테마 정의 (--private)
    ret = cli.main(["theme", "define", "--label", "비공개 보안", "--private", "--desc", "내부 전용"])
    assert ret == 0
    captured = capsys.readouterr().out
    assert "비공개 (Private 🔒)" in captured

    # 2. 목록 확인 (테이블 및 JSON)
    ret = cli.main(["theme", "list"])
    assert ret == 0
    table_out = capsys.readouterr().out
    assert "비공개 🔒" in table_out

    ret = cli.main(["theme", "list", "--json"])
    assert ret == 0
    data = json.loads(capsys.readouterr().out)
    t1 = next(t for t in data["themes"] if t["id"] == 1)
    assert t1["is_public"] is False

    # 3. 공개 테마로 수정 (--public)
    ret = cli.main(["theme", "update", "1", "--public"])
    assert ret == 0
    captured_update = capsys.readouterr().out
    assert "공개 (Public)" in captured_update

    ret = cli.main(["theme", "list", "--json"])
    assert ret == 0
    data_after = json.loads(capsys.readouterr().out)
    t1_after = next(t for t in data_after["themes"] if t["id"] == 1)
    assert t1_after["is_public"] is True


def test_cli_theme_update_full_options(cli_theme_env, capsys):
    """CLI에서 생성 시 지원하는 모든 옵션(--label, --desc, --icon, --public/--private) 및 이름 기반 수정 검증."""
    settings, tm = cli_theme_env

    # 1. 새 테마 정의
    ret = cli.main(["theme", "define", "--label", "초기 테마", "--desc", "설명 원본", "--icon", "📁"])
    assert ret == 0
    capsys.readouterr()

    # 2. 모든 옵션 수정 (ID로 지정)
    ret = cli.main([
        "theme", "update", "1",
        "--label", "전면 개편",
        "--description", "업데이트된 상세 설명",
        "--icon", "🚀",
        "--private",
    ])
    assert ret == 0
    captured = capsys.readouterr().out
    assert "전면 개편" in captured
    assert "업데이트된 상세 설명" in captured
    assert "🚀" in captured
    assert "비공개 (Private 🔒)" in captured

    # 3. 테마 이름(레이블)으로 찾아 수정
    ret = cli.main([
        "theme", "update", "전면 개편",
        "--desc", "이름 기반으로 바꾼 설명",
        "--public",
    ])
    assert ret == 0
    captured_by_name = capsys.readouterr().out
    assert "이름 기반으로 바꾼 설명" in captured_by_name
    assert "공개 (Public)" in captured_by_name


def test_cli_theme_delete_additional_only(cli_theme_env, capsys):
    """CLI에서 기본 테마(0) 삭제 차단 및 추가 테마 삭제 검증."""
    settings, tm = cli_theme_env

    # 1. 기본 테마(0) 삭제 시도 -> 에러 차단
    ret = cli.main(["theme", "delete", "0", "-y"])
    assert ret == 1
    err = capsys.readouterr().err
    assert "기본" in err and "삭제할 수 없습니다" in err

    # 2. 추가 테마 생성
    ret = cli.main(["theme", "define", "--label", "삭제용 테마"])
    assert ret == 0
    capsys.readouterr()

    # 3. 레이블 이름으로 추가 테마 삭제
    ret = cli.main(["theme", "delete", "삭제용 테마", "--purge", "-y"])
    assert ret == 0
    out = capsys.readouterr().out
    assert "삭제 완료" in out
    assert "삭제용 테마" in out


def test_cli_theme_default_focus(cli_theme_env, capsys):
    """CLI에서 --focus / --default-focus 옵션으로 기본 초점 정의 및 수정 검증."""
    settings, tm = cli_theme_env

    # 1. default_focus와 함께 테마 정의
    ret = cli.main([
        "theme", "define",
        "--label", "클라우드 인프라",
        "--focus", "쿠버네티스 및 분산 시스템 아키텍처 중심",
    ])
    assert ret == 0
    out = capsys.readouterr().out
    assert "기본 초점   : 쿠버네티스 및 분산 시스템 아키텍처 중심" in out

    # 2. theme list 출력 시 기본 초점 표시 확인
    ret = cli.main(["theme", "list"])
    assert ret == 0
    list_out = capsys.readouterr().out
    assert "기본 초점: 쿠버네티스 및 분산 시스템 아키텍처 중심" in list_out

    # 3. theme update로 기본 초점 수정
    ret = cli.main([
        "theme", "update", "1",
        "--focus", "클라우드 비용 최적화 및 FinOps 관점",
    ])
    assert ret == 0
    update_out = capsys.readouterr().out
    assert "기본 초점  : 클라우드 비용 최적화 및 FinOps 관점" in update_out

    # 4. theme list --json 출력 확인
    ret = cli.main(["theme", "list", "--json"])
    assert ret == 0
    data = json.loads(capsys.readouterr().out)
    t1 = next(t for t in data["themes"] if t["id"] == 1)
    assert t1["default_focus"] == "클라우드 비용 최적화 및 FinOps 관점"



