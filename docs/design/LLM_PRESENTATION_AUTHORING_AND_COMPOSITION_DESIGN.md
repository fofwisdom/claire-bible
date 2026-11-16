# Presentations: Authoring, Compilation, Viewer, and Per-Theme Switch

- **Status**: Implemented. Design principles only. Current behavior: [`PRESENTATION.md`](../implementation/PRESENTATION.md) (§4 composition, §5 compilation, §6 viewer, §8 per-theme switch).
- **Date**: 2026-10-01 (authoring design and viewer design), 2026-10-12 (per-theme switch) · condensed 2026-11-16. This document also keeps the principles of the retired `ASCIIDOCTOR_REVEALJS_PRESENTATION_UIUX_DESIGN.md` and `DATABASE_PRESENTATION_TOGGLE_DESIGN.md`.
- **Related**: [`ASCIIDOC_ENHANCEMENT_DESIGN.md`](ASCIIDOC_ENHANCEMENT_DESIGN.md) (the AsciiDoc body)

## Goal

A user can show an ingested document as a technical talk: slides for the audience and a script for the speaker. The first version split the body at its headings into slides. That gave walls of text: a body is an article for one reader, and a slide is a different kind of text.

## Decisions in Force

### Authoring

- **A deck is a new text, written by the LLM.** The LLM writes a separate AsciiDoc deck from the body: short slides with one message each, a narrative order, and visuals (tables, quotes, notes, code, images). Mechanical splitting of the body is removed and has no fallback. Reason: compression and storytelling need authoring, not cutting.
- **The speaker notes carry the depth.** Each slide has speaker notes with the context, numbers, and explanations of the source. The slide shows only the key words and the visual. Reason: reveal.js has a speaker view, and the detail of the source belongs to the speaker, not to the screen.
- **Two dimensions.** Horizontal slides are topics; vertical slides are details of a topic.
- **No facts outside the source,** as for the body.

### Compilation

- **Composition and compilation are separate stages.** Composition calls the LLM and costs tokens. Compilation is deterministic and fast. A change of the reveal.js theme, the transition, or the style recompiles without the LLM. Reason: cost, and the user can change the look freely.
- **The deck is an asset, not a cache.** It is stored, and a user or an agent can read it, edit it, and compile it again. A manual edit is a valid deck.
- **A changed body or a new prompt version makes the deck stale.** The system does not compose again by itself. Reason: composition has a cost; the user decides.
- **Compilation is isolated.** The compiler runs with a safe mode, no remote reads, a time limit, and a limit of parallel runs. Directives that read files or run code are removed from the deck before compilation. Reason: the deck is LLM output and can be edited by users.
- **One composition at a time per process, and one job per document.** Reason: a slow LLM call must not block the server.

### Viewer

- **A dedicated page per presentation,** usable alone, in a frame of the workspace, and from a share link.
- **The controls do not cover the slides.** The toolbar appears only near the top of the screen and is hidden inside the workspace frame, where the workspace has its own controls.
- **The light or dark theme follows the page or the system,** so that slides look like the rest of Claire.
- **A read request never starts composition.** Only an explicit generate or compose request starts the LLM. Reason: a page view must not spend tokens.

### Per-theme switch

- **Each theme has its own switch, and it is off by default.** Many themes (archives, logs, notes) do not need slides, and composition costs tokens. The owner turns it on where it is useful.
- **The server enforces the switch.** All presentation routes of the web API refuse a theme with the switch off. Reason: hiding a button is not a control.
- **The interface hides the entry points but does not take the view away.** A user in the presentation view of a theme with the switch off sees a static message. Reason: a forced change of view causes side effects in history, drawers, and routing.
- **Members need the tier feature for composition,** because it uses the LLM of the server.

## Rejected Alternatives

- **Mechanical split of the body at headings.** Rejected: text overflow and no narrative.
- **Compose and compile in one step.** Rejected: each change of style would call the LLM.
- **Presentations on for all themes.** Rejected: token waste and noise in themes without slides.
- **A forced change of view when the switch is off.** Rejected: see the viewer decisions.

## Limits for Later Changes

- A new route that composes or compiles must check the per-theme switch and the tier feature.
