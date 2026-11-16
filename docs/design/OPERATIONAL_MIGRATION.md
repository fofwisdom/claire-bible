# 전체 DB inventory 기반 update와 backup 설계 (`OPERATIONAL_MIGRATION.md`)

- **Status**: Planned, not implemented. The implemented update rules (one command, user values kept, schema migration before the new service starts) are principles E1 to E3 in [`PRINCIPLES.md`](../PRINCIPLES.md). Current behavior: environment backfill and the update sequence in [`OPERATIONS.md`](../implementation/OPERATIONS.md), the retired v12 recovery in [`SCHEMA_VERSIONING.md`](../implementation/SCHEMA_VERSIONING.md).
- **Date**: 2026-08-18 · condensed 2026-11-16
- **Note (2026-11-16)**: this plan was written before schema v14. Since v14 all themes are in one database file, and the Vault subsystem is removed. The roles `active_theme` and `dormant` and the Vault checks below refer to the earlier layout. The database set now is the knowledge database, `client.db`, `data/auth/auth.db`, `telemetry.db`, and `data/fedify/fedify.db`.

현재 `cb-manuscript update`는 자동 pre-update backup을 만들지 않으며, backup format v1은 기본 지식 DB 하나의 SQLite 메타데이터만 별도 검증한다. 아래 내용은 현재 동작이 아니라 backup format v2와 update transaction의 구현 계획이다.

### 5.1 공통 inventory 계약

update, migrate, health/liveness, backup, restore와 Support Bundle은 하나의 read-only `DatabaseInventory` 결과를 공유한다. inventory는 registry와 제한된 physical scan을 양방향 대조하고 각 SQLite 파일을 다음 역할로 분류한다.

- `canonical`: 기본 정본 지식 DB
- `active_theme`: registry에 등록된 활성 테마 DB
- `dormant`: registry에서 해제됐지만 보존 중인 테마 DB
- `telemetry`: 독립 telemetry DB

등록 DB 누락, 물리 orphan, 동일 파일의 중복 등록, 허용 data root 밖 path escape, symlink와 realpath alias를 검출한다. Vault는 DB 정본의 투영이므로 drift를 warning/reconcile 대상으로 보고하되 inventory 중 자동 삭제하지 않는다.

각 DB에 대해 role, logical/real path, registry relation, 파일 identity, `quick_check`, `foreign_key_check`, schema version과 lineage를 읽기 전용 연결로 수집한다. 지식 DB와 telemetry DB는 각자의 namespace에서 unknown lineage와 future version을 독립적으로 판정한다. 지식 DB의 unknown/future는 해당 update를 fail-closed로 중단한다. telemetry의 unknown/future는 telemetry read/write와 Support Bundle DB registry를 fail-closed로 비활성화하고 원본 파일을 변경하지 않지만, 정본 지식 update는 degraded 경고와 함께 계속한다. 진단 중 registry·DB·Vault를 생성하거나 migration하지 않는다.

### 5.2 backup format v2와 update gate

writer를 중지하기 전에 read-only inventory와 migration plan을 계산한다. 그 뒤 writer를 중지하고 inventory를 다시 대조해 변화가 없을 때만 모든 DB의 SQLite snapshot과 필요한 data/Vault 파일을 pre-update backup으로 만든다. v2 manifest의 `databases[]` 각 항목은 다음을 기록한다.

- DB별 snapshot path, role, 원본 path와 registry relation
- schema version/lineage, `quick_check`, `foreign_key_check`
- 파일 크기·SHA-256과 snapshot 생성 결과

모든 snapshot과 manifest 재검증이 끝나기 전에는 migration을 시작하지 않는다. 현재의 수동 backup format v1과 자동 pre-update backup은 동일 동작이 아니며, v2가 구현되기 전 `cb-manuscript update`가 복구점을 자동 보장한다고 간주해서는 안 된다.

### 5.3 DB-set rollback 정책

`init_db` DDL과 migration은 DB 단위 transaction 경계를 보장하도록 바꾸고, update는 전체 inventory를 하나의 release set으로 취급한다. 어느 DB migration이라도 실패하면 이미 변경된 DB까지 pre-update snapshot으로 복원한 후에만 이전 writer를 재개한다. 부분 migration 상태에서 old writer를 자동 재개하지 않는다. 복원 검증도 실패하면 writer를 중지한 채 transaction journal과 진단 자료를 남긴다.

새 image activation 또는 post-start liveness가 실패한 경우에는 새 실패 상태와 journal을 보존하고 자동 source rollback은 하지 않는 현재 운영 정책을 유지한다. 운영자는 명시적 rollback 절차로 이전 image와 전체 DB-set backup을 선택한다. migration failure의 DB-set 복원과 activation failure의 명시적 rollback을 서로 다른 정책으로 유지한다.
