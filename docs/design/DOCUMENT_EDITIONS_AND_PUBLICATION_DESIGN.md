# Editions of Published Documents, Opinions from Other Servers, and ActivityPub Publication (`DOCUMENT_EDITIONS_AND_PUBLICATION_DESIGN`)

- **Document Identifier**: `DESIGN-DOC-EDITIONS-20261125`
- **Status**: Proposal, revision 3. Implemented: only the texts of the three edition templates (`src/claire/extract/edition_templates.py`, test `tests/test_edition_templates.py`); no ingest path uses them yet (phase E3). Owner decisions O1 to O7 and O9 are given (2026-11-25, Section 11). O8 is on hold. The frontend part is rules without a visual prototype (Section 9.1).
- **Date**: 2026-11-25
- **Base commit**: `8e6b0723`
- **Revisions**:
  - Rev. 1 (2026-11-25): first proposal, questions Q1 to Q6, decisions O1 to O7 asked.
  - Rev. 2 (2026-11-25): owner answers to O1 to O7. Template `no_math` became `academic` (학문 특화). +1 is the ActivityPub `Like` as it is, and the delivered document object is the primary edition. New: opinions from other servers (Section 5.11), with the structure of Mastodon.
  - Rev. 3 (2026-11-25): O8 on hold (two kinds of reputation, Section 5.6 rules 6 and 7). O9: A, the idea is in [`TIMELINE_BACKFILL_IDEAS.md`](../ideas/TIMELINE_BACKFILL_IDEAS.md). Frontend coverage (Section 9.1).
- **Related**: [`FEDERATION_REMOTE_READING_DESIGN.md`](FEDERATION_REMOTE_READING_DESIGN.md) (RD10, RD21), [`KNOWLEDGE_FEDERATION_DESIGN.md`](KNOWLEDGE_FEDERATION_DESIGN.md) (vocabulary, known servers), [`PERMALINK_UNIFICATION_DESIGN.md`](PERMALINK_UNIFICATION_DESIGN.md), [`SILVERCORD_RENDERING_DESIGN.md`](SILVERCORD_RENDERING_DESIGN.md) (the `의견` tab, SR5, SD8), [`SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md`](SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md) (reputation, CT18), [`FEDERATION.md`](../implementation/FEDERATION.md), [`KNOWLEDGE_FEDERATION.md`](../implementation/KNOWLEDGE_FEDERATION.md), [`PERMALINKS.md`](../implementation/PERMALINKS.md), [`EXTRACTION.md`](../implementation/EXTRACTION.md), [`GLOSSARY.md`](../implementation/GLOSSARY.md)

## 1. Owner request (2026-11-25)

> 적재 문서 ActivityPub 발행 준비. 설계.
>
> * Claire Bible 서버끼리는 발행한 적재 문서의 여러 에디션 교환하여 다른 이용자가 적재한 다른 에디션을 골라 보고 +1 점수를 부여할 수 있다.
> * 예: 현재의 기본 상세 적재 프롬프트 뿐 아니라, 간결한 정리 혹은 수학 제외 등 고정 템플릿 3개 제공, 추가 템플릿 내용은 나중에 결정.
> * 다른 ActivityPub 서버에는 현행 요약까지만 전달할 수 있는지?
> * 교류/적재/게시 흐름에서 게시를 발행으로 교체. 겉만 아니라 속과 용어까지?
> * 현재의 실버코드가 경로 고정해야하는지 더 만들어야하는지 경로 내에서 확장이 되는지?
> * 지식 서비스이므로 인용 거부는 없음.
>
> 적극 연구 및 최신 ActivityPub 동향 확인하여 제언 필요. 모던 웹 가이던스 고려.

Owner answers to revision 1 (2026-11-25):

> * O1: A
> * O2: '간결'은 현행 프롬프트를 네가 복제한 후에 작성해서 저장해보세요. '수학 제외'를 '학문 특화'로 변경하고 현재의 상세 프롬프트를 그대로 복제해 두세요. 나중에 수정하겠습니다.
> * O3: A
> * O4: A
> * O5: ActivityPub 좋아요 기능을 그대로 씁시다. 기존 기능을 활용해도 리퓨테이션 연산에 쓸 수 있어보이네요.
> * O6: 권장안
> * O7: 권장안
> * 기타: ActivityPub 자체 기능 개선도 함께 들어가야합니다. 다른 서버의 의견을 끌어오는 기능입니다. https://github.com/nanos/FediFetcher 와 유사합니다. 최신 Mastodon이 업데이트하는 명세 파악 가능하다면 해당 구조를 우선합니다.

Owner answers to revision 2 (2026-11-25):

> * O8: 평판에도 종류가 있습니다. 발행자의 평판과 발행 에디션 간 평판입니다. 구상에 구멍이 있네요. 다시 별도의 점수제가 필요할 수 있으니, 보류합니다.
> * O9: 권장안
>
> 현재 설계는 백앤드 한정인가요? 프런트엔드를 포함하나요?

Questions and the answers of this design:

| # | Question | Short answer | Section |
| :--- | :--- | :--- | :--- |
| Q1 | Exchange editions between Claire servers, choose an edition of another user, give +1 | Yes. An edition is one readable body of a document, made with one fixed template. Editions of the same source make an edition set across servers. +1 is the ActivityPub `Like` as it is (O5). | 5.1 to 5.4, 5.6 |
| Q2 | Three fixed templates | `detailed` (상세, the current prompt), `concise` (간결, written from a copy), `academic` (학문 특화, an exact copy of the current prompt for the owner to change). The template changes the body only. | 5.2 |
| Q3 | Only the summary to other ActivityPub servers? | Yes. The standard fields of each Claire object carry the summary and the link only. A body is only behind a Claire extension link that a signed server actor of a Claire server reads. | 5.5 |
| Q4 | Replace 게시 with 발행, also inside? | Yes, for the publication of documents in all layers. The code identifiers already use `publish`. A note stays 게시 (a post), and its English word becomes "Post" (O7). | 5.9 |
| Q5 | Silvercord path: fixed, more paths, or extension inside the path? | Keep the one path word `/silvercord/{token}`. Add no path word. An edition and the opinions are sub-paths: `/silvercord/{token}/editions/{template}`, `/silvercord/{token}/opinions`. | 5.8 |
| Q6 | No refusal of quotes | Documents and editions are always quotable, with an automatic approval stamp for each request. Notes keep their policies (O3). | 5.7 |
| Q7 | Get the opinions of other servers (as FediFetcher), with the structure of Mastodon first | Yes. Claire publishes `replies`, `likes`, and `shares` in the form of Mastodon, so that other servers can read Claire threads, and crawls the `replies` collections of other servers as Mastodon 4.5 and later do, with the same limits. | 5.11 |

---

## 2. Facts at the base commit

1. **[code] Published object.** The `Article` of a published document has `name` (title), `content` (the summary paragraph and a paragraph with the permalink), `url` (the permalink), `published`, `updated`, `to`, `cc`. `summary` is not set. The body is never in the object (RD21; [`FEDERATION.md`](../implementation/FEDERATION.md) Section 4.4, Section 5.8). The object has no `replies`, `likes`, `shares`, or `context`.
2. **[code] One body for each document.** `ensure_document_detail` (`src/claire/ingest/pipeline.py`) makes the readable body with a separate model call. Its docstring says that it is independent of the graph: it fills `documents.detail` only and does not change entities or relations. The prompt is `render_detail_prompt_adoc` (`src/claire/extract/prompts.py`). 38 Python files under `src/claire` read `detail` or `detail_html`.
3. **[code] Template placeholder.** `build_extraction_prompt_bundle` has a parameter `template`; no caller sets it, and its value is always `DEFAULT_EXTRACTION_TEMPLATE` (`"default"`). The agent client spec and the provenance of a client extraction write it as `prompt_template` (`src/claire/ingest/client_extraction.py`).
4. **[code] Summary.** The summary comes from the structured extraction (`PROMPT_VERSION = "extract-v8"`), not from the body prompt. Search, hover cards, and the published object use it.
5. **[code] Permalink.** `/silvercord/{token}` is only for kinds that can be ActivityPub objects (`ACTIVITYPUB_KINDS`: document, note; owner rule 2026-11-23). The sub-paths `/silvercord/{token}/workspace` and `/silvercord/{token}/graph` are views of the same item. An alias token gives 308 also for the sub-paths ([`PERMALINKS.md`](../implementation/PERMALINKS.md) Sections 2, 3.1, 3.2).
6. **[code] Reactions.** A `Like` or an `Announce` of `{origin}/ap/objects/{public_id}` is stored one time for each actor and kind (`federation_object_reactions`). The counters are in `GET /api/federation/objects` ([`FEDERATION.md`](../implementation/FEDERATION.md) Section 22.4). The account can favourite (좋아요, `synd.compose.action.favourite`) and boost remote posts (Section 25.4).
7. **[code] Replies to own objects.** A delivered public reply to a published document becomes a timeline item of its owner with `mention` = 1 (Section 22.3). Nothing else stores it, and no page shows it to other readers.
8. **[code] Thread fetch.** For a timeline item that the owner opens, the sidecar reads the FEP-7888 `context` collection (FEP-f228) with `@fedify/backfill` (`~2.4.6`, strategy `context-auto`), and the reply tree when that gives nothing. Limits: `context_max_items`, `3 × max_items + 20` requests, 120 seconds. Each object comes from its own origin. The account key signs the fetches (Section 24).
9. **[code] Quotes.** FEP-044f is implemented. A published document has the policy `manual` by default; the policies are `public`, `followers`, `manual`, `nobody`; the owner can refuse and revoke. Claire does not fetch the quoting object (Section 26).
10. **[code] Claire vocabulary.** The context `https://w3id.org/claire-bible/ns/v1` has `claire:Extraction`, `claire:contentHash`, `claire:root`, and other terms. The context of a version must not change after a server serves it; a new term goes to a new version ([`KNOWLEDGE_FEDERATION.md`](../implementation/KNOWLEDGE_FEDERATION.md) Section 2). A collection of shared knowledge answers only a request that a server actor of another server signed (KS1, Section 10.3 of the same document).
11. **[code] Source identity.** `documents.canonical_url` comes from `canonicalize_url` (`src/claire/ingest/normalize.py`: host to lower case, tracking parameters removed, arXiv versions joined to `/abs/{id}`). `documents.content_hash` is SHA-256 of the normalized text.
12. **[code] Terms.** `ko.json` has 44 values with 게시 and 55 values with 발행. The stage name is 게시 (`nav.syndication`, `home.flow.publish`, `synd.stage.*`, `synd.kb.filter.*`). The publish dialog of a document uses 발행 (`synd.kb.publish.*`). The note dialog of the account home uses 발행 (`home.note_dlg.publish*`), and the compose button of `/syndication` uses 게시 (`synd.compose.submit`). The glossary defines 게시물 (Post) as a note or a document in the timeline (Section 1.11). The English texts use "publication" and "Publish" for documents and for notes. The code identifiers of document publication are `publish`.
13. **[code] Retired text that is still visible.** The console sections `/settings/connections` and `/settings/outbound` (`SYNDICATION_SECTION_SLUGS` in `src/claire/api/accounts_routes/console_syndication_panels.py`) show the texts `console.synd.connections.*` and `console.synd.outbound.*`. These texts describe the retired model of connected external accounts ("Claire 는 ActivityPub 서버를 … 직접 구현하지 않습니다") and use 발행 in that retired sense ("적재 발행", "사용자 간 발행 본문 공유").
14. **[code] Remote content in the page.** The federation client puts no content of a remote server into the page DOM. The body of a remote post stays in the sandbox frame ([`FEDERATION.md`](../implementation/FEDERATION.md) Section 26.6).
15. **[code] Page metadata.** The Silvercord document view has no `rel="cite-as"`, no `citation_*` meta elements, and no `fediverse:creator`. The actor document has no `attributionDomains`. The `의견` tab is a visual prototype (SR5, SD8).
16. **[code] Reputation.** `src/claire/collaboration/reputation.py` computes a level (`good`, `limited`, `low`) from two signals: the fiction share and the approval rate of contributions. A knowledge base gives at most one third of the decisions. The level controls the exchange only, never reading, ingest, publication, or membership.

---

## 3. Research findings (2026-11-25)

Sources are in Section 13. **[code: Mastodon]** marks a fact read in the Mastodon source code (branch `main`, 2026-11-25). A statement from a third party that this design could not confirm has the tag **[unverified]**.

| # | Finding | Effect on this design |
| :--- | :--- | :--- |
| R1 | Mastodon converts an `Article` or a `Page` to a status: it "uses `content` if available, or `name` if not", it appends `url`, and "the `summary` property will be used as the CW text" (Mastodon ActivityPub documentation). | The RD21 shape is correct for Mastodon. Do not put the summary in `summary`: Mastodon would hide it behind a content warning. |
| R2 | FEP-b2b8 "Long-form Text" (DRAFT, received 2024-11-07) puts the full text in `content`, a teaser of about 500 characters in `summary`, the original format in `source`, and an optional `preview` (`Note`). | Claire departs from FEP-b2b8 on purpose: the owner keeps the body on Claire (RD21). `summary` conflicts with R1. No `preview` now: Mastodon does not read it. |
| R3 | FEP-044f (DRAFT): the quote policy is advisory; a quote is valid only with a `QuoteAuthorization` stamp from the author. Mastodon writes `interactionPolicy.canQuote.automaticApproval` as a list, with the public collection for a public policy, and shows an object without a policy as not quotable **[code: Mastodon]**. | A Claire document always advertises a policy and always answers a request with a stamp. |
| R4 | FEP-844e "Capability discovery" (DRAFT, received 2025-06-14): "Implementers MUST NOT use it in cases where capabilities can be inferred from properties and types of objects"; it discourages NodeInfo for this. | Claire infers edition support from the `claire:` properties of an object. No capability flag, no check of the NodeInfo software name. |
| R5 | Mastodon releases: 4.6 (2026-06-17: collections with authorizations, `interactionPolicy.canFeature`, JSON-LD profile in `Accept`, multiple key pairs, FEP-2c59, FEP-3b86); 4.7 (2026-08-20: FEP-8967 `Link` attachments, verification of FEP-8b32 integrity proofs, inbound FEP-521a, no `Accept` or `Reject` of a `QuoteRequest` that it cannot find by ID); latest 4.7.6 and 5.0.0-beta.2 (2026-11-19: schema.org markup on profiles and replies; a bloom filter of the servers that fetched an account, to send fewer deletion notices). | `interactionPolicy` is the common pattern of consent. A Claire `Accept` of a quote must carry the `QuoteRequest` ID exactly (it does, [`FEDERATION.md`](../implementation/FEDERATION.md) Section 26.4). The bloom filter idea applies to edition bodies (Section 5.4 rule 4). |
| R6 | No FEP defines parallel editions. FEP-bad1 (object history collection) is about edits in time. FEP-7888, FEP-f228, and FEP-2931 use `context` for conversation containers and backfill. | Editions use Claire terms. Do not use `context` to group editions. |
| R7 | The W3C Social Web Working Group started on 2026-01-15. In May 2026 it adopted the Community Group drafts of ActivityPub, Activity Streams, and Activity Vocabulary. The revisions are backward-compatible, and extensions stay in the Community Group. The 2018 Recommendation is still the latest published version. | No protocol change blocks this design. |
| R8 | FEP-dc88 (mathematics in MathML Core, DRAFT) has no recent change; the FEP maintainer said that it will be withdrawn. | Claire sends no mathematics in federated text. The summary is plain text. |
| R9 | FEP-c118 (content licenses, DRAFT, 2023) has no agreed form. | No license property now. |
| R10 | RFC 8574 (informational) defines `rel="cite-as"`. Mastodon shows an author byline in link previews from `<meta name="fediverse:creator">` when the actor lists the site domain in `attributionDomains`. | Section 5.7, citation. |
| R11 | FEP-fe34 (origin-based security): an object is trusted for the origin of its ID. Fedify 1.9 and later enforce the same-origin rule. | A body and each fetched opinion must come from the origin of its ID. |
| R12 | **Fetch all replies (Mastodon).** 4.4 added it as an experiment; 4.5 turned it on for all servers and removed its settings. A request of the context of a status starts it (web interface and apps). It "fetches the tree of replies beneath an expanded post by recursively requesting the replies collections of each of the statuses, and then requesting the status itself". Limits in the code: 1000 replies for one tree, 500 for one status, 500 collection pages; a status is fetched again only after 15 minutes, and not in the first 5 minutes after it was made. Replies from accounts without local followers are fetched again, because no `Delete` reaches the server; a 404 deletes them **[code: Mastodon]**. | Claire uses the same algorithm and the same limits for opinions (Section 5.11). |
| R13 | **The `replies` collection of Mastodon.** A local status has `replies`: a `Collection` whose `first` page has at most 5 self-replies, and whose `next` goes to pages with `only_other_accounts=true` that list the public replies of other accounts (local objects embedded, remote objects as IRIs). `likes` and `shares` are collections with `totalItems` (the favourite and boost counts). `context` is the IRI of the conversation **[code: Mastodon]**. | Claire publishes the same three collections, so that Mastodon 4.5 and later can crawl Claire threads (Section 5.11 A). |
| R14 | **Async refreshes (Mastodon, experimental).** The context API answers with `Mastodon-Async-Refresh: id="…", retry=<seconds>, result_count=<n>` (4.5). The client polls `GET /api/v1_alpha/async_refreshes/:id` (4.4) until the status is `finished`. | Claire uses the same three values in the JSON of its own opinions route (Section 5.11 B rule 6). Claire has no Mastodon client API, so it does not use the header name. |
| R15 | Mastodon backfills only through `replies`; it publishes `context` as an IRI and groups by its older `conversation` value **[unverified]** (summary of a community guide, 2026-10-15). `@fedify/backfill` 2.4.1 did not follow `first` and `next` pages of collections; the issue closed on 2026-10-06 with a fix for the next patch release; the release with the fix is not named **[unverified]**. | The reply tree is the first source; a `context` collection is a second source. Verify the pagination of `@fedify/backfill` 2.4.6 before phase E7. |
| R16 | **FediFetcher** fetches missing replies on a schedule for posts of the home timeline, posts that a user replied to, bookmarks, favourites, and lists, and fills the profiles of new follows and followers with recent posts. Its author wrote (2025-07) that server-side fetching is "the most sustainable and user-friendly solution" in the long term. The built-in fetch of Mastodon runs only when a person opens a post. | Claire takes the Mastodon structure (fetch on open) and adds one scheduled refresh for its own published objects with new activity (Section 5.11 E). Profile filling is O9. |

---

## 4. Terms (proposal for `GLOSSARY.md`)

| Term | English | Definition | Not used |
| :--- | :--- | :--- | :--- |
| **에디션** | Edition | One readable body (상세) of a document, made with one edition template. A document has one edition for each template that it has. The summary, the entities, and the relations belong to the document, not to an edition. | "판", "버전", "리비전" (a revision is a change in time; an edition is a parallel body) |
| **에디션 템플릿** | Edition Template | A fixed instruction set for the body prompt, with an identifier (`detailed` 상세, `concise` 간결, `academic` 학문 특화) and a version. The code defines the set. | "프롬프트" alone, "초점" (`focus` is sealed data, CT28), the value `default` of the placeholder `prompt_template` (fact 3) |
| **기본 에디션** | Primary Edition | The edition of the first ingest. It does not change (O6). Its ActivityPub object is the object of the document. | — |
| **출처 식별자** | Source Identifier | The normalized identifier of the source of a document (Section 5.3). Documents with the same source identifier have editions of the same source. | "작품", "원본 ID" |
| **에디션 묶음** | Edition Set | All published editions with the same source identifier, on this server and on other Claire servers, and the own editions of the reader. | — |
| **좋아요** | Like | The ActivityPub `Like` of an object: of a remote post, of a published document, or of an edition (O5). The owner request called it "+1 점수"; the product uses the one existing word. | "+1" as a separate term, "추천" |
| **의견** | Opinion | A public post on any server that is in the reply tree of a published object of this server (a reply, or a reply to a reply), or a quote post of it with a valid stamp. The `의견` tab of the Silvercord view shows them. | "댓글" (a comment is a function of a page; an opinion is a federated post), "스레드" (use 글타래) |
| **발행** | Publication / Publish | The action that makes a document of a public personal knowledge base an ActivityPub object for followers and makes its editions available to Claire servers. Stage 3 of the flow 교류 · 적재 · 발행. | 게시 for this action |
| **게시** | Post | The action that sends a note. 게시물 (Post) stays the name of an item in the timeline. | 발행 for a note |
| **출처 표기** | Citation | The bibliographic reference to a document or an edition: `cite-as`, citation metadata, the copy action. | 인용 for this meaning (인용 is the quote of FEP-044f) |

The glossary uses "에디션" already in the same-entity rule ("버전·에디션·제품과 회사"), where it is a word about products in the world. "Shopify Editions" in the graph engine design is a product name. Neither is a Claire data concept, thus the new term has no collision (O1: A, 2026-11-25).

---

## 5. Design

### 5.1 ED1: Edition model

1. An edition is a document body made with one edition template. One document has at most one edition for each template.
2. The template changes the body only. The structured extraction (summary, key claims, entities, relations) stays one for each document. Reason: fact 2 says that the body render is already independent of the graph, so a second edition adds no entity and no relation.
3. A new version of the same template replaces the body of that edition. The edition keeps its identity, its object ID, its address, and its likes.
4. The primary edition is the edition of the first ingest, and it does not change (O6: recommendation B). A later design can let the owner change it.
5. A reader of an edition set sees editions of other accounts. These editions never enter the knowledge base of the reader (RD10 rule 1). The reader can ingest the same source with an own template; that makes a new document and a new edition.

### 5.2 ED2: Edition templates

The texts are in `src/claire/extract/edition_templates.py` (owner decision O2, 2026-11-25). `edition_template(id)` gives a template; an unknown identifier raises `UnknownEditionTemplate` (no default template).

| Identifier | Korean name | Version | Text |
| :--- | :--- | :--- | :--- |
| `detailed` | 상세 | `detailed-v1` | The current body prompt, `render_detail_instructions_adoc`. The registry calls it; it is not a copy. |
| `concise` | 간결 | `concise-v1` | A copy of the current prompt, written again for a short body: about one A4 page (merged: `scale` to `2 × scale` pages); the section order `== 핵심` (3 to 5 one-sentence claims), `== 내용`, `== 결과와 수치`, `== 한계`, each only when the source has it; less background, repetition, and examples; numbers, conditions, definitions, and names stay exactly; at most one `[quote]`, one note block, two figures; tables that carry a result stay; no text that the source does not have, also to fill space. The common rules stay: AsciiDoc only, written style, quote rules, names in their original form, formulas, truncated sections, no author line. |
| `academic` | 학문 특화 | `academic-v1` | An exact copy of the current prompt (the test of revision 2 compared the outputs character by character). The owner changes it later. |

Rules:

1. The code defines the set. An account cannot make a template. Reasons: the editions of an edition set must be comparable across servers; a free prompt from an account is a prompt injection path into a public page; a template identifier on the wire must have one meaning.
2. A change of the text of a template increments its version.
3. Each template gets the catalog keys `edition.template.{id}.label` and `edition.template.{id}.desc` in English and Korean when phase E3 shows it.
4. A new template is a new identifier in the code and a new value in the vocabulary (Section 5.5). An identifier never changes its meaning and is never used again for another template.
5. The agent client gets the template text from the server in the claimed task. The client does not hold template texts. The server validates the body as now (`_validate_asciidoc_detail`).
6. Phase E3 replaces the placeholder of fact 3: `prompt_template` carries the edition template identifier, and the value `"default"` and `DEFAULT_EXTRACTION_TEMPLATE` go away at once (no external consumers; the agent client template `agent_clients/common/agents/extractor.md.tmpl` changes in the same commit).

### 5.3 ED3: Creation of editions and the source identifier

1. **At ingest.** The ingest request selects one or more templates. The first selected template makes the primary edition. When the request selects none, the ingest form shows `detailed` as selected; the API requires the field (no hidden default).
2. **Later.** The document menu has "에디션 추가" with the templates that the document does not have. The run uses the stored raw text (glossary 1.1). It fetches nothing. It costs one model call.
3. **Limits.** The number of edition runs of an account uses the tier features as other model work does.
4. **Source identifier.** A document gets a source identifier when its source has one of these forms, in this order:

| Order | Form | Source identifier |
| :--- | :--- | :--- |
| 1 | A DOI in the URL, or in the PDF metadata that the extraction confirmed | `https://doi.org/{doi in lower case}` |
| 2 | An arXiv address | `https://arxiv.org/abs/{id without version}` (as `canonicalize_url` does now) |
| 3 | A web URL | the result of `canonicalize_url` |
| 4 | A file without a URL | none (O4: A) |

5. The key on the wire is `claire:sourceKey`: SHA-256 of the source identifier in lowercase hexadecimal. The normalization rules are part of the vocabulary contract with a version (`claire:sourceIdVersion`). A change of the rules is a new version, and a server sends the keys of all versions that it knows.
6. A document without a source identifier has editions, but no edition set across accounts.

### 5.4 ED4: Edition set and exchange between Claire servers

1. **Members.** An edition set has: the editions of the document that the reader opened; the published editions of other documents on this server with the same source key; the published editions on other Claire servers with the same source key. Unpublished editions of other accounts are never in a set (publication is the consent of the owner, RD10 rule 2).
2. **Pull first.** The publication of a document sends the activities of today (`Create`, `Update`, `Delete` of the document object). A new, changed, or deleted edition sends no new activity to followers. A Claire server reads editions when a reader needs them. Reasons: Mastodon followers get no noise; a server that follows nobody of the origin can still find editions; there is less delivery work.
3. **Discovery.**
   1. From a received document object with `claire:editions` (the follow of an account).
   2. From the source index of a known Claire server: `GET /ap/sources/{sourceKey}` gives an `OrderedCollection` of the published edition objects of that server for the key. The sidecar answers only a request that a server actor of another server signed, with the controls of KS1 (signature, blocked domain, the same 404 for each refusal). The servers to ask are the known servers of KF1 and the origins of editions that the server already has. There is no relay and no server-level partnership ([`RETIRED_PARTNERSHIP_IDEAS.md`](../ideas/RETIRED_PARTNERSHIP_IDEAS.md)).
4. **Cache and deletion.** A server keeps the metadata and the body of a remote edition as a cache with a lifetime (proposal: 24 hours) and revalidation with `ETag`. A 410 or a `Tombstone` deletes the copy at once, and a `Delete` of the document deletes the copies of its editions. The origin records each server actor that read a body (the read is signed), and at a retract or a privacy trigger it sends `Delete` of the edition objects to these servers only (the idea of the bloom filter of Mastodon 5.0, R5, with an exact list because the number of Claire servers is small).
5. **Privacy.** A source index request tells the other server that some reader of the asking server opened a document with that key. The server actor signs it, not the account.
6. **Load.** A server asks each known server at most one time for each key in the lifetime, keeps a negative answer for the same lifetime, and uses the rate limits of KF1.

### 5.5 ED5: Federation shape (answer to Q3)

**Rule: the standard Activity Streams fields of each Claire object carry the summary and the link only. A body is only behind the Claire extension property `claire:body`.**

Reasons:

1. The delivered object stays as RD21 says. Mastodon shows `content` (R1). Software that keeps the full JSON of an object gets no body, because the body is a link, not an embedded value.
2. One object has one representation for all readers. A response that changes with the signature of the reader breaks caches and the same-origin rule.
3. Claire servers recognize the extension from the object itself (R4).

**Primary edition = document object (O5, O6).** The delivered `Article` of a published document is also the object of its primary edition. Thus a favourite from Mastodon of the post is a like of the primary edition (O5: "ActivityPub 좋아요 기능을 그대로"). All fields of today stay. New fields:

| Field | Value |
| :--- | :--- |
| `claire:template`, `claire:templateVersion` | The template of the primary edition. |
| `claire:body`, `claire:contentHash` | Link to the body representation; SHA-256 of the AsciiDoc body. |
| `claire:sourceKey`, `claire:sourceIdVersion` | Section 5.3. Absent for a document without a source identifier. |
| `claire:editions` | IRI of an `OrderedCollection` of all published edition objects of the document, the primary edition first. |
| `replies`, `likes`, `shares` | Section 5.11 A. |
| `interactionPolicy` | `{"canQuote": {"automaticApproval": ["https://www.w3.org/ns/activitystreams#Public"]}}` (Section 5.7). |

**Other editions.** Each is a separate object in `federation_objects` (kind `edition`, with its parent object) at `{origin}/ap/objects/{public_id}`. Reason: reactions, quotes, and tombstones already work for this path (facts 6, 9). The type is `Article`; the property `claire:editionOf` marks the edition. Mastodon gets the same type that it shows now.

```json
{
  "@context": ["https://www.w3.org/ns/activitystreams", "https://w3id.org/claire-bible/ns/v2"],
  "id": "https://a.example/ap/objects/1043",
  "type": "Article",
  "attributedTo": "https://a.example/ap/actors/u7k2",
  "name": "Title of the document",
  "content": "<p>Summary of the document.</p><p><a href=\"https://a.example/silvercord/7mq2k9x4hz3d8n5p/editions/concise\">https://a.example/silvercord/7mq2k9x4hz3d8n5p/editions/concise</a></p>",
  "url": "https://a.example/silvercord/7mq2k9x4hz3d8n5p/editions/concise",
  "published": "2026-11-25T03:00:00Z",
  "updated": "2026-11-25T03:00:00Z",
  "to": ["https://www.w3.org/ns/activitystreams#Public"],
  "claire:editionOf": "https://a.example/ap/objects/1001",
  "claire:template": "concise",
  "claire:templateVersion": "concise-v1",
  "claire:sourceKey": "9f2c…(64 hexadecimal characters)",
  "claire:sourceIdVersion": 1,
  "claire:contentHash": "41ab…(64 hexadecimal characters)",
  "claire:body": "https://a.example/ap/objects/1043/body",
  "replies": "https://a.example/ap/objects/1043/replies",
  "likes": {"id": "https://a.example/ap/objects/1043/likes", "type": "Collection", "totalItems": 12},
  "shares": {"id": "https://a.example/ap/objects/1043/shares", "type": "Collection", "totalItems": 3},
  "interactionPolicy": {"canQuote": {"automaticApproval": ["https://www.w3.org/ns/activitystreams#Public"]}}
}
```

- No `Create` of an edition goes to followers (Section 5.4 rule 2). A fetch by URL from other software gives the summary and the link only.
- A reader compares `claire:contentHash` before it gets the body again.

**Body representation.** `GET {object ID}/body`. The sidecar answers only a request that a server actor of another server signed (KS1 controls). The answer is on the origin of the object (R11):

```json
{
  "@context": ["https://www.w3.org/ns/activitystreams", "https://w3id.org/claire-bible/ns/v2"],
  "id": "https://a.example/ap/objects/1043/body",
  "type": "claire:EditionBody",
  "claire:edition": "https://a.example/ap/objects/1043",
  "mediaType": "text/html",
  "content": "<h2>…</h2><p>…</p>",
  "source": {"mediaType": "text/asciidoc", "content": "== …"},
  "claire:contentHash": "41ab…"
}
```

- `content` is the compiled body, cleaned with `nh3` on the origin. The receiver cleans it again and shows it only in the sandbox frame (fact 14).
- `source` is the Activity Streams property for the original format (also FEP-b2b8).
- The body is not secret: the Silvercord page of a published document shows it. The signed access makes the body a channel between Claire servers, with blocks, quotas, logs, and the exact delete list of Section 5.4 rule 4.

**Vocabulary.** New terms go to `https://w3id.org/claire-bible/ns/v2`: `claire:EditionBody`, `claire:edition`, `claire:editionOf`, `claire:editions`, `claire:template` (closed values `detailed`, `concise`, `academic`), `claire:templateVersion`, `claire:sourceKey`, `claire:sourceIdVersion`, `claire:body`. `v1` does not change. The contract of the internal federation API goes from 1.17 to the next version.

### 5.6 ED6: Likes (answer to Q1, O5)

1. The mark of the owner request ("+1 점수") is the ActivityPub `Like` as it is (O5). The account likes an edition with the same function as a remote post (Section 25.4 of `FEDERATION.md`): `Like` to the author, `Undo(Like)` to take it back, one like for each account and object.
2. Each object counts its own likes: the primary edition (the document object) gets the favourites from Mastodon and the likes from Claire accounts; another edition gets the likes of the readers who chose it. The origin counts (`federation_object_reactions`, fact 6) and shows `likes.totalItems` (R13).
3. A like inside one server makes the same row and sends nothing.
4. The author cannot like an own edition; the origin ignores such a `Like`.
5. Anonymous visitors see the counts. The control needs an account that is a federation actor (every active account with a USERNAME, GD5 and GD8).
6. Order in the picker, until O8: the opened edition first, then the primary edition, then the template order of Section 5.2 (`detailed`, `concise`, `academic`), then `updated` (new first). The picker shows the like count of each edition, but the count does not change the order. Reason: an order by likes is already a score between editions, and that score is on hold (rule 7).
7. **Reputation: on hold (O8).** The owner separates two kinds: the reputation of a publisher (an account) and the reputation between published editions. Likes alone do not cover both, and a separate score can be necessary. Until a decision, likes are data only: `federation_object_reactions` keeps the object, the actor, and the time, and no reputation and no order reads them. The reputation of fact 16 does not change.
8. Domain blocks and the rate limit of interactions apply to inbound `Like`.

### 5.7 ED7: Quotes always permitted, and citation (answer to Q6)

Quotes (FEP-044f):

1. A published document and each of its editions advertise `canQuote` with automatic approval for the public collection.
2. Each `QuoteRequest` for a document or an edition gets `Accept` with a `QuoteAuthorization` stamp at once (R3).
3. The owner cannot refuse or revoke a quote of a document or an edition. The policy dialog of a published document, the field `quote_policy` of `PATCH /api/federation/objects/{object_id}`, and the policy values for documents go away. A retract of the document deletes the object; then the stamp path gives 404 or 410 as the result of the retract.
4. Migration: `federation_objects.quote_policy` becomes `public` for all rows. Each `pending` and `refused` request for a document becomes `approved` and sends its stamp. Each `revoked` row stays revoked (the remote side already has the `Delete`).
5. Notes keep the four policies (O3: A).
6. The rate limit of quote requests stays (spam is not a quote).
7. An approved quote post is an opinion (Section 5.11 B rule 3).

Citation (출처 표기) of the Silvercord view:

1. `<link rel="cite-as" href="{permalink of the item}">` (RFC 8574). An edition page cites its edition address; the page of the primary edition cites the document address.
2. `citation_title`, `citation_author` (the account), `citation_publication_date`, `citation_public_url`, and `citation_language` meta elements.
3. JSON-LD of schema.org: `Article` with `isBasedOn` (the source URL), `exampleOfWork` (the source identifier as `CreativeWork`), `version` (the template identifier and version), `author`, `datePublished`, `dateModified`. Mastodon 5.0 adds schema.org markup to its pages too (R5).
4. The action "출처 표기 복사" gives a plain text reference with the title, the account, the server, the date, the template, and the permalink.
5. `<meta name="fediverse:creator" content="@{username}@{host}">`, and `attributionDomains` with the host of the server in the actor document (R10).

### 5.8 ED8: Silvercord paths (answer to Q5)

Decision proposal: **the path word stays fixed. No new path word. Extension is inside the path.**

| Address | Content |
| :--- | :--- |
| `/silvercord/{token}` | The document, with its primary edition, the edition picker, and the `본문` / `의견` tabs. Unchanged as a link. |
| `/silvercord/{token}/editions/{template}` | One other edition of this document. `{template}` is an identifier of Section 5.2. The template of the primary edition gives 308 to `/silvercord/{token}`, because the primary edition is the document (Section 5.5) and does not change (O6). |
| `/silvercord/{token}/editions` | JSON of the edition set for the picker: local editions with their paths, remote editions with their origin addresses and local render tokens. |
| `/silvercord/{token}/opinions` | JSON of the opinions of the object of the page, with the refresh state (Section 5.11 B). An edition page uses `/silvercord/{token}/editions/{template}/opinions`. |
| `/silvercord/{token}/workspace`, `/silvercord/{token}/graph` | Unchanged. |

Rules:

1. **Rule for later kinds.** A new kind that is an ActivityPub object and an item of its own gets a token on `/silvercord/{token}` (owner rule 2026-11-23). A part or a view of an item is a sub-path of the token of that item. An edition is a part of its document (the glossary makes the body a part of the document); the opinions and the graph are views.
2. **Inheritance.** A sub-path takes the alias, gone, hidden, and link access rules of the document token. An alias gives 308 to the same sub-path of the target. A template that the document does not have gives 404.
3. **ActivityPub.** A request that accepts only ActivityPub gets 303 from an edition page to its object ID, as the document page does now.
4. **Remote editions.** A remote edition has no local token, because a token names an item of this server. The local document page shows it in the sandbox frame with the render token of the federation client and keeps the selection in its query (`?edition={short key}`). The citation and the "open on the origin" link use the address on the origin.
5. Reasons against one token for each edition: the registry would need a new kind and new alias and gone rules that copy those of the document; a near-duplicate merge would need more alias rows; the edition already has a stable identity in (document, template).

### 5.9 ED9: 게시 to 발행 in all layers (answer to Q4)

Replace the term in all layers for the publication of documents. Keep 게시 for notes. Remove the retired text that uses 발행 in another sense. After the change each word has one concept.

| Layer | Change |
| :--- | :--- |
| Catalog values (ko) | 게시 → 발행 in `nav.syndication`, `home.flow.label`, `home.flow.publish`, `home.flow.published`, `synd.stage.published`, `synd.stage.unpublished` (미게시 → 미발행), `synd.client.action.publish`, `synd.kb.menu.publish`, `synd.kb.filter.label`, `synd.kb.filter.unpublished`, `synd.kb.filter.published`, `synd.palette.stage_publish`, `console.synd.reader.app_tab_kb`. |
| Catalog values (ko), notes | 발행 → 게시 in `home.note_dlg.publish` (노트 게시), `home.note_dlg.publishing`, `home.note_dlg.published`, `home.note_dlg.error`. `synd.compose.submit` stays 게시. |
| Catalog keys | `home.note_dlg.publish*` → `home.note_dlg.post*`, so that the key names the concept. The document keys already use `publish`. |
| Catalog values (en) | Documents stay "Publish" and "publication". Notes: `synd.compose.submit` and `home.note_dlg.*` → "Post" (O7). |
| Retired texts | Remove the sections `/settings/connections` and `/settings/outbound`, their partials, and the keys `console.synd.connections.*` and `console.synd.outbound.*` (fact 13). |
| Code identifiers | No change for documents: routes, tables, the stage key `publish`, and functions already use `publish`. |
| Documents | RD10 table and rule 6 of [`FEDERATION_REMOTE_READING_DESIGN.md`](FEDERATION_REMOTE_READING_DESIGN.md) (Publication (게시) → Publication (발행)); [`GLOSSARY.md`](../implementation/GLOSSARY.md) Section 1.11 and the terms of Section 4; [`FEDERATION.md`](../implementation/FEDERATION.md) where it names the stage. |
| Tests | The i18n tests and the E2E specs that read these texts. |

No HTTP route and no JSON field changes its name.

### 5.10 ED10: Reader interface (modern web guidance)

The design uses the guides of the `modern-web-guidance` tool (version `2026_10_12-dacb5911`): `rich-media-picker`, `cross-document-transitions`, `scoped-component-transitions`, `improve-next-page-load-performance`, `defer-rendering-heavy-content`.

1. **Edition picker.** A native `<select>` with a visible `<label>` ("에디션"), with `appearance: base-select` and rich `<option>` content: template name, account, server, likes, date. The text of each option must make sense alone, because Firefox shows a plain list ("간결. @bob@b.example. 좋아요 12."). Use visually hidden full stops for that. Put no button or link inside an option. The selected option must be visible in the open list. Support: Chrome and Edge 135, Safari 27; Firefox shows the native list.
2. **Like control.** A `<button aria-pressed="true|false">` next to the edition heading, outside the picker, with the line icon `heart` of the prototype (SD8) and the count in its visible text. An anonymous visitor sees the count and a sign-in link, not a dead button.
3. **Switching local editions.** A local edition is a page (Section 5.8), so a switch is a navigation. Use `@view-transition { navigation: auto; }` inside `@media (prefers-reduced-motion: no-preference)`. Chrome, Edge, and Safari 18.2 animate; Firefox navigates at once.
4. **Switching to a remote edition, and new opinions.** The frame changes inside the page. Use `element.startViewTransition()` on the reader container or on the opinions container (Chrome and Edge 147). Other browsers change the frame at once, without `document.startViewTransition()`, so that the fixed bottom bar and the popovers stay on top.
5. **Prefetch.** Speculation rules with a document rule for the edition links of the picker, `prefetch`, eagerness `moderate`. No `prerender` (signed-in pages have state). Exclude `/silvercord/*/workspace` and each state change. Chromium only. Whether Chrome uses a prefetched answer with `Cache-Control: private, no-store` is **[unverified]**; measure it first.
6. **Long bodies and long opinion trees.** `content-visibility: auto` with `contain-intrinsic-size: auto 800px` on body sections and opinion groups below the first screen, never on the first screen. Test keyboard navigation through these sections.
7. **Mathematics.** `academic` and the other templates keep formulas; no template is a display fallback for formulas.
8. Each new text uses catalog keys in English and Korean from the start.

### 5.11 ED11: Opinions from other servers (answer to Q7)

Intent: the `의견` tab of a published object shows the public conversation about it in the whole fediverse, not only the replies that reached this server. The structure follows Mastodon 4.5 and later (owner: "최신 Mastodon … 구조를 우선"), and adds one scheduled refresh from the FediFetcher idea.

**A. Publish: make Claire threads readable for other servers.** This part improves Claire as an ActivityPub server, also without editions.

1. Each published object (document, edition, own note) gets `replies` in the form of Mastodon (R13): a `Collection` whose `first` page has the self-replies of the author (at most 5) and whose `next` goes to pages of the replies of other accounts. An item is the IRI of a public reply that this server knows (delivered or fetched) and that is not hidden. Pages have 20 items and a cursor. Reason: Mastodon 4.5 and later crawl `replies` (R12); without it, a reader on Mastodon sees only the replies that reached the server of that reader.
2. `likes` and `shares` with `totalItems` from `federation_object_reactions` (R13).
3. `context` stays absent in the first step. A later step can add a `context` collection (FEP-7888 with FEP-f228) for software that reads it; Mastodon does not (R15).

**B. Fetch: the reply tree, as Mastodon does it.**

1. **Trigger.** A request of the opinions of an object (`GET /silvercord/{token}/opinions`, the `의견` tab, and the view of a published object in `/syndication`). Anonymous visitors also start it: the conditions of rule 2 hold for each object, so more visitors do not make more fetches.
2. **Conditions.** The object is published and not gone; the last fetch for the object is older than 15 minutes; the object is older than 5 minutes (R12). The values are constants in the code, as Mastodon 4.5 removed its settings; no new admin setting.
3. **Start set.** The replies that this server has for the object (delivered, fact 7, and fetched before), and the quoting objects of the approved stamps of the object (`interactingObject`, FEP-044f) as opinions of the kind `quote`.
4. **Walk.** For each reply: get the object from its origin (R11), read its `replies` collection with its `first` and `next` pages, and add the IRIs that are new. When an object has a `context` that is a collection, read it too and add the objects of the same tree (strategy `context-auto` of `@fedify/backfill`, fact 8). The reply tree comes first because Mastodon publishes it (R13, R15). Quote posts are opinions; their own replies are not fetched.
5. **Limits.** 1000 opinions for one object, 500 under one reply, 500 collection pages, 120 seconds for one task (R12, fact 8). Blocked domains and private addresses get no request.
6. **Refresh state.** While a fetch runs, the JSON of the opinions has `refresh`: `{"id", "status": "running" | "finished", "retry": <seconds>, "result_count": <new opinions>}`, the three values of the Async Refreshes of Mastodon (R14). The page waits `retry` seconds, asks again (at most 10 times), and shows the new opinions with the transition of Section 5.10 rule 4.
7. **Known opinions of actors that no account here follows.** No `Delete` comes for them. Each fetch gets them again; a 404 or a 410 deletes the row (R12).
8. **Signature.** The server actor signs the fetches, not an account: the page is public, and the result belongs to the object, not to an account.
9. **Sidecar.** A new task kind `fetch_opinions` in the thread fetch loop (fact 8), with the object IRI as the start. The same rules for objects as now: each object from the origin of its ID, a public post (`Note`, `Article`, `Page`, `Question`), an author on the same origin. Before phase E7, confirm that `@fedify/backfill` 2.4.6 follows collection pages (R15); if it does not, the sidecar reads the pages itself.

**C. Store.**

1. `client.db` table `federation_opinions`, for the server, not for an account: key (`object_iri`, `opinion_iri`); `parent_iri`, `kind` (`reply`, `quote`), `source` (`delivered`, `fetched`), `author_iri` and the author profile (name, handle, avatar through the media proxy), `content_html` (cleaned), `language`, `summary` and `sensitive` (content warning), `published`, `updated`, `fetched_at`, `hidden_at`, `hidden_by`.
2. `object_iri` can be a remote edition: a reader of a remote edition gets the opinions of its origin with the same task.
3. A delivered reply to a published document also stays a timeline item of the owner (fact 7). The opinion row is a separate row for the public page.
4. A retract of the object deletes its opinions. A remote `Delete` deletes one opinion. The account purge does not delete opinions of other actors on other objects.

**D. Show: the `의견` tab (replaces the prototype of SR5 and SD8).**

1. A 글타래 tree of the replies by time, with a depth of 4 on the page and a link "원 서버에서 계속" for deeper levels; the quote posts in a separate group "인용".
2. The remote HTML is in one sandbox frame from the usercontent host (fact 14), with `X-Robots-Tag: noindex`: the posts of other people are not indexed under this server.
3. Each opinion has the author (link to the profile on its origin), the time (link to the post on its origin), the content warning (closed), and the text. The top of the tab has the counts of replies, quotes, likes, and boosts.
4. A signed-in account that is an actor can reply, like, boost, and quote with the functions of the federation client (Section 25 and 26 of `FEDERATION.md`).
5. The owner of the document and the server admin can hide an opinion from this page. A hidden opinion is also not in the `replies` collection. This is moderation of the page, not a quote refusal: the stamp of a quote stays valid (Section 5.7).
6. A document of a private knowledge base with link access is not published; its tab keeps the notice that it is not federated (SILVERCORD design access matrix).

**E. Scheduled refresh (from FediFetcher, R16).** Once a day, the server fetches the opinions of its own published objects that got a delivered reply, a like, or a quote request in the last 7 days, with the limits of rule B5 and at most 200 objects in one run. Reason: Mastodon fetches only when a person opens a post; this refresh keeps the tab current also before the next visit. The fetches of other posts (home timeline, bookmarks) stay with the thread fetch of the client (fact 8): FediFetcher's scan of all timelines is not taken.

**F. The thread fetch of the client (fact 8).** It changes in one point: it reads the reply tree and the `context` collection together and merges them, not the reply tree only after an empty `context`. Reason: Mastodon gives a `context` IRI that is not a useful collection, and a reply tree that is complete (R13, R15).

---

## 6. Data (proposal)

| Item | Proposal |
| :--- | :--- |
| `document_editions` (`claire.db`) | `doc_id`, `kb_id`, `template`, `template_version`, `detail`, `detail_format`, `detail_html`, `created_at`, `updated_at`, `model_lineage` (JSON), unique (`doc_id`, `template`). |
| Primary edition body | `documents.detail*` stays the body of the primary edition, so that the 38 readers (fact 2) do not change. `document_editions` has the other editions, and a row without a body for the primary edition (template and version). The primary edition does not change (O6), so no body moves. |
| `documents.source_key`, `documents.source_id_version` | Section 5.3. Index on `source_key`. |
| `federation_objects` | New columns `kind` (`document`, `edition`), `parent_object_id`, `opinions_fetched_at`. The `CHECK` of the outbox kinds needs no change: editions send no task (Section 5.4 rule 2). |
| Body readers (`claire.db`) | (`object_public_id`, `server_actor_iri`, `first_read_at`, `last_read_at`) for the exact delete list (Section 5.4 rule 4). |
| Remote editions (`client.db`) | Cache rows: edition IRI, origin, source key, template, template version, account IRI, name, `likes.totalItems`, content hash, body, `ETag`, `fetched_at`, `expires_at`. |
| Likes | `federation_object_reactions` for inbound `Like` (existing). Outbound likes of the account in the tables of the outbound interactions (existing favourites). |
| Opinions (`client.db`) | `federation_opinions` (Section 5.11 C). |
| Migrations | They add only what is absent, keep unknown data, and complete at the start of the process. No operator step. |

---

## 7. Rejected alternatives

| Alternative | Reason |
| :--- | :--- |
| A different JSON for each reader (Claire servers get the body by signature) | One object ID must have one representation; caches and the same-origin rule break. |
| The body in `content` (FEP-b2b8) | The owner keeps the body on Claire (RD21); Mastodon would show the full body as a status. |
| The document summary in `summary` | Mastodon shows `summary` as a content warning (R1). |
| `context` for the edition set | Backfill software reads `context` as a conversation (R6). |
| A server actor mesh that pushes editions to all Claire servers | It is a relay; relays are ideas, not design ([`RETIRED_PARTNERSHIP_IDEAS.md`](../ideas/RETIRED_PARTNERSHIP_IDEAS.md)). |
| An edition as a second ingest of the same source | Two documents of one source in one knowledge base double the sources of each entity. |
| Templates that an account writes | Not comparable across servers; a prompt injection path; no fixed meaning on the wire. |
| A capability flag (FEP-844e) or the NodeInfo software name | FEP-844e forbids it when the object properties show the capability (R4). |
| FEP-bad1 history for editions | History is a sequence of changes in time; editions are parallel. |
| A token for each edition | Section 5.8 rule 5. |
| A separate +1 activity or counter, and a document object apart from the primary edition | Owner decision O5: the `Like` as it is. One object less for each document. |
| A scan of all timelines on a schedule (FediFetcher) | Mastodon chose the fetch on open (R12, R16); a scan multiplies requests to other servers. Claire refreshes only its own objects with new activity (Section 5.11 E). |
| The header `Mastodon-Async-Refresh` | It belongs to the client API of Mastodon, which Claire does not have. Claire uses the same three values in its JSON (R14). |
| Inbox forwarding of replies (ActivityPub Section 7.1.2) for opinions | It applies only when a reply addresses a collection of this server; Mastodon replies do not address the followers of the replied author. The `replies` collection (Section 5.11 A) gives the thread to each crawling server. |

---

## 8. Security and privacy notes

1. A remote body and a remote opinion are untrusted HTML. Clean them on receipt and show them only in the sandbox frame of the usercontent host.
2. The body path and the source index answer only signed server actors, with KS1 controls and quotas.
3. A source index request shows only the key and the asking server (Section 5.4 rule 5).
4. A retract, a private knowledge base, a hidden document, a purged document, a deleted knowledge base, and an account purge make the edition objects 410 or 404, as for the document object now (FP3-4 privacy triggers), delete the opinions of these objects, and send `Delete` to the servers that read a body.
5. The opinions frame has `noindex`. The `replies` collection lists only public replies that are not hidden.
6. An anonymous request can start an opinion fetch, but at most one for each object in 15 minutes (Section 5.11 B rule 2).

---

## 9. Phases

| Phase | Content | Depends on | Size |
| :--- | :--- | :--- | :--- |
| E0 | Terms (Section 5.9): catalog values and keys, removal of the retired sections, glossary, design documents. | — | S |
| E1 | Quotes always permitted for documents (Section 5.7, quotes 1 to 4) and the migration. | — | S |
| E2 | Citation metadata, `fediverse:creator`, `attributionDomains` (Section 5.7, citation). | — | S |
| E3 | Templates and local editions: ingest selection, "에디션 추가", `document_editions`, the placeholder `prompt_template` (Section 5.2 rule 6), edition sub-paths and the picker of local editions (Sections 5.1 to 5.3, 5.8, 5.10). The template texts exist. | — | M |
| E4 | Vocabulary `v2`, edition objects, `claire:editions`, body path, likes of editions (Sections 5.5, 5.6). | E3 | M |
| E5 | Source index, remote edition cache, exact delete list, remote editions in the picker and in the sandbox frame (Section 5.4). | E4 | M |
| E6 | `replies`, `likes`, `shares` on published objects and own notes (Section 5.11 A). | — | S |
| E7 | Opinion fetch, store, refresh state, the `의견` tab, hide (Section 5.11 B to D), the change of the client thread fetch (Section 5.11 F). | E6 | M |
| E8 | Scheduled refresh (Section 5.11 E). | E7 | S |

E0, E1, E2, E3, and E6 do not depend on each other.

### 9.1 Frontend coverage

The design has backend and frontend parts. The backend parts (wire forms, data, sidecar tasks, limits) are complete for implementation. The frontend parts are rules; there is no visual prototype yet. Each other design of a page in this project starts from a prototype in `docs/design/prototypes/` (for example the Silvercord design), so a prototype comes before the frontend work of E3, E5, and E7.

| Surface | In this design | Not yet designed |
| :--- | :--- | :--- |
| Silvercord view (`share.html`) | Edition picker, like control, edition switch, prefetch, long bodies (5.10); `의견` tab: tree, sandbox frame, counts, actions, hide (5.11 D); citation metadata and the copy action (5.7); edition and opinion sub-paths (5.8) | Visual prototype; place of the picker and the like control in the mobile bottom bar (SD5); empty, loading, and error states; the refresh indicator of opinions |
| Ingest (`/ingest`, Telegram bot, CLI, MCP) | Template selection for each request, "에디션 추가" in the document menu (5.3) | Form layout; the bot and CLI syntax; the cost display before a run |
| `/syndication` knowledge base view (stage 3) | Term changes (5.9); removal of the quote policy dialog of documents (5.7) | Editions and opinion counts in the list of published documents; the publish dialog with the editions that go out |
| Workspace reader and context panel | — | Edition switch inside the workspace |
| Account home timeline | — | A document post with its editions |
| Console | Removal of `/settings/connections` and `/settings/outbound` (5.9) | — |
| Notifications | — | A new opinion or a like as a notification kind |
| Catalog | The rule "keys in English and Korean from the start" (5.2, 5.10) | The list of keys |

Tests (global rules: unit tests only for module interfaces, input and output, and calls to external libraries; at least one E2E test for each new feature):

- E2E: an edition switch in the picker and a like on a Silvercord page; the `의견` tab with fetched opinions (Playwright through `scripts/e2e.sh`); the vocabulary fixtures of `v2`; a DrFed scenario for the edition object, the body path with a signed server actor, the `replies` collection, and an inbound `Like`; two development servers for the source index and the opinion fetch (acceptance path as KA1 to KA5).
- Unit: the template set (exists), the source key normalization (input and output), the body representation builder and validator, the `replies` collection pages, the internal API contract fixtures.

---

## 10. Open measurements

1. Mastodon 4.7: show of an `Article` with `content` of the RD21 form and the new `claire:` properties.
2. Mastodon 4.5 or later: a quote of a Claire document with the automatic stamp.
3. Mastodon 4.5 or later: a crawl of the `replies` collection of a Claire object (do replies from a third server appear on Mastodon).
4. `@fedify/backfill` 2.4.6: pages of `replies` and `context` collections (R15).
5. Chrome: use of a prefetched answer with `Cache-Control: private, no-store` (Section 5.10 rule 5).

---

## 11. Owner decisions

| # | Question | Decision (2026-11-25) |
| :--- | :--- | :--- |
| O1 | Number of templates; terms 에디션 and 에디션 템플릿 | A: three in total. The terms of Section 4. |
| O2 | Texts of the templates | `concise`: written by the agent from a copy of the current prompt, saved in the code. `no_math` became `academic` (학문 특화): an exact copy of the current prompt; the owner changes it later. |
| O3 | Quote policy of notes | A: notes keep the four policies. |
| O4 | Files without a URL in edition sets | A: not in the first version. |
| O5 | Favourites and the mark of editions | The ActivityPub `Like` as it is. The likes can feed the reputation (Section 5.6 rule 7). |
| O6 | Change of the primary edition | Recommendation: always the first edition in phase E3. |
| O7 | English word for a note | Recommendation: "Post". |
| O9 | Fill the timeline with recent posts of a newly followed actor (the profile function of FediFetcher, R16) | A: not in this design. The idea is in [`TIMELINE_BACKFILL_IDEAS.md`](../ideas/TIMELINE_BACKFILL_IDEAS.md). |

Open:

| # | Question | Options | Recommendation |
| :--- | :--- | :--- | :--- |
| O8 | Likes as a reputation signal | **On hold** (2026-11-25). There are two kinds of reputation: of the publisher and between published editions. The plan has a gap, and a separate score can be necessary. Section 5.6 rules 6 and 7 apply until a decision. | — |

---

## 12. Modern Web Guidance

Section 5.10 lists the guides and the rules that came from them. The search for MathML display gave no guide, so this design does not use a guide for formulas.

## 13. Sources

- Mastodon documentation, ActivityPub: <https://docs.joinmastodon.org/spec/activitypub/>
- Mastodon documentation, configuration, Fetch All Replies: <https://docs.joinmastodon.org/admin/config/#fetch-all-replies>
- Mastodon documentation, statuses (context) and async refreshes: <https://docs.joinmastodon.org/methods/statuses/>, <https://docs.joinmastodon.org/methods/async_refreshes/>
- Mastodon source code, branch `main` (2026-11-25): `app/workers/activitypub/fetch_all_replies_worker.rb`, `app/services/activitypub/fetch_all_replies_service.rb`, `app/models/concerns/status/fetch_replies_concern.rb`, `app/serializers/activitypub/note_serializer.rb`, `app/controllers/activitypub/replies_controller.rb` at <https://github.com/mastodon/mastodon>
- Mastodon 4.4.0, 4.6.0, 4.7.0, 5.0.0-beta.2 releases: <https://github.com/mastodon/mastodon/releases>
- Mastodon 4.5 for developers (2025-10-29): <https://blog.joinmastodon.org/2025/10/mastodon-4-5-for-devs/>
- Mastodon 4.6 for developers (2026-06-12): <https://blog.joinmastodon.org/2026/06/mastodon-4-6-for-devs/>
- FediFetcher: <https://github.com/nanos/FediFetcher>; its author on the built-in fetch (2025-07-21): <https://blog.thms.uk/2025/07/fetching-replies-mastodon/>
- `@fedify/backfill` pages issue: <https://github.com/fedify-dev/fedify/issues/1248>
- Community guide on threads, replies, and backfill (2026-10-15): <https://github.com/socialdocs/socialdocs.org/pull/58>
- FEP-044f Quote posts: <https://codeberg.org/fediverse/fep/src/branch/main/fep/044f/fep-044f.md>
- FEP-b2b8 Long-form Text: <https://codeberg.org/fediverse/fep/src/branch/main/fep/b2b8/fep-b2b8.md>
- FEP-844e Capability discovery: <https://codeberg.org/fediverse/fep/src/branch/main/fep/844e/fep-844e.md>
- FEP-7888, FEP-f228, FEP-2931 (context): <https://socialhub.activitypub.rocks/t/fep-2931-representing-context-with-a-collection/5154>, <https://codeberg.org/fediverse/fep/pulls/499>
- FEP-bad1 Object history collection: <https://socialhub.activitypub.rocks/t/fep-bad1-object-history-collection/3352>
- FEP-dc88 Formatting mathematics: <https://socialhub.activitypub.rocks/t/fep-dc88-formatting-mathematics/3564>, <https://codeberg.org/fediverse/fep/pulls/181>
- FEP-c118 Content licensing: <https://socialhub.activitypub.rocks/t/fep-c118-content-licensing-support/2903>
- FEP-fe34 Origin-based security model: <https://socialhub.activitypub.rocks/t/fep-fe34-origin-based-security-model/4292>, Fedify 1.9.0: <https://socialhub.activitypub.rocks/t/fedify-1-9-0-released-security-enhancements-improved-dx-and-expanded-framework-support/8132>
- Social Web Foundation, long-form text (2025-05-01): <https://socialwebfoundation.org/2025/05/01/steps-forward-in-long-form-text/>
- W3C Social Web Working Group charter: <https://www.w3.org/2026/01/social-web-wg-charter.html>; adoption of the CG drafts (May 2026): <https://lists.w3.org/Archives/Public/public-socialweb/2026May/0012.html>
- RFC 8574 cite-as: <https://www.rfc-editor.org/rfc/rfc8574.html>
- Mastodon author attribution (`fediverse:creator`): <https://blog.joinmastodon.org/2024/07/highlighting-journalism-on-mastodon/>
