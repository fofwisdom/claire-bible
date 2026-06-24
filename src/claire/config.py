"""중앙 설정. .env 또는 환경변수에서 로드한다.

Gemini 키가 없으면 provider 는 자동으로 mock 으로 떨어진다(M0~M1 선행 개발용).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 프로젝트 루트 (이 파일 기준 src/claire/config.py -> 루트는 parents[2])
ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        env_prefix="",
        extra="ignore",
        case_sensitive=False,
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

    # --- local inject API (DM 과 동일 ingest 통로를 로컬에서 호출) ---
    inject_host: str = Field(default="127.0.0.1", alias="CLAIRE_INJECT_HOST")
    inject_port: int = Field(default=8765, alias="CLAIRE_INJECT_PORT")
    inject_token: str = Field(default="", alias="CLAIRE_INJECT_TOKEN")
    # 외부 공개 URL(예: https://claire.blackan.net) — /web 명령이 접속 링크를 만들 때 사용.
    public_url: str = Field(default="", alias="CLAIRE_PUBLIC_URL")

    @property
    def effective_provider(self) -> str:
        """키가 없으면 gemini 를 요청해도 mock 으로 떨어진다."""
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
