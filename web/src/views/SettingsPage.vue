<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import SettingsForm from '../components/settings/SettingsForm.vue'
import { useSettingsStore } from '../stores/settings'

const store = useSettingsStore()

const activeGroup = ref('')

const activeItems = computed(() => {
  const group = store.groups.find((g) => g.group === activeGroup.value)
  return group ? group.items : []
})

// 加载完成后把激活分组落到第一个分组
watch(
  () => store.groups.map((g) => g.group),
  (groups) => {
    if (!groups.includes(activeGroup.value) && groups.length > 0) {
      activeGroup.value = groups[0]
    }
  },
  { immediate: true },
)

onMounted(() => {
  void store.load().catch(() => {
    ElMessage.error('加载设置失败，请确认后端已启动')
  })
})

async function onSave(): Promise<void> {
  try {
    await store.save()
    ElMessage.success('设置已保存（重启后生效）')
  } catch {
    // 错误由 store.error 展示
  }
}

async function onRefresh(): Promise<void> {
  try {
    await store.load()
    ElMessage.success('已重新加载')
  } catch {
    // 错误由 store.error 展示
  }
}

function onResetAll(): void {
  store.resetAll()
  ElMessage.info('已撤销所有未保存的修改')
}
</script>

<template>
  <div v-loading="store.loading" class="page" element-loading-text="加载中…">
    <header class="page-header">
      <div class="header-left">
        <h1>设置中心</h1>
        <span v-if="store.loadedFrom" class="source">
          配置文件：<code>{{ store.loadedFrom }}</code>
        </span>
        <el-tag v-if="store.dirty" type="warning" effect="plain" size="small">
          有 {{ store.dirtyCount }} 项未保存的修改
        </el-tag>
      </div>
      <div class="header-actions">
        <el-button :disabled="store.saving" @click="onRefresh">刷新</el-button>
        <el-button
          :disabled="store.saving || !store.dirty"
          @click="onResetAll"
        >
          撤销修改
        </el-button>
        <el-button
          type="primary"
          :loading="store.saving"
          :disabled="!store.dirty"
          @click="onSave"
        >
          保存
        </el-button>
      </div>
    </header>

    <el-alert
      v-if="store.error"
      :title="store.error"
      type="error"
      show-icon
      :closable="true"
      class="error-banner"
    />

    <div v-if="store.groups.length" class="page-body">
      <el-container class="body-container">
        <el-aside width="180px" class="group-aside">
          <el-menu :default-active="activeGroup" @select="activeGroup = $event">
            <el-menu-item v-for="g in store.groups" :key="g.group" :index="g.group">
              {{ g.group }}
              <span v-if="g.items.length" class="group-count">{{ g.items.length }}</span>
            </el-menu-item>
          </el-menu>
        </el-aside>
        <el-main class="group-main">
          <SettingsForm :items="activeItems" />
        </el-main>
      </el-container>
    </div>

    <el-empty
      v-else-if="!store.loading && !store.error"
      description="暂无设置项"
    />
  </div>
</template>

<style scoped>
.page {
  min-height: calc(100vh - 48px);
  background: var(--el-bg-color-page);
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  padding: 20px 28px;
  background: var(--el-bg-color);
  border-bottom: 1px solid var(--el-border-color-light);
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.header-left h1 {
  margin: 0;
  font-size: 20px;
}

.source {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.source code {
  font-size: 12px;
}

.header-actions {
  display: flex;
  gap: 8px;
}

.error-banner {
  margin: 16px 28px 0;
}

.page-body {
  padding: 16px 28px;
}

.body-container {
  height: 100%;
}

.group-aside {
  border-right: 1px solid var(--el-border-color-light);
  background: var(--el-bg-color);
  border-radius: 6px;
}

.group-aside :deep(.el-menu) {
  border-right: none;
}

.group-count {
  margin-left: auto;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.group-main {
  padding: 20px 28px;
}
</style>
