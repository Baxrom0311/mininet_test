import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import { on } from '@/lib/bus'
import { isActive } from '@/lib/results'

export interface ScenarioJob {id:string;kind:'run'|'study'}
export interface JobState {status:string;progress?:number;complete?:boolean;error?:string;planned_runs?:number|null;observed_runs?:number;runs?:{run_id:string;status:string;error?:string}[];orphaned_run_ids?:string[]}
export function jobActive(state:JobState) {
  return isActive(state.status) || Boolean(state.runs?.some(row=>isActive(row.status) && !state.orphaned_run_ids?.includes(row.run_id)))
}
interface Snapshot {key:string;state:JobState|null;error:string;logs:string;stream:number}
export function useScenarioJob(project:string,job:ScenarioJob|null) {
  const key=project+'|'+job?.kind+'|'+job?.id
  const empty:Snapshot={key,state:null,error:'',logs:'',stream:0}
  const [saved,setSaved]=useState<Snapshot>(empty),[revision,setRevision]=useState(0)
  const current=saved.key===key?saved:empty
  const write=(fn:(value:Snapshot)=>Snapshot)=>setSaved(value=>fn(value.key===key?value:empty))
  const refresh=()=>setRevision(value=>value+1)
  useEffect(()=>{
    if(!job) return
    return on(job.kind==='run'?'ev:run':'ev:study',event=>{
      if((job.kind==='run'?event.run_id:event.study_id)!==job.id || event.project && event.project!==project) return
      if(event.log) write(value=>({...value,logs:(value.logs+event.log+'\n').slice(-20000)}))
      if(Number.isFinite(event.progress)) write(value=>{
        const stream=Math.max(value.stream,Math.min(1,Math.max(0,event.progress)))
        return stream===value.stream?value:{...value,stream}
      })
      if(event.done) refresh()
    })
  },[key])
  useEffect(()=>{
    if(!job) return
    const controller=new AbortController()
    let timer:ReturnType<typeof setTimeout>
    async function read() {
      let again=true,delay=1000
      try {
        const result=await api.get<JobState>(`/api/${job!.kind==='run'?'runs':'studies'}/${encodeURIComponent(project)}/${encodeURIComponent(job!.id)}`,controller.signal)
        if(controller.signal.aborted) return
        // Keep status only; completed archives may contain large series.
        const state:JobState={status:result.status,progress:result.progress,error:result.error,complete:result.complete,planned_runs:result.planned_runs,observed_runs:result.observed_runs,runs:result.runs,orphaned_run_ids:result.orphaned_run_ids}
        write(value=>({...value,state,error:''}));again=jobActive(result)
      } catch(failure) {if(!controller.signal.aborted) write(value=>({...value,error:failure instanceof Error?failure.message:'Holat olinmadi'}));delay=5000}
      finally {if(again && !controller.signal.aborted) timer=setTimeout(read,delay)}
    }
    read()
    return ()=>{controller.abort();clearTimeout(timer)}
  },[key,revision])
  const {state,error,logs,stream}=current
  const active=Boolean(job && (!state || jobActive(state)))
  const progress=state && !jobActive(state)?1:Math.min(1,Math.max(stream,Number.isFinite(state?.progress)?state!.progress!:0))
  return {state,error,logs,active,progress,refresh}
}
