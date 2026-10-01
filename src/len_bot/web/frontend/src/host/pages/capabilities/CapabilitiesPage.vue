<script setup>
import { computed, reactive } from 'vue'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { sceneName } from '../../../api.js'
import { useUnsavedChanges } from '../../../composables/useUnsavedChanges.js'
import { host } from '../../store.js'
import HostPage from '../../components/HostPage.vue'
import ToolsSection from './ToolsSection.vue'
import SkillsSection from './SkillsSection.vue'
import PluginsSection from './PluginsSection.vue'
import McpSection from './McpSection.vue'
import WebServicesSection from './WebServicesSection.vue'
import WorkerSection from './WorkerSection.vue'
import BrowserSection from './BrowserSection.vue'

const route = useRoute(), router = useRouter()
const tabs = [['tools', '工具与技能'], ['plugins', '插件'], ['mcp', 'MCP 服务'], ['web', '网页'], ['tasks', '独立任务']]
const perScene = ['tools', 'plugins']
const tab = computed(() => tabs.some(([key]) => key === route.query.tab) ? route.query.tab : 'tools')
const scenes = computed(() => host.state?.scenes || [])
const scene = computed(() => typeof route.query.scene === 'string' && scenes.value.some(item => item.scene === route.query.scene)
  ? route.query.scene : scenes.value[0]?.scene)
const options = computed(() => scenes.value.map(item => ({ title: sceneName(item.scene), value: item.scene })))

const dirty = reactive({})
const anyDirty = computed(() => Object.values(dirty).some(Boolean))
const { confirmLeave } = useUnsavedChanges(anyDirty)
onBeforeRouteUpdate(() => {
  if (!confirmLeave()) return false
  for (const key of Object.keys(dirty)) dirty[key] = false
  return true
})
const target = query => ({ name: 'host-capabilities', query: { ...route.query, ...query } })
</script>

<template>
  <HostPage title="能力" description="Bot 在群里能用的工具、插件和外部服务。">
    <template v-if="perScene.includes(tab) && options.length" #actions>
      <v-select :model-value="scene" :items="options" label="群聊" density="compact" class="scene-switch"
        @update:model-value="value => router.push(target({ scene: value }))" />
    </template>
    <nav class="cap-tabs" aria-label="能力分类">
      <v-btn v-for="[key, label] in tabs" :key="key" :to="target({ tab: key })" :active="tab === key"
        :variant="tab === key ? 'tonal' : 'text'" :color="tab === key ? 'primary' : undefined"
        :aria-current="tab === key ? 'page' : undefined">{{ label }}</v-btn>
    </nav>
    <template v-if="tab === 'tools' && scene">
      <ToolsSection :key="`t${scene}`" :scene="scene" @dirty="value => dirty.tools = value" />
      <SkillsSection :key="`s${scene}`" :scene="scene" @dirty="value => dirty.skills = value" />
    </template>
    <PluginsSection v-else-if="tab === 'plugins' && scene" :key="`p${scene}`" :scene="scene" @dirty="value => dirty.plugins = value" />
    <McpSection v-else-if="tab === 'mcp'" @dirty="value => dirty.mcp = value" />
    <WebServicesSection v-else-if="tab === 'web'" @dirty="value => dirty.web = value" />
    <template v-else-if="tab === 'tasks'">
      <WorkerSection @dirty="value => dirty.worker = value" />
      <BrowserSection @dirty="value => dirty.browser = value" />
    </template>
  </HostPage>
</template>

<style scoped>
.cap-tabs{display:flex;gap:4px;flex-wrap:wrap;border-bottom:1px solid var(--line);padding-bottom:8px}
.scene-switch{min-width:200px}
</style>
