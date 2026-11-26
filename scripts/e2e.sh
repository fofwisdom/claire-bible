#!/usr/bin/env bash
# Run Playwright E2E tests as a detached background process with a hard time limit and a 2400 MiB memory limit. Writes state files so any terminal or session can inspect or stop the run. Supports systemd and docker runner back ends (CLAIRE_E2E_RUNNER).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SELF="$REPO_ROOT/scripts/e2e.sh"
cd "$REPO_ROOT"

PORT="${CLAIRE_E2E_PORT:-${CLAIRE_INJECT_PORT:-8766}}"
UNIT="claire-e2e-$PORT"
TOOLBOX_IMAGE="${CLAIRE_E2E_IMAGE:-claire-toolbox:dev}"
RUNS_DIR="$REPO_ROOT/data/e2e-runs"
ACTIVE_FILE="$RUNS_DIR/active-$PORT"
KEEP_RUNS=10
# Time limit of the full run and of one Playwright process, in seconds.
RUN_TIMEOUT="${CLAIRE_E2E_RUN_TIMEOUT:-1200}"
SPEC_TIMEOUT="${CLAIRE_E2E_SPEC_TIMEOUT:-600}"

usage() {
  cat <<'EOF'
Usage: CLAIRE_E2E_RUNNER=systemd|docker bash scripts/e2e.sh <command> [Playwright arguments]

  run [args]     Start a run and wait for the result (same as: start, then wait).
  start [args]   Start a detached run and return immediately.
  wait [seconds] Wait for the end of the run. With seconds, stop waiting after that time.
  status         Show the state of the latest run.
  watch          Follow the output of the latest run until it ends.
  tmux           Open a tmux session that follows the latest run.
  stop           Stop the active run.

Required environment variable for host commands:
  CLAIRE_E2E_RUNNER=systemd   Run detached on this host (Linux).
  CLAIRE_E2E_RUNNER=docker    Run detached in the claire-toolbox:dev container.

No arguments after run or start: each spec file runs in its own Playwright process.
With arguments (a spec file name, -g <pattern>, and so on): one Playwright process gets them.
A first argument that is not a command is an argument of run.

Exit codes of run and wait: 0 passed, 1 failed, 2 stopped by the time limit, memory limit, or a stop command, 3 still running after the wait time.
EOF
}

require_runner() {
  case "${CLAIRE_E2E_RUNNER:-}" in
    systemd | docker) ;;
    "")
      echo "CLAIRE_E2E_RUNNER must be set to 'systemd' or 'docker'." >&2
      exit 1
      ;;
    *)
      echo "Invalid CLAIRE_E2E_RUNNER='${CLAIRE_E2E_RUNNER}'. Expected 'systemd' or 'docker'." >&2
      exit 1
      ;;
  esac
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
  if [[ -n "${CLAIRE_E2E_NODE_MODULES:-}" && -d "${CLAIRE_E2E_NODE_MODULES}/@playwright/test" ]]; then
    E2E_NODE_MODULES="$CLAIRE_E2E_NODE_MODULES"
  elif [[ -d "$REPO_ROOT/e2e/node_modules/@playwright/test" ]]; then
    E2E_NODE_MODULES="$REPO_ROOT/e2e/node_modules"
  elif [[ -d "$main_root/e2e/node_modules/@playwright/test" ]]; then
    E2E_NODE_MODULES="$main_root/e2e/node_modules"
  else
    echo "e2e/node_modules/@playwright/test is missing. Run 'npm ci' inside e2e/ or set CLAIRE_E2E_NODE_MODULES." >&2
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

docker_container_ref() {
  local dir="$1"
  if [[ -f "$dir/container" ]]; then
    cat "$dir/container" 2>/dev/null || printf '%s\n' "$UNIT"
  else
    printf '%s\n' "$UNIT"
  fi
}

docker_inspect_state() {
  local container="$1"
  docker inspect --format '{{.State.Running}} {{.State.OOMKilled}} {{.State.ExitCode}}' "$container" 2>/dev/null || true
}

docker_container_running() {
  local dir="$1" info running
  info="$(docker_inspect_state "$(docker_container_ref "$dir")")"
  running="${info%% *}"
  [[ "$running" == "true" ]]
}

run_runner() {
  local dir="$1" runner
  runner="$(meta_value "$dir" runner)"
  printf '%s\n' "${runner:-${CLAIRE_E2E_RUNNER:-systemd}}"
}

active_run_dir() {
  local dir pid runner
  [[ -f "$ACTIVE_FILE" ]] || return 1
  dir="$(cat "$ACTIVE_FILE" 2>/dev/null || true)"
  if [[ -z "$dir" || ! -d "$dir" || -f "$dir/result" ]]; then
    return 1
  fi
  runner="$(run_runner "$dir")"
  if [[ "$runner" == "docker" ]]; then
    if docker_container_running "$dir"; then
      printf '%s\n' "$dir"
      return 0
    fi
    return 1
  fi
  [[ -f "$dir/pid" ]] || return 1
  pid="$(cat "$dir/pid" 2>/dev/null || true)"
  if pid_alive "$pid"; then
    printf '%s\n' "$dir"
    return 0
  fi
  return 1
}

# Resolve $RUNS_DIR/latest without GNU readlink -f for Bash 3.2 and macOS compatibility.
latest_dir() {
  local link="$RUNS_DIR/latest" dir="" target=""
  if [[ -L "$link" ]]; then
    target="$(readlink "$link" 2>/dev/null || true)"
    if [[ -n "$target" ]]; then
      if [[ "$target" == /* ]]; then
        dir="$(cd "$target" 2>/dev/null && pwd || true)"
      else
        dir="$(cd "$RUNS_DIR/$target" 2>/dev/null && pwd || true)"
      fi
    fi
  elif [[ -d "$link" ]]; then
    dir="$(cd "$link" 2>/dev/null && pwd || true)"
  fi
  if [[ -z "$dir" || ! -d "$dir" ]]; then
    echo "No E2E run is recorded in $RUNS_DIR." >&2
    exit 1
  fi
  printf '%s\n' "$dir"
}

meta_value() {
  [[ -f "$1/meta" ]] || return 0
  sed -n "s/^$2=//p" "$1/meta"
}

# Print the state of a run: running, passed, failed, stopped, timeout, oom-kill, or lost.
run_state() {
  local dir="$1" pid runner info running oom_killed exit_code
  if [[ -f "$dir/result" ]]; then
    cat "$dir/result"
    return 0
  fi
  runner="$(run_runner "$dir")"
  if [[ "$runner" == "docker" ]]; then
    info="$(docker_inspect_state "$(docker_container_ref "$dir")")"
    if [[ -z "$info" ]]; then
      echo lost
      return 0
    fi
    read -r running oom_killed exit_code <<EOF
$info
EOF
    if [[ "$running" == "true" ]]; then
      echo running
    elif [[ "$oom_killed" == "true" ]]; then
      echo oom-kill
    elif [[ "$exit_code" == "124" || "$exit_code" == "137" ]]; then
      echo timeout
    else
      echo lost
    fi
    return 0
  fi
  if [[ -f "$dir/pid" ]]; then
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
    oom-kill) echo "The run stopped because it reached the memory limit." ;;
    timeout) echo "The run reached the time limit of ${RUN_TIMEOUT} s and stopped." ;;
    lost)
      if [[ -n "$dir" && "$(run_runner "$dir")" == "docker" ]]; then
        echo "The run stopped without a result.${dir:+ Look at: $dir/run.log}"
      else
        echo "The run stopped without a result.${dir:+ Look at: $dir/supervisor.log}"
      fi
      ;;
  esac
}

format_specs() {
  awk -F'\t' '{ printf "%-5s %-36s %4s s  %s\n", ($2 == 0 ? "PASS" : "FAIL"), $1, $3, $4 }'
}

# The internal supervisor of the detached systemd run: enforce RUN_TIMEOUT and record final state.
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

  timeout -k 10 "$RUN_TIMEOUT" bash "$SELF" __worker "$dir" ${1+"$@"} &
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
    CLAIRE_E2E_SERVER_LOG="$dir/$label.server.log" timeout -k 10 "$SPEC_TIMEOUT" "$PLAYWRIGHT_BIN" test ${1+"$@"} "--config=$REPO_ROOT/e2e/playwright.config.js" 2>&1 | tee -a "$dir/run.log" >"$dir/$label.log" || rc="${PIPESTATUS[0]}"
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
    run_one selection ${1+"$@"}
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

spawn_detached_systemd() {
  local dir="$1"
  shift
  if command -v setsid >/dev/null 2>&1; then
    setsid bash "$SELF" __supervisor "$dir" ${1+"$@"} </dev/null >"$dir/supervisor.log" 2>&1 &
  else
    local py="${CLAIRE_E2E_PYTHON:-python3}"
    "$py" -c 'import subprocess, sys; log = open(sys.argv[1], "ab"); subprocess.Popen(sys.argv[2:], stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)' "$dir/supervisor.log" bash "$SELF" __supervisor "$dir" ${1+"$@"}
  fi

  local tries=0
  while [[ ! -s "$dir/pid" && ! -f "$dir/result" && "$tries" -lt 100 ]]; do
    sleep 0.05
    tries=$((tries + 1))
  done
}

spawn_detached_docker() {
  local dir="$1" id="$2"
  shift 2
  if ! command -v docker >/dev/null 2>&1; then
    echo "docker is not installed or not in PATH." >&2
    rm -f "$ACTIVE_FILE"
    exit 1
  fi
  if ! docker image inspect "$TOOLBOX_IMAGE" >/dev/null 2>&1; then
    docker build -t "$TOOLBOX_IMAGE" -f "$REPO_ROOT/ops/dev/toolbox.Dockerfile" "$REPO_ROOT"
  fi
  docker rm -f "$UNIT" >/dev/null 2>&1 || true
  printf '%s\n' "$UNIT" >"$dir/container"
  if ! docker run -d \
    --name "$UNIT" \
    --memory 2400m \
    --memory-swap 2400m \
    --pids-limit 512 \
    -u "$(id -u):$(id -g)" \
    -v "$REPO_ROOT:/src" \
    -w /src \
    -e "CLAIRE_E2E_PORT=$PORT" \
    -e "CLAIRE_E2E_RUN_TIMEOUT=$RUN_TIMEOUT" \
    -e "CLAIRE_E2E_SPEC_TIMEOUT=$SPEC_TIMEOUT" \
    -e "TMPDIR=/tmp" \
    -e "HOME=/tmp" \
    -e "CLAIRE_IGNORE_DOTENV=1" \
    -e "CLAIRE_E2E_NODE_MODULES=/opt/claire-e2e/node_modules" \
    -e "CLAIRE_E2E_PYTHON=/opt/claire-venv/bin/python" \
    -e "PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright" \
    "$TOOLBOX_IMAGE" \
    timeout -k 10 "$RUN_TIMEOUT" bash /src/scripts/e2e.sh __worker "/src/data/e2e-runs/$id" ${1+"$@"} >/dev/null; then
    rm -f "$ACTIVE_FILE"
    echo "Failed to start Docker container $UNIT." >&2
    exit 1
  fi
}

prune_old_runs() {
  local old_dir
  find "$RUNS_DIR" -mindepth 1 -maxdepth 1 -type d | sort -r | tail -n "+$((KEEP_RUNS + 1))" | while IFS= read -r old_dir; do
    [[ -n "$old_dir" ]] && rm -rf "$old_dir"
  done
}

cmd_start() {
  require_runner
  if [[ "$CLAIRE_E2E_RUNNER" == "systemd" ]]; then
    resolve_tools
  fi
  local existing_dir
  if existing_dir="$(active_run_dir)"; then
    echo "An E2E run is active on port $PORT ($(meta_value "$existing_dir" id)). Use 'status', 'wait', or 'stop'." >&2
    exit 1
  fi
  rm -f "$ACTIVE_FILE"

  local id dir total suffix=1
  id="$(date +%Y%m%d-%H%M%S)"
  dir="$RUNS_DIR/$id"
  if [[ -e "$dir" ]]; then
    while [[ -e "${dir}-${suffix}" ]]; do
      suffix=$((suffix + 1))
    done
    id="${id}-${suffix}"
    dir="$RUNS_DIR/$id"
  fi
  mkdir -p "$dir"
  if [[ "$#" -gt 0 ]]; then
    total=1
  else
    total="$(find "$REPO_ROOT/e2e" -maxdepth 1 -name '*.spec.js' | wc -l | tr -d ' ')"
  fi
  printf 'id=%s\nstarted=%s\ntotal=%s\nrunner=%s\nargs=%s\n' "$id" "$(date +%s)" "$total" "$CLAIRE_E2E_RUNNER" "${*:-}" >"$dir/meta"
  : >"$dir/specs.tsv"
  : >"$dir/run.log"
  ln -sfn "$id" "$RUNS_DIR/latest"
  printf '%s\n' "$dir" >"$ACTIVE_FILE"
  prune_old_runs

  if [[ "$CLAIRE_E2E_RUNNER" == "docker" ]]; then
    spawn_detached_docker "$dir" "$id" ${1+"$@"}
    echo "Started E2E run $id (port $PORT, runner docker, container $UNIT, time limit ${RUN_TIMEOUT} s)."
  else
    spawn_detached_systemd "$dir" ${1+"$@"}
    echo "Started E2E run $id (port $PORT, runner systemd, pid $(cat "$dir/pid" 2>/dev/null || echo unknown), time limit ${RUN_TIMEOUT} s)."
  fi
  echo "Results: $dir"
}

print_failures() {
  local dir="$1" label rc
  while IFS=$'\t' read -r label rc _; do
    if [[ "$rc" -ne 0 ]]; then
      echo
      echo "--- $label (last 60 lines of $dir/$label.log; server log: $dir/$label.server.log) ---"
      tail -n 60 "$dir/$label.log"
    fi
  done <"$dir/specs.tsv"
}

cmd_wait() {
  require_runner
  local limit="${1:-0}" dir state printed=0 lines began
  dir="$(latest_dir)"
  began="$(date +%s)"
  while :; do
    state="$(run_state "$dir")"
    lines="$(wc -l <"$dir/specs.tsv" | tr -d ' ')"
    if [[ "$lines" -gt "$printed" ]]; then
      tail -n "+$((printed + 1))" "$dir/specs.tsv" | format_specs
      printed="$lines"
    fi
    if [[ "$state" != "running" ]]; then
      break
    fi
    if [[ "$limit" -gt 0 && "$(($(date +%s) - began))" -ge "$limit" ]]; then
      echo "The run is not complete after ${limit} s: $printed of $(meta_value "$dir" total) done, now $(cat "$dir/current" 2>/dev/null || echo -). Use 'wait' again."
      return 3
    fi
    sleep 2
  done
  if [[ ! -f "$dir/result" ]]; then
    echo "$state" >"$dir/result"
  fi
  if [[ -f "$ACTIVE_FILE" && "$(cat "$ACTIVE_FILE" 2>/dev/null || true)" == "$dir" ]]; then
    rm -f "$ACTIVE_FILE"
  fi
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
  require_runner
  local dir state current last_line
  dir="$(latest_dir)"
  state="$(run_state "$dir")"
  echo "E2E run $(meta_value "$dir" id): $state"
  explain_state "$state" "$dir"
  echo "Results: $dir"
  if [[ "$state" == "running" ]]; then
    current="$(cat "$dir/current" 2>/dev/null || true)"
    echo "Elapsed: $(($(date +%s) - $(meta_value "$dir" started))) s of ${RUN_TIMEOUT} s. Done: $(wc -l <"$dir/specs.tsv" | tr -d ' ') of $(meta_value "$dir" total). Now: ${current:--}"
    if [[ -n "$current" && -f "$dir/$current.log" ]]; then
      last_line="$(grep -av '^[[:space:]]*$' "$dir/$current.log" | tail -n 1 || true)"
      if [[ -n "$last_line" ]]; then
        echo "Last line: $last_line"
      fi
    fi
  fi
  format_specs <"$dir/specs.tsv"
}

cmd_watch() {
  require_runner
  local dir tail_pid
  dir="$(latest_dir)"
  tail -n +1 -f "$dir/run.log" &
  tail_pid="$!"
  trap 'kill "$tail_pid" 2>/dev/null || true' EXIT
  while [[ "$(run_state "$dir")" == "running" ]]; do
    sleep 2
  done
  sleep 1
  kill "$tail_pid" 2>/dev/null || true
  trap - EXIT
  echo
  cmd_status
}

cmd_tmux() {
  require_runner
  if ! command -v tmux >/dev/null 2>&1; then
    echo "tmux is not installed. Use 'watch' in a terminal." >&2
    exit 1
  fi
  latest_dir >/dev/null
  exec tmux new-session -A -s "$UNIT" "CLAIRE_E2E_RUNNER='$CLAIRE_E2E_RUNNER' bash '$SELF' watch; exec bash"
}

cmd_stop() {
  require_runner
  local dir pid tries=0 runner
  if ! dir="$(active_run_dir)"; then
    if [[ "$CLAIRE_E2E_RUNNER" == "docker" ]]; then
      docker rm -f "$UNIT" >/dev/null 2>&1 || true
    fi
    rm -f "$ACTIVE_FILE"
    echo "No E2E run is active on port $PORT."
    return 0
  fi
  runner="$(run_runner "$dir")"
  if [[ ! -f "$dir/result" ]]; then
    echo stopped >"$dir/result"
  fi
  rm -f "$dir/current"
  if [[ "$runner" == "docker" ]]; then
    docker rm -f "$UNIT" >/dev/null 2>&1 || true
  else
    pid="$(cat "$dir/pid")"
    kill -TERM -- -"$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    while pid_alive "$pid" && [[ "$tries" -lt 50 ]]; do
      sleep 0.1
      tries=$((tries + 1))
    done
    if pid_alive "$pid"; then
      kill -KILL -- -"$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
    fi
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
    cmd_start ${1+"$@"}
    cmd_wait
    ;;
  start) cmd_start ${1+"$@"} ;;
  wait) cmd_wait ${1+"$@"} ;;
  status) cmd_status ;;
  watch) cmd_watch ;;
  tmux) cmd_tmux ;;
  stop) cmd_stop ;;
  __supervisor) supervisor ${1+"$@"} ;;
  __worker) worker ${1+"$@"} ;;
esac
