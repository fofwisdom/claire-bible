import importlib.util
from pathlib import Path
from typing import Any, Callable, Dict
import importlib.metadata

class FetcherRegistry:
    def __init__(self):
        self.fetchers: list[Any] = []
        self.web_adapters: list[Any] = []
        self._builtins_loaded: bool = False

    def _ensure_builtins(self) -> None:
        if not self._builtins_loaded:
            self._builtins_loaded = True
            load_builtin_fetchers()

    def register_fetcher(self, name: str, priority: int = 100) -> Callable:
        def decorator(cls: Any) -> Any:
            self.fetchers.append((priority, name, cls))
            self.fetchers.sort(key=lambda x: x[0], reverse=True)
            return cls
        return decorator

    def register_web_adapter(self, name: str, domains: tuple[str, ...] = (), priority: int = 100) -> Callable:
        def decorator(cls: Any) -> Any:
            self.web_adapters.append((priority, name, domains, cls))
            self.web_adapters.sort(key=lambda x: x[0], reverse=True)
            return cls
        return decorator

    def classify(self, payload: str) -> str:
        self._ensure_builtins()
        t = (payload or "").strip()
        if not t:
            return "text"
            
        for _, _, cls in self.fetchers:
            if hasattr(cls, "can_handle") and cls.can_handle(payload):
                return cls.name() if hasattr(cls, "name") else "custom"
                
        # SSOT fallback logic
        import os
        from urllib.parse import urlsplit
        import re
        
        # Helper to extract shared url
        t_lower = t.lower()
        if not t_lower.startswith(("http://", "https://")):
            tokens = t.split()
            if tokens:
                last = tokens[-1].rstrip(".,;)。")
                m = re.fullmatch(r"https?://[^\s)\]\}<>\"']+", last)
                if m:
                    t = m.group(0)
                    t_lower = t.lower()
                    
        if t_lower.startswith("http://") or t_lower.startswith("https://"):
            from .fetchers.xcom import XCOM_HOSTS
            from .fetchers.youtube import YOUTUBE_HOSTS
            from .hosts import host_matches_any, url_host

            host = url_host(t) or ""
            path = urlsplit(t_lower).path
            if host_matches_any(host, YOUTUBE_HOSTS):
                return "youtube"
            from .fetchers.video import is_video_or_audio_url
            if is_video_or_audio_url(t):
                return "video"
            if host_matches_any(host, XCOM_HOSTS):
                return "xcom"
            if host == "share.google" or host.startswith("share."):
                return "redirect"
            if host == "wiki.hoyolab.com" or (host_matches_any(host, ("hoyolab.com", "*.hoyolab.com")) and ("/entry/" in path or "/character/" in path or "/entry_preview/" in path)):
                return "hoyowiki"
            if host_matches_any(host, ("hoyolab.com", "*.hoyolab.com")) and "/article/" in path:
                return "hoyolab"
            return "web"
            
        if (os.path.sep in t or t.lower().endswith((".pdf", ".odt", ".md", ".txt", ".markdown", ".rst"))) and os.path.exists(t):
            return "file"
        if t.startswith("file://"):
            return "file"
        return "text"

    def get_fetcher(self, payload: str) -> Any | None:
        self._ensure_builtins()
        for _, _, cls in self.fetchers:
            if hasattr(cls, "can_handle") and cls.can_handle(payload):
                return cls
        return None

    def get_matching_web_adapters(self, url: str, domain_only: bool = False) -> list[Any]:
        self._ensure_builtins()
        matches = []
        import urllib.parse
        parsed = urllib.parse.urlsplit(url)
        hostname = (parsed.hostname or "").lower()
        from .hosts import host_matches_any

        for _, _, domains, cls in self.web_adapters:
            if not domains:
                if not domain_only:
                    matches.append(cls)
                continue
            if host_matches_any(hostname.rstrip("."), (d.lower() for d in domains)):
                matches.append(cls)
        return matches

registry = FetcherRegistry()

# Optional decorators at module level for convenience
def register_fetcher(name: str, priority: int = 100) -> Callable:
    return registry.register_fetcher(name, priority)

def register_web_adapter(name: str, domains: tuple[str, ...] = (), priority: int = 100) -> Callable:
    return registry.register_web_adapter(name, domains, priority)


def load_builtin_fetchers() -> None:
    from .fetchers import law, discourse, video, youtube, xcom, hoyowiki, hoyolab, naver_clip, uttu
    # add other builtins if needed

def load_plugins_from_dir(plugin_dir: Path) -> None:
    if not plugin_dir.exists() or not plugin_dir.is_dir():
        return
        
    for py_file in plugin_dir.glob("*.py"):
        if py_file.name == "__init__.py":
            continue
            
        module_name = f"claire.plugins.{py_file.stem}"
        spec = importlib.util.spec_from_file_location(module_name, py_file)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

def load_entrypoints() -> None:
    try:
        # Python 3.10+
        eps = importlib.metadata.entry_points(group='claire.fetchers')
        for ep in eps:
            ep.load()
    except Exception:
        # Fallback or older python handled here if necessary
        try:
            # Fallback for Python 3.9
            eps = importlib.metadata.entry_points().get('claire.fetchers', [])
            for ep in eps:
                ep.load()
        except Exception:
            pass
