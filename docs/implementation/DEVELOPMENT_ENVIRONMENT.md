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
| Contributor toolbox (`./cb-manuscript dev toolbox`), Docker E2E, single-server stack, and `devnet` | Docker Engine 24+ with Compose v2.24.4+, Git, Bash, Python 3.11+ (stdlib only) | No `uv`, Deno, Node.js, Playwright, LLM API key, or administrator rights are required (`CLAIRE_PROVIDER=mock`) |

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
2. **Targeted tests, type check, and `.env` isolation (`CLAIRE_IGNORE_DOTENV`).**
   ```bash
   uv run pytest tests/test_devnet.py
   uv run ty check
   ```
   `tests/conftest.py` sets `CLAIRE_IGNORE_DOTENV=1` so `Settings` (`src/claire/core/config.py`) ignores the checkout `.env` file unless a test overrides `_env_file` explicitly (F9). The standalone toolbox image and `scripts/e2e.sh` Docker back end also set `CLAIRE_IGNORE_DOTENV=1`. `tests/test_toolbox_and_e2e_runner.py` verifies this isolation and ensures tracked Git files contain no personal home paths outside its allow list.
3. **Sidecar tests.**
   ```bash
   (cd sidecar/fedify && deno task test)
   ```
4. **End-to-end Playwright tests (`CLAIRE_E2E_RUNNER=systemd|docker`).**
   ```bash
   CLAIRE_E2E_RUNNER=systemd bash scripts/e2e.sh run   # Linux with systemd user manager
   CLAIRE_E2E_RUNNER=docker bash scripts/e2e.sh run    # Any Docker host (runs inside claire-toolbox:dev)
   ```
   `CLAIRE_E2E_RUNNER` is required and has no fallback default: omitting it or passing another value exits with an error. Both back ends support `run`, `start`, `wait`, `status`, `watch`, `tmux`, and `stop`, enforce `2400m` memory and `CLAIRE_E2E_RUN_TIMEOUT` (default `1200` s) / `CLAIRE_E2E_SPEC_TIMEOUT` (default `600` s) limits, write run files to `data/e2e-runs/<id>/`, and report states `running`, `passed`, `failed`, `stopped`, `timeout`, `oom-kill`, or `lost`. The `docker` back end runs detached in `claire-toolbox:dev` (`--pids-limit 512`, `CLAIRE_IGNORE_DOTENV=1`, port `8766` inside the container so a host dev stack on `8766` does not collide), and host commands in `scripts/e2e.sh` use Bash 3.2/POSIX-compatible constructs. See [`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md) §3 for details.
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

## 5. Standalone Toolbox and Two-Server Development Network (`devnet`)

### Standalone Contributor Toolbox (`./cb-manuscript dev toolbox`)

Use `./cb-manuscript dev toolbox` (`ops/toolbox.py`) to run checks and tests inside the multi-arch (`linux/amd64` and `linux/arm64`) `claire-toolbox:dev` image (`ops/dev/toolbox.Dockerfile`) without host `uv`, Deno, Node.js, or Playwright, and without reading `.env` or `.env.dev`:

```bash
./cb-manuscript dev toolbox bash scripts/ci.sh               # Run the full CI gate in the toolbox
./cb-manuscript dev toolbox pytest tests/test_devnet.py      # Run targeted pytest
./cb-manuscript dev toolbox deno task test                   # Runs in /src/sidecar/fedify automatically
./cb-manuscript dev toolbox --build pytest -q                # Rebuild claire-toolbox:dev before running
./cb-manuscript dev toolbox --network <name> pytest …        # Attach the toolbox to a Docker network (for DrFed/Garage)
```

`ops/dev/toolbox.Dockerfile` pre-installs Python 3.15.2 with all `uv.lock` extras (`/opt/claire-venv`), Deno 2.9.8 with cached sidecar dependencies (`/opt/deno-cache`), Node.js + Playwright + Chromium (`/opt/claire-e2e/node_modules`, `/opt/ms-playwright`), Docker CLI + Compose plugin, Ruby/Asciidoctor, and `ffmpeg`. `ops/toolbox.py` mounts the repository at `/src`, runs as the host `uid:gid` with `--memory 4g --memory-swap 4g`, sets `TMPDIR=/tmp`, `HOME=/tmp`, and `CLAIRE_IGNORE_DOTENV=1`, and never touches the checkout `.venv` or `node_modules` under `e2e/`.

### Two-Server Development Network (`devnet`)

Use `./cb-manuscript dev devnet` (`ops/devnet.py`) to run two Claire servers (`https://a.claire.test` and `https://b.claire.test`) with real Fedify sidecars, real TLS from a private certificate authority, and private DNS:

```bash
./cb-manuscript dev devnet up --servers a,b   # Build images + devnet toolbox, start core and servers, bootstrap admins
./cb-manuscript dev devnet check              # Run claire server check --federation on each running server
./cb-manuscript dev devnet test               # Run pytest -m devnet inside the devnet toolbox container
./cb-manuscript dev devnet fault <profile>    # Apply a gateway fault profile (normal, gf8, gf9, gf10, gf14, gf15, gf16, gf17) and reload nginx
./cb-manuscript dev devnet logs <a>|<b> [svc] # Show the last 200 log lines of one server or one service
./cb-manuscript dev devnet info               # Print URLs, access proxy (127.0.0.1:18443), CA path, and browser flags
./cb-manuscript dev devnet down               # Remove containers, networks, volumes, and .dev/devnet/
```

### Devnet Toolbox Container

`ops/dev/devnet/toolbox.Dockerfile` builds `claire-devnet-toolbox:devnet` from `claire-bible:devnet` with the `dev` extra from `uv.lock`. `./cb-manuscript dev devnet up` runs `ops/dev/devnet/bootstrap.py` inside this toolbox container on `claire-devnet-edge` to finish initial `/setup`, and `./cb-manuscript dev devnet test` runs `pytest -m devnet` inside it against the running network (`tests/test_devnet.py`).

### Host Browser Access Proxy

The `access` service (`ops/dev/devnet/access_proxy.py`) exposes an HTTP `CONNECT` proxy on `127.0.0.1:18443` (`CB_DEVNET_ACCESS_PORT`). It permits only `CONNECT <host>:443` for `claire.test` and `*.claire.test`, and rejects all other methods, hosts, and ports with `405` or `403`. Run `./cb-manuscript dev devnet info` to print the exact Chromium and Firefox flags and the CA certificate path (`.dev/devnet/tls/ca/ca.crt`) to trust in a separate browser profile.

See [`DEVELOPMENT_NETWORK.md`](DEVELOPMENT_NETWORK.md) for the network topology, fault profiles, rules, and integration test suite.

---

## 6. Remaining Owner Verification

DE1 (`ops/dev/toolbox.Dockerfile`, `ops/toolbox.py`, `CLAIRE_IGNORE_DOTENV=1`), DE3 (`CLAIRE_E2E_RUNNER=systemd|docker`), and DE5 (`ops/dev/devnet/faults.py`, `devnet fault`, `devnet logs`) are implemented. The remaining items in [`CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md`](../design/CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md) require owner hardware or repository administration:

- Physical host verification on macOS (Apple Silicon and Intel), Windows 11 with WSL2, and `linux/arm64`.
- `DQ4` Option B: remove `scratch/` from git history before making the repository public.
