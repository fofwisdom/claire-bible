# Survey Report: Requirement R4 and Requirement R6

Date: 2026-11-09
Agent: Survey Explorer 3
Working Directory: /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_explorer_survey_3

## 1. Executive Summary

This report gives the investigation results for Requirement R4 and Requirement R6.
Phase P2 of the Admin-Settings project is complete.
The current file `PROJECT.md` is outdated and shows Phase P2 as in progress.
The CI script `./scripts/ci.sh` failed during the baseline test run.
Two tests failed: one deterministic regex mismatch and one timing check.
This report catalogs the necessary updates for `PROJECT.md` and documents CI verification steps.

---

## 2. Requirement R4: PROJECT.md Status Update and Hygiene

### 2.1 Inspection of PROJECT.md
The file `PROJECT.md` at the project root currently contains:
- Title: `# Project: Admin and Settings Path Separation — Phase P2`
- Architecture: Describes Phase P2 modules and routes.
- Feature Inventory: 25 items for Phase P2.
- Milestones Table:
  - M1 (Lane P2-a: Shared Controllers & Drawer): Status `IN_PROGRESS`
  - M2 (Lane P2-b: Unified Admin Console Screens): Status `PLANNED`
  - M3 (Final Verification & Documentation): Status `PLANNED`
- Interface Contracts: Specifications for `manage_panels.js` and `console_shell.html`.
- Code Layout: File ownership for Phase P2.

### 2.2 Inspection of .agent-work/admin-settings/ and Task Logs
The directory `.agent-work/admin-settings/` contains five phase handoff notes:
- `P1.md`: Completed on 2026-11-05.
- `P2.md`: Completed on 2026-11-05 (0 test failures, verification gate passed).
- `P3.md`: Completed on 2026-11-05 (Consolidated `/settings/*` screens).
- `P4.md`: Completed on 2026-11-05 (Entry point redirects, removed old templates).
- `P5.md`: Completed on 2026-11-05 (Documentation sync across OpenAPI and markdown docs).

All 25 features listed in `PROJECT.md` are completely implemented in the codebase:
- `src/claire/static/js/manage_panels.js` exists and exports the required managers.
- `src/claire/static/js/app.js` tracks `IS_ADMIN` and controls drawer buttons.
- `src/claire/templates/console_shell.html` and `console_admin.js` serve all 8 admin screens.
- `GET /api/admin/mcp/telemetry` is implemented and verified.
- Privacy protections (S9, B1/N5, D5/K5) are implemented and covered by tests.

### 2.3 Uncompleted Items and Open Remnants
Phase P2 itself has zero uncompleted items.
However, subsequent phase notes (P3, P4, P5) and git commits recorded specific open items:
1. `profile_public` toggle:
   - P3 note recorded: "No API sets profile_public. It is shown read-only in network mode."
   - This item is now Requirement R1 in `ORIGINAL_REQUEST.md`.
2. Syndication prototype seed rows:
   - P3 and P5 notes recorded: "syndication feed/account/rule rows are still prototype seed data."
   - This item is now Requirement R5 in `ORIGINAL_REQUEST.md`.
3. Reserved usernames:
   - P5 note added `settings` to reserved usernames.
   - IdP and Fedify reserved usernames were not added.
   - This item is now Requirement R2 in `ORIGINAL_REQUEST.md`.
4. Token exchange account binding:
   - RFC 8693 token exchange permitted fallback to admin account ID 1.
   - This item is now Requirement R3 in `ORIGINAL_REQUEST.md`.
5. Status of `PROJECT.md`:
   - `PROJECT.md` was never updated after Phase P2 planning.
   - Milestones M1, M2, and M3 still show `IN_PROGRESS` or `PLANNED`.

### 2.4 Catalog of Necessary Updates for PROJECT.md
To bring `PROJECT.md` into accurate status and hygiene:
1. Mark Phase P2 Milestones (M1, M2, M3) as `COMPLETED`.
2. Update the document title to reflect the current milestone:
   - "Claire Bible Sealing and Cleanup: Account, IdP, and Fedify Integration"
3. Add a section that records the completion of Admin-Settings Phases P1 to P5.
4. Add the new Feature Inventory for Requirements R1 through R5:
   - Item 1: `profile_public` toggle support in `PATCH /api/me` and `/settings/account` (R1).
   - Item 2: Fedify and IdP reserved usernames synchronization in `src/claire/accounts/username_policy.py` (R2).
   - Item 3: RFC 8693 token exchange account binding integrity in `src/claire/api/oauth.py` (R3).
   - Item 4: `PROJECT.md` status update and hygiene (R4).
   - Item 5: Syndication prototype sealing and cleanup (R5).
5. Update the Milestones table with the current execution plan.

---

## 3. Requirement R6: Baseline Test Suite and CI Investigation

### 3.1 Inspection of ./scripts/ci.sh
The CI script `./scripts/ci.sh` executes four verification steps:
1. Step 1/4: Entrypoint syntax check:
   - `bash -n cb-manuscript deploy.sh scripts/ci.sh`
   - `python3 -m py_compile ops/cb_manuscript.py`
2. Step 2/4: Docker Compose model and volume specification integrity:
   - `docker compose --env-file .env.example -f docker-compose.yml config --quiet`
   - `docker compose --env-file .env.example --env-file .env.dev.example -f docker-compose.yml -f docker-compose.dev.yml config --quiet`
   - Volume checks: asserts `/host-bin:ro` and `/extra-certs:ro` exist; asserts Ubuntu host paths (`/etc/ssl/certs`, `/etc/localtime`) are not mounted.
3. Step 3/4: `uv.lock` consistency check:
   - `uv lock --check` verifies lockfile matches `pyproject.toml`.
4. Step 4/4: Full test suite:
   - `PYTHONPATH=. uv run --extra dev pytest -q`

### 3.2 Test Configuration and Execution Commands
- Configuration:
  - File: `pyproject.toml`
  - Setting: `[tool.pytest.ini_options]` with `testpaths = ["tests"]`, `asyncio_mode = "auto"`, `pythonpath = [".", "src"]`.
- Unit and Integration test command:
  - `PYTHONPATH=. uv run --extra dev pytest` (runs all tests in `tests/`).
- E2E test commands:
  - `PYTHONPATH=. uv run --extra dev pytest e2e/test_admin_and_settings_console_e2e.py e2e/test_syndication_e2e.py`
  - Browser E2E runner: `./scripts/e2e.sh` (executes Playwright inside Docker).

### 3.3 Baseline Test Execution Results
Execution command: `bash ./scripts/ci.sh`
- Steps 1/4, 2/4, and 3/4 passed with zero errors.
- Step 4/4 collected 2755 test items in `tests/`.
- Execution summary: `2 failed, 2753 passed, 14 warnings, 6 subtests passed in 322.15s (5m 22s)`.
- Skipped tests: `0 skipped`.

### 3.4 Detailed Analysis of Test Failures

#### Failure 1: Deterministic Regex Mismatch
- Test: `tests/test_security_hotfixes.py::test_escape_helpers_escape_single_quote[adoc_parser.js-re]`
- Result: `FAILED`
- Assertion Error:
  ```
  AssertionError: escape helper not found in adoc_parser.js
  assert None
  ```
- Root Cause:
  Commit `c4d0e1e` ("Fix AsciiDoc single quote corruption into highlight mark tags") changed the function signature in `src/claire/static/js/renderers/adoc_parser.js` from `function esc(s)` to `function esc(s, quote)`.
  The test pattern in `tests/test_security_hotfixes.py` line 291 expects:
  `r"function esc\(s\) \{.*?\n\}\n"`
  The pattern does not match the new parameter `quote`.
  This failure is 100% deterministic and is an existing defect on the main branch.

#### Failure 2: Flaky Timing Threshold Under Heavy Load
- Test: `tests/test_e2e_presentation_resilience.py::test_e2e_presentation_full_resilience_lifecycle`
- Result: `FAILED` (during full suite only)
- Assertion Error:
  ```
  AssertionError: Async generate must return immediately (<100ms), took 129.8872010083869ms
  assert 129.8872010083869 < 100
  ```
- Root Cause:
  Line 122 asserts that an asynchronous POST request returns in less than 100ms (`gen_time_ms < 100`).
  When running 2700+ tests sequentially over 5 minutes, CPU load caused this request to take 129.88ms.
  When executed in isolation, this test passes consistently (1.5s total test time, response time well under 100ms).

### 3.5 E2E Test Suite State
- Running `e2e/test_admin_and_settings_console_e2e.py` and `e2e/test_syndication_e2e.py`:
  - Result: 13 passed, 0 failed in 4.48s.
- Running `e2e/test_favicons_e2e.py`:
  - Result: Fails at `/oauth/authorize` with 400 because client ID `test-client-id` is not registered under OAuth 2.1 policy. Note: this file is not in `tests/` and is not executed by `ci.sh`.
