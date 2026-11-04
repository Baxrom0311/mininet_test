import { useRef } from 'react'
import { X } from 'lucide-react'
import { closeWin, focusWin, updateWin, useStore, type Win } from '@/lib/store'
import { cn } from '@/lib/utils'

/** Suzuvchi oyna: sudraladi (sarlavha), o'lchami o'zgaradi (o'ng-past burchak), bosilganda oldinga chiqadi. */
export function WindowFrame({ win, icon, title, sub, children, onClose }: {
  win: Win; icon?: React.ReactNode; title: React.ReactNode; sub?: React.ReactNode; children: React.ReactNode; onClose?: () => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const focused = useStore((s) => s.zTop === win.z)
  const startDrag = (ev: React.PointerEvent) => {
    if ((ev.target as Element).closest('button')) return
    const sx = ev.clientX - win.x, sy = ev.clientY - win.y
    const mv = (e: PointerEvent) => updateWin(win.id, { x: Math.max(0, e.clientX - sx), y: Math.max(0, e.clientY - sy) })
    const up = () => { removeEventListener('pointermove', mv); removeEventListener('pointerup', up) }
    addEventListener('pointermove', mv); addEventListener('pointerup', up)
  }
  const startResize = (ev: React.PointerEvent) => {
    ev.stopPropagation()
    const sx = ev.clientX, sy = ev.clientY, w0 = win.w, h0 = win.h
    const mv = (e: PointerEvent) => updateWin(win.id, { w: Math.max(420, w0 + e.clientX - sx), h: Math.max(280, h0 + e.clientY - sy) })
    const up = () => { removeEventListener('pointermove', mv); removeEventListener('pointerup', up) }
    addEventListener('pointermove', mv); addEventListener('pointerup', up)
  }
  return (
    <div ref={ref} onPointerDown={() => focusWin(win.id)} data-win={win.id}
      className={cn('glass fixed flex flex-col overflow-hidden rounded-xl', focused && 'border-foreground/25 shadow-2xl')}
      style={{ left: win.x, top: win.y, width: win.w, height: win.h, zIndex: 50 + win.z }}>
      <div onPointerDown={startDrag} className="flex cursor-move select-none items-center gap-2.5 border-b px-3 py-2">
        {icon && <span className="text-foreground [&_svg]:size-5">{icon}</span>}
        <div className="min-w-0"><div className="truncate text-sm font-semibold">{title}</div>{sub && <div className="truncate text-[11px] text-muted-foreground">{sub}</div>}</div>
        <button onClick={() => { onClose?.(); closeWin(win.id) }} className="ml-auto rounded-md p-1 text-muted-foreground hover:bg-destructive/15 hover:text-destructive" title="Yopish"><X className="h-4 w-4" /></button>
      </div>
      <div className="flex min-h-0 flex-1 flex-col">{children}</div>
      <div onPointerDown={startResize} className="absolute bottom-0 right-0 h-4 w-4 cursor-nwse-resize" />
    </div>
  )
}

/** Bo'limlar: bir marta ochilgan bo'lim saqlanadi (terminal uzilmasin), faqat ko'rinmaydi. */
export function KeepAliveTabs({ tabs, active, onChange }: {
  tabs: { id: string; label: React.ReactNode; flush?: boolean; render: () => React.ReactNode }[]; active: string; onChange: (id: string) => void
}) {
  const visited = useRef(new Set<string>())
  visited.current.add(active)
  return (
    <>
      <div className="flex gap-0.5 border-b px-2 pt-1.5">
        {tabs.map((t) => (
          <button key={t.id} onClick={() => onChange(t.id)}
            className={cn('-mb-px rounded-t-md border-b-2 border-transparent px-3 py-1.5 text-xs font-medium text-muted-foreground hover:text-foreground',
              active === t.id && 'border-foreground text-foreground')}>{t.label}</button>
        ))}
      </div>
      {tabs.filter((t) => visited.current.has(t.id)).map((t) => (
        <div key={t.id} className={cn('min-h-0 flex-1 flex-col overflow-auto', t.flush ? '' : 'gap-3 p-3', active === t.id ? 'flex' : 'hidden')}>{t.render()}</div>
      ))}
    </>
  )
}
