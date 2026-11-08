import { useEffect, useMemo, useState } from 'react'
import { api } from '@/lib/api'
import { isActive } from '@/lib/results'
import type { RunListItem, RunPage, RunResult, StudyState } from '@/lib/types'

export function useRunSelection(project:string, selected:string[], study:string|null, history:RunListItem[], epoch:number) {
  const [studyRuns,setStudyRuns] = useState<RunListItem[]>([])
  const [studyState,setStudyState] = useState<StudyState|null>(null)
  const [studyError,setStudyError] = useState('')
  const [cache,setCache] = useState<Record<string,{epoch:number;record:RunResult}>>({})
  const [errors,setErrors] = useState<Record<string,string>>({})
  const [loading,setLoading] = useState<string[]>([])
  const [tick,setTick] = useState(0)
  useEffect(() => {
    setStudyRuns([]); setStudyState(null); setStudyError('')
    if (!study) return
    const controller = new AbortController()
    let timer:ReturnType<typeof setTimeout>
    async function read() {
      let delay=5000
      try {
        const root = encodeURIComponent(project), id = encodeURIComponent(study!)
        const [rows,state] = await Promise.all([
          api.get<RunPage>(`/api/runs/${root}?study_id=${id}`,controller.signal),
          api.get<StudyState>(`/api/studies/${root}/${id}`,controller.signal),
        ])
        if (controller.signal.aborted) return
        setStudyRuns(rows.runs); setStudyState(state); setStudyError('')
        delay = isActive(state.status) ? 3000 : 15000
      } catch (failure) {
        if (!controller.signal.aborted) setStudyError(failure instanceof Error ? failure.message : 'Tadqiqotni yuklab bo‘lmadi')
      } finally { if (!controller.signal.aborted) timer=setTimeout(read,delay) }
    }
    read()
    return () => {controller.abort();clearTimeout(timer)}
  },[project,study,epoch])
  const need = useMemo(() => study ? studyRuns.filter(run => run.status === 'done').map(run => run.run_id) : selected,[study,studyRuns,selected])
  const key=need.join(',')
  const states=(study ? studyRuns : history).filter(run => need.includes(run.run_id)).map(run => `${run.run_id}:${run.status}:${run.label}`).join('|')
  useEffect(() => {
    const controller=new AbortController()
    let timer:ReturnType<typeof setTimeout>
    const rows=study ? studyRuns : history
    const ids=need.filter(id => {
      const item=cache[id], row=rows.find(value => value.run_id === id)
      return !item || item.epoch !== epoch || isActive(item.record.status) || Boolean(row &&
        ((row.queue_state || row.status) !== item.record.status || row.label !== item.record.label))
    })
    setCache(current => Object.fromEntries(Object.entries(current).filter(([id]) => need.includes(id))))
    setLoading(ids); setErrors({})
    let index=0, active=false
    async function worker() {
      while (index < ids.length && !controller.signal.aborted) {
        const id=ids[index++]
        try {
          const record=await api.get<RunResult>(`/api/runs/${encodeURIComponent(project)}/${encodeURIComponent(id)}`,controller.signal)
          if (controller.signal.aborted) return
          active ||= isActive(record.status)
          setCache(current => ({...current,[id]:{epoch,record}}))
        } catch (failure) {
          if (!controller.signal.aborted) setErrors(current => ({...current,[id]:failure instanceof Error ? failure.message : 'Natijani yuklab bo‘lmadi'}))
        } finally {if (!controller.signal.aborted) setLoading(current => current.filter(value => value !== id))}
      }
    }
    Promise.all(Array.from({length:Math.min(3,ids.length)},worker)).then(() => {
      if (active && !controller.signal.aborted) timer=setTimeout(() => setTick(value => value+1),3000)
    })
    return () => {controller.abort();clearTimeout(timer)}
    // Epoch, identity and status changes govern fetches; cache writes do not refetch terminal archives.
  },[project,key,states,epoch,tick])
  const data=useMemo(() => need.map(id => cache[id]).filter(item => item?.epoch === epoch).map(item => item.record)
    .sort((a,b) => (a.study_index2 ?? 0)-(b.study_index2 ?? 0) || (a.study_index ?? 0)-(b.study_index ?? 0)),[key,cache,epoch])
  return {data,errors,loading,studyRuns,studyState,studyError}
}
