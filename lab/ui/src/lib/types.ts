export type DeviceType = 'host' | 'server' | 'switch' | 'p4switch' | 'router' | 'sdnrouter' | 'wifiap'

export interface LinkParams { bw: number; delay: string; jitter: string; loss: number; queue: number; medium?: 'wire' | 'wifi'; qdisc?: 'fifo' | 'prio' | 'fq_codel' }
export interface Endpoint { dev: string; port: number }
export interface Link { a: Endpoint; b: Endpoint; params: LinkParams; up?: boolean }
export interface Device { id: number; type: DeviceType; x: number; y: number; config: Record<string, any> }
export interface ScenarioApp { id: string; type: 'udp' | 'tcp' | 'ping' | 'voip' | 'http' | 'ftp' | 'mcast'; src: string; dst: string; group?: string;  /** mcast: dst — qabul qiluvchilar ro'yxati (string[]) */ rate?: string; size?: number; start?: number; stop?: number; label?: string; interval?: number; dst_ip?: string; bytes?: number; codec?: 'g711' | 'g729'; page?: string; think?: number; file?: string; direction?: 'download' | 'upload'; dscp?: number }
export interface ScenarioEvent { t: number; action: 'link_down' | 'link_up' | 'link_params' | 'move' | 'roam'; link?: string; params?: Record<string, any>; device?: string; x?: number; y?: number; duration?: number; from?: string; to?: string }
export interface Scenario { duration: number; apps: ScenarioApp[]; events: ScenarioEvent[]; notes?: string; seed?: number; ip_version?: 4 | 6 }
export interface Project {
  version: number; name: string; notes?: string; meters_per_px?: number
  devices: Record<string, Device>; links: Record<string, Link>
  p4_programs: Record<string, string>; controllers: Record<string, { code: string; port: number }>
  scenarios: Record<string, Scenario>
}
export interface Status { driver?: string; running: boolean; project?: string | null; capture?: boolean }
export interface WifiModel {
  mcs: Record<string, [number, number][]>; width_mult: Record<string, number>; efficiency: Record<string, number>
  ref_loss: Record<string, number>; min_rssi: number; exponent: number; ap_defaults: Record<string, any>; standards: string[]
  channels: Record<string, Record<string, number[]>>
}
export interface Info {
  driver: 'demo' | 'real'; capabilities: Record<string, boolean>; device_types: Record<string, { label: string; ns: boolean; l3: boolean }>
  frr_daemons: string[]; wifi: WifiModel; status: Status
}
export interface LinkStats { ab_bps: number; ba_bps: number; ab_pps?: number; ba_pps?: number; util: number; drops?: number; queue_ab?: number; queue_ba?: number; qdrops?: number }
/** natijadagi kabel nuqtasi (soniyalik) */
export interface LinkPoint { t: number; util: number; ab_bps: number; ba_bps: number; queue_ab?: number; queue_ba?: number; drops_ab?: number; drops_ba?: number }
/** qurilma nuqtasi (L3: host/server/router) */
export interface NodePoint { t: number; routes: number; fwd_pps: number; rx_pps: number; tx_pps: number; noroute: number; discards: number }
export interface RouteEvent { t: number; dev: string; added: string[]; removed: string[] }
export interface Convergence { t: number; action: string; link: string; converged_s: number | null; last_change: number | null; devices: string[] }
export type Selection = { kind: 'device' | 'link'; id: string } | null
export interface InspectSection { title: string; kind: 'table' | 'text' | 'pipeline'; columns?: string[]; rows?: any[][]; text?: string; data?: Pipeline; hint?: string }
export interface Pipeline {
  parsers: { name: string; states: { name: string; extracts: string[]; next: string[] }[] }[]
  pipelines: { name: string; tables: { name: string; match: { field: string; type: string }[]; actions: string[]; size?: number | null }[] }[]
  deparsers: { name: string; order: string[] }[]; headers: string[]; counters: string[]; registers: { name: string; size?: number | null }[]
}
export interface FlowPoint { t: number; thr_bps: number; delay_ms: number | null; jitter_ms: number | null; loss_pct: number | null; rtt_ms?: number | null }
export interface FlowResult { id: string; label: string; type: string; src: string; dst: string; series: FlowPoint[]; summary: Record<string, number | null> }
export interface RunResult {
  run_id: string; scenario_name: string; engine: string; engine_label?: string; created: number; duration: number; wall_s?: number
  status: string; error?: string; label?: string; flows: Record<string, FlowResult>; links?: Record<string, LinkPoint[]>
  nodes?: Record<string, NodePoint[]>; route_events?: RouteEvent[]; convergence?: Convergence[]
  /** ilova modellari xulosasi (voip: avg_mos/min_mos/quality, http: pages/avg_page_s, ftp: transfer_s) */
  apps?: Record<string, { type: 'voip' | 'http' | 'ftp'; [k: string]: any }>
  events?: ScenarioEvent[]; notes?: string[]
  study_id?: string; study_param?: any; study_value?: any; study_index?: number; study_param2?: any; study_value2?: any; study_index2?: number
}
export type RunListItem = Pick<RunResult, 'run_id' | 'scenario_name' | 'engine' | 'created' | 'wall_s' | 'status' | 'label' | 'study_id' | 'study_param' | 'study_value' | 'study_index' | 'study_param2' | 'study_value2' | 'study_index2'> & { progress?: number }
/** POST /api/analysis/survivability — yakka nosozliklar ta'siri (N-1) va sig'im rejasi */
export interface SurvivabilityCase {
  kind: 'link' | 'device' | 'srlg' | 'pair'; target: string; down_links: string[]; score: number
  members?: { kind: 'link' | 'device'; target: string }[]
  broken: { flow: string; label?: string; why?: string | null }[]; broken_demand_pct: number
  rerouted: string[]; degraded: { flow: string; label?: string; thr_change_pct: number; thr_bps: number; base_thr_bps: number }[]
  udp_loss: { flow: string; loss_pct: number }[]; overloaded: { link: string; util: number }[]
  max_util: number; mos: Record<string, { base: number; fail: number }>; convergence: Record<string, number>
}
export interface CapacityPlanRow {
  link: string; dir: 'ab' | 'ba'; bw_mbps: number; base_load_mbps: number; worst_load_mbps: number
  worst_case: string | null; worst_util: number; worst_tcp_mbps: number; need_mbps: number; upgrade: boolean
}
export interface SurvivabilityReport {
  scenario: string; targets: 'links' | 'devices' | 'both'; wall_s: number; notes: string[]
  baseline: { flows: Record<string, { label?: string; type: string; thr_bps: number; loss_pct: number | null; alive: boolean; why?: string | null }>
              down_flows: string[]; links: Record<string, number>; mos: Record<string, number | null> }
  cases: SurvivabilityCase[]; spof: { kind: 'link' | 'device' | 'srlg'; target: string; broken: string[] }[]
  capacity_plan: CapacityPlanRow[]; upgrades: CapacityPlanRow[]
  /** order=2: juft nosozliklar */
  n2?: { pairs_total: number; pairs_candidates: number; pairs_evaluated: number; pruned: number; truncated: boolean
         max_cases: number; cases: SurvivabilityCase[]; cases_omitted: number
         vulnerabilities: { pair: string[]; target: string; broken: string[]; broken_demand_pct: number }[]
         capacity_plan: CapacityPlanRow[]; upgrades: CapacityPlanRow[] }
  /** oqim -> minimal kesim: 1 (SPOF), 2, '>=2' | '>=3', null (asosiy holatda ishlamaydi) */
  min_cut?: Record<string, number | string | null>
  srlg?: Record<string, string[]>; srlg_broken?: Record<string, string[]>; exclude_access?: boolean
}
