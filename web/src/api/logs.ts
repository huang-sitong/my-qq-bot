/** 日志实时 API 客户端：建立 WebSocket 连接，接收后端广播的日志条目。 */

import type { LogEntry } from '../types/logs'

/** 构造 WebSocket 地址：开发时经 Vite 代理（/api），生产同源。 */
export function buildLogWsUrl(): string {
  const base = import.meta.env.VITE_WS_BASE as string | undefined
  if (base) return `${base}/api/logs/ws`
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/api/logs/ws`
}

export function parseLogMessage(data: string): LogEntry | null {
  try {
    const raw = JSON.parse(data) as Partial<LogEntry>
    if (typeof raw.message !== 'string') return null
    return {
      ts: raw.ts ?? '',
      level: raw.level ?? 'INFO',
      logger: raw.logger ?? '',
      message: raw.message,
      trace_id: raw.trace_id ?? '-',
    }
  } catch {
    return null
  }
}
