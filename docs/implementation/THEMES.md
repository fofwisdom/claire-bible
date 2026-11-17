# 멀티 테마(단일 DB 파티셔닝) 구현 정본

기준 커밋: `00e06cb` (2026-11-02); §0-4, §3, §5, §7, §8은 `d15ae8e0` (2026-11-16)에서 다시 확인 · 대상 독자: 테마 경계를 건드리는 에이전트와 운영자 · 설계 원칙: [`MULTI_THEME_ARCHITECTURE_DESIGN.md`](../design/MULTI_THEME_ARCHITECTURE_DESIGN.md) · 스키마 계약: [`SCHEMA_VERSIONING.md`](SCHEMA_VERSIONING.md) · 명령: [`COMMANDS.md`](COMMANDS.md) §3.10 · 공개 사이트와 GA: [`PUBLIC_SITE.md`](PUBLIC_SITE.md)

이 문서는 **지금 코드가 테마를 어떻게 저장하고, 요청을 어느 테마로 보내고, 경계를 어떻게 지키는지**를 적는다. 근거 태그는 [`PROVIDERS.md`](PROVIDERS.md)와 같다: `[코드]`, `[실측]`, `[결정]`, `[미검증]`.

---

## 0. 작업 전에 알아 둘 것

1. **[코드] 모든 테마가 한 DB 파일을 쓴다.** v14부터 모든 테마의 데이터는 `data/claire.db` 하나에 있다. 테마 격리는 `kb_id` 컬럼으로만 이루어진다. 테마별 `Settings` 사본도 같은 `db_file`과 `data_dir`을 가리킨다(`KnowledgeBaseManager._settings_for_theme_info`). 따라서 다음은 **모든 테마가 공유한다.**
   - 파일 기반 상태: `data/jobs/*.json` 작업 기록, `providers.json`, `ingest_limits.json`, 텔레메트리
   - 이런 상태를 응답에 내보낼 때는 테마 권한으로 걸러야 한다. 예: `server.py`의 `_visible_active_jobs`(§4.3)
2. **[코드] 새 쿼리는 반드시 `kb_id`로 범위를 정한다.** `dbm.*` 함수 대부분이 `kb_id` 인자를 받는다. 이 인자를 빠뜨리면 기본값 0이 되거나 모든 테마를 대상으로 하게 된다.
   - 집행: `tests/test_kb_id_explicit.py`가 요청 경로 모듈(`api/`, `telegram_bot.py`, `ingest/`, `expand/`, `retrieval/`, `presentation/`, `graph_bundle.py`)에서 `store.db`·`store.queries`의 `kb_id` 함수를 이 인자 없이 부르는 곳을 찾아 실패시킨다. 모든 테마가 대상이면 `kb_id=None`처럼 명시해야 한다.
   - 이 검사 밖: 직접 쓴 SQL, `store.vectors`, `pipeline.ingest`
3. **[코드] 없는 테마는 다른 테마로 바뀌지 않는다.** `KnowledgeBaseManager.get_kb(ref)`는 찾지 못하거나 접근할 수 없는 테마에 `KnowledgeBaseNotFound`(`KeyError` 하위 클래스)를 던진다(§3). 참조를 생략한 경우(`None`, 빈 문자열) 주체 계정의 기본 테마(`theme.is_personal=True`, 관리자는 0)로 매칭한다. 레이블 부분 일치는 호출자가 `label_match="partial"`을 줄 때만 쓴다.
4. **[코드] 단일 테마 모드(`CLAIRE_MULTI_THEME=0`)에서도 Theme 0은 DB에서 읽는다(K8, Rev. 2.3).** 테마 정의·수정·삭제(`define_kb`·`update_kb`·`delete_kb`는 `RuntimeError`, HTTP `POST /kb`, `PATCH /`, `DELETE /`는 403)를 잠근다. `reset_kb`은 잠그지 않는다. Theme 0 메타데이터와 번호는 항상 DB의 `knowledge_bases` 테이블에서 읽어 일관된 코드 경로로 동작한다.
5. **[코드] 테마 소유권과 계정 귀속 (`account_id`)**: 모든 테마는 `KnowledgeBaseInfo.account_id`를 통해 특정 계정에 귀속된다(`claire.db` v15 `themes.account_id`). 관리자 테마는 `account_id = 1`이며, 멤버 계정이 생성한 테마는 해당 계정의 `account_id`를 갖는다.
6. **[코드] 기본 테마 불변식의 `theme.is_personal` 일원화 (Rev. 2.3)**: 멤버 계정(`account_id > 1`)의 기본 테마는 `id > 0, is_personal = 1`이다. 따라서 `tid == 0` 하드코딩을 제거하고 모든 기본 테마 불변식을 `theme.is_personal` 판정으로 일원화했다:
   - 기본 테마는 삭제할 수 없다 (`delete_kb`에서 `theme.is_personal`이면 `ValueError`).
   - The personal knowledge base takes writes only from its account (`kb_access.decide`, §4.1). The column `is_collaborator_accessible` is removed (claire.db version 28, unit CT-2).
   - 계정당 기본 테마는 정확히 1개이며, `claire.db`의 `idx_knowledge_bases_account_personal ON themes(account_id) WHERE is_personal = 1` 유니크 인덱스로 강제된다.
   - 테마 생략 시 `KnowledgeBaseManager.personal_kb_for(account_id)`로 해당 계정의 기본 테마를 단일 진실 공급원으로 조회한다.
7. **[코드] 테마 레이블 유일성 범위 `(account_id, label)`**: 테마 레이블 중복 검사는 전역이 아니라 **계정 범위** `(account_id, label)`로 판정한다. 서로 다른 계정 간에는 동일한 레이블명을 쓸 수 있으며, 동일 계정 내에서만 대소문자 무시 중복이 거부된다(`define_kb`, `update_kb`).

---

## 1. 파일 지도

| 파일 | 역할 | 주요 기호 |
| :--- | :--- | :--- |
| `src/claire/store/knowledge_base.py` | 테마 레지스트리, 조회, 수명주기, 테마별 설정 | `KnowledgeBaseInfo`, `KnowledgeBaseNotFound`, `KnowledgeBaseManager`(`reload`, `get_kb`, `define_kb`, `update_kb`, `delete_kb`, `reset_kb`, `kb_data_counts`, `get_settings_for_kb`, `active_kb_settings`, `get_kb_by_fqdn`), `THEMES_REGISTRY_FILENAME`, `validate_fqdn`, `get_kb_manager` |
| `src/claire/store/db.py` | `knowledge_bases` 테이블, `kb_id` 인덱스, 레거시 DB 병합 | `init_db`, `reset_graph`, `migrate_multi_db_to_single_db` |
| `src/claire/api/server.py` | HTTP 요청의 테마 결정과 접근 검사 | `_theme_accessible`, `_get_theme_ctx`, `_get_personal_kb_ctx`, `_visible_active_jobs`, `_job_kb_id` |
| `src/claire/ingest/service.py` | 테마별 적재 서비스 | `IngestService.kb_id`, `IngestServicePool` |
| `src/claire/cli.py` | CLI의 테마 결정 | `get_effective_settings`, `cmd_theme`, `cmd_migrate` |
| `src/claire/telegram_bot.py` | 텔레그램의 테마 선택 | `on_theme`, `parse_message_theme` |
| `src/claire/ingest/active.py` | 진행 중 작업 기록(전 테마 공용 디렉터리) | `get_active_jobs`, `get_primary_active_job` |

---

## 2. 저장 구조

**[코드] 레지스트리는 두 곳에 있다.**
- 정본은 DB의 `knowledge_bases` 테이블이다.
- `data/themes.json`은 `_save_registry()`가 원자적으로 다시 쓰는 사본이다.
- `reload()`는 DB 행을 읽고, JSON에만 있는 항목은 DB에 `INSERT OR IGNORE`로 넣는다. 그래서 두 곳이 다르면 DB 값이 이긴다.
- `define_kb`은 DB에 넣고 커밋한 뒤 JSON을 다시 쓴다. ID는 `seq`와 같은 일련번호(`_next_seq`)다.

**[코드] 기본 테마(`theme.is_personal=True`)는 항상 계정마다 1개 존재하며 삭제할 수 없다.** 관리자 계정의 기본 테마는 ID 0이며, 멤버 계정의 기본 테마는 `id > 0, is_personal = 1`이다. 모든 기본 테마는 협업자 접근이 꺼져 있다.

**[코드] 테마로 나뉘는 테이블**: `documents`, `entities`, `relations`, `embeddings`, `raw_inbox`, `extractions`, `proposals`, `jobs`, `refresh_queue`, `expand_queue`, `document_snapshots`, `document_presentations`, `doc_shares`, `purged_tombstones`, `syndication_dispatches`. `(kb_id, …)` 복합 인덱스는 `init_db`가 만든다.

**[코드] 레거시 DB 병합.** 멀티 테마 모드에서 `KnowledgeBaseManager(create_registry=True)`가 `reload()`할 때 동작은 다음과 같다.
- `data/themes/*/claire.db`가 있으면 `migrate_multi_db_to_single_db`를 부른다.
- 각 파일을 `ATTACH`하고, 행을 `INSERT OR IGNORE`로 `kb_id`를 붙여 복사한다.
- 다 끝나면 원본을 `claire.db.migrated`로 이름을 바꾼다.

---

## 3. 테마 조회 (`KnowledgeBaseManager.get_kb`)

**[코드]** 매칭 순서는 다음과 같다.
1. 비어 있으면(`None`, 빈 문자열) 관리자 계정의 기본 테마(`personal_kb_for(ADMIN_ACCOUNT_ID)`, 보통 테마 0). 관리 도구(CLI)의 기본값이다. HTTP 요청은 §4.1의 선택 규칙을 따른다.
2. 숫자 ID 정확 일치
3. 레이블 정확 일치(대소문자 무시)
4. 레이블 부분 일치(처음 걸린 테마): `label_match="partial"`일 때만

찾지 못했거나 접근할 수 없으면(`include_private=False`이면 비공개 테마) `KnowledgeBaseNotFound`를 던진다. 메시지는 같아서 비공개 테마의 존재가 드러나지 않는다. 다른 테마로 대신하는 분기는 없다(PRINCIPLES C1).

**[코드] 진입점별 처리**

| 진입점 | 레이블 일치 | 없는 테마 |
| :--- | :--- | :--- |
| HTTP `_get_theme_ctx` | 정확 | 404 `theme not found` (접근 불가와 같음) |
| HTTP 문서 → 테마 후보 (`_first_live_target`) | — | 삭제된 테마의 후보는 건너뛴다. 남는 후보가 없으면 404 |
| Syndication dispatch 대상·원본 테마 | 정확 | 404 |
| MCP 도구 (`_kb_id`) | 정확 | `{"error": "theme not found"}` |
| `IngestServicePool.get_service` | 정확 | `KnowledgeBaseNotFound` 전파 (캐시된 서비스도) |
| CLI `--theme` 등 사람이 입력하는 조회 | 부분 | 종료 코드 2 |
| 텔레그램 `/theme <arg>`, `#태그` | 부분 | 안내 메시지 |
| 텔레그램 저장된 선택 테마 (`resolve_selected_theme`) | — | 선택을 지우고 요청을 거부한다(적재·검색 안 함) |

- 집행: `tests/test_theme_strict_lookup.py`

---

## 4. 요청이 테마에 도달하는 경로

### 4.1 HTTP (`server.py`)

**[코드]** 지식 베이스 선택 규칙(공유 지식 베이스 설계 §5.2, CT23, `_get_theme_ctx`):

1. **네임스페이스 라우트**: 경로만 선택한다. `/kb/{number}/…`는 그 공유 지식 베이스를 고정한다(`pinned_kb_id`, 네임스페이스 계정 없음, 모드와 무관). 고정이 없으면 네임스페이스 계정의 개인 지식 베이스(`personal_kb_for`)다. 개인 지식 베이스의 번호로 `/kb/{number}/…`를 요청하면 읽을 수 있을 때 그 계정 경로(`/@{u}/…`, 싱글 모드 `/…`)로 308, 아니면 404다. 옛 링크 `/themes/{number}/…`·`/@{u}/themes/{number}/…`는 공유면 `/kb/{number}/…`로, 개인이면 그 계정 경로로 308이다(설계 §7).
2. **네임스페이스가 없는 라우트**(`any`·`system` 표면, 예: `/p/presentation`, `/api/me/data-bundles`, MCP 읽기 도구): 매개변수 `kb`(번호)가 필수다. 없으면 400 `kb_required`. `KnowledgeBaseManager.get_kb_by_reference`는 번호만 받는다(내부 ID·레이블·빈 값은 404).
3. **적재**(`/ingest`, `/ingest-stream`, 피드·타임라인 항목 적재): 자격증명 계정의 개인 지식 베이스에만 쓴다(`_get_personal_kb_ctx`). 다른 지식 베이스를 고정한 경로는 403 `kb_ingest_personal_only`, 다른 계정의 네임스페이스는 404다.
4. **옛 이름**: 쿼리·본문 `theme`, `X-Claire-Theme` 헤더, 내부 ID 참조는 모르는 필드처럼 무시한다. `Deprecation`·`Sunset` 헤더도 없다. 문서를 다른 지식 베이스에서 찾아 대상을 바꾸는 폴백(`/share`, `/document`, 재생성, 프레젠테이션, 배급 원본)은 없다. 공유 토큰(`/p?s=`, `/p/presentation?s=`)은 토큰이 자기 지식 베이스를 정한다.
5. 개별 테마 FQDN 설정은 폐지되었다(O5). 기존 FQDN 접근은 `HostRouter`에서 308 정본 경로로 리다이렉트된다.

**[코드] 지식 베이스의 두 종류와 권한(CT-1, 설계 §6.2·§6.5·§11.2)**:

- **개인**(`is_personal=1`, `account_id`=계정, `knowledge_type` NULL): 계정마다 하나(CT1). 공개 또는 비공개. 소유자만 쓴다.
- **공유**(`is_personal=0`, `account_id` NULL, 항상 공개, `knowledge_type`은 `real`·`fictional`): 소유자가 없다. `define_kb(..., knowledge_type=..., account_id=창립자)`가 만들고 창립자는 `created_by_account_id`와 첫 `moderator` 멤버십이 된다(CT8). `POST /kb`는 `knowledge_type`이 없으면 400 `knowledge_type_required`다. 개수 한도는 없다(CT3, 등급의 `max_themes`는 쓰지 않는다). 레이블은 겹쳐도 된다.
- **권한 판정**은 함수 하나다: `claire.kb_access.decide`(§6.2 표 전체, §6.5, §6.6). 서버는 `_kb_decide`·`_require_kb`로만 쓴다. 익명은 404, 권한 없는 계정은 403 `kb_capability_required`다. 숨긴 문서는 공유 지식 베이스에서 `review` 권한자만, 개인 지식 베이스에서 그 계정만 본다.
- **Route capabilities and credential scopes (CT-2, design Sections 6.6 to 6.9)**:
  - Each entry of `api.security.ROUTE_POLICY` declares two values: the access level (the minimum scope of the credential) and the capability in the selected knowledge base. `_request_capability` reads the capability of the route; no handler selects it. `tests/test_credential_scopes.py` has the table of all routes that act on a knowledge base; a route that is not in the table fails the test.
  - Capabilities of a route: `none` (no knowledge base), `read`, `share` (make a share link), `write` (a write into the personal knowledge base of the account of the credential: ingest, re-ingest, publish, dispatch), and the capabilities of design Section 6.2 (`review`, `curate`, `maintain`, `settings`, `remove`, `delete`, and the others).
  - In a personal knowledge base, `read` and `share` stay, a decision about the knowledge base (`settings`, `delete`) needs `settings`, and each other capability is a write of the content. In a shared knowledge base a route with `write` gets 403 `kb_ingest_personal_only` (CT4).
  - Scopes: `owner`, `write`, `readonly`. `write` replaces `collaborator`. In the personal knowledge base `write` reads, ingests, regenerates, and publishes. In a shared knowledge base `write` has only `read`, `contribute_direct`, `contribute_theme`, and `share`, also for a moderator or the admin. `readonly` has `read` and `share`.
  - A credential of an account whose scope is too small gets 403 `{"error": "scope_insufficient", "scope", "required"}` on a route that acts on a knowledge base. Under `/@{u}/` and on account, console, and admin routes the answer stays 404.
  - Regeneration (`/regenerate`, `/regenerate-stream`, `/document/regenerate`): minimum scope `write`, capability `maintain`. Thus a `write` token regenerates in the personal knowledge base, and a reviewer with the scope `owner` regenerates in a shared knowledge base (design Section 13.1 rule 5). `/synthesize` and `/research` write no document: capability `read`, minimum scope `owner`.
  - `share`: this capability is not in the table of design Section 6.2. Each account that reads a shared knowledge base has it (not a banned one); in a personal knowledge base only its account has it. In single mode a reader of the public knowledge base of the admin, an anonymous one included, has it (the rule from before this design).
  - Step-up (design Section 6.8): a route with `step_up=True` needs a session with a recent sign-in. A bearer credential gets 403 `step_up_requires_session`; an old session gets 401 `reauth_required`. Routes with it: `DELETE /kb/{number}/` and the admin moderator routes. `PATCH /kb/{number}/members/{account}` asks for it in the handler, only for a role change, because its other actions (approve, ban, remove) must be fast (Section 6.8 rule 2).
  - Syndication dispatch (`POST /api/syndication/dispatch`): the copy is an ingest, so its target is the personal knowledge base of the caller; a shared knowledge base as the target gets 403 `kb_ingest_personal_only`. The source needs `read`. Federation publish and retract take only documents of the personal knowledge base of the caller.
- **프로바이더(§13.1 규칙 5)**: 공유 지식 베이스에서 사람이 시작한 작업(재생성, 프레젠테이션, 합성 등)은 **요청자 계정의 프로바이더 컨텍스트**로 실행한다(`IngestServicePool.get_service(..., acting_account_id=)`, `_provider_account_of`). 서버의 프로바이더를 쓰지 않는다. 재임베딩 필요 표시는 서버 임베딩과 비교한다(CT24).
- **워커(CT10)**: `_theme_services_for_cycle`은 공유 지식 베이스를 돌지 않는다(watch·refresh·expand·recover는 개인 지식 베이스만).
- **목록**: `list_account_kbs`는 계정이 소유한 것(개인), `list_selectable_kbs`는 선택기용(개인 + 모든 공유)이다.
- **콘솔·텔레그램**: `/settings/*`의 `?kb=`와 계정 백업·데이터 번들은 개인 지식 베이스만 받는다. 텔레그램의 테마 선택은 소유한 것만 보므로 공유 지식 베이스는 대상이 아니다(정리는 CT-8). 연합 발행(`POST /api/federation/publish`)도 개인 지식 베이스에서만 한다.

**[코드]** `_theme_accessible`은 `_kb_decide(request, kb, <the capability of the route>) == "allow"`다. A personal knowledge base that the requester cannot use gives 404 from `_get_theme_ctx`.

| Requester | Personal knowledge base that it reads |
| :--- | :--- |
| Anonymous, or another account | A public one |
| The account of the knowledge base (`owner`, `write`, `readonly`) | Its own, private included |

### 4.5 Collections: `GET /kb`, `GET /@{u}/kb`, `GET /themes` (CT-2, design Section 7)

- **[코드] `/kb`**: the shared knowledge bases of the server, newest first, for each reader. `?joined=true` selects those where the requester is an active member. `?type=real` or `?type=fictional` selects one knowledge type. An unknown value of `joined` or `type` gets 400 (`invalid_joined`, `invalid_type`).
- **[코드] `/@{u}/kb`**: the shared knowledge bases where `u` is an active member (CT9). An HTML request gets the workspace page of `u`.
- **[코드] Response**: the root has `knowledge_bases` (the items), `personal_kb`, and `multi_theme`. `personal_kb` is the personal knowledge base of the requester at `/kb`, and the one of `u` at `/@{u}/kb` when the requester can read it; else `null`. An item has the fields of `KnowledgeBaseInfo.to_dict()`, `stats`, `url` (`/kb/{number}/`), and `role` (the role of the requester at `/kb`, of `u` at `/@{u}/kb`). `legacy_hosts` and `reembed_required` go only to a requester with the capability `settings`. With `CLAIRE_MULTI_THEME=0` the collection has no shared knowledge base.
- **[코드] `/themes`**: the collection of themes (table `themes`, design Section 8), with `?type=`. An item has `number`, `url` (`/themes/{number}`), `label`, `description`, `knowledge_type`, `state`, and `knowledge_bases` (the count). The page of one theme is unit CT-5.
- **[코드] Old links**: `GET /@{u}/themes` gets 308 to `/@{u}/kb`. The old routes `POST`, `PATCH`, and `DELETE /themes` are removed; the lifecycle routes are `POST /kb`, `PATCH /kb/{number}/`, and `DELETE /kb/{number}/` (§4.7).
- **[코드] Surface**: `/kb` and `/themes` have the route surface `collection` (`api.security._collection`): at the root there is no namespace account; under `/@{u}/` the namespace is `u`; a pinned knowledge base (`/kb/{number}/kb`) gets 404.
- **[코드] WebUI**: the selector of the workspace keeps the personal knowledge base from the inline list and reads the shared ones from `/kb` (`app.js` `fetchThemes`). The knowledge base manager of the console reads `/kb` (admin) or `/kb?joined=true` (`manage_panels.js` `renderThemeManager`).

- **2단계 컬렉션**: 루트(`OrderedCollection`)와 페이지(`OrderedCollectionPage`)로 제공된다(설계 §7.4).
- **커서 페이지네이션**: `max_id`, `min_id`, `since_id` 중 최대 1개와 `limit`(기본 20, 최대 80)를 지원한다. 둘 이상 커서 지정 시 400으로 거부된다.
- **캐시 규칙**: 익명 `public, max-age=60`, 인증 `private, no-cache`, 강한 ETag 및 조건부 요청(304). `Vary: Cookie, Authorization, Sec-Fetch-Dest, Accept`.
- **Root response**: see the list above. The old fields `themes`, `default_kb_id`, and `default_theme_number` are removed.

### 4.2 CLI

**[코드]** `get_effective_settings`의 동작은 다음과 같다.
- `--theme`이 있으면 엄격하게 조회한다. 없는 테마면 종료 코드 2로 실패한다.
- 단일 테마 모드에서는 경고를 출력하고 `--theme`을 무시한다.
- `--theme`이 없고 멀티 테마 모드면, `target`·`doc_id`·`token`으로 문서를 찾아 그 문서의 테마로 정한다(`resolve_document_targets`의 첫 결과).

### 4.3 공용 파일 상태와 테마 경계

**[코드]** `/stats`와 `/ingest/active`는 다음과 같이 동작한다.
- `active_jobs`를 `_visible_active_jobs`로 거른다. 요청자가 `_theme_accessible`로 접근할 수 있는 테마의 작업만 남는다.
- 대표 작업(`active_ingest`)은 거른 목록 안에서만 고른다(`get_primary_active_job(..., jobs=...)`). 이때 전 테마 공용인 레거시 `active_ingest.json`으로 폴백하지 않는다.
- 작업 조회 대상은 선택 규칙(§4.1)이 정한 지식 베이스다(`_job_kb_id`).
- 이 처리가 없으면 공개 테마 FQDN에서 비공개 테마의 적재 URL과 제목이 보인다(`tests/test_active_jobs_theme_isolation.py`).

### 4.4 적재 서비스와 워커

- **[코드]** `IngestServicePool`은 테마마다 `IngestService`를 하나씩 캐시하고, LLM 프로바이더 인스턴스는 공유한다.
- **[코드]** `recover-loop`, `refresh-loop`, `expand-loop`와 `app health`, `migrate`는 `active_kb_settings()`로 테마 목록을 매번 다시 읽는다.
- **[코드]** `cmd_migrate`는 멀티 테마 모드에서 테마마다 `init_db`와 `require_current_schema`를 실행한다. 대상 파일은 모두 같은 단일 DB다.

### 4.6 콘솔 화면의 테마 관리와 `?kb=`

- **[코드] 테마 관리 화면**: 멤버는 `/settings/themes`, 관리자는 `/admin/themes`에서 테마를 관리한다(`src/claire/static/js/manage_panels.js`의 `renderThemeManager`: 목록은 `GET /kb`, 변경은 `POST /kb`·`PATCH /kb/{number}/`·`DELETE /kb/{number}/`). 관리자가 `/settings/themes`를 열면 `/admin/themes`로 302된다. 루트 `/` 상세 메뉴에는 테마 관리 패널이 없다. 동작의 정본은 [`ACCOUNTS.md`](ACCOUNTS.md) §5.9다.
- **[코드] 관리자 화면의 경계**: `/admin/themes`는 관리자 계정(`account_id = 1`)의 테마만 관리하고, 서버 전체는 `COUNT(*)` 집계(`total_themes`, `admin_themes`, `purged`가 아닌 계정 수)만 보인다. 멤버 테마의 레이블·설명·본문은 읽지 않는다.
- **[코드] No slot count**: the tier field `max_themes` is retired (design Section 10.1). `ServerTier.from_dict` ignores the key in a stored tier or in an old request; the console has no slot count.
- **[코드] 콘솔의 `?kb=`**: `/admin/*`·`/settings/*`(`src/claire/api/accounts_routes/console.py`)는 `?kb=`(번호)를 `get_kb_by_reference`로 찾고 호출자 계정의 것인지 검사한다. 빈 값, 없는 번호, 다른 계정의 지식 베이스는 404다. 쿼리가 없으면 콘솔 주인 계정의 개인 지식 베이스다(`/settings/*`는 그 계정의 콘솔이다).
- **[코드] 옛 `/syndication/preferences?kb=`**: `?kb=`가 있으면 `_get_theme_ctx`로 검사하고 호출자 계정의 것이 아니면 404, 통과하면 `/settings/*` 대상에 공개 참조를 `?kb=`로 붙인다.

---

### 4.7 Lifecycle, membership, and moderation of a shared knowledge base (CT-3, design Section 10)

- **[코드] Store**: `store/kb_collab.py` has `add_membership`, `update_membership`, `delete_membership`, `active_moderators` (an active membership with the role `moderator` of an account whose status is `active`), `members_with_accounts`, and `log_moderation`. `KnowledgeBaseManager.update_kb` takes the settings of a shared knowledge base: `join_policy`, `knowledge_type` (a new type takes the knowledge base out of its themes), `seed_entities` (`validate_seed_entities`: at most 20 names), and `state` (`archived` sets `archived_at`).
- **[코드] Routes**: `POST /kb`, `PATCH /` and `DELETE /` under the path of the knowledge base, `POST` and `DELETE /membership`, `GET /members`, `PATCH /members/{account}`, and `PUT` and `DELETE /api/admin/kb/{number}/moderators/{account}`. The contract is in [`API_HTTP_CONTRACT.md`](API_HTTP_CONTRACT.md). `_select_kb` selects the knowledge base of the path and checks the capability of the route; `_get_theme_ctx` adds the settings and the ingest service.
- **[코드] Roles**: a join makes the role `member`. A moderator with a recent sign-in gives and removes `collaborator`. Only the admin gives and removes `moderator`. A moderator cannot change another moderator. The last active moderator cannot leave. A suspended account keeps its role in storage, but its credential is not valid, and it does not count as an active moderator.
- **[코드] Archive**: `kb_access.decide(..., archived=True)` refuses the capabilities of `ARCHIVED_BLOCKED`; the join route refuses with 403 `kb_archived`.
- **[코드] Log**: each decision about a member, each role change, and each settings change writes `kb_moderation_log` (actions `approve`, `ban`, `remove`, `role_collaborator`, `role_member`, `moderator_give`, `moderator_remove`, `leave`, `settings`). The admin actions on moderators and the delete also write `admin_audit`.
- **[미구현]** The console pages for members, roles, and the log are unit CT-9. Mutual collaborators of the founder do not become collaborators yet (unit CT-13).

### 4.8 Contributions to a shared knowledge base (CT-4, design Sections 9.1, 9.4 to 9.6)

- **[코드] Module**: `src/claire/contributions.py`. A contribution is a copy of a document of a personal knowledge base in a shared knowledge base K. The copy has its own document ID, its own files, and its own graph in K. `store/kb_collab.py` reads and writes `kb_contributions` and `kb_contribution_revisions`.
- **[코드] Direct contribution** (`contribute_direct`, one transaction): the checks are a duplicate in K (the same `canonical_url` or `content_hash`: 409 `contribution_duplicate` with the URL), a tombstone in K (409 `contribution_tombstoned`), and a completed extraction of the source (409 `document_not_extracted`). Then `copy_document` copies the `documents` row and the latest `extractions` row with a new ID (hidden, no watch, without the keys of `meta` that are results of the source knowledge base), and the contribution (origin `direct`, state `merging`, root = the source document) and its job are added. A contributor with the capability `review` needs no approval: `approved_at` is set at once. Else `approved_at` is NULL and the contributor can withdraw.
- **[코드] Merge job** (`run_merge_job`, `build_graph_from_extraction`): the job gives the copy its files (`copy_document_files`: local images, the text artifact, attachments; the uploaded source file of `raw_inbox` is not copied), and builds the graph of K from the stored extraction (`ExtractionResult` JSON in `extractions.raw_response`). It makes **no LLM call** (CT24): no new extraction, no entity judge (`resolve_or_create(judge_fn=None)`: name, alias, and the automatic threshold only), and no relation judge. Embeddings use the provider of the server and the fixed embedding model of K; when the provider does not serve that model, the contribution gets `failed` with the code `embedding_model_unavailable`. The resolution log goes into the `meta` of the copy, so a purge can undo its merges. Success: `documents.hidden = 0`, state `visible`. Failure: state `failed` with a code in `decision_reason`; `retry_merge` adds a new job.
- **[코드] Jobs**: a merge job is a row of `jobs` with `source_type = 'contribution_merge'` and the payload `{"contribution", "revision"?}`. States: `pending`, `running`, `done`, `error`. `claim_job` takes a job for one process; a job that is `running` for more than 15 minutes becomes `pending` again. The contribution routes run the jobs of K in the request. Each service job of `IngestService` runs the jobs that wait when it ends (`run_waiting_merge_jobs`), with the settings of the server, so the worker loops also run them.
- **[코드] Withdrawal and review** (`withdraw`, `approve`, `reject`, `set_hidden`, `set_updates_frozen`, `remove`): the contributor withdraws while `approved_at IS NULL` (the copy and the row are deleted, no tombstone); after the approval 409 `contribution_approved`. An approval is final; for a pending contribution it starts the merge job. A reject is for a pending contribution and needs a reason code. Hide and unhide change `documents.hidden` and the state; `POST /document/hide` on a copy does the same. Remove needs a reason code, purges the copy with a tombstone, and sets the state `removed`. Reason codes: `off_subject`, `wrong_knowledge_type`, `duplicate`, `low_quality`, `spam`, `other`. Each review action writes `kb_moderation_log`. A purge of a copy from another entry point (`POST /document/purge` in K) sets its contribution to `removed` (`purge_document_cascade`).
- **[코드] Revisions** (`start_revisions`, design Section 9.5): `ingest.pipeline.extract_resolve_store` calls `_start_contribution_revisions` after each successful extraction. When the document is the root of contributions and the `content_hash` of a copy differs from the root (`documents.content_hash` is the hash of the fetched text; regenerate does not change it), the new version and its extraction are copied into K without graph. For a visible or hidden contribution the merge job builds the graph of the new copy, points the contribution to it, purges the old copy, and records a row in `kb_contribution_revisions`; there is no approval, and the state stays. For a pending contribution the new version replaces the pending copy. A contribution with stopped updates (`updates_frozen`) and a closed one keep their version. The revisions do not read the visibility of the personal knowledge base (CT26). When the root is deleted, the copies stay.
- **[미구현]** Contribution to a theme and submissions (CT-6), curation and fork (CT-7), the reputation rule for pending contributions (CT-11), and the console pages (CT-9).

## 5. 테마 수명주기

| 동작 | 코드 | 범위 |
| :--- | :--- | :--- |
| 정의 | `define_kb` | `account_id` 귀속, `themes` 행 추가, JSON 갱신. `(account_id, label)` 범위 레이블 중복 검사 |
| 수정 | `update_kb` | 동일 계정 내 레이블 중복 검사. 비어 있지 않은 `fqdn`은 `ValueError`(테마 FQDN 폐지, HTTP는 400)이고, 기존 `themes.fqdn` 값은 `legacy_hosts`의 원본이라 지우지 않는다. GA ID는 `validate_ga_measurement_id`로 검증한다(빈 값은 해제). `theme.is_personal`이면 `default_focus=""` 강제. 공개 여부가 바뀌면 `visibility_epoch`를 올린다 |
| 리셋 | `reset_kb` | `reset_graph(kb_id)`로 엔티티·관계·임베딩·FTS를 지우고, §2의 테이블에서 해당 `kb_id` 행을 삭제. 메타데이터와 ID는 보존 |
| 삭제 | `delete_kb(purge=False)` | `theme.is_personal`이면 삭제 거부(`ValueError`). `themes` 행만 삭제. 데이터 행은 남는다 |
| 삭제 + 소각 | `delete_kb(purge=True)` | `theme.is_personal`이면 삭제 거부(`ValueError`). 리셋과 같은 범위를 지운 뒤 `themes` 행을 삭제 |

**[결정]** CLI `theme reset`과 `theme delete`는 기본이 dry-run이다. `--apply`가 있어야 실행하며, 데이터를 지우는 경우에는 확인을 받는다(PRINCIPLES D2·D3).

---

## 6. 알려진 한계와 미검증

- **[코드] 레거시 병합 실패를 삼킨다.** `reload()`는 `migrate_multi_db_to_single_db`의 예외를 `log.warning`으로만 남기고 계속한다. 그러면 `claire migrate`가 성공으로 끝나고 서비스도 시작되지만, 옛 테마 데이터 일부가 빠진 상태가 된다(PRINCIPLES C2·E3 위반).
  - 단순히 예외를 다시 던지게 고치면 재시도가 안전하지 않다. 병합은 DB 파일 단위로 커밋하고, 자동 증가 키 테이블은 `id`를 빼고 복사한다. 그래서 부분 실패 뒤에 다시 실행하면 그 테이블의 행이 중복될 수 있다.
- **[코드] 삭제(`purge=False`) 뒤에 남은 데이터 행에는 접근할 수 없다.** 해당 `kb_id`의 행이 DB에 남지만 어느 테마에서도 보이지 않는다. 정리하는 명령은 확인하지 못했다.
- **[미검증]** `import-graph --theme`은 테마의 `db_path`(현재는 단일 DB)를 대상으로 한다. 가져온 행에 `kb_id`가 붙는지는 확인하지 않았다.
- **[미검증]** 텔레그램 `/theme`과 MCP 도구의 테마 지정에서 비공개 테마 접근이 어떻게 검사되는지는 이 문서 작성 시 확인하지 않았다.

---

## 7. Default focus of a theme (removed)

- **[code]** The theme default focus is removed with the ingest focus seal (CT28, [`DATA_LIFECYCLE.md`](DATA_LIFECYCLE.md) §4). `KnowledgeBaseInfo` has no `default_focus`, theme create and update ignore the field, and `CLAIRE_THEME_DEFAULT_FOCUS` is not a setting. The column `themes.default_focus` stays as data.
- **[code]** A deleted theme gives `KnowledgeBaseNotFound`. The service does not use theme 0 in its place.

## 8. Public site and analytics of a theme

- **[code]** A theme has a GA4 measurement ID (`KnowledgeBaseInfo.ga_measurement_id`). A page with a theme context uses only this ID; there is no fallback to `CLAIRE_GA_MEASUREMENT_ID`. The tag also sends `kb_id` and `theme_label`. Details: [`PUBLIC_SITE.md`](PUBLIC_SITE.md) §6.
- **[code]** In multi-theme mode, `/sitemap.xml` lists the documents of all public themes. Details: [`PUBLIC_SITE.md`](PUBLIC_SITE.md) §4.
