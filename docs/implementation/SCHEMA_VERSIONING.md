# SQLite 스키마 버전

이 문서는 SQLite 정본 DB의 schema version과 마이그레이션을 적는다. 코드는 `meta.schema_version`과 `meta.schema_lineage`(고정 값 `claire-bible/common`)를 함께 호환성 판단에 쓴다.

- **[결정] 스키마 확장은 조율하고, 쓰이지 않는 영역은 순차 소각한다** (소유자, 2026-11-02). 아래 "이후 스키마 변경 규칙"을 따른다.

## Version 표

v13은 사용자 기능을 더하지 않은 version이다. v9의 테이블, 컬럼과 인덱스를 최소 schema로 다시 선언하고 `schema_lineage=claire-bible/common`을 추가했다. 그 밖의 컬럼과 테이블은 최소 schema와 이름 또는 의미가 충돌하지 않으면 보존한다.

| version | 상태 | 마이그레이션 처리 |
|---|---|---|
| 1–9 | 초기 이력 | 멱등 마이그레이션 후 v15로 전환 |
| 10–11 | 이전 이력 | 부가 schema를 보존하며 v15로 전환 가능 |
| 12 | 철회된 Support Bundle schema | 자동 전환 금지; 정확한 복구 절차로 먼저 v11 복원 후 전환 |
| 13 | 직전 version | `schema_lineage=claire-bible/common` 기준 단일 DB 및 `kb_id` 파티셔닝으로 승격 |
| 14 | 직전 version | 단일 SQLite DB 통합, 테마 파티셔닝(`kb_id`), 다중 사용자/신디케이션 지원, 볼트 서브시스템 영구 제거. 멱등 마이그레이션 후 v15로 전환 |
| 15 | 직전 version | 계정(`accounts`), 퇴역 USERNAME, 서버 설정, 테마·자격증명의 계정 귀속 |
| 16 | 직전 version (아래 "v16" 절) | 테마 번호(`number`), `visibility_epoch`, 임베딩 열, 레거시 호스트(`legacy_hosts`) |
| 17 | 직전 version (아래 "v17" 절) | 연합 팔로워(`federation_followers`), 툼스톤(`federation_tombstones`), 도메인 차단(`federation_domain_blocks`) |
| 18 | 직전 version (아래 "v18" 절) | 연합 발행 객체(`federation_objects`), 트랜잭션 아웃박스(`federation_outbox`) |
| 19 | 직전 version (아래 "v19" 절) | 계정 인터페이스 언어(`accounts.locale`, v46에서 제거) |
| 20 | 직전 version (아래 "v20" 절) | 쓰이지 않는 `syndication_*` 테이블 제거, `syndication_dispatches` 재구성 |
| 21 | 직전 version (아래 "v21" 절) | 계정 피드 구독(`feed_subscriptions`)과 피드 항목(`feed_items`) |
| 22 | 직전 version (아래 "v22" 절) | 피드 테이블을 `client.db`로 이동 |
| 23 | 직전 version (아래 "v23" 절) | `federation_objects.content_digest` |
| 24 | 직전 version (아래 "v24" 절) | `federation_objects.quote_policy` |
| 25 | 직전 version (아래 "v25" 절) | `instance_settings`를 `server_settings`로 이름 변경 |
| 26 | 직전 version (아래 "v26" 절) | 계정 엔티티 언어(`accounts.entity_language`) |
| 27 | 직전 version (아래 "v27" 절) | 테마를 지식 베이스로 이름 변경(`knowledge_bases`, `kb_id`, `is_personal`) |
| 28 | 직전 version (아래 "v28" 절) | 공유 지식 베이스, 협업 테이블, 테마 컬렉션(`themes`), 기존 추가 테마의 공유 지식 베이스 변환 |
| 29 | 직전 version (아래 "v29 to v31" 절) | 지식 연합 테이블(`kf_*`) |
| 30 | 직전 version (아래 "v29 to v31" 절) | 원격 지식 베이스(`kf_remote_knowledge_bases`, `theme_remote_knowledge_bases`) |
| 31 | 직전 version (아래 "v29 to v31" 절) | 원격 출처의 기여 열(`kb_contributions`), 지식 베이스 액터 |
| 32 | 직전 version (아래 "v32" 절) | 기여 제안과 권한 부여(`kf_outbound_offers`, `kf_inbound_offers`, `kf_grants`, `kf_received_grants`) |
| 33 | 직전 version (아래 "v33" 절) | 지식 연합 한도(`kf_inbox_journal` 할당 열, `kf_outbox_activities`, `kb_contributions.remote_updated_at`) |
| 34 | 직전 version (아래 "v34" 절) | `kf_outbound_offers.automatic` |
| 35 | 직전 version (아래 "v35" 절) | 링크 확장 원천 이름(`raw_inbox.source`), 동일 주제 출처 병합의 shadow 기록(`same_subject_shadow`) |
| 36 | 직전 version (아래 "v36" 절) | shadow 기록의 사람 판정 열(`same_subject_shadow.verdict*`) |
| 37 | 직전 version (아래 "v37" 절) | 개인 지식베이스 출처 공개(`accounts.personal_source_public`) |
| 38 | 직전 version (아래 "v38" 절) | 엔티티 쌍 검토(`pair_review_items`, `pair_review_verdicts`)와 옛 shadow 기록의 1회 채움 |
| 39 | 직전 version (아래 "v39" 절) | 임베딩 공간 레지스트리(`embedding_spaces`), KB별 활성/목표 공간(`active_space_id`, `target_space_id`), 다중 공간 벡터 PK(`embeddings(owner_id, kb_id, space_id)`) |
| 40 | 직전 version (아래 "v40" 절) | OAuth 2.1 스코프·회전·TX·Device Grant(`oauth_*`) 및 클라이언트 추출 스테이징(`accounts.extraction_mode`(v45에서 제거), `documents` 스테이징 열) |
| 41 | 직전 version (아래 "v41" 절) | USERNAME 대소문자 보존·비구별 인덱스, 7일 보호 이름 변경 |
| 42 | 직전 version | `accounts.summary` (코드 `_migrate_v42_account_summary`) |
| 43 | 직전 version (아래 "v43" 절) | 고유 주소 대장 `permalinks`, `doc_shares` 이관 후 테이블 제거 |
| 44 | 직전 version (아래 "v44" 절) | USERNAME 없는 계정을 `setup_required` 상태로만 허용하는 트리거 |
| 45 | 직전 version (아래 "v45" 절) | 계정 추출 모드 `accounts.extraction_mode` 제거, 요청의 추출 모드 `raw_inbox.extraction_mode` |
| 46 | 현재 version (아래 "v46" 절) | 선호 언어: `accounts.preferred_languages`, 서버 기본 언어 `server_settings.default_languages`, `accounts.locale` 제거 |
| 46 이상 | 미래 version | 현재 코드가 변경하지 않고 거부 |

v12는 정본 DB에 진단 테이블을 추가해 관측성 저장소 분리 원칙(PRINCIPLES A4)을 위반했던 version이므로 영구 폐기한다(`RETIRED_SCHEMA_VERSIONS`).

### Retired v12 recovery

Basis: commit d15ae8e0 (2026-11-16).

- **[code]** `init_db` in `src/claire/store/db.py` recovers a v12 database itself, before the normal migration (`_restore_local_support_v12_to_v11`). There is no separate procedure.
- **[code]** In one `BEGIN IMMEDIATE` transaction it checks the version again and the exact column signature of `ingest_attempts` and `fetch_attempts` (`_LOCAL_SUPPORT_V12_COLUMNS`). If the signature is different, it rolls back and fails. It does not guess.
- **[code]** It writes all rows of the two tables as JSONL plus `manifest.json` (row counts, bytes, SHA-256) to `data/raw/migrations/support-diagnostics-v12-to-v11-<UTC time>-<path hash>-<random>/` (`_export_local_support_v12`). The directory has mode `0700`, the files `0600`. It syncs the files and the directories to disk. If an export step fails, it removes the incomplete directory.
- **[code]** Only after the export, it drops the two tables and their indexes and sets `meta.schema_version` to 11. Then the same `init_db` call migrates the database to the current version and writes `schema_lineage`.
- **[decision]** v12 stays reserved. The recovery target is always v11.

## v14 (단일 데이터베이스 및 테마 파티셔닝)

v14는 물리적으로 분리되어 있던 멀티 데이터베이스(`themes/*/claire.db`)를 단일 정본 SQLite 데이터베이스(`data/claire.db`)로 통합하고, 논리적 테마 격리를 `kb_id` 컬럼과 복합 인덱스로 완비한 버전이다. 또한 외부 연동 및 테마 간 교차 적재를 위한 신디케이션(Syndication) 인프라를 구축하고, 옵시디언 볼트(Obsidian Vault) 마크다운 투영 서브시스템을 영구 제거하였다.

1. **단일 DB 통합 및 `kb_id` 파티셔닝**:
   - `documents`, `entities`, `relations`, `embeddings`, `raw_inbox`, `extractions`, `proposals`, `jobs`, `refresh_queue`, `expand_queue`, `document_snapshots`, `document_presentations`, `doc_shares`, `purged_tombstones`에 `kb_id INTEGER NOT NULL DEFAULT 0` 컬럼 추가.
   - `(kb_id, ...)` 복합 인덱스를 전면 배치하여 테마 간 데이터 누수를 물리적으로 방지하고 쿼리 성능 보장.
   - 서버 시작 시 `KnowledgeBaseManager.reload()`가 기존의 `themes/*/claire.db` 파일을 자동 감지하여 `data/claire.db`로 병합 마이그레이션하고, 완료된 파일은 `claire.db.migrated`로 보존.
2. **다중 사용자 및 테마 거버넌스 인프라**:
   - `users`: 사용자 계정, 역할, 쿼터.
   - `themes`: 테마 메타데이터, 도메인 FQDN, 공개/협업자 권한 설정.
   - `theme_members`: 테마별 사용자 멤버십 및 권한.
3. **신디케이션(Syndication) 인프라**:
   - `syndication_accounts`, `syndication_targets`, `syndication_items`, `syndication_dispatches`. v20에서 `syndication_dispatches`만 남았다(아래 "v20" 절).
   - 테마 간 교차 적재 및 외부 프로토콜(ActivityPub, AT Protocol 등) 송수신 큐 지원.
4. **옵시디언 볼트(Obsidian Vault) 서브시스템 영구 제거**:
   - 로컬 파일시스템 볼트 마크다운 동기화 로직 전면 삭제.
   - SQLite DB 본문(`raw_text`, `detail_html`)만을 단일 정본으로 유지.

## v14 불변 규칙

- `schema_version=14`와 `schema_lineage=claire-bible/common`의 의미를 재작성하지 않는다.
- version은 같지만 lineage가 없거나 다른 DB를 호환 대상으로 간주하지 않는다.
- 미래 version과 알 수 없는 폐기 version을 현재 코드가 추측해 변경하지 않는다.
- 별도 SQLite 저장소와 archive·registry 포맷은 각자의 namespace와 version을 사용한다. 보조 저장소의 version 변경은 지식 DB version 증가를 뜻하지 않는다.

## v15 (계정, P1)

확인 기준: P1 계정 코어 커밋(`d5e8fb4` 다음) (2026-11-02). 근거 표기는 [PRINCIPLES.md](../PRINCIPLES.md) F1을 따른다. 설계: [MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md](../design/MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md) §13.1. 계정 동작은 [ACCOUNTS.md](ACCOUNTS.md)에 있다.

- **[코드] 버전 상수**: `src/claire/store/db.py`의 `SCHEMA_VERSION = 15`, lineage는 `claire-bible/common` 그대로다. `_validate_migration_source`는 저장된 version이 15보다 크면 변경 없이 거부한다.
- **[코드] 추가 객체**(`_migrate_v15_accounts`, 모두 `IF NOT EXISTS`·`_ensure_column`으로 멱등)
  - 테이블 `accounts`, `username_tombstones`, `server_settings`
  - 인덱스 `idx_accounts_single_admin`(관리자 한 명), `idx_knowledge_bases_account_personal`(계정마다 기본 테마 하나)
  - 트리거 `accounts_username_immutable`(설정된 USERNAME 변경 거부)
  - 열 `themes.account_id`, `auth_sessions.account_id`, `auth_sessions.authenticated_at`, `oauth_codes.account_id`, `oauth_tokens.account_id`, `oauth_auth_requests.account_id`
- **[코드] 데이터 이관**(멱등): `server_settings.id_salt`가 없을 때만 만든다. 관리자 행(`id=1`, `role=admin`, `status=setup_required`, `username=NULL`)이 없을 때만 만들고, `public_id`는 고정 상수 `ADMIN_LEGACY_PUBLIC_ID_UNIX_MS`로 계산해 다시 실행해도 같다. `account_id`가 NULL인 테마와 자격증명 행을 1로 채운다. `id=1` 행이 관리자가 아니면 실패한다. v14의 `users` 테이블은 그대로 두고 쓰지 않는다.
- **[코드] 기본 테마 중복이면 실패**: v15 블록의 첫 단계(`_check_single_default_theme_per_account`)가 한 계정(NULL은 1로 봄)에 `is_personal=1` 테마가 둘 이상인지 본다. 있으면 어느 쪽이 기본인지 추측하지 않고 `RuntimeError`(계정과 `themes.id` 목록 포함)를 낸다. 이 검사는 v15 객체를 만들기 전에 실행되고 `init_db`는 `schema_version`을 쓰기 전에 멈추므로, v14 DB에서는 v15 테이블·열·행을 하나도 쓰지 않고 `schema_version=14`가 남는다(`tests/test_schema_v15.py`의 `test_multiple_default_themes_fail_migration_without_changes`). `is_personal=1`을 계정마다 하나로 고친 뒤 다시 실행한다.
- **[코드] 보조 파일**: `data/themes.json` 항목과 `data/auth/api_tokens.json` 항목에 `account_id`가 없으면 관리자(1)로 읽는다. 값이 정수가 아니면 테마는 `ValueError`, API 토큰은 그 항목을 무시한다.

## v16 (테마 번호와 레거시 호스트, P2)

확인 기준: P2 멀티 서브에이전트 통합 (2026-11-03). 설계: [MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md](../design/MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md) §7.2, §13.2.

- **[코드] 버전 상수**: v16 도입 당시 `src/claire/store/db.py`의 `SCHEMA_VERSION`은 16이었다(현재 값은 아래 "v17" 절). lineage는 `claire-bible/common` 그대로다.
- **[코드] 추가 객체**(`_migrate_v16_theme_numbers`, 모두 `IF NOT EXISTS`·`_ensure_column`으로 멱등):
  - 테이블 `legacy_hosts`: `(host TEXT PRIMARY KEY, kb_id INTEGER NOT NULL REFERENCES themes(id) ON DELETE CASCADE, activated_at REAL, sunset_at REAL, indefinitely INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL)`
  - 인덱스 `idx_legacy_hosts_kb_id`
  - 열 `themes.number`(INTEGER UNIQUE), `themes.visibility_epoch`(INTEGER NOT NULL DEFAULT 1), `themes.embed_provider`(TEXT NOT NULL DEFAULT ''), `themes.embed_model`(TEXT NOT NULL DEFAULT '')
- **[코드] 테마 번호 백필**: `themes.number`가 비어 있는 행을 `timestamp_id("themes", salt, unix_ms)`로 채운다. `created_at=0`이거나 비어 있는 레거시 행은 `ADMIN_LEGACY_PUBLIC_ID_UNIX_MS`로 하한을 고정하여 모든 이관 번호가 `>= 2^56`이 되도록 보장한다(A3).
- **[코드] 레거시 호스트 이관**: 기존 `themes.fqdn` 값이 있는 행을 `legacy_hosts` 테이블로 복사하고, `sunset_at = NULL` 상태로 보존하여 관리자가 이관을 활성화할 때까지 일몰 시계를 유예한다(R23-2).
- **[코드] 서버 정책**: `server_settings`에 `legacy_host_policy`(`default_sunset_days`, `allow_indefinite`)가 기록된다.

## v17 (연합 팔로워, 툼스톤, 도메인 차단, Fedify 사이드카 FP2-1)

확인 기준: FP2-1 구현 (2026-11-11). 현재 동작: [FEDERATION.md](FEDERATION.md) §5.6.

- **[코드] 버전 상수**: v17 도입 당시 `SCHEMA_VERSION`은 17이었다(현재 값은 아래 "v18" 절). lineage는 `claire-bible/common` 그대로다.
- **[코드] 추가 객체**(`_migrate_v17_federation`, 모두 `IF NOT EXISTS`로 멱등. 테이블만 더하고 기존 행은 바꾸지 않는다, A3):
  - 테이블 `federation_followers`: `(public_id INTEGER UNIQUE NOT NULL, account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE, actor_iri TEXT NOT NULL, inbox_iri TEXT NOT NULL, shared_inbox_iri TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL, PRIMARY KEY (account_id, actor_iri))`. `public_id`는 시간 정렬 번호(`claire.core.ids`)이고 컬렉션 커서다.
  - 인덱스 `idx_federation_followers_actor`(`actor_iri`): 원격 액터의 `Delete`·`Update`가 계정과 무관하게 행을 찾는다. 옛 설계 초안에는 없던 인덱스다.
  - 테이블 `federation_tombstones`: `(public_id INTEGER PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('actor', 'object')), gone_at REAL NOT NULL)`. USERNAME이 없고(K4) `accounts` 외래 키도 없다. 퍼지 뒤에도 행이 남아야 하기 때문이다.
  - 테이블 `federation_domain_blocks`: `(domain TEXT PRIMARY KEY, created_at REAL NOT NULL, created_by INTEGER NOT NULL REFERENCES accounts(id), note TEXT NOT NULL DEFAULT '')`. `domain`은 소문자 ASCII(punycode)다.
- **[코드] 저장소 함수**: `src/claire/store/federation.py`. 팔로워 저장(`upsert_follower`, 키는 `(account_id, actor_iri)`), 삭제, 원격 액터 단위 삭제·인박스 갱신, 개수, 커서 페이지(`max_id`·`min_id`·`since_id`), 툼스톤 기록·조회, 도메인 정규화·차단·해제·목록·판정(`blocking_domain`, 차단은 하위 도메인에도 적용), 차단 목록 ETag. 이 모듈의 함수는 커밋하지 않는다.
- **[코드] 설계와 다른 점**: 옛 설계 초안은 `federation_objects`와 `federation_outbox`도 version 17에 적었다. 이 두 테이블은 FP3-1에서 그 코드와 함께 v18로 더했다(쓰이지 않는 스키마를 먼저 만들지 않는다).
- **[테스트]** `tests/test_schema_v17.py`.

## v18 (연합 발행 객체와 아웃박스, Fedify 사이드카 FP3-1)

확인 기준: FP3-1 구현 (2026-11-11). 현재 동작: [FEDERATION.md](FEDERATION.md) §7.6.

- **[코드] 버전 상수**: v18 도입 당시 `src/claire/store/db.py`의 `SCHEMA_VERSION`은 18이었다(현재 값은 아래 "v19" 절). lineage는 `claire-bible/common` 그대로다.
- **[코드] 추가 객체**(`_migrate_v18_federation_publishing`, 모두 `IF NOT EXISTS`로 멱등. 테이블만 더한다, A3):
  - 테이블 `federation_objects`: `(public_id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE, kb_id INTEGER NOT NULL REFERENCES themes(id) ON DELETE CASCADE, document_id TEXT NOT NULL, published_by_account_id INTEGER NOT NULL REFERENCES accounts(id), published_at REAL NOT NULL, updated_at REAL NOT NULL, UNIQUE (kb_id, document_id))`. `public_id`는 시간 정렬 번호이고 객체 ID이자 아웃박스 컬렉션 커서다.
  - 인덱스 `idx_federation_objects_account`(`account_id, public_id`). 옛 설계 초안에는 없던 인덱스다.
  - 테이블 `federation_outbox`: `(id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('create', 'update', 'delete', 'update_actor', 'delete_actor')), object_public_id INTEGER, status TEXT NOT NULL CHECK (status IN ('pending', 'handed_off', 'rejected')), lease_until REAL, error_code TEXT, created_at REAL NOT NULL, handed_off_at REAL)`. `account_id`에는 기본값도 외래 키도 없다(설계 §8.3 항목 5, 퍼지 뒤에도 `delete_actor` 작업이 남아야 한다).
  - 인덱스 `idx_federation_outbox_ready`(`status, id`).
- **[코드] 저장소 함수**: `src/claire/store/federation.py`의 `publish_document`, `retract_object`, `add_outbox_task`, `lease_outbox_tasks`, `ack_outbox_task`, `count_pending_tasks`, `list_objects`, `count_objects`. 커밋은 호출자가 한다. 객체 행과 작업 행은 한 트랜잭션에 쓴다.
- **[테스트]** `tests/test_federation_publish.py`. `tests/test_kb_id_explicit.py`가 `federation_objects` 질의의 `kb_id` 명시를 검사한다.

## v19 (계정 인터페이스 언어, i18n IP1-2)

확인 기준: IP1-2 구현 (2026-11-12). 현재 동작: [I18N.md](I18N.md).

- **[코드] 버전 상수**: v19 도입 당시 `src/claire/store/db.py`의 `SCHEMA_VERSION`은 19였다(현재 값은 아래 "v20" 절). lineage는 `claire-bible/common` 그대로다.
- **[코드] 추가 객체**(`_migrate_v19_account_locale`, `_ensure_column`으로 멱등. NULL 허용 열 하나만 더한다, A3):
  - 열 `accounts.locale`: `TEXT CHECK (locale IS NULL OR locale IN ('en', 'ko'))`. NULL은 자동 협상(쿠키 → `Accept-Language` → `CLAIRE_LANG` → `en`)이다. 기존 계정과 새 계정의 기본값은 NULL이다.
- **[코드] 데이터 이관**: 없다.
- **[코드] 저장소 함수**: `src/claire/store/accounts.py`의 `AccountStore.update_locale`. `None`과 `""`는 NULL을 쓰고, `en`·`ko`가 아닌 값은 쓰기 전에 `ValueError("invalid_locale")`를 낸다.
- **[테스트]** `tests/test_i18n_api.py`.

## v20 (`syndication_*` 정리, 원격 읽기 P5)

확인 기준: P5 구현 (2026-11-13). 설계: [FEDERATION_REMOTE_READING_DESIGN.md](../design/FEDERATION_REMOTE_READING_DESIGN.md) §4.3. 결정: 운영 서버는 하나이고 그 서버에서 신디케이션 기능을 쓴 적이 없다(소유자, 2026-11-13).

- **[코드] 버전 상수**: v20 도입 당시 `src/claire/store/db.py`의 `SCHEMA_VERSION`은 20이었다(현재 값은 아래 "v21" 절). lineage는 `claire-bible/common` 그대로다.
- **[코드] 제거 객체**(`_migrate_v20_syndication_cleanup`, 멱등):
  - 테이블 `syndication_items`, `syndication_targets`, `syndication_accounts`와 인덱스 `idx_synd_items_kb_collected`, `idx_synd_items_kb_canon`, `idx_synd_items_kb_doc`. 새 DB의 `SCHEMA`에도 없다.
  - 저장소 함수 `create_syndication_account`, `get_syndication_account`, `list_syndication_accounts`, `save_syndication_account_token`, `load_syndication_account_token`. 테마 삭제(`store/knowledge_base.py`)와 계정 퍼지(`store/accounts.py`)의 정리 목록에서도 `syndication_items`를 뺐다.
- **[코드] 재구성**: `syndication_dispatches`에서 `item_id`·`account_id` 열을 뺀다. 새 테이블을 만들고 모든 행을 같은 ID와 값으로 복사한 뒤 바꿔 넣는다(A3). 인덱스 `idx_synd_dispatches_kb`는 다시 만든다. 퍼지 없이 삭제된 테마의 행도 복사하도록 재구성 동안만 외래 키 검사를 끈다. `POST /syndication/dispatch`와 `GET /syndication/dispatches`의 동작은 같다. 목록 응답의 행에는 늘 NULL이던 `item_id`·`account_id` 키가 더 없다.
- **[코드] 안전 규칙**: 서버 운영자는 DB를 직접 관리하지 않는다고 본다. 그래서 마이그레이션은 시작을 멈추지 않고 행도 지우지 않는다. 제거할 세 테이블 중 행이 있는 테이블은 지우지 않고 `<이름>_retired_v20`으로 이름만 바꾸어 남기며, 경고 로그에 테이블 이름과 행 수를 적는다(`_retire_v20_filled_tables`). 테마 삭제와 계정 퍼지는 이 테이블의 행도 `kb_id`로 지운다(`V20_RETIRED_SYNDICATION_TABLES`). 빈 테이블은 지운다.
- **[코드] 설계와 다른 점**: 설계는 version 20을 FP4-2 전체(이 정리와 `federation_following` 등)에 적었다. 코드에서는 이 정리만 version 20이다.
- **[테스트]** `tests/test_schema_v20.py`.

## v21 (계정 피드, 원격 읽기 FP4-F1)

확인 기준: FP4-F1 구현 (2026-11-13). 설계: [FEDERATION_REMOTE_READING_DESIGN.md](../design/FEDERATION_REMOTE_READING_DESIGN.md) §1.3 규칙 7, §14 피드 단위 표(RD13, RD15). 동작은 [FEDERATION.md](FEDERATION.md) §15에 있다.

- **[코드] 버전 상수**: `src/claire/store/db.py`의 `SCHEMA_VERSION = 21`, lineage는 `claire-bible/common` 그대로다. `_validate_migration_source`는 저장된 version이 21보다 크면 변경 없이 거부한다.
- **[코드] 추가 객체**(`_migrate_v21_account_feeds`, 모두 `IF NOT EXISTS`로 멱등. 테이블만 더하고 기존 행은 바꾸지 않는다, A3):
  - 테이블 `feed_subscriptions`: `(id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE, url TEXT NOT NULL, site_url TEXT, title TEXT NOT NULL DEFAULT '', folder TEXT NOT NULL DEFAULT '', format TEXT CHECK (format IS NULL OR format IN ('rss', 'atom', 'json')), etag TEXT, last_modified TEXT, websub_hub TEXT, websub_topic TEXT, last_polled_at REAL, last_success_at REAL, next_poll_at REAL, last_status INTEGER, last_error TEXT, failure_count INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL, UNIQUE (account_id, url))`. `folder`는 한 단계이고 빈 문자열은 폴더 없음이다. `format`은 첫 성공 폴링 전에는 NULL이다.
  - 인덱스 `idx_feed_subscriptions_next_poll`(`next_poll_at`): 스케줄러가 폴링할 피드를 찾는다.
  - 테이블 `feed_items`: `(id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE, feed_id INTEGER NOT NULL REFERENCES feed_subscriptions(id) ON DELETE CASCADE, item_key TEXT NOT NULL, url TEXT, title TEXT NOT NULL DEFAULT '', author TEXT, summary TEXT, content TEXT, categories TEXT NOT NULL DEFAULT '[]', published_at REAL, modified_at REAL, fetched_at REAL NOT NULL, unread INTEGER NOT NULL DEFAULT 1 CHECK (unread IN (0, 1)), starred INTEGER NOT NULL DEFAULT 0 CHECK (starred IN (0, 1)), linked_kb_id INTEGER, linked_doc_id TEXT, UNIQUE (feed_id, item_key))`. `categories`는 원문 태그의 JSON 배열이다. `linked_kb_id`·`linked_doc_id`에는 외래 키가 없다. 적재된 문서는 공유 자산이고 피드 자료를 지워도 문서는 지우지 않는다(설계 §10 규칙 5).
  - 인덱스 `idx_feed_items_account`(`account_id, id`): 항목 목록의 커서 페이지.
- **[코드] 데이터 이관**: 없다.
- **[코드] 저장소 함수**: `src/claire/store/feeds.py`. 모든 함수가 호출자 계정을 질의에 넣는다. 계정 퍼지(`AccountStore.purge`)는 `delete_account_feed_data`로 그 계정의 두 테이블 행을 지운다. 계정 행은 퍼지 뒤에도 남으므로 `ON DELETE CASCADE`에 기대지 않는다.
- **[코드] 설계와 다른 점**: 설계 §7.1은 FP4의 테이블을 version 22에 적었다. 피드 단위가 먼저 시작해서(소유자, 2026-11-13) 피드 테이블은 version 21이다. 설계에는 열 목록이 없었다.
- **[테스트]** v21의 생성 테스트는 v22에서 지웠다. 이동은 `tests/test_client_db.py`가 검사한다.

## v22 (피드 테이블을 `client.db`로 이동, RD27)

확인 기준: FP4-2와 DS-1 구현 (2026-11-13). 설계: [DATA_STORE_LAYOUT_DESIGN.md](../design/DATA_STORE_LAYOUT_DESIGN.md) §3.2.

- **[코드] 버전 상수**: `SCHEMA_VERSION = 22`.
- **[코드] 이관**(`_migrate_v22_move_feed_tables`, 멱등): `claire.db`에 피드 테이블이 있으면, 행을 같은 ID로 `claire.db` 옆의 `client.db`에 복사하고, 모든 ID가 들어갔는지 확인한 뒤에만 두 테이블과 인덱스를 지운다. 복사가 끝나지 않으면(오류, 또는 파일 없는 메모리 DB에 행이 있는 경우) 테이블을 두고 다음 기동에서 다시 시도한다. 행은 잃지 않는다. 운영자가 할 일은 없다.
- **[코드]** `_migrate_v21_account_feeds`는 이제 아무것도 만들지 않는다. 새 DB의 `claire.db`에는 피드 테이블이 없다.
- **[테스트]** `tests/test_client_db.py`. 다른 테스트는 버전 번호 대신 `dbm.SCHEMA_VERSION`과 비교한다(DS-2).

## v23 (발행 문서의 내용 다이제스트, FP4-P1)

확인 기준: FP4-P1 잔여 구현 (2026-11-15). 설계: [FEDERATION_REMOTE_READING_DESIGN.md](../design/FEDERATION_REMOTE_READING_DESIGN.md) §5.5 규칙 5, 7.

- **[코드] 버전 상수**: `SCHEMA_VERSION = 23`.
- **[코드] 이관**(`_migrate_v23_object_content_digest`, 멱등): `federation_objects`에 `content_digest TEXT` 열을 더한다. 값은 팔로워가 마지막으로 받은 요약(없으면 제목)과 공유 토큰의 SHA-256이다. 이전 버전의 행은 NULL이며, 첫 비교에서 값만 채우고 `Update`를 보내지 않는다(규칙 7).
- **[테스트]** `tests/test_federation_publish.py`.

## v24 (발행 문서의 인용 정책, FP4-9Q)

확인 기준: FP4-9Q 구현 (2026-11-15). 설계: [FEDERATION_REMOTE_READING_DESIGN.md](../design/FEDERATION_REMOTE_READING_DESIGN.md) §5.4 규칙 6, §7.1.

- **[코드] 버전 상수**: v24 도입 당시 `SCHEMA_VERSION`은 24였다(현재 값은 아래 "v25" 절).
- **[코드] 이관**(`_migrate_v24_object_quote_policy`, 멱등): `federation_objects`에 `quote_policy TEXT NOT NULL DEFAULT 'manual'` 열을 더한다. `CHECK`는 `public`, `followers`, `manual`, `nobody`만 허용한다. 이전 버전의 행은 기본값 `manual`을 받는다. 이관은 기동 때 스스로 끝나며 운영자가 할 일은 없다.
- **[코드] 위치 근거**: 정책 변경과 `update` 작업(`federation_outbox`)이 `claire.db`의 한 트랜잭션에 있어야 하므로 이 열은 `client.db`가 아니라 `claire.db`에 둔다(`store.federation.set_object_quote_policy`).
- **[테스트]** `tests/test_federation_quotes.py`.

### 별도 저장소: `client.db` (`claire.db` 옆)

- **[코드]** 계정의 소통 데이터(피드, 팔로잉, 받은 게시물, 클라이언트 아웃박스)는 `claire.db`와 같은 폴더의 `client.db`에 있다(`src/claire/store/client_db.py`). 자체 `client_meta.schema_version`을 쓰며 현재 값은 `CLIENT_SCHEMA_VERSION = 9`, lineage는 `claire-client`다. 이 번호는 `claire.db` version과 무관하다.
  - **v1 (FP4-2)**: `feed_subscriptions`, `feed_items`(v21과 같은 열, `account_id`는 외래키 없는 값), `federation_following`, `federation_account_settings`(v8에서 제거), `federation_client_outbox`(설계 §5.2의 클라이언트 작업 종류, `target_iri`, `ref_id`).
  - **v2 (FP4-4)**: `federation_timeline_items`(계정이 받은 원격 게시물, 키 `(account_id, object_iri)`, 설계 §7.1). 테이블 추가만 한다.
  - **v3 (FP4-7)**: `federation_media_cache`(원격 미디어 캐시 기록, 키 `url_hash`, 설계 §7.2). 계정 열이 없고 모든 계정이 함께 쓴다. 테이블 추가만 하며, 이관은 기동 때 스스로 끝난다.
  - **v4 (FP4-8)**: `federation_object_reactions`(자기 객체에 대한 원격 `Like`·`Announce` 기록, 키 `(object_public_id, kind, actor_iri)`, 객체 소유 계정 `account_id`), `federation_auto_replies`(비공개 메시지 자동 안내 기록, 키 `(account_id, sender_iri)`, 내용은 저장하지 않음). 설계 §7.1. 테이블 추가만 하며, 이관은 기동 때 스스로 끝난다.
  - **v5 (FP4-9)**: `federation_notes`(계정이 쓴 노트, 키 `public_id`, 게시 문서와 같은 번호 공간, 설계 §5.4·§7.1)와 인덱스 `idx_federation_notes_account`. 삭제한 노트는 행을 두고 `deleted_at`만 채운다(객체 경로 410). 인용 열(`quote_of_iri`, `quote_state`, `quote_policy`)은 FP4-9Q에서 `ALTER TABLE ... ADD COLUMN`으로 더한다. 테이블 추가만 하며, 이관은 기동 때 스스로 끝난다.
  - **v6 (FP4-9Q)**: `federation_notes`에 인용 열 `quote_of_iri`, `quote_author_iri`, `quote_state`(`pending`, `approved`, `refused`, `revoked` 또는 NULL), `quote_authorization_iri`, `quote_policy`(기본값 `public`)를 `ALTER TABLE ... ADD COLUMN`으로 더한다(열이 없을 때만, 재구성 없음). v5의 행은 인용이 없고 정책은 `public`이다. 새 테이블 `federation_quote_authorizations`(자기 객체에 대한 원격 인용 요청과 그 결과, 키 `id`, 유일 `(object_public_id, quoting_object_iri)`, 객체 소유 계정 `account_id`, 요청 활동 ID `request_iri`)와 인덱스 `idx_federation_quote_authorizations_account`. 설계 §5.4 규칙 5·6, §6.6 규칙 5, §7.1. 이관은 기동 때 스스로 끝나며, 모르는 테이블과 행은 그대로 둔다. 테스트: `tests/test_federation_quotes.py`.
  - **v7 (CT-0)**: 열 `linked_theme_id`를 값과 함께 `linked_kb_id`로 바꾼다(`_migrate_v7_linked_kb_id`, `ALTER TABLE ... RENAME COLUMN`). 다른 단계가 새 이름을 쓰므로 이 단계를 맨 먼저 실행한다. 멱등이다. 테스트: `tests/test_schema_v27.py`.
  - **v8 (2026-11-18)**: `federation_account_settings`를 지운다(`_migrate_v8_drop_account_settings`). 받은 게시물 보존 기간은 서버 관리자의 고유 권한이므로(소유자 결정) 계정 값이 없고, 보존 실행은 모든 계정에 서버 값을 쓴다. 지운 계정 값의 개수를 경고 로그로 남긴다. 멱등이다. 테스트: `tests/test_client_db.py`.
  - **v9 (PN1, 2026-11-22)**: 알림과 웹 푸시 테이블 `notifications`(부분 유일 인덱스 `idx_notifications_group`: 읽지 않은 행의 `(account_id, kind, group_key)`), `push_subscriptions`(유일 `endpoint`), `push_queue`(키 `(notification_id, subscription_id)`), `notification_push_off`(키 `(account_id, kind)`), `notification_scan_seen`(키 `(scope, item_id)`)을 더한다(`_migrate_v9`). 테이블 추가만 하며, 이관은 기동 때 스스로 끝난다. 계정 퍼지가 앞의 네 테이블에서 그 계정 행을 지운다([NOTIFICATIONS.md](NOTIFICATIONS.md) §3). 테스트: `tests/test_notifications.py`.
- **[코드]** 파일을 건너는 트랜잭션은 없다. 계정 퍼지는 `client.db`의 그 계정 행을 따로 지우고 커밋한다(`purge_account_of_claire_db`).
- **[코드]** 저장된 값이 `CLIENT_SCHEMA_VERSION`보다 크면 `RuntimeError`(다운그레이드 거부). `claire migrate`와 서비스 기동이 파일을 만들거나 맞춘다. `cb-manuscript backup`은 `claire.db`·`auth.db`처럼 스냅샷으로 복사한다.

### 별도 저장소: `data/auth/auth.db`

- **[코드]** 계정 비밀과 제어 평면은 지식 DB와 분리된 `data/auth/auth.db`에 있다(`src/claire/store/auth_db.py`). 자체 `meta.schema_version`을 쓰며 현재 값은 `AUTH_DB_SCHEMA_VERSION = 7`다. 이 번호는 위 지식 DB version과 무관하다("v14 불변 규칙"의 보조 저장소 규칙).
  - **v1 (P1/P3)**: 계정 자격증명(`auth_secrets`), 초대(`invitations`, `invitation_redemptions`), 사용량(`account_usage`), 로그인 스로틀(`login_throttle`), 단기 OIDC 상태(`oidc_states`).
  - **v2 (P4)**: `account_usage`에 UTC 일 적재 집행용 `daily_day`·`daily_ingests` 열 추가(설계 §10.3, §13.3).
  - **v3 (P5)**: `oidc_states`에 계정 연결용 `account_id` 열 추가, 외부 IdP 계정 연결 테이블 `account_identities` 연동(설계 §12, §13.3).
  - **v4 (유저 단위 기능)**: 계정별 텔레그램 봇 `account_telegram_bots`와 계정 백업 주기 `account_backup_policies` 테이블 추가.
  - **v5 (Fedify 사이드카 FP1-2)**: 연합 액터 키 테이블 `actor_keys` 추가. 기본 키는 `(account_id, alg)`이고 `account_id` 0은 서버 액터다. `alg`는 `rsa2048`·`ed25519`만 받는다. 개인 키는 `SecretService`가 AAD `actor_key|{account_id}|{alg}`로 암호화한다(`src/claire/store/actor_keys.py`, [FEDERATION.md](FEDERATION.md) §6, `tests/test_actor_keys.py`). 설계 문서는 이 변경을 "auth.db version 4"라고 적었지만, 설계 작성 뒤에 v4가 먼저 쓰여서 코드에서는 v5다.
  - **v6 (key decryption audit, 2026-11-19)**: adds the table `provider_key_decryptions` (primary key `account_id, provider, fingerprint, process, purpose, minute`; `count`, `first_at`, `last_at`) and the index on `last_at`. `CREATE TABLE IF NOT EXISTS` makes it in an older file; no data changes. See [ACCOUNTS.md](ACCOUNTS.md) §5.8.
  - **v7 (PWA notifications PN1, 2026-11-22)**: adds the table `server_key_pairs` (primary key `name`; `public_key`, `private_ciphertext`, `private_nonce`, `key_version`, `created_at`): key pairs of the server that are not actor keys. The row `webpush_vapid` is the VAPID pair of Web Push; `SecretService` encrypts the private key with the additional data `server_key|webpush_vapid`, and the first use makes the row (`store/notifications.py`, [NOTIFICATIONS.md](NOTIFICATIONS.md) §3). `CREATE TABLE IF NOT EXISTS` makes it in an older file; no data changes.
- **[코드]** 저장된 값이 5보다 크면 다운그레이드로 보고 `RuntimeError`, 1보다 작거나 정수가 아니면 `RuntimeError`다. lineage 표식은 없다.
- **[코드]** `claire migrate`가 지식 DB보다 먼저 auth.db를 만들거나 맞추고, 서비스 기동도 같은 일을 한다.
- **[코드]** 예전 연합 스위치 `server_settings.federation`(Fedify 사이드카 FP1-3)은 기존 `server_settings` 테이블의 키 하나라서 claire.db 버전을 올리지 않았다. 2026-11-22에 스위치를 없앴다(연합은 끌 수 없는 고유 기능, 결정 GD4). 스키마는 바뀌지 않았고, 남아 있는 `federation` 행은 읽지 않는다([FEDERATION.md](FEDERATION.md) §7.1).

## 운영 경계

스키마 전환은 서비스 기동 전에 명시적인 migration 경로에서 수행한다. health, liveness와 진단 조회는 읽기 전용 연결을 사용하며 DB, registry 또는 schema 객체를 생성·수정하지 않는다. 이전 version은 migration 전까지 비정상 또는 비호환 상태로 보고한다.

- **[code]** Exception: `connect_telemetry` runs `CREATE ... IF NOT EXISTS` and an `ALTER TABLE` on each connection, also when the Support Bundle only reads telemetry. See [`TELEMETRY.md`](TELEMETRY.md) §2.

마이그레이션은 다음 규칙을 따른다.

1. source version, lineage와 필수 객체 서명을 쓰기 전에 확인한다.
2. 삭제 또는 의미 변경이 필요한 데이터는 검증 가능한 형식으로 먼저 보존한다.
3. 마이그레이션 중간 실패가 부분 schema를 남기지 않도록 명시적 transaction을 사용한다.
4. version과 lineage는 목표 schema 적용과 검증이 끝난 뒤 기록한다.
5. 적용 후 version, lineage, 객체 서명과 데이터 무결성을 다시 검사한다.
6. 알 수 없는 source는 원본을 변경하지 않고 fail-closed한다.

## 이후 스키마 변경 규칙

1. **[결정]** 새 테이블·열은 기능 설계(모델 정의)에서 필요성과 소유 기능을 먼저 정하고, 문서·멱등 마이그레이션·이전 version 데이터 보존 테스트를 함께 제출한다. 무분별한 확장을 막기 위해서다.
2. **[결정]** 더 이상 쓰이지 않는 스키마 영역은 순차 소각한다. 소각도 새 version으로 하며, 삭제할 데이터가 있으면 운영 경계 2번대로 먼저 보존한다. 예: v14 `users` 테이블은 v15부터 쓰지 않는다(소각 후보).
3. 같은 version의 의미를 바꾸지 않는다. 이미 배포된 잘못된 version은 폐기하고 새 번호를 사용한다.
4. 새 version은 같은 커밋에서 위 version 표, 앞 version 절의 버전 상수 줄, 새 version 절을 함께 고친다. 다른 문서는 현재 번호를 적지 않고 이 표를 가리킨다. 공개 저장소(`origin`)에 올라간 version에 객체를 더하는 것도 3번의 의미 변경이다.

The retired v12 recovery is described above. Other stores have their own versions and do not share this version number: the telemetry database (no version marker now, see [`TELEMETRY.md`](TELEMETRY.md) §2), the Support Bundle archive (format 3) and sidecar registry (format 1), and the `cb-manuscript` backup archive (`BACKUP_FORMAT_VERSION` 1 in `ops/cb_manuscript.py`).

## v25 (서버 설정 테이블 이름, 용어 "서버")

확인 기준: 용어 정리 (2026-11-16). Claire 설치 하나를 Mastodon 문서처럼 "서버"라고 부른다("인스턴스"를 쓰지 않는다).

- **[코드] 버전 상수**: v25 도입 당시 `SCHEMA_VERSION`은 25였다(현재 값은 아래 "v26" 절).
- **[코드] 이관**(`_migrate_v25_server_settings`, 멱등): `instance_settings` 테이블을 행과 함께 `server_settings`로 이름만 바꾼다. 이 단계는 v15 단계보다 먼저 실행한다. v15 단계가 `server_settings`를 만들고 `id_salt`를 읽기 때문이다. 순서가 바뀌면 빈 테이블과 새 `id_salt`가 생겨 공개 ID가 바뀐다.
- **[코드] 안전 규칙**: 두 테이블이 함께 있으면(예상하지 않는 상태) `server_settings`에 없는 행만 복사한다. 옛 테이블의 모든 행이 같은 값으로 `server_settings`에 있을 때만 옛 테이블을 지운다. 아니면 옛 테이블을 `instance_settings_retired_v25`로 이름만 바꾸어 남기고 경고 로그를 쓴다. 기동은 멈추지 않고 행도 잃지 않는다. 운영자가 할 일은 없다.
- **[테스트]** `tests/test_schema_v25.py`.

## v26 (계정 엔티티 언어)

확인 기준: 엔티티 언어 구현 (2026-11-16).

- **[코드] 버전 상수**: v26 도입 당시 `SCHEMA_VERSION`은 26이었다(현재 값은 아래 "v27" 절).
- **[코드] 추가 객체**(`_migrate_v26_account_entity_language`, `_ensure_column`으로 멱등. NULL 허용 열 하나만 더한다, A3):
  - 열 `accounts.entity_language`: `TEXT`. NULL은 "설정 안 함"이며 응답은 저장된 대표 레이블을 그대로 싣는다. 기존 계정과 새 계정의 값은 NULL이다. 인터페이스 언어(`accounts.locale`)와 관계가 없다.
- **[코드] 데이터 이관**: 없다. `entities.name`과 `entities.aliases`는 바뀌지 않는다.
- **[코드] 저장소 함수**: `AccountStore.update_entity_language`. `None`과 `""`는 NULL을 쓰고, `en`·`ko`·`ja`·`zh`(`src/claire/ontology/label_language.py`의 `ENTITY_LANGUAGES`)가 아닌 값은 쓰기 전에 `ValueError("invalid_entity_language")`를 낸다. 열에 CHECK를 두지 않아 언어를 더할 때 이관이 필요 없다.
- **[테스트]** `tests/test_entity_language.py`.

## v27 (지식 베이스 이름, CT-0)

확인 기준: 공유 지식 베이스 설계 단위 CT-0 (2026-11-17). 설계: [`SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md`](../design/SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md) §5.1, §12.1. 이름만 바꾸고 동작은 바꾸지 않는다.

- **[코드] 버전 상수**: v27 도입 당시 `SCHEMA_VERSION`은 27이었다(현재 값은 아래 "v28" 절).
- **[코드] 이관**(`_migrate_v27_kb_names`, 멱등, 한 트랜잭션): 테이블 `themes` → `knowledge_bases`, 열 `is_default` → `is_personal`, 모든 테이블의 열 `theme_id` → `kb_id`, `syndication_dispatches.target_theme_id` → `target_kb_id`. `ALTER TABLE ... RENAME`이라 행과 다른 테이블의 외래키가 그대로 따라간다. 이름에 `theme`이 있는 인덱스는 지우고, `SCHEMA`와 뒤 단계가 새 이름(`idx_knowledge_bases_*`, `idx_<table>_kb_*`, `idx_knowledge_bases_account_personal`)으로 다시 만든다.
- **[코드] 순서**: `init_db`는 저장된 버전이 27보다 작을 때만 이 단계를 `SCHEMA`보다 먼저 실행한다. 순서가 바뀌면 `SCHEMA`가 옛 `themes` 옆에 빈 `knowledge_bases`를 만든다. 버전 28은 컬렉션용 새 `themes` 테이블을 가지므로, 27 이상에서는 이 단계를 실행하지 않는다.
- **[코드] 파일 재작성**: 같은 단계가 `claire.db` 옆의 작업 상태 파일(`active_ingest.json`, `active_jobs/*.json`)의 키 `theme_id`를 한 번 `kb_id`로 바꾼다. 읽을 수 없는 파일은 그대로 두고 경고 로그를 쓴다.
- **[코드] `data/themes.json` 퇴역**: 쓰기 가능한 첫 적재(`KnowledgeBaseManager.reload`)가 파일에만 있는 항목을 예전처럼 DB에 넣고(`account_id`가 없으면 관리자), 파일을 `themes.json.retired`로 바꾼다. 그 뒤 어떤 코드도 이 파일을 읽거나 쓰지 않는다. 읽을 수 없는 파일은 그대로 두고 기동은 계속한다.
- **[코드] 다른 형식**: 계정 백업 형식 2(스냅샷도 새 이름. 형식 1은 복원 때 꺼낸 사본에 같은 이름 바꾸기를 한다, `_upgrade_v1_snapshots`), 그래프 번들 형식 3(manifest 키 `kb_id`. 1과 2는 `theme_id`로 읽는다), 번들 sidecar 형식 2(형식 1의 `theme_id`도 읽는다). `client.db` v7은 위 절.
- **[코드] 코드 이름**: `store/knowledge_base.py`, `KnowledgeBaseManager`, `KnowledgeBaseInfo`, `KnowledgeBaseNotFound`, `get_kb_manager`, 메서드 `get_kb`·`list_kbs`·`define_kb`·`update_kb`·`delete_kb`·`reset_kb`·`personal_kb_for` 등. 응답 JSON의 키도 `kb_id`·`is_personal`이다. 내부 설정 `CLAIRE_THEME_ID`는 `CLAIRE_KB_ID`다.
- **[테스트]** `tests/test_schema_v27.py`.

## v28 (공유 지식 베이스와 테마, CT-1)

확인 기준: 공유 지식 베이스 설계 단위 CT-1의 스키마 부분 (2026-11-17). 설계: [`SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md`](../design/SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md) §5.4, §11.2, §11.4.

- **[코드] 버전 상수**: v28 도입 당시 `SCHEMA_VERSION`은 28이었다(현재 값은 아래 "v29 to v31" 절).
- **[코드] 스키마 단계**(`_migrate_v28_shared_kb_schema`, 멱등, 기동마다 실행): `knowledge_bases`에 `created_by_account_id`, `join_policy`(기본 `approval`), `state`(기본 `active`), `archived_at`, `knowledge_type`(CHECK `real`·`fictional`, 개인 지식 베이스는 NULL), `seed_entities`(기본 `[]`), `forked_from_kb_id`, `forked_at`을 더한다. 협업 테이블(`kb_memberships`, `kb_contributions`, `kb_contribution_revisions`, `kb_submissions`, `kb_submission_items`, `kb_auto_rules`, `kb_auto_rule_themes`, `kb_contribution_blocks`, `kb_mutual_collaborations`, `kb_moderation_log`, `account_reputation`)과 테마 테이블(`themes`(컬렉션), `theme_knowledge_bases`, `kb_signatures`, `kb_document_matches`)을 만든다.
- **[코드] 퇴역 테이블**: 읽는 코드가 없는 v14 테이블 `theme_members`와 `users`는 행이 없으면 지우고, 행이 있으면 `<name>_retired_v28`로 이름만 바꾸고 경고 로그를 쓴다. 기동은 멈추지 않는다.
- **[코드] 저장소 경계**: `store/kb_collab.py`만 협업 테이블을, `store/themes.py`만 테마 테이블을 읽고 쓴다. `kb_collab.kind_violations`는 개인·공유 두 종류에 맞지 않는 `knowledge_bases` 행을 찾는다.
- **[코드] 번호(§5.4)**: `ids.insert_with_timestamp_id`의 `is_taken`으로, 테마 번호는 지식 베이스 번호를, 지식 베이스 번호는 테마 번호를 피해 다음 `seq`로 넘어간다.
- **[코드] 변환**(`_migrate_v28_convert_knowledge_bases`, 설계 §12.2, 기동마다 옛 모양으로 남은 행에 실행): 개인이 아니고 소유자가 있는 지식 베이스마다 savepoint 하나로 공유 지식 베이스로 바꾼다. `created_by_account_id = 옛 소유자`, `account_id = NULL`, `is_public = 1`, `join_policy = 'approval'`, `state = 'active'`, `knowledge_type = 'real'`. **비공개였던 추가 테마도 공개가 된다(CT5·CT6).** 옛 소유자의 계정 상태가 `active`·`suspended`이거나 첫 설정 전 관리자(`setup_required`)이면 `moderator` 멤버십을 넣고, 아니면 넣지 않는다(관리자가 중재). 문서마다 기여 한 건(`origin 'migration'`, 크레딧은 옛 소유자, 루트 없음, `hidden=1`이면 `hidden` 아니면 `visible`, `approved_at` = 이관 시각). 그 문서들의 `watch_enabled = 0`, `refresh_queue`·`expand_queue`의 그 지식 베이스 행 삭제(CT10). 이미 있는 멤버십·기여는 다시 넣지 않는다.
- **[코드] 실패한 변환**: savepoint가 실패한 행은 그대로 남고 경고 로그를 쓴다. 그 행은 두 종류(§11.2 표)에 맞지 않으므로 `KnowledgeBaseManager`가 서비스하지 않는다(모든 라우트 404, `unavailable_kbs()`가 사유와 함께 돌려준다). 다음 기동이 다시 시도한다. 기동은 멈추지 않는다.
- **[코드] Credential scopes (CT-2, design Sections 6.6 and 11.3)**: `_migrate_v28_credential_scopes` runs at each start and is idempotent. It deletes the rows of `auth_sessions` with the scope `collaborator` and drops the column `knowledge_bases.is_collaborator_accessible`. The valid scopes are `owner`, `write`, and `readonly` (`store.accounts.SESSION_SCOPES`, `store.token_store.TOKEN_SCOPES`). `auth.db` has no scope column, so its version does not change. `data/auth/api_tokens.json`: `TokenStore.load` changes each token with the scope `collaborator` to `readonly`, adds `scope_notice: "collaborator_to_readonly"`, and writes the file; `GET /tokens` gives these tokens once in `scope_notices` (`take_scope_notices`). `CLAIRE_COLLABORATOR_TOKEN` is not read.
- **[코드] Owner step of version 15 and shared knowledge bases**: `_migrate_v15_accounts` gives the admin as the owner only to rows without an owner and without a knowledge type. A shared knowledge base has no owner by design; without this condition each start would give it to the admin, and the conversion of version 28 would replace its founder and its knowledge type (`tests/test_shared_kb.py`).
- **[코드] Contribution merge jobs (CT-4)**: no new table. A merge job is a row of `jobs` with `source_type = 'contribution_merge'`, the payload `{"contribution": <public ID>, "revision": <id of kb_contribution_revisions>?}`, and the states `pending`, `running`, `done`, `error`. `purge_document_cascade` sets the contribution of a purged copy to `removed`. Details: [`THEMES.md`](THEMES.md) §4.8.
- **[코드]** 공유 KB 감독 화면(`/moderation/oversight`, 옛 `/admin/kb`는 308 호환 리디렉트)이 활성 중재자 부재·재임베딩 필요·병합 실패 경고를 표시한다(`console_admin.js` `#admin-kb-host`).
- **[테스트]** `tests/test_schema_v28.py`, `tests/test_shared_kb.py`.

## v29 to v31 (federation of shared knowledge, units KF1 to KF3)

- **[코드] 버전 상수**: v31 도입 당시 `SCHEMA_VERSION`은 31이었다(현재 값은 아래 "v32" 절).
- **[코드]** The steps `_migrate_v29_knowledge_federation`, `_migrate_v30_remote_knowledge_bases`, `_migrate_v31_contribution_remote_columns`, and `_migrate_v31_knowledge_base_actors` run at each start and are idempotent. They make the tables `kf_*` and `theme_remote_knowledge_bases` when they are not there, and v31 adds the columns of a remote source to `kb_contributions`. They change no row. A table with one of these names and another form becomes `<name>_retired_v<version>`; the start continues. The tables and columns are in [`KNOWLEDGE_FEDERATION.md`](KNOWLEDGE_FEDERATION.md) Sections 10.2, 11.3, and 12.3.
- **[테스트]** `tests/test_knowledge_federation_stores.py`, `tests/test_knowledge_federation_remote.py`, `tests/test_knowledge_federation_kf3.py`.

## v32 (federation of shared knowledge, unit KF4)

- **[코드] 버전 상수**: v32 도입 당시 `SCHEMA_VERSION`은 32였다(현재 값은 아래 "v33" 절).
- **[코드]** The step `_migrate_v32_offers_and_grants` runs at each start and is idempotent. It makes the tables `kf_outbound_offers`, `kf_inbound_offers` (module `federation.knowledge.offers`), `kf_grants`, and `kf_received_grants` (module `federation.knowledge.grants`) with their indexes when they are not there. It changes no row. A table with one of these names and another form becomes `<name>_retired_v32` with its rows; the start continues. The tables are in [`KNOWLEDGE_FEDERATION.md`](KNOWLEDGE_FEDERATION.md) Section 13.3.
- **[테스트]** `tests/test_knowledge_federation_kf4.py` (`test_migration_v32_makes_the_tables_and_keeps_a_table_of_another_form`), `tests/test_knowledge_federation_stores.py`.

## v33 (federation of shared knowledge, limits of the security review)

- **[코드] 버전 상수**: v33 도입 당시 `SCHEMA_VERSION`은 33이었다(현재 값은 아래 "v34" 절).
- **[코드]** The step `_migrate_v33_knowledge_federation_limits` runs at each start, after the steps v29 to v32, and is idempotent. The start continues for each state of the data.
  - `kf_inbox_journal` gets the columns `quota_domain` (the registrable domain of the signer) and `payload_bytes`, and the index `idx_kf_inbox_journal_quota` (`journal.ensure_quota_columns`). An old row has NULL and does not count in the quota of 24 hours.
  - `kf_outbox`: the new table `kf_outbox_activities` (`activity_iri`, `payload`) keeps the activity JSON one time for each activity. A `kf_outbox` of version 31 to 32 has the column `payload` (one copy for each recipient): the step copies each activity one time to `kf_outbox_activities`, then drops the column (`outbox.move_payloads`). No activity is lost.
  - `kb_contributions.remote_updated_at` (the time of the version of the origin that a copy has) is in `kb_collab.REMOTE_COLUMNS`; the step v31 and this step add it when it is not there. An old copy has NULL: its next revision is applied.
  - The tables and the columns are in [`KNOWLEDGE_FEDERATION.md`](KNOWLEDGE_FEDERATION.md) Section 14.
- **[테스트]** `tests/test_knowledge_federation_security.py` (`test_the_outbox_keeps_one_copy_of_an_activity_for_its_recipients_and_the_migration_moves_old_copies`), `tests/test_knowledge_federation_stores.py`.

## v34 (federation of shared knowledge, unit KF5)

- **[코드] 버전 상수**: v34 도입 당시 `SCHEMA_VERSION`은 34였다(현재 값은 아래 "v35" 절).
- **[코드]** The step `_migrate_v34_automatic_offers` runs at each start, before the step v32, and is idempotent. It adds the column `automatic` (`INTEGER NOT NULL DEFAULT 0`) to a `kf_outbound_offers` of version 32 or 33 (`offers.ensure_automatic_column`), so that the step v32 finds each column that the code reads and keeps the table. An old row gets 0 (an offer of a submission). When the table is not there, the step v32 makes it with the column. No other row changes; the start continues for each state of the data. The table is in [`KNOWLEDGE_FEDERATION.md`](KNOWLEDGE_FEDERATION.md) Sections 13.3 and 15.3.
- **[테스트]** `tests/test_knowledge_federation_kf5.py` (`test_migration_v34_adds_the_column_and_keeps_the_rows_of_version_33`).

## v35 (link expansion terms and shadow records of the same-subject source merge)

- **[코드] 버전 상수**: v35 도입 당시 `SCHEMA_VERSION`은 35였다(현재 값은 아래 "v36" 절).
- **[코드]** The step `_migrate_v35_expansion_source_names` runs at each start and is idempotent. In `raw_inbox.source`, `onehop:<parent id>` becomes `expand:<parent id>`, and `telegram-expand` becomes `expand-proposal` (terms: [`GLOSSARY.md`](GLOSSARY.md)). No other row changes.
- **[코드]** The schema script makes the table `same_subject_shadow` and its index `idx_same_subject_shadow_kb`. Automatic expansion writes a row in the mode `same_subject_merge = "shadow"` ([`EXPANSION.md`](EXPANSION.md) §3.1).
- **[이력]** Commit `91e39495` (2026-11-18) made v35 without the verdict columns. Commit `c2b92bbb` (the same day) added them to this table and kept the number 35. The two commits are in the public repository (`origin`), so two forms of v35 are possible, against rule 3. Version 36 corrects this: the verdict columns belong to v36, and the step v36 gives each form of v35 the same form.
- **[코드]** `telemetry.db` (not versioned): `connect_telemetry` changes the call type `judge_research` to `judge_gate` at each connection (`_migrate_gate_call_type`).
- **[테스트]** `tests/test_schema_v35.py`.

## v36 (human verdict of the shadow records)

- **[코드] 버전 상수**: v36 도입 당시 `SCHEMA_VERSION`은 36이었다(현재 값은 아래 "v37" 절).
- **[코드]** The step `_migrate_v36_shadow_verdict_columns` runs at each start, after the step v35, and is idempotent (`_ensure_column`). It adds the columns `verdict` (`same`, `different`, or NULL), `verdict_note`, `verdict_at`, and `verdict_by` (the account of the verdict) to `same_subject_shadow` when they are not there. The rows of v35 stay and get NULL (not judged). No other row changes; the start continues for each state of the data. The schema script makes a new table with the columns. The WebUI (`PUT /api/admin/expansion/shadow/{record_id}/verdict`) writes the verdict ([`EXPANSION.md`](EXPANSION.md) §3.1).
- **[테스트]** `tests/test_schema_v36.py` (`test_a_v35_database_gets_the_verdict_columns_and_keeps_its_records`).

## v37 (sources of a personal knowledge base for other readers)

- **[코드] 버전 상수**: v37 도입 당시 `SCHEMA_VERSION`은 37이었다(현재 값은 아래 "v38" 절).
- **[코드]** The step `_migrate_v37_personal_source_public` runs at each start, after the step v36, and is idempotent (`_ensure_column`). It adds `accounts.personal_source_public` (`INTEGER NOT NULL DEFAULT 1`, 0 or 1). Each account of v36 and each new account gets 1: each reader sees the sources, as before v37 (owner decision 2026-11-18). With 0 only the account sees them. No other row changes. The rule of the responses is in [`ACCOUNTS.md`](ACCOUNTS.md) ("출처 공개").
- **[테스트]** `tests/test_schema_v37.py`.

## v38 (entity pair review)

- **[코드] 버전 상수**: v38 도입 당시 `SCHEMA_VERSION`은 38이었다(현재 값은 아래 "v39" 절).
- **[코드]** The schema script makes `pair_review_items` (one row for each queued pair of a Jev shadow decision: knowledge base, document, decision index and time, candidate, reasons, snapshots of the two entities, outcome, Jev score and model, pre-label, rule version; unique on document, index, time, and candidate; deleted with its document and its knowledge base) and `pair_review_verdicts` (one verdict for each item and account: `same`, `different`, or `unsure`, note, blind flag, rule version; deleted with its item and, by the account data cleanup, with its account). The step `_migrate_v38_pair_review_backfill` runs at each start, after the step v37: once (mark `meta.pair_review_backfill_v38`) it makes items from the shadow records that exist in `documents.meta` (`snapshot_source='current'`). No other row changes. Rules: [GRAPH_BUNDLE_REVIEW.md](GRAPH_BUNDLE_REVIEW.md) §5.5.
- **[테스트]** `tests/test_schema_v38.py`.

## v39 (임베딩 공간 레지스트리 및 다중 공간 EOS 전환, AE6b)

확인 기준: Track B (`AE6a`–`AE6d`) 구현 (2026-11-21). 설계: [`AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md`](../design/AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md) §7.3 (`AED7`, `AED10`, `AED11`).

- **[코드] 버전 상수**: `SCHEMA_VERSION = 39`.
- **[코드] 추가·변경 객체**(`_migrate_v39_embedding_spaces`, 멱등):
  - 테이블 `embedding_spaces(space_id PRIMARY KEY, provider, model, dim, prompt_format_version, normalized, eos_date, status)`. 초기 시드로 `google:gemini-embedding-001:3072:v1_param`(`eos_date='2028-05-14'`)과 `google:gemini-embedding-2:3072:v2_prompt`(`dim=3072`, `prompt_format_version='v2_prompt'`)를 등록한다.
  - 열 `knowledge_bases.active_space_id` (`TEXT`), `knowledge_bases.target_space_id` (`TEXT`).
  - 테이블 `embeddings`: `space_id TEXT NOT NULL DEFAULT ''` 열을 추가하고 기본 키를 `(owner_id, kb_id, space_id)`로 재구성하며 인덱스 `idx_embeddings_kb_space(kb_id, space_id)`를 생성한다.
- **[코드] 데이터 이관**: v38 이하 DB의 기존 `embeddings` 행은 `(knowledge_bases.embed_provider, embeddings.model, embeddings.dim)` 기준으로 표준 `space_id`를 계산해 재적재하며(레거시 `'claire'`·빈 모델 행은 `space_id=''`), `embed_model`이 설정된 KB의 `active_space_id`를 채운다.
- **[테스트]** `tests/test_schema_v39.py`, `tests/test_embedding_pin.py`.

## v40 (OAuth 2.1 인증·Device Grant 및 클라이언트 주도 추출 스테이징, AE2a/AE2b/AE4)

확인 기준: Track C (`AE2a`, `AE2b`, `AE3`) 및 Track D (`AE4`) 구현 (2026-11-21). 설계: [`AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md`](../design/AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md) §5.1 (`AED4`), §6.3–§6.6 (`AED5`).

- **[코드] 버전 상수**: `SCHEMA_VERSION = 40`.
- **[코드] 추가·변경 객체**(`_migrate_v40_oauth_v21`, 멱등):
  - 열 `oauth_codes.granted_scope` (`TEXT DEFAULT 'readonly'`).
  - 열 `oauth_tokens.granted_scope`, `token_family_id`, `refresh_expires_at`, `rotated_at`, `revoked_at`, `last_used_at` 및 인덱스 `idx_oauth_tokens_family(token_family_id)`.
  - 테이블 `oauth_pending_requests(tx_id PRIMARY KEY, client_id, client_name, redirect_uri, code_challenge, code_challenge_method, scope, state, account_id, created_at, expires_at, consumed_at)`.
  - 테이블 `oauth_device_codes(device_code PRIMARY KEY, user_code UNIQUE, client_id, client_name, scope, status, account_id, interval, last_poll_at, created_at, expires_at)` 및 인덱스 `idx_oauth_device_codes_user(user_code)`.
- **[코드] 추가·변경 객체**(`_migrate_v40_client_staging`, 멱등):
  - 열 `accounts.extraction_mode` (`TEXT NOT NULL DEFAULT 'provider' CHECK (extraction_mode IN ('provider', 'client'))`). v45부터 이 단계는 이 열을 만들지 않고, v45 단계가 이 열을 제거한다.
  - 열 `documents.content_hash`, `documents.extraction_status` (`TEXT NOT NULL DEFAULT 'extracted' CHECK (extraction_status IN ('pending', 'extracted', 'failed'))`), `documents.claim_token`, `documents.lease_expires_at`, `documents.claimed_by_client_id`, `documents.raw_chars` 및 인덱스 `idx_documents_kb_extraction_status(kb_id, extraction_status)`.
- **[코드] 데이터 이관**: v39 이하의 기존 `oauth_codes`·`oauth_tokens` 행은 `granted_scope`가 비어 있을 때 `scope`로 백필하고, `token_family_id`가 없는 기존 토큰은 고유 패밀리 ID를 부여한다. 기존 `accounts.extraction_mode`가 비어 있거나 허용 값 밖인 행은 `'provider'`로, 기존 `documents.extraction_status`가 비어 있거나 허용 값 밖인 행은 `'extracted'`로 정규화한다.
- **[테스트]** `tests/test_schema_v40.py`, `tests/test_oauth_flow.py`, `tests/test_client_staging_ae4.py`.

## v41 (USERNAME 대소문자 보존·비구별 인덱스 및 7일 보호 이름 변경)

확인 기준: 계정 관리 개선 구현 (2026-11-21).

- **[코드] 버전 상수**: `SCHEMA_VERSION = 41`.
- **[코드] 추가·변경 객체**(`_migrate_v41_username_case_and_rename`, 멱등):
  - 트리거 `accounts_username_immutable` 제거 및 트리거 `accounts_username_not_null` 생성(`OLD.username IS NOT NULL AND NEW.username IS NULL`만 `ABORT`).
  - 유니크 인덱스 `idx_accounts_username_ci ON accounts(lower(username)) WHERE username IS NOT NULL` 생성.
  - 테이블 `username_tombstones`에 열 `reusable_after REAL` 추가 및 인덱스 `idx_username_tombstones_lower ON username_tombstones(lower(username))` 생성.
- **[테스트]** `tests/test_schema_v41.py`.

## v43 (permalink registry)

Design: [`PERMALINK_UNIFICATION_DESIGN.md`](../design/PERMALINK_UNIFICATION_DESIGN.md) Section 7. Current behavior: [`PERMALINKS.md`](PERMALINKS.md).

- **[code]** New table `permalinks` (`src/claire/store/permalinks.py`): `token` (primary key), `kind` (`document`, `note`, `entity`), `kb_id`, `account_id`, `target`, `identity`, `link_access`, `alias_of`, `gone_at`, `created_at`. Unique index on (`kind`, `target`) for rows without `alias_of`.
- **[code]** `_migrate_v43_permalinks` runs at each start and is idempotent. For each document with valid rows in `doc_shares`, the newest valid token becomes the address token with `link_access` 1, and the older valid tokens become alias rows. Each other document and each entity gets a new token with `link_access` 0. Then the step removes the table `doc_shares` (`DROP TABLE IF EXISTS`).
- **[code]** `doc_shares` is not in the schema now, and no code reads or writes it. The import of an old theme database file (`migrate_multi_db_to_single_db`) reads the `doc_shares` table of that file and puts its tokens into `permalinks` (`permalinks.adopt_share_tokens`).
- **[code]** A review of the 106 tables that the code declares (2026-11-23) found no other table without a read and a write in the code.

## v44 (an account has a USERNAME)

- **[decision]** An account without a USERNAME must not exist, except the administrator row before the setup (owner, 2026-11-23). The production database had no such row on 2026-11-23 (owner, read-only query).
- **[code]** `_migrate_v44_username_required` makes two triggers on `accounts`: `accounts_username_required_insert` (before insert) and `accounts_username_required_update` (before update). Each refuses a row whose `username` is NULL and whose `status` is not `setup_required`, with the error `username required`. The trigger `accounts_username_not_null` of v41 stops the removal of a USERNAME.
- **[code]** The step runs at each start, is idempotent, and does not read or change a row. A row that broke the rule before the step stays; an update of that row is refused until it has a USERNAME.

## v45 (the extraction mode of each ingest request)

Design: [`AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md`](../design/AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md) decision `AED4-rev`. Current behavior: [`INGESTION.md`](INGESTION.md) §1 step 6.

- **[decision]** The agent client and the API are both usable; each ingest request selects its extraction mode (owner, 2026-11-24). The account setting of v40 has no function.
- **[code]** `_migrate_v45_retire_account_extraction_mode` drops the column `accounts.extraction_mode` (`ALTER TABLE ... DROP COLUMN`; the stored values are not used again) and adds the column `raw_inbox.extraction_mode` (`TEXT`, `NULL` or `'provider'` or `'client'`). The step runs at each start and is idempotent.
- **[code]** `log_inbox` writes the mode of the request. A retry (`recover-loop`, `replay-failed`, manual retry) takes the same mode; a row from before v45 has NULL and takes the default mode of the account.
- **[test]** `tests/test_schema_v45.py`, `tests/test_client_staging_ae4.py`.

## v46 (preferred languages)

Design: [`ACCOUNT_PREFERRED_LANGUAGES_DESIGN.md`](../design/ACCOUNT_PREFERRED_LANGUAGES_DESIGN.md) (owner decisions P1–P4, 2026-11-24).

- **[code]** `_migrate_v46_preferred_languages` (runs at each start, idempotent): adds `accounts.preferred_languages` (`TEXT`, NULL: the account follows the server default language); one time (marker `server_settings.languages_v46`) it writes `server_settings.default_languages` from `CLAIRE_PREFERRED_LANGUAGES` (else `ko` for a database from before v46; a new database gets no row unless the variable is set), then `en`; an account with `accounts.locale` gets an own list (that locale, then the server default language) when it differs from the server default language; then the step drops `accounts.locale`. The step v19 no longer adds `accounts.locale`.
- **[test]** `tests/test_i18n_api.py` (`test_v45_database_migrates_the_locale_to_the_preferred_languages_and_the_migration_is_idempotent`, `test_new_database_has_no_server_default_language_unless_the_variable_is_set`).
