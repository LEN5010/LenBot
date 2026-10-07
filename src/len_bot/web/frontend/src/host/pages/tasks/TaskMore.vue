<script setup>
import { computed, ref } from 'vue'
import { tasksApi } from '../../api/tasks.js'
import { materialsApi } from '../../api/materials.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { notify } from '../../store.js'
import ErrorNote from '../../ui/ErrorNote.vue'
import AdvancedFields from '../../ui/AdvancedFields.vue'
import FormDialog from '../../ui/FormDialog.vue'
import ObjectList from '../../ui/ObjectList.vue'
import ObjectRow from '../../ui/ObjectRow.vue'
import SkillInspector from '../../components/SkillInspector.vue'
import TaskSpaceCard from '../../components/TaskSpaceCard.vue'
import { finished } from './taskLabels.js'

const props = defineProps({
  scene: { type: String, required: true }, task: { type: Object, required: true }, files: { type: Array, required: true },
  service: { type: Object, required: true }, operator: { type: String, required: true },
})
const emit = defineEmits(['changed'])
const stopped = computed(() => props.service.configured && finished(props.task.status) && props.task.container === null)

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
  <AdvancedFields label="更多" class="more">
    <div class="more-body">
      <div class="inline">
        <a v-if="stopped" :href="tasksApi.sessionUrl(scene, task.id)" target="_blank" rel="noopener">下载任务会话记录</a>
        <a :href="tasksApi.exportUrl(scene, task.id)">下载诊断包（不含文件）</a>
      </div>

      <div v-if="files.length && service.configured" class="block">
        <h3>存为本群共享资料</h3>
        <p class="muted small">以后新建任务时可以选这份资料。</p>
        <div class="keep">
          <v-select :model-value="keepFile" :items="files.filter(file => file.exists).map(file => ({ title: file.name, value: file.id }))" label="交付的文件" @update:model-value="chooseKeep" />
          <v-text-field v-model="keepName" label="保存为" />
          <v-btn variant="outlined" :disabled="!files.some(file => file.id === keepFile && file.exists) || !keepName.trim()" :loading="keep.busy.value || shared.loading.value" @click="keepShared">保存</v-btn>
        </div>
        <ErrorNote v-if="shared.error.value" title="读取共享资料失败" :error="shared.error.value" />
        <ErrorNote v-if="keep.error.value" title="没有保存成功" :error="keep.error.value" />
      </div>

      <TaskSpaceCard v-if="service.configured" :scene="scene" :task-id="task.id" :operator="operator" @changed="emit('changed')" />

      <div v-if="stopped" class="block">
        <div class="inline"><h3>任务自己写的技能</h3><v-btn size="small" variant="text" :loading="skills.loading.value" @click="skills.reload()">查看</v-btn></div>
        <ErrorNote v-if="skills.error.value" title="读取技能失败" :error="skills.error.value" />
        <p v-if="skills.data.value && !skills.data.value.items.length" class="muted small">没有</p>
        <ObjectList v-if="skills.data.value" divided>
          <ObjectRow v-for="item in skills.data.value.items" :key="item.name" :title="item.name" :subtitle="item.description">
            <template #actions><v-btn size="small" variant="text" @click="inspected = item">查看并采用</v-btn></template>
          </ObjectRow>
        </ObjectList>
      </div>
    </div>
  </AdvancedFields>
  <FormDialog v-model="inspecting" :title="inspected?.name || ''" size="lg" cancel-label="关闭">
    <SkillInspector v-if="inspected" :key="inspected.name" :scene="scene" source="task" :name="inspected.name" :task-id="task.id" @changed="skillMoved" />
  </FormDialog>
</template>

<style scoped>
.more :deep(.advanced-grid){display:block}
.more-body{display:grid;gap:var(--sp-4)}
.block{display:grid;gap:var(--sp-2)}
.block p{margin:0}
.keep{display:grid;grid-template-columns:1fr 1fr auto;gap:var(--sp-2);align-items:center}
@media(max-width:600px){.keep{grid-template-columns:1fr}}
</style>
