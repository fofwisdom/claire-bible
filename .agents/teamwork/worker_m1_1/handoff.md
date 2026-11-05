# Handoff Report: Worker M1-1 (Lane P2-a)

## 1. Changed Files
- `src/claire/static/js/manage_panels.js` (created, 1347 lines): Shared client-side management controllers exporting `window.ClaireManagePanels = { renderProviderManager, renderLimitManager, renderThemeManager, renderTokenManager, ... }` and attaching each function to `window`.
- `src/claire/static/js/app.js`:
  - Added `let IS_ADMIN = false;` near line 595.
  - Updated `GET /whoami` handling to set `IS_ADMIN = d.is_admin === true;` on success and `IS_ADMIN = false;` on failure.
  - Updated `expireWriteAccess()` to reset `IS_ADMIN = false;`.
  - Updated `setAccessScope()` to display `#providermanagebtn` and `#limitmanagebtn` only when `canWrite() && IS_ADMIN`, and to control `#consolelinkbtn` (`🛠️ 관리자 콘솔` with `href="/admin"` when `IS_ADMIN`, or `⚙️ 내 설정` with `href="/settings"` when `canWrite() && !IS_ADMIN`).
  - Updated `openThemeManager()`, `openProviderManager()`, `openLimitManager()`, and `openTokenManager()` to delegate to `window.ClaireManagePanels.render*Manager(panel, opts)` while preserving standalone `GRAPH_HTML` compatibility (since `graphview.py` only inlines `app.js` in Phase P2).
- `src/claire/templates/index.html`:
  - Added `<a id="consolelinkbtn" class="sec" href="/settings" title="내 설정" aria-label="콘솔 바로가기" style="display:none"><span class="btn-icon">⚙️</span> <span class="btn-label">내 설정</span></a>` in `#manage-section .action-btn-row`.
  - Added `<script src=/static/js/manage_panels.js></script>` before `<script src=/static/js/app.js></script>`.

## 2. Verification Results
- `node --check src/claire/static/js/manage_panels.js`: Exit code 0.
- `node --check src/claire/static/js/app.js`: Exit code 0.
- `uv run pytest tests/test_graphview.py tests/test_graphview_runtime.py tests/test_theme_web.py tests/test_theme_collaborator.py tests/test_security_hotfixes.py tests/test_route_surface_contract.py tests/test_username_policy.py tests/test_single_mode_no_username_leak.py tests/test_console_routing.py`: `268 passed, 4 warnings in 26.18s` (Exit code 0).

## 3. Contract Notes for Lane P2-b
- `window.ClaireManagePanels` exposes `renderProviderManager(container, opts)`, `renderLimitManager(container, opts)`, `renderThemeManager(container, opts)`, and `renderTokenManager(container, opts)` directly on `window` and on `window.ClaireManagePanels`.
- Each `render*Manager(container, opts)` accepts a DOM element or selector string (`#...`, `.`) as `container` and hides the drawer close button when `opts.showClose === false` or `opts.showCloseButton === false` (or when neither `opts.onClose` nor `window.closeDrawer` is defined).
