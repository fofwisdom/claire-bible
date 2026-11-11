# Project: Account, IdP, and Fedify Preparation Sealing and Cleanup

## Architecture
This project prepares Claire Bible for account, IdP, and Fedify MIT sidecar integration.
It seals canonical contracts, fixes baseline tests, updates project status, and enforces clean CI execution.

The architecture establishes:
1. **Canonical Account Profile Public Contract (R1)**:
   - Exposes `profile_public` (0 or 1) in `AccountStore`, `GET /api/me`, and `PATCH /api/me`.
   - Renders the profile public switch toggle in `/settings/account` in network mode.
   - Enforces single-mode privacy (rule S9) by omitting the toggle when `mode == "single"`.
2. **Reserved Usernames Synchronization (R2)**:
   - Adds 21 Fedify federation and IdP reserved words to `RESERVED_USERNAMES` in `src/claire/accounts/username_policy.py`.
   - Synchronizes test specifications in `tests/test_username_policy.py` and `tests/test_route_surface_contract.py`.
3. **Token Exchange Account Binding Integrity (R3)**:
   - Enforces strict account binding for RFC 8693 Token Exchange (`urn:ietf:params:oauth:grant-type:token-exchange`) in `src/claire/api/oauth.py`.
   - Verifies subject token session binding and validates active account status.
   - Rejects unauthenticated or unbound tokens with HTTP 400 `invalid_grant`.
   - Blocks unauthorized fallbacks to admin account ID 1.
4. **Project Status and Hygiene (R4)**:
   - Resolves all remaining items from Admin-Settings Phase P2.
   - Marks Phase P2 milestones as completed.
   - Updates `PROJECT.md` to reflect current project architecture, feature inventory, interface contracts, and code layout.
5. **Syndication Prototype Sealing and Cleanup (R5)**:
   - Seals `/prototype/syndication` as an authenticated reference prototype under system permission.
   - Isolates the live `/syndication` console from prototype seed data and mock factories.
   - Retains full compatibility across all syndication test suites.
6. **Baseline Test Fix and CI Gate (R6)**:
   - Corrects the escape helper regex in `tests/test_security_hotfixes.py` for `adoc_parser.js`.
   - Verifies clean execution of `./scripts/ci.sh` across all test targets.

**Status**: Completed on 2026-11-09 (commit `7bc536f`). All requirements R1 through R6 are complete.

---

## Completed Project: Per-Account Features

The Telegram bot, support bundle, data bundle, and backup are per-account features. Each account owner configures and controls them in the WebUI (`/settings/telegram`, `/settings/bundles`, `/settings/backups`). Only the operator restores an account backup (`/admin/backups`). Design and owner decisions PA1 through PA4: [`docs/design/PER_ACCOUNT_FEATURES_DESIGN.md`](docs/design/PER_ACCOUNT_FEATURES_DESIGN.md).

**Status**: Completed on 2026-11-10.

---

## Next Project: Fedify Sidecar Federation

The approved design is [`docs/design/ACCOUNT_IDP_AND_FEDIFY_SIDECAR_INTEGRATION_DESIGN.md`](docs/design/ACCOUNT_IDP_AND_FEDIFY_SIDECAR_INTEGRATION_DESIGN.md). The owner locked decisions FD1 through FD12 on 2026-11-10. Phases FP1 (internal contract and discovery), FP2 (followers), and FP3 (publishing) are complete. Phase FP4 (remote reading) needs a design revision and has no work units. The implementation reference is [`docs/implementation/FEDERATION.md`](docs/implementation/FEDERATION.md); its Sections 12 and 13 list the known limits and the differences from the design.

The design divides each phase into work units (Section 14.4 of the design). The owner starts each unit with an instruction that names the unit ID, for example "Do FP1-3". The agent does one unit, updates this table, and stops.

| Unit | Status | Commit |
|------|--------|--------|
| FP0 | COMPLETED (2026-11-10) | `116d530` |
| FP1-1 | COMPLETED (2026-11-10) | `56d40c9` |
| FP1-2 | COMPLETED (2026-11-10) | `ef42ef2` |
| FP1-3 | COMPLETED (2026-11-11) | `2a0819e` |
| FP1-4 | COMPLETED (2026-11-11) | `b65b1e3` |
| FP1-5 | COMPLETED (2026-11-11) | `34d46b96` |
| FP1-6 | COMPLETED (2026-11-11) | `75d79e18` |
| FP1-7 | COMPLETED (2026-11-11) | `420c8fc7` |
| FP2-1 | COMPLETED (2026-11-11) | `73c85a16` |
| FP2-2 | COMPLETED (2026-11-11) | `f967d36b` |
| FP2-3 | COMPLETED (2026-11-11) | `db9a524c` |
| FP2-4 | COMPLETED (2026-11-11) | `1774f8d1` |
| FP2-5 | COMPLETED (2026-11-11) | `c3cd5d1b` |
| FP3-1 | COMPLETED (2026-11-11) | `abaf0988` |
| FP3-2 | COMPLETED (2026-11-11) | `9d91813f` |
| FP3-3 | COMPLETED (2026-11-12) | `085b48d5` |
| FP3-4 | COMPLETED (2026-11-12) | `46f46630` |
| FP3-5 | COMPLETED (2026-11-12) | `90000a00` |

Difference from the design: `actor_keys` is `auth.db` version 5, not version 4. The per-account features project used version 4 after the design was written.

Difference from the design: `claire.db` version 17 has the three tables of phase FP2 only (unit FP2-1). The tables `federation_objects` and `federation_outbox` are in version 18 (unit FP3-1), together with the code that uses them.

Difference from the design: the end-to-end test of phase FP2 (unit FP2-5) has the remote server in the test process, not in a second development instance. Claire cannot send a `Follow`, and Fedify refuses the loopback address of a second instance (FS1).

Open item after phase FP3: follower removal by the owner (unit FP2-4) sends no `Reject(Follow)`. The outbox has no task kind for it.

Difference from the design: the internal API contract version is 1.2 (unit FP3-4 added the answer 410 for a purged actor).

Effects on the contracts in this file:
- **Contract 1 (R1)**: Decision FD11 adds one condition (unit FP1-4, implemented). When the instance setting `federation` is `on`, a `profile_public` change from 0 to 1 through `PATCH /api/me` requires recent authentication: a session without it gets 401 `reauth_required`, and a bearer credential gets 403 `step_up_requires_session`. The change from 1 to 0 has no condition. When `federation` is `off`, the contract below is unchanged.
- **Contract 2 (R2)**: No change. The reserved words protect the federation endpoint names.
- **Contract 3 (R3)**: No change. The sidecar does not use token exchange.
- **Contract 4 (R5)**: No change.

---

## Completed Phase P2: Admin and Settings Path Separation
Phase P2 established shared client-side management controllers, integrated role-aware management controls into the root service drawer, and consolidated all 8 administrator sections into `console_shell.html`.
- **Status**: Completed on 2026-11-05.
- **Verification Gate**: Passed with zero test failures (`.agent-work/admin-settings/P2.md`).

---

## Feature Inventory

| # | Feature | Description | Milestone | Requirement | Status |
|---|---------|-------------|-----------|-------------|--------|
| 1 | `AccountStore.update_profile_public` | Update database column `profile_public` and commit transaction | M1 | R1 | COMPLETED |
| 2 | `GET /api/me` profile_public field | Include `profile_public` (0 or 1) in `me_payload` response | M1 | R1 | COMPLETED |
| 3 | `PATCH /api/me` profile_public toggle | Validate and persist `profile_public` in account update handler | M1 | R1 | COMPLETED |
| 4 | `/settings/account` switch UI | Render accessible switch toggle in network mode; omit in single mode (S9) | M1 | R1 | COMPLETED |
| 5 | `console_account.js` toggle binding | Listen to toggle changes and dispatch `PATCH /api/me` | M1 | R1 | COMPLETED |
| 6 | Fedify and IdP reserved words | Add federation and auth words to `RESERVED_USERNAMES` policy | M1 | R2 | COMPLETED |
| 7 | Policy tests synchronization | Align `SPEC_RESERVED` and test assertions with updated policy | M1 | R2 | COMPLETED |
| 8 | Token exchange fallback removal | Remove unauthenticated fallback logic in RFC 8693 token exchange handler | M2 | R3 | COMPLETED |
| 9 | Token exchange binding verification | Require bound session account and valid status; reject invalid tokens | M2 | R3 | COMPLETED |
| 10 | Token exchange dedicated tests | Provide comprehensive test coverage in `tests/test_oauth_token_exchange.py` | M2 | R3 | COMPLETED |
| 11 | Syndication prototype sealing | Seal reference prototype under system permission and isolate live console | M2 | R5 | COMPLETED |
| 12 | Baseline escape helper regex fix | Update regex in `tests/test_security_hotfixes.py` to match `adoc_parser.js` | M3 | R6 | COMPLETED |
| 13 | `PROJECT.md` status update and hygiene | Update milestone records, interface contracts, and layout boundaries | M3 | R4 | COMPLETED |
| 14 | Full CI verification suite gate | Execute `bash ./scripts/ci.sh` with all syntax, docker, lock, and test checks | M3 | R6 | COMPLETED |

---

## Milestones

### Historical Milestones: Admin and Settings Path Separation (Phase P2)
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| P2-M1 | Lane P2-a: Shared Controllers & Drawer | Extract `manage_panels.js`, update `app.js` and `index.html` | none | COMPLETED |
| P2-M2 | Lane P2-b: Unified Admin Console Screens | Implement 8 `/admin/*` screens in `console_shell.html` + `console_admin.js` | P2-M1 | COMPLETED |
| P2-M3 | Final Verification & Documentation | Execute pytest verification gate, full test suites, and write `P2.md` | P2-M1, P2-M2 | COMPLETED |

### Current Milestones: Account, IdP, and Fedify Preparation Sealing and Cleanup
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Account & Username Surface Contracts | Implement R1 (`profile_public` toggle) and R2 (Reserved usernames sync) | Phase P2 | COMPLETED |
| M2 | Token Exchange & Syndication Sealing | Implement R3 (Token exchange binding integrity) and R5 (Syndication prototype sealing) | M1 | COMPLETED |
| M3 | Hygiene, Baseline Fix & CI Gate | Implement R4 (`PROJECT.md` hygiene) and R6 (Baseline test fix and CI gate) | M1, M2 | COMPLETED |

---

## Interface Contracts

### 1. Canonical Account Profile Contract (R1)
- **Store Interface**:
  - `AccountStore.update_profile_public(account_id: int, profile_public: int | bool, *, now: float | None = None) -> Account`
  - Updates `profile_public` column (value 0 or 1) and commits transaction. Raises `AccountNotFound` if row does not exist.
- **REST API Endpoints**:
  - `GET /api/me`: Returns JSON object with `"profile_public": 0` or `"profile_public": 1`.
  - `PATCH /api/me`: Accepts JSON body `{"profile_public": 0}` or `{"profile_public": 1}`. Also accepts `true` and `false`. Refuses other values with HTTP 400 `invalid_profile_public`. Returns updated `me_payload`.
- **Console UI Contract**:
  - Element: `<input id="pref-profile-public" type="checkbox" switch data-settings-toggle="profile-public">` rendered in `/settings/account`.
  - Behavior: Dispatches `PATCH /api/me` with `{ profile_public: 1 }` or `{ profile_public: 0 }` on toggle change.
  - Privacy Constraint (S9): In single mode (`mode == "single"`), the toggle element is omitted completely.

### 2. Username Policy Contract (R2)
- **Module Interface**:
  - `src/claire/accounts/username_policy.py`: `RESERVED_USERNAMES` includes Fedify and IdP terms:
    `{"inbox", "outbox", "actor", "actors", "ap", "fedify", "federation", "nodeinfo", "oauth", "auth", "followers", "following", "shared_inbox", ...}`.
  - Function `validate_username_candidate(raw: str) -> None` rejects reserved terms with `InvalidUsernameError`.
  - Function `is_reserved_username(raw: str) -> bool` returns true for all reserved terms.

### 3. RFC 8693 Token Exchange Contract (R3)
- **OAuth Endpoint**:
  - `POST /oauth/token` with `grant_type=urn:ietf:params:oauth:grant-type:token-exchange`
  - Required Parameter: `subject_token`. The handler does not use the value of `subject_token_type`.
  - Validation: Resolves session binding via `validate_session_binding(conn, subject_token)`.
  - Status Check: Verifies account exists and `status in CREDENTIAL_ACCEPTING_STATUSES`.
  - Failure Responses: Returns HTTP 400 `{"error": "invalid_grant", "error_description": "..."}` for missing, expired, unbound, or disabled accounts.
  - Issued Token: A `readonly` OAuth access token and a refresh token for the same account.
  - Integrity Guarantee: Binds token directly to resolved account ID via `UPDATE oauth_tokens SET account_id=?`. Never falls back to admin account ID 1.

### 4. Syndication Prototype Contract (R5)
- **Reference Prototype**:
  - `GET /prototype/syndication`: Sealed as an authenticated reference prototype under system read permission (`_system("read")`).
- **Production Console**:
  - `GET /settings/connections` and `GET /settings/feeds`: Cleanly separated from prototype seed data and mock factories.

### 5. Shared Admin and Settings Console Contracts (Phase P2 Reference)
- `window.ClaireManagePanels = { renderProviderManager, renderLimitManager, renderThemeManager, renderTokenManager };`
- Console Bootstrap: `<script id="console-bootstrap" type="application/json">{ "section": "<section_name>", "role": "admin"|"owner", "mode": "single"|"network", ... }</script>`
- Header Policy: Console routes return `Referrer-Policy: same-origin` and `Cache-Control: no-store`.

---

## Code Layout
- **Exclusive File Boundaries**:
  - **Milestone M1**:
    - `src/claire/store/accounts.py`
    - `src/claire/api/accounts_routes/me.py`
    - `src/claire/api/accounts_routes/console.py`
    - `src/claire/static/js/console_account.js`
    - `src/claire/accounts/username_policy.py`
    - `tests/test_account_routes.py`
    - `tests/test_username_policy.py`
    - `tests/test_route_surface_contract.py`
  - **Milestone M2**:
    - `src/claire/api/oauth.py`
    - `tests/test_oauth_token_exchange.py`
    - `tests/test_oauth_flow.py`
    - `tests/test_syndication_route.py`
    - `e2e/test_syndication_e2e.py`
    - `tests/test_e2e_syndication_namespace.py`
  - **Milestone M3**:
    - `PROJECT.md`
    - `tests/test_security_hotfixes.py`
    - `.agent-work/admin-settings/P2.md`
