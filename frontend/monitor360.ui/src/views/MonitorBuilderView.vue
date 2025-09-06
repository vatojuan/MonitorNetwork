<script setup>
import { ref, watch, onMounted } from 'vue'
import api from '@/lib/api' // ← usamos el cliente central (con Bearer)

//
// --- Estado General ---
const searchQuery = ref('')
const searchResults = ref([])
const isLoading = ref(false)
const selectedDevice = ref(null)
const allMonitors = ref([])
const currentMonitor = ref(null)
const activeSensors = ref([])
const notification = ref({ show: false, message: '', type: 'success' })
const formToShow = ref(null)
const channels = ref([])

// --- Estado para Edición ---
const sensorToEdit = ref(null) // Contendrá el sensor que se está editando
const isEditMode = ref(false)

//
// --- Plantillas de Formularios ---
const createNewPingSensor = () => ({
  name: '',
  config: {
    ping_type: 'maestro_to_device',
    target_ip: '',
    interval_sec: 60,
    latency_threshold_ms: 150,
    display_mode: 'realtime',
    average_count: 5,
  },
  ui_alert_timeout: { enabled: false, channel_id: null, cooldown_minutes: 5 },
  ui_alert_latency: { enabled: false, threshold_ms: 200, channel_id: null, cooldown_minutes: 5 },
})

const createNewEthernetSensor = () => ({
  name: '',
  config: {
    interface_name: '',
    interval_sec: 30,
  },
  ui_alert_speed_change: { enabled: false, channel_id: null, cooldown_minutes: 10 },
  ui_alert_traffic: {
    enabled: false,
    threshold_mbps: 100,
    direction: 'any',
    channel_id: null,
    cooldown_minutes: 5,
  },
})

const newPingSensor = ref(createNewPingSensor())
const newEthernetSensor = ref(createNewEthernetSensor())

//
// --- Ciclo de Vida y Funciones ---
onMounted(() => {
  fetchAllMonitors()
  fetchChannels()
})

function showNotification(message, type = 'success') {
  notification.value = { show: true, message, type }
  setTimeout(() => {
    notification.value.show = false
  }, 4000)
}

async function fetchChannels() {
  try {
    const { data } = await api.get('/channels')
    channels.value = (data || []).map((ch) => ({
      ...ch,
      config: typeof ch.config === 'string' ? safeJsonParse(ch.config) : ch.config,
    }))
  } catch (err) {
    console.error('Error al cargar canales:', err)
    showNotification('Error al cargar canales.', 'error')
  }
}

function safeJsonParse(v, fallback = null) {
  try {
    return JSON.parse(v)
  } catch {
    return fallback
  }
}

//
// --- Lógica de Creación y Edición de Sensores ---
function buildSensorPayload(sensorType, sensorData) {
  const finalConfig = { ...sensorData.config }
  finalConfig.alerts = []

  if (sensorType === 'ping') {
    if (sensorData.ui_alert_timeout.enabled) {
      if (!sensorData.ui_alert_timeout.channel_id)
        throw new Error('Selecciona un canal para la alerta de Timeout.')
      finalConfig.alerts.push({ type: 'timeout', ...sensorData.ui_alert_timeout })
    }
    if (sensorData.ui_alert_latency.enabled) {
      if (!sensorData.ui_alert_latency.channel_id)
        throw new Error('Selecciona un canal para la alerta de Latencia.')
      finalConfig.alerts.push({ type: 'high_latency', ...sensorData.ui_alert_latency })
    }
  } else if (sensorType === 'ethernet') {
    if (sensorData.ui_alert_speed_change.enabled) {
      if (!sensorData.ui_alert_speed_change.channel_id)
        throw new Error('Selecciona un canal para la alerta de Cambio de Velocidad.')
      finalConfig.alerts.push({ type: 'speed_change', ...sensorData.ui_alert_speed_change })
    }
    if (sensorData.ui_alert_traffic.enabled) {
      if (!sensorData.ui_alert_traffic.channel_id)
        throw new Error('Selecciona un canal para la alerta de Umbral de Tráfico.')
      finalConfig.alerts.push({ type: 'traffic_threshold', ...sensorData.ui_alert_traffic })
    }
  }
  return { name: sensorData.name, config: finalConfig }
}

async function handleSaveSensor() {
  if (!formToShow.value) return
  const sensorData = formToShow.value === 'ping' ? newPingSensor.value : newEthernetSensor.value
  try {
    const payload = buildSensorPayload(formToShow.value, sensorData)

    if (isEditMode.value && sensorToEdit.value) {
      // --- Actualización ---
      const { data } = await api.put(`/sensors/${sensorToEdit.value.id}`, payload)

      // Mezclamos para no perder id / sensor_type
      const idx = activeSensors.value.findIndex((s) => s.id === sensorToEdit.value.id)
      if (idx !== -1) {
        activeSensors.value[idx] = {
          ...activeSensors.value[idx],
          ...data,
          id: activeSensors.value[idx].id,
          sensor_type: activeSensors.value[idx].sensor_type,
          config: typeof data.config === 'string' ? safeJsonParse(data.config, {}) : data.config,
        }
      }

      showNotification('Sensor actualizado.', 'success')
    } else {
      // --- Creación ---
      if (!currentMonitor.value?.monitor_id) {
        showNotification('Primero crea la tarjeta de monitoreo.', 'error')
        return
      }
      const createPayload = {
        monitor_id: currentMonitor.value.monitor_id,
        sensor_type: formToShow.value,
        ...payload,
      }
      const { data } = await api.post('/sensors', createPayload)
      activeSensors.value.push({
        ...data,
        config: typeof data.config === 'string' ? safeJsonParse(data.config, {}) : data.config,
      })
      showNotification('Sensor añadido.', 'success')
    }

    // Refrescar desde backend por consistencia
    await selectDevice(selectedDevice.value)
    closeForm()
  } catch (err) {
    showNotification(
      err?.message || err?.response?.data?.detail || 'Error al guardar el sensor.',
      'error',
    )
  }
}

//
// --- Abrir / Cerrar formularios ---
function openFormForCreate(type) {
  isEditMode.value = false
  sensorToEdit.value = null
  newPingSensor.value = createNewPingSensor()
  newEthernetSensor.value = createNewEthernetSensor()
  formToShow.value = type
}

function openFormForEdit(sensor) {
  isEditMode.value = true
  sensorToEdit.value = sensor

  // Normalizamos config a objeto por si vino como string
  const cfg = typeof sensor.config === 'string' ? safeJsonParse(sensor.config, {}) : sensor.config

  if (sensor.sensor_type === 'ping') {
    const uiData = createNewPingSensor()
    uiData.name = sensor.name
    uiData.config = { ...uiData.config, ...cfg }
    ;(cfg?.alerts || []).forEach((alert) => {
      if (alert.type === 'timeout') uiData.ui_alert_timeout = { enabled: true, ...alert }
      if (alert.type === 'high_latency') uiData.ui_alert_latency = { enabled: true, ...alert }
    })
    newPingSensor.value = uiData
  } else if (sensor.sensor_type === 'ethernet') {
    const uiData = createNewEthernetSensor()
    uiData.name = sensor.name
    uiData.config = { ...uiData.config, ...cfg }
    ;(cfg?.alerts || []).forEach((alert) => {
      if (alert.type === 'speed_change') uiData.ui_alert_speed_change = { enabled: true, ...alert }
      if (alert.type === 'traffic_threshold') uiData.ui_alert_traffic = { enabled: true, ...alert }
    })
    newEthernetSensor.value = uiData
  }
  formToShow.value = sensor.sensor_type
}

function closeForm() {
  formToShow.value = null
  sensorToEdit.value = null
  isEditMode.value = false
}

//
// --- Utilidades ---
async function fetchAllMonitors() {
  try {
    const { data } = await api.get('/monitors')
    allMonitors.value = data
  } catch (err) {
    console.error('Error fetching monitors:', err)
  }
}

async function selectDevice(device) {
  selectedDevice.value = device
  searchQuery.value = ''
  searchResults.value = []
  await fetchAllMonitors()
  const monitor = allMonitors.value.find((m) => m.device_id === device.id)
  if (monitor) {
    currentMonitor.value = monitor
    activeSensors.value = Array.isArray(monitor.sensors)
      ? monitor.sensors.map((s) => ({
          ...s,
          config: typeof s.config === 'string' ? safeJsonParse(s.config, {}) : s.config,
        }))
      : []
  } else {
    currentMonitor.value = null
    activeSensors.value = []
  }
}

function clearSelectedDevice() {
  selectedDevice.value = null
  currentMonitor.value = null
  activeSensors.value = []
  closeForm()
}

async function createMonitorCard() {
  if (!selectedDevice.value) return
  try {
    await api.post('/monitors', { device_id: selectedDevice.value.id })
    showNotification('Tarjeta de monitoreo creada con éxito.', 'success')
    await selectDevice(selectedDevice.value)
  } catch (err) {
    showNotification(err?.response?.data?.detail || 'Error al crear la tarjeta.', 'error')
  }
}

async function deleteSensor(sensorId) {
  if (!confirm('¿Seguro que quieres eliminar este sensor?')) return
  try {
    await api.delete(`/sensors/${sensorId}`)
    activeSensors.value = activeSensors.value.filter((s) => s.id !== sensorId)
    showNotification('Sensor eliminado.', 'success')
  } catch (err) {
    showNotification(err?.response?.data?.detail || 'Error al eliminar el sensor.', 'error')
  }
}

let searchDebounce = null
watch(searchQuery, (newQuery) => {
  clearTimeout(searchDebounce)
  if (newQuery.length < 2) {
    searchResults.value = []
    return
  }
  isLoading.value = true
  searchDebounce = setTimeout(async () => {
    try {
      const { data } = await api.get('/devices/search', {
        params: { search: newQuery },
      })
      searchResults.value = data
    } catch (err) {
      console.error('Error al buscar dispositivos:', err)
    } finally {
      isLoading.value = false
    }
  }, 350)
})
</script>

<template>
  <div>
    <div v-if="notification.show" :class="['notification', notification.type]">
      {{ notification.message }}
    </div>
    <div class="builder-layout">
      <!-- Sección 1 y 2 -->
      <section class="builder-step">
        <h2><span class="step-number">1</span> Seleccionar Dispositivo</h2>
        <div v-if="!selectedDevice">
          <div class="search-wrapper">
            <input
              type="text"
              v-model="searchQuery"
              placeholder="Buscar dispositivo..."
              class="search-input"
            />
          </div>
          <ul v-if="searchResults.length > 0" class="search-results">
            <li v-for="device in searchResults" :key="device.id" @click="selectDevice(device)">
              <strong>{{ device.client_name }}</strong
              ><span>{{ device.ip_address }}</span>
            </li>
          </ul>
        </div>
        <div v-else class="selected-device-card">
          <div>
            <h3>{{ selectedDevice.client_name }}</h3>
            <p>{{ selectedDevice.ip_address }}</p>
          </div>
          <button @click="clearSelectedDevice">Cambiar</button>
        </div>
      </section>

      <section v-if="selectedDevice" class="builder-step">
        <div v-if="!currentMonitor">
          <h2><span class="step-number">2</span> Crear Tarjeta de Monitoreo</h2>
          <button @click="createMonitorCard" class="btn-create">Crear Tarjeta</button>
        </div>
        <div v-else>
          <h2><span class="step-number">2</span> Gestionar Sensores</h2>
          <div class="sensor-list">
            <h4>Sensores Activos</h4>
            <ul v-if="activeSensors.length > 0">
              <li v-for="sensor in activeSensors" :key="sensor.id">
                <div class="sensor-info">
                  <span class="sensor-type-badge" :class="sensor.sensor_type">
                    {{ sensor.sensor_type }}
                  </span>
                  <strong>{{ sensor.name }}</strong>
                  <span
                    v-if="sensor.config?.alerts && sensor.config.alerts.length > 0"
                    class="alert-enabled-badge"
                    >🔔</span
                  >
                </div>
                <div class="sensor-actions">
                  <button @click="openFormForEdit(sensor)" class="action-btn edit-btn">✏️</button>
                  <button @click="deleteSensor(sensor.id)" class="action-btn delete-btn">×</button>
                </div>
              </li>
            </ul>
            <p v-else class="empty-list">No hay sensores configurados.</p>
          </div>
          <div class="add-sensor-section">
            <h4>Añadir Nuevo Sensor</h4>
            <div class="sensor-type-selector">
              <button @click="openFormForCreate('ping')">Añadir Ping</button>
              <button @click="openFormForCreate('ethernet')">Añadir Ethernet</button>
            </div>
          </div>
        </div>
      </section>
    </div>

    <!-- MODAL PARA AÑADIR/EDITAR SENSORES -->
    <div v-if="formToShow" class="modal-overlay" @click.self="closeForm">
      <div class="modal-content">
        <h3>{{ isEditMode ? 'Editar' : 'Añadir' }} Sensor {{ formToShow }}</h3>

        <!-- FORMULARIO PING -->
        <form v-if="formToShow === 'ping'" @submit.prevent="handleSaveSensor" class="config-form">
          <div class="form-group span-3">
            <label>Nombre del Sensor</label>
            <input type="text" v-model="newPingSensor.name" required />
          </div>

          <div class="form-group span-2">
            <label>Tipo de Ping</label>
            <select v-model="newPingSensor.config.ping_type">
              <option value="maestro_to_device">Ping al Dispositivo</option>
              <option value="device_to_external">Ping desde Dispositivo</option>
            </select>
          </div>

          <div class="form-group" v-if="newPingSensor.config.ping_type === 'device_to_external'">
            <label>IP de Destino</label>
            <input type="text" v-model="newPingSensor.config.target_ip" />
          </div>

          <div class="form-group">
            <label>Intervalo (seg)</label>
            <input type="number" v-model.number="newPingSensor.config.interval_sec" required />
            <p class="form-hint">Frecuencia del chequeo.</p>
          </div>

          <div class="form-group">
            <label>Umbral Latencia Visual (ms)</label>
            <input
              type="number"
              v-model.number="newPingSensor.config.latency_threshold_ms"
              required
            />
            <p class="form-hint">Para estado amarillo en dashboard.</p>
          </div>

          <div class="form-group">
            <label>Modo de Visualización</label>
            <select v-model="newPingSensor.config.display_mode">
              <option value="realtime">Tiempo Real</option>
              <option value="average">Promedio</option>
            </select>
            <p class="form-hint">Cómo se muestra en el dashboard.</p>
          </div>

          <div class="form-group" v-if="newPingSensor.config.display_mode === 'average'">
            <label>Nº Pings para Promediar</label>
            <input type="number" v-model.number="newPingSensor.config.average_count" required />
            <p class="form-hint">Suaviza picos de latencia.</p>
          </div>

          <div class="sub-section span-3">
            <h4>Configuración de Alertas</h4>

            <div class="alert-config-item span-3">
              <div class="form-group checkbox-group">
                <input
                  type="checkbox"
                  v-model="newPingSensor.ui_alert_timeout.enabled"
                  id="pingTimeout"
                />
                <label for="pingTimeout">Activar alerta por Timeout</label>
              </div>
              <template v-if="newPingSensor.ui_alert_timeout.enabled">
                <div class="form-group">
                  <label>Enviar a Canal</label>
                  <select v-model="newPingSensor.ui_alert_timeout.channel_id">
                    <option :value="null">-- Seleccionar --</option>
                    <option v-for="c in channels" :key="c.id" :value="c.id">{{ c.name }}</option>
                  </select>
                </div>
                <div class="form-group">
                  <label>Enfriamiento (min)</label>
                  <input
                    type="number"
                    v-model.number="newPingSensor.ui_alert_timeout.cooldown_minutes"
                    min="1"
                  />
                </div>
              </template>
            </div>

            <div class="alert-config-item span-3">
              <div class="form-group checkbox-group">
                <input
                  type="checkbox"
                  v-model="newPingSensor.ui_alert_latency.enabled"
                  id="pingLatency"
                />
                <label for="pingLatency">Activar alerta por Latencia Alta</label>
              </div>
              <template v-if="newPingSensor.ui_alert_latency.enabled">
                <div class="form-group">
                  <label>Umbral de Alerta (ms)</label>
                  <input
                    type="number"
                    v-model.number="newPingSensor.ui_alert_latency.threshold_ms"
                    min="1"
                  />
                  <p class="form-hint">Si la latencia supera este valor.</p>
                </div>
                <div class="form-group">
                  <label>Enviar a Canal</label>
                  <select v-model="newPingSensor.ui_alert_latency.channel_id">
                    <option :value="null">-- Seleccionar --</option>
                    <option v-for="c in channels" :key="c.id" :value="c.id">{{ c.name }}</option>
                  </select>
                </div>
                <div class="form-group">
                  <label>Enfriamiento (min)</label>
                  <input
                    type="number"
                    v-model.number="newPingSensor.ui_alert_latency.cooldown_minutes"
                    min="1"
                  />
                </div>
              </template>
            </div>
          </div>

          <div class="modal-actions span-3">
            <button type="button" @click="closeForm" class="btn-secondary">Cancelar</button>
            <button type="submit" class="btn-add">Guardar Sensor</button>
          </div>
        </form>

        <!-- FORMULARIO ETHERNET -->
        <form
          v-if="formToShow === 'ethernet'"
          @submit.prevent="handleSaveSensor"
          class="config-form"
        >
          <div class="form-group span-2">
            <label>Nombre del Sensor</label>
            <input type="text" v-model="newEthernetSensor.name" required />
          </div>

          <div class="form-group">
            <label>Nombre de Interfaz</label>
            <input type="text" v-model="newEthernetSensor.config.interface_name" required />
            <p class="form-hint">El nombre exacto en el dispositivo.</p>
          </div>

          <div class="form-group">
            <label>Intervalo (seg)</label>
            <input type="number" v-model.number="newEthernetSensor.config.interval_sec" required />
            <p class="form-hint">Frecuencia del chequeo.</p>
          </div>

          <div class="sub-section span-3">
            <h4>Configuración de Alertas</h4>

            <div class="alert-config-item span-3">
              <div class="form-group checkbox-group">
                <input
                  type="checkbox"
                  v-model="newEthernetSensor.ui_alert_speed_change.enabled"
                  id="ethSpeed"
                />
                <label for="ethSpeed">Activar alerta por Cambio de Velocidad</label>
              </div>
              <template v-if="newEthernetSensor.ui_alert_speed_change.enabled">
                <div class="form-group">
                  <label>Enviar a Canal</label>
                  <select v-model="newEthernetSensor.ui_alert_speed_change.channel_id">
                    <option :value="null">-- Seleccionar --</option>
                    <option v-for="c in channels" :key="c.id" :value="c.id">{{ c.name }}</option>
                  </select>
                </div>
                <div class="form-group">
                  <label>Enfriamiento (min)</label>
                  <input
                    type="number"
                    v-model.number="newEthernetSensor.ui_alert_speed_change.cooldown_minutes"
                    min="1"
                  />
                </div>
              </template>
            </div>

            <div class="alert-config-item span-3">
              <div class="form-group checkbox-group">
                <input
                  type="checkbox"
                  v-model="newEthernetSensor.ui_alert_traffic.enabled"
                  id="ethTraffic"
                />
                <label for="ethTraffic">Activar alerta por Umbral de Tráfico</label>
              </div>
              <template v-if="newEthernetSensor.ui_alert_traffic.enabled">
                <div class="form-group">
                  <label>Umbral (Mbps)</label>
                  <input
                    type="number"
                    v-model.number="newEthernetSensor.ui_alert_traffic.threshold_mbps"
                    min="1"
                  />
                </div>
                <div class="form-group">
                  <label>Dirección</label>
                  <select v-model="newEthernetSensor.ui_alert_traffic.direction">
                    <option value="any">Subida o Bajada</option>
                    <option value="rx">Solo Bajada</option>
                    <option value="tx">Solo Subida</option>
                  </select>
                </div>
                <div class="form-group">
                  <label>Enviar a Canal</label>
                  <select v-model="newEthernetSensor.ui_alert_traffic.channel_id">
                    <option :value="null">-- Seleccionar --</option>
                    <option v-for="c in channels" :key="c.id" :value="c.id">{{ c.name }}</option>
                  </select>
                </div>
                <div class="form-group">
                  <label>Enfriamiento (min)</label>
                  <input
                    type="number"
                    v-model.number="newEthernetSensor.ui_alert_traffic.cooldown_minutes"
                    min="1"
                  />
                </div>
              </template>
            </div>
          </div>

          <div class="modal-actions span-3">
            <button type="button" @click="closeForm" class="btn-secondary">Cancelar</button>
            <button type="submit" class="btn-add">Guardar Sensor</button>
          </div>
        </form>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ... (Estilos existentes) ... */
.builder-layout {
  max-width: 900px;
  margin: auto;
  display: flex;
  flex-direction: column;
  gap: 2rem;
}
.builder-step {
  background-color: var(--surface-color);
  padding: 2rem;
  border-radius: 12px;
}
.step-number {
  background-color: var(--blue);
  color: white;
  border-radius: 50%;
  width: 30px;
  height: 30px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-weight: bold;
  margin-right: 1rem;
}
h2,
h4 {
  color: #f1f1f1;
}
.search-wrapper {
  position: relative;
}
.search-input {
  width: 100%;
  padding: 0.8rem;
  background-color: var(--bg-color);
  border: 1px solid var(--primary-color);
  border-radius: 6px;
  color: white;
}
.search-results {
  list-style: none;
  padding: 0;
  margin-top: 0.5rem;
}
.search-results li {
  padding: 0.8rem;
  background-color: var(--bg-color);
  border-radius: 6px;
  margin-bottom: 0.5rem;
  cursor: pointer;
  display: flex;
  justify-content: space-between;
  transition: background-color 0.2s;
}
.search-results li:hover {
  background-color: var(--primary-color);
}
.selected-device-card {
  background-color: var(--bg-color);
  padding: 1rem;
  border-radius: 8px;
  margin-top: 1rem;
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-left: 4px solid var(--green);
}
.selected-device-card button {
  padding: 0.6rem 1.2rem;
  border: 1px solid var(--primary-color);
  background-color: var(--surface-color);
  color: #f1f1f1;
  cursor: pointer;
  border-radius: 6px;
}
.btn-create {
  width: 100%;
  padding: 1rem;
  background-color: var(--green);
  color: var(--bg-color);
  font-size: 1.2rem;
  font-weight: bold;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  margin-top: 1rem;
}
.sensor-list {
  margin-top: 2rem;
  padding-top: 1.5rem;
  border-top: 1px solid var(--primary-color);
}
.sensor-list ul {
  list-style: none;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.8rem;
}
.sensor-list li {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background-color: var(--bg-color);
  padding: 0.75rem 1rem;
  border-radius: 8px;
}
.sensor-info {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}
.sensor-type-badge {
  font-size: 0.75rem;
  font-weight: bold;
  padding: 0.2rem 0.5rem;
  border-radius: 12px;
  text-transform: uppercase;
}
.sensor-type-badge.ping {
  background-color: var(--blue);
  color: white;
}
.sensor-type-badge.ethernet {
  background-color: var(--green);
  color: var(--bg-color);
}
.empty-list {
  color: var(--gray);
  font-style: italic;
}
.add-sensor-section {
  margin-top: 2rem;
  padding-top: 1.5rem;
  border-top: 1px solid var(--primary-color);
}
.sensor-type-selector {
  display: flex;
  gap: 1rem;
  margin-bottom: 1.5rem;
}
.sensor-type-selector button {
  padding: 0.8rem 1.5rem;
  border: 1px solid var(--primary-color);
  background-color: transparent;
  color: var(--gray);
  font-weight: bold;
  cursor: pointer;
  border-radius: 8px;
}
.notification {
  position: fixed;
  top: 90px;
  right: 20px;
  padding: 1rem 1.5rem;
  border-radius: 8px;
  color: white;
  font-weight: bold;
  z-index: 1000;
}
.notification.success {
  background-color: var(--green);
}
.notification.error {
  background-color: var(--error-red);
}
.form-hint {
  font-size: 0.8rem;
  color: var(--gray);
  margin-top: 0.25rem;
}
.alert-enabled-badge {
  font-size: 0.9rem;
}
.sensor-actions {
  display: flex;
  gap: 0.5rem;
}
.action-btn {
  background: none;
  border: none;
  font-size: 1.2rem;
  cursor: pointer;
  padding: 0.2rem 0.5rem;
  border-radius: 4px;
  transition: background-color 0.2s;
}
.edit-btn:hover {
  background-color: var(--blue);
}
.delete-btn {
  font-size: 1.8rem;
  color: var(--gray);
}
.delete-btn:hover {
  background-color: transparent;
  color: var(--secondary-color);
}

/* --- Estilos del Modal --- */
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
  width: 90%;
  max-width: 900px;
  max-height: 90vh;
  overflow-y: auto;
}
.modal-content h3 {
  margin-top: 0;
}
.config-form {
  padding: 1.5rem;
  background-color: var(--bg-color);
  border-radius: 8px;
  border: 1px solid var(--primary-color);
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 1.5rem 1rem;
}
.form-group {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.form-group.span-2 {
  grid-column: span 2;
}
.form-group.span-3 {
  grid-column: span 3;
}
.form-group label {
  font-weight: bold;
  color: var(--gray);
}
.form-group input,
.form-group select {
  padding: 0.8rem;
  background-color: var(--surface-color);
  border: 1px solid var(--primary-color);
  border-radius: 6px;
  color: white;
  width: 100%;
}
.sub-section {
  grid-column: span 3;
  background-color: var(--surface-color);
  padding: 1.5rem;
  border-radius: 8px;
  margin-top: 1rem;
  border: 1px solid var(--primary-color);
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}
.sub-section h4 {
  margin: 0 0 0.5rem 0;
  border-bottom: 1px solid var(--primary-color);
  padding-bottom: 0.5rem;
}
.checkbox-group {
  flex-direction: row;
  align-items: center;
  gap: 0.8rem;
}
.checkbox-group input[type='checkbox'] {
  width: auto;
  accent-color: var(--blue);
}
.alert-config-item {
  border-top: 1px dashed var(--primary-color);
  padding-top: 1.5rem;
  display: contents;
}
.alert-config-item > .form-group {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 1rem;
  grid-column: span 3;
  align-items: center;
}
.alert-config-item > template {
  display: contents;
}
.modal-actions {
  grid-column: span 3;
  display: flex;
  justify-content: flex-end;
  gap: 1rem;
  margin-top: 1.5rem;
}
.modal-actions button {
  padding: 0.8rem 1.5rem;
  border: none;
  border-radius: 6px;
  font-weight: bold;
  cursor: pointer;
}
.btn-secondary {
  background-color: var(--primary-color);
  color: white;
}
.btn-add {
  background-color: var(--blue);
  color: white;
}
</style>
