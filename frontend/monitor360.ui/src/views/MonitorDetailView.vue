<script setup>
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import axios from 'axios'
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
} from 'chart.js'
import 'chartjs-adapter-date-fns'
import { es } from 'date-fns/locale'
import zoomPlugin from 'chartjs-plugin-zoom'

// Registrar componentes/escala/plugins de Chart.js (incluye zoom)
ChartJS.register(
  Title,
  Tooltip,
  Legend,
  LineElement,
  CategoryScale,
  LinearScale,
  PointElement,
  TimeScale,
  zoomPlugin,
)

// ---------- Endpoints dinámicos ----------
const API_PROTO = window.location.protocol
const HOST = window.location.hostname
const API_BASE_URL = `${API_PROTO}//${HOST}:8000/api`
const WS_PROTO = API_PROTO === 'https:' ? 'wss' : 'ws'
const WS_URL = `${WS_PROTO}://${HOST}:8000/ws`

// ---------- Router / estado base ----------
const route = useRoute()
const router = useRouter()
const chartRef = ref(null)

const sensorId = Number(route.params.id)
const sensorInfo = ref(null)
const historyData = ref([])
const knownTimestamps = ref(new Set())
const isLoading = ref(true)
const isZoomed = ref(false)
const timeRange = ref('24h')
const isLiveView = ref(true) // si estamos pegados al presente

const localTz = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'

// ---------- WebSocket ----------
let socket = null

function connectWebSocket() {
  try {
    socket = new WebSocket(WS_URL)

    socket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        if (Number(data.sensor_id) !== sensorId) return

        // Si estamos en vista "en vivo", agregamos el punto nuevo.
        if (isLiveView.value) {
          const key = new Date(data.timestamp).toISOString()
          if (!knownTimestamps.value.has(key)) {
            knownTimestamps.value.add(key)
            historyData.value.push(data)

            // Asegurar orden temporal si viniera fuera de orden
            const len = historyData.value.length
            if (len > 1) {
              const last = new Date(historyData.value[len - 1].timestamp)
              const prev = new Date(historyData.value[len - 2].timestamp)
              if (last < prev) {
                historyData.value.sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp))
              }
            }
          }
        }
      } catch (err) {
        // Evitamos no-empty y registramos en dev
        if (import.meta.env?.DEV) console.debug('WS parse error:', err)
      }
    }

    socket.onerror = (err) => {
      if (import.meta.env?.DEV) console.debug('WebSocket error:', err)
    }

    socket.onclose = () => {
      // Reintento simple
      setTimeout(connectWebSocket, 3000)
    }
  } catch (err) {
    if (import.meta.env?.DEV) console.debug('No se pudo abrir WebSocket:', err)
  }
}

// ---------- Helpers ----------
function formatBitrateForChart(bits) {
  const n = Number(bits)
  if (!Number.isFinite(n) || n < 0) return 0
  return Number((n / 1_000_000).toFixed(2)) // Mbps
}

const timeRanges = { '1h': 1, '12h': 12, '24h': 24, '7d': 168, '30d': 720 }

// ---------- Carga inicial de historial y sensor ----------
async function fetchHistory() {
  isLoading.value = true
  try {
    // El back calcula start/end en UTC según time_range
    const { data } = await axios.get(`${API_BASE_URL}/sensors/${sensorId}/history_range`, {
      params: { time_range: timeRange.value },
    })
    const arr = Array.isArray(data) ? data : []
    arr.sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp))
    historyData.value = arr
    knownTimestamps.value = new Set(arr.map((d) => new Date(d.timestamp).toISOString()))
  } catch (err) {
    if (import.meta.env?.DEV) console.debug('Error fetching history:', err)
    historyData.value = []
    knownTimestamps.value = new Set()
  } finally {
    isLoading.value = false
  }
}

async function fetchSensorInfo() {
  try {
    const { data } = await axios.get(`${API_BASE_URL}/sensors/${sensorId}/details`)
    sensorInfo.value = data
  } catch (err) {
    if (import.meta.env?.DEV) console.debug('Error fetching sensor info:', err)
    router.push('/')
  }
}

// ---------- Rango / zoom ----------
function setRange(range) {
  timeRange.value = range
  isLiveView.value = true
  isZoomed.value = false
  const chart = chartRef.value?.chart
  if (chart?.resetZoom) chart.resetZoom()
  fetchHistory()
}

function resetZoom() {
  const chart = chartRef.value?.chart
  if (chart?.resetZoom) {
    chart.resetZoom()
    isZoomed.value = false
    isLiveView.value = true
  }
}

// ---------- Eventos de enlace (ethernet) ----------
const linkStatusEvents = computed(() => {
  if (sensorInfo.value?.sensor_type !== 'ethernet' || historyData.value.length === 0) return []
  const events = []
  let lastStatus = null
  let lastSpeed = null

  historyData.value.forEach((d) => {
    if (d.status !== lastStatus || d.speed !== lastSpeed) {
      const tsLocal = new Date(d.timestamp)
      events.push({
        timestamp: new Intl.DateTimeFormat('es-AR', {
          dateStyle: 'medium',
          timeStyle: 'medium',
        }).format(tsLocal),
        status: d.status,
        speed: d.speed,
      })
      lastStatus = d.status
      lastSpeed = d.speed
    }
  })
  return events.reverse()
})

// ---------- Chart config ----------
const timeUnit = computed(() => {
  switch (timeRange.value) {
    case '1h':
      return 'minute'
    case '12h':
    case '24h':
      return 'hour'
    case '7d':
    case '30d':
      return 'day'
    default:
      return 'hour'
  }
})

const chartData = computed(() => {
  if (!sensorInfo.value) return { datasets: [] }

  // Alimentamos {x, y} en milisegundos locales
  if (sensorInfo.value.sensor_type === 'ping') {
    return {
      datasets: [
        {
          label: 'Latencia (ms)',
          backgroundColor: '#5372f0',
          borderColor: '#5372f0',
          data: historyData.value.map((d) => ({
            x: new Date(d.timestamp).valueOf(),
            y: Number(d.latency_ms || 0),
          })),
          tension: 0.2,
          pointRadius: 2,
        },
      ],
    }
  }

  if (sensorInfo.value.sensor_type === 'ethernet') {
    return {
      datasets: [
        {
          label: 'Descarga (Mbps)',
          backgroundColor: 'rgba(54, 162, 235, 0.5)',
          borderColor: 'rgba(54, 162, 235, 1)',
          data: historyData.value.map((d) => ({
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
          data: historyData.value.map((d) => ({
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

const chartOptions = computed(() => {
  const isEthernet = sensorInfo.value?.sensor_type === 'ethernet'
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    parsing: false, // usamos {x,y}
    scales: {
      x: {
        type: 'time',
        adapters: { date: { locale: es } },
        time: {
          unit: timeUnit.value,
          tooltipFormat: 'dd MMM, HH:mm:ss',
          displayFormats: { minute: 'HH:mm', hour: 'HH:mm', day: 'dd MMM' },
        },
        ticks: { color: '#8d8d8d' },
        grid: { color: 'rgba(255, 255, 255, 0.1)' },
      },
      y: {
        beginAtZero: true,
        ticks: {
          color: '#8d8d8d',
          callback: (value) => (isEthernet ? `${value} Mbps` : value),
        },
        grid: { color: 'rgba(255, 255, 255, 0.1)' },
      },
    },
    plugins: {
      legend: { display: isEthernet, labels: { color: '#e0e0e0' } },
      tooltip: {
        callbacks: {
          label: (ctx) => {
            let label = ctx.dataset.label ? `${ctx.dataset.label}: ` : ''
            if (ctx.parsed.y != null) {
              label += isEthernet ? `${ctx.parsed.y} Mbps` : `${ctx.parsed.y} ms`
            }
            return label
          },
        },
      },
      zoom: {
        // Pan con mouse presionado
        pan: {
          enabled: true,
          mode: 'x',
          onPanStart: () => {
            isLiveView.value = false
          },
        },
        // Zoom con rueda o pinza
        zoom: {
          wheel: { enabled: true },
          pinch: { enabled: true },
          mode: 'x',
          onZoomStart: () => {
            isLiveView.value = false
          },
          onZoomComplete: () => {
            isZoomed.value = true
          },
        },
      },
    },
    interaction: {
      mode: 'nearest',
      intersect: false,
    },
  }
})

// ---------- Efectos ----------
onMounted(() => {
  fetchSensorInfo()
  fetchHistory()
  connectWebSocket()
})

onUnmounted(() => {
  try {
    socket?.close()
  } catch (err) {
    // evita regla no-empty y no-unused-vars
    void err
  }
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
.range-selector,
.navigation {
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
