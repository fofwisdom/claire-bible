# 구현 상태: 알려진 제약 · 보류 결정 · 테스트 기준선

기준일: 2026-11-13 · 대상 독자: 기능을 바꾸는 에이전트 · 근거 태그: [`PROVIDERS.md`](PROVIDERS.md)와 같다(`[코드]`, `[실측]`, `[결정]`, `[미검증]`)

이 문서는 **치명적이지 않은** 구현 상태만 적는다. 버그는 아니지만 알고 시작해야 하는 제약, 소유자가 보류한 결정, 이미 실패하는 테스트가 대상이다. 작업 전에 해당 절만 읽으면 따로 분석하지 않고 착수할 수 있게 쓴다.

- **보안 문제는 적지 않는다.** 소유자에게 직접 보고한다.
- 동작 명세는 각 정본 문서에 있다. 이 문서는 그 정본에 없는 "현재 한계"만 담는다.
- 항목을 해결하면 그 커밋에서 항목을 지운다. 새 제약이 생기면 그 커밋에서 추가한다.

---

## 1. 홈 화면 `목록` (좌측 목록 창) · 문맥 패널

홈과 본문 뷰는 문서 목록, 그래프 뷰는 엔티티 목록을 기본으로 보이고 `#listswitch`로 전환한다(용어: [`GLOSSARY.md`](GLOSSARY.md) `목록`). 두 목록 모두 31개씩 보이고 스크롤하면 더 보인다. 우측 문맥 패널(`aside#detailpane`)은 선택이 있거나 그래프 뷰일 때만 열린다.

### 1.1 코드 위치

| 대상 | 위치 |
| :--- | :--- |
| 홈 화면 (개요) | `renderHome()`, `loadHomeStats()`, `resolveHomeView()`, `enterHome()` (`src/claire/static/js/app.js`), `#homewrap` (`src/claire/templates/index.html`) |
| 목록 전환 및 접기/펼치기 | `listMode()`, `setListOverride()`, `syncListPane()`, `toggleListPane()`, 저장소 키 `claireListOpen` (`src/claire/static/js/app.js`), 호출: `setCenterView()` (`src/claire/static/js/reader.js`), `#listswitch` · `#listtoggle` |
| 문맥 패널 (온디맨드) | `hasContext()`, `isContextOpen()`, `setContextUserOpen()`, `toggleContextPanel()`, `closeContextPanel()`, `syncWorkspaceLayout()`, 저장소 키 `claireContextOpen` (`src/claire/static/js/app.js`), `#detailpane` · `#ctxbtn` · `#detailclose` |
| 좁은 화면 하단 바·시트 (≤720px) | `openDocDrawer()`, `closeDocDrawer()`, `openDrawer()`, `closeDrawer()`, `closeAllDrawers()` (`src/claire/static/js/app.js`), `#botnav` (`src/claire/templates/index.html`, `src/claire/static/css/workspace.css`) |
| 31개 창 표시와 무한 스크롤 | `createWindowedList()`, 상수 `LIST_PAGE` |
| 문서 목록 데이터 | `docMainPager` · `docFilterPager`, `loadDocPage()`, `refreshDocs()`, `ensureDocLoaded()`, `loadDocsById()` |
| 엔티티 목록 | `renderEntityList()`, 표시 조건 `isNodeListed()` (그래프 `applyView()`와 같은 조건, [`GRAPH_ENGINE.md`](GRAPH_ENGINE.md) §10) |
| 노드 상세 패널 | `renderPanel()`, `srcRowHtml()`, `showNodeInList()`, 요청 순서 `nodeLoadSeq` |
| 서버 페이지 | `documents_page()` (`src/claire/store/queries.py`), 커서 `decode_documents_cursor()` (`src/claire/store/db.py`), 계약: [`API_HTTP_CONTRACT.md`](API_HTTP_CONTRACT.md) `GET /documents` |
| 테스트 | `tests/test_documents_paging.py`, `tests/test_graphview.py`, `e2e/workspace.spec.js`의 `document list with server pages`, `node detail panel and node request order` |

### 1.2 문서 목록의 제약 [코드]

1. 즐겨찾기 목록과 숨김 목록은 각각 최대 300개다(요청 하나씩, `pinned=1`, `hidden=1`).
2. 딥링크(`?doc=`)는 요청 한 번으로 최대 300개를 더 받는다. 목록 앞 331개 밖의 문서는 리더에서 열리지만, 사용자가 그 위치까지 스크롤하기 전에는 목록에 행이 보이지 않는다.
3. 필터 문자열은 입력이 300ms 멈춘 뒤 서버로 보낸다(`DOC_FILTER_DELAY`). 서버의 `LIKE`는 ASCII 문자만 대소문자를 무시한다. 서버는 저장된 요약을 비교하고, 목록 행은 정리한 요약을 보인다. 그래서 마크업 문자에서 결과가 다를 수 있다.
4. 백그라운드 새로고침은 각 목록의 첫 페이지만 다시 받는다. 뒤 페이지의 변경(제목, 읽음 표시, 삭제)은 페이지나 지식 테마를 다시 불러와야 보인다. 그래서 `#listtitle`의 전체 수가 받은 행과 삭제 건수만큼 다를 수 있다.
5. 그래프 뷰 문서 선택기의 이전/다음은 클라이언트가 아는 문서 안에서만 움직인다.
6. 클라이언트는 식별자를 JavaScript 문자열 순서로, 서버는 SQLite 바이트 순서로 비교한다. 애플리케이션이 만드는 식별자(ASCII)에서는 두 순서가 같다.

### 1.3 엔티티 목록과 상세 패널의 제약 [코드]

1. 타입별 숨김 칩은 없다. 범례는 타입을 색으로만 보이고 타입 토글이 없다.
2. 연결 수 막대의 최대값은 클라이언트 노드 집합에서 계산한다(`stats.max_degree`와 같은 값).
3. 출처 문서 행의 출처 유형 배지는 원래 값(`web`, `file`)을 보인다. 번역은 i18n 단위 IP2-2의 범위다.
4. 출처 문서 요약은 양쪽 정렬이다. CJK 문장에서 간격이 어절 사이에만 몰리지 않도록 `.srcsum`만 `word-break:normal`이다(본문 기본값은 `keep-all`).

### 1.4 보류 결정 [결정]

| 항목 | 결정 (2026-11-13) |
| :--- | :--- |
| `documents`의 `(theme_id, fetched_at DESC, id DESC)` 인덱스 | 넣지 않는다. 여러 사용자의 목록을 함께 보고 쓰는 프로젝트의 상세 설계에서 스키마 변경과 함께 정한다([`SCHEMA_VERSIONING.md`](SCHEMA_VERSIONING.md)). 지금은 요청마다 일치 행을 정렬한다. |

---

## 2. 전역 비활성 기능 [결정]

발표(프레젠테이션) 토글과 의사결정 스트림의 30개 페이지는 전역으로 꺼져 있고, 없어질 수 있다. 두 기능의 E2E 테스트는 2026-11-13에 지웠다. 기능을 다시 개선할 때 테스트를 다시 추가한다.

---

## 3. E2E 테스트 기준선 [실측]

2026-11-13 기준 `e2e/workspace.spec.js` 전체 실행에서 아래 테스트가 이미 실패한다. 이 실패는 다른 작업의 결과가 아니다. 새 실패만 조사한다.

| 테스트 | 상태 |
| :--- | :--- |
| 모바일 테스트 5개 (`mobile primary view …`, `mobile history back navigation …`, `mobile reader can be opened and closed …`, `mobile reader extends to bottom …`, `mobile view features …`) | `waitForClaire`에서 멈춘다. 원인 [미검증] |
| `telegram bot ingest dynamically suspends active graph …` | 실패. 원인 [미검증] |
| `Unified Ingest Cockpit renders 5-stage horizontal milestone stepper …` | 간헐 실패 |
| `accessing Claire Bible during active ingest boots directly into heatmap …`, `provider management lives in the admin console …` | 전체 실행에서만 간헐 실패하고 단독 실행은 통과한다. 앞 테스트의 상태가 넘어오는 것으로 보인다 [미검증] |

- 실행: `cd e2e && npx playwright test`. Playwright가 `e2e/seed_db.py`로 시드 DB를 만들고 포트 8766에 서버를 띄운다(시작 제한 30초).
- 전체 `pytest`는 모두 통과한다. 시간이 약 9분 걸리고, 임시 파일이 `/tmp/pytest-of-<user>`에 실행마다 1GB 가까이 쌓인다. `/tmp`가 작은 환경에서는 `TMPDIR`과 `--basetemp`를 다른 볼륨으로 준다(`--basetemp`는 `TMPDIR` 안에 두어야 asciidoctor 테스트가 통과한다).
