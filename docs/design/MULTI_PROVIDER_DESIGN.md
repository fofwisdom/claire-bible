# Multiple LLM Providers

- **Status**: Implemented (legacy `host-cli` code cleanup in `AE8`). Design principles only. Current behavior: [`PROVIDERS.md`](../implementation/PROVIDERS.md) §2 to §5A. For client-driven extraction (`agent_client`) and `host-cli` retirement (`AED6`, `AED8`), see [`AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md`](AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md). The TypeSafe Jev engine is now a shadow evaluator: see [`JEV_SHADOW_EVALUATION_DESIGN.md`](JEV_SHADOW_EVALUATION_DESIGN.md).
- **Date**: 2026-08-19 · condensed 2026-11-16 · updated 2026-11-20 (`AED6`, `AED8`)

## Goal / problem

Claire uses server language models for extraction, detail rendering, search synthesis, research, entity identity judgement, and relation judgement. Server deployments use an HTTPS `api-key` provider (currently Google Gemini `gemini` as the implemented baseline) or the `mock` provider in development, while client-side agent extraction uses `agent_client` ([`AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md`](AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md)). The number of provider settings grew to many environment variables. Operators had to edit `.env` files by hand, and a wrong value was hard to see.

## Decisions in force

- **`api-key` server providers only; `host-cli` retired (`AED6`, `AED8`, 2026-11-20).** Server-side LLM, STT, VLM, and embedding calls run over HTTPS `api-key` providers (currently `gemini` as the implemented baseline; `mock` in development). Server-side `host-cli` execution (`AntigravityProvider`, `CodexProvider`) and `ChatGPT`, `Codex`, `Claude` are retired; external agent extraction uses `agent_client`.
- **One place holds the provider configuration.** It is a file in the data directory, managed from the web UI and the CLI. It wins over environment variables. Reason: one source of truth, editable at runtime without a file edit on the host.
- **Old environment settings move automatically.** The first start copies them into the provider file and comments out the old lines, with the original comments kept. Reason: operators do not do manual migrations (principle E1), and a value must not exist in two places.
- **Secrets are never shown again.** The UI gets a mask. A save with the mask keeps the stored key. Only an explicit clear removes it. Reason: a model change must not lose the key, and the key must not leave the server.
- **A configuration can be tested before use.** Each provider has a connection test (key and model check). Reason: a wrong value is found at once, not at the next ingest.
- **Output is structured by a schema.** Each provider uses its native JSON schema mechanism. Reason: free text needs fragile parsing.
- **No silent fallback in production** (principle C2). A missing key or unavailable model makes the call fail. Development may use the mock provider.
- **All providers and `agent_client` pipelines share one prompt engine and schema contract.** Reason: results of different providers stay comparable, and a prompt or schema change is traceable.

## Rejected alternatives

- **Environment variables as the only configuration.** Rejected: too many variables, no runtime change, no test before use.
- **Fall back to the mock provider or to another provider when a call fails.** Rejected by principle C2: a fake result looks like a real one.
- **Run host developer CLI binaries (`AntigravityProvider` / `CodexProvider` `host-cli` subprocesses) or support `ChatGPT`, `Codex`, `Claude`.** Retired by owner decision `AED8` (2026-11-20).

## Limits for later changes

- A new server provider must be an `api-key` provider and must register its class in `src/claire/accounts/provider_policy.py` (`PROVIDER_CLASSES`). An unknown name fails closed (`C2`).
- A new provider must define its embedding behavior (the current Option B design baseline is Google Gemini Embedding with multi-year EOS backfill lifecycle; see [`AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md`](AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md)). In production, a provider without embeddings must not make fake vectors (`C2`).
