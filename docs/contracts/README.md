# Shared Compatibility Contracts (`docs/contracts/`)

이 디렉터리는 테제 계보(`blackan/claire_bible`)와 증강 계보(`fofwisdom/claire-bible`)가 함께 채택하고 갱신하는 호환성 계약을 보관한다.
여기의 규약은 서로 다른 Git 계보의 두 구현이 같은 데이터와 직렬화 형식을 안전하게 해석하기 위한 공동 경계다.
각 저장소의 역사적 기획과 전용 문서는 해당 저장소의 기존 문서 구조에 남긴다.
테제 계보는 `docs/`에 아키텍처 원본을 두고, 증강 계보는 `docs/upstream/`(테제 계보 동기화본)과 `docs/origin/`(증강 설계·운영)으로 분리 관리한다.

## 포함 기준

다음 조건을 모두 만족하는 문서만 이 디렉터리에 둔다.

- 양 계보 구현이 상호 호환을 위해 같은 의미를 사용해야 한다.
- version, lineage, 포맷 또는 마이그레이션 실패 규칙을 명시한다.
- 구현별 기능 목록이 아니라 최소 공통 계약을 정의한다.
- 변경 시 호환성 테스트와 데이터 보존 경계를 함께 검토할 수 있다.

구현 전용 복구 절차, 배포 명령, UI·보안·telemetry 구현 계획과 역사적 기획 원본은 각 저장소의 전용 문서 구조에 남긴다.

## 계약 목록

| 문서 | 범위 | 현재 상태 |
|---|---|---|
| [`SCHEMA_VERSIONING.md`](SCHEMA_VERSIONING.md) | SQLite 공통 schema version과 lineage, v13 수렴 및 이후 변경 규칙 | 증강 계보 채택, 테제 계보 제안 |
| [`API_HTTP_CONTRACT.md`](API_HTTP_CONTRACT.md) | HTTP REST/ASGI 와이어 프로토콜, 인증 채널 및 보안 경계 최소 공통 규약 | 증강 계보 채택, 테제 계보 제안 |
| [`TOKEN_EXCHANGE.md`](TOKEN_EXCHANGE.md) | RFC 8693 OAuth 2.0 Token Exchange 프로토콜, URN 레지스트리 및 토큰 로테이션 계약 | 증강 계보 채택, 테제 계보 제안 |
| [`openapi.yaml`](openapi.yaml) | 기계 판독용 OpenAPI 3.1.0 공통 API 사양서 정본 | 증강 계보 채택, 테제 계보 제안 |

## 변경 절차

공동 계약은 다음 상태를 사용한다.

1. `candidate`: 한 계보가 계약과 호환성 근거를 제안한 상태
2. `adopted (증강 계보)`: 증강 계보(`fofwisdom`)가 계약을 구현·선채택한 상태
3. `accepted (테제 계보)`: 테제 계보(`blackan`)가 계약을 공통 사양으로 병합한 상태
4. `retired`: 이미 발행된 의미를 재작성하지 않고 사용 중단한 상태

공통 version은 테제 계보 PR에서 먼저 예약하는 것을 기본으로 한다.
긴급한 증강 계보 선채택은 `candidate` 상태와 기준 테제 계보 commit을 기록하고, 테제 계보 병합 전까지 공통 확정으로 표현하지 않는다.
이미 배포된 version의 의미는 양쪽 문서를 동시에 고쳐서 바꾸지 않으며, 차이는 다음 미사용 version의 마이그레이션으로 해결한다.

양 저장소는 Git commit 동일성으로 호환성을 판정하지 않는다.
최신 테제 계보를 기준으로 작은 계약 patch, machine-readable manifest, 데이터 보존 migration과 contract test를 제출하고, 각 저장소의 구현 commit과 검증 결과를 계약 version에 연결한다.

## 문서와 기계 계약의 경계

이 디렉터리는 사람이 검토하는 정본 규약을 보관한다.
향후 `schema/common/v13.json`, 구현별 extension manifest와 append-only migration ledger를 도입할 때는 프로젝트 루트의 `schema/`에 두고 코드와 CI가 직접 검증한다.
기존 v13 DB에 manifest digest나 ledger를 필수 meta로 소급 추가하지 않는다.
