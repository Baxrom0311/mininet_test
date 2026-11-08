import * as React from 'react'
import * as SelectPrimitive from '@radix-ui/react-select'
import { Check, ChevronDown } from 'lucide-react'
import { cn } from '@/lib/utils'

export type Option = { value: string; label: React.ReactNode; disabled?: boolean }

/** Ixcham shadcn Select: options ro'yxati bilan. Bo'sh qiymat uchun "__none__" ishlatiladi (Radix bo'sh qiymatni qabul qilmaydi). */
export function Select({ value, onChange, options, placeholder, className, disabled, ariaLabel }: {
  value: string | undefined; onChange: (v: string) => void; options: Option[]; placeholder?: string; className?: string; disabled?: boolean; ariaLabel?: string
}) {
  const NONE = '__none__'
  const v = value === '' || value == null ? NONE : value
  return (
    <SelectPrimitive.Root value={v} onValueChange={(x) => onChange(x === NONE ? '' : x)} disabled={disabled}>
      <SelectPrimitive.Trigger aria-label={ariaLabel} className={cn(
        'flex h-8 w-full items-center justify-between gap-2 whitespace-nowrap rounded-md border border-input bg-background/50 px-2.5 text-sm shadow-sm focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-50 [&>span]:truncate',
        className)}>
        <SelectPrimitive.Value placeholder={placeholder} />
        <SelectPrimitive.Icon asChild><ChevronDown className="h-4 w-4 opacity-50" /></SelectPrimitive.Icon>
      </SelectPrimitive.Trigger>
      <SelectPrimitive.Portal>
        <SelectPrimitive.Content position="popper" sideOffset={4}
          className="relative z-[300] max-h-80 min-w-[8rem] overflow-hidden rounded-md border bg-popover text-popover-foreground shadow-md data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 w-[var(--radix-select-trigger-width)]">
          <SelectPrimitive.Viewport className="p-1">
            {options.map((o) => (
              <SelectPrimitive.Item key={o.value || NONE} value={o.value === '' ? NONE : o.value} disabled={o.disabled}
                className="relative flex w-full cursor-default select-none items-center rounded-sm py-1.5 pl-2 pr-8 text-sm outline-none focus:bg-accent focus:text-accent-foreground data-[disabled]:pointer-events-none data-[disabled]:opacity-50">
                <SelectPrimitive.ItemText>{o.label}</SelectPrimitive.ItemText>
                <span className="absolute right-2 flex h-3.5 w-3.5 items-center justify-center">
                  <SelectPrimitive.ItemIndicator><Check className="h-4 w-4" /></SelectPrimitive.ItemIndicator>
                </span>
              </SelectPrimitive.Item>
            ))}
          </SelectPrimitive.Viewport>
        </SelectPrimitive.Content>
      </SelectPrimitive.Portal>
    </SelectPrimitive.Root>
  )
}
