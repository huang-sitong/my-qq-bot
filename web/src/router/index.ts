import { createRouter, createWebHistory } from 'vue-router'

import LogConsolePage from '../views/LogConsolePage.vue'
import SettingsPage from '../views/SettingsPage.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/settings' },
    { path: '/settings', name: 'settings', component: SettingsPage },
    { path: '/logs', name: 'logs', component: LogConsolePage },
  ],
})

export default router
