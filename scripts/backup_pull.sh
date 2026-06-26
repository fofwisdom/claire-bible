#!/usr/bin/env bash
# 원격(미니PC) claire 백업본을 이 WSL 로 당겨오는 오프사이트 사본.
#
# 받는 것: 원격 data/backups/ 의 일관 스냅샷(claire backup-loop 가 VACUUM INTO 로 만든
#          것)만. 라이브 운영 DB(data/claire.db)는 건드리지 않는다(쓰는 중 찢긴 사본 방지).
# 보존:    rsync 는 --delete 없이 '누적'한다 → 원격이 keep 정책으로 지워도 로컬엔 남는다.
#          로컬은 KEEP_DAYS 기준으로만 정리해 원격보다 길게 보관(오프사이트의 의의).
# 트리거:  cron(매 6시간) + @reboot. WSL 이 켜져 있는 동안만 도므로 주기를 짧게 둔다.
set -euo pipefail

REMOTE="blackan@192.168.1.8"
PORT=2222
SRC="/home/blackan/claire_bible/data/backups/"
DEST="${HOME}/claire_backups"
KEEP_DAYS=60
LOG="${DEST}/pull.log"

mkdir -p "$DEST"
ts() { date '+%Y-%m-%d %H:%M:%S'; }

if rsync -az --timeout=120 \
     -e "ssh -p ${PORT} -o BatchMode=yes -o ConnectTimeout=10" \
     "${REMOTE}:${SRC}" "${DEST}/"; then
  n=$(find "$DEST" -maxdepth 1 -name 'claire-*.db' | wc -l)
  latest=$(find "$DEST" -maxdepth 1 -name 'claire-*.db' -printf '%f\n' | sort | tail -1)
  echo "$(ts) [pull] ok · 로컬 ${n}개 · 최신=${latest:-없음}" >> "$LOG"
else
  echo "$(ts) [pull] FAIL — 원격 미접속/오류" >> "$LOG"
  exit 1
fi

# 오프사이트 보존 정리: KEEP_DAYS 보다 오래된 사본만 삭제.
find "$DEST" -maxdepth 1 -name 'claire-*.db' -mtime "+${KEEP_DAYS}" -delete
