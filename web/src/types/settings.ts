/** 设置项 API 相关类型，与后端 `src/bot/package/api/schemas.py` 对齐。 */

export type SettingType = 'boolean' | 'integer' | 'number' | 'string' | 'list'

export interface SettingItem {
  key: string
  env: string
  value: unknown
  type: SettingType
  group: string
  label: string
  secret: boolean
  has_value: boolean
}

export interface SettingsResponse {
  settings: SettingItem[]
  loaded_from: string
  count: number
}

/** PUT /api/settings 请求体：字段名 -> 新值（可只传部分字段）。 */
export type SettingsUpdate = Record<string, unknown>
