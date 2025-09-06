// src/router/index.js
import { createRouter, createWebHistory } from 'vue-router'
import DashboardView from '../views/DashboardView.vue'
import MonitorBuilderView from '../views/MonitorBuilderView.vue'
import ManageDeviceView from '../views/ManageDeviceView.vue'
import CredentialsView from '../views/CredentialsView.vue'
import MonitorDetailView from '../views/MonitorDetailView.vue'
import ChannelsView from '../views/ChannelsView.vue'
import VpnsView from '../views/VpnsView.vue'
import LoginView from '../views/LoginView.vue'
import { getSession } from '@/lib/supabase'

const routes = [
  // Login oculta el chrome
  { path: '/login', name: 'login', component: LoginView, meta: { hideChrome: true } },

  { path: '/', name: 'dashboard', component: DashboardView, meta: { requiresAuth: true } },
  {
    path: '/monitor-builder',
    name: 'monitor-builder',
    component: MonitorBuilderView,
    meta: { requiresAuth: true },
  },
  {
    path: '/devices',
    name: 'manage-devices',
    component: ManageDeviceView,
    meta: { requiresAuth: true },
  },
  {
    path: '/credentials',
    name: 'credentials',
    component: CredentialsView,
    meta: { requiresAuth: true },
  },
  {
    path: '/sensor/:id',
    name: 'sensor-detail',
    component: MonitorDetailView,
    meta: { requiresAuth: true },
  },
  { path: '/channels', name: 'channels', component: ChannelsView, meta: { requiresAuth: true } },
  { path: '/vpns', name: 'vpns', component: VpnsView, meta: { requiresAuth: true } },

  // Fallback: lo mandamos al dashboard (el guard decidirá si hay que ir a login)
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
  scrollBehavior() {
    return { top: 0 }
  },
})

/**
 * Pequeña espera para cubrir el caso donde Supabase todavía está
 * restaurando la sesión desde storage al montar la app.
 */
async function waitSupabaseWarmup(ms = 0) {
  if (ms > 0) await new Promise((r) => setTimeout(r, ms))
}

router.beforeEach(async (to) => {
  await waitSupabaseWarmup(0) // subí a 50–100ms si ves flasheos raros

  // OJO: getSession() devuelve { data, error }, no la sesión directa.
  const { data } = await getSession()
  const hasSession = !!data?.session

  // Si ya estoy logueado e intento ir a /login → redirigir a destino (o /)
  if (to.name === 'login' && hasSession) {
    const dest =
      typeof to.query.redirect === 'string' && to.query.redirect ? to.query.redirect : '/'
    if (to.fullPath === dest) return true
    return { path: dest }
  }

  // Si la ruta requiere auth y no hay sesión → mandar a login con redirect
  if (to.meta?.requiresAuth && !hasSession) {
    if (to.name === 'login') return true // evitar loop
    return { name: 'login', query: { redirect: to.fullPath } }
  }

  return true
})

export default router
