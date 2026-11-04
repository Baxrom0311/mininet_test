import { useEffect, useMemo, useState } from 'react'
import { ArrowLeft, BarChart3, Download, X } from 'lucide-react'
import { Heatmap, LineChart, type Series } from '@/components/charts'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Select } from '@/components/ui/select'
import { DataTable, Section } from '@/components/ui/table'
import { Segmented } from '@/components/ui/tabs'
import { api } from '@/lib/api'
import { on } from '@/lib/bus'
import { engineName, fmtBps, fmtMs, fmtPct, seriesColor, storage } from '@/lib/format'
import { useStore, type Win } from '@/lib/store'
import type { RunListItem, RunResult } from '@/lib/types'
import { WindowFrame } from './WindowFrame'
import { cn } from '@/lib/utils'

type MetricId = 'thr_bps' | 'delay_ms' | 'rtt_ms' | 'jitter_ms' | 'loss_pct' | 'util'
const METRICS: { id: MetricId; label: string; fmt: (v: number) => string; sum?: string; link?: boolean }[] = [
  { id: 'thr_bps', label: "O'tkazuvchanlik", fmt: fmtBps, sum: 'avg_thr_bps' },
  { id: 'delay_ms', label: 'Kechikish', fmt: fmtMs, sum: 'avg_delay_ms' },
  { id: 'rtt_ms', label: 'RTT', fmt: fmtMs, sum: 'avg_rtt_ms' },
  { id: 'jitter_ms', label: 'Jitter', fmt: fmtMs, sum: 'avg_jitter_ms' },
  { id: 'loss_pct', label: "Yo'qotish", fmt: fmtPct, sum: 'loss_pct' },
  { id: 'util', label: 'Kabel yuklamasi', fmt: (v) => fmtPct(v * 100), link: true },
]
const FIELD_NAME: Record<string, string> = { channel: 'AP kanali', tx_power: 'AP quvvati, dBm', bw: 'kabel tezligi, Mbit/s', delay: 'kechikish, ms', loss: 'loss, %', queue: 'navbat, paket', rate: 'ilova tezligi', size: 'paket, B' }
const pnum = (v: unknown) => { const m = String(v).match(/[\d.]+/); return m ? parseFloat(m[0]) : NaN }
const runName = (r: RunResult) => (r.label || r.scenario_name) + ' · ' + engineName(r.engine)

export function ResultsWindow({ win }: { win: Win }) {
  const project = useStore((s) => s.project)
  const pname = project.name
  const [runs, setRuns] = useState<RunListItem[]>([])
  const [cache, setCache] = useState<Record<string, RunResult>>({})
  const [selected, setSelected] = useState<string[]>(win.arg?.run ? [win.arg.run] : [])
  const [study, setStudy] = useState<string | null>(win.arg?.study || null)
  const [metric, setMetric] = useState<MetricId>(storage.get('netlab.metric', 'thr_bps'))
  const [flowSel, setFlowSel] = useState('')
  const [linkSel, setLinkSel] = useState('')

  useEffect(() => { if (win.arg?.run) { setStudy(null); setSelected([win.arg.run]) } if (win.arg?.study) setStudy(win.arg.study) }, [win.arg])
  const loadList = async () => {
    try {
      const r = (await api.get<{ runs: RunListItem[] }>('/api/runs/' + encodeURIComponent(pname))).runs
      setRuns(r)
      setSelected((s) => s.length ? s : (r.find((x) => x.status !== 'running' && !x.study_id)?.run_id ? [r.find((x) => x.status !== 'running' && !x.study_id)!.run_id] : []))
    } catch { setRuns([]) }
  }
  useEffect(() => { loadList() }, [pname])
  useEffect(() => on('ev:run', (ev: any) => { if (ev.done || ev.progress === 0) loadList() }), [pname])
  const need = useMemo(() => study ? runs.filter((r) => r.study_id === study && r.status === 'done').map((r) => r.run_id) : selected, [study, runs, selected])
  useEffect(() => {
    need.filter((id) => !cache[id]).forEach((id) => api.get<RunResult>(`/api/runs/${encodeURIComponent(pname)}/${id}`).then((r) => setCache((c) => ({ ...c, [id]: r }))).catch(() => {}))
  }, [need.join(',')])
  useStore((s) => s.theme)   // mavzu almashsa seriya ranglari qayta o'qiladi
  const data = need.map((id) => cache[id]).filter(Boolean)
    .sort((a, b) => (a.study_index2 ?? 0) - (b.study_index2 ?? 0) || (a.study_index ?? 0) - (b.study_index ?? 0))
  const M = METRICS.find((m) => m.id === metric) || METRICS[0]

  const exportCsv = () => {
    const lines = [['run_id', 'scenario', 'engine', 'param', 'value', 'param2', 'value2', 'flow', 'avg_thr_bps', 'avg_delay_ms', 'avg_rtt_ms', 'avg_jitter_ms', 'loss_pct'].join(',')]
    data.forEach((r) => Object.values(r.flows || {}).forEach((f) => {
      const s = f.summary || {}
      lines.push([r.run_id, r.scenario_name, r.engine, r.study_param?.label, r.study_value, r.study_param2?.label, r.study_value2, JSON.stringify(f.label || f.id),
        s.avg_thr_bps, s.avg_delay_ms, s.avg_rtt_ms, s.avg_jitter_ms, s.loss_pct].map((v) => v ?? '').join(','))
    }))
    const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([lines.join('\n')], { type: 'text/csv' })); a.download = `${pname}_natijalar.csv`; a.click()
  }
  const summaryTable = (key: (r: RunResult) => string, keyHead: string) => (
    <Section title="Xulosa (o'rtacha qiymatlar)">
      <DataTable columns={[keyHead, 'Oqim', "O'tkazuvchanlik", 'Kechikish', 'RTT', 'Jitter', "Yo'qotish"]}
        rows={data.flatMap((r) => Object.values(r.flows || {}).map((f) => { const s = f.summary || {}; return [key(r), f.label || f.id, fmtBps(s.avg_thr_bps), fmtMs(s.avg_delay_ms), fmtMs(s.avg_rtt_ms), fmtMs(s.avg_jitter_ms), fmtPct(s.loss_pct)] }))} />
    </Section>
  )
  const metricTabs = <Segmented value={metric} onChange={(v) => { setMetric(v); storage.set('netlab.metric', v) }} items={METRICS.map((m) => ({ value: m.id, label: m.label }))} />

  let body: React.ReactNode
  if (!data.length) body = <div className="p-8 text-center text-xs text-muted-foreground">{study ? 'Tadqiqot natijalari hali tayyor emas…' : 'Chapdan bir yoki bir nechta natijani tanlang.'}</div>
  else if (study) {
    const P = data[0].study_param || {}, P2 = data[0].study_param2
    const flowIds = [...new Set(data.flatMap((r) => Object.keys(r.flows || {})))]
    const fsel = flowIds.includes(flowSel) ? flowSel : flowIds[0]
    const val = (r?: RunResult): number | null => {
      if (!r) return null
      if (M.link) { const lid = P.kind === 'link' ? P.link : Object.keys(r.links || {})[0]; const pts = r.links?.[lid] || []; return pts.length ? pts.reduce((a, p) => a + p.util, 0) / pts.length : null }
      return (r.flows[fsel]?.summary || {})[M.sum!] ?? null
    }
    if (P2) {
      const xs = [...new Set(data.map((r) => r.study_index!))].sort((a, b) => a - b), ys = [...new Set(data.map((r) => r.study_index2!))].sort((a, b) => a - b)
      const find = (i: number, j: number) => data.find((r) => r.study_index === xs[i] && r.study_index2 === ys[j])
      body = (
        <>
          <div className="flex flex-wrap items-center gap-2">{metricTabs}{!M.link && <><span className="text-xs text-muted-foreground">Oqim:</span>
            <Select className="w-56" value={fsel} onChange={setFlowSel} options={flowIds.map((f) => ({ value: f, label: data[0].flows[f]?.label || f }))} /></>}</div>
          <Heatmap xs={xs.map((i) => data.find((r) => r.study_index === i)!.study_value)} ys={ys.map((j) => data.find((r) => r.study_index2 === j)!.study_value2)}
            value={(i, j) => val(find(i, j))} fmt={M.fmt} xLabel={`${P.label} (${FIELD_NAME[P.field] || P.field})`} yLabel={`${P2.label} (${FIELD_NAME[P2.field] || P2.field})`}
            title={`${M.label} (o'rtacha)${M.link ? '' : ' — ' + (data[0].flows[fsel]?.label || fsel)} · ${engineName(data[0].engine)}`} />
          {summaryTable((r) => `${r.study_value} × ${r.study_value2}`, `${P.label} × ${P2.label}`)}
        </>
      )
    } else {
      const sorted = [...data].sort((a, b) => (a.study_index || 0) - (b.study_index || 0))
      const xs = sorted.map((r) => pnum(r.study_value))
      const series: Series[] = M.link ? [{ name: 'kabel yuklamasi', color: seriesColor(0), points: sorted.map((r, i) => ({ x: xs[i], y: val(r) })) }]
        : flowIds.slice(0, 6).map((fid, k) => ({ name: sorted[0].flows[fid]?.label || fid, color: seriesColor(k), points: sorted.map((r, i) => ({ x: xs[i], y: (r.flows[fid]?.summary || {})[M.sum!] ?? null })) }))
      body = (
        <>
          <div className="flex flex-wrap items-center gap-2">{metricTabs}</div>
          <LineChart series={series} fmt={M.fmt} markers xTicks={xs} xFmt={(x) => String(x)} xMax={Math.max(...xs)}
            title={`${M.label} (o'rtacha) ↔ ${P.label} — ${FIELD_NAME[P.field] || P.field} · ${engineName(sorted[0].engine)}`} />
          {summaryTable((r) => String(r.study_value), P.label)}
        </>
      )
    }
  } else {
    const multi = data.length > 1
    const flowIds = [...new Set(data.flatMap((r) => Object.keys(r.flows || {})))]
    let series: Series[] = []
    let ctrl: React.ReactNode = null
    if (M.link) {
      const lids = [...new Set(data.flatMap((r) => Object.keys(r.links || {})))]
      const ls = lids.includes(linkSel) ? linkSel : lids[0] || ''
      ctrl = <Select className="w-56" value={ls} onChange={setLinkSel} options={lids.map((l) => { const L = project.links[l]; return { value: l, label: L ? `${l}: ${L.a.dev}↔${L.b.dev}` : l } })} />
      series = data.map((r, i) => ({ name: runName(r), color: seriesColor(i), points: (r.links?.[ls] || []).map((p) => ({ x: p.t, y: p.util })) }))
    } else if (multi) {
      const fs = flowIds.includes(flowSel) ? flowSel : flowIds[0]
      ctrl = <><span className="text-xs text-muted-foreground">Oqim:</span><Select className="w-56" value={fs} onChange={setFlowSel} options={flowIds.map((f) => ({ value: f, label: data.find((r) => r.flows[f])?.flows[f].label || f }))} /></>
      series = data.map((r, i) => ({ name: runName(r), color: seriesColor(i), points: (r.flows[fs]?.series || []).map((p) => ({ x: p.t, y: (p as any)[metric] })) }))
    } else {
      series = flowIds.map((fid, i) => ({ name: data[0].flows[fid].label || fid, color: seriesColor(i), points: data[0].flows[fid].series.map((p) => ({ x: p.t, y: (p as any)[metric] })) }))
    }
    const notes = [...new Set(data.flatMap((r) => r.notes || []))]
    const errs = data.filter((r) => r.status === 'error').map((r) => `${runName(r)}: ${r.error}`)
    body = (
      <>
        <div className="flex flex-wrap items-center gap-2">{metricTabs}{ctrl}</div>
        <LineChart series={series.slice(0, 6)} fmt={M.fmt} events={multi ? [] : data[0].events || []} xMax={Math.max(...data.map((r) => r.duration || 0))}
          title={M.label + (M.link ? '' : multi ? ' — solishtirish' : ' — barcha oqimlar')} />
        {summaryTable(runName, 'Ishga tushirish')}
        {(notes.length > 0 || errs.length > 0) && <div className="whitespace-pre-wrap text-[11px] leading-5 text-muted-foreground">{[...errs.map((e) => '⚠ ' + e), ...notes.map((n) => '• ' + n)].join('\n')}</div>}
      </>
    )
  }

  // ro'yxat: tadqiqotlar guruhlangan
  const studies = [...new Set(runs.filter((r) => r.study_id).map((r) => r.study_id!))]
  return (
    <WindowFrame win={win} icon={<BarChart3 />} title={'Natijalar — ' + pname} sub="ssenariy ishga tushirishlari: grafiklar, solishtirish, tadqiqotlar">
      <div className="grid min-h-0 flex-1 grid-cols-[250px_1fr]">
        <div className="flex min-h-0 flex-col gap-1.5 overflow-auto border-r p-3">
          <div className="px-1 text-[10.5px] font-semibold uppercase tracking-wider text-muted-foreground">Ishga tushirishlar</div>
          {!runs.length && <div className="px-1 text-xs text-muted-foreground">Hali yo'q — Ssenariy oynasida "Ishga tushirish".</div>}
          {studies.map((sid) => {
            const mem = runs.filter((r) => r.study_id === sid), r0 = mem[0]
            const twoD = mem.some((r) => r.study_param2)
            return (
              <button key={sid} onClick={() => setStudy(sid)} className={cn('flex flex-col gap-0.5 rounded-lg border px-2.5 py-2 text-left text-xs transition-colors hover:border-foreground/40',
                study === sid && 'border-foreground/60 bg-accent')}>
                <span className="font-semibold">Tadqiqot{twoD ? ' (2D)' : ''}: {r0.scenario_name}</span>
                <span className="text-[11px] text-muted-foreground">{mem.length} ta · {engineName(r0.engine)}{mem.some((r) => r.status === 'running') ? ' · ishlayapti…' : ''}</span>
              </button>
            )
          })}
          {runs.filter((r) => !r.study_id).map((r) => (
            <label key={r.run_id} className={cn('flex cursor-pointer items-start gap-2 rounded-lg border px-2.5 py-2 text-xs hover:border-foreground/40', r.status === 'error' && 'border-destructive/50')}>
              <Checkbox checked={selected.includes(r.run_id) && !study} onChange={(v) => { setStudy(null); setSelected((s) => v ? [...s.filter((x) => x !== r.run_id), r.run_id] : s.filter((x) => x !== r.run_id)) }} />
              <div className="min-w-0 flex-1">
                <div className="truncate font-semibold">{r.label || r.scenario_name}{r.status === 'error' && ' ⚠'}</div>
                <div className="text-[11px] text-muted-foreground">{engineName(r.engine)} · {r.status === 'running' ? Math.round((r.progress || 0) * 100) + '%…' : new Date(r.created * 1000).toLocaleTimeString()}{r.wall_s != null ? ` · ${r.wall_s}s` : ''}</div>
              </div>
              {r.status !== 'running' && <button title="O'chirish" className="text-muted-foreground hover:text-destructive" onClick={async (e) => {
                e.preventDefault(); if (!confirm("Natija o'chirilsinmi?")) return
                await api.del(`/api/runs/${encodeURIComponent(pname)}/${r.run_id}`); setSelected((s) => s.filter((x) => x !== r.run_id)); loadList()
              }}><X className="h-3.5 w-3.5" /></button>}
            </label>
          ))}
        </div>
        <div className="flex min-h-0 flex-col gap-3 overflow-auto p-3">
          <div className="flex items-center gap-2">
            {study && <Button size="sm" variant="ghost" onClick={() => setStudy(null)}><ArrowLeft /> vaqt bo'yicha</Button>}
            <div className="flex-1" />
            <Button size="sm" variant="outline" onClick={exportCsv} disabled={!data.length}><Download /> CSV</Button>
          </div>
          {body}
        </div>
      </div>
    </WindowFrame>
  )
}
