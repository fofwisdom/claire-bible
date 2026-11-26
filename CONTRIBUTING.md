# Contributing to Claire Bible

Thank you for contributing to Claire Bible.

## 1. Getting Started

Read [`docs/implementation/DEVELOPMENT_ENVIRONMENT.md`](docs/implementation/DEVELOPMENT_ENVIRONMENT.md) to set up your local environment, run tests, and start the development network. Read [`docs/README.md`](docs/README.md) for the full documentation map.

---

## 2. License and Developer Certificate of Origin (DCO)

1. **MIT License.** All contributions to this repository are licensed under the MIT License in [`LICENSE.md`](LICENSE.md).
2. **DCO Sign-off Required.** Every commit must include a `Signed-off-by` line that certifies the Developer Certificate of Origin (version 1.1). By adding this line, you certify that you created the contribution or have the legal right to submit it under the MIT License.

Add the sign-off line automatically with `git commit -s`:

```text
Signed-off-by: Your Name <your.email@example.com>
```

---

## 3. Contributor Rules

Follow these repository rules for every change:

1. **Documentation folders.** Follow [`AGENTS.md`](AGENTS.md):
   - `docs/design/` records design intent, open questions, and architectural rationale.
   - `docs/implementation/` records only what is implemented now.
   - `docs/ideas/` holds unapproved ideas; do not implement features from `docs/ideas/`.
2. **Agents and Console UI.** As stated in [`AGENTS.md`](AGENTS.md), an agent must not give instructions to another agent. All console pages under `/account/*`, `/settings/*`, `/admin/*`, `/moderation/*`, `/laboratory/*`, `/support/bundles`, and `/support/pipeline` must follow the `AGENTS.md` Console UI contract.
3. **Language.** Write all new documents, code comments, docstrings, and CLI output in ASD-STE100 simplified technical English. CLI output is not localized ([`docs/design/I18N_DESIGN.md`](docs/design/I18N_DESIGN.md) ID6). Write Git commit messages in English.
4. **UI text.** For every new web UI or Telegram bot text, add catalog keys in both `src/claire/i18n/locales/en.json` and `src/claire/i18n/locales/ko.json` from the start (`I18N_DESIGN.md` ID5).
5. **No fallback defaults.** A missing or unknown input selects nothing. Required parameters or path arguments must return HTTP 400 when absent.
6. **Tests.** Write unit tests only for interfaces between modules, input/output (I/O) operations, and calls to external libraries. Write at least one end-to-end (E2E) test when you add a new feature. Run Playwright E2E tests only through `scripts/e2e.sh`.
7. **Terms.** Call a Claire installation a "server", not an "instance". Federation is open exchange; relays and server partnerships are ideas only.
8. **Clean room.** Follow the clean-room rule in [`docs/design/KNOWLEDGE_FEDERATION_DESIGN.md`](docs/design/KNOWLEDGE_FEDERATION_DESIGN.md) Section 4.2. Do not copy code, templates, schemas, tests, or documentation text from restricted projects. ForgeFed and CC0 FEPs are permitted.
9. **Dependencies.** Use the latest releases and record pins in `docs/implementation/` documents. After dependency changes, keep `uv.lock` synchronized (`uv lock --check`).
10. **Migrations.** Schema migrations run automatically at startup; operators do not edit the database manually.
11. **Code formatting and Python version.** Use Python 3.15 syntax (matching `Dockerfile`), except `ops/cb_manuscript.py` and `ops/devnet.py` must use only the Python standard library compatible with host Python 3.11 or later. Do not hard-wrap code lines. Keep existing unrelated comments intact.

---

## 4. Submitting a Change

1. Run the local CI gate before opening a pull request:
   ```bash
   bash ./scripts/ci.sh
   ```
2. Keep test fixtures free of personal data. Use invented names and RFC 2606 / RFC 6761 domains (`example.com`, `*.test`).
3. Do not commit secrets, `.env` files, or local scratch bundles (`/scratch/` is ignored in `.gitignore`).
4. Sign off every commit (`git commit -s`). The GitHub Actions workflow `.github/workflows/ci.yml` runs `scripts/ci.sh` on every pull request and push to `main`.
