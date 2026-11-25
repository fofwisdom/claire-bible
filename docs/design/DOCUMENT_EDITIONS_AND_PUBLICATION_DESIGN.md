# Editions of Published Documents and ActivityPub Publication (`DOCUMENT_EDITIONS_AND_PUBLICATION_DESIGN`)

- **Document Identifier**: `DESIGN-DOC-EDITIONS-20261125`
- **Status**: Proposal. Not implemented. Owner decisions O1 to O7 (Section 11) wait.
- **Date**: 2026-11-25
- **Base commit**: `8e6b0723`
- **Related**: [`FEDERATION_REMOTE_READING_DESIGN.md`](FEDERATION_REMOTE_READING_DESIGN.md) (RD10, RD21), [`KNOWLEDGE_FEDERATION_DESIGN.md`](KNOWLEDGE_FEDERATION_DESIGN.md) (vocabulary, known servers), [`PERMALINK_UNIFICATION_DESIGN.md`](PERMALINK_UNIFICATION_DESIGN.md), [`SILVERCORD_RENDERING_DESIGN.md`](SILVERCORD_RENDERING_DESIGN.md), [`FEDERATION.md`](../implementation/FEDERATION.md), [`KNOWLEDGE_FEDERATION.md`](../implementation/KNOWLEDGE_FEDERATION.md), [`PERMALINKS.md`](../implementation/PERMALINKS.md), [`EXTRACTION.md`](../implementation/EXTRACTION.md), [`GLOSSARY.md`](../implementation/GLOSSARY.md)

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

Questions of the request and the answers of this design:

| # | Question | Short answer | Section |
| :--- | :--- | :--- | :--- |
| Q1 | Exchange editions between Claire servers, choose an edition of another user, give +1 | Yes. An edition is one readable body of a document, made with one fixed template. Editions of the same source make an edition set across servers. +1 is an ActivityPub `Like` on one edition. | 5.1 to 5.4, 5.6 |
| Q2 | Three fixed templates | `detailed` (the current prompt), `concise`, `no_math`. The template changes the body only. The owner writes the template texts later. | 5.2 |
| Q3 | Only the summary to other ActivityPub servers? | Yes. The standard fields of each Claire object carry the summary and the link only. The bodies of editions are only behind a Claire extension link that a signed server actor of a Claire server reads. | 5.5 |
| Q4 | Replace 게시 with 발행, also inside? | Yes, for the publication of documents in all layers: catalog values and keys, documents, and the glossary. The code identifiers already use `publish`. A note stays 게시 (a post), because it is a different concept. | 5.9 |
| Q5 | Silvercord path: fixed, more paths, or extension inside the path? | Keep the one path word `/silvercord/{token}`. Add no path word. An edition is a part of its document, thus it is a sub-path: `/silvercord/{token}/editions/{template}`. | 5.8 |
| Q6 | No refusal of quotes | Documents and editions are always quotable: `canQuote` with automatic approval for the public collection, and an automatic approval stamp for each request. | 5.7 |

Reading of Q2 **[decision needed: O1]**: "고정 템플릿 3개" is read as three templates in total, and the current detailed prompt is one of them. Section 11 gives the other reading.

---

## 2. Facts at the base commit

1. **[code] Published object.** The `Article` of a published document has `name` (title), `content` (the summary paragraph and a paragraph with the permalink), `url` (the permalink), `published`, `updated`, `to`, `cc`. `summary` is not set. The body is never in the object (RD21; [`FEDERATION.md`](../implementation/FEDERATION.md) Section 4.4, Section 5.8).
2. **[code] One body for each document.** `ensure_document_detail` (`src/claire/ingest/pipeline.py`) makes the readable body with a separate model call. Its docstring says that it is independent of the graph: it fills `documents.detail` only and does not change entities or relations. The prompt is `render_detail_prompt_adoc` (`src/claire/extract/prompts.py`). 38 Python files under `src/claire` read `detail` or `detail_html`.
3. **[code] Summary.** The summary comes from the structured extraction (`PROMPT_VERSION = "extract-v8"`), not from the body prompt. Search, hover cards, and the published object use it.
4. **[code] Permalink.** `/silvercord/{token}` is only for kinds that can be ActivityPub objects (`ACTIVITYPUB_KINDS`: document, note; owner rule 2026-11-23). The sub-paths `/silvercord/{token}/workspace` and `/silvercord/{token}/graph` are views of the same item. An alias token gives 308 also for the sub-paths ([`PERMALINKS.md`](../implementation/PERMALINKS.md) Sections 2, 3.1, 3.2).
5. **[code] Reactions.** A `Like` or an `Announce` of `{origin}/ap/objects/{public_id}` is stored one time for each actor and kind (`federation_object_reactions`). The counters are in `GET /api/federation/objects` ([`FEDERATION.md`](../implementation/FEDERATION.md) Section 22.4).
6. **[code] Quotes.** FEP-044f is implemented. A published document has the policy `manual` by default; the policies are `public`, `followers`, `manual`, `nobody`; the owner can refuse and revoke ([`FEDERATION.md`](../implementation/FEDERATION.md) Section 26.4).
7. **[code] Claire vocabulary.** The context `https://w3id.org/claire-bible/ns/v1` has `claire:Extraction`, `claire:contentHash`, `claire:root`, and other terms. The context of a version must not change after a server serves it; a new term goes to a new version ([`KNOWLEDGE_FEDERATION.md`](../implementation/KNOWLEDGE_FEDERATION.md) Section 2). A collection of shared knowledge answers only a request that a server actor of another server signed (KS1, Section 10.3 of the same document).
8. **[code] Source identity.** `documents.canonical_url` comes from `canonicalize_url` (`src/claire/ingest/normalize.py`: host to lower case, tracking parameters removed, arXiv versions joined to `/abs/{id}`). `documents.content_hash` is SHA-256 of the normalized text.
9. **[code] Terms.** `ko.json` has 44 values with 게시 and 55 values with 발행. The stage name is 게시 (`nav.syndication`, `home.flow.publish`, `synd.stage.*`, `synd.kb.filter.*`). The publish dialog of a document uses 발행 (`synd.kb.publish.*`). The note dialog of the account home uses 발행 (`home.note_dlg.publish*`), and the compose button of `/syndication` uses 게시 (`synd.compose.submit`). The glossary defines 게시물 (Post) as a note or a document in the timeline (Section 1.11). The English texts use "publication" and "Publish" for documents and for notes. The code identifiers of document publication are `publish` (`POST /api/federation/publish`, `federation_objects.published_*`, stage key `publish`).
10. **[code] Retired text that is still visible.** The console sections `/settings/connections` and `/settings/outbound` (`SYNDICATION_SECTION_SLUGS` in `src/claire/api/accounts_routes/console_syndication_panels.py`) show the texts `console.synd.connections.*` and `console.synd.outbound.*`. These texts describe the retired model of connected external accounts ("Claire 는 ActivityPub 서버를 … 직접 구현하지 않습니다") and use 발행 in that retired sense ("적재 발행", "사용자 간 발행 본문 공유").
11. **[code] Remote content in the page.** The federation client puts no content of a remote server into the page DOM. The body of a remote post stays in the sandbox frame ([`FEDERATION.md`](../implementation/FEDERATION.md) Section 26.6).
12. **[code] Page metadata.** The Silvercord document view has no `rel="cite-as"`, no `citation_*` meta elements, and no `fediverse:creator`. The actor document has no `attributionDomains`.

---

## 3. Research findings (2026-11-25)

Sources are in Section 13. A statement from a third party that this design could not confirm has the tag **[unverified]**.

| # | Finding | Effect on this design |
| :--- | :--- | :--- |
| R1 | Mastodon converts an `Article` or a `Page` to a status: it "uses `content` if available, or `name` if not", it appends `url`, and "the `summary` property will be used as the CW text" (Mastodon ActivityPub documentation). | The RD21 shape is correct for Mastodon. Do not put the summary in `summary`: Mastodon would hide it behind a content warning. |
| R2 | FEP-b2b8 "Long-form Text" (DRAFT, received 2024-11-07) puts the full text in `content`, a teaser of about 500 characters in `summary`, the original format in `source`, and an optional `preview` (`Note`) for software that does not show `Article`. | Claire departs from FEP-b2b8 on purpose: the owner keeps the body on Claire (RD21). `summary` conflicts with R1. Claire does not add `preview` now: Mastodon does not read it. |
| R3 | FEP-044f (DRAFT): the quote policy is advisory; a quote is valid only with a `QuoteAuthorization` stamp from the author; to forbid all quotes, `automaticApproval` contains only the author. Mastodon 4.5 offers automatic approval only (public, followers, or nobody) and shows an object without a policy as not quotable. | A Claire document must always advertise a policy and always answer a request with a stamp, also when approval is automatic. |
| R4 | FEP-844e "Capability discovery" (DRAFT, received 2025-06-14) puts `implements` on an `Application` object. It says: "Implementers MUST NOT use it in cases where capabilities can be inferred from properties and types of objects", and it discourages NodeInfo for this. | Claire infers edition support from the `claire:` properties of an object. Claire adds no capability flag and does not read the NodeInfo software name for this. |
| R5 | Mastodon 4.6 (June 2026): collections with a full ActivityPub representation (FEP-7aa9) and authorizations like quote posts; `interactionPolicy.canFeature` on actors; the JSON-LD profile in the `Accept` header of object fetches; multiple key pairs; FEP-2c59; FEP-3b86 (Activity Intents). | `interactionPolicy` is now a common pattern for consent. The permalink route already treats `application/ld+json` as an ActivityPub request (`src/claire/api/server.py`). |
| R6 | No FEP defines parallel editions. FEP-bad1 (object history collection) is about edits in time. FEP-7888, FEP-f228, and FEP-2931 use `context` for conversation containers and backfill. | Editions use Claire terms. Do not use `context` to group editions: backfill software would read the group as a conversation. |
| R7 | The W3C Social Web Working Group started on 2026-01-15. In May 2026 it adopted the Community Group drafts of ActivityPub, Activity Streams, and Activity Vocabulary. The revisions are backward-compatible, and extensions stay in the Community Group. The 2018 Recommendation is still the latest published version. | No protocol change blocks this design. Extensions (FEPs, the Claire vocabulary) stay the method. |
| R8 | FEP-dc88 (mathematics in MathML Core, DRAFT) has no recent change, and the FEP maintainer said that it will be withdrawn. A user reports that Mastodon removes MathML and keeps the TeX annotation **[unverified]**. | Claire sends no mathematics in federated text. The summary is plain text. |
| R9 | FEP-c118 (content licenses, DRAFT, 2023) has no agreed form. | No license property now. |
| R10 | RFC 8574 (informational) defines `rel="cite-as"` for the preferred URI to reference a resource. Mastodon shows an author byline in link previews from `<meta name="fediverse:creator">` when the actor lists the site domain in `attributionDomains`. | The Silvercord view gets `cite-as`, citation metadata, and `fediverse:creator`; the actor gets `attributionDomains` (Section 5.7). |
| R11 | FEP-fe34 (origin-based security): an object is trusted for the origin of its ID. Fedify 1.9 and later enforce the same-origin rule. | The body of an edition must be on the origin of the edition object. |

---

## 4. Terms (proposal for `GLOSSARY.md`)

| Term | English | Definition | Not used |
| :--- | :--- | :--- | :--- |
| **에디션** | Edition | One readable body (상세) of a document, made with one edition template. A document has one edition for each template that it has. The summary, the entities, and the relations belong to the document, not to an edition. | "판", "버전", "리비전" (a revision is a change in time; an edition is a parallel body) |
| **에디션 템플릿** | Edition Template | A fixed instruction set for the body prompt, with an identifier (`detailed`, `concise`, `no_math`) and a version. The code defines the set. | "프롬프트" alone, "초점" (`focus` is sealed data, CT28) |
| **기본 에디션** | Primary Edition | The edition that the permalink of the document shows first. It is the edition of the first ingest. | — |
| **출처 식별자** | Source Identifier | The normalized identifier of the source of a document (Section 5.3). Documents with the same source identifier have editions of the same source. | "작품", "원본 ID" |
| **에디션 묶음** | Edition Set | All published editions with the same source identifier, on this server and on other Claire servers, and the own editions of the reader. | — |
| **+1** | Plus One | The mark of an account on one edition. On the wire it is a `Like` of the edition object. | "좋아요" (that is the favourite of a post, `synd.compose.action.favourite`) |
| **발행** | Publication / Publish | The action that makes a document of a public personal knowledge base an ActivityPub object for followers and makes its editions available to Claire servers. Stage 3 of the flow 교류 · 적재 · 발행. | 게시 for this action |
| **게시** | Post | The action that sends a note. 게시물 (Post) stays the name of an item in the timeline. | 발행 for a note |
| **출처 표기** | Citation | The bibliographic reference to a document or an edition: `cite-as`, citation metadata, the copy action. | 인용 for this meaning (인용 is the quote of FEP-044f) |

Note on 에디션: the glossary uses "에디션" already in the same-entity rule ("버전·에디션·제품과 회사"). There it is a word about products in the world, not a Claire concept. "Shopify Editions" in the graph engine design is a product name. Neither is a Claire data concept, thus the new term has no collision. **[decision needed: confirm the term, O1]**

---

## 5. Design

### 5.1 ED1: Edition model

1. An edition is a document body made with one edition template. One document has at most one edition for each template.
2. The template changes the body only. The structured extraction (summary, key claims, entities, relations) stays one for each document. Reason: fact 2 says that the body render is already independent of the graph, so a second edition adds no entity and no relation, and the graph stays clean.
3. A new prompt version of the same template replaces the body of that edition. The edition keeps its identity, its object ID, its address, and its +1 marks. Reason: +1 is for the edition of a template of a document, and the text gets better in time.
4. The primary edition is the edition of the first ingest. The owner of the document can make another own edition the primary edition **[decision needed: O6]**.
5. A reader of an edition set sees editions of other accounts. These editions never enter the knowledge base of the reader. Rule 1 of RD10 stays: collection is not ingestion. The reader can ingest the same source with an own template; that makes a new document and a new edition.

### 5.2 ED2: Edition templates

| Identifier | Korean name | Intent | Text |
| :--- | :--- | :--- | :--- |
| `detailed` | 상세 | The current body prompt (`render_detail_prompt_adoc`). The default. | Exists |
| `concise` | 간결 | A short structured body: the main claims, the method, the result, in about one A4 page. Tables stay when they carry the result. | Owner, later (O2) |
| `no_math` | 수학 제외 | The same scope as `detailed`, but each formula becomes a sentence in words. No `stem` blocks. For readers who do not read formulas. | Owner, later (O2) |

Rules:

1. The code defines the set. An account cannot make a template. Reasons: the editions of an edition set must be comparable across servers; a free prompt from an account is a prompt injection path into a public page; a template identifier on the wire must have one meaning.
2. A template has an identifier, a version (as `PROMPT_VERSION`), and catalog keys `edition.template.{id}.label` and `edition.template.{id}.desc` in English and Korean from the start.
3. A new template is a new identifier in the code and a new value in the vocabulary (Section 5.5). An identifier never changes its meaning and is never used again for a different template.
4. The common rules of the body prompt stay in all templates: AsciiDoc only (RD23), no text for a section that the cut made incomplete (rule 13), the language rules of [`EXTRACTION.md`](../implementation/EXTRACTION.md) Section 2.2.
5. The agent client gets the template text from the server in the claimed task. The client does not hold template texts. The server validates the body as now (`_validate_asciidoc_detail`).

### 5.3 ED3: Creation of editions and the source identifier

1. **At ingest.** The ingest request selects one or more templates. The default is `detailed`. This follows the rule "one selection for each request" of the extraction mode (AED4-rev).
2. **Later.** The document menu has "에디션 추가" with the templates that the document does not have. The run uses the stored raw text (lossless reproduction, glossary 1.1). It fetches nothing. It costs one model call.
3. **Limits.** The number of edition runs of an account uses the tier features as other model work does. The design gives no number.
4. **Source identifier.** A document gets a source identifier when its source has one of these forms, in this order:

| Order | Form | Source identifier |
| :--- | :--- | :--- |
| 1 | A DOI in the URL or in the PDF metadata that the extraction confirmed | `https://doi.org/{doi in lower case}` |
| 2 | An arXiv address | `https://arxiv.org/abs/{id without version}` (as `canonicalize_url` does now) |
| 3 | A web URL | the result of `canonicalize_url` |
| 4 | A file without a URL | none in the first version (O4) |

5. The key on the wire is `claire:sourceKey`: SHA-256 of the source identifier in lowercase hexadecimal. The normalization rules are part of the vocabulary contract with a version (`claire:sourceIdVersion`), because two servers must get the same key from the same source. A change of the rules is a new version, and a server sends the keys of all versions that it knows.
6. A document without a source identifier has editions, but no edition set across accounts.

### 5.4 ED4: Edition set and exchange between Claire servers

1. **Members.** An edition set has: the editions of the document that the reader opened; the published editions of other documents on this server with the same source key; the published editions on other Claire servers with the same source key. Unpublished editions of other accounts are never in a set. Reason: publication is the consent of the owner to show the edition to others (RD10 rule 2).
2. **Pull first.** The publication of a document sends the activities of today (`Create`, `Update`, `Delete` of the document `Article`). A new edition, a changed edition, and a deleted edition send no new activity to followers. A Claire server reads editions when a reader needs them. Reasons: Mastodon followers get no noise; a server that follows nobody of the origin can still find editions; there is less delivery work.
3. **Discovery.** A Claire server finds remote editions in two ways:
   1. From a received document `Article` with `claire:editions` (the follow of an account).
   2. From the source index of a known Claire server: `GET /ap/sources/{sourceKey}` gives an `OrderedCollection` of the published edition objects of that server for the key. The sidecar answers only a request that a server actor of another server signed, with the controls of KS1 (signature, blocked domain, same 404 for each refusal). The servers to ask are the known servers of KF1 and the origins of editions that the server already has. There is no relay and no server-level partnership ([`RETIRED_PARTNERSHIP_IDEAS.md`](../ideas/RETIRED_PARTNERSHIP_IDEAS.md)).
4. **Cache.** The server keeps the metadata and the body of a remote edition as a cache, with a lifetime (proposal: 24 hours) and revalidation with `ETag`. A 410 or a `Tombstone` deletes the cached copy at once. A `Delete` of the document from the origin deletes the cached editions of that document.
5. **Privacy.** A source index request tells the other server that some reader of the asking server opened a document with that key. It does not tell which account. The server actor signs it, not the account.
6. **Load.** The server asks each known server at most one time for each key in the lifetime, and keeps a negative answer for the same lifetime. The rate limits of KF1 apply.

### 5.5 ED5: Federation shape (answer to Q3)

**Rule: the standard Activity Streams fields of each Claire object carry the summary and the link only. A body is only behind the Claire extension property `claire:body`.**

Reasons:

1. The delivered `Article` stays as RD21 says. Mastodon shows `content` (R1). Software that keeps the full JSON of an object (for example, software that stores the object document) gets no body, because the body is a link and not an embedded value.
2. One object has one representation for all readers. A response that changes with the signature of the reader breaks caches and the same-origin rule, and the next fetch can give a different object.
3. Claire servers recognize the extension from the object itself (R4).

**Document `Article` (delivered to followers).** All fields of today stay. New fields, from vocabulary version `v2`:

| Field | Value |
| :--- | :--- |
| `claire:sourceKey`, `claire:sourceIdVersion` | Section 5.3. Absent for a document without a source identifier. |
| `claire:editions` | IRI of an `OrderedCollection` of the published edition objects of the document. |
| `claire:primaryEdition` | IRI of the primary edition object. |
| `interactionPolicy` | `{"canQuote": {"automaticApproval": "https://www.w3.org/ns/activitystreams#Public"}}` (Section 5.7). |

**Edition object.** A separate object in `federation_objects` (kind `edition`) at `{origin}/ap/objects/{public_id}`. Reason: the reactions, the quotes, and the tombstones already work for this path (facts 5, 6).

```json
{
  "@context": ["https://www.w3.org/ns/activitystreams", "https://w3id.org/claire-bible/ns/v2"],
  "id": "https://a.example/ap/objects/1043",
  "type": ["Article", "claire:Edition"],
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
  "likes": {"id": "https://a.example/ap/objects/1043/likes", "type": "Collection", "totalItems": 12},
  "interactionPolicy": {"canQuote": {"automaticApproval": "https://www.w3.org/ns/activitystreams#Public"}}
}
```

- The type array has the base type first, as the vocabulary rule says, so other software can show the object as an `Article`.
- No `Create` of an edition goes to followers (Section 5.4 rule 2). A fetch by URL from other software gives the summary and the link only.
- `claire:contentHash` is the hash of the AsciiDoc body. A reader compares it before it gets the body again.

**Body representation.** `GET {edition ID}/body`. The sidecar answers only a request that a server actor of another server signed (KS1 controls). The answer is on the origin of the edition (R11):

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

- `content` is the compiled body, cleaned with `nh3` on the origin. The receiver cleans it again and shows it only in the sandbox frame (fact 11).
- `source` is the Activity Streams property for the original format (also FEP-b2b8).
- The body is not secret: the Silvercord page of a published document shows it. The signed access makes the body a channel between Claire servers, with blocks, quotas, and logs. It does not make the body private.

**Vocabulary.** New terms go to `https://w3id.org/claire-bible/ns/v2`: `claire:Edition`, `claire:EditionBody`, `claire:edition`, `claire:editionOf`, `claire:editions`, `claire:primaryEdition`, `claire:template` (closed values `detailed`, `concise`, `no_math`), `claire:templateVersion`, `claire:sourceKey`, `claire:sourceIdVersion`, `claire:body`. `v1` does not change. The contract of the internal federation API goes from 1.17 to the next version.

### 5.6 ED6: +1

1. +1 is a `Like` from the account actor of the reader to the author of an edition object. `to` is the author. The `Like` is not public.
2. An account gives at most one +1 to one edition. `Undo(Like)` takes it back.
3. The author cannot give +1 to an own edition. The origin ignores a `Like` from the author.
4. The origin counts. The edition object has `likes` with `totalItems`. The reader server shows the count of its last fetch and its own state at once.
5. A +1 inside one server makes the same row and sends nothing.
6. A favourite (좋아요) of the document `Article` from Mastodon is a `Like` of the document object, not of an edition. It stays in the counter of the document (fact 5). The two counters stay separate **[decision needed: O5]**.
7. Anonymous visitors see the counts. The +1 control needs an account that is a federation actor (every active account with a USERNAME, GD5 and GD8).
8. Order in the picker: the opened edition first, then the others by +1 count (high first), then by `updated` (new first).
9. Domain blocks and the rate limit of interactions apply to inbound `Like`.

### 5.7 ED7: Quotes always permitted, and citation (answer to Q6)

Quotes (FEP-044f):

1. A published document and each of its editions advertise `canQuote` with automatic approval for the public collection.
2. Each `QuoteRequest` for a document or an edition gets `Accept` with a `QuoteAuthorization` stamp at once. Reason: FEP-044f makes a quote valid only with a stamp (R3).
3. The owner cannot refuse or revoke a quote of a document or an edition. The policy dialog of a published document, the field `quote_policy` of `PATCH /api/federation/objects/{object_id}`, and the policy values for documents go away. A retract of the document deletes the object; then the stamp path gives 404 or 410, which is the result of the retract, not a refusal.
4. Migration: `federation_objects.quote_policy` becomes `public` for all rows. Each `pending` and `refused` request for a document becomes `approved` and sends its stamp. Each `revoked` row stays revoked (the remote side already has the `Delete`).
5. Notes keep the four policies. The owner statement is about documents **[decision needed: O3]**.
6. The rate limit of quote requests stays (spam is not a quote).

Citation (출처 표기) of the Silvercord view:

1. `<link rel="cite-as" href="{permalink of the item}">` (RFC 8574). An edition page cites its edition address. The page of the primary edition cites the document address.
2. `citation_title`, `citation_author` (the account), `citation_publication_date`, `citation_public_url`, and `citation_language` meta elements.
3. JSON-LD of schema.org: `Article` with `isBasedOn` (the source URL), `exampleOfWork` (the source identifier as `CreativeWork`), `version` (the template identifier and version), `author`, `datePublished`, `dateModified`.
4. The action "출처 표기 복사" gives a plain text reference with the title, the account, the server, the date, the template, and the permalink.
5. `<meta name="fediverse:creator" content="@{username}@{host}">`, and `attributionDomains` with the host of the server in the actor document, so that Mastodon shows the byline in link previews (R10).

### 5.8 ED8: Silvercord paths (answer to Q5)

Decision proposal: **the path word stays fixed. No new path word. Extension is inside the path.**

| Address | Content |
| :--- | :--- |
| `/silvercord/{token}` | The document. It shows the primary edition and the edition picker. Unchanged as a link. |
| `/silvercord/{token}/editions/{template}` | One edition of this document. New. `{template}` is an identifier of Section 5.2. |
| `/silvercord/{token}/editions` | JSON of the edition set for the picker (local editions with their paths, remote editions with their origin addresses and local render tokens). New. |
| `/silvercord/{token}/workspace`, `/silvercord/{token}/graph` | Unchanged. |

Rules:

1. **Rule for later kinds.** A new kind that is an ActivityPub object and is an item of its own gets a token on `/silvercord/{token}` (owner rule 2026-11-23). A part or a view of an item is a sub-path of the token of that item. An edition is a part of its document (the glossary already makes the body a part of the document), thus it is a sub-path.
2. **Inheritance.** The sub-path takes the alias, gone, hidden, and link access rules of the document token. An alias gives 308 to `/silvercord/{target}/editions/{template}`. A template that the document does not have gives 404.
3. **Canonical link.** The page of the primary edition at its sub-path has `rel="canonical"` to `/silvercord/{token}`, because the content is the same. Each other edition page is canonical for itself.
4. **ActivityPub.** A request that accepts only ActivityPub gets 303 from the edition page to its object ID, as the document page does now.
5. **Remote editions.** A remote edition has no local token, because a token names an item of this server. The local document page shows it in the sandbox frame with the render token of the federation client. The page keeps the selection in its query (`?edition={short key}`) so that a reload shows the same edition. The citation and the "open on the origin" link use the address on the origin.
6. Reasons against one token for each edition: the registry would need a new kind and new alias and gone rules that copy those of the document; a merge of two documents (near-duplicate merge) would need more alias rows; and the edition already has a stable identity in (document, template).

### 5.9 ED9: 게시 to 발행 in all layers (answer to Q4)

Decision proposal: replace the term in all layers for the publication of documents. Keep 게시 for notes. Remove the retired text that uses 발행 in another sense. After the change each word has one concept.

| Layer | Change |
| :--- | :--- |
| Catalog values (ko) | 게시 → 발행 in `nav.syndication`, `home.flow.label`, `home.flow.publish`, `home.flow.published`, `synd.stage.published`, `synd.stage.unpublished` (미게시 → 미발행), `synd.client.action.publish`, `synd.kb.menu.publish`, `synd.kb.filter.label`, `synd.kb.filter.unpublished`, `synd.kb.filter.published`, `synd.palette.stage_publish`, `console.synd.reader.app_tab_kb`. |
| Catalog values (ko), notes | 발행 → 게시 in `home.note_dlg.publish` (노트 게시), `home.note_dlg.publishing`, `home.note_dlg.published`, `home.note_dlg.error`. `synd.compose.submit` stays 게시. |
| Catalog keys | `home.note_dlg.publish*` → `home.note_dlg.post*`, so that the key names the concept. The document keys already use `publish`. |
| Catalog values (en) | Documents stay "Publish" and "publication". Notes: `synd.compose.submit` and `home.note_dlg.*` → "Post" (O7). |
| Retired texts | Remove the sections `/settings/connections` and `/settings/outbound`, their partials, and the keys `console.synd.connections.*` and `console.synd.outbound.*` (fact 10). The sections describe a model that the code does not have, and they use 발행 in that retired sense. |
| Code identifiers | No change for documents: routes, tables, the stage key `publish`, and functions already use `publish`. |
| Documents | RD10 table and rule 6 of [`FEDERATION_REMOTE_READING_DESIGN.md`](FEDERATION_REMOTE_READING_DESIGN.md) (Publication (게시) → Publication (발행)); [`GLOSSARY.md`](../implementation/GLOSSARY.md) Section 1.11 and the terms of Section 4; [`FEDERATION.md`](../implementation/FEDERATION.md) where it names the stage. |
| Tests | The i18n tests and the E2E specs that read these texts. |

No HTTP route and no JSON field changes its name.

### 5.10 ED10: Reader interface (modern web guidance)

The design uses the guides of the `modern-web-guidance` tool (version `2026_10_12-dacb5911`): `rich-media-picker`, `cross-document-transitions`, `scoped-component-transitions`, `improve-next-page-load-performance`, `defer-rendering-heavy-content`.

1. **Edition picker.** A native `<select>` with a visible `<label>` ("에디션"), with `appearance: base-select` and rich `<option>` content: template name, account, server, +1 count, date. The text of each option must make sense alone, because Firefox shows a plain list ("간결. @bob@b.example. +1 12."). Use visually hidden full stops for that. Put no button or link inside an option. The selected option must be visible in the open list. Support: Chrome and Edge 135, Safari 27; Firefox shows the native list.
2. **+1 control.** A `<button aria-pressed="true|false">` next to the edition heading, outside the picker. The visible text has the count. An anonymous visitor sees the count and a sign-in link, not a dead button.
3. **Switching local editions.** A local edition is a page (Section 5.8), so a switch is a navigation. Use `@view-transition { navigation: auto; }` inside `@media (prefers-reduced-motion: no-preference)`. Chrome, Edge, and Safari 18.2 animate; Firefox navigates at once.
4. **Switching to a remote edition.** The frame changes inside the page. Use `element.startViewTransition()` on the reader container (Chrome and Edge 147). Other browsers change the frame at once, without `document.startViewTransition()`, so that the fixed bottom bar and the popovers stay on top.
5. **Prefetch.** Speculation rules with a document rule for the edition links of the picker, `prefetch`, eagerness `moderate`. No `prerender` (signed-in pages have state). Exclude `/silvercord/*/workspace` and each state change. Chromium only; other browsers ignore the rules. Whether Chrome uses a prefetched answer with `Cache-Control: private, no-store` is **[unverified]**; measure it before the work counts on it.
6. **Long bodies.** `content-visibility: auto` with `contain-intrinsic-size: auto 800px` on body sections below the first screen, never on the first screen. Test keyboard navigation through these sections.
7. **Mathematics.** `no_math` is an editorial choice of the template, not a display fallback. The display of formulas in the other editions does not change.
8. Each new text uses catalog keys in English and Korean from the start.

---

## 6. Data (proposal)

| Item | Proposal |
| :--- | :--- |
| `document_editions` (`claire.db`) | `doc_id`, `kb_id`, `template`, `template_version`, `detail`, `detail_format`, `detail_html`, `created_at`, `updated_at`, `model_lineage` (JSON), unique (`doc_id`, `template`). |
| Primary edition body | First phase: `documents.detail*` stays the body of the primary edition, so that the 38 readers (fact 2) do not change; `document_editions` has the other editions and a row without a body for the primary edition (template and version). Changing the primary edition (O6) moves the bodies in one transaction. A later phase can move all bodies into `document_editions` after a count of the readers. |
| `documents.source_key`, `documents.source_id_version` | Section 5.3. Index on `source_key`. |
| `federation_objects` | New column `kind` (`document`, `edition`) and `parent_object_id` for an edition. The `CHECK` of the outbox kinds needs no change: editions send no task (Section 5.4 rule 2). |
| Remote editions (`client.db`) | Cache rows: edition IRI, origin, source key, template, template version, account IRI, name, `likes.totalItems`, content hash, body, `ETag`, `fetched_at`, `expires_at`. |
| +1 | `federation_object_reactions` for inbound `Like` of own editions (existing). Outbound +1 of the account to remote editions in the outbound interaction tables of `client.db` (as favourites, Section 25 of `FEDERATION.md`). |
| Migrations | They add only what is absent, keep unknown data, and complete at the start of the process. No operator step. |

---

## 7. Rejected alternatives

| Alternative | Reason |
| :--- | :--- |
| A different JSON for each reader (Claire servers get the body by signature) | One object ID must have one representation; caches and the same-origin rule break; a second fetch can give another object (Section 5.5). |
| The body in `content` (FEP-b2b8) | The owner keeps the body on Claire (RD21); Mastodon would show the full body as a status. |
| The document summary in `summary` | Mastodon shows `summary` as a content warning (R1). |
| `context` for the edition set | Backfill software reads `context` as a conversation (R6). |
| A server actor mesh that pushes editions to all Claire servers | It is a relay; relays are ideas, not design ([`RETIRED_PARTNERSHIP_IDEAS.md`](../ideas/RETIRED_PARTNERSHIP_IDEAS.md)). |
| An edition as a second ingest of the same source | Two documents of one source in one knowledge base double the sources of each entity. |
| Templates that an account writes | Not comparable across servers; a prompt injection path; no fixed meaning on the wire (Section 5.2). |
| A capability flag (FEP-844e) or the NodeInfo software name | FEP-844e forbids it when the object properties show the capability (R4). |
| FEP-bad1 history for editions | History is a sequence of changes in time; editions are parallel. |
| A token for each edition | Section 5.8 rule 6. |
| Counting Mastodon favourites as +1 | A favourite is for the post (the summary), not for an edition body (Section 5.6 rule 6). |

---

## 8. Security and privacy notes

1. A remote body is untrusted HTML. Clean it on receipt and show it only in the sandbox frame of the usercontent host.
2. The body path and the source index answer only signed server actors, with KS1 controls and quotas.
3. A source index request shows only the key and the asking server (Section 5.4 rule 5).
4. A retract, a private knowledge base, a hidden document, a purged document, a deleted knowledge base, and an account purge make the edition objects 410 or 404, as for the document object now (FP3-4 privacy triggers). Cached copies on other Claire servers go away at the next revalidation or at the `Delete` of the document.

---

## 9. Phases

| Phase | Content | Depends on | Size |
| :--- | :--- | :--- | :--- |
| E0 | Terms (Section 5.9): catalog values and keys, removal of the retired sections, glossary, design documents. | — | S |
| E1 | Quotes always permitted for documents (Section 5.7, quotes 1 to 4) and the migration. | — | S |
| E2 | Citation metadata, `fediverse:creator`, `attributionDomains` (Section 5.7, citation). | — | S |
| E3 | Templates and local editions: template set, ingest selection, "에디션 추가", `document_editions`, edition sub-paths and the picker of local editions (Sections 5.1 to 5.3, 5.8, 5.10). | Template texts (O2) for `concise` and `no_math` | M |
| E4 | Vocabulary `v2`, edition objects, `claire:editions`, body path, +1 (Sections 5.5, 5.6). | E3 | M |
| E5 | Source index, remote edition cache, remote editions in the picker and in the sandbox frame (Section 5.4). | E4 | M |

Tests (global rules: unit tests only for module interfaces, input and output, and calls to external libraries; at least one E2E test for each new feature):

- E2E: an edition switch in the picker and +1 on a Silvercord page (Playwright through `scripts/e2e.sh`); the vocabulary fixtures of `v2`; a DrFed scenario for the edition object, the body path with a signed server actor, and an inbound `Like`; two development servers for the source index (acceptance path as KA1 to KA5).
- Unit: the source key normalization (input and output), the body representation builder and validator (interface with the sidecar), the internal API contract fixtures.

---

## 10. Open measurements

1. Mastodon 4.6: show of an `Article` with `content` of the RD21 form (length limit, link card), and of the edition object type array `["Article", "claire:Edition"]`.
2. Mastodon 4.5 and 4.6: a quote of a Claire document with the automatic stamp.
3. Chrome: use of a prefetched answer with `Cache-Control: private, no-store` (Section 5.10 rule 5).

---

## 11. Owner decisions needed

| # | Question | Options | Recommendation |
| :--- | :--- | :--- | :--- |
| O1 | Number of templates, and the terms 에디션 and 에디션 템플릿 | A: three in total (`detailed`, `concise`, `no_math`). B: the current one and three new ones. | A, and the terms of Section 4 |
| O2 | Texts of `concise` and `no_math` | The owner writes them, or the agent drafts them for review. | Agent draft, owner review |
| O3 | Quote policy of notes | A: notes keep the four policies. B: notes are also always quotable. | A (the request names documents) |
| O4 | Files without a URL in edition sets | A: not in the first version. B: SHA-256 of the original file bytes as the source identifier. | A |
| O5 | Mastodon favourites of the document and +1 of editions | A: separate counters. B: one counter. | A |
| O6 | Change of the primary edition | A: the owner of the document can change it. B: it is always the first edition. | B for E3, A later if needed |
| O7 | English word for a note | "Post" instead of "Publish" | Yes |

---

## 12. Modern Web Guidance

Section 5.10 lists the guides and the rules that came from them. The search for MathML display gave no guide, so this design does not use a guide for formulas.

## 13. Sources

- Mastodon documentation, ActivityPub: <https://docs.joinmastodon.org/spec/activitypub/>
- Mastodon 4.6 for developers (2026-06-12): <https://blog.joinmastodon.org/2026/06/mastodon-4-6-for-devs/>
- Mastodon 4.6.0 release: <https://github.com/mastodon/mastodon/releases/tag/v4.6.0>
- Mastodon quote posts (client documentation): <https://docs.joinmastodon.org/client/quotes/>
- FEP-044f Quote posts: <https://codeberg.org/fediverse/fep/src/branch/main/fep/044f/fep-044f.md>
- FEP-b2b8 Long-form Text: <https://codeberg.org/fediverse/fep/src/branch/main/fep/b2b8/fep-b2b8.md>
- FEP-844e Capability discovery: <https://codeberg.org/fediverse/fep/src/branch/main/fep/844e/fep-844e.md>
- FEP-7888, FEP-f228, FEP-2931 (context): <https://socialhub.activitypub.rocks/t/fep-2931-representing-context-with-a-collection/5154>, <https://codeberg.org/fediverse/fep/pulls/499>
- FEP-bad1 Object history collection: <https://socialhub.activitypub.rocks/t/fep-bad1-object-history-collection/3352>
- FEP-dc88 Formatting mathematics: <https://socialhub.activitypub.rocks/t/fep-dc88-formatting-mathematics/3564>, <https://codeberg.org/fediverse/fep/pulls/181>
- FEP-c118 Content licensing: <https://socialhub.activitypub.rocks/t/fep-c118-content-licensing-support/2903>
- FEP-fe34 Origin-based security model: <https://socialhub.activitypub.rocks/t/fep-fe34-origin-based-security-model/4292>, Fedify 1.9.0: <https://socialhub.activitypub.rocks/t/fedify-1-9-0-released-security-enhancements-improved-dx-and-expanded-framework-support/8132>
- Social Web Foundation, long-form text (2025-05-01): <https://socialwebfoundation.org/2025/05/01/steps-forward-in-long-form-text/>
- W3C Social Web Working Group charter: <https://www.w3.org/2026/01/social-web-wg-charter.html>; adoption of the CG drafts (May 2026): <https://lists.w3.org/Archives/Public/public-socialweb/2026May/0012.html>; announcement: <https://socialwebfoundation.org/2026/01/15/new-social-web-working-group-at-w3c/>
- RFC 8574 cite-as: <https://www.rfc-editor.org/rfc/rfc8574.html>
- Mastodon author attribution (`fediverse:creator`): <https://blog.joinmastodon.org/2024/07/highlighting-journalism-on-mastodon/>
