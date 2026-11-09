# BRIEFING — 2026-11-05T05:25:00Z

## Mission
Investigate the codebase for Lane P2-b: unified admin console screens in console_shell.html and console_admin.js, backend endpoints, and test contracts.

## 🔒 My Identity
- Archetype: teamwork_explorer
- Roles: Codebase Investigator, Synthesizer
- Working directory: /home/fow/Projects/claire-bible/.agents/teamwork/explorer_p2b_1
- Original parent: 6b12e74a-9ca9-44e7-9e6e-5bc3492e2404
- Milestone: Phase P2 Lane P2-b Codebase Investigation

## 🔒 Key Constraints
- Read-only investigation — do NOT implement
- Do NOT edit or write source code files
- Use ASD-STE100 English for all tasks
- Do not hard-wrap code lines
- Write reports to /home/fow/Projects/claire-bible/.agents/teamwork/explorer_p2b_1

## Current Parent
- Conversation ID: 6b12e74a-9ca9-44e7-9e6e-5bc3492e2404
- Updated: 2026-11-05T05:05:00Z

## Investigation State
- **Explored paths**:
  - `src/claire/templates/console_shell.html`
  - `src/claire/api/accounts_routes/{console,instance,admin,idp,invites}.py`
  - `src/claire/templates/{admin_accounts.html,admin_idp.html,syndication_preferences.html}`
  - `src/claire/static/js/{instance_mode.js,server_tier.js,accounts.js}`
  - `tests/{test_account_routes.py,test_server_tier.py,test_oidc_flow.py,test_console_routing.py,test_single_mode_no_username_leak.py,test_route_surface_contract.py,test_username_release.py}`
- **Key findings**:
  - `console_shell.html` has header with logout form (`action="/logout"`), storage key meta tag, and topbar back links. It needs script tags for `manage_panels.js` and `console_admin.js`.
  - Screens 1–3 (`/admin/instance`, `/admin/accounts`, `/admin/idp`) must render inside `console_shell.html` instead of standalone HTML files.
  - Screens 4–8 (`/admin/providers`, `/admin/limits`, `/admin/themes`, `/admin/tokens`, `/admin/mcp`) must render in `console_shell.html` with `console_admin.js` controllers and `manage_panels.js`.
  - Reauth modal (`/api/auth/reauth`) must handle step-up authentication across instance mode switch, server tier, account actions, and username release.
  - Test `tests/test_admin_instance.py` does not exist yet; the implementer must create it so the verification gate passes without failure.
  - In `tests/test_account_routes.py`, line 162 (`assert "<script" not in page.text`) must be updated because `console_shell.html` contains scripts.
- **Unexplored areas**: None for Lane P2-b scope.

## Key Decisions Made
- Map all 8 screens and required form IDs, endpoints, and contracts.
- Document exact required test updates and the need to create `tests/test_admin_instance.py`.

## Artifact Index
- /home/fow/Projects/claire-bible/.agents/teamwork/explorer_p2b_1/BRIEFING.md — Persistent state index
- /home/fow/Projects/claire-bible/.agents/teamwork/explorer_p2b_1/progress.md — Heartbeat and progress log
- /home/fow/Projects/claire-bible/.agents/teamwork/explorer_p2b_1/handoff.md — 5-component handoff report
