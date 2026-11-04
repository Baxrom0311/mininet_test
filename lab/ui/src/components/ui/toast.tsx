import { create } from 'zustand'
import { cn } from '@/lib/utils'

type Toast = { id: number; msg: string; kind: 'ok' | 'error' | 'warn' | 'info' }
const useToasts = create<{ items: Toast[] }>(() => ({ items: [] }))
let seq = 0

export function toast(msg: string, kind: Toast['kind'] = 'info') {
  const id = ++seq
  useToasts.setState((s) => ({ items: [...s.items, { id, msg, kind }] }))
  setTimeout(() => useToasts.setState((s) => ({ items: s.items.filter((t) => t.id !== id) })), kind === 'error' ? 7000 : 3500)
}

export function Toaster() {
  const items = useToasts((s) => s.items)
  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-[500] flex flex-col gap-2">
      {items.map((t) => (
        <div key={t.id} className={cn('glass pointer-events-auto max-w-sm whitespace-pre-wrap rounded-lg px-3.5 py-2.5 text-xs animate-in slide-in-from-bottom-2',
          t.kind === 'error' && 'border-destructive/60', t.kind === 'ok' && 'border-success/50', t.kind === 'warn' && 'border-warning/50')}>
          {t.msg}
        </div>
      ))}
    </div>
  )
}
