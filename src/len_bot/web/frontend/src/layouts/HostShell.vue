<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'
import { mdiViewDashboardOutline, mdiChatProcessingOutline, mdiForumOutline, mdiAccountOutline, mdiBookOpenPageVariantOutline,
  mdiBriefcaseOutline, mdiFolderOutline, mdiToolboxOutline, mdiChip, mdiTimelineTextOutline, mdiCogOutline, mdiMenu, mdiLogout, mdiRestart,
  mdiPuzzleOutline } from '@mdi/js'
import { logout, useAuth } from '../composables/useAuth.js'
import { confirm } from '../composables/useConfirm.js'
import { sceneTarget, showsScene, useCurrentScene } from '../composables/useCurrentScene.js'
import { host, readHostState, readPendingRestart, readSceneTitles } from '../host/store.js'
import { sectionLabel } from '../host/labels.js'
import { sceneName, sceneTitles } from '../api.js'
import { hostAreas, hostGroups, hostTarget } from '../router/hostNavigation.js'
import ErrorNote from '../host/ui/ErrorNote.vue'
import StatusBadge from '../host/ui/StatusBadge.vue'
import ScenePicker from '../host/ui/ScenePicker.vue'
import ConfirmHost from '../host/ui/ConfirmHost.vue'
import RestartDialog from '../host/components/RestartDialog.vue'
import { restartFlow, openRestart } from '../host/restart.js'
import markUrl from '../assets/lenbot-mark.svg'

const route = useRoute(), router = useRouter(), { mobile } = useDisplay()
const drawer = ref(!mobile.value), leaving = ref(false), logoutError = ref(null)
const icons = { home: mdiViewDashboardOutline, trial: mdiChatProcessingOutline, scenes: mdiForumOutline, persona: mdiAccountOutline,
  memory: mdiBookOpenPageVariantOutline, tasks: mdiBriefcaseOutline, resources: mdiFolderOutline, capabilities: mdiToolboxOutline, models: mdiChip,
  logs: mdiTimelineTextOutline, settings: mdiCogOutline, plugins: mdiPuzzleOutline }
const area = computed(() => hostAreas.find(item => item.pages.includes(route.name)))
const group = computed(() => hostGroups.find(item => item.areas.includes(area.value)))
const { scene } = useCurrentScene()
const scenePicker = computed(() => showsScene(route) && (host.state?.scenes.length || 0) > 0)
const status = computed(() => {
  const state = host.state
  if (!state) return { text: host.stateError ? '状态读取失败' : '读取中', tone: host.stateError ? 'error' : 'neutral' }
  if (state.connection.connected && state.connection.accepting) return { text: '在线', tone: 'success' }
  return state.connection.status === 'running' ? { text: '平台账号 未连接', tone: 'warning' } : { kind: 'runtime', value: state.connection.status }
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
// Names are asked for once 平台账号 is connected and while any scene still lacks one.
watch(() => host.state?.connection.connected && host.state.scenes.some(item => !sceneTitles[item.scene]),
  missing => { if (missing) readSceneTitles() }, { immediate: true })
watch(mobile, value => { drawer.value = !value })
watch(() => route.fullPath, () => { if (mobile.value) drawer.value = false })

async function exit() {
  if (leaving.value || !await confirm({ title: '退出登录？', text: '未保存的修改会丢失。', confirmLabel: '退出' })) return
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
  <v-navigation-drawer v-model="drawer" :permanent="!mobile" :temporary="mobile" :width="236" floating aria-label="主导航" class="shell-nav">
    <RouterLink :to="{ name: 'host-overview' }" class="shell-brand"><img :src="markUrl" alt="" /><div><strong>LenBot</strong><span>控制台</span></div></RouterLink>
    <nav class="shell-nav-groups">
      <div v-for="group in hostGroups" :key="group.title" class="shell-nav-group">
        <div class="shell-nav-heading">{{ group.title }}</div>
        <RouterLink v-for="item in group.areas" :key="item.id" :to="hostTarget(item.name, scene)" class="shell-nav-item"
          :class="{ active: area?.id === item.id }" :aria-current="area?.id === item.id ? 'page' : undefined">
          <v-icon :icon="icons[item.id]" size="20" />{{ item.title }}</RouterLink>
      </div>
    </nav>
    <template #append>
      <div class="shell-nav-footer"><v-btn :prepend-icon="mdiLogout" block variant="text" :loading="leaving" @click="exit">退出登录</v-btn></div>
    </template>
  </v-navigation-drawer>
  <v-main class="shell-wrap">
    <div class="shell-canvas">
      <header class="shell-top">
        <v-btn v-if="mobile" :icon="mdiMenu" variant="text" aria-label="打开导航" @click="drawer = true" />
        <span v-if="!scenePicker" class="shell-top-title"><span v-if="group" class="muted">{{ group.title }} /</span> {{ area?.title || route.meta.title }}</span>
        <ScenePicker v-else :model-value="scene" @update:model-value="value => router.push(sceneTarget(route, value))" />
        <v-spacer />
        <span v-if="host.state?.delivery === 'simulated'" class="shell-pill muted">模拟发送</span>
        <span class="shell-pill"><StatusBadge dot :pulse="status.tone === 'success'" :kind="status.kind" :value="status.value" :text="status.text" :tone="status.tone" /></span>
        <v-btn variant="tonal" color="primary" size="small" :prepend-icon="mdiRestart" :disabled="restartFlow.waiting" @click="openRestart">重启</v-btn>
      </header>
      <main class="shell-main" id="main-content">
        <ErrorNote v-if="logoutError" title="退出登录失败" :error="logoutError" />
        <v-alert v-if="restartItems.length" type="info" role="status" class="rise">
          已保存，重启后生效：{{ restartItems.join('、') }}
          <template #append><v-btn size="small" variant="text" :disabled="restartFlow.waiting" @click="openRestart">重启</v-btn></template>
        </v-alert>
        <ErrorNote v-if="host.restart?.error" title="无法确认哪些修改需要重启" :error="host.restart.error" @retry="readPendingRestart" />
        <slot v-if="!restartFlow.waiting" />
      </main>
    </div>
  </v-main>
  <RestartDialog />
  <ConfirmHost />
  <v-snackbar v-model="toast" :timeout="3000" location="bottom" color="primary" rounded="pill">{{ host.toast }}</v-snackbar>
</template>
