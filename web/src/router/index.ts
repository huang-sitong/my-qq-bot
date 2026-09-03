import { createRouter, createWebHistory } from 'vue-router'

import SettingsPage from '../views/SettingsPage.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/settings' },
    { path: '/settings', name: 'settings', component: SettingsPage },
  ],
})

export default router
