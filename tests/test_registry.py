import pytest
from pathlib import Path
from claire.ingest.registry import (
    FetcherRegistry, 
    register_fetcher, 
    register_web_adapter,
    registry,
    load_plugins_from_dir,
    load_entrypoints,
)

def test_registry_initialization():
    r = FetcherRegistry()
    assert r.fetchers == []
    assert r.web_adapters == []

def test_register_fetcher():
    r = FetcherRegistry()
    
    @r.register_fetcher("test_fetcher", priority=50)
    class TestFetcher:
        pass
        
    assert any(name == "test_fetcher" for _, name, _ in r.fetchers)
    assert any(cls == TestFetcher for _, _, cls in r.fetchers)

def test_register_web_adapter():
    r = FetcherRegistry()
    
    @r.register_web_adapter("test_adapter", domains=("example.com", "*.example.com"), priority=50)
    class TestAdapter:
        pass
        
    assert any(name == "test_adapter" for _, name, _, _ in r.web_adapters)
    assert any(cls == TestAdapter for _, _, _, cls in r.web_adapters)
    
    # Test domain matching
    matches = r.get_matching_web_adapters("https://example.com/page")
    assert TestAdapter in matches
    sub_matches = r.get_matching_web_adapters("https://sub.example.com/page")
    assert TestAdapter in sub_matches
    non_matches = r.get_matching_web_adapters("https://other.com/page")
    assert TestAdapter not in non_matches

def test_module_level_decorators():
    old_fetchers = list(registry.fetchers)
    old_adapters = list(registry.web_adapters)
    
    try:
        @register_fetcher("global_fetcher", priority=80)
        class GlobalFetcher:
            pass
            
        @register_web_adapter("global_adapter", priority=80)
        class GlobalAdapter:
            pass
            
        assert any(name == "global_fetcher" for _, name, _ in registry.fetchers)
        assert any(name == "global_adapter" for _, name, _, _ in registry.web_adapters)
    finally:
        registry.fetchers = old_fetchers
        registry.web_adapters = old_adapters

def test_load_plugins_from_dir(tmp_path: Path):
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    plugin_file = plugin_dir / "my_plugin.py"
    plugin_file.write_text("""
from claire.ingest.registry import register_fetcher

@register_fetcher("my_plugin_fetcher", priority=200)
class MyPluginFetcher:
    pass
""")
    
    old_fetchers = list(registry.fetchers)
    
    try:
        load_plugins_from_dir(plugin_dir)
        assert any(name == "my_plugin_fetcher" for _, name, _ in registry.fetchers)
    finally:
        registry.fetchers = old_fetchers

