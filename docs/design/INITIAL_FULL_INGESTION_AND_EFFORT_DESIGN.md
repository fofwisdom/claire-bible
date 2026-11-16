# Full Content and Reasoning Effort at First Ingestion

- **Status**: Implemented. Design principles only. Current behavior: [`INGESTION.md`](../implementation/INGESTION.md) §7.
- **Date**: 2026-09-02 · condensed 2026-11-16
- **Related**: [`PDF_INGESTION_AND_ADAPTIVE_EFFORT_DESIGN.md`](PDF_INGESTION_AND_ADAPTIVE_EFFORT_DESIGN.md)

## Goal / problem

Before this design, a user could ask for full text and a higher effort only when regenerating a document that was already stored. A first ingestion always cut the text twice: once at fetch time and again when the prompt was made. A long law text or a long specification lost its end at the first ingestion.

## Decisions in force

1. **One request gives full text and the requested effort at the first ingestion.** Reason: a second pass to repair a cut document wastes quota and time, and the user knows at input time that the text is long.
2. **The same two options exist on every input channel** (CLI, Telegram, HTTP API), with the same meaning. Reason: all channels use one ingestion pipeline; options must not depend on the channel.
3. **Full text is not cut at fetch time, in storage, or in the prompt.** A safety cap on the prompt stays, to protect the model context. Reason: the stored source and the prompt view must agree.
4. **Record what was applied.** The document metadata records full content and the applied effort. Reports and the reader show them. Reason: a reader must know if a detail text came from the full source or from a cut source.
5. **A detail text does not describe sections that the cut damaged.** When the text is cut, the detail covers only complete sections. Reason: a guess about lost text is worse than no text.

## Rejected alternatives

- **Full text only through regeneration.** It was the state before this design. It needs two ingestions for one source.
- **No cap for full text in the prompt.** A very long source can exceed the model context and fail the whole extraction.

## Limits for later changes

- A new input channel must accept both options and pass them to the shared pipeline.
- A new budget must keep fetch, storage, and prompt in agreement for full-content documents.
