# Laboratory: measurements and shadow evaluations (`LABORATORY_DESIGN`)

- **Status**: Decided (owner, 2026-11-24). Implementation: units LB1–LB4.
- **Owner request (2026-11-24)**: "Make a path `/laboratory/` that manages these measurements and the shadows together, and give it sections."
- **Related**: [`ACCOUNT_PREFERRED_LANGUAGES_DESIGN.md`](ACCOUNT_PREFERRED_LANGUAGES_DESIGN.md) (measurement M0), [`JEV_SHADOW_EVALUATION_DESIGN.md`](JEV_SHADOW_EVALUATION_DESIGN.md), [`EXPANSION.md`](../implementation/EXPANSION.md) §3.1 (same-subject source merge, mode `shadow`), [`PROVIDER_SETTINGS_SIMPLIFICATION_DESIGN.md`](PROVIDER_SETTINGS_SIMPLIFICATION_DESIGN.md) (principle 3: one place for one record).

## 1. Problem

The server has work that records or measures but never changes the knowledge graph. Its controls are in different places: the Jev shadow evaluation in the advanced part of `/admin/providers`, the same-subject source merge mode and its human review on `/admin/limits`, the shadow embedding spaces only in the CLI (`claire re-embed --target-space`). A measurement before a default change (M0: STT language hints) has no place.

## 2. Decisions

| ID | Decision |
| :--- | :--- |
| LB1 | A console area `/laboratory/*` for the server administrator only (`_system("admin")`; other callers get 404), in the console shell, with the sidebar group "Laboratory". `/laboratory` goes to the first section. |
| LB2 | Section `/laboratory/measurements`: runs that compare variants of one AI call on the same input and keep the result (`lab_runs`, schema v47). First kind: `stt_hints` (M0): one video, the audio once, STT without language hints and with the hints of a list; the result shows for each variant the length, the time, the share of each writing system, and the agreement of the two texts. The run uses the server key of Google AI Studio; the audio is at most 300 seconds. |
| LB3 | Section `/laboratory/shadows`: one card for each shadow evaluation. Jev (switch, key, address, the counts of its records) moves here from `/admin/providers`; the same-subject source merge (mode and the human review) moves here from `/admin/limits`; the shadow embedding spaces of the knowledge bases are shown read-only (the actions stay in the CLI). The old places link here. |
| LB4 | A run or a shadow never changes the knowledge graph or a document. A run that sends data to a service outside the server says so on its card. |

## 3. Limits

- A run costs AI calls. The form tells the number of calls before the start; one run at a time.
- The laboratory is not a test bench for members: member data does not go into a run.
