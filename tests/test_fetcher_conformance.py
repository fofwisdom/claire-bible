"""Every registry entry follows the contract (INGEST_ADAPTER_PLUGIN_DESIGN.md §7.2), with the network blocked for can_handle."""

import socket

import pytest

from claire.ingest.fetchers.base import BaseFetcher, BaseWebAdapter
from claire.ingest.registry import registry


@pytest.fixture
def no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("can_handle must not use the network")

    monkeypatch.setattr(socket, "getaddrinfo", _blocked)
    monkeypatch.setattr(socket.socket, "connect", _blocked)


def test_entries_follow_the_contract(no_network):
    entries = registry.entries()
    assert entries, "the registry has no entry"
    names = [(e.kind, e.name) for e in entries]
    assert len(names) == len(set(names)), "two entries of one kind have the same name"
    for entry in entries:
        assert entry.hosts, entry.name
        if entry.kind == "fetcher":
            assert issubclass(entry.cls, BaseFetcher), entry.name
            entry.cls.can_handle("https://example.test/a")
        else:
            assert issubclass(entry.cls, BaseWebAdapter), entry.name
