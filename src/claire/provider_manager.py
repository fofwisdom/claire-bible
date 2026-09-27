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
import tempfile
import time
from pathlib import Path
from typing import Any

from .config import (
    ROOT,
    diagnose_agy_environment,
    find_agy_executable,
    find_codex_executable,
)

log = logging.getLogger("claire.provider")

PROVIDERS_CONFIG_FILENAME = "providers.json"

DEFAULT_PROVIDERS_CONFIG: dict[str, Any] = {
    "version": 1,
    "active_provider": "mock",
    "providers": {
        "gemini": {
            "api_key": "",
            "model": "gemini-3.1-flash-lite",
            "effort": "medium",
            "embed_model": "gemini-embedding-001",
            "min_interval": 4.0,
            "max_retries": 5,
        },
        "antigravity": {
            "bin": "agy",
            "model": "gemini-3.7-flash",
            "effort": "medium",
            "timeout": 120.0,
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
        },
        "stt": {
            "enabled": True,
            "provider": "gemini",
            "model": "",
            "language": "ko",
            "timeout": 600.0,
        },
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
    # STT
    "CLAIRE_ENABLE_VIDEO_TRANSCRIPTION",
    "CLAIRE_STT_PROVIDER",
    "CLAIRE_STT_MODEL",
    "CLAIRE_STT_LANGUAGE",
    "CLAIRE_STT_TIMEOUT",
}

SUPPORTED_ACTIVE_PROVIDERS = ("mock", "gemini", "antigravity", "codex", "openai")


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

        # Gemini
        if "gemini" in providers:
            g = providers["gemini"]
            key = str(g.get("api_key") or "")
            g["has_api_key"] = bool(key.strip())
            g["api_key"] = "••••••••" if g["has_api_key"] else ""

        # Antigravity CLI
        if "antigravity" in providers:
            agy = providers["antigravity"]
            bin_name = str(agy.get("bin") or "agy")
            agy["installed"] = find_agy_executable(bin_name) is not None

        # Codex CLI
        if "codex" in providers:
            cdx = providers["codex"]
            bin_name = str(cdx.get("bin") or "codex")
            cdx["installed"] = find_codex_executable(bin_name) is not None

        # OpenAI
        if "openai" in providers:
            oai = providers["openai"]
            key = str(oai.get("api_key") or "")
            oai["has_api_key"] = bool(key.strip())
            oai["api_key"] = "••••••••" if oai["has_api_key"] else ""

        # Jev
        if "jev" in providers:
            jev = providers["jev"]
            key = str(jev.get("api_key") or "")
            jev["has_api_key"] = bool(key.strip())
            jev["api_key"] = "••••••••" if jev["has_api_key"] else ""

        return out

    def update_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        """WebUI 입력 정보를 바탕으로 설정을 갱신하고 저장한다.

        마스킹 문자열('••••••••')이나 빈 값으로 들어온 기존 비밀키는 기존 값을 보존한다.
        """
        current = self.get_config()
        curr_providers = current.setdefault("providers", {})

        # active_provider 갱신
        if "active_provider" in payload:
            act = str(payload["active_provider"]).strip().lower()
            if act not in SUPPORTED_ACTIVE_PROVIDERS:
                raise ValueError(
                    f"지원되지 않는 active_provider 입니다: '{act}'. "
                    f"지원 목록: {', '.join(SUPPORTED_ACTIVE_PROVIDERS)}"
                )
            current["active_provider"] = act

        incoming_providers = payload.get("providers", {})
        if isinstance(incoming_providers, dict):
            for name, inc_data in incoming_providers.items():
                if not isinstance(inc_data, dict):
                    continue
                target = curr_providers.setdefault(name, {})

                # 비밀키 보존 처리 (api_key)
                if "api_key" in inc_data:
                    raw_key = str(inc_data["api_key"]).strip()
                    if raw_key and raw_key != "••••••••":
                        target["api_key"] = raw_key
                    elif not raw_key and "api_key" in target and inc_data.get("clear_api_key"):
                        target["api_key"] = ""
                
                # 나머지 일반 필드 갱신
                for k, v in inc_data.items():
                    if k in ("api_key", "has_api_key", "clear_api_key", "installed"):
                        continue
                    target[k] = v

        self.save_config(current)
        return self.get_sanitized_config()

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
                    "error": "Gemini API 키가 입력되지 않았습니다.",
                }
            try:
                from google import genai
                from google.genai import types

                client = genai.Client(api_key=api_key)
                model_name = override.get("model") or gem_cfg.get("model") or "gemini-3.1-flash-lite"
                # 간단한 토큰 카운트 또는 텍스트 확인으로 키 유효성 검증
                res = client.models.count_tokens(
                    model=model_name,
                    contents="ping",
                )
                return {
                    "ok": True,
                    "provider": "gemini",
                    "message": f"Gemini API 연결 확인 성공 (모델: {model_name}, 토큰 검증 통과)",
                    "details": {"total_tokens": res.total_tokens},
                }
            except Exception as exc:
                return {
                    "ok": False,
                    "provider": "gemini",
                    "error": f"Gemini API 연결 실패: {exc}",
                }

        if p_name == "antigravity":
            agy_cfg = provs.get("antigravity", {})
            bin_name = override.get("bin") or agy_cfg.get("bin") or "agy"
            exe = find_agy_executable(bin_name)
            if exe:
                diag = diagnose_agy_environment(bin_name)
                return {
                    "ok": True,
                    "provider": "antigravity",
                    "message": f"Antigravity CLI 감지 성공: {exe}",
                    "details": diag,
                }
            return {
                "ok": False,
                "provider": "antigravity",
                "error": f"Antigravity CLI 바이너리('{bin_name}')를 찾을 수 없습니다.",
            }

        if p_name == "codex":
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
            oai_cfg = provs.get("openai", {})
            api_key = override.get("api_key") or oai_cfg.get("api_key") or ""
            if api_key == "••••••••":
                api_key = oai_cfg.get("api_key", "")
            if not api_key:
                return {
                    "ok": False,
                    "provider": "openai",
                    "error": "OpenAI API 키가 입력되지 않았습니다.",
                }
            return {
                "ok": True,
                "provider": "openai",
                "message": "OpenAI 호환 API 설정 형식이 올바릅니다.",
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
