/* Sozlamalar ekrani — ilova va server konfiguratsiyasi.
 * Kontrakt: get_config() / save_config(cfg) / test_ssh(cfg).
 * Konfiguratsiya kalitlari: ssh_host, ssh_user, ssh_pass, wsl_distro,
 * remote_repo, default_duration, default_seed, results_dir, theme. */
Studio.screen('settings', {
  title: 'Sozlamalar',
  subtitle: 'Ilova va server konfiguratsiyasi',
  order: 90,
  icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">' +
    '<circle cx="12" cy="12" r="3.2"/>' +
    '<path d="M12 2.6v2.4M12 19v2.4M4.6 4.6l1.7 1.7M17.7 17.7l1.7 1.7M2.6 12h2.4M19 12h2.4M4.6 19.4l1.7-1.7M17.7 6.3l1.7-1.7"/>' +
    '</svg>',

  render(root) {
    // Maydon ta'riflari — bir joyda, DRY
    const svrFields = [
      { key: 'ssh_host',   label: 'Server manzili (host / IP)', ph: '192.168.1.50', span: 1 },
      { key: 'ssh_user',   label: 'Foydalanuvchi',             ph: 'incubation',   span: 1 },
      { key: 'ssh_pass',   label: 'Parol',                     ph: '••••••••', type: 'password', span: 1 },
      { key: 'wsl_distro', label: 'WSL distributivi',          ph: 'Ubuntu-24.04', span: 1 },
      { key: 'remote_repo',label: 'Serverdagi repo yoʻli',     ph: '/root/mininet', span: 2, mono: true },
    ];
    const defFields = [
      { key: 'default_duration', label: 'Standart davomiylik (soniya)', ph: '300', type: 'number' },
      { key: 'default_seed',     label: 'Standart urugʻ (seed)',        ph: '42',  type: 'number' },
      { key: 'results_dir',      label: 'Natijalar papkasi',            ph: 'results', mono: true },
    ];

    const fieldHTML = (f) => {
      const cls = 'input' + (f.mono ? ' mono' : '');
      const type = f.type || 'text';
      const auto = type === 'password' ? 'autocomplete="new-password"' : 'autocomplete="off"';
      const extra = type === 'number' ? 'min="1" step="1" inputmode="numeric"' : '';
      return `<div class="field"${f.span === 2 ? ' style="grid-column:1/-1"' : ''}>
          <label for="set-${f.key}">${f.label}</label>
          <input id="set-${f.key}" class="${cls}" type="${type}" ${auto} ${extra}
                 placeholder="${f.ph}" data-key="${f.key}"${f.type === 'number' ? ' data-num="1"' : ''}>
        </div>`;
    };

    root.innerHTML = `
      <div class="settings-wrap">
        <div class="settings-grid">
          <!-- ============ CHAP USTUN ============ -->
          <div class="stack">

            <!-- SSH server -->
            <div class="card set-card">
              <div class="set-head">
                <div class="set-head-ico" data-accent="accent">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7">
                    <rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/>
                    <path d="M7 7.5h.01M7 16.5h.01"/></svg>
                </div>
                <div>
                  <div class="card-title">Server (SSH)</div>
                  <div class="card-sub">Simulyatsiyalar shu masofaviy WSL/Ubuntu serverida bajariladi</div>
                </div>
              </div>
              <div class="grid cols-2 set-fields">
                ${svrFields.map(fieldHTML).join('')}
              </div>
              <div class="set-actions">
                <button class="btn neon" id="set-test">
                  <span class="ico">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M13 2 4.5 13H11l-1 9 8.5-11H12l1-9Z"/></svg>
                  </span>
                  <span class="lbl">Ulanishni tekshirish</span>
                  <span class="spinner" hidden></span>
                </button>
                <span class="test-result" id="set-test-result" hidden></span>
                <span class="set-actions-sp"></span>
                <button class="btn primary" id="set-save-server">
                  <span class="ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M5 4h11l3 3v13H5z"/><path d="M8 4v5h7M8 20v-6h8v6"/></svg></span>
                  Saqlash
                </button>
              </div>
            </div>

            <!-- Standart qiymatlar -->
            <div class="card set-card">
              <div class="set-head">
                <div class="set-head-ico" data-accent="accent-2">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7">
                    <path d="M4 7h16M4 12h16M4 17h10"/></svg>
                </div>
                <div>
                  <div class="card-title">Standart qiymatlar</div>
                  <div class="card-sub">Yangi simulyatsiya oldindan shu qiymatlar bilan toʻldiriladi</div>
                </div>
              </div>
              <div class="grid cols-3 set-fields">
                ${defFields.map(fieldHTML).join('')}
              </div>
              <div class="set-actions">
                <button class="btn primary" id="set-save-defaults">
                  <span class="ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M5 4h11l3 3v13H5z"/><path d="M8 4v5h7M8 20v-6h8v6"/></svg></span>
                  Saqlash
                </button>
              </div>
            </div>
          </div>

          <!-- ============ OʻNG USTUN ============ -->
          <div class="stack">

            <!-- Koʻrinish -->
            <div class="card set-card">
              <div class="set-head">
                <div class="set-head-ico" data-accent="accent-3">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7">
                    <circle cx="12" cy="12" r="4"/><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M18.4 5.6 17 7M7 17l-1.4 1.4"/></svg>
                </div>
                <div>
                  <div class="card-title">Koʻrinish</div>
                  <div class="card-sub">Mavzu tanlovi darhol qoʻllanadi va saqlanadi</div>
                </div>
              </div>
              <div class="segment set-theme" id="set-theme">
                <button data-theme-val="dark">Tun</button>
                <button data-theme-val="light">Kun</button>
              </div>
              <div class="theme-preview" id="set-theme-preview" aria-hidden="true">
                <span class="tp-swatch tp-bg"></span>
                <span class="tp-swatch tp-a1"></span>
                <span class="tp-swatch tp-a2"></span>
                <span class="tp-swatch tp-a3"></span>
                <span class="tp-note hint">Jonli koʻrinish</span>
              </div>
            </div>

            <!-- Ilova haqida -->
            <div class="card set-card set-about">
              <div class="set-head">
                <div class="brand-mark set-about-mark"></div>
                <div>
                  <div class="card-title">Network <span class="grad">Studio</span></div>
                  <div class="card-sub">Vizual SDN simulyatsiya boshqaruvi</div>
                </div>
              </div>
              <p class="set-about-text">
                Topologiya qurish, uni masofaviy serverda ishga tushirish va yigʻilgan
                maʼlumotni tahlil qilish — barchasi bitta oynada.
              </p>
              <ul class="set-about-list">
                <li><span class="dot" style="--d:var(--accent)"></span>Topologiyani vizual quring va yoʻnaltirishni sozlang</li>
                <li><span class="dot" style="--d:var(--accent-2)"></span>Serverda ishga tushiring, jarayonni jonli kuzating</li>
                <li><span class="dot" style="--d:var(--accent-3)"></span>Natijalarni chizmalar va soʻrovlar bilan tahlil qiling</li>
              </ul>
              <div class="set-about-note hint">
                Mininet haqiqiy Linux yadrosini talab qilgani uchun simulyatsiyalar
                masofaviy WSL/Ubuntu serverida bajariladi.
              </div>
            </div>
          </div>
        </div>
      </div>`;

    // ---------- Yordamchilar ----------
    const $ = (s) => root.querySelector(s);
    const inputs = () => Array.from(root.querySelectorAll('input[data-key]'));

    // Barcha maydonlardan konfiguratsiya obyektini yigʻish
    const collect = () => {
      const out = {};
      inputs().forEach((el) => {
        if (el.dataset.num) {
          const n = parseInt(el.value, 10);
          out[el.dataset.key] = Number.isFinite(n) ? n : 0;
        } else {
          out[el.dataset.key] = el.value;
        }
      });
      out.theme = document.documentElement.dataset.theme || 'dark';
      return out;
    };

    // Konfiguratsiyani maydonlarga joylash
    const fill = (cfg) => {
      cfg = cfg || {};
      inputs().forEach((el) => {
        const v = cfg[el.dataset.key];
        el.value = (v === undefined || v === null) ? '' : v;
      });
      if (cfg.theme) syncThemeUI(cfg.theme);
    };

    const syncThemeUI = (t) => {
      root.querySelectorAll('#set-theme button').forEach((b) =>
        b.classList.toggle('active', b.dataset.themeVal === t));
    };

    const btnBusy = (btn, on) => {
      if (!btn) return;
      btn.disabled = on;
      const sp = btn.querySelector('.spinner');
      const lbl = btn.querySelector('.lbl');
      if (sp) sp.hidden = !on;
      if (lbl) lbl.textContent = on ? 'Tekshirilmoqda…' : 'Ulanishni tekshirish';
    };

    // ---------- Saqlash (round-trip) ----------
    const doSave = async (btn, ctx) => {
      const original = btn ? btn.innerHTML : '';
      if (btn) { btn.disabled = true; }
      try {
        await ctx.api.save_config(collect());
        // tasdiqlash uchun qayta oʻqiymiz
        const fresh = await ctx.api.get_config();
        fill(fresh);
        ctx.toast('Sozlamalar saqlandi', 'success');
      } catch (e) {
        ctx.toast('Saqlab boʻlmadi: ' + (e && e.message || e), 'danger');
      } finally {
        if (btn) { btn.disabled = false; btn.innerHTML = original; }
      }
    };

    // ---------- Ulanishni tekshirish ----------
    const doTest = async (ctx) => {
      const btn = $('#set-test');
      const res = $('#set-test-result');
      const cfg = collect();
      if (!cfg.ssh_host) {
        ctx.toast('Avval server manzilini kiriting', 'warn');
        $('#set-ssh_host').focus();
        return;
      }
      btnBusy(btn, true);
      res.hidden = false;
      res.className = 'test-result checking';
      res.innerHTML = '<span class="badge">Ulanmoqda…</span>';
      Studio.setConn('checking', cfg.ssh_host + ' — tekshirilmoqda…');
      try {
        const r = await ctx.api.test_ssh(cfg);
        const ok = !!(r && r.ok);
        const msg = (r && r.message) || (ok ? 'Ulanish muvaffaqiyatli' : 'Ulanib boʻlmadi');
        res.className = 'test-result ' + (ok ? 'ok' : 'bad');
        res.innerHTML = `<span class="badge ${ok ? 'ok' : 'danger'}">${ok ? '● Ulandi' : '● Xato'}</span>` +
          `<span class="test-msg">${escapeHtml(msg)}</span>`;
        Studio.setConn(ok ? 'ok' : 'off', ok ? cfg.ssh_host : (cfg.ssh_host + ' — ulanmadi'));
        ctx.toast(ok ? 'Serverga ulanildi' : 'Ulanib boʻlmadi', ok ? 'success' : 'danger');
      } catch (e) {
        res.className = 'test-result bad';
        res.innerHTML = '<span class="badge danger">● Xato</span>' +
          `<span class="test-msg">${escapeHtml(e && e.message || String(e))}</span>`;
        Studio.setConn('off', 'Ulanish xatosi');
        ctx.toast('Ulanishda xatolik', 'danger');
      } finally {
        btnBusy(btn, false);
      }
    };

    const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

    // ---------- Hodisalarni ulash (bir marta) ----------
    // ctx renderda mavjud emas — Studio ni to'g'ridan-to'g'ri ishlatamiz,
    // toast/api uchun esa har handlerda tozalangan ctx yasaymiz.
    const mkCtx = () => ({ api: Studio.api, toast: Studio.toast.bind(Studio) });

    $('#set-test').addEventListener('click', () => doTest(mkCtx()));
    $('#set-save-server').addEventListener('click', (e) => doSave(e.currentTarget, mkCtx()));
    $('#set-save-defaults').addEventListener('click', (e) => doSave(e.currentTarget, mkCtx()));

    // Enter -> saqlash (matn maydonlarida)
    root.querySelectorAll('input[data-key]').forEach((el) => {
      el.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter') { ev.preventDefault(); doSave($('#set-save-server'), mkCtx()); }
      });
    });

    // Mavzu segmenti — jonli + saqlash
    root.querySelector('#set-theme').addEventListener('click', async (ev) => {
      const b = ev.target.closest('button[data-theme-val]');
      if (!b) return;
      const t = b.dataset.themeVal;
      Studio.setTheme(t);
      syncThemeUI(t);
      try { await Studio.api.save_config({ theme: t }); } catch (e) { /* mavzu localStorage da saqlangan */ }
    });

    // onShow uchun helperlarni saqlaymiz
    this._fill = fill;
    this._syncTheme = syncThemeUI;
  },

  async onShow(ctx) {
    // Har koʻrsatilganda joriy qiymatlarni oʻqiymiz
    this._syncTheme(document.documentElement.dataset.theme || 'dark');
    try {
      const cfg = await ctx.api.get_config();
      this._fill(cfg || {});
    } catch (e) {
      ctx.toast('Konfiguratsiyani oʻqib boʻlmadi', 'danger');
    }
  },
});
