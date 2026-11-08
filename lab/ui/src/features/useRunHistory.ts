import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import { on } from '@/lib/bus'
import { isActive, runState } from '@/lib/results'
import type { RunPage } from '@/lib/types'

export function useRunHistory(project: string) {
  const [page,setPage] = useState(0)
  const [engine,setEngine] = useState('')
  const [status,setStatus] = useState('')
  const [revision,setRevision] = useState(0)
  const [version,setVersion] = useState(0)
  const [data,setData] = useState<RunPage>({runs:[],total:0,limit:50,offset:0})
  const [loading,setLoading] = useState(true)
  const [error,setError] = useState('')
  const refresh = () => setRevision(value => value+1)
  useEffect(() => {
    let debounce: ReturnType<typeof setTimeout>
    const off = on('ev:run', ev => {
      if (ev.project === project && (ev.done || ev.progress === 0)) {
        clearTimeout(debounce); debounce = setTimeout(refresh, 200)
      }
    })
    return () => { off(); clearTimeout(debounce) }
  },[project])
  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    setLoading(true); setError('')
    async function read() {
      let delay = 5000
      try {
        const query = new URLSearchParams({limit:'50',offset:String(page*50)})
        if (engine) query.set('engine',engine)
        if (status) query.set('status',status)
        const result = await api.get<RunPage>(`/api/runs/${encodeURIComponent(project)}?${query}`,controller.signal)
        if (controller.signal.aborted) return
        if (page && result.total && page*50 >= result.total) { setPage(Math.floor((result.total-1)/50)); return }
        if (page && !result.total) { setPage(0); return }
        setData(result); setVersion(value => value+1); setError('')
        delay = result.runs.some(run => isActive(runState(run))) ? 3000 : 15000
      } catch (failure) {
        if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : 'Natijalarni yuklab bo‘lmadi')
      } finally {
        if (!controller.signal.aborted) { setLoading(false); timer = setTimeout(read,delay) }
      }
    }
    read()
    return () => { controller.abort(); clearTimeout(timer) }
  },[project,page,engine,status,revision])
  return { ...data, page, setPage, engine, status, loading, error, refresh, version,
    setEngine:(value:string) => {setPage(0);setEngine(value)}, setStatus:(value:string) => {setPage(0);setStatus(value)} }
}
