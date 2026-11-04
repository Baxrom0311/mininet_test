/** Grafiklar: chiziqli (vaqt / parametr, crosshair + tooltip, hodisa belgilari, log x o'qi)
 *  va issiqlik xaritasi (2D tadqiqot; ketma-ket bir rangli shkala, katakda qiymat). */
import { useLayoutEffect, useRef, useState } from 'react'
import { useStore } from '@/lib/store'
import type { ScenarioEvent } from '@/lib/types'

export interface Series { name: string; color: string; points: { x: number; y: number | null | undefined }[] }

function niceMax(v: number) { const p = Math.pow(10, Math.floor(Math.log10(v))); const n = v / p; return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p }
function niceStep(v: number) { if (v <= 1) return 1; const p = Math.pow(10, Math.floor(Math.log10(v))); const n = v / p; return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p }

function useWidth() {
  const ref = useRef<HTMLDivElement>(null)
  const [w, setW] = useState(700)
  useLayoutEffect(() => {
    const el = ref.current!; const ro = new ResizeObserver(() => setW(el.clientWidth || 700)); ro.observe(el); setW(el.clientWidth || 700)
    return () => ro.disconnect()
  }, [])
  return [ref, w] as const
}

export function LineChart({ series: all, fmt, title, events = [], xMax: xMaxOpt, xTicks, xFmt = (x) => x + 's', markers, note }: {
  series: Series[]; fmt: (v: number) => string; title: string; events?: ScenarioEvent[]; xMax?: number
  xTicks?: number[]; xFmt?: (x: number) => string; markers?: boolean; note?: string
}) {
  const [ref, width] = useWidth()
  const [hover, setHover] = useState<number | null>(null)
  const series = all.filter((s) => s.points.length)
  const W = Math.max(width, 360), H = 280, m = { l: 66, r: series.length <= 4 ? 128 : 16, t: 10, b: 28 }
  const xs = series.flatMap((s) => s.points.map((p) => p.x))
  const ys = series.flatMap((s) => s.points.map((p) => p.y)).filter((v): v is number => v != null && isFinite(v))
  const xMax = Math.max(xMaxOpt || 0, ...xs, 1)
  const yMax = (() => { const v = Math.max(...ys, 0); return v <= 0 ? 1 : niceMax(v) })()
  const tv = (xTicks || []).filter(isFinite)
  const logX = tv.length > 2 && Math.min(...tv) > 0 && Math.max(...tv) / Math.min(...tv) >= 20
  const xLo = logX ? Math.log10(Math.min(...tv)) : 0, xHi = logX ? Math.log10(Math.max(...tv)) : xMax
  const X = (x: number) => m.l + ((logX ? Math.log10(x) : x) - xLo) / ((xHi - xLo) || 1) * (W - m.l - m.r)
  const Y = (y: number) => H - m.b - (y / yMax) * (H - m.t - m.b)
  let ticks = tv.length ? [...new Set(tv)].sort((a, b) => a - b) : (() => { const st = niceStep(xMax / 6), o: number[] = []; for (let x = 0; x <= xMax + 1e-9; x += st) o.push(x); return o })()
  let last = -1e9; ticks = ticks.filter((x) => { if (X(x) - last < 26) return false; last = X(x); return true })
  const labels = series.length <= 4 ? series.map((s) => { const lp = [...s.points].reverse().find((p) => p.y != null); return lp ? { s, x: X(lp.x) + 6, y: Y(lp.y as number) + 4 } : null })
    .filter(Boolean).sort((a, b) => a!.y - b!.y) as { s: Series; x: number; y: number }[] : []
  for (let i = 1; i < labels.length; i++) labels[i].y = Math.max(labels[i].y, labels[i - 1].y + 13)
  const over = labels.length ? labels[labels.length - 1].y - (H - m.b) : 0
  if (over > 0) labels.forEach((l) => { l.y -= over })
  const onMove = (e: React.MouseEvent<SVGRectElement>) => {
    const r = (e.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect()
    const px = (e.clientX - r.left) * (W / r.width)
    let near: number | null = null
    series.forEach((s) => s.points.forEach((p) => { if (near == null || Math.abs(X(p.x) - px) < Math.abs(X(near) - px)) near = p.x }))
    setHover(near)
  }
  const evLabel = (e: ScenarioEvent) => ({ link_down: '✕ ', link_up: '✓ ', link_params: '⚙ ', move: '➜ ', roam: '⇄ ' } as Record<string, string>)[e.action] + (e.action === 'roam' ? `${e.device}→${e.to}` : e.link || e.device || '')
  return (
    <div className="rounded-lg border bg-card/40 px-3 pb-1.5 pt-2.5">
      <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
        <div className="text-xs font-semibold">{title}</div>
        {series.length >= 2 && <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
          {series.map((s) => <span key={s.name} className="inline-flex items-center gap-1.5"><i className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: s.color }} />{s.name}</span>)}
        </div>}
      </div>
      <div ref={ref} className="relative">
        {!series.length ? <div className="py-10 text-center text-xs text-muted-foreground">Bu o'lchov uchun ma'lumot yo'q</div> : (
          <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} className="block overflow-visible">
            {[0, 1, 2, 3, 4].map((i) => { const v = (yMax * i) / 4, y = Y(v); return (
              <g key={i}><line x1={m.l} x2={W - m.r} y1={y} y2={y} stroke={i ? 'hsl(var(--border))' : 'hsl(var(--muted-foreground) / 0.6)'} />
                <text x={m.l - 8} y={y + 4} textAnchor="end" className="fill-muted-foreground font-mono text-[10.5px]">{fmt(v)}</text></g>) })}
            {ticks.map((x) => <text key={x} x={X(x)} y={H - 8} textAnchor="middle" className="fill-muted-foreground font-mono text-[10.5px]">{xFmt(x)}</text>)}
            {events.map((e, i) => (
              <g key={i}><line x1={X(e.t)} x2={X(e.t)} y1={m.t} y2={H - m.b} stroke="hsl(var(--muted-foreground))" strokeDasharray="4 4" />
                <text x={X(e.t) + 4} y={m.t + 11} className="fill-muted-foreground font-mono text-[10.5px]">{evLabel(e)}</text></g>))}
            {series.map((s) => {
              let d = '', pen = false
              s.points.forEach((p) => { if (p.y == null || !isFinite(p.y)) { pen = false; return } d += (pen ? 'L' : 'M') + X(p.x).toFixed(1) + ',' + Y(p.y).toFixed(1); pen = true })
              return (
                <g key={s.name}><path d={d} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
                  {markers && s.points.map((p, i) => p.y != null && isFinite(p.y) && <circle key={i} cx={X(p.x)} cy={Y(p.y)} r={4} fill={s.color} stroke="hsl(var(--background))" strokeWidth={2} />)}</g>)
            })}
            {labels.map((l) => <text key={l.s.name} x={l.x} y={l.y} className="fill-muted-foreground text-[11px] font-medium">{l.s.name.length > 18 ? l.s.name.slice(0, 17) + '…' : l.s.name}</text>)}
            {hover != null && <line x1={X(hover)} x2={X(hover)} y1={m.t} y2={H - m.b} stroke="hsl(var(--muted-foreground))" />}
            {hover != null && series.map((s) => { const p = s.points.find((q) => q.x === hover); return p && p.y != null ? <circle key={s.name} cx={X(p.x)} cy={Y(p.y)} r={4.5} fill={s.color} stroke="hsl(var(--background))" strokeWidth={2} /> : null })}
            <rect x={m.l} y={m.t} width={W - m.l - m.r} height={H - m.t - m.b} fill="transparent" onMouseMove={onMove} onMouseLeave={() => setHover(null)} />
          </svg>
        )}
        {hover != null && (
          <div className="glass pointer-events-none absolute top-2 z-10 min-w-[180px] rounded-lg px-2.5 py-2 text-[11.5px]" style={{ left: Math.min((X(hover) / W) * width + 14, width - 200) }}>
            <div className="mb-1 font-mono text-muted-foreground">{xTicks ? `qiymat = ${xFmt(hover)}` : `t = ${hover}s`}</div>
            {series.map((s) => { const p = s.points.find((q) => q.x === hover); return (
              <div key={s.name} className="flex items-center gap-1.5 leading-6 text-muted-foreground"><i className="h-2 w-2 rounded-sm" style={{ background: s.color }} />{s.name}
                <b className="ml-auto pl-3 font-mono font-semibold text-foreground">{p && p.y != null ? fmt(p.y) : '—'}</b></div>) })}
          </div>
        )}
      </div>
      {(logX || note) && <div className="px-1 pb-1 text-[11px] text-muted-foreground">{[logX && "x o'qi logarifmik", note].filter(Boolean).join(' · ')}</div>}
    </div>
  )
}

function hexToRgb(h: string) { const v = h.replace('#', ''); return [0, 2, 4].map((i) => parseInt(v.slice(i, i + 2), 16)) }
function lerpColor(a: string, b: string, t: number) {
  const A = hexToRgb(a), B = hexToRgb(b)
  return `rgb(${A.map((x, i) => Math.round(x + (B[i] - x) * t)).join(',')})`
}

/** Issiqlik xaritasi: x — 1-parametr qiymatlari, y — 2-parametr qiymatlari, katak — o'lchov. */
export function Heatmap({ xs, ys, value, fmt, title, xLabel, yLabel }: {
  xs: (string | number)[]; ys: (string | number)[]; value: (i: number, j: number) => number | null | undefined
  fmt: (v: number) => string; title: string; xLabel: string; yLabel: string
}) {
  const [ref, width] = useWidth()
  const [hover, setHover] = useState<{ i: number; j: number } | null>(null)
  const theme = useStore((s) => s.theme)          // mavzu almashsa — ranglar qayta o'qiladi
  const dark = theme === 'dark'
  const cs = getComputedStyle(document.documentElement)
  const lo = cs.getPropertyValue('--seq-lo').trim() || '#cde2fb', hi = cs.getPropertyValue('--seq-hi').trim() || '#0d366b'
  const vals = xs.flatMap((_, i) => ys.map((_, j) => value(i, j))).filter((v): v is number => v != null && isFinite(v))
  const vMin = Math.min(...vals, 0), vMax = Math.max(...vals, 1e-9)
  const m = { l: 70, r: 110, t: 8, b: 40 }
  const W = Math.max(width, 360), cellW = (W - m.l - m.r) / xs.length, cellH = Math.min(46, Math.max(28, 260 / ys.length)), H = m.t + m.b + cellH * ys.length
  const norm = (v: number) => (vMax - vMin > 0 ? (v - vMin) / (vMax - vMin) : 0.5)
  return (
    <div className="rounded-lg border bg-card/40 px-3 pb-2 pt-2.5">
      <div className="mb-1 text-xs font-semibold">{title}</div>
      <div ref={ref} className="relative">
        <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} className="block">
          {ys.map((yv, j) => xs.map((xv, i) => {
            const v = value(i, j), x = m.l + i * cellW, y = m.t + (ys.length - 1 - j) * cellH
            const t = v == null || !isFinite(v) ? null : norm(v)
            const fill = t == null ? 'hsl(var(--muted))' : lerpColor(lo, hi, t)
            const ink = t == null ? 'hsl(var(--muted-foreground))' : (dark ? t > 0.55 : t < 0.55) ? '#0a0a0a' : '#fafafa'
            return (
              <g key={`${i}-${j}`} onMouseEnter={() => setHover({ i, j })} onMouseLeave={() => setHover(null)}>
                <rect x={x + 1} y={y + 1} width={cellW - 2} height={cellH - 2} rx={4} fill={fill} stroke={hover?.i === i && hover?.j === j ? 'hsl(var(--foreground))' : 'none'} strokeWidth={1.5} />
                {cellW > 46 && <text x={x + cellW / 2} y={y + cellH / 2 + 4} textAnchor="middle" fill={ink} className="font-mono text-[10.5px]">{v == null ? '—' : fmt(v)}</text>}
                {j === 0 && <text x={x + cellW / 2} y={H - m.b + 16} textAnchor="middle" className="fill-muted-foreground font-mono text-[10.5px]">{String(xv)}</text>}
                {i === 0 && <text x={m.l - 8} y={y + cellH / 2 + 4} textAnchor="end" className="fill-muted-foreground font-mono text-[10.5px]">{String(yv)}</text>}
              </g>)
          }))}
          <text x={m.l + (W - m.l - m.r) / 2} y={H - 6} textAnchor="middle" className="fill-muted-foreground text-[11px]">{xLabel}</text>
          <text x={14} y={m.t + (H - m.t - m.b) / 2} textAnchor="middle" transform={`rotate(-90 14 ${m.t + (H - m.t - m.b) / 2})`} className="fill-muted-foreground text-[11px]">{yLabel}</text>
          <defs><linearGradient id="hmscale" x1="0" x2="0" y1="1" y2="0"><stop offset="0" stopColor={lo} /><stop offset="1" stopColor={hi} /></linearGradient></defs>
          <rect x={W - m.r + 16} y={m.t} width={12} height={H - m.t - m.b} rx={3} fill="url(#hmscale)" />
          <text x={W - m.r + 34} y={m.t + 10} className="fill-muted-foreground font-mono text-[10px]">{fmt(vMax)}</text>
          <text x={W - m.r + 34} y={H - m.b} className="fill-muted-foreground font-mono text-[10px]">{fmt(vMin)}</text>
        </svg>
        {hover && (() => { const v = value(hover.i, hover.j); return (
          <div className="glass pointer-events-none absolute top-1 z-10 rounded-lg px-2.5 py-1.5 font-mono text-[11.5px]" style={{ left: Math.min(m.l + (hover.i + 0.5) * cellW * (width / W) + 12, width - 200) }}>
            <div className="text-muted-foreground">{xLabel} = {String(xs[hover.i])}</div>
            <div className="text-muted-foreground">{yLabel} = {String(ys[hover.j])}</div>
            <div className="font-semibold">{v == null ? '—' : fmt(v)}</div>
          </div>) })()}
      </div>
    </div>
  )
}
