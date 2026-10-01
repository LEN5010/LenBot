<script setup>
// Background files the role can look things up in while chatting, one Markdown file each.
import { computed, ref, watch } from 'vue'
import { api, sceneName } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { notify, readPendingRestart } from '../../store.js'
import ErrorNote from '../../components/ErrorNote.vue'

const props = defineProps({ scene: { type: String, required: true } })
const emit = defineEmits(['dirty'])
const base = computed(() => `/api/host/scenes/${encodeURIComponent(props.scene)}/persona-knowledge`)
const listing = useResource(() => api(base.value))
const files = computed(() => listing.data.value?.files || [])
const shared = computed(() => (listing.data.value?.affected_scenes || []).filter(item => item !== props.scene))

// The open file: null, { creating: true }, or { filename, content } as saved.
const open = ref(null), name = ref(''), text = ref('')
const dirty = computed(() => open.value?.creating ? Boolean(name.value || text.value) : Boolean(open.value && text.value !== open.value.content))
watch(dirty, value => emit('dirty', value), { immediate: true })
const confirmDiscard = () => !dirty.value || window.confirm('这份资料还没保存，放弃修改？')

const reading = useAction()
async function pick(filename) {
  if (open.value?.filename === filename || !confirmDiscard()) return
  const result = await reading.run(() => api(`${base.value}/document?filename=${encodeURIComponent(filename)}&directory=${encodeURIComponent(listing.data.value.saved_path)}`))
  if (!result) return
  open.value = { filename, content: result.content }
  text.value = result.content
}
function create() {
  if (!confirmDiscard()) return
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
  if (!window.confirm(`删除资料 ${open.value.filename}？${others}`)) return
  if (await send('DELETE', {}, '已删除，重启后生效')) { open.value = null; text.value = '' }
}
</script>

<template>
  <ErrorNote v-if="listing.error.value" title="读取资料失败" :error="listing.error.value" />
  <div v-if="listing.data.value" class="knowledge">
    <section class="surface files">
      <div class="head"><h2>资料</h2><v-btn size="small" variant="tonal" @click="create">新建</v-btn></div>
      <p class="muted">世界观、人物关系这类背景。聊天时它需要会自己翻。</p>
      <p v-if="!files.length" class="muted">还没有资料。</p>
      <ul>
        <li v-for="file in files" :key="file.filename">
          <button type="button" :class="{ active: open?.filename === file.filename }" @click="pick(file.filename)">
            <strong>{{ file.filename.replace(/\.md$/, '') }}</strong>
            <span class="muted">{{ file.chars }} 字<template v-if="file.tags.length"> · {{ file.tags.join('、') }}</template></span>
          </button>
        </li>
      </ul>
      <ErrorNote v-if="reading.error.value" title="打不开这份资料" :error="reading.error.value" />
    </section>
    <form v-if="open" class="surface editor" @submit.prevent="save">
      <v-text-field v-if="open.creating" v-model="name" label="文件名" suffix=".md" hint="可以用 / 分文件夹，比如 人物/小明" persistent-hint />
      <h2 v-else>{{ open.filename.replace(/\.md$/, '') }}</h2>
      <v-textarea v-model="text" label="内容" rows="16" auto-grow hint="可以在开头写 ---、tags: [标签]、--- 三行给资料加标签" persistent-hint />
      <ErrorNote v-if="write.error.value" title="没有保存成功" :error="write.error.value" />
      <div class="actions">
        <v-btn type="submit" color="primary" :loading="write.busy.value" :disabled="!dirty || Boolean(problem)">保存</v-btn>
        <span v-if="problem" class="problem">{{ problem }}</span>
        <v-spacer />
        <v-btn v-if="!open.creating" variant="text" color="error" :disabled="write.busy.value" @click="remove">删除</v-btn>
      </div>
    </form>
    <section v-else class="surface empty"><p class="muted">选择一份资料，或者新建一份。</p></section>
  </div>
</template>

<style scoped>
.knowledge{display:grid;grid-template-columns:minmax(220px,1fr) minmax(0,2fr);gap:16px;align-items:start}
.files{display:grid;gap:8px}
.files p{margin:0}
.head{display:flex;justify-content:space-between;align-items:center}
ul{list-style:none;margin:0;padding:0;display:grid}
li button{display:grid;gap:2px;width:100%;text-align:left;padding:8px 10px;border:0;border-radius:8px;background:none;color:inherit;font:inherit;cursor:pointer}
li button:hover{background:var(--list-heading-bg)}
li button.active{background:var(--selected-bg)}
li .muted{font-size:12px}
.editor{display:grid;gap:16px}
.editor :deep(textarea){line-height:1.6}
.actions{display:flex;align-items:center;gap:12px}
.problem{color:var(--error-text)}
.empty{display:grid;place-items:center;min-height:160px}
@media(max-width:800px){.knowledge{grid-template-columns:1fr}}
</style>
