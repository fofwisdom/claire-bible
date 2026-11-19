"""LLM 프로바이더 정책 (설계 §10.1–§10.3, P4 계약 스텁).

작업마다 테마 → 계정 → ``ProviderContext`` 순서로 프로바이더를 정한다. 멤버 컨텍스트는 어떤 경로로도 ``host-cli``에 닿지 않고,
멤버 키가 거부되거나 예산이 소진되면 서버 등급·CLI로 폴백하지 않고 실패한다(C2).

레인 소유: L4-a1이 ``resolve_provider_context``·``settings_for_context``·``require_*``를 구현한다. 분류표와 데이터클래스는
오케스트레이터가 확정한 계약이므로 바꾸려면 요청으로 보고한다.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol

if TYPE_CHECKING:
    from claire.config import Settings
    from claire.store.secrets import SecretService

ProviderClass = Literal["host-cli", "api-key", "mock"]
ContextSource = Literal["admin", "byok", "tier", "none"]
Feature = Literal["expand", "stt", "vlm", "presentation", "synthesize"]

HOST_CLI: Final = "host-cli"
API_KEY: Final = "api-key"
MOCK: Final = "mock"

# §10.1 분류표. 여기에 없는 이름은 host-cli로 본다(fail-closed).
PROVIDER_CLASSES: Final[dict[str, ProviderClass]] = {
    "antigravity": HOST_CLI,
    "agy": HOST_CLI,
    "codex": HOST_CLI,
    "gemini": API_KEY,
    "openai": API_KEY,
    "mock": MOCK,
}

# 멤버가 자기 키로 등록할 수 있는 프로바이더. openai는 구현 전이라 내놓지 않는다(§10.1).
MEMBER_KEY_PROVIDERS: Final[tuple[str, ...]] = ("gemini",)
# 서버 등급 프로바이더로 고를 수 있는 값. "none"은 LLM 단계 실패(D6).
TIER_PROVIDERS: Final[tuple[str, ...]] = ("none", "gemini")
TIER_FEATURES: Final[tuple[Feature, ...]] = ("expand", "stt", "vlm", "presentation", "synthesize")
# research(allow_tools=True), Jev 평가, 텔레그램 봇은 관리자 전용이다(§10.1).
ADMIN_ONLY_FEATURES: Final[tuple[str, ...]] = ("research", "jev", "telegram")
EFFORT_ORDER: Final[tuple[str, ...]] = ("none", "minimal", "low", "medium", "high")
ALL_EFFORT_LEVELS: Final[tuple[str, ...]] = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
EFFORT_RANKS: Final[dict[str, int]] = {"none": 0, "minimal": 1, "low": 2, "medium": 3, "high": 4, "xhigh": 5, "max": 6}
_AGY_SUFFIX_ORDER: Final[tuple[str, ...]] = ("xhigh", "high", "medium", "low")
_AGY_FIXED_PREFIXES: Final[tuple[str, ...]] = ("gemini-4-", "claude-", "gpt-oss-", "gemini-pro-orchestrator")
_GEMINI_MODEL_EFFORTS: Final[dict[str, tuple[str, ...]]] = {
    "gemini-3-pro-preview": ("low", "high"),
    "gemini-3.6-flash": ("minimal", "low", "medium", "high"),
    "gemini-3.5-flash-lite": ("minimal", "low", "medium", "high"),
    "gemini-3.1-flash-lite": ("minimal", "low", "medium", "high"),
    "gemini-3-flash-preview": ("minimal", "low", "medium", "high"),
    "gemini-robotics-er-2-preview": ("minimal", "low", "medium", "high"),
    "gemini-2.5-flash": ("none", "low", "medium", "high"),
    "gemini-2.5-flash-lite": ("none", "low", "medium", "high"),
    "gemini-4-argon": ("low", "medium", "high"),
    "gemini-3.8-flash": ("low", "medium", "high"),
    "gemini-3.1-pro-preview": ("low", "medium", "high"),
    "gemini-3.1-pro": ("low", "medium", "high"),
    "gemini-2.5-pro": ("low", "medium", "high"),
}
_CODEX_XHIGH_MODELS: Final[frozenset[str]] = frozenset({"gpt-5.1-codex", "gpt-5.2-codex", "gpt-5.3-codex", "gpt-5.4"})
FEATURE_SLOTS: Final[tuple[str, ...]] = ("extract_graph", "summarize", "render_detail", "entity_judge", "relation_judge", "classify", "research", "embed", "stt", "vlm")
MODEL_CAPABILITY_RANKS: Final[dict[str, int]] = {
    "mock": 0,
    "gemini-embedding-001": 10,
    "text-embedding-3-small": 10,
    "text-embedding-ada-002": 10,
    "gemini-embedding-2": 20,
    "gemini-embedding-2-preview": 20,
    "text-embedding-3-large": 20,
    "jev-1.14.0": 10,
    "jev-latest": 10,
    "gemini-3.5-flash-lite": 10,
    "gemini-3.1-flash-lite": 10,
    "gemini-2.5-flash-lite": 10,
    "gpt-4o-mini": 15,
    "gemini-2.5-flash": 18,
    "gemini-3-flash-preview": 19,
    "gemini-3.6-flash": 19,
    "gemini-3.8-flash": 20,
    "gpt-4.1-mini": 22,
    "o4-mini": 24,
    "gpt-4o": 25,
    "o3-mini": 25,
    "gpt-4.1": 28,
    "gpt-5-mini": 28,
    "gemini-2.5-pro": 30,
    "claude-sonnet-4.6": 30,
    "codex-default": 30,
    "o3": 32,
    "gemini-3.1-pro": 35,
    "gemini-3.1-pro-preview": 35,
    "gpt-5": 35,
    "gpt-5-codex": 35,
    "gpt-5.1-codex": 36,
    "gpt-5.2-codex": 37,
    "gpt-5.3-codex": 38,
    "gpt-5.4": 40,
    "gemini-4-argon": 40,
    "claude-opus-5.5": 40,
    "gemini-4-hydrogen": 50,
}

SERVER_TIER_SETTING_KEY: Final = "server_tier"  # claire.db server_settings 키, 값은 ServerTier.to_dict() JSON


def normalize_model_and_effort(model: str | None, effort: str | None = None) -> tuple[str, str]:
    """Normalize a model identifier and separate any embedded effort suffix."""
    raw_model = str(model or "").strip().lower()
    raw_effort = str(effort or "").strip().lower()
    inferred_effort = ""
    if raw_model not in MODEL_CAPABILITY_RANKS:
        for suf in ("xhigh", "high", "medium", "low", "minimal", "none"):
            token = f"-{suf}"
            if raw_model.endswith(token):
                base = raw_model[: -len(token)]
                if base in MODEL_CAPABILITY_RANKS:
                    raw_model = base
                    inferred_effort = suf
                    break
    resolved_effort = raw_effort or inferred_effort or "medium"
    return raw_model, resolved_effort


def get_model_rank(model: str | None, effort: str | None = None) -> tuple[int, int]:
    """Return a comparable (capability_tier, effort_rank) tuple for a model and effort."""
    base_model, resolved_effort = normalize_model_and_effort(model, effort)
    if base_model == "":
        return (0, EFFORT_RANKS.get(resolved_effort, 3))
    tier_rank = MODEL_CAPABILITY_RANKS.get(base_model, 0)
    eff_rank = EFFORT_RANKS.get(resolved_effort, 3)
    return (tier_rank, eff_rank)


def is_model_equal_or_higher(user_model: str | None, user_effort: str | None, admin_model: str | None, admin_effort: str | None = None) -> bool:
    """Return True when the member BYOK model and effort meet or exceed the admin baseline."""
    user_clean = str(user_model or "").strip().lower()
    if not user_clean or user_clean == "mock":
        return False
    admin_clean = str(admin_model or "").strip().lower()
    if not admin_clean or admin_clean == "mock":
        return True
    return get_model_rank(user_clean, user_effort) >= get_model_rank(admin_clean, admin_effort)


def clamp_byok_model_for_feature(user_model: str | None, user_effort: str | None, admin_model: str | None, admin_effort: str | None = None) -> tuple[str, str]:
    """Return the user model and effort if >= admin baseline, else return the admin baseline."""
    if is_model_equal_or_higher(user_model, user_effort, admin_model, admin_effort):
        _, eff = normalize_model_and_effort(user_model, user_effort)
        return str(user_model or "").strip(), eff
    _, adm_eff = normalize_model_and_effort(admin_model, admin_effort)
    return str(admin_model or "").strip(), adm_eff


def classify_provider(name: str | None) -> ProviderClass:
    """§10.1 분류. 빈 값·미등록 이름은 ``host-cli``(fail-closed)."""
    return PROVIDER_CLASSES.get((name or "").strip().lower(), HOST_CLI)


class ProviderPolicyError(RuntimeError):
    """정책이 작업을 막았다. ``code``는 화면·API 오류 코드, ``remedy``는 사용자에게 보여 줄 복구 방법이다(§10.2 폴백 없음).

    메시지에 키 원문을 넣지 않는다(§11.3).
    """

    code: str = "provider_policy"

    def __init__(self, message: str, *, remedy: str = "") -> None:
        super().__init__(message)
        self.remedy = remedy


class ProviderNotConfigured(ProviderPolicyError):
    """멤버에게 자기 키도 없고 서버 등급도 ``none``이다. 원문 저장과 FTS는 계속 동작한다."""

    code = "provider_not_configured"


class ProviderNotAllowed(ProviderPolicyError):
    """멤버 컨텍스트가 host-cli·관리자 전용 기능·등급에서 끈 기능·상한 초과 effort를 요청했다."""

    code = "provider_not_allowed"


class ProviderBudgetExceeded(ProviderPolicyError):
    """UTC 월 토큰 상한 또는 UTC 일 적재 건수 상한을 넘었다(§10.3)."""

    code = "provider_budget_exceeded"


class ProviderKeyRejected(ProviderPolicyError):
    """멤버 키가 거부되었다(인증 실패·할당량 소진). 서버 등급으로 바꾸지 않는다(C2)."""

    code = "provider_key_rejected"


# Fields that an old stored tier or an old client can have. ``ServerTier.from_dict`` ignores them.
RETIRED_TIER_FIELDS = frozenset({"max_themes"})


@dataclass(frozen=True, slots=True)
class ServerTier:
    """§10.3 서버 등급. 기본값이 곧 D6(``none``)이다."""

    provider: str = "none"
    model: str = ""
    monthly_tokens: int = 0
    daily_ingests: int = 20
    max_effort: str = "low"
    features: frozenset[str] = frozenset()
    storage_mb: int = 2048
    max_documents: int = 5000
    byok_max_concurrency: int = 1  # byok_caps의 "동시 1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "monthly_tokens": self.monthly_tokens,
            "daily_ingests": self.daily_ingests,
            "max_effort": self.max_effort,
            "features": sorted(self.features),
            "storage_mb": self.storage_mb,
            "max_documents": self.max_documents,
            "byok_caps": {"max_concurrency": self.byok_max_concurrency},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ServerTier":
        """검증하며 읽는다. 모르는 키, ``TIER_PROVIDERS`` 밖의 프로바이더, ``TIER_FEATURES`` 밖의 기능, 음수는 ``ValueError``."""
        if not isinstance(data, dict):
            raise ValueError("server tier must be an object")
        known = {
            "provider", "model", "monthly_tokens", "daily_ingests", "max_effort", "features",
            "storage_mb", "max_documents", "byok_caps",
        }
        # ``max_themes`` is retired (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 10.1: an account has one personal knowledge base and makes shared ones without a limit). A stored tier and an old client can still have the key: it is ignored.
        unknown = set(data) - known - RETIRED_TIER_FIELDS
        if unknown:
            raise ValueError(f"unknown server tier fields: {sorted(unknown)}")
        default = cls()
        provider = str(data.get("provider", default.provider)).strip().lower()
        if provider not in TIER_PROVIDERS:
            raise ValueError(f"server tier provider must be one of {TIER_PROVIDERS}")
        model = "" if provider == "none" else str(data.get("model", "")).strip()
        max_effort = str(data.get("max_effort", default.max_effort)).strip().lower()
        if provider == "gemini" and model:
            _, embedded_eff = split_gemini_model_and_effort(model)
            if embedded_eff:
                max_effort = embedded_eff
        if max_effort not in EFFORT_ORDER:
            raise ValueError(f"max_effort must be one of {EFFORT_ORDER}")
        features_raw = data.get("features", [])
        if not isinstance(features_raw, (list, tuple)) or not all(isinstance(f, str) for f in features_raw):
            raise ValueError("features must be a list of strings")
        bad = set(features_raw) - set(TIER_FEATURES)
        if bad:
            raise ValueError(f"unknown features: {sorted(bad)}")
        caps = data.get("byok_caps", {})
        if not isinstance(caps, dict) or set(caps) - {"max_concurrency"}:
            raise ValueError("byok_caps must be an object with max_concurrency only")

        def _int(name: str, value: Any, minimum: int) -> int:
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise ValueError(f"{name} must be an integer >= {minimum}")
            return value

        return cls(
            provider=provider,
            model=model,
            monthly_tokens=_int("monthly_tokens", data.get("monthly_tokens", default.monthly_tokens), 0),
            daily_ingests=_int("daily_ingests", data.get("daily_ingests", default.daily_ingests), 0),
            max_effort=max_effort,
            features=frozenset(features_raw),
            storage_mb=_int("storage_mb", data.get("storage_mb", default.storage_mb), 0),
            max_documents=_int("max_documents", data.get("max_documents", default.max_documents), 0),
            byok_max_concurrency=_int(
                "byok_caps.max_concurrency", caps.get("max_concurrency", default.byok_max_concurrency), 1
            ),
        )


@dataclass(frozen=True, slots=True)
class ProviderContext:
    """한 작업이 쓸 프로바이더와 자격. ``api_key``는 repr·비교·직렬화에서 빠진다(§11.3)."""

    account_id: int | None          # None = 호스트 운영자(CLI·시스템). 테마 작업은 반드시 테마 소유 계정을 넣는다
    is_admin: bool                  # True = the server credentials (providers.json, host CLI). A personal key of the server administrator gives False here and True in server_admin
    source: ContextSource
    provider: str                   # "none"이면 LLM 단계가 ProviderNotConfigured로 실패
    model: str
    embed_model: str
    effort: str
    credential_key: str             # 호출 락·백오프 분리 단위. "admin" 또는 "byok:<fingerprint>" 또는 "tier"
    tier: ServerTier = field(default_factory=ServerTier)
    api_key: str = field(default="", repr=False, compare=False)
    feature_models: dict[str, dict[str, str]] = field(default_factory=dict)
    # The account is the server administrator. With a personal key (source "byok", is_admin False) the account keeps all features and has no server tier limits, but the calls use only the personal key.
    server_admin: bool = False

    @property
    def allows_host_cli(self) -> bool:
        return self.is_admin

    def feature_allowed(self, feature: str) -> bool:
        """관리자는 전부, 관리자 전용 기능은 멤버에게 항상 거부, BYOK는 등급 기능 전부, 서버 등급은 ``tier.features``만."""
        if self.is_admin or self.server_admin:
            return True
        if feature in ADMIN_ONLY_FEATURES:
            return False
        if self.source == "byok":
            return feature in TIER_FEATURES
        return self.source == "tier" and feature in self.tier.features


class UsageHooks(Protocol):
    """API 프로바이더가 호출 앞뒤에 부르는 훅. 멤버 컨텍스트에서만 붙는다(관리자는 None).

    L4-a1의 ``GeminiProvider``(LLM·임베딩)와 Gemini STT·VLM이 부르고, L4-a2가 ``accounts.server_tier`` 함수로 만든 구현을 붙인다.
    """

    def before_call(self) -> None:
        """예산이 없으면 ``ProviderBudgetExceeded``를 낸다."""

    def after_call(self, tokens_in: int, tokens_out: int) -> None:
        """응답의 사용량 메타데이터를 원장에 더한다. 메타데이터가 없으면 0을 넘긴다."""


_ADMIN_MODEL_FIELDS: Final[dict[str, tuple[str, str]]] = {
    "gemini": ("gemini_model", "gemini_effort"),
    "antigravity": ("agy_model", "agy_effort"),
    "agy": ("agy_model", "agy_effort"),
    "codex": ("codex_model", "codex_effort"),
    "codex-cli": ("codex_model", "codex_effort"),
}
# 프로바이더를 쓸 수 있는 계정 상태. 정지·삭제 중·소각·모르는 상태는 fail-closed로 거부한다.
_USABLE_ACCOUNT_STATUSES: Final = frozenset({"active", "setup_required"})
_EFFORT_ALIASES: Final[dict[str, str]] = {"off": "none", "0": "none"}


def admin_context(settings: "Settings") -> ProviderContext:
    """Admin or host operator context using providers.json."""
    provider = str(getattr(settings, "provider", "") or "").strip().lower()
    model_field, effort_field = _ADMIN_MODEL_FIELDS.get(provider, ("gemini_model", "gemini_effort"))
    return ProviderContext(
        account_id=None,
        is_admin=True,
        source="admin",
        provider=provider,
        model=str(getattr(settings, model_field, "") or ""),
        embed_model=str(getattr(settings, "gemini_embed_model", "") or ""),
        effort=str(getattr(settings, effort_field, "") or ""),
        credential_key="admin",
        api_key=str(getattr(settings, "gemini_api_key", "") or ""),
    )


def resolve_provider_context(
    account_id: int | None,
    *,
    settings: "Settings",
    db_path: Path | str | None = None,
    auth_db_path: Path | str | None = None,
    secret_service: "SecretService | None" = None,
) -> ProviderContext:
    """§10.2 순서로 컨텍스트를 정한다.

    1. ``account_id``가 None → ``admin_context``.
    1a. 관리자 계정: 개인 키가 있으면 ``source="byok"``, ``server_admin=True`` (2와 같은 키 규칙). 키가 없으면 ``admin_context``.
    2. 멤버, ``store.provider_keys.load_active_key``가 키를 돌려줌 → ``source="byok"``.
    3. 멤버, 키 없음 → ``accounts.server_tier.load_server_tier``의 등급. 등급 프로바이더가 api-key 분류가 아니면 ``none``으로 본다.
    4. 등급 ``none`` → ``source="none"``, ``provider="none"`` (예외는 실제 LLM 호출 지점에서 낸다).

    정지·삭제 중인 계정은 ``ProviderNotAllowed``.
    """
    if account_id is None:
        return admin_context(settings)

    from claire.accounts import server_tier as _server_tier
    from claire.store.db import connect_existing

    db_file = Path(db_path) if db_path is not None else Path(settings.db_file)
    if not db_file.is_file():
        raise ProviderNotAllowed(
            f"account {account_id}: account database is missing", remedy="관리자에게 문의한다."
        )
    conn = connect_existing(db_file, readonly=True)
    try:
        row = conn.execute("SELECT role, status FROM accounts WHERE id=?", (int(account_id),)).fetchone()
        if row is None:
            raise ProviderNotAllowed(f"account {account_id}: not found", remedy="관리자에게 문의한다.")
        role, status = str(row[0]), str(row[1])
        if status not in _USABLE_ACCOUNT_STATUSES:
            raise ProviderNotAllowed(
                f"account {account_id}: status {status!r} cannot use LLM providers",
                remedy="계정이 다시 활성화된 뒤에 작업한다.",
            )
        server_admin = role == "admin"
        # The server tier does not apply to the server administrator account.
        tier = ServerTier() if server_admin else _server_tier.load_server_tier(conn)
    finally:
        conn.close()

    # A registered key that is not usable raises ProviderKeyRejected also for the administrator: no change to the server credentials (C2).
    active = _load_member_key(int(account_id), settings=settings, auth_db_path=auth_db_path,
                              secret_service=secret_service)
    if server_admin and active is None:
        return replace(admin_context(settings), account_id=int(account_id))
    if active is not None:
        key_provider = str(active.provider or "").strip().lower()
        if key_provider not in MEMBER_KEY_PROVIDERS or classify_provider(key_provider) != API_KEY:
            raise ProviderNotAllowed(
                f"account {account_id}: provider {key_provider!r} is not available for member keys",
                remedy="지원하는 프로바이더(gemini)의 키를 다시 등록한다.",
            )
        prefs = _load_member_model_prefs(int(account_id), settings=settings, auth_db_path=auth_db_path, provider=key_provider)
        admin_base_model = str(getattr(settings, "gemini_model", "") or "")
        admin_base_effort = str(getattr(settings, "gemini_effort", "medium") or "medium")
        admin_base_embed = str(getattr(settings, "gemini_embed_model", "gemini-embedding-001") or "gemini-embedding-001")
        resolved_model, resolved_effort = clamp_byok_model_for_feature(prefs.get("model") or admin_base_model, prefs.get("effort") or admin_base_effort, admin_base_model, admin_base_effort)
        feature_models: dict[str, dict[str, str]] = {}
        raw_fm = prefs.get("feature_models") if isinstance(prefs.get("feature_models"), dict) else {}
        admin_routing = getattr(settings, "feature_routing", None) or {}
        resolved_embed = admin_base_embed
        for slot in FEATURE_SLOTS:
            if slot in ADMIN_ONLY_FEATURES:
                continue
            slot_admin = admin_routing.get(slot, {}) if isinstance(admin_routing, dict) else {}
            s_admin_m = str(slot_admin.get("model") or (admin_base_embed if slot == "embed" else admin_base_model) or "")
            s_admin_e = str(slot_admin.get("effort") or ("" if slot == "embed" else admin_base_effort) or "")
            slot_user = raw_fm.get(slot, {}) if isinstance(raw_fm.get(slot), dict) else {}
            u_m = str(slot_user.get("model") or (resolved_embed if slot == "embed" else resolved_model) or s_admin_m)
            u_e = str(slot_user.get("effort") or ("" if slot == "embed" else resolved_effort) or s_admin_e)
            cm, ce = clamp_byok_model_for_feature(u_m, u_e, s_admin_m, s_admin_e)
            if slot == "embed":
                resolved_embed = cm or admin_base_embed
                if slot_user:
                    feature_models[slot] = {"provider": key_provider, "model": resolved_embed, "effort": ""}
            elif slot_user:
                feature_models[slot] = {"provider": key_provider, "model": cm, "effort": ce}
        return ProviderContext(
            account_id=int(account_id),
            is_admin=False,
            source="byok",
            provider=key_provider,
            model=resolved_model or admin_base_model,
            embed_model=resolved_embed or admin_base_embed,
            effort=resolved_effort or admin_base_effort,
            credential_key=f"byok:{active.fingerprint}",
            tier=tier,
            api_key=active.api_key,
            feature_models=feature_models,
            server_admin=server_admin,
        )

    tier_provider = str(tier.provider or "none").strip().lower()
    server_key = str(getattr(settings, "gemini_api_key", "") or "")
    usable = (
        tier_provider in TIER_PROVIDERS
        and tier_provider != "none"
        and classify_provider(tier_provider) == API_KEY
        and bool(server_key)
    )
    if not usable:
        return ProviderContext(
            account_id=int(account_id),
            is_admin=False,
            source="none",
            provider="none",
            model="",
            embed_model="",
            effort="none",
            credential_key="tier",
            tier=tier,
        )
    tier_ctx = ProviderContext(
        account_id=int(account_id),
        is_admin=False,
        source="tier",
        provider=tier_provider,
        model=tier.model or str(getattr(settings, "gemini_model", "") or ""),
        embed_model=str(getattr(settings, "gemini_embed_model", "") or ""),
        effort=str(getattr(settings, "gemini_effort", "") or ""),
        credential_key="tier",
        tier=tier,
        api_key=server_key,
    )
    return replace(tier_ctx, effort=clamp_effort(tier_ctx, tier_ctx.effort))


def _load_member_model_prefs(
    account_id: int,
    *,
    settings: "Settings",
    auth_db_path: Path | str | None,
    provider: str = "gemini",
) -> dict[str, Any]:
    """Load member BYOK model preferences from auth.db if present."""
    from claire.store import auth_db as _auth_db
    from claire.store import provider_keys as _provider_keys

    path = Path(auth_db_path) if auth_db_path is not None else _auth_db.auth_db_path(settings.data_dir)
    if not path.is_file():
        return {}
    conn = _auth_db.connect_auth_db(path)
    try:
        fn = getattr(_provider_keys, "get_key_model_prefs", None)
        return fn(conn, account_id=account_id, provider=provider) if fn is not None else {}
    except Exception:
        return {}
    finally:
        conn.close()


def _load_member_key(
    account_id: int,
    *,
    settings: "Settings",
    auth_db_path: Path | str | None,
    secret_service: "SecretService | None",
):
    """auth.db의 활성 키. auth.db가 아직 없으면 키도 없다.

    등록된 키가 활성 상태가 아니거나(rejected·unverified) 복호화에 실패하면 서버 등급으로 폴백하지 않고 ``ProviderKeyRejected``(C2).
    키가 아예 없을 때만 None(서버 등급으로 간다).
    """
    from claire.secret_scrub import scrub_exception
    from claire.store import auth_db as _auth_db
    from claire.store import provider_keys as _provider_keys

    path = Path(auth_db_path) if auth_db_path is not None else _auth_db.auth_db_path(settings.data_dir)
    if not path.is_file():
        return None
    conn = _auth_db.connect_auth_db(path)
    remedy = "내 계정 > API 키에서 다시 검증하거나 교체하세요"
    try:
        try:
            active = _provider_keys.load_active_key(conn, account_id=account_id, secret_service=secret_service)
        except _provider_keys.ProviderKeyError as exc:
            raise ProviderKeyRejected(
                f"account {account_id}: stored provider key could not be used ({scrub_exception(exc)})",
                remedy=remedy,
            ) from None
        if active is None and _provider_keys.has_key(conn, account_id=account_id):
            raise ProviderKeyRejected(
                f"account {account_id}: registered provider key is not active", remedy=remedy
            )
        return active
    finally:
        conn.close()


def settings_for_context(settings: "Settings", ctx: ProviderContext) -> "Settings":
    """``settings`` 사본에 컨텍스트를 입힌다. 기존 팩토리(``get_provider`` 등)가 이 사본만 받아도 정책을 지키게 한다.

    멤버 컨텍스트 사본: ``provider``·``gemini_api_key``·``gemini_model``·``gemini_embed_model``·effort를 컨텍스트 값으로,
    ``enable_jev=False``, STT·VLM 프로바이더는 해당 기능이 허용될 때만 ``gemini`` 아니면 사용 불가, ``provider_context=ctx``.
    관리자 컨텍스트는 ``provider_context=ctx``만 붙인다.
    """
    if ctx.is_admin:
        copy = settings.model_copy()
        copy.provider_context = ctx
        return copy

    provider = ctx.provider if ctx.provider and classify_provider(ctx.provider) == API_KEY else "none"
    api_key = ctx.api_key if provider != "none" else ""
    effort = clamp_effort(ctx, ctx.effort)
    stt_ok = provider == "gemini" and ctx.feature_allowed("stt")
    vlm_ok = provider == "gemini" and ctx.feature_allowed("vlm")
    update: dict[str, Any] = {
        "provider": provider,
        "gemini_api_key": api_key,
        "gemini_model": ctx.model,
        "gemini_embed_model": ctx.embed_model,
        "gemini_effort": effort,
        "enable_jev": False,
        "jev_api_key": None,
        "telegram_bot_token": "",
        "stt_provider": "gemini" if stt_ok else "none",
        "vlm_provider": "gemini" if vlm_ok else "none",
        "enable_video_transcription": bool(getattr(settings, "enable_video_transcription", False)) and stt_ok,
        "enable_vlm_transcription": bool(getattr(settings, "enable_vlm_transcription", False)) and vlm_ok,
        "presentation_enabled": bool(getattr(settings, "presentation_enabled", False))
        and ctx.feature_allowed("presentation"),
    }
    if ctx.source == "byok" and ctx.feature_models:
        update["feature_routing"] = {
            slot: dict(spec)
            for slot, spec in ctx.feature_models.items()
            if slot not in ADMIN_ONLY_FEATURES and classify_provider(spec.get("provider")) == API_KEY
        }
    else:
        update["feature_routing"] = {}
    for name in ("pdf_paper_effort", "pdf_default_effort", "pdf_classifier_effort"):
        current = str(getattr(settings, name, "") or "")
        if current:
            update[name] = clamp_effort(ctx, current)
    copy = settings.model_copy(update=update)
    copy.provider_context = ctx
    return copy


def context_of(settings: "Settings") -> ProviderContext | None:
    """``settings_for_context``가 붙인 컨텍스트. 없으면 None(관리자·레거시 경로로 취급)."""
    return getattr(settings, "provider_context", None)


def require_provider_allowed(ctx: ProviderContext | None, provider_name: str) -> None:
    """멤버 컨텍스트에서 host-cli 분류 프로바이더를 만들려 하면 ``ProviderNotAllowed``. 팩토리 전부가 부른다."""
    if ctx is None or ctx.allows_host_cli:
        return
    if classify_provider(provider_name) == HOST_CLI:
        raise ProviderNotAllowed(
            f"provider {str(provider_name or '').strip().lower() or '(empty)'!r} is host-only and not available to member accounts",
            remedy="자기 API 키를 등록하거나 관리자에게 서버 등급을 요청한다.",
        )


def require_feature(ctx: ProviderContext | None, feature: str) -> None:
    """``ctx.feature_allowed(feature)``가 아니면 ``ProviderNotAllowed``. ``ctx``가 None이면 통과(관리자 경로)."""
    if ctx is None or ctx.feature_allowed(feature):
        return
    if feature in ADMIN_ONLY_FEATURES:
        remedy = "관리자 전용 기능이다."
    elif ctx.source == "byok":
        remedy = "지원하지 않는 기능이다."
    else:
        remedy = "자기 API 키를 등록하거나 관리자에게 서버 등급에서 이 기능을 켜 달라고 요청한다."
    raise ProviderNotAllowed(f"feature {feature!r} is not allowed for this account", remedy=remedy)


def clamp_effort(ctx: ProviderContext | None, effort: str) -> str:
    """서버 등급 컨텍스트에서는 ``tier.max_effort``를 넘지 않게 낮춘다. 관리자·BYOK는 그대로."""
    if ctx is None or ctx.is_admin or ctx.source != "tier":
        return effort
    ceiling = str(ctx.tier.max_effort or "none").strip().lower()
    if ceiling not in EFFORT_ORDER:
        ceiling = "none"
    raw = str(effort or "").strip().lower()
    raw = _EFFORT_ALIASES.get(raw, raw)
    # 비어 있음(모델 기본값)·모르는 값·숫자 예산은 상한으로 고정한다(fail-closed).
    if raw not in EFFORT_ORDER:
        return ceiling
    return raw if EFFORT_ORDER.index(raw) <= EFFORT_ORDER.index(ceiling) else ceiling


def split_gemini_model_and_effort(model: str | None) -> tuple[str, str | None]:
    """Split a Google AI Studio model identifier into (base_api_model, embedded_effort)."""
    clean = str(model or "").strip()
    if not clean:
        return "", None
    low = clean.lower()
    if low in _GEMINI_MODEL_EFFORTS:
        return clean, None
    for suf in ("none", "minimal", "low", "medium", "high"):
        token = f"-{suf}"
        if low.endswith(token):
            base = clean[: -len(token)]
            base_low = base.lower()
            if base_low in _GEMINI_MODEL_EFFORTS:
                if suf in _GEMINI_MODEL_EFFORTS[base_low]:
                    return base, suf
                return clean, None
            if base_low.startswith("gemini-") and suf in ("low", "medium", "high"):
                return base, suf
    return clean, None


def is_gemini_fixed_effort_model(model: str | None) -> bool:
    """Return True when a Google AI Studio model identifier encodes its reasoning effort."""
    return split_gemini_model_and_effort(model)[1] is not None


def enumerate_gemini_model_variants(base_models: Iterable[str]) -> list[str]:
    """Expand Google AI Studio base models into explicit <model>-<effort> identifiers."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in base_models:
        clean = str(raw or "").strip()
        if not clean:
            continue
        if is_gemini_fixed_effort_model(clean):
            if clean not in seen:
                seen.add(clean)
                out.append(clean)
            continue
        efforts = _GEMINI_MODEL_EFFORTS.get(clean.lower(), ("low", "medium", "high"))
        for eff in efforts:
            variant = f"{clean}-{eff}"
            if variant not in seen:
                seen.add(variant)
                out.append(variant)
    return out


def infer_model_effort_suffix(model: str | None) -> str | None:
    """Return embedded effort suffix (-xhigh/-high/-medium/-low) from a model identifier if present."""
    clean = str(model or "").strip().lower()
    for suf in _AGY_SUFFIX_ORDER:
        if clean.endswith(f"-{suf}"):
            return suf
    return None


def is_agy_fixed_effort_model(model: str | None) -> bool:
    """Return True when an Antigravity CLI model embeds or fixes effort without accepting --effort."""
    clean = str(model or "").strip().lower()
    return infer_model_effort_suffix(clean) is not None or clean.startswith(_AGY_FIXED_PREFIXES)


def supported_efforts_for(provider: str | None, model: str | None = None, *, tier_max_effort: str | None = None) -> tuple[str, ...]:
    """Return selectable reasoning effort levels supported by the given provider and model."""
    prov = str(provider or "").strip().lower()
    mod = str(model or "").strip().lower()
    if prov in ("", "none", "unavailable", "mock"):
        return ()
    if prov == "gemini":
        if is_gemini_fixed_effort_model(mod):
            return ()
        base = _GEMINI_MODEL_EFFORTS.get(mod, ("low", "medium", "high"))
    elif prov in ("antigravity", "agy"):
        base = () if is_agy_fixed_effort_model(mod) else ("low", "medium", "high", "xhigh", "max")
    elif prov == "codex":
        if mod in _CODEX_XHIGH_MODELS or "-codex" in mod or mod.startswith("gpt-5.4"):
            base = ("low", "medium", "high", "xhigh")
        elif mod == "gpt-5":
            base = ("minimal", "low", "medium", "high")
        else:
            base = ("low", "medium", "high")
    else:
        return ()
    if tier_max_effort is None:
        return base
    cap_key = str(tier_max_effort or "none").strip().lower()
    cap_rank = EFFORT_RANKS.get(cap_key, -1)
    return tuple(item for item in base if EFFORT_RANKS.get(item, 99) <= cap_rank)


def describe_ingest_effort_options(settings: Any, *, ctx: ProviderContext | None = None, provider_error: Exception | None = None) -> dict[str, Any]:
    """Describe selectable ingest reasoning effort options for the caller's active extract provider."""
    eff_ctx = ctx if ctx is not None else context_of(settings)
    if provider_error is not None:
        return {"provider": "unavailable", "model": "", "options": [], "default_effort": "", "fixed": True, "fixed_effort": "", "tier_max_effort": ""}

    from ..extract.provider import _resolve_slot_spec

    prov, model, default_effort = _resolve_slot_spec(settings, "extract_graph")
    if eff_ctx is not None and not eff_ctx.is_admin and getattr(eff_ctx, "provider", "") in ("none", ""):
        prov = "none"
    tier_cap = str(eff_ctx.tier.max_effort or "").strip().lower() if (eff_ctx is not None and not eff_ctx.is_admin and eff_ctx.source == "tier") else None

    if prov in ("", "none", "unavailable", "mock"):
        return {"provider": prov or "none", "model": model, "options": [], "default_effort": "", "fixed": True, "fixed_effort": "", "tier_max_effort": tier_cap or ""}

    options = list(supported_efforts_for(prov, model, tier_max_effort=tier_cap))
    if prov == "gemini" and is_gemini_fixed_effort_model(model):
        _, fixed_eff = split_gemini_model_and_effort(model)
        return {"provider": "gemini", "model": model, "options": [], "default_effort": fixed_eff or "", "fixed": True, "fixed_effort": fixed_eff or "", "tier_max_effort": tier_cap or ""}
    if prov in ("antigravity", "agy") and is_agy_fixed_effort_model(model):
        fixed_eff = infer_model_effort_suffix(model) or ""
        return {"provider": "antigravity", "model": model, "options": [], "default_effort": fixed_eff, "fixed": True, "fixed_effort": fixed_eff, "tier_max_effort": tier_cap or ""}

    clean_def = str(default_effort or "").strip().lower()
    if clean_def not in options:
        if tier_cap and clean_def:
            clamped = clamp_effort(eff_ctx, clean_def)
            clean_def = clamped if clamped in options else ""
        else:
            clean_def = ""
    return {"provider": "antigravity" if prov == "agy" else prov, "model": model, "options": options, "default_effort": clean_def, "fixed": len(options) == 0, "fixed_effort": "", "tier_max_effort": tier_cap or ""}


def validate_ingest_effort(settings: Any, effort: str | None, *, ctx: ProviderContext | None = None, allow_mock_standard: bool = False) -> str | None:
    """Validate an explicit ingest/reingest/regenerate effort without silent fallback on unsupported choices."""
    if effort is None or not str(effort).strip():
        return None
    raw = str(effort).strip().lower()
    info = describe_ingest_effort_options(settings, ctx=ctx)
    prov = info["provider"]
    if prov == "mock" and allow_mock_standard:
        allowed_mock = EFFORT_ORDER
        if info["tier_max_effort"]:
            cap_rank = EFFORT_RANKS.get(info["tier_max_effort"], -1)
            allowed_mock = tuple(item for item in EFFORT_ORDER if EFFORT_RANKS.get(item, 99) <= cap_rank)
        if raw not in allowed_mock:
            raise ValueError(f"Unsupported effort '{effort}' for provider 'mock'. Allowed: {', '.join(allowed_mock) or 'none'}")
        return raw
    if info["fixed"]:
        if info["fixed_effort"] and raw == info["fixed_effort"]:
            return raw
        raise ValueError(f"Provider '{prov}' (model '{info['model']}') uses a fixed reasoning level and does not accept effort override '{effort}'.")
    if raw not in info["options"]:
        supported_label = ", ".join(info["options"]) or "default only"
        raise ValueError(f"Unsupported effort '{effort}' for provider '{prov}' (model '{info['model']}'). Selectable options: {supported_label}.")
    return raw
