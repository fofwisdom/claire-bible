# Server Partnerships: Exchange of Shared Knowledge Between Claire Bible Servers (Idea I2)

- **Document Identifier**: `DESIGN-SERVER-PARTNERSHIP-20261116`
- **Status**: Draft, Rev. 3. The owner direction of Section 2 is locked (CT31 to CT33 of the shared knowledge base design, 2026-11-16). All other content is a **proposal**. Section 4.5 has a review that can change Sections 5 to 10 (PQ10, PQ11). Section 14 has the open questions; the owner answers them later.
- **Date**: 2026-11-16
- **Base commit**: `e679bf90`
- **Revisions**:
  - Rev. 1 (2026-11-16): first proposal with BookWyrm as the reference.
  - Rev. 2 (2026-11-16): review of seven more projects from the owner's research (NeoDB, Inventaire, PeerTube, Funkwhale, Mobilizon, ForgeFed and Forgejo, Lemmy). New: subscription and partnership as two levels, knowledge bases as actors, the `Offer` pattern of ForgeFed, remote collaborators with grants, authority identifiers of entities, the inbox journal, rules for copies. The owner confirmed the single mode rules (Section 13).
  - Rev. 3 (2026-11-16): interoperation review for each kind of software (Section 4.4): no special communication for software of other domains. The rule for the compact public address (Lemmy) is removed. Fedify baseline (Section 3.4). Review of the need for a server partnership and of the term "directory" (Section 4.5).
- **Related Documents**:
  - [`SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md`](SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md) (shared knowledge bases, themes, contributions; this design extends them across servers)
  - [`FEDERATION_REMOTE_READING_DESIGN.md`](FEDERATION_REMOTE_READING_DESIGN.md) (§4.2 ideas I1 and I2)
  - [`ACCOUNT_IDP_AND_FEDIFY_SIDECAR_INTEGRATION_DESIGN.md`](ACCOUNT_IDP_AND_FEDIFY_SIDECAR_INTEGRATION_DESIGN.md) (sidecar, keys, internal API)
  - [`FEDERATION.md`](../implementation/FEDERATION.md) (server actor §4.2, inbox event contract §5.7, switch and readiness checks §7)
  - [`MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md`](MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md) (server modes §4, first admin setup §8.4, S9)

Evidence tags follow [`PRINCIPLES.md`](../PRINCIPLES.md) F1: **[code]**, **[decision]**, **[proposal]**, **[external]**, **[unverified]**.

---

## 1. Goal

1. Shared knowledge bases grow across servers through partnerships between servers, not only inside one server.
2. Servers show the themes and the shared knowledge bases of their partners. The root page in network mode is the place for this information.
3. Reviewers curate from partner servers. Accounts contribute to shared knowledge bases of partner servers. When both servers allow it, automatic contributions also go across servers.
4. Each server keeps its own decisions: merge of entities, subject judgement, review, reputation. No server is an authority over another server.

---

## 2. Owner Direction (2026-11-16)

| Item | Direction |
| :--- | :--- |
| Aim | Shared knowledge bases aim at partnerships between servers. |
| Root page | In network mode, the root page shows the partnership information. |
| Reference | BookWyrm is a good reference for partnership interaction in ActivityPub: it shares book and author data between servers. The owner added a research list of similar projects (NeoDB, Inventaire, PeerTube, Funkwhale, Mobilizon, Gancio, Forgejo with ForgeFed, Lemmy). A reference needs a clean-room implementation because of the licenses. |
| Default | When the ActivityPub functions are complete, partnership communication is on by default, also in single mode. |
| Single mode | The admin sets the USERNAME (the handle) at the first setup. Single mode shows no root page of network mode: `/` sends the request to `/@{username}/` of the admin. This is an architecture change, and the rules change with it (owner, confirmed in Rev. 2). |
| Automatic contribution | The admin turns it on or off for the server in the ActivityPub federation menu. |
| Later work | Merge decisions differ between servers; federation of merge decisions waits for a later design. Links between duplicate knowledge wait until there is a real need. |

Production state (owner, 2026-11-16): the production server is in network mode, and its admin has a USERNAME. Thus Section 13 needs no setup step there.

---

## 3. Clean-Room Rules

### 3.1 License Facts

Read on 2026-11-16 from license files, READMEs, and documentation footers only.

| Project | Code license | Specification or data | Effect for Claire (MIT) |
| :--- | :--- | :--- | :--- |
| Claire Bible | MIT **[code]** `LICENSE.md` | — | — |
| BookWyrm | Anti-Capitalist Software License v1.4 (limits on kinds of users) | — | No code or text |
| NeoDB | AGPL-3.0 | — | No code or text |
| Inventaire | AGPL-3.0 | Data: CC0 (Wikidata-derived parts keep the Wikidata terms) | No code or text; data identifiers can be used |
| PeerTube | AGPL-3.0 | — | No code or text |
| Funkwhale | Not confirmed **[unverified]** | — | Treated as copyleft: no code or text |
| Mobilizon | AGPL-3.0 or later | Documentation: CC BY-SA 4.0 | No code or text |
| Forgejo | GPL-3.0 or later (v9 and later; MIT up to v8) | ForgeFed specification: CC0, and OWFA 1.0 from 2025-06-18 | No Forgejo code. The ForgeFed specification can be followed directly. |
| Lemmy | AGPL-3.0 | — | No code or text |
| FEPs (Fediverse Enhancement Proposals) | — | CC0 (repository-wide statement) | Can be followed directly |

### 3.2 Rules

1. No source code, template, schema, migration, test, or documentation text of these projects enters Claire Bible. The copyleft licenses (AGPL, GPL) and the BookWyrm license do not fit the MIT license of Claire.
2. This design takes only ideas and protocol facts from public documentation. Section 4 lists each idea, its project, and the Claire design in our own words.
3. Specifications with a public-domain or patent-promise license (ForgeFed: CC0 and OWFA 1.0; FEPs: CC0) can be followed directly. Claire still writes its own text and claims no conformance (ForgeFed says that it is "still under construction").
4. Persons who write Claire code for this design do not read the source code of these projects. They work only from this design and from the specifications.
5. Interoperation with these projects is not a goal. The exchange is between Claire Bible servers (idea I2). Section 4.4 says which software Claire exchanges with through the existing general federation. Standard base types (Section 8.1) let other software show Claire objects.
6. When a later revision takes a new idea from a project, it adds a row to Section 4 with the page and the date.

### 3.3 Pages Read (2026-11-16, documentation and license only)

| Project | Pages |
| :--- | :--- |
| BookWyrm | `FEDERATION.md`, `LICENSE.md`; `docs.joinbookwyrm.com`: federation, activitypub, admin-settings, system |
| NeoDB | `FEDERATION.md` (pointer), `neodb.net/internals/activitypub`, `neodb.net/servers/`, repository README |
| Inventaire | `data.inventaire.io`, `api.inventaire.io`, `wiki.inventaire.io` (Entities data, Technic), repository READMEs |
| PeerTube | `docs.joinpeertube.org/api/activitypub`, `docs.joinpeertube.org/admin/following-instances`, `LICENSE` |
| Funkwhale | `docs.funkwhale.audio/developer/federation/index.html`, documentation index |
| Mobilizon | `docs.mobilizon.org` (ActivityPub, instance federation), repository README |
| ForgeFed, Forgejo | `forgefed.org/spec` (parts of Sections 6 to 8 not reached), `forgefed.org`, Forgejo FAQ, Forgejo federation post of 2023-01-10 |
| Lemmy, Fedify | `join-lemmy.org/docs/contributors/05-federation.html`, `fedify.dev/tutorial/threadiverse`, repository README; FEP repository README. FEP-1b12 itself could not be read **[unverified]**. |

### 3.4 Fedify Baseline

The sidecar uses Fedify 2.4.5 (`deno.json` pins `~2.4.5`), the latest release on JSR on 2026-11-16 **[code]** FEDERATION.md §9.3. Work for this design uses the latest Fedify release at the time of the work. A fact about an older Fedify version in a tutorial or a report does not apply.

---

## 4. Reference Review

### 4.1 Check of the Research List

| Claim of the research list | Result |
| :--- | :--- |
| NeoDB uses a dedicated relay for catalog sharing between instances. | Partly confirmed. A default relay of the NeoDB developers sends public activities and catalog information between compatible NeoDB servers. It is optional; an operator can turn it off. It is not only for catalog data. |
| Inventaire has an ActivityPub shared inbox at `/activitypub/shared-inbox`. | Not confirmed. The Inventaire wiki says that its fediverse part is an experiment and that users and groups do not federate yet. Inventaire keeps book data on one "entities host" so that the data is not copied. |
| Funkwhale stores received payloads before it processes them. | Confirmed. |
| Lemmy wraps received activities of a community in `Announce` for its followers. | Confirmed (Lemmy documentation). |
| The other rows (PeerTube, Mobilizon, Gancio, ForgeFed) | Confirmed in their main points, except Gancio, which this review did not read. |

### 4.2 Ideas and Their Use

| No. | Project | Idea (own words) | Claire design |
| :--- | :--- | :--- | :--- |
| B1 | BookWyrm | Servers share book metadata to make a networked database. | Partners share a directory of themes and shared knowledge bases (Section 7). |
| B2 | BookWyrm | Objects have custom types and origin URLs; a receiver keeps a local copy and matches it to existing objects when it can. | Origin IRIs on all objects; the receiver matches entities by its own rules (Section 9.4). |
| B3 | BookWyrm | Data sources with an admin-set priority for search; to block incoming search gives more duplicates. | Partners are search sources; the page "Make a shared knowledge base" shows similar knowledge bases of partners first (Section 7.3). |
| B4 | BookWyrm | A book page shows reviews of the same book from connected servers. | A theme page shows knowledge bases of the same subject from partners (Section 7.2). |
| B5 | BookWyrm | A server actor signs GET requests; a server can require signed GET. | The server actor signs all partnership requests; partnership collections need a signed GET (Section 12). |
| B6 | BookWyrm | Admin list of federated servers with block. | Partner list with level, scopes, and state (Section 5.5). |
| B7 | BookWyrm | Custom types reach other software as `Article` or `Note`. | Standard base types (Section 8.1). |
| B8 | BookWyrm | The receiving server decides what it shows. | Each server decides what it shows and keeps (Sections 9 and 10). |
| B9 | BookWyrm | Custom types without a published JSON-LD context. | Claire publishes its context from the first version (Section 8.1). |
| P1 | PeerTube | The platform actor (`Application`) follows other platforms; the followed admin can require manual approval or refuse all followers. | The handshake between server actors (Section 5.3). |
| P2 | PeerTube | An admin can follow the platforms of an index list automatically. | An index list gives suggestions only; no partnership starts without an admin decision (Section 5.4). |
| P3 | PeerTube | A following platform receives metadata with links to the files, not the files. | The directory carries metadata and signatures, not documents (Section 7.1). |
| P4 | PeerTube | A mirror server tells the origin with a `CacheFile` object that it holds a copy; the origin controls who may mirror (`nobody`, `followings`, `anybody`). | A holder tells the origin that it holds a copy; scopes control who may copy (Sections 8.2, 9.2). |
| P5 | PeerTube | A channel is a `Group` actor that several accounts manage. | A shared knowledge base as an actor (Section 8.2). |
| M1 | Mobilizon | Instances subscribe to each other through an internal relay actor; the other admin approves; the subscription is one way; only public content created after the subscription is sent. | Subscription (one way) and partnership (both ways) as two levels; a full fetch at the start (Sections 5.1, 7.1). |
| M2 | Mobilizon | Group members have roles from "invited" to "administrator". | Same idea as the roles of a shared knowledge base (shared knowledge base design §6.2). |
| L1 | Lemmy | A community is a `Group`; it wraps each received activity in `Announce` and sends it to its followers. | A knowledge base actor sends revisions and decisions to its followers (Section 8.2). |
| L2 | Lemmy | Moderators of other servers can be added to a community; an admin removal of content in a remote community has effect only on the local server. | Remote collaborators with grants; no remote moderators in v1; a local removal never changes the origin (Sections 10.2, 9.3). |
| F1 | ForgeFed | To open an issue on a remote repository, the author sends `Offer` to the tracker actor; the tracker answers `Accept` with the new IRI in `result`, or `Reject`; the answer can wait for human review. | The contribution across servers (Section 10.1). |
| F2 | ForgeFed | When the target accepts, it hosts its own copy and controls it; the sender is only the author. Forks are new actors with copies whose `origin` points to the original. | Rules for copies (Section 9.1). |
| F3 | ForgeFed | Roles (`visit`, `report`, `triage`, `write`, `maintain`, `admin`) are given with `Grant` (context = resource, target = person, object = role, end time) and taken back with `Revoke`; the grant IRI is the capability that later activities carry. | Remote collaborators (Section 10.2). |
| W1 | Funkwhale | Each pod stores received payloads in the database before it processes or delivers them, to debug, send again, or process again. | The inbox journal (Section 11). |
| W2 | Funkwhale | A library is an actor and a collection; a follow of a private library needs the approval of its owner; the service actor of a pod is the authority for the objects of its domain. | Knowledge base actors; the origin is the authority (Sections 8.2, 9.1). |
| N1 | NeoDB | A remote review lands on an existing item that shares an external ID (ISBN, Open Library, Goodreads). | Authority identifiers of entities (Section 9.4). |
| N2 | NeoDB | Follow the origin: lists keep possibly stale mirrors and do not serve them again as ActivityPub. | A holder never serves a remote object as its own (Section 9.1). |
| N3 | NeoDB | An optional relay sends public activities between servers. | Later work for many partners (Section 15). |
| I1 | Inventaire | Entities have `wd:` (Wikidata) and `inv:` (local) identifiers; data is CC0. | Authority identifiers of entities (Section 9.4). |

### 4.3 Common Patterns

The projects agree on these points. The design follows them.

1. **Server actors follow each other with approval** (PeerTube, Mobilizon): Section 5.
2. **A container is an actor with followers** (Lemmy communities, PeerTube channels, Mobilizon groups, Funkwhale libraries, ForgeFed trackers): Section 8.2.
3. **A submission to a remote container is an offer that the container accepts or rejects** (ForgeFed): Section 10.1.
4. **The origin is the authority; a receiver keeps its own copy and marks the origin** (ForgeFed, Funkwhale, NeoDB, PeerTube): Section 9.1.
5. **Shared external identifiers make matching across servers possible** (Inventaire, NeoDB): Section 9.4.
6. **Received payloads are kept before processing** (Funkwhale): Section 11.

The parser of one project (Lemmy) is stricter than the others about the form of the public address. This is not a rule for Claire; Claire follows the forms that the specifications allow.

### 4.4 Interoperation by Kind of Software

The patterns of Section 4.3 are design references only. Communication is designed only where an exchange with a knowledge platform makes sense.

| Software | Domain | Exchange with Claire |
| :--- | :--- | :--- |
| Claire Bible | Knowledge | This design (idea I2) |
| Mastodon, Misskey, and other microblog software | Posts | Existing general federation: the account actor publishes documents as `Article` (FP3); remote reading receives `Note` and `Article` (FP4); content enters a knowledge base only by an ingest of the owner. No new protocol. |
| Blog software with ActivityPub (for example WriteFreely, the WordPress plugin, Ghost) | Articles | Same as the line above: their `Article` objects are sources for remote reading and ingest. No new protocol. **[unverified]** the federation state of each product. |
| BookWyrm, NeoDB | Book and media catalogs, reviews | Their reviews reach other software as `Article` or `Note` (documented by both), so remote reading covers them. Claire does not read their catalog types. Later: their book identifiers (ISBN) as authority identifiers (Section 9.4). |
| Inventaire | Book data (Wikidata-centered, CC0) | No ActivityPub exchange (its federation is an experiment). Later: its CC0 open data interface as an authority source for book entities, outside ActivityPub. |
| Lemmy and other forum software | Forums | Their posts reach other software as `Page` and `Note`; remote reading covers them. No special handling. A follow of a knowledge base actor from forum software is idea I1, outside this design. |
| Forgejo (ForgeFed) | Code forges | No exchange. Only the specification patterns (`Offer`, `Grant`) are used. |
| Funkwhale | Audio | No exchange. Pattern only (inbox journal). |
| PeerTube | Video | No exchange (owner, 2026-11-16). Pattern only (server follows, mirror notice). |
| Mobilizon, Gancio | Events | No exchange (owner, 2026-11-16). Pattern only (server subscriptions). |

### 4.5 Review After Rev. 2 **[proposal, PQ10, PQ11]**

Questions of the owner (2026-11-16): is the partnership controlled only by the admin; is it necessary when themes already share ingested documents; or is it a relation of deeper trust that shares ingested documents at the level of the server, beyond the scope of one person?

1. **Control.** In Rev. 2, yes: only the admin requests, accepts, changes, and ends a subscription or a partnership (Section 5.3). Account-level control stays with moderators and accounts: moderators give roles (also to remote accounts, Section 10.2), accounts contribute.
2. **What exists without a partnership.** On one server, shared knowledge bases and themes already share documents; a partnership adds nothing there. Between servers, the account actor already publishes documents, and another account can read and ingest them. That path is personal: it runs a new LLM extraction and loses the credit, the origin, and the revisions.
3. **What the exchange of this design adds.** Copies with their extraction (no LLM call), credit, origin, and automatic revisions; contributions into a shared knowledge base of another server; themes across servers; automatic contributions across servers.
4. **Which of these needs a server-level agreement.** The first three are already under control without one: the content is public, the reviewers of each shared knowledge base decide each contribution and curation, and the admin blocks bad domains. This is how the reviewed projects work. Only an exchange with no decision of a person for each document needs trust at the level of the server: automatic contributions across servers, and an optional rule that accepts the approvals of the reviewers of a partner as local approvals.
5. **Proposal (PQ10).** Two levels in place of Section 5:
   - **Open exchange** (on by default with `partnerships`, no agreement): public collections of shared knowledge bases readable by signed GET, themes across servers, curation, contribution offers, remote collaborators. Controls: reviewers, pending limits, domain blocks.
   - **Partnership** (admin only, both admins agree): automatic contributions across servers, the optional acceptance of the approvals of the partner, higher limits. This is the relation of deeper trust at the level of the server.
6. **"Directory" and themes (PQ11).** They are not the same:
   - The "directory" of Rev. 2 is the list of the shared knowledge bases of one server. This list already exists: the collection `/kb` (shared knowledge base design §7). Thus "directory" is not a new concept: a server follows or reads the `/kb` collection of another server.
   - A theme is a collection of knowledge bases with the same subject, from the judgement of one server. Both follow the collection contract (Mastodon Collections), but they select different items: `/kb` by origin server, a theme by subject.
   - The word "directory" also has another meaning in Claire: the public directory on the root page of network mode (namespace design D4). Thus the word "directory" is removed from this design; Section 7 uses "the `/kb` collection of the other server".
   - Themes are not exchanged: each server computes its own themes and links to the knowledge bases of other servers.

---

## 5. Subscription and Partnership

### 5.1 Two Levels

| Level | Direction | Content | Agreement |
| :--- | :--- | :--- | :--- |
| Subscription | One way (A reads B) | The directory of B (Section 7) | The admin of B approves the follow of A (M1, P1) |
| Partnership | Both ways | The scopes of Section 5.2 | Both admins agree; each decides once (Section 5.3) |

A subscription lets a server show the themes of another server on its root page without an exchange of content. A partnership is necessary for each exchange of content.

### 5.2 Scopes of a Partnership

| Scope | Content | Needs |
| :--- | :--- | :--- |
| `directory` | Themes, metadata and signatures of shared knowledge bases, in both directions | — |
| `curation` | Reviewers copy visible documents of shared knowledge bases of the partner | `directory` |
| `contribution` | Accounts send documents to shared knowledge bases of the partner; the reviewers of the partner decide | `directory` |
| `auto` | Automatic contributions to shared knowledge bases of the partner | `contribution`, and the switch `kb_auto_contribution` is `on` on both servers |

Each admin sets the scopes that its server offers to each partner. The effective scopes are the scopes that both servers offer. This is the negotiation: no server gets more than the other server gives.

### 5.3 Handshake

1. The actors are the server actors (`Application`, `claire@{handle host}`, `manuallyApprovesFollowers` true; **[code]** FEDERATION.md §4.2).
2. **Subscription.** The server actor of A sends `Follow` to the server actor of B. The admin of B accepts or rejects it.
3. **Partnership.** The `Follow` of A has a `claire:Partnership` object with the offered scopes. When the admin of B accepts, B sends `Accept` and its own `Follow` with its offered scopes, and A accepts it automatically because A made the request. Thus each admin decides once.
4. A change of offered scopes: `Update` of the `claire:Partnership` object. Both servers compute the effective scopes again.
5. End: `Undo(Follow)` from either server. A partnership becomes a subscription or ends, as the `Undo` says.
6. A domain block (existing function) ends a subscription and a partnership and refuses new requests.
7. Each request, accept, scope change, and end needs a recent sign-in of the admin and writes `admin_audit`.

### 5.4 Discovery

1. An admin enters a domain. The server reads its NodeInfo and the server actor and shows the software, the version, and the scopes that the other server can offer.
2. The directory of a partner lists its partners. An admin can also add the URL of an index list of Claire servers (P2). Both give suggestions only; nothing starts a subscription or a partnership without an admin decision.

### 5.5 Admin Console `/admin/federation`

| Area | Content |
| :--- | :--- |
| Switches | `federation`, `partnerships`, `kb_auto_contribution` (Section 12) |
| Servers | Domain, software, version, level (subscription or partnership, each direction), effective scopes, counts of themes and shared knowledge bases, last synchronization, errors |
| Requests | Incoming requests with the level and the scopes that the other server offers |
| Suggestions | Partners of partners, index lists |
| Offered scopes | For each partner |
| Journal | The inbox journal of Section 11, with replay of a failed activity |

---

## 6. Authority

1. The origin server is the authority for its objects: its knowledge bases, its documents, and the decisions of its reviewers (W2, F2).
2. A server never changes an object of another server. It sends offers and notices; the origin decides.
3. A local action on a copy (hide, remove, stop updates) has effect only on the local copy (L2).

---

## 7. Directory and Cross-Server Themes

### 7.1 Synchronization

1. The origin sends `Create`, `Update`, and `Delete` of each `claire:KnowledgeBase` (shared knowledge bases only) to its subscribers and partners: label, description, knowledge type, counts, the signature of §8.2 of the shared knowledge base design, and the IRI. No documents (P3).
2. At the start of a subscription, the subscriber fetches the collection of shared knowledge bases of the origin once with a signed GET, so that it does not wait for new activity (M1 sends only new content; Claire needs the full directory).
3. The subscriber keeps these records in a store of remote knowledge bases. A remote knowledge base is not a partition of the local `claire.db` and has no documents until a curation or a contribution.
4. Public personal knowledge bases are not in the directory **[proposal, PQ5]**.

### 7.2 Cross-Server Themes

1. The subject judgement of each server includes the signatures of remote shared knowledge bases. A remote knowledge base can be a member of a local theme.
2. Each server computes its own themes. The same subject can have different theme numbers on different servers. A theme number never leaves its server as an identifier.
3. The theme page shows local and remote knowledge bases. A remote knowledge base links to its origin.

### 7.3 Root Page (Network Mode)

1. Themes first, with the counts of local and remote shared knowledge bases.
2. A section "Partner servers": name, domain, level, effective scopes, number of shared knowledge bases and themes.
3. Recent activity across servers: new shared knowledge bases of partners, contributions that partners approved from accounts of this server, curations.
4. The page "Make a shared knowledge base" shows similar local and remote shared knowledge bases before the account makes a new one (B3).

Single mode shows no root page (Section 13).

---

## 8. Objects and Actors

### 8.1 Vocabulary

1. Claire publishes one JSON-LD context for its types from the first version (B9). The context IRI is the same for all Claire servers **[proposal, PQ2]**. Each server also serves a copy at a local path. Claire servers read the compact names; they do not need a JSON-LD processor.
2. The vocabulary document is written for Claire only, in our own words (Section 3.2).

| Type | Base type | Content |
| :--- | :--- | :--- |
| `claire:KnowledgeBase` | `Group` with a collection of documents **[proposal, PQ7]** | A shared knowledge base: label, description, knowledge type, counts, signature, documents collection, `followers` |
| `claire:Partnership` | `Object` | Offered scopes of one server |
| Document | `Article` | Title, content, canonical URL, content hash, `attributedTo` (the actor with the credit), `origin` (the object it was copied from), `claire:root` (the root document) |
| `claire:Extraction` | `Object` | Entities (keys `type` and `norm_name`, aliases, `sameAs` with authority identifiers) and relations of one document, so that the receiver builds its graph without an LLM call |
| `claire:Contribution` | `Object` | A document with its extraction, offered to one shared knowledge base |

### 8.2 Knowledge Bases as Actors **[proposal, PQ7]**

All reviewed projects model a container as an actor with followers (Section 4.3). Thus each shared knowledge base gets an actor:

1. Each shared knowledge base has a `Group` actor with an inbox, an outbox, and `followers`. Its keys are in the sidecar like the other keys.
2. A server that holds copies of the documents of a knowledge base follows that actor with its server actor. The followers are the holders. This replaces the `Add` notice of Rev. 1 (P4 had the same purpose).
3. The knowledge base actor sends `Update` of a document (revision) and `Delete` (removal at the origin) to its followers.
4. A contribution `Offer` goes to the inbox of the knowledge base actor (F1).
5. Followers are only partner server actors in this design. A follow of a person of the open fediverse is idea I1 and stays outside this design; the same actor can serve it later.
6. Alternative if PQ7 is rejected: no knowledge base actors; the holder sends `Add` (document IRI, target = its knowledge base) to the server actor of the origin, and the origin keeps the list of holders (as `CacheFile` in P4).

### 8.3 Activities

| Activity | From → to | Object | Effect |
| :--- | :--- | :--- | :--- |
| `Follow`, `Accept`, `Reject`, `Undo` | Server actor ↔ server actor | — or `claire:Partnership` | Subscription and partnership (Section 5.3) |
| `Update` | Server ↔ server | `claire:Partnership` | New offered scopes |
| `Create`, `Update`, `Delete` | Origin → subscribers and partners | `claire:KnowledgeBase` | Directory (Section 7.1) |
| `Follow`, `Accept`, `Undo` | Holder server actor → knowledge base actor | — | Register and end a holder (Section 8.2) |
| `Update` | Knowledge base actor → holders | `Article` with `claire:Extraction` | Revision (Section 9.2) |
| `Delete` | Knowledge base actor → holders | `Article` | Removal at the origin (Section 9.3) |
| `Offer` | Account actor → knowledge base actor | `claire:Contribution` | Pending contribution (Section 10.1) |
| `Accept` with `result`, `Reject` | Knowledge base actor → account actor | The `Offer` | Decision of the reviewers; `result` is the IRI of the contribution on the target |
| `Grant`, `Revoke` | Knowledge base actor → account actor | Role `collaborator` | Remote collaborator (Section 10.2) |

Curation reads documents and extractions with a signed GET of the collections of the partner.

---

## 9. Copies, Revisions, and Entities

### 9.1 Copies

1. A copy (curation or accepted contribution) is a new object on the holder with a new IRI. It keeps `origin` (the object it was copied from), the IRI of the actor with the credit, and `claire:root` (F2).
2. The holder hosts and controls its copy (F2). It never sends the content of a remote object as its own object: its directory and its collections show the copy with `origin` (N2).
3. When a partnership ends, copies stay (CT7, CT19). Revisions stop because the channel stops.

### 9.2 Revisions

The knowledge base actor of the origin sends `Update` for each revision of a document to its followers. The holders apply it automatically, as in CT20 and CT26 of the shared knowledge base design.

### 9.3 Removal at the Origin **[proposal, PQ3]**

The holder hides the copy at once and puts it in its review queue with the reason code of the origin. Its reviewers decide to remove it or to show it again. A copy is not removed automatically, because a removal at the origin can have a local reason; the hide at once protects the holder when the reason is legal or spam. A removal on the holder never changes the origin (L2).

### 9.4 Entities Across Servers

1. Each server resolves entities in its own merge job (CT24). No server changes the graph of another server. Different results on different servers are normal.
2. **Authority identifiers** **[proposal, PQ9]**. An entity can carry identifiers of an authority in `sameAs`: Wikidata (`wd:Q…`), ISBN, ORCID, and similar (I1, N1). The merge job of the receiver matches an entity with the same authority identifier first, then by keys. This makes matches across servers agree more often, without a federation of merge decisions. v1 sends an identifier only when the entity already has one; a function that links entities to an authority is later work.
3. Federation of merge decisions and links between duplicates are later work (owner direction).

---

## 10. Contributions and Roles Across Servers

### 10.1 Contribution to a Partner (F1)

1. An account of server A selects a shared knowledge base or a theme that has knowledge bases of partner B. The flow of §9.1 and §9.2 of the shared knowledge base design applies, with these changes.
2. The account actor of A sends `Offer` with the document and its extraction to the knowledge base actor of B. The account needs a handle (CT33).
3. B makes a pending contribution with the remote actor as contributor. The answer can wait for its reviewers. B sends `Accept` with `result` (the IRI of the contribution on B) or `Reject` with the reason code. The submission page on A shows the decision.
4. The credit on B is the remote actor. When B receives `Delete(Person)` for that actor, the credit becomes "deleted account" (CT7).
5. B computes the reputation of the remote account only from the decisions of B.

### 10.2 Remote Collaborators (F3, L2) **[proposal, PQ8]**

1. A moderator of a shared knowledge base on B can give the role `collaborator` to an account of a partner server. B sends `Grant` (context = the knowledge base, target = the remote actor, object = `collaborator`, end time 6 months) from the knowledge base actor.
2. Each `Offer` of that account carries the grant IRI as its capability. B checks the grant (its own actor, its context, its target, the time, not revoked) and then applies the rules of a collaborator: no approval (shared knowledge base design §6.2), unless the local reputation of the account is `low`.
3. `Revoke` ends the grant at once. The moderator can renew a grant before its end time.
4. A remote collaborator has only the capabilities `contribute_direct` and `contribute_theme` across servers. Review, curation, and maintenance stay with local accounts in v1, because they need a session on B.
5. No remote moderators in v1. The admin of B is responsible for its moderators.
6. Account partnerships across servers (shared knowledge base design §9.10) use the same grants.

### 10.3 Automatic Contribution to a Partner

1. Needs the effective scope `auto` (Section 5.2), which needs the switch `kb_auto_contribution` `on` on both servers.
2. The rule of the account (shared knowledge base design §9.9) can include partner targets. The candidates use the signature of the remote knowledge base from the directory.
3. All limits and safety rules of §9.9 apply on the sender. The receiver applies its own pending limits for each remote account.

---

## 11. Inbox Journal (W1)

1. The sidecar stores each received partnership activity, with the result of its signature check, before Claire processes it.
2. Processing is idempotent: the activity IRI is the key; the same activity processed twice changes nothing.
3. A failed activity stays in the journal with its error. The admin can process it again from `/admin/federation`.
4. Retention: 30 days for processed activities, 90 days for failed activities **[proposal]**. The journal is in `fedify.db` or a new table of `client.db` **[unverified]**: unit PS1 decides after a check of the inbox event contract (FEDERATION.md §5.7).

---

## 12. Security

| ID | Risk | Control |
| :--- | :--- | :--- |
| PS1 | A server that is not a partner reads or writes shared knowledge | Partnership endpoints and collections need a signed request of an approved server actor. Other requests get 404. |
| PS2 | A partner sends large or bad objects | Size limits for each object and each day; schema validation; HTML sanitization; the merge job makes no LLM call, so received text is not a prompt in the merge. |
| PS3 | A partner floods the review queue | Pending limits for each remote account and each partner; the admin changes the scopes or ends the partnership at once. |
| PS4 | A partner tells a false reputation or merge decision | Neither is received; each server computes its own (Sections 9.4, 10.1). |
| PS5 | Private data leaves the server | Only shared knowledge bases (always public) and their visible documents go to partners. Personal knowledge bases are not in the directory (PQ5). No IdP data goes into federation (existing rule). |
| PS6 | A blocked domain stays a partner | A domain block ends the subscription and the partnership. |
| PS7 | A partnership is accepted by mistake or with a stolen token | Recent sign-in for each partnership action; `admin_audit` (Section 5.3). |
| PS8 | A grant is used after it should end | End time of 6 months; `Revoke`; each use checks the grant (Section 10.2). |
| PS9 | An activity is processed twice or lost | Inbox journal with idempotent processing (Section 11). |

Switches (`server_settings`, all in `/admin/federation`):

| Switch | Values | Default |
| :--- | :--- | :--- |
| `federation` (exists) | `off`, `on` | `off` today; `on` when the ActivityPub functions are complete (owner direction) |
| `partnerships` | `off`, `on` | `on` with `federation` (owner direction). It opens the handshake; each subscription and partnership still needs an admin decision. |
| `kb_auto_contribution` | `off`, `on` | `off` until the admin turns it on **[proposal, PQ6]** |

---

## 13. Single Mode (CT33, confirmed)

1. **USERNAME.** The first setup sets the USERNAME of the admin (namespace design §8.4 already requires it). The production server already has one.
2. **Root.** `/` sends a temporary redirect (302) to `/@{username}/`. It is temporary because the meaning of `/` depends on the server mode, which the admin can change. Other root paths of the personal knowledge base of single mode get 308 to `/@{username}/…`; these targets stay valid in network mode.
3. **Setup not complete.** While the admin account has the state `setup_required`, `/` with the admin credential goes to `/setup` (as today); other requests get a page "setup not complete". There is no other target.
4. **USERNAME exposure.** Rule S9 of the namespace design (no USERNAME in single mode) is withdrawn. The test `tests/test_single_mode_no_username_leak.py` is replaced by a test of the redirects. The setup confirmation says that the USERNAME is public in both modes.
5. **Federation in single mode.** The server actor, subscriptions, and partnerships work in single mode. The readiness check GF1 (network mode) changes for them. The account actor of the admin in single mode: **[proposal, PQ4]**.
6. **Mode change.** The refusal of `network` → `single` while `federation` is `on` (FEDERATION.md §8.3) changes, because federation can be on in single mode. A change to single mode still needs no members.

---

## 14. Open Questions

| ID | Question | Proposal |
| :--- | :--- | :--- |
| PQ1 | Two levels: a one-way subscription (directory only, one approval) and a partnership (both ways, scopes as the intersection, each admin decides once)? | Yes (Section 5). |
| PQ2 | The IRI of the Claire JSON-LD context | One IRI under a domain of the project that the owner selects; each server also serves a copy. |
| PQ3 | Removal at the origin | The holder hides the copy at once and its reviewers decide (Section 9.3). |
| PQ4 | Does the account actor of the admin federate in single mode? It is necessary for contributions to partners. | Yes, with the same rules as network mode (`profile_public`, step-up). |
| PQ5 | Public personal knowledge bases in the directory? | No in v1. Only shared knowledge bases. |
| PQ6 | Default of `kb_auto_contribution` | `off`. |
| PQ7 | Each shared knowledge base as a `Group` actor, with holders as its followers (Section 8.2)? This is the pattern of all reviewed projects, and it prepares idea I1. | Yes. |
| PQ8 | Remote collaborators with `Grant` and `Revoke` of the ForgeFed pattern, only for contributions, no remote moderators (Section 10.2)? | Yes. |
| PQ9 | Authority identifiers (Wikidata and others) in `sameAs` of entities, matched first by the receiver (Section 9.4)? | Yes, when the entity already has one; linking to an authority is later work. |
| PQ10 | Open exchange without an agreement, and the partnership only for deeper trust: automatic contributions across servers, acceptance of the approvals of the partner, higher limits (Section 4.5)? | Yes. PQ1 then changes. |
| PQ11 | Remove the term "directory": a server reads or follows the `/kb` collection of another server; themes stay local judgements (Section 4.5)? | Yes. |

---

## 15. Phases

Precondition: units CT-0 to CT-9 of the shared knowledge base design, and the federation phases of the sidecar.

| Phase | Scope | Completion criteria |
| :--- | :--- | :--- |
| PS0 | Vocabulary and JSON-LD context in our own words; contract fixtures for each object and activity | Fixtures validate on both sides of a test with two Claire servers |
| PS1 | Switches, subscription, partnership handshake, server list, discovery, inbox journal, single mode root and USERNAME (Section 13) | Two test servers make a subscription, then a partnership, change it, and end it; a third server gets 404 on partnership collections; a failed activity can be processed again; single mode `/` gives 302 to `/@{username}/` |
| PS2 | Directory synchronization with the first full fetch, remote knowledge base store, cross-server themes, root page | A shared knowledge base on server B appears in a theme of server A and on the root page of A |
| PS3 | Knowledge base actors, holders, curation across servers, revisions, removal at the origin | A revision on B reaches the copy on A without approval; a removal on B hides the copy on A and puts it in the review queue |
| PS4 | Contributions across servers (`Offer`, `Accept` with `result`, `Reject`), remote collaborators (`Grant`, `Revoke`) | E2E test: an account of A contributes to a shared knowledge base of B; a reviewer of B approves; the submission page on A shows the decision; after a grant, the next contribution needs no approval; after `Revoke`, it needs approval again |
| PS5 | Automatic contribution across servers | With `auto` on both servers, a new shared knowledge base on B gets the matching documents of an account of A after a run |

Later work: federation of merge decisions; links between duplicate knowledge; a function that links entities to an authority; a relay for many partners (N3); idea I1 (follows of persons of the open fediverse to a knowledge base actor).
