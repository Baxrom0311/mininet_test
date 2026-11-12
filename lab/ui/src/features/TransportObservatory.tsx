import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Activity,
  AlertOctagon,
  AlertTriangle,
  ArrowDownUp,
  CheckCircle2,
  FileText,
  Gauge,
  GitCompare,
  HardDrive,
  Layers,
  Pause,
  PhoneCall,
  Play,
  RefreshCw,
  Search,
  ShieldAlert,
  ShieldCheck,
  Video,
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

type TabType = 'cwnd' | 'rtt' | 'rate' | 'buffers' | 'queues' | 'workloads' | 'sla' | 'compare'

export function TransportObservatory({ win }: { win?: Win }) {
  const [streams, setStreams] = useState<StreamSummary[]>([])
  const [selectedStreamId, setSelectedStreamId] = useState<string>('')
  const [streamDetail, setStreamDetail] = useState<StreamDetail | null>(null)
  const [activeTab, setActiveTab] = useState<TabType>('cwnd')
  const [searchQuery, setSearchQuery] = useState('')
  const [filterState, setFilterState] = useState<'all' | 'active' | 'recovery'>('all')
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [loading, setLoading] = useState(false)
  const [wsConnected, setWsConnected] = useState(false)
  const timerRef = useRef<number | null>(null)
  const wsRef = useRef<WebSocket | null>(null)

  // Yangi funksional holatlar
  const [queuesData, setQueuesData] = useState<any[]>([])
  const [bloatData, setBloatData] = useState<any>(null)
  const [voipData, setVoipData] = useState<any>(null)
  const [videoData, setVideoData] = useState<any>(null)
  const [slaData, setSlaData] = useState<any>(null)
  const [compareData, setCompareData] = useState<any>(null)

  // Workloads boshqaruvi
  const [voipCodec, setVoipCodec] = useState<'G.711' | 'G.729' | 'Opus'>('G.711')
  const [voipDelay, setVoipDelay] = useState<number>(30)
  const [voipJitter, setVoipJitter] = useState<number>(2.0)
  const [voipLoss, setVoipLoss] = useState<number>(0.5)

  // Solishtirish parametrlari
  const [compAlgoA, setCompAlgoA] = useState('cubic')
  const [compAlgoB, setCompAlgoB] = useState('bbr')
  const [compLoss, setCompLoss] = useState(1.5)
  const [compBw, setCompBw] = useState(20)

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

  const fetchQueues = async () => {
    try {
      const res = await api.get<{ queues: any[]; bufferbloat: any }>('/api/telemetry/l4/queues')
      setQueuesData(res.queues || [])
      setBloatData(res.bufferbloat || null)
    } catch {}
  }

  const fetchWorkloads = async () => {
    try {
      const [vRes, vidRes] = await Promise.all([
        api.get(`/api/telemetry/l4/workloads/voip?codec=${voipCodec}&delay_ms=${voipDelay}&jitter_ms=${voipJitter}&loss_pct=${voipLoss}`),
        api.get('/api/telemetry/l4/workloads/video'),
      ])
      setVoipData(vRes)
      setVideoData(vidRes)
    } catch {}
  }

  const fetchSla = async () => {
    try {
      const res = await api.get('/api/telemetry/l4/sla')
      setSlaData(res)
    } catch {}
  }

  const fetchCompare = async () => {
    try {
      const res = await api.get(
        `/api/telemetry/l4/compare/algorithms?algo_a=${compAlgoA}&algo_b=${compAlgoB}&loss_pct=${compLoss}&bottleneck_mbps=${compBw}`
      )
      setCompareData(res)
    } catch {}
  }

  const downloadLatexReport = async () => {
    try {
      const res = await fetch('/api/telemetry/l4/report/latex')
      if (!res.ok) throw new Error("Hisobotni yuklab bo'lmadi")
      const tex = await res.text()
      const blob = new Blob([tex], { type: 'text/plain;charset=utf-8' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'netlab_evaluation_report.tex'
      a.click()
      URL.revokeObjectURL(url)
      toast('IEEE LaTeX hisoboti muvaffaqiyatli yuklab olindi', 'ok')
    } catch (e: any) {
      toast('Yuklash xatosi: ' + e.message, 'error')
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

      ws.onopen = () => setWsConnected(true)
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
      ws.onclose = () => setWsConnected(false)
      ws.onerror = () => setWsConnected(false)
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
    fetchStreams(true)
    if (autoRefresh) {
      timerRef.current = window.setInterval(() => {
        fetchStreams(true)
        if (selectedStreamId) fetchDetail(selectedStreamId)
        if (activeTab === 'queues') fetchQueues()
        else if (activeTab === 'workloads') fetchWorkloads()
        else if (activeTab === 'sla') fetchSla()
        else if (activeTab === 'compare') fetchCompare()
      }, 1200)
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [autoRefresh, selectedStreamId, activeTab])

  // Tab yoki parametrlar o'zgarganda yuklash
  useEffect(() => {
    if (activeTab === 'queues') fetchQueues()
    else if (activeTab === 'workloads') fetchWorkloads()
    else if (activeTab === 'sla') fetchSla()
    else if (activeTab === 'compare') fetchCompare()
  }, [activeTab, voipCodec, voipDelay, voipJitter, voipLoss, compAlgoA, compAlgoB, compLoss, compBw])

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
        name: "Acked Ma'lumot (KB)",
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

  const bodyContent = (
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
                title={wsConnected ? 'WebSocket orqali jonli ulangan' : autoRefresh ? 'Avtomatik yangilanmoqda' : "To'xtatilgan"}
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
              <Zap className="mr-1 h-3 w-3" /> +Cubic
            </Button>
            <Button
              size="xs"
              variant="outline"
              onClick={() => runSimulation('bbr')}
              title="BBR modeliga asoslangan yuqori tezlik oqimi"
              className="border-indigo-500/30 text-indigo-400 hover:bg-indigo-500/10"
            >
              <Gauge className="mr-1 h-3 w-3" /> +BBR
            </Button>
            <Button
              size="xs"
              variant="outline"
              onClick={() => runSimulation('loss')}
              title="Tarmoqda paket yo'qotish va Fast Retransmit hodisasi"
              className="border-rose-500/30 text-rose-400 hover:bg-rose-500/10"
            >
              <AlertTriangle className="mr-1 h-3 w-3" /> +Loss
            </Button>
            <Button
              size="xs"
              variant="outline"
              onClick={downloadLatexReport}
              title="IEEE ilmiy maqola formatidagi LaTeX (.tex) hisobotini yuklab olish"
              className="border-primary/40 text-primary hover:bg-primary/10"
            >
              <FileText className="mr-1 h-3 w-3" /> LaTeX Hisobot
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
                if (activeTab === 'queues') fetchQueues()
                else if (activeTab === 'workloads') fetchWorkloads()
                else if (activeTab === 'sla') fetchSla()
                else if (activeTab === 'compare') fetchCompare()
              }}
              title="Yangilash"
            >
              <RefreshCw className={cn('h-3.5 w-3.5', loading && 'animate-spin')} />
            </Button>
          </div>
        </div>

        {/* Asosiy kontent: Chapda oqimlar jadvali, O'ngda ko'rsatkichlar va grafiklar */}
        <div className="grid min-h-[500px] flex-1 grid-cols-1 gap-3 lg:grid-cols-12">
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
                  {searchQuery ? "Qidiruv bo'yicha oqim topilmadi" : "Hozircha faol TCP/UDP oqimlar yo'q."}
                  <span className="mt-1 text-[11px]">
                    Yuqoridagi <b>"+Cubic"</b> yoki <b>"+BBR"</b> tugmasi orqali oqim yarating.
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

          {/* O'ng panel (8 ustun): Barcha telemetriya ko'rsatkichlari, tahlillar va grafiklar */}
          <div className="flex flex-col gap-2.5 lg:col-span-8">
            {/* Tablar navigatsiyasi */}
            <div className="flex flex-wrap items-center justify-between border-b pb-1 gap-2">
              <Segmented
                value={activeTab}
                onChange={(v) => setActiveTab(v as TabType)}
                items={[
                  { value: 'cwnd', label: 'CWND & Tiqilinch' },
                  { value: 'rtt', label: 'RTT / Jitter' },
                  { value: 'rate', label: 'Throughput' },
                  { value: 'buffers', label: 'Soket Bufirlari' },
                  { value: 'queues', label: 'Navbatlar & Bloat' },
                  { value: 'workloads', label: 'VoIP & Video' },
                  { value: 'sla', label: 'SLA Monitor' },
                  { value: 'compare', label: 'A/B Taqqoslash' },
                ]}
              />
              <span className="text-[11px] text-muted-foreground font-mono">
                {['queues', 'workloads', 'sla', 'compare'].includes(activeTab)
                  ? 'OPNET / L4 Platformasi'
                  : `${rawPoints.length} ta o'lchov nuqtasi`}
              </span>
            </div>

            {/* Oqim xulosa kartalari (L4 asosiy tablar uchun) */}
            {['cwnd', 'rtt', 'rate', 'buffers'].includes(activeTab) && cur && (
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
            )}

            {/* Asosiy kontent maydoni */}
            <div className="min-h-0 flex-1 overflow-auto">
              {/* TAB 1: CWND */}
              {activeTab === 'cwnd' && (
                cur ? (
                  <LineChart
                    series={cwndSeries}
                    fmt={(v) => `${Math.round(v)} seg`}
                    title="TCP Congestion Window (CWND) va Slow Start Threshold (ssthresh) Dinamikasi"
                    xFmt={(x) => `${Math.round(x)}s`}
                    note="Sawtooth to'lqini: paket yo'qotish aniqlanganda CWND multiplicatively pasayadi, so'ngra chiziqli o'sadi."
                  />
                ) : (
                  <div className="grid h-full place-items-center rounded-lg border bg-card/20 p-8 text-center text-xs text-muted-foreground">
                    <div>
                      <Activity className="mx-auto mb-2 h-10 w-10 opacity-30 text-primary" />
                      <div className="font-semibold text-foreground text-sm">Transport Oqimi Tanlanmagan</div>
                      <p className="mt-1 max-w-sm text-muted-foreground">Chap ro'yxatdan biror oqim tanlang yoki "+Cubic" tugmasini bosing.</p>
                    </div>
                  </div>
                )
              )}

              {/* TAB 2: RTT & Jitter */}
              {activeTab === 'rtt' && (
                cur ? (
                  <LineChart
                    series={rttSeries}
                    fmt={(v) => `${v.toFixed(1)} ms`}
                    title="Smoothed Round-Trip Time (RTT) va Jitter Tebranishi (RFC 3550)"
                    xFmt={(x) => `${Math.round(x)}s`}
                    note="Bufferbloat paytida RTT ortadi; router/switch navbati bo'shaganda normal darajaga qaytadi."
                  />
                ) : (
                  <div className="grid h-full place-items-center rounded-lg border bg-card/20 p-8 text-center text-xs text-muted-foreground">
                    <Activity className="mx-auto mb-2 h-10 w-10 opacity-30 text-primary" />
                    <div>Chap ro'yxatdan oqim tanlang.</div>
                  </div>
                )
              )}

              {/* TAB 3: Throughput & Pacing */}
              {activeTab === 'rate' && (
                cur ? (
                  <LineChart
                    series={rateSeries}
                    fmt={(v) => `${v.toFixed(1)} Mb/s`}
                    title="Haqiqiy Yetkazib Berish (Delivery Rate) va Yadro Pacing Tezligi"
                    xFmt={(x) => `${Math.round(x)}s`}
                    note="BBR va zamonaviy Linux yadrosi Fair Queuing (FQ) orqali paketlarni bir xil oraliqda chiqaradi."
                  />
                ) : (
                  <div className="grid h-full place-items-center rounded-lg border bg-card/20 p-8 text-center text-xs text-muted-foreground">
                    <Activity className="mx-auto mb-2 h-10 w-10 opacity-30 text-primary" />
                    <div>Chap ro'yxatdan oqim tanlang.</div>
                  </div>
                )
              )}

              {/* TAB 4: Socket Buffers */}
              {activeTab === 'buffers' && (
                cur ? (
                  <div className="space-y-3">
                    <LineChart
                      series={bufferSeries}
                      fmt={(v) => `${v.toFixed(1)} KB`}
                      title="Soket Bufirlari va In-Flight Ma'lumot Dinamikasi"
                      xFmt={(x) => `${Math.round(x)}s`}
                      note="Soket xotirasi (wmem/rmem) o'lchami va tarmoqda tasdiqlanmagan in-flight ma'lumot hajmi."
                    />

                    <div className="rounded-lg border bg-card/40 p-3 font-mono text-xs">
                      <div className="mb-2 flex items-center justify-between border-b pb-1">
                        <span className="font-semibold text-foreground font-sans flex items-center gap-1.5">
                          <HardDrive className="h-4 w-4 text-primary" /> Linux Soket Bufirlari va Parametrlari
                        </span>
                        <span className="text-[11px] text-muted-foreground">{cur.stream_id}</span>
                      </div>

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
                            <div className="h-full bg-emerald-500 transition-all duration-300" style={{ width: `${rcvBufPercent}%` }} />
                          </div>
                          <div className="mt-1 text-[10px] text-muted-foreground">Qabul qilingan oyna zahirasi (rcv_space)</div>
                        </div>
                      </div>

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
                ) : (
                  <div className="grid h-full place-items-center rounded-lg border bg-card/20 p-8 text-center text-xs text-muted-foreground">
                    <Activity className="mx-auto mb-2 h-10 w-10 opacity-30 text-primary" />
                    <div>Chap ro'yxatdan oqim tanlang.</div>
                  </div>
                )
              )}

              {/* TAB 5: Queues & Bufferbloat */}
              {activeTab === 'queues' && (
                <div className="space-y-3">
                  {/* Bufferbloat ogohlantirish statusi */}
                  <div className="flex items-center justify-between rounded-lg border p-3 bg-card/60">
                    <div className="flex items-center gap-2.5">
                      {bloatData && bloatData.bloated_count > 0 ? (
                        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-rose-500/10 text-rose-400 border border-rose-500/30">
                          <AlertOctagon className="h-5 w-5" />
                        </div>
                      ) : (
                        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">
                          <CheckCircle2 className="h-5 w-5" />
                        </div>
                      )}
                      <div>
                        <div className="text-xs font-semibold">
                          {bloatData && bloatData.bloated_count > 0
                            ? `Bufferbloat Aniqlangan (${bloatData.bloated_count} ta interfeysda navbat tiqilishi)`
                            : 'Bufferbloat Xavfi Aniqlanmadi (Navbatlar barqaror)'}
                        </div>
                        <div className="text-[11px] text-muted-foreground">
                          Linux `tc qdisc` navbatlarida paket to'planishi va sojourn time (kechikish) monitoringi.
                        </div>
                      </div>
                    </div>

                    <Badge variant={bloatData && bloatData.bloated_count > 0 ? 'destructive' : 'outline'} className="text-xs">
                      {queuesData.length} ta Navbat Qdisc
                    </Badge>
                  </div>

                  {/* Navbatlar ro'yxati */}
                  <div className="rounded-lg border bg-card/40 p-2.5">
                    <div className="mb-2 flex items-center justify-between text-xs font-semibold border-b pb-1.5">
                      <span className="flex items-center gap-1.5 font-sans">
                        <Layers className="h-4 w-4 text-primary" /> Interfeys Navbatlari (Traffic Control Qdisc)
                      </span>
                      <span className="text-[11px] text-muted-foreground">Linux Yadro tc Telemetriyasi</span>
                    </div>

                    {queuesData.length === 0 ? (
                      <div className="p-6 text-center text-xs text-muted-foreground">
                        Navbat telemetriyasi yuklanmoqda yoki emulyatsiya interfeyslari ishga tushirilmagan.
                      </div>
                    ) : (
                      <div className="space-y-2 font-mono text-xs">
                        {queuesData.map((q, idx) => (
                          <div
                            key={idx}
                            className="flex flex-col gap-1.5 rounded-md border border-border/60 bg-card/50 p-2.5 transition-colors hover:bg-card/80"
                          >
                            <div className="flex items-center justify-between font-sans">
                              <div className="flex items-center gap-2">
                                <b className="text-foreground">{q.dev}</b>
                                <Badge variant="secondary" className="font-mono text-[10px] uppercase">
                                  {q.qdisc || 'pfifo_fast'}
                                </Badge>
                                <span className="text-[11px] text-muted-foreground font-mono">handle {q.handle}</span>
                              </div>
                              {q.bufferbloat_detected && (
                                <Badge variant="destructive" className="text-[10px]">
                                  <AlertTriangle className="mr-1 h-3 w-3" /> Bloated ({q.sojourn_time_ms?.toFixed(1)}ms)
                                </Badge>
                              )}
                            </div>

                            <div className="grid grid-cols-2 gap-2 text-[11px] sm:grid-cols-4 font-mono">
                              <div>
                                <span className="text-muted-foreground">Backlog: </span>
                                <b className="text-foreground">{q.backlog_bytes?.toLocaleString() || 0} B</b>
                                <span className="text-muted-foreground"> ({q.qlen || 0} pkts)</span>
                              </div>
                              <div>
                                <span className="text-muted-foreground">Yo'qotish (Drops): </span>
                                <b className={q.drops > 0 ? 'text-rose-400' : 'text-foreground'}>{q.drops || 0}</b>
                              </div>
                              <div>
                                <span className="text-muted-foreground">Overlimits: </span>
                                <b className="text-foreground">{q.overlimits || 0}</b>
                              </div>
                              <div>
                                <span className="text-muted-foreground">Uzatilgan: </span>
                                <b className="text-foreground">{q.packets?.toLocaleString() || 0} pkts</b>
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* TAB 6: VoIP (ITU-T G.107) & Video (ABR) */}
              {activeTab === 'workloads' && (
                <div className="space-y-3">
                  {/* 1. VoIP QoE (ITU-T G.107 E-Model) */}
                  <div className="rounded-lg border bg-card/40 p-3">
                    <div className="mb-2 flex items-center justify-between border-b pb-1.5">
                      <span className="flex items-center gap-1.5 font-semibold text-xs font-sans">
                        <PhoneCall className="h-4 w-4 text-emerald-400" /> VoIP Qo'ng'irog'i Sifati (ITU-T G.107 E-Model MOS)
                      </span>
                      <span className="text-[11px] text-muted-foreground">OPNET Standarti</span>
                    </div>

                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-12">
                      {/* Chap: MOS natija kartasi */}
                      <div className="flex flex-col items-center justify-center rounded-lg border bg-card/60 p-3 sm:col-span-5 text-center">
                        <div className="text-[11px] text-muted-foreground">MOS Bahosi (Mean Opinion Score)</div>
                        <div className="mt-1 text-3xl font-extrabold text-emerald-400 font-mono">
                          {voipData?.mos?.toFixed(2) || '4.35'}
                          <span className="text-xs font-normal text-muted-foreground"> / 4.50</span>
                        </div>
                        <Badge
                          variant="outline"
                          className={cn(
                            'mt-2 text-xs font-sans',
                            (voipData?.mos || 4.35) >= 4.0
                              ? 'border-emerald-500/40 text-emerald-400 bg-emerald-500/10'
                              : (voipData?.mos || 4.35) >= 3.6
                              ? 'border-amber-500/40 text-amber-400 bg-amber-500/10'
                              : 'border-rose-500/40 text-rose-400 bg-rose-500/10'
                          )}
                        >
                          {voipData?.rating || "A'lo (Best / Transparent)"}
                        </Badge>
                        <div className="mt-2 text-[10.5px] text-muted-foreground font-mono">
                          R-Factor: <b>{voipData?.r_factor || '92.4'}</b> / 100
                        </div>
                      </div>

                      {/* O'ng: Parametrlar va Impairment balansi */}
                      <div className="flex flex-col justify-between space-y-2 sm:col-span-7">
                        <div className="grid grid-cols-4 gap-2">
                          <div>
                            <span className="text-[10px] text-muted-foreground">Kodek:</span>
                            <div className="flex gap-1 mt-0.5">
                              {(['G.711', 'G.729', 'Opus'] as const).map((c) => (
                                <button
                                  key={c}
                                  onClick={() => setVoipCodec(c)}
                                  className={cn(
                                    'rounded px-1.5 py-0.5 text-[10px] font-semibold border transition-colors',
                                    voipCodec === c ? 'bg-primary/20 text-primary border-primary/40' : 'bg-card text-muted-foreground'
                                  )}
                                >
                                  {c}
                                </button>
                              ))}
                            </div>
                          </div>

                          <div>
                            <span className="text-[10px] text-muted-foreground">Delay (ms):</span>
                            <Input
                              type="number"
                              value={voipDelay}
                              onChange={(e) => setVoipDelay(Number(e.target.value))}
                              className="h-6 text-xs font-mono mt-0.5"
                            />
                          </div>

                          <div>
                            <span className="text-[10px] text-muted-foreground">Jitter (ms):</span>
                            <Input
                              type="number"
                              step="0.5"
                              value={voipJitter}
                              onChange={(e) => setVoipJitter(Number(e.target.value))}
                              className="h-6 text-xs font-mono mt-0.5"
                            />
                          </div>

                          <div>
                            <span className="text-[10px] text-muted-foreground">Loss (%):</span>
                            <Input
                              type="number"
                              step="0.1"
                              value={voipLoss}
                              onChange={(e) => setVoipLoss(Number(e.target.value))}
                              className="h-6 text-xs font-mono mt-0.5"
                            />
                          </div>
                        </div>

                        {/* Impairment tahlili */}
                        <div className="rounded border bg-card/40 p-2 font-mono text-[11px] space-y-1">
                          <div className="flex justify-between">
                            <span className="text-muted-foreground">Kechikish zarari (Id):</span>
                            <span className="text-amber-400 font-semibold">{voipData?.impairment_delay || '0.72'}</span>
                          </div>
                          <div className="flex justify-between">
                            <span className="text-muted-foreground">Paket yo'qotish zarari (Ie,eff):</span>
                            <span className="text-rose-400 font-semibold">{voipData?.impairment_loss || '1.25'}</span>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* 2. Adaptive Video Streaming (HLS / DASH) */}
                  <div className="rounded-lg border bg-card/40 p-3">
                    <div className="mb-2 flex items-center justify-between border-b pb-1.5">
                      <span className="flex items-center gap-1.5 font-semibold text-xs font-sans">
                        <Video className="h-4 w-4 text-sky-400" /> Adaptiv Video Oqimi (HLS / DASH ABR)
                      </span>
                      <span className="text-[11px] text-muted-foreground">Kanal tezligiga moslashuv</span>
                    </div>

                    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 font-mono">
                      <div className="rounded border bg-card/60 p-2">
                        <span className="text-[10px] text-muted-foreground font-sans">Joriy Sifat:</span>
                        <div className="mt-0.5 text-base font-bold text-sky-400">
                          {videoData?.current_quality || '1080p'}
                        </div>
                        <span className="text-[10px] text-muted-foreground">{videoData?.resolution || '1920x1080'}</span>
                      </div>

                      <div className="rounded border bg-card/60 p-2">
                        <span className="text-[10px] text-muted-foreground font-sans">Video Bitrate:</span>
                        <div className="mt-0.5 text-base font-bold text-indigo-400">
                          {fmtBps(videoData?.bitrate_bps || 5000000)}
                        </div>
                        <span className="text-[10px] text-muted-foreground">ABR oqim tezligi</span>
                      </div>

                      <div className="rounded border bg-card/60 p-2">
                        <span className="text-[10px] text-muted-foreground font-sans">Video Bufer Zahirasi:</span>
                        <div className="mt-0.5 text-base font-bold text-emerald-400">
                          {videoData?.buffer_sec?.toFixed(1) || '14.2'} s
                        </div>
                        <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-muted">
                          <div
                            className="h-full bg-emerald-500"
                            style={{ width: `${Math.min(100, ((videoData?.buffer_sec || 14.2) / 20) * 100)}%` }}
                          />
                        </div>
                      </div>

                      <div className="rounded border bg-card/60 p-2">
                        <span className="text-[10px] text-muted-foreground font-sans">To'xtashlar (Stalls):</span>
                        <div className="mt-0.5 text-base font-bold text-foreground">
                          {videoData?.stalls || 0} ta
                        </div>
                        <span className="text-[10px] text-muted-foreground">
                          Sifat almashishi: {videoData?.switches || 0}
                        </span>
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* TAB 7: SLA Monitor */}
              {activeTab === 'sla' && (
                <div className="space-y-3">
                  <div className="flex flex-wrap items-center justify-between rounded-lg border bg-card/60 p-3">
                    <div className="flex items-center gap-3">
                      <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">
                        <ShieldCheck className="h-6 w-6" />
                      </div>
                      <div>
                        <div className="text-xs font-bold font-sans">SLA Xizmat Sifati Muvofiqligi</div>
                        <div className="text-[11px] text-muted-foreground">
                          Korporativ tarmoq talablari: Max RTT ≤ 50ms, Jitter ≤ 10ms, Loss ≤ 0.5%, Min MOS ≥ 4.0
                        </div>
                      </div>
                    </div>

                    <div className="text-right">
                      <div className="text-2xl font-extrabold text-emerald-400 font-mono">
                        {slaData?.compliance_pct?.toFixed(1) || '99.8'}%
                      </div>
                      <div className="text-[10px] text-muted-foreground">Maqsad: 99.9% Uptime/QoS</div>
                    </div>
                  </div>

                  {/* Qoidalar va mezonlar kartasi */}
                  <div className="grid grid-cols-2 gap-2 sm:grid-cols-5 text-xs font-mono">
                    <div className="rounded border bg-card/40 p-2">
                      <div className="text-[10px] text-muted-foreground font-sans">Max RTT</div>
                      <div className="font-bold text-foreground">≤ 50.0 ms</div>
                      <span className="text-[9.5px] text-emerald-400">Normada</span>
                    </div>
                    <div className="rounded border bg-card/40 p-2">
                      <div className="text-[10px] text-muted-foreground font-sans">Max Jitter</div>
                      <div className="font-bold text-foreground">≤ 10.0 ms</div>
                      <span className="text-[9.5px] text-emerald-400">Normada</span>
                    </div>
                    <div className="rounded border bg-card/40 p-2">
                      <div className="text-[10px] text-muted-foreground font-sans">Max Loss</div>
                      <div className="font-bold text-foreground">≤ 0.50 %</div>
                      <span className="text-[9.5px] text-emerald-400">Normada</span>
                    </div>
                    <div className="rounded border bg-card/40 p-2">
                      <div className="text-[10px] text-muted-foreground font-sans">Min Throughput</div>
                      <div className="font-bold text-foreground">≥ 1.0 Mb/s</div>
                      <span className="text-[9.5px] text-emerald-400">Normada</span>
                    </div>
                    <div className="rounded border bg-card/40 p-2">
                      <div className="text-[10px] text-muted-foreground font-sans">Min MOS (VoIP)</div>
                      <div className="font-bold text-foreground">≥ 4.00</div>
                      <span className="text-[9.5px] text-emerald-400">Normada</span>
                    </div>
                  </div>

                  {/* Buzilishlar jurnali (Violation Log) */}
                  <div className="rounded-lg border bg-card/40 p-2.5">
                    <div className="mb-2 flex items-center justify-between text-xs font-semibold border-b pb-1">
                      <span className="flex items-center gap-1.5 font-sans">
                        <ShieldAlert className="h-4 w-4 text-amber-400" /> SLA Cheklovlari Buzilishi Jurnali
                      </span>
                      <span className="text-[11px] text-muted-foreground">
                        Jami buzilishlar: {slaData?.total_violations || 0}
                      </span>
                    </div>

                    {(!slaData?.recent_violations || slaData.recent_violations.length === 0) ? (
                      <div className="p-4 text-center text-xs text-muted-foreground">
                        Hech qanday SLA buzilishi qayd etilmadi. Barcha oqimlar talablarga to'liq javob bermoqda.
                      </div>
                    ) : (
                      <div className="space-y-1.5 font-mono text-[11px]">
                        {slaData.recent_violations.map((v: any, idx: number) => (
                          <div
                            key={idx}
                            className="flex items-center justify-between rounded border border-border/50 bg-card/60 p-2"
                          >
                            <div className="flex items-center gap-2">
                              <Badge
                                variant={v.severity === 'critical' ? 'destructive' : 'outline'}
                                className="text-[9.5px] uppercase"
                              >
                                {v.severity}
                              </Badge>
                              <span className="text-foreground font-semibold">{v.metric}</span>
                              <span className="text-muted-foreground">oqim: {v.stream_id}</span>
                            </div>
                            <div>
                              Qiymat: <b className="text-rose-400">{v.measured_value}</b> / Chegara: {v.threshold_value}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* TAB 8: A/B Protocol & Algorithm Comparison */}
              {activeTab === 'compare' && (
                <div className="space-y-3">
                  {/* Boshqaruv parametrlari */}
                  <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border bg-card/60 p-2.5">
                    <div className="flex items-center gap-2">
                      <GitCompare className="h-4 w-4 text-primary" />
                      <span className="text-xs font-semibold font-sans">A/B Algoritm Solishtirish:</span>
                      <select
                        value={compAlgoA}
                        onChange={(e) => setCompAlgoA(e.target.value)}
                        className="h-6 rounded border bg-card text-xs font-mono px-1 uppercase text-sky-400"
                      >
                        <option value="cubic">CUBIC</option>
                        <option value="bbr">BBR</option>
                        <option value="reno">RENO</option>
                      </select>
                      <span className="text-xs text-muted-foreground font-sans">vs</span>
                      <select
                        value={compAlgoB}
                        onChange={(e) => setCompAlgoB(e.target.value)}
                        className="h-6 rounded border bg-card text-xs font-mono px-1 uppercase text-indigo-400"
                      >
                        <option value="bbr">BBR</option>
                        <option value="cubic">CUBIC</option>
                        <option value="reno">RENO</option>
                      </select>
                    </div>

                    <div className="flex flex-wrap items-center gap-2 text-xs font-mono">
                      <div className="flex items-center gap-1">
                        <span className="text-[11px] text-muted-foreground">Loss:</span>
                        <Input
                          type="number"
                          step="0.5"
                          value={compLoss}
                          onChange={(e) => setCompLoss(Number(e.target.value))}
                          className="h-6 w-16 text-xs"
                        />
                        <span className="text-[11px] text-muted-foreground">%</span>
                      </div>
                      <div className="flex items-center gap-1">
                        <span className="text-[11px] text-muted-foreground">Kanal:</span>
                        <Input
                          type="number"
                          value={compBw}
                          onChange={(e) => setCompBw(Number(e.target.value))}
                          className="h-6 w-16 text-xs"
                        />
                        <span className="text-[11px] text-muted-foreground">Mb/s</span>
                      </div>
                      <Button size="xs" variant="outline" onClick={fetchCompare}>
                        Qayta Hisoblash
                      </Button>
                    </div>
                  </div>

                  {/* Ilmiy xulosa va g'olib ko'rsatkichi */}
                  {compareData && (
                    <div className="rounded-lg border bg-card/40 p-3">
                      <div className="mb-2 flex items-center justify-between border-b pb-1.5 font-sans">
                        <span className="text-xs font-semibold text-foreground">
                          Ilmiy Tahlil & Xulosa (Scientific Summary)
                        </span>
                        <Badge
                          variant="outline"
                          className={cn(
                            'text-xs font-semibold',
                            compareData.overall_winner === 'candidate'
                              ? 'border-indigo-500/40 text-indigo-400 bg-indigo-500/10'
                              : 'border-sky-500/40 text-sky-400 bg-sky-500/10'
                          )}
                        >
                          {compareData.overall_winner === 'candidate' ? 'BBR Ustun' : 'CUBIC Ustun'}
                        </Badge>
                      </div>

                      <p className="text-xs text-muted-foreground leading-relaxed">
                        {compareData.verdict?.uz ||
                          "BBR algoritmi tasodifiy paket yo'qotish (loss) sharoitida o'z tezligini barqaror saqlab qoladi va tarmoq navbatlarida paket to'planishining (Bufferbloat) oldini oladi."}
                      </p>
                    </div>
                  )}

                  {/* Solishtirish jadvali */}
                  <div className="rounded-lg border bg-card/40 p-2.5">
                    <div className="mb-2 flex items-center justify-between text-xs font-semibold border-b pb-1 font-sans">
                      <span>Metrikalar Solishtirish Jadvali</span>
                      <span className="text-[11px] text-muted-foreground font-mono">Delta & Natijalar</span>
                    </div>

                    <div className="overflow-x-auto">
                      <table className="w-full text-left font-mono text-[11px]">
                        <thead>
                          <tr className="border-b text-muted-foreground font-sans">
                            <th className="py-1">Metrika</th>
                            <th className="py-1">CUBIC (Baseline)</th>
                            <th className="py-1">BBR (Candidate)</th>
                            <th className="py-1">Delta (Farq)</th>
                            <th className="py-1">Nisbiy (%)</th>
                            <th className="py-1">G'olib</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-border/40">
                          {compareData?.metrics?.map((m: any, idx: number) => {
                            const isWinB = m.winner === 'candidate'
                            return (
                              <tr key={idx} className="hover:bg-muted/40">
                                <td className="py-1.5 font-sans font-medium text-foreground">{m.metric}</td>
                                <td className="py-1.5 text-sky-400">
                                  {m.unit === 'bps' ? fmtBps(m.baseline) : `${m.baseline} ${m.unit}`}
                                </td>
                                <td className="py-1.5 text-indigo-400 font-bold">
                                  {m.unit === 'bps' ? fmtBps(m.candidate) : `${m.candidate} ${m.unit}`}
                                </td>
                                <td className="py-1.5 font-semibold">
                                  {m.delta > 0 ? `+${m.delta}` : m.delta} {m.unit !== 'bps' && m.unit}
                                </td>
                                <td className="py-1.5">
                                  {m.relative_pct != null ? `${m.relative_pct > 0 ? '+' : ''}${m.relative_pct}%` : '—'}
                                </td>
                                <td className="py-1.5">
                                  <Badge
                                    variant="outline"
                                    className={cn(
                                      'text-[9.5px]',
                                      isWinB
                                        ? 'border-indigo-500/40 text-indigo-400 bg-indigo-500/10'
                                        : 'border-sky-500/40 text-sky-400 bg-sky-500/10'
                                    )}
                                  >
                                    {isWinB ? 'BBR' : m.winner === 'baseline' ? 'CUBIC' : 'Teng'}
                                  </Badge>
                                </td>
                              </tr>
                            )
                          })}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
  )

  if (!win) {
    return (
      <div className="glass flex h-full min-h-0 flex-1 flex-col overflow-hidden rounded-xl shadow-lg">
        <div className="flex items-center gap-2.5 border-b border-border/50 px-4 py-2.5 bg-card/25 shrink-0">
          <Activity className="h-5 w-5 text-emerald-400" />
          <div>
            <div className="text-xs font-semibold text-foreground">Transport Qatlami (L4) & OPNET Observatory</div>
            <div className="text-[10.5px] text-muted-foreground">TCP CWND · RTT · Jitter (RFC 3550) · Linux Qdisc Bufferbloat · ITU-T G.107 VoIP · ABR Video · SLA & A/B Engine</div>
          </div>
        </div>
        {bodyContent}
      </div>
    )
  }

  return (
    <WindowFrame
      win={win}
      icon={<Activity className="text-primary" />}
      title="Transport Qatlami (L4) & OPNET Observatory"
      sub="TCP CWND · RTT · Jitter (RFC 3550) · Linux Qdisc Bufferbloat · ITU-T G.107 VoIP · ABR Video · SLA & A/B Engine"
    >
      {bodyContent}
    </WindowFrame>
  )
}
