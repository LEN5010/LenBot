<script setup>
import { computed, onScopeDispose, ref, watch } from 'vue'
import { api } from '../api.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'

const props = defineProps({
  scene: { type: String, required: true },
  taskId: { type: Number, required: true },
  files: { type: Array, required: true },
  configured: { type: Boolean, required: true },
})
const emit = defineEmits(['dirty', 'busy'])
const snapshot = ref(null), loading = ref(false), adopting = ref(false), downloading = ref(null)
const readError = ref(''), adoptError = ref(''), downloadError = ref(''), downloadNotice = ref('')
const result = ref(null), stale = ref(false)
const selectedFile = ref(null), targetName = ref(''), confirmed = ref(false)
const busy = computed(() => loading.value || adopting.value || downloading.value !== null)
const dirty = computed(() => selectedFile.value !== null || targetName.value !== '' || confirmed.value)
watch(busy, value => emit('busy', value), { immediate: true, flush: 'sync' })
watch(dirty, value => emit('dirty', value), { immediate: true, flush: 'sync' })
onScopeDispose(() => { emit('dirty', false); emit('busy', false) })
const selection = () => `${props.scene}\u0000${props.taskId}\u0000${props.configured}`
const beginRead = useRequestGuard(selection), beginAdopt = useRequestGuard(selection), beginDownload = useRequestGuard(selection)
const canAdopt = computed(() => props.configured && snapshot.value && selectedFile.value !== null
  && props.files.some(file => file.id === selectedFile.value) && targetName.value.trim() && confirmed.value && !busy.value)

async function read() {
  if (!props.configured || busy.value) return
  const fresh = beginRead()
  loading.value = true
  try {
    const value = await api(`/api/host/materials?${new URLSearchParams({scene:props.scene})}`)
    if (!fresh()) return
    snapshot.value = value; readError.value = ''; stale.value = false
  } catch (error) { if (fresh()) { readError.value = error.message; stale.value = snapshot.value !== null } }
  finally { if (fresh()) loading.value = false }
}
function chooseFile(id) {
  selectedFile.value = id; confirmed.value = false
  const file = props.files.find(item => item.id === id)
  if (file) targetName.value = file.name
}
function clearDraft() {
  selectedFile.value = null; targetName.value = ''; confirmed.value = false; adoptError.value = ''
}
async function adopt() {
  if (!canAdopt.value) return
  const fresh = beginAdopt(), directory = snapshot.value.directory
  const body = { task_id:props.taskId, file_id:selectedFile.value, name:targetName.value, directory, confirmed:confirmed.value }
  adopting.value = true
  try {
    const value = await api(`/api/host/materials/adopt?${new URLSearchParams({scene:props.scene})}`,
      { method:'POST', body:JSON.stringify(body) })
    if (!fresh()) return
    result.value = value; stale.value = true; clearDraft()
  } catch (error) {
    if (fresh()) {
      adoptError.value = `${error.message} 此次采用没有成功回执；草稿保留，请手动重读共享目录核对，不会自动重试。`
      stale.value = snapshot.value !== null
    }
  } finally { if (fresh()) adopting.value = false }
}
async function download(file) {
  if (busy.value || !props.configured) return
  const fresh = beginDownload()
  downloading.value = file.name; downloadError.value = ''; downloadNotice.value = ''
  try {
    const blob = await api(`/api/host/materials/file?${new URLSearchParams({scene:props.scene,name:file.name})}`, {}, 'blob')
    if (!fresh()) return
    const url = URL.createObjectURL(blob)
    try {
      const link = document.createElement('a')
      link.href = url; link.download = file.name; document.body.appendChild(link); link.click(); link.remove()
      downloadNotice.value = `已向浏览器发起 ${file.name} 的原字节下载；不代表已保存到本机，也不会上传 QQ。`
    } finally { setTimeout(() => URL.revokeObjectURL(url), 1000) }
  } catch (error) { if (fresh()) downloadError.value = error.message }
  finally { if (fresh()) downloading.value = null }
}
</script>

<template>
  <section class="shared-materials" aria-label="本场景共享资料">
    <div class="section-heading"><h3>本场景共享资料</h3><v-btn variant="outlined" :loading="loading" :disabled="!configured || busy" @click="read">读取共享目录</v-btn></div>
    <p class="muted">普通文件保全与下载，不属于共享技能或公共资料。新任务须明确选择输入才会建立其私有快照；采用本身不会挂入容器、调用模型或上传 QQ。</p>
    <p v-if="!configured" class="muted">当前未配置 worker，无法定位实际共享目录。</p>
    <v-alert v-if="readError" type="error" variant="tonal" role="alert" :title="snapshot?'读取失败 · 保留旧快照':'共享目录读取失败'">{{ readError }}</v-alert>
    <p v-if="loading" class="muted" role="status">正在读取实际文件元数据…</p>
    <template v-if="snapshot">
      <p class="path">当前运行配置的目的地：{{ snapshot.directory }}</p>
      <p class="muted">保存但未重启的配置不改变这次目的地；源交付副本保留，目标文件不覆盖。</p>
      <v-alert v-if="stale" type="info" variant="tonal" role="status">下方是上次目录快照，不能当作最新内容；请手动重读核对。</v-alert>
      <p v-if="!snapshot.exists" class="muted">读取时共享目录尚不存在；首次明确采用才创建。</p>
      <p v-else-if="!snapshot.files.length" class="muted">读取时目录存在，但没有普通共享文件。</p>
      <ul v-if="snapshot.files.length" class="material-list"><li v-for="file in snapshot.files" :key="file.name">
        <div><strong>{{ file.name }}</strong> · {{ file.size.toLocaleString('zh-CN') }} 字节</div>
        <v-btn variant="outlined" :loading="downloading===file.name" :disabled="busy || !configured" @click="download(file)">下载原字节</v-btn>
      </li></ul>
      <form @submit.prevent="adopt"><fieldset :disabled="busy || !configured" class="adoption-fields">
        <legend>从当前任务的已登记交付副本采用</legend>
        <v-select :model-value="selectedFile" :items="files.map(file=>({title:`${file.name} · ${file.size} 字节`,value:file.id}))" label="实际交付副本" clearable hide-details="auto" @update:model-value="chooseFile" />
        <v-text-field v-model="targetName" label="共享文件名（保持原字节，不覆盖）" hide-details="auto" @update:model-value="confirmed=false" />
        <v-checkbox v-model="confirmed" label="已核对这份副本内容适合保存在当前场景的共享目录" hide-details />
      </fieldset><div class="form-actions"><v-btn type="submit" color="primary" :loading="adopting" :disabled="!canAdopt">明确复制采用</v-btn>
        <v-btn variant="text" :disabled="busy || !dirty" @click="clearDraft">放弃采用草稿</v-btn></div></form>
      <p v-if="!files.length" class="muted">本任务尚无已登记副本，不从工作区路径或其他任务猜来源。</p>
    </template>
    <p v-else-if="!loading && !readError" class="muted">尚未读取共享目录；先读取真实目的地，再选择原副本采用。</p>
    <v-alert v-if="adoptError" type="error" variant="tonal" role="alert">{{ adoptError }}</v-alert>
    <v-alert v-if="result" type="success" variant="tonal" role="status">上次成功回执：已复制 {{ result.name }} · {{ result.size }} 字节。源任务 {{ result.source_task_id }} 的副本 {{ result.source_file_id }} 保留；未接入任务或上传平台。</v-alert>
    <p v-if="result" class="path">实际目标：{{ result.path }}</p>
    <v-alert v-if="downloadError" type="error" variant="tonal" role="alert">{{ downloadError }}</v-alert>
    <p v-if="downloadNotice" class="muted" role="status">{{ downloadNotice }}</p>
  </section>
</template>

<style scoped>
.shared-materials{border-top:1px solid var(--line);margin-top:20px;padding-top:12px;min-width:0;overflow-wrap:anywhere}.section-heading{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}.section-heading h3{margin:0}.path{font-size:13px;overflow-wrap:anywhere}.material-list{list-style:none;padding:0;display:grid;gap:10px}.material-list li{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;border:1px solid var(--line);border-radius:10px;padding:12px}.adoption-fields{display:grid;gap:12px;border:1px solid var(--line);border-radius:10px;padding:12px;margin:16px 0}.adoption-fields legend{padding:0 6px}.form-actions{display:flex;gap:10px;flex-wrap:wrap}.shared-materials :deep(.v-btn){min-height:44px}
</style>
