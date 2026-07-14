# claire_bible

텔레그램으로 던진 링크/문서/키워드를 **스크랩 → Gemini로 구조화 → 팔란티어식 타입 온톨로지 그래프**로 적재하고,
새 자료를 **기존에 쌓인 그래프와 연결**하며, 나중에 키워드로 **검색 → LLM 정리**해 보여주는 **개인용 지식베이스**.

- 설계 상세: [PLAN.md](PLAN.md) · 비전·로드맵: [GOALS.md](GOALS.md) · 조사 근거: [research/RESEARCH_NOTES.md](research/RESEARCH_NOTES.md)

## 상태

v1 파이프라인 완성 + **프로덕션 견고화(트랙1) 완료** — 개인용 프로덕션급. 단일 사용자 전용(멀티테넌시는 범위 밖, [GOALS.md](GOALS.md) 참조).
자동복구·검증된 백업·헬스·circuit breaker·능동 알림을 갖춘 무중단 운영.

## 빠른 시작 (로컬)

```bash
uv sync                      # 의존성 설치
cp .env.example .env         # 토큰/키 채우기 (GEMINI_API_KEY 없으면 mock provider 로 동작)
uv run claire doctor         # 환경/벡터백엔드/임베딩 점검
uv run claire health         # 시스템 건강 상태(JSON): DB·큐·inbox·백업
uv run claire ingest "https://example.com/article"   # 단건 적재
uv run claire search "키워드"                          # 하이브리드 검색 + LLM 정리
uv run claire bot            # 텔레그램 봇 (long-polling)
```

## CLI 명령

| 명령 | 설명 |
|---|---|
| `doctor` / `health` / `status` / `stats` | 환경 점검 / 건강 JSON / 현황 / 그래프 카운트 |
| `ingest <payload> [--expand]` | 단건 적재(URL/텍스트/파일) |
| `search <q> [--no-summary]` | FTS+벡터 하이브리드 검색 + Gemini 정리(인용) |
| `bot` / `serve-api` | 텔레그램 봇 / 로컬 inject API |
| `recover-run` / `recover-loop` | error inbox 자동 재적재(게이팅·지수백오프·영구실패 구분) |
| `refresh-mark` / `refresh-run` / `refresh-loop` | 빈약/구버전 문서 재스크랩(복원) |
| `backup` / `backup-loop` | DB 스냅샷(VACUUM INTO) + 복원가능 검증 + 보존 정리 |
| `replay-failed` | error inbox 수동 전량 재적재 |

## 운영 (원격 Docker)

한 호스트에 이 Compose 스택 한 인스턴스를 실행하는 구성이다. 배포 대상은 코드에
고정하지 않고 로컬 `.env`에서 읽는다.

```dotenv
# .env — 앱 설정과 함께 입력
DEPLOY_REMOTE=alice@example.com       # user@host 또는 SSH config 별칭
DEPLOY_PORT=22
DEPLOY_PATH=/home/alice/claire_bible  # 원격 절대 경로
DEPLOY_ENV_SYNC=if-missing            # 최초 업로드 후 기존 원격 .env 보존
```

`DEPLOY_REMOTE`와 `DEPLOY_PATH`는 원격 배포 시 필수다. `DEPLOY_ENV_SYNC=if-missing`은
원격 `.env`가 이미 있으면 보존하고, `never`는 기존 원격 `.env`가 있을 때만 전송을
건너뛴다. 같은 이름의 프로세스 환경변수에 비어 있지 않은 값을 주면 `.env`보다
우선한다. `always`에서는 원격에서 직접 수정한 `.env`가 다음 배포 때 로컬 파일로
덮어써진다.

다른 파일을 쓰려면 `DEPLOY_ENV_FILE=.env.production ./deploy.sh`처럼 실행한다. 이
파일은 배포 접속 설정만 담는 파일이 아니라, 원격 `.env`로 복사될 앱 설정 전체를
포함해야 하고 파일명은 `.env` 또는 `.env.*` 형식이어야 한다. 처음 쓰는 경로는
없거나, 비어 있거나, `data`·`vault`·`.env`만 있어야 한다. 이후에는 배포 스크립트가
만든 마커를 검사해 `rsync --delete`가 상위 디렉터리를 지우는 오입력을 차단한다.

기존 설치를 전환할 때는 로컬 `.env`에 `DEPLOY_REMOTE`·`DEPLOY_PORT`·`DEPLOY_PATH`를
추가하고 첫 실행은 `if-missing`으로 둔다. 원격과 로컬 `.env`를 비교한 뒤 로컬 파일을
정본으로 관리할 때만 `always`로 바꾼다.

6개 컨테이너(모두 `restart: unless-stopped`, `data`·`vault` 볼륨 공유):

| 컨테이너 | 역할 |
|---|---|
| `claire_bot` | 텔레그램 long-polling |
| `claire_api` | 로컬 inject API (127.0.0.1:8765, bearer token) |
| `claire_refresh` | 갱신 큐 주기 처리(1시간) |
| `claire_recover` | error inbox 자동 재적재(10분, 지수백오프) |
| `claire_expand` | 1홉 자동확장 큐 주기 처리(15분) |
| `claire_backup` | 일일 DB 스냅샷 + 30개 보존 |

```bash
./deploy.sh                          # .env의 대상 → rsync + compose 재빌드
docker compose ps                    # 컨테이너 상태
docker exec claire_bot uv run claire health   # 건강 점검
```

### 백업 · 복구 런북

- **자동 백업**: `claire_backup` 컨테이너가 매일 `data/backups/claire-YYYYMMDD-HHMMSS.db` 스냅샷을 만들고
  생성 즉시 row count 가 live DB 와 일치하는지 검증한다. 최근 30개 보존.
- **수동 백업**: `docker exec claire_backup uv run claire backup`

**복원 절차** (DB 손상/실수 삭제 시):

```bash
cd ~/claire_bible
docker compose stop                                  # 1) 쓰기 멈춤
ls -t data/backups/                                  # 2) 복원할 스냅샷 선택(최신순)
cp data/backups/claire-YYYYMMDD-HHMMSS.db data/claire.db   # 3) 정본 교체
docker compose start                                 # 4) 재시작
docker exec claire_bot uv run claire health          # 5) graph counts 로 검증
```

### 장애 대응

- **추출 실패(Gemini 429/quota/크레딧 소진)**: 원본은 `raw_inbox` error 로 보관(유실 0).
  `claire_recover` 가 지수백오프로 자동 재적재. 영구실패(`failed`) 누적 시 텔레그램으로 소유자 경보.
  크레딧 충전 등으로 회복되면 due 항목이 자동 복구된다.
- **건강 점검**: `claire health` 의 `degraded=true` 또는 `attention` 필드(error/failed inbox)를 본다.

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
  store/           SQLite(graph+FTS+vec) + 마이그레이션 + vault(.md) export + 백업
  expand/          1홉 자동 확장
  retrieval/       하이브리드 검색 + LLM 정리
```
