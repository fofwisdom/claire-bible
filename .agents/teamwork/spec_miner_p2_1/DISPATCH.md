# Dispatch: Spec Miner P2-1

## Identity
- Role: Specification Investigator (teamwork_spec_miner)
- Working Directory: /home/fow/Projects/claire-bible/.agents/teamwork/spec_miner_p2_1

## Mandatory Instruction
Read /home/fow/Projects/claire-bible/.agents/teamwork/ORIGINAL_REQUEST.md completely before you start work.
Follow user rules: Use ASD-STE100 English for all tasks and reports. Do not modify or write source code files.

## Investigation Scope
Investigate authoritative design documents and handoffs:
1. `docs/design/ADMIN_AND_SETTINGS_PATH_SEPARATION_DESIGN.md` (specifically Section 4 and Phase P2)
2. `.agent-work/admin-settings/P1.md` (Phase P1 handoff note and decisions)
3. `docs/PRINCIPLES.md` (architectural principles)

## Expected Deliverables
Write `handoff.md` in your working directory (/home/fow/Projects/claire-bible/.agents/teamwork/spec_miner_p2_1/handoff.md) containing:
1. Complete inventory of Phase P2 requirements (Lane P2-a and Lane P2-b).
2. Detailed interface contracts for `manage_panels.js` helpers (`renderProviderManager`, `renderLimitManager`, `renderThemeManager`, `renderTokenManager`).
3. Contract requirements for all 8 `/admin/*` sections (`/admin/instance`, `/admin/accounts`, `/admin/idp`, `/admin/providers`, `/admin/limits`, `/admin/themes`, `/admin/tokens`, `/admin/mcp`).
4. Step-up reauth requirements, single-mode vs network-mode requirements, and telemetry/export requirements.
5. Verification criteria and relevant test files.
Send a completion message back when done.

## 2026-11-05T05:01:50Z
Read /home/fow/Projects/claire-bible/.agents/teamwork/ORIGINAL_REQUEST.md completely.
Your working directory is /home/fow/Projects/claire-bible/.agents/teamwork/spec_miner_p2_1.
Your task details are in /home/fow/Projects/claire-bible/.agents/teamwork/spec_miner_p2_1/DISPATCH.md.
Extract all specifications and requirements for Phase P2 (Lane P2-a and Lane P2-b) from docs/design/ADMIN_AND_SETTINGS_PATH_SEPARATION_DESIGN.md, .agent-work/admin-settings/P1.md, and docs/PRINCIPLES.md.
Follow user rules: Use ASD-STE100 English. Do not write or edit source code files.
Write handoff.md to your working directory and notify the parent via send_message when complete.
