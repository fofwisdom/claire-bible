# 프로바이더 텔레메트리 격리 및 Support Bundle 아키텍처 설계

이 문서는 Antigravity(`agy`) 및 멀티 LLM 프로바이더 실행 시 발생하는 이상 현상(mock 요약, Google 정책 차단, 응답 결손)의 실증 데이터를 수집하기 위한 **물리적으로 격리된 텔레메트리 서브시스템**과 근본 원인 분석(RCA)을 위한 **Support Bundle(zstd 압축, 공유 링크 추적, 6시간 자동 파기)** 아키텍처 설계를 기술합니다.

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

### 2.2 Google 가이드라인 및 API 정책 차단 정밀 진단 (`diagnose_google_block`)
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

### 2.3 요약 품질 판정 체계 (`evaluate_summary_verdict`)
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

---

## 4. API, CLI 및 텔레그램 봇 운영 인터페이스

### 4.1 REST API 엔드포인트

| 메소드 | 경로 | 권한 | 설명 |
| :--- | :--- | :--- | :--- |
| `POST` | `/support/bundle` | `owner` | Support Bundle 생성 요청. JSON `{"days": 1, "target": "..."}` 지원 |
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
