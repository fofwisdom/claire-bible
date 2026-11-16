# One Term for Focus

- **Status**: Implemented. Design principles only. Current behavior: [`DATA_LIFECYCLE.md`](../implementation/DATA_LIFECYCLE.md) §4. Term: [`GLOSSARY.md`](../implementation/GLOSSARY.md).
- **Date**: 2026-09-17 · condensed 2026-11-16

## Goal / problem

A user can give the model a point of view for the extraction and the detail rendering of a document. Three names were used for this one concept: an early legacy name, a transition name, and "focus". All three were in CLI options, API bodies, stored document metadata, pipeline parameters, prompts, the bot, the web UI, and tests. The code had fallback chains that read all three names. The OpenAPI contract and the code did not agree, and people and agents were not sure which name was correct.

## Decisions in force

- **The concept has one name: focus (초점).** The theme default is the default focus. Reason: one name removes doubt in code, contracts, and conversation.
- **No alias and no deprecation period.** The old names were removed from all interfaces at once. Reason: an alias keeps the doubt alive and needs a fallback chain. There are no external API users that need a transition period.
- **Stored legacy data is migrated automatically.** The schema migration copies the legacy metadata key into `focus` when `focus` is empty and then removes the legacy key. It is idempotent. Reason: operators do not manage the database by hand (principle E1), and old documents must keep their focus.
- **The rename is complete only when no residue remains.** The check covers the source tree and the stored metadata. Words with a different meaning in third-party formats (for example web server or CSP directives) are not part of the check.

## Rejected alternatives

- **Keep the old names as accepted input.** Rejected: this was the cause of the problem.
- **A migration that the operator runs by hand.** Rejected: the update must finish in one command.

## Limits for later changes

- A later rename of a domain term follows the same rule: one name, all surfaces at once, automatic migration of stored data.
