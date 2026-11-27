# Public Site: Robots, Sitemap, Page Metadata, and Analytics Tags

Basis: 2026-11-27 · Readers: agents and operators that change what visitors, crawlers, and analytics see · Design principles: [`PUBLIC_SITE_CRAWLING_AND_INDEXING_DESIGN.md`](../design/PUBLIC_SITE_CRAWLING_AND_INDEXING_DESIGN.md), [`PUBLIC_ROOT_DIRECTORY_KB_DESIGN.md`](../design/PUBLIC_ROOT_DIRECTORY_KB_DESIGN.md) · Settings: [`ENVIRONMENT_VARIABLES.md`](ENVIRONMENT_VARIABLES.md) §2.12.1 · Theme boundary: [`THEMES.md`](THEMES.md)

This document tells what the code does now for anonymous visitors and crawlers: `/robots.txt`, `/sitemap.xml`, the metadata of the shared document page, and the Google Analytics 4 (GA4) tags. Evidence tags follow [`PRINCIPLES.md`](../PRINCIPLES.md) F1: [code], [measured], [decision], [unverified].

---

## 1. File map

| File | Role | Main symbols |
| :--- | :--- | :--- |
| `src/claire/api/server.py` | Robots, sitemap, directory/profile nav injection, network-mode root page, unified directory, `/themes` API, `/kb` HTML 308 redirect | `robots_txt_route`, `sitemap_xml_route`, `_sitemap_cache`, `public_directory_route`, `themes_collection_route`, `kb_collection_route` |
| `src/claire/api/routes/public_observatory.py` | Calm Federated Observatory snapshot builder and HTML renderers for `/` | `build_federation_observatory_snapshot`, `render_observatory_stage_html`, `render_observatory_themes_html` |
| `src/bible/static/css/public_observatory.css` | Styles for `/`, unified `/directory`, and shared public light/dark pages | `.obs-stage`, `.obs-flow-grid`, `.obs-reflux`, `.obs-hovercard`, `.obs-directory-main` |
| `src/bible/static/js/public_observatory.js` | Hovercard ARIA/fallback wiring and responsive Bezier conduit drawing | `wireHovercards`, `drawObservatoryCurves` |
| `src/claire/api/routes/public_about.py` | Public `/about` page route, shared footer context, KD15 snapshot | `build_public_about_routes`, `build_public_hero_context`, `_collect_public_snapshot` |
| `src/claire/api/templates/public_about.html` | Public `/about` HTML template | `#about-top`, `#pipeline-graph`, `#about-sources` |
| `src/claire/api/templates/public_home.html` | Public network-mode root `/` HTML template | `#home-main`, `#public-home-showcase`, `#home-observatory`, `#home-themes`, `#public-home-recent`, `#home-directory-link` |
| `src/claire/api/templates/public_directory.html` | Unified public `/directory` HTML template | `#directory-main`, `.obs-page-head`, `.obs-directory-grid`, `.about-footer` |
| `src/claire/api/templates/public_kb.html` | Public `/themes*` and `/kb/{number}/` Observatory shell HTML template | `#public-kb-root.obs-main.obs-themes-main`, `.about-footer`, `command_palette.js` |
| `src/claire/api/public_kb_routes.py` | Shared 4-item public appbar and `/themes*` + `/kb/{number}/` page renderer | `public_appbar_html`, `render_public_kb_page`, `directory_head_markup` |
| `src/bible/static/js/public_kb.js` | Client views for `/themes*`, `/kb/{number}/`, and `#public-home-recent` | `renderThemesList`, `renderThemeDetail`, `renderKbDetail`, `renderHomeRecent` |
| `src/claire/api/accounts_routes/pipeline.py` | Shared Gemini Neon pipeline serializer and mount | `build_pipeline_view_data`, `render_pipeline_mount_html`, `resolve_primary_language_info` |
| `src/claire/accounts/username_policy.py` | Username reservation policy (`"about"` reserved) | `RESERVED_USERNAMES` |
| `src/claire/api/security.py` | Route rules, CSP header | `ROUTE_POLICY`, `_build_content_security_policy`, `SafeAccessLogMiddleware` |
| `src/claire/store/db.py` | Documents and share tokens for the sitemap | `list_public_sitemap_documents`, `create_doc_share` |
| `src/bible/web/metadata.py` | Pure page metadata and GA tag builders | `render_ga_tag`, `render_open_graph_tags`, `sanitize_preview_text`, `extract_knowledge_node_tags`, `resolve_preview_image` |
| `src/claire_bible/web/pages.py` | Request and database page assembly | `shared_html`, `render_graph_html`, `render_syndication_html` |
| `src/claire/store/knowledge_base.py` | GA ID of a theme | `validate_ga_measurement_id`, `KnowledgeBaseManager.has_any_ga_enabled` |
| `src/claire/api/namespace.py` | GA ID of the theme that the path pins | `NamespaceResolverMiddleware` |
| `src/claire/core/config.py` | Settings | `robots_policy`, `block_ai_crawlers`, `sitemap_enabled`, `sitemap_cache_ttl`, `site_name`, `site_description`, `ga_measurement_id`, `anonymous_readonly` |

Tests: `tests/test_crawling_and_sitemap.py`, `e2e/test_crawling_e2e.py`, `tests/test_google_analytics.py`, `tests/test_theme_fqdn.py`, `tests/test_public_about.py`, `tests/test_public_home.py`, `tests/test_public_directory.py`, `e2e/public_about.spec.js`, `e2e/public_home.spec.js`, `e2e/public_directory.spec.js`, `e2e/kb_public_pages.spec.js`.

---

## 2. Route access

- **[code]** `GET` and `HEAD` of `/robots.txt`, `/sitemap.xml`, and `/about` are `system` routes with `_system("public", "none")` in `ROUTE_POLICY`. They need no credential. `"about"` is also reserved in `RESERVED_USERNAMES` (`src/claire/accounts/username_policy.py`) so no account can claim `/about` as a handle.
- **[code]** The `HostRouter` runs before these handlers. Only the app host reaches them. An old theme host (`legacy_hosts`) gets a 308 redirect to the theme path, and an unknown host gets 421 ([`EXTERNAL_ACCESS.md`](EXTERNAL_ACCESS.md)).

---

## 3. `/robots.txt` (`robots_txt_route`)

**[code]** The handler gives `text/plain; charset=utf-8`.

**[code] Block-all case.** The body is `User-agent: *` and `Disallow: /` when one of these is true:
- `anonymous_readonly` is false (`CLAIRE_ANONYMOUS_READONLY=0`).
- `robots_policy` is `disallow_all`. Any other value gives the public case.
- In multi-theme mode, `KnowledgeBaseManager.get_kb_by_fqdn` finds a theme for the `Host` name, and that theme is not public. This index comes from the old `themes.fqdn` values. Because of §2, it matches only when an old theme host is also the app host.

**[code] Public case.** The body has these parts:
1. `User-agent: *` with `Allow` lines for `/$`, `/silvercord/`, `/static/`, `/images/`, `/p/images/`, `/fonts/`, `/favicon.*`.
2. `Disallow` lines for search, write, authoring, purge, OAuth, MCP, support bundle, graph bundle, statistics, limits, providers, `/themes`, and `/syndication` paths. The full list is in the handler.
3. When `block_ai_crawlers` is true (default): one group with `User-agent` lines for `GPTBot`, `ChatGPT-User`, `ClaudeBot`, `anthropic-ai`, `CCBot`, `Bytespider`, `PerplexityBot`, `Cohere-ai`, `Diffbot`, `FacebookBot`, and `Disallow: /`.
4. A `Sitemap:` line with the absolute URL of `/sitemap.xml`. The handler builds it from the request.

**[code]** The `Disallow` list has no line for `/admin`, `/settings`, `/api`, or `/@{username}/` paths.

---

## 4. `/sitemap.xml` (`sitemap_xml_route`)

**[code] Off cases.** The handler gives 404 when `sitemap_enabled` is false, when `anonymous_readonly` is false, or when the `Host` name matches a theme that is not public (the same index as §3).

**[code] Which documents.**

| Mode | Themes in the sitemap |
| :--- | :--- |
| Single theme mode (`CLAIRE_MULTI_THEME=0`) | Theme 0 |
| Multi-theme mode, `Host` matches a theme | That theme |
| Multi-theme mode, other `Host` | All themes with `is_public`, of all accounts (`KnowledgeBaseManager.list_kbs()`) |

- **[code]** For each theme, `list_public_sitemap_documents(conn, kb_id=...)` reads the documents with `hidden = 0` and the address token of each one from the permalink registry ([`PERMALINKS.md`](PERMALINKS.md)).
- **[code]** When a document has no row in the registry, the function makes one (no change of link access). Thus a sitemap request can write new `permalinks` rows. If the database is read-only (`sqlite3.OperationalError`), such a document is not in the result.
- **[code]** The handler ignores an error of one theme and continues with the other themes.

**[code] XML.** Sitemaps protocol 0.9 (`urlset`). The first `url` is the site root (`changefreq` `daily`, `priority` `1.0`). Then one `url` for each token: `{origin}/silvercord/{token}`, `changefreq` `weekly`, `priority` `0.8`. `lastmod` is `fetched_at` in UTC (`YYYY-MM-DDTHH:MM:SSZ`), or else the `published_at` text. Presentation pages (`/p/presentation`) are not in the sitemap. There is no sitemap index and no limit on the number of URLs.

**[code] Cache.** `_sitemap_cache` is a dictionary in process memory. The key is the `Host` value. An entry lives for `sitemap_cache_ttl` seconds (default 900). The `ETag` is the MD5 hash of the XML bytes. A request with a matching `If-None-Match` gets 304. The response has `Cache-Control: public, max-age={ttl}`. A change to a document does not clear the cache; the entry expires.

---

## 5. Metadata of HTML pages

### 5.1 Item view of a document (`/silvercord/{token}`, `shared_html`)

- **[code]** A hidden document gives 404.
- **[code]** `render_open_graph_tags` in `src/bible/web/metadata.py` writes:
  - `description`, Open Graph (`og:site_name` is the fixed text `Claire Bible`, `og:type` `article`, title, description, URL, image), `article:*` tags (including `article:tag`), `keywords` from the knowledge nodes, and Twitter Card tags.
  - `<link rel="canonical">` with `{base}/silvercord/{token}`.
  - `<meta name="robots" content="index, follow, max-snippet:-1, max-image-preview:large, max-video-preview:-1">`. When only link access makes the document readable (the knowledge base is not readable for the caller), the route changes it to `noindex, nofollow` and sends `X-Robots-Tag: noindex, nofollow` ([`PERMALINKS.md`](PERMALINKS.md) §3).
  - Schema.org JSON-LD of type `TechArticle`: headline, description, `mainEntityOfPage`, `publisher` (an `Organization` with the name from `site_name`), and, when known, `author`, `datePublished`, `dateModified`, `keywords`. The JSON escapes `<` and `>`. `isBasedOn` (a `CreativeWork` with the source URL of the document) when the document has an HTTP source URL.
  - Citation tags that the permalink route adds (`render_citation_tags` in `src/claire/api/citation_tags.py`; design [`DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md`](../design/DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md) Section 5.7): `<link rel="cite-as">` with the permalink (RFC 8574) on each document page; on a page that search engines can index (the caller can read the knowledge base) also `citation_title`, `citation_author` (the display name, else `@username`, of the account of a personal knowledge base; absent for a shared knowledge base), `citation_publication_date` (`YYYY/MM/DD` of the publication, else of the ingest), `citation_public_url`, and `citation_language` (`ko`: the text that Claire makes is Korean); and `<meta name="fediverse:creator" content="@username@host">` for a published document. The page has no control for these data. The actor document has no `attributionDomains` yet, so Mastodon does not show the byline now (the design gives the reason). Test: `tests/test_silvercord_rendering.py`.
- **[code]** Initial `#docdata` strips `nodes` for all callers (`render_shared_page` in `src/bible/web/pages.py` calls `render_open_graph_tags` before `doc_copy.pop("nodes", None)`). On public KBs (`readable_kb` is true), `render_open_graph_tags` still emits `article:tag` and `keywords` in `<head>`. On `link_only` and private KB pages (`not readable_kb`), `permalink_route` in `src/claire/api/server.py` pops `nodes` before calling `shared_html`, so entity tags are omitted from both `<head>` metadata and `#docdata`.
- **[code]** `base` is `settings.public_url`. When it is empty, the route builds it from the request headers.
- **[code]** The page has a `<noscript>` block with the title and the escaped summary. When there is no summary, it has the plain text of `detail_html` without tags. The raw `detail_html` does not go into `<noscript>`.

### 5.2 Other pages

| Page | Robots directive | Source |
| :--- | :--- | :--- |
| Main application page | `<meta name="robots" content="index, follow">` | `src/bible/templates/index.html` |
| Syndication page | `noindex, nofollow, noarchive` | `src/bible/templates/syndication.html` |
| Login, setup | `noindex, nofollow` | `src/claire/api/templates/login.html`, `src/claire/api/templates/setup.html` |
| `/silvercord/{token}/graph` | Header `X-Robots-Tag: noindex, nofollow` (`Cache-Control: private, no-store`) | `src/claire/api/server.py` (`permalink_graph_route`) |
| `/invite/{code}` | Header `X-Robots-Tag: noindex, nofollow` | `src/claire/api/accounts_routes/invites.py` |
| Prototype host | Header `X-Robots-Tag: noindex, nofollow` | `src/claire/api/prototype_host.py` |

- **[code]** API and other routes have no `X-Robots-Tag` header. The main application page has no canonical link and no JSON-LD.
- **[code]** No code reads `site_description` (`CLAIRE_SITE_DESCRIPTION`).

---

## 6. Google Analytics 4 tags

### 6.1 Tag (`render_ga_tag`)

**[code]**
- The function gives an empty string when the ID is empty or has characters other than letters, digits, `_`, and `-`.
- Otherwise it loads `gtag.js` from `www.googletagmanager.com` and calls `gtag("config", id, {...})` with:
  - `page_location`: `origin + pathname`. On the shared document page it is `origin + '/p/{doc_id}'`. This value has no query string (`?s=`, `?t=`).
  - `cookie_domain`: the host name of the page. `cookie_flags`: `SameSite=Lax;Secure`.
  - `kb_id` and `theme_label`, only when the caller gives a theme.

### 6.2 Which measurement ID

| Page | ID |
| :--- | :--- |
| Main page and syndication page with a theme context (`render_graph_html`, `render_syndication_html` with `theme`) | The `ga_measurement_id` of that theme. When it is empty, there is no tag. The server ID is not used. |
| Same pages without a theme context | `CLAIRE_GA_MEASUREMENT_ID` (`Settings.effective_ga_measurement_id`) |
| Shared document page (`shared_html`) | `CLAIRE_GA_MEASUREMENT_ID` in all modes |

- **[code]** The main page route gets a theme context in multi-theme mode and when the path pins a theme (`/themes/{number}/`).
- **[code]** A theme ID must match `^(?:G|GTM)-[A-Z0-9]{4,20}$` (`validate_ga_measurement_id`). `POST /kb` and `PATCH /kb/{number}/` (or `PATCH /@{u}/`) take `ga_measurement_id`. An empty value clears it. The server ID only needs letters, digits, `_`, and `-` (`Settings._parse_ga_measurement_id`).
- Tests: `tests/test_google_analytics.py` (`test_render_graph_html_theme_ga_no_fallback_to_global_settings`), `tests/test_theme_fqdn.py`.

### 6.3 CSP (`SafeAccessLogMiddleware._csp_for`)

**[code]** The Google hosts are in `script-src` and `connect-src` only when a tag can be on the page:
- Path pins a theme: allowed only when that theme has a GA ID (`NamespaceResolverMiddleware` puts the ID in the request state).
- No pin, multi-theme mode: allowed when a theme has a GA ID (`has_any_ga_enabled`). Otherwise the CSP comes from the server ID.
- No pin, single theme mode: allowed when the server ID or a theme GA ID is set.

### 6.4 Events

**[code]** `src/bible/static/js/app.js` and `src/bible/static/js/reader.js` send `page_view` (virtual paths such as `/doc/{id}` and `/node/{id}`), `select_content`, `home_navigate`, `search` (with the search text in `search_term`), and `share` events when `window.gtag` exists.

---

## 7. Public `/`, `/themes`, `/directory`, and `/about` Pages and Navigation

Design: [`ABOUT_PAGE_AND_INTERACTIVE_PIPELINE_DESIGN.md`](../design/ABOUT_PAGE_AND_INTERACTIVE_PIPELINE_DESIGN.md) (`Q1`–`Q7`), [`PUBLIC_ROOT_DIRECTORY_KB_DESIGN.md`](../design/PUBLIC_ROOT_DIRECTORY_KB_DESIGN.md) (`DESIGN-PUBLIC-ROOT-DIRECTORY-KB-20261127`), and [`KNOWLEDGE_FEDERATION_DESIGN.md`](../design/KNOWLEDGE_FEDERATION_DESIGN.md) (`KD15` revision, 2026-11-27).

### 7.1 `/about` Route and Privacy Guarantees

- **[code]** `build_public_about_routes` in `src/claire/api/routes/public_about.py` handles `GET` and `HEAD` `/about` for anonymous visitors using `src/claire/api/templates/public_about.html` (`Vary: Cookie, Authorization, Accept-Language`). `build_public_hero_context` builds footer links (`_build_footer_links_html`) shared with `/`.
- **[code] [decision]** `_collect_public_snapshot` enforces public viewer isolation on `/about`:
  - **KPI Box 1**: server mode (`network` or `single`) and private invite-only registration policy (`about.kpi.mode_*`).
  - **KPI Box 2**: active public profiles (`accounts.profile_public = 1 AND status = 'active'`), public shared knowledge bases (`is_personal = 0 AND is_public = 1`), and active themes (`themes.state = 'active'`).
  - **KPI Box 3 (`Q3`)**: public-only extracted documents (`COALESCE(hidden, 0) = 0 AND COALESCE(extraction_status, 'extracted') = 'extracted'` in public knowledge bases) and non-provisional entities (`COALESCE(provisional, 0) = 0`), excluding private accounts, private knowledge bases, and `pending` staged documents.
  - **KPI Box 4 (`Q7` Option A)**: 7-character short build commit SHA from `CLAIRE_BUILD_COMMIT` or `/app/src/claire/BUILD_COMMIT`, omitted when unset or `unknown`.
  - **Curator strip**: owner `display_name` (or `username`) from `accounts` (`role = 'admin'`) and optional ActivityPub handle `@{username}@{expected_authority}`, shown once in `.about-meta-strip` and not repeated in the footer.
  - **Never exposed on `/about`**: private account or private KB counts, `pending` staged documents, federated peer server lists or counts, server/BYOK API keys, `jev-latest`, `gemini-decision-1`, recent document lineages, or `/admin/*` URLs.
- **[code]** `resolve_primary_language_info` reads `read_server_languages()` (`src/claire/accounts/languages.py`, fallback `en`) and maps the primary language tag (normalizing `-` to `_` via `lang.name.*`) into the hero lead (`about.hero.lead`) and `pipeline_view_data["primaryLangName"]`.
- **[code] [decision]** `/about` mounts the shared Gemini Neon component (`build_pipeline_view_data(viewer="public", ...)` + `render_pipeline_mount_html`, using generic `pipeline.neon.model_slot` labels and `showJev: False`), four philosophy cards (`about.philosophy.*`), and one always-visible supported sources table (`#about-sources`, `_SOURCE_ROW_KEYS`). Closed ontology lists (`11+11`) and 3-template rendition policy details are omitted from `/about` (`Q6`).

### 7.2 Network-Mode `/` Observatory and 2026-11-27 KD15 Revision

- **[code] [decision]** Under the 2026-11-27 `KD15` revision, server-list concealment is retired on `/`. Network-mode `/` renders the Calm Federated Observatory instead of `/about`'s dark-obsidian `.p4-theatre` / `.p4-hero-neon` hero.
- **[code]** `public_directory_route(..., root_page=True)` in `src/claire/api/server.py` calls `build_federation_observatory_snapshot`, `render_observatory_stage_html`, and `render_observatory_themes_html` in `src/claire/api/routes/public_observatory.py`, appends `#public-home-recent-data` + `#public-home-recent`, and renders `src/claire/api/templates/public_home.html` with `ETag`, `304`, `HEAD`, `Vary` (including `Accept-Language`), and `Referrer-Policy`.
- **[code]** `build_federation_observatory_snapshot` reads only public instance counts, active themes, recent cross-server exchanges, active `kf_known_servers`, and `kf_remote_knowledge_bases`. It filters out any peer domain matched by `federation_store.blocking_domain`, and never emits `software_name`, `software_version`, `last_error`, `added_by`, `server_actor_iri`, private accounts, private/hidden/pending KBs or documents, provisional entities, API keys, Jev data, or `/admin/*` paths.
- **[code]** Tier 1 (`#home-observatory`) renders `.obs-header` (instance domain, active/standby badge, and tabular metrics), the 3-column `.obs-flow-grid` (local facts, up to 4 bridged themes, up to 5 active peer servers or cold-start standby slot), the `.obs-reflux` return arc, and declarative `popover="hint"` hovercards wired with `interestfor`. `src/bible/static/js/public_observatory.js` sets `role="tooltip"` and `aria-describedby` upfront, adds fallback positioning when native `interestfor` or CSS anchor positioning (`@supports (position-area: block-end)`) is unavailable, and draws SVG Bezier flow edges inside `.obs-flow-shell`.
- **[code]** Tier 2 renders `#home-themes` (up to 6 active themes in `.obs-theme-grid`), `#public-home-recent`, and `#home-directory-link` (compact `.obs-directory-strip` linking to `/directory`, or cold-start empty state with `"There are no public accounts"` when `public_profiles == 0` and no active themes exist).
- **[code]** `src/bible/static/css/public_observatory.css` provides theme-native `light` / `dark` tokens (`color-scheme: light dark`, `color-mix(in oklab, ...)`), `@media (prefers-reduced-motion: no-preference)` for `@view-transition { navigation: auto; }` and conduit motion, non-color stroke cues (`WCAG 1.4.1`), and `@media (forced-colors: active)` overrides.

### 7.3 Unified `/directory`, `/themes` & `/kb/{number}/` Shell, and Public 4-Item Header Navigation

- **[code] [decision]** (`Q2`, `Q6`, `DESIGN-PUBLIC-ROOT-DIRECTORY-KB-20261127`) Public navigation bars use a single 4-item row, retire the `/kb` public HTML catalog (`308 -> /themes`), and keep `/kb`, `.obs-view-switch`, and `/reference` out of public UI links:
  - `public_appbar_html` (`src/claire/api/public_kb_routes.py`) renders `.pub-appbar-nav` with `/` (`kb.pub.nav_home`), `/themes` (`kb.pub.nav_themes`), `/directory` (`kb.pub.nav_accounts`), and `/about` (`nav.about`), plus `/login` for anonymous visitors, across `/`, `/themes*`, `/kb/{number}/`, `/directory`, and `/about`.
  - `kb_collection_route` (`src/claire/api/server.py`) returns `308` to `/themes` (keeping query parameters) when `ns.account_id is None` and `collections.wants_html(request)` is true, while keeping `GET /kb` JSON API, `POST /kb`, `/@{u}/kb`, and `/kb/{number}/` active.
  - `render_public_kb_page` supports `PUBLIC_VIEWS = ("kb_detail", "themes_list", "theme_detail")`, sets `active_nav = "themes"`, and renders `src/claire/api/templates/public_kb.html` with `<meta name="text-scale" content="scale"/>`, `public_observatory.css`, `<main id="public-kb-root" class="obs-main obs-themes-main">`, `.about-footer` (`$footer_links_html` linking `/`, `/themes`, `/directory`, and `/about`, with no `/reference` link), and `command_palette.js`.
  - `src/bible/static/js/public_kb.js` omits `.claire-pubnav` when `header.appbar .pub-appbar-nav` exists, omits `.obs-view-switch` and `/kb` links, and points `/kb/{number}/` breadcrumbs back to `/themes` (`kb.pub.theme_back`). `renderThemesList` renders `.obs-page-head` (Serif `h1` and `.obs-metrics` summary), `.obs-catalog-toolbar` (knowledge-type filter chips and authenticated `#pub-th-create-kb` only), and `.obs-themes-catalog-grid` with `.obs-catalog-card`, `.obs-catalog-entities`, and `.obs-catalog-peers` pills. `renderThemeDetail` and `renderKbDetail` also render `.obs-page-head`. `src/bible/static/js/console_kb.js` links back to `/themes` instead of `/kb`.
  - `themes_collection_route` (`GET /themes` JSON) enriches each active theme with public `top_entities` (first 4), `peer_servers` (sorted unique active non-blocked remote KB domains), and `last_activity_at` while keeping existing fields intact.
  - `public_directory_route(..., root_page=False)` renders `src/claire/api/templates/public_directory.html` via `accounts_common.render_template` inside the unified light/dark shell (`public_observatory.css`, `public_appbar_html(active="directory")`, `#directory-main.obs-main.obs-directory-main`, `.obs-page-head`, `.obs-directory-grid`, and `.about-footer` with no `/reference` link).
  - `src/claire/api/server.py` injects `about_label` (`nav.about`) into `src/claire/api/templates/private_profile.html`; `src/bible/templates/partials/common_appbar.html` includes `#menuaboutlink` inside `#pop-hamburger-menu`.
  - `_build_footer_links_html` in `src/claire/api/routes/public_about.py` renders `API 문서` / `API Docs` (`/reference`), plus non-empty `effective_github_repository` (`about.footer.github`) and `agent_marketplace_repo` (`about.footer.marketplace`) links in `.about-footer-links`, omitting empty repository links and omitting `MIT`, `v0.1.0`, `/robots.txt`, and `/mcp`.
