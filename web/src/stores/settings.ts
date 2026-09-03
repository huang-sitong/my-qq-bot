/** 设置中心 Pinia store：加载、草稿、脏标记、保存。 */

import axios from 'axios'
import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { getSettings, updateSettings } from '../api/settings'
import type {
  SettingItem,
  SettingsResponse,
  SettingsUpdate,
} from '../types/settings'

/** 后端对敏感字段的掩码占位符（与 router.py 的 _MASK_PLACEHOLDER 对齐）。 */
export const MASK_PLACEHOLDER = '***'

function extractError(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      return detail
        .map((item) => {
          const loc = (item?.loc ?? []).slice(1).join('.')
          const msg = item?.msg ?? ''
          return [loc, msg].filter(Boolean).join(' ')
        })
        .join('; ')
    }
    if (!error.response) return `无法连接后端：${error.message}`
    return error.message
  }
  if (error instanceof Error) return error.message
  return String(error)
}

export const useSettingsStore = defineStore('settings', () => {
  const items = ref<SettingItem[]>([])
  const draft = ref<Record<string, unknown>>({})
  const loadedFrom = ref('')
  const loading = ref(false)
  const saving = ref(false)
  const error = ref<string | null>(null)

  const itemByKey = computed(() => {
    const map = new Map<string, SettingItem>()
    for (const item of items.value) map.set(item.key, item)
    return map
  })

  /** 按后端分组名分组，保持后端返回顺序。 */
  const groups = computed(() => {
    const order: string[] = []
    const map = new Map<string, SettingItem[]>()
    for (const item of items.value) {
      const list = map.get(item.group)
      if (list) {
        list.push(item)
      } else {
        map.set(item.group, [item])
        order.push(item.group)
      }
    }
    return order.map((group) => ({ group, items: map.get(group)! }))
  })

  /** 字段可编辑的初始值：掩码敏感字段从空开始（留空 = 不修改）。 */
  function initialDraftValue(item: SettingItem): unknown {
    if (item.secret && item.has_value) return ''
    return item.value
  }

  /** 判断某个字段相对后端原值是否有改动。 */
  function isFieldDirty(item: SettingItem, value: unknown): boolean {
    if (item.secret && item.has_value) {
      // 掩码敏感字段：留空视为不修改
      return value !== '' && value !== null && value !== undefined
    }
    return !Object.is(value, item.value)
  }

  const dirty = computed(() =>
    items.value.some((item) => isFieldDirty(item, draft.value[item.key])),
  )

  const dirtyCount = computed(
    () =>
      items.value.filter((item) => isFieldDirty(item, draft.value[item.key]))
        .length,
  )

  function setDraft(key: string, value: unknown): void {
    draft.value[key] = value
  }

  /** 撤销单个字段的本地修改（不请求后端）。 */
  function resetDraft(key: string): void {
    const item = itemByKey.value.get(key)
    if (item) draft.value[key] = initialDraftValue(item)
  }

  /** 撤销所有本地修改（不请求后端）。 */
  function resetAll(): void {
    for (const item of items.value) {
      draft.value[item.key] = initialDraftValue(item)
    }
  }

  function applyResponse(resp: SettingsResponse): void {
    items.value = resp.settings
    loadedFrom.value = resp.loaded_from
    const next: Record<string, unknown> = {}
    for (const item of resp.settings) next[item.key] = initialDraftValue(item)
    draft.value = next
  }

  async function load(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      applyResponse(await getSettings())
    } catch (err) {
      error.value = extractError(err)
      throw err
    } finally {
      loading.value = false
    }
  }

  /** 收集所有发生改动的字段，构造 PUT 请求体。 */
  function buildPayload(): SettingsUpdate {
    const payload: SettingsUpdate = {}
    for (const item of items.value) {
      const value = draft.value[item.key]
      if (isFieldDirty(item, value)) payload[item.key] = value
    }
    return payload
  }

  /** 保存全部改动。 */
  async function save(): Promise<SettingsResponse> {
    const payload = buildPayload()
    if (Object.keys(payload).length === 0) {
      return {
        settings: items.value,
        loaded_from: loadedFrom.value,
        count: items.value.length,
      }
    }
    saving.value = true
    error.value = null
    try {
      const resp = await updateSettings(payload)
      applyResponse(resp)
      return resp
    } catch (err) {
      error.value = extractError(err)
      throw err
    } finally {
      saving.value = false
    }
  }

  /** 清除单个字段（发 null），由后端恢复默认值。 */
  async function clearField(key: string): Promise<void> {
    saving.value = true
    error.value = null
    try {
      applyResponse(await updateSettings({ [key]: null }))
    } catch (err) {
      error.value = extractError(err)
      throw err
    } finally {
      saving.value = false
    }
  }

  return {
    items,
    draft,
    loadedFrom,
    loading,
    saving,
    error,
    itemByKey,
    groups,
    dirty,
    dirtyCount,
    setDraft,
    resetDraft,
    resetAll,
    load,
    save,
    clearField,
    isFieldDirty,
    MASK_PLACEHOLDER,
  }
})
