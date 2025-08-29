<script setup>
import { ref, onMounted, onUnmounted, computed } from 'vue'
import axios from 'axios'
import { useRouter } from 'vue-router'

const router = useRouter()

// ===== Config dinámico (API y WS) =====
const API_PROTO = window.location.protocol // 'http:' | 'https:'
const API_HOST = window.location.hostname // ej: 'localhost' | '127.0.0.1'
const API_PORT = 8000
const API_BASE = `${API_PROTO}//${API_HOST}:${API_PORT}`

const WS_PROTO = API_PROTO === 'https:' ? 'wss' : 'ws'
const WS_URL = `${WS_PROTO}://${API_HOST}:${API_PORT}/ws`

// Axios instance
const http = axios.create({
  baseURL: API_BASE,
  timeout: 15000,
})

const monitors = ref([])
const liveSensorStatus = ref({})
const monitorToDelete = ref(null)
const sensorDetailsToShow = ref(null)

let socket = null
let wsRetryTimer = null

// --- FUNCIÓN HELPER PARA FORMATEAR TRÁFICO ---
function formatBitrate(bits) {
  const n = Number(bits)
  if (!Number.isFinite(n) || n <= 0) return '0 Kbps'
  const kbps = n / 1000
  if (kbps < 1000) return `${kbps.toFixed(1)} Kbps`
  const mbps = kbps / 1000
  return `${mbps.toFixed(1)} Mbps`
}

// --- WebSocket con reconexión y URL dinámica ---
function connectWebSocket() {
  // Evita abrir más de una conexión
  if (
    socket &&
    (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)
  ) {
    return
  }

  socket = new WebSocket(WS_URL)

  socket.onopen = () => {
    if (wsRetryTimer) {
      clearTimeout(wsRetryTimer)
      wsRetryTimer = null
    }
  }

  socket.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)
      // data esperado: { sensor_id, sensor_type, ... }
      liveSensorStatus.value[data.sensor_id] = data
    } catch (e) {
      console.error('WS message parse error:', e)
    }
  }

  socket.onclose = () => {
    wsRetryTimer = setTimeout(connectWebSocket, 5000)
  }

  socket.onerror = (error) => {
    console.error('Error de WebSocket:', error)
    // Cierre “seguro” sin try/catch vacío
    if (
      socket &&
      socket.readyState !== WebSocket.CLOSING &&
      socket.readyState !== WebSocket.CLOSED
    ) {
      socket.close()
    }
  }
}

onMounted(() => {
  fetchAllMonitors()
  connectWebSocket()
})

onUnmounted(() => {
  if (wsRetryTimer) clearTimeout(wsRetryTimer)
  if (socket && socket.readyState !== WebSocket.CLOSED) {
    socket.close()
  }
})

async function fetchAllMonitors() {
  try {
    const { data } = await http.get('/api/monitors')
    monitors.value = Array.isArray(data) ? data : []

    // Inicializar estados "pending" para sensores sin datos aún
    monitors.value.forEach((m) => {
      ;(m.sensors || []).forEach((s) => {
        if (!liveSensorStatus.value[s.id]) {
          liveSensorStatus.value[s.id] = { status: 'pending' }
        }
      })
    })
  } catch (err) {
    console.error('Error fetching monitors:', err)
    monitors.value = []
  }
}

function requestDeleteMonitor(monitor, event) {
  if (event?.stopPropagation) event.stopPropagation()
  monitorToDelete.value = monitor
}

async function confirmDeleteMonitor() {
  if (!monitorToDelete.value) return
  try {
    await http.delete(`/api/monitors/${monitorToDelete.value.monitor_id}`)
    monitors.value = monitors.value.filter((m) => m.monitor_id !== monitorToDelete.value.monitor_id)
  } catch (err) {
    console.error('Error deleting monitor:', err)
  } finally {
    monitorToDelete.value = null
  }
}

function getOverallCardStatus(monitor) {
  if (!monitor.sensors || monitor.sensors.length === 0) return false
  return monitor.sensors.some((sensor) => {
    const status = liveSensorStatus.value[sensor.id]?.status
    return (
      status === 'timeout' ||
      status === 'error' ||
      status === 'high_latency' ||
      status === 'link_down'
    )
  })
}

function getStatusClass(status) {
  if (status === 'timeout' || status === 'error' || status === 'link_down') return 'status-timeout'
  if (status === 'high_latency') return 'status-high-latency'
  if (status === 'ok' || status === 'link_up') return 'status-ok'
  return 'status-pending'
}

function goToSensorDetail(sensorId) {
  router.push(`/sensor/${sensorId}`)
}

function showSensorDetails(sensor, event) {
  if (event?.stopPropagation) event.stopPropagation()
  sensorDetailsToShow.value = sensor
}

// Formatea la configuración para mostrarla en el modal
const formattedSensorConfig = computed(() => {
  if (!sensorDetailsToShow.value) return []
  const config = sensorDetailsToShow.value.config || {}
  const details = []
  for (const key in config) {
    if (Object.prototype.hasOwnProperty.call(config, key) && key !== 'alerts') {
      details.push({ key, value: config[key] })
    }
  }
  if (Array.isArray(config.alerts) && config.alerts.length > 0) {
    details.push({ key: 'Alertas Configuradas', value: config.alerts.length })
  }
  return details
})
</script>

<template>
  <div>
    <!-- Modal eliminar monitor -->
    <div v-if="monitorToDelete" class="modal-overlay" @click.self="monitorToDelete = null">
      <div class="modal-content">
        <h3>Confirmar Eliminación</h3>
        <p>
          ¿Seguro que quieres eliminar el monitor para
          <strong>{{ monitorToDelete.client_name }}</strong
          >?
        </p>
        <div class="modal-actions">
          <button @click="monitorToDelete = null" class="btn-secondary">Cancelar</button>
          <button @click="confirmDeleteMonitor" class="btn-danger">Eliminar</button>
        </div>
      </div>
    </div>

    <!-- Modal detalles de sensor -->
    <div v-if="sensorDetailsToShow" class="modal-overlay" @click.self="sensorDetailsToShow = null">
      <div class="modal-content">
        <h3>Detalles del Sensor: {{ sensorDetailsToShow.name }}</h3>
        <table class="details-table">
          <tbody>
            <tr v-for="item in formattedSensorConfig" :key="item.key">
              <th>{{ item.key }}</th>
              <td>{{ item.value }}</td>
            </tr>
          </tbody>
        </table>
        <div class="modal-actions">
          <button @click="sensorDetailsToShow = null" class="btn-secondary">Cerrar</button>
        </div>
      </div>
    </div>

    <main class="dashboard-grid">
      <div v-if="monitors.length === 0" class="empty-state">
        <h3>No hay monitores activos</h3>
        <p>
          Ve a <router-link to="/monitor-builder" class="link">Añadir Monitor</router-link> para
          crear tu primera tarjeta.
        </p>
      </div>

      <div
        v-for="monitor in monitors"
        :key="monitor.monitor_id"
        :class="['monitor-card', { 'status-alert': getOverallCardStatus(monitor) }]"
      >
        <div class="card-header">
          <h3>{{ monitor.client_name }}</h3>
          <span class="device-info-header">{{ monitor.ip_address }}</span>
          <span v-if="getOverallCardStatus(monitor)" class="alert-icon">⚠️</span>
          <button @click="requestDeleteMonitor(monitor, $event)" class="remove-btn">×</button>
        </div>

        <div class="card-body">
          <div class="sensors-container">
            <div v-if="!monitor.sensors || monitor.sensors.length === 0" class="no-sensors">
              Sin sensores configurados.
            </div>

            <div
              v-else
              v-for="sensor in monitor.sensors"
              :key="sensor.id"
              class="sensor-row"
              @click="goToSensorDetail(sensor.id)"
            >
              <span class="sensor-name">{{ sensor.name }}</span>

              <div class="sensor-status-group">
                <div
                  class="sensor-value"
                  :class="getStatusClass(liveSensorStatus[sensor.id]?.status)"
                >
                  <template v-if="sensor.sensor_type === 'ping'">
                    {{
                      liveSensorStatus[sensor.id]?.status === 'pending'
                        ? '...'
                        : `${liveSensorStatus[sensor.id]?.latency_ms ?? '—'} ms`
                    }}
                  </template>

                  <template v-else-if="sensor.sensor_type === 'ethernet'">
                    <div class="ethernet-data">
                      <span class="ethernet-status">
                        {{ (liveSensorStatus[sensor.id]?.status || 'pending').replace('_', ' ') }}
                        <span
                          class="ethernet-speed"
                          v-if="liveSensorStatus[sensor.id]?.status === 'link_up'"
                        >
                          ({{ liveSensorStatus[sensor.id]?.speed || '—' }})
                        </span>
                      </span>
                      <span
                        class="ethernet-traffic"
                        v-if="liveSensorStatus[sensor.id]?.status !== 'pending'"
                      >
                        ↓ {{ formatBitrate(liveSensorStatus[sensor.id]?.rx_bitrate) }} / ↑
                        {{ formatBitrate(liveSensorStatus[sensor.id]?.tx_bitrate) }}
                      </span>
                    </div>
                  </template>

                  <template v-else>
                    {{ liveSensorStatus[sensor.id]?.status || 'pending' }}
                  </template>
                </div>

                <button @click="showSensorDetails(sensor, $event)" class="details-btn">⋮</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </main>
  </div>
</template>

<style scoped>
.dashboard-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 1.5rem;
}
.monitor-card {
  background-color: var(--surface-color);
  border-radius: 12px;
  border: 1px solid var(--primary-color);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.monitor-card.status-alert {
  border-color: var(--secondary-color);
  box-shadow: 0 0 8px var(--secondary-color);
}
.card-header {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.75rem 1rem;
  background-color: var(--primary-color);
}
.card-header h3 {
  flex-grow: 1;
  margin: 0;
  font-size: 1.1rem;
  color: white;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.device-info-header {
  font-size: 0.85rem;
  color: var(--gray);
  flex-shrink: 0;
}
.alert-icon {
  font-size: 1.25rem;
}
.remove-btn {
  background: none;
  border: none;
  color: var(--gray);
  font-size: 1.5rem;
  cursor: pointer;
}
.card-body {
  padding: 1rem;
  flex-grow: 1;
}
.sensors-container {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.sensor-row {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 1rem;
  align-items: center;
  background-color: var(--bg-color);
  padding: 0.6rem 0.8rem;
  border-radius: 6px;
  cursor: pointer;
  transition: background-color 0.2s;
}
.sensor-row:hover {
  background-color: var(--primary-color);
}
.sensor-name {
  font-size: 0.9rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sensor-value {
  text-align: right;
}
.ethernet-data {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
}
.ethernet-status {
  font-weight: bold;
  font-size: 0.95rem;
  text-transform: capitalize;
}
.ethernet-speed {
  font-weight: normal;
  color: var(--gray);
  font-size: 0.85rem;
  margin-left: 0.25rem;
}
.ethernet-traffic {
  font-size: 0.8rem;
  color: var(--gray);
  white-space: nowrap;
}
.status-ok {
  color: var(--green);
}
.status-high-latency {
  color: #facc15;
}
.status-timeout {
  color: var(--secondary-color);
}
.status-pending {
  color: var(--gray);
}
.empty-state {
  grid-column: 1 / -1;
  text-align: center;
  background-color: var(--surface-color);
  padding: 4rem;
  border-radius: 12px;
}
.empty-state .link {
  color: var(--blue);
  text-decoration: underline;
  cursor: pointer;
}
.no-sensors {
  color: var(--gray);
  font-style: italic;
  text-align: center;
  padding: 1rem;
}
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background-color: rgba(0, 0, 0, 0.7);
  display: flex;
  justify-content: center;
  align-items: center;
  z-index: 2000;
}
.modal-content {
  background-color: var(--surface-color);
  padding: 2rem;
  border-radius: 12px;
  max-width: 500px;
  width: 90%;
  text-align: center;
}
.modal-actions {
  display: flex;
  justify-content: center;
  gap: 1rem;
  margin-top: 1.5rem;
}
.modal-actions button {
  padding: 0.6rem 1.2rem;
  border: none;
  border-radius: 6px;
  font-weight: bold;
  cursor: pointer;
}
.btn-secondary {
  background-color: var(--primary-color);
  color: white;
}
.btn-danger {
  background-color: var(--secondary-color);
  color: white;
}

.sensor-status-group {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.details-btn {
  background: none;
  border: none;
  color: var(--gray);
  font-size: 1.5rem;
  font-weight: bold;
  cursor: pointer;
  border-radius: 50%;
  width: 30px;
  height: 30px;
  line-height: 30px;
  text-align: center;
}
.details-btn:hover {
  background-color: rgba(255, 255, 255, 0.1);
}
.details-table {
  width: 100%;
  text-align: left;
  margin-top: 1rem;
  border-collapse: collapse;
}
.details-table th,
.details-table td {
  padding: 0.75rem;
  border-bottom: 1px solid var(--primary-color);
}
.details-table th {
  color: var(--gray);
  text-transform: capitalize;
}
.details-table td {
  color: white;
  word-break: break-all;
}
</style>
