<script setup>
// Try the form as it is now, saved or not, in a test chat that never reaches QQ.
import { ref } from 'vue'
import { api } from '../../../api.js'
import { useAction } from '../../../composables/useResource.js'
import ErrorNote from '../../components/ErrorNote.vue'
import ChatPanel from '../../components/ChatPanel.vue'
import TrialStart from '../trial/TrialStart.vue'

const props = defineProps({ scene: { type: String, required: true }, draft: { type: Function, required: true }, disabled: Boolean })
const trial = ref(null), stop = useAction()
async function stopTrial() {
  if (!window.confirm('结束这次试聊？聊天记录会保留在对话测试页。')) return
  const result = await stop.run(() => api(`/api/host/trials/${encodeURIComponent(trial.value.id)}/stop`, { method: 'POST' }))
  if (result) trial.value = null
}
</script>

<template>
  <section v-if="trial" class="surface draft-trial">
    <div class="head">
      <h2>试聊：{{ trial.persona }}</h2>
      <div class="actions">
        <v-btn variant="text" :to="{ name: 'host-trials', query: { trial: trial.id } }">在对话测试页打开</v-btn>
        <v-btn variant="outlined" color="warning" :loading="stop.busy.value" @click="stopTrial">结束试聊</v-btn>
      </div>
    </div>
    <p class="muted">用的是开始试聊那一刻的设定，之后再改不会影响这次试聊。</p>
    <ErrorNote v-if="stop.error.value" title="没有结束成功" :error="stop.error.value" />
    <ChatPanel :key="trial.id" :api-base="`/api/host/trials/${encodeURIComponent(trial.id)}`" />
  </section>
  <p v-else-if="disabled" class="muted">把上面标出的问题改好后，可以先试聊再保存。</p>
  <TrialStart v-else :scene="scene" :draft="props.draft" @started="value => trial = value" />
</template>

<style scoped>
.draft-trial{display:grid;gap:12px}
.draft-trial p{margin:0}
.head{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}
.actions{display:flex;gap:8px}
</style>
