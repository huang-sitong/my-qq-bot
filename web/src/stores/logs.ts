/** 日志控制台 Pinia store：WebSocket 实时接收、筛选、自动滚动。 */

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { buildLogWsUrl, parseLogMessage } from '../api/logs'
import type { LogEntry } from '../types/logs'

const MAX_ENTRIES = 10000

export const useLogsStore = defineStore('logs', () => {
  const entries = ref<LogEntry[]>([])
  const connected = ref(false)
  const connecting = ref(false)
  const filterLevel = ref<string>('')
  const keyword = ref('')
  const autoScroll = ref(true)
  const paused = ref(false)

  let socket: WebSocket | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let manualClosed = false

  const filteredEntries = computed(() => {
    const lv = filterLevel.value
    const kw = keyword.value.trim().toLowerCase()
    return entries.value.filter((e) => {
      if (lv && e.level !== lv) return false
      if (kw) {
        const hay = `${e.ts} ${e.level} ${e.logger} ${e.message} ${e.trace_id}`.toLowerCase()
        if (!hay.includes(kw)) return false
      }
      return true
    })
  })

  function push(entry: LogEntry): void {
    entries.value.push(entry)
    if (entries.value.length > MAX_ENTRIES) {
      entries.value.splice(0, entries.value.length - MAX_ENTRIES)
    }
  }

  function clear(): void {
    entries.value = []
  }

  function scheduleReconnect(): void {
    if (manualClosed || reconnectTimer) return
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      connect()
    }, 2000)
  }

  function connect(): void {
    if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
      return
    }
    manualClosed = false
    connecting.value = true
    connected.value = false

    const ws = new WebSocket(buildLogWsUrl())
    socket = ws

    ws.onopen = () => {
      connecting.value = false
      connected.value = true
    }
    ws.onmessage = (event) => {
      const entry = parseLogMessage(String(event.data))
      if (entry) push(entry)
    }
    ws.onclose = () => {
      connecting.value = false
      connected.value = false
      if (socket === ws) socket = null
      scheduleReconnect()
    }
    ws.onerror = () => {
      // onclose 会随后触发并安排重连
      ws.close()
    }
  }

  function disconnect(): void {
    manualClosed = true
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    if (socket) {
      socket.onclose = null
      socket.close()
      socket = null
    }
    connecting.value = false
    connected.value = false
  }

  return {
    entries,
    connected,
    connecting,
    filterLevel,
    keyword,
    autoScroll,
    paused,
    filteredEntries,
    connect,
    disconnect,
    clear,
  }
})
