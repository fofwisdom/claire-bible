# Ideas From the Retired Partnership: Relay and Mutual Collaboration

- **Status**: Ideas only. They have no design for implementation. Do not implement from this document. A design starts only after an owner decision.
- **Date**: 2026-11-16
- **Source**: Taken out of [`KNOWLEDGE_FEDERATION_DESIGN.md`](../design/KNOWLEDGE_FEDERATION_DESIGN.md) (Rev. 5) and [`SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md`](../design/SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md) (Rev. 7) on 2026-11-16, because the plan grew too wide (owner). Earlier full texts are in git history: the bilateral partnership with scopes in `docs/design/SERVER_PARTNERSHIP_DESIGN.md` at commit `645676a0`, and the relay and the mutual collaboration in both design documents at commit `2e3a9191`.

---

## 1. Background

1. Rev. 1 to Rev. 3 of the design of the exchange between servers used "partnership" for a bilateral agreement of two admins: negotiated scopes (`directory`, `curation`, `contribution`, `auto`), a one-way subscription level, a handshake of the server actors, and a switch `partnerships`.
2. The owner then said that themes share ingested documents between servers without an agreement (CT35), and mapped the terms to Mastodon: the open exchange is **federation**, and the earlier meaning of "partnership" is **relay** (CT36). Mastodon has no bilateral agreement between servers.
3. The shared knowledge base design also had a "partnership" between two accounts, from a contributor proposal: "When friends agree, a new space gets their documents automatically after a short wait." Rev. 6 of that design renamed it **mutual collaboration**.

---

## 2. Where the Parts of the Old Partnership Went

| Part of the old partnership | Place now |
| :--- | :--- |
| Reading the shared knowledge bases of another server | Federation design (§6, §8) |
| Curation and contributions across servers | Federation design (§11, §12); the reviewers of each knowledge base decide |
| Trust for one remote account | A grant of the role `collaborator` by a moderator (federation design §12.2) |
| Automatic contributions across servers | Federation design §12.3, with the switch `kb_auto_contribution` on both servers |
| One-way subscription by the admin to receive announcements | **Idea: relay (Section 3)** |
| "Partnership" between two accounts | **Idea: mutual collaboration (Section 4)** |
| Acceptance of the approvals of the reviewers of another server as local approvals; higher limits for a partner | Dropped; nobody asked for them |

---

## 3. Idea: Relay

### 3.1 Facts

The term definition stays in the federation design §3.2 (checked on 2026-11-16): a relay is a separate service actor, a hub; servers subscribe to it with `Follow`, and it sends the public, signed activities that it receives to all its subscribers again (READMEs of pub-relay, Activity-Relay, Pleroma relay). The Mastodon documentation has no page about relays. Mobilizon calls the internal actor of its subscriptions between two servers `relay`. NeoDB has an optional relay for public activities.

### 3.2 Use in Claire

A relay is a channel that the admin opts into. It brings announcements of shared knowledge bases from servers that no account of the server knows yet, so that themes across servers grow beyond the follows of persons. It carries only public announcements, no documents and no contributions.

1. The admin subscribes the server to a relay with `Follow` from the server actor. The relay operator can approve. `Undo` ends the subscription. Each action needs a recent sign-in and writes `admin_audit`.
2. A subscribed server sends the public announcements of its shared knowledge bases (`Create`, `Update`, `Delete` of `claire:KnowledgeBase`) to the relay and receives those of the other subscribers. Each sender becomes a known server (federation design §6.2).
3. A relay carries no documents, no contributions, and no decisions. The exchange of content stays in federation between the two servers.
4. **[unverified]** Generic relay software sends again only payloads with a linked-data signature. A design must first check which proof the relays accept (for example FEP-8b32 object integrity proofs).
5. A Claire server that acts as a relay (as the internal `relay` actor of Mobilizon) is a later idea.
6. Default: no relay.

### 3.3 Parts in Other Areas

| Area | Content |
| :--- | :--- |
| Activities | `Follow`, `Undo` from the server actor to the relay actor; `Create`, `Update`, `Delete` of `claire:KnowledgeBase` (public) from the server to the relay |
| Console `/admin/federation` | An area "Relays": subscriptions and their state |
| Security | Spam announcements: announcements only add known servers and records of remote knowledge bases, never content; the admin ends the subscription or blocks the domain. A subscription by mistake or with a stolen token: recent sign-in and `admin_audit`. |
| Test | Two servers subscribed to a test relay know each other's shared knowledge bases without any account interaction. |
| References | PeerTube platform follows (P1), the Mobilizon `relay` actor (M1), the NeoDB relay (N3) in the federation design §5.2 |

### 3.4 Question for the Owner

Relay as above: admin opt-in, announcements of shared knowledge bases only, no relay by default; a Claire server as a relay later? Proposal: yes. (PQ12 of the federation design Rev. 4.)

---

## 4. Idea: Mutual Collaboration

### 4.1 Rules

1. Account A requests a mutual collaboration with account B; B accepts. Both actions need a recent sign-in. Either account ends it at any time.
2. Effect: when A founds a shared knowledge base, B becomes a collaborator in it at once (`decided_by_account_id` = A, log action `grant_collaborator`), and the reverse. When the automatic rule of B has the target `mutual`, the documents of B that fit go to the new knowledge base without approval.
3. The end of a mutual collaboration does not change roles that it gave; moderators change them. New knowledge bases are not affected after the end.
4. A mutual collaboration never gives `people`, `settings`, `remove`, or `moderator`.
5. A suspended or purged account loses its mutual collaborations (suspension: paused; purge: deleted).
6. Between accounts of different servers, it uses the grants of the federation design §12.2.

### 4.2 Parts in Other Areas

| Area | Content |
| :--- | :--- |
| Automatic rule | A target `mutual` (the shared knowledge bases that a mutual collaborator founded); a run when a mutual collaboration starts; column `kb_auto_rules.to_mutual` |
| Lifecycle | At `POST /kb`, the mutual collaborators of the founder become collaborators |
| Reputation | A contribution under a mutual collaboration needs no approval, except with the reputation `low` |
| Roles and security | Only the scope `owner` with a recent sign-in requests or accepts it; no IdP claim gives it; a back-channel logout keeps it; a suspension pauses it. Risk: a mutual collaborator misuses the collaborator role. Control: either account ends it at once; moderators remove the role; it never gives `people`, `settings`, or `remove`. |
| Console | `/settings/kb` lists the mutual collaborations of the account |
| Purge | Deleted at an account purge |
| Test | A and B make a mutual collaboration; B turns on its rule with `mutual` after the dry run; A makes a shared knowledge base with seed entities; after the run, the matching documents of B are visible in it without approval. |

```sql
CREATE TABLE IF NOT EXISTS kb_mutual_collaborations (
    account_low INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    account_high INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    requested_by_account_id INTEGER NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'active')),
    created_at REAL NOT NULL,
    decided_at REAL,
    PRIMARY KEY (account_low, account_high),
    CHECK (account_low < account_high)
);
```

### 4.3 Question for the Owner

Keep the mutual collaboration between two accounts? Proposal: yes; it works on one server and, with grants, between servers. (OQ29 of the shared knowledge base design Rev. 6.)

---

## 5. To Bring an Idea Back

1. The owner decides it.
2. A design revision adds it to the design document of its area, with the section numbers of that time. Then this document removes the idea.
