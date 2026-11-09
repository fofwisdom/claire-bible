# Dispatch: Explorer P2A-1

## Identity
- Role: Codebase Investigator (teamwork_explorer)
- Working Directory: /home/fow/Projects/claire-bible/.agents/teamwork/explorer_p2a_1

## Mandatory Instruction
Read /home/fow/Projects/claire-bible/.agents/teamwork/ORIGINAL_REQUEST.md completely before you start work.
Follow user rules: Use ASD-STE100 English for all tasks and reports. Do not modify or write source code files.

## Investigation Scope (Lane P2-a: Shared Management Controllers & Root Drawer)
Investigate existing implementation in:
1. `src/claire/static/js/app.js`:
   - How `setAccessScope`, `#providermanagebtn`, `#limitmanagebtn`, `#thememanagebtn`, `#tokenmanagebtn` are handled.
   - How `GET /whoami` is currently processed and where `IS_ADMIN` should be tracked and reset.
   - Where the management panel rendering logic currently lives in `app.js` (for providers, limits, themes, tokens).
2. `src/claire/templates/index.html`:
   - How `#manage-section` is structured, existing buttons, drawer markup.
   - Where `#consolelinkbtn` needs to be inserted and its behavior based on `IS_ADMIN` and `canWrite()`.
3. Extraction plan for `src/claire/static/js/manage_panels.js`:
   - Identify exact functions, DOM dependencies, and API calls to move out of `app.js` into reusable functions (`renderProviderManager`, `renderLimitManager`, `renderThemeManager`, `renderTokenManager`).
   - Identify how both `index.html` and `console_shell.html` load `manage_panels.js`.

## Expected Deliverables
Write `handoff.md` in your working directory (/home/fow/Projects/claire-bible/.agents/teamwork/explorer_p2a_1/handoff.md) containing:
1. Exact lines and code structures in `app.js` and `index.html`.
2. Proposed design of `manage_panels.js` with parameters and options.
3. Analysis of any risks or regressions for right-drawer actions in `index.html`.
4. Hotspot boundary check (`app.js` must only be modified for Lane P2-a).
Send a completion message back when done.

## 2026-11-05T05:01:56Z
Read /home/fow/Projects/claire-bible/.agents/teamwork/ORIGINAL_REQUEST.md completely.
Your working directory is /home/fow/Projects/claire-bible/.agents/teamwork/explorer_p2a_1.
Your task details are in /home/fow/Projects/claire-bible/.agents/teamwork/explorer_p2a_1/DISPATCH.md.
Investigate codebase for Lane P2-a: extraction of manage_panels.js from src/claire/static/js/app.js, drawer integration in src/claire/templates/index.html, GET /whoami IS_ADMIN state, and #consolelinkbtn.
Follow user rules: Use ASD-STE100 English. Do not write or edit source code files.
Write handoff.md to your working directory and notify the parent via send_message when complete.
