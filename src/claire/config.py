"""중앙 설정. .env 또는 환경변수에서 로드한다.

Gemini 키가 없으면 provider 는 자동으로 mock 으로 떨어진다(M0~M1 선행 개발용).
"""

from __future__ import annotations

import os
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, DotEnvSettingsSource, SettingsConfigDict

# 프로젝트 루트 (이 파일 기준 src/claire/config.py -> 루트는 parents[2])
ROOT = Path(__file__).resolve().parents[2]
_ANONYMOUS_READONLY_ENV = "CLAIRE_ANONYMOUS_READONLY"


def _validate_anonymous_readonly_dotenv(path: Path, *, encoding: str) -> None:
    """dotenv parser가 공백/따옴표를 정규화하기 전에 exact 0|1을 검사한다."""

    matches = 0
    for lineno, original in enumerate(
        path.read_text(encoding=encoding).splitlines(),
        start=1,
    ):
        line = original.lstrip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, raw = line.split("=", 1)
        if key.strip() != _ANONYMOUS_READONLY_ENV:
            continue
        matches += 1
        if raw not in {"0", "1"}:
            raise ValueError(
                f"{path}:{lineno}: {_ANONYMOUS_READONLY_ENV} must be exactly "
                "0 or 1 without quotes or outer whitespace"
            )
    if matches > 1:
        raise ValueError(f"{path}: duplicate {_ANONYMOUS_READONLY_ENV}")


class _ExactDotEnvSettingsSource(DotEnvSettingsSource):
    """보안 selector의 dotenv 원문 계약을 보존하는 settings source."""

    def _read_env_files(self) -> dict[str, str | None]:
        env_files: Any = self.env_file
        if env_files is None:
            return {}
        if isinstance(env_files, (str, Path)):
            env_files = (env_files,)
        for raw_path in env_files:
            path = Path(raw_path).expanduser()
            if path.is_file():
                _validate_anonymous_readonly_dotenv(
                    path,
                    encoding=self.env_file_encoding or "utf-8",
                )
        return dict(super()._read_env_files())


def find_agy_executable(agy_bin: str = "agy") -> str | None:
    """Find agy executable in PATH or standard container/host locations."""
    if not agy_bin:
        agy_bin = "agy"
    if Path(agy_bin).is_file() and os.access(agy_bin, os.X_OK):
        return str(Path(agy_bin).resolve())
    found = shutil.which(agy_bin)
    if found:
        return found
    extra_dirs = [
        "/host-bin",
        "/usr/local/bin",
        "/usr/bin",
        str(Path.home() / ".local" / "bin"),
        "/root/.local/bin",
    ]
    for d in extra_dirs:
        cand = Path(d) / agy_bin
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand.resolve())
    return None


def find_codex_executable(codex_bin: str = "codex") -> str | None:
    """명시 경로 또는 현재 PATH에서만 Codex CLI를 찾는다."""
    raw = str(codex_bin or "codex").strip() or "codex"
    explicit = Path(raw).expanduser()
    if explicit.is_file() and os.access(explicit, os.X_OK):
        return str(explicit.resolve())
    found = shutil.which(raw)
    return str(Path(found).resolve()) if found else None


def find_ffmpeg_executable(ffmpeg_bin: str = "ffmpeg") -> str | None:
    """Find ffmpeg executable in PATH or standard locations."""
    if not ffmpeg_bin:
        ffmpeg_bin = "ffmpeg"
    raw = str(ffmpeg_bin).strip()
    explicit = Path(raw).expanduser()
    if explicit.is_file() and os.access(explicit, os.X_OK):
        return str(explicit.resolve())
    found = shutil.which(raw)
    if found:
        return str(Path(found).resolve())
    extra_dirs = [
        "/usr/bin",
        "/usr/local/bin",
        "/host-bin",
        str(Path.home() / ".local" / "bin"),
        "/root/.local/bin",
    ]
    for d in extra_dirs:
        cand = Path(d) / raw
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand.resolve())
    try:
        import static_ffmpeg
        static_ffmpeg.add_paths()
        found = shutil.which(raw)
        if found:
            return str(Path(found).resolve())
    except Exception:
        pass
    return None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        env_prefix="",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        strict_dotenv = _ExactDotEnvSettingsSource(
            settings_cls,
            env_file=dotenv_settings.env_file,
            env_file_encoding=dotenv_settings.env_file_encoding,
        )
        return (
            init_settings,
            env_settings,
            strict_dotenv,
            file_secret_settings,
        )

    # --- secrets ---
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    allowed_users: str = Field(default="", alias="CLAIRE_ALLOWED_USERS")
    # 운영 경보를 받을 소유자 chat. 미설정(0)이면 allowed_users 로 폴백(개인 DM 은
    # chat_id == user_id 이므로 단일 사용자 환경에서 별도 설정 없이 동작).
    owner_chat_id: int = Field(default=0, alias="CLAIRE_OWNER_CHAT_ID")

    # --- provider ---
    provider: str = Field(default="mock", alias="CLAIRE_PROVIDER")
    gemini_model: str = Field(default="gemini-3.1-flash-lite", alias="CLAIRE_GEMINI_MODEL")
    gemini_effort: str = Field(default="medium", alias="CLAIRE_GEMINI_EFFORT")
    gemini_embed_model: str = Field(
        default="gemini-embedding-001", alias="CLAIRE_GEMINI_EMBED_MODEL"
    )
    # rate limit 보호: 호출 간 최소 간격(초) + 429/5xx 재시도 횟수.
    gemini_min_interval: float = Field(default=4.0, alias="CLAIRE_GEMINI_MIN_INTERVAL")
    gemini_max_retries: int = Field(default=5, alias="CLAIRE_GEMINI_MAX_RETRIES")

    # --- Antigravity CLI (agy) ---
    agy_bin: str = Field(default="agy", alias="CLAIRE_AGY_BIN")
    agy_model: str = Field(default="gemini-3.7-flash", alias="CLAIRE_AGY_MODEL")
    agy_effort: str = Field(default="medium", alias="CLAIRE_AGY_EFFORT")
    agy_timeout: float = Field(default=120.0, alias="CLAIRE_AGY_TIMEOUT")
    agy_max_concurrency: int = Field(default=2, alias="CLAIRE_AGY_MAX_CONCURRENCY")

    # --- Codex CLI (native host only) ---
    codex_bin: str = Field(default="codex", alias="CLAIRE_CODEX_BIN")
    codex_model: str = Field(default="", alias="CLAIRE_CODEX_MODEL")
    codex_effort: str = Field(default="medium", alias="CLAIRE_CODEX_EFFORT")
    codex_timeout: float = Field(default=300.0, alias="CLAIRE_CODEX_TIMEOUT")
    codex_max_concurrency: int = Field(
        default=1, alias="CLAIRE_CODEX_MAX_CONCURRENCY"
    )

    # --- Video & Audio Transcription (STT) ---
    enable_video_transcription: bool = Field(
        default=True, alias="CLAIRE_ENABLE_VIDEO_TRANSCRIPTION"
    )
    stt_provider: str = Field(default="gemini", alias="CLAIRE_STT_PROVIDER")
    stt_model: str = Field(default="", alias="CLAIRE_STT_MODEL")
    stt_language: str = Field(default="ko", alias="CLAIRE_STT_LANGUAGE")
    stt_timeout: float = Field(default=600.0, alias="CLAIRE_STT_TIMEOUT")
    video_chunk_duration_sec: int = Field(
        default=240, alias="CLAIRE_VIDEO_CHUNK_DURATION_SEC"
    )
    video_max_extract_chars: int = Field(
        default=200000, alias="CLAIRE_VIDEO_MAX_EXTRACT_CHARS"
    )
    stt_custom_vocabulary: list[str] = Field(
        default_factory=list, alias="CLAIRE_STT_CUSTOM_VOCABULARY"
    )
    ffmpeg_bin: str = Field(default="ffmpeg", alias="CLAIRE_FFMPEG_BIN")
    ytdlp_extractor_args: str = Field(
        default="generic:impersonate", alias="CLAIRE_YTDLP_EXTRACTOR_ARGS"
    )
    video_cache_ttl_sec: int = Field(
        default=259200, alias="CLAIRE_VIDEO_CACHE_TTL_SEC"
    )  # 3 days (사흘)
    presentation_pdf_max_bytes: int = Field(
        default=64 * 1024 * 1024,
        alias="CLAIRE_PRESENTATION_PDF_MAX_BYTES",
        gt=0,
    )

    # --- storage ---
    db_path: str = Field(default="data/claire.db", alias="CLAIRE_DB_PATH")
    vault_path: str = Field(default="vault", alias="CLAIRE_VAULT_PATH")
    vector_backend: str = Field(default="auto", alias="CLAIRE_VECTOR_BACKEND")
    # 읽기/가독 렌더링 포맷 (md: Markdown, adoc: AsciiDoc). 기본값 adoc.
    render_format: str = Field(default="adoc", alias="CLAIRE_RENDER_FORMAT")
    # 데이터 수명주기 정책: append-only (기본값) | purgeable (또는 mutable).
    data_lifecycle: str = Field(default="append-only", alias="CLAIRE_DATA_LIFECYCLE")
    # 명시적 소각 허용 플래그 (0|1 또는 boolean). 기본값 False(불허).
    allow_purge: bool = Field(default=False, alias="CLAIRE_ALLOW_PURGE")

    # --- expansion ---
    expand_max: int = Field(default=5, alias="CLAIRE_EXPAND_MAX")
    # 1홉 자동확장: 적재 문서의 링크를 LLM 이 선별→판정→적재(백그라운드 expand-loop).
    # 기본 ON. 끄면(0) 확장 안 함(텔레그램 confirm 버튼 경로도 비활성).
    auto_expand: bool = Field(default=True, alias="CLAIRE_AUTO_EXPAND")
    # 주기 크롤링: watch 문서의 기본 재확인 주기(일). 문서별 watch_interval 이 있으면 그게 우선.
    watch_interval_days: float = Field(default=1.0, alias="CLAIRE_WATCH_INTERVAL_DAYS")

    # --- text slicing & char budgets ---
    # 원문 수집 시 기본 보관 본문 상한 (web, text, xcom, youtube)
    raw_char_budget: int = Field(default=20000, alias="CLAIRE_RAW_CHAR_BUDGET")
    # PDF 스트림에서 추출할 최대 텍스트 분량
    pdf_max_extract_chars: int = Field(default=50000, alias="CLAIRE_PDF_MAX_EXTRACT_CHARS")
    # PDF 논문 판단 시 high effort 적용 기준 글자 수
    pdf_paper_threshold_chars: int = Field(default=15000, alias="CLAIRE_PDF_PAPER_THRESHOLD_CHARS")
    # PDF 논문 적재 시 부록(Appendix) 제외 정책
    pdf_exclude_appendix: bool = Field(default=True, alias="CLAIRE_PDF_EXCLUDE_APPENDIX")
    # PDF 논문 적재 시 참고문헌(References) 제외 정책
    pdf_exclude_references: bool = Field(default=True, alias="CLAIRE_PDF_EXCLUDE_REFERENCES")
    # PDF 추출 파서 엔진 선택 ("pypdf", "docling")
    pdf_parser: str = Field(default="pypdf", alias="CLAIRE_PDF_PARSER")
    # 15,000자 이상 논문 PDF 적재 시 사고/추론 레벨
    pdf_paper_effort: str = Field(default="high", alias="CLAIRE_PDF_PAPER_EFFORT")
    # 15,000자 미만 또는 비논문 PDF 적재 시 기본 레벨 (빈 문자열이면 프로바이더 기본 env 사용)
    pdf_default_effort: str = Field(default="", alias="CLAIRE_PDF_DEFAULT_EFFORT")
    # 무료 어댑터 우선 1차 논문 판정 시 사용할 최저 effort
    pdf_classifier_effort: str = Field(default="low", alias="CLAIRE_PDF_CLASSIFIER_EFFORT")
    # 단일 문서 KG 추출 LLM 프롬프트 투입 본문 상한
    extract_char_budget: int = Field(default=20000, alias="CLAIRE_EXTRACT_CHAR_BUDGET")
    # 병합 문서 KG 추출 투입 본문 상한 (0 지정 시 extract_char_budget * 2 자동 계산)
    merged_extract_char_budget: int = Field(default=0, alias="CLAIRE_MERGED_EXTRACT_CHAR_BUDGET")
    # 슬라이싱 전략 (table-exemption: 표 보존형, strict: 단순 절단형)
    slicing_strategy: str = Field(default="table-exemption", alias="CLAIRE_SLICING_STRATEGY")
    # 임베딩 생성 시 본문 슬라이싱 상한
    embed_char_budget: int = Field(default=8000, alias="CLAIRE_EMBED_CHAR_BUDGET")
    # 1홉 자동확장 및 선별 시 컨텍스트 상한
    expand_char_budget: int = Field(default=2000, alias="CLAIRE_EXPAND_CHAR_BUDGET")
    # 리서치 보고서/맥락 상한
    research_context_budget: int = Field(default=8000, alias="CLAIRE_RESEARCH_CONTEXT_BUDGET")

    # --- languages & localization ---
    # 프로젝트 광역 선호 언어 목록 (쉼표 구분, 기본값 'ko'). 'en'은 항상 공통 폴백으로 포함됨.
    preferred_languages: str = Field(
        default="ko", alias="CLAIRE_PREFERRED_LANGUAGES"
    )

    # --- web service (DM 과 동일 ingest 통로 + graph UI) ---
    # 운영 명령의 canonical selector. cb-manuscript가 development에서는 .env.dev
    # overlay까지 해소한 뒤 모든 컨테이너에 같은 값을 전달한다.
    environment: str = Field(default="", alias="CLAIRE_ENVIRONMENT")
    inject_host: str = Field(default="127.0.0.1", alias="CLAIRE_INJECT_HOST")
    inject_port: int = Field(default=8765, alias="CLAIRE_INJECT_PORT")
    inject_token: str = Field(default="", alias="CLAIRE_INJECT_TOKEN")
    # 읽기 전용 공개 토큰 — owner bearer(inject_token)와 별개. GET(검색/그래프/노드상세/
    # 문서목록)만 통과시키고 쓰기(ingest/dedup-merge/공유링크발급 등)는 차단(에이전트 조회용).
    readonly_token: str = Field(default="", alias="CLAIRE_READONLY_TOKEN")
    # exact 0|1 opt-in. True면 자격증명 없는 same-origin 요청을 읽기 전용으로만
    # 허용한다(숨김 문서는 제외). owner 인증과 쓰기 경로는 그대로 유지된다.
    anonymous_readonly: bool = Field(
        default=True,
        alias="CLAIRE_ANONYMOUS_READONLY",
    )
    # 브라우저 기준 canonical URL. Host 검증, same-origin 판정, /web 링크 생성에 함께 쓴다.
    public_url: str = Field(default="", alias="CLAIRE_PUBLIC_URL")
    # FQDN 또는 공개 도메인 호스트명 (예: claire.example.com). 미설정 시 public_url 호스트명 사용.
    fqdn: str = Field(default="", alias="CLAIRE_FQDN")
    # 브라우저 cross-origin 호출을 허용할 exact origin 목록. 인증은 Bearer만 허용한다.
    cors_allowed_origins: str = Field(
        default="", alias="CLAIRE_CORS_ALLOWED_ORIGINS"
    )
    # Google Analytics 4 측정 ID (예: G-XXXXXXXXXX, GTM-XXXXXXX). 미설정 시 GA 비활성화.
    ga_measurement_id: str = Field(
        default="", alias="CLAIRE_GA_MEASUREMENT_ID"
    )

    # --- source repository ---
    github_repository: str = Field(
        default="fofwisdom/claire-bible",
        alias="GITHUB_REPOSITORY",
    )
    source_base_url: str = Field(
        default="",
        alias="SOURCE_BASE_URL",
    )

    @field_validator("render_format", mode="before")
    @classmethod
    def _parse_render_format(cls, value: object) -> str:
        s = str(value or "md").strip().lower()
        if s in ("asciidoc", "adoc"):
            return "adoc"
        if s in ("markdown", "md"):
            return "md"
        raise ValueError("CLAIRE_RENDER_FORMAT must be 'md' or 'adoc'")

    @field_validator("data_lifecycle", mode="before")
    @classmethod
    def _parse_data_lifecycle(cls, value: object) -> str:
        s = str(value or "append-only").strip().lower()
        if s in ("append-only", "append_only", "appendonly"):
            return "append-only"
        if s in ("purgeable", "mutable", "purge_enabled", "purge"):
            return "purgeable"
        raise ValueError("CLAIRE_DATA_LIFECYCLE must be 'append-only' or 'purgeable'")

    @field_validator("allow_purge", mode="before")
    @classmethod
    def _parse_allow_purge(cls, value: object) -> bool:
        if isinstance(value, bool):
            return value
        s = str(value or "").strip().lower()
        if s in ("1", "true", "yes", "on"):
            return True
        if s in ("0", "false", "no", "off", ""):
            return False
        raise ValueError("CLAIRE_ALLOW_PURGE must be a boolean or 0/1")

    @field_validator("enable_video_transcription", mode="before")
    @classmethod
    def _parse_enable_video_transcription(cls, value: object) -> bool:
        if isinstance(value, bool):
            return value
        s = str(value or "").strip().lower()
        if s in ("1", "true", "yes", "on"):
            return True
        if s in ("0", "false", "no", "off", ""):
            return False
        raise ValueError("CLAIRE_ENABLE_VIDEO_TRANSCRIPTION must be a boolean or 0/1")

    @field_validator("anonymous_readonly", mode="before")
    @classmethod
    def _parse_anonymous_readonly(cls, value: object) -> bool:
        """보안 경계 설정은 Pydantic의 넓은 bool 별칭 대신 exact 0|1만 받는다."""

        if isinstance(value, bool):
            return value
        if value == "0":
            return False
        if value == "1":
            return True
        raise ValueError("CLAIRE_ANONYMOUS_READONLY must be exactly 0 or 1")

    @field_validator("slicing_strategy", mode="before")
    @classmethod
    def _parse_slicing_strategy(cls, value: object) -> str:
        s = str(value or "table-exemption").strip().lower()
        if s in ("table-exemption", "table_exemption", "table", "exemption"):
            return "table-exemption"
        if s in ("strict", "truncate", "cut"):
            return "strict"
        raise ValueError("CLAIRE_SLICING_STRATEGY must be 'table-exemption' or 'strict'")

    @field_validator("ga_measurement_id", mode="before")
    @classmethod
    def _parse_ga_measurement_id(cls, value: object) -> str:
        s = str(value or "").strip()
        if not s:
            return ""
        import re

        if not re.fullmatch(r"^[A-Za-z0-9_-]+$", s):
            raise ValueError(
                "CLAIRE_GA_MEASUREMENT_ID must contain only alphanumeric characters, dashes, and underscores"
            )
        return s

    @field_validator("stt_provider", mode="before")
    @classmethod
    def _parse_stt_provider(cls, value: object) -> str:
        s = str(value or "").strip()
        if not s:
            s = os.environ.get("STT_PROVIDER", "").strip() or os.environ.get("CLAIRE_STT_PROVIDER", "").strip()
        return s or "gemini"

    @field_validator("stt_model", mode="before")
    @classmethod
    def _parse_stt_model(cls, value: object) -> str:
        s = str(value or "").strip()
        if not s:
            s = os.environ.get("STT_MODEL", "").strip() or os.environ.get("CLAIRE_STT_MODEL", "").strip()
        return s

    @field_validator("stt_custom_vocabulary", mode="before")
    @classmethod
    def _parse_stt_custom_vocabulary(cls, value: object) -> list[str]:
        if isinstance(value, list):
            return [str(x).strip() for x in value if str(x).strip()]
        if isinstance(value, str):
            parts = [p.strip() for p in value.replace("\n", ",").split(",") if p.strip()]
            return parts
        return []

    @property
    def effective_merged_extract_char_budget(self) -> int:
        """병합 문서 추출 예산 (0 이하이면 단일 문서 예산의 2배)."""
        if self.merged_extract_char_budget > 0:
            return self.merged_extract_char_budget
        return self.extract_char_budget * 2

    @property
    def effective_ga_measurement_id(self) -> str:
        return self.ga_measurement_id.strip()

    @property
    def is_purge_allowed(self) -> bool:
        """소각(Purge) 명령 실행 가능 여부."""
        return self.data_lifecycle == "purgeable" or self.allow_purge

    @property
    def effective_github_repository(self) -> str:
        return self.github_repository.strip() or "fofwisdom/claire-bible"

    @property
    def effective_source_base_url(self) -> str:
        repo = self.effective_github_repository
        raw_url = self.source_base_url.strip()
        if not raw_url:
            return f"https://github.com/{repo}"
        url = raw_url.replace("$GITHUB_REPOSITORY", repo).replace("${GITHUB_REPOSITORY}", repo)
        return url.rstrip("/")

    @property
    def effective_preferred_languages(self) -> list[str]:
        """프로젝트 광역 선호 언어(기본 ko 등) + 항상 기본 포함되는 'en' 목록."""
        langs: list[str] = []
        for raw in self.preferred_languages.split(","):
            code = raw.strip().lower()
            if code and code not in langs and code != "en":
                langs.append(code)
        langs.append("en")
        return langs

    @property
    def effective_youtube_languages(self) -> list[str]:
        """YouTube 등 하위 기능 호환용 선호 언어 프로퍼티 (effective_preferred_languages 위임)."""
        return self.effective_preferred_languages

    @property
    def effective_provider(self) -> str:
        """키나 실행 환경이 갖춰지지 않으면 provider 는 mock 으로 떨어진다."""
        raw = self.provider.strip().lower()
        if raw in ("antigravity", "agy"):
            if find_agy_executable(self.agy_bin) is not None:
                return "antigravity"
            return "mock"
        if raw in ("codex", "codex-cli"):
            if find_codex_executable(self.codex_bin) is not None:
                return "codex"
            return "mock"
        if raw == "gemini" and not self.gemini_api_key:
            return "mock"
        return raw

    @property
    def effective_stt_provider(self) -> str:
        """비디오 전사 기능 활성화 및 실행 환경에 따른 STT provider 반환."""
        if not self.enable_video_transcription:
            return "mock"
        raw = (self.stt_provider or "gemini").strip().lower()
        if raw in ("gemini", "google"):
            if self.gemini_api_key:
                return "gemini"
            return "mock"
        if raw == "mock":
            return "mock"
        # antigravity 등 오디오 전사(STT) 미지원 프로바이더는 gemini로 하이잭하지 않고 안전하게 mock으로 폴백
        return "mock"

    @property
    def db_file(self) -> Path:
        p = Path(self.db_path)
        return p if p.is_absolute() else ROOT / p

    @property
    def data_dir(self) -> Path:
        """raw 보관 등 데이터 루트. db 파일의 부모 디렉터리."""
        return self.db_file.parent

    @property
    def vault_dir(self) -> Path:
        p = Path(self.vault_path)
        return p if p.is_absolute() else ROOT / p

    @property
    def allowed_user_ids(self) -> set[int]:
        out: set[int] = set()
        for tok in self.allowed_users.split(","):
            tok = tok.strip()
            if tok:
                try:
                    out.add(int(tok))
                except ValueError:
                    pass
        return out

    @property
    def notify_chat_id(self) -> int:
        """운영 경보를 보낼 chat. owner_chat_id 우선, 없으면 allowed_users 최솟값 폴백."""
        if self.owner_chat_id:
            return self.owner_chat_id
        ids = self.allowed_user_ids
        return min(ids) if ids else 0

    @property
    def effective_fqdn(self) -> str:
        """FQDN 호스트명. CLAIRE_FQDN 우선, 없으면 CLAIRE_PUBLIC_URL 에서 추출."""
        if self.fqdn:
            return self.fqdn.strip().lower()
        if self.public_url:
            from urllib.parse import urlsplit
            host = urlsplit(self.public_url).hostname
            if host:
                return host.strip().lower()
        return ""


def extract_own_share_token(url_candidate: str, settings: Settings | None = None) -> str | None:
    """URL이 자체 인스턴스의 공유 링크(/p?s=token)인 경우에만 token을 추출. 타 사이트 URL은 None."""
    from urllib.parse import parse_qs, urlsplit
    s = settings or get_settings()
    t = (url_candidate or "").strip()
    if not t:
        return None

    # 상대 경로인 경우 (?s=token 또는 /p?s=token)
    if t.startswith("?s="):
        return t[3:].split("&")[0].strip() or None
    if t.startswith("/p?") or t.startswith("/p/?"):
        qs = parse_qs(urlsplit(t).query)
        toks = qs.get("s")
        return toks[0].strip() if toks and toks[0].strip() else None

    # 절대 URL인 경우
    if t.lower().startswith(("http://", "https://")):
        parsed = urlsplit(t)
        host = (parsed.hostname or "").lower()
        eff_fqdn = s.effective_fqdn
        pub_host = (urlsplit(s.public_url).hostname or "").lower() if s.public_url else ""

        is_match = False
        if eff_fqdn and host == eff_fqdn:
            is_match = True
        elif pub_host and host == pub_host:
            is_match = True
        elif not eff_fqdn and not pub_host and host in ("localhost", "127.0.0.1"):
            is_match = True

        if is_match and parsed.path.rstrip("/") == "/p":
            qs = parse_qs(parsed.query)
            toks = qs.get("s")
            if toks and toks[0].strip():
                return toks[0].strip()

    return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
