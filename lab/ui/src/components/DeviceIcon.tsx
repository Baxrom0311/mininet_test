import type { DeviceType } from '@/lib/types'

/** O'ziga xos qurilma ikonkalari (48x48, chiziqli, currentColor). */
export const ICON_PATHS: Record<DeviceType, string> = {
  host: '<rect x="7" y="9" width="34" height="23" rx="3.5"/><path d="M18 40h12M24 32v8"/><path d="M12 14h24" opacity=".45"/>',
  server: '<rect x="12" y="5" width="24" height="38" rx="3.5"/><path d="M17 13h14M17 20h14M17 27h14"/><circle cx="19" cy="36" r="1.6" fill="currentColor"/><circle cx="25" cy="36" r="1.6" fill="currentColor"/>',
  switch: '<rect x="4" y="14" width="40" height="20" rx="5"/><path d="M12 21h18l-3-3M36 27H18l3 3"/><path d="M9 30h2M14 30h2" opacity=".5"/>',
  p4switch: '<rect x="12" y="12" width="24" height="24" rx="3"/><path d="M17 12V6M24 12V6M31 12V6M17 42v-6M24 42v-6M31 42v-6M12 17H6M12 24H6M12 31H6M42 17h-6M42 24h-6M42 31h-6"/><text x="24" y="28.5" text-anchor="middle" font-size="11" font-weight="800" fill="currentColor" stroke="none" font-family="system-ui">P4</text>',
  router: '<circle cx="24" cy="24" r="17"/><path d="M24 12v9m0 0-3-3m3 3 3-3M24 36v-9m0 0-3 3m3-3 3 3M12 24h9m0 0-3-3m3 3-3 3M36 24h-9m0 0 3-3m-3 3 3 3"/>',
  sdnrouter: '<path d="M24 5 40 14v20L24 43 8 34V14z"/><path d="M16 24h16m0 0-4-4m4 4-4 4M24 16v4M24 28v4"/><circle cx="24" cy="24" r="2" fill="currentColor"/>',
  wifiap: '<path d="M24 28v14M17 42h14"/><circle cx="24" cy="25" r="3" fill="currentColor"/><path d="M16.5 17.5a10.6 10.6 0 0 1 15 0M11.5 12.5a17.7 17.7 0 0 1 25 0M19.8 21.8a5 5 0 0 1 8.4 0"/>',
}

export function DeviceIcon({ type, size = 28, className }: { type: DeviceType; size?: number; className?: string }) {
  return (
    <svg viewBox="0 0 48 48" width={size} height={size} fill="none" stroke="currentColor" strokeWidth={2.4}
      strokeLinecap="round" strokeLinejoin="round" className={className} dangerouslySetInnerHTML={{ __html: ICON_PATHS[type] || ICON_PATHS.host }} />
  )
}
