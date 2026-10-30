# 그래프 엔진 구현 정본 (Graph Engine Implementation Reference)

기준 커밋: `99f98e3` (2026-10-31) · 대상 독자: 지식 그래프 화면을 고치거나 확장하는 에이전트 · 설계 배경(왜 이렇게 만들었는가): [GRAPH_ENGINE_SHOPIFY_EDITIONS_ADOPTION_DESIGN.md](../design/GRAPH_ENGINE_SHOPIFY_EDITIONS_ADOPTION_DESIGN.md)

이 문서는 **지금 코드가 무엇을 어디서 어떻게 하는지**를 적는다. 줄 번호는 금방 바뀌므로 파일 경로와 함수·상수 이름을 기준점으로 삼는다. 찾을 때는 `grep -n "<이름>" <파일>`을 쓴다. 각 항목에는 근거를 붙였다.

- **[코드]** 기준 커밋의 소스에서 직접 확인한 사실
- **[실측]** 브라우저로 측정한 값(측정 조건을 함께 적음)
- **[결정]** 저장소 소유자가 정한 방침. 바꾸기 전에 소유자에게 묻는다
- **[미검증]** 확인하지 못한 것. 사실로 인용하지 않는다

---

## 0. 작업 전에 알아 둘 것

1. **[코드] 엔진은 외부 의존성이 없다.** 파일 4개를 `src/claire/graphview.py`의 `_GRAPH_ENGINE_FILES = ("core.js", "simulation.js", "renderers.js", "network.js")` 순서대로 `GRAPH_HTML`에 인라인한다. CDN 스크립트를 추가하지 않는다.
2. **[코드] `GRAPH_HTML`은 모듈 import 시점에 한 번 만들어진다**(`GRAPH_HTML: str = _build_standalone_graph_html()`). JS를 고친 뒤에는 서버를 재시작해야 화면에 반영된다.
3. **[코드] 마커 테스트는 정확한 문자열을 검사한다.** `tests/test_graphview.py`의 `test_graph_engine_physics_and_rendering_backends`, `test_graph_engine_interaction_model`, `test_topological_structure_and_isolated_orbit_layout`, `test_adaptive_physics_and_visual_hierarchy_html_markers`가 함수 시그니처, 옵션 이름, 상수 줄을 그대로 찾는다. 이름이나 줄을 바꾸면 테스트도 같이 고친다.
4. **[결정] 운영 서버에 접근하거나 배포하지 않는다.** 검증은 개발 모드와 그래프 번들로 한다(§11). 운영 배포 상태를 알아야 하면 소유자에게 확인을 요청한다.
5. **[결정] 그래프 배경은 앱 라이트/다크 테마를 따른다.** Shopify처럼 어두운 캔버스로 고정하지 않는다.
6. **[결정] Shopify Editions에서는 마우스·터치 상호작용과 명확한 시각 스타일만 차용한다.** 엔티티에 숫자(연결 수 위첨자 등)를 붙이는 식으로 겉모양을 억지로 맞추지 않는다.
7. **[코드] 앱(`app.js`)은 물리를 켜고 끄지 않는다.** 안정화, 수면, 첫 표시는 엔진이 맡는다. vis-network 시절의 `physics:false` 토글이나 드롭 고정 같은 코드를 되살리지 않는다.

---

## 1. 파일 지도

| 파일 | 역할 | 주요 기호 |
| :--- | :--- | :--- |
| `src/claire/static/js/graph/core.js` | 네임스페이스 `window.ClaireGraph`, 수학·해시·색 유틸, 이벤트, vis 호환 `DataSet` | `hash32`(FNV-1a), `hashUnit`, `parseColor`, `mixColor`, `Emitter`, `DataSet` |
| `src/claire/static/js/graph/simulation.js` | 신장 숲 구축, 별 무리 배치, 바깥 궤도, 물리 한 스텝 | `SIM_DEFAULTS`, `buildHierarchy`, `packStars`, `Simulation`(`setTopology`, `setHidden`, `_placeOrbits`, `snapToHomes`, `warmup`, `step`, `release`, `dragTo`) |
| `src/claire/static/js/graph/renderers.js` | 렌더러와 무관한 프레임 버퍼, WebGPU(WGSL) 렌더러, Canvas2D 렌더러 | `Frame`, `NODE_STRIDE = 12`, `EDGE_STRIDE = 16`, `SHAPE_CIRCLE/RING/SQUARE`, `WebGPURenderer`, `Canvas2DRenderer` |
| `src/claire/static/js/graph/network.js` | vis-network 호환 `Network`: 렌더 루프, 카메라, 입력, 선택, 라벨 오버레이, 백엔드 폴백 | `DEFAULT_OPTIONS`, `_tick`, `_draw`, `_drawOverlay`, `_fitNow`, `_fitInsets`, `_syncPackAspect`, `_setHover` |
| `src/claire/static/js/app.js` | 워크스페이스. 데이터 적재, 필터, 검색, 경로, 팝업, 테마 연결 | `applyTopologicalStructure`, `applyView`, `nodeRadius`, `nodeFontSize`, `idleEdgeStyle`, `clusterMatches`, `NODE_POP_DELAY_MS`, `setupWheelZoom` |
| `src/claire/store/queries.py` | `/graph` 응답 생성 | `graph_json` |
| `src/claire/graphview.py` | 정적 자산을 묶어 단일 HTML 생성 | `_GRAPH_ENGINE_FILES`, `_build_standalone_graph_html` |

규모 [코드]: `core.js` 178줄, `simulation.js` 710줄, `renderers.js` 402줄, `network.js` 1,200줄, `app.js` 7,132줄.

---

## 2. 데이터 흐름: 서버에서 화면까지

1. **[코드] `/graph`** (`store/queries.py` `graph_json`)
   - 노드 필드: `id`, `label`, `group`(엔티티 타입), `sources`, `obs`(관찰 첫 줄, 최대 200자), `degree`.
   - 엣지 필드: `id`(`e{순번}`, append-only), `from`, `to`, `label`(관계 타입), `arrows: "to"`, `dashes`(provisional 관계).
   - 그 밖에 `stats.max_degree`가 온다. 양끝 노드가 모두 보이는 관계만 내보낸다.
2. **[코드] app.js 적재**: 첫 로드(`fetch('graph')` 콜백), `reloadThemeData`, `refreshGraph` 세 경로가 있다.
   - 노드마다 `size: nodeRadius(degree)`(3–9)와 `font.size: nodeFontSize(degree)`(11–18)를 붙인다.
   - `applyTopologicalStructure`로 고립 노드와 2노드 쌍에 `orbit`과 `physics:false`를 붙인다.
   - `ClaireGraph.DataSet` 두 개와 `ClaireGraph.Network`를 만든다.
3. **[코드] `applyView`**: 연결 수 필터(`curMinDeg`), 문서·검색·경로·격리 강조를 계산해 노드와 엣지를 `DataSet.update` 배열 한 번으로 반영한다(`hidden`, 색, 테두리, 투명도).
4. **[코드] `Network`가 `DataSet` 이벤트를 받는다**(`_bindData`).
   - `add`/`remove`: `_topologyDirty`가 켜지고 `_rebuild` → `Simulation.setTopology`로 이어진다.
   - `update`의 `hidden`: `_hiddenDirty`가 켜지고 `_refreshHidden` → `Simulation.setHidden`으로 이어진다.
   - `update`의 `physics`/`fixed`/`size`/`orbit`: `_structureChanged`가 실제로 값이 바뀐 것을 확인했을 때만 재구축한다.
   - `update`의 `x`/`y`: `_applyPositionUpdates`가 좌표만 옮긴다.
5. **[코드] 매 프레임**: 시뮬레이션 스텝 → `Frame`에 노드·엣지·패킷을 화면 좌표로 채움 → 렌더러 `draw(frame)` → 라벨 2D 캔버스(`_drawOverlay`).

`DataSet.update`는 최상위 필드만 얕게 병합한다 [코드]. `color`, `font`, `orbit` 같은 객체는 통째로 바뀐다.

---

## 3. 프레임 루프 (`Network._tick`) [코드]

순서대로 실행된다.

1. 화면 배율(`devicePixelRatio`)이 바뀌었으면 캔버스 크기를 다시 잡는다.
2. `_topologyDirty`이면 `_rebuild()`.
3. `_hiddenDirty`이면 `_refreshHidden()`. 첫 안정화 전이면 이 안에서 `snapToHomes()`와 `warmup()`까지 한다(§5.4).
4. 보이면 `_autoFitStep('pending')`.
5. 상호작용이 8초 넘게 없고 안정화도 끝났으면 30fps로 낮춘다(건너뛴 시간은 다음 dt에 합산).
6. 물리: 누적 시간으로 고정 스텝 `STEP = 1/60`을 최대 4스텝까지 `sim.step()`.
7. 포인터 속도 감쇠 → 카메라 애니메이션 → 호버 포커스 보간(`_updateFocus`) → 안정화 판정(`_trackStability`).
8. `_draw()`. 그리고 커서가 그래프 위에 멈춰 있으면 **매 프레임 호버를 다시 판정**한다(§7.1).
9. 바쁘거나(카메라 애니메이션, 드래그, 팬, 핀치, 포커스 보간, 안정화 대기, 포인터 있음, 평균 속도 > 0.5) 배경 애니메이션이 켜져 있으면 다음 프레임을 예약한다.

안정화 판정 [코드]: 평균 속도 < 6인 상태가 0.4초 이어지거나 에너지를 준 지 4초가 지나면 `stabilized` 이벤트를 낸다. 처음 한 번은 `_settledOnce = true`가 된다. 탭이 숨겨지거나 컨테이너 크기가 0이면 루프를 멈춘다.

---

## 4. 항목 필드와 참여 모드

### 4.1 엔진이 읽는 노드 필드 [코드]

| 필드 | 쓰임 |
| :--- | :--- |
| `id`, `label`, `group` | 식별, 라벨, 타입색(`groups[group].color`) |
| `size` | 월드 반경. 화면 반경은 `clamp(r × scale^0.6, 1.2, r × 2.5)`(`_screenRadius`) |
| `font.size` | 라벨 크기와 우선순위 |
| `color` | `{background, border, hover:{border}}` 또는 문자열 |
| `hidden` | 숨김(참여 모드 off) |
| `physics: false` | 물리 제외(static) |
| `fixed` | 고정. `hidden`과 함께면 숨은 앵커(검색 뭉치기) |
| `x`, `y` | 고정·static 노드는 그 좌표에 고정, 그 밖에는 초기 좌표 |
| `orbit: {band, index, side}` | `physics:false`일 때만 의미가 있다. 바깥 궤도 배치(§5.3) |
| `opacity`, `borderWidth` | 흐림, 강조 고리(`borderWidth ≥ 2`) |
| `degree` | 라벨 우선순위 |

### 4.2 엔진이 읽는 엣지 필드 [코드]

| 필드 | 쓰임 |
| :--- | :--- |
| `from`, `to`, `id` | 연결 |
| `color` | `{inherit, opacity, color}`. `inherit`이면 양끝 노드 타입색 그라디언트 |
| `width`, `dashes`, `arrows`, `hidden` | 선 모양, 방향, 숨김 |
| `label`, `font.size` | 관계 라벨. `font.size > 0`인 엣지만 그린다 |
| `length` | 숨은 앵커에 이어진 엣지(앵커 스프링)의 길이 |

### 4.3 참여 모드 (`Simulation.setHidden`) [코드]

| 모드 | 조건 | 동작 |
| :--- | :--- | :--- |
| `MODE_ANCHOR` (3) | `hidden && fixed` | 숨은 고정 앵커. 이어진 노드를 스프링으로 끈다 |
| `MODE_OFF` (0) | `hidden` | 그리지 않고 물리에서도 빠진다 |
| `MODE_STATIC` (2) | `fixed` 또는 `physics:false` | 움직이지 않는 장애물. 그리기·충돌만 한다 |
| `MODE_ACTIVE` (1) | 나머지 | 계층 물리에 참여한다 |

---

## 5. 레이아웃 (`simulation.js`)

### 5.1 신장 숲 (`buildHierarchy`) [코드]

`setTopology`(구조 변경) 때만 다시 만든다. 숨김만 바뀌면 숲은 유지한다.

1. **허브 수**: `clamp(round(√(참여 노드 수) × hubShare(0.6)), 1, maxHubs(32))`. 연결 수가 `minHubDegree(3)` 이상인 노드 가운데 연결 수 상위를 고른다. 동점은 `hash32(id, 11)`로 결정적으로 가른다.
2. **주 부모**: 허브에서 다중 출발 BFS로 정한다. 층마다 연결 수가 큰 부모가 먼저 자식을 가져간다. 허브에 닿지 않는 연결 요소는 요소 안의 연결 수 최대 노드를 루트로 삼는다.
3. **깊이 접기**: 깊이가 `maxDepth(2)`를 넘는 노드는 `maxDepth - 1` 층 조상의 자식으로 옮긴다. 그래서 숲은 허브 → 자식 → 손자 2단이 된다. 옮겨진 노드와 원래 부모 사이의 실제 관계는 교차 엣지가 된다.
4. **자식 정렬**: 타입(`group`) 순으로 정렬해 같은 색이 부채꼴로 모이게 한다.
5. **tether 기본 길이**: 잎에서 위로 계산한다.
   - `base`: 깊이 1이면 `linkLength(62) × max(1, √(형제 수 / starGrowth(22)))`, 그보다 깊으면 `nestedLength(30) × max(1, √(형제 수 / 8))`.
   - `tetherBase = base × 4^(hashSigned(id,29) × lengthJitter / 3) + (하위반경 − r) × 0.7 + r_부모 + r_자식`. 지터는 0.63–1.59배다.
6. **친화도**: 서로 다른 루트에 속한 노드를 잇는 관계 수(`affOf(a, b)`). 별 무리 배치에 쓴다.
7. **`rootOrder`**: 전체 하위 반경 순으로 정렬한다. 궤도 회전 방향(`orbitSign`)을 이 순서로 번갈아 준다.

### 5.2 별 무리 배치 (`packStars`)와 물리 [코드]

- **배치 시점**: `setHidden` 때마다 보이는 루트만으로 다시 짠다. 보이는 하위 반경(`visExt`)이 큰 별부터 놓는다.
- **배치 규칙**: 다음 별은 이미 놓인 별과 친화도 합이 가장 큰 것을 고른다. 친화도가 없으면 남은 것 가운데 가장 큰 별이다. 자리 후보는 놓인 별마다 맞닿는 지점 24곳이다(별 반경은 `visExt × starOverlap(0.8)`, 간격 `packGap(26)`). 그 가운데 `hypot(x / packAspectX, y) + 0.6 × 친화도 가중 평균 거리`가 가장 작은 곳에 놓는다. 마지막으로 경계 상자 가운데를 원점에 맞춘다.
- **`packAspectX`**: `Network._syncPackAspect`가 그래프 사용 영역(컨트롤 제외) 비율로 정한다. 범위는 0.6–2이고, 15% 넘게 바뀌면 다시 짠다(§8).
- **한 스텝의 순서** (`Simulation.step`): `_updateGrips` → `_packForces`(루트를 제자리로 `packStiffness(3.5)`, 별끼리 `separation(7)` 반발) → `_tetherForces`(임계 감쇠 스프링 + 궤도 위 제자리 각도로 끄는 `homeStrength`) → `_freeForces`(보이는 조상이 없는 노드: 이웃 스프링 + 중심 중력) → `_anchorForces` → `_pointerForces`(커서 자석·물결) → `_integrate` → `_projectTethers`(위치 기반 tether 보정) → `_collide`(균일 격자, 반발 계수 0.6).
- **질량**: 역질량은 `1 / (1 + 0.5 × 자식 수)`다. 큰 허브는 잎들의 반작용에 덜 흔들린다.
- **궤도 회전**: `orbitSpeed(0.012 rad/s)`. 호버하면 `hoverSlow(0.2)`배로 느려지고, 선택이 있으면 멈춘다.

### 5.3 바깥 궤도: 고립 노드와 2노드 쌍 [코드]

- **앱의 몫** (`applyTopologicalStructure`): 띠와 순서만 정한다.
  - 고립 노드: `{band: 1, index}`. 타입, 라벨 순으로 정렬한다.
  - 2노드 쌍: `{band: 0, index, side: ±1}`.
  - 둘 다 `physics:false`를 붙이고, 좌표는 넣지 않는다.
- **엔진의 몫** (`_placeOrbits`): `setHidden` 때마다 좌표를 계산한다.
  - 기준은 보이는 루트의 경계 상자에서 `orbitGap(60)` 바깥 타원이다.
  - 쌍 띠를 먼저 놓는다. 쌍 사이 `pairSpacing(36)`이 모자라면 타원을 키운다.
  - 그다음 `orbitBandGap(48)` 바깥에 고립 띠를 놓는다. 간격은 `orbitSpacing(24)`이고, 넘치면 `orbitRingGap(36)`씩 궤도를 더한다.
  - 보이는 노드만 `index` 순으로 고르게 펼친다.
- **예외**: 좌표가 있는 노드(사용자가 끌어다 놓아 `item.x/y`가 생긴 static 노드)는 놓은 자리를 지킨다.

### 5.4 첫 표시와 워밍업 [코드]

- **생성 시점**: `Network` 생성자의 `_rebuild`는 새 노드가 절반을 넘으면 `sim.warmup()`을 부른다. 최대 160스텝이고, 예산은 45ms다.
- **앱의 첫 필터**: 앱이 곧바로 필터를 적용하면 별 무리가 다시 짜인다. 첫 안정화 전(`!_settledOnce`)의 `_refreshHidden`은 `snapToHomes()`로 보이는 별을 통째로(루트와 하위 노드를 같은 만큼) 새 제자리로 평행 이동한 뒤 다시 `warmup()`한다.
- **그 뒤의 필터 변경**: 별이 미끄러져 옮겨 가는 애니메이션을 그대로 보여 준다.

---

## 6. 렌더링

- **[코드] 프레임 형식**
  - 노드 12 float: `x, y, radius, shape, r, g, b, a, ringWidth, 예약 3`.
  - 엣지 16 float: `x0, y0, x1, y1, width, dash, 예약 2, 시작 rgba, 끝 rgba`.
  - 모두 CSS px, 직선(straight) 알파다. 두 렌더러가 같은 프레임을 그린다.
- **[코드] 백엔드 선택** (`_initBackend`)
  - 순서: URL `?graphBackend=canvas2d|webgpu` → `localStorage.claireGraphBackend` → 옵션 `renderer.backend`(기본 `auto`).
  - `auto`는 WebGPU를 먼저 시도한다(초기화 제한 2.5초). 실패하면 Canvas2D를 쓴다.
  - 장치 손실 시 `_fallbackToCanvas`가 새 캔버스로 교체한다(WebGPU 컨텍스트를 잡은 캔버스는 2D로 바꿀 수 없다).
  - 현재 상태는 `claireDebug.graphRenderer`(`{backend, fallbackReason, nodes, edges}`)로 본다.
- **[코드] 엣지 스타일**
  - 대기 상태: 앱의 `idleEdgeStyle(scale)`(투명도 0.32–0.6, 굵기 0.8–1.2)에 `color.inherit` 타입색 그라디언트를 쓴다.
  - 신장 숲에 들지 않은 교차 엣지는 `renderer.crossEdgeAlpha(0.2)`배로 흐린다.
  - 호버한 노드의 관계는 `edges.color.highlight`(앱 테마의 `edgeHi`)로 섞어 밝힌다.
- **[코드] 노드 모양**: 허브(루트이면서 자식 3개 이상, `_isHub`)는 사각형, 나머지는 원이다.
  - 호버한 노드: 고리가 커진다(`_ringAnim`).
  - 호버한 노드의 이웃: 속이 빈 고리가 생긴다(색 `nodes.font.color`).
  - 선택·강조 노드: 테두리 고리가 생긴다.
- **[코드] 흐름 패킷**: 관계 방향(from → to)으로 점이 흐른다. 속도는 `flowSpeed(0.18)`이고, 위상은 `hashUnit(edge.id, 41)`로 흩는다.
- **[코드] 라벨** (`_drawOverlay`)
  - 후보 우선순위: 호버 > 선택 > 강조(`borderWidth ≥ 2`) > 호버 이웃 > 허브 > 나머지. 나머지는 `cam.scale ≥ labelZoom(0.8)`일 때만 후보가 된다.
  - 우선순위 순으로 4방향 자리를 시도하고, 겹치면 건너뛴다. 최대 개수는 `min(maxLabels(180), max(24, W×H/6500))`다.
  - 겹친 컨트롤 자리(`_avoidRects`)는 미리 차지된 자리로 둔다. 호버 라벨만은 자리가 없어도 그린다.
  - 호버하지도 선택하지도 않은 라벨은 `labelMaxChars(28)`에서 말줄임한다.
  - 호버 라벨은 `hoverLabelScale(1.7)`배로 키운다. 굵게 그리는 것은 선택 라벨뿐이다.
  - 글꼴은 `D2Coding, 'Noto Sans KR', ui-monospace, …`다.
- **[코드] 감속 모션**: `prefers-reduced-motion: reduce`이면 궤도 회전, 흐름 패킷, 커서 물결을 끈다.

---

## 7. 상호작용

### 7.1 호버 [코드]

- **판정**: `_hitNode(x, y, slop)`가 `거리 − 화면반경 ≤ slop`인 가장 가까운 노드를 고른다. slop은 마우스 4px, 터치 12px다.
- **다시 판정하는 시점**: 마우스가 움직일 때(`_onMove`)와, 누르고 있지 않은 동안 매 프레임(`_tick`)이다. 그래서 노드가 자석·궤도·안정화로 멈춘 커서 밑에 들어오거나 빠져도 따라간다.
- **포커스**: `focusSet`은 호버 노드와 그 이웃이다.
  - 나머지 노드는 `contextAlpha(0.45)`까지 흐리고, 엣지는 그 0.6배까지 흐린다.
  - 선택이 하나라도 있으면 포커스를 끈다.
  - 호버한 노드를 끄는 동안에는 포커스를 유지한다.
- **[실측] 안정성**: 배포본(`cc2efac`), 1440×900에서 큰 허브 3개에 커서를 2초씩 멈췄을 때 호버 대상이 바뀐 횟수는 0이었다.
- **[코드] 앱의 요약 팝업**
  - `hoverNode` 이벤트에서 `NODE_POP_DELAY_MS(1000)` 뒤 `showNodePop`을 부른다. 상세 패널의 "이 문서의 노드" 버튼(`peekNode`)도 같은 상수를 쓴다.
  - 모바일·컴팩트 화면(`compactMQ`/`mobileMQ`)에서는 호버 팝업을 쓰지 않는다. 탭으로 연다.
  - 안내 문구(`defaultHint`)는 이 상수로 초 단위를 표시한다.
- **[실측] 팝업 지연**: 개발 모드에서 호버 시작 후 약 1,026ms에 팝업이 보였다.

### 7.2 드래그, 팬, 핀치, 길게 누르기 [코드]

- **드래그 시작**: 마우스 4px, 터치 8px 넘게 움직이면 시작한다. 노드 위면 노드 드래그, 빈 곳이면 팬이다.
- **허브를 잡았을 때**: 별이 `grabConstrict(0.25)`만큼 오므라든다(`grip`). 호버만 해도 `hoverConstrict(0.2)`만큼 오므라든다.
- **놓을 때** (`release`)
  - 최근 포인터 속도 × `throwScale(1.25)`로 던진다. 80ms 넘게 멈춰 있었으면 지수적으로 줄인다.
  - 별 전체에 회복 감쇠(`releaseDamping`)를 건다.
  - 놓은 각도를 새 궤도 제자리로 삼는다.
  - static 노드는 놓은 자리에 남는다. 좌표를 `item.x/y`에 조용히 써서, 다음 재구축에도 유지된다.
- **두 손가락**: 핀치 줌.
- **길게 누르기**: 500ms 이상 움직이지 않으면 `hold` 이벤트를 낸다.
- **휠**: 엔진의 휠 확대는 `interaction.zoomView`가 false면 꺼진다. 앱은 `zoomView:false`로 끄고 `setupWheelZoom`에서 직접 처리한다(커서 아래 고정, `net.moveTo`).

### 7.3 커서 자석과 물결 [코드]

- **자석**: 반경 `magnetRadius(18)/scale` 안의 노드를 커서 쪽으로 살짝 끈다.
- **물결**: 커서를 빠르게 쓸면(`repelGate` 초과, 호버 중 아님, 감속 모션 아님) 반경 `repelRadius(200)/scale` 안의 노드를 밀어낸다.

---

## 8. 카메라 [코드]

- **자동 맞춤 단계** (`_autoFit`): `pending` → `refine` → `done`.
  - `pending`: 첫 보이는 프레임에서 즉시 맞춘다.
  - `refine`: 안정화 때 450ms 애니메이션으로 맞춘다.
- **`_cameraSet`**: 앱이나 사용자가 카메라를 만지면 켜지고, 그 뒤로 자동 맞춤을 하지 않는다. 켜는 경로는 `_cameraTo`를 타는 모든 `fit`/`focus`/`moveTo`, 팬, 핀치, 휠이다. 앱의 휠 확대도 `moveTo`를 쓰므로, 한 번이라도 휠을 굴리면 자동 맞춤이 꺼진다.
- **숨김 변경 시**: `_cameraSet`이 꺼져 있고 `_autoFit === 'done'`이면 `refine`으로 되돌려, 정돈된 뒤 다시 맞춘다. 영역 비율이 바뀌어 다시 짤 때도 같다.
- **`renderer.fitAvoid`**: 앱은 `['#degctl', '#zoomctl']`을 넘긴다. `_fitInsets`는 그래프와 겹치는 컨트롤마다 가장 적게 잘라 내는 변 하나를 안쪽 여백(+8px)으로 삼는다.
  - 각 변의 여백은 W·H의 40%를 넘지 않는다.
  - 같은 계산에서 라벨이 피할 `_avoidRects`도 만든다.
- **맞춤** (`_fitNow`)
  - 여백 = `clamp(min(사용폭, 사용높이) × 0.06, 12, 48)`.
  - 배율은 `fitMaxZoom(1.6)`와 `minZoom(0.05)` 사이로 자른다.
  - 사용 영역(컨트롤 제외)의 가운데에 맞춘다.
- `focus(id)`는 컨테이너 가운데에 맞춘다. 사용 영역은 반영하지 않는다.

---

## 9. 공개 API와 이벤트 (vis-network 호환) [코드]

- **메서드**: `fit`, `focus`, `moveTo`, `getScale`, `getViewPosition`, `DOMtoCanvas`, `canvasToDOM`, `getPosition`, `getPositions`, `getConnectedNodes`, `getConnectedEdges`, `selectNodes`, `selectEdges`, `getSelectedNodes`, `getSelectedEdges`, `unselectAll`, `setOptions`, `stopSimulation`, `startSimulation`, `stabilize`, `setSize`, `redraw`, `destroy`, `rendererInfo`(getter).
- **이벤트**: `click`, `selectNode`, `deselectNode`, `selectEdge`, `deselectEdge`, `hoverNode`, `blurNode`, `dragStart`, `dragEnd`, `hold`, `zoom`, `animationFinished`, `stabilized`.
- **옵션**: `DEFAULT_OPTIONS`(`network.js`)에 `deepMerge`로 합쳐진다. 시뮬레이션 상수는 `options.simulation`으로 `SIM_DEFAULTS`를 덮어쓸 수 있다. 앱은 `nodes`, `edges`, `groups`, `physics`, `interaction`, `renderer.fitAvoid`를 넘긴다. 테마를 바꾸면 `setOptions`로 글자색, 엣지색, 그룹색을 다시 넣는다.

---

## 10. app.js 연결 지점 [코드]

| 위치 | 하는 일 |
| :--- | :--- |
| 첫 로드 `fetch('graph')` 콜백 | DataSet과 Network 생성, 초기 연결 수 필터 결정, 이벤트 연결 |
| `reloadThemeData` | 지식 테마(멀티 테마) 전환·삭제 등으로 그래프를 통째로 다시 받을 때 DataSet을 비우고 다시 채운다. 라이트/다크 전환은 이 경로가 아니라 `toggleTheme` → `setOptions` → `applyView`다 |
| `refreshGraph` | 새 노드·엣지만 추가하고, 기존 노드는 `orbit`/`physics`를 갱신한다 |
| `applyTopologicalStructure` | 고립·쌍의 궤도 띠·순서 지정(좌표는 엔진, §5.3) |
| `applyView` | 숨김, 강조, 투명도를 일괄 반영(`n.degree < curMinDeg`면 숨김) |
| `clusterMatches` / `unclusterEdges` | 검색 결과를 숨은 고정 앵커 `cl_anchor`와 `cl_<n>` 엣지(길이 `CENTER_LEN = 55`)로 화면 중앙에 모은다. 해제하면 tether로 원래 별에 돌아간다. **`cl_` 접두어는 이 용도 전용이다.** |
| `setupWheelZoom` | 휠 확대(엔진 휠 비활성) |
| `hoverNode` / `blurNode` 핸들러 | 호버 강조(`applyHoverHighlight`)와 요약 팝업 타이머 |
| `window.claireDebug` | 읽기 전용 디버그 핸들: `sel`, `highlight`, `selected`, `positions(ids)`, `visibleNodePoints()`, `graphRenderer` 등 |

**[코드] 초기 연결 수 필터 기준이 경로마다 다르다.** 의도된 것인지는 [미검증]이며, 통일하려면 소유자에게 묻는다.

| 경로 | 5 | 2 | 1 | 0 |
| :--- | :--- | :--- | :--- | :--- |
| 첫 로드 | ≥800 | ≥200 | ≥80 | 그 외 |
| `reloadThemeData` | 없음 | ≥200 | ≥80 | 그 외 |
| `resetWorkspaceState` | ≥400 | ≥180 | ≥80 | 그 외 |

---

## 11. 검증 절차 (개발 모드)

### 11.1 자동 테스트

```bash
uv run pytest -q tests/test_graphview.py
```

```bash
cd e2e && npx playwright test workspace.spec.js
```

e2e는 `e2e/playwright.config.js`의 webServer가 `data/e2e.db`를 시드하고 포트 8766에서 서버를 띄운다. 이미 8766을 쓰는 서버가 있으면 실패한다(`reuseExistingServer: false`). 그래프 엔진 e2e는 다음 둘이다.

- `graph engine paints marks and labels through WebGPU or the forced Canvas2D fallback`
- `graph engine lays hubs out as spoke stars and springs a flung leaf back to its tether`

### 11.2 실제 규모 데이터로 보기 (그래프 번들)

e2e 시드 데이터는 작아서 별 무리, 카메라, 라벨 문제를 재현하지 못한다. 운영 규모로 보려면 소유자에게 그래프 번들 링크(`/graph/bundle?token=…`)를 받는다. 토큰은 문서, 커밋, 로그에 남기지 않는다.

1. 받은 `.tar.zst`를 빈 스크래치 디렉터리에 둔다. 가져오기 전에 압축 안의 경로를 확인한다(정상이면 `manifest.json`, `graph_export.db` 두 파일).
2. 별도 DB로 가져온다. 기존 `data/` DB는 건드리지 않는다.
   ```bash
   uv run python -m claire.cli import-graph <번들.tar.zst> --target-db <스크래치>/snapshot.db
   ```
3. 그 DB로 개발 모드 서버를 띄운다(백그라운드).
   ```bash
   CLAIRE_DB_PATH=<스크래치>/snapshot.db CLAIRE_INJECT_PORT=8767 CLAIRE_PUBLIC_URL=http://127.0.0.1:8767/ CLAIRE_ALLOW_INSECURE_HTTP=true CLAIRE_ENVIRONMENT=development CLAIRE_INJECT_TOKEN=<개발용 토큰> uv run python -m claire.cli serve-api
   ```
4. 로컬 DB에는 운영에 없는 형식 안내 배너(`#format-warn-banner`)가 떠서 그래프 영역 높이가 줄어든다. 운영과 비교할 때는 이 배너를 숨기고 측정한다.

### 11.3 엔진 내부 값 읽기

`Network` 인스턴스는 전역으로 노출되지 않는다. Playwright에서 페이지 스크립트보다 먼저 아래 스크립트를 넣으면 `window.__net`으로 잡힌다(`core.js`가 `window.ClaireGraph`를 만들고 `Object.assign`으로 `Network`를 싣는 것을 가로챈다).

```js
await page.addInitScript(() => {
  let cg;
  Object.defineProperty(window, 'ClaireGraph', { configurable: true, get(){ return cg; }, set(v){
    cg = v; let N;
    Object.defineProperty(v, 'Network', { configurable: true, enumerable: true, get(){ return N; }, set(C){
      N = class extends C { constructor(...a){ super(...a); window.__net = this; } };
    }});
  }});
});
```

자주 보는 값:

| 지표 | 표현식 | 해석 |
| :--- | :--- | :--- |
| 배율 | `__net.cam.scale` | 0.05면 최소 배율에 걸린 것 |
| 보이는 루트 수, 깊이 분포 | `sim.visibleRoots.length`, `sim.h.depth` | 깊이는 0–2여야 한다 |
| 루트의 제자리 이탈 | `hypot(sim.x[r] − sim.homeX[r], sim.y[r] − sim.homeY[r])`의 평균 | 첫 프레임에 0이면 첫 표시 정돈 정상 |
| 자동 맞춤 상태 | `__net._autoFit`, `__net._cameraSet` | |
| 호버 대상, 포커스 | `__net.hovered`, `__net.focusAmount` | |
| 겹친 컨트롤 여백, 무리 비율 | `__net._fitInsets()`, `sim.settings.packAspectX` | |
| 노드의 화면 좌표 | `container.getBoundingClientRect()` + `__net._toScreenX(sim.x[i])` | 호버·드래그 재현용 |

헤드리스 Chromium에서 WebGPU를 쓰려면 `--enable-unsafe-webgpu --use-webgpu-adapter=swiftshader`가 필요하다. 이 환경에서는 장치가 곧 손실되어 Canvas2D로 폴백하는 것이 관측됐다(§13).

### 11.4 화면 비교 기준

- 데스크톱 1440×900과 모바일(Playwright `devices['iPhone 13']`)을 라이트·다크(`localStorage.claireTheme = 'dark'`) 모두 본다. 다크 테마는 브라우저 `colorScheme` 에뮬레이션으로는 바뀌지 않는다.
- 기본 보기(앱이 정한 초기 필터)와 전체 보기(`.deg-preset-btn[data-deg="0"]` 클릭)를 본다.
- 로드 직후 0.5초 간격 프레임으로 첫 표시 정돈을 확인한다.

---

## 12. 실측 기록

같은 지표를 다시 잴 때 비교 기준으로 쓴다. 운영 데이터는 계속 늘어나므로 노드 수를 함께 본다.

| 커밋 | 환경·데이터 | 측정 |
| :--- | :--- | :--- |
| `0420aad` (고리 배치) | 운영, 2,625노드, 연결≥5 | 보이는 노드 427, 루트 48, 깊이 최대 8, 고리 반경 6,044px, 배율 0.05(최소값). 별은 점이 되고 교차 엣지만 보였다 |
| `cc2efac` | 개발, 번들 `gb_20261030_143108_7e869214`(2,660 엔티티, 4,307 관계) | 연결≥5: 보이는 노드 432, 배율 0.273. 전체 보기: 고리 시절 바깥 원 공식이면 0.10, 엔진 궤도 배치 후 0.19 |
| `cc2efac` | 운영 배포본, 2,634노드 | 연결≥5 안정 후 배율 0.30. 로드 후 약 4초 동안 루트가 제자리에서 평균 938px(140ms)부터 줄어들며 이동했고, 약 4.6초에 배율이 0.223에서 0.296으로 다시 맞춰졌다. iPhone 13: 그래프 영역 390×418, 배율 0.124, 오른쪽 컨트롤이 약 3분의 1을 덮었다 |
| `99f98e3` | 개발, 같은 번들 | 1440×900 연결≥5: 첫 프레임(약 0.25초)부터 루트 이탈 0, 배율 0.265로 고정(컨트롤 여백을 뺀 맞춤. 여백 반영 전 같은 조건은 0.291). iPhone 13(배너 숨김): 오른쪽 여백 118px, `packAspectX` 0.65, 배율 0.133, 라벨이 컨트롤 밑으로 들어가지 않음. 요약 팝업 약 1,026ms |
| `99f98e3` | 운영 | [미검증] 이 문서 작성 시점에 운영 반영 여부를 확인하지 않았다 |

전체 보기의 밀도 [실측, `cc2efac` 개발]: 가장 큰 별(VMware Cloud Foundation)은 보이는 자식 139, 손자 259다. 자식 tether는 설계값대로 130–240(10·90 백분위)이었다. 전체 보기는 약 2,500노드를 한 화면에 담아 Shopify 참고 화면(약 250개)보다 10배쯤 빽빽하다. 이것은 버그가 아니라 데이터 규모다.

Shopify Editions Fall '26 참고 관찰 [실측, 2026-10-30]:

- 섹션 허브 11개에 잎과 이미지가 달린 별 모양이다.
- 호버하면 그 허브의 스포크가 초록 강조색이 되고, 이웃이 속 빈 고리가 되며, 라벨이 크게 커진다. 나머지는 거의 흐려지지 않는다.
- 끄는 동안 강조가 유지되고, 놓으면 일부만 제자리로 돌아온다.
- 헤드리스 기본 설정에서는 WebGPU 어댑터가 없어 그래프가 검게 나왔다. swiftshader 플래그를 주면 그려졌다.

---

## 13. 알려진 한계와 미검증

- **[미검증] WebGPU 렌더링 화질.** 검증 환경(헤드리스 swiftshader)에서는 장치 손실로 Canvas2D로 폴백했다. 실제 GPU 브라우저에서 WGSL 경로를 눈으로 비교하지 않았다.
- **[코드] 서버 측 좌표 캐시가 없다.** 같은 데이터면 결정적 해시로 같은 지도가 나오지만, 새로고침 사이에 사용자가 끈 위치는 남지 않는다(설계 문서 8절).
- **[코드] 필터를 바꾸면 별 무리가 다시 짜인다.** 별의 상대 위치가 바뀔 수 있다. 별 하나의 모양(숲)은 유지된다.
- **[코드] 프레임당 O(n) 작업.** 호버 재판정(`_hitNode`)과 라벨 후보 정렬(O(n log n))이 매 프레임 전체 노드를 훑는다. 수천 개 규모에서 공간 격자로 바꿀 여지가 있다.
- **[코드] `focus()`는 겹친 컨트롤 영역을 반영하지 않는다(§8).**
- **[코드] `_avoidRects`는 측정(`_measure`)과 맞춤(`_fitNow`) 때만 갱신된다.** 컨트롤이 크기 변화 없이 나타나거나 사라지면 다음 측정까지 옛 영역을 피한다.
- **[코드] 초기 연결 수 필터 기준이 세 경로에서 다르다(§10).**

---

## 14. 변경 레시피

| 하려는 일 | 고칠 곳 | 함께 확인할 것 |
| :--- | :--- | :--- |
| 그래프 위에 새 컨트롤 추가 | app.js Network 옵션 `renderer:{fitAvoid:[…]}`에 선택자 추가 | 모바일에서 `_fitInsets()`가 맞는 변을 고르는지 |
| 요약 팝업 지연 변경 | app.js `NODE_POP_DELAY_MS` | 마커 테스트 `const NODE_POP_DELAY_MS = 1000;` |
| 레이아웃 상수 조정 | `simulation.js` `SIM_DEFAULTS` 또는 앱의 `options.simulation` | §11.3 지표(배율, 루트 이탈, 깊이)를 번들 데이터로 전후 비교 |
| 렌더 상수 조정(흐림, 라벨, 줌) | `network.js` `DEFAULT_OPTIONS.renderer` | 라이트·다크와 모바일 화면 |
| 고립·쌍 분류 규칙 변경 | app.js `applyTopologicalStructure`(띠·순서만) | 좌표를 앱에서 넣지 않는다. 엔진 `_placeOrbits` 띠 번호 0/1 |
| 노드에 새 시각 상태 추가 | 앱은 항목 필드(`borderWidth`, `opacity`, `color`)로 표현. 새 모양이 필요하면 `network.js` `_draw`에서 `frame.pushNode` | 두 렌더러가 같은 `SHAPE_*`를 지원하는지(`renderers.js`) |
| 새 이벤트 | `network.js`에서 `this.emit('<이름>', payload)`, app.js에서 `net.on` | 마커 테스트의 이벤트 목록(`test_graph_engine_interaction_model`) |
| 노드 크기·글자 크기 규칙 | app.js `nodeRadius`, `nodeFontSize` | 크기 변경은 `_structureChanged`를 거쳐 재구축을 일으킨다 |
| 새 엔진 파일 추가 | `graphview.py` `_GRAPH_ENGINE_FILES`(의존 순서) | 마커 테스트, 외부 의존성 없음 원칙 |
