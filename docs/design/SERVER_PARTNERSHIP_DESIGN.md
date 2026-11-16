# Server Partnerships: Exchange of Shared Knowledge Between Claire Bible Servers (Idea I2)

- **Document Identifier**: `DESIGN-SERVER-PARTNERSHIP-20261116`
- **Status**: Draft, Rev. 1. The owner direction of Section 2 is locked (CT31 to CT33 of the shared knowledge base design, 2026-11-16). All other content is a **proposal**. Section 12 has the open questions.
- **Date**: 2026-11-16
- **Base commit**: `e679bf90`
- **Related Documents**:
  - [`SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md`](SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md) (shared knowledge bases, themes, contributions; this design extends them across servers)
  - [`FEDERATION_REMOTE_READING_DESIGN.md`](FEDERATION_REMOTE_READING_DESIGN.md) (§4.2 idea I2: this document starts its design; idea I1 stays separate)
  - [`ACCOUNT_IDP_AND_FEDIFY_SIDECAR_INTEGRATION_DESIGN.md`](ACCOUNT_IDP_AND_FEDIFY_SIDECAR_INTEGRATION_DESIGN.md) (sidecar, keys, internal API)
  - [`FEDERATION.md`](../implementation/FEDERATION.md) (server actor §4.2, switch and readiness checks §7)
  - [`MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md`](MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md) (server modes §4, first admin setup §8.4, S9)

Evidence tags follow [`PRINCIPLES.md`](../PRINCIPLES.md) F1: **[code]**, **[decision]**, **[proposal]**, **[external]**, **[unverified]**.

---

## 1. Goal

1. Shared knowledge bases grow across servers through partnerships between servers, not only inside one server.
2. Partner servers show the themes and the shared knowledge bases of each other. The root page in network mode is the place for this information.
3. Reviewers curate from partner servers. Accounts contribute to shared knowledge bases of partner servers. When both servers allow it, automatic contributions also go across servers.
4. Each server keeps its own decisions: merge of entities, subject judgement, review, reputation. No server is an authority over another server.

---

## 2. Owner Direction (2026-11-16)

| Item | Direction |
| :--- | :--- |
| Aim | Shared knowledge bases aim at partnerships between servers. |
| Root page | In network mode, the root page shows the partnership information. |
| Reference | BookWyrm is a good reference for partnership interaction in ActivityPub: it shares book and author data between servers. A reference needs a clean-room implementation because of the license. |
| Default | When the ActivityPub functions are complete, partnership communication is on by default, also in single mode. |
| Single mode | The admin sets the USERNAME (the handle) at the first setup. Single mode shows no root page of network mode: `/` sends the request to `/@{username}/` of the admin. |
| Automatic contribution | The admin turns it on or off for the server in the ActivityPub federation menu. |
| Later work | Merge decisions differ between servers; federation of merge decisions waits for a later design. Links between duplicate knowledge wait until there is a real need. |

---

## 3. Clean-Room Rules

### 3.1 License Facts

| Project | License | Effect |
| :--- | :--- | :--- |
| Claire Bible | MIT **[code]** `LICENSE.md` | Any user may use the code. |
| BookWyrm | Anti-Capitalist Software License v1.4 **[external]** (`LICENSE.md` of the BookWyrm repository, read 2026-11-16) | Use is limited to some kinds of users (for example individuals, non-profit organizations, and worker-owned organizations) and is not permitted for law enforcement or the military. Code or text under this license cannot enter an MIT project without these limits. |

### 3.2 Rules

1. No BookWyrm source code, template, schema, migration, test, or documentation text enters Claire Bible.
2. This design takes only ideas and protocol facts from the public BookWyrm documentation. Section 4 lists each idea, its source page, and the Claire design in our own words.
3. Persons who write Claire code for this design do not read the BookWyrm source code. They work only from this design and from the ActivityPub and ActivityStreams specifications.
4. Interoperation with BookWyrm servers is not a goal. The exchange is between Claire Bible servers (idea I2).
5. When a later revision takes a new idea from BookWyrm, it adds a row to Section 4 with the page and the date.

Pages read on 2026-11-16 (documentation and license only): `FEDERATION.md` and `LICENSE.md` of the BookWyrm repository; `docs.joinbookwyrm.com` pages "federation", "activitypub", "admin-settings", and "system".

---

## 4. Reference Review: Ideas from the BookWyrm Documentation

| No. | Idea in the BookWyrm documentation (own words) | Claire design | Difference |
| :--- | :--- | :--- | :--- |
| B1 | Servers share book metadata to make a networked database. | Partners share the directory of themes and shared knowledge bases (Section 6). | Claire shares knowledge bases, documents, and graphs, not only metadata. |
| B2 | Books and authors are objects with custom types; each object `id` is a URL on its origin server. A receiving server keeps a local copy and matches an incoming object to an existing local object when it can. | Shared knowledge bases, documents, and extractions have IRIs on their origin server. A copy keeps the origin IRI. The merge job of the receiving server matches entities by its own rules (Section 7.3). | The receiving server never takes the entity merge of the origin as a fact. |
| B3 | Connectors are data sources for book search, with an order of priority that the admin sets; another server can use the search endpoint, and to block it gives more duplicate works. | Partner servers are sources with an order of priority for the search of themes and shared knowledge bases. The page "Make a shared knowledge base" shows similar knowledge bases on partners first (Section 6.3). | Claire searches only partners, not open sources. |
| B4 | A book page shows reviews of the same book from connected servers. | A theme page shows the knowledge bases of the same subject from partner servers (Section 6.2). | Each server judges subjects on its own. |
| B5 | A server actor signs requests; a server can require signed GET requests. | The existing server actor `claire@{handle host}` signs all partnership requests. Partnership collections need a signed GET from a partner (Section 9). | — |
| B6 | The admin sees a list of federated servers with domain and software and can block a server. | `/admin/federation` lists partners with state, scopes, counts, and the last synchronization (Section 5.4). | A partnership needs an explicit agreement; BookWyrm federation is open by default. |
| B7 | Custom status types reach other software as `Article` or `Note`. | Each Claire object has a standard ActivityStreams base type, so that other software can show it. | — |
| B8 | The receiving server decides what it shows from what its inbox receives. | Each server decides what it shows and keeps (Sections 7 and 8). | — |
| B9 | The documentation notes that the custom types have no published JSON-LD context. | Claire publishes its context from the first version (Section 7.1). | Learned from a gap. |

Why Claire needs an explicit agreement (difference from B6): a contribution moves content and changes the graph of another server. Thus an exchange of shared knowledge starts only after both admins agree.

---

## 5. Partnership

### 5.1 Scopes

| Scope | Content | Needs |
| :--- | :--- | :--- |
| `directory` | Themes, metadata and signatures of shared knowledge bases | — |
| `curation` | Reviewers copy visible documents of shared knowledge bases of the partner | `directory` |
| `contribution` | Accounts send documents to shared knowledge bases of the partner; the reviewers of the partner decide | `directory` |
| `auto` | Automatic contributions to shared knowledge bases of the partner | `contribution`, and the switch `kb_auto_contribution` is `on` on both servers |

Each admin sets the scopes that its server offers to each partner. The effective scopes of a partnership are the scopes that both servers offer. This is the negotiation: no server gets more than the other server gives.

### 5.2 Handshake

1. The actors are the server actors (`Application`, `claire@{handle host}`, `manuallyApprovesFollowers` true; **[code]** FEDERATION.md §4.2).
2. The admin of server A enters the domain of server B in `/admin/federation`. A sends `Follow` to the server actor of B, with the offered scopes in a `claire:Partnership` object.
3. The admin of B sees the request and accepts or rejects it. On accept, B sends `Accept` and its own `Follow` with its offered scopes. A accepts this `Follow` automatically because A made the request. The partnership is active when both follows are accepted. Thus each admin decides once.
4. A change of offered scopes: `Update` of the `claire:Partnership` object. Both servers compute the effective scopes again.
5. End: either server sends `Undo(Follow)`. Both servers stop the exchange at once.
6. A domain block (existing function) ends a partnership and refuses new requests.
7. Request, accept, scope change, and end need a recent sign-in of the admin and write `admin_audit`.

### 5.3 Discovery

1. An admin enters a domain. The server reads its NodeInfo and the server actor and shows the software, the version, and the scopes that the other server can offer.
2. The directory of a partner lists its partners. The console shows them as suggestions only; nothing starts a partnership without an admin decision.

### 5.4 Admin Console `/admin/federation`

| Area | Content |
| :--- | :--- |
| Switches | `federation`, `partnerships`, `kb_auto_contribution` (Section 10) |
| Partners | Domain, software, version, state (`requested`, `pending`, `active`, `ended`, `blocked`), effective scopes, counts of themes and shared knowledge bases, last synchronization, errors |
| Requests | Incoming requests with the scopes that the other server offers |
| Offered scopes | For each partner |

---

## 6. Directory and Cross-Server Themes

### 6.1 Synchronization of the Directory

1. With the scope `directory`, the origin sends `Create`, `Update`, and `Delete` of each `claire:KnowledgeBase` (shared knowledge bases only) to its partners: label, description, knowledge type, counts, the signature of Section 8.2 of the shared knowledge base design, and the IRI.
2. A partner keeps these records in a separate store of remote knowledge bases. A remote knowledge base is not a partition of the local `claire.db`; it has no documents until a curation or a contribution.
3. Public personal knowledge bases are not in the directory **[proposal, PQ5]**.

### 6.2 Cross-Server Themes

1. The subject judgement of each server includes the signatures of remote shared knowledge bases. A remote knowledge base can be a member of a local theme.
2. Each server computes its own themes. The same subject can have different theme numbers on different servers. A theme number is local; it never leaves the server as an identifier.
3. The theme page shows local and remote knowledge bases. A remote knowledge base links to its origin.

### 6.3 Root Page (Network Mode)

1. Themes first, with the counts of local and remote shared knowledge bases.
2. A section "Partner servers": name, domain, effective scopes, number of shared knowledge bases and themes.
3. Recent activity across servers: new shared knowledge bases of partners, contributions that partners approved from accounts of this server, curations.
4. The page "Make a shared knowledge base" shows similar local and remote shared knowledge bases before the account makes a new one (B3). This keeps the number of duplicate knowledge bases small.

Single mode shows no root page (Section 11).

---

## 7. Objects and Activities

### 7.1 Vocabulary

1. Claire publishes one JSON-LD context for its types from the first version (B9). The context IRI is the same for all Claire servers **[proposal, PQ2]**. Each server also serves a copy at a local path. Claire servers read the compact names; they do not need a JSON-LD processor.
2. The vocabulary document is written for Claire only, in our own words (Section 3.2).

| Type | Base type | Content |
| :--- | :--- | :--- |
| `claire:KnowledgeBase` | `Collection` | A shared knowledge base: label, description, knowledge type, counts, signature, IRI of its documents collection |
| `claire:Partnership` | `Object` | Offered scopes of one server |
| Document | `Article` | Title, content, canonical URL, content hash, `attributedTo` (the actor with the credit), `claire:root` (the IRI of the root document) |
| `claire:Extraction` | `Object` | Entities (keys `type` and `norm_name`, aliases, `sameAs` IRIs) and relations of one document, so that the receiver builds its graph without an LLM call |
| `claire:Contribution` | `Object` | A document with its extraction, offered to one shared knowledge base |

### 7.2 Activities

| Activity | From → to | Object | Effect |
| :--- | :--- | :--- | :--- |
| `Follow`, `Accept`, `Reject`, `Undo` | Server actor ↔ server actor | `claire:Partnership` | Handshake (Section 5.2) |
| `Update` | Server ↔ server | `claire:Partnership` | New offered scopes |
| `Create`, `Update`, `Delete` | Origin → partners | `claire:KnowledgeBase` | Directory (Section 6.1) |
| `Add` | Holder → origin | Document IRI, target = the knowledge base of the holder | The origin records that a partner holds a copy (Section 7.3) |
| `Update` | Origin → holders | `Article` with `claire:Extraction` | Revision (Section 7.3) |
| `Delete` | Origin → holders | `Article` | Removal at the origin (Section 7.3, PQ3) |
| `Offer` | Account actor → server actor of the target server | `claire:Contribution`, target = the shared knowledge base | Pending contribution at the target (Section 8.1) |
| `Accept`, `Reject` | Server actor of the target → account actor | the `Offer` | Decision of the reviewers, with the reason code on reject |

Curation reads documents and extractions with a signed GET of the collections of the partner.

### 7.3 Copies, Revisions, and Entities

1. **Origin.** A copy from a partner keeps the IRI of the document, of its knowledge base, and of its server, the IRI of the actor with the credit, and the IRI of its root document.
2. **Holders.** When a server makes a copy (curation or accepted contribution), it sends `Add` to the origin. The origin keeps the list of holders and sends `Update` for each revision to them. Revisions apply automatically, as in CT20 and CT26 of the shared knowledge base design.
3. **End of a partnership.** Copies stay (CT7, CT19). Revisions stop because the channel stops.
4. **Removal at the origin.** **[proposal, PQ3]** The holder hides the copy at once and puts it in its review queue with the reason code of the origin. Its reviewers decide to remove it or to show it again. A copy is not removed automatically, because a removal at the origin can have a local reason; the hide at once protects the holder when the reason is legal or spam.
5. **Entities.** Each server resolves entities in its own merge job (CT24). The received extraction gives keys and `sameAs` IRIs; the merge job of the receiver maps them to local entities by its own rules. No server changes the graph of another server. Different results on different servers are normal. Federation of merge decisions is later work (owner direction).
6. **Duplicates.** v1 copies documents. Links between duplicate knowledge on different servers are later work (owner direction).

---

## 8. Contributions Across Servers

### 8.1 Contribution to a Partner

1. An account of server A selects a shared knowledge base or a theme that has knowledge bases of partner B. The flow of the shared knowledge base design (Sections 9.1 and 9.2) applies, with these changes.
2. The account actor of A sends `Offer` with the document and its extraction to B. The account needs a handle; thus single mode needs the USERNAME of the admin (CT33).
3. B makes a pending contribution with the remote actor as contributor. The reviewers of B decide. B sends `Accept` or `Reject` with the reason code. The submission page on A shows the decision.
4. The credit on B is the remote actor. When B receives `Delete(Person)` for that actor, the credit becomes "deleted account" (CT7).
5. B computes the reputation of the remote account only from the decisions of B. B does not use a reputation from A.

### 8.2 Automatic Contribution to a Partner

1. Needs the effective scope `auto` (Section 5.1), which needs the switch `kb_auto_contribution` `on` on both servers.
2. The rule of the account (shared knowledge base design §9.9) can include partner targets. The candidates use the signature of the remote knowledge base from the directory.
3. All limits and safety rules of §9.9 apply on the sender. The receiver applies its own pending limits for each remote account.

---

## 9. Security

| ID | Risk | Control |
| :--- | :--- | :--- |
| PS1 | A server that is not a partner reads or writes shared knowledge | Partnership endpoints and collections need a signed request of a partner server actor. Other requests get 404. |
| PS2 | A partner sends large or bad objects | Size limits for each object and each day; schema validation; HTML sanitization; the merge job makes no LLM call, so received text is not a prompt in the merge. |
| PS3 | A partner floods the review queue | Pending limits for each remote account and each partner; the admin changes the scopes or ends the partnership at once. |
| PS4 | A partner tells a false reputation or merge decision | Neither is received; each server computes its own (Sections 7.3 and 8.1). |
| PS5 | Private data leaves the server | Only shared knowledge bases (always public) and their visible documents go to partners. Personal knowledge bases are not in the directory (PQ5). No IdP data goes into federation (existing rule). |
| PS6 | A blocked domain stays a partner | A domain block ends the partnership. |
| PS7 | The partnership is accepted by mistake or by a stolen token | Recent sign-in for each partnership action; `admin_audit` (Section 5.2). |

---

## 10. Server Switches

| Switch (`server_settings`) | Values | Default |
| :--- | :--- | :--- |
| `federation` (exists) | `off`, `on` | `off` today; `on` when the ActivityPub functions are complete (owner direction) |
| `partnerships` | `off`, `on` | `on` with `federation` (owner direction). It opens the handshake; each partnership still needs an admin decision. |
| `kb_auto_contribution` | `off`, `on` | `off` until the admin turns it on (CT31; **[proposal, PQ6]** for the default) |

All three are in `/admin/federation`.

---

## 11. Single Mode (CT33)

1. **USERNAME.** The first setup sets the USERNAME of the admin (namespace design §8.4 already requires it).
2. **Root.** `/` sends a temporary redirect (302) to `/@{username}/`. It is temporary because the meaning of `/` depends on the server mode, which the admin can change. Other root paths of the personal knowledge base of single mode get 308 to `/@{username}/…`; these targets stay valid in network mode.
3. **Setup not complete.** While the admin account has the state `setup_required`, `/` with the admin credential goes to `/setup` (as today); other requests get a page "setup not complete". There is no other target.
4. **USERNAME exposure.** Rule S9 of the namespace design (no USERNAME in single mode) is withdrawn. The setup confirmation says that the USERNAME is public in both modes.
5. **Federation in single mode.** The server actor and partnerships work in single mode. The readiness check GF1 (network mode) changes for the partnership channel. The account actor of the admin in single mode: **[proposal, PQ4]**.
6. **Mode change.** The refusal of `network` → `single` while `federation` is `on` (FEDERATION.md §8.3) changes, because federation can be on in single mode. A change to single mode still needs no members.

---

## 12. Open Questions

| ID | Question | Proposal |
| :--- | :--- | :--- |
| PQ1 | Partnership as mutual `Follow` of the server actors, with the effective scopes as the scopes that both servers offer (Sections 5.1, 5.2)? | Yes. |
| PQ2 | The IRI of the Claire JSON-LD context (Section 7.1) | One IRI under a domain of the project that the owner selects; each server also serves a copy. |
| PQ3 | Removal at the origin (Section 7.3, rule 4) | The holder hides the copy at once and its reviewers decide. |
| PQ4 | Does the account actor of the admin federate in single mode? It is necessary for contributions to partners (Section 8.1). | Yes, with the same rules as network mode (`profile_public`, step-up). |
| PQ5 | Public personal knowledge bases in the directory? | No in v1. Only shared knowledge bases. |
| PQ6 | Default of `kb_auto_contribution` | `off`. |

---

## 13. Phases

Precondition: units CT-0 to CT-9 of the shared knowledge base design, and the federation phases of the sidecar.

| Phase | Scope | Completion criteria |
| :--- | :--- | :--- |
| PS0 | Vocabulary and JSON-LD context in our own words; contract fixtures for each object and activity | Fixtures validate on both sides of a test with two Claire servers |
| PS1 | Switches, handshake, partner list, discovery, single mode root and USERNAME (Section 11) | Two test servers make, change, and end a partnership; a third server gets 404 on partnership collections; single mode `/` gives 302 to `/@{username}/` |
| PS2 | Directory synchronization, remote knowledge base store, cross-server themes, root page | A shared knowledge base on server B appears in a theme of server A and on the root page of A |
| PS3 | Curation across servers, holders, revisions, removal at the origin | A revision on B reaches the copy on A without approval; a removal on B hides the copy on A and puts it in the review queue |
| PS4 | Contributions across servers (`Offer`, `Accept`, `Reject`) | E2E test: an account of A contributes to a shared knowledge base of B; a reviewer of B approves; the submission page on A shows the decision |
| PS5 | Automatic contribution across servers | With `auto` on both servers, a new shared knowledge base on B gets the matching documents of an account of A after a run |

Later work: federation of merge decisions; links between duplicate knowledge; idea I1 (a shared knowledge base or a theme as a `Group` actor for the open fediverse).
