# Versioned Public API (/api/v1) and Role-Expanded Reference (/reference)

- **Document Identifier**: `DESIGN-API-V1-REFERENCE-20261124`
- **Status**: Decided (owner, 2026-11-24). Not implemented. Depends on [`LEGACY_ENV_AND_MANUAL_TOKEN_RETIREMENT_DESIGN.md`](LEGACY_ENV_AND_MANUAL_TOKEN_RETIREMENT_DESIGN.md) (units `ENV-1` to `ENV-4`).
- **Date**: 2026-11-24
- **Related**: [`LEGACY_ENV_AND_MANUAL_TOKEN_RETIREMENT_DESIGN.md`](LEGACY_ENV_AND_MANUAL_TOKEN_RETIREMENT_DESIGN.md), [`PERMALINK_UNIFICATION_DESIGN.md`](PERMALINK_UNIFICATION_DESIGN.md), [`MCP_SUPPORT.md`](MCP_SUPPORT.md), [`AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md`](AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md), [`API_AND_MCP_GUIDE.md`](../implementation/API_AND_MCP_GUIDE.md), [`API_HTTP_CONTRACT.md`](../implementation/API_HTTP_CONTRACT.md), [`PRINCIPLES.md`](../PRINCIPLES.md)

## 1. Problem and Owner Decisions (2026-11-24)

The HTTP surface of Claire Bible grew from a small local ingest API and WebUI backend into one 8,255-line file (`src/claire/api/server.py`) that mixes public integrations, WebUI BFF endpoints, console SSR HTML pages, and duplicate route aliases. The offline Scalar documentation at `/reference` operates, but it is isolated from the UI, mixes HTML pages with REST endpoints, omits large sets of implemented routes, has zero OpenAPI tags, and drifts from `ROUTE_POLICY`.

The owner locked decisions `AR1` through `AR10` on 2026-11-24:

| ID | Decision | Reason |
| :--- | :--- | :--- |
| `AR1` | **Option B — Versioned Public API (`/api/v1/*`)**: Introduce `/api/v1/*` as the sole stable, versioned public REST API surface for external developers, scripts, and applications. Classify existing unprefixed routes (`/graph`, `/node`, `/documents`, `/ingest`, `/providers`, `/limits`, etc.) and `/api/me/*`, `/api/admin/*` as internal WebUI and console BFF routes. | Separates external compatibility guarantees from internal UI iteration without breaking existing WebUI callers. |
| `AR2` | **Domain Terminology and Explicit KB Selection (`CT23`)**: Use the canonical terms from [`PERMALINK_UNIFICATION_DESIGN.md`](PERMALINK_UNIFICATION_DESIGN.md) §4 (`entity`, `relation`, `graph`, `document` — never `node` or `link` in `/api/v1/*`). Resolve knowledge bases with the `{kb}` path parameter (`"personal"` or an 18-digit external KB number); reject internal integer `kb_id` and legacy `theme`. Use `surface="system"` for all `/api/v1/*` routes with no `/@{username}` or `/kb/{number}` prefix duplication. | Removes terminology mismatch and eliminates ambiguity between namespaced prefixes and query parameters. |
| `AR3` | **Sealed Schemas (`PRINCIPLES.md` C4)**: Define every `/api/v1/*` request and response payload with Pydantic v2 models using `ConfigDict(extra="forbid", strict=True)` (JSON request inputs use `extra="forbid"`). | Prevents silent field drift and unknown-key leaks between runtime handlers and API contracts. |
| `AR4` | **Standard Wire Contract**: Return a unified error envelope `{"error": {"code": "<snake_case>", "message": "<text>", "request_id": "<id>", "details"?: object}}` (preserving HTTP `404` existence-hiding for unauthorized or private resources per `PRINCIPLES.md` B1), unified keyset pagination `{"items": [...], "next_cursor": str \| null, "total"?: int}`, and strict `application/json` content-type validation on JSON write endpoints. | Gives external clients one predictable error, pagination, and content-type contract across all v1 endpoints. |
| `AR5` | **Zero Legacy Manual Tokens**: `/api/v1/*` accepts only OAuth 2.1 Bearer tokens (`Authorization: Bearer`) and Same-Origin browser sessions (`Cookie`, `X-Session`), aligned with total retirement of legacy manual tokens across all services ([`LEGACY_ENV_AND_MANUAL_TOKEN_RETIREMENT_DESIGN.md`](LEGACY_ENV_AND_MANUAL_TOKEN_RETIREMENT_DESIGN.md) `ER7`). Cross-origin requests require an origin in `CLAIRE_CORS_ALLOWED_ORIGINS` and `Authorization: Bearer`. | Prevents legacy token debt from entering the new public API surface. |
| `AR6` | **Single `/reference` with Role-Expanded Scalar Tabs**: Keep a single `/reference` UI powered by the vendored offline `src/bible/static/docs/scalar.standalone.js` (which natively supports `sources: [...]` multi-document tabs). Change `GET /reference` in `ROUTE_POLICY` from `public` to `read` (`surface="system"`) so `AuthenticationMiddleware` resolves valid credentials while still allowing anonymous visitors (after `CLAIRE_ANONYMOUS_READONLY` retirement). Return `Cache-Control: private, no-store`, `Vary: Cookie, Authorization`, `Referrer-Policy: same-origin`, and render only the `sources` entries permitted for the caller's `Principal`. | Gives all callers one documentation URL while showing account BFF and admin tabs only to authorized callers. |
| `AR7` | **Defense-in-Depth Spec Endpoints**: Serve three separate OpenAPI 3.1 documents enforced by `ROUTE_POLICY`: (1) `GET /openapi/v1.json` (plus compatibility alias `GET /openapi.yaml` returning the v1 YAML) with `public` access; (2) `GET /openapi/account.json` with `owner` access (`404` to non-owner or anonymous); (3) `GET /openapi/admin.json` with `admin` access (`404` to non-admin). Exclude all SSR HTML pages (`/admin/*`, `/account/*`, `/settings/*`, `/moderation/*`, `/login`, `/setup`, `/device`) from every OpenAPI document. | Prevents credential-less reconnaissance of internal account and admin routes while keeping all documentation inside `/reference`. |
| `AR8` | **Single Source of Truth and CI Contract Gates**: Generate OpenAPI 3.1 specs directly from Python route definitions, Pydantic models, and `ROUTE_POLICY`. Remove the duplicate copy `src/bible/static/docs/openapi.yaml`. Keep `docs/implementation/openapi.yaml` as a generated artifact verified for zero diff in CI. Add `PyYAML` to dev dependencies in `pyproject.toml` so `test_openapi_yaml_serves_valid_openapi_3_1_spec` no longer skips. Enforce CI checks for zero untagged operations, 100% parity between OpenAPI `x-claire-access`/`x-claire-surface`/`x-claire-capability` and `ROUTE_POLICY`, and zero retired parameters (`theme`, etc.). | Eliminates manual YAML maintenance and guarantees the published specification matches runtime security rules. |
| `AR9` | **Scalar OAuth 2.1 Interactive Auth, i18n, and Discoverability**: Declare `OAuth2` (`authorizationCode` + PKCE `S256`, `/oauth/authorize`, `/oauth/token`, scopes `readonly`/`write`) in `components.securitySchemes` alongside `BearerAuth` and `SessionCookie`, with a dedicated same-origin callback configuration for Scalar "Try it". Bind `docs.html` `<html lang="...">` to `get_locale()`. Add `/reference` links in `/account/mcp`, the admin console, and `README.md`. | Enables interactive API testing with OAuth 2.1, fixes hardcoded `lang="ko"`, and makes `/reference` discoverable from the UI. |
| `AR10` | **Router Modularization and Alias Deprecation**: Place v1 routes in `src/claire/api/v1/` (`schemas.py`, `errors.py`, `pagination.py`, `router.py`, `kbs.py`, `documents.py`, `graph.py`, `collaboration.py`, `extraction.py`, `federation.py`, `openapi.py`). Incrementally split `src/claire/api/server.py` into domain BFF routers. Add `Deprecation: true` and `Link: <...>; rel="successor-version"` headers to duplicate legacy aliases (`/purge`, `/ingest-limits`, `/document/regenerate`, `/syndication/dispatch`, `/syndication/dispatches`, `/support/bundle`). | Reduces monolith size, keeps domain services shared, and signals deprecation on redundant legacy endpoints. |

## 2. Baseline Audit Facts (`[code]`, `[measured]`)

Audit of the repository at the base commit shows these verified measurements:

1. **API codebase footprint (`[code]`)**:
   - `src/claire/api/server.py`: 8,255 lines (`src/claire/api/` total across `*.py` and `accounts_routes/*.py`: 33,727 lines).
   - `src/claire/api/security.py` `ROUTE_POLICY`: 569 entries total, 370 non-`HEAD` operations (`system`: 198, `any`: 106, `namespaced`: 63, `collection`: 2, `root`: 1; `owner`: 168, `admin`: 82, `public`: 60, `read`: 36, `write`: 24).
2. **OpenAPI specification state (`[code]`, `[measured]`)**:
   - `docs/implementation/openapi.yaml` and `src/bible/static/docs/openapi.yaml`: 6,819 lines, 128 paths, 165 operations, 33 component schemas, and 3 security schemes (`BearerAuth`, `SessionHeader`, `SessionCookie` — no `oauth2` scheme).
   - **Zero OpenAPI tags**: `untagged: 165` (`tags: []` across all operations), so Scalar renders a flat ungrouped list.
   - **HTML pages mixed into API reference**: 13 pure `text/html` operations (`/admin/{section}`, `/account/{section}`, `/settings/{section}`, `/moderation/*`, `/login`, `/setup`, `/device`).
   - **Large undocumented gaps**: missing 27 of 36 `/api/me/*`, 27 of 52 `/api/admin/*`, 11 of 24 `/api/federation/*`, 15 of 18 `/document*`, and 9 of 10 ingest/pipeline routes.
   - **Contract drift (`[code]`)**: 3 `x-claire-access` mismatches against `ROUTE_POLICY` (`POST /ingest` spec `'owner'` vs code `'write'`, `POST /support/bundle` spec `'owner'` vs code `'admin'`, `GET /themes` spec `'public'` vs code `'read'`); retired `?theme=` query parameter still listed on `GET /documents` despite `CT23`.
3. **Scalar UI and test state (`[code]`, `[measured]`)**:
   - `src/bible/static/docs/scalar.standalone.js` (3,715,177 bytes) is vendored offline and natively parses `sources` arrays for multi-document tabs.
   - `src/claire/api/templates/docs.html` hardcodes `<html lang="ko" data-theme="dark">`.
   - Zero links to `/reference` exist in WebUI templates, console panels, `README.md`, or `PROJECT.md`.
   - In `tests/test_api_server.py`, `test_reference_serves_kepler_themed_scalar_html` and `tests/test_api_security.py::test_docs_and_openapi_are_publicly_accessible_without_credentials` pass, but `test_openapi_yaml_serves_valid_openapi_3_1_spec` is `skipped` because `PyYAML` is not installed in `.venv`.

## 3. `/api/v1/*` Resource Map and Wire Specification

### 3.1 Common Wire Contract

1. **Error envelope (`AR4`)**:
   - All `4xx` and `5xx` JSON responses on `/api/v1/*` use `{"error": {"code": "<snake_case>", "message": "<text>", "request_id": "<id>"}}` with optional `"details": {...}` for field validation errors (`400`/`422`).
   - Existence-hiding rules remain unchanged (`PRINCIPLES.md` B1): unauthenticated or unauthorized access to private KBs, private documents, or non-public endpoints returns `404` with code `"not_found"`.
2. **Pagination (`AR4`)**:
   - Collection list routes return `{"items": [...], "next_cursor": "<opaque>" | null, "total"?: int}` using keyset cursors rather than offset/page numbers.
3. **Knowledge Base Resolution (`AR2`)**:
   - Path parameter `{kb}` accepts `"personal"` (resolved to the authenticated caller's personal KB; returns `404` for anonymous callers) or an 18-digit public KB number (verified for visibility against `Principal`). Internal integer `kb_id` values and legacy `theme` parameters return `400 invalid_kb`.
4. **Ingest and Search Invariants**:
   - `POST /api/v1/kbs/personal/ingest` and `/stream` require `extraction_mode` (`"provider"` or `"client"`). Requests authenticated with an agent client OAuth 2.1 access token (`AED4-rev`) that pass `"provider"` fail with `403 agent_client_provider_refused`.
   - `POST /api/v1/kbs/{kb}/search` for `anonymous` callers enforces FTS-only search, omits summaries, limits results to at most 20 items, and never triggers LLM or embedding calls (`PLAN.md` §2).

### 3.2 `/api/v1/*` Endpoint Inventory

| OpenAPI Tag | Method and Path | Minimum Access / Capability | Legacy Counterpart & Notes |
| :--- | :--- | :--- | :--- |
| `System & Identity` | `GET /api/v1/health` | `public` | Replaces `GET /health` for v1 consumers; returns liveness, DB readiness, and API version. |
| `System & Identity` | `GET /api/v1/me` | `read` (credential required) | Consolidates `GET /whoami`; returns scope, `server.mode`, `relation`, `is_admin` (no `username`). |
| `Knowledge Bases` | `GET /api/v1/kbs` | `read` | Replaces `GET /kb` JSON response with keyset pagination (`?joined=`, `?knowledge_type=`). |
| `Knowledge Bases` | `POST /api/v1/kbs` | `owner` | Replaces `POST /kb` (`label`, `knowledge_type` required, optional `seed_entities`). |
| `Knowledge Bases` | `GET /api/v1/kbs/{kb}` | `read` | Combines KB metadata and counts (absorbs `GET /stats`). |
| `Knowledge Bases` | `PATCH /api/v1/kbs/{kb}` | `owner` (`settings`) | Replaces `PATCH /kb/{number}/` and personal `PATCH /@{u}/`. |
| `Knowledge Bases` | `DELETE /api/v1/kbs/{kb}` | `admin` (step-up) | Replaces `DELETE /kb/{number}/`; dry-run by default (`?apply=true` required to execute, `PRINCIPLES.md` D3). |
| `Knowledge Bases` | `GET /api/v1/themes` | `read` | Replaces `GET /themes` JSON collection. |
| `Documents & Ingest` | `GET /api/v1/kbs/{kb}/documents` | `read` | Replaces `GET /documents` with `{cursor}`, `limit`, `q`, `pinned`, `hidden`, `detail` (no `?theme=`). |
| `Documents & Ingest` | `GET /api/v1/kbs/{kb}/documents/{doc_id}` | `read` | Replaces `GET /document`; returns summary, AsciiDoc `detail`, `entities[]`, and permalink (`/silvercord/...`). |
| `Documents & Ingest` | `GET /api/v1/kbs/{kb}/documents/{doc_id}/raw` | `read` | Replaces `GET /document/raw`. |
| `Documents & Ingest` | `GET /api/v1/kbs/{kb}/documents/{doc_id}/export` | `read` | Replaces `GET /document/export` (`?format=markdown`). |
| `Documents & Ingest` | `PATCH /api/v1/kbs/{kb}/documents/{doc_id}` | `owner` (`curate`) | Consolidates `POST /document/title`, `/pin`, `/hide`, and `/seen` into one partial update. |
| `Documents & Ingest` | `DELETE /api/v1/kbs/{kb}/documents/{doc_id}` | `owner` (`remove`) | Replaces `POST /document/purge` and `/purge`. |
| `Documents & Ingest` | `POST /api/v1/kbs/personal/ingest` | `write` (`CT4`) | Replaces `POST /ingest`; `extraction_mode` required (`AED4-rev`). |
| `Documents & Ingest` | `POST /api/v1/kbs/personal/ingest/stream` | `write` (`CT4`) | Replaces `POST /ingest-stream` (NDJSON progress stream). |
| `Graph & Search` | `POST /api/v1/kbs/{kb}/search` | `read` | Replaces `POST /search` (hybrid/FTS; anonymous FTS-only capped at 20). |
| `Graph & Search` | `GET /api/v1/kbs/{kb}/graph` | `read` | Replaces `GET /graph`; returns `entities[]` and `relations[]` using canonical terminology. |
| `Graph & Search` | `GET /api/v1/kbs/{kb}/entities/{entity_id}` | `read` | Replaces `GET /node?id=` (`?docs=brief` supported). |
| `Graph & Search` | `GET /api/v1/kbs/{kb}/entities/{entity_id}/neighbors` | `read` | 1-hop neighborhood slice (`exclude_ids`, `limit`). |
| `Graph & Search` | `GET /api/v1/kbs/{kb}/graph/path` | `read` | Shortest undirected BFS path (`from_entity_id`, `to_entity_id`, `max_hops`). |
| `Shared KB Collaboration` | `GET /api/v1/kbs/{kb}/contributions` | `read` | Replaces `GET /kb/{number}/contributions`. |
| `Shared KB Collaboration` | `POST /api/v1/kbs/{kb}/contributions` | `write` (`contribute_direct`) | Replaces `POST /kb/{number}/contributions`. |
| `Shared KB Collaboration` | `PATCH /api/v1/kbs/{kb}/contributions/{number}` | `owner` (`review`) | Replaces `PATCH /kb/{number}/contributions/{contribution}`. |
| `Shared KB Collaboration` | `DELETE /api/v1/kbs/{kb}/contributions/{number}` | `write` | Replaces `DELETE /kb/{number}/contributions/{contribution}`. |
| `Shared KB Collaboration` | `POST /api/v1/kbs/{kb}/contributions/{number}/retry` | `write` | Replaces `POST /kb/{number}/contributions/{contribution}/retry`. |
| `Shared KB Collaboration` | `POST /api/v1/kbs/{kb}/contributions/{number}/remove` | `owner` (`remove`) | Replaces `POST /kb/{number}/contributions/{contribution}/remove`. |
| `Shared KB Collaboration` | `GET /api/v1/kbs/{kb}/members` | `read` | Replaces `GET /kb/{number}/members`. |
| `Shared KB Collaboration` | `POST /api/v1/kbs/{kb}/members` | `owner` | Replaces `POST /kb/{number}/membership`. |
| `Shared KB Collaboration` | `DELETE /api/v1/kbs/{kb}/members` | `owner` | Replaces `DELETE /kb/{number}/membership`. |
| `Shared KB Collaboration` | `PATCH /api/v1/kbs/{kb}/members/{account}` | `owner` (`people`) | Replaces `PATCH /kb/{number}/members/{account}`. |
| `Client Extraction` | `GET /api/v1/extraction/spec` | `public` | Replaces `GET /api/client-extraction/spec`. |
| `Client Extraction` | `POST /api/v1/extraction/stage` | `write` (`CT4`) | Replaces `POST /api/client-extraction/stage`. |
| `Client Extraction` | `GET /api/v1/extraction/sources` | `write` (`CT4`) | Replaces `GET /api/client-extraction/sources`. |
| `Client Extraction` | `GET /api/v1/extraction/sources/{document_id}` | `write` (`CT4`) | Replaces `GET /api/client-extraction/sources/{document_id}`. |
| `Client Extraction` | `POST /api/v1/extraction/sources/{document_id}/claim` | `write` (`CT4`) | Replaces `POST /api/client-extraction/sources/{document_id}/claim`. |
| `Client Extraction` | `POST /api/v1/extraction/sources/{document_id}/renew` | `write` (`CT4`) | Replaces `POST /api/client-extraction/sources/{document_id}/renew`. |
| `Client Extraction` | `POST /api/v1/extraction/sources/{document_id}/submit` | `write` (`CT4`) | Replaces `POST /api/client-extraction/sources/{document_id}/submit`. |
| `Federation & Feeds` | `GET /api/v1/federation/status` | `owner` | Replaces `GET /api/federation/status`. |
| `Federation & Feeds` | `POST /api/v1/federation/publish` | `write` | Replaces `POST /api/federation/publish`. |
| `Federation & Feeds` | `POST /api/v1/federation/retract` | `write` | Replaces `POST /api/federation/retract`. |
| `Federation & Feeds` | `GET /api/v1/feeds` · `POST /api/v1/feeds` · `DELETE /api/v1/feeds` | `owner` | Replaces `/api/feeds` CRUD. |
| `Federation & Feeds` | `GET /api/v1/feeds/items` · `PATCH /api/v1/feeds/items` | `owner` | Replaces `/api/feeds/items`. |
| `Federation & Feeds` | `POST /api/v1/feeds/items/{item_id}/ingest` | `owner` / `write` | Replaces `/api/feeds/items/{item_id}/ingest`. |
| `Account & Support` | `GET /api/v1/me/oauth/grants` · `DELETE /api/v1/me/oauth/grants` | `owner` | Replaces `/api/me/oauth/grants`. |
| `Account & Support` | `GET /api/v1/support/bundles` · `POST /api/v1/support/bundles` · `DELETE /api/v1/support/bundles/{bundle_id}` | `owner` / `admin` (`scope=server`) | Consolidates `/api/support/bundles` and legacy `POST /support/bundle`. |

## 4. Single `/reference` Multi-Document Architecture

### 4.1 Role-Based Tab Expansion

```text
Caller ──> GET /reference (ROUTE_POLICY: system, access="read")
              │
              ├─ Anonymous / readonly / write
              │     └─ sources = [ {slug:"v1", url:"/openapi/v1.json", default:true} ]
              │
              ├─ Authenticated owner (server_role != admin)
              │     └─ sources = [ v1 (default), {slug:"account", url:"/openapi/account.json"} ]
              │
              └─ Authenticated admin (owner + server_role == "admin")
                    └─ sources = [ v1 (default), account, {slug:"admin", url:"/openapi/admin.json"} ]
```

1. **Why `GET /reference` uses `access="read"` (`AR6`)**: In `src/claire/api/security.py`, `access="public"` skips credential lookup and forces `ANONYMOUS`. Using `access="read"` allows anonymous same-origin callers after `CLAIRE_ANONYMOUS_READONLY` retirement while still populating `Principal` and `server_role` for authenticated callers.
2. **Response isolation headers**: `GET /reference` and `/openapi/{account,admin}.json` emit `Cache-Control: private, no-store`, `Vary: Cookie, Authorization`, and `Referrer-Policy: same-origin` so shared caches or intermediaries never serve privileged spec URLs across users.
3. **Spec endpoint enforcement (`AR7`)**:
   - `GET /openapi/v1.json` and `GET /openapi.yaml`: `ROUTE_POLICY` `public`. Contains only `/api/v1/*` and OAuth 2.1 protocol routes (`/.well-known/*`, `/oauth/*`, `/device`). Documents MCP (`/mcp`) connection instructions and tool summary in `info.description`.
   - `GET /openapi/account.json`: `ROUTE_POLICY` `owner` (returns `404` to anonymous, `readonly`, and `write` callers). Documents `/api/me/*`, `/api/feeds/*`, `/api/federation/*`, `/api/invites`, `/api/auto-rule`, `/api/support/bundles`, and workspace BFF routes.
   - `GET /openapi/admin.json`: `ROUTE_POLICY` `admin` (returns `404` to non-admin callers). Documents `/api/admin/*`, `/providers*`, `/limits*`, and `/support/bundle`.
4. **CSP and OAuth 2.1 "Try it" (`AR9`)**:
   - Keep `withDefaultFonts: false` and `worker-src 'self' blob:` so Scalar stays 100% offline with existing CSP headers.
   - Register `OAuth2` (`type: oauth2`, `authorizationCode` flow with `x-usePkce: "SHA-256"`, `authorizationUrl: "/oauth/authorize"`, `tokenUrl: "/oauth/token"`, scopes `readonly` and `write`) in `components.securitySchemes`. Configure Scalar's redirect URI on the same origin under `/reference` so interactive "Try it" works without manual token pasting.
   - Render `<html lang="{locale}">` dynamically from `get_locale()` and add `/reference` links in `/account/mcp`, admin navigation, and `README.md`.

## 5. Work Units (`API-1` to `API-5`) and Verification

| Unit | Scope | Verification |
| :--- | :--- | :--- |
| `API-1` | Create `src/claire/api/v1/` foundation (`schemas.py`, `errors.py`, `pagination.py`, `openapi.py`, `router.py`), add `PyYAML` to `pyproject.toml` dev dependencies, and build CI contract gates checking `ROUTE_POLICY` parity, `untagged == 0`, and zero retired parameters. | Unit tests for Pydantic C4 strict schemas, error envelope serialization, cursor encode/decode I/O, OpenAPI generation parity against `ROUTE_POLICY`, and unskipped `test_openapi_yaml_serves_valid_openapi_3_1_spec`. |
| `API-2` | Implement core `/api/v1/*` endpoints: `health`, `me`, `kbs`, `themes`, `documents`, `ingest`, `ingest/stream`, `search`, `graph`, `entities`, `neighbors`, and `graph/path`. | Unit and integration tests covering `{kb}` resolution (`personal` + 18-digit number, rejection of integer `kb_id`), `AED4-rev` agent client refusal, anonymous search FTS-only cap, and OAuth-only auth enforcement. |
| `API-3` | Implement remaining `/api/v1/*` endpoints: `contributions`, `members`, `extraction/*`, `federation/*`, `feeds/*`, `me/oauth/grants`, and `support/bundles`. | Integration tests verifying scope/capability checks (`write` vs `owner` vs `admin`), step-up on `DELETE /api/v1/kbs/{kb}`, and `CT4` personal KB isolation on extraction routes. |
| `API-4` | Implement single `/reference` (`access="read"`) with role-expanded Scalar `sources`, `/openapi/{v1,account,admin}.json` + `/openapi.yaml`, OAuth 2.1 `securityScheme`, dynamic `lang`, console & `README.md` links, and remove `src/bible/static/docs/openapi.yaml`. | Security tests asserting `404` on `/openapi/{account,admin}.json` for unauthorized callers, `sources` filtering per role, `Cache-Control: private, no-store`, and CI diff-0 check on generated `docs/implementation/openapi.yaml`. |
| `API-5` | Add `Deprecation: true` and `Link` headers to duplicate legacy aliases (`/purge`, `/ingest-limits`, `/document/regenerate`, `/syndication/dispatch*`, `/support/bundle`), incrementally split `src/claire/api/server.py` BFF routes into domain modules, and update `API_AND_MCP_GUIDE.md`, `API_HTTP_CONTRACT.md`, and `openapi.yaml`. | Full `scripts/ci.sh` suite (`ty`, `pytest`), `tests/test_route_surface_contract.py`, `tests/test_docs_consistency.py`, plus Playwright E2E test verifying `/reference` tab visibility across anonymous, member owner, and admin sessions and `/api/v1/*` round-trip calls. |
