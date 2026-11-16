# Multi-Theme Architecture

- **Status**: Implemented. Design principles only. Current behavior: [`THEMES.md`](../implementation/THEMES.md) §0 to §8. The first design kept one database file for each theme; the code now keeps all themes in one database with a theme partition (see "Rejected alternatives").
- **Date**: 2026-09-10 (DESIGN-THEME-20260910-01) · condensed 2026-11-16
- **Related**: [`DATA_STORE_LAYOUT_DESIGN.md`](DATA_STORE_LAYOUT_DESIGN.md) (database layout), [`MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md`](MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md) (theme numbers, paths, accounts), [`PUBLIC_SITE_CRAWLING_AND_INDEXING_DESIGN.md`](PUBLIC_SITE_CRAWLING_AND_INDEXING_DESIGN.md) (analytics ID of a theme). The draft [`SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md`](SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md) proposes to change themes into collections of knowledge bases; until that is implemented, this document gives the principles of the current model.

## Goal

At first, all collected material went into one knowledge base. This caused three problems:

1. **Graph interference.** Entities from unrelated subjects with the same name (for example `Service`, `Grace`, `Node`) joined in the graph.
2. **Lower search quality.** Hybrid search ranked documents from other subjects high, and RAG summaries became less precise.
3. **No visibility control.** The owner could not make one subject public and keep another private or shared only with collaborators.

A **theme** is a separate knowledge area with its own documents, graph, search index, embeddings, and visibility.

## Decisions in force

1. **Users see "theme", not "database".** Reason: "select a database" is an engineering term and gives a high cognitive load.
2. **A stable identifier, not the label, identifies a theme.** The owner can change the label, icon, and description at any time. Reason: links, bookmarks, Telegram hashtags, and storage must not break when a label changes.
3. **Each account has exactly one default theme.** The default theme cannot be deleted. It has no default focus. Collaborators have no access to it. Reasons: the base knowledge base must always exist; it stays neutral for general collection; collaborators must not change the owner's base knowledge.
4. **Three visibility levels: public, open to collaborators, private.** A private theme is not found for a client that cannot see it, with the same response as a theme that does not exist. Reason: the response must not tell that a private theme exists.
5. **Only the owner of an account manages its themes.** Collaborators and anonymous visitors only select from the themes that they can see.
6. **Default focus of a theme.** An explicit focus in the request comes first. When the request has no focus, the default focus of the theme applies. The default theme gives no focus. Reason: a theme collects one subject; the user must not type a long focus for each ingest.
7. **Reset keeps the identity of a theme.** Reset removes the data of the theme and keeps its identifier, label, icon, focus, and visibility settings. Reason: delete-and-create gives a new identifier, breaks all links and hashtags, and makes the owner set all settings again.
8. **Delete has two levels:** remove the registration only, or remove the registration and the data. Destructive commands are dry runs by default and need confirmation (PRINCIPLES D2, D3).
9. **No substitution.** A theme that does not exist or that the client cannot see is an error. The system never uses another theme in its place (PRINCIPLES C1).
10. **Single mode is transparent.** When the server runs with one theme, the system behaves like a system without themes: no selector, no extra request, no layout shift. Reason: a user with one knowledge base must not pay for themes.
11. **Operations cover all themes.** Migration, health checks, and the resident queue workers process every theme. An error in one theme does not stop the other themes. Reason: a theme that is not migrated or not processed is a silent data loss.

## Rejected alternatives

- **One database file for each theme** (the first design of this document). It gave physical isolation. In 2026-09 the files were merged into one database with a theme column (schema v14, which also added syndication between themes). [`DATA_STORE_LAYOUT_DESIGN.md`](DATA_STORE_LAYOUT_DESIGN.md), option C, records the reason: one file for each theme is too fine. Isolation now depends on the theme scope of each query.
- **A directory or key from the label.** A label change would move paths and break links. Rejected by decision 2.
- **A host name for each theme.** Replaced by the single FQDN design; old theme hosts only redirect.

## Limits for later changes

- Every query on knowledge data must state its theme scope. A query for all themes must say so explicitly.
- State that all themes share (job files, provider files, telemetry) must be filtered by theme access before it goes into a response.
- Do not add a fallback from a missing theme to a default theme.
