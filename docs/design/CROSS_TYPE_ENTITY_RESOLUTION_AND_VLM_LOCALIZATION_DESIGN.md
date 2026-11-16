# Cross-Type Entity Resolution and VLM Language

- **Status**: Implemented. Design principles only. Current behavior: [`EXTRACTION.md`](../implementation/EXTRACTION.md) §4 (entity resolution) and §2.3 (content languages).
- **Date**: 2026-10-16 · condensed 2026-11-16

## Goal / problem

The extraction model often gives a narrative or abstract name the generic type `Concept`, while the graph already has the same name as `Person`, `Org`, or `Tool`. The old resolver rejected every candidate with a different type, also on an exact name match or an alias match. Thus the same subject became two isolated nodes, and the ingest report showed "new N / linked 0". The same-entity judge prompt also had a software-tool bias, so it called translated names (for example `Aha` and `아하`, `Trailblazer` and `개척자`) DIFFERENT. A separate VLM language setting existed next to the project-wide preferred languages and could disagree with it.

## Decisions in force

1. **An exact normalized name match merges, whatever the types.** In one theme, two entities with the same normalized name are one subject. A type difference from the model is noise, not a different subject. The version and two-letter acronym checks still apply.
2. **Generic types are compatible with all types.** `Concept` and `Note` can merge with any specific type through an alias, a vector score, or the judge. Reason: the generic types are the usual escape of the model, and they must not block a merge.
3. **A merge refines a generic type to the specific type, and the rollback restores it.** The graph keeps the more precise type. The rollback payload keeps the previous type, so a wrong merge can be undone without loss.
4. **The acronym gate requires the same type.** The deterministic match of an acronym and a full name (for example `MCP` and `Model Context Protocol`) has no model check. A type match is the only guard against collisions, so it stays strict.
5. **The same-entity judge is domain-neutral.** Its rules and examples cover translations, transliterations, localized names, honorific variants, and short names of organizations, and also items of one category that are different. Reason: the knowledge base holds narrative, game, and multilingual content, not only software.
6. **One source of truth for the VLM language.** The VLM target language is the first project-wide preferred language. There is no separate VLM language setting. See [PREFERRED_LANGUAGES_DESIGN.md](PREFERRED_LANGUAGES_DESIGN.md).

## Rejected alternatives

- **Keep the strict type check on name and alias matches.** It caused the duplicate nodes above.
- **Make all types compatible.** Specific types such as `Person` and `Tool` with different names are usually different subjects. Only the generic types are open.
- **Keep `CLAIRE_VLM_LANGUAGE`.** Two settings for one intent can disagree and add maintenance work.

## Limits for later changes

- A change that makes the gates more permissive must keep the rollback complete, including the type.
- New generic types must be added on purpose, because each one opens merges across all types.
