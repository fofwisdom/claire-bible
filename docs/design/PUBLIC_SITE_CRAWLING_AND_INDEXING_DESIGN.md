# Public Site: Crawling Control, Page Indexing, and Analytics Tags

- **Status**: Partly implemented. Implemented: `/robots.txt`, `/sitemap.xml`, the metadata of the shared document page, and the GA4 tags. Current behavior: [`PUBLIC_SITE.md`](../implementation/PUBLIC_SITE.md). Planned, not implemented: the items in "Planned work" below.
- **Date**: 2026-10-15 (crawling, DESIGN-SEO-CRAWL-20261015-01); the analytics part comes from the theme FQDN design of 2026-09-16 (DESIGN-THEME-FQDN-20260916-01, removed) · condensed 2026-11-16
- **Related**: [`MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md`](MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md) §5.3 (theme hosts retired, theme GA ID kept) and §6.6 (links and sitemap in network mode), [`MULTI_THEME_ARCHITECTURE_DESIGN.md`](MULTI_THEME_ARCHITECTURE_DESIGN.md)

## Goal

A server can open public themes and shared documents to the internet with anonymous read access. Then three kinds of clients come: search engine crawlers, AI training crawlers, and scanners. The public site must let search engines find and index the public knowledge. It must not let any client index private data, and it must not let crawlers use expensive endpoints.

## Decisions in force

1. **Fail closed for private data.** A private theme or a hidden document never goes into the sitemap. When the server has no anonymous read access, or the operator selects "disallow all", `/robots.txt` blocks all paths and the sitemap is off.
   - Reason: a search index keeps data for a long time. A leak to an index cannot be undone by the server.
2. **Crawlers do not use expensive or changing endpoints.** `/robots.txt` disallows search, ingest, regeneration, LLM authoring, purge, OAuth, MCP, and management paths.
   - Reason: full-text search, vector search, and LLM calls cost CPU and money for each request. A crawler can send many requests.
3. **Index every public document, but not presentations.** The sitemap lists each document that is not hidden in a public theme. If a document has no share link, the server makes one, so that a crawler can reach it. Presentation pages stay out of the sitemap.
   - Reason: the knowledge article is the primary content. A presentation repeats it.
4. **Block AI training crawlers by default.** The operator can turn this off.
   - Reason: the original design records no reason beyond the default itself. The setting keeps the choice with the operator.
5. **Serve the sitemap from a memory cache with a validator.** Repeated crawler requests do not read the database each time.
   - Reason: SQLite readers compete with the writer. Crawlers request the sitemap often.
6. **Give crawlers text without JavaScript, but only escaped text.** The shared document page has a fallback block with the title and summary for clients that do not run scripts. It has a canonical link, a robots directive, and Schema.org data.
   - Reason: the page draws its content with JavaScript. The fallback must never contain raw stored HTML, because a browser with scripts reads the fallback block as raw text and the HTML could escape it.
7. **Analytics: each theme has its own measurement ID, with no fallback.** A page with a theme context uses only the GA4 ID of that theme. When the theme has no ID, the page has no tag. The tag sends the theme ID and label so that one GA4 property can separate themes.
   - Reason: no default value that the owner did not select (PRINCIPLES C1). A theme can have a different owner or purpose from the server.
8. **Analytics: no secret goes to the analytics service.** The page location in the tag has no query string.
   - Reason: share tokens and sign-in tokens are in the query string.
9. **Analytics: the content security policy opens the Google hosts only when a tag can be on the page.** The policy follows the theme that the path selects.
   - Reason: a page with no tag must not be able to load third-party script.

## Rejected alternatives

- **A sitemap and robots file for each theme host.** The first design gave each theme its own host name. The single FQDN design §5.3 retired theme hosts (named O5 in the code comments). Old theme hosts only redirect, so per-host crawl rules are not necessary.
- **Static `robots.txt` file.** It cannot follow the anonymous-read setting or the operator policy.
- **Fallback from a theme GA ID to the server GA ID.** See decision 7.

## Planned work (not implemented)

- **P1. `X-Robots-Tag: noindex, nofollow, noarchive` on private routes and API responses.** Today only some pages send a directive. Reason: a crawler can find an API URL in a link and index the JSON.
- **P2. Sitemap index.** When the number of URLs is more than 50,000 (the protocol limit), serve a `sitemapindex` that points to smaller sitemaps.
- **P3. Site data on the main page.** A canonical link and Schema.org `WebSite` data for the root page.
- **P4. Publisher logo** in the Schema.org data of the shared document page.
- **P5. Network mode sitemap.** List only the public themes of accounts with a public profile, with namespace URLs. The rule is in the single FQDN design §6.6.

## Limits for later changes

- Every new route that is expensive or changes data must also go into the `Disallow` list.
- A new public page must decide its robots directive explicitly.
- Do not add a new analytics provider or a new tracked field without an owner decision.
