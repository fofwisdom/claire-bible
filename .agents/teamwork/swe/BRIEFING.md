# BRIEFING — 2026-11-14T14:41:00Z

## Mission
Implement unit HW-6 (Graph view insight lists in context panel without selection, and entity detail navigation from each view) as a production feature for Claire Bible.

## 🔒 My Identity
- Archetype: orchestrator
- Roles: orchestrator, user_liaison, human_reporter, successor
- Working directory: /home/fow/Projects/claire-bible
- State folder: /home/fow/Projects/claire-bible/.agents/teamwork/swe/
- Original parent: parent
- Original parent conversation ID: 0a9b8163-58b5-4c69-b4d8-6b123fa40d1d

## 🔒 My Workflow
- **Pattern**: SWE Light
- **Scope document**: /home/fow/Projects/claire-bible/.agents/teamwork/ORIGINAL_REQUEST.md
1. **Decompose**: SWE Light does not decompose. Sequential refinement: teamwork_implementer -> teamwork_reviewer (min 3 rounds) -> teamwork_victory_auditor.
2. **Dispatch & Execute** (pick ONE):
   - **Direct (iteration loop)**: Dispatch implementer first, then reviewer rounds iteratively. Maintain open-issues ledger. Verify with pytest and playwright. Audit before victory.
3. **On failure** (in this order):
   - Retry: nudge stuck agent or re-send task
   - Replace: spawn fresh agent with partial progress
   - Skip: proceed without (only if non-critical)
   - Redistribute: split stuck agent's remaining work
   - Redesign: re-partition decomposition
   - Escalate: report to parent (sub-orchestrators only, last resort)
4. **Succession**: At 16 spawns, write handoff.md, spawn successor.
- **Work items**:
  1. HW-6 Implementation [in-progress]
- **Current phase**: 2
- **Current focus**: HW-6 Implementation

## 🔒 Key Constraints
- Use ASD-STE100 for all code comments, documentation, and test descriptions.
- Write Git commit messages in English only.
- Do not hard-wrap code lines.
- Write at least one Playwright E2E test in e2e/workspace.spec.js.
- Never write or edit code directly; delegate implementation and review to workers.
- Never reuse a subagent after it has delivered its handoff — always spawn fresh.

## Current Parent
- Conversation ID: 0a9b8163-58b5-4c69-b4d8-6b123fa40d1d
- Updated: not yet

## Key Decisions Made
- Starting SWE Light iteration loop with teamwork_implementer.

## Team Roster
| Agent | Type | Work Item | Status | Conv ID |
|---|---|---|---|---|
| teamwork_implementer_r1 | teamwork_implementer | HW-6 Implementation | completed | e4e737e2-5a52-4174-aa2d-e8ac93e5e96e |
| teamwork_reviewer_r1 | teamwork_reviewer | HW-6 Review Round 1 | completed | 34dc0753-3d6f-4b1f-9f09-688f212c2068 |
| teamwork_reviewer_r2 | teamwork_reviewer | HW-6 Review Round 2 | completed | e1a56ae8-2df8-4f06-9a33-c2b745374c3d |
| teamwork_reviewer_r3 | teamwork_reviewer | HW-6 Review Round 3 | in-progress | 897263bd-3cf5-4f27-9523-323786949f34 |

## Succession Status
- Succession required: no
- Spawn count: 4 / 16
- Pending subagents: 897263bd-3cf5-4f27-9523-323786949f34
- Predecessor: none
- Successor spawned: not yet spawned
- Successor generation: none
- Successor state folder: none

## Active Timers
- Heartbeat cron: 1244039c-ee03-4559-bfce-ffe2686c34ca/task-11
- Safety timer: none
- On succession: kill all timers before spawning successor
- On context truncation: handle timers as step 4 of the State Recovery section of your prompt says

## Artifact Index
- /home/fow/Projects/claire-bible/.agents/teamwork/ORIGINAL_REQUEST.md — Original user request
- /home/fow/Projects/claire-bible/.agents/teamwork/swe/DISPATCH.md — Parent dispatch log
