import { useEffect, useRef, useState } from 'react'
import { Terminal as XTerm } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { DeviceIcon } from '@/components/DeviceIcon'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Field, Input, Textarea } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { DataTable, Section } from '@/components/ui/table'
import { toast } from '@/components/ui/toast'
import { api, wsUrl } from '@/lib/api'
import { on } from '@/lib/bus'
import { clock, fmtBps } from '@/lib/format'
import { devLabel, devMac, ifname, openWin, portsOf, renameDevice, running, saveDeviceConfig, updateWin, useStore, type Win } from '@/lib/store'
import type { InspectSection, Pipeline } from '@/lib/types'
import { apConfig, essKey, linkEstimate } from '@/lib/wifi'
import { KeepAliveTabs, WindowFrame } from './WindowFrame'
import { cn } from '@/lib/utils'

export function DeviceWindow({ win }: { win: Win }) {
  const name = win.ref
  const d = useStore((s) => s.project.devices[name])
  const isRunning = useStore((s) => s.status.running)
  if (!d) return null
  const tab = win.tab || (isRunning ? 'cli' : 'cfg')
  return (
    <WindowFrame win={win} icon={<DeviceIcon type={d.type} size={20} />} title={name} sub={`${devLabel(d.type)} · id ${d.id}`}>
      <KeepAliveTabs active={tab} onChange={(t) => updateWin(win.id, { tab: t })} tabs={[
        { id: 'cfg', label: 'Sozlama', render: () => <ConfigPane key={name + JSON.stringify(d.config)} name={name} /> },
        { id: 'cli', label: 'CLI', flush: true, render: () => <TerminalPane name={name} active={tab === 'cli'} /> },
        { id: 'tbl', label: 'Jadvallar', render: () => <InspectPane name={name} which="tables" active={tab === 'tbl'} /> },
        { id: 'hw', label: 'Hardware', render: () => <InspectPane name={name} which="hw" active={tab === 'hw'} /> },
      ]} />
    </WindowFrame>
  )
}

// ======================= Sozlama =======================
function ConfigPane({ name }: { name: string }) {
  const p = useStore((s) => s.project)
  const info = useStore((s) => s.info)
  const d = p.devices[name]
  const t = d.type
  const [cfg, setCfg] = useState<Record<string, any>>(() => JSON.parse(JSON.stringify(d.config || {})))
  const [newName, setNewName] = useState(name)
  const [p4log, setP4log] = useState<{ text: string; err: boolean } | null>(null)
  const ports = portsOf(p, name)
  const portNums = Object.keys(ports).map(Number).sort((a, b) => a - b)
  const upd = (patch: Record<string, any>) => setCfg((c) => ({ ...c, ...patch }))
  const save = async () => { await saveDeviceConfig(name, cfg); if (!running()) toast(`${name}: saqlandi`, 'ok') }
  const [routesText, setRoutesText] = useState(((d.config?.routes || []) as { prefix: string; via: string }[]).map((r) => `${r.prefix} via ${r.via}`).join('\n'))
  useEffect(() => {
    const routes = routesText.split('\n').map((x) => x.trim()).filter(Boolean).map((x) => { const m = x.match(/^(\S+)\s+via\s+(\S+)$/); return m ? { prefix: m[1], via: m[2] } : null }).filter(Boolean)
    setCfg((c) => ({ ...c, routes }))
  }, [routesText])
  const ctrlOpts = [{ value: '', label: '— tanlang —' }, ...Object.keys(p.controllers).map((c) => ({ value: c, label: c }))]

  return (
    <>
      {['host', 'server', 'router', 'sdnrouter'].includes(t) && (
        <Section title="Interfeyslar (IPv4)">
          <DataTable empty="Kabel ulanmagan — avval kanvasda kabel torting (C)." columns={['Interfeys', 'Ulangan', 'MAC', 'IP/prefiks']} rows={portNums.map((pt) => {
            const ifn = ifname(name, pt)
            return [ifn, `${ports[pt].peer}:eth${ports[pt].peerPort}`, ['host', 'server', 'router'].includes(t) ? devMac(d.id, pt) : 'OVS port MAC',
              <Input key={ifn} className="h-7 w-44 font-mono text-xs" placeholder="10.0.1.1/24" value={cfg.interfaces?.[ifn]?.ip || ''}
                onChange={(e) => { const ifs = { ...(cfg.interfaces || {}) }; if (e.target.value.trim()) ifs[ifn] = { ip: e.target.value.trim() }; else delete ifs[ifn]; upd({ interfaces: ifs }) }} />]
          })} />
        </Section>
      )}
      {['host', 'server'].includes(t) && <Field label="Default gateway"><Input value={cfg.gateway || ''} placeholder="10.0.1.1" onChange={(e) => upd({ gateway: e.target.value.trim() })} /></Field>}
      {t === 'router' && (
        <>
          <Section title="FRR daemonlari (zebra va staticd har doim ishlaydi)">
            <div className="flex flex-wrap gap-3 p-3">
              {(info?.frr_daemons || []).map((dm) => <Checkbox key={dm} label={dm} checked={(cfg.daemons || []).includes(dm)}
                onChange={(v) => upd({ daemons: v ? [...(cfg.daemons || []), dm] : (cfg.daemons || []).filter((x: string) => x !== dm) })} />)}
            </div>
          </Section>
          <Field label="Startup-config (tarmoq ishga tushganda qo'llanadi)">
            <Textarea rows={10} className="font-mono text-xs" value={cfg.startup || ''} onChange={(e) => upd({ startup: e.target.value })}
              placeholder={'FRR konfiguratsiyasi (vtysh sintaksisi), masalan:\nrouter ospf\n network 10.0.1.0/24 area 0'} />
          </Field>
          <div className="flex items-center gap-2">
            <Button size="sm" variant="outline" onClick={async () => {
              try { const r = await api.get(`/api/net/frr/${name}/running-config`); upd({ startup: r.config }); toast("running-config olindi — saqlashni unutmang", 'ok') } catch (e: any) { toast(e.message, 'error') }
            }}>Joriy running-config'ni olish</Button>
            <span className="text-[11px] text-muted-foreground">Jonli o'zgartirish: CLI bo'limida vtysh (configure terminal ...), keyin "olish" → "Saqlash".</span>
          </div>
        </>
      )}
      {t === 'switch' && (
        <>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Rejim"><Select value={cfg.mode || 'standalone'} onChange={(v) => upd({ mode: v })} options={[{ value: 'standalone', label: 'Standalone (OVS L2 learning)' }, { value: 'sdn', label: 'SDN (kontroller boshqaradi)' }]} /></Field>
            <Field label="SDN kontroller"><Select value={cfg.controller || ''} onChange={(v) => upd({ controller: v })} options={ctrlOpts} /></Field>
          </div>
          <Checkbox checked={!!cfg.stp} onChange={(v) => upd({ stp: v })} label="STP (802.1D) — halqali topologiya uchun (faqat standalone)" />
        </>
      )}
      {t === 'sdnrouter' && (
        <>
          <Field label="SDN kontroller (routing mantiqi shu ilovada)"><Select value={cfg.controller || ''} onChange={(v) => upd({ controller: v })} options={ctrlOpts} /></Field>
          <p className="text-[11px] text-muted-foreground">SDN router — OVS bridge; ARP javobi, LPM, TTL va flow o'rnatish kontroller ilovasida (l3_router shabloni). IP va marshrutlar kontrollerga NETLAB_CONFIG orqali beriladi.</p>
        </>
      )}
      {t === 'wifiap' && <WifiApConfig name={name} cfg={cfg} upd={upd} />}
      {['host', 'server', 'router', 'sdnrouter'].includes(t) && (
        <Field label={t === 'router' ? "Statik marshrutlar (kernel; FRR uchun startup-config'da \"ip route\")" : 'Statik marshrutlar'}>
          <Textarea rows={3} className="min-h-[60px] font-mono text-xs" value={routesText} onChange={(e) => setRoutesText(e.target.value)} placeholder="10.9.0.0/16 via 10.0.12.2" />
        </Field>
      )}
      {t === 'p4switch' && (
        <>
          <div className="flex items-end gap-2">
            <Field label="P4 dastur" className="flex-1"><Select value={cfg.program || ''} onChange={(v) => upd({ program: v })}
              options={[{ value: '', label: '— tanlang —' }, ...Object.keys(p.p4_programs).map((x) => ({ value: x, label: x }))]} /></Field>
            <Button size="sm" variant="outline" onClick={() => cfg.program && openWin('p4', cfg.program, { w: 880, h: 640 })}>Dasturni ochish</Button>
          </div>
          <Section title="Portlar → qo'shni MAC (jadval yozish uchun)">
            <pre className="p-3 font-mono text-[11.5px] text-muted-foreground">{portNums.map((pt) => {
              const peer = p.devices[ports[pt].peer]
              const mac = peer && ['host', 'server', 'router'].includes(peer.type) ? devMac(peer.id, ports[pt].peerPort) : '(switch)'
              return `port ${pt} → ${ports[pt].peer}:eth${ports[pt].peerPort}  ${mac}`
            }).join('\n') || "(kabel yo'q)"}</pre>
          </Section>
          <Field label="Runtime buyruqlari (dastur yuklangandan keyin qo'llanadi)">
            <Textarea rows={7} className="font-mono text-xs" value={cfg.runtime || ''} onChange={(e) => upd({ runtime: e.target.value })}
              placeholder={'simple_switch_CLI buyruqlari, masalan:\ntable_add dmac forward 00:00:00:00:01:01 => 1'} />
          </Field>
          <div className="flex gap-2">
            <Button size="sm" variant="secondary" onClick={async () => {
              await saveDeviceConfig(name, cfg)
              if (!running()) return toast('Avval tarmoqni ishga tushiring (yoki P4 muharririda "Tekshirish")', 'warn')
              setP4log({ text: 'kompilyatsiya...', err: false })
              try { const r = await api.post(`/api/net/p4/${name}/load`, { project: useStore.getState().project }); setP4log({ text: r.log, err: !r.ok }) } catch (e: any) { setP4log({ text: e.message, err: true }) }
            }}>Kompilyatsiya + yuklash</Button>
            <Button size="sm" variant="outline" onClick={async () => {
              try { const r = await api.post(`/api/net/p4/${name}/runtime`, { commands: cfg.runtime || '' }); setP4log({ text: r.output, err: false }) } catch (e: any) { setP4log({ text: e.message, err: true }) }
            }}>Faqat runtime yuborish</Button>
          </div>
          {p4log && <LogBox text={p4log.text} err={p4log.err} />}
        </>
      )}
      <div className="mt-auto flex items-center gap-2 pt-2">
        <Button size="sm" onClick={save}>{running() ? "Saqlash va qo'llash" : 'Saqlash'}</Button>
        <span className="text-xs text-muted-foreground">Nom:</span>
        <Input className="h-8 w-36" value={newName} onChange={(e) => setNewName(e.target.value)} />
        <Button size="sm" variant="outline" onClick={() => { const nn = newName.trim(); if (nn && nn !== name && renameDevice(name, nn)) openWin('device', nn, { tab: 'cfg' }) }}>Nomni o'zgartirish</Button>
      </div>
    </>
  )
}

function WifiApConfig({ name, cfg, upd }: { name: string; cfg: Record<string, any>; upd: (p: Record<string, any>) => void }) {
  const p = useStore((s) => s.project)
  const wifi = useStore((s) => s.info?.wifi)
  const c = apConfig(wifi, { ...p.devices[name], config: cfg })
  const band = cfg.standard === '80211g' ? '2.4' : cfg.standard === '80211ac' ? '5' : String(c.band)
  const chans = wifi?.channels?.[band]?.[String(c.width)] || []
  const ports = portsOf(p, name)
  const mine = essKey(wifi, name, { ...p.devices[name], config: cfg })
  const ess = Object.entries(p.devices).filter(([n, dv]) => dv.type === 'wifiap' && n !== name && essKey(wifi, n, dv) === mine).map(([n]) => n)
  return (
    <>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Standart"><Select value={c.standard} onChange={(v) => upd({ standard: v })} options={[
          { value: '80211ax', label: '802.11ax (Wi-Fi 6)' }, { value: '80211ac', label: '802.11ac (Wi-Fi 5)' }, { value: '80211n', label: '802.11n (Wi-Fi 4)' }, { value: '80211g', label: '802.11g' }]} /></Field>
        <Field label="Diapazon"><Select value={band} onChange={(v) => upd({ band: parseFloat(v) })} options={[{ value: '5', label: '5 GHz' }, { value: '2.4', label: '2.4 GHz' }]} /></Field>
        <Field label="Kanal kengligi"><Select value={String(c.width)} onChange={(v) => upd({ width: parseInt(v, 10) })} options={['20', '40', '80', '160'].map((x) => ({ value: x, label: x + ' MHz' }))} /></Field>
        <Field label="Uzatish quvvati (dBm)"><Input type="number" step="any" value={cfg.tx_power ?? 20} onChange={(e) => upd({ tx_power: parseFloat(e.target.value) || 20 })} /></Field>
        <Field label="SSID"><Input value={cfg.ssid || ''} placeholder={'netlab-' + name} onChange={(e) => upd({ ssid: e.target.value.trim() })} /></Field>
        <Field label="Kanal (bir kanaldagi yaqin AP'lar efirni bo'lishadi)"><Select value={String(c.channel)} onChange={(v) => upd({ channel: parseInt(v, 10) })} options={chans.map((x) => ({ value: String(x), label: String(x) }))} /></Field>
      </div>
      <p className="text-[11px] text-muted-foreground">{ess.length ? `ESS: ${[name, ...ess].join(', ')} — bir xil SSID va kanal. Harakatlanuvchi stansiyalar joriy AP qamrovidan chiqqach eng kuchli AP'ga o'tadi (roaming; ns-3 va analitik).`
        : "Roaming uchun boshqa AP'ga ham xuddi shu SSID va kanalni bering (ESS)."}</p>
      <Section title="Stansiyalar (radio modeli, joriy joylashuv)">
        <DataTable columns={['Qurilma', 'Masofa', 'RSSI dBm', 'SNR dB', 'MCS', 'Mbit/s']} rows={Object.entries(ports).map(([pt, info]) => {
          const e = linkEstimate(wifi, p, info.lid)
          return e ? [info.peer, e.distance_m.toFixed(1) + ' m', e.rssi_dbm.toFixed(1), e.snr_db.toFixed(1), e.ok ? 'MCS' + e.mcs : '—', e.ok ? '~' + e.thr_mbps.toFixed(1) : "aloqa yo'q"]
            : [`${info.peer} (simli, eth${pt})`, '', '', '', '', '']
        })} />
      </Section>
    </>
  )
}

export function LogBox({ text, err }: { text: string; err?: boolean }) {
  return <pre className={cn('max-h-56 shrink-0 overflow-auto whitespace-pre-wrap rounded-lg border bg-background/60 p-2.5 font-mono text-[11.5px]', err ? 'text-destructive' : 'text-muted-foreground')}>{text}</pre>
}

// ======================= Terminal =======================
function TerminalPane({ name, active }: { name: string; active: boolean }) {
  const d = useStore((s) => s.project.devices[name])
  const shells = d.type === 'router' ? [['vtysh', 'vtysh (FRR CLI)'], ['bash', 'bash']] : d.type === 'p4switch' ? [['cli', 'simple_switch_CLI'], ['bash', 'bash']] : [['default', 'bash']]
  const [shell, setShell] = useState(shells[0][0])
  const [state, setState] = useState('')
  const host = useRef<HTMLDivElement>(null)
  const term = useRef<XTerm>(), fit = useRef<FitAddon>(), ws = useRef<WebSocket>()
  const [gen, setGen] = useState(0)
  useEffect(() => {
    const dark = document.documentElement.classList.contains('dark')
    const t = new XTerm({ fontFamily: 'JetBrains Mono, SFMono-Regular, Menlo, Consolas, monospace', fontSize: 13, cursorBlink: true, scrollback: 5000,
      theme: dark ? { background: '#09090b', foreground: '#e4e4e7', cursor: '#fafafa', selectionBackground: '#fafafa33' }
        : { background: '#fafafa', foreground: '#18181b', cursor: '#18181b', selectionBackground: '#18181b22' } })
    const f = new FitAddon(); t.loadAddon(f); t.open(host.current!)
    term.current = t; fit.current = f
    t.onData((data) => ws.current?.readyState === 1 && ws.current.send(JSON.stringify({ t: 'in', d: data })))
    const ro = new ResizeObserver(() => { try { f.fit(); sendSize() } catch { /* yashirin */ } })
    ro.observe(host.current!)
    return () => { ro.disconnect(); t.dispose() }
  }, [])
  const sendSize = () => { const t = term.current; if (ws.current?.readyState === 1 && t) ws.current.send(JSON.stringify({ t: 'resize', cols: t.cols, rows: t.rows })) }
  useEffect(() => {
    if (!useStore.getState().status.running) setState("Tarmoq ishlamayapti — yuqoridagi \"Ishga tushirish\"")
    ws.current?.close()
    term.current?.clear()
    const s = new WebSocket(wsUrl(`/ws/term/${name}?shell=${shell}`))
    s.binaryType = 'arraybuffer'
    s.onopen = () => { setState('ulangan'); sendSize(); term.current?.focus() }
    s.onmessage = (m) => term.current?.write(typeof m.data === 'string' ? m.data : new Uint8Array(m.data))
    s.onclose = () => setState('uzildi')
    ws.current = s
    return () => s.close()
  }, [name, shell, gen])
  useEffect(() => { if (active) setTimeout(() => { try { fit.current?.fit() } catch { /* */ } term.current?.focus() }, 30) }, [active])
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b px-3 py-1.5 text-xs text-muted-foreground">
        Qobiq: <Select className="h-7 w-48" value={shell} onChange={setShell} options={shells.map(([v, l]) => ({ value: v, label: l }))} />
        <Button size="xs" variant="outline" onClick={() => setGen((g) => g + 1)}>Qayta ulash</Button>
        <span>{state}</span>
      </div>
      <div className="min-h-0 flex-1 bg-[#fafafa] p-2 dark:bg-[#09090b]" onClick={() => term.current?.focus()}><div ref={host} className="h-full w-full" /></div>
    </div>
  )
}

// ======================= Jadvallar / Hardware =======================
export function PipelineView({ data }: { data: Pipeline }) {
  const Box = ({ title, children }: { title: string; children: React.ReactNode }) => (
    <div className="flex min-w-[170px] flex-col gap-1.5 rounded-lg border bg-background/40 p-2.5">
      <div className="text-[10.5px] font-semibold uppercase tracking-wider text-muted-foreground">{title}</div>{children}
    </div>
  )
  const Node = ({ n, d, chips }: { n: string; d?: string; chips?: string[] }) => (
    <div className="rounded-md border bg-card/60 px-2 py-1.5 text-[11px]">
      <div className="font-mono font-semibold">{n}</div>
      {d && <div className="whitespace-pre-wrap font-mono text-muted-foreground">{d}</div>}
      {chips && <div className="mt-1 flex flex-wrap gap-1">{chips.map((c) => <span key={c} className="rounded border bg-muted px-1.5 font-mono text-[10px]">{c}</span>)}</div>}
    </div>
  )
  const Arrow = () => <div className="self-center text-lg text-muted-foreground">→</div>
  const extra = [data.headers?.length && 'Headerlar: ' + data.headers.join(', '), data.counters?.length && 'Counterlar: ' + data.counters.join(', '),
    data.registers?.length && 'Registerlar: ' + data.registers.map((r) => r.name + (r.size ? `[${r.size}]` : '')).join(', ')].filter(Boolean)
  return (
    <div className="flex flex-col gap-2 p-3">
      <div className="flex items-stretch gap-2 overflow-x-auto pb-1">
        <Box title="Parser">{(data.parsers[0]?.states || []).map((s) => <Node key={s.name} n={s.name} d={(s.extracts.length ? 'extract ' + s.extracts.join(', ') : '—') + '\n→ ' + s.next.join(' | ')} />)}</Box>
        {(data.pipelines || []).map((pl) => (
          <div key={pl.name} className="flex gap-2"><Arrow />
            <Box title={pl.name.replace(/^My/, '') + ' — match-action'}>
              {!pl.tables.length && <div className="text-[11px] text-muted-foreground">jadval yo'q</div>}
              {pl.tables.map((t) => <Node key={t.name} n={t.name + (t.size ? ` [${t.size}]` : '')} d={'key: ' + (t.match.map((m) => `${m.field} (${m.type})`).join(', ') || '—')} chips={t.actions} />)}
            </Box>
          </div>
        ))}
        <Arrow /><Box title="Deparser">{(data.deparsers[0]?.order || []).map((x) => <Node key={x} n={'emit ' + x} />)}</Box>
      </div>
      {!!extra.length && <div className="text-[11px] text-muted-foreground">{extra.join('   ·   ')}</div>}
    </div>
  )
}

function SectionView({ s }: { s: InspectSection }) {
  return (
    <Section title={s.title} right={s.hint && <span className="text-[10.5px] font-normal text-muted-foreground">{s.hint}</span>}>
      {s.kind === 'table' ? <DataTable columns={s.columns || []} rows={(s.rows || []).map((r) => r.map((c) => (c == null ? '' : String(c))))} />
        : s.kind === 'pipeline' && s.data ? <PipelineView data={s.data} />
          : <pre className="max-h-80 overflow-auto whitespace-pre-wrap p-3 font-mono text-[11.5px] text-muted-foreground">{s.text || "(bo'sh)"}</pre>}
    </Section>
  )
}

function PortPanel({ name }: { name: string }) {
  const p = useStore((s) => s.project)
  const stats = useStore((s) => s.stats)
  const isRunning = useStore((s) => s.status.running)
  const ports = portsOf(p, name)
  const nums = Object.keys(ports).map(Number).sort((a, b) => a - b)
  return (
    <Section title={`${devLabel(p.devices[name].type)} — old panel (portlar, link/activity LED)`}>
      <div className="flex flex-wrap gap-2.5 bg-gradient-to-b from-zinc-800 to-zinc-900 p-3">
        {!nums.length && <div className="text-xs text-zinc-400">Port ulanmagan</div>}
        {nums.map((pt) => {
          const info = ports[pt], l = p.links[info.lid], st = stats[info.lid]
          const up = isRunning && l.up !== false
          const tx = st ? (info.side === 'a' ? st.ab_bps : st.ba_bps) : 0, rx = st ? (info.side === 'a' ? st.ba_bps : st.ab_bps) : 0
          return (
            <div key={pt} title={`${ifname(name, pt)} → ${info.peer}:eth${info.peerPort}`} className="w-[76px] rounded-md border border-zinc-700 bg-zinc-950 p-1.5 font-mono text-[10px] text-zinc-300">
              <div className="relative mb-1.5 h-6 rounded border border-zinc-700 bg-black"><div className="absolute bottom-1 left-[30%] right-[30%] h-1.5 rounded-sm bg-zinc-800" /></div>
              <div className="mb-1 flex gap-1">
                <i className={cn('h-2 w-2 rounded-full bg-zinc-700', l.up === false ? 'bg-red-500' : up && 'bg-emerald-400 shadow-[0_0_6px_#34d399]')} />
                <i className={cn('h-2 w-2 rounded-full bg-zinc-700', up && tx + rx > 0 && 'animate-pulse bg-amber-400 shadow-[0_0_6px_#fbbf24]')} />
              </div>
              <div>eth{pt}</div><div className="truncate text-zinc-500">→ {info.peer}</div>
              <div title={`TX ${fmtBps(tx)} / RX ${fmtBps(rx)}`}>{up ? '↑' + fmtBps(tx) : l.up === false ? 'DOWN' : '—'}</div>
              <div className="text-zinc-500">{l.params.medium === 'wifi' ? 'Wi-Fi' : `${l.params.bw}M ${l.params.delay}`}</div>
            </div>
          )
        })}
      </div>
    </Section>
  )
}

function InspectPane({ name, which, active }: { name: string; which: 'tables' | 'hw'; active: boolean }) {
  const isRunning = useStore((s) => s.status.running)
  const [secs, setSecs] = useState<InspectSection[] | null>(null)
  const [status, setStatus] = useState('')
  const [auto, setAuto] = useState(false)
  const load = async () => {
    const st = useStore.getState()
    if (!st.status.running) {
      const d = st.project.devices[name]
      if (which === 'hw' && d?.type === 'p4switch' && st.project.p4_programs[d.config?.program]) {
        try { const r = await api.post('/api/p4/compile', { source: st.project.p4_programs[d.config.program] }); setSecs([{ title: `P4 pipeline: ${d.config.program}` + (r.outline_only ? ' (manba tahlili)' : ''), kind: 'pipeline', data: r.pipeline }]) } catch { setSecs([]) }
      } else setSecs([])
      setStatus('Jonli jadvallar uchun tarmoqni ishga tushiring.'); return
    }
    setStatus('yuklanmoqda...')
    try {
      const r = await api.get<{ sections: InspectSection[]; demo?: boolean }>('/api/net/inspect/' + name)
      const hwRe = /hardware|pipeline|navbat|qdisc|port|trace|STP|radio/i
      setSecs(r.sections.filter((s) => which === 'hw' ? s.kind === 'pipeline' || hwRe.test(s.title) : s.kind !== 'pipeline' && !/hardware|trace/i.test(s.title)))
      setStatus((r.demo ? 'demo · ' : '') + clock())
    } catch (e: any) { setStatus(e.message) }
  }
  useEffect(() => { if (active) load() }, [active, isRunning])
  useEffect(() => { if (!auto) return; const t = setInterval(() => active && load(), 3000); return () => clearInterval(t) }, [auto, active])
  useEffect(() => on('ev:state', () => active && load()), [active])
  return (
    <>
      <div className="flex items-center gap-3">
        <Button size="sm" variant="secondary" onClick={load}>Yangilash</Button>
        <Checkbox checked={auto} onChange={setAuto} label="Avto (3s)" />
        <span className="text-xs text-muted-foreground">{status}</span>
      </div>
      {which === 'hw' && <PortPanel name={name} />}
      {secs?.map((s, i) => <SectionView key={i} s={s} />)}
    </>
  )
}
