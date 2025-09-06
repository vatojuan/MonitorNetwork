<script setup>
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import api from '@/lib/api' // ⬅️ Axios preconfigurado con Bearer
import { supabase } from '@/lib/supabase'

const router = useRouter()

// ===== UI Estado general =====
const currentTab = ref('add')
const notification = ref({ show: false, message: '', type: 'success' })
function showNotification(message, type = 'success') {
  notification.value = { show: true, message, type }
  setTimeout(() => (notification.value.show = false), 4000)
}

// ===== Usuario (mostrar email y logout) =====
const userEmail = ref('')
async function loadUser() {
  const { data } = await supabase.auth.getUser()
  userEmail.value = data.user?.email || ''
}
async function logout() {
  await supabase.auth.signOut()
  showNotification('Sesión cerrada.', 'success')
  router.push('/login')
}

// ===== Estado “Alta en un paso” =====
const addForm = ref({
  client_name: '',
  ip_address: '',
  mac_address: '',
  node: '',
  connection_method: 'vpn', // 'vpn' | 'direct' | 'maestro'
  vpn_profile_id: null, // si connection_method = 'vpn'
  maestro_id: null, // si connection_method = 'maestro'
})

const isSubmitting = ref(false)
const isTesting = ref(false)
const testResult = ref(null)

// ===== Listados y soporte =====
const allDevices = ref([])
const vpnProfiles = ref([])
const isLoadingDevices = ref(false)
const deletingId = ref(null) // id del dispositivo que se está borrando

const maestros = computed(() => allDevices.value.filter((d) => d.is_maestro))

async function fetchAllDevices() {
  isLoadingDevices.value = true
  try {
    const { data } = await api.get('/devices') // ⬅️ sin /api
    allDevices.value = Array.isArray(data) ? data : []
  } catch (err) {
    console.error('Error al cargar dispositivos:', err)
    showNotification(err.response?.data?.detail || 'Error al cargar dispositivos.', 'error')
  } finally {
    isLoadingDevices.value = false
  }
}

async function fetchVpnProfiles() {
  try {
    const { data } = await api.get('/vpns') // ⬅️ sin /api
    vpnProfiles.value = Array.isArray(data) ? data : []
  } catch (err) {
    console.error('Error al cargar perfiles VPN:', err)
    showNotification(err.response?.data?.detail || 'Error al cargar perfiles VPN.', 'error')
  }
}

/**
 * Alta en UN paso:
 *  - VPN: backend levanta túnel, valida credencial y crea el dispositivo.
 *  - Directo: conecta contra la IP y crea.
 *  - Maestro: a través de un Maestro existente.
 */
async function handleAddDeviceOneStep() {
  // Validaciones mínimas
  if (!addForm.value.client_name?.trim() || !addForm.value.ip_address?.trim()) {
    showNotification('Completá Cliente e IP.', 'error')
    return
  }
  if (addForm.value.connection_method === 'vpn' && !addForm.value.vpn_profile_id) {
    showNotification('Seleccioná un Perfil VPN o cambiá el método de conexión.', 'error')
    return
  }
  if (addForm.value.connection_method === 'maestro' && !addForm.value.maestro_id) {
    showNotification('Seleccioná un Maestro o cambiá el método de conexión.', 'error')
    return
  }

  const payload = {
    client_name: addForm.value.client_name,
    ip_address: addForm.value.ip_address,
    mac_address: addForm.value.mac_address || '',
    node: addForm.value.node || '',
    maestro_id: addForm.value.connection_method === 'maestro' ? addForm.value.maestro_id : null,
    vpn_profile_id: addForm.value.connection_method === 'vpn' ? addForm.value.vpn_profile_id : null,
  }

  isSubmitting.value = true
  try {
    const { data } = await api.post('/devices/manual', payload) // ⬅️ sin /api
    showNotification(`Dispositivo "${data.client_name}" creado.`, 'success')
    resetAddForm()
    fetchAllDevices()
    currentTab.value = 'manage'
  } catch (err) {
    console.error('Error al añadir dispositivo (one-step):', err)
    showNotification(err.response?.data?.detail || 'Error al añadir dispositivo.', 'error')
  } finally {
    isSubmitting.value = false
  }
}

/** Probar conexión antes de crear (opcional) */
async function handleTestReachability() {
  if (!addForm.value.ip_address?.trim()) {
    showNotification('Ingresá la IP a probar.', 'error')
    return
  }
  const payload = { ip_address: addForm.value.ip_address }
  if (addForm.value.connection_method === 'vpn') {
    payload.vpn_profile_id = addForm.value.vpn_profile_id
  } else if (addForm.value.connection_method === 'maestro') {
    payload.maestro_id = addForm.value.maestro_id
  }

  isTesting.value = true
  testResult.value = null
  try {
    const { data } = await api.post('/devices/test_reachability', payload) // ⬅️ sin /api
    testResult.value = data
    if (data.reachable) {
      showNotification('¡Conexión OK! Podés crear el dispositivo.', 'success')
    } else {
      showNotification(data.detail || 'Dispositivo no alcanzable.', 'error')
    }
  } catch (err) {
    console.error('Error al probar la conexión:', err)
    showNotification(err.response?.data?.detail || 'Error al probar la conexión.', 'error')
    testResult.value = { reachable: false }
  } finally {
    isTesting.value = false
  }
}

function resetAddForm() {
  addForm.value = {
    client_name: '',
    ip_address: '',
    mac_address: '',
    node: '',
    connection_method: 'vpn',
    vpn_profile_id: null,
    maestro_id: null,
  }
  testResult.value = null
}

/* -------- Gestión de maestros / asociación VPN (sección manage) -------- */
async function promoteToMaestro(device) {
  if (!confirm(`¿Promover a "${device.client_name}" como Maestro?`)) return
  try {
    await api.put(`/devices/${device.id}/promote`) // ⬅️ sin /api
    showNotification(`${device.client_name} ahora es Maestro.`, 'success')
    fetchAllDevices()
  } catch (err) {
    console.error('Error al promover a maestro:', err)
    showNotification(err.response?.data?.detail || 'Error al promover.', 'error')
  }
}

async function handleVpnAssociation(device) {
  const vpnId = device.vpn_profile_id === '' ? null : device.vpn_profile_id
  try {
    await api.put(`/devices/${device.id}/associate_vpn`, { vpn_profile_id: vpnId }) // ⬅️
    showNotification('Asociación de VPN actualizada.', 'success')
    await fetchAllDevices()
  } catch (err) {
    console.error('Error al asociar VPN:', err)
    showNotification(err.response?.data?.detail || 'Error al asociar la VPN.', 'error')
    fetchAllDevices()
  }
}

/* ------------------- Eliminar dispositivo ------------------- */
async function deleteDevice(device) {
  const extra = device.is_maestro
    ? '\n\nATENCIÓN: este dispositivo es Maestro. Los equipos que dependan de él podrían necesitar reconfigurarse.'
    : ''
  if (!confirm(`¿Eliminar "${device.client_name}" (${device.ip_address})?${extra}`)) return

  try {
    deletingId.value = device.id
    await api.delete(`/devices/${device.id}`) // ⬅️ sin /api
    showNotification('Dispositivo eliminado.', 'success')
    await fetchAllDevices()
  } catch (err) {
    console.error('[DELETE device]', err)
    showNotification(err.response?.data?.detail || 'No se pudo eliminar el dispositivo.', 'error')
  } finally {
    deletingId.value = null
  }
}

// ===== Lifecycle =====
onMounted(async () => {
  await loadUser()
  // Si el guard te trajo aquí, ya hay sesión y el cliente api pondrá el Bearer.
  fetchAllDevices()
  fetchVpnProfiles()
})
</script>

<template>
  <div class="page-wrap">
    <header class="topbar">
      <h1>Dispositivos</h1>
      <div class="auth-box">
        <span v-if="userEmail" class="user-pill">{{ userEmail }}</span>
        <button class="btn-secondary" @click="logout">Cerrar sesión</button>
      </div>
    </header>

    <div class="tabs">
      <button :class="{ active: currentTab === 'add' }" @click="currentTab = 'add'">Agregar</button>
      <button :class="{ active: currentTab === 'manage' }" @click="currentTab = 'manage'">
        Gestionar
      </button>
    </div>

    <!-- ==================== TAB: ALTA EN UN PASO ==================== -->
    <section v-if="currentTab === 'add'" class="control-section">
      <h2><i class="icon">➕</i> Alta de dispositivo (en un paso)</h2>

      <form @submit.prevent="handleAddDeviceOneStep" class="form-layout">
        <div class="grid-2">
          <div>
            <label>Cliente *</label>
            <input
              type="text"
              v-model="addForm.client_name"
              placeholder="Nombre del cliente"
              required
            />
          </div>
          <div>
            <label>IP del dispositivo *</label>
            <input
              type="text"
              v-model="addForm.ip_address"
              placeholder="Ej: 192.168.81.4"
              required
            />
          </div>
        </div>

        <div class="grid-2">
          <div>
            <label>Método de conexión</label>
            <select v-model="addForm.connection_method">
              <option value="vpn">A través de un Perfil VPN</option>
              <option value="direct">Conexión directa (backend → dispositivo)</option>
              <option value="maestro">A través de un Maestro existente</option>
            </select>
          </div>

          <div v-if="addForm.connection_method === 'vpn'">
            <label>Perfil VPN</label>
            <select v-model="addForm.vpn_profile_id" required>
              <option :value="null" disabled>-- Selecciona un Perfil VPN --</option>
              <option v-for="vpn in vpnProfiles" :key="vpn.id" :value="vpn.id">
                {{ vpn.name }}
              </option>
            </select>
            <small v-if="!vpnProfiles.length">No hay perfiles. Creá uno en la sección VPN.</small>
          </div>

          <div v-if="addForm.connection_method === 'maestro'">
            <label>Maestro</label>
            <select v-model="addForm.maestro_id" required>
              <option :value="null" disabled>-- Selecciona un Maestro --</option>
              <option v-for="m in maestros" :key="m.id" :value="m.id">
                {{ m.client_name }} — {{ m.ip_address }}
              </option>
            </select>
            <small>El Maestro debe tener reachability hacia el destino.</small>
          </div>
        </div>

        <div class="grid-2">
          <div>
            <label>MAC (opcional)</label>
            <input type="text" v-model="addForm.mac_address" placeholder="AA:BB:CC:DD:EE:FF" />
          </div>
          <div>
            <label>Node (opcional)</label>
            <input type="text" v-model="addForm.node" placeholder="Nodo / etiqueta" />
          </div>
        </div>

        <div class="actions-row">
          <button class="btn-primary" type="submit" :disabled="isSubmitting">
            {{ isSubmitting ? 'Creando...' : 'Crear dispositivo' }}
          </button>
          <button
            class="btn-secondary"
            type="button"
            @click="handleTestReachability"
            :disabled="isTesting"
          >
            {{ isTesting ? 'Probando...' : 'Probar conexión (opcional)' }}
          </button>
        </div>

        <div v-if="testResult" class="test-box" :class="testResult.reachable ? 'ok' : 'error'">
          <strong>Resultado de prueba:</strong>
          <span v-if="testResult.reachable">Alcanzable ✅</span>
          <span v-else>No alcanzable ❌ — {{ testResult.detail || 'Sin detalle' }}</span>
        </div>
      </form>
    </section>

    <!-- ==================== TAB: GESTIONAR ==================== -->
    <section v-if="currentTab === 'manage'" class="control-section">
      <h2><i class="icon">👑</i> Gestionar Dispositivos y Maestros</h2>
      <div v-if="isLoadingDevices" class="loading-text">Cargando...</div>
      <ul v-else class="device-list">
        <li v-for="device in allDevices" :key="device.id">
          <div class="device-info">
            <strong>{{ device.client_name }}</strong>
            <span>{{ device.ip_address }}</span>
          </div>
          <div class="actions">
            <div v-if="device.is_maestro" class="maestro-actions">
              <select v-model="device.vpn_profile_id" @change="handleVpnAssociation(device)">
                <option :value="null">Sin VPN / Conexión Directa</option>
                <option v-for="vpn in vpnProfiles" :key="vpn.id" :value="vpn.id">
                  {{ vpn.name }}
                </option>
              </select>
              <span class="maestro-badge">Maestro</span>
            </div>
            <button v-else @click="promoteToMaestro(device)" class="btn-promote">
              Promover a Maestro
            </button>

            <!-- Eliminar -->
            <button
              class="btn-danger"
              :disabled="deletingId === device.id"
              @click="deleteDevice(device)"
              title="Eliminar dispositivo"
            >
              {{ deletingId === device.id ? 'Eliminando...' : 'Eliminar' }}
            </button>
          </div>
        </li>
      </ul>
    </section>

    <div v-if="notification.show" class="notification" :class="notification.type">
      {{ notification.message }}
    </div>
  </div>
</template>

<style scoped>
:root {
  --bg-color: #121212;
  --panel: #1b1b1b;
  --font-color: #eaeaea;
  --gray: #9aa0a6;
  --primary-color: #6ab4ff;
  --secondary-color: #ff6b6b;
  --green: #2ea043;
  --error-red: #d9534f;
}

.page-wrap {
  color: var(--font-color);
}
.topbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.75rem;
}
.auth-box {
  display: flex;
  gap: 0.5rem;
  align-items: center;
}
.user-pill {
  padding: 0.25rem 0.5rem;
  border-radius: 999px;
  border: 1px solid #2a2a2a;
  font-size: 0.9rem;
}

h1 {
  margin: 0;
}
h2 {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.75rem;
}
.icon {
  font-style: normal;
}

.tabs {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 1rem;
}
.tabs > button {
  background: transparent;
  color: var(--font-color);
  border: 1px solid var(--primary-color);
  border-radius: 8px;
  padding: 0.5rem 0.8rem;
  cursor: pointer;
}
.tabs > button.active {
  background: var(--primary-color);
  color: #0b1220;
}

.control-section {
  background: var(--panel);
  padding: 1rem;
  border-radius: 10px;
}
.form-layout {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}
.grid-2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1rem;
}
@media (max-width: 900px) {
  .grid-2 {
    grid-template-columns: 1fr;
  }
}

input,
select {
  width: 100%;
  background: #0e0e0e;
  color: var(--font-color);
  border: 1px solid #2a2a2a;
  border-radius: 8px;
  padding: 0.6rem 0.7rem;
}

.actions-row {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}
.btn-primary,
.btn-secondary,
.btn-promote,
.btn-danger {
  border-radius: 8px;
  padding: 0.6rem 0.9rem;
  cursor: pointer;
  border: 1px solid transparent;
}
.btn-primary {
  background: var(--green);
  color: white;
}
.btn-secondary {
  background: transparent;
  color: var(--font-color);
  border-color: var(--primary-color);
}
.btn-promote {
  background: var(--primary-color);
  color: #0b1220;
}
.btn-danger {
  background: var(--error-red);
  color: #fff;
}

.test-box {
  padding: 0.8rem;
  border-radius: 8px;
  background: #0e0e0e;
  border: 1px solid #2a2a2a;
}
.test-box.ok {
  border-color: var(--green);
}
.test-box.error {
  border-color: var(--error-red);
}

.device-list {
  list-style: none;
  padding: 0;
  margin-top: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.device-list li {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: var(--bg-color);
  border-radius: 8px;
  padding: 1rem;
  gap: 1rem;
}
.device-info {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}
.actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}
.maestro-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.maestro-badge {
  border: 1px solid var(--primary-color);
  color: var(--primary-color);
  padding: 0.15rem 0.4rem;
  border-radius: 6px;
  font-size: 0.8rem;
}

.loading-text {
  color: var(--gray);
}

.notification {
  position: fixed;
  bottom: 20px;
  right: 20px;
  z-index: 2000;
  padding: 1rem 1.2rem;
  border-radius: 8px;
  color: white;
  font-weight: 600;
}
.notification.success {
  background: var(--green);
}
.notification.error {
  background: var(--error-red);
}
</style>
