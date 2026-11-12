import { useEffect, useRef, useState } from 'react'
import {
  Activity,
  BarChart3,
  ChevronDown,
  Download,
  FilePlus2,
  FolderOpen,
  Moon,
  Network,
  Play,
  Radio,
  Redo2,
  Save,
  Search,
  Shield,
  Square,
  Sun,
  Undo2,
  Upload,
} from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Tip } from '@/components/ui/tooltip'
import { toast } from '@/components/ui/toast'
import { api } from '@/lib/api'
import {
  emptyProject,
  loadProject,
  mutate,
  redo,
  running,
  saveProject,
  setActiveTab,
  startNet,
  stopNet,
  toggleTheme,
  undo,
  useStore,
} from '@/lib/store'
import type { Project } from '@/lib/types'
import { canvasApi } from './Canvas'
import { paletteState } from './CommandPalette'
import { cn } from '@/lib/utils'

export function Topbar() {
  const project = useStore((s) => s.project)
  const dirty = useStore((s) => s.dirty)
  const status = useStore((s) => s.status)
  const info = useStore((s) => s.info)
  const busy = useStore((s) => s.busy)
  const wsOk = useStore((s) => s.wsOk)
  const dark = useStore((s) => s.theme) === 'dark'
  const activeTab = useStore((s) => s.activeTab)
  const canUndo = useStore((s) => s.canUndo), canRedo = useStore((s) => s.canRedo)
  const [openDlg, setOpenDlg] = useState(false)
  const [projectMenuOpen, setProjectMenuOpen] = useState(false)
  const [capsOpen, setCapsOpen] = useState(false)
  const [name, setName] = useState(project.name)
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => setName(project.name), [project.name])

  // Click outside to close project menu
  useEffect(() => {
    const onDocClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setProjectMenuOpen(false)
      }
    }
    if (projectMenuOpen) document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [projectMenuOpen])

  const confirmDirty = () => !useStore.getState().dirty || confirm("Saqlanmagan o'zgarishlar bor. Davom etilsinmi?")

  const exportJson = () => {
    const a = document.createElement('a')
    a.href = URL.createObjectURL(new Blob([JSON.stringify(project, null, 2)], { type: 'application/json' }))
    a.download = (project.name || 'netlab') + '.json'
    a.click()
    setTimeout(() => URL.revokeObjectURL(a.href), 2000)
    setProjectMenuOpen(false)
  }

  const importJson = () => {
    if (running()) return toast("Avval tarmoqni to'xtating", 'warn')
    const f = document.createElement('input')
    f.type = 'file'
    f.accept = '.json,application/json'
    f.onchange = async () => {
      const file = f.files?.[0]
      if (!file) return
      try {
        const p = JSON.parse(await file.text())
        if (!p.devices) throw new Error('NetLab loyihasi emas')
        loadProject(p, true)
        setTimeout(canvasApi.fit, 50)
      } catch (e: any) {
        toast('Import: ' + e.message, 'error')
      }
    }
    f.click()
    setProjectMenuOpen(false)
  }

  const caps: [string, string][] = [
    ['mininet', 'Mininet'],
    ['ovs', 'OVS'],
    ['frr', 'FRR'],
    ['p4', 'BMv2'],
    ['p4c', 'p4c'],
    ['sdn', 'os-ken'],
    ['tcpdump', 'tcpdump'],
  ]

  return (
    <header className="glass flex items-center justify-between gap-3 rounded-xl px-3.5 py-1.5 shadow-md">
      {/* ─── Chap Qism: Brend, Loyiha Nomi va Loyiha Menyusi ─── */}
      <div className="flex items-center gap-2.5">
        <div className="flex items-center gap-2 font-bold tracking-tight text-foreground select-none">
          <div className="grid h-7 w-7 place-items-center rounded-lg bg-primary text-primary-foreground shadow-sm">
            <Radio className="h-4 w-4" />
          </div>
          <span className="text-[15px]">NetLab</span>
        </div>

        <div className="flex items-center gap-1.5">
          <span
            className={cn('h-2 w-2 rounded-full bg-warning transition-opacity', !dirty && 'opacity-0')}
            title="Saqlanmagan o'zgarishlar"
          />
          <Input
            aria-label="Loyiha nomi"
            className="h-7 w-36 px-2 text-xs font-medium bg-background/50 border-border/60 hover:border-foreground/30 focus:border-foreground"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onBlur={() => {
              const v = name.trim().replace(/[^A-Za-z0-9_-]/g, '_').slice(0, 40) || 'untitled'
              setName(v)
              if (v !== project.name) mutate((p) => { p.name = v })
            }}
          />
        </div>

        {/* Loyiha menyusi (Dropdown) */}
        <div className="relative" ref={menuRef}>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 gap-1 px-2 text-xs font-medium text-muted-foreground hover:text-foreground"
            onClick={() => setProjectMenuOpen(!projectMenuOpen)}
          >
            Loyiha <ChevronDown className="h-3 w-3 opacity-60" />
          </Button>

          {projectMenuOpen && (
            <div className="glass absolute left-0 top-full mt-1.5 z-50 flex w-48 flex-col gap-0.5 rounded-lg border bg-popover/95 p-1 text-popover-foreground shadow-xl backdrop-blur-md animate-in fade-in-50 zoom-in-95">
              <button
                className="flex items-center gap-2 rounded-md px-2.5 py-1.5 text-xs text-left hover:bg-accent hover:text-foreground transition-colors"
                onClick={() => {
                  setProjectMenuOpen(false)
                  if (running()) return toast("Avval tarmoqni to'xtating", 'warn')
                  if (confirmDirty()) loadProject(emptyProject())
                }}
              >
                <FilePlus2 className="h-3.5 w-3.5 text-muted-foreground" /> Yangi loyiha
              </button>
              <button
                className="flex items-center gap-2 rounded-md px-2.5 py-1.5 text-xs text-left hover:bg-accent hover:text-foreground transition-colors"
                onClick={() => {
                  setProjectMenuOpen(false)
                  if (running()) return toast("Avval tarmoqni to'xtating", 'warn')
                  setOpenDlg(true)
                }}
              >
                <FolderOpen className="h-3.5 w-3.5 text-muted-foreground" /> Loyihani ochish…
              </button>
              <button
                className="flex items-center gap-2 rounded-md px-2.5 py-1.5 text-xs text-left hover:bg-accent hover:text-foreground transition-colors"
                onClick={() => {
                  setProjectMenuOpen(false)
                  saveProject().then(() => toast('Loyiha saqlandi', 'ok')).catch((e) => toast(e.message, 'error'))
                }}
              >
                <Save className="h-3.5 w-3.5 text-muted-foreground" /> Saqlash <span className="kbd ml-auto">Ctrl S</span>
              </button>
              <div className="my-1 h-px bg-border/60" />
              <button
                className="flex items-center gap-2 rounded-md px-2.5 py-1.5 text-xs text-left hover:bg-accent hover:text-foreground transition-colors"
                onClick={exportJson}
              >
                <Download className="h-3.5 w-3.5 text-muted-foreground" /> Eksport (JSON)
              </button>
              <button
                className="flex items-center gap-2 rounded-md px-2.5 py-1.5 text-xs text-left hover:bg-accent hover:text-foreground transition-colors"
                onClick={importJson}
              >
                <Upload className="h-3.5 w-3.5 text-muted-foreground" /> Import (JSON)
              </button>
            </div>
          )}
        </div>

        {/* Undo / Redo */}
        <div className="flex items-center">
          <Tip content="Bekor qilish (Ctrl+Z)">
            <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground" disabled={!canUndo} onClick={undo}>
              <Undo2 className="h-3.5 w-3.5" />
            </Button>
          </Tip>
          <Tip content="Qaytarish (Ctrl+Shift+Z)">
            <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground" disabled={!canRedo} onClick={redo}>
              <Redo2 className="h-3.5 w-3.5" />
            </Button>
          </Tip>
        </div>
      </div>

      {/* ─── O'rta Qism: Asosiy Ishchi Fazolar (Workspace Switcher) ─── */}
      <div className="flex items-center rounded-lg bg-muted/60 p-0.5 border border-border/40 shadow-inner">
        <button
          onClick={() => setActiveTab('canvas')}
          className={cn(
            'flex items-center gap-1.5 rounded-md px-3 py-1 text-xs font-medium transition-all',
            activeTab === 'canvas' ? 'bg-background text-foreground shadow-sm font-semibold' : 'text-muted-foreground hover:text-foreground'
          )}
        >
          <Network className="h-3.5 w-3.5 text-sky-400" />
          <span>Topologiya</span>
        </button>

        <button
          onClick={() => setActiveTab('telemetry')}
          className={cn(
            'flex items-center gap-1.5 rounded-md px-3 py-1 text-xs font-medium transition-all',
            activeTab === 'telemetry' ? 'bg-background text-foreground shadow-sm font-semibold' : 'text-muted-foreground hover:text-foreground'
          )}
        >
          <Activity className="h-3.5 w-3.5 text-emerald-400" />
          <span>L4 Telemetriya</span>
        </button>

        <button
          onClick={() => setActiveTab('security')}
          className={cn(
            'flex items-center gap-1.5 rounded-md px-3 py-1 text-xs font-medium transition-all',
            activeTab === 'security' ? 'bg-background text-foreground shadow-sm font-semibold' : 'text-muted-foreground hover:text-foreground'
          )}
        >
          <Shield className="h-3.5 w-3.5 text-rose-400" />
          <span>Xavfsizlik Studio</span>
        </button>

        <button
          onClick={() => setActiveTab('results')}
          className={cn(
            'flex items-center gap-1.5 rounded-md px-3 py-1 text-xs font-medium transition-all',
            activeTab === 'results' ? 'bg-background text-foreground shadow-sm font-semibold' : 'text-muted-foreground hover:text-foreground'
          )}
        >
          <BarChart3 className="h-3.5 w-3.5 text-amber-400" />
          <span>Tahlil & Natijalar</span>
        </button>
      </div>

      {/* ─── O'ng Qism: Simulyatsiya Boshqaruvi va Tizim Holati ─── */}
      <div className="flex items-center gap-2">
        {busy && <span className="text-[11px] text-muted-foreground animate-pulse mr-1">{busy}</span>}

        {/* Tizim Holati Pill (Hover/Click orqali qobiliyatlarni ko'rsatish) */}
        <div className="relative">
          <button
            onClick={() => setCapsOpen(!capsOpen)}
            className="flex items-center gap-1.5 rounded-full border border-border/70 bg-card/60 px-2.5 py-1 text-[11px] font-medium text-muted-foreground hover:border-foreground/30 hover:text-foreground transition-all"
            title="Tizim qobiliyatlari va drayver ma'lumoti"
          >
            <span
              className={cn(
                'h-2 w-2 rounded-full',
                wsOk ? (info?.driver === 'real' ? 'bg-emerald-500 shadow-[0_0_6px_#10b981]' : 'bg-sky-400') : 'bg-destructive'
              )}
            />
            <span>{info?.driver === 'real' ? 'Mininet' : 'Demo'}</span>
          </button>

          {capsOpen && (
            <div
              className="glass absolute right-0 top-full mt-1.5 z-50 flex w-56 flex-col gap-1.5 rounded-lg border bg-popover/95 p-3 text-popover-foreground shadow-xl backdrop-blur-md text-xs animate-in fade-in-50"
              onMouseLeave={() => setCapsOpen(false)}
            >
              <div className="flex items-center justify-between font-semibold border-b pb-1 text-foreground">
                <span>Drayver: {info?.driver || 'demo'}</span>
                <Badge variant={wsOk ? 'success' : 'destructive'} className="text-[9.5px]">
                  {wsOk ? 'Online' : 'Offline'}
                </Badge>
              </div>
              <div className="text-[11px] text-muted-foreground">Tizim qobiliyatlari:</div>
              <div className="flex flex-wrap gap-1">
                {caps.map(([k, l]) => (
                  <Badge key={k} variant={info?.capabilities?.[k] ? 'success' : 'outline'} className="text-[9.5px]">
                    {l}
                  </Badge>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Paket yozish (faqat tarmoq ishlaganda) */}
        {status.running && (
          <Button
            variant="outline"
            size="xs"
            className="h-7 text-[11px]"
            onClick={async () => {
              try {
                useStore.setState({ status: await api.post('/api/net/capture', { on: !status.capture }) })
              } catch (e: any) {
                toast(e.message, 'error')
              }
            }}
          >
            Paketlar: {status.capture === false ? 'off' : 'on'}
          </Button>
        )}

        {/* Asosiy Simulyatsiya Tugmasi */}
        <Button
          size="sm"
          variant={status.running ? 'destructive' : 'default'}
          disabled={!!busy}
          onClick={() => (status.running ? stopNet() : startNet())}
          className={cn(
            'h-7 px-3 text-xs font-semibold shadow-sm transition-all',
            !status.running && 'bg-emerald-600 hover:bg-emerald-500 text-white'
          )}
        >
          {status.running ? (
            <>
              <Square className="mr-1.5 h-3.5 w-3.5 fill-current" /> To'xtatish
            </>
          ) : (
            <>
              <Play className="mr-1.5 h-3.5 w-3.5 fill-current" /> Ishga tushirish
            </>
          )}
        </Button>

        {/* Qidiruv (Ctrl+K) */}
        <Tip content="Tezkor buyruqlar (Ctrl+K)">
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-muted-foreground hover:text-foreground"
            onClick={() => paletteState.open(true)}
          >
            <Search className="h-3.5 w-3.5" />
          </Button>
        </Tip>

        {/* Mavzu (Kun / Tun) */}
        <Tip content={dark ? 'Kunduzgi mavzu' : 'Tungi mavzu'}>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-muted-foreground hover:text-foreground"
            onClick={toggleTheme}
          >
            {dark ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}
          </Button>
        </Tip>
      </div>

      <OpenProjectDialog open={openDlg} onClose={() => setOpenDlg(false)} confirmDirty={confirmDirty} />
    </header>
  )
}

function OpenProjectDialog({ open, onClose, confirmDirty }: { open: boolean; onClose: () => void; confirmDirty: () => boolean }) {
  const [list, setList] = useState<{ projects: { name: string; mtime: number }[]; examples: { name: string }[] } | null>(null)
  useEffect(() => {
    if (open) api.get('/api/projects').then(setList).catch((e) => toast(e.message, 'error'))
  }, [open])

  const pick = async (n: string, ex: boolean) => {
    if (!confirmDirty()) return
    try {
      const p = await api.get<Project>(`/api/projects/${encodeURIComponent(n)}${ex ? '?example=true' : ''}`)
      loadProject(p, ex)
      onClose()
      setTimeout(canvasApi.fit, 60)
    } catch (e: any) {
      toast(e.message, 'error')
    }
  }

  const Item = ({ n, sub, ex }: { n: string; sub: string; ex: boolean }) => (
    <button
      onClick={() => pick(n, ex)}
      className="flex items-center justify-between rounded-md border px-3 py-2 text-left text-sm transition-colors hover:border-foreground/40 hover:bg-accent"
    >
      <span className="font-medium">{n}</span>
      <span className="text-xs text-muted-foreground">{sub}</span>
    </button>
  )

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()} title="Loyihani ochish">
      <div className="flex max-h-[60vh] flex-col gap-1.5 overflow-auto p-1">
        {!!list?.examples.length && (
          <div className="px-1 text-[10.5px] font-semibold uppercase tracking-wider text-muted-foreground">
            Namunalar (Sanoat ssenariylari)
          </div>
        )}
        {list?.examples.map((p) => (
          <Item key={p.name} n={p.name} sub="namuna" ex />
        ))}
        <div className="mt-3 px-1 text-[10.5px] font-semibold uppercase tracking-wider text-muted-foreground">
          Mening loyihalarim
        </div>
        {!list?.projects.length && <div className="px-1 text-xs text-muted-foreground">hali saqlanmagan loyihalar yo'q</div>}
        {list?.projects.map((p) => (
          <Item key={p.name} n={p.name} sub={new Date(p.mtime * 1000).toLocaleString()} ex={false} />
        ))}
      </div>
    </Dialog>
  )
}
