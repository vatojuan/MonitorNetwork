<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
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
import { format } from 'date-fns'
import zoomPlugin from 'chartjs-plugin-zoom'

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

// --- Endpoints dinámicos ---
const API_PROTO = window.location.protocol
const HOST = window.location.hostname
const API_BASE_URL = `${API_PROTO}//${HOST}:8000/api`
const WS_PROTO = API_PROTO === 'https:' ? 'wss' : 'ws'
const WS_URL = `${WS_PROTO}://${HOST}:8000/ws`

const route = useRoute()
const router = useRouter()

// --- Estado ---
const chartRef = ref(null)
const chartKey = ref(0) // Re-render sólo cuando cambiamos rango/cargamos historia
const sensorId = Number(route.params.id)
const sensorInfo = ref(null)
const historyData = ref([])
const isLoading = ref(true)
const isZoomed = ref(false)
const timeRange = ref('24h')
const viewEndDate = ref(new Date())
const liveMode = ref(true)

let socket = null
let wsReconnectTimer = null
let wsAttempts = 0
let liveTickTimer = null

// --- Helpers ---
function formatBitrateForChart(bits) {
  const n = Number(bits)
  if (!Number.isFinite(n) || n < 0) return 0
  return Number((n / 1_000_000).toFixed(2))
}

/**
 * Normaliza timestamps del backend.
 * - Con zona (Z / +hh:mm): se respeta tal cual (el navegador lo muestra en local).
 * - Sin zona (p.ej. "YYYY-MM-DD HH:mm:ss" de SQLite): se interpreta como HORA LOCAL (NO UTC).
 *   Esto evita el corrimiento de -3h que veías.
 */
// Reemplaza la función parseApiTs por esta versión
function parseApiTs(ts) {
  if (!ts) return new Date()
  if (ts instanceof Date) return ts
  if (typeof ts === 'number') return new Date(ts)

  if (typeof ts === 'string') {
    // 1) Si viene con zona (Z o +hh:mm), el navegador ya la convierte a local.
    if (/T.*([+-]\d{2}:\d{2}|Z)$/i.test(ts)) {
      return new Date(ts)
    }
    // 2) SQLite: "YYYY-MM-DD HH:mm:ss" (UTC pero sin zona) -> trátalo como UTC agregando 'Z'
    if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(ts)) {
      return new Date(ts.replace(' ', 'T') + 'Z')
    }
    // 3) "YYYY-MM-DDTHH:mm:ss" (sin zona) -> asúmelo UTC
    if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/.test(ts)) {
      return new Date(ts + 'Z')
    }
    // Fallback
    return new Date(ts)
  }

  return new Date(ts)
}

const timeRanges = { '1h': 1, '12h': 12, '24h': 24, '7d': 168, '30d': 720 }

const viewStartDate = computed(() => {
  const endDate = new Date(viewEndDate.value)
  endDate.setHours(endDate.getHours() - timeRanges[timeRange.value])
  return endDate
})

const isEthernet = computed(() => sensorInfo.value?.sensor_type === 'ethernet')
const isPing = computed(() => sensorInfo.value?.sensor_type === 'ping')

// Ventana viva (seguir al "ahora")
const isLiveWindow = computed(() => {
  const diff = Date.now() - viewEndDate.value.getTime()
  return liveMode.value && diff < 60_000
})

function trimHistoryToWindow() {
  const start = viewStartDate.value.getTime()
  const end = viewEndDate.value.getTime()
  historyData.value = historyData.value.filter((d) => {
    const t = d.timestamp.getTime()
    return t >= start && t <= end
  })
}

// --- Carga inicial ---
async function fetchSensorInfo() {
  try {
    const { data } = await axios.get(`${API_BASE_URL}/sensors/${sensorId}/details`)
    sensorInfo.value = data
  } catch (err) {
    console.error('Error fetching sensor info:', err)
    router.push('/')
  }
}

async function fetchHistory() {
  isLoading.value = true
  try {
    const startISO = viewStartDate.value.toISOString()
    const endISO = viewEndDate.value.toISOString()
    const { data } = await axios.get(`${API_BASE_URL}/sensors/${sensorId}/history_range`, {
      params: { start: startISO, end: endISO },
    })

    const arr = Array.isArray(data) ? data : []
    arr.sort((a, b) => parseApiTs(a.timestamp) - parseApiTs(b.timestamp))
    historyData.value = arr.map((row) => {
      const base = { timestamp: parseApiTs(row.timestamp) }
      if (row.latency_ms !== undefined) {
        base.latency_ms = Number(row.latency_ms) || 0
        base.status = row.status || 'ok'
      }
      if (row.rx_bitrate !== undefined || row.tx_bitrate !== undefined) {
        base.status = row.status || 'link_down'
        base.speed = row.speed || 'N/A'
        base.rx_bitrate = row.rx_bitrate ?? '0'
        base.tx_bitrate = row.tx_bitrate ?? '0'
      }
      return base
    })
  } catch (err) {
    console.error('Error fetching history:', err)
    historyData.value = []
  } finally {
    isLoading.value = false
  }
}

// --- WS tiempo real ---
function connectWebSocket() {
  try {
    socket = new WebSocket(WS_URL)

    socket.onopen = () => {
      wsAttempts = 0
    }

    socket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        if (Number(data.sensor_id) !== sensorId) return

        const ts = parseApiTs(data.timestamp)
        const last = historyData.value[historyData.value.length - 1]
        if (last && ts <= last.timestamp) return

        if (data.sensor_type === 'ping') {
          historyData.value.push({
            timestamp: ts,
            latency_ms: Number(data.latency_ms) || 0,
            status: data.status || 'ok',
          })
        } else if (data.sensor_type === 'ethernet') {
          historyData.value.push({
            timestamp: ts,
            status: data.status || 'link_down',
            speed: data.speed || 'N/A',
            rx_bitrate: data.rx_bitrate ?? '0',
            tx_bitrate: data.tx_bitrate ?? '0',
          })
        }

        if (isLiveWindow.value) {
          viewEndDate.value = new Date()
          trimHistoryToWindow()
        }

        updateChartFromState()
      } catch (err) {
        console.error('WS parse error:', err)
      }
    }

    socket.onclose = () => {
      const delay = Math.min(30000, 1000 * 2 ** wsAttempts)
      wsAttempts += 1
      if (wsReconnectTimer) clearTimeout(wsReconnectTimer)
      wsReconnectTimer = setTimeout(connectWebSocket, delay)
    }

    socket.onerror = (err) => {
      console.error('WebSocket error:', err)
      if (socket && socket.readyState !== WebSocket.CLOSED) {
        socket.close()
      }
    }
  } catch (err) {
    console.error('No se pudo abrir WebSocket:', err)
  }
}

// Mantener vivo el rango cuando liveMode está activo
function startLiveTick() {
  if (liveTickTimer) clearInterval(liveTickTimer)
  liveTickTimer = setInterval(() => {
    if (!isLiveWindow.value) return
    viewEndDate.value = new Date()
    trimHistoryToWindow()
    updateChartFromState()
  }, 5000)
}

// --- Interacciones de rango ---
function setRange(range) {
  timeRange.value = range
  viewEndDate.value = new Date()
  liveMode.value = true
  fetchHistory().then(() => {
    chartKey.value++ // re-render limpio del chart
  })
}

function navigateTime(direction) {
  const hours = timeRanges[timeRange.value]
  const newEnd = new Date(viewEndDate.value)
  newEnd.setHours(newEnd.getHours() + (direction === 'prev' ? -hours : hours))
  viewEndDate.value = newEnd

  if (direction === 'next' && Date.now() - newEnd.getTime() < 60_000) {
    liveMode.value = true
    viewEndDate.value = new Date()
  } else {
    liveMode.value = false
  }

  fetchHistory().then(() => {
    chartKey.value++
  })
}

function resetZoom() {
  const chart = chartRef.value?.chart
  if (chart?.resetZoom) {
    chart.resetZoom()
    isZoomed.value = false
  }
}

// --- Eventos de enlace (solo ethernet) ---
const linkStatusEvents = computed(() => {
  if (!isEthernet.value || historyData.value.length === 0) return []
  const events = []
  let lastStatus = null
  let lastSpeed = null
  historyData.value.forEach((d) => {
    if (d.status !== lastStatus || d.speed !== lastSpeed) {
      events.push({
        timestamp: format(d.timestamp, 'dd MMM yyyy, HH:mm:ss', { locale: es }),
        status: d.status,
        speed: d.speed,
      })
      lastStatus = d.status
      lastSpeed = d.speed
    }
  })
  return events.reverse()
})

// --- Chart ---
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

  if (isPing.value) {
    const points = historyData.value.map((d) => ({
      x: d.timestamp.getTime(),
      y: Number(d.latency_ms || 0),
    }))
    return {
      datasets: [
        {
          label: 'Latencia (ms)',
          data: points,
          backgroundColor: '#5372f0',
          borderColor: '#5372f0',
          tension: 0.2,
          pointRadius: 2,
        },
      ],
    }
  }

  if (isEthernet.value) {
    const rx = historyData.value.map((d) => ({
      x: d.timestamp.getTime(),
      y: formatBitrateForChart(d.rx_bitrate),
    }))
    const tx = historyData.value.map((d) => ({
      x: d.timestamp.getTime(),
      y: formatBitrateForChart(d.tx_bitrate),
    }))
    return {
      datasets: [
        {
          label: 'Descarga (Mbps)',
          data: rx,
          backgroundColor: 'rgba(54, 162, 235, 0.5)',
          borderColor: 'rgba(54, 162, 235, 1)',
          tension: 0.2,
          pointRadius: 2,
          fill: true,
        },
        {
          label: 'Subida (Mbps)',
          data: tx,
          backgroundColor: 'rgba(75, 192, 192, 0.5)',
          borderColor: 'rgba(75, 192, 192, 1)',
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
  const ethernet = isEthernet.value
  return {
    responsive: true,
    maintainAspectRatio: false,
    parsing: true, // usamos {x,y}
    scales: {
      x: {
        type: 'time',
        adapters: { date: { locale: es } },
        time: {
          unit: timeUnit.value,
          tooltipFormat: 'dd MMM, HH:mm',
          displayFormats: { minute: 'HH:mm', hour: 'HH:mm', day: 'dd MMM' },
        },
        ticks: { color: '#8d8d8d' },
        grid: { color: 'rgba(255, 255, 255, 0.1)' },
      },
      y: {
        beginAtZero: true,
        ticks: {
          color: '#8d8d8d',
          callback: (value) => (ethernet ? `${value} Mbps` : value),
        },
        grid: { color: 'rgba(255, 255, 255, 0.1)' },
      },
    },
    plugins: {
      legend: { display: ethernet, labels: { color: '#e0e0e0' } },
      tooltip: {
        callbacks: {
          title: (items) => {
            return format(new Date(items[0].parsed.x), 'dd MMM, HH:mm', { locale: es })
          },
          label: (context) => {
            const v = context.parsed.y
            const unit = ethernet ? 'Mbps' : 'ms'
            const name = context.dataset.label ? `${context.dataset.label}: ` : ''
            return `${name}${v} ${unit}`
          },
        },
      },
      zoom: {
        pan: { enabled: true, mode: 'x' },
        zoom: {
          wheel: { enabled: true },
          pinch: { enabled: true },
          mode: 'x',
          onZoomComplete: () => (isZoomed.value = true),
        },
      },
    },
  }
})

// Actualiza datasets sin recrear el componente
function updateChartFromState() {
  const chart = chartRef.value?.chart
  if (!chart) return

  if (isPing.value) {
    chart.data.datasets[0].data = historyData.value.map((d) => ({
      x: d.timestamp.getTime(),
      y: Number(d.latency_ms || 0),
    }))
  } else if (isEthernet.value) {
    chart.data.datasets[0].data = historyData.value.map((d) => ({
      x: d.timestamp.getTime(),
      y: formatBitrateForChart(d.rx_bitrate),
    }))
    chart.data.datasets[1].data = historyData.value.map((d) => ({
      x: d.timestamp.getTime(),
      y: formatBitrateForChart(d.tx_bitrate),
    }))
  }

  chart.update('none')
}

// --- Ciclo de vida ---
onMounted(async () => {
  await fetchSensorInfo()
  await fetchHistory()
  connectWebSocket()
  startLiveTick()
})

onUnmounted(() => {
  if (socket && socket.readyState !== WebSocket.CLOSED) {
    socket.close()
  }
  if (wsReconnectTimer) clearTimeout(wsReconnectTimer)
  if (liveTickTimer) clearInterval(liveTickTimer)
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
      <div class="navigation">
        <button @click="navigateTime('prev')">&lt; Anterior</button>
        <button @click="navigateTime('next')">Siguiente &gt;</button>
      </div>
    </div>

    <div class="chart-container">
      <button v-if="isZoomed" @click="resetZoom" class="reset-zoom-btn">Resetear Zoom</button>
      <Line
        v-if="!isLoading && historyData.length > 0"
        ref="chartRef"
        :key="chartKey"
        :data="chartData"
        :options="chartOptions"
      />
      <div v-else class="loading-overlay">
        <p>{{ isLoading ? 'Cargando datos...' : 'No hay historial para este rango.' }}</p>
      </div>
    </div>

    <div v-if="isEthernet && linkStatusEvents.length > 0" class="events-container">
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
