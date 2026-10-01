<script setup>
import { computed, reactive } from 'vue'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { sceneName } from '../../../api.js'
import { useUnsavedChanges } from '../../../composables/useUnsavedChanges.js'
import { host } from '../../store.js'
import HostPage from '../../components/HostPage.vue'
import ProfileTab from './ProfileTab.vue'
import KnowledgeTab from './KnowledgeTab.vue'
import StickersTab from './StickersTab.vue'
import PackageTab from './PackageTab.vue'

const route = useRoute(), router = useRouter()
const tabs = [['profile', '设定'], ['knowledge', '资料'], ['stickers', '表情'], ['package', '导入导出']]
const tab = computed(() => tabs.some(([key]) => key === route.query.tab) ? route.query.tab : 'profile')
const scenes = computed(() => host.state?.scenes || [])
const scene = computed(() => typeof route.query.scene === 'string' && scenes.value.some(item => item.scene === route.query.scene)
  ? route.query.scene : scenes.value[0]?.scene)
const options = computed(() => scenes.value.map(item => ({ title: `${sceneName(item.scene)} · ${item.persona.name}`, value: item.scene })))

const dirty = reactive({})
const { confirmLeave } = useUnsavedChanges(computed(() => Object.values(dirty).some(Boolean)))
onBeforeRouteUpdate(() => {
  if (!confirmLeave()) return false
  for (const key of Object.keys(dirty)) dirty[key] = false
  return true
})
const target = query => ({ name: 'host-persona', query: { ...route.query, ...query } })
</script>

<template>
  <HostPage title="角色" description="Bot 在群里扮演的人：名字、性格、说话方式、资料和表情。改完重启后生效。">
    <template v-if="options.length" #actions>
      <v-select :model-value="scene" :items="options" label="群聊" density="compact" class="scene-switch"
        @update:model-value="value => router.push(target({ scene: value }))" />
    </template>
    <nav class="persona-tabs" aria-label="角色内容">
      <v-btn v-for="[key, label] in tabs" :key="key" :to="target({ tab: key })" :active="tab === key"
        :variant="tab === key ? 'tonal' : 'text'" :color="tab === key ? 'primary' : undefined"
        :aria-current="tab === key ? 'page' : undefined">{{ label }}</v-btn>
    </nav>
    <template v-if="scene">
      <ProfileTab v-if="tab === 'profile'" :key="`p${scene}`" :scene="scene" @dirty="value => dirty.profile = value" />
      <KnowledgeTab v-else-if="tab === 'knowledge'" :key="`k${scene}`" :scene="scene" @dirty="value => dirty.knowledge = value" />
      <StickersTab v-else-if="tab === 'stickers'" :key="`s${scene}`" :scene="scene" @dirty="value => dirty.stickers = value" />
      <PackageTab v-else :key="`f${scene}`" :scene="scene" @dirty="value => dirty.package = value" />
    </template>
  </HostPage>
</template>

<style scoped>
.persona-tabs{display:flex;gap:4px;flex-wrap:wrap;border-bottom:1px solid var(--line);padding-bottom:8px}
.scene-switch{min-width:240px}
</style>
