# Public List of Blocked Domains

- **Document Identifier**: `DESIGN-PUBLIC-DOMAIN-BLOCK-LIST-20261119`
- **Status**: Proposal, not implemented (Rev. 1). The owner asked for this design in the review of 2026-11-19 (KD15 of [`KNOWLEDGE_FEDERATION_DESIGN.md`](KNOWLEDGE_FEDERATION_DESIGN.md)). Each item in Section 3 is a **proposal** until the owner answers the questions of Section 5.
- **Date**: 2026-11-19
- **Related Documents**:
  - [`KNOWLEDGE_FEDERATION_DESIGN.md`](KNOWLEDGE_FEDERATION_DESIGN.md) Section 6.3 (admin controls, the later level "limit": KD8) and Section 8.3 (root page, KD15)
  - [`FEDERATION.md`](../implementation/FEDERATION.md) Section 7.5 (domain blocks: routes and console) and Section 4.3 (blocked domains in the sidecar)
  - [`FEDERATION_REMOTE_READING_DESIGN.md`](FEDERATION_REMOTE_READING_DESIGN.md) Section 1.2.1 (deferred D1: blocks, hides, and reports of each account)

---

## 1. Background

The owner reviewed the root page of network mode on 2026-11-19 (KD15):

1. The root page had a public list of the federated servers. There is no reason to make that list public. It is removed.
2. A list of the blocked domains can be more useful than a list of the federated servers.

Facts of the code (2026-11-19):

1. The table `federation_domain_blocks` (`claire.db` version 17) has `domain`, `created_at`, `created_by`, and `note`. `note` is optional, 200 characters at most, and only the admin sees it.
2. A block applies to the domain and to its subdomains (`federation_store.blocking_domain`). It stops the federation of accounts (the sidecar refuses the activities and sends no deliveries) and the federation of shared knowledge (KNOWLEDGE_FEDERATION_DESIGN.md Section 6.3).
3. Only the admin can read and change the blocks: `GET`, `POST`, `DELETE /api/admin/federation/domain-blocks` and the console `/admin/federation`. A change needs the same origin and a recent sign-in, and writes an `admin_audit` row.
4. There is one level of block. The level "limit" is a later step (KD8).
5. No public page and no public route shows a block.

## 2. Goal

1. A reader can see which domains this server blocks, so that the reader knows why posts, follows, and shared knowledge of those domains do not arrive.
2. The admin of another server can see the policy of this server before an exchange.
3. The admin decides what is public. The default does not change the current behavior: nothing is public.

## 3. Proposal

### 3.1 Server Setting

A new server setting `domain_blocks_public` with three values:

| Value | Effect |
| :--- | :--- |
| `off` (default) | No public list. The route of Section 3.3 gives 404. This is the current behavior. |
| `accounts` | Signed-in accounts of this server see the list. Other callers get 404. |
| `public` | Each caller sees the list. |

Only an `owner` session of the admin with a recent sign-in changes the setting. The change writes an `admin_audit` row.

### 3.2 Data of Each Block

Two new columns of `federation_domain_blocks` (a new version of `claire.db`; the migration completes without operator action and keeps each existing row):

| Column | Meaning |
| :--- | :--- |
| `public_reason TEXT NOT NULL DEFAULT ''` | The reason that the list shows. It is different from `note`, which stays private. 200 characters at most, no control characters. |
| `listed INTEGER NOT NULL DEFAULT 0` | 1: the block is in the public list. 0: the block applies, but the list does not show it. Existing rows get 0, so that no existing block becomes public without an admin action. |

The list never shows `created_by` or `note`.

### 3.3 Route and Page

- `GET /domain-blocks` (system surface, both server modes). A browser gets an HTML page; an API client gets JSON: `{"domain_blocks": [{"domain", "level", "reason", "since"}]}`, in the order of the domain. `level` is `suspend` now; `limit` comes with KD8. `since` is the day of `created_at` (not the time).
- The public menu of `/themes`, `/kb`, and `/directory` gets a link to the page only when the caller can see the list.
- The text keys are new catalog keys in `en` and `ko`.

### 3.4 Console

- `/admin/federation`: the form of a new block gets "Public reason" and "Show in the public list". The table of blocks gets the two columns and an edit action for them.
- The setting of Section 3.1 is a control in the same section, with a preview of the public list.

### 3.5 Machine-Readable Export (Option)

KNOWLEDGE_FEDERATION_DESIGN.md Section 3.1 lists the import and export of block lists as a moderation tool of Mastodon. An export of the listed blocks lets another admin import them. Mastodon documents a CSV format for its domain block import and export. Claire can read the public documentation of that format and write its own code (no source code of another project is copied). This is an option for a later unit: Question BQ5.

### 3.6 Safety

1. The default is `off`, and each existing block has `listed = 0`.
2. A public reason is text of the admin. The console tells the admin that the text is public and stays in caches.
3. A domain that the list hides (`listed = 0`) is not in any public answer, also not by a count.
4. The JSON answer has `Cache-Control: public, max-age=300` only with `public`; with `accounts` it has `private, no-store`.

## 4. Rejected Alternatives

| Alternative | Reason |
| :--- | :--- |
| A public list of the federated servers | KD15: there is no reason to make it public. The admin sees the known servers in the console. |
| One switch that makes all blocks public | An admin can have a block that must stay private (for example, a block of a server that attacks this server). Each block needs its own choice (Section 3.2). |
| Show `note` as the public reason | `note` was written as private text. A change of its meaning would make old private text public. |

## 5. Open Questions

| ID | Question | Options |
| :--- | :--- | :--- |
| BQ1 | Is the public list wanted now, or after the level "limit" (KD8)? | A: now, with one level. B: together with "limit". |
| BQ2 | Values of the setting `domain_blocks_public` | A: `off`, `accounts`, `public` (Section 3.1). B: `off` and `public` only. |
| BQ3 | Can the list show a domain in a partly hidden form (for example `b*d.example`) with a SHA-256 digest of the full domain, so that another admin can compare it? | A: yes, a third choice for each block. B: no; a block is listed or not listed. |
| BQ4 | Path of the page | A: `/domain-blocks`. B: a section of a future "about this server" page. |
| BQ5 | Export of the listed blocks for other admins (Section 3.5) | A: a later unit. B: not planned. |
| BQ6 | The blocks of each account (deferred D1 of FEDERATION_REMOTE_READING_DESIGN.md) | A: never in a public list. B: decide with D1. |

## 6. Work Units (after the answers)

| Unit | Scope | Completion criterion |
| :--- | :--- | :--- |
| BL1 | Schema version, migration, store functions, setting, admin API fields | Tests of the migration (existing rows get `listed = 0`), of the setting, and of the admin API |
| BL2 | `GET /domain-blocks`, the HTML page, the menu link, the console fields, `en` and `ko` keys | Tests of each setting value and of a hidden block (no domain, no count in any public answer) |
| BL3 | One Playwright E2E test | The admin lists one block with a reason, sets `public`, and an anonymous browser sees the domain and the reason but not the hidden block |
