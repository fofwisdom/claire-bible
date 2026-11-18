"""Tests of __ID__. They never use the network."""

from pathlib import Path

import httpx

from claire.plugin_sdk.testing import load_plugin

FOLDER = Path(__file__).resolve().parents[1]


def test_registers_one_web_adapter():
    plugin = load_plugin(FOLDER)
    assert [e.name for e in plugin.context.entries] == ["__NAME__"]


def test_try_fetch_reads_the_page(monkeypatch):
    plugin = load_plugin(FOLDER)
    html = "<html><head><title>T</title></head><body><p>" + "Text. " * 80 + "</p></body></html>"
    plugin.mock_network(monkeypatch, lambda request: httpx.Response(200, text=html, headers={"content-type": "text/html"}))
    result = plugin.entry("__NAME__").cls.try_fetch("https://example.com/a")
    assert result is not None and result.title == "T"
