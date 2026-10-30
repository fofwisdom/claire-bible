/**
 * Claire Graph Engine — 프레임 버퍼와 렌더링 백엔드.
 *
 * Network 는 매 프레임 화면(CSS px) 좌표의 노드·엣지를 Float32Array 프레임에 채운다. 프레임은
 * 렌더러와 무관하므로 같은 데이터를 WebGPU(인스턴싱 + 분석적 커버리지 안티에일리어싱)와
 * Canvas2D(경로 일괄 그리기) 중 어느 쪽으로도 그릴 수 있다. WebGPU 초기화가 실패하거나 장치를
 * 잃으면 Network 가 Canvas2D 로 자동 폴백한다.
 */
(function(global){
  'use strict';
  const CG = global.ClaireGraph;
  const { cssColor } = CG;

  // 노드: x, y, radius, shape, r, g, b, a, ringWidth, (예약 3) — 모두 CSS px, 직선 알파.
  const NODE_STRIDE = 12;
  // 엣지: x0, y0, x1, y1, width, dash, (예약 2), 시작색 rgba, 끝색 rgba.
  const EDGE_STRIDE = 16;
  const SHAPE_CIRCLE = 0, SHAPE_RING = 1, SHAPE_SQUARE = 2;

  class Frame {
    constructor(){
      this.nodes = new Float32Array(NODE_STRIDE * 512);
      this.edges = new Float32Array(EDGE_STRIDE * 512);
      this.nodeCount = 0;
      this.edgeCount = 0;
    }
    reset(){ this.nodeCount = 0; this.edgeCount = 0; }
    pushNode(x, y, r, shape, c, alpha, ring){
      if((this.nodeCount + 1) * NODE_STRIDE > this.nodes.length){
        const next = new Float32Array(this.nodes.length * 2); next.set(this.nodes); this.nodes = next;
      }
      const o = this.nodeCount++ * NODE_STRIDE, b = this.nodes;
      b[o] = x; b[o + 1] = y; b[o + 2] = r; b[o + 3] = shape;
      b[o + 4] = c[0]; b[o + 5] = c[1]; b[o + 6] = c[2]; b[o + 7] = alpha;
      b[o + 8] = ring || 0; b[o + 9] = 0; b[o + 10] = 0; b[o + 11] = 0;
    }
    pushEdge(x0, y0, x1, y1, width, dash, c0, a0, c1, a1){
      if((this.edgeCount + 1) * EDGE_STRIDE > this.edges.length){
        const next = new Float32Array(this.edges.length * 2); next.set(this.edges); this.edges = next;
      }
      const o = this.edgeCount++ * EDGE_STRIDE, b = this.edges;
      b[o] = x0; b[o + 1] = y0; b[o + 2] = x1; b[o + 3] = y1;
      b[o + 4] = width; b[o + 5] = dash || 0; b[o + 6] = 0; b[o + 7] = 0;
      b[o + 8] = c0[0]; b[o + 9] = c0[1]; b[o + 10] = c0[2]; b[o + 11] = a0;
      b[o + 12] = c1[0]; b[o + 13] = c1[1]; b[o + 14] = c1[2]; b[o + 15] = a1;
    }
  }

  // 사각형 하나(삼각형 2개)를 인스턴스마다 펼치고, 프래그먼트에서 화면 미분으로 픽셀 폭을 재서
  // 원·고리·둥근 사각형·선분의 커버리지를 해석적으로 계산한다(MSAA 없이도 서브픽셀까지 매끈).
  const WGSL = `
struct Globals { viewport: vec4f };  // CSS 너비, CSS 높이, 기기 픽셀 배율, 예약
@group(0) @binding(0) var<uniform> globals: Globals;

fn quadCorner(i: u32) -> vec2f {
  var corners = array<vec2f, 6>(
    vec2f(-1.0, -1.0), vec2f(1.0, -1.0), vec2f(-1.0, 1.0),
    vec2f(-1.0, 1.0), vec2f(1.0, -1.0), vec2f(1.0, 1.0));
  return corners[i];
}
fn toClip(p: vec2f) -> vec4f {
  return vec4f(p.x / globals.viewport.x * 2.0 - 1.0, 1.0 - p.y / globals.viewport.y * 2.0, 0.0, 1.0);
}
// 한 픽셀 폭 상자 필터가 구간 [-h, h] 를 덮는 비율.
fn cover(v: f32, h: f32, px: f32) -> f32 {
  return clamp((v + h) / px + 0.5, 0.0, 1.0) - clamp((v - h) / px + 0.5, 0.0, 1.0);
}

struct EdgeIn {
  @location(0) ends: vec4f,
  @location(1) style: vec4f,
  @location(2) c0: vec4f,
  @location(3) c1: vec4f,
};
struct EdgeOut {
  @builtin(position) position: vec4f,
  @location(0) local: vec2f,
  @location(1) @interpolate(flat) dims: vec4f,
  @location(2) @interpolate(flat) c0: vec4f,
  @location(3) @interpolate(flat) c1: vec4f,
};

@vertex
fn edgeVertex(input: EdgeIn, @builtin(vertex_index) vi: u32) -> EdgeOut {
  let a = input.ends.xy;
  let b = input.ends.zw;
  let delta = b - a;
  let len = length(delta);
  var dir = vec2f(1.0, 0.0);
  if (len > 0.0001) { dir = delta / len; }
  let nrm = vec2f(-dir.y, dir.x);
  let px = 1.0 / max(globals.viewport.z, 0.0001);
  let half = max(input.style.x, 0.0) * 0.5;
  let corner = quadCorner(vi);
  let local = vec2f(corner.x * (len * 0.5 + px * 1.5), corner.y * (half + px * 1.5));
  var out: EdgeOut;
  out.position = toClip((a + b) * 0.5 + dir * local.x + nrm * local.y);
  out.local = local;
  out.dims = vec4f(len, half, input.style.y, 0.0);
  out.c0 = input.c0;
  out.c1 = input.c1;
  return out;
}

@fragment
fn edgeFragment(input: EdgeOut) -> @location(0) vec4f {
  // 미분은 분기 전에(균일 제어 흐름) 계산한다.
  let pxX = max(length(vec2f(dpdx(input.local.x), dpdy(input.local.x))), 0.0001);
  let pxY = max(length(vec2f(dpdx(input.local.y), dpdy(input.local.y))), 0.0001);
  let len = input.dims.x;
  let dash = input.dims.z;
  var coverage = cover(input.local.x, len * 0.5, pxX) * cover(input.local.y, input.dims.y, pxY);
  if (dash > 0.0) {
    let period = dash * 2.0;
    let phase = fract((input.local.x + len * 0.5) / period);
    coverage = coverage * cover(phase - 0.25, 0.25, pxX / period);
  }
  let t = clamp(input.local.x / max(len, 0.0001) + 0.5, 0.0, 1.0);
  let colour = mix(input.c0, input.c1, t);
  let alpha = colour.a * coverage;
  if (alpha <= 0.002) { discard; }
  return vec4f(colour.rgb * alpha, alpha);
}

struct NodeIn {
  @location(0) geometry: vec4f,
  @location(1) colour: vec4f,
  @location(2) extra: vec4f,
};
struct NodeOut {
  @builtin(position) position: vec4f,
  @location(0) local: vec2f,
  @location(1) @interpolate(flat) shape: vec4f,
  @location(2) @interpolate(flat) colour: vec4f,
};

@vertex
fn nodeVertex(input: NodeIn, @builtin(vertex_index) vi: u32) -> NodeOut {
  let px = 1.0 / max(globals.viewport.z, 0.0001);
  let radius = max(input.geometry.z, 0.0);
  let ring = max(input.extra.x, 0.0);
  let extent = radius + ring * 0.5 + px * 1.5;
  let local = quadCorner(vi) * extent;
  var out: NodeOut;
  out.position = toClip(input.geometry.xy + local);
  out.local = local;
  out.shape = vec4f(radius, input.geometry.w, ring, 0.0);
  out.colour = input.colour;
  return out;
}

@fragment
fn nodeFragment(input: NodeOut) -> @location(0) vec4f {
  let radius = input.shape.x;
  let kind = input.shape.y;
  let ring = input.shape.z;
  let circle = length(input.local) - radius;
  let corner = radius * 0.32;
  let q = abs(input.local) - vec2f(radius - corner);
  let square = length(max(q, vec2f(0.0))) + min(max(q.x, q.y), 0.0) - corner;
  let pxCircle = max(length(vec2f(dpdx(circle), dpdy(circle))), 0.0001);
  let pxSquare = max(length(vec2f(dpdx(square), dpdy(square))), 0.0001);
  var coverage = clamp(0.5 - circle / pxCircle, 0.0, 1.0);
  if (kind > 1.5) {
    coverage = clamp(0.5 - square / pxSquare, 0.0, 1.0);
  } else if (kind > 0.5) {
    coverage = cover(circle, ring * 0.5, pxCircle);
  }
  let alpha = input.colour.a * coverage;
  if (alpha <= 0.002) { discard; }
  return vec4f(input.colour.rgb * alpha, alpha);
}
`;

  const PREMULTIPLIED_BLEND = {
    color: { srcFactor: 'one', dstFactor: 'one-minus-src-alpha', operation: 'add' },
    alpha: { srcFactor: 'one', dstFactor: 'one-minus-src-alpha', operation: 'add' },
  };

  const nextCapacity = (current, needed) => { let c = Math.max(64, current); while(c < needed) c *= 2; return c; };

  class WebGPURenderer {
    static async create(canvas){
      if(!navigator.gpu) throw new Error('WebGPU unavailable');
      const adapter = await navigator.gpu.requestAdapter({ powerPreference: 'low-power' });
      if(!adapter) throw new Error('WebGPU adapter unavailable');
      const device = await adapter.requestDevice();
      const context = canvas.getContext('webgpu');
      if(!context){ device.destroy(); throw new Error('WebGPU canvas context unavailable'); }
      const format = navigator.gpu.getPreferredCanvasFormat();
      context.configure({ device, format, alphaMode: 'premultiplied' });
      device.pushErrorScope('validation');
      const module = device.createShaderModule({ code: WGSL, label: 'claire-graph' });
      const info = module.getCompilationInfo ? await module.getCompilationInfo() : { messages: [] };
      const errors = info.messages.filter(m => m.type === 'error');
      if(errors.length){
        device.destroy();
        throw new Error('graph shader compilation failed: ' + errors.map(m => m.lineNum + ':' + m.linePos + ' ' + m.message).join('; '));
      }
      const bindLayout = device.createBindGroupLayout({
        entries: [{ binding: 0, visibility: GPUShaderStage.VERTEX | GPUShaderStage.FRAGMENT, buffer: { type: 'uniform' } }],
      });
      const layout = device.createPipelineLayout({ bindGroupLayouts: [bindLayout] });
      const attrs = count => Array.from({ length: count }, (_, k) => ({ shaderLocation: k, offset: k * 16, format: 'float32x4' }));
      const pipeline = (entry, stride, attrCount) => device.createRenderPipelineAsync({
        label: 'claire-graph-' + entry,
        layout,
        vertex: { module, entryPoint: entry + 'Vertex', buffers: [{ arrayStride: stride, stepMode: 'instance', attributes: attrs(attrCount) }] },
        fragment: { module, entryPoint: entry + 'Fragment', targets: [{ format, blend: PREMULTIPLIED_BLEND }] },
        primitive: { topology: 'triangle-list' },
      });
      const [edgePipeline, nodePipeline] = await Promise.all([
        pipeline('edge', EDGE_STRIDE * 4, 4),
        pipeline('node', NODE_STRIDE * 4, 3),
      ]);
      const uniform = device.createBuffer({ label: 'claire-graph-viewport', size: 16, usage: GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST });
      const bindGroup = device.createBindGroup({ layout: bindLayout, entries: [{ binding: 0, resource: { buffer: uniform } }] });
      const scopeError = await device.popErrorScope();
      if(scopeError){ device.destroy(); throw new Error(scopeError.message); }
      return new WebGPURenderer({ canvas, device, context, format, edgePipeline, nodePipeline, uniform, bindGroup });
    }

    constructor(parts){
      Object.assign(this, parts);
      this.kind = 'webgpu';
      this.viewport = new Float32Array(4);
      this.edgeCapacity = 0; this.nodeCapacity = 0;
      this.edgeBuffer = null; this.nodeBuffer = null;
      this._lostHandler = null;
      this._disposed = false;
      this.device.lost.then(info => { if(!this._disposed && this._lostHandler) this._lostHandler(new Error('WebGPU device lost: ' + (info && info.message))); });
      this.device.onuncapturederror = ev => { if(!this._disposed && this._lostHandler) this._lostHandler(ev.error || new Error('WebGPU error')); };
    }
    onLost(fn){ this._lostHandler = fn; }
    resize(cssW, cssH, dpr){
      const max = this.device.limits.maxTextureDimension2D;
      const scale = Math.min(dpr, max / Math.max(1, cssW), max / Math.max(1, cssH));
      this.canvas.width = Math.max(1, Math.round(cssW * scale));
      this.canvas.height = Math.max(1, Math.round(cssH * scale));
      this.viewport[0] = cssW; this.viewport[1] = cssH; this.viewport[2] = scale; this.viewport[3] = 0;
      this.device.queue.writeBuffer(this.uniform, 0, this.viewport);
    }
    _ensure(kind, count, stride){
      const capKey = kind + 'Capacity', bufKey = kind + 'Buffer';
      if(count <= this[capKey] && this[bufKey]) return;
      this[capKey] = nextCapacity(this[capKey], count);
      if(this[bufKey]) this[bufKey].destroy();
      this[bufKey] = this.device.createBuffer({
        label: 'claire-graph-' + kind + 's',
        size: this[capKey] * stride * 4,
        usage: GPUBufferUsage.VERTEX | GPUBufferUsage.COPY_DST,
      });
    }
    draw(frame){
      if(this._disposed || this.canvas.width <= 1 && this.canvas.height <= 1) return;
      const { device } = this;
      if(frame.edgeCount){
        this._ensure('edge', frame.edgeCount, EDGE_STRIDE);
        device.queue.writeBuffer(this.edgeBuffer, 0, frame.edges.buffer, frame.edges.byteOffset, frame.edgeCount * EDGE_STRIDE * 4);
      }
      if(frame.nodeCount){
        this._ensure('node', frame.nodeCount, NODE_STRIDE);
        device.queue.writeBuffer(this.nodeBuffer, 0, frame.nodes.buffer, frame.nodes.byteOffset, frame.nodeCount * NODE_STRIDE * 4);
      }
      const encoder = device.createCommandEncoder({ label: 'claire-graph-frame' });
      const pass = encoder.beginRenderPass({
        colorAttachments: [{
          view: this.context.getCurrentTexture().createView(),
          clearValue: { r: 0, g: 0, b: 0, a: 0 },
          loadOp: 'clear',
          storeOp: 'store',
        }],
      });
      pass.setBindGroup(0, this.bindGroup);
      if(frame.edgeCount){
        pass.setPipeline(this.edgePipeline);
        pass.setVertexBuffer(0, this.edgeBuffer);
        pass.draw(6, frame.edgeCount);
      }
      if(frame.nodeCount){
        pass.setPipeline(this.nodePipeline);
        pass.setVertexBuffer(0, this.nodeBuffer);
        pass.draw(6, frame.nodeCount);
      }
      pass.end();
      device.queue.submit([encoder.finish()]);
    }
    dispose(){
      if(this._disposed) return;
      this._disposed = true;
      try{ this.context.unconfigure(); }catch(_){}
      if(this.edgeBuffer) this.edgeBuffer.destroy();
      if(this.nodeBuffer) this.nodeBuffer.destroy();
      this.uniform.destroy();
      this.device.destroy();
    }
  }

  // 같은 스타일의 선·원을 경로 하나로 모아 그린다. 양끝 색이 다른 엣지만 선형 그라디언트를 쓴다.
  class Canvas2DRenderer {
    constructor(canvas){
      this.canvas = canvas;
      this.kind = 'canvas2d';
      this.ctx = canvas.getContext('2d', { alpha: true });
      if(!this.ctx) throw new Error('Canvas2D context unavailable');
      this.dpr = 1; this.w = 0; this.h = 0;
      this._batches = new Map();
    }
    onLost(){}
    resize(cssW, cssH, dpr){
      this.canvas.width = Math.max(1, Math.round(cssW * dpr));
      this.canvas.height = Math.max(1, Math.round(cssH * dpr));
      this.dpr = dpr; this.w = cssW; this.h = cssH;
    }
    draw(frame){
      const ctx = this.ctx;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
      ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      this._drawEdges(ctx, frame);
      this._drawNodes(ctx, frame);
    }
    _drawEdges(ctx, frame){
      const E = frame.edges, batches = this._batches;
      batches.clear();
      const gradientBudget = frame.edgeCount > 1500 ? 0 : Infinity;
      let gradients = 0;
      ctx.lineCap = 'butt';
      for(let k = 0; k < frame.edgeCount; k++){
        const o = k * EDGE_STRIDE;
        const a0 = E[o + 11], a1 = E[o + 15];
        if(a0 <= 0.002 && a1 <= 0.002) continue;
        const same = Math.abs(E[o + 8] - E[o + 12]) + Math.abs(E[o + 9] - E[o + 13]) + Math.abs(E[o + 10] - E[o + 14]) + Math.abs(a0 - a1) < 0.01;
        if(!same && gradients < gradientBudget){
          gradients++;
          const g = ctx.createLinearGradient(E[o], E[o + 1], E[o + 2], E[o + 3]);
          g.addColorStop(0, cssColor([E[o + 8], E[o + 9], E[o + 10]], a0));
          g.addColorStop(1, cssColor([E[o + 12], E[o + 13], E[o + 14]], a1));
          ctx.strokeStyle = g;
          ctx.lineWidth = E[o + 4];
          ctx.setLineDash(E[o + 5] > 0 ? [E[o + 5], E[o + 5]] : []);
          ctx.beginPath(); ctx.moveTo(E[o], E[o + 1]); ctx.lineTo(E[o + 2], E[o + 3]); ctx.stroke();
          continue;
        }
        const colour = same
          ? cssColor([E[o + 8], E[o + 9], E[o + 10]], Math.round(a0 * 64) / 64)
          : cssColor([(E[o + 8] + E[o + 12]) / 2, (E[o + 9] + E[o + 13]) / 2, (E[o + 10] + E[o + 14]) / 2], Math.round((a0 + a1) * 32) / 64);
        const key = colour + '|' + E[o + 4].toFixed(2) + '|' + E[o + 5];
        let list = batches.get(key);
        if(!list){ list = []; batches.set(key, list); }
        list.push(o);
      }
      for(const [key, list] of batches){
        const [colour, width, dash] = key.split('|');
        ctx.strokeStyle = colour;
        ctx.lineWidth = +width;
        ctx.setLineDash(+dash > 0 ? [+dash, +dash] : []);
        ctx.beginPath();
        for(const o of list){ ctx.moveTo(E[o], E[o + 1]); ctx.lineTo(E[o + 2], E[o + 3]); }
        ctx.stroke();
      }
      ctx.setLineDash([]);
    }
    _drawNodes(ctx, frame){
      // 프레임 순서(패킷 → 노드 → 고리)를 지키면서 연속한 같은 스타일끼리만 묶는다.
      const N = frame.nodes;
      let key = '', open = false, stroke = false;
      const flush = () => { if(!open) return; if(stroke) ctx.stroke(); else ctx.fill(); open = false; };
      for(let k = 0; k < frame.nodeCount; k++){
        const o = k * NODE_STRIDE;
        const r = N[o + 2], shape = N[o + 3], alpha = N[o + 7];
        if(r <= 0 || alpha <= 0.002) continue;
        const colour = cssColor([N[o + 4], N[o + 5], N[o + 6]], Math.round(alpha * 64) / 64);
        const isRing = shape === SHAPE_RING;
        const nextKey = colour + '|' + shape + '|' + (isRing ? N[o + 8].toFixed(2) : '');
        if(nextKey !== key){
          flush();
          key = nextKey; stroke = isRing;
          if(isRing){ ctx.strokeStyle = colour; ctx.lineWidth = N[o + 8]; }
          else ctx.fillStyle = colour;
          ctx.beginPath(); open = true;
        }
        const x = N[o], y = N[o + 1];
        if(shape === SHAPE_SQUARE){
          ctx.moveTo(x + r, y);
          if(ctx.roundRect) ctx.roundRect(x - r, y - r, r * 2, r * 2, r * 0.32);
          else ctx.rect(x - r, y - r, r * 2, r * 2);
        } else {
          ctx.moveTo(x + r, y);
          ctx.arc(x, y, r, 0, Math.PI * 2);
        }
      }
      flush();
    }
    dispose(){}
  }

  Object.assign(CG, {
    Frame, WebGPURenderer, Canvas2DRenderer, GRAPH_WGSL: WGSL,
    NODE_STRIDE, EDGE_STRIDE, SHAPE_CIRCLE, SHAPE_RING, SHAPE_SQUARE,
  });
})(window);
