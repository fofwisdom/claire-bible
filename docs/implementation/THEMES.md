# 멀티 테마(단일 DB 파티셔닝) 구현 정본

기준 커밋: `00e06cb` (2026-11-02) · 대상 독자: 테마 경계를 건드리는 에이전트와 운영자 · 스키마 계약: [`SCHEMA_VERSIONING.md`](SCHEMA_VERSIONING.md) · 명령: [`COMMANDS.md`](COMMANDS.md) §3.10

이 문서는 **지금 코드가 테마를 어떻게 저장하고, 요청을 어느 테마로 보내고, 경계를 어떻게 지키는지**를 적는다. 근거 태그는 [`PROVIDERS.md`](PROVIDERS.md)와 같다: `[코드]`, `[실측]`, `[결정]`, `[미검증]`.

---

## 0. 작업 전에 알아 둘 것

1. **[코드] 모든 테마가 한 DB 파일을 쓴다.** v14부터 모든 테마의 데이터는 `data/claire.db` 하나에 있다. 테마 격리는 `theme_id` 컬럼으로만 이루어진다. 테마별 `Settings` 사본도 같은 `db_file`과 `data_dir`을 가리킨다(`ThemeManager._settings_for_theme_info`). 따라서 다음은 **모든 테마가 공유한다.**
   - 파일 기반 상태: `data/jobs/*.json` 작업 기록, `providers.json`, `ingest_limits.json`, 텔레메트리
   - 이런 상태를 응답에 내보낼 때는 테마 권한으로 걸러야 한다. 예: `server.py`의 `_visible_active_jobs`(§4.3)
2. **[코드] 새 쿼리는 반드시 `theme_id`로 범위를 정한다.** `dbm.*` 함수 대부분이 `theme_id` 인자를 받는다. 이 인자를 빠뜨리면 기본값 0이 되거나 모든 테마를 대상으로 하게 된다.
3. **[코드] 테마 조회 기본값은 "없으면 테마 0"이다.** `ThemeManager.get_theme(ref)`는 `strict=False`(기본)일 때 동작이 다음과 같다(§3).
   - 찾지 못하면 기본 테마 0을 돌려준다.
   - 레이블 일부만 일치해도 그 테마를 고른다.
   - 사용자가 지정한 테마로 데이터를 쓰는 경로는 `strict=True`를 써야 한다.
4. **[코드] 단일 테마 모드(`CLAIRE_MULTI_THEME=0`)에서는 레지스트리를 읽지 않는다.** 메모리에 있는 기본 테마 0 하나만 쓴다.

---

## 1. 파일 지도

| 파일 | 역할 | 주요 기호 |
| :--- | :--- | :--- |
| `src/claire/store/theme.py` | 테마 레지스트리, 조회, 수명주기, 테마별 설정 | `ThemeInfo`, `ThemeManager`(`reload`, `get_theme`, `define_theme`, `update_theme`, `delete_theme`, `reset_theme`, `theme_data_counts`, `get_settings_for_theme`, `active_theme_settings`, `get_theme_by_fqdn`), `THEMES_REGISTRY_FILENAME`, `validate_fqdn`, `get_theme_manager` |
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

**[코드] 테마 0은 항상 있고 삭제할 수 없다.** 협업자 접근은 꺼져 있고, 기본 초점은 빈 문자열로 고정된다(`ThemeInfo.from_dict`).

**[코드] 테마로 나뉘는 테이블**: `documents`, `entities`, `relations`, `embeddings`, `raw_inbox`, `extractions`, `proposals`, `jobs`, `refresh_queue`, `expand_queue`, `document_snapshots`, `document_presentations`, `doc_shares`, `purged_tombstones`, `syndication_items`, `syndication_dispatches`. `(theme_id, …)` 복합 인덱스는 `init_db`가 만든다.

**[코드] 레거시 DB 병합.** 멀티 테마 모드에서 `ThemeManager(create_registry=True)`가 `reload()`할 때 동작은 다음과 같다.
- `data/themes/*/claire.db`가 있으면 `migrate_multi_db_to_single_db`를 부른다.
- 각 파일을 `ATTACH`하고, 행을 `INSERT OR IGNORE`로 `theme_id`를 붙여 복사한다.
- 다 끝나면 원본을 `claire.db.migrated`로 이름을 바꾼다.

---

## 3. 테마 조회 (`ThemeManager.get_theme`)

**[코드]** 매칭 순서는 다음과 같다.
1. 비어 있으면 테마 0
2. 숫자 ID 정확 일치
3. 레이블 정확 일치(대소문자 무시)
4. **레이블 부분 일치**: 처음 걸린 테마를 고른다

찾지 못하거나 접근할 수 없는 경우는 `strict`에 따라 갈린다.
- `strict=True`: `KeyError`를 던진다.
- `strict=False`: 접근 가능한 기본 테마를 돌려준다.

**[코드] `strict=True`를 쓰는 곳**: CLI의 `--theme`(`get_effective_settings`, `untombstone`, `import-graph`), `theme reset`·`theme delete`(`cmd_theme`), 작업 목록 필터(`_visible_active_jobs`)

**[코드] `strict=False`를 쓰는 곳**: HTTP(`_get_theme_ctx`), `IngestServicePool.get_service`, MCP 도구(`mcp_tools.py`), 텔레그램 등. 이 경로에서는 존재하지 않는 테마를 지정해도 테마 0에서 처리된다. [`PRINCIPLES.md`](../PRINCIPLES.md)의 알려진 위반 항목을 보라.

---

## 4. 요청이 테마에 도달하는 경로

### 4.1 HTTP (`server.py`)

**[코드]** `_extract_theme_ref`의 우선순위는 다음과 같다.

1. `Host`가 등록된 테마 FQDN이고 그 테마 ID가 1 이상이면 그 테마로 **고정**한다. 이때 쿼리나 본문의 `theme`은 무시한다.
2. `Host`가 테마 0의 FQDN이면 `?theme=`, 본문의 `theme`, `X-Claire-Theme` 순으로 따르고, 셋 다 없으면 테마 0이다.
3. 그 밖에는 `?theme=` → 본문 `theme` → `X-Claire-Theme` 헤더 순이다.

**[코드]** `_theme_accessible`의 접근 검사(`_get_theme_ctx`가 쓰고, 실패하면 404)는 다음과 같다.

| 인증 범위 | 접근 가능한 테마 |
| :--- | :--- |
| `anonymous` | `is_public`인 테마. FQDN이 설정된 테마(ID 1 이상)는 그 FQDN으로 접속했을 때만 |
| `collaborator` | `is_public` 또는 `is_collaborator_accessible`인 테마 |
| 그 밖(`owner`, `readonly` 등) | 모든 테마 |

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
| 정의 | `define_theme` | `themes` 행 추가, JSON 갱신 |
| 수정 | `update_theme` | FQDN·GA ID는 `validate_fqdn`·`validate_ga_measurement_id`로 검증하고 중복을 거부 |
| 리셋 | `reset_theme` | `reset_graph(theme_id)`로 엔티티·관계·임베딩·FTS를 지우고, §2의 테이블에서 해당 `theme_id` 행을 삭제. 메타데이터와 ID는 보존 |
| 삭제 | `delete_theme(purge=False)` | `themes` 행만 삭제. 데이터 행은 남는다 |
| 삭제 + 소각 | `delete_theme(purge=True)` | 리셋과 같은 범위를 지운 뒤 `themes` 행을 삭제 |

**[결정]** CLI `theme reset`과 `theme delete`는 기본이 dry-run이다. `--apply`가 있어야 실행하며, 데이터를 지우는 경우에는 확인을 받는다(PRINCIPLES D2·D3).

---

## 6. 알려진 한계와 미검증

- **[코드] 레거시 병합 실패를 삼킨다.** `reload()`는 `migrate_multi_db_to_single_db`의 예외를 `log.warning`으로만 남기고 계속한다. 그러면 `claire migrate`가 성공으로 끝나고 서비스도 시작되지만, 옛 테마 데이터 일부가 빠진 상태가 된다(PRINCIPLES C2·E3 위반).
  - 단순히 예외를 다시 던지게 고치면 재시도가 안전하지 않다. 병합은 DB 파일 단위로 커밋하고, 자동 증가 키 테이블은 `id`를 빼고 복사한다. 그래서 부분 실패 뒤에 다시 실행하면 그 테이블의 행이 중복될 수 있다.
- **[코드] 삭제(`purge=False`) 뒤에 남은 데이터 행에는 접근할 수 없다.** 해당 `theme_id`의 행이 DB에 남지만 어느 테마에서도 보이지 않는다. 정리하는 명령은 확인하지 못했다.
- **[미검증]** `import-graph --theme`은 테마의 `db_path`(현재는 단일 DB)를 대상으로 한다. 가져온 행에 `theme_id`가 붙는지는 확인하지 않았다.
- **[미검증]** 텔레그램 `/theme`과 MCP 도구의 테마 지정에서 비공개 테마 접근이 어떻게 검사되는지는 이 문서 작성 시 확인하지 않았다.
