# Forensic Audit Report: Milestone M2

**Work Product**: Milestone M2 Implementation (`src/claire/api/oauth.py`, `tests/test_oauth_token_exchange.py`, `tests/test_syndication_route.py`)  
**Profile**: General Project  
**Integrity Mode**: Development  
**Verdict**: CLEAN  

---

## 1. Executive Summary

This forensic audit inspected all changes made for Milestone M2.
Milestone M2 implements Requirement R3 and Requirement R5.
The auditor inspected source code, test suites, and database operations.
The auditor performed independent verification tests.
All forensic checks on Milestone M2 work products passed without exception.
No hardcoded test outputs exist.
No fake or bypassed checks exist.
The fallback to admin account ID 1 is removed.
The handler verifies SQLite session bindings and account records.
The handler contains genuine error handling for missing accounts, invalid tokens, and inactive states.

---

## 2. Forensic Phase Results

### Phase 1: Source Code Analysis

- **Check 1.1: Hardcoded test results**: PASS  
  Source code in `src/claire/api/oauth.py` does not contain hardcoded results or expected test strings. All outputs depend on live SQLite database queries and cryptographic token generation.

- **Check 1.2: Facade detection**: PASS  
  The endpoint `handle_token` implements full verification logic. It validates session bindings, checks account existence, checks account status, and updates database records. No dummy returns or placeholder functions exist.

- **Check 1.3: Pre-populated artifact detection**: PASS  
  The workspace contains no pre-populated log files, fake test outputs, or spoofed attestation records for Milestone M2.

### Phase 2: Behavioral Verification

- **Check 2.1: Admin account ID 1 fallback removal**: PASS  
  In `src/claire/api/oauth.py` (lines 894-925), the unauthenticated fallback branch was removed. Unbound, invalid, or expired subject tokens trigger an immediate HTTP 400 `invalid_grant` response. Non-admin accounts receive tokens bound strictly to their account ID.

- **Check 2.2: SQLite session binding and account verification**: PASS  
  The handler executes `accounts_store.validate_session_binding(conn, subject_token)`. If the binding is missing or `account_id` is null, the request fails. If the account status is not in `CREDENTIAL_ACCEPTING_STATUSES`, the request fails.

- **Check 2.3: Genuine error handling**: PASS  
  The handler explicitly catches `accounts_store.AccountNotFound` and returns HTTP 400 with `"Account not found"`. It validates token presence, token validity, and token expiration. The `try ... finally` block guarantees SQLite connection closure.

- **Check 2.4: Syndication prototype sealing (R5)**: PASS  
  Route `/prototype/syndication` is sealed as an authenticated reference prototype under system read permissions. Live `/syndication` does not expose prototype seeds, client factories, or peer factories.

- **Check 2.5: Target test suite execution**: PASS  
  The auditor ran all target test suites independently:
  - `tests/test_oauth_token_exchange.py`: 10 passed in 1.24s.
  - `tests/test_syndication_route.py`: 25 passed in 1.98s.
  - Related OAuth and syndication isolation suite: 18 passed in 1.83s.

- **Check 2.6: Repository CI test execution (`./scripts/ci.sh`)**: PASS (Milestone Scope) / OBSERVE (Repository Scope)  
  CI executed 2,790 tests. 2,789 tests passed, 1 test failed.
  The failing test is `tests/test_security_hotfixes.py::test_escape_helpers_escape_single_quote[adoc_parser.js-re]`.
  Investigation showed this failure was introduced prior to Milestone M2 by commit `c4d0e1e` (`Fix AsciiDoc single quote corruption into highlight mark tags`). Commit `c4d0e1e` updated `function esc(s)` in `src/claire/static/js/renderers/adoc_parser.js` to `function esc(s, escapeQuotes = true)`.
  This pre-existing issue does not involve Milestone M2 files or requirements.

---

## 3. Evidence

### 3.1 Code Diff in `src/claire/api/oauth.py`

```diff
--- a/src/claire/api/oauth.py
+++ b/src/claire/api/oauth.py
@@ -893,39 +893,33 @@ async def handle_token(request: Request) -> Response:
 
         conn = dbm.connect_existing(config.db_file)
         try:
-            exchange_account_id: int | None = None
             binding = accounts_store.validate_session_binding(conn, subject_token)
-            if binding is not None:
-                try:
-                    acc = accounts_store.AccountStore(conn).get(binding.account_id)
-                    if acc.status not in accounts_store.CREDENTIAL_ACCEPTING_STATUSES:
-                        return JSONResponse({"error": "invalid_grant", "error_description": f"Account is {acc.status}"}, status_code=400)
-                    exchange_account_id = binding.account_id
-                except accounts_store.AccountNotFound:
-                    return JSONResponse({"error": "invalid_grant", "error_description": "Account not found"}, status_code=400)
-            else:
-                # Check session or bootstrap
-                exchanged = dbm.exchange_session_token(
-                    conn, subject_token, scopes=("owner", "collaborator", "readonly")
+            if binding is None or binding.account_id is None:
+                return JSONResponse(
+                    {"error": "invalid_grant", "error_description": "Subject token is invalid, expired, or unbound"},
+                    status_code=400,
                 )
-                if not exchanged:
-                    # Also check if it's already an active session token
-                    v_scope = dbm.validate_session_scope(
-                        conn, subject_token, scopes=("owner", "collaborator", "readonly")
+
             try:
                 acc = accounts_store.AccountStore(conn).get(binding.account_id)
                 if acc.status not in accounts_store.CREDENTIAL_ACCEPTING_STATUSES:
                     return JSONResponse(
                         {"error": "invalid_grant", "error_description": f"Account is {acc.status}"},
                         status_code=400,
                     )
+                exchange_account_id: int = int(binding.account_id)
+            except accounts_store.AccountNotFound:
+                return JSONResponse(
+                    {"error": "invalid_grant", "error_description": "Account not found"},
+                    status_code=400,
+                )
 
             # Issue isolated oauth token
             access_token, refresh_token = dbm.create_oauth_token(
                 conn, client_id="token_exchange_client", scope="readonly"
             )
-            if exchange_account_id is not None:
-                conn.execute("UPDATE oauth_tokens SET account_id=? WHERE token=?", (exchange_account_id, access_token))
-                conn.commit()
+            conn.execute("UPDATE oauth_tokens SET account_id=? WHERE token=?", (exchange_account_id, access_token))
+            conn.commit()
         finally:
             conn.close()
```

### 3.2 Test Run Output: `test_oauth_token_exchange.py`

```text
============================= test session starts ==============================
platform linux -- Python 3.14.4, pytest-9.1.1, pluggy-1.6.0
collected 10 items

tests/test_oauth_token_exchange.py::test_token_exchange_missing_subject_token PASSED [ 10%]
tests/test_oauth_token_exchange.py::test_token_exchange_valid_bound_session_token_admin PASSED [ 20%]
tests/test_oauth_token_exchange.py::test_token_exchange_valid_bound_session_token_member PASSED [ 30%]
tests/test_oauth_token_exchange.py::test_token_exchange_unbound_session_token_rejected PASSED [ 40%]
tests/test_oauth_token_exchange.py::test_token_exchange_invalid_subject_token_rejected PASSED [ 50%]
tests/test_oauth_token_exchange.py::test_token_exchange_expired_subject_token_rejected PASSED [ 60%]
tests/test_oauth_token_exchange.py::test_token_exchange_unapproved_subject_token_rejected PASSED [ 70%]
tests/test_oauth_token_exchange.py::test_token_exchange_inactive_or_suspended_account_rejected PASSED [ 80%]
tests/test_oauth_token_exchange.py::test_token_exchange_deleted_account_rejected PASSED [ 90%]
tests/test_oauth_token_exchange.py::test_token_exchange_non_admin_never_acquires_admin_account_id PASSED [100%]

============================== 10 passed in 1.24s ==============================
```

### 3.3 Test Run Output: `test_syndication_route.py`

```text
============================= test session starts ==============================
platform linux -- Python 3.14.4, pytest-9.1.1, pluggy-1.6.0
collected 25 items

tests/test_syndication_route.py::test_syndication_and_preferences_routes_io_interface PASSED [  4%]
...
tests/test_syndication_route.py::test_prototype_syndication_sealed_and_isolated PASSED [100%]

============================== 25 passed in 1.98s ==============================
```

### 3.4 CI Run Failure Details

```text
FAILED tests/test_security_hotfixes.py::test_escape_helpers_escape_single_quote[adoc_parser.js-re]
1 failed, 2789 passed, 14 warnings, 6 subtests passed in 327.00s (0:05:26)
Root cause: Commit c4d0e1e modified adoc_parser.js function signature to `function esc(s, escapeQuotes = true)`.
This predates Milestone M2 and does not affect Milestone M2 targets.
```
