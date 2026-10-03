/* Dependency-free SVG chart renderer. Charts are drawn only from the spec returned by the API (real results). */
(function (root) {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';
  const COLORS = ['#4f46e5', '#06b6d4', '#f59e0b', '#10b981', '#ef4444', '#8b5cf6', '#ec4899', '#64748b'];
  const W = 640, H = 300, M = { l: 68, r: 16, t: 12, b: 46 };

  function el(tag, attrs, ...kids) {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v);
    for (const k of kids) n.appendChild(typeof k === 'string' ? document.createTextNode(k) : k);
    return n;
  }
  const title = (t) => el('title', {}, t);
  function fmt(v, spec, full) {
    if (v === null || v === undefined || Number.isNaN(v)) return 'n/a';
    const cur = spec.format === 'money' ? (spec.currency || '') : '';
    const abs = Math.abs(v);
    if (!full && abs >= 1000) return cur + new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(v);
    return cur + new Intl.NumberFormat('en', { maximumFractionDigits: abs < 10 ? 2 : 0 }).format(v);
  }
  function niceMax(max) {
    if (max <= 0) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(max))), f = max / p;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
  }
  function axisY(svg, spec, min, max, ticks = 4) {
    for (let i = 0; i <= ticks; i++) {
      const v = min + ((max - min) * i) / ticks, y = M.t + (H - M.t - M.b) * (1 - i / ticks);
      svg.appendChild(el('line', { x1: M.l, x2: W - M.r, y1: y, y2: y, stroke: '#e2e8f0' }));
      svg.appendChild(el('text', { x: M.l - 6, y: y + 4, 'text-anchor': 'end', 'font-size': 10, fill: '#64748b' }, fmt(v, spec)));
    }
  }
  const trunc = (s, n) => (String(s).length > n ? String(s).slice(0, n - 1) + '…' : String(s));
  const ylab = (svg, t) => t && svg.appendChild(el('text', { x: 12, y: H / 2, transform: `rotate(-90 12 ${H / 2})`, 'text-anchor': 'middle', 'font-size': 10, fill: '#64748b' }, trunc(t, 40)));
  const xlab = (svg, t) => t && svg.appendChild(el('text', { x: (M.l + W - M.r) / 2, y: H - 4, 'text-anchor': 'middle', 'font-size': 10, fill: '#64748b' }, trunc(t, 40)));

  function bar(spec, svg) {
    const L = spec.labels, S = spec.series, n = L.length;
    const max = niceMax(Math.max(0, ...S.flatMap((s) => s.values.map((v) => v || 0)), spec.reference ? spec.reference.value : 0));
    axisY(svg, spec, 0, max);
    const pw = (W - M.l - M.r) / n, bw = (pw * 0.75) / S.length;
    L.forEach((lab, i) => {
      S.forEach((s, j) => {
        const v = s.values[i] || 0, h = (H - M.t - M.b) * (v / max), x = M.l + i * pw + pw * 0.125 + j * bw;
        svg.appendChild(el('rect', { x, y: H - M.b - h, width: Math.max(bw - 1, 1), height: Math.max(h, 0), fill: COLORS[j % 8], rx: 2 }, title(`${lab}${S.length > 1 ? ' – ' + s.name : ''}: ${fmt(v, spec, true)}`)));
      });
      const rot = n > 6;
      svg.appendChild(el('text', { x: M.l + i * pw + pw / 2, y: H - M.b + 14, 'text-anchor': rot ? 'end' : 'middle', 'font-size': 10, fill: '#475569', transform: rot ? `rotate(-35 ${M.l + i * pw + pw / 2} ${H - M.b + 14})` : '' }, trunc(lab, 14)));
    });
    if (spec.reference) refLine(svg, spec, spec.reference.value / max, false);
    ylab(svg, spec.y_label);
  }
  function refLine(svg, spec, frac, horizontal) {
    if (horizontal) {
      const x = M.l + (W - M.l - M.r) * frac;
      svg.appendChild(el('line', { x1: x, x2: x, y1: M.t, y2: H - M.b, stroke: '#ef4444', 'stroke-dasharray': '4 3' }, title(spec.reference.label || 'Reference')));
    } else {
      const y = H - M.b - (H - M.t - M.b) * frac;
      svg.appendChild(el('line', { x1: M.l, x2: W - M.r, y1: y, y2: y, stroke: '#ef4444', 'stroke-dasharray': '4 3' }, title(spec.reference.label || 'Reference')));
    }
  }
  function hbar(spec, svg) {
    const L = spec.labels, v = spec.series[0].values, n = L.length, left = 130;
    const max = niceMax(Math.max(0, ...v.map((x) => x || 0), spec.reference ? spec.reference.value : 0));
    const ph = (H - M.t - 20) / n;
    for (let i = 0; i <= 4; i++) {
      const x = left + (W - left - M.r) * (i / 4);
      svg.appendChild(el('line', { x1: x, x2: x, y1: M.t, y2: H - 20, stroke: '#e2e8f0' }));
      svg.appendChild(el('text', { x, y: H - 6, 'text-anchor': 'middle', 'font-size': 10, fill: '#64748b' }, fmt(max * i / 4, spec)));
    }
    L.forEach((lab, i) => {
      const w = (W - left - M.r) * ((v[i] || 0) / max), y = M.t + i * ph;
      svg.appendChild(el('rect', { x: left, y: y + ph * 0.15, width: Math.max(w, 0), height: ph * 0.7, fill: COLORS[0], rx: 2 }, title(`${lab}: ${fmt(v[i], spec, true)}`)));
      svg.appendChild(el('text', { x: left - 6, y: y + ph / 2 + 4, 'text-anchor': 'end', 'font-size': 10.5, fill: '#334155' }, trunc(lab, 20)));
      svg.appendChild(el('text', { x: left + w + 4, y: y + ph / 2 + 4, 'font-size': 10, fill: '#64748b' }, fmt(v[i], spec)));
    });
    if (spec.reference) {
      const x = left + (W - left - M.r) * (spec.reference.value / max);
      svg.appendChild(el('line', { x1: x, x2: x, y1: M.t, y2: H - 20, stroke: '#ef4444', 'stroke-dasharray': '4 3' }, title(spec.reference.label || 'Reference')));
    }
  }
  function line(spec, svg) {
    const L = spec.labels, S = spec.series, n = L.length;
    const all = S.flatMap((s) => s.values).concat(spec.band ? spec.band.upper.concat(spec.band.lower) : []).filter((v) => v !== null && v !== undefined);
    const lo = Math.min(0, ...all), max = niceMax(Math.max(...all)), span = max - lo || 1;
    axisY(svg, spec, lo, max);
    const X = (i) => M.l + (n === 1 ? 0.5 : i / (n - 1)) * (W - M.l - M.r), Y = (v) => H - M.b - ((v - lo) / span) * (H - M.t - M.b);
    if (spec.band) {
      const pts = [];
      spec.band.upper.forEach((v, i) => v !== null && pts.push([X(i), Y(v)]));
      const lows = [];
      spec.band.lower.forEach((v, i) => v !== null && lows.push([X(i), Y(v)]));
      if (pts.length) svg.appendChild(el('polygon', { points: pts.concat(lows.reverse()).map((p) => p.join(',')).join(' '), fill: '#f59e0b', opacity: 0.18 }, title('Approximate 95% interval')));
    }
    S.forEach((s, j) => {
      let d = '', pen = false;
      s.values.forEach((v, i) => { if (v === null || v === undefined) { pen = false; return; } d += `${pen ? 'L' : 'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)} `; pen = true; });
      svg.appendChild(el('path', { d, fill: 'none', stroke: s.dashed ? '#f59e0b' : COLORS[j % 8], 'stroke-width': 2, 'stroke-dasharray': s.dashed ? '6 4' : '' }));
      if (n <= 60) s.values.forEach((v, i) => v !== null && v !== undefined && svg.appendChild(el('circle', { cx: X(i), cy: Y(v), r: 2.6, fill: s.dashed ? '#f59e0b' : COLORS[j % 8] }, title(`${L[i]} – ${s.name}: ${fmt(v, spec, true)}`))));
    });
    const step = Math.max(1, Math.ceil(n / 10));
    L.forEach((lab, i) => { if (i % step === 0) svg.appendChild(el('text', { x: X(i), y: H - M.b + 14, 'text-anchor': 'end', 'font-size': 10, fill: '#475569', transform: `rotate(-35 ${X(i)} ${H - M.b + 14})` }, trunc(lab, 10))); });
    ylab(svg, spec.y_label);
  }
  function pie(spec, svg) {
    const v = spec.series[0].values.map((x) => Math.max(x || 0, 0)), total = v.reduce((a, b) => a + b, 0) || 1, cx = 200, cy = 150, r = 120, ri = 62;
    let a0 = -Math.PI / 2;
    v.forEach((x, i) => {
      const a1 = a0 + (x / total) * Math.PI * 2, large = a1 - a0 > Math.PI ? 1 : 0;
      const p = (a, rad) => [cx + rad * Math.cos(a), cy + rad * Math.sin(a)];
      const [x0, y0] = p(a0, r), [x1, y1] = p(Math.min(a1, a0 + Math.PI * 2 - 1e-4), r), [x2, y2] = p(Math.min(a1, a0 + Math.PI * 2 - 1e-4), ri), [x3, y3] = p(a0, ri);
      svg.appendChild(el('path', { d: `M${x0},${y0} A${r},${r} 0 ${large} 1 ${x1},${y1} L${x2},${y2} A${ri},${ri} 0 ${large} 0 ${x3},${y3} Z`, fill: COLORS[i % 8] }, title(`${spec.labels[i]}: ${fmt(x, spec, true)} (${(x / total * 100).toFixed(1)}%)`)));
      svg.appendChild(el('rect', { x: 370, y: 40 + i * 22, width: 11, height: 11, fill: COLORS[i % 8], rx: 2 }));
      svg.appendChild(el('text', { x: 388, y: 50 + i * 22, 'font-size': 11.5, fill: '#334155' }, `${trunc(spec.labels[i], 18)} · ${(x / total * 100).toFixed(1)}%`));
      a0 = a1;
    });
  }
  function scatter(spec, svg) {
    const P = spec.points, xs = P.map((p) => p.x), ys = P.map((p) => p.y);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(0, ...ys), y1 = niceMax(Math.max(...ys));
    axisY(svg, spec, y0, y1);
    P.forEach((p) => svg.appendChild(el('circle', { cx: M.l + ((p.x - x0) / (x1 - x0 || 1)) * (W - M.l - M.r), cy: H - M.b - ((p.y - y0) / (y1 - y0 || 1)) * (H - M.t - M.b), r: 2.8, fill: COLORS[0], opacity: 0.55 }, title(`(${fmt(p.x, spec, true)}, ${fmt(p.y, spec, true)})`))));
    svg.appendChild(el('text', { x: M.l, y: H - M.b + 14, 'font-size': 10, fill: '#64748b' }, fmt(x0, spec)));
    svg.appendChild(el('text', { x: W - M.r, y: H - M.b + 14, 'text-anchor': 'end', 'font-size': 10, fill: '#64748b' }, fmt(x1, spec)));
    xlab(svg, spec.x_label); ylab(svg, spec.y_label);
  }
  function histogram(spec, svg) {
    const B = spec.bins, max = niceMax(Math.max(...B.map((b) => b.count))), lo = B[0].x0, hi = B[B.length - 1].x1;
    axisY(svg, { format: 'number' }, 0, max);
    B.forEach((b) => {
      const x = M.l + ((b.x0 - lo) / (hi - lo || 1)) * (W - M.l - M.r), w = ((b.x1 - b.x0) / (hi - lo || 1)) * (W - M.l - M.r), h = (H - M.t - M.b) * (b.count / max);
      svg.appendChild(el('rect', { x, y: H - M.b - h, width: Math.max(w - 1, 1), height: h, fill: COLORS[0] }, title(`${fmt(b.x0, spec, true)} – ${fmt(b.x1, spec, true)}: ${b.count}`)));
    });
    svg.appendChild(el('text', { x: M.l, y: H - M.b + 14, 'font-size': 10, fill: '#64748b' }, fmt(lo, spec)));
    svg.appendChild(el('text', { x: W - M.r, y: H - M.b + 14, 'text-anchor': 'end', 'font-size': 10, fill: '#64748b' }, fmt(hi, spec)));
    xlab(svg, spec.x_label); ylab(svg, 'Count');
  }

  function render(spec) {
    const wrap = document.createElement('div');
    wrap.className = 'chart';
    const t = document.createElement('div'); t.className = 'chart-title'; t.textContent = spec.title || ''; wrap.appendChild(t);
    if (spec.type === 'kpi') { const v = document.createElement('div'); v.className = 'score'; v.textContent = fmt(spec.value, spec, true); wrap.appendChild(v); return wrap; }
    const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': spec.title || 'chart' });
    try {
      ({ bar, hbar, line, pie, scatter, histogram })[spec.type](spec, svg);
    } catch (e) {
      const m = document.createElement('div'); m.className = 'warn'; m.textContent = 'This chart could not be drawn.'; wrap.appendChild(m); return wrap;
    }
    wrap.appendChild(svg);
    if (spec.series && spec.series.length > 1 || (spec.band)) {
      const lg = document.createElement('div'); lg.className = 'legend';
      spec.series.forEach((s, i) => { const it = document.createElement('span'); const sw = document.createElement('i'); sw.style.background = s.dashed ? '#f59e0b' : COLORS[i % 8]; it.appendChild(sw); it.appendChild(document.createTextNode(s.name)); lg.appendChild(it); });
      if (spec.band) { const it = document.createElement('span'); it.textContent = 'Shaded band: approximate 95% interval'; lg.appendChild(it); }
      wrap.appendChild(lg);
    }
    return wrap;
  }
  root.Charts = { render, fmt };
})(typeof window !== 'undefined' ? window : globalThis);
