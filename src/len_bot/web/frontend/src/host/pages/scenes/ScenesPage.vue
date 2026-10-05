<script setup>
import { computed, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api, sceneName, sceneNumber } from '../../../api.js'
import { useAction } from '../../../composables/useResource.js'
import { useCurrentScene } from '../../../composables/useCurrentScene.js'
import { useUnsavedChanges } from '../../../composables/useUnsavedChanges.js'
import { host, notify, readPendingRestart } from '../../store.js'
import HostPage from '../../ui/HostPage.vue'
import PageTabs from '../../ui/PageTabs.vue'
import EmptyState from '../../ui/EmptyState.vue'
import ErrorNote from '../../ui/ErrorNote.vue'
import FormDialog from '../../ui/FormDialog.vue'
import MessagesTab from './MessagesTab.vue'
import SettingsTab from './SettingsTab.vue'
import BrainTab from './BrainTab.vue'
import LearningTab from './LearningTab.vue'

const route = useRoute()
const tabs = [['messages', '消息'], ['settings', '设置'], ['brain', '大脑'], ['learning', '学习']]
const { scene, current } = useCurrentScene()
const tab = computed(() => tabs.some(([key]) => key === route.query.tab) ? route.query.tab : 'messages')
const dirty = reactive({ settings: false, learning: false })
useUnsavedChanges(computed(() => Object.values(dirty).some(Boolean)), { onDiscard: () => Object.assign(dirty, { settings: false, learning: false }) })

const adding = ref(false), kind = ref('group'), qq = ref(''), persona = ref('')
const create = useAction()
const personas = computed(() => [...new Set((host.state?.scenes || []).map(item => item.persona_path))].filter(Boolean))
async function addScene() {
  const body = { scene: `onebot:${kind.value}:${qq.value.trim()}`, persona: persona.value }
  const result = await create.run(() => api('/api/host/settings/scenes', { method: 'POST', body: JSON.stringify(body) }))
  if (result) {
    adding.value = false
    qq.value = ''
    readPendingRestart()
    notify('已添加，重启后生效')
  }
}
</script>

<template>
  <HostPage :title="scene ? sceneName(scene) : '群聊'" :description="current ? `${sceneNumber(scene)} · 角色：${current.persona.name}` : ''">
    <template #actions><v-btn variant="tonal" color="primary" @click="adding = true">添加群聊</v-btn></template>
    <EmptyState v-if="host.state && !scene" text="还没有群聊"><v-btn color="primary" @click="adding = true">添加群聊</v-btn></EmptyState>
    <template v-else-if="scene">
      <PageTabs :tabs="tabs" :model-value="tab" label="群聊内容" />
      <MessagesTab v-if="tab === 'messages'" :key="`m${scene}`" :scene="scene" />
      <SettingsTab v-else-if="tab === 'settings'" :key="`s${scene}`" :scene="scene" @dirty="value => dirty.settings = value" />
      <BrainTab v-else-if="tab === 'brain'" :key="`b${scene}`" :scene="scene" />
      <LearningTab v-else-if="tab === 'learning'" :key="`l${scene}`" :scene="scene" @dirty="value => dirty.learning = value" />
    </template>

    <FormDialog v-model="adding" title="添加群聊" :busy="create.busy.value">
      <v-btn-toggle v-model="kind" mandatory>
        <v-btn value="group">群聊</v-btn><v-btn value="private">私聊</v-btn></v-btn-toggle>
      <v-text-field v-model="qq" :label="kind === 'group' ? '群号' : '对方 平台账号'"  />
      <v-combobox v-model="persona" :items="personas" label="角色包目录" hint="可以填已有角色包，例如 personas/my-bot" persistent-hint />
      <p class="muted">新群默认只在被 @ 时说话，添加后可以在群设置里修改。重启后生效。</p>
      <ErrorNote v-if="create.error.value" title="没有添加成功" :error="create.error.value" />
      <template #actions>
        <v-btn color="primary" :loading="create.busy.value" :disabled="!qq.trim() || !persona" @click="addScene">添加</v-btn>
      </template>
    </FormDialog>
  </HostPage>
</template>
