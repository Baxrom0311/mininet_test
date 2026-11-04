/** Backend hodisalari (WebSocket /ws/events) uchun oddiy pub-sub, avtomatik qayta ulanish bilan.
 *  React'dan tashqarida: paket animatsiyasi kabi tez-tez keladigan hodisalar re-render qilmasin. */
import { wsUrl } from './api'

type Handler = (ev: any) => void
const subs: Record<string, Set<Handler>> = {}

export function on(type: string, fn: Handler) {
  (subs[type] ||= new Set()).add(fn)
  return () => { subs[type]?.delete(fn) }
}
export function emit(type: string, ev: any) {
  subs[type]?.forEach((fn) => { try { fn(ev) } catch (e) { console.error(type, e) } })
}

let backoff = 500
export function connectEvents() {
  const ws = new WebSocket(wsUrl('/ws/events'))
  let ka: ReturnType<typeof setInterval> | undefined
  ws.onopen = () => { backoff = 500; emit('ws', true); ka = setInterval(() => ws.readyState === 1 && ws.send('ping'), 20000) }
  ws.onmessage = (m) => {
    let d: any
    try { d = JSON.parse(m.data) } catch { return }
    (Array.isArray(d) ? d : [d]).forEach((ev) => emit('ev:' + ev.type, ev))
  }
  ws.onclose = () => {
    clearInterval(ka); emit('ws', false)
    setTimeout(connectEvents, backoff); backoff = Math.min(backoff * 2, 8000)
  }
}
