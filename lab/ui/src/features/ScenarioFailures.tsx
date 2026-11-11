import { NumberInput } from '@/components/NumberInput'
import { Checkbox } from '@/components/ui/checkbox'
import { Field } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { Section } from '@/components/ui/table'
import type { Project, Scenario } from '@/lib/types'
import { ScenarioTargets } from './ScenarioTargets'

export function ScenarioFailures({project,sc,update}:{project:Project;sc:Scenario;update:(fn:(scenario:Scenario)=>void)=>void}) {
  const model=sc.failures
  return <Section title="Nosozlik va tiklanish" right={<Checkbox checked={Boolean(model)} label="Modelni yoqish" onChange={checked=>update(s=>{if(checked) s.failures={seed:1,distribution:'exponential',links:{mtbf_s:300,mttr_s:30,targets:'all'}};else delete s.failures})} />}>
    {!model?<p className="p-3 text-xs text-muted-foreground">MTBF/MTTR orqali tasodifiy yoki qat’iy uzilishlar va tiklanishlarni modellashtiring. Natijada oqimlarning mavjudligi ham hisoblanadi.</p>:<div className="space-y-3 p-3">
      <div className="scenario-field-grid grid grid-cols-2 gap-3"><Field label="Taqsimot"><Select ariaLabel="Nosozlik taqsimoti" value={model.distribution ?? 'exponential'} options={[{value:'exponential',label:'Eksponensial (tasodifiy)'},{value:'fixed',label:'Qat’iy davriy'}]} onChange={value=>update(s=>{s.failures!.distribution=value as 'fixed'|'exponential'})} /></Field>
        <Field label="Nosozlik seed"><NumberInput aria-label="Nosozlik seed" step={1} min={Number.MIN_SAFE_INTEGER} max={Number.MAX_SAFE_INTEGER} value={model.seed} placeholder="1" onChange={value=>update(s=>{s.failures!.seed=value})} /></Field></div>
      <p className="text-[11px] text-muted-foreground">MTBF — ishlash oralig‘i, MTTR — tiklanish davomiyligi. Bir xil seed va nishonlar bir xil nosozlik jadvalini beradi.</p>
      {(['links','devices'] as const).map(group=>{
        const spec=model[group],label=group==='links'?'Kabellar':'Qurilmalar'
        const options=Object.entries(group==='links'?project.links:project.devices).map(([id])=>({value:id,label:id}))
        return <div key={group} className="space-y-3 rounded-lg border p-3" data-failure-group={group}>
          <Checkbox checked={Boolean(spec)} label={label+' nosozliklari'} onChange={checked=>update(s=>{
            if(checked) s.failures![group]={mtbf_s:300,mttr_s:30,targets:'all'}
            else {delete s.failures![group];if(!s.failures!.links && !s.failures!.devices) delete s.failures}
          })} />
          {spec && <><div className="scenario-field-grid grid grid-cols-2 gap-3">
            <Field label="MTBF (s)"><NumberInput aria-label={label+' MTBF (s)'} required min={Number.MIN_VALUE} step="any" value={spec.mtbf_s} onChange={value=>{if(value!=null) update(s=>{s.failures![group]!.mtbf_s=value})}} /></Field>
            <Field label="MTTR (s)"><NumberInput aria-label={label+' MTTR (s)'} required min={Number.MIN_VALUE} step="any" value={spec.mttr_s} onChange={value=>{if(value!=null) update(s=>{s.failures![group]!.mttr_s=value})}} /></Field>
          </div><Field label="Nishonlar"><Select ariaLabel={label+' nishonlari'} value={spec.targets==='all'?'all':'selected'} options={[{value:'all',label:group==='links'?'Barcha kabellar':'Barcha tarmoq qurilmalari (host/serverdan tashqari)'},{value:'selected',label:'Tanlangan nishonlar'}]} onChange={value=>update(s=>{s.failures![group]!.targets=value==='all'?'all':[]})} /></Field>
          {spec.targets!=='all' && <ScenarioTargets label={label} options={options} value={spec.targets} onChange={value=>update(s=>{s.failures![group]!.targets=value})} />}</>}
        </div>
      })}
    </div>}
  </Section>
}
