<!-- src/App.vue -->
<script setup>
import { ref, reactive, onMounted, onBeforeUnmount, computed } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import { supabase } from '@/lib/supabase'
import logo from '@/assets/logo.svg'

// 🔌 WS
import { connectWebSocketWhenAuthenticated, addWsListener, getCurrentWebSocket } from '@/lib/ws'

const route = useRoute()
const router = useRouter()

// Auth/session
const session = ref(null)
const userEmail = ref('')

// Ocultar chrome en rutas que lo pidan
const hideChrome = computed(() => route.meta?.hideChrome === true)

// Estado de tiempo real para mostrar en el header
const live = reactive({
  connected: false,
  lastMsgIso: null, // ISO de la última actualización (batch o update)
  lastPingMs: null, // latency_ms de sensor ping
  lastSensorId: null, // último sensor_id que actualizó
})

// Tooltip del chip de estado
const liveTooltip = computed(() => {
  const parts = []
  parts.push(live.connected ? 'WS: conectado' : 'WS: reconectando')
  if (live.lastPingMs != null) parts.push(`ping: ${Math.round(live.lastPingMs)} ms`)
  if (live.lastMsgIso) parts.push(`última: ${new Date(live.lastMsgIso).toLocaleString()}`)
  if (live.lastSensorId != null) parts.push(`sensor_id: ${live.lastSensorId}`)
  return parts.join(' · ')
})

async function getSession() {
  const { data } = await supabase.auth.getSession()
  session.value = data.session
  userEmail.value = data.session?.user?.email || ''
}

async function logout() {
  try {
    await supabase.auth.signOut()
  } catch (err) {
    if (import.meta?.env?.DEV) {
      console.warn('[auth] signOut error:', err?.message || err)
    }
  }
  session.value = null
  userEmail.value = ''
  router.push('/login')
}

// Mantener live.connected coherente incluso si el WS rota internamente
let wsStateTimer = null
function startWsStatePolling() {
  stopWsStatePolling()
  wsStateTimer = setInterval(() => {
    try {
      const ws = getCurrentWebSocket()
      const state = ws?.readyState
      live.connected = state === WebSocket.OPEN
    } catch (err) {
      // si algo falla leyendo el estado, marcamos desconectado y seguimos
      live.connected = false
      if (import.meta?.env?.DEV) {
        console.debug('[WS] state poll error:', err?.message || err)
      }
    }
  }, 1500)
}
function stopWsStatePolling() {
  if (wsStateTimer) {
    clearInterval(wsStateTimer)
    wsStateTimer = null
  }
}

let offWsListener = null
let offAuthSub = null

onMounted(async () => {
  await getSession()

  // Suscripción a cambios de sesión
  const { data: sub } = supabase.auth.onAuthStateChange(async (_event, s) => {
    session.value = s
    userEmail.value = s?.user?.email || ''
  })
  offAuthSub = () => {
    try {
      sub?.subscription?.unsubscribe()
    } catch (err) {
      if (import.meta?.env?.DEV) {
        console.debug('[auth] unsubscribe error:', err?.message || err)
      }
    }
  }

  // Asegurar conexión WS global (idempotente)
  try {
    await connectWebSocketWhenAuthenticated()
  } catch (err) {
    if (import.meta?.env?.DEV) {
      console.warn('[WS] connect on App.vue failed (continuamos):', err?.message || err)
    }
  }

  // Polling del estado de conexión (cubre rotaciones de socket)
  startWsStatePolling()

  // Listener de mensajes en vivo
  offWsListener = addWsListener((msg) => {
    if (!msg || typeof msg !== 'object') return

    // Última marca de tiempo (preferimos timestamp del mensaje)
    const ts = (typeof msg.timestamp === 'string' && msg.timestamp) || new Date().toISOString()
    live.lastMsgIso = ts

    // Si es ping, reflejamos latency y sensor_id
    if (msg.sensor_type === 'ping' && Object.prototype.hasOwnProperty.call(msg, 'sensor_id')) {
      live.lastSensorId = msg.sensor_id
      if (Object.prototype.hasOwnProperty.call(msg, 'latency_ms')) {
        live.lastPingMs = msg.latency_ms
      }
    }
  })
})

onBeforeUnmount(() => {
  try {
    if (typeof offWsListener === 'function') offWsListener()
  } catch (err) {
    if (import.meta?.env?.DEV) {
      console.debug('[WS] offWsListener error:', err?.message || err)
    }
  }
  try {
    if (typeof offAuthSub === 'function') offAuthSub()
  } catch (err) {
    if (import.meta?.env?.DEV) {
      console.debug('[auth] offAuthSub error:', err?.message || err)
    }
  }
  stopWsStatePolling()
})
</script>

<template>
  <div id="app-layout">
    <!-- Header SIEMPRE visible para mantener logo/título -->
    <header class="main-header">
      <div class="header-container">
        <!-- Logo + Título (siempre visibles) -->
        <div class="title-group">
          <img :src="logo" alt="Monitor360 Logo" class="logo" />
          <h1 class="title-gradient">Monitor360</h1>
        </div>

        <!-- Navegación (oculta en páginas con hideChrome, p.ej. login) -->
        <nav class="main-nav" v-if="!hideChrome">
          <RouterLink to="/">Dashboard</RouterLink>
          <RouterLink to="/monitor-builder">Añadir Monitor</RouterLink>
          <RouterLink to="/devices">Gestionar Dispositivos</RouterLink>
          <RouterLink to="/credentials">Credenciales</RouterLink>
          <RouterLink to="/channels">Canales</RouterLink>
          <RouterLink to="/vpns">VPNs</RouterLink>
        </nav>

        <!-- Usuario + Logout + Chip tiempo real (oculto cuando hideChrome) -->
        <div v-if="!hideChrome" class="right-box">
          <!-- Chip de estado WS -->
          <div
            class="realtime-chip"
            :class="{ ok: live.connected, warn: !live.connected }"
            :title="liveTooltip"
            aria-label="Estado tiempo real"
          >
            <span class="dot" />
            <span class="label">{{ live.connected ? 'Tiempo real' : 'Reconectando…' }}</span>
            <span v-if="live.lastPingMs != null" class="sep">•</span>
            <span v-if="live.lastPingMs != null" class="metric"
              >ping {{ Math.round(live.lastPingMs) }} ms</span
            >
            <span v-if="live.lastMsgIso" class="sep">•</span>
            <span v-if="live.lastMsgIso" class="metric">
              {{ new Date(live.lastMsgIso).toLocaleTimeString() }}
            </span>
          </div>

          <div v-if="session" class="user-box">
            <span class="user-icon" :title="userEmail" aria-label="Usuario">
              <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
                <path
                  d="M12 12c2.761 0 5-2.686 5-6s-2.239-6-5-6-5 2.686-5 6 2.239 6 5 6zm0 2c-4.418 0-8 2.91-8 6.5V22h16v-1.5c0-3.59-3.582-6.5-8-6.5z"
                />
              </svg>
            </span>
            <button class="btn-logout" @click="logout">Cerrar sesión</button>
          </div>
        </div>
      </div>
    </header>

    <main class="main-content">
      <RouterView />
    </main>
  </div>
</template>

<style>
:root {
  --bg-color: #1a1a2e;
  --surface-color: #16213e;
  --primary-color: #0f3460;
  --secondary-color: #e94560;
  --font-color: #e0e0e0;
  --green: #3ddc84;
  --gray: #8d8d8d;
  --blue: #5372f0;
  --error-red: #f87171;
}

*,
*::before,
*::after {
  box-sizing: border-box;
}
html,
body,
#app {
  height: 100%;
}

body {
  margin: 0;
  background-color: var(--bg-color);
  color: var(--font-color);
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}

#app-layout {
  display: flex;
  flex-direction: column;
  min-height: 100vh;
}

/* ===== Header ===== */
.main-header {
  width: 100%;
  background-color: var(--surface-color);
  border-bottom: 1px solid var(--primary-color);
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.2);
  position: sticky;
  top: 0;
  z-index: 1000;
}

.header-container {
  display: flex;
  align-items: center;
  justify-content: space-between;
  max-width: 100%;
  margin: 0;
  padding: 1rem 2rem;
}

.title-group {
  display: flex;
  align-items: center;
  gap: 1.25rem;
  margin-right: auto;
}

.logo {
  height: 80px;
  width: auto;
  display: block;
}

/* ===== Título con gradiente ===== */
.title-gradient {
  font-size: 2.25rem;
  font-weight: 700;
  margin: 0;
  background: linear-gradient(90deg, #00c896, #23d7ff);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

/* ===== Nav centrado ===== */
.main-nav {
  flex-grow: 1;
  display: flex;
  justify-content: center;
  gap: 1.25rem;
}

.main-nav a {
  color: var(--gray);
  text-decoration: none;
  padding: 0.5rem 1.25rem;
  font-weight: 600;
  border-radius: 6px;
  transition: all 0.2s ease-in-out;
  border: 1px solid transparent;
  white-space: nowrap;
}

.main-nav a.router-link-exact-active {
  color: #fff;
  background-color: var(--blue);
}

.main-nav a:hover:not(.router-link-exact-active) {
  background-color: var(--primary-color);
  color: #fff;
}

/* ===== Lado derecho header ===== */
.right-box {
  display: flex;
  align-items: center;
  gap: 1rem;
}

/* ===== Chip estado tiempo real ===== */
.realtime-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.35rem 0.6rem;
  border-radius: 999px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  background: rgba(255, 255, 255, 0.05);
  color: #eaeaea;
  font-size: 0.85rem;
}
.realtime-chip .dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
  background: #ffbf47; /* por defecto (warn) */
}
.realtime-chip.ok .dot {
  background: #3ddc84;
}
.realtime-chip.warn .dot {
  background: #ffbf47;
}
.realtime-chip .label {
  font-weight: 600;
}
.realtime-chip .sep {
  opacity: 0.6;
}
.realtime-chip .metric {
  opacity: 0.95;
}

/* ===== User box ===== */
.user-box {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.user-icon {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  border: 1px solid #2a2a2a;
  background: #0e0e0e;
  display: grid;
  place-items: center;
  color: #eaeaea;
}

.user-icon svg {
  fill: #eaeaea;
  display: block;
}

.btn-logout {
  background: transparent;
  border: 1px solid var(--secondary-color);
  border-radius: 6px;
  color: var(--secondary-color);
  padding: 0.4rem 0.8rem;
  font-size: 0.85rem;
  cursor: pointer;
  transition: 0.2s;
}
.btn-logout:hover {
  background: var(--secondary-color);
  color: #fff;
}

/* ===== Contenido ===== */
.main-content {
  width: 100%;
  padding: 2rem;
  flex-grow: 1;
}

/* ===== Responsivo ===== */
@media (max-width: 1024px) {
  .header-container {
    flex-direction: column;
    justify-content: center;
    gap: 1rem;
  }
  .title-group {
    margin-right: 0;
    justify-content: center;
  }
  .logo {
    height: 64px;
  }
  .title-gradient {
    font-size: 2rem;
  }
}
@media (max-width: 768px) {
  .logo {
    height: 56px;
  }
  .title-gradient {
    font-size: 1.8rem;
  }
}
@media (max-width: 480px) {
  .logo {
    height: 48px;
  }
  .title-gradient {
    font-size: 1.6rem;
  }
  .main-nav {
    flex-wrap: wrap;
    justify-content: center;
    gap: 0.5rem;
  }
}
</style>
