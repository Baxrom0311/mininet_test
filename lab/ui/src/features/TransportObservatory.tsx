import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Activity,
  AlertOctagon,
  AlertTriangle,
  ArrowDownUp,
  HardDrive,
  Gauge,
  Pause,
  Play,
  RefreshCw,
  Search,
  Zap,
} from 'lucide-react'
import { LineChart, type Series } from '@/components/charts'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Segmented } from '@/components/ui/tabs'
import { toast } from '@/components/ui/toast'
import { api } from '@/lib/api'
import { fmtBps, fmtMs } from '@/lib/format'
import { type Win } from '@/lib/store'
import { cn } from '@/lib/utils'
import { WindowFrame } from './WindowFrame'

export interface StreamSummary {
  stream_id: string
  stream_key?: string
  src_ip: string
  src_port: number
  dst_ip: string
  dst_port: number
  state: string
  cwnd?: number
  snd_cwnd?: number
  ssthresh?: number | null
  snd_ssthresh?: number | null
  rtt_ms?: number
  rttvar_ms?: number
  retrans?: number
  retrans_total?: number
  total_retrans?: number
  delivery_rate_bps?: number
  pacing_rate_bps?: number
  ca_state?: string
  congestion_algo?: string
  bytes_acked?: number
  bytes_received?: number
  rcv_space?: number
  snd_buf?: number
  rcv_buf?: number
  node?: string
  timestamp?: number
}

export interface StreamSample {
  t?: number
  timestamp?: number
  cwnd?: number
  snd_cwnd?: number
  ssthresh?: number | null
  snd_ssthresh?: number | null
  rtt_ms?: number
  rttvar_ms?: number
  delivery_rate_bps?: number
  pacing_rate_bps?: number
  retrans?: number
  retrans_total?: number
  total_retrans?: number
  ca_state?: string
  bytes_acked?: number
  bytes_received?: number
  rcv_space?: number
}

export interface StreamDetail extends StreamSummary {
  samples?: StreamSample[]
  history?: StreamSample[]
}

export function TransportObservatory({ win }: { win: Win }) {
  const [streams, setStreams] = useState<StreamSummary[]>([])
  const [selectedStreamId, setSelectedStreamId] = useState<string>('')
  const [streamDetail, setStreamDetail] = useState<StreamDetail | null>(null)
  const [activeTab, setActiveTab] = useState<'cwnd' | 'rtt' | 'rate' | 'buffers'>('cwnd')
  const [searchQuery, setSearchQuery] = useState('')
  const [filterState, setFilterState] = useState<'all' | 'active' | 'recovery'>('all')
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [loading, setLoading] = useState(false)
  const [wsConnected, setWsConnected] = useState(false)
  const timerRef = useRef<number | null>(null)
  const wsRef = useRef<WebSocket | null>(null)

  // Oqimlar ro'yxatini olish (REST fallback)
  const fetchStreams = async (quiet = true) => {
    try {
      if (!quiet) setLoading(true)
      const res = await api.get<{ streams: StreamSummary[] }>('/api/telemetry/streams')
      const list = res.streams || []
      setStreams(list)
      if (list.length > 0 && !selectedStreamId) {
        setSelectedStreamId(list[0].stream_id)
      }
    } catch (e: any) {
      if (!quiet) toast("Telemetriyani yuklab bo'lmadi: " + e.message, 'error')
    } finally {
      if (!quiet) setLoading(false)
    }
  }

  // Tanlangan oqim detallarini olish
  const fetchDetail = async (id: string) => {
    if (!id) return
    try {
      const res = await api.get<StreamDetail>(`/api/telemetry/stream/${encodeURIComponent(id)}`)
      setStreamDetail(res)
    } catch {
      // Oqim hali ochilmagan bo'lishi mumkin
    }
  }

  // WebSocket orqali real-time telemetriya oqimi
  useEffect(() => {
    let ws: WebSocket | null = null
    try {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const wsUrl = `${protocol}//${window.location.host}/ws/telemetry`
      ws = new WebSocket(wsUrl)
      wsRef.current = ws

      ws.onopen = () => {
        setWsConnected(true)
      }

      ws.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data)
          if (payload && payload.type === 'init' && Array.isArray(payload.streams)) {
            setStreams(payload.streams)
            if (payload.streams.length > 0 && !selectedStreamId) {
              setSelectedStreamId(payload.streams[0].stream_id)
            }
          } else if (payload) {
            const items = Array.isArray(payload) ? payload : [payload]
            items.forEach((item: any) => {
              if (item.stream_id) {
                setStreams((prev) => {
                  const idx = prev.findIndex((s) => s.stream_id === item.stream_id)
                  if (idx >= 0) {
                    const copy = [...prev]
                    copy[idx] = { ...copy[idx], ...item }
                    return copy
                  }
                  return [item, ...prev]
                })

                if (item.stream_id === selectedStreamId) {
                  setStreamDetail((prev) => {
                    if (!prev) return item
                    const samples = [...(prev.samples || prev.history || [])]
                    samples.push({
                      t: item.timestamp ? Math.round(item.timestamp % 1000) : samples.length,
                      timestamp: item.timestamp,
                      cwnd: item.snd_cwnd ?? item.cwnd,
                      ssthresh: item.snd_ssthresh ?? item.ssthresh,
                      rtt_ms: item.rtt_ms,
                      rttvar_ms: item.rttvar_ms,
                      delivery_rate_bps: item.delivery_rate_bps,
                      pacing_rate_bps: item.pacing_rate_bps,
                      retrans: item.retrans,
                      total_retrans: item.total_retrans,
                      bytes_acked: item.bytes_acked,
                      bytes_received: item.bytes_received,
                      ca_state: item.ca_state,
                    })
                    if (samples.length > 120) samples.splice(0, samples.length - 120)
                    return { ...prev, ...item, samples }
                  })
                }
              }
            })
          }
        } catch {
          // Xatolik bo'lsa poller davom etadi
        }
      }

      ws.onclose = () => {
        setWsConnected(false)
      }
      ws.onerror = () => {
        setWsConnected(false)
      }
    } catch {
      setWsConnected(false)
    }

    return () => {
      if (ws) {
        ws.close()
        wsRef.current = null
      }
    }
  }, [selectedStreamId])

  // Polling va avtomatik yangilash
  useEffect(() => {
    fetchStreams(false)
    if (autoRefresh) {
      timerRef.current = window.setInterval(() => {
        fetchStreams(true)
        if (selectedStreamId) {
          fetchDetail(selectedStreamId)
        }
      }, 1000)
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [autoRefresh, selectedStreamId])

  // Oqim tanlanganda detalni yangilash
  useEffect(() => {
    if (selectedStreamId) {
      fetchDetail(selectedStreamId)
    }
  }, [selectedStreamId])

  // Sintetik test oqimlarini ishga tushirish (Demo / Sinov rejimi)
  const runSimulation = async (type: 'cubic' | 'bbr' | 'loss') => {
    try {
      setLoading(true)
      const port = 40000 + Math.floor(Math.random() * 10000)
      const hasLoss = type === 'loss'
      const algo = type === 'bbr' ? 'bbr' : 'cubic'
      const res = await api.post<{ ok: boolean; stream_id: string }>('/api/telemetry/simulate', {
        src_ip: type === 'bbr' ? '10.0.1.20' : hasLoss ? '10.0.1.30' : '10.0.1.10',
        src_port: port,
        dst_ip: type === 'bbr' ? '10.0.2.80' : hasLoss ? '10.0.2.50' : '10.0.2.20',
        dst_port: type === 'bbr' ? 443 : 80,
        steps: 14,
        has_loss: hasLoss,
        algo,
      })
      toast(`Simulyatsiya: ${res.stream_id} (${type.toUpperCase()}) yaratildi`, 'ok')
      await fetchStreams(true)
      if (res.stream_id) {
        setSelectedStreamId(res.stream_id)
        await fetchDetail(res.stream_id)
      }
    } catch (e: any) {
      toast('Simulyatsiya xatosi: ' + e.message, 'error')
    } finally {
      setLoading(false)
    }
  }

  // Filtrlangan oqimlar ro'yxati
  const filteredStreams = useMemo(() => {
    return streams.filter((s) => {
      const q = searchQuery.trim().toLowerCase()
      if (q) {
        const matchesQuery =
          s.src_ip.toLowerCase().includes(q) ||
          s.dst_ip.toLowerCase().includes(q) ||
          String(s.src_port).includes(q) ||
          String(s.dst_port).includes(q) ||
          (s.ca_state || '').toLowerCase().includes(q) ||
          (s.congestion_algo || '').toLowerCase().includes(q) ||
          (s.stream_id || '').toLowerCase().includes(q)
        if (!matchesQuery) return false
      }
      if (filterState === 'active') {
        return (s.state || 'ESTABLISHED').toUpperCase() === 'ESTABLISHED'
      }
      if (filterState === 'recovery') {
        const ca = (s.ca_state || '').toLowerCase()
        return ca === 'recovery' || ca === 'loss' || (s.total_retrans || s.retrans || 0) > 0
      }
      return true
    })
  }, [streams, searchQuery, filterState])

  // Tanlangan oqim xulosasi
  const cur = streamDetail || streams.find((s) => s.stream_id === selectedStreamId)

  // Grafik ma'lumotlari
  const rawPoints = useMemo(() => {
    const list = streamDetail?.samples || streamDetail?.history || []
    return list.map((s, idx) => {
      const t = s.t ?? (s.timestamp ? Math.round(s.timestamp % 1000) : idx)
      const cwnd = s.cwnd ?? s.snd_cwnd ?? 10
      const ssthresh = s.ssthresh ?? s.snd_ssthresh ?? null
      const rtt = s.rtt_ms ?? 1.0
      const rttvar = s.rttvar_ms ?? 0.5
      const rate = (s.delivery_rate_bps ?? 0) / 1e6
      const pacing = (s.pacing_rate_bps ?? 0) / 1e6
      // Soket buferi hisoblari (KB)
      const inFlightKb = (cwnd * 1460) / 1024
      const rcvSpaceKb = ((s as any).rcv_space || 65535) / 1024
      const ackedKb = ((s.bytes_acked || 0) / 1024) % 1000
      return { t, cwnd, ssthresh, rtt, rttvar, rate, pacing, inFlightKb, rcvSpaceKb, ackedKb }
    })
  }, [streamDetail])

  // 1. CWND Grafigi
  const cwndSeries = useMemo<Series[]>(() => {
    if (!rawPoints.length) return []
    const baseT = rawPoints[0].t
    const cwndPoints = rawPoints.map((p) => ({ x: Math.max(0, p.t - baseT), y: p.cwnd }))
    const ssthreshPoints = rawPoints
      .filter((p) => p.ssthresh != null)
      .map((p) => ({ x: Math.max(0, p.t - baseT), y: p.ssthresh }))

    const res: Series[] = [{ name: 'CWND (segmentlar)', color: '#38bdf8', points: cwndPoints }]
    if (ssthreshPoints.length > 0) {
      res.push({ name: 'ssthresh (chegara)', color: '#f59e0b', points: ssthreshPoints })
    }
    return res
  }, [rawPoints])

  // 2. RTT & Jitter Grafigi
  const rttSeries = useMemo<Series[]>(() => {
    if (!rawPoints.length) return []
    const baseT = rawPoints[0].t
    return [
      {
        name: 'Smoothed RTT (ms)',
        color: '#10b981',
        points: rawPoints.map((p) => ({ x: Math.max(0, p.t - baseT), y: p.rtt })),
      },
      {
        name: 'RTTVAR / Jitter (ms)',
        color: '#a855f7',
        points: rawPoints.map((p) => ({ x: Math.max(0, p.t - baseT), y: p.rttvar })),
      },
    ]
  }, [rawPoints])

  // 3. Throughput & Pacing Grafigi
  const rateSeries = useMemo<Series[]>(() => {
    if (!rawPoints.length) return []
    const baseT = rawPoints[0].t
    return [
      {
        name: 'Delivery Rate (Mb/s)',
        color: '#6366f1',
        points: rawPoints.map((p) => ({ x: Math.max(0, p.t - baseT), y: p.rate })),
      },
      {
        name: 'Pacing Rate (Mb/s)',
        color: '#ec4899',
        points: rawPoints.map((p) => ({ x: Math.max(0, p.t - baseT), y: p.pacing })),
      },
    ]
  }, [rawPoints])

  // 4. Socket Buffers Grafigi
  const bufferSeries = useMemo<Series[]>(() => {
    if (!rawPoints.length) return []
    const baseT = rawPoints[0].t
    return [
      {
        name: 'In-Flight / Unacked (KB)',
        color: '#f59e0b',
        points: rawPoints.map((p) => ({ x: Math.max(0, p.t - baseT), y: p.inFlightKb })),
      },
      {
        name: 'Receive Window (KB)',
        color: '#10b981',
        points: rawPoints.map((p) => ({ x: Math.max(0, p.t - baseT), y: p.rcvSpaceKb })),
      },
      {
        name: 'Acked Ma\'lumot (KB)',
        color: '#38bdf8',
        points: rawPoints.map((p) => ({ x: Math.max(0, p.t - baseT), y: p.ackedKb })),
      },
    ]
  }, [rawPoints])

  const totalBps = streams.reduce((acc, s) => acc + (s.delivery_rate_bps || 0), 0)
  const totalRetrans = streams.reduce((acc, s) => acc + (s.total_retrans || s.retrans || 0), 0)
  const lossCount = streams.filter((s) => {
    const ca = (s.ca_state || '').toLowerCase()
    return ca === 'recovery' || ca === 'loss' || (s.retrans || 0) > 0
  }).length

  // Soket bufer ko'rsatkichlari
  const curCwnd = cur?.cwnd ?? cur?.snd_cwnd ?? 10
  const curInFlightKb = Math.round((curCwnd * 1460) / 1024)
  const sndBufKb = cur?.snd_buf ? Math.round(cur.snd_buf / 1024) : 128
  const rcvBufKb = cur?.rcv_buf ? Math.round(cur.rcv_buf / 1024) : 128
  const sndBufPercent = Math.min(100, Math.round((curInFlightKb / Math.max(1, sndBufKb)) * 100))
  const rcvSpaceKb = cur?.rcv_space ? Math.round(cur.rcv_space / 1024) : 64
  const rcvBufPercent = Math.min(100, Math.round((rcvSpaceKb / Math.max(1, rcvBufKb)) * 100))

  return (
    <WindowFrame
      win={win}
      icon={<Activity className="text-primary" />}
      title="Transport Qatlami (L4) Observatory"
      sub="TCP CWND · SSTHRESH · RTT · Jitter (RFC 3550) · Soket Bufirlari · eBPF Telemetriyasi"
    >
      <div className="flex min-h-0 flex-1 flex-col gap-2.5 overflow-auto p-3">
        {/* Yuqori boshqaruv paneli */}
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border bg-card/40 p-2 shadow-sm">
          <div className="flex flex-wrap items-center gap-2.5">
            <div className="flex items-center gap-1.5">
              <span
                className={cn(
                  'inline-block h-2.5 w-2.5 rounded-full',
                  wsConnected
                    ? 'bg-emerald-500 shadow-[0_0_8px_#10b981]'
                    : autoRefresh
                    ? 'bg-sky-400 animate-pulse'
                    : 'bg-muted-foreground'
                )}
                title={wsConnected ? 'WebSocket orqali jonli ulangan' : autoRefresh ? 'Avtomatik yangilanmoqda' : 'To\'xtatilgan'}
              />
              <span className="text-xs font-semibold">
                Faol Oqimlar: <Badge variant="outline">{streams.length}</Badge>
              </span>
            </div>

            <span className="text-xs text-muted-foreground">
              Jami uzatish: <b className="text-foreground">{fmtBps(totalBps)}</b>
            </span>

            {totalRetrans > 0 && (
              <Badge variant="destructive" className="text-[11px]">
                <AlertTriangle className="mr-1 h-3 w-3" />
                {totalRetrans} Qayta yuborish
              </Badge>
            )}

            {lossCount > 0 && (
              <Badge variant="outline" className="border-rose-500/30 text-[11px] text-rose-400">
                <AlertOctagon className="mr-1 h-3 w-3" />
                {lossCount} ta oqim Recovery holatida
              </Badge>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-1.5">
            <Button
              size="xs"
              variant="outline"
              onClick={() => runSimulation('cubic')}
              title="Cubic sekin o'sish va ko'payuvchi pasayish simulyatsiyasi"
              className="border-sky-500/30 text-sky-400 hover:bg-sky-500/10"
            >
              <Zap className="mr-1 h-3 w-3" /> +Cubic Oqim
            </Button>
            <Button
              size="xs"
              variant="outline"
              onClick={() => runSimulation('bbr')}
              title="BBR modeliga asoslangan yuqori tezlik oqimi"
              className="border-indigo-500/30 text-indigo-400 hover:bg-indigo-500/10"
            >
              <Gauge className="mr-1 h-3 w-3" /> +BBR Oqim
            </Button>
            <Button
              size="xs"
              variant="outline"
              onClick={() => runSimulation('loss')}
              title="Tarmoqda paket yo'qotish va Fast Retransmit hodisasi"
              className="border-rose-500/30 text-rose-400 hover:bg-rose-500/10"
            >
              <AlertTriangle className="mr-1 h-3 w-3" /> +Loss Stsenariy
            </Button>
            <Button
              size="xs"
              variant="ghost"
              onClick={() => setAutoRefresh(!autoRefresh)}
              title={autoRefresh ? 'Pauza qilish' : 'Jonli yangilash'}
            >
              {autoRefresh ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
              {autoRefresh ? 'Pauza' : 'Jonli'}
            </Button>
            <Button
              size="xs"
              variant="ghost"
              disabled={loading}
              onClick={() => {
                fetchStreams(false)
                if (selectedStreamId) fetchDetail(selectedStreamId)
              }}
              title="Yangilash"
            >
              <RefreshCw className={cn('h-3.5 w-3.5', loading && 'animate-spin')} />
            </Button>
          </div>
        </div>

        {/* Asosiy kontent: Chapda oqimlar jadvali, O'ngda grafiklar */}
        <div className="grid min-h-[480px] flex-1 grid-cols-1 gap-3 lg:grid-cols-12">
          {/* Chap panel (4 ustun): Oqimlar ro'yxati va filter */}
          <div className="flex flex-col rounded-lg border bg-card/30 p-2.5 lg:col-span-4">
            <div className="mb-2 flex items-center justify-between text-xs font-semibold">
              <span className="flex items-center gap-1.5">
                <ArrowDownUp className="h-3.5 w-3.5 text-primary" /> L4 Oqimlar Jadvali
              </span>
              <span className="text-[11px] text-muted-foreground">{filteredStreams.length} / {streams.length}</span>
            </div>

            {/* Qidiruv va holat filtri */}
            <div className="mb-2 space-y-1.5">
              <div className="relative">
                <Search className="absolute left-2.5 top-2 h-3.5 w-3.5 text-muted-foreground" />
                <Input
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="IP, port yoki algoritm qidirish..."
                  className="h-7.5 pl-8 text-xs font-mono"
                />
              </div>
              <div className="flex items-center gap-1 text-[11px]">
                <button
                  onClick={() => setFilterState('all')}
                  className={cn(
                    'rounded px-2 py-0.5 transition-colors',
                    filterState === 'all' ? 'bg-primary/20 text-primary font-semibold' : 'text-muted-foreground hover:bg-muted'
                  )}
                >
                  Barchasi ({streams.length})
                </button>
                <button
                  onClick={() => setFilterState('active')}
                  className={cn(
                    'rounded px-2 py-0.5 transition-colors',
                    filterState === 'active' ? 'bg-emerald-500/20 text-emerald-400 font-semibold' : 'text-muted-foreground hover:bg-muted'
                  )}
                >
                  Faol
                </button>
                <button
                  onClick={() => setFilterState('recovery')}
                  className={cn(
                    'rounded px-2 py-0.5 transition-colors',
                    filterState === 'recovery' ? 'bg-rose-500/20 text-rose-400 font-semibold' : 'text-muted-foreground hover:bg-muted'
                  )}
                >
                  Loss / Recovery ({lossCount})
                </button>
              </div>
            </div>

            {/* Oqimlar ro'yxati */}
            <div className="min-h-0 flex-1 overflow-auto">
              {filteredStreams.length === 0 ? (
                <div className="flex flex-col items-center justify-center p-6 text-center text-xs text-muted-foreground">
                  <Activity className="mb-2 h-8 w-8 opacity-40 text-muted-foreground" />
                  {searchQuery ? 'Qidiruv bo\'yicha oqim topilmadi' : 'Hozircha faol TCP/UDP oqimlar yo\'q.'}
                  <span className="mt-1 text-[11px]">
                    Yuqoridagi <b>"+Cubic Oqim"</b> yoki <b>"+Loss"</b> tugmasi orqali oqim yarating.
                  </span>
                </div>
              ) : (
                <div className="space-y-1.5 font-mono text-[11.5px]">
                  {filteredStreams.map((s) => {
                    const isSel = s.stream_id === selectedStreamId
                    const ca = (s.ca_state || 'open').toLowerCase()
                    const caColor =
                      ca === 'recovery' || ca === 'loss'
                        ? 'text-rose-400 bg-rose-500/10 border-rose-500/30'
                        : ca === 'cwr' || ca === 'disorder'
                        ? 'text-amber-400 bg-amber-500/10 border-amber-500/30'
                        : 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30'

                    return (
                      <div
                        key={s.stream_id}
                        onClick={() => setSelectedStreamId(s.stream_id)}
                        className={cn(
                          'cursor-pointer rounded-md border p-2 transition-all hover:bg-accent/60',
                          isSel ? 'border-primary bg-primary/10 shadow-sm ring-1 ring-primary/40' : 'border-border/60 bg-card/40'
                        )}
                      >
                        <div className="flex items-center justify-between font-bold">
                          <span className="truncate text-foreground">
                            {s.src_ip}:{s.src_port} → {s.dst_ip}:{s.dst_port}
                          </span>
                          <span className={cn('rounded px-1 text-[9.5px] uppercase border', caColor)}>
                            {s.ca_state || 'OPEN'}
                          </span>
                        </div>

                        <div className="mt-1 grid grid-cols-3 gap-1 text-[10.5px] text-muted-foreground font-sans">
                          <div>
                            CWND: <b className="text-foreground">{s.cwnd ?? s.snd_cwnd ?? 10}</b> seg
                          </div>
                          <div>
                            RTT: <b className="text-foreground">{fmtMs(s.rtt_ms)}</b>
                          </div>
                          <div>
                            Tezlik: <b className="text-foreground">{fmtBps(s.delivery_rate_bps)}</b>
                          </div>
                        </div>

                        <div className="mt-1 flex items-center justify-between text-[10px] text-muted-foreground border-t border-border/40 pt-1">
                          <span className="uppercase text-sky-400 font-semibold">
                            {s.congestion_algo || 'CUBIC'}
                          </span>
                          <span>
                            Holat: <b>{s.state || 'ESTABLISHED'}</b>
                            {(s.total_retrans || s.retrans || 0) > 0 && (
                              <span className="ml-1 text-rose-400 font-bold">
                                · {s.total_retrans || s.retrans} retrans
                              </span>
                            )}
                          </span>
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          </div>

          {/* O'ng panel (8 ustun): Tanlangan oqim grafiki va telemetriyasi */}
          <div className="flex flex-col gap-2.5 lg:col-span-8">
            {cur ? (
              <>
                {/* Oqim xulosa kartalari */}
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                  <Card className="bg-card/50">
                    <CardContent className="p-2.5">
                      <div className="text-[11px] text-muted-foreground">CWND / SSTHRESH</div>
                      <div className="mt-0.5 text-base font-bold text-sky-400 font-mono">
                        {cur.cwnd ?? cur.snd_cwnd ?? '—'}
                        <span className="text-xs font-normal text-muted-foreground font-sans">
                          {' '}/ {cur.ssthresh ?? cur.snd_ssthresh ?? '∞'} seg
                        </span>
                      </div>
                      <div className="text-[10px] text-muted-foreground">
                        Algoritm: <b>{cur.congestion_algo?.toUpperCase() || 'CUBIC'}</b>
                      </div>
                    </CardContent>
                  </Card>

                  <Card className="bg-card/50">
                    <CardContent className="p-2.5">
                      <div className="text-[11px] text-muted-foreground">Smoothed RTT / Jitter</div>
                      <div className="mt-0.5 text-base font-bold text-emerald-400 font-mono">
                        {fmtMs(cur.rtt_ms)}
                      </div>
                      <div className="text-[10px] text-muted-foreground">
                        ± tebranish: {fmtMs(cur.rttvar_ms)}
                      </div>
                    </CardContent>
                  </Card>

                  <Card className="bg-card/50">
                    <CardContent className="p-2.5">
                      <div className="text-[11px] text-muted-foreground">Delivery / Pacing</div>
                      <div className="mt-0.5 text-base font-bold text-indigo-400 font-mono">
                        {fmtBps(cur.delivery_rate_bps)}
                      </div>
                      <div className="text-[10px] text-muted-foreground">
                        Pacing: {fmtBps(cur.pacing_rate_bps)}
                      </div>
                    </CardContent>
                  </Card>

                  <Card className="bg-card/50">
                    <CardContent className="p-2.5">
                      <div className="text-[11px] text-muted-foreground">Holat & Retransmitlar</div>
                      <div className="mt-0.5 flex items-center gap-1.5 text-base font-bold">
                        <span className="text-foreground">{cur.state}</span>
                        {(cur.retrans || 0) > 0 && (
                          <span className="rounded bg-rose-500/20 px-1 text-xs text-rose-400">
                            +{cur.retrans}
                          </span>
                        )}
                      </div>
                      <div className="text-[10px] text-muted-foreground">
                        Jami qayta uzatish: {cur.total_retrans ?? cur.retrans ?? 0}
                      </div>
                    </CardContent>
                  </Card>
                </div>

                {/* Tablar: Grafik turlari */}
                <div className="flex items-center justify-between border-b pb-1">
                  <Segmented
                    value={activeTab}
                    onChange={setActiveTab}
                    items={[
                      { value: 'cwnd', label: 'CWND & Tiqilinch Dinamikasi' },
                      { value: 'rtt', label: 'RTT & Jitter (RFC 3550)' },
                      { value: 'rate', label: 'Throughput & Pacing' },
                      { value: 'buffers', label: 'Soket Bufirlari (Socket Buffers)' },
                    ]}
                  />
                  <span className="text-[11px] text-muted-foreground font-mono">
                    {rawPoints.length} ta o'lchov nuqtasi
                  </span>
                </div>

                {/* Grafik oynasi */}
                <div className="min-h-0 flex-1">
                  {activeTab === 'cwnd' && (
                    <LineChart
                      series={cwndSeries}
                      fmt={(v) => `${Math.round(v)} seg`}
                      title="TCP Congestion Window (CWND) va Slow Start Threshold (ssthresh) Dinamikasi"
                      xFmt={(x) => `${Math.round(x)}s`}
                      note="Sawtooth to'lqini: paket yo'qotish aniqlanganda CWND multiplicatively pasayadi, so'ngra chiziqli o'sadi."
                    />
                  )}

                  {activeTab === 'rtt' && (
                    <LineChart
                      series={rttSeries}
                      fmt={(v) => `${v.toFixed(1)} ms`}
                      title="Smoothed Round-Trip Time (RTT) va Jitter Tebranishi (RFC 3550)"
                      xFmt={(x) => `${Math.round(x)}s`}
                      note="Bufferbloat paytida RTT ortadi; router/switch navbati bo'shaganda normal darajaga qaytadi."
                    />
                  )}

                  {activeTab === 'rate' && (
                    <LineChart
                      series={rateSeries}
                      fmt={(v) => `${v.toFixed(1)} Mb/s`}
                      title="Haqiqiy Yetkazib Berish (Delivery Rate) va Yadro Pacing Tezligi"
                      xFmt={(x) => `${Math.round(x)}s`}
                      note="BBR va zamonaviy Linux yadrosi Fair Queuing (FQ) orqali paketlarni bir xil oraliqda chiqaradi."
                    />
                  )}

                  {activeTab === 'buffers' && (
                    <div className="space-y-3">
                      {/* Socket Buffers Real-Time SVG Grafigi */}
                      <LineChart
                        series={bufferSeries}
                        fmt={(v) => `${v.toFixed(1)} KB`}
                        title="Soket Bufirlari va In-Flight Ma'lumot Dinamikasi"
                        xFmt={(x) => `${Math.round(x)}s`}
                        note="Soket xotirasi (wmem/rmem) o'lchami va tarmoqda tasdiqlanmagan in-flight ma'lumot hajmi."
                      />

                      {/* Soket buferi holati va Linux tcp_info parametrlari */}
                      <div className="rounded-lg border bg-card/40 p-3 font-mono text-xs">
                        <div className="mb-2 flex items-center justify-between border-b pb-1">
                          <span className="font-semibold text-foreground font-sans flex items-center gap-1.5">
                            <HardDrive className="h-4 w-4 text-primary" /> Linux Soket Bufirlari va Parametrlari
                          </span>
                          <span className="text-[11px] text-muted-foreground">{cur.stream_id}</span>
                        </div>

                        {/* Visual bufer to'liqligi progress barlari */}
                        <div className="mb-3 grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                          <div className="rounded border bg-card/60 p-2">
                            <div className="mb-1 flex justify-between text-[11px]">
                              <span className="text-muted-foreground">Send Buffer (snd_buf / wmem):</span>
                              <span className="font-bold text-amber-400">{curInFlightKb} KB / {sndBufKb} KB ({sndBufPercent}%)</span>
                            </div>
                            <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                              <div
                                className={cn(
                                  'h-full transition-all duration-300',
                                  sndBufPercent > 80 ? 'bg-rose-500' : sndBufPercent > 50 ? 'bg-amber-400' : 'bg-primary'
                                )}
                                style={{ width: `${sndBufPercent}%` }}
                              />
                            </div>
                            <div className="mt-1 text-[10px] text-muted-foreground">In-Flight segmentlar navbati</div>
                          </div>

                          <div className="rounded border bg-card/60 p-2">
                            <div className="mb-1 flex justify-between text-[11px]">
                              <span className="text-muted-foreground">Receive Buffer (rcv_buf / rmem):</span>
                              <span className="font-bold text-emerald-400">{rcvSpaceKb} KB / {rcvBufKb} KB ({rcvBufPercent}%)</span>
                            </div>
                            <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                              <div
                                className="h-full bg-emerald-500 transition-all duration-300"
                                style={{ width: `${rcvBufPercent}%` }}
                              />
                            </div>
                            <div className="mt-1 text-[10px] text-muted-foreground">Qabul qilingan oyna zahirasi (rcv_space)</div>
                          </div>
                        </div>

                        {/* struct tcp_info maydonlari */}
                        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                          <div className="space-y-1 border-r pr-3">
                            <div className="flex justify-between border-b pb-0.5">
                              <span className="text-muted-foreground">Tugun (Node / NS):</span>
                              <span>{cur.node || 'localhost (Mininet)'}</span>
                            </div>
                            <div className="flex justify-between border-b pb-0.5">
                              <span className="text-muted-foreground">MSS (Max Segment Size):</span>
                              <span>1,460 bayt</span>
                            </div>
                            <div className="flex justify-between border-b pb-0.5">
                              <span className="text-muted-foreground">In-Flight Baytlar:</span>
                              <span className="text-amber-400 font-bold">{(curCwnd * 1460).toLocaleString()} B</span>
                            </div>
                            <div className="flex justify-between border-b pb-0.5">
                              <span className="text-muted-foreground">RTO (Retransmit Timeout):</span>
                              <span>{(cur.rtt_ms ? (cur.rtt_ms + (cur.rttvar_ms || 0) * 4).toFixed(1) : '200.0')} ms</span>
                            </div>
                          </div>

                          <div className="space-y-1">
                            <div className="flex justify-between border-b pb-0.5">
                              <span className="text-muted-foreground">CA Algoritmi:</span>
                              <span className="text-sky-400 font-bold uppercase">{cur.congestion_algo || 'cubic'}</span>
                            </div>
                            <div className="flex justify-between border-b pb-0.5">
                              <span className="text-muted-foreground">CA Holati (tcpi_ca_state):</span>
                              <span className="uppercase text-emerald-400 font-bold">{cur.ca_state || 'TCP_CA_Open'}</span>
                            </div>
                            <div className="flex justify-between border-b pb-0.5">
                              <span className="text-muted-foreground">Tasdiqlangan baytlar (bytes_acked):</span>
                              <span>{cur.bytes_acked?.toLocaleString() || '0'} B</span>
                            </div>
                            <div className="flex justify-between border-b pb-0.5">
                              <span className="text-muted-foreground">Yadro Retransmits (tcpi_retrans):</span>
                              <span className={cur.retrans ? 'text-rose-400 font-bold' : ''}>
                                {cur.retrans || 0}
                              </span>
                            </div>
                          </div>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              </>
            ) : (
              <div className="grid h-full place-items-center rounded-lg border bg-card/20 text-xs text-muted-foreground p-8 text-center">
                <div>
                  <Activity className="mx-auto mb-2 h-10 w-10 opacity-30 text-primary" />
                  <div className="font-semibold text-foreground text-sm">Transport Oqimi Tanlanmagan</div>
                  <p className="mt-1 max-w-sm text-muted-foreground">
                    Tahlil qilish va real-time grafiklarini ko'rish uchun chap ro'yxatdan biror L4 oqimini tanlang yoki yuqoridagi <b>"+Cubic Oqim"</b> tugmasini bosing.
                  </p>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </WindowFrame>
  )
}
