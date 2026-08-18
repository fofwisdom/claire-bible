"""중앙 설정. .env 또는 환경변수에서 로드한다.

Gemini 키가 없으면 provider 는 자동으로 mock 으로 떨어진다(M0~M1 선행 개발용).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import shutil
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
    gemini_embed_model: str = Field(
        default="gemini-embedding-001", alias="CLAIRE_GEMINI_EMBED_MODEL"
    )
    # rate limit 보호: 호출 간 최소 간격(초) + 429/5xx 재시도 횟수.
    gemini_min_interval: float = Field(default=4.0, alias="CLAIRE_GEMINI_MIN_INTERVAL")
    gemini_max_retries: int = Field(default=5, alias="CLAIRE_GEMINI_MAX_RETRIES")

    # --- Antigravity CLI (agy) ---
    agy_bin: str = Field(default="agy", alias="CLAIRE_AGY_BIN")
    agy_model: str = Field(default="gemini-3.6-flash-high", alias="CLAIRE_AGY_MODEL")
    agy_effort: str = Field(default="medium", alias="CLAIRE_AGY_EFFORT")
    agy_timeout: float = Field(default=120.0, alias="CLAIRE_AGY_TIMEOUT")
    agy_max_concurrency: int = Field(default=2, alias="CLAIRE_AGY_MAX_CONCURRENCY")

    # --- storage ---
    db_path: str = Field(default="data/claire.db", alias="CLAIRE_DB_PATH")
    vault_path: str = Field(default="vault", alias="CLAIRE_VAULT_PATH")
    vector_backend: str = Field(default="auto", alias="CLAIRE_VECTOR_BACKEND")

    # --- expansion ---
    expand_max: int = Field(default=5, alias="CLAIRE_EXPAND_MAX")
    # 1홉 자동확장: 적재 문서의 링크를 LLM 이 선별→판정→적재(백그라운드 expand-loop).
    # 기본 ON. 끄면(0) 확장 안 함(텔레그램 confirm 버튼 경로도 비활성).
    auto_expand: bool = Field(default=True, alias="CLAIRE_AUTO_EXPAND")
    # 주기 크롤링: watch 문서의 기본 재확인 주기(일). 문서별 watch_interval 이 있으면 그게 우선.
    watch_interval_days: float = Field(default=1.0, alias="CLAIRE_WATCH_INTERVAL_DAYS")

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
    # 허용한다. owner 인증과 쓰기 경로는 그대로 유지된다.
    anonymous_readonly: bool = Field(
        default=False,
        alias="CLAIRE_ANONYMOUS_READONLY",
    )
    # 브라우저 기준 canonical URL. Host 검증, same-origin 판정, /web 링크 생성에 함께 쓴다.
    public_url: str = Field(default="", alias="CLAIRE_PUBLIC_URL")
    # 브라우저 cross-origin 호출을 허용할 exact origin 목록. 인증은 Bearer만 허용한다.
    cors_allowed_origins: str = Field(
        default="", alias="CLAIRE_CORS_ALLOWED_ORIGINS"
    )

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

    @property
    def effective_provider(self) -> str:
        """키나 실행 환경이 갖춰지지 않으면 provider 는 mock 으로 떨어진다."""
        if self.provider in ("antigravity", "agy"):
            if shutil.which(self.agy_bin) is not None:
                return "antigravity"
            return "mock"
        if self.provider == "gemini" and not self.gemini_api_key:
            return "mock"
        return self.provider

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
