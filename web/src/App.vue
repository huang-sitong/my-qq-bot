<script setup lang="ts">
import { useRoute } from 'vue-router'

const route = useRoute()
</script>

<template>
  <div class="app-shell">
    <nav class="top-nav">
      <span class="brand">QQ Bot 控制台</span>
      <router-link to="/settings" class="nav-link" :class="{ active: route.path === '/settings' }">
        设置中心
      </router-link>
      <router-link to="/logs" class="nav-link" :class="{ active: route.path === '/logs' }">
        日志控制台
      </router-link>
    </nav>
    <main class="app-main">
      <router-view />
    </main>
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

.top-nav {
  display: flex;
  align-items: center;
  gap: 20px;
  padding: 0 24px;
  height: 48px;
  background: var(--el-bg-color);
  border-bottom: 1px solid var(--el-border-color-light);
  position: sticky;
  top: 0;
  z-index: 10;
}

.brand {
  font-weight: 600;
  font-size: 15px;
  margin-right: 12px;
}

.nav-link {
  color: var(--el-text-color-secondary);
  text-decoration: none;
  font-size: 14px;
  padding: 4px 2px;
  border-bottom: 2px solid transparent;
}

.nav-link:hover {
  color: var(--el-color-primary);
}

.nav-link.active {
  color: var(--el-color-primary);
  border-bottom-color: var(--el-color-primary);
}

.app-main {
  flex: 1;
  display: flex;
  flex-direction: column;
}
</style>

