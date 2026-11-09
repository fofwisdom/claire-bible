# Technical Survey Report: Requirements R3 and R5

Author: Survey Explorer 2
Date: 2026-11-09
Status: Complete

## 1. Executive Summary

This report gives the findings of the read-only exploration for two requirements:
1. Requirement R3: Token Exchange Account Binding Integrity in RFC 8693 (`src/claire/api/oauth.py`).
2. Requirement R5: Syndication Prototype Sealing and Cleanup (`/prototype/syndication`).

Key findings:
- In Requirement R3, a security defect exists in `src/claire/api/oauth.py`. When session binding validation returns `None`, an unauthenticated fallback branch executes. In this branch, `exchange_account_id` stays `None`. The database helper `create_oauth_token` assigns `ADMIN_ACCOUNT_ID = 1` by default. The update step is skipped. Thus, an unbound token receives an access token bound to the administrator account (ID 1).
- No automated tests exist for RFC 8693 Token Exchange in the current test suite.
- In Requirement R5, `/prototype/syndication` and its seed components are fully functional mock assets. Five separate test suites currently assert their presence and exact behavior.
- The safest approach for Requirement R5 is formal sealing as an isolated reference prototype. Hard deletion will cause immediate test regressions across five test files.

---

## 2. Requirement R3: Token Exchange Account Binding Integrity

### 2.1 Code Flow Analysis in `src/claire/api/oauth.py`

The OAuth token endpoint is `POST /oauth/token` in `src/claire/api/oauth.py`.
Lines 884 to 942 process `grant_type == "urn:ietf:params:oauth:grant-type:token-exchange"`.

```python
    # 3. RFC 8693 Token Exchange
    if grant_type == "urn:ietf:params:oauth:grant-type:token-exchange":
        subject_token = str(params.get("subject_token") or "")
        subject_token_type = str(params.get("subject_token_type") or "")

        if not subject_token:
            return JSONResponse(
                {"error": "invalid_request", "error_description": "Missing subject_token"},
                status_code=400,
            )

        conn = dbm.connect_existing(config.db_file)
        try:
            exchange_account_id: int | None = None
            binding = accounts_store.validate_session_binding(conn, subject_token)
            if binding is not None:
                try:
                    acc = accounts_store.AccountStore(conn).get(binding.account_id)
                    if acc.status not in accounts_store.CREDENTIAL_ACCEPTING_STATUSES:
                        return JSONResponse({"error": "invalid_grant", "error_description": f"Account is {acc.status}"}, status_code=400)
                    exchange_account_id = binding.account_id
                except accounts_store.AccountNotFound:
                    return JSONResponse({"error": "invalid_grant", "error_description": "Account not found"}, status_code=400)
            else:
                # Check session or bootstrap
                exchanged = dbm.exchange_session_token(
                    conn, subject_token, scopes=("owner", "collaborator", "readonly")
                )
                if not exchanged:
                    # Also check if it's already an active session token
                    v_scope = dbm.validate_session_scope(
                        conn, subject_token, scopes=("owner", "collaborator", "readonly")
                    )
                    if not v_scope:
                        return JSONResponse(
                            {"error": "invalid_grant", "error_description": "Subject token is invalid or expired"},
                            status_code=400,
                        )

            # Issue isolated oauth token
            access_token, refresh_token = dbm.create_oauth_token(
                conn, client_id="token_exchange_client", scope="readonly"
            )
            if exchange_account_id is not None:
                conn.execute("UPDATE oauth_tokens SET account_id=? WHERE token=?", (exchange_account_id, access_token))
                conn.commit()
        finally:
            conn.close()
```

### 2.2 Identification of Root Cause and Admin Fallback

1. Line 896 initializes `exchange_account_id` to `None`.
2. Line 897 calls `accounts_store.validate_session_binding(conn, subject_token)`.
3. If `binding` is `None` (for example, if `auth_sessions.account_id` is `NULL`, or if `subject_token` failed binding lookup), execution enters the `else:` branch at line 906.
4. In lines 908 to 920, `dbm.exchange_session_token` and `dbm.validate_session_scope` check whether the token is in `auth_sessions`. These legacy functions do not inspect `account_id`.
5. If either function succeeds, execution continues past line 920. Notice that `exchange_account_id` remains `None`.
6. At line 923, `dbm.create_oauth_token(conn, client_id="token_exchange_client", scope="readonly")` executes.
7. In `src/claire/store/db.py` (lines 3053-3058):
   ```python
   conn.execute(
       """INSERT INTO oauth_tokens
       (token, refresh_token, client_id, scope, created_at, expires_at, account_id)
       VALUES (?, ?, ?, ?, ?, ?, ?)""",
       (access_token, refresh_token, client_id, scope, now, now + access_ttl, ADMIN_ACCOUNT_ID),
   )
   ```
   `ADMIN_ACCOUNT_ID` equals `1`. Thus, the token record initially receives `account_id = 1`.
8. At line 926 in `src/claire/api/oauth.py`:
   ```python
   if exchange_account_id is not None:
       conn.execute("UPDATE oauth_tokens SET account_id=? WHERE token=?", (exchange_account_id, access_token))
       conn.commit()
   ```
   Because `exchange_account_id` is `None`, the `if` condition evaluates to `False`. The update does not execute.
9. Result: The token table retains `account_id = 1`. An unbound session token successfully obtains an OAuth token for the administrator account.

### 2.3 Proposed Solution to Enforce Account Binding Integrity

To stop unauthorized fallback to admin account 1, the implementer must apply these rules:
1. Require explicit session binding:
   - Call `accounts_store.validate_session_binding(conn, subject_token)`.
   - If `binding is None` or `binding.account_id is None`, reject the request immediately with HTTP 400 `invalid_grant`.
   - Use error description: `"Subject token is invalid, expired, or unbound"`.
2. Delete the insecure fallback branch:
   - Remove lines 906 to 920 (the `else:` block containing `exchange_session_token` and `validate_session_scope`).
3. Validate account status:
   - Verify `AccountStore(conn).get(binding.account_id)`.
   - Ensure `acc.status` is in `CREDENTIAL_ACCEPTING_STATUSES`.
4. Ensure explicit account parameter in token creation:
   - Make `exchange_account_id: int` mandatory before token creation.
   - Update `create_oauth_token` in `src/claire/store/db.py` to accept `account_id: int`.
   - Never insert `ADMIN_ACCOUNT_ID` when an explicit account ID is required.

### 2.4 Test Suite Coverage for Token Exchange

- Current state:
  - Search of `tests/` for `token-exchange` and `subject_token` yielded zero matches.
  - Neither `tests/test_oauth_flow.py` nor `tests/test_token_oauth_mcp_isolation.py` exercises RFC 8693 Token Exchange.
- Required new test coverage:
  - A new test file (e.g. `tests/test_oauth_token_exchange.py` or addition to `tests/test_token_oauth_mcp_isolation.py`) must test:
    1. Member session token exchange: verifies issued access token and refresh token have `account_id == 2`.
    2. Admin session token exchange: verifies issued access token has `account_id == 1`.
    3. Unbound session token (`account_id = NULL`): verifies HTTP 400 `invalid_grant` and confirms no admin token is issued.
    4. Suspended account token: verifies HTTP 400 `invalid_grant`.
    5. Invalid or expired token: verifies HTTP 400 `invalid_grant`.
    6. Missing subject token: verifies HTTP 400 `invalid_request`.

---

## 3. Requirement R5: Syndication Prototype Sealing and Cleanup

### 3.1 Catalog of Prototype Components

The syndication prototype consists of these artifacts:

| Category | File Path | Purpose |
|---|---|---|
| Route Handler | `src/claire/api/server.py` (lines 1915-1934, 4633) | `Route("/prototype/syndication", prototype_syndication_ui_route)` |
| Security Policy | `src/claire/api/security.py` (lines 373-374) | Maps `("GET", "/prototype/syndication")` and `HEAD` to `_system("read")` |
| Route Surface | `tests/test_route_surface_contract.py` (line 54) | Enforces `/prototype/syndication` in `ROOT_PATHS` |
| UI Template | `src/claire/templates/prototype_syndication.html` (330 lines) | HTML template for the prototype interface |
| Live Navigation Link | `src/claire/templates/syndication.html` (line 87) | Link to prototype from live workspace popover menu |
| HTML Bundler | `src/claire/graphview.py` (lines 111-133, 856-879) | Bundles CSS and JS into `PROTOTYPE_SYNDICATION_HTML` |
| Prototype Seed Data | `src/claire/static/js/syndication_data.js` (923 lines) | Contains `window.CLAIRE_SYND_SEEDS` and templates |
| Mock Client Factory | `src/claire/static/js/syndication_client.js` (1166 lines) | ActivityPub and AT Protocol client mock simulation |
| Mock Peer Factory | `src/claire/static/js/syndication_peers.js` (334 lines) | Peer publication and reshare mock simulation |
| Prototype App | `src/claire/static/js/syndication_app.js` (1247 lines) | Client logic connecting seeds to prototype DOM |

### 3.2 Existing Automated Tests That Cover the Prototype

The following existing tests verify the syndication prototype:

1. `tests/test_syndication_route.py`:
   - `test_prototype_syndication_route_preserves_full_prototype` (lines 115-165):
     - Asserts HTTP 200 on `GET /prototype/syndication` and `HEAD /prototype/syndication`.
     - Asserts presence of strings:
       `"Claire Bible — Syndication Prototype"`, `'href="/syndication"'`, `"CLAIRE_SYND_CLIENT_FACTORY"`,
       `"CLAIRE_SYND_PEERS_FACTORY"`, `'id="app-tablist"'`, `'id="panel-client"'`, `'id="panel-kb"'`,
       `"CLAIRE_SYND_AP_FEATURES"`, `"accountSupports"`, `'missingFeatures: ["trends", "quote", ...]'`,
       `"/api/v2/notifications"`, `"/api/v1/statuses"`, `"com.atproto.repo.createRecord"`,
       `'id="tree-peers"'`, `'id="modal-reshare"'`, `"GET /api/peers/publications"`, etc.
   - `test_syndication_and_preferences_routes_io_interface` (lines 83, 86-89):
     - Asserts live `/syndication` contains link `'href="/prototype/syndication"'`.
     - Asserts live `/syndication` does NOT contain `"CLAIRE_SYND_SEEDS"`, `"CLAIRE_SYND_CLIENT_FACTORY"`, or `"CLAIRE_SYND_PEERS_FACTORY"`.
2. `e2e/test_syndication_e2e.py` (lines 71-84):
   - Asserts `'href="/prototype/syndication"' in r_pub.text`.
   - Asserts `client.get("/prototype/syndication")` returns HTTP 200.
   - Asserts presence of prototype elements (`data-app-tab="client"`, `'id="tab-client"'`, `'id="tab-kb"'`).
3. `tests/test_e2e_syndication_namespace.py` (lines 270-293):
   - Asserts `/prototype/syndication` is a root-mounted system route.
   - Asserts `client.get("/prototype/syndication")` requires authentication (returns 401 or 404).
   - Asserts `client.get("/@alice/prototype/syndication")` returns 404 (closed to namespace prefix).
4. `tests/test_api_security.py` (line 321):
   - Asserts `"/prototype/syndication"` is in `read_get`.
5. `tests/test_route_surface_contract.py` (line 54):
   - Asserts `"/prototype/syndication"` is in `ROOT_PATHS`.

### 3.3 Assessment of Approaches: Seal vs. Hard Delete

#### Option 1: Hard Deletion
- Description: Delete `/prototype/syndication`, `prototype_syndication.html`, and `syndication_{data,client,peers,app}.js`.
- Consequence:
  - Breaks all five test suites listed above.
  - Requires rewrite of route surface contracts, security permission tables, and e2e tests.
  - Violates the constraint in `ORIGINAL_REQUEST.md`: "without breaking existing tests".

#### Option 2: Clean Sealing (Recommended)
- Description:
  - Keep `/prototype/syndication` intact as a sealed, self-contained reference prototype.
  - Live `/syndication` already excludes prototype seed data and mock factories (`syndication.html` uses only `syndication_live.js`).
  - Add explicit sealing indicator (for example, metadata `<meta name="claire-prototype-sealed" content="true"/>` and visual badge in `prototype_syndication.html`).
  - Ensure the prototype route remains strictly guarded by authentication (`_system("read")`).
  - Ensure no prototype mock code or seed data interacts with real account stores or upcoming Fedify sidecar endpoints.
  - Zero test changes required; 100% test compatibility maintained.

---

## 4. Verification Method

To verify the findings and any future implementation:
1. Run syndication test suite:
   ```bash
   ./.venv/bin/pytest tests/test_syndication_route.py e2e/test_syndication_e2e.py tests/test_e2e_syndication_namespace.py -v
   ```
2. Run OAuth test suite:
   ```bash
   ./.venv/bin/pytest tests/test_oauth_flow.py tests/test_token_oauth_mcp_isolation.py -v
   ```
3. Run full test suite:
   ```bash
   ./.venv/bin/pytest -q
   ```
4. Verify token exchange account binding:
   - Create a session token without account ID.
   - Submit `POST /oauth/token` with `grant_type=urn:ietf:params:oauth:grant-type:token-exchange`.
   - Confirm response is HTTP 400 with `error="invalid_grant"`.
   - Confirm `SELECT COUNT(*) FROM oauth_tokens WHERE account_id=1` did not increase.
