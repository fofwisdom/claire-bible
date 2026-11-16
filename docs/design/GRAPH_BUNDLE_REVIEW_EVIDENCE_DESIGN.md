# Review Evidence in Graph Bundles (for the TypeSafe AI Jev Adoption Review)

- **Status**: Implemented. Design principles only. Current behavior: [`GRAPH_BUNDLE_REVIEW.md`](../implementation/GRAPH_BUNDLE_REVIEW.md) §2 (decision trace), §6 (bundle and review tables), §7 (limits of the evidence).
- **Date**: 2026-10-30 · condensed 2026-11-16
- **Related**: [GOALS.md](../GOALS.md) track 2 (extraction and linking quality), [JEV_SHADOW_EVALUATION_DESIGN.md](JEV_SHADOW_EVALUATION_DESIGN.md), [DECISION_STREAM_AND_HEATMAP_MATRIX_DESIGN.md](DECISION_STREAM_AND_HEATMAP_MATRIX_DESIGN.md)

## Problem

TypeSafe AI Jev (a System 1 classifier) was under adoption review. Jev had made no decisions, so no Jev data existed. A Go, No-Go, or limited-role conclusion needed two kinds of data from production:

1. **Baseline**: what the current resolution pipeline (deterministic rules, vector bands, System 2 LLM judge) decided, with which scores, and how fast.
2. **Replay input**: the material to ask Jev the same question offline.

The first graph bundle format did not carry this. It had no decision log, no heatmap origin, no `DIFFERENT` verdicts of the judge (the resolver kept only the final decision for each entity), no judge latency or input size, and no way to tell hash-fallback pseudo vectors from real embeddings.

## Review questions and evidence

| Question | Evidence |
| :--- | :--- |
| Accuracy (zero false merges, precision) | Borderline candidate pairs with the judge verdict and the decision-time input |
| Real negative pairs | Existing competitor and alternative relations |
| Effect on candidate screening | Distribution of candidate pair scores by band |
| Latency and cost | Judge call duration and input size |
| Baseline of old false merges | What each merge added (rollback payload) |
| Validity of the evidence | Thresholds, embedding validity, coverage, settings |

## Decisions in force

1. **Trace inside the existing decision record.** Each decision that enters the candidate loop keeps every candidate with its origin, score, and gate outcome, plus the input and the gate parameters of that moment. Reason: no schema change of the main database; the record already lives with the document.
2. **Evidence is separate from graph data.** The bundle keeps graph tables unchanged and adds evidence tables with their own prefix. Import never loads them into a target graph. Reason: evidence must not change a knowledge base.
3. **Scores leave the server, vectors do not.** Candidate pairs are computed offline from stored embeddings; only pair scores are exported. Reason: raw vectors stay private.
4. **Exclude invalid input from statistics.** Hash-fallback vectors and minority dimensions are removed and counted. Reason: meaningless cosine scores must not mix with real ones.
5. **Theme isolation for shared telemetry.** The telemetry database is shared by all themes. Only calls of exported documents go out as rows; calls that cannot be attributed go out only as aggregates. Reason: no data of another theme leaves in a bundle.
6. **Allow-lists, not deny-lists.** Telemetry columns are an explicit list. Secrets go out only as presence flags, and URLs lose credentials, query, and fragment.
7. **Failure isolation.** Each evidence section can fail alone and leaves an error status. The graph bundle is still made. Reason: the evidence is optional; the bundle is the main product.
8. **The judge is not ground truth.** The evidence gives a pool of pairs; people label the golden set from it.

## Rejected alternatives

- **A new table in the main database for decisions**: needs a schema migration and a separate life cycle from documents.
- **Export raw embeddings**: privacy cost; pair scores are enough for the review.
- **Load evidence on import**: mixes review data with graph data.

## Limits for later changes

- Keep evidence tables out of import.
- Keep the telemetry export theme-isolated and column-listed.
- When the decision record gets a new field that a reviewer needs, add it to the bundle and raise the review schema version.
