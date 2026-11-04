/** Wi-Fi radio modeli — jadvallar serverdan (/api/info.wifi, manba: lab/wifi.py), formula aynan takrorlanadi. */
import type { Device, Project, WifiModel } from './types'

export interface ApCfg { standard: string; band: 2.4 | 5; width: number; tx_power: number; ssid: string; channel: number }
export interface Estimate { distance_m: number; rssi_dbm: number; snr_db: number; mcs: number | null; phy_mbps: number; thr_mbps: number; ok: boolean; ap?: string; sta?: string }

export function apConfig(m: WifiModel | undefined, dev?: Device): ApCfg {
  const c: any = { ...(m?.ap_defaults || {}), ...(dev?.config || {}) }
  if (c.standard === '80211g') { c.band = 2.4; c.width = 20 }
  if (c.standard === '80211ac') c.band = 5
  c.band = parseFloat(c.band) < 4 ? 2.4 : 5
  c.width = [20, 40, 80, 160].includes(parseInt(c.width, 10)) ? parseInt(c.width, 10) : 20
  if (c.band === 2.4 && c.width > 40) c.width = 40
  c.tx_power = parseFloat(c.tx_power); if (!isFinite(c.tx_power)) c.tx_power = 20
  const allowed = m?.channels?.[c.band === 2.4 ? '2.4' : '5']?.[String(c.width)] || []
  const ch = parseInt(c.channel, 10)
  c.channel = allowed.includes(ch) ? ch : (allowed[0] || 0)
  return c as ApCfg
}

export function estimate(m: WifiModel | undefined, distM: number, c: ApCfg): Estimate | null {
  if (!m) return null
  const d = Math.max(distM, 1)
  const rssi = c.tx_power - (m.ref_loss[String(c.band)] + 10 * m.exponent * Math.log10(d))
  const snr = rssi - (-174 + 10 * Math.log10(c.width * 1e6) + 7)
  const table = m.mcs[c.standard] || m.mcs['80211ax']
  let mcs = -1
  table.forEach((r, i) => { if (snr >= r[0]) mcs = i })
  const ok = rssi >= m.min_rssi && mcs >= 0
  const phy = ok ? table[mcs][1] * m.width_mult[String(c.width)] : 0
  return { distance_m: d, rssi_dbm: rssi, snr_db: snr, mcs: ok ? mcs : null, phy_mbps: phy, thr_mbps: phy * (m.efficiency[c.standard] || 0.8), ok }
}

export const range = (m: WifiModel | undefined, c: ApCfg) => m ? Math.pow(10, (c.tx_power - m.min_rssi - m.ref_loss[String(c.band)]) / (10 * m.exponent)) : 0
export function rangeForSnr(m: WifiModel | undefined, c: ApCfg, snr: number) {
  if (!m) return 0
  const noise = -174 + 10 * Math.log10(c.width * 1e6) + 7
  return Math.pow(10, (c.tx_power - (noise + snr) - m.ref_loss[String(c.band)]) / (10 * m.exponent))
}
export const mpp = (p?: Project) => { const v = Number(p?.meters_per_px); return v > 0 ? v : 0.25 }

export function linkEstimate(m: WifiModel | undefined, p: Project, lid: string): Estimate | null {
  const l = p.links[lid]
  if (!l || l.params?.medium !== 'wifi') return null
  const A = p.devices[l.a.dev], B = p.devices[l.b.dev]
  if (!A || !B) return null
  const apIsA = A.type === 'wifiap'
  const est = estimate(m, Math.hypot(A.x - B.x, A.y - B.y) * mpp(p), apConfig(m, apIsA ? A : B))
  if (est) { est.ap = apIsA ? l.a.dev : l.b.dev; est.sta = apIsA ? l.b.dev : l.a.dev }
  return est
}

export const essKey = (m: WifiModel | undefined, name: string, d: Device) => {
  const c = apConfig(m, d)
  return [c.ssid || 'netlab-' + name, c.band, c.channel].join('|')
}
