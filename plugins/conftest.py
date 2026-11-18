"""Test setup of the project plugins. The template folder is not a plugin; its test runs only after `claire plugin new`."""

from tests.conftest import _isolate_limit_manager_files, korean_interface, mock_guarded_client  # noqa: F401

collect_ignore_glob = ["ingest_adapter/_template/*"]
