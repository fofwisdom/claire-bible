# Data Lifecycle and Purge

- **Status**: Implemented. Design principles only. Current behavior: [`DATA_LIFECYCLE.md`](../implementation/DATA_LIFECYCLE.md) §2 and §3.
- **Date**: 2026-08-25 · condensed 2026-11-16

## Goal / problem

The upstream design kept everything: the inbox, the raw text files, and the model output. This is good when all input is useful. But auto-expansion and crawlers can ingest legacy or harmful text. Examples are an old API version, a wrong Bible translation, or a document with a license problem.

For research about the history of ideas, old text is valid context. For engineering documents and canonical texts, an old version is pollution. The model takes it as fact, and the graph gets wrong entities and relations. To keep such data can also be a legal or operational risk.

Hiding a document is not sufficient. The text stays in storage, and a replay or a re-extraction brings it back. A plain row delete is also not sufficient, because expansion finds the same URL again.

## Decisions in force

- **The operator selects the lifecycle policy.** A server can be `purgeable` or strictly `append-only`. Reason: the right policy depends on the domain, and only the operator knows the domain. In append-only mode a purge is blocked, so that a mistake cannot destroy data.
- **A purge removes the document from all layers in one operation.** Database rows, raw files, images, attachments, graph references, published federation objects, and free database space. Reason: a residue in any layer can bring the polluted text back.
- **The database part of a purge is one transaction.** Reason: a partial purge leaves a state that is hard to find and to repair.
- **A purge leaves a tombstone, not the content.** The tombstone keeps only the fingerprint (URL, canonical URL, content hash) and the reason. Ingestion and expansion check it and drop a match. Reason: this stops re-ingestion without keeping the polluted text. The operator can skip the tombstone or lift it later, to allow a new ingest on purpose.
- **The graph is repaired after a purge.** Entities and relations that lose all their sources are removed. Reason: a fact without a source is not grounded (principle A1).
- **Anyone can prove that no residue remains.** An audit counts matches in every layer and checks that no document matches a tombstone.
- **A purge is a dry run by default.** It needs an explicit apply and a confirmation (principles D2, D3).

## Rejected alternatives

- **Append-only for all servers.** Rejected: it makes pollution permanent for domains where an old version is harmful.
- **Hide instead of delete.** Rejected: the data stays and comes back on replay.
- **Delete rows only.** Rejected: raw files stay, and expansion ingests the same source again.

## Limits for later changes

- A new table or file that holds document data must be part of the purge cascade and of the audit.
- A new ingest path must check tombstones before it stores a document.
