<script setup>
import { computed, onScopeDispose, ref, watch } from 'vue'
import { useAction, useResource } from '../../composables/useResource.js'
import { resourcesApi } from '../api/resources.js'
import { notify } from '../store.js'
import { formatTime } from '../time.js'
import ErrorNote from './ErrorNote.vue'

const props = defineProps({ scene: { type: String, required: true }, taskId: { type: Number, default: null }, operator: { type: String, default: '' } })
const emit = defineEmits(['changed'])
const scope = ref(props.taskId === null ? 'shared' : 'workspace'), path = ref(''), rows = ref([])
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

const registering = ref(null), name = ref(''), note = ref(''), save = useAction()
const registerOpen = computed({ get: () => registering.value !== null, set: value => { if (!value) registering.value = null } })
function chooseRegistration(entry) { registering.value = entry; name.value = entry.name; note.value = '' }
async function register() {
  const value = await save.run(() => resourcesApi.register(props.scene, {
    reference: registering.value.reference, requester: props.operator, name: name.value.trim(), note: note.value,
  }))
  if (!value) return
  registering.value = null; notify(`已登记交付：${value.name}`); listing.reload(); emit('changed')
}
const validOperator = computed(() => /^[1-9][0-9]*$/.test(props.operator))
</script>

<template>
  <section class="resource-browser surface">
    <div class="bar">
      <nav aria-label="文件范围"><v-btn v-for="[value, title] in scopes" :key="value" size="small"
        :variant="scope === value ? 'tonal' : 'text'" :color="scope === value ? 'primary' : undefined" @click="selectScope(value)">{{ title }}</v-btn></nav>
      <v-btn size="small" variant="text" :loading="listing.loading.value" @click="listing.reload()">刷新</v-btn>
    </div>
    <div class="path"><v-btn v-if="path" size="small" variant="text" @click="parent">上一级</v-btn><code>/{{ path }}</code></div>
    <p class="muted">{{ scope === 'runtime' ? '运行 home、输入和控制文件；清理按任务环境管理。' : scope === 'deliveries' ? '登记的独立副本；原工作区释放后仍可下载。' : '打开文件时读取正文，下载保持原文件。' }}</p>
    <ErrorNote v-if="listing.error.value" title="读取文件失败" :error="listing.error.value" />
    <p v-if="listing.data.value && !rows.length" class="muted">{{ listing.data.value.exists ? '目录为空' : '当前目录不存在' }}</p>
    <ul class="entries">
      <li v-for="entry in rows" :key="entry.reference.file_id || entry.reference.path">
        <div class="entry-info">
          <button v-if="entry.kind === 'directory' || (entry.kind === 'file' && entry.exists && entry.preview !== 'download')" class="filename" @click="open(entry)">{{ entry.kind === 'directory' ? '▸ ' : '' }}{{ entry.name }}</button>
          <strong v-else>{{ entry.name }}</strong>
          <small>{{ purpose[entry.purpose] }} · {{ size(entry.size) }} · {{ formatTime(entry.modified) }}{{ entry.kind === 'symlink' ? ' · 符号链接' : '' }}{{ !entry.exists ? ' · 副本已不在磁盘' : '' }}</small>
          <p v-if="entry.note">{{ entry.note }}</p>
          <small v-if="entry.upload">{{ upload[entry.upload.status] || entry.upload.status }}</small>
        </div>
        <div v-if="entry.kind === 'file' && entry.exists" class="actions">
          <v-btn v-if="entry.preview === 'download'" size="small" variant="text" @click="open({ ...entry, preview: 'text' })">查看文本</v-btn>
          <v-btn size="small" variant="text" :href="resourcesApi.downloadUrl(scene, entry.reference)">下载</v-btn>
          <v-btn v-if="scope === 'workspace'" size="small" variant="text" :disabled="!validOperator" @click="chooseRegistration(entry)">登记交付</v-btn>
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
  <v-dialog v-model="registerOpen" max-width="540">
    <v-card title="登记为独立交付">
      <v-card-text>
        <p class="muted">复制工作区文件，保留原件；登记后可下载或交给 Bot 发送。</p>
        <v-text-field v-model="name" label="文件名" /><v-textarea v-model="note" label="说明" rows="2" />
        <ErrorNote v-if="save.error.value" title="登记失败" :error="save.error.value" />
      </v-card-text>
      <v-card-actions><v-btn @click="registerOpen = false">取消</v-btn><v-spacer /><v-btn color="primary" :disabled="!name.trim() || !validOperator" :loading="save.busy.value" @click="register">登记</v-btn></v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.resource-browser{display:grid;gap:10px;min-width:0}.bar,.actions,.path{display:flex;gap:8px;align-items:center}.bar{justify-content:space-between;flex-wrap:wrap}.bar nav{display:flex;flex-wrap:wrap}.path code{overflow-wrap:anywhere}.entries{padding:0;margin:0;list-style:none}.entries li{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 0;border-bottom:1px solid var(--line)}.entry-info{min-width:0}.entry-info small{display:block;color:var(--muted)}.entry-info p{margin:4px 0}.entry-info strong,.filename{overflow-wrap:anywhere}.filename{color:var(--primary);text-align:left;font-weight:600}.text-preview{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.6 monospace;max-height:65vh;overflow:auto}.image-preview{display:block;max-width:100%;max-height:70vh;margin:auto;object-fit:contain}.pdf-preview{width:100%;height:70vh;border:0}@media(max-width:600px){.entries li{align-items:flex-start;flex-direction:column}}
</style>
