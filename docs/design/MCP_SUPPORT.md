# MCP and OAuth 2.1 Support

- **Status**: Implemented (read-only MCP tool set and the OAuth 2.1 authorization server). Design principles only, and a list of proposals that are not implemented. Current behavior: [`API_AND_MCP_GUIDE.md`](../implementation/API_AND_MCP_GUIDE.md) Sections 3 and 2.5 (token endpoint).
- **Date**: 2026-09-22 (implementation), proposals 2026-10-30 · condensed 2026-11-16. The first Korean version (flow diagram, metadata examples, tool table) is in git history before commit `d15ae8e0`.
- **Protocols**: MCP Streamable HTTP (JSON-RPC 2.0), RFC 6749, RFC 6750, RFC 7636 (PKCE S256), RFC 7591 (dynamic client registration), RFC 8414, RFC 9728, RFC 8693.

## Goal

External AI clients (for example Claude Code, Claude Desktop, Gemini connected apps, Cursor, Antigravity) can read the knowledge graph and the documents of Claire through one standard endpoint. A user connects a client without a copy and paste of a token when the client supports OAuth.

## Decisions in Force

1. **In the api process.** The MCP endpoint is a part of the existing API process, not a separate container. It has the same security gate as the REST routes and the same database access. Reason: one security boundary for probes without credentials.
2. **Read-only tools.** All tools only read. A credential that an external AI client gets by OAuth has the `readonly` scope only. Reason: an external agent must not change, ingest, or delete knowledge. A write tool needs a separate scope model first.
3. **No LLM call in a tool.** The tools use database queries and full-text search only. They do not call an embedding or summary API, also when a REST function with the same purpose does. Reason: an agent loop calls the tools many times; it must not use the LLM quota of the server.
4. **No side effect of a read.** A tool that shows a document does not mark it as seen. Reason: an agent read is not a user read.
5. **Small answers, agent-led exploration.** The server does not return a large subgraph at one time. Tools cap their lists, show when a list is cut, and have a compact mode. The agent extends the frontier step by step: orientation, entry point, neighbors, narrowing, and a final context call. Reason: the context window of the agent is small, and the agent decides what is important.
6. **Account isolation.** A tool sees only the knowledge of the account of the credential, with the same visibility rules as the REST routes. A knowledge base that the credential cannot see is reported as not found. Reason: MCP must not be a second path around the access rules.
7. **Standard discovery and one-touch approval.** An unauthenticated call gets 401 with the address of the protected resource metadata, and the client finds the authorization server from it. Dynamic client registration is open. The approval page lets a signed-in user approve with one action, and lets the admin approve from Telegram. Reason: standard clients connect without manual configuration.
8. **OAuth tokens are separate from web sessions.** A new web session does not end an OAuth connection. A refresh token renews the access token without user action. A token belongs to an account; an account that is suspended, being deleted, or purged cannot renew. Reason: a connected agent must stay connected, but never longer than its account.

## Rejected Alternatives

| Alternative | Reason for rejection |
| :--- | :--- |
| A separate MCP server process or container | Two security boundaries, and a second database access path |
| Reuse the REST search for the MCP search tool | The REST search calls the embedding API for each query |
| Tokens copied by hand only | Standard OAuth clients cannot connect, and users copy secrets |
| Write tools with the same `readonly` scope | No separation between reading and changing knowledge |

## Proposals (Planned, Not Implemented)

These items come from a comparison of the code and the first version on 2026-10-30, examined again on 2026-11-16. Each one is still open. The owner has not decided them.

| ID | Proposal |
| :--- | :--- |
| MP1 | Record the credential of each tool call (token, OAuth client, or session kind) in the MCP telemetry, and keep a last-use time for OAuth tokens. |
| MP2 | A revoke endpoint for OAuth tokens (RFC 7009), and a list and revoke view of OAuth connections for the owner. |
| MP3 | A call limit for each credential, with 429 and `Retry-After`. |
| MP4 | Correct client configuration examples for a remote HTTP MCP server, also in the output of the CLI token command. |

The first version also proposed syndication tools with their own scopes. The syndication design that they depended on is retired (2026-11-16). A later scope model for write tools must follow the credential scopes of [`SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md`](SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md) (CT17).
