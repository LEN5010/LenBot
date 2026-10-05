<script setup>
import { computed, ref, watch } from 'vue'
import { useAction, useResource } from '../../composables/useResource.js'
import { taskStorageApi } from '../api/taskStorage.js'
import { tasksApi } from '../api/tasks.js'
import { continuationLabel, fileSize } from '../spaceLabels.js'
import ErrorNote from '../ui/ErrorNote.vue'
import FormDialog from '../ui/FormDialog.vue'
import ObjectList from '../ui/ObjectList.vue'
import ObjectRow from '../ui/ObjectRow.vue'
import StatusBadge from '../ui/StatusBadge.vue'
import ResourceBrowser from './ResourceBrowser.vue'

const props = defineProps({ scene: { type: String, required: true }, taskIds: { type: Array, required: true },
  operator: { type: String, required: true }, operation: { type: String, required: true } })
const emit = defineEmits(['close', 'changed'])
const selected = ref(props.taskIds[0]), result = ref(null), filesVersion = ref(0)
const storage = useResource(() => tasksApi.storage(props.scene, selected.value))
watch(selected, () => storage.reload())
const current = computed(() => storage.data.value?.task_id === selected.value ? storage.data.value : null)
const clean = useAction()
const validIdentity = computed(() => /^[a-z][a-z0-9_-]*:[^:\s/\\]+$/.test(props.operator))
const title = computed(() => props.operation === 'temporary' ? '清理临时文件' : '释放整个任务环境')
async function run() {
  const value = await clean.run(() => taskStorageApi.cleanup(props.scene, { task_ids: props.taskIds, requester: props.operator, operation: props.operation }))
  if (!value) return
  result.value = value; filesVersion.value++; storage.reload(); emit('changed')
}
</script>

<template>
  <FormDialog :model-value="true" :title="`${title} · ${taskIds.length} 个任务`" size="lg" :busy="clean.busy.value" :cancel-label="result ? '关闭' : '取消'"
    persistent @update:model-value="emit('close')">
    <p>{{ operation === 'temporary' ? '删除控制文件、日志、公共浏览器临时目录和本次模型配置。工作文件、输入、任务会话和产物都保留，任务仍可以继续。'
      : '删除工作区和运行目录，包括输入、任务会话和没登记的产物，任务之后不能再继续。登记过的交付文件、共享资料和任务记录保留。' }}</p>
    <v-select v-if="taskIds.length > 1" v-model="selected" :items="taskIds.map(id => ({ title: `任务 #${id}`, value: id }))" label="查看其中一个任务" />
    <ErrorNote v-if="storage.error.value" title="读取任务空间失败" :error="storage.error.value" />
    <p v-if="current">#{{ selected }} {{ current.task.goal }} · {{ continuationLabel[current.continuation] }}<span v-if="!current.cleanable"> · 现在还不能清理</span></p>
    <ObjectList v-if="operation === 'temporary' && current" divided>
      <ObjectRow v-for="entry in current.temporary" :key="entry.path" :title="entry.path">
        <template #meta>{{ entry.exists ? fileSize(entry.file_bytes) : '不存在' }}</template>
      </ObjectRow>
    </ObjectList>
    <template v-if="operation === 'environment'">
      <p class="muted small">下面是任务的 out 目录。需要保留的文件可以先下载、登记交付或存为共享资料。</p>
      <ResourceBrowser :key="`${selected}:${filesVersion}`" :scene="scene" :task-id="selected" :operator="operator"
        initial-path="out" :selectable="false" @changed="storage.reload(); emit('changed')" />
    </template>
    <ErrorNote v-if="clean.error.value" title="清理请求失败" :error="clean.error.value" />
    <ObjectList v-if="result" divided>
      <ObjectRow v-for="item in result.items" :key="item.task_id" :title="`任务 #${item.task_id}`"
        :subtitle="item.removal ? `删除了 ${item.removal.removed.length} 个位置，${item.removal.absent.length} 个原本就不存在` : ''">
        <ErrorNote v-if="item.error" title="这一项没有完成" :error="item.error" />
        <template #meta><StatusBadge :text="item.status === 'complete' ? '清理完成' : '未完成'" :tone="item.status === 'complete' ? 'success' : 'error'" /></template>
      </ObjectRow>
    </ObjectList>
    <p v-if="!validIdentity" class="problem">先在页面上方填写你的 平台账号</p>
    <template #actions>
      <v-btn v-if="!result" color="error" :loading="clean.busy.value" :disabled="!validIdentity" @click="run">{{ title }}</v-btn>
    </template>
  </FormDialog>
</template>
