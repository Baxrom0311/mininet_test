/** Buyruqlar palitrasi (Ctrl+K): qurilma, ssenariy, dastur, namuna va buyruqlarni tez qidirish. */
import { useEffect, useMemo, useRef, useState } from 'react'
import * as DialogPrimitive from '@radix-ui/react-dialog'
import { BarChart3, Cpu, FilePlus2, FolderOpen, LineChart, Maximize2, Moon, Network, Play, Redo2, Save, Search, Square, Undo2 } from 'lucide-react'
import { DeviceIcon } from '@/components/DeviceIcon'
import { toast } from '@/components/ui/toast'
import { api } from '@/lib/api'
import { addDevice, devLabel, emptyProject, loadProject, openWin, redo, running, saveProject, select, startNet, stopNet, undo, useStore } from '@/lib/store'
import type { DeviceType, Project } from '@/lib/types'
import { canvasApi } from './Canvas'
import { cn } from '@/lib/utils'

type Cmd = { id: string; group: string; label: string; hint?: string; icon?: React.ReactNode; run: () => void }

export const paletteState = { open: (_v: boolean) => {} }

export function CommandPalette({ onToggleTheme }: { onToggleTheme: () => void }) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [idx, setIdx] = useState(0)
  const [examples, setExamples] = useState<string[]>([])
  const listRef = useRef<HTMLDivElement>(null)
  const project = useStore((s) => s.project)
  const isRunning = useStore((s) => s.status.running)
  paletteState.open = setOpen
  useEffect(() => {
    if (open) { setQ(''); setIdx(0); api.get('/api/projects').then((r) => setExamples(r.examples.map((x: { name: string }) => x.name))).catch(() => {}) }
  }, [open])

  const cmds = useMemo<Cmd[]>(() => {
    const out: Cmd[] = [
      { id: 'run', group: 'Buyruqlar', label: isRunning ? "Tarmoqni to'xtatish" : 'Tarmoqni ishga tushirish', icon: isRunning ? <Square /> : <Play />, run: () => (isRunning ? stopNet() : startNet()) },
      { id: 'save', group: 'Buyruqlar', label: 'Loyihani saqlash', hint: 'Ctrl+S', icon: <Save />, run: () => saveProject().catch((e) => toast(e.message, 'error')) },
      { id: 'undo', group: 'Buyruqlar', label: 'Bekor qilish', hint: 'Ctrl+Z', icon: <Undo2 />, run: undo },
      { id: 'redo', group: 'Buyruqlar', label: 'Qaytarish', hint: 'Ctrl+Shift+Z', icon: <Redo2 />, run: redo },
      { id: 'fit', group: 'Buyruqlar', label: "Hammasini ko'rsatish", hint: 'F', icon: <Maximize2 />, run: () => canvasApi.fit() },
      { id: 'results', group: 'Buyruqlar', label: 'Natijalar va solishtirish', icon: <BarChart3 />, run: () => openWin('results', 'main', { w: 1060, h: 700 }) },
      { id: 'theme', group: 'Buyruqlar', label: 'Kun / tun mavzusi', icon: <Moon />, run: onToggleTheme },
      { id: 'new', group: 'Buyruqlar', label: 'Yangi loyiha', icon: <FilePlus2 />, run: () => {
        if (running()) return toast("Avval tarmoqni to'xtating", 'warn')
        if (!useStore.getState().dirty || confirm("Saqlanmagan o'zgarishlar bor. Davom etilsinmi?")) loadProject(emptyProject())
      } },
    ]
    const types: DeviceType[] = ['host', 'server', 'switch', 'p4switch', 'router', 'sdnrouter', 'wifiap']
    for (const t of types) out.push({ id: 'add-' + t, group: "Qurilma qo'shish", label: `${devLabel(t)} qo'shish`, icon: <DeviceIcon type={t} size={16} />,
      run: () => { const ds = Object.values(useStore.getState().project.devices); const x = ds.length ? Math.max(...ds.map((d) => d.x)) + 120 : 300; const y = ds.length ? ds.reduce((a, d) => a + d.y, 0) / ds.length : 300; addDevice(t, x, y) } })
    for (const [n, d] of Object.entries(project.devices)) {
      const ips = Object.values((d.config?.interfaces || {}) as Record<string, { ip?: string }>).map((i) => i.ip).filter(Boolean).join(' ')
      out.push({ id: 'dev-' + n, group: 'Qurilmalar', label: n, hint: `${devLabel(d.type)} ${ips}`, icon: <DeviceIcon type={d.type} size={16} />, run: () => { select({ kind: 'device', id: n }); openWin('device', n) } })
    }
    for (const n of Object.keys(project.scenarios || {})) out.push({ id: 'sc-' + n, group: 'Ssenariylar', label: n, icon: <LineChart />, run: () => openWin('scenario', n, { w: 940, h: 680 }) })
    for (const n of Object.keys(project.p4_programs)) out.push({ id: 'p4-' + n, group: 'P4 / SDN', label: n + '.p4', icon: <Cpu />, run: () => openWin('p4', n, { w: 880, h: 640 }) })
    for (const n of Object.keys(project.controllers)) out.push({ id: 'sdn-' + n, group: 'P4 / SDN', label: n, hint: 'SDN kontroller', icon: <Network />, run: () => openWin('sdn', n, { w: 880, h: 660 }) })
    for (const n of examples) out.push({ id: 'ex-' + n, group: 'Namunalar', label: n, hint: 'namunani ochish', icon: <FolderOpen />, run: async () => {
      if (running()) return toast("Avval tarmoqni to'xtating", 'warn')
      if (useStore.getState().dirty && !confirm("Saqlanmagan o'zgarishlar bor. Davom etilsinmi?")) return
      try { loadProject(await api.get<Project>(`/api/projects/${encodeURIComponent(n)}?example=true`), true); setTimeout(canvasApi.fit, 60) } catch (e: any) { toast(e.message, 'error') }
    } })
    return out
  }, [project, isRunning, examples, onToggleTheme])

  const filtered = useMemo(() => {
    const terms = q.toLowerCase().split(/\s+/).filter(Boolean)
    if (!terms.length) return cmds.filter((c) => c.group !== 'Namunalar' || cmds.length < 40)
    return cmds.filter((c) => { const hay = `${c.label} ${c.hint || ''} ${c.group}`.toLowerCase(); return terms.every((t) => hay.includes(t)) })
  }, [cmds, q])
  useEffect(() => { setIdx(0) }, [q])
  useEffect(() => { listRef.current?.querySelector('[data-active=true]')?.scrollIntoView({ block: 'nearest' }) }, [idx])

  const exec = (c?: Cmd) => { if (!c) return; setOpen(false); setTimeout(c.run, 0) }
  let lastGroup = ''
  return (
    <DialogPrimitive.Root open={open} onOpenChange={setOpen}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-[300] bg-black/40 backdrop-blur-[2px]" />
        <DialogPrimitive.Content className="glass fixed left-1/2 top-[14vh] z-[301] w-[min(620px,92vw)] -translate-x-1/2 overflow-hidden rounded-xl p-0 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95">
          <DialogPrimitive.Title className="sr-only">Buyruqlar palitrasi</DialogPrimitive.Title>
          <div className="flex items-center gap-2 border-b px-3">
            <Search className="h-4 w-4 text-muted-foreground" />
            <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Qurilma, ssenariy, buyruq yoki namunani qidiring…"
              className="h-11 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
              onKeyDown={(e) => {
                if (e.key === 'ArrowDown') { e.preventDefault(); setIdx((i) => Math.min(i + 1, filtered.length - 1)) }
                else if (e.key === 'ArrowUp') { e.preventDefault(); setIdx((i) => Math.max(i - 1, 0)) }
                else if (e.key === 'Enter') { e.preventDefault(); exec(filtered[idx]) }
              }} />
            <span className="kbd">Esc</span>
          </div>
          <div ref={listRef} className="max-h-[52vh] overflow-auto p-1.5">
            {!filtered.length && <div className="px-3 py-6 text-center text-sm text-muted-foreground">Hech narsa topilmadi</div>}
            {filtered.map((c, i) => {
              const head = c.group !== lastGroup ? (lastGroup = c.group) : null
              return (
                <div key={c.id}>
                  {head && <div className="px-2 pb-1 pt-2 text-[10.5px] font-semibold uppercase tracking-wider text-muted-foreground">{head}</div>}
                  <button data-active={i === idx} onMouseMove={() => setIdx(i)} onClick={() => exec(c)}
                    className={cn('flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left text-sm [&_svg]:size-4 [&_svg]:shrink-0', i === idx ? 'bg-accent text-accent-foreground' : 'text-foreground/90')}>
                    <span className="text-muted-foreground">{c.icon}</span>
                    <span className="truncate">{c.label}</span>
                    {c.hint && <span className="ml-auto truncate pl-3 font-mono text-[11px] text-muted-foreground">{c.hint}</span>}
                  </button>
                </div>
              )
            })}
          </div>
          <div className="flex gap-3 border-t px-3 py-1.5 text-[11px] text-muted-foreground"><span><span className="kbd">↑↓</span> tanlash</span><span><span className="kbd">Enter</span> bajarish</span><span><span className="kbd">Ctrl K</span> ochish/yopish</span></div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
