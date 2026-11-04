/* NetLab — topologiya kanvasi (SVG): joylash, ko'chirish, kabel ulash,
   pan/zoom, paket animatsiyasi, kabel yuklamasi. */
(function (NL) {
  'use strict';
  const S = NL.store;
  const C = NL.canvas = { mode: 'select', placeType: null, linkSrc: null, tx: 0, ty: 0, k: 1, geom: {}, overlay: [], pickCb: null };
  let svg, vp, gCov, gLinks, gPkts, gDevs, gGhost, stageEl, hintEl, emptyEl, scaleEl;
  const R = 26;   // qurilma radiusi

  C.init = function (stage) {
    stageEl = stage;
    svg = NL.s('svg', { class: 'canvas' });
    const defs = NL.s('defs');
    const pat = NL.s('pattern', { id: 'gridp', width: 32, height: 32, patternUnits: 'userSpaceOnUse' });
    pat.appendChild(NL.s('circle', { cx: 1, cy: 1, r: 1.1, class: 'grid-dot' }));
    defs.appendChild(pat);
    const mk = NL.s('marker', { id: 'mvarrow', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse' });
    mk.appendChild(NL.s('path', { d: 'M0,0 L10,5 L0,10 z', class: 'mvhead' }));
    defs.appendChild(mk);
    svg.appendChild(defs);
    vp = NL.s('g');
    vp.appendChild(NL.s('rect', { x: -20000, y: -20000, width: 40000, height: 40000, fill: 'url(#gridp)', class: 'bg' }));
    gCov = NL.s('g'); gLinks = NL.s('g'); gPkts = NL.s('g'); gDevs = NL.s('g'); gGhost = NL.s('g');
    vp.append(gCov, gLinks, gPkts, gDevs, gGhost);
    svg.appendChild(vp);
    stage.appendChild(svg);
    hintEl = NL.h('div', { class: 'stage-hint' });
    emptyEl = NL.h('div', { class: 'stage-empty', html: '<div>Chapdagi paneldan qurilma tanlang va shu yerga bosing.<br>Kabel: <b>C</b> — ikki qurilmani ketma-ket bosing. Qurilmaga kirish: <b>ikki marta bosing</b>.</div>' });
    const zoom = NL.h('div', { class: 'stage-zoom' }, [
      NL.h('button', { title: 'Kattalashtirish', text: '+', onclick: () => zoomAt(1.2) }),
      NL.h('button', { title: 'Kichiklashtirish', text: '−', onclick: () => zoomAt(1 / 1.2) }),
      NL.h('button', { title: 'Hammasini ko\'rsatish', text: '⤢', onclick: () => C.fit() }),
    ]);
    scaleEl = NL.h('div', { class: 'stage-scale', title: 'Masshtab (loyiha: 1 px = meters_per_px m)' }, [NL.h('i'), NL.h('span')]);
    stage.append(hintEl, emptyEl, zoom, scaleEl);
    bind();
    applyView();
    requestAnimationFrame(tick);
    NL.on('change', C.render);
    NL.on('stats', updateStats);
    NL.on('ev:packet', (p) => C.packet(p));
  };

  C.setMode = function (mode, placeType) {
    C.mode = mode; C.placeType = placeType || null; C.linkSrc = null;
    gGhost.innerHTML = '';
    stageEl.className = 'panel lab-stage mode-' + mode;
    const hints = {
      select: '', link: 'Kabel: birinchi qurilmani bosing (Esc — bekor)', delete: 'O\'chirish: qurilma yoki kabelni bosing',
      place: `Joylash: ${placeType ? (NL.store.info.device_types[placeType] || {}).label : ''} — kanvasga bosing`,
      pick: 'Harakat manzili: kanvasdagi nuqtani bosing (Esc — bekor)',
    };
    hintEl.textContent = hints[mode] || '';
    NL.emit('mode', mode);
    C.render();
  };

  function applyView() { vp.setAttribute('transform', `translate(${C.tx},${C.ty}) scale(${C.k})`); updateScale(); }
  function updateScale() {
    if (!scaleEl || !S.project) return;
    const mpp = NL.wifi.mpp();
    const target = 120 / C.k * mpp;                 // ~120 ekran px ga to'g'ri keladigan metr
    const p = Math.pow(10, Math.floor(Math.log10(target)));
    const nice = [1, 2, 5, 10].map(x => x * p).filter(x => x <= target).pop() || p;
    scaleEl.firstChild.style.width = (nice / mpp * C.k) + 'px';
    scaleEl.lastChild.textContent = nice >= 1000 ? (nice / 1000) + ' km' : nice + ' m';
  }
  function toWorld(ev) {
    const r = svg.getBoundingClientRect();
    return { x: (ev.clientX - r.left - C.tx) / C.k, y: (ev.clientY - r.top - C.ty) / C.k };
  }
  function zoomAt(f, cx, cy) {
    const r = svg.getBoundingClientRect();
    cx = cx == null ? r.width / 2 : cx; cy = cy == null ? r.height / 2 : cy;
    const k2 = Math.min(3, Math.max(0.25, C.k * f));
    C.tx = cx - (cx - C.tx) * (k2 / C.k); C.ty = cy - (cy - C.ty) * (k2 / C.k); C.k = k2;
    applyView();
  }
  C.fit = function () {
    const ds = Object.values(S.project.devices);
    const r = svg.getBoundingClientRect();
    if (!ds.length || !r.width) { C.tx = 0; C.ty = 0; C.k = 1; applyView(); return; }
    const xs = ds.map(d => d.x), ys = ds.map(d => d.y);
    const minx = Math.min(...xs) - 80, maxx = Math.max(...xs) + 80, miny = Math.min(...ys) - 80, maxy = Math.max(...ys) + 80;
    C.k = Math.min(1.6, Math.max(0.3, Math.min(r.width / (maxx - minx), r.height / (maxy - miny))));
    C.tx = (r.width - (maxx - minx) * C.k) / 2 - minx * C.k;
    C.ty = (r.height - (maxy - miny) * C.k) / 2 - miny * C.k;
    applyView();
  };

  // ---------- render ----------
  C.render = function () {
    if (!S.project || !svg) return;
    emptyEl.style.display = Object.keys(S.project.devices).length ? 'none' : '';
    renderCoverage();
    renderLinks();
    renderDevices();
    updateStats();
    updateScale();
  };

  C.pickPoint = function (cb) { C.pickCb = cb; C.setMode('pick'); };
  C.setOverlay = function (moves) { C.overlay = moves || []; renderCoverage(); };

  // Wi-Fi AP qamrovi: tashqi doira — aloqa chegarasi (RSSI = -82 dBm), ichki — yuqori MCS zonasi (SNR >= 25 dB)
  function renderCoverage() {
    gCov.innerHTML = '';
    // ssenariy harakatlari (oyna ochiq bo'lsa): qurilmadan manzilgacha strelka
    let prevPos = {};
    for (const e of C.overlay) {
      const d = S.dev(e.device); if (!d || !isFinite(e.x) || !isFinite(e.y)) continue;
      const [x0, y0] = prevPos[e.device] || [d.x, d.y];
      gCov.appendChild(NL.s('line', { x1: x0, y1: y0, x2: e.x, y2: e.y, class: 'mvline', 'marker-end': 'url(#mvarrow)' }));
      gCov.appendChild(NL.s('circle', { cx: e.x, cy: e.y, r: 6, class: 'mvdot' }));
      const t = NL.s('text', { x: (x0 + e.x) / 2, y: (y0 + e.y) / 2 - 8, class: 'mvlbl' });
      t.textContent = `${e.device}: t=${e.t}s → ${(+e.t + (+e.duration || 0))}s`;
      gCov.appendChild(t);
      prevPos[e.device] = [e.x, e.y];
    }
    if (!S.info || !S.info.wifi) return;
    const mpp = NL.wifi.mpp();
    for (const [name, d] of Object.entries(S.project.devices)) {
      if (d.type !== 'wifiap') continue;
      const c = NL.wifi.apConfig(d);
      const sel = S.selection && S.selection.id === name;
      const rMax = NL.wifi.range(c) / mpp, rHi = NL.wifi.rangeForSnr(c, 25) / mpp;
      gCov.appendChild(NL.s('circle', { cx: d.x, cy: d.y, r: rMax, class: 'cov' + (sel ? ' sel' : '') }));
      gCov.appendChild(NL.s('circle', { cx: d.x, cy: d.y, r: rHi, class: 'cov hi' + (sel ? ' sel' : '') }));
      if (sel) {
        const t = NL.s('text', { x: d.x, y: d.y - rMax - 6, class: 'covlbl' });
        t.textContent = `qamrov ~${Math.round(NL.wifi.range(c))} m · ${c.standard.replace('80211', '802.11')} ${c.band} GHz`;
        gCov.appendChild(t);
      }
    }
  }

  function devIP(name) {
    const d = S.dev(name);
    const ifs = (d.config && d.config.interfaces) || {};
    const ips = Object.values(ifs).map(i => i.ip).filter(Boolean);
    if (!ips.length) return '';
    return ips[0] + (ips.length > 1 ? ` +${ips.length - 1}` : '');
  }

  function renderDevices() {
    gDevs.innerHTML = '';
    for (const [name, d] of Object.entries(S.project.devices)) {
      const sel = S.selection && S.selection.kind === 'device' && S.selection.id === name;
      const g = NL.s('g', { class: 'dev' + (sel ? ' sel' : '') + (C.linkSrc === name ? ' link-src' : ''),
        transform: `translate(${d.x},${d.y})`, 'data-dev': name, style: `--dc: var(--c-${d.type})` });
      g.appendChild(NL.s('circle', { r: R + 7, class: 'ring' }));
      g.appendChild(NL.s('circle', { r: R, fill: `color-mix(in srgb, var(--c-${d.type}) 14%, var(--bg-2))`,
        stroke: `color-mix(in srgb, var(--c-${d.type}) 55%, transparent)`, 'stroke-width': 1.5, class: 'body' }));
      const ic = NL.s('g', { transform: 'translate(-17,-17) scale(0.708)', fill: 'none', stroke: `var(--c-${d.type})`,
        'stroke-width': 2.4, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', style: `color: var(--c-${d.type})` });
      ic.innerHTML = NL.iconPaths[d.type] || '';
      g.appendChild(ic);
      if (S.running()) g.appendChild(NL.s('circle', { cx: R * 0.72, cy: -R * 0.72, r: 5, class: 'led', fill: 'var(--success)' }));
      const lbl = NL.s('text', { y: R + 17, class: 'lbl' }); lbl.textContent = name; g.appendChild(lbl);
      const ip = devIP(name);
      if (ip) { const t = NL.s('text', { y: R + 30, class: 'sub' }); t.textContent = ip; g.appendChild(t); }
      gDevs.appendChild(g);
    }
  }

  function renderLinks() {
    gLinks.innerHTML = '';
    C.geom = {};
    const pairCount = {}, pairIdx = {};
    for (const l of Object.values(S.project.links)) {
      const k = [l.a.dev, l.b.dev].sort().join('|'); pairCount[k] = (pairCount[k] || 0) + 1;
    }
    for (const [lid, l] of Object.entries(S.project.links)) {
      const A = S.dev(l.a.dev), B = S.dev(l.b.dev);
      if (!A || !B) continue;
      const k = [l.a.dev, l.b.dev].sort().join('|');
      const i = pairIdx[k] = (pairIdx[k] || 0) + 1;
      const n = pairCount[k];
      let dx = B.x - A.x, dy = B.y - A.y; const len = Math.hypot(dx, dy) || 1;
      const nx = -dy / len, ny = dx / len;
      const off = (i - (n + 1) / 2) * 14 * (l.a.dev < l.b.dev ? 1 : -1);
      const x1 = A.x + nx * off, y1 = A.y + ny * off, x2 = B.x + nx * off, y2 = B.y + ny * off;
      C.geom[lid] = { x1, y1, x2, y2 };
      const sel = S.selection && S.selection.kind === 'link' && S.selection.id === lid;
      const est = NL.wifi.linkEstimate(lid);
      const g = NL.s('g', { class: 'lnk' + (sel ? ' sel' : '') + (l.up === false || (est && !est.ok) ? ' down' : '') + (est ? ' wifi' : ''), 'data-link': lid });
      g.appendChild(NL.s('line', { x1, y1, x2, y2, class: 'wire', id: 'w-' + lid }));
      g.appendChild(NL.s('line', { x1, y1, x2, y2, class: 'hit' }));
      const ux = dx / len, uy = dy / len, d0 = R + 20;
      const pa = NL.s('text', { x: x1 + ux * d0 + nx * 9, y: y1 + uy * d0 + ny * 9 + 3, class: 'plbl' }); pa.textContent = 'eth' + l.a.port;
      const pb = NL.s('text', { x: x2 - ux * d0 + nx * 9, y: y2 - uy * d0 + ny * 9 + 3, class: 'plbl' }); pb.textContent = 'eth' + l.b.port;
      const ul = NL.s('text', { x: (x1 + x2) / 2 - nx * 10, y: (y1 + y2) / 2 - ny * 10 + 3, class: 'ulbl', id: 'u-' + lid });
      g.append(pa, pb, ul);
      if (est) {      // radio taxmini: MCS va foydali tezlik (masofaga bog'liq)
        const rl = NL.s('text', { x: (x1 + x2) / 2 + nx * 12, y: (y1 + y2) / 2 + ny * 12 + 3, class: 'rlbl' + (est.ok ? '' : ' bad') });
        rl.textContent = est.ok ? `MCS${est.mcs} · ~${Math.round(est.thr_mbps)} Mb/s · ${Math.round(est.distance_m)} m` : `signal yo'q · ${Math.round(est.distance_m)} m`;
        g.appendChild(rl);
      }
      gLinks.appendChild(g);
    }
  }

  function updateStats() {
    if (!S.project) return;
    for (const [lid, l] of Object.entries(S.project.links)) {
      const w = document.getElementById('w-' + lid), u = document.getElementById('u-' + lid);
      if (!w || !u) continue;
      const st = S.stats[lid];
      if (!S.running() || !st || l.up === false) { u.textContent = ''; w.style.stroke = ''; w.style.strokeWidth = ''; continue; }
      const util = st.util || 0;
      const peak = Math.max(st.ab_bps || 0, st.ba_bps || 0);
      u.textContent = peak > 0 ? NL.fmtBps(peak) : '';
      if (util > 0.001) {
        const hue = Math.round(150 - Math.min(util, 1) * 150);   // yashil -> qizil
        w.style.stroke = `hsl(${hue} 85% 58%)`;
        w.style.strokeWidth = String(2.2 + Math.min(util, 1) * 4);
      } else { w.style.stroke = ''; w.style.strokeWidth = ''; }
    }
  }

  // ---------- paket animatsiyasi ----------
  const active = [];
  C.packet = function (p) {
    if (!S.running() || document.hidden || active.length > 260) return;
    if (NL.dock && NL.dock.protoHidden && NL.dock.protoHidden(p.proto)) return;
    const g = C.geom[p.link];
    if (!g) return;
    const fwd = p.dir !== 'ba';
    const col = NL.PROTO_COLORS[p.proto] || NL.PROTO_COLORS.other;
    const el = NL.s('circle', { r: 4.6, fill: col, class: 'pkt', style: `filter: drop-shadow(0 0 5px ${col})` });
    gPkts.appendChild(el);
    active.push({ el, x1: fwd ? g.x1 : g.x2, y1: fwd ? g.y1 : g.y2, x2: fwd ? g.x2 : g.x1, y2: fwd ? g.y2 : g.y1, t0: performance.now(), dur: 520 });
  };
  function tick(now) {
    for (let i = active.length - 1; i >= 0; i--) {
      const a = active[i];
      const t = (now - a.t0) / a.dur;
      if (t >= 1) { a.el.remove(); active.splice(i, 1); continue; }
      const e = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
      a.el.setAttribute('cx', a.x1 + (a.x2 - a.x1) * e);
      a.el.setAttribute('cy', a.y1 + (a.y2 - a.y1) * e);
    }
    requestAnimationFrame(tick);
  }

  // ---------- interaktivlik ----------
  function bind() {
    let drag = null, pan = null, moved = false, lastClick = { name: null, t: 0 };
    svg.addEventListener('pointerdown', (ev) => {
      if (ev.button !== 0) return;
      const devEl = ev.target.closest('[data-dev]');
      const linkEl = ev.target.closest('[data-link]');
      const w = toWorld(ev);
      moved = false;
      if (C.mode === 'pick') {
        const cb = C.pickCb; C.pickCb = null; C.setMode('select');
        if (cb) cb(w.x, w.y);
        return;
      }
      if (C.mode === 'place' && !devEl) {
        S.addDevice(C.placeType, w.x, w.y);
        if (!ev.shiftKey) C.setMode('select');
        return;
      }
      if (devEl) {
        const name = devEl.getAttribute('data-dev');
        if (C.mode === 'delete') { S.removeDevice(name); return; }
        if (C.mode === 'link') {
          if (!C.linkSrc) { C.linkSrc = name; hintEl.textContent = `Kabel: ${name} → ikkinchi qurilmani bosing`; C.render(); }
          else if (C.linkSrc !== name) {
            const a = C.linkSrc; C.linkSrc = null; gGhost.innerHTML = '';
            hintEl.textContent = 'Kabel: birinchi qurilmani bosing (Esc — bekor)';
            S.addLink(a, name);
          }
          return;
        }
        // ikki marta bosish: render elementni almashtirgani uchun brauzer dblclick'iga tayanmaymiz
        const now = performance.now();
        if (lastClick.name === name && now - lastClick.t < 380) { lastClick = { name: null, t: 0 }; NL.devwin.open(name); return; }
        lastClick = { name, t: now };
        S.selection = { kind: 'device', id: name };
        const d = S.dev(name);
        drag = { name, dx: w.x - d.x, dy: w.y - d.y };
        svg.setPointerCapture(ev.pointerId);
        NL.emit('change');
        return;
      }
      if (linkEl) {
        const lid = linkEl.getAttribute('data-link');
        if (C.mode === 'delete') { S.removeLink(lid); return; }
        S.selection = { kind: 'link', id: lid };
        NL.emit('change');
        return;
      }
      pan = { x: ev.clientX, y: ev.clientY, tx: C.tx, ty: C.ty };
      svg.setPointerCapture(ev.pointerId);
    });
    svg.addEventListener('pointermove', (ev) => {
      if (drag) {
        const w = toWorld(ev), d = S.dev(drag.name);
        if (!d) { drag = null; return; }
        let nx = w.x - drag.dx, ny = w.y - drag.dy;
        if (!ev.altKey) { nx = Math.round(nx / 8) * 8; ny = Math.round(ny / 8) * 8; }
        if (nx !== d.x || ny !== d.y) { d.x = nx; d.y = ny; moved = true; renderCoverage(); renderLinks(); renderDevices(); updateStats(); }
      } else if (pan) {
        C.tx = pan.tx + ev.clientX - pan.x; C.ty = pan.ty + ev.clientY - pan.y; moved = true; applyView();
      } else if (C.mode === 'link' && C.linkSrc) {
        const a = S.dev(C.linkSrc), w = toWorld(ev);
        gGhost.innerHTML = '';
        gGhost.appendChild(NL.s('line', { x1: a.x, y1: a.y, x2: w.x, y2: w.y, class: 'ghost-wire' }));
      }
    });
    const end = () => {
      if (drag && moved) S.touch();
      if (pan && !moved && S.selection) { S.selection = null; NL.emit('change'); }
      drag = null; pan = null;
    };
    svg.addEventListener('pointerup', end);
    svg.addEventListener('pointercancel', end);
    svg.addEventListener('wheel', (ev) => {
      ev.preventDefault();
      const r = svg.getBoundingClientRect();
      zoomAt(ev.deltaY < 0 ? 1.1 : 1 / 1.1, ev.clientX - r.left, ev.clientY - r.top);
    }, { passive: false });
    // palitradan sudrab tashlash
    svg.addEventListener('dragover', (ev) => ev.preventDefault());
    svg.addEventListener('drop', (ev) => {
      ev.preventDefault();
      const type = ev.dataTransfer.getData('text/netlab-type');
      if (type) { const w = toWorld(ev); S.addDevice(type, w.x, w.y); C.setMode('select'); }
    });
  }
})(window.NL);
