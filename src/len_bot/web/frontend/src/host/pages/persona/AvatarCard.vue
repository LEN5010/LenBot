<script setup>
// The role's picture in the panel. It is saved right away, apart from the form.
import { computed, ref } from 'vue'
import { api } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { notify, readPendingRestart } from '../../store.js'
import ErrorNote from '../../components/ErrorNote.vue'

const props = defineProps({ scene: { type: String, required: true }, directory: { type: String, required: true }, name: { type: String, default: '' } })
const endpoint = computed(() => `/api/host/scenes/${encodeURIComponent(props.scene)}/persona-avatar`)
const avatar = useResource(() => api(endpoint.value))
const version = ref(0)
const image = computed(() => avatar.data.value?.saved.image
  ? `${endpoint.value}/image?view=saved&directory=${encodeURIComponent(props.directory)}&v=${version.value}` : null)
const input = ref(null), change = useAction()

async function done(result, text) {
  avatar.data.value = result
  avatar.error.value = null
  version.value++
  readPendingRestart()
  notify(text)
}
async function upload(event) {
  const file = event.target.files[0]
  event.target.value = ''
  if (!file) return
  const form = new FormData()
  form.append('file', file)
  form.append('directory', props.directory)
  const result = await change.run(() => api(endpoint.value, { method: 'PUT', body: form }))
  if (result) done(result, '头像已换好，重启后生效')
}
async function remove() {
  if (!window.confirm('删掉这个头像？')) return
  const result = await change.run(() => api(endpoint.value, { method: 'DELETE', body: JSON.stringify({ directory: props.directory }) }))
  if (result) done(result, '头像已删除，重启后生效')
}
</script>

<template>
  <div class="avatar">
    <img v-if="image" :src="image" :alt="`${name} 的头像`" />
    <div v-else class="blank" aria-hidden="true">{{ name.slice(0, 1) }}</div>
    <input ref="input" type="file" accept="image/png,.png" hidden @change="upload" />
    <div class="buttons">
      <v-btn size="small" variant="tonal" :loading="change.busy.value" @click="input.click()">{{ image ? '换头像' : '上传头像' }}</v-btn>
      <v-btn v-if="image" size="small" variant="text" :disabled="change.busy.value" @click="remove">删除</v-btn>
    </div>
    <span class="muted">PNG 图片，只在面板里显示</span>
    <ErrorNote v-if="avatar.error.value" title="头像读不出来" :error="avatar.error.value" />
    <ErrorNote v-if="change.error.value" title="头像没有改成功" :error="change.error.value" />
  </div>
</template>

<style scoped>
.avatar{display:grid;gap:8px;justify-items:center;width:160px}
img,.blank{width:112px;height:112px;border-radius:50%;object-fit:cover;border:1px solid var(--line)}
.blank{display:grid;place-items:center;font-size:40px;background:var(--list-heading-bg);color:var(--muted)}
.buttons{display:flex;gap:4px}
.avatar .muted{font-size:12px;text-align:center}
@media(max-width:600px){.avatar{width:100%}}
</style>
