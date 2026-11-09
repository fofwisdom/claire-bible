# BRIEFING — 2026-11-05T05:14:00Z

## Mission
Implement Milestone 1 (Lane P2-a): extract manage_panels.js, update app.js, and update index.html.

## 🔒 My Identity
- Archetype: teamwork_worker
- Roles: implementer, qa, specialist
- Working directory: /home/fow/Projects/claire-bible/.agents/teamwork/worker_m1_1
- Original parent: 6b12e74a-9ca9-44e7-9e6e-5bc3492e2404
- Milestone: Milestone 1 (Lane P2-a)

## 🔒 Key Constraints
- Use ASD-STE100 English for all tasks and commit messages.
- Do not hard-wrap code lines.
- Only edit src/claire/static/js/manage_panels.js, src/claire/static/js/app.js, and src/claire/templates/index.html.
- Do not edit src/claire/templates/console_shell.html or backend Python files.
- Do not cheat or make dummy implementations.
- Write unit tests only for module interfaces, I/O, and external calls. Minimum one E2E test for new features.

## Current Parent
- Conversation ID: 6b12e74a-9ca9-44e7-9e6e-5bc3492e2404
- Updated: not yet

## Task Summary
- **What to build**: Extract renderProviderManager, renderLimitManager, renderThemeManager, renderTokenManager into manage_panels.js. Add IS_ADMIN tracking and update setAccessScope in app.js. Add console link button and script tag in index.html.
- **Success criteria**: Reusable panel controllers work in both index.html and console_shell.html. Access scope accurately hides/shows buttons. Tests pass without errors.
- **Interface contracts**: PROJECT.md and DISPATCH.md
- **Code layout**: src/claire/static/js/ and src/claire/templates/

## Key Decisions Made
- Use window.ClaireManagePanels and attach functions to window object for global availability.

## Artifact Index
- /home/fow/Projects/claire-bible/.agents/teamwork/worker_m1_1/DISPATCH.md — Task instructions
- /home/fow/Projects/claire-bible/.agents/teamwork/worker_m1_1/BRIEFING.md — Working memory
- /home/fow/Projects/claire-bible/.agents/teamwork/worker_m1_1/progress.md — Progress log

## Change Tracker
- **Files modified**: None yet
- **Build status**: Not run yet
- **Pending issues**: None

## Quality Status
- **Build/test result**: Not run yet
- **Lint status**: 0 violations
- **Tests added/modified**: TBD

## Loaded Skills
None
