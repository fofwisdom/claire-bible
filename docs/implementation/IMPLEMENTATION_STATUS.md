# 구현 상태: 알려진 제약 · 보류 결정 · 테스트 기준선

기준일: 2026-11-15 · 대상 독자: 기능을 바꾸는 에이전트 · 근거 태그: [`PROVIDERS.md`](PROVIDERS.md)와 같다(`[코드]`, `[실측]`, `[결정]`, `[미검증]`)

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
| 홈 화면 (개요) | `renderHome()`, `renderHomeMap()`, `drawHomeMiniMap()`, `renderHomeDecisions()`, `renderHomeMaintenance()`, `loadHomeStats()`, `resolveHomeView()`, `enterHome()` (`src/claire/static/js/app.js`), `#homewrap` (`src/claire/templates/index.html`) |
| 홈 단계 띠 (HW-8) | `renderHomeFlow()`, `paintHomeFlow()` (`src/claire/static/js/app.js`), `#home-flow` (`src/claire/templates/index.html`), `.home-flow` (`src/claire/static/css/workspace.css`). owner에게만 보이고 `GET /api/feeds`, `GET /api/federation/following?limit=1`, `GET /api/federation/status`, `GET /api/federation/timeline?limit=1`, `GET /stats`의 건수를 쓴다. 실패한 경로의 건수는 보이지 않는다 |
| 목록 전환 및 접기/펼치기 | `listMode()`, `setListOverride()`, `syncListPane()`, `toggleListPane()`, 저장소 키 `claireListOpen` (`src/claire/static/js/app.js`), 호출: `setCenterView()` (`src/claire/static/js/reader.js`), `#listswitch` · `#listtoggle` |
| 문맥 패널 (온디맨드) | `hasContext()`, `isContextOpen()`, `setContextUserOpen()`, `toggleContextPanel()`, `closeContextPanel()`, `syncWorkspaceLayout()`, 저장소 키 `claireContextOpen` (`src/claire/static/js/app.js`), `#detailpane` · `#ctxbtn` · `#detailclose` |
| 좁은 화면 하단 바·시트 (≤720px) | `openDocDrawer()`, `closeDocDrawer()`, `openDrawer()`, `closeDrawer()`, `closeAllDrawers()` (`src/claire/static/js/app.js`), `#botnav` (`src/claire/templates/index.html`, `src/claire/static/css/workspace.css`) |
| 31개 창 표시와 무한 스크롤 | `createWindowedList()`, 상수 `LIST_PAGE` |
| 문서 목록 데이터 | `docMainPager` · `docFilterPager`, `loadDocPage()`, `refreshDocs()`, `ensureDocLoaded()`, `loadDocsById()` |
| 엔티티 목록 | `renderEntityList()`, 표시 조건 `isNodeListed()` (그래프 `applyView()`와 같은 조건, [`GRAPH_ENGINE.md`](GRAPH_ENGINE.md) §10) |
| 노드 상세 패널 | `renderPanel()`, `srcRowHtml()`, `showNodeInList()`, 요청 순서 `nodeLoadSeq` |
| 문서 문맥 3탭 및 출처·대조 단계 배지 (HW-3) | `renderDocPanel()`, `renderDocLinksTab()`, `renderDocEntitiesTab()`, `renderDocSourceTab()`, `setDocCtxTab()`, `onDocCtxTabKeydown()`, `docSourceBadgeHtml()`, `resolutionStageBadge()` (`src/claire/static/js/app.js`), `docMetaHtml()`, `renderReader()` (`src/claire/static/js/reader.js`), `.ctx-tabs`, `.src-badge`, `.stage-*` (`src/claire/static/css/workspace.css`) |
| 로컬 그래프 (문맥 패널 연결 탭, HW-4) | `renderLocalGraphHtml()`, `setEntityHoverHighlight()`, `clearEntityHoverHighlight()` (`src/claire/static/js/app.js`), `#ctx-local-graph-slot` (`renderDocLinksTab()`), `.ctx-local-graph` (`src/claire/static/css/workspace.css`), 링 1 최대 24개, 링 2 최대 12개, 초과 시 미표시 개수 안내 |
| 리더 엔티티 마크 및 서버 별칭 (HW-5) | `document_detail()` (`src/claire/store/queries.py`의 `nodes[].aliases`), `applyEntityMarks()`, `clearEntityMarks()`, `setReaderEntityMarkHover()`, `clearReaderEntityMarkHover()` (`src/claire/static/js/reader.js`), `::highlight(claire-entity)` (`src/claire/static/css/reader.css`), `#rlegend` (`src/claire/templates/index.html`), 본문 400,000자 초과 또는 후보 300개 초과 시 마크 비활성화 |
| 그래프 인사이트 목록 및 뷰 간 엔티티 상세 (HW-6) | `renderGraphInsights()`, `getGraphInsightLists()`, `deselectEntity()` (`src/claire/static/js/app.js`), `#ctx-insights`, `.ctx-insight-row`, `.backlink` |
| 명령 팔레트 (HW-7) | `ClaireCommandPalette` (`src/claire/static/js/command_palette.js`), `<dialog id="cmdpalette">` · `#palettebtn` (`src/claire/templates/index.html`), `.cmd-palette` (`src/claire/static/css/workspace.css`), `openCommandPaletteFromMenu()` (`src/claire/static/js/app.js`) |
| 서버 페이지 | `documents_page()` (`src/claire/store/queries.py`), 커서 `decode_documents_cursor()` (`src/claire/store/db.py`), 계약: [`API_HTTP_CONTRACT.md`](API_HTTP_CONTRACT.md) `GET /documents` |
| 테스트 | `tests/test_documents_paging.py`, `tests/test_graphview.py`, `e2e/workspace.spec.js`의 `document list with server pages`, `node detail panel and node request order`, `home view (HW-1, HW-2)`, `document context panel (HW-3)`, `command palette (HW-7)` |

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

### 1.4 홈 화면과 문맥 패널의 제약 [코드]

1. 적재·재생성·조사·종합의 양식과 결과는 문맥 패널(`#panel`)에 그린다. 이때 `openDetailPane()`이 `contextPinned`를 켜므로 홈 뷰에서도 패널이 열린다.
2. 좁은 화면에서 엔티티를 고르면 문맥 시트가 열린다. 목록 드로어가 열려 있을 때는 열지 않는다(엔티티 목록의 행이 상세를 보인다).
3. 지식 맵의 「브리지」 목록은 문서가 0개인 엔티티를 넣지 않는다.
4. 엔티티 마크의 클릭·호버 판정은 캐럿 위치와 범위 사각형을 함께 본다. 줄 끝 여백이나 문단 옆 여백은 마크로 판정하지 않는다. 마크 후보는 엔티티 이름과 별칭뿐이고 식별자는 후보가 아니다.
5. 상단 바의 `/syndication` 링크(`#syndlink`)와 홈의 단계 띠(`#home-flow`)는 owner에게만 보인다. 띠의 세 링크는 `/syndication?stage=collect|ingest|publish`로 간다. 타임라인·안 읽음·멘션·게시 문서 수는 `/syndication` 단계 막대와 같은 값이다(FP4-5, `FEDERATION.md` 19절).
6. 좁은 화면(≤720px)에서 뷰 전환은 하단 바(`#botnav`)에 있고, 뷰별 검색창은 상단 바의 둘째 줄에 남는다. 하단 바에는 프레젠테이션과 의사결정 스트림 단추가 없다.
7. 본문 상단의 태그는 모두 `.prov.det` 모양이다. 원문이 잘린 문서는 원문 배지 하나만 보이고(글자 수 포함), 잘린 이유는 배지의 툴팁과 `data-trunc`에 있다. 적재 모델 배지(`.model-badge`)는 원문 배지 오른쪽에 있고 값은 `GET /document`의 `extraction_model`(없으면 `extraction_provider`)이다. 공유 페이지는 원문 배지가 없어 기존 절단 태그를 유지한다.

### 1.5 보류 결정 [결정]

| 항목 | 결정 (2026-11-13) |
| :--- | :--- |
| `documents`의 `(theme_id, fetched_at DESC, id DESC)` 인덱스 | 넣지 않는다. 여러 사용자의 목록을 함께 보고 쓰는 프로젝트의 상세 설계에서 스키마 변경과 함께 정한다([`SCHEMA_VERSIONING.md`](SCHEMA_VERSIONING.md)). 지금은 요청마다 일치 행을 정렬한다. |

---

## 2. 전역 비활성 기능 [결정]

발표(프레젠테이션) 토글과 의사결정 스트림의 30개 페이지는 전역으로 꺼져 있고, 없어질 수 있다. 두 기능의 E2E 테스트는 2026-11-13에 지웠다. 기능을 다시 개선할 때 테스트를 다시 추가한다.

---

## 3. E2E 테스트 기준선 [실측]

2026-11-15 기준 `e2e/workspace.spec.js` 전체 실행(47개)에서 아래 테스트가 이미 실패한다. 이 실패는 다른 작업의 결과가 아니다. 새 실패만 조사한다.

| 테스트 | 상태 |
| :--- | :--- |
| `shared document page allows scrolling …` | 실패. 헤딩 마커가 5개 이상이어야 하는데 2개다. 홈 개편 전 커밋(`60fe8f87`)에서도 같은 결과다. 원인 [미검증] |
| `telegram bot ingest dynamically suspends active graph …` | 실패. 원인 [미검증] |
| `Unified Ingest Cockpit renders 5-stage horizontal milestone stepper …` | 간헐 실패 |
| `accessing Claire Bible during active ingest boots directly into heatmap …`, `provider management lives in the admin console …` | 전체 실행에서만 간헐 실패하고 단독 실행은 통과한다. 앞 테스트의 상태가 넘어오는 것으로 보인다 [미검증] |

- 모바일 테스트 5개(`mobile primary view …` 등)는 하단 바(`#botnav`) 기준으로 고쳤고 통과한다. 좁은 화면으로 시작하면 첫 화면이 본문 뷰다(`initialCenterView()`).
- 시드 DB의 로그인 토큰은 한 번만 쓸 수 있다. owner 세션이 필요한 테스트는 `e2e/seed_db.py`에 자기 토큰을 둔다.
- 실행: `cd e2e && npx playwright test`. Playwright가 `e2e/seed_db.py`로 시드 DB를 만들고 포트 8766에 서버를 띄운다(시작 제한 30초).
- 전체 `pytest`는 모두 통과한다. 시간이 약 9분 걸리고, 임시 파일이 `/tmp/pytest-of-<user>`에 실행마다 1GB 가까이 쌓인다. `/tmp`가 작은 환경에서는 `TMPDIR`과 `--basetemp`를 다른 볼륨으로 준다(`--basetemp`는 `TMPDIR` 안에 두어야 asciidoctor 테스트가 통과한다).
