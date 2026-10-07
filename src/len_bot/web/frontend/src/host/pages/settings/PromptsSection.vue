<script setup>
import { computed, ref, watch } from 'vue'
import { api } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { confirm } from '../../../composables/useConfirm.js'
import { notify, readPendingRestart } from '../../store.js'
import Panel from '../../ui/Panel.vue'
import ErrorNote from '../../ui/ErrorNote.vue'

const emit = defineEmits(['dirty'])
const overview = useResource(() => api('/api/host/prompts'))
const selected = ref(null)
const detail = ref(null)
const draft = ref('')
const load = useAction()
const save = useAction()
const setting = useAction()

const items = computed(() => (overview.data.value?.items || [])
  .map(item => ({ title: item.edited ? `${item.name}（已修改）` : item.name, value: item.name })))
const edited = computed(() => (overview.data.value?.items || []).filter(item => item.edited).length)
const outdated = computed(() => {
  const data = overview.data.value
  return data && data.edited_version && data.edited_version !== data.version ? data : null
})
const dirty = computed(() => detail.value !== null && draft.value !== detail.value.text)
watch(dirty, value => emit('dirty', value), { immediate: true })

watch(selected, async name => {
  detail.value = null
  if (!name) return
  const result = await load.run(() => api(`/api/host/prompts/${encodeURIComponent(name)}`), () => selected.value === name)
  if (result) {
    detail.value = result
    draft.value = result.text
  }
})

async function keep(value) {
  const result = await setting.run(() => api('/api/host/prompt-settings', { method: 'PUT', body: JSON.stringify({ keep_on_update: value }) }))
  if (result) overview.data.value = result
}

async function store(text) {
  const name = selected.value
  const result = await save.run(() => api(`/api/host/prompts/${encodeURIComponent(name)}`, { method: 'PUT', body: JSON.stringify({ text }) }))
  if (!result) return
  overview.data.value = result
  detail.value = { ...detail.value, text, edited: text !== detail.value.default }
  draft.value = text
  readPendingRestart()
  notify('已保存，重启后生效')
}

async function restore() {
  if (!await confirm({ title: '恢复默认内容？', text: `${selected.value} 的修改会被删除。`, confirmLabel: '恢复默认', danger: true })) return
  const name = selected.value
  const result = await save.run(() => api(`/api/host/prompts/${encodeURIComponent(name)}`, { method: 'DELETE' }))
  if (!result) return
  overview.data.value = result
  detail.value = { ...detail.value, text: detail.value.default, edited: false }
  draft.value = detail.value.default
  readPendingRestart()
  notify('已恢复默认，重启后生效')
}
</script>

<template>
  <Panel title="框架提示词">
    <v-alert type="warning" variant="tonal">
      框架提示词决定 Bot 怎样读聊天记录、怎样判断要不要回复、怎样调用工具。改错会影响所有群，可能让 Bot 不再回复或用错工具。修改在重启后生效。
    </v-alert>
    <ErrorNote v-if="overview.error.value" title="读取框架提示词失败" :error="overview.error.value" />
    <template v-if="overview.data.value">
      <v-alert v-if="outdated" type="warning" variant="tonal">
        现有的 {{ edited }} 处修改是在 {{ outdated.edited_version }} 上做的，当前版本是 {{ outdated.version }}。新版本的默认内容可能已经变化，请逐个对照检查。
      </v-alert>
      <v-checkbox :model-value="overview.data.value.keep_on_update" :disabled="setting.busy.value" hide-details
        label="更新 LenBot 后保留这些修改"
        @update:model-value="keep" />
      <ErrorNote v-if="setting.error.value" title="没有保存成功" :error="setting.error.value" />
      <v-select v-model="selected" :items="items" label="提示词文件" :disabled="dirty" />
      <ErrorNote v-if="load.error.value" title="读取失败" :error="load.error.value" />
      <template v-if="detail">
        <div v-if="detail.placeholders.length" class="placeholders">
          <span class="muted">必须保留的占位符</span>
          <v-chip v-for="item in detail.placeholders" :key="item" size="small" label>${{ item }}</v-chip>
        </div>
        <v-textarea v-model="draft" class="prompt-editor" rows="18" auto-grow spellcheck="false" hide-details />
        <v-alert v-if="save.error.value?.status === 422" type="error" role="alert">{{ save.error.value.message }}</v-alert>
        <ErrorNote v-else-if="save.error.value" title="没有保存成功" :error="save.error.value" />
        <div class="actions">
          <v-btn color="primary" :disabled="!dirty" :loading="save.busy.value" @click="store(draft)">保存</v-btn>
          <v-btn v-if="dirty" variant="text" @click="draft = detail.text">撤销修改</v-btn>
          <v-btn v-if="detail.edited && !dirty" variant="outlined" :disabled="save.busy.value" @click="restore">恢复默认</v-btn>
        </div>
      </template>
    </template>
  </Panel>
</template>

<style scoped>
.placeholders{display:flex;flex-wrap:wrap;align-items:center;gap:var(--sp-2)}
.prompt-editor :deep(textarea){font-family:var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);font-size:13px;line-height:1.6}
.actions{display:flex;gap:var(--sp-2);flex-wrap:wrap}
</style>
