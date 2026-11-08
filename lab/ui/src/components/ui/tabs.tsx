import * as React from 'react'
import * as TabsPrimitive from '@radix-ui/react-tabs'
import { cn } from '@/lib/utils'

export const Tabs = TabsPrimitive.Root

export const TabsList = React.forwardRef<React.ElementRef<typeof TabsPrimitive.List>, React.ComponentPropsWithoutRef<typeof TabsPrimitive.List>>(
  ({ className, ...props }, ref) => (
    <TabsPrimitive.List ref={ref} className={cn('inline-flex h-8 items-center justify-center rounded-lg bg-muted/70 p-0.5 text-muted-foreground', className)} {...props} />
  ))
TabsList.displayName = 'TabsList'

export const TabsTrigger = React.forwardRef<React.ElementRef<typeof TabsPrimitive.Trigger>, React.ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>>(
  ({ className, ...props }, ref) => (
    <TabsPrimitive.Trigger ref={ref} className={cn(
      'inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md px-2.5 py-1 text-xs font-medium transition-all focus-visible:outline-none disabled:opacity-50 data-[state=active]:bg-background data-[state=active]:text-foreground data-[state=active]:shadow [&_svg]:size-3.5',
      className)} {...props} />
  ))
TabsTrigger.displayName = 'TabsTrigger'

export const TabsContent = React.forwardRef<React.ElementRef<typeof TabsPrimitive.Content>, React.ComponentPropsWithoutRef<typeof TabsPrimitive.Content>>(
  ({ className, ...props }, ref) => (
    <TabsPrimitive.Content ref={ref} className={cn('focus-visible:outline-none', className)} {...props} />
  ))
TabsContent.displayName = 'TabsContent'

/** Oddiy segment boshqaruvi (Tabs uslubida, kontentsiz). */
export function Segmented<T extends string>({ value, onChange, items, className }: {
  value: T; onChange: (v: T) => void; items: { value: T; label: React.ReactNode }[]; className?: string
}) {
  return (
    <div className={cn('inline-flex h-8 items-center rounded-lg bg-muted/70 p-0.5 text-muted-foreground', className)}>
      {items.map((it) => (
        <button key={it.value} aria-pressed={value === it.value} onClick={() => onChange(it.value)}
          className={cn('inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition-all [&_svg]:size-3.5',
            value === it.value && 'bg-background text-foreground shadow')}>
          {it.label}
        </button>
      ))}
    </div>
  )
}
