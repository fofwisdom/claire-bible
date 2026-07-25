"""deploy.sh 설정 로딩과 원격 명령 조립을 네트워크 없이 검증한다."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy.sh"


def _executable(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    path.chmod(0o755)


def _run_deploy(
    tmp_path: Path,
    dotenv: str | None,
    *,
    ssh_exec_guard: bool = False,
    ssh_guard_status: int = 0,
    ssh_test_status: int = 1,
    extra_env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    env_file = tmp_path / ".env.test"
    if dotenv is not None:
        env_file.write_text(textwrap.dedent(dotenv), encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"

    _executable(
        bin_dir / "ssh",
        r"""
        #!/usr/bin/env bash
        set -u
        {
          printf 'ssh'
          for arg in "$@"; do printf '\t%s' "$arg"; done
          printf '\n'
        } >> "${CALL_LOG:?}"
        case "$*" in
          *"unexpected="*)
            if [ "${SSH_EXEC_GUARD:-0}" = "1" ]; then
              command_to_run=''
              for arg in "$@"; do command_to_run="$arg"; done
              /bin/sh -c "$command_to_run"
              exit $?
            fi
            exit "${SSH_GUARD_STATUS:-0}"
            ;;
          *"test -f"*) exit "${SSH_TEST_STATUS:-1}" ;;
        esac
        """,
    )
    _executable(
        bin_dir / "rsync",
        r"""
        #!/usr/bin/env bash
        set -u
        {
          printf 'rsync'
          for arg in "$@"; do printf '\t%s' "$arg"; done
          printf '\n'
        } >> "${CALL_LOG:?}"
        """,
    )

    env = os.environ.copy()
    for name in (
        "DEPLOY_REMOTE",
        "DEPLOY_PORT",
        "DEPLOY_PATH",
        "DEPLOY_ENV_SYNC",
        "DEPLOY_ACTION",
        "DEPLOY_ENV_FILE",
        "DEPLOY_APP_ENV_FILE",
        "SKIP_CI",
        "CLAIRE_ENVIRONMENT",
    ):
        env.pop(name, None)
    env.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{env.get('PATH', os.defpath)}",
            "CALL_LOG": str(calls),
            "DEPLOY_ENV_FILE": str(env_file),
            # 기존 개별 테스트는 같은 fixture를 접속/런타임 설정으로 함께 사용한다.
            # 실제 기본값은 .env.deploy(접속) + .env(런타임)로 분리된다.
            "DEPLOY_APP_ENV_FILE": str(env_file),
            "SKIP_CI": "1",
            "CLAIRE_ENVIRONMENT": "production",
            "SSH_EXEC_GUARD": "1" if ssh_exec_guard else "0",
            "SSH_GUARD_STATUS": str(ssh_guard_status),
            "SSH_TEST_STATUS": str(ssh_test_status),
        }
    )
    if extra_env:
        env.update(extra_env)

    result = subprocess.run(
        ["bash", str(DEPLOY)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, calls, env_file


class DeployScriptTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._temp_dir.name)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_ci_gate_does_not_leak_remote_environment_selector(self):
        script = DEPLOY.read_text(encoding="utf-8")
        self.assertIn(
            "env -u CLAIRE_ENVIRONMENT bash ./scripts/ci.sh",
            script,
        )

    def test_reads_dotenv_and_syncs_it_without_executing_contents(self):
        marker = self.tmp_path / "dotenv-was-executed"
        result, calls, env_file = _run_deploy(
            self.tmp_path,
            f"""
            DEPLOY_REMOTE="alice@kb.example" # target
            DEPLOY_PORT=2200
            DEPLOY_PATH='/srv/claire' # destination
            DEPLOY_ENV_SYNC=always # local file is canonical
            UNRELATED=$(touch {marker})
            """,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        logged = calls.read_text(encoding="utf-8")
        self.assertIn("alice@kb.example", logged)
        self.assertIn("/srv/claire/data", logged)
        self.assertIn(f"\t{env_file}\talice@kb.example:/srv/claire/.env", logged)
        self.assertIn("\t--include\t/.env.example", logged)
        self.assertIn("\t--include\t/.env.dev.example", logged)
        self.assertIn("\t--include\t/.env.deploy.example", logged)
        self.assertIn("\t--exclude\t.cb-manuscript", logged)
        self.assertIn("\t--exclude\t.env.*", logged)
        self.assertIn(".claire-deploy-root", logged)
        self.assertIn("chmod 600 '/srv/claire/.env'", logged)
        self.assertIn(
            "CLAIRE_ENVIRONMENT=production bash ./cb-manuscript update --no-fetch",
            logged,
        )
        self.assertIn(
            "CLAIRE_ENVIRONMENT=production bash ./cb-manuscript status",
            logged,
        )
        self.assertFalse(marker.exists())

    def test_deploy_and_runtime_env_files_are_separate(self):
        app_env = self.tmp_path / ".env.runtime"
        app_env.write_text(
            "CLAIRE_PROVIDER=mock\nCLAIRE_INJECT_TOKEN=runtime-secret\n",
            encoding="utf-8",
        )
        result, calls, deploy_env = _run_deploy(
            self.tmp_path,
            """
            DEPLOY_REMOTE=alice@kb.example
            DEPLOY_PATH=/srv/claire
            DEPLOY_ENV_SYNC=always
            """,
            extra_env={"DEPLOY_APP_ENV_FILE": str(app_env)},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        logged = calls.read_text(encoding="utf-8")
        self.assertIn(f"\t{app_env}\talice@kb.example:/srv/claire/.env", logged)
        self.assertNotIn(f"\t{deploy_env}\talice@kb.example:/srv/claire/.env", logged)

    def test_install_action_invokes_remote_install(self):
        result, calls, _ = _run_deploy(
            self.tmp_path,
            """
            DEPLOY_REMOTE=alice@kb.example
            DEPLOY_PATH=/srv/claire
            DEPLOY_ENV_SYNC=always
            DEPLOY_ACTION=install
            """,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        logged = calls.read_text(encoding="utf-8")
        self.assertIn(
            "cd '/srv/claire' && CLAIRE_ENVIRONMENT=production "
            "bash ./cb-manuscript install",
            logged,
        )
        self.assertNotIn("bash ./cb-manuscript update --no-fetch", logged)

    def test_development_environment_is_rejected_before_remote_calls(self):
        result, calls, _ = _run_deploy(
            self.tmp_path,
            """
            DEPLOY_REMOTE=alice@kb.example
            DEPLOY_PATH=/srv/claire
            DEPLOY_ENV_SYNC=always
            """,
            extra_env={"CLAIRE_ENVIRONMENT": "development"},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("production 전용", result.stderr)
        self.assertFalse(calls.exists())

    def test_if_missing_preserves_existing_remote_dotenv(self):
        result, calls, _ = _run_deploy(
            self.tmp_path,
            """
            DEPLOY_REMOTE=claire-host
            DEPLOY_PORT=22
            DEPLOY_PATH=/opt/claire
            DEPLOY_ENV_SYNC=if-missing
            """,
            ssh_test_status=0,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        lines = calls.read_text(encoding="utf-8").splitlines()
        self.assertEqual(sum(line.startswith("rsync\t") for line in lines), 1)
        self.assertIn("원격 .env 유지", result.stdout)

    def test_process_environment_overrides_dotenv(self):
        result, calls, _ = _run_deploy(
            self.tmp_path,
            """
            DEPLOY_REMOTE=from-file
            DEPLOY_PORT=22
            DEPLOY_PATH=/from/file
            DEPLOY_ENV_SYNC=never
            """,
            extra_env={
                "DEPLOY_REMOTE": "bob@override.example",
                "DEPLOY_PATH": "/srv/override",
            },
            ssh_test_status=0,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        logged = calls.read_text(encoding="utf-8")
        self.assertIn("bob@override.example", logged)
        self.assertIn("/srv/override", logged)
        self.assertNotIn("from-file", logged)
        self.assertNotIn("/from/file", logged)

    def test_missing_local_dotenv_is_allowed_when_remote_one_is_preserved(self):
        result, calls, env_file = _run_deploy(
            self.tmp_path,
            None,
            ssh_test_status=0,
            extra_env={
                "DEPLOY_REMOTE": "alice@host",
                "DEPLOY_PATH": "/srv/claire",
                "DEPLOY_ENV_SYNC": "if-missing",
            },
        )

        self.assertFalse(env_file.exists())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("원격 .env 유지", result.stdout)
        self.assertEqual(
            sum(
                line.startswith("rsync\t")
                for line in calls.read_text(encoding="utf-8").splitlines()
            ),
            1,
        )

    def test_always_requires_local_dotenv_before_remote_calls(self):
        result, calls, _ = _run_deploy(
            self.tmp_path,
            None,
            extra_env={
                "DEPLOY_REMOTE": "alice@host",
                "DEPLOY_PATH": "/srv/claire",
                "DEPLOY_ENV_SYNC": "always",
            },
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("파일이 없습니다", result.stderr)
        self.assertFalse(calls.exists())

    def test_unrecognized_nonempty_remote_path_stops_before_rsync(self):
        result, calls, _ = _run_deploy(
            self.tmp_path,
            """
            DEPLOY_REMOTE=alice@host
            DEPLOY_PATH=/home/alice
            DEPLOY_ENV_SYNC=never
            """,
            ssh_guard_status=1,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("기존 Claire 배포 루트나 안전한 신규 경로가 아닙니다", result.stderr)
        self.assertNotIn("rsync\t", calls.read_text(encoding="utf-8"))

    def test_guard_rejects_marker_or_compose_substring_false_positive(self):
        remote = self.tmp_path / "remote"
        remote.mkdir()
        (remote / ".claire-deploy-root").write_text("not-claire\n", encoding="utf-8")
        (remote / "docker-compose.yml").write_text(
            "# container_name: claire_bot\n", encoding="utf-8"
        )
        (remote / "keep-me").write_text("unrelated", encoding="utf-8")

        result, calls, _ = _run_deploy(
            self.tmp_path / "client",
            """
            DEPLOY_REMOTE=alice@host
            DEPLOY_ENV_SYNC=never
            """ + f"DEPLOY_PATH={remote}\n",
            ssh_exec_guard=True,
            ssh_test_status=0,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("기존 Claire 배포 루트나 안전한 신규 경로가 아닙니다", result.stderr)
        self.assertNotIn("rsync\t", calls.read_text(encoding="utf-8"))
        self.assertEqual((remote / "keep-me").read_text(encoding="utf-8"), "unrelated")

    def test_guard_accepts_exact_deploy_marker(self):
        remote = self.tmp_path / "remote"
        remote.mkdir()
        (remote / ".claire-deploy-root").write_text("claire-bible\n", encoding="utf-8")
        (remote / "existing-code").write_text("old", encoding="utf-8")

        result, _, _ = _run_deploy(
            self.tmp_path / "client",
            """
            DEPLOY_REMOTE=alice@host
            DEPLOY_ENV_SYNC=never
            """ + f"DEPLOY_PATH={remote}\n",
            ssh_exec_guard=True,
            ssh_test_status=0,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_guard_adopts_legacy_claire_checkout_using_multiple_fingerprints(self):
        remote = self.tmp_path / "remote"
        (remote / "src" / "claire").mkdir(parents=True)
        (remote / "docker-compose.yml").write_text(
            "services:\n  claire:\n    container_name: claire_bot\n", encoding="utf-8"
        )
        (remote / "pyproject.toml").write_text('name = "claire"\n', encoding="utf-8")

        result, _, _ = _run_deploy(
            self.tmp_path / "client",
            """
            DEPLOY_REMOTE=alice@host
            DEPLOY_ENV_SYNC=never
            """ + f"DEPLOY_PATH={remote}\n",
            ssh_exec_guard=True,
            ssh_test_status=0,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_never_requires_remote_dotenv_before_rsync(self):
        result, calls, _ = _run_deploy(
            self.tmp_path,
            """
            DEPLOY_REMOTE=alice@host
            DEPLOY_PATH=/srv/claire
            DEPLOY_ENV_SYNC=never
            """,
            ssh_test_status=1,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("기존 원격 /srv/claire/.env가 필요합니다", result.stderr)
        self.assertNotIn("rsync\t", calls.read_text(encoding="utf-8"))

    def test_if_missing_requires_local_or_remote_dotenv_before_rsync(self):
        result, calls, _ = _run_deploy(
            self.tmp_path,
            None,
            extra_env={
                "DEPLOY_REMOTE": "alice@host",
                "DEPLOY_PATH": "/srv/claire",
                "DEPLOY_ENV_SYNC": "if-missing",
            },
            ssh_test_status=1,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("로컬", result.stderr)
        self.assertIn("원격 /srv/claire/.env가 모두 없습니다", result.stderr)
        self.assertNotIn("rsync\t", calls.read_text(encoding="utf-8"))

    def test_deploy_env_file_name_must_follow_dotenv_pattern(self):
        result, calls, _ = _run_deploy(
            self.tmp_path,
            None,
            extra_env={
                "DEPLOY_ENV_FILE": "config/production.env",
                "DEPLOY_REMOTE": "alice@host",
                "DEPLOY_PATH": "/srv/claire",
                "DEPLOY_ENV_SYNC": "never",
            },
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("파일명은 .env 또는 .env.* 형식이어야 합니다", result.stderr)
        self.assertFalse(calls.exists())

    def test_app_env_file_name_must_follow_dotenv_pattern(self):
        result, calls, _ = _run_deploy(
            self.tmp_path,
            None,
            extra_env={
                "DEPLOY_APP_ENV_FILE": "config/runtime.env",
                "DEPLOY_REMOTE": "alice@host",
                "DEPLOY_PATH": "/srv/claire",
                "DEPLOY_ENV_SYNC": "never",
            },
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DEPLOY_APP_ENV_FILE의 파일명", result.stderr)
        self.assertFalse(calls.exists())

    def test_invalid_config_stops_before_remote_calls(self):
        cases = [
            ("DEPLOY_REMOTE=\nDEPLOY_PATH=/srv/claire\n", "DEPLOY_REMOTE가 비어 있습니다"),
            (
                "DEPLOY_REMOTE=-F\nDEPLOY_PATH=/srv/claire\n",
                "DEPLOY_REMOTE 형식이 잘못되었습니다",
            ),
            (
                "DEPLOY_REMOTE=alice@host\nDEPLOY_PATH=/srv/../root\n",
                "DEPLOY_PATH는 '.', '..' 경로 세그먼트가 없는",
            ),
            (
                "DEPLOY_REMOTE=alice@host\nDEPLOY_PATH=/./\n",
                "DEPLOY_PATH는 '.', '..' 경로 세그먼트가 없는",
            ),
            (
                "DEPLOY_REMOTE=alice@host\nDEPLOY_PATH=///\n",
                "DEPLOY_PATH에는 중복 '/'를 사용할 수 없습니다",
            ),
            (
                "DEPLOY_REMOTE=alice@host\nDEPLOY_PORT=70000\nDEPLOY_PATH=/srv/claire\n",
                "DEPLOY_PORT는 65535 이하여야 합니다",
            ),
        ]
        for index, (dotenv, message) in enumerate(cases):
            with self.subTest(dotenv=dotenv):
                case_dir = self.tmp_path / str(index)
                case_dir.mkdir()
                result, calls, _ = _run_deploy(case_dir, dotenv)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertFalse(calls.exists())


if __name__ == "__main__":
    unittest.main()
