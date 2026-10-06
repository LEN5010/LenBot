<script setup>
import { computed, ref, watch } from 'vue'
import { useAction, useResource } from '../../composables/useResource.js'
import { tasksApi } from '../api/tasks.js'
import { taskStorageApi } from '../api/taskStorage.js'
import { continuationLabel, fileSize, rootLabel } from '../spaceLabels.js'
import { formatTime } from '../time.js'
import { confirm } from '../../composables/useConfirm.js'
import ErrorNote from '../ui/ErrorNote.vue'
import TaskCleanupDialog from './TaskCleanupDialog.vue'

const props = defineProps({ scene: { type: String, required: true }, taskId: { type: Number, required: true }, operator: { type: String, required: true }, version: { type: Number, default: 0 } })
const emit = defineEmits(['changed'])
const storage = useResource(() => tasksApi.storage(props.scene, props.taskId), { immediate: false })
watch(() => props.version, () => { if (storage.data.value) storage.reload() })
const operation = ref(null)
const cleaning = computed({ get: () => operation.value !== null, set: value => { if (!value) operation.value = null } })
function changed() { storage.reload(); emit('changed') }
const closeAction = useAction()
const terminal = computed(() => ['done', 'failed', 'cancelled'].includes(storage.data.value?.task.status))
const validIdentity = computed(() => /^[a-z][a-z0-9_-]*:[^:\s/\\]+$/.test(props.operator))
async function closeEnvironment() {
  if (!await confirm({ title: `关闭任务 #${props.taskId} 遗留的运行环境？`, text: '关闭容器和绑定的浏览器会话，工作文件和任务会话保留。', confirmLabel: '关闭' })) return
  await closeAction.run(() => taskStorageApi.close(props.scene, props.taskId, props.operator))
  changed()
}
</script>

<template>
  <div class="space-card">
    <div class="inline"><h3>任务空间</h3><v-btn size="small" variant="text" class="ml-auto" :loading="storage.loading.value" @click="storage.reload()">{{ storage.data.value ? '刷新用量' : '查看用量与清理' }}</v-btn></div>
    <ErrorNote v-if="storage.error.value" title="读取空间失败" :error="storage.error.value" />
    <template v-if="storage.data.value">
      <p>{{ continuationLabel[storage.data.value.continuation] }}</p>
      <p v-if="storage.data.value.hard_disk_quota" class="muted small">共用存储池上限 {{ fileSize(storage.data.value.hard_disk_quota.limit_bytes) }}，还能写入 {{ fileSize(storage.data.value.hard_disk_quota.available_bytes) }}。</p>
      <ul class="roots"><li v-for="root in storage.data.value.roots" :key="root.kind">
        {{ rootLabel[root.kind] }}：{{ root.exists ? `${root.usage.files} 个文件 · ${fileSize(root.usage.file_bytes)}（占用 ${fileSize(root.usage.allocated_bytes)}）` : '目录不存在' }}</li></ul>
      <p class="muted small">临时文件 {{ fileSize(storage.data.value.temporary.reduce((sum, entry) => sum + entry.file_bytes, 0)) }}。</p>
      <p v-if="storage.data.value.last_cleanup" class="muted small">最近清理：{{ formatTime(storage.data.value.last_cleanup.created) }} · {{ storage.data.value.last_cleanup.complete ? '完成' : '未完成' }}</p>
      <ErrorNote v-if="storage.data.value.last_cleanup?.error" title="上次清理出错" :error="storage.data.value.last_cleanup.error" />
      <div v-if="storage.data.value.cleanable" class="inline">
        <v-btn size="small" variant="outlined" @click="operation = 'temporary'">清理临时文件</v-btn>
        <v-btn size="small" variant="text" color="error" @click="operation = 'environment'">释放环境</v-btn>
      </div>
      <p v-else class="muted small">任务结束、浏览器关闭后可以清理。</p>
      <div v-if="terminal && (storage.data.value.task.container || storage.data.value.task.browser_active)" class="inline">
        <span class="muted small">容器{{ storage.data.value.task.container ? '仍在运行' : '已关闭' }}，账号浏览{{ storage.data.value.task.browser_active ? '仍在使用' : '已关闭' }}</span>
        <v-btn size="small" variant="outlined" :disabled="!validIdentity" :loading="closeAction.busy.value" @click="closeEnvironment">关闭遗留运行环境</v-btn>
      </div>
      <ErrorNote v-if="closeAction.error.value" title="关闭环境失败" :error="closeAction.error.value" />
    </template>
  </div>
  <TaskCleanupDialog v-if="cleaning" :scene="scene" :task-ids="[taskId]" :operator="operator" :operation="operation" @close="operation = null" @changed="changed" />
</template>

<style scoped>
.space-card{display:grid;gap:var(--sp-2)}
p{margin:0}
.roots{padding-left:var(--sp-5);margin:0}
</style>
