import { useEffect, useRef } from 'react'
import { LineChart } from 'lucide-react'
import { validInputs } from '@/lib/forms'
import { mutate, useStore, type Win } from '@/lib/store'
import type { Scenario } from '@/lib/types'
import { ScenarioApplications } from './ScenarioApplications'
import { ScenarioEvents } from './ScenarioEvents'
import { ScenarioExecution } from './ScenarioExecution'
import { ScenarioFailures } from './ScenarioFailures'
import { ScenarioSettings } from './ScenarioSettings'
import { WindowFrame } from './WindowFrame'

export function ScenarioWindow({win}:{win:Win}) {
  const project=useStore(state=>state.project.name)
  return <ScenarioEditor key={project+':'+win.ref} win={win} />
}
function ScenarioEditor({win}:{win:Win}) {
  const project=useStore(state=>state.project),sc=project.scenarios[win.ref]
  const controls=useRef<HTMLDivElement>(null)
  const sections=useRef<Record<string,HTMLDivElement|null>>({})
  const focused=useStore(state=>state.zTop===win.z)
  useEffect(()=>{
    if(!focused) return
    useStore.setState({overlay:(sc?.events || []).filter(event=>event.action==='move')})
    return()=>useStore.setState({overlay:[]})
  },[sc?.events,focused])
  if(!sc) return null
  const update=(fn:(scenario:Scenario)=>void)=>mutate(draft=>{if(draft.name===project.name && draft.scenarios[win.ref]) fn(draft.scenarios[win.ref])})
  const validControls=()=>validInputs(controls.current)
  return <WindowFrame win={win} icon={<LineChart />} title={'Ssenariy: '+win.ref} sub="Ilovalar, nosozliklar va tajriba sozlamalari">
    <div className="scenario-workspace flex min-h-0 flex-1 flex-col gap-3 overflow-auto p-3">
      <nav aria-label="Ssenariy bo‘limlari" className="sticky top-0 z-10 flex shrink-0 gap-1 overflow-auto rounded-lg border bg-background/95 p-1 text-xs shadow-sm">
        {Object.entries({settings:'Sozlamalar',apps:'Ilovalar · '+sc.apps.length,failures:'Nosozliklar',events:'Hodisalar · '+sc.events.length,execution:'Bajarish'}).map(([key,label])=><button key={key} className="shrink-0 rounded-md px-2.5 py-1.5 text-muted-foreground hover:bg-accent hover:text-foreground" onClick={()=>sections.current[key]?.scrollIntoView({block:'start'})}>{label}</button>)}
      </nav>
      <div ref={controls} className="flex flex-col gap-3">
        <div ref={element=>{sections.current.settings=element}} className="scroll-mt-12"><ScenarioSettings sc={sc} update={update} /></div>
        <div ref={element=>{sections.current.apps=element}} className="scroll-mt-12"><ScenarioApplications project={project} sc={sc} update={update} /></div>
        <div ref={element=>{sections.current.failures=element}} className="scroll-mt-12"><ScenarioFailures project={project} sc={sc} update={update} /></div>
        <div ref={element=>{sections.current.events=element}} className="scroll-mt-12"><ScenarioEvents project={project} sc={sc} update={update} /></div>
      </div>
      <div ref={element=>{sections.current.execution=element}} className="scroll-mt-12"><ScenarioExecution project={project} name={win.ref} sc={sc} validControls={validControls} /></div>
    </div>
  </WindowFrame>
}
