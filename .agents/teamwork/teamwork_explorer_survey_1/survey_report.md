# Survey Report: Requirements R1 and R2

Date: 2026-11-09
Author: Survey Explorer 1

## Summary

This report documents the investigation of Requirement R1 and Requirement R2.
Requirement R1 defines the `profile_public` toggle and the account contract seal.
Requirement R2 defines the reserved usernames synchronization for Fedify and IdP.
No blockers exist. All required changes are isolated and well defined.

---

## 1. Requirement R1: `profile_public` Toggle and Account Contract Seal

### 1.1 Backend Implementation Points

#### 1.1.1 API Endpoint Definition
- File path: `src/claire/api/accounts_routes/me.py`
- Route definition:
  - Line 37: `ME_API_PATH = "/api/me"`
  - Line 193: `Route(ME_API_PATH, me_patch, methods=["PATCH"])`
- Handler function:
  - Lines 136-149: `async def me_patch(request: Request) -> Response`
- Current behavior:
  - Line 139 rejects all keys other than `display_name`:
    `if set(body) != {"display_name"}: return json_error(400, "invalid_request", fields=["display_name"])`
  - The endpoint does not accept `profile_public`.

#### 1.1.2 Canonical Response Payload
- File path: `src/claire/api/accounts_routes/me.py`
- Function: `me_payload(ctx: AccountsContext, principal: Principal) -> dict[str, Any]` (lines 42-61)
- Current return dictionary:
  - `public_id`, `username`, `display_name`, `role`, `scope`, `status`, `has_password`.
- Required modification:
  - Add `"profile_public": 1 if account.profile_public else 0` (or boolean `account.profile_public`).
  - This seals the canonical account contract.

#### 1.1.3 Account Model and Database Schema
- File path: `src/claire/store/db.py`
  - Line 1054: `profile_public INTEGER NOT NULL DEFAULT 0,`
  - The column already exists in the `accounts` table.
  - Default value in the database: `0`.
  - Database type: `INTEGER NOT NULL DEFAULT 0` (0 = private, 1 = public).
- File path: `src/claire/store/accounts.py`
  - Lines 79-91: `Account` dataclass contains `profile_public: bool` (line 86).
  - Line 117: `_row_to_account` converts column to boolean: `profile_public=bool(profile_public)`.
  - Lines 636, 649: `create_account` accepts `profile_public: bool = False` and writes `1 if profile_public else 0`.
- Missing database operation:
  - `src/claire/store/accounts.py` has `update_display_name` (line 366).
  - `AccountStore` does not have a method to update `profile_public`.
  - An update method must be added (e.g. `update_profile_public` or generic `update_profile`).

#### 1.1.4 Validation Rules
- Supported values: `0` or `1`.
- If input is not integer `0` or `1` (or boolean `False` or `True`): return HTTP 400 `invalid_request` or `invalid_profile_public`.
- Allowed keys in `PATCH /api/me`: `{"display_name", "profile_public"}`.
- Payload can contain `display_name`, `profile_public`, or both.
- Unexpected keys must cause HTTP 400 `invalid_request`.

#### 1.1.5 OpenAPI Specification
- File paths:
  - `docs/implementation/openapi.yaml`
  - `src/claire/static/docs/openapi.yaml`
- `AccountSelf` schema (line 2068):
  - Must add `profile_public` property (`type: integer`, `enum: [0, 1]` or `type: boolean`).
- `PATCH /api/me` schema (line 1641):
  - Must add optional `profile_public` property to request body.

---

### 1.2 Frontend Implementation Points

#### 1.2.1 Route and Template Rendering
- File path: `src/claire/api/accounts_routes/console.py`
  - Line 74: Section slug `account` mapped to `/settings/account`.
  - Lines 677-715: `_build_settings_account_section` dispatches to `_settings_account_body`.
  - Lines 816-962: `_settings_account_body` builds the HTML body.
  - Line 1213: Rendered with template `templates/console_shell.html`.
- Current display:
  - Lines 846-848: Displays `profile_public` as text in `<dl>`:
    `<dd data-account-field="profile_public">공개 (공개 디렉터리에 표시)</dd>` or `비공개`.
  - Lines 849-853: Displays obsolete note:
    `<p class="hint" data-profile-public-hint>프로필 공개 범위를 바꾸는 API가 아직 없어 이 화면에서는 현재 값만 보여 줍니다.</p>`
- Single mode rule (S9):
  - Line 834: In single mode (`mode == "single"`), `USERNAME` and `profile_public` controls are omitted.
  - This rule must be preserved.

#### 1.2.2 Control Placement and Submission
- Control design:
  - In network mode (`mode == "network"`), remove `data-profile-public-hint`.
  - Add switch control:
    `<input id="pref-profile-public" type="checkbox" switch data-settings-toggle="profile-public" aria-label="프로필 공개 여부"/>`
  - Use checked state: `checked` when `account.profile_public` is true.
- Client-side script:
  - File path: `src/claire/static/js/console_account.js`
  - Add change event listener for `[data-settings-toggle="profile-public"]`.
  - On toggle change:
    - Send `PATCH /api/me` with payload `{"profile_public": input.checked ? 1 : 0}`.
    - On success: update `<dd data-account-field="profile_public">` text and show confirmation.
    - On failure: revert `input.checked` and show error message.

---

## 2. Requirement R2: Reserved Usernames Synchronization

### 2.1 Backend Policy

- File path: `src/claire/accounts/username_policy.py`
- Current state:
  - `RESERVED_USERNAMES` (lines 17-32) contains 42 names in 4 categories:
    - 운영·권한 (admin, root, sysadmin, system, etc.)
    - 지원·연락 (support, help, info, security, abuse, etc.)
    - 일반 (user, guest, anonymous, public, private, me, etc.)
    - 서비스 (claire, api, www, mcp, explore, invite, settings, syndication, prototype, etc.)
- Missing Fedify and IdP words:
  - Primary list from request:
    - `inbox`
    - `outbox`
    - `actor`
    - `actors`
    - `ap`
    - `fedify`
    - `federation`
    - `nodeinfo`
    - `oauth`
    - `auth`
    - `followers`
    - `following`
    - `shared_inbox`
  - Related words recommended for inclusion:
    - `sharedinbox` (variant without underscore)
    - `webfinger` (ActivityPub discovery endpoint)
    - `idp` (Identity Provider route `/admin/idp`)
    - `oidc` (OpenID Connect route `/auth/oidc`)
    - `openid` (OpenID protocol)
    - `activitypub` (ActivityPub protocol)
    - `wellknown` (variant of `.well-known`)
    - `well_known` (variant of `.well-known`)

### 2.2 Short Word Behavior (`ap`)

- USERNAME regex is `^[a-z][a-z0-9_]{2,29}$` (line 14).
- Minimum username length is 3 characters.
- `ap` has 2 characters.
- When `validate_username("ap")` runs:
  - Syntax check fails before reserved check.
  - Reason returned is `"syntax"`.
- Why `ap` must be in `RESERVED_USERNAMES`:
  - `username_fragments` splits usernames by `_` and numbers (line 57).
  - Line 91 checks: `if fragment in RESERVED_USERNAMES ... raise UsernameRejected("reserved")`.
  - Having `ap` in `RESERVED_USERNAMES` blocks usernames like `ap_service` and `my_ap`.
  - This behaves exactly like existing reserved word `me` (length 2).

### 2.3 Test Suite Synchronization

#### 2.3.1 `tests/test_username_policy.py`
- Line 20: `SPEC_RESERVED` set must be updated with the exact same new reserved words.
- Line 143: `test_reserved_set_matches_spec_exactly` asserts `RESERVED_USERNAMES == frozenset(SPEC_RESERVED)`.
- Line 149: `test_every_reserved_name_is_rejected` handles length < 3 automatically:
  `expected = "syntax" if len(name) < 3 else "reserved"`
  `ap` passes this test correctly.

#### 2.3.2 `tests/test_route_surface_contract.py`
- Verifies system routes in `security.ROUTE_POLICY`.
- System routes include `/oauth/*` and `/auth/*`.
- Reserving `oauth` and `auth` prevents collision with these system routes.
- The test does not directly read `RESERVED_USERNAMES`.

#### 2.3.3 Collision Analysis
- Search across all tests in `tests/` and `e2e/`:
  - No test creates an account using any of the proposed reserved words.
  - No route conflicts with any of the proposed reserved words.

---

## 3. Impact and Affected Files Table

| Component | Target File | Line(s) | Action |
|-----------|-------------|---------|--------|
| Account API | `src/claire/api/accounts_routes/me.py` | 42-61, 136-149 | Allow `profile_public` in PATCH; include in `me_payload` |
| Account Store | `src/claire/store/accounts.py` | 366-376 | Add `update_profile_public` method |
| Account Console | `src/claire/api/accounts_routes/console.py` | 846-854 | Render toggle control in network mode; remove hint |
| Console Script | `src/claire/static/js/console_account.js` | 271-281, 407 | Add change handler for `profile_public` toggle |
| Username Policy | `src/claire/accounts/username_policy.py` | 17-32 | Add Fedify / IdP reserved words to `RESERVED_USERNAMES` |
| Username Tests | `tests/test_username_policy.py` | 20-33 | Synchronize `SPEC_RESERVED` |
| Account Route Tests | `tests/test_account_routes.py` | 183-195 | Update `GET /api/me` expectation; add `PATCH` tests for `profile_public` |
| OpenAPI Spec | `docs/implementation/openapi.yaml`, `src/claire/static/docs/openapi.yaml` | 1641-1675, 2068-2092 | Add `profile_public` to PATCH and `AccountSelf` |
