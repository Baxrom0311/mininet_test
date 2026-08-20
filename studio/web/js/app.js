/* Network Studio — yadro: ekran registri, router, mavzu, navigatsiya, toast.
 *
 * Ekran yozuvchilar uchun KONTRAKT:
 *   Studio.screen('id', {
 *     title:    'Ekran nomi',            // topbar sarlavhasi
 *     subtitle: 'qisqa izoh',            // topbar tagsarlavhasi (ixtiyoriy)
 *     icon:     '<svg ...>...</svg>',    // nav ikonasi (inline SVG matn)
 *     order:    10,                      // nav tartibi (kichik = yuqori)
 *     render(root, ctx) { ... },         // ekranni root elementga chizadi (bir marta)
 *     onShow(ctx) { ... },               // ekran har ko'rsatilganda (ixtiyoriy)
 *   });
 *   ctx = { api: Studio.api, el, toast, show, theme }.
 * `render` bir marta (birinchi ko'rsatishda) chaqiriladi va DOM keshlanadi;
 * `onShow` har safar chaqiriladi (ma'lumotni yangilash uchun).
 */
(function () {
  const screens = new Map();     // id -> def
  const rendered = new Map();     // id -> root element (kesh)
  let current = null;

  const ICON_FALLBACK =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">' +
    '<circle cx="12" cy="12" r="9"/></svg>';

  const Studio = {
    api: window.Bridge,

    screen(id, def) {
      screens.set(id, Object.assign({ id, order: 100, icon: ICON_FALLBACK }, def));
    },

    boot() {
      this._buildNav();
      this._initTheme();
      this._initThemeToggle();
      // hash yoki birinchi ekran
      const first = [...screens.values()].sort((a, b) => a.order - b.order)[0];
      const target = (location.hash || '').replace('#', '');
      this.show(screens.has(target) ? target : (first && first.id));
      this._pingConnection();
    },

    _buildNav() {
      const nav = document.getElementById('nav');
      nav.innerHTML = '';
      [...screens.values()].sort((a, b) => a.order - b.order).forEach((s) => {
        const a = document.createElement('button');
        a.className = 'nav-item';
        a.dataset.screen = s.id;
        a.innerHTML = `<span class="nav-ico">${s.icon}</span><span class="nav-label">${s.title}</span>`;
        a.addEventListener('click', () => this.show(s.id));
        nav.appendChild(a);
      });
    },

    show(id) {
      const def = screens.get(id);
      if (!def) return;
      current = id;
      location.hash = id;
      // nav faolligi
      document.querySelectorAll('.nav-item').forEach((n) =>
        n.classList.toggle('active', n.dataset.screen === id));
      // topbar
      document.getElementById('screen-title').textContent = def.title || '';
      document.getElementById('screen-subtitle').textContent = def.subtitle || '';
      // render (bir marta) + onShow (har safar)
      const rootEl = document.getElementById('screen-root');
      const ctx = this._ctx(def);
      if (!rendered.has(id)) {
        const holder = document.createElement('section');
        holder.className = 'screen';
        holder.dataset.screen = id;
        try { def.render && def.render(holder, ctx); }
        catch (e) { holder.innerHTML = this._errBox(e); console.error(e); }
        rendered.set(id, holder);
      }
      rootEl.innerHTML = '';
      const holder = rendered.get(id);
      rootEl.appendChild(holder);
      // yumshoq kirish animatsiyasi
      holder.classList.remove('enter'); void holder.offsetWidth; holder.classList.add('enter');
      try { def.onShow && def.onShow(ctx); }
      catch (e) { console.error(e); this.toast('Ekran xatosi: ' + e.message, 'danger'); }
    },

    refresh() { if (current) this.show(current); },

    _ctx(def) {
      return {
        api: this.api,
        toast: this.toast.bind(this),
        show: this.show.bind(this),
        theme: () => document.documentElement.dataset.theme,
        screenId: def.id,
      };
    },

    _errBox(e) {
      return `<div class="error-box glass"><b>Xato</b><pre>${(e && e.stack) || e}</pre></div>`;
    },

    /* ---- Mavzu (kun/tun) ---- */
    _initTheme() {
      const saved = localStorage.getItem('studio-theme') || 'dark';
      this.setTheme(saved);
    },
    setTheme(t) {
      document.documentElement.dataset.theme = t;
      localStorage.setItem('studio-theme', t);
    },
    toggleTheme() {
      const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
      this.setTheme(next);
    },
    _initThemeToggle() {
      const btn = document.getElementById('theme-toggle');
      btn && btn.addEventListener('click', () => this.toggleTheme());
    },

    /* ---- Ulanish indikatori ---- */
    setConn(state, label) {
      const el = document.getElementById('conn-status');
      if (!el) return;
      el.dataset.state = state; // 'ok' | 'off' | 'checking'
      const l = el.querySelector('.conn-label');
      if (l) l.textContent = label || state;
    },
    async _pingConnection() {
      this.setConn('checking', 'Tekshirilmoqda...');
      try {
        const cfg = await this.api.get_config();
        if (cfg && cfg.ssh_host) this.setConn('off', cfg.ssh_host + ' (sinalmagan)');
        else this.setConn('off', 'Sozlanmagan');
      } catch (e) { this.setConn('off', 'Backend yo\'q'); }
    },

    /* ---- Toast ---- */
    toast(msg, kind = 'info', ttl = 3800) {
      const wrap = document.getElementById('toasts');
      if (!wrap) return;
      const t = document.createElement('div');
      t.className = `toast glass ${kind}`;
      t.textContent = msg;
      wrap.appendChild(t);
      requestAnimationFrame(() => t.classList.add('in'));
      setTimeout(() => { t.classList.remove('in'); setTimeout(() => t.remove(), 300); }, ttl);
    },
  };

  window.Studio = Studio;
})();
