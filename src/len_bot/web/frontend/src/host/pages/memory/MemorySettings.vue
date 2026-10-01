<script setup>
import { computed, ref, watch } from 'vue'
import { api, sceneName } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { notify, readPendingRestart } from '../../store.js'
import { clone, numberOrBlank, numberOrNull, same } from '../../forms.js'
import SettingSection from '../../components/SettingSection.vue'
import ErrorNote from '../../components/ErrorNote.vue'
import AdvancedFields from '../../components/AdvancedFields.vue'

const emit = defineEmits(['dirty'])
const settings = useResource(() => api('/api/host/settings'))
const save = useAction()
const draft = ref(null), identities = ref([])
const saved = computed(() => settings.data.value?.saved)
const providers = computed(() => Object.keys(saved.value?.models.providers || {}))
const noMemoryModel = computed(() => saved.value && saved.value.models.roles.memory === null)

// OpenViking keys are never sent back; a blank key keeps the saved one.
const identityRows = (value, scenes) => scenes.map(scene => ({ scene, user_id: value?.[scene]?.user_id ?? '',
  api_key: '', configured: value?.[scene]?.api_key_configured ?? false }))
function payload(value, rows) {
  if (value === null) return null
  const common = { backend: value.backend, auto_recall: value.auto_recall, recall_budget_chars: value.recall_budget_chars,
    recall_limit: value.recall_limit, ingest: clone(value.ingest), summaries: value.summaries }
  if (value.backend === 'local') return { ...common, local: clone(value.local) }
  const { base_url, account_id, timeout_seconds, public_root, memory_policy } = value.openviking
  return { ...common, openviking: { base_url, account_id, timeout_seconds, public_root, memory_policy: clone(memory_policy),
    scenes: Object.fromEntries([...rows].sort((a, b) => a.scene.localeCompare(b.scene))
      .map(item => [item.scene, { user_id: item.user_id, api_key: item.api_key === '' ? null : item.api_key }])) } }
}
function adopt() {
  draft.value = clone(saved.value.memory)
  identities.value = draft.value?.backend === 'openviking'
    ? identityRows(saved.value.memory.openviking.scenes, Object.keys(saved.value.scenes)) : []
}
watch(() => settings.data.value, value => { if (value) adopt() })
const savedPayload = computed(() => saved.value?.memory?.backend === 'openviking'
  ? payload(saved.value.memory, identityRows(saved.value.memory.openviking.scenes, Object.keys(saved.value.scenes)))
  : saved.value ? payload(saved.value.memory, []) : null)
const dirty = computed(() => Boolean(saved.value) && (!same(payload(draft.value, identities.value), savedPayload.value)
  || identities.value.some(item => item.api_key !== '')))
watch(dirty, value => emit('dirty', value), { immediate: true })

const backend = computed({
  get: () => draft.value?.backend ?? 'none',
  set: value => {
    const base = { auto_recall: true, recall_budget_chars: 1500, recall_limit: 5, ingest: null, summaries: false }
    if (value === 'none') { draft.value = null; identities.value = [] }
    else if (value === saved.value.memory?.backend) adopt()
    else if (value === 'local') { draft.value = { ...base, backend: 'local', local: { directory: 'data/memory', embedding: null } }; identities.value = [] }
    else {
      draft.value = { ...base, backend: 'openviking', openviking: { base_url: '', account_id: '', timeout_seconds: 20, public_root: null, memory_policy: null } }
      identities.value = identityRows(null, Object.keys(saved.value.scenes))
    }
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
const policyOn = computed({
  get: () => draft.value?.openviking?.memory_policy != null,
  set: on => { draft.value.openviking.memory_policy = on ? { self: { enabled: true }, peer: { enabled: true }, working_memory: { enabled: false }, memory_types: null } : null },
})
const ingestFields = [['idle_seconds', '群里安静多久后整理（秒）'], ['min_messages', '至少攒多少条消息'], ['max_age_seconds', '最多等多久就整理（秒）'],
  ['batch_size', '每次最多整理几条消息'], ['max_steps', '每次最多调用模型几步'], ['timeout_seconds', '每次整理超时（秒）']]
const problem = computed(() => {
  if (!draft.value) return ''
  if (draft.value.backend === 'local' && (draft.value.ingest || draft.value.summaries) && noMemoryModel.value) return '后台整理和摘要需要先在模型页设置记忆整理模型'
  return ''
})

async function submit() {
  const result = await save.run(() => api('/api/host/settings/memory', { method: 'PUT', body: JSON.stringify({ memory: payload(draft.value, identities.value) }) }))
  if (result) {
    settings.data.value = result
    readPendingRestart()
    notify('已保存，重启后生效')
  }
}
</script>

<template>
  <ErrorNote v-if="settings.error.value" title="读取记忆设置失败" :error="settings.error.value" />
  <SettingSection v-if="saved" title="记忆设置" :dirty="dirty" :problem="problem" :saving="save.busy.value" :error="save.error.value" @save="submit">
    <v-select v-model="backend" label="记忆保存在哪里"
      :items="[{ title: '不开启记忆', value: 'none' }, { title: '保存在本机', value: 'local' }, { title: 'OpenViking 记忆服务', value: 'openviking' }]" />
    <template v-if="draft">
      <v-switch v-model="draft.auto_recall" color="primary" label="聊天时自动想起相关的记忆" hide-details />
      <v-switch v-model="ingestOn" color="primary" label="后台自动整理记忆" hint="攒够一批新消息后自动整理，只处理开启之后的消息" persistent-hint />
      <v-alert v-if="draft.backend === 'local' && ingestOn && noMemoryModel" type="info" variant="tonal" density="compact">
        需要先到 <RouterLink :to="{ name: 'host-models' }">模型</RouterLink> 页设置记忆整理用的模型，整理会产生费用。</v-alert>
      <v-switch v-model="draft.summaries" color="primary" hide-details
        :label="draft.backend === 'local' ? '为每个目录生成摘要，根目录摘要作为本群画像' : '使用记忆服务生成的群概览'" />

      <template v-if="draft.backend === 'local'">
        <v-text-field v-model="draft.local.directory" label="保存目录" hint="在 LenBot 目录里，例如 data/memory" persistent-hint />
      </template>
      <template v-else>
        <div class="form-grid">
          <v-text-field v-model="draft.openviking.base_url" label="服务地址" hint="例如 http://127.0.0.1:1933" persistent-hint />
          <v-text-field v-model="draft.openviking.account_id" label="账户 ID" />
        </div>
        <div v-for="item in identities" :key="item.scene" class="identity">
          <strong>{{ sceneName(item.scene) }}</strong>
          <div class="form-grid">
            <v-text-field v-model="item.user_id" label="用户 ID" />
            <v-text-field v-model="item.api_key" type="password" autocomplete="new-password" label="API Key"
              :placeholder="item.configured ? '已保存，留空不修改' : ''" persistent-placeholder />
          </div>
        </div>
      </template>

      <AdvancedFields>
        <v-text-field :model-value="draft.recall_budget_chars" type="number" label="每次最多想起多少字" hint="100 到 12000" persistent-hint
          @update:model-value="value => draft.recall_budget_chars = numberOrBlank(value)" />
        <v-text-field :model-value="draft.recall_limit" type="number" label="每次最多想起几条" hint="1 到 20" persistent-hint
          @update:model-value="value => draft.recall_limit = numberOrBlank(value)" />
        <template v-if="draft.ingest">
          <v-text-field v-for="[key, label] in ingestFields" :key="key" :model-value="draft.ingest[key]" type="number" :label="label"
            @update:model-value="value => draft.ingest[key] = numberOrBlank(value)" />
        </template>
        <template v-if="draft.backend === 'local'">
          <v-switch v-model="vectorOn" color="primary" label="用向量模型搜索记忆" hint="关闭时按文字搜索" persistent-hint />
          <template v-if="draft.local.embedding">
            <v-select v-model="draft.local.embedding.provider" :items="providers" label="向量模型服务商" />
            <v-text-field v-model="draft.local.embedding.model" label="向量模型名" />
            <v-text-field :model-value="draft.local.embedding.dimensions ?? ''" type="number" label="向量维数" hint="留空用模型默认值" persistent-hint
              @update:model-value="value => draft.local.embedding.dimensions = numberOrNull(value)" />
          </template>
        </template>
        <template v-else>
          <v-text-field :model-value="draft.openviking.timeout_seconds" type="number" label="请求超时（秒）"
            @update:model-value="value => draft.openviking.timeout_seconds = numberOrBlank(value)" />
          <v-text-field :model-value="draft.openviking.public_root ?? ''" label="公共记忆位置" hint="留空不使用公共记忆，例如 viking://resources/public" persistent-hint
            @update:model-value="value => draft.openviking.public_root = value ? value : null" />
          <v-switch v-model="policyOn" color="primary" label="自己指定记忆服务整理哪些内容" hint="关闭时用服务的默认设置" persistent-hint />
          <template v-if="draft.openviking.memory_policy">
            <v-switch v-model="draft.openviking.memory_policy.self.enabled" color="primary" label="整理群里的事" hide-details />
            <v-switch v-model="draft.openviking.memory_policy.peer.enabled" color="primary" label="整理每个人的事" hide-details />
            <v-switch v-model="draft.openviking.memory_policy.working_memory.enabled" color="primary" label="使用工作记忆" hide-details />
            <v-combobox :model-value="draft.openviking.memory_policy.memory_types ?? []" multiple chips closable-chips label="只整理这些类别"
              hint="留空用服务默认的全部类别；类别要先在记忆服务里加载" persistent-hint
              @update:model-value="value => draft.openviking.memory_policy.memory_types = value.length ? value : null" />
          </template>
        </template>
      </AdvancedFields>
    </template>
  </SettingSection>
</template>

<style scoped>
.form-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,240px),1fr));gap:16px;align-items:start}
.identity{display:grid;gap:8px}
</style>
