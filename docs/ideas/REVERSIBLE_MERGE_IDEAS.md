# Ideas: Full Reversible Merge and Rollback in the UI

- **Status**: Ideas only. They have no design for implementation. Do not implement from this document. A design starts only after an owner decision.
- **Date**: 2026-11-20
- **Source**: Taken out of [`DECISION_STREAM_AND_HEATMAP_MATRIX_DESIGN.md`](../design/DECISION_STREAM_AND_HEATMAP_MATRIX_DESIGN.md) (section "Planned, not implemented") on 2026-11-20 (owner). The design document keeps the principles of the implemented part.

---

- **Full reversible merge.** The rollback payload also records moved relations and a full snapshot of an absorbed entity. Rollback then reconnects relations and can add a relation (for example `competes_with` or `related_to`) between the two restored entities.
- **Soft merge of documents.** A document merge marks the loser document with a tombstone (`merged_into`, status `merged`) instead of a physical delete, so that its metadata and decision records stay.
- **Rollback in the UI.** One action in the decision stream undoes a merge. It needs tests of the rollback first.
