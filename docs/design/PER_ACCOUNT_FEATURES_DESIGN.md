# Per-Account Features: Telegram Bot, Support Bundle, Data Bundle, Backup

Status: IMPLEMENTED (2026-11-10).

## 1. Goal

Change four instance features into per-account features. Each account owner configures and controls each feature in the WebUI.

| Feature | Before | After |
|---------|--------|-------|
| Telegram bot | One bot (`TELEGRAM_BOT_TOKEN`), administrator account only | One bot for each account, registered in `/settings/telegram` |
| Support bundle | `POST /support/bundle`, administrator only, instance diagnostics | Account-scope bundle in `/settings/bundles`. The administrator instance bundle stays |
| Data bundle | `POST /graph/bundle` for one owned theme, no management | Create, list, download, and delete in `/settings/bundles` |
| Backup | `./cb-manuscript backup` and `restore`, full instance only | Account backup with schedule in `/settings/backups`. Restore in `/admin/backups` (operator only). The full instance backup stays |

## 2. Owner decisions

| ID | Decision |
|----|----------|
| PA1 | Telegram: each account registers its own BotFather token. One runner process (`claire bot`) runs all bots. |
| PA2 | Backup: the account owner creates, lists, downloads, and deletes backups and sets the schedule. Only the operator can restore, from the operator menu. |
| PA3 | Support bundle: add an account-scope bundle. Keep the administrator instance bundle. |
| PA4 | Implement directly, without a separate design approval step. |

PA1 replaces the earlier rule "the Telegram bot is for the administrator only" in `EXTERNAL_IDP_AND_PER_USER_WORKSPACE_DESIGN.md` §2 and `MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md` §10.1. The instance bot keeps its administrator role (login approval and alerts).

## 3. Storage

auth.db schema v4 adds two tables.

- `account_telegram_bots`: one row for each account. The bot token is encrypted with AES-256-GCM. The AAD is `telegram|account_id|key_id`. `bot_id` is unique, so two accounts cannot use the same bot. The allowed user list must not be empty. `status` (`pending`, `running`, `error`, `disabled`) and `last_error` are written by the runner.
- `account_backup_policies`: `schedule` (`off`, `daily`, `weekly`), `keep` (1 to 10), and the result of the last scheduled run.

`telemetry.db` `support_bundles` gets the column `account_id`. NULL means an instance bundle. The graph bundle registry records `account_id` (the theme owner).

Account backups are files in `data/account_backups/<account_id>/`.

## 4. Telegram bot

- `telegram_supervisor.BotSupervisor` reads the wanted bots every 15 seconds: the instance bot (if `TELEGRAM_BOT_TOKEN` is set) and every enabled account bot of an `active` account. It starts new bots, stops removed or disabled bots, and restarts a bot when its token or allowed users change. A bot that fails to start is not retried for 120 seconds with the same settings.
- `telegram_bot.build_app(settings, profile=...)` builds one bot. An account bot uses only the themes and provider context of its account (`AccountThemeScope`, `IngestServicePool`).
- A member account bot refuses the operator commands `/web`, `/webco`, `/webro`, `/token`, `/status` and the callbacks `auth:`, `oauth_appr:`, `oauth_deny:`. `/support` makes an account-scope bundle. `/export graph` exports only the account's own themes.
- The Compose `bot` service always runs. When no bot is configured, it waits.

## 5. Support bundle (account scope)

`account_support_bundle.create_account_support_bundle` puts in: account summary without secrets, owned themes and counts, inbox summary and failed items of owned themes, provider telemetry of owned documents, an optional tracked document (only inside owned themes), and build identity. It does not put in: host, mount, or storage diagnostics, configuration values, log files, OAuth or MCP records, other accounts' data, or document bodies. An account can have 5 active bundles. Bundles expire after 6 hours.

## 6. Backup and restore

- A backup has `manifest.json` and one SQLite snapshot for each owned theme (`themes/<id>.db`). A snapshot has the theme row, the theme rows of every table with a `theme_id` column, and the `entities_fts` rows of the theme entities. All themes are read in one read transaction. A backup does not contain auth.db, `master.key`, or `data/raw` and `data/images` files.
- A manual backup cannot be made less than 60 seconds after the last backup. After each backup, backups above `keep` are deleted, oldest first.
- The API process runs the schedule check every 10 minutes (`run_due_backups`).
- Restore (operator only, step-up authentication required) checks the archive (safe members, manifest hash, schema version and lineage), makes a checkpoint of the knowledge DB in `data/checkpoints/`, and replaces the rows of each theme in the backup in one transaction. It refuses, and changes nothing, when a theme now belongs to another account or a primary key collides with a row of another theme. Themes made after the backup stay as they are.

## 7. Routes

| Path | Methods | Access |
|------|---------|--------|
| `/api/me/telegram` | GET, HEAD, PUT, PATCH, DELETE | any, owner |
| `/api/me/support-bundles` | GET, HEAD, POST, DELETE | any, owner |
| `/api/me/support-bundles/download` | GET, HEAD | any, owner |
| `/api/me/data-bundles` | GET, HEAD, POST, DELETE | any, owner |
| `/api/me/data-bundles/download` | GET, HEAD | any, owner |
| `/api/me/backups` | GET, HEAD, POST, DELETE | any, owner |
| `/api/me/backups/download` | GET, HEAD | any, owner |
| `/api/me/backups/policy` | PUT | any, owner |
| `/api/admin/account-backups` | GET, HEAD | system, admin |
| `/api/admin/account-backups/restore` | POST | system, admin |
| `/settings/telegram`, `/settings/bundles`, `/settings/backups` | GET, HEAD | system, owner |
| `/admin/backups` | GET, HEAD | system, admin |

Step-up authentication: bot register, replace, and delete; allowed user change; backup delete; restore.

An account purge also deletes the account's bot, backup policy, backups, and account support bundles.

## 8. Limits

- A backup does not contain raw files (`data/raw`, `data/images`). Document text is in the DB.
- Bot status in the WebUI can be up to 15 seconds old.
- Instance backups (`./cb-manuscript backup`) include `data/account_backups/`.

## 9. Tests

- `tests/test_account_features.py`: bot store (I/O, encryption, AAD), API isolation and step-up, support bundle content scope, data bundle lifecycle, backup policy and schedule, restore and conflict, purge, runner.
- `tests/test_bot.py::test_member_account_bot_is_scoped_to_its_account`: member bot command and theme scope.
- `tests/test_e2e_account_features.py`: E2E. A member configures every feature in the WebUI and the operator restores a backup.
