import {useEffect, useState} from 'react'

export async function api(path, {method = 'GET', body, signal} = {}) {
  const headers = {'X-Server-Console': '1'}
  if (body !== undefined && !(body instanceof Blob)) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(body)
  }
  const res = await fetch('/api' + path, {method, body, headers, signal, cache: 'no-store'})
  const value = res.headers.get('content-type')?.includes('application/json') ? await res.json() : null
  if (!res.ok) {
    if (res.status === 401 && path !== '/login') window.dispatchEvent(new Event('console-logout'))
    throw Object.assign(new Error(typeof value?.detail === 'string' ? value.detail : `请求失败（HTTP ${res.status}）`), {status: res.status})
  }
  return value
}

export function useResource(path, interval = 0) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    if (!path) return
    const controller = new AbortController()
    let timer
    async function load() {
      try {
        const value = await api(path, {signal: controller.signal})
        if (!controller.signal.aborted) {
          setData(value)
          setError('')
        }
      } catch (e) {
        if (!controller.signal.aborted) setError(e.message)
      } finally {
        if (interval && !controller.signal.aborted) timer = setTimeout(load, interval)
      }
    }
    load()
    return () => {controller.abort(); clearTimeout(timer)}
  }, [path, interval, revision])
  return {data, error, reload: () => setRevision(n => n + 1)}
}

export function bytes(value, digits = 1) {
  if (!Number.isFinite(value)) return '—'
  const i = Math.min(4, Math.floor(Math.log(Math.max(1, value)) / Math.log(1024)))
  return (value / 1024 ** i).toFixed(i ? digits : 0) + ' ' + ['B', 'KiB', 'MiB', 'GiB', 'TiB'][i]
}

export function uptime(total) {
  if (!Number.isFinite(total)) return '—'
  const days = Math.floor(total / 86400), hours = Math.floor(total / 3600) % 24
  return (days ? days + ' 天 ' : '') + hours + ' 小时 ' + Math.floor(total / 60) % 60 + ' 分钟'
}
