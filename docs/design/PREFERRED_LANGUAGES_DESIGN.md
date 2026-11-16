# Project-Wide Preferred Content Languages

- **Status**: Implemented for fetching, caption selection, VLM transcription, and the preflight output. Design principles only. Current behavior: [`EXTRACTION.md`](../implementation/EXTRACTION.md) §2.3. The extraction prompts, the detail render, and the search tokenizer do not use this setting. The search tokenizer selects its analyzer from the script of the text ([`I18N.md`](../implementation/I18N.md) §7). No design plans a language target for extraction or detail render now.
- **Date**: 2026-09-02 · condensed 2026-11-16

## Goal / problem

Claire ingests web pages, captions, papers, and social posts in many languages. Before this design, language priorities were fixed in single fetchers or in separate settings (for example a VLM language). The user could not set one preference for all features, and English content could be lost when the preferred language had no track.

## Decisions in force

1. **One project-wide setting.** All features that select or produce content in a language read one list of preferred languages. Reason: separate settings for one intent can disagree.
2. **English is always the last fallback.** The effective list always ends with `en`, also when the user leaves the setting empty. Reason: when no preferred-language content exists, the system keeps the English original and does not stop.
3. **Normalization is deterministic.** The parser makes codes lowercase, removes spaces, empty items, and duplicates, and moves `en` to the end. The same input always gives the same list.
4. **The first preferred language is the transcription target.** VLM transcription of images and infographics uses the first item. There is no separate VLM language.
5. **Content language and interface language are separate.** The UI locale of an account and the entity label language of a viewer do not change the content language list, and the content language list does not change them.

## Rejected alternatives

- **One setting for each feature** (for example `CLAIRE_VLM_LANGUAGE`): removed, because the values could disagree.
- **No fallback language:** a fetch could find no usable track and lose the content.

## Limits for later changes

- A new feature that selects content by language reads the project-wide list. It does not add a new language setting.
- The extraction output language is fixed by the extraction prompt now. A change that makes it follow this list must also consider the stored summaries and the entity names that already exist.
