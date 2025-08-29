const CACHE_NAME = 'monitor360-cache-v1'
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

// Instalación: cachea archivos estáticos
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(ASSETS_TO_CACHE)
    }),
  )
})

// Activación: limpia caches viejos
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME) {
            return caches.delete(key)
          }
        }),
      ),
    ),
  )
})

// Fetch: responde desde cache si no hay conexión
self.addEventListener('fetch', (event) => {
  event.respondWith(
    caches.match(event.request).then((cachedResponse) => {
      return (
        cachedResponse ||
        fetch(event.request).catch(
          () => caches.match('/index.html'), // fallback offline
        )
      )
    }),
  )
})
