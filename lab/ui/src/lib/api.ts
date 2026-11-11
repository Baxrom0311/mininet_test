async function req<T>(method: string, url: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const opt: RequestInit = { method, headers: {}, signal }
  if (body !== undefined) { (opt.headers as Record<string, string>)['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body) }
  let r: Response
  try { r = await fetch(url, opt) } catch (error) { if (signal?.aborted) throw error; throw new Error("Server bilan aloqa yo'q") }
  let data: any = null
  try { data = await r.json() } catch { /* bo'sh javob */ }
  if (!r.ok) throw new Error((data && (data.error || data.detail)) || 'HTTP ' + r.status)
  return data as T
}
export const api = {
  get: <T = any>(u: string, signal?: AbortSignal) => req<T>('GET', u, undefined, signal),
  post: <T = any>(u: string, b: unknown = {}, signal?: AbortSignal) => req<T>('POST', u, b, signal),
  put: <T = any>(u: string, b: unknown) => req<T>('PUT', u, b),
  patch: <T = any>(u: string, b: unknown) => req<T>('PATCH', u, b),
  del: <T = any>(u: string) => req<T>('DELETE', u),
}
export const wsUrl = (path: string) => (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + path
