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

const roles = [
  ['mind', '大脑', '决定说不说、说什么、用哪些工具。更换模型前需要先停机', true],
  ['vision', '看图', '看懂群里发的图片', false],
  ['memory', '本地记忆整理', '供本地记忆整理与摘要', false],
  ['learner', '学习', '学习群里的说话方式、黑话和表情', false],
  ['worker', '任务', '执行群友委托的任务', false],
]
const providerApis = [
  { title: '聊天接口 · openai-chat', value: 'openai-chat' },
  { title: '语音转写接口 · openai-audio', value: 'openai-audio' },
  { title: '向量接口 · openai-embeddings', value: 'openai-embeddings' },
]
const route = useRoute(), router = useRouter()
const tabs = [['providers', '服务商'], ['roles', '用途'], ['usage', '用量与上限']]
const tab = computed(() => tabs.some(([key]) => key === route.query.tab) ? route.query.tab : 'providers')
// The open provider is `?item=` (its row number in the draft).
const selected = computed(() => typeof route.query.item === 'string' && draft.value?.providers[Number(route.query.item)] ? Number(route.query.item) : null)
const select = index => router.push({ query: { ...route.query, item: index === null ? undefined : String(index) } })
const settings = useResource(() => api('/api/host/settings'))
const draft = ref(null)
const save = useAction()

function fromSaved(models) {
  return {
    providers: Object.entries(models.providers).map(([alias, value]) => ({ alias, api: value.api, base_url: value.base_url, api_key: '', saved: value.api_key_configured })),
    roles: clone(models.roles),
  }
}
function body(value) {
  return {
    providers: Object.fromEntries(value.providers.map(row => [row.alias, { api: row.api, base_url: row.base_url, api_key: row.api_key || null }])),
    roles: value.roles,
  }
}
const saved = computed(() => settings.data.value?.saved.models)
function adopt() { draft.value = fromSaved(saved.value) }
watch(saved, value => { if (value && !draft.value) adopt() })
const dirty = computed(() => Boolean(draft.value) && !same(body(draft.value), body(fromSaved(saved.value))))
const limitsDirty = ref(false)
useUnsavedChanges(computed(() => dirty.value || limitsDirty.value), { keep: ['tab', 'item'] })
const providerNames = computed(() => draft.value?.providers.map(row => row.alias).filter(Boolean) || [])
const problem = computed(() => {
  if (!draft.value) return ''
  if (new Set(providerNames.value).size !== draft.value.providers.length) return '服务商名称不能为空，也不能重复'
  return ''
})

function addProvider() {
  draft.value.providers.push({ alias: '', api: 'openai-chat', base_url: '', api_key: '', saved: false })
  select(draft.value.providers.length - 1)
}
async function removeProvider(index) {
  const row = draft.value.providers[index]
  const users = usedBy(row.alias)
  if (users.length && !await confirm({ title: `删除服务商 ${row.alias}？`, text: `${users.join('、')} 还在用它，保存前要改成别的服务商。`, confirmLabel: '删除', danger: true })) return
  draft.value.providers.splice(index, 1)
  select(null)
}
const roleTitles = Object.fromEntries([...roles.map(([name, title]) => [name, title]), ['asr', '语音识别']])
const usedBy = alias => alias ? Object.entries(draft.value.roles).filter(([, value]) => value?.provider === alias).map(([name]) => roleTitles[name] || name) : []
const apiTitle = value => providerApis.find(item => item.value === value)?.title.split(' · ')[0] || value
function toggleRole(name, value) {
  draft.value.roles[name] = value ? { provider: providerNames.value[0] || '', model: '', context_window_tokens: 128000,
    temperature: 0.6, max_output_tokens: 1024, timeout_seconds: 60, reasoning_effort: null, history_policy: 'native' } : null
}
function toggleAsr(value) {
  draft.value.roles.asr = value ? { api: 'openai-audio', provider: providerNames.value[0] || '', model: '', timeout_seconds: 60, language: null } : null
}
async function submit() {
  const result = await save.run(() => api('/api/host/settings/models', { method: 'PUT', body: JSON.stringify(body(draft.value)) }))
  if (result) { settings.data.value = result; adopt(); readPendingRestart(); notify('已保存') }
}

const period = ref('day')
const usage = useResource(() => api(`/api/host/usage?period=${period.value}`))
watch(period, () => usage.reload())
const count = value => value.toLocaleString('zh-CN')
</script>

<template>
  <HostPage title="模型" description="LenBot 用到的模型都在这里设置，修改后重启生效。" :wide="tab === 'providers'">
    <PageTabs :tabs="tabs" :model-value="tab" label="模型设置" />
    <ResourceState :resource="settings" error-title="读取模型设置失败">
      <form v-if="draft" class="stack" @submit.prevent="submit">
        <MasterDetail v-if="tab === 'providers'" :selected="selected !== null" @back="select(null)">
          <template #list>
            <Panel title="服务商" flush>
              <template #actions><v-btn size="small" variant="outlined" :prepend-icon="mdiPlus" @click="addProvider">添加</v-btn></template>
              <ObjectList class="list">
                <ObjectRow v-for="(row, index) in draft.providers" :key="index" :title="row.alias || '未命名'" clickable :active="selected === index"
                  :subtitle="`${apiTitle(row.api)}${usedBy(row.alias).length ? ` · ${usedBy(row.alias).join('、')}` : ''}`" @click="select(index)">
                  <template #meta><StatusBadge dot :text="row.saved || row.api_key ? '密钥已填' : '缺密钥'" :tone="row.saved || row.api_key ? 'success' : 'warning'" /></template>
                </ObjectRow>
                <li v-if="!draft.providers.length" class="muted empty">还没有服务商</li>
              </ObjectList>
            </Panel>
          </template>
          <template #placeholder>按服务实际提供的接口类型添加服务商：聊天、语音转写或向量。下一步在用途里选模型。</template>
          <Panel v-if="selected !== null" :title="draft.providers[selected].alias || '新服务商'">
            <template #actions><v-btn variant="text" color="error" size="small" @click="removeProvider(selected)">删除</v-btn></template>
            <div class="form-grid">
              <v-text-field v-model="draft.providers[selected].alias" label="名称" :readonly="draft.providers[selected].saved" hint="自己起的名字，选模型时用；保存后不能改" persistent-hint />
              <v-select v-model="draft.providers[selected].api" :items="providerApis" label="接口类型" hint="只有语音或向量接口的服务不能用于聊天" persistent-hint />
            </div>
            <v-text-field v-model="draft.providers[selected].base_url" label="接口地址" placeholder="https://api.example.com/v1" />
            <v-text-field v-model="draft.providers[selected].api_key" type="password" autocomplete="new-password" label="密钥"
              :placeholder="draft.providers[selected].saved ? '已设置，留空保持不变' : ''" persistent-placeholder />
            <p v-if="usedBy(draft.providers[selected].alias).length" class="muted small">用于：{{ usedBy(draft.providers[selected].alias).join('、') }}</p>
          </Panel>
        </MasterDetail>

        <template v-else-if="tab === 'roles'">
          <Panel v-for="[name, title, hint, required] in roles" :key="name" :title="title" :description="hint">
            <template v-if="!required" #actions>
              <v-switch :model-value="draft.roles[name] !== null" :label="draft.roles[name] ? '已启用' : '未启用'" @update:model-value="value => toggleRole(name, value)" />
            </template>
            <template v-if="draft.roles[name]">
              <div class="form-grid">
                <v-select v-model="draft.roles[name].provider" :items="providerNames" label="服务商" />
                <v-text-field v-model="draft.roles[name].model" label="模型名" hint="和服务商文档里的名字完全一致" persistent-hint />
                <v-text-field :model-value="draft.roles[name].context_window_tokens" type="number" label="上下文长度（token）"
                  hint="模型一次能读的最大长度，见服务商文档" persistent-hint
                  @update:model-value="value => draft.roles[name].context_window_tokens = numberOrBlank(value)" />
              </div>
              <AdvancedFields>
                <v-select v-model="draft.roles[name].history_policy" label="历史续接方式"
                  :items="[{ title: '原生保留', value: 'native' }, { title: '省去可读思考', value: 'omit-reasoning' }]"
                  hint="只有确认路由自己保持签名续接时才选后者" persistent-hint />
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
          </Panel>
          <Panel title="语音识别" description="把群里的语音转成文字">
            <template #actions>
              <v-switch :model-value="draft.roles.asr !== null" :label="draft.roles.asr ? '已启用' : '未启用'" @update:model-value="toggleAsr" />
            </template>
            <template v-if="draft.roles.asr">
              <div class="form-grid">
                <v-select v-model="draft.roles.asr.provider" :items="providerNames" label="服务商" />
                <v-text-field v-model="draft.roles.asr.model" label="模型名" placeholder="例如 whisper-1" />
                <v-text-field :model-value="draft.roles.asr.language ?? ''" label="语言" hint="例如 zh，留空自动识别" persistent-hint
                  @update:model-value="value => draft.roles.asr.language = value || null" />
              </div>
              <AdvancedFields label="超时">
                <v-text-field :model-value="draft.roles.asr.timeout_seconds" type="number" label="超时（秒）"
                  @update:model-value="value => draft.roles.asr.timeout_seconds = numberOrBlank(value)" />
              </AdvancedFields>
            </template>
          </Panel>
        </template>

        <SaveBar :on-save="submit" :dirty="dirty" :saving="save.busy.value" :error="save.error.value" :problem="problem" label="保存模型设置" @discard="adopt" />
      </form>

      <div v-show="tab === 'usage'" class="stack">
        <Panel title="token 用量" description="按模型服务实际报告的数字统计；缓存命中算在输入里。语音转写和向量调用单独列出，不计入每天上限。">
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
