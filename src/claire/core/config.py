"""중앙 설정. .env 또는 환경변수에서 로드한다.

Gemini 키가 없으면 provider 는 자동으로 mock 으로 떨어진다(M0~M1 선행 개발용).
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, PrivateAttr, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


def resolve_app_root(
    module_file: Path | str | None = None,
    *,
    configured_root: str | None = None,
    working_directory: Path | str | None = None,
) -> Path:
    """소스 checkout과 설치된 wheel 모두에서 애플리케이션 루트를 해석한다."""
    raw = (
        os.environ.get("CLAIRE_APP_ROOT", "")
        if configured_root is None
        else configured_root
    )
    if raw:
        if raw != raw.strip() or "\x00" in raw:
            raise ValueError("CLAIRE_APP_ROOT must not contain outer whitespace or NUL")
        explicit = Path(raw).expanduser()
        if not explicit.is_absolute():
            raise ValueError("CLAIRE_APP_ROOT must be an absolute path")
        return explicit.resolve(strict=False)

    module_path = Path(module_file or __file__).resolve(strict=False)
    source_root = module_path.parents[3]
    if (source_root / "pyproject.toml").is_file():
        return source_root

    # Installed distributions do not contain the checkout above the package.
    # Native commands therefore resolve relative runtime paths from their cwd;
    # the container always supplies CLAIRE_APP_ROOT=/app explicitly.
    return Path(working_directory or Path.cwd()).resolve(strict=False)


ROOT = resolve_app_root()


class _ProvidersJsonSettingsSource(PydanticBaseSettingsSource):
    """providers.json (WebUI 관리 파일)에서 프로바이더 설정을 로드하는 settings source."""

    def __init__(
        self,
        settings_cls: type[BaseSettings],
        data_dir: str | Path | None = None,
        init_kwargs: Any = None,
    ) -> None:
        super().__init__(settings_cls)
        self.init_kwargs = dict(init_kwargs or {})
        self.explicit_data_dir = data_dir

    def get_field_value(
        self, field: Any, field_name: str
    ) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        try:
            cand = (
                self.init_kwargs.get("data_dir")
                or self.init_kwargs.get("CB_DATA_DIR")
                or self.init_kwargs.get("db_path")
                or self.init_kwargs.get("CLAIRE_DB_PATH")
                or self.explicit_data_dir
                or os.environ.get("CB_DATA_DIR")
                or os.environ.get("CLAIRE_DB_PATH")
                or "data"
            )
            data_dir = Path(cand).parent if str(cand).endswith(".db") else Path(cand)
            from .provider_manager import get_provider_manager

            pm = get_provider_manager(data_dir=data_dir)
            return pm.get_settings_dict()
        except Exception:
            return {}


# The model of each provider comes from the Web UI (providers.json) only. The old variables are read one time by ``ProviderManager.migrate_from_env_if_needed``.
MODEL_FIELDS = ("gemini_model", "gemini_embed_model", "jev_model", "stt_model")
# The preferred languages come from the database (the server default language, ``server_settings.default_languages``) and from the account of a job (``settings_for_context``). Schema v46 reads the old variable ``CLAIRE_PREFERRED_LANGUAGES`` one time (ACCOUNT_PREFERRED_LANGUAGES_DESIGN.md P3).
DATABASE_FIELDS = ("preferred_languages",)


class _ServerLanguagesSettingsSource(PydanticBaseSettingsSource):
    """The server default language from ``server_settings`` of claire.db (read only). No value when the database or the row does not exist."""

    def __init__(self, settings_cls: type[BaseSettings], init_kwargs: Any = None) -> None:
        super().__init__(settings_cls)
        self.init_kwargs = dict(init_kwargs or {})

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        import sqlite3

        raw = self.init_kwargs.get("CLAIRE_DB_PATH") or self.init_kwargs.get("db_path") or os.environ.get("CLAIRE_DB_PATH") or "data/claire.db"
        path = Path(str(raw))
        path = path if path.is_absolute() else ROOT / path
        if not path.is_file():
            return {}
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
            try:
                row = conn.execute("SELECT value FROM server_settings WHERE key='default_languages'").fetchone()
            finally:
                conn.close()
        except sqlite3.Error:
            return {}
        return {} if row is None else {"preferred_languages": str(row[0])}


class _WithoutModelFields(PydanticBaseSettingsSource):
    """An environment source without the model fields and the database fields: no environment variable and no ``.env`` line sets them."""

    def __init__(self, settings_cls: type[BaseSettings], source: Any) -> None:
        super().__init__(settings_cls)
        self.source = source

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        blocked = {name for field in (*MODEL_FIELDS, *DATABASE_FIELDS) for name in (field, f"claire_{field}")}
        return {key: value for key, value in self.source().items() if str(key).lower() not in blocked}


class _IngestLimitsJsonSettingsSource(PydanticBaseSettingsSource):
    """ingest_limits.json (WebUI 관리 파일)에서 상황 별 적재 제한 설정을 로드하는 settings source."""

    def __init__(
        self,
        settings_cls: type[BaseSettings],
        data_dir: str | Path | None = None,
        init_kwargs: Any = None,
    ) -> None:
        super().__init__(settings_cls)
        self.init_kwargs = dict(init_kwargs or {})
        self.explicit_data_dir = data_dir

    def get_field_value(
        self, field: Any, field_name: str
    ) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        try:
            cand = (
                self.init_kwargs.get("data_dir")
                or self.init_kwargs.get("CB_DATA_DIR")
                or self.init_kwargs.get("db_path")
                or self.init_kwargs.get("CLAIRE_DB_PATH")
                or self.explicit_data_dir
                or os.environ.get("CB_DATA_DIR")
                or os.environ.get("CLAIRE_DB_PATH")
                or "data"
            )
            data_dir = Path(cand).parent if str(cand).endswith(".db") else Path(cand)
            from .ingest_limit_manager import get_ingest_limit_manager

            ilm = get_ingest_limit_manager(data_dir=data_dir)
            limits = ilm.get_settings_dict()
        except Exception:
            return {}
        # The environment sources give a value under the alias (CLAIRE_*). With the field name, an exported variable or a .env line would win over this file. Give the alias, so that this source has the higher priority.
        fields = self.settings_cls.model_fields
        return {
            (fields[name].alias or name) if name in fields else name: value
            for name, value in limits.items()
        }


PROVIDER_UNAVAILABLE = "unavailable"


class ProviderUnavailableError(RuntimeError):
    """설정한 프로바이더를 쓸 수 없는데 mock 으로 대체하면 안 되는 경우(운영 환경)."""


def _is_executable_file(path: Path) -> bool:
    """True when ``path`` is a file that the process can run. A path in a directory that the process cannot read (for example /root/.local/bin when the process is not root) is not a candidate: ``Path.is_file`` returns False for such a path."""
    return path.is_file() and os.access(path, os.X_OK)


def find_ffmpeg_executable(ffmpeg_bin: str = "ffmpeg") -> str | None:
    """Find ffmpeg executable in PATH or standard locations."""
    if not ffmpeg_bin:
        ffmpeg_bin = "ffmpeg"
    raw = str(ffmpeg_bin).strip()
    explicit = Path(raw).expanduser()
    if _is_executable_file(explicit):
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
        if _is_executable_file(cand):
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
        init_kwargs = getattr(init_settings, "init_kwargs", None)
        providers_json = _ProvidersJsonSettingsSource(
            settings_cls,
            init_kwargs=init_kwargs,
        )
        ingest_limits_json = _IngestLimitsJsonSettingsSource(
            settings_cls,
            init_kwargs=init_kwargs,
        )
        return (
            init_settings,
            _ServerLanguagesSettingsSource(settings_cls, init_kwargs=init_kwargs),
            ingest_limits_json,
            providers_json,
            _WithoutModelFields(settings_cls, env_settings),
            _WithoutModelFields(settings_cls, dotenv_settings),
            file_secret_settings,
        )

    # --- provider policy (P4, 설계 §10.2) ---
    # 계정 컨텍스트(``accounts.provider_policy.ProviderContext``)는 필드가 아니라 비공개 속성이다. 그래서 env·.env·providers.json
    # 어느 소스에서도 읽히지 않고, ``model_dump``·직렬화·support bundle에 나가지 않는다. ``model_copy``는 이 값을 그대로 옮긴다.
    _provider_context: Any = PrivateAttr(default=None)

    @property
    def provider_context(self) -> Any:
        """``provider_policy.settings_for_context``가 붙인 컨텍스트. 없으면 None(관리자·레거시 경로)."""
        return self._provider_context

    @provider_context.setter
    def provider_context(self, value: Any) -> None:
        self._provider_context = value

    def _member_context(self) -> Any:
        ctx = self._provider_context
        return ctx if ctx is not None and not getattr(ctx, "is_admin", True) else None

    # --- secrets ---
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_allowed_users: str = Field(default="", alias="TELEGRAM_ALLOWED_USERS")
    # 운영 경보를 받을 소유자 chat. 미설정(0)이면 telegram_allowed_users 로 폴백(개인 DM 은
    # chat_id == user_id 이므로 단일 사용자 환경에서 별도 설정 없이 동작).
    telegram_owner_chat_id: int = Field(default=0, alias="TELEGRAM_OWNER_CHAT_ID")

    # --- provider ---
    provider: str = Field(default="mock", alias="CLAIRE_PROVIDER")
    # The model fields have no environment variable: the Web UI (providers.json) sets the models. ``_WithoutModelFields`` removes them from the environment sources.
    gemini_model: str = Field(default="gemini-3.1-flash-lite-medium")
    gemini_effort: str = Field(default="medium", alias="CLAIRE_GEMINI_EFFORT")
    gemini_embed_model: str = Field(default="gemini-embedding-2")
    embed_task_type: str = Field(
        default="SEMANTIC_SIMILARITY", alias="CLAIRE_EMBED_TASK_TYPE"
    )
    feature_routing: dict[str, dict[str, str]] = Field(default_factory=dict)
    sim_tier_auto_merge: float = Field(
        default=0.93, alias="CLAIRE_SIM_TIER_AUTO_MERGE"
    )
    sim_tier_borderline: float = Field(
        default=0.72, alias="CLAIRE_SIM_TIER_BORDERLINE"
    )
    sim_tier_relational: float = Field(
        default=0.70, alias="CLAIRE_SIM_TIER_RELATIONAL"
    )
    sim_tier_multihop: float = Field(
        default=0.55, alias="CLAIRE_SIM_TIER_MULTIHOP"
    )
    vector_adaptive_centering: bool = Field(
        default=True, alias="CLAIRE_VECTOR_ADAPTIVE_CENTERING"
    )
    # Phase 2: 지식 그래프 전역 관계(Edge) 자동 수립 파이프라인
    enable_relation_linking: bool = Field(
        default=True, alias="CLAIRE_ENABLE_RELATION_LINKING"
    )
    max_relation_judges_per_entity: int = Field(
        default=3, alias="CLAIRE_MAX_RELATION_JUDGES_PER_ENTITY"
    )
    max_relation_judges_per_doc: int = Field(
        default=10, alias="CLAIRE_MAX_RELATION_JUDGES_PER_DOC"
    )
    # rate limit 보호: 호출 간 최소 간격(초) + 429/5xx 재시도 횟수.
    gemini_min_interval: float = Field(default=4.0, alias="CLAIRE_GEMINI_MIN_INTERVAL")
    gemini_max_retries: int = Field(default=5, alias="CLAIRE_GEMINI_MAX_RETRIES")

    # --- TypeSafe AI Jev (System 1 entity resolution, shadow evaluation only) ---
    enable_jev: bool = Field(default=False, alias="CLAIRE_ENABLE_JEV")
    jev_api_key: str | None = Field(default=None, alias="CLAIRE_JEV_API_KEY")
    jev_base_url: str = Field(
        default="https://api.typesafe.ai/v1", alias="CLAIRE_JEV_BASE_URL"
    )
    jev_timeout: float = Field(default=15.0, alias="CLAIRE_JEV_TIMEOUT")
    jev_model: str = Field(default="jev-latest")

    # --- Video & Audio Transcription (STT) ---
    enable_video_transcription: bool = Field(
        default=True, alias="CLAIRE_ENABLE_VIDEO_TRANSCRIPTION"
    )
    stt_model: str = Field(default="")
    # "auto": the preferred languages of the job go to the STT request as hints; one language tag: that language only (ACCOUNT_PREFERRED_LANGUAGES_DESIGN.md P2).
    stt_language: str = Field(default="auto", alias="CLAIRE_STT_LANGUAGE")
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

    # --- Vision & Infographic Transcription (VLM) ---
    enable_vlm_transcription: bool = Field(
        default=True, alias="CLAIRE_ENABLE_VLM_TRANSCRIPTION"
    )
    vlm_provider: str = Field(default="gemini", alias="CLAIRE_VLM_PROVIDER")
    vlm_model: str = Field(default="", alias="CLAIRE_VLM_MODEL")
    vlm_max_images: int = Field(default=20, alias="CLAIRE_VLM_MAX_IMAGES")
    presentation_pdf_max_bytes: int = Field(
        default=64 * 1024 * 1024,
        alias="CLAIRE_PRESENTATION_PDF_MAX_BYTES",
        gt=0,
    )

    # --- storage ---
    db_path: str = Field(default="data/claire.db", alias="CLAIRE_DB_PATH")
    # Plugin folder of the server administrator (docs/implementation/INGESTION.md §2.2). Not in the data folder.
    plugins_path: str = Field(default="var/plugins", alias="CLAIRE_PLUGINS_DIR")
    # 데이터 수명주기 정책: purgeable (기본값) | append-only.
    data_lifecycle: str = Field(default="purgeable", alias="CLAIRE_DATA_LIFECYCLE")
    # 명시적 소각 허용 플래그 (0|1 또는 boolean). 기본값 True(허용).
    allow_purge: bool = Field(default=True, alias="CLAIRE_ALLOW_PURGE")
    kb_id: int = Field(default=0, alias="CLAIRE_KB_ID")

    # --- expansion ---
    expand_max: int = Field(default=5, alias="CLAIRE_EXPAND_MAX")
    # 자동 확장: 적재 문서의 링크를 LLM 이 선별→판정→적재(백그라운드 expand-loop).
    # 기본 ON. 끄면(0) 확장 안 함(텔레그램 confirm 버튼 경로도 비활성).
    auto_expand: bool = Field(default=True, alias="CLAIRE_AUTO_EXPAND")
    # 동일 주제 출처 병합: "off"(기본) 또는 "shadow"(판정만 기록하고 자식은 독립 문서로 적재). 병합하는 모드는 없다(소유자 결정, 2026-11-18). data/ingest_limits.json(WebUI)에서만 설정한다.
    same_subject_merge: str = Field(default="off", alias="CLAIRE_SAME_SUBJECT_MERGE")
    # 주기 재확인: watch 문서의 기본 재확인 주기(일). 문서별 watch_interval 이 있으면 그게 우선.
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

    # --- languages & localization ---
    # The preferred languages of the job (comma-separated, ACCOUNT_PREFERRED_LANGUAGES_DESIGN.md): the server default language from the database, or the list of the account of the job (settings_for_context). No environment variable sets it.
    preferred_languages: str = Field(default="")
    # Server default locale of the interface (WebUI, Telegram bot, CLI human output). Empty: the resolution chain continues to 'en'. No relation to preferred_languages (I18N design Section 9). The field name is not `lang`: with populate_by_name and no prefix, a field `lang` also reads the POSIX variable LANG.
    ui_lang: str = Field(default="", alias="CLAIRE_LANG", description="Server default UI/CLI/Bot locale ('en' or 'ko'; empty uses fallback 'en')")

    # --- web service (DM 과 동일 ingest 통로 + graph UI) ---
    # 운영 명령의 canonical selector. cb-manuscript가 development에서는 .env.dev
    # overlay까지 해소한 뒤 모든 컨테이너에 같은 값을 전달한다.
    environment: str = Field(default="", alias="CLAIRE_ENVIRONMENT")
    inject_host: str = Field(default="127.0.0.1", alias="CLAIRE_INJECT_HOST")
    inject_port: int = Field(default=8765, alias="CLAIRE_INJECT_PORT")
    # Compose-managed service URL.  Telegram uses the API as the sole Support
    # Bundle producer so the process returning the public download also owns the
    # archive and registry transaction.
    support_bundle_api_url: str = Field(
        default="", alias="CLAIRE_SUPPORT_BUNDLE_API_URL"
    )
    graph_bundle_api_url: str = Field(
        default="", alias="CLAIRE_GRAPH_BUNDLE_API_URL"
    )
    # 공개 도메인 호스트명 (FQDN, 예: claire.example.com). 프로덕션 환경 기준 필수 authority.
    fqdn: str = Field(default="", alias="CLAIRE_FQDN")
    # 역할 호스트(설계 §5.1). 형식은 CLAIRE_FQDN과 같다: production은 포트 없는 DNS 이름,
    # development는 app과 같은 IPv4의 다른 포트(`127.0.0.1:8767`). 빈 값이면 그 역할을 두지 않는다.
    # 검증(서로 다름, app 상위 도메인 금지, 등록 도메인 소속, CORS 허용 목록 제외)은
    # `api.host_router.HostRoles.from_settings`가 기동 때 한다.
    # 운영 환경의 등록 도메인(예: example.com). 공개 접미사 목록으로 추정하지 않는다.
    site_domain: str = Field(default="", alias="CLAIRE_SITE_DOMAIN")
    usercontent_fqdn: str = Field(default="", alias="CLAIRE_USERCONTENT_FQDN")
    media_fqdn: str = Field(default="", alias="CLAIRE_MEDIA_FQDN")
    # Cloudflare 공식 공인 IP 대역(https://www.cloudflare.com/ko-kr/ips/)만 접속 허용 (사설 IP는 영향 없음)
    cloudflare_ips_only: bool = Field(
        default=False, alias="CLAIRE_CLOUDFLARE_IPS_ONLY"
    )
    # The edge service (production): when CLAIRE_EDGE_LISTENER=1, the api process opens the edge listener on data/run/edge.sock and reads the peer address that the edge gives (api/edge_listener.py). Compose sets the value; it is not a setting of the env file.
    edge_listener: bool = Field(default=False, alias="CLAIRE_EDGE_LISTENER")
    # 로그인 실패 제한의 클라이언트 IP 판정에서 X-Forwarded-For를 믿을 바로 앞 프록시 대역(쉼표 구분 CIDR).
    # 빈 값이면 헤더를 읽지 않는다. 형식 검사는 `api.security.parse_trusted_proxies`가 기동 때 한다.
    trusted_proxies: str = Field(default="", alias="CLAIRE_TRUSTED_PROXIES")
    # 브라우저 cross-origin 호출을 허용할 exact origin 목록. 인증은 Bearer만 허용한다.
    cors_allowed_origins: str = Field(
        default="", alias="CLAIRE_CORS_ALLOWED_ORIGINS"
    )
    # Public GitHub owner/repo hosting agent marketplace bundles synced from agent_clients/.
    agent_marketplace_repo: str = Field(
        default="fofwisdom/claire-agents", alias="CLAIRE_AGENT_MARKETPLACE_REPO"
    )
    # Google Analytics 4 측정 ID (예: G-XXXXXXXXXX, GTM-XXXXXXX). 미설정 시 GA 비활성화.
    ga_measurement_id: str = Field(
        default="", alias="CLAIRE_GA_MEASUREMENT_ID"
    )

    # --- crawling & search engine optimization (SEO) ---
    # 크롤러 수집 정책 ("public" 또는 "disallow_all")
    robots_policy: str = Field(
        default="public",
        alias="CLAIRE_ROBOTS_POLICY",
    )
    # AI 학습/스크래핑 봇(GPTBot, ClaudeBot 등) robots.txt 차단 여부 (기본 True)
    block_ai_crawlers: bool = Field(
        default=True,
        alias="CLAIRE_BLOCK_AI_CRAWLERS",
    )
    # /sitemap.xml 동적 사이트맵 활성화 여부
    sitemap_enabled: bool = Field(
        default=True,
        alias="CLAIRE_SITEMAP_ENABLED",
    )
    # 사이트맵 인메모리 캐시 TTL (초 단위, 기본 15분)
    sitemap_cache_ttl: int = Field(
        default=900,
        alias="CLAIRE_SITEMAP_CACHE_TTL",
    )
    # 사이트 브랜딩 명칭
    site_name: str = Field(
        default="Claire Bible",
        alias="CLAIRE_SITE_NAME",
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

    # --- private network & SSRF policy ---
    # 온프레미스/사내망 수집 지원: 사설 IP 대역(10.x, 172.16-31.x, 192.168.x, 127.x) 접근 허용
    allow_private_networks: bool = Field(
        default=False, alias="CLAIRE_ALLOW_PRIVATE_NETWORKS"
    )
    # 특정 사설망 CIDR 또는 호스트명 화이트리스트 (예: "10.0.0.0/8, 192.168.1.0/24, .corp.local")
    private_network_allowlist: str = Field(
        default="", alias="CLAIRE_PRIVATE_NETWORK_ALLOWLIST"
    )


    @field_validator("ui_lang", mode="before")
    @classmethod
    def _validate_ui_lang(cls, value: object) -> str:
        cleaned = str(value or "").strip().lower()
        if cleaned not in {"", "en", "ko"}:
            raise ValueError("CLAIRE_LANG must be 'en', 'ko', or empty")
        return cleaned

    @field_validator("same_subject_merge", mode="before")
    @classmethod
    def _parse_same_subject_merge(cls, value: object) -> str:
        s = str(value).strip().lower()
        if s in ("off", "shadow"):
            return s
        raise ValueError("same_subject_merge must be 'off' or 'shadow'")

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

    @model_validator(mode="before")
    @classmethod
    def _normalize_fqdn_setting(cls, data: Any) -> Any:
        if isinstance(data, dict):
            explicit_fqdn = data.get("fqdn") or data.get("CLAIRE_FQDN")
            if explicit_fqdn:
                raw_fqdn = str(explicit_fqdn).strip()
                if "://" in raw_fqdn:
                    from urllib.parse import urlsplit

                    parsed = urlsplit(raw_fqdn)
                    raw_fqdn = parsed.netloc or parsed.path
                normalized = raw_fqdn.rstrip("/")
                data["CLAIRE_FQDN"] = normalized
                data["fqdn"] = normalized
        return data

    @field_validator("cloudflare_ips_only", mode="before")
    @classmethod
    def _parse_cloudflare_ips_only(cls, value: object) -> bool:
        if isinstance(value, bool):
            return value
        s = str(value or "").strip().lower()
        if s in ("1", "true", "yes", "on"):
            return True
        if s in ("0", "false", "no", "off", ""):
            return False
        raise ValueError("CLAIRE_CLOUDFLARE_IPS_ONLY must be a boolean or 0/1")

    @field_validator("edge_listener", mode="before")
    @classmethod
    def _parse_edge_listener(cls, value: object) -> bool:
        if isinstance(value, bool):
            return value
        s = str(value or "").strip()
        if s == "1":
            return True
        if s in ("0", ""):
            return False
        raise ValueError("CLAIRE_EDGE_LISTENER must be exactly 0 or 1")

    @field_validator("allow_private_networks", mode="before")
    @classmethod
    def _parse_allow_private_networks(cls, value: object) -> bool:
        if isinstance(value, bool):
            return value
        s = str(value or "").strip().lower()
        if s in ("1", "true", "yes", "on"):
            return True
        if s in ("0", "false", "no", "off", ""):
            return False
        raise ValueError("CLAIRE_ALLOW_PRIVATE_NETWORKS must be a boolean or 0/1")


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

    @field_validator("stt_model", mode="before")
    @classmethod
    def _parse_stt_model(cls, value: object) -> str:
        return str(value or "").strip()

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
        """The preferred languages of the job in their order (``accounts.languages.parse_stored``): ``en`` is always in the list, at the end when the list does not place it."""
        from ..accounts.languages import ALWAYS_LANGUAGE, parse_stored

        return list(parse_stored(self.preferred_languages) or (ALWAYS_LANGUAGE,))

    @property
    def effective_vlm_language(self) -> str:
        """VLM 전사 대상 언어 (광역 선호 언어 CLAIRE_PREFERRED_LANGUAGES 의 제1선호 언어 추종)."""
        return self.effective_preferred_languages[0] if self.effective_preferred_languages else "ko"

    @property
    def is_production(self) -> bool:
        return (self.environment or "").strip().lower() == "production"

    def _unavailable_or_mock(self) -> str:
        """운영 환경은 mock 으로 대체하지 않고 unavailable 로 표시한다(PRINCIPLES C2). 멤버 컨텍스트는 환경과 무관하게 unavailable."""
        if self._member_context() is not None:
            return PROVIDER_UNAVAILABLE
        return PROVIDER_UNAVAILABLE if self.is_production else "mock"

    def _member_unavailable_reason(self, kind: str, raw: str) -> str | None:
        """멤버 컨텍스트에서 kind 프로바이더를 쓸 수 없는 이유. api-key 분류(gemini)만 쓸 수 있다(§10.1)."""
        if raw in ("", "none"):
            return f"{kind}: 이 계정에 쓸 수 있는 프로바이더가 없다 (API 키를 등록하거나 관리자에게 서버 등급을 요청)"
        if raw in ("gemini", "google"):
            return None if self.gemini_api_key else f"{kind}: 이 계정의 API 키가 없다"
        return f"{kind}: 멤버 계정은 프로바이더 {raw}를 쓸 수 없다"

    def _llm_unavailable_reason(self) -> str | None:
        raw = self.provider.strip().lower()
        if self._member_context() is not None:
            return self._member_unavailable_reason("llm", raw)
        if raw == "gemini":
            return None if self.gemini_api_key else "CLAIRE_PROVIDER=gemini: GEMINI_API_KEY가 비어 있다"
        if raw == "mock":
            return None
        return f"CLAIRE_PROVIDER={self.provider}: 알 수 없는 프로바이더"

    def _stt_unavailable_reason(self) -> str | None:
        ctx = self._member_context()
        if ctx is not None:
            raw = "gemini" if (ctx.provider == "gemini" and ctx.feature_allowed("stt")) else "none"
            return self._member_unavailable_reason("stt", raw)
        return None if self.gemini_api_key else "STT: GEMINI_API_KEY가 비어 있다"

    def _vlm_unavailable_reason(self) -> str | None:
        raw = (self.vlm_provider or "gemini").strip().lower()
        if self._member_context() is not None:
            return self._member_unavailable_reason("vlm", raw)
        if raw in ("gemini", "google"):
            return None if self.gemini_api_key else f"CLAIRE_VLM_PROVIDER={raw}: GEMINI_API_KEY가 비어 있다"
        if raw == "mock":
            return None
        return f"CLAIRE_VLM_PROVIDER={self.vlm_provider}: 알 수 없는 프로바이더"

    def provider_unavailable_reason(self, kind: str) -> str | None:
        """kind(llm|stt|vlm) 프로바이더를 쓸 수 없는 이유. 쓸 수 있거나 기능이 꺼져 있으면 None."""
        if kind == "llm":
            return self._llm_unavailable_reason()
        if kind == "stt":
            return self._stt_unavailable_reason() if self.enable_video_transcription else None
        if kind == "vlm":
            return self._vlm_unavailable_reason() if self.enable_vlm_transcription else None
        raise ValueError(f"unknown provider kind: {kind}")

    @property
    def effective_provider(self) -> str:
        """쓸 수 없으면 개발 환경은 mock, 운영 환경은 unavailable."""
        if self._llm_unavailable_reason() is not None:
            return self._unavailable_or_mock()
        return self.provider.strip().lower()

    @property
    def effective_stt_provider(self) -> str:
        """비디오 전사 기능 활성화 및 실행 환경에 따른 STT provider 반환."""
        if not self.enable_video_transcription:
            return PROVIDER_UNAVAILABLE if self._member_context() is not None else "mock"
        if self._stt_unavailable_reason() is not None:
            return self._unavailable_or_mock()
        return "gemini"

    @property
    def effective_vlm_provider(self) -> str:
        """이미지/인포그래픽 전사(VLM) 기능 활성화 및 실행 환경에 따른 VLM provider 반환."""
        if not self.enable_vlm_transcription:
            return PROVIDER_UNAVAILABLE if self._member_context() is not None else "mock"
        if self._vlm_unavailable_reason() is not None:
            return self._unavailable_or_mock()
        raw = (self.vlm_provider or "gemini").strip().lower()
        return "gemini" if raw in ("gemini", "google") else "mock"

    @property
    def db_file(self) -> Path:
        p = Path(self.db_path)
        return p if p.is_absolute() else ROOT / p

    @property
    def data_dir(self) -> Path:
        """raw 보관 등 데이터 루트. db 파일의 부모 디렉터리."""
        return self.db_file.parent

    @property
    def plugins_dir(self) -> Path:
        """The plugin folder (``CLAIRE_PLUGINS_DIR``). A relative path is under the app root."""
        p = Path(self.plugins_path)
        return p if p.is_absolute() else ROOT / p

    @property
    def allowed_user_ids(self) -> set[int]:
        out: set[int] = set()
        for tok in self.telegram_allowed_users.split(","):
            tok = tok.strip()
            if tok:
                try:
                    out.add(int(tok))
                except ValueError:
                    pass
        return out

    @property
    def notify_chat_id(self) -> int:
        """운영 경보를 보낼 chat. telegram_owner_chat_id 우선, 없으면 allowed_user_ids 최솟값 폴백."""
        if self.telegram_owner_chat_id:
            return self.telegram_owner_chat_id
        ids = self.allowed_user_ids
        return min(ids) if ids else 0

    @property
    def effective_fqdn(self) -> str:
        """Canonical FQDN hostname extracted from CLAIRE_FQDN."""
        if not self.fqdn:
            return ""
        raw = self.fqdn.strip()
        if "://" in raw:
            from urllib.parse import urlsplit

            raw = urlsplit(raw).netloc or urlsplit(raw).path
        return raw.rstrip("/").lower()

    @property
    def public_url(self) -> str:
        """Canonical public origin derived from CLAIRE_FQDN and CLAIRE_ENVIRONMENT."""
        eff = self.effective_fqdn
        if not eff:
            return ""
        environment = (self.environment or "").strip().lower()
        # Decision CD4 (CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md): in development a DNS name below ".test" uses https, as the origin rule of ``security.WebRuntimeConfig`` requires; an IPv4 address uses http.
        labels = eff.split(":", 1)[0].split(".")
        test_name = len(labels) >= 2 and labels[-1] == "test" and all(labels)
        scheme = "https" if environment == "production" or (environment == "development" and test_name) else "http"
        return f"{scheme}://{eff}/"


def extract_own_share_token(url_candidate: str, settings: Settings | None = None) -> str | None:
    """The address token of a permalink of this server (``/silvercord/{token}``) or of an old share link (``/p?s=token``). None for a URL of another site."""
    from urllib.parse import parse_qs, urlsplit

    from ..store.permalinks import PERMALINK_PATH_PREFIX, plausible_token

    s = settings or get_settings()
    t = (url_candidate or "").strip()
    if not t:
        return None

    def _permalink_token(path: str) -> str | None:
        if not path.startswith(PERMALINK_PATH_PREFIX):
            return None
        token = path[len(PERMALINK_PATH_PREFIX):].split("/", 1)[0]
        return token if plausible_token(token) else None

    if t.startswith(PERMALINK_PATH_PREFIX):
        return _permalink_token(urlsplit(t).path)

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
        else:
            try:
                from ..store.knowledge_base import get_kb_manager

                tm = get_kb_manager(s)
                if tm.has_registered_fqdn(host):
                    is_match = True
            except Exception:
                pass

        if is_match and (found := _permalink_token(parsed.path)) is not None:
            return found
        if is_match and parsed.path.rstrip("/") == "/p":
            qs = parse_qs(parsed.query)
            toks = qs.get("s")
            if toks and toks[0].strip():
                return toks[0].strip()

    return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
