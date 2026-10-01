<script setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, sceneName } from '../../../api.js'
import { useAction } from '../../../composables/useResource.js'
import { host, notify, readPendingRestart } from '../../store.js'
import HostPage from '../../components/HostPage.vue'
import ErrorNote from '../../components/ErrorNote.vue'
import MessagesTab from './MessagesTab.vue'
import SettingsTab from './SettingsTab.vue'
import BrainTab from './BrainTab.vue'
import LearningTab from './LearningTab.vue'

const route = useRoute(), router = useRouter()
const tabs = [['messages', '消息'], ['settings', '设置'], ['brain', '大脑'], ['learning', '学习']]
const scene = computed(() => typeof route.query.scene === 'string' ? route.query.scene : '')
const tab = computed(() => tabs.some(([key]) => key === route.query.tab) ? route.query.tab : 'messages')
const current = computed(() => host.state?.scenes.find(item => item.scene === scene.value))
const options = computed(() => (host.state?.scenes || []).map(item => ({ title: sceneName(item.scene), value: item.scene })))
const tabTarget = key => ({ name: 'host-scenes', query: { scene: scene.value, tab: key } })

const adding = ref(false), kind = ref('group'), qq = ref(''), persona = ref(''), memoryUser = ref(''), memoryKey = ref('')
const create = useAction()
const personas = computed(() => [...new Set((host.state?.scenes || []).map(item => item.persona_path))].filter(Boolean))
async function addScene() {
  const body = { scene: `${kind.value}:${qq.value.trim()}`, persona: persona.value }
  if (memoryUser.value) body.memory_identity = { user_id: memoryUser.value, api_key: memoryKey.value }
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
  <HostPage v-if="!scene" title="群聊">
    <template #actions><v-btn color="primary" @click="adding = true">添加群聊</v-btn></template>
    <section v-if="host.state" class="surface">
      <p v-if="!host.state.scenes.length" class="empty-state">还没有群聊</p>
      <div class="scene-grid">
        <RouterLink v-for="item in host.state.scenes" :key="item.scene" :to="{ name: 'host-scenes', query: { scene: item.scene } }" class="scene-card">
          <strong>{{ sceneName(item.scene) }}</strong><span>{{ item.persona.name }}</span>
        </RouterLink>
      </div>
    </section>
    <v-dialog v-model="adding" max-width="520">
      <v-card title="添加群聊">
        <v-card-text class="add-form">
          <v-btn-toggle v-model="kind" mandatory density="comfortable" color="primary">
            <v-btn value="group">群聊</v-btn><v-btn value="private">私聊</v-btn></v-btn-toggle>
          <v-text-field v-model="qq" :label="kind === 'group' ? '群号' : '对方 QQ'" inputmode="numeric" />
          <v-combobox v-model="persona" :items="personas" label="角色包目录" hint="可以填已有角色包，例如 personas/my-bot" persistent-hint />
          <template v-if="host.state?.memory_backend === 'openviking'">
            <v-text-field v-model="memoryUser" label="远端记忆用户 ID" />
            <v-text-field v-model="memoryKey" label="远端记忆 API Key" type="password" autocomplete="off" />
          </template>
          <p class="muted">新群默认只在被 @ 时说话，添加后可以在群设置里修改。重启后生效。</p>
          <ErrorNote v-if="create.error.value" title="没有添加成功" :error="create.error.value" />
        </v-card-text>
        <v-card-actions><v-spacer /><v-btn @click="adding = false">取消</v-btn>
          <v-btn color="primary" :loading="create.busy.value" :disabled="!qq.trim() || !persona" @click="addScene">添加</v-btn></v-card-actions>
      </v-card>
    </v-dialog>
  </HostPage>

  <HostPage v-else :title="sceneName(scene)" :description="current ? `角色：${current.persona.name}` : ''">
    <template #actions>
      <v-select :model-value="scene" :items="options" label="切换群聊" density="compact" class="scene-switch"
        @update:model-value="value => router.push({ name: 'host-scenes', query: { scene: value, tab } })" />
    </template>
    <nav class="scene-tabs" aria-label="群聊内容">
      <v-btn v-for="[key, label] in tabs" :key="key" :to="tabTarget(key)" :active="tab === key" :variant="tab === key ? 'tonal' : 'text'"
        :color="tab === key ? 'primary' : undefined" :aria-current="tab === key ? 'page' : undefined">{{ label }}</v-btn>
    </nav>
    <p v-if="host.state && !current" class="surface">当前运行的配置里没有这个群。</p>
    <template v-else>
      <MessagesTab v-if="tab === 'messages'" :key="`m${scene}`" :scene="scene" />
      <SettingsTab v-else-if="tab === 'settings'" :key="`s${scene}`" :scene="scene" />
      <BrainTab v-else-if="tab === 'brain'" :key="`b${scene}`" :scene="scene" />
      <LearningTab v-else-if="tab === 'learning'" :key="`l${scene}`" :scene="scene" />
    </template>
  </HostPage>
</template>

<style scoped>
.scene-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,200px),1fr));gap:10px}
.scene-card{border:1px solid var(--line);border-radius:10px;padding:14px;color:inherit}
.scene-card:hover{border-color:var(--primary);text-decoration:none}
.scene-card span{display:block;color:var(--muted);font-size:13px}
.scene-switch{min-width:220px}
.scene-tabs{display:flex;gap:6px;flex-wrap:wrap;border-bottom:1px solid var(--line);padding-bottom:10px}
.add-form{display:grid;gap:16px}
</style>
