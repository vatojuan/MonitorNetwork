// src/main.js
import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import './assets/main.css'

// 1. Importamos el inicializador del sistema WebSocket
import { initializeWebSocketSystem } from '@/lib/ws'

// ... (Tu código de Service Worker puede ir aquí si lo tenías) ...

// --- Inicialización de la app ---
const app = createApp(App)

app.use(router)

// Usamos router.isReady() para asegurar que todo esté listo antes de montar
router
  .isReady()
  .then(() => {
    // 2. Montamos la aplicación en el DOM
    app.mount('#app')

    // 3. Y SÓLO DESPUÉS, inicializamos el sistema WebSocket en segundo plano.
    // Esta es la secuencia más segura y robusta.
    initializeWebSocketSystem()
  })
  .catch((err) => {
    console.error('Error fatal al inicializar la aplicación:', err)
  })
