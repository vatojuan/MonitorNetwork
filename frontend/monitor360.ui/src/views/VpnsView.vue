<script setup>
import { ref, onMounted } from 'vue'
import api from '@/lib/api' // ⬅️ Axios preconfigurado con baseURL y Bearer

const notification = ref({ show: false, message: '', type: 'success' })
function showNotification(message, type = 'success') {
  notification.value = { show: true, message, type }
  setTimeout(() => (notification.value.show = false), 4000)
}

const vpnProfiles = ref([])
const isLoading = ref(false)

// Formulario de creación
const newProfile = ref({
  name: '',
  check_ip: '',
  config_data: '',
})

onMounted(() => {
  fetchVpnProfiles()
})

async function fetchVpnProfiles() {
  isLoading.value = true
  try {
    const { data } = await api.get('/vpns') // ⬅️ SIN /api
    vpnProfiles.value = (data || []).map((p) => ({
      ...p,
      is_default: !!p.is_default,
      _expanded: false,
    }))
  } catch (err) {
    console.error('Error al cargar perfiles VPN:', err)
    showNotification(err.response?.data?.detail || 'Error al cargar perfiles VPN.', 'error')
  } finally {
    isLoading.value = false
  }
}

async function createProfile() {
  if (!newProfile.value.name.trim() || !newProfile.value.config_data.trim()) {
    showNotification('Nombre y Config son obligatorios.', 'error')
    return
  }
  try {
    const body = {
      name: newProfile.value.name.trim(),
      config_data: newProfile.value.config_data,
      check_ip: newProfile.value.check_ip.trim(),
    }
    const { data } = await api.post('/vpns', body) // ⬅️ SIN /api
    vpnProfiles.value.push({ ...data, is_default: !!data.is_default, _expanded: false })
    newProfile.value = { name: '', check_ip: '', config_data: '' }
    showNotification('Perfil VPN creado.', 'success')
  } catch (err) {
    console.error('Error al crear perfil:', err)
    showNotification(err.response?.data?.detail || 'Error al crear perfil.', 'error')
  }
}

async function saveProfile(profile) {
  try {
    const payload = {
      name: profile.name,
      check_ip: profile.check_ip,
      config_data: profile.config_data,
      // is_default se maneja aparte con setDefault
    }
    await api.put(`/vpns/${profile.id}`, payload) // ⬅️ SIN /api
    showNotification('Perfil actualizado.', 'success')
    await fetchVpnProfiles()
  } catch (err) {
    console.error('Error al actualizar perfil:', err)
    showNotification(err.response?.data?.detail || 'Error al actualizar perfil.', 'error')
  }
}

async function setDefault(profile) {
  try {
    await api.put(`/vpns/${profile.id}`, { is_default: true }) // ⬅️ SIN /api
    await fetchVpnProfiles()
    showNotification(`"${profile.name}" ahora es el default.`, 'success')
  } catch (err) {
    console.error('Error al marcar default:', err)
    showNotification(err.response?.data?.detail || 'No se pudo marcar como default.', 'error')
  }
}

async function testProfile(profile) {
  if (!profile.check_ip?.trim()) {
    showNotification('Configurar "check_ip" primero para probar el túnel.', 'error')
    return
  }
  try {
    const payload = { ip_address: profile.check_ip.trim(), vpn_profile_id: profile.id }
    const { data } = await api.post('/devices/test_reachability', payload) // ⬅️ SIN /api
    if (data.reachable) {
      showNotification(`Túnel OK. Alcanzable (${profile.check_ip}).`, 'success')
    } else {
      showNotification(data.detail || 'No alcanzable a través del túnel.', 'error')
    }
  } catch (err) {
    console.error('Error al probar túnel:', err)
    showNotification(err.response?.data?.detail || 'Error al probar el túnel.', 'error')
  }
}

async function deleteProfile(profile) {
  if (!confirm(`¿Eliminar el perfil "${profile.name}"?`)) return
  try {
    await api.delete(`/vpns/${profile.id}`) // ⬅️ SIN /api
    vpnProfiles.value = vpnProfiles.value.filter((p) => p.id !== profile.id)
    showNotification('Perfil eliminado.', 'success')
  } catch (err) {
    // Si está asociado a un dispositivo, el backend devuelve 400 con detalle.
    console.error('Error al eliminar perfil:', err)
    showNotification(err.response?.data?.detail || 'No se pudo eliminar el perfil.', 'error')
  }
}
</script>

<template>
  <div class="page-wrap">
    <h1>Perfiles VPN</h1>

    <!-- Crear nuevo perfil -->
    <section class="control-section">
      <h2><i class="icon">➕</i> Crear Perfil</h2>
      <div class="grid-2">
        <div>
          <label>Nombre *</label>
          <input v-model="newProfile.name" type="text" placeholder="Nombre del perfil" />
        </div>
        <div>
          <label>Check IP (opcional)</label>
          <input v-model="newProfile.check_ip" type="text" placeholder="Ej: 192.168.81.4" />
          <small>IP dentro del túnel para prueba rápida.</small>
        </div>
      </div>

      <div class="stack">
        <label>Config WireGuard *</label>
        <textarea
          v-model="newProfile.config_data"
          rows="10"
          spellcheck="false"
          placeholder="[Interface]&#10;PrivateKey = ...&#10;Address = ...&#10;DNS = ...&#10;&#10;[Peer]&#10;PublicKey = ...&#10;AllowedIPs = ...&#10;Endpoint = ..."
        />
      </div>

      <div class="actions-row">
        <button class="btn-primary" @click="createProfile">Crear Perfil</button>
      </div>
    </section>

    <!-- Listado / edición -->
    <section class="control-section">
      <h2><i class="icon">🗂️</i> Perfiles existentes</h2>
      <div v-if="isLoading" class="loading-text">Cargando...</div>
      <div v-else-if="!vpnProfiles.length" class="empty">No hay perfiles. Creá uno arriba.</div>

      <ul v-else class="vpn-list">
        <li v-for="p in vpnProfiles" :key="p.id" class="vpn-card">
          <div class="vpn-header" @click="p._expanded = !p._expanded">
            <div class="title">
              <span class="name">{{ p.name }}</span>
              <span v-if="p.is_default" class="badge-default" title="Default">★ Default</span>
            </div>
            <button class="btn-toggle" type="button">
              {{ p._expanded ? 'Ocultar' : 'Mostrar' }}
            </button>
          </div>

          <div v-if="p._expanded" class="vpn-body">
            <div class="grid-2">
              <div>
                <label>Nombre</label>
                <input v-model="p.name" type="text" />
              </div>
              <div>
                <label>Check IP</label>
                <input
                  v-model="p.check_ip"
                  type="text"
                  placeholder="IP para testear a través del túnel"
                />
              </div>
            </div>

            <div class="stack">
              <label>Config WireGuard</label>
              <textarea v-model="p.config_data" rows="12" spellcheck="false"></textarea>
            </div>

            <div class="actions-row">
              <button class="btn-primary" @click.stop="saveProfile(p)">Guardar cambios</button>
              <button
                class="btn-default"
                v-if="!p.is_default"
                @click.stop="setDefault(p)"
                title="Marcar este perfil como predeterminado"
              >
                Marcar como default
              </button>
              <button class="btn-secondary" @click.stop="testProfile(p)">Probar túnel</button>
              <button class="btn-danger" @click.stop="deleteProfile(p)">Eliminar</button>
            </div>
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
  --badge: #f4d03f;
}

.page-wrap {
  color: var(--font-color);
}
h1 {
  margin: 0 0 1rem 0;
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

.control-section {
  background: var(--panel);
  padding: 1rem;
  border-radius: 10px;
  margin-bottom: 1rem;
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

.stack {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

input,
textarea {
  width: 100%;
  background: #0e0e0e;
  color: var(--font-color);
  border: 1px solid #2a2a2a;
  border-radius: 8px;
  padding: 0.6rem 0.7rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
}
textarea {
  white-space: pre;
}

.actions-row {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
  margin-top: 0.5rem;
}
.btn-primary,
.btn-secondary,
.btn-danger,
.btn-default,
.btn-toggle {
  border-radius: 8px;
  padding: 0.6rem 0.9rem;
  cursor: pointer;
  border: 1px solid transparent;
  color: white;
}
.btn-primary {
  background: var(--green);
}
.btn-secondary {
  background: transparent;
  color: var(--font-color);
  border-color: var(--primary-color);
}
.btn-default {
  background: #2b5cb3;
}
.btn-danger {
  background: var(--secondary-color);
}
.btn-toggle {
  background: #2a2a2a;
  color: var(--font-color);
}

.loading-text {
  color: var(--gray);
}
.empty {
  color: var(--gray);
  padding: 0.5rem 0;
}

.vpn-list {
  list-style: none;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.vpn-card {
  background: #0e0e0e;
  border: 1px solid #2a2a2a;
  border-radius: 10px;
}
.vpn-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.8rem 1rem;
  border-bottom: 1px solid #2a2a2a;
}
.title {
  display: flex;
  align-items: center;
  gap: 0.6rem;
}
.name {
  font-weight: 700;
}
.badge-default {
  border: 1px solid var(--badge);
  color: #161616;
  background: var(--badge);
  border-radius: 6px;
  padding: 0.1rem 0.4rem;
  font-size: 0.8rem;
}
.vpn-body {
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 1rem;
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
