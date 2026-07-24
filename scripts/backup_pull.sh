#!/usr/bin/env bash
# 원격 Claire 백업본을 로컬로 당겨오는 오프사이트 사본.
#
# 받는 것: 원격 data/backups/ 의 일관 스냅샷(claire backup-loop 가 VACUUM INTO 로 만든
#          것)만. 라이브 운영 DB(data/claire.db)는 건드리지 않는다(쓰는 중 찢긴 사본 방지).
# 보존:    rsync 는 --delete 없이 '누적'한다 → 원격이 keep 정책으로 지워도 로컬엔 남는다.
#          로컬은 KEEP_DAYS 기준으로만 정리해 원격보다 길게 보관(오프사이트의 의의).
# 접속 대상은 코드에 기록하지 않고 CLAIRE_BACKUP_* 환경변수로 전달한다.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

BACKUP_REMOTE="${CLAIRE_BACKUP_REMOTE:-}"
BACKUP_PORT="${CLAIRE_BACKUP_PORT:-22}"
BACKUP_SOURCE="${CLAIRE_BACKUP_SOURCE:-/srv/claire/data/backups/}"
BACKUP_DEST="${CLAIRE_BACKUP_DEST:-${PROJECT_ROOT}/data/offsite-backups}"
BACKUP_KEEP_DAYS="${CLAIRE_BACKUP_KEEP_DAYS:-60}"
BACKUP_LOG="${BACKUP_DEST}/pull.log"

[ -n "$BACKUP_REMOTE" ] || {
  echo "CLAIRE_BACKUP_REMOTE가 필요합니다(user@host 또는 SSH alias)." >&2
  exit 2
}
[[ "$BACKUP_PORT" =~ ^[1-9][0-9]{0,4}$ ]] && (( BACKUP_PORT <= 65535 )) || {
  echo "CLAIRE_BACKUP_PORT는 1~65535 범위여야 합니다." >&2
  exit 2
}
[[ "$BACKUP_KEEP_DAYS" =~ ^[1-9][0-9]*$ ]] || {
  echo "CLAIRE_BACKUP_KEEP_DAYS는 양의 정수여야 합니다." >&2
  exit 2
}
[[ "$BACKUP_SOURCE" == /*/ ]] || {
  echo "CLAIRE_BACKUP_SOURCE는 '/'로 시작하고 끝나는 절대 경로여야 합니다." >&2
  exit 2
}

mkdir -p "$BACKUP_DEST"
ts() { date '+%Y-%m-%d %H:%M:%S'; }

if rsync -az --timeout=120 \
     -e "ssh -p ${BACKUP_PORT} -o BatchMode=yes -o ConnectTimeout=10" \
     "${BACKUP_REMOTE}:${BACKUP_SOURCE}" "${BACKUP_DEST}/"; then
  n=$(find "$BACKUP_DEST" -maxdepth 1 -name 'claire-*.db' | wc -l)
  latest=$(find "$BACKUP_DEST" -maxdepth 1 -name 'claire-*.db' -printf '%f\n' | sort | tail -1)
  echo "$(ts) [pull] ok · 로컬 ${n}개 · 최신=${latest:-없음}" >> "$BACKUP_LOG"
else
  echo "$(ts) [pull] FAIL — 원격 미접속/오류" >> "$BACKUP_LOG"
  exit 1
fi

# 오프사이트 보존 정리: KEEP_DAYS 보다 오래된 사본만 삭제.
find "$BACKUP_DEST" -maxdepth 1 -name 'claire-*.db' -mtime "+${BACKUP_KEEP_DAYS}" -delete
