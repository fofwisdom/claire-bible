# Video Ingestion: Publisher Captions First, Speech-to-Text Second

- **Status**: Implemented (publisher captions, Gemini STT, media cache). Design principles only. Current behavior: [`INGESTION.md`](../implementation/INGESTION.md) §9; STT provider selection: [`PROVIDERS.md`](../implementation/PROVIDERS.md). Other STT providers (Whisper, cloud speech, local models) are not implemented and have no design.
- **Date**: 2026-09-02 · condensed 2026-11-16
- **Related**: [`VIDEO_PRESENTATION_BUNDLE_INGESTION_DESIGN.md`](VIDEO_PRESENTATION_BUNDLE_INGESTION_DESIGN.md)

## Goal / problem

Conference and seminar video pages have almost no text: a title and a short description. The knowledge is in the speech. Some players give captions only as links to separate files, some give only thumbnail tracks, and some give no captions. Claire must get the spoken text at the lowest cost and with clear provenance.

## Decisions in force

1. **Publisher captions come first.** A valid caption in a preferred language is used as it is, and no audio is downloaded. Reason: publisher captions are cheaper, faster, and usually more correct than a machine transcript.
2. **Select captions by fixed rules.** Exact language match before a primary-language match; manual captions before automatic captions; the most direct track form first. Validate that a track has timed speech cues, and refuse thumbnail tracks. Reason: the same input must give the same transcript.
3. **STT runs only when no valid caption exists**, and only when the operator enables it. Reason: STT is the expensive path.
4. **An advertised caption that cannot be downloaded is an error, not a reason to run STT.** Reason: a silent switch to STT hides a defect and spends quota.
5. **Record the transcript source** (manual caption, automatic caption, or STT), the caption language and hash, and STT errors. Do not store caption URLs or signed tokens. Reason: provenance must be clear; signed URLs are secrets and expire.
6. **STT providers sit behind one interface.** Gemini is the only real provider now. A provider that cannot run fails the job; it does not fall back to a mock in production. Reason: no silent fallback that drives behavior.
7. **Respect the STT rate limit by design.** Audio goes in short chunks with a pause between calls and a bounded retry that reads the server delay. Reason: the transcribe model has a small per-minute token limit.
8. **Keep downloaded media for a short time after an STT failure.** A retry then uses the local file and does not download again. Reason: large downloads are slow and can fail; STT failures are often temporary.
9. **A video document with no transcript and no other content is not extracted.** Reason: a page shell gives an empty or wrong graph.

## Rejected alternatives

- **STT for every video.** It costs quota where publisher captions exist.
- **A registry of separate stream resolvers per platform.** The first design had one. The code uses one resolver function and `yt-dlp`, which covers the platforms in use.

## Limits for later changes

- A new STT provider must give timed segments, so that the transcript keeps time anchors.
- A new platform must keep the order: captions, then STT.
