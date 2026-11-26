# Handoff: Renditions, Publication, +1, and Opinions

- **Design**: [`DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md`](../design/DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md) (Rev. 11). Its Section 9 is the phase table; Section 11 has the owner decisions O1 to O21.
- **State (2026-11-26)**: phases E0 to E10 are on `main`, not pushed. The last commit of the phases is `f977bf7d`. Section 7 of this document lists what stays open.
- **Purpose**: a person or an agent without the conversation of the implementation can find each decision that the agent made alone, each change from the design, its reason, the code, and the cost to change it back.

This document records decisions and state. The design keeps the intent; [`FEDERATION.md`](FEDERATION.md), [`PERMALINKS.md`](PERMALINKS.md), and [`SCHEMA_VERSIONING.md`](SCHEMA_VERSIONING.md) keep the details of the code. When this document and the code do not agree, the code is correct; then correct this document.

## 1. Reading Order

1. Design Section 11 (owner decisions) and Section 9 (phase table; each row has "Changes" and "Not done").
2. Section 3 of this document (agent decisions AD1 and later, with reasons; the numbers do not change, and a new decision gets the next number).
3. The implementation pages:
   - [`FEDERATION.md`](FEDERATION.md) Sections 4.5 (`replies`, `likes`, `shares`), 4.6 (rendition objects, source index, remote renditions, +1), 4.7 (opinions), 17.1 (fill after a follow), 5.3 and 5.4 (internal endpoints, contract versions).
   - [`PERMALINKS.md`](PERMALINKS.md) Section 3 (page data of the Silvercord page), 3.1a (rendition sub-path), 3.1b (remote rendition frame), 3.1c (opinions route, opinion frame, the 의견 tab).
   - [`KNOWLEDGE_FEDERATION.md`](KNOWLEDGE_FEDERATION.md) (read queue, knowledge outbox, inbox journal, which phases E5 and E9 use).
   - [`SCHEMA_VERSIONING.md`](SCHEMA_VERSIONING.md) (`claire.db` v49 to v56, `client.db` v10 to v13).
   - [`EXTRACTION.md`](EXTRACTION.md) and [`API_AND_MCP_GUIDE.md`](API_AND_MCP_GUIDE.md) (templates, `renditions` of the ingest, the agent client).

## 2. Commits by Phase

| Phase | Commit | Data | Contract | Main code |
| :--- | :--- | :--- | :--- | :--- |
| E0 terms | `979d0ac4` | — | — | catalogs, `GLOSSARY.md` 1.14; removal of `/settings/connections`, `/settings/outbound` |
| E1 quotes | `0ba99c42` | claire.db v49 | 1.18 | `store/db.py` (migration), `federation_quote_routes.py`, sidecar `canQuote` |
| E2 citation | `5c93f55f` | — | — | `api/server.py` (`_document_permalink_page`), `share.html` |
| E6 collections | `741736dd` | client.db v10 | 1.19 | `federation/object_collections.py`, `api/federation_collection_routes.py`, sidecar `object_collections.ts` |
| E3a templates | `b9b3f819` | claire.db v50 | — | `extract/rendition_templates.py`, `extract/prompts.py`, `store/document_renditions.py`, `ingest/pipeline.py` |
| E3b more renditions | `1823b069` | — | — | `ingest/renditions.py`, `ingest/service.py` (`add_renditions`), client extraction, CLI, MCP, agent bundles 0.0.15 |
| E3c picker | `bc410a81` | — | — | `server.py` (`permalink_rendition_route`), `share.html`, `silvercord.css` |
| E4a vocabulary v2 | `83fc27f6` | claire.db v51 | — | `federation/knowledge/vocabulary.py` (`TERMS_V2`), `static/ns/v2.jsonld`, `ingest/source_key.py` |
| E4b rendition objects | `488228c1` | claire.db v52 | 1.20 | `store/federation.py` (`RenditionObject`, `sync_rendition_objects`), sidecar `renditions.ts` |
| E5a source index | `873a4a28` | claire.db v53 | 1.21 | `store/federation.py` (`record_body_reader`), knowledge outbox `delete_rendition` |
| E5b remote renditions | `48878047` | claire.db v54 | — | `federation/remote_renditions.py` |
| E5c remote frame | `19cbe1f6` | — | — | `server.py` (`permalink_remote_rendition_route`), `share.html` |
| E9a +1 at the origin | `91affe4a` | claire.db v55 | 1.22 | `store/plus_ones.py`, knowledge handlers `plus_one`, `undo_plus_one` |
| E9b +1 of accounts | `74178055` | claire.db v56 | — | `federation/plus_one_marks.py`, `api/accounts_routes/rendition_marks.py` |
| two-server test | `91d00031` | — | — | `tests/test_renditions_exchange.py`, `sidecar/fedify/e2e/renditions_exchange_e2e.ts`, `core/config.py` |
| E7a opinions | `17a94e29` | client.db v11 | 1.23 | `store/federation_opinions.py`, `api/federation_opinion_routes.py`, `federation/opinions.py`, sidecar `opinions.ts`, `context.ts` (`walkPosts`) |
| E7b 의견 tab | `91301c97` | — | — | `static/js/silvercord_opinions.js`, `silvercord.js`, `web/pages.py` |
| E7c hide, delete | `0e8bc673` | — | — | `accounts_routes/opinion_moderation.py`, `federation/opinion_scheduler.py`, `federation_routes.py` (`Delete`) |
| E8 daily refresh | `321b679c` | — | — | `federation/opinions.py` (`active_objects`, `daily_refresh`), `opinion_scheduler.py` |
| E7e opinion frame | `9d3180f5` | client.db v12 | — | `server.py` (`permalink_opinion_frame_route`) |
| E7d actions | `1c3d423e` | — | — | `accounts_routes/opinion_actions.py`, `accounts_routes/federation_notes.py` (`in_reply_to_object`) |
| E10 fill | `f977bf7d` | client.db v13 | 1.24 | `store/federation_fills.py`, `api/federation_fill_routes.py`, sidecar `fills.ts`, `syndication_client.js` |

The paths are under `src/claire/`, `src/bible/`, or `sidecar/fedify/src/`.

## 3. Decisions of the Agent and Changes from the Design

Each entry: what the code does, which design text it changes (if one), why, where, and the cost to change it back. The owner did not decide these items. An owner decision can replace each one.

### 3.1 Templates and Ingest (E3)

- **AD1. The source key moved from E3 to E4a.** Design Section 5.3 rules 4 to 6 were in E3. Reason: only published renditions use the source key, and E4 publishes them. Code: `ingest/source_key.py`, `documents.source_key` (claire.db v51). Cost to change back: none now.
- **AD2. `extract_stage(renditions=...)` of MCP is optional; no value means no additional rendition.** The HTTP field `renditions` of `/ingest`, `/ingest-stream`, and the personal ingest of API v1 is required. Reason: agent bundles that are installed now do not send the field and must keep working. Code: MCP tool `extract_stage`, `ingest/renditions.py` (`parse_rendition_selection`). Cost: a required MCP field breaks old bundles.
- **AD3. `DEFAULT_EXTRACTION_TEMPLATE` and the old body prompt are removed.** Each body run takes a required template. Reason: two copies of the body rules would differ in time. Code: `extract/prompts.py`, `extract/rendition_templates.py`.

### 3.2 Silvercord Page (E2, E3c, E5c, E7b, E7e)

- **AD4. The cross-document view transition is skipped for a destination that is not a Silvercord document or rendition page** (`pageswap` handler of `share.html`). Reason: a page that does not opt in (the workspace) makes Chromium abort the transition with a page error. Design Section 5.10 rule 4 has no such case.
- **AD5. The frame of a remote rendition (E5c) and the frame of one opinion (E7e) are on the app origin**, not on the usercontent host (design Section 5.8 rule 4 and Section 5.11 D2). Paths: `/silvercord/{token}/renditions/remote/{key}`, `/silvercord/{token}/opinions/{key}`. Reason: the render token of the usercontent host is of one account, and a reader of a Silvercord page can have no account. The same protection stays: `sandbox` without scripts, an opaque origin, `default-src 'none'`, `frame-ancestors 'self'`, `X-Frame-Options: SAMEORIGIN`, `noindex`. The page policy got `frame-src 'self'`. Cost to change back: a render token for anonymous readers on the usercontent host.
- **AD6. The two frames show no image**; an attachment is a link. Reason: the media proxy works for accounts, and a reader can have no account. The remote rendition frame has a fixed height.
- **AD7. The 의견 tab is a separate script** (`static/js/silvercord_opinions.js`), inlined before `silvercord.js` by `_build_standalone_shared_html` (`web/pages.py`). Reason: `silvercord.js` was already long; the tab has its own state.
- **AD8. "새 의견" marks the opinions that arrived during this visit** (after the first answer of the route). Design rule D8 says "until the next visit"; a reload starts a new visit, so the effect is the same without stored state.
- **AD9. The first answer of the route replaces the loading text without a transition**; only a later answer with new opinions has the element-scoped view transition, and only when the reader permits motion (`prefers-reduced-motion`).

### 3.3 Rendition Objects and Exchange (E4, E5, E9)

- **AD10. Delivered activities have no `claire:` field** (design Section 5.5). The object at its ID has the fields (`withRenditionFields` in the sidecar, built with `Article.fromJsonLd`). Reason: Fedify 2.4.6 compacts each outgoing activity and drops the terms that it does not know. A Claire server reads the object at its ID (design Section 5.4 rule 2), so the exchange works.
- **AD11. Rendition objects are in their own table `federation_rendition_objects`**, not in `federation_objects`. Reason: `federation_objects` has `UNIQUE (kb_id, document_id)`, and SQLite changes a constraint only with a copy of the table. The number space and the path `/ap/objects/{id}` are the same.
- **AD12. `claire:bodyHash`, not `claire:contentHash`, is the hash of a rendition body** (vocabulary v2). Reason: `claire:contentHash` of version 1 is the hash of the source text (`federation/knowledge/vocabulary.py`).
- **AD13. The reads of remote renditions use the read queue of the knowledge federation** (`kf_remote_reads`, kinds `rendition_*`). No sidecar change and no contract change. The `Delete` of a rendition and +1 use the knowledge outbox and the inbox journal.
- **AD14. The cache of remote renditions is in `claire.db`** (design Section 6 said `client.db`). Reason: it is of the server, and the answer of a read and its row change in one transaction.
- **AD15. No `ETag` for bodies**: the reader compares `claire:bodyHash` of the object and reads the body only when it changed.
- **AD16. The +1 marks of the accounts are in `claire.db`** (design Section 6), with the task of the knowledge outbox in the same transaction.
- **AD33. The account purge sends the `Undo` of each +1 with the signature of the server actor** (design Section 5.12 rule 14 says "the server of the account sends `Undo`"; it names no signer). Reason: the keys of a purged account are available only while its `delete_actor` task waits, and the journal of the receiver accepts a signer on the server of the actor of the activity. The actor of the `Undo` stays the account actor; its address comes from the stored activity address. A purge with `skip_federated_delete` sends no `Undo`: that option means "do not inform other servers". Code: `federation/plus_one_marks.py` (`purge_account`), called by `AccountStore.purge`. Test: `tests/test_plus_one_marks.py`, phase `unplus` of `tests/test_renditions_exchange.py`.

### 3.4 Opinions (E7, E8)

- **AD17. The sidecar never reads its own origin.** Thus the fetch of an own object does not start from the object (design rule B4): it starts from the replies that this server knows (delivered and fetched) and walks the reply tree of each one (`walkPosts` in `sidecar/fedify/src/context.ts`).
- **AD18. A quote post is read without a walk** (`quotes` of the task), and a reply to a quote post is not stored. This is design rule B4 ("their own replies are not fetched"); the first version of E7a walked them too.
- **AD19. The fetch of opinions is a separate queue** (`opinion-tasks`, `opinion-objects`, table `federation_opinion_fetches`), not a task kind of the client thread fetch (design rule B9). Reason: the result belongs to the object, not to an account; the server actor signs.
- **AD20. The refresh state has a third status `failed`** (3 leases of 5 minutes without an end). Reason: design rule D8 needs a failure text, and the Async Refreshes of Mastodon have only `running` and `finished`.
- **AD21. "500 under one reply" (rule B5) is 16 levels under each start post**, with 1,000 opinions and `3 × 1000 + 20` requests for one fetch. Reason: `@fedify/backfill` limits depth and items, not the items under one post.
- **AD22. The opinions of a gone object are deleted by a run each hour** (`federation/opinion_scheduler.py`), not in each retract path (design rule C4). Reason: a retract, a gone rendition, a private knowledge base, and an account purge all end in a gone object; the routes show nothing for a gone object before the run. The same run makes the daily refresh of E8 (`opinions_refreshed_at` in `client_meta`).
- **AD23. The page JSON lists the notes of this server that reply to the object or to one of its opinions** (`local: true`). Design rule D1 does not say it; without it, a reply from the page would not show.
- **AD24. The author of the object and a server admin get the hidden opinions** with `hidden: true` and the control "다시 보이기". Design rule D5 says only "hide".

### 3.5 Actions of a Signed-in Account (E7d)

- **AD25. The like of an account of this server for a post of this server is a row of `federation_object_reactions`** with the actor of the account (`activity_iri` `{actor}#likes/local-{number}`) and no activity. Reason: the author is on this server, and the sidecar refuses requests to its own origin. The account purge deletes these rows (`purge_account_of_claire_db`). Only a document object has the like (design Section 5.6 rule 2); the author cannot like an own post (rule 3).
- **AD26. An opinion becomes an item of the timeline of the account, outside the home stream** (`POST /api/federation/opinions/item`, `in_timeline` 0). Reason: the favourite, the boost, the reply, and the quote of the federation client (`FEDERATION.md` Sections 25 and 26) work on items; no new task kind is necessary.
- **AD27. `POST /api/federation/notes` takes `in_reply_to_object`**, the address of a published object of this server, for "이 정리본에 답글" (design rule D9). The note has no mention: the author is on this server.

### 3.6 Fill After a Follow (E10)

- **AD28. The fill queue is its own table `federation_fills` with `fill-tasks` and `fill-objects`**, not a kind `fill_follow` of `client-tasks` (design Section 5.13 rule 8). Reason: the kinds of the client outbox are a `CHECK` of its table (a copy of the table to change), and a fill has its own state (the domain limit, the copy, the 24 hours).
- **AD29. "Replies to actors that the account does not follow" (rule 3)**: a reply stays when the address of its target is under the address of the actor of the fill or of a followed actor, or when the account has the target as an item of such an author. Reason: the normal form has the address of the reply target, not its author; a fetch of each target costs requests.
- **AD30. The view of one actor is `GET /api/federation/timeline?author=`** (one page, newest first by `published`, also items outside the home stream) and a button of each accepted follow in the follow list of `/syndication` (design rule 6 names no route).

### 3.7 Development and Tests

- **AD31. A development name under `.test` has an `https` public URL** (`Settings.public_url`). This follows decision CD4 of `CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md`; before the fix such a server could not start. Found by the two-server test.
- **AD32. The end-to-end test of the exchange uses two real Claire processes and their real sidecar code** in one Deno process: a request to the origin of one server goes to the `handle` function of its sidecar (`sidecar/fedify/e2e/renditions_exchange_e2e.ts`). Phases: `read`, `plus`, `opinions` (also after the daily run), `fill`, `unplus`, `delete`. Each phase starts new sidecars.

## 4. Library Behaviour and the Workarounds

| Library | Behaviour | Workaround | Where |
| :--- | :--- | :--- | :--- |
| Fedify 2.4.6 | Compacts an outgoing activity and drops unknown terms. | `claire:` fields only on the object at its ID (AD10). | sidecar `renditions.ts` |
| Fedify 2.4.6 | After a link that cannot load (for example the reply target on this server), the object instance keeps an empty value; `replyTargetId` became `null` for a start post, and no opinion was stored. | `@fedify/backfill` gets its own instance of each document; the normal form uses the first instance. Test: `context_test.ts` (the own-object walk asserts `in_reply_to`). | `context.ts` (`walkPosts`, `load`) |
| Fedify 2.4.6 | No `attributionDomains` property. | Phase E2b waits. | design E2b |
| `@fedify/backfill` 2.4.6 | Follows `first` and `next` pages of `replies` and `context` (design R15 asked to verify). | None needed. Test: `context_test.ts`. | — |
| Chromium | Aborts a cross-document view transition to a page that does not opt in, with a page error. | `pageswap` skip (AD4). | `share.html` |
| Playwright | Puts its scripts into each frame; a sandbox frame refuses them and logs errors. | The specs filter these messages (`pageErrors` in `e2e/renditions.spec.js`; the frame test of `e2e/silvercord_rendering.spec.js`). | specs |

## 5. Versions

- `claire.db`: v49 (E1) to v56 (E9b). `client.db`: v10 (E6), v11 (E7a), v12 (E7e), v13 (E10).
- Internal federation contract: 1.18 (E1) to 1.24 (E10). Fixtures: `tests/fixtures/federation_contract/` (README lists each version).
- Vocabulary of the knowledge federation: v2 (`/ns/v2`, a superset of v1).
- Agent bundles: 0.0.15 (E3b).

## 6. Tests

- Python: `PATH="$HOME/.deno/bin:$PATH" uv run pytest -n 6`. Without Deno on `PATH`, `tests/test_fedify_sidecar.py` and `tests/test_renditions_exchange.py` are skipped. Baseline: 2 failures that are older than this work (`test_public_directory_authenticated_user_navigation_e2e`; `test_security_hotfixes` `app.js` `(!hid)`).
- Sidecar: `deno task test` and `deno task check` in `sidecar/fedify`. Do not use `deno test -A`: with network permission, Fedify resolves the test hosts in DNS and the tests fail.
- Playwright: only `bash scripts/e2e.sh start` and `wait` (or `run`). With no argument each spec has its own process (about 9 minutes now). Baseline: 3 failures that are older than this work (`line_icons.spec.js`; `workspace.spec.js` 2454 and 3229).
- Main tests of this work: `tests/test_federation_opinions.py`, `tests/test_federation_fills.py`, `tests/test_silvercord_rendering.py`, `tests/test_renditions_exchange.py`, `tests/test_plus_ones.py`, `tests/test_plus_one_marks.py`, `tests/test_federation_rendition_objects.py`, `e2e/renditions.spec.js`, `e2e/silvercord_rendering.spec.js`, `e2e/federation_client.spec.js`, `sidecar/fedify/tests/context_test.ts`.
- For a visual check, a temporary spec in `e2e/` with the mocks of a real spec and `page.screenshot` works through `scripts/e2e.sh`; delete the spec after.

## 7. Work After the Phases

| Item | Design | Commit | Note |
| :--- | :--- | :--- | :--- |
| `Undo` of each +1 at an account purge | 5.12 rule 14 | `db88222b` | AD33. Two-server phase `unplus`. |
| `Like` and `Announce` of a rendition object | 5.6 rule 2 | (this commit) | Stored with the number of the rendition object; its document object gives the account and the access rules. Test through the internal API only: no Claire function sends a `Like` of a remote rendition object, so the two-server test has no such phase. |

## 8. Open Items

| Item | Design | State | Note |
| :--- | :--- | :--- | :--- |
| `attributionDomains` | 5.7 citation rule 4 (E2b) | waits | Fedify 2.4.6 has no property. |
| +1 counts in the published list of `/syndication` | 5.12 rule 13 | not done | |
| Opinion fetch from the view of a published object in `/syndication` | 5.11 B1 | not done | |
| Opinions of a remote rendition | 5.11 C2 | no trigger | The store and the sidecar can do it (`own` false). |
| Notification to the author for a like or a reply from an account of this server | — | not done | Remote likes and replies notify; local ones do not. |
| Prefetch of other renditions and `content-visibility` of long bodies | 5.10 | not done | |
