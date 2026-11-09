# Handoff Report: Requirements R3 and R5 Exploration

Report Type: Hard (Task complete)
Author: Survey Explorer 2
Working Directory: /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_explorer_survey_2
Target Recipient: Orchestrator (466a086f-7210-4e3e-9220-ce86cf072b6c)

## 1. Observation

### 1.1 Requirement R3: Token Exchange Account Binding in `src/claire/api/oauth.py`
- In `src/claire/api/oauth.py` (lines 884-930), the handler for RFC 8693 Token Exchange (`urn:ietf:params:oauth:grant-type:token-exchange`) contains this code:
  ```python
  896:             exchange_account_id: int | None = None
  897:             binding = accounts_store.validate_session_binding(conn, subject_token)
  898:             if binding is not None:
  899:                 try:
  900:                     acc = accounts_store.AccountStore(conn).get(binding.account_id)
  901:                     if acc.status not in accounts_store.CREDENTIAL_ACCEPTING_STATUSES:
  902:                         return JSONResponse({"error": "invalid_grant", "error_description": f"Account is {acc.status}"}, status_code=400)
  903:                     exchange_account_id = binding.account_id
  904:                 except accounts_store.AccountNotFound:
  905:                     return JSONResponse({"error": "invalid_grant", "error_description": "Account not found"}, status_code=400)
  906:             else:
  907:                 # Check session or bootstrap
  908:                 exchanged = dbm.exchange_session_token(
  909:                     conn, subject_token, scopes=("owner", "collaborator", "readonly")
  910:                 )
  911:                 if not exchanged:
  912:                     # Also check if it's already an active session token
  913:                     v_scope = dbm.validate_session_scope(
  914:                         conn, subject_token, scopes=("owner", "collaborator", "readonly")
  915:                     )
  916:                     if not v_scope:
  917:                         return JSONResponse(
  918:                             {"error": "invalid_grant", "error_description": "Subject token is invalid or expired"},
  919:                             status_code=400,
  920:                         )
  921: 
  922:             # Issue isolated oauth token
  923:             access_token, refresh_token = dbm.create_oauth_token(
  924:                 conn, client_id="token_exchange_client", scope="readonly"
  925:             )
  926:             if exchange_account_id is not None:
  927:                 conn.execute("UPDATE oauth_tokens SET account_id=? WHERE token=?", (exchange_account_id, access_token))
  928:                 conn.commit()
  ```
- In `src/claire/store/db.py` (lines 3053-3058):
  ```python
  conn.execute(
      """INSERT INTO oauth_tokens
      (token, refresh_token, client_id, scope, created_at, expires_at, account_id)
      VALUES (?, ?, ?, ?, ?, ?, ?)""",
      (access_token, refresh_token, client_id, scope, now, now + access_ttl, ADMIN_ACCOUNT_ID),
  )
  ```
  `ADMIN_ACCOUNT_ID` is set to constant `1` (line 32 of `src/claire/store/db.py`).
- Tool command `grep_search` across `tests/` for `token-exchange` and `subject_token` returned 0 matches. No automated tests exist for RFC 8693 token exchange.

### 1.2 Requirement R5: Syndication Prototype Sealing and Cleanup
- Route `/prototype/syndication` is registered in `src/claire/api/server.py` at line 4633:
  ```python
  Route("/prototype/syndication", prototype_syndication_ui_route, methods=["GET", "HEAD"]),
  ```
- Access control in `src/claire/api/security.py` (lines 373-374):
  ```python
  ("GET", "/prototype/syndication"): _system("read"),
  ("HEAD", "/prototype/syndication"): _system("read"),
  ```
- Graphview bundler in `src/claire/graphview.py` (lines 111-133) builds `PROTOTYPE_SYNDICATION_HTML` by combining:
  - `src/claire/templates/prototype_syndication.html`
  - `src/claire/static/css/theme.css`
  - `src/claire/static/css/syndication.css`
  - `src/claire/static/js/syndication_data.js` (contains `window.CLAIRE_SYND_SEEDS`)
  - `src/claire/static/js/syndication_client.js` (contains `window.CLAIRE_SYND_CLIENT_FACTORY`)
  - `src/claire/static/js/syndication_peers.js` (contains `window.CLAIRE_SYND_PEERS_FACTORY`)
  - `src/claire/static/js/syndication_app.js`
- Menu link in `src/claire/templates/syndication.html` (line 87):
  ```html
  <a class="tt-pop-item" href="/prototype/syndication"><span>🧪 Syndication 프로토타입 보기 (/prototype/syndication)</span></a>
  ```
- Five existing test files assert the presence and exact responses of `/prototype/syndication`:
  1. `tests/test_syndication_route.py` (lines 83, 115-165)
  2. `e2e/test_syndication_e2e.py` (lines 71-84)
  3. `tests/test_e2e_syndication_namespace.py` (lines 270-293)
  4. `tests/test_api_security.py` (line 321)
  5. `tests/test_route_surface_contract.py` (line 54)
- Tool command `./.venv/bin/pytest tests/test_syndication_route.py e2e/test_syndication_e2e.py tests/test_e2e_syndication_namespace.py -v` passed all 27 tests in 4.90s.

---

## 2. Logic Chain

1. Observations in Section 1.1 show that line 896 sets `exchange_account_id = None`.
2. If `accounts_store.validate_session_binding` returns `None`, line 906 runs the `else:` branch.
3. In lines 908-920, `dbm.exchange_session_token` or `dbm.validate_session_scope` validates session presence without checking `account_id`.
4. If either legacy function succeeds, `exchange_account_id` remains `None`.
5. Observation 1.1 shows that `dbm.create_oauth_token` executes `INSERT INTO oauth_tokens (...) VALUES (..., ADMIN_ACCOUNT_ID)`.
6. Observation 1.1 shows that line 926 checks `if exchange_account_id is not None:`. Because `exchange_account_id` is `None`, this check evaluates to `False`.
7. Therefore, the update to `oauth_tokens` does not execute. The token record retains `account_id = 1`. An unbound or legacy session token gains admin credentials.
8. To stop this fallback, the `else:` branch in lines 906-920 must be removed. `validate_session_binding` must be strictly required. If binding fails, the handler must return HTTP 400 `invalid_grant`.
9. Observations in Section 1.2 show that five test suites check the `/prototype/syndication` route and its mock factories.
10. If an agent deletes `/prototype/syndication` or its static seed files, all five test suites will fail.
11. Therefore, sealing the prototype as an isolated reference component satisfies Requirement R5 and preserves all test assertions.

---

## 3. Caveats

1. This investigation is strictly read-only. No source files were changed.
2. Background task `task-145` ran the entire repository test suite. Individual target tests for syndication and OAuth passed 100%.

---

## 4. Conclusion

1. Requirement R3 (Token Exchange Account Binding Integrity):
   - Modify `src/claire/api/oauth.py` lines 884-930:
     - Remove the `else:` branch (lines 906-920).
     - Require `binding is not None` and `binding.account_id is not None`.
     - Return HTTP 400 `invalid_grant` with error description `"Subject token is invalid, expired, or unbound"` if binding is missing.
     - Pass `account_id=exchange_account_id` to token generation and stop default fallback to `ADMIN_ACCOUNT_ID`.
   - Add new automated tests in `tests/test_oauth_token_exchange.py` to verify account binding integrity and rejection of unbound tokens.
2. Requirement R5 (Syndication Prototype Sealing and Cleanup):
   - Do NOT delete `/prototype/syndication` or its seed files.
   - Seal `/prototype/syndication` by keeping it an isolated, authenticated (`_system("read")`) reference prototype.
   - Ensure `syndication.html` continues to use only `syndication_live.js` without loading prototype seeds.
   - Preserve all existing tests to prevent regressions across the five test files.

---

## 5. Verification Method

To verify these conclusions independently:

1. Inspect OAuth token exchange code:
   ```bash
   sed -n '884,940p' src/claire/api/oauth.py
   ```
2. Verify existing syndication test suite passes:
   ```bash
   ./.venv/bin/pytest tests/test_syndication_route.py e2e/test_syndication_e2e.py tests/test_e2e_syndication_namespace.py -v
   ```
3. Verify existing OAuth test suite passes:
   ```bash
   ./.venv/bin/pytest tests/test_oauth_flow.py tests/test_token_oauth_mcp_isolation.py -v
   ```
4. Invalidation condition:
   - If deleting `/prototype/syndication` is attempted, the command in step 2 will fail immediately on `test_prototype_syndication_route_preserves_full_prototype` and `test_syndication_e2e_workflow`.
