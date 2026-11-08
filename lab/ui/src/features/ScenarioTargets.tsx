import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'

export function ScenarioTargets({label,options,value,onChange}:{label:string;options:{value:string;label:string}[];value:string[];onChange:(value:string[])=>void}) {
  const missing=value.filter(id=>!options.some(option=>option.value===id))
  const all=[...options,...missing.map(id=>({value:id,label:id+' (topilmadi)'}))]
  return <fieldset className="min-w-0 rounded-lg border p-3"><legend className="px-1 text-xs font-medium text-muted-foreground">{label} · {value.length}</legend>
    <div className="mb-2 flex gap-2"><Button size="xs" variant="ghost" onClick={()=>onChange(options.map(option=>option.value))}>Barchasi</Button><Button size="xs" variant="ghost" onClick={()=>onChange([])}>Tozalash</Button></div>
    <div className="grid max-h-40 grid-cols-2 gap-2 overflow-auto">{all.map(option=><Checkbox key={option.value} label={option.label} checked={value.includes(option.value)} onChange={checked=>onChange(checked?[...value,option.value]:value.filter(id=>id!==option.value))} />)}</div>
    {!all.length && <p className="text-xs text-muted-foreground">Mos qurilma yo‘q.</p>}
  </fieldset>
}
