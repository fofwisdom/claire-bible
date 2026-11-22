# AGENTS.md

## Documentation folders

An old design document can describe a state that is not true now. These principles help an agent find the current state quickly and understand it correctly. They give the intent, not a procedure: use your judgment for each case.

- `docs/design/` tells what is intended, and why.
  - Architecture that is not fully implemented.
  - Design principles of work that is implemented.
- `docs/implementation/` tells what exists now.
  - Architecture that is implemented.
  - Term definitions.
  - The command manual for agents that use the CLI.
- `docs/ideas/` keeps ideas that have no design. Do not implement from it; an idea enters `docs/design/` only after an owner decision.

When you read, use each folder for its purpose. When you write, keep each folder true to its purpose.

## Console UI

- Every `/account/*`, `/settings/*`, `/admin/*`, and `/moderation/*` page must render inside `src/claire/api/templates/console_shell.html`.
- Build server HTML with `src/claire/api/accounts_routes/console_ui.py`. Build client HTML with `ClaireConsoleUI` (`src/bible/static/js/console_ui.js`). Style console elements with `src/bible/static/css/console_ui.css`.
- Mount client-rendered panels inside `.console-host` (`console_host`) and render top-level `.console-subcard` elements directly without nested cards.
- Use native `input[type="checkbox"][switch]` toggles and `.pref-btn` / `.pref-btn-accent` / `.pref-btn-danger` buttons. Do not add ad-hoc card, switch, button, inline fake-card, or `var(--error)` styles.
- `tests/test_console_ui_contract.py` and `e2e/console_ui_consistency.spec.js` enforce these rules.
