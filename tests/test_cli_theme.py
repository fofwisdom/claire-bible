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


def test_get_effective_settings_doc_target_auto_resolve(cli_theme_env):
    """--theme 미지정 시 doc_target(또는 args.target)으로 대상을 검색하여 테마를 자동 해소하는지 검증."""
    import argparse
    from claire.ontology.base import Document
    from claire.store import db as dbm

    settings, tm = cli_theme_env
    t1 = tm.define_theme("클라우드 엔지니어링")
    assert t1.id == 1

    t1_settings = tm.get_settings_for_theme(1)
    conn1 = dbm.connect(t1_settings.db_file)
    dbm.init_db(conn1)
    doc = Document(
        id="doc_t1_auto",
        url="https://example.com/auto",
        canonical_url="https://example.com/auto",
        title="Auto Resolved Doc",
        raw_text="Auto content",
        summary="Auto summary",
        source_type="web",
        content_hash="h_auto_1",
    )
    dbm.insert_document(conn1, doc)
    conn1.close()

    # 1. args.target에 doc_id 전달 시 자동 해소
    args1 = argparse.Namespace(theme=None, target="doc_t1_auto")
    eff_s, eff_theme = cli.get_effective_settings(args1)
    assert eff_theme is not None
    assert eff_theme.id == 1
    assert str(eff_s.db_file) == str(t1_settings.db_file)

    # 2. doc_target 인자로 전달 시 자동 해소
    args2 = argparse.Namespace(theme=None, target=None)
    eff_s2, eff_theme2 = cli.get_effective_settings(args2, doc_target="doc_t1_auto")
    assert eff_theme2 is not None
    assert eff_theme2.id == 1

    # 3. 존재하지 않는 대상인 경우 기본 테마(None) 반환
    args3 = argparse.Namespace(theme=None, target="non_existent_doc")
    eff_s3, eff_theme3 = cli.get_effective_settings(args3)
    assert eff_theme3 is None


def test_cli_subparsers_accept_theme_flag(cli_theme_env):
    """subparsers(regenerate, summary-regenerate, doc-title, dedup-merge, recanonicalize, purge)가 -t/--theme을 지원하는지 검증."""
    parser = cli.build_parser()

    # 1. regenerate
    args = parser.parse_args(["regenerate", "-t", "1", "doc_123"])
    assert args.theme == "1"
    assert args.target == "doc_123"

    # 2. summary-regenerate
    args = parser.parse_args(["summary-regenerate", "--theme", "인프라", "doc_123"])
    assert args.theme == "인프라"

    # 3. doc-title
    args = parser.parse_args(["doc-title", "-t", "1", "doc_123", "새 제목"])
    assert args.theme == "1"
    assert args.title == "새 제목"

    # 4. dedup-merge
    args = parser.parse_args(["dedup-merge", "-t", "1"])
    assert args.theme == "1"

    # 5. recanonicalize
    args = parser.parse_args(["recanonicalize", "-t", "1"])
    assert args.theme == "1"

    # 6. purge
    args = parser.parse_args(["purge", "-t", "1", "doc_123"])
    assert args.theme == "1"


def test_cli_theme_dispatch_commands(cli_theme_env, capsys, monkeypatch):
    """각 명령이 테마 옵션에 따라 해당 테마 DB를 대상으로 정상 동작하는지 검증."""
    from claire.ontology.base import Document
    from claire.store import db as dbm

    monkeypatch.setenv("CLAIRE_ALLOW_PURGE", "1")
    settings, tm = cli_theme_env
    settings.allow_purge = True
    t1 = tm.define_theme("데이터베이스 테마")
    t1_settings = tm.get_settings_for_theme(1)

    conn1 = dbm.connect(t1_settings.db_file)
    dbm.init_db(conn1)
    doc = Document(
        id="doc_theme_test",
        url="https://example.com/test",
        canonical_url="https://example.com/test",
        title="Original Title",
        raw_text="Document text content for test",
        summary="Document summary",
        source_type="web",
        content_hash="h_theme_test_1",
    )
    dbm.insert_document(conn1, doc)
    conn1.close()

    # 1. doc-title with -t 1
    ret = cli.main(["doc-title", "-t", "1", "doc_theme_test", "Updated Theme Title"])
    assert ret == 0
    out = capsys.readouterr().out
    assert "제목 갱신 완료" in out
    conn1 = dbm.connect(t1_settings.db_file)
    updated_doc = dbm.get_document(conn1, "doc_theme_test")
    conn1.close()
    assert updated_doc.title == "Updated Theme Title"

    # 2. purge with -t 1 (dry-run)
    ret = cli.main(["purge", "-t", "1", "doc_theme_test"])
    assert ret == 0
    out = capsys.readouterr().out
    assert "소각 대상 분석 보고서" in out
    assert "Updated Theme Title" in out

    # 3. recanonicalize with -t 1 (dry-run)
    ret = cli.main(["recanonicalize", "-t", "1"])
    assert ret == 0
    out = capsys.readouterr().out
    assert "dry-run" in out or "문서" in out

    # 4. dedup-merge with -t 1 (dry-run)
    ret = cli.main(["dedup-merge", "-t", "1"])
    assert ret == 0




