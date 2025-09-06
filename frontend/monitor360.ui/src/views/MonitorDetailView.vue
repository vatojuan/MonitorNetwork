<script setup>
import { ref, onMounted, onUnmounted, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '@/lib/api'
import { Line } from 'vue-chartjs'
import {
  Chart as ChartJS,
  Title,
  Tooltip,
  Legend,
  LineElement,
  CategoryScale,
  LinearScale,
  PointElement,
  TimeScale,
  Filler,
} from 'chart.js'
import 'chartjs-adapter-date-fns'
import { es } from 'date-fns/locale'
import zoomPlugin from 'chartjs-plugin-zoom'
import { addWsListener, connectWebSocketWhenAuthenticated, getCurrentWebSocket } from '@/lib/ws'

ChartJS.register(
  Title,
  Tooltip,
  Legend,
  LineElement,
  CategoryScale,
  LinearScale,
  PointElement,
  TimeScale,
  Filler,
  zoomPlugin,
)

const route = useRoute()
const router = useRouter()
const sensorId = Number(route.params.id)

const chartRef = ref(null)
const sensorInfo = ref(null)
const historyData = ref([])
const isLoading = ref(true)
const isZoomed = ref(false)
const timeRange = ref('24h')
// CAMBIO: La variable isLiveView ya no es necesaria, la hemos eliminado.

const localTz = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'

const hoursMap = { '1h': 1, '12h': 12, '24h': 24, '7d': 168, '30d': 720 }
const timeRanges = hoursMap

// --- Helpers de formato ---
function formatBitrateForChart(bits) {
  const n = Number(bits)
  return !Number.isFinite(n) || n < 0 ? 0 : Number((n / 1_000_000).toFixed(2))
}

function pickTimestamp(obj) {
  if (!obj) return null
  const v = obj.timestamp || obj.ts || obj.time
  if (v) {
    const d = new Date(v)
    if (!isNaN(d.valueOf())) return d
  }
  return new Date()
}

// --- Lógica de datos ---
async function fetchHistory() {
  isLoading.value = true
  try {
    const { data } = await api.get(`/sensors/${sensorId}/history_range`, {
      params: { time_range: timeRange.value },
    })
    historyData.value = Array.isArray(data) ? data : []
  } catch (err) {
    console.error('Error fetching history:', err)
    historyData.value = []
  } finally {
    isLoading.value = false
  }
}

// --- Lógica de WebSocket ---
function onBusMessage(payload) {
  const updates = Array.isArray(payload) ? payload : [payload]
  const relevantUpdates = updates.filter((u) => u && Number(u.sensor_id) === sensorId)

  if (relevantUpdates.length === 0) {
    return
  }

  // CAMBIO: Se eliminó el chequeo de "isLiveView". El gráfico ahora SIEMPRE se actualiza.

  const dataMap = new Map()

  historyData.value.forEach((point) => {
    const ts = new Date(point.timestamp).toISOString()
    dataMap.set(ts, point)
  })

  relevantUpdates.forEach((point) => {
    const ts = pickTimestamp(point).toISOString()
    dataMap.set(ts, { ...point, timestamp: ts })
  })

  const newHistory = Array.from(dataMap.values())
  newHistory.sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp))

  const nowMs = Date.now()
  const hours = hoursMap[timeRange.value] ?? 24
  const cutoff = nowMs - hours * 3600 * 1000

  historyData.value = newHistory.filter((p) => new Date(p.timestamp).getTime() >= cutoff)
}

// --- Configuración del Gráfico ---
const chartData = computed(() => {
  if (!sensorInfo.value) return { datasets: [] }

  const dataPoints = historyData.value
  const type = sensorInfo.value.sensor_type

  if (type === 'ping') {
    return {
      datasets: [
        {
          label: 'Latencia (ms)',
          backgroundColor: '#5372f0',
          borderColor: '#5372f0',
          data: dataPoints.map((d) => ({
            x: new Date(d.timestamp).valueOf(),
            y: Number(d.latency_ms ?? 0),
          })),
          tension: 0.2,
          pointRadius: 2,
        },
      ],
    }
  }

  if (type === 'ethernet') {
    return {
      datasets: [
        {
          label: 'Descarga (Mbps)',
          backgroundColor: 'rgba(54, 162, 235, 0.5)',
          borderColor: 'rgba(54, 162, 235, 1)',
          data: dataPoints.map((d) => ({
            x: new Date(d.timestamp).valueOf(),
            y: formatBitrateForChart(d.rx_bitrate),
          })),
          tension: 0.2,
          pointRadius: 2,
          fill: true,
        },
        {
          label: 'Subida (Mbps)',
          backgroundColor: 'rgba(75, 192, 192, 0.5)',
          borderColor: 'rgba(75, 192, 192, 1)',
          data: dataPoints.map((d) => ({
            x: new Date(d.timestamp).valueOf(),
            y: formatBitrateForChart(d.tx_bitrate),
          })),
          tension: 0.2,
          pointRadius: 2,
          fill: true,
        },
      ],
    }
  }

  return { datasets: [] }
})

const chartOptions = computed(() => ({
  responsive: true,
  maintainAspectRatio: false,
  animation: { duration: 0 },
  scales: {
    x: {
      type: 'time',
      adapters: { date: { locale: es } },
      time: {
        unit: timeRange.value === '1h' ? 'minute' : 'hour',
        tooltipFormat: 'dd MMM, HH:mm:ss',
        displayFormats: { minute: 'HH:mm', hour: 'HH:mm' },
      },
    },
    y: { beginAtZero: true },
  },
  plugins: {
    legend: { display: sensorInfo.value?.sensor_type === 'ethernet' },
    zoom: {
      // CAMBIO: Se eliminaron los "onPanStart" y "onZoomStart" que desactivaban el live view.
      pan: { enabled: true, mode: 'x' },
      zoom: {
        wheel: { enabled: true },
        mode: 'x',
        onZoomComplete: () => {
          isZoomed.value = true
        },
      },
    },
  },
  interaction: { mode: 'nearest', intersect: false },
}))

// --- Acciones de UI ---
function setRange(range) {
  timeRange.value = range
  isZoomed.value = false
  chartRef.value?.chart.resetZoom()
  fetchHistory()
}
function resetZoom() {
  chartRef.value?.chart.resetZoom()
  isZoomed.value = false
}

// --- Ciclo de Vida del Componente ---
let offBus = null

onMounted(async () => {
  try {
    const { data } = await api.get(`/sensors/${sensorId}/details`)
    sensorInfo.value = data
    await fetchHistory()
  } catch (err) {
    console.error('Error loading sensor details:', err)
    router.push('/')
    return
  }

  await connectWebSocketWhenAuthenticated()
  offBus = addWsListener(onBusMessage)

  const ws = getCurrentWebSocket()
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'subscribe_sensors', sensor_ids: [sensorId] }))
  }
})

onUnmounted(() => {
  if (offBus) offBus()
})

watch(timeRange, () => fetchHistory())

const linkStatusEvents = computed(() => {
  if (sensorInfo.value?.sensor_type !== 'ethernet' || historyData.value.length === 0) return []
  const events = []
  let lastStatus = null
  let lastSpeed = null
  historyData.value.forEach((d) => {
    if (d.status !== lastStatus || d.speed !== lastSpeed) {
      events.push({
        timestamp: new Intl.DateTimeFormat('es-AR', {
          dateStyle: 'medium',
          timeStyle: 'medium',
        }).format(new Date(d.timestamp)),
        status: d.status,
        speed: d.speed,
      })
      lastStatus = d.status
      lastSpeed = d.speed
    }
  })
  return events.reverse()
})
</script>

<template>
  <div class="detail-view">
    <button @click="router.push('/')" class="back-button">‹ Volver al Dashboard</button>

    <div v-if="sensorInfo" class="monitor-header">
      <h1>{{ sensorInfo.name }}</h1>
      <p>
        Sensor en <strong>{{ sensorInfo.client_name }}</strong> ({{ sensorInfo.ip_address }})
      </p>
    </div>

    <div class="time-controls">
      <div class="range-selector">
        <button
          v-for="(hours, range) in timeRanges"
          :key="range"
          @click="setRange(range)"
          :class="{ active: timeRange === range }"
        >
          {{ range }}
        </button>
      </div>
    </div>

    <div class="chart-container">
      <button v-if="isZoomed" @click="resetZoom" class="reset-zoom-btn">Resetear Zoom</button>

      <Line
        v-if="!isLoading && historyData.length > 0"
        ref="chartRef"
        :data="chartData"
        :options="chartOptions"
      />

      <div v-else class="loading-overlay">
        <p>{{ isLoading ? 'Cargando datos...' : 'No hay historial para este rango.' }}</p>
      </div>

      <div class="tz-hint">
        Mostrando horas en tu zona: <strong>{{ localTz }}</strong>
      </div>
    </div>

    <div
      v-if="sensorInfo?.sensor_type === 'ethernet' && linkStatusEvents.length > 0"
      class="events-container"
    >
      <h3>Historial de Eventos del Enlace</h3>
      <table class="events-table">
        <thead>
          <tr>
            <th>Fecha y Hora</th>
            <th>Evento</th>
            <th>Velocidad</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(event, index) in linkStatusEvents" :key="index">
            <td>{{ event.timestamp }}</td>
            <td>
              <span :class="['status-badge', event.status]">
                <span v-if="event.status === 'link_up'">✓ Enlace Activo</span>
                <span v-else-if="event.status === 'link_down'">✗ Enlace Caído</span>
                <span v-else>{{ event.status }}</span>
              </span>
            </td>
            <td>{{ event.status === 'link_up' ? event.speed : 'N/A' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.detail-view {
  max-width: 1200px;
  margin: auto;
}
.back-button {
  background: none;
  border: 1px solid var(--primary-color);
  color: var(--font-color);
  padding: 0.5rem 1rem;
  border-radius: 6px;
  cursor: pointer;
  margin-bottom: 2rem;
}
.monitor-header {
  background-color: var(--surface-color);
  padding: 1.5rem 2rem;
  border-radius: 12px;
  margin-bottom: 1rem;
}
.monitor-header h1 {
  margin: 0 0 0.5rem 0;
}
.monitor-header p {
  margin: 0;
  color: var(--gray);
}

.time-controls {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 1rem;
  margin-bottom: 1rem;
  padding: 1rem;
  background-color: var(--surface-color);
  border-radius: 12px;
}
.range-selector {
  display: flex;
  gap: 0.5rem;
}
.time-controls button {
  background-color: var(--primary-color);
  color: var(--font-color);
  border: none;
  padding: 0.5rem 1rem;
  border-radius: 6px;
  cursor: pointer;
}
.time-controls button:hover {
  background-color: #5372f0;
}
.time-controls button.active {
  background-color: var(--blue);
  color: white;
}

.chart-container {
  background-color: var(--surface-color);
  padding: 2rem;
  border-radius: 12px;
  height: 500px;
  position: relative;
}
.reset-zoom-btn {
  position: absolute;
  top: 1rem;
  right: 1rem;
  z-index: 10;
  background-color: var(--secondary-color);
  color: white;
  border: none;
  padding: 0.5rem 1rem;
  border-radius: 6px;
  cursor: pointer;
}
.loading-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  justify-content: center;
  align-items: center;
}
.tz-hint {
  position: absolute;
  left: 1rem;
  bottom: 0.75rem;
  color: var(--gray);
  font-size: 0.85rem;
}

.events-container {
  background-color: var(--surface-color);
  padding: 1.5rem 2rem;
  border-radius: 12px;
  margin-top: 2rem;
}
.events-container h3 {
  margin-top: 0;
  margin-bottom: 1.5rem;
}
.events-table {
  width: 100%;
  border-collapse: collapse;
}
.events-table th,
.events-table td {
  padding: 0.75rem 1rem;
  text-align: left;
  border-bottom: 1px solid var(--primary-color);
}
.events-table th {
  color: var(--gray);
  font-size: 0.9rem;
  text-transform: uppercase;
}
.status-badge {
  padding: 0.3rem 0.6rem;
  border-radius: 12px;
  font-weight: bold;
  font-size: 0.85rem;
}
.status-badge.link_up {
  background-color: rgba(61, 220, 132, 0.2);
  color: var(--green);
}
.status-badge.link_down {
  background-color: rgba(233, 69, 96, 0.2);
  color: var(--secondary-color);
}
</style>
