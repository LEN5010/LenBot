<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../api.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'

const props = defineProps({ scene: { type: String, required: true } })
const emit = defineEmits(['dirty', 'changed'])
const snapshot = ref(null), draft = ref(''), uploadName = ref(''), upload = ref(null), input = ref(null)
const busy = ref(false), error = ref(''), notice = ref('')
const preview = ref(null), previewError = ref(''), previewLoading = ref(false)
const base = computed(() => `/api/host/scenes/${encodeURIComponent(props.scene)}/persona-sticker-files`)
const dirtyIndex = computed(() => snapshot.value !== null && draft.value !== (snapshot.value.index_content ?? ''))
const dirty = computed(() => dirtyIndex.value || uploadName.value !== '' || upload.value !== null)
useUnsavedChanges(dirty)
watch(dirty, value => emit('dirty', value), { immediate: true })
const beginOperation = useRequestGuard(), beginPreview = useRequestGuard()

function clearPreview() {
  if (preview.value) URL.revokeObjectURL(preview.value.url)
  preview.value = null
}
onBeforeUnmount(() => { clearPreview(); emit('dirty', false) })

async function refresh() {
  if (busy.value || (dirtyIndex.value && !window.confirm('放弃未保存的表情索引原文，重读已保存索引与原件清单？上传选择仍保留。'))) return
  const fresh = beginOperation()
  busy.value = true
  error.value = ''
  try {
    const result = await api(base.value)
    if (!fresh()) return
    snapshot.value = result
    draft.value = result.index_content ?? ''
    beginPreview()
    previewLoading.value = false
    previewError.value = ''
    clearPreview()
    notice.value = ''
  } catch (problem) { if (fresh()) error.value = `读取失败：${problem.message} 当前索引草稿与上传选择保留。` }
  finally { if (fresh()) busy.value = false }
}

async function saveIndex() {
  if (busy.value || !snapshot.value) return
  const content = draft.value, fresh = beginOperation()
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    const result = await api(`${base.value}/index`, { method: 'PUT', body: JSON.stringify({ content, directory: snapshot.value.saved_path }) })
    if (!fresh()) return
    snapshot.value = result
    draft.value = result.index_content
    notice.value = `表情索引原文已保存；${result.restart_required ? '运行表情快照未变，重启后生效' : '与当前运行表情一致'}。`
    emit('changed')
  } catch (problem) {
    if (fresh()) error.value = problem.status >= 400 && problem.status < 500
      ? `未保存：${problem.message} 索引原文保留。`
      : `保存结果未确认：${problem.message} 请重读原文件核对，草稿保留，不自动重试。`
  } finally { if (fresh()) busy.value = false }
}

async function uploadImage() {
  if (busy.value || !snapshot.value) return
  error.value = ''
  if (!upload.value || !uploadName.value) { error.value = '请选择实际图片并填写 stickers 内的新相对文件名。'; return }
  if (upload.value.size > 10_000_000) { error.value = '原件不能超过 10,000,000 字节。'; return }
  const form = new FormData()
  form.append('name', uploadName.value)
  form.append('file', upload.value)
  form.append('directory', snapshot.value.saved_path)
  const fresh = beginOperation()
  busy.value = true
  notice.value = ''
  try {
    const result = await api(`${base.value}/image`, { method: 'POST', body: form })
    if (!fresh()) return
    snapshot.value = { ...snapshot.value, files: [...snapshot.value.files, { file: result.file, bytes: result.bytes }] }
    notice.value = `${result.file}（${result.bytes} 字节，${result.width}×${result.height}）：${result.notice}`
    upload.value = null
    uploadName.value = ''
    input.value.value = ''
    emit('changed')
  } catch (problem) {
    if (fresh()) error.value = problem.status >= 400 && problem.status < 500
      ? `上传未被接受：${problem.message} 文件选择与索引草稿保留。`
      : `上传结果未确认：${problem.message} 请重读原件清单核对，不自动重传或覆盖。`
  } finally { if (fresh()) busy.value = false }
}

async function remove(file) {
  if (busy.value || !window.confirm(`仅删除已保存清单未引用的原件 ${file}？不会根据未保存索引草稿判断引用。`)) return
  const fresh = beginOperation()
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    const result = await api(`${base.value}/image`, { method: 'DELETE', body: JSON.stringify({ file, directory: snapshot.value.saved_path }) })
    if (!fresh()) return
    snapshot.value = { ...snapshot.value, files: snapshot.value.files.filter(item => item.file !== result.file) }
    beginPreview()
    previewLoading.value = false
    if (preview.value?.file === result.file) clearPreview()
    notice.value = result.notice
    emit('changed')
  } catch (problem) {
    if (fresh()) error.value = problem.status >= 400 && problem.status < 500
      ? `未删除：${problem.message}` : `删除结果未确认：${problem.message} 请重读清单核对，不自动重试。`
  } finally { if (fresh()) busy.value = false }
}

async function viewImage(file) {
  const fresh = beginPreview()
  previewLoading.value = true
  previewError.value = ''
  try {
    const blob = await api(`${base.value}/image?file=${encodeURIComponent(file)}&directory=${encodeURIComponent(snapshot.value.saved_path)}`, {}, 'blob')
    if (!fresh()) return
    clearPreview()
    preview.value = { file, url: URL.createObjectURL(blob) }
  } catch (problem) { if (fresh()) previewError.value = `${file} 原件读取失败：${problem.message}` }
  finally { if (fresh()) previewLoading.value = false }
}
onMounted(refresh)
</script>

<template>
  <section class="surface persona-sticker-editor" aria-labelledby="persona-sticker-editor-title">
    <div class="section-heading"><div><h2 id="persona-sticker-editor-title">已保存表情清单与原件</h2>
      <p class="muted">编辑 index.yaml 与上传原件分别执行。原件上传不注册索引，也不向平台发送；运行表情不热替换。</p></div>
      <v-btn variant="outlined" :loading="busy" :disabled="busy" @click="refresh">重读保存索引与原件</v-btn></div>
    <v-alert v-if="error" type="error" variant="tonal" role="alert">{{ error }}</v-alert>
    <v-alert v-if="notice" type="success" variant="tonal" role="status">{{ notice }}</v-alert>
    <template v-if="snapshot">
      <p class="muted">保存包：{{ snapshot.saved_path }}；运行包：{{ snapshot.running_path }}。影响场景：{{ snapshot.affected_scenes.join('、') }}。</p>
      <p v-if="snapshot.restart_required === null" class="muted">当前显示原文件与运行条目，尚未比较全部图片字节；保存索引时才核对其实际图片和运行差异。</p>
      <v-alert v-if="snapshot.index_content === null" type="info" variant="tonal">尚无 index.yaml。请明确填写并保存索引；空列表 [] 可用于初始化，不会自动写入。</v-alert>
      <form @submit.prevent="saveIndex">
        <v-textarea v-model="draft" label="stickers/index.yaml 完整原文" placeholder="[]" rows="10" auto-grow spellcheck="false" :disabled="busy" hide-details="auto" class="source-editor" />
        <p class="muted">每项包含 file、description、emotions、tags。保存前校验清单中的所有真实图片及共用场景依赖；不自动挑图、改描述或转换格式。</p>
        <v-btn type="submit" color="primary" :disabled="busy || (!dirtyIndex && snapshot.index_content !== null)" :loading="busy">保存此索引原文</v-btn>
      </form>
      <form class="upload-form" @submit.prevent="uploadImage">
        <h3>上传新的实际图片原件</h3>
        <v-text-field v-model="uploadName" label="stickers 内相对文件名（不覆盖已有文件）" placeholder="happy/wave.png" :disabled="busy" hide-details="auto" />
        <label class="file-field">PNG/JPEG/GIF/WebP · 最多 10,000,000 字节<input ref="input" type="file" accept="image/png,image/jpeg,image/gif,image/webp" :disabled="busy" @change="upload=$event.target.files[0] ?? null" /></label>
        <v-btn type="submit" variant="outlined" :disabled="busy || snapshot.index_content === null" :loading="busy">只上传原件，不改索引</v-btn>
      </form>
      <h3>实际保存原件文件（包括未索引项）</h3>
      <p v-if="!snapshot.files.length" class="muted">当前目录没有索引以外的常规文件。</p>
      <ul class="asset-list"><li v-for="item in snapshot.files" :key="item.file"><span>{{ item.file }} · {{ item.bytes }} 字节</span>
        <v-btn variant="text" :disabled="busy || previewLoading" @click="viewImage(item.file)">读取图片预览</v-btn>
        <v-btn variant="outlined" color="warning" :disabled="busy" @click="remove(item.file)">删除未引用原件</v-btn></li></ul>
      <v-alert v-if="previewError" type="error" variant="tonal" role="alert">{{ previewError }}</v-alert>
      <figure v-if="preview"><img :src="preview.url" :alt="`已保存原件 ${preview.file}`" /><figcaption>{{ preview.file }} · 已保存包原字节，不是运行内存图或已发送平台图片。</figcaption></figure>
      <details><summary>当前运行条目字段投影 · 只读（不是原索引文件）</summary><pre>{{ JSON.stringify(snapshot.running_entries,null,2) }}</pre></details>
    </template>
  </section>
</template>

<style scoped>
.section-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:14px;flex-wrap:wrap}h2{font-size:18px;margin:0}h3{font-size:16px;margin:12px 0}
form{display:grid;gap:14px;margin:16px 0}.source-editor :deep(textarea){font-family:ui-monospace,SFMono-Regular,Consolas,monospace}.file-field{display:grid;gap:8px}.file-field input{max-width:100%;font:inherit}
.asset-list{padding:0;list-style:none}.asset-list li{display:flex;gap:10px;align-items:center;flex-wrap:wrap;border-top:1px solid var(--line);padding:12px 0}.asset-list span{flex:1;min-width:160px}
p,pre,span,figcaption{white-space:pre-wrap;overflow-wrap:anywhere}pre{max-height:400px;overflow:auto}figure{margin:16px 0}figure img{max-height:320px;max-width:100%;object-fit:contain}
summary,.persona-sticker-editor :deep(.v-btn){min-height:44px}
</style>
