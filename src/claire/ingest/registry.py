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
            parsed = urlsplit(t_lower)
            host = parsed.netloc
            path = parsed.path
            if "youtube.com" in host or "youtu.be" in host:
                return "youtube"
            if ("vmware.com" in host and "/explore/video/" in path) or "brightcove.net" in host:
                return "video"
            if "vimeo.com" in host:
                return "video"
            if "tv.naver.com" in host or "now.naver.com" in host or ("naver.com" in host and "/v/" in path):
                return "video"
            if path.endswith((".mp4", ".m3u8", ".mpd", ".webm", ".m4a", ".mp3")):
                return "video"
            if "x.com" in host or "twitter.com" in host:
                return "xcom"
            if "share.google" in host or host.startswith("share."):
                return "redirect"
            if "wiki.hoyolab.com" in host or "hoyolab.com" in host:
                return "hoyowiki"
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

    def get_matching_web_adapters(self, url: str) -> list[Any]:
        self._ensure_builtins()
        matches = []
        import urllib.parse
        parsed = urllib.parse.urlsplit(url)
        hostname = parsed.hostname or ""
        for _, _, domains, cls in self.web_adapters:
            if not domains:
                matches.append(cls)
                continue
            for domain in domains:
                if domain.startswith("*.") and hostname.endswith(domain[2:]):
                    matches.append(cls)
                    break
                elif hostname == domain:
                    matches.append(cls)
                    break
        return matches

registry = FetcherRegistry()

# Optional decorators at module level for convenience
def register_fetcher(name: str, priority: int = 100) -> Callable:
    return registry.register_fetcher(name, priority)

def register_web_adapter(name: str, domains: tuple[str, ...] = (), priority: int = 100) -> Callable:
    return registry.register_web_adapter(name, domains, priority)


def load_builtin_fetchers() -> None:
    from .fetchers import law, discourse, video, youtube, xcom, hoyowiki
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
