<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'
import { mdiViewDashboardOutline, mdiChatProcessingOutline, mdiForumOutline, mdiAccountOutline, mdiBookOpenPageVariantOutline,
  mdiBriefcaseOutline, mdiFolderOutline, mdiToolboxOutline, mdiChip, mdiTimelineTextOutline, mdiCogOutline, mdiMenu, mdiLogout, mdiRestart,
  mdiPuzzleOutline, mdiBellOutline } from '@mdi/js'
import { logout, useAuth } from '../composables/useAuth.js'
import { confirm } from '../composables/useConfirm.js'
import { useCurrentScene } from '../composables/useCurrentScene.js'
import { host, readHostState, readOverview, readPendingRestart, readSceneTitles, readUpdates } from '../host/store.js'
import { attentionItems } from '../host/attention.js'
import { sectionLabel } from '../host/labels.js'
import { sceneName } from '../api.js'
import { hostAreas, hostGroups, hostTarget } from '../router/hostNavigation.js'
import ErrorNote from '../host/ui/ErrorNote.vue'
import StatusBadge from '../host/ui/StatusBadge.vue'
import ConfirmHost from '../host/ui/ConfirmHost.vue'
import RestartDialog from '../host/components/RestartDialog.vue'
import { restartFlow, openRestart } from '../host/restart.js'
import markUrl from '../assets/lenbot-mark.svg'

const route = useRoute(), router = useRouter(), { mobile } = useDisplay(), auth = useAuth()
const drawer = ref(!mobile.value), leaving = ref(false), logoutError = ref(null)
const icons = { home: mdiViewDashboardOutline, trial: mdiChatProcessingOutline, scenes: mdiForumOutline, persona: mdiAccountOutline,
  memory: mdiBookOpenPageVariantOutline, tasks: mdiBriefcaseOutline, resources: mdiFolderOutline, capabilities: mdiToolboxOutline, models: mdiChip,
  logs: mdiTimelineTextOutline, settings: mdiCogOutline, plugins: mdiPuzzleOutline }
const area = computed(() => hostAreas.find(item => item.pages.includes(route.name)))
const group = computed(() => hostGroups.find(item => item.areas.includes(area.value)))
const { scene } = useCurrentScene()
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
    ...value.plugins.map(item => `插件 ${item.name}`), ...value.personas.map(item => `角色 ${item.name}`)])]
})
const attention = computed(() => attentionItems(host.state, host.overview).length)
const newer = computed(() => host.updates?.check?.update_available ? host.updates.check.latest : '')
const account = computed(() => auth.user?.username || '')
const toast = computed({ get: () => Boolean(host.toast), set: value => { if (!value) host.toast = '' } })

function refresh() {
  readHostState()
  readPendingRestart()
  readOverview()
}
onMounted(() => { refresh(); readUpdates(); readSceneTitles() })
watch(() => route.name, refresh)
// Cached names arrive at once; each new connection reads the names again.
watch(() => host.state?.connection.connected, connected => { if (connected) readSceneTitles() })
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
  <v-navigation-drawer v-model="drawer" :permanent="!mobile" :temporary="mobile" :width="232" floating aria-label="主导航" class="shell-nav">
    <RouterLink :to="{ name: 'host-overview' }" class="shell-brand"><img :src="markUrl" alt="" /><div><strong>LenBot</strong><span>控制台</span></div></RouterLink>
    <nav class="shell-nav-groups">
      <div v-for="group in hostGroups" :key="group.title" class="shell-nav-group">
        <div class="shell-nav-heading">{{ group.title }}</div>
        <RouterLink v-for="item in group.areas" :key="item.id" :to="hostTarget(item.name, scene)" class="shell-nav-item"
          :class="{ active: area?.id === item.id }" :aria-current="area?.id === item.id ? 'page' : undefined">
          <v-icon :icon="icons[item.id]" size="20" />{{ item.title }}</RouterLink>
      </div>
    </nav>
  </v-navigation-drawer>
  <v-main class="shell-wrap">
    <header class="shell-top">
      <v-btn v-if="mobile" :icon="mdiMenu" variant="text" aria-label="打开导航" @click="drawer = true" />
      <nav class="shell-crumbs" aria-label="当前位置">
        <span v-if="group" class="muted">{{ group.title }}</span><span v-if="group" class="sep">/</span>
        <strong>{{ area?.title || route.meta.title }}</strong>
      </nav>
      <v-spacer />
      <RouterLink v-if="newer" :to="{ name: 'host-system', query: { tab: 'updates' } }" class="shell-pill shell-update">
        <span class="shell-update-dot" />新版本 {{ newer }}</RouterLink>
      <span v-if="host.state?.delivery === 'simulated'" class="shell-pill muted">模拟发送</span>
      <span class="shell-pill"><StatusBadge dot :pulse="status.tone === 'success'" :kind="status.kind" :value="status.value" :text="status.text" :tone="status.tone" /></span>
      <v-btn :to="{ name: 'host-overview' }" :active="false" variant="text" icon size="small" :aria-label="attention ? `${attention} 件事需要处理` : '没有需要处理的事'">
        <v-badge v-if="attention" :content="attention" color="error" floating><v-icon :icon="mdiBellOutline" /></v-badge>
        <v-icon v-else :icon="mdiBellOutline" />
      </v-btn>
      <v-menu location="bottom end">
        <template #activator="{ props: menu }">
          <v-btn v-bind="menu" variant="text" icon size="small" aria-label="账号菜单"><span class="shell-account">{{ account.slice(0, 1).toUpperCase() || '?' }}</span></v-btn>
        </template>
        <v-list density="compact" min-width="200">
          <v-list-subheader>{{ account }}</v-list-subheader>
          <v-list-item :prepend-icon="mdiRestart" title="重启 LenBot" :disabled="restartFlow.waiting" @click="openRestart" />
          <v-list-item :prepend-icon="mdiLogout" title="退出登录" :disabled="leaving" @click="exit" />
        </v-list>
      </v-menu>
    </header>
    <main class="shell-main" id="main-content">
      <ErrorNote v-if="logoutError" title="退出登录失败" :error="logoutError" />
      <v-alert v-if="restartItems.length" type="info" role="status">
        已保存，重启后生效：{{ restartItems.join('、') }}
        <template #append><v-btn size="small" variant="outlined" :disabled="restartFlow.waiting" @click="openRestart">重启</v-btn></template>
      </v-alert>
      <ErrorNote v-if="host.restart?.error" title="无法确认哪些修改需要重启" :error="host.restart.error" @retry="readPendingRestart" />
      <slot v-if="!restartFlow.waiting" />
    </main>
  </v-main>
  <RestartDialog />
  <ConfirmHost />
  <v-snackbar v-model="toast" :timeout="3000" location="bottom" color="#1d1b20" rounded="lg">{{ host.toast }}</v-snackbar>
</template>
