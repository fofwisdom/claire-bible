# 프로바이더 및 MCP 텔레메트리 격리·Support Bundle 통합 아키텍처 설계

- **Status**: Partly implemented. The isolated telemetry database, provider and MCP telemetry, and Support Bundle format v3 with the sidecar registry are implemented: current behavior is in [`TELEMETRY.md`](../implementation/TELEMETRY.md). Planned, not implemented: telemetry schema v1 and its lineage, read-only diagnostic connections (§2.2), Support Bundle format v4 and the registry v2 match check (§3.5).
- **Date**: 2026-09-10 · condensed 2026-11-16

## 1. 설계 배경 및 변경 사유

### 1.1 문제 정의: LLM 이상 현상 및 외부 MCP 에이전트 관측성 사각지대
1. **인제스트 및 LLM 프로바이더 결손 은폐**:
   - Antigravity(`agy`) 프로바이더 사용 시 요약 결과가 간헐적으로 `[mock]` 또는 본문 첫 200자 슬라이스(`raw_text[:200]`)로 대체되는 현상이 보고되었다.
   - CLI 표준 에러(stderr)가 휘발되어 Google 정책 차단, RPM 429, 바이너리 권한 문제 추적이 불가능했고, 방어 슬라이싱이 정상 완료(`status='done'`)로 오판되는 문제가 있었다.
2. **외부 MCP 에이전트 연동의 관측성 부재 (100% Blind Spot)**:
   - Claire는 RFC 6749/6750/7591/7636/8414/9728 표준 OAuth 2.1 인가 서버와 Streamable HTTP MCP 엔드포인트(`/mcp`)를 구축하여 외부 에이전트와 통신한다.
   - 그러나 2026-09-22 프로덕션 Support Bundle을 실측 분석한 결과, MCP 도구 호출 텔레메트리, OAuth 인가/토큰 상태, API 서버 접근 로그가 완전히 누락되어 있어 외부 클라이언트 통신 장애 시 원격 근본 원인 분석(RCA)이 불가능한 한계가 입증되었다.

### 1.2 Decisions in force for the implemented part

- **Telemetry is physically separate from the knowledge database.** SQLite locks the whole file for a write. Telemetry writes during a large ingest or many MCP calls must not cause `database is locked` on the knowledge database, and telemetry volume must not grow it. This is principle A4 in [`PRINCIPLES.md`](../PRINCIPLES.md). The v12 schema that put diagnostic tables in the knowledge database was withdrawn for this reason.
- **Telemetry is fire-and-forget.** A telemetry failure must not stop an ingest transaction or an MCP response.
- **A fallback summary must be visible.** A defensive slice of the raw text or a mock summary must not look like a real model result. The summary verdict and the block-reason classification make these cases visible in telemetry.
- **A Support Bundle is short-lived.** It can hold logs and configuration, so it expires after 6 hours and is deleted on creation, on download, at start, and on request.
- **A diagnostic target is never guessed.** An ambiguous target is recorded as ambiguous with its candidates. A URL that failed before a document existed is found in the inbox.
- **Secrets never go into a bundle, but public source text stays readable.** Masking matches secret words at word edges, and an allow list keeps public text fields such as `author` and `title`.
- **The download registry has two copies.** The database row and a sidecar file named by the token digest let a valid download work when one copy is lost. Neither copy stores the token.
- **The API that serves the download also makes the bundle.** The bot asks the internal API, so that the archive and the registry are in the same data directory.
- **Different contracts do not share one version number.** The archive format, the registry record format, and the telemetry schema each have their own version.

## 2. Planned: telemetry schema version

### 2.2 telemetry schema v1과 독립 lineage (**Planned / 미구현**)

> Note (2026-11-16): the unversioned telemetry database now also has the table `mcp_telemetry` with four indexes, and the column `support_bundles.account_id`. The exact legacy signatures below were written before these objects. A v1 migration must add these signatures.

`telemetry.db`는 정본 지식 DB와 다른 저장소 계열이다. 최초 version 계약은 `meta.schema_version=1`, `meta.schema_lineage=claire-bible/telemetry`로 정의한다. 같은 `meta` key 이름을 사용하더라도 파일과 lineage가 다르므로 지식 DB의 common v13과 숫자나 마이그레이션 계보를 공유하지 않는다.

- 새 telemetry DB는 명시적인 쓰기 초기화 경로에서 v1 물리 schema와 version/lineage를 한 transaction으로 생성한다.
- version/lineage가 알려진 현재 값이면 schema signature까지 확인한다.
- 알려지지 않은 lineage, 현재 구현보다 높은 future version, version은 현재지만 lineage가 없는 DB는 변경하지 않고 실패한다.
- 지식 DB와 telemetry DB의 검증 결과는 독립적으로 보고한다. 어느 한쪽의 정상 상태로 다른 한쪽의 unknown/future schema를 허용하지 않는다.
- 일반 인제스트의 fire-and-forget 기록 실패는 지식 DB transaction을 롤백시키지 않는다. unknown/future telemetry는 telemetry read/write와 Support Bundle DB registry 기능에서 fail-closed로 처리하되, 정본 지식 DB update는 degraded 경고와 함께 계속한다.

#### exact legacy unversioned signature 승격

기존에 생성된 version 없는 telemetry DB는 다음 두 exact source signature 중 하나와 완전히 같을 때만 v1로 승격한다.

- 초기형은 application table `provider_telemetry`와 그 전용 index 네 개만 존재한다.
- 현재형은 초기형에 `support_bundles` table과 그 전용 index 두 개가 더해진 형태다. SQLite 내부 `sqlite_sequence`는 두 signature 모두 비교에서 제외한다.
- `provider_telemetry`의 column 이름·순서·type·nullability·default·PK는 현재 `TELEMETRY_SCHEMA`의 `id`부터 `summary_verdict`까지와 정확히 같아야 한다.
- 현재형의 `support_bundles`도 현재 `bundle_id`부터 `expires_at`까지 같은 기준으로 정확히 같아야 한다.
- application index는 `idx_telemetry_doc`, `idx_telemetry_status`, `idx_telemetry_reason`, `idx_telemetry_time`, `idx_support_bundles_token`, `idx_support_bundles_expires`의 이름·대상 column 순서와 정확히 같아야 한다.
- 두 signature 이외의 조합은 fail-closed로 거부한다. migration은 `BEGIN IMMEDIATE` 후 source signature를 다시 확인하고 v1 목표 schema와 meta를 기록한 뒤 commit한다. 누락·추가·변형된 application object가 있거나 재검증이 달라지면 rollback하고 원본을 변경하지 않는다. 비슷해 보이는 schema를 보정하거나 추측하지 않는다.

#### 진단 전용 read-only 연결

현재 `connect_telemetry()`는 연결 시 WAL 설정과 `executescript(TELEMETRY_SCHEMA)`를 실행하므로 Support Bundle의 telemetry 조회와 `quick_check`도 엄밀한 read-only 경계가 아니다. 계획상 연결을 다음처럼 분리한다.

| 연결 역할 | 허용 동작 |
|---|---|
| writer/init/migrate | 파일·directory 생성, WAL 설정, v1 초기화와 exact legacy migration |
| diagnostics/query | 기존 파일에 SQLite URI `mode=ro` 및 `PRAGMA query_only=ON`으로 연결; DDL·migration·registry 쓰기 금지 |

진단 경로는 파일이 없으면 `absent`를 보고하고 생성하지 않는다. unknown/future telemetry schema는 오류 상태로 보고하되 자동 migration은 명시적인 batch 경로에서만 수행한다.

## 3. Planned: Support Bundle format v4 and registry v2

### 3.5 독립 version 계약과 v4 전환 (**일부 Implemented / v4 Planned**)

세 계약을 하나의 `schema_version`으로 묶지 않는다.

| 계약 | 현재 구현 | 다음 계획 |
|---|---|---|
| archive manifest 및 tar member 계약 | `bundle_format_version=3` | Support Bundle archive format v4 |
| DB/sidecar registry record 계약 | sidecar `registry_format_version=1` | registry record v2 및 양쪽 `archive_sha256` |
| telemetry DB의 `support_bundles` 물리 table | version/lineage 없음 | telemetry schema v1의 일부로 고정 |

Support Bundle v4는 v3의 의미를 소급 변경하지 않는다. reader가 지원하는 archive format과 registry record version을 각각 명시하고, telemetry schema v1 호환성도 별도로 검사한다. archive format 증가만으로 telemetry DB나 registry record를 migration해서는 안 된다.

v4 manifest는 archive 내부 artifact마다 독립 schema version을 매핑한다. 최소 계약은 `telemetry/telemetry_records.jsonl`의 artifact schema v1과 `pipeline/db_integrity.json`의 artifact schema v2다. artifact version 증가는 archive format, registry record 또는 telemetry 물리 schema version을 암묵적으로 올리지 않는다.

`diagnostics/build.json`은 image가 기대하는 지식 DB 계약(`schema_version`, `schema_lineage=claire-bible/common`; written when the expected version was 13)과 inventory가 각 DB에서 실제로 관측한 version/lineage를 서로 다른 필드로 기록한다. expected 값으로 actual 값을 덮어쓰거나, 한 DB의 actual 값을 전체 inventory의 상태로 대체하지 않는다.

#### 이중 registry 일치 검증 (**Planned / 미구현**)

현재 조회는 telemetry DB registry를 우선하고 레코드가 없거나 조회가 실패하면 SHA-256 sidecar로 폴백한다. registry v2 계획에서는 DB record와 sidecar 양쪽에 `archive_sha256`을 기록하고, 동일 token에 대해 두 레코드가 모두 존재할 때 다음 필드가 일치하는지 검증한다.

- `bundle_id`, `filename`, `filepath`
- `days_covered`, `target_doc_id`, `size_bytes`
- `created_at`, `expires_at`, `archive_sha256`

두 레코드가 모두 있는데 값이 다르거나 archive 경로·크기·digest 검증이 다르면 임의의 한쪽을 신뢰하지 않고 다운로드·목록·파기를 fail-closed 처리한다. 한쪽 record만 존재하고 그 record와 archive hash가 유효하면 해당 record로 fallback을 허용한다. 자동으로 다른 registry를 덮어써서 불일치를 숨기지 않으며, reconcile은 별도 쓰기 명령과 감사 기록을 통해서만 수행한다. registry v2 도입에 필요한 telemetry table 변경은 telemetry 물리 schema version에서 별도로 선언한다.
