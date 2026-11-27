# Public `/`, `/themes`, `/directory`, and `/kb` Unified Information Architecture & Visual Design

- **Document Identifier**: `DESIGN-PUBLIC-ROOT-DIRECTORY-KB-20261127`
- **Status**: Design proposal (not implemented in `src/`).
- **Date**: 2026-11-27
- **Visual Prototype**: [`prototypes/public_pages_prototype.html`](prototypes/public_pages_prototype.html)
- **Related Documents**:
  - [`ABOUT_PAGE_AND_INTERACTIVE_PIPELINE_DESIGN.md`](ABOUT_PAGE_AND_INTERACTIVE_PIPELINE_DESIGN.md) (`Q1`–`Q7`)
  - [`SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md`](SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md) (§7, §8.4, `CT2`–`CT18`)
  - [`KNOWLEDGE_FEDERATION_DESIGN.md`](KNOWLEDGE_FEDERATION_DESIGN.md) (§6.2, §8.3, §8.4; **supersedes `KD15` server-list concealment rule**)
  - [`../implementation/PUBLIC_SITE.md`](../implementation/PUBLIC_SITE.md)

Evidence tags follow [`../PRINCIPLES.md`](../PRINCIPLES.md) F1: **[code]**, **[decision]**, **[proposal]**.

---

## 1. Policy Update: Public Federation Status on `/` (Superseding `KD15` Server-List Concealment)

- **[decision] (Owner Policy Update, 2026-11-27)**: The prior `KD15` policy forbidding public display of federated peer Claire Bible servers on `/` is **retired**.
- **Rationale**: Rather than showing entity-level graph graphs or burying cross-server federation in plain text bullet lists, `/` should foreground **how Claire Bible servers form a living knowledge virtuous cycle** — **`적재·정제` → `공유·발행` → `연합 교류 (선별 유입 · 기여 승인 · 원격 KB 색인)` → `환류·확산`** — as a primary visual centerpiece.
- **Public Exposure Boundaries (`[decision]`)**:
  - **Publicly displayed on `/`**: active, non-blocked Claire Bible peer server domains (`kf_known_servers` excluding domain-blocked entries), peer `shared_kb_count` and last read status, and aggregated public exchange counts (`curation` inbound copies, `accepted_contribution` outbound approvals, `remote_knowledge_base` indexed collections, and bridged theme names).
  - **Still strictly private (never exposed publicly)**: domain-blocked servers, private accounts (`profile_public = 0` when unauthenticated), private/hidden/pending KBs or documents, server/BYOK API keys, Jev shadow data, and `/admin/*` diagnostics.

---

## 2. Problem Statement & Visual Sibling Alignment with `/about`

1. **`/` duplicated `/about` instead of presenting network federation**: `public_home.html` previously copied `#about-top`'s instance overview lead, 4 KPI cards, and curator strip above plain cards, making `/` look like an `/about` clone `[code]`.
2. **Sibling visual parity with `/about`**: At the same time, `/`, `/themes`, `/directory`, and `/kb` must share the **exact visual language of `/about`** — `appbar.css` (`.appbar` with glowing `favicon.svg` emblem, `.pub-appbar-nav`, centered `.palette-trigger` `/`, right `.bar-icon-btn` controls), `.about-main` (`max-width: 1120px`), `.about-card`, `.p4-hero-neon` gradient top hairline, `.p4-theatre` obsidian optical glass (`#060b14`–`#0b1424`, emerald-mint `#26ffaa` / electric-azure `#38b6ff` / violet `#a78bfa`), and `.about-footer` — while giving `/` its own dedicated **Federated Knowledge Virtuous-Cycle Optical Canvas** `[proposal]`.
3. **Navigation granularity (`/themes` vs `/kb`)**: Exposing `테마` (`/themes`) and `공유 지식 베이스` (`/kb`) as separate top-level header items alongside `홈` (which already displays active themes) over-fragments the global appbar `[proposal]`.

---

## 3. Navigation Granularity Analysis: Should `/themes` and `/kb` Be Separate Top-Level Menus?

The prototype includes a live **`메뉴`** switcher comparing three top-navigation structures side by side `[proposal]`:

| Menu Model | Top Appbar Items | How `/themes` & `/kb` Are Reached | Assessment |
| :--- | :--- | :--- | :--- |
| **Model A: 4개 + 내부 전환** *(Recommended)* | `홈` (`/`), `공유 지식` (`/kb`), `공개 계정` (`/directory`), `소개` (`/about`) | Both `/kb` and `/themes` highlight `공유 지식` in `.pub-appbar-nav` and share an in-page segmented `<nav aria-label="공유 지식 보기">` switching between `공유 지식 베이스 (/kb)` and `주제별 테마 (/themes)` | Keeps top-level navigation to 4 clear destinations while providing 1-click access to both views |
| **Model B: 4개 (테마 메뉴 없음)** | `홈` (`/`), `공유 지식 베이스` (`/kb`), `공개 계정` (`/directory`), `소개` (`/about`) | `/themes` is entered via `/` (`모든 테마 →`) and theme links, with a `홈 / 테마` breadcrumb on `/themes` | Keeps 4 top-level items matching current `public_appbar_html` |
| **Model C: 5개 분리 (비교용)** | `홈`, `테마`, `공유 지식 베이스`, `공개 계정`, `소개` | Both routes sit side by side in `.pub-appbar-nav` | Kept in the prototype for direct visual comparison |

---

## 4. Public Route Role Separation Matrix

| Route | Primary Role | Visual Centerpiece & Content | Must Exclude |
| :--- | :--- | :--- | :--- |
| **`/`** (Network mode root) | Federated knowledge virtuous-cycle & network discovery | Compact `.p4-hero-neon` network masthead + **Federated Knowledge Virtuous-Cycle Optical Canvas (`.p4-theatre`)** showing active peer Claire Bible servers and `적재·정제 → 공유·발행 → 연합 교류 → 환류·확산` flows + active themes + public accounts | `/about` instance overview prose, 4-box instance KPI grid, curator strip, 5-stage document processing pipeline, supported sources table, blocked domains |
| **`/themes`** | Subject-clustered KB collections | `.p4-hero-neon` page header + active themes (`real`/`fictional` filter, merged/empty excluded), local & remote KB counts, top entities, member KB links | Follow state for non-reviewers, `/reference` link (`Q6`) |
| **`/directory`** | Public account index | `.p4-hero-neon` page header + active public accounts (`@username`, theme/doc counts, theme links, filter/sort, cursor pagination) matching `/about` card & theme tokens | `/reference` link (`Q6`), private/suspended/deleting accounts |
| **`/kb`** | Shared KB catalog | `.p4-hero-neon` page header + shared KBs (`real`/`fictional` & joined filters, join policy, member role/pending state, `@container` responsive table / optional card view) | `/reference` link (`Q6`), personal KBs, direct ingest affordances (`CT4`) |
| **`/about`** *(unchanged)* | Instance & document-processing overview | `p4-hero-neon` hero, 4-box KPI grid + build SHA, curator strip, shared 5-stage Gemini Neon document pipeline, 4 philosophy cards, supported sources table, `/reference` + repo footer | Private accounts/KBs, server/BYOK API keys, Jev/admin internals |

---

## 5. Three Visual Proposals for `/` & Public Pages

All three proposals share the exact `/about` `.appbar`, `.about-main`, `.p4-hero-neon`, `.p4-theatre`, and `.about-footer` visual system in [`prototypes/public_pages_prototype.html`](prototypes/public_pages_prototype.html) `[proposal]`:

| Proposal | `/` Federation Virtuous-Cycle Visual (`.p4-theatre`) | `/themes`, `/directory`, `/kb` Styling | Trade-offs |
| :--- | :--- | :--- | :--- |
| **1안: Optical Orbit (궤도 선순환)** *(Recommended)* | Interactive constellation SVG placing this server at the center of a 4-arc virtuous-cycle ring with orbiting peer Claire Bible servers (`b.example`, `lab.netspheres.example`, `lore.example`, `wiki.openkb.org`), directional animated beams (선별 유입 · 기여 승인 · 원격 KB 색인), flow filters, `#fed-motion-btn`, and live `.p4-glass` peer/phase inspector | 2-column `.about-card` / `.k-card` grids + `@container` responsive `/kb` table | Immediately communicates bidirectional multi-server federation and the self-reinforcing knowledge loop at a glance |
| **2안: Tri-Lane Loop (3단 순환도)** | 3-column spatial optical flow (`① 적재·정제` → `② 공유·발행` ↔ `③ 연합 Claire Bible 서버`) with animated photon conduits, clickable peer nodes, luminous bottom `④ 환류` return bar, and `.p4-glass` inspector | Neon header accents + scannable horizontal row cards on `/directory` + `표 보기`/`카드 보기` toggle on `/kb` | Closest structural sibling to `/about`'s linear optical prism; makes left-to-right stage progression and return feedback explicit |
| **3안: Pulse Ribbon (컴팩트 펄스)** | Compact 4-phase horizontal/stacked ribbon + glowing peer-server optical cards showing bidirectional mint/azure exchange bars and sync badges, keeping vertical footprint small so themes & accounts sit higher | High-density 3-column index cards on `/themes` & `/directory` + compact bordered catalog table on `/kb` | Shortest vertical scroll to reach themes and accounts while still visualizing every peer server's exchange balance |

---

## 6. Modern Web Guidance Patterns Applied

- **`motion`**: All photon pulses, beam flows, and loop sweeps animate exclusively on compositor properties (`transform` and `opacity`) inside `@media (prefers-reduced-motion: no-preference)`, paired with a visible `#fed-motion-btn` (`흐름 정지` / `흐름 재생` without `aria-pressed`) so users can stop persistent motion (WCAG 2.2.2).
- **`accessibility`**: Non-decorative constellation and cycle SVGs declare `role="img"` with `<title>` and `<desc>`; decorative icons carry `aria-hidden="true"`; every route keeps one `<main>`, one `<h1>`, sequential `<h2>`/`<h3>` headings, and keyboard-operable `<button>` peer/phase selectors.
- **`navbar`**: Exact `/about` `.appbar` shell with `.pub-appbar-nav`, centered `.palette-trigger` (`/`), theme toggle (`#preview-theme-btn`), and optional segmented `<nav aria-label="공유 지식 보기">` (`aria-current="page"` with non-color visual indicators and `forced-colors: active` outline).
- **`card` & `responsive-table` & `dark-mode-toggle`**: Semantic `<article>` cards with `.card-link::after` stretched hit areas and `z-index: 1` inner controls; `/kb` `<div tabindex="0" role="region">` tables with sticky headers and `@container (width < 37.5rem)` stacked mobile cards; `:root { color-scheme: light dark; }` with `[data-theme="dark"]` and `:root:has(> head > meta[name="color-scheme"][content="dark"])`.

---

## 7. Open Owner Decisions (`Q1`–`Q5`)

1. **`Q1` (Preferred Federation Visual on `/`)**: Choose **1안 (`Optical Orbit`)**, **2안 (`Tri-Lane Loop`)**, or **3안 (`Pulse Ribbon`)**.
2. **`Q2` (Top Navigation Structure for `/themes` and `/kb`)**: Choose **Model A (`4개 + 내부 전환` — Recommended)**, **Model B (`4개 (테마 없음)`)**, or **Model C (`5개 분리`)**.
3. **`Q3` (`/directory` Layout)**: Prefer 2-column cards (`1안`), scannable horizontal row cards (`2안`), or dense 3-column index cards (`3안`).
4. **`Q4` (`/kb` View Switcher)**: Keep table-only (`1안`/`3안`) or include the `표 보기` / `카드 보기` toggle (`2안`).
5. **`Q5` (Public Footer Policy Outside `/about`)**: Keep `.about-footer` linking to `/about` across `/`, `/themes`, `/directory`, and `/kb` (with `/reference` exclusive to `/about`, per `Q6`).
