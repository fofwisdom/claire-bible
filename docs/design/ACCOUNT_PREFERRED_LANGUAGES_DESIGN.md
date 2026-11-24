# Preferred languages of each account (`ACCOUNT_PREFERRED_LANGUAGES_DESIGN`)

- **Status**: Implemented (owner decisions of Section 8, 2026-11-24). Current behavior: [`ACCOUNTS.md`](../implementation/ACCOUNTS.md) (the item about the preferred languages), [`I18N.md`](../implementation/I18N.md) §2, [`EXTRACTION.md`](../implementation/EXTRACTION.md) §2.3.
- **Owner request (2026-11-24)**: on `/account/profile`, put "preferred languages" in place of "interface language", and let the STT value `auto` use the preferred languages. `CLAIRE_PREFERRED_LANGUAGES` is from the single-user design: move it into the current management or remove it.
- **Changes**: [`PREFERRED_LANGUAGES_DESIGN.md`](PREFERRED_LANGUAGES_DESIGN.md) (the project-wide list of 2026-09-02; Section 5 names the decisions that change) and one principle of [`I18N_DESIGN.md`](I18N_DESIGN.md).
- **Related**: [`PROVIDER_SETTINGS_SIMPLIFICATION_DESIGN.md`](PROVIDER_SETTINGS_SIMPLIFICATION_DESIGN.md) (PS8), [`INGESTION.md`](../implementation/INGESTION.md) §9, [`EXTRACTION.md`](../implementation/EXTRACTION.md) §2.3, [`I18N.md`](../implementation/I18N.md) §2.

## 1. Current state (code, 2026-11-24)

| Item | Now |
| :--- | :--- |
| Interface language of an account | `accounts.locale`: empty (automatic: cookie, `Accept-Language`, `CLAIRE_LANG`, `en`), `en`, or `ko`. Card `#settings-locale` on `/account/profile`. Also used by the Telegram bot, push messages, and the default language of a federation note. |
| Content languages | One list for the whole server: `CLAIRE_PREFERRED_LANGUAGES` (default `ko`); `en` is added at the end without a message (`effective_preferred_languages`). It selects the captions of a video, the first item is the VLM language, and plugins read it (`claire.youtube`, `claire.naver_clip`, `claire.hoyolab`, `claire.uttu`). No screen shows or changes it. |
| STT language | One server value (`providers.json` `stt.language`, default `ko`). `ko` and `auto` send no language code: Google detects the language. Another code is sent alone. |
| Entity language | `accounts.entity_language` (none, `en`, `ko`, `ja`, `zh`): the language of entity labels for the reader. Not in this proposal. |
| Output language of extraction | Korean, from the prompt. Not in this proposal. |

Facts from the Google Gen AI SDK (`TranscriptionConfig.language_codes`): "BCP-47 language codes providing hints about the languages present in the audio. If omitted or empty, defaults to automatic language detection." A request can send more than one code. [code] How strongly a hint changes the result for a language that is not in the hints is not measured. [unverified]

Each ingest job runs with the provider context of the account of the knowledge base (`resolve_service_context`, `settings_for_context`), and the fetchers read the settings of that job. Thus a list of the account can reach the fetchers and the plugins without a change to them. [code]

## 2. Proposal

1. **One ordered list for each account: `accounts.preferred_languages`** (for example `ko, en`). The card "Preferred languages" on `/account/profile` replaces the card "Interface language". The person adds a language, removes it, and moves it up or down.
2. **The interface uses the list.** The interface language is the first item that the interface supports (`en`, `ko`). A list without such an item continues the chain: cookie, `Accept-Language`, `CLAIRE_LANG`, `en`. The same rule applies to the Telegram bot (then Telegram `language_code`) and to push messages. The default language of a new federation note is the first item.
3. **The jobs use the list of their account.** `settings_for_context` puts the list of the account into the job settings. Caption order, VLM language, and the plugins then follow the account. A host operator command without an account uses the list of the server administrator account.
4. **STT `auto` uses the list.** The STT language of the server gets the values `auto` (default) or one code. `auto` sends the list of the account of the job as `language_codes` (hints). One code sends that code for all accounts. The special rule "`ko` means automatic" goes away.
5. **`en` stays visible.** The list always ends with `en` and the card shows it as a fixed last item. Reason: many videos have only English captions, and a caption costs nothing, but STT is a paid call.
6. **`CLAIRE_PREFERRED_LANGUAGES` goes away** after a one-time copy into the list of each account (Section 4). After that no code reads it. A server that still sets it starts normally; `claire preflight` tells that the variable has no effect.

## 3. What does not change

- The output language of extraction (summary, detail, observations) stays Korean.
- The entity language (`accounts.entity_language`) stays a separate setting.
- `CLAIRE_LANG` stays: it is the interface language for visitors without an account.
- The STT model stays `gemini-3.5-transcribe`, and the caption rules of `INGESTION.md` §9.2 stay (publisher captions first, exact tag before the primary subtag, no caption in a language outside the list).

## 4. Migration (schema v46, completes alone)

| Account | List after the migration | Interface |
| :--- | :--- | :--- |
| `locale` = `ko` or `en` | that locale, then the items of `CLAIRE_PREFERRED_LANGUAGES` at the time of the migration, then `en` | No change. |
| `locale` empty (automatic) | the items of `CLAIRE_PREFERRED_LANGUAGES`, then `en` | No change until the person saves the list once: until then the interface keeps the automatic chain and the console shows one notice with a link to the card. |

The jobs of all accounts get the same list as today, thus captions and VLM do not change at the migration. The STT value `ko` of `providers.json` becomes `auto` (it meant automatic detection). `accounts.locale` is removed after the copy. `GET /api/me` gives `preferred_languages`; `PATCH /api/me` takes it, and a `locale` field is ignored (no external API consumers).

## 5. Changes to the earlier decisions

| Earlier decision | With this proposal |
| :--- | :--- |
| `PREFERRED_LANGUAGES_DESIGN` 1: one project-wide list | One list for each account. The reason of decision 1 stays true: all features of a job read one list, the list of its account. |
| `PREFERRED_LANGUAGES_DESIGN` 2: `en` is always the last fallback | Kept, and now visible on the card (P4). |
| `PREFERRED_LANGUAGES_DESIGN` 3: deterministic normalization | Kept (lowercase, no empty items, no duplicates, `en` last). |
| `PREFERRED_LANGUAGES_DESIGN` 4: the first item is the VLM language | Kept, for the list of the account of the job. |
| `PREFERRED_LANGUAGES_DESIGN` 5 and the `I18N_DESIGN` principle "The interface language is not the content language" | Replaced: one list of the account selects the interface language (its first supported item) and the content languages of its jobs. It still does not change the output language of extraction or the entity labels. |

`GLOSSARY.md` gets the term "선호 언어 (Preferred languages)".

## 6. Units (after the selection)

| Unit | Work |
| :--- | :--- |
| PL1 | Schema v46: `accounts.preferred_languages`, migration of Section 4, store, `GET`/`PATCH /api/me`. |
| PL2 | Card "Preferred languages" on `/account/profile` (ordered list, add, remove, up, down; names from `Intl.DisplayNames`); locale chains of web, Telegram, push, and federation notes. |
| PL3 | Job context: `settings_for_context` and `admin_context` put the list into the job settings; remove `CLAIRE_PREFERRED_LANGUAGES` from `Settings`, the docs, and `preflight`. |
| PL4 | STT: values `auto` and one code; `auto` sends the list as hints; `providers.json` migration `ko` → `auto`; text of `/admin/providers`, `/account/providers`, and `/support/pipeline`. |
| PL5 | Unit tests of the interfaces (migration, `/api/me`, job settings, STT request), one E2E spec for the card and the interface language. |

Before PL4 becomes the default, a measurement (M0) can compare the transcripts of three short videos (Korean, English, Japanese) without hints and with the hints `ko, en`. It needs six calls with a Google AI Studio key.

## 7. Selections for the owner

| Question | Recommended | Other |
| :--- | :--- | :--- |
| P1 Interface language | One list; the interface uses its first supported item (Section 2, item 2). | The list and a separate interface selector whose value "automatic" follows the list. |
| P2 STT language of the server | `auto` (the list of the account of the job as hints) or one code. | Remove the server value: always the list of the account. |
| P3 `CLAIRE_PREFERRED_LANGUAGES` | Remove after the one-time copy into the accounts (Section 4). | Move it to `/admin/server` as the first list of a new account and the list of the jobs without an account. |
| P4 `en` at the end | Fixed last item, visible on the card. | A normal item that the person can remove. |

## 8. Owner decisions (2026-11-24)

| Question | Decision |
| :--- | :--- |
| P1 | The server administrator selects the "server default language" (an ordered list) in the server administration WebUI (`/admin/server`). It becomes the first language of the accounts: an account follows the server default language until the person saves an own list, and the interface uses the first supported item of the list that applies. |
| P2 | Recommended option: the STT language of the server is `auto` (the list of the account of the job as hints) or one code. |
| P3 | `CLAIRE_PREFERRED_LANGUAGES` moves into the server default language (a one-time copy at schema v46); no code reads the variable after that. The server default language is also the list of the jobs without an account. |
| P4 | Recommended option: `en` is always in the list, and the card shows it. |
| M0 | Accepted. The measurement and the shadow evaluations get one place: the console area `/laboratory/` ([`LABORATORY_DESIGN.md`](LABORATORY_DESIGN.md)). |

Consequences for the design of Sections 2 and 4:

1. **Two states of an account list.** `accounts.preferred_languages` is NULL (the account follows the server default language; the card shows this state and the list) or an own list. Thus a change of the server default language changes the first language of each account that follows it (P1).
2. **`en` can move.** With P1 the first supported item selects the interface language. A person who wants the English interface puts `en` first; thus `en` is always in the list (P4) but is not fixed at the end. A list without `en` gets it at the end.
3. **Migration.** The server default language is the value of `CLAIRE_PREFERRED_LANGUAGES` (else `ko`), then `en`. An account with `locale` = `ko` or `en` gets an own list (that locale first, then the server default language) only when that list is different from the server default language; all other accounts follow the server default language. An account that had the automatic interface language now gets the first supported item of the server default language (P1). `accounts.locale` is removed.
4. **Visitors without an account.** `CLAIRE_LANG` stays the interface language of a visitor without an account (cookie, `Accept-Language`, `CLAIRE_LANG`, `en`); its name in the documents is "interface language of visitors", not "server default language".
5. **STT.** `providers.json` gets `stt.language = "auto"` in place of the old default `ko` (format version 2); `auto` sends the list of the job as `language_codes`.
