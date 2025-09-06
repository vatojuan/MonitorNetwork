/* eslint-env serviceworker */

// public/service-worker.js
const CACHE_VERSION = 'v2'
const STATIC_CACHE = `monitor360-static-${CACHE_VERSION}`

const ASSETS_TO_CACHE = [
  '/', // página principal
  '/index.html',
  '/manifest.json',
  '/favicon.ico',
  '/favicon_32x32.png',
  '/favicon_64x64.png',
  '/favicon_128x128.png',
  '/favicon_512x512.png',
]

// ===== Install: precache estáticos =====
self.addEventListener('install', (event) => {
  self.skipWaiting()
  event.waitUntil(
    caches
      .open(STATIC_CACHE)
      .then((cache) => cache.addAll(ASSETS_TO_CACHE))
      .catch(() => {}),
  )
})

// ===== Activate: limpiar versiones viejas y tomar control =====
self.addEventListener('activate', (event) => {
  event.waitUntil(
    Promise.all([
      self.clients.claim(),
      caches
        .keys()
        .then((keys) =>
          Promise.all(keys.filter((k) => k !== STATIC_CACHE).map((k) => caches.delete(k))),
        ),
    ]),
  )
})

// ===== Helpers =====
const isSameOrigin = (url) => new URL(url, self.location.href).origin === self.location.origin

// ===== Fetch: estrategias por tipo =====
self.addEventListener('fetch', (event) => {
  const req = event.request

  // No interceptar nada que no sea GET
  if (req.method !== 'GET') return

  const url = new URL(req.url)

  // No interceptar API/WS/dinámicos (red directa)
  if (
    isSameOrigin(url) &&
    (url.pathname.startsWith('/api/') ||
      url.pathname === '/ws' ||
      url.pathname.startsWith('/socket.io'))
  ) {
    return // dejar que vaya directo a red
  }

  // Navegación (SPA): network-first con fallback a index.html
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req).catch(async () => {
        return (
          (await caches.match('/index.html')) ||
          new Response('<h1>Offline</h1>', { headers: { 'Content-Type': 'text/html' } })
        )
      }),
    )
    return
  }

  // Estáticos same-origin: cache-first con revalidación pasiva
  if (isSameOrigin(url)) {
    event.respondWith(
      caches.match(req).then((cached) => {
        if (cached) return cached
        return fetch(req)
          .then((res) => {
            if (res && res.ok && res.type === 'basic') {
              const clone = res.clone()
              caches
                .open(STATIC_CACHE)
                .then((cache) => cache.put(req, clone))
                .catch(() => {})
            }
            return res
          })
          .catch(
            async () => (await caches.match('/index.html')) || new Response('', { status: 504 }),
          )
      }),
    )
    return
  }

  // Cross-origin: passthrough (sin cache). Aseguramos siempre devolver Response.
  event.respondWith(fetch(req).catch(() => new Response('', { status: 504 })))
})
