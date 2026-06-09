#!/usr/bin/env bash
# claire_bible 원격 배포 (Docker). 로컬 → 원격 rsync 후 compose 재빌드.
# data/ vault/ 는 원격에 영속 — rsync 에서 제외하여 절대 덮어쓰지 않는다.
set -euo pipefail

cd "$(dirname "$0")"   # 어디서 실행해도 프로젝트 루트 기준

# [0/4] 배포 전 CI 게이트 — lock 동기 + 테스트. 실패하면 set -e 로 배포 중단(깨진 빌드
# 가 원격에 올라가 컨테이너가 무한재시작하는 사고 방지). 건너뛰려면 SKIP_CI=1.
if [ "${SKIP_CI:-0}" != "1" ]; then
  echo "[0/4] CI 게이트"
  ./scripts/ci.sh
fi

REMOTE="blackan@192.168.1.8"
PORT=2222
DEST="/home/blackan/claire_bible"
RSH="ssh -p ${PORT}"

echo "[1/4] 원격 디렉터리 준비"
${RSH} "${REMOTE}" "mkdir -p ${DEST}/data ${DEST}/vault"

echo "[2/4] 소스 동기화 (data/vault/research 등 제외; --delete 는 코드 트리에만)"
rsync -az --delete -e "${RSH}" \
  --exclude '.venv' \
  --exclude 'data' \
  --exclude 'vault' \
  --exclude 'research' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.git' \
  --exclude '.pytest_cache' \
  --exclude '*.egg-info' \
  --exclude 'docs/*.jpg' \
  --exclude '.env' \
  ./ "${REMOTE}:${DEST}/"

echo "[3/4] .env 동기화 (없을 때만 — 원격 .env 보호)"
${RSH} "${REMOTE}" "test -f ${DEST}/.env" || rsync -az -e "${RSH}" .env "${REMOTE}:${DEST}/.env"

echo "[4/4] 컨테이너 재빌드 & 기동"
${RSH} "${REMOTE}" "cd ${DEST} && docker compose up -d --build"
${RSH} "${REMOTE}" "cd ${DEST} && docker compose ps"
echo "배포 완료."
