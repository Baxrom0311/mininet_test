import { useEffect, useState } from 'react'
import {
  Binary,
  Check,
  Copy,
  Layers,
  Network,
  ShieldCheck,
} from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Segmented } from '@/components/ui/tabs'
import { toast } from '@/components/ui/toast'
import { api } from '@/lib/api'
import { PROTO_COLORS, clock } from '@/lib/format'
import { type Win } from '@/lib/store'
import { cn } from '@/lib/utils'
import { WindowFrame } from './WindowFrame'

interface LayerData {
  name?: string
  port?: string
  media?: string
  status?: string
  src_mac?: string
  dst_mac?: string
  ethertype?: string
  vlan?: any
  src_ip?: string
  dst_ip?: string
  ttl?: number
  protocol?: string
  checksum?: string
  total_len?: number
  proto?: string
  src_port?: number
  dst_port?: number
  seq?: number | null
  ack?: number | null
  flags?: string[]
  window?: number | null
  app?: string
  summary?: string
}

interface PDUInspectionResponse {
  device: string
  device_type: string
  timestamp: number
  proto: string
  summary: string
  in_layers: Record<string, LayerData>
  out_layers: Record<string, LayerData>
  device_logs: string[]
  hex_dump: string
}

export function PDUInspectorWindow({ win }: { win: Win }) {
  const [tab, setTab] = useState<'osi' | 'details' | 'hex'>('osi')
  const [osiDirection, setOsiDirection] = useState<'in' | 'out' | 'both'>('in')
  const [selectedLayer, setSelectedLayer] = useState<string>('l3')
  const [data, setData] = useState<PDUInspectionResponse | null>(null)
  const [copied, setCopied] = useState(false)

  const pkt = win.arg || {}
  const proto = (pkt.proto || 'TCP').toUpperCase()
  const summary = pkt.summary || `${proto} Packet`
  const link = pkt.link || 'eth0'

  // Backend orqali real PDU dekonstruksiyasi va Cisco Processing Logini olish
  useEffect(() => {
    let cancel = false
    const inspect = async () => {
      try {
        const res = await api.post<PDUInspectionResponse>('/api/pdu/inspect', {
          packet: pkt,
          device: 'Router_R1',
          device_type: 'router',
        })
        if (!cancel) setData(res)
      } catch {
        // Fallback agar backend xatosi bo'lsa
      }
    }
    inspect()
    return () => {
      cancel = true
    }
  }, [pkt])

  // Fallback / default data agar backend hali yuklanmagan bo'lsa
  const inLayers: Record<string, LayerData> = data?.in_layers || {
    l1: { name: 'Layer 1: Physical (Jismoniy)', port: `${link} (Rx)`, media: 'Ethernet 100Base-TX / Full-Duplex', status: 'UP' },
    l2: { name: 'Layer 2: Data Link (Kanal)', src_mac: '00:11:22:33:44:55', dst_mac: '00:aa:bb:cc:dd:ee', ethertype: proto === 'ARP' ? '0x0806 (ARP)' : '0x0800 (IPv4)' },
    l3: { name: 'Layer 3: Network (Tarmoq)', src_ip: '10.0.1.10', dst_ip: '10.0.2.20', ttl: 64, protocol: proto, checksum: '0x4a2b (Valid)', total_len: 60 },
    l4: { name: 'Layer 4: Transport', proto, src_port: 45678, dst_port: 80, seq: 1000, ack: 501, flags: ['ACK', 'PSH'], window: 64240 },
    l7: { name: 'Layer 7: Application (Ilova)', app: proto, summary },
  }

  const outLayers: Record<string, LayerData> = data?.out_layers || {
    l1: { name: 'Layer 1: Physical (Chiquvchi)', port: 'eth1 (Tx)', media: 'Ethernet 100Base-TX / Full-Duplex', status: 'Transmitting' },
    l2: { name: 'Layer 2: Data Link (Kanal)', src_mac: '00:1c:73:00:00:01', dst_mac: '00:50:56:c0:00:08', ethertype: proto === 'ARP' ? '0x0806 (ARP)' : '0x0800 (IPv4)' },
    l3: { name: 'Layer 3: Network (Tarmoq)', src_ip: '10.0.1.10', dst_ip: '10.0.2.20', ttl: 63, protocol: proto, checksum: '0x5b3c (Recalculated)', total_len: 60 },
    l4: { name: 'Layer 4: Transport', proto, src_port: 45678, dst_port: 80, seq: 1000, ack: 501, flags: ['ACK', 'PSH'], window: 64240 },
    l7: { name: 'Layer 7: Application (Ilova)', app: proto, summary },
  }

  const deviceLogs = data?.device_logs || [
    `1. Freym '${link}' portiga to'liq yetib keldi.`,
    `2. Layer 2: Freym sarlavhasi o'qildi. Maqsad MAC qurilma MAC manziliga mos keldi va qabul qilindi.`,
    `3. Layer 3: ${proto} paketi ochildi. TTL=64 > 1, IP nazorat yig'indisi to'g'ri.`,
    `4. Marshrutlash (FIB lookup): Maqsad IP uchun marshrutlar jadvalidan mos Next-Hop aniqlandi (Interfeys: eth1).`,
    `5. TTL dekrementi: TTL qiymati 64 dan 63 ga tushirildi, IP checksum qayta hisoblandi.`,
    `6. ARP jadvali: Next-Hop IP uchun yangi maqsad MAC manzili aniqlandi.`,
    `7. Layer 2: Yangi sarlavha (yangi Src/Dst MAC) bilan qayta inkapsulyatsiya qilindi.`,
    `8. Chiqish: Freym chiquvchi 'eth1' porti navbatiga qo'yildi.`,
  ]

  const hexDump = data?.hex_dump || `0000   00 aa bb cc dd ee 00 11  22 33 44 55 08 00 45 00   |..........3DU..E.|
0010   00 3c 00 65 40 00 40 06  3d 41 0a 00 01 0a 0a 00   |.<.e@.@.=A......|
0020   02 14 b2 6e 00 50 00 00  03 e8 00 00 01 f5 50 18   |...n.P........P.|
0030   fa f0 00 00 00 00 4e 45  54 4c 41 42 5f 4c 34       |......NETLAB_L4|`

  const copyHex = () => {
    navigator.clipboard.writeText(hexDump)
    setCopied(true)
    toast("Hex dump nusxalandi", 'ok')
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <WindowFrame
      win={win}
      icon={<Layers className="text-primary" />}
      title="Cisco / OPNET PDU Inspektori"
      sub={`OSI 1–7 Qatlamlar Tahlili · ${proto} · ${clock(pkt.ts || Date.now() / 1000)}`}
    >
      <div className="flex h-full min-h-0 flex-1 flex-col overflow-hidden bg-background/50">
        {/* Yuqori boshqaruv paneli */}
        <div className="flex flex-wrap items-center justify-between gap-2 border-b bg-card/40 px-3 py-2">
          <Segmented
            value={tab}
            onChange={setTab}
            items={[
              { value: 'osi', label: 'OSI Modeli (1-7)' },
              { value: 'details', label: 'PDU Sarlavhalari' },
              { value: 'hex', label: 'Hex / ASCII Dump' },
            ]}
          />

          <div className="flex items-center gap-2">
            <Badge
              variant="outline"
              className="font-mono text-xs"
              style={{ color: PROTO_COLORS[proto] || PROTO_COLORS.other }}
            >
              {proto}
            </Badge>
            <span className="font-mono text-xs text-muted-foreground">{link}</span>
          </div>
        </div>

        {/* Asosiy kontent */}
        <div className="min-h-0 flex-1 overflow-auto p-3.5">
          {tab === 'osi' && (
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
              {/* Chap taraf (7 ustun): OSI 1-7 In vs Out Qatlamlari */}
              <div className="flex flex-col gap-3 lg:col-span-7">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground uppercase">
                    <Network className="h-4 w-4" /> Qatlamlar Dekonstruktsiyasi
                  </div>
                  <Segmented
                    value={osiDirection}
                    onChange={setOsiDirection}
                    items={[
                      { value: 'in', label: 'In Layers (Kiruvchi)' },
                      { value: 'out', label: 'Out Layers (Chiquvchi)' },
                      { value: 'both', label: 'Taqqoslash' },
                    ]}
                  />
                </div>

                <div className="space-y-2.5">
                  {/* Layer 7: Application */}
                  <LayerCard
                    layerKey="l7"
                    number={7}
                    name="Application (Ilova)"
                    color="border-l-purple-500 text-purple-400"
                    inData={inLayers.l7}
                    outData={outLayers.l7}
                    mode={osiDirection}
                    selected={selectedLayer === 'l7'}
                    onSelect={() => setSelectedLayer('l7')}
                  />

                  {/* Layer 4: Transport */}
                  <LayerCard
                    layerKey="l4"
                    number={4}
                    name="Transport"
                    color="border-l-blue-500 text-blue-400"
                    inData={inLayers.l4}
                    outData={outLayers.l4}
                    mode={osiDirection}
                    selected={selectedLayer === 'l4'}
                    onSelect={() => setSelectedLayer('l4')}
                  />

                  {/* Layer 3: Network */}
                  <LayerCard
                    layerKey="l3"
                    number={3}
                    name="Network (Tarmoq)"
                    color="border-l-emerald-500 text-emerald-400"
                    inData={inLayers.l3}
                    outData={outLayers.l3}
                    mode={osiDirection}
                    selected={selectedLayer === 'l3'}
                    onSelect={() => setSelectedLayer('l3')}
                  />

                  {/* Layer 2: Data Link */}
                  <LayerCard
                    layerKey="l2"
                    number={2}
                    name="Data Link (Kanal)"
                    color="border-l-amber-500 text-amber-400"
                    inData={inLayers.l2}
                    outData={outLayers.l2}
                    mode={osiDirection}
                    selected={selectedLayer === 'l2'}
                    onSelect={() => setSelectedLayer('l2')}
                  />

                  {/* Layer 1: Physical */}
                  <LayerCard
                    layerKey="l1"
                    number={1}
                    name="Physical (Jismoniy)"
                    color="border-l-slate-500 text-slate-400"
                    inData={inLayers.l1}
                    outData={outLayers.l1}
                    mode={osiDirection}
                    selected={selectedLayer === 'l1'}
                    onSelect={() => setSelectedLayer('l1')}
                  />
                </div>
              </div>

              {/* O'ng taraf (5 ustun): Cisco uslubidagi Device Processing Log */}
              <div className="flex flex-col gap-2.5 lg:col-span-5">
                <div className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground uppercase">
                  <ShieldCheck className="h-4 w-4" /> Qurilma Harakatlari Jurnali (Device Processing Log)
                </div>

                <div className="flex-1 rounded-lg border bg-card/40 p-3 font-mono text-xs leading-relaxed">
                  <div className="space-y-2">
                    {deviceLogs.map((log, idx) => (
                      <div
                        key={idx}
                        className="rounded border border-border/30 bg-card/60 p-2 text-foreground/90 transition-colors hover:border-primary/40"
                      >
                        {log}
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          )}

          {tab === 'details' && (
            <div className="space-y-3 font-mono text-xs">
              {/* Ethernet II */}
              <Card className="bg-card/40">
                <CardContent className="p-3">
                  <div className="mb-2 font-bold text-sm text-foreground border-b pb-1 font-sans">
                    Ethernet II Freym Maydonlari (Layer 2)
                  </div>
                  <div className="grid grid-cols-2 gap-y-1 text-muted-foreground">
                    <div>Destination MAC: <b className="text-foreground">{inLayers.l2?.dst_mac}</b></div>
                    <div>Source MAC: <b className="text-foreground">{inLayers.l2?.src_mac}</b></div>
                    <div>EtherType: <b className="text-foreground">{inLayers.l2?.ethertype}</b></div>
                    <div>FCS / CRC: <span className="text-emerald-400">0x00000000 (To'g'ri)</span></div>
                  </div>
                </CardContent>
              </Card>

              {/* IPv4 */}
              <Card className="bg-card/40">
                <CardContent className="p-3">
                  <div className="mb-2 font-bold text-sm text-foreground border-b pb-1 font-sans">
                    IPv4 Sarlavhasi (Layer 3)
                  </div>
                  <div className="grid grid-cols-2 gap-y-1 text-muted-foreground">
                    <div>Source IP: <b className="text-foreground">{inLayers.l3?.src_ip}</b></div>
                    <div>Destination IP: <b className="text-foreground">{inLayers.l3?.dst_ip}</b></div>
                    <div>TTL (Time to Live): <b className="text-foreground">{inLayers.l3?.ttl}</b></div>
                    <div>Protocol: <b className="text-foreground">{inLayers.l3?.protocol}</b></div>
                    <div>Header Checksum: <span className="text-emerald-400">{inLayers.l3?.checksum}</span></div>
                    <div>Total Length: <span className="text-foreground">{inLayers.l3?.total_len || 60} bayt</span></div>
                  </div>
                </CardContent>
              </Card>

              {/* L4 TCP/UDP */}
              <Card className="bg-card/40">
                <CardContent className="p-3">
                  <div className="mb-2 font-bold text-sm text-foreground border-b pb-1 font-sans">
                    {proto} Transport Segmenti (Layer 4)
                  </div>
                  <div className="grid grid-cols-2 gap-y-1 text-muted-foreground">
                    <div>Source Port: <b className="text-foreground">{inLayers.l4?.src_port}</b></div>
                    <div>Destination Port: <b className="text-foreground">{inLayers.l4?.dst_port}</b></div>
                    {inLayers.l4?.seq != null && <div>Sequence Number: <span className="text-foreground">{inLayers.l4.seq}</span></div>}
                    {inLayers.l4?.ack != null && <div>Acknowledgment Number: <span className="text-foreground">{inLayers.l4.ack}</span></div>}
                    {inLayers.l4?.window != null && <div>Window Size: <span className="text-foreground">{inLayers.l4.window}</span></div>}
                    {inLayers.l4?.flags && <div>Flags: <span className="text-sky-400 font-bold">[{inLayers.l4.flags.join(', ')}]</span></div>}
                  </div>
                </CardContent>
              </Card>
            </div>
          )}

          {tab === 'hex' && (
            <div className="rounded-lg border bg-card/40 p-3 font-mono text-xs">
              <div className="mb-2 flex items-center justify-between border-b pb-1">
                <div className="flex items-center gap-2 font-bold text-sm text-foreground font-sans">
                  <Binary className="h-4 w-4" /> Wireshark Hex & ASCII Dekoder
                </div>
                <Button size="xs" variant="outline" onClick={copyHex}>
                  {copied ? <Check className="mr-1 h-3 w-3 text-success" /> : <Copy className="mr-1 h-3 w-3" />}
                  {copied ? 'Nusxalandi' : 'Nusxa olish'}
                </Button>
              </div>
              <pre className="overflow-x-auto leading-relaxed text-muted-foreground font-mono text-[11.5px]">
                {hexDump}
              </pre>
            </div>
          )}
        </div>
      </div>
    </WindowFrame>
  )
}

function LayerCard({
  layerKey,
  number,
  name,
  color,
  inData,
  outData,
  mode,
  selected,
  onSelect,
}: {
  layerKey: string
  number: number
  name: string
  color: string
  inData?: LayerData
  outData?: LayerData
  mode: 'in' | 'out' | 'both'
  selected: boolean
  onSelect: () => void
}) {
  const isBoth = mode === 'both'
  const isOut = mode === 'out'
  const cur = isOut ? outData : inData

  return (
    <div
      onClick={onSelect}
      className={cn(
        'cursor-pointer rounded-lg border bg-card/70 p-2.5 transition-all border-l-4 hover:bg-accent/40',
        color,
        selected && 'ring-1 ring-primary/50 bg-card'
      )}
    >
      <div className="flex items-center justify-between font-bold text-xs">
        <span>Layer {number}: {name}</span>
        {isBoth && <span className="text-[10px] text-muted-foreground font-mono">In vs Out</span>}
      </div>

      <div className="mt-1 font-mono text-[11.5px] text-muted-foreground">
        {isBoth ? (
          <div className="grid grid-cols-2 gap-2 text-[10.5px]">
            <div className="border-r pr-1">
              <div className="text-[10px] font-bold text-foreground">In:</div>
              {renderLayerSummary(layerKey, inData)}
            </div>
            <div>
              <div className="text-[10px] font-bold text-foreground">Out:</div>
              {renderLayerSummary(layerKey, outData)}
            </div>
          </div>
        ) : (
          <div>{renderLayerSummary(layerKey, cur)}</div>
        )}
      </div>
    </div>
  )
}

function renderLayerSummary(key: string, d?: LayerData) {
  if (!d) return '—'
  if (key === 'l1') {
    return <span>{d.port || 'eth0'} · {d.media || '100Base-TX'} · {d.status || 'UP'}</span>
  }
  if (key === 'l2') {
    return <span>Src: <b>{d.src_mac}</b> → Dst: <b>{d.dst_mac}</b></span>
  }
  if (key === 'l3') {
    return (
      <div className="flex flex-wrap gap-x-2">
        <span>Src: <b>{d.src_ip}</b></span>
        <span>Dst: <b>{d.dst_ip}</b></span>
        <span>TTL: <b>{d.ttl}</b></span>
      </div>
    )
  }
  if (key === 'l4') {
    return (
      <div className="flex flex-wrap gap-x-2">
        <span>Port: <b>{d.src_port} → {d.dst_port}</b></span>
        {d.flags && <span>Flags: <b>[{d.flags.join(',')}]</b></span>}
        {d.seq != null && <span>Seq: {d.seq}</span>}
      </div>
    )
  }
  return <span>{d.summary || d.app || 'Payload'}</span>
}
