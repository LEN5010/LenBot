<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, sceneName } from '../api.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'

const props = defineProps({ scene: { type: String, required: true } })
const emit = defineEmits(['dirty', 'changed'])
const snapshot = ref(null), file = ref(null), input = ref(null)
const directory = ref('')
const loading = ref(false), working = ref(false)
const readError = ref(''), writeError = ref(''), notice = ref('')
const images = ref({ saved: '', running: '' }), imageErrors = ref({ saved: '', running: '' })
const selectedImage = ref('')
const dirty = computed(() => file.value !== null || working.value)
useUnsavedChanges(dirty)
watch(dirty, value => emit('dirty', value), { immediate: true })
const beginRead = useRequestGuard(() => props.scene)
const beginWrite = useRequestGuard(() => props.scene)
const endpoint = () => `/api/host/scenes/${encodeURIComponent(props.scene)}/persona-avatar`

function releasePreviews() {
  for (const value of Object.values(images.value)) if (value) URL.revokeObjectURL(value)
  images.value = { saved: '', running: '' }
  imageErrors.value = { saved: '', running: '' }
}
function clearSelection(resetInput = true) {
  if (selectedImage.value) URL.revokeObjectURL(selectedImage.value)
  selectedImage.value = ''
  file.value = null
  if (resetInput && input.value) input.value.value = ''
}
function choose(event) {
  const selected = event.target.files[0] ?? null
  clearSelection(false)
  file.value = selected
  if (file.value) selectedImage.value = URL.createObjectURL(file.value)
  writeError.value = ''
  notice.value = ''
}
async function preview(view, fresh, sourceDirectory) {
  try {
    const blob = await api(`${endpoint()}/image?view=${view}&directory=${encodeURIComponent(sourceDirectory)}`, {}, 'blob')
    if (fresh()) images.value[view] = URL.createObjectURL(blob)
  } catch (error) {
    if (fresh()) imageErrors.value[view] = error.message
  }
}
async function show(value, fresh) {
  releasePreviews()
  snapshot.value = value
  await Promise.all(['saved', 'running'].filter(view => value[view].image !== null).map(view => preview(view, fresh, value[view].directory)))
}
async function read() {
  if (working.value) return
  const fresh = beginRead()
  loading.value = true
  releasePreviews()
  try {
    const target = await api(`${endpoint()}/location`)
    if (!fresh()) return
    directory.value = target.directory
    const value = await api(endpoint())
    if (!fresh()) return
    directory.value = value.directory
    readError.value = ''
    await show(value, fresh)
  } catch (error) {
    if (fresh()) readError.value = error.message
  } finally {
    if (fresh()) loading.value = false
  }
}
async function write(operation) {
  if (working.value) return
  if (!directory.value) { writeError.value = '尚未确认保存角色包路径，请先重读头像位置。'; return }
  if (operation === 'upload') {
    if (!file.value || file.value.size < 1 || file.value.size > 10000000) {
      writeError.value = '请选择 1–10,000,000 字节的 PNG 原件；实际格式由保存接口校验。'
      return
    }
    if (!window.confirm(`将所选 PNG 保存为 ${directory.value}/avatar.png，替换已有头像？当前运行角色与头像不变。`)) return
  } else if (!window.confirm(`移除 ${directory.value}/avatar.png？仅删除这一个常规文件；运行头像和已选上传草稿不变。`)) return
  const form = new FormData()
  if (operation === 'upload') { form.append('file', file.value); form.append('directory', directory.value) }
  const fresh = beginWrite(), currentRead = beginRead()
  working.value = true
  loading.value = false
  writeError.value = ''
  notice.value = ''
  releasePreviews()
  try {
    const value = await api(endpoint(), operation === 'upload' ? { method: 'PUT', body: form } : { method: 'DELETE', body: JSON.stringify({ directory: directory.value }) })
    if (!fresh()) return
    if (operation === 'upload') clearSelection()
    readError.value = ''
    notice.value = `${operation === 'upload' ? '头像原件已保存' : '保存头像已移除'}；当前运行头像不变。`
    emit('changed')
    await show(value, currentRead)
  } catch (error) {
    if (!fresh()) return
    writeError.value = error.status >= 400 && error.status < 500
      ? `操作未被接受：${error.message}`
      : `操作结果未确认：${error.message} 请重读实际文件核对；不会自动重试。`
  } finally {
    if (fresh()) working.value = false
  }
}
onMounted(read)
onBeforeUnmount(() => { releasePreviews(); clearSelection() })
</script>

<template>
  <section class="surface persona-avatar" aria-labelledby="avatar-title">
    <div class="section-heading"><div><h2 id="avatar-title">角色头像 · 可选</h2>
      <p class="muted">PNG 原件仅用于面板。保存包与本次运行快照分开展示，不进入提示词、群聊或记忆，不热加载。</p></div>
      <v-btn variant="outlined" :loading="loading" :disabled="working" @click="read">重读头像</v-btn></div>
    <v-alert v-if="readError" type="error" variant="tonal" role="alert">读取失败：{{ readError }}。已有元数据仅是上次成功读取值。可明确上传 PNG 或移除坏的常规文件；不会用运行图覆盖保存值。</v-alert>
    <v-alert v-if="writeError" type="error" variant="tonal" role="alert">{{ writeError }}</v-alert>
    <v-alert v-if="notice" type="success" variant="tonal" role="status">{{ notice }}</v-alert>
    <p v-if="directory" class="muted avatar-path">已读保存目标：{{ directory }}/avatar.png；绑定变化时操作被拒绝，不自动改写另一角色包。</p>
    <template v-if="snapshot">
      <v-chip variant="tonal" :color="snapshot.restart_required?'warning':'info'">{{ snapshot.restart_required?'保存头像待重启':'保存与运行头像一致' }}</v-chip>
      <p class="muted">共用保存包：{{ snapshot.affected_scenes.map(sceneName).join('、') }}。元数据是本次读取值，预览另读实际原件。</p>
      <div class="avatar-grid">
        <div v-for="view in ['saved','running']" :key="view" class="avatar-source">
          <h3>{{ view === 'saved' ? '保存包' : `运行快照 · ${snapshot.running.name}` }}</h3>
          <p class="muted avatar-path">{{ snapshot[view].path }}</p>
          <img v-if="images[view]" :src="images[view]" :alt="`${view==='saved'?'保存包':'运行角色'}头像原件`" @error="imageErrors[view]='浏览器未能解码实际 PNG 原件'" />
          <p v-if="snapshot[view].image">{{ snapshot[view].image.width }} × {{ snapshot[view].image.height }} · {{ snapshot[view].image.bytes }} 字节{{ snapshot[view].image.animated?' · 动画':'' }}</p>
          <p v-else class="muted">没有头像，未生成占位原件。</p>
          <v-alert v-if="imageErrors[view]" type="error" variant="tonal" role="alert">{{ imageErrors[view] }}</v-alert>
        </div>
      </div>
    </template>
    <form @submit.prevent="write('upload')">
      <label class="avatar-input">选择 PNG 原件（不转换或裁剪）<input ref="input" type="file" accept="image/png,.png" :disabled="working" @change="choose" /></label>
      <template v-if="file"><p>{{ file.name }} · {{ file.size }} 字节 · 尚未保存</p>
        <img v-if="selectedImage" :src="selectedImage" alt="尚未保存的本机文件预览，实际 PNG 格式未由服务器确认" /></template>
      <div class="form-actions"><v-btn type="submit" color="primary" :loading="working" :disabled="!file || !directory || loading">保存 avatar.png</v-btn>
        <v-btn variant="text" :disabled="working || !file" @click="clearSelection">放弃所选文件</v-btn>
        <v-btn variant="outlined" color="error" :disabled="working || loading || !directory" @click="write('delete')">移除保存头像</v-btn></div>
    </form>
  </section>
</template>

<style scoped>
.avatar-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1.25rem; margin: 1rem 0; }
.avatar-path { overflow-wrap: anywhere; }
.persona-avatar img { display: block; width: 160px; max-width: 100%; height: 160px; object-fit: contain; border: 1px solid var(--border-color, #ddd); border-radius: 12px; margin: .75rem 0; }
.avatar-input { display: grid; gap: .5rem; margin: 1rem 0; }
@media (max-width: 640px) { .avatar-grid { grid-template-columns: 1fr; } }
</style>
