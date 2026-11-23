# Telemetry and Support Bundles

Basis: commit d15ae8e0 (2026-11-16); §3.6 and the member list of §3.3: the commit that adds `support_bundle_services.py` (2026-11-18); the writers of §2 and the log list of §3.3: the commit that removes the host-cli providers (`AE8`, 2026-11-21); §3.7, the notification members, and the `row_counts` rule of §3.4: the commit that adds `support_bundle_notifications.py` (2026-11-22); federation diagnostics in §3.3, §3.6, and §4: current working tree (`support_bundle_federation.py`, 2026-11-22); the MCP trail columns of §2, the surfaces of §3.1, the tracked item of §3.8, and §4: the commit that adds `support_bundle_target.py` and the page `/support/bundles` (2026-11-24) · Principle: [`PRINCIPLES.md`](../PRINCIPLES.md) A4 · Commands: [`COMMANDS.md`](COMMANDS.md) §3.9

This document tells how the code records telemetry now, and how it makes, keeps, and deletes Support Bundles. It uses file paths and function names as anchors.

Evidence tags: **[code]** read in the source at the basis commit. **[decision]** set by the owner. **[unverified]** not examined.

## 1. Files

| File | Role |
| :--- | :--- |
| `src/claire/store/telemetry.py` | Telemetry database, provider and MCP records, the database registry of Support Bundles |
| `src/claire/ops/support_bundle.py` | Server-wide Support Bundle: collection, archive, sidecar registry, expiry |
| `src/claire/ops/support_bundle_services.py` | Collectors of the server-wide Support Bundle for the services, the runtime configuration, the federation, `client.db`, `auth.db`, the worker queues, and the shared knowledge bases (§3.6) |
| `src/claire/ops/support_bundle_federation.py` | Collectors of the two Support Bundles for sidecar heartbeat findings and account-scope federation state (§3.6, §4) |
| `src/claire/ops/support_bundle_notifications.py` | Collectors of the two Support Bundles for the notifications and Web Push (§3.7, §4) |
| `src/claire/notifications/sender.py` (`TaskRecorder`, `read_task_state`) | State file of the task of the `api` process that sends Web Push (§3.7) |
| `src/claire/ops/loop_runtime.py` | Log file and state file of the worker loops `refresh-loop`, `recover-loop`, `expand-loop` (§3.6) |
| `src/claire/ops/host_runtime.py` | Resource records of the host and of each Python service, and the member `diagnostics/host.json` (§3.6) |
| `src/claire/ops/account_support_bundle.py` | Account-scope Support Bundle (data of one account only) |
| `src/claire/ops/support_bundle_target.py` | The tracked item of the two bundles: resolution of a note or document link, the extraction trail of a document, the delivery trail of a note (§3.8) |
| `src/claire/api/accounts_routes/features.py` | The routes `/api/support/bundles*` and the body of the page `/support/bundles` |
| `src/claire/api/mcp_tools.py`, `src/claire/api/security.py`, `src/claire/api/server.py` | Write MCP telemetry |
| `src/claire/cli.py` (`telemetry`, `support-bundle`) | CLI surface |
| `src/claire/telegram/bot.py` | `/support bundle` command of the bot |

## 2. Telemetry database

- **[code] Separate file.** Telemetry goes to `telemetry.db` in the data directory (`get_telemetry_db_path`). It never goes to the knowledge database `claire.db` (principle A4).
- **[code] Connection.** `connect_telemetry` sets `journal_mode=WAL` and `busy_timeout=1000`, and opens SQLite with a timeout of 1 second. Each connection also runs `TELEMETRY_SCHEMA` (`CREATE ... IF NOT EXISTS`) and `_migrate_support_bundles_account_id`. Thus every connection, also a diagnostic read, can write to the file.
- **[code] No version marker.** The telemetry database has no `schema_version` and no `schema_lineage`. Each connection adds the columns that a file does not have (`support_bundles.account_id`, `support_bundles.target_link`, `mcp_telemetry.kb_id`, `document_id`, `detail`, and the index `idx_mcp_document`); old rows keep NULL.
- **[code] Tables.**
  - `provider_telemetry`: one row for each provider call (provider, model, call type, input size, exit code, duration, status, `google_block_reason`, error message, output snippet, `summary_verdict`).
  - `mcp_telemetry`: one row for each MCP tool call (`call_type="tool_call"`) or failed MCP HTTP request (`call_type="http_mcp"`, with the HTTP status in `error_code`), recording `client_id` attribution (`oauth:<id>` for OAuth grants, `api:<id>` for persistent tokens, or `env:*` for session/env channels). The columns `kb_id`, `document_id`, and `detail` (JSON) are the extraction trail (§3.8). Only `extract_stage`, `extract_claim`, `extract_read`, and `extract_submit` fill them (`_timed_call(..., trail=...)` and `_trail_detail` in `mcp_tools.py`); a call that the tool refuses before its work (scope, knowledge base, argument checks) has no row. `detail` has the facts of the request (`mode`, `force`, `refetch`, `renew`, `dry_run`, and for `extract_submit` `model_field` = `absent` \| `empty` \| `set` with `submitted_model`, at most 200 characters) and the codes of the answer (`ok`, `error`, `extraction_status`, `committed`, `duplicate`, the `code` of each item of `warnings` and `errors`, and `recorded_model`). It has no source text, summary, claim token, or payload.
  - `support_bundles`: the database registry of bundles, with the column `account_id` (NULL means a server-wide bundle) and the column `target_link` (the address of the tracked item: its Silvercord link, else the text that the person gave).
- **[code] Fire-and-forget.** Callers catch telemetry errors. A telemetry failure does not stop ingestion or an MCP response.
- **[code] Classification.** `diagnose_google_block` sorts a CLI result by text match into `RECITATION`, `SAFETY`, `RATE_LIMIT_429`, `QUOTA_EXCEEDED`, `MAX_TOKENS`, `INVALID_SCHEMA`, `TIMEOUT`, `ENV_MISSING`, `CLI_ERROR`, or `NONE`; no code currently calls `diagnose_google_block` after `AntigravityProvider` removal (`AE8`). `evaluate_summary_verdict` marks a summary as `EMPTY`, `MOCK_PREFIX`, `RAW_SLICE_200`, `TEMPLATE_FALLBACK`, `CORRUPTED_ADOC`, `PREFACE_META`, or `REAL_LLM`; `submit_client_extraction` (`_validate_summary` in `src/claire/ingest/client_extraction.py`) calls it to enforce summary quality for `agent_client` submissions.
- **[code] Writers.** No code writes `provider_telemetry` rows: the retired host-cli provider `AntigravityProvider` was the only writer. Its old rows stay in `telemetry.db` until `claire telemetry --prune` deletes them, and the readers (`query_telemetry`, the Support Bundles, the graph bundle) still show them. The MCP tool wrapper in `src/claire/api/mcp_tools.py` (`_timed_call` + `_telemetry_client_id`), the MCP authentication failure path in `src/claire/api/security.py`, and the MCP HTTP route in `src/claire/api/server.py` (including non-200 JSON-RPC requests with `client_id`) write MCP rows. The Gemini provider does not call `record_telemetry`.
- **[code] Retention.** `DEFAULT_TELEMETRY_RETENTION_DAYS` is 30. No `Settings` field changes it (the code reads `telemetry_retention_days` with `getattr` and a default of 30). No scheduler prunes telemetry. Only `claire telemetry --prune <days>` deletes old rows (`prune_old_telemetry`, which deletes from `provider_telemetry` and `mcp_telemetry`).

## 3. Server-wide Support Bundle

### 3.1 Request surfaces

| Surface | Who | Path |
| :--- | :--- | :--- |
| `/support/bundles` (page) and `POST /api/support/bundles` with `scope` `server` | admin (the handler gives 404 to other accounts) | `support_create` in `features.py` → `create_support_bundle` |
| `POST /support/bundle` | admin (`src/claire/api/security.py` route table) | `create_support_bundle_route` → `create_support_bundle` (the API of the Telegram delegation) |
| `GET /support/bundle/{token}` | public, the token is the capability | `download_support_bundle_route` |
| `claire support-bundle` | host operator | `src/claire/cli.py` |
| Telegram `/support bundle` | admin bot | `_create_support_bundle_for_delivery` |

- **[code] Telegram delegates to the API.** When `support_bundle_api_url` is set, the admin bot sends `POST /support/bundle` to the Compose service `http://api:<port>` with the inject token. The URL must use the scheme `http` and the host `api`. Then the API service that serves the download also makes the archive and the registry entries in the same data directory. When the URL is empty, the bot makes the bundle in its own process.
- **[code] A member bot** makes an account-scope bundle (§4), not a server-wide bundle.

### 3.2 Inputs and limits

- **[code] Period.** `DEFAULT_SUPPORT_BUNDLE_DAYS` is 1. `validate_bundle_days` refuses a value below 1 or above the retention limit (30). The API returns 400. The CLI returns exit code 2.
- **[code] Target.** A Silvercord link of a note goes first to `resolve_note_link` (a note of any account). Other text goes to `_resolve_support_target`, which uses the document target resolver and also exact matches on `raw_inbox.payload`. The status is one of `resolved_document`, `ambiguous`, `failed_inbox`, `inbox_only`, `ambiguous_pattern`, `not_observed`, or (when the resolver fails) `collector_error`. Many candidates are kept as a list; the code does not select the first one.

### 3.3 Archive

- **[code] Format.** A tar stream compressed by zstd level 3 (`compression.zstd.ZstdFile` with `level=3`), file `support_bundle_<UTC time>_<id8>.tar.zst` in `data/support_bundles/`. `BUNDLE_FORMAT_VERSION` is 3.
- **[code] Members** (root `support_bundle_<id8>/`):
  - `manifest.json` (no download token in it; `notification_findings` has the codes of §3.7 and `federation_findings` has the heartbeat finding codes of §3.6)
  - `diagnostics/`: `system.json`, `config_sanitized.json`, `mcp.json`, `storage.json`, `build.json`, `collector_warnings.json`, `services.json`, `host.json`, `runtime_config.json`
  - `telemetry/`: `telemetry_records.jsonl`, `telemetry_stats.json`, `mcp_records.jsonl`, `mcp_stats.json`
  - `pipeline/`: `health.json`, `inbox_summary.json`, `failed_items.json`, `shares_index.json`, `db_integrity.json`, `oauth_summary.json`, `active_ingest.json`, `worker_queues.json`, `shared_kb.json`
  - `federation/`: `server_queues.json`, `client_db.json`
  - `accounts/`: `auth_db.json`
  - `notifications/`: `summary.json` (§3.7)
  - `logs/`: the last 5000 lines of `telegram.log`, `api.log`, and of each other `*.log` in `data/logs/` (also `refresh-loop.log`, `recover-loop.log`, `expand-loop.log`, and the resource records `host-<service>.log`, §3.6)
  - `tracked_document/` (only with a target): `target_resolution.json`, `document_detail.json`, `extractions.json`, `graph_fragment.json`, `inbox_record.json`, `inbox_records.jsonl`, `telemetry_history.jsonl`, and, for a resolved document, `extraction_trail.json` (§3.8)
  - `tracked_note/` (only with the Silvercord link of a note; then there is no `tracked_document/`): `target_resolution.json` (status `resolved_note`) and `note_trail.json` (§3.8)
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
| `diagnostics/host.json` | `now`: one sample at the time of the bundle. `services.<service>`: from `data/logs/host-<service>.log`, the number of records of the period, the last record, `peaks` (lowest available memory, free swap and free disk of the host; highest load, memory and I/O pressure, container memory, process memory, open files), `restarts` (each start of the process or of the host: time, time of the last record before it, `gap_seconds`, `host_restarted`, `stop_recorded`, and the last 10 records before it), `stalls` (gaps of more than 5 minutes on one boot), `api_stalls` (each sequence of records in which the event loop of the `api` process did not answer or its own request failed: start, end, time of the first good record after it, highest executor queue, first record), and `network_outages` (each sequence of failed network probes: start, end, time of the first good probe after it, number of probes, failed steps, last error). `findings`: `API_EVENT_LOOP_BLOCKED`, `API_REQUEST_FAILED`, `API_EXECUTOR_QUEUE`, `HOST_NETWORK_UNREACHABLE`, `HOST_RESTART_WITHOUT_STOP`, `HOST_MEMORY_LOW_BEFORE_RESTART`, `HOST_MEMORY_PRESSURE`, `HOST_RECORD_STALLED`, `CGROUP_OOM_KILL`. The manifest has the codes in `host_findings` |
| `diagnostics/runtime_config.json` | `providers.json` and `ingest_limits.json` of the data directory, and each `server_settings` value of `claire.db`. `id_salt` is always `***REDACTED***`; API keys, `access_key`, and `secret` are masked |
| `federation/server_queues.json` | From `claire.db`: accounts by status and role; `federation_outbox` by status and kind; `federation_followers` and `federation_objects` totals; `heartbeat_findings` (`SIDECAR_HEARTBEAT_ABSENT`, `SIDECAR_HEARTBEAT_STALE`, `SIDECAR_SELF_CHECK_FAILED`, `SIDECAR_SIGNED_CHECK_FAILED`, `INBOUND_SIGNATURES_REJECTED`, `DELIVERIES_FAILED`); `kf_outbox`, `kf_inbox_journal`, `kf_remote_reads`, `kf_outbound_offers`, `kf_inbound_offers` by status or state, with the oldest and newest time; error and reason codes of the period by domain; retries of `kf_outbox`; `kf_known_servers` and `kf_remote_kb_reads` (up to 100, most errors first) |
| `federation/client_db.json` | From `client.db`: `client_meta`, `quick_check`, the row count of each table, `federation_client_outbox` by status and kind with error codes, follows by state, timeline totals, `federation_notes` totals (`total`, `active`, `deleted`, `published_in_period`), `federation_object_reactions` by kind, `federation_quote_authorizations` by state, media cache by state with bytes, and feeds (failing, poll overdue for 1 hour, by `last_status`, error texts with each URL replaced by `[URL]`) |
| `accounts/auth_db.json` | From `auth.db`: `meta`, `quick_check`, the row count of each table, the Telegram bot of each account (number, username, status, masked error; no ciphertext, nonce, or fingerprint), the backup policy of each account, invitation redemptions by status, counts of the login throttle, admin audit actions of the period (no detail) |
| `pipeline/worker_queues.json` | For each knowledge base database: `expand_queue`, `refresh_queue`, `jobs`, `syndication_dispatches`, `document_presentations` by status and knowledge base. Error texts only for knowledge bases of the admin account, like `failed_items.json` |
| `pipeline/shared_kb.json` | From `claire.db`: `kb_contributions`, `kb_contribution_revisions`, `kb_mutual_collaborations`, `kb_auto_rules`, `kb_memberships` by state, and the count of `kb_submissions` |

- **[code] Error texts.** `_short_text` cuts a text at 300 characters, replaces a Telegram bot token with `***REDACTED***`, and replaces each URL with `[URL]` (except the error samples of admin knowledge bases).
- **[code] Worker loops.** The Compose services `refresh`, `recover`, and `expand` write only to the standard output, which the bundle cannot read. `ops.loop_runtime.start_loop` copies each output line with the UTC time to `data/logs/<name>-loop.log` (rotation at 10 MB, 3 old files) and writes `data/run/loop_<name>.json` after each cycle (start time, time and result of the last cycle, last error, counts of the cycle, interval). A failure to write a record does not stop the loop.
- **[code] Host records.** After a forced restart of a host that did not respond, `/proc` has only new data, and the bundle had no record from before the restart. `ops.host_runtime.start_host_recorder` runs a thread in each Python service (`api` in `serve-api`, `telegram` in `claire bot`, `<name>-loop` in `start_loop`). The thread appends one JSON line each 60 seconds to `data/logs/host-<service>.log` and calls `fsync`, thus the lines before the stop stay on the disk (rotation at 5 MB, 1 old file, approximately 8 days). A line has the kernel boot ID (`/proc/sys/kernel/random/boot_id`), the host values that a container can read (`/proc/meminfo`, `/proc/loadavg`, `/proc/uptime`, `/proc/pressure/*`, free bytes of the data directory), the cgroup v2 values of the container (`memory.current`, `memory.max`, `memory.swap.current`, `oom` and `oom_kill` of `memory.events`), and the process values (resident memory, threads, open files). The lines `start` and `stop` mark the start and the correct stop (`atexit`) of the process. A value that cannot be read is `null`. A failure to write does not stop the service.
- **[code] Network probe.** A time in which the network of the host is down shows in `api.log` only as a time without lines, the same as a stop of the API process. The `api` service thus adds `network` to each line: it resolves the host of `public_url` (`getaddrinfo`) and opens one TCP connection to its port (timeout 5 seconds). The value has `ok`, `failed_step` (`dns` or `connect`), the error, and the time of each step. The probe sends no request. It does not run for an empty `public_url`, for a loopback or literal IP host, or on the `stop` line. `HOST_NETWORK_UNREACHABLE` reports each sequence of failed probes; the lines of that time also show that the process ran.
- **[code] API process probe.** The web service can stop to answer while the process and the host run (a blocked event loop, or no free thread in the default executor that `asyncio.to_thread` uses). `api.log` then has only a time without lines. `host_runtime.ApiProbe` runs in the recorder thread of the `api` service and adds `api` to each `sample` line: `loop_blocked` and `loop_lag_seconds` (a callback through `call_soon_threadsafe`, limit 5 seconds), `tasks`, `executor_threads`, `executor_max_threads`, `executor_queued`, `threads`, and `request` (one `GET /health` to its own listener, limit 5 seconds; each HTTP status is `ok`). `app_lifespan` gives the event loop to the probe (`set_api_loop`). When the loop does not answer or the request fails, the probe appends the stack of each thread (`faulthandler`) and of each asyncio task (8 frames, at most 200 tasks) to `data/logs/api-stall.log` (rotation at 5 MB, 1 old file). The stacks have file names, line numbers, and function names, no values. The bundle collects the file with the other logs. `API_EVENT_LOOP_BLOCKED`: the loop did not answer. `API_REQUEST_FAILED`: the request failed while the loop answered. `API_EXECUTOR_QUEUE`: calls waited for a thread.
- **[code] Host findings.** `HOST_RESTART_WITHOUT_STOP`: the boot ID changed and the `api` service has no `stop` line before it (the worker loops stop on `SIGTERM` without a `stop` line, thus only `api` gives this finding). `HOST_MEMORY_LOW_BEFORE_RESTART`: the last record before a restart without `stop` has less than 5% of the host memory available. `HOST_MEMORY_PRESSURE`: memory pressure `full avg10` of 25% or more. `HOST_RECORD_STALLED`: no record for more than 5 minutes on one boot. `CGROUP_OOM_KILL`: `oom_kill` increased across a restart of the process on one boot.
- **[code] Limits of the host records.** A container sees only its own cgroup and processes: the Fedify sidecar and other programs of the host have no record, only their effect on the host values. The kernel log and the Docker state of the host are not in the bundle. The account-scope bundle (§4) has no host data.
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

### 3.8 Tracked item: the link of a note or a document

- **[code] Reason.** A person sees a problem on one item: for example, the model badge of a document is absent. The bundle must show what the server stored for that item and what the clients sent for it. The MCP records had no document and no arguments, so a bundle could not answer which model an agent client sent (support bundle `sb_20261123_145828_21889db0`: the answer came only from the response sizes).
- **[code] Input.** The field "노트·문서 링크" of `/support/bundles` (`target`): a Silvercord link (`/silvercord/{token}`, absolute or as a path, or the bare token), a document ID, or the source URL of a document. `permalink_token_of` reads the token; an entity address is not a target.
- **[code] Account scope.** `resolve_owned_target` accepts only a note of the account (`permalinks.account_id`) or a document in a knowledge base of the account. Each other case raises `SupportTargetNotFound`, and the API answers 404 `target_not_found`: the bundle is not made. An item of another account is never tracked.
- **[code] `extraction_trail.json`** (`document_trail`):

| Key | Content |
| :--- | :--- |
| `document` | ID, Silvercord link, `extraction_status`, `extraction_origin`, `claimed_by_client_id`, lease time, `raw_chars`, fetch time, `hidden` |
| `item_view` | `extraction_provider` and `extraction_model` of `latest_extraction_model`, and `model_badge`: the text of the model badge of the item view (`reader.js`), `null` when the view shows no badge |
| `client_extraction` | The keys of `meta.client_extraction` (origin, client, OAuth client, model, schema and prompt versions, content hash, commit time) |
| `extractions` | The last 50 rows of `extractions`, newest first: time, provider, stored `model`, `model_recorded`, prompt version, and from `raw_response` the origin, client, OAuth client with its registered name, schema version, and `model_in_response` (`absent` when the key is absent). No summary, claims, entities |
| `mcp_calls` | The MCP calls of the document (`query_mcp_trail`, oldest first, at most 500): time, tool, status, client, duration, response size, error text (300 characters), `detail` (§2) |
| `federation` | The published object of the document with its outbox tasks (kind, status, error code, times) |
| `findings` | `NO_EXTRACTION`, `EXTRACTION_MODEL_MISSING` (the latest extraction is from an agent client without a model: the view shows no badge), `SUBMIT_WITHOUT_MODEL` (a commit of `extract_submit` without `payload.model`), `MODEL_WARNING_NOT_ADDRESSED` (a dry run gave `model_missing` and a commit came without a model), `MCP_TRAIL_ABSENT` (an agent client extraction without recorded calls: older than the trail columns or than the retention), `PENDING_WITHOUT_CLAIM`, `DOCUMENT_ABSENT` |

- **[code] `note_trail.json`** (`note_trail`): `note` (number, Silvercord link, times, `is_reply`, `has_audience`, `sensitive`, language, `text_chars`, the counts of mentions and tags, quote state and policy, `is_quote`), `client_tasks` (the last 100 tasks of `federation_client_outbox` with `ref_id` = the note: kind, status, error code, times; no target address), `reactions` (count by kind), `quote_requests` (count by state), and `findings` (`NOTE_DELETED`, `CLIENT_TASK_REJECTED`, `CLIENT_TASK_PENDING` after 10 minutes, `NO_CLIENT_TASK`, `NOTE_ABSENT`, `CLIENT_DB_ABSENT`, `NOTE_TABLES_ABSENT`). No note text or HTML, no remote address.
- **[code] Manifest.** The account-scope manifest has `target` (`kind`, `link`, the finding codes). The server-wide manifest keeps the target resolution in `target`.
- **[code] Tests.** `tests/test_account_features.py` (document link with the findings of a missing model, note link, refusal of an item of another account, administrator scope) and `tests/e2e_agent_extraction/test_e2e_client_extraction_ae5.py` (the MCP trail of a real OAuth extraction and the bundle of its Silvercord link).

## 4. Account-scope Support Bundle

- **[code]** `create_account_support_bundle` puts only the data of the calling account in the archive: an account summary without secrets, owned themes, queue summary and failed items of owned themes, provider telemetry of owned documents, an optional tracked note or document (§3.8: `tracked_document/target_resolution.json` and `extraction_trail.json`, or `tracked_note/target_resolution.json` and `note_trail.json`), and build information. It does not include host or mount diagnostics, settings, log files, OAuth records, MCP records other than the calls of the tracked document, other accounts, document or note bodies, or secrets.
- **[code]** `notifications/summary.json` (`collect_account_notifications`): only rows of the account. `server_can_send` and `server_sender_task` (the state word only); `devices` (number, label, push service host, `session` `in_effect` or `ended`, times, failures in sequence, last result code); `kinds_without_push`; `notifications_by_kind` and `push_queue` as in §3.7; `recent` (the last 50 notifications: number, kind, events, and the times made, changed, read, and sent); `findings` with the codes `SERVER_CANNOT_SEND`, `SERVER_SENDER_NOT_RUNNING`, `NO_DEVICE`, `DEVICE_SESSION_ENDED`, `DEVICE_FAILING`. Times are ISO 8601. It has no endpoint, no key, no parameter of a notification, and no entry address. A failure of this part gives a member with the error type; the bundle is made in each case.
- **[code]** `federation/summary.json` (`collect_account_federation`): only rows of the account (`WHERE account_id = ?`). Includes `actor` (`public_id`, `is_actor`, `actor_state`, `discoverable`, `handle`, `actor_id`), `sidecar` (safe heartbeat summary without secrets: `state`, `received_at`, `age_seconds`, `contract_version`, `sidecar_version`, `queue_depth`, `self_check_ok`, `knowledge_self_check_failed_paths`, `signed_checks`, aggregate `traffic` counters), `followers` and `published_objects` totals, `outbox_tasks`, `following`, `client_outbox_tasks`, `timeline`, `notes` (up to 20 recent rows with `public_id`, ISO timestamps, `quote_policy`, `likes`, `announces`, `quotes` and no text/HTML), `reactions`, `quotes` by state, and `findings` (`SIDECAR_HEARTBEAT_ABSENT`, `SIDECAR_HEARTBEAT_STALE`, `SIDECAR_SELF_CHECK_FAILED`, `SIDECAR_SIGNED_CHECK_FAILED`, `INBOUND_SIGNATURES_REJECTED`, `DELIVERIES_FAILED`, `ACCOUNT_NOT_ACTOR`, `OUTBOX_TASKS_REJECTED`, `CLIENT_OUTBOX_TASKS_REJECTED`). `manifest.json` records `counts.federation_findings` and `counts.federation_notes`. It excludes note bodies, remote actor addresses, keys, and other accounts. A failure of this part gives a member with the error type.
- **[code]** It uses the same registry, sidecar, token, and download route as the server-wide bundle. The column `account_id` marks the owner. `MAX_ACTIVE_ACCOUNT_BUNDLES` is 5.
- **[code]** Each account uses it from the page `/support/bundles` (`/api/support/bundles` with `scope` `account`, `src/claire/api/accounts_routes/features.py`) and from a member Telegram bot. See [`ACCOUNTS.md`](ACCOUNTS.md) for the account features.
