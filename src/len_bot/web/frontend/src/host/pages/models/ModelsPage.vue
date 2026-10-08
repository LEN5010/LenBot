<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { mdiPlus } from '@mdi/js'
import { api, sceneName } from '../../../api.js'
import { confirm } from '../../../composables/useConfirm.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { useUnsavedChanges } from '../../../composables/useUnsavedChanges.js'
import { notify, readPendingRestart } from '../../store.js'
import { callRoleLabel } from '../../labels.js'
import { clone, numberOrBlank, same } from '../../forms.js'
import HostPage from '../../ui/HostPage.vue'
import PageTabs from '../../ui/PageTabs.vue'
import Panel from '../../ui/Panel.vue'
import ResourceState from '../../ui/ResourceState.vue'
import MasterDetail from '../../ui/MasterDetail.vue'
import ObjectList from '../../ui/ObjectList.vue'
import ObjectRow from '../../ui/ObjectRow.vue'
import StatusBadge from '../../ui/StatusBadge.vue'
import RowEditor from '../../ui/RowEditor.vue'
import ErrorNote from '../../ui/ErrorNote.vue'
import AdvancedFields from '../../ui/AdvancedFields.vue'
import SaveBar from '../../ui/SaveBar.vue'
import DevOnly from '../../ui/DevOnly.vue'
import LimitsSection from './LimitsSection.vue'
import ProviderTools from './ProviderTools.vue'
import EmbeddingBindingEditor from './EmbeddingBindingEditor.vue'
import { credentialBinding, modelChoices, normalizedUrl, providerRows } from '../../providerModels.js'

const roles = [
  ['mind', '聊天', '', true],
  ['vision', '看图', '', false],
  ['memory', '本地记忆整理', '', false],
  ['learner', '学习', '', false],
  ['worker', '任务', '', false],
]
const protocols = useResource(() => api('/api/host/models/protocols'))
const providerApis = computed(() => (protocols.data.value?.protocols || []).map(row => ({ title: row.title, value: row.api })))
const catalog = ref({})
const protocol = api => protocols.data.value?.protocols.find(row => row.api === api)
const providerFor = id => draft.value.providers.find(row => row.id === id)
const providerChoices = role => draft.value.providers.filter(row => protocol(row.api)?.roles.includes(role)).map(row => ({ title: row.alias, value: row.id }))
const vectorProviders = computed(() => draft.value?.providers.filter(row => protocol(row.api)?.roles.includes('embedding')) || [])
function normalizeProtocol(binding, api) {
  if (api !== 'openai-chat') {
    binding.history_policy = 'native'
    binding.output_token_field = 'max_completion_tokens'
  }
  if (!['anthropic', 'gemini'].includes(api)) binding.thinking_budget_tokens = null
}
function chooseRoleProvider(name, id) {
  const binding = draft.value.roles[name]
  binding.provider = id
  normalizeProtocol(binding, providerFor(id).api)
}
function changeProtocol(row, api) {
  const previous = protocol(row.api)
  if (!row.base_url || row.base_url === previous?.base_url) row.base_url = protocol(api)?.base_url || ''
  row.api = api
  for (const binding of Object.values(draft.value.roles)) {
    if (binding?.provider === row.id && Object.hasOwn(binding, 'history_policy')) normalizeProtocol(binding, api)
  }
}
const route = useRoute(), router = useRouter()
const tabs = [['providers', '服务商'], ['roles', '用途'], ['vectors', '向量模型'], ['usage', '用量与上限']]
const tab = computed(() => tabs.some(([key]) => key === route.query.tab) ? route.query.tab : 'providers')
const selected = computed(() => typeof route.query.item === 'string' && draft.value?.providers[Number(route.query.item)] ? Number(route.query.item) : null)
const select = index => router.push({ query: { ...route.query, item: index === null ? undefined : String(index) } })
const settings = useResource(() => api('/api/host/settings'))
const draft = ref(null)
const save = useAction()

function fromSaved(snapshot) {
  return {
    providers: providerRows(snapshot.models),
    roles: clone(snapshot.models.roles),
    memory_embedding: clone(snapshot.memory?.local.embedding ?? null),
    learning_embeddings: Object.fromEntries(Object.entries(snapshot.scenes).filter(([, scene]) => scene.learning !== null)
      .map(([id, scene]) => [id, clone(scene.learning.embedding)])),
  }
}
function body(value) {
  const binding = item => item === null ? null : { ...item, provider: value.providers.find(row => row.id === item.provider)?.alias || item.provider }
  return {
    providers: Object.fromEntries(value.providers.map(row => [row.alias, { previous_alias: row.originalAlias,
      api: row.api, base_url: normalizedUrl(row.base_url), api_key: row.api_key || null, proxy: normalizedUrl(row.proxy) || null }])),
    roles: Object.fromEntries(Object.entries(value.roles).map(([key, item]) => [key, binding(item)])),
    memory_embedding: binding(value.memory_embedding),
    learning_embeddings: Object.fromEntries(Object.entries(value.learning_embeddings).map(([scene, item]) => [scene, binding(item)])),
  }
}
watch(() => draft.value?.providers.map(row => JSON.stringify([credentialBinding(row), row.api_key])), (rows, old = []) => {
  rows?.forEach((value, index) => {
    const row = draft.value.providers[index]
    row.keyReusable = row.keyConfigured && row.keyBinding === credentialBinding(row)
    if (value !== old[index]) delete catalog.value[row.id]
  })
})
const saved = computed(() => settings.data.value?.saved)
function adopt() { draft.value = fromSaved(saved.value); catalog.value = {} }
watch(saved, value => { if (value && !draft.value) adopt() })
const dirty = computed(() => Boolean(draft.value) && !same(body(draft.value), body(fromSaved(saved.value))))
const limitsDirty = ref(false)
useUnsavedChanges(computed(() => dirty.value || limitsDirty.value), { keep: ['tab', 'item'] })
const providerNames = computed(() => draft.value?.providers.map(row => row.alias).filter(Boolean) || [])
const problem = computed(() => {
  if (!draft.value) return ''
  if (new Set(providerNames.value).size !== draft.value.providers.length) return '服务商名称不能为空，也不能重复'
  if (draft.value.providers.some(row => !row.alias.trim() || row.alias !== row.alias.trim())) return '服务商名称不能为空，首尾不能有空白'
  const unbound = draft.value.providers.find(row => !row.keyReusable && !row.api_key)
  if (unbound) return `请填写 ${unbound.alias} 的 API Key`
  const bindings = [...Object.entries(draft.value.roles).map(([role, value]) => [roleTitles[role], role, value]),
    ['记忆向量', 'embedding', draft.value.memory_embedding],
    ...Object.entries(draft.value.learning_embeddings).map(([scene, value]) => [`${sceneName(scene)} 学习向量`, 'embedding', value])]
  for (const [title, role, value] of bindings) {
    if (value === null) continue
    const row = providerFor(value.provider)
    if (!row) return `${title} 需要重新选择服务商`
    if (!protocol(row.api)?.roles.includes(role)) return `${title} 的服务商协议不支持这个用途，请重新选择`
    if (!value.model?.trim()) return `${title} 需要填写模型名`
  }
  return ''
})

function addProvider() {
  draft.value.providers.push({ id: `draft:${crypto.randomUUID()}`, alias: '', originalAlias: null,
    api: 'openai-chat', base_url: '', api_key: '', proxy: '', keyConfigured: false, keyReusable: false })
  select(draft.value.providers.length - 1)
}
async function removeProvider(index) {
  const row = draft.value.providers[index]
  const users = usedBy(row.id)
  if (users.length && !await confirm({ title: `删除服务商 ${row.alias}？`, text: `${users.join('、')} 还在用它，保存前要改成别的服务商。`, confirmLabel: '删除', danger: true })) return
  draft.value.providers.splice(index, 1)
  select(null)
}
const roleTitles = Object.fromEntries([...roles.map(([name, title]) => [name, title]), ['asr', '语音识别']])
const usedBy = id => [...Object.entries(draft.value.roles).filter(([, value]) => value?.provider === id).map(([name]) => roleTitles[name] || name),
  ...(draft.value.memory_embedding?.provider === id ? ['记忆向量'] : []),
  ...Object.entries(draft.value.learning_embeddings).filter(([, value]) => value?.provider === id).map(([scene]) => `${sceneName(scene)} 学习向量`)]
const apiTitle = value => providerApis.value.find(item => item.value === value)?.title.split(' · ')[0] || value
function toggleRole(name, value) {
  draft.value.roles[name] = value ? { provider: providerChoices(name)[0]?.value || '', model: '', context_window_tokens: 128000,
    temperature: 0.6, max_output_tokens: 1024, timeout_seconds: 60, reasoning_effort: null, history_policy: 'native' } : null
}
function toggleAsr(value) {
  draft.value.roles.asr = value ? { api: 'openai-audio', provider: providerChoices('asr')[0]?.value || '', model: '', timeout_seconds: 60, language: null } : null
}
function chooseProviderModel(row, model) {
  if (row.api === 'openai-embeddings') {
    if (saved.value.memory === null) { notify('先在记忆页开启本地记忆，再配置记忆向量模型'); return }
    draft.value.memory_embedding = { ...draft.value.memory_embedding, provider: row.id, model,
      dimensions: draft.value.memory_embedding?.dimensions ?? null }
  } else if (row.api === 'openai-audio') {
    draft.value.roles.asr = { api: 'openai-audio', provider: row.id, model, timeout_seconds: 60, language: null }
  }
  router.push({ query: { ...route.query, tab: row.api === 'openai-embeddings' ? 'vectors' : 'roles', item: undefined } })
}
async function submit() {
  if (problem.value) return
  const result = await save.run(() => api('/api/host/settings/models', { method: 'PUT', body: JSON.stringify(body(draft.value)) }))
  if (result) { settings.data.value = result; adopt(); readPendingRestart(); notify('已保存') }
}

const period = ref('day')
const usage = useResource(() => api(`/api/host/usage?period=${period.value}`))
watch(period, () => usage.reload())
const count = value => value.toLocaleString('zh-CN')
</script>

<template>
  <HostPage title="模型" :wide="tab === 'providers'">
    <PageTabs :tabs="tabs" :model-value="tab" label="模型设置" />
    <ResourceState :resource="settings" error-title="读取模型设置失败">
      <form v-if="draft" class="stack" @submit.prevent="submit">
        <MasterDetail v-if="tab === 'providers'" :selected="selected !== null" :empty="!draft.providers.length" @back="select(null)">
          <template #list>
            <Panel title="服务商" flush>
              <template #actions><v-btn size="small" variant="outlined" :prepend-icon="mdiPlus" @click="addProvider">添加</v-btn></template>
              <ObjectList class="list">
                <ObjectRow v-for="(row, index) in draft.providers" :key="index" :title="row.alias || '未命名'" clickable :active="selected === index"
                  :subtitle="`${apiTitle(row.api)}${usedBy(row.id).length ? ` · ${usedBy(row.id).join('、')}` : ''}`" @click="select(index)">
                  <template #meta><StatusBadge dot :text="row.keyReusable || row.api_key ? '密钥已填' : '缺密钥'" :tone="row.keyReusable || row.api_key ? 'success' : 'warning'" /></template>
                </ObjectRow>
                <li v-if="!draft.providers.length" class="muted empty">还没有服务商</li>
              </ObjectList>
            </Panel>
          </template>
          <template #placeholder>选择或添加服务商</template>
          <Panel v-if="selected !== null" :title="draft.providers[selected].alias || '新服务商'">
            <template #actions><v-btn variant="text" color="error" size="small" @click="removeProvider(selected)">删除</v-btn></template>
            <div class="form-grid">
              <v-text-field v-model="draft.providers[selected].alias" label="名称" />
              <v-select :model-value="draft.providers[selected].api" :items="providerApis" label="原生协议" @update:model-value="value => changeProtocol(draft.providers[selected], value)" />
            </div>
            <v-text-field v-model="draft.providers[selected].base_url" label="接口地址" placeholder="https://api.example.com/v1" />
            <v-text-field v-model="draft.providers[selected].api_key" type="password" autocomplete="new-password" label="密钥"
              :placeholder="draft.providers[selected].keyReusable ? '已设置，留空保持不变' : ''" persistent-placeholder />
            <v-text-field v-model="draft.providers[selected].proxy" label="网络代理（选填）" placeholder="http://127.0.0.1:7890" />
            <ProviderTools :can-probe="protocol(draft.providers[selected].api)?.roles.includes('mind')" :key="selected" :provider="draft.providers[selected]" :models="catalog[draft.providers[selected].id] || []" @models="value => catalog[draft.providers[selected].id] = value"
              :selection-label="draft.providers[selected].api === 'openai-embeddings' ? '用于记忆向量' : '用于语音识别'"
              @choose-model="value => chooseProviderModel(draft.providers[selected], value)" />
            <ErrorNote v-if="protocols.error.value" title="读取协议说明失败" :error="protocols.error.value" />
          </Panel>
        </MasterDetail>

        <template v-else-if="tab === 'roles'">
          <Panel v-for="[name, title, hint, required] in roles" :key="name" :title="title" :description="hint">
            <template v-if="!required" #actions>
              <v-switch :model-value="draft.roles[name] !== null" :label="draft.roles[name] ? '已启用' : '未启用'" @update:model-value="value => toggleRole(name, value)" />
            </template>
            <template v-if="draft.roles[name]">
              <div class="form-grid">
                <v-select :model-value="draft.roles[name].provider" :items="providerChoices(name)" label="服务商" @update:model-value="value => chooseRoleProvider(name, value)" />
                <v-combobox v-model="draft.roles[name].model" :items="modelChoices(catalog[draft.roles[name].provider] || [])" :return-object="false" label="模型名" />
                <v-text-field :model-value="draft.roles[name].context_window_tokens" type="number" label="上下文长度（token）"
                 
                  @update:model-value="value => draft.roles[name].context_window_tokens = numberOrBlank(value)" />
              </div>
              <ProviderTools v-if="providerFor(draft.roles[name].provider)" :provider="providerFor(draft.roles[name].provider)" :binding="draft.roles[name]" :models="catalog[draft.roles[name].provider] || []"
                @models="value => catalog[draft.roles[name].provider] = value" @choose-model="value => draft.roles[name].model = value" />
              <AdvancedFields>
                <v-select v-if="providerFor(draft.roles[name].provider)?.api === 'openai-chat'" v-model="draft.roles[name].history_policy" label="历史续接方式"
                  :items="[{ title: '原生保留', value: 'native' }, { title: '省去可读思考', value: 'omit-reasoning' }]"
                  />
                <v-text-field :model-value="draft.roles[name].max_output_tokens" type="number" label="最长输出（token）"
                  @update:model-value="value => draft.roles[name].max_output_tokens = numberOrBlank(value)" />
                <v-text-field :model-value="draft.roles[name].temperature" type="number" step="0.1" label="温度"
                  @update:model-value="value => draft.roles[name].temperature = value === '' || value === null ? null : numberOrBlank(value)" />
                <v-text-field :model-value="draft.roles[name].timeout_seconds" type="number" label="超时（秒）"
                  @update:model-value="value => draft.roles[name].timeout_seconds = numberOrBlank(value)" />
                <v-text-field :model-value="draft.roles[name].reasoning_effort ?? ''" label="思考强度" placeholder="low、medium 或 high"
                  @update:model-value="value => draft.roles[name].reasoning_effort = value || null" />
                <v-text-field :model-value="draft.roles[name].thinking_budget_tokens ?? ''" type="number" label="思考额度（token，选填）" @update:model-value="value => draft.roles[name].thinking_budget_tokens = value === '' || value === null ? null : numberOrBlank(value)" />
                <v-select v-if="providerFor(draft.roles[name].provider)?.api === 'openai-chat'" v-model="draft.roles[name].output_token_field" label="输出额度字段" :items="['max_completion_tokens', 'max_tokens']" />
              </AdvancedFields>
            </template>
          </Panel>
          <Panel title="语音识别">
            <template #actions>
              <v-switch :model-value="draft.roles.asr !== null" :label="draft.roles.asr ? '已启用' : '未启用'" @update:model-value="toggleAsr" />
            </template>
            <template v-if="draft.roles.asr">
              <div class="form-grid">
                <v-select v-model="draft.roles.asr.provider" :items="providerChoices('asr')" label="服务商" />
                <v-text-field v-model="draft.roles.asr.model" label="模型名" placeholder="例如 whisper-1" />
                <v-text-field :model-value="draft.roles.asr.language ?? ''" label="语言" placeholder="自动识别"
                  @update:model-value="value => draft.roles.asr.language = value || null" />
              </div>
              <AdvancedFields label="超时">
                <v-text-field :model-value="draft.roles.asr.timeout_seconds" type="number" label="超时（秒）"
                  @update:model-value="value => draft.roles.asr.timeout_seconds = numberOrBlank(value)" />
              </AdvancedFields>
            </template>
          </Panel>
        </template>

        <template v-else-if="tab === 'vectors'">
          <Panel title="记忆向量模型">
            <EmbeddingBindingEditor v-if="saved.memory !== null" v-model="draft.memory_embedding" :providers="vectorProviders" />
            <p v-else>本地记忆未启用</p>
          </Panel>
          <Panel v-for="(_, scene) in draft.learning_embeddings" :key="scene" :title="`${sceneName(scene)} · 学习向量模型`">
            <EmbeddingBindingEditor v-model="draft.learning_embeddings[scene]" :providers="vectorProviders" />
          </Panel>
          <p v-if="!Object.keys(draft.learning_embeddings).length">暂无学习向量配置</p>
        </template>

        <SaveBar :on-save="submit" :dirty="dirty" :saving="save.busy.value" :error="save.error.value" :problem="problem" label="保存模型设置" @discard="adopt" />
      </form>

      <div v-show="tab === 'usage'" class="stack">
        <Panel title="token 用量">
          <template #actions>
            <v-btn-toggle v-model="period" mandatory><v-btn value="day">今天</v-btn><v-btn value="month">本月</v-btn></v-btn-toggle>
          </template>
          <ResourceState :resource="usage" error-title="读取用量失败" v-slot="{ data }">
            <p class="usage-total"><strong>{{ count(data.input + data.output) }}</strong>
              <span class="muted">输入 {{ count(data.input) }}（缓存命中 {{ count(data.cached) }}）· 输出 {{ count(data.output) }} · 共 {{ data.calls }} 次调用<template v-if="data.unknown_calls">，其中 {{ data.unknown_calls }} 次没有报告 token</template></span></p>
            <v-table v-if="data.groups.length" density="compact" class="usage-table">
              <thead><tr><th>群聊</th><th>用途</th><th class="num">次数</th><th class="num">输入</th><th class="num">输出</th></tr></thead>
              <tbody><tr v-for="row in data.groups" :key="`${row.scene}/${row.role}`">
                <td>{{ sceneName(row.scene) }}</td><td>{{ callRoleLabel(row.role) }}</td><td class="num">{{ row.calls }}<span v-if="row.unknown_calls" class="muted">（{{ row.unknown_calls }} 次未报告）</span></td>
                <td class="num">{{ count(row.input) }}</td><td class="num">{{ count(row.output) }}</td></tr></tbody>
            </v-table>
            <DevOnly label="统计范围与原始数据" :json="data" />
          </ResourceState>
        </Panel>
        <LimitsSection v-if="settings.data.value" :snapshot="settings.data.value" @dirty="value => limitsDirty = value" @saved="value => { settings.data.value = value; readPendingRestart(); notify('已保存') }" />
      </div>
    </ResourceState>
  </HostPage>
</template>

<style scoped>
.list{padding:0 var(--sp-2) var(--sp-2)}
.empty{padding:var(--sp-3)}
.usage-total{display:flex;gap:var(--sp-3);align-items:baseline;flex-wrap:wrap;margin:0}
.usage-total strong{font-size:var(--fs-xl)}
.usage-table{border:1px solid var(--line);border-radius:var(--radius)}
.usage-table .num{text-align:right;font-variant-numeric:tabular-nums}
.usage-table th,.usage-table td{white-space:nowrap}
p{margin:0}
</style>
