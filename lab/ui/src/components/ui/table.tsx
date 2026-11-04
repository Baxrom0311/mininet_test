import * as React from 'react'
import { cn } from '@/lib/utils'

export function DataTable({ columns, rows, className, empty = "(bo'sh)" }: {
  columns: React.ReactNode[]; rows: React.ReactNode[][]; className?: string; empty?: string
}) {
  if (!rows.length) return <div className="px-3 py-2 text-xs text-muted-foreground">{empty}</div>
  return (
    <div className={cn('w-full overflow-auto', className)}>
      <table className="w-full caption-bottom font-mono text-[11.5px]">
        <thead>
          <tr className="border-b">{columns.map((c, i) => <th key={i} className="h-7 px-2.5 text-left align-middle font-sans text-[11px] font-medium text-muted-foreground">{c}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-b border-border/50 transition-colors hover:bg-muted/40">
              {r.map((c, j) => <td key={j} className="px-2.5 py-1 align-middle">{c}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function Section({ title, right, children, className }: { title: React.ReactNode; right?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <div className={cn('shrink-0 overflow-hidden rounded-lg border bg-card/40', className)}>
      <div className="flex items-center justify-between gap-2 border-b bg-muted/40 px-3 py-1.5 text-xs font-semibold">{title}{right}</div>
      {children}
    </div>
  )
}
