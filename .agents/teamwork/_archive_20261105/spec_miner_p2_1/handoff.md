# Handoff Report: Phase P2 Specification Extraction

- **Author**: Specification Investigator (`spec_miner_p2_1`)
- **Date**: 2026-11-05
- **Task**: Phase P2 Specification and Requirement Extraction (Lane P2-a and Lane P2-b)
- **Status**: Complete

---

## Features Discovered

| # | Category | Feature | Description | Inputs | Outputs | Error Behavior | Discovered Via |
|---|----------|---------|-------------|--------|---------|----------------|----------------|
| 1 | Lane P2-a | `manage_panels.js` module creation | Provide reusable DOM and API controller functions for management panels | Container element, configuration options object | Rendered interactive DOM elements | Show error banner in container on API failure | `docs/design/ADMIN_AND_SETTINGS_PATH_SEPARATION_DESIGN.md` §5 |
| 2 | Lane P2-a | `renderProviderManager` helper | Manage LLM, STT, and hyperscaler provider settings | `(container, opts)` | Form cards, provider selector, connection test results | Show error message on network failure or invalid key | `src/claire/static/js/app.js:4901`, Design Doc §5 |
| 3 | Lane P2-a | `renderLimitManager` helper | Manage ingest limits and slicing char budgets | `(container, opts)` | Input forms for character limits and media thresholds | Return error alert on invalid numeric payload | `src/claire/static/js/app.js:5281`, Design Doc §5 |
| 4 | Lane P2-a | `renderThemeManager` helper | Manage knowledge themes (create, update, delete, visibility) | `(container, opts)` | Theme list cards, edit forms, creation details form | Alert user on duplicate name or network error | `src/claire/static/js/app.js:4544`, Design Doc §5 |
| 5 | Lane P2-a | `renderTokenManager` helper | Manage persistent API and MCP tokens and OAuth client list | `(container, opts)` | Token list, issue form, one-time token banner | Show error notice on failed issue or revocation | `src/claire/static/js/app.js:5549`, Design Doc §5 |
| 6 | Lane P2-a | `IS_ADMIN` client state tracking | Track administrator status in client state | `GET /whoami` response (`data.is_admin === true`) | Boolean variable `IS_ADMIN` in `app.js` | Reset `IS_ADMIN = false` when session expires or fails | Design Doc §1.2 (D2), §5 |
| 7 | Lane P2-a | Dynamic Drawer Button Visibility | Display provider and limit buttons only for administrators | `canWrite() && IS_ADMIN` | Toggle visibility of `#providermanagebtn`, `#limitmanagebtn` | Hide buttons when caller lacks administrator role | `src/claire/static/js/app.js:4505`, Design Doc §5 |
| 8 | Lane P2-a | Console Entry Link Button (`#consolelinkbtn`) | Quick navigation link in `#manage-section` | Click event, `IS_ADMIN` flag | Route to `/admin` or `/settings` | None; static navigation link | Design Doc §1.2 (D2), §5 |
| 9 | Lane P2-b | `/admin/instance` Console Screen | Display mode switch, G1–G9 checklist, sunset table, server tier form | `GET /admin/instance` (session cookie or bearer) | Full page HTML inside `console_shell.html` | Return 404 for non-administrator callers | `src/claire/api/accounts_routes/instance.py`, Design Doc §5 |
| 10 | Lane P2-b | `/admin/accounts` Console Screen | Display account overview, member table, lifecycle actions, invites, retired usernames | `GET /admin/accounts` | Full page HTML inside `console_shell.html` | Return 404 for non-administrator callers | `src/claire/api/accounts_routes/admin.py`, Design Doc §5 |
| 11 | Lane P2-b | `/admin/idp` Console Screen | Display external IdP registry, discovery check, policies, login simulator | `GET /admin/idp` | Full page HTML inside `console_shell.html` | Return 404 for non-administrator callers | `src/claire/api/accounts_routes/idp.py`, Design Doc §5 |
| 12 | Lane P2-b | `/admin/providers` Console Screen | Full-page console host for `renderProviderManager` | `GET /admin/providers` | Page inside `console_shell.html` host | Return 404 for non-administrator callers | Design Doc §2.1, §5 |
| 13 | Lane P2-b | `/admin/limits` Console Screen | Full-page console host for `renderLimitManager` | `GET /admin/limits` | Page inside `console_shell.html` host | Return 404 for non-administrator callers | Design Doc §2.1, §5 |
| 14 | Lane P2-b | `/admin/themes` Console Screen | Full-page host for `renderThemeManager` plus instance slot summary card | `GET /admin/themes` | Page inside `console_shell.html` host with aggregate counts | Return 404 for non-administrator callers | Design Doc §1.2 (D3), §2.1, §5 |
| 15 | Lane P2-b | `/admin/tokens` Console Screen | Full-page host for `renderTokenManager` plus OAuth authorized client list | `GET /admin/tokens` | Page inside `console_shell.html` host | Return 404 for non-administrator callers | Design Doc §1.2 (D3), §2.1, §5 |
| 16 | Lane P2-b | `/admin/mcp` Console Screen | Display live telemetry table, RBAC parity table, WebMCP policy, support bundle trigger | `GET /admin/mcp` | Page inside `console_shell.html` host | Return 404 for non-administrator callers | Design Doc §2.1, §5 |
| 17 | Lane P2-b | `GET /api/admin/mcp/telemetry` | Query MCP execution telemetry records from SQLite | Query params: `limit` (1..200), `status`, `tool_name`, `since` | JSON array of telemetry records and total count | Return 400 on invalid limit; 404 for non-admins | `src/claire/api/accounts_routes/console.py:589` |
| 18 | Lane P2-b | Step-up Reauthentication Modal | Re-verify administrator password before sensitive operations | Password input payload to `POST /api/auth/reauth` | Updates session `authenticated_at` timestamp | Return 401 when password does not match | `src/claire/api/accounts_routes/common.py:require_recent_auth` |
| 19 | Lane P2-b | Diagnostic Bundle Export Trigger | Trigger diagnostic support archive export | `POST /support/bundle` request | JSON response with download token and URL | Return 404 or 403 when caller is not admin | `src/claire/api/server.py:4517` |

---

## Edge Cases

| # | Feature | Input | Observed Behavior |
|---|---------|-------|-------------------|
| 1 | Non-administrator access to `/admin/*` | Request with `member`, `collaborator`, `readonly`, or anonymous session | Server returns HTTP 404 Not Found immediately without redirect. |
| 2 | Single mode username concealment | Caller requests `/admin/*` or `/whoami` while `instance.mode == "single"` | Response body, headers, and navigation omit `@username` strings (Rule S9). |
| 3 | Admin accesses `/settings/themes` or `/settings/tokens` | Administrator sends `GET /settings/themes` or `GET /settings/tokens` | Server returns HTTP 302 Found redirecting to `/admin/themes` or `/admin/tokens` (Rule D3). |
| 4 | Unknown section path in console | Caller requests `/admin/invalid-slug` or `/settings/invalid-slug` | Server returns HTTP 404 Not Found (Zero Speculation Rule C1). |
| 5 | Invalid `limit` parameter on telemetry API | `GET /api/admin/mcp/telemetry?limit=0` or `limit=999` or `limit=abc` | Server returns HTTP 400 Bad Request with `{"error": "invalid_limit"}`. |
| 6 | Stale administrator session during mode switch | Admin submits mode switch form with `authenticated_at` older than 15 minutes | Server rejects action with 401; client opens reauth password modal. |
| 7 | Switching to network mode with failed readiness | Admin attempts network mode switch when G1–G9 checklist has failed items | Form submit button remains disabled; mode switch API rejects with 400 error. |
| 8 | Switching to single mode with existing members | Admin attempts single mode switch when active, suspended, or invited members exist | Server rejects request with 400 error (`members_exist`). |
| 9 | Content peeking isolation on `/admin/themes` | Admin views `/admin/themes` summary card | Server queries only aggregate counts (`COUNT(*)`). Server never loads member theme names or text. |
| 10 | Token creation in `renderTokenManager` | Admin generates new token with `POST /tokens` | Server returns plaintext token once. UI shows copy box and never shows full plaintext again. |

---

## 1. Observation

### 1.1 Direct Source Code and Specification Citations
1. **Design Document Reference**:
   - In `docs/design/ADMIN_AND_SETTINGS_PATH_SEPARATION_DESIGN.md` lines 203–240:
     - Lane P2-a owns `src/claire/static/js/manage_panels.js` (new), `src/claire/static/js/app.js`, and `src/claire/templates/index.html`.
     - Lane P2-b owns `src/claire/api/accounts_routes/{admin,instance,idp,console}.py`, `src/claire/templates/console_shell.html`, `src/claire/static/js/console_admin.js` (new), and tests.
2. **Phase P1 Handoff Note Reference**:
   - In `.agent-work/admin-settings/P1.md` lines 18–22:
     - Confirms P1 completed with 0 test failures.
     - Confirms P2 lane file boundaries.
3. **Current Implementation in `src/claire/static/js/app.js`**:
   - Lines 4505–4541: `setAccessScope(scope, reason)` sets `AUTH_SCOPE` and toggles `#thememanagebtn`, `#providermanagebtn`, `#limitmanagebtn`, and `#tokenmanagebtn`.
   - Lines 4520–4525 show:
     ```javascript
     const pmBtn = document.getElementById('providermanagebtn');
     if(pmBtn) pmBtn.style.display = canWrite() ? '' : 'none';
     const lmBtn = document.getElementById('limitmanagebtn');
     if(lmBtn) lmBtn.style.display = canWrite() ? '' : 'none';
     ```
     `#providermanagebtn` and `#limitmanagebtn` currently check only `canWrite()`, not `IS_ADMIN`.
   - Lines 4544–4750: `openThemeManager()` and helper functions.
   - Lines 4901–5277: `openProviderManager()`, `renderProviderManager()`, `testProviderConnection()`, and `saveProviderSettings()`.
   - Lines 5281–5545: `openLimitManager()`, `renderLimitManager()`, `saveLimitSettings()`, and `resetLimitSettings()`.
   - Lines 5549–5700: `openTokenManager()`, `renderTokenManager()`, `createApiToken()`, and `revokeApiToken()`.
   - Lines 6003–6005: `fetch(rootUrl('whoami'))` updates scope but does not store `IS_ADMIN`.
4. **Current Implementation in `src/claire/templates/index.html`**:
   - Lines 412–422: `#manage-section` contains `#thememanagebtn`, `#providermanagebtn`, `#limitmanagebtn`, and `#tokenmanagebtn`.
   - `#consolelinkbtn` does not yet exist inside `#manage-section`.
5. **Current Implementation in `src/claire/api/accounts_routes/console.py`**:
   - Lines 218–310 define `ADMIN_SIDEBAR_GROUP_4_SPECS` and `ADMIN_CONSOLE_GROUP_5_SPECS`.
   - Lines 551–562: `admin_section_page` handles routes in `ADMIN_CONSOLE_BY_PATH` (`/admin/providers`, `/admin/limits`, `/admin/themes`, `/admin/tokens`, `/admin/mcp`).
   - Placeholder HTML is currently rendered for these sections (`<div class="console-placeholder" ...>`).
6. **Current Implementation of Admin Route Modules**:
   - `src/claire/api/accounts_routes/admin.py:118`: `admin_accounts_page` renders `admin_accounts.html`.
   - `src/claire/api/accounts_routes/instance.py:159`: `_render_instance_html` renders standalone HTML page.
   - `src/claire/api/accounts_routes/idp.py:751`: `admin_idp_page` renders `admin_idp.html`.
7. **Test Suite Verification Gate**:
   - Executing `uv run pytest tests/test_account_routes.py tests/test_oidc_flow.py tests/test_console_routing.py tests/test_single_mode_no_username_leak.py tests/test_server_tier.py tests/test_e2e_network_mode_transition.py` resulted in:
     `96 passed in 16.09s`.
   - Notice: `tests/test_admin_instance.py` named in the design doc does not exist as a file on disk; instance tests currently live in `tests/test_server_tier.py` and `tests/test_e2e_network_mode_transition.py`.

---

## 2. Logic Chain

1. **Step 1 — Extraction of Shared Management Modules (Lane P2-a)**:
   - Observation 1.3 shows that management functions for themes, providers, limits, and tokens currently reside in `src/claire/static/js/app.js` and render directly into `#panel`.
   - Observation 1.1 states that both the root drawer in `index.html` and the new unified console in `console_shell.html` require these controllers.
   - Inference: Extracting these routines into `src/claire/static/js/manage_panels.js` with explicit signatures `(container, opts)` allows:
     - `app.js` to call `render*Manager(document.getElementById('panel'), opts)`.
     - `console_admin.js` to call `render*Manager(document.getElementById('console-section-body'), opts)`.
     - Zero duplication of API interaction and DOM generation logic.

2. **Step 2 — Root Drawer Role Gating and Navigation (Lane P2-a)**:
   - Observation 1.3 shows that `#providermanagebtn` and `#limitmanagebtn` display for all callers who satisfy `canWrite()`.
   - Observation 1.1 requires that non-administrator members never see `#providermanagebtn` or `#limitmanagebtn`.
   - Inference: In `app.js`, `let IS_ADMIN = false;` must be set from `GET /whoami` (`data.is_admin === true`).
   - In `setAccessScope()`, display `#providermanagebtn` and `#limitmanagebtn` only when `canWrite() && IS_ADMIN`.
   - In `index.html`, add `#consolelinkbtn`. When `IS_ADMIN` is true, show `🛠️ 관리자 콘솔` (`href="/admin"`). When `canWrite() && !IS_ADMIN`, show `⚙️ 내 설정` (`href="/settings"`).

3. **Step 3 — Unification of Admin Screens into `console_shell.html` (Lane P2-b)**:
   - Observation 1.6 shows `admin.py`, `instance.py`, and `idp.py` currently render separate standalone HTML templates.
   - Observation 1.1 mandates that all `/admin/*` routes must render inside `console_shell.html`.
   - Inference: Route handlers in `instance.py`, `admin.py`, and `idp.py` must populate `console_shell.html` with:
     - Correct sidebar navigation (Groups 1–5 for administrator).
     - Form IDs, attributes, and scripts required by existing tests (`data-mode-switch-form`, `data-server-tier-form`, etc.).
     - Strict `Referrer-Policy: same-origin` and `Cache-Control: no-store` headers.

4. **Step 4 — Implementation of New Admin Sections (Lane P2-b)**:
   - In `console_shell.html` and `src/claire/static/js/console_admin.js`:
     - `/admin/providers`: Full-page host for `renderProviderManager`.
     - `/admin/limits`: Full-page host for `renderLimitManager`.
     - `/admin/themes`: Full-page host for `renderThemeManager` plus read-only instance slot count (`total_accounts`, `total_themes`).
     - `/admin/tokens`: Full-page host for `renderTokenManager` plus OAuth client list.
     - `/admin/mcp`: Live audit table via `GET /api/admin/mcp/telemetry`, RBAC parity, Permissions-Policy, and `POST /support/bundle` trigger.

5. **Step 5 — Invariant Protection**:
   - `PRINCIPLES.md` B1 and N5: Non-admins accessing `/admin/*` must receive 404 immediately.
   - `PRINCIPLES.md` C1: Unknown sections must fail with 404 (no default fallbacks).
   - `ACCOUNTS.md` D5 and K5: Zero admin content peeking. Administrator screens never read member themes, documents, or tokens.
   - `ACCOUNTS.md` S9: Single-mode username concealment. Omit `@username` in single mode.

---

## 3. Caveats

1. **`tests/test_admin_instance.py` Existence**:
   - The design document mentions `tests/test_admin_instance.py` in the verification gate command.
   - Investigation confirmed that `tests/test_admin_instance.py` does not exist in the repository. Current instance tests reside in `tests/test_server_tier.py` and `tests/test_e2e_network_mode_transition.py`.
   - Lane P2-b implementation should create `tests/test_admin_instance.py` to verify `/admin/instance` console rendering, or update the verification target so the verification gate succeeds cleanly without missing file errors.
2. **`test_account_routes.py` Assertion on Scripts**:
   - Line 162 of `tests/test_account_routes.py` asserts `assert "<script" not in page.text` on `/admin/accounts`.
   - Because `console_shell.html` includes `<script id="console-bootstrap">` and client controller scripts, `test_account_routes.py` must be updated in Lane P2-b (which owns `tests/test_account_routes.py`) to align with console shell requirements.
3. **Hotspot File Boundary**:
   - `src/claire/static/js/app.js` is modified only by Lane P2-a. Lane P2-b must not touch `app.js`.

---

## 4. Conclusion & Complete Specification Inventory

### 4.1 Interface Contract: `src/claire/static/js/manage_panels.js`

The new module `src/claire/static/js/manage_panels.js` must export four global functions to `window` (or shared scope):

#### 1. `renderProviderManager(container, opts)`
- **Parameters**:
  - `container` (HTMLElement, required): DOM element to render into.
  - `opts` (Object, optional):
    - `apiBase` (string): API prefix (default `""`).
    - `isAdmin` (boolean): Default `true`.
    - `showCloseButton` (boolean): Default `false` for console, `true` for drawer.
    - `onClose` (function): Callback when close button is clicked.
    - `onSave` (function): Callback when configuration is saved successfully.
- **API Endpoints**:
  - Read: `GET /providers` (returns `{ active_provider, effective_provider, providers: {...} }`).
  - Write: `PATCH /providers` with `{ active_provider, providers: {...} }`.
  - Test: `POST /providers/test` with `{ provider, config: {...} }`.
- **UI Elements**:
  - Provider select `#prov-active-select` (`gemini`, `antigravity`, `codex`, `openai`, `mock`).
  - Configuration cards for Gemini, Antigravity, Codex, OpenAI, Jev, and STT.
  - Masked password fields with eye toggle buttons (`togglePassVisibility`).
  - Save button and status alert banners.

#### 2. `renderLimitManager(container, opts)`
- **Parameters**:
  - `container` (HTMLElement, required): DOM element to render into.
  - `opts` (Object, optional):
    - `apiBase` (string): API prefix (default `""`).
    - `isAdmin` (boolean): Default `true`.
    - `showCloseButton` (boolean): Default `false` for console, `true` for drawer.
    - `onClose` (function): Callback when close button is clicked.
    - `onSave` (function): Callback when limits are saved successfully.
- **API Endpoints**:
  - Read: `GET /limits` (returns `{ limits: {...} }`).
  - Write: `PATCH /limits` with `{ limits: {...} }`.
- **UI Elements**:
  - Numeric inputs for raw char budget, PDF max extraction, paper threshold, KG extraction budgets, embedding budgets, and media limits.
  - Dropdown selectors for slicing strategy (`table-exemption` vs `strict`) and effort tiers.
  - Checkboxes for PDF appendix exclusion, references exclusion, and encoding defect detection.
  - Reset to default limits button (`resetLimitSettings`).

#### 3. `renderThemeManager(container, opts)`
- **Parameters**:
  - `container` (HTMLElement, required): DOM element to render into.
  - `opts` (Object, optional):
    - `apiBase` (string): API prefix (default `""`).
    - `isOwner` (boolean): Default `true`.
    - `isCollaborator` (boolean): Default `false`.
    - `isAdmin` (boolean): Default `false`.
    - `showCloseButton` (boolean): Default `false` for console, `true` for drawer.
    - `onThemeChanged` (function): Callback when themes are added, modified, or deleted.
- **API Endpoints**:
  - Read: `GET /themes` (returns `{ themes: [...] }`).
  - Create: `POST /themes` with `{ label, description, icon, is_public, ... }`.
  - Update: `PATCH /themes` with `{ id, label, description, icon, is_public, ... }`.
  - Delete: `DELETE /themes?id={id}`.
- **UI Elements**:
  - Theme list cards with badges for public, private, collaborator, presentation, and legacy hosts.
  - Inline edit form and options details toggle.
  - New theme creation form (shown only when `isOwner` is true).

#### 4. `renderTokenManager(container, opts)`
- **Parameters**:
  - `container` (HTMLElement, required): DOM element to render into.
  - `opts` (Object, optional):
    - `apiBase` (string): API prefix (default `""`).
    - `isOwner` (boolean): Default `true`.
    - `isAdmin` (boolean): Default `false`.
    - `showCloseButton` (boolean): Default `false` for console, `true` for drawer.
    - `onTokenCreated` (function): Callback with newly issued token object.
    - `onTokenRevoked` (function): Callback when token is revoked.
- **API Endpoints**:
  - Read: `GET /tokens` (returns `{ tokens: [...] }`).
  - Create: `POST /tokens` with `{ name, scope, expires_days }`.
  - Revoke: `POST /tokens/revoke` with `{ id }`.
- **UI Elements**:
  - Token creation form with name, scope (`readonly`, `collaborator`, `owner`), and expiration select.
  - Prominent one-time token display banner with copy button and JSON setup snippet.
  - Existing token list with ID, name, masked identifier (`cb_...`), expiration, last used date, and revoke button.

---

### 4.2 Specification for the 8 `/admin/*` Sections

All routes require `_system("admin")` and return `404 Not Found` for any caller without administrator privileges.

1. **`/admin/instance`** (`Group 4: 인스턴스 · 계정 · 인증`):
   - **Template**: Rendered inside `console_shell.html`.
   - **Preserved Forms and IDs**:
     - Mode switch card: `<section class="card" aria-label="인스턴스 모드" id="instance-mode">`
     - Form: `<form class="mode-form" data-mode-switch-form>`
     - Target mode hidden input: `<input type="hidden" name="target_mode" value="...">`
     - Step-up reauth form: `<form class="mode-form" data-mode-switch-reauth hidden>`
     - Server tier card: `<section class="card" aria-label="서버 등급" id="server-tier">`
     - Server tier form: `<form class="tier-form" data-server-tier-form>`
     - Server tier reauth form: `<form class="tier-form" data-server-tier-reauth hidden>`
   - **Checklists & Tables**:
     - G1–G9 readiness checklist table with item ID, name, status, and detail.
     - Legacy host sunset schedule table with host, theme ID, activation date, and sunset date.
   - **Scripts**: Include `<script src="/static/js/instance_mode.js" defer></script>` and `<script src="/static/js/server_tier.js" defer></script>`.

2. **`/admin/accounts`** (`Group 4: 인스턴스 · 계정 · 인증`):
   - **Template**: Rendered inside `console_shell.html`.
   - **Sections**:
     - Admin overview card with public ID, status, role, creation date, activation date, and theme count.
     - Member directory table: lists public ID, username (in network mode), role, status, theme count, key status.
     - Lifecycle action buttons: `suspend`, `resume`, `schedule_delete`, `cancel_delete`, `purge`, `reset_password_link`.
     - Invitations card (active in network mode): list invitations and issue new invite codes via `/api/invites`.
     - Retired username management card: list retired usernames via `GET /api/admin/usernames/retired` and release via `POST /api/admin/usernames/release` (preview mode when `apply=false`, release execution when `apply=true`).
     - Operator workspace summary card migrated from `panel-userdb` ("운영자 보기"): directory layout explanation, default quotas grid.

3. **`/admin/idp`** (`Group 4: 인스턴스 · 계정 · 인증`):
   - **Template**: Rendered inside `console_shell.html`.
   - **Sections**:
     - Provider registry table and card: displays registered providers loaded from `GET /api/admin/idp`.
     - Provider CRUD form: input provider type, issuer URL, client ID, display name, scopes; Discovery verification check.
     - Login mode selector: `telegram_only` vs `telegram_and_idp`.
     - Signup and account mapping policy: provisioning mode (`approval`, `auto`, `invite`), allowed email domains, verified email requirement.
     - Back-Channel Logout toggle.
     - Login simulator migrated from `panel-idp`: persona selector and step-by-step simulation display.

4. **`/admin/providers`** (`Group 5: 시스템 관리 및 설정`):
   - **Template**: Rendered inside `console_shell.html`.
   - **Body**: Container element hosting `renderProviderManager(container, { showCloseButton: false, isAdmin: true })`.
   - **Script**: Loads `manage_panels.js` and `console_admin.js`.

5. **`/admin/limits`** (`Group 5: 시스템 관리 및 설정`):
   - **Template**: Rendered inside `console_shell.html`.
   - **Body**: Container element hosting `renderLimitManager(container, { showCloseButton: false, isAdmin: true })`.
   - **Script**: Loads `manage_panels.js` and `console_admin.js`.

6. **`/admin/themes`** (`Group 5: 시스템 관리 및 설정`):
   - **Template**: Rendered inside `console_shell.html`.
   - **Body**:
     - Full-page host for `renderThemeManager` managing administrator's themes (`ADMIN_ACCOUNT_ID = 1`).
     - Read-only instance summary card:
       - Total accounts count (`SELECT COUNT(*) FROM accounts`).
       - Total themes count across instance (`SELECT COUNT(*) FROM themes`).
       - Invariant: Zero content peeking. Do not query or display member theme names or text.

7. **`/admin/tokens`** (`Group 5: 시스템 관리 및 설정`):
   - **Template**: Rendered inside `console_shell.html`.
   - **Body**:
     - Full-page host for `renderTokenManager` managing administrator's tokens (`ADMIN_ACCOUNT_ID = 1`).
     - Admin OAuth 2.1 authorized client list card.
     - Instance token policy summary.

8. **`/admin/mcp`** (`Group 5: 시스템 관리 및 설정`):
   - **Template**: Rendered inside `console_shell.html`.
   - **Body**:
     - Live `mcp_telemetry` audit table: fetches rows from `GET /api/admin/mcp/telemetry`.
     - Filter controls: limit selector (default 50, max 200), status filter, tool name filter.
     - RBAC vs MCP parity table: shows tool catalog mapped to permissions (`readonly`, `collaborator`, `owner`).
     - Global WebMCP `Permissions-Policy: tools=()` status card.
     - Diagnostic export button triggering `POST /support/bundle`.

---

### 4.3 Cross-Cutting Requirements

1. **Step-up Reauthentication**:
   - Enforced on sensitive admin APIs via `require_recent_auth(principal)` (validity: 15 minutes / 900 seconds).
   - Endpoints requiring fresh authentication:
     - `POST /api/admin/instance/mode`
     - `POST /api/admin/instance/legacy-hosts/sunset`
     - `PUT /api/admin/server-tier`
     - `PATCH /api/admin/accounts`
     - `POST /api/admin/usernames/release`
   - Client-side behavior: When an API call returns 401 with `step_up_auth_required`, client displays reauth modal requesting password, calls `POST /api/auth/reauth`, and retries the operation on success.

2. **Single Mode vs. Network Mode**:
   - In `single` mode:
     - Rule S9: Hide all `@username` identifiers in HTML, JSON, and redirects.
     - Back links point to `/` and `/syndication`.
     - Quota notice on BYOK keys explains instance providers are configured at `/admin/providers`.
     - Mode switch to network mode is gated behind G1–G9 readiness checks.
   - In `network` mode:
     - Back links point to `/@{username}/` and `/@{username}/syndication`.
     - Invitations and retired username management are active.
     - Mode switch to single mode requires purging all non-admin members first.

3. **Telemetry and Support Bundle Export**:
   - Telemetry endpoint `GET /api/admin/mcp/telemetry`:
     - Reads bounded records (`limit <= 200`) from `query_mcp_telemetry`.
     - Rejects invalid limit values with HTTP 400.
     - Protected by `_system("admin")`.
   - Support bundle endpoint `POST /support/bundle`:
     - Generates diagnostic archive bundle.
     - Returns download URL with one-time access token.

---

## 5. Verification Method

### 5.1 Project Test Command
Run the primary Phase P2 test suite:
```bash
uv run pytest tests/test_account_routes.py tests/test_oidc_flow.py tests/test_console_routing.py tests/test_single_mode_no_username_leak.py tests/test_server_tier.py tests/test_e2e_network_mode_transition.py
```

### 5.2 Test File Inspection and Additions
1. Create `tests/test_admin_instance.py` in Lane P2-b to test `/admin/instance` HTML rendering in `console_shell.html`.
2. Verify all 8 `/admin/*` routes return `200 OK` for administrator and `404 Not Found` for non-admin accounts in `tests/test_console_routing.py`.
3. Verify `tests/test_account_routes.py` passes when `/admin/accounts` renders inside `console_shell.html`.
4. Run syntax and route surface checks:
```bash
uv run pytest tests/test_route_surface_contract.py tests/test_username_policy.py
```

### 5.3 Invalidation Conditions
- Any `/admin/*` route returns `403 Forbidden` or `302 Found` instead of `404 Not Found` to a non-admin caller.
- Non-admin responses contain administrator DOM sections or navigation links.
- Single-mode responses leak `@username` strings.
- Hard-wrapped code lines are introduced into JS, HTML, CSS, or Python files.
