# 공통 스키마 버전 정책

이 문서는 Git 커밋 계보가 분리된 Claire Bible 업스트림과 공개 오리진이 SQLite 정본을 안전하게 다루기 위한 버전 계약을 정의한다.
Git 조상 관계나 저장소 이름은 DB에 남지 않으므로 `meta.schema_version`과 `meta.schema_lineage`를 함께 호환성 판단에 사용한다.

## 계약 계열과 namespace

서로 다른 저장소와 직렬화 형식은 같은 version 숫자를 공유하지 않는다. 숫자가 같더라도
아래 namespace가 다르면 호환 버전으로 해석하지 않는다.

| 계약 계열 | version/lineage namespace | 현재 상태 | 변경 규칙 |
|---|---|---|---|
| 정본 지식 DB (`claire.db`, 기본·테마) | `meta.schema_version=13`, `meta.schema_lineage=claire-bible/common` | **Implemented** | 공통 객체 변경은 다음 common version을 upstream PR-first로 제안 |
| 오리진 지식 DB 확장 | `fofwisdom.claire-bible/origin` | **Policy / 별도 version marker 미구현** | common 번호를 선점하지 않고 오리진 extension version으로 관리 |
| 물리 telemetry DB (`telemetry.db`) | `claire-bible/telemetry`, telemetry schema v1 | **Planned / 미구현** | 지식 DB version/lineage와 독립적으로 검사·마이그레이션 |
| Support Bundle archive | `support-bundle/archive` | format v3 **Implemented**, v4 **Planned / 미구현** | archive reader/writer 호환 계약으로 관리 |
| Support Bundle DB/sidecar registry record | `support-bundle/registry` | telemetry DB row는 **unversioned**, sidecar record v1 **Implemented**, registry v2 **Planned / 미구현** | archive format과 telemetry 물리 schema에서 독립 관리 |
| `cb-manuscript` backup archive | `cb-manuscript/backup` | format v1 **Implemented**, v2 **Planned / 미구현** | 전체 DB inventory manifest 계약으로 별도 승격 |

Support Bundle archive format, 이중 registry record, telemetry DB 물리 schema는 수명주기와
호환성 판단이 다르므로 각각 독립 version 계약을 갖는다. 한 계열의 version 증가가 다른
계열의 version 증가를 암시해서는 안 된다.

## v13 수렴 계약

공통 버전 v13은 새로운 사용자 기능을 추가하는 버전이 아니다.
업스트림 v9의 테이블, 컬럼, 인덱스를 공통 최소 스키마로 삼고 `schema_lineage=claire-bible/common`을 기록하는 수렴 지점이다.
오리진은 합의된 업스트림 PR head `d599e19`의 계약을 먼저 채택한다.

오리진의 `detail_format`, `detail_html`, `purged_tombstones` 같은 부가 스키마는 이미 각 설계 문서에 따라 운영되는 구현 확장이다.
공통 최소 스키마와 이름 또는 의미가 충돌하지 않으므로 v13 전환에서도 보존한다.
이 확장들을 업스트림에 병합하는 결정은 v13 버전 수렴과 분리한다.

### v13 비침범 원칙

이미 배포된 지식 DB v13의 의미는 재작성하지 않는다. telemetry schema v1, Support Bundle
archive v4, registry record 변경, backup format v2를 도입하더라도 지식 DB의
`schema_version=13`이나 `schema_lineage=claire-bible/common`을 바꾸는 근거가 되지 않는다.

- 공통 최소 객체를 바꿔야 하면 다음 미사용 common version을 upstream PR-first로
  제안하고, 데이터 보존 migration과 contract test를 함께 제출한다.
- 오리진 전용 객체는 `fofwisdom.claire-bible/origin` extension namespace에서 별도
  version으로 관리한다. 이를 common v13의 필수 객체로 소급 편입하지 않는다.
- telemetry DB는 `claire-bible/telemetry` lineage와 v1부터 시작한다. 지식 DB v13을
  재사용하거나 지식 DB migration 함수로 추측하여 초기화하지 않는다.
- 이미 공개된 version/lineage 조합의 의미가 잘못되었으면 기존 의미를 수정하지 않고
  폐기 상태를 선언한 뒤 새 version을 발행한다.

### machine-readable 계약 (**Planned / 미구현**)

- `schema/common/v13.json`: 이미 배포된 v13 common 객체와 계보를 기술만 한다. manifest
  digest나 migration ledger를 기존 v13 DB의 필수 meta로 소급 요구하지 않는다.
- `schema/extensions/fofwisdom-origin-v1.json`: 오리진 전용 객체와 호환 common version을
  별도 extension version으로 선언한다.
- `schema/migrations/ledger.jsonl`: migration ID, source/target namespace, 코드·계약
  checksum을 append-only로 기록한다. 게시된 ID/checksum은 수정하지 않는다.

이 digest와 ledger는 먼저 backup/release manifest에 기록한다. DB 내부의 common 필수
ledger는 v13이 아니라 다음 common version(v14 이상)의 upstream PR에서만 도입한다.
common 객체 변경도 v14 이상을 upstream PR-first로 제안하고, origin-only 변경은 extension
version만 올린다.

독립 Git history 간 전달은 오리진 전체 snapshot을 병합하지 않는다. 최신
`upstream/master`에서 common manifest, 작은 migration patch와 contract test만 별도
commit/PR로 제안하고, 오리진은 해당 contract digest와 검증 결과를 release manifest에
연결한다. 양쪽 구현의 호환성 증거는 Git commit 동일성이 아니라 같은 계약과 contract
test 결과다.

| 버전 | 상태 | 오리진의 v13 처리 |
|---|---|---|
| 1–9 | 업스트림 공통 이력 | 멱등 마이그레이션 후 v13으로 전환 |
| 10–11 | 오리진의 분리 계보 이력 | 부가 스키마와 데이터를 보존하며 v13으로 전환 |
| 12 | 철회된 Support Bundle 스키마 | 진단 데이터를 보존해 v11로 철회한 뒤 v13으로 전환 |
| 13 | 현재 공통 버전 | 공통 계보 표식이 있어야 유효 |
| 14 이상 | 미래 버전 | 현재 코드가 변경하지 않고 거부 |

## v12 철회와 v13 승격 순서

`claire migrate`는 각 활성 테마 DB마다 다음 순서를 지킨다.

1. v12이면 Support Bundle 테이블의 정확한 컬럼 서명을 확인한다.
2. `BEGIN IMMEDIATE` 잠금 안에서 행을 권한 제한 JSONL과 SHA-256 manifest로 보존한다.
3. 내보내기가 내구성 있게 완료된 경우에만 잘못된 테이블을 제거하고 버전을 v11로 복구한다.
4. v11을 포함한 지원 버전의 공통 계보 호환성을 확인하고 멱등 DDL을 적용한다.
5. 버전을 v13으로 올리고 공통 계보를 기록한 뒤 두 값을 다시 검증한다.

컬럼 서명이 다른 v12, 내보내기 실패, 미래 버전, 외부 계보, 계보가 없는 v13은 추측해 변경하지 않는다.
멀티 테마에서는 하나가 실패해도 나머지 대상을 점검하지만 최종 종료 코드는 실패이며, 실패한 DB는 해당 마이그레이션 경계 이전 상태를 유지한다.

## 선채택 기록과 이후 규칙

오리진은 업스트림 PR 후보의 공통 계약을 v13 운영 기준으로 먼저 채택했다.
업스트림이 병합 과정에서 해당 계약을 변경하면 이미 배포된 v13의 의미를 다시 쓰지 않는다.
차이는 다음 미사용 공통 버전의 별도 마이그레이션으로 해결한다.

이후 공통 스키마 변경은 다음 규칙을 따른다.

1. 공통 번호는 upstream PR-first로 문서, 멱등 마이그레이션, 이전 버전 데이터 보존 테스트와 함께 제안한다.
2. 구현 전용 실험은 공통 번호를 선점하지 않는다.
3. 이미 배포된 버전의 의미를 바꾸지 않는다. 잘못된 버전은 v12처럼 폐기하고 새 번호를 사용한다.
4. `health`와 `liveness`는 버전과 계보를 읽기 전용으로 검사하며 마이그레이션하지 않는다.
5. `cb-manuscript backup`은 DB 버전과 계보를 manifest에 기록하고 `restore`는 서비스를 중지하기 전에 두 값의 호환성을 확인한다.
6. 기능 스키마의 업스트림 채택은 관련 정책 검토를 거쳐 별도 PR로 처리한다.
