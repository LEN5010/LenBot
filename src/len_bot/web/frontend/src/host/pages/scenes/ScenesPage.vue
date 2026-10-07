<script setup>
import { computed, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, sceneName, sceneNumber } from '../../../api.js'
import { useAction } from '../../../composables/useResource.js'
import { sceneTarget, useCurrentScene } from '../../../composables/useCurrentScene.js'
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
import PermissionsSection from '../settings/PermissionsSection.vue'
import SceneAvatar from '../../ui/SceneAvatar.vue'

const route = useRoute(), router = useRouter()
const tabs = [['messages', '消息'], ['settings', '设置'], ['permissions', '权限'], ['brain', '会话'], ['learning', '学习']]
const { scene, current } = useCurrentScene()
const tab = computed(() => tabs.some(([key]) => key === route.query.tab) ? route.query.tab : 'messages')
const dirty = reactive({ settings: false, learning: false, permissions: false })
useUnsavedChanges(computed(() => Object.values(dirty).some(Boolean)), { onDiscard: () => Object.assign(dirty, { settings: false, learning: false, permissions: false }) })
const scenes = computed(() => host.state?.scenes || [])
const open = value => router.push(sceneTarget(route, value))

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
  <HostPage title="群聊" description="每个群的消息、设置、权限和学习。点左边的群切换，未保存的修改会先提醒。" wide>
    <template #actions><v-btn variant="outlined" @click="adding = true">添加群聊</v-btn></template>
    <EmptyState v-if="host.state && !scene" text="还没有群聊"><v-btn color="primary" @click="adding = true">添加群聊</v-btn></EmptyState>
    <div v-else-if="scene" class="scene-workbench">
      <nav class="scene-rail" aria-label="群聊列表">
        <button v-for="item in scenes" :key="item.scene" type="button" class="rail-item" :class="{ active: item.scene === scene }"
          :aria-current="item.scene === scene ? 'page' : undefined" @click="open(item.scene)">
          <SceneAvatar :scene="item.scene" :size="34" />
          <span class="rail-text"><strong>{{ sceneName(item.scene) }}</strong>
            <span>{{ sceneNumber(item.scene) }} · {{ item.persona.name }}<template v-if="!item.chat_enabled"> · 已关闭</template></span></span>
        </button>
      </nav>
      <v-select class="scene-select" :model-value="scene" :items="scenes.map(item => ({ title: sceneName(item.scene), value: item.scene }))"
        label="群聊" hide-details @update:model-value="open" />
      <section class="scene-detail">
      <header class="detail-head"><SceneAvatar :scene="scene" :size="40" />
        <div><h2>{{ sceneName(scene) }}</h2><p class="muted">{{ sceneNumber(scene) }}<template v-if="current"> · 角色：{{ current.persona.name }}</template></p></div></header>
      <PageTabs :tabs="tabs" :model-value="tab" label="群聊内容" />
      <MessagesTab v-if="tab === 'messages'" :key="`m${scene}`" :scene="scene" />
      <SettingsTab v-else-if="tab === 'settings'" :key="`s${scene}`" :scene="scene" @dirty="value => dirty.settings = value" />
      <BrainTab v-else-if="tab === 'brain'" :key="`b${scene}`" :scene="scene" />
      <PermissionsSection v-else-if="tab === 'permissions'" :key="`p${scene}`" :scene="scene"
        @saved="() => { readPendingRestart(); notify('已保存') }" @dirty="value => dirty.permissions = value" />
      <LearningTab v-else-if="tab === 'learning'" :key="`l${scene}`" :scene="scene" @dirty="value => dirty.learning = value" />
      </section>
    </div>

    <FormDialog v-model="adding" title="添加群聊" :busy="create.busy.value">
      <v-btn-toggle v-model="kind" mandatory>
        <v-btn value="group">群聊</v-btn><v-btn value="private">私聊</v-btn></v-btn-toggle>
      <v-text-field v-model="qq" :label="kind === 'group' ? '群号' : '对方 QQ 号'" />
      <v-combobox v-model="persona" :items="personas" label="角色包目录" hint="可以填已有角色包，例如 personas/my-bot" persistent-hint />
      <p class="muted">新群默认只在被 @ 时说话，添加后可以在群设置里修改。重启后生效。</p>
      <ErrorNote v-if="create.error.value" title="没有添加成功" :error="create.error.value" />
      <template #actions>
        <v-btn color="primary" :loading="create.busy.value" :disabled="!qq.trim() || !persona" @click="addScene">添加</v-btn>
      </template>
    </FormDialog>
  </HostPage>
</template>

<style scoped>
.scene-workbench{display:grid;grid-template-columns:280px minmax(0,1fr);gap:var(--sp-6);align-items:start}
.scene-rail{display:grid;gap:2px;position:sticky;top:calc(var(--top-height, 64px) + var(--sp-4));max-height:calc(100vh - 140px);overflow-y:auto;
  padding:0 var(--sp-4) 0 0;border-right:1px solid var(--line)}
.rail-item{display:flex;align-items:center;gap:var(--sp-3);width:100%;padding:8px 10px;border:0;border-radius:var(--radius);background:none;
  color:inherit;font:inherit;text-align:left;cursor:pointer;transition:background var(--dur-1)}
.rail-item:hover{background:var(--fill)}
.rail-item.active{background:var(--selected)}
.rail-item.active strong{color:var(--primary)}
.rail-text{display:grid;min-width:0;line-height:1.35}
.rail-text strong,.rail-text span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rail-text span{font-size:var(--fs-xs);color:var(--muted)}
.scene-detail{display:grid;gap:var(--sp-4);min-width:0}
.detail-head{display:flex;align-items:center;gap:var(--sp-3)}
.detail-head h2{margin:0;font-size:var(--fs-lg, 20px)}
.detail-head p{margin:2px 0 0}
.scene-select{display:none}
@media (max-width: 960px){
  .scene-workbench{grid-template-columns:minmax(0,1fr)}
  .scene-rail{display:none}
  .scene-select{display:block}
}
</style>
