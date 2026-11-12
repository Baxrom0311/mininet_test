import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Activity,
  AlertOctagon,
  AlertTriangle,
  ArrowDownUp,
  CheckCircle2,
  Clock,
  Cpu,
  Download,
  Flame,
  Globe,
  HardDrive,
  Layers,
  Play,
  Radio,
  RefreshCw,
  Search,
  Server,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Sliders,
  Square,
  Trash2,
  XCircle,
  Zap,
} from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input, Label } from '@/components/ui/input'
import { DataTable } from '@/components/ui/table'
import { Segmented } from '@/components/ui/tabs'
import { toast } from '@/components/ui/toast'
import { api } from '@/lib/api'
import { fmtBps } from '@/lib/format'
import { type Win } from '@/lib/store'
import { cn } from '@/lib/utils'
import { WindowFrame } from './WindowFrame'

// ──────────────────────────────── Data Models ────────────────────────────────

export interface ConntrackTuple {
  src_ip: string
  src_port: number
  dst_ip: string
  dst_port: number
  proto: string
}

export interface NATMapping {
  enabled?: boolean
  nat_type?: string
  original_src?: string
  original_sport?: number
  original_dst?: string
  original_dport?: number
  translated_src?: string
  translated_sport?: number
  translated_dst?: string
  translated_dport?: number
}

export interface ConntrackItem {
  id: string
  proto: string
  tcp_state?: string | null
  fw_state?: string
  status?: string[]
  orig: ConntrackTuple
  reply: ConntrackTuple
  nat?: NATMapping | null
  timeout_s?: number
  remaining_ttl: number
  packets_orig?: number
  bytes_orig?: number
  packets_reply?: number
  bytes_reply?: number
  total_packets?: number
  total_bytes?: number
  use?: number
}

export interface ConntrackStats {
  total: number
  tcp: number
  udp: number
  icmp: number
  established: number
  nat_count: number
  max_entries?: number
}

export interface SecurityAlertItem {
  id: string
  timestamp: number
  iso_time?: string
  severity: 'critical' | 'high' | 'medium' | 'low' | string
  attack_type: string
  source: string
  target: string
  description: string
  mitigation: string
  status?: string
  details?: Record<string, any>
}

export type AttackKind = 'syn_flood' | 'udp_reflection' | 'slowloris' | 'arp_spoof'

export interface AttackParams {
  attack_type: AttackKind
  target_ip?: string
  target_port?: number
  pps?: number
  duration_s?: number
  syn_cookies_enabled?: boolean
  backlog_size?: number
  victim_ip?: string
  reflector_type?: 'dns' | 'ntp' | 'snmp' | 'ssdp'
  request_pps?: number
  pipe_capacity_mbps?: number
  port?: number
  connections?: number
  header_interval_s?: number
  server_max_connections?: number
  gateway_ip?: string
  attacker_ip?: string
  attacker_mac?: string
  dai_enabled?: boolean
}

export interface AttackTimeSeriesPoint {
  time_s: number
  cpu_utilization_pct?: number
  backlog_used?: number
  dropped_syns?: number
  incoming_pps?: number
  inbound_mbps?: number
  dropped_mbps?: number
  packet_loss_pct?: number
  active_slow_connections?: number
  pool_utilization_pct?: number
  response_delay_ms?: number
  server_availability_pct?: number
  mitm_active?: boolean
  intercepted_packets_cumulative?: number
  dai_blocked_packets?: number
}

export interface AttackSimResult {
  attack_type: string
  duration_s: number
  connections?: number
  server_max_connections?: number
  cpu_impact_pct?: number
  server_availability_pct?: number
  total_packets?: number
  dropped_syns?: number
  backlog_exhausted?: boolean
  syn_cookies_enabled?: boolean
  pipe_saturated?: boolean
  inbound_reflected_mbps?: number
  pipe_capacity_mbps?: number
  dropped_traffic_mbps?: number
  victim_packet_loss_pct?: number
  pool_exhausted?: boolean
  pool_utilization_pct?: number
  response_delay_ms?: number
  mitm_active?: boolean
  intercepted_packets?: number
  dai_enabled?: boolean
  dai_message?: string
  mitigation_stats?: Record<string, any>
  alert_generated?: SecurityAlertItem
  time_series?: AttackTimeSeriesPoint[]
}

// ──────────────────────────────── Synthetic Fallback Data ────────────────────────────────

function generateInitialConntrack(): ConntrackItem[] {
  return [
    {
      id: 'ct_tcp_01',
      proto: 'tcp',
      tcp_state: 'ESTABLISHED',
      fw_state: 'ESTABLISHED',
      status: ['SEEN_REPLY', 'ASSURED'],
      orig: { src_ip: '10.0.0.1', src_port: 52410, dst_ip: '10.0.0.2', dst_port: 80, proto: 'tcp' },
      reply: { src_ip: '10.0.0.2', src_port: 80, dst_ip: '10.0.0.1', dst_port: 52410, proto: 'tcp' },
      remaining_ttl: 431980,
      timeout_s: 432000,
      packets_orig: 1420,
      bytes_orig: 184500,
      packets_reply: 1980,
      bytes_reply: 1482000,
      total_packets: 3400,
      total_bytes: 1666500,
    },
    {
      id: 'ct_tcp_02',
      proto: 'tcp',
      tcp_state: 'ESTABLISHED',
      fw_state: 'ESTABLISHED',
      status: ['SEEN_REPLY', 'ASSURED'],
      orig: { src_ip: '10.0.0.3', src_port: 49152, dst_ip: '198.51.100.10', dst_port: 443, proto: 'tcp' },
      reply: { src_ip: '198.51.100.10', src_port: 443, dst_ip: '203.0.113.1', dst_port: 49152, proto: 'tcp' },
      nat: {
        enabled: true,
        nat_type: 'snat',
        original_src: '10.0.0.3',
        original_sport: 49152,
        translated_src: '203.0.113.1',
        translated_sport: 49152,
        original_dst: '198.51.100.10',
        original_dport: 443,
        translated_dst: '198.51.100.10',
        translated_dport: 443,
      },
      remaining_ttl: 431720,
      timeout_s: 432000,
      packets_orig: 840,
      bytes_orig: 92400,
      packets_reply: 1120,
      bytes_reply: 840200,
      total_packets: 1960,
      total_bytes: 932600,
    },
    {
      id: 'ct_tcp_03',
      proto: 'tcp',
      tcp_state: 'SYN_SENT',
      fw_state: 'NEW',
      status: [],
      orig: { src_ip: '10.0.0.4', src_port: 58820, dst_ip: '10.0.0.2', dst_port: 22, proto: 'tcp' },
      reply: { src_ip: '10.0.0.2', src_port: 22, dst_ip: '10.0.0.4', dst_port: 58820, proto: 'tcp' },
      remaining_ttl: 84,
      timeout_s: 120,
      packets_orig: 2,
      bytes_orig: 120,
      packets_reply: 0,
      bytes_reply: 0,
      total_packets: 2,
      total_bytes: 120,
    },
    {
      id: 'ct_tcp_04',
      proto: 'tcp',
      tcp_state: 'TIME_WAIT',
      fw_state: 'ESTABLISHED',
      status: ['SEEN_REPLY', 'ASSURED'],
      orig: { src_ip: '10.0.0.1', src_port: 51200, dst_ip: '10.0.0.5', dst_port: 8080, proto: 'tcp' },
      reply: { src_ip: '10.0.0.5', src_port: 8080, dst_ip: '10.0.0.1', dst_port: 51200, proto: 'tcp' },
      remaining_ttl: 42,
      timeout_s: 120,
      packets_orig: 18,
      bytes_orig: 1240,
      packets_reply: 24,
      bytes_reply: 8900,
      total_packets: 42,
      total_bytes: 10140,
    },
    {
      id: 'ct_tcp_05',
      proto: 'tcp',
      tcp_state: 'FIN_WAIT',
      fw_state: 'ESTABLISHED',
      status: ['SEEN_REPLY', 'ASSURED'],
      orig: { src_ip: '10.0.0.2', src_port: 60100, dst_ip: '10.0.0.6', dst_port: 3306, proto: 'tcp' },
      reply: { src_ip: '10.0.0.6', src_port: 3306, dst_ip: '10.0.0.2', dst_port: 60100, proto: 'tcp' },
      remaining_ttl: 78,
      timeout_s: 120,
      packets_orig: 94,
      bytes_orig: 12800,
      packets_reply: 142,
      bytes_reply: 48900,
      total_packets: 236,
      total_bytes: 61700,
    },
    {
      id: 'ct_udp_01',
      proto: 'udp',
      tcp_state: null,
      fw_state: 'ESTABLISHED',
      status: ['SEEN_REPLY', 'ASSURED'],
      orig: { src_ip: '10.0.0.1', src_port: 48100, dst_ip: '8.8.8.8', dst_port: 53, proto: 'udp' },
      reply: { src_ip: '8.8.8.8', src_port: 53, dst_ip: '203.0.113.1', dst_port: 48100, proto: 'udp' },
      nat: {
        enabled: true,
        nat_type: 'snat',
        original_src: '10.0.0.1',
        original_sport: 48100,
        translated_src: '203.0.113.1',
        translated_sport: 48100,
        original_dst: '8.8.8.8',
        original_dport: 53,
        translated_dst: '8.8.8.8',
        translated_dport: 53,
      },
      remaining_ttl: 154,
      timeout_s: 180,
      packets_orig: 14,
      bytes_orig: 1020,
      packets_reply: 14,
      bytes_reply: 2450,
      total_packets: 28,
      total_bytes: 3470,
    },
    {
      id: 'ct_udp_02',
      proto: 'udp',
      tcp_state: null,
      fw_state: 'NEW',
      status: ['UNREPLIED'],
      orig: { src_ip: '10.0.0.3', src_port: 123, dst_ip: '162.159.200.1', dst_port: 123, proto: 'udp' },
      reply: { src_ip: '162.159.200.1', src_port: 123, dst_ip: '10.0.0.3', dst_port: 123, proto: 'udp' },
      remaining_ttl: 18,
      timeout_s: 30,
      packets_orig: 1,
      bytes_orig: 76,
      packets_reply: 0,
      bytes_reply: 0,
      total_packets: 1,
      total_bytes: 76,
    },
    {
      id: 'ct_icmp_01',
      proto: 'icmp',
      tcp_state: null,
      fw_state: 'ESTABLISHED',
      status: ['SEEN_REPLY'],
      orig: { src_ip: '10.0.0.1', src_port: 1, dst_ip: '10.0.0.2', dst_port: 0, proto: 'icmp' },
      reply: { src_ip: '10.0.0.2', src_port: 0, dst_ip: '10.0.0.1', dst_port: 1, proto: 'icmp' },
      remaining_ttl: 26,
      timeout_s: 30,
      packets_orig: 5,
      bytes_orig: 420,
      packets_reply: 5,
      bytes_reply: 420,
      total_packets: 10,
      total_bytes: 840,
    },
    {
      id: 'ct_dnat_01',
      proto: 'tcp',
      tcp_state: 'ESTABLISHED',
      fw_state: 'ESTABLISHED',
      status: ['SEEN_REPLY', 'ASSURED'],
      orig: { src_ip: '198.51.100.44', src_port: 41200, dst_ip: '203.0.113.1', dst_port: 80, proto: 'tcp' },
      reply: { src_ip: '10.0.0.2', src_port: 8080, dst_ip: '198.51.100.44', dst_port: 41200, proto: 'tcp' },
      nat: {
        enabled: true,
        nat_type: 'dnat',
        original_src: '198.51.100.44',
        original_sport: 41200,
        translated_src: '198.51.100.44',
        translated_sport: 41200,
        original_dst: '203.0.113.1',
        original_dport: 80,
        translated_dst: '10.0.0.2',
        translated_dport: 8080,
      },
      remaining_ttl: 431500,
      timeout_s: 432000,
      packets_orig: 320,
      bytes_orig: 24800,
      packets_reply: 490,
      bytes_reply: 489000,
      total_packets: 810,
      total_bytes: 513800,
    },
  ]
}

function generateInitialAlerts(): SecurityAlertItem[] {
  return [
    {
      id: 'alert_syn_01',
      timestamp: Date.now() - 45000,
      iso_time: new Date(Date.now() - 45000).toISOString(),
      severity: 'critical',
      attack_type: 'syn_flood',
      source: '10.0.0.99 (Botnet Worker)',
      target: '10.0.0.2:80',
      description: "TCP SYN Flood hujumi: Sekundiga 15,000 SYN paketi aniqlandi. SYN backlog navbati (1024) to'lib, qonuniy ulanishlar rad etilmoqda.",
      mitigation: "Zudlik bilan 'sysctl -w net.ipv4.tcp_syncookies=1' ni yoqing, 'net.ipv4.tcp_max_syn_backlog' ni 4096 ga oshiring va iptables SYN rate limiting qo'llang.",
      status: 'active',
      details: { pps: 15000, backlog_size: 1024, dropped_syns: 89400 },
    },
    {
      id: 'alert_udp_01',
      timestamp: Date.now() - 120000,
      iso_time: new Date(Date.now() - 120000).toISOString(),
      severity: 'high',
      attack_type: 'udp_reflection',
      source: 'Ochiq DNS Resolverlar (Port 53 ANY)',
      target: '10.0.0.5',
      description: "DNS Amplification Reflection: Kuchaytirish koeffitsienti ~54x. Qurbon kanaliga 48.6 Mbps kiruvchi flood trafigi kelmoqda.",
      mitigation: "DNS javoblariga Response Rate Limiting (RRL) qo'ying, upstream provayderdan BCP 38 / uRPF tekshiruvini talab qiling.",
      status: 'active',
      details: { amplification_ratio: 54, inbound_mbps: 48.6, pipe_capacity_mbps: 100 },
    },
    {
      id: 'alert_slow_01',
      timestamp: Date.now() - 300000,
      iso_time: new Date(Date.now() - 300000).toISOString(),
      severity: 'medium',
      attack_type: 'slowloris',
      source: '10.0.0.77 (Slowloris Pool)',
      target: '10.0.0.2:80',
      description: "Slowloris DoS: 180 ta chala HTTP GET sarlavhasi ochilgan holda ushlab turilibdi. Server worker pool bandligi: 90%.",
      mitigation: "Web-serverda mod_reqtimeout (RequestReadTimeout header=10-20,MinRate=500) ni sozlang yoki Nginx/HAProxy teskari proksi qo'llang.",
      status: 'active',
      details: { connections: 180, pool_utilization_pct: 90.0, response_delay_ms: 1350 },
    },
    {
      id: 'alert_arp_01',
      timestamp: Date.now() - 600000,
      iso_time: new Date(Date.now() - 600000).toISOString(),
      severity: 'critical',
      attack_type: 'arp_spoof',
      source: '10.0.0.99 (00:11:22:33:44:aa)',
      target: '10.0.0.3 <-> 10.0.0.1 (Gateway)',
      description: "Gratuitous ARP Poisoning: Hujumchi o'zini shlyuz (10.0.0.1) sifatida ko'rsatib, barcha L3 paketlarni o'ziga burib olmoqda (MitM).",
      mitigation: "L2 kommutatorlarda Dynamic ARP Inspection (DAI) va DHCP Snooping funksiyalarini zudlik bilan yoqing yoki statik ARP jadvallarini qo'llang.",
      status: 'active',
      details: { attacker_mac: '00:11:22:33:44:aa', claimed_ip: '10.0.0.1', intercepted_packets: 420 },
    },
  ]
}

// ──────────────────────────────── SecurityStudio Component ────────────────────────────────

export function SecurityStudio({ win }: { win?: Win }) {
  const [activeTab, setActiveTab] = useState<'conntrack' | 'attacks' | 'alerts'>('conntrack')

  // Conntrack State
  const [conntrackList, setConntrackList] = useState<ConntrackItem[]>(generateInitialConntrack)
  const [searchConntrack, setSearchConntrack] = useState('')
  const [protoFilter, setProtoFilter] = useState<'all' | 'tcp' | 'udp' | 'icmp'>('all')
  const [stateFilter, setStateFilter] = useState<string>('all')
  const [natOnly, setNatOnly] = useState(false)
  const [autoRefreshCt, setAutoRefreshCt] = useState(true)
  const [loadingCt, setLoadingCt] = useState(false)

  // Attack Simulator State
  const [selectedAttack, setSelectedAttack] = useState<AttackKind>('syn_flood')
  // SYN Flood controls
  const [synTargetIp, setSynTargetIp] = useState('10.0.0.2')
  const [synTargetPort, setSynTargetPort] = useState(80)
  const [synPps, setSynPps] = useState(15000)
  const [synDuration, setSynDuration] = useState(10)
  const [synCookies, setSynCookies] = useState(false)

  // UDP Reflection controls
  const [udpVictimIp, setUdpVictimIp] = useState('10.0.0.5')
  const [udpReflectorType, setUdpReflectorType] = useState<'dns' | 'ntp'>('dns')
  const [udpRequestPps, setUdpRequestPps] = useState(1000)
  const [udpDuration, setUdpDuration] = useState(10)

  // Slowloris controls
  const [slowTargetIp, setSlowTargetIp] = useState('10.0.0.2')
  const [slowTargetPort, setSlowTargetPort] = useState(80)
  const [slowConnections, setSlowConnections] = useState(200)
  const [slowInterval, setSlowInterval] = useState(8)

  // ARP Spoof controls
  const [arpVictimIp, setArpVictimIp] = useState('10.0.0.3')
  const [arpGatewayIp, setArpGatewayIp] = useState('10.0.0.1')
  const [arpAttackerIp, setArpAttackerIp] = useState('10.0.0.99')
  const [arpDuration, setArpDuration] = useState(10)
  const [arpDaiEnabled, setArpDaiEnabled] = useState(false)

  // Attack Execution & Dynamics
  const [isAttacking, setIsAttacking] = useState(false)
  const [attackProgress, setAttackProgress] = useState(0) // 0 to 100%
  const [currentAttackResult, setCurrentAttackResult] = useState<AttackSimResult | null>(null)
  const [liveCpu, setLiveCpu] = useState(12)
  const [liveQueue, setLiveQueue] = useState(5)
  const [liveDrops, setLiveDrops] = useState(0)
  const [liveBandwidth, setLiveBandwidth] = useState(1.2)
  const [liveChartPoints, setLiveChartPoints] = useState<{ t: number; cpu: number; queue: number; drops: number }[]>([])
  const attackTimerRef = useRef<number | null>(null)

  // Alerts State
  const [alertsList, setAlertsList] = useState<SecurityAlertItem[]>(generateInitialAlerts)
  const [searchAlerts, setSearchAlerts] = useState('')
  const [severityFilter, setSeverityFilter] = useState<'all' | 'critical' | 'high' | 'medium' | 'low'>('all')
  const [expandedAlertId, setExpandedAlertId] = useState<string | null>(null)

  // ──────────────────────── Conntrack Polling & Countdown ────────────────────────

  const fetchConntrack = useCallback(async (quiet = true) => {
    try {
      if (!quiet) setLoadingCt(true)
      const res = await api.get<{ entries?: ConntrackItem[]; conntrack?: ConntrackItem[]; connections?: ConntrackItem[] }>('/api/security/conntrack')
      const items = res?.connections || res?.entries || res?.conntrack
      if (Array.isArray(items) && items.length > 0) {
        setConntrackList(items)
      }
    } catch {
      // Backend bo'lmasa mavjud synth data qoladi
    } finally {
      if (!quiet) setLoadingCt(false)
    }
  }, [])

  // Auto-refresh interval
  useEffect(() => {
    fetchConntrack(true)
    if (!autoRefreshCt) return
    const id = window.setInterval(() => {
      fetchConntrack(true)
    }, 4000)
    return () => clearInterval(id)
  }, [autoRefreshCt, fetchConntrack])

  // Live countdown ticker (ticking remaining_ttl every 1s)
  useEffect(() => {
    const id = window.setInterval(() => {
      setConntrackList((prev) =>
        prev.map((item) => {
          const nextTtl = Math.max(0, item.remaining_ttl - 1)
          return {
            ...item,
            remaining_ttl: nextTtl === 0 && item.timeout_s ? item.timeout_s : nextTtl,
          }
        })
      )
    }, 1000)
    return () => clearInterval(id)
  }, [])

  // ──────────────────────── Alerts Polling ────────────────────────

  const fetchAlerts = useCallback(async () => {
    try {
      const res = await api.get<{ alerts?: SecurityAlertItem[] }>('/api/security/alerts')
      if (Array.isArray(res?.alerts) && res.alerts.length > 0) {
        setAlertsList(res.alerts)
      }
    } catch {
      // Fallback
    }
  }, [])

  useEffect(() => {
    fetchAlerts()
  }, [fetchAlerts])

  // ──────────────────────── Attack Simulation Runner ────────────────────────

  const stopAttack = useCallback(() => {
    if (attackTimerRef.current) {
      clearInterval(attackTimerRef.current)
      attackTimerRef.current = null
    }
    setIsAttacking(false)
  }, [])

  const startAttack = async () => {
    stopAttack()
    setIsAttacking(true)
    setAttackProgress(0)
    setLiveChartPoints([])

    let targetDuration = 10
    const attackPayload: AttackParams = { attack_type: selectedAttack }

    if (selectedAttack === 'syn_flood') {
      targetDuration = synDuration
      attackPayload.target_ip = synTargetIp
      attackPayload.target_port = synTargetPort
      attackPayload.pps = synPps
      attackPayload.duration_s = synDuration
      attackPayload.syn_cookies_enabled = synCookies
      attackPayload.backlog_size = 1024
    } else if (selectedAttack === 'udp_reflection') {
      targetDuration = udpDuration
      attackPayload.victim_ip = udpVictimIp
      attackPayload.reflector_type = udpReflectorType
      attackPayload.request_pps = udpRequestPps
      attackPayload.duration_s = udpDuration
      attackPayload.pipe_capacity_mbps = 100.0
    } else if (selectedAttack === 'slowloris') {
      targetDuration = 10
      attackPayload.target_ip = slowTargetIp
      attackPayload.port = slowTargetPort
      attackPayload.connections = slowConnections
      attackPayload.header_interval_s = slowInterval
      attackPayload.server_max_connections = 150
    } else if (selectedAttack === 'arp_spoof') {
      targetDuration = arpDuration
      attackPayload.target_ip = arpVictimIp
      attackPayload.gateway_ip = arpGatewayIp
      attackPayload.attacker_ip = arpAttackerIp
      attackPayload.duration_s = arpDuration
      attackPayload.dai_enabled = arpDaiEnabled
    }

    let resultData: AttackSimResult | null = null

    try {
      // Try calling live backend endpoint
      const res = await api.post<{ ok?: boolean; result?: AttackSimResult } & AttackSimResult>('/api/security/attack/simulate', attackPayload)
      const data = res?.result || res
      if (data && data.attack_type) {
        resultData = data
      }
    } catch {
      // Fallback synthetic model
    }

    if (!resultData) {
      // High-fidelity local simulation model
      if (selectedAttack === 'syn_flood') {
        const total = synPps * synDuration
        const backlog = 1024
        const drops = synCookies ? 0 : Math.max(0, total - backlog)
        const cpu = synCookies ? Math.min(65, 24 + synPps / 1200) : Math.min(99, 55 + synPps / 300)
        const avail = synCookies ? 98.5 : 0.0
        const alertItem: SecurityAlertItem = {
          id: `alert_${Date.now()}`,
          timestamp: Date.now(),
          iso_time: new Date().toISOString(),
          severity: synCookies ? 'medium' : 'critical',
          attack_type: 'syn_flood',
          source: '10.0.0.99 (Botnet)',
          target: `${synTargetIp}:${synTargetPort}`,
          description: synCookies
            ? `SYN Flood hujumi aniqlandi (${synPps} PPS). SYN Cookies faol bo'lganligi sababli xizmat to'xtamadi.`
            : `Kritik TCP SYN Flood: ${synPps} PPS. Backlog to'lib, ${drops.toLocaleString()} paket tashlandi (100% DoS).`,
          mitigation: synCookies
            ? "SYN Cookies faol. Perimetrda iptables SYN rate limiting qo'llash tavsiya etiladi."
            : "Zudlik bilan 'sysctl -w net.ipv4.tcp_syncookies=1' ni yoqing va backlog sig'imini oshiring.",
          details: { pps: synPps, dropped_syns: drops, cpu_pct: cpu },
        }
        resultData = {
          attack_type: 'syn_flood',
          duration_s: synDuration,
          cpu_impact_pct: cpu,
          server_availability_pct: avail,
          total_packets: total,
          dropped_syns: drops,
          backlog_exhausted: !synCookies,
          syn_cookies_enabled: synCookies,
          alert_generated: alertItem,
          time_series: Array.from({ length: 11 }, (_, i) => ({
            time_s: round(i * (synDuration / 10), 1),
            cpu_utilization_pct: synCookies ? Math.min(cpu, 15 + i * 4.5) : Math.min(99, 20 + i * 8.5),
            backlog_used: synCookies ? 120 : Math.min(1024, i * 200),
            dropped_syns: synCookies ? 0 : Math.max(0, i * (drops / 10)),
            incoming_pps: synPps,
          })),
        }
      } else if (selectedAttack === 'udp_reflection') {
        const ratio = udpReflectorType === 'ntp' ? 200 : 54
        const reqMbps = (udpRequestPps * (udpReflectorType === 'ntp' ? 48 : 64) * 8) / 1_000_000
        const inboundMbps = reqMbps * ratio
        const pipeCap = 100.0
        const sat = inboundMbps >= pipeCap
        const drops = sat ? inboundMbps - pipeCap : 0
        const loss = sat ? (drops / inboundMbps) * 100 : 0
        const alertItem: SecurityAlertItem = {
          id: `alert_${Date.now()}`,
          timestamp: Date.now(),
          iso_time: new Date().toISOString(),
          severity: sat ? 'critical' : 'high',
          attack_type: 'udp_reflection',
          source: `Ochiq ${udpReflectorType.toUpperCase()} Resolverlar (${ratio}x amplifikatsiya)`,
          target: udpVictimIp,
          description: `UDP ${udpReflectorType.toUpperCase()} Reflection: ${inboundMbps.toFixed(1)} Mbps trafik yo'naltirildi. Kanal sig'imi: ${pipeCap} Mbps (${loss.toFixed(1)}% yo'qotish).`,
          mitigation: 'Provayder darajasida BCP 38 / uRPF ni talab qiling va UDP Response Rate Limiting (RRL) o‘rnating.',
        }
        resultData = {
          attack_type: 'udp_reflection',
          duration_s: udpDuration,
          inbound_reflected_mbps: round(inboundMbps, 1),
          pipe_capacity_mbps: pipeCap,
          pipe_saturated: sat,
          dropped_traffic_mbps: round(drops, 1),
          victim_packet_loss_pct: round(loss, 1),
          alert_generated: alertItem,
          time_series: Array.from({ length: 11 }, (_, i) => ({
            time_s: round(i * (udpDuration / 10), 1),
            inbound_mbps: round(inboundMbps * (0.95 + 0.1 * Math.sin(i)), 1),
            dropped_mbps: round(drops, 1),
            packet_loss_pct: round(loss, 1),
            cpu_utilization_pct: Math.min(88, 25 + (inboundMbps / pipeCap) * 50),
          })),
        }
      } else if (selectedAttack === 'slowloris') {
        const maxConn = 150
        const exhausted = slowConnections >= maxConn
        const util = Math.min(100, (slowConnections / maxConn) * 100)
        const delay = exhausted ? 30000 : 25 + slowConnections * 7.5
        const avail = exhausted ? 0 : Math.max(0, 100 - util)
        const alertItem: SecurityAlertItem = {
          id: `alert_${Date.now()}`,
          timestamp: Date.now(),
          iso_time: new Date().toISOString(),
          severity: exhausted ? 'critical' : 'high',
          attack_type: 'slowloris',
          source: 'Slowloris HTTP Client Pool',
          target: `${slowTargetIp}:${slowTargetPort}`,
          description: `Slowloris DoS: ${slowConnections} ta chala ulanish orqali worker pool ${util.toFixed(1)}% ga band qilindi. Kechikish: ${delay.toFixed(0)} ms.`,
          mitigation: 'Veb-serverda mod_reqtimeout (RequestReadTimeout header=10-20) sozlang yoki NGINX teskari proksi o‘rnating.',
        }
        resultData = {
          attack_type: 'slowloris',
          duration_s: 10,
          connections: slowConnections,
          server_max_connections: maxConn,
          pool_exhausted: exhausted,
          pool_utilization_pct: round(util, 1),
          response_delay_ms: round(delay, 1),
          server_availability_pct: round(avail, 1),
          alert_generated: alertItem,
          time_series: Array.from({ length: 11 }, (_, i) => {
            const curC = Math.min(slowConnections, Math.round(i * (slowConnections / 7)))
            const curU = Math.min(100, (curC / maxConn) * 100)
            return {
              time_s: i,
              active_slow_connections: curC,
              pool_utilization_pct: round(curU, 1),
              response_delay_ms: curC >= maxConn ? 30000 : 25 + curC * 7.5,
              server_availability_pct: curC >= maxConn ? 0 : 100 - curU,
              cpu_utilization_pct: round(20 + curU * 0.4, 1),
            }
          }),
        }
      } else {
        // arp_spoof
        const intercepted = arpDaiEnabled ? 0 : Math.round(arpDuration * 42)
        const alertItem: SecurityAlertItem = {
          id: `alert_${Date.now()}`,
          timestamp: Date.now(),
          iso_time: new Date().toISOString(),
          severity: arpDaiEnabled ? 'medium' : 'critical',
          attack_type: 'arp_spoof',
          source: `${arpAttackerIp} (00:11:22:33:44:aa)`,
          target: `${arpVictimIp} <-> ${arpGatewayIp}`,
          description: arpDaiEnabled
            ? "ARP Spoofing urinishi Dynamic ARP Inspection (DAI) tomonidan to'xtatildi. Soxta paketlar bloklandi."
            : `Kritik ARP Cache Poisoning: Barcha L3 trafik (${intercepted} paket) hujumchi orqali o'tmoqda (MitM).`,
          mitigation: arpDaiEnabled
            ? "DAI muvaffaqiyatli ishladi. Hujumchi portini 802.1X port-security orqali karantinga oling."
            : "L2 kommutatorlarda Dynamic ARP Inspection (DAI) va DHCP Snooping funksiyalarini yoqing.",
        }
        resultData = {
          attack_type: 'arp_spoof',
          duration_s: arpDuration,
          mitm_active: !arpDaiEnabled,
          intercepted_packets: intercepted,
          dai_enabled: arpDaiEnabled,
          dai_message: arpDaiEnabled ? 'DAI faol: Soxta ARP xabarlari bloklandi' : 'DAI o‘chiq: ARP kesh zaharlandi',
          alert_generated: alertItem,
          time_series: Array.from({ length: 11 }, (_, i) => ({
            time_s: round(i * (arpDuration / 10), 1),
            intercepted_packets_cumulative: arpDaiEnabled ? 0 : Math.round(i * (intercepted / 10)),
            dai_blocked_packets: arpDaiEnabled ? i * 3 : 0,
            cpu_utilization_pct: 18 + (arpDaiEnabled ? 4 : 22),
          })),
        }
      }
    }

    setCurrentAttackResult(resultData)

    // Add alert to active list if present
    if (resultData?.alert_generated) {
      setAlertsList((prev) => [resultData!.alert_generated!, ...prev])
    }

    // Step-by-step dynamic animation loop over targetDuration
    const ts = resultData?.time_series || []
    const totalSteps = ts.length || 10
    let step = 0
    const intervalMs = Math.max(200, (targetDuration * 1000) / totalSteps)

    attackTimerRef.current = window.setInterval(() => {
      step += 1
      const pct = Math.min(100, Math.round((step / totalSteps) * 100))
      setAttackProgress(pct)

      const pt = ts[Math.min(step, ts.length - 1)]
      if (pt) {
        setLiveCpu(pt.cpu_utilization_pct ?? (45 + Math.random() * 20))
        setLiveQueue(pt.backlog_used ? Math.min(100, Math.round((pt.backlog_used / 1024) * 100)) : pt.pool_utilization_pct ?? 15)
        setLiveDrops(pt.dropped_syns ?? (pt.dropped_mbps ? Math.round(pt.dropped_mbps * 120) : 0))
        setLiveBandwidth(pt.inbound_mbps ?? 2.4)
        setLiveChartPoints((prev) => [
          ...prev,
          {
            t: pt.time_s,
            cpu: pt.cpu_utilization_pct ?? 45,
            queue: pt.pool_utilization_pct ?? (pt.backlog_used ? Math.round((pt.backlog_used / 1024) * 100) : 20),
            drops: pt.dropped_syns ?? (pt.dropped_mbps ? Math.round(pt.dropped_mbps * 10) : 0),
          },
        ])
      }

      if (step >= totalSteps) {
        stopAttack()
        toast("Hujum simulyatsiyasi yakunlandi. Natijalar va IDS ogohlantirishlari yangilandi.", 'ok')
      }
    }, intervalMs)
  }

  useEffect(() => {
    return () => {
      if (attackTimerRef.current) clearInterval(attackTimerRef.current)
    }
  }, [])

  // ──────────────────────── Filtered Data Computations ────────────────────────

  const filteredConntrack = useMemo(() => {
    let list = conntrackList
    if (protoFilter !== 'all') {
      list = list.filter((c) => c.proto.toLowerCase() === protoFilter)
    }
    if (stateFilter !== 'all') {
      list = list.filter((c) => (c.tcp_state || c.fw_state || '').toUpperCase() === stateFilter.toUpperCase())
    }
    if (natOnly) {
      list = list.filter((c) => c.nat && c.nat.enabled !== false)
    }
    if (searchConntrack.trim()) {
      const q = searchConntrack.toLowerCase().trim()
      list = list.filter((c) => {
        const text = `${c.proto} ${c.tcp_state || ''} ${c.fw_state || ''} ${c.orig.src_ip}:${c.orig.src_port} ${c.orig.dst_ip}:${c.orig.dst_port} ${c.reply.src_ip}:${c.reply.src_port} ${c.reply.dst_ip}:${c.reply.dst_port} ${c.nat?.nat_type || ''}`.toLowerCase()
        return text.includes(q)
      })
    }
    return list
  }, [conntrackList, protoFilter, stateFilter, natOnly, searchConntrack])

  const conntrackStats: ConntrackStats = useMemo(() => {
    let tcp = 0
    let udp = 0
    let icmp = 0
    let est = 0
    let nat = 0
    for (const c of conntrackList) {
      const p = c.proto.toLowerCase()
      if (p === 'tcp') tcp += 1
      else if (p === 'udp') udp += 1
      else if (p === 'icmp') icmp += 1

      if (c.tcp_state === 'ESTABLISHED' || c.fw_state === 'ESTABLISHED') est += 1
      if (c.nat && c.nat.enabled !== false) nat += 1
    }
    return {
      total: conntrackList.length,
      tcp,
      udp,
      icmp,
      established: est,
      nat_count: nat,
      max_entries: 65536,
    }
  }, [conntrackList])

  const filteredAlerts = useMemo(() => {
    let list = alertsList
    if (severityFilter !== 'all') {
      list = list.filter((a) => a.severity.toLowerCase() === severityFilter)
    }
    if (searchAlerts.trim()) {
      const q = searchAlerts.toLowerCase().trim()
      list = list.filter((a) => {
        const text = `${a.severity} ${a.attack_type} ${a.source} ${a.target} ${a.description} ${a.mitigation}`.toLowerCase()
        return text.includes(q)
      })
    }
    return list
  }, [alertsList, severityFilter, searchAlerts])

  const alertStats = useMemo(() => {
    const res = { total: alertsList.length, critical: 0, high: 0, medium: 0, low: 0 }
    for (const a of alertsList) {
      const s = a.severity.toLowerCase()
      if (s === 'critical') res.critical += 1
      else if (s === 'high') res.high += 1
      else if (s === 'medium') res.medium += 1
      else if (s === 'low') res.low += 1
    }
    return res
  }, [alertsList])

  // ──────────────────────── Helper Renderers ────────────────────────

  const renderTcpStateBadge = (state?: string | null, fwState?: string) => {
    const s = (state || fwState || 'UNKNOWN').toUpperCase()
    if (s === 'ESTABLISHED') {
      return <Badge variant="success" className="font-mono text-[10.5px]">ESTABLISHED</Badge>
    }
    if (s === 'SYN_SENT' || s === 'SYN_RECV') {
      return <Badge variant="warning" className="font-mono text-[10.5px]">{s}</Badge>
    }
    if (s === 'TIME_WAIT' || s === 'CLOSE_WAIT') {
      return <Badge variant="outline" className="border-slate-500/30 text-slate-400 font-mono text-[10.5px]">{s}</Badge>
    }
    if (s === 'FIN_WAIT') {
      return <Badge variant="secondary" className="font-mono text-[10.5px]">FIN_WAIT</Badge>
    }
    if (s === 'NEW') {
      return <Badge variant="outline" className="border-purple-500/40 text-purple-400 font-mono text-[10.5px]">NEW</Badge>
    }
    if (s === 'INVALID') {
      return <Badge variant="destructive" className="font-mono text-[10.5px]">INVALID</Badge>
    }
    return <Badge variant="outline" className="font-mono text-[10.5px]">{s}</Badge>
  }

  const renderTtlCountdown = (ttl: number) => {
    const isShort = ttl < 15
    const isMedium = ttl < 60
    return (
      <div className="flex items-center gap-1.5 font-mono text-xs">
        <Clock className={cn('h-3.3 w-3.3', isShort ? 'text-destructive animate-pulse' : isMedium ? 'text-warning' : 'text-muted-foreground')} />
        <span className={cn(isShort ? 'font-semibold text-destructive' : isMedium ? 'text-warning' : 'text-foreground')}>
          {ttl >= 3600 ? `${Math.floor(ttl / 3600)}h ${Math.floor((ttl % 3600) / 60)}m` : `${ttl}s`}
        </span>
      </div>
    )
  }

  const exportAlertsJson = () => {
    const a = document.createElement('a')
    a.href = URL.createObjectURL(new Blob([JSON.stringify(alertsList, null, 2)], { type: 'application/json' }))
    a.download = `security_alerts_${Date.now()}.json`
    a.click()
    setTimeout(() => URL.revokeObjectURL(a.href), 2000)
    toast('Xavfsizlik ogohlantirishlari JSON formatida yuklandi', 'ok')
  }

  // ──────────────────────── Render Tabs ────────────────────────

  const bodyContent = (
    <div className="flex flex-col h-full overflow-hidden">
        {/* Navigation Tabs Header */}
        <div className="flex items-center justify-between border-b px-3 py-1.5 bg-muted/20">
          <Segmented
            value={activeTab}
            onChange={(v) => setActiveTab(v as any)}
            items={[
              { value: 'conntrack', label: <><Layers className="h-3.5 w-3.5" /> Conntrack Table ({conntrackList.length})</> },
              { value: 'attacks', label: <><Flame className="h-3.5 w-3.5 text-warning" /> Attack Simulator</> },
              {
                value: 'alerts',
                label: (
                  <span className="inline-flex items-center gap-1.5">
                    <ShieldAlert className="h-3.5 w-3.5 text-destructive" />
                    Security Alerts
                    {alertStats.critical > 0 && (
                      <span className="rounded-full bg-destructive px-1.5 py-0.2 text-[10px] font-bold text-destructive-foreground">
                        {alertStats.critical}
                      </span>
                    )}
                  </span>
                ),
              },
            ]}
          />
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1">
              <span className="h-2 w-2 rounded-full bg-success animate-pulse" /> Stateful Engine Faol
            </span>
          </div>
        </div>

        {/* Tab 1: Conntrack Table */}
        {activeTab === 'conntrack' && (
          <div className="flex flex-1 flex-col overflow-auto p-3 gap-3">
            {/* KPI Stat Cards */}
            <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
              <div className="rounded-lg border bg-card p-2.5">
                <div className="flex items-center justify-between text-[11px] text-muted-foreground uppercase font-medium">
                  Jami Sessiyalar <Layers className="h-3.5 w-3.5 text-primary" />
                </div>
                <div className="mt-1 font-mono text-xl font-bold tracking-tight">{conntrackStats.total}</div>
                <div className="text-[10px] text-muted-foreground mt-0.5">Maks. {conntrackStats.max_entries}</div>
              </div>
              <div className="rounded-lg border bg-card p-2.5">
                <div className="flex items-center justify-between text-[11px] text-muted-foreground uppercase font-medium">
                  TCP Ulanishlar <Server className="h-3.5 w-3.5 text-emerald-500" />
                </div>
                <div className="mt-1 font-mono text-xl font-bold tracking-tight text-emerald-500">{conntrackStats.tcp}</div>
                <div className="text-[10px] text-muted-foreground mt-0.5">{conntrackStats.established} ESTABLISHED</div>
              </div>
              <div className="rounded-lg border bg-card p-2.5">
                <div className="flex items-center justify-between text-[11px] text-muted-foreground uppercase font-medium">
                  UDP Oqimlar <Radio className="h-3.5 w-3.5 text-sky-500" />
                </div>
                <div className="mt-1 font-mono text-xl font-bold tracking-tight text-sky-500">{conntrackStats.udp}</div>
                <div className="text-[10px] text-muted-foreground mt-0.5">DNS / NTP oqimlari</div>
              </div>
              <div className="rounded-lg border bg-card p-2.5">
                <div className="flex items-center justify-between text-[11px] text-muted-foreground uppercase font-medium">
                  ICMP Sessiyalar <Activity className="h-3.5 w-3.5 text-amber-500" />
                </div>
                <div className="mt-1 font-mono text-xl font-bold tracking-tight text-amber-500">{conntrackStats.icmp}</div>
                <div className="text-[10px] text-muted-foreground mt-0.5">Echo request / reply</div>
              </div>
              <div className="rounded-lg border bg-card p-2.5">
                <div className="flex items-center justify-between text-[11px] text-muted-foreground uppercase font-medium">
                  NAT Sessiyalar <ArrowDownUp className="h-3.5 w-3.5 text-purple-500" />
                </div>
                <div className="mt-1 font-mono text-xl font-bold tracking-tight text-purple-500">{conntrackStats.nat_count}</div>
                <div className="text-[10px] text-muted-foreground mt-0.5">SNAT / DNAT faol</div>
              </div>
            </div>

            {/* Filter & Search Toolbar */}
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border bg-card/50 p-2">
              <div className="flex flex-wrap items-center gap-2">
                <div className="relative w-56">
                  <Search className="absolute left-2.5 top-2 h-3.5 w-3.5 text-muted-foreground" />
                  <Input
                    placeholder="Qidiruv: IP, port yoki holat..."
                    value={searchConntrack}
                    onChange={(e) => setSearchConntrack(e.target.value)}
                    className="h-7 pl-8 text-xs"
                  />
                </div>
                <div className="flex items-center gap-1 border-l pl-2">
                  <span className="text-[11px] text-muted-foreground mr-1">Proto:</span>
                  {(['all', 'tcp', 'udp', 'icmp'] as const).map((p) => (
                    <button
                      key={p}
                      onClick={() => setProtoFilter(p)}
                      className={cn(
                        'rounded px-2 py-0.5 text-xs font-medium uppercase transition-colors',
                        protoFilter === p ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-accent'
                      )}
                    >
                      {p === 'all' ? 'Barchasi' : p}
                    </button>
                  ))}
                </div>
                <div className="flex items-center gap-1 border-l pl-2">
                  <span className="text-[11px] text-muted-foreground mr-1">Holat:</span>
                  <select
                    value={stateFilter}
                    onChange={(e) => setStateFilter(e.target.value)}
                    className="h-7 rounded border bg-background px-2 text-xs"
                  >
                    <option value="all">Barcha holatlar</option>
                    <option value="ESTABLISHED">ESTABLISHED</option>
                    <option value="SYN_SENT">SYN_SENT</option>
                    <option value="TIME_WAIT">TIME_WAIT</option>
                    <option value="FIN_WAIT">FIN_WAIT</option>
                    <option value="NEW">NEW</option>
                    <option value="INVALID">INVALID</option>
                  </select>
                </div>
                <button
                  onClick={() => setNatOnly(!natOnly)}
                  className={cn(
                    'flex items-center gap-1 rounded border px-2 py-1 text-xs transition-colors',
                    natOnly ? 'border-primary bg-primary/10 text-primary font-medium' : 'text-muted-foreground hover:bg-accent'
                  )}
                >
                  <ArrowDownUp className="h-3 w-3" /> Faqat NAT
                </button>
              </div>

              <div className="flex items-center gap-2">
                <label className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={autoRefreshCt}
                    onChange={(e) => setAutoRefreshCt(e.target.checked)}
                    className="rounded border"
                  />
                  Avto-yangilash
                </label>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={loadingCt}
                  onClick={() => fetchConntrack(false)}
                  className="h-7 text-xs"
                >
                  <RefreshCw className={cn('h-3.5 w-3.5 mr-1', loadingCt && 'animate-spin')} />
                  Yangilash
                </Button>
              </div>
            </div>

            {/* Sessions Table */}
            <div className="flex-1 rounded-lg border bg-card overflow-hidden flex flex-col">
              <DataTable
                columns={[
                  'Protokol',
                  'TCP Holati',
                  'Asl Oqim (Original)',
                  'Javob Oqimi (Reply)',
                  'NAT Status',
                  'TTL / Timeout',
                  'Paketlar / Baytlar',
                ]}
                rows={filteredConntrack.map((item) => {
                  const hasNat = item.nat && item.nat.enabled !== false
                  return [
                    <Badge
                      key="proto"
                      variant="outline"
                      className={cn(
                        'uppercase font-mono text-[10px]',
                        item.proto === 'tcp' && 'border-emerald-500/40 text-emerald-400',
                        item.proto === 'udp' && 'border-sky-500/40 text-sky-400',
                        item.proto === 'icmp' && 'border-amber-500/40 text-amber-400'
                      )}
                    >
                      {item.proto}
                    </Badge>,
                    <span key="state">{renderTcpStateBadge(item.tcp_state, item.fw_state)}</span>,
                    <span key="orig" className="font-mono text-[11px] text-foreground">
                      <b className="font-semibold">{item.orig.src_ip}</b>:{item.orig.src_port}{' '}
                      <span className="text-muted-foreground font-sans">➜</span>{' '}
                      <b>{item.orig.dst_ip}</b>:{item.orig.dst_port}
                    </span>,
                    <span key="reply" className="font-mono text-[11px] text-muted-foreground">
                      {item.reply.src_ip}:{item.reply.src_port}{' '}
                      <span className="text-muted-foreground font-sans">➜</span>{' '}
                      {item.reply.dst_ip}:{item.reply.dst_port}
                    </span>,
                    <span key="nat">
                      {hasNat ? (
                        <div className="flex items-center gap-1 font-mono text-[10.5px]">
                          <Badge variant="outline" className="border-purple-500/40 text-purple-400 uppercase text-[9.5px]">
                            {item.nat?.nat_type || 'NAT'}
                          </Badge>
                          <span className="text-muted-foreground">
                            {item.nat?.translated_src || item.nat?.translated_dst}:
                            {item.nat?.translated_sport || item.nat?.translated_dport}
                          </span>
                        </div>
                      ) : (
                        <span className="text-[11px] text-muted-foreground">—</span>
                      )}
                    </span>,
                    <span key="ttl">{renderTtlCountdown(item.remaining_ttl)}</span>,
                    <span key="bytes" className="font-mono text-[11px] text-muted-foreground">
                      {(item.total_packets ?? 0).toLocaleString()} pkts · {fmtBps((item.total_bytes ?? 0) * 8)}
                    </span>,
                  ]
                })}
                empty="Mos keluvchi conntrack sessiyalari topilmadi"
              />
            </div>
          </div>
        )}

        {/* Tab 2: Attack Simulator */}
        {activeTab === 'attacks' && (
          <div className="flex flex-1 flex-col overflow-auto p-3 gap-3">
            {/* 4 Attack Selector Cards */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2.5">
              {/* 1. SYN Flood */}
              <div
                onClick={() => setSelectedAttack('syn_flood')}
                className={cn(
                  'cursor-pointer rounded-xl border p-3 transition-all',
                  selectedAttack === 'syn_flood'
                    ? 'border-primary bg-primary/10 shadow-sm ring-1 ring-primary'
                    : 'border-border bg-card/50 hover:border-foreground/30 hover:bg-accent/40'
                )}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <div className="grid h-7 w-7 place-items-center rounded-lg bg-rose-500/20 text-rose-500">
                      <Flame className="h-4 w-4" />
                    </div>
                    <span className="font-semibold text-xs">TCP SYN Flood</span>
                  </div>
                  <Badge variant="outline" className="text-[9.5px] border-rose-500/40 text-rose-500">L4 DoS</Badge>
                </div>
                <p className="mt-2 text-[11px] text-muted-foreground leading-relaxed">
                  Yarim-ochiq ulanishlar orqali kernel SYN backlog navbatini to'ldirish va yangi ulanishlarni bloklash.
                </p>
              </div>

              {/* 2. UDP Reflection */}
              <div
                onClick={() => setSelectedAttack('udp_reflection')}
                className={cn(
                  'cursor-pointer rounded-xl border p-3 transition-all',
                  selectedAttack === 'udp_reflection'
                    ? 'border-primary bg-primary/10 shadow-sm ring-1 ring-primary'
                    : 'border-border bg-card/50 hover:border-foreground/30 hover:bg-accent/40'
                )}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <div className="grid h-7 w-7 place-items-center rounded-lg bg-sky-500/20 text-sky-500">
                      <Radio className="h-4 w-4" />
                    </div>
                    <span className="font-semibold text-xs">UDP Amplification</span>
                  </div>
                  <Badge variant="outline" className="text-[9.5px] border-sky-500/40 text-sky-500">50x-70x</Badge>
                </div>
                <p className="mt-2 text-[11px] text-muted-foreground leading-relaxed">
                  Ochiq DNS/NTP resolverlar orqali soxtalashtirilgan so'rovlar yordamida nishon kanalini to'ldirish.
                </p>
              </div>

              {/* 3. Slowloris */}
              <div
                onClick={() => setSelectedAttack('slowloris')}
                className={cn(
                  'cursor-pointer rounded-xl border p-3 transition-all',
                  selectedAttack === 'slowloris'
                    ? 'border-primary bg-primary/10 shadow-sm ring-1 ring-primary'
                    : 'border-border bg-card/50 hover:border-foreground/30 hover:bg-accent/40'
                )}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <div className="grid h-7 w-7 place-items-center rounded-lg bg-amber-500/20 text-amber-500">
                      <Clock className="h-4 w-4" />
                    </div>
                    <span className="font-semibold text-xs">Slowloris HTTP</span>
                  </div>
                  <Badge variant="outline" className="text-[9.5px] border-amber-500/40 text-amber-500">L7 Pool</Badge>
                </div>
                <p className="mt-2 text-[11px] text-muted-foreground leading-relaxed">
                  Tugallanmagan HTTP sarlavhalarini sekin uzatib, veb-serverning barcha worker oqimlarini band qilish.
                </p>
              </div>

              {/* 4. ARP Spoofing */}
              <div
                onClick={() => setSelectedAttack('arp_spoof')}
                className={cn(
                  'cursor-pointer rounded-xl border p-3 transition-all',
                  selectedAttack === 'arp_spoof'
                    ? 'border-primary bg-primary/10 shadow-sm ring-1 ring-primary'
                    : 'border-border bg-card/50 hover:border-foreground/30 hover:bg-accent/40'
                )}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <div className="grid h-7 w-7 place-items-center rounded-lg bg-purple-500/20 text-purple-500">
                      <ArrowDownUp className="h-4 w-4" />
                    </div>
                    <span className="font-semibold text-xs">ARP Spoof (MitM)</span>
                  </div>
                  <Badge variant="outline" className="text-[9.5px] border-purple-500/40 text-purple-500">L2 Hijack</Badge>
                </div>
                <p className="mt-2 text-[11px] text-muted-foreground leading-relaxed">
                  Soxta ARP javoblari orqali shlyuz va qurbon orasidagi trafikni ushlash (Man-in-the-Middle).
                </p>
              </div>
            </div>

            {/* Attack Parameters & Live Execution Panel */}
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-3">
              {/* Left Column: Configuration Controls */}
              <div className="lg:col-span-5 rounded-xl border bg-card p-3 flex flex-col justify-between">
                <div>
                  <div className="flex items-center justify-between border-b pb-2 mb-3">
                    <span className="text-xs font-semibold flex items-center gap-1.5">
                      <Sliders className="h-3.5 w-3.5 text-primary" /> Hujum Parametrlari
                    </span>
                    <Badge variant="outline" className="text-[10px] uppercase font-mono">
                      {selectedAttack}
                    </Badge>
                  </div>

                  {/* SYN Flood Form */}
                  {selectedAttack === 'syn_flood' && (
                    <div className="flex flex-col gap-3">
                      <div className="grid grid-cols-2 gap-2">
                        <div>
                          <Label className="text-[11px]">Nishon IP</Label>
                          <Input
                            value={synTargetIp}
                            onChange={(e) => setSynTargetIp(e.target.value)}
                            className="h-8 font-mono text-xs mt-1"
                          />
                        </div>
                        <div>
                          <Label className="text-[11px]">Nishon Port</Label>
                          <Input
                            type="number"
                            value={synTargetPort}
                            onChange={(e) => setSynTargetPort(Number(e.target.value))}
                            className="h-8 font-mono text-xs mt-1"
                          />
                        </div>
                      </div>

                      <div>
                        <div className="flex justify-between text-[11px]">
                          <Label>Paket tezligi (PPS)</Label>
                          <span className="font-mono font-semibold text-primary">{synPps.toLocaleString()} PPS</span>
                        </div>
                        <input
                          type="range"
                          min="500"
                          max="50000"
                          step="500"
                          value={synPps}
                          onChange={(e) => setSynPps(Number(e.target.value))}
                          className="w-full mt-1.5 cursor-pointer"
                        />
                      </div>

                      <div>
                        <div className="flex justify-between text-[11px]">
                          <Label>Davomiyligi (sekund)</Label>
                          <span className="font-mono font-semibold">{synDuration} s</span>
                        </div>
                        <input
                          type="range"
                          min="3"
                          max="60"
                          value={synDuration}
                          onChange={(e) => setSynDuration(Number(e.target.value))}
                          className="w-full mt-1.5 cursor-pointer"
                        />
                      </div>

                      <div className="rounded-lg border bg-accent/30 p-2.5 flex items-center justify-between">
                        <div>
                          <div className="text-xs font-semibold flex items-center gap-1.5">
                            <ShieldCheck className={cn('h-4 w-4', synCookies ? 'text-emerald-500' : 'text-muted-foreground')} />
                            SYN Cookies Himoyasi
                          </div>
                          <div className="text-[10.5px] text-muted-foreground mt-0.5">
                            net.ipv4.tcp_syncookies = {synCookies ? '1 (yoqilgan)' : '0 (o‘chiq)'}
                          </div>
                        </div>
                        <button
                          type="button"
                          onClick={() => setSynCookies(!synCookies)}
                          className={cn(
                            'relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out',
                            synCookies ? 'bg-primary' : 'bg-muted'
                          )}
                        >
                          <span
                            className={cn(
                              'pointer-events-none inline-block h-5 w-5 rounded-full bg-background shadow-lg ring-0 transition duration-200 ease-in-out',
                              synCookies ? 'translate-x-5' : 'translate-x-0'
                            )}
                          />
                        </button>
                      </div>
                    </div>
                  )}

                  {/* UDP Reflection Form */}
                  {selectedAttack === 'udp_reflection' && (
                    <div className="flex flex-col gap-3">
                      <div>
                        <Label className="text-[11px]">Qurbon (Victim) IP</Label>
                        <Input
                          value={udpVictimIp}
                          onChange={(e) => setUdpVictimIp(e.target.value)}
                          className="h-8 font-mono text-xs mt-1"
                        />
                      </div>

                      <div>
                        <Label className="text-[11px]">Kuchaytiruvchi Protokol (Reflector)</Label>
                        <div className="grid grid-cols-2 gap-2 mt-1">
                          <button
                            type="button"
                            onClick={() => setUdpReflectorType('dns')}
                            className={cn(
                              'rounded-md border p-2 text-left text-xs transition-colors',
                              udpReflectorType === 'dns'
                                ? 'border-primary bg-primary/10 font-semibold text-primary'
                                : 'border-border text-muted-foreground hover:bg-accent'
                            )}
                          >
                            <div>DNS (Port 53)</div>
                            <div className="text-[10px] text-muted-foreground font-normal">ANY query ~54x</div>
                          </button>
                          <button
                            type="button"
                            onClick={() => setUdpReflectorType('ntp')}
                            className={cn(
                              'rounded-md border p-2 text-left text-xs transition-colors',
                              udpReflectorType === 'ntp'
                                ? 'border-primary bg-primary/10 font-semibold text-primary'
                                : 'border-border text-muted-foreground hover:bg-accent'
                            )}
                          >
                            <div>NTP (Port 123)</div>
                            <div className="text-[10px] text-muted-foreground font-normal">monlist ~200x</div>
                          </button>
                        </div>
                      </div>

                      <div>
                        <div className="flex justify-between text-[11px]">
                          <Label>So'rovlar tezligi (Request PPS)</Label>
                          <span className="font-mono font-semibold text-sky-500">{udpRequestPps.toLocaleString()} PPS</span>
                        </div>
                        <input
                          type="range"
                          min="100"
                          max="10000"
                          step="100"
                          value={udpRequestPps}
                          onChange={(e) => setUdpRequestPps(Number(e.target.value))}
                          className="w-full mt-1.5 cursor-pointer"
                        />
                      </div>

                      <div>
                        <div className="flex justify-between text-[11px]">
                          <Label>Davomiyligi (sekund)</Label>
                          <span className="font-mono font-semibold">{udpDuration} s</span>
                        </div>
                        <input
                          type="range"
                          min="3"
                          max="30"
                          value={udpDuration}
                          onChange={(e) => setUdpDuration(Number(e.target.value))}
                          className="w-full mt-1.5 cursor-pointer"
                        />
                      </div>

                      <div className="rounded-lg border bg-card/60 p-2 text-[11px] text-muted-foreground">
                        Kutilayotgan kuchaytirilgan oqim:{' '}
                        <b className="font-mono text-foreground">
                          {((udpRequestPps * (udpReflectorType === 'ntp' ? 48 : 64) * 8 * (udpReflectorType === 'ntp' ? 200 : 54)) / 1_000_000).toFixed(1)} Mbps
                        </b>{' '}
                        (100 Mbps qurbon kanalining {(((udpRequestPps * (udpReflectorType === 'ntp' ? 48 : 64) * 8 * (udpReflectorType === 'ntp' ? 200 : 54)) / 1_000_000 / 100) * 100).toFixed(0)}% qismi)
                      </div>
                    </div>
                  )}

                  {/* Slowloris Form */}
                  {selectedAttack === 'slowloris' && (
                    <div className="flex flex-col gap-3">
                      <div className="grid grid-cols-2 gap-2">
                        <div>
                          <Label className="text-[11px]">Veb-server IP</Label>
                          <Input
                            value={slowTargetIp}
                            onChange={(e) => setSlowTargetIp(e.target.value)}
                            className="h-8 font-mono text-xs mt-1"
                          />
                        </div>
                        <div>
                          <Label className="text-[11px]">HTTP Port</Label>
                          <Input
                            type="number"
                            value={slowTargetPort}
                            onChange={(e) => setSlowTargetPort(Number(e.target.value))}
                            className="h-8 font-mono text-xs mt-1"
                          />
                        </div>
                      </div>

                      <div>
                        <div className="flex justify-between text-[11px]">
                          <Label>Sekin ulanishlar soni (Connections)</Label>
                          <span className="font-mono font-semibold text-amber-500">{slowConnections} oqim</span>
                        </div>
                        <input
                          type="range"
                          min="20"
                          max="1000"
                          step="10"
                          value={slowConnections}
                          onChange={(e) => setSlowConnections(Number(e.target.value))}
                          className="w-full mt-1.5 cursor-pointer"
                        />
                        <div className="text-[10px] text-muted-foreground mt-0.5">
                          Server Worker Pool sig'imi: 150 connection slots
                        </div>
                      </div>

                      <div>
                        <div className="flex justify-between text-[11px]">
                          <Label>Sarlavha uzatish oralig'i (Keep-alive)</Label>
                          <span className="font-mono font-semibold">{slowInterval} s</span>
                        </div>
                        <input
                          type="range"
                          min="2"
                          max="15"
                          value={slowInterval}
                          onChange={(e) => setSlowInterval(Number(e.target.value))}
                          className="w-full mt-1.5 cursor-pointer"
                        />
                      </div>
                    </div>
                  )}

                  {/* ARP Spoof Form */}
                  {selectedAttack === 'arp_spoof' && (
                    <div className="flex flex-col gap-3">
                      <div className="grid grid-cols-2 gap-2">
                        <div>
                          <Label className="text-[11px]">Jabrlanuvchi IP</Label>
                          <Input
                            value={arpVictimIp}
                            onChange={(e) => setArpVictimIp(e.target.value)}
                            className="h-8 font-mono text-xs mt-1"
                          />
                        </div>
                        <div>
                          <Label className="text-[11px]">Shlyuz (Gateway) IP</Label>
                          <Input
                            value={arpGatewayIp}
                            onChange={(e) => setArpGatewayIp(e.target.value)}
                            className="h-8 font-mono text-xs mt-1"
                          />
                        </div>
                      </div>

                      <div>
                        <Label className="text-[11px]">Hujumchi (Attacker) IP</Label>
                        <Input
                          value={arpAttackerIp}
                          onChange={(e) => setArpAttackerIp(e.target.value)}
                          className="h-8 font-mono text-xs mt-1"
                        />
                      </div>

                      <div>
                        <div className="flex justify-between text-[11px]">
                          <Label>Hujum davomiyligi (sekund)</Label>
                          <span className="font-mono font-semibold">{arpDuration} s</span>
                        </div>
                        <input
                          type="range"
                          min="3"
                          max="30"
                          value={arpDuration}
                          onChange={(e) => setArpDuration(Number(e.target.value))}
                          className="w-full mt-1.5 cursor-pointer"
                        />
                      </div>

                      <div className="rounded-lg border bg-accent/30 p-2.5 flex items-center justify-between">
                        <div>
                          <div className="text-xs font-semibold flex items-center gap-1.5">
                            <ShieldCheck className={cn('h-4 w-4', arpDaiEnabled ? 'text-emerald-500' : 'text-muted-foreground')} />
                            Dynamic ARP Inspection (DAI)
                          </div>
                          <div className="text-[10.5px] text-muted-foreground mt-0.5">
                            L2 Switch DHCP Snooping tekshiruvi
                          </div>
                        </div>
                        <button
                          type="button"
                          onClick={() => setArpDaiEnabled(!arpDaiEnabled)}
                          className={cn(
                            'relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out',
                            arpDaiEnabled ? 'bg-primary' : 'bg-muted'
                          )}
                        >
                          <span
                            className={cn(
                              'pointer-events-none inline-block h-5 w-5 rounded-full bg-background shadow-lg ring-0 transition duration-200 ease-in-out',
                              arpDaiEnabled ? 'translate-x-5' : 'translate-x-0'
                            )}
                          />
                        </button>
                      </div>
                    </div>
                  )}
                </div>

                {/* Action Buttons */}
                <div className="mt-4 pt-3 border-t flex items-center gap-2">
                  <Button
                    onClick={startAttack}
                    disabled={isAttacking}
                    className="flex-1 bg-destructive hover:bg-destructive/90 text-destructive-foreground text-xs"
                  >
                    {isAttacking ? (
                      <>
                        <RefreshCw className="h-3.5 w-3.5 mr-1.5 animate-spin" />
                        Hujum Simulyatsiyasi Bajarilmoqda...
                      </>
                    ) : (
                      <>
                        <Play className="h-3.5 w-3.5 mr-1.5 fill-current" />
                        Hujumni Boshlash
                      </>
                    )}
                  </Button>
                  {isAttacking && (
                    <Button variant="outline" size="icon" onClick={stopAttack} title="To'xtatish">
                      <Square className="h-3.5 w-3.5 text-foreground" />
                    </Button>
                  )}
                </div>
              </div>

              {/* Right Column: Live Dynamics & Real-Time Monitoring */}
              <div className="lg:col-span-7 flex flex-col gap-3">
                {/* Live Gauges Bar */}
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                  {/* Gauge 1: CPU Load */}
                  <div className="rounded-lg border bg-card p-2.5">
                    <div className="flex items-center justify-between text-[11px] text-muted-foreground font-medium">
                      CPU Yuklanishi <Cpu className="h-3.5 w-3.5 text-primary" />
                    </div>
                    <div className="mt-1 font-mono text-xl font-bold tracking-tight">
                      <span className={cn(liveCpu > 80 ? 'text-destructive' : liveCpu > 50 ? 'text-warning' : 'text-emerald-500')}>
                        {liveCpu.toFixed(1)}%
                      </span>
                    </div>
                    <div className="mt-1.5 h-1.5 w-full rounded-full bg-muted overflow-hidden">
                      <div
                        className={cn(
                          'h-full transition-all duration-300',
                          liveCpu > 80 ? 'bg-destructive' : liveCpu > 50 ? 'bg-warning' : 'bg-emerald-500'
                        )}
                        style={{ width: `${Math.min(100, liveCpu)}%` }}
                      />
                    </div>
                  </div>

                  {/* Gauge 2: Queue / Backlog Saturation */}
                  <div className="rounded-lg border bg-card p-2.5">
                    <div className="flex items-center justify-between text-[11px] text-muted-foreground font-medium">
                      Navbat To'lishi <HardDrive className="h-3.5 w-3.5 text-amber-500" />
                    </div>
                    <div className="mt-1 font-mono text-xl font-bold tracking-tight">
                      <span className={cn(liveQueue >= 90 ? 'text-destructive font-black' : liveQueue > 50 ? 'text-warning' : 'text-foreground')}>
                        {liveQueue}%
                      </span>
                    </div>
                    <div className="mt-1.5 h-1.5 w-full rounded-full bg-muted overflow-hidden">
                      <div
                        className={cn(
                          'h-full transition-all duration-300',
                          liveQueue >= 90 ? 'bg-destructive' : liveQueue > 50 ? 'bg-warning' : 'bg-primary'
                        )}
                        style={{ width: `${Math.min(100, liveQueue)}%` }}
                      />
                    </div>
                  </div>

                  {/* Gauge 3: Dropped / Blocked Packets */}
                  <div className="rounded-lg border bg-card p-2.5">
                    <div className="flex items-center justify-between text-[11px] text-muted-foreground font-medium">
                      Bloklangan Paket <XCircle className="h-3.5 w-3.5 text-destructive" />
                    </div>
                    <div className="mt-1 font-mono text-xl font-bold tracking-tight text-destructive">
                      {liveDrops.toLocaleString()}
                    </div>
                    <div className="text-[10px] text-muted-foreground mt-0.5 truncate">
                      {liveDrops > 0 ? 'Navbat to‘lib tashlandi' : 'Yo‘qotish 0%'}
                    </div>
                  </div>

                  {/* Gauge 4: Dynamic Bandwidth / Availability */}
                  <div className="rounded-lg border bg-card p-2.5">
                    <div className="flex items-center justify-between text-[11px] text-muted-foreground font-medium">
                      Oqim / Tezlik <Zap className="h-3.5 w-3.5 text-sky-500" />
                    </div>
                    <div className="mt-1 font-mono text-xl font-bold tracking-tight text-sky-500">
                      {liveBandwidth.toFixed(1)} Mbps
                    </div>
                    <div className="text-[10px] text-muted-foreground mt-0.5 truncate">
                      Trafik hajmi
                    </div>
                  </div>
                </div>

                {/* Visualizer Display (SYN Queue / Worker Pool / MitM flow) */}
                <div className="rounded-xl border bg-card p-3 flex flex-col justify-between flex-1">
                  <div className="flex items-center justify-between border-b pb-2 mb-2">
                    <span className="text-xs font-semibold flex items-center gap-1.5">
                      <Activity className="h-3.5 w-3.5 text-primary" /> Real-Vaqt Dinamikasi va Holat Xaritasi
                    </span>
                    {isAttacking && (
                      <span className="flex items-center gap-1.5 text-xs text-destructive font-semibold animate-pulse">
                        <Flame className="h-3.5 w-3.5 fill-current" /> Hujum Faol ({attackProgress}%)
                      </span>
                    )}
                  </div>

                  {/* Attack-specific visualizer */}
                  {selectedAttack === 'syn_flood' && (
                    <div className="p-2 bg-muted/20 rounded-lg">
                      <div className="flex justify-between text-xs mb-1 font-medium">
                        <span>TCP SYN Backlog Navbati (Sig'im: 1024 slots)</span>
                        <span className="font-mono text-muted-foreground">{synCookies ? 'SYN Cookies Faol (TCB tejalmoqda)' : `${liveQueue}% band`}</span>
                      </div>
                      <div className="grid grid-cols-16 gap-1 my-2">
                        {Array.from({ length: 32 }).map((_, idx) => {
                          const occupied = !synCookies && idx < (liveQueue / 100) * 32
                          return (
                            <div
                              key={idx}
                              className={cn(
                                'h-3 rounded-xs transition-colors duration-200',
                                synCookies
                                  ? 'bg-emerald-500/40 border border-emerald-500/60'
                                  : occupied
                                  ? 'bg-rose-500'
                                  : 'bg-muted-foreground/20'
                              )}
                              title={synCookies ? 'SYN Cookie tasdiqlangan' : occupied ? 'SYN navbatda qotib qolgan' : 'Bo‘sh slot'}
                            />
                          )
                        })}
                      </div>
                      <div className="flex items-center gap-4 text-[10.5px] text-muted-foreground mt-1.5">
                        <span className="flex items-center gap-1">
                          <i className="h-2 w-2 rounded-xs bg-rose-500 inline-block" /> Band/Qotgan TCB
                        </span>
                        <span className="flex items-center gap-1">
                          <i className="h-2 w-2 rounded-xs bg-muted-foreground/20 inline-block" /> Bo'sh slot
                        </span>
                        <span className="flex items-center gap-1">
                          <i className="h-2 w-2 rounded-xs bg-emerald-500/60 inline-block" /> SYN Cookie himoyalangan
                        </span>
                      </div>
                    </div>
                  )}

                  {selectedAttack === 'udp_reflection' && (
                    <div className="p-2 bg-muted/20 rounded-lg">
                      <div className="flex justify-between text-xs mb-1 font-medium">
                        <span>Qurbon Nishon Internet Kanali (100 Mbps)</span>
                        <span className="font-mono">{liveBandwidth.toFixed(1)} / 100 Mbps ({Math.min(100, (liveBandwidth / 100) * 100).toFixed(0)}%)</span>
                      </div>
                      <div className="h-4 w-full rounded-md bg-muted overflow-hidden my-2 flex">
                        <div
                          className={cn('h-full transition-all duration-200', liveBandwidth > 95 ? 'bg-destructive' : 'bg-sky-500')}
                          style={{ width: `${Math.min(100, liveBandwidth)}%` }}
                        />
                      </div>
                      <div className="text-[11px] text-muted-foreground">
                        Amplifikatsiya: <b>{udpReflectorType === 'ntp' ? 'NTP (~200x)' : 'DNS (~54x)'}</b>. So'rov o'lchami: 64 bayt ➜ Javob: 3,840 bayt.
                      </div>
                    </div>
                  )}

                  {selectedAttack === 'slowloris' && (
                    <div className="p-2 bg-muted/20 rounded-lg">
                      <div className="flex justify-between text-xs mb-1 font-medium">
                        <span>Apache/Nginx Worker Pool Ulanishlari</span>
                        <span className="font-mono text-amber-500">{liveQueue}% Workerlar band</span>
                      </div>
                      <div className="grid grid-cols-20 gap-1 my-2">
                        {Array.from({ length: 40 }).map((_, idx) => {
                          const hung = idx < (liveQueue / 100) * 40
                          return (
                            <div
                              key={idx}
                              className={cn(
                                'h-3 rounded-xs transition-colors duration-200',
                                hung ? 'bg-amber-500' : 'bg-muted-foreground/20'
                              )}
                              title={hung ? 'Chala header bilan band' : 'Bo‘sh worker'}
                            />
                          )
                        })}
                      </div>
                      <div className="text-[11px] text-muted-foreground">
                        Qonuniy foydalanuvchilar server javob kechikishi: <b>{liveQueue >= 90 ? '30,000 ms (Timeout / 100% DoS)' : '180 ms'}</b>
                      </div>
                    </div>
                  )}

                  {selectedAttack === 'arp_spoof' && (
                    <div className="p-2 bg-muted/20 rounded-lg">
                      <div className="flex items-center justify-around my-2 text-center text-xs">
                        <div className="rounded border bg-card p-2 font-mono">
                          <Server className="h-4 w-4 mx-auto mb-1 text-primary" />
                          <div>{arpVictimIp}</div>
                          <div className="text-[9.5px] text-muted-foreground">Victim PC</div>
                        </div>

                        <div className="flex flex-col items-center">
                          <ArrowDownUp className={cn('h-4 w-4', arpDaiEnabled ? 'text-emerald-500' : 'text-destructive animate-bounce')} />
                          <Badge variant={arpDaiEnabled ? 'success' : 'destructive'} className="text-[9px] mt-1">
                            {arpDaiEnabled ? 'DAI Blocked' : 'MitM Intercepting'}
                          </Badge>
                        </div>

                        <div className="rounded border bg-card p-2 font-mono border-destructive/50">
                          <AlertTriangle className="h-4 w-4 mx-auto mb-1 text-destructive" />
                          <div className="text-destructive font-semibold">{arpAttackerIp}</div>
                          <div className="text-[9.5px] text-muted-foreground">Attacker (MitM)</div>
                        </div>

                        <div className="flex flex-col items-center">
                          <ArrowDownUp className={cn('h-4 w-4', arpDaiEnabled ? 'text-emerald-500' : 'text-destructive animate-bounce')} />
                          <Badge variant={arpDaiEnabled ? 'success' : 'destructive'} className="text-[9px] mt-1">
                            {arpDaiEnabled ? 'DAI Blocked' : 'Poisoned ARP'}
                          </Badge>
                        </div>

                        <div className="rounded border bg-card p-2 font-mono">
                          <Globe className="h-4 w-4 mx-auto mb-1 text-emerald-500" />
                          <div>{arpGatewayIp}</div>
                          <div className="text-[9.5px] text-muted-foreground">Gateway Router</div>
                        </div>
                      </div>
                      <div className="text-[11px] text-center text-muted-foreground mt-1">
                        {arpDaiEnabled
                          ? 'Dynamic ARP Inspection (DAI) DHCP Snooping tekshiruvidan o‘tmagan soxta ARP xabarlarini bloklamoqda.'
                          : 'Qurbon va shlyuzning ARP keshi zaharlangan: butun trafik tajovuzkor qurilma orqali o‘tmoqda!'}
                      </div>
                    </div>
                  )}

                  {/* Dynamic Dynamics Chart (Live SVG sparkline/timeline) */}
                  <div className="mt-2 pt-2 border-t">
                    <div className="flex items-center justify-between text-[11px] font-medium text-muted-foreground mb-1">
                      <span>Vaqt dinamikasi (CPU % va Yo'qotishlar)</span>
                      <div className="flex items-center gap-3 text-[10px]">
                        <span className="flex items-center gap-1"><i className="h-2 w-2 rounded-full bg-primary inline-block" /> CPU %</span>
                        <span className="flex items-center gap-1"><i className="h-2 w-2 rounded-full bg-destructive inline-block" /> Navbat %</span>
                      </div>
                    </div>
                    <div className="h-24 w-full rounded bg-muted/10 border relative flex items-end px-2 py-1">
                      {liveChartPoints.length > 1 ? (
                        <svg className="w-full h-full overflow-visible" viewBox={`0 0 ${liveChartPoints.length - 1} 100`} preserveAspectRatio="none">
                          <polyline
                            fill="none"
                            stroke="hsl(var(--primary))"
                            strokeWidth="2"
                            points={liveChartPoints.map((p, i) => `${i},${100 - p.cpu}`).join(' ')}
                          />
                          <polyline
                            fill="none"
                            stroke="hsl(var(--destructive))"
                            strokeWidth="2"
                            strokeDasharray="2 2"
                            points={liveChartPoints.map((p, i) => `${i},${100 - p.queue}`).join(' ')}
                          />
                        </svg>
                      ) : (
                        <div className="grid place-items-center w-full text-xs text-muted-foreground">
                          Hujum boshlanganda dinamik grafik chiziladi
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {/* Attack Outcome & Mitigation Summary Box */}
            {currentAttackResult && (
              <div className="rounded-xl border border-primary/30 bg-primary/5 p-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <CheckCircle2 className="h-4 w-4 text-primary" />
                    <span className="text-xs font-semibold">Simulyatsiya Xulosasi va Hujum Oqibatlari</span>
                  </div>
                  <Badge variant="outline" className="text-[10px] uppercase font-mono border-primary/40 text-primary">
                    {currentAttackResult.attack_type}
                  </Badge>
                </div>
                <div className="mt-2 text-xs text-foreground leading-relaxed">
                  {currentAttackResult.alert_generated?.description}
                </div>
                <div className="mt-2.5 rounded-lg border bg-card p-2.5 flex items-start gap-2">
                  <ShieldCheck className="h-4 w-4 text-emerald-500 shrink-0 mt-0.5" />
                  <div className="text-xs">
                    <b className="font-semibold text-emerald-600 dark:text-emerald-400">Tavsiya etilgan himoya chorasi (Mitigation):</b>
                    <p className="mt-0.5 text-muted-foreground">{currentAttackResult.alert_generated?.mitigation}</p>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Tab 3: Security Alerts & IDS Log */}
        {activeTab === 'alerts' && (
          <div className="flex flex-1 flex-col overflow-auto p-3 gap-3">
            {/* Severity Counter Pills & Actions */}
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border bg-card p-2.5">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="text-xs text-muted-foreground font-medium mr-1">Daraja:</span>
                <button
                  onClick={() => setSeverityFilter('all')}
                  className={cn(
                    'rounded-md px-2.5 py-1 text-xs font-medium transition-colors',
                    severityFilter === 'all' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-accent'
                  )}
                >
                  Barchasi ({alertStats.total})
                </button>
                <button
                  onClick={() => setSeverityFilter('critical')}
                  className={cn(
                    'rounded-md px-2.5 py-1 text-xs font-semibold transition-colors flex items-center gap-1',
                    severityFilter === 'critical'
                      ? 'bg-destructive text-destructive-foreground'
                      : 'text-destructive hover:bg-destructive/10'
                  )}
                >
                  <AlertOctagon className="h-3 w-3" /> Critical ({alertStats.critical})
                </button>
                <button
                  onClick={() => setSeverityFilter('high')}
                  className={cn(
                    'rounded-md px-2.5 py-1 text-xs font-semibold transition-colors flex items-center gap-1',
                    severityFilter === 'high'
                      ? 'bg-amber-500 text-white'
                      : 'text-amber-500 hover:bg-amber-500/10'
                  )}
                >
                  <AlertTriangle className="h-3 w-3" /> High ({alertStats.high})
                </button>
                <button
                  onClick={() => setSeverityFilter('medium')}
                  className={cn(
                    'rounded-md px-2.5 py-1 text-xs font-medium transition-colors',
                    severityFilter === 'medium' ? 'bg-yellow-500 text-black' : 'text-yellow-600 dark:text-yellow-400 hover:bg-yellow-500/10'
                  )}
                >
                  Medium ({alertStats.medium})
                </button>
                <button
                  onClick={() => setSeverityFilter('low')}
                  className={cn(
                    'rounded-md px-2.5 py-1 text-xs font-medium transition-colors',
                    severityFilter === 'low' ? 'bg-sky-500 text-white' : 'text-sky-500 hover:bg-sky-500/10'
                  )}
                >
                  Low ({alertStats.low})
                </button>
              </div>

              <div className="flex items-center gap-2">
                <Button variant="outline" size="sm" onClick={exportAlertsJson} className="h-7 text-xs">
                  <Download className="h-3.5 w-3.5 mr-1" /> Eksport (JSON)
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setAlertsList([])
                    toast("Barcha xavfsizlik ogohlantirishlari tozalandi", 'info')
                  }}
                  className="h-7 text-xs text-muted-foreground hover:text-destructive"
                >
                  <Trash2 className="h-3.5 w-3.5 mr-1" /> Tozalash
                </Button>
              </div>
            </div>

            {/* Search Input */}
            <div className="relative">
              <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
              <Input
                placeholder="Ogohlantirishlarni qidirish: manba, nishon, hujum turi, tavsif..."
                value={searchAlerts}
                onChange={(e) => setSearchAlerts(e.target.value)}
                className="h-8 pl-8 text-xs"
              />
            </div>

            {/* Alerts Feed */}
            <div className="flex-1 flex flex-col gap-2 overflow-auto">
              {!filteredAlerts.length ? (
                <div className="py-12 text-center text-xs text-muted-foreground border rounded-lg bg-card/40">
                  Mos keluvchi xavfsizlik ogohlantirishlari yo'q
                </div>
              ) : (
                filteredAlerts.map((alert) => {
                  const isExpanded = expandedAlertId === alert.id
                  const isCrit = alert.severity === 'critical'
                  const isHigh = alert.severity === 'high'

                  return (
                    <div
                      key={alert.id}
                      className={cn(
                        'rounded-xl border p-3 bg-card transition-all cursor-pointer',
                        isCrit
                          ? 'border-destructive/40 hover:border-destructive'
                          : isHigh
                          ? 'border-amber-500/40 hover:border-amber-500'
                          : 'border-border hover:border-foreground/30'
                      )}
                      onClick={() => setExpandedAlertId(isExpanded ? null : alert.id)}
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="flex items-center gap-2">
                          <Badge
                            variant={isCrit ? 'destructive' : isHigh ? 'warning' : 'outline'}
                            className={cn(
                              'uppercase font-mono text-[10px] font-bold px-2 py-0.5',
                              isCrit && 'animate-pulse'
                            )}
                          >
                            {alert.severity}
                          </Badge>
                          <span className="font-semibold text-xs tracking-tight">{alert.attack_type.toUpperCase().replace('_', ' ')}</span>
                          <span className="font-mono text-[11px] text-muted-foreground">
                            {alert.source} ➜ {alert.target}
                          </span>
                        </div>
                        <div className="flex items-center gap-2 text-[11px] text-muted-foreground font-mono">
                          <Clock className="h-3 w-3" />
                          {new Date(alert.timestamp).toLocaleTimeString()}
                        </div>
                      </div>

                      <p className="mt-2 text-xs text-foreground leading-relaxed">
                        {alert.description}
                      </p>

                      {/* Mitigation Card */}
                      <div className="mt-2.5 rounded-lg border bg-muted/30 p-2.5 flex items-start gap-2">
                        <ShieldCheck className="h-4 w-4 text-emerald-500 shrink-0 mt-0.5" />
                        <div className="text-xs">
                          <b className="font-semibold text-emerald-600 dark:text-emerald-400">Tavsiya etilgan chora (Mitigation):</b>
                          <div className="mt-0.5 font-mono text-[11px] text-muted-foreground bg-background/60 p-1.5 rounded border mt-1">
                            {alert.mitigation}
                          </div>
                        </div>
                      </div>
                    </div>
                  )
                })
              )}
            </div>
          </div>
        )}
      </div>
  )

  if (!win) {
    return (
      <div className="glass flex h-full min-h-0 flex-1 flex-col overflow-hidden rounded-xl shadow-lg">
        <div className="flex items-center gap-2.5 border-b border-border/50 px-4 py-2.5 bg-card/25 shrink-0">
          <Shield className="h-5 w-5 text-rose-400" />
          <div>
            <div className="text-xs font-semibold text-foreground">Security Studio</div>
            <div className="text-[10.5px] text-muted-foreground">Conntrack Sessiyalar Jadvali, Kibernetik Hujumlar Simulyatori va IDS Monitoring</div>
          </div>
        </div>
        {bodyContent}
      </div>
    )
  }

  return (
    <WindowFrame
      win={win}
      icon={<Shield className="h-5 w-5 text-primary" />}
      title="Security Studio"
      sub="Conntrack Sessiyalar Jadvali, Kibernetik Hujumlar Simulyatori va IDS Monitoring"
    >
      {bodyContent}
    </WindowFrame>
  )
}

function round(v: number, p = 2) {
  const m = Math.pow(10, p)
  return Math.round(v * m) / m
}
