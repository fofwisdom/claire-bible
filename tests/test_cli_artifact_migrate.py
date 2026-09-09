"""Tests for the `claire artifact-migrate` CLI command."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

from claire.cli import build_parser, cmd_artifact_migrate
from claire.config import get_settings


def test_cli_parser_artifact_migrate():
    parser = build_parser()
    args = parser.parse_args(["artifact-migrate"])
    assert args.func == cmd_artifact_migrate
    assert args.apply is False
    assert args.dry_run is False
    assert args.level == 3
    assert args.json is False
    assert args.stop_on_error is False

    args_custom = parser.parse_args([
        "artifact-migrate",
        "--apply",
        "--level", "5",
        "--stop-on-error",
        "--json",
    ])
    assert args_custom.apply is True
    assert args_custom.level == 5
    assert args_custom.stop_on_error is True
    assert args_custom.json is True


def test_cmd_artifact_migrate_dry_run_and_apply(tmp_path: Path, monkeypatch, capsys):
    data_dir = tmp_path / "data"
    art_dir = data_dir / "raw" / "artifacts"
    art_dir.mkdir(parents=True, exist_ok=True)

    # 샘플 레거시 gz 생성
    for i in range(3):
        with gzip.open(art_dir / f"cli_doc_{i}.txt.gz", "wt", encoding="utf-8") as f:
            f.write(f"Sample body for cli doc {i} " * 50)

    monkeypatch.setenv("CLAIRE_DB_PATH", str(data_dir / "claire.db"))
    get_settings.cache_clear()
    try:
        parser = build_parser()

        # 1. Dry-run 실행
        args_dry = parser.parse_args(["artifact-migrate"])
        rc_dry = cmd_artifact_migrate(args_dry)
        assert rc_dry == 0
        captured_dry = capsys.readouterr()
        assert "DRY-RUN (시뮬레이션)" in captured_dry.out
        assert "총 아티팩트 대상: 3건" in captured_dry.out
        assert "변환 완료/예정:   3건" in captured_dry.out
        # 파일 상태 불변
        assert len(list(art_dir.glob("*.txt.gz"))) == 3
        assert len(list(art_dir.glob("*.txt.zst"))) == 0

        # 2. JSON 포맷 실행 (dry-run)
        args_json = parser.parse_args(["artifact-migrate", "--json"])
        rc_json = cmd_artifact_migrate(args_json)
        assert rc_json == 0
        captured_json = capsys.readouterr()
        data = json.loads(captured_json.out)
        assert data["total"] == 3
        assert data["migrated"] == 3
        assert data["dry_run"] is True
        assert data["errors"] == 0

        # 3. Apply 실행
        args_apply = parser.parse_args(["artifact-migrate", "--apply"])
        rc_apply = cmd_artifact_migrate(args_apply)
        assert rc_apply == 0
        captured_apply = capsys.readouterr()
        assert "APPLY (실제 변환)" in captured_apply.out
        assert "변환 완료/예정:   3건" in captured_apply.out
        # gz는 삭제되고 zst가 생성됨
        assert len(list(art_dir.glob("*.txt.gz"))) == 0
        assert len(list(art_dir.glob("*.txt.zst"))) == 3

        # 4. 재실행 (이미 변환된 상태)
        args_again = parser.parse_args(["artifact-migrate", "--apply", "--json"])
        rc_again = cmd_artifact_migrate(args_again)
        assert rc_again == 0
        captured_again = capsys.readouterr()
        data_again = json.loads(captured_again.out)
        assert data_again["total"] == 3
        assert data_again["already_zst"] == 3
        assert data_again["migrated"] == 0
    finally:
        get_settings.cache_clear()
