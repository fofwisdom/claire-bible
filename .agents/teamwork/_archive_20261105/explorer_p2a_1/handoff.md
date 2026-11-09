# Handoff Report: Lane P2-a Codebase Investigation

## 1. Observation

### 1.1 Existing Implementation in `src/claire/static/js/app.js`

1. **Authentication Scope and Privilege Variables**:
   - At line 595:
     ```javascript
     let AUTH_SCOPE='unknown'; let READONLY=true;
     ```
   - At line 626:
     ```javascript
     function canWrite(){ return AUTH_SCOPE==='owner'; }
     ```
   - `IS_ADMIN` does not exist in `app.js`.

2. **Access Scope Configuration and Button Controls**:
   - Lines 4505–4542 contain `setAccessScope` and `expireWriteAccess`:
     ```javascript
     function setAccessScope(scope, reason){
       AUTH_SCOPE = ['owner','collaborator','readonly','anonymous'].includes(scope) ? scope : 'unknown';
       READONLY = !canWrite();
       document.body.dataset.authScope=AUTH_SCOPE;
       document.body.classList.toggle('ro', READONLY);
       const label=document.getElementById('authstate');
       if(label) label.textContent =
         AUTH_SCOPE==='owner' ? '🔒 인증됨' :
         AUTH_SCOPE==='collaborator' ? '🤝 협업자' :
         AUTH_SCOPE==='readonly' ? '👁️ 읽기전용' :
         AUTH_SCOPE==='anonymous' ? '👁️ 익명 읽기전용' :
         reason==='expired' ? '🔓 쓰기 세션 만료 — /web 재접속' : '⚠️ 권한 확인 실패';
       const tmBtn = document.getElementById('thememanagebtn');
       const canAccessThemes = (canWrite() || AUTH_SCOPE === 'collaborator') && isMultiThemeEnabled();
       if(tmBtn) tmBtn.style.display = canAccessThemes ? '' : 'none';
       const pmBtn = document.getElementById('providermanagebtn');
       if(pmBtn) pmBtn.style.display = canWrite() ? '' : 'none';
       const lmBtn = document.getElementById('limitmanagebtn');
       if(lmBtn) lmBtn.style.display = canWrite() ? '' : 'none';
       const kmBtn = document.getElementById('tokenmanagebtn');
       if(kmBtn) kmBtn.style.display = canWrite() ? '' : 'none';
       const mgSec = document.getElementById('manage-section');
       if(mgSec) mgSec.style.display = (canAccessThemes || canWrite()) ? '' : 'none';
       if(!canWrite()){
         synthSet.clear();
         showHidden=false;
         renderChips();
         panel.innerHTML=defaultHint();
       }
       updateSearchModeUI();
       renderDocs();
       if(selectedNodeId) loadNode(selectedNodeId);
       else if(activeDoc) loadDocPanel(activeDoc);
       else panel.innerHTML=defaultHint();
     }
     function expireWriteAccess(){ setAccessScope('unknown','expired'); }
     ```
   - Currently, `#providermanagebtn` and `#limitmanagebtn` check only `canWrite()`. They do not check administrator privileges.

3. **`GET /whoami` Processing**:
   - Lines 6003–6005 contain the `/whoami` fetch call:
     ```javascript
     fetch(rootUrl('whoami')).then(r=>{ if(!r.ok) throw new Error('whoami failed'); return r.json(); }).then(d=>{
       setAccessScope(d.scope);
     }).catch(()=>{ setAccessScope('unknown','failed'); });
     ```
   - The response from `GET /whoami` includes `"is_admin"` boolean (added in Phase P1).
   - `app.js` ignores `d.is_admin`.

4. **Management Panel Functions in `src/claire/static/js/app.js`**:
   - Theme Manager: Lines 4544–4897 (354 lines):
     - Functions: `openThemeManager`, `toggleThemeOptionsView`, `showThemeEditForm`, `hideThemeEditForm`, `updateThemeFromUI`, `toggleThemeVisibility`, `toggleThemeCollaborator`, `toggleThemePresentation`, `createThemeFromUI`, `deleteThemeFromUI`.
     - Endpoints: `GET/POST/PATCH/DELETE apiUrl('themes')`.
   - Provider Manager: Lines 4899–5277 (379 lines):
     - Functions: `openProviderManager`, `renderProviderManager`, `togglePassVisibility`, `clearApiKeyInput`, `testProviderConnection`, `saveProviderSettings`.
     - Variable: `_provConfig`.
     - Endpoints: `GET/PATCH rootUrl('providers')`, `POST rootUrl('providers/test')`.
   - Limit Manager: Lines 5279–5545 (267 lines):
     - Functions: `openLimitManager`, `renderLimitManager`, `saveLimitSettings`, `resetLimitSettings`.
     - Variable: `_limitConfig`.
     - Endpoints: `GET/PATCH rootUrl('limits')`.
   - Token Manager: Lines 5547–5749 (203 lines):
     - Functions: `openTokenManager`, `renderTokenManager`, `createApiToken`, `revokeApiToken`.
     - Variable: `_tokenList`.
     - Endpoints: `GET/POST rootUrl('tokens')`, `POST rootUrl('tokens/revoke')`.

### 1.2 Existing Markup in `src/claire/templates/index.html`

1. **`#manage-section` Markup (lines 412–422)**:
   ```html
   <div id="manage-section" class="menu-section" aria-label="관리 및 설정" style="display:none">
     <div class="menu-section-head">
       <span class="menu-section-title">관리 및 설정</span>
     </div>
     <div class="action-btn-row">
       <button id="thememanagebtn" class="sec" onclick="openThemeManager()" title="지식 테마 정의 및 공개 여부 관리" aria-label="테마관리" style="display:none"><span class="btn-icon">📁</span> <span class="btn-label">테마 관리</span></button>
       <button id="providermanagebtn" class="sec" onclick="openProviderManager()" title="LLM 및 하이퍼스케일러 프로바이더 설정" aria-label="프로바이더 관리" style="display:none"><span class="btn-icon">⚡</span> <span class="btn-label">프로바이더</span></button>
       <button id="limitmanagebtn" class="sec" onclick="openLimitManager()" title="상황 별 적재 제한 및 슬라이싱 예산 설정" aria-label="적재 제한 관리" style="display:none"><span class="btn-icon">📏</span> <span class="btn-label">적재 제한</span></button>
       <button id="tokenmanagebtn" class="sec" onclick="openTokenManager()" title="API 및 MCP 상시 이용 토큰 관리" aria-label="API/MCP 토큰 관리" style="display:none"><span class="btn-icon">🔑</span> <span class="btn-label">API/MCP 키</span></button>
     </div>
   </div>
   ```
2. **Script Inclusions (lines 445–452)**:
   ```html
   <script src=/static/js/graph/core.js></script>
   <script src=/static/js/graph/simulation.js></script>
   <script src=/static/js/graph/renderers.js></script>
   <script src=/static/js/graph/network.js></script>
   <script src=/static/js/renderers/adoc_parser.js></script>
   <script src=/static/js/reader.js></script>
   <script src=/static/js/app.js></script>
   ```

### 1.3 Baseline Test Status

- Command:
  ```bash
  uv run pytest tests/test_account_routes.py tests/test_oidc_flow.py tests/test_console_routing.py tests/test_single_mode_no_username_leak.py
  ```
- Result: 57 passed in 12.96 seconds (exit code 0).

---

## 2. Logic Chain

1. **Premise 1**: Phase P1 added `"is_admin"` boolean to `GET /whoami` in `src/claire/api/server.py`.
   - Evidence: `tests/test_console_routing.py` verifies that `whoami` returns `is_admin: true` only for admin owner principals.
   - Inference: `src/claire/static/js/app.js` can track `let IS_ADMIN = false;` and set `IS_ADMIN = d.is_admin === true;` directly from `fetch(rootUrl('whoami'))`.

2. **Premise 2**: Decision D2 requires hiding `⚡ 프로바이더` and `📏 적재 제한` from non-admin members on the service root `/`.
   - Evidence: In `setAccessScope`, `#providermanagebtn` and `#limitmanagebtn` currently check only `canWrite()`.
   - Inference: Changing the condition to `canWrite() && IS_ADMIN` immediately satisfies D2 without changing the backend API.

3. **Premise 3**: Decision D2 requires a console entry point `#consolelinkbtn` inside `#manage-section`.
   - Evidence: Administrators need quick access to `/admin` (`🛠️ 관리자 콘솔`), and regular owners need access to `/settings` (`⚙️ 내 설정`).
   - Inference: An anchor element `<a id="consolelinkbtn" ...>` inside `#manage-section` updated dynamically by `setAccessScope` gives correct labeling and routing.

4. **Premise 4**: Both `index.html` (drawer) and `console_shell.html` (full-page cards) need the management UI controllers.
   - Evidence: `app.js` currently embeds 1,206 lines for providers, limits, themes, and tokens. `console_admin.js` in Lane P2-b needs the same controllers for `/admin/providers`, `/admin/limits`, `/admin/themes`, and `/admin/tokens`.
   - Inference: Extracting these controllers into `src/claire/static/js/manage_panels.js` allows code reuse and prevents duplication.

5. **Premise 5**: Inline drawer actions in `index.html` must continue to work without regression.
   - Evidence: Existing buttons in `index.html` use `onclick="openProviderManager()"`, `onclick="openLimitManager()"`, `onclick="openThemeManager()"`, and `onclick="openTokenManager()"`.
   - Inference: Keeping thin wrapper functions in `app.js` that call `manage_panels.js` preserves full backward compatibility.

---

## 3. Caveats

1. **Subagent Boundaries**:
   - Lane P2-a owns:
     - `src/claire/static/js/manage_panels.js` (new file)
     - `src/claire/static/js/app.js` (hotspot file)
     - `src/claire/templates/index.html`
   - Lane P2-b owns:
     - `src/claire/templates/console_shell.html`
     - `src/claire/static/js/console_admin.js`
     - Backend admin route files
   - Lane P2-a must not edit files that Lane P2-b owns.

2. **Script Loading Order**:
   - `manage_panels.js` must load before `app.js` in `src/claire/templates/index.html`.
   - `manage_panels.js` must also load in `console_shell.html` (handled by Lane P2-b).

3. **No Direct DOM Assumptions in Standalone Mode**:
   - `app.js` uses a global element `#panel` and a function `closeDrawer()`.
   - In `console_shell.html`, views render in `#console-section-body` and have no drawer.
   - Reusable controllers in `manage_panels.js` must accept a target `container` element and an `opts` object.

---

## 4. Conclusion and Implementation Blueprint

### 4.1 Specification for `src/claire/static/js/manage_panels.js`

Export four functions on `window` (and namespace `window.ClaireManagePanels`):

```javascript
window.ClaireManagePanels = {
  renderProviderManager,
  renderLimitManager,
  renderThemeManager,
  renderTokenManager
};
```

#### 1. `renderProviderManager(container, opts)`
- **Signature**: `async function renderProviderManager(container, opts = {})`
- **Parameters**:
  - `container`: DOM Element where HTML renders.
  - `opts`:
    - `rootUrl`: `(path) => string` (default: `window.rootUrl` or standard prefix resolver).
    - `canWrite`: `() => boolean` or `boolean` (default: `true`).
    - `onClose`: `() => void` (optional callback for the close button; if omitted or `opts.showClose === false`, close button is omitted).
    - `showClose`: `boolean` (default: `Boolean(opts.onClose)`).
- **Behavior**:
  - Fetches `GET rootUrl('providers')`.
  - Renders provider options (Gemini, Antigravity CLI, Codex CLI, OpenAI, Jev, STT).
  - Handles connection tests via `POST rootUrl('providers/test')`.
  - Saves configuration via `PATCH rootUrl('providers')`.
  - Supports API key toggles and clearing.

#### 2. `renderLimitManager(container, opts)`
- **Signature**: `async function renderLimitManager(container, opts = {})`
- **Parameters**:
  - `container`: DOM Element.
  - `opts`:
    - `rootUrl`: `(path) => string`.
    - `canWrite`: `() => boolean` or `boolean`.
    - `onClose`: `() => void`.
    - `showClose`: `boolean`.
- **Behavior**:
  - Fetches `GET rootUrl('limits')`.
  - Renders input controls for raw text, PDF parsing, KG extraction, embedding budgets, and media limits.
  - Saves configuration via `PATCH rootUrl('limits')`.
  - Restores default settings via `PATCH rootUrl('limits')`.

#### 3. `renderThemeManager(container, opts)`
- **Signature**: `async function renderThemeManager(container, opts = {})`
- **Parameters**:
  - `container`: DOM Element.
  - `opts`:
    - `apiUrl`: `(path) => string` (default: `window.apiUrl`).
    - `isOwner` or `canWrite`: `() => boolean` or `boolean` (default: `true`).
    - `isCollaborator`: `() => boolean` or `boolean` (default: `false`).
    - `onClose`: `() => void`.
    - `showClose`: `boolean`.
    - `onThemeChange`: `(action, themeId) => Promise<void> | void` (callback when theme list changes).
    - `activeThemeId`: `string` or `number`.
    - `defaultSorcerer`: `string`.
- **Behavior**:
  - Fetches `GET apiUrl('themes')`.
  - Renders theme list with badges (public/private, collaborator access, presentation).
  - Allows owners to toggle flags, edit settings, create new themes, and delete themes.
  - Allows collaborators to inspect read-only options.

#### 4. `renderTokenManager(container, opts)`
- **Signature**: `async function renderTokenManager(container, opts = {})`
- **Parameters**:
  - `container`: DOM Element.
  - `opts`:
    - `rootUrl`: `(path) => string`.
    - `canWrite`: `() => boolean` or `boolean`.
    - `onClose`: `() => void`.
    - `showClose`: `boolean`.
    - `onTokenChange`: `(action, token) => void`.
- **Behavior**:
  - Fetches `GET rootUrl('tokens')`.
  - Renders token creation form (name, scope, expiration).
  - Displays newly created token with copy button and MCP configuration snippet.
  - Lists existing tokens with revocation action via `POST rootUrl('tokens/revoke')`.

### 4.2 Exact Changes to `src/claire/static/js/app.js`

1. **Declare `IS_ADMIN`** (near line 595):
   ```javascript
   let AUTH_SCOPE='unknown'; let READONLY=true;
   let IS_ADMIN=false;
   ```

2. **Update `GET /whoami` handling** (lines 6003–6005):
   ```javascript
   fetch(rootUrl('whoami')).then(r=>{ if(!r.ok) throw new Error('whoami failed'); return r.json(); }).then(d=>{
     IS_ADMIN = d.is_admin === true;
     setAccessScope(d.scope);
   }).catch(()=>{
     IS_ADMIN = false;
     setAccessScope('unknown','failed');
   });
   ```

3. **Update `expireWriteAccess`** (line 4542):
   ```javascript
   function expireWriteAccess(){
     IS_ADMIN = false;
     setAccessScope('unknown','expired');
   }
   ```

4. **Update `setAccessScope`** (lines 4517–4527):
   ```javascript
   const tmBtn = document.getElementById('thememanagebtn');
   const canAccessThemes = (canWrite() || AUTH_SCOPE === 'collaborator') && isMultiThemeEnabled();
   if(tmBtn) tmBtn.style.display = canAccessThemes ? '' : 'none';
   const pmBtn = document.getElementById('providermanagebtn');
   if(pmBtn) pmBtn.style.display = (canWrite() && IS_ADMIN) ? '' : 'none';
   const lmBtn = document.getElementById('limitmanagebtn');
   if(lmBtn) lmBtn.style.display = (canWrite() && IS_ADMIN) ? '' : 'none';
   const kmBtn = document.getElementById('tokenmanagebtn');
   if(kmBtn) kmBtn.style.display = canWrite() ? '' : 'none';
   const clBtn = document.getElementById('consolelinkbtn');
   if(clBtn){
     const showConsole = canWrite();
     clBtn.style.display = showConsole ? '' : 'none';
     if(showConsole){
       if(IS_ADMIN){
         clBtn.setAttribute('href', '/admin');
         clBtn.title = '관리자 콘솔';
         clBtn.innerHTML = '<span class="btn-icon">🛠️</span> <span class="btn-label">관리자 콘솔</span>';
       } else {
         clBtn.setAttribute('href', '/settings');
         clBtn.title = '내 설정';
         clBtn.innerHTML = '<span class="btn-icon">⚙️</span> <span class="btn-label">내 설정</span>';
       }
     }
   }
   const mgSec = document.getElementById('manage-section');
   if(mgSec) mgSec.style.display = (canAccessThemes || canWrite()) ? '' : 'none';
   ```

5. **Replace 1,206 Lines in `app.js` with Wrapper Functions**:
   Replace lines 4544–5749 with:
   ```javascript
   async function openThemeManager(){
     const isOwner = canWrite();
     const isCollab = AUTH_SCOPE === 'collaborator';
     if(!isOwner && !isCollab) return;
     openDetailPane();
     if(typeof renderThemeManager === 'function'){
       await renderThemeManager(panel, {
         apiUrl: apiUrl,
         rootUrl: rootUrl,
         isOwner: isOwner,
         isCollaborator: isCollab,
         activeThemeId: typeof activeThemeId !== 'undefined' ? activeThemeId : '0',
         defaultSorcerer: (window.__CLAIRE_CONFIG && window.__CLAIRE_CONFIG.sorcerer) || 'owner',
         onClose: closeDrawer,
         onThemeChange: async (action, themeId) => {
           if(typeof fetchThemes === 'function') await fetchThemes();
           if(action === 'delete' || action === 'switch'){
             if(typeof reloadThemeData === 'function') await reloadThemeData();
             if(typeof loadThemeDocuments === 'function') await loadThemeDocuments();
           }
         }
       });
     }
   }

   async function openProviderManager(){
     if(!canWrite() || !IS_ADMIN) return;
     openDetailPane();
     if(typeof renderProviderManager === 'function'){
       await renderProviderManager(panel, {
         rootUrl: rootUrl,
         apiUrl: apiUrl,
         canWrite: canWrite,
         onClose: closeDrawer
       });
     }
   }

   async function openLimitManager(){
     if(!canWrite() || !IS_ADMIN) return;
     openDetailPane();
     if(typeof renderLimitManager === 'function'){
       await renderLimitManager(panel, {
         rootUrl: rootUrl,
         apiUrl: apiUrl,
         canWrite: canWrite,
         onClose: closeDrawer
       });
     }
   }

   async function openTokenManager(msg, isSuccess, newCreatedToken){
     if(!canWrite()) return;
     openDetailPane();
     if(typeof renderTokenManager === 'function'){
       await renderTokenManager(panel, {
         rootUrl: rootUrl,
         apiUrl: apiUrl,
         canWrite: canWrite,
         onClose: closeDrawer,
         initialMsg: msg,
         initialSuccess: isSuccess,
         initialNewToken: newCreatedToken
       });
     }
   }
   ```

### 4.3 Exact Changes to `src/claire/templates/index.html`

1. **Insert `#consolelinkbtn` into `#manage-section`** (around line 416):
   ```html
   <div id="manage-section" class="menu-section" aria-label="관리 및 설정" style="display:none">
     <div class="menu-section-head">
       <span class="menu-section-title">관리 및 설정</span>
     </div>
     <div class="action-btn-row">
       <a id="consolelinkbtn" class="sec" href="/settings" title="내 설정" aria-label="콘솔 바로가기" style="display:none"><span class="btn-icon">⚙️</span> <span class="btn-label">내 설정</span></a>
       <button id="thememanagebtn" class="sec" onclick="openThemeManager()" title="지식 테마 정의 및 공개 여부 관리" aria-label="테마관리" style="display:none"><span class="btn-icon">📁</span> <span class="btn-label">테마 관리</span></button>
       <button id="providermanagebtn" class="sec" onclick="openProviderManager()" title="LLM 및 하이퍼스케일러 프로바이더 설정" aria-label="프로바이더 관리" style="display:none"><span class="btn-icon">⚡</span> <span class="btn-label">프로바이더</span></button>
       <button id="limitmanagebtn" class="sec" onclick="openLimitManager()" title="상황 별 적재 제한 및 슬라이싱 예산 설정" aria-label="적재 제한 관리" style="display:none"><span class="btn-icon">📏</span> <span class="btn-label">적재 제한</span></button>
       <button id="tokenmanagebtn" class="sec" onclick="openTokenManager()" title="API 및 MCP 상시 이용 토큰 관리" aria-label="API/MCP 토큰 관리" style="display:none"><span class="btn-icon">🔑</span> <span class="btn-label">API/MCP 키</span></button>
     </div>
   </div>
   ```

2. **Include `manage_panels.js` script tag** (around line 450):
   ```html
   <script src=/static/js/reader.js></script>
   <script src=/static/js/manage_panels.js></script>
   <script src=/static/js/app.js></script>
   ```

---

## 5. Verification Method

### 5.1 Verification Commands

1. **Run Route Surface and Console Test Suite**:
   ```bash
   uv run pytest tests/test_route_surface_contract.py tests/test_username_policy.py tests/test_single_mode_no_username_leak.py tests/test_console_routing.py tests/test_account_routes.py
   ```
   - Criteria: All tests pass with exit code 0.

2. **Inspect Template Tag Matching and Formatting**:
   ```bash
   uv run pytest tests/test_graphview_runtime.py -k test_html_tag_structure_integrity
   ```
   - Criteria: Tag balance checks in `index.html` pass.

3. **Verify Script Syntax of Extracted Files**:
   ```bash
   node --check src/claire/static/js/manage_panels.js
   node --check src/claire/static/js/app.js
   ```
   - Criteria: Node syntax check passes with 0 syntax errors.

4. **Verify Single Line Wrap Discipline**:
   ```bash
   python3 -c "
   for p in ['src/claire/static/js/manage_panels.js', 'src/claire/static/js/app.js', 'src/claire/templates/index.html']:
       with open(p, 'r', encoding='utf-8') as f:
           lines = f.readlines()
       print(f'{p}: {len(lines)} lines')
   "
   ```

### 5.2 Invalidation Conditions
- Any occurrence of hard line wrapping in code lines.
- `#providermanagebtn` or `#limitmanagebtn` remaining visible when `IS_ADMIN` is false.
- `#consolelinkbtn` linking to `/admin` when `IS_ADMIN` is false.
- Syntax or runtime reference errors when opening drawer panels from `index.html`.
