/* dashboard ekrani — landing / umumiy ko'rinish (Boshqaruv paneli).
 * Ma'lumotlar to'plamini tanlaydi, KPI plitalar, 2x2 mini-grafiklar va
 * so'nggi ishga tushirishlarni ko'rsatadi. Faqat theme tokenlaridan foydalanadi.
 */

/* ---- kichik yordamchilar ---- */
function dashEl(tag, cls, txt) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (txt != null) e.textContent = txt;
  return e;
}

function dashRelTime(ts) {
  if (!ts) return '';
  const d = Math.max(0, Math.floor(Date.now() / 1000 - Number(ts)));
  if (d < 45) return 'hozirgina';
  if (d < 3600) return Math.floor(d / 60) + ' daqiqa oldin';
  if (d < 86400) return Math.floor(d / 3600) + ' soat oldin';
  return Math.floor(d / 86400) + ' kun oldin';
}

function dashRunBadge(state) {
  const s = String(state || '').toLowerCase();
  if (s === 'done') return { cls: 'ok', label: 'Tugadi' };
  if (s === 'error' || s === 'unknown') return { cls: 'danger', label: 'Xato' };
  if (s === 'running' || s === 'starting') return { cls: 'neon', label: 'Ishlamoqda' };
  return { cls: 'warn', label: state || '—' };
}

function dashEmpty(container, msg, sub) {
  container.innerHTML = '';
  const box = dashEl('div', 'empty');
  box.innerHTML =
    '<svg class="empty-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6">' +
    '<path d="M4 7h16M4 12h16M4 17h10"/></svg>';
  box.appendChild(dashEl('div', 'empty-msg', msg));
  if (sub) box.appendChild(dashEl('div', 'hint', sub));
  container.appendChild(box);
}

/* module holati (ekran ichida ulashiladi) */
const DASH = { folder: null, folders: [], data: null, els: {}, root: null, booted: false };

Studio.screen('dashboard', {
  title: 'Boshqaruv paneli',
  subtitle: 'Tarmoq operatsiyalari bir qarashda',
  order: 10,
  icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">' +
        '<rect x="3" y="3" width="8" height="10" rx="2"/><rect x="13" y="3" width="8" height="6" rx="2"/>' +
        '<rect x="13" y="11" width="8" height="10" rx="2"/><rect x="3" y="15" width="8" height="6" rx="2"/></svg>',

  render(root) {
    DASH.root = root;
    root.innerHTML = `
      <div class="dash">
        <header class="dash-hero card">
          <div class="hero-glow" aria-hidden="true"></div>
          <div class="hero-copy">
            <div class="hero-eyebrow">Network Studio · Nazorat markazi</div>
            <h2 class="hero-title">Tarmoq operatsiyalari bir qarashda</h2>
            <p class="hero-sub" data-ref="heroSub">Ma'lumotlar to'plami yuklanmoqda…</p>
            <div class="hero-meta" data-ref="heroMeta"></div>
          </div>
          <div class="hero-ctrl">
            <div class="field">
              <label>Ma'lumotlar to'plami</label>
              <select class="select" data-ref="folderSel"></select>
            </div>
            <button class="btn ghost sm" data-ref="refreshBtn">
              <span class="ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
                <path d="M20 11a8 8 0 1 0-1 5"/><path d="M20 4v5h-5"/></svg></span>
              Yangilash
            </button>
          </div>
        </header>

        <div class="kpi-row" data-ref="kpi"></div>

        <div class="charts-grid">
          <section class="card hover chart-card">
            <div class="chart-head">
              <div><div class="card-title">Routing bo'yicha o'rtacha RTT</div>
                <div class="card-sub">Yo'l izlaridagi haqiqiy RTT (ms)</div></div>
              <span class="badge neon">ms</span>
            </div>
            <div class="chart-body" data-ref="cRouting"></div>
          </section>

          <section class="card hover chart-card">
            <div class="chart-head">
              <div><div class="card-title">Anomaliya soatlik taqsimoti</div>
                <div class="card-sub">Sutkalik hodisalar zichligi</div></div>
              <span class="badge">24 soat</span>
            </div>
            <div class="chart-body" data-ref="cAnomaly"></div>
          </section>

          <section class="card hover chart-card">
            <div class="chart-head">
              <div><div class="card-title">DNS kesh nisbati</div>
                <div class="card-sub">So'rovlar keshdan yechildimi</div></div>
            </div>
            <div class="chart-body chart-split" data-ref="cDns"></div>
          </section>

          <section class="card hover chart-card">
            <div class="chart-head">
              <div><div class="card-title">QoS bandlari</div>
                <div class="card-sub">Navbat bandlari bo'yicha trafik</div></div>
            </div>
            <div class="chart-body" data-ref="cQos"></div>
          </section>
        </div>

        <section class="card runs-card">
          <div class="chart-head">
            <div><div class="card-title">So'nggi ishga tushirishlar</div>
              <div class="card-sub">Oxirgi simulyatsiyalar holati</div></div>
            <button class="btn ghost sm" data-ref="toControl">Simulyatsiya →</button>
          </div>
          <div class="runs-body" data-ref="runs"></div>
        </section>
      </div>`;

    const q = (r) => root.querySelector(`[data-ref="${r}"]`);
    DASH.els = {
      heroSub: q('heroSub'), heroMeta: q('heroMeta'), folderSel: q('folderSel'),
      refreshBtn: q('refreshBtn'), kpi: q('kpi'), runs: q('runs'),
      cRouting: q('cRouting'), cAnomaly: q('cAnomaly'), cDns: q('cDns'), cQos: q('cQos'),
      toControl: q('toControl'),
    };

    // theme almashganda grafiklarni qayta chizish
    new MutationObserver(() => {
      const vis = document.getElementById('screen-root');
      if (DASH.data && vis && vis.contains(root)) requestAnimationFrame(dashDrawCharts);
    }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
  },

  async onShow(ctx) {
    const els = DASH.els;
    els.refreshBtn.onclick = () => dashLoadAll(ctx, true);
    els.toControl.onclick = () => ctx.show('control');
    els.folderSel.onchange = () => {
      DASH.folder = els.folderSel.value;
      window.Studio._folder = DASH.folder;
      dashLoadAll(ctx, false);
    };

    // papkalarni bir marta yuklaymiz
    if (!DASH.booted) {
      DASH.booted = true;
      dashSkeleton();
      let folders = [];
      try { folders = (await ctx.api.dataset_folders()) || []; }
      catch (e) { folders = []; }
      DASH.folders = folders;

      if (!folders.length) {
        dashNoFolders();
        return;
      }
      els.folderSel.innerHTML = '';
      folders.forEach((f) => {
        const o = dashEl('option', null, f.label + (f.has_datasets ? '' : '  (bo\'sh)'));
        o.value = f.path;
        els.folderSel.appendChild(o);
      });
      // standart: 'combined', yoki tashqi tanlov, aks holda birinchisi
      const combined = folders.find((f) => (f.label || '').toLowerCase() === 'combined');
      const shared = window.Studio._folder && folders.find((f) => f.path === window.Studio._folder);
      DASH.folder = (shared && shared.path) || (combined && combined.path) || folders[0].path;
      els.folderSel.value = DASH.folder;
      window.Studio._folder = DASH.folder;
    }

    if (DASH.folders.length) dashLoadAll(ctx, false);
  },
});

/* ---- yuklash / render ---- */
function dashSkeleton() {
  const kpi = DASH.els.kpi;
  kpi.innerHTML = '';
  for (let i = 0; i < 5; i++) {
    const t = dashEl('div', 'tile skel');
    t.innerHTML = '<div class="tile-label">&nbsp;</div><div class="tile-value">&nbsp;</div><div class="tile-hint">&nbsp;</div>';
    kpi.appendChild(t);
  }
}

function dashNoFolders() {
  DASH.els.heroSub.textContent = 'Hali ma\'lumotlar to\'plami yo\'q. Simulyatsiyani ishga tushiring va natijalarni import qiling.';
  DASH.els.folderSel.innerHTML = '<option>—</option>';
  DASH.els.folderSel.disabled = true;
  DASH.els.heroMeta.innerHTML = '';
  dashEmpty(DASH.els.kpi, 'Dataset topilmadi',
    'results/ jildida CSV natijalar paydo bo\'lgach, panel avtomatik to\'ladi.');
  ['cRouting', 'cAnomaly', 'cDns', 'cQos'].forEach((k) =>
    dashEmpty(DASH.els[k], 'Ma\'lumot yo\'q'));
  dashEmpty(DASH.els.runs, 'Hali ishga tushirilmagan',
    'Simulyatsiya bo\'limiga o\'ting.');
}

async function dashLoadAll(ctx, isManual) {
  const folder = DASH.folder;
  if (!folder) return;
  const btn = DASH.els.refreshBtn;
  btn.disabled = true;
  if (!DASH.data) dashSkeleton();

  try {
    const [sum, rc, anom, dns, qos] = await Promise.all([
      ctx.api.dashboard_summary(folder),
      ctx.api.routing_compare(folder),
      ctx.api.anomaly_summary(folder),
      ctx.api.dns_summary(folder),
      ctx.api.qos_summary(folder),
    ]);
    DASH.data = {
      sum: sum || {},
      rc: rc || {},
      anom: anom || {},
      dns: dns || {},
      qos: qos || {},
    };
    dashRenderHero();
    dashRenderTiles();
    dashRenderRuns(ctx);
    requestAnimationFrame(dashDrawCharts);
    if (isManual) ctx.toast('Panel yangilandi', 'success');
  } catch (e) {
    ctx.toast('Yuklashda xato: ' + (e && e.message ? e.message : e), 'danger');
    if (!DASH.data) {
      dashEmpty(DASH.els.kpi, 'Ma\'lumotni yuklab bo\'lmadi', String(e && e.message || e));
    }
  } finally {
    btn.disabled = false;
  }
}

function dashRenderHero() {
  const tiles = DASH.data.sum.tiles || [];
  const label = (DASH.folders.find((f) => f.path === DASH.folder) || {}).label || DASH.folder;
  const findVal = (frag) => {
    const t = tiles.find((x) => (x.label || '').toLowerCase().includes(frag));
    return t ? t.value : null;
  };
  const rows = findVal('yozuv');
  const modes = findVal('routing');
  let sub = '“' + label + '” to\'plami tahlilga tayyor.';
  if (rows) sub = 'Jami ' + rows + ' yozuv';
  if (rows && modes) sub += ' · ' + modes + ' routing rejimi';
  sub += ' · “' + label + '”';
  DASH.els.heroSub.textContent = sub;

  const meta = DASH.els.heroMeta;
  meta.innerHTML = '';
  const chip = (t) => { const c = dashEl('span', 'meta-chip', t); meta.appendChild(c); };
  chip('Yangilandi: ' + new Date().toLocaleTimeString('uz', { hour: '2-digit', minute: '2-digit' }));
  chip(DASH.folders.length + ' ta to\'plam');
}

function dashRenderTiles() {
  const kpi = DASH.els.kpi;
  const tiles = DASH.data.sum.tiles || [];
  kpi.innerHTML = '';
  if (!tiles.length) {
    dashEmpty(kpi, 'Ko\'rsatkichlar yo\'q', 'Bu to\'plamda hisoblanadigan yozuvlar topilmadi.');
    return;
  }
  tiles.forEach((t) => {
    const el = dashEl('div', 'tile');
    el.dataset.accent = t.accent || 'accent';
    el.appendChild(dashEl('div', 'tile-label', t.label || ''));
    el.appendChild(dashEl('div', 'tile-value', t.value == null ? '—' : String(t.value)));
    el.appendChild(dashEl('div', 'tile-hint', t.hint || ''));
    kpi.appendChild(el);
  });
}

function dashRenderRuns(ctx) {
  const wrap = DASH.els.runs;
  const runs = DASH.data.sum.recent_runs || [];
  wrap.innerHTML = '';
  if (!runs.length) {
    const box = dashEl('div', 'empty');
    box.innerHTML =
      '<svg class="empty-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">' +
      '<circle cx="12" cy="12" r="9"/><path d="M10 8l6 4-6 4z"/></svg>';
    box.appendChild(dashEl('div', 'empty-msg', 'Hali ishga tushirilmagan'));
    box.appendChild(dashEl('div', 'hint', 'Simulyatsiya bo\'limiga o\'ting.'));
    const b = dashEl('button', 'btn neon sm', 'Simulyatsiyani ochish');
    b.onclick = () => ctx.show('control');
    box.appendChild(b);
    wrap.appendChild(box);
    return;
  }
  const list = dashEl('div', 'run-list');
  runs.forEach((r) => {
    const bd = dashRunBadge(r.state);
    const row = dashEl('div', 'run-row');
    row.innerHTML =
      `<span class="run-dot ${bd.cls}"></span>` +
      `<div class="run-main"><div class="run-topo">${dashSafe(r.topology || '—')}</div>` +
      `<div class="run-route mono">${dashSafe(r.routing || '')}</div></div>` +
      `<span class="badge ${bd.cls}">${bd.label}</span>` +
      `<span class="run-time hint">${dashRelTime(r.ts)}</span>`;
    list.appendChild(row);
  });
  wrap.appendChild(list);
}

function dashSafe(s) {
  return String(s).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

/* ---- grafiklar ---- */
function dashChartInto(container, hasData, drawFn) {
  container.innerHTML = '';
  if (!hasData) { dashEmpty(container, 'Ma\'lumot yo\'q'); return; }
  const box = dashEl('div', 'chart-box');
  const cv = dashEl('canvas', 'chart');
  box.appendChild(cv);
  container.appendChild(box);
  drawFn(cv);
}

function dashDrawCharts() {
  const d = DASH.data;
  if (!d) return;
  const accent = getComputedStyle(document.documentElement).getPropertyValue('--accent-2').trim() || '#9B7CFF';
  const success = getComputedStyle(document.documentElement).getPropertyValue('--success').trim() || '#37E39B';
  const warn = getComputedStyle(document.documentElement).getPropertyValue('--warn').trim() || '#FFC24B';

  // 1) Routing RTT
  const modes = d.rc.modes || [];
  const avg = d.rc.avg_rtt || [];
  dashChartInto(DASH.els.cRouting, modes.length > 0, (cv) =>
    Charts.bars(cv, { labels: modes, values: avg, horizontal: true, unit: ' ms' }));

  // 2) Anomaliya soatlik
  const hourly = d.anom.hourly || new Array(24).fill(0);
  dashChartInto(DASH.els.cAnomaly, hourly.some((v) => v > 0), (cv) =>
    Charts.hourly(cv, hourly, { color: accent }));

  // 3) DNS kesh donut + legend
  const cache = d.dns.cache || { hit: 0, miss: 0 };
  const hasDns = (cache.hit || 0) + (cache.miss || 0) > 0;
  DASH.els.cDns.innerHTML = '';
  if (!hasDns) {
    dashEmpty(DASH.els.cDns, 'Ma\'lumot yo\'q');
  } else {
    const box = dashEl('div', 'chart-box donut-box');
    const cv = dashEl('canvas', 'chart');
    box.appendChild(cv);
    const leg = dashEl('div', 'legend');
    const total = (cache.hit || 0) + (cache.miss || 0);
    const pct = (v) => Math.round((v / total) * 100);
    leg.innerHTML =
      `<span><i style="background:${success}"></i>Keshdan · ${pct(cache.hit || 0)}%</span>` +
      `<span><i style="background:${warn}"></i>Tashqaridan · ${pct(cache.miss || 0)}%</span>`;
    DASH.els.cDns.appendChild(box);
    DASH.els.cDns.appendChild(leg);
    Charts.donut(cv, { segments: [
      { label: 'Keshdan', value: cache.hit || 0, color: success },
      { label: 'Tashqaridan', value: cache.miss || 0, color: warn },
    ] });
  }

  // 4) QoS bandlari
  const bands = d.qos.bands || [];
  dashChartInto(DASH.els.cQos, bands.length > 0, (cv) =>
    Charts.bars(cv, {
      labels: bands.map((b) => b.band),
      values: bands.map((b) => b.count),
      horizontal: false,
    }));
}
