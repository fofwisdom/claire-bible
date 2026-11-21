---
name: claire-query
description: >-
  Query and traverse the Claire Bible knowledge graph and archived documents using Claire MCP tools. Use when the user asks to search, explore connections, find shortest paths, inspect entity details, or summarize stored knowledge in a Claire personal or shared knowledge base.
---

# Claire Graph and Document Query (`claire-query`)

Use this skill to answer user questions from Claire Bible knowledge bases using deterministic MCP read tools (`readonly` or `write` OAuth 2.1 scope).

## Query Rules (`CT23` and Zero-LLM Discipline)

1. **Always pass `kb` (`CT23`)**: Every read tool (`overview`, `resolve_entity`, `search`, `neighbors`, `path`, `context`, `node`, `documents`, `document`, `stats`) requires the external knowledge base number in parameter `kb`. Missing `kb` returns `{"error": "kb_required"}`. Never pass deprecated `theme` parameters or internal IDs.
2. **Zero Server LLM Calls / Read-Only**: MCP query tools use SQLite and FTS5 only and do not mark documents as seen. Synthesize answers directly from tool output without inventing unstated facts.
3. **Check Truncation Flags**: Inspect `truncated`, `omitted`, `documents_truncated`, and `documents_omitted` in responses so capped lists are never mistaken for missing results.

## Step-by-Step Exploration Loop

1. **Orient (`overview` / `stats`)**:
   - Call `overview(kb=...)` when the structure or key topics of the knowledge base are unknown to view `entity_types`, `source_types`, top-degree `hubs`, and `most_corroborated` entities.
   - Call `stats(kb=...)` if overall document, entity, and relation counts are needed.
2. **Find Entry Points (`resolve_entity` / `search` / `documents`)**:
   - Call `resolve_entity(name=..., kb=...)` to locate an entity ID by exact name, alias, or FTS5 fallback.
   - Call `search(query=..., entity_type=..., limit=8, kb=...)` for FTS5 keyword search (optionally filtered by `entity_type`).
   - Call `documents(limit=30, since=..., query=..., kb=...)` to filter documents by `YYYY-MM-DD` / ISO8601 timestamp or title/URL substring (max `limit` 100).
3. **Expand Frontier (`neighbors`)**:
   - Call `neighbors(entity_ids=[...], exclude_ids=[...], limit=50, kb=...)` to inspect 1-hop neighbors sorted by `degree` descending.
   - Always pass all previously visited `entity_id` values in `exclude_ids` to avoid cycles.
4. **Narrow Scope or Find Shortest Path (`search` / `path`)**:
   - Call `search(query=..., near_ids=[...], kb=...)` to search only within the 1-hop neighborhood of active frontier nodes.
   - Call `path(from_id=..., to_id=..., max_hops=4, kb=...)` to find the shortest undirected BFS connection between two entities directly.
5. **Gather Grounded Context (`context` / `node` / `document`)**:
   - Call `context(entity_ids=[...], compact=True, kb=...)` on up to 10 selected entities for observations, relations, and source summaries. Use `compact=False` only when complete observations and source summaries are required.
   - Call `node(entity_id=..., full=False, kb=...)` for one entity's full observations and up to 10 source documents (`full=True` includes document details, which can be large for high-degree hubs).
   - Call `document(document_id=..., kb=...)` to read the complete stored summary, detail, URL, and metadata of one specific document.
6. **Report Grounded Answer**:
   - Answer the user's question accurately from the retrieved entities, relations, observations, and document sources. Cite entity names and document IDs or URLs, and state clearly if the knowledge base lacks sufficient evidence.
