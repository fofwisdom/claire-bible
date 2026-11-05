# Implementation Plan — Phase P2

## 1. Objective
Complete Phase P2 of Admin and Settings Path Separation for the Claire repository.
Cover Lane P2-a (Shared Management Controllers & Root Drawer Integration) and Lane P2-b (Unified `admin/` Console Screens).

## 2. Approach (Project Pattern)
1. **Phase 0: Survey**
   - Explorer 1 (`teamwork_spec_miner`): Extract design specification details from `docs/design/ADMIN_AND_SETTINGS_PATH_SEPARATION_DESIGN.md` and `.agent-work/admin-settings/P1.md`.
   - Explorer 2 (`teamwork_explorer`): Inspect Lane P2-a code surfaces (`src/claire/static/js/app.js`, `src/claire/templates/index.html`, and how providers/limits/themes/tokens panels currently work).
   - Explorer 3 (`teamwork_explorer`): Inspect Lane P2-b code surfaces (`src/claire/templates/console_shell.html`, `src/claire/static/js/console_admin.js`, routes in `src/claire/`, existing admin screens `/admin/instance`, `/admin/accounts`, `/admin/idp`, and remaining sections `/admin/providers`, `/admin/limits`, `/admin/themes`, `/admin/tokens`, `/admin/mcp`).
2. **Decomposition & Architecture Mapping**
   - Consolidate explorer reports into `PROJECT.md`.
   - Define exact interface contracts for `manage_panels.js` and `console_admin.js`.
3. **Execution — Milestone 1 (Lane P2-a)**
   - Extract `manage_panels.js`.
   - Update `app.js` and `index.html`.
   - Verification loop (Worker -> Reviewers -> Challengers -> Auditor).
4. **Execution — Milestone 2 (Lane P2-b)**
   - Implement unified `admin/` console screens in `console_shell.html` and `console_admin.js`.
   - Wire 8 sections: instance, accounts, idp, providers, limits, themes, tokens, mcp.
   - Verification loop (Worker -> Reviewers -> Challengers -> Auditor).
5. **Execution — Milestone 3 (E2E Integration & Verification)**
   - Run required test suite: `tests/test_account_routes.py`, `tests/test_admin_instance.py`, `tests/test_oidc_flow.py`, `tests/test_console_routing.py`, `tests/test_single_mode_no_username_leak.py`.
   - Ensure full repository tests remain green.
   - Write `.agent-work/admin-settings/P2.md`.
   - Final audit and handoff to Sentinel.
