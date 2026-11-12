import { useEffect, useState } from 'react'
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  BarChart3,
  Binary,
  Check,
  CheckCircle2,
  Clock,
  Copy,
  Flame,
  Layers,
  Network,
  ShieldCheck,
  Zap,
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

export interface LayerData {
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
  session_id?: string
  encoding?: string
  encryption?: string
}

export interface PDUInspectionResponse {
  device: string
  device_type: string
  timestamp: number
  proto: string
  summary: string
  in_layers: Record<string, LayerData>
  out_layers: Record<string, LayerData>
  device_logs: string[]
  hex_dump: string
  out_hex_dump?: string
}

export function PDUInspectorWindow({ win }: { win: Win }) {
  const [tab, setTab] = useState<'osi' | 'details' | 'hex' | 'p4int'>('osi')
  const [osiDirection, setOsiDirection] = useState<'in' | 'out' | 'both'>('in')
  const [selectedLayer, setSelectedLayer] = useState<string>('l3')
  const [activeStepIdx, setActiveStepIdx] = useState<number | null>(null)
  const [pduDetailsMode, setPduDetailsMode] = useState<'out' | 'in'>('out')
  const [hexMode, setHexMode] = useState<'in' | 'out'>('in')
  const [data, setData] = useState<PDUInspectionResponse | null>(null)
  const [copied, setCopied] = useState(false)
  const [intReport, setIntReport] = useState<any>(null)
  const [intLoading, setIntLoading] = useState(false)
  const [burstSwitch, setBurstSwitch] = useState<string>('s2')

  const runINTSimulation = async (burst = false) => {
    setIntLoading(true)
    try {
      const res = await api.post<{ ok: boolean; header: any; report: any }>('/api/telemetry/p4/int/simulate', {
        path: ['s1', 's2', 's3', 's4'],
        flow_id: `${pkt.src_ip || '10.0.1.10'} -> ${pkt.dst_ip || '10.0.2.20'}`,
        base_hop_latency_us: 12.0,
        burst_switch: burst ? burstSwitch : undefined,
        burst_size_pkts: burst ? 70 : 0,
        traffic_load_pct: 35.0,
      })
      if (res.ok) {
        setIntReport(res.report)
        toast(burst ? "P4 INT mikro-portlash simulyatsiya qilindi" : "P4 INT oqimi o'lchandi", 'ok')
      }
    } catch (e: any) {
      toast(`P4 INT xatolik: ${e.message}`, 'error')
    } finally {
      setIntLoading(false)
    }
  }

  // P4 INT tabiga o'tganda agar hisobot yo'q bo'lsa avtomatik simulyatsiya qilish
  useEffect(() => {
    if (tab === 'p4int' && !intReport && !intLoading) {
      runINTSimulation(false)
    }
  }, [tab])

  const pkt = win.arg || {}
  const proto = (pkt.proto || 'TCP').toUpperCase()
  const summary = pkt.summary || `${proto} Packet`
  const link = pkt.link || 'eth0'
  const isDropped = Boolean(pkt.dropped)

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

  // Kiruvchi 7-qatlamli OSI modeli
  const inLayers: Record<string, LayerData> = {
    l1: {
      name: 'Layer 1: Physical (Jismoniy)',
      port: `${link} (Inbound Rx)`,
      media: 'Ethernet 100Base-TX / Full-Duplex',
      status: 'UP / Line Protocol Active',
      ...(data?.in_layers?.l1 || {}),
    },
    l2: {
      name: 'Layer 2: Data Link (Kanal)',
      src_mac: data?.in_layers?.l2?.src_mac || '00:11:22:33:44:55',
      dst_mac: data?.in_layers?.l2?.dst_mac || '00:aa:bb:cc:dd:ee',
      ethertype: proto === 'ARP' ? '0x0806 (ARP)' : '0x0800 (IPv4)',
      status: 'Frame Check Sequence (FCS) Valid',
      ...(data?.in_layers?.l2 || {}),
    },
    l3: {
      name: 'Layer 3: Network (Tarmoq)',
      src_ip: data?.in_layers?.l3?.src_ip || '10.0.1.10',
      dst_ip: data?.in_layers?.l3?.dst_ip || '10.0.2.20',
      ttl: data?.in_layers?.l3?.ttl ?? 64,
      protocol: `${proto} (Proto ${proto === 'TCP' ? 6 : proto === 'UDP' ? 17 : 1})`,
      checksum: '0x4a2b (Valid)',
      total_len: 60,
      ...(data?.in_layers?.l3 || {}),
    },
    l4: {
      name: 'Layer 4: Transport',
      proto,
      src_port: data?.in_layers?.l4?.src_port ?? 45678,
      dst_port: data?.in_layers?.l4?.dst_port ?? (proto === 'TCP' ? 80 : 53),
      seq: proto === 'TCP' ? data?.in_layers?.l4?.seq ?? 1000 : null,
      ack: proto === 'TCP' ? data?.in_layers?.l4?.ack ?? 501 : null,
      flags: proto === 'TCP' ? data?.in_layers?.l4?.flags ?? ['ACK', 'PSH'] : [],
      window: proto === 'TCP' ? data?.in_layers?.l4?.window ?? 64240 : null,
      ...(data?.in_layers?.l4 || {}),
    },
    l5: {
      name: 'Layer 5: Session (Sessiya)',
      session_id: 'TCP Socket (ESTABLISHED)',
      status: 'Aktiv dupleks sessiya',
      ...(data?.in_layers?.l5 || {}),
    },
    l6: {
      name: 'Layer 6: Presentation (Taqdimot)',
      encoding: 'ASCII / Binary Stream',
      encryption: 'Plaintext (Unencrypted)',
      ...(data?.in_layers?.l6 || {}),
    },
    l7: {
      name: 'Layer 7: Application (Ilova)',
      app: proto === 'TCP' ? 'HTTP/1.1' : proto,
      summary,
      ...(data?.in_layers?.l7 || {}),
    },
  }

  // Chiquvchi 7-qatlamli OSI modeli (Router orqali qayta inkapsulyatsiya qilingan)
  const outLayers: Record<string, LayerData> = {
    l1: {
      name: 'Layer 1: Physical (Chiquvchi)',
      port: 'eth1 (Outbound Tx)',
      media: 'Ethernet 100Base-TX / Full-Duplex',
      status: 'Transmitting / Port Queue Ready',
      ...(data?.out_layers?.l1 || {}),
    },
    l2: {
      name: 'Layer 2: Data Link (Chiquvchi Freym)',
      src_mac: '00:1c:73:00:00:01 (Router R1 eth1)',
      dst_mac: '00:50:56:c0:00:08 (Next-Hop Host MAC)',
      ethertype: proto === 'ARP' ? '0x0806 (ARP)' : '0x0800 (IPv4)',
      status: 'Frame Check Sequence (FCS) Recalculated',
      ...(data?.out_layers?.l2 || {}),
    },
    l3: {
      name: 'Layer 3: Network (Tarmoq)',
      src_ip: inLayers.l3.src_ip,
      dst_ip: inLayers.l3.dst_ip,
      ttl: Math.max(1, (inLayers.l3.ttl || 64) - 1),
      protocol: inLayers.l3.protocol,
      checksum: '0x5b3c (Recalculated)',
      total_len: inLayers.l3.total_len || 60,
      ...(data?.out_layers?.l3 || {}),
    },
    l4: {
      name: 'Layer 4: Transport',
      ...inLayers.l4,
      ...(data?.out_layers?.l4 || {}),
    },
    l5: {
      name: 'Layer 5: Session (Sessiya)',
      ...inLayers.l5,
      ...(data?.out_layers?.l5 || {}),
    },
    l6: {
      name: 'Layer 6: Presentation (Taqdimot)',
      ...inLayers.l6,
      ...(data?.out_layers?.l6 || {}),
    },
    l7: {
      name: 'Layer 7: Application (Ilova)',
      ...inLayers.l7,
      ...(data?.out_layers?.l7 || {}),
    },
  }

  // Cisco uslubidagi Device Processing Log (FIB lookup, TTL decrement, Checksum recalc, ARP next-hop resolution)
  const deviceProcessingSteps = [
    {
      title: '1. Port qabuli (Physical Ingress)',
      badge: 'INGRESS',
      layer: 'l1',
      desc: `Freym '${link}' interfeysiga to'liq yetib keldi. Jismoniy signal va liniya kodi (100Base-TX) normal.`,
    },
    {
      title: '2. Layer 2 Freym tekshiruvi & FCS',
      badge: 'L2 FCS OK',
      layer: 'l2',
      desc: `Freym nazorat yig'indisi (FCS) to'g'ri. Maqsad MAC (${inLayers.l2.dst_mac}) qurilma interfeysi MAC manziliga mos keldi va qabul qilindi.`,
    },
    {
      title: '3. Layer 3 Sarlavha tekshiruvi',
      badge: 'L3 VERIFY',
      layer: 'l3',
      desc: `IPv4 sarlavhasi tekshirildi. Version=4, IHL=20B, IP nazorat yig'indisi (${inLayers.l3.checksum}) to'g'ri. Kiruvchi TTL=${inLayers.l3.ttl} > 1.`,
    },
    {
      title: '4. Marshrutlash va FIB lookup (Routing Table)',
      badge: 'FIB MATCH',
      layer: 'l3',
      desc: `FIB (Forwarding Information Base) jadvalidan maqsad IP (${inLayers.l3.dst_ip}) uchun Longest Prefix Match (LPM) qidirildi. Next-Hop IP: 10.0.2.1, chiqish interfeysi: 'eth1'.`,
    },
    {
      title: '5. TTL dekrementi va Checksum qayta hisoblash',
      badge: 'TTL -1',
      layer: 'l3',
      desc: `RFC 791 qoidasiga ko'ra TTL qiymati ${inLayers.l3.ttl} dan ${outLayers.l3.ttl} ga tushirildi. RFC 1624 inkremental formulasi orqali IP checksum ${inLayers.l3.checksum} dan ${outLayers.l3.checksum} ga yangilandi.`,
    },
    {
      title: '6. ARP Next-Hop MAC aniqlash (ARP Resolution)',
      badge: 'ARP RESOLVED',
      layer: 'l2',
      desc: `Next-Hop IP (10.0.2.1) uchun ARP jadvali (ARP Cache) tekshirildi. Maqsad uskuna MAC manzili aniqlandi: '${outLayers.l2.dst_mac}'.`,
    },
    {
      title: '7. Layer 2 Qayta inkapsulyatsiya (Encapsulation)',
      badge: 'L2 ENCAP',
      layer: 'l2',
      desc: `Yangi Ethernet II freym sarlavhasi qurildi: Src MAC='${outLayers.l2.src_mac}', Dst MAC='${outLayers.l2.dst_mac}', EtherType=0x0800. FCS qayta hisoblandi.`,
    },
    {
      title: '8. Chiqish navbati (Egress Queuing)',
      badge: 'EGRESS',
      layer: 'l1',
      desc: `Yangi freym chiquvchi 'eth1' portining Tx FIFO navbatiga qo'yildi va uzatishga tayyorlandi.`,
    },
  ]

  const hexIn =
    data?.hex_dump ||
    `0000   00 aa bb cc dd ee 00 11  22 33 44 55 08 00 45 00   |..........3DU..E.|
0010   00 3c 00 65 40 00 40 06  4a 2b 0a 00 01 0a 0a 00   |.<.e@.@.J+......|
0020   02 14 b2 6e 00 50 00 00  03 e8 00 00 01 f5 50 18   |...n.P........P.|
0030   fa f0 00 00 00 00 4e 45  54 4c 41 42 5f 4c 34       |......NETLAB_L4|`

  const hexOut =
    data?.out_hex_dump ||
    `0000   00 50 56 c0 00 08 00 1c  73 00 00 01 08 00 45 00   |.PV.....s.....E.|
0010   00 3c 00 65 40 00 3f 06  5b 3c 0a 00 01 0a 0a 00   |.<.e@.?.[\<......|
0020   02 14 b2 6e 00 50 00 00  03 e8 00 00 01 f5 50 18   |...n.P........P.|
0030   fa f0 00 00 00 00 4e 45  54 4c 41 42 5f 4c 34       |......NETLAB_L4|`

  const currentHex = hexMode === 'out' ? hexOut : hexIn

  const copyHex = () => {
    navigator.clipboard.writeText(currentHex)
    setCopied(true)
    toast('Hex dump nusxalandi', 'ok')
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <WindowFrame
      win={win}
      icon={<Layers className="text-primary" />}
      title="Cisco / OPNET PDU Inspektori"
      sub={`OSI 1–7 Qatlamlar Taqqoslashi · ${proto} · ${clock(pkt.ts || Date.now() / 1000)}`}
    >
      <div className="flex h-full min-h-0 flex-1 flex-col overflow-hidden bg-background/50">
        {/* Yuqori boshqaruv paneli */}
        <div className="flex flex-wrap items-center justify-between gap-2 border-b bg-card/40 px-3 py-2 shadow-sm">
          <Segmented
            value={tab}
            onChange={setTab}
            items={[
              { value: 'osi', label: '7-Qatlamli OSI Modeli' },
              { value: 'details', label: 'PDU Sarlavhalari (Headers)' },
              { value: 'hex', label: 'Wireshark Hex / ASCII' },
              { value: 'p4int', label: 'P4 INT Telemetriya' },
            ]}
          />

          <div className="flex items-center gap-2">
            {isDropped ? (
              <Badge variant="destructive" className="font-mono text-xs animate-pulse">
                <AlertTriangle className="mr-1 h-3 w-3" /> DROPPED
              </Badge>
            ) : (
              <Badge variant="outline" className="text-emerald-400 border-emerald-500/30 text-xs">
                <CheckCircle2 className="mr-1 h-3 w-3" /> FORWARDED
              </Badge>
            )}
            <Badge
              variant="outline"
              className="font-mono text-xs font-bold"
              style={{ color: PROTO_COLORS[proto] || PROTO_COLORS.other }}
            >
              {proto}
            </Badge>
            <span className="font-mono text-xs text-muted-foreground">{link}</span>
          </div>
        </div>

        {/* Asosiy kontent */}
        <div className="min-h-0 flex-1 overflow-auto p-3.5">
          {/* TAB 1: 7-LAYER OSI MODEL COMPARISON */}
          {tab === 'osi' && (
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
              {/* Chap taraf (7 ustun): OSI 1-7 In vs Out Qatlamlari */}
              <div className="flex flex-col gap-3 lg:col-span-7">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground uppercase">
                    <Network className="h-4 w-4 text-primary" /> OSI 1–7 Qatlamlar Dekonstruktsiyasi
                  </div>
                  <Segmented
                    value={osiDirection}
                    onChange={setOsiDirection}
                    items={[
                      { value: 'in', label: 'In Layers (Kiruvchi)' },
                      { value: 'out', label: 'Out Layers (Chiquvchi)' },
                      { value: 'both', label: 'Ikkalasini Taqqoslash' },
                    ]}
                  />
                </div>

                <div className="space-y-2">
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

                  {/* Layer 6: Presentation */}
                  <LayerCard
                    layerKey="l6"
                    number={6}
                    name="Presentation (Taqdimot)"
                    color="border-l-indigo-500 text-indigo-400"
                    inData={inLayers.l6}
                    outData={outLayers.l6}
                    mode={osiDirection}
                    selected={selectedLayer === 'l6'}
                    onSelect={() => setSelectedLayer('l6')}
                  />

                  {/* Layer 5: Session */}
                  <LayerCard
                    layerKey="l5"
                    number={5}
                    name="Session (Sessiya)"
                    color="border-l-cyan-500 text-cyan-400"
                    inData={inLayers.l5}
                    outData={outLayers.l5}
                    mode={osiDirection}
                    selected={selectedLayer === 'l5'}
                    onSelect={() => setSelectedLayer('l5')}
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

              {/* O'ng taraf (5 ustun): Cisco uslubidagi Step-by-Step Device Processing Log */}
              <div className="flex flex-col gap-2.5 lg:col-span-5">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground uppercase">
                    <ShieldCheck className="h-4 w-4 text-primary" /> Qurilma Harakatlari Jurnali (Device Processing Log)
                  </div>
                  <span className="text-[11px] text-muted-foreground font-mono">
                    {deviceProcessingSteps.length} ta qadam
                  </span>
                </div>

                <div className="flex-1 space-y-2 overflow-auto rounded-lg border bg-card/40 p-2.5 font-mono text-xs">
                  {deviceProcessingSteps.map((step, idx) => {
                    const isActive = activeStepIdx === idx
                    return (
                      <div
                        key={idx}
                        onClick={() => {
                          setActiveStepIdx(isActive ? null : idx)
                          setSelectedLayer(step.layer)
                        }}
                        className={cn(
                          'cursor-pointer rounded border p-2 text-foreground/90 transition-all',
                          isActive
                            ? 'border-primary bg-primary/10 shadow-sm ring-1 ring-primary/40'
                            : 'border-border/40 bg-card/60 hover:border-primary/40 hover:bg-card/90'
                        )}
                      >
                        <div className="flex items-center justify-between font-bold text-[11px]">
                          <span className="text-foreground">{step.title}</span>
                          <span className="rounded bg-primary/20 px-1 py-0.5 text-[9.5px] text-primary">
                            {step.badge}
                          </span>
                        </div>
                        <div className="mt-1 font-sans text-[11px] leading-relaxed text-muted-foreground">
                          {step.desc}
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>
            </div>
          )}

          {/* TAB 2: OUTBOUND PDU DETAILS HEADER BREAKDOWN */}
          {tab === 'details' && (
            <div className="space-y-3 font-mono text-xs">
              {/* Inbound vs Outbound Tanlash */}
              <div className="flex items-center justify-between border-b pb-2">
                <div className="flex items-center gap-2">
                  <Segmented
                    value={pduDetailsMode}
                    onChange={setPduDetailsMode}
                    items={[
                      { value: 'out', label: 'Outbound PDU Details (Chiquvchi Freym)' },
                      { value: 'in', label: 'Inbound PDU Details (Kiruvchi Freym)' },
                    ]}
                  />
                  {pduDetailsMode === 'out' && (
                    <Badge variant="outline" className="border-sky-500/30 text-sky-400 text-[11px]">
                      Router R1 tomonidan modifikatsiya qilingan
                    </Badge>
                  )}
                </div>
                <span className="text-[11px] text-muted-foreground">
                  Interfeys: <b>{pduDetailsMode === 'out' ? 'eth1 (Tx)' : `${link} (Rx)`}</b>
                </span>
              </div>

              {/* Layer 2: Ethernet II */}
              <Card className="bg-card/40 border-l-4 border-l-amber-500">
                <CardContent className="p-3">
                  <div className="mb-2 flex items-center justify-between border-b pb-1">
                    <span className="font-bold text-sm text-foreground font-sans">
                      Ethernet II Freym Maydonlari (Layer 2)
                    </span>
                    <span className="text-[11px] text-muted-foreground">
                      Uzunlik: 14 bayt sarlavha + 4 bayt FCS
                    </span>
                  </div>
                  <div className="grid grid-cols-1 gap-y-1 sm:grid-cols-2 text-muted-foreground">
                    <div>
                      Destination MAC:{' '}
                      <b className={cn('text-foreground', pduDetailsMode === 'out' && 'text-amber-400')}>
                        {pduDetailsMode === 'out' ? outLayers.l2.dst_mac : inLayers.l2.dst_mac}
                      </b>
                      {pduDetailsMode === 'out' && (
                        <span className="ml-1 text-[10px] text-emerald-400 font-bold">[Next-Hop ARP]</span>
                      )}
                    </div>
                    <div>
                      Source MAC:{' '}
                      <b className={cn('text-foreground', pduDetailsMode === 'out' && 'text-sky-400')}>
                        {pduDetailsMode === 'out' ? outLayers.l2.src_mac : inLayers.l2.src_mac}
                      </b>
                      {pduDetailsMode === 'out' && (
                        <span className="ml-1 text-[10px] text-sky-400 font-bold">[R1 eth1]</span>
                      )}
                    </div>
                    <div>
                      EtherType: <b className="text-foreground">{inLayers.l2.ethertype}</b>
                    </div>
                    <div>
                      FCS / CRC:{' '}
                      <span className="text-emerald-400 font-bold">
                        {pduDetailsMode === 'out' ? '0x3a1f9c02 (Recalculated)' : '0x4e29b10a (Valid)'}
                      </span>
                    </div>
                  </div>
                </CardContent>
              </Card>

              {/* Layer 3: IPv4 Packet Header */}
              <Card className="bg-card/40 border-l-4 border-l-emerald-500">
                <CardContent className="p-3">
                  <div className="mb-2 flex items-center justify-between border-b pb-1">
                    <span className="font-bold text-sm text-foreground font-sans">
                      IPv4 Sarlavhasi (Layer 3 Header Breakdown)
                    </span>
                    <span className="text-[11px] text-muted-foreground">Uzunlik: 20 bayt</span>
                  </div>
                  <div className="grid grid-cols-2 gap-y-1.5 sm:grid-cols-4 text-muted-foreground">
                    <div>
                      Version: <b className="text-foreground">4</b>
                    </div>
                    <div>
                      Header Length (IHL): <b className="text-foreground">20 bayt (5)</b>
                    </div>
                    <div>
                      Type of Service (DSCP): <b className="text-foreground">0x00 (CS0)</b>
                    </div>
                    <div>
                      Total Length: <b className="text-foreground">{inLayers.l3.total_len || 60} bayt</b>
                    </div>

                    <div>
                      Identification: <b className="text-foreground">0x0065 (101)</b>
                    </div>
                    <div>
                      Flags: <b className="text-sky-400">0x40 (Don't Fragment)</b>
                    </div>
                    <div>
                      Time to Live (TTL):{' '}
                      <b
                        className={cn(
                          'text-foreground',
                          pduDetailsMode === 'out' && 'text-rose-400 font-bold'
                        )}
                      >
                        {pduDetailsMode === 'out' ? outLayers.l3.ttl : inLayers.l3.ttl}
                      </b>
                      {pduDetailsMode === 'out' && (
                        <span className="ml-1 text-[10px] text-rose-400">[-1 decrement]</span>
                      )}
                    </div>
                    <div>
                      Protocol: <b className="text-foreground">{inLayers.l3.protocol}</b>
                    </div>

                    <div className="col-span-2">
                      Source IP:{' '}
                      <b className="text-foreground font-bold">{inLayers.l3.src_ip}</b>
                    </div>
                    <div className="col-span-2">
                      Destination IP:{' '}
                      <b className="text-foreground font-bold">{inLayers.l3.dst_ip}</b>
                    </div>

                    <div className="col-span-2">
                      Header Checksum:{' '}
                      <span className="text-emerald-400 font-bold">
                        {pduDetailsMode === 'out' ? outLayers.l3.checksum : inLayers.l3.checksum}
                      </span>
                      {pduDetailsMode === 'out' && (
                        <span className="ml-1 text-[10px] text-sky-400 font-normal">
                          (RFC 1624 qayta hisoblangan)
                        </span>
                      )}
                    </div>
                    <div className="col-span-2">
                      FIB Routing Status:{' '}
                      <span className="text-emerald-400 font-bold">
                        LPM Match (Next-Hop: 10.0.2.1)
                      </span>
                    </div>
                  </div>
                </CardContent>
              </Card>

              {/* Layer 4: TCP / UDP Transport Segment */}
              <Card className="bg-card/40 border-l-4 border-l-blue-500">
                <CardContent className="p-3">
                  <div className="mb-2 flex items-center justify-between border-b pb-1">
                    <span className="font-bold text-sm text-foreground font-sans">
                      {proto} Transport Segmenti (Layer 4 Header)
                    </span>
                    <span className="text-[11px] text-muted-foreground">
                      {proto === 'TCP' ? 'Uzunlik: 20-32 bayt' : 'Uzunlik: 8 bayt'}
                    </span>
                  </div>
                  <div className="grid grid-cols-2 gap-y-1 sm:grid-cols-3 text-muted-foreground">
                    <div>
                      Source Port: <b className="text-foreground">{inLayers.l4.src_port}</b>
                    </div>
                    <div>
                      Destination Port: <b className="text-foreground">{inLayers.l4.dst_port}</b>
                    </div>
                    {inLayers.l4.seq != null && (
                      <div>
                        Sequence Number: <span className="text-foreground">{inLayers.l4.seq}</span>
                      </div>
                    )}
                    {inLayers.l4.ack != null && (
                      <div>
                        Acknowledgment Number: <span className="text-foreground">{inLayers.l4.ack}</span>
                      </div>
                    )}
                    {inLayers.l4.window != null && (
                      <div>
                        Window Size: <span className="text-foreground">{inLayers.l4.window} bayt</span>
                      </div>
                    )}
                    {inLayers.l4.flags && inLayers.l4.flags.length > 0 && (
                      <div>
                        Flags:{' '}
                        <span className="text-sky-400 font-bold">
                          [{inLayers.l4.flags.join(', ')}]
                        </span>
                      </div>
                    )}
                  </div>
                </CardContent>
              </Card>

              {/* Layer 7: Application / Payload */}
              <Card className="bg-card/40 border-l-4 border-l-purple-500">
                <CardContent className="p-3">
                  <div className="mb-1 font-bold text-sm text-foreground border-b pb-1 font-sans">
                    Ilova Qatlami Ma'lumoti (Layer 7 Payload)
                  </div>
                  <div className="text-muted-foreground">
                    <div>Xulosa: <b className="text-foreground">{summary}</b></div>
                    <div className="mt-1 text-[11px]">Hajm: ~16 bayt (NETLAB_L4 simulyatsiya ma'lumoti)</div>
                  </div>
                </CardContent>
              </Card>
            </div>
          )}

          {/* TAB 3: WIRESHARK HEX / ASCII DUMP */}
          {tab === 'hex' && (
            <div className="space-y-2.5 font-mono text-xs">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b pb-2">
                <div className="flex items-center gap-2">
                  <Binary className="h-4 w-4 text-primary" />
                  <span className="font-bold text-sm text-foreground font-sans">
                    Wireshark Hex & ASCII Dekoder
                  </span>
                  <Segmented
                    value={hexMode}
                    onChange={setHexMode}
                    items={[
                      { value: 'in', label: 'Kiruvchi Freym (Inbound)' },
                      { value: 'out', label: 'Chiquvchi Freym (Outbound)' },
                    ]}
                  />
                </div>
                <Button size="xs" variant="outline" onClick={copyHex}>
                  {copied ? <Check className="mr-1 h-3 w-3 text-emerald-400" /> : <Copy className="mr-1 h-3 w-3" />}
                  {copied ? 'Nusxalandi' : 'Hex Dump Nusxalash'}
                </Button>
              </div>

              {/* Qatlamlar rangi bo'yicha yo'riqnoma */}
              <div className="flex flex-wrap gap-3 text-[11px] text-muted-foreground">
                <span className="flex items-center gap-1">
                  <i className="h-2.5 w-2.5 rounded-sm bg-amber-500" /> Layer 2: Ethernet (0–13 B)
                </span>
                <span className="flex items-center gap-1">
                  <i className="h-2.5 w-2.5 rounded-sm bg-emerald-500" /> Layer 3: IPv4 Header (14–33 B)
                </span>
                <span className="flex items-center gap-1">
                  <i className="h-2.5 w-2.5 rounded-sm bg-blue-500" /> Layer 4: TCP/UDP (34–53 B)
                </span>
                <span className="flex items-center gap-1">
                  <i className="h-2.5 w-2.5 rounded-sm bg-purple-500" /> Layer 7: Payload (54+ B)
                </span>
              </div>

              <div className="rounded-lg border bg-card/60 p-3 shadow-inner">
                <pre className="overflow-x-auto leading-relaxed text-foreground font-mono text-[12px] tracking-wide">
                  {currentHex}
                </pre>
              </div>
            </div>
          )}

          {/* TAB 4: P4-16 IN-BAND NETWORK TELEMETRY (INT v2.1) */}
          {tab === 'p4int' && (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b pb-2">
                <div className="flex items-center gap-2">
                  <Activity className="h-4 w-4 text-cyan-400" />
                  <span className="text-sm font-bold text-foreground">
                    P4-16 In-Band Network Telemetry (INT v2.1)
                  </span>
                  <Badge variant="outline" className="border-cyan-500/30 text-cyan-400 font-mono text-[11px]">
                    Hop-by-Hop Dataplane
                  </Badge>
                </div>
                <div className="flex items-center gap-2">
                  <select
                    value={burstSwitch}
                    onChange={(e) => setBurstSwitch(e.target.value)}
                    className="h-7 rounded border border-border bg-card px-2 text-xs font-mono text-foreground"
                  >
                    <option value="s1">Burchni s1 da yaratish</option>
                    <option value="s2">Burchni s2 da yaratish</option>
                    <option value="s3">Burchni s3 da yaratish</option>
                    <option value="s4">Burchni s4 da yaratish</option>
                  </select>
                  <Button
                    size="xs"
                    variant="outline"
                    disabled={intLoading}
                    onClick={() => runINTSimulation(false)}
                  >
                    <Clock className="mr-1 h-3 w-3 text-emerald-400" /> Normal O'lchash
                  </Button>
                  <Button
                    size="xs"
                    variant="destructive"
                    disabled={intLoading}
                    onClick={() => runINTSimulation(true)}
                  >
                    <Flame className="mr-1 h-3 w-3 text-red-400" /> Mikro-Portlash Yaratish
                  </Button>
                </div>
              </div>

              {/* Xulosa metrikalar kartasi */}
              {intReport && (
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                  <Card className="border-cyan-500/20 bg-cyan-950/20 p-2.5">
                    <div className="text-[11px] text-muted-foreground flex items-center gap-1">
                      <Clock className="h-3 w-3 text-cyan-400" /> Umumiy Kechikish (E2E)
                    </div>
                    <div className="mt-1 font-mono text-lg font-bold text-cyan-300">
                      {intReport.end_to_end_latency_us} <span className="text-xs font-normal">µs</span>
                    </div>
                  </Card>
                  <Card className="border-border bg-card/40 p-2.5">
                    <div className="text-[11px] text-muted-foreground flex items-center gap-1">
                      <Layers className="h-3 w-3 text-primary" /> O'tilgan Hoplar
                    </div>
                    <div className="mt-1 font-mono text-lg font-bold text-foreground">
                      {intReport.total_hops} <span className="text-xs font-normal">switch</span>
                    </div>
                  </Card>
                  <Card className="border-border bg-card/40 p-2.5">
                    <div className="text-[11px] text-muted-foreground flex items-center gap-1">
                      <BarChart3 className="h-3 w-3 text-amber-400" /> Max Navbat Hajmi
                    </div>
                    <div className="mt-1 font-mono text-lg font-bold text-amber-300">
                      {intReport.max_queue_depth_pkts} <span className="text-xs font-normal">paket</span>
                    </div>
                  </Card>
                  <Card className={cn(
                    "p-2.5 border",
                    intReport.microburst_detected
                      ? "border-red-500/30 bg-red-950/20"
                      : "border-emerald-500/20 bg-emerald-950/20"
                  )}>
                    <div className="text-[11px] text-muted-foreground flex items-center gap-1">
                      <Zap className="h-3 w-3" /> Tarmoq Holati
                    </div>
                    <div className="mt-1 font-mono text-xs font-bold flex items-center gap-1.5">
                      {intReport.microburst_detected ? (
                        <span className="text-red-400">🔥 Mikro-Portlash ({intReport.bottleneck_switch})</span>
                      ) : (
                        <span className="text-emerald-400">✓ Optimal Oqim</span>
                      )}
                    </div>
                  </Card>
                </div>
              )}

              {/* Hop-by-Hop Vizual Yo'nalish */}
              {intReport && intReport.hops && (
                <div className="space-y-3">
                  <div className="text-xs font-semibold text-muted-foreground uppercase flex items-center gap-1.5">
                    <ArrowRight className="h-4 w-4 text-primary" /> P4 Switchlar Zanjiri (Hop-by-Hop Telemetry Headers)
                  </div>

                  <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
                    {intReport.hops.map((h: any, idx: number) => {
                      const isHot = h.queue_occupancy_pkts > 40 || h.hop_latency_us > 50
                      const qPct = Math.min(100, Math.round((h.queue_occupancy_pkts / 80) * 100))
                      return (
                        <div
                          key={h.switch_id + idx}
                          className={cn(
                            "relative flex flex-col justify-between rounded-lg border p-3 transition-all",
                            isHot
                              ? "border-red-500/40 bg-red-950/15 shadow-red-900/10"
                              : "border-border bg-card/60"
                          )}
                        >
                          <div className="flex items-center justify-between border-b pb-2">
                            <div className="flex items-center gap-1.5 font-bold font-mono text-sm">
                              <span className="flex h-5 w-5 items-center justify-center rounded bg-primary/20 text-[11px] text-primary">
                                {idx + 1}
                              </span>
                              <span className="text-foreground">{h.switch_id.toUpperCase()}</span>
                            </div>
                            <div className="flex items-center gap-1 text-[11px] font-mono text-muted-foreground">
                              <span>p{h.ingress_port}</span>
                              <ArrowRight className="h-3 w-3" />
                              <span>p{h.egress_port}</span>
                            </div>
                          </div>

                          <div className="my-2 space-y-1.5 text-xs font-mono">
                            <div className="flex justify-between">
                              <span className="text-muted-foreground">Hop Latency:</span>
                              <span className={cn("font-bold", isHot ? "text-red-400" : "text-emerald-400")}>
                                {h.hop_latency_us} µs
                              </span>
                            </div>
                            <div className="flex justify-between">
                              <span className="text-muted-foreground">Navbat:</span>
                              <span className="text-foreground">{h.queue_occupancy_pkts} pkt ({Math.round(h.queue_occupancy_bytes / 1024)} KB)</span>
                            </div>
                            <div className="flex justify-between">
                              <span className="text-muted-foreground">Port bandligi:</span>
                              <span className="text-foreground">{h.tx_utilization_pct}%</span>
                            </div>

                            {/* Navbat progress bari */}
                            <div className="mt-1 h-1.5 w-full rounded-full bg-border overflow-hidden">
                              <div
                                className={cn(
                                  "h-full rounded-full transition-all duration-300",
                                  qPct > 60 ? "bg-red-500" : qPct > 30 ? "bg-amber-500" : "bg-emerald-500"
                                )}
                                style={{ width: `${qPct}%` }}
                              />
                            </div>
                          </div>

                          <div className="border-t pt-1.5 flex items-center justify-between text-[10px] font-mono text-muted-foreground">
                            <span>QID: {h.queue_id}</span>
                            {isHot && (
                              <span className="text-red-400 font-bold animate-pulse">! Congestion</span>
                            )}
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </div>
              )}
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
        selected && 'ring-1 ring-primary/50 bg-card shadow-sm'
      )}
    >
      <div className="flex items-center justify-between font-bold text-xs">
        <span>Layer {number}: {name}</span>
        {isBoth && <span className="text-[10px] text-muted-foreground font-mono">In vs Out</span>}
      </div>

      <div className="mt-1 font-mono text-[11.5px] text-muted-foreground">
        {isBoth ? (
          <div className="grid grid-cols-2 gap-2 text-[10.5px]">
            <div className="border-r border-border/40 pr-1.5">
              <div className="text-[10px] font-bold text-foreground">Kiruvchi (In):</div>
              {renderLayerSummary(layerKey, inData)}
            </div>
            <div>
              <div className="text-[10px] font-bold text-foreground">Chiquvchi (Out):</div>
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
        {d.flags && d.flags.length > 0 && <span>Flags: <b>[{d.flags.join(',')}]</b></span>}
        {d.seq != null && <span>Seq: {d.seq}</span>}
      </div>
    )
  }
  if (key === 'l5') {
    return <span>{d.session_id || 'TCP Socket (ESTABLISHED)'} · {d.status || 'Active'}</span>
  }
  if (key === 'l6') {
    return <span>{d.encoding || 'ASCII / Binary'} · {d.encryption || 'Plaintext'}</span>
  }
  return <span>{d.summary || d.app || 'Payload Data'}</span>
}
