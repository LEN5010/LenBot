<script setup>
import { computed, ref, watch } from 'vue'
import { api, sceneName } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { useRequestGuard } from '../../../composables/useRequestGuard.js'
import { notify, readPendingRestart } from '../../store.js'
import { confirm } from '../../../composables/useConfirm.js'
import ResourceState from '../../ui/ResourceState.vue'
import MasterDetail from '../../ui/MasterDetail.vue'
import Panel from '../../ui/Panel.vue'
import ObjectList from '../../ui/ObjectList.vue'
import ObjectRow from '../../ui/ObjectRow.vue'
import ErrorNote from '../../ui/ErrorNote.vue'

const props = defineProps({ scene: { type: String, required: true } })
const emit = defineEmits(['dirty'])
const base = computed(() => `/api/host/scenes/${encodeURIComponent(props.scene)}/persona-knowledge`)
const listing = useResource(() => api(base.value))
const files = computed(() => listing.data.value?.files || [])
const shared = computed(() => (listing.data.value?.affected_scenes || []).filter(item => item !== props.scene))

const open = ref(null), name = ref(''), text = ref('')
const dirty = computed(() => open.value?.creating ? Boolean(name.value || text.value) : Boolean(open.value && text.value !== open.value.content))
watch(dirty, value => emit('dirty', value), { immediate: true })
const confirmDiscard = async () => !dirty.value || confirm({ title: '这份资料还没保存，放弃修改？', confirmLabel: '放弃', danger: true })

const reading = useAction()
const beginRead = useRequestGuard(() => props.scene)
async function pick(filename) {
  const current = beginRead()
  if (open.value?.filename === filename || !await confirmDiscard()) return
  if (!current()) return
  const result = await reading.run(() => api(`${base.value}/document?filename=${encodeURIComponent(filename)}&directory=${encodeURIComponent(listing.data.value.saved_path)}`), current)
  if (!result) return
  open.value = { filename, content: result.content }
  text.value = result.content
}
async function create() {
  beginRead()
  if (!await confirmDiscard()) return
  open.value = { creating: true }
  name.value = ''
  text.value = ''
}
const filename = computed(() => open.value?.creating ? (name.value.trim().endsWith('.md') ? name.value.trim() : `${name.value.trim()}.md`) : open.value?.filename)
const problem = computed(() => open.value?.creating && !name.value.trim() ? '先起个文件名'
  : open.value?.creating && files.value.some(file => file.filename === filename.value) ? '已经有同名的资料了' : '')

const write = useAction()
async function send(method, body, message) {
  const result = await write.run(() => api(`${base.value}/document`, { method,
    body: JSON.stringify({ directory: listing.data.value.saved_path, filename: filename.value, ...body }) }))
  if (!result) return null
  listing.data.value = result
  readPendingRestart()
  notify(message)
  return result
}
async function save() {
  const result = await send(open.value.creating ? 'POST' : 'PUT', { content: text.value }, '已保存，重启后生效')
  if (result) open.value = { filename: result.document.filename, content: result.document.content }
}
async function remove() {
  const others = shared.value.length ? `用这个角色的 ${shared.value.map(sceneName).join('、')} 也会少了它。` : ''
  if (!await confirm({ title: `删除资料 ${open.value.filename}？`, text: `重启后生效。${others}`, confirmLabel: '删除', danger: true })) return
  if (await send('DELETE', {}, '已删除，重启后生效')) { open.value = null; text.value = '' }
}
async function close() {
  if (!await confirmDiscard()) return
  open.value = null
  text.value = ''
}
</script>

<template>
  <ResourceState :resource="listing" error-title="读取资料失败">
    <MasterDetail :selected="Boolean(open)" :empty="!files.length" @back="close">
      <template #list>
        <Panel title="资料" flush>
          <template #actions><v-btn size="small" variant="outlined" @click="create">新建</v-btn></template>
          <div class="list">
            <p v-if="!files.length" class="muted small">还没有资料。</p>
            <ObjectList>
              <ObjectRow v-for="file in files" :key="file.filename" :title="file.filename.replace(/\.md$/, '')" clickable
                :active="open?.filename === file.filename" :subtitle="`${file.chars} 字${file.tags.length ? ` · ${file.tags.join('、')}` : ''}`"
                @click="pick(file.filename)" />
            </ObjectList>
            <ErrorNote v-if="reading.error.value" title="打不开这份资料" :error="reading.error.value" />
          </div>
        </Panel>
      </template>
      <template #placeholder>选择一份资料，或者新建一份。</template>
      <Panel v-if="open" tag="form" :title="open.creating ? '新资料' : open.filename.replace(/\.md$/, '')" @submit.prevent="save">
        <template v-if="!open.creating" #actions><v-btn variant="text" color="error" size="small" :disabled="write.busy.value" @click="remove">删除</v-btn></template>
        <v-text-field v-if="open.creating" v-model="name" label="文件名" suffix=".md" placeholder="人物/小明" />
        <v-textarea v-model="text" label="内容" rows="16" auto-grow />
        <ErrorNote v-if="write.error.value" title="没有保存成功" :error="write.error.value" />
        <template #footer>
          <span v-if="problem" class="problem small">{{ problem }}</span>
          <v-spacer />
          <v-btn type="submit" color="primary" :loading="write.busy.value" :disabled="!dirty || Boolean(problem)">保存</v-btn>
        </template>
      </Panel>
    </MasterDetail>
  </ResourceState>
</template>

<style scoped>
.list{display:grid;gap:var(--sp-1);padding:0 var(--sp-2) var(--sp-2)}
.list p{margin:0;padding:0 var(--sp-2)}
</style>
