# Handoff Report — Lane P2-b Codebase Investigation

## 1. Observation

### 1.1 Console Shell Skeleton and Route Mounting
- In `src/claire/templates/console_shell.html` (lines 37–69), the base console shell contains:
  - Topbar with back links: `id="console-back-graph"`, `id="console-back-syndication"`.
  - Logout form: `<form method="post" action="/logout" class="logout-form"><button type="submit" class="console-btn">로그아웃</button></form>`.
  - Content container: `<section class="console-card" id="console-section-$active_section" data-section="$active_section"><h1><span>$section_title</span> <span class="console-badge">$section_badge</span></h1><p class="console-desc">$section_desc</p><div id="console-section-body">$section_body_html</div></section>`.
  - Bootstrap payload tag: `<script id="console-bootstrap" type="application/json">$bootstrap_json</script>`.
  - `console_shell.html` currently lacks `<script src="/static/js/manage_panels.js" defer></script>` and `<script src="/static/js/console_admin.js" defer></script>`.
- In `src/claire/api/accounts_routes/console.py`:
  - Lines 218–252 define `ADMIN_SIDEBAR_GROUP_4_SPECS` for `instance` (`/admin/instance`), `accounts` (`/admin/accounts`), and `idp` (`/admin/idp`).
  - Lines 254–310 define `ADMIN_CONSOLE_GROUP_5_SPECS` for `providers` (`/admin/providers`), `limits` (`/admin/limits`), `themes` (`/admin/themes`), `tokens` (`/admin/tokens`), and `mcp` (`/admin/mcp`).
  - Line 589 implements `admin_mcp_telemetry_api` (`GET/HEAD /api/admin/mcp/telemetry`).
  - Lines 614–623 mount `/admin`, `/settings`, `/api/admin/mcp/telemetry`, `ADMIN_CONSOLE_GROUP_5_SPECS`, and `ALL_SETTINGS_SPECS`. `ADMIN_SIDEBAR_GROUP_4_SPECS` are currently not mounted in `console.py` because `instance.py`, `admin.py`, and `idp.py` mount them separately.

### 1.2 Current Screens 1–3 Implementation and Test Assertions
- In `src/claire/api/accounts_routes/instance.py`:
  - Lines 123–146 define `_render_server_tier_section`: `<section class="card" aria-label="서버 등급" id="server-tier">` with `data-server-tier-form` and `data-server-tier-reauth`.
  - Lines 235–350 define `_render_instance_html` which renders a full standalone HTML document with navigation links, `id="instance-mode"`, `data-mode-switch-form`, `data-mode-switch-reauth`, G1–G9 checklist table, legacy host table, and server tier section.
  - Line 354 mounts `Route(ADMIN_INSTANCE_PATH, admin_instance_page, methods=["GET", "HEAD"])`.
  - In `tests/test_single_mode_no_username_leak.py` lines 375–379, tests assert `"인스턴스 설정"`, `"준비 검사 (G1–G9)"`, and `"레거시 호스트 일몰 시계"` in `GET /admin/instance` response text.
  - In `tests/test_server_tier.py` lines 671–678, tests assert `id="server-tier"`, `data-server-tier-form`, and field names (`provider`, `model`, `monthly_tokens`, `daily_ingests`, `max_effort`, `storage_mb`, `max_themes`, `max_documents`, `byok_max_concurrency`, `features`).
- In `src/claire/api/accounts_routes/admin.py`:
  - Lines 118–124 define `admin_accounts_page` which calls `render_template("admin_accounts.html", markup={"cards": cards})`.
  - Lines 126–340 define `admin_accounts_api` (`GET /api/admin/accounts`), `admin_accounts_patch` (`PATCH /api/admin/accounts`), `admin_retired_usernames_api` (`GET /api/admin/usernames/retired`), and `admin_release_username_api` (`POST /api/admin/usernames/release`).
  - In `tests/test_account_routes.py` lines 159–163:
    - Line 160: `assert page.status_code == 200`.
    - Line 161: `assert "관리자 계정" in page.text and admin["public_id"] in page.text and USERNAME in page.text`.
    - Line 162: `assert "<script" not in page.text`.
    - Line 352–353: `assert page.headers["referrer-policy"] == "same-origin"` and `assert 'action="/logout"' in page.text`.
- In `src/claire/api/accounts_routes/idp.py`:
  - Lines 751–758 define `admin_idp_page` which calls `render_template("admin_idp.html", markup={"cards": cards})`.
  - Lines 760–830 define `admin_idp_get` (`GET /api/admin/idp`) and `admin_idp_put` (`PUT /api/admin/idp`).
  - In `tests/test_oidc_flow.py` lines 532–536, tests assert `status_code == 200`, `"외부 IdP 관리" in resp_html.text`, and provider display name (e.g., `"Google Workspace"`) in `resp_html.text`.
  - In `src/claire/templates/syndication_preferences.html` lines 816–936 and 1938–2056, `panel-idp` contains the full IdP table (`#pref-idp-tbody`), add provider form (`#pref-idp-add-form`), policy inputs (`#pref-idp-mode`, `#pref-idp-provisioning`, `#pref-idp-domains`, `#pref-idp-verified`, `#pref-idp-bcl`), and login simulator (`#pref-idp-login-preview`, `#pref-idp-persona`, `#btn-idp-sim-run`, `#pref-idp-sim-output`).

### 1.3 Screens 4–8 Requirements and Assets
- In `src/claire/static/js/app.js` lines 4901–5100, `renderProviderManager`, `renderLimitManager`, `renderThemeManager`, and `renderTokenManager` currently render into drawer `#panel`. Lane P2-a extracts them into `src/claire/static/js/manage_panels.js`.
- In `src/claire/templates/syndication_preferences.html`:
  - `panel-userdb` (lines 939–1024, 2081–2140): `#pref-userdb-tbody`, `#pref-userdb-stats`, `#pref-quota-storage`, `#pref-quota-docs`, `#pref-quota-themes`, `#pref-quota-llm`, `#pref-quota-feeds`, `#pref-quota-byo`, and `data-userdb-view="admin"`.
  - `panel-mcp` (lines 620–706, 1630–1708): `#pref-mcp-clients-tbody`, `#pref-mcp-rbac-tbody`, `#pref-mcp-audit-tbody`.
  - `panel-webmcp` (lines 710–730): `#pref-webmcp-enabled` (controls global `Permissions-Policy: tools=()`).
- In `src/claire/api/server.py`:
  - Line 4459: `Route("/providers", providers_get_route, methods=["GET", "HEAD"])`, `Route("/providers", providers_update_route, methods=["PATCH", "POST"])`.
  - Line 4461: `Route("/providers/test", providers_test_route, methods=["POST"])`.
  - Line 4462: `Route("/limits", limits_get_route, methods=["GET", "HEAD"])`, `Route("/limits", limits_update_route, methods=["PATCH", "POST"])`.
  - Line 4468: `Route("/ingest-limits", limits_get_route, methods=["GET", "HEAD"])`, `Route("/ingest-limits", limits_update_route, methods=["PATCH", "POST"])`.
  - Line 4455: `Route("/themes", themes_list_route, methods=["GET", "HEAD"])`, `Route("/themes", theme_define_route, methods=["POST"])`.
  - Line 4464: `Route("/tokens", tokens_list_route, methods=["GET", "HEAD"])`, `Route("/tokens", tokens_create_route, methods=["POST"])`, `Route("/tokens/revoke", tokens_revoke_route, methods=["POST"])`.
  - Line 4517: `Route("/support/bundle", create_support_bundle_route, methods=["POST"])`.

### 1.4 Test Verification Gate Findings
- The command `git log --all -p -- tests/test_admin_instance.py` returned code 0 with no history. The file `tests/test_admin_instance.py` does not exist in the repository.
- Running `uv run pytest tests/test_account_routes.py tests/test_oidc_flow.py tests/test_console_routing.py tests/test_single_mode_no_username_leak.py` passed 57 tests in 13.39 seconds.
- In `tests/test_account_routes.py` line 162, `assert "<script" not in page.text` will fail when `/admin/accounts` renders inside `console_shell.html` because `console_shell.html` loads scripts.

---

## 2. Logic Chain

1. **Unification Architecture**:
   - Observation 1.1 shows `console_shell.html` is the unified shell layout with topbar, sidebar, and section container.
   - Observation 1.2 shows `/admin/instance`, `/admin/accounts`, and `/admin/idp` currently render standalone HTML outside `console_shell.html`.
   - Therefore, `admin.py`, `instance.py`, and `idp.py` must render `console_shell.html` with their respective section content in `section_body_html`, or delegate section rendering through `console.py`.
   - Retaining the route handlers in `admin.py`, `instance.py`, and `idp.py` while using `console.py:_render_console_section` preserves existing module separation and avoids touching `build_account_routes`.

2. **Step-Up Reauthentication Wiring**:
   - Observation 1.2 and `instance_mode.js`, `server_tier.js` show that when an API returns HTTP 401 with `error === "reauth_required"`, client JavaScript submits `{password: password}` to `POST /api/auth/reauth`.
   - A single reauthentication modal component in `console_shell.html` (e.g., `<dialog id="console-reauth-modal">` or `<div id="console-reauth-modal" class="console-modal" hidden>`) with form `data-console-reauth-form` can serve all admin screens.
   - When any admin action receives `reauth_required`, `console_admin.js` opens this modal, sends `POST /api/auth/reauth`, closes the modal on success, and re-executes the original action.

3. **8 Admin Screens Breakdown**:
   - **Screen 1 (`/admin/instance`)**: Renders mode status, mode switch form (`data-mode-switch-form`), G1–G9 checklist table, legacy host table, and server tier form (`data-server-tier-form`). Preserves all text tokens for `test_single_mode_no_username_leak.py` and `test_server_tier.py`.
   - **Screen 2 (`/admin/accounts`)**: Renders admin account card (`"관리자 계정"`), member table with lifecycle actions (`suspend`, `resume`, `schedule_delete`, `cancel_delete`, `purge`, `reset_password_link`), invitations table/form via `/api/invites` (in network mode), retired username release controls via `/api/admin/usernames/*`, and operator directory view (`#pref-userdb-tbody`, `#pref-userdb-stats`) from `panel-userdb`.
   - **Screen 3 (`/admin/idp`)**: Renders IdP provider cards/table (`#pref-idp-tbody`), add form (`#pref-idp-add-form`), policy controls (`#pref-idp-mode`, `#pref-idp-provisioning`, `#pref-idp-domains`, `#pref-idp-verified`, `#pref-idp-bcl`), and login simulator (`#pref-idp-login-preview`, `#pref-idp-persona`, `#btn-idp-sim-run`, `#pref-idp-sim-output`). Preserves `"외부 IdP 관리"` for `test_oidc_flow.py`.
   - **Screen 4 (`/admin/providers`)**: Mounts full-page container for `window.renderProviderManager(container, opts)` from `manage_panels.js`.
   - **Screen 5 (`/admin/limits`)**: Mounts full-page container for `window.renderLimitManager(container, opts)` from `manage_panels.js`.
   - **Screen 6 (`/admin/themes`)**: Renders aggregate instance summary card (`total_accounts`, `total_themes` via SQL count without reading member theme content) and mounts container for `window.renderThemeManager(container, opts)` for admin themes (`account_id = 1`).
   - **Screen 7 (`/admin/tokens`)**: Mounts container for `window.renderTokenManager(container, opts)` for admin tokens and renders OAuth 2.1 client table (`#pref-mcp-clients-tbody`).
   - **Screen 8 (`/admin/mcp`)**: Renders live `mcp_telemetry` table (populated via `GET /api/admin/mcp/telemetry`), RBAC parity table (`#pref-mcp-rbac-tbody`), WebMCP policy switch (`#pref-webmcp-enabled`), and support bundle export button (`POST /support/bundle`).

4. **Test Suite Adaptation**:
   - Observation 1.4 shows `tests/test_admin_instance.py` does not exist. If pytest runs `tests/test_admin_instance.py`, pytest exits with an error. The implementer must create `tests/test_admin_instance.py` with tests for `/admin/instance` console rendering, mode switch, and server tier.
   - Observation 1.4 shows line 162 of `tests/test_account_routes.py` asserts `<script` not in `GET /admin/accounts` text. Because `console_shell.html` contains `<script id="console-bootstrap"...>`, line 162 must be updated (e.g., assert `<script id="console-bootstrap"` in page.text or remove the negative check).

---

## 3. Caveats

- Lane P2-a is concurrently creating `src/claire/static/js/manage_panels.js`. Lane P2-b depends on `manage_panels.js` for `renderProviderManager`, `renderLimitManager`, `renderThemeManager`, and `renderTokenManager`.
- In `single` mode, invitations (`/api/invites`) return HTTP 404 by design. Screen 2 (`/admin/accounts`) must display a notice in single mode rather than failing.
- Member themes and personal secrets must not leak in any admin query (invariants ACCOUNTS.md D5, K5). Theme summary card on `/admin/themes` must use SQL `COUNT(*)` only.

---

## 4. Conclusion

Lane P2-b implementation requires modifications strictly across its owned files:
1. `src/claire/templates/console_shell.html`:
   - Add `<script src="/static/js/manage_panels.js" defer></script>`.
   - Add `<script src="/static/js/console_admin.js" defer></script>`.
   - Add shared reauthentication modal markup (`<dialog id="console-reauth-modal">`).
2. `src/claire/api/accounts_routes/console.py`:
   - Extend `_render_console_section` to support passing custom `section_body_html` and extra bootstrap data.
   - Implement section body builders for `/admin/providers`, `/admin/limits`, `/admin/themes` (with SQL counts), `/admin/tokens`, and `/admin/mcp`.
3. `src/claire/api/accounts_routes/instance.py`:
   - Render `/admin/instance` using `_render_console_section` with preserved forms and ids (`#server-tier`, `data-mode-switch-form`, G1–G9 checklist).
4. `src/claire/api/accounts_routes/admin.py`:
   - Render `/admin/accounts` using `_render_console_section` with admin card, member table, invites, and retired username controls.
5. `src/claire/api/accounts_routes/idp.py`:
   - Render `/admin/idp` using `_render_console_section` with IdP table, discovery form, policy inputs, and login simulator.
6. `src/claire/static/js/console_admin.js` (new file):
   - Client controller dispatching by `bootstrap.section` to wire forms, fetch APIs, bind reauth modal, and invoke `manage_panels.js` functions.
7. `tests/test_account_routes.py` and `tests/test_admin_instance.py`:
   - Update line 162 in `test_account_routes.py` to allow script tags in `/admin/accounts`.
   - Create `tests/test_admin_instance.py` covering `/admin/instance` in the console shell.

---

## 5. Verification Method

To verify the investigation and subsequent implementation independently:

1. Inspect console shell template:
   ```bash
   grep -n "console-bootstrap" src/claire/templates/console_shell.html
   ```
2. Verify route surface contract:
   ```bash
   uv run pytest tests/test_route_surface_contract.py
   ```
3. Run test gate after implementation:
   ```bash
   uv run pytest tests/test_account_routes.py tests/test_admin_instance.py tests/test_oidc_flow.py tests/test_console_routing.py tests/test_single_mode_no_username_leak.py
   ```
4. Verify non-leakage invariant in single mode:
   ```bash
   uv run pytest tests/test_single_mode_no_username_leak.py
   ```
