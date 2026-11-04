/* NetLab — suzuvchi oynalar: qurilma ichi (Sozlama / CLI / Jadvallar / Hardware),
   P4 dastur muharriri, SDN kontroller muharriri. */
(function (NL) {
  'use strict';
  const S = NL.store, h = NL.h;
  let zTop = 60, offset = 0;
  const wins = {};

  // ---------- oyna menejeri ----------
  NL.win = {
    open(id, opt) {
      if (wins[id]) { this.focus(id); return wins[id]; }
      const tabsEl = h('div', { class: 'win-tabs' });
      const body = h('div', { class: 'win-body' });
      const head = h('div', { class: 'win-head' }, [
        h('span', { class: 'ico', html: opt.icon || '' }),
        h('div', {}, [h('div', { class: 't', text: opt.title }), h('div', { class: 's', text: opt.sub || '' })]),
        h('button', { class: 'x', title: 'Yopish', text: '×', onclick: () => this.close(id) }),
      ]);
      const el = h('div', { class: 'win', style: {
        width: (opt.w || 720) + 'px', height: (opt.h || 520) + 'px',
        left: Math.min(innerWidth - 460, 300 + offset) + 'px', top: Math.min(innerHeight - 320, 90 + offset) + 'px' } },
        [head, opt.tabs ? tabsEl : null, body]);
      offset = (offset + 28) % 200;
      document.getElementById('windows').appendChild(el);
      const w = { id, el, body, tabsEl, panes: {}, disposers: [], opt, active: null };
      wins[id] = w;
      el.addEventListener('pointerdown', () => this.focus(id));
      // sudrash
      head.addEventListener('pointerdown', (ev) => {
        if (ev.target.closest('.x')) return;
        const r = el.getBoundingClientRect(), sx = ev.clientX - r.left, sy = ev.clientY - r.top;
        const mv = (e) => { el.style.left = Math.max(0, e.clientX - sx) + 'px'; el.style.top = Math.max(0, e.clientY - sy) + 'px'; };
        const up = () => { removeEventListener('pointermove', mv); removeEventListener('pointerup', up); };
        addEventListener('pointermove', mv); addEventListener('pointerup', up);
      });
      if (opt.tabs) {
        opt.tabs.forEach(t => {
          const b = h('button', { text: t.label, onclick: () => this.tab(id, t.id) });
          b.dataset.tab = t.id;
          tabsEl.appendChild(b);
        });
        this.tab(id, opt.initial || opt.tabs[0].id);
      } else if (opt.render) {
        const d = opt.render(body, w);
        if (d && d.dispose) w.disposers.push(d.dispose);
      }
      this.focus(id);
      return w;
    },
    tab(id, tid) {
      const w = wins[id]; if (!w) return;
      w.active = tid;
      w.tabsEl.querySelectorAll('button').forEach(b => b.classList.toggle('active', b.dataset.tab === tid));
      Object.entries(w.panes).forEach(([k, p]) => { p.style.display = k === tid ? '' : 'none'; });
      if (!w.panes[tid]) {
        const t = w.opt.tabs.find(x => x.id === tid);
        const pane = h('div', { style: { display: 'flex', flexDirection: 'column', gap: '12px', flex: '1', minHeight: '0' } });
        w.panes[tid] = pane;
        w.body.appendChild(pane);
        w.body.classList.toggle('flush', !!t.flush);
        const d = t.render(pane, w);
        if (d && d.dispose) w.disposers.push(d.dispose);
        if (d && d.show) pane._show = d.show;
      } else {
        const t = w.opt.tabs.find(x => x.id === tid);
        w.body.classList.toggle('flush', !!t.flush);
      }
      if (w.panes[tid]._show) w.panes[tid]._show();
    },
    focus(id) {
      Object.values(wins).forEach(w => w.el.classList.remove('focus'));
      const w = wins[id]; if (!w) return;
      w.el.style.zIndex = ++zTop; w.el.classList.add('focus');
    },
    close(id) {
      const w = wins[id]; if (!w) return;
      w.disposers.forEach(fn => { try { fn(); } catch (e) { /* */ } });
      w.el.remove(); delete wins[id];
    },
    has: (id) => !!wins[id],
  };

  NL.on('device-removed', (name) => NL.win.close('dev:' + name));

  // ---------- umumiy forma yordamchilari ----------
  const field = (label, input) => h('div', { class: 'field' }, [h('label', { text: label }), input]);
  const inp = (val, ph, attrs) => h('input', Object.assign({ class: 'input', value: val == null ? '' : val, placeholder: ph || '' }, attrs || {}));
  const sel = (opts, val) => {
    const s = h('select', { class: 'select' });
    opts.forEach(o => { const op = new Option(o[1], o[0]); if (o[0] === val) op.selected = true; s.appendChild(op); });
    return s;
  };
  const ta = (val, rows) => { const t = h('textarea', { class: 'input code', rows: rows || 10, spellcheck: 'false' }); t.value = val || ''; return t; };
  NL.form = { field, inp, sel, ta };

  // ---------- qurilma oynasi ----------
  NL.devwin = {
    open(name, tab) {
      const d = S.dev(name); if (!d) return;
      const label = (S.info.device_types[d.type] || {}).label || d.type;
      const tabs = [
        { id: 'cfg', label: 'Sozlama', render: (p) => renderConfig(p, name) },
        { id: 'cli', label: 'CLI', flush: true, render: (p) => renderTerminal(p, name) },
        { id: 'tbl', label: 'Jadvallar', render: (p) => renderInspect(p, name, 'tables') },
        { id: 'hw', label: 'Hardware', render: (p) => renderInspect(p, name, 'hw') },
      ];
      const w = NL.win.open('dev:' + name, { title: name, sub: label + ' · id ' + d.id, icon: NL.icon(d.type, 22), tabs,
        initial: tab || (S.running() ? 'cli' : 'cfg'), w: 760, h: 540 });
      if (tab) NL.win.tab('dev:' + name, tab);
      return w;
    },
  };

  // ======== Sozlama ========
  function renderConfig(pane, name) {
    const d = S.dev(name);
    const cfg = JSON.parse(JSON.stringify(d.config || {}));
    const t = d.type;
    const ports = S.portsOf(name);
    const portNums = Object.keys(ports).map(Number).sort((a, b) => a - b);
    const getters = [];

    // interfeyslar (L3)
    if (['host', 'server', 'router', 'sdnrouter'].includes(t)) {
      cfg.interfaces = cfg.interfaces || {};
      const rows = portNums.map(p => {
        const ifn = S.ifname(name, p);
        const i = inp((cfg.interfaces[ifn] || {}).ip, 'masalan 10.0.1.1/24');
        getters.push(() => { const v = i.value.trim(); if (v) cfg.interfaces[ifn] = { ip: v }; else delete cfg.interfaces[ifn]; });
        return h('tr', {}, [h('td', { text: ifn }), h('td', { text: `${ports[p].peer}:eth${ports[p].peerPort}` }),
          h('td', { text: ['host', 'server', 'router'].includes(t) ? S.mac(d.id, p) : 'OVS port MAC' }), h('td', {}, i)]);
      });
      pane.appendChild(h('div', { class: 'isec' }, [h('h4', { text: 'Interfeyslar (IPv4)' }),
        rows.length ? h('table', {}, [h('tr', {}, ['Interfeys', 'Ulangan', 'MAC', 'IP/prefiks'].map(x => h('th', { text: x }))), ...rows])
          : h('div', { class: 'empty-row', text: 'Kabel ulanmagan — avval kanvasda kabel torting (C).' })]));
    }
    if (['host', 'server'].includes(t)) {
      const gw = inp(cfg.gateway, '10.0.1.1');
      getters.push(() => { cfg.gateway = gw.value.trim(); });
      pane.appendChild(field('Default gateway', gw));
    }
    if (t === 'router') {
      const box = h('div', { class: 'row wrap' });
      const checks = {};
      (S.info.frr_daemons || []).forEach(dm => {
        const c = h('input', { type: 'checkbox' }); c.checked = (cfg.daemons || []).includes(dm); checks[dm] = c;
        box.appendChild(h('label', { class: 'toggle' }, [c, dm]));
      });
      getters.push(() => { cfg.daemons = Object.keys(checks).filter(k => checks[k].checked); });
      pane.appendChild(h('div', { class: 'isec' }, [h('h4', { text: 'FRR daemonlari (zebra va staticd har doim ishlaydi)' }), h('div', { style: { padding: '10px' } }, box)]));
      const st = ta(cfg.startup, 12);
      st.placeholder = 'FRR konfiguratsiyasi (vtysh sintaksisi), masalan:\nrouter ospf\n network 10.0.1.0/24 area 0';
      getters.push(() => { cfg.startup = st.value; });
      const pull = h('button', { class: 'btn ghost sm', text: 'Joriy running-config\'ni olish', onclick: async () => {
        try { const r = await NL.api.get(`/api/net/frr/${name}/running-config`); st.value = r.config; NL.toast('running-config olindi — saqlashni unutmang', 'ok'); }
        catch (e) { NL.toast(e.message, 'error'); }
      } });
      pane.appendChild(h('div', { class: 'field' }, [h('label', { text: 'Startup-config (tarmoq ishga tushganda qo\'llanadi)' }), st,
        h('div', { class: 'btn-row' }, [pull, h('span', { class: 'note', text: 'Jonli o\'zgartirish: CLI bo\'limida vtysh (configure terminal ...). Keyin "olish" → "Saqlash".' })])]));
    }
    if (t === 'switch') {
      const mode = sel([['standalone', 'Standalone (OVS L2 learning)'], ['sdn', 'SDN (kontroller boshqaradi)']], cfg.mode || 'standalone');
      const ctrl = sel([['', '— tanlang —'], ...Object.keys(S.project.controllers).map(c => [c, c])], cfg.controller || '');
      const stp = h('input', { type: 'checkbox' }); stp.checked = !!cfg.stp;
      getters.push(() => { cfg.mode = mode.value; cfg.controller = ctrl.value; cfg.stp = stp.checked; });
      pane.append(h('div', { class: 'f-row' }, [field('Rejim', mode), field('SDN kontroller', ctrl)]),
        h('label', { class: 'toggle' }, [stp, 'STP (802.1D) — halqali topologiya uchun (faqat standalone)']));
    }
    if (t === 'wifiap') {
      const W = NL.wifi.apConfig(d);
      const std = sel([['80211ax', '802.11ax (Wi-Fi 6)'], ['80211ac', '802.11ac (Wi-Fi 5)'], ['80211n', '802.11n (Wi-Fi 4)'], ['80211g', '802.11g']], W.standard);
      const band = sel([['5', '5 GHz'], ['2.4', '2.4 GHz']], String(W.band));
      const width = sel([['20', '20 MHz'], ['40', '40 MHz'], ['80', '80 MHz'], ['160', '160 MHz']], String(W.width));
      const txp = inp(W.tx_power, '20', { type: 'number', step: 'any' });
      const chSel = sel([], '');
      const fillCh = () => {
        const b = std.value === '80211g' ? '2.4' : std.value === '80211ac' ? '5' : band.value;
        const wd = b === '2.4' ? String(Math.min(parseInt(width.value, 10), 40)) : width.value;
        const list = (((S.info.wifi || {}).channels || {})[b] || {})[wd] || [];
        const cur = chSel.value || String(W.channel);
        chSel.innerHTML = '';
        list.forEach(c => chSel.appendChild(new Option(String(c), String(c))));
        chSel.value = list.map(String).includes(cur) ? cur : String(list[0] || '');
      };
      [std, band, width].forEach(x => x.addEventListener('change', fillCh));
      fillCh();
      const ssid = inp(cfg.ssid || '', 'netlab-' + name);
      getters.push(() => { cfg.standard = std.value; cfg.band = parseFloat(band.value); cfg.width = parseInt(width.value, 10);
        cfg.tx_power = parseFloat(txp.value) || 20; cfg.ssid = ssid.value.trim(); cfg.channel = parseInt(chSel.value, 10) || 0; });
      const rows = Object.entries(ports).map(([p_, info]) => {
        const e = NL.wifi.linkEstimate(info.lid);
        return e ? h('tr', {}, [info.peer, e.distance_m.toFixed(1) + ' m', e.rssi_dbm.toFixed(1), e.snr_db.toFixed(1), e.ok ? 'MCS' + e.mcs : '—', e.ok ? '~' + e.thr_mbps.toFixed(1) : 'aloqa yo\'q'].map(x => h('td', { text: x })))
          : h('tr', {}, [info.peer + ' (simli, eth' + p_ + ')', '', '', '', '', ''].map(x => h('td', { text: x })));
      });
      pane.append(h('div', { class: 'f-row' }, [field('Standart', std), field('Diapazon', band)]),
        h('div', { class: 'f-row' }, [field('Kanal kengligi', width), field('Uzatish quvvati (dBm)', txp)]),
        h('div', { class: 'f-row' }, [field('SSID', ssid), field('Kanal (bir kanaldagi yaqin AP\'lar efirni bo\'lishadi)', chSel)]),
        h('div', { class: 'isec' }, [h('h4', { text: 'Stansiyalar (radio modeli, joriy joylashuv)' }),
          h('table', {}, [h('tr', {}, ['Qurilma', 'Masofa', 'RSSI dBm', 'SNR dB', 'MCS', 'Mbit/s'].map(x => h('th', { text: x }))), ...rows])]),
        h('div', { class: 'note', text: 'Simsiz ulash: kabel asbobi (C) bilan AP va host/server/router ni bosing — avtomatik Wi-Fi bo\'ladi. AP\'ning simli porti (switch/server ga) oddiy kabel.' }));
    }
    if (t === 'sdnrouter') {
      const ctrl = sel([['', '— tanlang —'], ...Object.keys(S.project.controllers).map(c => [c, c])], cfg.controller || '');
      getters.push(() => { cfg.controller = ctrl.value; });
      pane.append(field('SDN kontroller (routing mantiqi shu ilovada)', ctrl),
        h('div', { class: 'note', text: 'SDN router — OVS bridge; ARP javobi, LPM, TTL va flow o\'rnatish kontroller ilovasida (l3_router shabloni). IP va marshrutlar kontrollerga NETLAB_CONFIG orqali beriladi.' }));
    }
    if (['host', 'server', 'router', 'sdnrouter'].includes(t)) {
      cfg.routes = cfg.routes || [];
      const rt = ta(cfg.routes.map(r => `${r.prefix} via ${r.via}`).join('\n'), 3);
      rt.style.minHeight = '64px';
      rt.placeholder = '10.9.0.0/16 via 10.0.12.2';
      getters.push(() => {
        cfg.routes = rt.value.split('\n').map(x => x.trim()).filter(Boolean).map(x => {
          const m = x.match(/^(\S+)\s+via\s+(\S+)$/); return m ? { prefix: m[1], via: m[2] } : null;
        }).filter(Boolean);
      });
      pane.appendChild(field(t === 'router' ? 'Statik marshrutlar (kernel; FRR uchun startup-config\'da "ip route")' : 'Statik marshrutlar', rt));
    }
    let p4log;
    if (t === 'p4switch') {
      const prog = sel([['', '— tanlang —'], ...Object.keys(S.project.p4_programs).map(p => [p, p])], cfg.program || '');
      const rt = ta(cfg.runtime, 8);
      rt.placeholder = 'simple_switch_CLI buyruqlari, masalan:\ntable_add dmac forward 00:00:00:00:01:01 => 1';
      getters.push(() => { cfg.program = prog.value; cfg.runtime = rt.value; });
      p4log = h('pre', { class: 'log-out' });
      const macs = portNums.map(p => {
        const peer = S.dev(ports[p].peer);
        const mac = peer && ['host', 'server', 'router'].includes(peer.type) ? S.mac(peer.id, ports[p].peerPort) : '(switch)';
        return `port ${p} → ${ports[p].peer}:eth${ports[p].peerPort}  ${mac}`;
      }).join('\n');
      pane.append(
        h('div', { class: 'f-row' }, [field('P4 dastur', prog), h('div', { class: 'field' }, [h('label', { text: ' ' }),
          h('button', { class: 'btn ghost sm', text: 'Dasturni ochish', onclick: () => prog.value && NL.p4win.open(prog.value) })])]),
        h('div', { class: 'isec' }, [h('h4', { text: 'Portlar → qo\'shni MAC (jadval yozish uchun)' }), h('pre', { text: macs || '(kabel yo\'q)' })]),
        field('Runtime buyruqlari (dastur yuklangandan keyin qo\'llanadi)', rt),
        h('div', { class: 'btn-row' }, [
          h('button', { class: 'btn neon sm', text: 'Kompilyatsiya + yuklash', onclick: async () => {
            collect(); await S.saveDeviceConfig(name, cfg);
            if (!S.running()) { NL.toast('Avval tarmoqni ishga tushiring (yoki P4 muharririda "Tekshirish")', 'warn'); return; }
            p4log.textContent = 'kompilyatsiya...'; p4log.classList.remove('err');
            try { const r = await NL.api.post(`/api/net/p4/${name}/load`, { project: S.project });
              p4log.textContent = r.log; p4log.classList.toggle('err', !r.ok); } catch (e) { p4log.textContent = e.message; p4log.classList.add('err'); }
          } }),
          h('button', { class: 'btn ghost sm', text: 'Faqat runtime yuborish', onclick: async () => {
            try { const r = await NL.api.post(`/api/net/p4/${name}/runtime`, { commands: rt.value }); p4log.textContent = r.output; p4log.classList.remove('err'); }
            catch (e) { p4log.textContent = e.message; p4log.classList.add('err'); }
          } }),
        ]), p4log);
    }

    // nom
    const nm = inp(name, '', { style: { maxWidth: '160px' } });
    const notes = h('div', { class: 'btn-row', style: { marginTop: 'auto', alignItems: 'center' } }, [
      h('button', { class: 'btn primary sm', text: S.running() ? 'Saqlash va qo\'llash' : 'Saqlash', onclick: save }),
      h('span', { class: 'note', text: 'Nom:' }), nm,
      h('button', { class: 'btn ghost sm', text: 'Nomni o\'zgartirish', onclick: () => {
        const nn = nm.value.trim(); if (nn && nn !== name && S.renameDevice(name, nn)) NL.devwin.open(nn, 'cfg');
      } }),
    ]);
    pane.appendChild(notes);

    function collect() { getters.forEach(g => g()); }
    async function save() { collect(); await S.saveDeviceConfig(name, cfg); if (!S.running()) NL.toast(`${name}: saqlandi`, 'ok'); }
  }

  // ======== Terminal ========
  function renderTerminal(pane, name) {
    const d = S.dev(name);
    const shells = d.type === 'router' ? [['vtysh', 'vtysh (FRR CLI)'], ['bash', 'bash']]
      : d.type === 'p4switch' ? [['cli', 'simple_switch_CLI'], ['bash', 'bash']] : [['default', 'bash']];
    const shellSel = sel(shells, shells[0][0]);
    shellSel.style.width = '190px'; shellSel.style.padding = '5px 30px 5px 9px';
    const state = h('span', { class: 'note' });
    const host = h('div', { class: 'term-host' });
    const reconnect = h('button', { class: 'btn ghost sm', text: 'Qayta ulash', onclick: () => connect() });
    pane.appendChild(h('div', { class: 'term-bar' }, ['Qobiq:', shellSel, reconnect, state]));
    pane.appendChild(h('div', { class: 'term-wrap', onclick: () => term && term.focus && term.focus() }, host));
    pane.style.gap = '0';
    let term, fit, ws, ro, fallback;

    function connect() {
      if (ws) { try { ws.close(); } catch (e) { /* */ } }
      if (!S.running()) { state.textContent = 'Tarmoq ishlamayapti — yuqoridagi "Ishga tushirish" tugmasi'; }
      ensureTerm();
      term.clear && term.clear();
      ws = new WebSocket(NL.wsUrl(`/ws/term/${name}?shell=${shellSel.value}`));
      ws.binaryType = 'arraybuffer';
      ws.onopen = () => { state.textContent = 'ulangan'; sendSize(); term.focus && term.focus(); };
      ws.onmessage = (m) => term.write(typeof m.data === 'string' ? m.data : new Uint8Array(m.data));
      ws.onclose = () => { state.textContent = 'uzildi'; };
    }
    function sendSize() { if (ws && ws.readyState === 1 && term.cols) ws.send(JSON.stringify({ t: 'resize', cols: term.cols, rows: term.rows })); }
    function ensureTerm() {
      if (term) return;
      if (window.Terminal) {
        term = new window.Terminal({ fontFamily: 'SF Mono, Menlo, Consolas, monospace', fontSize: 13, cursorBlink: true,
          theme: { background: '#05070d', foreground: '#dfe6f5', cursor: '#35E0FF', selectionBackground: '#35E0FF55' }, scrollback: 5000 });
        if (window.FitAddon) { fit = new window.FitAddon.FitAddon(); term.loadAddon(fit); }
        term.open(host);
        term.onData((data) => { if (ws && ws.readyState === 1) ws.send(JSON.stringify({ t: 'in', d: data })); });
        ro = new ResizeObserver(() => { if (fit && host.offsetWidth) { try { fit.fit(); sendSize(); } catch (e) { /* */ } } });
        ro.observe(host);
      } else {
        // xterm.js yuklanmagan (offline) — oddiy zaxira terminal
        fallback = h('pre', { style: { color: '#dfe6f5', fontFamily: 'monospace', fontSize: '12.5px', whiteSpace: 'pre-wrap', height: '100%', overflow: 'auto', outline: 'none' }, tabindex: 0 });
        host.appendChild(fallback);
        const dec = new TextDecoder();
        term = {
          write(data) { const t = typeof data === 'string' ? data : dec.decode(data); fallback.textContent += t.replace(/\x1b\[[0-9;?]*[A-Za-z]/g, '').replace(/\r/g, ''); fallback.scrollTop = 1e9; },
          focus() { fallback.focus(); }, cols: 100, rows: 30,
        };
        fallback.addEventListener('keydown', (e) => {
          if (!ws || ws.readyState !== 1) return;
          let d = e.key.length === 1 ? e.key : ({ Enter: '\r', Backspace: '\x7f', Tab: '\t' })[e.key];
          if (e.ctrlKey && e.key === 'c') d = '\x03';
          if (d) { e.preventDefault(); ws.send(JSON.stringify({ t: 'in', d })); }
        });
      }
    }
    shellSel.addEventListener('change', connect);
    let started = false;
    return {
      show() {
        if (!started) { started = true; setTimeout(connect, 30); }
        else setTimeout(() => { try { if (fit) fit.fit(); } catch (e) { /* */ } if (term && term.focus) term.focus(); }, 30);
      },
      dispose() { if (ws) ws.close(); if (ro) ro.disconnect(); if (term && term.dispose) term.dispose(); },
    };
  }

  // ======== Jadvallar / Hardware ========
  function renderSection(sec) {
    const box = h('div', { class: 'isec' }, [h('h4', {}, [sec.title, sec.hint ? h('small', { class: 'note', text: sec.hint }) : null])]);
    if (sec.kind === 'table') {
      if (!sec.rows || !sec.rows.length) box.appendChild(h('div', { class: 'empty-row', text: '(bo\'sh)' }));
      else box.appendChild(h('table', {}, [h('tr', {}, sec.columns.map(c => h('th', { text: c }))),
        ...sec.rows.map(r => h('tr', {}, r.map(c => h('td', { text: c == null ? '' : String(c) }))))]));
    } else if (sec.kind === 'pipeline') {
      box.appendChild(h('div', { style: { padding: '10px' } }, NL.renderPipeline(sec.data)));
    } else {
      box.appendChild(h('pre', { text: sec.text }));
    }
    return box;
  }

  NL.renderPipeline = function (pd) {
    const wrap = h('div', { class: 'pipe' });
    const arrow = () => h('div', { class: 'pipe-arrow', text: '→' });
    const parser = h('div', { class: 'stage-box parser' }, [h('b', { text: 'Parser' })]);
    ((pd.parsers[0] || {}).states || []).forEach(s => parser.appendChild(h('div', { class: 'pnode' }, [
      h('div', { class: 'n', text: s.name }),
      h('div', { class: 'd', text: (s.extracts.length ? 'extract ' + s.extracts.join(', ') : '—') + '\n→ ' + s.next.join(' | ') })])));
    if (!(pd.parsers[0] || {}).states) parser.appendChild(h('div', { class: 'note', text: 'holat yo\'q' }));
    wrap.append(parser);
    (pd.pipelines || []).forEach((pl, i) => {
      const nm = pl.name.toLowerCase();
      const cls = nm.includes('egress') ? 'egress' : 'ingress';
      const box = h('div', { class: 'stage-box ' + cls }, [h('b', { text: pl.name.replace(/^My/, '') + ' — match-action' })]);
      if (!pl.tables.length) box.appendChild(h('div', { class: 'note', text: 'jadval yo\'q' }));
      pl.tables.forEach(t => box.appendChild(h('div', { class: 'pnode' }, [
        h('div', { class: 'n', text: t.name + (t.size ? ` [${t.size}]` : '') }),
        h('div', { class: 'd', text: 'key: ' + (t.match.map(m => `${m.field} (${m.type})`).join(', ') || '—') }),
        h('div', { class: 'chips' }, t.actions.map(a => h('span', { text: a })))])));
      wrap.append(arrow(), box);
    });
    const dep = h('div', { class: 'stage-box deparser' }, [h('b', { text: 'Deparser' })]);
    ((pd.deparsers[0] || {}).order || []).forEach(x => dep.appendChild(h('div', { class: 'pnode' }, h('div', { class: 'n', text: 'emit ' + x }))));
    wrap.append(arrow(), dep);
    const extra = [];
    if (pd.headers && pd.headers.length) extra.push('Headerlar: ' + pd.headers.join(', '));
    if (pd.counters && pd.counters.length) extra.push('Counterlar: ' + pd.counters.join(', '));
    if (pd.registers && pd.registers.length) extra.push('Registerlar: ' + pd.registers.map(r => r.name + (r.size ? `[${r.size}]` : '')).join(', '));
    return h('div', {}, [wrap, extra.length ? h('div', { class: 'note', style: { marginTop: '8px' }, text: extra.join('   ·   ') }) : null]);
  };

  function portPanel(name) {
    const d = S.dev(name);
    const ports = S.portsOf(name);
    const panel = h('div', { class: 'ports' });
    const els = {};
    Object.keys(ports).map(Number).sort((a, b) => a - b).forEach(p => {
      const info = ports[p], l = S.link(info.lid);
      const ledLink = h('i'), ledAct = h('i');
      const rate = h('div', { text: '—' });
      els[p] = { ledLink, ledAct, rate, info };
      panel.appendChild(h('div', { class: 'port', title: `${S.ifname(name, p)} → ${info.peer}:eth${info.peerPort}` }, [
        h('div', { class: 'jack' }), h('div', { class: 'leds' }, [ledLink, ledAct]),
        h('div', { text: 'eth' + p }), h('div', { style: { color: '#7d88a8' }, text: '→ ' + info.peer }), rate,
        h('div', { style: { color: '#7d88a8' }, text: `${l.params.bw}M ${l.params.delay}` })]));
    });
    if (!Object.keys(ports).length) panel.appendChild(h('div', { class: 'note', style: { color: '#9aa6c0' }, text: 'Port ulanmagan' }));
    function update() {
      for (const [p, e] of Object.entries(els)) {
        const l = S.link(e.info.lid);
        if (!l) continue;
        const st = S.stats[e.info.lid];
        const up = S.running() && l.up !== false;
        e.ledLink.className = l.up === false ? 'down' : up ? 'up' : '';
        const tx = st ? (e.info.side === 'a' ? st.ab_bps : st.ba_bps) : 0;
        const rx = st ? (e.info.side === 'a' ? st.ba_bps : st.ab_bps) : 0;
        e.ledAct.className = up && (tx + rx) > 0 ? 'act' : '';
        e.rate.textContent = up ? `↑${NL.fmtBps(tx)}` : (l.up === false ? 'DOWN' : '—');
        e.rate.title = `TX ${NL.fmtBps(tx)} / RX ${NL.fmtBps(rx)}`;
      }
    }
    update();
    return { panel, update, title: `${(S.info.device_types[d.type] || {}).label} — old panel (portlar, link/activity LED)` };
  }

  function renderInspect(pane, name, which) {
    const bar = h('div', { class: 'btn-row', style: { alignItems: 'center' } });
    const out = h('div', { style: { display: 'flex', flexDirection: 'column', gap: '10px' } });
    const auto = h('input', { type: 'checkbox' });
    const status = h('span', { class: 'note' });
    bar.append(h('button', { class: 'btn neon sm', text: 'Yangilash', onclick: load }), h('label', { class: 'toggle' }, [auto, 'Avto (3s)']), status);
    pane.append(bar);
    let pp = null;
    if (which === 'hw') {
      pp = portPanel(name);
      pane.append(h('div', { class: 'isec' }, [h('h4', { text: pp.title }), h('div', { style: { padding: '10px' } }, pp.panel)]));
    }
    pane.append(out);
    let timer = null, unsub = null, loading = false;
    async function load() {
      if (!S.running()) {
        out.innerHTML = '';
        if (which === 'hw' && S.dev(name) && S.dev(name).type === 'p4switch') {
          const prog = (S.dev(name).config || {}).program;
          const src = S.project.p4_programs[prog];
          if (src) {
            try { const r = await NL.api.post('/api/p4/compile', { source: src });
              out.appendChild(renderSection({ title: `P4 pipeline: ${prog}` + (r.outline_only ? ' (manba tahlili)' : ''), kind: 'pipeline', data: r.pipeline })); }
            catch (e) { /* */ }
          }
        }
        out.appendChild(h('div', { class: 'note', text: 'Jonli jadvallar uchun tarmoqni ishga tushiring.' }));
        return;
      }
      if (loading) return;
      loading = true; status.textContent = 'yuklanmoqda...';
      try {
        const r = await NL.api.get('/api/net/inspect/' + name);
        out.innerHTML = '';
        const hwRe = /hardware|pipeline|navbat|qdisc|port|trace|STP/i;
        r.sections.filter(s => which === 'hw' ? (s.kind === 'pipeline' || hwRe.test(s.title)) : (s.kind !== 'pipeline' && !/hardware|trace/i.test(s.title)))
          .forEach(s => out.appendChild(renderSection(s)));
        status.textContent = (r.demo ? 'demo · ' : '') + NL.clock();
      } catch (e) { status.textContent = e.message; }
      finally { loading = false; }
    }
    auto.addEventListener('change', () => {
      clearInterval(timer);
      if (auto.checked) timer = setInterval(() => { if (pane.style.display !== 'none') load(); }, 3000);
    });
    if (pp) unsub = NL.on('stats', pp.update);
    const unsub2 = NL.on('status', load);
    return { show: load, dispose() { clearInterval(timer); if (unsub) unsub(); unsub2(); } };
  }

  // ---------- P4 dastur muharriri ----------
  NL.p4win = {
    open(prog) {
      NL.win.open('p4:' + prog, { title: prog + '.p4', sub: 'P4_16 · v1model (BMv2)', icon: NL.icon('p4switch', 22), w: 860, h: 620,
        render(body) {
          const code = ta(S.project.p4_programs[prog], 24);
          const log = h('pre', { class: 'log-out' });
          const pipe = h('div');
          const users = () => Object.entries(S.project.devices).filter(([, d]) => d.type === 'p4switch' && (d.config || {}).program === prog).map(([n]) => n);
          const save = () => { S.project.p4_programs[prog] = code.value; S.touch(); };
          code.addEventListener('keydown', (e) => {
            if (e.key === 'Tab') { e.preventDefault(); const s = code.selectionStart; code.setRangeText('    ', s, code.selectionEnd, 'end'); }
          });
          body.append(code, h('div', { class: 'btn-row' }, [
            h('button', { class: 'btn primary sm', text: 'Saqlash', onclick: () => { save(); NL.toast(`${prog} saqlandi`, 'ok'); } }),
            h('button', { class: 'btn neon sm', text: 'Tekshirish (p4c)', onclick: async () => {
              save(); log.textContent = 'kompilyatsiya...'; log.classList.remove('err');
              try { const r = await NL.api.post('/api/p4/compile', { source: code.value });
                log.textContent = (r.outline_only ? '[p4c yo\'q — faqat tuzilma tahlili]\n' : '') + r.log; log.classList.toggle('err', !r.ok && !r.outline_only);
                pipe.innerHTML = ''; pipe.appendChild(NL.renderPipeline(r.pipeline)); }
              catch (e) { log.textContent = e.message; log.classList.add('err'); }
            } }),
            h('button', { class: 'btn ghost sm', text: 'Ishlatayotgan switchlarga yuklash', onclick: async () => {
              save();
              if (!S.running()) { NL.toast('Tarmoq ishlamayapti', 'warn'); return; }
              const us = users(); if (!us.length) { NL.toast('Bu dasturni hech bir switch ishlatmayapti', 'warn'); return; }
              for (const n of us) {
                try { const r = await NL.api.post(`/api/net/p4/${n}/load`, { project: S.project }); log.textContent = `[${n}]\n` + r.log; log.classList.toggle('err', !r.ok); }
                catch (e) { log.textContent = `[${n}] ${e.message}`; log.classList.add('err'); }
              }
            } }),
            h('span', { class: 'note', text: 'Ishlatadi: ' + (users().join(', ') || '—') }),
          ]), log, pipe);
        } });
    },
  };

  // ---------- SDN kontroller muharriri ----------
  NL.sdnwin = {
    open(cname) {
      NL.win.open('sdn:' + cname, { title: cname, sub: 'SDN kontroller ilovasi · os-ken / Ryu (OpenFlow 1.3)', icon: NL.icon('sdnrouter', 22), w: 860, h: 640,
        render(body) {
          const c = S.project.controllers[cname];
          const code = ta(c.code, 24);
          const port = inp(c.port, '6653', { type: 'number', style: { width: '110px' } });
          const log = h('pre', { class: 'log-out' });
          code.addEventListener('keydown', (e) => {
            if (e.key === 'Tab') { e.preventDefault(); const s = code.selectionStart; code.setRangeText('    ', s, code.selectionEnd, 'end'); }
          });
          const save = () => { c.code = code.value; c.port = parseInt(port.value, 10) || 6653; S.touch(); };
          const users = Object.entries(S.project.devices).filter(([, d]) => (d.config || {}).controller === cname).map(([n]) => n);
          body.append(code, h('div', { class: 'btn-row', style: { alignItems: 'center' } }, [
            h('span', { class: 'note', text: 'OpenFlow port:' }), port,
            h('button', { class: 'btn primary sm', text: 'Saqlash', onclick: () => { save(); NL.toast(`${cname} saqlandi`, 'ok'); } }),
            h('button', { class: 'btn neon sm', text: 'Saqlash + qayta ishga tushirish', onclick: async () => {
              save();
              if (!S.running()) { NL.toast('Saqlandi. Tarmoq ishga tushganda kontroller ham ishga tushadi.', 'ok'); return; }
              log.textContent = 'qayta ishga tushirilmoqda...';
              try { const r = await NL.api.post(`/api/net/sdn/${cname}/restart`, { project: S.project }); log.textContent = r.log || 'OK'; }
              catch (e) { log.textContent = e.message; log.classList.add('err'); }
            } }),
            h('button', { class: 'btn ghost sm', text: 'Log', onclick: async () => {
              try { const r = await NL.api.get(`/api/net/sdn/${cname}/log`); log.textContent = r.log.slice(-6000) || '(bo\'sh)'; log.scrollTop = 1e9; }
              catch (e) { log.textContent = e.message; }
            } }),
            h('span', { class: 'note', text: 'Ulangan: ' + (users.join(', ') || '—') }),
          ]), log);
        } });
    },
  };
})(window.NL);
