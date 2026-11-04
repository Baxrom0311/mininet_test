/* NetLab — holat (loyiha modeli) va jonli sinxronizatsiya.
   Frontend modelning asosiy manbai. Tarmoq ishlayotgan bo'lsa, har bir
   o'zgarish backendga ham yuboriladi (qurilma/kabel qo'shish, sozlama...). */
(function (NL) {
  'use strict';

  const S = NL.store = {
    project: null,
    info: null,            // /api/info
    templates: { p4: {}, sdn: {} },
    status: { running: false },
    selection: null,       // {kind:'device'|'link', id}
    stats: {},             // link_id -> oxirgi statistika
    history: {},           // link_id -> [{t, ab, ba}]
    dirty: false,
  };

  const TYPE_PREFIX = { host: 'pc', server: 'srv', switch: 's', p4switch: 'p', router: 'r', sdnrouter: 'sr', wifiap: 'ap' };

  S.empty = (name) => ({ version: 1, name: name || 'untitled', devices: {}, links: {}, p4_programs: {}, controllers: {}, scenarios: {}, notes: '' });

  S.load = function (project, opts) {
    S.project = JSON.parse(JSON.stringify(project));
    ['devices', 'links', 'p4_programs', 'controllers', 'scenarios'].forEach(k => { S.project[k] = S.project[k] || {}; });
    S.selection = null;
    S.dirty = !!(opts && opts.dirty);
    NL.emit('project');
    NL.emit('change');
  };

  S.touch = function () {
    S.dirty = true;
    NL.storage.set('netlab.draft', S.project);
    NL.emit('change');
  };

  S.running = () => !!(S.status && S.status.running);
  S.dev = (n) => S.project.devices[n];
  S.link = (id) => S.project.links[id];

  S.newName = function (type) {
    const pre = TYPE_PREFIX[type] || 'd';
    for (let i = 1; ; i++) if (!S.project.devices[pre + i]) return pre + i;
  };
  S.newId = () => Object.values(S.project.devices).reduce((m, d) => Math.max(m, d.id || 0), 0) + 1;
  S.newLinkId = function () { for (let i = 1; ; i++) if (!S.project.links['l' + i]) return 'l' + i; };

  S.portsOf = function (dev) {   // {port: {lid, peer, peerPort}}
    const out = {};
    for (const [lid, l] of Object.entries(S.project.links)) {
      if (l.a.dev === dev) out[l.a.port] = { lid, peer: l.b.dev, peerPort: l.b.port, side: 'a' };
      if (l.b.dev === dev) out[l.b.port] = { lid, peer: l.a.dev, peerPort: l.a.port, side: 'b' };
    }
    return out;
  };
  S.freePort = function (dev) { const used = S.portsOf(dev); for (let p = 1; ; p++) if (!used[p]) return p; };
  S.ifname = (dev, port) => `${dev}-eth${port}`;
  S.mac = (id, port) => '00:00:00:' + [(id >> 8) & 255, id & 255, port & 255].map(x => x.toString(16).padStart(2, '0')).join(':');

  S.defaultConfig = function (type) {
    switch (type) {
      case 'host': case 'server': return { interfaces: {}, gateway: '', routes: [] };
      case 'switch': return { mode: 'standalone', stp: false, controller: '' };
      case 'p4switch': {
        const prog = Object.keys(S.project.p4_programs)[0] || '';
        return { program: prog, runtime: '' };
      }
      case 'router': return { interfaces: {}, daemons: ['ospfd'], startup: '', routes: [] };
      case 'sdnrouter': return { interfaces: {}, controller: Object.keys(S.project.controllers)[0] || '', routes: [] };
      case 'wifiap': return { standard: '80211ax', band: 5, width: 20, tx_power: 20, ssid: '' };
      default: return {};
    }
  };

  async function live(fn, okMsg) {
    if (!S.running()) return true;
    try { await fn(); if (okMsg) NL.toast(okMsg, 'ok'); return true; }
    catch (e) { NL.toast(e.message, 'error'); return false; }
  }
  S.live = live;
  const P = () => ({ project: S.project });

  // ---------- qurilmalar ----------
  S.addDevice = async function (type, x, y) {
    if (type === 'p4switch' && !Object.keys(S.project.p4_programs).length) {
      const tpl = S.templates.p4.l2_switch;
      if (tpl) S.project.p4_programs.l2_switch = tpl;
    }
    if ((type === 'sdnrouter') && !Object.keys(S.project.controllers).length) {
      const tpl = S.templates.sdn.l3_router;
      if (tpl) S.project.controllers.c1 = { code: tpl, port: 6653 };
    }
    const name = S.newName(type);
    S.project.devices[name] = { id: S.newId(), type, x: Math.round(x), y: Math.round(y), config: S.defaultConfig(type) };
    S.selection = { kind: 'device', id: name };
    S.touch();
    await live(() => NL.api.post('/api/net/device/' + name, P()));
    return name;
  };

  S.removeDevice = async function (name) {
    const ports = S.portsOf(name);
    Object.values(ports).forEach(p => delete S.project.links[p.lid]);
    delete S.project.devices[name];
    if (S.selection && S.selection.id === name) S.selection = null;
    NL.emit('device-removed', name);
    S.touch();
    await live(() => NL.api.del('/api/net/device/' + name));
  };

  S.renameDevice = function (oldName, newName) {
    if (S.running()) { NL.toast('Tarmoq ishlayotganda nom o\'zgarmaydi — avval to\'xtating', 'error'); return false; }
    if (!/^[a-z][a-z0-9]{0,7}$/.test(newName)) { NL.toast('Nom: kichik lotin harf bilan, 1-8 belgi [a-z0-9]', 'error'); return false; }
    if (S.project.devices[newName]) { NL.toast('Bu nom band', 'error'); return false; }
    const d = S.project.devices[oldName];
    const ifs = (d.config || {}).interfaces;
    if (ifs) {
      const n = {};
      for (const [k, v] of Object.entries(ifs)) n[k.replace(oldName + '-', newName + '-')] = v;
      d.config.interfaces = n;
    }
    S.project.devices[newName] = d;
    delete S.project.devices[oldName];
    for (const l of Object.values(S.project.links)) {
      if (l.a.dev === oldName) l.a.dev = newName;
      if (l.b.dev === oldName) l.b.dev = newName;
    }
    S.selection = { kind: 'device', id: newName };
    NL.emit('device-removed', oldName);
    S.touch();
    return true;
  };

  S.saveDeviceConfig = async function (name, cfg) {
    S.project.devices[name].config = cfg;
    S.touch();
    return live(() => NL.api.put(`/api/net/device/${name}/config`, P()), S.running() ? `${name}: qo'llandi` : null);
  };

  // ---------- kabellar ----------
  S.addLink = async function (a, b) {
    if (a === b) return null;
    const lid = S.newLinkId();
    // Wi-Fi AP <-> host/server/router: simsiz (qurilma allaqachon boshqa AP'ga ulanmagan bo'lsa)
    const ta = S.dev(a).type, tb = S.dev(b).type, L3 = ['host', 'server', 'router'];
    const sta = ta === 'wifiap' && L3.includes(tb) ? b : tb === 'wifiap' && L3.includes(ta) ? a : null;
    const hasWifi = sta && Object.values(S.project.links).some(x => x.params && x.params.medium === 'wifi' && (x.a.dev === sta || x.b.dev === sta));
    const medium = sta && !hasWifi ? 'wifi' : 'wire';
    const l = { a: { dev: a, port: S.freePort(a) }, b: { dev: b, port: S.freePort(b) },
      params: { bw: 100, delay: '1ms', jitter: '0ms', loss: 0, queue: 1000, medium }, up: true };
    S.project.links[lid] = l;
    S.selection = { kind: 'link', id: lid };
    S.touch();
    await live(() => NL.api.post('/api/net/link/' + lid, P()));
    return lid;
  };

  S.removeLink = async function (lid) {
    const l = S.project.links[lid];
    if (!l) return;
    for (const side of ['a', 'b']) {   // shu portdagi IP sozlamasini tozalash
      const d = S.project.devices[l[side].dev];
      const ifs = d && d.config && d.config.interfaces;
      if (ifs) delete ifs[S.ifname(l[side].dev, l[side].port)];
    }
    delete S.project.links[lid];
    if (S.selection && S.selection.id === lid) S.selection = null;
    S.touch();
    await live(() => NL.api.del('/api/net/link/' + lid));
  };

  S.updateLink = async function (lid, params, up) {
    const l = S.project.links[lid];
    if (params) l.params = Object.assign({}, l.params, params);
    if (up !== undefined) l.up = up;
    S.touch();
    return live(() => NL.api.patch('/api/net/link/' + lid, { params: params ? l.params : undefined, up }));
  };

  // ---------- tarmoq ----------
  S.start = async function () {
    const v = await NL.api.post('/api/validate', S.project);
    if (v.errors.length) { NL.toast('Loyiha xatolari:\n• ' + v.errors.slice(0, 6).join('\n• '), 'error'); return; }
    NL.emit('busy', 'Tarmoq ishga tushirilmoqda...');
    try { S.status = await NL.api.post('/api/net/start', P()); NL.toast('Tarmoq ishga tushdi', 'ok'); }
    catch (e) { NL.toast(e.message, 'error'); }
    finally { NL.emit('busy', null); NL.emit('status'); NL.emit('change'); }
  };
  S.stop = async function () {
    NL.emit('busy', 'To\'xtatilmoqda...');
    try { S.status = await NL.api.post('/api/net/stop'); }
    catch (e) { NL.toast(e.message, 'error'); }
    finally { S.stats = {}; NL.emit('busy', null); NL.emit('status'); NL.emit('change'); }
  };

  // ---------- saqlash ----------
  S.save = async function (name) {
    name = name || S.project.name;
    S.project.name = name;
    const r = await NL.api.put('/api/projects/' + encodeURIComponent(name), S.project);
    S.dirty = false;
    NL.emit('change');
    NL.toast(`Saqlandi: ${name}` + (r.warnings && r.warnings.length ? `\n(${r.warnings.length} ogohlantirish)` : ''), 'ok');
  };

  // backend hodisalari
  NL.on('ev:state', (st) => { S.status = st; NL.emit('status'); NL.emit('change'); });
  NL.on('ev:stats', (ev) => {
    const now = Date.now();
    for (const [lid, s] of Object.entries(ev.links || {})) {
      S.stats[lid] = s;
      const h = S.history[lid] = S.history[lid] || [];
      h.push({ t: now, ab: s.ab_bps, ba: s.ba_bps });
      if (h.length > 60) h.shift();
    }
    NL.emit('stats');
  });
  NL.on('ev:link', (ev) => {
    const l = S.project && S.project.links[ev.link];
    if (l) { l.up = ev.up; if (ev.params) l.params = ev.params; NL.emit('change'); }
  });
})(window.NL);
