# 프로바이더 텔레메트리 격리 및 Support Bundle 아키텍처 설계

이 문서는 Antigravity(`agy`) 및 멀티 LLM 프로바이더 실행 시 발생하는 이상 현상(mock 요약, Google 정책 차단, 응답 결손)의 실증 데이터를 수집하기 위한 **물리적으로 격리된 텔레메트리 서브시스템**과 근본 원인 분석(RCA)을 위한 **Support Bundle(zstd 압축, 공유 링크 추적, 6시간 자동 파기)** 아키텍처 설계를 기술합니다.

> [!IMPORTANT]
> 이 문서에서 별도 표기가 없는 기존 텔레메트리 수집과 Support Bundle format v3 동작은
> **Implemented**입니다. 아래의 telemetry schema v1/lineage, read-only 진단 전용 연결,
> Support Bundle format v4와 이중 registry 불일치 차단은 **Planned / 미구현**입니다.

---

## 1. 설계 배경 및 변경 사유

### 1.1 문제 정의: 간헐적 Mock 및 방어 슬라이스 요약 재발
인제스트 파이프라인 운영 중, Antigravity(`agy`) 프로바이더를 사용할 때 요약 결과가 간헐적으로 `[mock]` 또는 본문 첫 200자 슬라이스(`raw_text[:200]`)로 대체되는 현상이 보고되었습니다.
- **원인 분석의 한계**: CLI 표준 에러(stderr)가 `/tmp/agy.log` 등 휘발성 경로에 기록되어 프로세스 종료 후 증거가 소실되었고, Google 정책 차단인지, RPM 429인지, 바이너리 실행 권한 문제인지 추론에 의존해야 했습니다.
- **방어 슬라이싱의 착시**: `AntigravityProvider.extract` 내에서 CLI 실패 시 `raw_text[:200]`을 요약으로 반환하는 방어 로직이 존재하여, 파이프라인은 오류가 아닌 정상 완료(`status='done'`)로 처리해 결손이 은폐되었습니다.

### 1.2 핵심 제약 조건: 정본 지식 DB(`claire.db`) 무부하·무경합 원칙
관측성 데이터를 수집하되, **정본 지식 데이터베이스(`data/claire.db`)의 안전성과 성능을 100% 보존**해야 했습니다:
1. **단일 작성자 락 경합(Single-Writer Lock Contention) 배제**: SQLite는 트랜잭션 쓰기 시 파일 전체 락을 점유합니다. 대량 문서 인제스트 도중 텔레메트리 레코드를 동일 DB에 기록하면 `busy_timeout` 초과 및 `database is locked` 에러가 발생할 위험이 있습니다.
2. **지식 DB 비대화(Bloat) 차단**: CLI 호출마다 누적되는 입출력 스니펫, 에러 로그, 통계 데이터가 본체 지식 그래프 용량을 오염시키지 않아야 합니다.
3. **Fire-and-Forget 안전성**: 텔레메트리 기록 실패가 본선 문서 인제스트 트랜잭션을 롤백시키거나 중단시켜서는 안 됩니다.

---

## 2. 물리적으로 격리된 텔레메트리 서브시스템

```mermaid
graph TD
    subgraph Ingestion_Pipeline [지식 인제스트 파이프라인]
        Worker["IngestService / AntigravityProvider"] -->|정본 지식 저장| ClaireDB[("data/claire.db (정본 지식 DB)")]
        Worker -.->|비동기/안전 기록 (Fire-and-Forget)| TelemetryStore["claire.store.telemetry"]
    end

    subgraph Isolated_Observability [격리된 관측성 스토리지]
        TelemetryStore -->|독립 WAL / 1s 타임아웃| TelemetryDB[("data/telemetry.db (독립 텔레메트리 DB)")]
        Worker -->|영구 로깅| PersistentLog["data/logs/agy.log"]
    end

    subgraph RCA_Subsystem [근본 원인 분석 (Support Bundle)]
        BundleManager["claire.support_bundle"] -->|조회| TelemetryDB
        BundleManager -->|읽기 전용 조회| ClaireDB
        BundleManager -->|로그 수집| PersistentLog
        BundleManager -->|zstd 압축 (Level 3)| BundleArchive["data/support_bundles/*.tar.zst"]
    end

    style ClaireDB fill:#dbeafe,stroke:#3b82f6,stroke-width:2px
    style TelemetryDB fill:#d1fae5,stroke:#10b981,stroke-width:2px
    style BundleArchive fill:#fef3c7,stroke:#f59e0b,stroke-width:2px
```

### 2.1 스토리지 물리 분리 (`data/telemetry.db`)
- `src/claire/store/telemetry.py`에 완전 독립된 SQLite 연결 풀 구성.
- 전용 WAL 모드(`PRAGMA journal_mode=WAL;`) 및 단기 타임아웃(`PRAGMA busy_timeout=1000;`) 적용.
- 본선 트랜잭션과 쓰기 락이 완벽히 분리되어 상호 간섭 0%.

### 2.2 telemetry schema v1과 독립 lineage (**Planned / 미구현**)

`telemetry.db`는 정본 지식 DB와 다른 저장소 계열이다. 최초 version 계약은
`meta.schema_version=1`, `meta.schema_lineage=claire-bible/telemetry`로 정의한다. 같은
`meta` key 이름을 사용하더라도 파일과 lineage가 다르므로 지식 DB의 common v13과 숫자나
마이그레이션 계보를 공유하지 않는다.

- 새 telemetry DB는 명시적인 쓰기 초기화 경로에서 v1 물리 schema와 version/lineage를
  한 transaction으로 생성한다.
- version/lineage가 알려진 현재 값이면 schema signature까지 확인한다.
- 알려지지 않은 lineage, 현재 구현보다 높은 future version, version은 현재지만 lineage가
  없는 DB는 변경하지 않고 실패한다.
- 지식 DB와 telemetry DB의 검증 결과는 독립적으로 보고한다. 어느 한쪽의 정상 상태로
  다른 한쪽의 unknown/future schema를 허용하지 않는다.
- 일반 인제스트의 fire-and-forget 기록 실패는 지식 DB transaction을 롤백시키지 않는다.
  unknown/future telemetry는 telemetry read/write와 Support Bundle DB registry 기능에서
  fail-closed로 처리하되, 정본 지식 DB update는 degraded 경고와 함께 계속한다.

#### exact legacy unversioned signature 승격

기존에 생성된 version 없는 telemetry DB는 다음 두 exact source signature 중 하나와
완전히 같을 때만 v1로 승격한다.

- 초기형은 application table `provider_telemetry`와 그 전용 index 네 개만 존재한다.
- 현재형은 초기형에 `support_bundles` table과 그 전용 index 두 개가 더해진 형태다.
  SQLite 내부 `sqlite_sequence`는 두 signature 모두 비교에서 제외한다.
- `provider_telemetry`의 column 이름·순서·type·nullability·default·PK는 현재
  `TELEMETRY_SCHEMA`의 `id`부터 `summary_verdict`까지와 정확히 같아야 한다.
- 현재형의 `support_bundles`도 현재 `bundle_id`부터 `expires_at`까지 같은 기준으로
  정확히 같아야 한다.
- application index는 `idx_telemetry_doc`, `idx_telemetry_status`,
  `idx_telemetry_reason`, `idx_telemetry_time`, `idx_support_bundles_token`,
  `idx_support_bundles_expires`의 이름·대상 column 순서와 정확히 같아야 한다.
- 두 signature 이외의 조합은 fail-closed로 거부한다. migration은 `BEGIN IMMEDIATE` 후
  source signature를 다시 확인하고 v1 목표 schema와 meta를 기록한 뒤 commit한다.
  누락·추가·변형된 application object가 있거나 재검증이 달라지면 rollback하고 원본을
  변경하지 않는다. 비슷해 보이는 schema를 보정하거나 추측하지 않는다.

#### 진단 전용 read-only 연결

현재 `connect_telemetry()`는 연결 시 WAL 설정과 `executescript(TELEMETRY_SCHEMA)`를
실행하므로 Support Bundle의 telemetry 조회와 `quick_check`도 엄밀한 read-only 경계가
아니다. 계획상 연결을 다음처럼 분리한다.

| 연결 역할 | 허용 동작 |
|---|---|
| writer/init/migrate | 파일·directory 생성, WAL 설정, v1 초기화와 exact legacy migration |
| diagnostics/query | 기존 파일에 SQLite URI `mode=ro` 및 `PRAGMA query_only=ON`으로 연결; DDL·migration·registry 쓰기 금지 |

진단 경로는 파일이 없으면 `absent`를 보고하고 생성하지 않는다. unknown/future telemetry
schema는 오류 상태로 보고하되 자동 migration은 명시적인 batch 경로에서만 수행한다.

### 2.3 Google 가이드라인 및 API 정책 차단 정밀 진단 (`diagnose_google_block`)
CLI 반환 코드, stderr, stdout을 분석하여 차단 원인을 8개 카테고리로 자동 분류합니다:

| 진단 코드 | 분류 기준 및 원인 |
| :--- | :--- |
| `RECITATION` | 학술 논문이나 저작권 텍스트 복제 방지 필터에 걸려 생성이 중단된 경우 |
| `SAFETY` | 금융, 보안, 위험 물질 등 Google 안전 가이드라인 위반으로 차단된 경우 |
| `RATE_LIMIT_429` | 분당 요청 수(RPM) 또는 동시 요청 한도 초과 (`ResourceExhausted`) |
| `QUOTA_EXCEEDED` | 일일/계정 할당량이 소진된 경우 |
| `MAX_TOKENS` | Gemini 3.7 Flash 등의 Thinking 토큰 과다 소모로 응답이 잘린 경우 |
| `INVALID_SCHEMA` | 구조화 JSON 스키마 미준수로 인한 파싱 실패 |
| `TIMEOUT` | 네트워크 지연 또는 CLI 프로세스 데드라인 초과 |
| `ENV_MISSING` | `agy` 바이너리 부재, 실행 권한 없음, 또는 잘못된 PATH |
| `NONE` / `CLI_ERROR` | 차단 없음 (정상 완료 또는 일반 CLI 에러) |

### 2.4 요약 품질 판정 체계 (`evaluate_summary_verdict`)
생성된 요약이 실제 LLM 생성문인지, 방어 슬라이스인지 자동 판정:
- `REAL_LLM`: 정상적인 LLM 생성 텍스트.
- `RAW_SLICE_200`: 오류 시 `raw_text[:200]`로 방어 슬라이싱된 폴백 요약 감지.
- `MOCK_PREFIX`: `[mock]` 접두어가 붙은 mock 요약 감지.
- `EMPTY`: 내용 없음.

---

## 3. Support Bundle 서브시스템 설계

근본 원인 분석(RCA) 및 원격 디버깅을 위해 최근 데이터와 시스템 상태를 단일 아카이브로 패키징하고, 6시간 후 안전하게 자동 파기합니다.

### 3.1 5대 핵심 요구사항
1. **zstd 압축**: Python 내장 `zstandard` 모듈(`level=3`)과 `tarfile` 스트리밍을 결합하여 고효율 압축 `.tar.zst` 생성.
2. **요청 기반 strict 타깃 특정 및 역추적**:
   - `resolve_document_targets`를 활용하여 공유 링크(`/p?s=token`), 공유 토큰, URL, 문서 ID를 스마트 인식.
   - 복수 후보를 첫 문서로 임의 선택하지 않고 `ambiguous`로 기록하며 후보 목록을 함께 보존.
   - 문서 생성 전에 실패한 URL도 `raw_inbox.payload` 정확 일치로 찾아 `failed_inbox` 상태의 타깃 번들을 생성.
   - 대상 지정 시 `tracked_document/`에 문서 상세, URL·문서에 연결된 전체 인박스 행 및 해당 문서 텔레메트리를 집중 패키징.
   - 모든 번들의 `pipeline/shares_index.json`은 공유 토큰 원문 대신 SHA-256을 수록하여 노출 없이 대조 가능.
3. **기본 기간 1일 및 보관 기한 상한 검증**:
   - 기본 lookback 기간은 **1일(`days = 1`)**.
   - 텔레메트리 보관 기한(기본 30일)을 초과하는 요청은 API 400 Bad Request, CLI 종료 코드 2로 엄격 차단.
4. **6시간 유효기간 및 자동 파기 (`SUPPORT_BUNDLE_TTL_SECONDS = 21600`)**:
   - 번들 생성 시, 다운로드 시, 서버 기동 시(`app_lifespan`), CLI 수동 파기 시 6시간이 지난 아카이브 파일 언링크 및 DB 레코드 삭제.
5. **다운로드 레지스트리 이중화 및 저장소 경로 진단**:
   - 다운로드 토큰 원문은 기존 `telemetry.db` 레코드에만 두고, 번들 디렉터리에는 토큰의 SHA-256으로 이름을 정한 권한 `0600` sidecar를 원자적으로 함께 기록한다. sidecar 내용에도 원문 토큰을 넣지 않는다.
   - `telemetry.db` 레코드가 유실되거나 일시적으로 읽히지 않아도 sidecar와 사용자가 가진 토큰을 대조해 유효기간 안의 아카이브를 다운로드할 수 있다. 두 레지스트리는 동일한 6시간 파기 경계를 따른다.
   - 생성기는 등록 직후 다운로드 엔드포인트와 같은 조회·경로 검증 함수를 실행한다. 검증에 실패하면 아카이브와 등록을 정리하고 URL을 발급하지 않는다. 새 계약의 opaque token은 `sb3_` prefix로 배포 여부를 식별하되 나머지 난수 entropy를 유지한다.
   - Compose에서는 Telegram이 owner 인증 내부 API에 생성을 위임한다. 이로써 공개 다운로드를 담당하는 API가 아카이브와 레지스트리를 같은 `/app/data`에서 생성하며, bot과 API 사이의 생성 책임 분리를 제거한다.
   - 설치된 wheel의 모듈 위치는 `/app/.venv/.../site-packages`이므로 데이터 기준 경로로 사용하지 않는다. Dockerfile과 Compose가 `CLAIRE_APP_ROOT=/app`을 고정하고 상대 DB·vault 경로를 bind mount 아래로 해석한다. `data_dir`이 overlay filesystem에 있거나 `/app/.venv` 아래로 해석되면 잘못된 빈 DB를 연 것으로 판정한다.
   - 컨테이너가 기존 데이터 대신 새 빈 경로를 열어도 단순 `health=ok`로 오판하지 않도록 실제 DB/data/vault 절대경로, mount identity, 파일 inode·크기·수정시각, 제한된 SQLite 후보 탐색과 테마별 해석 경로를 번들에 기록한다. 파일 본문이나 시크릿은 이 진단에 포함하지 않는다.

### 3.2 Support Bundle 아카이브 구조

```text
support_bundle_<id>/
├── manifest.json                  # format v3, 생성/만료 시각, 빌드 식별자, 요청·타깃 해석 상태
├── diagnostics/
│   ├── system.json                # OS, Python, CPU, 디스크 용량, SQLite/zstd 버전, agy 환경 진단
│   ├── config_sanitized.json      # 마스킹된 애플리케이션 설정 (시크릿/토큰 ***REDACTED***)
│   ├── storage.json               # 실제 경로·mount·inode·mtime, 테마별 DB 해석, 제한된 DB 후보 메타데이터
│   ├── build.json                 # 이미지에 내장된 Git SHA, 패키지·DB 스키마 버전·계보·이미지 버전
│   └── collector_warnings.json    # 타깃 모호성, 아티팩트 누락, 빌드 식별 실패
├── telemetry/
│   ├── telemetry_records.jsonl    # 지정 기간 내 프로바이더 호출/차단 텔레메트리 전량
│   └── telemetry_stats.json       # 성공률, 지연시간 백분위(p50/p95), 차단 사유별 집계 통계
├── logs/
│   └── agy.log                    # 최근 프로바이더 입출력/에러 로그 (민감정보 마스킹)
├── pipeline/
│   ├── health.json                # 모든 활성 테마의 read-only liveness 및 전체 health 보고
│   ├── inbox_summary.json         # raw_inbox 상태별 건수
│   ├── failed_items.json          # 에러/실패 인박스 항목 상세 (RCA 핵심)
│   ├── shares_index.json          # 토큰 SHA-256과 문서 ID 매핑(토큰 원문은 마스킹)
│   └── db_integrity.json          # 테마별 경로·스키마·행 수와 claire.db/telemetry.db quick_check 결과
└── tracked_document/              # (특정 대상 지정 시에만 생성)
    ├── target_resolution.json     # 타깃 해석 결과 (matched_by, share_token 여부)
    ├── document_detail.json       # 정본 문서 메타데이터 및 온톨로지 정보
    ├── inbox_record.json          # 하위 호환용 최신 인입 상태
    ├── inbox_records.jsonl        # URL·문서에 연결된 전체 인입 행
    └── telemetry_history.jsonl    # 해당 문서에 특화된 텔레메트리 호출 이력
```

`manifest.json`에는 다운로드 토큰을 넣지 않는다. 컨테이너의 Git 식별자는 런타임
`git rev-parse`에 의존하지 않고 `cb-manuscript`가 `CLAIRE_BUILD_COMMIT` build argument로
주입하며 OCI `org.opencontainers.image.revision` label에도 같은 값을 기록한다.[^support-build]

### 3.3 독립 version 계약과 v4 전환 (**일부 Implemented / v4 Planned**)

세 계약을 하나의 `schema_version`으로 묶지 않는다.

| 계약 | 현재 구현 | 다음 계획 |
|---|---|---|
| archive manifest 및 tar member 계약 | `bundle_format_version=3` | Support Bundle archive format v4 |
| DB/sidecar registry record 계약 | sidecar `registry_format_version=1` | registry record v2 및 양쪽 `archive_sha256` |
| telemetry DB의 `support_bundles` 물리 table | version/lineage 없음 | telemetry schema v1의 일부로 고정 |

Support Bundle v4는 v3의 의미를 소급 변경하지 않는다. reader가 지원하는 archive format과
registry record version을 각각 명시하고, telemetry schema v1 호환성도 별도로 검사한다.
archive format 증가만으로 telemetry DB나 registry record를 migration해서는 안 된다.

v4 manifest는 archive 내부 artifact마다 독립 schema version을 매핑한다. 최소 계약은
`telemetry/telemetry_records.jsonl`의 artifact schema v1과
`pipeline/db_integrity.json`의 artifact schema v2다. artifact version 증가는 archive
format, registry record 또는 telemetry 물리 schema version을 암묵적으로 올리지 않는다.

`diagnostics/build.json`은 image가 기대하는 지식 DB 계약
(`schema_version=13`, `schema_lineage=claire-bible/common`)과 inventory가 각 DB에서 실제로
관측한 version/lineage를 서로 다른 필드로 기록한다. expected 값으로 actual 값을
덮어쓰거나, 한 DB의 actual 값을 전체 inventory의 상태로 대체하지 않는다.

#### 이중 registry 일치 검증 (**Planned / 미구현**)

현재 조회는 telemetry DB registry를 우선하고 레코드가 없거나 조회가 실패하면 SHA-256
sidecar로 폴백한다. registry v2 계획에서는 DB record와 sidecar 양쪽에
`archive_sha256`을 기록하고, 동일 token에 대해 두 레코드가 모두 존재할 때 다음
필드가 일치하는지 검증한다.

- `bundle_id`, `filename`, `filepath`
- `days_covered`, `target_doc_id`, `size_bytes`
- `created_at`, `expires_at`, `archive_sha256`

두 레코드가 모두 있는데 값이 다르거나 archive 경로·크기·digest 검증이 다르면 임의의
한쪽을 신뢰하지 않고 다운로드·목록·파기를 fail-closed 처리한다. 한쪽 record만 존재하고
그 record와 archive hash가 유효하면 해당 record로 fallback을 허용한다. 자동으로 다른
registry를 덮어써서 불일치를 숨기지 않으며, reconcile은 별도 쓰기 명령과 감사 기록을
통해서만 수행한다. registry v2 도입에 필요한 telemetry table 변경은 telemetry 물리
schema version에서 별도로 선언한다.

---

## 4. API, CLI 및 텔레그램 봇 운영 인터페이스

### 4.1 REST API 엔드포인트

| 메소드 | 경로 | 권한 | 설명 |
| :--- | :--- | :--- | :--- |
| `POST` | `/support/bundle` | `owner` | Support Bundle 생성 요청. JSON `{"days": 1, "target": "..."}` 지원. Telegram도 Compose 내부에서 이 경로를 사용 |
| `GET` | `/support/bundle?token=...` | `public` | 6시간 유효 토큰 기반 zstd 아카이브 다운로드. 만료 시 `410 Gone` 및 파일 자동 삭제 |

### 4.2 CLI 명령어

#### `claire telemetry`
- `claire telemetry`: 최근 텔레메트리 기록 30건 조회.
- `claire telemetry --failed`: 실패, Google 정책 차단, 저품질 폴백 건만 필터링.
- `claire telemetry --doc <id>`: 특정 문서의 호출 및 차단 이력 조회.
- `claire telemetry --stats`: 성공률, p50/p95 레이턴시, 차단 사유별 통계 요약.
- `claire telemetry --prune <days>`: 보관 기한 초과 레코드 정리.

#### `claire support-bundle`
- `claire support-bundle`: 기본 1일치 번들 생성 및 다운로드 링크/만료시각 출력.
- `claire support-bundle --target "<share_link>"`: 특정 공유 링크/문서 전용 추적 번들 생성.
- `claire support-bundle --days N`: 기간 설정 (30일 초과 시 차단).
- `claire support-bundle --list`: 활성 유효 번들 및 토큰 목록 조회.
- `claire support-bundle --purge`: 만료 번들 즉시 수동 파기.

### 4.3 텔레그램 봇 인터페이스 (`/support bundle`)

모바일이나 원격 환경에서 서버 직접 접속(SSH) 없이 즉시 장애 원인을 진단하고 서포트 번들을 수령할 수 있도록 텔레그램 봇 명령 및 스마트 인라인 액션을 제공합니다.

1. **명령어 구문**:
   - `/support bundle`: 기본 1일치 zstd 압축 진단 번들 생성, 6시간 다운로드 링크 회신 및 파일 직접 첨부 전송(best-effort).
   - `/support bundle <일수>`: 지정 일수(최대 30일) 번들 생성 (예: `/support bundle 3`).
   - `/support bundle <공유URL|문서ID>`: 특정 문서 집중 추적 번들 생성 (예: `/support bundle https://kb.example.com/p?s=token`).
   - `/support bundle <일수> <대상>`: 기간 및 특정 문서 동시 지정.
   - `/support bundle list`: 현재 활성(미만료) 번들 목록 및 다운로드 URL 확인.
   - `/support bundle purge`: 6시간을 경과한 만료 번들 즉시 파기.

2. **공유 링크 원터치 인라인 액션 (`sb:{doc_id}`)**:
   - 사용자가 텔레그램 채팅창에 보관 문서의 공유 링크(`/p?s=token`)나 문서 ID를 전송하면, 재생성/재수집 버튼과 함께 `[📦 Support Bundle 생성]` 인라인 버튼이 자동 제공됩니다.
   - 버튼 클릭 시 해당 문서를 대상으로 즉시 Support Bundle을 생성하고 다운로드 링크와 첨부 파일을 제공합니다.

---

## 5. 구현 참조 파일
- 텔레메트리 격리 스토어: [`src/claire/store/telemetry.py`](../../../src/claire/store/telemetry.py)
- Support Bundle 코어: [`src/claire/support_bundle.py`](../../../src/claire/support_bundle.py)
- 텔레그램 봇 인터페이스: [`src/claire/telegram_bot.py`](../../../src/claire/telegram_bot.py)
- 프로바이더 계측: [`src/claire/extract/antigravity_provider.py`](../../../src/claire/extract/antigravity_provider.py)
- 웹 API 및 보안 경계: [`src/claire/api/server.py`](../../../src/claire/api/server.py), [`src/claire/api/security.py`](../../../src/claire/api/security.py)
- CLI 인터페이스: [`src/claire/cli.py`](../../../src/claire/cli.py)
- 호스트 운영 래퍼: [`ops/cb_manuscript.py`](../../../ops/cb_manuscript.py)
- 자동화 테스트: [`tests/test_telemetry.py`](../../../tests/test_telemetry.py), [`tests/test_support_bundle.py`](../../../tests/test_support_bundle.py), [`tests/test_migrate.py`](../../../tests/test_migrate.py), [`tests/test_bot.py`](../../../tests/test_bot.py)

[^support-build]: Claire Bible 구현 근거: [`Dockerfile`](../../../Dockerfile), [`docker-compose.yml`](../../../docker-compose.yml), [`ops/cb_manuscript.py`](../../../ops/cb_manuscript.py), [`src/claire/support_bundle.py`](../../../src/claire/support_bundle.py) (2026-09-11 확인).
