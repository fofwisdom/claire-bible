# Common Schema Version Contract

이 문서는 Git 커밋 계보가 분리된 테제 계보(`blackan/claire_bible`)와 증강 계보(`fofwisdom/claire-bible`)가 SQLite 정본을 안전하게 공유하기 위한 최소 version 계약을 정의한다. Git 조상 관계나 저장소 이름은 DB에 남지 않으므로 `meta.schema_version`과 `meta.schema_lineage`를 함께 호환성 판단에 사용한다.

계약 상태는 증강 계보(`fofwisdom`)에서 `adopted`, 테제 계보(`blackan`)에서 `candidate`다. 테제 계보 병합 후에만 `accepted`로 갱신한다.

## v13 수렴 기준

공통 version v13은 새로운 사용자 기능을 추가하는 version이 아니다. 테제 계보 v9의 테이블, 컬럼과 인덱스를 공통 최소 schema로 다시 선언하고 `schema_lineage=claire-bible/common`을 추가하는 수렴 지점이다. 구현별 부가 컬럼과 테이블은 공통 최소 schema와 이름 또는 의미가 충돌하지 않는 한 보존한다.

| version | 상태 | 마이그레이션 처리 |
|---|---|---|
| 1–9 | 테제 계보 공통 이력 | 멱등 마이그레이션 후 v14로 전환 |
| 10–11 | 증강 계보의 분리 계보 이력 | 부가 schema를 보존하며 v14로 전환 가능 |
| 12 | 증강 계보의 철회된 Support Bundle schema | 자동 전환 금지; 정확한 복구 절차로 먼저 v11 복원 후 전환 |
| 13 | 직전 공통 version | `schema_lineage=claire-bible/common` 기준 단일 DB 및 `theme_id` 파티셔닝으로 승격 |
| 14 | 현재 공통 version | 단일 SQLite DB 통합, 테마 파티셔닝(`theme_id`), 다중 사용자/신디케이션 지원, 볼트 서브시스템 영구 제거 |
| 15 이상 | 미래 version | 현재 코드가 변경하지 않고 거부 |

v12는 정본 DB에 진단 테이블을 추가해 관측성 저장소 분리 원칙을 위반했던 version이므로 영구 폐기한다. v12 DB는 증강 계보의 보존 복구 절차로 진단 행을 내구성 있게 내보낸 뒤 v11로 복원해야 한다. 공통 v14 마이그레이션은 이 복구를 추측하거나 대신하지 않는다. 복구되지 않은 v12를 발견하면 DB를 변경하지 않고 실패한다.

## v14 수렴 기준 (단일 데이터베이스 및 테마 파티셔닝)

공통 version v14는 물리적으로 분리되어 있던 멀티 데이터베이스(`themes/*/claire.db`)를 단일 정본 SQLite 데이터베이스(`data/claire.db`)로 통합하고, 논리적 테마 격리를 `theme_id` 컬럼과 복합 인덱스로 완비한 버전이다. 또한 외부 연동 및 테마 간 교차 적재를 위한 신디케이션(Syndication) 인프라를 구축하고, 옵시디언 볼트(Obsidian Vault) 마크다운 투영 서브시스템을 영구 제거하였다.

1. **단일 DB 통합 및 `theme_id` 파티셔닝**:
   - `documents`, `entities`, `relations`, `embeddings`, `raw_inbox`, `extractions`, `proposals`, `jobs`, `refresh_queue`, `expand_queue`, `document_snapshots`, `document_presentations`, `doc_shares`, `purged_tombstones`에 `theme_id INTEGER NOT NULL DEFAULT 0` 컬럼 추가.
   - `(theme_id, ...)` 복합 인덱스를 전면 배치하여 테마 간 데이터 누수를 물리적으로 방지하고 쿼리 성능 보장.
   - 서버 시작 시 `ThemeManager.reload()`가 기존의 `themes/*/claire.db` 파일을 자동 감지하여 `data/claire.db`로 병합 마이그레이션하고, 완료된 파일은 `claire.db.migrated`로 보존.
2. **다중 사용자 및 테마 거버넌스 인프라**:
   - `users`: 사용자 계정, 역할, 쿼터.
   - `themes`: 테마 메타데이터, 도메인 FQDN, 공개/협업자 권한 설정.
   - `theme_members`: 테마별 사용자 멤버십 및 권한.
3. **신디케이션(Syndication) 인프라**:
   - `syndication_accounts`, `syndication_targets`, `syndication_items`, `syndication_dispatches`.
   - 테마 간 교차 적재 및 외부 프로토콜(ActivityPub, AT Protocol 등) 송수신 큐 지원.
4. **옵시디언 볼트(Obsidian Vault) 서브시스템 영구 제거**:
   - 로컬 파일시스템 볼트 마크다운 동기화 로직 전면 삭제.
   - SQLite DB 본문(`raw_text`, `detail_html`)만을 단일 정본으로 유지.

## v14 불변 규칙

- `schema_version=14`와 `schema_lineage=claire-bible/common`의 의미를 재작성하지 않는다.
- 공통 최소 객체를 변경해야 하면 다음 미사용 common version을 제안한다.
- 구현 전용 객체를 v13의 필수 객체로 소급 편입하지 않는다.
- version은 같지만 lineage가 없거나 다른 DB를 호환 대상으로 간주하지 않는다.
- 미래 version과 알 수 없는 폐기 version을 현재 코드가 추측해 변경하지 않는다.
- 별도 SQLite 저장소와 archive·registry 포맷은 각자의 namespace와 version을 사용한다. 보조 저장소의 version 변경은 공통 지식 DB version 증가를 뜻하지 않는다.

## 운영 경계

스키마 전환은 서비스 기동 전에 명시적인 migration 경로에서 수행한다. health, liveness와 진단 조회는 읽기 전용 연결을 사용하며 DB, registry 또는 schema 객체를 생성·수정하지 않는다. 이전 version은 migration 전까지 비정상 또는 비호환 상태로 보고한다.

마이그레이션은 다음 계약을 따른다.

1. source version, lineage와 필수 객체 서명을 쓰기 전에 확인한다.
2. 삭제 또는 의미 변경이 필요한 데이터는 검증 가능한 형식으로 먼저 보존한다.
3. 마이그레이션 중간 실패가 부분 schema를 남기지 않도록 명시적 transaction을 사용한다.
4. version과 lineage는 목표 schema 적용과 검증이 끝난 뒤 기록한다.
5. 적용 후 version, lineage, 객체 서명과 데이터 무결성을 다시 검사한다.
6. 알 수 없는 source는 원본을 변경하지 않고 fail-closed한다.

## 이후 공동 변경 규칙

1. 공통 번호는 테제 계보 PR에서 먼저 예약하고 문서, 멱등 마이그레이션, 이전 version 데이터 보존 테스트를 함께 제출한다.
2. 구현 전용 실험은 공통 번호를 선점하지 않는다. 공통화가 필요하면 다음 미사용 번호로 별도 PR을 작성한다.
3. 같은 version의 의미를 바꾸지 않는다. 이미 배포된 잘못된 version은 폐기하고 새 공통 번호를 사용한다.
4. 새 기능 schema는 해당 기능의 정책 검토와 분리한다. 공통 계약 PR에 구현별 UI, 운영 도구 또는 무관한 기능 snapshot을 포함하지 않는다.
5. 독립 Git 계보 간 동등성은 commit SHA가 아니라 같은 계약 manifest와 contract test 결과로 증명한다.
6. machine-readable common manifest는 이미 배포된 v13을 기술할 수 있지만, 그 digest나 migration ledger를 기존 v13 DB의 필수 meta로 소급 강제하지 않는다.

구현별 schema 확장, retired version 복구와 보조 저장소 version 계획은 각 구현 저장소의 전용 문서에서 관리하며 이 공동 계약의 필수 객체로 간주하지 않는다.
