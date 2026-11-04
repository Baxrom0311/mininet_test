import * as React from 'react'
import * as DialogPrimitive from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import { cn } from '@/lib/utils'

export function Dialog({ open, onOpenChange, title, description, children, footer, className }: {
  open: boolean; onOpenChange: (o: boolean) => void; title: React.ReactNode; description?: React.ReactNode
  children?: React.ReactNode; footer?: React.ReactNode; className?: string
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-[200] bg-black/50 backdrop-blur-[2px] data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <DialogPrimitive.Content className={cn(
          'glass fixed left-1/2 top-1/2 z-[201] grid w-[min(520px,92vw)] max-h-[86vh] -translate-x-1/2 -translate-y-1/2 gap-4 overflow-auto rounded-xl p-5 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95',
          className)}>
          <div className="flex flex-col gap-1">
            <DialogPrimitive.Title className="text-base font-semibold">{title}</DialogPrimitive.Title>
            {description && <DialogPrimitive.Description className="text-xs text-muted-foreground">{description}</DialogPrimitive.Description>}
          </div>
          {children}
          {footer && <div className="flex justify-end gap-2">{footer}</div>}
          <DialogPrimitive.Close className="absolute right-3 top-3 rounded-sm opacity-60 transition-opacity hover:opacity-100"><X className="h-4 w-4" /></DialogPrimitive.Close>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
