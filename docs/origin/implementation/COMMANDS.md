# Claire Bible 전체 CLI 명령어 레퍼런스 (`COMMANDS.md`)

이 문서는 Claire Bible의 호스트 운영 도구인 **`cb-manuscript`**와 애플리케이션 핵심 CLI인 **`claire`**의 전체 명령어, 옵션, 동작 방식, 그리고 현재 구현 상태 및 제약사항을 상세히 기술합니다.

---

## 목차
1. [명령어 계층 및 실행 표면](#1-명령어-계층-및-실행-표면)
2. [호스트 운영 명령어 (`cb-manuscript`)](#2-호스트-운영-명령어-cb-manuscript)
   * [2.1 수명주기 및 환경 관리](#21-수명주기-및-환경-관리)
   * [2.2 인프라 사전 점검 (`preflight`)](#22-인프라-사전-점검-preflight)
   * [2.3 백업, 복원 및 원격 관리](#23-백업-복원-및-원격-관리)
   * [2.4 컨테이너 및 서비스 제어 (Compose Passthrough)](#24-컨테이너-및-서비스-제어-compose-passthrough)
3. [애플리케이션 CLI 명령어 (`claire` / `app`)](#3-애플리케이션-cli-명령어-claire--app)
   * [3.1 시스템 상태 및 지식그래프 진단/수복](#31-시스템-상태-및-지식그래프-진단수복)
   * [`queue` 큐 대시보드](#queue-큐-대시보드)
   * [3.2 수집 및 적재 (Ingest)](#32-수집-및-적재-ingest)
   * [3.3 검색 및 질의 (Search)](#33-검색-및-질의-search)
   * [3.4 재생성, 백필, 포맷 마이그레이션 및 복구](#34-재생성-백필-포맷-마이그레이션-및-복구)
   * [작업 진행률 및 중단 보고](#작업-진행률-및-중단-보고)
   * [3.5 1홉 자동 확장 (Expand)](#35-1홉-자동-확장-expand)
   * [3.6 중복 정리 및 정규화 (Dedup & Canon)](#36-중복-정리-및-정규화-dedup--canon)
   * [3.7 감시 및 문서 관리 (Watch & Doc)](#37-감시-및-문서-관리-watch--doc)
4. [미구현(Unimplemented) / 부분 구현 옵션 및 상태 명세](#4-미구현unimplemented--부분-구현-옵션-및-상태-명세)
5. [참고문헌](#5-참고문헌)

---

## 1. 명령어 계층 및 실행 표면

| 실행 환경 | 명령어 표면 | 대상 및 역할 |
| :--- | :--- | :--- |
| **호스트 (Host OS)** | `./cb-manuscript <command>` | 배포 환경, Docker Compose 오케스트레이션, 인프라 사전점검(`preflight`), 백업/복원 |
| **호스트 (One-off 임시 컨테이너)** | `./cb-manuscript app <command>` | 배포된 DB/볼륨을 공유하는 `claire` 애플리케이션 작업 실행 (`doctor`, `format-migrate`, `regenerate` 등) |
| **로컬 가상환경 (Local Dev)** | `uv run claire <command>` | 소스코드 개발, 로컬 SQLite/Mock 기반 단위 작업 및 테스트 |
| **컨테이너 내부 (Inside Container)** | `claire <command>` | 서비스 상주 데몬(API 서버, 텔레그램 봇, 큐 루프 등) |

---

## 2. 호스트 운영 명령어 (`cb-manuscript`)

### 2.1 수명주기 및 환경 관리

#### `init`
환경 설정 파일(`.env`, `.env.dev`)을 템플릿(`.env.example`, `.env.dev.example`)으로부터 안전하게 생성하고 초기화합니다.
* **사용법**: `./cb-manuscript init` 또는 `./cb-manuscript dev init`
* **동작**: 기존 파일이 있을 경우 기존 값을 보존하면서 누락된 신규 변수(예: `TZ`, `CLAIRE_GEMINI_EFFORT` 등)만 백필합니다.

#### `install`
최초 배포 파이프라인을 실행합니다.
* **사용법**: `./cb-manuscript install`
* **실행 순서**: `preflight` 검사 → Docker 이미지 빌드 (`docker compose build`) → DB 마이그레이션 (`claire migrate`) → 서비스 기동 (`up -d --wait`) → 헬스체크 (`health`).

#### `update`
Git 저장소 최신 커밋을 가져와 무중단 롤링 업데이트를 수행합니다.
* **사용법**: `./cb-manuscript update [--no-fetch]`
* **옵션**:
  * `--no-fetch`: 원격 git fetch 생략(로컬 변경사항만으로 빌드 및 재기동).

#### `version`
래퍼 스크립트 및 패키징된 Claire 소스코드의 버전을 출력합니다.
* **사용법**: `./cb-manuscript version`

---

### 2.2 인프라 사전 점검 (`preflight`)

#### `preflight`
*(구 `doctor`에서 변경)* 배포 환경, Docker 데몬, Compose 문법, 네트워크 바인딩, 디렉터리 권한, 보안 토큰을 사전 검증합니다.
* **사용법**: `./cb-manuscript preflight`
* **검증 항목**:
  * `.env` / `.env.dev` 문법 및 `CLAIRE_ENVIRONMENT` 일치 여부
  * `CB_API_BIND` IPv4 유효성 및 `CB_API_PORT` 충돌 여부
  * `data/` 및 `vault/` 디렉터리 권한 (`0700`)
  * 익명 읽기(`CLAIRE_ANONYMOUS_READONLY`) 노출 상태 경고

---

### 2.3 백업, 복원 및 원격 관리

#### `backup`
데이터베이스, Vault 마크다운, 환경 설정을 아카이브로 내보냅니다.
* **사용법**: `./cb-manuscript backup [--format {tgz,zip,dir}] [--component {all,db,vault,env}] [--replace | --force | -f]`
* **옵션**:
  * `--format`: 압축 포맷 지정 (`tgz` 기본값, `zip`, `dir`).
  * `--component`: 백업 대상 지정 (`all` 기본값, `db`, `vault`, `env`).
  * `--replace`, `--force`, `-f`: 동일 일자/경로의 기존 백업 덮어쓰기.

#### `restore`
백업 아카이브로부터 데이터와 설정을 복원합니다.
* **사용법**: `./cb-manuscript restore <source> [--component {all,db,vault,env}] [--yes | -y]`
* **옵션**:
  * `source`: 백업 디렉터리 또는 아카이브 파일 경로.
  * `--component`: 복원 대상 컴포넌트.
  * `--yes`, `-y`: 덮어쓰기 경고 확인 프롬프트 생략.

#### `remote`
원격 호스트에 SSH로 접속하여 배포 수명주기 명령을 실행합니다.
* **사용법**: `./cb-manuscript remote install <host>` 또는 `./cb-manuscript remote update <host>`

---

### 2.4 컨테이너 및 서비스 제어 (Compose Passthrough)

| 명령 | 사용법 및 설명 | 주요 옵션 |
| :--- | :--- | :--- |
| `up` | `./cb-manuscript up` (기본: `-d --wait` 안전 기동) | `--build`, `--no-deps`, `[service...]` |
| `down` | `./cb-manuscript down` (컨테이너 정지 및 정리) | `-v` (볼륨 삭제 주의), `--remove-orphans` |
| `restart` | `./cb-manuscript restart [service...]` (서비스 재시작) | `[service...]` |
| `status` | `./cb-manuscript status` (`docker compose ps` 컨테이너 상태) | — |
| `logs` | `./cb-manuscript logs [-f] [--tail N] [service...]` (로그 확인) | `-f` (follow), `--tail <N>` |
| `shell` | `./cb-manuscript shell [service] [cmd...]` (컨테이너 셸 진입) | 기본 서비스: `api` |
| `health` | `./cb-manuscript health` (컨테이너 내부 HTTP liveness 확인) | — |
| `app` | `./cb-manuscript app <claire_cmd...>` (One-off 앱 명령 실행) | `--advanced` (안전 가드 우회) |
| `compose` | `./cb-manuscript compose <docker_compose_args...>` | Compose 인자 직접 전달 |

---

## 3. 애플리케이션 CLI 명령어 (`claire` / `app`)

`claire`는 Python 패키지 내부 엔트리포인트이며, 로컬에서는 `uv run claire <cmd>`, 배포 환경에서는 `./cb-manuscript app <cmd>`로 실행합니다.

### 3.1 시스템 상태 및 지식그래프 진단/수복

| 명령 | 사용법 | 설명 |
| :--- | :--- | :--- |
| `doctor` | `claire doctor [--heal \| --apply] [--yes] [--json]` | 지식그래프 무결성(고아 노드/엣지, FTS 불일치) 진단 및 원클릭 자동 수복 |
| `preflight` | `claire preflight` | 파이썬 환경, 설정값, Gemini API Key, sqlite-vec 모듈, DB 연결 사전 점검 |
| `health` | `claire health` | DB, 큐(Queue), Inbox 상태를 담은 건강 진단 JSON 출력 |
| `liveness` | `claire liveness` | 읽기 전용 DB 및 스키마 생존 여부 확인 (Degraded 시 비정상 종료 안 함) |
| `status` | `claire status` | 운영 상태, DB 테이블 카운트, 프로바이더 설정 전체 출력 |
| `queue` | `claire queue status` / `claire queue list <inbox\|refresh\|expand>` | 비동기 큐 상태 분포와 대기·오류 항목 조회 |
| `stats` | `claire stats` | 지식그래프 노드(엔티티) 및 엣지(관계) 카운트 출력 |
| `repo` | `claire repo` | Git 소스 저장소 정보 및 원격 URL 출력 |
| `migrate` | `claire migrate` | 스키마를 최신 `SCHEMA_VERSION`으로 초기화/업그레이드 |

#### `doctor`
지식그래프(Knowledge Graph) 및 SQLite DB의 참조 무결성을 정밀 진단하고, 결함을 원클릭으로 자동 수복(Auto-Healing)합니다.
* **사용법**:
  ```bash
  ./cb-manuscript app doctor          # 기본: Dry-run 진단 보고서 출력
  ./cb-manuscript app doctor --heal   # 자동 수복 실행
  ./cb-manuscript app doctor --apply  # 자동 수복 실행 (동일)
  ./cb-manuscript app doctor --heal -y # 무인 자동 수복
  ./cb-manuscript app doctor --json   # 기계 판독용 JSON 출력
  ```
* **옵션**:
  * `--heal`, `--apply`: 고아 관계 삭제, 출처 정제, FTS 색인 재구축 등 자동 수복 적용.
  * `--yes`, `-y`: 확인 프롬프트 생략.
  * `--json`: 진단 결과를 JSON 포맷으로 출력.
* **진단/수복 범위**:
  1. **고아 관계 (Dangling Relations)**: 연결 대상 엔티티가 없는 엣지 탐지/삭제.
  2. **유효하지 않은 출처 참조 (Stale Sources)**: 삭제된 문서를 가리키는 `sources` JSON 필터링.
  3. **유령/고아 엔티티 (Ghost Entities)**: 유효 문서 출처 및 연결 관계가 0개인 고아 노드 회수.
  4. **고아 임베딩 (Orphan Embeddings)**: 엔티티가 삭제된 벡터 데이터 정리.
  5. **FTS 전문 색인 불일치 (FTS Desync)**: 실존 엔티티 기준 `entities_fts` 재색인.
  6. **오염 요약 마크업 탐지**: AsciiDoc 문법이 섞인 요약 탐지 및 `regenerate` 안내.

#### `queue` 큐 대시보드
`queue`는 `raw_inbox`, `refresh_queue`, `expand_queue`의 상태 분포와 처리 대기·오류 항목을 한 번에 조회한다.[^queue-implementation]

* **사용법**:
  ```bash
  ./cb-manuscript app queue status          # 세 큐의 집계 및 대기·오류 항목
  ./cb-manuscript app queue list inbox      # raw_inbox만 조회
  ./cb-manuscript app queue list refresh    # refresh_queue만 조회
  ./cb-manuscript app queue list expand     # expand_queue만 조회
  ./cb-manuscript app queue list inbox --limit 50
  ```
* **출력 범위**:
  * `inbox`는 상태별 건수, 즉시 재시도 가능한 `error` 항목 수, 최근 `error`·`failed` 항목을 표시한다.
  * `refresh`와 `expand`는 상태별 건수와 `pending`·`error` 항목을 표시한다. `refresh`는 URL과 사유를, `expand`는 문서 ID를 포함한다.
  * `list`에는 `inbox`, `refresh`, `expand` 중 하나가 필수다. 누락하면 종료 코드 `2`를 반환한다. `--name`은 위치 인수와 같은 역할을 하는 호환 별칭이다.
  * `--json`은 현재 세 큐 모두의 상태별 건수만 출력하며, `list`의 상세 행이나 큐 필터를 JSON에 반영하지 않는다.

> [!CAUTION]
> 텍스트 대시보드는 `raw_inbox` 페이로드와 `refresh_queue` URL의 앞부분을 표시한다. 터미널 로그를 외부로 전달하거나 공유 저장소에 보관하지 않는다.[^queue-implementation]

---

### 3.2 수집 및 적재 (Ingest)

#### `ingest <payload>`
URL, 일반 텍스트, 또는 로컬 파일로부터 문서를 수집하고 지식그래프를 구축합니다.
* **사용법**: `claire ingest "https://example.com/article" [--expand] [--title "제목"] [--format {md,adoc}] [--focus "초점 지침"]`
* **주요 옵션**:
  * `--focus <focus>`: 가독 본문(detail) 작성을 위한 집중 초점/지침 지정 (호환 별칭: `--orientation`, `--directive`).
  * `--expand`: 본문에서 추출된 외부 링크 URL들을 1홉 확장 큐(`expand_queue`)에 등록.
  * `--title <title>`: 자동 추출 제목 대신 수동 제목 지정.
  * `--format {md,adoc}`: 본문 detail 렌더링 포맷 지정.
  * `--source-type {web,text,youtube,discourse,xcom}`: 수집 소스 유형 강제 지정.

---

### 3.3 검색 및 질의 (Search)

#### `search <query>`
FTS5 전문 검색과 벡터 임베딩 코사인 유사도를 결합한 하이브리드 검색을 수행하고, LLM을 통해 인용 출처가 포함된 종합 답변을 생성합니다.
* **사용법**: `claire search "검색 질의어" [--no-summary] [--limit 10]`
* **주요 옵션**:
  * `--no-summary`: LLM 종합 요약을 건너뛰고 랭킹된 원본 매칭 엔티티/문서 스니펫만 빠르게 반환.
  * `--limit <N>`: 검색 결과 상위 노출 개수 (기본값: 5).

---

### 3.4 재생성, 백필, 포맷 마이그레이션 및 복구

| 명령 | 사용법 | 설명 |
| :--- | :--- | :--- |
| `regenerate` | `claire regenerate [<target>] [--tables] [--summary] [--detail] [--all] [--apply] [--force] [--effort <level>] [--focus <focus>]` | 특정 문서 또는 표(Table) 포함 문서 컴포넌트(요약/본문/그래프) 선택적 LLM 재생성 (기본: dry-run, 실행: `--apply`) |
| `summary-regenerate`| `claire summary-regenerate [<target>] [--tables] [--apply] [--force] [--effort <level>]` | `regenerate --summary`의 단축 Alias |
| `format-migrate` | `claire format-migrate [--format {md,adoc}] [--apply] [--yes] [--json]` | 문서 렌더링 포맷 진단 및 일괄 변환 (기본: dry-run, 실행: `--apply`) |
| `format-status` | `claire format-status` | 문서 detail의 포맷별(md, adoc, 누락) 통계 출력 |
| `truncation-status` | `claire truncation-status [<target>] [--json]` | 원문 절단(20k 슬라이싱) 및 메타데이터 누락 문서 진단 리포트 (단축: `truncation-scan`) |
| `truncation-backfill` | `claire truncation-backfill [<target>] [--apply] [--mark-refresh] [--force] [--yes] [--json]` | 메타데이터 누락 절단 문서에 `raw_truncated` 소급 기록 (기본: dry-run, 실행: `--apply`, 단축: `backfill-truncation`) |
| `backfill-detail` | `claire backfill-detail [--tables] [--format {md,adoc}] [--limit N] [--force] [--focus <focus>]` | detail 렌더링이 누락되었거나 표가 포함된 문서 일괄 생성 (그래프 불변) |
| `backfill-summary` | `claire backfill-summary [--limit N]` | 요약이 누락된 기존 문서의 요약 일괄 생성 |
| `backfill-images` | `claire backfill-images [--limit N]` | 문서 내 참조된 이미지 에셋 추출 및 다운로드 백필 |
| `recompile-html` | `claire recompile-html` | 저장된 detail 본문으로부터 `detail_html` AOT 사전 컴파일 갱신 |
| `reextract` | `claire reextract [--tables] [--no-rebuild] [--limit N]` | 저장된 `raw_text`로부터 지식그래프 전체(또는 표 포함 문서)를 재추출 |
| `replay-failed` | `claire replay-failed [--limit N]` | `raw_inbox`에서 `status=error`인 실패 건 전량 수동 재적재 |
| `recover-run` | `claire recover-run [--limit N]` | 에러 큐 단건/배치 복구 실행 (게이팅/지수 백오프 적용) |
| `recover-loop` | `claire recover-loop [--interval N]` | 에러 복구 자동 데몬 루프 |
| `refresh-mark` | `claire refresh-mark [--older-than-days N]` | 구버전/빈약 문서를 갱신 큐(`refresh_queue`)에 마킹 |
| `refresh-run` | `claire refresh-run [--limit N]` | 갱신 큐 1회 배치 처리 |
| `refresh-loop` | `claire refresh-loop [--interval N]` | 갱신 큐 상주 데몬 루프 |

#### `regenerate`
특정 문서의 컴포넌트(요약, 본문 detail, 그래프 노드/엣지)를 LLM을 통해 선택적으로 재생성하고 DB를 갱신합니다.
* **사용법**:
  ```bash
  ./cb-manuscript app regenerate <target> --summary              # Dry-run 진단 (기본)
  ./cb-manuscript app regenerate <target> --summary --apply      # 실제 LLM 호출 및 DB 갱신
  ./cb-manuscript app regenerate <target> --summary --apply --effort high # 추론 레벨 지정
  ./cb-manuscript app regenerate --corrupted --summary           # 오염된 요약 일괄 스캔
  ./cb-manuscript app regenerate --tables --all                  # 표 포함 문서 일괄 진단 (Dry-run)
  ./cb-manuscript app regenerate --tables --all --apply          # 표 포함 문서 요약/본문/그래프 일괄 재생성
  ./cb-manuscript app regenerate <target> --all --apply          # 특정 문서 전체 재생성
  ```
* **옵션**:
  * `target`: 문서 ID, 공유 토큰(예: `dzr73zpxh2bah4vp`), 또는 공유 URL (`https://.../p?s=token`).
  * `--token <token>`: 명시적 공유 토큰 지정.
  * `--doc-id <id>`: 명시적 문서 ID 지정.
  * `--summary`: 요약(summary) 재생성 (기본 대상). 지식그래프 노드/엣지는 100% 보존.
  * `--detail`: 본문(detail) 렌더링 텍스트 재생성.
  * `--graph`: 엔티티와 관계 재추출 및 지식그래프/Vault 갱신.
  * `--all`: 요약, 본문, 그래프 전체 동시 재생성.
  * `--corrupted`: AsciiDoc/마크업 문법 잔존으로 오염된 요약을 가진 문서를 전체 DB에서 자동 탐지.
  * `--tables`, `--has-tables`: 마크다운(`|...|`), AsciiDoc(`|===`), HTML(`<table>`) 표가 포함된 문서를 전체 DB에서 자동 탐지하여 일괄 대상으로 지정.
  * `--refetch`: 재생성 전 최신 웹 문서 재스크랩.
  * `--apply`: 실제 LLM 호출 및 DB 덮어쓰기 실행 (미지정 시 기본 dry-run).
  * `--force`, `-f`: 기존 컴포넌트가 이미 유효하더라도 강제 재생성/덮어쓰기.
  * `--dry-run`: 대상 문서 정보 및 계획만 출력하고 DB 변경 없음 (기본값).
  * `--effort <level>`: Gemini 사고/추론 레벨 오버라이드 (`low`, `medium`, `high`, `minimal`, `none`, 또는 정수 토큰 budget).
  * `--format {md,adoc}`: 본문 detail 렌더링 포맷 지정.
  * `--focus <focus>`: 가독 본문(detail) 작성을 위한 집중 초점/지침 지정 (호환 별칭: `--orientation`, `--directive`).

#### `summary-regenerate`
`regenerate --summary`의 단축 Alias입니다.
* **사용법**: `./cb-manuscript app summary-regenerate <target> [--apply] [--effort <level>]`

#### `format-migrate`
전체 문서의 detail 본문 렌더링 포맷(Markdown ↔ AsciiDoc) 현황을 점검하고 일괄 변환합니다.
* **사용법**:
  ```bash
  ./cb-manuscript app format-migrate          # 변환 현황 진단 (Dry-run)
  ./cb-manuscript app format-migrate --apply  # 미적용 문서 일괄 백필 변환
  ./cb-manuscript app format-migrate --apply -y
  ```
* **옵션**:
  * `--format {md,adoc}`: 목표 포맷 지정 (미지정 시 .env의 `CLAIRE_RENDER_FORMAT` 사용).
  * `--apply`: 미변환 문서에 대해 LLM detail 렌더링을 실행하여 일괄 적용.
  * `--dry-run`: 대상 문서 통계만 보고 (기본값).
  * `--yes`, `-y`: 확인 프롬프트 생략.
  * `--json`: 진단 통계를 JSON 포맷으로 출력.

#### `truncation-status` (단축: `truncation-scan`)
데이터베이스 내 문서들의 원문 20,000자 슬라이싱 여부 및 `raw_truncated` 메타데이터 누락 상태를 스캔하고 상세 리포트를 출력합니다.
* **사용법**:
  ```bash
  ./cb-manuscript app truncation-status                   # 전체 문서 절단 진단 리포트
  ./cb-manuscript app truncation-status <target>          # 특정 문서 단건 진단
  ./cb-manuscript app truncation-status --json            # JSON 포맷 출력
  ```
* **판정 기준**:
  * `content_hash` 불일치: 수집 당시 원문 전체로 계산된 해시 $\neq$ DB에 적재된 `raw_text` 해시.
  * 20,000자 상한 도달: 표(Table)를 제외한 산문(Prose) 글자 수가 정확히 20,000자에 도달.

#### `truncation-backfill` (단축: `backfill-truncation`)
과거에 슬라이싱되었으나 메타데이터가 누락된 문서의 `documents.meta`에 `raw_truncated: true`, `raw_chars: <len>`를 소급 기록합니다.
* **사용법**:
  ```bash
  ./cb-manuscript app truncation-backfill                 # Dry-run 진단 (기본)
  ./cb-manuscript app truncation-backfill --apply         # 실제 DB 메타데이터 소급 갱신
  ./cb-manuscript app truncation-backfill --apply --mark-refresh # 소급 갱신 + 원본 재수집(refresh) 큐 등록
  ./cb-manuscript app truncation-backfill <target> --apply # 특정 문서 단건 소급
  ```
* **옵션**:
  * `--apply`: 실제 DB `documents.meta` 갱신을 적용 (미지정 시 기본 dry-run).
  * `--mark-refresh`: 검출된 절단 문서를 원문 온전 재수집을 위해 `refresh_queue`에 자동 등록.
  * `--force`: 이미 `raw_truncated` 플래그가 있는 문서까지 포함하여 전체 재평가 및 갱신.
  * `--yes`, `-y`: 대화형 확인 프롬프트 생략.
  * `--json`: 결과를 JSON 포맷으로 출력.

#### `backfill-detail`
가독 본문(`detail`)이 누락된 문서 또는 표(`--tables`)가 포함된 문서를 선별하여 본문을 일괄 생성/재생성합니다 (지식그래프 불변, 비파괴).
* **사용법**:
  ```bash
  ./cb-manuscript app backfill-detail                     # detail 누락 문서만 생성
  ./cb-manuscript app backfill-detail --force             # 전체 문서 detail 강제 재생성
  ./cb-manuscript app backfill-detail --tables            # 표 포함 문서만 선별하여 detail 재생성
  ```
* **옵션**:
  * `--tables`, `--has-tables`: 원문/본문에 표(Markdown, AsciiDoc, HTML)가 포함된 문서만 선별하여 재생성.
  * `--force`, `-f`: 기존에 detail이 있더라도 강제로 재생성.
  * `--format {md,adoc}`: 생성할 본문 포맷 지정.
  * `--limit <N>`: 처리할 최대 문서 개수.

#### `reextract`
저장된 `raw_text`로부터 전체(또는 표 포함) 문서의 지식그래프(엔티티, 관계, 요약, 본문)를 백지 상태에서 재추출·재구축합니다.
* **사용법**:
  ```bash
  ./cb-manuscript app --advanced reextract                # 전체 그래프 초기화 및 재추출
  ./cb-manuscript app --advanced reextract --tables       # 표 포함 문서만 선별 재추출
  ./cb-manuscript app --advanced reextract --no-rebuild   # 그래프를 비우지 않고 누적 병합
  ```
* **옵션**:
  * `--tables`, `--has-tables`: 표가 포함된 문서만 선별하여 재추출.
  * `--no-rebuild`: 그래프 초기화(reset_graph) 없이 기존 그래프에 누적 추출.
  * `--format {md,adoc}`: detail 본문 포맷 지정.
  * `--limit <N>`: 처리할 최대 문서 개수.

#### 작업 진행률 및 중단 보고
다음의 **1회 실행 배치 명령**은 진행률 추적기를 사용한다: `regenerate --apply`, `reextract`, `backfill-detail`, `backfill-summary`, `format-migrate --apply`, `recover-run`, `refresh-run`, `expand-run`.[^progress-implementation]

* **정상 진행 출력**: 시작 시 전체 대상 수를 표시하고, 각 항목마다 `[현재/전체]`, 백분율, 대상 ID와 제목을 출력한다. 두 번째 항목부터는 완료 항목의 평균 시간으로 잔여 시간을 추정한다. 가능한 파이프라인에서는 구조화 추출, detail 렌더링, 엔티티 해소·동일체 판정, 관계 적재, Vault 동기화의 현재 단계를 함께 출력한다.[^progress-implementation]
* **`Ctrl+C` 처리**: 현재 문서·제목·URL·단계, 완료/잔여 수, 경과 시간과 재개 명령을 포함한 중단 보고서를 출력하고 해당 CLI 명령은 종료 코드 `130`을 반환한다. 보고서의 데이터 보존 문구는 이미 완료된 항목을 대상으로 한다.[^progress-implementation]
* **오류 경계**: 배치 본문에서 발생한 일반 예외도 중단 보고서를 먼저 출력하지만 예외 자체는 다시 전파된다. 따라서 오류를 종료 코드로 변환하거나 후속 처리를 재시도하지 않는다.[^progress-implementation]
* **적용 제외**: `replay-failed`와 `recover-loop`·`refresh-loop`·`expand-loop`에는 이 항목별 추적기가 연결되어 있지 않다. 이 경로들은 기존의 결과 요약 또는 주기 로그만 출력한다.[^progress-implementation]

---

### 3.5 1홉 자동 확장 (Expand)

* `claire expand-run [--limit N]`: 1홉 확장 큐(`expand_queue`)에 대기 중인 URL 후보를 선별하여 자동 수집 및 적재 1회 실행.
* `claire expand-loop [--interval N] [--batch N]`: 1홉 확장을 백그라운드에서 주기적으로 수행하는 데몬 루프.

---

### 3.6 중복 정리 및 정규화 (Dedup & Canon)

* `claire dedup-scan [--threshold 0.85] [--min-len 200]`: MinHash LSH 기반으로 내용이 유사한 근사 중복(Near-duplicate) 문서 클러스터 탐지 및 보고 (비파괴 진단).
* `claire dedup-merge [--threshold 0.85] [--apply] [--yes]`: 탐지된 중복 문서를 대표 문서(Keeper)로 병합하고 지식그래프 엣지 통합 (기본: dry-run, 실행: `--apply`).
* `claire recanonicalize [--apply] [--dry-run]`: URL 정규화 규칙(ArXiv 버전 번호 통일 등)을 기존 문서에 재계산하여 일괄 갱신 (기본: dry-run, 실행: `--apply`).

---

### 3.7 감시 및 문서 관리 (Watch & Doc)

* `claire watch [--list | <target> --on/--off --interval-days N]`: 주기적 재수집 대상 문서 목록 조회 및 주기 설정. `target`으로 문서 ID, 일반 URL, 공유 URL(`/p?s=token`)을 스마트 인식.
* `claire doc-title <target> "<new_title>"`: 특정 문서의 제목을 수동 수정하고 MinHash 서명 재계산. `target`으로 문서 ID, 일반 URL, 공유 URL 지원.
* `claire serve-api`: Starlette + Uvicorn 기반 웹 인터페이스 및 REST API 서비스 실행.
* `claire bot`: Telegram Long-polling 봇 서비스 실행.

---

### 3.8 데이터 수명주기 및 오염 소각 (Lifecycle & Purge)

* `claire purge <target> [--doc-id <ID>] [--token <token>] [--url <URL>] [--pattern <str>] [--reason <str>] [--apply] [--yes] [--json]`:
  * **스마트 타깃 자동 판별**: `target` 하나로 문서 ID(SHA256/UUID), 공유 링크(`/p?s=token`), 일반 원본 URL, 정규화된 canonical URL, 프로토콜 누락 도메인(`domain.com/...`), 제목 키워드를 4단계 우선순위로 자동 판별.
  * **수명주기 게이트**: `.env`에 `CLAIRE_DATA_LIFECYCLE=purgeable` (또는 `CLAIRE_ALLOW_PURGE=1`) 설정 시에만 실행 허용 (`append-only` 시 안전 차단).
  * **원자적 소각**: 툼스톤(`purged_tombstones`) 등록 ➔ DB 8개 테이블 연쇄 Hard Delete ➔ 로컬 파일시스템 아티팩트(`raw/artifacts`, `images`, `vault`) Unlink ➔ `heal_graph` 수복 ➔ `VACUUM` 압축을 일괄 수행.
  * **공유 링크 소각 경고**: 공유 링크로 식별된 경우 단순 링크 무효화가 아닌 원본 문서 전체 파괴임을 Dry-Run에 명시적 경고.
  * 기본 실행은 Dry-Run으로 영향 범위를 사전 출력하며, `--apply` 지정 시 실제 소각 실행 (대화형 `[y/N]` 확인 또는 `--yes`/`-y`로 무인 실행).
* `claire audit [<target>] [--pattern <str>] [--json]`:
  * 특정 키워드, URL, ID, 또는 툼스톤 대상이 DB(문서/인박스/추출/스냅샷), 로컬 디스크 파일, 엔티티 sources에 1건이라도 남아있는지 전수 검사하고 Freelist 미회수 용량을 보고.

---

## 4. 미구현(Unimplemented) / 부분 구현 옵션 및 상태 명세

시스템 운영 및 개발 시 혼선을 방지하기 위해 현재 코드베이스의 **부분 구현, 예약된 옵션, 또는 기능적 제약사항**을 명시합니다.

### 4.1 `claire search`
* **`--no-summary` (완전 구현)**: LLM 추론 비용과 응답 지연을 방지하기 위해 사용되며, FTS 및 벡터 검색 결과의 원시 텍스트 청크만 즉시 출력합니다.
* **다국어 교차 검색 (부분 지원)**: 영어/한국어 혼용 질의는 엔티티 `norm_name` 및 Gemini 임베딩 모델(`gemini-embedding-001`)의 다국어 투영 공간을 통해 처리되나, 한자/일본어 등 CJK 확장 언어에 대한 형태소 분절은 FTS5 단순 토크나이저에 의존합니다.

### 4.2 `claire regenerate` 및 `CLAIRE_GEMINI_EFFORT`
* **`--effort` 지원 범위 (조건부 적용)**:
  * Gemini 2.0 Flash Thinking, Gemini 3.0/3.1 계열 등 **Thinking 기능이 지원되는 모델**에서는 `thinking_config`(`low`, `medium`, `high` 또는 토큰 수치)가 정상 작동합니다.
  * Thinking을 지원하지 않는 구형 모델이나 Mock Provider에서는 `--effort` 인자가 주어져도 API 에러를 내지 않고 조용히 무시(Graceful fallback)됩니다.
* **`--detail` 재생성 후 그래프 동기화 (순차 수복 필요)**:
  * `regenerate --summary`는 `extractions.raw_response`의 요약만 교체하므로 그래프 무결성에 영향이 없습니다.
  * `regenerate --detail`은 본문 렌더링 텍스트를 새로 작성하지만, 본문 변경에 따른 새로운 엔티티/관계의 자동 재추출은 수행하지 않습니다. 본문 내용 변경에 따른 전체 그래프 갱신이 필요할 경우 `reextract`를 실행해야 합니다.

### 4.3 `claire doctor` (무결성 수복) vs `claire dedup-merge`
* **결정론적 무결성 수복 (`doctor --heal`)**: 고아 관계 제거, 출처 배열 정제, 고아 엔티티 삭제, FTS 재구축 등 SQL/규칙 기반 수복은 100% 완전 자동 지원됩니다.
* **의미론적 개체 통합 (LLM Semantic Merge)**: 표기가 약간 다른 동일 인물/개체(예: `Antigravity`와 `Google Antigravity`)의 의미론적 병합은 `doctor --heal`의 범위가 아니며, `dedup-merge` 또는 재추출(`reextract`) 파이프라인에서 수행됩니다.

### 4.4 `sqlite-vec` 벡터 확장 모듈
* `sqlite-vec` 바이너리 확장이 호스트 환경에서 로드 가능한 경우(`probe_sqlite_vec` OK) 네이티브 벡터 인덱스를 사용합니다.
* 확장을 로드할 수 없는 아키텍처나 배포판에서는 순수 파이썬 Brute-force 코사인 유사도 연산으로 자동 폴백(Fallback)되며, 기능은 100% 동일하게 동작하나 엔티티 수만 건 이상 시 속도 저하가 발생할 수 있습니다.

### 4.5 `claire backfill-images`
* 문서 내 포함된 이미지 URL을 파싱하여 로컬 볼륨으로 다운로드합니다. 외부 이미지 호스트가 접근 차단(Hotlinking 방지) 또는 404인 경우 다운로드가 스킵되며, 원본 URL 링크 형태로 유지됩니다.

---

## 5. 참고문헌

[^queue-implementation]: Claire Bible 구현 근거: [`src/claire/cli.py`](../../../src/claire/cli.py), [`src/claire/status.py`](../../../src/claire/status.py), [`ops/cb_manuscript.py`](../../../ops/cb_manuscript.py) (2026-08-27 확인).
[^progress-implementation]: Claire Bible 구현 근거: [`src/claire/progress.py`](../../../src/claire/progress.py), [`src/claire/cli.py`](../../../src/claire/cli.py), [`src/claire/ingest/service.py`](../../../src/claire/ingest/service.py), [`src/claire/ingest/pipeline.py`](../../../src/claire/ingest/pipeline.py) (2026-08-27 확인).
