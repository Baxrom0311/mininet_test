import { useEffect, useState } from 'react'
import { SlidersHorizontal } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Toaster, toast } from '@/components/ui/toast'
import { TooltipProvider } from '@/components/ui/tooltip'
import { api } from '@/lib/api'
import { connectEvents } from '@/lib/bus'
import { storage } from '@/lib/format'
import {
  emptyProject,
  loadProject,
  openWin,
  redo,
  removeDevice,
  removeLink,
  saveProject,
  setInspectorOpen,
  setMode,
  toggleInspector,
  toggleTheme,
  undo,
  useStore,
} from '@/lib/store'
import type { Info, Project } from '@/lib/types'
import { Canvas, canvasApi } from '@/features/Canvas'
import { CommandPalette, paletteState } from '@/features/CommandPalette'
import { P4Window, SdnWindow } from '@/features/CodeWindows'
import { DeviceWindow } from '@/features/DeviceWindow'
import { Dock, logLocal } from '@/features/Dock'
import { Inspector } from '@/features/Inspector'
import { PDUInspectorWindow } from '@/features/PDUInspector'
import { ResultsWindow } from '@/features/ResultsWindow'
import { ScenarioWindow } from '@/features/ScenarioWindow'
import { SecurityStudio } from '@/features/SecurityStudio'
import { Sidebar } from '@/features/Sidebar'
import { Topbar } from '@/features/Topbar'
import { TransportObservatory } from '@/features/TransportObservatory'
import { cn } from '@/lib/utils'

function Windows() {
  const wins = useStore((s) => s.wins)
  return (
    <>
      {wins.map((w) =>
        w.kind === 'device' ? (
          <DeviceWindow key={w.id} win={w} />
        ) : w.kind === 'p4' ? (
          <P4Window key={w.id} win={w} />
        ) : w.kind === 'sdn' ? (
          <SdnWindow key={w.id} win={w} />
        ) : w.kind === 'scenario' ? (
          <ScenarioWindow key={w.id} win={w} />
        ) : w.kind === 'pdu' ? (
          <PDUInspectorWindow key={w.id} win={w} />
        ) : w.kind === 'telemetry' ? (
          <TransportObservatory key={w.id} win={w} />
        ) : w.kind === 'security' ? (
          <SecurityStudio key={w.id} win={w} />
        ) : (
          <ResultsWindow key={w.id} win={w} />
        )
      )}
    </>
  )
}

function useKeys() {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const st = useStore.getState()
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        paletteState.open(true)
        return
      }
      if (e.key === 'Escape') {
        if (st.selection) {
          useStore.getState().selection && useStore.setState({ selection: null, inspectorOpen: false })
          return
        }
        if (st.mode !== 'select') {
          setMode('select')
          return
        }
      }
      const t = e.target as Element
      if (t.closest?.('input,textarea,select,[role=combobox],.xterm,[data-win],[role=dialog]')) return
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
        e.preventDefault()
        saveProject().catch((er) => toast(er.message, 'error'))
        return
      }
      if ((e.ctrlKey || e.metaKey) && (e.key.toLowerCase() === 'y' || (e.key.toLowerCase() === 'z' && e.shiftKey))) {
        e.preventDefault()
        redo()
        return
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
        e.preventDefault()
        undo()
        return
      }
      if (e.ctrlKey || e.metaKey || e.altKey) return
      const k = e.key.toLowerCase()
      if (k === 'v') setMode('select')
      else if (k === 'c') setMode('link')
      else if (k === 'd') setMode('delete')
      else if (k === 'f') canvasApi.fit()
      else if ((k === 'delete' || k === 'backspace') && st.selection) {
        e.preventDefault()
        st.selection.kind === 'device' ? removeDevice(st.selection.id) : removeLink(st.selection.id)
      } else if (k === 'enter' && st.selection?.kind === 'device') {
        openWin('device', st.selection.id)
      }
    }
    const onUnload = (e: BeforeUnloadEvent) => {
      if (useStore.getState().dirty) {
        e.preventDefault()
        e.returnValue = ''
      }
    }
    addEventListener('keydown', onKey)
    addEventListener('beforeunload', onUnload)
    return () => {
      removeEventListener('keydown', onKey)
      removeEventListener('beforeunload', onUnload)
    }
  }, [])
}

export default function App() {
  const [ready, setReady] = useState<'loading' | 'ok' | string>('loading')
  const [dockCollapsed, setDockCollapsed] = useState(true)
  const activeTab = useStore((s) => s.activeTab)
  const inspectorOpen = useStore((s) => s.inspectorOpen)
  const selection = useStore((s) => s.selection)
  const running = useStore((s) => s.status.running)

  useKeys()

  useEffect(() => {
    ;(async () => {
      try {
        const [info, templates] = await Promise.all([api.get<Info>('/api/info'), api.get('/api/templates')])
        useStore.setState({ info, templates, status: info.status })
        let live: Project | null = null
        if (info.status.running) {
          try {
            live = (await api.get('/api/net/project')).project
          } catch {
            /* */
          }
        }
        const draft = storage.get<Project | null>('netlab.draft', null)
        if (live) loadProject(live, true)
        else loadProject(draft?.devices ? draft : emptyProject(), !!draft)
        connectEvents()
        setReady('ok')
        if (!Object.keys(useStore.getState().project.devices).length) {
          logLocal('info', 'Boshlash: "Loyiha" → Ochish yoki namunani tanlang.')
        }
      } catch (e: any) {
        setReady("Backend bilan aloqa yo'q: " + e.message)
      }
    })()
  }, [])

  // Agar tarmoq ishga tushsa, dockni avtomatik ochish
  useEffect(() => {
    if (running) setDockCollapsed(false)
  }, [running])

  if (ready !== 'ok') {
    return <div className="grid h-full place-items-center text-sm text-muted-foreground">{ready === 'loading' ? 'Yuklanmoqda…' : ready}</div>
  }

  const isInspectorVisible = inspectorOpen || !!selection

  return (
    <TooltipProvider>
      <div className="flex h-full w-full flex-col gap-2.5 p-2.5 overflow-hidden bg-background">
        {/* Yuqori Panel (Header) */}
        <Topbar />

        {/* Asosiy Ishchi Hudud (Workspace Stage) */}
        <div className="flex-1 min-h-0 w-full overflow-hidden">
          {activeTab === 'telemetry' ? (
            <TransportObservatory />
          ) : activeTab === 'security' ? (
            <SecurityStudio />
          ) : activeTab === 'results' ? (
            <ResultsWindow />
          ) : (
            // Topologiya Kanvasi (Maksimal Kenglik va Qulaylik)
            <div className="flex h-full w-full gap-2.5 overflow-hidden">
              {/* Chap Asboblar Paneli (Yig'iluvchi) */}
              <Sidebar />

              {/* Markaziy Kanvas va Pastki Paket/Hodisa Dock */}
              <div className="flex flex-1 min-h-0 min-w-0 flex-col gap-2.5 overflow-hidden">
                <main className="glass relative flex-1 min-h-0 overflow-hidden rounded-xl">
                  <Canvas />

                  {/* Agar Inspektor yopiq bo'lsa, uni ochish uchun o'ng burchakdagi qulay tugma */}
                  {!isInspectorVisible && (
                    <Button
                      variant="outline"
                      size="xs"
                      onClick={toggleInspector}
                      className="absolute right-3 top-3 h-7 gap-1.5 bg-card/80 text-[11px] shadow-sm backdrop-blur"
                      title="Loyiha va sozlamalar inspektori"
                    >
                      <SlidersHorizontal className="h-3.5 w-3.5 text-muted-foreground" />
                      <span>Inspektor</span>
                    </Button>
                  )}
                </main>

                {/* Pastki Paketlar va Hodisalar Konsoli (Dock) */}
                <div className={cn('shrink-0 transition-all duration-200', dockCollapsed ? 'h-9' : 'h-44')}>
                  <Dock collapsed={dockCollapsed} onToggle={() => setDockCollapsed(!dockCollapsed)} />
                </div>
              </div>

              {/* O'ng Inspektor (Faqat element tanlanganda yoki foydalanuvchi ochganda) */}
              {isInspectorVisible && (
                <div className="shrink-0 h-full">
                  <Inspector onClose={() => setInspectorOpen(false)} />
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      <Windows />
      <CommandPalette onToggleTheme={toggleTheme} />
      <Toaster />
    </TooltipProvider>
  )
}
