<script setup lang="ts">
import { computed } from 'vue'

import type { SettingItem } from '../../types/settings'

const props = defineProps<{
  item: SettingItem
  modelValue: string
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
}>()

const placeholder = computed(() =>
  props.item.has_value ? '***（留空则不修改）' : '未设置',
)
</script>

<template>
  <el-input
    :model-value="modelValue"
    type="password"
    show-password
    :placeholder="placeholder"
    autocomplete="new-password"
    @update:model-value="emit('update:modelValue', $event)"
  />
</template>
