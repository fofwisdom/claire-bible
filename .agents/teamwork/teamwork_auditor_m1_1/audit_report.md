## Forensic Audit Report

**Work Product**: Milestone M1 changes by Worker M1
**Profile**: General Project (Development Mode per ORIGINAL_REQUEST.md)
**Verdict**: CLEAN

### Phase Results
- Hardcoded test results check: PASS — No hardcoded test outputs or fixed dummy returns exist.
- Facade implementation check: PASS — Database queries and updates execute genuine SQLite statements.
- Pre-populated verification artifacts check: PASS — No fake logs, results, or attestation files exist in the workspace.
- Database persistence check: PASS — `AccountStore.update_profile_public` updates the `accounts` table and commits the transaction.
- Input validation check: PASS — `PATCH /api/me` rejects empty bodies, invalid keys, invalid values, and invalid types with HTTP 400.
- Error handling check: PASS — `AccountStore.update_profile_public` rolls back and raises `AccountNotFound` when the account does not exist.
- Security isolation check: PASS — `PATCH /api/me` restricts updates to the authenticated principal account. Mass-assignment is blocked.
- Single mode compliance (Rule S9) check: PASS — The web console hides `profile_public` controls in single mode.
- Reserved usernames synchronization check: PASS — All 21 Fedify and IdP terms exist in `RESERVED_USERNAMES` and `SPEC_RESERVED`.
- Independent test execution check: PASS — All target test suites pass (178 passed).

### Forensic Verification Details

#### 1. Source Code Inspection

1. **`src/claire/store/accounts.py`**:
   - `update_profile_public` runs an `UPDATE accounts SET profile_public=?, updated_at=? WHERE id=?` query.
   - It validates `cur.rowcount == 1`. It rolls back and raises `AccountNotFound(account_id)` if rowcount is not 1.
   - It calls `self.conn.commit()`.
   - It calls `self.get(account_id)` to read back the actual record from SQLite.

2. **`src/claire/api/accounts_routes/me.py`**:
   - `me_payload` returns the actual persisted value `1 if account.profile_public else 0`.
   - `me_patch` checks `not body or not (set(body) <= {"display_name", "profile_public"})`. It rejects invalid fields with HTTP 400.
   - It validates `profile_public` strictly. It accepts `True`, `False`, `0`, and `1`. It rejects other types (strings, floats, null, out-of-range integers).
   - It calls `update_profile_public` in a background thread and returns freshly loaded account data.

3. **`src/claire/api/accounts_routes/console.py`**:
   - Renders the switch toggle only when `mode == "network"`.
   - In single mode, the toggle HTML is empty. This follows Rule S9.
   - Removes the obsolete hint text `data-profile-public-hint`.

4. **`src/claire/static/js/console_account.js`**:
   - `bindToggles()` binds event listeners to `input[data-settings-toggle='profile-public']`.
   - Dispatches real `PATCH /api/me` requests with `{ profile_public: checked ? 1 : 0 }`.
   - Restores previous checkbox state if the network call fails or returns an error.

5. **`src/claire/accounts/username_policy.py`**:
   - Added 21 terms: `inbox`, `outbox`, `actor`, `actors`, `ap`, `fedify`, `federation`, `nodeinfo`, `oauth`, `auth`, `followers`, `following`, `shared_inbox`, `sharedinbox`, `webfinger`, `idp`, `oidc`, `openid`, `activitypub`, `wellknown`, `well_known`.
   - All terms are rejected by `validate_username`.

#### 2. Test Integrity Analysis

1. `tests/test_account_routes.py`:
   - `test_account_store_update_profile_public_unit` tests the store layer, timestamp update, and `AccountNotFound` exception.
   - `test_me_patch_profile_public_and_combinations` tests persistence, combinations with `display_name`, and 8 negative edge cases.
   - `test_settings_account_profile_public_toggle_e2e` tests server-side HTML rendering in network mode and single mode.

2. `tests/test_username_policy.py`:
   - `test_reserved_set_matches_spec_exactly` asserts strict set equality between `RESERVED_USERNAMES` and `SPEC_RESERVED`.
   - `test_every_reserved_name_is_rejected` tests every reserved word.

### Evidence

1. Target test suite execution:
   ```bash
   uv run pytest tests/test_account_routes.py tests/test_username_policy.py tests/test_route_surface_contract.py -v
   ```
   Result: 178 passed in 5.32s.

2. Settings and store test suite execution:
   ```bash
   uv run pytest tests/test_settings_account.py tests/test_account_store.py -v
   ```
   Result: 25 passed in 2.46s.

3. Full repository test suite execution:
   ```bash
   uv run pytest
   ```
   Result: 2778 passed in 320.26s.
   Note: One failure in `tests/test_security_hotfixes.py` (`test_escape_helpers_escape_single_quote[adoc_parser.js-re]`) is a pre-existing regression from commit `c4d0e1e` (Nov 8) outside the Milestone M1 scope.

4. Static analysis and syntax checks:
   - Python compilation: `python3 -m py_compile` passed with code 0.
   - JavaScript syntax: `node -c src/claire/static/js/console_account.js` passed with code 0.
   - CI environment and volume checks: `bash -n`, `docker compose config`, `uv lock --check` passed with code 0.
