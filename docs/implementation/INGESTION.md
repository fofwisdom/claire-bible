# Ingestion: router, fetchers, budgets, and media bundles

Basis: commit d15ae8e0 (2026-11-16) · Readers: agents that change how Claire fetches and prepares source text · Settings and their defaults: [`ENVIRONMENT_VARIABLES.md`](ENVIRONMENT_VARIABLES.md) · STT and VLM provider selection: [`PROVIDERS.md`](PROVIDERS.md) · Pipeline overview: [`PLAN.md`](PLAN.md) · Commands: [`COMMANDS.md`](COMMANDS.md)

This document tells what the ingestion code does now, from the input payload to the `Document` that goes to extraction. It uses file paths and function names as anchors, not line numbers.

- **[code]** fact read in the source at the basis commit
- **[measured]** value found by a run (the conditions are given)
- **[decision]** rule that the owner set
- **[unverified]** not examined

Design reasons: [`PDF_INGESTION_AND_ADAPTIVE_EFFORT_DESIGN.md`](../design/PDF_INGESTION_AND_ADAPTIVE_EFFORT_DESIGN.md), [`INITIAL_FULL_INGESTION_AND_EFFORT_DESIGN.md`](../design/INITIAL_FULL_INGESTION_AND_EFFORT_DESIGN.md), [`TABLE_INGESTION_DESIGN.md`](../design/TABLE_INGESTION_DESIGN.md), [`VIDEO_AUDIO_TRANSCRIPTION_AND_INGESTION_DESIGN.md`](../design/VIDEO_AUDIO_TRANSCRIPTION_AND_INGESTION_DESIGN.md), [`VIDEO_PRESENTATION_BUNDLE_INGESTION_DESIGN.md`](../design/VIDEO_PRESENTATION_BUNDLE_INGESTION_DESIGN.md), [`TELEGRAM_ATTACHMENT_LINK_COMPOSITE_INGESTION_DESIGN.md`](../design/TELEGRAM_ATTACHMENT_LINK_COMPOSITE_INGESTION_DESIGN.md), [`FETCHER_CONTRIBUTION_AND_ONPREMISE_NETWORK_DESIGN.md`](../design/FETCHER_CONTRIBUTION_AND_ONPREMISE_NETWORK_DESIGN.md).

---

## 1. Path from payload to document

1. The CLI, the Telegram bot, and the HTTP API call `IngestService.ingest` in `src/claire/ingest/service.py`. The bot and the API also call `IngestService.ingest_composite` (§11). [code]
2. `pipeline.ingest` in `src/claire/ingest/pipeline.py` writes the payload to `raw_inbox` first, then calls the fetch function inside `bound_fetch_settings(settings)`. The default fetch function is `router.fetch`. [code]
3. `bound_fetch_settings` and `fetch_settings` in `src/claire/ingest/fetchers/base.py` give each fetcher the settings of the job (the theme owner account context). A fetcher reads settings through `fetch_settings(None, get_settings)`. It falls back to the global settings only outside a job. `attach_usage_hooks` adds budget hooks to STT and VLM providers in a member context. [code]
4. A fetch error stops the ingest. The `raw_inbox` row gets `status="error"` and the error text. Replay uses the same row. [code]
5. For `pdf` and `odt` documents, `pipeline.ingest` calls `reconcile_pdf_metadata` before extraction (§6.4). [code]
6. When the account's `accounts.extraction_mode == "client"` and `force_provider=False` (`resolve_extraction_mode` in `src/claire/store/db.py`), `pipeline.stage_raw` (`src/claire/ingest/pipeline.py`) stages the source with `full_content=True` into `documents` (`extraction_status='pending'`, `content_hash`, `raw_chars`) and `raw_inbox` (`status='staged'`) without calling a server LLM provider (`recover-loop` skips `'staged'` rows). An attached `agent_client` claims, reads, and submits the extraction via [`EXTRACTION.md`](EXTRACTION.md) §9; `force_provider=True` (`--force-provider` CLI flag) overrides client mode back to server provider extraction. [code]
7. Otherwise `extract_resolve_store` calls `validate_extractable_content` first (§12), then selects the reasoning effort (§7), then calls the LLM. [code]

## 2. Router and registry

### 2.1 `router.fetch`

`fetch(payload, *, full_content=False)` in `src/claire/ingest/router.py`: [code]

1. If the payload does not start with `http://` or `https://`, `extract_shared_url` takes the last token when it is a URL ("title + link" share text). A URL in the middle of a note does not change the note.
2. `classify` gives the kind. `_clean_url` removes trailing text after the first URL token for URL kinds.
3. `registry.get_fetcher(payload)` returns the first registered `BaseFetcher` whose `can_handle` is true. If one matches, the router calls its `fetch`.
4. Else the router dispatches on the kind: `video`, `file` (`fetch_file`), `web` (`fetch_web`), else `text` (`fetch_text`). Site kinds come only from plugin entries (step 3). `claire.plugin_sdk.reroute` routes a resolved URL again, at most two levels.
5. `_call_fetcher` passes `full_content`. If the fetcher does not accept it (`TypeError`), the router calls it without the argument.

### 2.2 `FetcherRegistry` and plugins

`src/claire/ingest/registry.py` keeps one module object, `registry`. Design: [`INGEST_ADAPTER_PLUGIN_DESIGN.md`](../design/INGEST_ADAPTER_PLUGIN_DESIGN.md). [code]

- An entry (`RegisteredEntry` in `src/claire/plugin_host/context.py`) has a kind (`fetcher` or `web_adapter`), a name, a priority, host patterns, a class, and a plugin id (`None` for a core entry). The entries come from `_core_entries()` and from the enabled plugins (`plugin_host.runtime`). Order: priority (higher first), then plugin id, then name.
- Host rules use exact patterns from `src/claire/ingest/hosts.py`: `example.com` matches only that host, `*.example.com` matches only its subdomains, `*` matches every host, and there is no substring match (`dropbox.com` is not an x.com host). The registry calls `can_handle` only for a URL whose host matches the entry. An exception from `can_handle` is "no match" and is logged with the entry.
- `classify(payload)` is the single classifier. The router and `classify_input` in `src/claire/telegram_bot.py` both call it. It returns the name of the first fetcher entry that takes the payload (the Naver Clip fetcher gives `naver_clip`), else `web` for a URL, then local file rules, else `text`. A share link gives `share_link` (project plugin `claire.share_link`).
- `web_adapter_entries(url, domain_only)` returns web adapter entries whose hosts match. An entry with `*` is general and comes only when `domain_only` is false.
- The old drop-in loader (`load_plugins_from_dir`, `load_entrypoints`, `plugins/fetchers/`) and the decorators `register_fetcher` and `register_web_adapter` are removed.

Core entry: fetcher `video`, priority 80, hosts `*` (`can_handle` is `is_video_or_audio_url`), `src/claire/ingest/fetchers/video.py`. All site entries come from plugins. [code]

Project plugins (`plugins/ingest_adapter/<id>/`, enabled at the first start of the plugin store): [code]

| Plugin | Kind | Name | Priority | Hosts |
|---|---|---|---|---|
| `claire.law_go_kr` | web adapter | `law` | 20 | `law.go.kr`, `*.law.go.kr` |
| `claire.discourse` | web adapter | `discourse` | 30 | `*` (general, after the static fetch) |
| `claire.uttu` | web adapter | `uttu` | 35 | `uttu.merui.net`, `*.merui.net` |
| `claire.xcom` | fetcher | `xcom` | 90 | x.com, Twitter, and mirror hosts (calls only `api.fxtwitter.com`, `api.vxtwitter.com`) |
| `claire.hoyolab` | fetcher | `hoyowiki` | 120 | `hoyolab.com`, `*.hoyolab.com` (entry pages) |
| `claire.hoyolab` | fetcher | `hoyolab` | 115 | `hoyolab.com`, `*.hoyolab.com` (articles) |
| `claire.hoyolab` | web adapter | `hoyowiki` | 40 | `wiki.hoyolab.com`, `*.hoyolab.com` |
| `claire.hoyolab` | web adapter | `hoyolab` | 38 | `hoyolab.com`, `*.hoyolab.com` |
| `claire.naver_clip` | fetcher | `naver_clip` | 120 | Naver Clip hosts and `m.naver.com` (`/shorts` path); source type `video` |
| `claire.youtube` | fetcher | `youtube` | 90 | YouTube hosts; network modules `yt_dlp`, `youtube_transcript_api`; source type `youtube` (§2.2 source types) |
| `claire.share_link` | fetcher | `share_link` | 100 | `*`; `can_handle` takes `share.google` and hosts that start with `share.`; routes the destination again with `reroute` |
| `claire.vmware_explore` | fetcher | `vmware_explore` | 110 | `vmware.com`, `www.vmware.com` (`/explore/video/<digits>`); source type `video` |

#### Plugins [code]

- Source types (`src/claire/ingest/source_types.py`): core code branches only on `web`, `video`, `pdf`, `odt`, `text`, `file`, `custom`, and on the frozen value `youtube` of stored documents (`LEGACY_MEDIA_SOURCE_TYPES`): the core treats it as media (`is_media_source_type`), and the audit hashes its body only (`BODY_ONLY_HASH_SOURCE_TYPES`). A new plugin that gives audio or video uses `video`.
- Folders: the project plugin folder `<app root>/plugins` (in Git; `plugins/project.lock` holds id, revision, and digest of each project plugin) and the plugin folder of the server administrator `CLAIRE_PLUGINS_DIR` (default `<app root>/var/plugins`). Each plugin is `<folder>/<type>/<id>/` with `plugin.toml`, where `<type>` is `ingest_adapter` or `agent_client` (`PLUGIN_TYPES`, `AE1a`). A folder name that starts with `_` or `.` never loads (`plugins/ingest_adapter/_template`). Project plugins include `ingest_adapter` fetchers/web adapters and the reference `agent_client` bundle `plugins/agent_client/claire.antigravity/` (`client_id = "antigravity"`, `AE7`).
- `src/claire/plugin_host/`: `manifest.py` (parser; unknown keys are errors), `files.py` (digest: SHA-256 of the sorted `sha256sum` lines, `files_sha256` per declared file, `PluginFileError`, and `check_no_executable_files` rejecting POSIX executable mode bits, symlinks, and non-regular files), `lock.py`, `importcheck.py` (AST: `ingest_adapter` may import only `claire.plugin_sdk` from core, and both `ingest_adapter` and `agent_client` block `bible*` and `claire_bible*` imports, network modules only when `permissions.network_modules` names them, and process modules only with `permissions.executables`; `agent_client` blocks any `claire*` import including `claire.plugin_sdk`, `ctypes`/`_ctypes`/`importlib`, network modules, process modules/functions, and `builtins.__import__`/`exec`/`eval`; `tests/` not checked), `discovery.py` (states and overlap groups, no import), `runtime.py` (loading for `ingest_adapter`, skipping Python import for `agent_client`, serving `Snapshot.agent_clients()`), `service.py` (list, switch, `get_agent_client`, delete record, audit), `cli.py`.
- Manifest `plugin.toml` (format version `manifest = 1`):
  - Common fields: `id` (`<publisher>.<snake_case>`, two parts matching the folder name; Python name replaces `.` with `_`), `type` (`ingest_adapter` or `agent_client`, matching `<type>`), `revision` (>= 1), `core_commit` (40 hex chars), `api` (`PLUGIN_API_LEVELS = (1,)`), `license`, `maturity` (`experimental`, `stable`, `deprecated`), `maintainers`, `[text.en]` (required) and `[text.<locale>]` (`name`, `description`), `[requires]` (`distributions`, `executables`). The console shows `[text.<locale>]`, else `[text.en]`.
  - `ingest_adapter` fields: `entry` (`<module>:<function>`), `[[provides]]` (`kind` `fetcher` or `web_adapter`, `name` unique per kind, `priority`, `hosts`, `source_types`), `[permissions]` (`network`, `private_network`, `network_modules`, `executables`, `providers` `stt`/`vlm`).
  - `agent_client` fields (`AE1a`): `client_id` (`^[a-z0-9][a-z0-9-]{1,31}$`), `required_scopes` (non-empty subset of `("readonly", "write")`), `files` (non-empty relative POSIX paths under the bundle folder; forbids `..`, `/`, `plugin.toml`, `__pycache__`, files under `tests/`, and disallowed suffixes `.py`, `.pyc`, `.pyo`, `.so`, `.dll`, `.dylib`, `.sh`, `.bash`, `.zsh`, `.ps1`, `.bat`, `.cmd`, `.exe`). `entry` and `[[provides]]` are forbidden, and `[permissions]` fields must all be empty or false.
- Version: plugin revision and core commit (`r<revision> @ <8 characters>`). `claire plugin lock` refuses a new digest without a revision above the lock of `HEAD`; a conformance test (`tests/test_plugin_host.py`, `claire plugin check`) fails when a project plugin differs from the lock or two project plugins overlap.
- States in check order: `invalid`, `unlocked`, `incompatible`, `missing_requirement`, `new`, `disabled`, `changed`, `overlap`, `enabled`; for `ingest_adapter`, a load attempt can give `failed` (exception) or `invalid` (`register(ctx)` differs from `[[provides]]`); a record without a folder is `removed`. `requires.distributions` holds distribution names; the check is that the distribution is installed.
- Overlap: two `ingest_adapter` plugins overlap when entries of the same kind have the same name, or entries have a common host (`*` is no overlap); two `agent_client` plugins overlap when they declare the same `client_id`. Only one plugin of an overlap group can be on; when two records are on, both get `overlap` and neither loads.
- Records: one `claire.db` `server_settings` row, key `plugins` (`src/claire/store/plugins.py`). When the row is absent and at least one valid project plugin exists, the first refresh or `claire migrate` writes it with an `enabled` record for each project plugin.
- `runtime().current()` gives a snapshot at most one second old (a switch in the same process reads again at once). An enabled `ingest_adapter` plugin is imported once as `claire_plugins.ingest_adapter.<id with "." changed to "_">`; enabled `agent_client` bundles are never imported as Python and are served read-only by `clients_host` ([EXTERNAL_ACCESS.md](EXTERNAL_ACCESS.md)). New code of a loaded `ingest_adapter` needs a restart (`restart_needed` in the report). A failure changes only the state of that plugin. The long-running processes (`api`, `bot`, `recover_loop`, `refresh_loop`, `expand_loop`) write `data/run/plugins_<process>.json`.
- `fetch_web` logs an exception of a web adapter with the plugin id and the entry, counts it in the process report, and continues with the next step.
- Console and API: `/admin/plugins` and `GET`/`PUT`/`DELETE /api/admin/plugins` (`src/claire/api/accounts_routes/plugins.py`; [`API_HTTP_CONTRACT.md`](API_HTTP_CONTRACT.md) §2.2). Switching on needs the digest that the administrator saw and a recent sign-in; `replace` must name exactly the enabled plugins of the overlap group. The CLI `claire plugin enable/disable` uses the same service and audit. The support bundle has `diagnostics/plugins.json`.
- Container: `/app` (src, `.venv`, `plugins`) belongs to `root`; the process user writes only `/app/data` and `/app/vault`. The plugin folder is the read-only mount of `CB_PLUGINS_DIR` at `/app/var/plugins`; `cb-manuscript` resolves it, refuses nesting with the data folder, the vault, the backups, and the project plugin folder, and backs it up (component `plugins`). [code] [measured: image build and `claire migrate` as uid 1000 on 2026-11-19]
- Discovery reads TOML and file hashes only and never imports code. A plugin imports only `claire.plugin_sdk` from the core; the core imports a plugin only in the state `enabled` (`plugin_host.runtime.import_plugin`). The project plugin folder holds only the publisher `claire` and plugins in the lock; the plugin folder refuses the publisher `claire` and the ids of project plugins (no shadowing).
- Audit (`auth.db` `admin_audit`): `plugin.enable`, `plugin.disable` (with `replaced_by` for a replaced plugin), `plugin.delete_record`, with `plugin_id`, `revision`, `core_commit`, `digest`, `origin`, and `via` (`console` or `cli`).
- `claire.plugin_sdk` (API level 1) is the surface for plugins; `tests/test_plugin_sdk_surface.py` keeps its snapshot. `claire.plugin_sdk.testing.load_plugin` loads a plugin for its tests without records.
- `ctx.http_client()` and `ctx.safe_get()` add the host list of `permissions.network` to the private network guard on each request and redirect hop. Without `permissions.private_network`, a plugin never reaches private addresses, whatever the server allows.

### 2.3 Fetcher contract

`src/claire/ingest/fetchers/base.py`: [code]

- `BaseFetcher`: class methods `can_handle(url)` and `fetch(url, **kwargs) -> Document`. Built-in fetchers also give `name()`.
- `BaseWebAdapter`: class methods `name()` and `try_fetch(url, **kwargs) -> WebAdapterResult | None`. `None` passes control to the next step of the web chain.
- `WebAdapterResult` is a dataclass: `title`, `text`, `links`, `anchors`, `images`, `is_pdf`, `doc_type`, `parser_info`, `biblio`.
- `FetchError`: the message goes to the user and to `raw_inbox.error`.

`tests/test_fetcher_conformance.py` checks every registry entry against this contract and runs `can_handle` with the network blocked. [code]

## 3. Web fetch chain

`fetch_web(url, *, full_content=False)` in `src/claire/ingest/fetchers/web.py` tries these steps in sequence. A step result is usable when `_is_usable` is true: the text has at least `MIN_CONTENT` (300) characters and `validate_web_content` passes. If no step is usable, the longest text is kept for the error message and `FetchError` stops the ingest. [code]

1. Domain web adapters (`get_matching_web_adapters(url, domain_only=True)`).
2. `_fetch_static`: `SafeHttpClient.get` (§4), then by response type:
   - media response (`MediaResponseDetected`, media content type or disposition): `fetch_video` takes over;
   - ODT: `extract_odt_bytes`;
   - PDF (content type, disposition, `.pdf` path, or `%PDF-` bytes): `extract_pdf_bytes` (§5) and the parser fields in `parser_info`;
   - else HTML: `_extract_html`.
3. Other web adapters (adapters with no domain list, such as `discourse`).
4. `_fetch_scrapling`: Scrapling `Fetcher` with stealth headers, no browser.
5. `_fetch_cdp`: `render_html_cdp` with the system Chromium (§10.3). A media URL does not go to this step.
6. VLM step: when no text step is usable, the page has content images, and `enable_vlm_transcription` is on, `get_vision_provider(...).transcribe_images` adds the image text. `parser_info` records `vlm_transcribed`, `vlm_images_count`, `vlm_model`, `vlm_language`.

After a usable step, `fetch_web` decides `doc_type` (`odt`, `pdf`, or `web`), cuts the text to the budget (§7.1), and sets `meta` keys: `links` (at most 50), `link_anchors`, `fetch_via`, `effective_url`, `images`, `raw_truncated`, `appendix_truncated`, `references_truncated`, `orig_chars`, `raw_chars`, plus `parser_info`. The canonical URL comes from the URL after redirects. [code]

`_extract_html` removes `script`, `style`, `nav`, `header`, `footer` and some app-banner classes, collects at most 12 content images (`_collect_images`, no images inside tables), converts tables (§8.1), and returns the body text. [code]

### 3.1 Content guard

`validate_web_content(title, text)` in `src/claire/ingest/fetchers/guard.py` returns `(ok, reason)`. It rejects, by length-limited pattern checks: browser TLS error pages, bot challenges, short access-denied pages, short not-found pages, short login or paywall pages, text with less than 30% alphanumeric characters (from 300 characters), and short UI-shell or hashtag noise with low lexical diversity. The reason strings start with `blocked:` or `low_quality:`. [code]

## 4. Private network guard

`src/claire/ingest/fetchers/http.py`: [code]

- `check_ip_safety(ip_str, *, allow_private, allowlist, hostname) -> (is_safe, reason)`:
  1. `STRICTLY_PROHIBITED_NETWORKS` are always refused: `169.254.0.0/16` (cloud metadata), `224.0.0.0/4`, `0.0.0.0/8`, `255.255.255.255/32`, `fe80::/10`, `ff00::/8`, `::/128`.
  2. An address or host name that matches the allowlist is accepted. An allowlist entry is a CIDR or IP, a host name, or a `*.domain` pattern.
  3. Private addresses (`PRIVATE_NETWORKS`: RFC 1918, `127.0.0.0/8`, `100.64.0.0/10`, `fc00::/7`, `::1/128`, and any address that Python marks private or loopback) are accepted only when `allow_private` is true.
  4. Other addresses are accepted.
- `check_url_safety(url, *, allow_private, allowlist)` accepts only `http` and `https`, resolves the host with `socket.getaddrinfo`, and checks every returned address with `check_ip_safety`. A refused address raises `FetchError` (`SSRF policy violation: …`).
- `guarded_client(**kwargs)` returns an `httpx.Client` with a `request` event hook that calls `check_url_safety` before each request. httpx calls this hook for each redirect hop too, so a redirect to a refused address stops before the request is sent. The policy (`allow_private_networks`, `private_network_allowlist`, comma-separated) comes from the fetch settings unless the caller gives it.
- `SafeHttpClient(timeout=30.0, max_size=10 MiB, ...)` uses the same policy. `get(url)` checks the URL, then sends the request through `guarded_client` with `follow_redirects=True`, raises `MediaResponseDetected` for media responses, and raises `FetchError` when `Content-Length` is larger than `max_size`.
- Settings: `CLAIRE_ALLOW_PRIVATE_NETWORKS` (default off, boolean or 0/1) and `CLAIRE_PRIVATE_NETWORK_ALLOWLIST` (default empty) in `src/claire/core/config.py`.

Users of the guard: `_fetch_static` in `web.py`; the project plugins through `ctx.http_client()` and `ctx.safe_get()`, which add the host list of the plugin (§2.2); the YouTube page requests, the share link resolver (project plugin `claire.share_link`), the page image download (`store/raw.py` `download_images`), and the Gemini VLM image download (through `guarded_client`); the feed fetcher in `src/claire/feeds/fetch.py` and the media proxy in `src/claire/federation/media_proxy.py` (through `check_ip_safety` on each hop, with the same two settings). The VMware Explore presentation download checks its allowed host on each redirect hop in addition (§10.2). [code] A new fetcher that sends HTTP requests uses `guarded_client`, `SafeHttpClient`, or `check_ip_safety` on each hop.

Tests: `tests/test_http_security.py` (also the redirect hop checks). Tests that replace the network use the fixture `mock_guarded_client` in `tests/conftest.py`. [code]

## 5. PDF parsing

### 5.1 Parser tiers

`extract_pdf_stream(stream, url, fallback_title, engine)` in `src/claire/ingest/fetchers/pdf.py`; `extract_pdf_bytes` wraps it. [code]

| Tier | Engine | When |
|---|---|---|
| 1 | `extract_pdf_stream_pypdfium2` (PDFium through `pypdfium2`) | `CLAIRE_PDF_PARSER` is `default` or `pypdfium2` |
| fallback | `extract_pdf_stream_pypdf` | tier 1 returns an error that is not `encrypted…`. The result has `parser_fallback=True` and `parser_fallback_reason` |
| explicit | `extract_pdf_stream_pypdf` | `CLAIRE_PDF_PARSER=pypdf`. No fallback |

- There is no layout-analysis or vision tier for PDF text. Docling is removed from the dependencies. [code]
- `ops/cb_manuscript.py` `_check_pdf_parser` stops `cb-manuscript` preflight when `CLAIRE_PDF_PARSER` (or `pdf_parser` in `ingest_limits.json`) is `pypdf` or `docling`. [code]
- Both engines stop at 1,000 pages with text or 10,000,000 characters. Both take the title from PDF metadata, else from the first two text lines, else from `fallback_title`. Both collect at most 50 HTTP links (PDF link annotations and URLs in the text). pypdfium2 reads the link annotations of a page before the text links. `_drop_url_fragments` removes an address that text detection cut at a line wrap: a host without a dot (`https://www`) or the start of a longer address in the list. [code]
- `PdfExtractResult` is a 6-tuple `(title, text, links, anchors, error, images)` with more attributes: `parser_requested`, `parser_used`, `parser_fallback`, `parser_fallback_reason`, `encoding_flaw_detected`, `encoding_flaws`, `is_scanned`, `encoding_flaw_details`, `pdf_metadata`. [code]
- An empty text layer gives the error `empty PDF content`. `fetch_file` and the web chain then fail. There is no OCR. [code]

### 5.2 Encoding flaw detection

`detect_pdf_encoding_flaws(text, page_count, image_count)` runs after each successful extraction. It reports: empty text layer or image-only scan, low text density with images (`scanned_low_density`, less than 25 characters per page), replacement characters, private-use code points, `(cid:N)` tokens, control characters, mojibake, and separated Hangul jamo. The PDF fetchers copy the result to `meta`: `pdf_parser_requested`, `pdf_parser_used`, `pdf_parser_fallback`, `pdf_parser_fallback_reason`, `pdf_encoding_flaw_detected`, `pdf_encoding_flaws`, `pdf_is_scanned`, `pdf_metadata`. [code] The setting `pdf_detect_encoding_flaws` is not read by `pdf.py`; detection always runs. [code]

These keys go to `IngestReport` (CLI and Telegram summary), to the Telegram completion text in `src/claire/telegram_bot.py`, and to the reader badges. [code]

### 5.3 Appendix and references cut

`slice_pdf_text(text, limit, *, strategy, exclude_appendix, exclude_references)` finds the first heading of an appendix (`APPENDIX_PATTERNS`, English and Korean) and of references (`REFERENCES_PATTERNS`), cuts the text at the earlier one, then applies the budget with `slice_document_text`. An appendix match is not a heading when it follows a single line break after a line that ends with a lower-case letter, `,`, `;`, `(`, or a dash (`_continues_sentence`): that line is a wrapped body sentence such as "details are in / Appendix B. We want …". It returns `(text, is_truncated, appendix_truncated, references_truncated, orig_chars, raw_chars)`. `CLAIRE_PDF_EXCLUDE_APPENDIX` and `CLAIRE_PDF_EXCLUDE_REFERENCES` (both on by default) control it. With `full_content`, neither cut is applied. Presentation PDFs never get the cuts (§10.2). [code]

### 5.4 ODT preference and local files

`fetch_file(path, *, full_content)` in `src/claire/ingest/fetchers/textfile.py` reads `.pdf`, `.odt`, `.md`, `.txt`, `.markdown`, `.rst`, files with no suffix, and ODT bytes with another suffix. Other suffixes raise `FetchError("unsupported file type (M1): …")`. When a `.pdf` path has an `.odt` file with the same stem, the ODT is used and `meta.format_preference="odt_over_pdf"`. [code]

## 6. PDF metadata reconciliation and paper classification

`src/claire/extract/classifier.py`: [code]

- `classify_paper(doc, settings, *, provider)` calls `provider.classify_paper(doc, effort=pdf_classifier_effort)`. The provider is the job provider, or `get_lowest_effort_provider(settings)`, which returns `get_provider(settings, feature="classify")`: the configured provider for the `classify` feature. It does not compare providers by effort and does not fall back to another provider. `parse_effort_score` still exists and maps effort names and token numbers to scores.
- `reconcile_pdf_metadata(doc, settings, *, provider)`:
  1. classifies the document and gets `author` and `title` from the provider answer;
  2. uses `extract_author_heuristic` and, when `is_dtp_title(doc.title)`, `extract_title_heuristic` on the first page text when the provider gives none;
  3. replaces `doc.author` with the found author (`meta.author_reconciled`, `meta.meta_author_raw`), or removes a typesetter account name found by `is_dtp_typesetter_artifact` (`meta.author_rejected="dtp_typesetter_artifact"`);
  4. replaces a typesetting file name title (`meta.title_reconciled`, `meta.meta_title_raw`);
  5. writes `meta.paper_classification` (`is_paper`, `reason`, `extracted_author`, `extracted_title`, `raw_chars`).
- A provider policy error in this step does not stop `pipeline.ingest`. The extraction step reports the same error. [code]
- `doc_to_prompt` does not add an author line to the prompt. Title, URL, and source type are the prompt header. [code]

## 7. Text budgets and reasoning effort

### 7.1 Stored text budget (fetch time)

| Source | Budget for `raw_text` | Cut function |
|---|---|---|
| web page, x.com, YouTube | `raw_char_budget` (20,000) | `slice_document_text` |
| PDF, ODT | `pdf_max_extract_chars` (50,000) | `slice_pdf_text` (PDF), `slice_document_text` (ODT) |
| generic video, Naver Clip | `video_max_extract_chars` (200,000) | `slice_document_text` |
| presentation PDF in a bundle | `pdf_max_extract_chars`, no appendix or references cut | `slice_pdf_text` |
| free text (`fetch_text`) | no cut | — |

With `full_content=True`, the budget is 0 (no cut) in `fetch_web`, `fetch_file`, `fetch_video`, and the presentation extract. [code] The values above are the defaults. `data/ingest_limits.json` holds the current values and has priority over environment variables ([`ENVIRONMENT_VARIABLES.md`](ENVIRONMENT_VARIABLES.md)). [code]

### 7.2 Prompt budget (extraction time)

`doc_to_prompt(doc, *, full_content=False)` in `src/claire/extract/prompts.py` selects the limit: [code]

1. full content (`full_content` argument or `meta.full_content`): `max(100000, pdf_max_extract_chars * 2)`;
2. merged document (`meta.extra_sources` present): `effective_merged_extract_char_budget` (`merged_extract_char_budget`, or `extract_char_budget * 2` when it is 0);
3. `pdf` or `odt`: `pdf_max_extract_chars`;
4. else `extract_char_budget` (20,000).

When `meta.content_components` has two or more parts, `_slice_component_content` first gives each part a reserve of `limit // max(3, number of parts)`, gives the rest to the longer parts, and keeps the original order. Else `slice_text` cuts the whole text. [code]

### 7.3 Full content and effort at first ingestion

- CLI: `claire ingest --full` (aliases `--full-content`, `--no-truncate`) and `--effort/-e {none,minimal,low,medium,high,max}`. [code]
- Telegram: `parse_regenerate_flags` reads `--full`, `--full-content`, `--refetch-full`, `--no-truncate`, `-R` and `--effort/--reasoning/-e <level>` in new messages, `/ingest`, and file captions, and passes them to `IngestService.ingest` or `ingest_composite`. [code]
- HTTP API: `POST /ingest` and the stream route read `full_content` (or `no_truncate`) and `effort` from the JSON body. [code]
- `pipeline.ingest` passes `full_content` to the fetch function and sets `meta.full_content=True` and `meta.applied_effort`. [code]
- The detail prompt tells the model not to write sections whose text the cut removed (rule 13 of the detail prompt in `prompts.py`). [code]

### 7.4 Effort selection

In `extract_resolve_store` and `ensure_document_detail` (`src/claire/ingest/pipeline.py`): [code]

1. An explicit effort wins.
2. For `pdf` and `odt`: if `paper_classification.is_paper` is true and `raw_text` has at least `pdf_paper_threshold_chars` (15,000) characters, the effort is `pdf_paper_effort` (`high`). Else it is `pdf_default_effort`, or the provider effort when that is empty.
3. Other sources use the provider effort.

The selected value goes to `meta.applied_effort` and `IngestReport.effort`. [code]

## 8. Tables

### 8.1 Capture

`_format_html_tables_to_adoc` in `web.py` replaces each HTML `<table>` with an AsciiDoc table (`adoc_table`, `adoc_cell` in `src/claire/ingest/fetchers/adoc_text.py`). It removes media elements in cells (`img`, `svg`, `video`, `audio`, `iframe`, `canvas`, `picture`) and keeps their tail text. It keeps the `<caption>`. A table with no data rows is removed. [code] Claire writes no Markdown tables ([decision], RD24).

### 8.2 Budget exemption

`src/claire/extract/table_budget.py`: [code]

- `split_text_segments` finds AsciiDoc (`|===`), Markdown pipe, and raw HTML tables and returns prose and table segments in their original order.
- `slice_text_with_table_exemption_info(text, limit)` counts only prose against `limit`. It keeps every table in full, also after the prose budget is used.
- `slice_document_text(text, limit, strategy)`: `table-exemption` (default) uses the exemption; `strict` cuts at `limit`. `CLAIRE_SLICING_STRATEGY` selects the strategy.
- `has_tables` and `count_tables` serve the table selection below.

### 8.3 Prompts and reprocessing

- The extraction prompt has a `TABLES & DATA MATRICES` rule. The detail prompts keep tables and allow the removal of media in tables. [code]
- `documents_with_tables` in `src/claire/store/db.py` selects documents with tables in `raw_text` or `detail`. `claire reextract --tables`, `claire backfill-detail --tables`, `claire regenerate --tables`, and `claire reprocess --scope tables` use it ([`COMMANDS.md`](COMMANDS.md)). [code]

## 9. Video: publisher captions first, STT second

### 9.1 `fetch_video`

`fetch_video(url, *, full_content, preferred_languages, settings)` in `src/claire/ingest/fetchers/video.py`: [code]

1. `fetch_video` is the media service of the core; plugins call it as `claire.plugin_sdk.fetch_media`. A plugin can give `media_url` (the URL that yt-dlp reads); the document keeps `url`.
2. `resolve_video_target_url` finds a Brightcove embed, an HTML5 `<video>` source, or `og:video` in given HTML.
3. `yt-dlp` reads the metadata (`skip_download`) with `CLAIRE_YTDLP_EXTRACTOR_ARGS`. `acquire_caption` (§9.2) gets a publisher caption.
4. If no valid caption exists and `enable_video_transcription` is on: a cached media file (§9.4) or a `yt-dlp` audio download goes to the STT provider (`get_transcript_provider`) with `stt_language`. `ffmpeg` must exist (`find_ffmpeg_executable`), else `stt_error` is set.
5. The text has these blocks: play time, `[영상 챕터]`, `[영상 자막]` or `[영상 음성 전사 (STT)]`, `[영상 설명]`, `[태그]`. With no transcript, the caption block has a notice text.
6. Meta keys include `has_transcript`, `transcript_segments`, `transcript_source` (`manual_caption`, `automatic_caption`, or `stt`), `caption_status`, `caption_language`, `caption_format`, `caption_content_hash`, `caption_error`, `is_stt`, `stt_error`, `stt_truncated` (budget cut, or more than 120 seconds between the last segment and the end), `video_cached`, `video_cache_used`.

`is_video_or_audio_url` sends Brightcove, Vimeo, TikTok, Chzzk, Instagram reels, media file suffixes, and query values with media suffixes to the `video` fetcher. A local file path with a media suffix also matches; there is no separate local media fetcher. [code] Whether `yt-dlp` reads such a local path is [unverified].

YouTube has its own fetcher in the project plugin `claire.youtube` (`fetch_youtube`): `youtube-transcript-api` first, then `yt-dlp` captions, then STT. [code]

### 9.2 Captions

`src/claire/ingest/fetchers/captions.py`: [code]

- `select_caption_candidates` normalizes language tags, matches the preferred languages exactly first and then by primary subtag, prefers manual captions to automatic captions, and prefers inline data, then HTTPS VTT, then HTTP VTT, then other tracks.
- `_read_caption` reads inline data or downloads the track with the `yt-dlp` session. `MAX_CAPTION_BYTES` is 8 MiB.
- `is_valid_speech_vtt` needs a `WEBVTT` header, timed cues, and text, and refuses thumbnail sprite tracks (`xywh`).
- If a preferred caption is advertised but every download fails, `acquire_caption` returns `download_failed` and `fetch_video` raises `FetchError`. Caption URLs are not stored in meta or errors.

### 9.3 STT providers

`get_transcript_provider` in `src/claire/extract/transcript/factory.py` returns `GeminiTranscriptProvider`, `MockTranscriptProvider`, or `UnavailableTranscriptProvider` (raises when the configured provider cannot run). A member context never falls back to the mock. Details: [`PROVIDERS.md`](PROVIDERS.md). [code]

`GeminiTranscriptProvider` in `src/claire/extract/transcript/gemini_stt.py`: [code]

- Model: `stt_model`, else `gemini-3.5-transcribe`.
- Audio chunks of `min(video_chunk_duration_sec, 240)` seconds; at least 62 seconds between chunk calls; on HTTP 429 it reads `retryDelay` and tries again a limited number of times.
- `stt_custom_vocabulary`, else `DEFAULT_CUSTOM_VOCABULARY`, goes into the prompt.
- Timestamps are rebased to the full audio.

### 9.4 Media cache

`src/claire/store/video_cache.py` keeps a downloaded media file in `data/cache/video/` when STT fails after the download, for `video_cache_ttl_sec` (default 259,200 s, three days). The next fetch of the same URL uses the cached file. A file smaller than 1 KiB is deleted. The cached file is deleted after a successful transcript or a failed STT on the cached file. `claire video-reprocess` uses the same path. [code]

## 10. VMware Explore presentation bundles

The project plugin `claire.vmware_explore` (`plugins/ingest_adapter/claire.vmware_explore/adapter.py`: discovery and download) and the core format service `src/claire/ingest/fetchers/presentation.py` (`extract_presentation`, `compose_video_presentations`). [code]

### 10.1 Discovery

- `vmware_explore_video_id(url)` accepts only `https://www.vmware.com/explore/video/<digits>` (also without `www`).
- `discover_presentations(url)` reads the static HTML first. `select_presentation_candidates` takes only HTTPS links inside a `.presentation-details` element that has a heading `Presentation PDF`, at most three (`_MAX_PRESENTATIONS`).
- With no static candidate, it calls `render_html_cdp(url, click_tab_label="Presentation")`. Empty HTML gives `discovery_failed` / `render_failed`. With no candidate, `rendered_session_is_ready` decides between `absent` (the session detail rendered: the video ID, at least 100 characters, a heading, and two of `Details`, `Speakers`, `Share`) and `discovery_failed` / `session_not_ready`.
- `fetch_vmware_explore` raises `FetchError("presentation_pdf.discovery_failed: …")` for `discovery_failed`, before the media part. It downloads and extracts each candidate, then calls `fetch_media(url, media_url=<Brightcove player URL>)`, then `compose_video_presentations`. For `absent` it sets `meta.presentation_pdf={"status": "absent"}`.

### 10.2 Download and extraction

`download_presentation(candidate, settings)`: [code]

- HTTPS only, no user information in the URL, host in `_ALLOWED_PRESENTATION_HOSTS` (`static.rainfocus.com`).
- The network client of the plugin (`ctx.http_client()`) applies the private network guard and the host list of the plugin to each request; redirects are followed by hand, at most five, and each hop must pass the host rule again.
- The media type must be a PDF type, `Content-Length` and the streamed size must not exceed `presentation_pdf_max_bytes` (64 MiB), and the body must start with `%PDF-`.
- The stored public URL has no query and no fragment (`_redact_url`).
- Errors are `PresentationFetchError` with the status `invalid`, `download_failed`, `extract_failed`, or `bundle_incomplete_media`.

`extract_presentation(attachment, settings, *, full_content)` uses `extract_pdf_bytes` with `pdf_parser` and `slice_pdf_text` without the appendix and references cut. [code]

### 10.3 Rendering with active waiting

`render_html_cdp(url, *, wait_seconds=2.5, click_tab_label=None, interaction_timeout_seconds=12.0, post_click_wait_seconds=1.5)` in `web.py` uses Scrapling `StealthyFetcher` with the system Chromium (`_system_chromium_executable`). With `click_tab_label`, after the first wait it looks for the `role=tab` element with that exact name. If the tab is not there yet, it waits for `state="attached"` for at most 4 seconds, then clicks it and waits `post_click_wait_seconds`. If the tab does not appear, it returns the DOM without a click, so that the caller can decide `absent`. A failed click or a render error returns an empty string. [code]

### 10.4 Composition and storage

- `compose_video_presentations(video_doc, presentations)` needs `has_transcript`; else it raises `bundle_incomplete_media`. It inserts one block per PDF after the transcript block: `---`, `[발표자료 PDF — <session code>]`, `출처: <public URL>`, text. [code]
- Meta: `presentation_pdf` (first item), `presentation_pdfs`, `content_components` (`transcript` and `presentation_pdf` spans with hashes and character counts), `extra_sources` (the PDF as `source_type="pdf"`), combined `orig_chars` and `raw_chars`. The binaries go to `Document.attachments` as `SourceAttachment` (`src/claire/ontology/base.py`), with `required=True`. [code]
- `pipeline.ingest` stores required attachments before the document write (`_store_required_doc_attachments` → `save_document_attachments` in `src/claire/store/raw.py`) at `data/raw/attachments/<document_id>/<kind>/<sha256>.pdf`. A failed store stops the ingest with `attachment_store_failed`. [code]
- `preserve_presentation_history` adds replaced PDF versions to `meta.presentation_history`. `_link_existing_presentation_documents` records an existing standalone PDF document in `presentation_document_ids` and does not delete or absorb it. [code]

## 11. Composite ingestion: Telegram file plus link

### 11.1 Caption parsing

`on_document` in `src/claire/telegram_bot.py` takes documents, native videos, and audio (`filters.Document.ALL | filters.VIDEO | filters.AUDIO`). `parse_caption_composite(caption, theme_mgr)` removes the theme tag (`parse_message_theme`) and the flags (`parse_regenerate_flags`), then takes the first URL (`router._URL_RE`) as the composite URL. The bot ignores the text before and after the URL, because the ingest focus is sealed ([`DATA_LIFECYCLE.md`](DATA_LIFECYCLE.md) §4). With no URL, the file is a single ingest. [code]

### 11.2 Service and composer

- `IngestService.ingest_composite(file_path, file_name, url, …)` writes a `raw_inbox` row with `kind="composite"`, `file_ref`, `file_name`, and a JSON payload (`url`, `attachment_name`, `effort`, `full_content`, `kb_id`). The replay of failed rows reads this payload. [code]
- `fetch_composite_components` in `src/claire/ingest/composite.py` reads the file with `fetch_file` and the URL with `router.fetch`. A missing file, a zero-byte file, an empty file text, a URL fetch error, or an empty URL text raises `FetchError`. Then the row gets `status="error"` and no document is written. [code]
- `compose_composite_document(file_doc, url_doc, file_name, …)` always uses the URL document as the primary document. [code]
  - Video or YouTube URL plus PDF: the PDF block follows the transcript; meta gets `presentation_pdf`, `presentation_pdfs`, and `content_components`.
  - Other URL plus PDF: the PDF block and `presentation_pdf` meta, no `content_components`.
  - Other file: an `[첨부 보조 자료 — <name>]` block.
  - Always: `extra_sources` (primary and file), `composite_ingest=True`, `composite_kind="attachment_with_link"`, combined counts and hash.
  - The PDF binary goes to `attachments` with `required=False`, so the pre-write store (§10.4) does not save it; the inbound copy from `save_inbound_file` stays. [code]
- The composed document goes to `IngestService.ingest(..., prefetched=composite_doc)`: one extraction for both sources. [code]
- A media file is not read by `fetch_file`, so an attached MP4 or audio file with a link in the caption fails in `fetch_composite_components`. [code]

## 12. Validation before extraction

`validate_extractable_content(doc)` in `pipeline.py`: [code]

- empty `raw_text` fails;
- a `video` document with `has_transcript=False` and no presentation fails when the text without the notice, play time, and tag blocks is shorter than 250 characters (`video_transcript_missing`);
- `web` and `custom` documents must pass `validate_web_content`.

The failure text is `insufficient_content: …`. [code]

## 13. Tests

`tests/test_router.py`, `tests/test_registry.py`, `tests/test_fetcher_conformance.py`, `tests/test_http_security.py`, `tests/test_pdf_pypdfium2.py`, `tests/test_pdf_encoding_flaws.py`, `tests/test_pdf_appendix.py`, `tests/test_pdf_references_and_biblio.py`, `tests/test_pdf_author_reconciliation.py`, `tests/test_pdf_budget_and_effort.py`, `tests/test_initial_full_ingest_and_effort.py`, `tests/test_table_ingest_budget.py`, `tests/test_table_regenerate.py`, `tests/test_video_captions.py`, `tests/test_gemini_stt.py`, `tests/test_video_cache.py`, `tests/test_video_presentation.py`, `tests/test_composite_ingest.py`. These test files exist at the basis commit; this document did not run them. [unverified]
