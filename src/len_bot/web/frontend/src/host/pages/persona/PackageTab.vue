<script setup>
// Move a role between hosts as one ZIP, and the raw role files for people who know the format.
import { computed, ref, watch } from 'vue'
import { api, sceneName } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { notify, readPendingRestart } from '../../store.js'
import { confirm } from '../../../composables/useConfirm.js'
import Panel from '../../ui/Panel.vue'
import AdvancedFields from '../../ui/AdvancedFields.vue'
import ErrorNote from '../../ui/ErrorNote.vue'
import DevOnly from '../../ui/DevOnly.vue'

const props = defineProps({ scene: { type: String, required: true } })
const emit = defineEmits(['dirty'])

const download = useAction()
async function exportPackage() {
  const blob = await download.run(() => api(`/api/host/scenes/${encodeURIComponent(props.scene)}/persona-package`, {}, 'blob'))
  if (!blob) return
  const url = URL.createObjectURL(blob), link = document.createElement('a')
  link.href = url
  link.download = `persona-${props.scene.replace(':', '-')}.zip`
  link.click()
  URL.revokeObjectURL(url)
}

const name = ref(''), zip = ref(null), imported = ref(null), upload = useAction()
const nameOk = computed(() => /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(name.value))
async function importPackage() {
  const form = new FormData()
  form.append('name', name.value)
  form.append('file', zip.value)
  const result = await upload.run(() => api('/api/host/personas/import', { method: 'POST', body: form }))
  if (!result) return
  imported.value = result
  name.value = ''
  zip.value = null
}

const files = [['persona.yaml', '设定 persona.yaml'], ['voice.md', '说话方式 voice.md'], ['boundaries.md', '底线 boundaries.md'], ['examples.yaml', '样例 examples.yaml']]
const raw = useResource(() => api(`/api/host/scenes/${encodeURIComponent(props.scene)}/persona-files`), { immediate: false })
const file = ref('persona.yaml'), text = ref('')
watch(raw.data, value => { if (value) text.value = value.saved[file.value] })
const dirty = computed(() => Boolean(raw.data.value && text.value !== raw.data.value.saved[file.value]))
watch(dirty, value => emit('dirty', value), { immediate: true })
async function pick(next) {
  if (dirty.value && !await confirm({ title: '这个文件还没保存，放弃修改？', confirmLabel: '放弃', danger: true })) return
  file.value = next
  text.value = raw.data.value.saved[next]
}
const save = useAction()
async function saveRaw() {
  const result = await save.run(() => api(`/api/host/scenes/${encodeURIComponent(props.scene)}/persona-files/${file.value}`, {
    method: 'PUT', body: JSON.stringify({ content: text.value, directory: raw.data.value.saved_path }) }))
  if (!result) return
  raw.data.value = result
  readPendingRestart()
  notify('已保存，重启后生效')
}
</script>

<template>
  <Panel title="导出" description="把这个角色的设定、资料、表情和头像打包成一个 ZIP，可以在别的 LenBot 上导入。">
    <template #actions><v-btn variant="outlined" :loading="download.busy.value" @click="exportPackage">下载 ZIP</v-btn></template>
    <ErrorNote v-if="download.error.value" title="没有导出成功" :error="download.error.value" />
  </Panel>

  <Panel tag="form" title="导入" description="导入的角色放进新文件夹，不会替换现在的角色。导入后到群聊设置里选用它。" @submit.prevent="importPackage">
    <div class="form-grid">
      <v-text-field v-model="name" label="文件夹名" :error-messages="name && !nameOk ? '只能用英文字母、数字、- 和 _' : ''"
        hint="英文字母、数字、- 或 _，比如 my-role" persistent-hint />
      <v-file-input v-model="zip" label="角色 ZIP" accept=".zip,application/zip" prepend-icon="" />
    </div>
    <ErrorNote v-if="upload.error.value" title="没有导入成功" :error="upload.error.value" />
    <v-alert v-if="imported" type="success">
      已导入 {{ imported.persona.name }}，放在 {{ imported.config_path }}。
      <RouterLink :to="{ name: 'host-scenes', query: { scene, tab: 'settings' } }">去 {{ sceneName(scene) }} 的设置里选用</RouterLink>
      <DevOnly label="导入结果" :json="imported" />
    </v-alert>
    <template #footer><v-spacer /><v-btn type="submit" color="primary" :loading="upload.busy.value" :disabled="!nameOk || !zip">导入</v-btn></template>
  </Panel>

  <AdvancedFields label="直接编辑角色文件" class="raw">
    <div class="raw-body">
      <ErrorNote v-if="raw.error.value" title="读取角色文件失败" :error="raw.error.value" @retry="raw.reload()" />
      <v-btn v-if="!raw.data.value && !raw.error.value" variant="outlined" class="start" :loading="raw.loading.value" @click="raw.reload()">读取角色文件</v-btn>
      <form v-if="raw.data.value" class="stack" @submit.prevent="saveRaw">
        <p class="muted small">设定页改不到的内容可以在这里改。保存前会检查格式，格式不对不会保存。</p>
        <v-select :model-value="file" :items="files.map(([value, title]) => ({ value, title }))" label="文件" @update:model-value="pick" />
        <v-textarea v-model="text" rows="18" auto-grow spellcheck="false" class="mono" />
        <ErrorNote v-if="save.error.value" title="没有保存成功" :error="save.error.value" />
        <v-btn type="submit" color="primary" class="start" :loading="save.busy.value" :disabled="!dirty">保存这个文件</v-btn>
        <DevOnly label="文件位置"><code>{{ raw.data.value.saved_path }}</code></DevOnly>
      </form>
    </div>
  </AdvancedFields>
</template>

<style scoped>
.raw :deep(.advanced-grid){display:block}
.raw-body{display:grid;gap:var(--sp-3)}
.raw-body p{margin:0}
.start{justify-self:start}
</style>
