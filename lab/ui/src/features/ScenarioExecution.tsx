import { useEffect, useRef, useState } from 'react'
import { CheckCircle2, Loader2, Play, Square } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Field, Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { Section } from '@/components/ui/table'
import { toast } from '@/components/ui/toast'
import { api } from '@/lib/api'
import { storage } from '@/lib/format'
import { validInputs } from '@/lib/forms'
import { statusLabel } from '@/lib/results'
import { FIELD_LABEL, PARAM_DEFAULTS, paramOptions, parseStudyValues, toParam } from '@/lib/scenario'
import { openWin } from '@/lib/store'
import type { Project, Scenario } from '@/lib/types'
import { LogBox } from './DeviceWindow'
import { type ScenarioJob, useScenarioJob } from './useScenarioJob'
import { useScenarioPreflight } from './useScenarioPreflight'

type Engine={id:string;label:string;available:boolean;hint:string}
type JobStatus=ReturnType<typeof useScenarioJob>
function JobDetails({project,job,jobState}:{project:string;job:ScenarioJob|null;jobState:JobStatus}) {
  const [cancelling,setCancelling]=useState(false),[error,setError]=useState('')
  const handled=useRef('')
  const currentJob=useRef(job?.id);currentJob.current=job?.id
  useEffect(()=>{
    if(job && jobState.state && !jobState.active && handled.current!==job.id) {
      handled.current=job.id
      if(jobState.state.status==='done' && (job.kind!=='study' || jobState.state.complete)) {
        toast('Natija tayyor','ok');openWin('results','main',{w:1060,h:700,arg:{[job.kind==='run'?'run':'study']:job.id}})
      }
    }
  },[job?.id,jobState.state,jobState.active])
  useEffect(()=>{setError('');setCancelling(false)},[job?.id])
  if(!job) return null
  const cancel=async()=>{
    setCancelling(true);setError('')
    try{await api.post(`/api/${job.kind==='run'?'runs':'studies'}/${encodeURIComponent(project)}/${encodeURIComponent(job.id)}/cancel`);jobState.refresh()}
    catch(failure){if(currentJob.current===job.id) setError(failure instanceof Error?failure.message:'Bekor qilinmadi')}
    finally{if(currentJob.current===job.id) setCancelling(false)}
  }
  const state=jobState.state,status=state?.status || 'queued'
  return <div className="space-y-2 rounded-lg border bg-background/30 p-3" data-job-kind={job.kind}>
    <div className="flex flex-wrap items-center gap-2 text-xs"><Badge variant={status==='done'?'success':['error','failed','interrupted','incomplete'].includes(status)?'destructive':jobState.active?'warning':'outline'}>{statusLabel(status)}</Badge><span className="min-w-0 flex-1 break-all font-mono text-[10px]">{job.id}</span>
      <Button size="xs" variant="outline" onClick={()=>openWin('results','main',{w:1060,h:700,arg:{[job.kind==='run'?'run':'study']:job.id}})}>Natijani ochish</Button>
      {jobState.active && <Button size="xs" variant="outline" disabled={cancelling} onClick={cancel}><Square />{cancelling?'Bekor qilinyapti…':'Bekor qilish'}</Button>}
    </div>
    <div role="progressbar" aria-label={job.kind==='run'?'Ish jarayoni':'Tadqiqot jarayoni'} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(jobState.progress*100)} className="h-1.5 overflow-hidden rounded bg-muted"><div className="h-full bg-primary transition-all" style={{width:jobState.progress*100+'%'}} /></div>
    {job.kind==='study' && state && <p className="text-xs text-muted-foreground">{state.observed_runs} / {state.planned_runs ?? '?'} variant · {state.complete?'Barcha variantlar muvaffaqiyatli':jobState.active?'Tadqiqot davom etmoqda':'Barcha variantlar muvaffaqiyatli yakunlanmagan'}</p>}
    {(error || jobState.error || state?.error) && <div role="alert" className="text-xs text-destructive">{error || jobState.error || state?.error}{jobState.error && <Button size="xs" variant="ghost" onClick={jobState.refresh}>Qayta urinish</Button>}</div>}
    {!!state?.runs?.some(row=>row.error) && <div role="alert" className="space-y-1 text-xs text-destructive">{state.runs.filter(row=>row.error).slice(0,10).map(row=><p key={row.run_id}>{row.run_id}: {row.error}</p>)}</div>}
    {jobState.logs && <LogBox text={jobState.logs} err={status==='error'} />}
  </div>
}

export function ScenarioExecution({project,name,sc,validControls}:{project:Project;name:string;sc:Scenario;validControls:()=>boolean}) {
  const [engines,setEngines]=useState<Engine[]>([]),[engine,setEngine]=useState(storage.get('netlab.engine','analytic'))
  const [engineError,setEngineError]=useState(''),[engineRevision,setEngineRevision]=useState(0)
  const [submitting,setSubmitting]=useState(false),[error,setError]=useState(''),[job,setJob]=useState<ScenarioJob|null>(null)
  const guard=useRef(false),mounted=useRef(true)
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[])
  const preflight=useScenarioPreflight(project,name,engine),jobState=useScenarioJob(project.name,job)
  useEffect(()=>{
    const controller=new AbortController();setEngineError('')
    api.get<{engines:Engine[]}>('/api/engines',controller.signal).then(result=>{
      if(controller.signal.aborted) return
      setEngines(result.engines);setEngine(value=>result.engines.some(item=>item.id===value && item.available)?value:result.engines.find(item=>item.available)?.id || 'analytic')
    }).catch(failure=>{if(!controller.signal.aborted) setEngineError(failure.message)})
    return()=>controller.abort()
  },[engineRevision])
  const selected=engines.find(item=>item.id===engine)
  const check=async()=>validControls() && await preflight.check()
  const start=async()=>{
    if(guard.current) return
    guard.current=true;setSubmitting(true);setError('')
    try{if(await check()) {const result=await api.post<{run_id:string}>('/api/runs',{project,scenario:name,engine});if(mounted.current) setJob({id:result.run_id,kind:'run'})}}
    catch(failure){if(mounted.current) setError(failure instanceof Error?failure.message:'Ishga tushirilmadi')}
    finally{guard.current=false;if(mounted.current) setSubmitting(false)}
  }
  return <>
    <Section title="Tekshirish va ishga tushirish"><div className="space-y-3 p-3">
      <div className="flex flex-wrap items-center gap-2"><Select ariaLabel="Simulyatsiya dvigateli" className="min-w-0 flex-1 basis-56" value={engine} disabled={submitting} options={engines.map(item=>({value:item.id,label:item.label+(item.available?'':' — mavjud emas'),disabled:!item.available}))} onChange={value=>{setEngine(value);storage.set('netlab.engine',value)}} />
        <Button size="sm" variant="outline" disabled={preflight.loading || !selected || submitting} onClick={check}>{preflight.loading?<Loader2 className="animate-spin" />:<CheckCircle2 />} Tekshirish</Button>
        <Button size="sm" disabled={submitting || jobState.active || !selected?.available} onClick={start}>{submitting?<Loader2 className="animate-spin" />:<Play />} Ishga tushirish</Button>
      </div>
      {engineError && <div role="alert" className="text-xs text-destructive">{engineError}<Button size="xs" variant="ghost" onClick={()=>setEngineRevision(value=>value+1)}>Qayta urinish</Button></div>}
      {selected && <p className="text-[11px] text-muted-foreground">{selected.hint}</p>}
      {(error || preflight.error) && <p role="alert" className="text-xs text-destructive">{error || preflight.error}</p>}
      {preflight.report && <div className="space-y-2 rounded-lg border p-3 text-xs" data-preflight>
        <Badge variant={preflight.report.ready?'success':'destructive'}>{preflight.report.ready?'Ishga tushirishga tayyor':'Ishga tushirishga tayyor emas'}</Badge>
        {!preflight.report.available && <p>{preflight.report.unavailable_reason || 'Dvigatel mavjud emas.'}</p>}
        {preflight.report.valid && <p className="text-muted-foreground">{preflight.report.counts.applications} ilova → {preflight.report.counts.expanded_applications} oqim · {preflight.report.counts.events} hodisa · namuna {String(preflight.report.effective.sample_s)} s</p>}
        {preflight.report.issues.slice(0,20).map(issue=><div key={issue.code} role={issue.severity==='error'?'alert':undefined} className={issue.severity==='error'?'text-destructive':'text-muted-foreground'}><p>{issue.message}{issue.count && issue.count>1?' ('+issue.count+')':''}</p>{Boolean(issue.paths?.length) && <p className="break-all font-mono text-[10px]">{issue.paths!.join(', ')}</p>}</div>)}
      </div>}
      <JobDetails project={project.name} job={job} jobState={jobState} />
    </div></Section>
    <ScenarioStudy project={project} name={name} sc={sc} engine={engine} ready={Boolean(selected?.available)} check={check} />
  </>
}

function ScenarioStudy({project,name,sc,engine,ready,check}:{project:Project;name:string;sc:Scenario;engine:string;ready:boolean;check:()=>Promise<boolean>}) {
  const options=paramOptions(project,sc,engine)
  const [p1,setP1]=useState(storage.get('netlab.studyParam',options[0]?.value || '')),[p2,setP2]=useState('')
  const [v1,setV1]=useState(PARAM_DEFAULTS[p1.split('|')[2]] || ''),[v2,setV2]=useState('')
  const [submitting,setSubmitting]=useState(false),[error,setError]=useState(''),[job,setJob]=useState<ScenarioJob|null>(null)
  const guard=useRef(false),fieldset=useRef<HTMLFieldSetElement>(null),mounted=useRef(true)
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[])
  const jobState=useScenarioJob(project.name,job)
  const valid1=options.some(option=>option.value===p1),valid2=!p2 || options.some(option=>option.value===p2) && p2!==p1
  useEffect(()=>{
    if(!valid1) {const next=options[0]?.value || '';setP1(next);setV1(PARAM_DEFAULTS[next.split('|')[2]] || '')}
    if(!valid2) {setP2('');setV2('')}
  },[valid1,valid2,options[0]?.value])
  let count=0,valuesError=''
  try{count=parseStudyValues(v1).length*(p2?parseStudyValues(v2).length:1)}catch(failure){valuesError=failure instanceof Error?failure.message:'Qiymatlar noto‘g‘ri'}
  const start=async()=>{
    if(guard.current || !validInputs(fieldset.current)) return
    guard.current=true;setSubmitting(true);setError('')
    try{if(await check()) {
      const result=await api.post<{study_id:string}>('/api/studies',{project,scenario:name,engine,param:toParam(p1),values:parseStudyValues(v1),...(p2?{param2:toParam(p2),values2:parseStudyValues(v2)}:{})})
      if(mounted.current) setJob({id:result.study_id,kind:'study'})
    }}catch(failure){if(mounted.current) setError(failure instanceof Error?failure.message:'Tadqiqot boshlanmadi')}
    finally{guard.current=false;if(mounted.current) setSubmitting(false)}
  }
  return <Section title="Parametrik tadqiqot"><div className="space-y-3 p-3">
    <fieldset ref={fieldset} disabled={submitting} className="scenario-field-grid grid min-w-0 grid-cols-2 gap-3">
      <Field label="1-parametr (x)"><Select ariaLabel="1-parametr" value={p1} options={options} onChange={value=>{setP1(value);storage.set('netlab.studyParam',value);setV1(PARAM_DEFAULTS[value.split('|')[2]] || '')}} /></Field>
      <Field label="1-parametr qiymatlari"><Input aria-label="1-parametr qiymatlari" required value={v1} placeholder="1, 2, 5" onChange={event=>setV1(event.target.value)} /></Field>
      <Field label="2-parametr (y, ixtiyoriy)"><Select ariaLabel="2-parametr" value={p2} options={[{value:'',label:'Ikkinchi parametr yo‘q'},...options.filter(option=>option.value!==p1)]} onChange={value=>{setP2(value);setV2(value?PARAM_DEFAULTS[value.split('|')[2]] || '':'')}} /></Field>
      <Field label="2-parametr qiymatlari"><Input aria-label="2-parametr qiymatlari" required={Boolean(p2)} disabled={!p2} value={v2} onChange={event=>setV2(event.target.value)} /></Field>
    </fieldset>
    <div className="flex flex-wrap items-center gap-2"><Button size="sm" variant="secondary" disabled={submitting || jobState.active || !ready || !valid1 || !valid2 || !count || count>49 || Boolean(valuesError)} onClick={start}>{submitting?<Loader2 className="animate-spin" />:<Play />} Tadqiqotni boshlash</Button><span className="text-xs text-muted-foreground">{count} variant · ko‘pi bilan 49</span></div>
    {(error || valuesError || count>49) && <p role="alert" className="text-xs text-destructive">{error || valuesError || '49 tadan ko‘p kombinatsiya tanlangan.'}</p>}
    <p className="text-[11px] text-muted-foreground">Qiymatlarni vergul bilan ajrating; birliklar va kodek nomlari saqlanadi. Bitta parametr — grafik, ikkita parametr — issiqlik xaritasi. {p1 && FIELD_LABEL[p1.split('|')[2]]}</p>
    <JobDetails project={project.name} job={job} jobState={jobState} />
  </div></Section>
}
