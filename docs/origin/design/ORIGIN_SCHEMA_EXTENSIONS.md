# 오리진 스키마 확장과 복구 정책

공통 지식 DB version과 lineage의 정본은 [`docs/contracts/SCHEMA_VERSIONING.md`](../../contracts/SCHEMA_VERSIONING.md)다. 이 문서는 공개 오리진에서만 구현하거나 운영하는 schema 확장, retired version 복구와 보조 저장소 version 계획을 정의한다.

## 공통 v13 선채택

오리진은 업스트림 PR 후보 head `d599e19`의 공통 계약을 v13 운영 기준으로 먼저 채택했다. 업스트림에 병합되기 전 계약 상태는 `adopted-by-origin`이며 공동 채택 완료로 표현하지 않는다. 업스트림 병합 과정에서 차이가 생겨도 배포된 v13의 의미를 다시 쓰지 않고 다음 미사용 common version으로 해결한다.

오리진의 `detail_format`, `detail_html`, `purged_tombstones` 같은 부가 객체는 공통 최소 schema와 충돌하지 않는 구현 확장이다. 공통 v13의 필수 객체로 소급 편입하지 않으며 `fofwisdom.claire-bible/origin` extension namespace에서 별도 version으로 관리한다.

## retired v12 보존 복구

오리진에서 생성된 Support Bundle 진단 확장의 `ingest_attempts`와 `fetch_attempts`는 관측성 스토리지 물리 분리 원칙을 위반했으므로 v12와 함께 영구 폐기한다.

`claire migrate`는 v12 DB를 발견하면 다음 순서를 지킨다.

1. 두 테이블과 관련 인덱스의 정확한 컬럼 서명을 확인한다.
2. `BEGIN IMMEDIATE` 안에서 source version과 서명을 다시 확인한다.
3. 모든 행을 권한 `0600` JSONL과 행 수·바이트 수·SHA-256 manifest로 내보내고 파일과 디렉터리를 디스크에 동기화한다.
4. 내보내기가 내구성 있게 완료된 경우에만 전용 인덱스와 테이블을 제거하고 v11로 복원한다.
5. 같은 migration 실행에서 공통 계약에 따라 v11을 v13으로 승격하고 `schema_lineage=claire-bible/common`을 기록한다.

컬럼 서명이 다르거나 내보내기·동기화가 실패하면 DB transaction을 rollback하고 불완전한 내보내기를 제거한다. 알 수 없는 v12를 비슷한 schema로 추측해 변경하지 않는다.

## 오리진 보조 저장소와 포맷

서로 다른 저장소와 직렬화 형식은 같은 version 숫자를 공유하지 않는다.

| 계약 계열 | namespace | 현재 상태 |
|---|---|---|
| 오리진 지식 DB 확장 | `fofwisdom.claire-bible/origin` | 별도 version marker **Planned / 미구현** |
| 물리 telemetry DB | `claire-bible/telemetry` | telemetry schema v1 **Planned / 미구현** |
| Support Bundle archive | `support-bundle/archive` | format v3 **Implemented**, v4 **Planned / 미구현** |
| Support Bundle DB/sidecar registry | `support-bundle/registry` | DB row unversioned, sidecar v1 **Implemented**, registry v2 **Planned / 미구현** |
| `cb-manuscript` backup archive | `cb-manuscript/backup` | format v1 **Implemented**, v2 **Planned / 미구현** |

telemetry schema v1, Support Bundle format v4, registry v2와 backup format v2를 도입해도 공통 지식 DB v13의 version이나 lineage를 변경하지 않는다. 세부 계약은 [`TELEMETRY_AND_SUPPORT_BUNDLE_DESIGN.md`](TELEMETRY_AND_SUPPORT_BUNDLE_DESIGN.md)와 [`OPERATIONAL_MIGRATION.md`](OPERATIONAL_MIGRATION.md)에서 관리한다.

## machine-readable 오리진 계약 (**Planned / 미구현**)

- `schema/common/v13.json`: 이미 배포된 공통 v13 객체와 계보를 기술한다.
- `schema/extensions/fofwisdom-origin-v1.json`: 오리진 전용 객체와 호환 common version을 선언한다.
- `schema/migrations/ledger.jsonl`: migration ID, source/target namespace와 코드·계약 checksum을 append-only로 기록한다.

공통 manifest digest와 ledger는 먼저 backup/release manifest에 기록한다. DB 내부의 공통 필수 ledger는 v13에 소급 추가하지 않고 다음 common version의 업스트림 PR에서만 제안한다. 게시된 migration ID와 checksum은 수정하지 않는다.
