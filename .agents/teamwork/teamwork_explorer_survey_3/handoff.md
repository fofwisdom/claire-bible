# Handoff Report: Baseline Test Suite and PROJECT.md Status Survey

Date: 2026-11-09
Author: Survey Explorer 3
Recipient: Orchestrator (466a086f-7210-4e3e-9220-ce86cf072b6c)

## 1. Observation

1. **`PROJECT.md` State**:
   - File path: `/home/fow/Projects/claire-bible/PROJECT.md`
   - Header (line 1): `# Project: Admin and Settings Path Separation — Phase P2`
   - Milestones table (lines 56–60):
     - M1: `IN_PROGRESS`
     - M2: `PLANNED`
     - M3: `PLANNED`
   - Last modified in commit `2a04ae8d328ee70b976a22bae73c476c37bb1403` ("Admin And Settings Path Design: P2").

2. **Phase Completion Records**:
   - `.agent-work/admin-settings/P2.md` lines 1–6:
     - Date: 2026-11-05, Phase: `P2`, Status: `Complete (0 test failures)`.
   - `.agent-work/admin-settings/P3.md`, `P4.md`, `P5.md` exist and confirm completion of all 5 phases.
   - P3.md line 26 records open item: "No API sets `profile_public`. It is shown read-only in network mode."
   - P3.md line 29 records open item: "Feed/account/rule rows are still the prototype seed data, as on the legacy page."

3. **CI Script Structure**:
   - File path: `/home/fow/Projects/claire-bible/scripts/ci.sh`
   - Line 16: `bash -n cb-manuscript deploy.sh scripts/ci.sh`
   - Line 17: `python3 -m py_compile ops/cb_manuscript.py`
   - Lines 20–24: `docker compose ... config --quiet` for `.env.example` and `.env.dev.example`
   - Lines 27–34: Volume specification assertions for `/host-bin:ro` and `/extra-certs:ro`
   - Line 37: `uv lock --check`
   - Line 40: `PYTHONPATH=. uv run --extra dev pytest -q`

4. **Baseline CI Execution**:
   - Command: `bash ./scripts/ci.sh`
   - Exit code: `1` (failed).
   - Test results: `2 failed, 2753 passed, 14 warnings, 6 subtests passed in 322.15s`.
   - Skipped count: `0`.

5. **Verbatim Failure 1 (Deterministic)**:
   - Command: `PYTHONPATH=. uv run --extra dev pytest tests/test_security_hotfixes.py -k test_escape_helpers_escape_single_quote`
   - File: `tests/test_security_hotfixes.py:306`
   - Output:
     ```
     FAILED tests/test_security_hotfixes.py::test_escape_helpers_escape_single_quote[adoc_parser.js-re]
     AssertionError: escape helper not found in adoc_parser.js
     assert None
     ```
   - Target file: `src/claire/static/js/renderers/adoc_parser.js` line 14:
     `function esc(s, quote) {`
   - Test expectation at `tests/test_security_hotfixes.py` line 291:
     `pattern = 'function esc\\(s\\) \\{.*?\\n\\}\\n'`

6. **Verbatim Failure 2 (Timing Flake Under High CPU Load)**:
   - File: `tests/test_e2e_presentation_resilience.py:122`
   - Output:
     ```
     FAILED tests/test_e2e_presentation_resilience.py::test_e2e_presentation_full_resilience_lifecycle
     AssertionError: Async generate must return immediately (<100ms), took 129.8872010083869ms
     assert 129.8872010083869 < 100
     ```
   - Re-run in isolation: `PYTHONPATH=. uv run --extra dev pytest tests/test_e2e_presentation_resilience.py -v` PASSED in 1.51s.

7. **E2E Test Execution**:
   - Command: `PYTHONPATH=. uv run --extra dev pytest e2e/test_admin_and_settings_console_e2e.py e2e/test_syndication_e2e.py -v`
   - Output: `13 passed in 4.48s`.

---

## 2. Logic Chain

1. **Phase P2 Completion**:
   - Observation 1 shows `PROJECT.md` still has M1 `IN_PROGRESS` and M2/M3 `PLANNED`.
   - Observation 2 shows `.agent-work/admin-settings/P2.md` was completed on 2026-11-05 with zero test failures.
   - Therefore, the milestone status in `PROJECT.md` is outdated and does not match the actual repository state.

2. **Transition from P2 to Current Scope**:
   - Observation 2 notes open items from P3/P5: `profile_public` API absence, prototype seed data in syndication.
   - These open items match Requirements R1 and R5 in `ORIGINAL_REQUEST.md`.
   - Therefore, `PROJECT.md` must be updated to mark Phase P2 complete and define the new scope for Requirements R1 through R6.

3. **CI Baseline State**:
   - Observation 3 shows `scripts/ci.sh` executes 4 steps, ending with `pytest -q`.
   - Observation 4 shows `scripts/ci.sh` currently fails at step 4/4 with 2 test failures out of 2755 tests.
   - Observation 5 shows Failure 1 is caused by a regex in `tests/test_security_hotfixes.py` that did not accommodate the `quote` parameter added in commit `c4d0e1e`.
   - Observation 6 shows Failure 2 is a timing threshold check (100ms) that fluctuates when the 5-minute full suite executes.
   - Therefore, the baseline test suite has 1 deterministic bug introduced by the latest commit `c4d0e1e` and 1 load-sensitive timing test.

---

## 3. Caveats

1. Docker Playwright E2E tests (`./scripts/e2e.sh`) were inspected for structure, but not executed with live browsers because the task asks for the pytest test runner baseline.
2. The timing threshold in `tests/test_e2e_presentation_resilience.py` (100ms) may pass or fail depending on system background load.
3. `e2e/test_favicons_e2e.py` fails when invoked directly because client registration is now required by OAuth 2.1 policy; however, it is not part of `tests/` and is not run by `scripts/ci.sh`.

---

## 4. Conclusion

1. **Requirement R4 Action Items**:
   - Mark Milestones M1, M2, and M3 of Phase P2 as `COMPLETED`.
   - Retitle `PROJECT.md` to cover the current milestone: Account contract sealing, IdP/Fedify reserved usernames, token exchange integrity, and syndication prototype cleanup.
   - Add catalog of new feature items (Requirements R1 through R5) and update interface contracts.

2. **Requirement R6 Action Items**:
   - Baseline status: 2753 passed, 2 failed, 0 skipped.
   - Fix required for green CI: Update regex pattern in `tests/test_security_hotfixes.py` line 291 to accept optional parameters in `function esc(...)`.
   - The team must note the 100ms limit in `tests/test_e2e_presentation_resilience.py` when evaluating full CI runs under heavy load.

---

## 5. Verification Method

To independently verify these findings, run these commands:

1. Verify `PROJECT.md` status:
   `head -n 65 PROJECT.md`
   Notice lines 56–60 show `IN_PROGRESS` and `PLANNED`.

2. Verify Phase P2 record:
   `head -n 10 .agent-work/admin-settings/P2.md`
   Notice line 5 shows `Status: Complete (0 test failures)`.

3. Verify CI failure on `test_security_hotfixes.py`:
   `PYTHONPATH=. uv run --extra dev pytest tests/test_security_hotfixes.py -k test_escape_helpers_escape_single_quote`
   Observe verbatim `AssertionError: escape helper not found in adoc_parser.js`.

4. Verify E2E suite passes:
   `PYTHONPATH=. uv run --extra dev pytest e2e/test_admin_and_settings_console_e2e.py e2e/test_syndication_e2e.py`
   Observe `13 passed`.
