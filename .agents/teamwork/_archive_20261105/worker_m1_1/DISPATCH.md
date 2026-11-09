# Dispatch: Worker M1-1 (Lane P2-a)

## Identity
- Role: Implementation Worker (teamwork_worker)
- Working Directory: /home/fow/Projects/claire-bible/.agents/teamwork/worker_m1_1

## Mandatory Instruction
Read /home/fow/Projects/claire-bible/.agents/teamwork/ORIGINAL_REQUEST.md completely before you start work.
Follow user rules:
- Use ASD-STE100 English for all tasks and git commit messages (if any).
- Do not hard-wrap code lines in Python, JavaScript, HTML, or CSS files.
- Exclusive file ownership: You own and may edit ONLY:
  - `src/claire/static/js/manage_panels.js` (create new)
  - `src/claire/static/js/app.js` (hotspot file)
  - `src/claire/templates/index.html`
- Do NOT touch `src/claire/templates/console_shell.html` or any backend python files.

## Mandatory Integrity Warning
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

## Detailed Inputs
- Project specification: `/home/fow/Projects/claire-bible/PROJECT.md`
- Lane P2-a exploration handoff: `/home/fow/Projects/claire-bible/.agents/teamwork/explorer_p2a_1/handoff.md`
- Phase P2 specification handoff: `/home/fow/Projects/claire-bible/.agents/teamwork/spec_miner_p2_1/handoff.md`

## Tasks to Implement
1. **Extract `src/claire/static/js/manage_panels.js`**:
   - Provide `window.ClaireManagePanels = { renderProviderManager, renderLimitManager, renderThemeManager, renderTokenManager };` and attach each function to `window`.
   - Implement `renderProviderManager(container, opts)` with support for testing connections (`POST /providers/test`), saving settings (`PATCH /providers`), password visibility toggling, and close callbacks.
   - Implement `renderLimitManager(container, opts)` with support for saving settings (`PATCH /limits`), resetting defaults, and close callbacks.
   - Implement `renderThemeManager(container, opts)` with support for owner options, collaborator read view, theme edit form, theme creation form, visibility/collaborator/presentation toggles, and deletion.
   - Implement `renderTokenManager(container, opts)` with token creation (`POST /tokens`), one-time token display banner with copy button and JSON config snippet, existing token list, and revocation (`POST /tokens/revoke`).
2. **Update `src/claire/static/js/app.js`**:
   - Add `let IS_ADMIN = false;` near line 595.
   - In `fetch(rootUrl('whoami'))` (lines 6003–6005), set `IS_ADMIN = d.is_admin === true;`. On error, reset `IS_ADMIN = false;`.
   - In `expireWriteAccess()`, reset `IS_ADMIN = false;`.
   - In `setAccessScope()` (lines 4505–4541):
     - Display `#providermanagebtn` and `#limitmanagebtn` ONLY when `canWrite() && IS_ADMIN`.
     - Display `#thememanagebtn` when `(canWrite() || AUTH_SCOPE === 'collaborator') && isMultiThemeEnabled()`.
     - Display `#tokenmanagebtn` when `canWrite()`.
     - Control `#consolelinkbtn`: display when `canWrite()`. If `IS_ADMIN`, label `🛠️ 관리자 콘솔` with `href="/admin"`. If `canWrite() && !IS_ADMIN`, label `⚙️ 내 설정` with `href="/settings"`.
   - Replace the ~1,200 lines of theme/provider/limit/token management implementations with clean delegating wrapper functions `openThemeManager()`, `openProviderManager()`, `openLimitManager()`, and `openTokenManager()` calling `render*Manager(panel, opts)`.
3. **Update `src/claire/templates/index.html`**:
   - In `#manage-section`, add `<a id="consolelinkbtn" class="sec" href="/settings" title="내 설정" aria-label="콘솔 바로가기" style="display:none"><span class="btn-icon">⚙️</span> <span class="btn-label">내 설정</span></a>` as the first item in `.action-btn-row`.
   - Include `<script src=/static/js/manage_panels.js></script>` before `<script src=/static/js/app.js></script>`.

## Verification Commands
Worker MUST execute and report passing results:
1. `node --check src/claire/static/js/manage_panels.js`
2. `node --check src/claire/static/js/app.js`
3. `uv run pytest tests/test_route_surface_contract.py tests/test_username_policy.py tests/test_single_mode_no_username_leak.py tests/test_console_routing.py tests/test_account_routes.py`
4. Verify no hard-wrapped code lines.

Write `handoff.md` to `/home/fow/Projects/claire-bible/.agents/teamwork/worker_m1_1/handoff.md` and notify parent via `send_message`.


## 2026-11-05T05:13:11Z
Read /home/fow/Projects/claire-bible/.agents/teamwork/ORIGINAL_REQUEST.md completely.
Your working directory is /home/fow/Projects/claire-bible/.agents/teamwork/worker_m1_1.
Your task instructions and inputs are in /home/fow/Projects/claire-bible/.agents/teamwork/worker_m1_1/DISPATCH.md.
Implement Milestone 1 (Lane P2-a): extract src/claire/static/js/manage_panels.js, update src/claire/static/js/app.js, and update src/claire/templates/index.html.
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.
Do not hard-wrap code lines. Run verification commands and document passing results in handoff.md.
Send message to parent when complete.
