#!/usr/bin/env bash
# Run Playwright E2E tests in an isolated systemd user scope with capped memory so systemd-oomd cannot kill the parent agent session. Supports git worktrees without duplicate node_modules or .venv directories.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

resolve_main_root() {
  local common_dir
  common_dir="$(git -C "$REPO_ROOT" rev-parse --git-common-dir 2>/dev/null || true)"
  if [[ -n "$common_dir" ]]; then
    (cd "$REPO_ROOT" && cd "$common_dir/.." && pwd)
  else
    printf '%s\n' "$REPO_ROOT"
  fi
}

MAIN_ROOT="$(resolve_main_root)"

if [[ -d "$REPO_ROOT/e2e/node_modules/@playwright/test" ]]; then
  E2E_NODE_MODULES="$REPO_ROOT/e2e/node_modules"
elif [[ -d "$MAIN_ROOT/e2e/node_modules/@playwright/test" ]]; then
  E2E_NODE_MODULES="$MAIN_ROOT/e2e/node_modules"
else
  echo "e2e/node_modules/@playwright/test is missing. Run 'npm ci' inside e2e/." >&2
  exit 1
fi

PLAYWRIGHT_BIN="$E2E_NODE_MODULES/.bin/playwright"

if [[ -z "${CLAIRE_E2E_PYTHON:-}" ]]; then
  if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
    export CLAIRE_E2E_PYTHON="$REPO_ROOT/.venv/bin/python"
  elif [[ -x "$MAIN_ROOT/.venv/bin/python" ]]; then
    export CLAIRE_E2E_PYTHON="$MAIN_ROOT/.venv/bin/python"
  fi
fi

export NODE_PATH="$E2E_NODE_MODULES${NODE_PATH:+:$NODE_PATH}"
if [[ " ${NODE_OPTIONS:-} " != *" --max-old-space-size="* ]]; then
  export NODE_OPTIONS="--max-old-space-size=512${NODE_OPTIONS:+ $NODE_OPTIONS}"
fi

MEM_HIGH="${CLAIRE_E2E_MEM_HIGH:-1800M}"
MEM_MAX="${CLAIRE_E2E_MEM_MAX:-2400M}"

USE_SCOPE=0
if [[ "${CLAIRE_E2E_NO_SCOPE:-0}" != "1" ]] && command -v systemd-run >/dev/null 2>&1; then
  if systemd-run --user --scope --quiet true 2>/dev/null; then
    USE_SCOPE=1
  else
    echo "Warning: systemd-run --user --scope is unavailable; running in the current cgroup." >&2
  fi
fi

run_playwright() {
  local args=("$@" "--config=$REPO_ROOT/e2e/playwright.config.js")
  if [[ "$USE_SCOPE" -eq 1 ]]; then
    systemd-run --user --scope -p "MemoryHigh=$MEM_HIGH" -p "MemoryMax=$MEM_MAX" -p ManagedOOMMemoryPressure=kill -- \
      "$PLAYWRIGHT_BIN" test "${args[@]}"
  else
    "$PLAYWRIGHT_BIN" test "${args[@]}"
  fi
}

if [[ "$#" -gt 0 ]]; then
  run_playwright "$@"
  exit 0
fi

# Run each spec file in its own Playwright process so Chromium memory is released and single-use seed tokens do not collide across files.
failed_specs=()
for spec in "$REPO_ROOT"/e2e/*.spec.js; do
  spec_name="$(basename "$spec")"
  echo "=== Running $spec_name ==="
  if ! run_playwright "$spec_name"; then
    failed_specs+=("$spec_name")
  fi
done

if [[ "${#failed_specs[@]}" -gt 0 ]]; then
  echo "Failed specs: ${failed_specs[*]}" >&2
  exit 1
fi
