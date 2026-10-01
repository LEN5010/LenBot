<script setup>
import { computed, ref, watch } from 'vue'
import { api, sceneName } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { useUnsavedChanges } from '../../../composables/useUnsavedChanges.js'
import { notify, readPendingRestart } from '../../store.js'
import { callRoleLabel } from '../../labels.js'
import { clone, numberOrBlank, same } from '../../forms.js'
import HostPage from '../../components/HostPage.vue'
import ErrorNote from '../../components/ErrorNote.vue'
import AdvancedFields from '../../components/AdvancedFields.vue'
import SaveBar from '../../components/SaveBar.vue'
import DevOnly from '../../components/DevOnly.vue'
import LimitsSection from './LimitsSection.vue'

const roles = [
  ['mind', '大脑', '决定说不说、说什么、用哪些工具。运行中不能更换，需要先停止 LenBot', true],
  ['voice', '表达器', '把大脑的意思写成角色口吻的台词', true],
  ['vision', '看图', '看懂群里发的图片', false],
  ['memory', '本地记忆整理', '供本地记忆后端整理与摘要；OpenViking 使用服务自身配置的模型，不使用此绑定', false],
  ['learner', '学习', '学习群里的说话方式、黑话和表情', false],
  ['worker', '任务', '执行群友委托的任务', false],
]
const providerApis = [
  { title: '聊天接口 · openai-chat', value: 'openai-chat' },
  { title: '语音转写接口 · openai-audio', value: 'openai-audio' },
  { title: '向量接口 · openai-embeddings', value: 'openai-embeddings' },
]
const settings = useResource(() => api('/api/host/settings'))
const draft = ref(null)
const save = useAction()

function fromSaved(models) {
  return {
    providers: Object.entries(models.providers).map(([alias, value]) => ({ alias, api: value.api, base_url: value.base_url, api_key: '', saved: value.api_key_configured })),
    roles: clone(models.roles),
    prices: Object.entries(models.prices).flatMap(([provider, entries]) => Object.entries(entries).map(([model, price]) => ({ provider, model, ...price }))),
  }
}
function body(value) {
  return {
    providers: Object.fromEntries(value.providers.map(row => [row.alias, { api: row.api, base_url: row.base_url, api_key: row.api_key || null }])),
    roles: value.roles,
    prices: value.prices.reduce((all, { provider, model, ...price }) => {
      (all[provider] ??= {})[model] = price
      return all
    }, {}),
  }
}
const saved = computed(() => settings.data.value?.saved.models)
function adopt() { draft.value = fromSaved(saved.value) }
watch(saved, value => { if (value && !draft.value) adopt() })
const dirty = computed(() => Boolean(draft.value) && !same(body(draft.value), body(fromSaved(saved.value))))
const limitsDirty = ref(false)
useUnsavedChanges(computed(() => dirty.value || limitsDirty.value))
const providerNames = computed(() => draft.value?.providers.map(row => row.alias).filter(Boolean) || [])
const problem = computed(() => {
  if (!draft.value) return ''
  if (new Set(providerNames.value).size !== draft.value.providers.length) return '服务商名称不能为空，也不能重复'
  if (new Set(draft.value.prices.map(row => `${row.provider}/${row.model}`)).size !== draft.value.prices.length) return '同一个模型的价格填了两次'
  return ''
})

function addProvider() {
  draft.value.providers.push({ alias: '', api: 'openai-chat', base_url: '', api_key: '', saved: false })
}
function toggleRole(name, value) {
  draft.value.roles[name] = value ? { provider: providerNames.value[0] || '', model: '', context_window_tokens: 128000,
    temperature: 0.6, max_output_tokens: 1024, timeout_seconds: 60, reasoning_effort: null } : null
}
function toggleAsr(value) {
  draft.value.roles.asr = value ? { api: 'openai-audio', provider: providerNames.value[0] || '', model: '', timeout_seconds: 60, language: null, price: null } : null
}
function asrPrice(type) {
  draft.value.roles.asr.price = type === 'none' ? null : type === 'duration'
    ? { type: 'duration', currency: 'USD', per_second: '' }
    : { type: 'tokens', currency: 'USD', input_audio: '', input_text: '', output: '' }
}
async function submit() {
  const result = await save.run(() => api('/api/host/settings/models', { method: 'PUT', body: JSON.stringify(body(draft.value)) }))
  if (result) { settings.data.value = result; adopt(); readPendingRestart(); notify('已保存') }
}

const period = ref('day')
const usage = useResource(() => api(`/api/host/usage?period=${period.value}`))
watch(period, () => usage.reload())
const amounts = value => Object.entries(value || {}).map(([currency, amount]) => `${amount} ${currency}`).join(' · ') || '—'
</script>

<template>
  <HostPage title="模型" description="LenBot 用到的模型都在这里设置，修改后重启生效。">
    <ErrorNote v-if="settings.error.value" title="读取模型设置失败" :error="settings.error.value" />
    <form v-if="draft" class="page-stack" @submit.prevent="submit">
      <section class="surface">
        <h2>服务商</h2>
        <p class="muted">按服务实际提供的接口类型选择：聊天、语音转写或向量。只有语音或向量接口的服务不能承担聊天用途；模型名称仍须填写服务实际支持的名称。</p>
        <div v-for="(row, index) in draft.providers" :key="index" class="provider-row">
          <v-text-field v-model="row.alias" label="名称" :readonly="row.saved" hint="自己起的名字，下面选模型时用" persistent-hint />
          <v-select v-model="row.api" :items="providerApis" label="接口类型" />
          <v-text-field v-model="row.base_url" label="接口地址" placeholder="https://api.example.com/v1" />
          <v-text-field v-model="row.api_key" type="password" autocomplete="new-password" label="密钥"
            :placeholder="row.saved ? '已设置，留空保持不变' : ''" />
          <v-btn variant="text" @click="draft.providers.splice(index, 1)">删除</v-btn>
        </div>
        <v-btn variant="outlined" size="small" @click="addProvider">添加服务商</v-btn>
      </section>

      <section class="surface">
        <h2>用途</h2>
        <div v-for="[name, title, hint, required] in roles" :key="name" class="role-card">
          <div class="role-head">
            <div><strong>{{ title }}</strong><p class="muted">{{ hint }}</p></div>
            <v-switch v-if="!required" :model-value="draft.roles[name] !== null" :label="draft.roles[name] ? '已启用' : '未启用'"
              @update:model-value="value => toggleRole(name, value)" />
          </div>
          <template v-if="draft.roles[name]">
            <div class="form-grid">
              <v-select v-model="draft.roles[name].provider" :items="providerNames" label="服务商" />
              <v-text-field v-model="draft.roles[name].model" label="模型名" hint="和服务商文档里的名字完全一致" persistent-hint />
              <v-text-field :model-value="draft.roles[name].context_window_tokens" type="number" label="上下文长度（token）"
                hint="模型一次能读的最大长度，见服务商文档" persistent-hint
                @update:model-value="value => draft.roles[name].context_window_tokens = numberOrBlank(value)" />
            </div>
            <AdvancedFields>
              <v-text-field :model-value="draft.roles[name].max_output_tokens" type="number" label="最长输出（token）"
                @update:model-value="value => draft.roles[name].max_output_tokens = numberOrBlank(value)" />
              <v-text-field :model-value="draft.roles[name].temperature" type="number" step="0.1" label="温度" hint="越高越随机" persistent-hint
                @update:model-value="value => draft.roles[name].temperature = numberOrBlank(value)" />
              <v-text-field :model-value="draft.roles[name].timeout_seconds" type="number" label="超时（秒）"
                @update:model-value="value => draft.roles[name].timeout_seconds = numberOrBlank(value)" />
              <v-text-field :model-value="draft.roles[name].reasoning_effort ?? ''" label="思考强度" hint="支持推理的模型可填 low、medium、high，留空不设置" persistent-hint
                @update:model-value="value => draft.roles[name].reasoning_effort = value || null" />
            </AdvancedFields>
          </template>
        </div>
        <div class="role-card">
          <div class="role-head">
            <div><strong>语音识别</strong><p class="muted">把群里的语音转成文字</p></div>
            <v-switch :model-value="draft.roles.asr !== null" :label="draft.roles.asr ? '已启用' : '未启用'" @update:model-value="toggleAsr" />
          </div>
          <template v-if="draft.roles.asr">
            <div class="form-grid">
              <v-select v-model="draft.roles.asr.provider" :items="providerNames" label="服务商" />
              <v-text-field v-model="draft.roles.asr.model" label="模型名" placeholder="例如 whisper-1" />
              <v-text-field :model-value="draft.roles.asr.language ?? ''" label="语言" hint="例如 zh，留空自动识别" persistent-hint
                @update:model-value="value => draft.roles.asr.language = value || null" />
            </div>
            <AdvancedFields label="超时与价格">
              <v-text-field :model-value="draft.roles.asr.timeout_seconds" type="number" label="超时（秒）"
                @update:model-value="value => draft.roles.asr.timeout_seconds = numberOrBlank(value)" />
              <v-select :model-value="draft.roles.asr.price?.type ?? 'none'" label="计价方式" @update:model-value="asrPrice"
                :items="[{ title: '不填价格', value: 'none' }, { title: '按秒', value: 'duration' }, { title: '按 token', value: 'tokens' }]" />
              <template v-if="draft.roles.asr.price">
                <v-text-field v-model="draft.roles.asr.price.currency" label="币种" />
                <v-text-field v-if="draft.roles.asr.price.type === 'duration'" v-model="draft.roles.asr.price.per_second" label="每秒价格" />
                <template v-else>
                  <v-text-field v-model="draft.roles.asr.price.input_audio" label="每百万音频输入 token" />
                  <v-text-field v-model="draft.roles.asr.price.input_text" label="每百万文字输入 token" />
                  <v-text-field v-model="draft.roles.asr.price.output" label="每百万输出 token" />
                </template>
              </template>
            </AdvancedFields>
          </template>
        </div>
      </section>

      <section class="surface">
        <h2>价格</h2>
        <p class="muted">用来估算花费，按每百万 token 填写。没填价格的模型花费显示为未知。</p>
        <div v-for="(row, index) in draft.prices" :key="index" class="price-row">
          <v-select v-model="row.provider" :items="providerNames" label="服务商" />
          <v-text-field v-model="row.model" label="模型名" />
          <v-text-field v-model="row.currency" label="币种" placeholder="USD" />
          <v-text-field v-model="row.input" label="输入" inputmode="decimal" />
          <v-text-field v-model="row.cache_read" label="缓存命中输入" inputmode="decimal" />
          <v-text-field v-model="row.output" label="输出" inputmode="decimal" />
          <v-btn variant="text" @click="draft.prices.splice(index, 1)">删除</v-btn>
        </div>
        <v-btn variant="outlined" size="small" @click="draft.prices.push({ provider: providerNames[0] || '', model: '', currency: 'USD', input: '', cache_read: '', output: '' })">添加价格</v-btn>
      </section>
      <SaveBar :dirty="dirty" :saving="save.busy.value" :error="save.error.value" :problem="problem" label="保存模型设置" @discard="adopt" />
    </form>

    <section class="surface">
      <div class="usage-head"><h2>花费</h2>
        <v-btn-toggle v-model="period" mandatory density="compact" color="primary"><v-btn value="day">今天</v-btn><v-btn value="month">本月</v-btn></v-btn-toggle></div>
      <ErrorNote v-if="usage.error.value" title="读取花费失败" :error="usage.error.value" />
      <template v-if="usage.data.value">
        <p class="usage-total"><strong>{{ amounts(usage.data.value.known_amounts) }}</strong>
          <span class="muted">共 {{ usage.data.value.calls }} 次调用<template v-if="usage.data.value.unknown_calls">，其中 {{ usage.data.value.unknown_calls }} 次费用未知</template></span></p>
        <table v-if="usage.data.value.groups.length" class="usage-table">
          <thead><tr><th>群聊</th><th>用途</th><th>次数</th><th>花费</th></tr></thead>
          <tbody><tr v-for="row in usage.data.value.groups" :key="`${row.scene}/${row.role}`">
            <td>{{ sceneName(row.scene) }}</td><td>{{ callRoleLabel(row.role) }}</td><td>{{ row.calls }}</td>
            <td>{{ amounts(row.known_amounts) }}<span v-if="row.unknown_calls" class="muted">（{{ row.unknown_calls }} 次未知）</span></td></tr></tbody>
        </table>
        <DevOnly label="统计范围与原始数据"><pre>{{ JSON.stringify(usage.data.value, null, 2) }}</pre></DevOnly>
      </template>
    </section>

    <LimitsSection v-if="settings.data.value" :snapshot="settings.data.value" @dirty="value => limitsDirty = value" @saved="value => { settings.data.value = value; readPendingRestart(); notify('已保存') }" />
  </HostPage>
</template>

<style scoped>
section.surface{display:grid;gap:14px}
section.surface > h2{margin:0 !important}
section.surface > .muted{margin:-8px 0 0 !important}
.provider-row{display:grid;grid-template-columns:minmax(100px,150px) minmax(170px,1fr) minmax(0,2fr) minmax(0,1.5fr) auto;gap:12px;align-items:start}
.price-row{display:grid;grid-template-columns:repeat(6,minmax(0,1fr)) auto;gap:10px;align-items:start}
.role-card{border:1px solid var(--line);border-radius:10px;padding:14px;display:grid;gap:12px}
.role-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}
.role-head p{margin:2px 0 0;font-size:13px}
.role-head .v-switch{flex:none}
.usage-head{display:flex;justify-content:space-between;align-items:center}
.usage-total{display:flex;gap:12px;align-items:baseline;margin:0}
.usage-total strong{font-size:22px}
.usage-table{width:100%;border-collapse:collapse}
.usage-table th,.usage-table td{text-align:left;padding:8px;border-bottom:1px solid var(--line)}
@media(max-width:900px){.provider-row,.price-row{grid-template-columns:1fr 1fr}.provider-row .v-btn,.price-row .v-btn{justify-self:start}}
</style>
