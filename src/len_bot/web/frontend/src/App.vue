<script setup>
import { ref, onMounted } from 'vue'
import { api } from './api.js'
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
  { id: 'overview', label: '运行概览', icon: '📊', comp: OverviewView },
  { id: 'scenes', label: '会话场景', icon: '💬', comp: ScenesView },
  { id: 'trace', label: '决策链路', icon: '🧭', comp: TraceView },
  { id: 'tasks', label: '任务与闭环', icon: '⏳', comp: TasksLoopsView },
  { id: 'memory', label: '社会记忆', icon: '🧠', comp: MemoryView },
  { id: 'models', label: '模型与路由', icon: '⚡', comp: ModelsView },
  { id: 'plugins', label: '扩展插件', icon: '🧩', comp: PluginsView },
  { id: 'events', label: '事件与日志', icon: '📜', comp: EventsLogsView },
  { id: 'replay', label: '策略实验室', icon: '🔬', comp: ReplayView },
  { id: 'settings', label: '系统设置', icon: '⚙️', comp: SettingsView },
]

const authed = ref(false)
const view = ref('overview')

function currentHash() {
  return (location.hash.replace('#/', '') || 'overview').split('?')[0]
}

onMounted(async () => {
  view.value = currentHash()
  window.addEventListener('hashchange', () => { view.value = currentHash() })
  try {
    await api('/api/auth/me')
    authed.value = true
  } catch {
    authed.value = false
  }
})

function onLogin() {
  authed.value = true
}

async function onLogout() {
  try { await api('/api/auth/logout', { method: 'POST' }) } catch {}
  authed.value = false
}
</script>

<template>
  <LoginView v-if="!authed" @logged-in="onLogin" />
  <div v-else class="shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-title">LenBot <span class="brand-sub">控制中心</span></div>
        <div class="status-pill"><span class="status-dot"></span>系统在线</div>
      </div>
      <nav>
        <a v-for="item in NAV" :key="item.id"
           :class="{ active: view === item.id }"
           :href="'#/' + item.id">
          <span class="nav-icon">{{ item.icon }}</span>
          <span class="nav-text">{{ item.label }}</span>
        </a>
      </nav>
      <div class="sidebar-footer">
        <button class="logout-btn" @click="onLogout">
          <span>安全退出</span>
        </button>
      </div>
    </aside>
    <main class="content">
      <component :is="NAV.find(n => n.id === view)?.comp || OverviewView" />
    </main>
  </div>
</template>

<style scoped>
.shell {
  display: flex;
  min-height: 100vh;
}

.sidebar {
  width: 230px;
  flex-shrink: 0;
  padding: 24px 16px 20px;
  background: rgba(14, 20, 32, 0.82);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  position: sticky;
  top: 0;
  height: 100vh;
  box-sizing: border-box;
}

.brand {
  padding: 0 6px 20px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  margin-bottom: 14px;
}

.brand-title {
  font-weight: 800;
  font-size: 1.25rem;
  letter-spacing: -0.02em;
  background: linear-gradient(135deg, #ffffff 30%, #94a3b8 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}

.brand-sub {
  font-size: 0.82rem;
  font-weight: 500;
  color: #60a5fa;
  -webkit-text-fill-color: #60a5fa;
  margin-left: 4px;
}

.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-top: 8px;
  padding: 3px 8px;
  border-radius: 9999px;
  background: rgba(16, 185, 129, 0.12);
  border: 1px solid rgba(16, 185, 129, 0.25);
  font-size: 0.74rem;
  color: #34d399;
  font-weight: 500;
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #10b981;
  box-shadow: 0 0 8px #10b981;
  animation: pulse 2s infinite;
}

@keyframes pulse {
  0% { opacity: 1; }
  50% { opacity: 0.4; }
  100% { opacity: 1; }
}

nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 1;
  overflow-y: auto;
}

nav a {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  border-radius: var(--radius-md);
  color: #94a3b8;
  font-size: 0.91rem;
  font-weight: 500;
  transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
  border: 1px solid transparent;
}

nav a:hover {
  background: rgba(255, 255, 255, 0.05);
  color: #ffffff;
  transform: translateX(2px);
}

nav a.active {
  background: linear-gradient(135deg, rgba(59, 130, 246, 0.15), rgba(99, 102, 241, 0.15));
  color: #60a5fa;
  border-color: rgba(96, 165, 250, 0.3);
  box-shadow: 0 4px 14px rgba(59, 130, 246, 0.15);
  font-weight: 600;
}

.nav-icon {
  font-size: 1rem;
  display: flex;
  align-items: center;
  justify-content: center;
}

.sidebar-footer {
  padding-top: 14px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
}

.logout-btn {
  width: 100%;
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  background: rgba(239, 68, 68, 0.08);
  border: 1px solid rgba(239, 68, 68, 0.2);
  color: #f87171;
  font-size: 0.88rem;
  font-weight: 500;
  transition: all 0.2s;
}

.logout-btn:hover {
  background: rgba(239, 68, 68, 0.2);
  border-color: rgba(239, 68, 68, 0.4);
  color: #fca5a5;
}

.content {
  flex: 1;
  padding: 28px 36px;
  max-width: 1440px;
  min-width: 0;
}
</style>
