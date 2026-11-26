# Handoff: Renditions, Publication, +1, and Opinions

- **Design**: [`DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md`](../design/DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md) (Rev. 11). Its Section 9 is the phase table; Section 11 has the owner decisions O1 to O23.
- **State (2026-11-26)**: phases E0 to E10 and the work after them (Section 7) are on `main`, not pushed. The last commit of the phases is `f977bf7d`; Section 7 lists the later commits. Section 8 lists what stays open.
- **Later work of the same sequence**: after the renditions design, the same work went on with the acceptance of the knowledge federation (tests with real sidecars, the check of the JSON-LD context, the development network of two servers with KA1 to KA3), a fix of the master key file, and the type check. Section 11 has its commits, its decisions (AD43 and later), and its open items.
- **Purpose**: a person or an agent without the conversation of the implementation can find each decision that the agent made alone, each change from the design, its reason, the code, and the cost to change it back.

This document records decisions and state. The design keeps the intent; [`FEDERATION.md`](FEDERATION.md), [`PERMALINKS.md`](PERMALINKS.md), and [`SCHEMA_VERSIONING.md`](SCHEMA_VERSIONING.md) keep the details of the code. When this document and the code do not agree, the code is correct; then correct this document.

## 1. Reading Order

1. Design Section 11 (owner decisions) and Section 9 (phase table; each row has "Changes" and "Not done").
2. Section 3 of this document (agent decisions AD1 and later, with reasons; the numbers do not change, and a new decision gets the next number).
3. The implementation pages:
   - [`FEDERATION.md`](FEDERATION.md) Sections 4.5 (`replies`, `likes`, `shares`), 4.6 (rendition objects, source index, remote renditions, +1, reactions of rendition objects), 4.7 (opinions, also of remote renditions), 17.1 (fill after a follow), 19.1 (`?author=`), 5.3 and 5.4 (internal endpoints, contract versions).
   - [`PERMALINKS.md`](PERMALINKS.md) Section 3 (page data of the Silvercord page), 3.0a (prefetch, long bodies), 3.1a (rendition sub-path), 3.1b (remote rendition frame), 3.1c (opinions routes, opinion frame, the 의견 tab).
   - [`KNOWLEDGE_FEDERATION.md`](KNOWLEDGE_FEDERATION.md) (read queue, knowledge outbox, inbox journal, which phases E5 and E9 use).
   - [`SCHEMA_VERSIONING.md`](SCHEMA_VERSIONING.md) (`claire.db` v49 to v56, `client.db` v10 to v13).
   - [`EXTRACTION.md`](EXTRACTION.md) and [`API_AND_MCP_GUIDE.md`](API_AND_MCP_GUIDE.md) (templates, `renditions` of the ingest, the agent client).
4. For the later work: Section 11 of this document, then [`DEVELOPMENT_NETWORK.md`](DEVELOPMENT_NETWORK.md) and [`KNOWLEDGE_FEDERATION.md`](KNOWLEDGE_FEDERATION.md) Sections 1, 8 (items 3 and 4), 9, 11.8, 12.10, 13.9, and 15.8.

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

### 3.2 Silvercord Page (E2, E3c, E5c, E7b, E7e, and after)

- **AD4. The cross-document view transition is skipped for a destination that is not a Silvercord document or rendition page** (`pageswap` handler of `share.html`). Reason: a page that does not opt in (the workspace) makes Chromium abort the transition with a page error. Design Section 5.10 rule 4 has no such case.
- **AD5. The frame of a remote rendition (E5c) and the frame of one opinion (E7e) are on the app origin**, not on the usercontent host (design Section 5.8 rule 4 and Section 5.11 D2). Paths: `/silvercord/{token}/renditions/remote/{key}`, `/silvercord/{token}/opinions/{key}`. Reason: the render token of the usercontent host is of one account, and a reader of a Silvercord page can have no account. The same protection stays: `sandbox` without scripts, an opaque origin, `default-src 'none'`, `frame-ancestors 'self'`, `X-Frame-Options: SAMEORIGIN`, `noindex`. The page policy got `frame-src 'self'`. Cost to change back: a render token for anonymous readers on the usercontent host.
- **AD6. The two frames show no image**; an attachment is a link. Reason: the media proxy works for accounts, and a reader can have no account. The remote rendition frame has a fixed height.
- **AD7. The 의견 tab is a separate script** (`static/js/silvercord_opinions.js`), inlined before `silvercord.js` by `_build_standalone_shared_html` (`web/pages.py`). Reason: `silvercord.js` was already long; the tab has its own state.
- **AD8. "새 의견" marks the opinions that arrived during this visit** (after the first answer of the route). Design rule D8 says "until the next visit"; a reload starts a new visit, so the effect is the same without stored state.
- **AD9. The first answer of the route replaces the loading text without a transition**; only a later answer with new opinions has the element-scoped view transition, and only when the reader permits motion (`prefers-reduced-motion`).
- **AD36. Prefetch uses a list rule added on intent, not a document rule** (design Section 5.10 rule 5 says a document rule with eagerness `moderate`). Reason: the picker is a `select`; its options are not links, so a document rule cannot match them. The page adds the rule when the reader focuses the picker or keeps the pointer on it for 200 ms (the delay of `moderate`), with eagerness `immediate`, for the other renditions of this document only. Measurement 6 (headless Chromium 153): the prefetched answer is used also with `Cache-Control: private, no-store`, so the rule is the same for visitors and signed-in readers. Code: `prefetchRenditions` in `share.html`.
- **AD37. Long bodies get parts; opinion threads use a smaller size estimate** (design Section 5.10 rule 6). The body HTML has no section elements (the renderer gives flat `h2` and `p`), so the page puts the content after each `h2` into a part when the body has 3 or more `h2`; the headings stay outside the parts for the section rail and the find of the browser. A part or a thread gets `content-visibility: auto` only when it starts below the first screen at the first render. A thread uses `contain-intrinsic-size: auto 160px`, not 800px: one thread is much shorter than a body section, and 800px for each of hundreds of threads would make the scroll bar wrong. Test note: `innerText` leaves out the parts that are not rendered yet; compare `textContent`. Code: `deferLongBody` in `share.html`, `deferThreads` in `silvercord_opinions.js`, `silvercord.css`.

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

- **AD17. The sidecar never reads its own origin.** Thus the fetch of an own object does not start from the object (design rule B4): it starts from the replies that this server knows (delivered and fetched) and walks the reply tree of each one (`walkPosts` in `sidecar/fedify/src/context.ts`). **Changed by RD30 (2026-11-26)**: the sidecar reads its own origin through its loopback (AD39); the start set stays, and it also takes the replies of this server (AD41).
- **AD18. A quote post is read without a walk** (`quotes` of the task), and a reply to a quote post is not stored. This is design rule B4 ("their own replies are not fetched"); the first version of E7a walked them too.
- **AD19. The fetch of opinions is a separate queue** (`opinion-tasks`, `opinion-objects`, table `federation_opinion_fetches`), not a task kind of the client thread fetch (design rule B9). Reason: the result belongs to the object, not to an account; the server actor signs.
- **AD20. The refresh state has a third status `failed`** (3 leases of 5 minutes without an end). Reason: design rule D8 needs a failure text, and the Async Refreshes of Mastodon have only `running` and `finished`.
- **AD21. "500 under one reply" (rule B5) is 16 levels under each start post**, with 1,000 opinions and `3 × 1000 + 20` requests for one fetch. Reason: `@fedify/backfill` limits depth and items, not the items under one post.
- **AD22. The opinions of a gone object are deleted by a run each hour** (`federation/opinion_scheduler.py`), not in each retract path (design rule C4). Reason: a retract, a gone rendition, a private knowledge base, and an account purge all end in a gone object; the routes show nothing for a gone object before the run. The same run makes the daily refresh of E8 (`opinions_refreshed_at` in `client_meta`).
- **AD23. The page JSON lists the notes of this server that reply to the object or to one of its opinions** (`local: true`). Design rule D1 does not say it; without it, a reply from the page would not show.
- **AD24. The author of the object and a server admin get the hidden opinions** with `hidden: true` and the control "다시 보이기". Design rule D5 says only "hide".
- **AD34. "The view of a published object in `/syndication`" (rule B1) is the selection of a published document in the knowledge base view** (stage 2). The page sends `POST /api/federation/objects/{object_id}/opinions` one time for each document and page load; the server applies rule B2. Reason: `/syndication` has no other view of one published object. The route needs no actor (the server actor signs the fetch). Code: `syndication_live.js` (`bridge.onOpenDocument`), `syndication_client.js`, `accounts_routes/opinion_actions.py`.
- **AD35. The opinions of a rendition of another server (rule C2) have no controls of this server**: no hide, no actions, no frame, and no likes or boosts (`null`). Reason: the opinions belong to an object of the origin; this server cannot moderate them, and it has no item or frame route for them yet. The fetch has no age check (`object_published` 0): the age of the remote object is not known; the interval of 15 minutes still limits the fetches. The hourly run deletes them when the copy leaves the cache. Code: `permalink_remote_opinions_route` in `server.py`, `opinions_view` (`object_public_id` None), `purge_gone_objects`, `silvercord_opinions.js` (`setUrl`), `share.html` (`setOpinionsSource`).

### 3.5 Actions of a Signed-in Account (E7d)

- **AD25. The like of an account of this server for a post of this server is a row of `federation_object_reactions`** with the actor of the account (`activity_iri` `{actor}#likes/local-{number}`) and no activity. Reason: the author is on this server, and the sidecar refuses requests to its own origin. The account purge deletes these rows (`purge_account_of_claire_db`). Only a document object has the like (design Section 5.6 rule 2); the author cannot like an own post (rule 3). **Replaced by owner decision RD30 (2026-11-26)**: the like of the post is the favourite of the item of the object (a `Like` that the sidecar delivers through its loopback); the local reaction row, its route `/api/federation/opinions/like`, and its purge code are gone.
- **AD26. An opinion becomes an item of the timeline of the account, outside the home stream** (`POST /api/federation/opinions/item`, `in_timeline` 0). Reason: the favourite, the boost, the reply, and the quote of the federation client (`FEDERATION.md` Sections 25 and 26) work on items; no new task kind is necessary.
- **AD27. `POST /api/federation/notes` takes `in_reply_to_object`**, the address of a published object of this server, for "이 정리본에 답글" (design rule D9), and since 2026-11-26 also of a note of an account of this server, for "답글" on a reply of this server in the tab (rule D4). The note has no mention: the author is on this server, and the sidecar does not resolve a mention at its own origin; the author gets the notification of O22 instead. **Changed by RD30 (2026-11-26)**: the reply with `in_reply_to_object` now mentions the author of the target, as each reply mentions the author of its parent, so the delivery reaches the author.
- **AD38. The notification of a local reply (owner decision O22) comes from the note route**, not from the timeline event: the internal API refuses a timeline event whose actor is on this server, and the sidecar does not deliver to its own origin. The route finds the owner of the reply target (`_local_owner` in `federation_notes.py`: a document object, a rendition object, or a note), gives it the item with `mention` 1, and calls `ap_mention` with the handle of the replying account (`actor_label`; the actor address of a local account has a number, not a name). The scope is a reply; a mention without a reply, a quote, and a like of a local account make no notification (the owner decision names replies only). **Replaced by RD30 (2026-11-26)**: the note route no longer makes the item and the notification; the delivery through the loopback and the timeline event make them, as for another server.

### 3.6 Fill After a Follow (E10)

- **AD28. The fill queue is its own table `federation_fills` with `fill-tasks` and `fill-objects`**, not a kind `fill_follow` of `client-tasks` (design Section 5.13 rule 8). Reason: the kinds of the client outbox are a `CHECK` of its table (a copy of the table to change), and a fill has its own state (the domain limit, the copy, the 24 hours).
- **AD29. "Replies to actors that the account does not follow" (rule 3)**: a reply stays when the address of its target is under the address of the actor of the fill or of a followed actor, or when the account has the target as an item of such an author. Reason: the normal form has the address of the reply target, not its author; a fetch of each target costs requests.
- **AD30. The view of one actor is `GET /api/federation/timeline?author=`** (one page, newest first by `published`, also items outside the home stream) and a button of each accepted follow in the follow list of `/syndication` (design rule 6 names no route).

### 3.6a Same Server as Other Servers (RD30)

- **AD39. The loopback is a wrapper of the global `fetch` in the sidecar**, not a change of Fedify. Reason: on Deno, Fedify 2.4.6 makes reads, deliveries, and WebFinger lookups with the global `fetch` after its address check (its runtime pinner is null on Deno); its transport hook for deliveries (`openHop`) is only for its tests. The internal API and the public self-checks keep the real `fetch`. Risk: a later Fedify with a pinner on Deno would bypass the wrapper; then the reads need `documentLoaderFactory` and the deliveries a queue wrapper (the outbox message has the inbox, the activity, and the keys). Code: `sidecar/fedify/src/loopback.ts`, `main.ts`.
- **AD40. One inbound rule for addresses of this server** (`_peer_host`): an address of this server is accepted only as the address of the verified actor (the actor or its inbox) or the shared inbox, and only for an active account with a USERNAME; an object of this server only when it exists here and has that author (`_object_refusal`). Reason: a remote server must not use an address of this server; the signature check of Fedify proves the actor, and the database of Claire proves the object.
- **AD41. A reply of this server is an opinion as a post of another server**: the start set of an opinion fetch takes replies of this server too, the sidecar reads them through the loopback, and the author of the object can hide them; the `replies` collection leaves out a hidden local reply; the view skips each opinion row when it adds the notes of this server (a note that is not fetched yet shows at once, with `hideable` false). Code: `federation_opinion_routes.py` (`_remote_unique`), `federation_collection_routes.py` (`known_replies`), `federation/opinions.py` (`opinions_view`).
- **AD42. Each sidecar process needs a queue of Fedify.** Without a queue Fedify sends at once, inside the task: an `Accept` can arrive before the acknowledgement of the `Follow` task gives Claire the actor of the follow, and Claire ignores it. `main.ts` gives a SQLite queue; the test of one server gives an in-process queue.

### 3.7 Development and Tests

- **AD31. A development name under `.test` has an `https` public URL** (`Settings.public_url`). This follows decision CD4 of `CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md`; before the fix such a server could not start. Found by the two-server test.
- **AD32. The end-to-end test of the exchange uses two real Claire processes and their real sidecar code** in one Deno process: a request to the origin of one server goes to the `handle` function of its sidecar (`sidecar/fedify/e2e/renditions_exchange_e2e.ts`). Phases: `read`, `plus`, `opinions` (also after the daily run), `fill`, `unplus`, `delete`. Each phase starts new sidecars.

## 4. Library Behaviour and the Workarounds

| Library | Behaviour | Workaround | Where |
| :--- | :--- | :--- | :--- |
| Fedify 2.4.6 | Compacts an outgoing activity and drops unknown terms. | `claire:` fields only on the object at its ID (AD10). | sidecar `renditions.ts` |
| Fedify 2.4.6 | After a link that cannot load (for example the reply target on this server), the object instance keeps an empty value; `replyTargetId` became `null` for a start post, and no opinion was stored. | `@fedify/backfill` gets its own instance of each document; the normal form uses the first instance. Test: `context_test.ts` (the own-object walk asserts `in_reply_to`). | `context.ts` (`walkPosts`, `load`) |
| Fedify 2.4.6 | No `attributionDomains` property; deliveries drop unknown terms. | An idea (O23). | `docs/ideas/ATTRIBUTION_DOMAINS_IDEAS.md` |
| `@fedify/backfill` 2.4.6 | Follows `first` and `next` pages of `replies` and `context` (design R15 asked to verify). | None needed. Test: `context_test.ts`. | — |
| Chromium | Aborts a cross-document view transition to a page that does not opt in, with a page error. | `pageswap` skip (AD4). | `share.html` |
| Playwright | Puts its scripts into each frame; a sandbox frame refuses them and logs errors. | The specs filter these messages (`pageErrors` in `e2e/renditions.spec.js`; the frame test of `e2e/silvercord_rendering.spec.js`). | specs |
| Chromium 153 (headless) | Uses a prefetched page of a speculation rule also when its answer has `Cache-Control: private, no-store` (design measurement 6). | One rule for visitors and signed-in readers (AD36). | `share.html` |
| Browsers | `innerText` leaves out content that `content-visibility: auto` does not render yet. | Tests compare `textContent` (AD37). | `e2e/renditions.spec.js` |
| Fedify 2.4.6 (sidecar tests) | With network permission (`deno test -A`), the document loader resolves the test hosts in DNS and fails. | `deno task test` (no network permission). | `sidecar/fedify/deno.json` |

## 5. Versions

- `claire.db`: v49 (E1) to v56 (E9b). `client.db`: v10 (E6), v11 (E7a), v12 (E7e), v13 (E10).
- Internal federation contract: 1.18 (E1) to 1.24 (E10). Fixtures: `tests/fixtures/federation_contract/` (README lists each version).
- Vocabulary of the knowledge federation: v2 (`/ns/v2`, a superset of v1).
- Agent bundles: 0.0.15 (E3b).

## 6. Tests

- Python: `PATH="$HOME/.deno/bin:$PATH" uv run pytest -n 6`. Without Deno on `PATH`, `tests/test_fedify_sidecar.py`, `tests/test_renditions_exchange.py`, `tests/test_local_federation.py`, and `tests/test_knowledge_exchange.py` are skipped. Baseline (2026-11-26, `5a5b9e8d`): `tests` 3852 passed and `plugins` 106 passed; 2 failures that are older than this work (`test_public_directory_authenticated_user_navigation_e2e`; `test_security_hotfixes` `app.js` `(!hid)`). Under the full load of `scripts/ci.sh` (`-n auto`), `test_notifications.py::test_task_of_the_api_process_sends_after_an_emit` failed one time; alone it passes.
- Type check: `uv run ty check` has no diagnostic over `ty-baseline.json` since `5a5b9e8d` (step 4 of `scripts/ci.sh`). A new diagnostic fails CI: fix it, do not add it to the baseline.
- Development network: `./cb-manuscript dev devnet up --servers a,b`, then `./cb-manuscript dev devnet test` (7 tests, about 5 minutes), then `./cb-manuscript dev devnet down`. [`DEVELOPMENT_NETWORK.md`](DEVELOPMENT_NETWORK.md).
- Sidecar: `deno task test` and `deno task check` in `sidecar/fedify`. Do not use `deno test -A`: with network permission, Fedify resolves the test hosts in DNS and the tests fail.
- Playwright: only `bash scripts/e2e.sh start` and `wait` (or `run`). With no argument each spec has its own process (about 9 minutes now). Baseline: 3 failures that are older than this work (`line_icons.spec.js`; `workspace.spec.js` 2454 and 3229). A full run took 622 and 694 seconds on 2026-11-26 (39 spec files). `bash scripts/e2e.sh` with no command starts a full run; to run one spec: `bash scripts/e2e.sh start silvercord_rendering.spec.js --repeat-each=3`, then `status` until it is not `running`.
- Main tests of this work: `tests/test_federation_opinions.py`, `tests/test_federation_fills.py`, `tests/test_silvercord_rendering.py`, `tests/test_renditions_exchange.py`, `tests/test_plus_ones.py`, `tests/test_plus_one_marks.py`, `tests/test_federation_rendition_objects.py`, `tests/test_e2e_federation_client.py`, `e2e/renditions.spec.js`, `e2e/silvercord_rendering.spec.js`, `e2e/federation_client.spec.js`, `e2e/federation_compose.spec.js`, `sidecar/fedify/tests/context_test.ts`, `sidecar/fedify/tests/internal_api_test.ts`.
- For a visual check, a temporary spec in `e2e/` with the mocks of a real spec and `page.screenshot` works through `scripts/e2e.sh`; delete the spec after.

## 7. Work After the Phases

| Item | Design | Commit | Note |
| :--- | :--- | :--- | :--- |
| `Undo` of each +1 at an account purge | 5.12 rule 14 | `db88222b` | AD33. Two-server phase `unplus`. |
| `Like` and `Announce` of a rendition object | 5.6 rule 2 | `4d9e8a26` | Stored with the number of the rendition object; its document object gives the account and the access rules. Test through the internal API only: no Claire function sends a `Like` of a remote rendition object, so the two-server test has no such phase. |
| +1 scores in the published list of `/syndication` | 5.12 rule 13 | `b0fab1c3` | `plus_ones` of `GET /api/federation/objects`; the knowledge base view shows "+1" with each rendition that has a score, else "+1 0". Tests: `tests/test_plus_one_marks.py`, `e2e/federation_compose.spec.js`. |
| Opinion fetch from the view of a published document in `/syndication` | 5.11 B1 | `251010ae` | AD34. `POST /api/federation/objects/{object_id}/opinions`. Tests: `tests/test_silvercord_rendering.py`, `e2e/federation_compose.spec.js`. |
| Opinions of a rendition of another server | 5.11 C2 | `071cf2a2` | AD35. `GET /silvercord/{token}/renditions/remote/{key}/opinions`; the tab follows the shown rendition. Tests: `tests/test_silvercord_rendering.py`, `sidecar/fedify/tests/context_test.ts`, `e2e/renditions.spec.js`. No two-server phase: in the test of two servers all opinions are on the reading server, and the sidecar never reads its own origin. |
| Prefetch of the other renditions; `content-visibility` of long bodies and opinion trees | 5.10 rules 5 and 6; measurement 6 | `96900bf2` | AD36, AD37. Measurement 6: headless Chromium 153 uses a prefetched answer also with `Cache-Control: private, no-store`. |
| Notification for a reply of an account of this server | owner decision O22 | `f0fb8944` | The note route gives the owner of the target (another account of this server) an item with `mention` 1 and `ap_mention`, as a reply of another server does. Applies to each reply of the federation client, not only the 의견 tab. A like still makes no notification (no notification kind for likes, also for remote likes). AD38. |
| "답글" on a reply of this server in the 의견 tab | 5.11 D4 | `48e183c8` | AD27 (extended). The reply goes to the address of the note with `in_reply_to_object`; its author gets the notification of O22. |
| Same server as other servers (RD30): loopback, inbound rule, removal of the special paths, all controls on each opinion | owner decision RD30 | `4aff60c7` | Section 10; AD39 to AD42; AD25 and AD38 replaced, AD27 changed. |
| `attributionDomains` (phase E2b) moved to an ideas document | owner decision O23 | `f9b0955e`, `1a34ea11` | Section 8. |
| Type diagnostics of the code of this work | — | `5a5b9e8d` | AD53. Narrowing; three answers for a state that should not occur (Section 11.2). |
| The E7e spec waits for the frame to lose its address | — | `33742d8c` | AD54. |

## 8. Open Items

None in this design. `attributionDomains` (the former phase E2b) is an idea since owner decision O23 (2026-11-26): [`ATTRIBUTION_DOMAINS_IDEAS.md`](../ideas/ATTRIBUTION_DOMAINS_IDEAS.md) has the facts of the probe and the ways to take it up. The open items of the later work are in Section 11.3.

## 9. How to Add to This Document

1. A decision that the design does not give, or a change from the design, gets the next number (AD55, and so on) in the section of its area. Write: what the code does, the design text that it changes, the reason, the code, and a test. Do not change the numbers of the other entries.
2. Change the row of the phase in design Section 9 ("Changes", "Added after the phase") and the implementation page of the area in the same commit.
3. Add the commit to Section 7 (or Section 2 for a new phase). A commit cannot have its own hash in its text: write "(this commit)" and put the hash in with the next commit.
4. When an open item of Section 8 is done, move it to Section 7. When an owner decision replaces an AD entry, keep the entry and add "Replaced by owner decision O.." with the date.

## 10. Same Server as Other Servers (RD30)

Owner decision RD30 (2026-11-26, `FEDERATION_REMOTE_READING_DESIGN.md` Section 6.7; implementation `FEDERATION.md` Section 2.1): an account of this server is an actor as an actor of another server in each interaction of the federation client. Why the distinction was there: the remote reading design named only remote actors, and the code refused addresses of its own origin since FP2-2 (an input guard, commit `f967d36b`); this work first added workarounds (AD25, AD27, AD38) and did not remove the gap. RD30 replaced them.

| Step | Content | State |
| :--- | :--- | :--- |
| L1 | Sidecar loopback (AD39); the own-origin refusals of the sidecar loaders and of the parent chain go. | done (`4aff60c7`) |
| L2 | Claire accepts an actor of this server in inbox, timeline, and quote events, and an own object only from its own author (AD40); this server is never a blocked domain for the note tasks. | done (`4aff60c7`) |
| L3 | The special paths go: the like route (AD25), the note-route notification (AD38); a reply to an object mentions its author (AD27); replies of this server become opinions (AD41); the tab gives the same controls to each opinion. | done (`4aff60c7`) |
| L4 | `tests/test_local_federation.py`: one Claire process and its real sidecar code; follow, note, reply, like, boost, reply and like from the 의견 tab, opinion fetch and hide of a local reply, quote. | done (`4aff60c7`) |

Not changed: the opinion fetch of an own object still starts from the known replies and quote posts (AD17); since RD30 the sidecar could start from the object (design rule B4), but the start set gives the same result and also the replies that the `replies` collection hides.

## 11. Later Work: Knowledge Federation Acceptance, Development Network, Master Key, Type Check

The work went on after the renditions design in the same sequence (owner, 2026-11-26: "계속하세요", "진행하세요", "네. 계속하세요."). The designs are [`KNOWLEDGE_FEDERATION_DESIGN.md`](../design/KNOWLEDGE_FEDERATION_DESIGN.md) Section 14.2 (acceptance steps KA1 to KA5) and [`CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md`](../design/CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md) (phases DE1 to DE6). The order of the steps is an owner direction (2026-11-18); their content is a proposal of the agent.

### 11.1 Commits

| Item | Commit | Main files | Result |
| :--- | :--- | :--- | :--- |
| Knowledge federation with two real Claire processes and real sidecars: KF2 read, `Offer`, `Follow`/`Accept`, approval, `Accept` with `result`, `Grant`, direct contribution with the capability | `6305bbb1` | `tests/test_knowledge_exchange.py`, `sidecar/fedify/e2e/two_servers_e2e.ts` | Passed at the first run; no defect |
| The same harness for KF3 (curation, `Update`, `Delete`), KF4 (`Revoke`, `Reject`), and KF5 (automatic `Offer`) | `b89912ac` | the same files | Passed; no defect |
| JSON-LD processor check of the Claire contexts (agent part of KA4) | `5dd88048` | `sidecar/fedify/tests/claire_context_test.ts`; `KNOWLEDGE_FEDERATION.md` Section 8 items 3 and 4, Section 9 | All fixtures pass with no change of a context; ForgeFed IRIs agree with ForgeFed 1.0 |
| Atomic creation of `data/auth/master.key` | `d84cf509` | `src/claire/store/secrets.py`, `tests/test_secrets.py`, `OPERATIONS.md` | Defect found by the development network (AD52) |
| Development network of two servers; KA1 to KA3 | `ebfe3e13` | `ops/devnet.py`, `ops/dev/devnet/`, `tests/test_devnet.py`, `tests/test_federation_compose.py`, `DEVELOPMENT_NETWORK.md` | 7 tests passed from nothing and again on the same network |
| One annotation gave 171 type diagnostics | `e9741519` | `tests/test_support_pipeline.py` | — |
| The other 70 type diagnostics | `5a5b9e8d` | 7 source files, 17 test files, `tests/typing_helpers.py` | `scripts/ci.sh` steps 1 to 4 pass (AD53) |
| E7e spec | `33742d8c` | `e2e/silvercord_rendering.spec.js` | AD54 |

### 11.2 Decisions of the Agent

- **AD43. The tests of the knowledge federation with real sidecars use one generic runner.** `two_servers_e2e.ts` makes the two sidecars with the real code and runs the named queues (`E2E_WORK`, for example `a:outbox,b:outbox`) again and again until no queue gives work; a request to a host outside the test is an error, and a refused delivery or a failed read fails the run. The Python test makes its data with direct rows (accounts, documents, the known server) and starts the revision of KF3 with a job on B. Thus these tests are not step KA3 (no TLS, no DNS, no reverse proxy, direct rows); the tests of the development network are (AD50).
- **AD44. The JSON-LD check of KA4 ran before KA3 was complete.** Design rule 1 of Section 14.2 starts a step only after the step before it. The agent ran only the check of KA4 early, because it is a test and changes nothing; the owner parts of KA4 (the vocabulary page, the freeze and the hosting of `v1`, the pull request on `w3id.org`) wait. jsonld.js 9 (the build of Fedify) expands each fixture and compacts it again; each event of jsonld.js is an error, and each key and type must be an `http` or `https` IRI, because the AS2 context has `"@vocab": "_:"` (an unknown term becomes a blank IRI; safe mode does not refuse it).
- **AD45. `formerType` keeps the bare AS2 type name.** The AS2 context defines `formerType` with `"@type": "@id"`, so a JSON-LD processor reads `"Article"` as a relative IRI. The AS2 vocabulary uses the same form in its examples (Example 142). No change; the check accepts only this event.
- **AD46. The `Grant` of Claire is not a ForgeFed `Grant` for other software.** ForgeFed 1.0 gives `object` a `Role` and has `allows`, `fulfills`, and `result`; Claire uses `claire:collaborator` and none of them. The design does not ask for ForgeFed software. Not changed; recorded in `KNOWLEDGE_FEDERATION.md` Section 8 item 3.
- **AD47. The development network uses bind mounts, not named volumes** (design Section 6.8 rule 1). The overlay then keeps the mounts of `docker-compose.yml`, and no copy job of the service key is needed. The reason of the design (Docker Desktop) was not verified: only Linux ran.
- **AD48. The gateway of the network has only TLS and the server selection.** Since unit GL3 the edge service of each server has the reverse proxy rules; the gateway sends each name of a server to its edge (`a-edge:8080`). The Nginx example of `EXTERNAL_ACCESS.md` is not made from `gateway.conf` (design Section 6.5 rule 1).
- **AD49. The toolbox of the network is the Claire image with the extra `dev`.** It runs the bootstrap and pytest only; it is not the toolbox of phase DE1 (Deno, Node.js, Playwright, multi-architecture).
- **AD50. The KA3 tests act through public routes only, with these choices.** (1) The admin of each server is the actor of the tests: the server tier accepts only `none` and `gemini`, so a member has no mock provider. (2) Documents with chosen entities come through the path of the agent client (`/api/v1/extraction/stage`, `claim`, `submit`). (3) KF5 needs web pages, because an automatic rule never sends a pasted text: the toolbox serves them, and the servers fetch them (`CLAIRE_ALLOW_PRIVATE_NETWORKS=1`; the fetcher refuses a body of about 220 characters). (4) A new read of the `/kb` collection: the admin removes the known server and adds it again. (5) KF1 with two servers: B blocks A; the sidecar of A records the 404 as `collection_unavailable`. (6) The replay of a failed activity is not in these tests (no public route makes an activity fail).
- **AD51. Fixed values of the network.** Edge network `172.31.250.0/24` (constant `EDGE_PREFIX` in `ops/devnet.py`: gateway `.2`, DNS `.3`), image tag `devnet` (the images of another stack on the host do not change), admin USERNAME `keeper` with a random password in `.dev/devnet/state.json` (mode 0600).
- **AD52. Atomic creation of the master key file.** `claire migrate` does not make `data/auth/master.key`; the api and the workers start together, and `SecretService` checked for the file and then wrote it, so two processes could each write a key. The first run of the network from nothing failed on server b with GF12 `actor_keys_decrypt_failed` and GF11 `loop_stalled`, and then each exchange of B. The fix writes the key to a temporary file and links it to the path only when no file is there; a process that loses reads the file. Test: six processes together, five times (the old code fails it). A server installed before the fix may still have actor keys that its master key cannot decrypt: GF12 shows it, and the fix does not repair it.
- **AD53. Type diagnostics.** Source: optional values are narrowed with branches. Three branches give an answer for a state that should not occur: the opinions view of a rendition without its document object and without its rendition object answers "not published" (before: an `AttributeError`); the body route of an object (`federation_collection_routes.py`) answers 404 for an object without its document row (before: a `TypeError` for the 대표 정리본, and the body of an additional rendition from its own row). The add-rendition address of the Silvercord page needs the number of the knowledge base (before: a `TypeError` without it). `PaperClassificationResult` declares the attributes that `__new__` sets. Tests: `need()` (`tests/typing_helpers.py`) asserts that a value is there. The spy of `tests/test_focus_seal.py` dropped the argument `template`, so the original raised `TypeError` and the pipeline hid it; it now passes the arguments on.
- **AD54. The E7e spec waits for the frame address to go.** The dialog fires `close` after it is hidden, and the handler removes `src` then; the spec read `src` once and failed in two full runs. It polls now (30 of 30 passed with `--repeat-each=3`).

### 11.3 Open Items

| Item | Who | Note |
| :--- | :--- | :--- |
| KA4: the owner reads the vocabulary page; `v1` is frozen and served on `claire.bible`; GF17 there; the pull request on `w3id.org` | owner | Production and external publication |
| KA5: calibration of thresholds and limits with real data | owner gives the numbers | Agents do not operate production |
| GF12 on `claire.bible` (AD52) | owner | A server installed before `d84cf509` may have lost the race |
| Development network: access proxy (open question DQ2), fault profiles (DE5), full toolbox (DE1, DE3), macOS and WSL2, DrFed in the toolbox (F12), contributor documents (DE6) | agent, after owner answers for DQ2 to DQ7 | [`DEVELOPMENT_NETWORK.md`](DEVELOPMENT_NETWORK.md) Section 5 |
| KF5 with the switch off on B, and the replay of a failed activity, on the development network | agent | Simulated tests cover them |
| `test_notifications.py::test_task_of_the_api_process_sends_after_an_emit` under `-n auto` | agent | Failed one time under the full load of CI; passes alone |
