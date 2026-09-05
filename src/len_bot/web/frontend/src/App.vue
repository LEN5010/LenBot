<script setup>
import { computed, ref, onMounted, onUnmounted } from 'vue'
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
import JobsView from './views/JobsView.vue'
import MediaView from './views/MediaView.vue'

const NAV = [
  { id: 'overview', label: '概览', icon: '⌂', comp: OverviewView },
  { id: 'scenes', label: '群聊', icon: '◌', comp: ScenesView },
  { id: 'trace', label: '动态', icon: '↗', comp: TraceView },
  { id: 'tasks', label: '计划', icon: '◷', comp: TasksLoopsView },
  { id: 'jobs', label: '工作', icon: '⌕', comp: JobsView },
  { id: 'media', label: '图片', icon: '▧', comp: MediaView },
  { id: 'memory', label: '记忆', icon: '◇', comp: MemoryView },
  { id: 'models', label: '模型', icon: '✦', comp: ModelsView },
  { id: 'plugins', label: '能力', icon: '＋', comp: PluginsView },
  { id: 'events', label: '记录', icon: '≡', comp: EventsLogsView },
  { id: 'replay', label: '回放', icon: '↺', comp: ReplayView },
  { id: 'settings', label: '设置', icon: '⚙', comp: SettingsView },
]

const authed = ref(false)
const view = ref('overview')
const menuOpen = ref(false)
const currentItem = computed(() => NAV.find(item => item.id === view.value) || NAV[0])
function currentHash() { return (location.hash.replace('#/', '') || 'overview').split('?')[0] }
function syncHash() { view.value = currentHash(); menuOpen.value = false }

onMounted(async () => {
  syncHash()
  window.addEventListener('hashchange', syncHash)
  try { await api('/api/auth/me'); authed.value = true } catch { authed.value = false }
})
onUnmounted(() => window.removeEventListener('hashchange', syncHash))
function onLogin() { authed.value = true }
async function onLogout() { try { await api('/api/auth/logout', { method: 'POST' }) } catch {}; authed.value = false }
</script>

<template>
  <LoginView v-if="!authed" @logged-in="onLogin" />
  <div v-else class="shell">
    <aside class="sidebar" :class="{ open: menuOpen }">
      <div class="brand">
        <div class="brand-mark">L</div>
        <div><div class="brand-title">LenBot</div><div class="brand-sub">管理中心</div></div>
      </div>
      <div class="status-pill"><span class="status-dot"></span>正在运行</div>
      <nav aria-label="主导航">
        <a v-for="item in NAV" :key="item.id" :class="{ active: view === item.id }" :href="'#/' + item.id">
          <span class="nav-icon">{{ item.icon }}</span><span>{{ item.label }}</span>
        </a>
      </nav>
      <button class="logout-btn" @click="onLogout">退出登录</button>
    </aside>
    <div class="workspace">
      <header class="topbar">
        <button class="menu-button" aria-label="打开导航" @click="menuOpen = !menuOpen">☰</button>
        <div><strong>{{ currentItem.label }}</strong><span>管理 LenBot 的运行状态</span></div>
        <div class="topbar-state"><span class="status-dot"></span>服务正常</div>
      </header>
      <main class="content"><component :is="currentItem.comp" /></main>
    </div>
  </div>
</template>

<style scoped>
.shell { display: flex; min-height: 100vh; }
.sidebar { position: sticky; top: 0; width: 228px; height: 100vh; padding: 24px 16px 18px; display: flex; flex-direction: column; flex-shrink: 0; background: rgba(255,255,255,.67); border-right: 1px solid rgba(255,255,255,.9); box-shadow: 12px 0 40px rgba(64,91,127,.06); backdrop-filter: blur(22px) saturate(135%); z-index: 20; }
.brand { display: flex; align-items: center; gap: 11px; padding: 2px 7px 17px; }
.brand-mark { width: 40px; height: 40px; display: grid; place-items: center; color: #fff; font-weight: 800; border-radius: 13px; background: var(--accent-gradient); box-shadow: 0 9px 20px var(--accent-glow); }
.brand-title { color: var(--text); font-size: 1.08rem; font-weight: 800; letter-spacing: -.03em; }
.brand-sub { margin-top: 1px; color: var(--muted); font-size: .76rem; }
.status-pill { margin: 0 7px 18px; padding: 8px 10px; display: flex; align-items: center; gap: 8px; color: var(--ok); font-size: .8rem; font-weight: 700; background: var(--ok-bg); border-radius: 11px; }
.status-dot { width: 7px; height: 7px; flex: 0 0 auto; border-radius: 50%; background: #10b981; box-shadow: 0 0 0 4px rgba(16,185,129,.12); }
nav { display: flex; flex: 1; flex-direction: column; gap: 4px; overflow-y: auto; }
nav a { min-height: 43px; padding: 9px 12px; display: flex; align-items: center; gap: 11px; color: #687991; font-size: .91rem; font-weight: 650; border: 1px solid transparent; border-radius: 12px; transition: all .18s ease; }
nav a:hover { color: var(--accent-strong); background: rgba(232,240,255,.66); }
nav a.active { color: var(--accent-strong); background: #eaf1ff; border-color: rgba(37,99,235,.1); box-shadow: inset 3px 0 0 var(--accent); }
.nav-icon { width: 20px; text-align: center; font-size: 1.02rem; }
.logout-btn { width: 100%; color: var(--muted); background: transparent; box-shadow: none; }
.workspace { min-width: 0; flex: 1; }
.topbar { height: 70px; padding: 0 32px; display: flex; align-items: center; gap: 14px; position: sticky; top: 0; z-index: 10; background: rgba(247,250,255,.66); border-bottom: 1px solid rgba(255,255,255,.86); backdrop-filter: blur(18px); }
.topbar strong { display: block; color: var(--text); font-size: .95rem; }
.topbar span { color: var(--muted); font-size: .78rem; }
.topbar-state { margin-left: auto; display: flex; align-items: center; gap: 9px; color: var(--text-soft); font-size: .82rem; }
.menu-button { display: none; min-height: 36px; padding: 5px 9px; }
.content { width: 100%; max-width: 1540px; padding: 30px 34px 56px; }
@media (max-width: 820px) { .sidebar { position: fixed; transform: translateX(-105%); transition: transform .22s ease; } .sidebar.open { transform: translateX(0); } .menu-button { display: inline-flex; } .topbar { padding: 0 18px; } .content { padding: 22px 17px 44px; } }
</style>
