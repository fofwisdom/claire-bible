# Ingest Adapters as Plugins

- **Status**: Planned. Not implemented. Current behavior of fetchers, web adapters, and the registry: [`INGESTION.md`](../implementation/INGESTION.md) §2. Units PL0 to PL4 (§13).
- **Date**: 2026-11-18 · revision 2 on 2026-11-19 · basis commit `b869440` (each fact in §1 and §4 was read in the code at this commit)
- **Related**: [`FETCHER_CONTRIBUTION_AND_ONPREMISE_NETWORK_DESIGN.md`](FETCHER_CONTRIBUTION_AND_ONPREMISE_NETWORK_DESIGN.md) (its decisions 1 to 7 stay in force), [`ADMIN_AND_SETTINGS_PATH_SEPARATION_DESIGN.md`](ADMIN_AND_SETTINGS_PATH_SEPARATION_DESIGN.md), [`CONSOLE_LAYOUT_DESIGN.md`](CONSOLE_LAYOUT_DESIGN.md), [`DATA_STORE_LAYOUT_DESIGN.md`](DATA_STORE_LAYOUT_DESIGN.md), [`I18N_DESIGN.md`](I18N_DESIGN.md)

## 0. Source of each item

The owner asked for these five items (2026-11-18). All other items in this document are agent proposals. The owner has not decided them.

| ID | Owner request |
| :--- | :--- |
| O1 | An ingest adapter is one plugin type. The goal is easier code contribution and maintenance. |
| O2 | All present ingest adapters move to a path under the plugin folder. |
| O3 | Any person can make a plugin. When it is put in the plugin folder, the settings can switch it on and off. |
| O4 | A plugin has a revision number. It records the core commit that its author followed. These two values are its version. |
| O5 | Propose rules for plugins that follow current practice. |

## 1. Problem

Facts at the basis commit:

1. **Site code and general code are in one package**, `src/claire/ingest/fetchers/`. A contributor who adds one site must edit core files: the module list in `registry.load_builtin_fetchers`, the URL rules in `FetcherRegistry.classify`, the dispatch in `router.fetch`, and tests in `tests/`.
2. **Site rules exist two times**: in the `can_handle` methods of the registered fetchers, and in the URL rules of `classify` and `router.fetch` (YouTube, x.com, share links, HoYoWiki, HoYoLAB). `video.is_video_or_audio_url` has a third copy for Naver and VMware Explore hosts.
3. **The core imports site modules directly**: `router.fetch` (HoYoLAB, HoYoWiki, YouTube, share link, x.com), `video.fetch_video` (Naver Clip, VMware Explore), `cli.cmd_hoyowiki`, and through it `scripts/crawl_hoyowiki.py`.
4. **Host rules use substring matches.** Results of `registry.classify` and `registry.get_fetcher` at the basis commit:

   | URL | Result | Cause |
   | :--- | :--- | :--- |
   | `https://www.dropbox.com/s/abc/file.pdf` | `xcom`, `XComFetcher` | `"x.com" in host` |
   | `https://www.fox.com/news/a` | `xcom`, `XComFetcher` | same |
   | `https://notyoutube.com/x` | `youtube`, `YouTubeFetcher` | `"youtube.com" in host` |

   Also: `naver_clip.is_naver_clip_url` searches the full URL, so a query value `naver.me` sends any URL to the Naver Clip fetcher (priority 120). `get_matching_web_adapters` matches `*.hoyolab.com` with `hostname.endswith("hoyolab.com")`, so `nothoyolab.com` matches.
5. **Core code branches on site source types.** The value `youtube` is read in `composite.compose_composite_document`, `pipeline` (media badges), `graphview` (author of the share card), `extract/provider.py` (entity type of the mock provider), and `store/db.py` (the content hash rule of the audit, two places).
6. **A drop-in loader exists, but nothing calls it** (`load_plugins_from_dir`, `load_entrypoints`; [`IMPLEMENTATION_STATUS.md`](../implementation/IMPLEMENTATION_STATUS.md) §4.1 item 2). It has no manifest, no version, and no on/off control. It executes each file when it scans the folder.
7. **An adapter has no version of its own.** An operator cannot see which adapter code runs, and cannot stop one adapter without a code change.
8. **Network access is not uniform.** Most adapters use `http.guarded_client` or `SafeHttpClient`. `hoyowiki.HoyowikiClient` checks the URL once and then sends with a plain `httpx.Client`. `presentation_vmware_explore` has its own DNS check. `youtube` calls `yt_dlp` and `youtube_transcript_api`, which open their own connections.
9. **A web adapter error is silent.** `fetch_web` catches every exception of a web adapter and continues with `pass`. No log names the adapter.

## 2. Terms

| Term | Korean | Meaning |
| :--- | :--- | :--- |
| plugin | 플러그인 | A folder with a manifest (`plugin.toml`). It adds a function to Claire without an edit of core files. It has exactly one plugin type. |
| plugin type | 플러그인 유형 | The contract that a plugin implements. This design defines one type: `ingest-adapter`. Each other type needs its own design. |
| ingest adapter | 적재 어댑터 | The plugin type for one source (§3 PL-D1). It gives one or more registry entries. It takes URLs only. |
| registry entry | 레지스트리 항목 | One fetcher or one web adapter, as in [`INGESTION.md`](../implementation/INGESTION.md) §2.3. These two kinds do not change. |
| core | 코어 | The package `src/claire`. |
| plugin SDK | 플러그인 SDK | The module `claire.plugin_sdk`: the only part of the core that a plugin imports (§7). |
| plugin API level | 플러그인 API 수준 | An integer. It identifies one version of the plugin SDK contract. It is a compatibility field, not a part of the version. |
| plugin revision | 플러그인 리비전 | A positive integer. The author increases it by 1 for each committed change of the plugin. |
| core commit | 추종 커밋 | The full 40-character hash of the Claire Bible commit that the author used as the base of the plugin revision. |
| plugin version | 플러그인 버전 | The pair (plugin revision, core commit), as O4 says. Written `r3 @ 6010d69b`. |
| plugin digest | 플러그인 다이제스트 | A SHA-256 value of all files of the plugin (§6.3). |
| bundled plugin | 동봉 플러그인 | A plugin in `<app root>/plugins/` whose id and digest agree with `plugins/bundled.lock`. |
| local plugin | 로컬 플러그인 | Any other plugin. |
| maturity | 성숙도 | Manifest field: `experimental`, `stable`, or `deprecated`. |
| state | 상태 | Loading state that the server computes (§8.1). |

Term collisions found:

1. "Contribution" (기여) is already a term of shared knowledge bases (`kb_contributions`). This document says "code contribution" (코드 기여) for a change to the repository.
2. "Revision" is already used for contribution revisions (`kb_contribution_revisions`). This document always says "plugin revision".
3. "Adapter" already names the registry kind "web adapter". The plugin type is always "ingest adapter". An ingest adapter can contain a web adapter.
4. "코어" in [`GLOSSARY.md`](../implementation/GLOSSARY.md) §1 is the knowledge core (지식 코어). Here "core" (코어) is the code package. Korean text writes "코어 패키지" when the context does not make it clear.
5. Revision 1 used the manifest key `status` and the word "state" for two different things. Revision 2 renames the key to `maturity`.

Proposal for the owner: add "plugin", "ingest adapter", "plugin revision", "core commit", and "plugin version" to [`GLOSSARY.md`](../implementation/GLOSSARY.md). This design does not change the glossary.

## 3. Decisions (proposed)

| ID | Decision | Reason |
| :--- | :--- | :--- |
| PL-D1 | **Source is a plugin; format is core.** A source is one site, service, or software platform that needs its own code. A format is a kind of content that many sources give: HTML, PDF, ODT, plain text and AsciiDoc, audio and video media. | A format parser and the general web chain are necessary for every ingest. If an operator switches them off, ingest stops for all sources. A source adapter is optional by nature. |
| PL-D2 | **Media host hints stay in the core.** A host for which yt-dlp is the complete fetcher (Vimeo, TikTok, Instagram reels, Chzzk, the Brightcove player) is data of the core media route, not an adapter. A host that needs Claire code of its own is a plugin. | yt-dlp is the media format service. A list of hosts that it takes has no code to contribute. |
| PL-D3 | **One folder for each plugin**, `plugins/<type folder>/<plugin id>/`. The type folder for ingest adapters is `ingest`. | A reviewer sees one plugin in one folder. Code, manifest, tests, and fixtures move together. |
| PL-D4 | **A declarative manifest** `plugin.toml`. The server reads it without an import. | The console must show a plugin, its hosts, and its permissions before anyone executes its code. |
| PL-D5 | **Version = plugin revision + core commit (O4).** A separate integer, the plugin API level, is the only compatibility check. | A container has no Git history, so it cannot find the distance between two commits. An integer level is a check that the server can do. The core commit stays as the record of origin. |
| PL-D6 | **The server administrator switches plugins on and off** in the console `/admin/plugins`. A member cannot. | A plugin is code that runs in the server process for all accounts. |
| PL-D7 | **No implicit state.** A plugin without a record is `new` and does not load. `claire migrate` writes `enabled` records for the bundled plugins that replace present behavior (§11). | Principle C1 and the rule against a fallback that drives behavior. The migration keeps the present behavior. |
| PL-D8 | **Discovery does not execute code.** The server imports a plugin only when its state is `enabled`. | A plugin that the administrator did not switch on must not run. |
| PL-D9 | **The origin comes from the root and the lock.** A plugin in `<app root>/plugins` that agrees with `bundled.lock` is bundled and is trusted with the release. Every other plugin is local and loads only with an approved digest. A change of one file stops a local plugin until the administrator approves the new digest. | An approval must apply to the code that the administrator saw. An upgrade of the server must not need a new approval for each bundled plugin. |
| PL-D10 | **The plugin imports only the plugin SDK from the core. The core never imports a plugin module.** | Then a change inside the core does not break plugins, and a plugin can move without a core edit. |
| PL-D11 | **Explicit registration.** The manifest names an entry function `register(ctx)`. The import of a plugin module has no registration side effect. The class decorators `register_fetcher` and `register_web_adapter` are removed. | The loader can attach each entry to its plugin, compare it with the manifest, and leave it out when the plugin is switched off. |
| PL-D12 | **Declared hosts decide the match.** The registry calls `can_handle` only for a URL whose host matches the `hosts` of the entry (§7.3). Host patterns are exact, never substring. | The console shows the true reach of a plugin. It removes the substring errors of §1 item 4. |
| PL-D13 | **The old loader is removed**: `load_plugins_from_dir`, `load_entrypoints`, the entry point group `claire.fetchers`, and the folder `plugins/fetchers/`. | Dead code. Pip-installed plugins are not part of this design (§12). |

## 4. Scope

### 4.1 Moves to plugins (O2)

| Present code | Present registry entries and rules | Plugin id |
| :--- | :--- | :--- |
| `naver_clip.py`, the Naver Clip branch of `video.fetch_video`, the Naver Clip hosts of `video.is_video_or_audio_url` | fetcher `naver_clip` (priority 120) | `claire.naver-clip` |
| `hoyowiki.py`, `cli.cmd_hoyowiki` and its parser, `scripts/crawl_hoyowiki.py` | fetcher `hoyowiki` (120), web adapter `hoyowiki` (40), CLI command `claire hoyowiki`, the HoYoWiki rule in `classify` and `router.fetch` | `claire.hoyowiki` |
| `hoyolab.py` | fetcher `hoyolab` (115), web adapter `hoyolab` (38), the HoYoLAB rule in `classify` and `router.fetch` | `claire.hoyolab` |
| `youtube.py` | fetcher `youtube` (90), the YouTube rule in `classify` and `router.fetch` | `claire.youtube` |
| `xcom.py` | fetcher `xcom` (90), the x.com rule in `classify` and `router.fetch` | `claire.xcom` |
| `law.py` | web adapter `law` (20) | `claire.law-go-kr` |
| `uttu.py` | web adapter `uttu` (35) | `claire.uttu` |
| `discourse.py` | web adapter `discourse` (30, no domain list) | `claire.discourse` |
| `redirect.py` | the share link rule in `classify` and the `redirect` branch of `router.fetch` | `claire.share-link` (a new fetcher entry) |
| the VMware Explore parts of `presentation_vmware_explore.py` (`vmware_explore_video_id`, discovery, download), the VMware branch of `video.fetch_video`, the VMware rule of `video.resolve_video_target_url` and `is_video_or_audio_url` | no entry now | `claire.vmware-explore` (a new fetcher entry) |

### 4.2 Stays in the core (PL-D1, PL-D2)

| Code | Why it stays |
| :--- | :--- |
| `web.py` (general web chain: static fetch, Scrapling, CDP, VLM) | The route for every URL that no adapter takes. Adapters call it too (x.com falls back to it). |
| `video.py` without the parts of §4.1 | Media format service: yt-dlp, publisher captions, STT, the media host hints (PL-D2), and the general Brightcove embed rule. The entry `video` becomes a core route. |
| `extract_presentation`, `compose_video_presentations`, `compose_video_presentation` (now in `presentation_vmware_explore.py`) | Video and PDF composition is a format service ([`VIDEO_PRESENTATION_BUNDLE_INGESTION_DESIGN.md`](VIDEO_PRESENTATION_BUNDLE_INGESTION_DESIGN.md)). They move to a core module. |
| `captions.py` | Media format service. |
| `pdf.py`, `odt.py`, `textfile.py`, `adoc_text.py` | Format parsers and text helpers. |
| `http.py`, `http_policy.py`, `guard.py` | Network guard and content guard. They are security limits; a plugin must not replace them. |
| `base.py` | Its contracts move into the plugin SDK. |
| `registry.py`, `router.py` | The core keeps only format routes: media URL, `web`, `file`, `text`. All site rules come from enabled plugins. |

### 4.3 Core couplings that the move removes

| Coupling | Change |
| :--- | :--- |
| `router._clean_url` runs for a fixed list of kinds that contains site kinds | It runs for every URL payload. |
| `classify` returns `cls.name()`, so the Naver Clip fetcher gives the kind `video` | The kind is the entry name (`naver_clip`). The kind is a label for the bot and the router only. |
| Core reads of the source type `youtube` (§1 item 5) | §7.5. |
| `ops/cb_manuscript.py` lists `hoyowiki` as an app command; the `cli.py` help text names it | Replaced by `plugin`. |
| `tests/` hold the tests of the moved adapters: `test_youtube_fetcher.py`, `test_xcom.py`, `test_hoyowiki.py`, `test_hoyolab_fetcher.py`, `test_law_fetch.py`, `test_naver_clip.py`, `test_redirect.py`, `test_uttu_fetcher.py`, `test_e2e_hoyolab_vlm_ingest.py`, `test_e2e_naver_clip_stt.py`, `test_e2e_uttu_ingest.py`, and the VMware parts of `test_video_presentation.py` | They move into the plugin folders (§11). |

## 5. Folder layout and origin

```text
plugins/                         # in Git; in the image at <app root>/plugins
  bundled.lock                   # id, plugin revision, digest of each bundled plugin
  ingest/                        # type folder of the ingest-adapter type
    _template/                   # copied by `claire plugin new`; a name with "_" never loads
    claire.youtube/
      plugin.toml                # manifest
      __init__.py
      adapter.py                 # register(ctx) and the registry entry classes
      README.md                  # for contributors; the server does not render it
      tests/
        test_claire_youtube.py   # unique file name (§14 rule 2)
        fixtures/                # recorded responses; tests never use the network
<data dir>/plugins/ingest/<id>/  # local plugins, in a container and in a checkout
```

- **Two roots, one layout.** The server scans `<app root>/plugins` and `<data dir>/plugins`. The app root comes from `config.resolve_app_root` (`CLAIRE_APP_ROOT` in the image, the checkout in a source tree). In a container, each upgrade replaces `/app`; an operator can write only the data volume.
- **Origin (PL-D9).** Bundled: in the app root, and the id and digest agree with `bundled.lock`. Local: all others. A contributor who changes a bundled plugin in a checkout makes it local until `claire plugin lock` (§6.2). Then the change is visible, and the conformance test fails until the lock agrees.
- **No shadowing.** A plugin in the data root with an id that exists in the app root, or with the publisher `claire`, gets the state `invalid`.
- `.gitignore` stops to ignore `plugins/`. Local plugins go to the data root, also in a checkout, so Git does not show them.
- The Dockerfile copies `plugins/` into the image.
- `pyproject.toml` adds `plugins` to the pytest `testpaths`.

## 6. Manifest and version

### 6.1 `plugin.toml`

TOML, read with `tomllib` from the standard library (the image uses Python 3.11).

```toml
manifest = 1                     # format version of this file
id = "claire.youtube"            # equal to the folder name
type = "ingest-adapter"          # equal to the type folder
revision = 1                     # plugin revision (O4)
core_commit = "b8694402b6ee0ce08f5af1a6af2853bb911575d5"   # core commit (O4)
api = 1                          # plugin API level (PL-D5)
entry = "adapter:register"       # module in this folder : function
license = "MIT"                  # SPDX license expression
maturity = "stable"              # experimental | stable | deprecated
maintainers = ["github:example"]

[text.en]                        # required; ASD-STE100
name = "YouTube"
description = "Gets the metadata and the captions of YouTube videos. Uses speech to text when a video has no captions."

[text.ko]                        # required for a bundled plugin
name = "YouTube"
description = "YouTube 영상의 메타데이터와 자막을 가져온다. 자막이 없으면 음성 인식을 쓴다."

[[provides]]
kind = "fetcher"                 # fetcher | web-adapter
name = "youtube"                 # registry entry name and classify kind
priority = 90
hosts = ["youtube.com", "*.youtube.com", "youtu.be"]
source_types = ["youtube"]

[permissions]
network = ["youtube.com", "*.youtube.com", "youtu.be"]
private_network = false
network_modules = ["yt_dlp", "youtube_transcript_api"]   # §9 S6
executables = ["ffmpeg"]
providers = ["stt"]

[requires]
distributions = ["yt-dlp", "youtube-transcript-api"]     # PEP 508; checked, never installed
executables = []
```

Rules:

1. **Id.** `<publisher>.<name>`, lowercase, pattern `^[a-z][a-z0-9]*(\.[a-z][a-z0-9-]*)+$`. The folder name is the id. The publisher `claire` is reserved for bundled plugins.
2. **Plugin revision.** Starts at 1. Increases by 1 for each committed change of any file of the plugin. It never decreases.
3. **Core commit.** The full hash of the commit that the author used as the base. A commit cannot contain its own hash. Thus, when a commit changes a bundled plugin, the core commit of the plugin is the parent commit. In a checkout, `claire plugin check` confirms that the core commit is an ancestor of `HEAD`.
4. **API level.** The core declares the supported levels (`PLUGIN_API_LEVELS`). A plugin with another level gets the state `incompatible`. There is no "nearest level" (C1).
5. **`provides`.** Each registry entry that `register(ctx)` makes must have one item here, with the same kind, name, priority, and hosts. A difference gives the state `invalid`. Reason: the console shows this list before an import (PL-D4).
6. **`hosts`** (PL-D12): `example.com` matches only that host. `*.example.com` matches each subdomain, not `example.com`. `*` matches every host; the console shows a badge for it. No other pattern.
7. **`permissions.network`** uses the same patterns. It can differ from `provides.hosts`: the x.com adapter takes `x.com` URLs but calls `api.fxtwitter.com`.
8. **`source_types`** (§7.5).
9. **Texts.** `[text.en]` is required. The console shows `[text.<interface language>]` and, when it is absent, `[text.en]`: the same lookup as the catalogs ([`I18N.md`](../implementation/I18N.md)). The console does not render HTML or AsciiDoc from a manifest.
10. **Unknown keys** give the state `invalid`. The manifest version (`manifest`) changes when a key is added.

### 6.2 `bundled.lock`

```toml
[[plugin]]
id = "claire.youtube"
revision = 1
digest = "sha256:…"
```

- `claire plugin lock` writes it. It refuses a new digest for a plugin whose revision did not increase.
- A conformance test fails when a digest in the tree differs from the lock. Result: a code contribution that changes a bundled plugin must raise its revision.

### 6.3 Digest

1. List each regular file in the plugin folder. Skip `__pycache__/` and `*.pyc`.
2. For each file, make the line `<sha256 of the content>  <path relative to the plugin folder>`, with `/` as separator.
3. Sort the lines by path. Join them with `\n`.
4. The digest is `sha256:` and the SHA-256 of that text.

A person can make the same lines with `sha256sum`. A symbolic link or a path outside the folder gives the state `invalid`.

## 7. Plugin SDK and registration

### 7.1 `claire.plugin_sdk` (API level 1)

The SDK gives names that exist now under one stable surface. A plugin imports nothing else from `claire`. The present location is in brackets.

| Area | Names |
| :--- | :--- |
| Contracts | `Document`, `SourceAttachment` (`ontology.base`), `FetchError`, `BaseFetcher`, `BaseWebAdapter`, `WebAdapterResult` (`fetchers.base`), `PluginContext` (new) |
| Settings | `fetch_settings`, `attach_usage_hooks` (`fetchers.base`). The fields of `Settings` that bundled plugins read are part of the level. |
| Network | `ctx.http_client(**httpx options)`: an `httpx.Client` with the request hook of `http.guarded_client` plus a check of `permissions.network` on each request and each redirect hop. `ctx.safe_get(url)`: `SafeHttpClient.get` with the same host check. `BROWSER_USER_AGENT`. The httpx types that a plugin needs: `Response`, `HTTPError`, `RequestError`, `TimeoutException`. |
| Text | `canonicalize_url`, `content_hash` (`ingest.normalize`), `slice_document_text` (`extract.table_budget`), `adoc_cell`, `adoc_table`, `adoc_list_item`, `adoc_link_text` (`fetchers.adoc_text`), `extract_html` (now `web._extract_html`), `fetch_web` |
| Formats | `extract_pdf_bytes`, `slice_pdf_text` (`fetchers.pdf`), `render_html_cdp` (`fetchers.web`) |
| Media | `fetch_media` (the core part of `fetch_video`), `acquire_caption`, `format_chapters`, `CaptionAcquisition` (`fetchers.captions`), `parse_ytdlp_extractor_args`, the video cache functions (`store.video_cache`), `extract_presentation`, `compose_video_presentations` |
| Providers | `get_transcript_provider`, `get_vision_provider`, `emit_progress`, `ProviderUnavailableError` |
| Routing | `reroute(url)`: routes a resolved URL again, with the depth limit of the router |
| CLI | `ctx.cli_command(name, build_parser, run)`, `ingest_document(...)` for a command that ingests |

- A test keeps a snapshot of the SDK surface (names and signatures). A change of the snapshot needs a decision: a compatible addition (same level) or a break (new level). At a break, the old level is removed in the same change, because no external plugin exists yet. Local plugins of the old level then show `incompatible`.
- `ctx` also gives the plugin id, its folder, and a logger with the plugin id in each record.

### 7.2 `register(ctx)`

```python
from claire.plugin_sdk import PluginContext

def register(ctx: PluginContext) -> None:
    ctx.fetcher(YoutubeFetcher, name="youtube", priority=90, hosts=("youtube.com", "*.youtube.com", "youtu.be"))
```

- `ctx.fetcher`, `ctx.web_adapter`, and `ctx.cli_command` record the entry with the plugin id.
- The registry orders entries by priority (higher first), then by plugin id. The order is deterministic. The console shows a warning when two enabled entries have the same priority and overlapping hosts.
- `can_handle` must be fast and must not use the network. The conformance test runs it with the network blocked.

### 7.3 Matching and errors

1. The registry parses the URL once (lowercase host, IDNA). It calls `can_handle` only for entries whose `hosts` match (PL-D12). `can_handle` can refuse a URL by its path; it cannot add hosts.
2. An exception from `can_handle` counts as "no match" and is logged with the plugin id.
3. An exception from a fetcher stops the ingest, as now (`raw_inbox.status = "error"`).
4. An exception from a web adapter passes control to the next step of the web chain, as now. New: the log line names the plugin id and the entry, and the long-running process counts the errors for each entry in its process report (§8.3). The present `except Exception: pass` in `fetch_web` goes away.

### 7.4 Commands of a plugin

A plugin command runs as `claire plugin run <id> <command> [arguments]`. Thus `claire hoyowiki …` becomes `claire plugin run claire.hoyowiki crawl …`, and on a deployed host `./cb-manuscript app plugin run claire.hoyowiki crawl …` (C5). A plugin that is not enabled gives exit code 4 (precondition failed, C3). Reason: no name collision with core commands; the help of `claire` does not change with the state of plugins. `scripts/crawl_hoyowiki.py` is removed; the plugin command replaces it.

### 7.5 Source types

1. The core knows a closed set of source types: `web`, `video`, `pdf`, `odt`, `text`, `file`, `custom`. Core code branches only on these.
2. One value of a plugin is frozen in the core: `youtube`. Stored documents have it. The core treats it as `video` in each place of §1 item 5, and the audit keeps its content hash rule (body only). A constant `LEGACY_MEDIA_SOURCE_TYPES = ("youtube",)` holds it in one place.
3. A bundled plugin keeps its present values, because stored documents use them: `youtube`, `xcom`, `hoyowiki`, `hoyolab`, `video` for Naver Clip and VMware Explore, `web` for web adapters.
4. A new plugin that gives audio or video uses `video`. Any other new value is the plugin id or starts with `<id>:`. The core does not branch on it.

## 8. States and loading

### 8.1 States

The loader checks in this order and stops at the first state that applies.

| State | Meaning | Loads |
| :--- | :--- | :--- |
| `invalid` | Manifest error, id and folder differ, shadowing (§5), symbolic link, a bundled plugin fails the import check, or `provides` differs from the registration | No |
| `incompatible` | The API level is not supported | No |
| `missing-requirement` | A declared distribution or executable is absent | No |
| `new` | No record | No |
| `disabled` | Record is off | No |
| `changed` | Local plugin: the digest is not the approved digest | No |
| `enabled` | Record is on, all checks pass | Yes |
| `failed` | The import or `register(ctx)` raised an error. The console shows the error text. | No |
| `removed` | A record exists, but the folder is absent | No |

`invalid`, `incompatible`, `missing-requirement`, and `failed` keep the record as it is. When the cause goes away, the plugin loads again with no action. `changed` needs a new approval (PL-D9).

### 8.2 Records

- One `claire.db` `server_settings` row for each plugin, key `plugin:<id>`. JSON value: `enabled`, `approved_digest`, `approved_revision`. The columns `updated_at` and `updated_by` of the table hold the time and the administrator. No schema version change.
- Switching on stores the digest that the administrator saw, for each origin. The `changed` check uses it only for local plugins.
- Only the new store module `src/claire/store/plugins.py` reads and writes these keys (store boundary R2 of [`DATA_STORE_LAYOUT_DESIGN.md`](DATA_STORE_LAYOUT_DESIGN.md)).

### 8.3 Loading in each process

The `api` process, the bot, the worker loops, and the CLI each do this at the first use of the registry:

1. **Discover.** Read each `plugin.toml` in the two roots. Compute each digest. Compare the app root with `bundled.lock`. Do not import.
2. **Decide.** Read the records. Compute the state of each plugin.
3. **Load.** For each `enabled` plugin: import its folder as the package `claire_plugins.ingest.<id with "." and "-" changed to "_">` with `importlib.util.spec_from_file_location`, then call the entry function. No change to `sys.path`.
4. **Report.** Each long-running process writes `data/run/plugins_<process>.json`: id, version, digest, state, and the error counts of §7.3 of each plugin. The CLI writes no report. The console and the support bundle read these files.

A failure of one plugin never stops the start of a process (operator rule). It changes only the state of that plugin.

### 8.4 Effect of a switch

- **Off**: each process reads the records at the start of each ingest job and of each `classify` call of the bot, and uses only the entries of `enabled` plugins. The module stays in memory until the next restart, but no job uses it.
- **On**: at the start of the next job, a process imports the plugin if it did not import it before.
- **New code of a loaded plugin**: Python cannot unload a module safely. The process keeps the old code until a restart. The console compares the process reports with the files and shows "restart needed" for each process.
- Stored documents do not change. A later refetch of a URL uses the routing of that time.

## 9. Security

**Trust model.** A plugin runs in the server process with all rights of that process. It can read the files, the databases, and the environment of the server. Switching a plugin on means trusting its author as much as the core. This design does not make a sandbox. It controls who can switch a plugin on, which code runs, and what the administrator sees before the switch.

| ID | Control |
| :--- | :--- |
| S1 | Discovery reads TOML and file hashes only. No import, no `.pth` file, no change to `sys.path` (PL-D8). |
| S2 | Only the administrator switches a plugin. Switching on needs a recent sign-in (`security.step_up_response`: 401 `reauth_required`, 403 `step_up_requires_session` for a bearer token). Switching off needs only the administrator session: it is the emergency stop and it must be fast. |
| S3 | The request to switch on carries the digest that the administrator saw. When the files changed after the page was read, the server answers 409 `plugin_changed` and changes nothing. |
| S4 | The loader refuses symbolic links, files outside the plugin folder, and files or folders that other users can write. In the data root, each file must belong to the process user or to `root`. |
| S5 | No shadowing (§5). |
| S6 | Network access goes through `ctx.http_client()` or `ctx.safe_get()`: the private network guard (principle B2) plus the host list of `permissions.network`. An import check finds direct network modules (`httpx`, `requests`, `urllib.request`, `http.client`, `socket`, `aiohttp`, `yt_dlp`, `curl_cffi`, `scrapling`, `playwright`) and process modules (`subprocess`, `os.system`, `os.exec*`); the SDK keeps the list. A plugin can import only the network modules that it names in `permissions.network_modules`. The console shows a badge "direct network" for them. The check is static: it does not stop code that hides its imports. |
| S7 | The SDK gives the fetch settings only. It gives no access to `SecretService`, `auth.db`, or the tokens of accounts. |
| S8 | `admin_audit` in `auth.db` (`auth_db.record_admin_audit`) records `plugin.enable` and `plugin.disable` with `plugin_id`, `revision`, `core_commit`, `digest`, and `origin`. |
| S9 | The support bundle adds `diagnostics/plugins.json`: states, versions, digests, process reports, and load error texts after the present masking. It contains no plugin files. |
| S10 | **Change to the image** (now `chown -R 1000:1000 /app` gives all of `/app` to the process user): `/app/src`, `/app/.venv`, and `/app/plugins` belong to `root` and the process user cannot write them. The entrypoint already gives `/app/data` and `/app/vault` to the process user. PL3 runs the dev container and confirms that no process writes elsewhere under `/app`. A server that runs with `CB_UID=0` runs as `root`, and this control does not apply; the console shows a warning. |
| S11 | Limit: the loader computes the digest, then Python reads the files again at import. A process that can write the data root between the two steps can change the code. Only the process user and `root` can write there (S4). |

Out-of-process isolation is possible later for local plugins (§12). It is not in this design.

## 10. Settings: console, API, and CLI

### 10.1 Console `/admin/plugins`

- A new section of the sidebar group `admin_system`, after `providers` and `limits` (`ADMIN_CONSOLE_GROUP_5_SPECS` in `api/accounts_routes/console.py`). Rule: administrator only; 404 for all other callers (stealth 404).
- A table with a caption. One row for each plugin: name and id, type, version (`r3 @ 6010d69b`; the full hash in the details), origin (bundled or local), maturity, state badge, switch. Above the table, the server shows the commit of the running core, as the support bundle finds it (`support_bundle._get_git_commit_and_source`), so that the administrator can compare. On a narrow container, the table becomes stacked rows (the present `.claire-table td[data-label]` pattern).
- The switch is `<input type="checkbox" switch>` with a label. For a state that cannot load, the switch is `disabled`, and `aria-describedby` points to the reason text.
- A `<details>` element in each row shows the description, the `provides` list with hosts, the permissions, the requirements, the current and the approved digest, maintainers, license, the load error, and the process reports.
- Switching on opens a modal `<dialog>` with `aria-labelledby` on its heading. The dialog lists the permissions, the hosts, the network modules, and the digest. `autofocus` is on Cancel. A 401 `reauth_required` answer shows the present step-up form, then sends the request again.
- All interface text is in the `en` and `ko` catalogs from the start ([`I18N_DESIGN.md`](I18N_DESIGN.md) ID5).

### 10.2 HTTP API

| Route | Behavior |
| :--- | :--- |
| `GET /api/admin/plugins` | The list of §10.1 as JSON. |
| `PUT /api/admin/plugins/{id}` | Body `{"enabled": true, "digest": "sha256:…"}` or `{"enabled": false}`. `digest` is required to switch on (400 `digest_required`, no default). 404 for an unknown id. 409 `plugin_changed` (S3). 409 `plugin_not_loadable` with the state. Step-up for `true` (S2). |
| `DELETE /api/admin/plugins/{id}` | Removes the record of a `removed` plugin only. 409 when the folder exists. |

No MCP tool. Changes go to [`API_HTTP_CONTRACT.md`](../implementation/API_HTTP_CONTRACT.md) and `openapi.yaml` in the same commit as the code.

### 10.3 CLI

ASD-STE100 English, `--json` with the `AgentOutput` contract (C3). On a deployed host: `./cb-manuscript app plugin …` (C5); `ops/cb_manuscript.py` adds `plugin` to the app commands.

| Command | Use |
| :--- | :--- |
| `claire plugin list` | States of all plugins. |
| `claire plugin show <id>` | Manifest, digest, and records of one plugin. |
| `claire plugin enable <id> --digest <sha256:…>` | The host operator path. The same store and audit. `--digest` is required. |
| `claire plugin disable <id>` | Same. |
| `claire plugin check [<folder>]` | For contributors: manifest, digest, lock, import check, core commit ancestry (in a checkout), and conformance tests. It does not use the server state. |
| `claire plugin lock` | Writes `bundled.lock` (§6.2). |
| `claire plugin new <id> --type ingest-adapter` | Copies `_template` into the data root (a `claire.` id: into the app root), sets `revision = 1`, `api`, and `core_commit`. The core commit is the commit of the running core, from the same function as the console. Exit code 4 when it gives no full 40-character hash. |
| `claire plugin run <id> <command> …` | §7.4. |

## 11. Migration

1. `claire migrate` (principle E3) writes an `enabled` record for each plugin id of §4.1, when no record exists. It logs the count. The operator does nothing. The list of ids is fixed in the code. A later release that moves present behavior into a plugin adds that id to the list; a release that adds a new source does not, and the new plugin is `new` (PL-D7). The release notes name it.
2. Each moved plugin starts at `revision = 1`. Its core commit is the parent of the commit that moves it.
3. The tests of §4.3 move into `plugins/ingest/<id>/tests/` with unique file names. Fixtures move with them.
4. `claire hoyowiki` becomes `claire plugin run claire.hoyowiki …`. [`COMMANDS.md`](../implementation/COMMANDS.md), the command list of `ops/cb_manuscript.py`, and the help text of `cli.py` change in the same commit.
5. Old drop-in files in `plugins/fetchers/` were never loaded. After the `.gitignore` change, Git shows them as untracked; the person who owns the checkout deletes them.

## 12. Rejected alternatives

| Alternative | Why not |
| :--- | :--- |
| Pip packages with entry points | An operator cannot install a package into the image. A drop-in folder is the request (O3). |
| Flat single-file plugins (the present `load_plugins_from_dir`) | No manifest, no place for tests, and the scan executes each file. |
| Import all plugins at start, then filter by state | Executes the code of plugins that are off. |
| Plugins in `src/claire/plugins/` | Inside the core package. A drop-in is not possible in the image, and a contributor still edits the core tree. |
| Semantic versioning | The owner chose plugin revision and core commit (O4). The API level carries the compatibility information that a major version would carry. |
| API level as a part of the version | O4 defines the version as two values. The level is a separate compatibility field. |
| Media host hints (Vimeo, TikTok, …) as plugins | They have no code; yt-dlp is the fetcher (PL-D2). |
| A data migration that writes a new mark into the stored `youtube` documents | Rewrites stored rows for a label. One frozen constant (§7.5) costs less and changes no data. |
| A sandbox now (subprocess with JSON-RPC, or WebAssembly) | Real isolation, but the media adapters need yt-dlp, ffmpeg, and Chromium. High cost for a server that has no third-party plugin yet. Possible later for local plugins only. |
| On and off for each account | Not requested. A plugin is server code. |
| Plugin-owned settings with a schema in the manifest | Not requested. The present settings stay in the core. A later design can add it when a local plugin needs a secret. |

## 13. Units

Each unit ends with a full test run that passes.

| Unit | Content |
| :--- | :--- |
| PL0 | Independent of plugins, can go first: exact host rules for x.com, YouTube, Naver Clip, and the `*.` web adapter match (§1 item 4), with unit tests for the URLs of that table. |
| PL1 | `claire.plugin_sdk` (level 1, snapshot test), manifest parser, discovery, digest, `bundled.lock`, `store/plugins.py`, loader with states, host matching (§7.3), process reports, registry entries with plugin ids, conformance suite, import check, `claire plugin list/show/check/lock/new/enable/disable/run`. Removal of the old loader and the decorators (PL-D11, PL-D13). |
| PL2 | Move of the adapters of §4.1, one commit for each plugin, in this order: `law-go-kr`, `discourse`, `uttu`, `xcom`, `hoyolab`, `hoyowiki`, `naver-clip`, `youtube`, `share-link`, `vmware-explore`. Each moved plugin uses the SDK network client and passes the import check (`hoyowiki` and `vmware-explore` change their HTTP code). Removal of the site rules from `classify`, `router.fetch`, and `video.py`, and of the couplings of §4.3. The constant of §7.5. Migration (§11). |
| PL3 | `/admin/plugins`, the HTTP API, audit, support bundle file, catalog keys, Dockerfile and `.gitignore` changes (S10), and one Playwright E2E test through `scripts/e2e.sh`: put a local test plugin (offline, fixture answer) in the data root, see `new`, switch it on with step-up, ingest a payload that it takes, switch it off, ingest the same payload, and see the core route. |
| PL4 | Implementation documents: [`INGESTION.md`](../implementation/INGESTION.md) §2, [`COMMANDS.md`](../implementation/COMMANDS.md), [`ACCOUNTS.md`](../implementation/ACCOUNTS.md) §5.9, [`API_HTTP_CONTRACT.md`](../implementation/API_HTTP_CONTRACT.md), `openapi.yaml`, [`IMPLEMENTATION_STATUS.md`](../implementation/IMPLEMENTATION_STATUS.md) §4.1 item 2. Then condense this design to principles. |

Tests follow the project rule: unit tests for module interfaces (SDK, registration), I/O (discovery, digest, store, process reports), and calls to external libraries; the conformance suite for every plugin; one E2E test for the feature.

## 14. Rules for code contribution (O5)

1. **One plugin for one source.** Shared code goes into the core SDK, never into an import from another plugin.
2. **Self-contained folder.** Manifest, code, `README.md`, tests, and fixtures are in the plugin folder. Tests never use the network. Test file names are `test_<id with "." and "-" changed to "_">*.py`, because pytest needs unique names in folders without `__init__.py`. Tests load the plugin with the SDK test helper, not with an import of the folder name.
3. **Raise the plugin revision for each change**, and set the core commit to the base commit (§6.1 rules 2 and 3). The lock test enforces it for bundled plugins.
4. **Commit message**: Conventional Commits with the plugin id as scope, and a Git trailer with the revision:

   ```text
   fix(claire.youtube): keep chapter titles when captions are absent

   Plugin-Revision: 4
   ```

5. **License**: an SPDX expression in the manifest. A bundled plugin is MIT, as the repository. Do not copy code from a project whose license is not compatible. For a site parser, read the site and its documentation, not the code of another parser.
6. **Maturity**: `experimental` for a new plugin, `stable` after it has fixtures for its main page kinds, `deprecated` with the text of the replacement. The console shows it.
7. **Texts**: `[text.en]` in ASD-STE100. A bundled plugin also has `[text.ko]`.
8. **Hosts**: exact patterns only (§6.1 rule 6). `can_handle` refuses by path, never by a substring of the URL.
9. **Network and processes**: only through the SDK, or through a module named in `permissions.network_modules` (S6).

## 15. Practice that this design follows

Reference date: general knowledge of these projects as of 2026. No code or text is copied. Verify the details again at implementation time.

| Practice | Examples | Used here |
| :--- | :--- | :--- |
| One folder and a declarative manifest that the host reads before activation | VS Code extension manifest (`contributes`), Grafana `plugin.json`, Home Assistant integration `manifest.json`, Obsidian `manifest.json` | PL-D3, PL-D4, `provides` |
| An integer plugin version and a declared core requirement | Moodle `version.php` (`version`, `requires`, `maturity`), Obsidian `minAppVersion` | Plugin revision, API level, `maturity` |
| A checksum list of all plugin files, and an explicit administrator choice for plugins without a trusted origin | Grafana plugin signature (`MANIFEST.txt` with SHA-256 for each file, unsigned plugins only by an explicit setting) | §6.3, PL-D9 |
| Declared permissions that the user sees before activation | Browser extension host permissions, Deno permission flags | `[permissions]`, the switch dialog |
| Host match patterns with exact semantics | Browser extension match patterns | §6.1 rule 6 |
| A quality ladder for contributed integrations | Home Assistant integration quality scale | `maturity`, conformance suite |
| A record of the source revision of a build | SLSA provenance, Git trailers | Core commit, `Plugin-Revision` trailer |
| License identifiers | SPDX | `license` |
| Explicit registration through a host object | pluggy, Home Assistant setup functions | `register(ctx)` |
| Process isolation for third-party plugins | HashiCorp go-plugin, WebAssembly component model | Rejected for now (§12) |
