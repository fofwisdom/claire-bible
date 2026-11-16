# Home Overview, Context Panel, and Local Graph

- **Status**: Implemented (units HW-1 to HW-8, 2026-11-15; application bar for all pages, 2026-11-16). Design principles only. Current behavior: [`WEBUI.md`](../implementation/WEBUI.md); code positions and limits: [`IMPLEMENTATION_STATUS.md`](../implementation/IMPLEMENTATION_STATUS.md) §1.
- **Date**: 2026-11-14 (owner decisions HD1 to HD13; HD14 on 2026-11-15) · condensed 2026-11-16
- **Section numbers in code comments**: "Section 4.1" and "Section 4.8" (application bar) are now "Application bar" below and [`WEBUI.md`](../implementation/WEBUI.md) §2. "HW-1" (header, grid, list switch, context panel) is [`WEBUI.md`](../implementation/WEBUI.md) §2 to §4. HD4 keeps its identifier.
- This document also keeps the principles of four earlier designs that it replaced: the unified home entry (2026-10-26), the compact right rail (2026-08-21), the view-contextual search field (2026-09-28), and the reader meta line (2026-09-11). Git history keeps those files and the visual prototype of 2026-11-14.

## Goal

The home page of a knowledge base answers the questions of the reader first: what is new, what is important, what needs work. The full graph answers none of them as a first screen. While the owner reads a document, the graph shows only the connections of that document. The look (tokens, fonts, cards) does not change.

## Decisions in Force

| ID | Decision | Reason |
| :--- | :--- | :--- |
| HD1 | The overview is the first screen. The full graph opens from the graph view and from the knowledge map card. | A full graph as first screen fills the center and answers no question. |
| HD2 | One list on the left side. A context panel on the right side opens only when a context exists (a document, an entity, the graph view, or a pinned form). | A fixed right pane without a selection takes width and shows only help text. This also replaces the compact icon rail: the pane has no width when it has nothing to show. |
| HD3 | The local graph of the open document is in the context panel. No split view. | The reader keeps the width of the text; the connections stay one look away. |
| HD4 | Icons are line icons in one color (`currentColor`). No emoji. | One visual language on all pages, and the icon follows the text color in both themes. Rule for all pages: [`PRINCIPLES.md`](../PRINCIPLES.md) G1. |
| HD5 | No "continue reading" card and no stored reading position. | Not necessary for the owner. |
| HD6 | Entity marks in the reader text, on the condition that they cannot change the extraction and cannot make a contamination path (see "Entity marks"). | The marks help the reader; the knowledge data must not depend on a page. |
| HD7 | The presentation and decision stream views stay as they are, with their feature switches. | Out of scope. |
| HD8 | More work for tablet widths comes after the implementation. | Open (planned, not done). |
| HD9 | The home units came before other work that was not implemented. | Priority of the owner. |
| HD10 | No new library and no script from a content delivery network. | Fewer dependencies; the platform gives popover, dialog, highlight, and container queries. |
| HD11 | Isolated entities and pairs stay on the graph canvas. | The canvas shows all data; the lists do not hide entities. |
| HD12 | No color for each relation type in the full graph. | The canvas has one color code, the entity type. A second color code makes the two codes compete, and the relation types have no stable color set. The relation type shows as text. |
| HD13 | Home title: catalog key `home.title`. | — |
| HD14 | The stage strip uses counts of routes that exist; it does not wait for later work. | A count from a route that exists is a fact; a missing route gives no count. |

Recorded changes after use (2026-11-15):

- P1: no label "model output" on the summary and the detail. Ingestion is always the work of a model, thus the label says nothing. The page shows the model of the latest extraction, because a later ingestion with a better model is possible.
- P2: the source badge stays; a cut source text has one notice, not two.
- P3: the Home button goes from the reader to the home view in one step, not through the graph view.

## Principles

- **Facts only on cards.** A card shows stored facts and counts from a route. A card without its source is hidden. No placeholder number and no estimate ([`PRINCIPLES.md`](../PRINCIPLES.md) C1, C2).
- **One home entry.** One function decides the home screen for the page load, the brand link, the Home button, and a theme switch. The server job state decides it, not a client cache: an active job of this theme opens the ingest view. The brand link is a real link, thus a new tab is a new page load.
- **Solid for facts, dashed for inferences.** A solid line marks the source text or a result of deterministic code. A dashed or dotted line marks a provisional item or a decision of a model. A color alone never carries the meaning (principle A2 made visible).
- **One function for each field.** The bar has no text field. The list pane has one filter for the list that shows. The decision stream has its own filter. The command palette (`/` key) does search, movement, and commands; Ctrl+K stays with the browser. The user never selects a technical search mode; the server selects it from the scope. A field whose function changed with the view was not clear.
- **The palette adds no write path.** Each row calls a function that exists.
- **One application bar for all pages** with an owner session (workspace, `/syndication`, console): same structure, same classes, one stylesheet, one storage key for light and dark. The bar has no input field. The brand link opens the home view.
- **Reader meta line.** The left part has the links to the source and to the copies (the remote page, the stored raw file, the transcript, the presentation PDF). The right part has the tags of the ingestion (parser, cut, focus, transcription, model). The meta line has no author and no date: those are facts of the source text and show in the Source tab. The two parts do not mix, and an action has one place (no second share or source button elsewhere).
- **Platform first for accessibility.** `<dialog>` with `showModal()`, the `popover` attribute, a real `tablist`, `inert` behind a narrow-screen sheet. The page adds no focus trap and no Escape handler where the browser gives them. A selected state shows with a line or a border, not only a color.

## Entity Marks (HD6)

Investigation result: a mark has no path to the extraction input, a stored body, an index, or a prompt, when these controls hold.

1. The server makes no mark. Stored bodies and the routes that give them do not change.
2. The marks use the CSS Custom Highlight API. The document tree of the text does not change (no element, attribute, or text is added). A browser without the API shows no marks; the page does not wrap text as a fallback.
3. Match rules are strict: names and aliases of the entities of the open document only; no stem, no partial word, no similarity; a string of two entities gets no mark; short ASCII names match with case. A wrong visual match is the risk that stays, and the legend explains the marks.
4. A click on a mark only selects the entity. It starts no write.
5. Entity names are untrusted text (they come from a model that read remote content). They never go into an HTML string.
6. The marks are a visual aid only. Each entity is also a button in the Entities tab and the local graph.
7. Size limits switch the marks off for a very long text or very many names.

## Rejected Alternatives

| Alternative | Reason |
| :--- | :--- |
| Full graph as home (earlier home entry design) | HD1. |
| Compact icon rail for the right pane | HD2: the pane closes instead. |
| One header search field whose function changes with the view, with manual full-text and semantic switches | One field with a different function in each view was not clear; technical modes are a server decision. |
| Split view and "no graph" options of the prototype | HD3. |
| Marks written into the stored body, or elements around the text | Contamination path into prompts, share page, and export (HD6). |
| A second graph engine instance for the local graph or the mini map | The local graph is a small SVG; the mini map uses the layout code of the engine without its renderer. One layout in the product. |
| Undecided proposals of the earlier graph exploration design (ego network mode, community overview, time or hierarchy layout, WebGL renderer) | Never decided by the owner; the home overview and the local graph replaced their purpose. A new proposal needs a new design. |

## Limits for Later Changes

- Do not add a card or a count without a route that gives it.
- Do not put a mark, a label, or any page-only data into a stored body.
- Do not add a second color code to the graph canvas.
- Each new text uses catalog keys from the start ([`I18N_DESIGN.md`](I18N_DESIGN.md) ID5).
