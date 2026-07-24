#!/usr/bin/env bash
# Claire Bible 원격 전송 호환 계층.
# 로컬 → 원격 rsync 후 원격의 cb-manuscript install/update를 호출한다.
# data/ vault/ 는 원격에 영속 — rsync 에서 제외하여 절대 덮어쓰지 않는다.
set -euo pipefail

cd "$(dirname "$0")"   # 어디서 실행해도 프로젝트 루트 기준

fail() {
  echo "deploy: $*" >&2
  exit 1
}

# 접속 설정(.env.deploy)과 컨테이너 런타임 설정(.env)을 분리한다. 어느 파일도 셸
# 코드로 source 하지 않는다. 비어 있지 않은 프로세스 환경변수가 배포 파일보다
# 우선한다.
DEPLOY_ENV_FILE="${DEPLOY_ENV_FILE:-.env.deploy}"
DEPLOY_APP_ENV_FILE="${DEPLOY_APP_ENV_FILE:-.env}"
case "${DEPLOY_ENV_FILE##*/}" in
  .env|.env.*) ;;
  *) fail "DEPLOY_ENV_FILE의 파일명은 .env 또는 .env.* 형식이어야 합니다." ;;
esac
case "${DEPLOY_APP_ENV_FILE##*/}" in
  .env|.env.*) ;;
  *) fail "DEPLOY_APP_ENV_FILE의 파일명은 .env 또는 .env.* 형식이어야 합니다." ;;
esac
case "$DEPLOY_ENV_FILE" in
  /*) DEPLOY_ENV_SOURCE="$DEPLOY_ENV_FILE" ;;
  *) DEPLOY_ENV_SOURCE="./$DEPLOY_ENV_FILE" ;;
esac
case "$DEPLOY_APP_ENV_FILE" in
  /*) APP_ENV_SOURCE="$DEPLOY_APP_ENV_FILE" ;;
  *) APP_ENV_SOURCE="./$DEPLOY_APP_ENV_FILE" ;;
esac

trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

# 필요한 키만 읽는 제한적인 dotenv 파서. 임의의 .env 내용을 실행하지 않는다.
dotenv_get() {
  local key="$1" line value="" found=0
  local double_quoted='^"([^"]*)"([[:space:]]*#.*)?$'
  local single_quoted="^'([^']*)'([[:space:]]*#.*)?$"

  [ -f "$DEPLOY_ENV_SOURCE" ] || return 1

  while IFS= read -r line || [ -n "$line" ]; do
    line="$(trim "$line")"
    [ -z "$line" ] && continue
    [ "${line#\#}" != "$line" ] && continue

    if [[ "$line" =~ ^export[[:space:]]+ ]]; then
      line="$(trim "${line#export}")"
    fi
    if [[ "$line" =~ ^${key}[[:space:]]*=(.*)$ ]]; then
      value="$(trim "${BASH_REMATCH[1]}")"
      found=1
    fi
  done < "$DEPLOY_ENV_SOURCE"

  [ "$found" -eq 1 ] || return 1

  # 배포 값은 단순 문자열이다. 따옴표 뒤 또는 비인용 인라인 주석을 제거한다.
  if [[ "$value" =~ $double_quoted ]]; then
    value="${BASH_REMATCH[1]}"
  elif [[ "$value" =~ $single_quoted ]]; then
    value="${BASH_REMATCH[1]}"
  else
    value="${value%%[[:space:]]\#*}"
    value="$(trim "$value")"
  fi
  printf '%s' "$value"
}

DEPLOY_REMOTE="${DEPLOY_REMOTE:-$(dotenv_get DEPLOY_REMOTE || true)}"
DEPLOY_PORT="${DEPLOY_PORT:-$(dotenv_get DEPLOY_PORT || true)}"
DEPLOY_PATH="${DEPLOY_PATH:-$(dotenv_get DEPLOY_PATH || true)}"
DEPLOY_ENV_SYNC="${DEPLOY_ENV_SYNC:-$(dotenv_get DEPLOY_ENV_SYNC || true)}"
DEPLOY_ACTION="${DEPLOY_ACTION:-$(dotenv_get DEPLOY_ACTION || true)}"
SKIP_CI="${SKIP_CI:-$(dotenv_get SKIP_CI || true)}"

DEPLOY_PORT="${DEPLOY_PORT:-22}"
DEPLOY_ENV_SYNC="${DEPLOY_ENV_SYNC:-if-missing}"
DEPLOY_ACTION="${DEPLOY_ACTION:-update}"
SKIP_CI="${SKIP_CI:-0}"

[ -n "$DEPLOY_REMOTE" ] || fail \
  "DEPLOY_REMOTE가 비어 있습니다. $DEPLOY_ENV_FILE 또는 프로세스 환경에 설정하세요."
[[ "$DEPLOY_REMOTE" =~ ^([A-Za-z0-9._][A-Za-z0-9._-]*@)?[A-Za-z0-9._][A-Za-z0-9._-]*$ ]] || fail \
  "DEPLOY_REMOTE 형식이 잘못되었습니다: user@host 또는 SSH 별칭만 사용할 수 있습니다."

[[ "$DEPLOY_PORT" =~ ^[1-9][0-9]{0,4}$ ]] || fail \
  "DEPLOY_PORT는 1~65535 범위의 정수여야 합니다."
(( DEPLOY_PORT <= 65535 )) || fail "DEPLOY_PORT는 65535 이하여야 합니다."

case "$DEPLOY_PATH" in
  *//*) fail "DEPLOY_PATH에는 중복 '/'를 사용할 수 없습니다." ;;
esac
DEST="${DEPLOY_PATH%/}"
[ -n "$DEST" ] || fail \
  "DEPLOY_PATH가 비어 있습니다. $DEPLOY_ENV_FILE에 원격 절대 경로를 설정하세요."
[[ "$DEST" =~ ^/[A-Za-z0-9._/-]+$ ]] || fail \
  "DEPLOY_PATH에는 영문자, 숫자, '.', '_', '-', '/'만 사용할 수 있습니다."
case "$DEST" in
  /|*/.|*/./*|*/..|*/../*)
    fail "DEPLOY_PATH는 '.', '..' 경로 세그먼트가 없는 루트 외 절대 경로여야 합니다."
    ;;
esac

case "$DEPLOY_ENV_SYNC" in
  always|if-missing|never) ;;
  *) fail "DEPLOY_ENV_SYNC는 always, if-missing, never 중 하나여야 합니다." ;;
esac
case "$DEPLOY_ACTION" in
  install|update) ;;
  *) fail "DEPLOY_ACTION은 install 또는 update여야 합니다." ;;
esac

if [ "$DEPLOY_ENV_SYNC" = "always" ] && [ ! -f "$APP_ENV_SOURCE" ]; then
  fail "$DEPLOY_APP_ENV_FILE 파일이 없습니다. './cb-manuscript init' 후 값을 채우세요."
fi

# [0/5] 배포 전 CI 게이트 — lock 동기 + 테스트. 실패하면 set -e 로 배포 중단(깨진 빌드
# 가 원격에 올라가 컨테이너가 무한재시작하는 사고 방지). 건너뛰려면 SKIP_CI=1.
if [ "$SKIP_CI" != "1" ]; then
  echo "[0/5] CI 게이트"
  bash ./scripts/ci.sh
fi

REMOTE="$DEPLOY_REMOTE"
PORT="$DEPLOY_PORT"
SSH_CMD=(ssh -p "$PORT")
RSH="ssh -p $PORT"

echo "[1/5] 원격 디렉터리 준비"
REMOTE_GUARD="
set -eu
if [ ! -e '$DEST' ]; then exit 0; fi
if [ ! -d '$DEST' ]; then exit 1; fi
if [ -f '$DEST/.claire-deploy-root' ] &&
   grep -qxF claire-bible '$DEST/.claire-deploy-root'; then
  exit 0
fi
if [ -f '$DEST/docker-compose.yml' ] &&
   (grep -Eq '^[[:space:]]*container_name:[[:space:]]*claire_bot[[:space:]]*$' \
      '$DEST/docker-compose.yml' ||
    grep -Eq '^[[:space:]]{2}api:[[:space:]]*$' '$DEST/docker-compose.yml') &&
   [ -f '$DEST/pyproject.toml' ] &&
   grep -Eq '^[[:space:]]*name[[:space:]]*=[[:space:]]*\"claire\"[[:space:]]*$' \
     '$DEST/pyproject.toml' &&
   [ -d '$DEST/src/claire' ]; then
  exit 0
fi
unexpected=\$(find '$DEST' -mindepth 1 -maxdepth 1 \
  ! -name data ! -name vault ! -name backups ! -name .env -print -quit)
[ -z \"\$unexpected\" ]
"
if ! "${SSH_CMD[@]}" "$REMOTE" "$REMOTE_GUARD"; then
  fail "DEPLOY_PATH가 기존 Claire 배포 루트나 안전한 신규 경로가 아닙니다: $DEST"
fi

REMOTE_ENV_EXISTS=0
if [ "$DEPLOY_ENV_SYNC" != "always" ]; then
  if "${SSH_CMD[@]}" "$REMOTE" "test -f '$DEST/.env'"; then
    REMOTE_ENV_EXISTS=1
  fi
fi
if [ "$DEPLOY_ENV_SYNC" = "if-missing" ] &&
   [ "$REMOTE_ENV_EXISTS" -eq 0 ] && [ ! -f "$APP_ENV_SOURCE" ]; then
  fail "로컬 ${DEPLOY_APP_ENV_FILE}과 원격 $DEST/.env가 모두 없습니다."
fi
if [ "$DEPLOY_ENV_SYNC" = "never" ] && [ "$REMOTE_ENV_EXISTS" -eq 0 ]; then
  fail "DEPLOY_ENV_SYNC=never에는 기존 원격 $DEST/.env가 필요합니다."
fi

"${SSH_CMD[@]}" "$REMOTE" \
  "mkdir -p -- '$DEST/data' '$DEST/vault' '$DEST/backups' && chmod 700 '$DEST/backups' && printf '%s\n' claire-bible > '$DEST/.claire-deploy-root'"

echo "[2/5] 소스 동기화 (data/vault/research 등 제외; --delete 는 코드 트리에만)"
rsync -az --delete -e "${RSH}" \
  --exclude '.venv' \
  --exclude '.cb-manuscript' \
  --exclude 'backups' \
  --exclude 'data' \
  --exclude 'vault' \
  --exclude 'research' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.git' \
  --exclude '.pytest_cache' \
  --exclude '*.egg-info' \
  --exclude 'docs/*.jpg' \
  --include '/.env.example' \
  --include '/.env.dev.example' \
  --include '/.env.deploy.example' \
  --exclude '.env' \
  --exclude '.env.*' \
  --exclude '.claire-deploy-root' \
  ./ "${REMOTE}:${DEST}/"

sync_env() {
  [ -f "$APP_ENV_SOURCE" ] || fail \
    "$DEPLOY_APP_ENV_FILE 파일이 없어 원격 .env를 만들 수 없습니다."
  rsync -az --chmod=F600 -e "$RSH" -- "$APP_ENV_SOURCE" "$REMOTE:$DEST/.env"
  "${SSH_CMD[@]}" "$REMOTE" "chmod 600 '$DEST/.env'"
}

echo "[3/5] 런타임 .env 동기화 ($DEPLOY_ENV_SYNC)"
case "$DEPLOY_ENV_SYNC" in
  always)
    sync_env
    ;;
  if-missing)
    if [ "$REMOTE_ENV_EXISTS" -eq 1 ]; then
      echo "      원격 .env 유지"
    else
      sync_env
    fi
    ;;
  never)
    echo "      건너뜀 — 원격 .env를 별도로 관리"
    ;;
esac

echo "[4/5] 원격 cb-manuscript $DEPLOY_ACTION"
if [ "$DEPLOY_ACTION" = "install" ]; then
  "${SSH_CMD[@]}" "$REMOTE" "cd '$DEST' && bash ./cb-manuscript install"
else
  "${SSH_CMD[@]}" "$REMOTE" "cd '$DEST' && bash ./cb-manuscript update --no-fetch"
fi

echo "[5/5] 원격 상태"
"${SSH_CMD[@]}" "$REMOTE" "cd '$DEST' && bash ./cb-manuscript status"
echo "배포 완료."
