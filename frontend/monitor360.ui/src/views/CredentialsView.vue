<script setup>
import { ref, onMounted } from 'vue'
import api from '@/lib/api' // 👈 usamos el cliente central con Bearer

const savedCredentials = ref([])
const newCredential = ref({ name: '', username: '', password: '' })
const notification = ref({ show: false, message: '', type: 'success' })
const credentialToDeleteId = ref(null)

onMounted(() => {
  fetchCredentials()
})

function showNotification(message, type = 'success') {
  notification.value = { show: true, message, type }
  setTimeout(() => {
    notification.value.show = false
  }, 4000)
}

async function fetchCredentials() {
  try {
    const { data } = await api.get('/credentials')
    savedCredentials.value = Array.isArray(data) ? data : []
  } catch (err) {
    console.error('Error al cargar credenciales:', err)
    showNotification(err.response?.data?.detail || 'Error al cargar las credenciales.', 'error')
  }
}

async function handleAddCredential() {
  if (!newCredential.value.name.trim() || !newCredential.value.username.trim()) {
    showNotification('Los campos Nombre y Usuario son obligatorios.', 'error')
    return
  }
  try {
    await api.post('/credentials', newCredential.value)
    showNotification(`Credencial '${newCredential.value.name}' añadida.`, 'success')
    newCredential.value = { name: '', username: '', password: '' }
    fetchCredentials()
  } catch (err) {
    console.error('Error al añadir credencial:', err)
    showNotification(err.response?.data?.detail || 'Error al añadir.', 'error')
  }
}

function requestDeleteCredential(credentialId) {
  credentialToDeleteId.value = credentialId
}

async function confirmDeleteCredential() {
  if (!credentialToDeleteId.value) return
  try {
    await api.delete(`/credentials/${credentialToDeleteId.value}`)
    showNotification('Credencial eliminada.', 'success')
    fetchCredentials()
  } catch (err) {
    console.error('Error al eliminar credencial:', err)
    showNotification(err.response?.data?.detail || 'Error al eliminar.', 'error')
  } finally {
    credentialToDeleteId.value = null
  }
}
</script>

<template>
  <div>
    <div v-if="notification.show" :class="['notification', notification.type]">
      {{ notification.message }}
    </div>

    <div v-if="credentialToDeleteId" class="modal-overlay">
      <div class="modal-content">
        <h3>Confirmar Eliminación</h3>
        <p>¿Seguro que quieres eliminar esta credencial?</p>
        <div class="modal-actions">
          <button @click="credentialToDeleteId = null" class="btn-secondary">Cancelar</button>
          <button @click="confirmDeleteCredential" class="btn-danger">Eliminar</button>
        </div>
      </div>
    </div>

    <div class="credentials-layout">
      <section class="control-section">
        <h2><i class="icon">➕</i> Añadir Credencial</h2>
        <form @submit.prevent="handleAddCredential" class="credential-form">
          <input
            type="text"
            v-model="newCredential.name"
            placeholder="Nombre (ej: Admin General) *"
          />
          <input type="text" v-model="newCredential.username" placeholder="Usuario *" />
          <input
            type="password"
            v-model="newCredential.password"
            placeholder="Contraseña (opcional)"
          />
          <button type="submit">Añadir Credencial</button>
        </form>
      </section>

      <section class="control-section">
        <h2><i class="icon">📋</i> Credenciales Guardadas</h2>
        <ul v-if="savedCredentials.length > 0" class="credentials-list">
          <li v-for="cred in savedCredentials" :key="cred.id">
            <div class="cred-info">
              <span class="cred-name">{{ cred.name }}</span>
              <span class="cred-user">Usuario: {{ cred.username }}</span>
            </div>
            <button @click="requestDeleteCredential(cred.id)" class="delete-btn">×</button>
          </li>
        </ul>
        <div v-else class="empty-list">No hay credenciales guardadas.</div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.credentials-layout {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
  gap: 2rem;
}
.control-section {
  background-color: var(--surface-color);
  padding: 2rem;
  border-radius: 12px;
}
.control-section h2 {
  margin-top: 0;
  color: white;
}
.credential-form {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}
.credential-form input {
  padding: 0.8rem;
  background-color: var(--bg-color);
  border: 1px solid var(--primary-color);
  border-radius: 6px;
  color: white;
}
.credential-form button {
  padding: 0.8rem;
  background-color: var(--blue);
  border: none;
  border-radius: 6px;
  color: white;
  font-weight: bold;
  cursor: pointer;
}
.empty-list {
  color: var(--gray);
  text-align: center;
  padding: 2rem;
  border: 2px dashed var(--primary-color);
  border-radius: 8px;
}
.credentials-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 0.8rem;
}
.credentials-list li {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background-color: var(--bg-color);
  padding: 1rem;
  border-radius: 8px;
}
.cred-info {
  display: flex;
  flex-direction: column;
}
.cred-name {
  font-weight: bold;
  color: white;
}
.cred-user {
  font-size: 0.9rem;
  color: var(--gray);
}
.delete-btn {
  background: none;
  border: none;
  color: var(--gray);
  font-size: 1.8rem;
  cursor: pointer;
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
  max-width: 400px;
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
</style>
