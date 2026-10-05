<script setup>
// Start a test chat from a running group, or from an unsaved role draft when `draft` is given.
import { computed, ref } from 'vue'
import { api, sceneName } from '../../../api.js'
import { useAction } from '../../../composables/useResource.js'
import { host } from '../../store.js'
import Panel from '../../ui/Panel.vue'
import ErrorNote from '../../ui/ErrorNote.vue'

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
  <Panel tag="form" :title="draft ? '先试聊看看' : '开始测试'"
    :description="draft ? '用上面的设定聊几句，没保存的修改也算在内。回复不会发到 QQ。' : ''" @submit.prevent="submit">
    <div class="fields">
      <v-select v-if="!draft" v-model="chosen" :items="options" label="用哪个群的设置" />
      <v-select v-model="context" label="开头"
        :items="[{ title: '从空白开始', value: 0 }, { title: '复制群里最近 20 条消息', value: 20 }, { title: '复制最近 50 条', value: 50 }, { title: '复制最近 100 条', value: 100 }]" />
      <v-checkbox v-model="agreed" label="我知道测试会调用模型并产生费用" hide-details />
    </div>
    <ErrorNote v-if="start.error.value" title="没有开始成功" :error="start.error.value" />
    <template #footer><v-spacer /><v-btn type="submit" color="primary" :loading="start.busy.value" :disabled="!agreed || !chosen">开始</v-btn></template>
  </Panel>
</template>

<style scoped>
.fields{display:grid;gap:var(--sp-3);max-width:560px}
</style>
