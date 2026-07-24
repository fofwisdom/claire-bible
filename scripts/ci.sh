#!/usr/bin/env bash
# claire_bible CI — 배포/커밋 전 자동 검증. deploy.sh 가 배포 전 게이트로 호출하고,
# 수동으로도 `./scripts/ci.sh` 로 돌린다.
#
# 두 가지를 본다:
#  1) uv.lock 이 pyproject 와 동기인가 — 컨테이너는 `uv sync --frozen` 으로 lock 기준
#     설치하므로, deps 를 pyproject 에만 추가하고 lock 을 안 하면 "로컬은 통과인데
#     컨테이너는 무한재시작" 사고가 난다(실제 겪음). 이 검사가 그걸 미리 잡는다.
#  2) 전체 테스트.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "[ci] 1/2 uv.lock 일관성 검사 (pyproject 와 동기인지)"
uv lock --check

echo "[ci] 2/2 테스트"
uv run pytest -q

echo "[ci] ✅ 통과"
