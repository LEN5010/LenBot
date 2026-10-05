<script setup>
import { computed, onScopeDispose, ref, watch } from 'vue'
import { mdiArrowUp, mdiDotsVertical, mdiFileOutline, mdiFolderOutline } from '@mdi/js'
import { confirm } from '../../composables/useConfirm.js'
import { useAction, useResource } from '../../composables/useResource.js'
import { resourcesApi } from '../api/resources.js'
import { notify } from '../store.js'
import { formatTime } from '../time.js'
import { resourceLabel } from '../resourceLabels.js'
import Panel from '../ui/Panel.vue'
import PageTabs from '../ui/PageTabs.vue'
import ErrorNote from '../ui/ErrorNote.vue'
import ObjectList from '../ui/ObjectList.vue'
import ObjectRow from '../ui/ObjectRow.vue'
import StatusBadge from '../ui/StatusBadge.vue'
import FormDialog from '../ui/FormDialog.vue'
import CodeBlock from '../ui/CodeBlock.vue'
import LoadMore from '../ui/LoadMore.vue'

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
  const effect = scope.value === 'deliveries' ? '删除本地交付副本，任务记录和发送记录保留。' : '删除这个文件，已经给到任务的副本和登记的交付不受影响。'
  if (!await confirm({ title: `删除 ${entry.name}？`, text: `${effect}\n${resourceLabel(entry.reference)}`, confirmLabel: '删除', danger: true })) return
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
const validOperator = computed(() => /^[a-z][a-z0-9_-]*:[^:\s/\\]+$/.test(props.operator))
const openable = entry => entry.kind === 'directory' || (entry.kind === 'file' && entry.exists && entry.preview !== 'download')
function details(entry) {
  const lines = [`${purpose[entry.purpose]} · ${size(entry.size)} · ${formatTime(entry.modified)}${entry.kind === 'symlink' ? ' · 符号链接' : ''}${!entry.exists && !entry.deletion ? ' · 副本已不在磁盘' : ''}`]
  if (entry.source) lines.push(`来源：${sourceLabel(entry)}`)
  if (entry.browser_source) lines.push(`${browserKind[entry.browser_source.kind]} · ${entry.browser_source.page_title} · ${entry.browser_source.page_url}`)
  if (entry.registrations.length) lines.push(`登记过交付：${entry.registrations.map(id => `#${id}`).join('、')}`)
  if (entry.deletion) lines.push(`${formatTime(entry.deletion.created)} · 平台账号 ${entry.deletion.requester} 删除了本地副本`)
  return lines.join('\n')
}
</script>

<template>
  <Panel title="文件" flush class="resource-browser">
    <template #actions>
      <v-btn v-if="scope === 'shared'" size="small" variant="tonal" color="primary" :disabled="!validOperator" @click="chooseUpload">上传资料</v-btn>
      <v-btn size="small" variant="text" :loading="listing.loading.value" @click="listing.reload()">刷新</v-btn>
    </template>
    <div class="browser-body">
      <PageTabs v-if="scopes.length > 1" :tabs="scopes" :model-value="scope" local label="文件范围" @update:model-value="selectScope" />
      <div class="inline path">
        <v-btn v-if="path" size="small" variant="text" :prepend-icon="mdiArrowUp" @click="parent">上一级</v-btn>
        <code>/{{ path }}</code>
        <span class="muted small hint">{{ scope === 'runtime' ? '运行目录随任务环境清理' : scope === 'deliveries' ? '登记的副本，工作区释放后仍可下载' : '' }}</span>
      </div>
      <ErrorNote v-if="listing.error.value" title="读取文件失败" :error="listing.error.value" @retry="listing.reload()" />
      <ErrorNote v-if="removal.error.value" title="删除失败" :error="removal.error.value" />
      <p v-if="listing.data.value && !rows.length" class="muted empty">{{ listing.data.value.exists ? '目录是空的' : '这个目录不存在' }}</p>
      <ObjectList divided>
        <ObjectRow v-for="entry in rows" :key="entry.reference.file_id || entry.reference.path" :title="entry.name"
          :clickable="openable(entry)" @click="open(entry)">
          <template #prepend><v-icon :icon="entry.kind === 'directory' ? mdiFolderOutline : mdiFileOutline" size="20" color="secondary" /></template>
          <template #subtitle><span class="details">{{ details(entry) }}</span></template>
          <p v-if="entry.note" class="note">{{ entry.note }}</p>
          <template v-if="entry.upload" #meta><StatusBadge kind="upload" :value="entry.upload.status" /></template>
          <template v-if="entry.kind === 'file' && entry.exists" #actions>
            <v-btn size="small" variant="text" :href="resourcesApi.downloadUrl(scene, entry.reference)">下载</v-btn>
            <v-menu>
              <template #activator="{ props: menu }"><v-btn v-bind="menu" :icon="mdiDotsVertical" size="small" variant="text" aria-label="更多操作" /></template>
              <v-list density="compact">
                <v-list-item v-if="entry.preview === 'download'" title="查看文本" @click="open({ ...entry, preview: 'text' })" />
                <v-list-item v-if="selectable && scope !== 'runtime'" title="用作任务资料" @click="emit('select', entry)" />
                <v-list-item v-if="scope === 'workspace'" title="登记交付" :disabled="!validOperator" @click="chooseCopy(entry, 'register')" />
                <v-list-item v-if="['workspace', 'inputs', 'deliveries'].includes(scope)" title="存为共享资料" :disabled="!validOperator" @click="chooseCopy(entry, 'adopt')" />
                <v-list-item v-if="entry.deletable" title="删除" base-color="error" :disabled="!validOperator || removal.busy.value" @click="remove(entry)" />
              </v-list>
            </v-menu>
          </template>
        </ObjectRow>
      </ObjectList>
      <LoadMore v-if="listing.data.value?.next_offset != null" :loading="listing.loading.value" @more="listing.reload(true)" />
      <p v-if="taskId && !validOperator" class="muted small">在页面上方填写你的 平台账号 后可以登记成果、存为共享资料或删除。</p>
    </div>
  </Panel>

  <FormDialog v-model="previewOpen" :title="selected?.name || ''" size="lg" cancel-label="关闭">
    <ErrorNote v-if="preview.error.value" title="预览失败" :error="preview.error.value" />
    <v-progress-linear v-if="preview.loading.value" indeterminate color="primary" />
    <CodeBlock v-if="selected?.preview === 'text'" :text="text" class="text-preview" />
    <img v-if="selected?.preview === 'image' && objectUrl" :src="objectUrl" :alt="selected.name" class="image-preview" />
    <iframe v-if="selected?.preview === 'pdf' && objectUrl" :src="objectUrl" :title="selected.name" class="pdf-preview" />
    <LoadMore v-if="selected?.preview === 'text' && preview.data.value?.entry === selected && preview.data.value.page?.next_offset != null"
      label="继续读取" :loading="preview.loading.value" @more="preview.reload(selected, preview.data.value.page.next_offset)" />
  </FormDialog>
  <FormDialog v-model="copyOpen" :title="copyMode === 'register' ? '登记为交付文件' : '存为本群共享资料'" :busy="save.busy.value">
    <p class="muted">{{ copyMode === 'register' ? '复制一份作为交付，原件保留；登记后可以下载或让 Bot 发送。' : '复制到本群共享资料，以后新建任务时可以选；不会覆盖同名资料。' }}</p>
    <v-text-field v-model="name" label="文件名" />
    <v-textarea v-if="copyMode === 'register'" v-model="note" label="说明" rows="2" />
    <ErrorNote v-if="save.error.value" title="保存失败" :error="save.error.value" />
    <template #actions><v-btn color="primary" :disabled="!name.trim() || !validOperator" :loading="save.busy.value" @click="copy">保存</v-btn></template>
  </FormDialog>
  <FormDialog v-model="uploading" title="上传本群共享资料" :busy="uploadAction.busy.value">
    <v-file-input v-model="file" label="选择文件" :disabled="uploadAction.busy.value" />
    <v-text-field v-model="uploadName" label="共享文件名" hint="不会覆盖已有同名文件" persistent-hint :disabled="uploadAction.busy.value" />
    <ErrorNote v-if="uploadAction.error.value" title="上传失败" :error="uploadAction.error.value" />
    <template #actions><v-btn color="primary" :loading="uploadAction.busy.value" :disabled="!file || !uploadName.trim() || !validOperator" @click="uploadFile">上传</v-btn></template>
  </FormDialog>
</template>

<style scoped>
.browser-body{display:grid;gap:var(--sp-2);padding:0 var(--sp-4) var(--sp-3);min-width:0}
.path code{overflow-wrap:anywhere}
.hint{margin-left:auto}
.empty{margin:0;padding:var(--sp-3) 0}
.details{white-space:pre-line}
.note{margin:var(--sp-1) 0 0}
.text-preview{max-height:65vh}
.image-preview{display:block;max-width:100%;max-height:70vh;margin:auto;object-fit:contain}
.pdf-preview{width:100%;height:70vh;border:0}
</style>
