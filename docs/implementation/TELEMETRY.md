# Telemetry and Support Bundles

Basis: commit d15ae8e0 (2026-11-16); §3.6 and the member list of §3.3: the commit that adds `support_bundle_services.py` (2026-11-18); the writers of §2 and the log list of §3.3: the commit that removes the host-cli providers (`AE8`, 2026-11-21); §3.7, the notification members, and the `row_counts` rule of §3.4: the commit that adds `support_bundle_notifications.py` (2026-11-22) · Principle: [`PRINCIPLES.md`](../PRINCIPLES.md) A4 · Commands: [`COMMANDS.md`](COMMANDS.md) §3.9

This document tells how the code records telemetry now, and how it makes, keeps, and deletes Support Bundles. It uses file paths and function names as anchors.

Evidence tags: **[code]** read in the source at the basis commit. **[decision]** set by the owner. **[unverified]** not examined.

## 1. Files

| File | Role |
| :--- | :--- |
| `src/claire/store/telemetry.py` | Telemetry database, provider and MCP records, the database registry of Support Bundles |
| `src/claire/ops/support_bundle.py` | Server-wide Support Bundle: collection, archive, sidecar registry, expiry |
| `src/claire/ops/support_bundle_services.py` | Collectors of the server-wide Support Bundle for the services, the runtime configuration, the federation, `client.db`, `auth.db`, the worker queues, and the shared knowledge bases (§3.6) |
| `src/claire/ops/support_bundle_notifications.py` | Collectors of the two Support Bundles for the notifications and Web Push (§3.7, §4) |
| `src/claire/notifications/sender.py` (`TaskRecorder`, `read_task_state`) | State file of the task of the `api` process that sends Web Push (§3.7) |
| `src/claire/ops/loop_runtime.py` | Log file and state file of the worker loops `refresh-loop`, `recover-loop`, `expand-loop` (§3.6) |
| `src/claire/ops/account_support_bundle.py` | Account-scope Support Bundle (data of one account only) |
| `src/claire/api/mcp_tools.py`, `src/claire/api/security.py`, `src/claire/api/server.py` | Write MCP telemetry |
| `src/claire/cli.py` (`telemetry`, `support-bundle`) | CLI surface |
| `src/claire/telegram/bot.py` | `/support bundle` command of the bot |

## 2. Telemetry database

- **[code] Separate file.** Telemetry goes to `telemetry.db` in the data directory (`get_telemetry_db_path`). It never goes to the knowledge database `claire.db` (principle A4).
- **[code] Connection.** `connect_telemetry` sets `journal_mode=WAL` and `busy_timeout=1000`, and opens SQLite with a timeout of 1 second. Each connection also runs `TELEMETRY_SCHEMA` (`CREATE ... IF NOT EXISTS`) and `_migrate_support_bundles_account_id`. Thus every connection, also a diagnostic read, can write to the file.
- **[code] No version marker.** The telemetry database has no `schema_version` and no `schema_lineage`.
- **[code] Tables.**
  - `provider_telemetry`: one row for each provider call (provider, model, call type, input size, exit code, duration, status, `google_block_reason`, error message, output snippet, `summary_verdict`).
  - `mcp_telemetry`: one row for each MCP tool call (`call_type="tool_call"`) or failed MCP HTTP request (`call_type="http_mcp"`, with the HTTP status in `error_code`), recording `client_id` attribution (`oauth:<id>` for OAuth grants, `api:<id>` for persistent tokens, or `env:*` for session/env channels).
  - `support_bundles`: the database registry of bundles, with the column `account_id` (NULL means a server-wide bundle).
- **[code] Fire-and-forget.** Callers catch telemetry errors. A telemetry failure does not stop ingestion or an MCP response.
- **[code] Classification.** `diagnose_google_block` sorts a CLI result by text match into `RECITATION`, `SAFETY`, `RATE_LIMIT_429`, `QUOTA_EXCEEDED`, `MAX_TOKENS`, `INVALID_SCHEMA`, `TIMEOUT`, `ENV_MISSING`, `CLI_ERROR`, or `NONE`; no code currently calls `diagnose_google_block` after `AntigravityProvider` removal (`AE8`). `evaluate_summary_verdict` marks a summary as `EMPTY`, `MOCK_PREFIX`, `RAW_SLICE_200`, `TEMPLATE_FALLBACK`, `CORRUPTED_ADOC`, `PREFACE_META`, or `REAL_LLM`; `submit_client_extraction` (`_validate_summary` in `src/claire/ingest/client_extraction.py`) calls it to enforce summary quality for `agent_client` submissions.
- **[code] Writers.** No code writes `provider_telemetry` rows: the retired host-cli provider `AntigravityProvider` was the only writer. Its old rows stay in `telemetry.db` until `claire telemetry --prune` deletes them, and the readers (`query_telemetry`, the Support Bundles, the graph bundle) still show them. The MCP tool wrapper in `src/claire/api/mcp_tools.py` (`_timed_call` + `_telemetry_client_id`), the MCP authentication failure path in `src/claire/api/security.py`, and the MCP HTTP route in `src/claire/api/server.py` (including non-200 JSON-RPC requests with `client_id`) write MCP rows. The Gemini provider does not call `record_telemetry`.
- **[code] Retention.** `DEFAULT_TELEMETRY_RETENTION_DAYS` is 30. No `Settings` field changes it (the code reads `telemetry_retention_days` with `getattr` and a default of 30). No scheduler prunes telemetry. Only `claire telemetry --prune <days>` deletes old rows (`prune_old_telemetry`, which deletes from `provider_telemetry` and `mcp_telemetry`).

## 3. Server-wide Support Bundle

### 3.1 Request surfaces

| Surface | Who | Path |
| :--- | :--- | :--- |
| `POST /support/bundle` | admin (`src/claire/api/security.py` route table) | `create_support_bundle_route` → `create_support_bundle` |
| `GET /support/bundle?token=...` | public, the token is the capability | `download_support_bundle_route` |
| `claire support-bundle` | host operator | `src/claire/cli.py` |
| Telegram `/support bundle` | admin bot | `_create_support_bundle_for_delivery` |

- **[code] Telegram delegates to the API.** When `support_bundle_api_url` is set, the admin bot sends `POST /support/bundle` to the Compose service `http://api:<port>` with the inject token. The URL must use the scheme `http` and the host `api`. Then the API service that serves the download also makes the archive and the registry entries in the same data directory. When the URL is empty, the bot makes the bundle in its own process.
- **[code] A member bot** makes an account-scope bundle (§4), not a server-wide bundle.

### 3.2 Inputs and limits

- **[code] Period.** `DEFAULT_SUPPORT_BUNDLE_DAYS` is 1. `validate_bundle_days` refuses a value below 1 or above the retention limit (30). The API returns 400. The CLI returns exit code 2.
- **[code] Target.** `_resolve_support_target` uses the document target resolver and also exact matches on `raw_inbox.payload`. The status is one of `resolved_document`, `ambiguous`, `failed_inbox`, `inbox_only`, `ambiguous_pattern`, `not_observed`, or (when the resolver fails) `collector_error`. Many candidates are kept as a list; the code does not select the first one.

### 3.3 Archive

- **[code] Format.** A tar stream compressed by zstd level 3 (`compression.zstd.ZstdFile` with `level=3`), file `support_bundle_<UTC time>_<id8>.tar.zst` in `data/support_bundles/`. `BUNDLE_FORMAT_VERSION` is 3.
- **[code] Members** (root `support_bundle_<id8>/`):
  - `manifest.json` (no download token in it; `notification_findings` has the codes of §3.7)
  - `diagnostics/`: `system.json`, `config_sanitized.json`, `mcp.json`, `storage.json`, `build.json`, `collector_warnings.json`, `services.json`, `runtime_config.json`
  - `telemetry/`: `telemetry_records.jsonl`, `telemetry_stats.json`, `mcp_records.jsonl`, `mcp_stats.json`
  - `pipeline/`: `health.json`, `inbox_summary.json`, `failed_items.json`, `shares_index.json`, `db_integrity.json`, `oauth_summary.json`, `active_ingest.json`, `worker_queues.json`, `shared_kb.json`
  - `federation/`: `server_queues.json`, `client_db.json`
  - `accounts/`: `auth_db.json`
  - `notifications/`: `summary.json` (§3.7)
  - `logs/`: the last 5000 lines of `telegram.log`, `api.log`, and of each other `*.log` in `data/logs/` (also `refresh-loop.log`, `recover-loop.log`, `expand-loop.log`, §3.6)
  - `tracked_document/` (only with a target): `target_resolution.json`, `document_detail.json`, `extractions.json`, `graph_fragment.json`, `inbox_record.json`, `inbox_records.jsonl`, `telemetry_history.jsonl`
- **[code] Storage diagnostics.** `_collect_storage_diagnostics` records real paths, the enclosing mount from `/proc/self/mountinfo`, file identity (inode, size, mtime), and a limited scan for SQLite candidates (`_MAX_STORAGE_SCAN_ENTRIES` 2000, `_MAX_STORAGE_DATABASES` 100). It skips `auth`, `backups`, `cache`, `fedify`, `logs`, `offsite-backups`, `raw`, `run`, and `support_bundles`.
- **[code] Build identity.** `build.json` uses the commit that `cb-manuscript` gives to the image as the build argument `CLAIRE_BUILD_COMMIT` (also the OCI label `org.opencontainers.image.revision`, see `Dockerfile`).

### 3.4 Masking

- **[code]** `sanitize_sensitive_data` replaces the value of a key that matches `_SENSITIVE_KEY_RE` with `***REDACTED***`. The pattern needs a word edge or `_`/`-` around the secret word, so `author` does not match `auth`.
- **[code]** Keys in `_DOCUMENT_SAFE_KEYS` (for example `author`, `title`, `raw_text`, `summary`, `token_count`) are never masked, because they hold public source text or counts.
- **[code]** In strings, `_BEARER_TOKEN_RE` masks bearer tokens and `_URL_SECRET_RE` masks secret query parameters (for example `s`, `token`, `code`, `state`). Logs go through the same function.
- **[code]** Under the key `row_counts` (the row count of each table), a whole number stays as it is. The keys are table names; before this rule the counts of `server_key_pairs` and `provider_key_decryptions` showed as `***REDACTED***`, because the names have the word `key`.
- **[code]** `shares_index.json` holds the SHA-256 of each share token, not the token. `oauth_summary.json` holds SHA-256 prefixes only.

### 3.5 Registry, download, and expiry

- **[code] Token.** `sb3_` plus `secrets.token_urlsafe(32)`. The prefix names the producer contract.
- **[code] Two registries.** `register_support_bundle` writes a row in `telemetry.db`. `_write_bundle_sidecar` writes `data/support_bundles/.registry/<sha256(token)>.json` (directory mode `0700`, file mode `0600`, atomic replace and `fsync`). The sidecar holds `registry_format_version` 1 and the token digest, not the token.
- **[code] Lookup.** `lookup_support_bundle_by_token` reads the database first. If the database read fails or has no row, it reads the sidecar. `_validated_archive_path` accepts only a file directly in `data/support_bundles/` whose name is the recorded file name.
- **[code] Lifetime.** `SUPPORT_BUNDLE_TTL_SECONDS` is 6 hours. `purge_expired_bundles` deletes expired database rows, expired sidecars with their archives, and archive files older than 6 hours. It runs before each new bundle, at API start (`app_lifespan`), and on `claire support-bundle --purge` or Telegram `/support bundle purge`.
- **[code] Download.** An unknown token gives 404. An expired token gives 410, and the code deletes the file, the row, and the sidecar. The response has `Cache-Control: no-store, private`. `robots.txt` disallows `/support/bundle`.

### 3.6 Members of the subsystems after the first format 3 members

- **[code] Reason.** The federation, the knowledge federation, `client.db`, `auth.db`, the shared knowledge bases, the runtime settings files, and the worker services came after the first members of format 3. The bundle had no data of them. The members below add them. They are new members of format 3; `BUNDLE_FORMAT_VERSION` stays 3 (a reader that does not know a member ignores it).
- **[code] Read-only.** `src/claire/ops/support_bundle_services.py` opens each database with `connect_existing(readonly=True)` (`mode=ro`, `query_only`). It does not make an absent file: an absent database or table is `{"state": "absent"}`. A failure of one part is the value `{"state": "error"}` of that part and a warning `*_COLLECTOR_FAILED` or `*_UNREADABLE` in `collector_warnings.json`. Each member also goes through `sanitize_sensitive_data`.
- **[code] Members.**

| Member | Content |
| :--- | :--- |
| `diagnostics/services.json` | `worker_loops`: the state file of each worker loop with its age (`recent`, `overdue` after two intervals and 10 minutes, `absent`, `unreadable`). `api_tasks.web_push`: the state file of the task that sends Web Push (§3.7). `fedify_sidecar`: the last heartbeat (`run/federation_heartbeat.json`) with its age, the presence of the socket, and the size and time of `fedify/fedify.db` (not its content) |
| `diagnostics/runtime_config.json` | `providers.json` and `ingest_limits.json` of the data directory, and each `server_settings` value of `claire.db`. `id_salt` is always `***REDACTED***`; API keys, `access_key`, and `secret` are masked |
| `federation/server_queues.json` | From `claire.db`: accounts by status and role; `federation_outbox` by status and kind; `kf_outbox`, `kf_inbox_journal`, `kf_remote_reads`, `kf_outbound_offers`, `kf_inbound_offers` by status or state, with the oldest and newest time; error and reason codes of the period by domain; retries of `kf_outbox`; `kf_known_servers` and `kf_remote_kb_reads` (up to 100, most errors first) |
| `federation/client_db.json` | From `client.db`: `client_meta`, `quick_check`, the row count of each table, `federation_client_outbox` by status and kind with error codes, follows by state, timeline totals, media cache by state with bytes, and feeds (failing, poll overdue for 1 hour, by `last_status`, error texts with each URL replaced by `[URL]`) |
| `accounts/auth_db.json` | From `auth.db`: `meta`, `quick_check`, the row count of each table, the Telegram bot of each account (number, username, status, masked error; no ciphertext, nonce, or fingerprint), the backup policy of each account, invitation redemptions by status, counts of the login throttle, admin audit actions of the period (no detail) |
| `pipeline/worker_queues.json` | For each knowledge base database: `expand_queue`, `refresh_queue`, `jobs`, `syndication_dispatches`, `document_presentations` by status and knowledge base. Error texts only for knowledge bases of the admin account, like `failed_items.json` |
| `pipeline/shared_kb.json` | From `claire.db`: `kb_contributions`, `kb_contribution_revisions`, `kb_mutual_collaborations`, `kb_auto_rules`, `kb_memberships` by state, and the count of `kb_submissions` |

- **[code] Error texts.** `_short_text` cuts a text at 300 characters, replaces a Telegram bot token with `***REDACTED***`, and replaces each URL with `[URL]` (except the error samples of admin knowledge bases).
- **[code] Worker loops.** The Compose services `refresh`, `recover`, and `expand` write only to the standard output, which the bundle cannot read. `ops.loop_runtime.start_loop` copies each output line with the UTC time to `data/logs/<name>-loop.log` (rotation at 10 MB, 3 old files) and writes `data/run/loop_<name>.json` after each cycle (start time, time and result of the last cycle, last error, counts of the cycle, interval). A failure to write a record does not stop the loop.
- **[code] Not in the bundle.** The standard output of the Fedify sidecar and the content of `fedify.db` (the data of the sidecar), password hashes, keys, ciphertexts, invitation codes, the keys of the login throttle, feed and post addresses, and the payloads of the queues.

### 3.7 Notifications and Web Push

Function: [`NOTIFICATIONS.md`](NOTIFICATIONS.md). Collector: `support_bundle_notifications.collect_notifications` (read-only connections to `client.db`, `claire.db`, and `auth.db`; it makes no file).

- **[code] Reason.** A person who gets no notification cannot see the cause. The records are in `client.db`, the key pair is in `auth.db`, the sessions are in `claire.db`, and the sender is a task of the `api` process that had only log lines. The member puts these in one place and names the cause.
- **[code] Task state.** The task writes `data/run/task_web_push.json` (`sender.TaskRecorder`): process, interval, start, last cycle and its result, last cycle with work and its counts, totals since the start (`sent`, `retried`, `dropped`, `removed_subscriptions`, `told_contributions`), and the last 20 reasons of failed sends. It writes after a cycle with work, after a failed cycle, and one time each 60 seconds at least. `read_task_state` gives `recent`, `overdue` (older than 180 s), `absent`, or `unreadable`. The file has no endpoint, key, or account.
- **[code] Member `notifications/summary.json`.**

| Key | Content |
| :--- | :--- |
| `findings` | The diagnosis: a list of `{"code", "message"}` (table below). An empty list means that the collector found no problem. |
| `readiness` | `public_origin` (the settings give a canonical origin), `origin_scheme`, and the state and the time of the VAPID key pair row |
| `sender_task` | The task state file with its state and age |
| `client_db` | Path and state: `present`, `absent`, `no_notification_tables` (a `client.db` before version 9), `error` |
| `notifications`, `notifications_by_kind` | Totals; for each kind: channel, class, `known`, notifications, unread, events (the sum of the counts), made in the period, unread that were never sent, newest time, oldest unread time |
| `push_queue` | Messages, due now, retrying, most attempts, the oldest due time and its age, and the messages by kind |
| `push_subscriptions` | Devices, accounts, failing devices, most failures in sequence, devices without a session in effect (the sender deletes them at its next send), and for each push service host: devices, failing, never sent, last success. `last_errors`: result codes with the count of devices |
| `push_off` | For each kind: the count of accounts that turned Web Push off |
| `scan` | The contribution scan: time of the first scan, contributions that it told about, contributions that wait for review, and those that no scan told about |
| `accounts_with_most_unread` | 20 accounts at most: account number, unread, oldest unread time, devices, queued messages |

| Finding | Meaning |
| :--- | :--- |
| `NOTIFICATION_TABLES_ABSENT` | `client.db` is older than version 9 |
| `NO_PUBLIC_ORIGIN` | No canonical origin while devices or queued messages exist: the sender drops each message |
| `ORIGIN_NOT_HTTPS` | The canonical origin is `http` while devices exist |
| `VAPID_PAIR_ABSENT` | Devices exist and `auth.db` has no key pair: each device must subscribe again |
| `SENDER_TASK_NOT_RUNNING`, `SENDER_TASK_CYCLE_FAILED` | The task state is `absent`, `overdue`, or `unreadable`; or its last cycle failed |
| `QUEUE_OVERDUE` | The oldest due message waits for more than 120 s |
| `DEVICES_FAILING`, `DEVICES_WITHOUT_SESSION` | Devices with failed sends (with the last result codes); devices whose sign-in session ended |
| `UNKNOWN_KINDS` | Notifications of a kind that the code does not know |
| `SCAN_NEVER_RAN`, `SCAN_BEHIND` | Contributions wait for review, and the scan never ran or did not tell about some of them |

- **[code] Not in the member.** The endpoint of a subscription (only its host name: the path is the capability of the device), `p256dh`, `auth`, the session identifier, the VAPID keys, the parameters of a notification (names of accounts, actors, and knowledge bases), and the entry address.
- **[code] Tests.** `tests/test_support_bundle_services.py` (the member with each finding, no capability in the archive, the absent and the old `client.db`, the task state file) and `tests/test_notifications.py` (the account-scope member through the routes).

## 4. Account-scope Support Bundle

- **[code]** `create_account_support_bundle` puts only the data of the calling account in the archive: an account summary without secrets, owned themes, queue summary and failed items of owned themes, provider telemetry of owned documents, an optional tracked document, and build information. It does not include host or mount diagnostics, settings, log files, OAuth or MCP records, other accounts, document bodies, or secrets.
- **[code]** `notifications/summary.json` (`collect_account_notifications`): only rows of the account. `server_can_send` and `server_sender_task` (the state word only); `devices` (number, label, push service host, `session` `in_effect` or `ended`, times, failures in sequence, last result code); `kinds_without_push`; `notifications_by_kind` and `push_queue` as in §3.7; `recent` (the last 50 notifications: number, kind, events, and the times made, changed, read, and sent); `findings` with the codes `SERVER_CANNOT_SEND`, `SERVER_SENDER_NOT_RUNNING`, `NO_DEVICE`, `DEVICE_SESSION_ENDED`, `DEVICE_FAILING`. Times are ISO 8601. It has no endpoint, no key, no parameter of a notification, and no entry address. A failure of this part gives a member with the error type; the bundle is made in each case.
- **[code]** It uses the same registry, sidecar, token, and download route as the server-wide bundle. The column `account_id` marks the owner. `MAX_ACTIVE_ACCOUNT_BUNDLES` is 5.
- **[code]** Members use it from the web settings (`src/claire/api/accounts_routes/features.py`) and from a member Telegram bot. See [`ACCOUNTS.md`](ACCOUNTS.md) for the account features.
