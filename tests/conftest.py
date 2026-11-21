"""Shared fixtures of the test suite."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

import claire_bible.bootstrap
from claire.i18n import use_locale

claire_bible.bootstrap.configure()


@pytest.fixture(autouse=True)
def _configure_claire_bible_ports() -> None:
    """Configure renderer ports before each test."""
    claire_bible.bootstrap.configure(force=True)


@pytest.fixture
def korean_interface(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """State the interface locale that a test expects: Korean (I18N design Sections 3.2 and 7.1).

    ``CLAIRE_LANG=ko`` is the server default step of each resolution chain, thus a ``Settings`` object that the test makes resolves to Korean when no account locale and no Telegram ``language_code`` apply. The active locale of the context is also Korean, for code that the test calls outside a request or an update. A test module selects this fixture with ``pytestmark = pytest.mark.usefixtures("korean_interface")``.
    """
    monkeypatch.setenv("CLAIRE_LANG", "ko")
    with use_locale("ko"):
        yield


@pytest.fixture(autouse=True)
def _isolate_limit_manager_files(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A test must not read or change the files of the repository through the limit manager.

    - The env files (``.env``, ``.env.dev``) that the migration reads and comments out: an empty temporary folder.
    - The limits file of the repository data folder (``data/ingest_limits.json``): a new temporary data folder for each test. At its first use in the test, the manager makes the file from the variables of the test, as a real first start does.
    """
    from pathlib import Path

    import claire.core.ingest_limit_manager as ilm

    monkeypatch.setattr(ilm, "ENV_ROOT", tmp_path_factory.mktemp("env-root"))
    repo_data = (ilm.ROOT / "data").resolve()
    isolated_data = tmp_path_factory.mktemp("limits-data")
    real_get = ilm.get_ingest_limit_manager

    def isolated_get(data_dir=None):
        if data_dir is None:
            import os

            cand = os.environ.get("CB_DATA_DIR") or os.environ.get("CLAIRE_DB_PATH")
            if not cand:
                return real_get(isolated_data)
            data_dir = Path(cand).parent if cand.endswith(".db") else Path(cand)
        p = Path(data_dir)
        if (p if p.is_absolute() else ilm.ROOT / p).resolve() == repo_data:
            return real_get(isolated_data)
        return real_get(data_dir)

    monkeypatch.setattr(ilm, "get_ingest_limit_manager", isolated_get)
    ilm._INGEST_LIMIT_MANAGERS.clear()
    yield
    ilm._INGEST_LIMIT_MANAGERS.clear()


@pytest.fixture
def mock_guarded_client(monkeypatch: pytest.MonkeyPatch):
    """Replace ``guarded_client`` in one module with a client that sends each request to ``handler`` (``httpx.MockTransport``).

    Use: ``mock_guarded_client("claire.ingest.fetchers.hoyolab", handler)``. The handler gets an ``httpx.Request`` and returns an ``httpx.Response``. The private network check does not run, because the requests do not leave the process.
    """
    import httpx

    def _install(module_path: str, handler) -> None:
        def _factory(**kwargs):
            for key in ("allow_private_networks", "private_network_allowlist", "transport"):
                kwargs.pop(key, None)
            return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr(f"{module_path}.guarded_client", _factory)

    return _install


@pytest.fixture(autouse=True)
def _isolate_plugin_records(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A test must not start the plugin store in the database of the repository data folder (docs/implementation/INGESTION.md §2.2).

    When the settings of the test point to ``data/claire.db`` of the repository, the plugin runtime reads and writes the records in a new temporary database. The project plugins of the repository then start enabled, as on a new server.
    """
    from pathlib import Path

    from claire.core.config import ROOT
    from claire.plugin_host import runtime as rt

    repo_db = (Path(ROOT) / "data" / "claire.db").resolve()
    real = rt.server_db_file
    isolated: dict[str, Path] = {}

    def isolated_db(settings=None):
        path = real(settings)
        if Path(path).resolve() != repo_db:
            return path
        if "db" not in isolated:
            from claire.store import db as dbm

            target = tmp_path_factory.mktemp("plugin-records") / "claire.db"
            conn = dbm.connect(target)
            dbm.init_db(conn)
            conn.close()
            isolated["db"] = target
        return isolated["db"]

    monkeypatch.setattr(rt, "server_db_file", isolated_db)
    rt.runtime().invalidate()
    yield
    rt.runtime().invalidate()


@pytest.fixture(autouse=True)
def _clear_settings_cache_after_test() -> Iterator[None]:
    """A test that changes environment variables and fills the ``get_settings`` cache must not leak those settings into the next test of the same worker (monkeypatch restores the variables, but not the cache)."""
    yield
    from claire.core.config import get_settings

    get_settings.cache_clear()
