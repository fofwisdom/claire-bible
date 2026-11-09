# Handoff Report — Survey Explorer 1

Date: 2026-11-09
Working Directory: `/home/fow/Projects/claire-bible/.agents/teamwork/teamwork_explorer_survey_1`

## 1. Observation

### 1.1 Requirement R1 (`profile_public` Toggle and Account Contract Seal)
1. **`src/claire/api/accounts_routes/me.py`**:
   - Lines 36-39: `ME_PATH = "/me"`, `ME_API_PATH = "/api/me"`, `SETTINGS_ACCOUNT_PATH = "/settings/account"`.
   - Lines 42-61: `me_payload` returns a dictionary with keys: `public_id`, `username`, `display_name`, `role`, `scope`, `status`, `has_password`. The key `profile_public` is absent.
   - Lines 136-149: `me_patch` handles `PATCH /api/me`. Line 139 states:
     ```python
     if set(body) != {"display_name"}:
         return json_error(400, "invalid_request", fields=["display_name"])
     ```
     `profile_public` is currently rejected.
   - Line 193: Route registered as `Route(ME_API_PATH, me_patch, methods=["PATCH"])`.
2. **`src/claire/store/db.py`**:
   - Line 1054: Column defined as `profile_public INTEGER NOT NULL DEFAULT 0,` in `CREATE TABLE IF NOT EXISTS accounts`.
3. **`src/claire/store/accounts.py`**:
   - Line 86: `Account` dataclass defines `profile_public: bool`.
   - Line 117: `_row_to_account` maps row: `profile_public=bool(profile_public)`.
   - Lines 366-376: Store defines `update_display_name`. No method exists to update `profile_public`.
   - Line 636, 649: `create_account` accepts `profile_public: bool = False` and inserts `1 if profile_public else 0`.
4. **`src/claire/api/accounts_routes/console.py`**:
   - Lines 846-848: Displays `profile_public` as read-only text in `info_rows`:
     ```python
     if mode == "network":
         info_rows.append(("profile_public", "프로필 공개", "공개 (공개 디렉터리에 표시)" if account.profile_public else "비공개"))
     ```
   - Lines 849-853: Contains obsolete static note:
     ```python
     profile_hint = (
         '<p class="hint" data-profile-public-hint>프로필 공개 범위를 바꾸는 API가 아직 없어 이 화면에서는 현재 값만 보여 줍니다.</p>'
         if mode == "network"
         else ""
     )
     ```
   - Line 834: Single mode check (rule S9) hides USERNAME and `profile_public` when `mode == "single"`.
5. **`src/claire/static/js/console_account.js`**:
   - Lines 271-281: `handlers["display-name"]` submits `{ display_name: value }` to `PATCH /api/me`.
   - No handler or change listener exists for `profile_public`.
6. **`tests/test_account_routes.py`**:
   - Line 183: Tests exact match of `me` dictionary from `GET /api/me`:
     ```python
     assert me == {
         "public_id": me["public_id"],
         "username": USERNAME,
         "display_name": "관리자",
         "role": "admin",
         "scope": "owner",
         "status": "active",
         "has_password": True,
     }
     ```
   - Lines 194-213: Tests `PATCH /api/me` with `display_name` and asserts 400 for unexpected keys.

### 1.2 Requirement R2 (Reserved Usernames Synchronization)
1. **`src/claire/accounts/username_policy.py`**:
   - Line 14: `USERNAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,29}$")`. Minimum length is 3 characters.
   - Lines 17-32: `RESERVED_USERNAMES` contains 42 names. Missing words: `inbox`, `outbox`, `actor`, `actors`, `ap`, `fedify`, `federation`, `nodeinfo`, `oauth`, `auth`, `followers`, `following`, `shared_inbox`, `sharedinbox`, `webfinger`, `idp`, `oidc`, `openid`, `activitypub`, `wellknown`, `well_known`.
   - Line 57: `username_fragments` splits by `_` and digits: `_FRAGMENT_SPLIT_RE = re.compile(r"[_0-9]+")`.
   - Line 91: Rejects any username containing a fragment present in `RESERVED_USERNAMES`.
2. **`tests/test_username_policy.py`**:
   - Lines 20-33: `SPEC_RESERVED` defines expected set.
   - Line 143: `assert RESERVED_USERNAMES == frozenset(SPEC_RESERVED)`.
   - Line 149: `expected = "syntax" if len(name) < 3 else "reserved"`. Names with length < 3 (`me`, `ap`) are caught by syntax check first.
3. **`tests/test_route_surface_contract.py`**:
   - Lines 39-41: System routes include `/oauth/register`, `/oauth/authorize`, `/oauth/token`, `/auth/oidc/{id}/start`, `/auth/oidc/{id}/callback`.
   - Grep search for `username="inbox"`, `username="actor"`, `username="oauth"`, `username="auth"`, `username="idp"` across `tests/` yielded zero results. No test conflicts exist.

---

## 2. Logic Chain

1. **Premise 1 (R1 Backend)**:
   Observation 1.1.1 shows `me_patch` restricts body to `display_name`.
   Observation 1.1.2 shows `me_payload` omits `profile_public`.
   Observation 1.1.3 shows database column `profile_public` exists in `accounts` table, but `AccountStore` lacks an update method.
   *Inference 1*: `AccountStore` must implement `update_profile_public` (or equivalent update method), and `me_patch` must accept `profile_public` (0 or 1), update the account, and return `me_payload` containing `profile_public`.

2. **Premise 2 (R1 Contract & Tests)**:
   Observation 1.1.6 shows `tests/test_account_routes.py` line 183 verifies exact dictionary keys of `me_payload`.
   *Inference 2*: When `profile_public` is added to `me_payload`, line 183 of `tests/test_account_routes.py` must be updated to expect `profile_public`, and new test cases must cover `PATCH /api/me` with `profile_public`.

3. **Premise 3 (R1 Frontend)**:
   Observation 1.1.4 shows `_settings_account_body` renders a static read-only text with an obsolete hint.
   Observation 1.1.5 shows `console_account.js` does not have a handler for `profile_public`.
   *Inference 3*: In network mode, `_settings_account_body` must remove the obsolete hint and render an interactive switch control (`<input type="checkbox" switch data-settings-toggle="profile-public">`). `console_account.js` must listen for toggle change events and send `PATCH /api/me` with `{"profile_public": input.checked ? 1 : 0}`. In single mode, rule S9 must remain active to hide the toggle control.

4. **Premise 4 (R2 Policy & Synchronization)**:
   Observation 1.2.1 shows `src/claire/accounts/username_policy.py` lacks Fedify and IdP reserved words.
   Observation 1.2.2 shows `tests/test_username_policy.py` checks exact equality between `RESERVED_USERNAMES` and `SPEC_RESERVED`.
   Observation 1.2.3 shows no existing tests create accounts with these reserved names.
   *Inference 4*: Adding the required words to `RESERVED_USERNAMES` in `username_policy.py` and simultaneously updating `SPEC_RESERVED` in `test_username_policy.py` will synchronize the policy without breaking existing route contracts or test suites.

---

## 3. Caveats

1. The test runner for this workspace requires `uv run pytest` instead of system `pytest`. System Python lacks virtualenv dependencies.
2. In `me_payload`, the canonical format for `profile_public` can be integer (`1` or `0`) or boolean (`True` or `False`). The task prompt specifies `profile_public toggle (0 or 1)`. In SQLite, the column type is integer 0 or 1. We recommend returning integer `1` or `0` in JSON, or accepting both in PATCH.
3. No other caveats.

---

## 4. Conclusion

1. Requirement R1 implementation plan:
   - In `src/claire/store/accounts.py`: add `update_profile_public(self, account_id: int, profile_public: int | bool, *, now: float | None = None) -> Account`.
   - In `src/claire/api/accounts_routes/me.py`:
     - Allow keys `{"display_name", "profile_public"}` in `me_patch`.
     - Validate `profile_public in (0, 1)`.
     - Add `"profile_public": 1 if account.profile_public else 0` to `me_payload`.
   - In `src/claire/api/accounts_routes/console.py`:
     - In network mode, remove `data-profile-public-hint` and insert switch `<input type="checkbox" switch data-settings-toggle="profile-public">`.
     - Preserve rule S9 in single mode.
   - In `src/claire/static/js/console_account.js`:
     - Add change listener on `[data-settings-toggle="profile-public"]` to invoke `PATCH /api/me`.
   - In `docs/implementation/openapi.yaml` and `src/claire/static/docs/openapi.yaml`:
     - Update `AccountSelf` schema and `PATCH /api/me` request body.
2. Requirement R2 implementation plan:
   - In `src/claire/accounts/username_policy.py`:
     - Add Fedify / IdP words (`inbox`, `outbox`, `actor`, `actors`, `ap`, `fedify`, `federation`, `nodeinfo`, `oauth`, `auth`, `followers`, `following`, `shared_inbox`, plus recommended `sharedinbox`, `webfinger`, `idp`, `oidc`, `openid`, `activitypub`, `wellknown`, `well_known`) to `RESERVED_USERNAMES`.
   - In `tests/test_username_policy.py`:
     - Add identical words to `SPEC_RESERVED`.

---

## 5. Verification Method

To independently verify all findings and test behavior:
1. Run username policy tests:
   ```bash
   uv run pytest tests/test_username_policy.py
   ```
2. Run route surface contract tests:
   ```bash
   uv run pytest tests/test_route_surface_contract.py
   ```
3. Run account routes tests:
   ```bash
   uv run pytest tests/test_account_routes.py
   ```
4. Verify file locations:
   - Inspect `src/claire/api/accounts_routes/me.py` lines 136-149.
   - Inspect `src/claire/store/db.py` line 1054.
   - Inspect `src/claire/accounts/username_policy.py` lines 17-32.
   - Inspect `src/claire/api/accounts_routes/console.py` lines 846-854.
