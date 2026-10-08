<script setup>
import { computed, reactive } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../../../api.js'
import { useResource } from '../../../composables/useResource.js'
import { useCurrentScene } from '../../../composables/useCurrentScene.js'
import { useUnsavedChanges } from '../../../composables/useUnsavedChanges.js'
import HostPage from '../../ui/HostPage.vue'
import PageTabs from '../../ui/PageTabs.vue'
import ResourceState from '../../ui/ResourceState.vue'
import EmptyState from '../../ui/EmptyState.vue'
import BrowseTab from './BrowseTab.vue'
import SearchTab from './SearchTab.vue'
import IngestTab from './IngestTab.vue'
import MemorySettings from './MemorySettings.vue'
import IndexTab from './IndexTab.vue'
import { host } from '../../store.js'

const route = useRoute(), router = useRouter()
const memory = useResource(() => api('/api/host/memory/state'))
const state = computed(() => memory.data.value)
const index = computed(() => host.overview?.memory_index ?? state.value?.index)
const tabs = computed(() => state.value?.enabled
  ? [['browse', '浏览'], ['search', '搜索'], ['ingest', '后台整理'], ['index', '索引'], ['settings', '设置']]
  : [['settings', '设置']])
const tab = computed(() => tabs.value.some(([key]) => key === route.query.tab) ? route.query.tab : tabs.value[0][0])
const { scene } = useCurrentScene()
const remembered = computed(() => (state.value?.scenes || []).some(item => item.scene === scene.value))
const openFile = hit => router.push({ name: 'host-memory', query: { ...route.query, tab: 'browse', path: hit.path, scope: hit.scope } })

const dirty = reactive({})
useUnsavedChanges(computed(() => Object.values(dirty).some(Boolean)), { onDiscard: () => { for (const key of Object.keys(dirty)) dirty[key] = false } })
</script>

<template>
  <HostPage title="记忆" :wide="tab === 'browse'">
    <ResourceState :resource="memory" error-title="读取记忆状态失败">
      <v-alert v-if="!state.enabled" type="info">还没有开启记忆。在下面选择记忆的保存方式，保存后重启 LenBot。</v-alert>
      <v-alert v-if="index?.needs_rebuild && tab !== 'index'" type="warning">
        <div class="index-status"><span>向量配置与记忆索引不匹配，当前使用全文检索。</span>
          <v-btn variant="outlined" :to="{ name: 'host-memory', query: { ...route.query, tab: 'index' } }">重建索引</v-btn></div>
      </v-alert>
      <PageTabs v-if="tabs.length > 1" :tabs="tabs" :model-value="tab" label="记忆" />
      <MemorySettings v-if="tab === 'settings'" @dirty="value => dirty.settings = value" />
      <IndexTab v-else-if="tab === 'index'" :index="index" :command="state.reindex_command" :container="state.maintenance_container" />
      <EmptyState v-else-if="scene && !remembered" text="这个群没有使用记忆" />
      <template v-else-if="scene">
        <BrowseTab v-if="tab === 'browse'" :key="`b${scene}`" :scene="scene" :state="state" @dirty="value => dirty.browse = value" />
        <SearchTab v-else-if="tab === 'search'" :key="`s${scene}`" :scene="scene" @open="openFile" />
        <IngestTab v-else-if="tab === 'ingest'" :key="`i${scene}`" :scene="scene" />
      </template>
    </ResourceState>
  </HostPage>
</template>

<style scoped>
.index-status{display:flex;align-items:center;justify-content:space-between;gap:var(--sp-4);flex-wrap:wrap}
</style>
