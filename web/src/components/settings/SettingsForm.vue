<script setup lang="ts">
import { ElMessage } from 'element-plus'

import { useSettingsStore } from '../../stores/settings'
import type { SettingItem } from '../../types/settings'
import SettingField from './SettingField.vue'

defineProps<{
  items: SettingItem[]
}>()

const store = useSettingsStore()

async function onClear(item: SettingItem): Promise<void> {
  try {
    await store.clearField(item.key)
    ElMessage.success(`已恢复默认：${item.label}`)
  } catch {
    // 错误已写入 store.error，由页面统一提示
  }
}
</script>

<template>
  <el-form label-position="top" class="settings-form">
    <el-form-item
      v-for="item in items"
      :key="item.key"
      :label="item.label"
      class="field-item"
    >
      <div class="field-row">
        <div class="field-control">
          <SettingField
            :item="item"
            :model-value="store.draft[item.key]"
            @update:model-value="store.setDraft(item.key, $event)"
          />
        </div>
        <el-tooltip content="清除该项，恢复默认值" :disabled="store.saving">
          <el-button
            link
            type="danger"
            :disabled="store.saving"
            @click="onClear(item)"
          >
            恢复默认
          </el-button>
        </el-tooltip>
      </div>
      <div class="field-meta">
        <code>{{ item.env }}</code>
        <el-tag
          v-if="store.isFieldDirty(item, store.draft[item.key])"
          size="small"
          type="warning"
          effect="plain"
        >
          已修改
        </el-tag>
      </div>
    </el-form-item>
  </el-form>
</template>

<style scoped>
.settings-form {
  padding-right: 8px;
}

.field-item {
  margin-bottom: 20px;
}

.field-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  width: 100%;
}

.field-control {
  flex: 1;
  min-width: 0;
}

.field-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
}

.field-meta code {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
