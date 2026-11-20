# Ideas: Presenton Sidecar Replacement for Document Presentations

- **Status**: Ideas only. They have no design for implementation. Do not implement from this document. A design starts only after an owner decision.
- **Date**: 2026-11-20
- **Basis**: commit `49acf60`. Official Presenton image `ghcr.io/presenton/presenton:latest` (`0.9.12-beta`, digest `sha256:dd444e4384df0caeb7cb96a26bfa7543a12d2be19c83d661446b2087d09aed54`, created `2026-10-26T14:59:23Z`). Local technical spike checks PT1 to PT6 with a deterministic recording OpenAI-compatible mock server.
- **Related**: [`LLM_PRESENTATION_AUTHORING_AND_COMPOSITION_DESIGN.md`](../design/LLM_PRESENTATION_AUTHORING_AND_COMPOSITION_DESIGN.md), [`ACCOUNT_IDP_AND_FEDIFY_SIDECAR_INTEGRATION_DESIGN.md`](../design/ACCOUNT_IDP_AND_FEDIFY_SIDECAR_INTEGRATION_DESIGN.md), [`PRESENTATION.md`](../implementation/PRESENTATION.md), [`PROVIDERS.md`](../implementation/PROVIDERS.md), [`DOCUMENT_FORMAT.md`](../implementation/DOCUMENT_FORMAT.md), [`THEMES.md`](../implementation/THEMES.md), [`IMPLEMENTATION_STATUS.md`](../implementation/IMPLEMENTATION_STATUS.md), [`COMMANDS.md`](../implementation/COMMANDS.md), [`PRINCIPLES.md`](../PRINCIPLES.md)

---

## 1. Summary and Scope Boundary

Claire Bible can replace its internal AsciiDoc reveal.js presentation stack with an external open-source slide engine, [`presenton/presenton`](https://github.com/presenton/presenton) (Apache-2.0 license). The goal is to delegate slide authoring prompts, layout selection, and editable `.pptx`/`.pdf` rendering to an upstream project while Claire stays canonical for authentication, knowledge-base isolation, billing/quota enforcement, artifact storage, and viewing.

### Scope Boundary

| Area | Scope | Reason and Notes |
| :--- | :--- | :--- |
| **Document-to-slide output** | **In scope** | Replaces LLM AsciiDoc deck authoring (`compose_presentation`), `asciidoctor-revealjs` compilation, reveal.js viewer/HUD, and the `compose`/`compile`/`PUT .../adoc` contract. |
| **Video and PDF bundle ingestion** | **Out of scope (keep)** | [`VIDEO_PRESENTATION_BUNDLE_INGESTION_DESIGN.md`](../design/VIDEO_PRESENTATION_BUNDLE_INGESTION_DESIGN.md), [`TELEGRAM_ATTACHMENT_LINK_COMPOSITE_INGESTION_DESIGN.md`](../design/TELEGRAM_ATTACHMENT_LINK_COMPOSITE_INGESTION_DESIGN.md), `src/claire/ingest/fetchers/presentation.py`, and `CLAIRE_PRESENTATION_PDF_MAX_BYTES` belong to source ingestion, not slide output. |
| **Shared favicon helper** | **Relocate (do not delete)** | `src/claire/presentation/favicon.py` is imported by `src/claire/api/server.py`, `src/claire/api/oauth.py`, `src/claire/api/accounts_routes/common.py`, and `src/claire/graphview.py`. Move it to a shared package such as `src/claire/render/` before removing `src/claire/presentation/`. |

---

## 2. Inventory of Existing Presentation Docs and Code

### 2.1 Existing Documentation

| Document | Current Role | Impact of Replacement |
| :--- | :--- | :--- |
| [`LLM_PRESENTATION_AUTHORING_AND_COMPOSITION_DESIGN.md`](../design/LLM_PRESENTATION_AUTHORING_AND_COMPOSITION_DESIGN.md) | Active design principles (absorbed retired `ASCIIDOCTOR_REVEALJS_PRESENTATION_UIUX_DESIGN.md` and `DATABASE_PRESENTATION_TOGGLE_DESIGN.md` on 2026-11-16) | Replace or retire principles tied to AsciiDoc decks, 2D slides, two-stage compose/compile, and reveal.js speaker view. Keep cost-control rules (no auto-compose on read, stale on body change, per-theme opt-in switch). |
| [`PRESENTATION.md`](../implementation/PRESENTATION.md) | Current implementation reference | Rewrite completely when the new contract is implemented. |
| [`COMMANDS.md`](../implementation/COMMANDS.md) §3.11 | CLI reference (`compose`, `compile`, `show-adoc`, `generate`, `status`, `recompile`, `doctor`) | Replace with sidecar-backed `generate`, `export`, `status`, and `doctor` commands. |
| [`ENVIRONMENT_VARIABLES.md`](../implementation/ENVIRONMENT_VARIABLES.md) | `CLAIRE_PRESENTATION_ENABLED` (`0` default) | Keep `CLAIRE_PRESENTATION_ENABLED`; add sidecar switch/URL variables when designed. |
| [`MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md`](../design/MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md) | `usercontent` CSP/nonce and opaque `sandbox` iframe for reveal.js | Simplifies `/p/presentation` security surface when reveal.js inline execution is removed. |
| [`SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md`](../design/SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md) | Permission rules (`owner` or shared KB `maintain`; `presentation_enabled` toggle is admin-only on shared KBs) | Keep unchanged. |
| [`PROVIDERS.md`](../implementation/PROVIDERS.md) | `tier.features` gate (`presentation`) and BYOK/server-tier ledger | Enforce through the new internal LLM proxy (Option A-2). |
| [`IMPLEMENTATION_STATUS.md`](../implementation/IMPLEMENTATION_STATUS.md) §2, §4.4 | Notes that presentations are globally disabled, E2E tests were removed on 2026-11-13, and `hud.py`/`views.py` violate `G1` line-icon and `i18n` rules | Replacement eliminates `hud.py` and `asciidoctor` views while resolving `G1`/`i18n` violations in the new viewer. |

### 2.2 Measured Code and Container Footprint

- **Code and static assets**: `src/claire/presentation/*.py` and `src/claire/static/css/reveal-claire.css` total `2,308` lines; vendored `src/claire/static/vendor/reveal.js` occupies `1.3 MB`; `compose_presentation()` is implemented across four provider classes plus `src/claire/extract/prompts.py`.
- **Container dependency (`Dockerfile`)**: `ruby`, `asciidoctor ~> 2.0.26`, and `asciidoctor-revealjs ~> 5.2.0` are used **only** for presentation compilation. Standard document body rendering uses pure-Python `src/claire/render/aot.py` and browser `adoc_parser.js` ([`DOCUMENT_FORMAT.md`](../implementation/DOCUMENT_FORMAT.md) §3). Removing presentations from the core image eliminates the entire Ruby runtime.
- **Shared path collision in current storage**: Current code stores files under `<data_dir>/presentations/<document_id>.{adoc,html}` across all themes in the same folder ([`PRESENTATION.md`](../implementation/PRESENTATION.md) §2). The replacement should namespace outputs by knowledge base (`data/presentations/<kb_id>/<document_id>/`).

---

## 3. Proposed Direction (Owner Concept Decisions)

The table below captures the aligned direction from the concept review prior to formal design.

| Topic | Proposed Decision | Key Rationale |
| :--- | :--- | :--- |
| **Integration depth (Option A)** | Internal-only headless Presenton sidecar under Compose `profiles: ["presentation"]`, activated by `CB_PRESENTATION=1` (same pattern as Fedify `CB_FEDERATION=1`). | No host port exposure, no external reverse-proxy exposure to Presenton's single-user UI/API. If disabled or unreachable, fail closed without fallback (`PRINCIPLES.md` C2). |
| **LLM and secrets (Option A-2)** | Presenton uses `LLM=custom` pointing to a Claire internal OpenAI-compatible proxy (`CUSTOM_LLM_URL`) with a per-job or short-lived internal token. | Keeps zero permanent LLM keys in sidecar env or `/app_data/userConfig.json` (same custody principle as Fedify FD5/FK1/FK2), while enforcing per-account BYOK and server-tier `usage_hooks`. |
| **Input assembly (Option 3)** | Unchanged ingestion. On explicit generate request, Claire deterministically builds input from stored DB fields: `detail` (or `raw_text`) converted via `document_markdown()` / `adoc_to_markdown()`, uploaded via `files`, plus a short `content` brief and fixed `instructions`. | Zero extra LLM calls or latency during ingestion; respects per-request `focus`, `n_slides`, `tone`, and `language`; avoids duplicating Presenton's internal authoring prompts. |
| **Viewer and canonical artifacts** | Retire reveal.js 2D slides and live speaker window. Claire pulls `.pptx`, `.pdf`, and `speaker_note` JSON from Presenton, saves them in `data/presentations/<kb_id>/<doc_id>/`, and renders a lightweight Claire viewer (PDF or page images + speaker notes panel + `.pptx`/`.pdf` download). | Removes 1.3 MB `reveal.js` and complex HUD/CSP injection; provides editable `.pptx` and portable `.pdf`; keeps full ownership of canonical data and document `purge` inside Claire (FK1). |
| **API/CLI contract and purge** | Replace `compose`/`compile`/`PUT .../adoc` directly with the new `generate` + `export` contract. On production update, a one-time idempotent `claire migrate` step purges legacy `presentation_adoc` values and `data/presentations/*.{adoc,html}` files. | Avoids dual-contract maintenance; follows the existing non-destructive/idempotent one-time purge pattern of `_purge_markdown_bodies` (`DOCUMENT_FORMAT.md` §1, `PRINCIPLES.md` A3). |

### 3.1 Rejected Alternatives

| Alternative | Reason for Rejection |
| :--- | :--- |
| **Option B (Expose Presenton Web Editor)** | Presenton OSS lacks per-account isolation, leaks plaintext keys under `DISABLE_AUTH=true`, and runs its own SQLite/auth layer (`FK1`/`FK2` conflict). |
| **Option C (Remove Feature Completely)** | Loses integrated WebUI tab and `/p/presentation?s=` share viewing for curated knowledge bases. |
| **Option A-1 (Dedicated Sidecar Key / Ollama)** | Cannot cleanly enforce per-account BYOK keys or server-tier token ledgers (`usage_hooks`), and stores permanent secrets inside the sidecar. |
| **Ingest-time pre-authoring** | Spends LLM tokens and adds latency on documents and knowledge bases where presentations are never requested; cannot adapt to request-time `focus` or `n_slides`. |
| **Claire-authored `slides_markdown` pre-generation** | Retains Claire slide-authoring prompt maintenance while still paying Presenton's internal LLM calls for layout selection and per-slide JSON slot mapping. |

---

## 4. Technical Spike Measurements (PT1 to PT6)

All checks ran locally against `ghcr.io/presenton/presenton:latest` (`0.9.12-beta`, `2026-10-26`) bound strictly to `127.0.0.1:5055` and wired via `LLM=custom` (`http://host.docker.internal:18089/v1`) to a deterministic recording OpenAI-compatible mock server (`mock_openai_server.py`). Test input was a Korean technical Markdown fixture (`17,704` bytes, `9,059` chars) converted via `claire.render.markdown_export.document_markdown()` from a `17,486`-byte AsciiDoc fixture containing headings, quotes, lists, and tables, with five start/middle/end verification markers.

### 4.1 Summary Matrix

| Check | Result | Key Measured Evidence |
| :--- | :--- | :--- |
| **PT1 (CJK fonts & visual output)** | Partial Pass | `Noto Sans CJK KR` and `Noto Serif CJK KR` are pre-installed (no tofu boxes `▯`). PPTX (`139,440` B, 4 slides + 4 `notesSlides`) and PDF (`156,811` B, 4 pages) keep Korean text intact. Visual review found CSS line-height vertical overlap on wrapped Korean headings in `general`, card divider overlap on 2-line card titles in `modern`, and clean non-overlapping layout in `standard`. |
| **PT2 (Single generate -> PPTX + PDF)** | Pass | `POST /api/v1/ppt/presentation/generate` (`export_as: "pptx"`) completed in `10.127 s` (`6` LLM calls). Follow-up `POST /api/v1/ppt/presentation/{id}/export` (`export_as: "pdf"`) completed in `5.956 s` with **`0` extra LLM calls**. |
| **PT3 (Speaker notes & delete cleanup)** | Caveat | `GET /api/v1/ppt/presentation/{id}` returned `4/4` `speaker_note` fields. `DELETE` returned `204` (`GET` -> `404`), but left files behind in `/app_data/exports/`, `/tmp/presenton/`, and `/app_data/mem0/` (unless `MEM0_ENABLED=false`). |
| **PT4 (Footprint, startup, RAM, security)** | Measured | Uncompressed image size: `5,712,516,773` B (`~5.32 GiB`). Startup to `/openapi.json` `200`: `6.36 s`. Idle RAM: `457.2 MiB`; peak RAM during generate + PDF export: `1,134.59 MiB` (`1.11 GiB`). `DISABLE_IMAGE_GENERATION=true` triggered 0 external image calls. Under `DISABLE_AUTH=true`, `GET /api/v1/admin/provider-settings` returned plaintext `CUSTOM_LLM_API_KEY`. |
| **PT5 (A-2 wire protocol)** | Pass | `2 + n_slides` streaming OpenAI `POST /v1/chat/completions` (`stream: true`) calls per generation. User `messages[].content` uses multipart list format (`[{"type": "text", "text": "..."}]`). Every call uses `response_format.json_schema` (`strict: false` on outline/layout, `strict: true` on per-slide), even when `TOOL_CALLS=true`. |
| **PT6 (`files` upload vs inline `content`)** | `files` Better | Both modes passed all 5 start/middle/end markers to Stage 1 (`req_001`: `14,552` vs `14,455` chars). In Stage 2 (`req_002` layout selection), `files` sent `5,074` chars (outlines only), whereas inline `content` re-sent the full document (`14,032` chars). |

### 4.2 Detailed Spike Findings

1. **Image size and memory (`PT4`)**:
   - The uncompressed image is `5.71 GB` (`5,712,516,773` B), notably larger than community estimates (`~3.9 GB`). Keeping Presenton in an optional `CB_PRESENTATION=1` Compose profile protects Track 1 minimal deployments.
   - By default (`MEM0_ENABLED=true`), Presenton initializes FastEmbed (`BAAI/bge-small-en-v1.5`) and local Qdrant, pushing FastAPI RSS to `776–819 MiB`. Setting **`MEM0_ENABLED=false`** prevented `/app_data/mem0` initialization and kept FastAPI RSS at `476 MiB` before Chromium export.
2. **Korean typography and template behavior (`PT1`)**:
   - Because `Noto Sans CJK KR` and `Noto Serif CJK KR` are already bundled, a derivative Dockerfile is not needed for glyph coverage.
   - Built-in templates (`16` listed via `GET /api/v1/ppt/template/all`) use Latin CSS metrics:
     - `general`: two-line wrapped Korean headings overlap vertically due to tight `line-height`.
     - `modern`: main headings render cleanly, but wrapped two-line card titles overlap the horizontal divider bar inside small cards.
     - `standard`: renders cleanly without vertical overlap (mid-word Hangul wrapping still appears because `word-break: keep-all` is absent).
   - **Export timeout anomaly**: An isolated `pptx` export on `modern` (`n_slides=2`, `MEM0_ENABLED=false`) timed out at `120.0 s` (`httpx.ReadTimeout`) while `node /app/presentation-export/runner.mjs` (Chromium) ran and subsequently exited with `returncode=1`. Earlier `modern` `pdf` and `general`/`standard` exports succeeded cleanly.
3. **Sidecar disk leaks and data persistence (`PT3`)**:
   - `DELETE /api/v1/ppt/presentation/{id}` deletes SQLite rows (`presentations`, `slides`), but leaves three artifact classes on disk:
     1. `/app_data/exports/pptx/*.pptx` and `/app_data/exports/pdf/*.pdf`.
     2. `/tmp/presenton/<uuid>/*` files uploaded via `POST /api/v1/ppt/files/upload`.
     3. `/app_data/mem0/` (`history.db` retained `24` rows of raw prompts, instructions, and system prompts, plus Qdrant vectors and `telemetry/oss` directory) when `MEM0_ENABLED=true`.
   - Disabling mem0 (`MEM0_ENABLED=false`) eliminates class 3 completely. Claire must explicitly reclaim classes 1 and 2 so purged Claire documents do not leave fragments inside the sidecar.
4. **Secret exposure under `DISABLE_AUTH=true` (`PT4`)**:
   - Unauthenticated `GET /api/v1/auth/status` returned `{"configured":true,"authenticated":true,"username":"electron","user_id":null,"role":"admin"}`.
   - Unauthenticated `GET /api/v1/admin/provider-settings` returned `200` with plaintext `CUSTOM_LLM_API_KEY`.
   - `/app_data/userConfig.json` (`0600 root:root`) stored plaintext `CUSTOM_LLM_API_KEY` and `AUTH_SECRET_KEY`.
   - Even with `CAN_CHANGE_KEYS=false` and `DISABLE_IMAGE_GENERATION=true`, keeping the sidecar off host ports and putting zero real API keys inside it (Option A-2) is mandatory.
5. **Exact OpenAI proxy contract (`PT5`)**:
   - Presenton `0.9.12-beta` dispatches `2 + n_slides` streaming OpenAI `POST /v1/chat/completions` (`stream: true`) requests per deck:
     - Call 1 (Outline generation): `response_format.json_schema` (`strict: false`, `PresentationOutlineModelWithNSlides`), receives `Content`, `Instructions`, and uploaded `Context`.
     - Call 2 (Layout selection): `response_format.json_schema` (`strict: false`, `PresentationStructureModelWithNSlides`), picks `n_slides` template layout indices.
     - Calls `3..2+n_slides` (Slide content, concurrent): `response_format.json_schema` (`strict: true`, `additionalProperties: false`, layout-specific schema with `__speaker_note__`).
   - All `user` messages pass `content` as a list (`[{"type": "text", "text": "..."}]`), not a string.
   - `TOOL_CALLS=true` did not switch requests to tool calling (`has_tools: false` on all 4 calls; `TOOL_CALLS` is absent from `/app/servers/fastapi` sources in this build). Claire's A-2 proxy therefore only needs OpenAI `response_format.json_schema` + SSE streaming translation, not function-calling synthesis.
6. **Input path comparison (`PT6`)**:
   - Uploading `.md` via `POST /api/v1/ppt/files/upload` (`files` multipart field) and sending a compact `content` brief reduces Stage 2 (`req_002`) input size from `14,032` chars to `5,074` chars (~`9,000` fewer prompt characters per generation for a `17.7 KB` document) while delivering identical full-document coverage in Stage 1 (`req_001`).

---

## 5. Constraints and Architectural Implications

1. **Mandatory sidecar environment baseline**:
   - `LLM=custom`, `CUSTOM_LLM_URL=<internal Claire proxy>`, `CUSTOM_LLM_API_KEY=<ephemeral or per-job internal token>`, `CUSTOM_MODEL=<proxy model id>`, `DISABLE_IMAGE_GENERATION=true`, `CAN_CHANGE_KEYS=false`, `MEM0_ENABLED=false`.
2. **Stateless sidecar storage and reclamation**:
   - Claire must download `.pptx`, `.pdf`, and `GET /api/v1/ppt/presentation/{id}` (`speaker_note`) into `data/presentations/<kb_id>/<doc_id>/`, then call `DELETE /api/v1/ppt/presentation/{id}`, and clear stale files under `/app_data/exports/` and `/tmp/presenton/` (for example via an ephemeral volume/tmpfs or post-job cleanup when no job is active). Exclude sidecar `/app_data` from Claire backups and support bundles. Note that the official container currently runs its entrypoint processes as `root`.
3. **A-2 proxy capability boundary**:
   - Converting OpenAI `response_format.json_schema` (`strict: false`/`true`) and `stream: true` SSE chunks fits `api-key` providers (`GeminiProvider` via `google-genai` `response_json_schema`).
   - Host CLI providers (`antigravity`, `codex`) cannot enforce arbitrary per-slide strict JSON schemas or native SSE token streaming without fragile wrapping, so policy should explicitly decide whether A-2 supports `api-key` only (failing closed with `ProviderNotSupported` for CLI providers) or adapts CLI output.
4. **Viewer rendering dependency note**:
   - Claire's Python environment includes `pypdfium2` and `pypdf` (`pyproject.toml`), without `Pillow`. Server-side PNG page rendering can either encode RGB buffers directly with stdlib `zlib`/`struct` (verified in spike) or serve the `.pdf` directly to browser PDF rendering / `pdf.js` alongside Claire's speaker-note panel and `.pptx` download button.
5. **Version pinning**:
   - Presenton is currently `0.9.12-beta`. Any Compose integration must pin an immutable image digest (`@sha256:...`) and verify `PT1`–`PT6` before bumping tags.

---

## 6. Open Decisions Before Entering `docs/design/`

1. **Default template and Korean typography policy**: Use `standard` (verified overlap-free in `PT1`) as Claire's default, restrict the template allowlist to verified layouts, or maintain one Claire custom React/Tailwind template (`word-break: keep-all`, tuned `line-height`) mounted into Presenton?
2. **Provider support in A-2 proxy**: Limit presentation generation to `api-key` providers (`GeminiProvider` / OpenAI-compatible) with explicit `ProviderUnavailableError` on CLI providers (`antigravity`, `codex`), or allow non-strict best-effort JSON parsing for administrator CLI?
3. **Claire viewer design**: Render slides in the WebUI and `/p/presentation?s=` via embedded `.pdf` vs server-cached PNG slide images (`pypdfium2` + stdlib PNG) paired with the `speaker_note` drawer and `.pptx`/`.pdf` downloads?
4. **Sidecar scratch file cleanup**: Restart-ephemeral volume / `tmpfs` for exports & `/tmp/presenton`, or shared bind mount swept by Claire after artifact retrieval?
5. **Live Gemini qualitative check (`PT7`) and `modern` `.pptx` timeout triage**: Run one live Gemini generation against `standard` and `general` before finalizing `docs/design/` to verify real Korean slide compression, `speaker_note` quality, and `.pptx`/`.pdf` export reliability across `n_slides` budgets (`4` to `12`).

---

## 7. Limits of the Measurements

- **Mock LLM**: `PT1`–`PT6` used a schema-driven mock server rather than a live Gemini model, so measured latency (`10.127 s`) excludes real LLM inference time and does not evaluate semantic slide summarization quality.
- **Template coverage**: Visual PDF inspection covered `3` of `16` built-in templates (`general`, `standard`, `modern`) at `n_slides=2..4`.
- **Single-case timeout**: The `modern` `.pptx` `120.0 s` timeout occurred in one `n_slides=2` run with `MEM0_ENABLED=false` and `TOOL_CALLS=true`; root cause inside `runner.mjs` (such as icon/SVG resolution) was not isolated.
- **Memory ceiling**: Peak RAM (`1,134.59 MiB`) reflects one concurrent `4`-slide job; larger decks (`12+` slides) or concurrent Chromium runs were not benchmarked.
- **Single image digest**: All observations apply to `ghcr.io/presenton/presenton@sha256:dd444e4384df0caeb7cb96a26bfa7543a12d2be19c83d661446b2087d09aed54` (`0.9.12-beta`).
