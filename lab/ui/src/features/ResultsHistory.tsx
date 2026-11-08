import { useState } from 'react'
import { BarChart3, ChevronDown, ChevronLeft, ChevronRight, ChevronUp, Loader2, Search, ShieldAlert, Square, Trash2 } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { engineName } from '@/lib/format'
import { isActive, runState, statusLabel } from '@/lib/results'
import type { RunListItem } from '@/lib/types'
import { cn } from '@/lib/utils'
import type { useRunHistory } from './useRunHistory'

export function ResultsHistory({history,visible,search,onSearch,selected,study,onStudy,onChoose,onAction,busy,onInvalid}:{
  history:ReturnType<typeof useRunHistory>;visible:RunListItem[];search:string;onSearch:(value:string)=>void;
  selected:string[];study:string|null;onStudy:(id:string)=>void;onChoose:(id:string,checked?:boolean,exclusive?:boolean)=>void;
  onAction:(id:string,kind:'cancel'|'delete')=>void;busy:boolean;onInvalid:()=>void;
}) {
  const [expanded,setExpanded]=useState(false)
  const studies=[...new Set(visible.map(run => run.study_id).filter((id):id is string => Boolean(id)))]
  return <aside className={cn('results-history flex min-h-0 flex-col border-r bg-muted/20',expanded && 'history-expanded')}>
    <div className="space-y-2.5 border-b p-3"><div className="flex items-center justify-between gap-2"><h2 className="text-xs font-semibold">Ishga tushirishlar</h2><Badge className="ml-auto">{history.total}</Badge><button className="results-history-toggle items-center gap-1 text-xs" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>{expanded ? 'Tarixni yopish' : 'Tarixni ochish'}{expanded ? <ChevronUp className="size-3.5" /> : <ChevronDown className="size-3.5" />}</button></div>
      <div className="results-history-filters relative"><Search className="pointer-events-none absolute left-2.5 top-2.5 size-3.5 text-muted-foreground" /><Input aria-label="Natijalarni qidirish" className="pl-8 text-xs" placeholder="Nom, ssenariy yoki ID…" value={search} onChange={event => onSearch(event.target.value)} /></div>
      <div className="results-history-filters grid grid-cols-2 gap-1.5"><Select ariaLabel="Dvigatel filtri" value={history.engine} onChange={history.setEngine} options={[{value:'',label:'Barcha dvigatellar'},{value:'analytic',label:'Analitik'},{value:'ns3',label:'ns-3'},{value:'mininet',label:'Mininet'}]} className="text-xs" />
        <Select ariaLabel="Holat filtri" value={history.status} onChange={history.setStatus} options={[{value:'',label:'Barcha holatlar'},...['done','running','queued','cancelling','error','cancelled','interrupted'].map(value => ({value,label:statusLabel(value)}))]} className="text-xs" /></div>
    </div>
    <div className="results-history-scroll flex min-h-0 flex-1 flex-col gap-2 overflow-auto p-3" aria-busy={history.loading}>
      {history.error && <div role="alert" className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-xs"><p>{history.error}</p><Button size="xs" variant="outline" className="mt-2" onClick={history.refresh}>Qayta urinish</Button></div>}
      {history.loading && !history.runs.length && <div role="status" className="flex items-center gap-2 p-3 text-xs text-muted-foreground"><Loader2 className="size-4 animate-spin" /> Yuklanmoqda…</div>}
      {!history.loading && !history.error && !visible.length && <div className="rounded-lg border border-dashed px-3 py-6 text-center text-xs text-muted-foreground"><BarChart3 className="mx-auto mb-2 size-6 opacity-40" />{search ? 'Bu sahifada mos natija topilmadi.' : history.engine || history.status ? 'Bu filtrlar bo‘yicha natija yo‘q.' : 'Hali natija yo‘q. Ssenariyni ishga tushiring.'}</div>}
      {studies.map(id => {const members=visible.filter(run => run.study_id === id),first=members[0];return <button key={id} data-study-id={id} onClick={() => {onStudy(id);setExpanded(false)}} className={cn('rounded-lg border bg-card p-3 text-left text-xs hover:border-foreground/30',study === id && 'border-primary bg-accent')}><div className="mb-1 flex items-center gap-2 font-medium"><BarChart3 className="size-3.5" />Parametrik tadqiqot</div><div className="truncate font-semibold">{first.scenario_name}</div><div className="mt-1 truncate text-[10px] text-muted-foreground">{first.study_param?.label || first.study_param?.field}{first.study_param2 ? " × "+(first.study_param2.label || first.study_param2.field) : ""}</div><div className="mt-1 text-[10px] text-muted-foreground">{members.length} ta bu sahifada · {engineName(first.engine)}</div></button>})}
      {visible.filter(run => !run.study_id).map(run => {const state=runState(run),checked=selected.includes(run.run_id) && !study;
        const date=new Date(run.created*1000),created=Number.isFinite(date.getTime()) ? date.toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}) : 'Sana noma’lum';
        return <div key={run.run_id} data-run-id={run.run_id} className={cn('rounded-lg border bg-card p-2.5 transition-colors hover:border-foreground/30',checked && 'border-primary/60 bg-accent/60')}>
          <div className="flex items-start gap-2"><Checkbox checked={checked} onChange={value => onChoose(run.run_id,value)} label={<span className="sr-only">Solishtirish: {run.label || run.scenario_name || run.run_id}</span>} /><button className="min-w-0 flex-1 text-left" onClick={() => {onChoose(run.run_id,true,true);setExpanded(false)}}><span className="block truncate text-xs font-semibold">{run.label || run.scenario_name || run.run_id}</span><span className="mt-0.5 block text-[10px] text-muted-foreground">{engineName(run.engine)} · {created}</span></button></div>
          <div className="mt-2 flex items-center justify-between gap-1"><Badge variant={state === 'done' ? 'success' : state === 'error' ? 'destructive' : isActive(state) ? 'warning' : 'outline'}>{statusLabel(state)}</Badge>
            {isActive(state) ? <Button aria-label={'Bekor qilish: '+(run.label || run.scenario_name)} size="icon" variant="ghost" className="size-6" disabled={busy || state === 'cancelling'} onClick={() => onAction(run.run_id,'cancel')}><Square className="size-3" /></Button> : <Button aria-label={'O‘chirish: '+(run.label || run.scenario_name)} size="icon" variant="ghost" className="size-6 text-muted-foreground hover:text-destructive" disabled={busy} onClick={() => onAction(run.run_id,'delete')}><Trash2 className="size-3.5" /></Button>}</div>
          {isActive(state) && <div className="mt-2 h-1 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-primary transition-all" style={{width:Math.min(100,Math.max(0,(run.progress || 0)*100))+'%'}} /></div>}
        </div>})}
    </div>
    <div className="results-history-footer space-y-2 border-t p-3 text-[10px] text-muted-foreground"><div className="flex items-center justify-between"><span>{history.total ? history.offset+1 : 0}–{Math.min(history.offset+history.runs.length,history.total)} / {history.total}</span><div className="flex gap-1"><Button aria-label="Oldingi natijalar" size="icon" variant="ghost" className="size-6" disabled={!history.page || history.loading} onClick={() => history.setPage(history.page-1)}><ChevronLeft /></Button><Button aria-label="Keyingi natijalar" size="icon" variant="ghost" className="size-6" disabled={history.offset+50 >= history.total || history.loading} onClick={() => history.setPage(history.page+1)}><ChevronRight /></Button></div></div>{search && <p>Qidiruv joriy sahifadagi 50 tagacha natijaga qo‘llanadi.</p>}<button className="inline-flex items-center gap-1.5 hover:text-foreground" onClick={onInvalid}><ShieldAlert className="size-3.5" />O‘qilmagan natijalar</button></div>
  </aside>
}
