# 운영 명령 경계

배포된 Claire 인스턴스는 `cb-manuscript`를 호스트 운영 진입점으로 사용한다.
애플리케이션 CLI인 `claire`는 유지하되 실행 환경에 따라 다음 표면을 구분한다.

| 명령 표면 | 용도 | 실행 환경 |
|---|---|---|
| `./cb-manuscript <command>` | 설정, 설치·업데이트, 컨테이너 수명주기 | 배포 호스트 |
| `./cb-manuscript app <command>` | 배포 설정·데이터를 사용하는 앱 one-off 작업 | 임시 `api` 컨테이너 |
| `uv run claire <command>` | 현재 checkout의 로컬 개발·테스트 | 호스트 가상환경 |
| `claire <command>` | 서비스 프로세스와 앱 기능의 내부 진입점 | 컨테이너 내부 |

## 호스트 운영

다음 작업은 `cb-manuscript` 최상위 명령으로 수행한다.

```bash
./cb-manuscript init
./cb-manuscript preflight
./cb-manuscript install
./cb-manuscript update
./cb-manuscript up
./cb-manuscript down
./cb-manuscript restart
./cb-manuscript backup
./cb-manuscript restore backups/cb-YYYYMMDD --yes
./cb-manuscript status
./cb-manuscript logs -f api
./cb-manuscript health
```

이 계층은 `CLAIRE_ENVIRONMENT`, env overlay, Compose project, 서비스 profile,
업데이트 잠금과 migration 순서를 일관되게 적용한다. 배포 인스턴스를 직접
`docker compose`나 호스트의 `uv run claire`로 관리하지 않는다.

`CLAIRE_ENVIRONMENT`는 exact `development` 또는 `production` 값이 필수다. bare
명령의 선택은 프로세스 값을 먼저 보지만 파일의 역할을 재해석하지는 않는다. `.env`는
`production`, `.env.dev`는 `development`를 선언해야 한다. development가 선택되면
`.env` 다음에 `.env.dev`와 `docker-compose.dev.yml`을 적용한다. public URL과 CORS는
선택된 env 파일의 값이 사전 검사와 컨테이너 실행에 똑같이 사용된다.
`CLAIRE_ANONYMOUS_READONLY`도 host process 값이 아니라 선택된 파일의 exact `0|1`만
사용한다.

```bash
CLAIRE_ENVIRONMENT=development ./cb-manuscript up
CLAIRE_ENVIRONMENT=production ./cb-manuscript up
```

`./cb-manuscript dev <command>`는 development의 기존 호환 별칭이다. 프로세스
`CLAIRE_ENVIRONMENT=production`과 함께 사용하면 추측하지 않고 실패한다. Docker
development는 현재 checkout에서 직접 실행하는 `uv run claire <command>`와 다른
환경이며, 별도의 project·데이터 경로를 사용한다.

### 기존 env 파일 마이그레이션 및 자동 백필

이 구조를 적용하기 전부터 `.env` 또는 `.env.dev`가 있더라도, `./cb-manuscript update` 또는 `./cb-manuscript install`, `./cb-manuscript init` 실행 시 누락된 신규 환경변수(예: `TZ` 등)가 `.env.example` / `.env.dev.example`로부터 자동으로 안전하게 백필됩니다 (자세한 설계 규약은 [../design/OPERATIONAL_MIGRATION.md](../design/OPERATIONAL_MIGRATION.md) 참조).

이 과정은 기존 secret과 사용자가 설정한 값을 절대 덮어쓰지 않으며, 누락된 selector를 `.env=production`, `.env.dev=development`로 보충하고, `TZ`는 호스트 `timedatectl` 타임존으로 자동 채우며, `CLAIRE_ANONYMOUS_READONLY=1`도 각각 보충합니다. production에서 `1`인데 development 파일에 값이 없으면 공개 설정의 암묵적 상속을 막기 위해 기동 전 실패합니다. production `.env`의 빈 `CLAIRE_PUBLIC_URL`은 추측해서 채우지 않으므로 실제 외부 hostname의 `https://.../` 값으로 직접 설정해야 합니다. CORS가 필요 없으면 `CLAIRE_CORS_ALLOWED_ORIGINS`는 생략하거나 빈 값으로 둡니다.

`CB_API_BIND`는 Docker host가 게시할 정확한 IPv4 주소다. `0.0.0.0`, multicast,
hostname과 IPv6는 사전 검사에서 거부한다. loopback은 안전한 초기값으로 허용하지만
다른 LAN 호스트에서 접근하려면 실제 고정 LAN IPv4로 변경해야 한다.

- development의 `CLAIRE_PUBLIC_URL`은
  `http://<CB_API_BIND>:<CB_API_PORT>/`와 authority가 정확히 같아야 한다.
- production은 root 경로의 `https://<DNS-hostname>/`이어야 한다. TLS는 Claire가 아닌
  외부 reverse proxy가 종료한다.
- `CLAIRE_CORS_ALLOWED_ORIGINS`는 path와 wildcard가 없는 origin의 쉼표 목록이다.
  production에서는 `https` origin만 허용하며 빈 값은 same-origin 전용이다.
- exact `CLAIRE_ANONYMOUS_READONLY=1`(기본값)은 canonical same-origin 또는 Origin 헤더가 없는
  무자격증명 요청의 읽기 전용 접근을 허용한다. owner 쓰기는 계속 유효하며, 숨김 문서(`hidden=1`) 및
  그와 연관된 엔티티는 익명 읽기 계층에서 철저히 제외되어 안전하게 공개된다. `0`은 인증 전용 동작이다.

`./cb-manuscript preflight`는 선택 profile의 anonymous readonly 상태를 출력한다. enabled
표시는 의도적인 공개 결정인지 확인해야 할 운영 경보다.

### 익명 읽기 배포와 롤백

먼저 `CLAIRE_ANONYMOUS_READONLY=0`인 채로 코드를 업데이트하고
`./cb-manuscript init`, `./cb-manuscript preflight`를 통과시킨다. reverse proxy의
`/search` per-IP 제한과 backend 방화벽을 확인한 다음, trusted LAN 또는 VPN에만
노출되는 profile의 값을 `1`로 바꾸고 `./cb-manuscript up`으로 재기동한다.

롤백은 같은 profile의 값을 `0`으로 되돌리고 다시 `./cb-manuscript up`을 실행한다.
이 모드의 보장 범위는 API 시작 완료 뒤의 익명 HTTP 요청뿐이다. 시작 migration과
별도 worker, Telegram bot, CLI의 쓰기는 계속 동작하며 범위 밖이다.

### 비디오 음성 전사 (STT) 운영 및 환경변수

웹 비디오(VMware Explore/Brightcove, YouTube 등)의 음성 전사(STT) 기능은 컨테이너에 내장된 `ffmpeg`와 `yt-dlp` 및 STT 프로바이더(프로덕션 권장: `gemini` - `gemini-3.5-transcribe`)를 통해 동작합니다.

* **`CLAIRE_ENABLE_VIDEO_TRANSCRIPTION=1` (기본값: 활성)**:
  * 비디오 URL 적재 시 내장 자막이 없으면 오디오 스트림을 추출하여 STT로 타임스탬프 자막을 생성합니다.
  * `0`으로 설정 시 무거운 오디오 다운로드/STT를 건너뛰고 비디오 페이지의 메타데이터만 수집하여 경량 문서로 적재합니다.
* **`CLAIRE_STT_PROVIDER=gemini` (또는 `STT_PROVIDER=gemini`)**:
  * `gemini`: Google GenAI SDK 기반 고성능 STT (기본 모델: `gemini-3.5-transcribe`).
  * `antigravity`: 호스트 Antigravity CLI 기반 폴백.
* **`CLAIRE_STT_MODEL=gemini-3.5-transcribe` (또는 `STT_MODEL=gemini-3.5-transcribe`)**:
  * 최신 전문 음성 전사 전용 모델 사용.
* **`CLAIRE_VIDEO_CHUNK_DURATION_SEC=240`**:
  * `gemini-3.5-transcribe`의 분당 10K 입력 토큰(TPM) 한도를 준수하기 위해 단일 청크를 240초(4분, 약 6,000 토큰)로 제한.
  * 청크 간 최소 62초 슬라이딩 윈도우 페이싱(Pacing)을 적용하여 1분당 1개 청크만 안전하게 호출.
  * 429 응답 발생 시 API 응답 헤더의 `retryDelay`를 파싱하여 해당 시간 대기 후 최대 5회 자동 재시도.
* **`CLAIRE_VIDEO_CACHE_TTL_SEC=259200` (사흘 = 3일 보존)**:
  * 다운로드 후 STT 실패 시 대용량 오디오 미디어를 `data/cache/video/`에 3일간 자동 보존.
  * 재처리(`video-reprocess`) 실행 시 원격 다운로드를 생략하고 로컬 캐시를 즉시 재사용.
  * 1KB 미만의 손상/더미 파일은 자동 삭제되며, 캐시 STT 실패 시에도 손상 캐시를 즉시 삭제하고 원격 재다운로드로 자동 폴백.
* **비디오 재전사 실행 경로**:
  1. **CLI 실행**:
     ```bash
     ./cb-manuscript app video-reprocess --doc-id <doc_id> --apply
     ```
     터미널에서 단계별 진행 상황(`[원문 전체 재수집]`, `[오디오 변환]`, `[STT 청크 N/M 전사]`, `[LLM 요약 및 본문 렌더링]`)이 실시간 스트리밍 출력됩니다.
  2. **텔레그램 봇 실행**:
     - 텔레그램 봇 채팅방에 문서 공유 링크(`https://.../p?s=...`) 또는 `doc_id` 전송 후 **`[ 🌐 전체 원문 재수집 (전체 길이) ]`** 버튼 클릭.
     - 또는 `doc_id --refetch-full` 한 줄 메시지 전송으로 즉시 원스톱 백그라운드 재전사 실행.

## 배포된 앱의 one-off 명령

배포된 인스턴스와 같은 설정·데이터로 앱 명령을 한 번 실행할 때
`cb-manuscript app`을 사용한다.

```bash
./cb-manuscript app doctor
./cb-manuscript app status
./cb-manuscript app stats
./cb-manuscript app search "키워드"
./cb-manuscript app health
./cb-manuscript app --help
```

`app`에서 실행이 허용된 앱 명령은 인수와 종료 코드를 그대로 전달한다. `bot`,
`serve-api`, `*-loop`처럼 계속 실행되는 서비스는 `app`으로 중복 기동하지 않고
Compose 수명주기에 맡긴다. 설치·업데이트·migration처럼 서비스 정지 순서가 필요한
작업도 해당 `cb-manuscript` 최상위 흐름을 사용한다.

모든 `app` 실행은 인스턴스 잠금을 획득하므로 `install`, `update`, `up`, `down`,
`restart`와 동시에 진행되지 않는다. 다음 작업은 일반 one-off가 아니므로 기본
차단된다.

- lifecycle: `migrate`
- Compose 관리 서비스: `bot`, `serve-api`, `recover-loop`, `refresh-loop`,
  `expand-loop`
- 파괴적·영속 유지보수: `reextract`, `dedup-merge --apply`,
  적용 모드의 `recanonicalize`

구현 조사나 긴급 복구처럼 raw 앱 명령이 반드시 필요하면 명령 바로 뒤에
`--advanced`를 명시할 수 있다.

```bash
./cb-manuscript app --advanced <claire-command> [args...]
```

이는 차단만 해제하는 전문가용 탈출구다. 인스턴스 잠금은 유지하지만 이미 실행 중인
서비스를 중지하지 않으며, migration 순서, 데이터 백업 또는 복구 가능성을 보장하지
않는다. 인스턴스 백업·복원은 최상위 `backup`, `restore`만 사용한다.

## health의 두 의미

```bash
./cb-manuscript health
./cb-manuscript app health
```

- `cb-manuscript health`는 실행 중인 `api` 컨테이너에서 `claire liveness`를 호출한다.
  DB 접근과 현재 schema가 정상이면 성공한다. 출력에 `degraded` 진단이 있더라도
  liveness 성공 여부에는 반영하지 않는다.
- `cb-manuscript app health`는 임시 컨테이너에서 전체 애플리케이션 health를 계산한다.
  DB·큐·inbox 상태를 출력하며, `degraded`이면 종료 코드 1을 반환한다.

따라서 배포 직후와 감시용 생존 확인에는 전자를, 누적 실패와 사람의 조치가 필요한
상태 진단에는 후자를 사용한다.

## 고급 Compose 탈출구

일반 운영 명령이 제공하지 않는 Compose 기능이 필요할 때만 다음 탈출구를 사용한다.

```bash
./cb-manuscript compose -- ps
./cb-manuscript compose -- config
```

이 경로도 올바른 env 파일과 Compose project를 선택하지만, `install`·`update`가
보장하는 작업 순서까지 대신 적용하지는 않는다. 서비스 기동·중지와 migration은
가능하면 전용 최상위 명령을 사용한다.

원격 `remote install/update`는 production 전용이다. `dev remote` 또는 프로세스
`CLAIRE_ENVIRONMENT=development`는 원격 접속 전에 실패하며, 허용된 배포는 로컬
`deploy.sh`와 원격 `cb-manuscript` 모두에 production 값을 명시적으로 전달한다.
기본 `DEPLOY_ENV_SYNC=if-missing`은 기존 원격 `.env`를 유지하므로 코드 update만으로
anonymous readonly가 켜지지 않는다. 원격 파일을 직접 수정하거나 `always` 동기화를
명시한 뒤 원격 `preflight` 출력으로 유효값을 확인한다.

## 백업과 복원

```bash
# data + vault, 폴더 산출물
./cb-manuscript backup

# 단일 archive 파일
./cb-manuscript backup --format archive

# component 선택
./cb-manuscript backup --component data

# 파일 또는 폴더 자동 판별
./cb-manuscript restore backups/cb-20260725 --yes
./cb-manuscript restore backups/cb-20260725.tar.gz --component data --yes
```

산출물 이름은 현지 날짜 기준 `backups/cb-YYYYMMDD/` 또는
`backups/cb-YYYYMMDD.tar.gz`다. 같은 날의 파일과 폴더는 하나의 slot으로 간주한다.
기존 backup이 있으면 중단하며, 명시적인 `--replace`만 검증된 새 산출물로 교체한다.
자동 prune이나 보존 개수 정책은 두지 않는다.

기본 component는 `data`와 `vault`다. `data`의 기존 `backups`,
`offsite-backups`, 내부 `checkpoints`는 재귀 backup에서 제외한다. `.env`는 secret과
호스트 경로가 섞여 있으므로 v1 산출물에 포함하지 않는다. 재해 복구 전에 대상 호스트의
`.env`를 별도로 준비해야 한다.

backup은 현재 project와 exact-name legacy writer를 모두 중지하고 SQLite를 일관된
단일 파일로 snapshot한 뒤 전체 파일을 복사한다. manifest는 component, source revision,
DB schema, 파일 크기와 SHA-256을 기록한다. hash는 우발적 손상·단순 변조 탐지이며
서명이나 악의적 재작성 방지는 아니다.

restore는 archive traversal, link·특수 파일, manifest 밖의 파일, hash·DB 오류,
profile/project 불일치를 writer 정지 전에 거부한다. 승인된 component는 같은
filesystem의 sibling staging에서 준비하고 기존 경로와 교체한다. data 복원에는 현재
이미지의 migration을 적용한다. writer 재개와 liveness까지 실패하면 component 교체를
역순으로 rollback하고 이전 실행 상태만 재개한다. rollback 자체가 실패하면 writer를
중지하고 `.cb-manuscript/restore-transaction.json`을 남겨 수동 복구 경로를 보존한다.

`backups/`는 Git, Docker build context와 원격 `rsync --delete`에서 모두 제외한다.
운영 backup을 컨테이너 내부 `claire` 명령으로 만들거나 복원하지 않는다.
