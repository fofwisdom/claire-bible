#!/usr/bin/env bash
# Run Playwright E2E tests as a detached background process with a hard time limit and no forced memory cap. Writes state files so any terminal or session can inspect or stop the run. Supports git worktrees without duplicate node_modules or .venv directories.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SELF="$REPO_ROOT/scripts/e2e.sh"
cd "$REPO_ROOT"

PORT="${CLAIRE_E2E_PORT:-${CLAIRE_INJECT_PORT:-8766}}"
UNIT="claire-e2e-$PORT"
RUNS_DIR="$REPO_ROOT/data/e2e-runs"
ACTIVE_FILE="$RUNS_DIR/active-$PORT"
KEEP_RUNS=10
# Time limit of the full run and of one Playwright process, in seconds.
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
  stop           Stop the active run.

No arguments after run or start: each spec file runs in its own Playwright process.
With arguments (a spec file name, -g <pattern>, and so on): one Playwright process gets them.
A first argument that is not a command is an argument of run.

Exit codes of run and wait: 0 passed, 1 failed, 2 stopped by the time limit or a stop command, 3 still running after the wait time.
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

prepend_path_if_missing() {
  local dir="$1"
  if [[ -d "$dir" && ":$PATH:" != *":$dir:"* ]]; then
    export PATH="$dir:$PATH"
  fi
}

resolve_tools() {
  local main_root
  prepend_path_if_missing "$HOME/.local/bin"
  prepend_path_if_missing "$HOME/.deno/bin"
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
  if ! command -v node >/dev/null 2>&1; then
    echo "node is not installed or not in PATH." >&2
    exit 1
  fi
  if [[ -z "${CLAIRE_E2E_PYTHON:-}" ]]; then
    if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
      export CLAIRE_E2E_PYTHON="$REPO_ROOT/.venv/bin/python"
    elif [[ -x "$main_root/.venv/bin/python" ]]; then
      export CLAIRE_E2E_PYTHON="$main_root/.venv/bin/python"
    fi
  fi
  export NODE_PATH="$E2E_NODE_MODULES${NODE_PATH:+:$NODE_PATH}"
}

pid_alive() {
  local pid="$1" args
  [[ -n "$pid" && "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  args="$(ps -p "$pid" -o args= 2>/dev/null || true)"
  [[ "$args" == *"__supervisor"* ]]
}

active_run_dir() {
  local dir pid
  [[ -f "$ACTIVE_FILE" ]] || return 1
  dir="$(cat "$ACTIVE_FILE" 2>/dev/null || true)"
  if [[ -z "$dir" || ! -d "$dir" || -f "$dir/result" || ! -f "$dir/pid" ]]; then
    return 1
  fi
  pid="$(cat "$dir/pid" 2>/dev/null || true)"
  if pid_alive "$pid"; then
    printf '%s\n' "$dir"
    return 0
  fi
  return 1
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

# Print the state of a run: running, passed, failed, stopped, timeout, or lost.
run_state() {
  local dir="$1" pid
  if [[ -f "$dir/result" ]]; then
    cat "$dir/result"
  elif [[ -f "$dir/pid" ]]; then
    pid="$(cat "$dir/pid" 2>/dev/null || true)"
    if pid_alive "$pid"; then
      echo running
    else
      echo lost
    fi
  else
    echo lost
  fi
}

explain_state() {
  local state="$1" dir="${2:-}"
  case "$state" in
    timeout) echo "The run reached the time limit of ${RUN_TIMEOUT} s and stopped." ;;
    lost) echo "The run stopped without a result.${dir:+ Look at: $dir/supervisor.log}" ;;
  esac
}

format_specs() {
  awk -F'\t' '{ printf "%-5s %-36s %4s s  %s\n", ($2 == 0 ? "PASS" : "FAIL"), $1, $3, $4 }'
}

# The internal supervisor of the detached run: enforce RUN_TIMEOUT and record final state.
supervisor() {
  local dir="$1" worker_pid="" rc=0
  shift
  echo "$$" >"$dir/pid"

  on_signal() {
    if [[ ! -f "$dir/result" ]]; then
      echo stopped >"$dir/result"
    fi
    rm -f "$dir/current"
    if [[ -n "$worker_pid" ]]; then
      kill -TERM "$worker_pid" 2>/dev/null || true
    fi
    if [[ -f "$ACTIVE_FILE" && "$(cat "$ACTIVE_FILE" 2>/dev/null || true)" == "$dir" ]]; then
      rm -f "$ACTIVE_FILE"
    fi
    exit 2
  }
  trap on_signal TERM INT

  timeout -k 10 "$RUN_TIMEOUT" bash "$SELF" __worker "$dir" "$@" &
  worker_pid="$!"
  wait "$worker_pid" || rc="$?"

  if [[ "$rc" -eq 124 || "$rc" -eq 137 ]]; then
    if [[ ! -f "$dir/result" ]]; then
      echo timeout >"$dir/result"
    fi
    rm -f "$dir/current"
    rc=2
  elif [[ ! -f "$dir/result" ]]; then
    echo lost >"$dir/result"
    rm -f "$dir/current"
  fi
  if [[ -f "$ACTIVE_FILE" && "$(cat "$ACTIVE_FILE" 2>/dev/null || true)" == "$dir" ]]; then
    rm -f "$ACTIVE_FILE"
  fi
  exit "$rc"
}

# The internal worker: run Playwright and record the results.
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

spawn_detached() {
  local dir="$1"
  shift
  if command -v setsid >/dev/null 2>&1; then
    setsid bash "$SELF" __supervisor "$dir" "$@" </dev/null >"$dir/supervisor.log" 2>&1 &
  else
    local py="${CLAIRE_E2E_PYTHON:-python3}"
    "$py" -c 'import os, subprocess, sys; log = open(sys.argv[1], "ab"); subprocess.Popen(sys.argv[2:], stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)' "$dir/supervisor.log" bash "$SELF" __supervisor "$dir" "$@"
  fi

  local tries=0
  while [[ ! -s "$dir/pid" && ! -f "$dir/result" && "$tries" -lt 100 ]]; do
    sleep 0.05
    tries=$((tries + 1))
  done
}

cmd_start() {
  resolve_tools
  local existing_dir
  if existing_dir="$(active_run_dir)"; then
    echo "An E2E run is active on port $PORT ($(meta_value "$existing_dir" id)). Use 'status', 'wait', or 'stop'." >&2
    exit 1
  fi
  rm -f "$ACTIVE_FILE"

  local id dir total
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
  printf '%s\n' "$dir" >"$ACTIVE_FILE"
  # Keep only the newest run directories.
  find "$RUNS_DIR" -mindepth 1 -maxdepth 1 -type d | sort -r | tail -n "+$((KEEP_RUNS + 1))" | xargs -r rm -rf

  spawn_detached "$dir" "$@"
  echo "Started E2E run $id (port $PORT, pid $(cat "$dir/pid" 2>/dev/null || echo unknown), time limit ${RUN_TIMEOUT} s)."
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
  explain_state "$state" "$dir"
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
  explain_state "$state" "$dir"
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
  local dir pid tries=0
  if ! dir="$(active_run_dir)"; then
    rm -f "$ACTIVE_FILE"
    echo "No E2E run is active on port $PORT."
    return 0
  fi
  pid="$(cat "$dir/pid")"
  if [[ ! -f "$dir/result" ]]; then
    echo stopped >"$dir/result"
  fi
  rm -f "$dir/current"
  kill -TERM -- -"$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
  while pid_alive "$pid" && [[ "$tries" -lt 50 ]]; do
    sleep 0.1
    tries=$((tries + 1))
  done
  if pid_alive "$pid"; then
    kill -KILL -- -"$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
  fi
  rm -f "$ACTIVE_FILE"
  echo "Stopped E2E run $(meta_value "$dir" id)."
}

command="${1:-run}"
case "$command" in
  run | start | wait | status | watch | tmux | stop | __supervisor | __worker) shift || true ;;
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
  __supervisor) supervisor "$@" ;;
  __worker) worker "$@" ;;
esac
