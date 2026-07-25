# Claire Bible

텔레그램으로 던진 링크/문서/키워드를 **스크랩 → Gemini로 구조화 → 팔란티어식 타입 온톨로지 그래프**로 적재하고,
새 자료를 **기존에 쌓인 그래프와 연결**하며, 나중에 키워드로 **검색 → LLM 정리**해 보여주는 **개인용 지식베이스**.

- 설계 상세: [PLAN.md](PLAN.md) · 비전·로드맵: [GOALS.md](GOALS.md)

## 상태

v1 파이프라인 완성 + 개인용 컨테이너 운영 구조. 단일 사용자 전용이며 멀티테넌시는
범위 밖이다([GOALS.md](GOALS.md) 참조). 자동복구·헬스·circuit breaker·능동 알림을
제공한다.

## 로컬 소스 개발

```bash
uv sync                      # 의존성 설치
cp .env.example .env         # 토큰/키 채우기 (GEMINI_API_KEY 없으면 mock provider 로 동작)
uv run claire doctor         # 환경/벡터백엔드/임베딩 점검
uv run claire health         # 시스템 건강 상태(JSON): DB·schema·큐·inbox
uv run claire ingest "https://example.com/article"   # 단건 적재
uv run claire search "키워드"                          # 하이브리드 검색 + LLM 정리
uv run claire bot            # 텔레그램 봇 (long-polling)
```

`uv run claire ...`는 현재 checkout과 가상환경을 사용하는 로컬 개발·테스트 경로다.
배포된 컨테이너의 데이터를 조회하거나 변경할 때는 이 경로와 섞지 않고
`./cb-manuscript app ...`을 사용한다.

## 애플리케이션 CLI (`claire`)

`claire`는 컨테이너 내부 프로세스와 로컬 개발이 공유하는 애플리케이션 진입점이다.
로컬 checkout에서는 `uv run claire ...`로, 배포 환경의 one-off 작업은 호스트에서
`./cb-manuscript app ...`으로 실행한다.

| 명령 | 설명 |
|---|---|
| `doctor` / `health` / `status` / `stats` | 환경 점검 / 건강 JSON / 현황 / 그래프 카운트 |
| `migrate` / `liveness` | 명시적 DB migration / 읽기 전용 DB·schema 생존 확인 |
| `ingest <payload> [--expand]` | 단건 적재(URL/텍스트/파일) |
| `search <q> [--no-summary]` | FTS+벡터 하이브리드 검색 + Gemini 정리(인용) |
| `bot` / `serve-api` | 텔레그램 봇 / Starlette·Uvicorn 웹 서비스 |
| `recover-run` / `recover-loop` | error inbox 자동 재적재(게이팅·지수백오프·영구실패 구분) |
| `refresh-mark` / `refresh-run` / `refresh-loop` | 빈약/구버전 문서 재스크랩(복원) |
| `replay-failed` | error inbox 수동 전량 재적재 |

## 컨테이너 운영

배포된 인스턴스의 호스트 수명주기는 루트의 `cb-manuscript`로만 조작한다.
`cb-manuscript`는 `.env`, 설치·업데이트와 Compose를 담당하고,
`cb-manuscript app`은 같은 배포 설정과 데이터로 `claire` one-off 명령을 실행한다.
영속 서비스의 컨테이너 내부 명령은 Compose가 직접 `claire`를 호출한다. 세부 경계와
health 종료 코드 차이는 [운영 명령 경계](docs/OPERATIONS.md)를 참고한다.

호스트에는 Python 3, Git, 실행 중인 Docker Engine과 Docker Compose v2가 필요하다.

```bash
./cb-manuscript init       # .env/.env.dev 생성(기존 파일 미덮어쓰기)
# .env에 hostname/LAN bind/Telegram/Gemini 설정 입력
./cb-manuscript doctor     # Docker·Compose·설정 사전 검사
./cb-manuscript install    # build → migrate → up → health
./cb-manuscript status
```

`TELEGRAM_BOT_TOKEN`이 비어 있으면 `bot` profile은 기동하지 않는다. API와 worker는
mock provider로 설치·검증할 수 있다.

주요 명령:

```bash
./cb-manuscript update             # fast-forward source → build → stop → migrate → up
./cb-manuscript update --no-fetch  # 이미 동기화된 소스로 재배치
./cb-manuscript up
./cb-manuscript down
./cb-manuscript restart
./cb-manuscript backup                    # backups/cb-YYYYMMDD/
./cb-manuscript backup --format archive   # backups/cb-YYYYMMDD.tar.gz
./cb-manuscript restore backups/cb-YYYYMMDD --yes
./cb-manuscript health
./cb-manuscript logs -f api
./cb-manuscript shell
./cb-manuscript app --help          # 배포 이미지의 전체 앱 명령 확인
./cb-manuscript app status         # 배포된 앱의 one-off 상태 조회
./cb-manuscript app health         # degraded까지 평가하는 전체 health
./cb-manuscript compose -- ps      # 고급 Compose 탈출구
CLAIRE_ENVIRONMENT=development ./cb-manuscript install
./cb-manuscript dev install        # 위 development 선택의 호환 별칭
```

`CLAIRE_ENVIRONMENT`는 `development` 또는 `production` 중 하나가 반드시 필요하다.
bare 명령의 환경 선택은 프로세스 값을 먼저 본다. 다만 설정 파일의 역할까지 바꾸지는
않으므로 `.env`는 `production`, `.env.dev`는 `development`를 선언해야 한다.
development가 선택되면 `.env` 다음에 `.env.dev`와 개발 Compose overlay를 적용한다.
기존 `dev` prefix는 development 별칭으로 유지하지만 프로세스 환경이 production이면
충돌로 중단한다. `CLAIRE_PUBLIC_URL`과 CORS 목록은 선택된 env 파일의 값을 검사하고
그대로 컨테이너에 전달한다.

기존 설치를 처음 이 구조로 올릴 때는 lifecycle 명령 전에 `./cb-manuscript init`을 한
번 다시 실행한다. 기존 secret과 비어 있지 않은 값은 유지하면서 누락된 환경 selector만
보충한다. 그 뒤 production `.env`에는 실제 외부 hostname의
`CLAIRE_PUBLIC_URL=https://.../`을 반드시 설정하고, 필요할 때만 exact HTTPS origin을
`CLAIRE_CORS_ALLOWED_ORIGINS`에 넣는다.

`app`, `shell`, 고급 `compose` one-off는 인스턴스 잠금을 잡아 lifecycle 및 백업·복원과
동시에 실행되지 않는다. migration, Compose 관리 daemon과 파괴적 유지보수는 실수로
실행되지 않도록 기본 차단된다. `app --advanced ...`는 전문가용 raw passthrough이며
서비스 정지, migration 순서, 백업 또는 복구 가능성을 보장하지 않는다.

백업은 현재 profile의 `data`와 `vault`를 writer 정지 상태에서 함께 캡처하고,
SQLite snapshot·`quick_check`·foreign-key 검사·SHA-256 manifest를 검증한 뒤에만
공개한다. 기본은 폴더이고 `--format archive`는 `.tar.gz` 파일을 만든다.
`--component data` 또는 `--component vault`로 일부만 선택할 수 있다. 같은 날짜 산출물은
묵시적으로 덮어쓰지 않으며 새 상태로 교체하려면 `--replace`가 필요하다. `.env`의
secret과 호스트 topology는 v1 backup에 포함하지 않는다.

복원은 파일 또는 폴더를 자동 판별하며 profile·project·hash·SQLite를 서비스 정지 전에
검증한다. `--yes`가 필요하고, 선택한 component를 교체한 뒤 migration과 liveness까지
성공해야 완료한다. 실패하면 직전 data/vault를 되돌리고 원래 실행 중이던 컨테이너만
재개한다.

`./cb-manuscript health`는 실행 중인 API 컨테이너의 DB·schema liveness를 확인한다.
주의 항목이 누적된 `degraded` 상태도 출력하지만 liveness가 정상이면 성공한다.
`./cb-manuscript app health`는 전체 애플리케이션 상태를 평가하므로 `degraded`이면
종료 코드 1을 반환한다.

`update`는 dirty worktree와 non-fast-forward 갱신을 거부한다. 새 이미지 build가 성공한
뒤 현재 project와 이전 고정 이름 컨테이너를 중지하고 migration을 한 번만 실행한다.
SQLite migration 중에는 짧은 쓰기 중단이 발생한다. migration 전에 실패하면 직전에
실행 중이던 컨테이너만 다시 시작한다. 새 스택 기동 이후 실패는 진단을 위해 그 상태를
유지하며 자동 rollback으로 오인하지 않는다. 이 update 실패 정책은 별도의
`cb-manuscript restore` component rollback과 구분한다.

환경 파일:

| 파일 | 역할 |
|---|---|
| `.env` | production 기본 runtime·Compose 설정과 secret |
| `.env.dev` | development project·포트·데이터 경로 override |
| `.env.deploy` | production SSH/rsync 접속 설정. 컨테이너에는 전달하지 않음 |

Compose project 이름은 `CB_PROJECT_NAME`으로 고정한다. 운영은 기본 `claire-bible`,
개발은 `claire-bible-dev`이며 고정 `container_name`을 사용하지 않는다. 설치 후 이름이
바뀌면 중복 writer 방지를 위해 명령이 거부된다. 이전 이름으로 `down`을 완료한 뒤 표시된
상태 파일을 제거해야 이름을 전환할 수 있다.

5개 서비스는 같은 이미지와 `data`·`vault`를 공유한다.

| 서비스 | 역할 |
|---|---|
| `bot` | 선택적 Telegram long-polling |
| `api` | ASGI API·웹 UI. 컨테이너는 전체 interface에서 듣고 호스트는 `CB_API_BIND`의 정확한 IPv4에만 게시 |
| `refresh` | 갱신 큐 처리 |
| `recover` | error inbox 자동 재적재 |
| `expand` | 1홉 자동확장 큐 처리 |

### 원격 호환 실행

워크스테이션에서 원격 호스트로 전송해야 하면 접속 설정을 runtime `.env`와 분리한다.
워크스테이션에는 SSH, rsync와 CI 실행용 `uv`도 필요하다.

```bash
cp .env.deploy.example .env.deploy
# DEPLOY_REMOTE, DEPLOY_PATH 입력
./cb-manuscript remote install
./cb-manuscript remote update
```

원격 전송은 `deploy.sh` 호환 계층을 사용하지만 실제 컨테이너 lifecycle은 원격의
`cb-manuscript`가 수행한다. `DEPLOY_ENV_SYNC=if-missing|always|never`로 원격 runtime
`.env` 동기화 정책을 정한다. 원격 install/update는 production 전용이며 로컬과 원격
명령 모두 `CLAIRE_ENVIRONMENT=production`으로 고정된다.

웹 접속은 [외부 접속과 reverse proxy](docs/EXTERNAL_ACCESS.md)를 따른다. development는
고정 IPv4로 직접 HTTP 접속하고, production은 별도 LAN reverse proxy가 hostname과
클라이언트 TLS를 담당한 뒤 Claire의 HTTP upstream으로 전달한다. Claire 자체 HTTPS,
인증서 발급과 Let's Encrypt는 제공하지 않는다.

### 장애 대응

- **추출 실패(Gemini 429/quota/크레딧 소진)**: 원본은 `raw_inbox` error 로 보관(유실 0).
  `recover`가 지수백오프로 자동 재적재. 영구실패(`failed`) 누적 시 텔레그램으로 소유자 경보.
  크레딧 충전 등으로 회복되면 due 항목이 자동 복구된다.
- **기동 여부 확인**: `./cb-manuscript health`로 API 컨테이너의 liveness를 확인한다.
- **주의 상태 진단**: `./cb-manuscript app health`의 `degraded`, `attention` 필드를
  확인한다. `degraded`이면 명령도 실패로 종료한다.

## 구조

```
src/claire/
  config.py        설정(.env)
  cli.py           CLI 진입점
  telegram_bot.py  텔레그램 진입점
  api/             ASGI API와 웹 UI
  health.py        건강 상태 산출(/health · CLI 공유)
  notify.py        텔레그램 소유자 경보
  ingest/          fetcher 라우터 + normalize + dedup + IngestService(공유 통로) + 자동복구
  ontology/        타입 온톨로지(코드 인터페이스) + registry(domain/range)
  extract/         Gemini structured 추출 + provider 어댑터(mock/gemini) + resolver(약어 동의어 수렴) + circuit breaker
  store/           SQLite(graph+FTS+vec) + 마이그레이션 + vault(.md) export
  expand/          1홉 자동 확장
  retrieval/       하이브리드 검색 + LLM 정리
```
