# Internationalization (i18n) Design for WebUI, Telegram Bot, and CLI

- **Document Identifier**: `DESIGN-I18N-WEBUI-BOT-CLI-20261111`
- **Status**: Draft (Owner decisions ID1–ID4 locked on 2026-11-11; ID1 changed by ID5 and ID6 on 2026-11-13)
- **Date**: 2026-11-11 · Base Commit `c1edbce0`
- **Related Documents**:
  - [`PRINCIPLES.md`](../PRINCIPLES.md)
  - [`INTERNATIONALIZATION_AND_LOCALIZATION_DESIGN.md`](INTERNATIONALIZATION_AND_LOCALIZATION_DESIGN.md) (frozen 2026-09-17 draft)
  - [`PREFERRED_LANGUAGES_DESIGN.md`](PREFERRED_LANGUAGES_DESIGN.md) (content language pipeline)
  - [`ACCOUNTS.md`](../implementation/ACCOUNTS.md), [`API_HTTP_CONTRACT.md`](../implementation/API_HTTP_CONTRACT.md), [`SCHEMA_VERSIONING.md`](../implementation/SCHEMA_VERSIONING.md), [`ENVIRONMENT_VARIABLES.md`](../implementation/ENVIRONMENT_VARIABLES.md)

Evidence tags follow [`PRINCIPLES.md`](../PRINCIPLES.md) F1: **[code]** (confirmed in source at the base commit), **[decision]** (owner policy).

---

## 1. Objective and Scope

### 1.1 Objective

Establish a unified internationalization (i18n) architecture across the Claire Bible WebUI, Telegram Bot, and CLI human output. The system resolves the active locale deterministically for each request, update, or command invocation, and translates interface text from a single JSON catalog source for English (`en`) and Korean (`ko`).

### 1.2 Relation to Earlier Documents and Existing Code

The frozen draft [`INTERNATIONALIZATION_AND_LOCALIZATION_DESIGN.md`](INTERNATIONALIZATION_AND_LOCALIZATION_DESIGN.md) (2026-09-17) combined two different concerns: interface translation and Korean morphological FTS tokenization. Since that draft was written, the codebase changed as follows:

| Subsystem | Fact at Base Commit `c1edbce0` | Status in this Design |
| :--- | :--- | :--- |
| Content language preference | **[code]** `Settings.preferred_languages` (`CLAIRE_PREFERRED_LANGUAGES`) and `Settings.effective_preferred_languages` exist in `src/claire/config.py`. | Unchanged. Controls document fetching, caption selection, and VLM/LLM content language hints only. |
| Morphological FTS tokenizer | **[code]** `SearchTokenizer`, `KoreanKiwiTokenizer`, and `SimpleTokenizer` exist in `src/claire/retrieval/tokenizer.py`. | Already implemented. Out of scope for interface i18n. |
| Multi-account control plane | **[code]** `accounts` table in `claire.db` (v15–v17), `Principal` in `src/claire/api/principal.py`, and `/settings/*` + `/admin/*` in `src/claire/api/accounts_routes/console.py`. | Integrated. Each account stores an optional `locale` preference (`en` or `ko`). |
| Per-account Telegram bots | **[code]** `BotSupervisor` (`src/claire/telegram_supervisor.py`) and `telegram_bot.py` run the instance bot and per-account bots (`account_telegram_bots`). | Integrated. Bot replies resolve locale per Telegram user and bot owner account. |
| Interface translation (`src/claire/i18n/`) | **[code]** Not implemented. Strings are hardcoded in Korean and English across templates, JavaScript files, `console.py`, `telegram_bot.py`, and `cli.py`. | **Core scope of this design.** |

### 1.3 Target Surfaces (Decision ID1)

1. **WebUI**:
   - Server-rendered HTML templates in `src/claire/templates/` (`index.html`, `console_shell.html`, `login.html`, `setup.html`, `share.html`, `explore_accounts.html`, `syndication.html`, `docs.html`).
   - Server-rendered HTML fragments in `src/claire/api/accounts_routes/` (`console.py`, `console_federation_panels.py`, `console_syndication_panels.py`, `features.py`, `keys.py`, `admin.py`, `idp.py`, `invites.py`, `setup.py`, `login.py`).
   - Client-side JavaScript modules in `src/claire/static/js/` (`app.js`, `reader.js`, `manage_panels.js`, `console_account.js`, `console_admin.js`, `console_features.js`, `console_syndication.js`, `syndication_app.js`, `syndication_live.js`, `syndication_peers.js`, `graph/*.js`, `renderers/*.js`).
2. **Telegram Bot**:
   - User-facing replies, inline keyboard labels, status messages, and command refusal notices in `src/claire/telegram_bot.py` and `src/claire/notify.py`.
3. **CLI (Human Output Only)**:
   - Human-readable terminal messages, table headers, prompts, and hints in `src/claire/cli.py`.

### 1.4 Non-Goals and Invariants

- **Agent CLI JSON invariance (`PRINCIPLES.md` C3, C4)**: When `--json` is active, `AgentOutput` on `stdout` keeps its sealed schema, field names, and machine error codes in English. Only human messages written to `stderr` or non-JSON terminal output use localized strings.
- **HTTP API wire-code invariance**: JSON error identifiers (`{"error": "invalid_grant"}`, `{"error": "reauth_required"}`, `{"error": "invalid_profile_public"}`) and protocol payloads (ActivityPub JSON-LD, OAuth 2.1, MCP tool schemas) never change with locale.
- **No external translation SaaS or compilation step (Decision ID4)**: The system does not use GNU gettext `.po`/`.mo` compilation or Crowdin CI workflows.
- **No coupling between UI locale and ingestion language**: Changing an account's UI locale (`accounts.locale`) does not alter `CLAIRE_PREFERRED_LANGUAGES` or FTS indexing behavior.

---

## 2. Owner Decisions

| ID | Topic | Locked Decision | Section |
| :--- | :--- | :--- | :--- |
| ID1 | Target surfaces | Apply i18n to WebUI, Telegram Bot, and CLI human output. Preserve the `--json` `AgentOutput` contract without translation. | §1.3, §8 |
| ID2 | Supported locales and default | Support `ko` (Korean) and `en` (English) in v1. Use `en` as the final fallback locale (`DEFAULT_LOCALE = "en"`). | §3 |
| ID3 | Locale persistence and priority | Resolve locale in order: Account setting (`accounts.locale` + `/settings/account` UI) → Cookie (`claire_locale`) → `Accept-Language` header → `CLAIRE_LANG` environment variable → `"en"`. | §3, §5 |
| ID4 | Catalog format | Use a single set of JSON catalogs (`src/claire/static/locales/{en,ko}.json`) shared directly by Python backend and browser JavaScript without a build step. | §4 |
| ID5 | Order of i18n and federation work (2026-11-13) | Mixed order, chosen for the lowest cost: (1) each new screen and each new message is written with catalog keys from the start, in both catalogs (no later retrofit); (2) the IP units that do not overlap the federation work (FP4) can run at any time; (3) screens that FP4 replaces are not localized: the `/syndication` page and the syndication console panels get their keys when FP4 rebuilds them. This replaces the earlier owner order "implementation first, i18n later" (FEDERATION_REMOTE_READING_DESIGN.md D3). | §10 |
| ID6 | CLI language (2026-11-13) | The CLI is not localized. Its human output is English in ASD-STE100 (Simplified Technical English) for all users. No `--lang` option and no `resolve_cli_locale` wiring. The `--json` `AgentOutput` contract is unchanged. This changes ID1 for the CLI. | §8, §10 |

---

## 3. Locale Model and Deterministic Resolution

### 3.1 Supported Locales and Strict Validation (`PRINCIPLES.md` C1)

```python
# src/claire/i18n/locales.py
from typing import Final, Literal

LocaleCode = Literal["en", "ko"]
SUPPORTED_LOCALES: Final[tuple[LocaleCode, ...]] = ("en", "ko")
SUPPORTED_LOCALE_SET: Final[frozenset[str]] = frozenset(SUPPORTED_LOCALES)
DEFAULT_LOCALE: Final[LocaleCode] = "en"
LOCALE_COOKIE_NAME: Final[str] = "claire_locale"
```

The system distinguishes between **explicit locale selection** and **passive locale negotiation**:

1. **Explicit selection** (`PATCH /api/me`, `POST /api/locale`, CLI `--lang` / `-l`, `Settings.lang` / `CLAIRE_LANG`):
   - Accepts only exact canonical codes in `SUPPORTED_LOCALE_SET` (`"en"` or `"ko"`, case-insensitive ASCII trimmed, or primary tag match `en-*` / `ko-*` normalized to `"en"` / `"ko"` only when specified for negotiation; for explicit API/CLI/config fields, only `"en"` and `"ko"` are valid).
   - Any other non-empty value fails immediately without guessing (`PRINCIPLES.md` C1):
     - HTTP API: returns HTTP 400 `{"error": "invalid_locale"}`.
     - CLI `--lang`: exits with code 2.
     - `Settings.lang` (`CLAIRE_LANG`): raises `ValidationError` at startup.
2. **Passive negotiation** (`Accept-Language` header, `claire_locale` cookie from an older client, POSIX `LC_ALL`/`LC_MESSAGES`/`LANG`, Telegram `language_code`):
   - Extracts the primary subtag (for example, `ko-KR` → `ko`, `ko_KR.UTF-8` → `ko`, `en-US` → `en`).
   - If the subtag is in `SUPPORTED_LOCALE_SET`, selects it.
   - If the subtag is not supported or malformed, ignores that step and continues to the next step in the resolution chain.

### 3.2 Resolution Chains by Surface

```mermaid
flowchart TD
    subgraph WebUI ["1. WebUI & HTTP Request Chain"]
        W1["Authenticated Principal with accounts.locale != NULL"] -->|Match| W_END["Active Locale ('en' | 'ko')"]
        W1 -->|Anonymous or NULL| W2["Cookie 'claire_locale'"]
        W2 -->|Valid in SUPPORTED_LOCALES| W_END
        W2 -->|Missing or unsupported| W3["HTTP Accept-Language (RFC 9110 q-factor)"]
        W3 -->|Highest-q supported match| W_END
        W3 -->|No match| W4["Settings.lang (CLAIRE_LANG)"]
        W4 -->|Set| W_END
        W4 -->|Unset| W5["DEFAULT_LOCALE ('en')"]
        W5 --> W_END
    end

    subgraph Bot ["2. Telegram Bot Chain"]
        B1["Bot owner accounts.locale != NULL"] -->|Match| B_END["Active Locale ('en' | 'ko')"]
        B1 -->|NULL| B2["Telegram User language_code"]
        B2 -->|Supported match| B_END
        B2 -->|Missing or unsupported| B3["Settings.lang (CLAIRE_LANG)"]
        B3 -->|Set| B_END
        B3 -->|Unset| B4["DEFAULT_LOCALE ('en')"]
        B4 --> B_END
    end

    subgraph CLI ["3. CLI Chain"]
        C1["CLI Flag (--lang / -l)"] -->|Valid ('en' | 'ko')| C_END["Active Locale ('en' | 'ko')"]
        C1 -->|Invalid| C_ERR["Fail with Exit Code 2 (C1)"]
        C1 -->|Omitted| C2["Settings.lang (CLAIRE_LANG)"]
        C2 -->|Set| C_END
        C2 -->|Unset| C3["POSIX Env (LC_ALL -> LC_MESSAGES -> LANG)"]
        C3 -->|Supported match| C_END
        C3 -->|Unmatched or C/POSIX| C4["DEFAULT_LOCALE ('en')"]
        C4 --> C_END
    end
```

### 3.3 RFC 9110 `Accept-Language` Parser

`parse_accept_language(header: str | None) -> LocaleCode | None` parses comma-separated language ranges with optional `;q=` weights:
- Valid `q` values range from `0.0` to `1.0` (up to 3 decimal places). A range with `q=0` is excluded.
- Malformed tokens are skipped without raising exceptions.
- Ranges are sorted by descending `q` weight, preserving original left-to-right order on ties.
- For each range, the lowercase primary subtag (`tag.split("-")[0]`) is checked against `SUPPORTED_LOCALE_SET`. The first match is returned.

### 3.4 Async Context Isolation

To prevent cross-request locale leakage in the single-worker async Uvicorn server and multi-bot `BotSupervisor` loop, the active locale lives in a `contextvars.ContextVar`:

```python
# src/claire/i18n/context.py
from __future__ import annotations
import contextvars
from contextlib import contextmanager
from typing import Iterator
from .locales import DEFAULT_LOCALE, LocaleCode, SUPPORTED_LOCALE_SET

_current_locale: contextvars.ContextVar[LocaleCode] = contextvars.ContextVar(
    "claire_current_locale", default=DEFAULT_LOCALE
)

def get_locale() -> LocaleCode:
    return _current_locale.get()

def set_locale(locale: str | None) -> LocaleCode:
    norm = (locale or "").strip().lower()
    resolved: LocaleCode = norm if norm in SUPPORTED_LOCALE_SET else DEFAULT_LOCALE  # type: ignore[assignment]
    _current_locale.set(resolved)
    return resolved

@contextmanager
def use_locale(locale: str | None) -> Iterator[LocaleCode]:
    token = _current_locale.set(
        locale if locale in SUPPORTED_LOCALE_SET else DEFAULT_LOCALE  # type: ignore[arg-type]
    )
    try:
        yield _current_locale.get()
    finally:
        _current_locale.reset(token)
```

---

## 4. Single JSON Catalog Architecture (Decision ID4)

### 4.1 Directory Layout

```text
src/claire/
├── i18n/
│   ├── __init__.py          # Public API: t, ngettext_t, get_locale, set_locale, use_locale, resolve_*
│   ├── catalog.py           # JSON catalog loader, key validator, interpolation engine
│   ├── context.py           # ContextVar management
│   ├── locales.py           # Constants and strict/negotiated validators
│   └── resolver.py          # WebUI, Bot, and CLI locale resolution functions
└── static/
    ├── js/
    │   └── i18n.js          # Browser ClaireI18n runtime (t, applyDom, setLocale)
    └── locales/
        ├── en.json          # Canonical English catalog (SSOT)
        └── ko.json          # Canonical Korean catalog (SSOT)
```

Placing `en.json` and `ko.json` in `src/claire/static/locales/` allows:
1. **Python backend**: Loads `Path(__file__).resolve().parent.parent / "static" / "locales" / f"{locale}.json"` directly into memory once at process startup.
2. **Browser JavaScript**: Fetches `/static/locales/{locale}.json` or reads the server-injected bootstrap payload without any build, transpile, or copy step.

### 4.2 Catalog Key Structure and Interpolation Syntax

Catalogs use flat dot-delimited namespace keys (`"<domain>.<section>.<identifier>"`) mapped to string templates. Flat keys avoid deep object traversal overhead and make key parity checks trivial.

```json
{
  "common.action.save": "Save",
  "common.action.cancel": "Cancel",
  "common.action.logout": "Log out",
  "common.status.loading": "Loading…",
  "nav.topbar.graph": "← Knowledge Graph",
  "nav.topbar.syndication": "📡 Syndication",
  "nav.topbar.console": "⚙️ Settings & Admin Console",
  "console.account.title": "👤 Account · Profile · Security",
  "console.account.locale_label": "Interface language",
  "console.account.locale_auto": "Automatic (browser / system)",
  "console.reauth.title": "🔐 Step-up Authentication",
  "console.reauth.desc": "Enter your current account password to continue (valid for 15 minutes).",
  "bot.error.member_refusal": "This command is available only on the operator bot. Use web settings (/settings).",
  "cli.preflight.header": "claire preflight",
  "doc.count.items": "{count} documents"
}
```

- **Placeholder syntax**: `{name}` (single-brace named placeholders).
  - In Python: formatted safely by replacing `{name}` with `str(params[name])` (using a regex `r"\{([a-zA-Z0-9_]+)\}"` rather than `str.format()` so literal braces in text never raise `KeyError` or `ValueError` or allow attribute access like `{obj.__class__}`).
  - In JavaScript: replaced with the exact same regex `/\{([a-zA-Z0-9_]+)\}/g`.
- **Pluralization**: Handled via explicit `.one` / `.other` keys when English requires singular/plural distinction (for example, `doc.count.one`: `"{count} document"`, `doc.count.other`: `"{count} documents"`), resolved by `tn(base_key: str, count: int, **params: Any) -> str`. In Korean, `ko.json` maps either `doc.count.other` (or both keys) to `"{count}개 문서"`.
- **Fallback rule**:
  1. Look up `key` in the active locale catalog (`ko` or `en`).
  2. If missing in `ko`, fall back to `en.json` (`DEFAULT_LOCALE`).
  3. In tests (`tests/test_i18n_catalog.py`), a strict parity gate asserts that `set(en.keys()) == set(ko.keys())` and that every `{placeholder}` in `en.json` matches `ko.json` identically.

---

## 5. Database Schema and Account API Contract (Decision ID3)

### 5.1 `claire.db` Migration (`accounts.locale`)

`accounts` in `data/claire.db` gets one nullable column `locale`:

```sql
ALTER TABLE accounts ADD COLUMN locale TEXT CHECK (locale IS NULL OR locale IN ('en', 'ko'));
```

- `NULL` (default for existing and new accounts) means **automatic negotiation** (falls through to cookie → `Accept-Language` → `CLAIRE_LANG` → `"en"`).
- `"en"` or `"ko"` pins the account's preferred interface language across browsers, sessions, and the account's Telegram bot.
- Migration follows `PRINCIPLES.md` A3 and `SCHEMA_VERSIONING.md`:
  - Uses `_ensure_column(conn, "accounts", "locale", "TEXT")` inside an explicit transaction.
  - Note on version numbering: `claire.db` is currently at `SCHEMA_VERSION = 17` (`c1edbce0`), and Federation Phase FP3-1 also plans a schema version bump. The i18n migration uses the next sequential `SCHEMA_VERSION` at implementation time and updates `SCHEMA_VERSIONING.md`.

### 5.2 `Account` Model and `AccountStore` Contract

In `src/claire/store/accounts.py`:
- `Account` dataclass adds `locale: LocaleCode | None = None`.
- `_ACCOUNT_COLUMNS` adds `locale`.
- `AccountStore.update_locale(account_id: int, locale: str | None, *, now: float | None = None) -> Account`:
  - If `locale is None` or `locale == ""`: sets `locale = NULL`.
  - If `locale in ("en", "ko")`: sets `locale = locale`.
  - Otherwise: raises `ValueError("invalid_locale")` (`PRINCIPLES.md` C1).

### 5.3 Principal and Authentication Binding

In `src/claire/api/principal.py` and `src/claire/api/security.py`:
- `Principal` adds `locale: LocaleCode | None = None`.
- `_bind_account` in `src/claire/api/security.py` already loads `account = accounts_store.AccountStore(conn).get(account_id)` on every authenticated request. It copies `account.locale` into `_Credential` and `Principal` with zero additional database queries.

### 5.4 REST API Endpoints

1. **`GET /api/me` (`src/claire/api/accounts_routes/me.py`)**:
   - Adds two fields to `me_payload`:
     - `"locale"`: `account.locale` (`"en"`, `"ko"`, or `None` when automatic).
     - `"effective_locale"`: `get_locale()` (`"en"` or `"ko"`).
2. **`PATCH /api/me` (`src/claire/api/accounts_routes/me.py`)**:
   - Accepts optional `"locale"` key in the JSON body alongside `"display_name"` and `"profile_public"`.
   - Valid values: `"en"`, `"ko"`, `None`, or `""` (resets to `NULL`).
   - Any other value (for example, `"ja"`, `123`, `true`) returns HTTP 400 `{"error": "invalid_locale"}`.
   - Does **not** require step-up authentication (unlike `profile_public` 0 → 1 when federation is `on`).
   - When `"locale"` is set to `"en"` or `"ko"`, the response also sets the `claire_locale` cookie; when reset to `None` / `""`, it clears the `claire_locale` cookie.
3. **`POST /api/locale` (Public / Anonymous Locale Switch)**:
   - Route rule in `ROUTE_POLICY` (`src/claire/api/security.py`): `("POST", "/api/locale"): _system("public")`, guarded by `same_origin(request)` (CSRF protection).
   - Accepts `{"locale": "en" | "ko" | null | ""}`.
   - Validates the value strictly (`400 invalid_locale` on unknown strings).
   - Sets or clears the `claire_locale` cookie (`Path=/; Max-Age=31536000; SameSite=Lax`, plus `Secure` when `config.secure_cookie` is true; not `HttpOnly` so client-side JS can read it if needed, or `HttpOnly=False`).
   - If the caller is authenticated (`not principal.is_anonymous` and `principal.scope == "owner"`), also updates `accounts.locale` so the choice persists on the account.
   - Returns `200 OK` with `{"locale": ..., "effective_locale": ...}`.

---

## 6. WebUI Integration Architecture

### 6.1 ASGI Middleware Placement

`I18nMiddleware` runs inside `AuthenticationMiddleware` in `wrap_web_app` (`src/claire/api/security.py`):

```text
SafeAccessLog -> (CloudflareIPFilter) -> HostRouter -> PrefixParser -> CORS -> BodyLimit -> ErrorBoundary -> Authentication -> I18nMiddleware -> NamespaceResolver -> Starlette Router
```

Why this position:
- `AuthenticationMiddleware` has already populated `request.state.claire_principal` (including `principal.locale`).
- `I18nMiddleware` resolves the request locale using `(principal.locale, cookie "claire_locale", header "accept-language", settings.lang)` and calls `set_locale(locale)`.
- On the outbound response, `I18nMiddleware` sets `Content-Language: <locale>`.

### 6.2 Server-Side Template and Console Rendering

1. **`render_template` (`src/claire/api/accounts_routes/common.py`)**:
   - Automatically injects `html_lang=get_locale()` and any `$t_<key>` placeholders into `string.Template` substitutions.
   - Replaces hardcoded `<html lang="ko">` in `console_shell.html`, `login.html`, `setup.html`, `share.html`, `explore_accounts.html`, `syndication.html`, `prototype_syndication.html`, and `docs.html` with `<html lang="$html_lang">`.
2. **Graph Workspace (`index.html` in `src/claire/api/server.py`)**:
   - Sets `<html lang="{locale}">` and injects `<meta name="claire-locale" content="{locale}"/>`.
   - Embeds `<script id="claire-i18n-catalog" type="application/json">{catalog_json}</script>` (or loads `/static/js/i18n.js` + `/static/locales/{locale}.json`) so initial paint and watchdog (`window.__CLAIRE_CLEAR_LOADING`) have immediate synchronous access to localized strings without network race conditions.
3. **Unified Console (`src/claire/api/accounts_routes/console.py`)**:
   - `ConsoleSectionSpec` stores translation keys (`group_label_key`, `nav_label_key`, `title_key`, `badge_key`, `description_key`) resolved at render time via `t(...)`.
   - `console-bootstrap` JSON (`<script id="console-bootstrap" type="application/json">`) includes `"locale": account.locale` and `"effective_locale": get_locale()`.
   - `/settings/account` adds the **Interface Language** (`#pref-account-locale`) selector (`Automatic`, `English (en)`, `한국어 (ko)`).

### 6.3 Client-Side JavaScript Runtime (`src/claire/static/js/i18n.js`)

```javascript
// src/claire/static/js/i18n.js
(function (global) {
  'use strict';
  var catalog = {};
  var currentLocale = document.documentElement.getAttribute('lang') || 'en';

  var inlineEl = document.getElementById('claire-i18n-catalog');
  if (inlineEl) {
    try { catalog = JSON.parse(inlineEl.textContent || '{}'); } catch (_) {}
  }

  function t(key, params) {
    var template = Object.prototype.hasOwnProperty.call(catalog, key) ? catalog[key] : key;
    if (!params) return template;
    return String(template).replace(/\{([a-zA-Z0-9_]+)\}/g, function (match, name) {
      return Object.prototype.hasOwnProperty.call(params, name) && params[name] != null
        ? String(params[name])
        : match;
    });
  }

  function applyDom(root) {
    var scope = root || document;
    scope.querySelectorAll('[data-i18n]').forEach(function (el) {
      el.textContent = t(el.getAttribute('data-i18n'));
    });
    scope.querySelectorAll('[data-i18n-title]').forEach(function (el) {
      el.setAttribute('title', t(el.getAttribute('data-i18n-title')));
    });
    scope.querySelectorAll('[data-i18n-placeholder]').forEach(function (el) {
      el.setAttribute('placeholder', t(el.getAttribute('data-i18n-placeholder')));
    });
    scope.querySelectorAll('[data-i18n-aria-label]').forEach(function (el) {
      el.setAttribute('aria-label', t(el.getAttribute('data-i18n-aria-label')));
    });
  }

  global.ClaireI18n = {
    t: t,
    applyDom: applyDom,
    getLocale: function () { return currentLocale; }
  };
})(window);
```

Embedding `<script id="claire-i18n-catalog" type="application/json">` in the HTML response guarantees:
- **Zero FOUC (Flash of Untranslated Content)**: `ClaireI18n.t()` works synchronously during initial script execution before any `fetch()` completes.
- **Offline / Watchdog Resilience**: Even if the network stalls, `window.__CLAIRE_CLEAR_LOADING` in `index.html` displays localized fallback text.

---

## 7. Telegram Bot Integration Architecture

### 7.1 Per-Update Locale Resolution

In `src/claire/telegram_bot.py`:
- Every bot instance created by `build_app(settings, profile=...)` knows its `account_id` (`profile.account_id`, or `ADMIN_ACCOUNT_ID` for the instance bot).
- When a Telegram `Update` arrives, the handler resolves the locale in order:
  1. The bot owner account's `accounts.locale` (if not `NULL`).
  2. `update.effective_user.language_code` (for example, `"ko"`, `"en-US"` → `"en"`), if supported.
  3. `settings.lang` (`CLAIRE_LANG`), if set.
  4. `DEFAULT_LOCALE` (`"en"`).
- The handler enters `with use_locale(resolved_locale):` around the update processing coroutine so all calls to `t(...)` inside `telegram_bot.py` and `notify.py` use that locale in the current async task context.

### 7.2 Push Notifications (`notify.py` and OAuth / Login Approval)

When the API server sends a push notification via Telegram (for example, login approval or OAuth client approval in `src/claire/api/accounts_routes/login.py` and `src/claire/api/oauth.py`):
- Resolves the target account's `accounts.locale` → `settings.lang` → `DEFAULT_LOCALE` (`"en"`).
- Renders message text and inline button labels (`Approve` / `Deny`) via `t(..., locale=target_locale)`.

---

## 8. CLI Integration Architecture

**ID6 (2026-11-13) replaces Section 8.1.** The CLI has one language: English in ASD-STE100. Section 8.1 is kept for reference only; `resolve_cli_locale` in `i18n/resolver.py` is not wired to the CLI. Rules for the CLI text:

1. Each human output line, error message, help text, and prompt of `cli.py` and `ops/cb_manuscript.py` is English in ASD-STE100 (approved words, one instruction per sentence, active voice, sentences of 20 words or fewer for procedures).
2. The CLI does not use the JSON catalogs.
3. The `--json` `AgentOutput` contract (Section 8.2) does not change; machine-parsed keys and error codes stay as they are.
4. Existing Korean CLI text changes to ASD-STE100 English in unit IP3-2. New CLI text is ASD-STE100 English from the start.

### 8.1 Global `--lang` / `-l` Option and Environment Resolution (replaced by ID6)

In `src/claire/cli.py`:
- The root parser and subcommand parent parser accept `--lang` / `-l` (`choices=("en", "ko")`).
- Following `PRINCIPLES.md` C1, an unsupported `--lang` argument causes `argparse` (and `with_agent_contract`) to fail immediately with exit code 2.
- At the start of `main(argv)`, before executing the subcommand handler:
  ```python
  locale = resolve_cli_locale(
      cli_lang=getattr(args, "lang", None),
      settings_lang=settings.lang,
      environ=os.environ,
  )
  set_locale(locale)
  ```
- `preflight` output (`claire preflight`) adds a diagnostic line:
  ```text
  ui locale         : en (source=default)
  preferred langs   : ko, en
  ```

### 8.2 Preservation of Agent CLI Contract (`PRINCIPLES.md` C3, C4)

- When `--json` is passed:
  - `AgentOutput` fields (`status`, `command`, `data`, `error.code`, `error.reason`, `meta`) remain untouched.
  - Machine-parsed keys in `data` are never translated.
  - Human-oriented diagnostic strings printed to `stderr` may use `t()`.

---

## 9. Configuration (`src/claire/config.py` and `.env.example`)

### 9.1 `Settings` Field

```python
# src/claire/config.py
lang: str = Field(
    default="",
    alias="CLAIRE_LANG",
    description="Instance default UI/CLI/Bot locale ('en' or 'ko'; empty uses fallback 'en')",
)

@field_validator("lang")
@classmethod
def _validate_lang(cls, v: str) -> str:
    cleaned = (v or "").strip().lower()
    if cleaned not in {"", "en", "ko"}:
        raise ValueError("CLAIRE_LANG must be 'en', 'ko', or empty")
    return cleaned
```

- When `CLAIRE_LANG` is empty (`""`, the default), the resolution chain falls through to `DEFAULT_LOCALE = "en"`.
- When `CLAIRE_LANG=ko` or `CLAIRE_LANG=en`, it acts as the instance-wide fallback before `DEFAULT_LOCALE`.
- `docs/implementation/ENVIRONMENT_VARIABLES.md`, `.env.example`, and `.env.dev.example` are updated in the same commit as the `Settings.lang` addition so `tests/test_docs_consistency.py` passes (`PRINCIPLES.md` F1).

---

## 10. Phases, Work Units, and Verification Plan

### 10.1 Phases

| Phase | Scope | Completion Criteria |
| :--- | :--- | :--- |
| **IP1** Core runtime, schema, and account locale contract | `src/claire/i18n/` package, `static/locales/{en,ko}.json` foundation, `Settings.lang` (`CLAIRE_LANG`), `claire.db` `accounts.locale` migration, `GET/PATCH /api/me`, `POST /api/locale`, `I18nMiddleware`, `i18n.js` | Deterministic locale resolution across all priority chains; strict rejection of invalid locales (`400 invalid_locale`, exit code 2); catalog parity test passing. |
| **IP2** WebUI localization | Templates (`index.html`, `console_shell.html`, `login.html`, `setup.html`, `share.html`, `explore_accounts.html`, `syndication.html`, `docs.html`), server-rendered console panels (`console.py`, `features.py`, `keys.py`, `admin.py`, `federation`), client JS modules, `/settings/account` language selector | Switching locale between `en` and `ko` in `/settings/account` or via cookie renders all WebUI pages and console panels in the selected language with `<html lang="...">` and `Content-Language`. |
| **IP3** Telegram Bot and CLI localization | `telegram_bot.py`, `notify.py`, `cli.py` (`--lang` flag, human output messages), implementation docs update, E2E test suite | Bot replies in the resolved locale; CLI human output respects `--lang` and `CLAIRE_LANG` while `--json` `AgentOutput` stays invariant; full `./scripts/ci.sh` passes. |

### 10.2 Work Units

Each unit ends with passing tests and one commit, following the repository work-unit convention (`PROJECT.md`).

| Unit | Scope | Completion Criteria | After | Size |
| :--- | :--- | :--- | :--- | :--- |
| **IP1-1** | `src/claire/i18n/` (`locales.py`, `context.py`, `catalog.py`, `resolver.py`), initial `src/claire/static/locales/{en,ko}.json`, `Settings.lang` (`CLAIRE_LANG`), env docs sync | Unit tests for resolver priority chains, RFC 9110 `Accept-Language` q-factors, placeholder interpolation, and catalog parity pass. `test_docs_consistency.py` passes. | none | S |
| **IP1-2** | `claire.db` migration for `accounts.locale`, `AccountStore.update_locale`, `Principal.locale` binding in `security.py`, `GET/PATCH /api/me` locale contract, `POST /api/locale`, `I18nMiddleware` | Migration idempotence test passes. `PATCH /api/me` and `POST /api/locale` persist `en`/`ko`/`null`, set `claire_locale` cookie, and reject invalid values with 400 `invalid_locale`. | IP1-1 | M |
| **IP2-1** | `src/claire/static/js/i18n.js`, inline catalog injection, `/settings/account` language selector UI (`console.py` + `console_account.js`), auth pages (`login.html`, `setup.html`) | User can switch language in `/settings/account` and on `/login`; `<html lang>` and `Content-Language` reflect `en` or `ko`. | IP1-2 | M |
| **IP2-2** | Unified console localization (`console_shell.html`, `console.py`, `features.py`, `keys.py`, `admin.py`, `console_federation_panels.py`, `manage_panels.js`, `console_*.js` except `console_syndication.js`). ID5: the syndication panels (`console_syndication_panels.py`, `console_syndication.js`) get their keys in FP4. | All `/settings/*` and `/admin/*` screens render in `en` and `ko` without hardcoded Korean strings in `en` mode. | IP2-1 | L |
| **IP2-3** | Graph workspace, reader, share, and explore UI localization (`index.html`, `share.html`, `explore_accounts.html`, `app.js`, `reader.js`, `graph/*.js`). ID5: `/syndication` (`syndication.html`, `syndication_*.js`) gets its keys when FP4-5 rebuilds it. | Main workspace `/`, `/share/*`, and `/explore` render in `en` and `ko`. | IP2-1 | L |
| **IP3-1** | Telegram Bot and push notification localization (`telegram_bot.py`, `notify.py`) | Instance and member bots resolve locale from owner `accounts.locale` and Telegram `language_code`; replies and push prompts localize in `en` and `ko`. | IP1-2 | M |
| **IP3-2** | ID6: CLI human output, errors, help, and prompts in ASD-STE100 English (`cli.py`, `ops/cb_manuscript.py`); no `--lang` option; `--json` `AgentOutput` contract unchanged | No Korean text in the human output, help, and errors of `claire` and `cb-manuscript`; `test_agent_cli.py` passes unchanged. | — | M |
| **IP3-3** | End-to-end i18n test (`tests/test_e2e_i18n.py`), implementation docs (`ACCOUNTS.md`, `API_HTTP_CONTRACT.md`, `SCHEMA_VERSIONING.md`, `ENVIRONMENT_VARIABLES.md`, `openapi.yaml`), full `./scripts/ci.sh` gate | E2E test verifies default `en`, `Accept-Language: ko`, `/settings/account` persistence, and logout fallback. `./scripts/ci.sh` passes with 0 failures. | IP2-2, IP2-3, IP3-1, IP3-2 | M |

### 10.3 Testing Policy Alignment

Following the repository testing rules:
1. **Interfaces between modules (`tests/test_i18n_resolver.py`, `tests/test_i18n_api.py`)**:
   - Verify contract boundaries between `I18nMiddleware`, `AuthenticationMiddleware` (`Principal.locale`), `AccountStore.update_locale`, `GET/PATCH /api/me`, and `POST /api/locale`.
2. **Input/output (I/O) operations (`tests/test_i18n_catalog.py`, `tests/test_schema_i18n.py`)**:
   - Verify loading of `en.json` and `ko.json`, key/placeholder parity between catalogs, and idempotent SQLite schema migration for `accounts.locale` (including account backup/restore round-trip).
3. **Calls to external libraries**:
   - Starlette cookie/header parsing and Telegram `Update` `effective_user.language_code` binding.
4. **End-to-End test (`tests/test_e2e_i18n.py`)**:
   - Verify the complete user journey across anonymous negotiation (`en` default vs `Accept-Language: ko-KR`), authenticated preference change in `/settings/account`, console/workspace rendering, and CLI `--lang` vs `--json` invariance.
