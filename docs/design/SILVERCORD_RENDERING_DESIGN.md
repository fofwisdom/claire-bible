# Silvercord Document Rendering: Pull-Out Local Graph and Opinions Switch (`SILVERCORD_RENDERING_DESIGN`)

- **Document Identifier**: `DESIGN-SILVERCORD-RENDER-20261124`
- **Status**: Implemented (S1–S3, 2026-11-24; `SQ1`–`SQ5` confirmed; current behavior in [`PERMALINKS.md`](../implementation/PERMALINKS.md) and [`WEBUI.md`](../implementation/WEBUI.md))
- **Date**: 2026-11-24
- **Visual prototype**: [`prototypes/silvercord_rendering_prototype.html`](prototypes/silvercord_rendering_prototype.html)
- **Related**: [`PERMALINK_UNIFICATION_DESIGN.md`](PERMALINK_UNIFICATION_DESIGN.md), [`HOME_OVERVIEW_AND_CONTEXT_PANEL_DESIGN.md`](HOME_OVERVIEW_AND_CONTEXT_PANEL_DESIGN.md), [`USER_HOME_ACTIVITYPUB_DESIGN.md`](USER_HOME_ACTIVITYPUB_DESIGN.md), [`PERMALINKS.md`](../implementation/PERMALINKS.md), [`WEBUI.md`](../implementation/WEBUI.md), [`PUBLIC_SITE.md`](../implementation/PUBLIC_SITE.md)

## 1. Owner request and decisions (2026-11-24)

Initial request:

> /silvercord/ 렌더링 변경 설계.
>
> 유저 홈에서 볼 수 있는 문서 전용 그래프를 silvercord에서도 꺼내볼 수 있도록 제공하고자 합니다. 서버에 로그인한 이용자가 아니라면 그래프와 상호작할 수 없습니다. 비로그인 접속자는 지금처럼 간결한 문서 형태를 유지합니다. 이용자는 모바일에서도 본문/의견을 빠르게 전환할 수 있는 UI가 제공되어야 합니다. '의견'은 아직 설계 도입하지 않은 구상이며 ActivityPub으로 소통하고자 합니다. 아직은 의견은 동작하지 않는 시각 프로토타입으로 채웁니다.

Owner refinement (`SQ1`–`SQ5` and clarifications `A1`–`A2`, 2026-11-24):

- **A1 (No discrimination against anonymous visitors)**: Anonymous visitors and signed-in users get the same Silvercord document view, `본문` / `의견` switching, and interactive Ring 1 + Ring 2 local graph (closed by default so the initial view stays compact).
- **A2 (Private link-access boundary)**: On a private knowledge base document shared via `link_access=1`, show the Ring 1 + Ring 2 graph and connecting edges with opaque IDs while blocking reading other unreadable documents.

Requirements:

- **SR1.** Provide the document-only local graph (`renderLocalGraphHtml` from the User Home / Workspace document context) as a pull-out interactive panel on document `/silvercord/{token}` pages.
- **SR2.** Enforce access boundaries by KB readability and `link_access`, not login discrimination (`SQ2: B`, `A1`). Keep the graph panel closed by default (`HD3`) so the initial document page stays compact and fast.
- **SR3.** On private KB `link_access=1` documents for non-members, expose the Ring 1 + Ring 2 graph with opaque IDs (`e0`, `e1`, ...) while strictly blocking other unreadable documents (`SQ4`, `A2`).
- **SR4.** Provide fast switching between Document (`본문`) and Opinions (`의견`) on desktop and mobile (via a fixed/sticky bottom bar on `< 820px`, `SQ3: B`) without losing reading scroll position.
- **SR5.** Fill Opinions (`의견`) with a clearly labeled, non-functional ActivityPub visual prototype until the ActivityPub discussion feature is designed ([`PRINCIPLES.md`](../PRINCIPLES.md) C1, C2, G1).

**Scope.** This design applies to `kind == "document"` on `GET /silvercord/{token}`. `kind == "note"` (`note_item.html`) has no document graph and stays unchanged now; the Opinions markup is structured for reuse on Note threads later. `/entity/{token}` stays unchanged except as a conditional drill-down destination.

## 2. Current state findings (`[code]`)

| Area | Current behavior | Gap for SR1–SR5 |
| :--- | :--- | :--- |
| Route policy (`src/claire/api/security.py:590`) | `GET /silvercord/{token}` is `_system("public", "none")`. | Needs a companion `/silvercord/{token}/graph` route and a `/silvercord/{token}/workspace` representation route (`U8`). |
| Member landing (`src/claire/api/server.py:5985-5986`) | KB members at `GET /silvercord/{token}` are sent straight to `_workspace_view`. `GET {workspace}?doc={id}` redirects 308 to `/silvercord/{token}`. | Under `SQ1: B`, KB members also land on the unified Silvercord view and need `GET /silvercord/{token}/workspace` (`U8`) to switch into Workspace without a query string or 308 loop. |
| Item view (`src/claire/api/server.py:6007-6017`, `src/bible/templates/share.html`) | Non-members on public KBs or `link_access=1` documents get `shared_html`. `U6` (switch to workspace) is not rendered in `share.html`. | No pull-out graph panel, no `본문`/`의견` switcher, no mobile bottom bar, and no `U6` workspace switch. |
| Document payload (`src/claire/store/queries.py:279-306`) | `document_detail()` populates `nodes` (`id`, `label`, `group`, `observations`, `aliases`). `share.html` embeds the full JSON into `<script id="docdata">` and never reads `dc.nodes`. | Initial HTML embeds unused `nodes` and internal entity IDs (`ent_*`), even for `link_access=1` documents. |
| Local graph (`src/bible/static/js/app.js:5176-5307`, `src/bible/static/css/workspace.css:717-732`) | Inline radial SVG (`viewBox="-160 -160 320 320"`): center document icon, Ring 1 (up to 24 document entities sorted by degree), Ring 2 (up to 12 direct KB neighbors), solid and dashed provisional edges, overflow counter, hover highlight sync with entity list and reader marks (`reader.js` `applyEntityMarks`). | Coupled to `app.js` globals (`allNodes`, `allEdges`, `docById`, `selectDoc`, `focusNode`, `loadNode`). |

## 3. Access Matrix, Routes, and Privacy Boundary

### SD1. Unified Silvercord entry and `GET /silvercord/{token}/workspace`

Keep `GET /silvercord/{token}` public (`_system("public", "none")`) and resolve an optional `viewer_principal: Principal | None` via `read_session_cookie` and `_validate_session` in `src/claire/api/security.py` (preserving Section 5.2 S11 cookie-tossing protection):

1. **Unified Document View (`SQ1: B`, `SQ2: B`, `A1`)**: For `kind == "document"`, every readable caller (anonymous visitor, signed-in non-member, and KB member) receives the upgraded Silvercord view (`shared_html`) at `GET /silvercord/{token}`. This supersedes `PERMALINK_UNIFICATION_DESIGN.md` §6 rule 4 for documents. Unreadable private documents continue to delegate to `_workspace_view(..., readable=False)` without leaking existence.
2. **Hidden / pending check**: When `pending or document.get("hidden")`, only KB members (`_caller_is_member`) can read the document; all other callers continue to receive `404`.
3. **Workspace representation route (`U8`)**: Add `GET /silvercord/{token}/workspace` (`_system("public", "none")`):
   - Invalid token / unknown / non-document -> `404`; alias -> `308` to `/silvercord/{target}/workspace`; gone -> `410`.
   - If `is_member or readable_kb`: serve `_workspace_view(request, kb, token=found.token, kind="document", target=found.target, readable=True)` (with `hidden/pending` still gated on `is_member`).
   - Otherwise (unreadable private KB or `link_access=1` non-member): serve `_workspace_view(request, kb, token=found.token, kind="document", target=found.target, readable=False)` (sign-in redirect, private profile page, or `404`, with zero document ID).

### SD2. `GET /silvercord/{token}/graph` and Data Isolation

Strip `nodes` and internal graph metadata from initial `#docdata` JSON in `GET /silvercord/{token}` for all callers so first paint stays compact and internal IDs never appear in `link_access=1` HTML source. Graph data loads lazily on demand when the user opens the pull-out graph panel via `GET /silvercord/{token}/graph`:

1. **Route policy**: `_system("public", "none")` so anonymous visitors and signed-in users have equal access (`SQ2: B`, `A1`).
2. **Validation and visibility inside the handler**:
   - Invalid token / missing row / non-document kind -> `404`.
   - Alias token -> `308` to `/silvercord/{target_token}/graph`.
   - Gone document -> `410`.
   - Hidden or pending document when `not is_member` -> `404`.
   - Unreadable document (`not is_member and not readable_kb and not found.link_access`) -> `404`.
3. **Full Local Graph scope (`is_member or readable_kb`)**: Identical for anonymous visitors, signed-in non-members, and KB members on public KBs (and for KB members on private KBs):
   - Ring 1 (up to 24 document entities sorted by degree) + Ring 2 (up to 12 direct KB neighbors).
   - Edges between rendered nodes (Ring 1↔Ring 1 and Ring 1↔Ring 2, with `provisional` flag).
   - Up to 5 shared visible documents (`shared_docs`, linked via `/silvercord/{other_token}`).
   - Entity detail fields (`aliases`, `observations`, `relations`, and `/entity/{entity_token}` link).
4. **Link-Access-Only Graph scope (`not is_member and not readable_kb and found.link_access`, `SQ4`, `A2`)**:
   - **Ring 1 (up to 24) + Ring 2 (up to 12)** and **connecting edges (Ring 1↔Ring 1, Ring 1↔Ring 2)** with `provisional` flag.
   - **Opaque IDs (`e0`, `e1`, ...)** instead of internal `ent_*` IDs (matching `_entity_link_page` in `src/claire/api/server.py:6040`).
   - **Other documents strictly blocked**: Omit `shared_docs` titles and links (`shared_docs: []`, returning only a closed count `hidden_shared_docs_count` if > 0). In Entity Detail, omit titles/links of other source documents behind a closed count, and include `/entity/{entity_token}` only when that entity itself has `link_access == 1`.

### SD3. Caching, Referrer, and Robots Headers

| Caller / Visibility | HTML & UI | `Cache-Control` & `Vary` | Robots | Graph Scope (`GET /silvercord/{t}/graph`) |
| :--- | :--- | :--- | :--- | :--- |
| **Public KB — Anonymous** | Unified Silvercord View + `본문`/`의견` + Pull-out Graph (closed by default) + `/silvercord/{t}/workspace` link | `Vary: Accept, Cookie` | `index, follow` + canonical + OG + JSON-LD + AP alternate link | Full Local Graph (Ring 1 + Ring 2 + shared docs) |
| **Public KB — Signed-in Non-Member** | Same Unified Silvercord View + `/silvercord/{t}/workspace` link | `Cache-Control: private, no-store`, `Vary: Accept, Cookie` | `index, follow` | Full Local Graph (Ring 1 + Ring 2 + shared docs) |
| **Private KB (`link_access=1`) — Anonymous or Signed-in Non-Member** | Unified Silvercord View + `본문`/`의견` (non-federated notice) + Pull-out Graph (closed by default); NO workspace link | `Cache-Control: private, no-store`, `Vary: Accept, Cookie` | `X-Robots-Tag: noindex, nofollow` + `<meta name="robots" content="noindex, nofollow"/>` (`U9`) | Link-Access-Only Graph (Ring 1 + Ring 2, opaque IDs `e0..eN`, other documents blocked) |
| **KB Member (Public or Private KB)** | Same Unified Silvercord View + prominent `/silvercord/{t}/workspace` link | `Cache-Control: private, no-store`, `Vary: Accept, Cookie` | Public KB: `index, follow` / Private KB: `noindex, nofollow` | Full Local Graph (Ring 1 + Ring 2 + shared docs) |

All `/silvercord/` responses keep `Referrer-Policy: strict-origin-when-cross-origin`.

## 4. UI Architecture and Responsive Interaction

### SD4. Default-Closed Compact Reading View (`HD3`)

Every caller starts on the `본문` tab with `#sc-graph-panel` closed. The initial reading surface preserves the centered `780px` `.wrap` reading column, `#srail` heading rail, STT modal, and presentation link without visual clutter.

### SD5. Silvercord Chrome, Mobile Bottom Bar (`SQ3: B`), and Pull-Out Local Graph (`SQ5: A`)

1. **Desktop (`>= 820px`) Sticky Header (`48px`)**:
   - **Start**: Claire Bible brand link (`/`) + KB badge (`kb.label` when `is_member or readable_kb`; generic `링크 공개 문서` label when `link_access=1` only).
   - **Center**: Segmented tabs (`본문` | `의견`).
   - **End**: `문서 그래프` toggle button (`aria-expanded="false"`, `aria-controls="sc-desktop-aside"`, entity count badge), `워크스페이스` link (`/silvercord/{token}/workspace`, hidden when `not is_member and not readable_kb`), and light/dark toggle (`claireTheme`).
2. **Mobile (`< 820px`, `SQ3: B`) Slim Top Bar + Fixed/Sticky Bottom Bar**:
   - **Top Bar**: Slim `44px` bar with brand link on the start and optional `워크스페이스` icon link + light/dark toggle on the end (no duplicate `본문`/`의견` or graph buttons).
   - **Fixed/Sticky Bottom Bar**: Respects `env(safe-area-inset-bottom)` and places the `본문` | `의견` `role="tablist"` (`focusgroup="tablist nomemory"`) alongside a separate **`문서 그래프` toggle button positioned outside `role="tablist"`** (`aria-expanded`), with `>= 44px` touch targets for fast one-handed thumb switching.
3. **Pull-Out Local Graph Panel (`#sc-graph-panel`)**:
   - **Desktop (`>= 1100px`)**: Opening the panel expands a right-hand `360px` grid column (`minmax(0, 1fr) 360px`) beside the reading area. Keeping it non-modal on desktop allows the document body to remain interactive so hovering a graph node highlights matching entity text marks in the document body (`::highlight(claire-entity-hover)`, `HD6`).
   - **Mobile & Compact (`< 1100px`)**: Opening the panel opens a native modal `<dialog>` bottom sheet via `showModal()` (`closedby="any"` + `pointerdown`/`pointerup` outside-bounds fallback). Apply `overscroll-behavior: contain` on `dialog:modal` and `overflow: hidden; overscroll-behavior: contain` on `dialog::backdrop` (`prevent-page-scroll-under-modal-dialog`); never set `overflow: hidden` or `position: fixed` on `html` or `body`. Hide `#srail` while the mobile modal sheet is open.
   - **Lazy Fetch and Error Handling (`PRINCIPLES.md` C2)**: Fetch `GET /silvercord/{token}/graph` on the first open of `#sc-graph-panel` (`credentials: "same-origin"`) and cache in memory for the page session. Render explicit loading and retryable error states without fake fallback data.
   - **Panel Contents & Entity Drill-Down (`SQ5: A`)**: Sub-tabs **로컬 그래프** (radial SVG + Ring 1/Ring 2 legend + unshown count + shared documents or blocked-docs privacy notice) and **엔티티 (N)**. Clicking a node or entity row drills down inside the same panel to **엔티티 상세** (name, type, aliases, observations, relations, source documents or closed count, a `← 로컬 그래프로 돌아가기` back button, and an optional `/entity/{token}` link when accessible).

### SD6. Shared Local Graph Module (`local_graph.js`)

Extract pure local-graph rendering and hover-highlight helpers from `src/bible/static/js/app.js` (`renderLocalGraphHtml`, `setEntityHoverHighlight`, `clearEntityHoverHighlight`, and `.ctx-local-graph` styles from `src/bible/static/css/workspace.css`) into a shared `src/bible/static/js/local_graph.js` module + shared CSS rules used by both Workspace and Silvercord.

### SD7. Fast Switching Between `본문` and `의견` (`SR4`)

1. **Accessible Tabs + Find-in-Page**:
   - Use `focusgroup="tablist nomemory"` and `role="tablist"` with `<button type="button" role="tab" aria-selected="true|false">` (moving `focusgroupstart` to the selected tab, no `aria-controls` on tabs) and a small `ArrowLeft`/`ArrowRight`/`Home`/`End` keydown fallback (zero CDN polyfills per `HD10`).
   - Inactive panels (`#sc-panel-doc` or `#sc-panel-opinions`) use `hidden="until-found"` with initial state rendered in HTML. Place all padding, borders, and backgrounds on inner child wrappers, never on `[hidden="until-found"]` itself. Attach a `beforematch` listener to synchronize tab selection and `aria-selected` on browser Find-in-Page.
   - Draw the active tab indicator with `border-block-end` (not color alone or `box-shadow`), animated with CSS Anchor Positioning (`anchor-name: --active-sc-tab`, `position-anchor: --active-sc-tab`) inside `@supports (position-anchor: auto)` and `@media (prefers-reduced-motion: no-preference)`, with a `::after` `border-block-end` fallback.
2. **Scroll Position Preservation and `#srail` Sync**:
   - Save `window.scrollY` when switching from `본문` to `의견`, scroll to the top of `의견`, and hide `#srail`.
   - Restore `window.scrollTo({ top: savedDocScrollY, behavior: "instant" })` when returning to `본문`, and call `updateSharedRail()` to keep heading markers synchronized.

### SD8. ActivityPub `의견` Visual Prototype (`SR5`)

Until the ActivityPub discussion protocol and storage model are designed:

- **Tokens and Icons (`theme.css` + `PRINCIPLES.md` G1)**: Use `--proto` / `--proto-bg` for prototype badges (`시각 프로토타입 · 미구현`) and `--remote` / `--remote-bg` for ActivityPub federation elements. Use inline SVG line icons only (`comment`, `broadcast`, `heart`, `repeat`, `send`, `lock`, `info`), with zero emojis (`tests/test_ui_line_icons.py`).
- **Prototype Sections**:
  1. **Prototype Notice Banner**: States clearly that `의견` is a non-functional visual prototype for planned `Create(Note)` `inReplyTo` `/ap/objects/{public_id}` federation.
  2. **Disabled Composer & Reaction Bar**: Textarea (`disabled`, `aria-disabled="true"`), `의견 남기기 (미구현)` button (`disabled`), and preview reaction chips (`공감`, `알리기`).
  3. **Sample Thread Preview** (Public KB): Clearly framed sample box (`예시 스레드 · 실제 의견이 아님`) showing local and remote Fediverse replies (`@researcher@mastodon.example`).
  4. **Private Link-Access State (`not kb.is_public`)**: Shows a dedicated non-federated notice (`비공개 지식베이스 문서 — ActivityPub 비연합`).

## 5. Modern Web Guidance Applied

- **`tabs` & `anchor-positioning-tab-underline`**: `focusgroup="tablist nomemory"` with `focusgroupstart`, no `aria-controls` on tabs, `border-block-end` indicator (never color alone or `box-shadow`) animated with `anchor-name` / `position-anchor` inside `@supports (position-anchor: auto)` and `prefers-reduced-motion: no-preference`, plus `::after` fallback and `ArrowLeft`/`ArrowRight`/`Home`/`End` JS fallback (no CDN polyfill per `HD10`).
- **`search-hidden-content`**: `hidden="until-found"` on inactive panels, padding/borders placed only on inner child wrappers, and a `beforematch` listener to sync tab state on Find-in-Page.
- **`dialog` & `prevent-page-scroll-under-modal-dialog` & `navigation-drawer`**: Mobile pull-out graph uses a native modal `<dialog>` (`showModal()`) with `overscroll-behavior: contain` on `dialog:modal` and `overflow: hidden; overscroll-behavior: contain` on `dialog::backdrop` (never `overflow: hidden` or `position: fixed` on `html`/`body`), plus `closedby="any"` and a `pointerdown`/`pointerup` outside-bounds fallback.

## 6. Implementation Phases and Verification Plan

| Phase | Scope | Key deliverables |
| :--- | :--- | :--- |
| **S1** (Backend & Boundary) | Unified Silvercord route, `/workspace`, `/graph`, `#docdata` trim | `security.py` optional session helper; `server.py` `GET /silvercord/{token}` unified rendering (`SQ1: B`, `SQ2: B`) + `GET /silvercord/{token}/workspace` (`U8`) + `GET /silvercord/{token}/graph` (Full vs Link-Access-Only Ring 1+2 with opaque IDs & blocked other docs); strip `nodes` from initial `#docdata`; `Cache-Control: private, no-store` & `Vary: Accept, Cookie`. |
| **S2** (Shared Local Graph) | Extract and embed pull-out local graph | `src/bible/static/js/local_graph.js` shared by Workspace and Silvercord; desktop right column (`>= 1100px`) + mobile modal `<dialog>` sheet (`< 1100px`); in-panel Entity Detail drill-down (`SQ5: A`); hover sync with CSS Custom Highlight entity marks (`applyEntityMarks`). |
| **S3** (Silvercord UI & Prototype) | Desktop header, mobile bottom bar, switcher, prototype | `share.html` / `silvercord.js` / `silvercord.css` (and `src/claire_bible/web/pages.py` bundle); mobile bottom bar (`SQ3: B`); `본문`/`의견` tabs with scroll preservation; ActivityPub `의견` visual prototype; `ko.json` / `en.json` catalog keys. |

**Planned verification (when implementing S1–S3):**
- **Unit tests**: Update `tests/test_permalinks.py` for `SQ1: B` (`/silvercord/{token}` unified landing + `/silvercord/{token}/workspace`); `GET /silvercord/{token}/graph` authorization matrix (`404` non-readable, Ring 1 + Ring 2 with opaque IDs and blocked other docs on `link_access=1`, Full Ring 1 + Ring 2 on public KB and for members); initial `#docdata` contains zero `nodes`.
- **E2E tests (Playwright)**: Anonymous and signed-in visitors see identical Silvercord reader with closed-by-default graph toggle; opening graph lazily fetches `/graph`, hovers node to highlight body mark, and drills down in-panel (`SQ5: A`); private `link_access=1` hides other documents; mobile (`390px`) bottom bar (`SQ3: B`) switches `본문` ↔ `의견` while preserving scroll position; `test_ui_line_icons.py` passes with zero emojis.

## 7. Owner Decisions (2026-11-24)

| ID | Question | Decision |
| :--- | :--- | :--- |
| **SQ1** | Landing view for a **KB member** opening `/silvercord/{token}` | **B.** Unified Silvercord reading view by default, with a prominent `워크스페이스` switch (`GET /silvercord/{token}/workspace`, `U8`). |
| **SQ2** | Anonymous visitor access to `본문`/`의견` switcher and local graph | **B + Ring 1 + Ring 2 (`A1`).** No discrimination against anonymous visitors: same Silvercord view, `본문`/`의견` switching, and interactive Ring 1 + Ring 2 local graph (closed by default). |
| **SQ3** | Mobile (`< 820px`) placement of the `본문` / `의견` switcher | **B.** Fixed/sticky bottom bar containing the `본문`/`의견` tablist and an adjacent `문서 그래프` button outside `role="tablist"`. |
| **SQ4** | Graph scope for non-members on a **private KB (`link_access=1`)** document | **Ring 1 + Ring 2 + `A2`.** Return Ring 1 + Ring 2 nodes and connecting edges with opaque IDs (`e0`, `e1`, ...) while strictly blocking reading other documents (no unreadable document titles, links, or workspace link). |
| **SQ5** | Clicking an entity node inside the Silvercord local graph | **A.** In-panel Entity Detail drill-down with a back button and a conditional `/entity/{token}` link when accessible. |
