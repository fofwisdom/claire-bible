# Claude Agent Client: A Working `claire` Plugin for Claude Code

- **Document Identifier**: `DESIGN-CLAUDE-AGENT-CLIENT-20261122`
- **Status**: Proposal. Not implemented. Units CL1 to CL8 are ready to implement after owner question CQ1. Units CL6 and CL9 wait for CQ2 and CQ4. Unit CL0 is an owner measurement for CQ3.
- **Date**: 2026-11-22
- **Base commit**: `ea4804b` (marketplace `v0.2.0`, Option A)
- **Related Documents**:
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
| T16 | Which Claude Desktop surface opens "Marketplace management" (the term in the guides), and if the plugin works in Cowork, are not known. | [unverified] |

---

## 3. Goal and Non-Goals

Goals:

1. **G1**: A Claude Code user copies one command block from `/settings/mcp`, signs in one time in the browser, and runs `/claire:ingest <url>` to a committed extraction.
2. **G2**: The four defense layers hold. A deterministic check enforces Layer 1, not only text.
3. **G3**: `/settings/mcp` gives Claude the same level of help as Antigravity.
4. **G4**: The bundle stays declarative. The build accepts no file of code. The only executable item is one fixed guard line that the build writes itself (CQ1).

Non-goals:

- A server provider that calls Claude (retired by `AED8`).
- A change of Option A for Claude Code. The server stays one global `claire` registration.
- Plugin support in Cowork or claude.ai before measurement CL0 and decision CQ3.
- A submission to the Anthropic plugin directory (it needs a PNG icon, and the build reads text files only).

---

## 4. Supported Surfaces

| Surface | Status after this design | How the server is attached | Functions |
| :--- | :--- | :--- | :--- |
| Claude Code (terminal, IDE extensions, Code tab of Claude Desktop) | Supported | `claude mcp add --transport http --scope user claire <url>` (tools `mcp__claire__*`) | `/claire:ingest`, `/claire:extract`, `/claire:query`, contribution |
| claude.ai web, Claude Desktop chat, mobile | Query only (CL6) | Custom connector, scope `readonly` | Read tools only. The server refuses raw source reads. |
| Cowork | Not supported yet | Not known (T16) | Decide after CL0 (CQ3) |

Reason for query only on chat surfaces: these surfaces have no plugin subagent and no hook. A raw source there would enter a context that also has web, mail, and file tools. Layer 1 cannot hold.

---

## 5. Design

### CL1. Claude-native bundle

Change the rendered Claude bundle as follows. The build keeps one common source. Claude-only text goes into `agent_clients/clients/claude/overrides/`, which the build already supports.

| File | Change |
| :--- | :--- |
| `agents/extractor.md` | Frontmatter for Claude: `name: extractor`, `description`, `tools` (the eight `mcp__claire__*` tools, unchanged), `model: inherit`. Remove `subagent` and `mainAgent`. The body is unchanged. |
| `skills/extract/SKILL.md` | Add `context: fork`, `agent: claire:extractor`, `argument-hint: "[document_id]"`, and `allowed-tools` with the eight extractor tools. Write the body as direct instructions to the subagent, because the body becomes the subagent prompt. Use `$ARGUMENTS` for the optional `document_id`. |
| `skills/ingest/SKILL.md` | Add `argument-hint: "<url | file path | note>"` and `allowed-tools: mcp__claire__extract_stage mcp__claire__kb_list`. The main thread stages the source. It then calls the Agent tool with subagent type `claire:extractor` and gives only the `document_id`. |
| `skills/query/SKILL.md` | Add `argument-hint: "<question>"` and `allowed-tools` with the nine read tools (`kb_list`, `kb_overview`, `entity_search`, `entity_get`, `graph_neighbors`, `graph_path`, `graph_context`, `doc_list`, `doc_get`). `contrib_candidates` and `contrib_submit` stay outside `allowed-tools`, so a contribution always asks the user. |
| `hooks/hooks.json` | New. The Layer 1 guard (CL2). |
| `README.md` | New install block (CL4) and the one-step line `/plugin install claire --marketplace fofwisdom/claire-agents` (Claude Code 2.1.275 or later). |

All references to the extractor in Claude text use the name `claire:extractor`.

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

This is code that runs on the computer of the user. It needs owner decision CQ1.

### CL3. MCP registration (Option A kept)

- The plugin supports one registration: the server name `claire`, added with `--scope user`. The canonical command is `claude mcp add --transport http --scope user claire https://<server>/mcp`.
- Sign-in: start `claude`, type `/mcp`, select `claire`, and select Authenticate. The browser opens the existing OAuth 2.1 flow (dynamic client registration, PKCE S256).
- The extractor does not use connector tools (`claude.ai <Name>`). The guides say this, and the troubleshooting section tells the user to add the `claire` registration.

### CL4. One setup block

`/settings/mcp` and the guides show this block. The page fills in the server URL:

```bash
claude plugin marketplace add fofwisdom/claire-agents
claude plugin install claire@claire-agents
claude mcp add --transport http --scope user claire https://<server>/mcp
```

Then the user signs in with `/mcp` (CL3). Updates: `claude plugin marketplace update claire-agents`, then `claude plugin update claire@claire-agents`, then a restart. The guides also tell how to turn on auto-update for the marketplace in `/plugin` (T12). A fork operator gets the repository from `CLAIRE_AGENT_MARKETPLACE_REPO`, and the marketplace name comes from `marketplace.toml`.

### CL5. `/settings/mcp` for Claude

In the card `#pref-mcp-client-bundles`, add rows for Claude with the same pattern as Antigravity:

1. **Claude Code setup commands**: `<code id="pref-mcp-claude-setup">` with the CL4 block and a copy button `#pref-mcp-copy-claude-setup`. `console_syndication.js` puts in the endpoint, the same way it does for `#pref-mcp-antigravity-prompt`.
2. **Claude guide link**: `#pref-mcp-claude-guide`, for the language of the page (`docs/claude.ko.md` or `docs/claude.en.md`). Add `claude_guide_url` to the `agent_marketplace` bootstrap.
3. Change `#pref-mcp-snippet-cli` to `claude mcp add --transport http --scope user claire "<endpoint>"`.
4. Change the Claude notice line: Claude Code commands and `/mcp` sign-in, not "Marketplace management".

Use catalog keys in `en.json` and `ko.json` from the start. Build the rows with `console_ui.py` and use `.pref-btn` classes (AGENTS.md console rules).

### CL6. Query-only connectors on chat surfaces

- The consent screen shows each requested scope as a checkbox. The user can clear `write`. The server then issues a token with fewer scopes and returns the granted scope in the token response (RFC 6749 Section 3.3).
- The Claude guide tells chat-surface users to clear `write`. With `readonly`, the server refuses `extract_read` and other write tools (T13). The defense does not depend on the chat model.
- Other clients do not change: when the user keeps all boxes, the token is the same as today.

This changes the server consent flow. It needs owner decision CQ2.

### CL7. Guides and documents

- Write `agent_clients/docs/claude.{ko,en}.md` again: the surface table (Section 4), the CL4 block, `/mcp` sign-in, permission prompts in Claude Code, checks (`/plugin` shows `claire` as installed, `/agents` shows `claire:extractor`, `/mcp` shows `claire` as connected), and troubleshooting (scope `local`, connector tool names, no update without a version change).
- Correct the Goal of `MCP_SUPPORT.md` to agree with `AED8` (T15).
- When CL1 to CL5 are implemented, record the current state in `API_AND_MCP_GUIDE.md` and `EXTERNAL_ACCESS.md`, and give this document an implemented status line.

### CL8. Version

Set the Claude client version and the marketplace version to `0.3.0`. Without a version change, Claude Code users do not get the new bundle (T12). The Antigravity client version stays `0.2.0` if its bundle does not change.

### CL9. Upload link for local files (proposal)

A new MCP tool gives a single-use upload URL for the personal knowledge base of the caller. The agent sends the file with `curl -F file=@<path> <url>`, and the answer gives the `document_id`. The file bytes do not go through the model context (T14). This also helps Antigravity.

Limits: the URL is valid for 10 minutes and for one upload. It is bound to the account and the personal knowledge base, it has the 20 MiB limit, and it uses the same staging path. Logs record only a hash of the URL. The tool count changes from 17 to 18, which is an MCP contract change. It needs owner decision CQ4.

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
4. Reject an `agent:` value that does not name an agent in the same bundle.
5. Keep the current gates: no script files, no symbolic links, no executable bits, the secret scan, and the tool contract check against `mcp_tools.py`.

Optional [proposal]: run `claude plugin validate` in the marketplace check workflow. This needs the Claude Code package in CI.

---

## 8. Verification

- **Unit tests** (`tests/test_agent_marketplace_build.py`, the interface and the file output of the build): the Claude frontmatter, the exact hook file, the `allowed-tools` rule, the `agent:` reference rule, and the version.
- **E2E test** (`e2e/mcp_claude_setup.spec.js`, run only through `scripts/e2e.sh`): in English and Korean, `#pref-mcp-claude-setup` has the page origin with `/mcp`, `--scope user`, and the marketplace repository. The copy button puts the same text on the clipboard. `#pref-mcp-claude-guide` points to the guide for the page language.
- **Acceptance on Claude Code** (development server, a temporary `CLAUDE_CONFIG_DIR`):
  1. Run the CL4 block. `/mcp` shows `claire` as connected after sign-in.
  2. `/claire:ingest <url>` gives a committed extraction, and the hook log shows `extract_read` only with `agent_type: "claire:extractor"`.
  3. A request to the main agent to call `extract_read` itself is blocked with the guard message.
  4. `/claire:extract` processes one pending document.
  5. `/claire:query <question>` answers with document IDs.
  6. Find out if `allowed-tools` in a forked skill removes the permission prompts in the subagent [unverified]. If not, the guide tells the user to allow each tool one time.
- **Known limit to examine**: the guard on native Windows. Claude Code runs shell commands through Git Bash there [unverified].

---

## 9. Work Units and Order

| Unit | Content | Depends on |
| :--- | :--- | :--- |
| CL0 | Owner measurement in Claude Desktop: where the plugin loads, which tool prefix a connector gets in Cowork, and if hooks run there | None |
| CL1, CL2, CL8 | Bundle, guard, version, build gates (Section 7), unit tests | CQ1 for CL2 |
| CL3, CL4, CL7 | Guides, plugin README, `MCP_SUPPORT.md` correction | CL1 |
| CL5 | `/settings/mcp` rows, catalog keys, E2E test | CL4 text |
| CL6 | Consent checkboxes, smaller token scope | CQ2 |
| CL9 | Upload link tool | CQ4 |

---

## 10. Open Owner Questions

| ID | Question | Recommendation |
| :--- | :--- | :--- |
| CQ1 | Can the Claude bundle contain the one fixed guard command of CL2? It runs on the computer of the user, but the build writes it, and the build accepts no other hook. | Yes. Without it, Layer 1 on Claude depends only on text, because Claude Code loads no rules file (T2, T4). A `prompt` hook is not deterministic and has a cost for each call. |
| CQ2 | Can the consent screen let the user clear the `write` scope (CL6)? | Yes. Chat surfaces then get a server-enforced read-only connection, and other clients do not change. |
| CQ3 | Do you want plugin support in Cowork and claude.ai? It can need a plugin-bundled MCP server with a `userConfig` URL (tools `mcp__plugin_claire_claire__*`). This is a different packaging from Option A for those surfaces. | Do CL0 first. Decide with the measured data. |
| CQ4 | Can the MCP surface get the upload link tool of CL9 (17 to 18 tools)? | Yes, as a separate release after CL1 to CL5. |

---

## 11. Rejected Alternatives

| Alternative | Reason for rejection |
| :--- | :--- |
| A `SessionStart` hook that adds the rules text to each session | It adds context in every session of every project. The guard hook already enforces the rule. |
| A plugin `settings.json` that sets the main agent | Not documented for plugins, and it changes the whole session of the user. |
| An extractor with a deny list (`disallowedTools`) | Other MCP tools (mail, drive) stay open to the extractor. |
| An allow list with many server prefixes | The name of a claude.ai connector is the choice of the user. The build cannot know it. |
| A subagent-only MCP server (`mcpServers` in the extractor) | Claude Code ignores this field for plugin subagents (T10). |
| A plugin-bundled MCP server for Claude Code now | It reverses Option A for Claude Code without a need. CQ3 keeps this option for the surfaces where Option A cannot work. |
