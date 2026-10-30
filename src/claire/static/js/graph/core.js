/**
 * Claire Graph Engine — 공통 기반 (네임스페이스, 수학·해시·색 유틸, DataSet, 이벤트).
 *
 * Shopify Editions Fall '26 그래프의 구현 방식(렌더러 독립 Float32Array 프레임 버퍼 →
 * WebGPU/Canvas2D 백엔드 자동 폴백, 허브-스포크 계층 물리, 흐름 패킷, 호버 포커스)을
 * 지식 그래프용으로 새로 구현한 엔진이다. app.js 가 쓰던 vis-network API 표면
 * (DataSet + Network)을 그대로 제공해 기존 워크스페이스 로직을 바꾸지 않고 교체한다.
 */
(function(global){
  'use strict';
  const CG = global.ClaireGraph = global.ClaireGraph || {};

  // --- 수학 ---
  const clamp = (v, lo, hi) => v < lo ? lo : (v > hi ? hi : v);
  const lerp = (a, b, t) => a + (b - a) * t;
  const smoothstep = t => { const c = clamp(t, 0, 1); return c * c * (3 - 2 * c); };
  const easeInOutCubic = t => { const c = clamp(t, 0, 1); return c < 0.5 ? 4 * c * c * c : 1 - Math.pow(-2 * c + 2, 3) / 2; };
  const easeOutCubic = t => 1 - Math.pow(1 - clamp(t, 0, 1), 3);
  // 프레임레이트와 무관한 지수 감쇠 보간 계수: rate 가 클수록 빨리 목표에 붙는다.
  const approach = (rate, dt) => 1 - Math.exp(-rate * dt);

  // --- 결정적 해시 (FNV-1a + avalanche) — 같은 id 는 언제나 같은 배치·위상을 얻는다 ---
  function hash32(str, seed){
    let h = (2166136261 ^ (seed >>> 0)) >>> 0;
    const s = String(str);
    for(let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
    h = Math.imul(h ^ (h >>> 16), 2246822507);
    h = Math.imul(h ^ (h >>> 13), 3266489909);
    return (h ^ (h >>> 16)) >>> 0;
  }
  const hashUnit = (str, seed) => hash32(str, seed) / 4294967296;          // [0, 1)
  const hashSigned = (str, seed) => hashUnit(str, seed) * 2 - 1;           // [-1, 1)

  // --- 색: CSS 문자열 → 직선(straight) RGBA [0..1] (캐시) ---
  const colorCache = new Map();
  let probeCtx = null;
  function parseColor(input, fallback){
    if(Array.isArray(input)) return input;
    const key = String(input || '');
    if(!key) return fallback || [0.55, 0.58, 0.62, 1];
    const hit = colorCache.get(key);
    if(hit) return hit;
    let out = null;
    let m = /^#([0-9a-f]{3,8})$/i.exec(key);
    if(m){
      let h = m[1];
      if(h.length === 3 || h.length === 4) h = h.split('').map(c => c + c).join('');
      const n = parseInt(h.slice(0, 6), 16);
      out = [(n >> 16 & 255) / 255, (n >> 8 & 255) / 255, (n & 255) / 255, h.length === 8 ? parseInt(h.slice(6), 16) / 255 : 1];
    } else if((m = /^rgba?\(([^)]+)\)$/i.exec(key))){
      const p = m[1].split(/[\s,/]+/).filter(Boolean).map(parseFloat);
      out = [p[0] / 255, p[1] / 255, p[2] / 255, p.length > 3 ? p[3] : 1];
    } else {
      // 이름 색 등 — 브라우저에 한 번 해석시켜 #rrggbb 로 정규화한다.
      try{
        probeCtx = probeCtx || document.createElement('canvas').getContext('2d');
        probeCtx.fillStyle = '#000'; probeCtx.fillStyle = key;
        const norm = probeCtx.fillStyle;
        if(norm && norm !== key) out = parseColor(norm, fallback);
      }catch(_){}
    }
    out = out || fallback || [0.55, 0.58, 0.62, 1];
    colorCache.set(key, out);
    return out;
  }
  const mixColor = (a, b, t) => [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t), lerp(a[3], b[3], t)];
  function cssColor(c, alpha){
    const a = clamp(alpha == null ? c[3] : alpha, 0, 1);
    return 'rgba(' + Math.round(c[0] * 255) + ',' + Math.round(c[1] * 255) + ',' + Math.round(c[2] * 255) + ',' + a.toFixed(3) + ')';
  }

  // --- 이벤트 ---
  class Emitter {
    constructor(){ this._handlers = new Map(); }
    on(name, fn){
      if(!this._handlers.has(name)) this._handlers.set(name, []);
      this._handlers.get(name).push(fn);
      return this;
    }
    off(name, fn){
      const list = this._handlers.get(name);
      if(!list) return this;
      if(!fn) this._handlers.delete(name);
      else this._handlers.set(name, list.filter(f => f !== fn));
      return this;
    }
    emit(name, payload){
      const list = this._handlers.get(name);
      if(!list || !list.length) return;
      for(const fn of list.slice()){
        try{ fn(payload); }catch(err){ console.warn('ClaireGraph handler error (' + name + '):', err); }
      }
    }
  }

  // --- DataSet: vis-data 호환 부분집합 (add/update/get/getIds/forEach/remove/clear/length/on) ---
  // update 는 vis-data 와 같이 최상위 필드만 얕게 병합한다(font/color 같은 중첩 객체는 통째로 교체).
  class DataSet extends Emitter {
    constructor(items){
      super();
      this._items = new Map();
      if(items) this.add(items);
    }
    get length(){ return this._items.size; }
    add(items){
      const list = Array.isArray(items) ? items : [items];
      const ids = [];
      for(const item of list){
        if(!item || item.id == null) throw new Error('DataSet.add: item without id');
        if(this._items.has(item.id)) throw new Error('Cannot add item: item with id ' + item.id + ' already exists');
        this._items.set(item.id, Object.assign({}, item));
        ids.push(item.id);
      }
      if(ids.length) this.emit('add', { items: ids });
      return ids;
    }
    update(items){
      const list = Array.isArray(items) ? items : [items];
      const added = [], updated = [], fields = new Set();
      for(const item of list){
        if(!item || item.id == null) continue;
        const cur = this._items.get(item.id);
        if(cur){
          for(const k in item) if(k !== 'id') fields.add(k);
          Object.assign(cur, item);
          updated.push(item.id);
        } else {
          this._items.set(item.id, Object.assign({}, item));
          added.push(item.id);
        }
      }
      if(added.length) this.emit('add', { items: added });
      if(updated.length) this.emit('update', { items: updated, fields: fields });
      return added.concat(updated);
    }
    get(arg){
      if(arg == null || (typeof arg === 'object' && !Array.isArray(arg))){
        const filter = arg && typeof arg.filter === 'function' ? arg.filter : null;
        const out = [];
        for(const item of this._items.values()) if(!filter || filter(item)) out.push(item);
        return out;
      }
      if(Array.isArray(arg)) return arg.map(id => this._items.get(id)).filter(Boolean);
      return this._items.get(arg) || null;
    }
    getIds(arg){
      const filter = arg && typeof arg.filter === 'function' ? arg.filter : null;
      const out = [];
      for(const [id, item] of this._items) if(!filter || filter(item)) out.push(id);
      return out;
    }
    forEach(fn){ for(const [id, item] of this._items) fn(item, id); }
    map(fn){ const out = []; for(const [id, item] of this._items) out.push(fn(item, id)); return out; }
    remove(ids){
      const list = Array.isArray(ids) ? ids : [ids];
      const removed = [];
      for(const raw of list){
        const id = raw && typeof raw === 'object' ? raw.id : raw;
        if(this._items.delete(id)) removed.push(id);
      }
      if(removed.length) this.emit('remove', { items: removed });
      return removed;
    }
    clear(){
      const ids = [...this._items.keys()];
      this._items.clear();
      if(ids.length) this.emit('remove', { items: ids });
      return ids;
    }
  }

  Object.assign(CG, {
    clamp, lerp, smoothstep, easeInOutCubic, easeOutCubic, approach,
    hash32, hashUnit, hashSigned,
    parseColor, mixColor, cssColor,
    Emitter, DataSet,
  });
})(window);
