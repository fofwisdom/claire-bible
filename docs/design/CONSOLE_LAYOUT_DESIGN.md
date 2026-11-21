# 설정 및 관리 콘솔 레이아웃 개편 설계 (`CONSOLE_LAYOUT_DESIGN`)

- 문서 상태: Google 현대 웹 아키텍처 기준 개편 확정 및 콘솔 셸 구현 완료 (공유 지식베이스 섹션은 후속 구현)
- 시각 참조 프로토타입:
  - 모던 웹 개선판 (채택): [`prototypes/console_modern_prototype.html`](prototypes/console_modern_prototype.html)
  - 초기 비교 시안: [`prototypes/console_and_shared_knowledge_prototype.html`](prototypes/console_and_shared_knowledge_prototype.html)
- 관련 문서:
  - [`ADMIN_AND_SETTINGS_PATH_SEPARATION_DESIGN.md`](ADMIN_AND_SETTINGS_PATH_SEPARATION_DESIGN.md)
  - [`../implementation/ACCOUNTS.md`](../implementation/ACCOUNTS.md) §5.9
  - [`../implementation/WEBUI.md`](../implementation/WEBUI.md) §2, §7

---

## 1. 목적 및 개편 원칙

설정 및 관리 콘솔(`/settings/*`, `/admin/*`)을 현대 웹 표준 기반의 **단일 셸 정보 구조**로 개편한다.

1. **문서 자연 스크롤 우선**: `theme.css`의 워크스페이스용 `body { overflow: hidden }`을 콘솔에서 해제하고(`height: auto; min-height: 100dvh; overflow: visible`), 사이드바와 본문 목차에 `position: sticky`를 써서 잘림 없는 스크롤과 OS 텍스트 배율(`<meta name="text-scale" content="scale">`)을 보장한다.
2. **단일 접이식 사이드바 + 공통 상단바 Command Palette (`/`)**: 2단 레일/중첩 셸 대신 단일 그룹 아코디언 사이드바(`button[data-nav-group-toggle]`)를 두고, `ClaireCommandPalette`(`<dialog id="cmdpalette" closedby="any">`) 트리거(`#palettetrigger`)는 사이드바나 모바일 유틸리티 바가 아니라 모든 페이지 공통 상단바(`.appbar-center`) 안에 유지해 섹션 전환과 페이지 내 항목 점프를 통합한다.
3. **권한 경계 우선 (CL11 / Invariants 1–3)**:
   - 비관리자의 `/admin/*` 요청은 계속 `404`로 응답한다.
   - 비관리자 응답 HTML에는 `/admin/*` 링크·마크업·스크립트(`console_admin.js`)·관리자 i18n 키(`console.js.admin.*`)를 싣지 않는다.
   - `console_shell.js` Command Palette는 서버가 이미 렌더링한 `#console-sidebar-inner a[data-nav-section]`만 색인하므로 클라이언트 JS가 관리자 경로를 보유하지 않는다.
4. **공통 상단바 단일 원본 규약 (`src/claire/ui_chrome.py`)**: 콘솔(`header.appbar`), 워크스페이스, `/syndication` 및 향후 확장 경로(`themes`, `kb` 등)는 `partials/common_appbar.html` · `static/css/appbar.css` · `static/js/appbar.js`를 공유한다. 오른쪽 끝 아이콘 순서는 오른쪽부터 햄버거(`#hamburger-btn`) → 신디케이션(`#syndlink`) → 유저 홈(`#homelinkbtn`)으로 통일하고, 콘솔 진입(`#consolelinkbtn`)과 로그아웃(`/logout`)은 햄버거 메뉴(`#pop-hamburger-menu`) 안에서 호출한다(상단바 내부 `<input>`, 인증 상태 배지, 바깥 로그아웃 없음).

---

## 2. 정보 구조 (사이드바 그룹 & 향후 확장 슬롯)

### 2.1 현재 구현 그룹 (`src/claire/api/accounts_routes/console.py`)

| 그룹 ID | 대상 | 섹션 |
| :--- | :--- | :--- |
| `workspace` | Owner | `account`, `federation`, `keys`, (`themes`, `tokens`: 비관리자만), `telegram`, `bundles`, `backups` |
| `syndication` | Owner (테마별) | `reader`, `connections`, `feeds`, `tags`, `rules`, `outbound`, `opml` |
| `agents` | Owner (테마별) | `mcp`, `webmcp` |
| `admin_server` | Admin 전용 | `server`, `federation`, `accounts`, `idp`, `providers`, `limits` |
| `admin_system` | Admin 전용 | `themes`, `tokens`, `mcp`, `backups`, `storage` |

### 2.2 후속 확장 슬롯: 공유 지식베이스 (미구현, 별도 설계 연계)

- `/settings/kb`, `/settings/kb/{kb_number}`, `/admin/kb` 라우트 및 백엔드 기능은 공유 지식베이스 설계 구현 시 동일 그룹 아코디언 + Split Row 패턴으로 편입한다.

---

## 3. 현대 웹 레이아웃 및 상호작용 명세

### 3.1 데스크톱 / 태블릿 (`> 860px`)

- **CSS Grid**: `.console-body { display: grid; grid-template-columns: 16rem minmax(0, 1fr); align-items: start; }`
- **Sticky 사이드바**: `top: var(--console-topbar-h, 46px)`, `max-height: calc(100dvh - var(--console-topbar-h, 46px))`, `overscroll-behavior: contain`, `scrollbar-gutter: stable`.
- **그룹 아코디언**: 접힘 상태는 계정 범위 `sessionStorage`(`claireConsoleCollapsedGroups:<scope>`)에 저장하되, 현재 페이지(`a[aria-current="page"]`)를 포함한 그룹은 항상 펼쳐 둔다.
- **Unboxed 섹션 헤더 + 우측 Sticky TOC**: 본문 컨테이너를 `@container cmain / inline-size`로 감싸고, `.console-layout`은 `h2`가 3개 이상일 때 우측 `12.5rem` In-Page TOC(`#console-toc`)를 표시한다. `@container cmain (max-width: 56rem)`에서 TOC를 자동 숨김해 본문 폭을 지킨다.

### 3.2 모바일 (`<= 860px`) 오프캔버스 드로어

- **유틸리티 바**: `.console-mob-bar`가 상단바 아래에 sticky 고정되어 메뉴 버튼(`#console-mob-menu`)과 현재 섹션 제목을 제공한다(모바일에서도 `#palettetrigger`는 공통 상단바 `.appbar-center` 2행에 위치한다).
- **단일 DOM 이동 + `<dialog closedby="any">`**: `console_shell.js`가 `matchMedia('(max-width: 860px)')`에 따라 `#console-sidebar-inner`를 `.console-sidebar`와 `<dialog id="console-nav-drawer">` 사이로 이동한다. 복제하지 않으므로 ID·`aria-current`·이벤트 중복이 발생하지 않는다.
- **스크롤 스냅 드로어 + `overscroll-behavior: contain`**: 가로 `scroll-snap-type: x mandatory` 트랙과 `IntersectionObserver`로 스와이프 닫기, 백드롭 클릭 닫기, `Esc` 닫기를 모두 지원한다.

### 3.3 콘텐츠 컴포넌트 현대화 (`console_shell.js` + `console_shell.html`)

| 패턴 | 구현 방식 | 목적 |
| :--- | :--- | :--- |
| **Split Setting Row (`.is-split`)** | `@container cmain (min-width: 43.75rem)`에서 2열 그리드(`0.9fr / 1.35fr`). 제목·설명은 왼쪽(`.console-split-meta`), 조작 폼·스위치·결과는 오른쪽(`.console-split-ctrl`) | 세로 스크롤 길이를 줄이고 설정 스캔 속도를 높임 (`table`, `.pref-table-wrap`, `.pref-stat-row`, 중첩 호스트 카드는 제외) |
| **Subgrid KPI 카드** | `.pref-stat-row` + `@supports (grid-template-rows: subgrid)` | 지표 설명 줄바꿈 시에도 모든 KPI 숫자와 라벨 행을 수평 정렬 |
| **컨테이너 쿼리 스택 테이블** | `@container cmain (max-width: 35rem)` + `console_shell.js`의 `td[data-label]` 자동 주입 + `.pref-table-wrap[tabindex="0"]` | 좁은 본문 폭에서 가로 잘림 대신 키-값 카드 행으로 전환 |
| **크로스브라우저 Switch** | `appearance: none` 기반 커스텀 `input[type="checkbox"][switch]` 트랙·썸 | Chromium·Firefox·Safari 모두 일관된 스위치 UX 제공 |
| **문서 간 전환** | `@media (prefers-reduced-motion: no-preference) { @view-transition { navigation: auto; } }` | 섹션 이동 시 브라우저 네이티브 크로스페이드 전환 |

---

## 4. 검증 기준

1. `1440×900`, `1280×620`, `390×800` 모든 뷰포트에서 가로 오버플로(`scrollWidth > clientWidth`) 0건, 문서 끝까지 세로 스크롤 가능.
2. 비관리자 `/settings/*` HTML 및 `/` Command Palette에 `/admin/*`·`console_admin.js` 미포함.
3. 상단바(`header.appbar`) 높이 및 요소 수 동등성(`e2e/app_bar.spec.js`), 라인 아이콘·무이모지 규칙(`tests/test_ui_line_icons.py`), i18n 카탈로그 분리 규칙(`tests/test_i18n_console.py`, `e2e/console_i18n.spec.js`), 통합 콘솔 UI 계약 및 일관성 검증(`tests/test_console_ui_contract.py`, `e2e/console_ui_consistency.spec.js`) 모두 통과.

---

## 5. Unified Console UI System and Invariant

Every console route under `/settings/*`, `/admin/*`, and `/moderation/*` uses one shared Console UI System based on the `/settings/account` visual design.

### 5.1 System Layers

1. **Shell template (`src/claire/api/templates/console_shell.html`)**: Renders the shared application bar (`header.appbar`), the collapsible sidebar (`#console-sidebar`), the section header (`.console-sec-head`), `#console-section-body`, and the sticky table of contents (`#console-toc`). It loads `console_ui.css`, `console_ui.js`, and `console_shell.js`.
2. **Stylesheet (`src/bible/static/css/console_ui.css`)**: Defines the console grid layout, design tokens, card rules (`.console-subcard`, `.card`, `.pref-card`), form primitives (`.console-field`, `.console-form-row`, `.console-form-grid`, `.console-actions`, `.console-kv`, `.pref-input`, `.pref-select`, `.pref-btn`, `.pref-btn-accent`, `.pref-btn-danger`, `.pref-btn-sm`), native checkbox switches (`input[type="checkbox"][switch]`), responsive tables (`.pref-table-wrap` and `.pref-table`), notices (`.pref-notice`), KPI grids (`.pref-stat-row`), and pills (`.pref-pill`). It uses `var(--danger)` for error states and never uses `var(--error)`.
3. **Server HTML builders (`src/claire/api/accounts_routes/console_ui.py`)**: Provides typed Python HTML builder functions (`console_card`, `console_form_card`, `console_host`, `console_field`, `console_toggle_row`, `console_table`, `console_notice`, `console_kv`, `console_result`, `console_stat_row`, `console_actions`, `console_pill`).
4. **Client HTML builders (`src/bible/static/js/console_ui.js`)**: Exposes `window.ClaireConsoleUI` (`esc`, `icon`, `card`, `field`, `toggleRow`, `notice`, `pill`, `table`, `statRow`, `kv`, `result`, `btn`, `actions`) for client-rendered sections.

### 5.2 Host Containers and Flat Cards

- Script-mounted areas use `.console-host` (`console_host(...)`), an unboxed container without card borders.
- Top-level sections inside `#console-section-body` or `.console-host` render as direct sibling `.console-subcard` elements.
- Do not nest a card (`.card`, `.console-subcard`, `.pref-card`, or `.claire-card`) inside another card.

### 5.3 Scoped `.claire-*` Unification

- `console_ui.css` overrides `.claire-card`, `.claire-card-head`, `.claire-btn`, `.claire-input`, `.claire-select`, `.claire-textarea`, `.claire-note`, `.claire-tag`, `.claire-srow`, `.claire-kpis`, `.claire-kpi`, `.claire-tw`, and `.claire-table` strictly under `.console-shell` (and `dialog.claire-dlg` under `body[data-active-area]`).
- This scoping aligns shared knowledge base, moderation, and federation console views with `/settings/account` while leaving public `/kb`, `/themes`, `/accounts`, and workspace views unchanged.
- Console markup and scripts must use `input[type="checkbox"][switch]` and must not emit `.claire-switch`.

### 5.4 Architectural Invariant and Guardrails

- **Invariant**: All current and future features under `/settings/*`, `/admin/*`, and `/moderation/*` must render inside `console_shell.html` and build UI elements through `console_ui.py` (server) and `ClaireConsoleUI` (`console_ui.js`) with `console_ui.css`. Do not add ad-hoc card, switch, button, or inline fake-card styles.
- **Verification**: `tests/test_console_ui_contract.py` checks builder outputs, all-route shell structure, zero nested cards, zero `var(--error)`, non-admin CL11 isolation, and static JS/partial guardrails. `e2e/console_ui_consistency.spec.js` checks computed card borders, primary button color, heading typography, switch controls, sticky TOC visibility, zero horizontal overflow, zero JS errors, and zero raw i18n keys across desktop and mobile viewports.
