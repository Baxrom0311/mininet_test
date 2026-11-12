import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, BarChart3, Download, FileText, Loader2, RefreshCw, Square, X } from 'lucide-react'
import { Heatmap, LineChart, type Series } from '@/components/charts'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Select } from '@/components/ui/select'
import { Section } from '@/components/ui/table'
import { Segmented } from '@/components/ui/tabs'
import { toast } from '@/components/ui/toast'
import { api } from '@/lib/api'
import { engineName, fmtBps, fmtMs, fmtPct, seriesColor, storage } from '@/lib/format'
import { downloadCsv, filterRuns, isActive, resultsCsv, runState, statusLabel, visibleFlows } from '@/lib/results'
import { useStore, type Win } from '@/lib/store'
import type { InvalidResults, RunResult } from '@/lib/types'
import { cn } from '@/lib/utils'
import { ResultInsights, ResultStats, ResultSummary, ResultTable as DataTable } from './ResultInsights'
import { useRunHistory } from './useRunHistory'
import { useRunSelection } from './useRunSelection'
import { ResultsHistory } from './ResultsHistory'
import { RunChart } from './RunChart'
import { WindowFrame } from './WindowFrame'

type MetricId = 'thr_bps' | 'delay_ms' | 'rtt_ms' | 'jitter_ms' | 'loss_pct' | 'util'
const METRICS: { id: MetricId; label: string; fmt: (v: number) => string; sum?: string; link?: boolean }[] = [
  { id: 'thr_bps', label: "O'tkazuvchanlik", fmt: fmtBps, sum: 'avg_thr_bps' },
  { id: 'delay_ms', label: 'Kechikish', fmt: fmtMs, sum: 'avg_delay_ms' },
  { id: 'rtt_ms', label: 'RTT', fmt: fmtMs, sum: 'avg_rtt_ms' },
  { id: 'jitter_ms', label: 'Jitter', fmt: fmtMs, sum: 'avg_jitter_ms' },
  { id: 'loss_pct', label: "Yo'qotish", fmt: fmtPct, sum: 'loss_pct' },
  { id: 'util', label: 'Kabel yuklamasi', fmt: v => fmtPct(v*100), link: true },
]
const FIELD_NAME: Record<string,string> = {channel:'AP kanali',tx_power:'AP quvvati, dBm',bw:'kabel tezligi, Mbit/s',delay:'kechikish, ms',loss:'loss, %',queue:'navbat, paket',rate:'ilova tezligi',size:'paket, B'}
const runName = (run:RunResult) => (run.label || run.scenario_name || run.run_id)+' · '+engineName(run.engine)
function StateBadge({status}:{status:string}) {
  return <Badge variant={status === 'done' ? 'success' : ['error','failed'].includes(status) ? 'destructive' : isActive(status) ? 'warning' : 'outline'}>{statusLabel(status)}</Badge>
}
export function ResultsWindow({win}:{win?:Win}) {
  const defaultWin: Win = { id: 'results:main', kind: 'results', ref: 'main', z: 1, x: 0, y: 0, w: 1000, h: 700 }
  const activeWin = win || defaultWin
  const project=useStore(state => state.project.name)
  const scoped = activeWin.arg?.project === project ? activeWin : {...activeWin,arg:undefined}
  return <ResultsWorkspace key={project} win={scoped} embedded={!win} />
}
function ResultsWorkspace({win, embedded}:{win:Win; embedded?: boolean}) {
  const project=useStore(state => state.project), pname=project.name
  const history=useRunHistory(pname), {runs}=history
  const [selected,setSelected]=useState<string[]>(win.arg?.run ? [win.arg.run] : [])
  const [study,setStudy]=useState<string|null>(win.arg?.study || null)
  const [epoch,setEpoch]=useState(0)
  const [search,setSearch]=useState('')
  const [view,setView]=useState<'charts'|'apps'|'network'|'events'>('charts')
  const [metric,setMetric]=useState<MetricId>(storage.get('netlab.metric','thr_bps'))
  const [flowSel,setFlowSel]=useState(''), [linkSel,setLinkSel]=useState('')
  const [action,setAction]=useState(''), [actionError,setActionError]=useState('')
  const [showInvalid,setShowInvalid]=useState(false), [invalidPage,setInvalidPage]=useState(0)
  const [invalid,setInvalid]=useState<InvalidResults|null>(null), [invalidError,setInvalidError]=useState('')
  const auto=useRef(Boolean(win.arg))
  const detail=useRunSelection(pname,selected,study,runs,epoch)
  const {data,studyRuns,studyState}=detail
  const root='/api/runs/'+encodeURIComponent(pname)
  const visible=useMemo(() => filterRuns(runs,search),[runs,search])
  const M=METRICS.find(item => item.id === metric) || METRICS[0]
  const validAxes=studyRuns.every(run => Number.isInteger(run.study_index) && run.study_index! >= 0 && run.study_index! < 49 &&
    (!run.study_param2 || Number.isInteger(run.study_index2) && run.study_index2! >= 0 && run.study_index2! < 49)) &&
    new Set(studyRuns.map(run => `${run.study_index}:${run.study_index2 ?? ''}`)).size === studyRuns.length
  useEffect(() => {
    if (win.arg?.run) {auto.current=true;setStudy(null);setSelected([win.arg.run])}
    if (win.arg?.study) {auto.current=true;setStudy(win.arg.study)}
  },[win.arg])
  useEffect(() => {
    if (!auto.current && runs.length) {
      auto.current=true
      const first=runs.find(run => run.status === 'done' && !run.study_id) || runs.find(run => !run.study_id)
      if (first) setSelected([first.run_id])
    }
  },[runs])
  useEffect(() => {
    if (!showInvalid) return
    const controller=new AbortController()
    setInvalidError('')
    api.get<InvalidResults>(`/api/results/${encodeURIComponent(pname)}/invalid?limit=20&offset=${invalidPage*20}`,controller.signal)
      .then(setInvalid).catch(error => {if (!controller.signal.aborted) setInvalidError(error.message)})
    return () => controller.abort()
  },[showInvalid,invalidPage,pname,epoch])
  useStore(state => state.theme)
  const refresh=() => {history.refresh();setEpoch(value => value+1)}
  const choose=(id:string,checked=true,exclusive=false) => {
    auto.current=true;setStudy(null);setFlowSel('');setActionError('')
    setSelected(current => {
      if (!checked) return current.filter(value => value !== id)
      if (exclusive) return [id]
      if (current.includes(id)) return current
      if (current.length >= 20) {toast('Solishtirish uchun ko‘pi bilan 20 ta natija tanlang.','warn');return current}
      return [...current,id]
    })
  }
  const perform=async (id:string,kind:'cancel'|'delete'|'study') => {
    if (kind === 'delete' && !confirm('Natija o‘chirilsinmi?')) return
    setAction(id);setActionError('')
    try {
      if (kind === 'delete') {await api.del(`${root}/${encodeURIComponent(id)}`);setSelected(current => current.filter(value => value !== id))}
      else await api.post(kind === 'study' ? `/api/studies/${encodeURIComponent(pname)}/${encodeURIComponent(id)}/cancel` : `${root}/${encodeURIComponent(id)}/cancel`)
      refresh()
    } catch(error) {setActionError(error instanceof Error ? error.message : 'Amal bajarilmadi')}
    finally {setAction('')}
  }
  const exportCsv=() => downloadCsv(resultsCsv(data),`${pname}_natijalar.csv`)
  const exportComparison=async () => {
    setAction('export');setActionError('')
    try {
      const response=await fetch('/api/reports/compare',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({project:pname,run_ids:selected})})
      if (!response.ok) {const error=await response.json();throw new Error(error.error || error.detail || 'Hisobot tayyorlanmadi')}
      const url=URL.createObjectURL(await response.blob()), anchor=document.createElement('a')
      anchor.href=url;anchor.download=pname+'_solishtirish.html';anchor.click();setTimeout(() => URL.revokeObjectURL(url),1000)
    } catch(error) {setActionError(error instanceof Error ? error.message : 'Eksport bajarilmadi')}
    finally {setAction('')}
  }
  const exportLatex=async () => {
    setAction('export-latex');setActionError('')
    try {
      let content = ''
      const targetIds = selected.length ? selected : data.map(r => r.run_id)
      if (data.length === 1 && !study) {
        try {
          const res = await fetch(`${root}/${encodeURIComponent(data[0].run_id)}/report.tex`)
          if (res.ok) content = await res.text()
        } catch {}
      }
      if (!content) {
        try {
          const res = await fetch('/api/reports/latex', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ project: pname, run_ids: targetIds })
          })
          if (res.ok) content = await res.text()
        } catch {}
      }
      if (!content) {
        try {
          const res = await fetch(`/api/telemetry/l4/report/latex?title=${encodeURIComponent(pname + ' Hisobot')}`)
          if (res.ok) content = await res.text()
        } catch {}
      }
      if (!content) {
        content = generateLatexDocument(pname, data)
      }
      const blob = new Blob([content], { type: 'application/x-latex;charset=utf-8' })
      const url = URL.createObjectURL(blob), anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `${pname}_hisobot.tex`
      anchor.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (error) {
      setActionError(error instanceof Error ? error.message : 'LaTeX hisoboti yuklanmadi')
    } finally {
      setAction('')
    }
  }
  const summaryTable=(key:(run:RunResult)=>string,keyHead:string) => <ResultSummary data={data} getKey={key} heading={keyHead} />
  const metricTabs = <Segmented className="h-auto flex-wrap [&_button]:whitespace-nowrap" value={metric} onChange={(v) => { setMetric(v); storage.set('netlab.metric', v) }} items={METRICS.map((m) => ({ value: m.id, label: m.label }))} />

  let body: React.ReactNode
  if (study && !validAxes) body = <div role="alert" className="rounded-lg border border-warning/40 p-4 text-xs text-warning">Variant koordinatalari noto‘g‘ri; grafik hisoblanmadi. To‘liq holat yuqorida ko‘rsatilgan.</div>
  else if (!data.length) body = <div className="p-8 text-center text-xs text-muted-foreground">{study ? 'Tadqiqot natijalari hali tayyor emas…' : 'Chapdan bir yoki bir nechta natijani tanlang.'}</div>
  else if (study) {
    const P = data[0].study_param || {}, P2 = data[0].study_param2
    const flowIds = [...new Set(data.flatMap((r) => visibleFlows(r).map(([id]) => id)))]
    const fsel = flowIds.includes(flowSel) ? flowSel : flowIds[0]
    const val = (r?: RunResult): number | null => {
      if (!r) return null
      if (M.link) { const lid = P.kind === 'link' ? P.link : Object.keys(r.links || {})[0]; const pts = r.links?.[lid] || []; return pts.length ? pts.reduce((a, p) => a + p.util, 0) / pts.length : null }
      return (r.flows?.[fsel]?.summary || {})[M.sum!] ?? null
    }
    if (P2) {
      const xs = [...new Set(studyRuns.map((r) => r.study_index!))].sort((a, b) => a - b), ys = [...new Set(studyRuns.map((r) => r.study_index2!))].sort((a, b) => a - b)
      const find = (i: number, j: number) => data.find((r) => r.study_index === xs[i] && r.study_index2 === ys[j])
      body = (
        <>
          <div className="flex flex-wrap items-center gap-2">{metricTabs}{!M.link && <><span className="text-xs text-muted-foreground">Oqim:</span>
            <Select className="w-56" value={fsel} onChange={setFlowSel} options={flowIds.map((f) => ({ value: f, label: data[0].flows?.[f]?.label || f }))} /></>}</div>
          <Heatmap xs={xs.map((i) => studyRuns.find((r) => r.study_index === i)!.study_value)} ys={ys.map((j) => studyRuns.find((r) => r.study_index2 === j)!.study_value2)}
            value={(i, j) => val(find(i, j))} fmt={M.fmt} xLabel={`${P.label} (${FIELD_NAME[P.field] || P.field})`} yLabel={`${P2.label} (${FIELD_NAME[P2.field] || P2.field})`}
            title={`${M.label} (o'rtacha)${M.link ? '' : ' — ' + (data[0].flows?.[fsel]?.label || fsel)} · ${engineName(data[0].engine)}`} />
          {summaryTable((r) => `${r.study_value} × ${r.study_value2}`, `${P.label} × ${P2.label}`)}
        </>
      )
    } else {
      const sorted = [...studyRuns].sort((a, b) => (a.study_index || 0) - (b.study_index || 0))
      const xs = sorted.map((_,i) => i)
      const series: Series[] = M.link ? [{ name: 'kabel yuklamasi', color: seriesColor(0), points: sorted.map((r, i) => ({ x: xs[i], y: val(data.find(item => item.run_id === r.run_id)) })) }]
        : flowIds.slice(0, 6).map((fid, k) => ({ name: data[0].flows?.[fid]?.label || fid, color: seriesColor(k), points: sorted.map((r, i) => ({ x: xs[i], y: (data.find(item => item.run_id === r.run_id)?.flows?.[fid]?.summary || {})[M.sum!] ?? null })) }))
      body = (
        <>
          <div className="flex flex-wrap items-center gap-2">{metricTabs}</div>
          <LineChart series={series} fmt={M.fmt} markers xTicks={xs} xFmt={(x) => String(sorted[x]?.study_value ?? x)} xMax={Math.max(...xs)}
            title={`${M.label} (o'rtacha) ↔ ${P.label} — ${FIELD_NAME[P.field] || P.field} · ${engineName(sorted[0].engine)}`} />
          {summaryTable((r) => String(r.study_value), P.label)}
        </>
      )
    }
  } else {
    const notes=[...new Set(data.flatMap(run => run.notes || []))]
    const errors=data.filter(run => run.status === 'error').map(run => `${runName(run)}: ${run.error}`)
    body=<><div className="flex flex-wrap items-center gap-2">{metricTabs}</div>
      <RunChart data={data} metric={M} flow={flowSel} link={linkSel} onFlow={setFlowSel} onLink={setLinkSel} />
      {summaryTable(runName,'Ishga tushirish')}
      {(notes.length > 0 || errors.length > 0) && <div className="whitespace-pre-wrap text-[11px] leading-5 text-muted-foreground">{[...errors.map(error => '⚠ '+error),...notes.map(note => '• '+note)].join('\n')}</div>}
    </>
  }

  const workspaceContent = (
    <div className="results-workspace flex min-h-0 flex-1 flex-col"><div className="results-layout grid min-h-0 flex-1">
      <ResultsHistory history={history} visible={visible} search={search} onSearch={setSearch} selected={selected} study={study} onStudy={id => {setStudy(id);setFlowSel('');setActionError('')}} onChoose={choose} onAction={perform} busy={Boolean(action)} onInvalid={() => setShowInvalid(value => !value)} />
      <div className="flex min-h-0 min-w-0 flex-col">
        <div className="flex shrink-0 flex-wrap items-center gap-2 border-b bg-card/40 px-4 py-2.5">
          {study ? <Button size="xs" variant="ghost" onClick={() => setStudy(null)}><ArrowLeft /> Natijalarga</Button> : <div className="flex items-center gap-2 text-xs"><span className="font-semibold">{selected.length ? selected.length+' ta tanlandi' : 'Tahlil'}</span>{selected.length > 0 && <button aria-label="Tanlovni tozalash" className="text-muted-foreground hover:text-foreground" onClick={() => {auto.current=true;setSelected([])}}><X className="size-3.5" /></button>}</div>}
          <div className="flex-1" /><Button size="xs" variant="ghost" onClick={refresh} aria-label="Natijalarni yangilash"><RefreshCw className={cn('size-3.5',history.loading && 'animate-spin')} /> Yangilash</Button>
          {study ? <Button size="xs" variant="outline" asChild><a href={`/api/studies/${encodeURIComponent(pname)}/${encodeURIComponent(study)}/report.html`} target="_blank" rel="noreferrer"><FileText /> HTML</a></Button> : data.length === 1 ? <><Button size="xs" variant="outline" asChild><a href={`${root}/${encodeURIComponent(data[0].run_id)}/report.html`} target="_blank" rel="noreferrer"><FileText /> HTML</a></Button><Button size="xs" variant="outline" asChild><a href={`${root}/${encodeURIComponent(data[0].run_id)}/export.zip`}><Download /> ZIP</a></Button></> : data.length > 1 ? <Button size="xs" variant="outline" disabled={Boolean(action) || data.length !== selected.length} onClick={exportComparison}><FileText /> HTML</Button> : null}
          <Button size="xs" variant="outline" onClick={exportLatex} disabled={!data.length || detail.loading.length > 0 || Boolean(action)} aria-label="LaTeX Hisobot (.tex)" title="LaTeX Hisobot (.tex)"><FileText className="size-3.5" /> LaTeX Hisobot (.tex)</Button>
          <Button size="xs" variant="outline" onClick={exportCsv} disabled={!data.length || detail.loading.length > 0}><Download /> CSV</Button>
        </div>
        <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-auto p-4">
          {actionError && <div role="alert" className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-xs">{actionError}</div>}
          {showInvalid && <Section title="O‘qilmagan natijalar" right={<Button size="xs" variant="ghost" disabled={Boolean(action)} onClick={async () => {setAction('reindex');try {await api.post(root+'/reindex');setInvalidPage(0);refresh()} catch(error) {setActionError(error instanceof Error ? error.message : 'Qayta tekshirilmadi')} finally {setAction('')}}}><RefreshCw /> Qayta tekshirish</Button>}>
            {invalidError ? <p role="alert" className="p-3 text-xs text-destructive">{invalidError}</p> : invalid ? <><DataTable columns={['Run ID','Fayl, bayt','Sabab']} rows={invalid.files.map(file => [file.run_id,file.size,file.error])} empty="Barcha natijalar o‘qildi. Buzilgan fayl topilmadi." />{invalid.total > 20 && <div className="flex items-center justify-between border-t p-2 text-xs"><span>{invalidPage*20+1}–{Math.min((invalidPage+1)*20,invalid.total)} / {invalid.total}</span><div className="flex gap-1"><Button size="xs" variant="ghost" disabled={!invalidPage} onClick={() => setInvalidPage(value => value-1)}>Oldingi</Button><Button size="xs" variant="ghost" disabled={(invalidPage+1)*20 >= invalid.total} onClick={() => setInvalidPage(value => value+1)}>Keyingi</Button></div></div>}</> : <p className="p-3 text-xs text-muted-foreground">Tekshirilmoqda…</p>}
          </Section>}
          {study && <Section title="Tadqiqot holati" right={studyState && <StateBadge status={studyState.status} />}>
            {detail.studyError ? <p role="alert" className="p-3 text-xs text-destructive">{detail.studyError}</p> : studyState ? <><div className="flex flex-wrap items-center justify-between gap-2 p-3 text-xs text-muted-foreground"><span>{studyState.observed_runs} / {studyState.planned_runs ?? '?'} variant · {studyState.complete ? 'Barcha variantlar tayyor' : studyState.settled ? 'Variantlar yakunlandi; barchasi muvaffaqiyatli emas' : 'Tadqiqot hali to‘liq emas'}</span>{isActive(studyState.status) && <Button size="xs" variant="outline" disabled={Boolean(action)} onClick={() => perform(study,'study')}><Square /> Tadqiqotni bekor qilish</Button>}</div><DataTable columns={['Run ID','Qiymat','Holat','Xato']} rows={studyState.runs.map(run => [run.run_id,String(run.study_value ?? '')+(run.study_value2 != null ? ' × '+String(run.study_value2) : ''),<StateBadge status={run.status} />,run.error || '—'])} />{!!studyState.missing_run_ids.length && <p className="p-3 text-xs text-warning">Yetishmayotgan variantlar: {studyState.missing_run_ids.join(', ')}</p>}</> : <p className="p-3 text-xs text-muted-foreground">Variantlar yuklanmoqda…</p>}
          </Section>}
          {!study && selected.length > 0 && <div className="flex flex-wrap gap-2">{selected.map(id => {const run=runs.find(item => item.run_id === id) || data.find(item => item.run_id === id);return <div key={id} className="flex max-w-full items-center gap-2 rounded-md bg-muted/60 px-2.5 py-1.5 text-[10px]"><span className="max-w-48 truncate" title={id}>{run?.label || run?.scenario_name || id}</span>{run && <StateBadge status={runState(run)} />}{detail.loading.includes(id) && <Loader2 className="size-3 animate-spin" />}</div>})}</div>}
          {Object.entries(detail.errors).map(([id,error]) => <div key={id} role="alert" className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-xs"><b>{id}</b><p className="mt-1">{error}</p><Button size="xs" variant="outline" className="mt-2" onClick={refresh}>Qayta urinish</Button></div>)}
          {detail.loading.length > 0 && <div role="status" className="flex items-center gap-2 text-xs text-muted-foreground"><Loader2 className="size-3.5 animate-spin" /> {detail.loading.length} ta natija yuklanmoqda…</div>}
          {!!data.length && <><ResultStats data={data} /><div className="sticky top-0 z-10 flex shrink-0 gap-1 overflow-auto border-b bg-background/95" role="tablist" aria-label="Natija bo‘limlari">{([{id:'charts',label:'Grafiklar'},{id:'apps',label:'Ilovalar'},{id:'network',label:'Tarmoq sifati'},{id:'events',label:'Hodisalar'}] as const).map(tab => <button key={tab.id} role="tab" tabIndex={view === tab.id ? 0 : -1} onKeyDown={event => {
                const ids=['charts','apps','network','events'] as const
                const current=ids.indexOf(view)
                const next=event.key === 'ArrowRight' ? (current+1)%4 : event.key === 'ArrowLeft' ? (current+3)%4 : event.key === 'Home' ? 0 : event.key === 'End' ? 3 : -1
                if (next >= 0) {event.preventDefault();setView(ids[next]);document.getElementById('result-tab-'+win.id+'-'+ids[next])?.focus()}
              }} aria-selected={view === tab.id} aria-controls={'result-'+win.id+'-'+tab.id} id={'result-tab-'+win.id+'-'+tab.id} onClick={() => setView(tab.id)} className={cn('whitespace-nowrap border-b-2 border-transparent px-3 py-2 text-xs font-medium text-muted-foreground',view === tab.id && 'border-primary text-foreground')}>{tab.label}</button>)}</div></>}
          <div id={'result-'+win.id+'-'+view} role={data.length ? "tabpanel" : undefined} aria-labelledby={data.length ? 'result-tab-'+win.id+'-'+view : undefined} className="flex flex-col gap-3">{view === 'charts' || !data.length ? body : <ResultInsights data={data} tab={view} />}</div>
        </div>
      </div>
    </div></div>
  )

  if (embedded) {
    return (
      <div className="glass flex h-full min-h-0 flex-1 flex-col overflow-hidden rounded-xl shadow-lg">
        <div className="flex items-center gap-2.5 border-b border-border/50 px-4 py-2.5 bg-card/25 shrink-0">
          <BarChart3 className="h-5 w-5 text-amber-400" />
          <div>
            <div className="text-xs font-semibold text-foreground">Tahlil va Natijalar — {pname}</div>
            <div className="text-[10.5px] text-muted-foreground">Grafiklar, ilovalar sifati, SLA monitoring va LaTeX hisobotlar</div>
          </div>
        </div>
        <div className="flex-1 min-h-0 overflow-hidden">{workspaceContent}</div>
      </div>
    )
  }

  return (
    <WindowFrame win={win} icon={<BarChart3 />} title={'Natijalar — '+pname} sub="Grafiklar, ilovalar sifati va tarmoq hodisalari">
      {workspaceContent}
    </WindowFrame>
  )
}

function generateLatexDocument(pname: string, runs: RunResult[]): string {
  const dateStr = new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })
  const escapeLatex = (str: unknown) => String(str ?? '').replace(/([&%$#_{}~^\\<>])/g, (c) => {
    const map: Record<string, string> = { '&': '\\&', '%': '\\%', '$': '\\$', '#': '\\#', '_': '\\_', '{': '\\{', '}': '\\}', '~': '\\textasciitilde{}', '^': '\\textasciicircum{}', '\\': '\\textbackslash{}', '<': '$<$', '>': '$>$' }
    return map[c] || c
  })

  let runsSection = ''
  for (const r of runs) {
    const flows = Object.entries(r.flows || {}).filter(([, f]) => !f.hidden)
    let flowsTable = ''
    if (flows.length) {
      flowsTable = `\\begin{table}[htbp]
\\caption{Flow Performance Metrics for Run: ${escapeLatex(r.run_id)}}
\\centering
\\begin{tabular}{lrrrr}
\\toprule
\\textbf{Flow} & \\textbf{Throughput} & \\textbf{Delay (ms)} & \\textbf{Jitter (ms)} & \\textbf{Loss (\\%)} \\\\
\\midrule
${flows.map(([id, f]) => {
  const sm = f.summary || {}
  const thr = sm.avg_thr_bps != null ? (sm.avg_thr_bps / 1e6).toFixed(2) + ' Mbps' : '--'
  const del = sm.avg_delay_ms != null ? sm.avg_delay_ms.toFixed(2) : '--'
  const jit = sm.avg_jitter_ms != null ? sm.avg_jitter_ms.toFixed(2) : '--'
  const loss = sm.loss_pct != null ? sm.loss_pct.toFixed(2) : '0.00'
  return `${escapeLatex(f.label || id)} & ${thr} & ${del} & ${jit} & ${loss} \\\\`
}).join('\n')}
\\bottomrule
\\end{tabular}
\\end{table}`
    }

    runsSection += `\\subsection{Evaluation of Run: ${escapeLatex(r.label || r.scenario_name || r.run_id)}}
Scenario: \\textbf{${escapeLatex(r.scenario_name)}}, Engine: \\textbf{${escapeLatex(r.engine)}}, Duration: ${r.duration} s.

${flowsTable}
`
  }

  return `\\documentclass[conference]{IEEEtran}
\\usepackage{cite}
\\usepackage{amsmath,amssymb,amsfonts}
\\usepackage{booktabs}
\\usepackage{tabularx}
\\usepackage{xcolor}
\\usepackage{hyperref}

\\begin{document}

\\title{NetLab Telemetry and Workload Performance Report: ${escapeLatex(pname)}}

\\author{\\IEEEauthorblockN{High Performance Networking Laboratory}
\\IEEEauthorblockA{\\textit{NetLab Emulation and Research Platform} \\\\
Tashkent, Uzbekistan \\\\
Generated: ${dateStr}}}

\\maketitle

\\begin{abstract}
This report provides IEEE-formatted empirical evaluation results for project ${escapeLatex(pname)}, capturing L4 transport telemetry, OPNET-grade application performance (Adaptive Video, HTTP/3, VoIP, FTP), and SLA compliance.
\\end{abstract}

\\begin{IEEEkeywords}
Network Emulation, SDN, Transport Telemetry, SLA Compliance, QoE, ITU-T P.1203.
\\end{IEEEkeywords}

\\section{Experimental Summary}
Total Evaluated Runs: ${runs.length}. Emulation platform provides reproducible network measurements with real kernel data-plane metrics.

${runsSection}

\\section{Conclusion}
The platform demonstrates deterministic telemetry evaluation suitable for production SLA validation and scientific research.

\\end{document}
`
}
