import { createRouter, createWebHistory } from 'vue-router'
import DashboardView from '../views/DashboardView.vue'
import MonitorBuilderView from '../views/MonitorBuilderView.vue'
import ManageDeviceView from '../views/ManageDeviceView.vue' // <-- Renombrado
import CredentialsView from '../views/CredentialsView.vue'
import MonitorDetailView from '../views/MonitorDetailView.vue'
import ChannelsView from '../views/ChannelsView.vue'
import VpnsView from '../views/VpnsView.vue' // <-- Nueva vista

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', name: 'dashboard', component: DashboardView },
    { path: '/monitor-builder', name: 'monitor-builder', component: MonitorBuilderView },
    { path: '/devices', name: 'manage-devices', component: ManageDeviceView }, // <-- Ruta actualizada
    { path: '/credentials', name: 'credentials', component: CredentialsView },
    { path: '/sensor/:id', name: 'sensor-detail', component: MonitorDetailView },
    { path: '/channels', name: 'channels', component: ChannelsView },
    { path: '/vpns', name: 'vpns', component: VpnsView }, // <-- Nueva ruta
  ],
})

export default router
