# 멀티 테마(단일 DB 파티셔닝) 구현 정본

기준 커밋: `00e06cb` (2026-11-02) · 대상 독자: 테마 경계를 건드리는 에이전트와 운영자 · 스키마 계약: [`SCHEMA_VERSIONING.md`](SCHEMA_VERSIONING.md) · 명령: [`COMMANDS.md`](COMMANDS.md) §3.10

이 문서는 **지금 코드가 테마를 어떻게 저장하고, 요청을 어느 테마로 보내고, 경계를 어떻게 지키는지**를 적는다. 근거 태그는 [`PROVIDERS.md`](PROVIDERS.md)와 같다: `[코드]`, `[실측]`, `[결정]`, `[미검증]`.

---

## 0. 작업 전에 알아 둘 것

1. **[코드] 모든 테마가 한 DB 파일을 쓴다.** v14부터 모든 테마의 데이터는 `data/claire.db` 하나에 있다. 테마 격리는 `theme_id` 컬럼으로만 이루어진다. 테마별 `Settings` 사본도 같은 `db_file`과 `data_dir`을 가리킨다(`ThemeManager._settings_for_theme_info`). 따라서 다음은 **모든 테마가 공유한다.**
   - 파일 기반 상태: `data/jobs/*.json` 작업 기록, `providers.json`, `ingest_limits.json`, 텔레메트리
   - 이런 상태를 응답에 내보낼 때는 테마 권한으로 걸러야 한다. 예: `server.py`의 `_visible_active_jobs`(§4.3)
2. **[코드] 새 쿼리는 반드시 `theme_id`로 범위를 정한다.** `dbm.*` 함수 대부분이 `theme_id` 인자를 받는다. 이 인자를 빠뜨리면 기본값 0이 되거나 모든 테마를 대상으로 하게 된다.
   - 집행: `tests/test_theme_id_explicit.py`가 요청 경로 모듈(`api/`, `telegram_bot.py`, `ingest/`, `expand/`, `retrieval/`, `presentation/`, `graph_bundle.py`)에서 `store.db`·`store.queries`의 `theme_id` 함수를 이 인자 없이 부르는 곳을 찾아 실패시킨다. 모든 테마가 대상이면 `theme_id=None`처럼 명시해야 한다.
   - 이 검사 밖: 직접 쓴 SQL, `store.vectors`, `pipeline.ingest`
3. **[코드] 없는 테마는 다른 테마로 바뀌지 않는다.** `ThemeManager.get_theme(ref)`는 찾지 못하거나 접근할 수 없는 테마에 `ThemeNotFound`(`KeyError` 하위 클래스)를 던진다(§3). 참조를 생략한 경우(`None`, 빈 문자열) 주체 계정의 기본 테마(`theme.is_default=True`, 관리자는 0)로 매칭한다. 레이블 부분 일치는 호출자가 `label_match="partial"`을 줄 때만 쓴다.
4. **[코드] 단일 테마 모드(`CLAIRE_MULTI_THEME=0`)에서도 Theme 0은 DB에서 읽는다(K8, Rev. 2.3).** 추가 테마 생성(`POST /themes`)만 잠그며, Theme 0 메타데이터와 번호는 항상 DB의 `themes` 테이블에서 읽어 일관된 코드 경로로 동작한다.
5. **[코드] 테마 소유권과 계정 귀속 (`account_id`)**: 모든 테마는 `ThemeInfo.account_id`를 통해 특정 계정에 귀속된다(`claire.db` v15 `themes.account_id`). 관리자 테마는 `account_id = 1`이며, 멤버 계정이 생성한 테마는 해당 계정의 `account_id`를 갖는다.
6. **[코드] 기본 테마 불변식의 `theme.is_default` 일원화 (Rev. 2.3)**: 멤버 계정(`account_id > 1`)의 기본 테마는 `id > 0, is_default = 1`이다. 따라서 `tid == 0` 하드코딩을 제거하고 모든 기본 테마 불변식을 `theme.is_default` 판정으로 일원화했다:
   - 기본 테마는 삭제할 수 없다 (`delete_theme`에서 `theme.is_default`이면 `ValueError`).
   - 기본 테마는 협업자 쓰기·접근이 차단된다 (`is_collaborator_accessible = False`).
   - 기본 테마는 기본 초점이 빈 문자열로 강제된다 (`default_focus = ""`).
   - 계정당 기본 테마는 정확히 1개이며, `claire.db`의 `idx_themes_account_default ON themes(account_id) WHERE is_default = 1` 유니크 인덱스로 강제된다.
   - 테마 생략 시 `ThemeManager.default_theme_for(account_id)`로 해당 계정의 기본 테마를 단일 진실 공급원으로 조회한다.
7. **[코드] 테마 레이블 유일성 범위 `(account_id, label)`**: 테마 레이블 중복 검사는 전역이 아니라 **계정 범위** `(account_id, label)`로 판정한다. 서로 다른 계정 간에는 동일한 레이블명을 쓸 수 있으며, 동일 계정 내에서만 대소문자 무시 중복이 거부된다(`define_theme`, `update_theme`).

---

## 1. 파일 지도

| 파일 | 역할 | 주요 기호 |
| :--- | :--- | :--- |
| `src/claire/store/theme.py` | 테마 레지스트리, 조회, 수명주기, 테마별 설정 | `ThemeInfo`, `ThemeNotFound`, `ThemeManager`(`reload`, `get_theme`, `define_theme`, `update_theme`, `delete_theme`, `reset_theme`, `theme_data_counts`, `get_settings_for_theme`, `active_theme_settings`, `get_theme_by_fqdn`), `THEMES_REGISTRY_FILENAME`, `validate_fqdn`, `get_theme_manager` |
| `src/claire/store/db.py` | `themes` 테이블, `theme_id` 인덱스, 레거시 DB 병합 | `init_db`, `reset_graph`, `migrate_multi_db_to_single_db` |
| `src/claire/api/server.py` | HTTP 요청의 테마 결정과 접근 검사 | `_extract_theme_ref`, `_theme_accessible`, `_get_theme_ctx`, `_visible_active_jobs`, `_job_theme_id` |
| `src/claire/ingest/service.py` | 테마별 적재 서비스 | `IngestService.theme_id`, `IngestServicePool` |
| `src/claire/cli.py` | CLI의 테마 결정 | `get_effective_settings`, `cmd_theme`, `cmd_migrate` |
| `src/claire/telegram_bot.py` | 텔레그램의 테마 선택 | `on_theme`, `parse_message_theme` |
| `src/claire/ingest/active.py` | 진행 중 작업 기록(전 테마 공용 디렉터리) | `get_active_jobs`, `get_primary_active_job` |

---

## 2. 저장 구조

**[코드] 레지스트리는 두 곳에 있다.**
- 정본은 DB의 `themes` 테이블이다.
- `data/themes.json`은 `_save_registry()`가 원자적으로 다시 쓰는 사본이다.
- `reload()`는 DB 행을 읽고, JSON에만 있는 항목은 DB에 `INSERT OR IGNORE`로 넣는다. 그래서 두 곳이 다르면 DB 값이 이긴다.
- `define_theme`은 DB에 넣고 커밋한 뒤 JSON을 다시 쓴다. ID는 `seq`와 같은 일련번호(`_next_seq`)다.

**[코드] 기본 테마(`theme.is_default=True`)는 항상 계정마다 1개 존재하며 삭제할 수 없다.** 관리자 계정의 기본 테마는 ID 0이며, 멤버 계정의 기본 테마는 `id > 0, is_default = 1`이다. 모든 기본 테마는 협업자 접근이 꺼져 있고, 기본 초점은 빈 문자열로 고정된다(`ThemeInfo.from_dict`).

**[코드] 테마로 나뉘는 테이블**: `documents`, `entities`, `relations`, `embeddings`, `raw_inbox`, `extractions`, `proposals`, `jobs`, `refresh_queue`, `expand_queue`, `document_snapshots`, `document_presentations`, `doc_shares`, `purged_tombstones`, `syndication_items`, `syndication_dispatches`. `(theme_id, …)` 복합 인덱스는 `init_db`가 만든다.

**[코드] 레거시 DB 병합.** 멀티 테마 모드에서 `ThemeManager(create_registry=True)`가 `reload()`할 때 동작은 다음과 같다.
- `data/themes/*/claire.db`가 있으면 `migrate_multi_db_to_single_db`를 부른다.
- 각 파일을 `ATTACH`하고, 행을 `INSERT OR IGNORE`로 `theme_id`를 붙여 복사한다.
- 다 끝나면 원본을 `claire.db.migrated`로 이름을 바꾼다.

---

## 3. 테마 조회 (`ThemeManager.get_theme`)

**[코드]** 매칭 순서는 다음과 같다.
1. 비어 있으면(`None`, 빈 문자열) 테마 0. 참조를 생략한 경우의 기본값이다.
2. 숫자 ID 정확 일치
3. 레이블 정확 일치(대소문자 무시)
4. 레이블 부분 일치(처음 걸린 테마): `label_match="partial"`일 때만

찾지 못했거나 접근할 수 없으면(`include_private=False`이면 비공개 테마, `collaborator=True`이면 협업 비허용 비공개 테마) `ThemeNotFound`를 던진다. 두 경우의 메시지는 같아서 비공개 테마의 존재가 드러나지 않는다. 다른 테마로 대신하는 분기는 없다(PRINCIPLES C1).

**[코드] 진입점별 처리**

| 진입점 | 레이블 일치 | 없는 테마 |
| :--- | :--- | :--- |
| HTTP `_get_theme_ctx` | 정확 | 404 `theme not found` (접근 불가와 같음) |
| HTTP 문서 → 테마 후보 (`_first_live_target`) | — | 삭제된 테마의 후보는 건너뛴다. 남는 후보가 없으면 404 |
| Syndication dispatch 대상·원본 테마 | 정확 | 404 |
| MCP 도구 (`_theme_id`) | 정확 | `{"error": "theme not found"}` |
| `IngestServicePool.get_service` | 정확 | `ThemeNotFound` 전파 (캐시된 서비스도) |
| CLI `--theme` 등 사람이 입력하는 조회 | 부분 | 종료 코드 2 |
| 텔레그램 `/theme <arg>`, `#태그` | 부분 | 안내 메시지 |
| 텔레그램 저장된 선택 테마 (`resolve_selected_theme`) | — | 선택을 지우고 요청을 거부한다(적재·검색 안 함) |

- 집행: `tests/test_theme_strict_lookup.py`

---

## 4. 요청이 테마에 도달하는 경로

### 4.1 HTTP (`server.py`)

**[코드]** 테마 참조 및 경로 해석 우선순위는 다음과 같다.

1. 경로에 테마 번호가 포함되어 있으면(`/themes/{number}/...`) 해당 테마로 고정(`pinned_theme_id`)한다. 고정과 모순되는 `?theme=` 쿼리나 본문 파라미터는 400으로 거부한다.
2. 경로에 고정이 없으면 `?theme=` → 본문 `theme` → `X-Claire-Theme` 헤더 순으로 해석한다(`resolve_theme_ref`).
3. 외부 식별자는 64비트 시간 정렬 번호(`number`, `>= 2^56`)를 사용한다. 내부 ID(`0 <= id < 2^31`)로 들어온 요청은 호환 수용하되 RFC 8594 `Deprecation` 및 `Sunset` 응답 헤더를 부착한다.
4. 개별 테마 FQDN 설정은 폐지되었다(O5). 기존 FQDN 접근은 `HostRouter`에서 308 정본 경로로 리다이렉트된다.

**[코드]** `_theme_accessible`의 접근 검사(`_get_theme_ctx`가 쓰고, 실패하면 404)는 다음과 같다.

| 인증 범위 | 접근 가능한 테마 |
| :--- | :--- |
| `anonymous` / 타 계정 방문자 | `is_public`인 테마 |
| `collaborator` | `is_public` 또는 `is_collaborator_accessible`인 테마 |
| 그 밖(`owner`, `readonly` 등) | 계정 내 모든 테마 |

### 4.5 테마 컬렉션 계약 (`GET /themes`)

- **2단계 컬렉션**: 루트(`OrderedCollection`)와 페이지(`OrderedCollectionPage`)로 제공된다(설계 §7.4).
- **커서 페이지네이션**: `max_id`, `min_id`, `since_id` 중 최대 1개와 `limit`(기본 20, 최대 80)를 지원한다. 둘 이상 커서 지정 시 400으로 거부된다.
- **캐시 규칙**: 익명 `public, max-age=60`, 인증 `private, no-cache`, 강한 ETag 및 조건부 요청(304). `Vary: Cookie, Authorization, Sec-Fetch-Dest, Accept`.
- **하위 호환**: P2–P3 기간 동안 루트 응답에 기존 `themes` 배열, `default_theme_id`, `default_theme_number`, `multi_theme`을 함께 반환한다. Owner에게는 `legacy_hosts` 목록을 함께 싣는다.

### 4.2 CLI

**[코드]** `get_effective_settings`의 동작은 다음과 같다.
- `--theme`이 있으면 엄격하게 조회한다. 없는 테마면 종료 코드 2로 실패한다.
- 단일 테마 모드에서는 경고를 출력하고 `--theme`을 무시한다.
- `--theme`이 없고 멀티 테마 모드면, `target`·`doc_id`·`token`으로 문서를 찾아 그 문서의 테마로 정한다(`resolve_document_targets`의 첫 결과).

### 4.3 공용 파일 상태와 테마 경계

**[코드]** `/stats`와 `/ingest/active`는 다음과 같이 동작한다.
- `active_jobs`를 `_visible_active_jobs`로 거른다. 요청자가 `_theme_accessible`로 접근할 수 있는 테마의 작업만 남는다.
- 대표 작업(`active_ingest`)은 거른 목록 안에서만 고른다(`get_primary_active_job(..., jobs=...)`). 이때 전 테마 공용인 레거시 `active_ingest.json`으로 폴백하지 않는다.
- 멀티 테마 모드에서는 권한 검사를 통과한 테마 ID만 쓰고, 날 `?theme=` 값은 무시한다(`_job_theme_id`).
- 이 처리가 없으면 공개 테마 FQDN에서 비공개 테마의 적재 URL과 제목이 보인다(`tests/test_active_jobs_theme_isolation.py`).

### 4.4 적재 서비스와 워커

- **[코드]** `IngestServicePool`은 테마마다 `IngestService`를 하나씩 캐시하고, LLM 프로바이더 인스턴스는 공유한다.
- **[코드]** `recover-loop`, `refresh-loop`, `expand-loop`와 `app health`, `migrate`는 `active_theme_settings()`로 테마 목록을 매번 다시 읽는다.
- **[코드]** `cmd_migrate`는 멀티 테마 모드에서 테마마다 `init_db`와 `require_current_schema`를 실행한다. 대상 파일은 모두 같은 단일 DB다.

---

## 5. 테마 수명주기

| 동작 | 코드 | 범위 |
| :--- | :--- | :--- |
| 정의 | `define_theme` | `account_id` 귀속, `themes` 행 추가, JSON 갱신. `(account_id, label)` 범위 레이블 중복 검사 |
| 수정 | `update_theme` | 동일 계정 내 레이블 중복 검사, FQDN·GA ID는 `validate_fqdn`·`validate_ga_measurement_id`로 검증하고 중복 거부. `theme.is_default`이면 `is_collaborator_accessible=False`, `default_focus=""` 강제 |
| 리셋 | `reset_theme` | `reset_graph(theme_id)`로 엔티티·관계·임베딩·FTS를 지우고, §2의 테이블에서 해당 `theme_id` 행을 삭제. 메타데이터와 ID는 보존 |
| 삭제 | `delete_theme(purge=False)` | `theme.is_default`이면 삭제 거부(`ValueError`). `themes` 행만 삭제. 데이터 행은 남는다 |
| 삭제 + 소각 | `delete_theme(purge=True)` | `theme.is_default`이면 삭제 거부(`ValueError`). 리셋과 같은 범위를 지운 뒤 `themes` 행을 삭제 |

**[결정]** CLI `theme reset`과 `theme delete`는 기본이 dry-run이다. `--apply`가 있어야 실행하며, 데이터를 지우는 경우에는 확인을 받는다(PRINCIPLES D2·D3).

---

## 6. 알려진 한계와 미검증

- **[코드] 레거시 병합 실패를 삼킨다.** `reload()`는 `migrate_multi_db_to_single_db`의 예외를 `log.warning`으로만 남기고 계속한다. 그러면 `claire migrate`가 성공으로 끝나고 서비스도 시작되지만, 옛 테마 데이터 일부가 빠진 상태가 된다(PRINCIPLES C2·E3 위반).
  - 단순히 예외를 다시 던지게 고치면 재시도가 안전하지 않다. 병합은 DB 파일 단위로 커밋하고, 자동 증가 키 테이블은 `id`를 빼고 복사한다. 그래서 부분 실패 뒤에 다시 실행하면 그 테이블의 행이 중복될 수 있다.
- **[코드] 삭제(`purge=False`) 뒤에 남은 데이터 행에는 접근할 수 없다.** 해당 `theme_id`의 행이 DB에 남지만 어느 테마에서도 보이지 않는다. 정리하는 명령은 확인하지 못했다.
- **[미검증]** `import-graph --theme`은 테마의 `db_path`(현재는 단일 DB)를 대상으로 한다. 가져온 행에 `theme_id`가 붙는지는 확인하지 않았다.
- **[미검증]** 텔레그램 `/theme`과 MCP 도구의 테마 지정에서 비공개 테마 접근이 어떻게 검사되는지는 이 문서 작성 시 확인하지 않았다.
