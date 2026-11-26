# Public `/about` Page and Shared Interactive Pipeline Design

Status: Design proposal (not implemented in `src/`).

## 1. Goals and Problem Statement

This document defines two connected changes for Claire Bible:

1. Add a public `/about` page accessible to unauthenticated internet visitors (`_system("public", "none")` in `src/claire/api/security.py` `[code]`). The page restores relevant instance information that currently lives only in closed drawers or removed environment configuration, and recommends useful public architecture and discovery sections `[decision]`.
2. Refactor the document processing pipeline diagram in `src/claire/api/accounts_routes/pipeline.py` (`support_pipeline_body`, `_graph_card`) into one shared interactive component usable on both `/support/pipeline` and `/about` `[decision]`.

Why the current pipeline diagram confuses readers `[code]`:

- `src/claire/api/accounts_routes/pipeline.py` renders a static SVG (`_graph_card`) with separate prose cards (`_api_flow_card`, `_client_flow_card`, `_other_ai_card`). It does not separate the 6 document-creation entrypoints (`web_api_cli`, `telegram`, `mcp_agent`, `feed_syndication`, `background_loops`, `reprocess_renditions`).
- Readers cannot tell from one view which entrypoints support `provider` vs `client` mode, which skip fetch or subtitle/STT, which omit Gate 4 LLM judging, `relation_judge`, heatmap logging, and Jev shadow scoring, or where `classify_watch` and `expand-loop` run.

Visual prototype creation sequence `[decision]`:

- **Step 1 (before Hindsight reference)**: Build Proposals 1 and 2 in [about_and_pipeline_prototype.html](prototypes/about_and_pipeline_prototype.html).
- **Step 2 (reference inspection)**: Inspect `https://hindsight.vectorize.io/#architecture-deep-dive` to study its fixed 4-column SVG spatial diagram, database cylinders, micro-pills, and timeline controls.
- **Step 3 (after Hindsight inspection)**: Add Proposal 3 (4-column live spatial flow) to [about_and_pipeline_prototype.html](prototypes/about_and_pipeline_prototype.html).
- **Step 4 (simplification + Gemini Neon identity)**: Build Proposal 4 (Gemini Neon — unified 5-stage optical prism) dropping the 6-entrypoint selector taxonomy to avoid choice overload while retaining the single core `provider` vs `client` architectural switch.

## 2. Public `/about` Content Audit and Security Boundaries

### 2.1 Previously Shown or Configured Information to Restore

Audit of `src/` and history identifies four safe categories that were hidden or retired:

| Item | Current / Historical State | Proposed Role on `/about` |
| --- | --- | --- |
| Operator (`#drawermanager`) & Repository (`#repolink`) | Present in `src/bible/templates/index.html` inside the default-closed `#drawerfooter` of `#detailpane`, resolved via `_resolve_sorcerer_name`, `effective_github_repository`, and `effective_source_base_url` in `src/claire_bible/web/pages.py`; absent from `public_directory.html` and `public_kb.html` `[code]`. Historical `CLAIRE_SORCERER` env var was retired (`3f9c2932`) in favor of owner `display_name` `[git]`. Build commit SHA is embedded via `CLAIRE_BUILD_COMMIT` in `Dockerfile` and `/app/src/claire/BUILD_COMMIT` (`src/claire/ops/support_bundle.py`) `[code]`. | Show `"지식 관리자"` (Knowledge Curator) with the owner display name and ActivityPub handle (`@{username}@{host}`, omitted when unset) once in the `/about` hero metadata strip; show the 7-char short build commit from `CLAIRE_BUILD_COMMIT` in KPI Box 4 (hidden when `unknown`/empty); and place `API 문서 (/reference)`, `github_repository`, and `agent_marketplace_repo` visibly in the `/about` footer without repeating `지식 관리자` `[decision]`. |
| Site Mission Description | `CLAIRE_SITE_DESCRIPTION` existed in configuration and was removed in `ENV-1` (`73a7b7c6`) because no active template read it `[git]`. | Present a concise product-focused lead explaining multi-source structuring, server-preferred-language summaries and AsciiDoc renditions, ontology graph linking, and shared/federated exploration (without foregrounding internal `raw_text` storage in the intro) `[decision]`. |
| Processing Pipeline Diagram & Rules | Previously visible only in admin console, then moved to owner-only `/support/pipeline` in `src/claire/api/accounts_routes/console.py` (`SUPPORT_PIPELINE_SPEC`) `[code]`. | Embed the shared interactive pipeline component in public viewer mode (`public`), teaching how content flows without leaking account data `[decision]`. |
| Top Header & Footer Links | `/explore/*` routes were retired (`d9f5ff07`), while `/directory` and `/kb` already appear in the shared public top navigation bar `[code]`. | Keep the top public header for human browsing (`홈`, `/directory`, `/kb`, `/about`, login — moving `/reference` out of the header); omit the `'표준 인터페이스 · 연동 경로'` block (`NodeInfo`, `/mcp`, `/ns`, `/sitemap.xml`, `/robots.txt`) from `/about` completely; and render `API 문서 (/reference)`, `GitHub 저장소`, and `에이전트 마켓플레이스` always-visible in the `/about` footer `[decision]`. |

### 2.2 Recommended New Public Additions

1. **Product Philosophy Cards (Enduring Principles)**: Present four enduring project philosophies without development-stage rules (`C2` no hidden fallback) or low-level storage details (`raw_text` internals stay inside pipeline Beat 01–02):
   - Ontology-Based Knowledge Linking (11 seed entities, 11 relations, 5-gate entity resolution).
   - Deterministic Boundaries vs AI Inference (clear split between solid-rule guards/validation and dashed AI extraction/judging).
   - Personal Sovereignty with Selective Sharing & ActivityPub Federation.
   - Open Agent Interoperability & Isolation Security (`provider` + MCP OAuth 2.1 `client`, nonce framing, strict schema checks).
2. **Dynamic i18n & Server Preferred Language Interpolation**: Render `/about` UI copy through `localize_page(html, get_locale())` (`src/claire_bible/web/pages.py`) and read the server's primary preferred language dynamically via `read_server_languages()` (`src/claire/accounts/languages.py`, first entry of `server_settings.default_languages`, fallback `en`) mapped to localized language names directly inside sentence prose (e.g., `"{LANG} 초록"` instead of a separate `ko -> en` KPI box) `[code]`.
3. **Supported Sources Overview (Always Visible)**: Display one always-visible table card on `/about` titled `"지원하는 자료와 출처"` (`자료 유형`, `수집 방식`, `특이사항`) covering Web/Discourse/Scrapling/CDP, PDFium/`pypdf`/ODT/DOCX, YouTube subtitle-first -> STT, X/HoYoLAB/Law/Uttu/VMware Explore adapters, and RSS/Atom/JSON feeds (avoiding collapsed disclosures and ambiguous `"경로"` wording). Omit closed ontology lists (`11+11`) from `/about`, and place the 3 Rendition templates policy (`standard`, `concise`, `academic` in `src/claire/extract/rendition_templates.py`) inside the Ingest dialog (`적재 기능 선택`, `#modal-ingest`) contextual help instead of `/about` `[decision]`.
4. **Public Operational Posture & KPI Grid (`Q3` Decided)**: Display four KPI cards in the `/about` hero:
   - **Box 1**: Network mode & invite-only registration (`openRegistrations: false` in `sidecar/fedify/src/sidecar.ts`).
   - **Box 2**: Public profile count, public shared KB count, and public theme count.
   - **Box 3 (`Q3` Decided)**: Public-only published document and entity counts (`is_public = 1`).
   - **Box 4 (`Q7` Decided — Option A)**: 7-char build commit SHA (`CLAIRE_BUILD_COMMIT` / `/app/src/claire/BUILD_COMMIT`, omitted when unset).

### 2.3 Strict Privacy and Isolation Guarantees (`KD15` & Viewer Context)

Public `/about` and `member` views MUST enforce strict data isolation (see `tests/test_support_pipeline.py` `[code]`):

- **Never expose on `/about` (`viewer = "public"`)**: private account counts, private KB titles/counts, hidden documents, federated peer server lists or counts (`KD15`), server AI provider keys, member BYOK keys/models, recent document titles/lineages (`_recent_card`), removed fallback tables (`_fallback_card`), `/admin/*` URLs, Jev shadow evaluation (`jev-latest`), or `gemini-decision-1` pre-labeling metadata.
- **Never expose on `/support/pipeline` (`viewer = "member"`)**: server provider model assignments of other users, admin-only recent document lineage, removed fallback tables, `jev_shadow`, `gemini-decision-1`, or `/admin/*` links.
- **Route Policy Safety**: Reserve `about` in `RESERVED_USERNAMES` (`src/claire/accounts/username_policy.py`) alongside `directory`, `support`, `admin`, and `kb`, and register `/about` in `ROUTE_POLICY` (`src/claire/api/security.py`) as `_system("public", "none")` `[code]`.

## 3. Six Document-Creation Entrypoints (`[code]`)

Verified against `src/claire/ingest/service.py`, `src/claire/ingest/pipeline.py`, `src/claire/ingest/client_extraction.py`, and `src/claire/ingest/renditions.py`:

| Entrypoint ID | Trigger Surfaces | Extraction Mode | Key Pipeline Differences |
| --- | --- | --- | --- |
| `web_api_cli` | Web UI dialog, `POST /ingest`, `/api/v1/kbs/{kb}/ingest`, `claire ingest` | `provider` or `client` (Web UI requires explicit mode; CLI defaults to account setting) | Standard path. Supports optional `concise` and `academic` renditions alongside default `standard`. |
| `telegram` | Telegram DM link, file upload, or caption+link | `provider` or `client` (`--force-provider`, `--agent-client`, `--full`, `--effort`) | If attachment + URL co-occur, runs `compose_composite_document` (`IngestService.ingest_composite`) with 2x prompt budget. Ingest focus is sealed against delimiter text. |
| `mcp_agent` | MCP OAuth 2.1 `extract_stage`, `extract_claim`, `extract_read`, `extract_submit` | Always `client` (personal KB only, `CT4`; server API or `/reingest`/`/regenerate` returns `403 agent_client_provider_refused`) | `stage_raw(..., full_content=True)` turns OFF server STT, VLM, and paper classification and skips `agent_source_staged` notifications. `extract_submit` runs 4-layer deterministic validation, precomputes embeddings before DB write, runs Gate 1–3 only, commits via `_AtomicConnection` (`origin="agent_client"`), and skips Gate 4, Jev, heatmap, `relation_judge`, `classify_watch`, and auto-expand. |
| `feed_syndication` | `/syndication` RSS/Atom/JSON feeds & remote ActivityPub items (`POST /api/feeds/items/{id}/ingest`) | `provider` or `client` | Shares standard personal KB isolation and mode branching after item selection. |
| `background_loops` | `expand-loop`, `refresh-loop`, `recover-loop` | Server workers (`provider` except `recover-loop`, which keeps original `raw_inbox.extraction_mode`) | Runs only on personal KBs (`CT10`), never shared KBs. `expand-loop` sets depth 1 (`expand_max=0`) with optional shadow verdict. `refresh-loop` re-fetches using the same `doc.id` to keep entity sources. `recover-loop` retries failures with exponential backoff and skips `staged` rows. |
| `reprocess_renditions` | `reingest`, `regenerate` (`regenerate_components`), `add_renditions` | Web/API: `provider` only (`mcp_agent` uses `extract_stage(document_id, force=True)`) | `regenerate` skips external fetch/STT/classify, reuses `raw_text`, preserves prior graph on failure via `before_store`, and skips `classify_watch`/auto-expand. `add_renditions` runs `render_detail` only for `concise`/`academic` without touching entities or relations. |

## 4. Shared Pipeline Component Architecture

### 4.1 Port Boundary (`src/claire` vs `src/bible`)

Following the UI port isolation rule (`src/claire_bible/web/pages.py` `[code]`):

- **Data producer (`src/claire`)**: Provide a pure serializer function (for example in `src/claire/api/accounts_routes/pipeline.py` or a dedicated module imported by both `pipeline.py` and `public_site.py`) that outputs a sanitized JSON payload for the viewer role (`public`, `member`, `admin`). For `public`, emit generic slot labels and architecture constants only; never include account models or admin data in HTML or JSON responses.
- **Shared renderer (`src/bible`)**: Host reusable client assets (`src/bible/static/js/` and `src/bible/static/css/`) built cleanly with existing theme tokens (`src/bible/static/css/theme.css`) and 16x16 line icons (`src/bible/static/js/icons.js`).
- **Console contract compliance (`AGENTS.md`)**: When `/support/pipeline` mounts the shared component inside `src/claire/api/templates/console_shell.html`, render top-level `.console-subcard` containers directly inside `.console-host`, use native `console_ui.css` controls (no `var(--error)`—always `var(--danger)`, no emojis in UI, preserve `tests/test_console_ui_contract.py` and `e2e/console_ui_consistency.spec.js`).
- **Public shell compliance**: When `/about` mounts the same component, wrap it in a public layout compatible with `src/claire/api/templates/public_directory.html` and `src/claire/api/templates/public_kb.html`.

### 4.2 Existing `/support/pipeline` Test Compatibility

`tests/test_support_pipeline.py` and `e2e/support_pipeline.spec.js` check DOM anchors (`#pipeline-graph`, `#pipeline-stt-order`, `#pipeline-fixed`, `#pipeline-recent`) and role-based privacy rules (`member` vs `admin`). The refactored console wrapper keeps those card IDs and tab isolation intact.

## 5. Visual Prototype Comparison (Proposals 1, 2, 3, and 4)

All four proposals share one interactive file: [about_and_pipeline_prototype.html](prototypes/about_and_pipeline_prototype.html). Controls let the reviewer switch between Proposal 1, Proposal 2, Proposal 3, Proposal 4, viewer context (`/about` public, `/support/pipeline` member, `/support/pipeline` admin), viewport width (desktop vs `390px` mobile), and light/dark theme.

| Proposal | Primary Interaction Pattern | Strengths | Trade-offs |
| --- | --- | --- | --- |
| **1안: Path-Tracer + Live Inspector** | Entrypoint tabs (`tablist`), `provider`/`client` toggle, 4-zone clickable stage canvas (active vs dim `skipped` badges), sticky/inline inspector showing inputs, actions, applied model, fail-closed rule, and nested details, plus step-by-step Playthrough buttons. | Zero overlapping text on `390px` mobile; deep technical inspection per stage (inputs, outputs, invariants). | Spatial motion across columns is static compared to Proposal 3. |
| **2안: Scenario Workbench + Diff & Simulator** | Entrypoint launcher cards + 3 workbench tabs: (A) 4-column accordion decomposition with AI/Rule/Client filter, (B) 1:1 path diff table highlighting changed steps, (C) Interactive 5-Gate Entity Resolution Simulator & Subtitle/STT Decision Simulator. | Best for understanding "why do these two entrypoints differ?" and testing edge cases like Gate 2 version conflicts or Fail-Closed subtitle failures. | Higher initial visual density for first-time `/about` visitors. |
| **3안: Hindsight-Inspired 4-Column Live Flow** (built after inspecting `hindsight.vectorize.io`) | Fixed 4-column SVG (`Entrypoints` -> `Claire Processing Engine` -> `SQLite Canonical Cylinders` -> `Async Personal-KB Workers`), scenario tabs with `scaleX` step progress bar, Play/Pause + `1x`/`2x` + Fullscreen (`Esc`), bottom `prev/next` scrubber with `aria-live` captions, animated active dashed edges, and node click-to-jump. | Instantly shows live synthetic payload states flowing across sync/async boundaries and database tables without shifting columns across scenarios. | Requires horizontal scroll (`overflow-x: auto`) or fullscreen expansion on `390px` mobile. |
| **4안: Gemini Neon — Unified 5-Stage Optical Prism** *(Owner Selected)* | Drops all 6-entrypoint selectors. Uses a deep-obsidian optical glass canvas inspired by `src/bible/static/icons/favicon.svg` emerald-mint (`#26ffaa`) and electric-azure (`#38b6ff`) neon lines, one unified 5-stage photon rail (`01`..`05`), one `서버 API` / `에이전트 클라이언트` fork switch, live specimen metamorphosis (rendition preview, AED9 nonce shield, inline Beat 04 5-Gate optical meter, Beat 05 SVG constellation), visible motion Pause/Resume, and refined `/about` metadata/philosophies (no `MIT` or `robots.txt` links, dynamic `server_settings.default_languages`, short `CLAIRE_BUILD_COMMIT` option). | Eliminates entrypoint choice overload and centers on the true document lifecycle while aligning `/about` framing cleanly with end-user navigation and i18n. | Entrypoint-specific nuances (Telegram 2x composite budget, background worker loops) move into secondary accordions rather than top-level filters. |

Recommendation note: **Proposal 4** is selected by the owner (`[decision]`) as the direction for `/about` and `/support/pipeline`, keeping Proposals 1–3 in the prototype file for comparison.

## 6. Modern Web Guidance Patterns Applied

- `tabs` & `anchor-positioning-tab-underline`: `focusgroup="tablist nomemory"`, arrow-key / `Home` / `End` keyboard navigation, `scroll-snap-type: inline proximity`, CSS `anchor-name` + `position-anchor` with border-based underline fallback (safe in forced-colors mode), and zero padding/background on `[role="tabpanel"]` itself (`hidden="until-found"` safety).
- `accordion`: Native adjacent `<details>` accordions (`details.p4-acc`) in Proposal 4 use `::details-content` with `interpolate-size: allow-keywords`, `contain: layout`, and `transition-behavior: allow-discrete`, place a rotating chevron inline SVG directly before the summary label, and never embed headings inside `<summary>`.
- `motion` & `natural-interactions`: Proposal 4 optical photon animations run strictly on compositor properties (`transform` and `opacity`) inside `@media (prefers-reduced-motion: no-preference)`, offer an accessible Pause/Resume toggle without `aria-pressed` conflict on label swap (`#p4-motion-btn`), and provide explicit hover/press (`scale(0.97)`) and `:focus-visible` ring feedback across desktop and keyboard interactions.
- `search-hidden-content`: `beforematch` event handler opens the matching hidden tabpanel automatically during browser find-in-page (`Ctrl+F`).
- `size-aware-styling`: `container-type: inline-size` on `.pipe-root` and `.p4-theatre` adapts multi-column grids and horizontal optical rails to the mounted container width rather than relying only on viewport width.
- `dark-mode-toggle`: `:root` keeps `color-scheme: light dark` and supports `[data-theme]` switching with `:root:has(> head > meta[name="color-scheme"][content="dark"])`.
- `accessibility`: `aria-hidden="true"` on decorative SVG icons and the inline Arcane Core emblem, `role="img"` + `<title>`/`<desc>` on architectural/constellation SVGs, `aria-live="polite"` status reporting, and `prefers-reduced-motion: reduce` disabling autoplay and transitions.

## 7. Owner Decisions (`Q1`–`Q7` — All Settled)

All seven design questions are now resolved (`[decision]`):

1. **`Q1` (Preferred Diagram Direction — Decided)**: **Proposal 4 (`4안`: Gemini Neon — Unified 5-Stage Optical Prism)**.
2. **`Q2` (`/about` Header Link — Decided)**: Include `/about` in the shared public top navigation bar (`public_directory.html`, `public_kb.html`).
3. **`Q3` (Public Instance KPIs on `/about` — Decided)**: Remove `AI 학습 크롤러 차단` (`block_ai_crawlers`) and show public-only published document and entity counts (`128편 · 410개`, `is_public = 1`) in KPI Box 3.
4. **`Q4` (`/support/pipeline` Legacy Card Replacement — Decided)**: Replace legacy static flow cards (`_api_flow_card`, `_client_flow_card`, `_other_ai_card` in `src/claire/api/accounts_routes/pipeline.py`) with the shared Proposal 4 component, keeping `_stt_order_card`, `_fixed_card`, and admin-only `_recent_card` / `_fallback_card`.
5. **`Q5` (`member` Simulator Access — Decided)**: Provide interactive simulators (5-Gate Optical Meter and Subtitle/STT rules) to logged-in members on `/support/pipeline` scoped to their BYOK settings, with admin `jev-latest` / `gemini-decision-1` strictly excluded.
6. **`Q6` (`/about` UI Outbound Links & Lower Sections — Decided)**: Keep `/reference` out of the top public header and move it into the always-visible `/about` footer alongside `GitHub 저장소` and `에이전트 마켓플레이스` (without repeating `지식 관리자`); remove `'표준 인터페이스 · 연동 경로'`, `'닫힌 온톨로지 (11+11)'`, and `'정리본(Rendition) 템플릿 3종 정책'` from `/about` (relocate Rendition help to the `#modal-ingest` ingest menu); and present `"지원하는 자료와 출처"` as one always-visible table card.
7. **`Q7` (Software Version Display — Decided: Option A)**: Show the 7-character short commit SHA from `CLAIRE_BUILD_COMMIT` (`/app/src/claire/BUILD_COMMIT`) in KPI Box 4, hidden automatically when unset or `unknown`.
