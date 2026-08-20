/* Simulyatsiya (control) — masofaviy WSL serverda simulyatsiyani ishga tushirish
 * va jonli kuzatish (mission-control). Backend: run_simulation / run_status /
 * stop_simulation / list_runs / import_results (studio/api.py).
 * IIFE-siz oddiy skript: barcha holat/yordamchilar bitta CTRL nom-fazosida
 * (global ifloslanishning oldini olish uchun), pastda Studio.screen(...) chaqiriladi. */

var CTRL = {
  ctx: null,
  els: {},
  fields: {},
  timer: null,       // polling intervali
  runId: null,       // faol run
  topo: '', routing: '',
  done: false,
  optionsReady: false,

  ICO: {
    run:    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M8 5.5l11 6.5-11 6.5z" fill="currentColor" stroke-linejoin="round"/></svg>',
    stop:   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="6.5" y="6.5" width="11" height="11" rx="2.5"/></svg>',
    import: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3.5v11m0 0l-4-4m4 4l4-4"/><path d="M4.5 16.5V19a1.5 1.5 0 001.5 1.5h12a1.5 1.5 0 001.5-1.5v-2.5"/></svg>',
    chart:  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20V13M9.5 20V7M15 20v-5M20.5 20V4"/></svg>',
    server: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01" stroke-linecap="round"/></svg>',
    radar:  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4.5"/><path d="M12 12l7-4" stroke-linecap="round"/><circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none"/></svg>',
  },

  /* ---- Yordamchilar ---- */
  relTime: function (ts) {
    if (!ts) return '';
    var d = Date.now() / 1000 - Number(ts);
    if (d < 0) d = 0;
    if (d < 45) return 'hozir';
    if (d < 90) return '1 daqiqa oldin';
    if (d < 3600) return Math.round(d / 60) + ' daqiqa oldin';
    if (d < 5400) return '1 soat oldin';
    if (d < 86400) return Math.round(d / 3600) + ' soat oldin';
    if (d < 172800) return 'kecha';
    return Math.round(d / 86400) + ' kun oldin';
  },

  stateMeta: function (s) {
    switch (s) {
      case 'running':  return { cls: 'neon', label: 'Ishlamoqda', live: true };
      case 'starting': return { cls: 'warn', label: 'Boshlanmoqda', live: true };
      case 'done':     return { cls: 'ok',   label: 'Tugadi', live: false };
      case 'stopped':  return { cls: 'warn', label: "To'xtatilgan", live: false };
      case 'error':    return { cls: 'danger', label: 'Xato', live: false };
      default:         return { cls: 'danger', label: s || "Noma'lum", live: false };
    }
  },

  optGroup: function (label, items) {
    var g = document.createElement('optgroup');
    g.label = label;
    items.forEach(function (name) { g.appendChild(new Option(name, name)); });
    return g;
  },

  /* ---- DOM qurish ---- */
  build: function (root, ctx) {
    CTRL.ctx = ctx;
    var I = CTRL.ICO;
    root.innerHTML =
      '<div class="ctrl-wrap">' +
        '<div class="ctrl-top">' +

          /* Sozlash kartasi */
          '<div class="card ctrl-config">' +
            '<div class="ctrl-eyebrow">Mission control</div>' +
            '<div class="card-title">Simulyatsiyani sozlash</div>' +
            '<div class="card-sub">Parametrlarni tanlang va masofaviy WSL serverda ishga tushiring.</div>' +
            '<div class="grid cols-2 ctrl-fields">' +
              '<div class="field"><label>Topologiya</label><select class="select" data-f="topology"></select></div>' +
              '<div class="field"><label>Routing rejimi</label><select class="select" data-f="routing"></select></div>' +
              '<div class="field"><label>Davomiylik (soniya)</label><input class="input" type="number" min="10" step="10" data-f="duration"></div>' +
              '<div class="field"><label>Seed</label><input class="input" type="number" min="0" step="1" data-f="seed"></div>' +
            '</div>' +
            '<div class="ctrl-server">' +
              '<span class="ctrl-srv-label">' + I.server + '<span class="ctrl-srv-host">Tekshirilmoqda...</span></span>' +
              '<span class="badge" data-el="srvBadge">—</span>' +
            '</div>' +
            '<button class="btn primary launch-btn" data-el="launch"><span class="ico">' + I.run + '</span>Ishga tushirish</button>' +
            '<div class="ctrl-nohost hint" data-el="nohost" hidden>Server manzili kiritilmagan. <a data-go="settings">Sozlamalarda</a> SSH hostni qo\'shing.</div>' +
          '</div>' +

          /* Jonli monitor */
          '<div class="card ctrl-monitor" data-el="monitor">' +
            '<div class="ctrl-idle">' +
              '<span class="ctrl-idle-ico">' + I.radar + '</span>' +
              '<h3>Faol simulyatsiya yo\'q</h3>' +
              '<p>Chapdagi parametrlarni sozlab «Ishga tushirish»ni bosing — jarayon shu yerda jonli kuzatiladi.</p>' +
            '</div>' +
            '<div class="ctrl-live">' +
              '<div class="ctrl-live-head">' +
                '<div><div class="ctrl-run-title" data-el="runTitle">—</div><div class="ctrl-run-meta" data-el="runMeta"></div></div>' +
                '<span class="badge" data-el="stateBadge">—</span>' +
              '</div>' +
              '<div class="ctrl-readout">' +
                '<span class="ctrl-pct" data-el="pct">0</span><span class="ctrl-pct-sign">%</span>' +
                '<span class="ctrl-phase"><span class="live-dot" data-el="phaseDot"></span><span class="ctrl-phase-text" data-el="phaseText">boshlanmoqda</span></span>' +
              '</div>' +
              '<div class="progress"><span data-el="bar" style="width:0%"></span></div>' +
              '<div class="console ctrl-console" data-el="console"></div>' +
              '<div class="ctrl-actions">' +
                '<button class="btn danger" data-el="stop"><span class="ico">' + I.stop + '</span>To\'xtatish</button>' +
                '<button class="btn neon" data-el="import" hidden><span class="ico">' + I.import + '</span>Natijani import qilish</button>' +
                '<button class="btn primary" data-el="open" hidden><span class="ico">' + I.chart + '</span>Analitikani ochish</button>' +
              '</div>' +
            '</div>' +
          '</div>' +
        '</div>' +

        /* Tarix */
        '<div class="card ctrl-history">' +
          '<div class="card-title">Ishga tushirishlar tarixi</div>' +
          '<div class="card-sub">Jarayondagi simulyatsiyani qayta ochish uchun ustiga bosing.</div>' +
          '<div class="ctrl-runs" data-el="runs"></div>' +
        '</div>' +
      '</div>';

    var els = CTRL.els = {};
    root.querySelectorAll('[data-el]').forEach(function (n) { els[n.dataset.el] = n; });
    CTRL.fields = {};
    root.querySelectorAll('[data-f]').forEach(function (n) { CTRL.fields[n.dataset.f] = n; });
    els.srvHost = root.querySelector('.ctrl-srv-host');

    els.launch.addEventListener('click', CTRL.launch);
    els.stop.addEventListener('click', CTRL.stopRun);
    els.import.addEventListener('click', CTRL.doImport);
    els.open.addEventListener('click', function () { CTRL.ctx.show('analytics'); });
    var goLink = root.querySelector('[data-go="settings"]');
    if (goLink) goLink.addEventListener('click', function () { CTRL.ctx.show('settings'); });
  },

  /* ---- Ma'lumot yuklash ---- */
  loadOptions: async function () {
    var api = CTRL.ctx.api;
    var r = await Promise.all([
      api.get_config().catch(function () { return null; }),
      api.list_topologies().catch(function () { return null; }),
      api.routing_modes().catch(function () { return null; }),
    ]);
    var cfg = r[0], topos = r[1], modes = r[2];

    var topoSel = CTRL.fields.topology;
    topoSel.innerHTML = '';
    var t = topos || { builtin: [], custom: [] };
    if ((t.builtin || []).length) topoSel.appendChild(CTRL.optGroup('Tayyor', t.builtin));
    if ((t.custom || []).length) topoSel.appendChild(CTRL.optGroup('Maxsus', t.custom));
    if (!topoSel.children.length) topoSel.appendChild(new Option("— topologiya yo'q —", ''));

    var rSel = CTRL.fields.routing;
    rSel.innerHTML = '';
    (modes || []).forEach(function (m) { rSel.appendChild(new Option(m, m)); });
    if (!rSel.children.length) rSel.appendChild(new Option("— rejim yo'q —", ''));

    CTRL.fields.duration.value = (cfg && cfg.default_duration) != null ? cfg.default_duration : 300;
    CTRL.fields.seed.value = (cfg && cfg.default_seed) != null ? cfg.default_seed : 42;

    CTRL.optionsReady = true;
    CTRL.applyServer(cfg);
  },

  refreshServer: async function () {
    var cfg = await CTRL.ctx.api.get_config().catch(function () { return null; });
    CTRL.applyServer(cfg);
  },

  applyServer: function (cfg) {
    var host = (cfg && cfg.ssh_host) ? String(cfg.ssh_host) : '';
    var els = CTRL.els, has = !!host;
    els.launch.disabled = !has || !!CTRL.timer; // yo'q bo'lsa yoki run ketayotgan bo'lsa
    els.nohost.hidden = has;
    els.srvHost.textContent = has ? host : 'Server ulanmagan';
    els.srvBadge.className = 'badge ' + (has ? 'neon' : 'danger');
    els.srvBadge.textContent = has ? 'Sozlangan' : 'Sozlanmagan';
  },

  loadRuns: async function () {
    var runs = await CTRL.ctx.api.list_runs().catch(function () { return null; });
    CTRL.renderRuns(Array.isArray(runs) ? runs : []);
  },

  renderRuns: function (runs) {
    var box = CTRL.els.runs;
    box.innerHTML = '';
    if (!runs.length) {
      var e = document.createElement('div');
      e.className = 'ctrl-runs-empty';
      e.textContent = "Hali ishga tushirishlar yo'q.";
      box.appendChild(e);
      return;
    }
    runs.forEach(function (r) {
      var meta = CTRL.stateMeta(r.state), live = meta.live;
      var row = document.createElement(live ? 'button' : 'div');
      row.className = 'ctrl-run' + (live ? ' is-live' : '');

      var b = document.createElement('span');
      b.className = 'badge ' + meta.cls;
      if (live) { var dt = document.createElement('span'); dt.className = 'live-dot'; b.appendChild(dt); }
      b.appendChild(document.createTextNode(meta.label));

      var main = document.createElement('span');
      main.className = 'ctrl-run-main';
      var strong = document.createElement('b');
      strong.textContent = r.topology || '—';
      var sep = document.createElement('span');
      sep.className = 'sep-dot'; sep.textContent = '·';
      main.appendChild(strong); main.appendChild(sep);
      main.appendChild(document.createTextNode(r.routing || ''));

      var time = document.createElement('span');
      time.className = 'ctrl-run-time mono';
      time.textContent = CTRL.relTime(r.ts);

      row.appendChild(b); row.appendChild(main); row.appendChild(time);

      if (live) {
        var tag = document.createElement('span');
        tag.className = 'ctrl-run-tag hint';
        tag.textContent = 'ochish';
        row.appendChild(tag);
        row.addEventListener('click', function () { CTRL.attachRun(r.run_id, r.topology, r.routing); });
      }
      box.appendChild(row);
    });
  },

  /* ---- Ishga tushirish / kuzatish ---- */
  launch: async function () {
    var f = CTRL.fields;
    var topology = f.topology.value, routing = f.routing.value;
    if (!topology || !routing) { CTRL.ctx.toast('Topologiya va routing rejimini tanlang', 'warn'); return; }
    var params = {
      topology: topology,
      routing: routing,
      duration: Math.max(10, parseInt(f.duration.value, 10) || 300),
      seed: parseInt(f.seed.value, 10) || 0,
    };

    var btn = CTRL.els.launch;
    btn.disabled = true;
    btn.innerHTML = '<span class="ico">' + CTRL.ICO.run + '</span>Ishga tushirilmoqda...';

    var res = await CTRL.ctx.api.run_simulation(params).catch(function (e) { return { ok: false, error: String(e) }; });

    btn.innerHTML = '<span class="ico">' + CTRL.ICO.run + '</span>Ishga tushirish';

    if (!res || !res.ok || !res.run_id) {
      btn.disabled = false;
      CTRL.ctx.toast((res && res.error) || "Simulyatsiyani ishga tushirib bo'lmadi", 'danger');
      return;
    }
    CTRL.ctx.toast('Simulyatsiya boshlandi', 'success');
    CTRL.attachRun(res.run_id, params.topology, params.routing);
    CTRL.loadRuns();
  },

  attachRun: function (runId, topo, routing) {
    if (!runId) return;
    CTRL.runId = runId;
    CTRL.topo = topo || '';
    CTRL.routing = routing || '';
    CTRL.done = false;

    var els = CTRL.els;
    els.monitor.classList.add('is-active');
    els.runTitle.textContent = (topo || '—') + '  ·  ' + (routing || '');
    els.runMeta.textContent = '';
    els.stop.hidden = false; els.stop.disabled = false;
    els.import.hidden = true;
    els.open.hidden = true;
    els.launch.disabled = true;
    CTRL.renderLog([]);
    CTRL.startPolling();
  },

  startPolling: function () {
    CTRL.stopPolling();
    CTRL.poll();
    CTRL.timer = setInterval(CTRL.poll, 2000);
  },
  stopPolling: function () {
    if (CTRL.timer) { clearInterval(CTRL.timer); CTRL.timer = null; }
  },

  poll: async function () {
    if (!CTRL.runId) return;
    var st = await CTRL.ctx.api.run_status(CTRL.runId).catch(function () { return null; });
    if (!st) { CTRL.els.phaseText.textContent = 'holat olinmadi'; return; }
    CTRL.renderStatus(st);
    if (st.done && !CTRL.done) {
      CTRL.done = true;
      CTRL.stopPolling();
      CTRL.handleDone(st);
    }
  },

  renderStatus: function (st) {
    var els = CTRL.els;
    var pct = Math.max(0, Math.min(100, Math.round(Number(st.progress) || 0)));
    els.bar.style.width = pct + '%';
    els.pct.textContent = String(pct);

    var topo = st.topology || CTRL.topo || '—';
    var routing = st.routing || CTRL.routing || '';
    els.runTitle.textContent = topo + '  ·  ' + routing;
    var bits = [];
    if (st.duration != null) bits.push(st.duration + 's');
    if (st.seed != null) bits.push('seed ' + st.seed);
    if (CTRL.runId) bits.push(CTRL.runId);
    els.runMeta.textContent = bits.join('   ·   ');

    var err = st.state === 'unknown' || (st.done && st.error);
    var meta = err ? { cls: 'danger', label: st.error ? 'Xato' : "Noma'lum", live: false } : CTRL.stateMeta(st.state);
    els.stateBadge.className = 'badge ' + meta.cls;
    els.stateBadge.innerHTML = '';
    if (meta.live) { var d = document.createElement('span'); d.className = 'live-dot'; els.stateBadge.appendChild(d); }
    els.stateBadge.appendChild(document.createTextNode(meta.label));

    els.phaseText.textContent = st.phase || (st.done ? 'tugadi' : 'ishlamoqda');
    els.phaseDot.className = 'live-dot' + (st.done ? ' done' : '');
    els.phaseDot.style.display = (st.done && err) ? 'none' : '';

    CTRL.renderLog(st.log_tail);
  },

  renderLog: function (lines) {
    var c = CTRL.els.console;
    lines = Array.isArray(lines) ? lines : [];
    c.innerHTML = '';
    if (!lines.length) {
      var d = document.createElement('div');
      d.className = 'ln muted';
      d.textContent = 'Log kutilmoqda...';
      c.appendChild(d);
    } else {
      lines.forEach(function (l) {
        var ln = document.createElement('div');
        ln.className = 'ln';
        ln.textContent = l;
        c.appendChild(ln);
      });
    }
    c.scrollTop = c.scrollHeight;
  },

  handleDone: function (st) {
    var els = CTRL.els;
    els.stop.hidden = true;
    CTRL.refreshServer(); // CTRL.timer endi null — launch server holatiga qarab qayta yoqiladi
    if (st.error || st.state === 'unknown') {
      CTRL.ctx.toast('Simulyatsiya xatosi: ' + (st.error || "noma'lum"), 'danger');
    } else {
      CTRL.ctx.toast('Simulyatsiya yakunlandi', 'success');
      els.import.hidden = false;
    }
    CTRL.loadRuns();
  },

  stopRun: async function () {
    if (!CTRL.runId) return;
    CTRL.els.stop.disabled = true;
    await CTRL.ctx.api.stop_simulation(CTRL.runId).catch(function () {});
    CTRL.els.stop.disabled = false;
    CTRL.ctx.toast("To'xtatish so'raldi", 'warn');
    CTRL.loadRuns(); // polling davom etadi — worker to'xtaganini keyingi statusdan ko'ramiz
  },

  doImport: async function () {
    var els = CTRL.els;
    if (!CTRL.runId) return;
    els.import.disabled = true;
    els.import.innerHTML = '<span class="ico">' + CTRL.ICO.import + '</span>Import qilinmoqda...';

    var res = await CTRL.ctx.api.import_results(CTRL.runId).catch(function (e) { return { ok: false, error: String(e) }; });

    els.import.disabled = false;
    els.import.innerHTML = '<span class="ico">' + CTRL.ICO.import + '</span>Natijani import qilish';

    if (res && res.ok) {
      CTRL.ctx.toast("Natijalar import qilindi: " + (res.folder || '') + " — Analitikada ko'ring", 'success');
      els.import.hidden = true;
      els.open.hidden = false;
    } else {
      CTRL.ctx.toast('Import xatosi: ' + ((res && res.error) || "noma'lum"), 'danger');
    }
  },
};

/* ============================================================================
   Ekran ro'yxatga olish
   ========================================================================== */
Studio.screen('control', {
  title: 'Simulyatsiya',
  subtitle: 'Masofaviy serverda simulyatsiyani ishga tushiring va kuzating',
  order: 40,
  icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"/><path d="M10 8.4l6 3.6-6 3.6z" fill="currentColor" stroke-linejoin="round"/></svg>',
  render: function (root, ctx) { CTRL.build(root, ctx); },
  onShow: function (ctx) {
    CTRL.ctx = ctx;
    if (!CTRL.optionsReady) CTRL.loadOptions();
    else CTRL.refreshServer();
    CTRL.loadRuns();
  },
});
