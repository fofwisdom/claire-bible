# Knowledge Graph Linking, Calibration

- **Status**: Implemented (Phase 1 and Phase 2). Design principles only. Implemented: score tiers in entity resolution, entity frame embedding, task types, adaptive centering, rank fusion in search, the cross-document relation judge, and the `re-embed` and `link-relations` commands. Current behavior: [`EXTRACTION.md`](../implementation/EXTRACTION.md) §4.5, §5, and §6. Phase 3 (missing links over two hops) is now an idea: [`MISSING_LINK_DISCOVERY_IDEAS.md`](../ideas/MISSING_LINK_DISCOVERY_IDEAS.md) (owner, 2026-11-20).
- **Date**: 2026-09-16 · restructured 2026-11-16

## Goal / problem

The graph must connect knowledge across documents, not only summarize one document. The old resolver had two limits only (auto merge and judge floor). A candidate below the judge floor was dropped, also when it was clearly related. The entity vector came from the name and the type only, so observations had no effect. The embedding space showed hubness: some common words were near many nodes. Search, indexing, and similarity used one task type. Better relation prompts alone could not help, because the candidate set was too small.

## Decisions in force

1. **Four score tiers, not two.** Above the auto-merge limit: same entity, no model call. Between the borderline limit and the auto-merge limit: the same-entity judge decides. Below that: a relational band (candidates for a direct edge) and a multi-hop band (candidates for later missing-link work). A candidate that the judge calls DIFFERENT is kept as a relational candidate, not dropped. Reason: "not the same" is often "related".
2. **The borderline limit stays at 0.72, not 0.82 as the original proposal said.** Reason: the old resolver judged candidates from 0.72. With 0.82, same entities with spelling or description differences in the band from 0.72 to 0.82 would become new entities without a judge (false splits).
3. **The resolution result stays compatible with `(entity, created)`.** The tier data is added to the tuple, so old callers keep working.
4. **Embed a structured entity frame.** The frame has the type, the aliases, and a few key observations. Reason: the name alone does not carry the function and the domain of the entity.
5. **Separate task types for documents, queries, and similarity.** Stored entity frames use the document type, search queries use the query type.
6. **Adaptive centering against hubness.** Subtract the mean vector of the stored vectors and normalize again before cosine similarity. Reason: it removes much of the common background similarity at low cost.
7. **Rank fusion for hybrid search.** Reciprocal rank fusion joins the FTS rank and the vector rank without weight tuning.
8. **Cross-document edges come from a relation judge with cost caps.** The judge gives a relation type from the ontology, a direction, a reason, and a confidence. It is conservative, and the ontology domain and range checks still apply. Caps for each entity and for each document, and a check for existing edges, limit the calls. A command does the same for the whole knowledge base.
9. **The embedding model is a setting, and changing it needs a full re-embed.** Vectors of different models are not comparable.

## Rejected alternatives

- **CSLS (cross-domain similarity local scaling).** It needs the mean similarity of the K nearest neighbours of each node at each search or update, an extra O(|V|·K) cost. With a local SQLite file, mean-vector centering removes enough hubness. Not adopted.
- **Runtime selection of a multilingual embedding model by text language.** Not implemented. A fixed model setting is the only control.
- **Borderline limit 0.82.** See decision 2.
