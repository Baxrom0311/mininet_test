import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '@/lib/api'
import type { Project } from '@/lib/types'

export interface Preflight {ready:boolean;valid:boolean;available:boolean;compatible:boolean;unavailable_reason?:string;issues:{code:string;severity:string;message:string;count?:number;paths?:string[]}[];counts:Record<string,number>;effective:Record<string,unknown>}
export function useScenarioPreflight(project:Project,name:string,engine:string) {
  const scope=useMemo(()=>({project,name,engine}),[project,name,engine]),current=useRef(scope)
  current.current=scope
  const [entry,setEntry]=useState<{scope:typeof scope;report?:Preflight;error?:string}|null>(null)
  const [pending,setPending]=useState<typeof scope|null>(null)
  const request=useRef<{scope:typeof scope;controller:AbortController}|null>(null)
  useEffect(()=>()=>{if(request.current?.scope===scope) request.current.controller.abort()},[scope])
  async function check() {
    request.current?.controller.abort()
    const controller=new AbortController();request.current={scope,controller}
    setPending(scope);setEntry(null)
    try {
      const report=await api.post<Preflight>('/api/scenario/preflight',{project,scenario:name,engine},controller.signal)
      if(controller.signal.aborted || current.current!==scope) return false
      setEntry({scope,report});return report.ready
    } catch(failure) {if(!controller.signal.aborted && current.current===scope) setEntry({scope,error:failure instanceof Error?failure.message:'Tekshiruv bajarilmadi'});return false}
    finally {setPending(value=>value===scope?null:value)}
  }
  return {check,loading:pending===scope,report:entry?.scope===scope?entry.report:undefined,error:entry?.scope===scope?entry.error:undefined}
}
