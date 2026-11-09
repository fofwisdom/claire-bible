# Review Report — Milestone M1

## Review Summary

**Verdict**: APPROVE

This review evaluated all code modifications for Milestone M1.
The modifications satisfy Requirement R1 and Requirement R2.
The implementation contains no integrity violations.
The tests verify all positive and negative execution paths.

## Findings

No critical, major, or minor defects were found.
The implementation adheres to project contracts, security boundaries, and coding standards.

### Positive Observations

1. **Input Validation Integrity**:
   `PATCH /api/me` strictly validates `profile_public`.
   The endpoint permits only integer values `0` and `1`, or boolean values `True` and `False`.
   The endpoint rejects negative numbers, strings, floats, extra keys, and empty bodies.
   Validation occurs before database writes.
   This sequence prevents partial updates.

2. **Access Control**:
   The update logic uses `principal.account_id` from the verified session.
   No user can modify the profile state of another account.

3. **Client-Side Fault Tolerance**:
   `console_account.js` disables the toggle control during network requests.
   If the network fails or the server returns an error, the script reverts the toggle control.
   The script displays an error message to the user.
   The script re-enables the toggle control after completion.

4. **Reserved Word Boundary**:
   The 21 added Fedify and IdP words protect federation routing endpoints.
   The fragment splitting rules do not break ordinary words.
   Valid usernames continue to pass validation.

5. **Interface and Layout Compliance**:
   All changes remain in designated project directories.
   No non-metadata files were placed in `.agents/teamwork/`.

## Verified Claims

- `AccountStore.update_profile_public` updates the database record and updated timestamp → verified via unit test `test_account_store_update_profile_public_unit` → PASS
- `PATCH /api/me` validates `profile_public` and updates account → verified via API test `test_me_patch_profile_public_and_combinations` → PASS
- `GET /api/me` returns `profile_public` as `0` or `1` → verified via API test `test_me_read_and_display_name_update` → PASS
- `/settings/account` renders toggle in network mode and hides toggle in single mode → verified via E2E test `test_settings_account_profile_public_toggle_e2e` → PASS
- `RESERVED_USERNAMES` matches `SPEC_RESERVED` exactly → verified via contract test `test_reserved_set_matches_spec_exactly` → PASS
- All 178 tests in target test suites pass → verified via `uv run pytest tests/test_account_routes.py tests/test_username_policy.py tests/test_route_surface_contract.py -v` → PASS

## Adversarial Analysis

### 1. Assumption Stress-Testing
- **Assumption**: A client might send floating-point values like `1.0` or `0.0`.
  - Result: `isinstance(raw_pp, int)` evaluates to false for `float`. The server returns HTTP 400 `invalid_profile_public`. PASS.
- **Assumption**: A client might send string values like `"1"` or `"0"`.
  - Result: The server rejects strings with HTTP 400 `invalid_profile_public`. PASS.
- **Assumption**: A client might send unknown parameters together with `profile_public`.
  - Result: `not (set(body) <= {"display_name", "profile_public"})` detects unauthorized keys and returns HTTP 400 `invalid_request`. PASS.

### 2. Client Resilience
- **Scenario**: Network connection disconnects while user toggles the switch.
  - Result: The `fetch` promise rejects. The `.catch()` handler restores the previous checkbox state, shows a connection error message, and enables the input. PASS.

### 3. Namespace Collision
- **Scenario**: A user registers a name containing an added reserved word as a substring (for example, `badminton` containing `admin`, or `allison` containing `all`).
  - Result: Word fragment segmentation splits only on underscores and digits. Whole words are not segmented. `badminton` and `allison` remain permitted. PASS.

## Coverage Gaps

No coverage gaps identified.
The test suite covers unit storage methods, route contracts, client HTML rendering, and end-to-end integration.

## Unverified Items

None. All claims and paths were verified directly through code inspection and automated test execution.
