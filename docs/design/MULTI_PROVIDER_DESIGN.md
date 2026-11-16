# Multiple LLM Providers

- **Status**: Implemented. Design principles only. Current behavior: [`PROVIDERS.md`](../implementation/PROVIDERS.md) §2 to §5A. Not implemented: the `openai` LLM provider (configuration exists, no provider class; see `PROVIDERS.md` §7). The TypeSafe Jev engine is now a shadow evaluator: see [`JEV_SHADOW_EVALUATION_DESIGN.md`](JEV_SHADOW_EVALUATION_DESIGN.md).
- **Date**: 2026-08-19 · condensed 2026-11-16

## Goal / problem

Claire uses language models for extraction, detail rendering, search synthesis, research, entity identity judgement, and relation judgement. Different servers have different models available: an API key, a host CLI that is logged in, or no model at all in development. The number of provider settings grew to many environment variables. Operators had to edit `.env` files by hand, and a wrong value was hard to see.

## Decisions in force

- **One place holds the provider configuration.** It is a file in the data directory, managed from the web UI and the CLI. It wins over environment variables. Reason: one source of truth, editable at runtime without a file edit on the host.
- **Old environment settings move automatically.** The first start copies them into the provider file and comments out the old lines, with the original comments kept. Reason: operators do not do manual migrations (principle E1), and a value must not exist in two places.
- **Secrets are never shown again.** The UI gets a mask. A save with the mask keeps the stored key. Only an explicit clear removes it. Reason: a model change must not lose the key, and the key must not leave the server.
- **A configuration can be tested before use.** Each provider has a connection test (key, model, or binary). Reason: a wrong value is found at once, not at the next ingest.
- **A host CLI is an agent, so it runs isolated.** Calls that need no tools run with tools forbidden, in an empty working directory, with a concurrency limit. Only research may use tools or web search. Reason: an agent CLI that gets a simple judgement prompt starts to search files and run commands, which is slow and unsafe.
- **The Codex CLI runs only on the native host.** Its login and binary are not in the container image, so the container profile refuses it. Reason: a container cannot use the host login safely.
- **Output is structured by a schema.** Each provider uses its native JSON schema mechanism. Reason: free text needs fragile parsing.
- **No silent fallback in production** (principle C2). A missing key or binary makes the call fail. Development may use the mock provider.
- **All providers share one prompt engine and one prompt version.** Reason: results of different providers stay comparable, and a prompt change is traceable.

## Rejected alternatives

- **Environment variables as the only configuration.** Rejected: too many variables, no runtime change, no test before use.
- **Fall back to the mock provider or to another provider when a call fails.** Rejected by principle C2: a fake result looks like a real one.

## Limits for later changes

- A new provider must have a class in the provider policy. An unknown name is treated as a host CLI (fail closed).
- A new provider must define its embedding behavior. In production, a provider without embeddings must not make fake vectors.
