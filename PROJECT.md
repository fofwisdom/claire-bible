# Project: Admin and Settings Path Separation — Phase P2

## Architecture
Phase P2 establishes shared client-side management controllers, integrates role-aware management controls into the root service drawer (D2), and consolidates all 8 administrator sections into a unified `console_shell.html` host (Lane P2-b).

- **Client Modules**:
  - `src/claire/static/js/manage_panels.js`: Reusable DOM and API controllers for providers, limits, themes, and tokens.
  - `src/claire/static/js/app.js`: Root application client script. Tracks `IS_ADMIN` and controls `#providermanagebtn`, `#limitmanagebtn`, and `#consolelinkbtn`. (Hotspot file: owned exclusively by Milestone 1 / Lane P2-a).
  - `src/claire/static/js/console_admin.js`: Client controller for the 8 `/admin/*` console sections, step-up reauthentication modal, and live telemetry.
- **Templates**:
  - `src/claire/templates/index.html`: Root workspace template with `#manage-section` drawer actions and script inclusion for `manage_panels.js`.
  - `src/claire/templates/console_shell.html`: Shared console shell hosting all `/admin/*` sections, topbar back links, logout form, reauth modal, and script inclusions.
- **Backend Route Handlers**:
  - `src/claire/api/accounts_routes/console.py`: Core console section dispatcher, metadata registry, and `GET /api/admin/mcp/telemetry`.
  - `src/claire/api/accounts_routes/instance.py`: Mode switch, G1–G9 checklist, legacy host sunset table, and server tier form rendered via `console_shell.html`.
  - `src/claire/api/accounts_routes/admin.py`: Member directory table, lifecycle actions, invites, and retired username management rendered via `console_shell.html`.
  - `src/claire/api/accounts_routes/idp.py`: External IdP registry, discovery check, policy controls, and login simulator rendered via `console_shell.html`.

---

## Feature Inventory
Every feature identified during Phase 0 Survey is cataloged below with its assigned milestone:

| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | `manage_panels.js` extraction | Shared controller helpers for providers, limits, themes, tokens | M1 (Lane P2-a) | Survey §1.1 |
| 2 | `renderProviderManager(container, opts)` | Provider selector, credentials, test connection, save settings | M1 (Lane P2-a) | Survey §1.1 |
| 3 | `renderLimitManager(container, opts)` | Ingest limits, PDF max chars, KG budgets, slicing strategies | M1 (Lane P2-a) | Survey §1.1 |
| 4 | `renderThemeManager(container, opts)` | Knowledge theme list, badges, options, edit, create, delete | M1 (Lane P2-a) | Survey §1.1 |
| 5 | `renderTokenManager(container, opts)` | API/MCP token list, token issue with copy box, revocation | M1 (Lane P2-a) | Survey §1.1 |
| 6 | `IS_ADMIN` client state tracking | Track admin role from `GET /whoami` (`is_admin === true`) in `app.js` | M1 (Lane P2-a) | Survey §1.2 |
| 7 | Role-aware drawer buttons | Show `#providermanagebtn` and `#limitmanagebtn` only for `canWrite() && IS_ADMIN` | M1 (Lane P2-a) | Survey §1.2 |
| 8 | `#consolelinkbtn` root entry | Add console button in `#manage-section`: `/admin` for admin, `/settings` for owner | M1 (Lane P2-a) | Survey §1.2 |
| 9 | `/admin/instance` unified screen | Render mode switch, G1–G9, sunset, server tier form in `console_shell.html` | M2 (Lane P2-b) | Survey §1.3 |
| 10 | `/admin/accounts` unified screen | Render member table, lifecycle actions, invites, retired usernames, userdb view | M2 (Lane P2-b) | Survey §1.3 |
| 11 | `/admin/idp` unified screen | Render IdP registry, discovery check, policies, login simulator in `console_shell.html` | M2 (Lane P2-b) | Survey §1.3 |
| 12 | `/admin/providers` full-page host | Host `renderProviderManager` in `console_shell.html` + `console_admin.js` | M2 (Lane P2-b) | Survey §1.3 |
| 13 | `/admin/limits` full-page host | Host `renderLimitManager` in `console_shell.html` + `console_admin.js` | M2 (Lane P2-b) | Survey §1.3 |
| 14 | `/admin/themes` full-page host | Host `renderThemeManager` + instance summary card (total_accounts, total_themes) | M2 (Lane P2-b) | Survey §1.3 |
| 15 | `/admin/tokens` full-page host | Host `renderTokenManager` + OAuth 2.1 authorized client list card | M2 (Lane P2-b) | Survey §1.3 |
| 16 | `/admin/mcp` unified screen | Live telemetry audit table, RBAC parity table, WebMCP policy, support bundle | M2 (Lane P2-b) | Survey §1.3 |
| 17 | `GET /api/admin/mcp/telemetry` | Query SQLite MCP telemetry records with limit and filter validation | M2 (Lane P2-b) | Survey §1.3 |
| 18 | Step-up reauth modal | Universal modal in `console_shell.html` calling `POST /api/auth/reauth` on 401 | M2 (Lane P2-b) | Survey §1.3 |
| 19 | Support bundle export trigger | Trigger diagnostic archive export via `POST /support/bundle` | M2 (Lane P2-b) | Survey §1.3 |
| 20 | Single mode no username leak (S9) | Omit `@username` in single mode across all console responses | M2 (Lane P2-b) | Survey §1.3 |
| 21 | Non-admin 404 enforcement (B1/N5) | Return 404 for non-admin callers on all 8 `/admin/*` routes | M2 (Lane P2-b) | Survey §1.3 |
| 22 | Content peeking isolation (D5/K5) | Aggregate count only for `/admin/themes`; zero member theme/token leaks | M2 (Lane P2-b) | Survey §1.3 |
| 23 | Create `tests/test_admin_instance.py` | Create dedicated test suite for `/admin/instance` console rendering | M2 (Lane P2-b) | Survey §1.4 |
| 24 | Update `tests/test_account_routes.py` | Align script tag assertion with `console_shell.html` bootstrap scripts | M2 (Lane P2-b) | Survey §1.4 |
| 25 | Verification Gate & Phase P2 Notes | Run full verification suite and write `.agent-work/admin-settings/P2.md` | M3 (E2E & Notes) | Survey §1.4 |

---

## Milestones

| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Lane P2-a: Shared Controllers & Drawer | Extract `manage_panels.js`, update `app.js` and `index.html` | none | IN_PROGRESS |
| M2 | Lane P2-b: Unified Admin Console Screens | Implement 8 `/admin/*` screens in `console_shell.html` + `console_admin.js` + route handlers | M1 | PLANNED |
| M3 | Final Verification & Documentation | Execute pytest verification gate, full test suites, and write `P2.md` | M1, M2 | PLANNED |

---

## Interface Contracts

### 1. `manage_panels.js` ↔ `app.js` & `console_admin.js`
- `window.ClaireManagePanels = { renderProviderManager, renderLimitManager, renderThemeManager, renderTokenManager };`
- Each function is also available directly on `window`:
  - `renderProviderManager(container, opts)`
    - `container`: HTMLElement.
    - `opts`: `{ rootUrl, apiUrl, canWrite, showClose, onClose }`.
  - `renderLimitManager(container, opts)`
    - `container`: HTMLElement.
    - `opts`: `{ rootUrl, apiUrl, canWrite, showClose, onClose }`.
  - `renderThemeManager(container, opts)`
    - `container`: HTMLElement.
    - `opts`: `{ rootUrl, apiUrl, isOwner, isCollaborator, isAdmin, activeThemeId, defaultSorcerer, showClose, onClose, onThemeChange }`.
  - `renderTokenManager(container, opts)`
    - `container`: HTMLElement.
    - `opts`: `{ rootUrl, apiUrl, canWrite, isAdmin, showClose, onClose, initialMsg, initialSuccess, initialNewToken, onTokenChange }`.

### 2. Backend Routes ↔ `console_shell.html` & `console_admin.js`
- Bootstrap payload: `<script id="console-bootstrap" type="application/json">{ "section": "<section_name>", "role": "admin", "mode": "single"|"network", ... }</script>`.
- Reauthentication Modal: `<dialog id="console-reauth-modal" class="console-modal">` or `<div id="console-reauth-modal" class="console-modal" hidden>` with form submitting to `POST /api/auth/reauth`.
- Header requirements: All `/admin/*` routes must return `Referrer-Policy: same-origin` and `Cache-Control: no-store`.

---

## Code Layout
- Exclusive File Boundaries:
  - Lane P2-a exclusive files:
    - `src/claire/static/js/app.js` (hotspot file)
    - `src/claire/static/js/manage_panels.js`
    - `src/claire/templates/index.html`
  - Lane P2-b exclusive files:
    - `src/claire/templates/console_shell.html`
    - `src/claire/static/js/console_admin.js`
    - `src/claire/api/accounts_routes/console.py`
    - `src/claire/api/accounts_routes/instance.py`
    - `src/claire/api/accounts_routes/admin.py`
    - `src/claire/api/accounts_routes/idp.py`
    - `tests/test_admin_instance.py`
    - `tests/test_account_routes.py`
