# Agent Client Common Specification (`claire` Plugin, `v0.4.1`)

- **Document Identifier**: `SPEC-AGENT-CLIENT-COMMON-20261122`
- **Status**: Active Specification (`fofwisdom/claire-agents` `v0.4.1`) -> Implemented behavior in [`API_AND_MCP_GUIDE.md`](../implementation/API_AND_MCP_GUIDE.md) Section 3.4, [`EXTERNAL_ACCESS.md`](../implementation/EXTERNAL_ACCESS.md), [`EXTRACTION.md`](../implementation/EXTRACTION.md) Section 9, [`GLOSSARY.md`](../implementation/GLOSSARY.md), and [`INGESTION.md`](../implementation/INGESTION.md)
- **Date**: 2026-11-22
- **Related Documents**:
  - [`CLAUDE_AGENT_CLIENT_DESIGN.md`](CLAUDE_AGENT_CLIENT_DESIGN.md) (empirical lessons `T1`–`T19` and owner decisions `CD1`–`CD4`)
  - [`AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md`](AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md) (`AED1`–`AED11`, `AED3-rev`, 4-layer defense in Section 5.4)
  - [`MCP_SUPPORT.md`](MCP_SUPPORT.md) (17-tool `<domain>_<action>` server `"claire"`, Option A global registration)
  - [`PRINCIPLES.md`](../PRINCIPLES.md) (`A1`–`A4`, `B1`–`B4`, `C1`–`C5`, `G1`)
  - `agent_clients/`, `scripts/build_agent_marketplace.py`, `tests/test_agent_marketplace_build.py`

---

## 1. Purpose and Scope

This specification defines the canonical, vendor-neutral contract for all first-party and third-party Claire Bible `agent_client` plugins built from `agent_clients/` into `fofwisdom/claire-agents`.

Previous versions (`v0.2.0` and `v0.3.0`) exposed three user-facing skills and mixed client-specific branches in `scripts/build_agent_marketplace.py`. That split caused two defects:

1. Users had to choose between two extraction entry points ("ingest" vs "extract") for the same outcome.
2. Antigravity `plugin.json` silently dropped a fourth `suggestedPrompts` entry (`T` limit of 3 prompts).
3. Client adapters duplicated skill/subagent conditionals in Python instead of declaring harness differences as data.

Starting in `v0.4.0`, every `agent_client` target renders from shared templates under `agent_clients/common/` and a declarative `[adapter]` table in `agent_clients/clients/<target>/client.toml`.

---

## 2. Core UX Invariant: Two User-Facing Skills Only

Every Claire Bible client plugin MUST expose **only two** logical user-facing skills, formatted according to the harness adapter's `skill_naming` rule:

| Logical Skill | `hyphen_prefix` (Antigravity) | `plugin_colon` (Claude) | Purpose |
| :--- | :--- | :--- | :--- |
| `claire:ingest` | `claire-ingest` | `/claire:ingest` | Stage and extract a URL, local file, text note, or pending `extraction_status='pending'` queue |
| `claire:query` | `claire-query` | `/claire:query` | Search and traverse KBs (`kb_list` + scoped read tools) and perform user-confirmed `contrib_submit` |

**Hard Rule**: A client plugin MUST NOT expose a separate user-facing `extract` skill. Pending-queue extraction is Flow D inside `claire:ingest`.

### 2.1 Four Ingest Flows Inside `claire:ingest`

`claire:ingest` handles four input cases uniformly:

1. **Flow A — Web URL**:
   - The top-level agent MUST NOT open the URL in a browser or fetch it with `curl`.
   - Call `extract_stage(url="https://...", full_content=True)` (optionally with `title` or `kb="personal"`).
   - If the document is already extracted (`duplicate: true` and `extraction_status == "extracted"`), report the existing document.
   - Otherwise pass only `document_id` (and `kb="personal"`) to the isolated extractor.
2. **Flow B — Local Document File (`PDF`, `DOCX`, `ODT`, `MD`, `TXT`, `RST`, `ADOC`, `HTML`)**:
   - Maximum server file size is 20 MiB (`MAX_STAGE_FILE_BYTES`).
   - For files `<= 200 KB`, base64-encode the bytes without interpreting untrusted prose in the top-level agent context and call `extract_stage(filename="<basename>", content_base64="<base64>", full_content=True)` (lesson `T14` in [`CLAUDE_AGENT_CLIENT_DESIGN.md`](CLAUDE_AGENT_CLIENT_DESIGN.md); future `CL9` upload-link tool remains open).
   - For files `> 200 KB` up to 20 MiB, stage via multipart `POST /api/client-extraction/stage` (`file=@...`) or Web UI upload.
   - Pass only the returned `document_id` to the isolated extractor.
3. **Flow C — Text Note or Excerpt**:
   - Call `extract_stage(text="...", title="...", full_content=True)`.
   - Pass only the returned `document_id` to the isolated extractor.
4. **Flow D — Pending Queue Drain or Empty-Input Onboarding**:
   - Triggered when the user supplies a `document_id`, passes no URL/file/note, or asks to process queued/pending items (from Web UI, Telegram, or feeds).
   - If a `document_id` is given directly, hand it to the isolated extractor.
   - Otherwise call `extract_queue(status="pending", limit=50)`.
   - **Empty queue (`count == 0`)**: Reply with a clear onboarding message that asks the user to paste a URL, local file path, or text note to start.
   - **Non-empty queue (`count > 0`)**: Hand pending `document_id` values to the isolated extractor cleanly and report the committed result plus the remaining pending count.

### 2.2 Query and Contribution Scope Inside `claire:query`

`claire:query` covers deterministic, zero-LLM read exploration (`kb_list`, `kb_overview`, `entity_search`, `entity_get`, `graph_neighbors`, `graph_path`, `graph_context`, `doc_list`, `doc_get`) enforcing `CT23` (`kb="personal"` or an 18-digit external `kb` ID from `kb_list()`).

When the user asks to contribute personal documents to a shared KB, theme, or federated remote KB, `claire:query` uses `contrib_candidates` and `contrib_submit` (`write` scope required). On harnesses that support `allowed-tools` (`T19`), `contrib_candidates` and `contrib_submit` stay outside `allowed-tools` so contribution always prompts for explicit user confirmation.

---

## 3. Declarative Adapter Contract (`[adapter]` in `client.toml`)

Each target lives under `agent_clients/clients/<target>/` and declares its harness capabilities in `client.toml` under `[adapter]`. The builder (`scripts/build_agent_marketplace.py`) reads `[adapter]` rather than branching on `target == "claude"`.

### 3.1 `[adapter]` Field Specification

| Field | Allowed Values | Meaning |
| :--- | :--- | :--- |
| `skill_naming` | `"hyphen_prefix"` \| `"plugin_colon"` | `"hyphen_prefix"` writes `skills/<plugin>-<skill>/SKILL.md` (`claire-ingest`, `claire-query`). `"plugin_colon"` writes `skills/<skill>/SKILL.md`, invoked as `/<plugin>:<skill>` (`/claire:ingest`, `/claire:query`). |
| `agent_naming` | `"hyphen_prefix"` \| `"plugin_colon"` | `"hyphen_prefix"` writes `agents/<plugin>-extractor.md` (`claire-extractor`). `"plugin_colon"` writes `agents/extractor.md`, referenced as `<plugin>:extractor` (`claire:extractor`, lesson `T7`). |
| `tool_prefix` | `""` \| `"mcp__claire__"` | Prefix prepended to bare MCP tool names in frontmatter and instructions (`""` for Antigravity, `"mcp__claire__"` for Claude Option A global server `"claire"`, lessons `T5`, `T6`, `T11`). |
| `handoff_mode` | `"direct_subagent"` \| `"forked_internal_skill"` | `"direct_subagent"` instructs the top-level skill to hand `document_id` directly to the extractor subagent. `"forked_internal_skill"` routes the handoff through a hidden forked internal runner skill (`Skill(<plugin>:<internal_runner_skill>)`, lessons `T7`, `T19`). |
| `internal_runner_skill` | Non-empty skill slug (required iff `handoff_mode == "forked_internal_skill"`) | Name of the hidden internal runner skill rendered from `agent_clients/common/runner/SKILL.md.tmpl` (for Claude: `"extract-runner"`). Must set `user-invocable: false`, `context: fork`, `agent: <extractor_ref>`, and `allowed-tools` for the 8 extractor tools. |
| `guard_mode` | `"rules"` \| `"claude_pre_tool_use_hook"` | `"rules"` renders `rules/AGENTS.md` from `agent_clients/common/rules/AGENTS.md.tmpl`. `"claude_pre_tool_use_hook"` omits `rules/AGENTS.md` (lesson `T2`) and writes the fixed fail-closed `hooks/hooks.json` `PreToolUse` guard (lessons `T8`, `T9`, decision `CD1`). |
| `supports_allowed_tools` | `true` \| `false` | When `true`, emits `argument-hint` and `allowed-tools` in user skill frontmatter and `tools` + `model: inherit` (without Antigravity keys `subagent` / `mainAgent`, lessons `T3`, `T10`) in extractor frontmatter. When `false`, emits Antigravity frontmatter (`subagent: true`, `mainAgent: false`, `tools`). |
| `extractor_mcp_mode` | `"per_tool_list"` \| `"inherit_mcp"` (optional, default `"per_tool_list"`) | `"per_tool_list"` declares all 8 Claire MCP tools directly in `tools: [...]`. `"inherit_mcp"` emits `tools: [send_message]` and `inheritMcp: true` (valid only when `supports_allowed_tools == false`) for harnesses such as Antigravity whose agent `tools:` registry accepts built-in components only and delegates MCP access via `inheritMcp`. |

### 3.2 `suggested_prompts` Rule (`plugin.json` Limit)

For Antigravity (`target = "antigravity"`), the harness UI silently drops any `suggestedPrompts` item after the third entry.

- `client.toml` MUST define `1 <= len(suggested_prompts) <= 3`.
- The three starter prompts target immediate new-user workflows:
  1. Save and extract a URL (`Flow A`).
  2. Stage and extract a local document file (`PDF/DOCX/MD`, `Flow B`).
  3. Search the user's Claire Bible knowledge graph (`claire:query`).
- Do NOT include a separate pending-queue prompt in `suggested_prompts` (Flow D is invoked through `claire:ingest`).

### 3.3 How First-Party Harnesses Implement the Same Spec

Both first-party targets consume the exact same `agent_clients/common/` skill, agent, and runner templates without per-client template forks:

| Aspect | Antigravity (`antigravity`) | Claude (`claude`) |
| :--- | :--- | :--- |
| **User Skills (2)** | `skills/claire-ingest/SKILL.md`, `skills/claire-query/SKILL.md` | `skills/ingest/SKILL.md`, `skills/query/SKILL.md` (`/claire:ingest`, `/claire:query`) |
| **Extractor Subagent** | `agents/claire-extractor.md` (`subagent: true`, `mainAgent: false`, `tools: [send_message]`, `inheritMcp: true`, restricted to the 8 Claire MCP tools) | `agents/extractor.md` (`claire:extractor`, 8 `mcp__claire__*` tools, `model: inherit`) |
| **Ingest Handoff** | `direct_subagent`: `claire-ingest` passes `document_id` directly to `claire-extractor` | `forked_internal_skill`: `ingest` calls `Skill(claire:extract-runner)` with `document_id`; `skills/extract-runner/SKILL.md` sets `user-invocable: false`, `context: fork`, `agent: claire:extractor`, and `allowed-tools` so MCP calls run without permission prompts (`T19`) |
| **Layer 1 Guard** | `guard_mode = "rules"` (`rules/AGENTS.md`) | `guard_mode = "claude_pre_tool_use_hook"` (`hooks/hooks.json` fail-closed `PreToolUse` command matching `^mcp__.*__extract_read$`, allowing only `"agent_type" *: *"claire:extractor"`, exiting `2` with block message pointing to `/claire:ingest` or `claire:extractor`, `CD1`) |
| **Starter Prompts** | 3 bilingual `suggestedPrompts` in `plugin.json` | Slash menu shows only `/claire:ingest` and `/claire:query` (`extract-runner` hidden via `user-invocable: false`) |

---

## 4. Shared 4-Layer Security Defense (`AED9`, `CT4`, `CD1`, `CD2`)

All first-party and third-party adapters MUST preserve all four defense layers defined in [`AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md`](AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md) Section 5.4:

1. **Layer 1 — Isolated MCP-Only Subagent**:
   - The top-level agent holds shell, file, and browser tools and MUST NEVER call `extract_read` or fetch untrusted URLs directly.
   - Only the isolated extractor (`claire-extractor` / `claire:extractor`) holds `extract_read`, restricted to an explicit allow list of 8 Claire MCP tools (`extract_spec`, `extract_queue`, `extract_claim`, `extract_read`, `extract_submit`, `kb_list`, `entity_search`, `graph_neighbors`) with zero shell, file write/edit, or browser tools. (Deny lists are forbidden because user mail/drive tools would stay exposed.)
   - Harnesses with `PreToolUse` hook capability (`claude_pre_tool_use_hook`) MUST enforce this deterministically and fail closed (`CD1`).
   - Harnesses without isolated subagent + hook support (such as chat-only surfaces, [`CLAUDE_AGENT_CLIENT_DESIGN.md`](CLAUDE_AGENT_CLIENT_DESIGN.md) Section 4 & `CL6`) MUST use `readonly` OAuth scope (user clears `write` on the consent screen, `CD2`) so the server rejects `extract_read` and write tools.
2. **Layer 2 — Nonce-Bound Untrusted Data Framing and Full-Content Pagination**:
   - `extract_read` neutralizes embedded closing tags and wraps untrusted text in `<untrusted_source_document nonce="<hex>" document_id="..." sha256="...">`.
   - The extractor treats everything inside the nonce fence strictly as inert data, verifies `sha256` against `expected_content_hash` from `extract_claim`, and pages through the full untruncated source (`full_content=True`, `offset=next_offset` while `has_more` is true).
3. **Layer 3 — OAuth 2.1 `write` Scope and Personal-KB Isolation (`CT4`)**:
   - Plugins contain zero server URLs, `mcp_config.json` files, or secrets (Option A standalone `"claire"` server registration).
   - Authentication uses OAuth 2.1 (`PKCE S256` or headless RFC 8628 `/device`) with `readonly` and step-up `write` scopes.
   - Server middleware confines `extract_*` writes strictly to the caller's personal KB (`kb="personal"` or omitted, rejecting shared KBs with `kb_ingest_by_contribution`).
4. **Layer 4 — Server Validation and Atomic Commit Gates (`extract_submit`)**:
   - Server enforces `ClientExtractionPayload` (`extra="forbid"`, `strict=True`), active `claim_token` lease, optimistic `expected_content_hash` check, `evaluate_summary_verdict` summary lint, AsciiDoc lint + `sanitize_rendered_detail` / `render_to_html`, ontology/hard-invariant checks, and atomic commit + Option B embedding.

---

## 5. Conformance Build Gates (`scripts/build_agent_marketplace.py`)

Every change to `agent_clients/` or `scripts/build_agent_marketplace.py` MUST pass all deterministic build gates before publication to `fofwisdom/claire-agents`:

1. **Source Hygiene (`_validate_source_tree`)**: Reject symlinks, path escapes, executable mode bits (`0o111`), and forbidden script/binary extensions (`.py`, `.sh`, `.js`, `.ts`, etc.).
2. **Declarative `[adapter]` & Prompt Validation (`_validate_client_meta`)**: Validate every `[adapter]` key and enum value, require `internal_runner_skill` iff `handoff_mode == "forked_internal_skill"`, and enforce `1 <= len(suggested_prompts) <= 3` when `suggested_prompts` is configured.
3. **Rendered Content & Stale-Skill Gate (`_check_rendered_content`)**: Reject unresolved `{{...}}` placeholders (except `{{MCP_URL}}` in `templates/mcp_config.json.tmpl`), secret patterns, legacy paths/tool names, and any occurrence of the retired separate `extract` user skill identifier across all rendered plugin files, top-level `README.md`, `docs/*.md`, and `release_notes.md`.
4. **2-Axis Skill Invariant Gate**: Enforce that each target bundle renders ONLY the two user-facing skills (`ingest` and `query` formatted per `skill_naming`), plus `internal_runner_skill` only when `handoff_mode == "forked_internal_skill"`.
5. **Forked Skill & Hook Safety Gate**:
   - Any skill with `context: fork` MUST set `user-invocable: false` and `agent:` referencing an extractor agent in the same bundle.
   - `extract_read` in `allowed-tools` / `tools` is permitted ONLY in the isolated extractor agent or a `context: fork` skill wired to that extractor agent.
   - When `guard_mode == "claude_pre_tool_use_hook"`, `hooks/hooks.json` MUST equal the exact constant from `claude_guard_hooks()`, with zero other hook files allowed; when `guard_mode != "claude_pre_tool_use_hook"`, `hooks/` files are forbidden.
   - Reject Antigravity-only or unsupported plugin subagent keys (`subagent`, `mainAgent`, `hooks`, `mcpServers`, `permissionMode`, `initialPrompt`) when `supports_allowed_tools` is true (`T3`, `T10`).
6. **Live MCP Tool Contract Gate (`_verify_mcp_contracts`)**: Parse `@mcp.tool` definitions from `src/claire/api/mcp_tools.py` via AST and reject any unknown tool in frontmatter or skill/agent/rule text, ensuring `tool_prefix` matches `[adapter].tool_prefix`.

---

## 6. Guidance for Third-Party Harness Implementers

Third-party maintainers can add support for a new AI agent harness without forking `agent_clients/common/` templates or weakening UX and security:

1. **Keep the 2-Axis User UX**:
   - Do not invent a third user-facing command for pending extraction. Route URLs, files, notes, and pending queue drain through `claire:ingest` (Flows A–D), and route reading + user-confirmed contribution through `claire:query`.
2. **Declare a New Target Adapter**:
   - Create `agent_clients/clients/<target>/client.toml` and `agent_clients/clients/<target>/README.md.tmpl`.
   - Set `[adapter]` fields (`skill_naming`, `agent_naming`, `tool_prefix`, `handoff_mode`, `internal_runner_skill`, `guard_mode`, `supports_allowed_tools`) to match how the harness names skills/agents/MCP tools and enforces tool isolation.
   - Add `<target>` to `ALLOWED_TARGETS` and wire its manifest/packaging output in `scripts/build_agent_marketplace.py` using `[adapter]` attributes rather than ad-hoc target special cases.
3. **Preserve Option A and the 4 Defense Layers**:
   - Never bundle `mcp_config.json`, tokens, or server URLs inside a plugin archive. Register the global `"claire"` OAuth 2.1 MCP server once per user workstation.
   - Confine `extract_read` to an MCP-only isolated subagent with an explicit 8-tool allow list (never a deny list). If the harness supports deterministic pre-tool hooks, enforce isolation with a fail-closed hook; if the harness cannot isolate subagents or hooks, require `readonly` OAuth scope so extraction reads are blocked on the server.
   - Keep `scripts/build_agent_marketplace.py` free of executable scripts inside plugin bundles unless an explicit owner-approved constant hook (matching `CD1`) is required for deterministic Layer 1 enforcement.
4. **Verify Against Conformance Gates**:
   - Run `.venv/bin/python scripts/build_agent_marketplace.py --source agent_clients --out /tmp/claire-agents-check --check --mcp-tools-file src/claire/api/mcp_tools.py` and `.venv/bin/pytest -q tests/test_agent_marketplace_build.py`.
