<script setup>
import { ref, onMounted } from 'vue'
import { api, getToken, setToken } from './api.js'
import LoginView from './views/LoginView.vue'
import OverviewView from './views/OverviewView.vue'
import ScenesView from './views/ScenesView.vue'
import TraceView from './views/TraceView.vue'
import TasksLoopsView from './views/TasksLoopsView.vue'
import MemoryView from './views/MemoryView.vue'
import ModelsView from './views/ModelsView.vue'
import PluginsView from './views/PluginsView.vue'
import EventsLogsView from './views/EventsLogsView.vue'
import ReplayView from './views/ReplayView.vue'
import SettingsView from './views/SettingsView.vue'

const NAV = [
  { id: 'overview', label: 'Overview', comp: OverviewView },
  { id: 'scenes', label: 'Scenes', comp: ScenesView },
  { id: 'trace', label: 'Trace', comp: TraceView },
  { id: 'tasks', label: 'Tasks & Loops', comp: TasksLoopsView },
  { id: 'memory', label: 'Memory', comp: MemoryView },
  { id: 'models', label: 'Models & Routing', comp: ModelsView },
  { id: 'plugins', label: 'Plugins', comp: PluginsView },
  { id: 'events', label: 'Events & Logs', comp: EventsLogsView },
  { id: 'replay', label: 'Replay Lab', comp: ReplayView },
  { id: 'settings', label: 'Settings & Security', comp: SettingsView },
]

const authed = ref(false)
const view = ref('overview')

function currentHash() {
  return (location.hash.replace('#/', '') || 'overview').split('?')[0]
}

onMounted(async () => {
  view.value = currentHash()
  window.addEventListener('hashchange', () => { view.value = currentHash() })
  if (!getToken()) { authed.value = false; return }
  try {
    await api('/api/auth/me')
    authed.value = true
  } catch {
    authed.value = false
  }
})

function onLogin(token) {
  setToken(token)
  authed.value = true
}

async function onLogout() {
  try { await api('/api/auth/logout', { method: 'POST' }) } catch {}
  setToken('')
  authed.value = false
}
</script>

<template>
  <LoginView v-if="!authed" @logged-in="onLogin" />
  <div v-else class="shell">
    <aside class="sidebar">
      <div class="brand">LenBot<span> Control Plane</span></div>
      <nav>
        <a v-for="item in NAV" :key="item.id"
           :class="{ active: view === item.id }"
           :href="'#/' + item.id">{{ item.label }}</a>
      </nav>
      <button class="logout" @click="onLogout">登出</button>
    </aside>
    <main class="content">
      <component :is="NAV.find(n => n.id === view)?.comp || OverviewView" />
    </main>
  </div>
</template>

<style scoped>
.shell { display: flex; min-height: 100vh; }
.sidebar {
  width: 200px; flex-shrink: 0; padding: 18px 12px;
  background: var(--panel); border-right: 1px solid var(--border);
  display: flex; flex-direction: column;
}
.brand { font-weight: 800; font-size: 1.05rem; padding: 0 8px 16px; }
.brand span { color: var(--muted); font-weight: 400; font-size: 0.85rem; }
nav { display: flex; flex-direction: column; gap: 2px; flex: 1; }
nav a { padding: 7px 10px; border-radius: 6px; color: var(--text); font-size: 0.92rem; }
nav a:hover { background: var(--panel-2); }
nav a.active { background: var(--panel-2); color: var(--accent); }
.logout { margin-top: 12px; }
.content { flex: 1; padding: 22px 26px; max-width: 1400px; }
</style>
