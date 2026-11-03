/* NetLab — o'ng panel: tanlangan qurilma/kabel xususiyatlari. */
(function (NL) {
  'use strict';
  const S = NL.store, h = NL.h, F = () => NL.form;
  let root, lastKey = null, statsBox = null;

  NL.inspector = {
    init(el) {
      root = el;
      NL.on('change', render);
      NL.on('stats', () => statsBox && statsBox());
    },
  };

  function render() {
    if (!root || !S.project) return;
    const sel = S.selection;
    const key = sel ? sel.kind + ':' + sel.id + ':' + S.running() + ':' + JSON.stringify(sel.kind === 'link' ? S.link(sel.id) : S.dev(sel.id)) : 'none:' + S.running() + Object.keys(S.project.devices).length + Object.keys(S.project.links).length;
    if (key === lastKey) return;
    // forma maydoniga yozilayotganda qayta chizmaslik
    if (root.contains(document.activeElement) && document.activeElement.matches('input,select,textarea') && sel && lastKey && lastKey.startsWith(sel.kind + ':' + sel.id + ':')) return;
    lastKey = key;
    root.innerHTML = '';
    statsBox = null;
    if (!sel) return renderProject();
    if (sel.kind === 'device' && S.dev(sel.id)) return renderDevice(sel.id);
    if (sel.kind === 'link' && S.link(sel.id)) return renderLink(sel.id);
    renderProject();
  }

  function head(icon, title, sub) {
    return h('div', { class: 'insp-head' }, [h('span', { class: 'ico', html: icon }),
      h('div', {}, [h('div', { class: 't', text: title }), h('div', { class: 's', text: sub })])]);
  }

  function renderProject() {
    const p = S.project;
    const types = {};
    Object.values(p.devices).forEach(d => { types[d.type] = (types[d.type] || 0) + 1; });
    const body = h('div', { class: 'insp-body' });
    const kv = h('div', { class: 'kv' });
    const add = (k, v) => kv.append(h('div', { class: 'k', text: k }), h('div', { class: 'v', text: v }));
    add('Loyiha', p.name);
    add('Qurilmalar', String(Object.keys(p.devices).length));
    Object.entries(types).forEach(([t, n]) => add('  ' + ((S.info.device_types[t] || {}).label || t), String(n)));
    add('Kabellar', String(Object.keys(p.links).length));
    add('P4 dasturlar', String(Object.keys(p.p4_programs).length));
    add('Kontrollerlar', String(Object.keys(p.controllers).length));
    add('Driver', S.info.driver + (S.running() ? ' · ishlayapti' : ''));
    const notes = h('textarea', { class: 'input', rows: 6, placeholder: 'Loyiha izohlari (vazifa, IP reja, ...)' });
    notes.value = p.notes || '';
    notes.addEventListener('change', () => { p.notes = notes.value; S.touch(); });
    body.append(kv, F().field('Izohlar', notes),
      h('div', { class: 'note', html: '<b>Klaviatura:</b> V — tanlash, C — kabel, D — o\'chirish, Del — tanlanganni o\'chirish, Enter — qurilmaga kirish, Esc — bekor, Ctrl+S — saqlash.<br><b>Sichqoncha:</b> bo\'sh joyni sudrash — siljitish, g\'ildirak — zoom, Alt — panjarasiz ko\'chirish.' }));
    root.append(head(NL.ui.ico('<path d="M3 7h6l2 2h10v10H3z"/>'), 'Loyiha', 'Hech narsa tanlanmagan'), body);
  }

  function renderDevice(name) {
    const d = S.dev(name);
    const label = (S.info.device_types[d.type] || {}).label || d.type;
    const body = h('div', { class: 'insp-body' });
    const kv = h('div', { class: 'kv' });
    const add = (k, v) => kv.append(h('div', { class: 'k', text: k }), h('div', { class: 'v', text: v }));
    add('Tur', label);
    add('ID / dpid', `${d.id} / ${d.id.toString(16).padStart(16, '0')}`);
    const cfg = d.config || {};
    if (cfg.mode) add('Rejim', cfg.mode + (cfg.stp ? ' + STP' : ''));
    if (cfg.controller) add('Kontroller', cfg.controller);
    if (cfg.program) add('P4 dastur', cfg.program);
    if (cfg.daemons) add('FRR', ['zebra', 'staticd', ...cfg.daemons].join(', '));
    if (cfg.gateway) add('Gateway', cfg.gateway);
    const ports = S.portsOf(name);
    Object.keys(ports).map(Number).sort((a, b) => a - b).forEach(p => {
      const ifn = S.ifname(name, p);
      const ip = ((cfg.interfaces || {})[ifn] || {}).ip;
      add('eth' + p, `→ ${ports[p].peer}:eth${ports[p].peerPort}${ip ? '\n' + ip : ''}`);
    });
    const open = (tab) => NL.devwin.open(name, tab);
    body.append(kv,
      h('div', { class: 'btn-row' }, [
        h('button', { class: 'btn primary sm', text: 'Qurilmaga kirish', onclick: () => open() }),
        h('button', { class: 'btn neon sm', text: 'CLI', onclick: () => open('cli') }),
        h('button', { class: 'btn ghost sm', text: 'Hardware', onclick: () => open('hw') }),
      ]),
      h('div', { class: 'btn-row' }, [
        h('button', { class: 'btn ghost sm', text: 'Sozlama', onclick: () => open('cfg') }),
        h('button', { class: 'btn ghost sm', text: 'Jadvallar', onclick: () => open('tbl') }),
        h('button', { class: 'btn danger sm', text: 'O\'chirish', onclick: () => S.removeDevice(name) }),
      ]));
    root.append(head(NL.icon(d.type, 30), name, label), body);
  }

  function renderLink(lid) {
    const l = S.link(lid);
    const body = h('div', { class: 'insp-body' });
    const p = l.params || {};
    const bw = F().inp(p.bw, '100', { type: 'number', min: 0.1, step: 'any' });
    const delay = F().inp(p.delay, '1ms');
    const jitter = F().inp(p.jitter, '0ms');
    const loss = F().inp(p.loss, '0', { type: 'number', min: 0, max: 100, step: 'any' });
    const queue = F().inp(p.queue, '1000', { type: 'number', min: 1 });
    const upChk = h('input', { type: 'checkbox' }); upChk.checked = l.up !== false;
    upChk.addEventListener('change', () => S.updateLink(lid, null, upChk.checked));
    const apply = async () => {
      const ok = await S.updateLink(lid, { bw: parseFloat(bw.value), delay: delay.value.trim(), jitter: jitter.value.trim(),
        loss: parseFloat(loss.value) || 0, queue: parseInt(queue.value, 10) || 1000 });
      if (ok) NL.toast(S.running() ? 'Kabel parametrlari qo\'llandi (tc)' : 'Saqlandi', 'ok');
    };
    const sparkA = NL.s('path', { class: 'a' }), sparkB = NL.s('path', { class: 'b' });
    const spark = NL.s('svg', { class: 'spark', viewBox: '0 0 240 46', preserveAspectRatio: 'none' });
    spark.append(sparkA, sparkB);
    const rateA = h('div'), rateB = h('div'), barA = h('i'), barB = h('i');
    const sbox = h('div', { class: 'stat-bars' }, [
      h('div', { class: 'row spread' }, [h('span', { text: `${l.a.dev} → ${l.b.dev}` }), rateA]), h('div', { class: 'bar' }, barA),
      h('div', { class: 'row spread' }, [h('span', { text: `${l.b.dev} → ${l.a.dev}` }), rateB]), h('div', { class: 'bar' }, barB),
      spark]);
    statsBox = () => {
      const st = S.stats[lid] || {};
      const cap = (parseFloat(l.params.bw) || 100) * 1e6;
      rateA.textContent = NL.fmtBps(st.ab_bps) + (st.ab_pps ? ` · ${st.ab_pps} pps` : '');
      rateB.textContent = NL.fmtBps(st.ba_bps) + (st.ba_pps ? ` · ${st.ba_pps} pps` : '');
      barA.style.width = Math.min(100, (st.ab_bps || 0) / cap * 100) + '%';
      barB.style.width = Math.min(100, (st.ba_bps || 0) / cap * 100) + '%';
      const hist = S.history[lid] || [];
      const mx = Math.max(1, ...hist.map(x => Math.max(x.ab, x.ba)));
      const path = (k) => hist.map((x, i) => `${i ? 'L' : 'M'}${(i / 59) * 240},${44 - (x[k] / mx) * 40}`).join('');
      sparkA.setAttribute('d', path('ab')); sparkB.setAttribute('d', path('ba'));
    };
    statsBox();
    body.append(
      h('div', { class: 'kv' }, [h('div', { class: 'k', text: 'A' }), h('div', { class: 'v', text: `${l.a.dev}:eth${l.a.port}` }),
        h('div', { class: 'k', text: 'B' }), h('div', { class: 'v', text: `${l.b.dev}:eth${l.b.port}` })]),
      h('label', { class: 'toggle' }, [upChk, 'Kabel ulangan (UP) — o\'chirsangiz link uziladi']),
      h('div', { class: 'f-row' }, [F().field('Tezlik (Mbit/s)', bw), F().field('Navbat (paket)', queue)]),
      h('div', { class: 'f-row' }, [F().field('Kechikish', delay), F().field('Jitter', jitter)]),
      F().field('Yo\'qotish (loss %)', loss),
      h('div', { class: 'btn-row' }, [h('button', { class: 'btn primary sm', text: 'Qo\'llash', onclick: apply }),
        h('button', { class: 'btn danger sm', text: 'Kabelni o\'chirish', onclick: () => S.removeLink(lid) })]),
      h('div', { class: 'note', text: 'Fizik qatlam: tc HTB (tezlik) + netem (kechikish/jitter/loss) + navbat uzunligi. Tarmoq ishlayotganda darhol qo\'llanadi.' }),
      S.running() ? h('div', { class: 'isec' }, [h('h4', { text: 'Jonli trafik' }), h('div', { style: { padding: '10px' } }, sbox)]) : null);
    root.append(head(NL.ui.ico('<circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="6" r="2.5"/><path d="M8 16 16 8"/>'), 'Kabel ' + lid,
      `${l.a.dev} ↔ ${l.b.dev}`), body);
  }
})(window.NL);
