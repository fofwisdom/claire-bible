"""Full-instance backup/restore contracts for ``cb-manuscript``.

The filesystem, hashing, archive, and rollback paths are real.  Only Docker
processes are replaced so these tests need neither a daemon nor a running
Claire stack.
"""

from __future__ import annotations

import json
import re
import subprocess
import tarfile
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path, PurePosixPath
from unittest.mock import patch

import pytest

from claire.ontology.base import Document
from claire.store import db as dbm
from ops import cb_manuscript as cb

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_REAL_SUBPROCESS_RUN = subprocess.run


def _completed(
    argv: list[str] | tuple[str, ...],
    returncode: int = 0,
    *,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


class DockerHarness:
    """Record Docker calls and emulate the small lifecycle surface under test."""

    def __init__(
        self,
        *,
        running: tuple[str, ...] = (),
        migrate_returncode: int = 0,
        liveness_returncode: int = 0,
    ) -> None:
        self.running = running
        self.migrate_returncode = migrate_returncode
        self.liveness_returncode = liveness_returncode
        self.commands: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        command = [str(part) for part in argv]
        if not command or command[0] != "docker":
            return _REAL_SUBPROCESS_RUN(command, **kwargs)

        self.commands.append(command)
        if command[:3] == ["docker", "ps", "-q"]:
            # No exact-name legacy containers are running in unit tests.
            return _completed(command, stdout="")
        if (
            command[:2] == ["docker", "compose"]
            and "ps" in command
            and "-q" in command
        ):
            stdout = "".join(f"{container}\n" for container in self.running)
            return _completed(command, stdout=stdout)
        if command[-2:] == ["claire", "migrate"]:
            return _completed(command, returncode=self.migrate_returncode)
        if command[-2:] == ["claire", "liveness"]:
            return _completed(command, returncode=self.liveness_returncode)
        return _completed(command)


@contextmanager
def _docker(harness: DockerHarness):
    with patch.object(cb.subprocess, "run", side_effect=harness):
        yield harness


def _write_layout(
    root: Path,
    *,
    dev: bool = True,
    prod_data: str = "./data",
    prod_vault: str = "./vault",
    dev_data: str = "./.dev/data",
    dev_vault: str = "./.dev/vault",
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    prod = "\n".join(
        (
            "CLAIRE_ENVIRONMENT=production",
            "CB_PROJECT_NAME=claire-bible",
            "CB_WAIT_TIMEOUT=45",
            "CB_API_BIND=127.0.0.1",
            "CB_API_PORT=8765",
            f"CB_DATA_DIR={prod_data}",
            f"CB_VAULT_DIR={prod_vault}",
            "CLAIRE_DB_PATH=data/claire.db",
            "CLAIRE_INJECT_TOKEN=test-only-" + ("s" * 32),
            "CLAIRE_PUBLIC_URL=https://claire.example.com/",
            "CLAIRE_CORS_ALLOWED_ORIGINS=",
            "TELEGRAM_BOT_TOKEN=",
            "",
        )
    )
    development = "\n".join(
        (
            "CLAIRE_ENVIRONMENT=development",
            "CB_PROJECT_NAME=claire-bible-dev",
            "CB_WAIT_TIMEOUT=15",
            "CB_API_BIND=127.0.0.1",
            "CB_API_PORT=8766",
            f"CB_DATA_DIR={dev_data}",
            f"CB_VAULT_DIR={dev_vault}",
            "CLAIRE_PUBLIC_URL=http://127.0.0.1:8766/",
            "CLAIRE_CORS_ALLOWED_ORIGINS=",
            "",
        )
    )
    (root / ".env.example").write_text(prod, encoding="utf-8")
    (root / ".env").write_text(prod, encoding="utf-8")
    schema_source = root / "src" / "claire" / "store" / "db.py"
    schema_source.parent.mkdir(parents=True, exist_ok=True)
    schema_source.write_text(
        f"SCHEMA_VERSION = {dbm.SCHEMA_VERSION}\n",
        encoding="utf-8",
    )
    (root / "docker-compose.yml").write_text(
        "services:\n  api:\n    image: test\n", encoding="utf-8"
    )
    if dev:
        (root / ".env.dev.example").write_text(development, encoding="utf-8")
        (root / ".env.dev").write_text(development, encoding="utf-8")
        (root / "docker-compose.dev.yml").write_text(
            "services:\n  api: {}\n", encoding="utf-8"
        )


def _resolved_storage(root: Path, value: str) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else root / candidate


def _seed_storage(root: Path, *, data: str = "./data", vault: str = "./vault") -> None:
    data_path = _resolved_storage(root, data)
    vault_path = _resolved_storage(root, vault)
    (data_path / "raw" / "files").mkdir(parents=True, exist_ok=True)
    (data_path / "raw" / "files" / "source.txt").write_text(
        "source-original", encoding="utf-8"
    )
    (data_path / "marker.txt").write_text("data-original", encoding="utf-8")
    vault_path.mkdir(parents=True, exist_ok=True)
    (vault_path / "note.md").write_text("vault-original", encoding="utf-8")

    conn = dbm.connect(data_path / "claire.db")
    dbm.init_db(conn)
    dbm.insert_document(
        conn,
        Document(
            id="backup-contract",
            source_type="text",
            raw_text="restorable",
            content_hash="backup-contract-hash",
        ),
    )
    conn.close()


def _visible_artifacts(root: Path) -> list[Path]:
    backup_root = root / "backups"
    if not backup_root.is_dir():
        return []
    return sorted(
        path
        for path in backup_root.iterdir()
        if path.name.startswith("cb-") and not path.name.startswith(".")
    )


def _artifact_id(path: Path) -> str:
    if path.name.endswith(".tar.gz"):
        return path.name[: -len(".tar.gz")]
    return path.name


def _read_manifest(artifact: Path) -> dict[str, object]:
    if artifact.is_dir():
        value = json.loads((artifact / "manifest.json").read_text(encoding="utf-8"))
    else:
        with tarfile.open(artifact, "r:*") as archive:
            manifest_member = next(
                (
                    member
                    for member in archive.getmembers()
                    if PurePosixPath(member.name).as_posix().removeprefix("./")
                    == "manifest.json"
                ),
                None,
            )
            assert manifest_member is not None
            stream = archive.extractfile(manifest_member)
            assert stream is not None
            value = json.loads(stream.read().decode("utf-8"))
    assert isinstance(value, dict)
    return value


def _manifest_components(manifest: dict[str, object]) -> set[str]:
    components = manifest["components"]
    assert isinstance(components, list)
    assert all(isinstance(component, str) for component in components)
    return set(components)


def _payload_file(artifact: Path, suffix: str) -> Path:
    assert artifact.is_dir()
    manifest = _read_manifest(artifact)
    entries = manifest["entries"]
    assert isinstance(entries, list)
    matching = [
        entry
        for entry in entries
        if isinstance(entry, dict)
        and entry.get("type") == "file"
        and isinstance(entry.get("path"), str)
        and str(entry["path"]).endswith(suffix)
    ]
    assert len(matching) == 1
    relative = Path(str(matching[0]["path"]))
    candidates = (artifact / "payload" / relative, artifact / relative)
    return next(candidate for candidate in candidates if candidate.is_file())


def _has_compose_action(commands: list[list[str]], action: str) -> bool:
    return any(
        command[:2] == ["docker", "compose"] and action in command
        for command in commands
    )


def _first_compose_action(commands: list[list[str]], action: str) -> int:
    return next(
        index
        for index, command in enumerate(commands)
        if command[:2] == ["docker", "compose"] and action in command
    )


def _make_archive_from_directory(
    source: Path,
    destination: Path,
    *,
    traversal: bool = False,
    symlink: bool = False,
) -> None:
    with tarfile.open(destination, "w:gz") as archive:
        for path in sorted(source.rglob("*")):
            archive.add(
                path,
                arcname=path.relative_to(source).as_posix(),
                recursive=False,
            )
        if traversal:
            member = tarfile.TarInfo("../outside-created-by-restore")
            payload = b"must-not-escape"
            member.size = len(payload)
            archive.addfile(member, BytesIO(payload))
        if symlink:
            member = tarfile.TarInfo("payload/data/escape-link")
            member.type = tarfile.SYMTYPE
            member.linkname = "../../outside-created-by-restore"
            archive.addfile(member)


@pytest.mark.parametrize(
    ("backup_format", "expected_kind"),
    (("directory", "directory"), ("archive", "file")),
)
def test_directory_and_archive_round_trip(
    tmp_path: Path, backup_format: str, expected_kind: str
) -> None:
    root = tmp_path / "project"
    _write_layout(root, dev=False)
    _seed_storage(root)

    backup_stack = DockerHarness(running=("api-id", "worker-id"))
    with _docker(backup_stack):
        assert (
            cb.main(["backup", "--format", backup_format], root=root)
            == 0
        )

    artifacts = _visible_artifacts(root)
    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert artifact.name.startswith("cb-")
    assert artifact.is_dir() if expected_kind == "directory" else artifact.is_file()
    assert artifact.name.endswith(".tar.gz") == (backup_format == "archive")
    manifest = _read_manifest(artifact)
    assert manifest["id"] == _artifact_id(artifact)
    assert manifest["profile"] == "production"
    assert manifest["project"] == "claire-bible"
    assert _manifest_components(manifest) == {"data", "vault"}
    assert "test-only-secret" not in json.dumps(manifest, ensure_ascii=False)

    artifact_bytes = artifact.read_bytes() if artifact.is_file() else None
    (root / "data" / "marker.txt").write_text("data-mutated", encoding="utf-8")
    (root / "data" / "stale.txt").write_text("remove-me", encoding="utf-8")
    (root / "vault" / "note.md").write_text("vault-mutated", encoding="utf-8")

    restore_stack = DockerHarness(running=("api-id", "worker-id"))
    with _docker(restore_stack):
        assert cb.main(["restore", str(artifact), "--yes"], root=root) == 0

    assert (root / "data" / "marker.txt").read_text(encoding="utf-8") == "data-original"
    assert not (root / "data" / "stale.txt").exists()
    assert (root / "vault" / "note.md").read_text(encoding="utf-8") == "vault-original"
    conn = dbm.connect(root / "data" / "claire.db")
    try:
        restored = dbm.get_document(conn, "backup-contract")
    finally:
        conn.close()
    assert restored is not None and restored.raw_text == "restorable"
    assert artifact.exists()
    if artifact_bytes is not None:
        assert artifact.read_bytes() == artifact_bytes


def test_backup_and_restore_component_selection(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _write_layout(root, dev=False)
    _seed_storage(root)

    with _docker(DockerHarness()):
        assert (
            cb.main(
                ["backup", "--format", "directory", "--component", "data"],
                root=root,
            )
            == 0
        )

    artifact = _visible_artifacts(root)[0]
    manifest = _read_manifest(artifact)
    assert _manifest_components(manifest) == {"data"}
    assert not (artifact / "payload" / "vault").exists()
    assert not any(
        str(entry.get("path", "")).startswith("vault/")
        for entry in manifest["entries"]
        if isinstance(entry, dict)
    )

    (root / "data" / "marker.txt").write_text("data-mutated", encoding="utf-8")
    (root / "vault" / "note.md").write_text("vault-must-stay", encoding="utf-8")
    with _docker(DockerHarness()):
        assert (
            cb.main(
                ["restore", str(artifact), "--component", "data", "--yes"],
                root=root,
            )
            == 0
        )

    assert (root / "data" / "marker.txt").read_text(encoding="utf-8") == "data-original"
    assert (root / "vault" / "note.md").read_text(encoding="utf-8") == "vault-must-stay"


def test_backup_rejects_config_component_in_v1(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _write_layout(root, dev=False)
    _seed_storage(root)

    harness = DockerHarness()
    with _docker(harness):
        assert cb.main(["backup", "--component", "config"], root=root) == 2

    assert not _visible_artifacts(root)
    assert not _has_compose_action(harness.commands, "stop")


def test_same_date_collision_is_rejected_and_replace_switches_format(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    _write_layout(root, dev=False)
    _seed_storage(root)

    with patch.object(cb, "_backup_id", return_value="cb-20260821-120000"):
        with _docker(DockerHarness()):
            assert cb.main(["backup", "--format", "directory"], root=root) == 0
        original = _visible_artifacts(root)[0]
        original_manifest = (original / "manifest.json").read_bytes()

        collision = DockerHarness(running=("api-id",))
        with _docker(collision):
            assert cb.main(["backup", "--format", "archive"], root=root) == 2
        assert original.is_dir()
        assert (original / "manifest.json").read_bytes() == original_manifest
        assert not _has_compose_action(collision.commands, "stop")

        with _docker(DockerHarness()):
            assert (
                cb.main(["backup", "--format", "archive", "--replace"], root=root)
                == 0
            )
        artifacts = _visible_artifacts(root)
        assert len(artifacts) == 1
        assert artifacts[0].is_file()
        assert artifacts[0].name == f"{_artifact_id(original)}.tar.gz"


def test_hash_tamper_is_rejected_before_service_stop(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _write_layout(root, dev=False)
    _seed_storage(root)

    with _docker(DockerHarness()):
        assert cb.main(["backup", "--format", "directory"], root=root) == 0
    artifact = _visible_artifacts(root)[0]
    _payload_file(artifact, "marker.txt").write_text("tampered", encoding="utf-8")
    (root / "data" / "marker.txt").write_text("live-current", encoding="utf-8")

    restore = DockerHarness(running=("api-id",))
    with _docker(restore):
        assert cb.main(["restore", str(artifact), "--yes"], root=root) != 0

    assert not _has_compose_action(restore.commands, "stop")
    assert (root / "data" / "marker.txt").read_text(encoding="utf-8") == "live-current"


@pytest.mark.parametrize("malicious_kind", ("traversal", "symlink"))
def test_archive_traversal_and_symlink_are_rejected_before_stop(
    tmp_path: Path, malicious_kind: str
) -> None:
    root = tmp_path / "project"
    _write_layout(root, dev=False)
    _seed_storage(root)
    with _docker(DockerHarness()):
        assert cb.main(["backup", "--format", "directory"], root=root) == 0
    directory = _visible_artifacts(root)[0]

    malicious = tmp_path / f"malicious-{malicious_kind}.tar.gz"
    _make_archive_from_directory(
        directory,
        malicious,
        traversal=malicious_kind == "traversal",
        symlink=malicious_kind == "symlink",
    )
    outside = tmp_path / "outside-created-by-restore"
    restore = DockerHarness(running=("api-id",))
    with _docker(restore):
        assert cb.main(["restore", str(malicious), "--yes"], root=root) != 0

    assert not outside.exists()
    assert not _has_compose_action(restore.commands, "stop")


def test_directory_symlink_is_rejected_before_stop(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _write_layout(root, dev=False)
    _seed_storage(root)
    with _docker(DockerHarness()):
        assert cb.main(["backup", "--format", "directory"], root=root) == 0
    artifact = _visible_artifacts(root)[0]
    outside = tmp_path / "outside"
    outside.write_text("outside", encoding="utf-8")
    (artifact / "payload" / "data" / "escape-link").symlink_to(outside)

    restore = DockerHarness(running=("api-id",))
    with _docker(restore):
        assert cb.main(["restore", str(artifact), "--yes"], root=root) != 0

    assert outside.read_text(encoding="utf-8") == "outside"
    assert not _has_compose_action(restore.commands, "stop")


def test_backup_stops_before_capture_and_restarts_exact_running_containers(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    _write_layout(root, dev=False)
    _seed_storage(root)
    harness = DockerHarness(running=("api-id", "refresh-id"))

    with _docker(harness):
        assert cb.main(["backup"], root=root) == 0

    ps_index = _first_compose_action(harness.commands, "ps")
    stop_index = _first_compose_action(harness.commands, "stop")
    restart_index = harness.commands.index(
        ["docker", "start", "api-id", "refresh-id"]
    )
    assert ps_index < stop_index < restart_index
    stop_command = harness.commands[stop_index]
    assert stop_command[stop_command.index("--profile") + 1] == "*"
    assert _visible_artifacts(root)


def test_restore_migration_failure_rolls_back_live_tree_and_restarts(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    _write_layout(root, dev=False)
    _seed_storage(root)
    with _docker(DockerHarness()):
        assert cb.main(["backup", "--format", "directory"], root=root) == 0
    artifact = _visible_artifacts(root)[0]

    (root / "data" / "marker.txt").write_text(
        "pre-restore-current", encoding="utf-8"
    )
    (root / "data" / "current-only.txt").write_text(
        "must-survive-rollback", encoding="utf-8"
    )
    (root / "vault" / "note.md").write_text(
        "pre-restore-vault", encoding="utf-8"
    )

    restore = DockerHarness(
        running=("api-id", "refresh-id"),
        migrate_returncode=17,
    )
    with _docker(restore):
        assert cb.main(["restore", str(artifact), "--yes"], root=root) == 17

    assert (
        root / "data" / "marker.txt"
    ).read_text(encoding="utf-8") == "pre-restore-current"
    assert (
        root / "data" / "current-only.txt"
    ).read_text(encoding="utf-8") == "must-survive-rollback"
    assert (
        root / "vault" / "note.md"
    ).read_text(encoding="utf-8") == "pre-restore-vault"
    assert ["docker", "start", "api-id", "refresh-id"] in restore.commands


def test_restore_liveness_failure_rolls_back_after_stopping_restored_stack(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    _write_layout(root, dev=False)
    _seed_storage(root)
    with _docker(DockerHarness()):
        assert cb.main(["backup"], root=root) == 0
    artifact = _visible_artifacts(root)[0]

    (root / "data" / "marker.txt").write_text(
        "pre-restore-current", encoding="utf-8"
    )
    restore = DockerHarness(
        running=("api-id", "refresh-id"),
        liveness_returncode=23,
    )
    with _docker(restore), patch.object(cb.time, "sleep"):
        assert cb.main(["restore", str(artifact), "--yes"], root=root) == 2

    assert (
        root / "data" / "marker.txt"
    ).read_text(encoding="utf-8") == "pre-restore-current"
    assert ["docker", "stop", "api-id", "refresh-id"] in restore.commands
    assert restore.commands.count(
        ["docker", "start", "api-id", "refresh-id"]
    ) == 2


def test_prod_and_dev_profile_mismatch_is_rejected_before_stop(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    _write_layout(root)
    _seed_storage(root, data="./.dev/data", vault="./.dev/vault")

    with _docker(DockerHarness()):
        assert cb.main(["dev", "backup"], root=root) == 0
    artifact = _visible_artifacts(root)[0]
    assert _read_manifest(artifact)["profile"] == "development"

    restore = DockerHarness(running=("prod-api",))
    with _docker(restore):
        assert cb.main(["restore", str(artifact), "--yes"], root=root) == 2
    assert not _has_compose_action(restore.commands, "stop")


def test_relative_storage_path_overrides_are_round_tripped(tmp_path: Path) -> None:
    root = tmp_path / "project"
    data = "./state/prod-data"
    vault = "./state/prod-vault"
    _write_layout(root, dev=False, prod_data=data, prod_vault=vault)
    _seed_storage(root, data=data, vault=vault)

    with _docker(DockerHarness()):
        assert cb.main(["backup"], root=root) == 0
    artifact = _visible_artifacts(root)[0]
    data_path = _resolved_storage(root, data)
    vault_path = _resolved_storage(root, vault)
    (data_path / "marker.txt").write_text("mutated", encoding="utf-8")
    (vault_path / "note.md").write_text("mutated", encoding="utf-8")

    with _docker(DockerHarness()):
        assert cb.main(["restore", str(artifact), "--yes"], root=root) == 0
    assert (data_path / "marker.txt").read_text(encoding="utf-8") == "data-original"
    assert (vault_path / "note.md").read_text(encoding="utf-8") == "vault-original"
    assert not (root / "data").exists()
    assert not (root / "vault").exists()


def test_backup_paths_are_excluded_from_source_build_and_remote_delete() -> None:
    gitignore = (REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "/backups/" in {
        line.strip() for line in gitignore.splitlines() if line.strip()
    }

    dockerignore = (REPOSITORY_ROOT / ".dockerignore").read_text(encoding="utf-8")
    docker_entries = {
        line.strip().strip("/").lstrip("/")
        for line in dockerignore.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert "backups" in docker_entries

    deploy = (REPOSITORY_ROOT / "deploy.sh").read_text(encoding="utf-8")
    assert re.search(
        r"--exclude[ =]+[\"']/?backups/?[\"']",
        deploy,
    )


def test_backup_timestamp_id_format(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _write_layout(root, dev=False)
    _seed_storage(root)

    with _docker(DockerHarness()):
        assert cb.main(["backup"], root=root) == 0

    artifacts = _visible_artifacts(root)
    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert re.fullmatch(r"cb-[0-9]{8}-[0-9]{6}", artifact.name)
    manifest = _read_manifest(artifact)
    assert manifest["id"] == artifact.name


def test_consecutive_backups_create_distinct_snapshots(tmp_path: Path) -> None:
    from datetime import datetime, timezone

    root = tmp_path / "project"
    _write_layout(root, dev=False)
    _seed_storage(root)

    dt1 = datetime(2026, 8, 20, 10, 0, 0, tzinfo=timezone.utc)
    dt2 = datetime(2026, 8, 20, 10, 5, 0, tzinfo=timezone.utc)

    with _docker(DockerHarness()), patch.object(cb, "_local_now", return_value=dt1):
        assert cb.main(["backup"], root=root) == 0

    (root / "data" / "marker.txt").write_text("data-updated-v2", encoding="utf-8")

    with _docker(DockerHarness()), patch.object(cb, "_local_now", return_value=dt2):
        assert cb.main(["backup", "--format", "archive"], root=root) == 0

    artifacts = _visible_artifacts(root)
    assert len(artifacts) == 2
    assert artifacts[0].name == "cb-20260820-100000"
    assert artifacts[0].is_dir()
    assert artifacts[1].name == "cb-20260820-100500.tar.gz"
    assert artifacts[1].is_file()


def test_restore_interactive_selection(tmp_path: Path) -> None:
    from datetime import datetime, timezone

    root = tmp_path / "project"
    _write_layout(root, dev=False)
    _seed_storage(root)

    dt1 = datetime(2026, 8, 20, 10, 0, 0, tzinfo=timezone.utc)
    dt2 = datetime(2026, 8, 20, 10, 5, 0, tzinfo=timezone.utc)

    with _docker(DockerHarness()), patch.object(cb, "_local_now", return_value=dt1):
        assert cb.main(["backup"], root=root) == 0

    (root / "data" / "marker.txt").write_text("v2-data", encoding="utf-8")
    with _docker(DockerHarness()), patch.object(cb, "_local_now", return_value=dt2):
        assert cb.main(["backup"], root=root) == 0

    (root / "data" / "marker.txt").write_text("v3-mutated", encoding="utf-8")

    # Available backups are ordered newest first: [1] 10:05:00, [2] 10:00:00
    # Select [2] (the original one where marker is 'data-original') and confirm with 'y'
    inputs = iter(["2", "y"])
    with _docker(DockerHarness()), patch("builtins.input", side_effect=inputs):
        assert cb.main(["restore"], root=root) == 0

    assert (root / "data" / "marker.txt").read_text(encoding="utf-8") == "data-original"


def test_restore_interactive_with_yes_flag(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _write_layout(root, dev=False)
    _seed_storage(root)

    with _docker(DockerHarness()):
        assert cb.main(["backup"], root=root) == 0

    (root / "data" / "marker.txt").write_text("mutated", encoding="utf-8")

    # With --yes flag, only selection number is needed (confirmation prompt is skipped)
    inputs = iter(["1"])
    with _docker(DockerHarness()), patch("builtins.input", side_effect=inputs):
        assert cb.main(["restore", "--yes"], root=root) == 0

    assert (root / "data" / "marker.txt").read_text(encoding="utf-8") == "data-original"


def test_restore_interactive_cancel_inputs(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _write_layout(root, dev=False)
    _seed_storage(root)

    with _docker(DockerHarness()):
        assert cb.main(["backup"], root=root) == 0

    # User cancels at selection
    with _docker(DockerHarness()), patch("builtins.input", side_effect=iter(["q"])):
        assert cb.main(["restore"], root=root) == 2

    # User cancels at confirmation
    with _docker(DockerHarness()), patch("builtins.input", side_effect=iter(["1", "n"])):
        assert cb.main(["restore"], root=root) == 2


def test_restore_interactive_no_backups_error(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _write_layout(root, dev=False)

    with _docker(DockerHarness()):
        assert cb.main(["restore"], root=root) == 2


def test_restore_non_interactive_without_source_fails(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _write_layout(root, dev=False)
    _seed_storage(root)

    with _docker(DockerHarness()):
        assert cb.main(["backup"], root=root) == 0

    runtime = cb.load_runtime(cb.Layout(root))
    # When input raises EOFError (non-interactive / closed stdin)
    with patch("builtins.input", side_effect=EOFError):
        with pytest.raises(cb.ManuscriptError, match="Backup source path is required"):
            cb.command_restore(
                runtime,
                raw_source=None,
                raw_components=None,
                confirmed=True,
            )


