# Internationalization (i18n) of the Web UI, Telegram Bot, and CLI

- **Status**: Implemented (units IP1-1 to IP3-3, 2026-11-11 to 2026-11-16). Design principles only. Current behavior and the texts that are not localized now: [`I18N.md`](../implementation/I18N.md); account and route details: [`ACCOUNTS.md`](../implementation/ACCOUNTS.md).
- **Date**: 2026-11-11 (owner decisions ID1 to ID4; ID5 and ID6 on 2026-11-13) · condensed 2026-11-16
- This document replaces the earlier draft "internationalization and morphological analysis" (2026-09-17). Git history keeps it. Its interface part is replaced by ID4; its search tokenizer part is implemented ([`I18N.md`](../implementation/I18N.md) §7).

## Goal

Each request, Telegram update, and notification gets one interface language by a deterministic rule. All interface text comes from one catalog source for English and Korean. Machine contracts never change with the language.

## Owner Decisions

| ID | Decision | Reason |
| :--- | :--- | :--- |
| ID1 | Localize the web UI, the Telegram bot, and (changed by ID6) not the CLI. The `--json` agent output never changes. | The owner and members read the web UI and the bot; agents parse the CLI. |
| ID2 | Two locales, `ko` and `en`. The final default is `en`. | The users of the project. English is the neutral default for an unknown reader. |
| ID3 | Order: account setting, cookie, `Accept-Language`, server setting, `en`. | An explicit choice of the person wins over the browser, and the browser over the server. |
| ID4 | One set of flat JSON catalogs, read directly by Python and by the browser. No build step, no gettext compilation, no translation service. | One source, no tool chain, no drift between two formats. |
| ID5 | New screens and messages use catalog keys from the start, in both catalogs. Screens that a later unit replaces get their keys in that unit. | No later retrofit; no work on screens that go away. |
| ID6 | The CLI is not localized. Its human output is English in ASD-STE100 for all users. No language option. | Agents and operators read the CLI; one controlled language is easier to read and to test. |

## Principles

- **Explicit values are strict, passive values are tolerant.** A value that a person or a configuration sets must be an exact supported code, else it fails ([`PRINCIPLES.md`](../PRINCIPLES.md) C1). A value from negotiation (header, cookie, Telegram, POSIX) that is not supported is ignored, and the chain continues.
- **Machine contracts do not follow the language.** Error codes, protocol payloads (ActivityPub, OAuth, MCP), agent JSON, and schema field names stay English and stable. Only text for a person changes.
- **The interface language is not the content language.** It does not change what the server fetches, extracts, indexes, or how entity labels are chosen. Changed 2026-11-24 ([`ACCOUNT_PREFERRED_LANGUAGES_DESIGN.md`](ACCOUNT_PREFERRED_LANGUAGES_DESIGN.md)): one preferred-language list of the account gives its interface language (the first supported item) and the content languages of its jobs; it still does not change the extraction output language or the entity labels.
- **The server renders the text.** A page gets its text and an inline catalog in the response, thus the first paint has the right language and no request can fail before the text shows. A page gets only the keys that its scripts can use; text of the administrator area does not go to a member page.
- **Isolated per task.** The active language belongs to one request or one update. It never leaks to a concurrent task.
- **Pages without scripts stay without scripts.** Their language switch is a plain form.
- **The search tokenizer is a separate subject.** The index and the query use the same tokenizer. An optional analyzer must not stop the server when it is absent.

## Rejected Alternatives

| Alternative | Reason |
| :--- | :--- |
| GNU gettext catalogs with a JSON projection for the browser, and a translation service with CI synchronization (earlier draft) | ID4: two formats and a tool chain for two languages. |
| A CLI language option and a CLI locale chain | ID6. |
| A `ja` locale in the first version (earlier draft) | ID2. |
| Translation of error codes or protocol fields | Clients and agents parse them. |

## Limits for Later Changes

- A new locale needs a full catalog with the same keys and placeholders; the parity test enforces it.
- New interface text never goes into code as a literal; it goes into both catalogs.
- New CLI text is English in ASD-STE100, also text that a shared function prints for the CLI.
