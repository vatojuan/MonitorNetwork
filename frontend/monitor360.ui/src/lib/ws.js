// src/lib/ws.js
import { API_BASE_URL, supabase, waitForSession } from './supabase'

const DEV = typeof import.meta !== 'undefined' && import.meta?.env?.DEV === true

function wsLog(...args) {
  if (DEV) {
    console.log(
      '%c[WS]',
      'background: #007acc; color: #fff; padding: 2px 6px; border-radius: 4px;',
      ...args,
    )
  }
}

export function buildWsUrlFromApiBase(base = API_BASE_URL) {
  try {
    const u = new URL(base)
    u.pathname = u.pathname.replace(/\/+$/, '')
    if (u.pathname.endsWith('/api')) u.pathname = u.pathname.slice(0, -4)
    u.protocol = u.protocol === 'https:' ? 'wss:' : 'ws:'
    u.pathname = (u.pathname.replace(/\/+$/, '') || '') + '/ws'
    return u.toString()
  } catch (err) {
    if (DEV) console.warn('[WS] buildWsUrlFromApiBase fallback:', err?.message || err)
    const trimmed = String(base || '')
      .trim()
      .replace(/\/+$/, '')
    const noApi = trimmed.replace(/\/api$/i, '')
    const proto = /^https:/i.test(noApi) ? 'wss' : 'ws'
    return `${proto}://${noApi.replace(/^https?:\/\//i, '')}/ws`
  }
}

function attachKeepAlive(ws, intervalMs = 25_000) {
  const t = setInterval(() => {
    if (ws.readyState === WebSocket.OPEN) {
      try {
        ws.send(JSON.stringify({ type: 'ping' }))
      } catch {
        /* No action needed */
      }
    }
  }, intervalMs)

  const onMessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data)
      if (msg?.type === 'ping') {
        try {
          ws.send(JSON.stringify({ type: 'pong' }))
        } catch {
          /* No action needed */
        }
      }
    } catch {
      /* No action needed */
    }
  }

  const cleanup = () => {
    clearInterval(t)
    ws.removeEventListener('message', onMessage)
  }

  ws.addEventListener('message', onMessage)
  ws.addEventListener('close', cleanup)
  ws.addEventListener('error', cleanup)
  return cleanup
}

const listeners = new Set()

function notifyAll(payload) {
  for (const fn of listeners) {
    try {
      fn(payload)
    } catch (err) {
      if (DEV) console.debug('[WS] listener error:', err?.message || err)
    }
  }
}

export function addWsListener(fn) {
  if (typeof fn === 'function') listeners.add(fn)
  return () => removeWsListener(fn)
}

export function removeWsListener(fn) {
  listeners.delete(fn)
}

let wsRef = null
let cleanupKeepAlive = null
let reconnectTimer = null
let backoffStep = 0
const BACKOFF_BASE_MS = 1000
const BACKOFF_MAX_MS = 15000

function clearReconnectTimer() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
}

function scheduleReconnect(reason = 'unknown') {
  if (reconnectTimer) return
  backoffStep = Math.min(backoffStep + 1, 6)
  const delay = Math.min(BACKOFF_BASE_MS * 2 ** (backoffStep - 1), BACKOFF_MAX_MS)
  if (DEV) console.warn(`[WS] Reintento en ${delay}ms (motivo: ${reason})`)
  reconnectTimer = setTimeout(async () => {
    reconnectTimer = null
    try {
      await openAppWebSocketSingleton()
    } catch (err) {
      if (DEV) console.warn('[WS] Open failed, reschedule:', err?.message || err)
      scheduleReconnect(err?.message || 'open_failed')
    }
  }, delay)
}

async function openAppWebSocket() {
  const token = await waitForSession({ requireAuth: true, timeoutMs: 8000 })
  const baseWsUrl = buildWsUrlFromApiBase(API_BASE_URL)
  const finalUrl = `${baseWsUrl}?token=${encodeURIComponent(token)}`
  const ws = new WebSocket(finalUrl)

  ws.addEventListener('open', () => {
    wsLog('Conexión WebSocket ABIERTA.')
  })

  ws.addEventListener('message', (ev) => {
    try {
      const msg = JSON.parse(ev.data)
      if (msg?.type === 'sensor_batch' && Array.isArray(msg?.items)) {
        for (const item of msg.items) {
          notifyAll(item)
        }
      } else if (msg && Object.prototype.hasOwnProperty.call(msg, 'sensor_id')) {
        notifyAll(msg)
      }
    } catch (err) {
      if (DEV) console.debug('[WS] parse skip:', err?.message || err)
    }
  })

  ws.addEventListener('close', (e) => {
    if (DEV) console.warn(`[WS] Conexión CERRADA (code=${e.code})`)
    if (cleanupKeepAlive) {
      try {
        cleanupKeepAlive()
      } catch {
        /* No action needed */
      }
      cleanupKeepAlive = null
    }
    scheduleReconnect(`close_${e.code}`)
  })

  ws.addEventListener('error', () => {
    if (DEV) console.warn('[WS] Error de WebSocket.')
    scheduleReconnect('error')
  })

  return ws
}

async function openAppWebSocketSingleton() {
  if (wsRef && (wsRef.readyState === WebSocket.OPEN || wsRef.readyState === WebSocket.CONNECTING)) {
    return wsRef
  }
  try {
    if (wsRef) wsRef.close(4000, 'reopen')
  } catch {
    /* No action needed */
  }
  if (cleanupKeepAlive) {
    try {
      cleanupKeepAlive()
    } catch {
      /* No action needed */
    }
    cleanupKeepAlive = null
  }

  wsRef = await openAppWebSocket()
  cleanupKeepAlive = attachKeepAlive(wsRef)
  return wsRef
}

export async function connectWebSocketWhenAuthenticated(onMessage) {
  if (typeof onMessage === 'function') addWsListener(onMessage)
  const { data } = await supabase.auth.getSession()
  if (data?.session?.access_token) {
    return openAppWebSocketSingleton()
  }
  return null
}

export function getCurrentWebSocket() {
  return wsRef
}

export function stopWebSocket(reason = 'app_unload') {
  clearReconnectTimer()
  if (wsRef) {
    try {
      wsRef.close(4002, reason)
    } catch {
      /* No action needed */
    }
  }
  if (cleanupKeepAlive) {
    try {
      cleanupKeepAlive()
    } catch {
      /* No action needed */
    }
    cleanupKeepAlive = null
  }
  wsRef = null
}

export function initializeWebSocketSystem() {
  wsLog('Inicializando sistema WebSocket.')
  supabase.auth.onAuthStateChange(async (_evt, session) => {
    const hasToken = !!session?.access_token
    wsLog(`Cambio de estado de Auth. Logueado: ${hasToken}`)
    if (hasToken) {
      await openAppWebSocketSingleton()
    } else {
      stopWebSocket('logout')
    }
  })
}
