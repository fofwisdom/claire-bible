/**
 * Claire Graph Engine — 계층형 물리 시뮬레이션.
 *
 * Shopify Editions 그래프의 물리 모델(루트 → 섹션 → 업데이트 트리, 자식은 부모에 tether 로
 * 묶여 각도·길이를 유지하며 천천히 궤도 회전, 섹션끼리는 반발, 커서 자석·물결, 잡아 던지기,
 * 결정적 시드 배치 + 워밍업)을 일반 지식 그래프에 맞게 옮긴다. 지식 그래프는 트리가 아니므로
 * 연결 수 상위 노드를 허브(섹션)로 뽑고, 허브에서 다중 출발 BFS 로 각 노드의 주 부모를 정해
 * 신장 숲(spanning forest)을 만든다. 숲은 Shopify 처럼 허브 → 자식 → 손자 2단으로 접는다. 숲에
 * 들지 않은 관계(교차 엣지)는 그리기만 하고, 허브 간 친화도(서로 다른 별 사이 관계 수)로 바꿔
 * 관련 주제의 별이 화면 위에서 이웃하게 놓는다.
 *
 * 좌표계는 화면과 무관한 월드 px 이다(카메라가 확대·이동을 맡는다).
 */
(function(global){
  'use strict';
  const CG = global.ClaireGraph;
  const { clamp, approach, hash32, hashSigned, hashUnit } = CG;

  // 노드 참여 방식: 숨김(off) / 시뮬레이션(active) / 고정 장애물(static) / 숨은 고정 앵커(anchor)
  const MODE_OFF = 0, MODE_ACTIVE = 1, MODE_STATIC = 2, MODE_ANCHOR = 3;

  const SIM_DEFAULTS = {
    hubShare: 0.6,          // 허브 수 ≈ √N × hubShare
    maxHubs: 32,
    minHubDegree: 3,
    maxDepth: 2,            // 허브 → 자식 → 손자. 더 깊은 노드는 깊이 1 조상에 붙인다(사슬이 별을 키우지 않게)
    linkLength: 62,         // 허브 직계 자식 tether 기본 길이
    nestedLength: 30,       // 손자 이하 tether 기본 길이
    lengthJitter: 1,        // 스포크 길이를 4^(±jitter/3) 범위로 흩뜨려 별 모양을 만든다
    starGrowth: 22,         // 자식 수가 이보다 많으면 √비율로 tether 를 늘린다
    packAspectX: 1.4,       // 별 무리를 가로로 늘려 넓은 화면을 채운다
    packGap: 26,
    starOverlap: 0.8,       // 이웃 별의 스포크 끝이 서로 맞물리도록 별 반경을 이만큼만 비워 둔다
    collidePad: 3,
    restitution: 0.6,
    velDamp: 2.5,
    maxSpeed: 1400,
    linkStiffness: 18,
    linkDamp: 1,
    tetherRate: 14,         // 위치 기반 tether 보정 속도(1/s)
    homeStrength: 1.2,      // 궤도 위 제자리(각도)로 끄는 힘
    packStiffness: 3.5,
    separation: 7,
    freeLength: 70,
    centerGravity: 0.35,
    orbitSpeed: 0.012,      // rad/s — 별 하나가 한 바퀴 도는 데 약 9분
    hoverSlow: 0.2,
    hoverConstrict: 0.2,
    grabConstrict: 0.25,
    grabResponse: 30,
    releaseRelaxation: 3,
    releaseDamping: 14,
    throwScale: 1.25,
    magnet: 1, magnetRadius: 18,
    cursorRepel: 2, repelRadius: 200, repelInner: 24, repelGate: 24,
    repelMaxSpeed: 1800, repelCarry: 1.45, repelShield: 1,
    anchorStiffness: 6,
    orbitGap: 60,           // 별 무리 경계 상자에서 첫 바깥 궤도까지
    orbitBandGap: 48,       // 2노드 쌍 띠와 고립 노드 띠 사이
    orbitRingGap: 36,       // 한 띠가 넘칠 때 다음 궤도까지
    orbitSpacing: 24,       // 궤도 위 고립 노드 간격
    pairSpacing: 36,        // 궤도 위 2노드 쌍 간격
    warmupSteps: 160, warmupBudgetMs: 45,
  };

  // --- 균일 격자 공간 해시: 충돌·근접 질의를 O(n) 으로 ---
  class SpatialGrid {
    constructor(){ this.cell = 24; this.mask = 0; this.heads = new Int32Array(0); this.next = new Int32Array(0); }
    build(xs, ys, include, n, cell){
      this.cell = cell;
      let size = 64; while(size < n * 2) size <<= 1;
      if(this.heads.length !== size) this.heads = new Int32Array(size);
      this.heads.fill(-1);
      if(this.next.length < n) this.next = new Int32Array(Math.max(64, n * 2));
      this.mask = size - 1;
      for(let i = 0; i < n; i++){
        if(!include(i)) continue;
        const k = this._key(Math.floor(xs[i] / cell), Math.floor(ys[i] / cell));
        this.next[i] = this.heads[k]; this.heads[k] = i;
      }
    }
    _key(cx, cy){ return (Math.imul(cx, 73856093) ^ Math.imul(cy, 19349663)) & this.mask; }
    // (x, y) 주변 3×3 셀의 후보를 순회한다(해시 충돌로 먼 노드가 섞일 수 있어 호출자가 거리로 거른다).
    forNear(x, y, fn){
      const cx = Math.floor(x / this.cell), cy = Math.floor(y / this.cell);
      for(let ox = -1; ox <= 1; ox++) for(let oy = -1; oy <= 1; oy++){
        for(let j = this.heads[this._key(cx + ox, cy + oy)]; j !== -1; j = this.next[j]) fn(j);
      }
    }
  }

  // --- 신장 숲(hierarchy) 구축 ---
  // eligible: 계층 참여 여부(앵커·physics:false 제외). adj: CSR 인접(실제 관계만).
  function buildHierarchy(topo, settings){
    const n = topo.ids.length;
    const { eligible, adjStart, adjList } = topo;
    const degree = new Int32Array(n);
    let nEligible = 0;
    for(let i = 0; i < n; i++){
      if(!eligible[i]) continue;
      nEligible++;
      for(let k = adjStart[i]; k < adjStart[i + 1]; k++) if(eligible[adjList[k]]) degree[i]++;
    }
    const tie = i => hash32(topo.ids[i], 11);
    const byDegree = (a, b) => (degree[b] - degree[a]) || (tie(a) - tie(b));

    const hubCount = clamp(Math.round(Math.sqrt(nEligible) * settings.hubShare), 1, settings.maxHubs);
    const candidates = [];
    for(let i = 0; i < n; i++) if(eligible[i] && degree[i] >= settings.minHubDegree) candidates.push(i);
    candidates.sort(byDegree);
    const hubs = candidates.slice(0, hubCount);

    const parent = new Int32Array(n).fill(-1);
    const depth = new Int32Array(n).fill(-1);
    const order = [];
    const roots = [];
    // 다중 출발 BFS: 같은 층에서 연결 수가 큰 부모가 먼저 자식을 가져가도록 층마다 정렬한다.
    const bfs = seeds => {
      let frontier = seeds.slice();
      for(const r of frontier){ depth[r] = 0; order.push(r); roots.push(r); }
      while(frontier.length){
        frontier.sort(byDegree);
        const nextFrontier = [];
        for(const p of frontier){
          for(let k = adjStart[p]; k < adjStart[p + 1]; k++){
            const c = adjList[k];
            if(!eligible[c] || depth[c] !== -1) continue;
            depth[c] = depth[p] + 1; parent[c] = p;
            order.push(c); nextFrontier.push(c);
          }
        }
        frontier = nextFrontier;
      }
    };
    bfs(hubs);
    // 허브에 닿지 않는 작은 연결 요소: 요소마다 연결 수 최대 노드를 루트로 삼는다.
    const rest = [];
    for(let i = 0; i < n; i++) if(eligible[i] && depth[i] === -1) rest.push(i);
    rest.sort(byDegree);
    for(const r of rest) if(depth[r] === -1) bfs([r]);
    // 깊은 사슬은 층마다 하위 반경이 누적돼 별 하나가 화면 전체만큼 커진다. order 는 부모가 먼저 오므로
    // 위에서부터 차례로 maxDepth 를 넘는 노드를 maxDepth - 1 층 조상의 자식으로 옮긴다.
    for(const i of order){
      if(depth[i] <= settings.maxDepth) continue;
      let a = parent[i];
      while(depth[a] >= settings.maxDepth) a = parent[a];
      parent[i] = a; depth[i] = depth[a] + 1;
    }

    // 자식 목록: 타입(group)별로 묶어 별 안에서 같은 색이 부채꼴을 이루게 한다.
    const children = Array.from({ length: n }, () => []);
    for(const i of order) if(parent[i] >= 0) children[parent[i]].push(i);
    const groupOf = topo.group;
    for(const list of children){
      if(list.length > 1) list.sort((a, b) => String(groupOf[a]).localeCompare(String(groupOf[b])) || byDegree(a, b));
    }

    // tether 기본 길이와 하위 트리 반경(ext)을 잎에서 위로 계산한다.
    const radius = topo.radius;
    const tetherBase = new Float64Array(n);
    const ext = new Float64Array(n);
    for(let i = 0; i < n; i++) ext[i] = radius[i];
    for(let k = order.length - 1; k >= 0; k--){
      const i = order[k], p = parent[i];
      if(p < 0) continue;
      const siblings = children[p].length;
      const base = depth[i] === 1
        ? settings.linkLength * Math.max(1, Math.sqrt(siblings / settings.starGrowth))
        : settings.nestedLength * Math.max(1, Math.sqrt(siblings / 8));
      const jitter = Math.pow(4, hashSigned(topo.ids[i], 29) * settings.lengthJitter / 3);
      tetherBase[i] = base * jitter + (ext[i] - radius[i]) * 0.7 + radius[p] + radius[i];
      ext[p] = Math.max(ext[p], tetherBase[i] + ext[i]);
    }

    // 별 사이 친화도: 서로 다른 루트에 속한 두 노드를 잇는 관계 수.
    const rootOf = new Int32Array(n).fill(-1);
    for(const i of order) rootOf[i] = parent[i] < 0 ? i : rootOf[parent[i]];
    const affinity = new Map();
    for(let i = 0; i < n; i++){
      if(rootOf[i] < 0) continue;
      for(let k = adjStart[i]; k < adjStart[i + 1]; k++){
        const j = adjList[k];
        if(j <= i || rootOf[j] < 0 || rootOf[j] === rootOf[i]) continue;
        const a = Math.min(rootOf[i], rootOf[j]), b = Math.max(rootOf[i], rootOf[j]);
        const key = a * n + b;
        affinity.set(key, (affinity.get(key) || 0) + 1);
      }
    }
    const affOf = (a, b) => affinity.get(Math.min(a, b) * n + Math.max(a, b)) || 0;
    // 배치 순서: 큰 별부터(가운데를 차지한다). 궤도 방향은 이 순서로 번갈아 준다.
    const rootOrder = roots.slice().sort((a, b) => (ext[b] - ext[a]) || (tie(a) - tie(b)));
    return { degree, parent, depth, order, roots, children, tetherBase, ext, rootOf, affOf, rootOrder };
  }

  // 별 무리 배치: 큰 별부터 하나씩, 이미 놓인 별에 맞닿는 후보 자리 가운데 (가로로 늘린) 중심에서
  // 가깝고 친화도 높은 별에 가까운 곳을 고른다. 고리와 달리 가운데가 비지 않아 별이 커도 화면을 채운다.
  function packStars(roots, radiusOf, affOf, idOf, s){
    const ax = s.packAspectX, gap = s.packGap, home = new Map(), placed = [];
    const pending = roots.slice();
    while(pending.length){
      // 이미 놓인 별과 친화도가 가장 큰 별을 다음으로(없으면 남은 것 중 가장 큰 별).
      let pick = 0, bestAff = 0;
      for(let k = 0; k < pending.length; k++){
        let a = 0;
        for(const q of placed) a += affOf(pending[k], q[0]);
        if(a > bestAff){ bestAff = a; pick = k; }
      }
      const r = pending.splice(pick, 1)[0], rad = radiusOf(r);
      if(!placed.length){ placed.push([r, 0, 0, rad]); continue; }
      let bx = 0, by = 0, best = Infinity;
      const turn = hashUnit(idOf(r), 7) * Math.PI * 2;
      for(const q of placed){
        const d = q[3] + rad + gap;
        for(let t = 0; t < 24; t++){
          const ang = turn + t / 24 * Math.PI * 2;
          const x = q[1] + Math.cos(ang) * d, y = q[2] + Math.sin(ang) * d;
          let clear = true, wsum = 0, wd = 0;
          for(const o of placed){
            const dd = Math.hypot(x - o[1], y - o[2]);
            if(dd < o[3] + rad + gap - 1e-6){ clear = false; break; }
            const w = affOf(r, o[0]);
            if(w){ wsum += w; wd += w * dd; }
          }
          if(!clear) continue;
          const score = Math.hypot(x / ax, y) + (wsum ? 0.6 * wd / wsum : 0);
          if(score < best){ best = score; bx = x; by = y; }
        }
      }
      placed.push([r, bx, by, rad]);
    }
    // 경계 상자 가운데를 원점에 맞춘다(자유 노드의 중심 중력·카메라 맞춤 기준).
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for(const [, x, y, rad] of placed){ x0 = Math.min(x0, x - rad); x1 = Math.max(x1, x + rad); y0 = Math.min(y0, y - rad); y1 = Math.max(y1, y + rad); }
    const cx = placed.length ? (x0 + x1) / 2 : 0, cy = placed.length ? (y0 + y1) / 2 : 0;
    for(const [r, x, y] of placed) home.set(r, [x - cx, y - cy]);
    return home;
  }

  class Simulation {
    constructor(settings){
      this.settings = Object.assign({}, SIM_DEFAULTS, settings || {});
      this.n = 0;
      this.ids = [];
      this.time = 0;
      this.orbitPhase = 0;
      this.calm = 0;
      this.meanSpeed = 0;
      this.homeX = new Float64Array(0);
      this.homeY = new Float64Array(0);
      this.grid = new SpatialGrid();
      this.springs = [];
      this.visibleRoots = [];
      this.h = null;
    }

    // 구조가 바뀔 때(노드·관계 추가/삭제, physics/fixed 변경) 호출. 기존 좌표는 id 로 이어받는다.
    setTopology(topo){
      const prev = new Map();
      for(let i = 0; i < this.n; i++) prev.set(this.ids[i], [this.x[i], this.y[i], this.vx[i], this.vy[i]]);
      const n = this.n = topo.ids.length;
      this.ids = topo.ids;
      this.topo = topo;
      this.x = new Float64Array(n); this.y = new Float64Array(n);
      this.vx = new Float64Array(n); this.vy = new Float64Array(n);
      this.fx = new Float64Array(n); this.fy = new Float64Array(n);
      this.radius = topo.radius;
      this.mode = new Uint8Array(n);
      this.effParent = new Int32Array(n).fill(-1);
      this.tether = new Float64Array(n);
      this.homeAngle = new Float64Array(n);
      this.grip = new Float32Array(n);
      this.hoverGrip = new Float32Array(n);
      this.recovery = new Float32Array(n);
      this.pulled = new Uint8Array(n);
      this.visExt = new Float64Array(n);
      this.homeX = new Float64Array(n); this.homeY = new Float64Array(n);
      this.orbitSign = new Int8Array(n).fill(1);
      const h = this.h = buildHierarchy(topo, this.settings);
      h.rootOrder.forEach((r, k) => { this.orbitSign[r] = k % 2 ? -1 : 1; });
      for(const i of h.order) if(h.parent[i] >= 0) this.orbitSign[i] = this.orbitSign[h.rootOf[i]];
      for(let i = 0; i < n; i++) this.tether[i] = h.tetherBase[i];
      // 질량 ∝ 자식 수: 큰 허브는 잎들의 반작용·충돌에 휘둘리지 않는다(역질량으로 힘을 나눈다).
      this.invMass = new Float64Array(n);
      for(let i = 0; i < n; i++) this.invMass[i] = 1 / (1 + 0.5 * h.children[i].length);

      const placed = new Uint8Array(n);
      for(let i = 0; i < n; i++){
        const keep = prev.get(topo.ids[i]);
        if(Number.isFinite(topo.fixedX[i])){
          this.x[i] = topo.fixedX[i]; this.y[i] = topo.fixedY[i]; placed[i] = 1;
        } else if(keep){
          this.x[i] = keep[0]; this.y[i] = keep[1]; this.vx[i] = keep[2]; this.vy[i] = keep[3]; placed[i] = 1;
        } else if(Number.isFinite(topo.initX[i])){
          this.x[i] = topo.initX[i]; this.y[i] = topo.initY[i]; placed[i] = 1;
        }
      }
      this.springs = topo.springs || [];
      for(const sp of this.springs) this.pulled[sp.b] = 1;
      this.setHidden(topo.hidden);
      this._seedLayout(placed);
    }

    // 숨김만 바뀔 때(연결 수 필터·격리 보기 등) — 계층은 유지하고 참여 모드·유효 부모·고리 반경만 갱신.
    setHidden(hidden){
      const n = this.n, topo = this.topo, h = this.h;
      if(!h) return;
      for(let i = 0; i < n; i++){
        const fixed = Number.isFinite(topo.fixedX[i]);
        if(topo.anchor[i]) this.mode[i] = MODE_ANCHOR;
        else if(hidden[i]) this.mode[i] = MODE_OFF;
        else if(fixed || !topo.eligible[i]) this.mode[i] = MODE_STATIC;
        else this.mode[i] = MODE_ACTIVE;
      }
      this.hidden = hidden;
      // 부모가 숨겨지면 가장 가까운 보이는 조상에 묶는다. 없으면 자유 노드(-1).
      for(const i of h.order){
        let p = h.parent[i];
        while(p >= 0 && this.mode[p] !== MODE_ACTIVE && this.mode[p] !== MODE_STATIC) p = h.parent[p];
        this.effParent[i] = p;
      }
      // 보이는 노드만으로 별 반경을 다시 재서 별 무리를 다시 짠다(필터로 별이 작아지면 무리도 좁힌다).
      const visExt = this.visExt;
      for(let i = 0; i < n; i++) visExt[i] = this.radius[i];
      for(let k = h.order.length - 1; k >= 0; k--){
        const i = h.order[k], p = this.effParent[i];
        if(p < 0 || this.mode[i] !== MODE_ACTIVE) continue;
        visExt[p] = Math.max(visExt[p], this.tether[i] + visExt[i]);
      }
      this.visibleRoots = h.rootOrder.filter(r => this.mode[r] === MODE_ACTIVE);
      const s = this.settings;
      // 필터 뒤 보이는 크기로 다시 줄 세워 가장 큰 별이 가운데를 차지하게 한다.
      const bySize = this.visibleRoots.slice().sort((a, b) => (visExt[b] - visExt[a]) || (hash32(this.ids[a], 11) - hash32(this.ids[b], 11)));
      const home = packStars(bySize, r => visExt[r] * s.starOverlap, h.affOf, r => String(this.ids[r]), s);
      for(const [r, [x, y]] of home){ this.homeX[r] = x; this.homeY[r] = y; }
      this._placeOrbits();
    }

    // 바깥 궤도(app 의 orbit 띠): 고립·2노드 쌍을 별 무리 경계 상자 바로 바깥 타원에 둔다. 띠 0 은
    // 2노드 쌍, 띠 1 은 고립 노드이며 보이는 것만 index 순으로 고르게 펼친다. 넘치면 바깥 궤도를 더한다.
    // 사용자가 끌어다 놓아 좌표가 생긴 노드(fixedX)는 놓은 자리를 지킨다.
    _placeOrbits(){
      const topo = this.topo, s = this.settings, n = this.n;
      if(!topo.orbitBand) return;
      const bands = [[], []];
      for(let i = 0; i < n; i++){
        const b = topo.orbitBand[i];
        if(b < 0 || b > 1 || this.mode[i] !== MODE_STATIC || Number.isFinite(topo.fixedX[i])) continue;
        bands[b].push(i);
      }
      if(!bands[0].length && !bands[1].length) return;
      let rx = 0, ry = 0;
      for(const r of this.visibleRoots){
        rx = Math.max(rx, Math.abs(this.homeX[r]) + this.visExt[r]);
        ry = Math.max(ry, Math.abs(this.homeY[r]) + this.visExt[r]);
      }
      // 타원 둘레(Ramanujan 근사)
      const perimeter = (a, b) => Math.PI * (3 * (a + b) - Math.sqrt((3 * a + b) * (a + 3 * b)));
      let a = rx + s.orbitGap, b = ry + s.orbitGap;
      const byIndex = (p, q) => (topo.orbitIndex[p] - topo.orbitIndex[q]) || (topo.orbitSide[p] - topo.orbitSide[q]);
      if(bands[0].length){
        // 같은 index 의 두 노드가 한 쌍 — 쌍 단위로 각도를 나누고 좌우(side)로 벌린다.
        const slots = [...new Set(bands[0].map(i => topo.orbitIndex[i]))].sort((p, q) => p - q);
        const grow = Math.max(1, slots.length * s.pairSpacing / perimeter(a, b));
        a *= grow; b *= grow;
        const slotOf = new Map(slots.map((v, k) => [v, k]));
        for(const i of bands[0]){
          const ang = 2 * Math.PI * slotOf.get(topo.orbitIndex[i]) / slots.length;
          this.x[i] = Math.cos(ang) * a + topo.orbitSide[i] * 9;
          this.y[i] = Math.sin(ang) * b;
        }
        a += s.orbitBandGap; b += s.orbitBandGap;
      }
      const iso = bands[1].sort(byIndex);
      for(let k = 0; k < iso.length;){
        const cap = Math.max(1, Math.floor(perimeter(a, b) / s.orbitSpacing));
        const count = Math.min(cap, iso.length - k);
        for(let j = 0; j < count; j++){
          const i = iso[k + j], ang = 2 * Math.PI * j / count;
          this.x[i] = Math.cos(ang) * a; this.y[i] = Math.sin(ang) * b;
        }
        k += count;
        a += s.orbitRingGap; b += s.orbitRingGap;
      }
      for(const list of bands) for(const i of list){ this.vx[i] = 0; this.vy[i] = 0; }
    }

    // 결정적 시드 배치: 루트는 별 무리의 제자리에 놓고, 자식은 부모 둘레(손자는 바깥쪽 부채꼴)에
    // 펼친다. 이미 좌표가 있는 노드는 현재 상대 각도를 제자리 각도로 삼는다.
    _seedLayout(placed){
      const h = this.h, s = this.settings;
      for(const r of h.rootOrder){
        this.homeAngle[r] = Math.atan2(this.homeY[r], this.homeX[r]);
        if(!placed[r]){ this.x[r] = this.homeX[r]; this.y[r] = this.homeY[r]; }
      }
      for(const p of h.order){
        const kids = h.children[p];
        if(!kids.length) continue;
        const outward = this.homeAngle[p];
        const nested = h.parent[p] >= 0;
        let spread = Math.PI * 2;
        if(nested){
          const need = kids.length * (2 * 4 + s.collidePad) / Math.max(1, this.tether[kids[0]]);
          spread = clamp(Math.max(Math.PI * 2 / 3, need), 0, Math.PI * 2);
        }
        kids.forEach((c, j) => {
          let ang;
          if(spread >= Math.PI * 2 - 1e-6) ang = outward + j / kids.length * Math.PI * 2;
          else ang = outward + (kids.length > 1 ? (j / (kids.length - 1) - 0.5) * spread : 0);
          if(placed[c]){
            ang = Math.atan2(this.y[c] - this.y[p], this.x[c] - this.x[p]) - this.orbitSign[c] * this.orbitPhase;
          } else {
            this.x[c] = this.x[p] + Math.cos(ang) * this.tether[c];
            this.y[c] = this.y[p] + Math.sin(ang) * this.tether[c];
          }
          this.homeAngle[c] = ang;
        });
      }
      // 계층 밖 노드(자유·앵커 없는 static 제외)는 원점 근처 결정적 위치에 둔다.
      for(let i = 0; i < this.n; i++){
        if(placed[i] || h.depth[i] >= 0 || (this.topo.orbitBand && this.topo.orbitBand[i] >= 0)) continue;
        const a = hashSigned(this.ids[i], 3) * Math.PI, r = 40 + Math.abs(hashSigned(this.ids[i], 5)) * 80;
        this.x[i] = Math.cos(a) * r; this.y[i] = Math.sin(a) * r;
      }
    }

    // 화면에 그리기 전에 미리 시뮬레이션을 돌려 정돈된 상태로 첫 프레임을 낸다(예산 제한).
    warmup(){
      const s = this.settings, t0 = performance.now();
      const io = { pointer: null, hovered: -1, dragging: -1, selected: false, ambient: false, scale: 1 };
      for(let k = 0; k < s.warmupSteps; k++){
        this.step(1 / 60, io);
        if(performance.now() - t0 > s.warmupBudgetMs) break;
      }
      this.vx.fill(0); this.vy.fill(0);
    }

    step(dt, io){
      const n = this.n;
      if(!n || !this.h) return 0;
      const s = this.settings;
      this.time += dt;
      const calmTarget = io.selected || io.hovered >= 0 ? 1 : 0;
      this.calm += (calmTarget - this.calm) * approach(10, dt);
      const pace = io.ambient ? (io.selected ? 0 : 1 - (1 - s.hoverSlow) * this.calm) : 0;
      this.orbitPhase += s.orbitSpeed * pace * dt;
      this.fx.fill(0); this.fy.fill(0);
      this._updateGrips(dt, io);
      this._packForces();
      this._tetherForces(io);
      this._freeForces();
      this._anchorForces();
      this._pointerForces(io, pace);
      this._integrate(dt, io);
      this._projectTethers(dt, io);
      this._collide(io);
      return this.meanSpeed;
    }

    _updateGrips(dt, io){
      const s = this.settings, h = this.h, n = this.n;
      for(let i = 0; i < n; i++){
        const grabbed = i === io.dragging && h.children[i].length > 0;
        this.grip[i] += ((grabbed ? 1 : 0) - this.grip[i]) * (grabbed ? Math.min(1, dt * s.grabResponse) : approach(s.releaseRelaxation, dt));
        const hovered = i === io.hovered && h.children[i].length > 0;
        this.hoverGrip[i] += ((hovered ? 1 : 0) - this.hoverGrip[i]) * approach(hovered ? 15 : s.releaseRelaxation, dt);
        this.recovery[i] *= Math.exp(-s.releaseRelaxation * dt * 0.5);
      }
      for(const i of h.order){
        const p = this.effParent[i];
        if(p < 0) continue;
        const constrict = Math.max(this.grip[p] * s.grabConstrict, this.hoverGrip[p] * s.hoverConstrict);
        const target = h.tetherBase[i] * (1 - constrict);
        this.tether[i] += (target - this.tether[i]) * approach(12, dt);
      }
    }

    // 루트(허브): 별 무리의 제자리로 끌고, 서로 겹치지 않게 민다(놓친 별도 결국 제자리로 돌아온다).
    _packForces(){
      const s = this.settings, roots = this.visibleRoots;
      const { x, y, fx, fy, visExt } = this;
      for(const r of roots){
        fx[r] += (this.homeX[r] - x[r]) * s.packStiffness;
        fy[r] += (this.homeY[r] - y[r]) * s.packStiffness;
      }
      for(let a = 0; a < roots.length; a++){
        const i = roots[a];
        for(let b = a + 1; b < roots.length; b++){
          const j = roots[b];
          let dx = x[j] - x[i], dy = y[j] - y[i];
          let d = Math.hypot(dx, dy);
          if(d < 1e-3){ dx = (a - b) * 0.01; dy = 0.007; d = Math.hypot(dx, dy); }
          const minSep = (visExt[i] + visExt[j]) * s.starOverlap + s.packGap;
          if(d >= minSep) continue;
          const push = s.separation * (minSep - d);
          fx[i] -= dx / d * push; fy[i] -= dy / d * push;
          fx[j] += dx / d * push; fy[j] += dy / d * push;
        }
      }
    }

    // 자식: 부모와의 tether(임계 감쇠 스프링) + 궤도 회전하는 제자리 각도로 끄는 힘.
    _tetherForces(io){
      const s = this.settings, h = this.h;
      const { x, y, vx, vy, fx, fy, mode } = this;
      for(const i of h.order){
        const p = this.effParent[i];
        if(p < 0 || mode[i] !== MODE_ACTIVE || this.pulled[i]) continue;
        const dx = x[i] - x[p], dy = y[i] - y[p];
        const d = Math.hypot(dx, dy) || 1e-3, ux = dx / d, uy = dy / d;
        const L = this.tether[i];
        const k = s.linkStiffness * (io.dragging === i || io.dragging === p ? 2 : 1);
        const relV = (vx[i] - vx[p]) * ux + (vy[i] - vy[p]) * uy;
        const F = k * (d - L) + 2 * Math.sqrt(k) * s.linkDamp * relV;
        fx[i] -= ux * F; fy[i] -= uy * F;
        if(mode[p] === MODE_ACTIVE && p !== io.dragging){
          const back = F * 0.35 * this.invMass[p];
          fx[p] += ux * back; fy[p] += uy * back;
        }
        const ang = this.homeAngle[i] + this.orbitSign[i] * this.orbitPhase;
        fx[i] += (x[p] + Math.cos(ang) * L - x[i]) * s.homeStrength;
        fy[i] += (y[p] + Math.sin(ang) * L - y[i]) * s.homeStrength;
      }
    }

    // 보이는 조상이 없는 노드: 보이는 이웃과 스프링, 원점으로 약한 중력.
    _freeForces(){
      const s = this.settings, h = this.h, topo = this.topo;
      const { x, y, fx, fy, mode } = this;
      for(let i = 0; i < this.n; i++){
        if(mode[i] !== MODE_ACTIVE || this.pulled[i]) continue;
        if(h.depth[i] === 0 || this.effParent[i] >= 0) continue;
        fx[i] -= x[i] * s.centerGravity; fy[i] -= y[i] * s.centerGravity;
        for(let k = topo.adjStart[i]; k < topo.adjStart[i + 1]; k++){
          const j = topo.adjList[k];
          if(mode[j] !== MODE_ACTIVE && mode[j] !== MODE_STATIC) continue;
          const dx = x[j] - x[i], dy = y[j] - y[i], d = Math.hypot(dx, dy) || 1e-3;
          const F = 4 * (d - s.freeLength);
          fx[i] += dx / d * F; fy[i] += dy / d * F;
        }
      }
    }

    // 검색 뭉치기용 숨은 앵커 스프링(app 의 cl_anchor / cl_* 엣지).
    _anchorForces(){
      const s = this.settings;
      const { x, y, vx, vy, fx, fy, mode } = this;
      for(const sp of this.springs){
        const a = sp.a, b = sp.b;
        if(mode[b] !== MODE_ACTIVE) continue;
        const dx = x[a] - x[b], dy = y[a] - y[b], d = Math.hypot(dx, dy) || 1e-3;
        const k = s.anchorStiffness;
        const relV = -(vx[b] * dx + vy[b] * dy) / d;
        const F = k * (d - sp.length) + 2 * Math.sqrt(k) * relV;
        fx[b] += dx / d * F; fy[b] += dy / d * F;
      }
    }

    // 커서: 가까운 노드를 살짝 끌어당기는 자석 + 빠르게 쓸고 지나갈 때 밀어내는 물결.
    _pointerForces(io, pace){
      const p = io.pointer;
      if(!p || io.dragging >= 0) return;
      const s = this.settings, scale = io.scale || 1;
      const { x, y, fx, fy, mode } = this;
      const magR = s.magnetRadius / scale;
      for(let i = 0; i < this.n; i++){
        if(mode[i] !== MODE_ACTIVE) continue;
        const dx = p[0] - x[i], dy = p[1] - y[i], d = Math.hypot(dx, dy);
        if(d >= magR) continue;
        const k = Math.pow(Math.max(0, 1 - (d / magR) * (d / magR)), 2);
        const f = s.magnet * 900 * k / Math.max(8 / scale, d);
        fx[i] += dx * f; fy[i] += dy * f;
      }
      const pv = io.pointerVel;
      if(!pv || io.hovered >= 0 || io.reduced) return;
      const speed = Math.hypot(pv[0], pv[1]);
      if(speed <= s.repelGate / scale) return;
      const sp = Math.min(speed, s.repelMaxSpeed / scale), ux = pv[0] / speed, uy = pv[1] / speed;
      const outer = s.repelRadius / scale, inner = Math.max(s.repelInner, s.magnetRadius) / scale;
      for(let i = 0; i < this.n; i++){
        if(mode[i] !== MODE_ACTIVE) continue;
        const dx = x[i] - p[0], dy = y[i] - p[1], d = Math.hypot(dx, dy);
        if(d <= inner || d >= outer) continue;
        const L = 1 - (d - inner) / (outer - inner);
        const ahead = (dx * ux + dy * uy) / d;
        const shield = 1 - s.repelShield * Math.max(0, ahead);
        const k = L * L * s.cursorRepel * shield * Math.max(pace, 0.4) * (this.h.depth[i] === 0 ? 0.6 : 1);
        fx[i] += dx / d * k * sp + ux * k * sp * s.repelCarry;
        fy[i] += dy / d * k * sp + uy * k * sp * s.repelCarry;
      }
    }

    _integrate(dt, io){
      const s = this.settings, h = this.h;
      const { x, y, vx, vy, fx, fy, mode } = this;
      let sum = 0, count = 0;
      for(let i = 0; i < this.n; i++){
        if(mode[i] !== MODE_ACTIVE) continue;
        if(i === io.dragging){
          if(io.dragIdle > 0.08){ vx[i] *= Math.exp(-dt / 0.06); vy[i] *= Math.exp(-dt / 0.06); }
          continue;
        }
        const r = h.rootOf[i] >= 0 ? this.recovery[h.rootOf[i]] : 0;
        const damp = Math.exp(-(s.velDamp + s.releaseDamping * r) * dt);
        let nvx = (vx[i] + (Number.isFinite(fx[i]) ? fx[i] : 0) * dt) * damp;
        let nvy = (vy[i] + (Number.isFinite(fy[i]) ? fy[i] : 0) * dt) * damp;
        const sp = Math.hypot(nvx, nvy);
        if(sp > s.maxSpeed){ nvx *= s.maxSpeed / sp; nvy *= s.maxSpeed / sp; }
        vx[i] = Number.isFinite(nvx) ? nvx : 0; vy[i] = Number.isFinite(nvy) ? nvy : 0;
        x[i] += vx[i] * dt; y[i] += vy[i] * dt;
        sum += Math.hypot(vx[i], vy[i]); count++;
      }
      this.meanSpeed = count ? sum / count : 0;
    }

    // 위치 기반 tether 보정: 부모부터 차례로 자식을 목표 길이로 끌어 별의 형태를 단단히 유지한다.
    _projectTethers(dt, io){
      const s = this.settings, h = this.h;
      const { x, y, vx, vy, mode } = this;
      const rate = approach(s.tetherRate, dt), maxStep = s.maxSpeed * dt;
      for(const i of h.order){
        const p = this.effParent[i];
        if(p < 0 || mode[i] !== MODE_ACTIVE || i === io.dragging || this.pulled[i]) continue;
        const dx = x[i] - x[p], dy = y[i] - y[p];
        const d = Math.hypot(dx, dy) || 1e-3, ux = dx / d, uy = dy / d;
        const corr = clamp((this.tether[i] - d) * rate, -maxStep, maxStep);
        x[i] += ux * corr; y[i] += uy * corr;
        const relV = (vx[i] - vx[p]) * ux + (vy[i] - vy[p]) * uy;
        vx[i] -= ux * relV * rate; vy[i] -= uy * relV * rate;
      }
    }

    // 겹침 해소: 위치를 무게 비율로 떼어 놓고, 다가오는 속도에는 반발 계수만큼 충격량을 준다.
    _collide(io){
      const s = this.settings, n = this.n;
      const { x, y, vx, vy, mode, radius } = this;
      let maxR = 0;
      for(let i = 0; i < n; i++) if(mode[i] === MODE_ACTIVE || mode[i] === MODE_STATIC) maxR = Math.max(maxR, radius[i]);
      const cell = Math.max(8, maxR * 2 + s.collidePad);
      this.grid.build(x, y, i => mode[i] === MODE_ACTIVE || mode[i] === MODE_STATIC, n, cell);
      for(let i = 0; i < n; i++){
        if(mode[i] !== MODE_ACTIVE) continue;
        this.grid.forNear(x[i], y[i], j => {
          if(j === i || (mode[j] === MODE_ACTIVE && j < i)) return;
          let dx = x[j] - x[i], dy = y[j] - y[i];
          let d = Math.hypot(dx, dy);
          const minD = radius[i] + radius[j] + s.collidePad;
          if(d >= minD) return;
          if(d < 1e-3){ dx = i % 2 ? 1 : -1; dy = j % 2 ? 1 : -1; d = Math.SQRT2; }
          const wi = i === io.dragging ? 0 : this.invMass[i];
          const wj = mode[j] !== MODE_ACTIVE || j === io.dragging ? 0 : this.invMass[j];
          const wsum = wi + wj;
          if(!wsum) return;
          const ux = dx / d, uy = dy / d, overlap = minD - d;
          x[i] -= ux * overlap * wi / wsum; y[i] -= uy * overlap * wi / wsum;
          x[j] += ux * overlap * wj / wsum; y[j] += uy * overlap * wj / wsum;
          const closing = (vx[j] - vx[i]) * ux + (vy[j] - vy[i]) * uy;
          if(closing >= 0) return;
          const imp = -(1 + s.restitution) * closing / wsum;
          vx[i] -= ux * imp * wi; vy[i] -= uy * imp * wi;
          vx[j] += ux * imp * wj; vy[j] += uy * imp * wj;
        });
      }
    }

    // 드래그를 놓을 때: 최근 포인터 속도로 던지고(오래 멈춰 있었으면 감쇠), 별 전체에 회복 감쇠를 건다.
    release(i, idleMs, cancelled){
      if(i < 0 || i >= this.n) return;
      const s = this.settings;
      const decay = Math.exp(-Math.max(0, idleMs - 80) / 60);
      const k = cancelled ? 0 : s.throwScale * decay;
      this.vx[i] *= k; this.vy[i] *= k;
      const sp = Math.hypot(this.vx[i], this.vy[i]);
      if(sp > s.maxSpeed){ this.vx[i] *= s.maxSpeed / sp; this.vy[i] *= s.maxSpeed / sp; }
      const root = this.h && this.h.rootOf[i] >= 0 ? this.h.rootOf[i] : i;
      this.recovery[root] = Math.max(this.recovery[root], this.grip[i] || 0.6);
      // 놓은 자리에서 다시 궤도에 합류하도록 제자리 각도를 현재 각도로 맞춘다.
      const p = this.effParent[i];
      if(p >= 0) this.homeAngle[i] = Math.atan2(this.y[i] - this.y[p], this.x[i] - this.x[p]) - this.orbitSign[i] * this.orbitPhase;
    }

    // 드래그 중 노드 이동: 포인터 속도를 지수 평균으로 기록해 던지기에 쓴다.
    dragTo(i, wx, wy, dtMs){
      const dt = Math.max(0.001, dtMs / 1000);
      const ddx = wx - this.x[i], ddy = wy - this.y[i];
      this.vx[i] = this.vx[i] * 0.6 + ddx / dt * 0.4;
      this.vy[i] = this.vy[i] * 0.6 + ddy / dt * 0.4;
      this.x[i] = wx; this.y[i] = wy;
    }

    isRoot(i){ return !!this.h && this.h.depth[i] === 0; }
    childCount(i){ return this.h ? this.h.children[i].length : 0; }
  }

  Object.assign(CG, { Simulation, SpatialGrid, SIM_DEFAULTS, MODE_OFF, MODE_ACTIVE, MODE_STATIC, MODE_ANCHOR });
})(window);
