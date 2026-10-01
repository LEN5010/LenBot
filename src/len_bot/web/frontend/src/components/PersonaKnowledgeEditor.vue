<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../api.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'

const props = defineProps({ scene: { type: String, required: true } })
const emit = defineEmits(['dirty', 'changed'])
const snapshot = ref(null), document = ref(null), selected = ref('')
const creating = ref(false), filename = ref(''), draft = ref('')
const reading = ref(false), writing = ref(false), error = ref(''), notice = ref('')
const beginRead = useRequestGuard(), beginWrite = useRequestGuard()
const base = computed(() => `/api/host/scenes/${encodeURIComponent(props.scene)}/persona-knowledge`)
const options = computed(() => snapshot.value?.files.map(item => ({ title: `${item.filename} · ${item.chars} 字`, value: item.filename })) || [])
const dirty = computed(() => creating.value ? filename.value !== '' || draft.value !== ''
  : document.value !== null && draft.value !== document.value.content)
useUnsavedChanges(dirty)
watch(dirty, value => emit('dirty', value), { immediate: true })
onBeforeUnmount(() => emit('dirty', false))

function discard(question) { return !dirty.value || window.confirm(question) }

async function refresh() {
  if (reading.value || writing.value || !discard('放弃未保存的知识文件草稿，重读已保存目录？')) return
  const fresh = beginRead()
  reading.value = true
  error.value = ''
  try {
    const result = await api(base.value)
    if (!fresh()) return
    snapshot.value = result
    document.value = null
    selected.value = ''
    creating.value = false
    filename.value = ''
    draft.value = ''
    notice.value = ''
  } catch (problem) {
    if (fresh()) error.value = `读取保存目录失败：${problem.message} 上次目录与当前草稿保留。`
  } finally { if (fresh()) reading.value = false }
}

async function select(next) {
  if (reading.value || writing.value || next === selected.value || !discard('放弃当前知识文件未保存的草稿，打开另一文件？')) return
  const fresh = beginRead()
  reading.value = true
  error.value = ''
  notice.value = ''
  try {
    const result = await api(`${base.value}/document?filename=${encodeURIComponent(next)}&directory=${encodeURIComponent(snapshot.value.saved_path)}`)
    if (!fresh()) return
    document.value = result
    selected.value = next
    creating.value = false
    filename.value = next
    draft.value = result.content
  } catch (problem) {
    if (fresh()) error.value = `读取文件失败：${problem.message} 当前文件与草稿保留。`
  } finally { if (fresh()) reading.value = false }
}

function newFile() {
  if (reading.value || writing.value || !discard('放弃当前知识文件未保存的草稿，准备新文件？')) return
  document.value = null
  selected.value = ''
  creating.value = true
  filename.value = ''
  draft.value = ''
  error.value = ''
  notice.value = ''
}

async function mutate(remove = false) {
  if (reading.value || writing.value || (!creating.value && document.value === null)) return
  const name = creating.value ? filename.value : selected.value
  if (!name.endsWith('.md') || name.split('/').some(part => !part || part === '.' || part === '..') || name.includes('\\')) {
    error.value = '请填写 knowledge 内的相对 Markdown 路径，例如 setting/background.md。'
    return
  }
  if (remove && !window.confirm(`删除已保存知识文件 ${name}？共享角色的其他场景也会受影响；当前运行快照仍保留到重启。`)) return
  if (remove && !discard('删除会丢弃当前文件的未保存原文，继续？')) return
  const content = draft.value
  const method = remove ? 'DELETE' : creating.value ? 'POST' : 'PUT'
  const fresh = beginWrite()
  writing.value = true
  error.value = ''
  notice.value = ''
  try {
    const result = await api(`${base.value}/document`, { method, body: JSON.stringify({ directory: snapshot.value.saved_path, filename: name, ...(remove ? {} : { content }) }) })
    if (!fresh()) return
    snapshot.value = result
    if (remove) {
      document.value = null
      selected.value = ''
      draft.value = ''
      filename.value = ''
    } else {
      document.value = result.document
      selected.value = name
      filename.value = name
      creating.value = false
    }
    notice.value = `${remove ? '已删除保存文件' : '知识原文已保存'}；${result.restart_required ? '生产运行快照未变，重启后生效' : '知识与当前运行值一致'}。`
    emit('changed')
  } catch (problem) {
    if (fresh()) error.value = problem.status >= 400 && problem.status < 500
      ? `未接受本次${remove ? '删除' : '保存'}：${problem.message} 草稿保留。`
      : `结果未确认：${problem.message} 草稿保留，请重读目录核对 ${name}；不会自动重试。`
  } finally { if (fresh()) writing.value = false }
}
onMounted(refresh)
</script>

<template>
  <section class="surface persona-knowledge-editor" aria-labelledby="persona-knowledge-editor-title">
    <div class="section-heading"><div><h2 id="persona-knowledge-editor-title">角色知识文件</h2>
      <p class="muted">资料是角色包内容，不是群聊经历或长期记忆；只编辑一个实际 Markdown 文件，不改其余角色文件与样例。</p></div>
      <v-btn variant="outlined" :loading="reading" :disabled="writing" @click="refresh">重读已保存知识目录</v-btn></div>
    <v-alert v-if="error" type="error" variant="tonal" role="alert">{{ error }}</v-alert>
    <v-alert v-if="notice" type="success" variant="tonal" role="status">{{ notice }}</v-alert>
    <template v-if="snapshot">
      <p class="muted">保存包：{{ snapshot.saved_persona.name }}（{{ snapshot.saved_path }}）；运行包：{{ snapshot.running_persona.name }}（{{ snapshot.running_path }}）。</p>
      <p>共用此保存包的场景：{{ snapshot.affected_scenes.join('、') }}。</p>
      <v-chip :color="snapshot.restart_required ? 'warning' : 'info'" variant="tonal">{{ snapshot.restart_required ? '知识保存值与运行快照不同 · 待重启' : '知识与运行快照一致' }}</v-chip>
      <div class="file-actions"><v-select :model-value="selected" :items="options" label="选择已保存知识文件" :disabled="reading || writing" hide-details="auto" @update:model-value="select" />
        <v-btn variant="outlined" :disabled="reading || writing" @click="newFile">新建 Markdown</v-btn></div>
      <p v-if="!snapshot.files.length" class="muted">保存包尚无知识文件，可明确新建；不会用运行旧资料当成保存内容。</p>
      <form v-if="creating || document" @submit.prevent="mutate(false)">
        <v-text-field v-if="creating" v-model="filename" label="knowledge 内的相对 Markdown 路径" placeholder="setting/background.md" :disabled="reading || writing" hide-details="auto" />
        <p v-else>正在编辑：<strong>{{ selected }}</strong>；保存标签：{{ document.tags.join('、') || '无' }}。</p>
        <v-textarea v-model="draft" label="知识文件完整原文（含 YAML 标签头）" rows="12" auto-grow spellcheck="false" :disabled="reading || writing" hide-details="auto" class="source-editor" />
        <p v-if="dirty" class="muted" role="status">有未保存草稿；重读、换文件或换场景前会确认放弃。</p>
        <div class="file-actions"><v-btn type="submit" color="primary" :loading="writing" :disabled="reading || writing || (!creating && !dirty)">{{ creating ? '创建新文件（不覆盖）' : '保存此原文' }}</v-btn>
          <v-btn v-if="!creating" variant="outlined" color="warning" :disabled="reading || writing" @click="mutate(true)">删除已保存文件</v-btn></div>
        <details v-if="document"><summary>同名文件的运行快照 · 只读</summary>
          <pre v-if="document.running_content !== null">{{ document.running_content }}</pre><p v-else class="muted">当前运行包没有此同名文件。</p></details>
      </form>
    </template>
  </section>
</template>

<style scoped>
.section-heading,.file-actions{display:flex;justify-content:space-between;align-items:flex-start;gap:14px;flex-wrap:wrap}.file-actions{margin:16px 0}
.file-actions :deep(.v-input){min-width:220px;flex:1}h2{font-size:18px;margin:0}.section-heading p{margin:8px 0 16px}
form{display:grid;gap:14px;margin-top:16px}.source-editor :deep(textarea){font-family:ui-monospace,SFMono-Regular,Consolas,monospace}
p,pre{white-space:pre-wrap;overflow-wrap:anywhere}pre{max-height:400px;overflow:auto}summary,.persona-knowledge-editor :deep(.v-btn){min-height:44px}
</style>
