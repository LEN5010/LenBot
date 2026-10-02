<script setup>
// Files of one skill, with moving it between task, group and shared, or deleting it.
import { computed, ref, watch } from 'vue'
import { api, queryString, sceneName } from '../../api.js'
import { useAction, useResource } from '../../composables/useResource.js'
import { notify } from '../store.js'
import ErrorNote from './ErrorNote.vue'
import DevOnly from './DevOnly.vue'

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
  if (!window.confirm(`把技能 ${props.name} 移到${target === 'scene' ? '本群' : '共享'}？原来的位置会移走。`)) return
  const result = await act.run(() => api('/api/host/skills/move?' + queryString({ scene: props.scene }), { method: 'POST',
    body: JSON.stringify({ source: props.source, name: props.name, task_id: props.source === 'task' ? props.taskId : null, target }) }))
  if (result) { notify('已移动'); emit('changed', result) }
}
async function remove() {
  if (!window.confirm(`删除技能 ${props.name}？`)) return
  const result = await act.run(() => api(`/api/host/skills/${encodeURIComponent(props.source)}/${encodeURIComponent(props.name)}?` + queryString({ scene: props.scene }), { method: 'DELETE' }))
  if (result) { notify('已删除'); emit('changed', result) }
}
</script>

<template>
  <div class="inspector">
    <ErrorNote v-if="listing.error.value" title="读取技能失败" :error="listing.error.value" />
    <template v-if="listing.data.value">
      <p><strong>{{ listing.data.value.skill.name }}</strong> · {{ labels[source] }}<br><span class="muted">{{ listing.data.value.skill.description }}</span></p>
      <p v-if="listing.data.value.used_by_running.length" class="muted">正在被 {{ listing.data.value.used_by_running.map(sceneName).join('、') }} 使用</p>
      <div class="files">
        <v-chip v-for="file in listing.data.value.files" :key="file.path" size="small" :variant="file.path === path ? 'flat' : 'outlined'"
          :color="file.path === path ? 'primary' : undefined" @click="read(file.path)">{{ file.path }}</v-chip>
      </div>
      <ErrorNote v-if="reading.error.value" title="读取文件失败" :error="reading.error.value" />
      <pre v-if="path" class="text">{{ text || '（空文件）' }}</pre>
      <v-btn v-if="next !== null" size="small" variant="text" :loading="reading.busy.value" @click="read(path, true)">继续读</v-btn>
      <div v-if="canMove || canDelete" class="actions">
        <v-btn v-if="source === 'task'" variant="outlined" :loading="act.busy.value" @click="move('scene')">移到本群</v-btn>
        <v-btn v-if="canMove" variant="outlined" :loading="act.busy.value" @click="move('shared')">{{ source === 'scene' ? '改为共享' : '移到共享' }}</v-btn>
        <v-btn v-if="canDelete" variant="text" color="error" :disabled="act.busy.value" @click="remove">删除</v-btn>
      </div>
      <ErrorNote v-if="act.error.value" title="操作没有成功" :error="act.error.value" />
      <DevOnly label="技能详情"><pre>{{ JSON.stringify(listing.data.value, null, 2) }}</pre></DevOnly>
    </template>
  </div>
</template>

<style scoped>
.inspector{display:grid;gap:10px;min-width:0}
.inspector p{margin:0}
.files{display:flex;gap:6px;flex-wrap:wrap}
.text{white-space:pre-wrap;overflow-wrap:anywhere;max-height:28rem;overflow:auto;font-size:13px;background:var(--code-bg);padding:12px;border-radius:8px;margin:0}
.actions{display:flex;gap:8px;flex-wrap:wrap}
</style>
