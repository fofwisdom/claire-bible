# claire_bible

텔레그램으로 던진 링크/문서/키워드를 **스크랩 → Gemini로 구조화 → 팔란티어식 타입 온톨로지 그래프**로 적재하고,
새 자료를 **기존에 쌓인 그래프와 연결**하며, 나중에 키워드로 **검색 → LLM 정리**해 보여주는 **개인용 지식베이스**.

- 설계 상세: [PLAN.md](PLAN.md) · 비전·로드맵: [GOALS.md](GOALS.md)

## 상태

v1 파이프라인 완성 + 개인용 컨테이너 운영 구조. 단일 사용자 전용이며 멀티테넌시는
범위 밖이다([GOALS.md](GOALS.md) 참조). 자동복구·헬스·circuit breaker·능동 알림을
제공한다.

## 빠른 시작 (로컬)

```bash
uv sync                      # 의존성 설치
cp .env.example .env         # 토큰/키 채우기 (GEMINI_API_KEY 없으면 mock provider 로 동작)
uv run claire doctor         # 환경/벡터백엔드/임베딩 점검
uv run claire health         # 시스템 건강 상태(JSON): DB·schema·큐·inbox
uv run claire ingest "https://example.com/article"   # 단건 적재
uv run claire search "키워드"                          # 하이브리드 검색 + LLM 정리
uv run claire bot            # 텔레그램 봇 (long-polling)
```

## CLI 명령

| 명령 | 설명 |
|---|---|
| `doctor` / `health` / `status` / `stats` | 환경 점검 / 건강 JSON / 현황 / 그래프 카운트 |
| `migrate` / `liveness` | 명시적 DB migration / 읽기 전용 DB·schema 생존 확인 |
| `ingest <payload> [--expand]` | 단건 적재(URL/텍스트/파일) |
| `search <q> [--no-summary]` | FTS+벡터 하이브리드 검색 + Gemini 정리(인용) |
| `bot` / `serve-api` | 텔레그램 봇 / 로컬 inject API |
| `recover-run` / `recover-loop` | error inbox 자동 재적재(게이팅·지수백오프·영구실패 구분) |
| `refresh-mark` / `refresh-run` / `refresh-loop` | 빈약/구버전 문서 재스크랩(복원) |
| `replay-failed` | error inbox 수동 전량 재적재 |

## 컨테이너 운영

호스트 수명주기는 루트의 `cb-manuscript`로만 조작한다. `claire`는 컨테이너 안의
애플리케이션 명령이고, `cb-manuscript`는 설치·업데이트·Compose 실행을 담당한다.
호스트에는 Python 3, Git, 실행 중인 Docker Engine과 Docker Compose v2가 필요하다.

```bash
./cb-manuscript init       # .env/.env.dev 생성(기존 파일 미덮어쓰기)
# .env에 Telegram/Gemini 설정 입력
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
./cb-manuscript health
./cb-manuscript logs -f api
./cb-manuscript shell
./cb-manuscript app status
./cb-manuscript compose -- ps      # Compose 고급 탈출구
./cb-manuscript dev install        # .env.dev + 격리된 .dev/data·vault
```

`update`는 dirty worktree와 non-fast-forward 갱신을 거부한다. 새 이미지 build가 성공한
뒤 현재 project와 이전 고정 이름 컨테이너를 중지하고 migration을 한 번만 실행한다.
SQLite migration 중에는 짧은 쓰기 중단이 발생한다. migration 전에 실패하면 직전에
실행 중이던 컨테이너만 다시 시작한다. 새 스택 기동 이후 실패는 진단을 위해 그 상태를
유지하며 자동 rollback으로 오인하지 않는다. 백업·복원은 이번 통합 운영 구조의 범위에
포함하지 않으며 별도로 다시 설계한다.

환경 파일:

| 파일 | 역할 |
|---|---|
| `.env` | 운영 runtime·Compose 설정과 secret |
| `.env.dev` | 개발 project·포트·데이터 경로 override |
| `.env.deploy` | 선택적인 SSH/rsync 접속 설정. 컨테이너에는 전달하지 않음 |

Compose project 이름은 `CB_PROJECT_NAME`으로 고정한다. 운영은 기본 `claire-bible`,
개발은 `claire-bible-dev`이며 고정 `container_name`을 사용하지 않는다. 설치 후 이름이
바뀌면 중복 writer 방지를 위해 명령이 거부된다. 이전 이름으로 `down`을 완료한 뒤 표시된
상태 파일을 제거해야 이름을 전환할 수 있다.

5개 서비스는 같은 이미지와 `data`·`vault`를 공유한다.

| 서비스 | 역할 |
|---|---|
| `bot` | 선택적 Telegram long-polling |
| `api` | inject API·웹 UI, 기본 `127.0.0.1:8765` |
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
`.env` 동기화 정책을 정한다.

### 장애 대응

- **추출 실패(Gemini 429/quota/크레딧 소진)**: 원본은 `raw_inbox` error 로 보관(유실 0).
  `recover`가 지수백오프로 자동 재적재. 영구실패(`failed`) 누적 시 텔레그램으로 소유자 경보.
  크레딧 충전 등으로 회복되면 due 항목이 자동 복구된다.
- **건강 점검**: `./cb-manuscript health` 또는 `claire health`의 `degraded`,
  `attention` 필드를 본다.

## 구조

```
src/claire/
  config.py        설정(.env)
  cli.py           CLI 진입점
  telegram_bot.py  텔레그램 진입점
  api/             로컬 inject API (aiohttp)
  health.py        건강 상태 산출(/health · CLI 공유)
  notify.py        텔레그램 소유자 경보
  ingest/          fetcher 라우터 + normalize + dedup + IngestService(공유 통로) + 자동복구
  ontology/        타입 온톨로지(코드 인터페이스) + registry(domain/range)
  extract/         Gemini structured 추출 + provider 어댑터(mock/gemini) + resolver(약어 동의어 수렴) + circuit breaker
  store/           SQLite(graph+FTS+vec) + 마이그레이션 + vault(.md) export
  expand/          1홉 자동 확장
  retrieval/       하이브리드 검색 + LLM 정리
```
