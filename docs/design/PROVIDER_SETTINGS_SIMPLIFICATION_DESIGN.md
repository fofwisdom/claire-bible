# AI provider settings: simple pages that show what applies (`PROVIDER_SETTINGS_SIMPLIFICATION_DESIGN`)

- **Status**: Implemented as option A (2026-11-24). The owner selects among the options of Section 6 after the implementation (owner request: "implement all, then ask for the selection"). Current behavior: [`PROVIDERS.md`](../implementation/PROVIDERS.md) §5A, [`ACCOUNTS.md`](../implementation/ACCOUNTS.md) §5.8.
- **Visual prototype**: [`prototypes/provider_settings_prototype.html`](prototypes/provider_settings_prototype.html) (administrator A and B, account A and B, problem → solution).
- **Related**: [`AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md`](AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md) (`AED4-rev`, `AED6`, `AED8`), [`CONSOLE_LAYOUT_DESIGN.md`](CONSOLE_LAYOUT_DESIGN.md), [`MULTI_PROVIDER_DESIGN.md`](MULTI_PROVIDER_DESIGN.md).

## 1. Problem (owner, 2026-11-24)

1. `/admin/providers` is too complex for a person: too many policy items (nine cards, about 3700 px).
2. The page shows controls as if they work, but some do not work.
3. STT cannot be replaced, but the page offers a provider choice for it.
4. On `/account/providers` a person has many settings but no way to see what applies to the jobs of the account.
5. After a save the message shows far away from the button that the person pressed.

## 2. Findings (code audit of 2026-11-24)

| Item | Finding |
| :--- | :--- |
| Routing rows `stt`, `vlm` | No effect: no code reads `feature_routing.stt` or `.vlm`. The transcript and vision factories select the provider. |
| STT provider select (Gemini or mock) | The only real STT is Google AI Studio with the fixed model `gemini-3.5-transcribe`; "mock" makes fake transcripts, also on a production server. |
| Routing hint "the member defaults" | Wrong: the routing applies only to the server jobs and the administrator jobs; a member key uses its own model or the main model. |
| Server tier card on `/admin/providers` | The same record as `/admin/server`, saved without step-up and admin audit; a stale `/admin/server` tab could overwrite it. |
| Lineage diagram, fixed values table, fallback catalog | Read-only developer information, not settings. Now on `/support/pipeline` (PS8). |
| Save on `/admin/providers` | One save button at the end; the result banner was the first element of the re-rendered page; a failed save threw away the typed values. |
| Main provider `mock` | Selectable on a production server, although the principles allow it in development only. |
| Account page | No view of the source (key, server tier, none), of the model of each feature, or of the limits that apply. Key put, check, and delete were three cards. A model change needed the key again. |

## 3. Principles

1. **What applies comes first.** Each page starts with a read-only view that uses the resolvers of the jobs (`accounts/provider_view.py`): a person reads the AI of each feature and its state before any control.
2. **A control exists only when it changes a job.** A fixed value shows as a fixed value (STT), not as a choice. A value that a job does not read is not on the page.
3. **One place for one record.** The server tier is edited on `/admin/server` only.
4. **The result is next to the button.** Each form saves only its part and shows its result in a `role="status"` element in the row of its buttons. A failure keeps the typed values.
5. **Advanced is closed.** Per-feature models, call interval and retries, Jev, and the development test provider are inside closed `<details>`.

## 4. Decisions implemented (option A)

| ID | Decision |
| :--- | :--- |
| PS1 | `/admin/providers` has five parts: what applies now, the Google AI Studio key, the models (per-feature models closed), STT (switch and language only), advanced (closed). The embedding table of the shared knowledge bases stays below. |
| PS2 | `FEATURE_SLOTS` loses `stt` and `vlm`; a routing or key preference for them is refused as an unknown slot. |
| PS3 | The server keeps `stt.provider = "gemini"` on each STT save; the page shows "Google AI Studio · gemini-3.5-transcribe — no other provider or model". |
| PS4 | `PATCH /providers` ignores `server_tier`. `/admin/server` shows and edits the provider, model, maximum effort, and features of the tier (with its step-up and audit). |
| PS5 | A production server refuses `active_provider = "mock"` (400 `mock_not_allowed_in_production`); the page shows the switch only on a development server. A save of the key on a production server makes Google AI Studio the main provider. |
| PS6 | A save of a part does not stop at a main provider without a key; only a change of the main provider is checked. The status view shows the problem. |
| PS7 | `/account/providers`: the card "What applies to my jobs" (source, model of each feature and where it comes from, STT, VLM, other features, ingest choices, the limits that apply) and one key card (new key, model, save, check again, delete). `PATCH /api/me/provider-keys` changes only the model of a key. The defaults of a key show the main model, not the server routing. |
| PS8 | Owner request 2026-11-24: the lineage diagram goes to `/support/pipeline` for each account, with its contradictions corrected: Jev was drawn inside the main path between the embedding and the judge (it only records); the summary and the detail were drawn as the input of the embedding (the embedding text is the entity frame, and the embedding is made only when gate 1 has no match); the relation judge was grouped with the entity judge (it is the later step across documents); the classification slot was missing; the fallback table read a field `description` that does not exist (`behavior`); the task type was described as a parameter (`gemini-embedding-2` uses a prompt prefix); the recent document list read columns that do not exist. The STT card and the account card show the order of a video: publisher captions, automatic captions, each in the preferred language order, then STT. |

## 5. Modern web guidance applied

- `forms`: the result of an AJAX save goes to `role="status"`; the buttons are disabled only after a valid submit; the focus goes to the field that needs a value.
- `switch`: the switches are `input[type="checkbox"][switch]` with a label.
- `search-hidden-content`: the advanced parts are `<details>` (searchable with "Find in page").

## 6. Selections for the owner

| Question | A (implemented) | B (prototype) |
| :--- | :--- | :--- |
| Q1 Layout of `/admin/providers` | One page, one form per part, the result next to each button | Tabs (status, key and model, STT, advanced) with one sticky save bar and a count of unsaved changes |
| Q2 Layout of `/account/providers` | The view on top, the key card below | Two columns: settings left, a preview "after the save" right |
| Q3 Per-feature models of the server | Kept, closed under "advanced" | Removed (one default model for all features) |
| Q4 Jev shadow evaluation on the page | Kept, closed under "advanced" | Removed from the page (configuration file only) |
