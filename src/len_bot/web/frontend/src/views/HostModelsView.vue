<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api.js'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'

const snapshot = ref(null), loading = ref(false), saving = ref(false)
const readError = ref(''), saveError = ref(''), localError = ref(''), savedNotice = ref('')
const providers = ref([]), roles = ref(null), prices = ref([]), visionEnabled = ref(false), memoryEnabled = ref(false)
const beginRead = useRequestGuard(), beginSave = useRequestGuard()
const providerOptions = computed(() => providers.value.filter(row => row.alias).map(row => row.alias))

function copy(value) { return JSON.parse(JSON.stringify(value)) }
function numberValue(value) { return value === '' ? '' : Number(value) }
function binding(value) {
  return { ...copy(value), reasoning_effort: value.reasoning_effort ?? '' }
}
function adopt(result) {
  snapshot.value = result
  const models = result.saved.models
  providers.value = Object.entries(models.providers).map(([alias, value]) => ({
    alias, api: value.api, base_url: value.base_url, api_key: '', api_key_configured: value.api_key_configured,
  }))
  roles.value = { mind: binding(models.roles.mind), voice: binding(models.roles.voice),
    vision: models.roles.vision === null ? null : binding(models.roles.vision),
    memory: models.roles.memory === null ? null : binding(models.roles.memory) }
  visionEnabled.value = models.roles.vision !== null
  memoryEnabled.value = models.roles.memory !== null
  prices.value = Object.entries(models.prices).flatMap(([provider, entries]) =>
    Object.entries(entries).map(([model, value]) => ({ provider, model, ...copy(value) })))
  localError.value = ''
  savedNotice.value = ''
}
function roleBody() {
  const output = copy(roles.value)
  for (const name of ['mind','voice','vision','memory']) {
    if (output[name] === null) continue
    output[name].reasoning_effort = output[name].reasoning_effort === '' ? null : output[name].reasoning_effort
  }
  return output
}
function body() {
  return {
    providers: Object.fromEntries(providers.value.map(row => [row.alias, {
      api: row.api, base_url: row.base_url, api_key: row.api_key === '' ? null : row.api_key,
    }])),
    roles: roleBody(),
    prices: prices.value.reduce((all, row) => {
      if (!Object.hasOwn(all, row.provider)) all[row.provider] = Object.create(null)
      all[row.provider][row.model] = {
        currency: row.currency, input: row.input, output: row.output, cache_read: row.cache_read,
      }
      return all
    }, Object.create(null)),
  }
}
const dirty = computed(() => {
  if (!snapshot.value) return false
  const saved = snapshot.value.saved.models
  const original = {
    providers: Object.fromEntries(Object.entries(saved.providers).map(([alias, value]) => [alias, {
      api: value.api, base_url: value.base_url, api_key: null,
    }])),
    roles: saved.roles,
    prices: saved.prices,
  }
  const duplicateProviders = providers.value.length !== new Set(providers.value.map(row => row.alias)).size
  const duplicatePrices = prices.value.length !== new Set(prices.value.map(row => `${row.provider}\u0000${row.model}`)).size
  return duplicateProviders || duplicatePrices || JSON.stringify(body()) !== JSON.stringify(original)
})
useUnsavedChanges(dirty)

async function read(confirmDiscard = true) {
  if (confirmDiscard && dirty.value && !window.confirm('放弃未保存的模型配置草稿，重读根配置？')) return
  const fresh = beginRead()
  loading.value = true
  try {
    const value = await api('/api/host/settings')
    if (!fresh()) return
    adopt(value)
    readError.value = ''
    saveError.value = ''
  } catch (error) {
    if (fresh()) readError.value = error.message
  } finally {
    if (fresh()) loading.value = false
  }
}
function addProvider() {
  providers.value.push({ alias: '', api: 'openai-chat', base_url: '', api_key: '', api_key_configured: false })
}
function enableVision(value) {
  visionEnabled.value = value
  roles.value.vision = value ? {
    provider: '', model: '', context_window_tokens: '', temperature: 0.6,
    max_output_tokens: '', timeout_seconds: 60, reasoning_effort: '',
  } : null
}
function enableMemory(value) {
  memoryEnabled.value = value
  roles.value.memory = value ? {
    provider: '', model: '', context_window_tokens: '', temperature: 0.6,
    max_output_tokens: '', timeout_seconds: 60, reasoning_effort: '',
  } : null
}
function draftProblem() {
  const aliases = providers.value.map(row => row.alias)
  if (aliases.length !== new Set(aliases).size) return '提供方别名重复；保存前请明确保留哪一项。'
  if (aliases.some(alias => !alias.trim())) return '提供方别名不能为空。'
  const keys = prices.value.map(row => `${row.provider}\u0000${row.model}`)
  if (keys.length !== new Set(keys).size) return '同一提供方与模型的价格重复；保存前请删除重复项。'
  if (prices.value.some(row => !row.provider.trim() || !row.model.trim())) return '价格项须给出提供方别名和精确模型名。'
  return ''
}
async function save() {
  if (!snapshot.value || !dirty.value || loading.value || saving.value) return
  localError.value = draftProblem()
  if (localError.value) return
  const fresh = beginSave()
  const payload = body()
  saving.value = true
  saveError.value = ''
  savedNotice.value = ''
  try {
    const result = await api('/api/host/settings/models', { method: 'PUT', body: JSON.stringify(payload) })
    if (!fresh()) return
    adopt(result)
    savedNotice.value = result.restart_required.models
      ? '模型配置已保存到根文件；正在运行的绑定和密钥不变，重启宿主后生效。'
      : '模型配置已保存到根文件；与当前运行值一致。'
  } catch (error) {
    if (fresh()) saveError.value = error.status >= 400 && error.status < 500
      ? `保存未被接受：${error.message}`
      : `保存结果未确认：${error.message} 草稿已保留；请重读根配置核对，不会自动重试。`
  } finally {
    if (fresh()) saving.value = false
  }
}
onMounted(() => read(false))
</script>

<template>
  <div class="page-stack host-models">
    <header class="page-intro"><div><p class="eyebrow">独立多场景宿主</p><h1>模型配置</h1>
      <p class="muted">编辑根配置中的提供方、用途绑定和三档价格。保存只写文件，不更换运行中模型；这里不提供测试连接。</p></div>
      <v-btn variant="outlined" :loading="loading" :disabled="saving" @click="read()">重读保存值</v-btn></header>
    <v-alert v-if="readError" type="error" variant="tonal" role="alert"
      :title="snapshot?'读取失败 · 保留上次草稿':'读取模型配置失败'">{{ readError }}</v-alert>
    <v-alert v-if="saveError" type="error" variant="tonal" role="alert">{{ saveError }}</v-alert>
    <v-alert v-if="localError" type="warning" variant="tonal" role="alert">{{ localError }}</v-alert>
    <v-alert v-if="savedNotice && !dirty" type="success" variant="tonal" role="status">{{ savedNotice }}</v-alert>
    <div v-if="loading && !snapshot" class="surface empty-state" role="status">正在读取已保存和运行中的模型配置…</div>
    <template v-if="snapshot">
      <section class="surface" aria-labelledby="model-running-title">
        <div class="section-heading"><h2 id="model-running-title">当前运行绑定</h2>
          <v-chip variant="tonal" :color="snapshot.restart_required.models?'warning':'info'">
            {{ snapshot.restart_required.models?'保存值待重启':'保存值与运行值一致' }}
          </v-chip></div>
        <dl class="facts">
          <div v-for="name in ['mind','voice','vision','memory']" :key="name"><dt>{{ ({mind:'大脑',voice:'表达器',vision:'视觉',memory:'记忆抽取'})[name] }}</dt>
            <dd>{{ snapshot.running.models.roles[name] === null ? '未配置' : `${snapshot.running.models.roles[name].provider} / ${snapshot.running.models.roles[name].model}` }}</dd></div>
        </dl>
        <p class="muted">运行中提供方：{{ Object.keys(snapshot.running.models.providers).join('、') }}。密钥只显示是否已填写，不回显原值。</p>
        <details class="runtime-detail"><summary>查看运行中的提供方、完整绑定与配置价格</summary>
          <h3>提供方</h3><dl class="facts"><div v-for="(provider,alias) in snapshot.running.models.providers" :key="alias">
            <dt>{{ alias }} · {{ provider.api }}</dt><dd>{{ provider.base_url }} · {{ provider.api_key_configured?'已配置密钥':'未配置密钥' }}</dd></div></dl>
          <h3>用途绑定</h3><dl class="facts"><div v-for="name in ['mind','voice','vision','memory']" :key="name"><dt>{{ name }}</dt>
            <dd v-if="snapshot.running.models.roles[name]">{{ snapshot.running.models.roles[name].provider }} / {{ snapshot.running.models.roles[name].model }} · 窗口 {{ snapshot.running.models.roles[name].context_window_tokens }} · 输出 {{ snapshot.running.models.roles[name].max_output_tokens }} · 温度 {{ snapshot.running.models.roles[name].temperature }} · 超时 {{ snapshot.running.models.roles[name].timeout_seconds }} 秒 · 思考强度 {{ snapshot.running.models.roles[name].reasoning_effort ?? '未设置' }}</dd>
            <dd v-else>未配置</dd></div></dl>
          <h3>配置价格</h3><dl class="facts"><template v-for="(entries,provider) in snapshot.running.models.prices" :key="provider">
            <div v-for="(price,model) in entries" :key="model"><dt>{{ provider }} / {{ model }} · {{ price.currency }}</dt>
              <dd>每百万 token：输入 {{ price.input }} · 缓存读取 {{ price.cache_read }} · 输出 {{ price.output }}</dd></div></template></dl>
          <p class="muted">价格是配置估算依据，不是供应商账单；没有记录价格时仍为未知。</p>
        </details>
        <p v-if="dirty" class="dirty-note" role="status">模型草稿有未保存修改。</p>
      </section>

      <form @submit.prevent="save" class="page-stack">
        <fieldset :disabled="saving || loading" class="surface editor-section">
          <legend>提供方 · 根配置保存值</legend>
          <p class="muted">已有密钥留空即保留；只有输入非空新值才替换。新提供方必须填写密钥。不会把旧密钥显示在页面。</p>
          <div v-for="(row,index) in providers" :key="index" class="entry-card provider-grid">
            <v-text-field v-model="row.alias" label="提供方别名" :readonly="row.api_key_configured" hide-details="auto" />
            <v-select v-model="row.api" label="协议" :items="['openai-chat']" hide-details="auto" />
            <v-text-field v-model="row.base_url" label="服务地址" hide-details="auto" />
            <v-text-field v-model="row.api_key" label="新密钥（留空保留已有）" type="password" autocomplete="new-password" hide-details="auto" />
            <p class="muted">{{ row.api_key_configured?'已保存密钥；此页不显示原文':'尚无保存密钥' }}</p>
            <v-btn variant="outlined" @click="providers.splice(index,1)">移除提供方</v-btn>
          </div>
          <v-btn variant="outlined" @click="addProvider">添加提供方</v-btn>
        </fieldset>

        <fieldset :disabled="saving || loading" class="surface editor-section">
          <legend>用途绑定 · 根配置保存值</legend>
          <div v-for="name in ['mind','voice','vision','memory']" :key="name" class="entry-card">
            <div class="binding-title"><h3>{{ ({mind:'大脑',voice:'表达器',vision:'视觉',memory:'记忆抽取'})[name] }}</h3>
              <v-switch v-if="name==='vision'" :model-value="visionEnabled" label="启用视觉绑定"
                hide-details @update:model-value="enableVision" />
              <v-switch v-else-if="name==='memory'" :model-value="memoryEnabled" label="启用记忆抽取绑定"
                hide-details @update:model-value="enableMemory" /></div>
            <div v-if="roles[name] !== null" class="form-grid">
              <v-select v-model="roles[name].provider" label="提供方" :items="providerOptions" hide-details="auto" />
              <v-text-field v-model="roles[name].model" label="精确模型名" hide-details="auto" />
              <v-text-field :model-value="roles[name].context_window_tokens" type="number" label="上下文窗口 token"
                hide-details="auto" @update:model-value="value=>roles[name].context_window_tokens=numberValue(value)" />
              <v-text-field :model-value="roles[name].max_output_tokens" type="number" label="最大输出 token"
                hide-details="auto" @update:model-value="value=>roles[name].max_output_tokens=numberValue(value)" />
              <v-text-field :model-value="roles[name].temperature" type="number" step="0.01" label="温度"
                hide-details="auto" @update:model-value="value=>roles[name].temperature=numberValue(value)" />
              <v-text-field :model-value="roles[name].timeout_seconds" type="number" label="请求超时（秒）"
                hide-details="auto" @update:model-value="value=>roles[name].timeout_seconds=numberValue(value)" />
              <v-text-field v-model="roles[name].reasoning_effort" label="思考强度（可留空）" hide-details="auto" />
            </div>
            <p v-else class="muted">{{ name==='vision'?'未配置视觉模型；看图工具不会注册。':'未配置记忆抽取模型；本地自动抽取不能运行。' }}</p>
          </div>
        </fieldset>

        <fieldset :disabled="saving || loading" class="surface editor-section">
          <legend>每百万 token 的配置价格</legend>
          <p class="muted">仅为按配置估算，不是供应商账单；没有价格或缺少必要用量时费用保持未知，不按零计算。</p>
          <div v-for="(row,index) in prices" :key="index" class="entry-card price-grid">
            <v-select v-model="row.provider" label="提供方" :items="providerOptions" hide-details="auto" />
            <v-text-field v-model="row.model" label="精确模型名" hide-details="auto" />
            <v-text-field v-model="row.currency" label="币种（三位大写）" hide-details="auto" />
            <v-text-field v-model="row.input" label="普通输入" inputmode="decimal" hide-details="auto" />
            <v-text-field v-model="row.cache_read" label="缓存读取" inputmode="decimal" hide-details="auto" />
            <v-text-field v-model="row.output" label="输出" inputmode="decimal" hide-details="auto" />
            <v-btn variant="outlined" @click="prices.splice(index,1)">移除价格</v-btn>
          </div>
          <p v-if="!prices.length" class="muted">尚未配置价格；费用仍为未知。</p>
          <v-btn variant="outlined" @click="prices.push({provider:'',model:'',currency:'',input:'',output:'',cache_read:''})">添加价格</v-btn>
        </fieldset>
        <div class="form-actions"><v-btn type="submit" color="primary" :loading="saving" :disabled="!dirty || loading">保存模型配置</v-btn>
          <span class="muted">大脑协议、地址或模型变更须先停机完成离线会话转换；运行中保存会被后端拒绝。</span></div>
      </form>
    </template>
  </div>
</template>

<style scoped>
.host-models{max-width:1280px;margin-inline:auto}
.page-intro,.section-heading,.binding-title{display:flex;justify-content:space-between;align-items:flex-start;gap:14px;flex-wrap:wrap}
.page-intro>div{min-width:0;flex:1 1 460px}
.page-intro h1{margin:0 0 10px}
.eyebrow{font-size:12px;letter-spacing:.08em;color:var(--primary);font-weight:700;margin:0 0 5px}
.surface,.entry-card{min-width:0}
.surface h2{font-size:18px;margin:0 0 14px}
.facts{display:flex;gap:16px;flex-wrap:wrap;margin:0 0 14px}
.facts>div{min-width:160px;overflow-wrap:anywhere}
.facts dt{font-size:12px;color:var(--muted)}
.facts dd{margin:0;font-weight:600}
.dirty-note{border-left:3px solid var(--primary);background:var(--selected-bg);padding:8px 12px}
.editor-section{border:1px solid var(--line)}
.editor-section legend{font-size:18px;font-weight:650;padding:0 6px}
.entry-card{border:1px solid var(--line);border-radius:10px;padding:14px;margin:12px 0;overflow-wrap:anywhere}
.entry-card h3{font-size:16px;margin:0}
.entry-card>p{margin:0}
.runtime-detail{border-top:1px solid var(--line);padding:12px 0;margin-bottom:12px}.runtime-detail summary{cursor:pointer;min-height:44px;font-weight:700}.runtime-detail h3{font-size:15px;margin:12px 0 8px}.runtime-detail dd{overflow-wrap:anywhere}
.provider-grid,.price-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,205px),1fr));gap:12px;align-items:start}
.provider-grid>p{align-self:center}
.form-grid{margin-top:12px}
.host-models :deep(.v-btn){min-height:44px}
.host-models :deep(.v-alert),.host-models .muted{overflow-wrap:anywhere}
@media(max-width:600px){.page-intro{display:grid}.page-intro>.v-btn{width:100%}.entry-card{padding:12px}}
</style>
