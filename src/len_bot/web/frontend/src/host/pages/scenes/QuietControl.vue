<script setup>
import { ref } from 'vue'
import { api } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { formatTime } from '../../time.js'
import Panel from '../../ui/Panel.vue'
import ResourceState from '../../ui/ResourceState.vue'
import ErrorNote from '../../ui/ErrorNote.vue'

// Takes effect immediately and survives restarts; not part of the saved config.
const props = defineProps({ scene: { type: String, required: true } })
const root = `/api/host/scenes/${encodeURIComponent(props.scene)}/control`
const state = useResource(() => api(root))
const action = useAction()
const hours = ref(1), direct = ref('allow')
const quietUntil = value => value.temporary_quiet && value.temporary_quiet.until * 1000 > Date.now() ? value.temporary_quiet.until : null

async function change(kind) {
  const body = kind === 'quiet' ? { body: JSON.stringify({ seconds: Math.round(Number(hours.value) * 3600), direct: direct.value }) } : {}
  const value = await action.run(() => api(`${root}/${kind}`, { method: 'POST', ...body }))
  if (value) state.data.value = value
}
</script>

<template>
  <Panel title="暂时安静" description="立即生效。消息照常保存，结束后 Bot 会看到这期间的聊天。">
    <ErrorNote v-if="action.error.value" title="操作没有完成" :error="action.error.value" />
    <ResourceState :resource="state" error-title="读取安静状态失败" v-slot="{ data }">
      <div v-if="quietUntil(data)" class="inline">
        <span>Bot 暂时安静到 {{ formatTime(quietUntil(data), data.timezone) }}</span>
        <v-btn variant="tonal" color="primary" :loading="action.busy.value" @click="change('resume')">现在恢复</v-btn>
      </div>
      <div v-else class="quiet-form">
        <v-text-field v-model="hours" type="number" label="安静几小时" min="0.1" />
        <v-select v-model="direct" label="期间被 @" :items="[{ title: '照常回复', value: 'allow' }, { title: '也先不回', value: 'defer' }]" />
        <v-btn variant="tonal" color="primary" :loading="action.busy.value" @click="change('quiet')">开始安静</v-btn>
      </div>
    </ResourceState>
  </Panel>
</template>

<style scoped>
.quiet-form{display:flex;gap:var(--sp-3);align-items:center;flex-wrap:wrap}
.quiet-form .v-input{flex:0 1 200px}
</style>
