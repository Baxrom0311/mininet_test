import { useEffect, useState } from 'react'
import { Cable, Folder, Radio, X } from 'lucide-react'
import { DeviceIcon } from '@/components/DeviceIcon'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Field, Input, Textarea } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { toast } from '@/components/ui/toast'
import { fmtBps } from '@/lib/format'
import { devLabel, mutate, openWin, portsOf, removeDevice, removeLink, running, select, setInspectorOpen, updateLink, useStore } from '@/lib/store'
import { apConfig, linkEstimate } from '@/lib/wifi'

function Head({
  icon,
  title,
  sub,
  onClose,
}: {
  icon: React.ReactNode
  title: string
  sub: string
  onClose?: () => void
}) {
  return (
    <div className="flex items-center gap-3 border-b border-border/50 px-4 py-2.5 bg-card/20 shrink-0">
      <span className="text-foreground">{icon}</span>
      <div className="min-w-0 flex-1">
        <div className="truncate text-xs font-semibold">{title}</div>
        <div className="truncate text-[10.5px] text-muted-foreground">{sub}</div>
      </div>
      {onClose && (
        <button
          onClick={onClose}
          className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
          title="Yopish (Esc)"
        >
          <X className="h-4 w-4" />
        </button>
      )}
    </div>
  )
}

function KV({ rows }: { rows: [string, React.ReactNode][] }) {
  return (
    <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-xs">
      {rows.map(([k, v], i) => (
        <div key={i} className="contents">
          <div className="text-muted-foreground">{k}</div>
          <div className="break-all font-mono text-[11px]">{v}</div>
        </div>
      ))}
    </div>
  )
}

export function Inspector({ onClose }: { onClose?: () => void }) {
  const sel = useStore((s) => s.selection)
  const project = useStore((s) => s.project)

  const handleClose = () => {
    select(null)
    setInspectorOpen(false)
    onClose?.()
  }

  return (
    <aside className="glass flex h-full min-h-0 w-80 flex-col overflow-hidden rounded-xl shadow-xl animate-in slide-in-from-right-3 duration-150">
      {sel?.kind === 'device' && project.devices[sel.id] ? (
        <DeviceInsp name={sel.id} onClose={handleClose} />
      ) : sel?.kind === 'link' && project.links[sel.id] ? (
        <LinkInsp key={sel.id} lid={sel.id} onClose={handleClose} />
      ) : (
        <ProjectInsp onClose={handleClose} />
      )}
    </aside>
  )
}

function ProjectInsp({ onClose }: { onClose?: () => void }) {
  const p = useStore((s) => s.project)
  const info = useStore((s) => s.info)
  const isRunning = useStore((s) => s.status.running)
  const types: Record<string, number> = {}
  Object.values(p.devices).forEach((d) => {
    types[d.type] = (types[d.type] || 0) + 1
  })
  const [notes, setNotes] = useState(p.notes || '')
  const [mpp, setMpp] = useState(String(p.meters_per_px ?? 0.25))

  useEffect(() => {
    setNotes(p.notes || '')
    setMpp(String(p.meters_per_px ?? 0.25))
  }, [p.name])

  return (
    <>
      <Head icon={<Folder className="h-5 w-5 text-primary" />} title="Loyiha sozlamalari" sub={p.name} onClose={onClose} />
      <div className="flex flex-col gap-3.5 overflow-auto p-4 text-xs">
        <KV
          rows={[
            ['Loyiha', p.name],
            ['Qurilmalar', String(Object.keys(p.devices).length)],
            ...Object.entries(types).map(([t, n]) => ['  ' + devLabel(t as any), String(n)] as [string, string]),
            ['Kabellar', String(Object.keys(p.links).length)],
            ['P4 dasturlar', String(Object.keys(p.p4_programs).length)],
            ['Kontrollerlar', String(Object.keys(p.controllers).length)],
            ['Ssenariylar', String(Object.keys(p.scenarios || {}).length)],
            ['Driver', (info?.driver || '') + (isRunning ? ' · ishlayapti' : '')],
          ]}
        />
        <Field label="Masshtab: 1 px = ? metr (Wi-Fi masofalari uchun)">
          <Input
            type="number"
            step="any"
            value={mpp}
            onChange={(e) => setMpp(e.target.value)}
            onBlur={() => {
              const v = parseFloat(mpp)
              if (v > 0 && v !== p.meters_per_px) mutate((q) => { q.meters_per_px = v })
            }}
          />
        </Field>
        <Field label="Loyiha tavsifi va izohlari">
          <Textarea
            rows={5}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            onBlur={() => notes !== (p.notes || '') && mutate((q) => { q.notes = notes })}
            placeholder="Loyiha vazifalari, IP reja, xulosalar..."
          />
        </Field>
      </div>
    </>
  )
}

function DeviceInsp({ name, onClose }: { name: string; onClose?: () => void }) {
  const p = useStore((s) => s.project)
  const d = p.devices[name]
  if (!d) return null
  const cfg = d.config || {}
  const ports = portsOf(p, name)
  const rows: [string, React.ReactNode][] = [
    ['Tur', devLabel(d.type)],
    ['ID / dpid', `${d.id} / ${d.id.toString(16).padStart(16, '0')}`],
  ]
  if (cfg.mode) rows.push(['Rejim', cfg.mode + (cfg.stp ? ' + STP' : '')])
  if (cfg.controller) rows.push(['Kontroller', cfg.controller])
  if (cfg.program) rows.push(['P4 dastur', cfg.program])
  if (cfg.daemons) rows.push(['FRR', ['zebra', 'staticd', ...cfg.daemons].join(', ')])
  if (cfg.gateway) rows.push(['Gateway', cfg.gateway])
  if (d.type === 'wifiap') {
    const c = apConfig(useStore.getState().info?.wifi, d)
    rows.push(['Radio', `${c.standard.replace('80211', '802.11')} · ${c.band} GHz · kanal ${c.channel} · ${c.tx_power} dBm`])
  }
  Object.keys(ports)
    .map(Number)
    .sort((a, b) => a - b)
    .forEach((pt) => {
      const ip = cfg.interfaces?.[`${name}-eth${pt}`]?.ip
      rows.push(['eth' + pt, <>→ {ports[pt].peer}:eth{ports[pt].peerPort}{ip && <><br />{ip}</>}</>])
    })
  const open = (tab?: string) => openWin('device', name, { tab })

  return (
    <>
      <Head icon={<DeviceIcon type={d.type} size={24} />} title={name} sub={devLabel(d.type)} onClose={onClose} />
      <div className="flex flex-col gap-3.5 overflow-auto p-4 text-xs">
        <KV rows={rows} />
        <div className="grid grid-cols-2 gap-1.5 pt-1">
          <Button size="sm" className="col-span-2" onClick={() => open()}>Qurilmaga kirish (Terminal)</Button>
          <Button size="sm" variant="outline" onClick={() => open('cli')}>CLI</Button>
          <Button size="sm" variant="outline" onClick={() => open('tbl')}>Jadvallar</Button>
          <Button size="sm" variant="outline" onClick={() => open('hw')}>Hardware</Button>
          <Button size="sm" variant="outline" onClick={() => open('cfg')}>Sozlama</Button>
          <Button size="sm" variant="outline" className="col-span-2 text-destructive hover:bg-destructive/10" onClick={() => removeDevice(name)}>
            Qurilmani o'chirish
          </Button>
        </div>
      </div>
    </>
  )
}

function LinkInsp({ lid, onClose }: { lid: string; onClose?: () => void }) {
  const p = useStore((s) => s.project)
  const wifi = useStore((s) => s.info?.wifi)
  const st = useStore((s) => s.stats[lid])
  const hist = useStore((s) => s.history[lid]) || []
  const isRunning = useStore((s) => s.status.running)
  const l = p.links[lid]
  if (!l) return null
  const prm = l.params
  const [f, setF] = useState({ bw: String(prm.bw), delay: prm.delay, jitter: prm.jitter, loss: String(prm.loss), queue: String(prm.queue) })
  const ta = p.devices[l.a.dev]?.type, tb = p.devices[l.b.dev]?.type, L3 = ['host', 'server', 'router']
  const canWifi = (ta === 'wifiap' && L3.includes(tb)) || (tb === 'wifiap' && L3.includes(ta))
  const est = wifi ? linkEstimate(wifi, p, lid) : null

  const medium = canWifi && (
    <Field label="Muhit">
      <Select
        value={prm.medium === 'wifi' ? 'wifi' : 'wire'}
        options={[
          { value: 'wifi', label: 'Wi-Fi (simsiz)' },
          { value: 'wire', label: 'Kabel (simli)' },
        ]}
        onChange={(v) => {
          if (running()) return toast("Tarmoq ishlayotganda muhit o'zgarmaydi", 'warn')
          mutate((q) => {
            q.links[lid].params = { ...q.links[lid].params, medium: v as 'wire' | 'wifi' }
          })
        }}
      />
    </Field>
  )

  const upBox = (
    <Checkbox
      checked={l.up !== false}
      onChange={(v) => updateLink(lid, null, v)}
      label="Ulangan (UP) — o'chirsangiz link uziladi"
    />
  )

  if (est) {
    const c = apConfig(wifi, p.devices[est.ap!])
    return (
      <>
        <Head icon={<Radio className="h-5 w-5 text-sky-400" />} title={'Wi-Fi ' + lid} sub={`${l.a.dev} ⋯ ${l.b.dev}`} onClose={onClose} />
        <div className="flex flex-col gap-3 overflow-auto p-4 text-xs">
          {medium}
          <KV
            rows={[
              ['AP', `${est.ap} (${c.standard.replace('80211', '802.11')} · ${c.band} GHz · ${c.width} MHz · ${c.tx_power} dBm)`],
              ['Stansiya', est.sta!],
              ['Masofa', est.distance_m.toFixed(1) + ' m'],
              ['RSSI', est.rssi_dbm.toFixed(1) + ' dBm'],
              ['SNR', est.snr_db.toFixed(1) + ' dB'],
              ['MCS / PHY', est.ok ? `MCS${est.mcs} · ${est.phy_mbps.toFixed(1)} Mbit/s` : '— (aloqa yo‘q)'],
              ['Foydali (taxmin)', est.ok ? `~${est.thr_mbps.toFixed(1)} Mbit/s` : '—'],
            ]}
          />
          {upBox}
          <Button size="sm" variant="outline" className="text-destructive hover:bg-destructive/10" onClick={() => removeLink(lid)}>
            Kabelni o'chirish
          </Button>
        </div>
      </>
    )
  }

  const apply = async () => {
    const ok = await updateLink(lid, {
      bw: parseFloat(f.bw),
      delay: f.delay.trim(),
      jitter: f.jitter.trim(),
      loss: parseFloat(f.loss) || 0,
      queue: parseInt(f.queue, 10) || 1000,
    })
    if (ok) toast(isRunning ? "Kabel parametrlari qo'llandi (tc)" : 'Saqlandi', 'ok')
  }

  const cap = (parseFloat(String(prm.bw)) || 100) * 1e6
  const mx = Math.max(1, ...hist.map((x) => Math.max(x.ab, x.ba)))
  const path = (k: 'ab' | 'ba') => hist.map((x, i) => `${i ? 'L' : 'M'}${(i / 59) * 240},${44 - (x[k] / mx) * 40}`).join('')

  return (
    <>
      <Head icon={<Cable className="h-5 w-5 text-emerald-400" />} title={'Kabel ' + lid} sub={`${l.a.dev} ↔ ${l.b.dev}`} onClose={onClose} />
      <div className="flex flex-col gap-3 overflow-auto p-4 text-xs">
        {medium}
        <KV
          rows={[
            ['A', `${l.a.dev}:eth${l.a.port}`],
            ['B', `${l.b.dev}:eth${l.b.port}`],
          ]}
        />
        {upBox}
        <div className="grid grid-cols-2 gap-2">
          <Field label="Tezlik (Mbit/s)">
            <Input type="number" value={f.bw} onChange={(e) => setF({ ...f, bw: e.target.value })} />
          </Field>
          <Field label="Navbat (paket)">
            <Input type="number" value={f.queue} onChange={(e) => setF({ ...f, queue: e.target.value })} />
          </Field>
          <Field label="Kechikish">
            <Input value={f.delay} onChange={(e) => setF({ ...f, delay: e.target.value })} />
          </Field>
          <Field label="Jitter">
            <Input value={f.jitter} onChange={(e) => setF({ ...f, jitter: e.target.value })} />
          </Field>
          <Field label="Yo'qotish (%)" className="col-span-2">
            <Input type="number" step="any" value={f.loss} onChange={(e) => setF({ ...f, loss: e.target.value })} />
          </Field>
        </div>
        <div className="flex gap-2">
          <Button size="sm" onClick={apply}>Qo'llash</Button>
          <Button size="sm" variant="outline" className="text-destructive hover:bg-destructive/10" onClick={() => removeLink(lid)}>
            Kabelni o'chirish
          </Button>
        </div>
        {isRunning && (
          <div className="flex flex-col gap-1.5 rounded-lg border p-2.5 text-xs">
            <div className="font-semibold text-[11px]">Jonli trafik</div>
            {(['ab', 'ba'] as const).map((k) => {
              const v = st ? (k === 'ab' ? st.ab_bps : st.ba_bps) : 0
              return (
                <div key={k} className="flex flex-col gap-0.5">
                  <div className="flex justify-between text-muted-foreground text-[10.5px]">
                    <span>{k === 'ab' ? `${l.a.dev} → ${l.b.dev}` : `${l.b.dev} → ${l.a.dev}`}</span>
                    <span className="font-mono">{fmtBps(v)}</span>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded bg-muted">
                    <div className="h-full bg-foreground transition-all" style={{ width: Math.min(100, (v / cap) * 100) + '%' }} />
                  </div>
                </div>
              )
            })}
            <svg viewBox="0 0 240 46" preserveAspectRatio="none" className="h-10 w-full mt-1">
              <path d={path('ab')} fill="none" stroke="hsl(var(--foreground))" strokeWidth={1.6} />
              <path d={path('ba')} fill="none" stroke="hsl(var(--muted-foreground))" strokeWidth={1.6} strokeDasharray="3 2" />
            </svg>
          </div>
        )}
      </div>
    </>
  )
}
