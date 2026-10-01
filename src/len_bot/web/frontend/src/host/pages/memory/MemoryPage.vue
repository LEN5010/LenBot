<script setup>
import { computed, reactive } from 'vue'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { api, sceneName } from '../../../api.js'
import { useResource } from '../../../composables/useResource.js'
import { useUnsavedChanges } from '../../../composables/useUnsavedChanges.js'
import HostPage from '../../components/HostPage.vue'
import ErrorNote from '../../components/ErrorNote.vue'
import BrowseTab from './BrowseTab.vue'
import SearchTab from './SearchTab.vue'
import IngestTab from './IngestTab.vue'
import MemorySettings from './MemorySettings.vue'

const route = useRoute(), router = useRouter()
const memory = useResource(() => api('/api/host/memory/state'))
const state = computed(() => memory.data.value)
const tabs = computed(() => state.value?.enabled
  ? [['browse', '浏览'], ['search', '搜索'], ['ingest', '后台整理'], ['settings', '设置']]
  : [['settings', '设置']])
const tab = computed(() => tabs.value.some(([key]) => key === route.query.tab) ? route.query.tab : tabs.value[0][0])
const scene = computed(() => {
  const scenes = state.value?.scenes || []
  return typeof route.query.scene === 'string' && scenes.some(item => item.scene === route.query.scene)
    ? route.query.scene : scenes[0]?.scene
})
const options = computed(() => (state.value?.scenes || []).map(item => ({ title: sceneName(item.scene), value: item.scene })))
const target = query => ({ name: 'host-memory', query: { ...route.query, ...query } })
// The search tab opens a file by switching to the browse tab with the path in the address.
const openFile = hit => router.push(target({ tab: 'browse', path: hit.path, scope: hit.scope }))

const dirty = reactive({})
const { confirmLeave } = useUnsavedChanges(computed(() => Object.values(dirty).some(Boolean)))
onBeforeRouteUpdate((to, from) => {
  if (to.query.scene === from.query.scene && to.query.tab === from.query.tab) return true
  if (!confirmLeave()) return false
  for (const key of Object.keys(dirty)) dirty[key] = false
  return true
})
</script>

<template>
  <HostPage title="记忆" description="Bot 从聊天里记住的事。可以查看、修改，也可以让它忘掉。">
    <template v-if="state?.enabled && tab !== 'settings' && options.length" #actions>
      <v-select :model-value="scene" :items="options" label="群聊" density="compact" class="scene-switch"
        @update:model-value="value => router.push(target({ scene: value, path: undefined, scope: undefined }))" />
    </template>
    <ErrorNote v-if="memory.error.value" title="读取记忆状态失败" :error="memory.error.value" />
    <template v-if="state">
      <p v-if="!state.enabled" class="surface">还没有开启记忆。在下面选择记忆的保存方式，保存后重启 LenBot。</p>
      <nav v-if="tabs.length > 1" class="memory-tabs" aria-label="记忆">
        <v-btn v-for="[key, label] in tabs" :key="key" :to="target({ tab: key })" :active="tab === key"
          :variant="tab === key ? 'tonal' : 'text'" :color="tab === key ? 'primary' : undefined"
          :aria-current="tab === key ? 'page' : undefined">{{ label }}</v-btn>
      </nav>
      <BrowseTab v-if="tab === 'browse' && scene" :key="`b${scene}`" :scene="scene" :state="state"
        @dirty="value => dirty.browse = value" />
      <SearchTab v-else-if="tab === 'search' && scene" :key="`s${scene}`" :scene="scene" @open="openFile" />
      <IngestTab v-else-if="tab === 'ingest' && scene" :key="`i${scene}`" :scene="scene" />
      <MemorySettings v-else-if="tab === 'settings'" @dirty="value => dirty.settings = value" />
    </template>
  </HostPage>
</template>

<style scoped>
.memory-tabs{display:flex;gap:4px;flex-wrap:wrap;border-bottom:1px solid var(--line);padding-bottom:8px}
.scene-switch{min-width:200px}
</style>
