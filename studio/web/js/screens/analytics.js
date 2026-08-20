/* Analitika ekrani — yig'ilgan datasetlar ustidan ma'lumot tadqiqoti.
 *
 * Yuqorida: papka tanlagich + xulosa (jadval soni, yozuvlar, routing rejimlar).
 * Segment tab-bar: Routing / Anomaliya / DNS / QoS / SQL. Har tab o'z panelini
 * ko'rsatadi; grafiklar canvas o'lchamiga muhtoj bo'lgani uchun panel ko'rsatilgach
 * (rAF ichida) chiziladi va mavzu almashganda qayta chiziladi.
 * ctx = { api, toast, show, theme, screenId }; api = window.Bridge (barcha metodlar Promise).
 */
Studio.screen('analytics', {
  title: 'Analitika',
  subtitle: 'Yig‘ilgan datasetlar bo‘yicha tahlil va erkin SQL so‘rov',
  order: 30,
  icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">'
      + '<path d="M3 3v16a2 2 0 0 0 2 2h16" stroke-linecap="round"/>'
      + '<rect x="7" y="11" width="3" height="6" rx="1"/>'
      + '<rect x="12" y="7" width="3" height="10" rx="1"/>'
      + '<rect x="17" y="4" width="3" height="13" rx="1"/></svg>',

  // ---- Tab ta'riflari ----
  _TABS: [
    { id: 'routing', label: 'Routing' },
    { id: 'anomaly', label: 'Anomaliya' },
    { id: 'dns', label: 'DNS' },
    { id: 'qos', label: 'QoS' },
    { id: 'sql', label: 'SQL' },
  ],
  _SAMPLE_SQL: 'SELECT routing, avg(real_rtt_ms) AS avg_rtt\nFROM path_traces\nGROUP BY routing\nORDER BY avg_rtt',

  // =====================================================================
  //  RENDER — bir marta: DOM skeletini quradi, hodisalarni ulaydi
  // =====================================================================
  render(root, ctx) {
    const self = this;
    this._s = { folders: [], folder: null, tab: 'routing', tables: [], modes: [], cache: {} };

    root.innerHTML = `
      <div class="an-wrap">
        <div class="card an-head">
          <div class="an-picker">
            <div class="field">
              <label><span class="an-live"></span>Dataset papkasi</label>
              <select class="select" data-el="folder"></select>
            </div>
          </div>
          <div class="an-summary" data-el="summary"></div>
        </div>

        <div data-el="workspace">
          <div class="an-tabbar">
            <div class="segment" data-el="tabs"></div>
            <span class="an-tabhint" data-el="tabhint"></span>
          </div>
          <div class="an-panels" data-el="panels">
            <div class="an-panel" data-panel="routing"></div>
            <div class="an-panel" data-panel="anomaly"></div>
            <div class="an-panel" data-panel="dns"></div>
            <div class="an-panel" data-panel="qos"></div>
            <div class="an-panel" data-panel="sql"></div>
          </div>
        </div>

        <div class="an-empty-global" data-el="emptyGlobal" style="display:none"></div>
      </div>`;

    const q = (sel) => root.querySelector(sel);
    this.el = {
      root,
      folder: q('[data-el="folder"]'),
      summary: q('[data-el="summary"]'),
      workspace: q('[data-el="workspace"]'),
      tabs: q('[data-el="tabs"]'),
      tabhint: q('[data-el="tabhint"]'),
      panels: {
        routing: q('[data-panel="routing"]'),
        anomaly: q('[data-panel="anomaly"]'),
        dns: q('[data-panel="dns"]'),
        qos: q('[data-panel="qos"]'),
        sql: q('[data-panel="sql"]'),
      },
      emptyGlobal: q('[data-el="emptyGlobal"]'),
    };

    // Tab tugmalari
    this._TABS.forEach((t) => {
      const b = document.createElement('button');
      b.dataset.tab = t.id;
      b.textContent = t.label;
      b.addEventListener('click', () => self._setTab(ctx, t.id));
      this.el.tabs.appendChild(b);
    });

    // Papka almashuvi
    this.el.folder.addEventListener('change', (e) => {
      self._s.folder = e.target.value;
      self._loadFolder(ctx);
    });

    // SQL paneli statik skeleti (ma'lumot yo'qolmasligi uchun bir marta quriladi)
    this._buildSqlPanel(ctx);

    // Mavzu almashsa — faol panelni qayta chizish (SQL bundan mustasno)
    this._themeObs = new MutationObserver(() => {
      requestAnimationFrame(() => self._redrawActive());
    });
    this._themeObs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
  },

  // =====================================================================
  //  ONSHOW — har ko'rsatilganda: papkalarni (qayta) yuklaydi
  // =====================================================================
  async onShow(ctx) {
    const S = this._s;
    let folders;
    try {
      folders = await ctx.api.dataset_folders();
    } catch (e) {
      ctx.toast('Datasetlarni o‘qib bo‘lmadi: ' + (e.message || e), 'danger');
      folders = [];
    }
    S.folders = Array.isArray(folders) ? folders : [];

    if (!S.folders.length) {
      this._showGlobalEmpty();
      return;
    }
    this.el.workspace.style.display = '';
    this.el.emptyGlobal.style.display = 'none';

    // Select variantlari
    this.el.folder.innerHTML = S.folders
      .map((f) => `<option value="${this._attr(f.path)}">${this._esc(f.label)}</option>`)
      .join('');

    // Joriy tanlovni saqlab qolish (yoki birinchisi)
    if (!S.folder || !S.folders.some((f) => f.path === S.folder)) {
      S.folder = S.folders[0].path;
    }
    this.el.folder.value = S.folder;

    // Faol tabni segmentda belgilash
    this._syncTabButtons();
    await this._loadFolder(ctx);
  },

  _showGlobalEmpty() {
    this._s.folder = null;
    this.el.workspace.style.display = 'none';
    this.el.summary.innerHTML = '<span class="an-faint">Ko‘rsatiladigan dataset yo‘q</span>';
    this.el.folder.innerHTML = '<option>—</option>';
    this.el.folder.disabled = true;
    this.el.emptyGlobal.style.display = '';
    this.el.emptyGlobal.innerHTML = this._emptyHTML(
      'Hali dataset yo‘q',
      'Simulyatsiyani ishga tushiring va natijalarni import qiling — bu yerda tahlil paydo bo‘ladi.',
      `<button class="btn neon" data-el="goControl">Simulyatsiyaga o‘tish</button>`
    );
    const b = this.el.emptyGlobal.querySelector('[data-el="goControl"]');
    if (b) b.addEventListener('click', () => { try { window.Studio.show('control'); } catch (e) {} });
  },

  // =====================================================================
  //  Papka ma'lumotini yuklash: xulosa + jadval chiplari + kesh tozalash
  // =====================================================================
  async _loadFolder(ctx) {
    const S = this._s;
    this.el.folder.disabled = false;
    S.cache = {};                       // yangi papka — grafik keshini tozalash
    this.el.summary.innerHTML = '<span class="an-faint">Yuklanmoqda…</span>';

    let info;
    try {
      info = await ctx.api.list_datasets(S.folder);
    } catch (e) {
      this.el.summary.innerHTML = `<span class="an-err">${this._esc(e.message || String(e))}</span>`;
      info = { tables: [], routing_modes: [] };
    }
    S.tables = info.tables || [];
    S.modes = info.routing_modes || [];

    // Xulosa satri
    const totalRows = S.tables.reduce((a, t) => a + (t.rows || 0), 0);
    const errKeys = info.errors ? Object.keys(info.errors) : [];
    const modesHtml = S.modes.length
      ? `<span class="an-modes">${S.modes.map((m) => `<span class="badge neon">${this._esc(m)}</span>`).join('')}</span>`
      : '<span class="an-faint">routing rejimi aniqlanmadi</span>';
    this.el.summary.innerHTML =
      `<span class="an-sum"><b>${S.tables.length}</b> jadval</span>` +
      `<span class="an-dot"></span>` +
      `<span class="an-sum"><b>${this._fmt(totalRows)}</b> yozuv</span>` +
      `<span class="an-dot"></span>` +
      modesHtml +
      (errKeys.length ? `<span class="an-dot"></span><span class="badge warn">${errKeys.length} ogohlantirish</span>` : '');

    // SQL panel: jadval chiplari
    this._renderChips();

    // Faol panelni yangilash
    this._syncTabButtons();
    this._togglePanels();
    await this._ensurePanel(ctx, S.tab);
  },

  // =====================================================================
  //  Tab boshqaruvi
  // =====================================================================
  _setTab(ctx, tab) {
    if (this._s.tab === tab && this.el.panels[tab].childElementCount) {
      // faqat SQL uchun — qayta bosilsa fokus
      if (tab === 'sql' && this.el.sqlInput) this.el.sqlInput.focus();
      return;
    }
    this._s.tab = tab;
    this._syncTabButtons();
    this._togglePanels();
    this._ensurePanel(ctx, tab);
  },

  _syncTabButtons() {
    const tab = this._s.tab;
    this.el.tabs.querySelectorAll('button').forEach((b) =>
      b.classList.toggle('active', b.dataset.tab === tab));
    const hints = {
      routing: '11 routing rejimini kechikish bo‘yicha taqqoslash',
      anomaly: 'Aniqlangan hujum turlari va soatlik taqsimot',
      dns: 'Nom yechish bosqichlari va kesh samaradorligi',
      qos: 'Navbat sinflari va tarmoq buzilish hodisalari',
      sql: 'Cmd/Ctrl + Enter bilan so‘rovni ishga tushiring',
    };
    this.el.tabhint.textContent = hints[tab] || '';
  },

  _togglePanels() {
    const tab = this._s.tab;
    Object.keys(this.el.panels).forEach((k) => {
      this.el.panels[k].style.display = (k === tab) ? '' : 'none';
    });
  },

  // Kerak bo'lsa ma'lumotni yuklab, panelni chizadi (papka bo'yicha keshlanadi)
  async _ensurePanel(ctx, tab) {
    if (tab === 'sql') return;          // SQL ma'lumoti chiplarda tayyor
    const S = this._s;
    const panel = this.el.panels[tab];
    if (!S.cache[tab]) {
      panel.innerHTML = this._loadingHTML();
      const map = {
        routing: () => ctx.api.routing_compare(S.folder),
        anomaly: () => ctx.api.anomaly_summary(S.folder),
        dns: () => ctx.api.dns_summary(S.folder),
        qos: () => ctx.api.qos_summary(S.folder),
      };
      try {
        S.cache[tab] = (await map[tab]()) || {};
      } catch (e) {
        panel.innerHTML = this._emptyHTML('Yuklab bo‘lmadi', e.message || String(e));
        return;
      }
      // Foydalanuvchi bu orada boshqa tabga o'tgan bo'lishi mumkin
      if (S.tab !== tab) return;
    }
    this._drawPanel(tab);
  },

  _drawPanel(tab) {
    const data = this._s.cache[tab];
    if (!data) return;
    if (tab === 'routing') this._renderRouting(data);
    else if (tab === 'anomaly') this._renderAnomaly(data);
    else if (tab === 'dns') this._renderDns(data);
    else if (tab === 'qos') this._renderQos(data);
  },

  _redrawActive() {
    const tab = this._s.tab;
    if (tab === 'sql') return;          // SQL — foydalanuvchi kiritmasini saqlaymiz
    if (this._s.cache[tab]) this._drawPanel(tab);
  },

  // =====================================================================
  //  ROUTING paneli
  // =====================================================================
  _renderRouting(data) {
    const panel = this.el.panels.routing;
    if (!data.modes || !data.modes.length) {
      panel.innerHTML = this._emptyHTML('path_traces topilmadi',
        'Bu datasetda yo‘l izlari yozuvi yo‘q, shuning uchun RTT taqqoslash mavjud emas.');
      return;
    }
    panel.innerHTML = `
      <div class="an-two">
        <div class="card">
          <div class="card-title">O‘rtacha RTT — routing rejimi bo‘yicha</div>
          <div class="card-sub">Har rejim uchun o‘lchangan aylanma kechikish (ms)</div>
          <div class="an-cbox tall"><canvas class="chart" data-c="avg"></canvas></div>
        </div>
        <div class="card">
          <div class="card-title">RTT taqsimoti</div>
          <div class="card-sub">Box-plot: median, kvartillar va tarqoqlik</div>
          <div class="an-cbox tall"><canvas class="chart" data-c="box"></canvas></div>
        </div>
      </div>
      <div class="an-cap">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16h.01" stroke-linecap="round"/></svg>
        <span>Ikkala grafik ham 11 routing rejimini yonma-yon taqqoslaydi: chapda o‘rtacha kechikish, o‘ngda esa har rejimning barqarorligi (quti qanchalik past va tor bo‘lsa, shuncha yaxshi).</span>
      </div>`;
    const avg = panel.querySelector('[data-c="avg"]');
    const box = panel.querySelector('[data-c="box"]');
    this._raf(() => {
      window.Charts.bars(avg, { labels: data.modes, values: data.avg_rtt, horizontal: true, unit: ' ms' });
      window.Charts.box(box, { labels: data.modes, series: data.modes.map((m) => data.box[m] || []) });
    });
  },

  // =====================================================================
  //  ANOMALIYA paneli
  // =====================================================================
  _renderAnomaly(data) {
    const panel = this.el.panels.anomaly;
    const types = data.types || [];
    const hourly = data.hourly || new Array(24).fill(0);
    const hasHourly = hourly.some((v) => v > 0);
    if (!types.length && !hasHourly) {
      panel.innerHTML = this._emptyHTML('anomaly_events topilmadi',
        'Bu datasetda hujum hodisalari qayd etilmagan.');
      return;
    }
    panel.innerHTML = `
      <div class="an-two">
        <div class="card">
          <div class="card-title">Hujum turlari</div>
          <div class="card-sub">Hodisalar soni bo‘yicha (eng ko‘pi tepada)</div>
          <div class="an-cbox tall"><canvas class="chart" data-c="types"></canvas></div>
        </div>
        <div class="card">
          <div class="card-title">Soatlik taqsimot</div>
          <div class="card-sub">Simulyatsiya soati (0–23) bo‘yicha hodisalar</div>
          <div class="an-cbox tall"><canvas class="chart" data-c="hourly"></canvas></div>
          <div class="an-cap"><span>Kunning qaysi soatlarida hujumlar zichlashganini ko‘rsatadi.</span></div>
        </div>
      </div>`;
    const cT = panel.querySelector('[data-c="types"]');
    const cH = panel.querySelector('[data-c="hourly"]');
    this._raf(() => {
      if (types.length) {
        window.Charts.bars(cT, {
          labels: types.map((t) => t.type),
          values: types.map((t) => t.count),
          horizontal: true,
        });
      } else {
        this._canvasEmpty(cT, 'Tur ma‘lumoti yo‘q');
      }
      window.Charts.hourly(cH, hourly);
    });
  },

  // =====================================================================
  //  DNS paneli
  // =====================================================================
  _renderDns(data) {
    const panel = this.el.panels.dns;
    const stages = data.stages || [];
    const cache = data.cache || { hit: 0, miss: 0 };
    const hasCache = (cache.hit + cache.miss) > 0;
    if (!stages.length && !hasCache) {
      panel.innerHTML = this._emptyHTML('dns_queries topilmadi',
        'Bu datasetda DNS so‘rovlari yozuvi mavjud emas.');
      return;
    }
    const cP50 = this._cvar('--accent-2');
    const cP90 = this._cvar('--accent');
    panel.innerHTML = `
      <div class="an-two">
        <div class="card">
          <div class="card-title">Bosqich kechikishi</div>
          <div class="card-sub">cache → root → tld → authoritative (ms)</div>
          <div class="an-mini-label"><i style="background:${cP50}"></i>p50 · median</div>
          <div class="an-cbox sm"><canvas class="chart" data-c="p50"></canvas></div>
          <div class="an-mini-label"><i style="background:${cP90}"></i>p90 · tail</div>
          <div class="an-cbox sm"><canvas class="chart" data-c="p90"></canvas></div>
        </div>
        <div class="card">
          <div class="card-title">Cache hit / miss</div>
          <div class="card-sub">DNS keshi qanchalik samarali ishlagani</div>
          <div class="an-cbox tall"><canvas class="chart" data-c="cache"></canvas></div>
          <div class="legend" data-c="legend"></div>
        </div>
      </div>`;
    const p50 = panel.querySelector('[data-c="p50"]');
    const p90 = panel.querySelector('[data-c="p90"]');
    const cCache = panel.querySelector('[data-c="cache"]');
    const legend = panel.querySelector('[data-c="legend"]');
    const cHit = this._cvar('--success');
    const cMiss = this._cvar('--danger');
    this._raf(() => {
      if (stages.length) {
        const labels = stages.map((s) => s.stage);
        window.Charts.bars(p50, { labels, values: stages.map((s) => s.p50), horizontal: true, unit: ' ms', colors: [cP50] });
        window.Charts.bars(p90, { labels, values: stages.map((s) => s.p90), horizontal: true, unit: ' ms', colors: [cP90] });
      } else {
        this._canvasEmpty(p50, 'Bosqich ma‘lumoti yo‘q');
        this._canvasEmpty(p90, '');
      }
      if (hasCache) {
        window.Charts.donut(cCache, { segments: [
          { label: 'hit', value: cache.hit, color: cHit },
          { label: 'miss', value: cache.miss, color: cMiss },
        ] });
        const tot = cache.hit + cache.miss;
        const pct = tot ? Math.round(cache.hit / tot * 100) : 0;
        legend.innerHTML =
          `<span><i style="background:${cHit}"></i>hit — ${this._fmt(cache.hit)} (${pct}%)</span>` +
          `<span><i style="background:${cMiss}"></i>miss — ${this._fmt(cache.miss)}</span>`;
      } else {
        this._canvasEmpty(cCache, 'Kesh ma‘lumoti yo‘q');
        legend.innerHTML = '';
      }
    });
  },

  // =====================================================================
  //  QoS paneli
  // =====================================================================
  _renderQos(data) {
    const panel = this.el.panels.qos;
    const bands = data.bands || [];
    const imps = data.impairments || [];
    if (!bands.length && !imps.length) {
      panel.innerHTML = this._emptyHTML('QoS ma‘lumoti topilmadi',
        'Bu datasetda navbat sinflari yoki buzilish hodisalari yozuvi yo‘q.');
      return;
    }
    panel.innerHTML = `
      <div class="an-two">
        <div class="card">
          <div class="card-title">Trafik navbat sinflari</div>
          <div class="card-sub">DiffServ band bo‘yicha paketlar ulushi</div>
          <div class="an-cbox tall"><canvas class="chart" data-c="bands"></canvas></div>
          <div class="legend" data-c="blegend"></div>
        </div>
        <div class="card">
          <div class="card-title">Buzilish hodisalari</div>
          <div class="card-sub">Kechikish/yo‘qotish kabi impairment hodisalari soni</div>
          <div class="an-cbox tall"><canvas class="chart" data-c="imps"></canvas></div>
        </div>
      </div>`;
    const cB = panel.querySelector('[data-c="bands"]');
    const cI = panel.querySelector('[data-c="imps"]');
    const blegend = panel.querySelector('[data-c="blegend"]');
    const pal = [this._cvar('--accent'), this._cvar('--accent-2'), this._cvar('--accent-3'), this._cvar('--success'), this._cvar('--warn')];
    this._raf(() => {
      if (bands.length) {
        window.Charts.donut(cB, { segments: bands.map((b, i) => ({ label: b.band, value: b.count, color: pal[i % pal.length] })) });
        blegend.innerHTML = bands.map((b, i) =>
          `<span><i style="background:${pal[i % pal.length]}"></i>${this._esc(b.band)} — ${this._fmt(b.count)}</span>`).join('');
      } else {
        this._canvasEmpty(cB, 'Navbat ma‘lumoti yo‘q');
        blegend.innerHTML = '';
      }
      if (imps.length) {
        window.Charts.bars(cI, { labels: imps.map((e) => e.event), values: imps.map((e) => e.count), horizontal: true });
      } else {
        this._canvasEmpty(cI, 'Buzilish hodisasi yo‘q');
      }
    });
  },

  // =====================================================================
  //  SQL paneli (imzo elementi) — statik skelet + dinamik natija
  // =====================================================================
  _buildSqlPanel(ctx) {
    const self = this;
    const panel = this.el.panels.sql;
    panel.innerHTML = `
      <div class="card an-sql">
        <div>
          <div class="card-title">Erkin SQL so‘rov</div>
          <div class="card-sub">DuckDB sintaksisi. Jadvallar CSV fayllardan view sifatida ochilgan.</div>
          <div class="an-sql-editor">
            <textarea class="input mono" data-el="sqlInput" spellcheck="false" autocapitalize="off" autocomplete="off"></textarea>
            <span class="an-kbd">⌘ / Ctrl + ↵</span>
          </div>
        </div>
        <div>
          <div class="an-chips-head">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M9 9v11" /></svg>
            Jadval nomini so‘rovga qo‘shish uchun bosing
          </div>
          <div class="an-chips" data-el="sqlChips"></div>
        </div>
        <div class="an-run-row">
          <button class="btn primary" data-el="runBtn">
            <span class="ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><path d="M6 4l14 8-14 8z" stroke-linejoin="round"/></svg></span>
            Ishga tushirish
          </button>
          <div class="an-status" data-el="sqlStatus"></div>
        </div>
        <div data-el="sqlResults"></div>
      </div>`;

    this.el.sqlInput = panel.querySelector('[data-el="sqlInput"]');
    this.el.sqlChips = panel.querySelector('[data-el="sqlChips"]');
    this.el.runBtn = panel.querySelector('[data-el="runBtn"]');
    this.el.sqlStatus = panel.querySelector('[data-el="sqlStatus"]');
    this.el.sqlResults = panel.querySelector('[data-el="sqlResults"]');

    this.el.sqlInput.value = this._SAMPLE_SQL;
    this.el.sqlResults.innerHTML = this._emptyHTML('Natija bu yerda ko‘rinadi',
      'So‘rov yozing yoki namunani ishga tushiring.');

    this.el.runBtn.addEventListener('click', () => self._runQuery(ctx));
    this.el.sqlInput.addEventListener('keydown', (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        e.preventDefault();
        self._runQuery(ctx);
      }
    });
  },

  _renderChips() {
    const chips = this.el.sqlChips;
    if (!chips) return;
    if (!this._s.tables.length) {
      chips.innerHTML = '<span class="an-faint">Jadval yo‘q</span>';
      return;
    }
    const self = this;
    chips.innerHTML = '';
    this._s.tables.forEach((t) => {
      const b = document.createElement('button');
      b.className = 'badge an-chip';
      b.type = 'button';
      b.title = `${self._fmt(t.rows || 0)} yozuv`;
      b.innerHTML = `${self._esc(t.name)}<span class="an-chip-n">${self._fmt(t.rows || 0)}</span>`;
      b.addEventListener('click', () => self._insertAtCursor(t.name));
      chips.appendChild(b);
    });
  },

  _insertAtCursor(text) {
    const ta = this.el.sqlInput;
    if (!ta) return;
    const s = ta.selectionStart != null ? ta.selectionStart : ta.value.length;
    const e = ta.selectionEnd != null ? ta.selectionEnd : ta.value.length;
    const before = ta.value.slice(0, s);
    // Kerak bo'lsa nom oldiga bo'sh joy qo'shamiz
    const needSpace = before.length && !/\s$/.test(before);
    const ins = (needSpace ? ' ' : '') + text;
    ta.value = before + ins + ta.value.slice(e);
    const pos = s + ins.length;
    ta.selectionStart = ta.selectionEnd = pos;
    ta.focus();
  },

  async _runQuery(ctx) {
    const S = this._s;
    if (!S.folder) return;
    const sql = (this.el.sqlInput.value || '').trim();
    if (!sql) {
      ctx.toast('So‘rov bo‘sh', 'warn');
      return;
    }
    const btn = this.el.runBtn;
    const orig = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = 'Bajarilmoqda…';
    this.el.sqlStatus.innerHTML = '';
    let res;
    try {
      res = await ctx.api.query(S.folder, sql);
    } catch (e) {
      res = { columns: [], rows: [], error: e.message || String(e) };
    } finally {
      btn.disabled = false;
      btn.innerHTML = orig;
    }
    this._renderResults(res);
  },

  _renderResults(res) {
    const wrap = this.el.sqlResults;
    const status = this.el.sqlStatus;
    if (res && res.error) {
      status.innerHTML = `<span class="badge danger">xato</span>`;
      wrap.innerHTML = this._emptyHTML('So‘rov bajarilmadi',
        `<span class="an-err">${this._esc(res.error)}</span>`);
      return;
    }
    const cols = (res && res.columns) || [];
    const rows = (res && res.rows) || [];
    if (!cols.length) {
      status.innerHTML = '<span class="an-faint">Natija yo‘q</span>';
      wrap.innerHTML = this._emptyHTML('Ustun qaytmadi', 'So‘rov hech qanday jadval qaytarmadi.');
      return;
    }
    const CAP = 500;
    const shown = rows.slice(0, CAP);
    let st = `<span class="badge ok">${this._fmt(rows.length)} qator</span>`;
    if (rows.length >= 1000) st += ` <span class="hint">backend 1000 bilan cheklangan</span>`;
    if (rows.length > CAP) st += ` <span class="hint">— ${CAP} ko‘rsatildi</span>`;
    status.innerHTML = st;

    const self = this;
    const isNum = (v) => typeof v === 'number';
    let h = '<div class="table-wrap an-tablewrap"><table class="data"><thead><tr>';
    h += cols.map((c) => `<th>${self._esc(String(c))}</th>`).join('');
    h += '</tr></thead><tbody>';
    h += shown.map((r) => '<tr>' + r.map((v) => {
      if (v === null || v === undefined) return '<td><span class="an-null">·</span></td>';
      const cls = isNum(v) ? ' class="an-num"' : '';
      const val = isNum(v) ? (Number.isInteger(v) ? v : Math.round(v * 1000) / 1000) : v;
      return `<td${cls}>${self._esc(String(val))}</td>`;
    }).join('') + '</tr>').join('');
    h += '</tbody></table></div>';
    wrap.innerHTML = h;
  },

  // =====================================================================
  //  Yordamchilar
  // =====================================================================
  _raf(fn) { requestAnimationFrame(() => requestAnimationFrame(fn)); },

  _cvar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#888';
  },

  _fmt(n) {
    const num = Number(n) || 0;
    return num.toLocaleString('en-US');
  },

  _esc(s) {
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  },

  _attr(s) { return this._esc(s).replace(/"/g, '&quot;'); },

  _loadingHTML() {
    return `<div class="card"><div class="an-load"><div class="an-spin"></div><span>Yuklanmoqda…</span></div></div>`;
  },

  _emptyHTML(title, sub, extra) {
    return `<div class="card"><div class="empty">
      <svg class="empty-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
        <rect x="3" y="4" width="18" height="16" rx="3"/><path d="M3 9h18M8 4v16" stroke-linecap="round"/></svg>
      <div style="font-size:14px;font-weight:600;color:var(--text-dim)">${title}</div>
      <div style="max-width:420px">${sub || ''}</div>
      ${extra || ''}
    </div></div>`;
  },

  _canvasEmpty(canvas, msg) {
    const box = canvas && canvas.parentElement;
    if (box) box.innerHTML = `<div class="an-load" style="padding:34px 10px"><span>${this._esc(msg || 'Ma‘lumot yo‘q')}</span></div>`;
  },
});
