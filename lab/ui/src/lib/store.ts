/** NetLab holati (zustand). Frontend loyiha modelining asosiy manbai; tarmoq ishlayotganda
 *  har bir o'zgarish backendga ham yuboriladi (qurilma/kabel qo'shish, sozlama, ...). */
import { create } from 'zustand'
import { api } from './api'
import { on } from './bus'
import { storage } from './format'
import { renameScenarioDevice } from './scenario'
import { toast } from '@/components/ui/toast'
import type { Device, DeviceType, Info, Link, LinkParams, LinkStats, Project, ScenarioEvent, Selection, Status } from './types'

export type Mode = 'select' | 'link' | 'delete' | 'place' | 'pick'
export type WinKind = 'device' | 'p4' | 'sdn' | 'scenario' | 'results' | 'telemetry' | 'pdu' | 'security'
export interface Win { id: string; kind: WinKind; ref: string; tab?: string; z: number; x: number; y: number; w: number; h: number; arg?: any }

const TYPE_PREFIX: Record<DeviceType, string> = { host: 'pc', server: 'srv', switch: 's', p4switch: 'p', router: 'r', sdnrouter: 'sr', wifiap: 'ap' }
const L3 = ['host', 'server', 'router']

export const emptyProject = (name = 'untitled'): Project => ({
  version: 1, name, devices: {}, links: {}, p4_programs: {}, controllers: {}, scenarios: {}, notes: '', meters_per_px: 0.25,
})

interface State {
  info: Info | null
  templates: { p4: Record<string, string>; sdn: Record<string, string> }
  status: Status
  project: Project
  selection: Selection
  dirty: boolean
  stats: Record<string, LinkStats>
  history: Record<string, { t: number; ab: number; ba: number }[]>
  mode: Mode
  placeType: DeviceType | null
  linkSrc: string | null
  overlay: ScenarioEvent[]
  pickCb: ((x: number, y: number) => void) | null
  busy: string | null
  wsOk: boolean
  wins: Win[]
  zTop: number
  theme: 'dark' | 'light'
  canUndo: boolean
  canRedo: boolean
}

export const useStore = create<State>(() => ({
  info: null, templates: { p4: {}, sdn: {} }, status: { running: false }, project: emptyProject(), selection: null, dirty: false,
  stats: {}, history: {}, mode: 'select', placeType: null, linkSrc: null, overlay: [], pickCb: null, busy: null, wsOk: false, wins: [], zTop: 10,
  theme: document.documentElement.classList.contains('dark') ? 'dark' : 'light',
  canUndo: false, canRedo: false,
}))

const S = () => useStore.getState()
const set = useStore.setState
const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x))

// ---------------- bekor qilish / qaytarish ----------------
// Har mutate() dan oldingi loyiha holati saqlanadi. Tez ketma-ket o'zgarishlar (yozish, 700 ms ichida)
// bitta qadamga birlashtiriladi. Tarmoq ishlayotganda o'chirilgan: backend holati bilan farqlanib qoladi.
const undoStack: Project[] = [], redoStack: Project[] = []
let lastPush = 0
const HISTORY_MAX = 100
const syncFlags = () => set({ canUndo: undoStack.length > 0, canRedo: redoStack.length > 0 })

export function pushHistory(prev: Project, coalesce = true) {
  const now = Date.now()
  if (!(coalesce && now - lastPush < 700 && undoStack.length)) {
    undoStack.push(prev)
    if (undoStack.length > HISTORY_MAX) undoStack.shift()
  }
  lastPush = now
  redoStack.length = 0
  syncFlags()
}

function restore(p: Project) {
  set((s) => ({
    project: p, dirty: true,
    selection: s.selection && (s.selection.kind === 'device' ? p.devices[s.selection.id] : p.links[s.selection.id]) ? s.selection : null,
    wins: s.wins.filter((w) => w.kind === 'results' || w.kind === 'telemetry' || w.kind === 'pdu' || w.kind === 'security' || (w.kind === 'device' ? p.devices[w.ref] : w.kind === 'p4' ? p.p4_programs[w.ref] != null
      : w.kind === 'sdn' ? p.controllers[w.ref] : p.scenarios?.[w.ref])),
  }))
  storage.set('netlab.draft', p)
}

export function undo() {
  if (running()) return toast("Tarmoq ishlayotganda bekor qilib bo'lmaydi — avval to'xtating", 'warn')
  const prev = undoStack.pop(); if (!prev) return
  redoStack.push(S().project); lastPush = 0
  restore(prev); syncFlags()
}
export function redo() {
  if (running()) return toast("Tarmoq ishlayotganda qaytarib bo'lmaydi — avval to'xtating", 'warn')
  const next = redoStack.pop(); if (!next) return
  undoStack.push(S().project); lastPush = 0
  restore(next); syncFlags()
}
const clearHistory = () => { undoStack.length = 0; redoStack.length = 0; lastPush = 0; syncFlags() }

/** Loyihani o'zgartirish: draft (nusxa) ustida ishlaydi, tarixga yozadi va dirty belgisini qo'yadi. */
export function mutate(fn: (p: Project) => void, opts: { history?: boolean } = {}) {
  const before = S().project
  const p = clone(before)
  fn(p)
  if (opts.history !== false) pushHistory(before)
  set({ project: p, dirty: true })
  storage.set('netlab.draft', p)
  return p
}

export const running = () => !!S().status.running

async function live(fn: () => Promise<unknown>, okMsg?: string, before?: Project) {
  if (!running()) return true
  try { await fn(); if (okMsg) toast(okMsg, 'ok'); return true } catch (e: any) {
    toast(e.message, 'error')
    try {
      const state = await api.get<{ project: Project | null; running: boolean }>('/api/net/project')
      if (state.project && state.running) {
        // Keep local scenario/code edits, but restore authoritative network state.
        restore({ ...S().project, devices: state.project.devices, links: state.project.links })
      } else if (before) restore(before)
    } catch { if (before) restore(before); toast('Ulanishni tiklab, sahifani yangilang', 'warn') }
    return false
  }
}

export function loadProject(p: Project, dirty = false) {
  const q = clone(p)
  for (const k of ['devices', 'links', 'p4_programs', 'controllers', 'scenarios'] as const) (q as any)[k] ||= {}
  set({ project: q, selection: null, dirty, wins: [], overlay: [] })
  storage.set('netlab.draft', q)
  clearHistory()
}

export const portsOf = (p: Project, dev: string) => {
  const out: Record<number, { lid: string; peer: string; peerPort: number; side: 'a' | 'b' }> = {}
  for (const [lid, l] of Object.entries(p.links)) {
    if (l.a.dev === dev) out[l.a.port] = { lid, peer: l.b.dev, peerPort: l.b.port, side: 'a' }
    if (l.b.dev === dev) out[l.b.port] = { lid, peer: l.a.dev, peerPort: l.a.port, side: 'b' }
  }
  return out
}
const freePort = (p: Project, dev: string) => { const u = portsOf(p, dev); let i = 1; while (u[i]) i++; return i }
export const ifname = (dev: string, port: number) => `${dev}-eth${port}`
export const devMac = (id: number, port: number) => '00:00:00:' + [(id >> 8) & 255, id & 255, port & 255].map((x) => x.toString(16).padStart(2, '0')).join(':')

function defaultConfig(p: Project, type: DeviceType): Record<string, any> {
  switch (type) {
    case 'host': case 'server': return { interfaces: {}, gateway: '', routes: [] }
    case 'switch': return { mode: 'standalone', stp: false, controller: '' }
    case 'p4switch': return { program: Object.keys(p.p4_programs)[0] || '', runtime: '' }
    case 'router': return { interfaces: {}, daemons: ['ospfd'], startup: '', routes: [] }
    case 'sdnrouter': return { interfaces: {}, controller: Object.keys(p.controllers)[0] || '', routes: [] }
    case 'wifiap': return { standard: '80211ax', band: 5, width: 20, tx_power: 20, ssid: '', channel: 0 }
  }
}

// ---------------- qurilmalar ----------------
export async function addDevice(type: DeviceType, x: number, y: number) {
  const before = S().project
  let name = ''
  const p = mutate((p) => {
    if (type === 'p4switch' && !Object.keys(p.p4_programs).length && S().templates.p4.l2_switch) p.p4_programs.l2_switch = S().templates.p4.l2_switch
    if (type === 'sdnrouter' && !Object.keys(p.controllers).length && S().templates.sdn.l3_router) p.controllers.c1 = { code: S().templates.sdn.l3_router, port: 6653 }
    const pre = TYPE_PREFIX[type]
    let i = 1; while (p.devices[pre + i]) i++
    name = pre + i
    const id = Object.values(p.devices).reduce((m, d) => Math.max(m, d.id || 0), 0) + 1
    p.devices[name] = { id, type, x: Math.round(x), y: Math.round(y), config: defaultConfig(p, type) }
  })
  set({ selection: { kind: 'device', id: name } })
  if (!await live(() => api.post('/api/net/device/' + name, { project: p }), undefined, before)) return null
  return name
}

export async function removeDevice(name: string) {
  const before = S().project
  mutate((p) => {
    for (const x of Object.values(portsOf(p, name))) delete p.links[x.lid]
    delete p.devices[name]
  })
  set((s) => ({ selection: s.selection?.id === name ? null : s.selection, wins: s.wins.filter((w) => !(w.kind === 'device' && w.ref === name)) }))
  await live(() => api.del('/api/net/device/' + name), undefined, before)
}

export function renameDevice(oldName: string, newName: string) {
  if (running()) { toast("Tarmoq ishlayotganda nom o'zgarmaydi — avval to'xtating", 'error'); return false }
  if (!/^[a-z][a-z0-9]{0,7}$/.test(newName)) { toast('Nom: kichik lotin harf bilan, 1-8 belgi [a-z0-9]', 'error'); return false }
  if (S().project.devices[newName]) { toast('Bu nom band', 'error'); return false }
  mutate((p) => {
    const d = p.devices[oldName]
    if (d.config?.interfaces) {
      const n: Record<string, any> = {}
      for (const [k, v] of Object.entries(d.config.interfaces)) n[k.replace(oldName + '-', newName + '-')] = v
      d.config.interfaces = n
    }
    p.devices[newName] = d; delete p.devices[oldName]
    for (const l of Object.values(p.links)) { if (l.a.dev === oldName) l.a.dev = newName; if (l.b.dev === oldName) l.b.dev = newName }
    for (const sc of Object.values(p.scenarios || {})) {
      renameScenarioDevice(sc, oldName, newName)
    }
  })
  set((s) => ({ selection: { kind: 'device', id: newName }, wins: s.wins.map((w) => w.kind === 'device' && w.ref === oldName ? { ...w, ref: newName, id: 'dev:' + newName } : w) }))
  return true
}

export function moveDevice(name: string, x: number, y: number, persist = false) {
  const p = S().project
  const d = p.devices[name]; if (!d) return
  const np = { ...p, devices: { ...p.devices, [name]: { ...d, x, y } } }
  set({ project: np, dirty: persist ? true : S().dirty })
  if (persist) storage.set('netlab.draft', np)
}

export async function saveDeviceConfig(name: string, cfg: Record<string, any>) {
  const before = S().project
  const p = mutate((p) => { p.devices[name].config = cfg })
  return live(() => api.put(`/api/net/device/${name}/config`, { project: p }), running() ? `${name}: qo'llandi` : undefined, before)
}

// ---------------- kabellar ----------------
export async function addLink(a: string, b: string) {
  const before = S().project
  if (a === b) return null
  let lid = ''
  const p = mutate((p) => {
    let i = 1; while (p.links['l' + i]) i++
    lid = 'l' + i
    const ta = p.devices[a].type, tb = p.devices[b].type
    const sta = ta === 'wifiap' && L3.includes(tb) ? b : tb === 'wifiap' && L3.includes(ta) ? a : null
    const hasWifi = sta && Object.values(p.links).some((x) => x.params?.medium === 'wifi' && (x.a.dev === sta || x.b.dev === sta))
    const params: LinkParams = { bw: 100, delay: '1ms', jitter: '0ms', loss: 0, queue: 1000, medium: sta && !hasWifi ? 'wifi' : 'wire' }
    p.links[lid] = { a: { dev: a, port: freePort(p, a) }, b: { dev: b, port: freePort(p, b) }, params, up: true }
  })
  set({ selection: { kind: 'link', id: lid } })
  if (!await live(() => api.post('/api/net/link/' + lid, { project: p }), undefined, before)) return null
  return lid
}

export async function removeLink(lid: string) {
  const before = S().project
  if (!S().project.links[lid]) return
  mutate((p) => {
    const l = p.links[lid]
    for (const side of ['a', 'b'] as const) {
      const ifs = p.devices[l[side].dev]?.config?.interfaces
      if (ifs) delete ifs[ifname(l[side].dev, l[side].port)]
    }
    delete p.links[lid]
  })
  set((s) => ({ selection: s.selection?.id === lid ? null : s.selection }))
  await live(() => api.del('/api/net/link/' + lid), undefined, before)
}

export async function updateLink(lid: string, params?: Partial<LinkParams> | null, up?: boolean) {
  const before = S().project
  const p = mutate((p) => {
    const l = p.links[lid]
    if (params) l.params = { ...l.params, ...params }
    if (up !== undefined) l.up = up
  })
  return live(() => api.patch('/api/net/link/' + lid, { params: params ? p.links[lid].params : undefined, up }), undefined, before)
}

// ---------------- tarmoq / saqlash ----------------
export async function startNet() {
  const v = await api.post<{ errors: string[] }>('/api/validate', S().project)
  if (v.errors.length) { toast('Loyiha xatolari:\n• ' + v.errors.slice(0, 6).join('\n• '), 'error'); return }
  set({ busy: 'Tarmoq ishga tushirilmoqda...' })
  try { set({ status: await api.post('/api/net/start', { project: S().project }) }); toast('Tarmoq ishga tushdi', 'ok') }
  catch (e: any) { toast(e.message, 'error') } finally { set({ busy: null }) }
}
export async function stopNet() {
  set({ busy: "To'xtatilmoqda..." })
  try { set({ status: await api.post('/api/net/stop') }) } catch (e: any) { toast(e.message, 'error') } finally { set({ busy: null, stats: {} }) }
}
export async function saveProject(name?: string) {
  const n = name || S().project.name
  const p = mutate((p) => { p.name = n }, { history: false })
  const r = await api.put<{ warnings: string[] }>('/api/projects/' + encodeURIComponent(n), p)
  set({ dirty: false })
  toast(`Saqlandi: ${n}` + (r.warnings?.length ? `\n(${r.warnings.length} ogohlantirish)` : ''), 'ok')
}

// ---------------- kanvas rejimi / oynalar ----------------
export const setMode = (mode: Mode, placeType: DeviceType | null = null) => set({ mode, placeType, linkSrc: null, ...(mode !== 'pick' ? { pickCb: null } : {}) })
export const pickPoint = (cb: (x: number, y: number) => void) => set({ mode: 'pick', pickCb: cb })
export const select = (selection: Selection) => set({ selection })

export function openWin(kind: WinKind, ref: string, opts: { tab?: string; w?: number; h?: number; arg?: any } = {}) {
  const id = `${kind}:${ref}`
  const s = S()
  const ex = s.wins.find((w) => w.id === id)
  const arg = opts.arg && kind === 'results' ? { ...opts.arg, project: s.project.name } : opts.arg
  const z = s.zTop + 1
  if (ex) { set({ zTop: z, wins: s.wins.map((w) => w.id === id ? { ...w, z, tab: opts.tab || w.tab, arg: arg ?? w.arg } : w) }); return }
  const off = (s.wins.length * 28) % 200
  const w: Win = { id, kind, ref, tab: opts.tab, z, x: Math.min(innerWidth - 480, 290 + off), y: Math.min(innerHeight - 340, 80 + off), w: opts.w || 760, h: opts.h || 540, arg }
  set({ zTop: z, wins: [...s.wins, w] })
}
export const closeWin = (id: string) => set((s) => ({ wins: s.wins.filter((w) => w.id !== id) }))
export const focusWin = (id: string) => set((s) => { const z = s.zTop + 1; return { zTop: z, wins: s.wins.map((w) => w.id === id ? { ...w, z } : w) } })
export const updateWin = (id: string, patch: Partial<Win>) => set((s) => ({ wins: s.wins.map((w) => w.id === id ? { ...w, ...patch } : w) }))

// ---------------- backend hodisalari ----------------
on('ev:state', (st: Status) => set({ status: st }))
on('ws', (ok: boolean) => set({ wsOk: ok }))
on('ev:stats', (ev: { links: Record<string, LinkStats> }) => {
  const now = Date.now()
  const s = S()
  const stats = { ...s.stats }, history = { ...s.history }
  for (const [lid, st] of Object.entries(ev.links || {})) {
    stats[lid] = st
    const h = (history[lid] || []).concat({ t: now, ab: st.ab_bps, ba: st.ba_bps })
    history[lid] = h.length > 60 ? h.slice(-60) : h
  }
  set({ stats, history })
})
on('ev:link', (ev: { link: string; up: boolean; params?: LinkParams }) => {
  const l = S().project.links[ev.link]
  if (!l) return
  const nl: Link = { ...l, up: ev.up, params: ev.params || l.params }
  set({ project: { ...S().project, links: { ...S().project.links, [ev.link]: nl } } })
})

export function toggleTheme() {
  const dark = S().theme !== 'dark'
  document.documentElement.classList.toggle('dark', dark)
  storage.set('netlab.theme', dark ? 'dark' : 'light')
  set({ theme: dark ? 'dark' : 'light' })      // grafiklar CSS ranglarini qayta o'qiydi
}

export const devLabel = (t: string) => S().info?.device_types[t]?.label || t
export type { Device }
