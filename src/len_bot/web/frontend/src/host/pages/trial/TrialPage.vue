<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, sceneName } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { confirm } from '../../../composables/useConfirm.js'
import { host, notify } from '../../store.js'
import { formatTime } from '../../time.js'
import HostPage from '../../ui/HostPage.vue'
import ErrorNote from '../../ui/ErrorNote.vue'
import Fold from '../../ui/Fold.vue'
import DevOnly from '../../ui/DevOnly.vue'
import ChatPanel from '../../components/ChatPanel.vue'
import TrialStart from './TrialStart.vue'

const route = useRoute(), router = useRouter()
const trials = useResource(() => api('/api/host/trials'))
const items = computed(() => trials.data.value?.items || [])
const active = computed(() => items.value.find(item => item.active) || null)
const selected = computed(() => items.value.find(item => item.id === route.query.trial) || active.value)
const pick = id => router.push({ name: 'host-trials', query: { ...route.query, trial: id } })
const past = computed(() => items.value.map(item => ({ title: `${sceneName(item.scene)} · ${formatTime(item.created)}${item.active ? '（进行中）' : ''}`, value: item.id })))
const startScene = computed(() => typeof route.query.scene === 'string' ? route.query.scene : host.state?.scenes[0]?.scene)

const stop = useAction()
async function stopTrial() {
  if (!await confirm({ title: '结束这次测试？', text: '正在进行的模型请求会被取消，聊天记录会保留。', confirmLabel: '结束' })) return
  if (await stop.run(() => api(`/api/host/trials/${encodeURIComponent(active.value.id)}/stop`, { method: 'POST' }))) {
    notify('已结束')
    trials.reload()
  }
}
async function started(trial) {
  await trials.reload()
  pick(trial.id)
}
const newOne = ref(false)
watch(active, value => { if (value) newOne.value = false })
</script>

<template>
  <HostPage title="对话测试" description="用和群里一样的角色和模型试着聊几句，回复只在这里显示。" wide>
    <template #actions>
      <v-select v-if="past.length" :model-value="selected?.id" :items="past" label="测试记录" density="compact" hide-details class="past" @update:model-value="pick" />
      <v-btn v-if="active" variant="outlined" :loading="stop.busy.value" @click="stopTrial">结束测试</v-btn>
      <v-btn v-else-if="selected && !newOne" color="primary" @click="newOne = true">开始新的测试</v-btn>
    </template>
    <ErrorNote v-if="trials.error.value" title="读取测试列表失败" :error="trials.error.value" @retry="trials.reload()" />
    <ErrorNote v-if="stop.error.value" title="没有结束成功" :error="stop.error.value" />
    <v-progress-linear v-if="trials.loading.value && !trials.data.value" indeterminate color="primary" />
    <template v-if="trials.data.value">
      <TrialStart v-if="!active && (!selected || newOne)" :scene="startScene" @started="started" />
      <template v-if="selected && !(newOne && !active)">
        <div class="meta">
          <p class="muted">{{ sceneName(selected.scene) }} · {{ selected.persona }}{{ selected.persona_source === 'draft' ? '（角色页的草稿）' : '' }}{{ selected.active ? '' : ' · 已结束' }}</p>
          <Fold v-if="selected.context.length" :label="`开头复制了 ${selected.context.length} 条群聊消息`" code>{{ selected.context.join('\n\n') }}</Fold>
        </div>
        <ChatPanel :key="selected.id" :api-base="`/api/host/trials/${encodeURIComponent(selected.id)}`" />
        <DevOnly label="测试范围" :json="{ memory: selected.memory, models: selected.models, excluded_tools: selected.excluded_tools, root: selected.root, draft_path: selected.draft_path }" />
      </template>
    </template>
  </HostPage>
</template>

<style scoped>
.past{min-width:240px}
.meta{display:grid;gap:var(--sp-2)}
.meta p{margin:0}
</style>
