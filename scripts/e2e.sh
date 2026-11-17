#!/usr/bin/env bash
# Run the Playwright E2E tests as a detached systemd user service. The run has a memory limit and a hard time limit, does not depend on the terminal or agent session that started it, and writes its state to files that each terminal can read. Supports git worktrees without duplicate node_modules or .venv directories.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SELF="$REPO_ROOT/scripts/e2e.sh"
cd "$REPO_ROOT"

PORT="${CLAIRE_E2E_PORT:-${CLAIRE_INJECT_PORT:-8766}}"
UNIT="claire-e2e-$PORT"
RUNS_DIR="$REPO_ROOT/data/e2e-runs"
KEEP_RUNS=10
MEM_MAX="${CLAIRE_E2E_MEM_MAX:-2400M}"
# Time limit of the full run and of one Playwright process, in seconds. A full run takes about 4 minutes.
RUN_TIMEOUT="${CLAIRE_E2E_RUN_TIMEOUT:-1200}"
SPEC_TIMEOUT="${CLAIRE_E2E_SPEC_TIMEOUT:-600}"

usage() {
  cat <<'EOF'
Usage: bash scripts/e2e.sh <command> [Playwright arguments]

  run [args]     Start a run and wait for the result (same as: start, then wait).
  start [args]   Start a detached run and return immediately.
  wait [seconds] Wait for the end of the run. With seconds, stop waiting after that time.
  status         Show the state of the latest run.
  watch          Follow the output of the latest run until it ends.
  tmux           Open a tmux session that follows the latest run.
  stop           Stop the run.

No arguments after run or start: each spec file runs in its own Playwright process.
With arguments (a spec file name, -g <pattern>, and so on): one Playwright process gets them.
A first argument that is not a command is an argument of run.

Exit codes of run and wait: 0 passed, 1 failed, 2 stopped by the time limit, the memory limit, or a stop command, 3 still running after the wait time.
EOF
}

resolve_main_root() {
  local common_dir
  common_dir="$(git -C "$REPO_ROOT" rev-parse --git-common-dir 2>/dev/null || true)"
  if [[ -n "$common_dir" ]]; then
    (cd "$REPO_ROOT" && cd "$common_dir/.." && pwd)
  else
    printf '%s\n' "$REPO_ROOT"
  fi
}

resolve_tools() {
  local main_root
  main_root="$(resolve_main_root)"
  if [[ -d "$REPO_ROOT/e2e/node_modules/@playwright/test" ]]; then
    E2E_NODE_MODULES="$REPO_ROOT/e2e/node_modules"
  elif [[ -d "$main_root/e2e/node_modules/@playwright/test" ]]; then
    E2E_NODE_MODULES="$main_root/e2e/node_modules"
  else
    echo "e2e/node_modules/@playwright/test is missing. Run 'npm ci' inside e2e/." >&2
    exit 1
  fi
  PLAYWRIGHT_BIN="$E2E_NODE_MODULES/.bin/playwright"
  if [[ -z "${CLAIRE_E2E_PYTHON:-}" ]]; then
    if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
      export CLAIRE_E2E_PYTHON="$REPO_ROOT/.venv/bin/python"
    elif [[ -x "$main_root/.venv/bin/python" ]]; then
      export CLAIRE_E2E_PYTHON="$main_root/.venv/bin/python"
    fi
  fi
  export NODE_PATH="$E2E_NODE_MODULES${NODE_PATH:+:$NODE_PATH}"
  if [[ " ${NODE_OPTIONS:-} " != *" --max-old-space-size="* ]]; then
    export NODE_OPTIONS="--max-old-space-size=512${NODE_OPTIONS:+ $NODE_OPTIONS}"
  fi
}

unit_active() {
  systemctl --user is-active --quiet "$UNIT.service" 2>/dev/null
}

latest_dir() {
  local dir
  dir="$(readlink -f "$RUNS_DIR/latest" 2>/dev/null || true)"
  if [[ -z "$dir" || ! -d "$dir" ]]; then
    echo "No E2E run is recorded in $RUNS_DIR." >&2
    exit 1
  fi
  printf '%s\n' "$dir"
}

meta_value() {
  sed -n "s/^$2=//p" "$1/meta"
}

# Print the state of a run: running, passed, failed, stopped, timeout, oom-kill, or lost.
run_state() {
  local dir="$1" result
  if [[ -f "$dir/result" ]]; then
    cat "$dir/result"
  elif unit_active; then
    echo running
  else
    result="$(systemctl --user show "$UNIT.service" -p Result --value 2>/dev/null || true)"
    case "$result" in
      timeout | oom-kill) echo "$result" ;;
      *) echo lost ;;
    esac
  fi
}

explain_state() {
  case "$1" in
    timeout) echo "The run reached the time limit of ${RUN_TIMEOUT} s and systemd stopped it." ;;
    oom-kill) echo "The run reached the memory limit of ${MEM_MAX} and the kernel stopped it." ;;
    lost) echo "The run stopped without a result. Look at: journalctl --user -u $UNIT.service" ;;
  esac
}

format_specs() {
  awk -F'\t' '{ printf "%-5s %-36s %4s s  %s\n", ($2 == 0 ? "PASS" : "FAIL"), $1, $3, $4 }'
}

# The internal command of the service: run Playwright and record the results.
worker() {
  local dir="$1" failed=0
  shift
  resolve_tools

  run_one() {
    local label="$1" rc=0 started summary
    shift
    echo "$label" >"$dir/current"
    echo "=== $label ===" >>"$dir/run.log"
    started="$(date +%s)"
    CLAIRE_E2E_SERVER_LOG="$dir/$label.server.log" timeout -k 10 "$SPEC_TIMEOUT" "$PLAYWRIGHT_BIN" test "$@" "--config=$REPO_ROOT/e2e/playwright.config.js" 2>&1 | tee -a "$dir/run.log" >"$dir/$label.log" || rc="${PIPESTATUS[0]}"
    summary="$(grep -aoE '[0-9]+ (passed|failed|flaky|skipped|interrupted|did not run)' "$dir/$label.log" | paste -sd, - || true)"
    if [[ "$rc" -eq 124 || "$rc" -eq 137 ]]; then
      summary="stopped by the time limit of ${SPEC_TIMEOUT} s${summary:+, $summary}"
    fi
    printf '%s\t%s\t%s\t%s\n' "$label" "$rc" "$(($(date +%s) - started))" "$summary" >>"$dir/specs.tsv"
    if [[ "$rc" -ne 0 ]]; then
      failed=1
    fi
  }

  if [[ "$#" -gt 0 ]]; then
    run_one selection "$@"
  else
    # One Playwright process for each spec file: Chromium memory is released and each file gets a new seed database.
    local spec
    for spec in "$REPO_ROOT"/e2e/*.spec.js; do
      run_one "$(basename "$spec")" "$(basename "$spec")"
    done
  fi
  rm -f "$dir/current"
  if [[ "$failed" -eq 0 ]]; then
    echo passed >"$dir/result"
  else
    echo failed >"$dir/result"
  fi
  exit "$failed"
}

cmd_start() {
  resolve_tools
  if ! systemctl --user show-environment >/dev/null 2>&1; then
    echo "The systemd user manager is not available. This script needs it to isolate the run." >&2
    exit 1
  fi
  if unit_active; then
    echo "An E2E run is active on port $PORT (unit $UNIT.service). Use 'status', 'wait', or 'stop'." >&2
    exit 1
  fi
  systemctl --user reset-failed "$UNIT.service" 2>/dev/null || true

  local id dir total name env_args=()
  id="$(date +%Y%m%d-%H%M%S)"
  dir="$RUNS_DIR/$id"
  mkdir -p "$dir"
  if [[ "$#" -gt 0 ]]; then
    total=1
  else
    total="$(find "$REPO_ROOT/e2e" -maxdepth 1 -name '*.spec.js' | wc -l)"
  fi
  printf 'id=%s\nstarted=%s\ntotal=%s\nargs=%s\n' "$id" "$(date +%s)" "$total" "$*" >"$dir/meta"
  : >"$dir/specs.tsv"
  : >"$dir/run.log"
  ln -sfn "$id" "$RUNS_DIR/latest"
  # Keep only the newest run directories.
  find "$RUNS_DIR" -mindepth 1 -maxdepth 1 -type d | sort -r | tail -n "+$((KEEP_RUNS + 1))" | xargs -r rm -rf

  # A service does not get the environment of the caller. Give it the tool paths and all CLAIRE_ variables.
  for name in PATH HOME LANG NODE_PATH NODE_OPTIONS PLAYWRIGHT_BROWSERS_PATH $(compgen -e | grep '^CLAIRE_' || true); do
    if [[ -n "${!name:-}" ]]; then
      env_args+=("--setenv=$name")
    fi
  done
  systemd-run --user --quiet --unit="$UNIT" --description="Claire Playwright E2E run $id" --working-directory="$REPO_ROOT" -p "MemoryMax=$MEM_MAX" -p ManagedOOMMemoryPressure=kill -p "RuntimeMaxSec=$RUN_TIMEOUT" -p TimeoutStopSec=10 "${env_args[@]}" -- bash "$SELF" __worker "$dir" "$@"
  echo "Started E2E run $id (unit $UNIT.service, time limit ${RUN_TIMEOUT} s, memory limit ${MEM_MAX})."
  echo "Results: $dir"
}

print_failures() {
  local dir="$1" label
  while IFS=$'\t' read -r label rc _; do
    if [[ "$rc" -ne 0 ]]; then
      echo
      echo "--- $label (last 60 lines of $dir/$label.log; server log: $dir/$label.server.log) ---"
      tail -n 60 "$dir/$label.log"
    fi
  done <"$dir/specs.tsv"
}

cmd_wait() {
  local limit="${1:-0}" dir state printed=0 lines began
  dir="$(latest_dir)"
  began="$(date +%s)"
  while :; do
    state="$(run_state "$dir")"
    lines="$(wc -l <"$dir/specs.tsv")"
    if [[ "$lines" -gt "$printed" ]]; then
      tail -n "+$((printed + 1))" "$dir/specs.tsv" | format_specs
      printed="$lines"
    fi
    if [[ "$state" != running ]]; then
      break
    fi
    if [[ "$limit" -gt 0 && "$(($(date +%s) - began))" -ge "$limit" ]]; then
      echo "The run is not complete after ${limit} s: $printed of $(meta_value "$dir" total) done, now $(cat "$dir/current" 2>/dev/null || echo -). Use 'wait' again."
      return 3
    fi
    sleep 2
  done
  echo "E2E run $(meta_value "$dir" id): $state"
  explain_state "$state"
  case "$state" in
    passed) return 0 ;;
    failed)
      print_failures "$dir"
      return 1
      ;;
    *) return 2 ;;
  esac
}

cmd_status() {
  local dir state current
  dir="$(latest_dir)"
  state="$(run_state "$dir")"
  echo "E2E run $(meta_value "$dir" id): $state"
  explain_state "$state"
  echo "Results: $dir"
  if [[ "$state" == running ]]; then
    current="$(cat "$dir/current" 2>/dev/null || true)"
    echo "Elapsed: $(($(date +%s) - $(meta_value "$dir" started))) s of ${RUN_TIMEOUT} s. Done: $(wc -l <"$dir/specs.tsv") of $(meta_value "$dir" total). Now: ${current:--}"
    if [[ -n "$current" && -f "$dir/$current.log" ]]; then
      echo "Last line: $(grep -av '^[[:space:]]*$' "$dir/$current.log" | tail -n 1)"
    fi
  fi
  format_specs <"$dir/specs.tsv"
}

cmd_watch() {
  local dir tail_pid
  dir="$(latest_dir)"
  tail -n +1 -f "$dir/run.log" &
  tail_pid="$!"
  trap 'kill "$tail_pid" 2>/dev/null || true' EXIT
  while [[ "$(run_state "$dir")" == running ]]; do
    sleep 2
  done
  sleep 1
  kill "$tail_pid" 2>/dev/null || true
  trap - EXIT
  echo
  cmd_status
}

cmd_tmux() {
  if ! command -v tmux >/dev/null 2>&1; then
    echo "tmux is not installed. Use 'watch' in a terminal." >&2
    exit 1
  fi
  latest_dir >/dev/null
  exec tmux new-session -A -s "$UNIT" "bash '$SELF' watch; exec bash"
}

cmd_stop() {
  local dir
  if ! unit_active; then
    echo "No E2E run is active on port $PORT."
    return 0
  fi
  dir="$(latest_dir)"
  systemctl --user stop "$UNIT.service"
  if [[ ! -f "$dir/result" ]]; then
    echo stopped >"$dir/result"
  fi
  echo "Stopped E2E run $(meta_value "$dir" id)."
}

command="${1:-run}"
case "$command" in
  run | start | wait | status | watch | tmux | stop | __worker) shift || true ;;
  -h | --help | help)
    usage
    exit 0
    ;;
  *) command=run ;;
esac

case "$command" in
  run)
    cmd_start "$@"
    cmd_wait
    ;;
  start) cmd_start "$@" ;;
  wait) cmd_wait "$@" ;;
  status) cmd_status ;;
  watch) cmd_watch ;;
  tmux) cmd_tmux ;;
  stop) cmd_stop ;;
  __worker) worker "$@" ;;
esac
