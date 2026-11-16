# Web UI Modularization and Static Assets

- **Status**: Implemented. Design principles only. Current behavior: [`GRAPH_ENGINE.md`](../implementation/GRAPH_ENGINE.md) §15 (page assembly and static assets).
- **Date**: 2026-09-04 · condensed 2026-11-16
- **Related**: [GOALS.md](../GOALS.md) track 3 (search, UX, web UI), [GRAPH_ENGINE_SHOPIFY_EDITIONS_ADOPTION_DESIGN.md](GRAPH_ENGINE_SHOPIFY_EDITIONS_ADOPTION_DESIGN.md)

## Problem

The web interface began as a small graph viewer in one Python module. Feature by feature (document list, reader, transcripts, synthesis, shared page, AsciiDoc, math, analytics), the module grew to about 7,000 lines of Python strings that held HTML, CSS, and JavaScript. The problems were:

- One module mixed database queries, LLM context assembly, the main single-page app, the shared viewer, and a markup parser.
- The shared page had its own copy of about 1,500 lines of parser code. A rendering fix had to be made in three places.
- Query functions were shaped for the browser. Agent interfaces (MCP) that called them got large payloads and side effects (for example, a document marked as read).
- JavaScript and CSS inside Python strings had no editor support, linting, or syntax check.

## Decisions in force

1. **No front-end build step.** No Node.js toolchain, npm bundler, or transpiler in the product. Plain browser JavaScript and CSS, assembled by Python. Reason: the container stays light and independent, and deployment needs no extra build stage.
2. **One page for documents and graph.** The graph and the reader stay in one single-page workspace. Selecting a document highlights its nodes, and clicking a node opens the document, without a page change. Reason: two-way exploration between documents and the graph is the core of the product.
3. **Data queries are separate from the view.** Queries return plain serializable data without view side effects, in a query layer that the HTTP API, the MCP tools, and the CLI share. Reason: agent interfaces must not inherit browser-only behavior.
4. **Assets are real files.** Templates, CSS, and JavaScript live as normal files. Python only reads, inlines, and fills slots. Reason: editor support and syntax checks.
5. **One renderer for each format in the browser.** The workspace and the shared page use the same browser AsciiDoc parser file. The server-side renderer is the other single source. Reason: a rendering fix is made once on each side.
6. **Self-contained pages stay possible.** The main pages can be built as one HTML string with all assets inline. Reason: the pages work without extra asset requests and keep their old contract.
7. **Compatibility with deployment and access control.** Static files are public. All other routes keep the existing access gate. Old imports of query functions from the view module keep working.

## Rejected alternatives

- **A JavaScript bundler (Vite, Webpack)**: adds a Node.js build and a second toolchain to deployment.
- **Separate URLs for the reader and the graph**: breaks the continuous two-way exploration.
- **Keep the parser copy in the shared page**: three places to fix for each rendering bug.

## Limits for later changes

- Add a new page script as a file under the static assets, and add it to the page assembly; do not put new JavaScript or CSS into Python strings.
- Keep query functions free of view side effects.
- Do not introduce a front-end build step without an owner decision.
