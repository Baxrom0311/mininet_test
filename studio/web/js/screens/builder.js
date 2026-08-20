/* ============================================================================
   builder — vizual topologiya muharriri (SVG, Cisco Packet-Tracer uslubi)
   Sichqoncha bilan switch/host qo'shish, link chizish, ko'chirish, o'chirish;
   tekshirish (validate), saqlash, ochish. Faqat ctx.api kontraktidan foydalanadi.
   ========================================================================== */
Studio.screen('builder', {
  title: 'Topologiya quruvchi',
  subtitle: 'Tarmoqni sichqoncha bilan yigʻing — switch, host, link',
  order: 20,
  icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="6" cy="6" r="2.4"/><circle cx="18" cy="6" r="2.4"/><circle cx="12" cy="18" r="2.4"/><path d="M7.7 7.4 10.6 16M16.3 7.4 13.4 16M8 6h8"/></svg>',

  render(root, ctx) {
    const SVGNS = 'http://www.w3.org/2000/svg';
    const VW = 1200;                 // logik kenglik (viewBox)
    let VH = 760;                    // logik balandlik (sahna nisbatiga qarab)

    // ---- ichki model ----
    const model = { switches: {}, hosts: {}, links: {} };
    let mode = 'select';
    let selected = null;             // {type:'switch'|'host', id}
    let linkSrc = null;              // add-link birinchi tugun
    let asOrder = [];                // AS -> rang indeks tartibi

    // ---- DOM yasash yordamchilari ----
    const h = (tag, attrs, kids) => {
      const e = document.createElement(tag);
      if (attrs) for (const k in attrs) {
        if (k === 'class') e.className = attrs[k];
        else if (k === 'html') e.innerHTML = attrs[k];
        else if (k === 'text') e.textContent = attrs[k];
        else if (k.startsWith('on') && typeof attrs[k] === 'function') e.addEventListener(k.slice(2), attrs[k]);
        else if (attrs[k] != null) e.setAttribute(k, attrs[k]);
      }
      (kids || []).forEach(c => c != null && e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c));
      return e;
    };
    const s = (tag, attrs) => {
      const e = document.createElementNS(SVGNS, tag);
      if (attrs) for (const k in attrs) if (attrs[k] != null) e.setAttribute(k, attrs[k]);
      return e;
    };
    const ico = (path) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${path}</svg>`;

    const NAME_RE = /^[A-Za-z][A-Za-z0-9_]*$/;

    // ---- layout skeleti ----
    const wrap = h('div', { class: 'builder-wrap' });

    // asboblar paneli
    const modeDefs = [
      ['add-switch', 'Switch', ico('<rect x="4" y="8" width="16" height="8" rx="2"/><path d="M8 12h.01M12 12h.01M16 12h.01"/>')],
      ['add-host', 'Host', ico('<rect x="4" y="5" width="16" height="11" rx="2"/><path d="M8 20h8M12 16v4"/>')],
      ['add-link', 'Link', ico('<circle cx="6" cy="18" r="2"/><circle cx="18" cy="6" r="2"/><path d="M8 16 16 8"/>')],
      ['select', 'Koʻchirish', ico('<path d="M4 4l6 16 2-6 6-2z"/>')],
      ['delete', 'Oʻchirish', ico('<path d="M4 7h16M9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12"/>')],
    ];
    const modeSeg = h('div', { class: 'segment builder-modes' });
    const modeBtns = {};
    modeDefs.forEach(([id, label, svg]) => {
      const b = h('button', { html: svg + '<span>' + label + '</span>', onclick: () => setMode(id) });
      modeBtns[id] = b; modeSeg.appendChild(b);
    });

    const actions = h('div', { class: 'builder-actions' }, [
      h('button', { class: 'btn neon sm', html: ico('<path d="M20 6 9 17l-5-5"/>') + 'Tekshirish', onclick: doValidate }),
      h('button', { class: 'btn primary sm', html: ico('<path d="M5 3h11l3 3v15H5zM8 3v6h7"/>') + 'Saqlash', onclick: openSave }),
      h('button', { class: 'btn ghost sm', html: ico('<path d="M3 7h6l2 2h10v10H3z"/>') + 'Ochish', onclick: openPicker }),
      h('button', { class: 'btn ghost sm', html: ico('<path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14"/>') + 'Tozalash', onclick: confirmClear }),
    ]);

    const bar = h('div', { class: 'card glass builder-bar' }, [
      h('span', { class: 'seg-label', text: 'Rejim' }), modeSeg, actions,
    ]);

    // jonli hisob chiplar (alohida qatorda, sahna ustida)
    const chipSw = chip('dot-sw', 'switch', 0);
    const chipHo = chip('dot-ho', 'host', 0);
    const chipLn = chip('dot-ln', 'link', 0);
    const chipVal = h('span', { class: 'b-chip', html: '<span>tekshirilmagan</span>' });
    const chips = h('div', { class: 'builder-chips' }, [chipSw, chipHo, chipLn, chipVal]);
    bar.insertBefore(chips, actions);
    bar.insertBefore(h('span', { class: 'builder-sep' }), actions);

    function chip(dotCls, label, n) {
      return h('span', { class: 'b-chip', html: `<i class="${dotCls}"></i><b>${n}</b> ${label}` });
    }

    // sahna + svg
    const stage = h('div', { class: 'builder-stage' });
    const svg = s('svg', { class: 'builder-svg', 'data-mode': 'select', preserveAspectRatio: 'xMidYMid slice' });
    const gGrid = s('g', { class: 'b-grid' });
    const gLinks = s('g', { class: 'b-links' });
    const gRubber = s('g', {});
    const gNodes = s('g', { class: 'b-nodes' });
    const bg = s('rect', { class: 'b-bg', x: 0, y: 0, width: VW, height: 4000, fill: 'transparent' });
    svg.appendChild(bg); svg.appendChild(gGrid); svg.appendChild(gLinks);
    svg.appendChild(gRubber); svg.appendChild(gNodes);

    const emptyOverlay = h('div', { class: 'builder-empty' }, [
      h('div', { class: 'be-ico', html: ico('<circle cx="6" cy="6" r="2.4"/><circle cx="18" cy="6" r="2.4"/><circle cx="12" cy="18" r="2.4"/><path d="M7.7 7.4 10.6 16M16.3 7.4 13.4 16M8 6h8"/>') }),
      h('h3', { text: 'Boʻsh sahna' }),
      h('p', { text: 'Yuqoridan rejim tanlang. “Switch” yoki “Host” rejimida sahnaga bosib qurilma qoʻshing, yoki “Ochish” bilan tayyor topologiyani yuklang.' }),
    ]);
    const hint = h('div', { class: 'builder-hint' });
    stage.appendChild(svg); stage.appendChild(emptyOverlay); stage.appendChild(hint);

    wrap.appendChild(bar); wrap.appendChild(stage);
    root.appendChild(wrap);

    // ---- viewBox o'lchamini sahnaga moslash ----
    function fitViewBox() {
      const r = stage.getBoundingClientRect();
      if (r.width < 10) return;
      VH = Math.round(VW * (r.height / r.width));
      svg.setAttribute('viewBox', `0 0 ${VW} ${VH}`);
      bg.setAttribute('height', VH);
      drawGrid();
    }
    function drawGrid() {
      gGrid.innerHTML = '';
      const step = 60;
      for (let x = step; x < VW; x += step) gGrid.appendChild(s('line', { x1: x, y1: 0, x2: x, y2: VH }));
      for (let y = step; y < VH; y += step) gGrid.appendChild(s('line', { x1: 0, y1: y, x2: VW, y2: y }));
    }
    const ro = new ResizeObserver(fitViewBox);
    ro.observe(stage);

    // ---- koordinata: ekran -> svg logik ----
    function toLocal(evt) {
      const pt = svg.createSVGPoint();
      pt.x = evt.clientX; pt.y = evt.clientY;
      const m = svg.getScreenCTM();
      if (!m) return { x: VW / 2, y: VH / 2 };
      const p = pt.matrixTransform(m.inverse());
      return { x: p.x, y: p.y };
    }

    // ---- rang / rejim ----
    function asIndex(as) {
      const key = String(as);
      let i = asOrder.indexOf(key);
      if (i < 0) { asOrder.push(key); i = asOrder.length - 1; }
      return i % 5;
    }
    function setMode(m) {
      mode = m; svg.setAttribute('data-mode', m);
      Object.entries(modeBtns).forEach(([id, b]) => b.classList.toggle('active', id === m));
      linkSrc = null;
      if (m !== 'select') { selected = null; }
      render();
      updateHint();
    }
    const HINTS = {
      'add-switch': ['Sahnaga bosing', 'yangi <b>switch</b> qoʻshiladi.'],
      'add-host': ['Sahnaga bosing', 'yangi <b>host</b> qoʻshiladi (avval switch kerak).'],
      'add-link': ['Ikki switchni', 'ketma-ket bosing — orasida <b>link</b> chiziladi.'],
      'select': ['Tugunni torting', 'joyini oʻzgartirish uchun; bosib tanlang.'],
      'delete': ['Tugun yoki linkni bosing', '— oʻchiriladi.'],
    };
    function updateHint() {
      const [a, b] = HINTS[mode] || ['', ''];
      let extra = '';
      if (mode === 'add-link' && linkSrc) extra = ` — tanlangan: <kbd>${linkSrc}</kbd>, ikkinchisini bosing.`;
      hint.innerHTML = `<span>${a}</span> ${b}${extra}`;
    }

    // ============================ RENDER =====================================
    function render() {
      // linklar
      gLinks.innerHTML = '';
      gRubber.innerHTML = '';
      // access (host -> switch) dashed
      for (const [hid, ho] of Object.entries(model.hosts)) {
        const sw = model.switches[ho.switch];
        if (!sw) continue;
        const g = s('g', { class: 'b-link access', 'data-host': hid, 'data-sw': ho.switch });
        g.appendChild(s('line', { class: 'b-link-line', x1: ho.x, y1: ho.y, x2: sw.x, y2: sw.y }));
        g.appendChild(s('line', { class: 'b-link-hit', x1: ho.x, y1: ho.y, x2: sw.x, y2: sw.y }));
        gLinks.appendChild(g);
      }
      // backbone
      for (const key of Object.keys(model.links)) {
        const [a, b] = key.split('-');
        const A = model.switches[a], B = model.switches[b];
        if (!A || !B) continue;
        const bw = model.links[key].bw;
        gLinks.appendChild(linkEl(key, A.x, A.y, B.x, B.y, bw ? bw + 'M' : ''));
      }

      // tugunlar
      gNodes.innerHTML = '';
      for (const [id, sw] of Object.entries(model.switches)) gNodes.appendChild(switchEl(id, sw));
      for (const [id, ho] of Object.entries(model.hosts)) gNodes.appendChild(hostEl(id, ho));

      // chiplar + overlay
      const nS = Object.keys(model.switches).length, nH = Object.keys(model.hosts).length, nL = Object.keys(model.links).length;
      chipSw.querySelector('b').textContent = nS;
      chipHo.querySelector('b').textContent = nH;
      chipLn.querySelector('b').textContent = nL;
      emptyOverlay.style.display = (nS + nH) ? 'none' : 'grid';
    }

    function linkEl(key, x1, y1, x2, y2, capText) {
      const g = s('g', { class: 'b-link', 'data-link': key });
      g.appendChild(s('line', { class: 'b-link-line', x1, y1, x2, y2 }));
      g.appendChild(s('line', { class: 'b-link-hit', x1, y1, x2, y2 }));
      if (capText) {
        const t = s('text', { class: 'b-link-cap', x: (x1 + x2) / 2, y: (y1 + y2) / 2 - 6, 'text-anchor': 'middle' });
        t.textContent = capText; g.appendChild(t);
      }
      g.addEventListener('click', (e) => { if (mode === 'delete') { e.stopPropagation(); removeLink(key); } });
      return g;
    }

    function switchEl(id, sw) {
      const g = s('g', { class: `b-node b-switch as${asIndex(sw.as)}${selected && selected.id === id ? ' selected' : ''}${linkSrc === id ? ' link-src' : ''}`, transform: `translate(${sw.x},${sw.y})`, 'data-id': id, 'data-type': 'switch' });
      g.appendChild(s('rect', { class: 'b-shape', x: -48, y: -23, width: 96, height: 46, rx: 12 }));
      const nm = s('text', { class: 'b-name', x: 0, y: -1 }); nm.textContent = id; g.appendChild(nm);
      const bd = s('text', { class: 'b-badge', x: 0, y: 14 }); bd.textContent = 'AS' + sw.as; g.appendChild(bd);
      const sub = s('text', { class: 'b-sub', x: 0, y: 40 }); sub.textContent = sw.role || ''; g.appendChild(sub);
      wireNode(g, id, 'switch');
      return g;
    }
    function hostEl(id, ho) {
      const g = s('g', { class: `b-node b-host role-${ho.role || 'client'}${selected && selected.id === id ? ' selected' : ''}`, transform: `translate(${ho.x},${ho.y})`, 'data-id': id, 'data-type': 'host' });
      g.appendChild(s('circle', { class: 'b-shape', cx: 0, cy: 0, r: 17 }));
      const nm = s('text', { class: 'b-name host', x: 0, y: 0 }); nm.textContent = id; g.appendChild(nm);
      const sub = s('text', { class: 'b-sub', x: 0, y: 33 }); sub.textContent = ho.role || ''; g.appendChild(sub);
      wireNode(g, id, 'host');
      return g;
    }

    // ---- tugun bilan ishlash (tanlash / torting / link / o'chirish) ----
    let drag = null;
    function wireNode(g, id, type) {
      g.addEventListener('mousedown', (e) => {
        if (mode !== 'select') return;
        e.preventDefault(); e.stopPropagation();
        const p = toLocal(e); const n = model.switches[id] || model.hosts[id];
        drag = { id, type, dx: n.x - p.x, dy: n.y - p.y, moved: false };
        g.classList.add('dragging');
      });
      g.addEventListener('click', (e) => {
        e.stopPropagation();
        if (mode === 'delete') { removeNode(id, type); return; }
        if (mode === 'add-link') { pickLinkEnd(id, type); return; }
        if (mode === 'select') {
          if (drag && drag.moved) return;
          selected = { id, type }; render();
        }
      });
    }
    svg.addEventListener('mousemove', (e) => {
      if (drag) {
        const p = toLocal(e);
        const n = model.switches[drag.id] || model.hosts[drag.id];
        n.x = clamp(p.x + drag.dx, 30, VW - 30);
        n.y = clamp(p.y + drag.dy, 26, VH - 26);
        drag.moved = true;
        const g = gNodes.querySelector(`[data-id="${cssEsc(drag.id)}"]`);
        if (g) g.setAttribute('transform', `translate(${n.x},${n.y})`);
        updateLinksFor(drag.id);
        return;
      }
      // add-link rejimida: manbadan kursorgacha "rezina" chiziq
      if (mode === 'add-link' && linkSrc && model.switches[linkSrc]) {
        const src = model.switches[linkSrc], p = toLocal(e);
        let ln = gRubber.firstChild;
        if (!ln) { ln = s('line', { class: 'b-rubber' }); gRubber.appendChild(ln); }
        ln.setAttribute('x1', src.x); ln.setAttribute('y1', src.y);
        ln.setAttribute('x2', p.x); ln.setAttribute('y2', p.y);
      } else if (gRubber.firstChild) { gRubber.innerHTML = ''; }
    });
    window.addEventListener('mouseup', () => {
      if (!drag) return;
      const g = gNodes.querySelector(`[data-id="${cssEsc(drag.id)}"]`);
      if (g) g.classList.remove('dragging');
      drag = null;
    });
    // torttirilgan tugunga tegishli linklarni joyida yangilash (to'liq render'siz)
    function updateLinksFor(id) {
      const setLine = (g, x1, y1, x2, y2) => g.querySelectorAll('line').forEach(ln => {
        ln.setAttribute('x1', x1); ln.setAttribute('y1', y1); ln.setAttribute('x2', x2); ln.setAttribute('y2', y2);
      });
      for (const key of Object.keys(model.links)) {
        const [a, b] = key.split('-');
        if (a !== id && b !== id) continue;
        const g = gLinks.querySelector(`[data-link="${cssEsc(key)}"]`);
        const A = model.switches[a], B = model.switches[b];
        if (!g || !A || !B) continue;
        setLine(g, A.x, A.y, B.x, B.y);
        const t = g.querySelector('.b-link-cap');
        if (t) { t.setAttribute('x', (A.x + B.x) / 2); t.setAttribute('y', (A.y + B.y) / 2 - 6); }
      }
      if (model.hosts[id]) {
        const ho = model.hosts[id], sw = model.switches[ho.switch];
        const g = gLinks.querySelector(`[data-host="${cssEsc(id)}"]`);
        if (g && sw) setLine(g, ho.x, ho.y, sw.x, sw.y);
      } else {
        gLinks.querySelectorAll(`[data-sw="${cssEsc(id)}"]`).forEach(g => {
          const ho = model.hosts[g.getAttribute('data-host')], sw = model.switches[id];
          if (ho && sw) setLine(g, ho.x, ho.y, sw.x, sw.y);
        });
      }
    }
    const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
    const cssEsc = (v) => (window.CSS && CSS.escape) ? CSS.escape(v) : v;

    // bo'sh sahnaga bosish -> qo'shish yoki tanlovni bekor qilish
    svg.addEventListener('click', (e) => {
      if (drag && drag.moved) return;
      if (mode === 'add-switch') { const p = toLocal(e); openSwitchForm(p); }
      else if (mode === 'add-host') { const p = toLocal(e); openHostForm(p); }
      else if (mode === 'select') { selected = null; render(); }
      else if (mode === 'add-link') { linkSrc = null; render(); updateHint(); }
    });

    // ---- link uchi tanlash ----
    function pickLinkEnd(id, type) {
      if (type !== 'switch') { ctx.toast('Link faqat switchlar orasida chiziladi.', 'warn'); return; }
      if (!linkSrc) { linkSrc = id; render(); updateHint(); return; }
      if (linkSrc === id) { linkSrc = null; render(); updateHint(); return; }
      const a = linkSrc, b = id;
      if (model.links[`${a}-${b}`] || model.links[`${b}-${a}`]) {
        ctx.toast('Bu ikki switch allaqachon bogʻlangan.', 'warn'); linkSrc = null; render(); updateHint(); return;
      }
      openLinkForm(a, b);
    }

    // ============================ FORMALAR (modal) ===========================
    function modal({ title, sub, icon, danger, body, actions }) {
      const back = h('div', { class: 'b-modal-back' });
      const card = h('div', { class: 'card glass b-modal' });
      const foot = h('div', { class: 'bm-foot' });
      const close = () => { back.remove(); document.removeEventListener('keydown', onKey); };
      const onKey = (e) => { if (e.key === 'Escape') close(); };
      document.addEventListener('keydown', onKey);
      back.addEventListener('mousedown', (e) => { if (e.target === back) close(); });
      card.appendChild(h('div', { class: 'bm-head' }, [
        icon ? h('div', { class: 'bm-ico' + (danger ? ' danger' : ''), html: icon }) : null,
        h('div', {}, [h('div', { class: 'bm-title', text: title }), sub ? h('div', { class: 'bm-sub', text: sub }) : null]),
      ]));
      if (body) card.appendChild(body);
      (actions || []).forEach(a => {
        const b = h('button', { class: 'btn ' + (a.cls || 'ghost'), text: a.label, onclick: () => a.onClick(close) });
        foot.appendChild(b);
      });
      card.appendChild(foot);
      back.appendChild(card); document.body.appendChild(back);
      const first = card.querySelector('input,select');
      if (first) setTimeout(() => first.focus(), 40);
      return { close, card };
    }
    function field(label, control, full) {
      return h('div', { class: 'field' + (full ? ' full' : '') }, [h('label', { text: label }), control]);
    }
    function input(attrs) { return h('input', Object.assign({ class: 'input' }, attrs)); }
    function select(opts, val) {
      const sel = h('select', { class: 'select' });
      opts.forEach(o => {
        const [v, t] = Array.isArray(o) ? o : [o, o];
        const op = h('option', { value: v, text: t }); if (v === val) op.selected = true; sel.appendChild(op);
      });
      return sel;
    }
    function nextName(prefix, pool) {
      let i = 1; while (pool[prefix + i]) i++; return prefix + i;
    }
    function validName(name) {
      if (!NAME_RE.test(name)) { ctx.toast('Nom faqat harf bilan boshlanib, harf/raqam/_ dan iborat boʻlsin (“-” va boʻsh joy mumkin emas).', 'danger'); return false; }
      if (model.switches[name] || model.hosts[name]) { ctx.toast(`“${name}” nomi band.`, 'danger'); return false; }
      return true;
    }
    const fmtDelay = (v) => { v = String(v || '').trim(); return /^\d+(\.\d+)?$/.test(v) ? v + 'ms' : (v || '0ms'); };

    function openSwitchForm(pos) {
      const nName = input({ value: nextName('s', model.switches), placeholder: 's1' });
      const nAs = input({ type: 'number', value: 100, min: 1 });
      const nRole = select(['core', 'border', 'access', 'aggregation', 'servers', 'edge'], 'core');
      const nArea = input({ type: 'number', placeholder: 'ixtiyoriy', min: 0 });
      const nIsis = select([['', '—'], 'L1', 'L2', 'L1L2'], '');
      const body = h('div', { class: 'b-form' }, [
        field('Nom', nName, true),
        field('AS raqami', nAs), field('Rol', nRole),
        field('Area (ixtiyoriy)', nArea), field('IS-IS daraja', nIsis),
      ]);
      const m = modal({
        title: 'Switch qoʻshish', sub: 'Backbone kommutatori',
        icon: ico('<rect x="4" y="8" width="16" height="8" rx="2"/><path d="M8 12h.01M12 12h.01M16 12h.01"/>'),
        body,
        actions: [
          { label: 'Bekor', cls: 'ghost', onClick: (c) => c() },
          {
            label: 'Qoʻshish', cls: 'primary', onClick: (close) => {
              const name = nName.value.trim();
              if (!validName(name)) return;
              const as = parseInt(nAs.value, 10);
              if (!Number.isFinite(as)) { ctx.toast('AS raqami kerak.', 'danger'); return; }
              const sw = { as, role: nRole.value, x: pos.x, y: pos.y };
              if (nArea.value !== '') sw.area = parseInt(nArea.value, 10);
              if (nIsis.value) sw.isis_level = nIsis.value;
              model.switches[name] = sw; selected = { type: 'switch', id: name };
              render(); close(); ctx.toast(`Switch “${name}” qoʻshildi.`, 'success');
            }
          },
        ],
      });
      return m;
    }

    function openHostForm(pos) {
      const swIds = Object.keys(model.switches);
      if (!swIds.length) { ctx.toast('Avval kamida bitta switch qoʻshing.', 'warn'); setMode('add-switch'); return; }
      const nName = input({ value: nextName('h', model.hosts), placeholder: 'h1' });
      const nSwitch = select(swIds, swIds[0]);
      const nIp = input({ value: '10.0.0.1/8', placeholder: '10.0.0.1/8' });
      const nRole = select(['server', 'client', 'gateway'], 'client');
      const nBw = input({ type: 'number', value: 20, min: 0 });
      const nDelay = input({ value: '3ms', placeholder: '3ms' });
      const nLoss = input({ type: 'number', value: 0, min: 0, step: '0.01' });
      const body = h('div', { class: 'b-form' }, [
        field('Nom', nName), field('Ulanadigan switch', nSwitch),
        field('IP manzil', nIp, true),
        field('Rol', nRole), field('Kirish tezligi (Mbps)', nBw),
        field('Kechikish', nDelay), field('Yoʻqotish (%)', nLoss),
      ]);
      modal({
        title: 'Host qoʻshish', sub: 'Server / mijoz / gateway',
        icon: ico('<rect x="4" y="5" width="16" height="11" rx="2"/><path d="M8 20h8M12 16v4"/>'),
        body,
        actions: [
          { label: 'Bekor', cls: 'ghost', onClick: (c) => c() },
          {
            label: 'Qoʻshish', cls: 'primary', onClick: (close) => {
              const name = nName.value.trim();
              if (!validName(name)) return;
              model.hosts[name] = {
                switch: nSwitch.value, ip: nIp.value.trim(), role: nRole.value,
                x: pos.x, y: pos.y,
                access: { bw: numOr(nBw.value, 20), delay: fmtDelay(nDelay.value), loss: numOr(nLoss.value, 0) },
              };
              selected = { type: 'host', id: name };
              render(); close(); ctx.toast(`Host “${name}” qoʻshildi.`, 'success');
            }
          },
        ],
      });
    }

    function openLinkForm(a, b) {
      const nBw = input({ type: 'number', value: 10, min: 0 });
      const nDelay = input({ value: '5ms' });
      const nLoss = input({ type: 'number', value: 0, min: 0, step: '0.01' });
      const nJit = input({ value: '1ms' });
      const nQ = input({ type: 'number', value: 50, min: 0 });
      const body = h('div', {}, [
        h('div', { class: 'b-endpoints' }, [
          h('span', { class: 'ep', text: a }), h('span', { class: 'arrow', text: '↔' }), h('span', { class: 'ep', text: b }),
        ]),
        h('div', { class: 'b-form', style: 'margin-top:16px' }, [
          field('Tezlik (Mbps)', nBw), field('Kechikish', nDelay),
          field('Yoʻqotish (%)', nLoss), field('Jitter', nJit),
          field('Navbat (paket)', nQ, true),
        ]),
      ]);
      modal({
        title: 'Link chizish', sub: 'Backbone bogʻlanish parametrlari',
        icon: ico('<circle cx="6" cy="18" r="2"/><circle cx="18" cy="6" r="2"/><path d="M8 16 16 8"/>'),
        body,
        actions: [
          { label: 'Bekor', cls: 'ghost', onClick: (c) => { linkSrc = null; render(); updateHint(); c(); } },
          {
            label: 'Chizish', cls: 'primary', onClick: (close) => {
              model.links[`${a}-${b}`] = {
                bw: numOr(nBw.value, 10), delay: fmtDelay(nDelay.value),
                loss: numOr(nLoss.value, 0), jitter: fmtDelay(nJit.value), queue: numOr(nQ.value, 50),
              };
              linkSrc = null; render(); updateHint(); close();
              ctx.toast(`Link ${a} ↔ ${b} chizildi.`, 'success');
            }
          },
        ],
      });
    }
    const numOr = (v, d) => { const n = parseFloat(v); return Number.isFinite(n) ? n : d; };

    // ---- o'chirish ----
    function removeLink(key) {
      delete model.links[key]; render(); ctx.toast('Link oʻchirildi.', 'info');
    }
    function removeNode(id, type) {
      if (type === 'host') {
        delete model.hosts[id];
        if (selected && selected.id === id) selected = null;
        render(); ctx.toast(`Host “${id}” oʻchirildi.`, 'info'); return;
      }
      // switch: bog'liq host va linklarni sanab, ogohlantirish
      const hosts = Object.keys(model.hosts).filter(hh => model.hosts[hh].switch === id);
      const links = Object.keys(model.links).filter(k => { const [a, b] = k.split('-'); return a === id || b === id; });
      const doDelete = () => {
        hosts.forEach(hh => delete model.hosts[hh]);
        links.forEach(k => delete model.links[k]);
        delete model.switches[id];
        if (selected && selected.id === id) selected = null;
        render();
        ctx.toast(`Switch “${id}” va unga bogʻliq ${hosts.length} host, ${links.length} link oʻchirildi.`, 'info');
      };
      if (!hosts.length && !links.length) { doDelete(); return; }
      modal({
        title: `“${id}” switchni oʻchirish?`, danger: true,
        sub: `${hosts.length} host va ${links.length} link ham oʻchadi.`,
        icon: ico('<path d="M12 9v4M12 17h.01M10.3 3.9 2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>'),
        actions: [
          { label: 'Bekor', cls: 'ghost', onClick: (c) => c() },
          { label: 'Oʻchirish', cls: 'danger', onClick: (c) => { doDelete(); c(); } },
        ],
      });
    }

    // ============================ TEKSHIRISH =================================
    async function doValidate() {
      if (!Object.keys(model.hosts).length) { ctx.toast('Kamida ikkita host kerak (tekshirish uchun).', 'warn'); return; }
      chipVal.className = 'b-chip'; chipVal.innerHTML = '<span>tekshirilmoqda…</span>';
      let res;
      try { res = await ctx.api.validate_topology(buildTopo()); }
      catch (e) { res = { ok: false, error: 'Backend xatosi: ' + e.message }; }
      if (res && res.ok) {
        const hops = avgHops();
        chipVal.className = 'b-chip val-ok';
        chipVal.innerHTML = `<span>✓ ulangan · <b>${res.pairs}</b> juftlik${hops ? ` · ≈${hops} hop` : ''}</span>`;
        ctx.toast(`Topologiya toʻgʻri — ${res.pairs} host juftligi ulangan.`, 'success');
      } else {
        chipVal.className = 'b-chip val-bad';
        chipVal.innerHTML = '<span>✗ xato</span>';
        ctx.toast((res && res.error) ? res.error : 'Tekshirish muvaffaqiyatsiz.', 'danger');
      }
    }
    // mahalliy taxminiy o'rtacha hop (switch grafi bo'yicha BFS)
    function avgHops() {
      const adj = {};
      Object.keys(model.switches).forEach(k => adj[k] = []);
      Object.keys(model.links).forEach(k => { const [a, b] = k.split('-'); if (adj[a] && adj[b]) { adj[a].push(b); adj[b].push(a); } });
      const hs = Object.entries(model.hosts).filter(([, ho]) => model.switches[ho.switch]);
      if (hs.length < 2) return 0;
      let sum = 0, cnt = 0;
      for (let i = 0; i < hs.length; i++) for (let j = 0; j < hs.length; j++) {
        if (i === j) continue;
        const d = bfs(adj, hs[i][1].switch, hs[j][1].switch);
        if (d >= 0) { sum += d + 2; cnt++; }   // +2: host->switch ikki uch
      }
      return cnt ? Math.round(sum / cnt * 10) / 10 : 0;
    }
    function bfs(adj, src, dst) {
      if (src === dst) return 0;
      const seen = { [src]: 0 }, q = [src];
      while (q.length) {
        const u = q.shift();
        for (const v of (adj[u] || [])) if (!(v in seen)) { seen[v] = seen[u] + 1; if (v === dst) return seen[v]; q.push(v); }
      }
      return -1;
    }

    // ============================ SAQLASH ====================================
    function buildTopo() {
      const switches = {}, hosts = {}, links = {}, access_links = {}, positions = {};
      for (const [id, sw] of Object.entries(model.switches)) {
        const o = { as: sw.as, role: sw.role };
        if (sw.area !== undefined) o.area = sw.area;
        if (sw.isis_level) o.isis_level = sw.isis_level;
        switches[id] = o; positions[id] = [Math.round(sw.x), Math.round(sw.y)];
      }
      for (const [id, ho] of Object.entries(model.hosts)) {
        hosts[id] = { switch: ho.switch, ip: ho.ip, role: ho.role };
        access_links[id] = ho.access || { bw: 20, delay: '3ms', loss: 0 };
        positions[id] = [Math.round(ho.x), Math.round(ho.y)];
      }
      for (const [k, v] of Object.entries(model.links)) links[k] = v;
      return { switches, hosts, links, access_links, positions };
    }

    function openSave() {
      if (!Object.keys(model.switches).length) { ctx.toast('Saqlash uchun avval topologiya quring.', 'warn'); return; }
      const nName = input({ placeholder: 'mening_tarmogim', value: '' });
      const body = h('div', {}, [
        field('Topologiya nomi', nName, true),
        h('p', { class: 'hint', style: 'margin-top:8px', text: 'Faqat harf/raqam/_ ; tayyor nomlar (three_as, five_as, datacenter, campus) band. Saqlashdan oldin avtomatik tekshiriladi.' }),
      ]);
      modal({
        title: 'Topologiyani saqlash', sub: 'Custom topologiya sifatida',
        icon: ico('<path d="M5 3h11l3 3v15H5zM8 3v6h7"/>'),
        body,
        actions: [
          { label: 'Bekor', cls: 'ghost', onClick: (c) => c() },
          {
            label: 'Saqlash', cls: 'primary', onClick: async (close) => {
              const name = nName.value.trim();
              if (!NAME_RE.test(name)) { ctx.toast('Nom faqat harf bilan boshlanib, harf/raqam/_ dan iborat boʻlsin.', 'danger'); return; }
              let res;
              try { res = await ctx.api.save_topology(name, buildTopo()); }
              catch (e) { res = { ok: false, error: e.message }; }
              if (res && res.ok) {
                close();
                ctx.toast(`“${name}” saqlandi.`, 'success');
                showSavedCmd(name, res.path);
              } else {
                ctx.toast((res && res.error) ? res.error : 'Saqlab boʻlmadi.', 'danger');
              }
            }
          },
        ],
      });
    }

    function showSavedCmd(name, path) {
      const cmd = `sudo python3 light_simulation.py --topology ${name} --routing hybrid --duration 300`;
      const code = h('code', { text: cmd });
      const copyBtn = h('button', {
        class: 'btn sm ghost', html: ico('<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h8"/>'),
        title: 'Nusxa olish',
        onclick: () => {
          const ta = h('textarea', { style: 'position:fixed;left:-9999px' }); ta.value = cmd;
          document.body.appendChild(ta); ta.select();
          try { document.execCommand('copy'); ctx.toast('Buyruq nusxa olindi.', 'success'); }
          catch (e) { ctx.toast('Nusxa olib boʻlmadi — qoʻlda tanlang.', 'warn'); }
          ta.remove();
        },
      });
      const body = h('div', {}, [
        path ? h('p', { class: 'hint', text: 'Saqlandi: ' + path }) : null,
        h('p', { class: 'hint', style: 'margin-top:4px', text: 'Serverda ishga tushirish uchun:' }),
        h('div', { class: 'b-cmd' }, [code, copyBtn]),
      ]);
      modal({
        title: 'Saqlandi ✓', sub: name,
        icon: ico('<path d="M20 6 9 17l-5-5"/>'),
        body,
        actions: [{ label: 'Yopish', cls: 'primary', onClick: (c) => c() }],
      });
    }

    // ============================ OCHISH =====================================
    async function openPicker() {
      let list = { builtin: [], custom: [] };
      try { list = await ctx.api.list_topologies() || list; } catch (e) { }
      const mkList = (arr, custom) => {
        if (!arr.length) return h('p', { class: 'hint', text: 'yoʻq' });
        const grid = h('div', { class: 'b-pick-list' });
        arr.forEach(name => grid.appendChild(h('button', {
          class: 'b-pick' + (custom ? ' custom' : ''),
          onclick: () => { loadTopology(name); box.close(); },
          html: `<span class="pk-ico">${ico('<circle cx="6" cy="6" r="2"/><circle cx="18" cy="6" r="2"/><circle cx="12" cy="18" r="2"/><path d="M7.5 7.5 11 16M16.5 7.5 13 16M8 6h8"/>')}</span>` +
            `<span><span class="pk-name">${name}</span><br><span class="pk-kind">${custom ? 'custom' : 'tayyor'}</span></span>`,
        })));
        return grid;
      };
      const body = h('div', {}, [
        h('div', { class: 'b-pick-group' }, [h('h4', { text: 'Tayyor topologiyalar' }), mkList(list.builtin || [], false)]),
        h('div', { class: 'b-pick-group', style: 'margin-top:16px' }, [h('h4', { text: 'Saqlangan (custom)' }), mkList(list.custom || [], true)]),
      ]);
      const box = modal({
        title: 'Topologiyani ochish', sub: 'Yuklangach sahnaga chiziladi',
        icon: ico('<path d="M3 7h6l2 2h10v10H3z"/>'),
        body,
        actions: [{ label: 'Yopish', cls: 'ghost', onClick: (c) => c() }],
      });
    }

    async function loadTopology(name) {
      let topo;
      try { topo = await ctx.api.get_topology(name); } catch (e) { ctx.toast('Yuklab boʻlmadi: ' + e.message, 'danger'); return; }
      if (!topo || topo.error) { ctx.toast(topo && topo.error ? topo.error : 'Topologiya topilmadi.', 'danger'); return; }
      model.switches = {}; model.hosts = {}; model.links = {};
      selected = null; linkSrc = null; asOrder = [];
      const pos = topo.positions || {};
      const swIds = Object.keys(topo.switches || {});
      // avto-joylashuv (positions bo'lmasa doira bo'yicha)
      const cx = VW / 2, cy = VH / 2, R = Math.min(VW, VH) * 0.34;
      swIds.forEach((id, i) => {
        const src = topo.switches[id]; const p = pos[id];
        const ang = -Math.PI / 2 + (i / Math.max(swIds.length, 1)) * Math.PI * 2;
        model.switches[id] = {
          as: src.as, role: src.role,
          area: src.area, isis_level: src.isis_level,
          x: p ? p[0] : cx + R * Math.cos(ang),
          y: p ? p[1] : cy + R * Math.sin(ang),
        };
        if (src.area === undefined) delete model.switches[id].area;
      });
      const access = topo.access_links || {};
      const hostIdsBySwitch = {};
      Object.entries(topo.hosts || {}).forEach(([id, ho]) => {
        (hostIdsBySwitch[ho.switch] = hostIdsBySwitch[ho.switch] || []).push(id);
      });
      Object.entries(topo.hosts || {}).forEach(([id, ho]) => {
        const p = pos[id]; let x, y;
        if (p) { x = p[0]; y = p[1]; }
        else {
          const sw = model.switches[ho.switch];
          const sibs = hostIdsBySwitch[ho.switch] || [id];
          const k = sibs.indexOf(id), ang = -Math.PI / 2 + (k / Math.max(sibs.length, 1)) * Math.PI * 2;
          x = (sw ? sw.x : cx) + 70 * Math.cos(ang);
          y = (sw ? sw.y : cy) + 70 * Math.sin(ang);
        }
        model.hosts[id] = {
          switch: ho.switch, ip: ho.ip, role: ho.role, x: clamp(x, 30, VW - 30), y: clamp(y, 26, VH - 26),
          access: access[id] || { bw: 20, delay: '3ms', loss: 0 },
        };
      });
      Object.entries(topo.links || {}).forEach(([k, v]) => { model.links[k] = v; });
      chipVal.className = 'b-chip'; chipVal.innerHTML = '<span>tekshirilmagan</span>';
      render();
      ctx.toast(`“${name}” yuklandi.`, 'success');
    }

    // ============================ TOZALASH ===================================
    function confirmClear() {
      if (!Object.keys(model.switches).length && !Object.keys(model.hosts).length) { ctx.toast('Sahna allaqachon boʻsh.', 'info'); return; }
      modal({
        title: 'Sahnani tozalash?', danger: true, sub: 'Barcha switch, host va linklar oʻchadi.',
        icon: ico('<path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14"/>'),
        actions: [
          { label: 'Bekor', cls: 'ghost', onClick: (c) => c() },
          {
            label: 'Tozalash', cls: 'danger', onClick: (c) => {
              model.switches = {}; model.hosts = {}; model.links = {};
              selected = null; linkSrc = null; asOrder = [];
              chipVal.className = 'b-chip'; chipVal.innerHTML = '<span>tekshirilmagan</span>';
              render(); c(); ctx.toast('Sahna tozalandi.', 'info');
            }
          },
        ],
      });
    }

    // ---- dastlabki holat ----
    setMode('select');
    requestAnimationFrame(() => { fitViewBox(); render(); });
  },

  onShow() {
    // Sahna o'lchami ResizeObserver orqali avtomatik moslashadi; bu yerda
    // qo'shimcha ish shart emas.
  },
});
