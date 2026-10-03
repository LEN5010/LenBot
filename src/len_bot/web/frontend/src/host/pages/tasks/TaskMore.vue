<script setup>
import { computed, ref } from 'vue'
import { tasksApi } from '../../api/tasks.js'
import { materialsApi } from '../../api/materials.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { notify } from '../../store.js'
import ErrorNote from '../../components/ErrorNote.vue'
import SkillInspector from '../../components/SkillInspector.vue'
import TaskSpaceCard from '../../components/TaskSpaceCard.vue'
import { finished } from './taskLabels.js'

const props = defineProps({
  scene: { type: String, required: true }, task: { type: Object, required: true }, files: { type: Array, required: true },
  service: { type: Object, required: true }, operator: { type: String, required: true },
})
const emit = defineEmits(['changed'])
const stopped = computed(() => props.service.configured && finished(props.task.status) && props.task.container === null)

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

const skills = useResource(() => tasksApi.skills(props.scene, props.task.id), { immediate: false })
const inspected = ref(null)
const inspecting = computed({ get: () => inspected.value !== null, set: value => { if (!value) inspected.value = null } })
function skillMoved() { inspected.value = null; skills.reload() }

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

        <TaskSpaceCard v-if="service.configured" :scene="scene" :task-id="task.id" :operator="operator" @changed="emit('changed')" />

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
