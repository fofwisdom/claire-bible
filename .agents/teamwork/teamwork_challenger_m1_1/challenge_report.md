# Challenge Report — Milestone M1 Requirement R1

**Evaluator**: Empirical Challenger 1
**Target**: Requirement R1 (profile_public toggle and account contract seal)
**Date**: 2026-11-09
**Verdict**: APPROVE

## Challenge Summary

**Overall risk assessment**: LOW

We tested the implementation with adversarial test suites.
All boundary inputs, invalid data types, extra keys, and SQL injection payloads fail safely.
The database schema and types maintain consistency.
Single mode and network mode render correctly per specification.

## Challenges

### [Low] Challenge 1: Type Coercion and Boolean Interoperability

- **Assumption challenged**:
  The system must accept both integer values (0, 1) and boolean values (False, True).
  The system must reject float values (0.0, 1.0) and string representations ("0", "1", "true").
- **Attack scenario**:
  Send payload `{"profile_public": 1.0}`, `{"profile_public": "1"}`, or `{"profile_public": "true"}`.
  A weak implementation might cast floats or strings to integers.
- **Blast radius**:
  Silent data corruption or incorrect profile exposure.
- **Mitigation verified**:
  `me_patch` performs strict type checks.
  Booleans `True`/`False` and integers `0`/`1` pass.
  Floats, strings, `None`, lists, and dictionaries return HTTP 400 `invalid_profile_public`.
  The SQLite storage type is strictly `integer`.

### [Low] Challenge 2: Privilege Escalation via Extra Payload Keys

- **Assumption challenged**:
  Callers must not pass arbitrary fields (for example `role`, `status`, `public_id`) to `PATCH /api/me`.
- **Attack scenario**:
  A regular user sends `{"profile_public": 1, "role": "admin"}` to escalate privileges.
- **Blast radius**:
  Unauthorized administrative privilege grant.
- **Mitigation verified**:
  `me_patch` validates `set(body) <= {"display_name", "profile_public"}`.
  Any unexpected key returns HTTP 400 `invalid_request`.

### [Low] Challenge 3: Atomic Validation across Multiple Fields

- **Assumption challenged**:
  When a payload contains both `display_name` and `profile_public`, a failure in one field must not apply changes to the other.
- **Attack scenario**:
  Send `{"display_name": "A" * 65, "profile_public": 1}`.
- **Blast radius**:
  Partial database updates with inconsistent client error responses.
- **Mitigation verified**:
  Validation executes before any database store update.
  When either field fails validation, no changes apply to the database.

### [Low] Challenge 4: Information Leakage in Single Mode (Rule S9)

- **Assumption challenged**:
  Single mode console rendering must hide profile visibility controls and hints.
- **Attack scenario**:
  A user visits `/settings/account` in single mode.
- **Blast radius**:
  Exposure of network federation controls to single-tenant users.
- **Mitigation verified**:
  In single mode, `profile_toggle_html` evaluates to empty string.
  The toggle switch, account field, and obsolete hints do not appear in the HTML.

## Stress Test Results

- `PATCH /api/me` with `{"profile_public": True}` → returns HTTP 200, integer `1` → pass
- `PATCH /api/me` with `{"profile_public": False}` → returns HTTP 200, integer `0` → pass
- `PATCH /api/me` with `{"profile_public": 1}` → returns HTTP 200, integer `1` → pass
- `PATCH /api/me` with `{"profile_public": 0}` → returns HTTP 200, integer `0` → pass
- `PATCH /api/me` with `{"profile_public": 2}` → returns HTTP 400 `invalid_profile_public` → pass
- `PATCH /api/me` with `{"profile_public": -1}` → returns HTTP 400 `invalid_profile_public` → pass
- `PATCH /api/me` with `{"profile_public": 999}` → returns HTTP 400 `invalid_profile_public` → pass
- `PATCH /api/me` with `{"profile_public": 1.0}` → returns HTTP 400 `invalid_profile_public` → pass
- `PATCH /api/me` with `{"profile_public": "true"}` → returns HTTP 400 `invalid_profile_public` → pass
- `PATCH /api/me` with `{"profile_public": "1"}` → returns HTTP 400 `invalid_profile_public` → pass
- `PATCH /api/me` with `{"profile_public": None}` → returns HTTP 400 `invalid_profile_public` → pass
- `PATCH /api/me` with `{"profile_public": {}}` → returns HTTP 400 `invalid_profile_public` → pass
- `PATCH /api/me` with `{"profile_public": []}` → returns HTTP 400 `invalid_profile_public` → pass
- `PATCH /api/me` with unexpected key `{"extra": 1}` → returns HTTP 400 `invalid_request` → pass
- `PATCH /api/me` with empty object `{}` → returns HTTP 400 `invalid_request` → pass
- `PATCH /api/me` with SQL injection payload → returns HTTP 400 `invalid_profile_public` → pass
- `PATCH /api/me` with invalid `display_name` + valid `profile_public` → rejected with zero state change → pass
- Database `typeof(profile_public)` in SQLite → verified strictly `integer` → pass
- Database `updated_at` advance → verified monotonic timestamp advance on update → pass
- Member account `PATCH /api/me` → verified non-admin member can toggle profile_public → pass
- Readonly session and unauthenticated call → verified fail-closed HTTP 404 → pass
- Console `/settings/account` in network mode → switch and status text rendered → pass
- Console `/settings/account` in single mode → switch and status text hidden (Rule S9) → pass

## Unchallenged Areas

- Fedify actor inbox delivery: Out of scope for Requirement R1. Covered under upcoming sidecar milestone.
- Pre-existing CI test mismatch: `tests/test_security_hotfixes.py::test_escape_helpers_escape_single_quote[adoc_parser.js-re]` failed in full CI run. Commit `c4d0e1e` changed `esc(s, quote)` before Milestone M1. This issue is unrelated to Requirement R1.

