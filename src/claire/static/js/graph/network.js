/**
 * Claire Graph Engine — Network: vis-network 호환 파사드.
 *
 * DataSet 변화를 받아 계층 시뮬레이션을 갱신하고, 매 프레임 화면 좌표 프레임을 만들어 렌더러
 * 백엔드(WebGPU → Canvas2D 폴백)에 넘긴다. 라벨·관계 라벨·화살표는 위에 겹친 2D 캔버스에
 * 충돌 회피(LOD)로 그린다. 카메라·포인터(마우스·터치·핀치)·선택·이벤트는 app.js 가 쓰던
 * vis-network 메서드와 이벤트 이름을 그대로 따른다.
 */
(function(global){
  'use strict';
  const CG = global.ClaireGraph;
  const {
    clamp, lerp, approach, easeInOutCubic, parseColor, mixColor, cssColor, hashUnit,
    Emitter, Simulation, Frame, WebGPURenderer, Canvas2DRenderer,
    MODE_ACTIVE, MODE_STATIC, SHAPE_CIRCLE, SHAPE_RING, SHAPE_SQUARE,
  } = CG;

  const DEFAULT_OPTIONS = {
    nodes: { size: 4, font: { color: '#1f2328', size: 12 }, color: { background: '#8b949e', border: '#d0d7de' } },
    edges: { color: { color: '#c4ccd6', highlight: '#1a7f37', opacity: 0.35 }, width: 1, font: { color: '#1f2328', size: 0, strokeColor: '' } },
    groups: {},
    physics: true,
    interaction: { hover: true, dragNodes: true, dragView: true, zoomView: true, multiselect: false },
    renderer: {
      backend: 'auto',          // 'auto' | 'webgpu' | 'canvas2d'
      ambient: true,            // 궤도 회전 + 흐름 패킷(감속 모션 설정이면 꺼진다)
      flowSpeed: 0.18,          // 패킷이 엣지 하나를 지나는 비율/초
      hoverFocus: true,         // 호버한 노드의 이웃 밖은 흐리게(Shopify 처럼 은은하게 — 맥락은 남긴다)
      contextAlpha: 0.45,
      hoverLabelScale: 1.7,     // 호버한 노드 라벨 확대 배율
      labelFont: "D2Coding, 'Noto Sans KR', ui-monospace, SFMono-Regular, Menlo, monospace",
      maxLabels: 180,
      labelZoom: 0.8,           // 이 확대율 이상에서만 허브가 아닌 일반 노드 라벨을 그린다
      edgeWidthScale: 0.75,
      crossEdgeAlpha: 0.2,      // 대기 상태(inherit) 교차 관계의 불투명도 배율(스포크가 별 구조를 이끈다)
      labelMaxChars: 28,        // 호버·선택하지 않은 라벨은 이 길이에서 말줄임
      minZoom: 0.05,
      maxZoom: 5,
      fitMaxZoom: 1.6,
      fitAvoid: [],             // 그래프 위에 겹친 컨트롤(요소·선택자). 카메라 맞춤과 별 무리 비율이 이 영역을 피한다
    },
    simulation: {},
  };

  const isPlainObject = v => v && typeof v === 'object' && !Array.isArray(v);
  function deepMerge(base, extra){
    const out = Object.assign({}, base);
    if(!isPlainObject(extra)) return out;
    for(const k of Object.keys(extra)){
      const v = extra[k];
      out[k] = isPlainObject(v) && isPlainObject(base[k]) ? deepMerge(base[k], v) : v;
    }
    return out;
  }
  const now = () => performance.now();
  const STEP = 1 / 60;

  // URL(?graphBackend=canvas2d) → localStorage → 옵션 순으로 백엔드 선호를 정한다(디버깅용 강제 전환).
  function preferredBackend(option){
    try{
      const q = new URLSearchParams(location.search).get('graphBackend');
      if(q === 'webgpu' || q === 'canvas2d' || q === 'auto') return q;
    }catch(_){}
    try{
      const saved = localStorage.getItem('claireGraphBackend');
      if(saved === 'webgpu' || saved === 'canvas2d') return saved;
    }catch(_){}
    return option || 'auto';
  }

  class Network extends Emitter {
    constructor(container, data, options){
      super();
      this.container = container;
      this.options = deepMerge(DEFAULT_OPTIONS, options || {});
      this.nodes = data && data.nodes;
      this.edges = data && data.edges;
      this.sim = new Simulation(this.options.simulation);
      this.frame = new Frame();
      this.cam = { x: 0, y: 0, scale: 1 };
      this.camAnim = null;
      this.W = 0; this.H = 0; this.dpr = 1;
      this.renderer = null;
      this.backend = null;
      this.fallbackReason = '';
      this.physicsEnabled = this.options.physics !== false;
      this.suspended = false;
      this.selectedNodes = new Set();
      this.selectedEdges = new Set();
      this.hovered = -1;
      this.focusSet = null;
      this.focusAmount = 0;
      this.dragging = -1;
      this.pointerWorld = null;
      this.pointerVel = [0, 0];
      this._lastPointerT = 0;
      this._pointers = new Map();
      this._press = null;
      this._pinch = null;
      this._panning = false;
      this._raf = 0;
      this._lastTick = 0;
      this._acc = 0;
      this._lastInteract = now();
      this._lastDraw = 0;
      this._cameraSet = false;
      this._autoFit = 'pending';
      this._pendingFit = null;
      this._stabilizePending = true;
      this._settledOnce = false;
      this._settleClock = 0;
      this._energizedAt = now();
      this._destroyed = false;
      this._topologyDirty = true;
      this._hiddenDirty = false;
      this._ids = [];
      this._index = new Map();
      this._items = [];
      this._edgeIds = [];
      this._edgeA = new Int32Array(0);
      this._edgeB = new Int32Array(0);
      this._edgeTree = new Uint8Array(0);
      this._edgeItems = [];
      this._neighbors = new Map();
      this._incident = new Map();
      this._ringAnim = new Float32Array(0);
      this._labelRects = [];
      this._measureCache = new Map();
      this._reducedMQ = global.matchMedia ? global.matchMedia('(prefers-reduced-motion: reduce)') : null;

      this._setupDom();
      this._bindData();
      this._bindInput();
      this._refreshTheme();
      this._rebuild();          // 첫 적재는 모두 새 노드라 _rebuild 안에서 워밍업까지 마친다
      this._initBackend();
      this._ro = global.ResizeObserver ? new ResizeObserver(() => this._measure()) : null;
      if(this._ro) this._ro.observe(container);
      this._onWindowResize = () => this._measure();
      global.addEventListener('resize', this._onWindowResize);
      this._onVisibility = () => { if(!document.hidden) this._wake(); };
      document.addEventListener('visibilitychange', this._onVisibility);
      this._measure();
    }

    // --- DOM ---
    _setupDom(){
      const c = this.container;
      if(getComputedStyle(c).position === 'static') c.style.position = 'relative';
      c.style.overflow = 'hidden';
      c.style.touchAction = 'none';
      c.style.userSelect = 'none';
      c.style.webkitUserSelect = 'none';
      this.gpuCanvas = this._makeCanvas('claire-graph-marks');
      this.labelCanvas = this._makeCanvas('claire-graph-labels');
      this.labelCanvas.style.pointerEvents = 'none';
      c.appendChild(this.gpuCanvas);
      c.appendChild(this.labelCanvas);
      this.labelCtx = this.labelCanvas.getContext('2d');
    }
    _makeCanvas(cls){
      const el = document.createElement('canvas');
      el.className = cls;
      el.setAttribute('aria-hidden', 'true');
      Object.assign(el.style, { position: 'absolute', left: '0', top: '0', width: '100%', height: '100%', display: 'block', touchAction: 'none' });
      return el;
    }

    async _initBackend(){
      const pref = preferredBackend(this.options.renderer.backend);
      if(pref !== 'canvas2d' && global.navigator && navigator.gpu){
        try{
          const renderer = await Promise.race([
            WebGPURenderer.create(this.gpuCanvas),
            new Promise((_, reject) => setTimeout(() => reject(new Error('WebGPU init timeout')), 2500)),
          ]);
          if(this._destroyed){ renderer.dispose(); return; }
          renderer.onLost(err => this._fallbackToCanvas(err));
          this.renderer = renderer;
          this.backend = 'webgpu';
          this._sizeRenderer();
          this._wake();
          return;
        }catch(err){
          this.fallbackReason = String(err && err.message || err);
        }
      }
      this._useCanvas2D();
    }
    _useCanvas2D(){
      if(this._destroyed) return;
      // WebGPU 컨텍스트를 한 번 잡은 캔버스는 2D 로 바꿀 수 없어 새 캔버스로 교체한다.
      const fresh = this._makeCanvas('claire-graph-marks');
      this.container.replaceChild(fresh, this.gpuCanvas);
      this.gpuCanvas = fresh;
      this.renderer = new Canvas2DRenderer(fresh);
      this.backend = 'canvas2d';
      this._sizeRenderer();
      this._wake();
    }
    _fallbackToCanvas(err){
      if(this.backend !== 'webgpu') return;
      this.fallbackReason = String(err && err.message || err);
      console.warn('ClaireGraph: WebGPU 렌더러를 잃어 Canvas2D 로 전환합니다 —', this.fallbackReason);
      const old = this.renderer;
      this.renderer = null;
      try{ old.dispose(); }catch(_){}
      this._useCanvas2D();
    }

    _measure(){
      if(this._destroyed) return;
      const r = this.container.getBoundingClientRect();
      const w = Math.round(r.width), h = Math.round(r.height);
      const dpr = Math.min(global.devicePixelRatio || 1, 3);
      const changed = w !== this.W || h !== this.H || dpr !== this.dpr;
      this.W = w; this.H = h; this.dpr = dpr;
      if(changed) this._sizeRenderer();
      if(w > 0 && h > 0) this._syncPackAspect();
      if(w > 0 && h > 0 && this._pendingFit){ const opts = this._pendingFit; this._pendingFit = null; this.fit(opts); }
      this._wake();
    }
    // 겹친 컨트롤이 가리는 만큼의 안쪽 여백(CSS px). 컨트롤마다 가장 적게 잘라 내는 변 하나를 고른다
    // (오른쪽 세로 컨트롤 열이면 오른쪽). 숨겨졌거나 그래프와 겹치지 않는 컨트롤은 건너뛴다.
    _fitInsets(){
      const ins = { l: 0, r: 0, t: 0, b: 0 };
      const list = this.options.renderer.fitAvoid || [];
      this._avoidRects = [];
      if(!list.length) return ins;
      const c = this.container.getBoundingClientRect();
      for(const ref of list){
        const el = typeof ref === 'string' ? document.querySelector(ref) : ref;
        if(!el || !el.getBoundingClientRect) continue;
        const r = el.getBoundingClientRect();
        if(r.width <= 0 || r.height <= 0 || r.right <= c.left || r.left >= c.right || r.bottom <= c.top || r.top >= c.bottom) continue;
        this._avoidRects.push([r.left - c.left - 4, r.top - c.top - 4, r.width + 8, r.height + 8]);
        const cut = { r: c.right - r.left, l: r.right - c.left, t: r.bottom - c.top, b: c.bottom - r.top };
        const side = Object.keys(cut).reduce((p, q) => cut[q] < cut[p] ? q : p);
        ins[side] = Math.max(ins[side], cut[side] + 8);
      }
      // 컨트롤이 화면 대부분을 덮는 좁은 화면에서도 그래프 자리는 남긴다.
      ins.l = Math.min(ins.l, this.W * 0.4); ins.r = Math.min(ins.r, this.W * 0.4);
      ins.t = Math.min(ins.t, this.H * 0.4); ins.b = Math.min(ins.b, this.H * 0.4);
      return ins;
    }
    // 별 무리를 그래프가 실제로 쓰는 영역(컨트롤 제외) 비율로 펼친다. 넓은 화면은 가로로, 세로로 긴
    // 모바일은 세로로. 비율이 크게 바뀌면(회전·패널 전환) 무리를 다시 짜고, 카메라를 손대지 않았으면 다시 맞춘다.
    _syncPackAspect(){
      const ins = this._fitInsets();
      const aw = this.W - ins.l - ins.r, ah = this.H - ins.t - ins.b;
      if(aw <= 0 || ah <= 0) return;
      const aspect = clamp(aw / ah, 0.6, 2);
      const cur = this.sim.settings.packAspectX;
      if(Math.abs(Math.log(aspect / cur)) < 0.15) return;
      this.sim.settings.packAspectX = aspect;
      if(!this.sim.h) return;
      this._hiddenDirty = true;
      this._energize();
      if(!this._cameraSet && this._autoFit === 'done') this._autoFit = 'refine';
    }
    // 앱이 카메라를 정하지 않았으면 스스로 맞춘다: 첫 프레임(앱의 첫 applyView 로 숨김이 반영된 뒤)에
    // 한 번, 첫 안정화 때 정돈된 배치로 한 번 더. 앱·사용자가 카메라를 만지면 그 뒤로는 손대지 않는다.
    _autoFitStep(stage){
      if(this._autoFit !== stage) return;
      this._autoFit = stage === 'pending' ? 'refine' : 'done';
      if(this._cameraSet || !this._ids.length){ this._autoFit = 'done'; return; }
      this._fitNow(this._fitTarget(null), stage === 'pending' ? false : { duration: 450 });
      this._cameraSet = false;
    }
    _sizeRenderer(){
      if(this.W <= 0 || this.H <= 0) return;
      if(this.renderer) this.renderer.resize(this.W, this.H, this.dpr);
      this.labelCanvas.width = Math.max(1, Math.round(this.W * this.dpr));
      this.labelCanvas.height = Math.max(1, Math.round(this.H * this.dpr));
    }

    // --- 데이터 ---
    _bindData(){
      const topo = () => { this._topologyDirty = true; this._energize(); this._wake(); };
      this._dataHandlers = [
        [this.nodes, 'add', topo], [this.nodes, 'remove', topo],
        [this.nodes, 'update', p => {
          const f = p && p.fields;
          // applyView 는 매번 size 등을 다시 보내므로, 실제로 값이 바뀐 경우에만 계층을 다시 짓는다.
          if(f && (f.has('physics') || f.has('fixed') || f.has('size') || f.has('orbit')) && this._structureChanged(p.items)) this._topologyDirty = true;
          else if(f && (f.has('x') || f.has('y'))) this._applyPositionUpdates(p.items);
          if(f && f.has('hidden')){
            this._hiddenDirty = true; this._energize();
            // 숨김이 바뀌면 별 무리가 다시 짜인다. 사용자·앱이 카메라를 만지지 않았으면 정돈된 뒤 다시 맞춘다.
            if(!this._cameraSet && this._autoFit === 'done') this._autoFit = 'refine';
          }
          this._wake();
        }],
        [this.edges, 'add', topo], [this.edges, 'remove', topo],
        [this.edges, 'update', () => this._wake()],
      ];
      for(const [ds, ev, fn] of this._dataHandlers) if(ds && ds.on) ds.on(ev, fn);
    }
    _nodeFlags(it){
      const fixed = !!it.fixed && (it.fixed === true || !!it.fixed.x || !!it.fixed.y);
      const anchor = !!(it.hidden && fixed);
      return {
        fixed, anchor,
        eligible: !anchor && it.physics !== false && !fixed,
        radius: Math.max(0.5, +(it.size != null ? it.size : this.options.nodes.size) || 4),
      };
    }
    // app 의 바깥 궤도 지정 { band, index, side } → [band, index, side] (물리에서 빠진 노드만 의미가 있다).
    _orbitOf(it){
      const ob = it.orbit;
      if(!ob || it.physics !== false || !Number.isFinite(+ob.band)) return null;
      return [+ob.band, +ob.index || 0, Math.sign(+ob.side || 0)];
    }
    _structureChanged(ids){
      const topo = this.sim.topo;
      if(!topo) return true;
      for(const id of ids){
        const i = this._index.get(id), item = this.nodes.get(id);
        if(i === undefined || !item) return true;
        const f = this._nodeFlags(item);
        if(!!topo.eligible[i] !== f.eligible || !!topo.anchor[i] !== f.anchor) return true;
        if(Math.abs(topo.radius[i] - f.radius) > 1e-3) return true;
        const ob = this._orbitOf(item);
        if(ob ? topo.orbitBand[i] !== ob[0] || topo.orbitIndex[i] !== ob[1] || topo.orbitSide[i] !== ob[2] : topo.orbitBand[i] >= 0) return true;
        if(f.fixed || item.physics === false){
          if(Number.isFinite(item.x) && item.x !== topo.fixedX[i]) return true;
          if(Number.isFinite(item.y) && item.y !== topo.fixedY[i]) return true;
        }
      }
      return false;
    }
    _applyPositionUpdates(ids){
      for(const id of ids){
        const i = this._index.get(id), item = this.nodes.get(id);
        if(i === undefined || !item) continue;
        if(Number.isFinite(item.x)) this.sim.x[i] = item.x;
        if(Number.isFinite(item.y)) this.sim.y[i] = item.y;
      }
    }

    _rebuild(){
      this._topologyDirty = false;
      this._hiddenDirty = false;
      const items = this.nodes ? this.nodes.get() : [];
      const n = items.length;
      const ids = items.map(it => it.id);
      const index = new Map(ids.map((id, i) => [id, i]));
      const group = new Array(n);
      const radius = new Float32Array(n);
      const eligible = new Uint8Array(n), anchor = new Uint8Array(n), hidden = new Uint8Array(n);
      const fixedX = new Float64Array(n).fill(NaN), fixedY = new Float64Array(n).fill(NaN);
      const initX = new Float64Array(n).fill(NaN), initY = new Float64Array(n).fill(NaN);
      const orbitBand = new Int8Array(n).fill(-1), orbitIndex = new Float64Array(n), orbitSide = new Int8Array(n);
      let fresh = 0;
      items.forEach((it, i) => {
        const f = this._nodeFlags(it);
        group[i] = it.group || '';
        radius[i] = f.radius;
        hidden[i] = it.hidden ? 1 : 0;
        anchor[i] = f.anchor ? 1 : 0;
        eligible[i] = f.eligible ? 1 : 0;
        const hasPos = Number.isFinite(it.x) && Number.isFinite(it.y);
        if(hasPos && (f.fixed || it.physics === false)){ fixedX[i] = it.x; fixedY[i] = it.y; }
        else if(hasPos){ initX[i] = it.x; initY[i] = it.y; }
        const ob = this._orbitOf(it);
        if(ob){ orbitBand[i] = ob[0]; orbitIndex[i] = ob[1]; orbitSide[i] = ob[2]; }
        if(!this._index.has(it.id)) fresh++;
      });
      const edgeItems = this.edges ? this.edges.get() : [];
      const ea = [], eb = [], eItems = [], eIds = [], springs = [];
      const adj = Array.from({ length: n }, () => []);
      const neighbors = new Map(), incident = new Map();
      const link = (map, k, v) => { let s = map.get(k); if(!s){ s = []; map.set(k, s); } s.push(v); };
      for(const e of edgeItems){
        const a = index.get(e.from), b = index.get(e.to);
        if(a === undefined || b === undefined) continue;
        link(neighbors, e.from, e.to); link(neighbors, e.to, e.from);
        link(incident, e.from, e.id); if(e.to !== e.from) link(incident, e.to, e.id);
        if(anchor[a] || anchor[b]){
          springs.push({ a: anchor[a] ? a : b, b: anchor[a] ? b : a, length: +e.length || 80 });
          continue;
        }
        ea.push(a); eb.push(b); eItems.push(e); eIds.push(e.id);
        if(a !== b){ adj[a].push(b); adj[b].push(a); }
      }
      const adjStart = new Int32Array(n + 1);
      for(let i = 0; i < n; i++) adjStart[i + 1] = adjStart[i] + adj[i].length;
      const adjList = new Int32Array(adjStart[n]);
      for(let i = 0; i < n; i++) adjList.set(adj[i], adjStart[i]);

      this._ids = ids; this._index = index; this._items = items;
      this._edgeA = Int32Array.from(ea); this._edgeB = Int32Array.from(eb);
      this._edgeItems = eItems; this._edgeIds = eIds;
      this._neighbors = neighbors; this._incident = incident;
      const prevRing = this._ringAnim;
      this._ringAnim = new Float32Array(n);
      if(prevRing.length === n) this._ringAnim.set(prevRing);
      for(const id of [...this.selectedNodes]) if(!index.has(id)) this.selectedNodes.delete(id);
      if(this.hovered >= n) this.hovered = -1;
      this.sim.setTopology({ ids, group, radius, eligible, anchor, hidden, fixedX, fixedY, initX, initY, adjStart, adjList, springs, orbitBand, orbitIndex, orbitSide });
      // 신장 숲에 속한 엣지(tether)인지 — 대기 상태에서 교차 관계는 흐리게 그려 별 구조를 살린다.
      const parent = this.sim.h ? this.sim.h.parent : null;
      this._edgeTree = Uint8Array.from(ea, (a, k) => parent && (parent[eb[k]] === a || parent[a] === eb[k]) ? 1 : 0);
      // 대부분이 새 노드(첫 적재·테마 전환)면 정돈된 상태로 보이도록 다시 워밍업한다.
      if(n && fresh > n / 2) this.sim.warmup();
      if(this.hoverId != null) this.hovered = index.has(this.hoverId) ? index.get(this.hoverId) : -1;
      this._energize();
    }
    _refreshHidden(){
      this._hiddenDirty = false;
      const n = this._ids.length, hidden = new Uint8Array(n);
      for(let i = 0; i < n; i++) hidden[i] = this._items[i].hidden ? 1 : 0;
      this.sim.setHidden(hidden);
      // 첫 안정화 전(앱의 첫 필터 적용)에는 별을 통째로 새 제자리에 옮기고 워밍업해 정돈된 상태로 보인다.
      // 그 뒤의 필터 변경은 별이 미끄러져 옮겨 가는 모습을 그대로 보여 준다.
      if(!this._settledOnce){ this.sim.snapToHomes(); this.sim.warmup(); }
    }

    // --- 렌더 루프 ---
    _wake(){
      if(this._raf || this._destroyed) return;
      this._raf = requestAnimationFrame(t => this._tick(t));
    }
    _energize(){
      this._stabilizePending = true;
      this._settleClock = 0;
      this._energizedAt = now();
    }
    _ambient(){
      return this.options.renderer.ambient && !(this._reducedMQ && this._reducedMQ.matches);
    }
    _tick(t){
      this._raf = 0;
      if(this._destroyed) return;
      const dpr = Math.min(global.devicePixelRatio || 1, 3);
      if(dpr !== this.dpr){ this.dpr = dpr; this._sizeRenderer(); }
      if(this._topologyDirty) this._rebuild();
      if(this._hiddenDirty) this._refreshHidden();
      const visible = this.W > 0 && this.H > 0 && !document.hidden;
      if(visible) this._autoFitStep('pending');
      const ambient = this._ambient();
      const idle = now() - this._lastInteract > 8000 && !this._stabilizePending;
      // 상호작용 없이 배경 애니메이션만 돌 때는 30fps 로 낮춰 전력을 아낀다(건너뛴 시간은 다음 dt 에 합산).
      if(visible && idle && ambient && !this.camAnim && t - this._lastDraw < 30){ this._wake(); return; }
      const dt = this._lastTick ? clamp((t - this._lastTick) / 1000, 0, 0.1) : STEP;
      this._lastTick = t;

      let simulating = false;
      if(this.physicsEnabled && !this.suspended && this._ids.length){
        this._acc = Math.min(this._acc + dt, STEP * 4);
        const io = this._simIO(ambient);
        while(this._acc >= STEP){ this.sim.step(STEP, io); this._acc -= STEP; }
        simulating = true;
      }
      this._decayPointerVelocity(dt);
      this._updateCamera(t);
      const easing = this._updateFocus(dt);
      this._trackStability(dt, simulating);
      if(visible && this.renderer){
        this._draw(t / 1000);
        this._lastDraw = t;
        // 노드가 움직여(자석·궤도·안정화) 멈춘 커서 밑으로 들어오거나 빠져나가도 호버를 따라가게 한다.
        if(this.pointerWorld && this._pointerScreen && !this._press && this.options.interaction.hover !== false){
          this._setHover(this._hitNode(this._pointerScreen.x, this._pointerScreen.y, 4), this._pointerScreen);
        }
      }
      const busy = this.camAnim || this.dragging >= 0 || this._panning || this._pinch || easing ||
        this._stabilizePending || this.pointerWorld || (simulating && this.sim.meanSpeed > 0.5);
      const animated = visible && ambient && this._ids.length > 0;
      if(visible && (busy || animated)) this._wake();
    }
    _simIO(ambient){
      return {
        pointer: this.pointerWorld,
        pointerVel: this.pointerVel,
        hovered: this.hovered,
        dragging: this.dragging,
        dragIdle: this._press ? (now() - this._press.lastT) / 1000 : 0,
        selected: this.selectedNodes.size > 0,
        ambient,
        reduced: !!(this._reducedMQ && this._reducedMQ.matches),
        scale: this.cam.scale,
      };
    }
    _trackStability(dt, simulating){
      if(!this._stabilizePending) return;
      const elapsed = (now() - this._energizedAt) / 1000;
      if(!simulating || !this._ids.length){ this._settleClock += dt; }
      else if(this.sim.meanSpeed < 6) this._settleClock += dt;
      else this._settleClock = 0;
      if(this._settleClock >= 0.4 || elapsed > 4){
        this._stabilizePending = false;
        this._settledOnce = true;
        if(this.W > 0 && this.H > 0) this._autoFitStep('refine');
        this.emit('stabilized', { iterations: Math.round(elapsed * 60) });
      }
    }
    _decayPointerVelocity(dt){
      if(now() - this._lastPointerT > 40){
        const k = Math.exp(-dt / 0.05);
        this.pointerVel[0] *= k; this.pointerVel[1] *= k;
      }
    }

    // --- 스타일 해석 ---
    _refreshTheme(){
      // 라벨 halo 는 컨테이너 실제 배경을 우선한다(테마 전환 뒤에도 옛 strokeColor 가 남지 않게).
      const bg = getComputedStyle(this.container).backgroundColor;
      const stroke = this.options.edges.font && this.options.edges.font.strokeColor;
      this._haloColor = bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent' ? bg : (stroke || '#ffffff');
    }
    _nodeColor(item){
      const c = item.color;
      if(c && typeof c === 'object' && c.background) return parseColor(c.background);
      if(typeof c === 'string') return parseColor(c);
      const g = item.group != null && this.options.groups[item.group];
      if(g && g.color) return parseColor(typeof g.color === 'string' ? g.color : g.color.background);
      return parseColor(this.options.nodes.color.background);
    }
    _nodeBorder(item){
      const c = item.color;
      if(c && typeof c === 'object' && c.border) return parseColor(c.border);
      return parseColor(this.options.nodes.color.border);
    }
    _isHub(i){ return this.sim.isRoot(i) && this.sim.childCount(i) >= 3; }
    _screenRadius(r){
      const s = this.cam.scale;
      return clamp(r * Math.pow(s, 0.6), 1.2, r * 2.5);
    }
    _updateFocus(dt){
      // 잡고 끄는 노드는 호버 강조를 유지한다(Shopify 처럼 끌려오는 별이 계속 밝게 보인다).
      const want = this.options.renderer.hoverFocus && this.hovered >= 0 && this.selectedNodes.size === 0 &&
        (this.dragging < 0 || this.dragging === this.hovered) ? 1 : 0;
      const prev = this.focusAmount;
      this.focusAmount += (want - this.focusAmount) * approach(want ? 9 : 6, dt);
      if(Math.abs(this.focusAmount - want) < 0.003) this.focusAmount = want;
      let ringMoving = false;
      for(let i = 0; i < this._ringAnim.length; i++){
        const target = i === this.hovered ? 1 : 0, cur = this._ringAnim[i];
        if(cur === target) continue;
        let next = cur + (target - cur) * approach(14, dt);
        if(Math.abs(next - target) < 0.01) next = target;
        this._ringAnim[i] = next;
        ringMoving = true;
      }
      return ringMoving || prev !== this.focusAmount;
    }

    // --- 프레임 생성 + 그리기 ---
    _toScreenX(x){ return (x - this.cam.x) * this.cam.scale + this.W / 2; }
    _toScreenY(y){ return (y - this.cam.y) * this.cam.scale + this.H / 2; }
    _rendered(i){
      const m = this.sim.mode[i];
      return (m === MODE_ACTIVE || m === MODE_STATIC) && !this._items[i].hidden;
    }
    _nodeAlpha(i){
      const item = this._items[i];
      let a = item.opacity != null ? +item.opacity : 1;
      if(this.focusAmount > 0 && this.focusSet && !this.focusSet.has(i)) a *= lerp(1, this.options.renderer.contextAlpha, this.focusAmount);
      return clamp(a, 0, 1);
    }
    _draw(time){
      const frame = this.frame, sim = this.sim, opts = this.options;
      const W = this.W, H = this.H, n = this._ids.length;
      frame.reset();
      if(!this._screen || this._screen.sx.length !== n){
        this._screen = { sx: new Float32Array(n), sy: new Float32Array(n), sr: new Float32Array(n), alpha: new Float32Array(n), colour: new Array(n) };
      }
      const { sx, sy, sr, alpha, colour } = this._screen;
      alpha.fill(0);
      for(let i = 0; i < n; i++){
        if(!this._rendered(i)) continue;
        sx[i] = this._toScreenX(sim.x[i]); sy[i] = this._toScreenY(sim.y[i]);
        sr[i] = this._screenRadius(sim.radius[i]);
        alpha[i] = this._nodeAlpha(i);
        colour[i] = this._nodeColor(this._items[i]);
      }
      const ambient = this._ambient();
      const flowOn = ambient && opts.renderer.flowSpeed > 0;
      const edgeDefaults = opts.edges;
      const defaultEdgeColor = parseColor(edgeDefaults.color && edgeDefaults.color.color);
      const widthScale = opts.renderer.edgeWidthScale;
      const packets = [];
      const ringColour = parseColor(opts.nodes.font.color);
      this._visibleEdges = [];
      for(let k = 0; k < this._edgeItems.length; k++){
        const e = this._edgeItems[k], a = this._edgeA[k], b = this._edgeB[k];
        if(e.hidden || !this._rendered(a) || !this._rendered(b)) continue;
        const x0 = sx[a], y0 = sy[a], x1 = sx[b], y1 = sy[b];
        if((x0 < 0 && x1 < 0) || (x0 > W && x1 > W) || (y0 < 0 && y1 < 0) || (y0 > H && y1 > H)) continue;
        const c = e.color && typeof e.color === 'object' ? e.color : null;
        const opacity = c && c.opacity != null ? +c.opacity : (edgeDefaults.color.opacity != null ? +edgeDefaults.color.opacity : 1);
        let a0 = opacity * Math.min(alpha[a], alpha[b]);
        if(this.focusAmount > 0 && this.hovered >= 0 && a !== this.hovered && b !== this.hovered){
          a0 *= lerp(1, opts.renderer.contextAlpha * 0.6, this.focusAmount);
        }
        const width = (e.width != null ? +e.width : edgeDefaults.width) * widthScale;
        if(a0 <= 0.004 || width <= 0) continue;
        let c0, c1;
        const hot = this.focusAmount > 0 && this.hovered >= 0 && (a === this.hovered || b === this.hovered);
        if(hot && c && c.inherit){
          // 호버한 노드의 관계는 강조색으로 밝힌다(Shopify 의 초록 스포크).
          const hi = parseColor(edgeDefaults.color && edgeDefaults.color.highlight);
          c0 = mixColor(colour[a], hi, this.focusAmount); c1 = mixColor(colour[b], hi, this.focusAmount);
          a0 = lerp(a0, Math.max(a0, 0.9), this.focusAmount);
        }
        else if(c && c.inherit){
          c0 = colour[a]; c1 = colour[b];
          if(!this._edgeTree[k]) a0 *= opts.renderer.crossEdgeAlpha;
        }
        else { c0 = c1 = c && c.color ? parseColor(c.color) : (typeof e.color === 'string' ? parseColor(e.color) : defaultEdgeColor); }
        frame.pushEdge(x0, y0, x1, y1, Math.max(0.5, width), e.dashes ? 4 : 0, c0, a0, c1, a0);
        this._visibleEdges.push(k);
        if(flowOn && a !== b && a0 > 0.05){
          // 흐름 패킷: 관계 방향(from → to)으로 흐르는 점. 위상은 엣지 id 해시로 흩는다.
          const t = (time * opts.renderer.flowSpeed + hashUnit(e.id, 41)) % 1;
          packets.push(lerp(x0, x1, t), lerp(y0, y1, t), hot ? 1.5 : 1.1, k, Math.min(1, a0 * 1.6 + 0.08));
        }
      }
      for(let p = 0; p < packets.length; p += 5){
        const k = packets[p + 3], e = this._edgeItems[k];
        const c = e.color && typeof e.color === 'object' && !e.color.inherit && e.color.color ? parseColor(e.color.color) : mixColor(colour[this._edgeA[k]], colour[this._edgeB[k]], 0.5);
        frame.pushNode(packets[p], packets[p + 1], packets[p + 2], SHAPE_CIRCLE, mixColor(c, [1, 1, 1, 1], 0.25), packets[p + 4], 0);
      }
      for(let i = 0; i < n; i++){
        if(!this._rendered(i) || alpha[i] <= 0.004) continue;
        const x = sx[i], y = sy[i], r = sr[i];
        if(x < -r - 8 || x > W + r + 8 || y < -r - 8 || y > H + r + 8) continue;
        const item = this._items[i];
        frame.pushNode(x, y, r, this._isHub(i) ? SHAPE_SQUARE : SHAPE_CIRCLE, colour[i], alpha[i], 0);
        const ring = this._ringAnim[i];
        const lit = (+item.borderWidth || 0) >= 2 || this.selectedNodes.has(this._ids[i]);
        if(lit){
          frame.pushNode(x, y, r + 2.6, SHAPE_RING, this._nodeBorder(item), Math.min(1, alpha[i] + 0.2), this.selectedNodes.has(this._ids[i]) ? 1.6 : 1.25);
        }
        if(ring > 0.01){
          const hc = item.color && item.color.hover && item.color.hover.border ? parseColor(item.color.hover.border) : colour[i];
          frame.pushNode(x, y, r + 2 + ring * 2.5, SHAPE_RING, hc, ring, 1.2);
        } else if(this.focusAmount > 0.01 && this.focusSet && this.focusSet.has(i)){
          // 호버한 노드의 이웃은 속이 빈 고리로 짚어 준다(어디로 이어지는지 한눈에).
          frame.pushNode(x, y, r + 2.2, SHAPE_RING, ringColour, this.focusAmount * 0.9, 1);
        }
      }
      this.renderer.draw(frame);
      this._drawOverlay();
    }

    _measureText(ctx, text, font){
      const key = font + '\u0000' + text;
      let w = this._measureCache.get(key);
      if(w === undefined){
        w = ctx.measureText(text).width;
        if(this._measureCache.size > 4000) this._measureCache.clear();
        this._measureCache.set(key, w);
      }
      return w;
    }
    _drawOverlay(){
      const ctx = this.labelCtx, { sx, sy, sr, alpha } = this._screen;
      const opts = this.options, family = opts.renderer.labelFont;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, this.labelCanvas.width, this.labelCanvas.height);
      ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      const halo = this._haloColor;
      // 강조된 엣지에는 방향을 알리는 작은 화살촉을 그린다(감속 모션에서도 방향이 보이게).
      for(const k of this._visibleEdges){
        const e = this._edgeItems[k];
        const c = e.color && typeof e.color === 'object' ? e.color : null;
        const op = c && c.opacity != null ? +c.opacity : 0;
        const arrow = e.arrows === 'to' || (e.arrows && e.arrows.to);
        if(!arrow || op < 0.5) continue;
        const a = this._edgeA[k], b = this._edgeB[k];
        const dx = sx[b] - sx[a], dy = sy[b] - sy[a], d = Math.hypot(dx, dy);
        if(d < sr[b] + 14) continue;
        const ux = dx / d, uy = dy / d, tipX = sx[b] - ux * (sr[b] + 2.5), tipY = sy[b] - uy * (sr[b] + 2.5);
        const col = c && c.color ? parseColor(c.color) : this._nodeColor(this._items[b]);
        ctx.fillStyle = cssColor(col, Math.min(1, op * Math.min(alpha[a], alpha[b])));
        ctx.beginPath();
        ctx.moveTo(tipX, tipY);
        ctx.lineTo(tipX - ux * 6 - uy * 3, tipY - uy * 6 + ux * 3);
        ctx.lineTo(tipX - ux * 6 + uy * 3, tipY - uy * 6 - ux * 3);
        ctx.closePath(); ctx.fill();
      }
      const placed = this._labelRects; placed.length = 0;
      // 겹친 컨트롤 자리는 이미 차지된 것으로 보고 라벨이 그 밑으로 들어가지 않게 한다(호버 라벨은 예외).
      if(this._avoidRects) for(const r of this._avoidRects) placed.push(r);
      const overlaps = (x, y, w, h) => {
        if(x < 2 || y < 2 || x + w > this.W - 2 || y + h > this.H - 2) return true;
        for(const r of placed) if(x < r[0] + r[2] && x + w > r[0] && y < r[1] + r[3] && y + h > r[1]) return true;
        return false;
      };
      // 라벨 LOD: 호버 > 선택 > 강조 > 호버 이웃 > 허브 는 항상 후보, 나머지는 확대했을 때만.
      // 후보는 우선순위 순으로 자리를 차지하고 겹치면 건너뛴다(개수는 화면 면적에 비례).
      const cands = [];
      const detail = this.cam.scale >= opts.renderer.labelZoom;
      const hoverNeighbors = this.focusAmount > 0.5 ? this.focusSet : null;
      for(let i = 0; i < this._ids.length; i++){
        if(!this._rendered(i) || alpha[i] < 0.08) continue;
        const item = this._items[i];
        const fs = item.font && item.font.size != null ? +item.font.size : opts.nodes.font.size;
        if(!fs || !item.label) continue;
        if(sx[i] < -40 || sx[i] > this.W + 40 || sy[i] < -20 || sy[i] > this.H + 20) continue;
        const hub = this._isHub(i);
        const deg = item.degree != null ? +item.degree : this.sim.childCount(i);
        const lit = (+item.borderWidth || 0) >= 2;
        const selected = this.selectedNodes.has(this._ids[i]);
        const near = !!(hoverNeighbors && hoverNeighbors.has(i));
        if(!detail && !hub && !lit && !selected && !near && i !== this.hovered) continue;
        let pri = deg * 10 + fs;
        if(hub) pri += 1e6;
        if(near) pri += 5e6;
        if(lit) pri += 1e7;
        if(selected) pri += 1e8;
        if(i === this.hovered) pri += 1e9;
        cands.push([pri, i, fs, hub]);
      }
      cands.sort((p, q) => q[0] - p[0]);
      const max = Math.min(opts.renderer.maxLabels, Math.max(24, Math.round(this.W * this.H / 6500)));
      ctx.textBaseline = 'middle';
      ctx.lineJoin = 'round';
      let drawn = 0;
      for(const [, i, fs, hub] of cands){
        if(drawn >= max) break;
        const item = this._items[i];
        const isHover = i === this.hovered;
        const selected = this.selectedNodes.has(this._ids[i]);
        // 호버한 라벨만 크게 키운다. 나머지는 가는 고정폭 한 가지 굵기로 차분하게 둔다.
        const px = (clamp(10 + (fs - 11) * 0.45, 10, 13.5) + (hub ? 0.5 : 0)) * (isHover ? lerp(1, opts.renderer.hoverLabelScale, this.focusAmount) : 1);
        const font = (selected ? '600 ' : '400 ') + px.toFixed(1) + 'px ' + family;
        ctx.font = font;
        let text = String(item.label);
        const maxChars = opts.renderer.labelMaxChars;
        if(maxChars > 0 && !isHover && !selected && text.length > maxChars) text = text.slice(0, maxChars - 1).trimEnd() + '…';
        const tw = this._measureText(ctx, text, font);
        const w = tw + 4, h = px + 4, r = sr[i];
        const spots = [
          [sx[i] + r + 4, sy[i] - h / 2],
          [sx[i] - r - 4 - w, sy[i] - h / 2],
          [sx[i] - w / 2, sy[i] - r - 3 - h],
          [sx[i] - w / 2, sy[i] + r + 3],
        ];
        let spot = null;
        for(const s of spots) if(!overlaps(s[0], s[1], w, h)){ spot = s; break; }
        if(!spot){
          if(i !== this.hovered) continue;
          spot = spots[0];
        }
        placed.push([spot[0], spot[1], w, h]);
        drawn++;
        const fc = parseColor(item.font && item.font.color ? item.font.color : opts.nodes.font.color);
        const a = Math.min(1, alpha[i] * (item.opacity != null && +item.opacity < 0.5 ? 0.8 : 1));
        const tx = spot[0] + 2, ty = spot[1] + h / 2;
        ctx.lineWidth = 3;
        ctx.strokeStyle = halo;
        ctx.globalAlpha = a;
        ctx.strokeText(text, tx, ty);
        ctx.fillStyle = cssColor(fc, 1);
        ctx.fillText(text, tx, ty);
        ctx.globalAlpha = 1;
      }
      // 관계 라벨: app 이 font.size 를 켠 엣지만(선택·경로·확대 단계).
      let edgeLabels = 0;
      for(const k of this._visibleEdges){
        if(edgeLabels >= 120) break;
        const e = this._edgeItems[k];
        if(!e.label || !e.font || !(+e.font.size > 0)) continue;
        const a = this._edgeA[k], b = this._edgeB[k];
        const mx = (sx[a] + sx[b]) / 2, my = (sy[a] + sy[b]) / 2;
        const px = clamp(+e.font.size * 0.95, 9, 12);
        const font = '400 ' + px.toFixed(1) + 'px ' + family;
        ctx.font = font;
        const tw = this._measureText(ctx, e.label, font), w = tw + 4, h = px + 2;
        if(overlaps(mx - w / 2, my - h / 2, w, h)) continue;
        placed.push([mx - w / 2, my - h / 2, w, h]);
        edgeLabels++;
        const c = e.color && typeof e.color === 'object' ? e.color : {};
        ctx.globalAlpha = clamp((c.opacity != null ? +c.opacity : 1) + 0.25, 0, 1) * Math.min(alpha[a], alpha[b]);
        ctx.lineWidth = 3;
        ctx.strokeStyle = e.font.strokeColor || halo;
        ctx.strokeText(e.label, mx - tw / 2, my);
        ctx.fillStyle = e.font.color || opts.edges.font.color;
        ctx.fillText(e.label, mx - tw / 2, my);
        ctx.globalAlpha = 1;
      }
    }

    // --- 입력 ---
    _bindInput(){
      const el = this.container;
      this._h = {
        down: e => this._onDown(e),
        move: e => this._onMove(e),
        up: e => this._onUp(e, false),
        cancel: e => this._onUp(e, true),
        leave: e => this._onLeave(e),
        wheel: e => this._onWheel(e),
      };
      el.addEventListener('pointerdown', this._h.down);
      el.addEventListener('pointermove', this._h.move);
      el.addEventListener('pointerup', this._h.up);
      el.addEventListener('pointercancel', this._h.cancel);
      el.addEventListener('pointerleave', this._h.leave);
      el.addEventListener('wheel', this._h.wheel, { passive: false });
    }
    _local(e){
      const r = this.container.getBoundingClientRect();
      return { x: e.clientX - r.left, y: e.clientY - r.top };
    }
    _pointerPayload(p){ return { DOM: { x: p.x, y: p.y }, canvas: this.DOMtoCanvas(p) }; }
    _hitNode(x, y, slop){
      if(!this._screen) return -1;
      const { sx, sy, sr } = this._screen;
      let best = -1, bestD = Infinity;
      for(let i = 0; i < this._ids.length; i++){
        if(!this._rendered(i) || i >= sx.length) continue;
        const d = Math.hypot(sx[i] - x, sy[i] - y) - sr[i];
        if(d <= slop && d < bestD){ bestD = d; best = i; }
      }
      return best;
    }
    _hitEdge(x, y){
      if(!this._screen || !this._visibleEdges) return null;
      const { sx, sy } = this._screen;
      let best = null, bestD = 5;
      for(const k of this._visibleEdges){
        const a = this._edgeA[k], b = this._edgeB[k];
        const dx = sx[b] - sx[a], dy = sy[b] - sy[a], len2 = dx * dx + dy * dy;
        const t = len2 > 0 ? clamp(((x - sx[a]) * dx + (y - sy[a]) * dy) / len2, 0, 1) : 0;
        const d = Math.hypot(sx[a] + dx * t - x, sy[a] + dy * t - y);
        if(d < bestD){ bestD = d; best = this._edgeIds[k]; }
      }
      return best;
    }
    _setHover(i, p){
      if(i === this.hovered) return;
      const prevId = this.hoverId;
      if(this.hovered >= 0) this.emit('blurNode', { node: prevId, pointer: p ? this._pointerPayload(p) : null });
      this.hovered = i;
      this.hoverId = i >= 0 ? this._ids[i] : null;
      if(i >= 0){
        const set = new Set([i]);
        for(const nb of this._neighbors.get(this.hoverId) || []){ const j = this._index.get(nb); if(j !== undefined) set.add(j); }
        this.focusSet = set;
        this.emit('hoverNode', { node: this.hoverId, pointer: p ? this._pointerPayload(p) : null });
      }
      this.container.style.cursor = i >= 0 ? 'pointer' : '';
      this._wake();
    }
    _onDown(e){
      if(e.pointerType === 'mouse' && e.button !== 0) return;
      this._lastInteract = now();
      const p = this._local(e);
      this._pointers.set(e.pointerId, { x: p.x, y: p.y });
      try{ this.container.setPointerCapture(e.pointerId); }catch(_){}
      if(this._pointers.size === 2){
        this._cancelPress(e);
        const [a, b] = [...this._pointers.values()];
        const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
        this._pinch = { d0: Math.hypot(a.x - b.x, a.y - b.y) || 1, scale0: this.cam.scale, world: this.DOMtoCanvas(mid) };
        this.camAnim = null;
        return;
      }
      if(this._pointers.size > 2) return;
      const node = this._hitNode(p.x, p.y, e.pointerType === 'mouse' ? 4 : 12);
      const t = now();
      this._press = { id: e.pointerId, node, x0: p.x, y0: p.y, t0: t, lastT: t, moved: false, held: false, type: e.pointerType, offset: null };
      this._press.hold = setTimeout(() => {
        const press = this._press;
        if(!press || press.moved) return;
        press.held = true;
        this.emit('hold', { nodes: node >= 0 ? [this._ids[node]] : [], edges: [], event: { srcEvent: e }, pointer: this._pointerPayload(p) });
      }, 500);
      this._wake();
    }
    _cancelPress(e){
      const press = this._press;
      if(!press) return;
      clearTimeout(press.hold);
      if(press.moved) this._endDrag(e, true);
      this._press = null;
    }
    _onMove(e){
      const p = this._local(e);
      if(this._pointers.has(e.pointerId)) this._pointers.set(e.pointerId, { x: p.x, y: p.y });
      if(this._pinch && this._pointers.size >= 2){
        this._lastInteract = now();
        const [a, b] = [...this._pointers.values()];
        const d = Math.hypot(a.x - b.x, a.y - b.y) || 1;
        const r = this.options.renderer;
        const prevScale = this.cam.scale;
        const scale = clamp(this._pinch.scale0 * d / this._pinch.d0, r.minZoom, r.maxZoom);
        const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
        this.cam.scale = scale;
        this.cam.x = this._pinch.world.x - (mid.x - this.W / 2) / scale;
        this.cam.y = this._pinch.world.y - (mid.y - this.H / 2) / scale;
        this._cameraSet = true;
        this.emit('zoom', { direction: scale >= prevScale ? '+' : '-', scale, pointer: mid });
        this._wake();
        return;
      }
      const press = this._press;
      if(press && press.id === e.pointerId){
        this._lastInteract = now();
        const dist = Math.hypot(p.x - press.x0, p.y - press.y0);
        if(!press.moved && dist > (press.type === 'mouse' ? 4 : 8)) this._startDrag(e, p);
        if(press.moved){
          const t = now();
          if(this.dragging >= 0){
            const w = this.DOMtoCanvas(p);
            this.sim.dragTo(this.dragging, w.x + press.offset[0], w.y + press.offset[1], t - press.lastT);
          } else if(this._panning){
            this.cam.x -= (p.x - press.px) / this.cam.scale;
            this.cam.y -= (p.y - press.py) / this.cam.scale;
            press.px = p.x; press.py = p.y;
          }
          press.lastT = t;
          this._wake();
        }
        return;
      }
      if(e.pointerType !== 'mouse' || this._pointers.size) return;
      const t = now(), w = this.DOMtoCanvas(p);
      if(this.pointerWorld && t > this._lastPointerT){
        const dts = Math.max(0.008, (t - this._lastPointerT) / 1000);
        this.pointerVel[0] = this.pointerVel[0] * 0.5 + (w.x - this.pointerWorld[0]) / dts * 0.5;
        this.pointerVel[1] = this.pointerVel[1] * 0.5 + (w.y - this.pointerWorld[1]) / dts * 0.5;
      }
      this.pointerWorld = [w.x, w.y];
      this._pointerScreen = p;
      this._lastPointerT = t;
      this._lastInteract = t;
      if(this.options.interaction.hover !== false) this._setHover(this._hitNode(p.x, p.y, 4), p);
      this._wake();
    }
    _startDrag(e, p){
      const press = this._press;
      press.moved = true;
      clearTimeout(press.hold);
      const it = this.options.interaction;
      const mode = press.node >= 0 ? this.sim.mode[press.node] : -1;
      if(press.node >= 0 && it.dragNodes !== false && (mode === MODE_ACTIVE || mode === MODE_STATIC)){
        this.dragging = press.node;
        const w = this.DOMtoCanvas(p);
        press.offset = [this.sim.x[press.node] - w.x, this.sim.y[press.node] - w.y];
        this.camAnim = null;
        this.emit('dragStart', { nodes: [this._ids[press.node]], edges: [], event: { srcEvent: e }, pointer: this._pointerPayload(p) });
      } else if(it.dragView !== false){
        this._panning = true;
        press.px = p.x; press.py = p.y;
        this.camAnim = null;
        this._cameraSet = true;
        this.container.style.cursor = 'grabbing';
        this.emit('dragStart', { nodes: [], edges: [], event: { srcEvent: e }, pointer: this._pointerPayload(p) });
      }
    }
    _endDrag(e, cancelled){
      const press = this._press;
      const p = e ? this._local(e) : { x: 0, y: 0 };
      if(this.dragging >= 0){
        const i = this.dragging;
        if(this.sim.mode[i] === MODE_STATIC){
          // 고정 노드는 놓은 자리에 남는다 — 다음 재구축에서도 유지되도록 항목 좌표를 조용히 갱신.
          const item = this._items[i];
          item.x = this.sim.x[i]; item.y = this.sim.y[i];
          this.sim.vx[i] = this.sim.vy[i] = 0;
        } else this.sim.release(i, now() - press.lastT, cancelled);
        this.dragging = -1;
        this._energize();
        this.emit('dragEnd', { nodes: [this._ids[i]], edges: [], event: { srcEvent: e }, pointer: this._pointerPayload(p) });
      } else if(this._panning){
        this._panning = false;
        this.container.style.cursor = '';
        this.emit('dragEnd', { nodes: [], edges: [], event: { srcEvent: e }, pointer: this._pointerPayload(p) });
      }
    }
    _onUp(e, cancelled){
      this._pointers.delete(e.pointerId);
      try{ this.container.releasePointerCapture(e.pointerId); }catch(_){}
      if(this._pinch){
        if(this._pointers.size < 2) this._pinch = null;
        this._press = null;
        return;
      }
      const press = this._press;
      if(!press || press.id !== e.pointerId) return;
      clearTimeout(press.hold);
      this._lastInteract = now();
      if(press.moved) this._endDrag(e, cancelled);
      else if(!press.held && !cancelled) this._click(e);
      this._press = null;
      if(e.pointerType !== 'mouse'){ this.pointerWorld = null; this._setHover(-1, null); }
      this._wake();
    }
    _onLeave(e){
      if(this._press || e.pointerType !== 'mouse') return;
      this.pointerWorld = null;
      this._setHover(-1, this._local(e));
    }
    _click(e){
      const p = this._local(e);
      const node = this._hitNode(p.x, p.y, e.pointerType === 'mouse' ? 4 : 12);
      const it = this.options.interaction;
      const prevNodes = [...this.selectedNodes], prevEdges = [...this.selectedEdges];
      let edge = null;
      if(node >= 0){
        const id = this._ids[node];
        if(it.multiselect && (e.ctrlKey || e.metaKey)){
          if(this.selectedNodes.has(id)) this.selectedNodes.delete(id); else this.selectedNodes.add(id);
        } else {
          this.selectedNodes = new Set([id]);
        }
        this.selectedEdges = new Set(this._incident.get(id) || []);
      } else {
        edge = this._hitEdge(p.x, p.y);
        this.selectedNodes = new Set();
        this.selectedEdges = edge != null ? new Set([edge]) : new Set();
      }
      // nodes[0] 은 언제나 방금 누른 노드(app.js 가 p.nodes[0] 을 대상 노드로 쓴다).
      const clicked = node >= 0 ? this._ids[node] : null;
      const payload = {
        nodes: clicked == null ? [] : [clicked, ...[...this.selectedNodes].filter(id => id !== clicked)],
        edges: [...this.selectedEdges],
        event: { srcEvent: e },
        pointer: this._pointerPayload(p),
      };
      const nodeChanged = prevNodes.length !== this.selectedNodes.size || prevNodes.some(id => !this.selectedNodes.has(id));
      const edgeChanged = prevEdges.length !== this.selectedEdges.size || prevEdges.some(id => !this.selectedEdges.has(id));
      if(nodeChanged && prevNodes.length) this.emit('deselectNode', { nodes: payload.nodes, previousSelection: { nodes: prevNodes, edges: prevEdges } });
      if(nodeChanged && this.selectedNodes.size) this.emit('selectNode', payload);
      if(edgeChanged && node < 0 && prevEdges.length && !edge) this.emit('deselectEdge', { edges: [], previousSelection: { nodes: prevNodes, edges: prevEdges } });
      if(edgeChanged && node < 0 && edge != null) this.emit('selectEdge', payload);
      this.emit('click', payload);
      this._wake();
    }
    _onWheel(e){
      if(this.options.interaction.zoomView === false) return;
      e.preventDefault();
      const p = this._local(e), r = this.options.renderer;
      const before = this.DOMtoCanvas(p);
      const prev = this.cam.scale;
      this.cam.scale = clamp(prev * Math.exp(-e.deltaY * 0.0015), r.minZoom, r.maxZoom);
      const after = this.DOMtoCanvas(p);
      this.cam.x += before.x - after.x; this.cam.y += before.y - after.y;
      this._cameraSet = true;
      this.camAnim = null;
      this.emit('zoom', { direction: this.cam.scale >= prev ? '+' : '-', scale: this.cam.scale, pointer: p });
      this._wake();
    }

    // --- 카메라 ---
    _animDuration(animation){
      if(!animation) return 0;
      if(typeof animation === 'object') return animation.duration != null ? +animation.duration : 1000;
      return 1000;
    }
    _cameraTo(target, animation, trackIndex){
      this._cameraSet = true;
      const duration = this._animDuration(animation);
      if(duration <= 0 || this.W <= 0){
        this.camAnim = null;
        Object.assign(this.cam, target);
        this._wake();
        return;
      }
      this.camAnim = { from: Object.assign({}, this.cam), to: target, t0: now(), duration, track: trackIndex };
      this._wake();
    }
    _updateCamera(){
      const a = this.camAnim;
      if(!a) return;
      const k = easeInOutCubic((now() - a.t0) / a.duration);
      if(a.track != null && a.track >= 0){ a.to.x = this.sim.x[a.track]; a.to.y = this.sim.y[a.track]; }
      const ls0 = Math.log(a.from.scale), ls1 = Math.log(a.to.scale);
      this.cam.scale = Math.exp(lerp(ls0, ls1, k));
      this.cam.x = lerp(a.from.x, a.to.x, k);
      this.cam.y = lerp(a.from.y, a.to.y, k);
      if(k >= 1){
        this.camAnim = null;
        this.emit('animationFinished');
      }
    }
    _fitTarget(ids){
      const list = [];
      if(ids && ids.length){ for(const id of ids){ const i = this._index.get(id); if(i !== undefined) list.push(i); } }
      else for(let i = 0; i < this._ids.length; i++) if(this._rendered(i)) list.push(i);
      return list;
    }
    _fitNow(list, animation, opts){
      if(!list.length || this.W <= 0 || this.H <= 0) return;
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      for(const i of list){
        const r = this.sim.radius[i];
        x0 = Math.min(x0, this.sim.x[i] - r); x1 = Math.max(x1, this.sim.x[i] + r);
        y0 = Math.min(y0, this.sim.y[i] - r); y1 = Math.max(y1, this.sim.y[i] + r);
      }
      const rend = this.options.renderer, ins = this._fitInsets();
      // 여백은 영역에 비례(좁은 모바일에서 고정 48px 가 폭의 1/3 을 먹지 않게).
      const pad = clamp(Math.min(this.W - ins.l - ins.r, this.H - ins.t - ins.b) * 0.06, 12, 48);
      const maxZoom = opts && opts.maxZoomLevel != null ? +opts.maxZoomLevel : rend.fitMaxZoom;
      const minZoom = opts && opts.minZoomLevel != null ? +opts.minZoomLevel : rend.minZoom;
      const sw = Math.max(1, this.W - ins.l - ins.r - pad * 2), sh = Math.max(1, this.H - ins.t - ins.b - pad * 2);
      const scale = clamp(Math.min(sw / Math.max(1, x1 - x0), sh / Math.max(1, y1 - y0)), minZoom, maxZoom);
      // 컨트롤을 뺀 영역의 가운데에 맞춘다.
      const offX = (ins.l - ins.r) / 2, offY = (ins.t - ins.b) / 2;
      this._cameraTo({ x: (x0 + x1) / 2 - offX / scale, y: (y0 + y1) / 2 - offY / scale, scale }, animation);
    }

    // --- vis-network 호환 공개 API ---
    fit(opts){
      opts = opts || {};
      if(this.W <= 0 || this.H <= 0){ this._pendingFit = opts; this._cameraSet = true; return; }
      if(this._topologyDirty) this._rebuild();
      if(this._hiddenDirty) this._refreshHidden();
      this._fitNow(this._fitTarget(opts.nodes), opts.animation, opts);
    }
    focus(id, opts){
      opts = opts || {};
      const i = this._index.get(id);
      if(i === undefined) return;
      const scale = opts.scale != null ? +opts.scale : this.cam.scale;
      const off = opts.offset || { x: 0, y: 0 };
      this._cameraTo({ x: this.sim.x[i] - (off.x || 0) / scale, y: this.sim.y[i] - (off.y || 0) / scale, scale }, opts.animation, opts.offset ? null : i);
    }
    moveTo(opts){
      opts = opts || {};
      const pos = opts.position || { x: this.cam.x, y: this.cam.y };
      const scale = opts.scale != null ? +opts.scale : this.cam.scale;
      this._cameraTo({ x: +pos.x, y: +pos.y, scale }, opts.animation);
    }
    getScale(){ return this.cam.scale; }
    getViewPosition(){ return { x: this.cam.x, y: this.cam.y }; }
    DOMtoCanvas(p){ return { x: (p.x - this.W / 2) / this.cam.scale + this.cam.x, y: (p.y - this.H / 2) / this.cam.scale + this.cam.y }; }
    canvasToDOM(p){ return { x: (p.x - this.cam.x) * this.cam.scale + this.W / 2, y: (p.y - this.cam.y) * this.cam.scale + this.H / 2 }; }
    getPosition(id){
      const i = this._index.get(id);
      return i === undefined ? { x: 0, y: 0 } : { x: this.sim.x[i], y: this.sim.y[i] };
    }
    getPositions(ids){
      const out = {};
      const list = ids == null ? this._ids : (Array.isArray(ids) ? ids : [ids]);
      for(const id of list){ const i = this._index.get(id); if(i !== undefined) out[id] = { x: this.sim.x[i], y: this.sim.y[i] }; }
      return out;
    }
    getConnectedNodes(id){ return [...new Set(this._neighbors.get(id) || [])]; }
    getConnectedEdges(id){ return (this._incident.get(id) || []).slice(); }
    selectNodes(ids, highlightEdges){
      this.selectedNodes = new Set((ids || []).filter(id => this._index.has(id)));
      this.selectedEdges = new Set();
      if(highlightEdges !== false) for(const id of this.selectedNodes) for(const e of this._incident.get(id) || []) this.selectedEdges.add(e);
      this._wake();
    }
    selectEdges(ids){ this.selectedEdges = new Set(ids || []); this._wake(); }
    getSelectedNodes(){ return [...this.selectedNodes]; }
    getSelectedEdges(){ return [...this.selectedEdges]; }
    unselectAll(){ this.selectedNodes = new Set(); this.selectedEdges = new Set(); this._wake(); }
    setOptions(opts){
      if(!opts) return;
      if('physics' in opts){
        const on = opts.physics !== false && !(isPlainObject(opts.physics) && opts.physics.enabled === false);
        if(on && !this.physicsEnabled) this._energize();
        this.physicsEnabled = on;
      }
      const rest = Object.assign({}, opts);
      delete rest.physics;
      if(rest.groups) this.options.groups = rest.groups;
      delete rest.groups;
      this.options = deepMerge(this.options, rest);
      if(opts.simulation) Object.assign(this.sim.settings, opts.simulation);
      this._refreshTheme();
      this._wake();
    }
    stopSimulation(){ this.suspended = true; }
    startSimulation(){ this.suspended = false; this._energize(); this._wake(); }
    stabilize(){ this.sim.warmup(); this._energize(); this._wake(); }
    setSize(){ this._measure(); }
    redraw(){ this._refreshTheme(); this._wake(); }
    get rendererInfo(){ return { backend: this.backend, fallbackReason: this.fallbackReason, nodes: this._ids.length, edges: this._edgeItems.length }; }
    destroy(){
      this._destroyed = true;
      if(this._raf) cancelAnimationFrame(this._raf);
      if(this._ro) this._ro.disconnect();
      global.removeEventListener('resize', this._onWindowResize);
      document.removeEventListener('visibilitychange', this._onVisibility);
      const el = this.container;
      el.removeEventListener('pointerdown', this._h.down);
      el.removeEventListener('pointermove', this._h.move);
      el.removeEventListener('pointerup', this._h.up);
      el.removeEventListener('pointercancel', this._h.cancel);
      el.removeEventListener('pointerleave', this._h.leave);
      el.removeEventListener('wheel', this._h.wheel);
      for(const [ds, ev, fn] of this._dataHandlers || []) if(ds && ds.off) ds.off(ev, fn);
      if(this.renderer) this.renderer.dispose();
      this.gpuCanvas.remove();
      this.labelCanvas.remove();
    }
  }

  CG.Network = Network;
  CG.DEFAULT_OPTIONS = DEFAULT_OPTIONS;
})(window);
