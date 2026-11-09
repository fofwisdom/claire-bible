# Scope: Account, IdP, and Fedify Preparation

## Architecture
This project prepares Claire Bible for account, IdP, and Fedify MIT sidecar integration.
It establishes:
1. Canonical account profile public contract (`profile_public` toggle in store, API, and UI).
2. Fedify and IdP reserved usernames synchronization in username policy and test suites.
3. Strict RFC 8693 token exchange account binding integrity in OAuth handler.
4. Clean sealing of legacy syndication prototype elements.
5. Baseline test fixes, PROJECT.md hygiene, and clean CI execution.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | `AccountStore.update_profile_public` | Store method to update profile_public flag in database | M1 | R1 |
| 2 | `PATCH /api/me` profile_public | Support profile_public (0 or 1) in PATCH /api/me | M1 | R1 |
| 3 | `GET /api/me` profile_public | Include profile_public in me_payload dictionary | M1 | R1 |
| 4 | `/settings/account` toggle | Render switch control in network mode, hide in single mode | M1 | R1 |
| 5 | `console_account.js` toggle handler | Event listener to submit PATCH /api/me on toggle change | M1 | R1 |
| 6 | Fedify & IdP reserved words | Add reserved usernames to RESERVED_USERNAMES | M1 | R2 |
| 7 | Policy tests synchronization | Update SPEC_RESERVED in tests/test_username_policy.py | M1 | R2 |
| 8 | Token exchange fallback removal | Remove unauthenticated fallback in oauth.py token exchange | M2 | R3 |
| 9 | Token exchange binding enforcement | Require valid binding and account_id, return 400 invalid_grant | M2 | R3 |
| 10 | Token exchange test suite | Comprehensive tests in tests/test_oauth_token_exchange.py | M2 | R3 |
| 11 | Syndication prototype sealing | Seal reference prototype, verify 5 syndication test suites | M2 | R5 |
| 12 | Baseline regex fix | Fix regex in tests/test_security_hotfixes.py for adoc_parser.js | M3 | R6 |
| 13 | PROJECT.md status update | Mark Phase P2 complete, document current milestones | M3 | R4 |
| 14 | Full CI verification gate | Execute bash ./scripts/ci.sh cleanly | M3 | R6 |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Account & Username Surface Contracts | R1 & R2 | Survey | DONE |
| M2 | Token Exchange & Syndication Sealing | R3 & R5 | Survey | DONE |
| M3 | Hygiene, Baseline Fix & CI Gate | R4 & R6 | M1, M2 | DONE |

## Code Layout
- Exclusive File Boundaries:
  - Milestone M1 exclusive files:
    - `src/claire/store/accounts.py`
    - `src/claire/api/accounts_routes/me.py`
    - `src/claire/api/accounts_routes/console.py`
    - `src/claire/static/js/console_account.js`
    - `src/claire/accounts/username_policy.py`
    - `tests/test_account_routes.py`
    - `tests/test_username_policy.py`
    - `tests/test_route_surface_contract.py`
  - Milestone M2 exclusive files:
    - `src/claire/api/oauth.py`
    - `tests/test_oauth_token_exchange.py`
    - `tests/test_oauth_flow.py`
    - `tests/test_syndication_route.py`
    - `e2e/test_syndication_e2e.py`
    - `tests/test_e2e_syndication_namespace.py`
  - Milestone M3 exclusive files:
    - `PROJECT.md`
    - `tests/test_security_hotfixes.py`
    - `.agent-work/admin-settings/P2.md`
