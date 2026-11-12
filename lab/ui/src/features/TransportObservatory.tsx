import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Activity,
  AlertTriangle,
  ArrowDownUp,
  Gauge,
  Pause,
  Play,
  RefreshCw,
  Zap,
} from 'lucide-react'
import { LineChart, type Series } from '@/components/charts'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Segmented } from '@/components/ui/tabs'
import { toast } from '@/components/ui/toast'
import { api } from '@/lib/api'
import { fmtBps, fmtMs } from '@/lib/format'
import { type Win } from '@/lib/store'
import { cn } from '@/lib/utils'
import { WindowFrame } from './WindowFrame'

interface StreamSummary {
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
  timestamp?: number
}

interface StreamSample {
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
}

interface StreamDetail extends StreamSummary {
  samples?: StreamSample[]
  history?: StreamSample[]
}

export function TransportObservatory({ win }: { win: Win }) {
  const [streams, setStreams] = useState<StreamSummary[]>([])
  const [selectedStreamId, setSelectedStreamId] = useState<string>('')
  const [streamDetail, setStreamDetail] = useState<StreamDetail | null>(null)
  const [activeTab, setActiveTab] = useState<'cwnd' | 'rtt' | 'rate' | 'drops'>('cwnd')
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [loading, setLoading] = useState(false)
  const timerRef = useRef<number | null>(null)

  // Oqimlar ro'yxatini olish
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

  // Auto-refresh intervali
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
      const res = await api.post<{ ok: boolean; stream_id: string }>('/api/telemetry/simulate', {
        src_ip: type === 'bbr' ? '10.0.1.20' : '10.0.1.10',
        src_port: port,
        dst_ip: type === 'bbr' ? '10.0.2.80' : '10.0.2.20',
        dst_port: type === 'bbr' ? 443 : 80,
        steps: 12,
        has_loss: hasLoss,
      })
      toast(`Simulyatsiya: ${res.stream_id} (${type.toUpperCase()}) yaratildi`, 'ok')
      await fetchStreams(true)
      setSelectedStreamId(res.stream_id)
      await fetchDetail(res.stream_id)
    } catch (e: any) {
      toast('Simulyatsiya xatosi: ' + e.message, 'error')
    } finally {
      setLoading(false)
    }
  }

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
      return { t, cwnd, ssthresh, rtt, rttvar, rate, pacing }
    })
  }, [streamDetail])

  // CWND Grafigi
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

  // RTT & Jitter Grafigi
  const rttSeries = useMemo<Series[]>(() => {
    if (!rawPoints.length) return []
    const baseT = rawPoints[0].t
    return [
      {
        name: 'RTT (ms)',
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

  // Throughput & Pacing Grafigi
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

  const cur = streamDetail || streams.find((s) => s.stream_id === selectedStreamId)
  const totalBps = streams.reduce((acc, s) => acc + (s.delivery_rate_bps || 0), 0)
  const totalRetrans = streams.reduce((acc, s) => acc + (s.total_retrans || s.retrans || 0), 0)

  return (
    <WindowFrame
      win={win}
      icon={<Activity className="text-primary" />}
      title="Transport Qatlami (L4) Observatory"
      sub="TCP CWND · SSTHRESH · RTT · Jitter (RFC 3550) · eBPF Yadro Telemetriyasi"
    >
      <div className="flex min-h-0 flex-1 flex-col gap-2.5 overflow-auto p-3">
        {/* Yuqori boshqaruv paneli */}
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border bg-card/40 p-2">
          <div className="flex items-center gap-2">
            <span
              className={cn(
                'inline-block h-2.5 w-2.5 rounded-full',
                autoRefresh ? 'bg-success animate-pulse' : 'bg-muted-foreground'
              )}
            />
            <span className="text-xs font-semibold">
              Oqimlar soni: <Badge variant="outline">{streams.length}</Badge>
            </span>
            <span className="text-xs text-muted-foreground">
              Umumiy tezlik: <b className="text-foreground">{fmtBps(totalBps)}</b>
            </span>
            {totalRetrans > 0 && (
              <Badge variant="destructive" className="text-[11px]">
                <AlertTriangle className="mr-1 h-3 w-3" />
                {totalRetrans} Qayta yuborish
              </Badge>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-1.5">
            <Button
              size="xs"
              variant="outline"
              onClick={() => runSimulation('cubic')}
              title="Cubic sekin o'sish va ko'payuvchi pasayish simulyatsiyasi"
            >
              <Zap className="mr-1 h-3 w-3 text-sky-400" /> +Cubic Oqim
            </Button>
            <Button
              size="xs"
              variant="outline"
              onClick={() => runSimulation('bbr')}
              title="BBR modeliga asoslangan yuqori tezlik oqimi"
            >
              <Gauge className="mr-1 h-3 w-3 text-indigo-400" /> +BBR Oqim
            </Button>
            <Button
              size="xs"
              variant="outline"
              onClick={() => runSimulation('loss')}
              title="Tarmoqda paket tushishi va Fast Retransmit hodisasi"
            >
              <AlertTriangle className="mr-1 h-3 w-3 text-rose-400" /> +Loss Stsenariy
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
            >
              <RefreshCw className={cn('h-3.5 w-3.5', loading && 'animate-spin')} />
            </Button>
          </div>
        </div>

        {/* Asosiy kontent: Chapda oqimlar jadvali, O'ngda grafiklar */}
        <div className="grid min-h-[460px] flex-1 grid-cols-1 gap-3 lg:grid-cols-12">
          {/* Chap panel (4 ustun): Oqimlar ro'yxati */}
          <div className="flex flex-col rounded-lg border bg-card/30 p-2.5 lg:col-span-4">
            <div className="mb-2 flex items-center justify-between text-xs font-semibold">
              <span className="flex items-center gap-1">
                <ArrowDownUp className="h-3.5 w-3.5" /> Faol L4 Oqimlar
              </span>
              <span className="text-[11px] text-muted-foreground">{streams.length} ta mavjud</span>
            </div>

            <div className="min-h-0 flex-1 overflow-auto">
              {streams.length === 0 ? (
                <div className="flex flex-col items-center justify-center p-6 text-center text-xs text-muted-foreground">
                  <Activity className="mb-2 h-8 w-8 opacity-40" />
                  Hozircha faol TCP/UDP oqimlar yo'q.
                  <span className="mt-1 text-[11px]">
                    Yuqoridagi <b>"+Cubic Oqim"</b> yoki <b>"+Loss"</b> tugmasini bosing yoki Mininet xostlarida trafik ishga tushiring.
                  </span>
                </div>
              ) : (
                <div className="space-y-1.5 font-mono text-[11.5px]">
                  {streams.map((s) => {
                    const isSel = s.stream_id === selectedStreamId
                    const ca = (s.ca_state || 'open').toLowerCase()
                    const caColor =
                      ca === 'recovery' || ca === 'loss'
                        ? 'text-rose-400 bg-rose-500/10 border-rose-500/20'
                        : ca === 'cwr' || ca === 'disorder'
                        ? 'text-amber-400 bg-amber-500/10 border-amber-500/20'
                        : 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20'

                    return (
                      <div
                        key={s.stream_id}
                        onClick={() => setSelectedStreamId(s.stream_id)}
                        className={cn(
                          'cursor-pointer rounded-md border p-2 transition-all hover:bg-accent/60',
                          isSel ? 'border-primary bg-primary/10 shadow-sm' : 'border-border/60 bg-card/40'
                        )}
                      >
                        <div className="flex items-center justify-between font-bold">
                          <span className="truncate text-foreground">
                            {s.src_ip}:{s.src_port} → {s.dst_ip}:{s.dst_port}
                          </span>
                          <span className={cn('rounded px-1 text-[10px] uppercase border', caColor)}>
                            {s.ca_state || 'OPEN'}
                          </span>
                        </div>

                        <div className="mt-1 grid grid-cols-3 gap-1 text-[10.5px] text-muted-foreground font-sans">
                          <div>
                            CWND: <b className="text-foreground">{s.cwnd ?? s.snd_cwnd ?? 10}</b>
                          </div>
                          <div>
                            RTT: <b className="text-foreground">{fmtMs(s.rtt_ms)}</b>
                          </div>
                          <div>
                            Tezlik: <b className="text-foreground">{fmtBps(s.delivery_rate_bps)}</b>
                          </div>
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
                      <div className="mt-0.5 text-base font-bold text-sky-400">
                        {cur.cwnd ?? cur.snd_cwnd ?? '—'}
                        <span className="text-xs font-normal text-muted-foreground">
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
                      <div className="mt-0.5 text-base font-bold text-emerald-400">
                        {fmtMs(cur.rtt_ms)}
                      </div>
                      <div className="text-[10px] text-muted-foreground">
                        ± tebranish: {fmtMs(cur.rttvar_ms)}
                      </div>
                    </CardContent>
                  </Card>

                  <Card className="bg-card/50">
                    <CardContent className="p-2.5">
                      <div className="text-[11px] text-muted-foreground">Delivery / Goodput</div>
                      <div className="mt-0.5 text-base font-bold text-indigo-400">
                        {fmtBps(cur.delivery_rate_bps)}
                      </div>
                      <div className="text-[10px] text-muted-foreground">
                        Acked: {((cur.bytes_acked || 0) / 1024).toFixed(1)} KB
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
                      { value: 'drops', label: 'Yadro Soket Parametrlari' },
                    ]}
                  />
                  <span className="text-[11px] text-muted-foreground">
                    {rawPoints.length} ta o'lchov nuqtasi
                  </span>
                </div>

                {/* Grafik oynasi */}
                <div className="min-h-0 flex-1">
                  {activeTab === 'cwnd' && (
                    <LineChart
                      series={cwndSeries}
                      fmt={(v) => `${Math.round(v)} seg`}
                      title="TCP Congestion Window (CWND) va Slow Start Threshold (ssthresh) dinamikasi"
                      xFmt={(x) => `${Math.round(x)}s`}
                      note="Sawtooth to'lqini: paket yo'qotish aniqlanganda CWND multiplicatively kamayadi, so'ngra chiziqli o'sadi."
                    />
                  )}

                  {activeTab === 'rtt' && (
                    <LineChart
                      series={rttSeries}
                      fmt={(v) => `${v.toFixed(1)} ms`}
                      title="Smoothed Round-Trip Time (RTT) va Jitter tebranishi"
                      xFmt={(x) => `${Math.round(x)}s`}
                      note="Bufferbloat paytida RTT ortadi; kabel bo'shaganda normal darajaga qaytadi."
                    />
                  )}

                  {activeTab === 'rate' && (
                    <LineChart
                      series={rateSeries}
                      fmt={(v) => `${v.toFixed(1)} Mb/s`}
                      title="Haqiqiy yetkazib berish (Delivery Rate) va Yadro Pacing tezligi"
                      xFmt={(x) => `${Math.round(x)}s`}
                      note="BBR va zamonaviy Linux yadrosi soket pacing mexanizmi orqali paketlarni bir xil oraliqda chiqaradi."
                    />
                  )}

                  {activeTab === 'drops' && (
                    <div className="rounded-lg border bg-card/40 p-4 font-mono text-xs">
                      <div className="mb-3 text-sm font-semibold font-sans">
                        Linux `struct tcp_info` va Soket Holati Tafsilotlari:
                      </div>
                      <div className="grid grid-cols-2 gap-4">
                        <div className="space-y-1.5 border-r pr-4">
                          <div className="flex justify-between border-b pb-1">
                            <span className="text-muted-foreground">Soket identifikatori:</span>
                            <span>{cur.stream_id}</span>
                          </div>
                          <div className="flex justify-between border-b pb-1">
                            <span className="text-muted-foreground">Tugun (Node):</span>
                            <span>localhost (Mininet NS)</span>
                          </div>
                          <div className="flex justify-between border-b pb-1">
                            <span className="text-muted-foreground">MSS (Max Segment Size):</span>
                            <span>1460 bayt</span>
                          </div>
                          <div className="flex justify-between border-b pb-1">
                            <span className="text-muted-foreground">Qabul qilish bufiri (rcvspace):</span>
                            <span>65,535 bayt</span>
                          </div>
                        </div>

                        <div className="space-y-1.5">
                          <div className="flex justify-between border-b pb-1">
                            <span className="text-muted-foreground">CA Algoritm:</span>
                            <span className="text-primary font-bold">{cur.congestion_algo || 'cubic'}</span>
                          </div>
                          <div className="flex justify-between border-b pb-1">
                            <span className="text-muted-foreground">CA Holati (tcpi_ca_state):</span>
                            <span className="uppercase text-emerald-400">{cur.ca_state || 'TCP_CA_Open'}</span>
                          </div>
                          <div className="flex justify-between border-b pb-1">
                            <span className="text-muted-foreground">Tasdiqlangan baytlar (bytes_acked):</span>
                            <span>{cur.bytes_acked?.toLocaleString() || '0'} B</span>
                          </div>
                          <div className="flex justify-between border-b pb-1">
                            <span className="text-muted-foreground">Yadro Retransmits (tcpi_retrans):</span>
                            <span className={cur.retrans ? 'text-rose-400 font-bold' : ''}>
                              {cur.retrans || 0}
                            </span>
                          </div>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              </>
            ) : (
              <div className="grid h-full place-items-center rounded-lg border bg-card/20 text-xs text-muted-foreground">
                Tahlil qilish uchun chap ro'yxatdan biror L4 oqimini tanlang.
              </div>
            )}
          </div>
        </div>
      </div>
    </WindowFrame>
  )
}
