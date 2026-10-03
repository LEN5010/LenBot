<script setup>
import { computed, ref } from 'vue'
import { tasksApi } from '../../api/tasks.js'
import { materialsApi } from '../../api/materials.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { notify } from '../../store.js'
import ErrorNote from '../../components/ErrorNote.vue'
import DevOnly from '../../components/DevOnly.vue'
import SkillInspector from '../../components/SkillInspector.vue'
import { finished } from './taskLabels.js'

const props = defineProps({
  scene: { type: String, required: true }, task: { type: Object, required: true }, files: { type: Array, required: true },
  service: { type: Object, required: true }, operator: { type: String, required: true },
})
const emit = defineEmits(['changed'])
const stopped = computed(() => props.service.configured && finished(props.task.status) && props.task.container === null)
const validQQ = computed(() => /^[1-9][0-9]*$/.test(props.operator))

// Keep a delivered file in this group's shared materials, so later tasks can be given it.
const shared = useResource(() => materialsApi.list(props.scene), { immediate: false })
const keepFile = ref(null), keepName = ref('')
const keep = useAction()
async function keepShared() {
  const materials = shared.data.value ?? await shared.reload()
  if (!materials) return
  const result = await keep.run(() => materialsApi.adopt(props.scene, {
    task_id: props.task.id, file_id: keepFile.value, name: keepName.value.trim(), directory: materials.directory, confirmed: true }))
  if (!result) return
  notify(`已存为共享资料 ${result.name}`)
  keepFile.value = null
  keepName.value = ''
  shared.reload()
}
function chooseKeep(id) {
  keepFile.value = id
  keepName.value = props.files.find(file => file.id === id)?.name ?? ''
}

const storage = useResource(() => tasksApi.storage(props.scene, props.task.id), { immediate: false })
const rootLabel = { workspace: '工作目录', runtime: '运行目录', deliveries: '交付文件' }
const size = bytes => bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${(bytes / 1024).toFixed(1)} KB`

const skills = useResource(() => tasksApi.skills(props.scene, props.task.id), { immediate: false })
const inspected = ref(null)
const inspecting = computed({ get: () => inspected.value !== null, set: value => { if (!value) inspected.value = null } })
function skillMoved() { inspected.value = null; skills.reload() }

// Discarding removes the workspace and the native session; delivered files and records stay.
const discard = useAction()
const canDiscard = computed(() => stopped.value && !props.task.browser_active && !props.task.workspace_discard_requested)
async function discardEnvironment() {
  if (!window.confirm('清理这个任务的工作目录和会话？清理后不能再接着做，交付的文件会保留。')) return
  const result = await discard.run(async () => {
    const usage = await tasksApi.storage(props.scene, props.task.id)
    const path = kind => usage.roots.find(root => root.kind === kind).path
    return tasksApi.discard(props.scene, props.task.id, {
      requester: props.operator, workspace: path('workspace'), runtime: path('runtime'), confirmed: true })
  })
  if (!result) return
  notify(result.removal.removed.length || result.removal.absent.length ? '已清理' : result.notice)
  emit('changed')
}
</script>

<template>
  <v-expansion-panels variant="accordion" class="more">
    <v-expansion-panel title="更多" elevation="0">
      <v-expansion-panel-text class="more-body">
        <div class="links">
          <a v-if="stopped" :href="tasksApi.sessionUrl(scene, task.id)" target="_blank" rel="noopener">下载任务会话记录</a>
          <a :href="tasksApi.exportUrl(scene, task.id)">下载诊断包（不含文件）</a>
        </div>

        <div v-if="files.length && service.configured" class="block">
          <h3>存为本群共享资料</h3>
          <p class="muted">以后新建任务时可以选这份资料。</p>
          <div class="row">
            <v-select :model-value="keepFile" :items="files.filter(file => file.exists).map(file => ({ title: file.name, value: file.id }))" label="交付的文件" density="compact" hide-details @update:model-value="chooseKeep" />
            <v-text-field v-model="keepName" label="保存为" density="compact" hide-details />
            <v-btn variant="outlined" :disabled="!files.some(file => file.id === keepFile && file.exists) || !keepName.trim()" :loading="keep.busy.value || shared.loading.value" @click="keepShared">保存</v-btn>
          </div>
          <ErrorNote v-if="shared.error.value" title="读取共享资料失败" :error="shared.error.value" />
          <ErrorNote v-if="keep.error.value" title="没有保存成功" :error="keep.error.value" />
        </div>

        <div v-if="service.configured" class="block">
          <h3>占用空间 <v-btn size="small" variant="text" :loading="storage.loading.value" @click="storage.reload()">查看</v-btn></h3>
          <ErrorNote v-if="storage.error.value" title="读取占用空间失败" :error="storage.error.value" />
          <ul v-if="storage.data.value" class="plain">
            <li v-for="root in storage.data.value.roots" :key="root.kind">{{ rootLabel[root.kind] || root.kind }}：{{ root.exists ? size(root.usage.file_bytes) : '已清理' }}
              <DevOnly><span class="muted"> {{ root.path }}</span></DevOnly></li>
          </ul>
        </div>

        <div v-if="stopped" class="block">
          <h3>任务自己写的技能 <v-btn size="small" variant="text" :loading="skills.loading.value" @click="skills.reload()">查看</v-btn></h3>
          <ErrorNote v-if="skills.error.value" title="读取技能失败" :error="skills.error.value" />
          <p v-if="skills.data.value && !skills.data.value.items.length" class="muted">没有</p>
          <ul v-if="skills.data.value" class="plain">
            <li v-for="item in skills.data.value.items" :key="item.name">
              <strong>{{ item.name }}</strong> <span class="muted">{{ item.description }}</span>
              <v-btn size="small" variant="text" @click="inspected = item">查看并采用</v-btn></li>
          </ul>
        </div>

        <div v-if="canDiscard" class="block">
          <h3>清理任务环境</h3>
          <p class="muted">删除工作目录和会话，释放空间。之后不能再接着做，交付的文件和记录会保留。</p>
          <v-btn variant="outlined" color="error" :disabled="!validQQ" :loading="discard.busy.value" @click="discardEnvironment">清理</v-btn>
          <p v-if="!validQQ" class="muted">填写上方你的 QQ 后才能清理。</p>
          <ErrorNote v-if="discard.error.value" title="没有清理成功" :error="discard.error.value" />
        </div>
        <p v-else-if="task.workspace_discard_requested" class="muted">任务环境已清理。</p>
      </v-expansion-panel-text>
    </v-expansion-panel>
  </v-expansion-panels>
  <v-dialog v-model="inspecting" max-width="900" scrollable>
    <v-card v-if="inspected" :title="inspected.name">
      <v-card-text><SkillInspector :key="inspected.name" :scene="scene" source="task" :name="inspected.name" :task-id="task.id" @changed="skillMoved" /></v-card-text>
      <v-card-actions><v-spacer /><v-btn @click="inspected = null">关闭</v-btn></v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.more{border:1px solid var(--line);border-radius:10px}
.more-body :deep(.v-expansion-panel-text__wrapper){display:grid;gap:16px}
.links{display:flex;gap:16px;flex-wrap:wrap}
.block{display:grid;gap:6px}
.block h3{font-size:15px;margin:0;display:flex;align-items:center;gap:4px}
.block p{margin:0}
.row{display:grid;grid-template-columns:1fr 1fr auto;gap:8px;align-items:center}
.plain{margin:0;padding-left:18px}
@media(max-width:600px){.row{grid-template-columns:1fr}}
</style>
