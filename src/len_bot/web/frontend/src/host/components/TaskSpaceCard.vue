<script setup>
import { computed, ref, watch } from 'vue'
import { useAction, useResource } from '../../composables/useResource.js'
import { tasksApi } from '../api/tasks.js'
import { taskStorageApi } from '../api/taskStorage.js'
import { continuationLabel, fileSize, rootLabel } from '../spaceLabels.js'
import { formatTime } from '../time.js'
import ErrorNote from './ErrorNote.vue'
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
const validQQ = computed(() => /^[1-9][0-9]*$/.test(props.operator))
async function closeEnvironment() {
  if (!window.confirm('关闭此任务遗留的容器和已绑定浏览器会话？工作文件与 Pi 会话保留。')) return
  await closeAction.run(() => taskStorageApi.close(props.scene, props.taskId, props.operator))
  changed()
}
</script>

<template>
  <section class="space-card">
    <div class="heading"><h3>任务空间</h3><v-btn size="small" variant="text" :loading="storage.loading.value" @click="storage.reload()">{{ storage.data.value ? '刷新用量' : '查看用量与清理' }}</v-btn></div>
    <ErrorNote v-if="storage.error.value" title="读取空间失败" :error="storage.error.value" />
    <template v-if="storage.data.value">
      <p>{{ continuationLabel[storage.data.value.continuation] }}</p>
      <ul><li v-for="root in storage.data.value.roots" :key="root.kind">
        {{ rootLabel[root.kind] }}：{{ root.exists ? `${root.usage.files} 个文件 · 内容 ${fileSize(root.usage.file_bytes)} · 磁盘分配 ${fileSize(root.usage.allocated_bytes)}` : '目录不存在' }}</li></ul>
      <p class="muted">临时文件：{{ fileSize(storage.data.value.temporary.reduce((sum, entry) => sum + entry.file_bytes, 0)) }}。Pi 会话、工作文件和输入不属于临时清理。</p>
      <p v-if="storage.data.value.last_cleanup" class="muted">最近清理：{{ formatTime(storage.data.value.last_cleanup.created) }} · {{ storage.data.value.last_cleanup.complete ? '完成' : '未完成' }}</p>
      <ErrorNote v-if="storage.data.value.last_cleanup?.error" title="上次清理错误" :error="storage.data.value.last_cleanup.error" />
      <div class="actions" v-if="storage.data.value.cleanable">
        <v-btn size="small" variant="outlined" @click="operation = 'temporary'">清理临时文件</v-btn>
        <v-btn size="small" variant="outlined" color="error" @click="operation = 'environment'">释放环境</v-btn>
      </div>
      <p v-else class="muted">任务执行与浏览器关闭后可清理。</p>
      <template v-if="terminal && (storage.data.value.task.container || storage.data.value.task.browser_active)">
        <p class="muted">容器：{{ storage.data.value.task.container || '已关闭' }}；账号浏览：{{ storage.data.value.task.browser_active ? '仍持有会话' : '已关闭' }}</p>
        <v-btn size="small" variant="outlined" :disabled="!validQQ" :loading="closeAction.busy.value" @click="closeEnvironment">关闭遗留运行环境</v-btn>
        <RouterLink :to="{name:'host-capabilities',query:{tab:'tasks'}}">查看专用浏览器与会话</RouterLink>
      </template>
      <ErrorNote v-if="closeAction.error.value" title="关闭环境失败" :error="closeAction.error.value" />
    </template>
  </section>
  <v-dialog v-model="cleaning" max-width="1000" scrollable persistent>
    <TaskCleanupDialog v-if="cleaning" :scene="scene" :task-ids="[taskId]" :operator="operator" :operation="operation" @close="operation = null" @changed="changed" />
  </v-dialog>
</template>

<style scoped>
.space-card{display:grid;gap:10px}.heading,.actions{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.heading{justify-content:space-between}h3,p{margin:0}ul{padding-left:20px;margin:0}
</style>
