# Quality Verification Guardrails and Summary Integrity

- **Status**: Implemented. Design principles only. Current behavior: [PRINCIPLES.md](../PRINCIPLES.md) C2, [`PROVIDERS.md`](../implementation/PROVIDERS.md), and [`EXTRACTION.md`](../implementation/EXTRACTION.md) §7.
- **Date**: 2026-09-08 (first commit) · condensed 2026-11-16
- **Related**: [INGESTION_INTEGRITY_AND_POLLUTION_CONTROL_RESEARCH.md](./INGESTION_INTEGRITY_AND_POLLUTION_CONTROL_RESEARCH.md), [MULTI_PROVIDER_DESIGN.md](./MULTI_PROVIDER_DESIGN.md)

---

## 1. Problem

In production, the LLM summary was often missing, and a mock summary replaced it without a signal. The cause was hard to find from the server logs and the ingest report. At the time of writing, the analysis found five causes:

1. The provider selection fell back to mock without a log line when a binary or a key was missing.
2. The ingest pipeline and the Telegram bot wrote almost no log lines around the model calls.
3. The CLI providers hid their errors: after a failed call, a slice of the first 200 characters or a list of entity names became the summary.
4. The `extractions` table recorded `provider=mock` but not the reason, and `raw_inbox` recorded `done`, so the recover loop did not retry.
5. The ingest report had no flag for a mock or fallback summary, so the user saw a normal success.

The existing guardrails (web content guard, deduplication and tombstones, table preservation, ontology checks, the one-hop gate, and the markup check of summaries) protect against outside noise and broken syntax. None of them checked that a summary has real content. Current behavior of these checks: [`EXTRACTION.md`](../implementation/EXTRACTION.md) §3, §7 and [`ONEHOP_MERGE.md`](../implementation/ONEHOP_MERGE.md) §3; the web content guard, deduplication, and tombstones: [`INGESTION.md`](../implementation/INGESTION.md).

---

## 2. Decisions in force

1. **Fail fast in production.** A configured provider that cannot run makes the call fail. It does not fall back to mock (PRINCIPLES C2). Reason: a silent mock summary looks like a success and hides the cause.
2. **Make a missing summary visible.** The ingest report marks an empty summary, and a summary verdict goes to the separate telemetry database. Reason: the user and the operator must see a bad result without reading server logs.
