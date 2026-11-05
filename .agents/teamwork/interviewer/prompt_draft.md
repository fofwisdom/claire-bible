Implement Phase P2 (Shared Management Modules, Root '관리 및 설정' Integration (D2), and Full `admin/` Console) of Admin and Settings Path Separation for the Claire repository, covering both Lane P2-a and Lane P2-b.

Integrity mode: development

## References
- Design specification: `docs/design/ADMIN_AND_SETTINGS_PATH_SEPARATION_DESIGN.md` (Section 4 and Phase P2)
- Phase P1 handoff note: `.agent-work/admin-settings/P1.md`
- Architectural principles: `docs/PRINCIPLES.md`

## Requirements

### R1. Lane P2-a: Shared Management Controllers & Root `'관리 및 설정'` Drawer Integration (D2)
- Extract reusable DOM/API controller helpers (`renderProviderManager(container, opts)`, `renderLimitManager(container, opts)`, `renderThemeManager(container, opts)`, `renderTokenManager(container, opts)`) into `src/claire/static/js/manage_panels.js` (loaded by both `index.html` and `console_shell.html`).
- In `src/claire/static/js/app.js`:
  - Track `let IS_ADMIN = false;` populated from `GET /whoami` (`data.is_admin === true`). Reset `IS_ADMIN = false` when auth fails or expires.
  - In `setAccessScope`: display `#providermanagebtn` and `#limitmanagebtn` only when `canWrite() && IS_ADMIN`. Keep `#thememanagebtn` and `#tokenmanagebtn` visible for `canWrite()` (and collaborator read view on themes).
- In `src/claire/templates/index.html` (`#manage-section`):
  - Add entry link/button `#consolelinkbtn` inside `#manage-section`: when `IS_ADMIN` is true, label `🛠️ 관리자 콘솔` (`href="/admin"`); when `canWrite() && !IS_ADMIN`, label `⚙️ 내 설정` (`href="/settings"`). Keep inline right-drawer actions working via `manage_panels.js`.

### R2. Lane P2-b: Unified `admin/` Console Screens
- Render `/admin/instance`, `/admin/accounts`, and `/admin/idp` inside `console_shell.html` while preserving existing form IDs, JSON payload contracts, `Referrer-Policy: same-origin`, `action="/logout"`, step-up reauth modals (`/api/auth/reauth`), and single-mode vs. network-mode notices tested by `tests/test_account_routes.py`, `tests/test_admin_instance.py`, and `tests/test_oidc_flow.py`:
  - `/admin/instance`: Mode switch, G1–G9 checklist, legacy host sunset, and `ServerTier` default quota form (`/api/admin/server-tier`).
  - `/admin/accounts`: Admin overview, member table + lifecycle actions, invitations (`/api/invites` in network mode), retired `USERNAME` release (`/api/admin/usernames/*`), and workspace directory overview migrated from `panel-userdb` (`운영자 보기`).
  - `/admin/idp`: OIDC/GitHub provider CRUD + Discovery check (`/api/admin/idp`), login/signup/domain policy controls, Back-Channel Logout toggle, and login simulator migrated from `panel-idp`.
- Implement the remaining 5 `/admin/*` sections inside `console_shell.html` + `src/claire/static/js/console_admin.js` (using `manage_panels.js`):
  - `/admin/providers`: Full-page host for `renderProviderManager` (`/providers`, `/providers/test`).
  - `/admin/limits`: Full-page host for `renderLimitManager` (`/limits`, `/ingest-limits`).
  - `/admin/themes`: Full-page host for `renderThemeManager` (admin's own themes) + read-only instance summary card (`total_accounts`, `total_themes` count from SQLite without reading member theme names/descriptions).
  - `/admin/tokens`: Full-page host for `renderTokenManager` (admin's own tokens) + admin OAuth 2.1 authorized client list.
  - `/admin/mcp`: Live `mcp_telemetry` audit table fetched from `GET /api/admin/mcp/telemetry`, RBAC vs. MCP parity table, global WebMCP `Permissions-Policy` policy card, and `POST /support/bundle` export trigger.

### R3. Implementation and Safety Constraints
- Never hard-wrap code lines in Python, JavaScript, HTML, or CSS files.
- Isolate all test state in `tmp_path` and clear `CLAIRE_*` environment variables in test fixtures; never write to repository root `data/` and never touch production hosts.
- Write unit tests strictly for module interfaces, I/O operations, and calls to external libraries.
- Respect hotspot file boundaries: `src/claire/static/js/app.js` is edited only in Lane P2-a.
- Record completion notes in `.agent-work/admin-settings/P2.md`.

## Acceptance Criteria

### Test Suite Verification Gate
- [ ] `uv run pytest tests/test_account_routes.py tests/test_admin_instance.py tests/test_oidc_flow.py tests/test_console_routing.py tests/test_single_mode_no_username_leak.py` passes with 0 failures.
- [ ] Existing test suites in the repository remain green.
- [ ] Phase P2 completion documented in `.agent-work/admin-settings/P2.md`.
