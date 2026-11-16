# Graph Engine: Adoption of the Shopify Editions Approach

- **Status**: Implemented. Design principles only. Current behavior: [`GRAPH_ENGINE.md`](../implementation/GRAPH_ENGINE.md) (all sections; the limits are in §13).
- **Date**: 2026-10-30 · condensed 2026-11-16
- **Related**: [GOALS.md](../GOALS.md) (UI and UX), [GRAPHVIEW_MODULARIZATION_AND_STATIC_ASSET_DESIGN.md](GRAPHVIEW_MODULARIZATION_AND_STATIC_ASSET_DESIGN.md). P5 (label level of detail) and P7 (stable coordinates) are goals of the earlier web UI redesign document, which is now only in git history.

## Problem

The old graph (a third-party network library with a force layout) became a hairball at a few hundred nodes. The coordinates changed at each load, and the edges disappeared during zoom and drag. The home graph of Shopify Editions Fall '26 shows hundreds of items as ordered hub-and-spoke stars and reacts physically to the cursor and to drag. The owner wanted that quality for the Claire knowledge graph.

## Decisions in force

1. **Own engine, no third-party graph library.** Claire has its own small graph engine without external dependencies or CDN scripts. Reason: style changes on a library cannot give hierarchical physics (tethers, orbits, throw and recover) or analytic anti-aliased rendering.
2. **Same API surface as the old library.** The engine gives the data-set and network methods and events that the workspace already used. Reason: the workspace logic (search, filters, paths, isolation, document links) stays almost unchanged.
3. **Clean-room reimplementation.** The Shopify bundle is copyrighted. Claire follows only the structure and techniques seen on the public page. All code and shaders are new.
4. **Only interaction and visual clarity are adopted** [owner decision]. Claire takes the mouse and touch interactions and the clear visual style. It does not force the look: for example, no degree numbers on entities to imitate the reference.
5. **The background follows the app theme** [owner decision]. The graph uses the light or dark theme of the app. It is not a fixed dark canvas.
6. **A general graph becomes a shallow spanning forest.** A knowledge graph is not a tree. The engine picks hubs, attaches each node to one main parent, and folds the forest to two levels below a hub. Edges outside the forest are drawn, not simulated, and their count pulls related stars near each other. Reason: deep chains make one star as large as the screen and push the zoom to its minimum.
7. **Deterministic layout.** Placement comes from seeded hashes, so the same data gives the same map. A change of visibility (filters, isolation) keeps the forest and only repacks the visible stars. Reason: the map must not jump when the user changes a filter (client half of P7).
8. **Stars are packed from the center.** Stars go next to already placed stars, near the center and near related stars, in the aspect ratio of the free graph area. Unconnected nodes and two-node pairs go to outer orbits that the engine computes; the app gives only band and order. Reason: a ring of hubs leaves the center empty and shrinks the zoom when the data grows.
9. **The engine owns stabilization.** The app does not switch physics on and off and does not pin dropped nodes. The first view appears already settled. Reason: the old toggles fought the hierarchical physics.
10. **GPU first, Canvas2D always available.** Both renderers draw the same renderer-neutral frame. The engine falls back on failure or device loss.
11. **Context stays visible on hover.** Hover lightens the neighborhood and dims the rest only partly, so that the user keeps the context.
12. **Respect the user and the device.** Reduced-motion turns off continuous motion. Idle time lowers the frame rate, and a hidden tab stops the loop.

## Rejected alternatives

- **Keep the old library and change its style**: no hierarchical physics, no analytic rendering quality.
- **sigma.js with graphology**: good WebGL performance, but the hierarchical physics and interactions must still be written, and the CDN dependencies grow.
- **Hubs on a ring**: the center becomes empty and the zoom falls to the minimum on real data.

## Limits for later changes

- Do not add an external graph dependency or a CDN script.
- Keep both renderers able to draw every node shape and edge style.
- Measure layout changes on data of production size (a graph bundle in development mode), not only on the small end-to-end seed. Never use the production server for this.
- Deferred work (server-side coordinate cache for the second half of P7, spatial index for hover and labels at thousands of nodes) is listed as a limit in the implementation document.
