# BRIEFING — 2026-11-05T05:00:00Z

## Mission
Orchestrate the implementation of Phase P2 of Admin and Settings Path Separation for the Claire repository.

## 🔒 My Identity
- Archetype: teamwork_orchestrator
- Roles: orchestrator, user_liaison, human_reporter, successor
- Working directory: /home/fow/Projects/claire-bible/.agents/teamwork/orchestrator
- Original parent: Sentinel
- Original parent conversation ID: 2e1cff97-2a1a-41f9-8dca-1b568e47efa6

## 🔒 My Workflow
- **Pattern**: Project Pattern (Dual Track: Implementation Track + E2E Testing Track)
- **Scope document**: /home/fow/Projects/claire-bible/PROJECT.md
1. **Decompose**: Survey full scope with 3 exploratory subagents, then construct PROJECT.md feature inventory and milestones.
2. **Dispatch & Execute**:
   - Implementation Track: Sequential milestones (Lane P2-a shared modules, Lane P2-b admin console screens, final test verification).
   - E2E Testing Track: Requirements-based verification of console routes and UI components.
3. **On failure**:
   - Retry: Nudge stuck agent.
   - Replace: Spawn fresh agent.
   - Skip: Proceed without (only if non-critical).
   - Redistribute: Split work.
   - Redesign: Re-partition milestones.
4. **Succession**: At 16 spawns, write handoff.md and spawn successor.
- **Work items**:
  1. Survey and Scope Mapping [in-progress]
  2. Lane P2-a: Shared Management Controllers & Drawer Integration [pending]
  3. Lane P2-b: Unified Admin Console Screens [pending]
  4. Final E2E and Regression Verification [pending]
- **Current phase**: 0 (Survey)
- **Current focus**: Survey and Scope Mapping

## 🔒 Key Constraints
- Use ASD-STE100 for all tasks, except when you answer users.
- Never write, modify, or create source code files directly.
- Never run build/test commands directly; require workers to do so.
- Never hard-wrap code lines in Python, JavaScript, HTML, or CSS files.
- Isolate test state in tmp_path; clear CLAIRE_* environment variables.
- Write unit tests strictly for module interfaces, I/O operations, and calls to external libraries.
- Respect hotspot file boundaries: src/claire/static/js/app.js is edited only in Lane P2-a.
- Record completion notes in .agent-work/admin-settings/P2.md.
- Never reuse a subagent after it has delivered its handoff — always spawn fresh.

## Current Parent
- Conversation ID: 2e1cff97-2a1a-41f9-8dca-1b568e47efa6
- Updated: not yet

## Key Decisions Made
- Use Project Pattern with Survey phase to catalog all requirements from P1 handoff, design spec, and codebase.

## Team Roster
| Agent | Type | Work Item | Status | Conv ID |
|-------|------|-----------|--------|---------|
| spec_miner_p2_1 | teamwork_spec_miner | Survey Spec & Architecture | completed | 89fe4282-ea5f-408b-8821-0b667f0e73a2 |
| explorer_p2a_1 | teamwork_explorer | Survey Lane P2-a Codebase | completed | 1cc769b1-5708-45ef-a31e-5c5525088c79 |
| explorer_p2b_1 | teamwork_explorer | Survey Lane P2-b Codebase | completed | a0b617d1-94ee-4b55-94ac-0edced413cfd |
| worker_m1_1 | teamwork_worker | Implement Milestone 1 (Lane P2-a) | in-progress | eb8d50eb-0092-4753-a10c-7575d3cbd419 |

## Succession Status
- Succession required: no
- Spawn count: 4 / 16
- Pending subagents: eb8d50eb-0092-4753-a10c-7575d3cbd419
- Predecessor: none
- Successor: not yet spawned

## Active Timers
- Heartbeat cron: 6b12e74a-9ca9-44e7-9e6e-5bc3492e2404/task-27 (*/10 * * * *)
- Safety timer: 6b12e74a-9ca9-44e7-9e6e-5bc3492e2404/task-89 (600s)

## Artifact Index
- /home/fow/Projects/claire-bible/.agents/teamwork/ORIGINAL_REQUEST.md — Original User Request
- /home/fow/Projects/claire-bible/.agents/teamwork/orchestrator/DISPATCH.md — Incoming Dispatch Record
- /home/fow/Projects/claire-bible/docs/design/ADMIN_AND_SETTINGS_PATH_SEPARATION_DESIGN.md — Design Specification
- /home/fow/Projects/claire-bible/.agent-work/admin-settings/P1.md — Phase P1 Handoff
- /home/fow/Projects/claire-bible/PROJECT.md — Global Project Plan and Feature Inventory (to be generated)
