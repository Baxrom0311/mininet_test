/* NetLab — ssenariylar (trafik ilovalari + vaqtli hodisalar), ishga tushirish
   (analitik / ns-3 / Mininet) va natijalar (grafiklar, solishtirish, jadval). */
(function (NL) {
  'use strict';
  const S = NL.store, h = NL.h;
  const F = () => NL.form;
  const SERIES_N = 6;
  const seriesColor = (i) => getComputedStyle(document.documentElement).getPropertyValue(`--s${(i % SERIES_N) + 1}`).trim() || '#3987e5';
  const fmtMs = (v) => v == null ? '—' : (v >= 1000 ? (v / 1000).toFixed(2) + ' s' : v.toFixed(v < 10 ? 2 : 1) + ' ms');
  const fmtPct = (v) => v == null ? '—' : v.toFixed(v < 1 ? 2 : 1) + '%';

  NL.on('project', () => { S.project.scenarios = S.project.scenarios || {}; });
  const l3devs = () => Object.entries(S.project.devices).filter(([, d]) => ['host', 'server', 'router'].includes(d.type)).map(([n]) => n).sort();

  // ======================= ssenariy muharriri =======================
  NL.scwin = {
    open(name) {
      NL.win.open('sc:' + name, { title: 'Ssenariy: ' + name, sub: 'trafik ilovalari + vaqtli hodisalar → ishga tushirish', w: 900, h: 640,
        icon: NL.ui.ico('<path d="M4 19V5M4 19h16M8 15l3-4 3 2 5-6"/>'),
        render(body) { return renderScenario(body, name); } });
    },
  };

  function renderScenario(body, name) {
    const sc = S.project.scenarios[name];
    sc.apps = sc.apps || []; sc.events = sc.events || [];
    const save = () => S.touch();
    const dur = F().inp(sc.duration || 30, '30', { type: 'number', min: 1, max: 3600, style: { width: '110px' } });
    dur.addEventListener('change', () => { sc.duration = parseFloat(dur.value) || 30; save(); });
    const notes = F().inp(sc.notes || '', 'izoh (nima tekshirilyapti?)');
    notes.addEventListener('change', () => { sc.notes = notes.value; save(); });

    // --- ilovalar
    const appsBox = h('div');
    const drawApps = () => {
      appsBox.innerHTML = '';
      const devs = l3devs();
      const rows = sc.apps.map((a, i) => {
        const cell = (el) => h('td', {}, el);
        const typ = F().sel([['udp', 'UDP (CBR)'], ['tcp', 'TCP (bulk)'], ['ping', 'Ping (ICMP)']], a.type);
        const src = F().sel(devs.map(d => [d, d]), a.src), dst = F().sel(devs.map(d => [d, d]), a.dst);
        const rate = F().inp(a.rate || '', a.type === 'tcp' ? 'cheksiz' : '5Mbps');
        const size = F().inp(a.size || '', a.type === 'ping' ? '64' : '1000', { type: 'number' });
        const st = F().inp(a.start ?? 1, '', { type: 'number', step: 'any' });
        const sp = F().inp(a.stop ?? sc.duration, '', { type: 'number', step: 'any' });
        const lb = F().inp(a.label || '', `${a.type} ${a.src}→${a.dst}`);
        [typ, src, dst, rate, size, st, sp, lb].forEach(el => el.addEventListener('change', () => {
          Object.assign(a, { type: typ.value, src: src.value, dst: dst.value, rate: rate.value.trim() || undefined,
            size: size.value ? parseInt(size.value, 10) : undefined, start: parseFloat(st.value) || 0,
            stop: parseFloat(sp.value) || sc.duration, label: lb.value.trim() || undefined });
          if (el === typ) drawApps();
          save();
        }));
        [rate, size, st, sp].forEach(el => { el.style.width = '84px'; });
        return h('tr', {}, [cell(h('span', { class: 'mono', text: a.id })), cell(typ), cell(src), cell(dst), cell(rate), cell(size), cell(st), cell(sp), cell(lb),
          cell(h('button', { class: 'btn ghost sm', text: '×', title: 'O\'chirish', onclick: () => { sc.apps.splice(i, 1); save(); drawApps(); } }))]);
      });
      appsBox.appendChild(h('div', { class: 'isec sc-table' }, [h('h4', {}, ['Trafik ilovalari', h('button', { class: 'btn neon sm', text: '+ ilova', onclick: () => {
        if (devs.length < 2) { NL.toast('Kamida 2 ta host/server/router kerak', 'warn'); return; }
        let k = sc.apps.length + 1; while (sc.apps.some(x => x.id === 'a' + k)) k++;
        sc.apps.push({ id: 'a' + k, type: 'udp', src: devs[0], dst: devs[devs.length - 1], rate: '1Mbps', size: 1000, start: 1, stop: sc.duration || 30 });
        save(); drawApps();
      } })]),
      rows.length ? h('table', {}, [h('tr', {}, ['id', 'Tur', 'Manba', 'Qabul', 'Tezlik', 'Paket (B)', 'Start s', 'Stop s', 'Nom', ''].map(x => h('th', { text: x }))), ...rows])
        : h('div', { class: 'empty-row', text: 'Ilova yo\'q. UDP — video/VoIP (qat\'iy tezlik), TCP — fayl uzatish (bor sig\'imni oladi), Ping — RTT o\'lchovi.' })]));
    };

    // --- hodisalar
    const evBox = h('div');
    const drawEvents = () => {
      evBox.innerHTML = '';
      const links = Object.entries(S.project.links).map(([lid, l]) => [lid, `${lid}: ${l.a.dev}↔${l.b.dev}`]);
      const rows = sc.events.map((e, i) => {
        const t = F().inp(e.t ?? 10, '', { type: 'number', step: 'any', style: { width: '80px' } });
        const act = F().sel([['link_down', 'Kabel uzilishi'], ['link_up', 'Kabel tiklanishi'], ['link_params', 'Kabel parametrlari']], e.action);
        const lk = F().sel(links, e.link);
        const p = e.params || {};
        const bw = F().inp(p.bw ?? '', 'Mbit/s', { style: { width: '80px' } }), dl = F().inp(p.delay ?? '', 'ms', { style: { width: '70px' } }),
          ls = F().inp(p.loss ?? '', 'loss %', { style: { width: '70px' } });
        const pBox = h('div', { class: 'row', style: { gap: '4px' } }, [bw, dl, ls]);
        pBox.style.display = e.action === 'link_params' ? '' : 'none';
        [t, act, lk, bw, dl, ls].forEach(el => el.addEventListener('change', () => {
          e.t = parseFloat(t.value) || 0; e.action = act.value; e.link = lk.value;
          if (e.action === 'link_params') {
            e.params = {};
            if (bw.value) e.params.bw = parseFloat(bw.value);
            if (dl.value) e.params.delay = dl.value.trim();
            if (ls.value) e.params.loss = parseFloat(ls.value);
          } else delete e.params;
          pBox.style.display = e.action === 'link_params' ? '' : 'none';
          save();
        }));
        return h('tr', {}, [h('td', {}, t), h('td', {}, act), h('td', {}, lk), h('td', {}, pBox),
          h('td', {}, h('button', { class: 'btn ghost sm', text: '×', onclick: () => { sc.events.splice(i, 1); save(); drawEvents(); } }))]);
      });
      evBox.appendChild(h('div', { class: 'isec sc-table' }, [h('h4', {}, ['Vaqtli hodisalar', h('button', { class: 'btn neon sm', text: '+ hodisa', onclick: () => {
        const first = Object.keys(S.project.links)[0];
        if (!first) { NL.toast('Avval kabel ulang', 'warn'); return; }
        sc.events.push({ t: Math.round((sc.duration || 30) / 3), action: 'link_down', link: first }); save(); drawEvents();
      } })]),
      rows.length ? h('table', {}, [h('tr', {}, ['Vaqt (s)', 'Amal', 'Kabel', 'Parametrlar', ''].map(x => h('th', { text: x }))), ...rows])
        : h('div', { class: 'empty-row', text: 'Hodisa yo\'q — masalan t=10s da kabel uzilishi, t=16s da tiklanishi (failover tahlili).' })]));
    };

    // --- ishga tushirish
    const engSel = F().sel([], '');
    engSel.style.width = '300px';
    const engHint = h('span', { class: 'note' });
    const bar = h('i'), prog = h('div', { class: 'bar', style: { flex: '1', minWidth: '120px' } }, bar);
    const runLog = h('pre', { class: 'log-out' });
    const runBtn = h('button', { class: 'btn primary sm', text: '▶ Ishga tushirish', onclick: run });
    let engines = [];
    NL.api.get('/api/engines').then(r => {
      engines = r.engines;
      engSel.innerHTML = '';
      engines.forEach(e => { const o = new Option(e.label + (e.available ? '' : ' — mavjud emas'), e.id); o.disabled = !e.available; engSel.appendChild(o); });
      const last = NL.storage.get('netlab.engine', 'analytic');
      const pick = engines.find(e => e.id === last && e.available) || engines.find(e => e.available);
      if (pick) engSel.value = pick.id;
      hint();
    }).catch(() => {});
    const hint = () => { const e = engines.find(x => x.id === engSel.value); engHint.textContent = e ? e.hint : ''; };
    engSel.addEventListener('change', () => { NL.storage.set('netlab.engine', engSel.value); hint(); });
    let current = null;
    const unsub = NL.on('ev:run', (ev) => {
      if (ev.run_id !== current) return;
      if (ev.progress != null) bar.style.width = (ev.progress * 100) + '%';
      if (ev.log) { runLog.textContent += ev.log + '\n'; runLog.scrollTop = 1e9; }
      if (ev.done) {
        runBtn.disabled = false;
        if (ev.status === 'done') { NL.toast('Natija tayyor', 'ok'); NL.reswin.open(current); }
        else { runLog.classList.add('err'); runLog.textContent += (ev.error || 'xato') + '\n'; }
        current = null;
      }
    });
    async function run() {
      dur.dispatchEvent(new Event('change'));
      try {
        const v = await NL.api.post('/api/scenario/validate', { project: S.project, scenario: sc });
        if (v.errors.length) { NL.toast('Ssenariy xatolari:\n• ' + v.errors.slice(0, 6).join('\n• '), 'error'); return; }
        runLog.textContent = ''; runLog.classList.remove('err'); bar.style.width = '0';
        const r = await NL.api.post('/api/runs', { project: S.project, scenario: name, engine: engSel.value });
        current = r.run_id; runBtn.disabled = true;
        runLog.textContent = `${r.run_id} boshlandi\n`;
      } catch (e) { NL.toast(e.message, 'error'); }
    }

    drawApps(); drawEvents();
    body.append(
      h('div', { class: 'row wrap', style: { gap: '10px' } }, [h('span', { class: 'note', text: 'Davomiylik (s):' }), dur, h('div', { style: { flex: '1', minWidth: '200px' } }, notes)]),
      appsBox, evBox,
      h('div', { class: 'isec' }, [h('h4', { text: 'Ishga tushirish' }), h('div', { style: { padding: '10px', display: 'flex', flexDirection: 'column', gap: '8px' } }, [
        h('div', { class: 'row wrap' }, [engSel, runBtn, prog, h('button', { class: 'btn ghost sm', text: 'Natijalar', onclick: () => NL.reswin.open() })]),
        engHint, runLog])]));
    return { dispose: unsub };
  }

  // ======================= natijalar oynasi =======================
  const METRICS = [
    { id: 'thr_bps', label: 'O\'tkazuvchanlik', unit: 'bit/s', fmt: (v) => NL.fmtBps(v), sum: 'avg_thr_bps' },
    { id: 'delay_ms', label: 'Kechikish', unit: 'ms', fmt: fmtMs, sum: 'avg_delay_ms' },
    { id: 'rtt_ms', label: 'RTT', unit: 'ms', fmt: fmtMs, sum: 'avg_rtt_ms' },
    { id: 'jitter_ms', label: 'Jitter', unit: 'ms', fmt: fmtMs, sum: 'avg_jitter_ms' },
    { id: 'loss_pct', label: 'Yo\'qotish', unit: '%', fmt: fmtPct, sum: 'loss_pct' },
    { id: 'util', label: 'Kabel yuklamasi', unit: '%', fmt: (v) => fmtPct(v * 100), link: true },
  ];

  NL.reswin = {
    open(runId) {
      const id = 'results';
      if (NL.win.has(id)) { NL.win.focus(id); NL.emit('results:select', runId); return; }
      NL.win.open(id, { title: 'Natijalar — ' + S.project.name, sub: 'ssenariy ishga tushirishlari: grafiklar, solishtirish', w: 1040, h: 680,
        icon: NL.ui.ico('<path d="M4 19V5M4 19h16M7 14l3-3 3 2 5-6"/>'),
        render(body) { return renderResults(body, runId); } });
    },
  };

  function renderResults(body, initial) {
    body.classList.add('flush');
    const list = h('div', { class: 'res-runs' });
    const main = h('div', { class: 'res-main' });
    body.appendChild(h('div', { class: 'res-wrap' }, [list, main]));
    const cache = {};
    let runs = [], selected = new Set(initial ? [initial] : []), metric = NL.storage.get('netlab.metric', 'thr_bps'), flowSel = '__all__', linkSel = '';

    async function loadList() {
      try { runs = (await NL.api.get('/api/runs/' + encodeURIComponent(S.project.name))).runs; }
      catch (e) { list.innerHTML = ''; list.appendChild(h('div', { class: 'res-empty', text: e.message })); return; }
      if (!selected.size && runs.length) selected.add(runs.find(r => r.status !== 'running')?.run_id);
      drawList(); await draw();
    }
    function drawList() {
      list.innerHTML = '';
      list.appendChild(h('div', { class: 'sec-title', text: 'Ishga tushirishlar' }));
      if (!runs.length) list.appendChild(h('div', { class: 'res-empty', text: 'Hali yo\'q — Ssenariy oynasida "Ishga tushirish".' }));
      runs.forEach(r => {
        const cb = h('input', { type: 'checkbox' }); cb.checked = selected.has(r.run_id); cb.disabled = r.status === 'running';
        cb.addEventListener('change', () => { if (cb.checked) selected.add(r.run_id); else selected.delete(r.run_id); draw(); });
        const eng = { analytic: 'analitik', ns3: 'ns-3', mininet: 'mininet' }[r.engine] || r.engine;
        list.appendChild(h('label', { class: 'res-run' + (r.status === 'error' ? ' err' : '') }, [cb,
          h('div', { style: { minWidth: 0, flex: 1 } }, [
            h('div', { class: 'n', text: r.label || r.scenario_name }),
            h('div', { class: 'd', text: `${eng} · ${r.status === 'running' ? Math.round((r.progress || 0) * 100) + '%…' : new Date(r.created * 1000).toLocaleTimeString()}${r.wall_s != null ? ' · ' + r.wall_s + 's' : ''}` })]),
          r.status !== 'running' ? h('button', { class: 'x', title: 'O\'chirish', text: '×', onclick: async (e) => {
            e.preventDefault(); if (!confirm('Natija o\'chirilsinmi?')) return;
            await NL.api.del(`/api/runs/${encodeURIComponent(S.project.name)}/${r.run_id}`); selected.delete(r.run_id); loadList();
          } }) : null]));
      });
    }
    async function getRun(id) {
      if (!cache[id]) cache[id] = await NL.api.get(`/api/runs/${encodeURIComponent(S.project.name)}/${id}`);
      return cache[id];
    }
    let drawGen = 0;
    async function draw() {
      const gen = ++drawGen;      // asinxron chizishlar aralashmasin — faqat oxirgisi qoladi
      const ids = [...selected].filter(Boolean);
      if (!ids.length) { main.innerHTML = ''; main.appendChild(h('div', { class: 'res-empty', style: { padding: '30px' }, text: 'Chapdan bir yoki bir nechta natijani tanlang.' })); return; }
      let data;
      try { data = await Promise.all(ids.map(getRun)); } catch (e) { if (gen === drawGen) { main.innerHTML = ''; main.appendChild(h('div', { class: 'res-empty', text: e.message })); } return; }
      if (gen !== drawGen) return;
      main.innerHTML = '';
      const multi = data.length > 1;
      const runName = (r) => (r.label || r.scenario_name) + ' · ' + ({ analytic: 'analitik', ns3: 'ns-3', mininet: 'mininet' }[r.engine] || r.engine);
      // boshqaruvlar
      const seg = h('div', { class: 'segment' });
      METRICS.forEach(m => seg.appendChild(h('button', { class: m.id === metric ? 'active' : '', text: m.label, onclick: () => { metric = m.id; NL.storage.set('netlab.metric', metric); draw(); } })));
      const M = METRICS.find(m => m.id === metric) || METRICS[0];
      const flowIds = [...new Set(data.flatMap(r => Object.keys(r.flows || {})))];
      const flowLabel = (fid) => { for (const r of data) if (r.flows[fid]) return r.flows[fid].label || fid; return fid; };
      const ctrls = [seg];
      let series = [];
      if (M.link) {
        const lids = [...new Set(data.flatMap(r => Object.keys(r.links || {})))];
        if (!linkSel || !lids.includes(linkSel)) linkSel = lids[0] || '';
        const ls = F().sel(lids.map(l => { const L = S.link(l); return [l, L ? `${l}: ${L.a.dev}↔${L.b.dev}` : l]; }), linkSel);
        ls.style.width = '220px';
        ls.addEventListener('change', () => { linkSel = ls.value; draw(); });
        ctrls.push(ls);
        series = data.map((r, i) => ({ name: runName(r), color: seriesColor(i), points: ((r.links || {})[linkSel] || []).map(p => ({ x: p.t, y: p.util })) }));
        if (!series.some(s => s.points.length)) ctrls.push(h('span', { class: 'note', text: 'Bu dvigatel kabel yuklamasini yozmaydi (ns-3: faqat oqimlar)' }));
      } else if (multi) {
        if (flowSel === '__all__' || !flowIds.includes(flowSel)) flowSel = flowIds[0];
        const fs = F().sel(flowIds.map(f => [f, flowLabel(f)]), flowSel);
        fs.style.width = '220px';
        fs.addEventListener('change', () => { flowSel = fs.value; draw(); });
        ctrls.push(h('span', { class: 'note', text: 'Oqim:' }), fs);
        series = data.map((r, i) => ({ name: runName(r), color: seriesColor(i),
          points: ((r.flows[flowSel] || {}).series || []).map(p => ({ x: p.t, y: p[metric] })) }));
      } else {
        series = flowIds.map((fid, i) => ({ name: flowLabel(fid), color: seriesColor(i),
          points: (data[0].flows[fid].series || []).map(p => ({ x: p.t, y: p[metric] })) }));
      }
      series = series.slice(0, SERIES_N);
      const csvBtn = h('button', { class: 'btn ghost sm', text: 'CSV', title: 'Xulosa jadvalini yuklab olish', onclick: () => exportCsv(data) });
      main.append(h('div', { class: 'row wrap', style: { gap: '10px', padding: '12px 14px 0' } }, [...ctrls, h('div', { class: 'top-spacer' }), csvBtn]));
      const events = multi ? [] : (data[0].events || []);
      const chartBox = h('div', { class: 'chart-card' });
      main.append(chartBox);
      NL.lineChart(chartBox, { series, fmt: M.fmt, title: M.label + (M.link ? '' : multi ? ' — ' + flowLabel(flowSel) : ' — barcha oqimlar'),
        events, xMax: Math.max(...data.map(r => r.duration || 0)) });
      // xulosa jadvali
      const rows = [];
      data.forEach(r => Object.values(r.flows || {}).forEach(f => {
        const s = f.summary || {};
        rows.push([runName(r), f.label || f.id, NL.fmtBps(s.avg_thr_bps), fmtMs(s.avg_delay_ms), fmtMs(s.avg_rtt_ms), fmtMs(s.avg_jitter_ms), fmtPct(s.loss_pct)]);
      }));
      main.append(h('div', { class: 'isec', style: { margin: '0 14px 14px' } }, [h('h4', { text: 'Xulosa (o\'rtacha qiymatlar)' }),
        h('table', {}, [h('tr', {}, ['Ishga tushirish', 'Oqim', 'O\'tkazuvchanlik', 'Kechikish', 'RTT', 'Jitter', 'Yo\'qotish'].map(x => h('th', { text: x }))),
          ...rows.map(r => h('tr', {}, r.map(c => h('td', { text: c }))))])]));
      const notes = [...new Set(data.flatMap(r => r.notes || []))];
      const errs = data.filter(r => r.status === 'error').map(r => `${runName(r)}: ${r.error}`);
      if (notes.length || errs.length) main.append(h('div', { class: 'note', style: { padding: '0 14px 14px', whiteSpace: 'pre-wrap' },
        text: [...errs.map(e => '⚠ ' + e), ...notes.map(n => '• ' + n)].join('\n') }));
    }
    function exportCsv(data) {
      const lines = [['run_id', 'scenario', 'engine', 'flow', 'avg_thr_bps', 'avg_delay_ms', 'avg_rtt_ms', 'avg_jitter_ms', 'loss_pct'].join(',')];
      data.forEach(r => Object.values(r.flows || {}).forEach(f => {
        const s = f.summary || {};
        lines.push([r.run_id, r.scenario_name, r.engine, JSON.stringify(f.label || f.id), s.avg_thr_bps, s.avg_delay_ms, s.avg_rtt_ms, s.avg_jitter_ms, s.loss_pct].map(v => v ?? '').join(','));
      }));
      const a = h('a', { href: URL.createObjectURL(new Blob([lines.join('\n')], { type: 'text/csv' })), download: `${S.project.name}_natijalar.csv` });
      document.body.appendChild(a); a.click(); a.remove();
    }
    const u1 = NL.on('ev:run', (ev) => { if (ev.done || ev.progress === 0) { if (ev.done && ev.status === 'done') selected.add(ev.run_id); loadList(); } });
    const u2 = NL.on('results:select', (rid) => { if (rid) { selected = new Set([rid]); loadList(); } });
    loadList();
    return { dispose() { u1(); u2(); } };
  }

  // ======================= chiziqli grafik (SVG, crosshair + tooltip) =======================
  NL.lineChart = function (box, opt) {
    box.innerHTML = '';
    const series = opt.series.filter(s => s.points.length);
    const head = h('div', { class: 'chart-head' }, [h('div', { class: 'chart-title', text: opt.title || '' })]);
    const legend = h('div', { class: 'chart-legend' });
    series.forEach(s => legend.appendChild(h('span', {}, [h('i', { style: { background: s.color } }), s.name])));
    if (series.length >= 2) head.appendChild(legend);
    box.appendChild(head);
    if (!series.length) { box.appendChild(h('div', { class: 'res-empty', style: { padding: '40px', textAlign: 'center' }, text: 'Bu o\'lchov uchun ma\'lumot yo\'q' })); return; }
    const W = Math.max(box.clientWidth || 700, 360), H = 280, m = { l: 64, r: series.length <= 4 ? 120 : 16, t: 10, b: 28 };
    const xs = series.flatMap(s => s.points.map(p => p.x));
    const ys = series.flatMap(s => s.points.map(p => p.y)).filter(v => v != null && isFinite(v));
    const xMax = Math.max(opt.xMax || 0, ...xs, 1);
    let yMax = Math.max(...ys, 0);
    yMax = yMax <= 0 ? 1 : niceMax(yMax);
    const X = (x) => m.l + (x / xMax) * (W - m.l - m.r), Y = (y) => H - m.b - (y / yMax) * (H - m.t - m.b);
    const svg = NL.s('svg', { viewBox: `0 0 ${W} ${H}`, class: 'chart-svg', width: '100%', height: H, role: 'img', 'aria-label': opt.title });
    // grid + o'qlar
    for (let i = 0; i <= 4; i++) {
      const v = yMax * i / 4, y = Y(v);
      svg.appendChild(NL.s('line', { x1: m.l, x2: W - m.r, y1: y, y2: y, class: 'grid' + (i === 0 ? ' base' : '') }));
      const t = NL.s('text', { x: m.l - 8, y: y + 4, class: 'ax', 'text-anchor': 'end' }); t.textContent = opt.fmt(v); svg.appendChild(t);
    }
    const xStep = niceStep(xMax / 6);
    for (let x = 0; x <= xMax + 1e-9; x += xStep) {
      const t = NL.s('text', { x: X(x), y: H - 8, class: 'ax', 'text-anchor': 'middle' }); t.textContent = x + 's'; svg.appendChild(t);
    }
    // hodisalar
    (opt.events || []).forEach(e => {
      const x = X(e.t);
      svg.appendChild(NL.s('line', { x1: x, x2: x, y1: m.t, y2: H - m.b, class: 'evline' }));
      const t = NL.s('text', { x: x + 4, y: m.t + 11, class: 'evlbl' });
      t.textContent = ({ link_down: '✕ ', link_up: '✓ ', link_params: '⚙ ' }[e.action] || '') + (e.link || '');
      svg.appendChild(t);
    });
    // chiziqlar (null — uzilish)
    series.forEach(s => {
      let d = '', pen = false;
      s.points.forEach(p => {
        if (p.y == null || !isFinite(p.y)) { pen = false; return; }
        d += (pen ? 'L' : 'M') + X(p.x).toFixed(1) + ',' + Y(p.y).toFixed(1); pen = true;
      });
      svg.appendChild(NL.s('path', { d, class: 'ln', stroke: s.color }));
    });
    if (series.length <= 4) {       // to'g'ridan-to'g'ri yorliqlar (oxirgi nuqtada), to'qnashmasin
      const labels = series.map(s => {
        const last = [...s.points].reverse().find(p => p.y != null);
        return last ? { s, x: X(last.x) + 6, y: Y(last.y) + 4 } : null;
      }).filter(Boolean).sort((a, b) => a.y - b.y);
      for (let i = 1; i < labels.length; i++) labels[i].y = Math.max(labels[i].y, labels[i - 1].y + 13);
      const over = labels.length ? labels[labels.length - 1].y - (H - m.b) : 0;
      if (over > 0) labels.forEach(l => { l.y -= over; });
      labels.forEach(l => {
        const t = NL.s('text', { x: l.x, y: l.y, class: 'dl' });
        t.textContent = l.s.name.length > 18 ? l.s.name.slice(0, 17) + '…' : l.s.name; svg.appendChild(t);
      });
    }
    // crosshair
    const cross = NL.s('line', { y1: m.t, y2: H - m.b, class: 'cross', style: 'display:none' });
    const dots = series.map(s => { const c = NL.s('circle', { r: 4.5, fill: s.color, class: 'dot', style: 'display:none' }); return c; });
    svg.appendChild(cross); dots.forEach(d => svg.appendChild(d));
    const hit = NL.s('rect', { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, fill: 'transparent' });
    svg.appendChild(hit);
    const wrap = h('div', { class: 'chart-wrap' }, svg);
    const tip = h('div', { class: 'chart-tip' });
    wrap.appendChild(tip);
    box.appendChild(wrap);
    hit.addEventListener('mousemove', (ev) => {
      const r = svg.getBoundingClientRect();
      const px = (ev.clientX - r.left) * (W / r.width);
      const xv = (px - m.l) / (W - m.l - m.r) * xMax;
      let near = null;
      series.forEach(s => s.points.forEach(p => { if (near == null || Math.abs(p.x - xv) < Math.abs(near - xv)) near = p.x; }));
      cross.setAttribute('x1', X(near)); cross.setAttribute('x2', X(near)); cross.style.display = '';
      const rows = [h('div', { class: 'tt', text: `t = ${near}s` })];
      series.forEach((s, i) => {
        const p = s.points.find(q => q.x === near);
        if (p && p.y != null) { dots[i].setAttribute('cx', X(p.x)); dots[i].setAttribute('cy', Y(p.y)); dots[i].style.display = ''; }
        else dots[i].style.display = 'none';
        rows.push(h('div', { class: 'tr' }, [h('i', { style: { background: s.color } }), h('span', { text: s.name }), h('b', { text: p && p.y != null ? opt.fmt(p.y) : '—' })]));
      });
      tip.innerHTML = ''; rows.forEach(x => tip.appendChild(x));
      tip.style.display = 'block';
      const tx = (X(near) / W) * r.width;
      tip.style.left = Math.min(tx + 14, r.width - tip.offsetWidth - 4) + 'px';
      tip.style.top = '8px';
    });
    hit.addEventListener('mouseleave', () => { tip.style.display = 'none'; cross.style.display = 'none'; dots.forEach(d => { d.style.display = 'none'; }); });
  };
  function niceMax(v) { const p = Math.pow(10, Math.floor(Math.log10(v))); const n = v / p; return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p; }
  function niceStep(v) { if (v <= 1) return 1; const p = Math.pow(10, Math.floor(Math.log10(v))); const n = v / p; return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p; }
})(window.NL);
