<script setup>
// Files of one skill, with moving it between task, group and shared, or deleting it.
import { computed, ref, watch } from 'vue'
import { api, queryString, sceneName } from '../../api.js'
import { useAction, useResource } from '../../composables/useResource.js'
import { confirm } from '../../composables/useConfirm.js'
import { notify } from '../store.js'
import ErrorNote from '../ui/ErrorNote.vue'
import CodeBlock from '../ui/CodeBlock.vue'
import LoadMore from '../ui/LoadMore.vue'
import DevOnly from '../ui/DevOnly.vue'

const props = defineProps({
  scene: { type: String, required: true }, source: { type: String, required: true },
  name: { type: String, required: true }, taskId: { type: Number, default: null },
})
const emit = defineEmits(['changed'])
const where = () => ({ scene: props.scene, source: props.source, name: props.name, ...(props.source === 'task' ? { task_id: String(props.taskId) } : {}) })
const listing = useResource(() => api('/api/host/skills/files?' + queryString(where())))
const path = ref(null), text = ref(''), next = ref(null)
const reading = useAction(), act = useAction()
async function read(file, more = false) {
  const page = await reading.run(() => api('/api/host/skills/text?' + queryString({ ...where(), path: file, offset: more ? String(next.value) : '0' })))
  if (!page) return
  path.value = file
  text.value = more ? text.value + page.content : page.content
  next.value = page.next_offset
}
watch(() => listing.data.value, value => { if (value?.files.some(file => file.path === 'SKILL.md')) read('SKILL.md') })

const labels = { builtin: '内置', plugin: '插件附带', shared: '共享', scene: '本群', task: '任务自己写的' }
const canMove = computed(() => ['task', 'scene'].includes(props.source))
const canDelete = computed(() => ['shared', 'scene'].includes(props.source))
async function move(target) {
  const place = target === 'scene' ? '本群' : '共享'
  if (!await confirm({ title: `把技能 ${props.name} 移到${place}？`, text: '原来位置上的这份技能会被移走。', confirmLabel: '移动' })) return
  const result = await act.run(() => api('/api/host/skills/move?' + queryString({ scene: props.scene }), { method: 'POST',
    body: JSON.stringify({ source: props.source, name: props.name, task_id: props.source === 'task' ? props.taskId : null, target }) }))
  if (result) { notify('已移动'); emit('changed', result) }
}
async function remove() {
  if (!await confirm({ title: `删除技能 ${props.name}？`, text: '技能文件会被删掉，不能恢复。', confirmLabel: '删除', danger: true })) return
  const result = await act.run(() => api(`/api/host/skills/${encodeURIComponent(props.source)}/${encodeURIComponent(props.name)}?` + queryString({ scene: props.scene }), { method: 'DELETE' }))
  if (result) { notify('已删除'); emit('changed', result) }
}
</script>

<template>
  <div class="inspector">
    <ErrorNote v-if="listing.error.value" title="读取技能失败" :error="listing.error.value" @retry="listing.reload()" />
    <v-progress-linear v-if="listing.loading.value && !listing.data.value" indeterminate color="primary" />
    <template v-if="listing.data.value">
      <div>
        <p>{{ listing.data.value.skill.description }}</p>
        <p class="muted small">{{ labels[source] }}<template v-if="listing.data.value.used_by_running.length"> · 正在被 {{ listing.data.value.used_by_running.map(sceneName).join('、') }} 使用</template></p>
      </div>
      <v-chip-group :model-value="path" mandatory selected-class="file-on" @update:model-value="file => file && read(file)">
        <v-chip v-for="file in listing.data.value.files" :key="file.path" :value="file.path" variant="outlined">{{ file.path }}</v-chip>
      </v-chip-group>
      <ErrorNote v-if="reading.error.value" title="读取文件失败" :error="reading.error.value" />
      <CodeBlock v-if="path" :text="text || '（空文件）'" />
      <LoadMore v-if="next !== null" label="继续读" :loading="reading.busy.value" @more="read(path, true)" />
      <div v-if="canMove || canDelete" class="inline">
        <v-btn v-if="source === 'task'" variant="tonal" :loading="act.busy.value" @click="move('scene')">移到本群</v-btn>
        <v-btn v-if="canMove" variant="tonal" :loading="act.busy.value" @click="move('shared')">{{ source === 'scene' ? '改为共享' : '移到共享' }}</v-btn>
        <v-btn v-if="canDelete" variant="text" color="error" :disabled="act.busy.value" @click="remove">删除</v-btn>
      </div>
      <ErrorNote v-if="act.error.value" title="操作没有成功" :error="act.error.value" />
      <DevOnly label="技能详情" :json="listing.data.value" />
    </template>
  </div>
</template>

<style scoped>
.inspector{display:grid;gap:var(--sp-3);min-width:0}
.inspector p{margin:0}
.file-on{background:var(--selected);border-color:var(--ink)}
</style>
