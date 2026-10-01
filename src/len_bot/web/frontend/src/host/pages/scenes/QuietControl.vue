<script setup>
import { ref } from 'vue'
import { api } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { formatTime } from '../../time.js'
import ErrorNote from '../../components/ErrorNote.vue'

// Takes effect immediately and survives restarts; not part of the saved config.
const props = defineProps({ scene: { type: String, required: true } })
const root = `/api/host/scenes/${encodeURIComponent(props.scene)}/control`
const state = useResource(() => api(root))
const action = useAction()
const hours = ref(1), direct = ref('allow')

async function change(kind) {
  const body = kind === 'quiet' ? { body: JSON.stringify({ seconds: Math.round(Number(hours.value) * 3600), direct: direct.value }) } : {}
  const value = await action.run(() => api(`${root}/${kind}`, { method: 'POST', ...body }))
  if (value) state.data.value = value
}
</script>

<template>
  <section class="surface">
    <h2>暂时安静</h2>
    <ErrorNote v-if="state.error.value" title="读取安静状态失败" :error="state.error.value" />
    <ErrorNote v-if="action.error.value" title="操作没有完成" :error="action.error.value" />
    <template v-if="state.data.value">
      <div v-if="state.data.value.temporary_quiet && state.data.value.temporary_quiet.until * 1000 > Date.now()" class="quiet-now">
        <span>Bot 暂时安静到 {{ formatTime(state.data.value.temporary_quiet.until, state.data.value.timezone) }}</span>
        <v-btn variant="tonal" color="primary" :loading="action.busy.value" @click="change('resume')">现在恢复</v-btn>
      </div>
      <div v-else class="quiet-form">
        <v-text-field v-model="hours" type="number" label="安静几小时" min="0.1" />
        <v-select v-model="direct" label="期间被 @" :items="[{ title: '照常回复', value: 'allow' }, { title: '也先不回', value: 'defer' }]" />
        <v-btn variant="tonal" color="primary" :loading="action.busy.value" @click="change('quiet')">开始安静</v-btn>
      </div>
      <p class="muted">立即生效，不用重启。消息照常保存，结束后 Bot 会看到这期间的聊天。</p>
    </template>
  </section>
</template>

<style scoped>
.quiet-now,.quiet-form{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.quiet-form .v-input{flex:0 1 200px}
.muted{margin:12px 0 0}
</style>
