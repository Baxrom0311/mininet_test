import { useEffect, useState } from 'react'
import { Toaster, toast } from '@/components/ui/toast'
import { TooltipProvider } from '@/components/ui/tooltip'
import { api } from '@/lib/api'
import { connectEvents } from '@/lib/bus'
import { storage } from '@/lib/format'
import { emptyProject, loadProject, openWin, removeDevice, removeLink, saveProject, setMode, useStore } from '@/lib/store'
import type { Info, Project } from '@/lib/types'
import { Canvas, canvasApi } from '@/features/Canvas'
import { P4Window, SdnWindow } from '@/features/CodeWindows'
import { DeviceWindow } from '@/features/DeviceWindow'
import { Dock, logLocal } from '@/features/Dock'
import { Inspector } from '@/features/Inspector'
import { ResultsWindow } from '@/features/ResultsWindow'
import { ScenarioWindow } from '@/features/ScenarioWindow'
import { Sidebar } from '@/features/Sidebar'
import { Topbar } from '@/features/Topbar'
import { cn } from '@/lib/utils'

function Windows() {
  const wins = useStore((s) => s.wins)
  return (
    <>
      {wins.map((w) => w.kind === 'device' ? <DeviceWindow key={w.id} win={w} />
        : w.kind === 'p4' ? <P4Window key={w.id} win={w} />
          : w.kind === 'sdn' ? <SdnWindow key={w.id} win={w} />
            : w.kind === 'scenario' ? <ScenarioWindow key={w.id} win={w} />
              : <ResultsWindow key={w.id} win={w} />)}
    </>
  )
}

function useKeys() {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const st = useStore.getState()
      if (e.key === 'Escape' && st.mode !== 'select') { setMode('select'); return }
      const t = e.target as Element
      if (t.closest?.('input,textarea,select,[role=combobox],.xterm,[data-win],[role=dialog]')) return
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); saveProject().catch((er) => toast(er.message, 'error')); return }
      if (e.ctrlKey || e.metaKey || e.altKey) return
      const k = e.key.toLowerCase()
      if (k === 'v') setMode('select')
      else if (k === 'c') setMode('link')
      else if (k === 'd') setMode('delete')
      else if (k === 'f') canvasApi.fit()
      else if ((k === 'delete' || k === 'backspace') && st.selection) { e.preventDefault(); st.selection.kind === 'device' ? removeDevice(st.selection.id) : removeLink(st.selection.id) }
      else if (k === 'enter' && st.selection?.kind === 'device') openWin('device', st.selection.id)
    }
    const onUnload = (e: BeforeUnloadEvent) => { if (useStore.getState().dirty) { e.preventDefault(); e.returnValue = '' } }
    addEventListener('keydown', onKey); addEventListener('beforeunload', onUnload)
    return () => { removeEventListener('keydown', onKey); removeEventListener('beforeunload', onUnload) }
  }, [])
}

export default function App() {
  const [ready, setReady] = useState<'loading' | 'ok' | string>('loading')
  const [dockCollapsed, setDockCollapsed] = useState(false)
  useKeys()
  useEffect(() => {
    (async () => {
      try {
        const [info, templates] = await Promise.all([api.get<Info>('/api/info'), api.get('/api/templates')])
        useStore.setState({ info, templates, status: info.status })
        let live: Project | null = null
        if (info.status.running) { try { live = (await api.get('/api/net/project')).project } catch { /* */ } }
        const draft = storage.get<Project | null>('netlab.draft', null)
        if (live) loadProject(live, true)
        else loadProject(draft?.devices ? draft : emptyProject(), !!draft)
        connectEvents()
        setReady('ok')
        if (!Object.keys(useStore.getState().project.devices).length) logLocal('info', 'Boshlash: "Ochish" → namuna yoki chapdan qurilma tanlang.')
      } catch (e: any) { setReady("Backend bilan aloqa yo'q: " + e.message) }
    })()
  }, [])
  if (ready !== 'ok') return <div className="grid h-full place-items-center text-sm text-muted-foreground">{ready === 'loading' ? 'Yuklanmoqda…' : ready}</div>
  return (
    <TooltipProvider>
      <div className={cn('grid h-full gap-2.5 p-2.5', dockCollapsed ? 'grid-rows-[52px_1fr_40px]' : 'grid-rows-[52px_1fr_200px]')}
        style={{ gridTemplateColumns: '232px 1fr 320px', gridTemplateAreas: '"top top top" "left stage right" "left dock right"' }}>
        <div style={{ gridArea: 'top' }} className="flex"><div className="flex-1"><Topbar /></div></div>
        <div style={{ gridArea: 'left' }} className="flex min-h-0 flex-col"><Sidebar /></div>
        <main style={{ gridArea: 'stage' }} className="glass min-h-0 overflow-hidden rounded-xl"><Canvas /></main>
        <div style={{ gridArea: 'right' }} className="flex min-h-0 flex-col"><Inspector /></div>
        <div style={{ gridArea: 'dock' }} className="flex min-h-0 flex-col"><Dock collapsed={dockCollapsed} onToggle={() => setDockCollapsed(!dockCollapsed)} /></div>
      </div>
      <Windows />
      <Toaster />
    </TooltipProvider>
  )
}
