# MCP and OAuth 2.1 Support

- **Status**: Implemented (17-tool `<domain>_<action>` MCP toolset under server `"claire"` v0.2.0 and the OAuth 2.1 authorization server; agent client plugin packaging evolved in `v0.4.0` per [`AGENT_CLIENT_COMMON_SPEC.md`](AGENT_CLIENT_COMMON_SPEC.md)). Design principles, current architecture decisions, and remaining open proposals. Current behavior: [`API_AND_MCP_GUIDE.md`](../implementation/API_AND_MCP_GUIDE.md) Sections 3 and 2.5 (token endpoint).
- **Date**: 2026-09-22 (implementation), proposals 2026-10-30 · condensed 2026-11-16. The first Korean version (flow diagram, metadata examples, tool table) is in git history before commit `d15ae8e0`.
- **Protocols**: MCP Streamable HTTP (JSON-RPC 2.0), RFC 6749, RFC 6750, RFC 7636 (PKCE S256), RFC 7591 (dynamic client registration), RFC 8414, RFC 9728, RFC 8693.

## Goal

External AI agent clients (for example Antigravity / `agy` via [`AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md`](AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md)) can read the knowledge graph and the documents of Claire through one standard endpoint (and participate in explicit client-driven extraction when authorized with `write`). ChatGPT, Codex, and Claude as server providers, and the obsolete Claude Desktop stdio instructions, are retired from Claire Bible principles and design scope (`AED8`, 2026-11-20). Claude stays supported as an OAuth 2.1 `agent_client` ([`CLAUDE_AGENT_CLIENT_DESIGN.md`](CLAUDE_AGENT_CLIENT_DESIGN.md)). A user connects a client without copying and pasting manual persistent tokens over OAuth 2.1 (`AED5`).

## Decisions in Force

1. **In the api process.** The MCP endpoint is a part of the existing API process, not a separate container. It has the same security gate as the REST routes and the same database access. Reason: one security boundary for probes without credentials.
2. **Scoped OAuth 2.1 tokens (`readonly` and `write`, updated 2026-11-20 `AED5`).** Original baseline tools only read under `readonly`. Under [`AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md`](AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md) (`AED4`, `AED5`, `AED9`), write/extraction actions use the separate explicit `write` scope with step-up confirmation, `S256`-only PKCE, refresh token rotation, RFC 7009 `/oauth/revoke`, and a Telegram-free `/login?tx=` OAuth flow (with optional RFC 8628 device grant). Manual persistent tokens are deprecated.
3. **No LLM call in a tool.** The tools use database queries and full-text search only. They do not call an embedding or summary API, also when a REST function with the same purpose does. Reason: an agent loop calls the tools many times; it must not use the LLM quota of the server.
4. **No side effect of a read.** A tool that shows a document does not mark it as seen. Reason: an agent read is not a user read.
5. **Small answers, agent-led exploration.** The server does not return a large subgraph at one time. Tools cap their lists, show when a list is cut, and have a compact mode. The agent extends the frontier step by step: orientation, entry point, neighbors, narrowing, and a final context call. Reason: the context window of the agent is small, and the agent decides what is important.
6. **Account isolation.** A tool sees only the knowledge of the account of the credential, with the same visibility rules as the REST routes. A knowledge base that the credential cannot see is reported as not found. Reason: MCP must not be a second path around the access rules.
7. **Standard discovery and one-touch approval.** An unauthenticated call gets 401 with the address of the protected resource metadata, and the client finds the authorization server from it. Dynamic client registration is open. The approval page lets a signed-in user approve with one action, and lets the admin approve from Telegram. Reason: standard clients connect without manual configuration.
8. **OAuth tokens are separate from web sessions.** A new web session does not end an OAuth connection. A refresh token renews the access token without user action. A token belongs to an account; an account that is suspended, being deleted, or purged cannot renew. Reason: a connected agent must stay connected, but never longer than its account.
9. **Clean-slate server identity, `<domain>_<action>` toolset, and Option A plugin packaging (`v0.2.0`, evolved to `v0.4.0` common spec).** The remote HTTP server identifies globally as `claire` (`MCPServer("claire", title="Claire Bible", version="0.2.0", instructions=...)`) instead of plugin-scoped server names. All 17 tools follow `<domain>_<action>` naming with MCP `ToolAnnotations` (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`), `kb_list` discovery, and explicit `kb="personal"` resolution alongside 18-digit external `kb` numbers. Standalone MCP server config lives once in the client's global config while `fofwisdom/claire-agents` plugins bundle only rules, skills, and isolated subagent definitions. Under `v0.4.0` ([`AGENT_CLIENT_COMMON_SPEC.md`](AGENT_CLIENT_COMMON_SPEC.md)), this supersedes the former 3-skill layout with the 2-axis `claire:ingest` / `claire:query` UX + declarative `[adapter]` contract, hidden Claude `extract-runner` shim (`user-invocable: false`, `context: fork`), updated `PreToolUse` block message referencing `/claire:ingest`, and 3 Antigravity `suggestedPrompts`.

## Rejected Alternatives

| Alternative | Reason for rejection |
| :--- | :--- |
| A separate MCP server process or container | Two security boundaries, and a second database access path |
| Reuse the REST search for the MCP search tool | The REST search calls the embedding API for each query |
| Tokens copied by hand only | Standard OAuth clients cannot connect, and users copy secrets |
| Write tools with the same `readonly` scope | No separation between reading and changing knowledge |

## Proposals (Status Updated 2026-11-22)

These items come from a comparison of the code and the first version on 2026-10-30, examined again on 2026-11-16 and 2026-11-22.

| ID | Proposal | Status |
| :--- | :--- | :--- |
| MP1 | Record the credential of each tool call (`oauth:<id>`, `api:<id>`, or `env:*` channel) in `mcp_telemetry.client_id`, and keep a last-use time for OAuth tokens. | **Partially implemented** (`mcp_telemetry.client_id` attribution on tool calls and HTTP `/mcp` requests in `mcp_tools.py`/`server.py`; OAuth `last_used_at` remains open) |
| MP2 | A revoke endpoint for OAuth tokens (RFC 7009), and a list and revoke view of OAuth connections for the owner. | **Implemented** (`/oauth/revoke`, `/api/me/oauth/grants`, `/settings/mcp`) |
| MP3 | A call limit for each credential, with 429 and `Retry-After`. | **Open** |
| MP4 | Correct client configuration examples for a remote HTTP MCP server, also in the output of the CLI token command. | **Implemented** (Option A global `claire` HTTP config in `/settings/mcp` and `agent_clients/`; manual token creation deprecated) |

The first version also proposed syndication tools with their own scopes. The syndication design that they depended on is retired (2026-11-16). A later scope model for write tools must follow the credential scopes of [`SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md`](SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md) (CT17).
