<script setup>
import { computed, ref, watch } from 'vue'
import { api } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { notify, readPendingRestart } from '../../store.js'
import { clone, numberOrBlank, numberOrNull, same } from '../../forms.js'
import SettingSection from '../../ui/SettingSection.vue'
import ResourceState from '../../ui/ResourceState.vue'
import AdvancedFields from '../../ui/AdvancedFields.vue'

const emit = defineEmits(['dirty'])
const settings = useResource(() => api('/api/host/settings'))
const save = useAction()
const draft = ref(null)
const saved = computed(() => settings.data.value?.saved)
const providers = computed(() => Object.keys(saved.value?.models.providers || {}))
const noMemoryModel = computed(() => saved.value && saved.value.models.roles.memory === null)

function adopt() { draft.value = clone(saved.value.memory) }
watch(() => settings.data.value, value => { if (value) adopt() })
const dirty = computed(() => Boolean(saved.value) && !same(draft.value, saved.value.memory))
watch(dirty, value => emit('dirty', value), { immediate: true })

const enabled = computed({
  get: () => draft.value !== null,
  set: on => {
    if (!on) draft.value = null
    else if (saved.value.memory) adopt()
    else draft.value = { backend: 'local', auto_recall: true, recall_budget_chars: 1500,
      recall_limit: 5, ingest: null, summaries: false, local: { directory: 'data/memory', embedding: null } }
  },
})
const ingestOn = computed({
  get: () => draft.value?.ingest !== null,
  set: on => { draft.value.ingest = on ? { idle_seconds: 1800, min_messages: 50, max_age_seconds: 86400, batch_size: 100, max_steps: 8, timeout_seconds: 180 } : null },
})
const vectorOn = computed({
  get: () => draft.value?.local?.embedding != null,
  set: on => { draft.value.local.embedding = on ? { provider: providers.value[0] || '', model: '', dimensions: null } : null },
})
const ingestFields = [['idle_seconds', '群里安静多久后整理（秒）'], ['min_messages', '至少攒多少条消息'], ['max_age_seconds', '最多等多久就整理（秒）'],
  ['batch_size', '每次最多整理几条消息'], ['max_steps', '每次最多调用模型几步'], ['timeout_seconds', '每次整理超时（秒）']]
const problem = computed(() => {
  if (!draft.value) return ''
  if ((draft.value.ingest || draft.value.summaries) && noMemoryModel.value) return '后台整理和摘要需要先在模型页设置记忆整理模型'
  return ''
})

async function submit() {
  const result = await save.run(() => api('/api/host/settings/memory', { method: 'PUT', body: JSON.stringify({ memory: draft.value }) }))
  if (result) {
    settings.data.value = result
    readPendingRestart()
    notify('已保存，重启后生效')
  }
}
</script>

<template>
  <ResourceState :resource="settings" error-title="读取记忆设置失败">
  <SettingSection title="记忆设置" :dirty="dirty" :problem="problem" :saving="save.busy.value" :error="save.error.value" @save="submit">
    <v-switch v-model="enabled" label="开启本地记忆" hide-details />
    <template v-if="draft">
      <v-switch v-model="draft.auto_recall" label="聊天时自动想起相关的记忆" hide-details />
      <v-switch v-model="ingestOn" label="后台自动整理记忆" hint="攒够一批新消息后自动整理，只处理开启之后的消息" persistent-hint />
      <v-alert v-if="ingestOn && noMemoryModel" type="info">
        需要先到 <RouterLink :to="{ name: 'host-models', query: { tab: 'roles' } }">模型</RouterLink> 页设置记忆整理用的模型。</v-alert>
      <v-switch v-model="draft.summaries" hide-details
        label="为每个目录生成摘要，根目录摘要作为本群画像" />
      <v-text-field v-model="draft.local.directory" label="保存目录" hint="在 LenBot 目录里，例如 data/memory" persistent-hint />

      <AdvancedFields>
        <v-text-field :model-value="draft.recall_budget_chars" type="number" label="每次最多想起多少字" hint="100 到 12000" persistent-hint
          @update:model-value="value => draft.recall_budget_chars = numberOrBlank(value)" />
        <v-text-field :model-value="draft.recall_limit" type="number" label="每次最多想起几条" hint="1 到 20" persistent-hint
          @update:model-value="value => draft.recall_limit = numberOrBlank(value)" />
        <template v-if="draft.ingest">
          <v-text-field v-for="[key, label] in ingestFields" :key="key" :model-value="draft.ingest[key]" type="number" :label="label"
            @update:model-value="value => draft.ingest[key] = numberOrBlank(value)" />
        </template>
        <v-switch v-model="vectorOn" label="用向量模型搜索记忆" hint="关闭时按文字搜索" persistent-hint />
        <template v-if="draft.local.embedding">
          <v-select v-model="draft.local.embedding.provider" :items="providers" label="向量模型服务商" />
          <v-text-field v-model="draft.local.embedding.model" label="向量模型名" />
          <v-text-field :model-value="draft.local.embedding.dimensions ?? ''" type="number" label="向量维数" hint="留空用模型默认值" persistent-hint
            @update:model-value="value => draft.local.embedding.dimensions = numberOrNull(value)" />
        </template>
      </AdvancedFields>
    </template>
  </SettingSection>
  </ResourceState>
</template>
