<script setup>
import { computed, ref, watch } from 'vue'
import { useAction, useResource } from '../../composables/useResource.js'
import { taskStorageApi } from '../api/taskStorage.js'
import { tasksApi } from '../api/tasks.js'
import { continuationLabel, fileSize } from '../spaceLabels.js'
import ErrorNote from './ErrorNote.vue'
import ResourceBrowser from './ResourceBrowser.vue'

const props = defineProps({ scene: { type: String, required: true }, taskIds: { type: Array, required: true },
  operator: { type: String, required: true }, operation: { type: String, required: true } })
const emit = defineEmits(['close', 'changed'])
const selected = ref(props.taskIds[0]), result = ref(null), filesVersion = ref(0)
const storage = useResource(() => tasksApi.storage(props.scene, selected.value))
watch(selected, () => storage.reload())
const current = computed(() => storage.data.value?.task_id === selected.value ? storage.data.value : null)
const clean = useAction()
const validQQ = computed(() => /^[1-9][0-9]*$/.test(props.operator))
const title = computed(() => props.operation === 'temporary' ? '清理临时文件' : '释放整个任务环境')
async function run() {
  const value = await clean.run(() => taskStorageApi.cleanup(props.scene, { task_ids: props.taskIds, requester: props.operator, operation: props.operation }))
  if (!value) return
  result.value = value; filesVersion.value++; storage.reload(); emit('changed')
}
</script>

<template>
  <v-card :title="`${title} · ${taskIds.length} 个任务`">
    <v-card-text class="contents">
      <p>{{ operation === 'temporary' ? '删除控制文件、宿主日志、公共浏览临时目录和本次模型配置。工作文件、输入、Pi 会话、home 和全部产物保留，不改变原任务的续接状态。'
        : '删除工作区与运行目录，包括输入、Pi 会话、home 和未登记产物。任务不再续接；独立交付、共享资料与任务记录保留。' }}</p>
      <v-select v-if="taskIds.length > 1" v-model="selected" :items="taskIds.map(id => ({title:`任务 #${id}`,value:id}))" label="查看其中一个任务" density="compact" hide-details />
      <ErrorNote v-if="storage.error.value" title="读取任务空间失败" :error="storage.error.value" />
      <p v-if="current">#{{ selected }} {{ current.task.goal }} · {{ continuationLabel[current.continuation] }}
        <span v-if="!current.cleanable"> · 当前不执行文件清理</span></p>
      <template v-if="operation === 'temporary' && current">
        <ul class="temporary"><li v-for="entry in current.temporary" :key="entry.path"><code>{{ entry.path }}</code>
          <span>{{ entry.exists ? fileSize(entry.file_bytes) : '当前不存在' }}</span></li></ul>
        <p class="muted">容器 /tmp 随容器退出释放。上面列出的才是本次临时清理范围。</p>
      </template>
      <template v-if="operation === 'environment'">
        <p class="muted">下面直接查看 out 原件。需要保留的文件可先下载、登记交付或采用共享，也可切换到工作区其他位置。</p>
        <ResourceBrowser :key="`${selected}:${filesVersion}`" :scene="scene" :task-id="selected" :operator="operator"
          initial-path="out" :selectable="false" @changed="storage.reload(); emit('changed')" />
      </template>
      <ErrorNote v-if="clean.error.value" title="清理请求失败" :error="clean.error.value" />
      <ul v-if="result" class="results"><li v-for="item in result.items" :key="item.task_id">
        <strong>#{{ item.task_id }} · {{ item.status === 'complete' ? '清理完成' : '未完成' }}</strong>
        <p v-if="item.removal">删除 {{ item.removal.removed.length }} 个位置，{{ item.removal.absent.length }} 个位置原本不存在。</p>
        <ErrorNote v-if="item.error" title="本项结果" :error="item.error" />
      </li></ul>
      <p v-if="!validQQ" class="muted">填写你的 QQ 后执行。</p>
    </v-card-text>
    <v-card-actions><v-btn :disabled="clean.busy.value" @click="emit('close')">{{ result ? '关闭' : '取消' }}</v-btn><v-spacer />
      <v-btn v-if="!result" color="error" :loading="clean.busy.value" :disabled="!validQQ" @click="run">{{ title }}</v-btn></v-card-actions>
  </v-card>
</template>

<style scoped>
.contents{display:grid;gap:14px}p{margin:0}.temporary,.results{padding:0;list-style:none;display:grid;gap:8px}.temporary li{display:flex;justify-content:space-between;gap:12px}.temporary code{overflow-wrap:anywhere}.temporary span{white-space:nowrap}.results li{border-top:1px solid var(--line);padding-top:8px}
</style>
