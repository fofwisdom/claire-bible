# Ideas: Public Site Indexing

- **Status**: Ideas only. They have no design for implementation. Do not implement from this document. A design starts only after an owner decision.
- **Date**: 2026-11-20
- **Source**: Taken out of [`PUBLIC_SITE_CRAWLING_AND_INDEXING_DESIGN.md`](../design/PUBLIC_SITE_CRAWLING_AND_INDEXING_DESIGN.md) (section "Planned work") on 2026-11-20 (owner). The design document keeps the principles of the implemented part.

---

- **P1. `X-Robots-Tag: noindex, nofollow, noarchive` on private routes and API responses.** Today only some pages send a directive. Reason: a crawler can find an API URL in a link and index the JSON.
- **P2. Sitemap index.** When the number of URLs is more than 50,000 (the protocol limit), serve a `sitemapindex` that points to smaller sitemaps.
- **P3. Site data on the main page.** A canonical link and Schema.org `WebSite` data for the root page.
- **P4. Publisher logo** in the Schema.org data of the shared document page.
- **P5. Network mode sitemap.** List only the public themes of accounts with a public profile, with namespace URLs. The rule is in the single FQDN design §6.6.
