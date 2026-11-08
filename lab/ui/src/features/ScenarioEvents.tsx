import { MapPin, Plus, Trash2 } from 'lucide-react'
import { NumberInput } from '@/components/NumberInput'
import { Button } from '@/components/ui/button'
import { Field, Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { Section } from '@/components/ui/table'
import { pickPoint } from '@/lib/store'
import type { Project, Scenario, ScenarioEvent } from '@/lib/types'

export function ScenarioEvents({project,sc,update}:{project:Project;sc:Scenario;update:(fn:(scenario:Scenario)=>void)=>void}) {
  const devices=Object.keys(project.devices).sort(),links=Object.keys(project.links)
  const patch=(index:number,values:Partial<ScenarioEvent>)=>update(s=>{s.events[index]={...s.events[index],...values}})
  const change=(event:ScenarioEvent,action:string):ScenarioEvent=>action==='move'?
    {t:event.t,action:'move',device:event.device || devices[0],x:(project.devices[event.device || devices[0]]?.x || 0)+100,y:project.devices[event.device || devices[0]]?.y || 0,duration:10}:
    {t:event.t,action:action as ScenarioEvent['action'],link:event.link || links[0],...(action==='link_params'?{params:event.params || {}}:{})}
  return <Section title="Vaqtli hodisalar" right={<Button size="xs" variant="outline" disabled={!links.length && !devices.length} onClick={()=>update(s=>s.events.push(change({t:Math.round(s.duration/3),action:'link_down'},links.length?'link_down':'move')))}><Plus /> Hodisa qo‘shish</Button>}>
    {!sc.events.length?<p className="p-3 text-xs text-muted-foreground">Kabelni uzish/tiklash, parametrlarini almashtirish yoki qurilmani harakatlantirish uchun hodisa qo‘shing.</p>:<div className="space-y-2 p-3">{sc.events.map((event,index)=><div key={index} className="rounded-lg border p-3" data-event-index={index}>
      <div className="mb-3 flex items-center justify-between text-xs font-medium"><span>Hodisa {index+1}</span><Button aria-label={'Hodisani o‘chirish: '+(index+1)} size="icon" variant="ghost" className="size-7" onClick={()=>update(s=>s.events.splice(index,1))}><Trash2 className="size-3.5" /></Button></div>
      <div className="scenario-field-grid grid grid-cols-2 gap-3">
        <Field label="Vaqt (s)"><NumberInput aria-label="Hodisa vaqti (s)" required min={0} max={sc.duration} step="any" value={event.t} onChange={value=>{if(value!=null) patch(index,{t:value})}} /></Field>
        <Field label="Amal"><Select ariaLabel="Hodisa amali" value={event.action} options={[{value:'link_down',label:'Kabel uzilishi',disabled:!links.length},{value:'link_up',label:'Kabel tiklanishi',disabled:!links.length},{value:'link_params',label:'Kabel parametrlari',disabled:!links.length},{value:'move',label:'Qurilma harakati',disabled:!devices.length}]} onChange={value=>update(s=>{s.events[index]=change(event,value)})} /></Field>
        <Field label={event.action==='move'?'Qurilma':'Kabel'}><Select ariaLabel="Hodisa nishoni" value={event.action==='move'?event.device:event.link} options={event.action==='move'?devices.map(id=>({value:id,label:id})):links.map(id=>({value:id,label:`${id}: ${project.links[id].a.dev}↔${project.links[id].b.dev}`}))} onChange={value=>patch(index,event.action==='move'?{device:value}:{link:value})} /></Field>
        {event.action==='move' && <><Field label="Harakat davomiyligi (s)"><NumberInput aria-label="Harakat davomiyligi (s)" min={0} step="any" value={event.duration} placeholder="0" onChange={value=>patch(index,{duration:value})} /></Field>
          {(['x','y'] as const).map(key=><Field key={key} label={key+' (px)'}><NumberInput aria-label={key+' (px)'} required step="any" value={event[key]} onChange={value=>{if(value!=null) patch(index,{[key]:value})}} /></Field>)}
          <Button size="sm" variant="outline" onClick={()=>pickPoint((x,y)=>patch(index,{x:Math.round(x),y:Math.round(y)}))}><MapPin /> Kanvasdan tanlash</Button></>}
        {event.action==='link_params' && <>{(['bw','delay','loss','queue'] as const).map(key=>{
          const label={bw:'Tezlik (Mbit/s)',delay:'Kechikish (ms, us yoki s)',loss:'Yo‘qotish (%)',queue:'Navbat (paket)'}[key]
          return <Field key={key} label={label}>{key==='delay'?<Input aria-label={label} placeholder="O‘zgarmasin" value={event.params?.[key] ?? ''} onChange={e=>patch(index,{params:{...event.params,[key]:e.target.value || undefined}})} />:
            <NumberInput aria-label={label} placeholder="O‘zgarmasin" min={key==='loss'?0:key==='queue'?1:Number.MIN_VALUE} max={key==='loss'?100:undefined} step={key==='queue'?1:'any'} value={event.params?.[key]} onChange={value=>patch(index,{params:{...event.params,[key]:value}})} />}</Field>
        })}</>}
      </div>
    </div>)}</div>}
  </Section>
}
