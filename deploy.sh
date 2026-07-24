#!/usr/bin/env bash
# claire_bible 원격 배포 (Docker). 로컬 → 원격 rsync 후 compose 재빌드.
# data/ vault/ 는 원격에 영속 — rsync 에서 제외하여 절대 덮어쓰지 않는다.
set -euo pipefail

cd "$(dirname "$0")"   # 어디서 실행해도 프로젝트 루트 기준

fail() {
  echo "deploy: $*" >&2
  exit 1
}

# 앱 설정과 배포 설정은 같은 dotenv 파일을 쓰되, 셸 코드로 source 하지는 않는다.
# 비어 있지 않은 프로세스 환경변수가 .env 값보다 우선한다.
DEPLOY_ENV_FILE="${DEPLOY_ENV_FILE:-.env}"
case "${DEPLOY_ENV_FILE##*/}" in
  .env|.env.*) ;;
  *) fail "DEPLOY_ENV_FILE의 파일명은 .env 또는 .env.* 형식이어야 합니다." ;;
esac
case "$DEPLOY_ENV_FILE" in
  /*) ENV_SOURCE="$DEPLOY_ENV_FILE" ;;
  *) ENV_SOURCE="./$DEPLOY_ENV_FILE" ;;
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

  [ -f "$ENV_SOURCE" ] || return 1

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
  done < "$ENV_SOURCE"

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
SKIP_CI="${SKIP_CI:-$(dotenv_get SKIP_CI || true)}"

DEPLOY_PORT="${DEPLOY_PORT:-22}"
DEPLOY_ENV_SYNC="${DEPLOY_ENV_SYNC:-if-missing}"
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
[ -n "$DEST" ] || fail "DEPLOY_PATH가 비어 있습니다. .env에 원격 절대 경로를 설정하세요."
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

if [ "$DEPLOY_ENV_SYNC" = "always" ] && [ ! -f "$ENV_SOURCE" ]; then
  fail "$DEPLOY_ENV_FILE 파일이 없습니다. 'cp .env.example .env' 후 값을 채우세요."
fi

# [0/4] 배포 전 CI 게이트 — lock 동기 + 테스트. 실패하면 set -e 로 배포 중단(깨진 빌드
# 가 원격에 올라가 컨테이너가 무한재시작하는 사고 방지). 건너뛰려면 SKIP_CI=1.
if [ "$SKIP_CI" != "1" ]; then
  echo "[0/4] CI 게이트"
  ./scripts/ci.sh
fi

REMOTE="$DEPLOY_REMOTE"
PORT="$DEPLOY_PORT"
SSH_CMD=(ssh -p "$PORT")
RSH="ssh -p $PORT"

echo "[1/4] 원격 디렉터리 준비"
REMOTE_GUARD="
set -eu
if [ ! -e '$DEST' ]; then exit 0; fi
if [ ! -d '$DEST' ]; then exit 1; fi
if [ -f '$DEST/.claire-deploy-root' ] &&
   grep -qxF claire_bible '$DEST/.claire-deploy-root'; then
  exit 0
fi
if [ -f '$DEST/docker-compose.yml' ] &&
   grep -Eq '^[[:space:]]*container_name:[[:space:]]*claire_bot[[:space:]]*$' \
     '$DEST/docker-compose.yml' &&
   [ -f '$DEST/pyproject.toml' ] &&
   grep -Eq '^[[:space:]]*name[[:space:]]*=[[:space:]]*\"claire\"[[:space:]]*$' \
     '$DEST/pyproject.toml' &&
   [ -d '$DEST/src/claire' ]; then
  exit 0
fi
unexpected=\$(find '$DEST' -mindepth 1 -maxdepth 1 \
  ! -name data ! -name vault ! -name .env -print -quit)
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
   [ "$REMOTE_ENV_EXISTS" -eq 0 ] && [ ! -f "$ENV_SOURCE" ]; then
  fail "로컬 ${DEPLOY_ENV_FILE}과 원격 $DEST/.env가 모두 없습니다."
fi
if [ "$DEPLOY_ENV_SYNC" = "never" ] && [ "$REMOTE_ENV_EXISTS" -eq 0 ]; then
  fail "DEPLOY_ENV_SYNC=never에는 기존 원격 $DEST/.env가 필요합니다."
fi

"${SSH_CMD[@]}" "$REMOTE" \
  "mkdir -p -- '$DEST/data' '$DEST/vault' && printf '%s\n' claire_bible > '$DEST/.claire-deploy-root'"

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
  --include '/.env.example' \
  --exclude '.env' \
  --exclude '.env.*' \
  --exclude '.claire-deploy-root' \
  ./ "${REMOTE}:${DEST}/"

sync_env() {
  [ -f "$ENV_SOURCE" ] || fail \
    "$DEPLOY_ENV_FILE 파일이 없어 원격 .env를 만들 수 없습니다."
  rsync -az --chmod=F600 -e "$RSH" -- "$ENV_SOURCE" "$REMOTE:$DEST/.env"
  "${SSH_CMD[@]}" "$REMOTE" "chmod 600 '$DEST/.env'"
}

echo "[3/4] .env 동기화 ($DEPLOY_ENV_SYNC)"
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

# 보안 기본값 전환 뒤에도 기존 서비스를 재시작 루프에 넣지 않도록, 새 이미지를
# 기동하기 전에 원격 .env를 검사한다. 값 자체는 원격 밖으로 출력하지 않는다.
if ! "${SSH_CMD[@]}" "$REMOTE" \
  "sh -s -- '$DEST/.env' claire-security-env-check" <<'CLAIRE_SECURITY_ENV_CHECK'
set -eu
env_file="$1"

dotenv_value() {
  awk -v wanted="$1" '
    function trim(value) {
      sub(/^[[:space:]]+/, "", value)
      sub(/[[:space:]]+$/, "", value)
      return value
    }
    {
      line = trim($0)
      if (line == "" || substr(line, 1, 1) == "#") {
        next
      }
      sub(/^export[[:space:]]+/, "", line)
      separator = index(line, "=")
      if (!separator || trim(substr(line, 1, separator - 1)) != wanted) {
        next
      }

      value = trim(substr(line, separator + 1))
      quote = substr(value, 1, 1)
      if (quote == "\"" || quote == "\047") {
        value = substr(value, 2)
        closing = index(value, quote)
        trailing = closing ? trim(substr(value, closing + 1)) : "invalid"
        if (!closing || (trailing != "" && substr(trailing, 1, 1) != "#")) {
          result = ""
          found = 1
          next
        }
        value = substr(value, 1, closing - 1)
      } else {
        sub(/[[:space:]]+#.*$/, "", value)
        value = trim(value)
      }
      result = value
      found = 1
    }
    END {
      if (!found) {
        exit 1
      }
      print result
    }
  ' "$env_file"
}

[ -f "$env_file" ] || {
  echo "deploy: 원격 .env가 없습니다." >&2
  exit 41
}

inject_token="$(dotenv_value CLAIRE_INJECT_TOKEN || true)"
allowed_users="$(dotenv_value CLAIRE_ALLOWED_USERS || true)"
allow_all_users="$(dotenv_value CLAIRE_ALLOW_ALL_USERS || true)"

if [ -z "$inject_token" ]; then
  echo "deploy: CLAIRE_INJECT_TOKEN이 비어 있습니다." >&2
  exit 42
fi

if [ -n "$allowed_users" ]; then
  if ! printf '%s\n' "$allowed_users" |
    grep -Eq '^[[:space:]]*[0-9]+([[:space:]]*,[[:space:]]*[0-9]+)*[[:space:]]*$'; then
    echo "deploy: CLAIRE_ALLOWED_USERS는 숫자 ID의 쉼표 목록이어야 합니다." >&2
    exit 43
  fi
else
  allow_all_users="$(printf '%s' "$allow_all_users" | tr '[:upper:]' '[:lower:]')"
  case "$allow_all_users" in
    1|true|yes|on) ;;
    *)
      echo "deploy: CLAIRE_ALLOWED_USERS가 비어 있고 전체 허용도 명시되지 않았습니다." >&2
      exit 44
      ;;
  esac
fi
CLAIRE_SECURITY_ENV_CHECK
then
  fail "원격 .env 보안 설정을 보완한 뒤 다시 실행하세요. 기존 컨테이너는 변경하지 않았습니다."
fi

echo "[4/4] 컨테이너 재빌드 & 기동"
"${SSH_CMD[@]}" "$REMOTE" "cd '$DEST' && docker compose up -d --build"
"${SSH_CMD[@]}" "$REMOTE" "cd '$DEST' && docker compose ps"
echo "배포 완료."
