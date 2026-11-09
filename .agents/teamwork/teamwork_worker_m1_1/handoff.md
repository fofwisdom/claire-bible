# Handoff Report — Milestone M1

Date: 2026-11-09
Worker: Worker M1
Directory: `/home/fow/Projects/claire-bible/.agents/teamwork/teamwork_worker_m1_1`

## 1. Observation

1. **`src/claire/store/accounts.py`**:
   - Added `update_profile_public` at lines 378-389:
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

2. **`src/claire/api/accounts_routes/me.py`**:
   - Updated `me_payload` at lines 53-62 to include `"profile_public": 1 if account.profile_public else 0`.
   - Added `update_profile_public` helper at lines 71-77.
   - Updated `me_patch` at lines 145-173 to accept `{"display_name", "profile_public"}`, validate values, and apply updates.

3. **`src/claire/api/accounts_routes/console.py`**:
   - Removed obsolete hint `data-profile-public-hint` from `_settings_account_body`.
   - Added `profile_toggle_html` at lines 849-858 with `<input id="pref-profile-public" type="checkbox" switch data-settings-toggle="profile-public" ...>`.
   - Preserved rule S9: in single mode (`mode == "single"`), `profile_toggle_html` evaluates to `""`.

4. **`src/claire/static/js/console_account.js`**:
   - Added `bindToggles()` at lines 385-408.
   - Added listener for change events on `input[data-settings-toggle='profile-public']`.
   - Dispatches `PATCH /api/me` with `{ profile_public: input.checked ? 1 : 0 }`.
   - Updates `[data-account-field='profile_public']` on success. Reverts input on failure.

5. **`src/claire/accounts/username_policy.py`**:
   - Added 21 Fedify and IdP reserved words to `RESERVED_USERNAMES` at lines 31-35.

6. **`tests/test_username_policy.py`**:
   - Synchronized `SPEC_RESERVED` at lines 33-37 with `RESERVED_USERNAMES`.

7. **`tests/test_account_routes.py`**:
   - Updated line 191 to expect `"profile_public": 0` in `GET /api/me`.
   - Added `test_account_store_update_profile_public_unit` at lines 222-240.
   - Added `test_me_patch_profile_public_and_combinations` at lines 243-286.
   - Added `test_settings_account_profile_public_toggle_e2e` at lines 289-338.

8. **Test Execution**:
   - Ran `uv run pytest tests/test_account_routes.py tests/test_username_policy.py tests/test_route_surface_contract.py -v`.
   - Output: `178 passed in 4.62s`.
   - Ran `uv run pytest tests/test_settings_account.py -v`.
   - Output: `10 passed in 1.93s`.
   - Ran `uv run pytest tests/test_account_store.py -v`.
   - Output: `15 passed in 0.36s`.

## 2. Logic Chain

1. Observation 1 shows that `AccountStore` now exposes `update_profile_public`. This updates the database record and commits the transaction.
2. Observation 2 shows that `PATCH /api/me` validates `profile_public` and calls `AccountStore.update_profile_public`. It also shows that `GET /api/me` returns `profile_public`.
3. Observation 3 and 4 show that the web console renders the switch input and triggers `PATCH /api/me` on toggle change.
4. Observation 5 and 6 show that the reserved username policy and its verification suite contain identical Fedify and IdP terms.
5. Observation 7 and 8 show that all unit tests, route tests, surface tests, and end-to-end integration tests pass without error.
6. Therefore, Requirements R1 and R2 are fully met and verified.

## 3. Caveats

No caveats. All changes are verified with automated tests. No regressions were observed.

## 4. Conclusion

Requirement R1 (`profile_public` toggle and account contract seal) and Requirement R2 (Reserved usernames synchronization) are complete.
All modified files remain within the exclusive file ownership boundaries assigned to Worker M1.

## 5. Verification Method

To verify these changes independently, execute:
```bash
uv run pytest tests/test_account_routes.py tests/test_username_policy.py tests/test_route_surface_contract.py -v
```
All 178 tests must pass.
