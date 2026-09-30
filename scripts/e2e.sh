#!/usr/bin/env bash
# Browser regression gate. Node, Chromium and their OS libraries live only in the
# upstream Microsoft Playwright image; Claire's installer and runtime stay unchanged.
set -euo pipefail

cd "$(dirname "$0")/.."

readonly PLAYWRIGHT_IMAGE="mcr.microsoft.com/playwright:v1.62.0-noble@sha256:baed2032d533817f3dbe6425de795788430ba345e819a1201337009ba17c9d07"
BASE_URL="${CLAIRE_E2E_BASE_URL:-http://127.0.0.1:8766/}"
readonly SUITE_DIR="$PWD/e2e"
E2E_TMP="$(mktemp -d)"
trap 'rm -rf -- "$E2E_TMP"' EXIT

cp "$SUITE_DIR/package.json" "$SUITE_DIR/package-lock.json" \
  "$SUITE_DIR/playwright.config.js" "$SUITE_DIR/workspace.spec.js" "$E2E_TMP/"

DOCKER_NET_ARGS=(--network=host)
if [[ "$(uname -s)" != "Linux" ]]; then
  DOCKER_NET_ARGS=(--add-host=host.docker.internal:host-gateway)
  if [[ "$BASE_URL" =~ 127\.0\.0\.1|localhost ]]; then
    BASE_URL="${BASE_URL/127.0.0.1/host.docker.internal}"
    BASE_URL="${BASE_URL/localhost/host.docker.internal}"
  fi
fi

docker run --rm --init --ipc=host "${DOCKER_NET_ARGS[@]}" \
  --user "$(id -u):$(id -g)" \
  --env HOME=/tmp \
  --env PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
  --env "CLAIRE_E2E_BASE_URL=$BASE_URL" \
  --volume "$E2E_TMP:/work" \
  --workdir /work \
  "$PLAYWRIGHT_IMAGE" \
  /bin/bash -lc 'npm ci --ignore-scripts --no-audit --no-fund && npm test'
