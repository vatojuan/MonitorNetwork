// src/lib/api.js
import axios from 'axios'
import { API_BASE_URL, supabase, getAccessToken, waitForSession } from './supabase'

/**
 * Normaliza la base del backend:
 * - Acepta con o sin /api
 * - Evita dobles barras
 */
function ensureApiBase(url) {
  const u = (url || 'http://127.0.0.1:8000').trim().replace(/\/+$/, '')
  return /\/api$/i.test(u) ? u : `${u}/api`
}

const api = axios.create({
  baseURL: ensureApiBase(API_BASE_URL),
  timeout: 20000,
})

/**
 * REQUEST INTERCEPTOR
 * - Inyecta Authorization: Bearer <token>
 * - Si aún no hay token (carrera post-login), espera brevemente
 */
api.interceptors.request.use(
  async (config) => {
    try {
      // 1) intento rápido
      let token = await getAccessToken()

      // 2) si no hay, espera un poco por si Supabase está restaurando sesión
      if (!token) {
        token = await waitForSession({ timeoutMs: 1500, requireAuth: false })
      }

      // 3) último intento directo
      if (!token) {
        const { data } = await supabase.auth.getSession()
        token = data?.session?.access_token || null
      }

      // Inyectar header si existe token
      if (token) {
        config.headers = config.headers || {}
        config.headers.Authorization = `Bearer ${token}`
        if (import.meta?.env?.DEV) {
          console.debug(
            '[api] Authorization: Bearer <token> →',
            config.method?.toUpperCase(),
            config.url,
          )
        }
      } else if (import.meta?.env?.DEV) {
        console.debug('[api] sin token para', config.method?.toUpperCase(), config.url)
      }

      // Asegurar Accept por sanidad
      config.headers = config.headers || {}
      if (!config.headers.Accept) config.headers.Accept = 'application/json'
      return config
    } catch (e) {
      if (import.meta?.env?.DEV) {
        console.debug('[api] interceptor request error:', e?.message || e)
      }
      return config
    }
  },
  (error) => Promise.reject(error),
)

/**
 * RESPONSE INTERCEPTOR
 * - Si 401, reintenta una vez con token fresco
 */
api.interceptors.response.use(
  (res) => res,
  async (error) => {
    const { response, config } = error || {}
    if (response?.status === 401 && config && !config.__retried401) {
      try {
        config.__retried401 = true
        // intenar refrescar/recuperar token
        let token = await getAccessToken()
        if (!token) {
          const { data } = await supabase.auth.getSession()
          token = data?.session?.access_token || null
        }
        if (!token) {
          token = await waitForSession({ timeoutMs: 1500, requireAuth: false })
        }
        if (token) {
          config.headers = config.headers || {}
          config.headers.Authorization = `Bearer ${token}`
          if (import.meta?.env?.DEV) {
            console.debug(
              '[api] reintento 401 con nuevo token →',
              config.method?.toUpperCase(),
              config.url,
            )
          }
          return api.request(config)
        }
      } catch (e) {
        if (import.meta?.env?.DEV) {
          console.debug('[api] manejo 401 falló:', e?.message || e)
        }
      }
    }
    return Promise.reject(error)
  },
)

export default api
