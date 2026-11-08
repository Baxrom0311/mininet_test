import { memo, useMemo } from 'react'
import { LineChart, type Series } from '@/components/charts'
import { Select } from '@/components/ui/select'
import { engineName, seriesColor } from '@/lib/format'
import { visibleFlows } from '@/lib/results'
import { useStore } from '@/lib/store'
import type { RunResult } from '@/lib/types'

export const RunChart=memo(function RunChart({data,metric,flow,link,onFlow,onLink}:{
  data:RunResult[];metric:{id:string;label:string;link?:boolean;fmt:(value:number)=>string};
  flow:string;link:string;onFlow:(value:string)=>void;onLink:(value:string)=>void;
}) {
  const theme=useStore(state => state.theme)
  const multi=data.length > 1
  const flows=useMemo(() => [...new Set(data.flatMap(run => visibleFlows(run).map(([id]) => id)))],[data])
  const links=useMemo(() => [...new Set(data.flatMap(run => Object.keys(run.links || {})))],[data])
  const chosenFlow=flows.includes(flow) ? flow : multi ? flows[0] || '' : ''
  const chosenLink=links.includes(link) ? link : links[0] || ''
  const series=useMemo<Series[]>(() => {
    const title=(run:RunResult) => (run.label || run.scenario_name || run.run_id)+' · '+engineName(run.engine)
    if (metric.link) return data.slice(0,6).map((run,index) => ({name:title(run),color:seriesColor(index),points:(run.links?.[chosenLink] || []).map(point => ({x:point.t,y:point.util}))}))
    if (multi) return data.slice(0,6).map((run,index) => ({name:title(run),color:seriesColor(index),points:(run.flows?.[chosenFlow]?.series || []).map(point => ({x:point.t,y:(point as unknown as Record<string,number|null>)[metric.id]}))}))
    const ids=chosenFlow ? [chosenFlow] : flows.slice(0,6)
    return ids.map((id,index) => ({name:data[0]?.flows?.[id]?.label || id,color:seriesColor(index),points:(data[0]?.flows?.[id]?.series || []).map(point => ({x:point.t,y:(point as unknown as Record<string,number|null>)[metric.id]}))}))
  },[data,metric,multi,chosenFlow,chosenLink,flows,theme])
  const note=multi && data.length > 6 ? `Grafikda dastlabki 6 / ${data.length} natija. Jadval va eksportda barcha tanlangan natijalar bor.`
    : !multi && !metric.link && !chosenFlow && flows.length > 6 ? `Dastlabki 6 / ${flows.length} oqim. Boshqa oqimni yuqoridan tanlang.` : undefined
  return <>
    <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground"><span>{metric.link ? 'Kabel:' : 'Oqim:'}</span>
      {metric.link ? <Select ariaLabel="Grafik kabeli" className="w-64" value={chosenLink} onChange={onLink} options={links.map(id => ({value:id,label:id}))} />
        : <Select ariaLabel="Grafik oqimi" className="w-64" value={chosenFlow} onChange={onFlow} options={[...(!multi ? [{value:'',label:'Barcha oqimlar (6 tagacha)'}] : []),...flows.map(id => ({value:id,label:data.find(run => run.flows?.[id])?.flows?.[id]?.label || id}))]} />}
    </div>
    <LineChart series={series} fmt={metric.fmt} events={multi ? [] : data[0]?.events || []} xMax={data.reduce((max,run) => Math.max(max,Number.isFinite(run.duration) ? run.duration : 0),0)} note={note} title={metric.label+(multi ? ' — solishtirish' : '')} />
  </>
})
