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
   * [3.8 데이터 수명주기 및 오염 소각 (Lifecycle & Purge)](#38-데이터-수명주기-및-오염-소각-lifecycle--purge)
   * [3.9 관측성 및 문제 해결 (Observability, Telemetry & Support Bundle)](#39-관측성-및-문제-해결-observability-telemetry--support-bundle)
   * [3.10 지식 테마 관리 및 다중 DB 격리 (Theme Management)](#310-지식-테마-관리-및-다중-db-격리-theme-management)
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
| `health` | `claire health` | DB, 큐(Queue), Inbox 상태를 담은 건강 진단 JSON 출력. 멀티 테마 모드에서는 등록 DB별 진단과 전체 합계를 출력 |
| `liveness` | `claire liveness` | 모든 활성 DB의 읽기 전용 접근·현재 스키마 확인 (Degraded 시 비정상 종료 안 함) |
| `status` | `claire status` | 운영 상태, DB 테이블 카운트, 프로바이더 설정 전체 출력 |
| `queue` | `claire queue status` / `claire queue list <inbox\|refresh\|expand>` | 비동기 큐 상태 분포와 대기·오류 항목 조회 |
| `stats` | `claire stats [-t <theme>]` | 지식그래프 노드(엔티티) 및 엣지(관계) 카운트 출력 (멀티 테마 지원) |
| `repo` | `claire repo` | Git 소스 저장소 정보 및 원격 URL 출력 |
| `migrate` | `claire migrate` | 싱글 모드에서는 기본 DB, 멀티 테마 모드에서는 등록된 모든 DB를 공통 v13으로 초기화/업그레이드하고 버전·계보를 검증 |

`CLAIRE_MULTI_THEME=1`일 때 `migrate`는 레지스트리를 테마 ID 순서로 읽고 각 DB의 마이그레이션 결과를 개별 출력한다.
한 DB가 실패해도 나머지를 계속 점검하며, 하나라도 실패하면 최종 종료 코드는 `1`이다.
`health`와 `liveness`는 DB를 생성하거나 마이그레이션하지 않고 읽기 전용 연결과 스키마 버전·계보를 검사한다.
v12는 진단 행을 보존해 v11로 철회한 뒤 같은 실행에서 v13으로 승격한다.
손상된 `themes.json`도 기본 레지스트리로 덮어쓰지 않고 실패로 보고한다.[^multi-theme-operations]

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
* **사용법**: `claire ingest "https://example.com/article" [-t <theme>] [--expand] [--title "제목"] [--format {md,adoc}] [--focus "초점 지침"]`
* **주요 옵션**:
  * `-t <theme>`, `--theme <theme>`: 적재 대상 테마 지정 (테마 ID, 일련번호, 또는 레이블 이름 지원). 미지정 시 기본 지식베이스(ID 0)에 적재. 대상 추가 테마에 `default_focus`가 설정되어 있고 명시적 `--focus`가 없으면 해당 기본 초점이 자동 적용됩니다.
  * `--focus <focus>`: 가독 상세(detail) 작성을 위한 집중 초점/지침 지정 (호환 별칭: `--orientation`, `--directive`). 지정 시 테마의 기본 초점보다 우선하여 덮어씁니다.
  * `--expand`: 본문에서 추출된 외부 링크 URL들을 1홉 확장 큐(`expand_queue`)에 등록.
  * `--title <title>`: 자동 추출 제목 대신 수동 제목 지정.
  * `--format {md,adoc}`: 상세 detail 렌더링 포맷 지정.
  * `--source-type {web,text,youtube,video,discourse,xcom}`: 수집 소스 유형 강제 지정 (video: 비디오 스트림 및 음성 전사).

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
| `regenerate` | `claire regenerate [<target>] [--tables] [--summary] [--detail] [--all] [--apply] [--force] [--effort <level>] [--focus <focus>]` | 특정 문서 또는 표(Table) 포함 문서 컴포넌트(요약/상세/그래프) 선택적 LLM 재생성 (기본: dry-run, 실행: `--apply`) |
| `summary-regenerate`| `claire summary-regenerate [<target>] [--tables] [--apply] [--force] [--effort <level>]` | `regenerate --summary`의 단축 Alias |
| `format-migrate` | `claire format-migrate [--format {md,adoc}] [--apply] [--yes] [--json]` | 문서 렌더링 포맷 진단 및 일괄 변환 (기본: dry-run, 실행: `--apply`) |
| `format-status` | `claire format-status` | 문서 detail의 포맷별(md, adoc, 누락) 통계 출력 |
| `truncation-status` | `claire truncation-status [<target>] [--json]` | 원문 절단(20k 슬라이싱) 및 메타데이터 누락 문서 진단 리포트 (단축: `truncation-scan`) |
| `truncation-backfill` | `claire truncation-backfill [<target>] [--apply] [--mark-refresh] [--force] [--yes] [--json]` | 메타데이터 누락 절단 문서에 `raw_truncated` 소급 기록 (기본: dry-run, 실행: `--apply`, 단축: `backfill-truncation`) |
| `backfill-detail` | `claire backfill-detail [--tables] [--format {md,adoc}] [--limit N] [--force] [--focus <focus>]` | 상세(detail) 렌더링이 누락되었거나 표가 포함된 문서 일괄 생성 (그래프 불변) |
| `backfill-summary` | `claire backfill-summary [--limit N]` | 요약이 누락된 기존 문서의 요약 일괄 생성 |
| `backfill-images` | `claire backfill-images [--limit N]` | 문서 내 참조된 이미지 에셋 추출 및 다운로드 백필 |
| `recompile-html` | `claire recompile-html` | 저장된 상세(detail)로부터 `detail_html` AOT 사전 컴파일 갱신 |
| `reextract` | `claire reextract [--tables] [--no-rebuild] [--limit N]` | 저장된 `raw_text`로부터 지식그래프 전체(또는 표 포함 문서)를 재추출 |
| `replay-failed` | `claire replay-failed [--limit N]` | `raw_inbox`에서 `status=error`인 실패 건 전량 수동 재적재 |
| `recover-run` | `claire recover-run [--limit N]` | 에러 큐 단건/배치 복구 실행 (게이팅/지수 백오프 적용) |
| `recover-loop` | `claire recover-loop [--interval N] [--batch N]` | 모든 활성 테마의 에러 복구 큐를 전역 batch 한도 안에서 순환 처리하는 자동 데몬 |
| `refresh-mark` | `claire refresh-mark [--older-than-days N]` | 구버전/빈약 문서를 갱신 큐(`refresh_queue`)에 마킹 |
| `refresh-run` | `claire refresh-run [--limit N]` | 갱신 큐 1회 배치 처리 |
| `refresh-loop` | `claire refresh-loop [--interval N] [--batch N]` | 모든 활성 테마의 watch·갱신 큐를 순환 처리하는 상주 데몬 |

#### `regenerate`
특정 문서의 컴포넌트(요약, 상세 detail, 그래프 노드/엣지)를 LLM을 통해 선택적으로 재생성하고 DB를 갱신합니다.
* **사용법**:
  ```bash
  ./cb-manuscript app regenerate <target> --summary              # Dry-run 진단 (기본)
  ./cb-manuscript app regenerate <target> --summary --apply      # 실제 LLM 호출 및 DB 갱신
  ./cb-manuscript app regenerate <target> --summary --apply --effort high # 추론 레벨 지정
  ./cb-manuscript app regenerate --corrupted --summary           # 오염된 요약 일괄 스캔
  ./cb-manuscript app regenerate --tables --all                  # 표 포함 문서 일괄 진단 (Dry-run)
  ./cb-manuscript app regenerate --tables --all --apply          # 표 포함 문서 요약/상세/그래프 일괄 재생성
  ./cb-manuscript app regenerate <target> --all --apply          # 특정 문서 전체 재생성
  ```
* **옵션**:
  * `target`: 문서 ID, 공유 토큰(예: `dzr73zpxh2bah4vp`), 또는 공유 URL (`https://.../p?s=token`).
  * `--token <token>`: 명시적 공유 토큰 지정.
  * `--doc-id <id>`: 명시적 문서 ID 지정.
  * `--summary`: 요약(summary) 재생성 (기본 대상). 지식그래프 노드/엣지는 100% 보존.
  * `--detail`: 상세(detail) 렌더링 텍스트 재생성.
  * `--graph`: 엔티티와 관계 재추출 및 지식그래프/Vault 갱신.
  * `--all`: 요약, 상세, 그래프 전체 동시 재생성.
  * `--corrupted`: AsciiDoc/마크업 문법 잔존으로 오염된 요약을 가진 문서를 전체 DB에서 자동 탐지.
  * `--tables`, `--has-tables`: 마크다운(`|...|`), AsciiDoc(`|===`), HTML(`<table>`) 표가 포함된 문서를 전체 DB에서 자동 탐지하여 일괄 대상으로 지정.
  * `--refetch`: 환경변수(`CLAIRE_RAW_CHAR_BUDGET`)의 수집 길이 제한을 적용하여 원본 URL에서 최신 문서 재스크랩 후 재생성.
  * `--refetch-full`: 환경변수 길이 제한 없이 원본 URL에서 원문 전체 길이를 수집 후 재생성.
  * `--apply`: 실제 LLM 호출 및 DB 덮어쓰기 실행 (미지정 시 기본 dry-run).
  * `--force`, `-f`: 기존 컴포넌트가 이미 유효하더라도 강제 재생성/덮어쓰기.
  * `--dry-run`: 대상 문서 정보 및 계획만 출력하고 DB 변경 없음 (기본값).
  * `--effort <level>`: Gemini 사고/추론 레벨 오버라이드 (`low`, `medium`, `high`, `minimal`, `none`, 또는 정수 토큰 budget).
  * `--format {md,adoc}`: 상세 detail 렌더링 포맷 지정.
  * `--focus <focus>`: 가독 상세(detail) 작성을 위한 집중 초점/지침 지정 (호환 별칭: `--orientation`, `--directive`).

#### `summary-regenerate`
`regenerate --summary`의 단축 Alias입니다.
* **사용법**: `./cb-manuscript app summary-regenerate <target> [--refetch | --refetch-full] [--apply] [--effort <level>]`

#### `format-migrate`
전체 문서의 detail 상세 렌더링 포맷(Markdown ↔ AsciiDoc) 현황을 점검하고 일괄 변환합니다.
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
  * `--mark-refresh`: 검출한 절단 문서를 원문 온전 재수집을 위해 `refresh_queue`에 자동 등록.
  * `--force`: 이미 `raw_truncated` 플래그가 있는 문서까지 포함하여 전체 재평가 및 갱신.
  * `--yes`, `-y`: 대화형 확인 프롬프트 생략.
  * `--json`: 결과를 JSON 포맷으로 출력.

#### `backfill-detail`
가독 상세(`detail`)가 누락된 문서 또는 표(`--tables`)가 포함된 문서를 선별하여 상세를 일괄 생성/재생성합니다 (지식그래프 불변, 비파괴).
* **사용법**:
  ```bash
  ./cb-manuscript app backfill-detail                     # detail 누락 문서만 생성
  ./cb-manuscript app backfill-detail --force             # 전체 문서 detail 강제 재생성
  ./cb-manuscript app backfill-detail --tables            # 표 포함 문서만 선별하여 detail 재생성
  ```
* **옵션**:
  * `--tables`, `--has-tables`: 원문/상세에 표(Markdown, AsciiDoc, HTML)가 포함된 문서만 선별하여 재생성.
  * `--force`, `-f`: 기존에 detail이 있더라도 강제로 재생성.
  * `--format {md,adoc}`: 생성할 상세 포맷 지정.
  * `--limit <N>`: 처리할 최대 문서 개수.

#### `reextract`
저장된 `raw_text`로부터 전체(또는 표 포함) 문서의 지식그래프(엔티티, 관계, 요약, 상세)를 백지 상태에서 재추출·재구축합니다.
* **사용법**:
  ```bash
  ./cb-manuscript app --advanced reextract                # 전체 그래프 초기화 및 재추출
  ./cb-manuscript app --advanced reextract --tables       # 표 포함 문서만 선별 재추출
  ./cb-manuscript app --advanced reextract --no-rebuild   # 그래프를 비우지 않고 누적 병합
  ```
* **옵션**:
  * `--tables`, `--has-tables`: 표가 포함된 문서만 선별하여 재추출.
  * `--no-rebuild`: 그래프 초기화(reset_graph) 없이 기존 그래프에 누적 추출.
#### `video-reprocess` (단축: `reprocess-video`)
기존에 자막 없이 적재되었거나 전사가 누락된 비디오 문서를 다시 수집합니다. 발행자가 선호 언어 CC를 제공하면 해당 자막을 내려받아 보존하고, 유효한 CC가 없을 때만 오디오와 STT 경로를 실행합니다. VMware Explore 상세 페이지가 Presentation PDF를 제공하면 검증된 원본 PDF와 추출 텍스트를 같은 영상 문서에 함께 갱신합니다. STT 처리 실패 시 사흘(3일)간 로컬 캐시(`data/cache/video/`)에 보존된 미디어를 재다운로드 없이 재사용합니다.[^video-caption-implementation][^video-presentation-implementation]
* **사용법**:
  ```bash
  ./cb-manuscript app video-reprocess --doc-id <doc_id>          # Dry-run 진단 (기본)
  ./cb-manuscript app video-reprocess --doc-id <doc_id> --apply  # 실제 자막 재수집 및 지식 갱신
  ./cb-manuscript app video-reprocess <target_url> --apply       # URL 기반 자막 수집 및 적재
  ./cb-manuscript app video-reprocess <target> --apply --effort high --format adoc # 옵션 지정
  ```
* **옵션**:
  * `target`: 대상 비디오 URL 또는 문서 ID / 공유 URL.
  * `--doc-id <ID>`: 특정 문서 ID (예: `doc_b19da8da2980`).
  * `--apply`: 실제 CC/미디어 재다운로드, 필요한 경우의 STT, 지식베이스 갱신을 실행 (미지정 시 기본 dry-run).
  * `--force`, `-f`: 기존 전사문이 있더라도 강제 덮어쓰기.
  * `--effort {low,medium,high}`: LLM 요약/상세 생성 추론 레벨 오버라이드.
  * `--format {md,adoc}`: 가독 상세(detail) 렌더링 포맷.
  * `--json`: 결과를 JSON 포맷으로 출력.
* **동작 특징**:
  * **CC 우선**: 선호 언어, 언어 태그 정확도, 수동/자동 구분, 전송 형식을 기준으로 후보를 정렬합니다. 유효한 WebVTT를 확보하면 오디오 다운로드와 STT를 생략합니다.[^video-caption-implementation]
  * **실패의 복구 가능성**: 광고된 선호 언어 CC의 다운로드가 모두 실패하면 STT로 숨기지 않고 오류로 반환합니다. 자막 URL의 서명·쿼리 토큰은 문서 메타데이터와 오류 문자열에 저장하지 않습니다.[^video-caption-implementation]
  * **Presentation 번들 원자성**: VMware Explore가 명시한 Presentation PDF는 허용 호스트·공개 IP·리다이렉트·크기·MIME·PDF 매직을 검증하고 기존 PDF 파서로 추출합니다. 광고된 PDF의 다운로드·추출·원본 저장이 실패하면 CC/STT만 성공한 것으로 적재하지 않습니다.[^video-presentation-implementation]
  * **원본 및 버전 보존**: PDF 원본은 `data/raw/attachments/<document_id>/presentation/<sha256>.pdf`에 저장하며, 새 버전은 기존 파일을 삭제하지 않고 `presentation_history`와 함께 추가합니다.[^video-presentation-implementation]
  * **3일 미디어 캐시 재사용**: 유효한 CC가 없고 이전 STT 수집이 실패했을 때 `data/cache/video/`에 저장된 오디오 미디어가 있으면 외부 미디어 다운로드를 생략하고 STT를 진행합니다.
  * **실시간 단계별 진행률 스트리밍**: `[원문 전체 재수집]`, 필요한 경우의 `[오디오 다운로드/변환]`, `[STT 청크 전사]`, `[LLM 요약 및 지식 그래프 추출]` 단계가 터미널에 출력됩니다.
  * **전사 무결성 검증**: CC 획득 또는 STT가 실패하거나 `has_transcript`가 `False`인 경우 오류 원인을 `stderr`에 출력하고 종료 코드 `1`을 반환합니다.

#### 작업 진행률 및 중단 보고
다음의 **1회 실행 배치 명령**은 진행률 추적기를 사용한다: `regenerate --apply`, `reextract`, `backfill-detail`, `backfill-summary`, `format-migrate --apply`, `recover-run`, `refresh-run`, `expand-run`.[^progress-implementation]

* **정상 진행 출력**: 시작 시 전체 대상 수를 표시하고, 각 항목마다 `[현재/전체]`, 백분율, 대상 ID와 제목을 출력한다. 두 번째 항목부터는 완료 항목의 평균 시간으로 잔여 시간을 추정한다. 가능한 파이프라인에서는 구조화 추출, detail 렌더링, 엔티티 해소·동일체 판정, 관계 적재, Vault 동기화의 현재 단계를 함께 출력한다.[^progress-implementation]
* **`Ctrl+C` 처리**: 현재 문서·제목·URL·단계, 완료/잔여 수, 경과 시간과 재개 명령을 포함한 중단 보고서를 출력하고 해당 CLI 명령은 종료 코드 `130`을 반환한다. 보고서의 데이터 보존 문구는 이미 완료된 항목을 대상으로 한다.[^progress-implementation]
* **오류 경계**: 배치 본문에서 발생한 일반 예외도 중단 보고서를 먼저 출력하지만 예외 자체는 다시 전파된다. 따라서 오류를 종료 코드로 변환하거나 후속 처리를 재시도하지 않는다.[^progress-implementation]
* **적용 제외**: `replay-failed`와 `recover-loop`·`refresh-loop`·`expand-loop`에는 이 항목별 추적기가 연결되어 있지 않다. 이 경로들은 기존의 결과 요약 또는 주기 로그만 출력한다.[^progress-implementation]

---

### 3.5 1홉 자동 확장 (Expand)

* `claire expand-run [--limit N]`: 1홉 확장 큐(`expand_queue`)에 대기 중인 URL 후보를 선별하여 자동 수집 및 적재 1회 실행.
* `claire expand-loop [--interval N] [--batch N]`: 모든 활성 테마의 1홉 확장을 전역 batch 한도 안에서 순환 처리하는 데몬 루프. 확장 알림은 적재 결과와 링크를 테마별로 묶는다. 세 상주 루프는 매 cycle 레지스트리를 다시 읽어 신규·삭제 테마를 반영하고, 한 테마의 오류를 다른 테마 처리와 격리한다.[^multi-theme-operations]

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

* `claire purge <target> [--doc-id <ID>] [--token <token>] [--url <URL>] [--pattern <str>] [--reason <str>] [--no-tombstone] [--apply] [--yes] [--json]`:
  * **스마트 타깃 자동 판별**: `target` 하나로 문서 ID(SHA256/UUID), 공유 링크(`/p?s=token`), 일반 원본 URL, 정규화된 canonical URL, 프로토콜 누락 도메인(`domain.com/...`), 제목 키워드를 4단계 우선순위로 자동 판별.
  * **수명주기 게이트**: `.env`에 `CLAIRE_DATA_LIFECYCLE=purgeable` (또는 `CLAIRE_ALLOW_PURGE=1`) 설정 시에만 실행 허용 (`append-only` 시 안전 차단).
  * **원자적 소각**: 툼스톤(`purged_tombstones`) 등록 ➔ DB 8개 테이블 연쇄 Hard Delete ➔ 로컬 파일시스템 아티팩트(`raw/artifacts`, `raw/attachments`, `images`, `vault`) Unlink ➔ `heal_graph` 수복 ➔ `VACUUM` 압축을 일괄 수행.[^video-presentation-implementation]
  * **툼스톤 등록 제어 (`--no-tombstone`)**: 기본값은 소각 시 `purged_tombstones`에 지문을 등록하여 동일 URL/해시의 영구 재유입을 차단합니다. 테스트 목적 또는 포맷/옵션을 변경하여 즉시 재수집(re-ingest)하려는 경우 `--no-tombstone` 옵션을 지정하면 툼스톤 등록을 건너뛰어 향후 재수집이 가능합니다.
  * **공유 링크 소각 경고**: 공유 링크로 식별된 경우 단순 링크 무효화가 아닌 원본 문서 전체 파괴임을 Dry-Run에 명시적 경고.
  * 기본 실행은 Dry-Run으로 영향 범위를 사전 출력하며, `--apply` 지정 시 실제 소각 실행 (대화형 `[y/N]` 확인 또는 `--yes`/`-y`로 무인 실행).
* `claire audit [<target>] [--pattern <str>] [--json]`:
  * 특정 키워드, URL, ID, 또는 툼스톤 대상이 DB(문서/인박스/추출/스냅샷), 로컬 디스크 파일, 엔티티 sources에 1건이라도 남아있는지 전수 검사하고 Freelist 미회수 용량을 보고.

---

### 3.9 관측성 및 문제 해결 (Observability, Telemetry & Support Bundle)

* `claire telemetry [--limit N] [--failed] [--doc <ID>] [--provider <name>] [--stats] [--prune <days>] [--json]`:
  * **물리적 스토리지 격리**: 정본 지식 DB(`claire.db`)와의 쓰기 락 충돌을 100% 방지하기 위해 완전히 독립된 `data/telemetry.db`(WAL 모드)를 사용.[^telemetry-implementation]
  * **Google 정책/가이드라인 차단 진단**: `RECITATION`, `SAFETY`, `RATE_LIMIT_429`, `QUOTA_EXCEEDED`, `MAX_TOKENS`, `INVALID_SCHEMA`, `TIMEOUT`, `ENV_MISSING` 코드를 정밀 자동 분류.
  * **요약 품질 판정**: `REAL_LLM`(정상), `RAW_SLICE_200`(200자 방어 슬라이싱 폴백), `MOCK_PREFIX`(`[mock]` 접두어), `EMPTY` 감지.
  * **옵션**:
    * `--limit N`: 조회 레코드 수 (기본값: 30).
    * `--failed`: 실패, 차단, 또는 저품질 폴백된 호출만 필터링.
    * `--doc <ID>`: 특정 문서 ID의 호출 이력만 필터링.
    * `--provider <name>`: 특정 프로바이더명으로 필터링.
    * `--stats`: 총 호출 수, 성공률, p50/p95 레이턴시, 차단 사유별 통계 요약 출력.
    * `--prune <DAYS>`: 지정 일수(예: 14일, 30일)를 초과한 구 텔레메트리 레코드 즉시 정리.
    * `--json`: 기계 판독용 JSON 포맷 출력.

* `claire support-bundle [--days N] [--target <target>] [--list] [--purge] [--json]`:
  * **RCA 전용 zstd 압축 아카이브**: 시스템 진단, 마스킹된 설정, 저장소 경로·mount·파일 identity, 테마별 read-only health, 텔레메트리, 인박스 실패 내역, 활성 공유 링크 인덱스, 프로바이더 로그를 format v3 `.tar.zst`로 패키징.[^telemetry-implementation]
  * **요청 기반 strict 타깃 역추적**: `target`으로 공유 링크(`/p?s=token`), 공유 토큰, URL, 문서 ID를 입력받는다. 복수 후보는 첫 문서로 임의 선택하지 않고 `ambiguous`, 미관측 대상은 `not_observed`, 문서 생성 전 실패 URL은 `failed_inbox`로 기록한다.
  * **인박스 이력**: `tracked_document/`에 URL·문서에 연결된 전체 `raw_inbox` 행을 포함한다. 관측성 데이터는 정본 `claire.db`에 신규 테이블을 추가하지 않는다.[^telemetry-implementation]
  * **빌드 식별**: `manifest.json`과 `diagnostics/build.json`에 이미지 빌드 시 주입된 Git commit, 패키지 버전, DB 스키마 버전·계보 및 이미지 태그를 기록한다.
  * **다운로드 내구성**: `telemetry.db`의 레코드와 원문 토큰을 포함하지 않는 권한 `0600` SHA-256 sidecar를 함께 기록한다. DB 레코드가 유실되거나 읽기 실패해도 사용자가 가진 토큰과 sidecar가 일치하면 유효기간 안의 파일을 제공한다.
  * **6시간 자동 파기**: 번들 생성 시 6시간 유효한 보안 다운로드 토큰(`GET /support/bundle?token=...`)을 발급하며, 생성 6시간 경과 시 디스크 및 DB에서 자동 파기 (`410 Gone`).
  * **옵션**:
    * `--days N`: 수집 대상 기간 (기본값: 1일). 텔레메트리 보관 기한(기본 30일)을 초과할 수 없음.
    * `--target <target>`: 추적 대상 공유 링크, 토큰, URL 또는 문서 ID 지정. 문서가 없어도 일치하는 실패 인박스나 `not_observed` 진단 상태를 포함한 번들을 생성한다.
    * `--list`: 현재 유효한(미만료) Support Bundle 목록 및 토큰 조회.
    * `--purge`: 6시간을 초과한 만료 번들 즉시 수동 파기.
    * `--json`: 번들 메타데이터를 JSON 포맷으로 출력.

* `Telegram 봇: /support bundle [<일수>] [<대상>]`, `/support bundle list`, `/support bundle purge`:
  * **원격 진단 번들 발급**: 텔레그램 채팅창에서 `/support bundle` 명령으로 즉시 최근 1일(또는 지정 일수, 대상 문서)의 zstd 진단 번들을 생성하고 6시간 다운로드 링크를 회신받음.[^telemetry-implementation]
  * **인라인 원터치 액션 (`sb:{doc_id}`)**: 보관 문서의 공유 링크(`/p?s=token`) 또는 문서 ID를 봇에 전송하면 나타나는 스마트 액션 메뉴에 `[📦 Support Bundle 생성]` 버튼이 제공되어 원클릭으로 특정 문서 추적 번들 생성 가능.
  * **문서 첨부 전송**: 봇은 6시간 다운로드 URL 회신과 함께 생성된 `.tar.zst` 파일을 텔레그램 문서로 채팅방에 직접 첨부 전송(best-effort).

---

### 3.10 지식 테마 관리 및 다중 DB 격리 (Theme Management)

`claire theme` 명령군은 지식 테마 레지스트리(`themes.json`) 및 시퀀스 기반의 물리적 저장소(`data/themes/{seq}/claire.db`, `vault/themes/{seq}/`)를 생성, 변경, 조회, 삭제/소각(`--purge`)하는 관리자 CLI 도구입니다.[^theme-implementation]

| 명령 | 사용법 | 설명 |
| :--- | :--- | :--- |
| `theme list` | `claire theme list [--json]` | 등록된 테마 목록, 활성화 상태, 공개 여부 및 통계(문서/엔티티/관계) 조회 |
| `theme define` | `claire theme define --label <L> [--desc <D>] [--icon <I>] [--focus <F>] [--public\|--private] [--collaborator\|--no-collaborator] [--json]` | 다음 시퀀스 번호의 신규 테마 디렉토리 및 DB/볼륨을 자동 프로비저닝하여 등록 |
| `theme update` | `claire theme update <id_or_label> [--label <L>] [--desc <D>] [--icon <I>] [--focus <F>] [--public\|--private] [--collaborator\|--no-collaborator] [--json]` | 기존 테마의 레이블, 설명, 아이콘, 기본 초점, 공개/협력자 접근 권한 수정 |
| `theme delete` | `claire theme delete <id> [--purge] [--yes] [--json]` | 테마 레지스트리에서 비활성화/삭제. `--purge` 지정 시 물리적 DB 및 vault 영구 소각 (기본 테마 `0`은 삭제 불가 보호) |

#### `claire theme list`
* **사용법**: `claire theme list [--json]` (또는 `claire theme` 단독 실행 시 기본 동작)
* **출력 항목**:
  * Sequence ID, 레이블(Label), 아이콘(Icon), 설명(Description)
  * 공개 여부 (Public / Private), 협력자 접근 허용 여부 (Collab O/X)
  * 기본 초점 (Default Focus: 지식 적재 시 자동 적용될 Directive)
  * 문서/엔티티/관계 통계 및 물리적 DB 경로 (`data/themes/{seq}/claire.db`)
* **`--json`**: 기계 가독형 JSON 배열로 출력.

#### `claire theme define`
* **사용법**: `claire theme define --label <L> [--desc <D>] [--icon <I>] [--focus <F>] [--public|--private] [--collaborator|--no-collaborator] [--json]`
* **주요 옵션**:
  * `--label`, `-l` *(필수)*: 테마 명칭 (예: `기계학습`, `재정/회계`). 기존 테마 레이블과 중복 불가.
  * `--desc`, `--description`: 테마 설명 (웹 UI 테마 선택 팝오버 및 텔레그램 안내에 노출).
  * `--icon`: 테마 식별 이모지 (기본값: `📁`).
  * `--focus`, `--default-focus`: 해당 테마에 문서 적재 시 기본 적용될 초점(지시문/Directive). 사용자가 적재 시 별도 `--focus`를 주지 않으면 이 값이 자동으로 적용됩니다.
  * `--public` / `--private`: 테마 공개 여부 (기본값: `--public`). `--private` 시 비인증(Anonymous) 사용자의 웹/API 열람이 차단되며(404 Not Found), 세션 토큰 소유자만 접근 가능합니다.
  * `--collaborator` / `--no-collaborator`: 협력자 세션(`CLAIRE_COLLABORATOR_TOKEN`) 접근 허용 여부 (기본값: `--collaborator`). `--no-collaborator` 지정 시 시스템 관리자(Owner)만 열람/적재 가능합니다.
* **디렉토리 프로비저닝**: 테마 생성 즉시 `data/themes/{seq}/` 및 `vault/themes/{seq}/` 디렉토리가 생성되고 독립된 SQLite DB 초기화(`init_db`)가 수행됩니다.

#### `claire theme update`
* **사용법**: `claire theme update <id_or_label> [--label <L>] [--desc <D>] [--icon <I>] [--focus <F>] [--public|--private] [--collaborator|--no-collaborator] [--json]`
* **주요 옵션**:
  * `id`: 대상 테마의 ID, 시퀀스 번호, 또는 레이블.
  * `--focus ""`: 빈 문자열을 전달하여 기존에 설정된 기본 초점을 해제(초기화) 가능.
  * `--private`, `--no-collaborator`: 기존 공개/협력자 허용 테마의 접근 권한을 동적으로 즉시 회수 가능.
  * 기본 테마 `0`(Default Theme)의 경우 레이블, 설명, 아이콘, 공개 여부는 변경 가능하지만 기본 초점은 `""`로 고정됩니다.

#### `claire theme delete`
* **사용법**: `claire theme delete <id> [--purge] [--yes] [--json]`
* **주요 옵션**:
  * `id`: 삭제할 테마의 ID 또는 시퀀스 번호.
  * `--purge`: 테마 레지스트리 제거뿐만 아니라 디스크 상의 `data/themes/{seq}/claire.db` 파일 및 `vault/themes/{seq}/` 보관소 디렉토리까지 영구 소각(shredding/rmtree).
  * `--yes`, `-y`: 삭제 확인 대화형 프롬프트 건너뛰기.
* **안전 보호 장치 (Safety Guard)**:
  * **기본 테마 `0` 삭제 불가**: 테마 `0`은 시스템의 루트 지식베이스이므로 삭제 시도 시 즉시 거부되고 오류 코드 1을 반환합니다.
  * 비정상 접근 및 오염 방지를 위해 `--purge` 누락 시에는 레지스트리에서만 비활성화/제거되고 물리적 파일은 디스크에 보존됩니다.

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
  * `regenerate --detail`은 상세 렌더링 텍스트를 새로 작성하지만, 본문 변경에 따른 새로운 엔티티/관계의 자동 재추출은 수행하지 않습니다. 본문 내용 변경에 따른 전체 그래프 갱신이 필요할 경우 `reextract`를 실행해야 합니다.

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
[^video-caption-implementation]: Claire Bible 구현 근거: [`src/claire/ingest/fetchers/captions.py`](../../../src/claire/ingest/fetchers/captions.py), [`src/claire/ingest/fetchers/video.py`](../../../src/claire/ingest/fetchers/video.py), [`tests/test_video_captions.py`](../../../tests/test_video_captions.py) (2026-09-04 확인).
[^video-presentation-implementation]: Claire Bible 구현 근거: [`src/claire/ingest/fetchers/presentation_vmware_explore.py`](../../../src/claire/ingest/fetchers/presentation_vmware_explore.py), [`src/claire/ingest/fetchers/pdf.py`](../../../src/claire/ingest/fetchers/pdf.py), [`src/claire/ingest/pipeline.py`](../../../src/claire/ingest/pipeline.py), [`src/claire/store/raw.py`](../../../src/claire/store/raw.py), [`tests/test_video_presentation.py`](../../../tests/test_video_presentation.py) (2026-09-04 확인). 설계 근거: [VIDEO_PRESENTATION_BUNDLE_INGESTION_DESIGN.md](../design/VIDEO_PRESENTATION_BUNDLE_INGESTION_DESIGN.md).
[^telemetry-implementation]: Claire Bible 구현 근거: [`src/claire/store/telemetry.py`](../../../src/claire/store/telemetry.py), [`src/claire/store/db.py`](../../../src/claire/store/db.py), [`src/claire/support_bundle.py`](../../../src/claire/support_bundle.py), [`src/claire/telegram_bot.py`](../../../src/claire/telegram_bot.py), [`src/claire/cli.py`](../../../src/claire/cli.py), [`ops/cb_manuscript.py`](../../../ops/cb_manuscript.py), [`tests/test_migrate.py`](../../../tests/test_migrate.py), [`tests/test_telemetry.py`](../../../tests/test_telemetry.py), [`tests/test_support_bundle.py`](../../../tests/test_support_bundle.py), [`tests/test_bot.py`](../../../tests/test_bot.py) (2026-09-11 확인). 설계 근거: [TELEMETRY_AND_SUPPORT_BUNDLE_DESIGN.md](../design/TELEMETRY_AND_SUPPORT_BUNDLE_DESIGN.md).
[^theme-implementation]: Claire Bible 구현 근거: [`src/claire/store/theme.py`](../../../src/claire/store/theme.py), [`src/claire/cli.py`](../../../src/claire/cli.py), [`src/claire/api/server.py`](../../../src/claire/api/server.py), [`src/claire/telegram_bot.py`](../../../src/claire/telegram_bot.py), [`tests/test_cli_theme.py`](../../../tests/test_cli_theme.py), [`tests/test_theme_manager.py`](../../../tests/test_theme_manager.py), [`tests/test_theme_api.py`](../../../tests/test_theme_api.py), [`tests/test_theme_collaborator.py`](../../../tests/test_theme_collaborator.py), [`tests/test_theme_web.py`](../../../tests/test_theme_web.py) (2026-09-10 확인). 설계 근거: [MULTI_THEME_ARCHITECTURE_DESIGN.md](../design/MULTI_THEME_ARCHITECTURE_DESIGN.md).
[^multi-theme-operations]: Claire Bible 멀티 테마 운영 data-plane 구현 근거: [`src/claire/store/theme.py`](../../../src/claire/store/theme.py), [`src/claire/health.py`](../../../src/claire/health.py), [`src/claire/cli.py`](../../../src/claire/cli.py), [`tests/test_theme_manager.py`](../../../tests/test_theme_manager.py), [`tests/test_health.py`](../../../tests/test_health.py), [`tests/test_migrate.py`](../../../tests/test_migrate.py), [`tests/test_multi_theme_workers.py`](../../../tests/test_multi_theme_workers.py) (2026-09-11 확인).
