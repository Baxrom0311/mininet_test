import { useState } from 'react'
import { Activity, BarChart3, Cable, Cpu, LineChart, MousePointer2, Network, Plus, Trash2 } from 'lucide-react'
import { DeviceIcon } from '@/components/DeviceIcon'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Field, Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { toast } from '@/components/ui/toast'
import { devLabel, mutate, openWin, setMode, useStore } from '@/lib/store'
import type { DeviceType } from '@/lib/types'
import { cn } from '@/lib/utils'

const PALETTE: DeviceType[] = ['host', 'server', 'switch', 'p4switch', 'router', 'sdnrouter', 'wifiap']

function SecTitle({ children, onAdd, addTitle }: { children: React.ReactNode; onAdd?: () => void; addTitle?: string }) {
  return (
    <div className="flex items-center justify-between px-1 text-[10.5px] font-semibold uppercase tracking-wider text-muted-foreground">
      {children}
      {onAdd && <button title={addTitle} onClick={onAdd} className="rounded p-0.5 hover:bg-accent hover:text-foreground"><Plus className="h-3.5 w-3.5" /></button>}
    </div>
  )
}

export function Sidebar() {
  const mode = useStore((s) => s.mode)
  const placeType = useStore((s) => s.placeType)
  const project = useStore((s) => s.project)
  const [dlg, setDlg] = useState<null | 'p4' | 'sdn' | 'scenario'>(null)

  const tools: [typeof mode, string, string, React.ReactNode][] = [
    ['select', "Tanlash / ko'chirish", 'V', <MousePointer2 key="s" />],
    ['link', 'Kabel ulash', 'C', <Cable key="l" />],
    ['delete', "O'chirish", 'D', <Trash2 key="d" />],
  ]
  return (
    <aside className="glass flex min-h-0 flex-col gap-4 overflow-auto rounded-xl p-3">
      <div className="flex flex-col gap-2">
        <SecTitle>Qurilmalar</SecTitle>
        <div className="grid grid-cols-2 gap-1.5">
          {PALETTE.map((t) => (
            <button key={t} draggable onDragStart={(e) => e.dataTransfer.setData('text/netlab-type', t)}
              onClick={() => setMode(mode === 'place' && placeType === t ? 'select' : 'place', t)}
              title="Bosing va kanvasga joylang (yoki sudrang). Shift — bir nechta."
              className={cn('flex flex-col items-center gap-1 rounded-lg border bg-background/30 px-1 pb-2 pt-2.5 text-[11px] text-muted-foreground transition-colors hover:border-foreground/30 hover:text-foreground',
                mode === 'place' && placeType === t && 'border-foreground/60 bg-accent text-foreground')}>
              <DeviceIcon type={t} size={28} />
              {devLabel(t)}
            </button>
          ))}
        </div>
      </div>
      <div className="flex flex-col gap-1">
        <SecTitle>Asboblar</SecTitle>
        {tools.map(([m, label, key, icon]) => (
          <button key={m} onClick={() => setMode(m)}
            className={cn('flex items-center gap-2 rounded-md px-2 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground [&_svg]:size-4',
              mode === m && 'bg-accent text-foreground')}>
            {icon}<span>{label}</span><span className="kbd ml-auto">{key}</span>
          </button>
        ))}
      </div>
      <ResList title="P4 dasturlar" icon={<Cpu />} items={Object.keys(project.p4_programs).map((n) => [n, '.p4'])}
        empty="yo'q — P4 switch qo'shilganda shablon qo'shiladi" onAdd={() => setDlg('p4')} onOpen={(n) => openWin('p4', n, { w: 880, h: 640 })} />
      <ResList title="SDN kontrollerlar" icon={<Network />} items={Object.entries(project.controllers).map(([n, c]) => [n, ':' + c.port])}
        empty="yo'q" onAdd={() => setDlg('sdn')} onOpen={(n) => openWin('sdn', n, { w: 880, h: 660 })} />
      <ResList title="Ssenariylar" icon={<LineChart />} items={Object.entries(project.scenarios || {}).map(([n, sc]) => [n, `${(sc.apps || []).length} ilova`])}
        empty="yo'q — trafik va hodisalarni tahlil qilish uchun" onAdd={() => setDlg('scenario')} onOpen={(n) => openWin('scenario', n, { w: 940, h: 680 })} />
      <Button variant="outline" size="sm" onClick={() => openWin('telemetry', 'main', { w: 980, h: 680 })}><Activity /> L4 Observatory</Button>
      <Button variant="outline" size="sm" onClick={() => openWin('results', 'main', { w: 1060, h: 700 })}><BarChart3 /> Natijalar va solishtirish</Button>
      <NewResourceDialog kind={dlg} onClose={() => setDlg(null)} />
    </aside>
  )
}

function ResList({ title, icon, items, empty, onAdd, onOpen }: { title: string; icon: React.ReactNode; items: [string, string][]; empty: string; onAdd: () => void; onOpen: (n: string) => void }) {
  return (
    <div className="flex flex-col gap-1">
      <SecTitle onAdd={onAdd} addTitle={'Yangi: ' + title}>{title}</SecTitle>
      {!items.length && <div className="px-1 text-[11px] text-muted-foreground/80">{empty}</div>}
      {items.map(([n, tag]) => (
        <button key={n} onClick={() => onOpen(n)} className="flex items-center gap-2 rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-accent hover:text-foreground [&_svg]:size-3.5">
          {icon}<span className="truncate">{n}</span><span className="ml-auto font-mono text-[10px] opacity-70">{tag}</span>
        </button>
      ))}
    </div>
  )
}

function NewResourceDialog({ kind, onClose }: { kind: null | 'p4' | 'sdn' | 'scenario'; onClose: () => void }) {
  const templates = useStore((s) => s.templates)
  const [name, setName] = useState('')
  const [tpl, setTpl] = useState<string>('')
  const tpls = kind === 'p4' ? templates.p4 : kind === 'sdn' ? templates.sdn : {}
  const create = () => {
    const n = name.trim()
    const p = useStore.getState().project
    if (kind === 'scenario') {
      if (!/^[A-Za-z0-9_-]{1,30}$/.test(n)) return toast('Nom: harf/raqam/_/- (1-30)', 'error')
      if (p.scenarios[n]) return toast('Bu nom band', 'error')
      mutate((q) => { q.scenarios[n] = { duration: 30, apps: [], events: [] } })
      openWin('scenario', n, { w: 940, h: 680 })
    } else {
      const re = kind === 'p4' ? /^[a-z][a-z0-9_]{0,23}$/ : /^[a-z][a-z0-9]{0,7}$/
      if (!re.test(n)) return toast(kind === 'p4' ? 'Nom: kichik harf, [a-z0-9_], 1-24' : 'Nom: kichik harf, [a-z0-9], 1-8', 'error')
      const src = tpl && tpl !== '__empty__' ? tpls[tpl] : ''
      if (kind === 'p4') {
        if (p.p4_programs[n]) return toast('Bu nom band', 'error')
        mutate((q) => { q.p4_programs[n] = src }); openWin('p4', n, { w: 880, h: 640 })
      } else {
        if (p.controllers[n]) return toast('Bu nom band', 'error')
        const used = Object.values(p.controllers).map((c) => c.port)
        let port = 6653; while (used.includes(port)) port++
        mutate((q) => { q.controllers[n] = { code: src, port } }); openWin('sdn', n, { w: 880, h: 660 })
      }
    }
    setName(''); onClose()
  }
  const title = kind === 'p4' ? 'Yangi P4 dastur' : kind === 'sdn' ? 'Yangi SDN kontroller' : 'Yangi ssenariy'
  return (
    <Dialog open={!!kind} onOpenChange={(o) => !o && onClose()} title={title}
      description={kind === 'scenario' ? "Ssenariy = trafik ilovalari (UDP/TCP/ping) + vaqtli hodisalar. Analitik, ns-3 yoki Mininet dvigatelida ishga tushirib, natijalarni solishtirasiz." : undefined}
      footer={<><Button variant="ghost" onClick={onClose}>Bekor</Button><Button onClick={create}>Yaratish</Button></>}>
      <Field label="Nom"><Input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder={kind === 'p4' ? 'my_router' : kind === 'sdn' ? 'c2' : 'uzilish'} onKeyDown={(e) => e.key === 'Enter' && create()} /></Field>
      {kind !== 'scenario' && (
        <Field label="Asos">
          <Select value={tpl || Object.keys(tpls)[0] || '__empty__'} onChange={setTpl}
            options={[{ value: '__empty__', label: "— bo'sh —" }, ...Object.keys(tpls).map((t) => ({ value: t, label: 'shablon: ' + t }))]} />
        </Field>
      )}
    </Dialog>
  )
}
