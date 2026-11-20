# TypeSafe AI Jev Shadow Evaluation

- **Status**: Shadow evaluation implemented; design principles only. Current behavior: [`GRAPH_BUNDLE_REVIEW.md`](../implementation/GRAPH_BUNDLE_REVIEW.md) §5 (Jev client, shadow, filter and hybrid modes), §6.2 (shadow evidence in bundles). The filter and hybrid modes (2026-11-15) were added after this design and give Jev a decision role when an administrator selects them (owner decision, implemented). The Go / No-Go evaluation (Section "Evaluation") is a process, not code. Results of each run: [`JEV_EVALUATION.md`](../implementation/JEV_EVALUATION.md) (first run 2026-11-20).
- **Date**: 2026-10-31 · condensed 2026-11-16
- **Related**: [GOALS.md](../GOALS.md) track 2 (extraction and linking quality), [GRAPH_BUNDLE_REVIEW_EVIDENCE_DESIGN.md](GRAPH_BUNDLE_REVIEW_EVIDENCE_DESIGN.md), [DECISION_STREAM_AND_HEATMAP_MATRIX_DESIGN.md](DECISION_STREAM_AND_HEATMAP_MATRIX_DESIGN.md)

## Problem

The first review bundle (2026-10-30, production data) measured the System 2 judge for the first time: about 42 judge calls a day, call latency p50 about 10 s, up to several minutes of judge wait for one document, and only about 1.3% of judge calls gave a merge. The borderline pairs were mostly near variants (for example, two update numbers of one product). Thus the value of Jev is latency more than cost. But its accuracy on near variants could not be known without real measurement.

Two errors made the earlier picture wrong:

- The heatmap code labelled a local `difflib` matrix as `jev` without a call to Jev. Production settings had Jev on and a key, so the label was taken as Jev output.
- The old verification script read the probability from the wrong key (always 0.0) and called a path that the API does not document.

## Decisions in force

1. **Side-by-side records.** For the same candidate pairs, the record keeps the current decision (rules and System 2 judge) and the Jev probability next to each other. Reason: accuracy, latency, and cost can then be compared on the production distribution.
2. **In shadow mode, Jev does not decide.** Merge or create, candidate order, and judge calls are the same with or without the shadow. A Jev failure never raises; it only sets a status in the record.
3. **Ask about every candidate that enters the loop.** Also candidates that the invariants rejected, that the judge limit skipped, or that came after the decision. Deterministic Gate 1 decisions are not asked. Reason: pairs that the judge never saw also get a score.
4. **One request for each decision.** All candidates of one entity go in one request, with one yes/no question each. The criteria for "different" name the near-variant types seen in the data (versions, editions, product and vendor).
5. **Query the pre-merge state.** Candidates are read before the loop. Reason: a merge adds observations to the target, and the Jev input would lean to "same".
6. **Hide the latency, contain failures.** The HTTP call runs on worker threads without database access, in parallel with the judge. No retry. A circuit breaker stops calls for a while after repeated failures. Gaps show in the status counts.
7. **Bound the input.** Entity summaries are cut so that each question stays within the per-question context limit of the API.
8. **Pin the model for an evaluation.** The alias `jev-latest` can move during an evaluation; pin a version for a clean comparison.
9. **Only administrators.** Jev evaluation is an administrator feature; member contexts never call Jev.
10. **The heatmap is never Jev output.** The stub was removed; old `jev` heatmaps are marked as stub output in bundles.
11. **Data leaves the server.** With Jev on, names, types, aliases, and short observations of the new entity and its candidates go to the Jev API (hosted in the United States). Source documents do not. Turning Jev off stops this.

## Evaluation (planned process)

1. Collect at least 200 judged pairs, plus pairs that the judge did not see (judge limit, invariant reject).
2. Label all pairs where Jev and the judge disagree, a random sample, and known suspect merges.
3. Measure false-positive rate, precision, recall, Jev latency p95, and input token cost, for Jev and for the judge on the same pairs.
4. Gate (same as the verification script): zero false merges, precision at least 98%, p95 below 500 ms.
5. Choose the role from the data: (a) replace the judge, (b) filter before the judge (skip the judge below a probability x, with x chosen so that no `judge_same` pair is lost), or (c) reject.

## Rejected alternatives

- **Give Jev decisions before evaluation**: a false merge damages the graph permanently, and there was no accuracy data.
- **Retry failed calls**: adds latency to ingest; the gaps are visible anyway.
- **Separate call for each candidate**: more requests; the API evaluates many questions on one shared state.

## Decision role (owner, 2026-11-16)

The filter and hybrid modes are implemented and an administrator may select them. They let Jev skip judge calls and, in hybrid mode, merge without the judge. The default stays shadow. The evaluation above is a tool for the administrator, not a gate in the code.

## Limits for later changes

- Keep shadow mode free of decision effects.
- Keep Jev calls free of database access and free of exceptions to the resolver.
- When the request or the parser changes, change the verification script with it; both use the same client code.
