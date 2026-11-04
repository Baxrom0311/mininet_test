import { useEffect, useRef, useState } from 'react'
import { ChevronDown, ChevronUp, Pause, Play, Trash } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Segmented } from '@/components/ui/tabs'
import { toast } from '@/components/ui/toast'
import { emit, on } from '@/lib/bus'
import { PROTO_COLORS, clock, storage } from '@/lib/format'
import { useStore } from '@/lib/store'
import { setHiddenProtos } from './Canvas'
import { cn } from '@/lib/utils'

type Pkt = { ts: number; link: string; dir: string; proto: string; summary: string }
type Log = { ts: number; level: string; msg: string; device?: string }

export const logLocal = (level: string, msg: string, device?: string) => emit('ev:log', { ts: Date.now() / 1000, level, msg, device })

export function Dock({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  const [tab, setTab] = useState<'pkt' | 'log'>('pkt')
  const [paused, setPaused] = useState(false)
  const [hidden, setHidden] = useState<Set<string>>(() => new Set(storage.get<string[]>('netlab.hiddenProto', ['STP', 'LLDP', 'IPv6'])))
  const pkts = useRef<Pkt[]>([]), logs = useRef<Log[]>([])
  const [, force] = useState(0)
  const [count, setCount] = useState(0)
  const body = useRef<HTMLDivElement>(null)
  const pausedRef = useRef(paused); pausedRef.current = paused

  useEffect(() => { setHiddenProtos(hidden); storage.set('netlab.hiddenProto', [...hidden]) }, [hidden])
  useEffect(() => {
    let pending = false
    const schedule = () => { if (!pending) { pending = true; requestAnimationFrame(() => { pending = false; force((x) => x + 1) }) } }
    const a = on('ev:packet', (p: Pkt) => {
      setCount((c) => c + 1)
      if (pausedRef.current) return
      pkts.current.push(p); if (pkts.current.length > 2000) pkts.current.splice(0, 500)
      schedule()
    })
    const b = on('ev:log', (e: Log) => {
      logs.current.push(e); if (logs.current.length > 1000) logs.current.splice(0, 200)
      if (e.level === 'error') toast((e.device ? e.device + ': ' : '') + e.msg, 'error')
      schedule()
    })
    return () => { a(); b() }
  })
  useEffect(() => { if (body.current) body.current.scrollTop = 1e9 })

  const project = useStore.getState().project
  const linkName = (lid: string, dir: string) => {
    const l = project.links[lid]; if (!l) return lid
    const [a, b] = dir === 'ba' ? [l.b, l.a] : [l.a, l.b]
    return `${a.dev}:${a.port} → ${b.dev}:${b.port}`
  }
  const rows = tab === 'pkt' ? pkts.current.filter((p) => !hidden.has(p.proto)).slice(-300) : []
  return (
    <section className="glass flex min-h-0 flex-col overflow-hidden rounded-xl">
      <div className="flex items-center gap-3 border-b px-2 py-1.5">
        <Segmented value={tab} onChange={setTab} items={[{ value: 'pkt', label: 'Paketlar' }, { value: 'log', label: 'Hodisalar' }]} />
        {tab === 'pkt' && !collapsed && (
          <div className="flex flex-wrap gap-x-2 gap-y-0.5">
            {Object.keys(PROTO_COLORS).map((p) => (
              <Checkbox key={p} checked={!hidden.has(p)} label={<span style={{ color: PROTO_COLORS[p] }} className="text-[11px]">{p}</span>}
                onChange={(v) => setHidden((s) => { const n = new Set(s); if (v) n.delete(p); else n.add(p); return n })} />
            ))}
          </div>
        )}
        <div className="flex-1" />
        <span className="text-[11px] text-muted-foreground">{count} paket</span>
        <Button size="xs" variant="ghost" onClick={() => setPaused(!paused)}>{paused ? <><Play /> Davom</> : <><Pause /> Pauza</>}</Button>
        <Button size="xs" variant="ghost" onClick={() => { (tab === 'pkt' ? pkts : logs).current = []; force((x) => x + 1) }}><Trash /> Tozalash</Button>
        <Button size="icon" variant="ghost" className="h-7 w-7" onClick={onToggle}>{collapsed ? <ChevronUp /> : <ChevronDown />}</Button>
      </div>
      {!collapsed && (
        <div ref={body} className="min-h-0 flex-1 overflow-auto font-mono text-[11.5px]">
          {tab === 'pkt' ? rows.map((p, i) => (
            <div key={i} className="grid grid-cols-[76px_160px_60px_1fr] gap-2.5 whitespace-nowrap border-b border-border/40 px-3 py-0.5">
              <span className="text-muted-foreground">{clock(p.ts)}</span><span className="truncate">{linkName(p.link, p.dir)}</span>
              <span className="font-bold" style={{ color: PROTO_COLORS[p.proto] || PROTO_COLORS.other }}>{p.proto}</span>
              <span className="truncate" title={p.summary}>{p.summary}</span>
            </div>
          )) : logs.current.slice(-300).map((e, i) => (
            <div key={i} className="grid grid-cols-[76px_72px_1fr] gap-2.5 border-b border-border/40 px-3 py-0.5">
              <span className="text-muted-foreground">{clock(e.ts)}</span>
              <span className={cn(e.level === 'ok' && 'text-success', e.level === 'error' && 'text-destructive', e.level === 'warn' && 'text-warning')}>{e.device || e.level}</span>
              <span className="whitespace-pre-wrap">{e.msg}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
