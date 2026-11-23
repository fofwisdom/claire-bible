# 구현 상태: 알려진 제약 · 보류 결정 · 테스트 기준선

기준일: 2026-11-16 (§4는 기준 커밋 d15ae8e0) · 대상 독자: 기능을 바꾸는 에이전트 · 근거 태그: [`PROVIDERS.md`](PROVIDERS.md)와 같다(`[코드]`, `[실측]`, `[결정]`, `[미검증]`)

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
| 홈 화면 (개요) | `renderHome()`, `renderHomeMap()`, `drawHomeMiniMap()`, `renderHomeDecisions()`, `renderHomeMaintenance()`, `loadHomeStats()`, `resolveHomeView()`, `enterHome()` (`src/bible/static/js/app.js`), `#homewrap` (`src/bible/templates/index.html`) |
| 홈 단계 띠 (HW-8) | `renderHomeFlow()`, `paintHomeFlow()` (`src/bible/static/js/app.js`), `#home-flow` (`src/bible/templates/index.html`), `.home-flow` (`src/bible/static/css/workspace.css`). owner에게만 보이고 `GET /api/feeds`, `GET /api/federation/following?limit=1`, `GET /api/federation/status`, `GET /api/federation/timeline?limit=1`, `GET /stats`의 건수를 쓴다. 실패한 경로의 건수는 보이지 않는다 |
| 목록 전환 및 접기/펼치기 | `listMode()`, `setListOverride()`, `syncListPane()`, `toggleListPane()`, 저장소 키 `claireListOpen` (`src/bible/static/js/app.js`), 호출: `setCenterView()` (`src/bible/static/js/reader.js`), `#listswitch` · `#listtoggle` |
| 문맥 패널 (온디맨드) | `hasContext()`, `isContextOpen()`, `setContextUserOpen()`, `openContextPanel()`, `closeContextPanel()`, `contextOpenButton()`, `syncWorkspaceLayout()`, 저장소 키 `claireContextOpen` (`src/bible/static/js/app.js`), `#detailpane` · `#detailclose` (유일한 닫기) · 열기 단추 `#rctxbtn` · `#gctxbtn` · `#botnav-context` |
| 좁은 화면 하단 바·시트 (≤720px) | `openDocDrawer()`, `closeDocDrawer()`, `openDrawer()`, `closeDrawer()`, `closeAllDrawers()` (`src/bible/static/js/app.js`), `#botnav` (`src/bible/templates/index.html`, `src/bible/static/css/workspace.css`) |
| 31개 창 표시와 무한 스크롤 | `createWindowedList()`, 상수 `LIST_PAGE` |
| 문서 목록 데이터 | `docMainPager` · `docFilterPager`, `loadDocPage()`, `refreshDocs()`, `ensureDocLoaded()`, `loadDocsById()` |
| 엔티티 목록 | `renderEntityList()`, 표시 조건 `isNodeListed()` (그래프 `applyView()`와 같은 조건, [`GRAPH_ENGINE.md`](GRAPH_ENGINE.md) §10) |
| 노드 상세 패널 | `renderPanel()`, `srcRowHtml()`, `showNodeInList()`, 요청 순서 `nodeLoadSeq` |
| 문서 문맥 3탭 및 출처·대조 단계 배지 (HW-3) | `renderDocPanel()`, `renderDocLinksTab()`, `renderDocEntitiesTab()`, `renderDocSourceTab()`, `switchDocCtxTab()`, `#panel`의 `keydown` 리스너, 출처 배지는 `docMetaHtml()`, `resolutionStageBadge()` (`src/bible/static/js/app.js`), `docMetaHtml()`, `renderReader()` (`src/bible/static/js/reader.js`), `.ctx-tabs`, `.src-badge`, `.stage-*` (`src/bible/static/css/workspace.css`) |
| 로컬 그래프 (문맥 패널 연결 탭, HW-4) | `renderLocalGraphHtml()`, `setEntityHoverHighlight()`, `clearEntityHoverHighlight()` (`src/bible/static/js/app.js`), `#ctx-local-graph-slot` (`renderDocLinksTab()`), `.ctx-local-graph` (`src/bible/static/css/workspace.css`), 링 1 최대 24개, 링 2 최대 12개, 초과 시 미표시 개수 안내 |
| 리더 엔티티 마크 및 서버 별칭 (HW-5) | `document_detail()` (`src/claire/store/queries.py`의 `nodes[].aliases`), `applyEntityMarks()`, `clearEntityMarks()`, `setReaderEntityMarkHover()`, `clearReaderEntityMarkHover()` (`src/bible/static/js/reader.js`), `::highlight(claire-entity)` (`src/bible/static/css/reader.css`), `#rlegend` (`src/bible/templates/index.html`), 본문 400,000자 초과 또는 후보 300개 초과 시 마크 비활성화 |
| 그래프 인사이트 목록 및 뷰 간 엔티티 상세 (HW-6) | `renderGraphInsights()`, `getGraphInsightLists()`, `deselectEntity()` (`src/bible/static/js/app.js`), `#ctx-insights`, `.ctx-insight-row`, `.backlink` |
| 명령 팔레트 (HW-7) | `ClaireCommandPalette` (`src/bible/static/js/command_palette.js`), `<dialog id="cmdpalette">` · `#palettebtn` (`src/bible/templates/index.html`), `.cmd-palette` (`src/bible/static/css/workspace.css`), `openCommandPaletteFromMenu()` (`src/bible/static/js/app.js`) |
| 선 아이콘 공용 모듈 (HD4를 `/syndication`, `/settings`, `/admin`, 문서 리더 메타 태그, 공유 페이지, 홈 작업 영역에 적용) | `ClaireIcons.svg()`, `ClaireIcons.hydrate()` (`src/bible/static/js/icons.js`), `.lineicon` · `[data-icon]` (`src/bible/static/css/theme.css`), `icon_html()` (`src/claire/api/accounts_routes/common.py`), `ConsoleSectionSpec.icon` (`src/claire/api/accounts_routes/console.py`), 명령 팔레트의 `iconName`. 마크업은 빈 `data-icon` 요소를 쓰고 모듈이 `currentColor` 인라인 SVG로 채운다. 홈 작업 영역의 패널·메뉴·상태 배너(`app.js`, `index.html`)도 같은 규칙을 쓴다. 채운 모양은 `class="filled"`를 쓴다(즐겨찾기 별). 테마 아이콘(사용자 데이터)과 텔레그램 단추 이름은 범위 밖이다. `textContent`로 쓰는 상태 문구와 `<option>`은 아이콘 없이 글자만 둔다. 규칙은 [`PRINCIPLES.md`](../PRINCIPLES.md) G1. 테스트: `e2e/line_icons.spec.js`, `tests/test_ui_line_icons.py` |
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
6. 좁은 화면(≤720px)에서 뷰 전환은 하단 바(`#botnav`)에 있고, 명령 팔레트 단추는 상단 바의 둘째 줄에 남는다. 목록 필터(`#q`)는 목록 서랍 안에 있다. 하단 바에는 프레젠테이션과 의사결정 스트림 단추가 없다.
7. 본문 상단의 태그는 모두 `.prov.det` 모양이다. 원문이 잘린 문서는 원문 배지 하나만 보이고(글자 수 포함), 잘린 이유는 배지의 툴팁과 `data-trunc`에 있다. 적재 모델 배지(`.model-badge`)는 원문 배지 오른쪽에 있고 값은 `GET /document`의 `extraction_model`(없으면 `extraction_provider`)이다. 공유 페이지는 원문 배지가 없어 기존 절단 태그를 유지한다.

### 1.5 Application bar of all pages [code]

Current behavior: [`WEBUI.md`](WEBUI.md) §2. Design principles: [`HOME_OVERVIEW_AND_CONTEXT_PANEL_DESIGN.md`](../design/HOME_OVERVIEW_AND_CONTEXT_PANEL_DESIGN.md).

| Item | Position |
| :--- | :--- |
| Rules of the bar | `src/bible/static/css/appbar.css` (`.appbar`, `.bar-brand-group`, `.appbar-center`, `.appbar-end`, `.bar-icon-btn`, `.bar-ingest-btn`, `.bar-auth`, `.palette-trigger`, theme picker). `src/bible/web/pages.py` puts the file in the bundles of the workspace and of `/syndication`; the console loads it as a link. |
| Workspace | `<header id="bar" class="appbar">` (`src/bible/templates/index.html`) |
| `/syndication` | `<header id="synd-bar" class="appbar">` and the view toolbar `.synd-toolbar` (`src/bible/templates/syndication.html`, `src/bible/static/css/syndication.css`). `renderTopBar()` (`src/bible/static/js/syndication_live.js`) hides the theme picker when there is one theme. |
| Console | `<header class="appbar console-topbar">` and the script of the light and dark switch (`src/claire/api/templates/console_shell.html`), `_build_theme_selector_html()` (`src/claire/api/accounts_routes/console.py`) |
| Tests | `e2e/app_bar.spec.js` (same structure, height, and background on the three pages; the switch applies to the other pages; narrow screen). `_assert_workspace_syndication_rule()` in `tests/test_pwa_mobile_view.py`, `tests/test_pwa_restoration.py`, and `tests/test_pwa_mobile_view_adversarial_challenger1.py`. |

Limits:

1. The workspace links to `/syndication` only from the bar (`#syndlink`, for the owner) and from the stage strip. `/syndication` is not in the manifests and is not a view of the workspace.
2. `/syndication` and the console do not show the authentication state. The console link of the workspace goes to `/admin` for an administrator and to `/account` for other accounts; the link of `/syndication` goes to `/settings/reader`.
3. The text of `/syndication` is in the catalogs (`synd.*`; the knowledge base view has `synd.kb.*`, test `e2e/syndication_i18n.spec.js`). The text of the console is in the catalogs (unit IP2-2: `console.*` for the text that the server renders, `console.js.*` for the console scripts; tests `test_each_console_page_follows_the_interface_locale` and `e2e/console_i18n.spec.js`). The workspace, the share page, and the public directory are in the catalogs (unit IP2-3: `ws.*`, `reader.*`, `share.*`, `directory.*`; the server resolves the slots of the page for the locale of the request; tests `tests/test_i18n_workspace.py` and `e2e/workspace_i18n.spec.js`). Not in the catalogs: `console_syndication.js` and text that an API response supplies (for example the progress messages of the ingestion cockpit) ([`I18N_DESIGN.md`](../design/I18N_DESIGN.md)).
4. The login and setup pages, the shared document page, and the public pages have no application bar.

### 1.6 보류 결정 [결정]

| 항목 | 결정 (2026-11-13) |
| :--- | :--- |
| HD8 태블릿 폭(721–1100px) | 작업하지 않았다. 문맥 패널은 이 폭에서 서랍이다. |
| `documents`의 `(kb_id, fetched_at DESC, id DESC)` 인덱스 | 넣지 않는다. 여러 사용자의 목록을 함께 보고 쓰는 프로젝트의 상세 설계에서 스키마 변경과 함께 정한다([`SCHEMA_VERSIONING.md`](SCHEMA_VERSIONING.md)). 지금은 요청마다 일치 행을 정렬한다. |

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
- 2026-11-24: `e2e/provider_settings.spec.js` replaces the old provider routing and lineage spec, and the workspace test `admin providers checks capabilities …` is gone with the capability lock of the old provider page. Each test of the new spec has its own seed token; the first test saves on the E2E server (`data/providers.json` of the repository) and restores the values.
- 시드 DB의 로그인 토큰은 한 번만 쓸 수 있다. owner 세션이 필요한 테스트는 `e2e/seed_db.py`에 자기 토큰을 둔다.
- 실행: `bash scripts/e2e.sh run [<spec>.spec.js]`. 실행은 분리된 백그라운드 세션(`setsid` 슈퍼바이저, 포트별 `data/e2e-runs/active-<포트>`로 추적)에서 돈다. 시작한 터미널이나 에이전트 세션이 끝나도 실행은 계속되고, 실행이 죽어도 세션은 남는다. git worktree에서도 메인 저장소의 `.venv`·`e2e/node_modules`를 재사용한다. `cd e2e && npx playwright test`는 쓰지 않는다.
  - 명령: `run`(시작하고 끝까지 기다림), `start`(시작만 하고 바로 돌아옴), `wait [초]`, `status`, `watch`(출력 따라가기), `tmux`(tmux 세션에서 `watch`), `stop`. 인자가 없으면 스펙 파일마다 Playwright 프로세스를 따로 띄우고, 인자가 있으면 한 프로세스에 그대로 넘긴다.
  - 제한: 전체 실행 1200초(`CLAIRE_E2E_RUN_TIMEOUT`), Playwright 프로세스 하나 600초(`CLAIRE_E2E_SPEC_TIMEOUT`)와 `globalTimeout` 9분(메모리 상한 강제는 없음). 시간 제한에 닿으면 실행을 죽이고 `status`가 이유(`timeout`)를 알린다. 전체 실행은 보통 6분 30초 안팎이다(스펙 18개, 가장 큰 `workspace.spec.js`가 약 2분).
  - 결과: `data/e2e-runs/<실행 id>/`에 `specs.tsv`(스펙별 종료 코드·초·요약), `<스펙>.log`(테스트 출력), `<스펙>.server.log`(서버 출력), `supervisor.log`, `run.log`(전체 출력), `pid`, `result`를 둔다. `latest`가 마지막 실행을 가리키고 최근 10개만 남긴다.
  - 종료 코드(`run`, `wait`): 0 통과, 1 실패, 2 시간 제한·`stop`으로 중단, 3 기다린 시간 안에 끝나지 않음.
  - 배경(2026-11-17): 에이전트 세션이 Playwright를 다섯 시간 넘게 "실행 중"으로 보였다. journal 기록으로는 그동안 Playwright가 돌지 않았다(마지막 실행은 약 3.5분 만에 끝났다). 실행이 세션의 전경 명령에 묶여 있었고, 시간 제한과 세션 밖에서 볼 수 있는 상태가 없어서 멈춘 세션과 도는 테스트를 구분할 수 없었다.
  - Playwright가 `e2e/seed_db.py`로 시드 DB를 만들고 포트 8766에 서버를 띄운다(시작 제한 30초). `CLAIRE_E2E_BASE_URL`을 지정하면 외부 서버를 사용하고 자체 `webServer`는 띄우지 않는다.
- 전체 `pytest`는 모두 통과한다. 시간이 약 9분 걸리고, 임시 파일이 `/tmp/pytest-of-<user>`에 실행마다 1GB 가까이 쌓인다. `/tmp`가 작은 환경에서는 `TMPDIR`과 `--basetemp`를 다른 볼륨으로 준다(`--basetemp`는 `TMPDIR` 안에 두어야 asciidoctor 테스트가 통과한다).

---

## 4. 영역별 알려진 제약 [코드]

문서 개편(2026-11-16)에서 코드와 대조해 찾은 제약이다. 각 영역의 현재 동작은 링크한 구현 문서에 있다.

### 4.1 적재 ([`INGESTION.md`](INGESTION.md))

1. `CLAIRE_PDF_DETECT_ENCODING_FLAWS`는 효과가 없다. `detect_pdf_encoding_flaws`는 항상 실행된다.
2. 플러그인([INGESTION.md](INGESTION.md) §2.2)의 제약: (a) `permissions.network_modules`로 직접 접속하는 모듈(`yt_dlp`, `youtube_transcript_api`)은 플러그인 호스트 목록의 검사를 받지 않는다. (b) import 검사는 정적이라 import를 숨긴 코드를 막지 못한다. (c) 다이제스트 계산과 import 사이에 파일이 바뀌면 바뀐 코드가 실행된다(컨테이너에서는 플러그인 폴더가 읽기 전용이라 호스트에서만 바꿀 수 있다). (d) `claire.naver_clip`의 미디어 스트림 호스트는 녹화된 응답(`*.pstatic.net`)에서 정했다. 실제 스트림이 다른 호스트에서 오면 호스트 검사가 막는다 [미검증]. (e) 콘솔 업로드는 없다. 설치는 플러그인 폴더에 폴더를 넣는 것이다. (f) 실행 중 새 플러그인 코드는 프로세스를 다시 시작해야 반영된다.
3. 올린 MP4·오디오 파일을 읽는 로컬 미디어 리더가 없다. 미디어 경로는 `fetch_video`(yt-dlp, 로컬 경로 [미검증])로 가고, 복합 메시지의 미디어 파일은 `fetch_file`에서 실패한다.
4. 복합 적재의 PDF 첨부는 `required=False`라서 `data/raw/attachments/`에 저장되지 않는다. `save_inbound_file`의 수신 사본만 남는다.
5. 옛 `Docling` 기본 라벨이 남아 있다: `composite.py`(`parser_requested`/`parser_used` 기본값), `src/claire/telegram/bot.py`의 PDF 대체 안내문, `reader.js`와 `reader.tag.docling_fallback` 카탈로그 키.
6. YouTube `raw_text`는 `video_max_extract_chars`가 아니라 `raw_char_budget`(20,000자)을 쓴다. 의도인지 [미검증].
7. 추출 모드(AED4-rev): 재적재·재생성 창에는 추출 모드 선택이 없고 API로만 다시 추출한다(에이전트 클라이언트로 다시 추출하려면 에이전트가 `extract_stage(document_id=…, force=True)`를 쓴다). 문서 복사(`/api/syndication/dispatch`)는 창이 없어 계정의 기본 모드를 쓴다. v45 이전의 `raw_inbox` 행은 모드가 없어 재시도 때 계정의 기본 모드를 쓴다. 대기열로 적재한 피드·타임라인 항목은 추출 전에도 `linked`를 받지만 문서는 추출 뒤에 보인다.

8. AI 프로바이더 화면(PROVIDER_SETTINGS_SIMPLIFICATION_DESIGN.md): `PATCH /providers`에는 단계 인증과 관리자 감사 기록이 없다(서버 등급은 단계 인증·감사가 있는 `/admin/server`로 옮겼다). 이미지 해석(VLM)은 서버 환경 변수 `CLAIRE_ENABLE_VLM_TRANSCRIPTION`·`CLAIRE_VLM_*`로만 켜고 끄며 화면은 상태만 보인다. `POST /providers/check`(옛 화면의 실시간 잠금 검사)는 남아 있지만 화면이 부르지 않는다. 저장 뒤 설정 캐시는 웹 프로세스에서만 비우므로 다른 프로세스(봇, 작업 루프)는 다시 시작할 때까지 옛 값을 쓸 수 있다 [미검증].

### 4.2 추출과 링크 확장 ([`EXTRACTION.md`](EXTRACTION.md), [`EXPANSION.md`](EXPANSION.md))

1. `CLAIRE_EXPAND_CHAR_BUDGET`, `CLAIRE_RESEARCH_CONTEXT_BUDGET`은 설정·한도 관리자·UI에 있지만 확장과 리서치 코드는 읽지 않는다.
2. `diagnose_google_block` has no caller after the host-cli provider `AntigravityProvider` was deleted in `AE8` (2026-11-21). Note that `evaluate_summary_verdict` is called by `src/claire/ingest/client_extraction.py` (`_validate_summary`) during `agent_client` extraction validation.
3. `multihop_candidates`는 엣지를 만들지 않는다(2홉 누락 링크는 구상: [`MISSING_LINK_DISCOVERY_IDEAS.md`](../ideas/MISSING_LINK_DISCOVERY_IDEAS.md)).
4. `make_vector_store`는 항상 brute-force 저장소를 돌려준다. sqlite-vec는 탐지만 한다.
5. 동일 주제 출처 병합 복원은 실패한 재추출이 이미 커밋한 엔티티를 남긴다.
6. `CLAIRE_PREFERRED_LANGUAGES`는 추출 요약·상세 출력 언어에 쓰이지 않는다. 출력은 항상 한국어다.

### 4.3 판정 기록과 그래프 ([`GRAPH_BUNDLE_REVIEW.md`](GRAPH_BUNDLE_REVIEW.md), [`GRAPH_ENGINE.md`](GRAPH_ENGINE.md))

1. 홈의 '최근 판정' 카드는 테마의 `decision_stream_enabled`가 참일 때만 보이는데, 이 필드를 넣는 서버 코드가 없어 항상 숨는다.
2. `POST /resolution/rollback`(owner)은 동작하지만 UI 단추는 '테스트되지 않음'으로 꺼져 있다. 롤백 페이로드에는 관계 재연결과 흡수 엔티티 스냅샷이 없고, `merge_documents`는 진 문서를 물리 삭제한다.
3. 그래프 엔진 보류: 서버 측 좌표 캐시, 수천 노드에서 호버·라벨 후보의 공간 격자(GRAPH_ENGINE.md §13).

### 4.4 문서 형식과 프레젠테이션 ([`DOCUMENT_FORMAT.md`](DOCUMENT_FORMAT.md), [`PRESENTATION.md`](PRESENTATION.md))

1. 프레젠테이션 HUD와 미준비 페이지(`src/bible/slides/hud.py`, `src/bible/slides/views.py`)는 이모지 아이콘과 카탈로그 키 없는 한국어를 쓴다(G1, i18n 규칙 위반).
2. 여러 본문을 함께 내보내는 기능(PDF, EPUB, 묶음)은 설계가 없다.

### 4.5 인터페이스 언어 ([`I18N.md`](I18N.md))

1. `heal_graph()`(`src/claire/store/db.py`)의 CLI 진행 줄은 한국어다(`doctor --heal`, `audit --heal`). ID6은 영어를 요구한다.
2. `app.js`(재적재 대상 placeholder)와 `console_admin.js`(IdP 행 삭제 단추)에 고정 한국어가 있다. `src/claire/api/templates/docs.html`은 `lang="ko"`로 고정이다.
3. 카탈로그 키 `cli.preflight.header`와 `resolve_cli_locale()`은 쓰이지 않는다.

### 4.6 계정, 공개 사이트, MCP ([`ACCOUNTS.md`](ACCOUNTS.md), [`PUBLIC_SITE.md`](PUBLIC_SITE.md), [`API_AND_MCP_GUIDE.md`](API_AND_MCP_GUIDE.md))

1. `/admin/idp`의 로그인 정책 카드(승인·자동 가입, 허용 도메인, `email_verified`, Back-Channel Logout)는 프로토타입이다. 저장하는 API가 없고 서버가 적용하지 않는다. 가입은 초대로만 한다.
2. `og:site_name`은 고정 문자열 "Claire Bible"이고 `CLAIRE_SITE_NAME`을 쓰지 않는다. `CLAIRE_SITE_DESCRIPTION`은 쓰이지 않는다.
3. robots `Disallow` 목록에 `/admin`, `/settings`, `/api`, `/@{username}/` 경로가 없다.
4. `robots_txt_route`·`sitemap_xml_route`의 테마 호스트 분기(`get_kb_by_fqdn`)는 실제로 맞지 않는다. `HostRouter`가 옛 테마 호스트를 먼저 보낸다.
5. sitemap 캐시는 문서를 숨겨도 지워지지 않고 TTL(기본 900초)을 기다린다. 한 테마의 오류와 공유 토큰 생성 중 `sqlite3.OperationalError`는 무시되어 일부만 담긴 sitemap이 정상처럼 보인다(C2).
6. `KnowledgeBaseManager.get_kb(None)`은 관리자 기본 테마가 없으면 `is_personal` 테마나 테마 0으로 대체한다.
7. MCP: `mcp_telemetry` does not record `client_id`, and there is no per-credential call rate limit (`API_AND_MCP_GUIDE.md`, `TELEMETRY.md`). (OAuth 2.1 grant list/revoke routes `/api/me/oauth/grants` and `/oauth/revoke`, as well as `claire token` `mcp_client_config`, are now implemented.)

### 4.7 운영, 텔레메트리, 데이터 ([`TELEMETRY.md`](TELEMETRY.md), [`OPERATIONS.md`](OPERATIONS.md), [`FEDERATION.md`](FEDERATION.md))

1. `telemetry.db`의 모든 연결(`connect_telemetry`)이 DDL·ALTER를 실행한다. Support Bundle 진단 조회도 읽기 전용이 아니다.
2. 텔레메트리 자동 정리는 없다. `claire telemetry --prune`만 지운다. 보관 기한 30일은 설정할 수 없다.
3. 프로바이더 텔레메트리(`provider_telemetry`)를 기록하는 코드가 없다. 유일한 기록자였던 `AntigravityProvider`는 `AE8`에서 삭제되었고(2026-11-21), 예전 행은 읽기 경로에 남는다. MCP 텔레메트리는 계속 기록된다.
4. `cb-manuscript update`는 업데이트 전 백업을 자동으로 만들지 않는다. backup v1 manifest는 `claire.db` 하나만 검증한다.
5. 데이터 저장 배치 DS-3(계정별 관리자 적재 한도)은 구현되지 않았다.
6. 연합 FP4 보류 항목: D1(계정별 차단·숨김·신고), D2(계정별 발신 속도 한도), D4/RD12(외부 연결 계정), 피드 자동 적재 규칙.
7. Support Bundle의 MCP 추적 열(`kb_id`, `document_id`, `detail`)은 2026-11-24 이후의 `extract_*` 호출에만 있다. 그 전 호출은 `extraction_trail.json`의 `mcp_calls`에 없고 `MCP_TRAIL_ABSENT`로 보인다. 계정 범위 번들은 내 계정의 노트·문서만 추적한다: 멤버가 다른 계정의 공개 문서나 공유 지식 베이스 문서의 링크를 넣으면 번들을 만들지 않는다(404 `target_not_found`).
8. 서버 서포트 번들의 만들기 API가 둘이다: `/support/bundles` 화면이 쓰는 `POST /api/support/bundles`(`scope` `server`)와 텔레그램 위임이 쓰는 `POST /support/bundle`. 등록부는 서버 번들을 만든 관리자를 기록하지 않으므로 관리자 목록은 모든 서버 번들을 보인다.
