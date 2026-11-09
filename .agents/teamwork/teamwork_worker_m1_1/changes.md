# Changes Report — Milestone M1

Date: 2026-11-09
Worker: Worker M1
Target Requirements: R1 (profile_public toggle and contract seal), R2 (Reserved usernames synchronization)

## 1. Files Modified

1. `src/claire/store/accounts.py`
   - Added method `update_profile_public(self, account_id: int, profile_public: int | bool, *, now: float | None = None) -> Account`.
   - The method updates `profile_public` (0 or 1) and `updated_at`.
   - The method commits the change and returns the updated `Account`.
   - The method raises `AccountNotFound` if the account does not exist.

2. `src/claire/api/accounts_routes/me.py`
   - Added helper `update_profile_public(ctx: AccountsContext, account_id: int, profile_public: int | bool) -> None`.
   - Updated `me_payload` to include `"profile_public": 1 if account.profile_public else 0`.
   - Updated `me_patch` to accept keys in `{"display_name", "profile_public"}`.
   - Added validation for `profile_public` (must be `0`, `1`, `False`, or `True`). Invalid values return HTTP 400 `invalid_profile_public`.
   - Applied updates via `update_display_name` and `update_profile_public` when present in the request.
   - Returned `me_payload` with the updated values.

3. `src/claire/api/accounts_routes/console.py`
   - Removed obsolete hint `<p class="hint" data-profile-public-hint>`.
   - Added switch input `<input id="pref-profile-public" type="checkbox" switch data-settings-toggle="profile-public" ...>` in network mode.
   - Marked the checkbox `checked` when `account.profile_public` is true.
   - Added result placeholder `<p class="result" data-settings-result role="status" aria-live="polite"></p>`.
   - Preserved rule S9: omitted switch control and username field in single mode.

4. `src/claire/static/js/console_account.js`
   - Added function `bindToggles()`.
   - Attached `change` event listener to `input[data-settings-toggle='profile-public']`.
   - The listener sends `PATCH /api/me` with `{ profile_public: input.checked ? 1 : 0 }`.
   - On success: updates `[data-account-field='profile_public']` text and shows success message.
   - On failure: reverts checkbox state and shows error text.

5. `src/claire/accounts/username_policy.py`
   - Added 21 reserved words for Fedify and IdP integration to `RESERVED_USERNAMES`:
     `inbox`, `outbox`, `actor`, `actors`, `ap`, `fedify`, `federation`, `nodeinfo`, `oauth`, `auth`, `followers`, `following`, `shared_inbox`, `sharedinbox`, `webfinger`, `idp`, `oidc`, `openid`, `activitypub`, `wellknown`, `well_known`.

6. `tests/test_username_policy.py`
   - Synchronized `SPEC_RESERVED` with `RESERVED_USERNAMES` to include all 21 new reserved words.

7. `tests/test_account_routes.py`
   - Updated line 183 to assert `"profile_public": 0` in `GET /api/me` response.
   - Added unit test `test_account_store_update_profile_public_unit`.
   - Added API tests `test_me_patch_profile_public_and_combinations` for valid, invalid, and combined inputs.
   - Added end-to-end test `test_settings_account_profile_public_toggle_e2e` for `/settings/account` toggle and `/api/me` in network and single modes.

## 2. Test Verification

Test execution command:
```bash
uv run pytest tests/test_account_routes.py tests/test_username_policy.py tests/test_route_surface_contract.py -v
```
Result: 178 passed in 4.62s.
