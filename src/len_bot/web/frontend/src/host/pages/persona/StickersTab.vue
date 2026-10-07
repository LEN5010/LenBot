<script setup>
// Stickers the Bot can send, each with a description it picks by.
import { computed, ref, watch } from 'vue'
import { mdiPlus } from '@mdi/js'
import { api } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { notify, readPendingRestart } from '../../store.js'
import { confirm } from '../../../composables/useConfirm.js'
import Panel from '../../ui/Panel.vue'
import ResourceState from '../../ui/ResourceState.vue'
import ErrorNote from '../../ui/ErrorNote.vue'
import StickerDialog from './StickerDialog.vue'

const props = defineProps({ scene: { type: String, required: true } })
const emit = defineEmits(['dirty'])
const base = computed(() => `/api/host/scenes/${encodeURIComponent(props.scene)}/persona-sticker-files`)
const listing = useResource(() => api(base.value))
const entries = computed(() => listing.data.value?.entries || [])
const loose = computed(() => (listing.data.value?.files || []).filter(file => !entries.value.some(entry => entry.file === file.file)))
const emotions = computed(() => [...new Set(entries.value.flatMap(entry => entry.emotions))])
const tags = computed(() => [...new Set(entries.value.flatMap(entry => entry.tags))])
const image = file => `${base.value}/image?file=${encodeURIComponent(file)}&directory=${encodeURIComponent(listing.data.value.saved_path)}`
const broken = ref({})

// The dialog edits one entry: an existing one, a loose picture being registered, or a new upload (file null).
const dialog = ref(false), editing = ref(null)
watch(dialog, value => emit('dirty', value), { immediate: true })
function edit(entry) { editing.value = entry; dialog.value = true }

const write = useAction()
const putEntries = list => api(`${base.value}/entries`, { method: 'PUT',
  body: JSON.stringify({ directory: listing.data.value.saved_path, entries: list }) })
function done(result, text) {
  listing.data.value = result
  readPendingRestart()
  notify(text)
}
async function save({ file, description, emotions, tags }) {
  const result = await write.run(async () => {
    const directory = listing.data.value.saved_path
    let current = listing.data.value.entries || []
    let name = editing.value?.file
    if (!editing.value) {
      if (listing.data.value.entries === null) current = (await putEntries([])).entries
      const form = new FormData()
      form.append('name', file.name)
      form.append('file', file)
      form.append('directory', directory)
      name = (await api(`${base.value}/image`, { method: 'POST', body: form })).file
      // The picture is saved now; if the description fails below, saving again only retries the description.
      editing.value = { file: name, description, emotions, tags }
    }
    const entry = { file: name, description, emotions, tags }
    const known = current.some(item => item.file === name)
    return putEntries(known ? current.map(item => item.file === name ? entry : item) : [...current, entry])
  })
  if (!result) { listing.reload(); return }
  dialog.value = false
  done(result, '已保存，重启后生效')
}

const removing = useAction()
async function remove(file) {
  if (!await confirm({ title: '删除这个表情？', text: '图片也会一起删掉，重启后生效。', confirmLabel: '删除', danger: true })) return
  const result = await removing.run(async () => {
    const current = listing.data.value.entries || []
    if (current.some(item => item.file === file)) await putEntries(current.filter(item => item.file !== file))
    await api(`${base.value}/image`, { method: 'DELETE', body: JSON.stringify({ file, directory: listing.data.value.saved_path }) })
    return api(base.value)
  })
  if (result) done(result, '已删除，重启后生效')
  else listing.reload()
}
</script>

<template>
  <ResourceState :resource="listing" error-title="读取表情失败">
    <Panel title="表情" description="Bot 聊天时会按描述挑合适的表情发出去。">
      <template #actions><v-btn :prepend-icon="mdiPlus" variant="outlined" @click="edit(null)">添加表情</v-btn></template>
      <ErrorNote v-if="removing.error.value" title="没有删除成功" :error="removing.error.value" />
      <p v-if="!entries.length" class="muted">还没有表情。</p>
      <ul class="plain-list grid">
        <li v-for="entry in entries" :key="entry.file">
          <div class="picture">
            <span v-if="broken[entry.file]" class="muted small">图片打不开</span>
            <img v-else :src="image(entry.file)" :alt="entry.description" loading="lazy" @error="broken[entry.file] = true" />
          </div>
          <p>{{ entry.description }}</p>
          <div v-if="entry.emotions.length || entry.tags.length" class="inline chips">
            <v-chip v-for="item in entry.emotions" :key="`e${item}`">{{ item }}</v-chip>
            <v-chip v-for="item in entry.tags" :key="`t${item}`" variant="outlined">{{ item }}</v-chip>
          </div>
          <div class="actions">
            <v-btn size="small" variant="text" @click="edit(entry)">编辑</v-btn>
            <v-btn size="small" variant="text" color="error" :disabled="removing.busy.value" @click="remove(entry.file)">删除</v-btn>
          </div>
        </li>
      </ul>
    </Panel>
    <Panel v-if="loose.length" title="还没写描述的图片" description="这些图片在角色文件夹里，写上描述后 Bot 才会用。">
      <ul class="plain-list grid">
        <li v-for="item in loose" :key="item.file">
          <div class="picture"><img :src="image(item.file)" :alt="item.file" loading="lazy" /></div>
          <p class="muted small">{{ item.file }}</p>
          <div class="actions">
            <v-btn size="small" variant="text" @click="edit({ file: item.file, description: '', emotions: [], tags: [] })">写描述</v-btn>
            <v-btn size="small" variant="text" color="error" :disabled="removing.busy.value" @click="remove(item.file)">删除</v-btn>
          </div>
        </li>
      </ul>
    </Panel>
  </ResourceState>
  <StickerDialog v-model="dialog" :entry="editing" :image="editing ? image(editing.file) : null" :emotions="emotions" :tags="tags"
    :busy="write.busy.value" :error="write.error.value" @save="save" />
</template>

<style scoped>
p{margin:0}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:var(--sp-3)}
.grid li{display:grid;gap:var(--sp-2);align-content:start;border:1px solid var(--line);border-radius:var(--radius);padding:var(--sp-3);min-width:0;overflow-wrap:anywhere}
.picture{display:grid;place-items:center;height:140px;background:var(--hover);border-radius:var(--radius-sm);overflow:hidden}
.picture img{max-width:100%;max-height:100%;object-fit:contain}
.chips{gap:var(--sp-1)}
.actions{display:flex;justify-content:flex-end}
</style>
