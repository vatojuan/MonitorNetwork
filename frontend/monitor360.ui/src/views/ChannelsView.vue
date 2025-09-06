<script setup>
import { ref, onMounted } from 'vue'
import api from '@/lib/api' // 👈 cliente central con Authorization automático

const currentTab = ref('channels')
const notification = ref({ show: false, message: '', type: 'success' })

// --- Estado ---
const channels = ref([])
const history = ref([])
const newChannelType = ref('webhook')
const newChannel = ref({
  name: '',
  webhook: { url: '' },
  telegram: { bot_token: '', chat_id: '' },
})
const availableChats = ref([])
const isFetchingChats = ref(false)

onMounted(() => {
  fetchChannels()
  fetchHistory()
})

function showNotification(message, type = 'success') {
  notification.value = { show: true, message, type }
  setTimeout(() => (notification.value.show = false), 4000)
}

function safeParse(jsonLike) {
  try {
    return typeof jsonLike === 'string' ? JSON.parse(jsonLike) : jsonLike || {}
  } catch {
    return {}
  }
}

async function fetchChannels() {
  try {
    const { data } = await api.get('/channels')
    channels.value = (data || []).map((ch) => ({ ...ch, config: safeParse(ch.config) }))
  } catch (err) {
    console.error('Error al cargar canales:', err)
    showNotification(err.response?.data?.detail || 'Error al cargar canales.', 'error')
  }
}

async function fetchHistory() {
  try {
    const { data } = await api.get('/alerts/history')
    history.value = Array.isArray(data) ? data : []
  } catch (err) {
    console.error('Error al cargar historial:', err)
    showNotification(err.response?.data?.detail || 'Error al cargar historial.', 'error')
  }
}

async function handleFetchChats() {
  if (!newChannel.value.telegram.bot_token) {
    return showNotification('Por favor, introduce primero el Bot Token.', 'error')
  }
  isFetchingChats.value = true
  availableChats.value = []
  newChannel.value.telegram.chat_id = ''
  try {
    const { data } = await api.post('/channels/telegram/get_chats', {
      bot_token: newChannel.value.telegram.bot_token,
    })
    if (!Array.isArray(data) || data.length === 0) {
      showNotification(
        'No se encontraron chats recientes. Asegúrate de que tu bot esté en un grupo o hayas iniciado una conversación con él.',
        'error',
      )
    }
    availableChats.value = data || []
  } catch (err) {
    console.error('Error al buscar chats:', err)
    showNotification(err.response?.data?.detail || 'Error al buscar chats.', 'error')
  } finally {
    isFetchingChats.value = false
  }
}

async function handleAddChannel() {
  let payload
  if (newChannelType.value === 'webhook') {
    if (!newChannel.value.name.trim() || !newChannel.value.webhook.url.trim()) {
      return showNotification('Nombre y URL son obligatorios.', 'error')
    }
    payload = {
      name: newChannel.value.name.trim(),
      type: 'webhook',
      config: { url: newChannel.value.webhook.url.trim() },
    }
  } else {
    // telegram
    if (
      !newChannel.value.name.trim() ||
      !newChannel.value.telegram.bot_token.trim() ||
      !newChannel.value.telegram.chat_id
    ) {
      return showNotification('Todos los campos de Telegram son obligatorios.', 'error')
    }
    payload = {
      name: newChannel.value.name.trim(),
      type: 'telegram',
      config: {
        bot_token: newChannel.value.telegram.bot_token.trim(),
        chat_id: newChannel.value.telegram.chat_id,
      },
    }
  }

  try {
    await api.post('/channels', payload)
    showNotification('Canal añadido.', 'success')
    // reset
    newChannel.value = { name: '', webhook: { url: '' }, telegram: { bot_token: '', chat_id: '' } }
    newChannelType.value = 'webhook'
    availableChats.value = []
    fetchChannels()
  } catch (err) {
    console.error('Error al añadir canal:', err)
    showNotification(err.response?.data?.detail || 'Error al añadir canal.', 'error')
  }
}

async function handleDeleteChannel(id) {
  if (!confirm('¿Seguro? Los sensores que usen este canal dejarán de notificar.')) return
  try {
    await api.delete(`/channels/${id}`)
    showNotification('Canal eliminado.', 'success')
    fetchChannels()
  } catch (err) {
    console.error('Error al eliminar canal:', err)
    showNotification(err.response?.data?.detail || 'Error al eliminar canal.', 'error')
  }
}

function formatHistoryDetails(details) {
  try {
    const parsed = safeParse(details)
    return parsed?.reason || details
  } catch {
    return details
  }
}
</script>

<template>
  <div>
    <div v-if="notification.show" :class="['notification', notification.type]">
      {{ notification.message }}
    </div>

    <div class="tabs">
      <button @click="currentTab = 'channels'" :class="{ active: currentTab === 'channels' }">
        Canales de Notificación
      </button>
      <button @click="currentTab = 'history'" :class="{ active: currentTab === 'history' }">
        Historial de Alertas
      </button>
    </div>

    <div class="tab-content">
      <!-- Pestaña de Canales -->
      <section v-if="currentTab === 'channels'" class="grid-layout">
        <div class="control-section">
          <h2><i class="icon">➕</i> Añadir Canal</h2>

          <div class="channel-type-selector">
            <button
              @click="newChannelType = 'webhook'"
              :class="{ active: newChannelType === 'webhook' }"
            >
              Webhook
            </button>
            <button
              @click="newChannelType = 'telegram'"
              :class="{ active: newChannelType === 'telegram' }"
            >
              Telegram Bot
            </button>
          </div>

          <form
            v-if="newChannelType === 'webhook'"
            @submit.prevent="handleAddChannel"
            class="form-layout"
          >
            <p>Envía alertas a una URL. Ideal para Discord o Slack.</p>
            <label>Nombre del Canal</label>
            <input
              type="text"
              v-model="newChannel.name"
              placeholder="Ej: Slack #soporte"
              required
            />
            <label>URL del Webhook</label>
            <input
              type="url"
              v-model="newChannel.webhook.url"
              placeholder="https://hooks.slack.com/..."
              required
            />
            <button type="submit">Añadir Canal Webhook</button>
          </form>

          <form
            v-if="newChannelType === 'telegram'"
            @submit.prevent="handleAddChannel"
            class="form-layout"
          >
            <p>Envía alertas a un chat de Telegram a través de un bot.</p>
            <label>Nombre del Canal</label>
            <input
              type="text"
              v-model="newChannel.name"
              placeholder="Ej: Alertas Telegram"
              required
            />
            <label>Bot Token</label>
            <input
              type="text"
              v-model="newChannel.telegram.bot_token"
              placeholder="Obtenido de BotFather"
              required
            />

            <button
              type="button"
              @click="handleFetchChats"
              :disabled="isFetchingChats"
              class="btn-secondary"
            >
              <span v-if="!isFetchingChats">Buscar Chats</span>
              <span v-else>Buscando...</span>
            </button>

            <label>Chat ID</label>
            <select v-if="availableChats.length > 0" v-model="newChannel.telegram.chat_id" required>
              <option value="" disabled>-- Selecciona un chat --</option>
              <option v-for="chat in availableChats" :key="chat.id" :value="chat.id">
                {{ chat.title }} (ID: {{ chat.id }})
              </option>
            </select>
            <input
              v-else
              type="text"
              v-model="newChannel.telegram.chat_id"
              placeholder="ID del usuario o grupo"
              required
            />

            <div class="form-hint-box">
              <strong>Instrucciones:</strong>
              <ol>
                <li>Introduce el Token de tu bot.</li>
                <li>
                  Asegúrate de que el bot haya sido añadido a un grupo o que le hayas hablado en
                  privado.
                </li>
                <li>Pulsa "Buscar Chats" para ver la lista.</li>
              </ol>
            </div>
            <button type="submit">Añadir Canal de Telegram</button>
          </form>
        </div>

        <div class="control-section">
          <h2><i class="icon">📡</i> Canales Guardados</h2>
          <ul v-if="channels.length > 0" class="item-list">
            <li v-for="channel in channels" :key="channel.id">
              <div class="item-info">
                <strong>{{ channel.name }}</strong>
                <span class="channel-type-badge" :class="channel.type">{{ channel.type }}</span>
              </div>
              <button @click="handleDeleteChannel(channel.id)" class="delete-btn">×</button>
            </li>
          </ul>
          <div v-else class="empty-list">No hay canales configurados.</div>
        </div>
      </section>

      <!-- Pestaña de Historial -->
      <section v-if="currentTab === 'history'" class="control-section full-width">
        <h2><i class="icon">📚</i> Historial de Alertas Enviadas</h2>
        <table v-if="history.length > 0" class="history-table">
          <thead>
            <tr>
              <th>Fecha</th>
              <th>Sensor Afectado</th>
              <th>Detalles del Evento</th>
              <th>Canal Notificado</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in history" :key="item.id">
              <td class="nowrap">{{ new Date(item.timestamp).toLocaleString() }}</td>
              <td>{{ item.sensor_name }}</td>
              <td>{{ formatHistoryDetails(item.details) }}</td>
              <td>{{ item.channel_name }}</td>
            </tr>
          </tbody>
        </table>
        <div v-else class="empty-list">No se han registrado alertas.</div>
      </section>
    </div>

    <div v-if="notification.show" :class="['notification', notification.type]">
      {{ notification.message }}
    </div>
  </div>
</template>

<style scoped>
.tabs {
  display: flex;
  gap: 0.5rem;
  border-bottom: 2px solid var(--primary-color);
  margin-bottom: 2rem;
}
.tabs button {
  padding: 0.8rem 1.5rem;
  border: none;
  background-color: transparent;
  color: var(--gray);
  font-size: 1rem;
  font-weight: bold;
  cursor: pointer;
  border-radius: 8px 8px 0 0;
}
.tabs button.active {
  background-color: var(--primary-color);
  color: white;
}
.grid-layout {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(450px, 1fr));
  gap: 2rem;
}
.control-section {
  background-color: var(--surface-color);
  padding: 2rem;
  border-radius: 12px;
}
.control-section.full-width {
  grid-column: 1 / -1;
}
.control-section h2 {
  margin-top: 0;
  color: white;
  margin-bottom: 1.5rem;
}
.control-section p {
  color: var(--gray);
  margin-bottom: 1.5rem;
  margin-top: -0.5rem;
  font-size: 0.9rem;
}
.form-layout {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}
.form-layout label {
  font-weight: bold;
  color: var(--gray);
  margin-bottom: -0.5rem;
}
.form-layout input,
.form-layout select {
  padding: 0.8rem;
  background-color: var(--bg-color);
  border: 1px solid var(--primary-color);
  border-radius: 6px;
  color: white;
  width: 100%;
}
.form-layout button {
  padding: 0.8rem;
  margin-top: 1rem;
  background-color: var(--blue);
  border: none;
  border-radius: 6px;
  color: white;
  font-weight: bold;
  cursor: pointer;
}
.btn-secondary {
  background-color: var(--primary-color) !important;
  margin-top: 0 !important;
}
.empty-list {
  color: var(--gray);
  text-align: center;
  padding: 2rem;
  border: 2px dashed var(--primary-color);
  border-radius: 8px;
}
.item-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 0.8rem;
}
.item-list li {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background-color: var(--bg-color);
  padding: 1rem;
  border-radius: 8px;
}
.item-info {
  display: flex;
  align-items: center;
  gap: 1rem;
  overflow: hidden;
}
.item-info strong {
  color: white;
}
.delete-btn {
  background: none;
  border: none;
  color: var(--gray);
  font-size: 1.8rem;
  cursor: pointer;
  padding: 0 0.5rem;
}
.history-table {
  width: 100%;
  border-collapse: collapse;
}
.history-table th,
.history-table td {
  padding: 0.75rem 1rem;
  text-align: left;
  border-bottom: 1px solid var(--primary-color);
}
.history-table th {
  color: var(--gray);
}
.nowrap {
  white-space: nowrap;
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
.channel-type-selector {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 1.5rem;
  background-color: var(--bg-color);
  padding: 0.5rem;
  border-radius: 8px;
}
.channel-type-selector button {
  flex-grow: 1;
  padding: 0.6rem;
  border: none;
  background-color: transparent;
  color: var(--gray);
  font-weight: bold;
  cursor: pointer;
  border-radius: 6px;
  transition: all 0.2s ease;
}
.channel-type-selector button.active {
  background-color: var(--blue);
  color: white;
}
.channel-type-badge {
  font-size: 0.75rem;
  font-weight: bold;
  padding: 0.2rem 0.6rem;
  border-radius: 12px;
  text-transform: capitalize;
  color: white;
  width: fit-content;
}
.channel-type-badge.webhook {
  background-color: var(--blue);
}
.channel-type-badge.telegram {
  background-color: #34a8de;
}
.form-hint-box {
  background-color: var(--bg-color);
  border: 1px solid var(--primary-color);
  border-radius: 6px;
  padding: 1rem;
  margin-top: 0.5rem;
  font-size: 0.9rem;
  color: var(--gray);
}
.form-hint-box strong {
  color: var(--font-color);
  display: block;
  margin-bottom: 0.5rem;
}
.form-hint-box ol {
  padding-left: 1.2rem;
  margin: 0;
}
.form-hint-box li {
  margin-bottom: 0.25rem;
}
</style>
