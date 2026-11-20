# Jev Evaluation Results

Basis: graph bundle `gb_20261120_111718_209fa41d` (2026-11-20, production, knowledge base 0) · Process: [`JEV_SHADOW_EVALUATION_DESIGN.md`](../design/JEV_SHADOW_EVALUATION_DESIGN.md) section "Evaluation" · Code: [`GRAPH_BUNDLE_REVIEW.md`](GRAPH_BUNDLE_REVIEW.md) §5

This document records the result of each run of the Jev evaluation process. Evidence tags follow [`PRINCIPLES.md`](../PRINCIPLES.md) F1: [measured], [decision], [unverified].

---

## 1. Run of 2026-11-20

### 1.1 Data [measured]

- Window: 2026-10-30 to 2026-11-18 (first and last decision with a Jev record).
- 283 decisions with a Jev record, all with status `ok`. 1,795 candidate pairs; 1,653 have a Jev probability. The other 142 pairs were not sent to Jev (in filter mode, only the borderline candidates go to Jev).
- Model: the setting is `jev-latest` (not pinned, design decision 8), but all 283 records show `jev-1.14.0`. Thus the run has one model version.
- Mode: shadow, then filter from 2026-11-16 to 2026-11-18 (331 `jev_filtered` pairs). Hybrid mode was not used (0 `jev_hybrid_auto_merge` decisions).
- No Jev record after 2026-11-18. Since 2026-11-19 the jobs of the administrator account use the personal key of the administrator, and that context does not call Jev (commit `b73e3ec`). Thus no new evaluation data comes in now.

### 1.2 Step 1: pairs [measured]

| Outcome | Pairs with a Jev probability |
| :--- | :--- |
| `judge_different` | 475 |
| `judge_same` | 2 |
| `invariant_reject` | 438 |
| `judge_cap` | 403 |
| `jev_filtered` | 331 |
| `not_reached` | 4 |

477 pairs have a judge answer (the design asks for at least 200). 1,176 pairs did not go to the judge.

### 1.3 Step 2: labels

- Labeler: an agent. Labels are not yet confirmed by the owner.
- Rule: the shared rule of the Jev criteria (`SAME_ENTITY_CRITERIA` in `src/claire/extract/jev.py`). SAME: the same referent with a different spelling, alias, abbreviation, translation, or name. DIFFERENT: different versions, releases, editions, or variants; a product and its vendor; a broad concept and a narrower one.
- Scope: all 1,653 pairs with a Jev probability (a full count, more than the design asks for). 541 pairs got a check of names, types, aliases, and observations: all pairs with Jev ≥ 0.2 (18), the pairs that the judge merged (2), the pairs with similar names (403), and a random sample (120, seed 20261120). The other 1,112 pairs got a check of names; no pair needed more data.
- Result: 3 SAME, 1 UNSURE, 1,649 DIFFERENT.

| Pair (new → candidate) | Jev | Outcome | Label |
| :--- | :--- | :--- | :--- |
| Antrea CNI (Tool) → Antrea (Framework) | 0.97 | `invariant_reject` | SAME |
| Orchestrator → Orchestrator in Google Antigravity | 0.90 | `judge_same` (merged) | SAME |
| 3.8 Flash → Gemini 3.8 Flash | 0.79 | `judge_same` (merged) | SAME |
| Data Center Investment → Data Center | 0.65 | `judge_different` | DIFFERENT |
| atenet-egress → Agent Gateway | 0.62 | `judge_different` | DIFFERENT |
| Astra (Tool, alias "GPT 아스트라") → GPT-6 Astra (Model) | 0.09 | `invariant_reject` | UNSURE (owner check) |

- Known suspect merges: the bundle has 21 merge decisions by the judge or by the vector gate. Only 2 of them have a Jev probability (the 2 rows `judge_same` above). Section 1.6 lists the others.

### 1.4 Step 3: measurements [measured]

Jev on all 1,653 pairs (3 positives):

| Threshold | TP | FP | FN | Precision (95% Wilson lower bound) | Recall |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 0.5 | 3 | 2 | 0 | 0.60 (0.23) | 1.00 |
| 0.7 | 3 | 0 | 0 | 1.00 (0.44) | 1.00 |
| 0.9 | 2 | 0 | 1 | 1.00 (0.34) | 0.67 |

Jev and the judge on the same 477 judged pairs (2 positives):

| Engine | TP | FP | FN |
| :--- | :--- | :--- | :--- |
| Judge | 2 | 0 | 0 |
| Jev at 0.5 | 2 | 2 | 0 |
| Jev at 0.9 | 1 | 0 | 1 |

If the Astra pair is SAME, Jev recall on all pairs at 0.5 becomes 0.75. The judged pairs do not change.

| Item | Jev | Judge (`judge_same_entity`) |
| :--- | :--- | :--- |
| Latency | p50 209 ms, p95 509 ms, max 1,780 ms (one request for each decision) | p50 10.2 s, p95 13.0 s (each candidate) |
| Input | 610,996 tokens, 2,159 for each decision | 519 calls in the window, 966,938 characters, 1,863 for each call |

### 1.5 Step 4: gate

| Gate item | Result |
| :--- | :--- |
| Zero false merges | Fail at 0.5 (2 FP). Pass at 0.7 and 0.9. |
| Precision ≥ 98% | Not shown. With 3 positives and 0 errors, the 95% lower bound is 0.44. With zero errors, about 150 positives are necessary for a lower bound of 98% (rule of three). |
| p95 < 500 ms | Fail (509 ms). |

Result: **No-Go** for a role that decides a merge.

### 1.6 Step 5: role

- (a) Replace the judge: not supported. The positives are too few to show precision.
- Hybrid automatic merge (≥ 0.90): not supported, for the same reason. Hybrid mode was not used.
- (b) Filter before the judge: supported, with weak evidence.

| Filter threshold x | Judge calls skipped (of 477) | Positives lost |
| :--- | :--- | :--- |
| 0.1 | 448 | 0 |
| 0.2 (default) | 460 | 0 |
| 0.3 | 470 | 0 |
| 0.5 | 473 | 0 |

The lowest Jev probability of a positive is 0.79. Thus 0.20 keeps a large margin. A filter error causes a missing merge (a duplicate node that a later merge can repair), not a false merge. The evidence is 2 positives in the judged population.

### 1.7 Other findings

1. **Jev finds false invariant rejects.** Antrea CNI (Tool) and Antrea (Framework) are one thing (Jev 0.97), but the type check rejected the pair before the judge. In filter and hybrid modes the resolver does not ask Jev about invariant rejects, so this signal is lost there (design decision 3 is true only in shadow mode).
2. **The judge rule and the Jev rule are not the same.** The Jev criteria say that versions, editions, and broad and narrow concepts are DIFFERENT. The judge prompt (`judge_same_entity_prompt` in `src/claire/extract/prompts.py`) has no such rule. Under the Jev rule, these merges are suspect (owner check):
   - a different product: Gorgon Halo → Strix Halo; VMware Telco Cloud Infrastructure → VMware Telco Cloud Platform (vector gate, 0.932, 2026-11-19);
   - an edition or a narrower concept: Microsoft Outlook → Outlook for Windows; Modern Authentication → AD FS Modern Authentication;
   - a version into its family: Claude Mythos 5.1 → Mythos, Claude Mythos Preview → Mythos, Kimodo-SOMA-RP-v1.1 → Kimodo, CWE-bench v1 → CWE-Bench, Bones Rigplay 1 → Bones Rigplay.
   If the owner confirms them, the current pipeline also fails the gate item "zero false merges".
3. **Positives are rare.** 3 of 1,653 pairs (0.18%). One more evaluation of the same size does not change the gate result. A gate on precision needs a labeled set of near variants (design: versions, editions, product and vendor) of about 150 positives.

### 1.8 Open items for the owner

- Confirm the labels: the Astra pair, and the suspect merges in section 1.7 item 2.
- Choose the Jev role: keep filter at 0.20, or go back to shadow.
- Choose whether the jobs of the administrator account with a personal key call Jev, so that evaluation data comes in again.
- Choose one rule for "same entity" for the judge and for Jev.
