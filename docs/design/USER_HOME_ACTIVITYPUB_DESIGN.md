# User Home ActivityPub Profile, Timeline, and Knowledge Map Design

- Status: Implemented (conditional Proposal B)
- Related documents:
  - [Home Overview and Context Panel Design](HOME_OVERVIEW_AND_CONTEXT_PANEL_DESIGN.md)
  - [Federation Implementation](../implementation/FEDERATION.md)
  - [Web UI Implementation](../implementation/WEBUI.md)
  - [Visual Prototype](prototypes/user_home_activitypub_prototype.html)

## 1. Goals

This design specifies how the personal User Home (`/@{username}/`) shows the ActivityPub identity and the personal Knowledge Base overview together:

1. Show the account ActivityPub profile (`Person` handle, display name, bio `summary`, Fediverse follow handle copy/dialog trigger, and compose Note dialog for owners) in the User Home header card.
2. Use the short title **Timeline** (`타임라인`) for the outbox timeline section.
3. Apply conditional **Proposal B** layout:
   - **Wide / desktop container (`>= 820px`)**: Full-width ActivityPub Profile card at the top, followed by a two-column workspace grid: Timeline on the left, and Knowledge Base Summary, Recent Ingestion, Knowledge Map (mini-map canvas with **Hubs**, **Bridges**, and **Recent** columns), Decisions, Themes, and Maintenance on the right.
   - **Narrow / mobile container (`< 820px`)**: Full-width ActivityPub Profile card at the top, followed by mobile-only tabs (**Timeline** | **Knowledge Base**) using progressive enhancement (`hidden="until-found"`).
4. Show an **always-public Knowledge Base summary** (`kb_summary`: label, public/private badge, document/entity/relation counts, and last ingestion date) in visitor views even when personal KB `is_public` is `0`, without leaking private document titles, entities, articles, or `/graph` and `/documents` routes.
5. Add editable account bio `summary` backed by `claire.db` (`accounts.summary`, schema v42) and federated as escaped HTML `<p>...</p>` on ActivityPub `Person.summary` (internal federation contract `1.16`).
6. Exclude personal ActivityPub Profile, Timeline, and mobile tabs on Shared KBs (`/kb/{number}/`).

## 2. Account Bio `summary` and ActivityPub Federation

- **Storage**: `accounts.summary TEXT NOT NULL DEFAULT ''` in `claire.db` (schema version 42).
- **Validation**: `validate_summary` in `src/claire/api/accounts_routes/common.py` accepts plain text up to 500 characters (`SUMMARY_MAX_LENGTH = 500`) and rejects disallowed control characters.
- **Edit surface**:
  - API: `PATCH /api/me` accepts `{"summary": ...}` and `me_payload` returns `"summary"`.
  - Console UI: `/account/profile` renders a top-level `.console-subcard` (`#settings-summary`) managed by `console_account.js`.
- **Federation**:
  - When `AccountStore.update_summary()` changes `summary` on an account with an active federation actor, it enqueues `update_actor` (`Update(Person)`).
  - Internal federation API (`1.16`) adds nullable `summary: string | null` on `ActorRecord`.
  - The Fedify sidecar escapes HTML entities and wraps non-empty text in `<p>...</p>` (with `<br>` for line breaks) on ActivityPub `Person.summary`, and omits `summary` when empty.

## 3. Conditional Proposal B Layout (`/@{username}/`)

- **Container query**: `.home-profile-layout` / `.home-b-grid` enables the two-column desktop grid when container width is at least `820px`, and hides `#home-mobile-tabs`.
- **Narrow viewport (`< 820px`)**: `#home-mobile-tabs` becomes visible with two tabs (`Timeline` and `Knowledge Base`), switching panels while preserving in-page search via `hidden="until-found"`.
- **Knowledge Map**: Preserves `#home-map` with `#home-map-col-hubs` (Hubs), `#home-map-col-bridges` (Bridges), and `#home-map-col-recent` (Recent), plus the interactive canvas preview.
- **Shared KB exclusion**: When `ns.pinned_kb_id` is set (`/kb/{number}/`), `GET /home-profile` and `GET /home-timeline` return `404`, and `ClaireUserHome` hides `#home-ap-profile`, `#home-ap-timeline`, and `#home-mobile-tabs`.

## 4. Public Endpoints and Visitor Privacy Boundary

- `GET /home-profile` (`/@{username}/home-profile`):
  - Returns `{"actor": ..., "kb_summary": ...}` for an active personal account with `Cache-Control: no-store`.
  - `kb_summary` (`label`, `is_public`, `documents`, `entities`, `relations`, `last_ingest_at`) is always returned regardless of `personal_kb.is_public`.
  - Exposes no private document lists, entity names, or article items when `is_public` is `0`.
- `GET /home-timeline` (`/@{username}/home-timeline`):
  - Supports `limit` (1 to 50, default 20), `max_id` (cursor), and `kind` (`all`, `note`, `article`).
  - Returns public `Note` items and public `Article` entries when personal KB `is_public` is `1`, or public `Note` items only when `is_public` is `0`.
- **Private Visitor SSR (`private_profile.html`)**:
  - Sets `data-profile-state="private"` when `is_public` is `0` for non-owners.
  - Renders SSR Actor Profile, `summary`, always-public KB Summary card (`#uh-private-kb-summary`), and public `Note` Timeline (hydrated by `user_home_ap.js`), while `/graph` and `/documents` keep returning `404`.
