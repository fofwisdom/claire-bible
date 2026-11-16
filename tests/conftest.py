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
