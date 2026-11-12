import { useState } from 'react'
import { Check, Copy, Cpu, ExternalLink, Network, Terminal } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, type Option } from '@/components/ui/select'
import { toast } from '@/components/ui/toast'
import { api } from '@/lib/api'
import { mutate, running, useStore, type Win } from '@/lib/store'
import type { Controller, ControllerType, Pipeline } from '@/lib/types'
import { cn } from '@/lib/utils'
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

const TYPE_OPTIONS: Option[] = [
  { value: 'ryu', label: 'Ryu / os-ken' },
  { value: 'pox', label: 'POX' },
  { value: 'remote', label: 'Remote Controller' },
  { value: 'onos', label: 'ONOS' },
  { value: 'odl', label: 'OpenDaylight' },
  { value: 'floodlight', label: 'Floodlight' },
]

const SUB_MAP: Record<ControllerType, string> = {
  ryu: 'SDN kontroller ilovasi · os-ken / Ryu (OpenFlow 1.3)',
  pox: 'SDN kontroller · POX (OpenFlow 1.0)',
  remote: 'Masofaviy SDN kontroller · Remote (OpenFlow)',
  onos: 'SDN kontroller · ONOS (OpenFlow 1.3)',
  odl: 'SDN kontroller · OpenDaylight (OpenFlow 1.3)',
  floodlight: 'SDN kontroller · Floodlight (OpenFlow 1.0 / 1.3)',
}

const POX_MODULE_PRESETS: Option[] = [
  { value: 'forwarding.l2_learning', label: 'forwarding.l2_learning (L2 Learning Switch)' },
  { value: 'forwarding.l2_pairs', label: 'forwarding.l2_pairs (L2 Flow-based Pairs)' },
  { value: 'forwarding.hub', label: 'forwarding.hub (Hub - har bir paketni flood)' },
  { value: 'forwarding.l3_learning', label: 'forwarding.l3_learning (L3 Router)' },
  { value: 'openflow.spanning_tree --no-flood --hold-down forwarding.l2_learning', label: 'openflow.spanning_tree + l2_learning (STP)' },
  { value: '__custom__', label: '— Maxsus modul / argumentlar —' },
]

const POX_TEMPLATE_CODE = `"""NetLab SDN ilova: POX L2 learning switch (OpenFlow 1.0)."""
from pox.core import core
import pox.openflow.libopenflow_01 as of

log = core.getLogger()

class LearningSwitch:
    def __init__(self, connection):
        self.connection = connection
        self.mac_to_port = {}
        connection.addListeners(self)

    def _handle_PacketIn(self, event):
        packet = event.parsed
        if not packet.parsed:
            return
        in_port = event.port
        src_mac = packet.src
        dst_mac = packet.dst
        self.mac_to_port[src_mac] = in_port

        if dst_mac in self.mac_to_port:
            out_port = self.mac_to_port[dst_mac]
            msg = of.ofp_flow_mod()
            msg.match = of.ofp_match.from_prn(packet)
            msg.idle_timeout = 30
            msg.hard_timeout = 60
            msg.actions.append(of.ofp_action_output(port=out_port))
            msg.data = event.ofp
            self.connection.send(msg)
        else:
            msg = of.ofp_packet_out()
            msg.data = event.ofp
            msg.actions.append(of.ofp_action_output(port=of.OFPP_FLOOD))
            msg.in_port = in_port
            self.connection.send(msg)

def launch():
    def start_switch(event):
        log.info("Switch ulandi: dpid=%s", event.dpid)
        LearningSwitch(event.connection)

    core.openflow.addListenerByName("ConnectionUp", start_switch)
    log.info("POX L2 learning switch faollashtirildi")
`

function DockerQuickstartCard({ type, ip, port }: { type: ControllerType; ip: string; port: string }) {
  const [copied, setCopied] = useState<string | null>(null)
  const copyCmd = (cmd: string, id: string) => {
    navigator.clipboard?.writeText(cmd)
    setCopied(id)
    toast("Buyruq nusxalandi", 'ok')
    setTimeout(() => setCopied(null), 2000)
  }

  if (type === 'onos') {
    const cmd = 'docker run -d --name onos -p 6653:6653 -p 8181:8181 onosproject/onos:latest'
    const actCmd = 'docker exec -it onos /root/onos/bin/onos "onos:app activate org.onosproject.openflow org.onosproject.fwd"'
    return (
      <Card className="flex-1 overflow-auto border-blue-500/30 bg-blue-500/5">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm text-blue-400">
            <Terminal className="h-4 w-4" />
            ONOS SDN Kontrolleri — Docker orqali tezkor ishga tushirish
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-xs leading-relaxed">
          <div>
            <p className="font-semibold text-foreground mb-1">1. ONOS konteynerini ishga tushirish:</p>
            <div className="flex items-center justify-between rounded bg-muted/80 p-2 font-mono text-[11.5px]">
              <span className="overflow-x-auto select-all">{cmd}</span>
              <Button size="icon" variant="ghost" className="h-6 w-6 ml-2 shrink-0" onClick={() => copyCmd(cmd, 'cmd')}>
                {copied === 'cmd' ? <Check className="h-3.5 w-3.5 text-emerald-500" /> : <Copy className="h-3.5 w-3.5" />}
              </Button>
            </div>
          </div>
          <div>
            <p className="font-semibold text-foreground mb-1">2. Kerakli ilovalarni faollashtirish (OpenFlow + L2 forwarding):</p>
            <div className="flex items-center justify-between rounded bg-muted/80 p-2 font-mono text-[11.5px]">
              <span className="overflow-x-auto select-all">{actCmd}</span>
              <Button size="icon" variant="ghost" className="h-6 w-6 ml-2 shrink-0" onClick={() => copyCmd(actCmd, 'act')}>
                {copied === 'act' ? <Check className="h-3.5 w-3.5 text-emerald-500" /> : <Copy className="h-3.5 w-3.5" />}
              </Button>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2 pt-1 text-[11.5px] border-t border-border/50">
            <div>
              <span className="text-muted-foreground">Web GUI interfeysi: </span>
              <a href="http://localhost:8181/onos/ui" target="_blank" rel="noreferrer" className="text-primary underline inline-flex items-center gap-1">
                http://localhost:8181/onos/ui <ExternalLink className="h-3 w-3" />
              </a>
              <div className="text-muted-foreground text-[10.5px]">Login / Parol: <span className="font-mono text-foreground">karaf / karaf</span></div>
            </div>
            <div>
              <span className="text-muted-foreground">Mininet ulanishi: </span>
              <span className="font-mono text-foreground font-semibold">tcp:{ip || '127.0.0.1'}:{port || '6653'}</span>
              <div className="text-muted-foreground text-[10.5px]">OpenFlow 1.3 protokoli qo'llab-quvvatlanadi</div>
            </div>
          </div>
        </CardContent>
      </Card>
    )
  }

  if (type === 'odl') {
    const cmd = 'docker run -d --name odl -p 6653:6653 -p 8181:8181 opendaylight/odl:latest'
    const featCmd = 'feature:install odl-restconf odl-l2switch-switch'
    return (
      <Card className="flex-1 overflow-auto border-amber-500/30 bg-amber-500/5">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm text-amber-400">
            <Terminal className="h-4 w-4" />
            OpenDaylight (ODL) Kontrolleri — Docker orqali tezkor ishga tushirish
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-xs leading-relaxed">
          <div>
            <p className="font-semibold text-foreground mb-1">1. OpenDaylight konteynerini ishga tushirish:</p>
            <div className="flex items-center justify-between rounded bg-muted/80 p-2 font-mono text-[11.5px]">
              <span className="overflow-x-auto select-all">{cmd}</span>
              <Button size="icon" variant="ghost" className="h-6 w-6 ml-2 shrink-0" onClick={() => copyCmd(cmd, 'cmd')}>
                {copied === 'cmd' ? <Check className="h-3.5 w-3.5 text-emerald-500" /> : <Copy className="h-3.5 w-3.5" />}
              </Button>
            </div>
          </div>
          <div>
            <p className="font-semibold text-foreground mb-1">2. ODL Karaf konsolida OpenFlow modullarini yoqish:</p>
            <div className="flex items-center justify-between rounded bg-muted/80 p-2 font-mono text-[11.5px]">
              <span className="overflow-x-auto select-all">{featCmd}</span>
              <Button size="icon" variant="ghost" className="h-6 w-6 ml-2 shrink-0" onClick={() => copyCmd(featCmd, 'feat')}>
                {copied === 'feat' ? <Check className="h-3.5 w-3.5 text-emerald-500" /> : <Copy className="h-3.5 w-3.5" />}
              </Button>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2 pt-1 text-[11.5px] border-t border-border/50">
            <div>
              <span className="text-muted-foreground">Dlux / REST Web: </span>
              <a href="http://localhost:8181/index.html" target="_blank" rel="noreferrer" className="text-primary underline inline-flex items-center gap-1">
                http://localhost:8181/index.html <ExternalLink className="h-3 w-3" />
              </a>
              <div className="text-muted-foreground text-[10.5px]">Login / Parol: <span className="font-mono text-foreground">admin / admin</span></div>
            </div>
            <div>
              <span className="text-muted-foreground">Mininet ulanishi: </span>
              <span className="font-mono text-foreground font-semibold">tcp:{ip || '127.0.0.1'}:{port || '6653'}</span>
            </div>
          </div>
        </CardContent>
      </Card>
    )
  }

  if (type === 'floodlight') {
    const cmd = 'docker run -d --name floodlight -p 6653:6653 -p 8080:8080 floodlight/floodlight'
    return (
      <Card className="flex-1 overflow-auto border-emerald-500/30 bg-emerald-500/5">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm text-emerald-400">
            <Terminal className="h-4 w-4" />
            Floodlight Kontrolleri — Docker orqali tezkor ishga tushirish
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-xs leading-relaxed">
          <div>
            <p className="font-semibold text-foreground mb-1">1. Floodlight konteynerini ishga tushirish:</p>
            <div className="flex items-center justify-between rounded bg-muted/80 p-2 font-mono text-[11.5px]">
              <span className="overflow-x-auto select-all">{cmd}</span>
              <Button size="icon" variant="ghost" className="h-6 w-6 ml-2 shrink-0" onClick={() => copyCmd(cmd, 'cmd')}>
                {copied === 'cmd' ? <Check className="h-3.5 w-3.5 text-emerald-500" /> : <Copy className="h-3.5 w-3.5" />}
              </Button>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2 pt-1 text-[11.5px] border-t border-border/50">
            <div>
              <span className="text-muted-foreground">Web GUI / REST API: </span>
              <a href="http://localhost:8080/ui/index.html" target="_blank" rel="noreferrer" className="text-primary underline inline-flex items-center gap-1">
                http://localhost:8080/ui/index.html <ExternalLink className="h-3 w-3" />
              </a>
            </div>
            <div>
              <span className="text-muted-foreground">Mininet ulanishi: </span>
              <span className="font-mono text-foreground font-semibold">tcp:{ip || '127.0.0.1'}:{port || '6653'}</span>
            </div>
          </div>
        </CardContent>
      </Card>
    )
  }

  // Default: generic remote
  const onosCmd = 'docker run -d --name onos -p 6653:6653 -p 8181:8181 onosproject/onos:latest'
  const odlCmd = 'docker run -d --name odl -p 6653:6653 -p 8181:8181 opendaylight/odl:latest'
  const flCmd = 'docker run -d --name floodlight -p 6653:6653 -p 8080:8080 floodlight/floodlight'

  return (
    <Card className="flex-1 overflow-auto border-purple-500/30 bg-purple-500/5">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm text-purple-400">
          <Terminal className="h-4 w-4" />
          Masofaviy SDN Kontroller (Remote) — Quickstart Docker tavsiyalari
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-xs leading-relaxed">
        <p className="text-muted-foreground">
          Mininet switchlari quyidagi manzilga ulanadi:{' '}
          <span className="font-mono font-semibold text-foreground">
            tcp:{ip || '127.0.0.1'}:{port || '6653'}
          </span>
          . Tashqi kontroller sifatida Docker konteynerlaridan foydalanishingiz mumkin:
        </p>
        <div className="space-y-2">
          <div>
            <div className="flex items-center justify-between mb-1">
              <span className="font-semibold text-foreground">ONOS Kontrolleri:</span>
              <Button size="icon" variant="ghost" className="h-5 w-5" onClick={() => copyCmd(onosCmd, 'onos')}>
                {copied === 'onos' ? <Check className="h-3 w-3 text-emerald-500" /> : <Copy className="h-3 w-3" />}
              </Button>
            </div>
            <div className="rounded bg-muted/80 p-1.5 font-mono text-[11px] select-all overflow-x-auto">{onosCmd}</div>
          </div>
          <div>
            <div className="flex items-center justify-between mb-1">
              <span className="font-semibold text-foreground">OpenDaylight (ODL):</span>
              <Button size="icon" variant="ghost" className="h-5 w-5" onClick={() => copyCmd(odlCmd, 'odl')}>
                {copied === 'odl' ? <Check className="h-3 w-3 text-emerald-500" /> : <Copy className="h-3 w-3" />}
              </Button>
            </div>
            <div className="rounded bg-muted/80 p-1.5 font-mono text-[11px] select-all overflow-x-auto">{odlCmd}</div>
          </div>
          <div>
            <div className="flex items-center justify-between mb-1">
              <span className="font-semibold text-foreground">Floodlight:</span>
              <Button size="icon" variant="ghost" className="h-5 w-5" onClick={() => copyCmd(flCmd, 'fl')}>
                {copied === 'fl' ? <Check className="h-3 w-3 text-emerald-500" /> : <Copy className="h-3 w-3" />}
              </Button>
            </div>
            <div className="rounded bg-muted/80 p-1.5 font-mono text-[11px] select-all overflow-x-auto">{flCmd}</div>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

export function SdnWindow({ win }: { win: Win }) {
  const cname = win.ref
  const c = useStore((s) => s.project.controllers[cname])
  const devices = useStore((s) => s.project.devices)

  const [type, setType] = useState<ControllerType>((c?.type as ControllerType) || 'ryu')
  const [ip, setIp] = useState(c?.ip ?? '127.0.0.1')
  const [port, setPort] = useState(String(c?.port ?? 6653))
  const [modules, setModules] = useState(c?.modules ?? 'forwarding.l2_learning')
  const [code, setCode] = useState(c?.code ?? '')
  const [poxMode, setPoxMode] = useState<'modules' | 'code'>(() => {
    return c?.type === 'pox' && c?.code && c.code.trim().length > 0 ? 'code' : 'modules'
  })
  const [log, setLog] = useState<{ text: string; err: boolean } | null>(null)

  if (!c) return null
  const users = Object.entries(devices).filter(([, d]) => d.config?.controller === cname).map(([n]) => n)
  const isRemoteLike = type === 'remote' || type === 'onos' || type === 'odl' || type === 'floodlight'

  const save = () => mutate((p) => {
    const portNum = parseInt(port, 10) || 6653
    const updated: Controller = {
      type,
      port: portNum,
    }
    if (type === 'ryu') {
      updated.code = code
    } else if (type === 'pox') {
      updated.modules = modules.trim() || 'forwarding.l2_learning'
      updated.code = poxMode === 'code' ? code : ''
    } else {
      updated.ip = ip.trim() || '127.0.0.1'
      updated.code = ''
    }
    p.controllers[cname] = updated
  })

  const handleRestart = async () => {
    save()
    if (!running()) {
      return toast(
        isRemoteLike
          ? 'Saqlandi. Tarmoq ishga tushganda kontrollerga ulaniladi.'
          : 'Saqlandi. Tarmoq ishga tushganda kontroller ham ishga tushadi.',
        'ok',
      )
    }
    setLog({ text: isRemoteLike ? 'ulanish tekshirilmoqda...' : 'qayta ishga tushirilmoqda...', err: false })
    try {
      const r = await api.post(`/api/net/sdn/${cname}/restart`, { project: useStore.getState().project })
      setLog({ text: r.log || 'OK', err: false })
    } catch (e: any) {
      setLog({ text: e.message, err: true })
    }
  }

  const handleLog = async () => {
    try {
      const r = await api.get(`/api/net/sdn/${cname}/log`)
      setLog({ text: r.log.slice(-6000) || "(bo'sh)", err: false })
    } catch (e: any) {
      setLog({ text: e.message, err: true })
    }
  }

  return (
    <WindowFrame win={win} icon={<Network />} title={cname} sub={SUB_MAP[type] || SUB_MAP.ryu}>
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-auto p-3">
        {/* Yuqori boshqaruv paneli: Kontroller turi va unga xos parametrlar */}
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border bg-muted/30 p-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-medium text-muted-foreground whitespace-nowrap">Kontroller turi:</span>
            <Select
              className="h-8 w-44"
              value={type}
              onChange={(v) => {
                const nextType = v as ControllerType
                setType(nextType)
                if (!port || port === '6633' || port === '6653') {
                  setPort('6653')
                }
              }}
              options={TYPE_OPTIONS}
            />
          </div>

          {/* Remote / ONOS / ODL / Floodlight uchun IP va Port */}
          {isRemoteLike && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-muted-foreground">IP manzil:</span>
              <Input
                className="h-8 w-32 font-mono text-xs"
                value={ip}
                onChange={(e) => setIp(e.target.value)}
                placeholder="127.0.0.1"
              />
              <span className="text-xs text-muted-foreground">Port:</span>
              <Input
                type="number"
                className="h-8 w-24 font-mono text-xs"
                value={port}
                onChange={(e) => setPort(e.target.value)}
                placeholder="6653"
              />
            </div>
          )}

          {/* POX uchun Rejim tanlash va Port */}
          {type === 'pox' && (
            <div className="flex flex-wrap items-center gap-2">
              <div className="flex items-center rounded-md border bg-background/60 p-0.5">
                <button
                  type="button"
                  className={cn(
                    'px-2.5 py-1 text-xs rounded transition-colors',
                    poxMode === 'modules'
                      ? 'bg-primary text-primary-foreground font-medium shadow-sm'
                      : 'text-muted-foreground hover:text-foreground',
                  )}
                  onClick={() => setPoxMode('modules')}
                >
                  POX modullari
                </button>
                <button
                  type="button"
                  className={cn(
                    'px-2.5 py-1 text-xs rounded transition-colors',
                    poxMode === 'code'
                      ? 'bg-primary text-primary-foreground font-medium shadow-sm'
                      : 'text-muted-foreground hover:text-foreground',
                  )}
                  onClick={() => setPoxMode('code')}
                >
                  Maxsus kod (app.py)
                </button>
              </div>
              <span className="text-xs text-muted-foreground">Port:</span>
              <Input
                type="number"
                className="h-8 w-24 font-mono text-xs"
                value={port}
                onChange={(e) => setPort(e.target.value)}
                placeholder="6653"
              />
            </div>
          )}
        </div>

        {/* Asosiy ish maydoni */}
        {type === 'ryu' && (
          <CodeArea value={code} onChange={setCode} />
        )}

        {type === 'pox' && (
          poxMode === 'code' ? (
            <div className="flex flex-1 flex-col gap-2 min-h-0">
              <CodeArea value={code} onChange={setCode} />
              <div className="flex items-center justify-between text-xs text-muted-foreground">
                <span>POX Python kodi <code>app.py</code> sifatida yuklanadi va <code>launch()</code> orqali chaqiriladi.</span>
                {!code.trim() && (
                  <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => setCode(POX_TEMPLATE_CODE)}>
                    POX shablonini yuklash
                  </Button>
                )}
              </div>
            </div>
          ) : (
            <Card className="flex-1 overflow-auto border-orange-500/30 bg-orange-500/5">
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-sm text-orange-400">
                  <Network className="h-4 w-4" />
                  POX OpenFlow 1.0 Kontroller modullari
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-4 text-xs">
                <div>
                  <label className="block text-xs font-semibold text-foreground mb-1.5">Tayyor POX moduli shabloni:</label>
                  <Select
                    className="h-8 w-full"
                    value={POX_MODULE_PRESETS.some((p) => p.value === modules) ? modules : '__custom__'}
                    onChange={(v) => {
                      if (v !== '__custom__') setModules(v)
                    }}
                    options={POX_MODULE_PRESETS}
                  />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-foreground mb-1.5">POX modullari / ishga tushirish argumentlari:</label>
                  <Input
                    className="h-8 w-full font-mono text-xs"
                    value={modules}
                    onChange={(e) => setModules(e.target.value)}
                    placeholder="forwarding.l2_learning"
                  />
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    Bir nechta modulni bo'sh joy bilan ajratib yozishingiz mumkin (masalan: <code>forwarding.l2_learning samples.pretty_log</code>).
                  </p>
                </div>
                <div className="rounded border bg-background/80 p-3 text-[11.5px] leading-relaxed">
                  <div className="font-semibold text-foreground mb-1">Buyruq satrida ishga tushirish ko'rinishi:</div>
                  <code className="block rounded bg-muted/70 p-2 font-mono text-xs select-all text-primary">
                    python3 pox.py openflow.of_01 --port={port || '6653'} {modules || 'forwarding.l2_learning'}
                  </code>
                  <p className="mt-2 text-muted-foreground">
                    Tarmoq ishga tushganda Mininet switchlari OpenFlow 1.0 protokoli orqali ushbu kontrollerga ulanadi.
                  </p>
                </div>
              </CardContent>
            </Card>
          )
        )}

        {isRemoteLike && (
          <DockerQuickstartCard type={type} ip={ip} port={port} />
        )}

        {/* Pastki harakatlar paneli */}
        <div className="flex flex-wrap items-center gap-2 pt-1 border-t border-border/50">
          {type === 'ryu' && (
            <>
              <span className="text-xs text-muted-foreground">OpenFlow port:</span>
              <Input type="number" className="h-8 w-24" value={port} onChange={(e) => setPort(e.target.value)} />
            </>
          )}
          <Button size="sm" onClick={() => { save(); toast(`${cname} saqlandi`, 'ok') }}>
            Saqlash
          </Button>
          <Button size="sm" variant="secondary" onClick={handleRestart}>
            {isRemoteLike ? 'Saqlash + ulanishni tekshirish' : 'Saqlash + qayta ishga tushirish'}
          </Button>
          <Button size="sm" variant="outline" onClick={handleLog}>
            Log
          </Button>
          <span className="text-[11px] text-muted-foreground">Ulangan: {users.join(', ') || '—'}</span>
        </div>

        {log && <LogBox text={log.text} err={log.err} />}
      </div>
    </WindowFrame>
  )
}
