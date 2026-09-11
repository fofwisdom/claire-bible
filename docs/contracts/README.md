# Shared Compatibility Contracts (`docs/contracts/`)

이 디렉터리는 Claire Bible 업스트림과 공개 오리진이 함께 채택하고 갱신하는 호환성 계약을 보관한다. 여기의 규약은 서로 다른 Git 계보의 구현이 같은 데이터와 직렬화 형식을 안전하게 해석하기 위한 공동 경계다. 각 저장소의 역사적 원본과 구현 전용 문서는 해당 저장소의 기존 문서 구조에 남긴다. 공개 오리진에서는 업스트림 원본을 `docs/upstream/`, 오리진 전용 설계·운영 문서를 `docs/origin/`에 둔다.

## 포함 기준

다음 조건을 모두 만족하는 문서만 이 디렉터리에 둔다.

- 양 저장소의 구현이 상호 호환을 위해 같은 의미를 사용해야 한다.
- version, lineage, 포맷 또는 마이그레이션 실패 규칙을 명시한다.
- 구현별 기능 목록이 아니라 최소 공통 계약을 정의한다.
- 변경 시 호환성 테스트와 데이터 보존 경계를 함께 검토할 수 있다.

구현 전용 복구 절차, 배포 명령, UI·보안·telemetry 구현 계획과 역사적 기획 원본은 각 저장소의 전용 문서 구조에 남긴다.

## 계약 목록

| 문서 | 범위 | 현재 상태 |
|---|---|---|
| [`SCHEMA_VERSIONING.md`](SCHEMA_VERSIONING.md) | SQLite 공통 schema version과 lineage, v13 수렴 및 이후 변경 규칙 | 오리진 채택, 업스트림 제안 |

## 변경 절차

공동 계약은 다음 상태를 사용한다.

1. `candidate`: 한 저장소가 계약과 호환성 근거를 제안한 상태
2. `adopted-by-origin`: 공개 오리진이 계약을 구현·채택한 상태
3. `accepted-upstream`: 업스트림이 계약을 병합한 상태
4. `retired`: 이미 발행된 의미를 재작성하지 않고 사용 중단한 상태

공통 version은 업스트림 PR에서 먼저 예약하는 것을 기본으로 한다. 긴급한 오리진 선채택은 `candidate` 상태와 기준 업스트림 commit을 기록하고, 업스트림 병합 전까지 공동 채택으로 표현하지 않는다. 이미 배포된 version의 의미는 양쪽 문서를 동시에 고쳐서 바꾸지 않으며, 차이는 다음 미사용 version의 마이그레이션으로 해결한다.

양 저장소는 Git commit 동일성으로 호환성을 판정하지 않는다. 최신 업스트림을 기준으로 작은 계약 patch, machine-readable manifest, 데이터 보존 migration과 contract test를 제출하고, 각 저장소의 구현 commit과 검증 결과를 계약 version에 연결한다.

## 문서와 기계 계약의 경계

이 디렉터리는 사람이 검토하는 정본 규약을 보관한다. 향후 `schema/common/v13.json`, 구현별 extension manifest와 append-only migration ledger를 도입할 때는 프로젝트 루트의 `schema/`에 두고 코드와 CI가 직접 검증한다. 기존 v13 DB에 manifest digest나 ledger를 필수 meta로 소급 추가하지 않는다.
