/** 日志条目类型，与后端 `log/web.jsonl`（LogHub 广播）字段对齐。 */

export interface LogEntry {
  ts: string
  level: string
  logger: string
  message: string
  trace_id: string
}
