# Challenge Report: Milestone M2 Requirement R3

Target: Token Exchange Account Binding Integrity (`src/claire/api/oauth.py`)
Challenger: Challenger 1 (teamwork_challenger_m2_1)
Date: 2026-11-09
Verdict: APPROVE

## Challenge Summary

**Overall risk assessment**: LOW
**Explicit verdict**: APPROVE

Requirement R3 blocks fallbacks to admin account ID 1.
The empirical verification shows that account binding is enforced.
All 24 adversarial probe tests passed cleanly.

## Challenges

### [Medium] Challenge 1: Orphaned Admin Token on Aborted Update

- **Assumption challenged**:
  The system assumes that `create_oauth_token` and `UPDATE oauth_tokens` execute as an atomic transaction.
- **Attack scenario**:
  `create_oauth_token` inserts a token with `account_id = 1` and commits immediately.
  If the subsequent `UPDATE oauth_tokens` fails (for example, database lock or trigger abort), the update does not complete.
- **Blast radius**:
  The endpoint returns HTTP 500 to the client.
  The client does not get the token.
  However, the database retains an active token bound to admin account ID 1.
- **Mitigation**:
  Pass `account_id` directly to `create_oauth_token`.
  Do not commit an intermediate row with admin account ID 1.

### [Low] Challenge 2: RFC 8693 Parameter `subject_token_type` Lenience

- **Assumption challenged**:
  The system assumes that `subject_token_type` is validated per RFC 8693 Section 2.1.
- **Attack scenario**:
  A client sends an unsupported or invalid `subject_token_type` value.
- **Blast radius**:
  The endpoint processes the exchange if `subject_token` is valid.
  Account binding remains secure.
  However, this behavior deviates from RFC 8693 specification.
- **Mitigation**:
  Add validation to reject invalid `subject_token_type` strings.

## Stress Test Results

Test suite file: `.agents/teamwork/teamwork_challenger_m2_1/test_challenge_r3.py` (24 tests)

1. Unbound session token (`account_id = NULL`) -> Expected HTTP 400 invalid_grant -> Actual HTTP 400 -> PASS
2. Non-existent account ID (`account_id = 99999`) -> Expected HTTP 400 invalid_grant -> Actual HTTP 400 -> PASS
3. Non-existent and SQL injection tokens -> Expected HTTP 400 invalid_grant -> Actual HTTP 400 -> PASS
4. Empty, missing, and whitespace parameters -> Expected HTTP 400 invalid_request -> Actual HTTP 400 -> PASS
5. JSON payloads with null, boolean, integer, list, dict -> Expected HTTP 400 -> Actual HTTP 400 -> PASS
6. Suspended account -> Expected HTTP 400 invalid_grant -> Actual HTTP 400 -> PASS
7. Disabled account -> Expected HTTP 400 invalid_grant -> Actual HTTP 400 -> PASS
8. Pending verification account -> Expected HTTP 400 invalid_grant -> Actual HTTP 400 -> PASS
9. Archived account -> Expected HTTP 400 invalid_grant -> Actual HTTP 400 -> PASS
10. Locked account -> Expected HTTP 400 invalid_grant -> Actual HTTP 400 -> PASS
11. Deleted account -> Expected HTTP 400 invalid_grant -> Actual HTTP 400 -> PASS
12. Purged account -> Expected HTTP 400 invalid_grant -> Actual HTTP 400 -> PASS
13. Non-admin member token binding -> Expected token bound to member ID, not 1 -> Actual row account_id matches member -> PASS
14. Non-admin token API identity -> Expected `is_admin: False` on `/whoami` -> Actual `is_admin: False` -> PASS
15. Non-admin token scope restriction -> Expected `/api/me` blocked (HTTP 404) -> Actual HTTP 404 -> PASS
16. Token refresh account retention -> Expected refreshed token keeps member ID -> Actual row account_id matches member -> PASS
17. Concurrent exchange stress test (20 requests across 5 accounts) -> Expected correct binding for all -> Actual 20/20 correct -> PASS
18. Scope escalation attempt (requesting `owner`) -> Expected `readonly` scope -> Actual `readonly` -> PASS
19. Post-issuance account suspension -> Expected immediate token rejection on API calls -> Actual HTTP 404/401 -> PASS
20. OAuth token used as subject token -> Expected HTTP 400 invalid_grant -> Actual HTTP 400 -> PASS

## Unchallenged Areas

- UI web templates in console (checked by Reviewer and Auditor).
- OAuth dynamic client registration (covered in M1).
