<script setup>
// Start a test chat from a running group, or from an unsaved role draft when `draft` is given.
import { computed, ref } from 'vue'
import { api, sceneName } from '../../../api.js'
import { useAction } from '../../../composables/useResource.js'
import { host } from '../../store.js'
import ErrorNote from '../../components/ErrorNote.vue'

const props = defineProps({ scene: { type: String, default: null }, draft: { type: Function, default: null } })
const emit = defineEmits(['started'])
const chosen = ref(props.scene), context = ref(0), agreed = ref(false)
const options = computed(() => (host.state?.scenes || []).map(item => ({ title: `${sceneName(item.scene)} · ${item.persona.name}`, value: item.scene })))
const start = useAction()
async function submit() {
  const result = await start.run(async () => api('/api/host/trials', { method: 'POST', body: JSON.stringify({
    scene: chosen.value, acknowledge_model_cost: true, context_messages: context.value,
    ...(props.draft ? { persona_draft: await props.draft() } : {}) }) }))
  if (!result) return
  agreed.value = false
  emit('started', result)
}
</script>

<template>
  <form class="surface start" @submit.prevent="submit">
    <h2>{{ draft ? '先试聊看看' : '开始测试' }}</h2>
    <p v-if="draft" class="muted">用上面的设定聊几句，没保存的修改也算在内。回复不会发到 QQ。</p>
    <v-select v-if="!draft" v-model="chosen" :items="options" label="用哪个群的设置" />
    <v-select v-model="context" label="开头"
      :items="[{ title: '从空白开始', value: 0 }, { title: '复制群里最近 20 条消息', value: 20 }, { title: '复制最近 50 条', value: 50 }, { title: '复制最近 100 条', value: 100 }]" />
    <v-checkbox v-model="agreed" label="我知道测试会调用模型并产生费用" hide-details />
    <ErrorNote v-if="start.error.value" title="没有开始成功" :error="start.error.value" />
    <div><v-btn type="submit" color="primary" :loading="start.busy.value" :disabled="!agreed || !chosen">开始</v-btn></div>
  </form>
</template>

<style scoped>
.start{display:grid;gap:12px}
.start>*{max-width:560px}
.start p{margin:0}
</style>
