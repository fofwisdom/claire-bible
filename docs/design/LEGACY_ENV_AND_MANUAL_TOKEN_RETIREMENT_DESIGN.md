# Retirement of CLAIRE_ANONYMOUS_READONLY, Legacy Environment Variables, and Manual API Tokens

- **Document Identifier**: `DESIGN-ENV-RETIREMENT-20261124`
- **Status**: Implemented (owner, 2026-11-24). Units `ENV-1` to `ENV-4` are implemented; see [`ENVIRONMENT_VARIABLES.md`](../implementation/ENVIRONMENT_VARIABLES.md), [`API_AND_MCP_GUIDE.md`](../implementation/API_AND_MCP_GUIDE.md), [`API_HTTP_CONTRACT.md`](../implementation/API_HTTP_CONTRACT.md), and [`SCHEMA_VERSIONING.md`](../implementation/SCHEMA_VERSIONING.md) for current runtime behavior.
- **Date**: 2026-11-24
- **Related**: [`API_V1_AND_REFERENCE_DESIGN.md`](API_V1_AND_REFERENCE_DESIGN.md), [`AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md`](AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md) (`AE3`), [`PROVIDER_SETTINGS_SIMPLIFICATION_DESIGN.md`](PROVIDER_SETTINGS_SIMPLIFICATION_DESIGN.md) (`PS3`), [`MARKDOWN_RETIREMENT_DESIGN.md`](MARKDOWN_RETIREMENT_DESIGN.md) (`RD23`), [`ACCOUNT_PREFERRED_LANGUAGES_DESIGN.md`](ACCOUNT_PREFERRED_LANGUAGES_DESIGN.md), [`ENVIRONMENT_VARIABLES.md`](../implementation/ENVIRONMENT_VARIABLES.md), [`PRINCIPLES.md`](../PRINCIPLES.md)

## 1. Scope and Locked Owner Decisions (2026-11-24)

`CLAIRE_ANONYMOUS_READONLY` was introduced as a server-wide kill switch when Claire Bible supported only a single theme and no user accounts. With multi-account namespaces, per-knowledge-base `is_public` flags, per-document `hidden` flags, permalinks (`link_access`), and OAuth 2.1 in place, maintaining a server-wide anonymous read switch, dead environment variables from retired subsystems, and legacy manual `cb_...` tokens adds complexity and masks real per-resource access bugs.

The owner locked decisions `ER1` through `ER9` on 2026-11-24:

| ID | Decision | Reason |
| :--- | :--- | :--- |
| `ER1` | **Remove `CLAIRE_ANONYMOUS_READONLY`**: Permanently delete the server-wide anonymous read kill switch from `Settings`, `WebRuntimeConfig`, `AuthenticationMiddleware`, `cb_manuscript`, `.env*`, and `ENVIRONMENT_VARIABLES.md`. Resource-level settings (`knowledge_bases.is_public`, `documents.hidden`, `accounts.profile_public`, permalink `link_access`, `CLAIRE_ROBOTS_POLICY`, `CLAIRE_SITEMAP_ENABLED`) become the sole visibility authority. | Eliminates single-theme era configuration that contradicts multi-account per-KB visibility. |
| `ER2` | **Automatic Upgrade Migration (`claire.db` Schema v48)**: During idempotent migration to schema v48 (following the pattern of schema v46 for `CLAIRE_PREFERRED_LANGUAGES`), read `CLAIRE_ANONYMOUS_READONLY` from the process environment and `.env`/`.env.dev` once. If `"0"`, automatically set all personal KBs to private (`is_public = 0`, incrementing `visibility_epoch`), keep shared KBs public (`kind_violation_reason` requires `is_public = 1` for shared KBs) while logging a warning if shared KBs exist, and comment out `CLAIRE_ANONYMOUS_READONLY` in `.env`/`.env.dev`. If `"1"` or absent, leave KB rows untouched and comment out the variable. | Guarantees that private installations running with `CLAIRE_ANONYMOUS_READONLY=0` never become silently public on upgrade. |
| `ER3` | **`access="read"` Route Hardening and Dead-Branch Cleanup**: Elevate `GET`/`HEAD /syndication` from `_system("read", "read")` to `_system("owner", "none")` to close single-mode anonymous exposure. Align account-required `read` routes (`/api/syndication/dispatches`, `/api/auto-rule`, `/api/reputation`, `/submissions`, `/api/me/pair-review/*`) with `ROUTE_POLICY`. Replace the `anonymous_readonly` branch in `graph_ui` (`?pwa=1`) with an admin personal KB `is_public` check, unify magic-link failure redirects in `security.py`, remove `anonymous_readonly` and unreachable `get_kb_by_fqdn` branches in `robots_txt_route` and `sitemap_xml_route`, and update `claire preflight` to report DB KB visibility counts. | Prevents unauthenticated exposure on routes that relied on `CLAIRE_ANONYMOUS_READONLY=0` and removes dead legacy routing code. |
| `ER4` | **Remove Tier 1 Dead / No-Op Env Vars and Dead `Settings` Properties**: Delete 11 dead or single-value variables (`CLAIRE_SITE_DESCRIPTION`, `CLAIRE_EXPAND_CHAR_BUDGET`, `CLAIRE_RESEARCH_CONTEXT_BUDGET`, `CLAIRE_PDF_DETECT_ENCODING_FLAWS`, `CLAIRE_PDF_FLAW_ESCALATE_DOCLING`, `CLAIRE_PDF_PARSER`, `CLAIRE_RENDER_FORMAT`, `CLAIRE_VECTOR_BACKEND`, `CLAIRE_VAULT_PATH`, `CB_VAULT_DIR`, and `CLAIRE_PREFERRED_LANGUAGES` from templates and CLI warning while retaining historical v46 DB upgrade fallback in `store/db.py`), 9 zero-caller `@property` aliases in `config.py`, and 3 already-migrated uncommented lines in `.env.example` (`CLAIRE_EXPAND_MAX`, `CLAIRE_AUTO_EXPAND`, `CLAIRE_WATCH_INTERVAL_DAYS`). Preserve `CLAIRE_PRESENTATION_PDF_MAX_BYTES`, which `plugins/ingest_adapter/claire.vmware_explore/adapter.py` reads at runtime. | Removes configuration knobs that no code reads or that are locked to one value, preventing operator confusion. |
| `ER5` | **Remove Tier 2 Legacy Aliases and `_ExactDotEnvSettingsSource`**: Delete `CLAIRE_PUBLIC_URL` (`legacy_public_url`, superseded by `CLAIRE_FQDN`), `CLAIRE_CLOUDFLARE_ONLY` (superseded by `CLAIRE_CLOUDFLARE_IPS_ONLY`), `STT_PROVIDER`/`STT_PROIVDER`/`CLAIRE_STT_PROVIDER` (aligned with `PROVIDER_SETTINGS_SIMPLIFICATION_DESIGN.md` `PS3`), and the one-time `CLAIRE_MEDIA_S3_*` import in `src/claire/store/media_storage.py` (superseded by `/admin/storage`). Together with `ER1`, delete `_validate_anonymous_readonly_dotenv` and `_ExactDotEnvSettingsSource` in `src/claire/core/config.py`. | Simplifies `Settings` source loading and removes superseded aliases. |
| `ER6` | **Remove Tier 3 Policy Env Vars**: Delete `CLAIRE_MULTI_THEME` (multi-KB mode is always enabled across all servers; remove single-theme lock checks and `_load_single_mode_theme0()`), `CLAIRE_PRESENTATION_ENABLED` (per-KB `knowledge_bases.presentation_enabled` DB column is the sole setting), and `CLAIRE_SORCERER` (resolve from `knowledge_bases.sorcerer` or the owning account's `display_name`). | Completes the transition from global single-theme env switches to per-KB and per-account DB state. |
| `ER7` | **Total Retirement of Legacy Manual Tokens Across All Services**: Remove `CLAIRE_INJECT_TOKEN`, `CLAIRE_READONLY_TOKEN`, `CLAIRE_MANUAL_TOKENS_RETIRED`, `src/claire/store/token_store.py`, DB token wrappers, `WebRuntimeConfig.owner_token`/`readonly_token`, `/tokens*` HTTP endpoints, `/account/tokens` and `/admin/tokens` console UI pages, `claire token` CLI subcommand, Telegram `/token` command, `cb_manuscript` token generation/preflight checks, and `api:<id>`/`env:*` MCP telemetry branches. Rename any existing `data/auth/api_tokens.json` to `data/auth/api_tokens.json.retired` on startup without reading it. | Establishes OAuth 2.1 (`oauth_tokens`) and Same-Origin sessions (`auth_sessions`) as the only credentials across all services. |
| `ER8` | **Telegram Bot Internal Bundle Delegation Without `CLAIRE_INJECT_TOKEN`**: Replace `Authorization: Bearer {settings.inject_token}` in `src/claire/telegram/bot.py` (`POST /support/bundle` and `POST /support/graph-bundle`) with an ephemeral account-bound `X-Session` token (TTL 60 seconds, written to `auth_sessions` in `claire.db` for the requesting `account_id`, revoked in `finally`). | Enables Telegram bot bundle delegation for both admin server bundles and per-account bundles without a static owner token. |
| `ER9` | **Test Suite Auth Helper Migration**: Add a shared `tests/auth_helpers.py` that issues real account-bound OAuth 2.1 access tokens or `auth_sessions` (`owner`, `write`, `readonly`) and migrate the ~85 test files referencing `anonymous_readonly`, `inject_token`, or `readonly_token`. | Keeps test coverage exercising real authentication and resource visibility paths. |

## 2. Complete Code-Verified Inventory (`[code]`)

### 2.1 `CLAIRE_ANONYMOUS_READONLY` Removal Targets

| Area | File and Location | Current Behavior | Replacement Behavior |
| :--- | :--- | :--- | :--- |
| Config & DotEnv | `src/claire/core/config.py:24-149`, `:577`, `:841-852` | `_validate_anonymous_readonly_dotenv`, `_ExactDotEnvSettingsSource`, `anonymous_readonly` field | Delete field, validator, and `_ExactDotEnvSettingsSource` (after `CLAIRE_PUBLIC_URL` removal in `ER5`). |
| Auth Middleware | `src/claire/api/security.py:1104-1206`, `:2151-2198`, `:2422-2435` | Guards `auth_scope = "anonymous"` fallback on `access == "read"` and magic-link failure `404` vs `302 /` | Always allow `anonymous` on `access == "read"` when `path != "/mcp"`, `origin_kind in {"none", "same"}`, and `can_fallback_anonymous`; always redirect `302 /` (clearing cookie) on magic-link failure. |
| Syndication & Account Read Routes | `src/claire/api/security.py:407-416`, `src/claire/api/server.py:4517-4553` | `GET /syndication` has `access="read"` and permits anonymous access in single mode | Elevate `GET`/`HEAD /syndication` to `_system("owner", "none")`; align account-scoped `read` routes (`/api/syndication/dispatches`, `/api/auto-rule`, `/api/reputation`, `/submissions`, `/api/me/pair-review/*`) with `ROUTE_POLICY`. |
| Network `?pwa=1` Root | `src/claire/api/server.py:4170-4181` | Redirects unauthenticated `?pwa=1` to `/@{admin}/?pwa=1` when `anonymous_readonly=True`, else `/login` | Check admin's personal KB `is_public`: redirect to `/@{admin}/?pwa=1` if public, else `/login`. |
| `robots.txt` & `sitemap.xml` | `src/claire/api/server.py:4839-4940` | Checks `not anonymous_readonly` and dead `get_kb_by_fqdn(req_host)` branch | Remove `anonymous_readonly` and dead `get_kb_by_fqdn` checks; evaluate `CLAIRE_ROBOTS_POLICY`, `CLAIRE_SITEMAP_ENABLED`, and KB `is_public` only. |
| Preflight & Ops | `src/claire/cli.py:54-59`, `ops/cb_manuscript.py` | Enforces exact `0\|1` in `.env`/`.env.dev` and prints `ENABLED (full knowledge base is public)` | Remove `ANONYMOUS_READONLY_KEY` parsing/backfill in `cb_manuscript.py`; `claire preflight` prints DB public/private KB summary. |
| Templates & Locales | `.env.example`, `.env.dev.example`, `src/claire/i18n/locales/{ko,en}.json` (`console.synd.mcp.auth_desc`) | Documents `CLAIRE_ANONYMOUS_READONLY` in examples and MCP auth copy | Remove from `.env*` examples; update `console.synd.mcp.auth_desc` to remove `CLAIRE_ANONYMOUS_READONLY` reference. |

### 2.2 Tier 1 Dead / No-Op Env Vars and Dead `Settings` Properties

| Category | Names | Current Locations | Action |
| :--- | :--- | :--- | :--- |
| Zero-caller / no-op vars (5) | `CLAIRE_SITE_DESCRIPTION`, `CLAIRE_EXPAND_CHAR_BUDGET`, `CLAIRE_RESEARCH_CONTEXT_BUDGET`, `CLAIRE_PDF_DETECT_ENCODING_FLAWS`, `CLAIRE_PDF_FLAW_ESCALATE_DOCLING` | `config.py`, `ingest_limit_manager.py`, `.env.example`, `manage_panels.js`, `ENVIRONMENT_VARIABLES.md` | Remove from `Settings`, `MIGRATED_LIMIT_ENV_VARS`, UI limit forms, and docs. |
| Fixed single-value vars (3) | `CLAIRE_PDF_PARSER`, `CLAIRE_RENDER_FORMAT`, `CLAIRE_VECTOR_BACKEND` | `config.py`, `pdf.py`, `presentation.py`, `vectors.py` (`make_vector_store`), `cli.py`, `cb_manuscript.py`, `.env.example` | Remove env fields and hardcode `"pypdfium2"`, `"adoc"`, and `"brute"` directly in callers. |
| Removed Vault subsystem (2) | `CLAIRE_VAULT_PATH`, `CB_VAULT_DIR` | `config.py` (`vault_path`, `vault_dir`), `cli.py:1667`, `docker-compose*.yml`, `ops/cb_manuscript.py`, `.env*.example` | Remove fields, `/app/vault` Compose mounts, and `cb_manuscript` vault directory validation. |
| Post-v46 migrated var (1) | `CLAIRE_PREFERRED_LANGUAGES` | `.env.example:101`, `.env.dev.example:59`, `cli.py:61` | Remove from `.env*.example` and preflight warning; keep historical v46 migration fallback read in `store/db.py` for pre-v46 DB upgrades. |
| Migrated `.env.example` lines (3) | `CLAIRE_EXPAND_MAX`, `CLAIRE_AUTO_EXPAND`, `CLAIRE_WATCH_INTERVAL_DAYS` | `.env.example:95-97` | Remove uncommented lines from `.env.example` (already managed in `data/ingest_limits.json`). |
| Zero-caller `@property` methods (9) | `effective_ga_measurement_id`, `effective_sorcerer`, `owner`, `effective_owner`, `knowledge_manager`, `effective_knowledge_manager`, `effective_youtube_languages`, `allowed_users`, `owner_chat_id` | `src/claire/core/config.py` | Delete all 9 unused properties. |

*(Preserved active variable: `CLAIRE_PRESENTATION_PDF_MAX_BYTES` remains active because `plugins/ingest_adapter/claire.vmware_explore/adapter.py:243` reads `settings.presentation_pdf_max_bytes`.)*

### 2.3 Tier 2 Legacy Aliases

| Variables | Canonical Replacement | Cleanup Scope |
| :--- | :--- | :--- |
| `CLAIRE_PUBLIC_URL` (`legacy_public_url`) | `CLAIRE_FQDN` | Remove from `config.py`, `security.py`, `cb_manuscript.py`, `ENVIRONMENT_VARIABLES.md`, and remove `_ExactDotEnvSettingsSource`. |
| `CLAIRE_CLOUDFLARE_ONLY` | `CLAIRE_CLOUDFLARE_IPS_ONLY` | Remove alias fallback in `config.py:729`. |
| `STT_PROVIDER`, `STT_PROIVDER`, `CLAIRE_STT_PROVIDER` | Fixed Gemini STT (`PS3`) | Remove fields and `os.environ` fallbacks in `config.py`, `provider_manager.py`, `provider_policy.py`, and `video.py`. |
| `CLAIRE_MEDIA_S3_ENDPOINT`, `_BUCKET`, `_ACCESS_KEY`, `_SECRET_KEY`, `_REGION` | `/admin/storage` DB config | Remove `_LEGACY_ENV` and `_import_legacy_environment` in `src/claire/store/media_storage.py`. |

### 2.4 Tier 3 Policy Env Vars

| Variable | Current Locations | Replacement Behavior |
| :--- | :--- | :--- |
| `CLAIRE_MULTI_THEME` | `config.py`, `knowledge_base.py`, `server.py`, `accounts_routes/server.py`, `pages.py`, `cli.py`, `ko.json`/`en.json` | Multi-KB mode is always enabled; delete `_load_single_mode_theme0()`, `403` single-theme locks, and network-mode prerequisite errors. |
| `CLAIRE_PRESENTATION_ENABLED` | `config.py`, `knowledge_base.py`, `provider_policy.py`, `pages.py`, `cli.py` | Read solely from `knowledge_bases.presentation_enabled` (default `0`). |
| `CLAIRE_SORCERER` | `config.py`, `knowledge_base.py`, `server.py`, `pages.py`, `.env*.example` | Read `knowledge_bases.sorcerer` if set; otherwise fall back to the owning account's `display_name` (or omit when empty). |

### 2.5 Manual Token Removal Matrix (`ER7`, `ER8`)

| Service / Layer | Files | Removal and Replacement Scope |
| :--- | :--- | :--- |
| Token Store & DB | `src/claire/store/token_store.py`, `src/claire/store/db.py:4978-5030` | Delete `token_store.py` and DB token wrapper functions; rename existing `data/auth/api_tokens.json` to `api_tokens.json.retired` on startup. |
| Auth Middleware | `src/claire/api/security.py` | Remove `owner_token`, `readonly_token`, `_validate_api_token`, `_env_token_credential`, and manual-token `Deprecation` headers; `Bearer` resolves OAuth 2.1 tokens and same-origin sessions only. |
| HTTP Endpoints | `src/claire/api/server.py`, `src/claire/api/security.py` | Remove `GET`/`HEAD`/`POST`/`DELETE /tokens` and `POST /tokens/revoke` routes and `ROUTE_POLICY` entries (return `404`). |
| Console UI | `src/claire/api/accounts_routes/console.py`, `src/claire/api/security.py`, `src/claire/api/server.py`, `tests/test_console_ui_contract.py`, `e2e/console_ui_consistency.spec.js` | Remove `/account/tokens`, `/admin/tokens`, `/settings/tokens` redirect, sidebar links, and JS token panels; update console UI contracts. |
| CLI | `src/claire/cli.py`, `src/claire/ops/status.py` | Remove `claire token` subcommand and `inject (token ...)` status line. |
| Telegram Bot | `src/claire/telegram/bot.py`, `src/claire/i18n/locales/{ko,en}.json` | Remove `/token` command and `bot.token.*` keys; replace `Bearer {settings.inject_token}` in `/support/bundle` and `/support/graph-bundle` calls with ephemeral `X-Session` (60s TTL, revoked in `finally`, `ER8`). |
| Host Ops & Examples | `ops/cb_manuscript.py`, `.env.example`, `.env.dev.example` | Remove `CLAIRE_INJECT_TOKEN` / `CLAIRE_READONLY_TOKEN` generation, validation, and preflight checks. |
| MCP Telemetry | `src/claire/api/mcp_tools.py` | Remove `api:<id>` and `env:*` client attribution branches; retain `oauth:<id>` and `session:<id>`. |

## 3. Schema v48 Auto-Migration Specification

1. **Trigger and Idempotency (`PRINCIPLES.md` A3)**:
   - Bump `SCHEMA_VERSION` in `src/claire/store/db.py` from `47` to `48`.
   - `migrate_to_v48` runs inside `init_db` / `claire migrate` before `edge` or `api` starts (`PRINCIPLES.md` E3). Re-running on an already v48 database is a no-op.
2. **Visibility Conversion Algorithm (`ER2`)**:
   - Inspect `CLAIRE_ANONYMOUS_READONLY` in `os.environ`, `.env`, and `.env.dev` (process env takes precedence if set, then `.env.dev`, then `.env`).
   - If the resolved raw value is `"0"`:
     - Execute `UPDATE knowledge_bases SET is_public = 0, visibility_epoch = visibility_epoch + 1 WHERE is_personal = 1 AND is_public != 0`.
     - Query `SELECT COUNT(*) FROM knowledge_bases WHERE is_personal = 0` and log a warning if shared KBs exist (shared KBs remain `is_public = 1` per `kb_collab.kind_violation_reason` because shared KBs have no private mode).
   - If the resolved value is `"1"` or unset: perform no KB row updates.
   - Comment out active `CLAIRE_ANONYMOUS_READONLY=` lines in `.env` and `.env.dev` if writable (in container environments where `.env` is not mounted writable, log a deprecation warning instructing removal).
3. **Non-Destructive Manual Token Retirement (`ER7`)**:
   - During `prepare_auth_db` / startup, if `data/auth/api_tokens.json` exists and `data/auth/api_tokens.json.retired` does not, atomically rename `api_tokens.json` to `api_tokens.json.retired` and log an info record pointing operators to `/account/mcp` OAuth 2.1.

## 4. Work Units (`ENV-1` to `ENV-4`) and Verification

| Unit | Scope | Verification |
| :--- | :--- | :--- |
| `ENV-1` | Remove Tier 1 dead env vars (11), dead `Settings` properties (9), migrated `.env.example` lines (3), Compose `/app/vault` mounts, and dead `ingest_limit_manager.py` / `manage_panels.js` limit fields; update `ENVIRONMENT_VARIABLES.md`. | `tests/test_docs_consistency.py`, `tests/test_ingest_limits.py`, `tests/test_cb_manuscript.py`, and `tests/test_config.py` pass. |
| `ENV-2` | Implement schema v48 auto-migration (`ER2`), remove `CLAIRE_ANONYMOUS_READONLY` across `config.py`, `security.py`, `server.py`, `cli.py`, `cb_manuscript.py`, i18n, and `.env*`; harden `GET /syndication`, `?pwa=1`, `robots.txt`, and `sitemap.xml` (`ER3`). | Unit tests for schema v48 migration (`=0` sets personal KBs `is_public=0` and increments `visibility_epoch`; `=1` keeps rows unchanged; shared KBs stay `is_public=1`; idempotent re-run) and updated `test_anonymous_readonly_invariance.py` / `test_anonymous_readonly_hidden_isolation.py` covering `is_public=0` hiding. |
| `ENV-3` | Remove Tier 2 legacy aliases (`CLAIRE_PUBLIC_URL`, `CLAIRE_CLOUDFLARE_ONLY`, `STT_PROVIDER`/`CLAIRE_STT_PROVIDER`, `CLAIRE_MEDIA_S3_*`), delete `_ExactDotEnvSettingsSource`, and remove Tier 3 policy vars (`CLAIRE_MULTI_THEME`, `CLAIRE_PRESENTATION_ENABLED`, `CLAIRE_SORCERER`). | Unit tests for `Settings` priority without `_ExactDotEnvSettingsSource`, `MediaStore` DB-only config, and always-on multi-KB lifecycle. |
| `ENV-4` | Execute total manual token retirement across all 8 surfaces (`ER7`), implement ephemeral `X-Session` bundle delegation in `src/claire/telegram/bot.py` (`ER8`), create `tests/auth_helpers.py`, and migrate affected tests (`ER9`). | Unit tests for ephemeral bot `X-Session` creation/revocation I/O, `test_console_ui_contract.py`, `test_route_surface_contract.py`, `test_oauth_flow.py`, `test_api_security.py`, full `scripts/ci.sh`, and E2E verification (`console_ui_consistency.spec.js` without `/tokens` and private personal KB `is_public=0` access isolation). |
