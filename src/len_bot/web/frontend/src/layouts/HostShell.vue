<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'
import { mdiViewDashboardOutline, mdiChatProcessingOutline, mdiForumOutline, mdiAccountOutline, mdiBookOpenPageVariantOutline,
  mdiBriefcaseOutline, mdiFolderOutline, mdiToolboxOutline, mdiChip, mdiTimelineTextOutline, mdiCogOutline, mdiMenu, mdiLogout, mdiRestart } from '@mdi/js'
import { logout, useAuth } from '../composables/useAuth.js'
import { confirm } from '../composables/useConfirm.js'
import { sceneTarget, showsScene, useCurrentScene } from '../composables/useCurrentScene.js'
import { host, readHostState, readPendingRestart } from '../host/store.js'
import { sectionLabel } from '../host/labels.js'
import { sceneName } from '../api.js'
import { hostAreas, hostGroups, hostTarget } from '../router/hostNavigation.js'
import ErrorNote from '../host/ui/ErrorNote.vue'
import StatusBadge from '../host/ui/StatusBadge.vue'
import ConfirmHost from '../host/ui/ConfirmHost.vue'
import RestartDialog from '../host/components/RestartDialog.vue'
import { restartFlow, openRestart } from '../host/restart.js'
import markUrl from '../assets/lenbot-mark.svg'

const route = useRoute(), router = useRouter(), { mobile } = useDisplay()
const drawer = ref(!mobile.value), leaving = ref(false), logoutError = ref(null)
const icons = { home: mdiViewDashboardOutline, trial: mdiChatProcessingOutline, scenes: mdiForumOutline, persona: mdiAccountOutline,
  memory: mdiBookOpenPageVariantOutline, tasks: mdiBriefcaseOutline, resources: mdiFolderOutline, capabilities: mdiToolboxOutline, models: mdiChip,
  logs: mdiTimelineTextOutline, settings: mdiCogOutline }
const area = computed(() => hostAreas.find(item => item.pages.includes(route.name)))
const { scene } = useCurrentScene()
const scenePicker = computed(() => showsScene(route) && (host.state?.scenes.length || 0) > 0)
const sceneOptions = computed(() => (host.state?.scenes || []).map(item => ({ title: sceneName(item.scene), subtitle: item.persona.name, value: item.scene })))
const status = computed(() => {
  const state = host.state
  if (!state) return { text: host.stateError ? '状态读取失败' : '读取中', tone: host.stateError ? 'error' : 'neutral' }
  if (state.connection.connected && state.connection.accepting) return { text: '在线', tone: 'success' }
  return state.connection.status === 'running' ? { text: 'QQ 未连接', tone: 'warning' } : { kind: 'runtime', value: state.connection.status }
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
  <v-navigation-drawer v-model="drawer" :permanent="!mobile" :temporary="mobile" :width="216" aria-label="主导航" class="shell-nav">
    <div class="shell-brand"><img :src="markUrl" alt="" /><strong>LenBot</strong></div>
    <nav class="shell-nav-groups">
      <div v-for="group in hostGroups" :key="group.title" class="shell-nav-group">
        <div class="shell-nav-heading">{{ group.title }}</div>
        <RouterLink v-for="item in group.areas" :key="item.id" :to="hostTarget(item.name, scene)" class="shell-nav-item"
          :class="{ active: area?.id === item.id }" :aria-current="area?.id === item.id ? 'page' : undefined">
          <v-icon :icon="icons[item.id]" size="18" />{{ item.title }}</RouterLink>
      </div>
    </nav>
    <template #append>
      <div class="shell-nav-footer"><v-btn :prepend-icon="mdiLogout" block variant="text" :loading="leaving" @click="exit">退出登录</v-btn></div>
    </template>
  </v-navigation-drawer>
  <v-app-bar flat :height="56" class="shell-bar">
    <v-btn v-if="mobile" :icon="mdiMenu" variant="text" aria-label="打开导航" @click="drawer = true" />
    <span class="shell-bar-title">{{ area?.title || route.meta.title }}</span>
    <v-select v-if="scenePicker" :model-value="scene" :items="sceneOptions" label="群聊" class="host-scene" aria-label="当前群聊"
      @update:model-value="value => router.push(sceneTarget(route, value))">
      <template #item="{ props: itemProps, item }"><v-list-item v-bind="itemProps" :subtitle="item.raw.subtitle" /></template>
    </v-select>
    <v-spacer />
    <v-chip v-if="host.state?.delivery === 'simulated'" class="mr-2">模拟发送</v-chip>
    <StatusBadge dot :kind="status.kind" :value="status.value" :text="status.text" :tone="status.tone" class="mr-2" />
    <v-btn variant="text" size="small" :prepend-icon="mdiRestart" class="mr-2" :disabled="restartFlow.waiting" @click="openRestart">重启</v-btn>
  </v-app-bar>
  <v-main tag="div">
    <main class="shell-main" id="main-content">
      <ErrorNote v-if="logoutError" title="退出登录失败" :error="logoutError" />
      <v-alert v-if="restartItems.length" type="info" role="status">
        已保存，重启后生效：{{ restartItems.join('、') }}
        <template #append><v-btn size="small" variant="text" :disabled="restartFlow.waiting" @click="openRestart">重启</v-btn></template>
      </v-alert>
      <ErrorNote v-if="host.restart?.error" title="无法确认哪些修改需要重启" :error="host.restart.error" @retry="readPendingRestart" />
      <slot v-if="!restartFlow.waiting" />
    </main>
  </v-main>
  <RestartDialog />
  <ConfirmHost />
  <v-snackbar v-model="toast" :timeout="3000" location="bottom">{{ host.toast }}</v-snackbar>
</template>

<style scoped>
.host-scene{flex:0 1 260px;min-width:160px}
</style>
