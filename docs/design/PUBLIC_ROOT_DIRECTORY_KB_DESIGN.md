# Public `/`, `/themes`, `/directory`, and `/about` Unified Observatory Architecture & Visual Design

- **Document Identifier**: `DESIGN-PUBLIC-ROOT-DIRECTORY-KB-20261127`
- **Status**: Implemented (2026-11-27).
- **Date**: 2026-11-27
- **Related Documents**:
  - [`ABOUT_PAGE_AND_INTERACTIVE_PIPELINE_DESIGN.md`](ABOUT_PAGE_AND_INTERACTIVE_PIPELINE_DESIGN.md) (`Q1`–`Q7`)
  - [`SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md`](SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md) (§7, §8.4, `CT2`–`CT18`)
  - [`KNOWLEDGE_FEDERATION_DESIGN.md`](KNOWLEDGE_FEDERATION_DESIGN.md) (§6.2, §8.3, §8.4; **supersedes `KD15` server-list concealment rule**)
  - [`../implementation/PUBLIC_SITE.md`](../implementation/PUBLIC_SITE.md)

Evidence tags follow [`../PRINCIPLES.md`](../PRINCIPLES.md) F1: **[code]**, **[decision]**, **[implementation]**.

---

## 1. Policy Update: Public Federation Status on `/` (Superseding `KD15` Server-List Concealment)

- **[decision] (Owner Policy Update, 2026-11-27)**: The prior `KD15` policy forbidding public display of active federated peer Claire Bible servers on `/` is **retired**.
- **Rationale**: Rather than rendering `/` as an `/about` duplicate, an entity graph, or an operator console, `/` functions as a **Calm Federated Observatory** that shows how this server and connected Claire Bible servers form a living virtuous cycle — **`1. Local Ingest & Publish` → `2. Shared Theme Bridges` → `3. Peer Claire Bible Servers` → `Reflux (Curations & Approved Contributions)`**.
- **Public Exposure Boundaries (`[decision]`)**:
  - **Publicly displayed on `/`**: active, non-blocked Claire Bible peer server domains (`kf_known_servers` excluding domains matched by `federation_store.blocking_domain`), peer `shared_kb_count` (or indexed remote KB count), bridged theme names, indexed remote KB links, and aggregated public exchange counts (`curation`, `accepted_contribution`, `remote_knowledge_base`).
  - **Strictly private (never exposed publicly)**: domain-blocked servers, `software_name`, `software_version`, `last_error`, `added_by`, `server_actor_iri`, private accounts (`profile_public = 0` when unauthenticated), private/hidden/pending KBs or documents, provisional entities, server/BYOK API keys, Jev shadow data, and `/admin/*` diagnostics.

---

## 2. Locked Navigation Architecture (4 Top-Level Items)

- **[decision] (Owner Decision, 2026-11-27)**: Retire the `/kb` browser HTML catalog (`GET /kb` HTML requests redirect with `308` to `/themes`, preserving query parameters), remove every UI link and view switcher pointing to `/kb`, and keep **`/themes`** as the sole public catalog. JSON `GET /kb`, `POST /kb`, `/@{u}/kb`, and individual KB pages `/kb/{number}/` (with breadcrumb returning to `/themes`) remain intact.
- **Unified Top Appbar (`.pub-appbar-nav`, single row)**:
  - **`홈` (`/`)**: Calm Federated Observatory — network federation virtuous cycle and bridged active themes.
  - **`테마` (`/themes`)**: Sole public catalog of subject-clustered local & remote KB collections, plus shared KB creation.
  - **`공개 계정` (`/directory`)**: Public accounts index (`@username`, theme/doc counts, cursor pagination), built with the unified light/dark shell instead of standalone inline CSS.
  - **`소개` (`/about`)**: Instance overview, KPI cards, interactive 5-stage Gemini Neon document-processing pipeline, philosophy cards, supported sources table, and `/reference` footer link.
- **No Duplicate Sub-Navigation Bar**: `.claire-pubnav` is omitted when `header.appbar .pub-appbar-nav` is present on `/`, `/themes`, `/themes/{number}/`, `/kb/{number}/`, `/directory`, and `/about`.

---

## 3. Visual Architecture: Calm Federated Observatory

1. **Theme-Native Surface Polarity (No Dark Neon Island on `/`)**:
   - Uses `:root { color-scheme: light dark; }` with `theme.css` tokens (`--bg`, `--card-bg`, `--panel-bg`, `--fg`, `--muted`, `--border`) and `@supports (color: light-dark(white, black))` (`--obs-*` tokens, `color-mix(in oklab, ...)` soft tints).
   - Never mounts `/about`'s dark-obsidian `.p4-theatre` or `.p4-hero-neon` gradient border on `/`.
2. **90/10 Signal Palette & Multi-Cue Accessibility (WCAG 1.4.1 & Forced Colors Mode)**:
   - 90% neutral structure (`--card-bg`, `--panel-bg`, `1px solid var(--border)`).
   - 10% semantic signal accents paired with stroke patterns, icons, and labels:
     - `--accent2` + solid border/conduit: local ingest & public publication.
     - `--accent` + solid border/bar: shared theme bridges (`real` / `fictional`).
     - `--remote` + dashed border/return arc: cross-server federation & reflux loop.
   - `@media (forced-colors: active)` enforces explicit `CanvasText`, `LinkText`, and `outline` rules.
3. **Typography Split**:
   - `Noto Serif KR` for the `/` observatory masthead (`h1.obs-title`), distinguishing the public editorial observatory from admin consoles.
   - `Noto Sans KR` for UI body copy; `D2Coding` with `font-variant-numeric: tabular-nums` for domains and live counts.
4. **Two-Tier Layout Without Console Controls**:
   - **Tier 1 (`#home-observatory`, Above the fold)**: Single integrated stage combining inline identity + tabular telemetry bar (`.obs-header`), 3-column **Directed Flow Topology** (`.obs-flow-shell` with responsive `@container obs` Bezier SVG conduits), and bottom **Reflux Return Arc** (`.obs-reflux`). Zero mode switches, filter buttons, pause buttons, or permanent inspector drawers. Cold start (`peer_count == 0`) shows a clean dashed standby slot in Column 3.
   - **Tier 2 (`#home-themes`, `#public-home-recent`, `.obs-directory-strip`)**: Active federated themes aligned via CSS `grid-template-rows: subgrid`, compact cross-server recent activity list (`#public-home-recent`), and a lightweight directory bar linking to `/directory` (no redundant account card wall on `/`).

---

## 4. Modern Web Platform Implementation

- **Progressive Hovercards (`popover="hint"` + `interestfor` + CSS Anchor Positioning)**:
  - Each theme bridge and peer server node is a standard `<a href>` link whose hover/focus triggers a declarative `popover="hint"` preview card anchored via `position-area: block-end span-inline-end` and `position-try-fallbacks` (gated inside `@supports (position-area: block-end)` so unsupported browsers never strand overlays at `(0, 0)`).
  - `public_observatory.js` wires `role="tooltip"` and `aria-describedby` upfront before interaction and adds graceful hover/focus + viewport-clamped fallback positioning when native `interestfor` is unavailable.
- **Cross-Document MPA View Transitions**:
  - Public routes share `public_observatory.css`, opting into `@view-transition { navigation: auto; }` strictly inside `@media (prefers-reduced-motion: no-preference)`.
- **Reduced Motion**:
  - SVG conduit pulse motion runs only under `@media (prefers-reduced-motion: no-preference)` and is disabled otherwise.
