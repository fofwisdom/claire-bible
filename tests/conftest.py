"""Shared fixtures of the test suite."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from claire.i18n import use_locale


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

    import claire.ingest_limit_manager as ilm

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
