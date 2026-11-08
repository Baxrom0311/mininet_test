import type { RunListItem, RunResult } from './types'

export const isActive = (status: string) => ['queued', 'running', 'cancelling'].includes(status)
export const runState = (run: Pick<RunListItem, 'status' | 'queue_state'>) => run.queue_state || run.status
export const statusLabel = (status: string) => ({ done: 'Tayyor', running: 'Ishlayapti', queued: 'Navbatda', cancelling: 'Bekor qilinmoqda', cancelled: 'Bekor qilingan', error: 'Xato', interrupted: 'Uzilgan', incomplete: 'To‘liq emas', failed: 'Xato', unknown: 'Noma’lum' } as Record<string,string>)[status] || status
export const visibleFlows = (run: RunResult) => Object.entries(run.flows || {}).filter(([,flow]) => !flow.hidden)
export const csvCell = (value: unknown) => '"' + String(value ?? '').replace(/"/g, '""') + '"'
export function resultsCsv(runs: RunResult[]) {
  const rows: unknown[][] = [['run_id','scenario','engine','param','value','param2','value2','flow','avg_thr_bps','avg_delay_ms','avg_rtt_ms','avg_jitter_ms','loss_pct']]
  for (const run of runs) for (const [fid,flow] of visibleFlows(run)) {
    const s = flow.summary || {}
    rows.push([run.run_id,run.scenario_name,run.engine,run.study_param?.label,run.study_value,run.study_param2?.label,run.study_value2,flow.label || fid,s.avg_thr_bps,s.avg_delay_ms,s.avg_rtt_ms,s.avg_jitter_ms,s.loss_pct])
  }
  return rows.map(row => row.map(csvCell).join(',')).join('\r\n')+'\r\n'
}
export function downloadCsv(text: string, name: string) {
  const url = URL.createObjectURL(new Blob(['\ufeff',text], {type:'text/csv;charset=utf-8'}))
  const anchor = document.createElement('a')
  anchor.href = url; anchor.download = name; anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
export function filterRuns(runs: RunListItem[], search: string) {
  const q = search.trim().toLocaleLowerCase()
  return runs.filter(run => [run.label,run.scenario_name,run.run_id,run.study_id].some(value => value?.toLocaleLowerCase().includes(q)))
}
