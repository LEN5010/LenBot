<script setup>
import { computed, ref, watch } from 'vue'
import { api, queryString } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { notify } from '../../store.js'
import ErrorNote from '../../components/ErrorNote.vue'

const props = defineProps({ scene: { type: String, required: true }, operator: { type: String, required: true } })
const emit = defineEmits(['dirty', 'created', 'close'])
const goal = ref(''), deliverable = ref(''), context = ref(''), materials = ref([]), accountBrowser = ref(false)
const shared = useResource(() => api('/api/host/materials?' + queryString({ scene: props.scene })))
const files = computed(() => (shared.data.value?.files || []).map(file => ({ title: file.name, value: file.name })))
const dirty = computed(() => Boolean(goal.value || deliverable.value || context.value || materials.value.length || accountBrowser.value))
watch(dirty, value => emit('dirty', value), { immediate: true })
const validQQ = computed(() => /^[1-9][0-9]*$/.test(props.operator))
const create = useAction()
async function submit() {
  const result = await create.run(() => api('/api/host/tasks/delegate?' + queryString({ scene: props.scene }), { method: 'POST',
    body: JSON.stringify({ requester: props.operator, goal: goal.value, deliverable: deliverable.value, context: context.value,
      account_browser: accountBrowser.value, materials: materials.value }) }))
  if (!result) return
  notify('任务已排队')
  emit('created', result)
}
function close() {
  if (dirty.value && !window.confirm('放弃没提交的任务？')) return
  emit('dirty', false)
  emit('close')
}
</script>

<template>
  <v-card title="新建任务">
    <v-card-text class="form">
      <v-textarea v-model="goal" label="要做什么" rows="3" auto-grow />
      <v-textarea v-model="deliverable" label="做完交付什么" rows="2" auto-grow hint="例如一份 PDF 报告、一段整理好的文字" persistent-hint />
      <v-textarea v-model="context" label="补充说明（可不填）" rows="2" auto-grow />
      <v-select v-model="materials" :items="files" multiple chips closable-chips label="给任务的资料（可不选）"
        hint="从本群的共享资料里选，任务里只能读不能改" persistent-hint :loading="shared.loading.value" />
      <ErrorNote v-if="shared.error.value" title="读取共享资料失败" :error="shared.error.value" />
      <v-checkbox v-model="accountBrowser" label="使用账号浏览器（需要主人本人发起）" hide-details />
      <p v-if="!validQQ" class="problem">先在上方填写你的 QQ</p>
      <ErrorNote v-if="create.error.value" title="没有创建成功" :error="create.error.value" />
      <p v-if="create.error.value && materials.length" class="muted">选了资料时，任务可能已经建好了，请先看看任务列表再决定要不要重新提交。</p>
    </v-card-text>
    <v-card-actions><v-spacer /><v-btn :disabled="create.busy.value" @click="close">取消</v-btn>
      <v-btn color="primary" :loading="create.busy.value" :disabled="!validQQ || !goal.trim() || !deliverable.trim()" @click="submit">开始</v-btn></v-card-actions>
  </v-card>
</template>

<style scoped>
.form{display:grid;gap:14px}
.problem{color:var(--error-text);margin:0}
</style>
