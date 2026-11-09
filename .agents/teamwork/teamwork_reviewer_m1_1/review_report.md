# Review Report — Milestone M1

## Review Summary

**Verdict**: APPROVE

Worker M1 implemented requirements R1 and R2 accurately.
The implementation updates `profile_public` cleanly in the store and API.
Validation correctly permits only valid values (`0`, `1`, `False`, `True`).
Unexpected keys in `PATCH /api/me` are rejected with HTTP 400.
The settings UI hides the toggle in single mode to preserve rule S9.
All 21 reserved words are present in `RESERVED_USERNAMES` and tests.
Tests pass without failure. No integrity violations or code regressions exist.

## Findings

No defects or integrity violations were found.

### [Minor] Informational Note 1: Boolean Coercion
- What: Python treats boolean values as subtypes of `int`.
- Where: `src/claire/api/accounts_routes/me.py:158-163`
- Why: The code explicitly tests `raw_pp is True` and `raw_pp is False` before the `isinstance(raw_pp, int)` check.
- Suggestion: This is good practice because it prevents unexpected type conversions.

## Verified Claims

- `AccountStore.update_profile_public` updates `profile_public` and `updated_at`, then commits -> verified via `test_account_store_update_profile_public_unit` -> PASS
- `PATCH /api/me` validates `profile_public` and rejects invalid values (strings, floats, numbers outside 0 and 1, null) -> verified via `test_me_patch_profile_public_and_combinations` -> PASS
- `PATCH /api/me` rejects unexpected keys with HTTP 400 -> verified via `test_me_patch_profile_public_and_combinations` -> PASS
- Web console in network mode shows the toggle switch -> verified via `test_settings_account_profile_public_toggle_e2e` -> PASS
- Web console in single mode hides the toggle switch (rule S9) -> verified via `test_settings_account_profile_public_toggle_e2e` and `test_account_page_single_mode_admin_hides_username_and_profile_public` -> PASS
- `RESERVED_USERNAMES` contains all 21 reserved words for Fedify and IdP -> verified via `test_reserved_set_matches_spec_exactly` and `test_every_reserved_name_is_rejected` -> PASS
- No hard-wrapped code lines introduced in modified files -> verified via line inspection and git diff -> PASS

## Coverage Gaps

- None. All modified files and adjacent contract surfaces have test coverage.

## Unverified Items

- None. All claims were verified independently.

---

# Adversarial Challenge Report — Milestone M1

## Challenge Summary

**Overall risk assessment**: LOW

## Challenges

### [Low] Challenge 1: Concurrent Toggling on Console UI
- Assumption challenged: Rapid clicks on the toggle switch could cause race conditions.
- Attack scenario: A user clicks the toggle button multiple times while a request is in flight.
- Mitigation: In `src/claire/static/js/console_account.js:391`, `input.disabled = true;` is set before dispatching the request. The input stays disabled until completion (`input.disabled = false;` in `.then()`). If an error occurs, the checkbox state reverts.
- Result: Handled cleanly.

### [Low] Challenge 2: Partial Updates on Invalid Composite Requests
- Assumption challenged: A PATCH request with a valid `display_name` and an invalid `profile_public` might write the display name before failing.
- Attack scenario: Send `PATCH /api/me` with `{"display_name": "Bob", "profile_public": "invalid"}`.
- Mitigation: `src/claire/api/accounts_routes/me.py` validates all input fields in memory before executing database writes. Both fields must pass validation before either update runs.
- Result: Handled cleanly.

### [Low] Challenge 3: Negative Integers and Floats
- Assumption challenged: Numbers like `-1`, `2`, `1.0`, or `"1"` could pass integer checks.
- Attack scenario: Send non-binary numbers or string representations.
- Mitigation: Strict validation checks `isinstance(raw_pp, int)` and `raw_pp in (0, 1)`. Floats and strings fail this check and return HTTP 400 `invalid_profile_public`.
- Result: Handled cleanly.

## Stress Test Results

- `PATCH /api/me` with `{}` -> HTTP 400 -> PASS
- `PATCH /api/me` with `{"profile_public": 2}` -> HTTP 400 -> PASS
- `PATCH /api/me` with `{"profile_public": 1.0}` -> HTTP 400 -> PASS
- `PATCH /api/me` with `{"profile_public": "1"}` -> HTTP 400 -> PASS
- `PATCH /api/me` with `{"profile_public": None}` -> HTTP 400 -> PASS
- `PATCH /api/me` with `{"profile_public": 1, "extra": "x"}` -> HTTP 400 -> PASS
- `PATCH /api/me` with `{"display_name": "New", "profile_public": 1}` -> HTTP 200 with both updated -> PASS

## Unchallenged Areas

- External Fedify sidecar HTTP endpoints. Reason: Out of milestone M1 scope (slated for subsequent milestones).
