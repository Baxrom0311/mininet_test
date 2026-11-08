import { memo, useMemo, useState } from 'react'
import { Activity, ArrowDownToLine, Gauge, Radio, ShieldCheck } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { DataTable, Section } from '@/components/ui/table'
import { fmtBps, fmtMs, fmtPct } from '@/lib/format'
import { visibleFlows } from '@/lib/results'
import type { RunResult } from '@/lib/types'

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)
const number = (value: unknown, unit='') => finite(value) ? value.toLocaleString(undefined,{maximumFractionDigits:2})+unit : '—'
const extremum = (values: unknown[], kind:'min'|'max') => {
  const data = values.filter(finite)
  return data.length ? data.reduce((a,b) => kind === 'min' ? Math.min(a,b) : Math.max(a,b)) : null
}

export function ResultTable({columns,rows,empty='Bu natijada ma’lumot yo‘q.'}: {columns:string[];rows:React.ReactNode[][];empty?:string}) {
  const [page,setPage] = useState(0)
  const last = Math.max(0,Math.ceil(rows.length/50)-1), current = Math.min(page,last)
  return <><DataTable columns={columns} rows={rows.slice(current*50,current*50+50)} empty={empty} />
    {rows.length > 50 && <div className="flex items-center justify-between border-t px-3 py-2 text-xs text-muted-foreground">
      <span>{current*50+1}–{Math.min(rows.length,(current+1)*50)} / {rows.length}</span><div className="flex gap-1">
        <Button variant="ghost" size="xs" disabled={!current} onClick={() => setPage(current-1)}>Oldingi</Button>
        <Button variant="ghost" size="xs" disabled={current >= last} onClick={() => setPage(current+1)}>Keyingi</Button></div></div>}</>
}

export const ResultStats=memo(function ResultStats({data}:{data:RunResult[]}) {
  const flows = data.flatMap(run => visibleFlows(run).map(([,flow]) => flow))
  const throughput = extremum(flows.map(flow => flow.summary?.avg_thr_bps),'max')
  const loss = extremum(flows.map(flow => flow.summary?.loss_pct),'max')
  const availability = extremum(data.flatMap(run => Object.values(run.availability?.flows || {}).map(flow => flow.availability_pct)),'min')
  const cards = [
    {label:'Oqim natijalari',value:String(flows.length),icon:Radio},
    {label:'Eng tez oqim',value:throughput == null ? '—' : fmtBps(throughput),icon:Gauge},
    {label:'Eng katta yo‘qotish',value:fmtPct(loss),icon:Activity},
    {label:'Min. mavjudlik',value:fmtPct(availability),icon:ShieldCheck},
  ]
  return <div className="result-stat-grid grid grid-cols-2 gap-2">{cards.map(({label,value,icon:Icon}) =>
    <div key={label} className="min-w-0 rounded-lg border bg-card px-3 py-2.5"><div className="flex items-center justify-between gap-2 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">{label}<Icon className="size-3.5 shrink-0" /></div>
      <div className="mt-1.5 truncate font-mono text-lg font-semibold tracking-tight tabular-nums">{value}</div></div>)}</div>
})

export const ResultInsights=memo(function ResultInsights({data,tab}:{data:RunResult[];tab:'apps'|'network'|'events'}) {
  const multi = data.length > 1
  const prefix = (run:RunResult) => multi ? [run.label || run.scenario_name || run.run_id] : []
  const heads = multi ? ['Natija'] : []
  if (tab === 'apps') {
    const rows = data.flatMap(run => Object.entries(run.apps || {}).map(([id,app]) => {
      const details = app.type === 'voip' ? `MOS: ${number(app.avg_mos)} · min: ${number(app.min_mos)}${app.quality ? ' · '+app.quality : ''}`
        : app.type === 'http' ? `${number(app.pages_done)} / ${number(app.pages)} sahifa · o‘rtacha ${number(app.avg_page_s,' s')}`
          : app.type === 'ftp' ? `${app.completed ? 'Yakunlangan' : 'Yakunlanmagan'} · ${number(app.transfer_s,' s')} · ${finite(app.avg_rate_bps) ? fmtBps(app.avg_rate_bps) : '—'}`
            : `${number(app.reached)} / ${number(app.receivers)} qabul qiluvchi · yo‘qotish ${number(app.avg_loss_pct,'%')}`
      return [...prefix(run),id,app.type.toUpperCase(),details]
    }))
    return <Section title="Ilovalar sifati" right={<span className="text-[10px] font-normal text-muted-foreground">VoIP · HTTP · FTP · Multicast</span>}>
      <ResultTable columns={[...heads,'Ilova','Tur','Natija']} rows={rows} empty="VoIP, HTTP, FTP yoki multicast ilovasi mavjud emas." /></Section>
  }
  if (tab === 'network') {
    const availability = data.flatMap(run => Object.entries(run.availability?.flows || {}).map(([id,flow]) =>
      [...prefix(run),id,fmtPct(flow.availability_pct),number(flow.downtime_s,' s'),flow.outages?.length ?? 0]))
    const convergence = data.flatMap(run => (run.convergence || []).map(event =>
      [...prefix(run),number(event.t,' s'),event.action,event.link || '—',number(event.converged_s,' s'),(event.devices || []).join(', ')]))
    const links = data.flatMap(run => Object.entries(run.links || {}).map(([id,points]) => {
      let util:number|null=null,queue:number|null=null,drops=0,hasDrops=false
      for (const point of points || []) {
        if (finite(point.util)) util = util == null ? point.util : Math.max(util,point.util)
        for (const value of [point.queue_ab,point.queue_ba]) if (finite(value)) queue = queue == null ? value : Math.max(queue,value)
        for (const value of [point.drops_ab,point.drops_ba]) if (finite(value)) {drops+=value;hasDrops=true}
      }
      return [...prefix(run),id,fmtPct(util == null ? null : util*100),number(queue),hasDrops ? number(drops) : '—']
    }))
    const nodes = data.flatMap(run => Object.entries(run.nodes || {}).map(([id,points]) => {
      let routes:number|null=null,forwarding:number|null=null
      for (const point of points || []) {
        if (finite(point.routes)) routes=routes == null ? point.routes : Math.max(routes,point.routes)
        if (finite(point.fwd_pps)) forwarding=forwarding == null ? point.fwd_pps : Math.max(forwarding,point.fwd_pps)
      }
      return [...prefix(run),id,number(routes),number(forwarding)]
    }))
    return <><Section title="Mavjudlik"><ResultTable columns={[...heads,'Oqim','Mavjudlik','Uzilish','Uzilishlar']} rows={availability} /></Section>
      <Section title="Konvergensiya"><ResultTable columns={[...heads,'Vaqt','Hodisa','Kabel','Tiklanish','Qurilmalar']} rows={convergence} /></Section>
      <Section title="Kabel navbatlari va tashlangan paketlar" right={<ArrowDownToLine className="size-3.5 text-muted-foreground" />}><ResultTable columns={[...heads,'Kabel','Max. yuklama','Max. navbat, pkt','Tashlangan, pkt']} rows={links} /></Section>
      <Section title="Qurilmalar"><ResultTable columns={[...heads,'Qurilma','Max. marshrutlar','Max. tranzit, pkt/s']} rows={nodes} /></Section></>
  }
  const events = data.flatMap(run => (run.events || []).map(event => [...prefix(run),number(event.t,' s'),event.action,event.link || event.device || '—']))
  const routes = data.flatMap(run => (run.route_events || []).map(event => [...prefix(run),number(event.t,' s'),event.dev,(event.added || []).join(', ') || '—',(event.removed || []).join(', ') || '—']))
  return <><Section title="Ssenariy hodisalari"><ResultTable columns={[...heads,'Vaqt','Amal','Nishon']} rows={events} /></Section>
    <Section title="Marshrut o‘zgarishlari"><ResultTable columns={[...heads,'Vaqt','Qurilma','Qo‘shilgan','Olib tashlangan']} rows={routes} /></Section></>
})

export const ResultSummary=memo(function ResultSummary({data,getKey,heading}:{data:RunResult[];getKey:(run:RunResult)=>string;heading:string}) {
  const rows=useMemo(() => data.flatMap(run => visibleFlows(run).map(([id,flow]) => {
    const summary=flow.summary || {}
    return [getKey(run),flow.label || id,fmtBps(summary.avg_thr_bps),fmtMs(summary.avg_delay_ms),fmtMs(summary.avg_rtt_ms),fmtMs(summary.avg_jitter_ms),fmtPct(summary.loss_pct)]
  })),[data,getKey])
  return <Section title="Xulosa (o‘rtacha qiymatlar)"><ResultTable columns={[heading,'Oqim',"O'tkazuvchanlik",'Kechikish','RTT','Jitter',"Yo'qotish"]} rows={rows} /></Section>
})
