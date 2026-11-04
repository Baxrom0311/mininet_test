import { useEffect, useState } from 'react'
import { LineChart as LineIcon, MapPin, Play, Plus, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Field, Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { Section } from '@/components/ui/table'
import { toast } from '@/components/ui/toast'
import { api } from '@/lib/api'
import { on } from '@/lib/bus'
import { storage } from '@/lib/format'
import { mutate, openWin, pickPoint, useStore, type Win } from '@/lib/store'
import type { Scenario, ScenarioApp, ScenarioEvent } from '@/lib/types'
import { LogBox } from './DeviceWindow'
import { WindowFrame } from './WindowFrame'

const PARAM_DEFAULTS: Record<string, string> = { bw: '1,2,5,10,20,50,100', delay: '1,5,10,20,50,100', loss: '0,0.5,1,2,5,10', queue: '10,50,100,500,1000',
  rate: '1Mbps,2Mbps,5Mbps,10Mbps,20Mbps', size: '200,500,1000,1400', tx_power: '5,10,15,20,25', channel: '36,40' }
const FIELD_LABEL: Record<string, string> = { bw: 'tezlik (Mbit/s)', delay: 'kechikish (ms)', loss: "yo'qotish (%)", queue: 'navbat (paket)', rate: 'tezlik', size: 'paket (B)', tx_power: 'quvvat (dBm)', channel: 'kanal' }
const cell = 'px-1.5 py-1 align-middle'

export function ScenarioWindow({ win }: { win: Win }) {
  const name = win.ref
  const project = useStore((s) => s.project)
  const sc = project.scenarios?.[name]
  useEffect(() => { useStore.setState({ overlay: (sc?.events || []).filter((e) => e.action === 'move') }) }, [sc?.events])
  useEffect(() => () => useStore.setState({ overlay: [] }), [])
  if (!sc) return null
  const upd = (fn: (s: Scenario) => void) => mutate((p) => fn(p.scenarios[name]))
  const l3 = Object.entries(project.devices).filter(([, d]) => ['host', 'server', 'router'].includes(d.type)).map(([n]) => n).sort()
  const devNames = Object.keys(project.devices).sort()
  const links = Object.entries(project.links).map(([lid, l]) => ({ value: lid, label: `${lid}: ${l.a.dev}↔${l.b.dev}` }))

  const updApp = (i: number, patch: Partial<ScenarioApp>) => upd((s) => { s.apps[i] = { ...s.apps[i], ...patch } })
  const updEv = (i: number, patch: Partial<ScenarioEvent>) => upd((s) => { s.events[i] = { ...s.events[i], ...patch } })

  return (
    <WindowFrame win={win} icon={<LineIcon />} title={'Ssenariy: ' + name} sub="trafik ilovalari + vaqtli hodisalar → ishga tushirish">
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-auto p-3">
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted-foreground">Davomiylik (s):</span>
          <Input type="number" className="h-8 w-24" value={sc.duration} onChange={(e) => upd((s) => { s.duration = parseFloat(e.target.value) || 30 })} />
          <Input className="h-8 flex-1" placeholder="izoh (nima tekshirilyapti?)" value={sc.notes || ''} onChange={(e) => upd((s) => { s.notes = e.target.value })} />
        </div>

        <Section title="Trafik ilovalari" right={<Button size="xs" variant="outline" onClick={() => {
          if (l3.length < 2) return toast('Kamida 2 ta host/server/router kerak', 'warn')
          upd((s) => { let k = s.apps.length + 1; while (s.apps.some((x) => x.id === 'a' + k)) k++; s.apps.push({ id: 'a' + k, type: 'udp', src: l3[0], dst: l3[l3.length - 1], rate: '1Mbps', size: 1000, start: 1, stop: s.duration || 30 }) })
        }}><Plus /> ilova</Button>}>
          {!sc.apps.length ? <div className="px-3 py-2 text-xs text-muted-foreground">Ilova yo'q. UDP — video/VoIP (qat'iy tezlik), TCP — fayl uzatish (bor sig'imni oladi), Ping — RTT o'lchovi.</div> : (
            <div className="overflow-x-auto"><table className="w-full min-w-[820px] text-xs">
              <thead><tr className="border-b text-left text-muted-foreground">{['id', 'Tur', 'Manba', 'Qabul', 'Tezlik', 'Paket (B)', 'Start', 'Stop', 'Nom', ''].map((h) => <th key={h} className="px-1.5 py-1.5 font-medium">{h}</th>)}</tr></thead>
              <tbody>{sc.apps.map((a, i) => (
                <tr key={i} className="border-b border-border/50">
                  <td className={cell + ' font-mono'}>{a.id}</td>
                  <td className={cell}><Select className="h-7 w-28" value={a.type} onChange={(v) => updApp(i, { type: v as ScenarioApp['type'] })} options={[{ value: 'udp', label: 'UDP (CBR)' }, { value: 'tcp', label: 'TCP (bulk)' }, { value: 'ping', label: 'Ping' }]} /></td>
                  <td className={cell}><Select className="h-7 w-24" value={a.src} onChange={(v) => updApp(i, { src: v })} options={l3.map((n) => ({ value: n, label: n }))} /></td>
                  <td className={cell}><Select className="h-7 w-24" value={a.dst} onChange={(v) => updApp(i, { dst: v })} options={l3.map((n) => ({ value: n, label: n }))} /></td>
                  <td className={cell}><Input className="h-7 w-24" value={a.rate || ''} placeholder={a.type === 'tcp' ? 'cheksiz' : '5Mbps'} disabled={a.type === 'ping'} onChange={(e) => updApp(i, { rate: e.target.value.trim() || undefined })} /></td>
                  <td className={cell}><Input type="number" className="h-7 w-20" value={a.size ?? ''} placeholder={a.type === 'ping' ? '64' : '1000'} onChange={(e) => updApp(i, { size: e.target.value ? parseInt(e.target.value, 10) : undefined })} /></td>
                  <td className={cell}><Input type="number" step="any" className="h-7 w-16" value={a.start ?? 1} onChange={(e) => updApp(i, { start: parseFloat(e.target.value) || 0 })} /></td>
                  <td className={cell}><Input type="number" step="any" className="h-7 w-16" value={a.stop ?? sc.duration} onChange={(e) => updApp(i, { stop: parseFloat(e.target.value) || sc.duration })} /></td>
                  <td className={cell}><Input className="h-7 w-40" value={a.label || ''} placeholder={`${a.type} ${a.src}→${a.dst}`} onChange={(e) => updApp(i, { label: e.target.value || undefined })} /></td>
                  <td className={cell}><Button size="icon" variant="ghost" className="h-7 w-7" onClick={() => upd((s) => { s.apps.splice(i, 1) })}><X /></Button></td>
                </tr>))}</tbody>
            </table></div>
          )}
        </Section>

        <Section title="Vaqtli hodisalar" right={<Button size="xs" variant="outline" onClick={() => upd((s) => {
          const first = Object.keys(project.links)[0]
          if (first) s.events.push({ t: Math.round((s.duration || 30) / 3), action: 'link_down', link: first })
          else { const d0 = devNames[0]; if (d0) s.events.push({ t: 5, action: 'move', device: d0, x: project.devices[d0].x + 100, y: project.devices[d0].y, duration: 10 }) }
        })}><Plus /> hodisa</Button>}>
          {!sc.events.length ? <div className="px-3 py-2 text-xs text-muted-foreground">Hodisa yo'q — masalan t=10s da kabel uzilishi, t=16s da tiklanishi (failover tahlili), yoki Wi-Fi stansiyaning harakati.</div> : (
            <div className="overflow-x-auto"><table className="w-full min-w-[760px] text-xs">
              <thead><tr className="border-b text-left text-muted-foreground">{['Vaqt (s)', 'Amal', 'Kabel / qurilma', 'Parametrlar', ''].map((h) => <th key={h} className="px-1.5 py-1.5 font-medium">{h}</th>)}</tr></thead>
              <tbody>{sc.events.map((e, i) => (
                <tr key={i} className="border-b border-border/50">
                  <td className={cell}><Input type="number" step="any" className="h-7 w-20" value={e.t} onChange={(ev) => updEv(i, { t: parseFloat(ev.target.value) || 0 })} /></td>
                  <td className={cell}><Select className="h-7 w-44" value={e.action} options={[{ value: 'link_down', label: 'Kabel uzilishi' }, { value: 'link_up', label: 'Kabel tiklanishi' }, { value: 'link_params', label: 'Kabel parametrlari' }, { value: 'move', label: 'Harakat (qurilma yuradi)' }]}
                    onChange={(v) => upd((s) => {
                      const ev = s.events[i]
                      if (v === 'move') { const dn = ev.device || devNames[0]; const d = project.devices[dn]; s.events[i] = { t: ev.t, action: 'move', device: dn, x: d.x + 100, y: d.y, duration: 10 } }
                      else s.events[i] = { t: ev.t, action: v as ScenarioEvent['action'], link: ev.link || links[0]?.value, ...(v === 'link_params' ? { params: ev.params || {} } : {}) }
                    })} /></td>
                  <td className={cell}>{e.action === 'move'
                    ? <Select className="h-7 w-28" value={e.device} onChange={(v) => updEv(i, { device: v })} options={devNames.map((n) => ({ value: n, label: n }))} />
                    : <Select className="h-7 w-44" value={e.link} onChange={(v) => updEv(i, { link: v })} options={links} />}</td>
                  <td className={cell}>{e.action === 'move' ? (
                    <div className="flex items-center gap-1">
                      <Input type="number" className="h-7 w-20" value={Math.round(e.x ?? 0)} onChange={(ev) => updEv(i, { x: parseFloat(ev.target.value) })} title="x (px)" />
                      <Input type="number" className="h-7 w-20" value={Math.round(e.y ?? 0)} onChange={(ev) => updEv(i, { y: parseFloat(ev.target.value) })} title="y (px)" />
                      <Button size="icon" variant="outline" className="h-7 w-7" title="Manzilni kanvasdan tanlash" onClick={() => pickPoint((x, y) => updEv(i, { x: Math.round(x), y: Math.round(y) }))}><MapPin /></Button>
                      <span className="px-1 text-muted-foreground">davom.</span>
                      <Input type="number" className="h-7 w-16" value={e.duration ?? 0} onChange={(ev) => updEv(i, { duration: Math.max(parseFloat(ev.target.value) || 0, 0) })} />
                    </div>
                  ) : e.action === 'link_params' ? (
                    <div className="flex gap-1">
                      {(['bw', 'delay', 'loss'] as const).map((k) => <Input key={k} className="h-7 w-20" placeholder={k === 'bw' ? 'Mbit/s' : k === 'delay' ? 'ms' : 'loss %'} value={e.params?.[k] ?? ''}
                        onChange={(ev) => updEv(i, { params: { ...(e.params || {}), [k]: ev.target.value === '' ? undefined : k === 'delay' ? ev.target.value : parseFloat(ev.target.value) } })} />)}
                    </div>
                  ) : null}</td>
                  <td className={cell}><Button size="icon" variant="ghost" className="h-7 w-7" onClick={() => upd((s) => { s.events.splice(i, 1) })}><X /></Button></td>
                </tr>))}</tbody>
            </table></div>
          )}
        </Section>

        <RunSection name={name} sc={sc} />
      </div>
    </WindowFrame>
  )
}

type Engine = { id: string; label: string; available: boolean; hint: string }

function RunSection({ name, sc }: { name: string; sc: Scenario }) {
  const project = useStore((s) => s.project)
  const [engines, setEngines] = useState<Engine[]>([])
  const [engine, setEngine] = useState(storage.get('netlab.engine', 'analytic'))
  const [prog, setProg] = useState(0)
  const [log, setLog] = useState<{ text: string; err: boolean } | null>(null)
  const [runId, setRunId] = useState<string | null>(null)
  useEffect(() => { api.get<{ engines: Engine[] }>('/api/engines').then((r) => { setEngines(r.engines); if (!r.engines.find((e) => e.id === engine && e.available)) setEngine(r.engines.find((e) => e.available)?.id || 'analytic') }).catch(() => {}) }, [])
  useEffect(() => on('ev:run', (ev: any) => {
    if (ev.run_id !== runId) return
    if (ev.progress != null) setProg(ev.progress)
    if (ev.log) setLog((l) => ({ text: (l?.text || '') + ev.log + '\n', err: false }))
    if (ev.done) {
      setRunId(null)
      if (ev.status === 'done') { toast('Natija tayyor', 'ok'); openWin('results', 'main', { w: 1060, h: 700, arg: { run: ev.run_id } }) }
      else setLog((l) => ({ text: (l?.text || '') + (ev.error || 'xato') + '\n', err: true }))
    }
  }), [runId])
  const run = async () => {
    try {
      const v = await api.post<{ errors: string[] }>('/api/scenario/validate', { project, scenario: sc })
      if (v.errors.length) return toast('Ssenariy xatolari:\n• ' + v.errors.slice(0, 6).join('\n• '), 'error')
      const r = await api.post('/api/runs', { project, scenario: name, engine })
      setRunId(r.run_id); setProg(0); setLog({ text: r.run_id + ' boshlandi\n', err: false })
    } catch (e: any) { toast(e.message, 'error') }
  }
  const eng = engines.find((e) => e.id === engine)
  return (
    <>
      <Section title="Ishga tushirish">
        <div className="flex flex-col gap-2 p-3">
          <div className="flex flex-wrap items-center gap-2">
            <Select className="w-80" value={engine} onChange={(v) => { setEngine(v); storage.set('netlab.engine', v) }}
              options={engines.map((e) => ({ value: e.id, label: e.label + (e.available ? '' : ' — mavjud emas'), disabled: !e.available }))} />
            <Button size="sm" onClick={run} disabled={!!runId}><Play /> Ishga tushirish</Button>
            <div className="h-1.5 min-w-[120px] flex-1 overflow-hidden rounded bg-muted"><div className="h-full bg-foreground transition-all" style={{ width: prog * 100 + '%' }} /></div>
            <Button size="sm" variant="outline" onClick={() => openWin('results', 'main', { w: 1060, h: 700 })}>Natijalar</Button>
          </div>
          {eng && <div className="text-[11px] text-muted-foreground">{eng.hint}</div>}
          {log && <LogBox text={log.text} err={log.err} />}
        </div>
      </Section>
      <StudySection name={name} sc={sc} engine={engine} />
    </>
  )
}

function paramOptions(project: ReturnType<typeof useStore.getState>['project'], sc: Scenario) {
  const opts: { value: string; label: string }[] = []
  Object.entries(project.links).forEach(([lid, l]) => (['bw', 'delay', 'loss', 'queue'] as const).forEach((f) => opts.push({ value: `link|${lid}|${f}`, label: `kabel ${lid} (${l.a.dev}↔${l.b.dev}) · ${FIELD_LABEL[f]}` })))
  Object.entries(project.devices).filter(([, d]) => d.type === 'wifiap').forEach(([n]) => { opts.push({ value: `ap|${n}|tx_power`, label: `Wi-Fi ${n} · uzatish quvvati (dBm)` }); opts.push({ value: `ap|${n}|channel`, label: `Wi-Fi ${n} · kanal` }) })
  sc.apps.forEach((a) => (a.type === 'tcp' ? ['rate'] : a.type === 'udp' ? ['rate', 'size'] : []).forEach((f) => opts.push({ value: `app|${a.id}|${f}`, label: `ilova ${a.id} · ${FIELD_LABEL[f]}` })))
  return opts
}
const toParam = (v: string) => { const [kind, target, field] = v.split('|'); return { kind, [kind]: target, field } }
const parseVals = (s: string) => s.split(',').map((x) => x.trim()).filter(Boolean).map((x) => (/^[\d.]+$/.test(x) ? parseFloat(x) : x))

function StudySection({ name, sc, engine }: { name: string; sc: Scenario; engine: string }) {
  const project = useStore((s) => s.project)
  const opts = paramOptions(project, sc)
  const [p1, setP1] = useState(storage.get('netlab.studyParam', opts[0]?.value || ''))
  const [p2, setP2] = useState('')
  const [v1, setV1] = useState(PARAM_DEFAULTS[p1.split('|')[2]] || '')
  const [v2, setV2] = useState('')
  const [st, setSt] = useState<{ id: string; n: number; i: number } | null>(null)
  const [info, setInfo] = useState('')
  useEffect(() => on('ev:study', (ev: any) => {
    if (!st || ev.study_id !== st.id) return
    if (ev.done) { setInfo(`tayyor: ${ev.total} ta ishga tushirish`); toast('Parametrik tadqiqot tayyor', 'ok'); openWin('results', 'main', { w: 1060, h: 700, arg: { study: st.id } }); setSt(null); return }
    setSt((s) => s && { ...s, i: ev.index }); setInfo(`${ev.index + 1}/${ev.total}: ${ev.value}`)
  }), [st])
  const start = async () => {
    if (!p1) return toast('Parametr tanlang', 'warn')
    try {
      const body: any = { project, scenario: name, engine, param: toParam(p1), values: parseVals(v1) }
      if (p2) { body.param2 = toParam(p2); body.values2 = parseVals(v2) }
      const r = await api.post('/api/studies', body)
      setSt({ id: r.study_id, n: r.runs, i: 0 }); setInfo(`0/${r.runs}`)
    } catch (e: any) { toast(e.message, 'error') }
  }
  const n = parseVals(v1).length * (p2 ? parseVals(v2).length : 1)
  return (
    <Section title="Parametrik tadqiqot (1 yoki 2 parametr)">
      <div className="flex flex-col gap-2 p-3">
        <div className="grid grid-cols-[1fr_1fr] gap-2">
          <Field label="1-parametr (x o'qi)"><Select value={p1} options={opts} onChange={(v) => { setP1(v); storage.set('netlab.studyParam', v); setV1(PARAM_DEFAULTS[v.split('|')[2]] || '') }} /></Field>
          <Field label="Qiymatlar (vergul bilan)"><Input value={v1} onChange={(e) => setV1(e.target.value)} /></Field>
          <Field label="2-parametr (y o'qi, ixtiyoriy → issiqlik xaritasi)"><Select value={p2} options={[{ value: '', label: "— yo'q —" }, ...opts.filter((o) => o.value !== p1)]}
            onChange={(v) => { setP2(v); setV2(v ? PARAM_DEFAULTS[v.split('|')[2]] || '' : '') }} /></Field>
          <Field label="Qiymatlar (vergul bilan)"><Input value={v2} disabled={!p2} onChange={(e) => setV2(e.target.value)} /></Field>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="secondary" onClick={start} disabled={!!st}><Play /> Tadqiqotni boshlash</Button>
          <span className="text-[11px] text-muted-foreground">{n} ta ishga tushirish{n > 49 ? ' (ko\'pi bilan 49)' : ''}</span>
          <div className="h-1.5 min-w-[120px] flex-1 overflow-hidden rounded bg-muted"><div className="h-full bg-foreground transition-all" style={{ width: st ? (st.i / st.n) * 100 + '%' : info.startsWith('tayyor') ? '100%' : '0' }} /></div>
          <span className="text-xs text-muted-foreground">{info}</span>
        </div>
        <p className="text-[11px] text-muted-foreground">Har bir qiymat (yoki kombinatsiya) yuqoridagi dvigatelda alohida ishga tushiriladi. Natijalar oynasida 1 parametr — "parametr ↔ o'lchov" grafigi, 2 parametr — issiqlik xaritasi (OPNET parametric study).</p>
      </div>
    </Section>
  )
}
