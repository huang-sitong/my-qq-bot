<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { useLogsStore } from '../stores/logs'

const store = useLogsStore()

const LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'] as const

const listEl = ref<HTMLElement | null>(null)
const atBottom = ref(true)

const visibleEntries = computed(() => store.filteredEntries)

const statusText = computed(() => {
  if (store.connecting) return '连接中…'
  if (store.connected) return '实时'
  return '已断开'
})

const statusType = computed(() => {
  if (store.connected) return 'success'
  if (store.connecting) return 'warning'
  return 'danger'
})

function levelTagType(level: string): string {
  switch (level) {
    case 'DEBUG':
      return 'info'
    case 'INFO':
      return 'primary'
    case 'WARNING':
      return 'warning'
    case 'ERROR':
      return 'danger'
    default:
      return 'danger'
  }
}

function onScroll(): void {
  const el = listEl.value
  if (!el) return
  const distance = el.scrollHeight - el.scrollTop - el.clientHeight
  atBottom.value = distance < 40
}

async function scrollToBottom(): Promise<void> {
  const el = listEl.value
  if (!el) return
  el.scrollTop = el.scrollHeight
}

watch(
  () => store.entries.length,
  async () => {
    if (store.autoScroll && !store.paused && atBottom.value) {
      await nextTick()
      await scrollToBottom()
    }
  },
)

onMounted(() => {
  store.connect()
})

onBeforeUnmount(() => {
  store.disconnect()
})
</script>

<template>
  <div class="page">
    <header class="page-header">
      <div class="header-left">
        <h1>日志控制台</h1>
        <el-tag :type="statusType" effect="plain" size="small">{{ statusText }}</el-tag>
        <span class="count">{{ visibleEntries.length }} 条</span>
      </div>
      <div class="header-actions">
        <el-select
          v-model="store.filterLevel"
          placeholder="全部级别"
          clearable
          size="small"
          style="width: 120px"
        >
          <el-option v-for="lv in LEVELS" :key="lv" :label="lv" :value="lv" />
        </el-select>
        <el-input
          v-model="store.keyword"
          placeholder="关键字过滤"
          clearable
          size="small"
          style="width: 180px"
        />
        <el-button size="small" @click="store.paused = !store.paused">
          {{ store.paused ? '继续' : '暂停' }}
        </el-button>
        <el-button size="small" @click="store.clear">清屏</el-button>
        <el-switch
          v-model="store.autoScroll"
          active-text="自动滚动"
          size="small"
        />
      </div>
    </header>

    <div ref="listEl" class="log-list" @scroll="onScroll">
      <el-empty v-if="!visibleEntries.length" description="暂无日志" />
      <div
        v-for="(entry, i) in visibleEntries"
        :key="i"
        class="log-row"
        :class="`level-${entry.level.toLowerCase()}`"
      >
        <span class="log-ts">{{ entry.ts }}</span>
        <el-tag :type="levelTagType(entry.level) as any" size="small" effect="dark" class="log-level">
          {{ entry.level }}
        </el-tag>
        <span class="log-trace" :title="`trace_id: ${entry.trace_id}`">{{ entry.trace_id }}</span>
        <span class="log-logger">{{ entry.logger }}</span>
        <span class="log-message">{{ entry.message }}</span>
      </div>
      <div v-if="store.paused" class="pause-hint">
        <el-tag type="info" effect="plain" size="small">已暂停，新日志继续缓冲</el-tag>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page {
  height: calc(100vh - 48px);
  display: flex;
  flex-direction: column;
  background: var(--el-bg-color-page);
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  padding: 16px 24px;
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

.count {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.log-list {
  flex: 1;
  overflow-y: auto;
  padding: 12px 24px 24px;
  font-family: ui-monospace, SFMono-Regular, Consolas, 'Liberation Mono', monospace;
  font-size: 12px;
  line-height: 1.6;
}

.log-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 2px 4px;
  border-radius: 4px;
  white-space: pre-wrap;
  word-break: break-all;
}

.log-row:hover {
  background: var(--el-fill-color-light);
}

.log-ts {
  color: var(--el-text-color-secondary);
  flex-shrink: 0;
}

.log-level {
  flex-shrink: 0;
  min-width: 60px;
  text-align: center;
}

.log-trace {
  color: var(--el-text-color-secondary);
  flex-shrink: 0;
}

.log-logger {
  color: #409eff;
  flex-shrink: 0;
}

.log-message {
  color: var(--el-text-color-primary);
  flex: 1;
}

.level-debug .log-message {
  color: var(--el-text-color-secondary);
}

.level-warning .log-message {
  color: #e6a23c;
}

.level-error .log-message,
.level-critical .log-message {
  color: #f56c6c;
  font-weight: 600;
}

.pause-hint {
  padding: 8px;
  text-align: center;
}
</style>
