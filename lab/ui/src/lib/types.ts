export type DeviceType = 'host' | 'server' | 'switch' | 'p4switch' | 'router' | 'sdnrouter' | 'wifiap'

export interface LinkParams { bw: number; delay: string; jitter: string; loss: number; queue: number; medium?: 'wire' | 'wifi' }
export interface Endpoint { dev: string; port: number }
export interface Link { a: Endpoint; b: Endpoint; params: LinkParams; up?: boolean }
export interface Device { id: number; type: DeviceType; x: number; y: number; config: Record<string, any> }
export interface ScenarioApp { id: string; type: 'udp' | 'tcp' | 'ping'; src: string; dst: string; rate?: string; size?: number; start?: number; stop?: number; label?: string; interval?: number; dst_ip?: string }
export interface ScenarioEvent { t: number; action: 'link_down' | 'link_up' | 'link_params' | 'move' | 'roam'; link?: string; params?: Record<string, any>; device?: string; x?: number; y?: number; duration?: number; from?: string; to?: string }
export interface Scenario { duration: number; apps: ScenarioApp[]; events: ScenarioEvent[]; notes?: string; seed?: number }
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
export interface LinkStats { ab_bps: number; ba_bps: number; ab_pps?: number; ba_pps?: number; util: number; drops?: number }
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
  status: string; error?: string; label?: string; flows: Record<string, FlowResult>; links?: Record<string, { t: number; util: number; ab_bps: number; ba_bps: number }[]>
  events?: ScenarioEvent[]; notes?: string[]
  study_id?: string; study_param?: any; study_value?: any; study_index?: number; study_param2?: any; study_value2?: any; study_index2?: number
}
export type RunListItem = Pick<RunResult, 'run_id' | 'scenario_name' | 'engine' | 'created' | 'wall_s' | 'status' | 'label' | 'study_id' | 'study_param' | 'study_value' | 'study_index' | 'study_param2' | 'study_value2' | 'study_index2'> & { progress?: number }
