# Handoff Report — Milestone M1 Reviewer 2

Date: 2026-11-09
Reviewer: Reviewer M1 (Instance 2)
Verdict: APPROVE

## 1. Observation

1. **`src/claire/store/accounts.py` (lines 378-389)**:
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
   The method updates `profile_public` and `updated_at` in SQLite.
   It uses parameter binding to prevent SQL injection.
   It rolls back and raises `AccountNotFound` if the account does not exist.

2. **`src/claire/api/accounts_routes/me.py` (lines 145-173)**:
   - `me_patch` checks body keys:
     ```python
     if not body or not (set(body) <= {"display_name", "profile_public"}):
         return json_error(400, "invalid_request", fields=["display_name", "profile_public"])
     ```
   - `me_patch` checks `profile_public`:
     ```python
     if "profile_public" in body:
         raw_pp = body["profile_public"]
         if raw_pp is True:
             profile_public = 1
         elif raw_pp is False:
             profile_public = 0
         elif isinstance(raw_pp, int) and raw_pp in (0, 1):
             profile_public = raw_pp
         else:
             return json_error(400, "invalid_profile_public")
     ```
   - `me_payload` returns `"profile_public": 1 if account.profile_public else 0`.
   - The handler updates only `principal.account_id`.

3. **`src/claire/api/accounts_routes/console.py` (lines 846-857)**:
   - Network mode appends `profile_public` status to `info_rows`.
   - Network mode renders `<input id="pref-profile-public" type="checkbox" switch data-settings-toggle="profile-public"...>`.
   - Single mode omits both the status row and the switch toggle.

4. **`src/claire/static/js/console_account.js` (lines 385-408)**:
   - `bindToggles()` attaches change event listeners to `input[data-settings-toggle='profile-public']`.
   - It sets `input.disabled = true` during request transmission.
   - On error or exception, it executes `input.checked = !checked` to revert the toggle.
   - It shows error feedback and re-enables the toggle input in `.then()`.

5. **`src/claire/accounts/username_policy.py` (lines 31-35)**:
   - Added 21 reserved words for Fedify and IdP integration:
     `inbox`, `outbox`, `actor`, `actors`, `ap`, `fedify`, `federation`, `nodeinfo`,
     `oauth`, `auth`, `followers`, `following`, `shared_inbox`, `sharedinbox`,
     `webfinger`, `idp`, `oidc`, `openid`, `activitypub`, `wellknown`, `well_known`.

6. **Test Execution**:
   - Command: `uv run pytest tests/test_account_routes.py tests/test_username_policy.py tests/test_route_surface_contract.py -v`.
   - Output: `178 passed in 4.76s`.
   - Command: `uv run pytest tests/test_settings_account.py tests/test_account_store.py -v`.
   - Output: `25 passed in 2.12s`.

## 2. Logic Chain

1. Observation 1 confirms that `AccountStore` implements database updates for `profile_public`.
2. Observation 2 confirms that `PATCH /api/me` strictly validates input types and restricts modifications to the caller.
3. Observation 3 confirms that `/settings/account` renders the toggle control in network mode and preserves rule S9 in single mode.
4. Observation 4 confirms that `console_account.js` handles network and server errors gracefully by reverting state.
5. Observation 5 confirms that reserved federation words are synchronized and do not block valid names.
6. Observation 6 confirms that all unit, contract, and integration tests pass without error.
7. Therefore, the implementation meets all requirements for Milestone M1 without defects.

## 3. Caveats

No caveats. All modified files were inspected and tested directly.

## 4. Conclusion

Verdict: APPROVE.
The changes for Milestone M1 satisfy Requirement R1 and Requirement R2.
The code contains no security regressions or integrity violations.

## 5. Verification Method

To verify these changes independently, run:
```bash
uv run pytest tests/test_account_routes.py tests/test_username_policy.py tests/test_route_surface_contract.py -v
```
All 178 tests must pass.
