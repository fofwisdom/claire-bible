# Public Site: Robots, Sitemap, Page Metadata, and Analytics Tags

Basis: commit d15ae8e0 (2026-11-16) · Readers: agents and operators that change what crawlers and analytics see · Design principles: [`PUBLIC_SITE_CRAWLING_AND_INDEXING_DESIGN.md`](../design/PUBLIC_SITE_CRAWLING_AND_INDEXING_DESIGN.md) · Settings: [`ENVIRONMENT_VARIABLES.md`](ENVIRONMENT_VARIABLES.md) §2.13.1 · Theme boundary: [`THEMES.md`](THEMES.md)

This document tells what the code does now for anonymous visitors and crawlers: `/robots.txt`, `/sitemap.xml`, the metadata of the shared document page, and the Google Analytics 4 (GA4) tags. Evidence tags follow [`PRINCIPLES.md`](../PRINCIPLES.md) F1: [code], [measured], [decision], [unverified].

---

## 1. File map

| File | Role | Main symbols |
| :--- | :--- | :--- |
| `src/claire/api/server.py` | Robots and sitemap handlers, shared document page | `robots_txt_route`, `sitemap_xml_route`, `_sitemap_cache` |
| `src/claire/api/security.py` | Route rules, CSP header | `ROUTE_POLICY`, `_build_content_security_policy`, `SafeAccessLogMiddleware` |
| `src/claire/store/db.py` | Documents and share tokens for the sitemap | `list_public_sitemap_documents`, `create_doc_share` |
| `src/claire/graphview.py` | Page metadata and GA tag | `render_ga_tag`, `render_open_graph_tags`, `shared_html`, `render_graph_html` |
| `src/claire/store/knowledge_base.py` | GA ID of a theme | `validate_ga_measurement_id`, `KnowledgeBaseManager.has_any_ga_enabled` |
| `src/claire/api/namespace.py` | GA ID of the theme that the path pins | `NamespaceResolverMiddleware` |
| `src/claire/config.py` | Settings | `robots_policy`, `block_ai_crawlers`, `sitemap_enabled`, `sitemap_cache_ttl`, `site_name`, `site_description`, `ga_measurement_id`, `anonymous_readonly` |

Tests: `tests/test_crawling_and_sitemap.py`, `e2e/test_crawling_e2e.py`, `tests/test_google_analytics.py`, `tests/test_theme_fqdn.py`.

---

## 2. Route access

- **[code]** `GET` and `HEAD` of `/robots.txt` and `/sitemap.xml` are `system` routes with the `public` rule in `ROUTE_POLICY`. They need no credential.
- **[code]** The `HostRouter` runs before these handlers. Only the app host reaches them. An old theme host (`legacy_hosts`) gets a 308 redirect to the theme path, and an unknown host gets 421 ([`EXTERNAL_ACCESS.md`](EXTERNAL_ACCESS.md)).

---

## 3. `/robots.txt` (`robots_txt_route`)

**[code]** The handler gives `text/plain; charset=utf-8`.

**[code] Block-all case.** The body is `User-agent: *` and `Disallow: /` when one of these is true:
- `anonymous_readonly` is false (`CLAIRE_ANONYMOUS_READONLY=0`).
- `robots_policy` is `disallow_all`. Any other value gives the public case.
- In multi-theme mode, `KnowledgeBaseManager.get_kb_by_fqdn` finds a theme for the `Host` name, and that theme is not public. This index comes from the old `themes.fqdn` values. Because of §2, it matches only when an old theme host is also the app host.

**[code] Public case.** The body has these parts:
1. `User-agent: *` with `Allow` lines for `/$`, `/p?`, `/static/`, `/images/`, `/p/images/`, `/fonts/`, `/favicon.*`.
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

- **[code]** For each theme, `list_public_sitemap_documents(conn, ensure_shares=True, kb_id=...)` reads the documents with `hidden = 0`. For each document it uses the newest share token that is not expired.
- **[code]** When a document has no such token, the function makes one with `create_doc_share(..., reuse_existing=True)`. Thus a sitemap request can write new `doc_shares` rows. If the database is read-only (`sqlite3.OperationalError`), it returns only the existing tokens.
- **[code]** The handler ignores an error of one theme and continues with the other themes.

**[code] XML.** Sitemaps protocol 0.9 (`urlset`). The first `url` is the site root (`changefreq` `daily`, `priority` `1.0`). Then one `url` for each token: `{origin}/p?s={token}`, `changefreq` `weekly`, `priority` `0.8`. `lastmod` is `fetched_at` in UTC (`YYYY-MM-DDTHH:MM:SSZ`), or else the `published_at` text. Presentation pages (`/p/presentation`) are not in the sitemap. There is no sitemap index and no limit on the number of URLs.

**[code] Cache.** `_sitemap_cache` is a dictionary in process memory. The key is the `Host` value. An entry lives for `sitemap_cache_ttl` seconds (default 900). The `ETag` is the MD5 hash of the XML bytes. A request with a matching `If-None-Match` gets 304. The response has `Cache-Control: public, max-age={ttl}`. A change to a document does not clear the cache; the entry expires.

---

## 5. Metadata of HTML pages

### 5.1 Shared document page (`/p?s={token}`, `shared_html`)

- **[code]** A hidden document gives 404.
- **[code]** `render_open_graph_tags` writes:
  - `description`, Open Graph (`og:site_name` is the fixed text `Claire Bible`, `og:type` `article`, title, description, URL, image), `article:*` tags, `keywords` from the knowledge nodes, and Twitter Card tags.
  - `<link rel="canonical">` with `{base}/p?s={token}`.
  - `<meta name="robots" content="index, follow, max-snippet:-1, max-image-preview:large, max-video-preview:-1">`.
  - Schema.org JSON-LD of type `TechArticle`: headline, description, `mainEntityOfPage`, `publisher` (an `Organization` with the name from `site_name`), and, when known, `author`, `datePublished`, `dateModified`, `keywords`. The JSON escapes `<` and `>`.
- **[code]** `base` is `settings.public_url`. When it is empty, the route builds it from the request headers.
- **[code]** The page has a `<noscript>` block with the title and the escaped summary. When there is no summary, it has the plain text of `detail_html` without tags. The raw `detail_html` does not go into `<noscript>`.

### 5.2 Other pages

| Page | Robots directive | Source |
| :--- | :--- | :--- |
| Main application page | `<meta name="robots" content="index, follow">` | `src/claire/templates/index.html` |
| Syndication page | `noindex, nofollow, noarchive` | `src/claire/templates/syndication.html` |
| Login, setup | `noindex, nofollow` | `src/claire/templates/login.html`, `src/claire/templates/setup.html` |
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
- **[code]** A theme ID must match `^(?:G|GTM)-[A-Z0-9]{4,20}$` (`validate_ga_measurement_id`). `POST /themes` and `PATCH /themes` take `ga_measurement_id` (old name `ga_id`). An empty value clears it. The server ID only needs letters, digits, `_`, and `-` (`Settings._parse_ga_measurement_id`).
- Tests: `tests/test_google_analytics.py` (`test_render_graph_html_theme_ga_no_fallback_to_global_settings`), `tests/test_theme_fqdn.py`.

### 6.3 CSP (`SafeAccessLogMiddleware._csp_for`)

**[code]** The Google hosts are in `script-src` and `connect-src` only when a tag can be on the page:
- Path pins a theme: allowed only when that theme has a GA ID (`NamespaceResolverMiddleware` puts the ID in the request state).
- No pin, multi-theme mode: allowed when a theme has a GA ID (`has_any_ga_enabled`). Otherwise the CSP comes from the server ID.
- No pin, single theme mode: allowed when the server ID or a theme GA ID is set.

### 6.4 Events

**[code]** `src/claire/static/js/app.js` and `src/claire/static/js/reader.js` send `page_view` (virtual paths such as `/doc/{id}` and `/node/{id}`), `select_content`, `home_navigate`, `search` (with the search text in `search_term`), and `share` events when `window.gtag` exists.
