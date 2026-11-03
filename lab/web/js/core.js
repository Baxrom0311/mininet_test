/* NetLab — yadro: API, hodisalar oqimi, pub-sub, DOM yordamchilari, ikonkalar. */
window.NL = window.NL || {};
(function (NL) {
  'use strict';

  // ---------- pub-sub ----------
  const subs = {};
  NL.on = (ev, fn) => { (subs[ev] = subs[ev] || []).push(fn); return () => { subs[ev] = subs[ev].filter(f => f !== fn); }; };
  NL.emit = (ev, data) => (subs[ev] || []).slice().forEach(fn => { try { fn(data); } catch (e) { console.error(ev, e); } });

  // ---------- DOM ----------
  NL.h = function h(tag, attrs, kids) {
    const e = document.createElement(tag);
    if (attrs) for (const k in attrs) {
      const v = attrs[k];
      if (v == null || v === false) continue;
      if (k === 'class') e.className = v;
      else if (k === 'html') e.innerHTML = v;
      else if (k === 'text') e.textContent = v;
      else if (k === 'style' && typeof v === 'object') Object.assign(e.style, v);
      else if (k.startsWith('on') && typeof v === 'function') e.addEventListener(k.slice(2), v);
      else if (v === true) e.setAttribute(k, '');
      else e.setAttribute(k, v);
    }
    (Array.isArray(kids) ? kids : kids != null ? [kids] : []).forEach(c => {
      if (c == null || c === false) return;
      e.appendChild(typeof c === 'string' || typeof c === 'number' ? document.createTextNode(String(c)) : c);
    });
    return e;
  };
  const SVGNS = 'http://www.w3.org/2000/svg';
  NL.s = function s(tag, attrs) {
    const e = document.createElementNS(SVGNS, tag);
    if (attrs) for (const k in attrs) if (attrs[k] != null) e.setAttribute(k, attrs[k]);
    return e;
  };
  NL.esc = (t) => String(t == null ? '' : t).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  NL.toast = function (msg, kind) {
    const box = document.getElementById('toasts');
    const t = NL.h('div', { class: 'toast-i ' + (kind || ''), text: msg });
    box.appendChild(t);
    setTimeout(() => { t.style.opacity = '0'; t.style.transition = 'opacity .4s'; }, kind === 'error' ? 6500 : 3200);
    setTimeout(() => t.remove(), kind === 'error' ? 7000 : 3700);
  };

  NL.storage = {
    get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private rejim */ } },
  };

  NL.fmtBps = function (b) {
    if (!b) return '0';
    if (b >= 1e9) return (b / 1e9).toFixed(2) + ' Gb/s';
    if (b >= 1e6) return (b / 1e6).toFixed(2) + ' Mb/s';
    if (b >= 1e3) return (b / 1e3).toFixed(1) + ' kb/s';
    return Math.round(b) + ' b/s';
  };
  NL.clock = (ts) => { const d = ts ? new Date(ts * 1000) : new Date(); return d.toTimeString().slice(0, 8); };

  // ---------- API ----------
  async function req(method, url, body) {
    const opt = { method, headers: {} };
    if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
    let r;
    try { r = await fetch(url, opt); } catch (e) { throw new Error('Server bilan aloqa yo\'q'); }
    let data = null;
    try { data = await r.json(); } catch (e) { /* bo'sh javob */ }
    if (!r.ok) throw new Error((data && (data.error || data.detail)) || ('HTTP ' + r.status));
    return data;
  }
  NL.api = {
    get: (u) => req('GET', u),
    post: (u, b) => req('POST', u, b === undefined ? {} : b),
    put: (u, b) => req('PUT', u, b),
    patch: (u, b) => req('PATCH', u, b),
    del: (u) => req('DELETE', u),
  };
  NL.wsUrl = (path) => (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + path;

  // ---------- hodisalar oqimi (WebSocket, avtomatik qayta ulanish) ----------
  NL.events = {
    ws: null, ok: false, backoff: 500,
    connect() {
      const ws = new WebSocket(NL.wsUrl('/ws/events'));
      this.ws = ws;
      ws.onopen = () => { this.ok = true; this.backoff = 500; NL.emit('ws', true); };
      ws.onmessage = (m) => {
        let d; try { d = JSON.parse(m.data); } catch (e) { return; }
        (Array.isArray(d) ? d : [d]).forEach(ev => NL.emit('ev:' + ev.type, ev));
      };
      ws.onclose = () => {
        this.ok = false; NL.emit('ws', false);
        setTimeout(() => this.connect(), this.backoff);
        this.backoff = Math.min(this.backoff * 2, 8000);
      };
      clearInterval(this._ka);
      this._ka = setInterval(() => { if (ws.readyState === 1) ws.send('ping'); }, 20000);
    },
  };

  // ---------- ikonkalar (o'ziga xos, 48x48) ----------
  const IC = {
    host: '<rect x="7" y="9" width="34" height="23" rx="3.5"/><path d="M18 40h12M24 32v8"/><path d="M12 14h24" opacity=".45"/>',
    server: '<rect x="12" y="5" width="24" height="38" rx="3.5"/><path d="M17 13h14M17 20h14M17 27h14"/><circle cx="19" cy="36" r="1.6" fill="currentColor"/><circle cx="25" cy="36" r="1.6" fill="currentColor"/>',
    switch: '<rect x="4" y="14" width="40" height="20" rx="5"/><path d="M12 21h18l-3-3M36 27H18l3 3"/><path d="M9 30h2M14 30h2" opacity=".5"/>',
    p4switch: '<rect x="12" y="12" width="24" height="24" rx="3"/><path d="M17 12V6M24 12V6M31 12V6M17 42v-6M24 42v-6M31 42v-6M12 17H6M12 24H6M12 31H6M42 17h-6M42 24h-6M42 31h-6"/><text x="24" y="28.5" text-anchor="middle" font-size="11" font-weight="800" fill="currentColor" stroke="none" font-family="system-ui">P4</text>',
    router: '<circle cx="24" cy="24" r="17"/><path d="M24 12v9m0 0-3-3m3 3 3-3M24 36v-9m0 0-3 3m3-3 3 3M12 24h9m0 0-3-3m3 3-3 3M36 24h-9m0 0 3-3m-3 3 3 3"/>',
    sdnrouter: '<path d="M24 5 40 14v20L24 43 8 34V14z"/><path d="M16 24h16m0 0-4-4m4 4-4 4M24 16v4M24 28v4" /><circle cx="24" cy="24" r="2" fill="currentColor"/>',
  };
  NL.icon = (type, size) => `<svg viewBox="0 0 48 48" width="${size || 34}" height="${size || 34}" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round" style="color:var(--c-${type})">${IC[type] || IC.host}</svg>`;
  NL.iconPaths = IC;

  NL.ui = {
    ico: (p) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">${p}</svg>`,
  };

  NL.PROTO_COLORS = {
    ICMP: '#35E0FF', ARP: '#FFC24B', TCP: '#9B7CFF', UDP: '#37E39B', DNS: '#5BFFB0',
    OSPF: '#FF5FD2', BGP: '#FF8A4B', RIP: '#FFD84B', 'IS-IS': '#C77DFF', EIGRP: '#FF9EC7',
    STP: '#8892B0', LLDP: '#8892B0', IPv6: '#7FA8FF', other: '#B8C1DA',
  };
})(window.NL);
