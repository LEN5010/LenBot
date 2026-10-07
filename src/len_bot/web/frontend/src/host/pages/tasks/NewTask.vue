<script setup>
import { computed, ref, watch } from 'vue'
import { tasksApi } from '../../api/tasks.js'
import { materialsApi } from '../../api/materials.js'
import { resourceLabel } from '../../resourceLabels.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { notify } from '../../store.js'
import { confirm } from '../../../composables/useConfirm.js'
import ErrorNote from '../../ui/ErrorNote.vue'
import FormDialog from '../../ui/FormDialog.vue'
import RowEditor from '../../ui/RowEditor.vue'

const props = defineProps({ scene: { type: String, required: true },
  initialResources: { type: Array, default: () => [] } })
const emit = defineEmits(['dirty', 'created', 'close'])
const goal = ref(''), deliverable = ref(''), context = ref(''), materials = ref([]), accountBrowser = ref(false)
const resources = ref(props.initialResources.map(item => ({ reference: { ...item.reference }, name: item.name })))
const shared = useResource(() => materialsApi.list(props.scene))
const files = computed(() => (shared.data.value?.files || []).map(file => ({ title: file.name, value: file.name })))
const dirty = computed(() => Boolean(goal.value || deliverable.value || context.value || materials.value.length || resources.value.length || accountBrowser.value))
const inputProblem = computed(() => {
  const names = [...materials.value, ...resources.value.map(item => item.name)]
  if (names.length > 16) return '每项任务最多选择 16 份资料。'
  if (names.some(name => !name.trim() || name.length > 240 || /[/\\\0\r\n]/.test(name))) return '输入文件名须为 1–240 字符，不包含目录分隔符或换行。'
  if (new Set(names).size !== names.length) return '资料文件名重复，请修改输入文件名或移除重复选择。'
  return ''
})
watch(dirty, value => emit('dirty', value), { immediate: true })
const create = useAction()
async function submit() {
  const result = await create.run(() => tasksApi.delegate(props.scene, { goal: goal.value, deliverable: deliverable.value, context: context.value,
      account_browser: accountBrowser.value, materials: materials.value, resources: resources.value }))
  if (!result) return
  notify('任务已排队')
  emit('created', result)
}
async function close() {
  if (dirty.value && !await confirm({ title: '放弃没提交的任务？', confirmLabel: '放弃', danger: true })) return
  emit('dirty', false)
  emit('close')
}
</script>

<template>
  <FormDialog :model-value="true" title="新建任务" size="md" :busy="create.busy.value" persistent @update:model-value="close">
    <v-textarea v-model="goal" label="要做什么" rows="3" auto-grow />
    <v-textarea v-model="deliverable" label="做完交付什么" rows="2" auto-grow placeholder="一份 PDF 报告、一段整理好的文字" />
    <v-textarea v-model="context" label="补充说明（可不填）" rows="2" auto-grow />
    <v-select v-model="materials" :items="files" multiple chips closable-chips label="给任务的资料（可不选）"
      :loading="shared.loading.value" />
    <ErrorNote v-if="shared.error.value" title="读取共享资料失败" :error="shared.error.value" />
    <template v-if="resources.length">
      <h3>从资源页选取的资料</h3>
      <RowEditor :items="resources" :make="() => null" add-label="">
        <template #default="{ item }">
          <v-text-field v-model="item.name" label="任务中的文件名" :hint="resourceLabel(item.reference)" persistent-hint />
        </template>
      </RowEditor>
    </template>
    <p v-if="inputProblem" class="problem">{{ inputProblem }}</p>
    <v-checkbox v-model="accountBrowser" label="使用账号浏览器（需要主人本人发起）" />
    <ErrorNote v-if="create.error.value" title="没有创建成功" :error="create.error.value" />
    <p v-if="create.error.value && (materials.length || resources.length)" class="muted small">选了资料时，任务可能已经建好了，请先看看任务列表再决定要不要重新提交。</p>
    <template #actions>
      <v-btn color="primary" :loading="create.busy.value" :disabled="!goal.trim() || !deliverable.trim() || Boolean(inputProblem)" @click="submit">开始</v-btn>
    </template>
  </FormDialog>
</template>
