# Ingest Adapters as Plugins

- **Status**: Implemented (units PL0 to PL4). Design principles only. Current behavior: [`INGESTION.md`](../implementation/INGESTION.md) §2.2 (registry, plugin host, manifest, states, overlap, records, project plugins, security controls), [`COMMANDS.md`](../implementation/COMMANDS.md) §3.19 (`claire plugin`), [`ACCOUNTS.md`](../implementation/ACCOUNTS.md) §5.9 and [`API_HTTP_CONTRACT.md`](../implementation/API_HTTP_CONTRACT.md) §2.2 (`/admin/plugins`, `/api/admin/plugins`), [`ENVIRONMENT_VARIABLES.md`](../implementation/ENVIRONMENT_VARIABLES.md) (`CLAIRE_PLUGINS_DIR`, `CB_PLUGINS_DIR`), [`GLOSSARY.md`](../implementation/GLOSSARY.md) §3 (terms), [`IMPLEMENTATION_STATUS.md`](../implementation/IMPLEMENTATION_STATUS.md) §4.1 item 2 (limits).
- **Date**: 2026-11-18 · revisions 2 to 7 on 2026-11-19 · implemented and condensed 2026-11-19
- **Related**: [`FETCHER_CONTRIBUTION_AND_ONPREMISE_NETWORK_DESIGN.md`](FETCHER_CONTRIBUTION_AND_ONPREMISE_NETWORK_DESIGN.md) (its decisions 1 to 7 stay in force), [`VIDEO_PRESENTATION_BUNDLE_INGESTION_DESIGN.md`](VIDEO_PRESENTATION_BUNDLE_INGESTION_DESIGN.md), [`I18N_DESIGN.md`](I18N_DESIGN.md)

## Source of each item

The owner asked for items O1 to O5 on 2026-11-18 and for items O6 to O8 on 2026-11-19. All other items are agent proposals that the owner accepted on 2026-11-19; they keep their origin as agent proposals.

| ID | Owner request |
| :--- | :--- |
| O1 | An ingest adapter is one plugin type. The goal is easier code contribution and maintenance. |
| O2 | All present ingest adapters move to a path under the plugin folder. |
| O3 | Any person can make a plugin. When it is put in the plugin folder, the settings can switch it on and off. |
| O4 | A plugin has a revision number. It records the core commit that its author followed. These two values are its version. |
| O5 | Propose rules for plugins that follow current practice. |
| O6 | When the manifests of different adapters overlap, the server finds it, and only one of these plugins can be selected. |
| O7 | At the first deployment, the plugins that the project gives are enabled by default. |
| O8 | Plugins that the project makes and plugins that the server administrator installs have different paths. To install (the owner first said "upload") is to put a plugin into the plugin folder. The plugin folder is separate from the data folder, because the data folder holds files that are not customizations. A plugin there is installed by the server administrator, not by a user. Paths: the plugin folder is `var/plugins`, the project plugin folder is `plugins`. |

O2 and O8 together: the present adapters became project plugins in the project plugin folder `plugins/`, not in the plugin folder `var/plugins/` of the server administrator.

## Problem

Site code and general code were in one package. A contributor who added one site edited core files (the module list of the registry, the URL rules of the classifier, the dispatch of the router, the tests). Site rules existed two or three times and used substring matches (a Dropbox link went to the x.com fetcher). The core imported site modules and branched on site source types. A drop-in loader existed, but nothing called it, and an adapter had no version and no switch.

## Decisions in force

| ID | Decision | Reason |
| :--- | :--- | :--- |
| PL-D1 | **Source is a plugin; format is core.** A source is one site, service, or software platform that needs its own code. A format (HTML, PDF, ODT, text, media, video and PDF composition) is core. | A format service is necessary for every ingest; a source adapter is optional by nature. |
| PL-D2 | **Media host hints stay in the core.** A host for which yt-dlp is the complete fetcher is data of the media route; a host that needs Claire code of its own is a plugin. | A list of hosts has no code to contribute. |
| PL-D3 | **One folder for each plugin** under a type folder that has the name of the plugin type. | Code, manifest, tests, and fixtures move together; a reviewer sees one plugin in one folder. |
| PL-D4 | **A declarative manifest** that the server reads without an import. Unknown keys are errors. | The administrator sees a plugin, its hosts, and its permissions before its code runs. |
| PL-D5 | **Version = plugin revision + core commit (O4).** A separate integer API level is the only compatibility check; there is no "nearest level". | A container has no Git history to compare commits. The core commit stays as the record of origin. |
| PL-D6 | **Only the server administrator switches plugins.** Switching on needs a recent sign-in and the digest that the administrator saw; switching off is the emergency stop and needs only the session. | A plugin is code that runs in the server process for all accounts. An approval must apply to the code that the administrator saw. |
| PL-D7 | **Records, not implicit state.** The first start of the plugin store on a server enables the project plugins (O7); after that a plugin without a record is `new` and does not load. | The first deployment, and the upgrade that brought plugins, keep the present behavior; a later plugin needs a choice (C1). |
| PL-D8 | **Discovery does not execute code.** The core imports a plugin only in the state `enabled`. | A plugin that is off must not run. |
| PL-D9 | **The folder decides the origin (O8).** The project plugin folder holds only project plugins, checked by a lock file; the plugin folder holds only plugins of the server administrator, which load only with an approved digest. No shadowing between the two. | A project plugin is trusted with its release; a changed file of an installed plugin stops it until a new approval. |
| PL-D10 | **A plugin imports only the plugin SDK; the core never imports a plugin.** The SDK surface is versioned by the API level and kept as a snapshot. | A change inside the core does not break plugins, and a plugin can move without a core edit. |
| PL-D11 | **Explicit registration** through `register(ctx)`; the manifest and the registration must agree. | The host can attach each entry to its plugin and leave it out when the plugin is off. |
| PL-D12 | **Declared exact host patterns decide the match** (`example.com`, `*.example.com`, `*`); never a substring. | The console shows the true reach of a plugin, and misrouting by substring stops. |
| PL-D13 | **The old drop-in loader is removed** (single files, entry points, class decorators). | Dead code without manifest, version, or switch. |
| PL-D14 | **Overlapping plugins are exclusive (O6).** The server finds overlaps from the manifests; only one plugin of an overlap group can be on, and the administrator names the plugins that go off. The server never selects by priority or by age. | No hidden choice between two plugins for the same host (C1). |
| PL-D15 | **The plugin folder is outside the data folder (O8)** and is a read-only mount in the container; the image gives the process user only the data folder and the vault. | The data folder holds what the server writes; plugin code is a customization that the process must not change. |
| PL-D16 | **One frozen source type of a plugin in the core:** `youtube` of stored documents. New media plugins use `video`; other plugin values are opaque to the core. | Stored rows keep their values; core branches stay on core types. |
| PL-D17 | **A plugin failure stays local.** An import error, a registration mismatch, or an entry exception changes only the state or the count of that plugin; a process never stops for a plugin. | Operator rule: no manual action at start. |

## Rules for code contribution (O5)

1. **One plugin for one source.** Shared code goes into the core SDK, never into an import from another plugin. A new project plugin must not overlap a project plugin; extend that plugin instead.
2. **Self-contained folder.** Manifest, code, `README.md`, tests, and fixtures are in the folder of the plugin. Tests never use the network. Test file names start with `test_<Python name>` (pytest needs unique names), and tests load the plugin with the SDK test helper.
3. **Raise the plugin revision for each committed change**, and set the core commit to the base commit (a commit cannot contain its own hash: the parent commit). The lock enforces it for project plugins.
4. **Commit message**: Conventional Commits with the plugin id as scope, and a Git trailer with the revision:

   ```text
   fix(claire.youtube): keep chapter titles when captions are absent

   Plugin-Revision: 4
   ```

5. **License**: an SPDX expression. A project plugin is MIT, as the repository. Do not copy code from a project whose license is not compatible. For a site parser, read the site and its documentation, not the code of another parser.
6. **Maturity**: `experimental` for a new plugin, `stable` after it has fixtures for its main page kinds, `deprecated` with the text of the replacement.
7. **Texts**: `[text.en]` in ASD-STE100. A project plugin also has `[text.ko]`.
8. **Hosts**: exact patterns only. `can_handle` refuses by path, never by a substring of the URL.
9. **Network and processes**: only through the SDK network client, or through a module named in `permissions.network_modules`; process modules only with `permissions.executables`.
10. **Identifiers** follow the existing styles: snake_case values and keys, `<object>.<verb>` audit actions, kebab-case CLI commands, `<publisher>.<snake_case>` plugin ids.

## Rejected alternatives

| Alternative | Why not |
| :--- | :--- |
| Pip packages with entry points | An operator cannot install a package into the image. A drop-in folder is the request (O3). |
| Flat single-file plugins (the old `load_plugins_from_dir`) | No manifest, no place for tests, and the scan executes each file. |
| Import all plugins at start, then filter by state | Executes the code of plugins that are off. |
| Plugins in `src/claire/plugins/` | Inside the core package; a drop-in is not possible in the image. |
| Semantic versioning, or the API level as a part of the version | The owner defined the version as two values (O4). |
| Media host hints as plugins | They have no code; yt-dlp is the fetcher (PL-D2). |
| A data migration that marks the stored `youtube` documents | Rewrites stored rows for a label; one frozen constant changes no data. |
| Overlapping plugins together, ordered by priority; or the first enabled plugin wins | The administrator could not tell which plugin takes a URL (C1). |
| "First deployment" by the absence of `claire.db` | Other processes can make `claire.db` before `claire migrate`; the row of the plugin store is exact. |
| A changed project plugin loads as an installed plugin | O8: the two kinds have separate folders. |
| The name "user plugin" and plugin folders inside the data folder | The server administrator installs a plugin, not a user; the data folder holds what the server writes (O8). |
| A file upload in the console | Not requested. To install is to put a folder into the plugin folder (O8). |
| A sandbox now (subprocess with JSON-RPC, or WebAssembly) | The media adapters need yt-dlp, ffmpeg, and Chromium; high cost for a server without third-party plugins. Possible later for installed plugins only. |
| On and off for each account; plugin-owned settings | Not requested. A plugin is server code; settings stay in the core. |

## Limits for later changes

- A new plugin type (for example an export format) needs its own design: its contract, its type folder, and its SDK names.
- A break of the SDK surface needs a new API level; the old level goes in the same change while no external plugin exists.
- Out-of-process isolation, plugin-owned settings with secrets, and a console upload are not designed.

## Practice that this design follows

Reference date: general knowledge of these projects as of 2026. No code or text is copied.

| Practice | Examples | Used here |
| :--- | :--- | :--- |
| One folder and a declarative manifest that the host reads before activation | VS Code extension manifest (`contributes`), Grafana `plugin.json`, Home Assistant integration `manifest.json`, Obsidian `manifest.json` | PL-D3, PL-D4 |
| An integer plugin version and a declared core requirement | Moodle `version.php` (`version`, `requires`, `maturity`), Obsidian `minAppVersion` | Plugin revision, API level, maturity |
| A checksum list of all plugin files, and an explicit administrator choice for plugins without a trusted origin | Grafana plugin signature (`MANIFEST.txt`) | Digest, PL-D9 |
| Separate folders for shipped extensions and extensions that the administrator installs | WordPress plugin folder apart from core files, Home Assistant `custom_components` | O8, PL-D15 |
| Declared permissions that the administrator sees before activation | Browser extension host permissions, Deno permission flags | `[permissions]`, the switch dialog |
| Host match patterns with exact semantics | Browser extension match patterns | PL-D12 |
| A quality ladder for contributed integrations | Home Assistant integration quality scale | Maturity, conformance checks |
| A record of the source revision of a build | SLSA provenance, Git trailers | Core commit, `Plugin-Revision` trailer |
| License identifiers | SPDX | `license` |
| Explicit registration through a host object | pluggy, Home Assistant setup functions | `register(ctx)` |
| Process isolation for third-party plugins | HashiCorp go-plugin, WebAssembly component model | Rejected for now |
