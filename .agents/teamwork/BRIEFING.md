# BRIEFING — 2026-11-05T05:03:00Z

## Mission
Orchestrate and supervise Phase P2 implementation (Shared Management Modules, Root '관리 및 설정' Integration (D2), and Full admin/ Console) via teamwork_orchestrator.

## 🔒 My Identity
- Archetype: sentinel
- Working directory: /home/fow/Projects/claire-bible/.agents/teamwork/
- Orchestrator: 6b12e74a-9ca9-44e7-9e6e-5bc3492e2404
- Victory Auditor: [to be spawned on victory claim]

## 🔒 Key Constraints
- No technical decisions — relay only
- Victory Audit is MANDATORY before reporting completion
- Never hard-wrap code lines in Python, JavaScript, HTML, or CSS files
- Isolate all test state in tmp_path and clear CLAIRE_* environment variables in test fixtures; never write to repository root data/ and never touch production hosts
- Write unit tests strictly for module interfaces, I/O operations, and calls to external libraries
- Respect hotspot file boundaries: src/claire/static/js/app.js is edited only in Lane P2-a
- Record completion notes in .agent-work/admin-settings/P2.md

## User Context
- **Last user request**: Approved task prompt received for Phase P2
- **Pending clarifications**: none
- **Delivered results**: [none]

## Project Status
- **Phase**: in progress (Phase 0: Survey and Scope Mapping active)
- **Active Subagents in Swarm**: spec_miner_p2_1, explorer_p2a_1, explorer_p2b_1
- **Cron 1 (Reporting)**: task-29 (*/8 * * * *)
- **Cron 2 (Liveness)**: task-31 (*/10 * * * *)

## Victory Audit Status
- **Triggered**: no
- **Verdict**: pending
- **Retry count**: 0

## Artifact Index
- /home/fow/Projects/claire-bible/.agents/teamwork/ORIGINAL_REQUEST.md — Authoritative user request
- /home/fow/Projects/claire-bible/docs/design/ADMIN_AND_SETTINGS_PATH_SEPARATION_DESIGN.md — Design specification
- /home/fow/Projects/claire-bible/.agent-work/admin-settings/P1.md — Phase P1 handoff
- /home/fow/Projects/claire-bible/.agents/teamwork/orchestrator/ — Orchestrator workspace
