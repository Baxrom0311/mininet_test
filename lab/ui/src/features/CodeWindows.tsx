import { useState } from 'react'
import { Cpu, Network } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { toast } from '@/components/ui/toast'
import { api } from '@/lib/api'
import { mutate, running, useStore, type Win } from '@/lib/store'
import type { Pipeline } from '@/lib/types'
import { LogBox, PipelineView } from './DeviceWindow'
import { WindowFrame } from './WindowFrame'

function CodeArea({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <textarea spellCheck={false} value={value} onChange={(e) => onChange(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === 'Tab') { e.preventDefault(); const el = e.currentTarget, s = el.selectionStart; el.setRangeText('    ', s, el.selectionEnd, 'end'); onChange(el.value) }
      }}
      className="min-h-[240px] flex-1 resize-none rounded-lg border bg-background/70 p-3 font-mono text-[12.5px] leading-relaxed outline-none focus:ring-1 focus:ring-ring" style={{ tabSize: 4 }} />
  )
}

export function P4Window({ win }: { win: Win }) {
  const prog = win.ref
  const src = useStore((s) => s.project.p4_programs[prog])
  const devices = useStore((s) => s.project.devices)
  const [code, setCode] = useState(src ?? '')
  const [log, setLog] = useState<{ text: string; err: boolean } | null>(null)
  const [pipe, setPipe] = useState<Pipeline | null>(null)
  const users = Object.entries(devices).filter(([, d]) => d.type === 'p4switch' && d.config?.program === prog).map(([n]) => n)
  const save = () => mutate((p) => { p.p4_programs[prog] = code })
  return (
    <WindowFrame win={win} icon={<Cpu />} title={prog + '.p4'} sub="P4_16 · v1model (BMv2)">
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-auto p-3">
        <CodeArea value={code} onChange={setCode} />
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" onClick={() => { save(); toast(`${prog} saqlandi`, 'ok') }}>Saqlash</Button>
          <Button size="sm" variant="secondary" onClick={async () => {
            save(); setLog({ text: 'kompilyatsiya...', err: false })
            try { const r = await api.post('/api/p4/compile', { source: code }); setLog({ text: (r.outline_only ? "[p4c yo'q — faqat tuzilma tahlili]\n" : '') + r.log, err: !r.ok && !r.outline_only }); setPipe(r.pipeline) }
            catch (e: any) { setLog({ text: e.message, err: true }) }
          }}>Tekshirish (p4c)</Button>
          <Button size="sm" variant="outline" onClick={async () => {
            save()
            if (!running()) return toast('Tarmoq ishlamayapti', 'warn')
            if (!users.length) return toast('Bu dasturni hech bir switch ishlatmayapti', 'warn')
            for (const n of users) {
              try { const r = await api.post(`/api/net/p4/${n}/load`, { project: useStore.getState().project }); setLog({ text: `[${n}]\n` + r.log, err: !r.ok }) }
              catch (e: any) { setLog({ text: `[${n}] ${e.message}`, err: true }) }
            }
          }}>Ishlatayotgan switchlarga yuklash</Button>
          <span className="text-[11px] text-muted-foreground">Ishlatadi: {users.join(', ') || '—'}</span>
        </div>
        {log && <LogBox text={log.text} err={log.err} />}
        {pipe && <div className="rounded-lg border"><PipelineView data={pipe} /></div>}
      </div>
    </WindowFrame>
  )
}

export function SdnWindow({ win }: { win: Win }) {
  const cname = win.ref
  const c = useStore((s) => s.project.controllers[cname])
  const devices = useStore((s) => s.project.devices)
  const [code, setCode] = useState(c?.code ?? '')
  const [port, setPort] = useState(String(c?.port ?? 6653))
  const [log, setLog] = useState<{ text: string; err: boolean } | null>(null)
  if (!c) return null
  const users = Object.entries(devices).filter(([, d]) => d.config?.controller === cname).map(([n]) => n)
  const save = () => mutate((p) => { p.controllers[cname] = { code, port: parseInt(port, 10) || 6653 } })
  return (
    <WindowFrame win={win} icon={<Network />} title={cname} sub="SDN kontroller ilovasi · os-ken / Ryu (OpenFlow 1.3)">
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-auto p-3">
        <CodeArea value={code} onChange={setCode} />
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted-foreground">OpenFlow port:</span>
          <Input type="number" className="h-8 w-24" value={port} onChange={(e) => setPort(e.target.value)} />
          <Button size="sm" onClick={() => { save(); toast(`${cname} saqlandi`, 'ok') }}>Saqlash</Button>
          <Button size="sm" variant="secondary" onClick={async () => {
            save()
            if (!running()) return toast('Saqlandi. Tarmoq ishga tushganda kontroller ham ishga tushadi.', 'ok')
            setLog({ text: 'qayta ishga tushirilmoqda...', err: false })
            try { const r = await api.post(`/api/net/sdn/${cname}/restart`, { project: useStore.getState().project }); setLog({ text: r.log || 'OK', err: false }) }
            catch (e: any) { setLog({ text: e.message, err: true }) }
          }}>Saqlash + qayta ishga tushirish</Button>
          <Button size="sm" variant="outline" onClick={async () => {
            try { const r = await api.get(`/api/net/sdn/${cname}/log`); setLog({ text: r.log.slice(-6000) || "(bo'sh)", err: false }) } catch (e: any) { setLog({ text: e.message, err: true }) }
          }}>Log</Button>
          <span className="text-[11px] text-muted-foreground">Ulangan: {users.join(', ') || '—'}</span>
        </div>
        {log && <LogBox text={log.text} err={log.err} />}
      </div>
    </WindowFrame>
  )
}
