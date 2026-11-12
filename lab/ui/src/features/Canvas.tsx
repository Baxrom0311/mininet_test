/** Topologiya kanvasi (SVG): joylash, ko'chirish, kabel ulash, pan/zoom, paket animatsiyasi,
 *  kabel yuklamasi, Wi-Fi qamrovi va radio yorliqlari, ssenariy harakatlari, masshtab. */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Maximize2, Minus, Pause, Play, Plus, SkipForward } from 'lucide-react'
import { ICON_PATHS } from '@/components/DeviceIcon'
import { Button } from '@/components/ui/button'
import { on } from '@/lib/bus'
import { PROTO_COLORS, fmtBps } from '@/lib/format'
import { addDevice, addLink, devLabel, moveDevice, openWin, pushHistory, removeDevice, removeLink, select, setMode, useStore } from '@/lib/store'
import type { DeviceType, Project } from '@/lib/types'
import { apConfig, linkEstimate, mpp as mppOf, range, rangeForSnr } from '@/lib/wifi'
import { cn } from '@/lib/utils'

const R = 26

export interface CanvasApi { fit: () => void }
export const canvasApi: CanvasApi = { fit: () => {} }
export let hiddenProtos = new Set<string>()
export const setHiddenProtos = (s: Set<string>) => { hiddenProtos = s }

interface ActivePacket {
  el: SVGElement
  x1: number
  y1: number
  x2: number
  y2: number
  angle: number
  t0: number
  p: any
}

export function Canvas() {
  const project = useStore((s) => s.project)
  const selection = useStore((s) => s.selection)
  const mode = useStore((s) => s.mode)
  const placeType = useStore((s) => s.placeType)
  const linkSrc = useStore((s) => s.linkSrc)
  const stats = useStore((s) => s.stats)
  const running = useStore((s) => s.status.running)
  const overlay = useStore((s) => s.overlay)
  const wifi = useStore((s) => s.info?.wifi)
  const svgRef = useRef<SVGSVGElement>(null)
  const pktRef = useRef<SVGGElement>(null)
  const [view, setView] = useState({ tx: 0, ty: 0, k: 1 })
  const viewRef = useRef(view); viewRef.current = view
  const [ghost, setGhost] = useState<{ x: number; y: number } | null>(null)
  const drag = useRef<{ name?: string; dx?: number; dy?: number; pan?: { x: number; y: number; tx: number; ty: number }; moved?: boolean; before?: Project } | null>(null)
  const lastClick = useRef({ name: '', t: 0 })
  const [simPaused, setSimPaused] = useState(false)
  const [simSpeed, setSimSpeed] = useState<number>(1)
  const simPausedRef = useRef(simPaused); simPausedRef.current = simPaused
  const simSpeedRef = useRef(simSpeed); simSpeedRef.current = simSpeed
  const activePktsRef = useRef<ActivePacket[]>([])

  // ---- kabel geometriyasi (parallel kabellar ajratiladi) ----
  const geom = useMemo(() => {
    const out: Record<string, { x1: number; y1: number; x2: number; y2: number; nx: number; ny: number; ux: number; uy: number }> = {}
    const cnt: Record<string, number> = {}, idx: Record<string, number> = {}
    for (const l of Object.values(project.links)) { const k = [l.a.dev, l.b.dev].sort().join('|'); cnt[k] = (cnt[k] || 0) + 1 }
    for (const [lid, l] of Object.entries(project.links)) {
      const A = project.devices[l.a.dev], B = project.devices[l.b.dev]
      if (!A || !B) continue
      const k = [l.a.dev, l.b.dev].sort().join('|')
      const i = (idx[k] = (idx[k] || 0) + 1)
      const dx = B.x - A.x, dy = B.y - A.y, len = Math.hypot(dx, dy) || 1
      const nx = -dy / len, ny = dx / len
      const off = (i - (cnt[k] + 1) / 2) * 14 * (l.a.dev < l.b.dev ? 1 : -1)
      out[lid] = { x1: A.x + nx * off, y1: A.y + ny * off, x2: B.x + nx * off, y2: B.y + ny * off, nx, ny, ux: dx / len, uy: dy / len }
    }
    return out
  }, [project.links, project.devices])
  const geomRef = useRef(geom); geomRef.current = geom

  // Qadamma-qadam simulyatsiya oldinga yurishi (Step Forward)
  const stepForward = useCallback(() => {
    if (!simPausedRef.current) return
    const now = performance.now()
    const duration = 520 / (simSpeedRef.current || 1)
    const stepAmount = duration * 0.15

    const active = activePktsRef.current
    for (let i = active.length - 1; i >= 0; i--) {
      const a = active[i]
      a.t0 -= stepAmount
      const t = (now - a.t0) / duration
      if (t >= 1) {
        a.el.remove()
        active.splice(i, 1)
        continue
      }
      const e = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2
      const cx = a.x1 + (a.x2 - a.x1) * e
      const cy = a.y1 + (a.y2 - a.y1) * e
      a.el.setAttribute('transform', `translate(${cx}, ${cy}) rotate(${a.angle})`)
    }
  }, [])

  // ---- paket animatsiyasi (React'dan tashqarida, rAF) ----
  useEffect(() => {
    const active: ActivePacket[] = []
    activePktsRef.current = active
    let lastNow = performance.now()
    let raf = 0
    const tick = (now: number) => {
      if (!simPausedRef.current) {
        for (let i = active.length - 1; i >= 0; i--) {
          const a = active[i]
          const duration = 520 / (simSpeedRef.current || 1)
          const t = (now - a.t0) / duration
          if (t >= 1) { a.el.remove(); active.splice(i, 1); continue }
          const e = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2
          const cx = a.x1 + (a.x2 - a.x1) * e
          const cy = a.y1 + (a.y2 - a.y1) * e
          a.el.setAttribute('transform', `translate(${cx}, ${cy}) rotate(${a.angle})`)
        }
      } else {
        const elapsed = now - lastNow
        for (const a of active) a.t0 += elapsed
      }
      lastNow = now
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)

    const off = on('ev:packet', (p: { link: string; dir: string; proto: string; dropped?: boolean; summary?: string }) => {
      if (!useStore.getState().status.running || document.hidden || active.length > 260 || hiddenProtos.has(p.proto)) return
      const g = geomRef.current[p.link]; const layer = pktRef.current
      if (!g || !layer) return
      const fwd = p.dir !== 'ba'
      const x1 = fwd ? g.x1 : g.x2, y1 = fwd ? g.y1 : g.y2
      const x2 = fwd ? g.x2 : g.x1, y2 = fwd ? g.y2 : g.y1
      const angle = Math.atan2(y2 - y1, x2 - x1) * (180 / Math.PI)
      const col = p.dropped ? '#ef4444' : (PROTO_COLORS[p.proto] || PROTO_COLORS.other)

      if (p.dropped) {
        // Paket yo'qotilishi (Packet Drop Badge) animatsiyasi
        const mx = (x1 + x2) / 2, my = (y1 + y2) / 2
        createDropBadge(layer, mx, my, p)
        return
      }

      // Cisco Packet Tracer uslubidagi paket konverti (Envelope)
      const gEl = document.createElementNS('http://www.w3.org/2000/svg', 'g')
      gEl.setAttribute('class', 'packet-envelope')
      gEl.setAttribute('style', 'cursor: pointer; pointer-events: auto;')

      // Konvert asosiy tanasi
      const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect')
      rect.setAttribute('x', '-12')
      rect.setAttribute('y', '-8')
      rect.setAttribute('width', '24')
      rect.setAttribute('height', '16')
      rect.setAttribute('rx', '2.5')
      rect.setAttribute('fill', col)
      rect.setAttribute('stroke', '#ffffff')
      rect.setAttribute('stroke-width', '0.9')
      rect.setAttribute('stroke-opacity', '0.75')
      rect.setAttribute('style', `filter: drop-shadow(0 2px 5px ${col});`)
      gEl.appendChild(rect)

      // Konvert ustki qopqog'i (Flap chizig'i)
      const flap = document.createElementNS('http://www.w3.org/2000/svg', 'path')
      flap.setAttribute('d', 'M-11,-7 L0,1 L11,-7')
      flap.setAttribute('fill', 'none')
      flap.setAttribute('stroke', '#ffffff')
      flap.setAttribute('stroke-width', '1')
      flap.setAttribute('stroke-opacity', '0.85')
      gEl.appendChild(flap)

      // Pastki burchak chiziqlari
      const fold = document.createElementNS('http://www.w3.org/2000/svg', 'path')
      fold.setAttribute('d', 'M-11,7 L-3,1 M11,7 L3,1')
      fold.setAttribute('fill', 'none')
      fold.setAttribute('stroke', '#ffffff')
      fold.setAttribute('stroke-width', '0.75')
      fold.setAttribute('stroke-opacity', '0.4')
      gEl.appendChild(fold)

      // Protokol yorlig'i
      const txt = document.createElementNS('http://www.w3.org/2000/svg', 'text')
      txt.setAttribute('x', '0')
      txt.setAttribute('y', '5')
      txt.setAttribute('text-anchor', 'middle')
      txt.setAttribute('fill', '#ffffff')
      txt.setAttribute('font-size', '6.5')
      txt.setAttribute('font-weight', 'bold')
      txt.setAttribute('font-family', 'monospace')
      txt.setAttribute('style', 'pointer-events: none; user-select: none;')
      txt.textContent = (p.proto || 'PKT').slice(0, 4)
      gEl.appendChild(txt)

      const titleEl = document.createElementNS('http://www.w3.org/2000/svg', 'title')
      titleEl.textContent = `${p.proto}: ${p.summary || `${p.proto} Packet`} (PDU Inspektori uchun bosing)`
      gEl.appendChild(titleEl)

      gEl.onclick = (e) => {
        e.stopPropagation()
        openWin('pdu', `pkt-${Date.now()}`, { arg: p, w: 820, h: 580 })
      }

      gEl.setAttribute('transform', `translate(${x1}, ${y1}) rotate(${angle})`)
      layer.appendChild(gEl)
      active.push({ el: gEl, x1, y1, x2, y2, angle, t0: performance.now(), p })
    })

    return () => { cancelAnimationFrame(raf); off() }
  }, [])

  const toWorld = useCallback((cx: number, cy: number) => {
    const r = svgRef.current!.getBoundingClientRect(), v = viewRef.current
    return { x: (cx - r.left - v.tx) / v.k, y: (cy - r.top - v.ty) / v.k }
  }, [])
  const zoomAt = (f: number, cx?: number, cy?: number) => {
    const r = svgRef.current!.getBoundingClientRect()
    const x = cx ?? r.width / 2, y = cy ?? r.height / 2
    setView((v) => { const k = Math.min(3, Math.max(0.25, v.k * f)); return { k, tx: x - (x - v.tx) * (k / v.k), ty: y - (y - v.ty) * (k / v.k) } })
  }
  const fit = useCallback(() => {
    const ds = Object.values(useStore.getState().project.devices), r = svgRef.current?.getBoundingClientRect()
    if (!ds.length || !r?.width) { setView({ tx: 0, ty: 0, k: 1 }); return }
    const xs = ds.map((d) => d.x), ys = ds.map((d) => d.y)
    const minx = Math.min(...xs) - 90, maxx = Math.max(...xs) + 90, miny = Math.min(...ys) - 90, maxy = Math.max(...ys) + 90
    const k = Math.min(1.6, Math.max(0.3, Math.min(r.width / (maxx - minx), r.height / (maxy - miny))))
    setView({ k, tx: (r.width - (maxx - minx) * k) / 2 - minx * k, ty: (r.height - (maxy - miny) * k) / 2 - miny * k })
  }, [])
  canvasApi.fit = fit
  useEffect(() => { const t = setTimeout(fit, 60); return () => clearTimeout(t) }, [project.name, fit])
  useEffect(() => {
    const el = svgRef.current!
    const onWheel = (ev: WheelEvent) => { ev.preventDefault(); const r = el.getBoundingClientRect(); zoomAt(ev.deltaY < 0 ? 1.1 : 1 / 1.1, ev.clientX - r.left, ev.clientY - r.top) }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [])

  // ---- sichqoncha ----
  const onPointerDown = (ev: React.PointerEvent) => {
    if (ev.button !== 0) return
    const t = ev.target as Element
    const devEl = t.closest('[data-dev]'), linkEl = t.closest('[data-link]')
    const w = toWorld(ev.clientX, ev.clientY)
    const st = useStore.getState()
    if (st.mode === 'pick') { const cb = st.pickCb; setMode('select'); cb?.(w.x, w.y); return }
    if (st.mode === 'place' && !devEl && st.placeType) { addDevice(st.placeType, w.x, w.y); if (!ev.shiftKey) setMode('select'); return }
    if (devEl) {
      const name = devEl.getAttribute('data-dev')!
      if (st.mode === 'delete') { removeDevice(name); return }
      if (st.mode === 'link') {
        if (!st.linkSrc) useStore.setState({ linkSrc: name })
        else if (st.linkSrc !== name) { const a = st.linkSrc; useStore.setState({ linkSrc: null }); setGhost(null); addLink(a, name) }
        return
      }
      const now = performance.now()
      if (lastClick.current.name === name && now - lastClick.current.t < 380) { lastClick.current = { name: '', t: 0 }; openWin('device', name); return }
      lastClick.current = { name, t: now }
      select({ kind: 'device', id: name })
      const d = st.project.devices[name]
      drag.current = { name, dx: w.x - d.x, dy: w.y - d.y, moved: false, before: st.project }
      svgRef.current!.setPointerCapture(ev.pointerId)
      return
    }
    if (linkEl) {
      const lid = linkEl.getAttribute('data-link')!
      if (st.mode === 'delete') { removeLink(lid); return }
      select({ kind: 'link', id: lid }); return
    }
    drag.current = { pan: { x: ev.clientX, y: ev.clientY, tx: viewRef.current.tx, ty: viewRef.current.ty }, moved: false }
    svgRef.current!.setPointerCapture(ev.pointerId)
  }
  const onPointerMove = (ev: React.PointerEvent) => {
    const dr = drag.current
    if (dr?.name) {
      const w = toWorld(ev.clientX, ev.clientY)
      let nx = w.x - dr.dx!, ny = w.y - dr.dy!
      if (!ev.altKey) { nx = Math.round(nx / 8) * 8; ny = Math.round(ny / 8) * 8 }
      const d = useStore.getState().project.devices[dr.name]
      if (d && (nx !== d.x || ny !== d.y)) { moveDevice(dr.name, nx, ny); dr.moved = true }
    } else if (dr?.pan) {
      const p = dr.pan
      setView((v) => ({ ...v, tx: p.tx + ev.clientX - p.x, ty: p.ty + ev.clientY - p.y })); dr.moved = true
    } else if (useStore.getState().mode === 'link' && useStore.getState().linkSrc) {
      setGhost(toWorld(ev.clientX, ev.clientY))
    }
  }
  const onPointerUp = () => {
    const dr = drag.current
    if (dr?.name && dr.moved) { const d = useStore.getState().project.devices[dr.name]; pushHistory(dr.before!, false); moveDevice(dr.name, d.x, d.y, true) }
    if (dr?.pan && !dr.moved && useStore.getState().selection) select(null)
    drag.current = null
  }

  // ---- render ----
  const hint = mode === 'link' ? (linkSrc ? `Kabel: ${linkSrc} → ikkinchi qurilmani bosing` : 'Kabel: birinchi qurilmani bosing (Esc — bekor)')
    : mode === 'delete' ? "O'chirish: qurilma yoki kabelni bosing" : mode === 'place' && placeType ? `Joylash: ${devLabel(placeType)} — kanvasga bosing (Shift — bir nechta)`
      : mode === 'pick' ? 'Harakat manzili: kanvasdagi nuqtani bosing (Esc — bekor)' : ''
  const m = mppOf(project)
  const scale = (() => {
    const target = (120 / view.k) * m, p = Math.pow(10, Math.floor(Math.log10(target)))
    const nice = [1, 2, 5, 10].map((x) => x * p).filter((x) => x <= target).pop() || p
    return { px: (nice / m) * view.k, label: nice >= 1000 ? nice / 1000 + ' km' : nice + ' m' }
  })()
  const prevPos: Record<string, [number, number]> = {}

  return (
    <div className={cn('relative h-full w-full overflow-hidden', (mode === 'link' || mode === 'place' || mode === 'pick') && 'cursor-crosshair', mode === 'delete' && 'cursor-not-allowed')}>
      <svg ref={svgRef} className="block h-full w-full touch-none select-none"
        onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => { e.preventDefault(); const t = e.dataTransfer.getData('text/netlab-type') as DeviceType; if (t) { const w = toWorld(e.clientX, e.clientY); addDevice(t, w.x, w.y); setMode('select') } }}>
        <defs>
          <pattern id="gridp" width="32" height="32" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r="1.1" fill="hsl(var(--grid))" /></pattern>
          <marker id="mvarrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="hsl(var(--foreground))" /></marker>
        </defs>
        <g transform={`translate(${view.tx},${view.ty}) scale(${view.k})`}>
          <rect x={-20000} y={-20000} width={40000} height={40000} fill="url(#gridp)" />
          {/* Wi-Fi qamrovi */}
          {wifi && Object.entries(project.devices).filter(([, d]) => d.type === 'wifiap').map(([n, d]) => {
            const c = apConfig(wifi, d), sel = selection?.id === n
            const rMax = range(wifi, c) / m, rHi = rangeForSnr(wifi, c, 25) / m
            return (
              <g key={'cov' + n} className="pointer-events-none">
                <circle cx={d.x} cy={d.y} r={rMax} fill="hsl(var(--foreground) / 0.025)" stroke="hsl(var(--foreground) / 0.22)" strokeDasharray="3 6" />
                <circle cx={d.x} cy={d.y} r={rHi} fill="hsl(var(--foreground) / 0.03)" stroke="hsl(var(--foreground) / 0.14)" />
                {sel && <text x={d.x} y={d.y - rMax - 6} textAnchor="middle" className="fill-muted-foreground font-mono text-[11px]" style={{ paintOrder: 'stroke', stroke: 'hsl(var(--background))', strokeWidth: 3 }}>
                  qamrov ~{Math.round(range(wifi, c))} m · {c.standard.replace('80211', '802.11')} {c.band} GHz · kanal {c.channel}</text>}
              </g>
            )
          })}
          {/* ssenariy harakatlari */}
          {overlay.map((e, i) => {
            const d = e.device ? project.devices[e.device] : null
            if (!d || !isFinite(e.x as number) || !isFinite(e.y as number)) return null
            const [x0, y0] = prevPos[e.device!] || [d.x, d.y]
            prevPos[e.device!] = [e.x!, e.y!]
            return (
              <g key={'mv' + i} className="pointer-events-none">
                <line x1={x0} y1={y0} x2={e.x} y2={e.y} stroke="hsl(var(--foreground))" strokeWidth={1.8} strokeDasharray="6 5" markerEnd="url(#mvarrow)" opacity={0.7} />
                <circle cx={e.x} cy={e.y} r={6} fill="hsl(var(--foreground) / 0.15)" stroke="hsl(var(--foreground))" />
                <text x={(x0 + e.x!) / 2} y={(y0 + e.y!) / 2 - 8} textAnchor="middle" className="fill-foreground font-mono text-[10.5px]" style={{ paintOrder: 'stroke', stroke: 'hsl(var(--background))', strokeWidth: 3 }}>
                  {e.device}: t={e.t}s → {+e.t + (+(e.duration || 0))}s</text>
              </g>
            )
          })}
          {/* kabellar */}
          {Object.entries(project.links).map(([lid, l]) => {
            const g = geom[lid]; if (!g) return null
            const est = wifi ? linkEstimate(wifi, project, lid) : null
            const down = l.up === false || (est && !est.ok)
            const sel = selection?.kind === 'link' && selection.id === lid
            const st = stats[lid]
            const util = running && st && !down ? st.util || 0 : 0
            const stroke = down ? 'hsl(var(--destructive))' : util > 0.001 ? `hsl(${Math.round(150 - Math.min(util, 1) * 150)} 80% 50%)` : sel ? 'hsl(var(--foreground))' : 'hsl(var(--muted-foreground) / 0.8)'
            const sw = sel ? 3 : 2 + Math.min(util, 1) * 4
            const d0 = R + 20
            const peak = st ? Math.max(st.ab_bps || 0, st.ba_bps || 0) : 0
            const lbl = { paintOrder: 'stroke' as const, stroke: 'hsl(var(--background))', strokeWidth: 3 }
            const pLabels = !running && !est && !down && l.params ? [
              l.params.delay && l.params.delay !== '0ms' ? l.params.delay : '',
              l.params.loss ? `${l.params.loss}%` : '',
              l.params.bw && l.params.bw !== 1000 && l.params.bw !== 100 ? `${l.params.bw}M` : '',
            ].filter(Boolean).join(' · ') : ''
            return (
              <g key={lid} data-link={lid} className="cursor-pointer">
                <line x1={g.x1} y1={g.y1} x2={g.x2} y2={g.y2} stroke={stroke} strokeWidth={sw} strokeLinecap="round"
                  strokeDasharray={est ? '2 5' : down ? '6 5' : undefined} className="pointer-events-none" />
                <line x1={g.x1} y1={g.y1} x2={g.x2} y2={g.y2} stroke="transparent" strokeWidth={14} />
                <text x={g.x1 + g.ux * d0 + g.nx * 9} y={g.y1 + g.uy * d0 + g.ny * 9 + 3} textAnchor="middle" className="pointer-events-none fill-muted-foreground font-mono text-[9.5px]" style={lbl}>eth{l.a.port}</text>
                <text x={g.x2 - g.ux * d0 + g.nx * 9} y={g.y2 - g.uy * d0 + g.ny * 9 + 3} textAnchor="middle" className="pointer-events-none fill-muted-foreground font-mono text-[9.5px]" style={lbl}>eth{l.b.port}</text>
                {running && peak > 0 && !down && <text x={(g.x1 + g.x2) / 2 - g.nx * 10} y={(g.y1 + g.y2) / 2 - g.ny * 10 + 3} textAnchor="middle" className="pointer-events-none fill-foreground font-mono text-[9.5px]" style={lbl}>{fmtBps(peak)}</text>}
                {pLabels && <text x={(g.x1 + g.x2) / 2 - g.nx * 10} y={(g.y1 + g.y2) / 2 - g.ny * 10 + 3} textAnchor="middle" className="pointer-events-none fill-muted-foreground font-mono text-[9px]" style={lbl}>{pLabels}</text>}
                {est && <text x={(g.x1 + g.x2) / 2 + g.nx * 12} y={(g.y1 + g.y2) / 2 + g.ny * 12 + 3} textAnchor="middle"
                  className={cn('pointer-events-none font-mono text-[9.5px]', est.ok ? 'fill-muted-foreground' : 'fill-destructive')} style={lbl}>
                  {est.ok ? `MCS${est.mcs} · ~${Math.round(est.thr_mbps)} Mb/s · ${Math.round(est.distance_m)} m` : `signal yo'q · ${Math.round(est.distance_m)} m`}</text>}
              </g>
            )
          })}
          <g ref={pktRef} className="pointer-events-none" />
          {/* qurilmalar */}
          {Object.entries(project.devices).map(([name, d]) => {
            const sel = selection?.kind === 'device' && selection.id === name
            const ips = Object.values((d.config?.interfaces || {}) as Record<string, { ip?: string }>).map((i) => i.ip).filter(Boolean) as string[]
            const lbl = { paintOrder: 'stroke' as const, stroke: 'hsl(var(--background))', strokeWidth: 3 }
            return (
              <g key={name} data-dev={name} transform={`translate(${d.x},${d.y})`} className="group cursor-pointer">
                <circle r={R + 7} fill="none" stroke={linkSrc === name ? 'hsl(var(--success))' : sel ? 'hsl(var(--foreground))' : 'none'} strokeWidth={sel || linkSrc === name ? 1.8 : 0} strokeDasharray={sel ? '4 3' : undefined} />
                <circle r={R} fill="hsl(var(--card))" stroke="hsl(var(--border))" strokeWidth={1.5} className="transition-[stroke] group-hover:stroke-[hsl(var(--foreground)/0.6)]" />
                <g transform="translate(-17,-17) scale(0.708)" fill="none" stroke="hsl(var(--foreground))" strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round"
                  style={{ color: 'hsl(var(--foreground))' }} dangerouslySetInnerHTML={{ __html: ICON_PATHS[d.type] }} />
                {running && <circle cx={R * 0.72} cy={-R * 0.72} r={4.5} fill="hsl(var(--success))" stroke="hsl(var(--background))" strokeWidth={1.5} />}
                <text y={R + 17} textAnchor="middle" className="fill-foreground text-[12.5px] font-semibold" style={lbl}>{name}</text>
                {ips.length > 0 && <text y={R + 30} textAnchor="middle" className="fill-muted-foreground font-mono text-[10.5px]" style={lbl}>{ips[0]}{ips.length > 1 ? ` +${ips.length - 1}` : ''}</text>}
              </g>
            )
          })}
          {ghost && linkSrc && project.devices[linkSrc] && (
            <line x1={project.devices[linkSrc].x} y1={project.devices[linkSrc].y} x2={ghost.x} y2={ghost.y} stroke="hsl(var(--success))" strokeWidth={2} strokeDasharray="5 4" className="pointer-events-none" />
          )}
        </g>
      </svg>
      {hint && <div className="glass pointer-events-none absolute left-3 top-3 rounded-md px-3 py-1.5 text-xs text-muted-foreground">{hint}</div>}
      {!Object.keys(project.devices).length && (
        <div className="pointer-events-none absolute inset-0 grid place-items-center text-center text-sm leading-7 text-muted-foreground">
          <div>Chapdagi paneldan qurilma tanlang va shu yerga bosing.<br />Kabel: <span className="kbd">C</span> — ikki qurilmani ketma-ket bosing. Qurilmaga kirish: ikki marta bosing.</div>
        </div>
      )}
      <div className="pointer-events-none absolute bottom-3 left-3 flex flex-col gap-1 font-mono text-[10.5px] text-muted-foreground">
        <i className="block h-1.5 border border-t-0 border-muted-foreground" style={{ width: scale.px }} />
        <span>{scale.label}</span>
      </div>

      {/* Cisco Simulation Controller: Play / Pause / Step / Speed */}
      <div className="absolute bottom-3 left-1/2 -translate-x-1/2 flex items-center gap-1.5 rounded-lg border bg-card/90 px-3 py-1.5 shadow-xl backdrop-blur select-none">
        <div className="flex items-center gap-1.5 mr-1">
          <span
            className={cn(
              "inline-block h-2 w-2 rounded-full",
              simPaused ? "bg-amber-400" : "bg-emerald-400 animate-pulse"
            )}
          />
          <span className="text-[10.5px] font-medium text-muted-foreground hidden sm:inline">
            {simPaused ? "Pauzada" : "Simulyatsiya"}
          </span>
        </div>

        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7"
          onClick={() => setSimPaused(!simPaused)}
          title={simPaused ? "Davom ettirish (Play)" : "To'xtatish (Pause)"}
        >
          {simPaused ? <Play className="h-4 w-4 text-emerald-400 fill-emerald-400" /> : <Pause className="h-4 w-4" />}
        </Button>

        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7"
          disabled={!simPaused}
          onClick={stepForward}
          title="Qadamma-qadam oldinga (Step Forward)"
        >
          <SkipForward className="h-4 w-4" />
        </Button>

        <div className="mx-1 h-4 w-px bg-border" />
        <span className="text-[11px] font-medium text-muted-foreground">Tezlik:</span>
        {[0.5, 1, 2].map((sp) => (
          <button
            key={sp}
            onClick={() => setSimSpeed(sp)}
            className={cn(
              "rounded px-1.5 py-0.5 text-[11px] font-mono transition-colors",
              simSpeed === sp ? "bg-primary text-primary-foreground font-bold" : "text-muted-foreground hover:bg-muted"
            )}
          >
            {sp}x
          </button>
        ))}
      </div>

      <div className="absolute bottom-3 right-3 flex gap-1.5">
        <Button variant="outline" size="icon" className="glass" onClick={() => zoomAt(1.2)} title="Kattalashtirish"><Plus /></Button>
        <Button variant="outline" size="icon" className="glass" onClick={() => zoomAt(1 / 1.2)} title="Kichiklashtirish"><Minus /></Button>
        <Button variant="outline" size="icon" className="glass" onClick={fit} title="Hammasini ko'rsatish (F)"><Maximize2 /></Button>
      </div>
    </div>
  )
}

function createDropBadge(layer: SVGElement, x: number, y: number, p: any) {
  const g = document.createElementNS('http://www.w3.org/2000/svg', 'g')
  g.setAttribute('class', 'packet-drop-badge')
  g.setAttribute('transform', `translate(${x}, ${y})`)
  g.setAttribute('style', 'cursor: pointer; pointer-events: auto;')

  // Qizil to'lqin halqasi (Ripple ring animation)
  const ripple = document.createElementNS('http://www.w3.org/2000/svg', 'circle')
  ripple.setAttribute('r', '8')
  ripple.setAttribute('fill', 'none')
  ripple.setAttribute('stroke', '#ef4444')
  ripple.setAttribute('stroke-width', '2')

  const animR = document.createElementNS('http://www.w3.org/2000/svg', 'animate')
  animR.setAttribute('attributeName', 'r')
  animR.setAttribute('values', '6;24')
  animR.setAttribute('dur', '1.2s')
  animR.setAttribute('repeatCount', 'indefinite')
  ripple.appendChild(animR)

  const animOp = document.createElementNS('http://www.w3.org/2000/svg', 'animate')
  animOp.setAttribute('attributeName', 'opacity')
  animOp.setAttribute('values', '1;0')
  animOp.setAttribute('dur', '1.2s')
  animOp.setAttribute('repeatCount', 'indefinite')
  ripple.appendChild(animOp)
  g.appendChild(ripple)

  // Asosiy qizil nishon doirasi
  const badge = document.createElementNS('http://www.w3.org/2000/svg', 'circle')
  badge.setAttribute('r', '9')
  badge.setAttribute('fill', '#ef4444')
  badge.setAttribute('stroke', '#ffffff')
  badge.setAttribute('stroke-width', '1.5')
  badge.setAttribute('style', 'filter: drop-shadow(0 0 6px #ef4444);')
  g.appendChild(badge)

  // Oq '✕' belgisi
  const cross = document.createElementNS('http://www.w3.org/2000/svg', 'text')
  cross.setAttribute('x', '0')
  cross.setAttribute('y', '3.5')
  cross.setAttribute('text-anchor', 'middle')
  cross.setAttribute('fill', '#ffffff')
  cross.setAttribute('font-size', '10')
  cross.setAttribute('font-weight', 'bold')
  cross.setAttribute('font-family', 'sans-serif')
  cross.setAttribute('style', 'pointer-events: none; user-select: none;')
  cross.textContent = '✕'
  g.appendChild(cross)

  // DROP yorliq plakati
  const pill = document.createElementNS('http://www.w3.org/2000/svg', 'rect')
  pill.setAttribute('x', '-22')
  pill.setAttribute('y', '-22')
  pill.setAttribute('width', '44')
  pill.setAttribute('height', '14')
  pill.setAttribute('rx', '3')
  pill.setAttribute('fill', '#dc2626')
  pill.setAttribute('stroke', '#ffffff')
  pill.setAttribute('stroke-width', '0.8')
  g.appendChild(pill)

  const pillText = document.createElementNS('http://www.w3.org/2000/svg', 'text')
  pillText.setAttribute('x', '0')
  pillText.setAttribute('y', '-12')
  pillText.setAttribute('text-anchor', 'middle')
  pillText.setAttribute('fill', '#ffffff')
  pillText.setAttribute('font-size', '8')
  pillText.setAttribute('font-weight', 'bold')
  pillText.setAttribute('font-family', 'monospace')
  pillText.setAttribute('style', 'pointer-events: none; user-select: none;')
  pillText.textContent = '✕ DROP'
  g.appendChild(pillText)

  // Tooltip
  const title = document.createElementNS('http://www.w3.org/2000/svg', 'title')
  title.textContent = `Paket Yo'qotildi (Drop): ${p.proto} · Queue Overflow / Bufferbloat (PDU Inspektori uchun bosing)`
  g.appendChild(title)

  g.onclick = (e) => {
    e.stopPropagation()
    openWin('pdu', `pkt-drop-${Date.now()}`, { arg: { ...p, dropped: true }, w: 820, h: 580 })
  }

  layer.appendChild(g)

  // 2.2 soniyadan so'ng asta-sekin yo'qolish
  setTimeout(() => {
    g.style.transition = 'opacity 0.4s'
    g.style.opacity = '0'
    setTimeout(() => g.remove(), 400)
  }, 2200)
}
