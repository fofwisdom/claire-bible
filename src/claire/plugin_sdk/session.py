"""``ingest_session(args)``: ingest from a plugin command (docs/implementation/INGESTION.md §2.2)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


class IngestSession:
    """An open knowledge base for a plugin command. ``ingest(url)`` runs the normal ingest pipeline."""

    def __init__(self, settings: Any, conn: Any, provider: Any, vstore: Any) -> None:
        self.settings = settings
        self._conn = conn
        self._provider = provider
        self._vstore = vstore

    def ingest(self, url: str, *, source: str, full_content: bool = True) -> Any:
        """Ingest ``url`` with the default extraction mode of the account (the server API when the account has one, else the queue of the agent client). The result has ``error`` (``None`` on success) and ``telegram_summary()``."""
        from ..accounts.provider_policy import context_of
        from ..ingest.extraction_mode import available_extraction_modes
        from ..ingest.pipeline import ingest

        return ingest(
            url,
            conn=self._conn,
            provider=self._provider,
            vstore=self._vstore,
            data_dir=self.settings.data_dir,
            source=source,
            full_content=full_content,
            extraction_mode=available_extraction_modes(self.settings, ctx=context_of(self.settings))[0],
        )


@contextmanager
def ingest_session(args: Any) -> Iterator[IngestSession]:
    """Open the knowledge base that the command arguments select (``--theme``), as the core commands do."""
    from ..cli import get_effective_settings
    from ..extract.provider import get_provider
    from ..store import db as dbm
    from ..store.vectors import make_vector_store

    settings, _theme = get_effective_settings(args)
    conn = dbm.connect(settings.db_file)
    try:
        dbm.init_db(conn)
        yield IngestSession(settings, conn, get_provider(settings), make_vector_store(conn, "brute"))
    finally:
        conn.close()
