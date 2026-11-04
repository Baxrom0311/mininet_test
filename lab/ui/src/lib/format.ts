export const fmtBps = (b?: number | null) => {
  if (!b) return '0'
  if (b >= 1e9) return (b / 1e9).toFixed(2) + ' Gb/s'
  if (b >= 1e6) return (b / 1e6).toFixed(2) + ' Mb/s'
  if (b >= 1e3) return (b / 1e3).toFixed(1) + ' kb/s'
  return Math.round(b) + ' b/s'
}
export const fmtMs = (v?: number | null) => v == null ? '—' : v >= 1000 ? (v / 1000).toFixed(2) + ' s' : v.toFixed(v < 10 ? 2 : 1) + ' ms'
export const fmtPct = (v?: number | null) => v == null ? '—' : v.toFixed(v < 1 ? 2 : 1) + '%'
export const clock = (ts?: number) => (ts ? new Date(ts * 1000) : new Date()).toTimeString().slice(0, 8)
export const engineName = (e?: string) => ({ analytic: 'analitik', ns3: 'ns-3', mininet: 'mininet' } as Record<string, string>)[e || ''] || e || ''

export const storage = {
  get<T>(k: string, d: T): T { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v) } catch { return d } },
  set(k: string, v: unknown) { try { localStorage.setItem(k, JSON.stringify(v)) } catch { /* private rejim */ } },
}

export const PROTO_COLORS: Record<string, string> = {
  ICMP: '#38bdf8', ARP: '#f59e0b', TCP: '#a78bfa', UDP: '#34d399', DNS: '#5eead4',
  OSPF: '#f472b6', BGP: '#fb923c', RIP: '#facc15', 'IS-IS': '#c084fc', EIGRP: '#f9a8d4',
  STP: '#94a3b8', LLDP: '#94a3b8', IPv6: '#93c5fd', other: '#cbd5e1',
}
export const seriesColor = (i: number) => getComputedStyle(document.documentElement).getPropertyValue(`--s${(i % 6) + 1}`).trim() || '#3987e5'
