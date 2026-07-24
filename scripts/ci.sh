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
bash -n cb-manuscript deploy.sh scripts/ci.sh scripts/backup_pull.sh
python3 -m py_compile ops/cb_manuscript.py

echo "[ci] 2/4 Compose 운영·개발 설정 검사"
CB_ENV_FILE=.env.example \
  docker compose --env-file .env.example -f docker-compose.yml config --quiet
CB_ENV_FILE=.env.example CB_DEV_ENV_FILE=.env.dev.example \
  docker compose --env-file .env.example --env-file .env.dev.example \
    -f docker-compose.yml -f docker-compose.dev.yml config --quiet

echo "[ci] 3/4 uv.lock 일관성 검사 (pyproject 와 동기인지)"
uv lock --check

echo "[ci] 4/4 테스트"
PYTHONPATH=. uv run --extra dev pytest -q

echo "[ci] ✅ 통과"
