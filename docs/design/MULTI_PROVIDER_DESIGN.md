# Server LLM Providers (Google-Only Policy)

- **Status**: Implemented (legacy code cleanup in `AE8`). Design principles only. Current behavior: [`PROVIDERS.md`](../implementation/PROVIDERS.md) §2 to §5A. For client-driven Antigravity extraction (`agent_client`) and host-CLI retirement (`AED6`, `AED8`), see [`AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md`](AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md). The TypeSafe Jev engine is now a shadow evaluator: see [`JEV_SHADOW_EVALUATION_DESIGN.md`](JEV_SHADOW_EVALUATION_DESIGN.md).
- **Date**: 2026-08-19 · condensed 2026-11-16 · updated 2026-11-20 (`AED6`, `AED8`)

## Goal / problem

Claire uses server language models for extraction, detail rendering, search synthesis, research, entity identity judgement, and relation judgement. Different server deployments operate with a Google Gemini API key or a development mock provider (when client-side extraction is used instead, see [`AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md`](AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md)). The number of provider settings grew to many environment variables. Operators had to edit `.env` files by hand, and a wrong value was hard to see.

## Decisions in force

- **Google-only server providers (owner decisions `AED6` and `AED8`, 2026-11-20).** Server-side LLM, STT, VLM, and embedding calls use **Google Gemini (`gemini`)** in production and `mock` in development only. Server-side host-CLI execution (`AntigravityProvider`, `CodexProvider`) and non-Google providers (ChatGPT, Codex, OpenAI, Claude) are retired (`agent_client` replaces server-side CLI invocation).
- **One place holds the provider configuration.** It is a file in the data directory, managed from the web UI and the CLI. It wins over environment variables. Reason: one source of truth, editable at runtime without a file edit on the host.
- **Old environment settings move automatically.** The first start copies them into the provider file and comments out the old lines, with the original comments kept. Reason: operators do not do manual migrations (principle E1), and a value must not exist in two places.
- **Secrets are never shown again.** The UI gets a mask. A save with the mask keeps the stored key. Only an explicit clear removes it. Reason: a model change must not lose the key, and the key must not leave the server.
- **A configuration can be tested before use.** Each provider has a connection test (key and model check). Reason: a wrong value is found at once, not at the next ingest.
- **Output is structured by a schema.** The provider uses its native JSON schema mechanism. Reason: free text needs fragile parsing.
- **No silent fallback in production** (principle C2). A missing key or unavailable model makes the call fail. Development may use the mock provider.
- **All compatible server and `agent_client` pipelines share one prompt engine and schema contract.** Reason: results stay comparable, and a schema change is traceable.

## Rejected alternatives

- **Environment variables as the only configuration.** Rejected: too many variables, no runtime change, no test before use.
- **Fall back to the mock provider or to another provider when a call fails.** Rejected by principle C2: a fake result looks like a real one.
- **Run host developer CLI binaries (`agy`, `codex`) as server subprocesses or support non-Google LLM backends.** Retired by owner decisions `AED6` and `AED8` (2026-11-20).

## Limits for later changes

- Production server providers are restricted to Google Gemini (`gemini`) and must fail closed on unknown or unsupported names (`C2`).
- Embedding behavior follows Google-only Option B (Gemini Embedding with multi-year EOS backfill lifecycle; see [`AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md`](AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md)). In production, missing embedding capability must never generate fake vectors.
