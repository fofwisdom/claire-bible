# Resolution Decision Stream, Heatmap Matrix, and Safe Gating

- **Status**: Partly implemented. Implemented: the decision record in document metadata, the gated resolver, the rollback payload with a rollback function and an owner API, the heatmap (local matrix only), and the decision stream view; see [`GRAPH_BUNDLE_REVIEW.md`](../implementation/GRAPH_BUNDLE_REVIEW.md) §2 to §4. Planned, not implemented: full reversible merge (Section "Planned"). The rollback button is off because the rollback is not tested, and the decision stream pages are off for all themes and can be removed ([`IMPLEMENTATION_STATUS.md`](../implementation/IMPLEMENTATION_STATUS.md) §2).
- **Date**: 2026-09-27 · condensed 2026-11-16
- **Related**: [GOALS.md](../GOALS.md) track 2 (extraction and linking quality), [GRAPH_BUNDLE_REVIEW_EVIDENCE_DESIGN.md](GRAPH_BUNDLE_REVIEW_EVIDENCE_DESIGN.md), [JEV_SHADOW_EVALUATION_DESIGN.md](JEV_SHADOW_EVALUATION_DESIGN.md), [CROSS_TYPE_ENTITY_RESOLUTION_AND_VLM_LOCALIZATION_DESIGN.md](CROSS_TYPE_ENTITY_RESOLUTION_AND_VLM_LOCALIZATION_DESIGN.md)

## Problem

The ingest pipeline extracts entities and resolves them against the knowledge base. Users wanted two things:

1. To feel the size of the comparison work while an ingest runs.
2. To audit later why an entity was merged or created, and to correct a false merge.

## Asymmetric risk

The two resolution errors are not equal:

- **False negative (no merge)**: two nodes for one thing. A cross relation or a later correction repairs it. Low risk.
- **False positive (false merge)**: two different things (competing tools, versions, homonyms) become one node. Relations and identity mix, and the graph can collapse into a "god entity". Critical risk.

Thus every gate is designed for zero false merges first, and recall second.

## Decisions in force

1. **Two views with different life cycles.**
   - The **heatmap matrix** shows the size of the comparison as color only (no numbers, no text). It is a one-time view: it shows during the ingest and once on the document, and it goes away when the user confirms it.
   - The **decision stream** is an audit trail of each entity state change, with the stage and the reason. It is kept permanently.
2. **Plan B: decision records in document metadata.** No new table and no schema migration. The records live with their document, so theme isolation and purge apply to them automatically. Reason: the knowledge database and the telemetry database stay separate (telemetry is short-lived and must not hold permanent records, and a cross-database join risks lock conflicts).
3. **The heatmap does not get its own storage.** It is not written to a table. (The code keeps it in the document metadata until the first confirmation and then removes it; see the implementation document §3.)
4. **Five serial gates for resolution.**
   1. Deterministic rules (name, registered alias, acronym of the same type): merge with certainty, no embedding and no token cost.
   2. Hard invariants (type mismatch except provisional types, version token conflict, two-letter acronyms): the candidate is removed whatever its score.
   3. Fast screening by vector score: low scores are dropped, very high scores merge.
   4. The System 2 judge, only for the borderline band, with observations as context. `DIFFERENT` keeps the pair as a relation candidate.
   5. Reversible write: every merge stores what it added.
   Reason: the cheap and certain checks come first; the expensive judge sees only the uncertain band; nothing can bypass the invariants.
5. **Each merge is reversible by design.** The record stores what the merge added, so that a false merge can be undone without data loss.
6. **The heatmap engine is local.** The matrix comes from recorded decision scores and lexical similarity. Jev never makes it. (An earlier plan let Jev produce the matrix; it was replaced by the shadow evaluation.)
7. **Measure against a golden set before a new engine decides.** Categories: exact and alias matches, acronyms, true synonyms, competing tools, version splits, homonyms. The gate is zero false merges and precision at least 98%. A synthetic harness only proves the measuring rig, not the engine.

## Planned, not implemented

- **Full reversible merge.** The rollback payload also records moved relations and a full snapshot of an absorbed entity. Rollback then reconnects relations and can add a relation (for example `competes_with` or `related_to`) between the two restored entities.
- **Soft merge of documents.** A document merge marks the loser document with a tombstone (`merged_into`, status `merged`) instead of a physical delete, so that its metadata and decision records stay.
- **Rollback in the UI.** One action in the decision stream undoes a merge. It needs tests of the rollback first.

## Rejected alternatives

- **A dedicated decision table**: schema migration and a separate life cycle from documents.
- **Decision records in the telemetry database**: telemetry rolls over after about 30 days.
- **Jev as the heatmap provider or as a sole merge judge before evaluation**: no accuracy data; see the shadow evaluation design.

## Limits for later changes

- Keep the invariants of Gate 2 in front of every merge path, also new engine paths.
- Keep every merge writing a rollback payload.
- Do not show the heatmap values as numbers; it is not an evidence view.
