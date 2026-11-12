import { useEffect, useState } from 'react'
import { Activity, Download, FilePlus2, FolderOpen, Moon, Play, Radio, Redo2, Save, Search, Shield, Square, Sun, Undo2, Upload } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Tip } from '@/components/ui/tooltip'
import { toast } from '@/components/ui/toast'
import { api } from '@/lib/api'
import { closeWin, emptyProject, loadProject, mutate, openWin, redo, running, saveProject, startNet, stopNet, toggleTheme, undo, useStore } from '@/lib/store'
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
  const canUndo = useStore((s) => s.canUndo), canRedo = useStore((s) => s.canRedo)
  const [openDlg, setOpenDlg] = useState(false)
  const [name, setName] = useState(project.name)
  useEffect(() => setName(project.name), [project.name])
  const confirmDirty = () => !useStore.getState().dirty || confirm("Saqlanmagan o'zgarishlar bor. Davom etilsinmi?")

  const exportJson = () => {
    const a = document.createElement('a')
    a.href = URL.createObjectURL(new Blob([JSON.stringify(project, null, 2)], { type: 'application/json' }))
    a.download = (project.name || 'netlab') + '.json'; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000)
  }
  const importJson = () => {
    if (running()) return toast("Avval tarmoqni to'xtating", 'warn')
    const f = document.createElement('input'); f.type = 'file'; f.accept = '.json,application/json'
    f.onchange = async () => {
      const file = f.files?.[0]; if (!file) return
      try { const p = JSON.parse(await file.text()); if (!p.devices) throw new Error('NetLab loyihasi emas'); loadProject(p, true); setTimeout(canvasApi.fit, 50) }
      catch (e: any) { toast('Import: ' + e.message, 'error') }
    }
    f.click()
  }
  const caps: [string, string][] = [['mininet', 'Mininet'], ['ovs', 'OVS'], ['frr', 'FRR'], ['p4', 'BMv2'], ['p4c', 'p4c'], ['sdn', 'os-ken'], ['tcpdump', 'tcpdump']]

  return (
    <header className="glass flex items-center gap-2 rounded-xl px-3">
      <div className="mr-1 flex items-center gap-2 font-semibold tracking-tight">
        <div className="grid h-7 w-7 place-items-center rounded-md bg-primary text-primary-foreground"><Radio className="h-4 w-4" /></div>
        NetLab
      </div>
      <div className="flex items-center gap-1.5">
        <span className={cn('h-2 w-2 rounded-full bg-warning', !dirty && 'invisible')} title="Saqlanmagan o'zgarishlar" />
        <Input aria-label="Loyiha nomi" className="h-8 w-44 font-medium" value={name} onChange={(e) => setName(e.target.value)}
          onBlur={() => { const v = name.trim().replace(/[^A-Za-z0-9_-]/g, '_').slice(0, 40) || 'untitled'; setName(v); if (v !== project.name) mutate((p) => { p.name = v }) }} />
      </div>
      <Button variant="ghost" size="sm" onClick={() => { if (running()) return toast("Avval tarmoqni to'xtating", 'warn'); if (confirmDirty()) { loadProject(emptyProject()); } }}><FilePlus2 /> Yangi</Button>
      <Button variant="ghost" size="sm" onClick={() => running() ? toast("Avval tarmoqni to'xtating", 'warn') : setOpenDlg(true)}><FolderOpen /> Ochish</Button>
      <Button variant="ghost" size="sm" onClick={() => saveProject().catch((e) => toast(e.message, 'error'))}><Save /> Saqlash</Button>
      <Button variant="ghost" size="sm" onClick={exportJson}><Download /> Eksport</Button>
      <Button variant="ghost" size="sm" onClick={importJson}><Upload /> Import</Button>
      <div className="mx-1 h-5 w-px bg-border" />
      <Tip content="Bekor qilish (Ctrl+Z)"><Button variant="ghost" size="icon" disabled={!canUndo} onClick={undo}><Undo2 /></Button></Tip>
      <Tip content="Qaytarish (Ctrl+Shift+Z)"><Button variant="ghost" size="icon" disabled={!canRedo} onClick={redo}><Redo2 /></Button></Tip>
      <Button variant="outline" size="sm" className="ml-1 w-44 justify-start text-muted-foreground" onClick={() => paletteState.open(true)}>
        <Search /> Qidirish… <span className="kbd ml-auto">Ctrl K</span>
      </Button>
      <div className="flex-1" />
      {busy && <span className="text-xs text-muted-foreground">{busy}</span>}
      <div className="hidden max-w-[360px] flex-wrap gap-1 xl:flex">
        {info?.driver === 'demo' ? <Badge variant="outline" title="Mininet'siz soddalashtirilgan simulyatsiya">demo rejim</Badge>
          : caps.map(([k, l]) => <Badge key={k} variant={info?.capabilities[k] ? 'success' : 'outline'}>{l}</Badge>)}
      </div>
      <Badge variant={info?.driver === 'real' ? 'success' : 'warning'}>driver: {info?.driver}</Badge>
      <Tip content={wsOk ? 'Backend bilan aloqa bor' : "Backend bilan aloqa yo'q"}><span className={cn('h-2 w-2 rounded-full', wsOk ? 'bg-success' : 'bg-destructive')} /></Tip>
      {status.running && (
        <Button variant="outline" size="sm" onClick={async () => {
          try { useStore.setState({ status: await api.post('/api/net/capture', { on: !status.capture }) }) } catch (e: any) { toast(e.message, 'error') }
        }}>Paketlar: {status.capture === false ? 'off' : 'on'}</Button>
      )}
      <Button size="sm" variant={status.running ? 'destructive' : 'default'} disabled={!!busy} onClick={() => (status.running ? stopNet() : startNet())}>
        {status.running ? <><Square /> To'xtatish</> : <><Play /> Ishga tushirish</>}
      </Button>
      <Button variant="outline" size="sm" onClick={() => openWin('telemetry', 'l4', { w: 920, h: 600 })}>
        <Activity className="h-4 w-4 text-primary" /> L4 Telemetriya
      </Button>
      <Button
        variant="outline"
        size="sm"
        className={cn(
          useStore((s) => s.wins.some((w) => w.kind === 'security')) && 'bg-primary/15 border-primary text-primary font-medium'
        )}
        onClick={() => {
          const isOpen = useStore.getState().wins.some((w) => w.kind === 'security')
          if (isOpen) {
            closeWin('security:studio')
          } else {
            openWin('security', 'studio', { w: 1060, h: 680 })
          }
        }}
        title="Security Studio (Conntrack, Stateful Netfilter va Hujumlar Simulyatsiyasi)"
      >
        <Shield className="h-4 w-4 text-primary" /> Security Studio
      </Button>
      <Button variant="ghost" size="icon" onClick={toggleTheme} title="Kun / tun">{dark ? <Sun /> : <Moon />}</Button>
      <OpenProjectDialog open={openDlg} onClose={() => setOpenDlg(false)} confirmDirty={confirmDirty} />
    </header>
  )
}

function OpenProjectDialog({ open, onClose, confirmDirty }: { open: boolean; onClose: () => void; confirmDirty: () => boolean }) {
  const [list, setList] = useState<{ projects: { name: string; mtime: number }[]; examples: { name: string }[] } | null>(null)
  useEffect(() => { if (open) api.get('/api/projects').then(setList).catch((e) => toast(e.message, 'error')) }, [open])
  const pick = async (n: string, ex: boolean) => {
    if (!confirmDirty()) return
    try { const p = await api.get<Project>(`/api/projects/${encodeURIComponent(n)}${ex ? '?example=true' : ''}`); loadProject(p, ex); onClose(); setTimeout(canvasApi.fit, 60) }
    catch (e: any) { toast(e.message, 'error') }
  }
  const Item = ({ n, sub, ex }: { n: string; sub: string; ex: boolean }) => (
    <button onClick={() => pick(n, ex)} className="flex items-center justify-between rounded-md border px-3 py-2 text-left text-sm transition-colors hover:border-foreground/40 hover:bg-accent">
      <span>{n}</span><span className="text-xs text-muted-foreground">{sub}</span>
    </button>
  )
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()} title="Loyihani ochish">
      <div className="flex max-h-[60vh] flex-col gap-1.5 overflow-auto">
        {!!list?.examples.length && <div className="px-1 text-[10.5px] font-semibold uppercase tracking-wider text-muted-foreground">Namunalar</div>}
        {list?.examples.map((p) => <Item key={p.name} n={p.name} sub="namuna" ex />)}
        <div className="mt-2 px-1 text-[10.5px] font-semibold uppercase tracking-wider text-muted-foreground">Mening loyihalarim</div>
        {!list?.projects.length && <div className="px-1 text-xs text-muted-foreground">hali saqlanmagan</div>}
        {list?.projects.map((p) => <Item key={p.name} n={p.name} sub={new Date(p.mtime * 1000).toLocaleString()} ex={false} />)}
      </div>
    </Dialog>
  )
}
