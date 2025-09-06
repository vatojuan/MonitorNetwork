<!-- src/views/LoginView.vue -->
<script setup>
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { supabase } from '@/lib/supabase' // ✅ ahora sale de lib/supabase

const router = useRouter()
const route = useRoute()

const authMode = ref('login') // 'login' | 'signup'
const email = ref('')
const password = ref('')
const showPassword = ref(false)
const isAuthLoading = ref(false)
const notification = ref({ show: false, message: '', type: 'success' })

function showNotification(message, type = 'success', ttl = 4000) {
  notification.value = { show: true, message, type }
  setTimeout(() => (notification.value.show = false), ttl)
}

function destAfterLogin() {
  const q = route.query?.redirect
  return typeof q === 'string' && q.length > 0 ? q : '/'
}

async function handleAuthSubmit() {
  if (isAuthLoading.value) return
  if (!email.value?.trim() || !password.value) {
    showNotification('Completá email y password.', 'error')
    return
  }
  isAuthLoading.value = true

  try {
    if (authMode.value === 'login') {
      const { data, error } = await supabase.auth.signInWithPassword({
        email: email.value.trim(),
        password: password.value,
      })
      if (error) {
        const code = error?.message?.toLowerCase?.() || ''
        if (code.includes('email not confirmed') || code.includes('email_not_confirmed')) {
          throw new Error('Tu email no está confirmado. Revisá tu bandeja y confirmá la cuenta.')
        }
        if (code.includes('invalid login') || code.includes('invalid_credentials')) {
          throw new Error('Credenciales inválidas. Verificá tu email y password.')
        }
        throw error
      }

      // Solo debug temporal: logear para confirmar alg
      const token = data?.session?.access_token
      console.log('[Auth] Token emitido:', token?.slice(0, 40) + '...')

      showNotification('Sesión iniciada ✔', 'success', 1200)
      router.replace(destAfterLogin()) // replace para no volver al login con "atrás"
    } else {
      const { error } = await supabase.auth.signUp({
        email: email.value.trim(),
        password: password.value,
      })
      if (error) throw error
      showNotification(
        'Cuenta creada. Te enviamos un email para confirmar la dirección. Revisá tu bandeja.',
        'success',
        6000,
      )
      authMode.value = 'login'
    }
  } catch (err) {
    console.error('[Auth]', err)
    showNotification(err?.message || 'Error de autenticación.', 'error')
  } finally {
    isAuthLoading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <div class="login-card">
      <h2>{{ authMode === 'login' ? 'Iniciar sesión' : 'Crear cuenta' }}</h2>

      <form @submit.prevent="handleAuthSubmit" class="login-form" autocomplete="on">
        <label for="email" class="sr-only">Email</label>
        <input
          id="email"
          type="email"
          v-model.trim="email"
          placeholder="tu@email.com"
          required
          autocomplete="email"
          inputmode="email"
        />

        <label for="password" class="sr-only">Password</label>
        <div class="password-row">
          <input
            id="password"
            :type="showPassword ? 'text' : 'password'"
            v-model="password"
            placeholder="••••••••"
            required
            :autocomplete="authMode === 'login' ? 'current-password' : 'new-password'"
          />
          <button
            type="button"
            class="btn-eye"
            @click="showPassword = !showPassword"
            :aria-pressed="showPassword"
            aria-label="Mostrar/Ocultar password"
          >
            {{ showPassword ? '🙈' : '👁️' }}
          </button>
        </div>

        <button type="submit" class="btn-primary" :disabled="isAuthLoading">
          <span v-if="isAuthLoading">Procesando…</span>
          <span v-else>{{ authMode === 'login' ? 'Entrar' : 'Registrarme' }}</span>
        </button>
      </form>

      <p class="switch-mode">
        <span v-if="authMode === 'login'">
          ¿No tenés cuenta?
          <a href="#" @click.prevent="authMode = 'signup'">Crear cuenta</a>
        </span>
        <span v-else>
          ¿Ya tenés cuenta?
          <a href="#" @click.prevent="authMode = 'login'">Iniciar sesión</a>
        </span>
      </p>
    </div>

    <div v-if="notification.show" class="notification" :class="notification.type">
      {{ notification.message }}
    </div>
  </div>
</template>

<style scoped>
.login-page {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: calc(100vh - 120px);
  padding: 2rem;
}
.login-card {
  background: var(--surface-color, #16213e);
  border: 1px solid var(--primary-color, #0f3460);
  border-radius: 12px;
  padding: 2rem;
  max-width: 420px;
  width: 100%;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
  text-align: center;
}
.login-card h2 {
  margin-bottom: 1.5rem;
  color: var(--font-color, #e0e0e0);
  font-size: 1.5rem;
}
.login-form {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}
.login-form input {
  width: 100%;
  background: #0e0e0e;
  color: #eaeaea;
  border: 1px solid #2a2a2a;
  border-radius: 8px;
  padding: 0.75rem;
  font-size: 0.95rem;
}
.password-row {
  position: relative;
  display: flex;
  align-items: center;
}
.password-row input {
  padding-right: 3rem;
}
.btn-eye {
  position: absolute;
  right: 0.35rem;
  border: none;
  background: transparent;
  color: #9aa0a6;
  font-size: 1rem;
  cursor: pointer;
  padding: 0.25rem 0.5rem;
}
.btn-eye:hover {
  opacity: 0.8;
}
.btn-primary {
  background: var(--green, #3ddc84);
  border: none;
  border-radius: 8px;
  color: white;
  font-weight: 600;
  padding: 0.75rem;
  cursor: pointer;
  transition: background 0.2s;
}
.btn-primary:hover {
  background: #32c176;
}
.btn-primary:disabled {
  opacity: 0.7;
  cursor: default;
}
.switch-mode {
  margin-top: 1rem;
  color: #9aa0a6;
  font-size: 0.9rem;
}
.switch-mode a {
  color: #6ab4ff;
  cursor: pointer;
  text-decoration: none;
  font-weight: 600;
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
  background: #2ea043;
}
.notification.error {
  background: #d9534f;
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  border: 0;
}
</style>
