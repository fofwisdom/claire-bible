# Per-Account Features: Telegram Bot, Support Bundle, Data Bundle, Backup

- **Status**: Implemented. Design principles only. Current behavior: [`ACCOUNTS.md`](../implementation/ACCOUNTS.md) §14.
- **Date**: 2026-11-10 · condensed 2026-11-16

## Goal

Four features were server features for the administrator only: the Telegram bot, the support bundle, the data (graph) bundle, and the backup. With many accounts on one server, each account owner must configure and control these features for the own account, in the WebUI, without help from the operator.

## Decisions in force

| ID | Decision | Reason |
| :--- | :--- | :--- |
| PA1 | Each account registers its own BotFather token. One runner process runs all bots. The server bot keeps its administrator role (login approval and alerts). | A member must not depend on the administrator bot, and one process is simpler to operate than one process for each bot. This replaces the earlier rule "the Telegram bot is for the administrator only". |
| PA2 | The account owner creates, lists, downloads, and deletes account backups and sets the schedule. Only the operator restores, from the operator console. | A restore changes the shared knowledge DB. It is destructive and can collide with other accounts, so the operator controls it. |
| PA3 | Add an account-scope support bundle. Keep the server bundle of the administrator. | A member needs diagnostics for the own data. Server diagnostics stay with the operator. |
| PA4 | Implement directly, without a separate design approval step. | Owner decision on process. |

## Principles

1. **Account boundary.** Each feature reads and writes only the data of the calling account. An account bot sees only the themes of its account. A bundle or backup of another account is handled as not found.
2. **Secrets stay write-only.** A bot token is stored encrypted and bound to its row. No response shows it again. A bundle or backup never contains passwords, keys, tokens, or the master key.
3. **Least content in diagnostics.** The account support bundle has the state that helps to find a problem, not document bodies and not host or configuration data.
4. **Operator commands stay with the operator.** A bot of a member account refuses the commands and callbacks that issue web sessions, tokens, or approvals, or that show server status.
5. **Allowed users are necessary.** An account bot must have a non-empty list of allowed Telegram users, because the bot writes into the account.
6. **Step-up for changes that give access or destroy data.** Bot register, replace, and delete, a change of allowed users, backup delete, and restore need a recent authentication.
7. **Restore is all or nothing.** A restore first makes a checkpoint. It changes nothing when a theme now belongs to another account or a row collides with data outside the theme. It does not touch themes made after the backup.
8. **Purge removes the features.** An account purge also removes its bot, backup policy, backups, and account bundles.

## Rejected alternatives

- **One process for each account bot**: more processes to supervise, and no shared retry or status logic.
- **Restore by the account owner**: a restore writes into the shared knowledge DB and can break other accounts. The operator must decide.
- **Account backup that includes raw files** (raw sources and images): not done, because the document text is already in the DB. The server backup keeps the raw files.

## Limits for later changes

- The server-wide backup and restore stay as operator tools. Account backups do not replace them.
- A new per-account feature follows the same boundary, secret, and step-up rules.
