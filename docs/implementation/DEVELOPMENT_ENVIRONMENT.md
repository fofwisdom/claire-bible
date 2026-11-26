# Development Environment

- **Date**: 2026-11-27
- **Design**: [`CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md`](../design/CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md)
- **Related Documents**: [`../../CONTRIBUTING.md`](../../CONTRIBUTING.md), [`COMMANDS.md`](COMMANDS.md), [`DEVELOPMENT_NETWORK.md`](DEVELOPMENT_NETWORK.md)

This document describes the development tools and commands that exist in the repository now.

---

## 1. Prerequisites

| Workflow | Required Tools | Notes |
| :--- | :--- | :--- |
| Local `uv` development and CI | `uv`, Git, Bash | `uv` installs Python 3.15.2 automatically when it is not on the host |
| Sidecar tests (optional locally) | Deno `2.9.8` | Runs `sidecar/fedify/` tests; skipped in `scripts/ci.sh` when `deno` is absent |
| Single-server Docker stack and `devnet` | Docker Engine 24+ with Compose v2.24.4+, Git, Bash, Python 3.11+ (stdlib only) | No `uv`, Deno, LLM API key, or administrator rights are required (`CLAIRE_PROVIDER=mock`) |

---

## 2. Local Development with `uv`

1. Synchronize the virtual environment with the extras of the container image:
   ```bash
   uv sync --extra dev --extra stealth --extra audio --extra ko
   ```
   Omitting `stealth`, `audio`, or `ko` causes `ModuleNotFoundError` in tests that require `scrapling`, `yt-dlp`, or `kiwipiepy`.
2. Create `.env` and `.env.dev` if they do not exist:
   ```bash
   ./cb-manuscript dev init
   ```
3. Start the API server against `.dev/data/claire.db`:
   ```bash
   CLAIRE_INJECT_HOST=127.0.0.1 CLAIRE_INJECT_PORT=8766 CLAIRE_DB_PATH=.dev/data/claire.db \
     uv run --env-file .env.dev claire serve-api
   ```
   The server listens on `http://127.0.0.1:8766/`. See [`COMMANDS.md`](COMMANDS.md) §1.1 for role host ports and configuration precedence.

---

## 3. Checks, Tests, and CI

1. **Full CI gate (`scripts/ci.sh`).** Run all five checks before you push:
   ```bash
   bash ./scripts/ci.sh
   ```
   `scripts/ci.sh` checks: (1) Bash and Python 3.15.2 syntax, (2) Compose configuration and sidecar mount rules in `docker-compose.yml` and `docker-compose.dev.yml`, (3) `deno task test` in `sidecar/fedify/` when `deno` is installed, (4) `uv lock --check` and `uv run ty check`, and (5) `pytest`.
2. **Targeted tests and type check.**
   ```bash
   uv run pytest tests/test_devnet.py
   uv run ty check
   ```
3. **Sidecar tests.**
   ```bash
   (cd sidecar/fedify && deno task test)
   ```
4. **End-to-end Playwright tests (Linux with systemd user manager).**
   ```bash
   bash scripts/e2e.sh run
   ```
   See [`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md) §3 for runner options and memory limits.
5. **Hosted GitHub Actions CI.** `.github/workflows/ci.yml` runs `bash ./scripts/ci.sh` (with `uv` and Deno `2.9.8`) on every `pull_request` and `push` to `main`. It also supports `workflow_dispatch` with `run_devnet=true` to run the development network tests.

---

## 4. Single-Server Docker Development Stack

Use `./cb-manuscript dev` to verify container builds, entrypoints, and Compose wiring on `http://127.0.0.1:8766/`:

```bash
./cb-manuscript dev up             # Build images and start the stack
./cb-manuscript dev health         # Check API liveness
./cb-manuscript dev logs -f api    # Follow API logs
./cb-manuscript dev down           # Stop and remove containers
```

Do not run the local `uv` server and `./cb-manuscript dev up` at the same time because both use `.dev/data` and port `8766`.

---

## 5. Two-Server Development Network (`devnet`) and Toolbox

Use `./cb-manuscript dev devnet` (`ops/devnet.py`) to run two Claire servers (`https://a.claire.test` and `https://b.claire.test`) with real Fedify sidecars, real TLS from a private certificate authority, and private DNS:

```bash
./cb-manuscript dev devnet up --servers a,b   # Build images + toolbox, start core and servers, bootstrap admins
./cb-manuscript dev devnet check              # Run claire server check --federation on each running server
./cb-manuscript dev devnet test               # Run pytest -m devnet inside the toolbox container
./cb-manuscript dev devnet info               # Print URLs, access proxy (127.0.0.1:18443), CA path, and browser flags
./cb-manuscript dev devnet down               # Remove containers, networks, volumes, and .dev/devnet/
```

### Toolbox Container

`ops/dev/devnet/toolbox.Dockerfile` builds `claire-devnet-toolbox:devnet` from `claire-bible:devnet` with the `dev` extra from `uv.lock`. `./cb-manuscript dev devnet up` runs `ops/dev/devnet/bootstrap.py` inside the toolbox container on `claire-devnet-edge` to finish initial `/setup`, and `./cb-manuscript dev devnet test` runs `pytest -m devnet` inside the same toolbox image against the running network (`tests/test_devnet.py`).

### Host Browser Access Proxy

The `access` service (`ops/dev/devnet/access_proxy.py`) exposes an HTTP `CONNECT` proxy on `127.0.0.1:18443` (`CB_DEVNET_ACCESS_PORT`). It permits only `CONNECT <host>:443` for `claire.test` and `*.claire.test`, and rejects all other methods, hosts, and ports with `405` or `403`. Run `./cb-manuscript dev devnet info` to print the exact Chromium and Firefox flags and the CA certificate path (`.dev/devnet/tls/ca/ca.crt`) to trust in a separate browser profile.

See [`DEVELOPMENT_NETWORK.md`](DEVELOPMENT_NETWORK.md) for the network topology, rules, and integration test suite.

---

## 6. Planned Items Not Yet Implemented

The following items in [`CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md`](../design/CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md) are not implemented yet:

- Standalone `./cb-manuscript dev toolbox <command>` runner (design phase DE1).
- Docker back end `CLAIRE_E2E_RUNNER=docker` in `scripts/e2e.sh` (design phase DE3).
- Gateway fault profiles for unit KF6 testing (design phase DE5).
