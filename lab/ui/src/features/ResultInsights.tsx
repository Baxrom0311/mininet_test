import { memo, useMemo, useState } from 'react'
import { Activity, ArrowDownToLine, Gauge, Radio, ShieldCheck } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { DataTable, Section } from '@/components/ui/table'
import { fmtBps, fmtMs, fmtPct } from '@/lib/format'
import { visibleFlows } from '@/lib/results'
import type { RunResult } from '@/lib/types'
import { cn } from '@/lib/utils'

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

interface SlaViolationRow {
  t?: number | string
  flow: string
  metric: string
  measured: string
  threshold: string
  severity: 'critical' | 'warning'
}

function getSlaReport(run: RunResult) {
  const slaData = run.sla || (run as any).sla_results || (run as any).sla_summary
  if (slaData && typeof slaData.compliance_pct === 'number') {
    const rawViolations = slaData.violations || slaData.recent_violations || []
    const violations: SlaViolationRow[] = rawViolations.map((v: any) => ({
      t: v.timestamp ?? v.t,
      flow: v.stream_id || v.flow || 'Barchasi',
      metric: v.metric || v.rule_name || 'Metrika',
      measured: typeof v.measured_value === 'number'
        ? (String(v.metric).includes('bps') ? fmtBps(v.measured_value) : String(v.metric).includes('pct') ? fmtPct(v.measured_value) : number(v.measured_value))
        : String(v.measured_value ?? '—'),
      threshold: typeof v.threshold_value === 'number'
        ? (String(v.metric).includes('bps') ? fmtBps(v.threshold_value) : String(v.metric).includes('pct') ? fmtPct(v.threshold_value) : number(v.threshold_value))
        : String(v.threshold_value ?? '—'),
      severity: v.severity === 'critical' ? 'critical' : 'warning',
    }))
    return {
      compliance_pct: slaData.compliance_pct,
      total_violations: slaData.total_violations ?? violations.length,
      violations,
      rule_name: slaData.rule_name || 'Standart SLA',
    }
  }

  // Fallback: evaluate SLA from run flows
  const violations: SlaViolationRow[] = []
  let totalEvaluated = 0
  let passCount = 0

  for (const [fid, flow] of Object.entries(run.flows || {})) {
    if (flow.hidden) continue
    const sm = flow.summary || {}
    totalEvaluated += 4

    // 1. Latency <= 50ms
    if (sm.avg_delay_ms != null) {
      if (sm.avg_delay_ms > 50) {
        violations.push({
          t: '—',
          flow: flow.label || fid,
          metric: 'Kechikish (Delay)',
          measured: fmtMs(sm.avg_delay_ms),
          threshold: '≤ 50.0 ms',
          severity: sm.avg_delay_ms > 100 ? 'critical' : 'warning',
        })
      } else passCount++
    } else passCount++

    // 2. Jitter <= 10ms
    if (sm.avg_jitter_ms != null) {
      if (sm.avg_jitter_ms > 10) {
        violations.push({
          t: '—',
          flow: flow.label || fid,
          metric: 'Jitter',
          measured: fmtMs(sm.avg_jitter_ms),
          threshold: '≤ 10.0 ms',
          severity: sm.avg_jitter_ms > 20 ? 'critical' : 'warning',
        })
      } else passCount++
    } else passCount++

    // 3. Loss <= 1%
    if (sm.loss_pct != null) {
      if (sm.loss_pct > 1.0) {
        violations.push({
          t: '—',
          flow: flow.label || fid,
          metric: 'Paket yo‘qotish (Loss)',
          measured: fmtPct(sm.loss_pct),
          threshold: '≤ 1.0%',
          severity: sm.loss_pct > 3.0 ? 'critical' : 'warning',
        })
      } else passCount++
    } else passCount++

    // 4. Throughput
    if (sm.avg_thr_bps != null && sm.avg_thr_bps > 0) {
      if (sm.avg_thr_bps < 1_000_000 && flow.type !== 'ping') {
        violations.push({
          t: '—',
          flow: flow.label || fid,
          metric: 'O‘tkazuvchanlik',
          measured: fmtBps(sm.avg_thr_bps),
          threshold: '≥ 1.0 Mbps',
          severity: 'warning',
        })
      } else passCount++
    } else passCount++
  }

  const compliance_pct = totalEvaluated > 0 ? Math.round((passCount / totalEvaluated) * 1000) / 10 : 100
  return {
    compliance_pct,
    total_violations: violations.length,
    violations,
    rule_name: 'Standart Korporativ SLA',
  }
}

export const ResultInsights=memo(function ResultInsights({data,tab}:{data:RunResult[];tab:'apps'|'network'|'events'}) {
  const multi = data.length > 1
  const prefix = (run:RunResult) => multi ? [run.label || run.scenario_name || run.run_id] : []
  const heads = multi ? ['Natija'] : []

  const slaReports = data.map(getSlaReport)
  const avgCompliance = slaReports.length ? slaReports.reduce((acc, r) => acc + r.compliance_pct, 0) / slaReports.length : 100
  const totalViolations = slaReports.reduce((acc, r) => acc + r.total_violations, 0)

  const slaCards = (
    <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
      <div className="min-w-0 rounded-lg border bg-card px-3 py-2.5">
        <div className="flex items-center justify-between gap-2 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
          SLA Muvofiqlik
          <ShieldCheck className={cn("size-3.5 shrink-0", avgCompliance >= 95 ? "text-success" : avgCompliance >= 80 ? "text-warning" : "text-destructive")} />
        </div>
        <div className={cn("mt-1.5 truncate font-mono text-lg font-semibold tracking-tight tabular-nums",
          avgCompliance >= 95 ? "text-success" : avgCompliance >= 80 ? "text-warning" : "text-destructive")}>
          {avgCompliance.toFixed(1)}%
        </div>
      </div>
      <div className="min-w-0 rounded-lg border bg-card px-3 py-2.5">
        <div className="flex items-center justify-between gap-2 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
          Jami Buzilishlar
          <Activity className="size-3.5 shrink-0 text-muted-foreground" />
        </div>
        <div className={cn("mt-1.5 truncate font-mono text-lg font-semibold tracking-tight tabular-nums",
          totalViolations > 0 ? "text-warning" : "text-foreground")}>
          {totalViolations} ta
        </div>
      </div>
      <div className="col-span-2 sm:col-span-1 min-w-0 rounded-lg border bg-card px-3 py-2.5">
        <div className="flex items-center justify-between gap-2 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
          SLA Qoidasi
          <Gauge className="size-3.5 shrink-0 text-muted-foreground" />
        </div>
        <div className="mt-1.5 truncate font-mono text-xs font-semibold tracking-tight text-foreground">
          Kechikish ≤ 50ms · Yo‘qotish ≤ 1%
        </div>
      </div>
    </div>
  )

  const slaRows = data.flatMap(run => {
    const { violations } = getSlaReport(run)
    return violations.map((v, i) => [
      ...prefix(run),
      number(v.t, ' s'),
      v.flow,
      v.metric,
      v.measured,
      v.threshold,
      <Badge key={i} variant={v.severity === 'critical' ? 'destructive' : 'warning'}>
        {v.severity === 'critical' ? 'Kritik' : 'Ogohlantirish'}
      </Badge>,
    ])
  })

  const slaSection = (
    <Section title="SLA Monitoring (Xizmat darajasi nazorati)" right={<Badge variant={avgCompliance >= 95 ? 'success' : avgCompliance >= 80 ? 'warning' : 'destructive'}>{avgCompliance.toFixed(1)}% muvofiqlik</Badge>}>
      {slaCards}
      <div className="mt-2">
        <ResultTable columns={[...heads, 'Vaqt', 'Oqim', 'Metrika', 'O‘lchangan', 'Chegara', 'Daraja']} rows={slaRows} empty="SLA qoidalari buzilishi aniqlanmadi (100% muvofiqlik)." />
      </div>
    </Section>
  )

  if (tab === 'apps') {
    const rows = data.flatMap(run => Object.entries(run.apps || {}).map(([id,app]) => {
      let details = ''
      if (app.type === 'voip') {
        details = `MOS: ${number(app.avg_mos)} · min: ${number(app.min_mos)}${app.quality ? ' · '+app.quality : ''}`
      } else if (app.type === 'video') {
        const qoe = app.qoe_score ?? app.p1203_score ?? app.qoe ?? app.mos
        const bitrate = app.avg_bitrate_kbps ? app.avg_bitrate_kbps * 1000 : (app.avg_bitrate_bps ?? app.avg_bitrate ?? app.avg_rate_bps ?? app.bitrate_bps)
        const stalls = app.stalls_count ?? app.stalls ?? app.stall_count ?? 0
        const rebuffer = app.stall_duration_s ?? app.rebuffering_duration ?? app.total_stall_sec ?? app.rebuffer_s ?? 0
        details = `QoE: ${number(qoe)} / 5.0 · ${finite(bitrate) ? fmtBps(bitrate) : '—'} · Stalls: ${stalls} ta · Rebuffering: ${number(rebuffer, ' s')}${app.resolution ? ' · ' + app.resolution : ''}`
      } else if (app.type === 'http3') {
        const done = app.pages_done ?? app.requests_done ?? app.streams_done
        const total = app.pages ?? app.requests ?? app.streams
        const avgTime = app.avg_page_s ?? app.avg_rtt_ms ?? app.avg_time_s
        details = `QUIC/0-RTT · ${finite(done) && finite(total) ? `${number(done)} / ${number(total)} yuklandi · ` : ''}o‘rtacha ${number(avgTime, ' s')}${app.streams ? ` · ${app.streams} oqim` : ''}${app.zero_rtt ? ' · 0-RTT' : ''}`
      } else if (app.type === 'http') {
        details = `${number(app.pages_done)} / ${number(app.pages)} sahifa · o‘rtacha ${number(app.avg_page_s,' s')}`
      } else if (app.type === 'ftp') {
        details = `${app.completed ? 'Yakunlangan' : 'Yakunlanmagan'} · ${number(app.transfer_s,' s')} · ${finite(app.avg_rate_bps) ? fmtBps(app.avg_rate_bps) : '—'}`
      } else {
        details = `${number(app.reached)} / ${number(app.receivers)} qabul qiluvchi · yo‘qotish ${number(app.avg_loss_pct,'%')}`
      }
      return [...prefix(run),id,app.type.toUpperCase(),details]
    }))
    return <>
      <Section title="Ilovalar sifati" right={<span className="text-[10px] font-normal text-muted-foreground">VoIP · Video · HTTP/3 · HTTP · FTP · Multicast</span>}>
        <ResultTable columns={[...heads,'Ilova','Tur','Natija']} rows={rows} empty="VoIP, Video, HTTP/3, HTTP, FTP yoki multicast ilovasi mavjud emas." />
      </Section>
      {slaSection}
    </>
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
    return <>
      {slaSection}
      <Section title="Mavjudlik"><ResultTable columns={[...heads,'Oqim','Mavjudlik','Uzilish','Uzilishlar']} rows={availability} /></Section>
      <Section title="Konvergensiya"><ResultTable columns={[...heads,'Vaqt','Hodisa','Kabel','Tiklanish','Qurilmalar']} rows={convergence} /></Section>
      <Section title="Kabel navbatlari va tashlangan paketlar" right={<ArrowDownToLine className="size-3.5 text-muted-foreground" />}><ResultTable columns={[...heads,'Kabel','Max. yuklama','Max. navbat, pkt','Tashlangan, pkt']} rows={links} /></Section>
      <Section title="Qurilmalar"><ResultTable columns={[...heads,'Qurilma','Max. marshrutlar','Max. tranzit, pkt/s']} rows={nodes} /></Section>
    </>
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
