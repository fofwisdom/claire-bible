# TypeSafe AI Jev Shadow Evaluation

- **Status**: Shadow evaluation implemented. Jev has only the shadow role (owner decision 2026-11-20; the filter and hybrid modes of 2026-11-15 are removed). Current behavior: [`GRAPH_BUNDLE_REVIEW.md`](../implementation/GRAPH_BUNDLE_REVIEW.md) §5 (Jev client, shadow role), §6.2 (shadow evidence in bundles). The Go / No-Go evaluation (Section "Evaluation") is a process, not code. Results of each run: [`JEV_EVALUATION.md`](../implementation/JEV_EVALUATION.md) (first run 2026-11-20). Section "Routine evaluation": the backend is implemented ([`GRAPH_BUNDLE_REVIEW.md`](../implementation/GRAPH_BUNDLE_REVIEW.md) §5.5, schema v38); the frontend (a page and a request in a popup) and R7 are not.
- **Date**: 2026-10-31 · condensed 2026-11-16 · shadow only and routine evaluation 2026-11-20
- **Related**: [GOALS.md](../GOALS.md) track 2 (extraction and linking quality), [GRAPH_BUNDLE_REVIEW_EVIDENCE_DESIGN.md](GRAPH_BUNDLE_REVIEW_EVIDENCE_DESIGN.md), [DECISION_STREAM_AND_HEATMAP_MATRIX_DESIGN.md](DECISION_STREAM_AND_HEATMAP_MATRIX_DESIGN.md)

## Problem

The first review bundle (2026-10-30, production data) measured the System 2 judge for the first time: about 42 judge calls a day, call latency p50 about 10 s, up to several minutes of judge wait for one document, and only about 1.3% of judge calls gave a merge. The borderline pairs were mostly near variants (for example, two update numbers of one product). Thus the value of Jev is latency more than cost. But its accuracy on near variants could not be known without real measurement.

Two errors made the earlier picture wrong:

- The heatmap code labelled a local `difflib` matrix as `jev` without a call to Jev. Production settings had Jev on and a key, so the label was taken as Jev output.
- The old verification script read the probability from the wrong key (always 0.0) and called a path that the API does not document.

## Decisions in force

1. **Side-by-side records.** For the same candidate pairs, the record keeps the current decision (rules and System 2 judge) and the Jev probability next to each other. Reason: accuracy, latency, and cost can then be compared on the production distribution.
2. **Jev does not decide.** Merge or create, candidate order, and judge calls are the same with or without the shadow. A Jev failure never raises; it only sets a status in the record.
3. **Ask about every candidate that enters the loop.** Also candidates that the invariants rejected, that the judge limit skipped, or that came after the decision. Deterministic Gate 1 decisions are not asked. Reason: pairs that the judge never saw also get a score.
4. **One request for each decision.** All candidates of one entity go in one request, with one yes/no question each. The criteria for "different" name the near-variant types seen in the data (versions, editions, product and vendor).
5. **Query the pre-merge state.** Candidates are read before the loop. Reason: a merge adds observations to the target, and the Jev input would lean to "same".
6. **Hide the latency, contain failures.** The HTTP call runs on worker threads without database access, in parallel with the judge. No retry. A circuit breaker stops calls for a while after repeated failures. Gaps show in the status counts.
7. **Bound the input.** Entity summaries are cut so that each question stays within the per-question context limit of the API.
8. **Pin the model for an evaluation.** The alias `jev-latest` can move during an evaluation; pin a version for a clean comparison.
9. **Only the server administrator account.** Jev evaluation is an administrator feature. The jobs of the administrator account call Jev with the server credentials and with the personal key of the administrator (owner decision 2026-11-20; before, the personal key context had no Jev and the data stopped on 2026-11-19). Member contexts never call Jev.
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

## Decision role (owner, 2026-11-20)

Jev has only the shadow role, whatever the administrator selects. The filter and hybrid modes (owner decision 2026-11-16) are removed. Reason: the first evaluation (2026-11-20) gave No-Go; 3 positives cannot show the precision for a decision role. A later role change needs a new owner decision, based on the routine evaluation below.

## Routine evaluation (design, 2026-11-20; backend implemented)

### Problem

The first evaluation took a manual analysis of a bundle by an agent, and the agent gave the labels. Only 3 of 1,653 pairs were the same entity (0.18%): a random sample finds almost no positives, and the gate needs about 150 positives (zero errors, 95% lower bound 98%). The evaluation must run all the time, at low cost for the owner, and the labels must come from a person.

### Principles

R1. **Collect all the time.** The shadow runs on every job of the administrator account (decision 9). An evaluation period has one Jev model version (decision 8); a new version starts a new period, and the report splits by version (the record has `model`).

R2. **Queue the pairs that carry information.** A pair goes to the review queue for one or more of these reasons, in this order of priority:

| Reason | Rule | Why |
| :--- | :--- | :--- |
| `disagree` | Jev ≥ 0.5 and judge different, or Jev < 0.5 and judge same | Errors of one engine are here |
| `invariant` | Jev ≥ 0.5 and Gate 2 rejected the pair | False type rejects (example: Antrea CNI and Antrea) |
| `merged` | Each merge by the judge or by the vector gate, with or without a Jev score | False merges of the current pipeline (example: Gorgon Halo → Strix Halo) |
| `near_variant` | Names that contain each other, share most tokens, or differ only in a version or edition mark | Most positives and most hard negatives are here |
| `sample` | A fixed share of the other scored pairs (seeded) | Base rate and missed positives |

A pair is in the queue once; the record keeps all its reasons.

R3. **A third engine gives a pre-label; a person gives the label** (owner proposal and decision 2026-11-20). The pre-label engine is the Gemini Decisions API (`gemini-decision-1`): one request for each decision, one `noul` question for each queued pair, the same rule text as R6. The person sees the pre-label and accepts it or changes it. Conditions from the API documentation:
- The API returns probabilities only (`noul` 0.01 to 0.99 and `confidence`), no reason. Thus the card shows the evidence (names, types, aliases, observations, and links to the source documents of both entities), not a reason.
- The API has no web search and no stated knowledge cutoff. A new product name (as in the Astra pair) can be outside its knowledge. A pre-label is never a label: only the verdict of a person counts in the report.
- No paid-tier rule (owner decision 2026-11-20): the data is public information for the mosaic intelligence of people, thus the use of the inputs to improve the products is acceptable. The data region is not stated. The data goes to Google, as decision 11 says for Jev; only data of the jobs of the administrator account (decision 9).
- The judge of the administrator is also a Gemini model. The report shows the agreement of the pre-label with the judge, so that a shared error of the two Google models can be seen.
- Anchoring: a person who sees a pre-label agrees more often. Three of ten pairs of item and account show no pre-label until the person answers (owner decision 2026-11-20); the report compares the agreement rate of the two groups.

R4. **Verdicts from all users** (owner decision 2026-11-20). The verdicts of the administrator and the moderators alone come slowly and give few measurements. Each signed-in account can help: later a dedicated page, or a request in a popup that the user can answer. The backend comes first; the frontend comes later. An account gets only items of the knowledge bases that it can read. Verdicts `same`, `different`, `unsure`, an optional note, the account and the time; one verdict for each item and account. A table in the server database keeps the queue items (with the entity snapshots, so that an item stands alone after later merges), and a second table keeps the verdicts. The label of an item is the consensus of the verdicts (agent decision: at least 2 verdicts `same` or `different`, and 2/3 of them agree). The CLI only lists and reports. Pre-labels are requested in batches when an account gets items: no background process, and no cost when nobody reviews.

R5. **The report comes from the records and the verdicts.** For each model version and period: pairs, scored pairs, Jev latency p50 and p95, input tokens; Jev precision, recall, and false-positive rate at 0.5, 0.7, and 0.9 with the 95% Wilson lower bound; the judge on the same pairs; false merges of the pipeline; the filter simulation; positives with a verdict against the about 150 that the gate needs; the agreement of the pre-label with the person and with the judge; the queue size. The console card shows it, and the bundle gets the verdict table and the report (review schema 4). The report shows the gate result; it changes no role.

R6. **One rule for "same entity".** The person, Jev, the judge, and the pre-label engine must use one rule, else the comparison measures the rule difference. Owner decision 2026-11-20: the rule of the Jev criteria (versions, editions, a product and its vendor, and a broad and a narrow concept are different) is the rule of all; the judge prompt takes it. One rule text in one place (`same_entity_rule.py`); each verdict keeps the rule version.

R7. **A judge failure is not a judge answer.** Now a failed judge call returns `False` and the trace records `judge_different` (all providers). Before the report counts the judge, the trace must record a failure as its own outcome.

### Facts found for this design

- The console counters "JEV 텔레메트리" (`ProviderManager.get_lineage_summary`) read the tables `review_shadow_pairs` and `resolution_decisions` from the server database. These tables exist only in a bundle, thus the counters always show 0. The report of R5 replaces them.
- The Google AI Studio provider writes no telemetry rows, thus the judge calls of that provider are not in `review_judge_calls` of a bundle. The bundle (review schema 3) has the judge latency from the trace for all providers.
- The candidate state in a bundle is the state at bundle time, not at decision time. The review card shows the current state and the observation count at decision time (`cand_obs_count`). [unverified] How often a later merge changes a candidate so much that the verdict changes.

### Decisions of the owner (2026-11-20)

1. R6: one rule, the rule of the Jev criteria; the judge prompt takes it.
2. R3: the Gemini Decisions API gives the pre-label; no paid-tier rule.
3. R3: three of ten without the pre-label first.
4. R4: verdicts from all users who can read the knowledge base of an item; backend first.

## Limits for later changes

- Keep the shadow free of decision effects.
- Keep Jev calls free of database access and free of exceptions to the resolver.
- When the request or the parser changes, change the verification script with it; both use the same client code.
