# Common Schema Version Contract

이 문서는 SQLite 정본의 version 계약을 정의한다. 처음에는 Git 커밋 계보가 분리된 테제 계보(`blackan/claire_bible`)와 증강 계보(`fofwisdom/claire-bible`)가 정본을 안전하게 공유하기 위해 만들었다. `meta.schema_version`과 `meta.schema_lineage`를 함께 호환성 판단에 사용한다.

- **[결정] 테제 계보는 더 이상 없다** (소유자, 2026-11-02). 공통 번호를 다른 계보와 예약할 필요가 없다. `schema_lineage` 값 `claire-bible/common`은 기존 DB와의 호환을 위해 그대로 쓴다.
- **[결정] 스키마 확장은 조율하고, 쓰이지 않는 영역은 순차 소각한다** (소유자, 2026-11-02). 아래 "이후 스키마 변경 규칙"을 따른다.

## v13 수렴 기준

공통 version v13은 새로운 사용자 기능을 추가하는 version이 아니다. 테제 계보 v9의 테이블, 컬럼과 인덱스를 공통 최소 schema로 다시 선언하고 `schema_lineage=claire-bible/common`을 추가하는 수렴 지점이다. 구현별 부가 컬럼과 테이블은 공통 최소 schema와 이름 또는 의미가 충돌하지 않는 한 보존한다.

| version | 상태 | 마이그레이션 처리 |
|---|---|---|
| 1–9 | 테제 계보 공통 이력 | 멱등 마이그레이션 후 v15로 전환 |
| 10–11 | 증강 계보의 분리 계보 이력 | 부가 schema를 보존하며 v15로 전환 가능 |
| 12 | 증강 계보의 철회된 Support Bundle schema | 자동 전환 금지; 정확한 복구 절차로 먼저 v11 복원 후 전환 |
| 13 | 직전 공통 version | `schema_lineage=claire-bible/common` 기준 단일 DB 및 `theme_id` 파티셔닝으로 승격 |
| 14 | 직전 version | 단일 SQLite DB 통합, 테마 파티셔닝(`theme_id`), 다중 사용자/신디케이션 지원, 볼트 서브시스템 영구 제거. 멱등 마이그레이션 후 v15로 전환 |
| 15 | 직전 version | 계정(`accounts`), 퇴역 USERNAME, 인스턴스 설정, 테마·자격증명의 계정 귀속 |
| 16 | 직전 version (아래 "v16" 절) | 테마 번호(`number`), `visibility_epoch`, 임베딩 열, 레거시 호스트(`legacy_hosts`) |
| 17 | 현재 version (아래 "v17" 절) | 연합 팔로워(`federation_followers`), 툼스톤(`federation_tombstones`), 도메인 차단(`federation_domain_blocks`) |
| 18 이상 | 미래 version | 현재 코드가 변경하지 않고 거부 |

v12는 정본 DB에 진단 테이블을 추가해 관측성 저장소 분리 원칙을 위반했던 version이므로 영구 폐기한다. v12 DB는 증강 계보의 보존 복구 절차로 진단 행을 내구성 있게 내보낸 뒤 v11로 복원해야 한다. 공통 마이그레이션은 이 복구를 추측하거나 대신하지 않는다. 복구되지 않은 v12를 발견하면 DB를 변경하지 않고 실패한다.

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

## v15 (계정, P1)

확인 기준: P1 계정 코어 커밋(`d5e8fb4` 다음) (2026-11-02). 근거 표기는 [PRINCIPLES.md](../PRINCIPLES.md) F1을 따른다. 설계: [MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md](../design/MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md) §13.1. 계정 동작은 [ACCOUNTS.md](ACCOUNTS.md)에 있다.

- **[코드] 버전 상수**: `src/claire/store/db.py`의 `SCHEMA_VERSION = 15`, lineage는 `claire-bible/common` 그대로다. `_validate_migration_source`는 저장된 version이 15보다 크면 변경 없이 거부한다.
- **[결정] 번호 예약 불필요**: 테제 계보가 없어졌으므로 v15는 다른 계보와 예약하지 않는다.
- **[코드] 추가 객체**(`_migrate_v15_accounts`, 모두 `IF NOT EXISTS`·`_ensure_column`으로 멱등)
  - 테이블 `accounts`, `username_tombstones`, `instance_settings`
  - 인덱스 `idx_accounts_single_admin`(관리자 한 명), `idx_themes_account_default`(계정마다 기본 테마 하나)
  - 트리거 `accounts_username_immutable`(설정된 USERNAME 변경 거부)
  - 열 `themes.account_id`, `auth_sessions.account_id`, `auth_sessions.authenticated_at`, `oauth_codes.account_id`, `oauth_tokens.account_id`, `oauth_auth_requests.account_id`
- **[코드] 데이터 이관**(멱등): `instance_settings.id_salt`가 없을 때만 만든다. 관리자 행(`id=1`, `role=admin`, `status=setup_required`, `username=NULL`)이 없을 때만 만들고, `public_id`는 고정 상수 `ADMIN_LEGACY_PUBLIC_ID_UNIX_MS`로 계산해 다시 실행해도 같다. `account_id`가 NULL인 테마와 자격증명 행을 1로 채운다. `id=1` 행이 관리자가 아니면 실패한다. v14의 `users` 테이블은 그대로 두고 쓰지 않는다.
- **[코드] 기본 테마 중복이면 실패**: v15 블록의 첫 단계(`_check_single_default_theme_per_account`)가 한 계정(NULL은 1로 봄)에 `is_default=1` 테마가 둘 이상인지 본다. 있으면 어느 쪽이 기본인지 추측하지 않고 `RuntimeError`(계정과 `themes.id` 목록 포함)를 낸다. 이 검사는 v15 객체를 만들기 전에 실행되고 `init_db`는 `schema_version`을 쓰기 전에 멈추므로, v14 DB에서는 v15 테이블·열·행을 하나도 쓰지 않고 `schema_version=14`가 남는다(`tests/test_schema_v15.py`의 `test_multiple_default_themes_fail_migration_without_changes`). `is_default=1`을 계정마다 하나로 고친 뒤 다시 실행한다.
- **[코드] 보조 파일**: `data/themes.json` 항목과 `data/auth/api_tokens.json` 항목에 `account_id`가 없으면 관리자(1)로 읽는다. 값이 정수가 아니면 테마는 `ValueError`, API 토큰은 그 항목을 무시한다.

## v16 (테마 번호와 레거시 호스트, P2)

확인 기준: P2 멀티 서브에이전트 통합 (2026-11-03). 설계: [MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md](../design/MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md) §7.2, §13.2.

- **[코드] 버전 상수**: v16 도입 당시 `src/claire/store/db.py`의 `SCHEMA_VERSION`은 16이었다(현재 값은 아래 "v17" 절). lineage는 `claire-bible/common` 그대로다.
- **[코드] 추가 객체**(`_migrate_v16_theme_numbers`, 모두 `IF NOT EXISTS`·`_ensure_column`으로 멱등):
  - 테이블 `legacy_hosts`: `(host TEXT PRIMARY KEY, theme_id INTEGER NOT NULL REFERENCES themes(id) ON DELETE CASCADE, activated_at REAL, sunset_at REAL, indefinitely INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL)`
  - 인덱스 `idx_legacy_hosts_theme_id`
  - 열 `themes.number`(INTEGER UNIQUE), `themes.visibility_epoch`(INTEGER NOT NULL DEFAULT 1), `themes.embed_provider`(TEXT NOT NULL DEFAULT ''), `themes.embed_model`(TEXT NOT NULL DEFAULT '')
- **[코드] 테마 번호 백필**: `themes.number`가 비어 있는 행을 `timestamp_id("themes", salt, unix_ms)`로 채운다. `created_at=0`이거나 비어 있는 레거시 행은 `ADMIN_LEGACY_PUBLIC_ID_UNIX_MS`로 하한을 고정하여 모든 이관 번호가 `>= 2^56`이 되도록 보장한다(A3).
- **[코드] 레거시 호스트 이관**: 기존 `themes.fqdn` 값이 있는 행을 `legacy_hosts` 테이블로 복사하고, `sunset_at = NULL` 상태로 보존하여 관리자가 이관을 활성화할 때까지 일몰 시계를 유예한다(R23-2).
- **[코드] 인스턴스 정책**: `instance_settings`에 `legacy_host_policy`(`default_sunset_days`, `allow_indefinite`)가 기록된다.

## v17 (연합 팔로워, 툼스톤, 도메인 차단, Fedify 사이드카 FP2-1)

확인 기준: FP2-1 구현 (2026-11-11). 설계: [ACCOUNT_IDP_AND_FEDIFY_SIDECAR_INTEGRATION_DESIGN.md](../design/ACCOUNT_IDP_AND_FEDIFY_SIDECAR_INTEGRATION_DESIGN.md) §10.1.

- **[코드] 버전 상수**: `src/claire/store/db.py`의 `SCHEMA_VERSION = 17`, lineage는 `claire-bible/common` 그대로다. `_validate_migration_source`는 저장된 version이 17보다 크면 변경 없이 거부한다.
- **[코드] 추가 객체**(`_migrate_v17_federation`, 모두 `IF NOT EXISTS`로 멱등. 테이블만 더하고 기존 행은 바꾸지 않는다, A3):
  - 테이블 `federation_followers`: `(public_id INTEGER UNIQUE NOT NULL, account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE, actor_iri TEXT NOT NULL, inbox_iri TEXT NOT NULL, shared_inbox_iri TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL, PRIMARY KEY (account_id, actor_iri))`. `public_id`는 시간 정렬 번호(`claire.ids`)이고 컬렉션 커서다.
  - 인덱스 `idx_federation_followers_actor`(`actor_iri`): 원격 액터의 `Delete`·`Update`가 계정과 무관하게 행을 찾는다. 설계 §10.1에는 없는 인덱스다.
  - 테이블 `federation_tombstones`: `(public_id INTEGER PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('actor', 'object')), gone_at REAL NOT NULL)`. USERNAME이 없고(K4) `accounts` 외래 키도 없다. 퍼지 뒤에도 행이 남아야 하기 때문이다.
  - 테이블 `federation_domain_blocks`: `(domain TEXT PRIMARY KEY, created_at REAL NOT NULL, created_by INTEGER NOT NULL REFERENCES accounts(id), note TEXT NOT NULL DEFAULT '')`. `domain`은 소문자 ASCII(punycode)다.
- **[코드] 저장소 함수**: `src/claire/store/federation.py`. 팔로워 저장(`upsert_follower`, 키는 `(account_id, actor_iri)`), 삭제, 원격 액터 단위 삭제·인박스 갱신, 개수, 커서 페이지(`max_id`·`min_id`·`since_id`), 툼스톤 기록·조회, 도메인 정규화·차단·해제·목록·판정(`blocking_domain`, 차단은 하위 도메인에도 적용), 차단 목록 ETag. 이 모듈의 함수는 커밋하지 않는다.
- **[코드] 설계와 다른 점**: 설계 §10.1은 `federation_objects`와 `federation_outbox`도 version 17에 적었다. 이 두 테이블은 FP3-1에서 그 코드와 함께 더한다(쓰이지 않는 스키마를 먼저 만들지 않는다). 따라서 FP3-1은 version을 한 번 더 올린다.
- **[테스트]** `tests/test_schema_v17.py`.

### 별도 저장소: `data/auth/auth.db`

- **[코드]** 계정 비밀과 제어 평면은 지식 DB와 분리된 `data/auth/auth.db`에 있다(`src/claire/store/auth_db.py`). 자체 `meta.schema_version`을 쓰며 현재 값은 `AUTH_DB_SCHEMA_VERSION = 5`다. 이 번호는 위 공통 version과 무관하다("v14 불변 규칙"의 보조 저장소 규칙).
  - **v1 (P1/P3)**: 계정 자격증명(`auth_secrets`), 초대(`invitations`, `invitation_redemptions`), 사용량(`account_usage`), 로그인 스로틀(`login_throttle`), 단기 OIDC 상태(`oidc_states`).
  - **v2 (P4)**: `account_usage`에 UTC 일 적재 집행용 `daily_day`·`daily_ingests` 열 추가(설계 §10.3, §13.3).
  - **v3 (P5)**: `oidc_states`에 계정 연결용 `account_id` 열 추가, 외부 IdP 계정 연결 테이블 `account_identities` 연동(설계 §12, §13.3).
  - **v4 (유저 단위 기능)**: 계정별 텔레그램 봇 `account_telegram_bots`와 계정 백업 주기 `account_backup_policies` 테이블 추가.
  - **v5 (Fedify 사이드카 FP1-2)**: 연합 액터 키 테이블 `actor_keys` 추가. 기본 키는 `(account_id, alg)`이고 `account_id` 0은 인스턴스 액터다. `alg`는 `rsa2048`·`ed25519`만 받는다. 개인 키는 `SecretService`가 AAD `actor_key|{account_id}|{alg}`로 암호화한다(`src/claire/store/actor_keys.py`, [FEDERATION.md](FEDERATION.md) §6, `tests/test_actor_keys.py`). 설계 문서는 이 변경을 "auth.db version 4"라고 적었지만, 설계 작성 뒤에 v4가 먼저 쓰여서 코드에서는 v5다.
- **[코드]** 저장된 값이 5보다 크면 다운그레이드로 보고 `RuntimeError`, 1보다 작거나 정수가 아니면 `RuntimeError`다. lineage 표식은 없다.
- **[코드]** `claire migrate`가 지식 DB보다 먼저 auth.db를 만들거나 맞추고, 서비스 기동도 같은 일을 한다.
- **[코드]** 연합 스위치 `instance_settings.federation`(Fedify 사이드카 FP1-3)은 기존 `instance_settings` 테이블의 키 하나라서 claire.db 버전을 올리지 않았다. 그 시점의 claire.db는 v16 그대로였다([FEDERATION.md](FEDERATION.md) §7.1).

## 운영 경계

스키마 전환은 서비스 기동 전에 명시적인 migration 경로에서 수행한다. health, liveness와 진단 조회는 읽기 전용 연결을 사용하며 DB, registry 또는 schema 객체를 생성·수정하지 않는다. 이전 version은 migration 전까지 비정상 또는 비호환 상태로 보고한다.

마이그레이션은 다음 계약을 따른다.

1. source version, lineage와 필수 객체 서명을 쓰기 전에 확인한다.
2. 삭제 또는 의미 변경이 필요한 데이터는 검증 가능한 형식으로 먼저 보존한다.
3. 마이그레이션 중간 실패가 부분 schema를 남기지 않도록 명시적 transaction을 사용한다.
4. version과 lineage는 목표 schema 적용과 검증이 끝난 뒤 기록한다.
5. 적용 후 version, lineage, 객체 서명과 데이터 무결성을 다시 검사한다.
6. 알 수 없는 source는 원본을 변경하지 않고 fail-closed한다.

## 이후 스키마 변경 규칙

1. **[결정]** 새 테이블·열은 기능 설계(모델 정의)에서 필요성과 소유 기능을 먼저 정하고, 문서·멱등 마이그레이션·이전 version 데이터 보존 테스트를 함께 제출한다. 무분별한 확장을 막기 위해서다.
2. **[결정]** 더 이상 쓰이지 않는 스키마 영역은 순차 소각한다. 소각도 새 version으로 하며, 삭제할 데이터가 있으면 운영 경계 2번대로 먼저 보존한다. 예: v14 `users` 테이블은 v15부터 쓰지 않는다(소각 후보).
3. 같은 version의 의미를 바꾸지 않는다. 이미 배포된 잘못된 version은 폐기하고 새 공통 번호를 사용한다.
4. 새 기능 schema는 해당 기능의 정책 검토와 분리한다. 공통 계약 PR에 구현별 UI, 운영 도구 또는 무관한 기능 snapshot을 포함하지 않는다.
5. machine-readable common manifest는 이미 배포된 v13을 기술할 수 있지만, 그 digest나 migration ledger를 기존 v13 DB의 필수 meta로 소급 강제하지 않는다.

구현별 schema 확장, retired version 복구와 보조 저장소 version 계획은 각 구현 저장소의 전용 문서에서 관리하며 이 공동 계약의 필수 객체로 간주하지 않는다.
