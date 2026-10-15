"""상황 별 적재 제한 설정 관리자.

환경변수(.env)에 의존하던 상황 별 적재 제한 설정(글자 수 예산, PDF 추출 및 논문 판정 기준,
슬라이싱 전략, 임베딩/확장/리서치 예산, 미디어 용량 상한 등)을
WebUI에서 관리하는 영구 파일(data/ingest_limits.json)로 전환하고,
기존 환경변수 설정을 자동 마이그레이션한 뒤 env 파일의 해당 항목 선언 사용을 종료(주석처리)한다.
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

from .config import ROOT

log = logging.getLogger("claire.ingest_limits")

INGEST_LIMITS_CONFIG_FILENAME = "ingest_limits.json"

DEFAULT_INGEST_LIMITS_CONFIG: dict[str, Any] = {
    "version": 1,
    "limits": {
        # 1. 일반 웹 / 텍스트 수집 상황
        "raw_char_budget": 20000,
        # 2. PDF 수집 및 학술 논문 처리 상황
        "pdf_max_extract_chars": 50000,
        "pdf_paper_threshold_chars": 15000,
        "pdf_exclude_appendix": True,
        "pdf_exclude_references": True,
        "pdf_parser": "default",
        "pdf_detect_encoding_flaws": True,
        "pdf_flaw_escalate_docling": True,
        "pdf_paper_effort": "high",
        "pdf_default_effort": "",
        "pdf_classifier_effort": "low",
        # 3. 프롬프트 KG 추출 및 슬라이싱 상황
        "extract_char_budget": 20000,
        "merged_extract_char_budget": 0,
        "slicing_strategy": "table-exemption",
        # 4. 임베딩 벡터 생성 상황
        "embed_char_budget": 8000,
        # 5. 1홉 확장 탐색 상황
        "expand_char_budget": 2000,
        "expand_max": 5,
        # 6. 심층 리서치 상황
        "research_context_budget": 8000,
        # 7. 미디어 / 프레젠테이션 / 비디오 수집 상황
        "presentation_pdf_max_bytes": 67108864,
        "video_max_extract_chars": 200000,
        "video_chunk_duration_sec": 240,
        "vlm_max_images": 20,
    },
    "updated_at": 0.0,
}

MIGRATED_LIMIT_ENV_VARS: set[str] = {
    "CLAIRE_RAW_CHAR_BUDGET",
    "CLAIRE_PDF_MAX_EXTRACT_CHARS",
    "CLAIRE_PDF_PAPER_THRESHOLD_CHARS",
    "CLAIRE_PDF_EXCLUDE_APPENDIX",
    "CLAIRE_PDF_EXCLUDE_REFERENCES",
    "CLAIRE_PDF_PARSER",
    "CLAIRE_PDF_DETECT_ENCODING_FLAWS",
    "CLAIRE_PDF_FLAW_ESCALATE_DOCLING",
    "CLAIRE_PDF_PAPER_EFFORT",
    "CLAIRE_PDF_DEFAULT_EFFORT",
    "CLAIRE_PDF_CLASSIFIER_EFFORT",
    "CLAIRE_EXTRACT_CHAR_BUDGET",
    "CLAIRE_MERGED_EXTRACT_CHAR_BUDGET",
    "CLAIRE_SLICING_STRATEGY",
    "CLAIRE_EMBED_CHAR_BUDGET",
    "CLAIRE_EXPAND_CHAR_BUDGET",
    "CLAIRE_EXPAND_MAX",
    "CLAIRE_RESEARCH_CONTEXT_BUDGET",
    "CLAIRE_PRESENTATION_PDF_MAX_BYTES",
    "CLAIRE_VIDEO_MAX_EXTRACT_CHARS",
    "CLAIRE_VIDEO_CHUNK_DURATION_SEC",
    "CLAIRE_VLM_MAX_IMAGES",
}

VALID_SLICING_STRATEGIES = ("table-exemption", "strict")
VALID_PDF_PARSERS = ("default", "docling")
VALID_EFFORTS = ("low", "medium", "high")


def comment_out_env_file_limits(file_path: Path, keys_to_comment: set[str]) -> int:
    """주어진 env 파일에서 keys_to_comment에 해당하는 활성 환경변수 할당 라인을 주석처리한다."""
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
                    f"# Migrated to {INGEST_LIMITS_CONFIG_FILENAME}{endl}"
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


class IngestLimitManager:
    """data/ingest_limits.json 파일 기반의 상황 별 적재 제한 설정 관리자."""

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

        self.registry_path = self.data_dir / INGEST_LIMITS_CONFIG_FILENAME
        self._env_files = env_files or [ROOT / ".env", ROOT / ".env.dev"]
        if auto_migrate:
            self.migrate_from_env_if_needed()

    def get_config(self) -> dict[str, Any]:
        """디스크에서 ingest_limits.json 설정을 로드하거나 기본값을 반환한다."""
        if not self.registry_path.is_file():
            return copy.deepcopy(DEFAULT_INGEST_LIMITS_CONFIG)

        try:
            raw = json.loads(self.registry_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return copy.deepcopy(DEFAULT_INGEST_LIMITS_CONFIG)

            cfg = copy.deepcopy(DEFAULT_INGEST_LIMITS_CONFIG)
            cfg["version"] = raw.get("version", 1)
            cfg["updated_at"] = raw.get("updated_at", 0.0)

            raw_limits = raw.get("limits", {})
            if isinstance(raw_limits, dict):
                for k, v in raw_limits.items():
                    if k in cfg["limits"]:
                        cfg["limits"][k] = v

            return cfg
        except Exception as exc:
            log.warning("Failed to load %s: %s. Using default limits config.", self.registry_path, exc)
            return copy.deepcopy(DEFAULT_INGEST_LIMITS_CONFIG)

    def save_config(self, config: dict[str, Any]) -> None:
        """ingest_limits.json 설정을 원자적(atomic write)으로 저장한다."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        config["updated_at"] = time.time()
        raw_json = json.dumps(config, ensure_ascii=False, indent=2)
        with tempfile.NamedTemporaryFile("w", dir=self.data_dir, delete=False, encoding="utf-8") as tf:
            tf.write(raw_json)
            tmp_name = tf.name
        os.replace(tmp_name, self.registry_path)
        log.info("Saved ingest limits configuration to %s", self.registry_path)

    def get_sanitized_config(self) -> dict[str, Any]:
        """WebUI 전달용으로 정제된 설정 딕셔너리를 반환한다."""
        return self.get_config()

    def update_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        """WebUI 또는 API 입력 정보를 바탕으로 적재 제한 설정을 검증 후 갱신/저장한다."""
        current = self.get_config()
        curr_limits = current.setdefault("limits", {})

        incoming_limits = payload.get("limits") if "limits" in payload and isinstance(payload["limits"], dict) else payload

        int_non_negative_fields = (
            "raw_char_budget",
            "pdf_max_extract_chars",
            "pdf_paper_threshold_chars",
            "extract_char_budget",
            "merged_extract_char_budget",
            "embed_char_budget",
            "expand_char_budget",
            "expand_max",
            "research_context_budget",
            "video_max_extract_chars",
            "video_chunk_duration_sec",
            "vlm_max_images",
        )

        for f in int_non_negative_fields:
            if f in incoming_limits:
                try:
                    val = int(incoming_limits[f])
                    if val < 0:
                        raise ValueError(f"{f} must be greater than or equal to 0")
                    curr_limits[f] = val
                except (TypeError, ValueError) as err:
                    raise ValueError(f"Invalid integer value for {f}: {err}") from err

        if "presentation_pdf_max_bytes" in incoming_limits:
            try:
                val = int(incoming_limits["presentation_pdf_max_bytes"])
                if val <= 0:
                    raise ValueError("presentation_pdf_max_bytes must be greater than 0")
                curr_limits["presentation_pdf_max_bytes"] = val
            except (TypeError, ValueError) as err:
                raise ValueError(f"Invalid value for presentation_pdf_max_bytes: {err}") from err

        if "slicing_strategy" in incoming_limits:
            strat = str(incoming_limits["slicing_strategy"]).strip().lower()
            if strat not in VALID_SLICING_STRATEGIES:
                raise ValueError(
                    f"slicing_strategy must be one of {VALID_SLICING_STRATEGIES}, got '{strat}'"
                )
            curr_limits["slicing_strategy"] = strat

        if "pdf_parser" in incoming_limits:
            parser = str(incoming_limits["pdf_parser"]).strip().lower()
            if parser not in VALID_PDF_PARSERS:
                raise ValueError(
                    f"pdf_parser must be one of {VALID_PDF_PARSERS}, got '{parser}'"
                )
            curr_limits["pdf_parser"] = parser

        if "pdf_paper_effort" in incoming_limits:
            eff = str(incoming_limits["pdf_paper_effort"]).strip().lower()
            if eff not in VALID_EFFORTS:
                raise ValueError(f"pdf_paper_effort must be one of {VALID_EFFORTS}, got '{eff}'")
            curr_limits["pdf_paper_effort"] = eff

        if "pdf_classifier_effort" in incoming_limits:
            eff = str(incoming_limits["pdf_classifier_effort"]).strip().lower()
            if eff not in VALID_EFFORTS:
                raise ValueError(f"pdf_classifier_effort must be one of {VALID_EFFORTS}, got '{eff}'")
            curr_limits["pdf_classifier_effort"] = eff

        if "pdf_default_effort" in incoming_limits:
            raw_eff = incoming_limits["pdf_default_effort"]
            eff = str(raw_eff or "").strip().lower()
            if eff and eff not in VALID_EFFORTS:
                raise ValueError(f"pdf_default_effort must be empty or one of {VALID_EFFORTS}, got '{eff}'")
            curr_limits["pdf_default_effort"] = eff

        bool_fields = (
            "pdf_exclude_appendix",
            "pdf_exclude_references",
            "pdf_detect_encoding_flaws",
            "pdf_flaw_escalate_docling",
        )
        for bf in bool_fields:
            if bf in incoming_limits:
                raw_b = incoming_limits[bf]
                if isinstance(raw_b, bool):
                    curr_limits[bf] = raw_b
                else:
                    curr_limits[bf] = str(raw_b).strip().lower() in ("1", "true", "yes", "on")

        self.save_config(current)
        return self.get_sanitized_config()

    def get_settings_dict(self) -> dict[str, Any]:
        """Pydantic Settings 소스에서 주입할 수 있는 필드 딕셔너리를 생성한다."""
        cfg = self.get_config()
        limits = cfg.get("limits", {})
        return dict(limits)

    def migrate_from_env_if_needed(self, force: bool = False) -> bool:
        """기존 환경변수(.env / .env.dev 및 os.environ)의 적재 제한 설정을 ingest_limits.json으로 마이그레이션한다.

        마이그레이션 완료 후 .env 파일의 해당 변수 선언을 주석처리하여 사용을 종료한다.
        """
        needs_migration = not self.registry_path.is_file() or force
        if not needs_migration:
            for f in self._env_files:
                comment_out_env_file_limits(f, MIGRATED_LIMIT_ENV_VARS)
            return False

        collected: dict[str, str] = {}
        for f in reversed(self._env_files):
            collected.update(_parse_env_file_values(f))
        for k in MIGRATED_LIMIT_ENV_VARS:
            v = os.environ.get(k)
            if v is not None and v != "":
                collected[k] = v

        cfg = copy.deepcopy(DEFAULT_INGEST_LIMITS_CONFIG)
        lims = cfg["limits"]

        int_mappings = {
            "CLAIRE_RAW_CHAR_BUDGET": "raw_char_budget",
            "CLAIRE_PDF_MAX_EXTRACT_CHARS": "pdf_max_extract_chars",
            "CLAIRE_PDF_PAPER_THRESHOLD_CHARS": "pdf_paper_threshold_chars",
            "CLAIRE_EXTRACT_CHAR_BUDGET": "extract_char_budget",
            "CLAIRE_MERGED_EXTRACT_CHAR_BUDGET": "merged_extract_char_budget",
            "CLAIRE_EMBED_CHAR_BUDGET": "embed_char_budget",
            "CLAIRE_EXPAND_CHAR_BUDGET": "expand_char_budget",
            "CLAIRE_EXPAND_MAX": "expand_max",
            "CLAIRE_RESEARCH_CONTEXT_BUDGET": "research_context_budget",
            "CLAIRE_PRESENTATION_PDF_MAX_BYTES": "presentation_pdf_max_bytes",
            "CLAIRE_VIDEO_MAX_EXTRACT_CHARS": "video_max_extract_chars",
            "CLAIRE_VIDEO_CHUNK_DURATION_SEC": "video_chunk_duration_sec",
            "CLAIRE_VLM_MAX_IMAGES": "vlm_max_images",
        }
        for env_k, field_k in int_mappings.items():
            if env_k in collected and collected[env_k].strip():
                try:
                    lims[field_k] = int(collected[env_k].strip())
                except ValueError:
                    pass

        bool_mappings = {
            "CLAIRE_PDF_EXCLUDE_APPENDIX": "pdf_exclude_appendix",
            "CLAIRE_PDF_EXCLUDE_REFERENCES": "pdf_exclude_references",
            "CLAIRE_PDF_DETECT_ENCODING_FLAWS": "pdf_detect_encoding_flaws",
            "CLAIRE_PDF_FLAW_ESCALATE_DOCLING": "pdf_flaw_escalate_docling",
        }
        for env_k, field_k in bool_mappings.items():
            if env_k in collected and collected[env_k].strip():
                lims[field_k] = collected[env_k].strip().lower() in ("1", "true", "yes", "on")

        if "CLAIRE_PDF_PARSER" in collected and collected["CLAIRE_PDF_PARSER"].strip():
            p = collected["CLAIRE_PDF_PARSER"].strip().lower()
            if p in VALID_PDF_PARSERS:
                lims["pdf_parser"] = p

        if "CLAIRE_SLICING_STRATEGY" in collected and collected["CLAIRE_SLICING_STRATEGY"].strip():
            s = collected["CLAIRE_SLICING_STRATEGY"].strip().lower()
            if s in VALID_SLICING_STRATEGIES:
                lims["slicing_strategy"] = s

        if "CLAIRE_PDF_PAPER_EFFORT" in collected and collected["CLAIRE_PDF_PAPER_EFFORT"].strip():
            eff = collected["CLAIRE_PDF_PAPER_EFFORT"].strip().lower()
            if eff in VALID_EFFORTS:
                lims["pdf_paper_effort"] = eff

        if "CLAIRE_PDF_DEFAULT_EFFORT" in collected:
            eff = collected["CLAIRE_PDF_DEFAULT_EFFORT"].strip().lower()
            if eff in VALID_EFFORTS or eff == "":
                lims["pdf_default_effort"] = eff

        if "CLAIRE_PDF_CLASSIFIER_EFFORT" in collected and collected["CLAIRE_PDF_CLASSIFIER_EFFORT"].strip():
            eff = collected["CLAIRE_PDF_CLASSIFIER_EFFORT"].strip().lower()
            if eff in VALID_EFFORTS:
                lims["pdf_classifier_effort"] = eff

        self.save_config(cfg)
        for f in self._env_files:
            comment_out_env_file_limits(f, MIGRATED_LIMIT_ENV_VARS)
        return True


_INGEST_LIMIT_MANAGERS: dict[str, IngestLimitManager] = {}


def get_ingest_limit_manager(data_dir: str | Path | None = None) -> IngestLimitManager:
    """data_dir 경로별 IngestLimitManager 인스턴스를 반환한다."""
    key = str(Path(data_dir).resolve()) if data_dir else "default"
    if key not in _INGEST_LIMIT_MANAGERS:
        _INGEST_LIMIT_MANAGERS[key] = IngestLimitManager(data_dir=data_dir)
    return _INGEST_LIMIT_MANAGERS[key]
