import { NumberInput } from '@/components/NumberInput'
import { Checkbox } from '@/components/ui/checkbox'
import { Field, Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { Section } from '@/components/ui/table'
import type { Scenario } from '@/lib/types'

export function ScenarioSettings({sc,update}:{sc:Scenario;update:(fn:(scenario:Scenario)=>void)=>void}) {
  return <Section title="Ssenariy sozlamalari"><div className="space-y-3 p-3">
    <div className="scenario-field-grid grid grid-cols-2 gap-3">
      <Field label="Davomiylik (s)"><NumberInput aria-label="Davomiylik (s)" required min={1} max={3600} step="any" value={sc.duration} onChange={value=>{if(value!=null) update(s=>{s.duration=value})}} /></Field>
      <Field label="IP versiyasi"><Select ariaLabel="IP versiyasi" value={String(sc.ip_version ?? 4)} options={[{value:'4',label:'IPv4'},{value:'6',label:'IPv6'}]} onChange={value=>update(s=>{s.ip_version=Number(value) as 4|6})} /></Field>
      <Field label="Namuna oralig‘i (s)"><NumberInput aria-label="Namuna oralig‘i (s)" min={.001} max={3600} step="any" placeholder="1" value={sc.sample} onChange={value=>update(s=>{s.sample=value})} /></Field>
      <Field label="Izoh"><Input aria-label="Ssenariy izohi" value={sc.notes || ''} placeholder="Nima tekshirilyapti?" onChange={event=>update(s=>{s.notes=event.target.value})} /></Field>
    </div>
    <p className="text-[11px] text-muted-foreground">Ko‘pi bilan 100 000 namuna oralig‘i. Analitik model hodisalarni shu vaqt qadamida baholaydi. IPv6 uchun qurilmalarda IPv6 manzillari kerak; dvigatel mosligi ishga tushirishdan oldin tekshiriladi.</p>
    <details className="rounded-lg border p-3 text-xs"><summary className="cursor-pointer font-medium text-muted-foreground">Takrorlanuvchanlik va ns-3</summary><div className="scenario-field-grid mt-3 grid grid-cols-2 gap-3">
      <Field label="Seed"><NumberInput aria-label="Seed" step={1} min={1} max={2147483647} value={sc.seed} placeholder="1" onChange={value=>update(s=>{s.seed=value})} /></Field>
      <Field label="RNG run"><NumberInput aria-label="RNG run" step={1} min={1} max={2147483647} value={sc.rng_run} placeholder="1" onChange={value=>update(s=>{s.rng_run=value})} /></Field>
    </div><div className="mt-3"><Checkbox checked={sc.ns3_fast===true} label="ns-3 tez rejim — MTU 9000" onChange={value=>update(s=>{s.ns3_fast=value})} /><p className="mt-2 text-[11px] text-muted-foreground">MTU o‘zgarishi paket darajasidagi kechikish, yo‘qotish va navbatlarga ta’sir qiladi. Wi-Fi bo‘lsa tez rejim qo‘llanmaydi.</p></div></details>
  </div></Section>
}
