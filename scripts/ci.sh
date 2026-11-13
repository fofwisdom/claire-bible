#!/usr/bin/env bash
# Claire Bible CI — 배포/커밋 전 자동 검증. deploy.sh 가 배포 전 게이트로 호출하고,
# 수동으로도 `bash ./scripts/ci.sh` 로 돌린다.
#
# 네 가지를 본다:
#  1) 운영 셸/Python 진입점의 기본 구문
#  2) 운영·개발 Compose 모델
#  3) uv.lock 이 pyproject 와 동기인가 — 컨테이너는 `uv sync --frozen` 으로 lock 기준
#     설치하므로, deps 를 pyproject 에만 추가하고 lock 을 안 하면 "로컬은 통과인데
#     컨테이너는 무한재시작" 사고가 난다(실제 겪음). 이 검사가 그걸 미리 잡는다.
#  4) 전체 테스트.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "[ci] 1/4 진입점 구문 검사"
bash -n cb-manuscript deploy.sh scripts/ci.sh
python3 -m py_compile ops/cb_manuscript.py
# The container image uses Python 3.11 (Dockerfile FROM python:3.11-slim). A newer local Python accepts syntax that 3.11 rejects (for example PEP 701 f-strings), and the api container then restarts without end. Compile all sources with 3.11.
uv run --no-project --python 3.11 python -I -c '
import pathlib, sys
errors = 0
for root in ("src", "ops", "scripts"):
    for path in sorted(pathlib.Path(root).rglob("*.py")):
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except SyntaxError as exc:
            errors += 1
            print(f"CI Error: Python 3.11 syntax: {path}:{exc.lineno}: {exc.msg}")
sys.exit(1 if errors else 0)
'

echo "[ci] 2/4 Compose 운영·개발 설정 및 볼륨 무결성 검사"
CB_ENV_FILE=.env.example \
  docker compose --env-file .env.example -f docker-compose.yml config --quiet
CB_ENV_FILE=.env.example CB_DEV_ENV_FILE=.env.dev.example \
  docker compose --env-file .env.example --env-file .env.dev.example \
    -f docker-compose.yml -f docker-compose.dev.yml config --quiet
# The same two models with the Compose profile `federation` active (service fedify).
CB_ENV_FILE=.env.example \
  docker compose --env-file .env.example --profile federation -f docker-compose.yml config --quiet
CB_ENV_FILE=.env.example CB_DEV_ENV_FILE=.env.dev.example \
  docker compose --env-file .env.example --env-file .env.dev.example --profile federation \
    -f docker-compose.yml -f docker-compose.dev.yml config --quiet

# Volume specification assertions: /host-bin and /extra-certs must be present; Ubuntu system paths must not regress
grep -q '/host-bin:ro' docker-compose.yml || { echo "CI Error: /host-bin:ro volume missing in docker-compose.yml"; exit 1; }
grep -q '/host-bin:ro' docker-compose.dev.yml || { echo "CI Error: /host-bin:ro volume missing in docker-compose.dev.yml"; exit 1; }
grep -q '/extra-certs:ro' docker-compose.yml || { echo "CI Error: /extra-certs:ro volume missing in docker-compose.yml"; exit 1; }
grep -q '/extra-certs:ro' docker-compose.dev.yml || { echo "CI Error: /extra-certs:ro volume missing in docker-compose.dev.yml"; exit 1; }
if grep -Eq '^[[:space:]]*-[[:space:]]*/etc/(ssl/certs|ca-certificates|localtime)' docker-compose.yml docker-compose.dev.yml; then
  echo "CI Error: Host Ubuntu system paths must not be mounted in docker-compose files"
  exit 1
fi

# Fedify sidecar mount assertions: the service has the profile `federation`, mounts the service key file, and does not mount data/auth as a directory, the complete data directory, or the Claire database.
for compose_file in docker-compose.yml docker-compose.dev.yml; do
  fedify_block="$(awk '/^  fedify:[[:space:]]*$/ {found=1; next} found && /^  [^[:space:]#]/ {found=0} found' "$compose_file")"
  [ -n "$fedify_block" ] || { echo "CI Error: service fedify missing in $compose_file"; exit 1; }
  grep -q 'auth/fedify_service\.key' <<<"$fedify_block" || { echo "CI Error: fedify must mount the service key file in $compose_file"; exit 1; }
  if grep -Eq 'claire\.db|/app/data|/auth/?"?[[:space:]]*$|/auth/?:|(CB_DATA_DIR[^}]*\}|/data)/?"?[[:space:]]*$' <<<"$fedify_block"; then
    echo "CI Error: fedify must not mount data/auth as a directory, the data directory, or claire.db in $compose_file"
    exit 1
  fi
done
grep -Eq '^    profiles: \["federation"\]' docker-compose.yml || { echo "CI Error: service fedify must have the Compose profile federation"; exit 1; }
if CB_ENV_FILE=.env.example docker compose --env-file .env.example -f docker-compose.yml config --services | grep -qx fedify; then
  echo "CI Error: service fedify must not start when the profile federation is not active"
  exit 1
fi

# Sidecar tests run only when deno is installed. tests/test_fedify_sidecar.py has the same condition.
if command -v deno >/dev/null 2>&1; then
  echo "[ci] 2/4 Fedify sidecar tests (deno task test)"
  (cd sidecar/fedify && deno task test)
else
  echo "[ci] 2/4 Fedify sidecar tests skipped: deno is not installed"
fi

echo "[ci] 3/4 uv.lock 일관성 검사 (pyproject 와 동기인지)"
uv lock --check

echo "[ci] 4/4 테스트"
PYTHONPATH=. uv run --extra dev pytest -q

echo "[ci] ✅ 통과"
