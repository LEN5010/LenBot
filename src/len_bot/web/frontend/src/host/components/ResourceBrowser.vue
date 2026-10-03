<script setup>
import { computed, onScopeDispose, ref, watch } from 'vue'
import { useAction, useResource } from '../../composables/useResource.js'
import { resourcesApi } from '../api/resources.js'
import { notify } from '../store.js'
import { formatTime } from '../time.js'
import { resourceLabel } from '../resourceLabels.js'
import ErrorNote from './ErrorNote.vue'

const props = defineProps({ scene: { type: String, required: true }, taskId: { type: Number, default: null }, operator: { type: String, default: '' },
  initialPath: { type: String, default: '' }, selectable: { type: Boolean, default: true } })
const emit = defineEmits(['changed', 'select'])
const scope = ref(props.taskId === null ? 'shared' : 'workspace'), path = ref(props.initialPath), rows = ref([])
const scopes = computed(() => props.taskId === null ? [['shared', '共享资料']] : [
  ['workspace', '工作区'], ['inputs', '输入快照'], ['deliveries', '登记交付'], ['runtime', '运行目录'],
])
const location = computed(() => ({ scope: scope.value, task_id: props.taskId, path: path.value }))
const listing = useResource(async (more = false) => ({ more: more === true,
  ...(await resourcesApi.list(props.scene, location.value, more === true ? listing.data.value.next_offset : 0)) }))
watch(listing.data, value => { if (value) rows.value = value.more ? [...rows.value, ...value.entries] : value.entries })
watch([scope, path], () => { rows.value = []; listing.reload() })
function selectScope(value) { scope.value = value; path.value = '' }
function parent() { path.value = path.value.split('/').slice(0, -1).join('/') }
const size = bytes => bytes == null ? '—' : bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${(bytes / 1024).toFixed(1)} KB`
const purpose = { workspace: '工作文件', output: 'out 产物原件', session: 'Pi 会话', inputs: '输入快照', deliveries: '独立交付副本', runtime: '运行文件', shared: '共享资料' }
const upload = { uploaded: '平台已确认上传', failed: '上传失败', unconfirmed: '上传结果未确认' }
const browserKind = { download: '浏览器下载', screenshot: '页面截图', pdf: '页面 PDF' }

const previewOpen = ref(false), selected = ref(null), text = ref(''), objectUrl = ref(null)
const preview = useResource(async (entry, offset = 0) => ({ entry,
  ...(entry.preview === 'text' ? { page: await resourcesApi.text(props.scene, entry.reference, offset) }
    : { blob: await resourcesApi.preview(props.scene, entry.reference) }) }), { immediate: false })
function releaseUrl() { if (objectUrl.value) URL.revokeObjectURL(objectUrl.value); objectUrl.value = null }
watch(preview.data, value => {
  if (!previewOpen.value || value.entry !== selected.value) return
  if (value.page) text.value = value.page.offset === 0 ? value.page.text : text.value + value.page.text
  else { releaseUrl(); objectUrl.value = URL.createObjectURL(value.blob) }
})
watch(previewOpen, value => { if (!value) releaseUrl() })
onScopeDispose(releaseUrl)
function open(entry) {
  if (entry.kind === 'directory') { path.value = entry.reference.path; return }
  selected.value = entry; text.value = ''; releaseUrl(); previewOpen.value = true
  preview.reload(entry)
}

const copying = ref(null), copyMode = ref('register'), name = ref(''), note = ref(''), save = useAction()
const copyOpen = computed({ get: () => copying.value !== null, set: value => { if (!value) copying.value = null } })
function chooseCopy(entry, mode) {
  copying.value = entry; copyMode.value = mode; name.value = entry.name; note.value = ''; save.error.value = null
}
async function copy() {
  const body = { reference: copying.value.reference, requester: props.operator, name: name.value.trim() }
  const value = await save.run(() => copyMode.value === 'register'
    ? resourcesApi.register(props.scene, { ...body, note: note.value }) : resourcesApi.adopt(props.scene, body))
  if (!value) return
  copying.value = null; notify(`${copyMode.value === 'register' ? '已登记交付' : '已采用为共享资料'}：${value.name}`)
  listing.reload(); emit('changed')
}
const removal = useAction()
async function remove(entry) {
  const effect = scope.value === 'deliveries' ? '删除本地交付副本；任务记录和历史平台上传回执保留。' : '删除这个文件；已有任务输入快照和独立登记副本保留。'
  if (!window.confirm(`${effect}\n${resourceLabel(entry.reference)}`)) return
  const value = await removal.run(() => resourcesApi.remove(props.scene, { reference: entry.reference, requester: props.operator }))
  if (!value) return
  notify(`已删除：${value.name}`); listing.reload(); emit('changed')
}
const uploading = ref(false), file = ref(null), uploadName = ref(''), uploadAction = useAction()
watch(file, value => { uploadName.value = value ? value.name : '' })
function chooseUpload() { file.value = null; uploadName.value = ''; uploadAction.error.value = null; uploading.value = true }
async function uploadFile() {
  const value = await uploadAction.run(() => resourcesApi.upload(props.scene, { file: file.value, requester: props.operator, name: uploadName.value.trim() }))
  if (!value) return
  uploading.value = false; file.value = null; notify(`已上传共享资料：${value.name}`); listing.reload(); emit('changed')
}
function sourceLabel(entry) {
  return entry.purpose === 'inputs'
    ? (entry.source.reference ? resourceLabel(entry.source.reference) : entry.source.source_path)
    : resourceLabel(entry.source)
}
const validOperator = computed(() => /^[1-9][0-9]*$/.test(props.operator))
</script>

<template>
  <section class="resource-browser surface">
    <div class="bar">
      <nav aria-label="文件范围"><v-btn v-for="[value, title] in scopes" :key="value" size="small"
        :variant="scope === value ? 'tonal' : 'text'" :color="scope === value ? 'primary' : undefined" @click="selectScope(value)">{{ title }}</v-btn></nav>
      <div class="actions"><v-btn v-if="scope === 'shared'" size="small" variant="tonal" :disabled="!validOperator" @click="chooseUpload">上传资料</v-btn>
        <v-btn size="small" variant="text" :loading="listing.loading.value" @click="listing.reload()">刷新</v-btn></div>
    </div>
    <div class="path"><v-btn v-if="path" size="small" variant="text" @click="parent">上一级</v-btn><code>/{{ path }}</code></div>
    <p class="muted">{{ scope === 'runtime' ? '运行 home、输入和控制文件；清理按任务环境管理。' : scope === 'deliveries' ? '登记的独立副本；原工作区释放后仍可下载。' : '打开文件时读取正文，下载保持原文件。' }}</p>
    <ErrorNote v-if="listing.error.value" title="读取文件失败" :error="listing.error.value" />
    <ErrorNote v-if="removal.error.value" title="删除失败" :error="removal.error.value" />
    <p v-if="listing.data.value && !rows.length" class="muted">{{ listing.data.value.exists ? '目录为空' : '当前目录不存在' }}</p>
    <ul class="entries">
      <li v-for="entry in rows" :key="entry.reference.file_id || entry.reference.path">
        <div class="entry-info">
          <button v-if="entry.kind === 'directory' || (entry.kind === 'file' && entry.exists && entry.preview !== 'download')" class="filename" @click="open(entry)">{{ entry.kind === 'directory' ? '▸ ' : '' }}{{ entry.name }}</button>
          <strong v-else>{{ entry.name }}</strong>
          <small>{{ purpose[entry.purpose] }} · {{ size(entry.size) }} · {{ formatTime(entry.modified) }}{{ entry.kind === 'symlink' ? ' · 符号链接' : '' }}{{ !entry.exists && !entry.deletion ? ' · 副本已不在磁盘' : '' }}</small>
          <small v-if="entry.source" class="source">来源：{{ sourceLabel(entry) }}</small>
          <small v-if="entry.browser_source">{{ browserKind[entry.browser_source.kind] }} · {{ formatTime(entry.browser_source.created) }}<br />
            生成时页面：{{ entry.browser_source.page_title }} · {{ entry.browser_source.page_url }}
            <template v-if="entry.browser_source.download_url"><br />下载地址：{{ entry.browser_source.download_url }}</template>
          </small>
          <small v-if="entry.registrations.length">曾登记交付：{{ entry.registrations.map(id => `#${id}`).join('、') }}（独立副本）</small>
          <small v-if="entry.deletion">{{ formatTime(entry.deletion.created) }} · QQ {{ entry.deletion.requester }} 已删除本地副本</small>
          <p v-if="entry.note">{{ entry.note }}</p>
          <small v-if="entry.upload">{{ upload[entry.upload.status] || entry.upload.status }}</small>
        </div>
        <div v-if="entry.kind === 'file' && entry.exists" class="actions">
          <v-btn v-if="entry.preview === 'download'" size="small" variant="text" @click="open({ ...entry, preview: 'text' })">查看文本</v-btn>
          <v-btn size="small" variant="text" :href="resourcesApi.downloadUrl(scene, entry.reference)">下载</v-btn>
          <v-btn v-if="selectable && scope !== 'runtime'" size="small" variant="text" @click="emit('select', entry)">用作任务资料</v-btn>
          <v-btn v-if="scope === 'workspace'" size="small" variant="text" :disabled="!validOperator" @click="chooseCopy(entry, 'register')">登记交付</v-btn>
          <v-btn v-if="['workspace', 'inputs', 'deliveries'].includes(scope)" size="small" variant="text" :disabled="!validOperator" @click="chooseCopy(entry, 'adopt')">采用共享</v-btn>
          <v-btn v-if="entry.deletable" size="small" variant="text" color="error" :disabled="!validOperator || removal.busy.value" @click="remove(entry)">删除</v-btn>
        </div>
      </li>
    </ul>
    <v-btn v-if="listing.data.value?.next_offset != null" variant="text" :loading="listing.loading.value" @click="listing.reload(true)">显示更多</v-btn>
    <p v-if="taskId && !validOperator" class="muted">填写你的 QQ 后可登记工作成果。</p>
  </section>
  <v-dialog v-model="previewOpen" max-width="1000" scrollable>
    <v-card :title="selected?.name">
      <v-card-text>
        <ErrorNote v-if="preview.error.value" title="预览失败" :error="preview.error.value" />
        <v-progress-linear v-if="preview.loading.value" indeterminate />
        <pre v-if="selected?.preview === 'text'" class="text-preview">{{ text }}</pre>
        <img v-if="selected?.preview === 'image' && objectUrl" :src="objectUrl" :alt="selected.name" class="image-preview" />
        <iframe v-if="selected?.preview === 'pdf' && objectUrl" :src="objectUrl" :title="selected.name" class="pdf-preview" />
        <v-btn v-if="selected?.preview === 'text' && preview.data.value?.entry === selected && preview.data.value.page?.next_offset != null" variant="text" :loading="preview.loading.value"
          @click="preview.reload(selected, preview.data.value.page.next_offset)">继续读取</v-btn>
      </v-card-text>
      <v-card-actions><v-spacer /><v-btn @click="previewOpen = false">关闭</v-btn></v-card-actions>
    </v-card>
  </v-dialog>
  <v-dialog v-model="copyOpen" max-width="540" persistent>
    <v-card :title="copyMode === 'register' ? '登记为独立交付' : '采用为本场景共享资料'">
      <v-card-text>
        <p class="muted">{{ copyMode === 'register' ? '复制工作区文件，保留原件；登记后可下载或交给 Bot 发送。' : '复制文件到本场景共享资料，供后续任务选择；保留来源文件，不覆盖同名资料。' }}</p>
        <v-text-field v-model="name" label="文件名" /><v-textarea v-if="copyMode === 'register'" v-model="note" label="说明" rows="2" />
        <ErrorNote v-if="save.error.value" title="保存失败" :error="save.error.value" />
      </v-card-text>
      <v-card-actions><v-btn :disabled="save.busy.value" @click="copyOpen = false">取消</v-btn><v-spacer /><v-btn color="primary" :disabled="!name.trim() || !validOperator" :loading="save.busy.value" @click="copy">保存</v-btn></v-card-actions>
    </v-card>
  </v-dialog>
  <v-dialog v-model="uploading" max-width="540" persistent>
    <v-card title="上传本场景共享资料">
      <v-card-text>
        <v-file-input v-model="file" label="选择文件" :disabled="uploadAction.busy.value" />
        <v-text-field v-model="uploadName" label="共享文件名" :disabled="uploadAction.busy.value" />
        <p class="muted">上传后由新任务明确选择，不覆盖已有同名文件。</p>
        <ErrorNote v-if="uploadAction.error.value" title="上传失败" :error="uploadAction.error.value" />
      </v-card-text>
      <v-card-actions><v-btn :disabled="uploadAction.busy.value" @click="uploading = false">取消</v-btn><v-spacer />
        <v-btn color="primary" :loading="uploadAction.busy.value" :disabled="!file || !uploadName.trim() || !validOperator" @click="uploadFile">上传</v-btn></v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.resource-browser{display:grid;gap:10px;min-width:0}.bar,.actions,.path{display:flex;gap:8px;align-items:center}.bar{justify-content:space-between;flex-wrap:wrap}.bar nav,.actions{display:flex;flex-wrap:wrap}.path code{overflow-wrap:anywhere}.entries{padding:0;margin:0;list-style:none}.entries li{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;padding:10px 0;border-bottom:1px solid var(--line)}.entry-info{min-width:0;flex:1}.entry-info small{display:block;color:var(--muted);overflow-wrap:anywhere}.entry-info p{margin:4px 0}.entry-info strong,.filename{overflow-wrap:anywhere}.entries .actions{max-width:360px;justify-content:flex-end}.filename{color:var(--primary);text-align:left;font-weight:600}.text-preview{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.6 monospace;max-height:65vh;overflow:auto}.image-preview{display:block;max-width:100%;max-height:70vh;margin:auto;object-fit:contain}.pdf-preview{width:100%;height:70vh;border:0}@media(max-width:600px){.entries li{flex-direction:column}.entries .actions{justify-content:flex-start}}
</style>
