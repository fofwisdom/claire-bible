"""프로바이더 및 하이퍼스케일러 설정 관리자.

환경변수(.env)에 의존하던 프로바이더 입력 정보(API 키, 모델, 사고수준, 타임아웃 등)를
WebUI에서 관리하는 영구 파일(data/providers.json)로 전환하고,
기존 환경변수 설정을 자동 마이그레이션한 뒤 env 파일의 해당 항목을 안전하게 주석처리한다.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from .accounts.provider_policy import FEATURE_SLOTS, MODEL_CAPABILITY_RANKS, enumerate_gemini_model_variants, get_model_rank, infer_model_effort_suffix, is_model_equal_or_higher, split_gemini_model_and_effort
from .config import (
    ROOT,
    diagnose_agy_environment,
    find_agy_executable,
    find_codex_executable,
)

log = logging.getLogger("claire.provider")

PROVIDERS_CONFIG_FILENAME = "providers.json"

DEFAULT_GEMINI_BASE_MODELS: tuple[str, ...] = (
    "gemini-3.5-flash-lite",
    "gemini-3.8-flash",
    "gemini-4-argon",
    "gemini-3.6-flash",
    "gemini-3.1-flash-lite",
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-pro",
)

DEFAULT_GEMINI_MODELS: tuple[str, ...] = tuple(enumerate_gemini_model_variants(DEFAULT_GEMINI_BASE_MODELS))

DEFAULT_GEMINI_EMBED_MODELS: tuple[str, ...] = (
    "gemini-embedding-001",
    "gemini-embedding-2",
    "gemini-embedding-2-preview",
)

DEFAULT_CODEX_MODELS: tuple[str, ...] = (
    "",
    "gpt-5.4",
    "gpt-5.3-codex",
    "gpt-5.2-codex",
    "gpt-5.1-codex",
    "gpt-5-codex",
    "gpt-5",
    "o3",
    "o4-mini",
)

DEFAULT_OPENAI_MODELS: tuple[str, ...] = (
    "gpt-4o-mini",
    "gpt-4o",
    "gpt-4.1-mini",
    "gpt-4.1",
    "gpt-5-mini",
    "gpt-5",
    "o4-mini",
    "o3-mini",
)

DEFAULT_OPENAI_EMBED_MODELS: tuple[str, ...] = (
    "text-embedding-3-small",
    "text-embedding-3-large",
    "text-embedding-ada-002",
)

_AGY_MODELS_CACHE_TTL_SEC = 300.0
_AGY_UNAVAILABLE_CACHE_TTL_SEC = 30.0
_AGY_MODELS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_AGY_VERSION_CACHE: dict[str, tuple[float, str | None]] = {}
_GEMINI_DISCOVERED_MODELS: list[str] = []


def is_running_in_container() -> bool:
    """Return True when the current process runs inside a container runtime."""
    return Path("/.dockerenv").is_file() or os.environ.get("CLAIRE_APP_ROOT") == "/app"


def infer_agy_effort_from_model(model_name: str) -> str | None:
    """모델명 접미사(-xhigh, -high, -medium, -low)에서 추론 수준을 추출한다."""
    return infer_model_effort_suffix(model_name)


def _agy_cli_env() -> dict[str, str]:
    """Build the minimal environment for agy CLI discovery commands."""
    from .extract.antigravity_provider import _subprocess_env

    return _subprocess_env()


def get_agy_cli_version(exe: str, *, force_refresh: bool = False) -> str | None:
    """Return the version string of the installed agy CLI binary, or None."""
    now = time.monotonic()
    if not force_refresh and exe in _AGY_VERSION_CACHE:
        cached_at, cached_ver = _AGY_VERSION_CACHE[exe]
        if (now - cached_at) < _AGY_MODELS_CACHE_TTL_SEC:
            return cached_ver
    ver: str | None = None
    try:
        proc = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=3.0, env=_agy_cli_env(), check=False)
        raw = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        if proc.returncode == 0 and raw:
            first_line = raw.splitlines()[0].strip()
            if first_line and len(first_line) <= 64:
                ver = first_line
    except Exception as exc:
        log.debug("Failed to read agy version via %s: %s", exe, exc)
    _AGY_VERSION_CACHE[exe] = (now, ver)
    return ver


def _parse_agy_models_output(raw: str) -> list[dict[str, str]]:
    """`agy models` CLI 표준 출력을 파싱한다."""
    models: list[dict[str, str]] = []
    seen: set[str] = set()
    for chunk in re.split(r"[\r\n]+", raw or ""):
        line = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", chunk).strip()
        if not line or line.startswith("Fetching available models"):
            continue
        # 스피너 문자 제거
        line = re.sub(r"^[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏\s]+", "", line).strip()
        if not line or line.startswith("Fetching available models"):
            continue
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]+)(?:\t+|\s{2,})(.+)$", line)
        if not m:
            continue
        model_id = m.group(1).strip()
        label = m.group(2).strip()
        if model_id and model_id not in seen:
            seen.add(model_id)
            models.append({"id": model_id, "label": label})
    return models


def discover_agy_models(bin_name: str = "agy", *, timeout: float = 10.0, force_refresh: bool = False) -> dict[str, Any]:
    """Query the installed agy CLI for supported models without any fallback list."""
    exe = find_agy_executable(bin_name)
    if not exe:
        return {"models": [], "source": "unavailable", "cli_version": None, "error": f"agy binary '{bin_name}' was not found"}
    now = time.monotonic()
    if not force_refresh and exe in _AGY_MODELS_CACHE:
        cached_at, cached = _AGY_MODELS_CACHE[exe]
        ttl = _AGY_MODELS_CACHE_TTL_SEC if cached["source"] == "cli" else _AGY_UNAVAILABLE_CACHE_TTL_SEC
        if (now - cached_at) < ttl:
            return {"models": [dict(item) for item in cached["models"]], "source": cached["source"], "cli_version": cached["cli_version"], "error": cached["error"]}
    cli_version = get_agy_cli_version(exe, force_refresh=force_refresh)
    err_detail: str | None = None
    try:
        proc = subprocess.run([exe, "models"], capture_output=True, text=True, timeout=timeout, env=_agy_cli_env(), check=False)
        parsed = _parse_agy_models_output(proc.stdout or "")
        if proc.returncode == 0 and parsed:
            result = {"models": parsed, "source": "cli", "cli_version": cli_version, "error": None}
            _AGY_MODELS_CACHE[exe] = (now, result)
            return {"models": [dict(item) for item in parsed], "source": "cli", "cli_version": cli_version, "error": None}
        raw_err = ((proc.stderr or "") or (proc.stdout or "")).strip()
        err_detail = raw_err.splitlines()[0][:200] if raw_err else f"exit code {proc.returncode}"
    except Exception as exc:
        err_detail = str(exc)[:200]
        log.debug("Failed to list agy models via %s: %s", exe, exc)
    unavailable = {"models": [], "source": "unavailable", "cli_version": cli_version, "error": err_detail or "model discovery failed"}
    _AGY_MODELS_CACHE[exe] = (now, unavailable)
    return {"models": [], "source": "unavailable", "cli_version": cli_version, "error": unavailable["error"]}


def list_agy_models(bin_name: str = "agy", *, timeout: float = 10.0, force_refresh: bool = False) -> list[dict[str, str]]:
    """Return models dynamically verified by the installed agy CLI."""
    return discover_agy_models(bin_name, timeout=timeout, force_refresh=force_refresh)["models"]


def list_gemini_base_models() -> list[str]:
    """Return supported Google AI Studio base model names."""
    seen: list[str] = list(DEFAULT_GEMINI_BASE_MODELS)
    for m in _GEMINI_DISCOVERED_MODELS:
        base_m, _ = split_gemini_model_and_effort(m)
        if base_m and base_m not in seen:
            seen.append(base_m)
    return seen


def list_gemini_models() -> list[str]:
    """Return supported Google AI Studio generation models enumerated with embedded effort levels."""
    return enumerate_gemini_model_variants(list_gemini_base_models())


def _allowed_gemini_models(disc: dict[str, Any] | None = None) -> set[str]:
    """Return allowed enumerated and legacy bare Google AI Studio generation model names."""
    info = disc or {}
    return {m["id"] for m in info.get("models", ())} | {str(m) for m in info.get("base_models", ())} | set(list_gemini_models()) | set(list_gemini_base_models())


def _validate_provider_model(
    provider_name: str, field_name: str, value: str, allowed: set[str]
) -> None:
    if value not in allowed:
        if not allowed:
            raise ValueError(f"{provider_name} {field_name}('{value}')의 지원 여부를 확인할 수 없어 변경할 수 없습니다.")
        raise ValueError(
            f"지원되지 않는 {provider_name} {field_name} 입니다: '{value}'. "
            f"지원 목록: {', '.join(sorted(m or '(계정 기본)' for m in allowed))}"
        )


def _probe_gemini_model(api_key: str, model_name: str) -> tuple[bool, int | None, str | None]:
    """Check whether a Gemini API key and model respond to token counting."""
    try:
        from google import genai

        api_model, _ = split_gemini_model_and_effort(model_name)
        client = genai.Client(api_key=api_key)
        res = client.models.count_tokens(model=api_model, contents="ping")
        return True, getattr(res, "total_tokens", None), None
    except Exception as exc:
        return False, None, str(exc)

ALLOWED_EMBED_TASK_TYPES: tuple[str, ...] = (
    "SEMANTIC_SIMILARITY",
    "RETRIEVAL_DOCUMENT",
    "RETRIEVAL_QUERY",
    "CLASSIFICATION",
    "CLUSTERING",
)

ALLOWED_JEV_MODES: tuple[str, ...] = ("shadow", "filter", "hybrid")

ALLOWED_ROUTE_PROVIDERS: tuple[str, ...] = ("inherit", "gemini", "antigravity", "codex", "mock")

REMOVED_FALLBACK_CATALOG: tuple[dict[str, str], ...] = (
    {"id": "F1", "location": "antigravity_provider.extract", "behavior": "Structured JSON failure fell back to plain text JSON prompt", "status": "REMOVED"},
    {"id": "F2", "location": "gemini_provider.extract", "behavior": "Interactions schema failure fell back to plain text _extract_json_fallback", "status": "REMOVED"},
    {"id": "F3", "location": "antigravity/gemini/codex extract", "behavior": "Preface summary was silently overwritten with key_claims", "status": "REMOVED"},
    {"id": "F4", "location": "antigravity_provider.render_detail", "behavior": "high effort failure silently retried with medium effort", "status": "REMOVED"},
    {"id": "F5", "location": "antigravity_provider.render_detail", "behavior": "Body > 30k timeout silently retried with condensed context", "status": "REMOVED"},
    {"id": "F6", "location": "ingest/pipeline._embed", "behavior": "Embedding API error was swallowed and saved without vector", "status": "REMOVED"},
    {"id": "F7", "location": "antigravity_provider.embed", "behavior": "Missing key or error in dev fell back to SHA-256 hash vector", "status": "REMOVED"},
    {"id": "F8", "location": "gemini/antigravity.embed", "behavior": "Error stripped EmbedContentConfig (dropped task_type and projection space)", "status": "REMOVED"},
    {"id": "F9", "location": "classifier.py & classify_paper", "behavior": "Cross-provider CLI scan and regex heuristic fallback on error", "status": "REMOVED"},
    {"id": "F10", "location": "ingest/pdf & youtube", "behavior": "Non-LLM PDF/transcript parser fallback", "status": "OUT_OF_SCOPE"},
)

DEFAULT_FEATURE_ROUTING: dict[str, dict[str, str]] = {
    slot: {"provider": "inherit", "model": "", "effort": ""} for slot in FEATURE_SLOTS
}

DEFAULT_PROVIDERS_CONFIG: dict[str, Any] = {
    "version": 1,
    "active_provider": "mock",
    "feature_routing": copy.deepcopy(DEFAULT_FEATURE_ROUTING),
    "providers": {
        "gemini": {
            "api_key": "",
            "model": "gemini-3.1-flash-lite-medium",
            "effort": "medium",
            "embed_model": "gemini-embedding-001",
            "embed_task_type": "SEMANTIC_SIMILARITY",
            "min_interval": 4.0,
            "max_retries": 5,
        },
        "antigravity": {
            "bin": "agy",
            "model": "gemini-3.8-flash-medium",
            "effort": "medium",
            "timeout": 180.0,
            "detail_timeout": 300.0,
            "max_concurrency": 2,
        },
        "codex": {
            "bin": "codex",
            "model": "",
            "effort": "medium",
            "timeout": 300.0,
            "max_concurrency": 1,
        },
        "openai": {
            "api_key": "",
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o-mini",
            "embed_model": "text-embedding-3-small",
            "timeout": 60.0,
        },
        "jev": {
            "enabled": False,
            "api_key": "",
            "base_url": "https://api.typesafe.ai/v1",
            "timeout": 15.0,
            "model": "jev-latest",
            "mode": "filter",
            "filter_threshold": 0.20,
            "auto_merge_threshold": 0.90,
        },
        "stt": {
            "enabled": True,
            "provider": "gemini",
            "model": "",
            "language": "ko",
            "timeout": 600.0,
        },
    },
    "personal_cli": {
        "enabled": False,
        "active_cli": "none",
        "feature_routing": copy.deepcopy(DEFAULT_FEATURE_ROUTING),
    },
    "updated_at": 0.0,
}

MIGRATED_ENV_VARS: set[str] = {
    # Active provider selection
    "CLAIRE_PROVIDER",
    # Gemini
    "GEMINI_API_KEY",
    "CLAIRE_GEMINI_MODEL",
    "CLAIRE_GEMINI_EFFORT",
    "CLAIRE_GEMINI_EMBED_MODEL",
    "CLAIRE_GEMINI_MIN_INTERVAL",
    "CLAIRE_GEMINI_MAX_RETRIES",
    # Antigravity CLI
    "CLAIRE_AGY_BIN",
    "CLAIRE_AGY_MODEL",
    "CLAIRE_AGY_EFFORT",
    "CLAIRE_AGY_TIMEOUT",
    "CLAIRE_AGY_MAX_CONCURRENCY",
    # Codex CLI
    "CLAIRE_CODEX_BIN",
    "CLAIRE_CODEX_MODEL",
    "CLAIRE_CODEX_EFFORT",
    "CLAIRE_CODEX_TIMEOUT",
    "CLAIRE_CODEX_MAX_CONCURRENCY",
    # Jev
    "CLAIRE_ENABLE_JEV",
    "CLAIRE_JEV_API_KEY",
    "CLAIRE_JEV_BASE_URL",
    "CLAIRE_JEV_TIMEOUT",
    "CLAIRE_JEV_MODEL",
    # STT
    "CLAIRE_ENABLE_VIDEO_TRANSCRIPTION",
    "CLAIRE_STT_PROVIDER",
    "CLAIRE_STT_MODEL",
    "CLAIRE_STT_LANGUAGE",
    "CLAIRE_STT_TIMEOUT",
}

SUPPORTED_ACTIVE_PROVIDERS = ("mock", "gemini", "antigravity", "codex", "openai")
SUPPORTED_SERVER_ACTIVE_PROVIDERS = ("mock", "gemini")
ALLOWED_SERVER_ROUTE_PROVIDERS = ("inherit", "gemini", "mock")
ALLOWED_PERSONAL_CLI_PROVIDERS = ("none", "antigravity", "codex")

_GEMINI_MODELS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_GEMINI_CACHE_TTL = 300.0


def discover_gemini_models(api_key: str = "", *, force_refresh: bool = False) -> dict[str, Any]:
    """Query Google AI Studio (models.list) for supported generation and embedding models."""
    clean_key = str(api_key or "").strip()
    default_base = list_gemini_base_models()
    default_variants = enumerate_gemini_model_variants(default_base)
    default_emb = list(DEFAULT_GEMINI_EMBED_MODELS)
    if not clean_key or "••" in clean_key:
        return {
            "models": [{"id": m} for m in default_variants],
            "base_models": list(default_base),
            "embed_models": [{"id": m} for m in default_emb],
            "source": "default",
            "error": None,
        }

    cache_key = clean_key[:12] + ":" + str(len(clean_key))
    now = time.time()
    if not force_refresh and cache_key in _GEMINI_MODELS_CACHE:
        ts, cached = _GEMINI_MODELS_CACHE[cache_key]
        if now - ts < _GEMINI_CACHE_TTL:
            return copy.deepcopy(cached)

    try:
        from google import genai

        client = genai.Client(api_key=clean_key)
        gen_base_ids: list[str] = []
        emb_ids: list[str] = []
        for item in client.models.list():
            raw_name = str(getattr(item, "name", "") or "").strip()
            mid = raw_name.removeprefix("models/").strip()
            if not mid:
                continue
            actions = [str(a) for a in (getattr(item, "supported_actions", None) or getattr(item, "supported_generation_methods", None) or [])]
            low = mid.lower()
            if "embedContent" in actions or "embedding" in low:
                if mid.startswith(("gemini-embedding", "text-embedding")) and mid not in emb_ids:
                    emb_ids.append(mid)
            elif "generateContent" in actions and mid.startswith("gemini-"):
                if not any(skip in low for skip in ("embedding", "imagen", "veo", "aqa", "tts", "audio")) and mid not in gen_base_ids:
                    gen_base_ids.append(mid)

        for m in default_base:
            if m not in gen_base_ids:
                gen_base_ids.append(m)
        for em in default_emb:
            if em not in emb_ids:
                emb_ids.append(em)

        gen_variants = enumerate_gemini_model_variants(gen_base_ids)
        res = {
            "models": [{"id": m} for m in gen_variants],
            "base_models": list(gen_base_ids),
            "embed_models": [{"id": m} for m in emb_ids],
            "source": "api",
            "error": None,
        }
        _GEMINI_MODELS_CACHE[cache_key] = (now, res)
        return copy.deepcopy(res)
    except Exception as exc:
        return {
            "models": [{"id": m} for m in default_variants],
            "base_models": list(default_base),
            "embed_models": [{"id": m} for m in default_emb],
            "source": "default",
            "error": f"{type(exc).__name__}: {exc}",
        }


def comment_out_env_file(file_path: Path, keys_to_comment: set[str]) -> int:
    """주어진 env 파일에서 keys_to_comment에 해당하는 활성 환경변수 할당 라인을 주석처리한다.

    이미 주석(#) 처리된 라인은 건너뛰고, 원본 포맷과 미해당 설정을 유지한다.
    반환값: 주석 처리된 라인 수.
    """
    if not file_path.is_file():
        return 0

    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as exc:
        log.warning("Failed to read env file %s for commenting: %s", file_path, exc)
        return 0

    lines = content.splitlines(keepends=True)
    new_lines: list[str] = []
    commented_count = 0

    line_re = re.compile(r"^(\s*)(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            new_lines.append(line)
            continue

        m = line_re.match(line)
        if m:
            indent, var_name, rest = m.groups()
            if var_name in keys_to_comment:
                commented_count += 1
                endl = "\n" if line.endswith("\n") else ""
                new_line = (
                    f"{indent}# {var_name}={rest.rstrip(chr(13) + chr(10))}  "
                    f"# Migrated to {PROVIDERS_CONFIG_FILENAME}{endl}"
                )
                new_lines.append(new_line)
                continue

        new_lines.append(line)

    if commented_count > 0:
        try:
            file_path.write_text("".join(new_lines), encoding="utf-8")
            log.info("Commented out %d migrated variables in %s", commented_count, file_path)
        except Exception as exc:
            log.warning("Failed to write commented env file %s: %s", file_path, exc)

    return commented_count


def _parse_env_file_values(file_path: Path) -> dict[str, str]:
    """간단한 dotenv 파서 — 파일에서 KEY=VALUE 쌍을 추출한다."""
    if not file_path.is_file():
        return {}
    res: dict[str, str] = {}
    line_re = re.compile(r"^(\s*)(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")
    try:
        for line in file_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            m = line_re.match(line)
            if m:
                _, k, v = m.groups()
                v = v.strip()
                if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                    v = v[1:-1]
                res[k] = v
    except Exception:
        pass
    return res


class ProviderManager:
    """data/providers.json 파일 기반의 프로바이더 설정 관리자."""

    def __init__(
        self,
        data_dir: str | Path | None = None,
        *,
        auto_migrate: bool = True,
        env_files: list[Path] | None = None,
    ) -> None:
        if data_dir is not None:
            p = Path(data_dir)
            self.data_dir = p if p.is_absolute() else (ROOT / p).resolve()
        else:
            cand = os.environ.get("CB_DATA_DIR") or os.environ.get("CLAIRE_DB_PATH")
            if cand:
                p = Path(cand)
                p = p.parent if cand.endswith(".db") else p
                self.data_dir = p if p.is_absolute() else (ROOT / p).resolve()
            else:
                self.data_dir = (ROOT / "data").resolve()

        self.registry_path = self.data_dir / PROVIDERS_CONFIG_FILENAME
        self._env_files = env_files or [ROOT / ".env", ROOT / ".env.dev"]
        if auto_migrate:
            self.migrate_from_env_if_needed()

    def get_config(self) -> dict[str, Any]:
        """디스크에서 providers.json 설정을 로드하거나 기본값을 반환한다."""
        if not self.registry_path.is_file():
            return copy.deepcopy(DEFAULT_PROVIDERS_CONFIG)

        try:
            raw = json.loads(self.registry_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return copy.deepcopy(DEFAULT_PROVIDERS_CONFIG)

            cfg = copy.deepcopy(DEFAULT_PROVIDERS_CONFIG)
            cfg["version"] = raw.get("version", 1)
            cfg["active_provider"] = raw.get("active_provider", cfg["active_provider"])
            cfg["updated_at"] = raw.get("updated_at", 0.0)

            raw_providers = raw.get("providers", {})
            if isinstance(raw_providers, dict):
                for prov_name, prov_defaults in cfg["providers"].items():
                    if prov_name in raw_providers and isinstance(raw_providers[prov_name], dict):
                        prov_defaults.update(raw_providers[prov_name])
                # 알 수 없는 커스텀 프로바이더도 보존
                for prov_name, prov_val in raw_providers.items():
                    if prov_name not in cfg["providers"] and isinstance(prov_val, dict):
                        cfg["providers"][prov_name] = prov_val

            if "feature_routing" in raw:
                cfg["feature_routing"] = self._normalize_feature_routing(raw.get("feature_routing"))

            if "personal_cli" in raw and isinstance(raw.get("personal_cli"), dict):
                raw_pcli = raw["personal_cli"]
                pcli_out = copy.deepcopy(cfg["personal_cli"])
                if "enabled" in raw_pcli:
                    pcli_out["enabled"] = bool(raw_pcli["enabled"])
                if "active_cli" in raw_pcli:
                    ac = str(raw_pcli.get("active_cli") or "none").strip().lower()
                    if ac in ALLOWED_PERSONAL_CLI_PROVIDERS:
                        pcli_out["active_cli"] = ac
                if "feature_routing" in raw_pcli:
                    pcli_out["feature_routing"] = self._normalize_feature_routing(raw_pcli.get("feature_routing"))
                cfg["personal_cli"] = pcli_out

            return cfg
        except Exception as exc:
            log.warning("Failed to load %s: %s. Using default config.", self.registry_path, exc)
            return copy.deepcopy(DEFAULT_PROVIDERS_CONFIG)

    def save_config(self, config: dict[str, Any]) -> None:
        """providers.json 설정을 원자적(atomic write)으로 저장한다."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        config["updated_at"] = time.time()
        raw_json = json.dumps(config, ensure_ascii=False, indent=2)
        with tempfile.NamedTemporaryFile("w", dir=self.data_dir, delete=False, encoding="utf-8") as tf:
            tf.write(raw_json)
            tmp_name = tf.name
        os.replace(tmp_name, self.registry_path)
        log.info("Saved provider configuration to %s", self.registry_path)

    def get_sanitized_config(self) -> dict[str, Any]:
        """WebUI 전달용으로 민감한 API 키를 마스킹하고 환경 감지 정보를 보강하여 반환한다."""
        cfg = self.get_config()
        out = copy.deepcopy(cfg)
        providers = out.get("providers", {})

        # Google AI Studio (Gemini)
        if "gemini" in providers:
            g = providers["gemini"]
            key = str(g.get("api_key") or "")
            g["has_api_key"] = bool(key.strip())
            g["api_key"] = "••••••••" if g["has_api_key"] else ""
            gem_disc = discover_gemini_models(key)
            g["available_models"] = gem_disc["models"]
            g["available_embed_models"] = gem_disc["embed_models"]
            g["models_source"] = gem_disc["source"]
            if gem_disc.get("error"):
                g["models_error"] = gem_disc["error"]

        disc_agy: dict[str, Any] | None = None

        # Antigravity CLI
        if "antigravity" in providers:
            agy = providers["antigravity"]
            bin_name = str(agy.get("bin") or "agy")
            agy["installed"] = find_agy_executable(bin_name) is not None
            disc_agy = discover_agy_models(bin_name)
            agy["available_models"] = disc_agy["models"]
            agy["models_source"] = disc_agy["source"]
            agy["cli_version"] = disc_agy["cli_version"]
            if disc_agy["error"]:
                agy["models_error"] = disc_agy["error"]

        # Codex CLI
        if "codex" in providers:
            cdx = providers["codex"]
            bin_name = str(cdx.get("bin") or "codex")
            cdx["installed"] = find_codex_executable(bin_name) is not None
            cdx["available_models"] = [
                {"id": m, "label": m or "계정 기본"} for m in DEFAULT_CODEX_MODELS
            ]

        # OpenAI
        if "openai" in providers:
            oai = providers["openai"]
            key = str(oai.get("api_key") or "")
            oai["has_api_key"] = bool(key.strip())
            oai["api_key"] = "••••••••" if oai["has_api_key"] else ""
            oai["available_models"] = [{"id": m} for m in DEFAULT_OPENAI_MODELS]
            oai["available_embed_models"] = [{"id": m} for m in DEFAULT_OPENAI_EMBED_MODELS]

        # Jev
        if "jev" in providers:
            jev = providers["jev"]
            key = str(jev.get("api_key") or "")
            jev["has_api_key"] = bool(key.strip())
            jev["api_key"] = "••••••••" if jev["has_api_key"] else ""

        out["capabilities"] = self._evaluate_capabilities(cfg, agy_discovery=disc_agy)
        out["feature_routing"] = self._normalize_feature_routing(cfg.get("feature_routing"))
        raw_pcli = cfg.get("personal_cli") if isinstance(cfg.get("personal_cli"), dict) else {}
        out["personal_cli"] = {
            "enabled": bool(raw_pcli.get("enabled", False)),
            "active_cli": str(raw_pcli.get("active_cli") or "none").strip().lower(),
            "feature_routing": self._normalize_feature_routing(raw_pcli.get("feature_routing")),
        }
        out["feature_slots"] = list(FEATURE_SLOTS)
        out["server_active_providers"] = list(SUPPORTED_SERVER_ACTIVE_PROVIDERS)
        out["server_route_providers"] = list(ALLOWED_SERVER_ROUTE_PROVIDERS)
        out["model_ranks"] = dict(MODEL_CAPABILITY_RANKS)
        out["embed_task_types"] = list(ALLOWED_EMBED_TASK_TYPES)
        out["jev_modes"] = list(ALLOWED_JEV_MODES)
        out["lineage_summary"] = self.get_lineage_summary(cfg)
        return out

    @staticmethod
    def _normalize_feature_routing(raw_routing: Any) -> dict[str, dict[str, str]]:
        """Normalize per-feature routing configuration onto all known feature slots."""
        out = copy.deepcopy(DEFAULT_FEATURE_ROUTING)
        if not isinstance(raw_routing, dict):
            return out
        for slot in FEATURE_SLOTS:
            entry = raw_routing.get(slot)
            if not isinstance(entry, dict):
                continue
            prov = str(entry.get("provider") or "inherit").strip().lower() or "inherit"
            model = str(entry.get("model") or "").strip()
            effort = str(entry.get("effort") or "").strip().lower()
            out[slot] = {"provider": prov, "model": model, "effort": effort}
        return out

    def get_lineage_summary(self, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
        """Compute effective pipeline stages, JEV telemetry, and recent document model lineage."""
        import sqlite3

        snap = cfg if cfg is not None else self.get_config()
        providers = snap.get("providers", {})
        active = str(snap.get("active_provider") or "mock").strip().lower()
        routing = self._normalize_feature_routing(snap.get("feature_routing"))
        jev_cfg = providers.get("jev", {})

        def _base_model(prov_name: str, slot: str) -> tuple[str, str]:
            if prov_name == "gemini":
                if slot == "embed":
                    return str(providers.get("gemini", {}).get("embed_model") or "gemini-embedding-001"), ""
                gem_m = str(providers.get("gemini", {}).get("model") or "")
                _, gem_embedded_eff = split_gemini_model_and_effort(gem_m)
                return gem_m, gem_embedded_eff or str(providers.get("gemini", {}).get("effort") or "")
            if prov_name == "antigravity":
                return str(providers.get("antigravity", {}).get("model") or ""), str(providers.get("antigravity", {}).get("effort") or "")
            if prov_name == "codex":
                return str(providers.get("codex", {}).get("model") or "codex-default"), str(providers.get("codex", {}).get("effort") or "")
            if prov_name == "mock":
                return "mock", ""
            return "", ""

        effective_slots: dict[str, dict[str, str]] = {}
        for slot in FEATURE_SLOTS:
            r = routing[slot]
            eff_prov = active if r["provider"] == "inherit" else r["provider"]
            if slot == "embed" and eff_prov in ("antigravity", "codex"):
                eff_prov = "gemini" if providers.get("gemini", {}).get("api_key") else "fts-only"
            base_m, base_e = _base_model(eff_prov, slot)
            eff_model = r["model"] or base_m
            eff_effort = r["effort"] or base_e
            if eff_prov == "gemini" and slot != "embed":
                _, slot_embedded_eff = split_gemini_model_and_effort(eff_model)
                if slot_embedded_eff:
                    eff_effort = slot_embedded_eff
            effective_slots[slot] = {
                "provider": eff_prov,
                "model": eff_model,
                "effort": eff_effort,
                "inherited": str(r["provider"] == "inherit").lower(),
            }

        recent_docs: list[dict[str, Any]] = []
        extraction_models: list[dict[str, Any]] = []
        decision_gates: list[dict[str, Any]] = []
        jev_stats: dict[str, Any] = {
            "enabled": bool(jev_cfg.get("enabled", False)),
            "mode": str(jev_cfg.get("mode") or "filter"),
            "filter_threshold": float(jev_cfg.get("filter_threshold", 0.20)),
            "auto_merge_threshold": float(jev_cfg.get("auto_merge_threshold", 0.90)),
            "model": str(jev_cfg.get("model") or "jev-latest"),
            "shadow_pairs": 0,
            "ok_calls": 0,
            "avg_latency_ms": None,
            "below_filter_threshold": 0,
        }

        db_candidates = []
        env_db = os.environ.get("CLAIRE_DB_PATH")
        if env_db:
            db_candidates.append(Path(env_db))
        db_candidates.append(self.data_dir / "claire.db")
        db_path = next((p for p in db_candidates if p.is_file()), None)

        if db_path is not None:
            try:
                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
                conn.row_factory = sqlite3.Row
                try:
                    cols = {r[1] for r in conn.execute("PRAGMA table_info(documents)").fetchall()}
                    if "meta_json" in cols:
                        rows = conn.execute("SELECT id, title, meta_json, created_at FROM documents ORDER BY created_at DESC LIMIT 20").fetchall()
                        for row in rows:
                            try:
                                meta = json.loads(row["meta_json"] or "{}")
                            except Exception:
                                meta = {}
                            if isinstance(meta, dict) and isinstance(meta.get("model_lineage"), dict):
                                recent_docs.append({
                                    "id": row["id"],
                                    "title": row["title"],
                                    "created_at": row["created_at"],
                                    "lineage": meta["model_lineage"],
                                })
                    tbls = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                    if "extractions" in tbls:
                        for er in conn.execute("SELECT provider, model, COUNT(*) AS cnt FROM extractions GROUP BY provider, model ORDER BY cnt DESC LIMIT 12").fetchall():
                            extraction_models.append({"provider": er["provider"], "model": er["model"], "count": int(er["cnt"])})
                    if "resolution_decisions" in tbls:
                        for gr in conn.execute("SELECT gate, outcome, COUNT(*) AS cnt FROM resolution_decisions GROUP BY gate, outcome ORDER BY cnt DESC").fetchall():
                            decision_gates.append({"gate": gr["gate"], "outcome": gr["outcome"], "count": int(gr["cnt"])})
                    if "review_shadow_pairs" in tbls:
                        jr = conn.execute(
                            "SELECT COUNT(*) AS total, SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) AS ok_cnt, AVG(CASE WHEN status='ok' THEN latency_ms END) AS avg_ms, SUM(CASE WHEN status='ok' AND jev_noul < ? THEN 1 ELSE 0 END) AS below_thr FROM review_shadow_pairs",
                            (jev_stats["filter_threshold"],),
                        ).fetchone()
                        if jr is not None:
                            jev_stats["shadow_pairs"] = int(jr["total"] or 0)
                            jev_stats["ok_calls"] = int(jr["ok_cnt"] or 0)
                            jev_stats["avg_latency_ms"] = round(float(jr["avg_ms"]), 1) if jr["avg_ms"] is not None else None
                            jev_stats["below_filter_threshold"] = int(jr["below_thr"] or 0)
                finally:
                    conn.close()
            except Exception as exc:
                log.debug("lineage summary db read skipped: %s", exc)

        return {
            "effective_slots": effective_slots,
            "jev": jev_stats,
            "fallbacks": list(REMOVED_FALLBACK_CATALOG),
            "architecture_specs": [
                {
                    "stage": "벡터 임베딩 모델 (고정)",
                    "fixed_value": "gemini-embedding-001 (3072d · Google AI Studio)",
                    "scope": "config.py, extract/gemini_provider.py, store/vectors.py",
                    "rationale": "엔티티 프레임(이름·타입·별칭·관찰) 색인, 하이브리드 검색, 유사 엔티티 후보 탐색에 사용하며 테마별 벡터 공간 일관성을 위해 서버에서 단일 모델로 고정합니다.",
                },
                {
                    "stage": "임베딩 Task Type (문서 색인)",
                    "fixed_value": "RETRIEVAL_DOCUMENT",
                    "scope": "extract/gemini_provider.py, ingest/pipeline.py",
                    "rationale": "엔티티·문서 벡터 DB 색인 시 비대칭 검색(문서 측) 용도로 코드에서 고정 적용합니다.",
                },
                {
                    "stage": "임베딩 Task Type (검색 질의)",
                    "fixed_value": "RETRIEVAL_QUERY",
                    "scope": "retrieval/query.py",
                    "rationale": "사용자 질의 임베딩 시 검색 질문 전용 좌표계로 코드에서 고정 적용합니다.",
                },
                {
                    "stage": "임베딩 -> JEV 데이터 전달",
                    "fixed_value": "Gate 2·3 통과 후보만 자연어 JSON 스냅샷 전달 (Cosine >= 0.72)",
                    "scope": "extract/resolver.py, extract/jev.py",
                    "rationale": "벡터 좌표를 직접 JEV에 넣지 않고 규칙/초고신뢰 자동 병합 후 남은 경계선 후보의 이름·타입·별칭·관찰만 전달합니다.",
                },
                {
                    "stage": "JEV Hybrid 단독 1위 가드",
                    "fixed_value": "1위 >= 0.90, 2위 < 0.50, 격차 >= 0.40",
                    "scope": "extract/resolver.py",
                    "rationale": "복수 후보가 동시에 고득점(noul)일 때 오병합을 막기 위해 System 2 LLM 판정으로 위임합니다.",
                },
            ],
            "recent_documents": recent_docs,
            "extraction_models": extraction_models,
            "decision_gates": decision_gates,
        }

    def _merge_payload(self, base: dict[str, Any], payload: dict[str, Any], *, validate_models: bool = True) -> dict[str, Any]:
        """Merge an incoming provider update payload onto a config snapshot."""
        merged = copy.deepcopy(base)
        curr_providers = merged.setdefault("providers", {})

        if "active_provider" in payload:
            act = str(payload["active_provider"]).strip().lower()
            if act not in SUPPORTED_ACTIVE_PROVIDERS:
                raise ValueError(
                    f"지원되지 않는 active_provider 입니다: '{act}'. "
                    f"지원 목록: {', '.join(SUPPORTED_ACTIVE_PROVIDERS)}"
                )
            merged["active_provider"] = act

        incoming_providers = payload.get("providers", {})
        if isinstance(incoming_providers, dict):
            for name, inc_data in incoming_providers.items():
                if not isinstance(inc_data, dict):
                    continue
                target = curr_providers.setdefault(name, {})

                if "api_key" in inc_data:
                    raw_key = str(inc_data["api_key"]).strip()
                    if raw_key and raw_key != "••••••••":
                        target["api_key"] = raw_key
                    elif not raw_key and "api_key" in target and inc_data.get("clear_api_key"):
                        target["api_key"] = ""

                if validate_models:
                    if name == "gemini":
                        gem_key_for_check = str(inc_data.get("api_key") or target.get("api_key") or "").strip()
                        if "••" in gem_key_for_check:
                            gem_key_for_check = str(target.get("api_key") or "").strip()
                        gem_disc_check = discover_gemini_models(gem_key_for_check)
                        if "model" in inc_data:
                            allowed_gem = _allowed_gemini_models(gem_disc_check)
                            _validate_provider_model("gemini", "모델", str(inc_data["model"]).strip(), allowed_gem)
                        if "embed_model" in inc_data:
                            allowed_emb = {m["id"] for m in gem_disc_check["embed_models"]} | set(DEFAULT_GEMINI_EMBED_MODELS)
                            _validate_provider_model("gemini", "임베딩 모델", str(inc_data["embed_model"]).strip(), allowed_emb)
                        if "embed_task_type" in inc_data:
                            _validate_provider_model("gemini", "임베딩 task_type", str(inc_data["embed_task_type"]).strip(), set(ALLOWED_EMBED_TASK_TYPES))
                    elif name == "antigravity" and "model" in inc_data:
                        bin_for_check = str(inc_data.get("bin") or target.get("bin") or "agy").strip()
                        allowed_agy = {m["id"] for m in list_agy_models(bin_for_check)}
                        _validate_provider_model("antigravity", "모델", str(inc_data["model"]).strip(), allowed_agy)
                    elif name == "codex" and "model" in inc_data:
                        _validate_provider_model("codex", "모델", str(inc_data["model"]).strip(), set(DEFAULT_CODEX_MODELS))
                    elif name == "openai":
                        if "model" in inc_data:
                            _validate_provider_model("openai", "모델", str(inc_data["model"]).strip(), set(DEFAULT_OPENAI_MODELS))
                        if "embed_model" in inc_data:
                            _validate_provider_model("openai", "임베딩 모델", str(inc_data["embed_model"]).strip(), set(DEFAULT_OPENAI_EMBED_MODELS))
                    elif name == "jev":
                        if "mode" in inc_data:
                            _validate_provider_model("jev", "판정 모드", str(inc_data["mode"]).strip().lower(), set(ALLOWED_JEV_MODES))
                        for thr_key in ("filter_threshold", "auto_merge_threshold"):
                            if thr_key in inc_data:
                                thr_val = float(inc_data[thr_key])
                                if not (0.0 <= thr_val <= 1.0):
                                    raise ValueError(f"jev {thr_key} 값은 0.0~1.0 범위여야 합니다.")

                for k, v in inc_data.items():
                    if k in ("api_key", "has_api_key", "clear_api_key", "installed", "available_models", "available_embed_models", "models_source", "cli_version", "models_error"):
                        continue
                    target[k] = v

                if name == "gemini" and "model" in inc_data:
                    _, inferred_gem_effort = split_gemini_model_and_effort(str(inc_data["model"]))
                    if inferred_gem_effort:
                        target["effort"] = inferred_gem_effort

                if name == "antigravity" and "model" in inc_data and "effort" not in inc_data:
                    inferred_effort = infer_agy_effort_from_model(str(inc_data["model"]))
                    if inferred_effort:
                        target["effort"] = inferred_effort

        if "feature_routing" in payload and isinstance(payload["feature_routing"], dict):
            curr_routing = self._normalize_feature_routing(merged.get("feature_routing"))
            for slot, raw_slot in payload["feature_routing"].items():
                if slot not in FEATURE_SLOTS:
                    raise ValueError(f"지원되지 않는 feature_routing 슬롯입니다: '{slot}'")
                if not isinstance(raw_slot, dict):
                    raise ValueError(f"feature_routing['{slot}'] 값은 객체여야 합니다.")
                prov = str(raw_slot.get("provider") or curr_routing[slot].get("provider") or "inherit").strip().lower()
                if prov not in ALLOWED_ROUTE_PROVIDERS:
                    raise ValueError(f"feature_routing['{slot}'] 프로바이더 '{prov}'는 지원되지 않습니다.")
                model = str(raw_slot.get("model") if "model" in raw_slot else curr_routing[slot].get("model", "")).strip()
                effort = str(raw_slot.get("effort") if "effort" in raw_slot else curr_routing[slot].get("effort", "")).strip().lower()
                if validate_models and model:
                    eff_prov = merged.get("active_provider", "mock") if prov == "inherit" else prov
                    if eff_prov == "gemini":
                        gem_key_slot = str(curr_providers.get("gemini", {}).get("api_key") or "").strip()
                        gem_disc_slot = discover_gemini_models(gem_key_slot)
                        allowed = ({m["id"] for m in gem_disc_slot["embed_models"]} | set(DEFAULT_GEMINI_EMBED_MODELS)) if slot == "embed" else _allowed_gemini_models(gem_disc_slot)
                        _validate_provider_model("gemini", f"{slot} 모델", model, allowed)
                        if slot != "embed":
                            _, slot_gem_effort = split_gemini_model_and_effort(model)
                            if slot_gem_effort:
                                effort = slot_gem_effort
                    elif eff_prov == "codex":
                        _validate_provider_model("codex", f"{slot} 모델", model, set(DEFAULT_CODEX_MODELS))
                    elif eff_prov == "antigravity":
                        bin_name = str(curr_providers.get("antigravity", {}).get("bin") or "agy").strip()
                        allowed_agy = {m["id"] for m in list_agy_models(bin_name)}
                        _validate_provider_model("antigravity", f"{slot} 모델", model, allowed_agy)
                curr_routing[slot] = {"provider": prov, "model": model, "effort": effort}
            merged["feature_routing"] = curr_routing

        return merged

    def _evaluate_capabilities(self, cfg: dict[str, Any], *, live: bool = False, refresh_agy: bool = False, agy_discovery: dict[str, Any] | None = None) -> dict[str, Any]:
        """Evaluate runtime capabilities, feature support, and UI/save lock status per provider."""
        providers = cfg.get("providers", {})
        gemini = providers.get("gemini", {})
        agy = providers.get("antigravity", {})
        cdx = providers.get("codex", {})
        stt = providers.get("stt", {})
        active = str(cfg.get("active_provider") or "mock").strip().lower()
        in_container = is_running_in_container()

        gem_key = str(gemini.get("api_key") or "").strip()
        gem_has_key = bool(gem_key)
        gem_model = str(gemini.get("model") or "").strip()
        gem_embed = str(gemini.get("embed_model") or "").strip()
        gem_disc = discover_gemini_models(gem_key, force_refresh=live)
        gem_model_ids = _allowed_gemini_models(gem_disc)
        gem_embed_ids = [m["id"] for m in gem_disc["embed_models"]]

        # Google AI Studio (Gemini) capability evaluation
        gem_lock: str | None = None
        gem_live_ok: bool | None = None
        if not gem_has_key:
            gem_lock = "Google AI Studio API Key가 설정되지 않아 활성화할 수 없습니다."
        elif gem_model not in gem_model_ids:
            gem_lock = f"선택한 Google AI Studio 모델('{gem_model}')이 지원 목록에 없습니다."
        elif gem_embed not in set(gem_embed_ids):
            gem_lock = f"선택한 Google AI Studio 임베딩 모델('{gem_embed}')이 지원 목록에 없습니다."
        elif live:
            ok, _tok, err = _probe_gemini_model(gem_key, gem_model)
            gem_live_ok = ok
            if not ok:
                gem_lock = f"Google AI Studio 모델('{gem_model}') 실행 확인 실패: {err}"

        # Antigravity CLI capability evaluation
        agy_bin = str(agy.get("bin") or "agy").strip() or "agy"
        agy_model = str(agy.get("model") or "").strip()
        disc = agy_discovery if agy_discovery is not None else discover_agy_models(agy_bin, force_refresh=refresh_agy)
        agy_ids = [m["id"] for m in disc["models"]]
        agy_ver = disc.get("cli_version")
        ver_label = f"v{agy_ver}" if agy_ver else "버전 확인 불가"
        agy_lock: str | None = None
        if find_agy_executable(agy_bin) is None:
            agy_lock = f"Antigravity CLI 바이너리('{agy_bin}')를 찾을 수 없습니다."
        elif disc["source"] != "cli" or not agy_ids:
            err_note = f" ({disc['error']})" if disc.get("error") else ""
            agy_lock = f"Antigravity CLI ({ver_label})에서 지원 모델 조회(agy models)에 실패하여 기능성을 확인할 수 없습니다{err_note}. 호스트 agy를 업데이트하거나 상태를 점검하세요."
        elif agy_model not in set(agy_ids):
            agy_lock = f"선택한 모델('{agy_model}')은 설치된 Antigravity CLI ({ver_label})의 지원 모델 목록에 없습니다."

        # Codex CLI capability evaluation
        cdx_bin = str(cdx.get("bin") or "codex").strip() or "codex"
        cdx_model = str(cdx.get("model") or "").strip()
        cdx_lock: str | None = None
        if in_container:
            cdx_lock = "Codex CLI는 호스트 네이티브 전용이므로 Docker/컨테이너 환경에서는 사용할 수 없습니다."
        elif find_codex_executable(cdx_bin) is None:
            cdx_lock = f"Codex CLI 바이너리('{cdx_bin}')를 찾을 수 없습니다."
        elif cdx_model not in set(DEFAULT_CODEX_MODELS):
            cdx_lock = f"선택한 Codex 모델('{cdx_model}')이 지원 목록에 없습니다."

        # OpenAI capability evaluation (always locked until backend exists)
        oai_lock = "OpenAI 호환 LLM/임베딩 백엔드가 아직 구현되어 있지 않아 프로바이더로 사용할 수 없습니다."

        cli_embed_warning = None if gem_has_key else "CLI 프로바이더는 자체 임베딩이 없어 Google AI Studio API Key 없이는 운영 환경에서 벡터 검색 없이 FTS만 동작합니다."

        provider_caps: dict[str, dict[str, Any]] = {
            "gemini": {
                "selectable": gem_lock is None,
                "locked_reason": gem_lock,
                "live_checked": gem_live_ok is not None,
                "features": {"extract": True, "research": True, "embedding": True, "stt_vlm": True},
                "models_source": gem_disc["source"],
                "models_error": gem_disc.get("error"),
                "available_models": gem_disc["models"],
                "available_embed_models": gem_disc["embed_models"],
            },
            "antigravity": {
                "selectable": agy_lock is None,
                "locked_reason": agy_lock,
                "features": {"extract": True, "research": True, "embedding": gem_has_key, "stt_vlm": False},
                "embedding_note": cli_embed_warning,
                "cli_version": agy_ver,
                "models_source": disc["source"],
                "models_error": disc.get("error"),
                "available_models": disc["models"],
            },
            "codex": {
                "selectable": cdx_lock is None,
                "locked_reason": cdx_lock,
                "features": {"extract": True, "research": True, "embedding": gem_has_key, "stt_vlm": False},
                "embedding_note": cli_embed_warning,
                "available_models": [{"id": m, "label": m or "계정 기본"} for m in DEFAULT_CODEX_MODELS],
            },
            "openai": {
                "selectable": False,
                "locked_reason": oai_lock,
                "features": {"extract": False, "research": False, "embedding": False, "stt_vlm": False},
            },
            "mock": {
                "selectable": True,
                "locked_reason": None,
                "features": {"extract": False, "research": False, "embedding": False, "stt_vlm": False},
                "advisory": "가상 테스트 프로바이더입니다. 실제 AI 추출·요약·임베딩을 수행하지 않습니다.",
            },
        }

        lock_reasons: list[str] = []
        active_cap = provider_caps.get(active)
        if active_cap is None:
            lock_reasons.append(f"알 수 없는 active_provider: '{active}'")
        elif not active_cap["selectable"] and active_cap.get("locked_reason"):
            lock_reasons.append(active_cap["locked_reason"])

        warnings: list[str] = []
        if active in ("antigravity", "codex") and cli_embed_warning:
            warnings.append(cli_embed_warning)
        if active == "mock":
            warnings.append(provider_caps["mock"]["advisory"])

        stt_enabled = stt.get("enabled", True) is not False
        stt_prov = str(stt.get("provider") or "gemini").strip().lower()
        stt_ready = (not stt_enabled) or stt_prov == "mock" or (stt_prov == "gemini" and gem_has_key)
        if stt_enabled and stt_prov == "gemini" and not gem_has_key:
            warnings.append("STT가 Google AI Studio로 설정되어 있으나 API Key가 없어 음성 전사가 동작하지 않습니다.")

        jev_cfg = providers.get("jev", {})
        return {
            "active_provider": active,
            "in_container": in_container,
            "can_save": len(lock_reasons) == 0,
            "lock_reasons": lock_reasons,
            "warnings": warnings,
            "stt": {"enabled": stt_enabled, "provider": stt_prov, "ready": stt_ready},
            "jev": {
                "enabled": bool(jev_cfg.get("enabled", False)),
                "mode": str(jev_cfg.get("mode") or "filter"),
                "filter_threshold": float(jev_cfg.get("filter_threshold", 0.20)),
                "auto_merge_threshold": float(jev_cfg.get("auto_merge_threshold", 0.90)),
            },
            "providers": provider_caps,
        }

    def check_capabilities(self, candidate: dict[str, Any] | None = None, *, live: bool = False, refresh_agy: bool = False) -> dict[str, Any]:
        """Evaluate capability and lock state for a stored or unsaved candidate configuration."""
        base = self.get_config()
        merged = self._merge_payload(base, candidate, validate_models=False) if candidate else base
        return self._evaluate_capabilities(merged, live=live, refresh_agy=refresh_agy)

    def update_config(self, payload: dict[str, Any], *, server_scope: bool = False) -> dict[str, Any]:
        """Validate candidate provider settings against runtime capabilities, persist, and return sanitized config."""
        if server_scope:
            cand_active = str(payload.get("active_provider") or "").strip().lower()
            if cand_active in ("antigravity", "codex"):
                raise ValueError("CLI 클라이언트(Antigravity/Codex)는 서버 공용 프로바이더 메뉴에서 선택할 수 없습니다. 관리자 개인 계정 설정(/settings/keys)에서 구성하세요.")
            gem_in = (payload.get("providers") or {}).get("gemini") if isinstance(payload.get("providers"), dict) else None
            if isinstance(gem_in, dict) and "embed_model" in gem_in:
                req_embed_model = str(gem_in.get("embed_model") or "").strip()
                if req_embed_model and req_embed_model != "gemini-embedding-001":
                    raise ValueError("서버 공용 벡터 임베딩 모델은 'gemini-embedding-001'로 고정되어 변경할 수 없습니다.")
            fr_in = payload.get("feature_routing")
            if isinstance(fr_in, dict):
                for slot_key, item in fr_in.items():
                    if isinstance(item, dict) and str(item.get("provider") or "").strip().lower() in ("antigravity", "codex"):
                        raise ValueError(f"서버 공용 Feature Routing('{slot_key}')에서는 CLI 클라이언트를 선택할 수 없습니다. 관리자 개인 계정 설정(/settings/keys)을 이용하세요.")
                    if slot_key == "embed" and isinstance(item, dict):
                        emb_prov = str(item.get("provider") or "inherit").strip().lower()
                        emb_model = str(item.get("model") or "").strip()
                        if emb_prov not in ("", "inherit") or emb_model not in ("", "gemini-embedding-001"):
                            raise ValueError("서버 공용 벡터 임베딩은 Google AI Studio의 'gemini-embedding-001'로 고정되어 프로바이더나 모델을 선택할 수 없습니다.")
        current = self.get_config()
        merged = self._merge_payload(current, payload, validate_models=True)
        caps = self._evaluate_capabilities(merged)
        act = merged.get("active_provider", "mock")
        act_cap = caps["providers"].get(act, {})
        if not act_cap.get("selectable", False):
            raise ValueError(act_cap.get("locked_reason") or f"프로바이더 '{act}'는 현재 환경에서 활성화할 수 없습니다.")

        self.save_config(merged)
        return self.get_sanitized_config()

    def get_personal_cli_config(self) -> dict[str, Any]:
        """Return sanitized personal CLI settings for the administrator's own account scope."""
        san = self.get_sanitized_config()
        pcli = copy.deepcopy(san.get("personal_cli") or DEFAULT_PROVIDERS_CONFIG["personal_cli"])
        return {
            "personal_cli": pcli,
            "providers": {
                "antigravity": san["providers"]["antigravity"],
                "codex": san["providers"]["codex"],
            },
            "capabilities": {
                "in_container": san["capabilities"]["in_container"],
                "providers": {
                    "antigravity": san["capabilities"]["providers"]["antigravity"],
                    "codex": san["capabilities"]["providers"]["codex"],
                },
            },
        }

    def update_personal_cli_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate and persist administrator personal CLI settings and return sanitized personal CLI state."""
        current = self.get_config()
        prov_patch: dict[str, Any] = {}
        if isinstance(payload.get("providers"), dict):
            for k in ("antigravity", "codex"):
                if isinstance(payload["providers"].get(k), dict):
                    prov_patch[k] = payload["providers"][k]
        merged = self._merge_payload(current, {"providers": prov_patch}, validate_models=True) if prov_patch else copy.deepcopy(current)

        pcli_cur = copy.deepcopy(merged.get("personal_cli") or DEFAULT_PROVIDERS_CONFIG["personal_cli"])
        pcli_in = payload.get("personal_cli") if isinstance(payload.get("personal_cli"), dict) else payload
        if "enabled" in pcli_in:
            pcli_cur["enabled"] = bool(pcli_in["enabled"])
        if "active_cli" in pcli_in:
            act_cli = str(pcli_in.get("active_cli") or "none").strip().lower()
            if act_cli not in ALLOWED_PERSONAL_CLI_PROVIDERS:
                raise ValueError(f"지원하지 않는 개인 CLI 클라이언트입니다: {act_cli}")
            pcli_cur["active_cli"] = act_cli
        if isinstance(pcli_in.get("feature_routing"), dict):
            fr_cur = copy.deepcopy(pcli_cur.get("feature_routing") or DEFAULT_FEATURE_ROUTING)
            for slot_key in FEATURE_SLOTS:
                raw_slot = pcli_in["feature_routing"].get(slot_key)
                if isinstance(raw_slot, dict):
                    sp = str(raw_slot.get("provider") or "inherit").strip().lower()
                    if sp not in ALLOWED_ROUTE_PROVIDERS:
                        raise ValueError(f"지원하지 않는 개인 CLI 라우팅 프로바이더입니다: {sp}")
                    fr_cur[slot_key] = {
                        "provider": sp,
                        "model": str(raw_slot.get("model") or "").strip(),
                        "effort": str(raw_slot.get("effort") or "").strip().lower(),
                    }
            pcli_cur["feature_routing"] = fr_cur

        merged["personal_cli"] = pcli_cur
        caps = self._evaluate_capabilities(merged)
        if pcli_cur.get("enabled") and pcli_cur.get("active_cli") in ("antigravity", "codex"):
            cli_name = pcli_cur["active_cli"]
            cli_cap = caps["providers"].get(cli_name, {})
            if not cli_cap.get("selectable", False):
                raise ValueError(cli_cap.get("locked_reason") or f"개인 CLI '{cli_name}'를 현재 환경에서 활성화할 수 없습니다.")

        self.save_config(merged)
        return self.get_personal_cli_config()

    def get_settings_dict(self) -> dict[str, Any]:
        """Pydantic Settings 소스에서 주입할 수 있는 필드 딕셔너리를 생성한다."""
        cfg = self.get_config()
        providers = cfg.get("providers", {})
        gemini = providers.get("gemini", {})
        agy = providers.get("antigravity", {})
        cdx = providers.get("codex", {})
        jev = providers.get("jev", {})
        stt = providers.get("stt", {})

        out: dict[str, Any] = {
            "provider": cfg.get("active_provider", "mock"),
        }

        # Gemini
        if gemini.get("api_key"):
            out["gemini_api_key"] = gemini["api_key"]
        if gemini.get("model"):
            out["gemini_model"] = gemini["model"]
        if gemini.get("effort"):
            out["gemini_effort"] = gemini["effort"]
        if gemini.get("embed_model"):
            out["gemini_embed_model"] = gemini["embed_model"]
        if gemini.get("embed_task_type"):
            out["embed_task_type"] = str(gemini["embed_task_type"])
        if gemini.get("min_interval") is not None:
            out["gemini_min_interval"] = float(gemini["min_interval"])
        if gemini.get("max_retries") is not None:
            out["gemini_max_retries"] = int(gemini["max_retries"])

        # Antigravity CLI
        if agy.get("bin"):
            out["agy_bin"] = agy["bin"]
        if agy.get("model"):
            out["agy_model"] = agy["model"]
        if agy.get("effort"):
            out["agy_effort"] = agy["effort"]
        if agy.get("timeout") is not None:
            out["agy_timeout"] = float(agy["timeout"])
        if agy.get("detail_timeout") is not None:
            out["agy_detail_timeout"] = float(agy["detail_timeout"])
        if agy.get("max_concurrency") is not None:
            out["agy_max_concurrency"] = int(agy["max_concurrency"])

        # Codex CLI
        if cdx.get("bin"):
            out["codex_bin"] = cdx["bin"]
        if cdx.get("model") is not None:
            out["codex_model"] = cdx["model"]
        if cdx.get("effort"):
            out["codex_effort"] = cdx["effort"]
        if cdx.get("timeout") is not None:
            out["codex_timeout"] = float(cdx["timeout"])
        if cdx.get("max_concurrency") is not None:
            out["codex_max_concurrency"] = int(cdx["max_concurrency"])

        # Jev
        if jev.get("enabled") is not None:
            out["enable_jev"] = bool(jev["enabled"])
        if jev.get("api_key") is not None:
            out["jev_api_key"] = jev["api_key"]
        if jev.get("base_url"):
            out["jev_base_url"] = jev["base_url"]
        if jev.get("timeout") is not None:
            out["jev_timeout"] = float(jev["timeout"])
        if jev.get("model"):
            out["jev_model"] = jev["model"]
        if jev.get("mode"):
            out["jev_mode"] = str(jev["mode"]).strip().lower()
        if jev.get("filter_threshold") is not None:
            out["jev_filter_threshold"] = float(jev["filter_threshold"])
        if jev.get("auto_merge_threshold") is not None:
            out["jev_auto_merge_threshold"] = float(jev["auto_merge_threshold"])

        out["feature_routing"] = self._normalize_feature_routing(cfg.get("feature_routing"))
        raw_pcli = cfg.get("personal_cli") if isinstance(cfg.get("personal_cli"), dict) else {}
        out["personal_cli"] = {
            "enabled": bool(raw_pcli.get("enabled", False)),
            "active_cli": str(raw_pcli.get("active_cli") or "none").strip().lower(),
            "feature_routing": self._normalize_feature_routing(raw_pcli.get("feature_routing")),
        }

        # STT
        if stt.get("enabled") is not None:
            out["enable_video_transcription"] = bool(stt["enabled"])
        if stt.get("provider"):
            out["stt_provider"] = stt["provider"]
        if stt.get("model") is not None:
            out["stt_model"] = stt["model"]
        if stt.get("language"):
            out["stt_language"] = stt["language"]
        if stt.get("timeout") is not None:
            out["stt_timeout"] = float(stt["timeout"])

        return out

    def migrate_from_env_if_needed(self, force: bool = False) -> bool:
        """기존 환경변수(.env / .env.dev 및 os.environ)의 프로바이더 설정을 providers.json으로 마이그레이션한다.

        마이그레이션 완료 후 .env 파일의 해당 변수들을 주석처리한다.
        """
        needs_migration = not self.registry_path.is_file() or force
        if not needs_migration:
            # providers.json 파일이 이미 존재하더라도 .env에 남아 있는 미주석 환경변수를 정리
            for f in self._env_files:
                comment_out_env_file(f, MIGRATED_ENV_VARS)
            return False

        # 환경변수 값 수집 (os.environ 우선, 그다음 .env.dev, .env)
        collected: dict[str, str] = {}
        for f in reversed(self._env_files):
            collected.update(_parse_env_file_values(f))
        for k in MIGRATED_ENV_VARS:
            v = os.environ.get(k)
            if v is not None and v != "":
                collected[k] = v

        cfg = copy.deepcopy(DEFAULT_PROVIDERS_CONFIG)
        provs = cfg["providers"]

        # Active provider
        if "CLAIRE_PROVIDER" in collected and collected["CLAIRE_PROVIDER"].strip():
            raw_prov = collected["CLAIRE_PROVIDER"].strip().lower()
            if raw_prov in ("codex-cli", "codex"):
                cfg["active_provider"] = "codex"
            elif raw_prov in SUPPORTED_ACTIVE_PROVIDERS:
                cfg["active_provider"] = raw_prov

        # Gemini
        if "GEMINI_API_KEY" in collected:
            provs["gemini"]["api_key"] = collected["GEMINI_API_KEY"].strip()
        if "CLAIRE_GEMINI_MODEL" in collected and collected["CLAIRE_GEMINI_MODEL"].strip():
            provs["gemini"]["model"] = collected["CLAIRE_GEMINI_MODEL"].strip()
        if "CLAIRE_GEMINI_EFFORT" in collected and collected["CLAIRE_GEMINI_EFFORT"].strip():
            provs["gemini"]["effort"] = collected["CLAIRE_GEMINI_EFFORT"].strip()
        if "CLAIRE_GEMINI_EMBED_MODEL" in collected and collected["CLAIRE_GEMINI_EMBED_MODEL"].strip():
            provs["gemini"]["embed_model"] = collected["CLAIRE_GEMINI_EMBED_MODEL"].strip()
        if "CLAIRE_GEMINI_MIN_INTERVAL" in collected:
            try:
                provs["gemini"]["min_interval"] = float(collected["CLAIRE_GEMINI_MIN_INTERVAL"])
            except ValueError:
                pass
        if "CLAIRE_GEMINI_MAX_RETRIES" in collected:
            try:
                provs["gemini"]["max_retries"] = int(collected["CLAIRE_GEMINI_MAX_RETRIES"])
            except ValueError:
                pass

        # Antigravity CLI
        if "CLAIRE_AGY_BIN" in collected and collected["CLAIRE_AGY_BIN"].strip():
            provs["antigravity"]["bin"] = collected["CLAIRE_AGY_BIN"].strip()
        if "CLAIRE_AGY_MODEL" in collected and collected["CLAIRE_AGY_MODEL"].strip():
            provs["antigravity"]["model"] = collected["CLAIRE_AGY_MODEL"].strip()
        if "CLAIRE_AGY_EFFORT" in collected and collected["CLAIRE_AGY_EFFORT"].strip():
            provs["antigravity"]["effort"] = collected["CLAIRE_AGY_EFFORT"].strip()
        if "CLAIRE_AGY_TIMEOUT" in collected:
            try:
                provs["antigravity"]["timeout"] = float(collected["CLAIRE_AGY_TIMEOUT"])
            except ValueError:
                pass
        if "CLAIRE_AGY_MAX_CONCURRENCY" in collected:
            try:
                provs["antigravity"]["max_concurrency"] = int(collected["CLAIRE_AGY_MAX_CONCURRENCY"])
            except ValueError:
                pass

        # Codex CLI
        if "CLAIRE_CODEX_BIN" in collected and collected["CLAIRE_CODEX_BIN"].strip():
            provs["codex"]["bin"] = collected["CLAIRE_CODEX_BIN"].strip()
        if "CLAIRE_CODEX_MODEL" in collected:
            provs["codex"]["model"] = collected["CLAIRE_CODEX_MODEL"].strip()
        if "CLAIRE_CODEX_EFFORT" in collected and collected["CLAIRE_CODEX_EFFORT"].strip():
            provs["codex"]["effort"] = collected["CLAIRE_CODEX_EFFORT"].strip()
        if "CLAIRE_CODEX_TIMEOUT" in collected:
            try:
                provs["codex"]["timeout"] = float(collected["CLAIRE_CODEX_TIMEOUT"])
            except ValueError:
                pass
        if "CLAIRE_CODEX_MAX_CONCURRENCY" in collected:
            try:
                provs["codex"]["max_concurrency"] = int(collected["CLAIRE_CODEX_MAX_CONCURRENCY"])
            except ValueError:
                pass

        # Jev
        if "CLAIRE_ENABLE_JEV" in collected:
            provs["jev"]["enabled"] = collected["CLAIRE_ENABLE_JEV"].strip().lower() in ("1", "true", "yes", "on")
        if "CLAIRE_JEV_API_KEY" in collected:
            provs["jev"]["api_key"] = collected["CLAIRE_JEV_API_KEY"].strip()
        if "CLAIRE_JEV_BASE_URL" in collected and collected["CLAIRE_JEV_BASE_URL"].strip():
            provs["jev"]["base_url"] = collected["CLAIRE_JEV_BASE_URL"].strip()
        if "CLAIRE_JEV_TIMEOUT" in collected:
            try:
                provs["jev"]["timeout"] = float(collected["CLAIRE_JEV_TIMEOUT"])
            except ValueError:
                pass
        if "CLAIRE_JEV_MODEL" in collected and collected["CLAIRE_JEV_MODEL"].strip():
            provs["jev"]["model"] = collected["CLAIRE_JEV_MODEL"].strip()

        # STT
        if "CLAIRE_ENABLE_VIDEO_TRANSCRIPTION" in collected:
            provs["stt"]["enabled"] = collected["CLAIRE_ENABLE_VIDEO_TRANSCRIPTION"].strip().lower() in (
                "1", "true", "yes", "on"
            )
        if "CLAIRE_STT_PROVIDER" in collected and collected["CLAIRE_STT_PROVIDER"].strip():
            provs["stt"]["provider"] = collected["CLAIRE_STT_PROVIDER"].strip()
        if "CLAIRE_STT_MODEL" in collected:
            provs["stt"]["model"] = collected["CLAIRE_STT_MODEL"].strip()
        if "CLAIRE_STT_LANGUAGE" in collected and collected["CLAIRE_STT_LANGUAGE"].strip():
            provs["stt"]["language"] = collected["CLAIRE_STT_LANGUAGE"].strip()
        if "CLAIRE_STT_TIMEOUT" in collected:
            try:
                provs["stt"]["timeout"] = float(collected["CLAIRE_STT_TIMEOUT"])
            except ValueError:
                pass

        # 파일 저장
        self.save_config(cfg)
        log.info("Migrated provider settings to %s", self.registry_path)

        # env 파일의 해당 변수들을 주석처리
        for f in self._env_files:
            comment_out_env_file(f, MIGRATED_ENV_VARS)

        return True

    def test_connection(
        self, provider_name: str, config: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """선택한 프로바이더의 연결 또는 CLI 실행 가능 상태를 테스트한다."""
        p_name = provider_name.strip().lower()
        cfg = self.get_config()
        provs = cfg.get("providers", {})
        override = config or {}

        if p_name == "mock":
            return {
                "ok": True,
                "provider": "mock",
                "message": "Mock 프로바이더가 활성화되어 있습니다 (외부 네트워크 호출 없음).",
            }

        if p_name == "gemini":
            gem_cfg = provs.get("gemini", {})
            api_key = override.get("api_key") or gem_cfg.get("api_key") or ""
            if api_key == "••••••••":
                api_key = gem_cfg.get("api_key", "")
            if not api_key:
                return {
                    "ok": False,
                    "provider": "gemini",
                    "error": "Google AI Studio API 키가 입력되지 않았습니다.",
                }
            model_name = override.get("model") or gem_cfg.get("model") or "gemini-3.8-flash-medium"
            disc = discover_gemini_models(api_key, force_refresh=True)
            ok, total_tokens, err = _probe_gemini_model(api_key, model_name)
            if ok:
                return {
                    "ok": True,
                    "provider": "gemini",
                    "message": f"Google AI Studio API 확인 성공 (모델: {model_name}, 조회 모델 {len(disc['models'])}개)",
                    "details": {
                        "total_tokens": total_tokens,
                        "models": disc["models"],
                        "embed_models": disc["embed_models"],
                        "models_source": disc["source"],
                        "models_error": disc.get("error"),
                    },
                }
            return {
                "ok": False,
                "provider": "gemini",
                "error": f"Google AI Studio API 연결 실패: {err}",
                "details": {
                    "models": disc["models"],
                    "embed_models": disc["embed_models"],
                    "models_source": disc["source"],
                },
            }

        if p_name == "antigravity":
            agy_cfg = provs.get("antigravity", {})
            bin_name = override.get("bin") or agy_cfg.get("bin") or "agy"
            exe = find_agy_executable(bin_name)
            if exe:
                diag = diagnose_agy_environment(bin_name)
                disc = discover_agy_models(bin_name, force_refresh=True)
                models = disc["models"]
                ver_text = f"v{disc['cli_version']}" if disc.get("cli_version") else "버전 확인 불가"
                if disc["source"] == "cli" and models:
                    return {
                        "ok": True,
                        "provider": "antigravity",
                        "message": f"Antigravity CLI 감지 성공: {exe} ({ver_text}, 모델 {len(models)}개 조회)",
                        "details": {**diag, "models": models, "models_source": disc["source"], "cli_version": disc["cli_version"], "models_error": disc["error"]},
                    }
                return {
                    "ok": False,
                    "provider": "antigravity",
                    "error": f"Antigravity CLI ({ver_text})에서 동적 모델 조회(agy models)에 실패했습니다: {disc.get('error') or '빈 응답'}",
                    "details": {**diag, "models": [], "models_source": disc["source"], "cli_version": disc["cli_version"], "models_error": disc["error"]},
                }
            return {
                "ok": False,
                "provider": "antigravity",
                "error": f"Antigravity CLI 바이너리('{bin_name}')를 찾을 수 없습니다.",
            }

        if p_name == "codex":
            if is_running_in_container():
                return {
                    "ok": False,
                    "provider": "codex",
                    "error": "Codex CLI는 호스트 네이티브 전용이므로 Docker/컨테이너 환경에서는 사용할 수 없습니다.",
                }
            cdx_cfg = provs.get("codex", {})
            bin_name = override.get("bin") or cdx_cfg.get("bin") or "codex"
            exe = find_codex_executable(bin_name)
            if exe:
                return {
                    "ok": True,
                    "provider": "codex",
                    "message": f"Codex CLI 감지 성공: {exe} (호스트 네이티브 전용)",
                }
            return {
                "ok": False,
                "provider": "codex",
                "error": f"Codex CLI 바이너리('{bin_name}')를 찾을 수 없습니다.",
            }

        if p_name == "openai":
            return {
                "ok": False,
                "provider": "openai",
                "error": "OpenAI 호환 LLM/임베딩 백엔드가 아직 구현되어 있지 않아 사용할 수 없습니다.",
            }

        return {
            "ok": False,
            "provider": p_name,
            "error": f"테스트를 지원하지 않는 프로바이더입니다: '{p_name}'",
        }


_PROVIDER_MANAGERS: dict[str, ProviderManager] = {}


def get_provider_manager(
    data_dir: str | Path | None = None,
    *,
    auto_migrate: bool = True,
    env_files: list[Path] | None = None,
) -> ProviderManager:
    """ProviderManager 싱글톤 인스턴스 팩토리."""
    key = str(Path(data_dir).resolve()) if data_dir is not None else "default"
    if key not in _PROVIDER_MANAGERS:
        _PROVIDER_MANAGERS[key] = ProviderManager(
            data_dir=data_dir,
            auto_migrate=auto_migrate,
            env_files=env_files,
        )
    return _PROVIDER_MANAGERS[key]
