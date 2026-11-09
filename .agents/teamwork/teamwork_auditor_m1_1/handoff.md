# Handoff Report — Forensic Audit M1

Date: 2026-11-09
Auditor: Forensic Auditor M1
Directory: `/home/fow/Projects/claire-bible/.agents/teamwork/teamwork_auditor_m1_1`
Target: Milestone M1 Work Product

## 1. Observation

1. **`src/claire/store/accounts.py` (lines 378-389)**:
   - Added method `update_profile_public`:
     ```python
     def update_profile_public(self, account_id: int, profile_public: int | bool, *, now: float | None = None) -> Account:
         when = time.time() if now is None else now
         value = 1 if profile_public else 0
         cur = self.conn.execute(
             "UPDATE accounts SET profile_public=?, updated_at=? WHERE id=?",
             (value, when, account_id),
         )
         if cur.rowcount != 1:
             self.conn.rollback()
             raise AccountNotFound(account_id)
         self.conn.commit()
         return self.get(account_id)
     ```

2. **`src/claire/api/accounts_routes/me.py` (lines 53-62, 73-78, 145-173)**:
   - `me_payload` returns `"profile_public": 1 if account.profile_public else 0`.
   - `update_profile_public` helper executes `AccountStore.update_profile_public` in SQLite session.
   - `me_patch` validates `body`:
     - Rejects empty bodies and unknown keys with HTTP 400 `invalid_request`.
     - Validates `profile_public` values. Accepts only `True`, `False`, `0`, and `1`. Rejects invalid values and invalid types with HTTP 400 `invalid_profile_public`.
     - Calls `update_profile_public` and returns the newly loaded account record.

3. **`src/claire/api/accounts_routes/console.py` (lines 846-857, 919)**:
   - Renders `<input id="pref-profile-public" type="checkbox" switch data-settings-toggle="profile-public" ...>` when `mode == "network"`.
   - Renders empty string for single mode per Rule S9.
   - Removes obsolete hint `data-profile-public-hint`.

4. **`src/claire/static/js/console_account.js` (lines 385-408, 436)**:
   - `bindToggles()` attaches change event handler to `input[data-settings-toggle='profile-public']`.
   - Sends `PATCH /api/me` with `{ profile_public: input.checked ? 1 : 0 }`.
   - Updates `[data-account-field='profile_public']` on success.
   - Reverts toggle and displays error message on failure.

5. **`src/claire/accounts/username_policy.py` (lines 31-35)**:
   - Added 21 reserved words for Fedify and IdP integration:
     `inbox`, `outbox`, `actor`, `actors`, `ap`, `fedify`, `federation`, `nodeinfo`, `oauth`, `auth`, `followers`, `following`, `shared_inbox`, `sharedinbox`, `webfinger`, `idp`, `oidc`, `openid`, `activitypub`, `wellknown`, `well_known`.

6. **`tests/test_username_policy.py` (lines 33-37)**:
   - Added the identical 21 reserved words to `SPEC_RESERVED`.
   - `test_reserved_set_matches_spec_exactly` verifies set equality between `RESERVED_USERNAMES` and `SPEC_RESERVED`.

7. **`tests/test_account_routes.py` (lines 191, 222-338)**:
   - Updated GET /api/me test to assert `"profile_public": 0`.
   - Added unit test `test_account_store_update_profile_public_unit`.
   - Added API test `test_me_patch_profile_public_and_combinations` with positive and negative test cases.
   - Added end-to-end test `test_settings_account_profile_public_toggle_e2e` for network and single mode.

8. **Test Execution**:
   - Command: `uv run pytest tests/test_account_routes.py tests/test_username_policy.py tests/test_route_surface_contract.py -v`
   - Result: 178 passed in 5.32s.
   - Command: `uv run pytest tests/test_settings_account.py tests/test_account_store.py -v`
   - Result: 25 passed in 2.46s.
   - Command: `uv run pytest`
   - Result: 2778 passed, 1 failed (pre-existing `adoc_parser.js` test issue outside Milestone M1).

## 2. Logic Chain

1. Observation 1 shows that `AccountStore.update_profile_public` updates the SQLite database and commits the transaction. It raises `AccountNotFound` on missing accounts.
2. Observation 2 shows that `PATCH /api/me` validates the input types, calls `AccountStore.update_profile_public`, and returns the persisted state.
3. Observation 3 and 4 show that `/settings/account` renders the toggle in network mode, omits it in single mode (Rule S9), and sends real PATCH requests via JavaScript.
4. Observation 5 and 6 show that all 21 Fedify and IdP terms are added to `RESERVED_USERNAMES` and verified against `SPEC_RESERVED`.
5. Observation 7 and 8 show that tests cover unit, API, error cases, and end-to-end UI rendering without hardcoded or fake returns.
6. Therefore, Milestone M1 satisfies Requirements R1 and R2 cleanly and contains no integrity violations.

## 3. Caveats

Full repository test run showed one failure in `tests/test_security_hotfixes.py` (`test_escape_helpers_escape_single_quote[adoc_parser.js-re]`). Git history shows this is a pre-existing issue introduced by commit `c4d0e1e` on Nov 8. It does not affect any files in Milestone M1.

## 4. Conclusion

The audit verdict is **CLEAN**. Milestone M1 work product is authentic, complete, and contains no integrity violations.

## 5. Verification Method

To verify these results independently, execute:
```bash
uv run pytest tests/test_account_routes.py tests/test_username_policy.py tests/test_route_surface_contract.py -v
```
All 178 tests must pass.
