# Handoff Report: Reader Marks & Test Investigation (HW-5)

## 1. Observation

### 1.1 Server Endpoint (`GET /document`)
- File: `src/claire/store/queries.py`, lines 221-246:
```python
def document_detail(
    conn: sqlite3.Connection,
    document_id: str,
    include_hidden: bool = True,
    theme_id: int | None = None,
) -> dict | None:
    row = dbm.get_document_row(conn, document_id, theme_id=theme_id)
    if row is None:
        return None
    if not include_hidden and bool(row["hidden"]):
        return None
    ents = dbm.document_entities(conn, document_id, theme_id=theme_id)
    nodes = [
        {
            "id": e.id,
            "label": e.name,
            "group": e.type,
            "observations": e.observations or [],
        }
        for e in ents
    ]
```
- In `src/claire/store/db.py`, line 2341, `Entity` parsing produces:
`aliases=json.loads(row["aliases"] or "[]")`
The `Entity` instance `e` holds `e.aliases` as `list[str]`.
- In `src/claire/store/queries.py`, lines 330-333:
```python
        "detail": dbm.get_document_detail(conn, document_id, theme_id=theme_id) or "",
        "detail_format": dbm.get_document_detail_format(conn, document_id, theme_id=theme_id),
        "detail_html": dbm.get_document_detail_html(conn, document_id, theme_id=theme_id) or "",
```
The fields `detail` and `detail_html` are read directly from `dbm` and remain unchanged.
- In `src/claire/api/server.py`, lines 2706-2731, `document_detail_route` calls `_document_detail(conn, document_id, ...)`. It returns the dictionary that `store.queries.document_detail()` creates.

### 1.2 Reader Text Rendering
- File: `src/claire/static/js/reader.js`, lines 846-850:
```javascript
  const body=document.getElementById('rbody'); body.innerHTML=h; body.scrollTop=0; body.scrollLeft=0;
  applyMathRendering(body);
  document.getElementById('reader').setAttribute('aria-busy','false');
  updateReaderRail();
  setupReaderAccessibleBlocks(body);
```
- `renderReader(dc)` writes all document HTML into `body.innerHTML`. It then executes `applyMathRendering(body)`.
- File: `src/claire/templates/index.html`, lines 448-454:
```html
        <div class="rbody-wrap">
          <div class="rbody" id="rbody" tabindex="0" role="region" aria-label="문서 본문">
            <p class="hint" style="padding:20px;text-align:center">왼쪽 목록에서 문서를 선택하면 본문이 표시됩니다.</p>
          </div>
          <aside class="cb-heading-rail" id="rrail" aria-label="문서 내비게이션 바">
```
- File: `src/claire/templates/share.html`, lines 38-40:
```html
<body class="shared-page"><!-- __SEMANTIC_FALLBACK__ --><div class="wrap" id="wrap"></div>
```
The share page does not contain `#rbody` and does not load `app.js` or `reader.js`.

### 1.3 Node Selection and Interaction
- File: `src/claire/static/js/app.js`, lines 1999-2025:
`loadNode(id)` executes:
`fetch(apiUrl('node?id='+encodeURIComponent(id)+'&docs=brief')).then(r=>r.json()).then(d=>{ renderPanel(d); ... });`
This operation issues an HTTP `GET` request only. It performs no `POST` request and causes no data mutation.

### 1.4 Test Infrastructure Baseline
- Pytest suite:
  - Command: `./.venv/bin/pytest -q`
  - Result: 3266 passed, 12 skipped, 7 warnings, 6 subtests passed in 340.75s (0:05:40).
  - Command: `./.venv/bin/pytest -q tests/test_graphview.py`
  - Result: 52 passed in 2.64s.
- Playwright E2E suite:
  - Command: `cd e2e && npx playwright test -g "home overview knowledge map lists"`
  - Result: 1 passed in 9.6 seconds.
  - Command: `cd e2e && npx playwright test -g "document context panel renders Links, Entities, and Source tabs"`
  - Result: 1 passed in 14.5 seconds.
  - Root directory execution requires `cd e2e` or `-c e2e/playwright.config.js` because `package.json` and `playwright.config.js` reside inside the `e2e` subdirectory.
- Seed database:
  - File: `e2e/seed_db.py`
  - Pre-seeds documents: `doc-1`, `doc-2`, `doc-3`, `doc-page-1` through `doc-page-40`.
  - Pre-seeds entities: `ent-1` (name: "엔티티 A", aliases: `["별칭 A1", "별칭 A2"]`), `ent-2` (name: "엔티티 B"), `ent-3` (name: "엔티티 C").
  - Pre-seeds share token `23456789abcdefgh` for `doc-1`.

---

## 2. Logic Chain

1. **Server Alias Exposure**:
   - Observation 1.1 shows that `document_detail()` constructs `nodes` from `ents`, but does not include `aliases`.
   - Observation 1.1 also shows that each `Entity` instance `e` contains `e.aliases`.
   - Therefore, adding `"aliases": e.aliases or []` to the node dictionary in `document_detail()` delivers `nodes[].aliases` on `GET /document`. The `detail` and `detail_html` properties remain untouched.

2. **DOM Preservation and T1 Compliance**:
   - Observation 1.2 shows that `renderReader(dc)` sets `body.innerHTML = h` and calls `applyMathRendering(body)`.
   - CSS Custom Highlight API (`new Highlight(...ranges)` and `CSS.highlights.set('claire-entity', highlight)`) creates highlighting layers through browser rendering mechanisms without altering DOM elements or text nodes.
   - Therefore, `textContent` and `innerHTML` of `#rbody` remain strictly equal whether highlights are enabled or disabled via `claireEntityMarks`.
   - A dedicated legend container (`#rlegend`) positioned outside `#rbody` inside `#reader .sheet` ensures legend text updates do not alter `#rbody`.

3. **Click Interaction and T2 Compliance**:
   - Observation 1.3 confirms that `loadNode(id)` sends `GET /node?id=...&docs=brief` and executes no mutations.
   - A click event listener on `#rbody` resolves the click position using `document.caretPositionFromPoint` or `document.caretRangeFromPoint`.
   - If the resolved character offset falls inside an active entity range, the handler calls `loadNode(matchedEntityId)`.
   - When text is selected (`window.getSelection()`), the click handler aborts to permit standard copying.
   - Therefore, clicking an entity mark triggers only `GET /node` with zero POST requests.

4. **Share Page Isolation and T3 Compliance**:
   - Observation 1.2 shows that `src/claire/templates/share.html` uses `#wrap` and does not include `app.js` or `reader.js`.
   - Highlight registration code runs exclusively in the reader view of the main workspace.
   - Therefore, `CSS.highlights` contains no `claire-entity` highlights on `/p?s=...`.

5. **Match Engine Rule Enforcement (T4)**:
   - Candidates are collected only from `dc.nodes` of the active document.
   - If total text length exceeds 400,000 characters or candidates exceed 300 names, highlighting stops and the legend indicates the limit condition.
   - Collision suppression: If a candidate string belongs to more than one entity in `dc.nodes`, that string is excluded from highlighting.
   - Short candidate rule: Names with fewer than 2 characters are dropped. Names with 2 or 3 ASCII characters require exact case match (`Go`, `AI`, `MCP`). All other names match with case insensitivity via `toLowerCase()`.
   - Word boundaries: If a candidate begins with an ASCII letter or digit, the preceding character in text must not be an ASCII letter or digit. If it ends with an ASCII letter or digit, the subsequent character must not be an ASCII letter or digit (Korean particles such as `에서` are allowed).
   - Longest match first: Candidates are sorted by string length descending. Overlapping matches are discarded.
   - Traversal scope: A `TreeWalker` processes text nodes contained in `p`, `li`, `td`, `th` inside `#rbody .doc-content`, skipping `h1-h6`, `code`, `pre`, `a`, and `.katex`.

---

## 3. Caveats

1. **CSS Highlight Cursor Support**:
   - The CSS specification does not support the `cursor` property inside `::highlight()`.
   - To provide interactive affordance, a `mousemove` event handler on `#rbody` evaluates the cursor coordinate against active highlight ranges and sets `body.style.cursor = 'pointer'` when hovering over a mark.

2. **Caret Position API Compatibility**:
   - Modern Chromium and Firefox support `document.caretPositionFromPoint()`, while WebKit and older Chromium support `document.caretRangeFromPoint()`.
   - The implementation must test `document.caretPositionFromPoint` first and fall back to `document.caretRangeFromPoint`.

3. **Fallback Policy**:
   - In accordance with design constraint R2 and Section 8.4 rule 11, browsers lacking `CSS.highlights` support must not use DOM-wrapping fallbacks (`<mark>` or `<span>`). In unsupported browsers, marks remain absent and `#rlegend` displays an explanatory message.

---

## 4. Conclusion

### 4.1 Target Files
1. `src/claire/store/queries.py` (Add `"aliases": e.aliases or []` in `document_detail`)
2. `src/claire/static/js/reader.js` (Implement `applyEntityMarks`, range builder, click and hover listeners)
3. `src/claire/static/css/reader.css` (Add `::highlight(claire-entity)`, `::highlight(claire-entity-hover)`, and `.rlegend` rules)
4. `src/claire/templates/index.html` (Add `<div id="rlegend" class="rlegend" aria-live="polite"></div>` under `rbody-wrap`)
5. `src/claire/static/locales/en.json` and `src/claire/static/locales/ko.json` (Add catalog keys for legend and limit messages)
6. `docs/implementation/API_HTTP_CONTRACT.md` (Update `GET /document` row)
7. `docs/implementation/openapi.yaml` and `src/claire/static/docs/openapi.yaml` (Add `aliases` array to `GET /document` schema)
8. `docs/implementation/IMPLEMENTATION_STATUS.md` (Add HW-5 code locations and limits in Section 1)
9. `docs/implementation/GLOSSARY.md` (Add entry for entity mark)
10. `tests/test_graphview.py` (Add unit test for `nodes[].aliases` and `detail` preservation)
11. `e2e/workspace.spec.js` (Add E2E tests for T1, T2, T3, and T4 match cases)

### 4.2 Proposed Code Modifications

#### A. `src/claire/store/queries.py`
```python
# Before (line 237)
    nodes = [
        {
            "id": e.id,
            "label": e.name,
            "group": e.type,
            "observations": e.observations or [],
        }
        for e in ents
    ]

# After
    nodes = [
        {
            "id": e.id,
            "label": e.name,
            "group": e.type,
            "observations": e.observations or [],
            "aliases": e.aliases or [],
        }
        for e in ents
    ]
```

#### B. `src/claire/static/css/reader.css`
```css
::highlight(claire-entity) {
  text-decoration: underline dotted var(--muted);
  text-underline-offset: 3px;
}
::highlight(claire-entity-hover) {
  text-decoration: underline solid var(--accent);
  text-underline-offset: 3px;
  background-color: rgba(0, 255, 170, 0.12);
}
.rlegend {
  font-size: 11px;
  color: var(--muted);
  padding: 4px 14px;
  border-top: 1px solid var(--border);
  line-height: 1.4;
  display: none;
}
.rlegend.visible {
  display: block;
}
```

#### C. `src/claire/static/js/reader.js`
In `renderReader(dc)` immediately following `applyMathRendering(body)`:
```javascript
  if(typeof applyEntityMarks === 'function'){
    applyEntityMarks(dc);
  }
```

---

## 5. Verification Method

### 5.1 Unit Tests
- Execute command:
  `./.venv/bin/pytest -q tests/test_graphview.py -k test_document_detail`
- Test criteria:
  - `document_detail(conn, doc_id)["nodes"][0]["aliases"]` is a list matching entity aliases.
  - `document_detail(conn, doc_id)["detail"]` and `document_detail(conn, doc_id)["detail_html"]` are unchanged.

### 5.2 Playwright E2E Tests
- Execute command:
  `cd e2e && npx playwright test workspace.spec.js -g "entity marks"`
- Test criteria:
  - **T1**: Verify `textContent` and `innerHTML` of `#rbody` before and after setting `localStorage.setItem('claireEntityMarks', '0')` are strictly identical.
  - **T2**: Click marked entity text, verify `GET /node?id=...` request is made, and verify zero `POST` requests are initiated.
  - **T3**: Open `/p?s=23456789abcdefgh`, verify `CSS.highlights.has('claire-entity')` is false or highlight size is 0.
  - **T4**: Verify table of match cases (case sensitivity for 2-3 ASCII characters, case insensitivity for >= 4 characters, boundaries, Korean particle attachment, overlap suppression, collision suppression, and limits).

### 5.3 Invalidation Conditions
- Any change that alters `#rbody.innerHTML` or `#rbody.textContent` between marks on and off invalidates T1.
- Any request with method `POST` triggered by clicking marked text invalidates T2.
- Any presence of `claire-entity` highlight on the share page invalidates T3.
