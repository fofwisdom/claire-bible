# Dispatch: Explorer P2B-1

## Identity
- Role: Codebase Investigator (teamwork_explorer)
- Working Directory: /home/fow/Projects/claire-bible/.agents/teamwork/explorer_p2b_1

## Mandatory Instruction
Read /home/fow/Projects/claire-bible/.agents/teamwork/ORIGINAL_REQUEST.md completely before you start work.
Follow user rules: Use ASD-STE100 English for all tasks and reports. Do not modify or write source code files.

## Investigation Scope (Lane P2-b: Unified `admin/` Console Screens)
Investigate current admin routes and console implementation:
1. `src/claire/templates/console_shell.html` and `src/claire/static/js/console_admin.js`:
   - How `console_shell.html` handles navigation, active section switching, breadcrumbs, `action="/logout"`, `Referrer-Policy: same-origin`.
   - How existing screens (`/admin/instance`, `/admin/accounts`, `/admin/idp`) are rendered.
   - How step-up reauth modal (`/api/auth/reauth`) is wired.
2. Migration of remaining 5 `/admin/*` screens:
   - `/admin/providers`: integration with `renderProviderManager`.
   - `/admin/limits`: integration with `renderLimitManager`.
   - `/admin/themes`: integration with `renderThemeManager` + read-only instance summary card (`total_accounts`, `total_themes` count).
   - `/admin/tokens`: integration with `renderTokenManager` + admin OAuth 2.1 client list.
   - `/admin/mcp`: live `mcp_telemetry` audit table (`GET /api/admin/mcp/telemetry`), RBAC vs MCP parity table, WebMCP `Permissions-Policy`, and `POST /support/bundle` export.
3. Relevant backend routes and test contracts:
   - Inspect backend routes in `src/claire/` handling `/admin/*`, `/api/admin/*`, `/api/auth/reauth`, `/support/bundle`.
   - Inspect tests: `tests/test_account_routes.py`, `tests/test_admin_instance.py`, `tests/test_oidc_flow.py`, `tests/test_console_routing.py`, `tests/test_single_mode_no_username_leak.py`.

## Expected Deliverables
Write `handoff.md` in your working directory (/home/fow/Projects/claire-bible/.agents/teamwork/explorer_p2b_1/handoff.md) containing:
1. Exact structure and requirements for all 8 admin screens in `console_shell.html` and `console_admin.js`.
2. Existing form IDs, element IDs, JSON payload contracts that must be preserved.
3. Backend endpoints and any needed route updates or template context.
4. Test mapping for all acceptance criteria.
Send a completion message back when done.
