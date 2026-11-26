# Renditions of Published Documents, +1 Between Claire Servers, Opinions from Other Servers, and ActivityPub Publication (`DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN`)

- **Document Identifier**: `DESIGN-DOC-RENDITIONS-20261125`
- **Status**: Proposal, revision 11. Phases done: E0, E1, E2, E3 (E3a, E3b, E3c), E4 (E4a, E4b), E5 (E5a, E5b, E5c), E6, E7 (E7a to E7e), E8, E9 (E9a, E9b), E10. The decisions of the agent and the changes from this design, with their reasons, are in [`RENDITIONS_HANDOFF.md`](../implementation/RENDITIONS_HANDOFF.md). Implemented before the phases: the texts of the three rendition templates (`src/claire/extract/rendition_templates.py`, test `tests/test_rendition_templates.py`; each body run uses them since E3a) and the term 초록 for the document summary in the Korean prompts and texts (commit `0dc44a6d`, [`GLOSSARY.md`](../implementation/GLOSSARY.md) Section 1.3). Owner decisions O1 to O7 and O9 to O22 are given (Section 11). The glossary has the terms of Section 4 since Rev. 10 ([`GLOSSARY.md`](../implementation/GLOSSARY.md) Sections 1.4, 1.13, 1.14), with their state. O8 is on hold for the reputation of a publisher. The Silvercord view has a visual prototype; other screens are rules only (Section 9.1).
- **Date**: 2026-11-25
- **Base commit**: `8e6b0723`
- **Visual prototype**: [`prototypes/document_renditions_prototype.html`](prototypes/document_renditions_prototype.html) (Silvercord view: rendition picker, +1, remote rendition, `의견` tab with likes, mobile; mock data)
- **Revisions**:
  - Rev. 1 (2026-11-25): first proposal (then named "editions", 에디션), questions Q1 to Q6, decisions O1 to O7 asked.
  - Rev. 2 (2026-11-25): owner answers to O1 to O7. Template `no_math` became `academic`. The mark of a rendition was the ActivityPub `Like`, and the delivered document object is the primary rendition. New: opinions from other servers (Section 5.11), with the structure of Mastodon.
  - Rev. 3 (2026-11-25): O8 on hold (two kinds of reputation). O9: the fill of a timeline after a follow went to an idea document. Frontend coverage (Section 9.1).
  - Rev. 4 (2026-11-25): visual prototype of the Silvercord view.
  - Rev. 5 (2026-11-25): owner review of the prototype. No citation copy, no message about the quote policy, the summary section above the rendition bar. Questions O10 (term) and O11 (label).
  - Rev. 6 (2026-11-25): 에디션 → 정리본 (English "rendition") in all layers (O10); label 학술 (O11); 요약 → 초록 in prompts and texts (O12, implemented); +1 is a mark of a rendition that only Claire servers exchange, separate from the `Like` (Section 5.12); the fill of a timeline after a follow is in this design (O9 revised, Section 5.13; the idea document is removed).
  - Rev. 7 (2026-11-25): the template "상세" became "기본" with the identifier `standard` (O14: the body itself is 상세). The rendition of the first ingest is now 대표 정리본, so that "기본" names only the template.
  - Rev. 8 (2026-11-25): the picker does not order by +1; it orders by template (기본, 간결, 학술) (O16). The limit of one server in the +1 score is the estimated number of accounts that one Claire server can serve now: 1,000 (O15, Section 5.12 rule 9). 대표 정리본 accepted (O14). Review of the template model for the owner (Section 5.2 rules 8 and 9).
  - Rev. 9 (2026-11-25): the 대표 정리본 is always the rendition of the template 기본 (owner: "대표 정리본은 간결 정리본일 수 없습니다"); each ingest makes it. O17: 학술 stays an exact copy of the current prompt, and the template 기본 changes: no tables of the source, representative formulas only. Open: O18 (tables in 간결).
  - Rev. 10 (2026-11-25): owner confirmed the explanation of Rev. 9 and asked for the glossary revision: GLOSSARY 1.4 (상세, tables), 1.13 (정리본 terms), 1.14 (발행, 게시). O18: `concise` uses no tables of the source (implemented in the template module). Open: O20 (formulas in 간결).
  - Rev. 11 (2026-11-25): O20: `concise` keeps only the representative formulas. Owner question: do the templates take the common principles and apply only their own traits? Answer: no, each template was a full copy; the template module now builds each template from the core rules, the fixed rules, the traits, and its changes (Section 5.2, "Structure").
- **Related**: [`FEDERATION_REMOTE_READING_DESIGN.md`](FEDERATION_REMOTE_READING_DESIGN.md) (RD10, RD21), [`KNOWLEDGE_FEDERATION_DESIGN.md`](KNOWLEDGE_FEDERATION_DESIGN.md) (vocabulary, known servers, inbox journal), [`PERMALINK_UNIFICATION_DESIGN.md`](PERMALINK_UNIFICATION_DESIGN.md), [`SILVERCORD_RENDERING_DESIGN.md`](SILVERCORD_RENDERING_DESIGN.md) (the `의견` tab, SR5, SD8), [`SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md`](SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md) (reputation, CT18), [`FEDERATION.md`](../implementation/FEDERATION.md), [`KNOWLEDGE_FEDERATION.md`](../implementation/KNOWLEDGE_FEDERATION.md), [`PERMALINKS.md`](../implementation/PERMALINKS.md), [`EXTRACTION.md`](../implementation/EXTRACTION.md), [`GLOSSARY.md`](../implementation/GLOSSARY.md)

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

Owner review of the prototype (revision 4, 2026-11-25):

> * "문서와 에디션의 인용은 항상 허용되며 승인 증표가 자동으로 발급됩니다." 메시지 불필요
> * 에디션은 의미가 너무 큰 듯한데 다른 추천 용어 있나요?
> * '출처 표기' 클립보드 복사 기능 없앱니다. 이미 링크 복사 기능이 있으니까요.
> * '학문 특화'도 한 어절로 더 저절하거나 추천할만한 것을 고려해줘요.
> * '개요'가 첫 등장하는데, '요약'의 혼용인가요? 또는 추가하려는 공통 요소인가요?

Owner answers to revision 5 (2026-11-25):

> * docs/ideas/TIMELINE_BACKFILL_IDEAS.md 이번에 같이 고려합시다.
> * 학술 수용
> * 정리본 수용
> * 문서의 기존 프롬프트 및 UI 표현 '요약'을 '초록'으로 변경한다.
> * 클레어바이블 서버 간에만 주고 받는 정리본 단위의 '+1' 설계

Owner message during revision 6 (2026-11-25):

> 정리본 '상세'는 상세의 상세가 되니 '기본'으로 변경하여 혼란을 줄이죠.

Owner answers to revision 7 (2026-11-25):

> * +1 점수 상한: 서버 이용자 수에 다른 클레어바이블 서버의 이용자 수가 상한이므로 일단 클레어바이블 서버가 현재 수용 가능한 성능 단위 유저 수를 임의로 추정하여 정해주세요.
> * 선택기 정렬: 점수 순 정렬하지 않습니다. 기본/간결/학술 순 정렬합니다. 제가 템플릿을 제대로 인식했는지 검토 바랍니다.
> * 용어: 수용합니다.

Owner answers to revision 8 (2026-11-25):

> 1. 수용
> 2. 한 서버에서 작성 가능한 템플릿은 최대3개 겠지만, 같은 템플릿으로 다른 모델을 작성한 이용자가 생길 수 있지요. 또한 다른 서버에서도 문서를 작성할 것이고요. 저에게 제공한 시각 프로토타입을 근거로 하여 설명하였습니다. '대표 정리본'은 초록과 실버코드 링크라고 저에게 말한 것 같은데요? 그게 아니라면 대표 정리본은 간결 정리본일 수 없습니다.
> 3. 그렇군요.
>
> O17: 학술 템플릿은 그대로 두고, 기본 템플릿을 고칩니다. '원문 테이블 미사용', '수식은 대표 공식만 사용'.

Owner answers to revision 9 (2026-11-25):

> * 예. 설명이 맞습니다. 용어집 개편하세요.
> * O18: 권장안

Owner answers to revision 10 (2026-11-25):

> O20 수용합니다.
>
> 만들 템플릿이 기본 원칙을 공통으로 수용하고, 템플릿의으로서의 각특성을 적용하도록 구조가 잘 되어 있나요?

Questions and the answers of this design:

| # | Question | Short answer | Section |
| :--- | :--- | :--- | :--- |
| Q1 | Exchange renditions between Claire servers, choose a rendition of another user, give +1 | Yes. A rendition (정리본) is one readable body of a document, made with one fixed template. Renditions of the same source make a rendition set across servers. +1 is a Claire activity for one rendition that only Claire servers exchange; the ActivityPub `Like` stays the like of a post. | 5.1 to 5.4, 5.6, 5.12 |
| Q2 | Three fixed templates | `standard` (기본, the current prompt), `concise` (간결, written from a copy), `academic` (학술, an exact copy of the current prompt for the owner to change). The template changes the body only. | 5.2 |
| Q3 | Only the 초록 to other ActivityPub servers? | Yes. The standard fields of each Claire object carry the 초록 and the link only. A body is only behind a Claire extension link that a signed server actor of a Claire server reads. | 5.5 |
| Q4 | Replace 게시 with 발행, also inside? | Yes, for the publication of documents in all layers. The code identifiers already use `publish`. A note stays 게시 (a post), and its English word becomes "Post" (O7). | 5.9 |
| Q5 | Silvercord path: fixed, more paths, or extension inside the path? | Keep the one path word `/silvercord/{token}`. Add no path word. A rendition and the opinions are sub-paths: `/silvercord/{token}/renditions/{template}`, `/silvercord/{token}/opinions`. | 5.8 |
| Q6 | No refusal of quotes | Documents and renditions are always quotable, with an automatic approval stamp for each request. Notes keep their policies (O3). | 5.7 |
| Q7 | Get the opinions of other servers (as FediFetcher), with the structure of Mastodon first | Yes. Claire publishes `replies`, `likes`, and `shares` in the form of Mastodon, and crawls the `replies` collections of other servers as Mastodon 4.5 and later do, with the same limits. | 5.11 |
| Q8 | Recent posts of a newly followed actor (from the idea document) | Yes, in two steps: the `featured` collection, as Mastodon reads it, and the first page of the `outbox`, from FediFetcher. The posts go to the view of that actor, not to the top of the home stream. | 5.13 |

---

## 2. Facts at the base commit

1. **[code] Published object.** The `Article` of a published document has `name` (title), `content` (the 초록 paragraph and a paragraph with the permalink), `url` (the permalink), `published`, `updated`, `to`, `cc`. `summary` is not set. The body is never in the object (RD21; [`FEDERATION.md`](../implementation/FEDERATION.md) Section 4.4, Section 5.8). The object has no `replies`, `likes`, `shares`, or `context`.
2. **[code] One body for each document.** `ensure_document_detail` (`src/claire/ingest/pipeline.py`) makes the readable body with a separate model call. Its docstring says that it is independent of the graph: it fills `documents.detail` only and does not change entities or relations. The prompt is `render_detail_prompt_adoc` (`src/claire/extract/prompts.py`). 38 Python files under `src/claire` read `detail` or `detail_html`.
3. **[code] Template placeholder.** `build_extraction_prompt_bundle` has a parameter `template`; no caller sets it, and its value is always `DEFAULT_EXTRACTION_TEMPLATE` (`"default"`). The agent client spec and the provenance of a client extraction write it as `prompt_template` (`src/claire/ingest/client_extraction.py`).
4. **[code] 초록.** The 1 to 3 sentences of a document (code name `summary`) come from the structured extraction (`PROMPT_VERSION = "extract-v9"`), not from the body prompt. Search, hover cards, and the published object use it. Since commit `0dc44a6d` the Korean prompts and texts call it 초록 (O12).
5. **[code] Permalink.** `/silvercord/{token}` is only for kinds that can be ActivityPub objects (`ACTIVITYPUB_KINDS`: document, note; owner rule 2026-11-23). The sub-paths `/silvercord/{token}/workspace` and `/silvercord/{token}/graph` are views of the same item. An alias token gives 308 also for the sub-paths ([`PERMALINKS.md`](../implementation/PERMALINKS.md) Sections 2, 3.1, 3.2).
6. **[code] Reactions.** A `Like` or an `Announce` of `{origin}/ap/objects/{public_id}` is stored one time for each actor and kind (`federation_object_reactions`). The counters are in `GET /api/federation/objects` ([`FEDERATION.md`](../implementation/FEDERATION.md) Section 22.4). The account can favourite (좋아요, `synd.compose.action.favourite`) and boost remote posts (Section 25.4).
7. **[code] Replies to own objects.** A delivered public reply to a published document becomes a timeline item of its owner with `mention` = 1 (Section 22.3). Nothing else stores it, and no page shows it to other readers.
8. **[code] Thread fetch.** For a timeline item that the owner opens, the sidecar reads the FEP-7888 `context` collection (FEP-f228) with `@fedify/backfill` (`~2.4.6`, strategy `context-auto`), and the reply tree when that gives nothing. Limits: `context_max_items`, `3 × max_items + 20` requests, 120 seconds. Each object comes from its own origin. The account key signs the fetches (Section 24).
9. **[code] Quotes.** FEP-044f is implemented. A published document has the policy `manual` by default; the policies are `public`, `followers`, `manual`, `nobody`; the owner can refuse and revoke. Claire does not fetch the quoting object (Section 26).
10. **[code] Claire vocabulary.** The context `https://w3id.org/claire-bible/ns/v1` has `claire:Extraction`, `claire:contentHash`, `claire:root`, and other terms. The context of a version must not change after a server serves it; a new term goes to a new version ([`KNOWLEDGE_FEDERATION.md`](../implementation/KNOWLEDGE_FEDERATION.md) Section 2). A collection of shared knowledge answers only a request that a server actor of another server signed (KS1, Section 10.3 of the same document). The inbox journal records each Claire activity with its state and permits a replay (Section 10.3).
11. **[code] Source identity.** `documents.canonical_url` comes from `canonicalize_url` (`src/claire/ingest/normalize.py`: host to lower case, tracking parameters removed, arXiv versions joined to `/abs/{id}`). `documents.content_hash` is SHA-256 of the normalized text.
12. **[code] Terms.** `ko.json` has 44 values with 게시 and 55 values with 발행. The stage name is 게시 (`nav.syndication`, `home.flow.publish`, `synd.stage.*`, `synd.kb.filter.*`). The publish dialog of a document uses 발행 (`synd.kb.publish.*`). The note dialog of the account home uses 발행 (`home.note_dlg.publish*`), and the compose button of `/syndication` uses 게시 (`synd.compose.submit`). The glossary defines 게시물 (Post) as a note or a document in the timeline (Section 1.11). The English texts use "publication" and "Publish" for documents and for notes. The code identifiers of document publication are `publish`.
13. **[code] Retired text that is still visible.** The console sections `/settings/connections` and `/settings/outbound` (`SYNDICATION_SECTION_SLUGS` in `src/claire/api/accounts_routes/console_syndication_panels.py`) show the texts `console.synd.connections.*` and `console.synd.outbound.*`. These texts describe the retired model of connected external accounts ("Claire 는 ActivityPub 서버를 … 직접 구현하지 않습니다") and use 발행 in that retired sense ("적재 발행", "사용자 간 발행 본문 공유").
14. **[code] Remote content in the page.** The federation client puts no content of a remote server into the page DOM. The body of a remote post stays in the sandbox frame ([`FEDERATION.md`](../implementation/FEDERATION.md) Section 26.6).
15. **[code] Page metadata.** The Silvercord document view has no `rel="cite-as"`, no `citation_*` meta elements, and no `fediverse:creator`. The actor document has no `attributionDomains`. The page shows the section "초록" and then the section "상세" (`share.html`). The `의견` tab is a visual prototype (SR5, SD8).
16. **[code] Reputation.** `src/claire/collaboration/reputation.py` computes a level (`good`, `limited`, `low`) from two signals: the fiction share and the approval rate of contributions. A knowledge base gives at most one third of the decisions. The level controls the exchange only, never reading, ingest, publication, or membership.
17. **[code] Follows and the timeline.** An `Accept` of a `Follow` changes the follow to `accepted` (Section 17). The timeline stores only posts that come after the follow, by delivery (Section 18). The timeline route orders items by their ID, newest first (`max_id` cursor, Section 19.1), so the order is the order of storage.

---

## 3. Research findings (2026-11-25)

Sources are in Section 13. **[code: Mastodon]** marks a fact read in the Mastodon source code (branch `main`, 2026-11-25). A statement from a third party that this design could not confirm has the tag **[unverified]**.

| # | Finding | Effect on this design |
| :--- | :--- | :--- |
| R1 | Mastodon converts an `Article` or a `Page` to a status: it "uses `content` if available, or `name` if not", it appends `url`, and "the `summary` property will be used as the CW text" (Mastodon ActivityPub documentation). | The RD21 shape is correct for Mastodon. Do not put the 초록 in `summary`: Mastodon would hide it behind a content warning. |
| R2 | FEP-b2b8 "Long-form Text" (DRAFT, received 2024-11-07) puts the full text in `content`, a teaser of about 500 characters in `summary`, the original format in `source`, and an optional `preview` (`Note`). | Claire departs from FEP-b2b8 on purpose: the owner keeps the body on Claire (RD21). `summary` conflicts with R1. No `preview` now: Mastodon does not read it. |
| R3 | FEP-044f (DRAFT): the quote policy is advisory; a quote is valid only with a `QuoteAuthorization` stamp from the author. Mastodon writes `interactionPolicy.canQuote.automaticApproval` as a list, with the public collection for a public policy, and shows an object without a policy as not quotable **[code: Mastodon]**. | A Claire document always advertises a policy and always answers a request with a stamp. |
| R4 | FEP-844e "Capability discovery" (DRAFT, received 2025-06-14): "Implementers MUST NOT use it in cases where capabilities can be inferred from properties and types of objects"; it discourages NodeInfo for this. | Claire infers rendition support from the `claire:` properties of an object. No capability flag, no check of the NodeInfo software name. |
| R5 | Mastodon releases: 4.6 (2026-06-17: collections with authorizations, `interactionPolicy.canFeature`, JSON-LD profile in `Accept`, multiple key pairs, FEP-2c59, FEP-3b86); 4.7 (2026-08-20: FEP-8967 `Link` attachments, verification of FEP-8b32 integrity proofs, inbound FEP-521a, no `Accept` or `Reject` of a `QuoteRequest` that it cannot find by ID); latest 4.7.6 and 5.0.0-beta.2 (2026-11-19: schema.org markup on profiles and replies; a bloom filter of the servers that fetched an account, to send fewer deletion notices). | `interactionPolicy` is the common pattern of consent. A Claire `Accept` of a quote must carry the `QuoteRequest` ID exactly (it does). The bloom filter idea applies to rendition bodies (Section 5.4 rule 4). |
| R6 | No FEP defines parallel versions of one text. FEP-bad1 (object history collection) is about edits in time. FEP-7888, FEP-f228, and FEP-2931 use `context` for conversation containers and backfill. | Renditions use Claire terms. Do not use `context` to group renditions. |
| R7 | The W3C Social Web Working Group started on 2026-01-15. In May 2026 it adopted the Community Group drafts of ActivityPub, Activity Streams, and Activity Vocabulary. The revisions are backward-compatible, and extensions stay in the Community Group. The 2018 Recommendation is still the latest published version. | No protocol change blocks this design. |
| R8 | FEP-dc88 (mathematics in MathML Core, DRAFT) has no recent change; the FEP maintainer said that it will be withdrawn. | Claire sends no mathematics in federated text. The 초록 is plain text. |
| R9 | FEP-c118 (content licenses, DRAFT, 2023) has no agreed form. | No license property now. |
| R10 | RFC 8574 (informational) defines `rel="cite-as"`. Mastodon shows an author byline in link previews from `<meta name="fediverse:creator">` when the actor lists the site domain in `attributionDomains`. | Section 5.7, citation. |
| R11 | FEP-fe34 (origin-based security): an object is trusted for the origin of its ID. Fedify 1.9 and later enforce the same-origin rule. | A body, each fetched opinion, and each filled post must come from the origin of its ID. |
| R12 | **Fetch all replies (Mastodon).** 4.4 added it as an experiment; 4.5 turned it on for all servers and removed its settings. A request of the context of a status starts it. It "fetches the tree of replies beneath an expanded post by recursively requesting the replies collections of each of the statuses, and then requesting the status itself". Limits in the code: 1000 replies for one tree, 500 for one status, 500 collection pages; a status is fetched again only after 15 minutes, and not in the first 5 minutes after it was made. Replies from accounts without local followers are fetched again, because no `Delete` reaches the server; a 404 deletes them **[code: Mastodon]**. | Claire uses the same algorithm and the same limits for opinions (Section 5.11). |
| R13 | **The `replies` collection of Mastodon.** A local status has `replies`: a `Collection` whose `first` page has at most 5 self-replies, and whose `next` goes to pages with `only_other_accounts=true` that list the public replies of other accounts. `likes` and `shares` are collections with `totalItems` (the favourite and boost counts) and no items. `context` is the IRI of the conversation **[code: Mastodon]**. | Claire publishes the same three collections (Section 5.11 A). A count without items is also the form of the +1 collection (Section 5.12). |
| R14 | **Async refreshes (Mastodon, experimental).** The context API answers with `Mastodon-Async-Refresh: id="…", retry=<seconds>, result_count=<n>` (4.5). The client polls `GET /api/v1_alpha/async_refreshes/:id` (4.4) until the status is `finished`. | Claire uses the same three values in the JSON of its own opinions route (Section 5.11 B rule 6). |
| R15 | Mastodon backfills only through `replies`; it publishes `context` as an IRI and groups by its older `conversation` value **[unverified]** (summary of a community guide, 2026-10-15). `@fedify/backfill` 2.4.1 did not follow `first` and `next` pages of collections; the issue closed on 2026-10-06 with a fix for the next patch release; the release with the fix is not named **[unverified]**. | The reply tree is the first source; a `context` collection is a second source. Verified in phase E7a: `@fedify/backfill` 2.4.6 follows the `first` and `next` pages of `replies` and of a `context` collection (`sidecar/fedify/tests/context_test.ts`). |
| R16 | **FediFetcher** fetches missing replies on a schedule for posts of the home timeline, posts that a user replied to, bookmarks, favourites, and lists, and fills the profiles of new follows and followers with recent posts. Its author wrote (2025-07) that server-side fetching is "the most sustainable and user-friendly solution" in the long term. | Claire takes the Mastodon structure (fetch on open) for opinions, one scheduled refresh of own objects (Section 5.11 E), and the fill of new follows (Section 5.13). |
| R17 | **Recent posts of an account (Mastodon).** Mastodon does not fill the posts of a newly followed account; the request for it is open since 2016 (issues 34 and 9525). When Mastodon processes a remote account, it reads the first page of the `featured` collection (pinned posts) with the signature of a local follower, and it reads only `totalItems` of the `outbox`. An outbox page of Mastodon has 20 items (`?page=true&max_id=`), and a server in authorized fetch mode needs a signature **[code: Mastodon]**. | Step 1 of Section 5.13 reads `featured` as Mastodon does; step 2 reads one `outbox` page, the idea of FediFetcher, with one fill for each follow. |

---

## 4. Terms (proposal for `GLOSSARY.md`)

| Term | English | Definition | Not used |
| :--- | :--- | :--- | :--- |
| **정리본** | Rendition | One readable body of a document, made with one rendition template (O10). A document has one rendition for each template that it has. The 초록, the entities, and the relations belong to the document, not to a rendition. | "에디션" (Rev. 1 to 5; too large in meaning), "판", "버전", "리비전" (a revision is a change in time; a rendition is a parallel body) |
| **정리본 템플릿** | Rendition Template | A fixed instruction set for the body prompt, with an identifier (`standard` 기본, `concise` 간결, `academic` 학술) and a version. The code defines the set. | "프롬프트" alone, "초점" (`focus` is sealed data, CT28), the value `default` of the placeholder `prompt_template` (fact 3) |
| **대표 정리본** | Primary Rendition | The rendition of the template 기본 (`standard`) of a document. Each ingest makes it, and it does not change (O6, Rev. 9). Its ActivityPub object is the object of the document: other servers see it as the post with the title, the 초록, and the Silvercord link; its body stays on Claire servers. | "기본 정리본" (기본 is the template `standard`, O14) |
| **출처 식별자** | Source Identifier | The normalized identifier of the source of a document (Section 5.3). Documents with the same source identifier have renditions of the same source. | "작품", "원본 ID" |
| **정리본 묶음** | Rendition Set | All published renditions with the same source identifier, on this server and on other Claire servers, and the own renditions of the reader. | — |
| **+1** | Plus One | The mark of an account of a Claire server on one rendition: "같은 출처의 정리본 가운데 이 정리본을 추천한다". Only Claire servers send and receive it (Section 5.12). | "좋아요" (that is the `Like` of a post), "추천" as a separate name, "투표" |
| **좋아요** | Like | The ActivityPub `Like` of a post: of a remote post, of an own note, or of a published document (its object is the primary rendition). Each server in the fediverse can send it. | "+1" |
| **초록** | Summary (code `summary`) | The 1 to 3 plain sentences of a document (GLOSSARY 1.3, O12, implemented). | "요약" for this item |
| **의견** | Opinion | A public post on any server that is in the reply tree of a published object of this server (a reply, or a reply to a reply), or a quote post of it with a valid stamp. The `의견` tab of the Silvercord view shows them. | "댓글" (a comment is a function of a page; an opinion is a federated post), "스레드" (use 글타래) |
| **발행** | Publication / Publish | The action that makes a document of a public personal knowledge base an ActivityPub object for followers and makes its renditions available to Claire servers. Stage 3 of the flow 교류 · 적재 · 발행. | 게시 for this action |
| **게시** | Post | The action that sends a note. 게시물 (Post) stays the name of an item in the timeline. | 발행 for a note |
| **출처 표기** | Citation | The bibliographic reference to a document or a rendition for machines: `cite-as` and citation metadata. The page has no copy action for it (Rev. 5). | 인용 for this meaning (인용 is the quote of FEP-044f) |
| **채우기** | Fill | The one-time read of recent posts of an actor after an account follows it (Section 5.13). | "백필" in the user interface |

Notes:

1. "상세" (GLOSSARY 1.4) stays the name of the stored body (`documents.detail`) in the code and the implementation documents. In the user interface, from phase E3, the body section of a document is "정리본". No template is named "상세" (O14): the current prompt is the template "기본" (`standard`). Today the page shows the section label "상세" (fact 15).
2. "에디션" stays only in the same-entity rule of the glossary ("버전·에디션·제품과 회사"), a word about products in the world, and in "Shopify Editions", a product name.

---

## 5. Design

### 5.1 ED1: Rendition model

1. A rendition is a document body made with one rendition template. One document has at most one rendition for each template.
2. The template changes the body only. The structured extraction (초록, key claims, entities, relations) stays one for each document. Reason: fact 2 says that the body render is already independent of the graph, so a second rendition adds no entity and no relation.
3. A new version of the same template replaces the body of that rendition. The rendition keeps its identity, its object ID, its address, its likes, and its +1 marks.
4. The primary rendition (대표 정리본) is always the rendition of the template `standard` (기본), and it does not change (O6, Rev. 9). Each ingest makes it. Other servers see its object as the post: the title, the 초록, and the Silvercord link (Section 5.5); its body stays on Claire servers. Reason: the owner rule that the 대표 정리본 cannot be a 간결 rendition, and one fixed template for the 대표 정리본 of each document of a rendition set.
5. A reader of a rendition set sees renditions of other accounts. These renditions never enter the knowledge base of the reader (RD10 rule 1). The reader can ingest the same source with an own template; that makes a new document and a new rendition.

### 5.2 ED2: Rendition templates

The texts are in `src/claire/extract/rendition_templates.py` (owner decisions O2 and O11). `rendition_template(id)` gives a template; an unknown identifier raises `UnknownRenditionTemplate` (no default template).

**Structure** (Rev. 11, owner question "기본 원칙을 공통으로 수용하고, 템플릿으로서의 각 특성을 적용"). Before Rev. 11 each template was a full copy of the prompt, and the common rules were in four places (three templates and the current prompt). Now:

| Part | Content | Rule |
| :--- | :--- | :--- |
| Core rules (공통 원칙) | The 14 rules of the current prompt, word for word, in their order: AsciiDoc only, written style, structure, quotes, code, notes, tables, emphasis, names, formulas, anchors, no invented text, truncated sections, no author line. | One copy. A template without changes builds the current prompt exactly (test). |
| Fixed rules | AsciiDoc only, written style, quotes, emphasis, names, no invented text, truncated sections, no author line. | No template replaces or omits them; a template can add lines (for example 간결: "`[quote]` 한 곳 이내"). The module refuses another change at load time (test). |
| Traits (특성) | Rule texts that more than one template uses: no tables of the source; representative formulas only. | One copy for each trait. |
| Changes of a template | Its scope sentence and length; replaced rules; added lines; omitted rules; inserted rules; the first section after the title; a line for the figures. | The rules get their numbers when the template builds its instructions. |

| Template | Changes |
| :--- | :--- |
| `standard` 기본 | Traits: no tables of the source, representative formulas only. |
| `concise` 간결 | Short scope sentence; fixed section order (rule "간결 구성") and the inserted rule "덜어 낼 것과 남길 것"; short code; at most one note block; the two traits; one `[quote]` at most (added line); "no text to fill space" (added line); no anchors; first section `== 핵심`; at most two figures. |
| `academic` 학술 | None: the current prompt. The owner changes it later. |

| Identifier | Korean name | Version | Text |
| :--- | :--- | :--- | :--- |
| `standard` | 기본 | `standard-v1` | A copy of the current prompt with two changes (O17): rule 7 "원문 테이블 미사용" (no `\|===` table; the key numbers of a source table go into sentences or bullets, with units and conditions, not rounded); rule 10 "수식은 대표 공식만" (only the formulas that define the main result or method, usually 1 to 3, as block formulas; derivations and other symbols in words). The other rules stay. Not "상세": the body itself is 상세 (O14). |
| `concise` | 간결 | `concise-v1` | A copy of the current prompt, written again for a short body: about one A4 page (merged: `scale` to `2 × scale` pages); the section order `== 핵심` (3 to 5 one-sentence claims), `== 내용`, `== 결과와 수치`, `== 한계`, each only when the source has it; less background, repetition, and examples; numbers, conditions, definitions, and names stay exactly; at most one `[quote]`, one note block, two figures; no tables of the source: the key numbers of a result table go into `== 결과와 수치` as sentences or bullets, with units and conditions (O18); representative formulas only (O20); no text that the source does not have, also to fill space. The fixed rules stay (see Structure). |
| `academic` | 학술 | `academic-v1` | An exact copy of the current prompt, with all tables and all formulas (O17: it stays). The owner changes it later. |

Rules:

1. The code defines the set. An account cannot make a template. Reasons: the renditions of a rendition set must be comparable across servers; a free prompt from an account is a prompt injection path into a public page; a template identifier on the wire must have one meaning.
2. The version counts from the first use in phase E3. After that, a change of the text of a template increments its version. (The change of revision 6, "요약" to "초록" in the instruction text, came before the first use and keeps `v1`.)
3. Each template gets the catalog keys `rendition.template.{id}.label` and `rendition.template.{id}.desc` in English and Korean when phase E3 shows it.
4. A new template is a new identifier in the code and a new value in the vocabulary (Section 5.5). An identifier never changes its meaning and is never used again for another template.
5. The agent client gets the template text from the server in the claimed task. The client does not hold template texts. The server validates the body as now (`_validate_asciidoc_detail`).
6. Phase E3 replaces the placeholder of fact 3: `prompt_template` carries the rendition template identifier, and the value `"default"` and `DEFAULT_EXTRACTION_TEMPLATE` go away at once (no external consumers). Done in E3a: the agent client template `agent_clients/common/agents/extractor.md.tmpl` names no value of `prompt_template`, so it did not change.
7. The body of `standard` and `academic` often starts with `== 개요`, because rule 14 of the current prompt names it as an example. That heading is a part of the body, not the 초록 (Rev. 5 question).
8. **Template, document, and set** (review for O16). A template tells how the body is written; it does not tell who wrote it or where. One document (one account, one source) has at most one rendition for each template, thus at most three. A rendition set (all accounts and servers with the same source) can have many renditions of the same template. A user of another server or another model can make a rendition with the same template (the owner's reading, Rev. 8). Since Rev. 9 the 대표 정리본 is always the rendition of the template 기본.
9. **No two templates with the same text** (O17). The owner kept `academic` and changed `standard`, so the three texts differ, and the ingest form offers all three. The test `tests/test_rendition_templates.py` checks that no two templates build the same instructions.
10. **The ingest from E3.** Each body run uses a template (E3a): the 대표 정리본 uses `standard` (no tables of the source), and `prompts.py` keeps no copy of the rules. Bodies of before E3 were made with the prompt of before the templates (the text of `academic`); they count as `standard` with the version `pre-template` (O21). The table data stays in the raw text and in the graph; the rendition `academic` keeps the tables. GLOSSARY 1.4 says this since Rev. 10.
11. **Tables in 간결** (O18). `concise` uses no tables of the source, as `standard` (owner: "권장안"). Only `academic` keeps the tables.
12. **Formulas in 간결** (O20). `concise` keeps only the representative formulas, as `standard` (owner: "수용합니다"). Only `academic` keeps all formulas.

### 5.3 ED3: Creation of renditions and the source identifier

1. **At ingest.** Each ingest makes the rendition `standard` (기본), which is the 대표 정리본 (Rev. 9). The ingest request can also select `concise` and `academic`; the API requires the list of the additional templates, which can be empty (no hidden default).
2. **Later.** The document menu has "정리본 추가" with the templates that the document does not have. The run uses the stored raw text (glossary 1.1). It fetches nothing. It costs one model call.
3. **Limits.** The number of rendition runs of an account uses the tier features as other model work does.
4. **Source identifier.** A document gets a source identifier when its source has one of these forms, in this order:

| Order | Form | Source identifier |
| :--- | :--- | :--- |
| 1 | A DOI in the URL, or in the PDF metadata that the extraction confirmed | `https://doi.org/{doi in lower case}` |
| 2 | An arXiv address | `https://arxiv.org/abs/{id without version}` (as `canonicalize_url` does now) |
| 3 | A web URL | the result of `canonicalize_url` |
| 4 | A file without a URL | none (O4: A) |

5. The key on the wire is `claire:sourceKey`: SHA-256 of the source identifier in lowercase hexadecimal. The normalization rules are part of the vocabulary contract with a version (`claire:sourceIdVersion`). A change of the rules is a new version, and a server sends the keys of all versions that it knows.
6. A document without a source identifier has renditions, but no rendition set across accounts.

### 5.4 ED4: Rendition set and exchange between Claire servers

1. **Members.** A rendition set has: the renditions of the document that the reader opened; the published renditions of other documents on this server with the same source key; the published renditions on other Claire servers with the same source key. Unpublished renditions of other accounts are never in a set (publication is the consent of the owner, RD10 rule 2).
2. **Pull first.** The publication of a document sends the activities of today (`Create`, `Update`, `Delete` of the document object). A new, changed, or deleted rendition sends no new activity to followers. A Claire server reads renditions when a reader needs them. Reasons: Mastodon followers get no noise; a server that follows nobody of the origin can still find renditions; there is less delivery work.
3. **Discovery.**
   1. From a received document object with `claire:renditions` (the follow of an account).
   2. From the source index of a known Claire server: `GET /ap/sources/{sourceKey}` gives an `OrderedCollection` of the published rendition objects of that server for the key. The sidecar answers only a request that a server actor of another server signed, with the controls of KS1 (signature, blocked domain, the same 404 for each refusal). The servers to ask are the known servers of KF1 and the origins of renditions that the server already has. There is no relay and no server-level partnership ([`RETIRED_PARTNERSHIP_IDEAS.md`](../ideas/RETIRED_PARTNERSHIP_IDEAS.md)).
4. **Cache and deletion.** A server keeps the metadata and the body of a remote rendition as a cache with a lifetime (proposal: 24 hours) and revalidation with `ETag`. A 410 or a `Tombstone` deletes the copy at once, and a `Delete` of the document deletes the copies of its renditions. The origin records each server actor that read a body (the read is signed), and at a retract or a privacy trigger it sends `Delete` of the rendition objects to these servers only (the idea of the bloom filter of Mastodon 5.0, R5, with an exact list because the number of Claire servers is small).
5. **Privacy.** A source index request tells the other server that some reader of the asking server opened a document with that key. The server actor signs it, not the account.
6. **Load.** A server asks each known server at most one time for each key in the lifetime, keeps a negative answer for the same lifetime, and uses the rate limits of KF1.

### 5.5 ED5: Federation shape (answer to Q3)

**Rule: the standard Activity Streams fields of each Claire object carry the 초록 and the link only. A body is only behind the Claire extension property `claire:body`.**

Reasons:

1. The delivered object stays as RD21 says. Mastodon shows `content` (R1). Software that keeps the full JSON of an object gets no body, because the body is a link, not an embedded value.
2. One object has one representation for all readers. A response that changes with the signature of the reader breaks caches and the same-origin rule.
3. Claire servers recognize the extension from the object itself (R4).

**Primary rendition = document object (O5, O6).** The delivered `Article` of a published document is also the object of its primary rendition. All fields of today stay. New fields:

| Field | Value |
| :--- | :--- |
| `claire:template`, `claire:templateVersion` | The template of the primary rendition. |
| `claire:body`, `claire:bodyHash` | Link to the body representation; SHA-256 of the AsciiDoc body. (E4a: not `claire:contentHash`, which version 1 defines as the SHA-256 of the source text of a document: one term for each concept.) |
| `claire:sourceKey`, `claire:sourceIdVersion` | Section 5.3. Absent for a document without a source identifier. |
| `claire:renditions` | IRI of an `OrderedCollection` of all published rendition objects of the document, the primary rendition first. |
| `claire:plusOnes` | The +1 count (Section 5.12). |
| `replies`, `likes`, `shares` | Section 5.11 A. |
| `interactionPolicy` | `{"canQuote": {"automaticApproval": ["https://www.w3.org/ns/activitystreams#Public"]}}` (Section 5.7). |

**Other renditions.** Each is a separate object in `federation_objects` (kind `rendition`, with its parent object) at `{origin}/ap/objects/{public_id}`. Reason: reactions, quotes, and tombstones already work for this path (facts 6, 9). The type is `Article`; the property `claire:renditionOf` marks the rendition. Mastodon gets the same type that it shows now.

```json
{
  "@context": ["https://www.w3.org/ns/activitystreams", "https://w3id.org/claire-bible/ns/v2"],
  "id": "https://a.example/ap/objects/1043",
  "type": "Article",
  "attributedTo": "https://a.example/ap/actors/u7k2",
  "name": "Title of the document",
  "content": "<p>초록 of the document.</p><p><a href=\"https://a.example/silvercord/7mq2k9x4hz3d8n5p/renditions/concise\">https://a.example/silvercord/7mq2k9x4hz3d8n5p/renditions/concise</a></p>",
  "url": "https://a.example/silvercord/7mq2k9x4hz3d8n5p/renditions/concise",
  "published": "2026-11-25T03:00:00Z",
  "updated": "2026-11-25T03:00:00Z",
  "to": ["https://www.w3.org/ns/activitystreams#Public"],
  "claire:renditionOf": "https://a.example/ap/objects/1001",
  "claire:template": "concise",
  "claire:templateVersion": "concise-v1",
  "claire:sourceKey": "9f2c…(64 hexadecimal characters)",
  "claire:sourceIdVersion": 1,
  "claire:bodyHash": "41ab…(64 hexadecimal characters)",
  "claire:body": "https://a.example/ap/objects/1043/body",
  "claire:plusOnes": {"id": "https://a.example/ap/objects/1043/plus-ones", "type": "Collection", "totalItems": 9, "claire:servers": 4, "claire:score": 9},
  "replies": "https://a.example/ap/objects/1043/replies",
  "likes": {"id": "https://a.example/ap/objects/1043/likes", "type": "Collection", "totalItems": 2},
  "shares": {"id": "https://a.example/ap/objects/1043/shares", "type": "Collection", "totalItems": 0},
  "interactionPolicy": {"canQuote": {"automaticApproval": ["https://www.w3.org/ns/activitystreams#Public"]}}
}
```

- No `Create` of a rendition goes to followers (Section 5.4 rule 2). A fetch by URL from other software gives the 초록 and the link only.
- A reader compares `claire:bodyHash` before it gets the body again.

**Body representation.** `GET {object ID}/body`. The sidecar answers only a request that a server actor of another server signed (KS1 controls). The answer is on the origin of the object (R11):

```json
{
  "@context": ["https://www.w3.org/ns/activitystreams", "https://w3id.org/claire-bible/ns/v2"],
  "id": "https://a.example/ap/objects/1043/body",
  "type": "claire:RenditionBody",
  "claire:rendition": "https://a.example/ap/objects/1043",
  "mediaType": "text/html",
  "content": "<h2>…</h2><p>…</p>",
  "source": {"mediaType": "text/asciidoc", "content": "== …"},
  "claire:bodyHash": "41ab…"
}
```

- `content` is the compiled body, cleaned with `nh3` on the origin. The receiver cleans it again and shows it only in the sandbox frame (fact 14).
- `source` is the Activity Streams property for the original format (also FEP-b2b8).
- The body is not secret: the Silvercord page of a published document shows it. The signed access makes the body a channel between Claire servers, with blocks, quotas, logs, and the exact delete list of Section 5.4 rule 4.

**Vocabulary.** New terms go to `https://w3id.org/claire-bible/ns/v2`: `claire:RenditionBody`, `claire:rendition`, `claire:renditionOf`, `claire:renditions`, `claire:template` (closed values `standard`, `concise`, `academic`), `claire:templateVersion`, `claire:sourceKey`, `claire:sourceIdVersion`, `claire:body`, `claire:bodyHash`, and the +1 terms of Section 5.12 (`claire:PlusOne`, `claire:plusOnes`, `claire:servers`, `claire:score`). `v1` does not change. The contract of the internal federation API goes from 1.17 to the next version.

### 5.6 ED6: Likes (revised in Rev. 6)

1. The `Like` is the like of a post, as it is in ActivityPub (O5). The posts of this server are the published documents (whose object is the primary rendition) and the own notes. The account likes a remote post with the existing function (Section 25.4 of `FEDERATION.md`).
2. A rendition that is not the primary rendition is not a post: no follower gets it. The page offers no like for it. An inbound `Like` of its object is stored as for each object (fact 6), and `likes.totalItems` shows it.
3. The origin counts (`federation_object_reactions`) and shows `likes.totalItems` (R13). The author cannot like an own object; the origin ignores such a `Like`.
4. The `의견` tab shows the like control and the counts of likes and boosts of the post (Section 5.11 D rule 3). The rendition bar shows the +1 control (Section 5.12), not the like control.
5. Likes are data for later decisions only; no reputation and no order reads them (O8, fact 16).
6. Domain blocks and the rate limit of interactions apply to inbound `Like`.

### 5.7 ED7: Quotes always permitted, and citation (answer to Q6)

Quotes (FEP-044f):

1. A published document and each of its renditions advertise `canQuote` with automatic approval for the public collection.
2. Each `QuoteRequest` for a document or a rendition gets `Accept` with a `QuoteAuthorization` stamp at once (R3).
3. The owner cannot refuse or revoke a quote of a document or a rendition. The policy dialog of a published document, the field `quote_policy` of `PATCH /api/federation/objects/{object_id}`, and the policy values for documents go away. A retract of the document deletes the object; then the stamp path gives 404 or 410 as the result of the retract.
4. Migration: `federation_objects.quote_policy` becomes `public` for all rows. Each `pending` and `refused` request for a document becomes `approved` and sends its stamp. Each `revoked` row stays revoked (the remote side already has the `Delete`).
5. Notes keep the four policies (O3: A).
6. The rate limit of quote requests stays (spam is not a quote).
7. An approved quote post is an opinion (Section 5.11 B rule 3). The page shows no message about the quote policy (Rev. 5).

Citation (출처 표기) of the Silvercord view, for machines only (Rev. 5):

1. `<link rel="cite-as" href="{permalink of the item}">` (RFC 8574). A rendition page cites its rendition address; the page of the primary rendition cites the document address.
2. `citation_title`, `citation_author` (the account), `citation_publication_date`, `citation_public_url`, and `citation_language` meta elements.
3. JSON-LD of schema.org: `Article` with `isBasedOn` (the source URL), `exampleOfWork` (the source identifier as `CreativeWork`), `version` (the template identifier and version), `author`, `datePublished`, `dateModified`. Mastodon 5.0 adds schema.org markup to its pages too (R5).
4. `<meta name="fediverse:creator" content="@{username}@{host}">`, and `attributionDomains` with the host of the server in the actor document (R10). **State (2026-11-26):** the meta element is implemented (phase E2). `attributionDomains` is not: the vocabulary of Fedify 2.4.6 has no such property of `Person`, and the property must be in the actor document and in each `Update(Person)` that the sidecar sends (Mastodon stores the list from each of them), so a change of the served document alone gives a state that changes with each update. It is phase E2b: check the next Fedify release for the property, else add it to the two serialized forms in the sidecar. Until then Mastodon shows no byline.

### 5.8 ED8: Silvercord paths (answer to Q5)

Decision proposal: **the path word stays fixed. No new path word. Extension is inside the path.**

| Address | Content |
| :--- | :--- |
| `/silvercord/{token}` | The document, with its primary rendition, the rendition picker, and the `본문` / `의견` tabs. Unchanged as a link. |
| `/silvercord/{token}/renditions/{template}` | One other rendition of this document. `{template}` is an identifier of Section 5.2. `/silvercord/{token}/renditions/standard` gives 308 to `/silvercord/{token}`, because the 대표 정리본 is the rendition `standard` and its object is the document (Section 5.5). |
| `/silvercord/{token}/renditions` | JSON of the rendition set for the picker: local renditions with their paths, remote renditions with their origin addresses and local render tokens, and the +1 counts. |
| `/silvercord/{token}/opinions` | JSON of the opinions of the object of the page, with the refresh state (Section 5.11 B). A rendition page uses `/silvercord/{token}/renditions/{template}/opinions`. |
| `/silvercord/{token}/workspace`, `/silvercord/{token}/graph` | Unchanged. |

Rules:

1. **Rule for later kinds.** A new kind that is an ActivityPub object and an item of its own gets a token on `/silvercord/{token}` (owner rule 2026-11-23). A part or a view of an item is a sub-path of the token of that item. A rendition is a part of its document (the glossary makes the body a part of the document); the opinions and the graph are views.
2. **Inheritance.** A sub-path takes the alias, gone, hidden, and link access rules of the document token. An alias gives 308 to the same sub-path of the target. A template that the document does not have gives 404.
3. **ActivityPub.** A request that accepts only ActivityPub gets 303 from a rendition page to its object ID, as the document page does now.
4. **Remote renditions.** A remote rendition has no local token, because a token names an item of this server. The local document page shows it in the sandbox frame with the render token of the federation client and keeps the selection in its query (`?rendition={short key}`). The citation and the "open on the origin" link use the address on the origin.
5. Reasons against one token for each rendition: the registry would need a new kind and new alias and gone rules that copy those of the document; a near-duplicate merge would need more alias rows; the rendition already has a stable identity in (document, template).

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

The design uses the guides of the `modern-web-guidance` tool (version `2026_10_12-dacb5911`): `rich-media-picker`, `cross-document-transitions`, `scoped-component-transitions`, `improve-next-page-load-performance`, `defer-rendering-heavy-content`, and `accessibility` (live regions, `<progress>` for waiting states, `iframe` titles, target sizes, `aria-pressed`).

1. **Rendition picker.** A native `<select>` with a visible `<label>` ("정리본"), with `appearance: base-select` and rich `<option>` content: template name, account, server, +1 count, date. The text of each option must make sense alone, because Firefox shows a plain list ("간결. @bob@b.example. +1 12."). Use visually hidden full stops for that. Put no button or link inside an option. The selected option must be visible in the open list. Support: Chrome and Edge 135, Safari 27; Firefox shows the native list.
2. **+1 control.** A `<button aria-pressed="true|false">` in the rendition bar, outside the picker, with the visible text "+1" and the count. An anonymous visitor and an account of another software see the count and, for the visitor, a sign-in link, not a dead button. The author sees the count as text (Section 5.12 rule 3).
3. **Switching local renditions.** A local rendition is a page (Section 5.8), so a switch is a navigation. Use `@view-transition { navigation: auto; }` inside `@media (prefers-reduced-motion: no-preference)`. Chrome, Edge, and Safari 18.2 animate; Firefox navigates at once.
4. **Switching to a remote rendition, and new opinions.** The frame changes inside the page. Use `element.startViewTransition()` on the reader container or on the opinions container (Chrome and Edge 147). Other browsers change the frame at once, without `document.startViewTransition()`, so that the fixed bottom bar and the popovers stay on top.
5. **Prefetch.** Speculation rules with a document rule for the rendition links of the picker, `prefetch`, eagerness `moderate`. No `prerender` (signed-in pages have state). Exclude `/silvercord/*/workspace` and each state change. Chromium only. Whether Chrome uses a prefetched answer with `Cache-Control: private, no-store` is **[unverified]**; measure it first.
6. **Long bodies and long opinion trees.** `content-visibility: auto` with `contain-intrinsic-size: auto 800px` on body sections and opinion groups below the first screen, never on the first screen. Test keyboard navigation through these sections.
7. **Mathematics.** `academic` and the other templates keep formulas; no template is a display fallback for formulas.
8. Each new text uses catalog keys in English and Korean from the start.
9. **Place of the rendition controls.** The page keeps the order of the current view: title, metadata, the section "초록" (one for each document; it does not change with the rendition), then the section "정리본". The rendition bar (label, picker, +1 control, and for the owner "정리본 추가") is the first element of the section "정리본", on desktop and on mobile. On mobile the picker takes the full width and the other controls go to a second line. The bottom bar keeps only `본문` / `의견` and the graph button (SD5). Reason: the bottom bar switches views; the rendition bar belongs to the body that it changes.
10. **Groups and order in the picker.** The options are in three `<optgroup>` groups by origin: "이 문서", "같은 출처 · 이 서버", "같은 출처 · 다른 서버". Inside each group, the order is the template order 기본, 간결, 학술 (`standard`, `concise`, `academic`), and for the same template the newest `updated` first (O16). The +1 count does not change the order. Each option shows the template, the account, "대표 정리본" for the primary rendition, the +1 count, and the date. A line under the bar shows the template, its version, the account, the date, and the origin of the selected rendition.
11. **No copy button for a citation or for the address** (owner, Rev. 5). The address of the page is the address of the rendition (Section 5.8), and the link copy of the workspace reader and of the timeline exists ([`PERMALINKS.md`](../implementation/PERMALINKS.md) Section 5).

### 5.11 ED11: Opinions from other servers (answer to Q7)

Intent: the `의견` tab of a published object shows the public conversation about it in the whole fediverse, not only the replies that reached this server. The structure follows Mastodon 4.5 and later (owner: "최신 Mastodon … 구조를 우선"), and adds one scheduled refresh from the FediFetcher idea.

**A. Publish: make Claire threads readable for other servers.** This part improves Claire as an ActivityPub server, also without renditions.

1. Each published object (document, rendition, own note) gets `replies` in the form of Mastodon (R13): a `Collection` whose `first` page has the self-replies of the author (at most 5) and whose `next` goes to pages of the replies of other accounts. An item is the IRI of a public reply that this server knows (delivered or fetched) and that is not hidden. Pages have 20 items and a cursor. Reason: Mastodon 4.5 and later crawl `replies` (R12); without it, a reader on Mastodon sees only the replies that reached the server of that reader.
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
2. `object_iri` can be a remote rendition: a reader of a remote rendition gets the opinions of its origin with the same task.
3. A delivered reply to a published document also stays a timeline item of the owner (fact 7). The opinion row is a separate row for the public page.
4. A retract of the object deletes its opinions. A remote `Delete` deletes one opinion. The account purge does not delete opinions of other actors on other objects.

**D. Show: the `의견` tab (replaces the prototype of SR5 and SD8).**

1. A 글타래 tree of the replies by time, with a depth of 4 on the page and a link "원 서버에서 계속 (N개 더)" for deeper levels; the quote posts in a separate group "인용".
2. **Text in the page, formatted body in the frame.** Each opinion needs its own controls (reply, like, boost, quote, hide), and a sandbox frame without scripts cannot carry them; one frame for each opinion is too heavy. Thus the page shows each opinion as text: the author name, the handle, the time, the content warning, and the body converted on the server from the cleaned HTML to text with paragraph breaks and `http` and `https` links (`rel="nofollow noopener noreferrer"`). No other markup, no image, and no style from the remote server enters the page. "서식과 첨부 보기" opens the formatted body and the attachments of one opinion in a sandbox frame from the usercontent host (the rules of Section 19.2 of `FEDERATION.md`) in a modal dialog. This changes the rule of fact 14 for opinions only: the rule now permits remote text as text nodes, as the page already does for the summary of a content warning.
3. The top of the tab has the counts of replies, quotes, likes, and boosts of the post, and the like control of the post for a signed-in actor (Section 5.6 rule 4). Each opinion has the author (link to the profile on its origin), the time (link to the post on its origin), the content warning (closed), and the text.
4. A signed-in account that is an actor can reply, like, boost, and quote each opinion with the functions of the federation client (Sections 25 and 26 of `FEDERATION.md`).
5. The owner of the document and the server admin can hide an opinion from this page. A hidden opinion is also not in the `replies` collection. This is moderation of the page, not a quote refusal: the stamp of a quote stays valid (Section 5.7).
6. A document of a private knowledge base with link access is not published; its tab keeps the notice that it is not federated (SILVERCORD design access matrix).
7. **Indexing.** The page loads the opinions only when the reader opens the `의견` tab (as the graph panel loads its data on the first open), the JSON route has `X-Robots-Tag: noindex`, and the opinions container has `data-nosnippet`. The posts of other people are not indexed under this server.
8. **State of a fetch.** While a fetch runs, the tab shows a `<progress>` element with a text, not a live region. When it ends, one polite live region of the page says the number of new opinions once, and the new opinions have a mark "새 의견" until the next visit. A failure shows a `role="alert"` text, the opinions that the server has, and "다시 시도"; it never shows invented data (PRINCIPLES C2).
9. **Reply from another server.** An anonymous visitor sees a text: to reply from an account of another server, paste the address of this page into the search of that server. The page address is enough: for a request that accepts only ActivityPub, the permalink gives 303 to the object ID ([`PERMALINKS.md`](../implementation/PERMALINKS.md) Section 3 rule 7). A signed-in actor gets "이 정리본에 답글", which opens the compose dialog of the federation client with `inReplyTo` set.

**E. Scheduled refresh (from FediFetcher, R16).** Once a day, the server fetches the opinions of its own published objects that got a delivered reply, a like, or a quote request in the last 7 days, with the limits of rule B5 and at most 200 objects in one run. Reason: Mastodon fetches only when a person opens a post; this refresh keeps the tab current also before the next visit. FediFetcher's scan of all timelines is not taken.

**F. The thread fetch of the client (fact 8).** It changes in one point: it reads the reply tree and the `context` collection together and merges them, not the reply tree only after an empty `context`. Reason: Mastodon gives a `context` IRI that is not a useful collection, and a reply tree that is complete (R13, R15).

### 5.12 ED12: +1 between Claire servers (Rev. 6)

Intent: the owner request "다른 이용자가 적재한 다른 정리본을 골라 보고 +1 점수를 부여" and the separate score of O8 ("발행 에디션 간 평판"). +1 is the score between the renditions of one source. It is not a `Like`, and only Claire servers send and receive it.

**Rules of the mark.**

1. **Meaning.** "같은 출처의 정리본 가운데 이 정리본을 추천한다". The object of a +1 is always a rendition object (the primary rendition is the document object), never a note and never a post of other software.
2. **Who.** An account of a Claire server that is a federation actor (active, with a USERNAME, GD5 and GD8). One +1 for each account and rendition. An account can give +1 to more than one rendition of the same set (approval voting). Reason: a reader can find two good renditions for different uses (a concise one and an academic one); one vote for each set would force a single choice, and approval counts rank alternatives well.
3. **Not for own renditions.** The author of a rendition cannot give it +1; the origin ignores such a mark. The page shows the count as text to the author.
4. **Take back.** The account can take its +1 back at any time (`Undo`).

**Wire.**

5. **Activity.** A Claire activity of vocabulary `v2`, sent by the account actor of the reader to the inbox of the author of the rendition (shared inbox when the origin has one):

```json
{
  "@context": ["https://www.w3.org/ns/activitystreams", "https://w3id.org/claire-bible/ns/v2"],
  "id": "https://b.example/ap/actors/k3p9#plus-ones/88",
  "type": ["Activity", "claire:PlusOne"],
  "actor": "https://b.example/ap/actors/k3p9",
  "object": "https://a.example/ap/objects/1043",
  "to": ["https://a.example/ap/actors/u7k2"],
  "published": "2026-11-25T05:00:00Z"
}
```

   The type array has the base type `Activity` first, as the vocabulary rule says. It is not `Like`: software that does not know the Claire terms reads an unknown activity and does nothing, and a Claire server never counts it as a like. `Undo` with the `claire:PlusOne` embedded (`id`, `type`, `actor`, `object`) takes it back.
6. **Only between Claire servers.** The sender sends a `claire:PlusOne` only to the origin of a rendition object that it read as a rendition (an object with `claire:template`, Section 5.5). That origin is a Claire server by construction; no other server gets a +1. The receiver applies a `claire:PlusOne` only when `object` is an own published rendition; each other object is refused (`not_a_rendition`). The inbox journal of KF1 records each +1 and each `Undo` (fact 10), so a failure can be replayed.
7. **Count.** The origin stores one row for each actor and rendition and publishes the counts in `claire:plusOnes` (Section 5.5 example): `totalItems` (all marks), `claire:servers` (the number of distinct servers of the actors), and `claire:score`. The collection has no items, as the `likes` of Mastodon (R13): who gave +1 is not public.

**Score.**

8. **No order by score** (O16). The picker orders by template (Section 5.10 rule 10). The score is the "+1 점수" that the page shows for each rendition.
9. **Score and its limit for one server** (O15). `claire:score` is the number of marks, but the marks of the accounts of one server count at most `PLUS_ONE_SERVER_LIMIT` = 1,000. The owner rule: the marks of one server cannot be more than the accounts of that server, so the limit is the number of accounts that one Claire server can serve now. The value is an estimate (table below), a constant in the code and in the vocabulary page (no admin setting), and it changes after a measurement (Section 10 item 8). With this limit an honest server is never cut, and a server that makes accounts only for +1 counts at most as much as one full server.

| Item | Value | Source |
| :--- | :--- | :--- |
| Process and database | One API process; one `claire.db` for all knowledge bases with WAL; one writer at a time; `busy_timeout` 5 seconds | [code] `src/claire/store/db.py`, `docker-compose.yml` |
| Limits of one account (default tier) | 20 ingests in one day, 5,000 documents, 2,048 MB, 1 concurrent job with an own key | [code] `ServerTier` in `src/claire/accounts/provider_policy.py` |
| Write time of one ingest in `claire.db` | about 1 second (entity resolution, relations, extraction log) | **[unverified]** estimate; the model calls are outside the database |
| Load of 1,000 accounts at the full daily limit | 20,000 ingests in one day, about 20,000 seconds of writes: 23 % of the day on average, near 100 % in a peak hour at 4 times the average | Calculation from the three rows above |
| Storage of 1,000 accounts at the full limit | about 2 TB | Calculation; normal use is much less |

   The estimate: at 1,000 accounts, the single writer reaches its limit in a peak hour when all accounts use their full daily limit. Thus 1,000 accounts is the size of one server unit now. More accounts need a second process for writes or a second database, which is a different architecture.
10. **Trust.** The origin computes the counts of its own renditions, and a reader server cannot check them (the collection has no items). A reader server shows the counts of an origin as they are and gives no weight to an origin on its domain block list. The own marks of the accounts of the reader server are exact on that server. This limit is accepted: the counts are a help for the choice, not a guarantee.
11. **Freshness.** A reader server reads the counts with the rendition metadata (cache of Section 5.4 rule 4, revalidation with `ETag`) and shows its own new mark at once.

**Limits, privacy, and lifecycle.**

12. **Rate limits.** At most 60 marks of one account in one hour (sender), and at most 600 marks from one domain in one hour (receiver; more are `rate_limited`). Domain blocks apply on both sides.
13. **No notification.** A +1 makes no notification of the author; the author sees the counts in the picker and in the list of the published documents (`/syndication` stage 3).
14. **Retract and purge.** A retract or a privacy trigger of a rendition deletes its marks on the origin. The account purge makes the server of the account send `Undo` for each of its marks (a lifecycle task, as FP3-4).
15. **Reputation.** +1 is the score between renditions. It does not feed the reputation of an account; the reputation of a publisher stays on hold (O8).

### 5.13 ED13: Fill of recent posts after a follow (Rev. 6, from the idea document)

Intent: an account that follows a remote actor sees some posts of that actor at once, not only the posts that come after the follow (fact 17). Mastodon does not do this (R17); FediFetcher does (R16). The structure of Mastodon comes first (owner rule): the `featured` collection. One page of the `outbox` follows, from FediFetcher.

1. **Trigger.** The `Accept` of a `Follow` of the account (fact 17). One fill for each follow. A new follow of the same actor after an unfollow fills again only when the last fill is older than 24 hours. A `Reject` or a follow that waits makes no fill.
2. **Step 1: `featured` (Mastodon).** The sidecar reads the first page of the `featured` collection of the actor (pinned posts), as Mastodon does when it processes an account (R17).
3. **Step 2: `outbox` (FediFetcher).** The sidecar reads the first page of the `outbox` of the actor (Mastodon gives 20 items). It keeps `Create` activities of public posts (`Note`, `Article`, `Page`, `Question`) whose author is the actor and whose `published` is in the retention of the timeline. It skips `Announce` (posts of third parties) and replies to actors that the account does not follow.
4. **Signature.** The key of the account signs the reads, because the account follows the actor and a server in authorized fetch mode needs a signature (R17). Only public posts are stored, also when the server gives more.
5. **Rules of objects.** The rules of inbound posts apply (Section 18 of `FEDERATION.md`): each object from the origin of its ID (R11), domain blocks, private addresses, size limits. The media follow the media proxy rules.
6. **Place in the timeline.** The timeline orders items by storage (fact 17), so filled posts would go to the top of the home stream as if they were new. Thus a filled post is an item with `in_timeline` = 0 and the mark `filled`. The client shows the filled posts in the view of that actor (the follow list opens the posts of the actor, newest first by `published`), with the label "팔로우 전 글". A later delivered post of the actor goes to the home stream as now.
7. **Limits.** 1 page of `featured`, 1 page of `outbox`, at most 20 stored posts and 30 seconds for one fill. At most 30 fills for one remote domain in one hour for the server; more fills wait. When another account of this server filled the same actor in the last hour, the server copies those items and reads nothing.
8. **Sidecar.** A new client task kind `fill_follow` (`client-tasks`, contract change), made by Claire in the same transaction as the change to `accepted`. The sidecar posts the objects to a new internal endpoint (`fill-objects`, with the parts limits of `context-objects`).
9. **Not taken from FediFetcher.** Fills for new followers, for accounts in notifications, and for lists: they cost requests to other servers for actors that the account did not choose, and some of them read the posts of people who only followed the account.
10. **Retention.** Filled items follow the retention run of timeline items (Section 27.4 of `FEDERATION.md`).

---

## 6. Data (proposal)

| Item | Proposal |
| :--- | :--- |
| `document_renditions` (`claire.db`) | `doc_id`, `kb_id`, `template`, `template_version`, `detail`, `detail_format`, `detail_html`, `created_at`, `updated_at`, `model_lineage` (JSON), unique (`doc_id`, `template`). |
| Primary rendition body | `documents.detail*` stays the body of the 대표 정리본 (template `standard`), so that the 38 readers (fact 2) do not change. `document_renditions` has the renditions `concise` and `academic`, and a row without a body for the 대표 정리본 (template and version). The 대표 정리본 does not change (O6), so no body moves. Documents of before E3 have a body of the prompt of before the templates (the text of `academic`): E3 records it as the rendition `standard` with the template version `pre-template` (owner decision O21: the owner changes `academic` later, so the old body is not labelled `academic`). The next body run of the document writes a `standard` body. |
| `documents.source_key`, `documents.source_id_version` | Section 5.3. Index on `source_key`. |
| `federation_objects` | New columns `kind` (`document`, `rendition`), `parent_object_id`, `opinions_fetched_at`. The `CHECK` of the outbox kinds needs no change: renditions send no task (Section 5.4 rule 2). |
| Body readers (`claire.db`) | (`object_public_id`, `server_actor_iri`, `first_read_at`, `last_read_at`) for the exact delete list (Section 5.4 rule 4). |
| Remote renditions (`client.db`) | Cache rows: rendition IRI, origin, source key, template, template version, account IRI, name, the +1 counts (`totalItems`, `servers`, `score`), `likes.totalItems`, content hash, body, `ETag`, `fetched_at`, `expires_at`. |
| +1 at the origin (`claire.db`) | `rendition_plus_ones`: (`object_public_id`, `actor_iri`), `actor_host`, `activity_iri`, `created_at`. The counts come from these rows. |
| +1 of own accounts (`client.db`) | `federation_plus_ones`: (`account_id`, `rendition_iri`), `activity_iri`, `state` (`pending`, `sent`, `undone`), `created_at`. The sidecar sends from the client outbox (task kinds `plus_one`, `undo_plus_one`). |
| Likes | `federation_object_reactions` for inbound `Like` (existing). Outbound likes of the account in the tables of the outbound interactions (existing favourites). |
| Opinions (`client.db`) | `federation_opinions` (Section 5.11 C). |
| Fill (`client.db`) | `federation_timeline_items.filled` (0 or 1); `federation_following.filled_at`. |
| Migrations | They add only what is absent, keep unknown data, and complete at the start of the process. No operator step. |

---

## 7. Rejected alternatives

| Alternative | Reason |
| :--- | :--- |
| A different JSON for each reader (Claire servers get the body by signature) | One object ID must have one representation; caches and the same-origin rule break. |
| The body in `content` (FEP-b2b8) | The owner keeps the body on Claire (RD21); Mastodon would show the full body as a status. |
| The 초록 in `summary` | Mastodon shows `summary` as a content warning (R1). |
| `context` for the rendition set | Backfill software reads `context` as a conversation (R6). |
| A server actor mesh that pushes renditions to all Claire servers | It is a relay; relays are ideas, not design ([`RETIRED_PARTNERSHIP_IDEAS.md`](../ideas/RETIRED_PARTNERSHIP_IDEAS.md)). |
| A rendition as a second ingest of the same source | Two documents of one source in one knowledge base double the sources of each entity. |
| Templates that an account writes | Not comparable across servers; a prompt injection path; no fixed meaning on the wire. |
| A capability flag (FEP-844e) or the NodeInfo software name | FEP-844e forbids it when the object properties show the capability (R4). |
| FEP-bad1 history for renditions | History is a sequence of changes in time; renditions are parallel. |
| A token for each rendition | Section 5.8 rule 5. |
| The `Like` as the +1 (Rev. 2) | Owner, Rev. 6: +1 is a separate mark that only Claire servers exchange. A `Like` of a post from Mastodon (for the 초록) is not a choice between renditions. |
| A `Like` with an added Claire type as the +1 | Software that reads the base type `Like` would count it as a like. |
| One +1 for each account and rendition set (a single choice) | Section 5.12 rule 2: approval voting. |
| A +1 count without the limit for one server | Section 5.12 rule 9: accounts that one server makes only for +1 would decide the score. |
| An order of the picker by +1 score | Owner, O16: the order is by template. |
| A limit of 10 marks for one server (Rev. 6) | Owner, O15: the limit is the number of accounts of one server unit, not a small number. |
| A public list of the accounts that gave +1 | Not necessary for the order; it shows the reading choices of people. |
| A scan of all timelines on a schedule (FediFetcher) | Mastodon chose the fetch on open (R12, R16); a scan multiplies requests to other servers. Claire refreshes only its own objects with new activity (Section 5.11 E). |
| Filled posts at the top of the home stream | The timeline orders by storage (fact 17); old posts would look new (Section 5.13 rule 6). |
| Fills for followers, notifications, and lists (FediFetcher) | Section 5.13 rule 9. |
| The header `Mastodon-Async-Refresh` | It belongs to the client API of Mastodon, which Claire does not have. Claire uses the same three values in its JSON (R14). |
| Inbox forwarding of replies (ActivityPub Section 7.1.2) for opinions | It applies only when a reply addresses a collection of this server; Mastodon replies do not address the followers of the replied author. The `replies` collection (Section 5.11 A) gives the thread to each crawling server. |

---

## 8. Security and privacy notes

1. A remote body and a remote opinion are untrusted HTML. Clean them on receipt and show them only in the sandbox frame of the usercontent host; opinions show only text in the page (Section 5.11 D rule 2).
2. The body path and the source index answer only signed server actors, with KS1 controls and quotas.
3. A source index request shows only the key and the asking server (Section 5.4 rule 5).
4. A retract, a private knowledge base, a hidden document, a purged document, a deleted knowledge base, and an account purge make the rendition objects 410 or 404, as for the document object now (FP3-4 privacy triggers), delete the opinions and the +1 rows of these objects, and send `Delete` to the servers that read a body.
5. The opinions frame has `noindex`. The `replies` collection lists only public replies that are not hidden.
6. An anonymous request can start an opinion fetch, but at most one for each object in 15 minutes (Section 5.11 B rule 2).
7. The +1 collection has counts only (Section 5.12 rule 7). The +1 activity goes only to the origin of the rendition.
8. A fill reads only actors that the account chose to follow, once for each follow (Section 5.13).

---

## 9. Phases

| Phase | Content | Depends on | Size |
| :--- | :--- | :--- | :--- |
| E0 | **Done (2026-11-26).** Terms (Section 5.9): catalog values and keys, removal of the retired sections, glossary, design documents. | — | S |
| E1 | **Done (2026-11-26, claire.db v49, contract 1.18).** Quotes always permitted for documents (Section 5.7, quotes 1 to 4) and the migration. | — | S |
| E2 | **Done (2026-11-26).** Citation metadata and `fediverse:creator` (Section 5.7, citation). | — | S |
| E2b | `attributionDomains` in the actor document and in `Update(Person)` (Section 5.7, citation rule 4). State 2026-11-26: the actor document can have it (`Person.fromJsonLd` keeps the term), but Fedify 2.4.6 drops it from each delivered `Update(Person)`; the sidecar route needs a delivery of `Update(Person)` outside Fedify. Details: `RENDITIONS_HANDOFF.md` Section 8. | Fedify support, or a change of the two serialized forms in the sidecar | S |
| E3a | **Done (2026-11-26, claire.db v50).** Templates in each body run: the 대표 정리본 uses `standard` on the server path and in the agent client task (`prompt_template` and `template_version`); `document_renditions`; bodies of before E3 recorded as `standard` with `pre-template` (O21); the old body prompt and `DEFAULT_EXTRACTION_TEMPLATE` removed (Section 5.2 rules 5, 6, 10). | — | S |
| E3b | **Done (2026-11-26).** Additional renditions: the required request field `renditions` of `/ingest`, `/ingest-stream`, and the personal ingest of API v1; the selection in the ingest dialog; `claire ingest --rendition`; `extract_stage(renditions=...)`; `rendition_prompts` and `payload.renditions` of the agent client task (agent bundles 0.0.15); "정리본 추가" as `POST /api/v1/kbs/{kb}/documents/{doc_id}/renditions` with the server API (Section 5.3 rules 1 to 3). | E3a | M |
| E3c | **Done (2026-11-26).** The section "정리본" of the Silvercord page with the picker of the renditions of the document (group "이 문서"), the info line, and "정리본 추가" for the owner of a personal knowledge base; the sub-path `/silvercord/{token}/renditions/{template}` (308 for `standard`, 406 for a request that accepts only ActivityPub until E4); the cross-document view transition; the template choice of the ingest dialog came with E3b (Sections 5.8, 5.10). Not in E3c: the rendition switch in the workspace reader (Section 9.1: not designed). Added after the phase (2026-11-26): prefetch of the other renditions (Section 5.10 rule 5) after measurement 6, and `content-visibility` of long bodies and long opinion trees (rule 6). | E3b | M |
| E4a | **Done (2026-11-26, claire.db v51).** Vocabulary `v2` (`/ns/v2`, the terms of Section 5.5 and 5.12, with `claire:bodyHash` for the hash of a rendition body) and the source identifier with `documents.source_key` and `documents.source_id_version` (Section 5.3 rules 4 to 6; moved from E3 because only published renditions use it). | E3 | S |
| E4b | **Done (2026-11-26, claire.db v52, contract 1.20).** Rendition objects, the `claire:` fields of the object at its ID, `claire:renditions`, the signed body path, and the picker group "같은 출처 · 이 서버" (Sections 5.4 rule 1, 5.5, 5.10 rule 10). Changes from Section 5.5, from the code of Fedify 2.4.6 and of `claire.db`: (1) a delivered `Create` or `Update` has no `claire:` field, because Fedify compacts each activity before it sends it and drops the terms that it does not know; the object at its ID has them, and a Claire server reads the object there (Section 5.4 rule 2); (2) the rendition objects are in the table `federation_rendition_objects`, not in `federation_objects`, whose `UNIQUE (kb_id, document_id)` SQLite changes only with a copy of the table; the number space and the path are the same; (3) a `Like` or `Announce` of a rendition object was not stored in E4b; since 2026-11-26 it is stored with the number of the rendition object (Section 5.6 rule 2), and a quote and the collections work. Not in E4b: the readers of a body and their `Delete` list (E5), `claire:plusOnes` (E9). | E4a | M |
| E5a | **Done (2026-11-26, claire.db v53, contract 1.21).** The origin side: the source index `/ap/sources/{sourceKey}` for the server actor of another server, the record of the body readers, and the exact delete list (one `Delete` of a rendition object, the activity kind `delete_rendition` of vocabulary `v2`, to these readers only, through the outbox of the knowledge federation) (Section 5.4 rules 3 and 4). | E4 | S |
| E5b | **Done (2026-11-26, claire.db v54).** The reader side: a Silvercord page of a document with a source key asks each known server (and each server of a cached rendition) for its source index, at most one time for each server and key in 24 hours; the reads of the index, the objects, the bodies, and the accounts; the cache `remote_renditions`; the deletion of a copy at a 404 or 410, at an index answer without it, and at a `delete_rendition` of its account (Section 5.4 rules 3 to 6). Changes from Sections 5.4 and 6: (1) the reads use the read queue of the knowledge federation (`kf_remote_reads`), so the sidecar and the contract do not change; (2) the cache is in `claire.db`, not `client.db`: it is of the server, and the answer of a read and its row change in one transaction; (3) no `ETag`: the reader compares `claire:bodyHash` of the object and reads the body only when it changed. Tested with two Claire processes and their real sidecars (`tests/test_renditions_exchange.py`, 2026-11-26): the reads, a +1, and the `Delete` list. | E5a | M |
| E5c | **Done (2026-11-26).** Remote renditions on the Silvercord page: the picker group "같은 출처 · 다른 서버", the sandbox frame in the body area with an element-scoped view transition, the address `?rendition={key}`, the link to the origin (Sections 5.8 rule 4, 5.10 rule 4). Change from Section 5.8 rule 4: the frame is not a page of the usercontent host with a render token of the federation client (that token is of one account, and a reader of a Silvercord page can have no account); it is `/silvercord/{token}/renditions/remote/{key}` on the app origin, with the access rules of the document and the same policy (`sandbox`, no script, an opaque origin). The frame shows no images (no media proxy for a reader without an account) and has a fixed height. | E5b | M |
| E6 | **Done (2026-11-26, contract 1.19, client.db v10).** `replies`, `likes`, `shares` on published objects and own notes (Section 5.11 A). | — | S |
| E7a | **Done (2026-11-26, contract 1.23, client.db v11).** Opinion fetch, store, refresh state, the JSON route of the opinions, and the change of the client thread fetch (Section 5.11 B, C, F). Changes from Section 5.11: (1) the sidecar did not read its own origin (until RD30 of `FEDERATION_REMOTE_READING_DESIGN.md`), so the fetch of an own object starts from the replies that this server knows (delivered and fetched; since RD30 also the replies of this server) and walks the reply tree of each one; it reads each quote post without a walk (rule B4); (2) the fetch is a separate queue (`opinion-tasks`, `opinion-objects`), not a task kind of the client thread fetch (rule B9), because the result is of the object and not of an account; (3) the refresh state has a third status `failed` after 3 leases of 5 minutes, for the failure text of rule D8; (4) the limit "500 under one reply" is 16 levels under each start post, with 1000 opinions and `3 × 1000 + 20` requests for one fetch. Added after the phase (2026-11-26): the trigger in the view of `/syndication` (rule B1): a published document that the owner opens in the knowledge base view; and the opinions of a remote rendition (rule C2): the 의견 tab follows the shown rendition, without the controls of this server. | E6 | M |
| E7b | **Done (2026-11-26).** The `의견` tab on the Silvercord page (Section 5.11 D1 to D3, D6 to D9): the JSON on the first open of the tab (`silvercord.opinions_url`), the counts, the reply tree with 4 levels and the link "원 서버에서 계속 (N개 더)", the group "인용", the text as text nodes and `http`/`https` links, a closed content warning, `data-nosnippet`, a `<progress>` with a text while a fetch runs (at most 10 requests, each after `retry` seconds), one polite message when the fetch ends, the mark "새 의견", an alert with "다시 시도" after a failure, the notice of a document that is not published, and the text with the page address for a reply from another server (rule D9, first sentence). Moved: "서식과 첨부 보기" (rule D2, last sentences) to E7e, and "이 정리본에 답글" for a signed-in actor (rule D9, second sentence) to E7d. | E7a | M |
| E7c | **Done (2026-11-26).** Hide by the author of the object and the server admin (D5): `POST /api/federation/opinions/hide`, the hidden opinions with a control "다시 보이기" for them, no hidden opinion in the counts, the page, and the `replies` collection. The remote `Delete` of an opinion by its author (C4). The delete of the opinions of a gone object (C4) is a run each hour in the `api` process, not a step of each retract: a retract, a gone rendition, a private knowledge base, and a purge all end in a gone object, and the routes give nothing for a gone object before the run. | E7b | S |
| E7d | **Done (2026-11-26).** The like control of the post (Section 5.6 rule 4), reply, like, boost, and quote of an opinion (D4), and "이 정리본에 답글" for a signed-in actor (D9). Changes: (1) the like of the post was first a local reaction row without an activity; since RD30 of `FEDERATION_REMOTE_READING_DESIGN.md` (2026-11-26) it is the favourite of the item of the object, a `Like` as from another server; (2) an opinion becomes an item of the timeline of the account that is not in the home stream (`POST /api/federation/opinions/item`), so that the routes of the federation client work on it without a change; (3) the note route takes `in_reply_to_object`, the address of a published object of this server, for a reply that is not to an item; (4) the tab also shows the notes of this server that reply to an opinion. Added after the phase (2026-11-26): the notification of a reply of an account of this server (O22), and, with RD30, the same controls on each opinion of the tab, also on a reply of this server. | E7c | M |
| E7e | **Done (2026-11-26, client.db v12).** "서식과 첨부 보기": the formatted body and the attachments of one opinion in a sandbox frame in a modal dialog (D2). Change from rule D2: the frame is `/silvercord/{token}/opinions/{key}` on the app origin, not a page of the usercontent host, with the same reason as phase E5c (the render token of the usercontent host is of one account, and a reader can have no account). The frame has no script, no resource, and an opaque origin; an attachment is a link, not an image (no media proxy for a reader without an account). | E7c | S |
| E8 | **Done (2026-11-26).** Scheduled refresh (Section 5.11 E): the hourly run of E7c asks one time each day for a fetch of each own object with a delivered reply, a like, or a quote request in the last 7 days, the newest activity first, 200 objects at most; the conditions of rule B2 apply. | E7c | S |
| E9a | **Done (2026-11-26, claire.db v55, contract 1.22).** +1 at the origin: the activities `plus_one` and `undo_plus_one` of vocabulary `v2` (builders, validators, fixtures), their handlers in the inbox journal (`not_a_rendition`, `rate_limited` at 600 marks of one domain in one hour), the table `rendition_plus_ones`, the counts with the limit of 1,000 for one server, `claire:plusOnes` on the object at its ID, the collection `/ap/objects/{id}/plus-ones`, and the deletion of the marks with the object (Section 5.12 rules 5 to 9, 12, 14). | E4 | S |
| E9b | **Done (2026-11-26, claire.db v56).** +1 of the accounts of this server: `POST /api/renditions/plus-one` for a rendition of this server (a row at the origin, no activity) and for a cached rendition of another server (`account_plus_ones` and the activity `claire:PlusOne` or its `Undo` through the outbox of the knowledge federation, signed by the account actor); the refusals `own_rendition`, `rate_limited` (60 marks of one account in one hour), `blocked_domain`; the cached counts of the origin; the control and the counts on the Silvercord page (Sections 5.10 rule 2, 5.12 rules 2 to 4, 10 to 13). Change from Section 6: the marks of the accounts are in `claire.db`, with the outbox task in one transaction. Added after the phase (2026-11-26): the scores in the list of the published documents of `/syndication` (rule 13), and the account purge sends the `Undo` of each mark (rule 14, second sentence); the server actor signs it, because the keys of a purged account go with the purge, and a purge that does not inform other servers sends none. | E9a, E5 | M |
| E10 | **Done (2026-11-26, contract 1.24, client.db v13).** Fill after a follow: the trigger at the `Accept`, the `featured` and `outbox` reads with the key of the account, `fill-objects`, the copy of a fill of another account, the domain limit, and the view of an actor with filled posts (Section 5.13). Changes: (1) the queue is its own table with the endpoint `fill-tasks`, not a kind `fill_follow` of `client-tasks` (rule 8): the kinds of the client outbox are a `CHECK` of its table, and a fill has its own state; (2) rule 3 "replies to actors that the account does not follow": the reply target is a post of a followed actor when its address is under the address of that actor, or when the account has it as an item of that author; (3) the view of an actor is `GET /api/federation/timeline?author=` and a button of each accepted follow in the follow list of `/syndication`. | — | S |

E0, E1, E2, E3, E6, and E10 do not depend on each other.

### 9.1 Frontend coverage

The design has backend and frontend parts. The backend parts (wire forms, data, sidecar tasks, limits) are complete for implementation. The Silvercord view has a visual prototype ([`prototypes/document_renditions_prototype.html`](prototypes/document_renditions_prototype.html), Rev. 6). The other screens are rules only; each needs a prototype before its frontend work.

| Surface | In this design | Not yet designed |
| :--- | :--- | :--- |
| Silvercord view (`share.html`) | Prototype (Rev. 6). 초록 section, rendition bar and picker groups, +1 control, rendition switch, remote rendition frame, prefetch, long bodies (5.10); `의견` tab: tree, text and frame, counts and the like of the post, actions, hide, fetch states, reply from another server (5.11 D); citation metadata (5.7); sub-paths (5.8) | Measurement with real data; the graph panel stays as in the Silvercord design |
| Ingest (`/ingest`, Telegram bot, CLI, MCP) | Template selection for each request, "정리본 추가" in the document menu (5.3) | Form layout; the bot and CLI syntax; the cost display before a run |
| `/syndication` knowledge base view (stage 3) | Term changes (5.9); removal of the quote policy dialog of documents (5.7); +1 counts of own renditions (5.12 rule 13) | Renditions and opinion counts in the list of published documents; the publish dialog with the renditions that go out |
| `/syndication` client view | The view of an actor with filled posts and the label "팔로우 전 글" (5.13 rule 6) | Layout of the view of an actor |
| Workspace reader and context panel | — | Rendition switch inside the workspace |
| Account home timeline | — | A document post with its renditions |
| Console | Removal of `/settings/connections` and `/settings/outbound` (5.9) | — |
| Notifications | No notification for +1 (5.12 rule 13) | A new opinion or a like as a notification kind |
| Catalog | The rule "keys in English and Korean from the start" (5.2, 5.10) | The list of keys |

Tests (global rules: unit tests only for module interfaces, input and output, and calls to external libraries; at least one E2E test for each new feature):

- E2E: a rendition switch in the picker and a +1 on a Silvercord page; the `의견` tab with fetched opinions and the like of the post; the view of an actor after a follow with filled posts (Playwright through `scripts/e2e.sh`); the vocabulary fixtures of `v2` (with `claire:PlusOne`); a DrFed scenario for the rendition object, the body path with a signed server actor, the `replies` collection, an inbound `Like`, and a fill from a remote `featured` and `outbox`; two development servers for the source index, the opinion fetch, and a +1 with its `Undo` (acceptance path as KA1 to KA5).
- Unit: the template set (exists), the source key normalization (input and output), the body representation builder and validator, the `replies` collection pages, the +1 validator and the score with the limit of one server, the internal API contract fixtures.

---

## 10. Open measurements

1. Mastodon 4.7: show of an `Article` with `content` of the RD21 form and the new `claire:` properties.
2. Mastodon 4.5 or later: a quote of a Claire document with the automatic stamp.
3. Mastodon 4.5 or later: a crawl of the `replies` collection of a Claire object (do replies from a third server appear on Mastodon).
4. Mastodon 4.7: the answer to an unknown activity type (`["Activity", "claire:PlusOne"]`) that reaches its inbox by error (expected: ignored, no error loop).
5. `@fedify/backfill` 2.4.6: pages of `replies` and `context` collections (R15).
6. Chrome: use of a prefetched answer with `Cache-Control: private, no-store` (Section 5.10 rule 5). **Measured 2026-11-26** (headless Chromium 153 of Playwright, a list rule with eagerness `immediate`, then a navigation): the navigation used the prefetched answer (`deliveryType` `navigational-prefetch`) for a visitor without `Cache-Control` and for a signed-in owner with `Cache-Control: private, no-store`. A desktop Chrome was not measured.
7. The `outbox` of GoToSocial, Misskey, and Akkoma without and with a signature (Section 5.13 rule 4).
8. The write time of one ingest in `claire.db` on the production host, and the number of accounts at which a peak hour fills the writer. The result replaces the estimate 1,000 of `PLUS_ONE_SERVER_LIMIT` (Section 5.12 rule 9).

---

## 11. Owner decisions

| # | Question | Decision (2026-11-25) |
| :--- | :--- | :--- |
| O1 | Number of templates | A: three in total. |
| O2 | Texts of the templates | `concise`: written by the agent from a copy of the current prompt, saved in the code. The third template (first "수학 제외", then "학문 특화") is an exact copy of the current prompt; the owner changes it later. |
| O3 | Quote policy of notes | A: notes keep the four policies. |
| O4 | Files without a URL in rendition sets | A: not in the first version. |
| O5 | Favourites | The ActivityPub `Like` as it is, for posts. Rev. 6: +1 is separate (O13). |
| O6 | Change of the primary rendition | It does not change. Since Rev. 9 it is always the rendition 기본 (O19). |
| O7 | English word for a note | Recommendation: "Post". |
| O9 | Recent posts of a newly followed actor | Rev. 3: an idea. Rev. 6: in this design ("이번에 같이 고려합시다"), Section 5.13; the idea document is removed. |
| O10 | Term for 에디션 | 정리본 (English "rendition"). |
| O11 | Label of `academic` | 학술. |
| O12 | 요약 → 초록 | The Korean prompts and texts call the document summary 초록. Implemented in commit `0dc44a6d`. |
| O13 | +1 | A mark of a rendition that only Claire servers exchange (Section 5.12). |
| O14 | Label of the template; term of the 대표 정리본 | 기본, identifier `standard` (not 상세: the body itself is 상세). 대표 정리본 accepted (Rev. 8); since Rev. 9 it is always the rendition `standard`. |
| O15 | Limit of one server in the +1 score | The number of accounts that one Claire server can serve now, estimated by the agent: 1,000 (Section 5.12 rule 9). |
| O16 | Order of the picker | Not by score. By template: 기본, 간결, 학술 (Section 5.10 rule 10). |
| O17 | 학술 and 기본 with the same text | 학술 stays an exact copy of the current prompt. 기본 changes: "원문 테이블 미사용", "수식은 대표 공식만 사용" (Section 5.2). Implemented in the template module. |
| O18 | Tables in the template 간결 | No tables of the source, as in 기본 (Section 5.2 rule 11). Implemented in the template module. |
| O19 | Which rendition is the 대표 정리본 | Always the rendition 기본 (owner: "대표 정리본은 간결 정리본일 수 없습니다"). |
| O20 | Formulas in the template 간결 | Representative formulas only, as in 기본 (Section 5.2 rule 12). Implemented with the template structure of Rev. 11. |
| O21 | Label of the bodies of before E3 (2026-11-26) | The rendition 기본 (`standard`), not 학술: the owner changes the template 학술 later (owner: "E3 이전 적재 문서는 학술이 아닌 기본 정리본으로 기록하세요"). The template version is `pre-template`. |
| O22 | Notification for a reply of an account of the same server (2026-11-26) | The author gets it, as for a reply of another server (owner: "같은 서버 계정의 답글도 알림을 받아야합니다"). Implemented: an item with `mention` 1 and `ap_mention` (`FEDERATION.md` Section 25.3 rule 13); since RD30 the delivery of the reply makes them, as for another server. |

Open:

| # | Question | State |
| :--- | :--- | :--- |
| O8 | Reputation of a publisher | **On hold** (2026-11-25). The score between renditions is +1 (Section 5.12). Likes and +1 do not feed the reputation of an account until a decision. |

---

## 12. Modern Web Guidance

Section 5.10 lists the guides and the rules that came from them; Section 5.11 D rules 2, 7, and 8 use the `accessibility` guide. The prototype follows the same rules. The search for MathML display gave no guide, so this design does not use a guide for formulas.

## 13. Sources

- Mastodon documentation, ActivityPub: <https://docs.joinmastodon.org/spec/activitypub/>
- Mastodon documentation, configuration, Fetch All Replies: <https://docs.joinmastodon.org/admin/config/#fetch-all-replies>
- Mastodon documentation, statuses (context) and async refreshes: <https://docs.joinmastodon.org/methods/statuses/>, <https://docs.joinmastodon.org/methods/async_refreshes/>
- Mastodon source code, branch `main` (2026-11-25): `app/workers/activitypub/fetch_all_replies_worker.rb`, `app/services/activitypub/fetch_all_replies_service.rb`, `app/models/concerns/status/fetch_replies_concern.rb`, `app/serializers/activitypub/note_serializer.rb`, `app/controllers/activitypub/replies_controller.rb`, `app/controllers/activitypub/outboxes_controller.rb`, `app/services/activitypub/fetch_featured_collection_service.rb`, `app/services/activitypub/process_account_service.rb` at <https://github.com/mastodon/mastodon>
- Mastodon issues on the fill of a followed account: <https://github.com/mastodon/mastodon/issues/34>, <https://github.com/mastodon/mastodon/issues/9525>
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
