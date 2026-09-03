<script setup lang="ts">
import { computed } from 'vue'

import type { SettingItem } from '../../types/settings'
import SecretInput from './SecretInput.vue'

const props = defineProps<{
  item: SettingItem
  modelValue: unknown
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: unknown): void
}>()

/** 字符串类字段（含敏感字段）的可编辑值。 */
const stringValue = computed<string>(() => {
  return typeof props.modelValue === 'string' ? props.modelValue : ''
})

/** 数字类字段的可编辑值。 */
const numberValue = computed<number>(() => {
  const value = props.modelValue
  const num = typeof value === 'number' ? value : Number(value ?? 0)
  return Number.isFinite(num) ? num : 0
})

/** list 字段以逗号分隔文本呈现，提交时再转回数组。 */
const listText = computed<string>({
  get() {
    const value = props.modelValue
    if (Array.isArray(value)) return value.join(', ')
    if (typeof value === 'string') return value
    return ''
  },
  set(value: string) {
    const parts = value
      .split(',')
      .map((part) => part.trim())
      .filter(Boolean)
    emit('update:modelValue', parts)
  },
})
</script>

<template>
  <SecretInput
    v-if="item.secret"
    :item="item"
    :model-value="stringValue"
    @update:model-value="emit('update:modelValue', $event)"
  />
  <el-switch
    v-else-if="item.type === 'boolean'"
    :model-value="Boolean(modelValue)"
    @update:model-value="emit('update:modelValue', $event)"
  />
  <el-input-number
    v-else-if="item.type === 'integer' || item.type === 'number'"
    :model-value="numberValue"
    :precision="item.type === 'number' ? undefined : 0"
    :step="item.type === 'number' ? 0.1 : 1"
    :controls="true"
    class="field-number"
    @update:model-value="emit('update:modelValue', $event)"
  />
  <el-input
    v-else-if="item.type === 'list'"
    :model-value="listText"
    placeholder="多个值用英文逗号分隔"
    @update:model-value="listText = $event"
  />
  <el-input
    v-else
    :model-value="stringValue"
    :placeholder="item.has_value ? '' : '未设置'"
    @update:model-value="emit('update:modelValue', $event)"
  />
</template>

<style scoped>
.field-number {
  width: 100%;
}
</style>
