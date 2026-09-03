/** 设置 API 客户端：封装 GET/PUT /api/settings。 */

import axios from 'axios'

import type { SettingsResponse, SettingsUpdate } from '../types/settings'

const http = axios.create({
  // 可经 VITE_API_BASE 覆盖（默认走 Vite dev proxy 的 /api）
  baseURL: import.meta.env.VITE_API_BASE ?? '',
  timeout: 20000,
})

export async function getSettings(): Promise<SettingsResponse> {
  const { data } = await http.get<SettingsResponse>('/api/settings')
  return data
}

export async function updateSettings(
  payload: SettingsUpdate,
): Promise<SettingsResponse> {
  const { data } = await http.put<SettingsResponse>('/api/settings', payload)
  return data
}

export default http
