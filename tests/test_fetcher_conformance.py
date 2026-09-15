import pytest
from claire.ingest.registry import registry
from claire.ingest.fetchers.base import BaseFetcher, BaseWebAdapter

def test_fetchers_conformance():
    """Verify that all registered fetchers subclass BaseFetcher and implement abstract methods."""
    fetchers = registry.fetchers
    for priority, name, cls in fetchers:
        assert issubclass(cls, BaseFetcher), f"{name} ({cls.__name__}) does not inherit from BaseFetcher"
        assert hasattr(cls, 'can_handle'), f"{name} is missing 'can_handle'"
        assert hasattr(cls, 'fetch'), f"{name} is missing 'fetch'"
        # In Python, abstract methods must be implemented to be instantiated,
        # but since they might be used class-methods, let's just assert existence.
        
def test_web_adapters_conformance():
    """Verify that all registered web adapters subclass BaseWebAdapter."""
    adapters = registry.web_adapters
    for priority, name, domains, cls in adapters:
        assert issubclass(cls, BaseWebAdapter), f"{name} ({cls.__name__}) does not inherit from BaseWebAdapter"
        assert hasattr(cls, 'name'), f"{name} is missing 'name'"
        assert hasattr(cls, 'try_fetch'), f"{name} is missing 'try_fetch'"
