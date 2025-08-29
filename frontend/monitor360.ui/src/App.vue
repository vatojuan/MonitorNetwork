<script setup>
import { RouterLink, RouterView } from 'vue-router'
import logo from '@/assets/logo.svg'
</script>

<template>
  <div id="app-layout">
    <header class="main-header">
      <div class="header-container">
        <!-- Logo + Título bien pegados a la izquierda -->
        <div class="title-group">
          <img :src="logo" alt="Monitor360 Logo" class="logo" />
          <h1 class="title-gradient">Monitor360</h1>
        </div>

        <!-- Navegación centrada -->
        <nav class="main-nav">
          <RouterLink to="/">Dashboard</RouterLink>
          <RouterLink to="/monitor-builder">Añadir Monitor</RouterLink>
          <RouterLink to="/devices">Gestionar Dispositivos</RouterLink>
          <RouterLink to="/credentials">Credenciales</RouterLink>
          <RouterLink to="/channels">Canales</RouterLink>
          <RouterLink to="/vpns">VPNs</RouterLink>
        </nav>
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
  margin-right: auto; /* fuerza a la izquierda */
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
  justify-content: center; /* centra el nav */
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
