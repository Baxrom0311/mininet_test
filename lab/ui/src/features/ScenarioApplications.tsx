import { useEffect, useId, useRef, useState } from 'react'
import { ChevronDown, ChevronUp, Copy, Plus, Trash2 } from 'lucide-react'
import { NumberInput } from '@/components/NumberInput'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Field, Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { Section } from '@/components/ui/table'
import { APPLICATIONS, changeAppType, newApp, receiversOf } from '@/lib/scenario'
import { validInputs } from '@/lib/forms'
import type { Project, Scenario, ScenarioApp } from '@/lib/types'
import { ScenarioTargets } from './ScenarioTargets'

type Update=(fn:(scenario:Scenario)=>void)=>void
export function ScenarioApplications({project,sc,update}:{project:Project;sc:Scenario;update:Update}) {
  const editorId=useId()
  const [active,setActive]=useState(0)
  const editor=useRef<HTMLDivElement>(null)
  const endpoints=Object.entries(project.devices).filter(([,device])=>['host','server','router'].includes(device.type)).map(([id])=>id).sort()
  const receivers=endpoints.filter(id=>['host','server'].includes(project.devices[id].type))
  useEffect(()=>{if(active>=sc.apps.length) setActive(Math.max(0,sc.apps.length-1))},[sc.apps.length,active])
  const canLeave=()=>validInputs(editor.current)
  const add=(copy?:ScenarioApp)=>{
    if(!canLeave()) return
    const fresh=newApp(sc,endpoints,receivers)
    update(scenario=>scenario.apps.push(copy?{...copy,id:fresh.id,label:(copy.label || copy.id)+' — nusxa'}:fresh));setActive(sc.apps.length)
  }
  const patch=(index:number,values:Partial<ScenarioApp>)=>update(scenario=>{scenario.apps[index]={...scenario.apps[index],...values}})
  const choices=(value:string)=>[...endpoints.map(id=>({value:id,label:id})),...(!endpoints.includes(value) && value?[{value,label:value+' (topilmadi)'}]:[])]
  return <Section title="Trafik ilovalari" right={<Button size="xs" variant="outline" disabled={endpoints.length<2} onClick={()=>add()}><Plus /> Ilova qo‘shish</Button>}>
    {!sc.apps.length && <p className="p-3 text-xs text-muted-foreground">UDP, TCP, Ping, VoIP, Video (DASH/HLS), HTTP/3 (QUIC), HTTP, FTP yoki multicast trafik qo‘shing. Kamida ikkita trafik qurilmasi kerak.</p>}
    <div ref={editor} className="space-y-2 p-3">{sc.apps.map((app,index)=>{
      const opened=index===active,kind=APPLICATIONS.find(option=>option.value===app.type)
      const set=(values:Partial<ScenarioApp>)=>patch(index,values)
      const number=(label:string,key:'size'|'start'|'stop'|'interval'|'think'|'dscp'|'dport'|'target_buffer'|'streams',placeholder:string,min:number,max?:number,required=false,step:number|string='any')=><Field label={label}><NumberInput aria-label={label} min={min} max={max} step={step} required={required} placeholder={placeholder} value={app[key]} onChange={value=>set({[key]:value})} /></Field>
      return <div key={index} data-app-index={index} className="min-w-0 scroll-mt-12 overflow-hidden rounded-lg border bg-background/30">
        <div className="flex items-center gap-2 px-3 py-2"><button aria-expanded={opened} aria-controls={editorId+'-app-'+index} onClick={()=>{if(canLeave()) setActive(opened?-1:index)}} className="flex min-w-0 flex-1 items-center gap-2 text-left text-xs"><Badge>{kind?.label || app.type}</Badge><span className="truncate font-semibold">{app.label || app.id}</span><span className="ml-auto hidden truncate text-muted-foreground sm:inline">{app.src} → {receiversOf(app).join(', ')}</span>{opened?<ChevronUp className="size-3.5 shrink-0" />:<ChevronDown className="size-3.5 shrink-0" />}</button>
          <Button size="icon" variant="ghost" className="size-7" aria-label={'Nusxalash: '+app.id} onClick={()=>add(app)}><Copy className="size-3.5" /></Button><Button size="icon" variant="ghost" className="size-7 text-muted-foreground hover:text-destructive" aria-label={'O‘chirish: '+app.id} onClick={()=>{update(scenario=>scenario.apps.splice(index,1));setActive(value=>value>index?value-1:value)}}><Trash2 className="size-3.5" /></Button></div>
        {opened && <div id={editorId+'-app-'+index} className="space-y-3 border-t p-3" role="group" aria-label={'Ilova '+app.id}>
          <p className="text-xs text-muted-foreground">{kind?.hint}</p>
          <div className="scenario-field-grid grid grid-cols-2 gap-3">
            <Field label="Ilova ID"><Input aria-label="Ilova ID" required value={app.id} onChange={event=>set({id:event.target.value})} /></Field>
            <Field label="Trafik turi"><Select ariaLabel="Trafik turi" value={app.type} options={[...APPLICATIONS]} onChange={value=>update(scenario=>{scenario.apps[index]=changeAppType(app,value as ScenarioApp['type'],receivers)})} /></Field>
            <Field label={['http','ftp','video','http3'].includes(app.type)?'Mijoz':'Manba'}><Select ariaLabel="Manba" value={app.src} options={choices(app.src)} onChange={value=>set({src:value,...(app.type==='mcast'?{dst:receiversOf(app).filter(id=>id!==value)}:{})})} /></Field>
            {app.type==='mcast'?<Field label="IPv4 multicast guruhi"><Input aria-label="IPv4 multicast guruhi" required placeholder="239.1.1.1" value={app.group || ''} onChange={event=>set({group:event.target.value})} /></Field>:
              <Field label={['http','ftp','video','http3'].includes(app.type)?'Server':'Qabul qiluvchi'}><Select ariaLabel="Qabul qiluvchi" value={typeof app.dst==='string'?app.dst:''} options={choices(typeof app.dst==='string'?app.dst:'')} onChange={value=>set({dst:value})} /></Field>}
            {['udp','tcp','mcast'].includes(app.type) && <Field label="Tezlik"><Input aria-label="Tezlik" required={app.type!=='tcp'} placeholder={app.type==='tcp'?'Cheklanmagan':'1Mbps'} value={app.rate || ''} onChange={event=>set({rate:event.target.value || undefined})} /></Field>}
            {['udp','tcp','ping','mcast'].includes(app.type) && number('Paket hajmi (B)','size',app.type==='ping'?'64':'1000',app.type==='mcast'?20:1,65507,false,1)}
            {app.type==='ping' && number('Ping oralig‘i (s)','interval','0.2',1e-9,9223372036)}
            {app.type==='voip' && <Field label="Ovoz kodeki"><Select ariaLabel="Ovoz kodeki" value={app.codec || 'g711'} options={[{value:'g711',label:'G.711 — 64 kbit/s'},{value:'g729',label:'G.729 — 8 kbit/s'}]} onChange={value=>set({codec:value as ScenarioApp['codec']})} /></Field>}
            {app.type==='video' && <>
              <Field label="Rezolyutsiya / Sifat preseti">
                <Select
                  ariaLabel="Rezolyutsiya / Sifat preseti"
                  value={app.preset || '1080p'}
                  options={[
                    {value:'1080p',label:'1080p FHD (1920×1080 · 5 Mbps)'},
                    {value:'720p',label:'720p HD (1280×720 · 2.5 Mbps)'},
                    {value:'480p',label:'480p SD (854×480 · 1.2 Mbps)'},
                    {value:'360p',label:'360p Low (640×360 · 600 kbps)'},
                    {value:'auto',label:'Avtomatik ABR (DASH/HLS dinamik)'},
                  ]}
                  onChange={value=>set({preset:value})}
                />
              </Field>
              {number('Maqsadli bufer hajmi (s)','target_buffer','20',1,120,false,1)}
            </>}
            {app.type==='http3' && <>
              <Field label="Sahifa hajmi">
                <Input aria-label="Sahifa hajmi" required value={app.page ?? '500KB'} placeholder="500KB" onChange={event=>set({page:event.target.value})} />
              </Field>
              {number('So‘rov oralig‘i (s)','think','5',Number.MIN_VALUE,undefined)}
              {number('QUIC oqimlari (streams)','streams','4',1,64,false,1)}
            </>}
            {app.type==='http' && <><Field label="Sahifa hajmi"><Input aria-label="Sahifa hajmi" required value={app.page ?? '500KB'} placeholder="500KB" onChange={event=>set({page:event.target.value})} /></Field>{number('So‘rov oralig‘i (s)','think','5',Number.MIN_VALUE,undefined)}</>}
            {app.type==='ftp' && <><Field label="Fayl hajmi"><Input aria-label="Fayl hajmi" required value={app.file ?? '10MB'} placeholder="10MB" onChange={event=>set({file:event.target.value})} /></Field><Field label="Uzatish yo‘nalishi"><Select ariaLabel="Uzatish yo‘nalishi" value={app.direction || 'download'} options={[{value:'download',label:'Server → mijoz'},{value:'upload',label:'Mijoz → server'}]} onChange={value=>set({direction:value as ScenarioApp['direction']})} /></Field></>}
            {number('Boshlanish (s)','start','1',0,sc.duration)}{number('Tugash (s)','stop',String(sc.duration),0)}
            {number('DSCP (0–63)','dscp','0',0,63,false,1)}
            <Field label="Ko‘rinadigan nom"><Input aria-label="Ko‘rinadigan nom" value={app.label || ''} placeholder={app.id} onChange={event=>set({label:event.target.value || undefined})} /></Field>
          </div>
          {app.type==='mcast' && <ScenarioTargets label="Qabul qiluvchilar" options={receivers.filter(id=>id!==app.src).map(id=>({value:id,label:id}))} value={receiversOf(app)} onChange={value=>set({dst:value})} />}
          <details className="rounded-lg border p-2.5 text-xs"><summary className="cursor-pointer text-muted-foreground">Qo‘shimcha manzil va port</summary><div className="scenario-field-grid mt-3 grid grid-cols-2 gap-3">
            {app.type!=='mcast' && <Field label="Qabul IP manzili (ixtiyoriy)"><Input aria-label="Qabul IP manzili" value={app.dst_ip || ''} onChange={event=>set({dst_ip:event.target.value || undefined})} /></Field>}
            {number('Qabul porti','dport','Avtomatik',1,65535,false,1)}
          </div></details>
        </div>}
      </div>
    })}</div>
  </Section>
}
