"""Test setup of the project plugins. The template folder is not a plugin; its test runs only after `claire plugin new`."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.conftest import _clear_settings_cache_after_test, _isolate_limit_manager_files, _isolate_plugin_records, korean_interface, mock_guarded_client  # noqa: F401

collect_ignore_glob = ["ingest_adapter/_template/*"]


@pytest.fixture(autouse=True)
def _isolate_data_folder(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A plugin test never writes into the data folder of the repository (video cache, raw artifacts): the global settings point to a temporary data folder. A test that sets CLAIRE_DB_PATH itself keeps its value."""
    import os

    from claire.config import get_settings

    if not os.environ.get("CLAIRE_DB_PATH"):
        from claire.store import db as dbm

        db_file = tmp_path / "data" / "claire.db"
        db_file.parent.mkdir(parents=True)
        conn = dbm.connect(db_file)
        dbm.init_db(conn)
        conn.close()
        # A new server: the first refresh of the plugin runtime enables the project plugins.
        monkeypatch.setenv("CLAIRE_DB_PATH", str(db_file))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
