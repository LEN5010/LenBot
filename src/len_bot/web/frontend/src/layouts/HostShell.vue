<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'
import { mdiViewDashboardOutline, mdiChatProcessingOutline, mdiForumOutline, mdiAccountOutline, mdiBookOpenPageVariantOutline,
  mdiBriefcaseOutline, mdiToolboxOutline, mdiChip, mdiTimelineTextOutline, mdiCogOutline, mdiMenu, mdiLogout } from '@mdi/js'
import { logout, useAuth } from '../composables/useAuth.js'
import { host, readHostState, readPendingRestart } from '../host/store.js'
import { runtimeLabel, sectionLabel } from '../host/labels.js'
import { sceneName } from '../api.js'
import { hostAreas, hostTarget } from '../router/hostNavigation.js'
import ErrorNote from '../host/components/ErrorNote.vue'
import markUrl from '../assets/lenbot-mark.svg'

const route = useRoute(), router = useRouter(), { mobile } = useDisplay()
const drawer = ref(!mobile.value), leaving = ref(false), logoutError = ref(null)
const icons = { home: mdiViewDashboardOutline, trial: mdiChatProcessingOutline, scenes: mdiForumOutline, persona: mdiAccountOutline,
  memory: mdiBookOpenPageVariantOutline, tasks: mdiBriefcaseOutline, capabilities: mdiToolboxOutline, models: mdiChip,
  logs: mdiTimelineTextOutline, settings: mdiCogOutline }
const area = computed(() => hostAreas.find(item => item.pages.some(([, name]) => name === route.name)))
const status = computed(() => {
  const state = host.state
  if (!state) return { text: host.stateError ? '状态读取失败' : '读取中', ok: false }
  if (state.connection.connected) return { text: '在线', ok: true }
  return { text: state.connection.status === 'running' ? 'QQ 未连接' : runtimeLabel(state.connection.status), ok: false }
})
const restartItems = computed(() => {
  const value = host.restart
  if (!value || value.error) return []
  return [...new Set([...value.sections.map(sectionLabel), ...value.scenes.map(sceneName),
    ...value.personas.map(item => `角色 ${item.name}`)])]
})
const toast = computed({ get: () => Boolean(host.toast), set: value => { if (!value) host.toast = '' } })

function refresh() {
  readHostState()
  readPendingRestart()
}
onMounted(refresh)
watch(() => route.name, refresh)
watch(mobile, value => { drawer.value = !value })
watch(() => route.fullPath, () => { if (mobile.value) drawer.value = false })

async function exit() {
  if (leaving.value || !window.confirm('退出登录？未保存的修改会丢失。')) return
  leaving.value = true
  logoutError.value = null
  try {
    await logout()
    if (useAuth().status === 'unauthenticated') await router.replace({ name: 'login' })
  } catch (error) {
    logoutError.value = error
  } finally {
    leaving.value = false
  }
}
</script>

<template>
  <v-navigation-drawer v-model="drawer" :permanent="!mobile" :temporary="mobile" :width="208" aria-label="主导航" class="host-nav">
    <div class="host-brand"><img :src="markUrl" alt="" /><strong>LenBot</strong></div>
    <v-list nav density="compact" class="host-nav-list">
      <v-list-item v-for="item in hostAreas" :key="item.id" :to="hostTarget(item.name, route)" :active="area?.id === item.id"
        color="primary" :prepend-icon="icons[item.id]" :title="item.title" />
    </v-list>
    <template #append>
      <div class="host-nav-footer"><v-btn :prepend-icon="mdiLogout" block variant="text" :loading="leaving" @click="exit">退出登录</v-btn></div>
    </template>
  </v-navigation-drawer>
  <v-app-bar flat :height="56" class="host-bar">
    <v-btn v-if="mobile" :icon="mdiMenu" variant="text" aria-label="打开导航" @click="drawer = true" />
    <v-app-bar-title><span class="host-bar-title">{{ area?.title || route.meta.title }}</span></v-app-bar-title>
    <v-chip v-if="host.state?.delivery === 'simulated'" size="small" variant="tonal" color="secondary" class="mr-2">模拟发送</v-chip>
    <span class="host-status" :class="{ ok: status.ok }"><span class="dot" />{{ status.text }}</span>
  </v-app-bar>
  <v-main tag="div">
    <main class="app-page" id="main-content">
      <ErrorNote v-if="logoutError" title="退出登录失败" :error="logoutError" class="mb-4" />
      <v-alert v-if="restartItems.length" type="info" variant="tonal" class="mb-4" role="status">
        这些修改已保存，重启 LenBot 后生效：{{ restartItems.join('、') }}
      </v-alert>
      <ErrorNote v-if="host.restart?.error" title="无法确认哪些修改需要重启" :error="host.restart.error" class="mb-4" />
      <nav v-if="area && area.pages.length > 1" class="host-subnav" aria-label="当前区域">
        <v-btn v-for="[label, name] in area.pages" :key="name" :to="hostTarget(name, route)" size="small"
          :variant="route.name === name ? 'tonal' : 'text'" :aria-current="route.name === name ? 'page' : undefined">{{ label }}</v-btn>
      </nav>
      <slot />
    </main>
  </v-main>
  <v-snackbar v-model="toast" :timeout="3000" location="bottom">{{ host.toast }}</v-snackbar>
</template>

<style scoped>
.host-nav{border-right:1px solid var(--line)}
.host-brand{display:flex;align-items:center;gap:10px;padding:20px 20px 12px}
.host-brand img{width:32px;height:32px;border-radius:8px}
.host-brand strong{font-size:19px;letter-spacing:-.02em}
.host-nav-list{padding:4px 10px}
.host-nav-list :deep(.v-list-item){min-height:40px;border-radius:8px;margin-bottom:2px}
.host-nav-list :deep(.v-list-item__prepend > .v-icon){margin-inline-end:12px;opacity:.85}
.host-nav-list :deep(.v-list-item__spacer){width:12px}
.host-nav-list :deep(.v-list-item-title){font-size:14px}
.host-nav-footer{padding:12px;border-top:1px solid var(--line)}
.host-bar{border-bottom:1px solid var(--line)}
.host-bar-title{font-size:15px;font-weight:600}
.host-status{display:inline-flex;align-items:center;gap:6px;margin-right:20px;font-size:13px;color:var(--muted)}
.host-status .dot{width:8px;height:8px;border-radius:50%;background:var(--status-warning)}
.host-status.ok .dot{background:var(--success)}
.host-subnav{display:flex;gap:6px;flex-wrap:wrap;max-width:1080px;margin:0 auto 20px;padding-bottom:10px;border-bottom:1px solid var(--line)}
</style>
