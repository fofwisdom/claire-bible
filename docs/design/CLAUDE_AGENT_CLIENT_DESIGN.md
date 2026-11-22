# Claude Agent Client: A Working `claire` Plugin for Claude Code

- **Document Identifier**: `DESIGN-CLAUDE-AGENT-CLIENT-20261122`
- **Status**: Units CL0 to CL8 are implemented (2026-11-22, marketplace `v0.3.0`); superseded in `v0.4.0` by the 2-axis common specification in [`AGENT_CLIENT_COMMON_SPEC.md`](AGENT_CLIENT_COMMON_SPEC.md) (`claire:ingest` / `claire:query` + declarative `[adapter]`, hidden `extract-runner` forked shim, updated `PreToolUse` block message referencing `/claire:ingest`, and 3 Antigravity `suggestedPrompts`). The current state is in [`API_AND_MCP_GUIDE.md`](../implementation/API_AND_MCP_GUIDE.md) Sections 3.4 and 3.1 (consent choice) and [`EXTERNAL_ACCESS.md`](../implementation/EXTERNAL_ACCESS.md). Unit CL9 (upload link) is not implemented. Unit CL10 (Cowork and claude.ai plugin support) has no design yet. Owner decisions CD1 to CD4 (Section 10) are locked (2026-11-22).
- **Date**: 2026-11-22
- **Base commit**: `ea4804b` (marketplace `v0.2.0`, Option A)
- **Related Documents**:
  - [`AGENT_CLIENT_COMMON_SPEC.md`](AGENT_CLIENT_COMMON_SPEC.md) (`v0.4.0` canonical vendor-neutral 2-axis skill and `[adapter]` specification)
  - [`AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md`](AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md) (`AED3-rev`, `AED8`, the four defense layers in Section 5.4)
  - [`MCP_SUPPORT.md`](MCP_SUPPORT.md) (decision 9: Option A)
  - [`API_AND_MCP_GUIDE.md`](../implementation/API_AND_MCP_GUIDE.md) and [`EXTERNAL_ACCESS.md`](../implementation/EXTERNAL_ACCESS.md) (current setup text)
  - `agent_clients/`, `scripts/build_agent_marketplace.py`, `src/claire/api/accounts_routes/console_syndication_panels.py`

Evidence tags follow [`PRINCIPLES.md`](../PRINCIPLES.md) F1: **[code]**, **[decision]**, **[proposal]**, **[external]**, **[unverified]**. The tag **[measured]** marks a result of a test on Claude Code 2.1.329 on 2026-11-22.

---

## 1. Problem

`AED8` keeps Claude as a supported `agent_client`. The marketplace publishes a Claude plugin, and Claude Code can add the marketplace. After that, the user gets no working function:

1. The plugin calls `mcp__claire__*` tools. The setup text registers the server in a scope that is only the current folder, or as a claude.ai connector whose tools have a different name. In both cases the skills and the extractor find no tools.
2. `/settings/mcp` gives Antigravity a setup prompt, a guide link, and a configuration file. It gives Claude one sentence and the `owner/repo` text.
3. The Claude bundle has no rules file. Layer 1 of the defense (no raw source in the top-level agent) depends only on text in the skills.

This design makes the Claude target work from zero to the first committed extraction, with the same four defense layers as Antigravity.

---

## 2. Facts at the Base Commit

| No. | Fact | Tag |
| :--- | :--- | :--- |
| T1 | The Claude bundle has `.claude-plugin/plugin.json`, `agents/extractor.md`, `skills/{ingest,extract,query}/SKILL.md`, and `README.md`. The Antigravity bundle also has `rules/AGENTS.md`. The build writes `rules/AGENTS.md` only for Antigravity. | [code] `build_marketplace()` |
| T2 | Claude Code does not load a `CLAUDE.md` from a plugin. The documentation says: put instructions in a skill, and put a rule that must always hold in a hook. | [external] plugins-reference |
| T3 | The Claude extractor frontmatter has the Antigravity keys `subagent: true` and `mainAgent: false`. Claude Code does not use them. `claude plugin validate` passes the bundle with one warning (no icon). | [code], [measured] |
| T4 | The skills tell the top-level agent not to call `extract_read`. No control stops the call when the model did not load a skill. | [code] |
| T5 | `claude mcp add` uses scope `local` by default (current project only). The setup text in the guides, the plugin README, and `#pref-mcp-snippet-cli` has no `--scope user`. | [measured] `claude mcp add --help`; [code] |
| T6 | Claude Code shows claude.ai connectors as servers named `claude.ai <Name>`. Their tools do not have the `mcp__claire__` prefix. The guides tell the user to add the server under "Claude Connectors". | [measured] `claude mcp list`; [code] guides |
| T7 | A plugin subagent is named `<plugin>:<name>` (`claire:extractor`). A skill with `context: fork` and `agent: <plugin>:<name>` runs as that plugin subagent. A YAML list in `tools` is accepted. | [external] sub-agents; [measured] |
| T8 | In a `PreToolUse` hook, the input has `agent_type: "<plugin>:<name>"` when the call is in a plugin subagent. The input has no `agent_type` on the main thread. | [measured] |
| T9 | A one-line shell command in `hooks/hooks.json` (no script file) blocked a main-thread tool call with exit code 2 and let the same call in the plugin subagent continue. | [measured] |
| T10 | Plugin subagents ignore the frontmatter fields `hooks`, `mcpServers`, `permissionMode`, and `initialPrompt`. | [external] sub-agents, plugin components |
| T11 | A plugin MCP server gets tools named `mcp__plugin_<plugin>_<server>__<tool>`. `userConfig` values go into `pluginConfigs` in the user settings, not into the plugin folder. | [external] plugins-reference |
| T12 | Claude Code gives a user a new plugin copy only when the plugin version changes. Auto-update is off by default for a marketplace that is not official. | [external] plugin marketplaces |
| T13 | `extract_read`, `extract_stage`, `extract_claim`, `extract_submit`, and `contrib_submit` need scope `write`. The consent screen approves the requested scope as one value. | [code] `mcp_tools.py`, `oauth.py` |
| T14 | To ingest a local file, the top-level agent puts the base64 text of the file into an `extract_stage` call. A 200 KB file gives about 270 KB of base64 text in the context of the main agent. | [code] ingest skill |
| T15 | `MCP_SUPPORT.md` Goal says that Claude is retired from the design scope. `AED8` says that Claude stays supported as an `agent_client`. The two texts do not agree. | [code] |
| T16 | In the Claude desktop app, Code mode (`</>`), the page "플러그인" has the button "추가", the dialog "마켓플레이스 추가" (a URL field for `owner/repo`, button "동기화"), the button "이 컴퓨터에 추가" for a plugin, and the dialog "마켓플레이스 관리". That dialog says that it lists the marketplaces that Claude Code added on this computer. The desktop app and the `claude` command thus use the same plugin store. | [decision] owner screenshots, 2026-11-22 |
| T17 | The plugin page of the desktop app also lists connectors and the MCP servers of plugins. If the plugin works in Cowork, and how a user-scope MCP server signs in from that page, are not known. | [decision] owner screenshot; rest [unverified] |
| T18 | `claude plugin install <plugin> --marketplace <owner/repo>` adds the marketplace and installs the plugin in one step (Claude Code 2.1.275 or later). `claude mcp login <name>` opens the OAuth sign-in in the browser. | [measured] CLI help |
| T19 | A subagent that the Agent tool starts asks for permission for each MCP tool. A skill with `context: fork` gets the tools of its `allowed-tools` without a prompt. A model call of a skill needs the permission `Skill(<plugin>:<skill>)`. | [measured] |

---

## 3. Goal and Non-Goals

Goals:

1. **G1**: A Claude Code user copies one command block from `/settings/mcp`, signs in one time in the browser, and runs `/claire:ingest <url>` to a committed extraction.
2. **G2**: The four defense layers hold. A deterministic check enforces Layer 1, not only text.
3. **G3**: `/settings/mcp` gives Claude the same level of help as Antigravity.
4. **G4**: The bundle stays declarative. The build accepts no file of code. The only executable item is one fixed guard line that the build writes itself (CD1).

Non-goals:

- A server provider that calls Claude (retired by `AED8`).
- A change of Option A for Claude Code. The server stays one global `claire` registration.
- Plugin support in Cowork or claude.ai in this version. It is unit CL10 (CD3) and waits for the observations of Section 10.1.
- A submission to the Anthropic plugin directory (it needs a PNG icon, and the build reads text files only).

---

## 4. Supported Surfaces

| Surface | Status after this design | How the server is attached | Functions |
| :--- | :--- | :--- | :--- |
| Claude Code (terminal, IDE extensions, Code tab of Claude Desktop) | Supported | `claude mcp add --transport http --scope user claire <url>` (tools `mcp__claire__*`) | `/claire:ingest`, `/claire:extract`, `/claire:query`, contribution |
| claude.ai web, Claude Desktop chat, mobile | Query only (CL6) | Custom connector, scope `readonly` | Read tools only. The server refuses raw source reads. |
| Cowork | Not supported yet | Not known (T17) | Unit CL10 (CD3) |

Reason for query only on chat surfaces: these surfaces have no plugin subagent and no hook. A raw source there would enter a context that also has web, mail, and file tools. Layer 1 cannot hold.

---

## 5. Design

### CL1. Claude-native bundle

Change the rendered Claude bundle as follows. The build keeps one common source. Claude-only text goes into `agent_clients/clients/claude/overrides/`, which the build already supports.

| File | Change |
| :--- | :--- |
| `agents/extractor.md` | Frontmatter for Claude: `name: extractor`, `description`, `tools` (the eight `mcp__claire__*` tools, unchanged), `model: inherit`. Remove `subagent` and `mainAgent`. The body is unchanged. |
| `skills/extract/SKILL.md` | Add `context: fork`, `agent: claire:extractor`, `argument-hint: "[document_id]"`, and `allowed-tools` with the eight extractor tools. Write the body as direct instructions to the subagent, because the body becomes the subagent prompt. Use `$ARGUMENTS` for the optional `document_id`. |
| `skills/ingest/SKILL.md` | Add `argument-hint: "<url | file path | note>"` and `allowed-tools` with `mcp__claire__extract_stage`, `mcp__claire__kb_list`, and `Skill(claire:extract)`. The main thread stages the source. It then invokes the `claire:extract` skill with only the `document_id`. The handoff goes through the skill, not through the Agent tool, because only the forked skill gets its tools without permission prompts (T19). |
| `skills/query/SKILL.md` | Add `argument-hint: "<question>"` and `allowed-tools` with the nine read tools (`kb_list`, `kb_overview`, `entity_search`, `entity_get`, `graph_neighbors`, `graph_path`, `graph_context`, `doc_list`, `doc_get`). `contrib_candidates` and `contrib_submit` stay outside `allowed-tools`, so a contribution always asks the user. |
| `hooks/hooks.json` | New. The Layer 1 guard (CL2). |
| `README.md` | New install block (CL4) and the one-step line `/plugin install claire --marketplace fofwisdom/claire-agents` (Claude Code 2.1.275 or later). |

All references to the extractor in Claude text use the name `claire:extractor`. The common templates get this name from the placeholder `{{EXTRACTOR_REF}}`, and the frontmatter of each target from placeholders that the build fills. The Antigravity bundle text does not change.

Result: `/claire:extract` runs fully in the isolated subagent. The top-level agent does not see the raw source, because the skill never runs on the main thread.

### CL2. Layer 1 guard hook

The build writes this file for the Claude target. The content is a constant in `scripts/build_agent_marketplace.py`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "^mcp__.*__extract_read$",
        "hooks": [
          {
            "type": "command",
            "command": "grep -Eq '\"agent_type\" *: *\"claire:extractor\"' && exit 0; echo 'Claire Bible: extract_read is allowed only in the claire:extractor subagent. Use /claire:extract, or give the document_id to claire:extractor.' >&2; exit 2"
          }
        ]
      }
    ]
  }
}
```

Rules:

1. The matcher is a regular expression for every server name. It also stops a main-thread read through a claude.ai connector or another registration of the same server (T6).
2. The check fails closed. If `grep` is not available, the command does not exit 0, and the call stops with exit code 2.
3. The main agent cannot forge the field. It controls only `tool_input`, and a quote inside `tool_input` is escaped as `\"`, so the pattern does not match it.
4. Only `extract_read` returns raw source text. Other tools do not need the guard.
5. The build rejects a Claude bundle whose `hooks/hooks.json` is not equal to the constant, and rejects hooks in any other file. The rule for forbidden file types does not change.

This is code that runs on the computer of the user. Owner decision CD1 permits it.

### CL3. MCP registration (Option A kept)

- The plugin supports one registration: the server name `claire`, added with `--scope user`. The canonical command is `claude mcp add --transport http --scope user claire https://<server>/mcp`.
- Sign-in: `claude mcp login claire`, or `/mcp` in a conversation. The browser opens the existing OAuth 2.1 flow (dynamic client registration, PKCE S256).
- The extractor does not use connector tools (`claude.ai <Name>`). The guides say this, and the troubleshooting section tells the user to add the `claire` registration.

### CL4. One setup block

`/settings/mcp` and the guides show this block. The page fills in the server URL:

```bash
claude plugin install claire --marketplace fofwisdom/claire-agents
claude mcp add --transport http --scope user claire https://<server>/mcp
claude mcp login claire
```

The first line needs only the repository, so it is also correct for a fork (T18). Updates: `claude plugin marketplace update claire-agents`, then `claude plugin update claire@claire-agents`, then a restart. The guides also tell how to turn on auto-update for the marketplace in `/plugin` (T12). A fork operator gets the repository from `CLAIRE_AGENT_MARKETPLACE_REPO`.

### CL0. Guide for users without IT knowledge (owner instruction, 2026-11-22)

The guides `docs/claude.{ko,en}.md` follow the pattern of the Antigravity guide. A user needs no terminal:

1. **Plugin, mouse clicks only**: Code mode of the Claude desktop app, "플러그인" -> "추가" -> "마켓플레이스 추가" -> `fofwisdom/claire-agents` -> "동기화" -> "이 컴퓨터에 추가" (T16).
2. **Server, one pasted prompt**: the user copies the Claude quick setup prompt from `/settings/mcp` and pastes it into a new conversation. Claude runs the CL4 block. The prompt tells Claude to continue when a step is already done, to stop when the `claude` command is not found, and to change no other setting.
3. **Check**: `/claire:query` shows the list of knowledge bases.

The guide has a table of surfaces, a troubleshooting table, and a collapsed part for terminal users. Limits: the English labels of the desktop app are translations of the Korean labels of the screenshots, and the availability of the `claude` command in a desktop conversation is not measured [unverified]. The troubleshooting table covers the case that the command is not found.

### CL5. `/settings/mcp` for Claude

In the card `#pref-mcp-client-bundles`, add rows for Claude with the same pattern as Antigravity:

1. **Claude quick setup prompt**: `<code id="pref-mcp-claude-prompt">` with a copy button `#pref-mcp-copy-claude-prompt` (catalog key `console.js.synd.mcp.claude_prompt`). `console_syndication.js` puts in the endpoint, the same way it does for `#pref-mcp-antigravity-prompt`.
2. **Terminal commands**: `<code id="pref-mcp-claude-setup">` with the CL4 block and a copy button, in the collapsed part of the card.
3. **Claude guide link**: `#pref-mcp-claude-guide`, for the language of the page (`docs/claude.ko.md` or `docs/claude.en.md`). Add `claude_guide_url` to the `agent_marketplace` bootstrap.
4. Change `#pref-mcp-snippet-cli` to `claude mcp add --transport http --scope user claire "<endpoint>"`.
5. Change the Claude notice line to the steps of CL0.

The catalog must not contain the product names that `AED8` retired from the page text (a test enforces this). The page text thus says "Claude app" and "Claude terminal commands". Use catalog keys in `en.json` and `ko.json` from the start. Build the rows with `console_ui.py` and use `.pref-btn` classes (AGENTS.md console rules).

### CL6. Query-only connectors on chat surfaces

- When the client requests `write`, the consent screen shows one checkbox for it, selected by default. The user can clear it. The server then issues a `readonly` token, asks for no step-up, and returns the granted scope in the token response (RFC 6749 Section 3.3). The form has a marker field, so a request without the marker keeps the requested scope. The device flow does not change.
- The Claude guide tells chat-surface users to clear `write`. With `readonly`, the server refuses `extract_read` and other write tools (T13). The defense does not depend on the chat model.
- Other clients do not change: when the user keeps all boxes, the token is the same as today.

This changes the server consent flow. Owner decision CD2 permits it.

### CL7. Guides and documents

- Write `agent_clients/docs/claude.{ko,en}.md` again as the guide of CL0, with the surface table (Section 4), the CL4 block for terminal users, and troubleshooting (scope `local`, connector tool names, no update without a version change).
- Correct the Goal of `MCP_SUPPORT.md` to agree with `AED8` (T15).
- When CL1 to CL5 are implemented, record the current state in `API_AND_MCP_GUIDE.md` and `EXTERNAL_ACCESS.md`, and give this document an implemented status line.

### CL8. Version

Set the marketplace version and the versions of the two clients to `0.3.0`. Without a version change, Claude Code users do not get the new bundle (T12). The Antigravity bundle text does not change, but its guide makes the name of the release file from the marketplace version, so the two clients keep one version.

### CL9. Upload link for local files (proposal)

A new MCP tool gives a single-use upload URL for the personal knowledge base of the caller. The agent sends the file with `curl -F file=@<path> <url>`, and the answer gives the `document_id`. The file bytes do not go through the model context (T14). This also helps Antigravity.

Limits: the URL is valid for 10 minutes and for one upload. It is bound to the account and the personal knowledge base, it has the 20 MiB limit, and it uses the same staging path. Logs record only a hash of the URL. The tool count changes from 17 to 18, which is an MCP contract change. Owner decision CD4 permits it.

---

## 6. Defense Layers on Claude Code

| Layer | Antigravity control | Claude control after this design |
| :--- | :--- | :--- |
| 1. Isolated subagent | Text in `rules/AGENTS.md` and the skills | `/claire:extract` runs as the forked `claire:extractor` subagent (CL1). The fixed hook stops `extract_read` outside that subagent (CL2). On chat surfaces, the `readonly` scope stops the read on the server (CL6). |
| 2. Nonce-bound framing | Server framing and the extractor text | No change (the server and the shared extractor body) |
| 3. Scope and personal knowledge base | OAuth 2.1 and `CT4` on the server | No change. The consent checkboxes add a smaller scope (CL6). |
| 4. Server validation | `extract_submit` gates | No change |

The extractor keeps an allow list (`tools`), not a deny list. With a deny list, mail, drive, and other MCP tools of the user stay open to the extractor, which is a path for data loss.

---

## 7. Build Gate Changes

In `scripts/build_agent_marketplace.py`:

1. Write frontmatter for each target. Reject `subagent:` and `mainAgent:` in Claude files.
2. Write `hooks/hooks.json` for Claude from the constant (CL2). Reject any other hook content.
3. Accept `extract_read` in `allowed-tools` only in a skill that has `context: fork` and `agent: claire:extractor`.
4. Reject an `agent:` value that does not name an agent in the same bundle, and a `Skill(...)` entry that does not name a skill in the same bundle.
5. Reject hook files for a target that is not Claude.
6. Keep the current gates: no script files, no symbolic links, no executable bits, the secret scan, and the tool contract check against `mcp_tools.py`.

Optional [proposal]: run `claude plugin validate` in the marketplace check workflow. This needs the Claude Code package in CI.

---

## 8. Verification

- **Unit tests** (`tests/test_agent_marketplace_build.py`, the interface and the file output of the build): the Claude frontmatter, the exact hook file, the `allowed-tools` rule, the `agent:` reference rule, and the version.
- **E2E test** (`e2e/mcp_claude_setup.spec.js`, run only through `scripts/e2e.sh`): in English and Korean, `#pref-mcp-claude-setup` has the page origin with `/mcp`, `--scope user`, and the marketplace repository. The copy button puts the same text on the clipboard. `#pref-mcp-claude-guide` points to the guide for the page language.
- **Acceptance on Claude Code** [measured] (2026-11-22, Claude Code 2.1.329, the built `v0.3.0` bundle with `--plugin-dir`, a development server with a temporary database, the server `claire` given by `--mcp-config` with a development token, no tools approved in advance):
  1. `/claire:ingest <note>`: `extract_stage` on the main thread, then the `claire:extract` skill. In the subagent: `extract_spec`, `extract_claim`, `extract_read`, `entity_search`, `extract_submit` (dry run), `extract_submit` (commit), `extract_queue`. The extraction was committed with no permission refusal.
  2. `/claire:extract <document_id>`: all tool calls in the subagent, committed. The source had an injected instruction to run a shell command. The subagent did not follow it.
  3. A request to the main agent to call `extract_read` itself was blocked with the guard message.
  4. `/claire:query <question>`: `kb_list`, `entity_search`, `graph_context`, `entity_get`, and an answer with the document ID.
- **Not measured**: the OAuth sign-in from Claude (`claude mcp login`), the steps of CL0 in the desktop app, and the guard on native Windows, where Claude Code runs shell commands through Git Bash [unverified].

---

## 9. Work Units and Order

| Unit | Content | State |
| :--- | :--- | :--- |
| CL0 | Guide for users without IT knowledge (`docs/claude.{ko,en}.md`) | Implemented |
| CL1, CL2, CL8 | Bundle, guard, version, build gates (Section 7), unit tests | Implemented |
| CL3, CL4, CL7 | Setup block, plugin README, `MCP_SUPPORT.md` correction | Implemented |
| CL5 | `/settings/mcp` rows, catalog keys, E2E test | Implemented |
| CL6 | Consent checkbox, smaller token scope (CD2), unit test and E2E test | Implemented |
| CL9 | Upload link tool (CD4) | Open |
| CL10 | Plugin support in Cowork and claude.ai (CD3) | No design. It needs the observations of Section 10.1. |

---

## 10. Owner Decisions (2026-11-22)

| ID | Decision |
| :--- | :--- |
| CD1 | The Claude bundle contains the one fixed guard command of CL2. The build writes it and accepts no other hook. |
| CD2 | The consent screen lets the user clear the `write` scope (CL6). |
| CD3 | Plugin support in Cowork and claude.ai is a goal (CL10). The packaging for those surfaces can be different from Option A. |
| CD4 | The MCP surface gets the upload link tool of CL9. |

The owner also replaced the measurement unit CL0 of the first version with the guide of Section 5 (CL0).

### 10.1 Observations that CL10 needs

CL10 has no design, because these facts are not known. A person who follows the guide in the desktop app can see them:

1. In a Cowork conversation, does the `claire` plugin show its three commands?
2. On the plugin page, after the setup of the guide, does `claire` show in the list "커넥터", and does it have a sign-in control?
3. In Cowork, does a request to read a waiting source directly show the guard message of CL2?

If item 1 is "no", CL10 needs a second packaging (a plugin-bundled MCP server with a `userConfig` URL, tools `mcp__plugin_claire_claire__*`). If item 3 is "no", Cowork has no Layer 1 control, and extraction must stay off that surface.

---

## 11. Rejected Alternatives

| Alternative | Reason for rejection |
| :--- | :--- |
| A `SessionStart` hook that adds the rules text to each session | It adds context in every session of every project. The guard hook already enforces the rule. |
| A plugin `settings.json` that sets the main agent | Not documented for plugins, and it changes the whole session of the user. |
| An extractor with a deny list (`disallowedTools`) | Other MCP tools (mail, drive) stay open to the extractor. |
| An allow list with many server prefixes | The name of a claude.ai connector is the choice of the user. The build cannot know it. |
| A subagent-only MCP server (`mcpServers` in the extractor) | Claude Code ignores this field for plugin subagents (T10). |
| A plugin-bundled MCP server for Claude Code now | It reverses Option A for Claude Code without a need. CD3 keeps this option for the surfaces where Option A cannot work. |

---

## 12. `v0.4.0` Evolution (Superseded 3-Skill Layout)

Marketplace `v0.4.0` retains every empirical lesson (`T1`–`T19`), owner decision (`CD1`–`CD4`), Option A server registration, and 4-layer defense from this design while aligning both Claude and Antigravity under [`AGENT_CLIENT_COMMON_SPEC.md`](AGENT_CLIENT_COMMON_SPEC.md):

1. **2-Axis User-Facing Skills**: The `v0.3.0` 3-skill layout (`/claire:ingest`, `/claire:extract`, `/claire:query`) is superseded by two user-facing skills (`/claire:ingest` and `/claire:query`). Pending queue extraction and empty-queue onboarding (`Flow D`) move into `/claire:ingest`.
2. **Hidden `extract-runner` Shim**: `skills/extract/SKILL.md` is replaced by `skills/extract-runner/SKILL.md` with `user-invocable: false`, `context: fork`, `agent: claire:extractor`, and the eight extractor `allowed-tools` (`T7`, `T19`). `/claire:ingest` invokes `Skill(claire:extract-runner)` instead of exposing a separate extract command.
3. **Updated `PreToolUse` Guard Message (`CD1`)**: `hooks/hooks.json` keeps the exact fail-closed `grep` command on `^mcp__.*__extract_read$`, updated to direct blocked callers to `/claire:ingest` (`"Use /claire:ingest, or give the document_id to claire:extractor."`).
4. **Declarative `[adapter]` & Antigravity `suggestedPrompts`**: `agent_clients/clients/claude/client.toml` and `agent_clients/clients/antigravity/client.toml` declare `[adapter]` settings instead of hardcoded build branches, and Antigravity `suggestedPrompts` is capped at 3 entries.
5. **CL10 Observation Note**: Section 10.1 observation #1 now checks for the two user-facing commands (`/claire:ingest` and `/claire:query`, with `extract-runner` hidden via `user-invocable: false`).
