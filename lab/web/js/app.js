/* NetLab — ilova: yuqori panel, palitra, resurslar, dock (hodisalar/paketlar), modallar. */
(function (NL) {
  'use strict';
  const S = NL.store, h = NL.h;
  const ico = NL.ui.ico;
  const PALETTE = ['host', 'server', 'switch', 'p4switch', 'router', 'sdnrouter', 'wifiap'];

  // ---------- modal ----------
  NL.modal = function (title, content, buttons) {
    const back = h('div', { class: 'modal-back' });
    const close = () => back.remove();
    const m = h('div', { class: 'modal' }, [h('h3', { text: title }), content,
      h('div', { class: 'btn-row', style: { justifyContent: 'flex-end' } },
        (buttons || [['Yopish', 'ghost', () => true]]).map(([t, cls, fn]) => h('button', { class: 'btn sm ' + cls, text: t, onclick: async () => { if (await fn()) close(); } })))]);
    back.appendChild(m);
    back.addEventListener('pointerdown', (e) => { if (e.target === back) close(); });
    document.body.appendChild(back);
    const f = m.querySelector('input,select,textarea'); if (f) setTimeout(() => f.focus(), 30);
    return close;
  };
  const confirmDirty = (msg) => !S.dirty || window.confirm(msg || 'Saqlanmagan o\'zgarishlar bor. Davom etilsinmi?');

  // ---------- yuqori panel ----------
  function buildTop(el) {
    const name = h('input', { class: 'input proj-name', title: 'Loyiha nomi' });
    name.addEventListener('change', () => {
      const v = name.value.trim().replace(/[^A-Za-z0-9_\-]/g, '_').slice(0, 40) || 'untitled';
      name.value = v; S.project.name = v; S.touch();
    });
    const runBtn = h('button', { class: 'btn primary sm run-btn', onclick: () => (S.running() ? S.stop() : S.start()) });
    const capBtn = h('button', { class: 'btn ghost sm', title: 'Paket animatsiyasi (tcpdump)', onclick: async () => {
      try { S.status = await NL.api.post('/api/net/capture', { on: !S.status.capture }); NL.emit('status'); } catch (e) { NL.toast(e.message, 'error'); }
    } });
    const wsDot = h('span', { class: 'ws-dot', title: 'Backend aloqasi' });
    const drv = h('span', { class: 'badge' });
    const caps = h('div', { class: 'caps' });
    const busy = h('span', { class: 'note' });
    const theme = h('button', { class: 'icon-btn', title: 'Kun / tun', style: { width: '34px', height: '34px' }, html: '<span class="theme-ico"></span>', onclick: () => {
      const t = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
      document.documentElement.dataset.theme = t; NL.storage.set('netlab.theme', t);
    } });
    const wrap = h('div', { class: 'row', style: { gap: '6px' } }, [h('span', { class: 'dirty-dot', title: 'Saqlanmagan' }), name]);
    el.append(
      h('div', { class: 'lab-brand' }, [h('div', { class: 'mark', html: ico('<circle cx="5" cy="12" r="2.2"/><circle cx="19" cy="6" r="2.2"/><circle cx="19" cy="18" r="2.2"/><path d="M7 11l10-4M7 13l10 4"/>') }), h('span', { text: 'NetLab' })]),
      wrap,
      h('button', { class: 'btn ghost sm', text: 'Yangi', onclick: newProject }),
      h('button', { class: 'btn ghost sm', text: 'Ochish', onclick: openPicker }),
      h('button', { class: 'btn ghost sm', text: 'Saqlash', onclick: () => S.save().catch(e => NL.toast(e.message, 'error')) }),
      h('button', { class: 'btn ghost sm', text: 'Eksport', onclick: exportJson }),
      h('button', { class: 'btn ghost sm', text: 'Import', onclick: importJson }),
      h('div', { class: 'top-spacer' }), busy, caps, drv, wsDot, capBtn, runBtn, theme);
    const sync = () => {
      wrap.classList.toggle('dirty', S.dirty);
      if (document.activeElement !== name) name.value = S.project.name;
      runBtn.textContent = S.running() ? '■ To\'xtatish' : '▶ Ishga tushirish';
      runBtn.classList.toggle('running', S.running());
      capBtn.textContent = 'Paketlar: ' + (S.status.capture === false ? 'off' : 'on');
      capBtn.style.display = S.running() ? '' : 'none';
    };
    NL.on('change', sync); NL.on('status', sync);
    NL.on('ws', (ok) => { wsDot.className = 'ws-dot ' + (ok ? 'ok' : 'bad'); });
    NL.on('busy', (m) => { busy.textContent = m || ''; runBtn.disabled = !!m; });
    drv.textContent = 'driver: ' + S.info.driver;
    drv.className = 'badge ' + (S.info.driver === 'real' ? 'ok' : 'warn');
    const cl = { mininet: 'Mininet', ovs: 'OVS', frr: 'FRR', p4: 'BMv2', p4c: 'p4c', sdn: 'os-ken', tcpdump: 'tcpdump' };
    if (S.info.driver === 'demo') caps.appendChild(h('span', { class: 'cap', title: 'Mininet\'siz soddalashtirilgan simulyatsiya', text: 'demo rejim' }));
    else Object.entries(cl).forEach(([k, l]) => caps.appendChild(h('span', { class: 'cap' + (S.info.capabilities[k] ? ' on' : ''), title: S.info.capabilities[k] ? 'o\'rnatilgan' : 'topilmadi', text: l })));
    sync();
  }

  function newProject() {
    if (S.running()) { NL.toast('Avval tarmoqni to\'xtating', 'warn'); return; }
    if (!confirmDirty()) return;
    S.load(S.empty('untitled'));
    NL.canvas.fit();
  }

  async function openPicker() {
    if (S.running()) { NL.toast('Avval tarmoqni to\'xtating', 'warn'); return; }
    let list;
    try { list = await NL.api.get('/api/projects'); } catch (e) { NL.toast(e.message, 'error'); return; }
    const box = h('div', { class: 'pick-list' });
    let close;
    const pick = async (n, ex) => {
      if (!confirmDirty()) return;
      try { const p = await NL.api.get(`/api/projects/${encodeURIComponent(n)}${ex ? '?example=true' : ''}`); S.load(p, { dirty: ex }); close(); setTimeout(() => NL.canvas.fit(), 30); }
      catch (e) { NL.toast(e.message, 'error'); }
    };
    if (list.examples.length) box.appendChild(h('div', { class: 'sec-title', text: 'Namunalar' }));
    list.examples.forEach(p => box.appendChild(h('button', { onclick: () => pick(p.name, true) }, [p.name, h('small', { text: 'namuna' })])));
    box.appendChild(h('div', { class: 'sec-title', text: 'Mening loyihalarim' }));
    if (!list.projects.length) box.appendChild(h('div', { class: 'res-empty', text: 'hali saqlanmagan' }));
    list.projects.forEach(p => box.appendChild(h('button', { onclick: () => pick(p.name, false) }, [p.name, h('small', { text: new Date(p.mtime * 1000).toLocaleString() })])));
    close = NL.modal('Loyihani ochish', box);
  }

  function exportJson() {
    const blob = new Blob([JSON.stringify(S.project, null, 2)], { type: 'application/json' });
    const a = h('a', { href: URL.createObjectURL(blob), download: (S.project.name || 'netlab') + '.json' });
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }
  function importJson() {
    if (S.running()) { NL.toast('Avval tarmoqni to\'xtating', 'warn'); return; }
    const f = h('input', { type: 'file', accept: '.json,application/json' });
    f.addEventListener('change', async () => {
      const file = f.files[0]; if (!file) return;
      try { const p = JSON.parse(await file.text()); if (!p.devices) throw new Error('NetLab loyihasi emas'); S.load(p, { dirty: true }); NL.canvas.fit(); }
      catch (e) { NL.toast('Import: ' + e.message, 'error'); }
    });
    f.click();
  }

  // ---------- chap panel ----------
  function buildLeft(el) {
    const pal = h('div', { class: 'palette' });
    const palBtns = {};
    PALETTE.forEach(t => {
      const b = h('div', { class: 'pal-item', draggable: 'true', title: 'Bosing va kanvasga joylang (yoki sudrang). Shift — bir nechta.',
        onclick: () => NL.canvas.setMode(NL.canvas.mode === 'place' && NL.canvas.placeType === t ? 'select' : 'place', t) },
        [h('span', { html: NL.icon(t, 34) }), h('span', { text: S.info.device_types[t].label })]);
      b.addEventListener('dragstart', (e) => { e.dataTransfer.setData('text/netlab-type', t); });
      palBtns[t] = b; pal.appendChild(b);
    });
    const tools = h('div', { class: 'tools' });
    const toolBtns = {};
    [['select', 'Tanlash / ko\'chirish', 'V', '<path d="M5 3l6 17 2.2-6.8L20 11z"/>'],
     ['link', 'Kabel ulash', 'C', '<circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="6" r="2.5"/><path d="M8 16 16 8"/>'],
     ['delete', 'O\'chirish', 'D', '<path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13"/>']].forEach(([m, l, k, p]) => {
      const b = h('button', { class: 'tool', html: ico(p) + `<span>${l}</span><kbd>${k}</kbd>`, onclick: () => NL.canvas.setMode(m) });
      toolBtns[m] = b; tools.appendChild(b);
    });
    NL.on('mode', (m) => {
      Object.entries(toolBtns).forEach(([k, b]) => b.classList.toggle('active', k === m));
      Object.entries(palBtns).forEach(([k, b]) => b.classList.toggle('active', m === 'place' && NL.canvas.placeType === k));
    });
    const p4list = h('div', { class: 'res-list' });
    const sdnlist = h('div', { class: 'res-list' });
    const sclist = h('div', { class: 'res-list' });
    el.append(h('div', { class: 'sec-title', text: 'Qurilmalar' }), pal,
      h('div', { class: 'sec-title', text: 'Asboblar' }), tools,
      h('div', { class: 'sec-title' }, ['P4 dasturlar', h('button', { title: 'Yangi P4 dastur', text: '+', onclick: () => newResource('p4') })]), p4list,
      h('div', { class: 'sec-title' }, ['SDN kontrollerlar', h('button', { title: 'Yangi kontroller', text: '+', onclick: () => newResource('sdn') })]), sdnlist,
      h('div', { class: 'sec-title' }, ['Ssenariylar', h('button', { title: 'Yangi ssenariy (trafik + hodisalar)', text: '+', onclick: newScenario })]), sclist,
      h('button', { class: 'btn ghost sm', text: 'Natijalar va solishtirish', onclick: () => NL.reswin.open() }));
    const sync = () => {
      p4list.innerHTML = ''; sdnlist.innerHTML = '';
      const p4 = Object.keys(S.project.p4_programs), sdn = Object.entries(S.project.controllers);
      if (!p4.length) p4list.appendChild(h('div', { class: 'res-empty', text: 'yo\'q — P4 switch qo\'shilganda shablon qo\'shiladi' }));
      p4.forEach(n => p4list.appendChild(h('div', { class: 'res-item', onclick: () => NL.p4win.open(n) },
        [h('span', { html: NL.icon('p4switch', 16) }), n, h('span', { class: 'tag', text: '.p4' })])));
      if (!sdn.length) sdnlist.appendChild(h('div', { class: 'res-empty', text: 'yo\'q' }));
      sclist.innerHTML = '';
      const scs = Object.entries(S.project.scenarios || {});
      if (!scs.length) sclist.appendChild(h('div', { class: 'res-empty', text: 'yo\'q — trafik va hodisalarni tahlil qilish uchun' }));
      scs.forEach(([n, sc]) => sclist.appendChild(h('div', { class: 'res-item', onclick: () => NL.scwin.open(n) },
        [h('span', { html: NL.ui.ico('<path d="M4 19V5M4 19h16M8 15l3-4 3 2 5-6"/>'), style: { width: '16px', height: '16px', display: 'inline-flex' } }), n,
         h('span', { class: 'tag', text: `${(sc.apps || []).length} ilova` })])));
      sdn.forEach(([n, c]) => sdnlist.appendChild(h('div', { class: 'res-item', onclick: () => NL.sdnwin.open(n) },
        [h('span', { html: NL.icon('sdnrouter', 16) }), n, h('span', { class: 'tag', text: ':' + c.port })])));
    };
    NL.on('change', sync);
    sync();
  }

  function newScenario() {
    const name = NL.form.inp('', 'masalan uzilish');
    NL.modal('Yangi ssenariy', h('div', { class: 'stack' }, [NL.form.field('Nom', name),
      h('div', { class: 'note', text: 'Ssenariy = trafik ilovalari (UDP/TCP/ping) + vaqtli hodisalar (kabel uzilishi va h.k.). Uni analitik, ns-3 yoki Mininet dvigatelida ishga tushirib, natijalarni solishtirasiz.' })]), [
      ['Bekor', 'ghost', () => true],
      ['Yaratish', 'primary', () => {
        const n = name.value.trim();
        if (!/^[A-Za-z0-9_\-]{1,30}$/.test(n)) { NL.toast('Nom: harf/raqam/_/- (1-30)', 'error'); return false; }
        if (S.project.scenarios[n]) { NL.toast('Bu nom band', 'error'); return false; }
        S.project.scenarios[n] = { duration: 30, apps: [], events: [] }; S.touch(); NL.scwin.open(n); return true;
      }]]);
  }

  function newResource(kind) {
    const tpls = kind === 'p4' ? S.templates.p4 : S.templates.sdn;
    const name = NL.form.inp('', kind === 'p4' ? 'masalan my_router' : 'masalan c2');
    const tsel = NL.form.sel([['__empty__', '— bo\'sh —'], ...Object.keys(tpls).map(t => [t, 'shablon: ' + t])], Object.keys(tpls)[0]);
    const content = h('div', { class: 'stack' }, [NL.form.field('Nom', name), NL.form.field('Asos', tsel)]);
    NL.modal(kind === 'p4' ? 'Yangi P4 dastur' : 'Yangi SDN kontroller', content, [
      ['Bekor', 'ghost', () => true],
      ['Yaratish', 'primary', () => {
        const n = name.value.trim();
        const re = kind === 'p4' ? /^[a-z][a-z0-9_]{0,23}$/ : /^[a-z][a-z0-9]{0,7}$/;
        if (!re.test(n)) { NL.toast(kind === 'p4' ? 'Nom: kichik harf, [a-z0-9_], 1-24' : 'Nom: kichik harf, [a-z0-9], 1-8', 'error'); return false; }
        const store = kind === 'p4' ? S.project.p4_programs : S.project.controllers;
        if (store[n]) { NL.toast('Bu nom band', 'error'); return false; }
        const src = tsel.value === '__empty__' ? '' : tpls[tsel.value];
        if (kind === 'p4') { S.project.p4_programs[n] = src; S.touch(); NL.p4win.open(n); }
        else {
          const used = Object.values(S.project.controllers).map(c => c.port);
          let port = 6653; while (used.includes(port)) port++;
          S.project.controllers[n] = { code: src, port }; S.touch(); NL.sdnwin.open(n);
        }
        return true;
      }],
    ]);
  }

  // ---------- dock ----------
  function buildDock(el, lab) {
    const seg = h('div', { class: 'segment' });
    const tabs = {};
    let cur = 'pkt';
    const body = h('div', { class: 'dock-body' });
    const pktRows = [], logRows = [];
    let paused = false;
    const hidden = new Set(NL.storage.get('netlab.hiddenProto', ['STP', 'LLDP', 'IPv6']));
    const protoSel = h('div', { class: 'row wrap', style: { gap: '4px' } });
    const counter = h('span', { class: 'note' });
    let nPkts = 0;
    Object.keys(NL.PROTO_COLORS).forEach(p => {
      const c = h('label', { class: 'toggle', style: { fontSize: '11px', gap: '3px' } }, [h('input', { type: 'checkbox' }), h('span', { style: { color: NL.PROTO_COLORS[p] }, text: p })]);
      const cb = c.querySelector('input'); cb.checked = !hidden.has(p);
      cb.addEventListener('change', () => { if (cb.checked) hidden.delete(p); else hidden.add(p); NL.storage.set('netlab.hiddenProto', [...hidden]); });
      protoSel.appendChild(c);
    });
    NL.dock = { protoHidden: (p) => hidden.has(p) };
    [['pkt', 'Paketlar'], ['log', 'Hodisalar']].forEach(([k, l]) => {
      const b = h('button', { text: l, onclick: () => { cur = k; Object.entries(tabs).forEach(([x, y]) => y.classList.toggle('active', x === k)); redraw(); } });
      tabs[k] = b; seg.appendChild(b);
    });
    tabs.pkt.classList.add('active');
    const pauseBtn = h('button', { class: 'btn ghost sm', text: 'Pauza', onclick: () => { paused = !paused; pauseBtn.textContent = paused ? 'Davom' : 'Pauza'; } });
    const collapse = h('button', { class: 'btn ghost sm', text: '▾', title: 'Yig\'ish', onclick: () => {
      lab.classList.toggle('dock-collapsed'); collapse.textContent = lab.classList.contains('dock-collapsed') ? '▴' : '▾';
    } });
    el.append(h('div', { class: 'dock-head' }, [seg, protoSel, h('div', { class: 'top-spacer' }), counter, pauseBtn,
      h('button', { class: 'btn ghost sm', text: 'Tozalash', onclick: () => { (cur === 'pkt' ? pktRows : logRows).length = 0; redraw(); } }), collapse]), body);

    const linkName = (lid, dir) => {
      const l = S.link(lid); if (!l) return lid;
      const [a, b] = dir === 'ba' ? [l.b, l.a] : [l.a, l.b];
      return `${a.dev}:${a.port} → ${b.dev}:${b.port}`;
    };
    function pktRow(p) {
      return h('div', { class: 'dock-row' }, [h('span', { class: 't', text: NL.clock(p.ts) }), h('span', { text: linkName(p.link, p.dir) }),
        h('span', { class: 'proto', style: { color: NL.PROTO_COLORS[p.proto] || NL.PROTO_COLORS.other }, text: p.proto }), h('span', { text: p.summary, title: p.summary })]);
    }
    function logRow(e) {
      return h('div', { class: 'dock-row log' }, [h('span', { class: 't', text: NL.clock(e.ts) }), h('span', { class: 'lvl-' + e.level, text: e.device || e.level }), h('span', { text: e.msg })]);
    }
    function redraw() {
      body.innerHTML = '';
      const rows = cur === 'pkt' ? pktRows.filter(p => !hidden.has(p.proto)).slice(-300).map(pktRow) : logRows.slice(-300).map(logRow);
      rows.forEach(r => body.appendChild(r));
      body.scrollTop = 1e9;
      protoSel.style.display = cur === 'pkt' ? '' : 'none';
    }
    let pending = false;
    const schedule = () => { if (!pending) { pending = true; requestAnimationFrame(() => { pending = false; redraw(); }); } };
    NL.on('ev:packet', (p) => {
      nPkts++; counter.textContent = nPkts + ' paket';
      if (paused) return;
      pktRows.push(p); if (pktRows.length > 2000) pktRows.splice(0, 500);
      if (cur === 'pkt' && !hidden.has(p.proto)) schedule();
    });
    NL.on('ev:log', (e) => { logRows.push(e); if (logRows.length > 1000) logRows.splice(0, 200); if (cur === 'log') schedule(); if (e.level === 'error') NL.toast((e.device ? e.device + ': ' : '') + e.msg, 'error'); });
    NL.log = (level, msg, device) => NL.emit('ev:log', { ts: Date.now() / 1000, level, msg, device });
    redraw();
  }

  // ---------- klaviatura ----------
  function bindKeys() {
    addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && NL.canvas.mode === 'pick') { NL.canvas.pickCb = null; NL.canvas.setMode('select'); return; }
      if (e.target.closest && e.target.closest('input,textarea,select,.xterm,.win,.modal-back')) return;
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); S.save().catch(er => NL.toast(er.message, 'error')); return; }
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const k = e.key.toLowerCase();
      if (k === 'escape') { NL.canvas.pickCb = null; NL.canvas.setMode('select'); }
      else if (k === 'v') NL.canvas.setMode('select');
      else if (k === 'c') NL.canvas.setMode('link');
      else if (k === 'd') NL.canvas.setMode('delete');
      else if ((k === 'delete' || k === 'backspace') && S.selection) {
        e.preventDefault();
        if (S.selection.kind === 'device') S.removeDevice(S.selection.id); else S.removeLink(S.selection.id);
      } else if (k === 'enter' && S.selection && S.selection.kind === 'device') NL.devwin.open(S.selection.id);
      else if (k === 'f') NL.canvas.fit();
    });
    addEventListener('beforeunload', (e) => { if (S.dirty) { e.preventDefault(); e.returnValue = ''; } });
  }

  // ---------- ishga tushirish ----------
  async function boot() {
    const t = NL.storage.get('netlab.theme', null);
    if (t) document.documentElement.dataset.theme = t;
    try {
      [S.info, S.templates] = await Promise.all([NL.api.get('/api/info'), NL.api.get('/api/templates')]);
    } catch (e) {
      document.body.appendChild(h('div', { class: 'error-box', text: 'Backend bilan aloqa yo\'q: ' + e.message })); return;
    }
    S.status = S.info.status;
    const draft = NL.storage.get('netlab.draft', null);
    let live = null;
    if (S.status.running) { try { live = (await NL.api.get('/api/net/project')).project; } catch (e) { /* */ } }
    if (live) S.load(live, { dirty: true });
    else S.load(draft && draft.devices ? draft : S.empty('untitled'), { dirty: !!draft });
    const lab = document.getElementById('lab');
    buildTop(lab.querySelector('.lab-top'));
    buildLeft(lab.querySelector('.lab-left'));
    NL.canvas.init(lab.querySelector('.lab-stage'));
    NL.inspector.init(lab.querySelector('.lab-right'));
    buildDock(lab.querySelector('.lab-dock'), lab);
    bindKeys();
    NL.canvas.setMode('select');
    NL.events.connect();
    NL.emit('change'); NL.emit('status');
    setTimeout(() => NL.canvas.fit(), 50);
    if (!Object.keys(S.project.devices).length) NL.log('info', 'Boshlash: "Ochish" → namuna (ospf_lab) yoki chapdan qurilma tanlang.');
  }
  document.addEventListener('DOMContentLoaded', boot);
})(window.NL);
