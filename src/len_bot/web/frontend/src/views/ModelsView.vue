<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, fmtTime } from '../api.js'
import PageHeader from '../components/PageHeader.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'

const route = useRoute(), router = useRouter()
const tab = computed(() => route.query.tab === 'providers' ? 'providers' : 'roles')
const data = ref({ providers: [], routing: null }), loading = ref(false), loaded = ref(false), readAt = ref(null)
const error = ref(''), message = ref(''), busy = ref(''), catalogs = ref({}), selectedModels = ref({}), modelOriginal = ref({})
const providerOpen = ref(false), editingProvider = ref(''), providerForm = ref(null), providerOriginal = ref('')
const routesOpen = ref(false), routingForm = ref(null), routingOriginal = ref(''), roleEnabled = ref({})
const testConfirm = ref(null), testResult = ref(null)
const emptyRetrievalProfile = () => ({provider_id:'', model:'', dimension:null, protocol:null})
const retrievalOpen = ref(false), retrievalForm = ref({embedding:emptyRetrievalProfile(), rerank:emptyRetrievalProfile()}), retrievalOriginal = ref('')
const roles = [
  { key: 'conversation', name: '对话', description: '理解原话与图片，选择参与、文字、表情或沉默。' },
  { key: 'work', name: '工作', description: '后台查询、计算和核实，形成带来源的结果。' },
  { key: 'maintenance', name: '维护', description: '增量整理历史与认识，压缩工作上下文和整理方法技能。' },
]
const emptyProfile = () => ({ provider_id: '', model: '', reasoning_effort: '' })
const providerDirty = computed(() => providerOpen.value && JSON.stringify(providerForm.value) !== providerOriginal.value)
const routingSnapshot = () => JSON.stringify({ profiles: routingForm.value, enabled: roleEnabled.value })
const routingDirty = computed(() => routesOpen.value && routingSnapshot() !== routingOriginal.value)
const catalogDirty = computed(() => Object.keys(catalogs.value).some(id => JSON.stringify(selectedModels.value[id]) !== modelOriginal.value[id]))
const retrievalDirty = computed(() => retrievalOpen.value && JSON.stringify(retrievalForm.value) !== retrievalOriginal.value)
useUnsavedChanges(computed(() => providerDirty.value || routingDirty.value || catalogDirty.value || retrievalDirty.value))
let requestId = 0
const providerById = id => data.value.providers.find(provider => provider.id === id)
const inUse = id => roles.some(({ key }) => data.value.routing?.[key]?.provider_id === id)
  || [data.value.retrieval?.embedding, data.value.retrieval?.rerank].some(profile => profile?.provider_id === id)
const choicesFor = id => providerById(id)?.models || []
const canSaveProvider = computed(() => providerForm.value && providerForm.value.id.trim()
  && providerForm.value.base_url.trim() && providerForm.value.api_style
  && Number.isFinite(providerForm.value.timeout_seconds) && providerForm.value.timeout_seconds > 0)
function initialiseRouting() {
  routingForm.value = Object.fromEntries(roles.map(({ key }) => [key,
    data.value.routing?.[key] ? { ...data.value.routing[key] } : emptyProfile()]))
  roleEnabled.value = Object.fromEntries(roles.map(({ key }) => [key, data.value.routing?.[key] != null]))
  routingOriginal.value = routingSnapshot()
}
function editRetrieval() {
  retrievalForm.value = { embedding: data.value.retrieval?.embedding ? {...data.value.retrieval.embedding} : emptyRetrievalProfile(),
    rerank: data.value.retrieval?.rerank ? {...data.value.retrieval.rerank} : emptyRetrievalProfile() }
  retrievalOriginal.value = JSON.stringify(retrievalForm.value); retrievalOpen.value = true
}
function closeRetrieval() { if (busy.value) return; if (retrievalDirty.value && !window.confirm('放弃尚未保存的语义检索配置？')) return; retrievalOpen.value = false }
async function saveRetrieval() {
  if (busy.value || !retrievalForm.value) return
  busy.value='retrieval'; error.value=''; message.value=''
  const profile = (value, rerank=false) => value?.provider_id?.trim() && value?.model?.trim() ? {provider_id:value.provider_id.trim(),model:value.model.trim(),...(value.dimension ? {dimension:Number(value.dimension)} : {}),...(rerank && value.protocol ? {protocol:value.protocol} : {})} : null
  try { const result = await api('/api/models/retrieval',{method:'POST',body:JSON.stringify({embedding:profile(retrievalForm.value.embedding),rerank:profile(retrievalForm.value.rerank,true)})}); retrievalOpen.value=false; message.value=result.message; await load() }
  catch(e){ error.value=e.message } finally { busy.value='' }
}
async function load() {
  const request = ++requestId
  loading.value = true; error.value = ''
  try {
    const result = await api('/api/models/providers')
    if (request !== requestId) return
    data.value = result; loaded.value = true; readAt.value = Date.now()/1000
    if (!routingDirty.value) initialiseRouting()
  } catch (e) { if (request === requestId) error.value = e.message }
  finally { if (request === requestId) loading.value = false }
}
function editProvider(provider = null) {
  editingProvider.value = provider?.id || ''
  providerForm.value = provider ? {
    id: provider.id, base_url: provider.base_url, api_style: provider.api_style, api_key: '',
    enabled: provider.enabled, timeout_seconds: provider.timeout_seconds, models: provider.models.join('\n'),
  } : { id: '', base_url: '', api_style: 'openai', api_key: '', enabled: false, timeout_seconds: null, models: '' }
  providerOriginal.value = JSON.stringify(providerForm.value)
  providerOpen.value = true
}
function closeProvider() {
  if (busy.value) return
  if (providerDirty.value && !window.confirm('放弃尚未保存的供应商修改？')) return
  providerOpen.value = false; providerForm.value = null
}
function editRouting() { if (!routingForm.value) initialiseRouting(); routesOpen.value = true }
function closeRouting() {
  if (busy.value) return
  if (routingDirty.value && !window.confirm('放弃尚未保存的职责配置？')) return
  routesOpen.value = false; initialiseRouting()
}
async function saveProvider() {
  if (busy.value || !canSaveProvider.value) return
  if (!window.confirm('保存此供应商配置？接口和启用状态将用于后续运行，空白密钥保留原值。')) return
  busy.value = 'provider'
  error.value = ''
  message.value = ''
  try {
    const body = { ...providerForm.value,
      models: [...new Set(providerForm.value.models.split('\n').map(model=>model.trim()).filter(Boolean))],
      api_key: providerForm.value.api_key.trim() || null,
    }
    const result = await api('/api/models/providers', { method: 'POST', body: JSON.stringify(body) })
    providerOpen.value = false
    providerForm.value = null
    message.value = result.message
    await load()
  } catch (e) { error.value = e.message }
  finally { busy.value = '' }
}
async function deleteProvider(provider) {
  if (busy.value || !window.confirm(`删除供应商「${provider.id}」及保存的密钥？`)) return
  busy.value = `delete:${provider.id}`; error.value = ''; message.value = ''
  try {
    const result = await api(`/api/models/providers/${encodeURIComponent(provider.id)}`, { method: 'DELETE' })
    delete catalogs.value[provider.id]; delete selectedModels.value[provider.id]; delete modelOriginal.value[provider.id]
    message.value = result.message; await load()
  } catch (e) { error.value = e.message }
  finally { busy.value = '' }
}
async function fetchModels(provider) {
  if (busy.value) return
  busy.value = `catalog:${provider.id}`; error.value = ''
  try {
    const result = await api(`/api/models/providers/${encodeURIComponent(provider.id)}/models`)
    catalogs.value[provider.id] = result.models
    selectedModels.value[provider.id] = [...provider.models]
    modelOriginal.value[provider.id] = JSON.stringify(selectedModels.value[provider.id])
  } catch (e) { error.value = e.message }
  finally { busy.value = '' }
}
async function saveModels(provider) {
  if (busy.value) return
  busy.value = `models:${provider.id}`; error.value = ''
  try {
    const result = await api(`/api/models/providers/${encodeURIComponent(provider.id)}/models`, { method: 'POST', body: JSON.stringify({ models: selectedModels.value[provider.id] }) })
    delete catalogs.value[provider.id]; message.value = result.message; await load()
  } catch (e) { error.value = e.message }
  finally { busy.value = '' }
}
function profile(key) {
  const value = routingForm.value[key]
  return { provider_id: value.provider_id, model: value.model.trim(), reasoning_effort: value.reasoning_effort?.trim() || null }
}
const canSaveRouting = computed(() => routingForm.value && roles.every(({key}) =>
  !roleEnabled.value[key] || routingForm.value[key].provider_id && routingForm.value[key].model?.trim()))
async function saveRouting() {
  if (busy.value || !canSaveRouting.value) return
  if (!window.confirm('保存对话、工作与维护配置？后续运行将使用所选模型；正在进行的运行保留原绑定。')) return
  busy.value = 'routing'
  error.value = ''
  message.value = ''
  try {
    const routing = Object.fromEntries(roles.map(({key})=>[key,roleEnabled.value[key]?profile(key):null]))
    const result = await api('/api/models/routing', { method: 'POST', body: JSON.stringify(routing) })
    routesOpen.value = false
    message.value = result.message
    await load()
  } catch (e) { error.value = e.message }
  finally { busy.value = '' }
}
async function testRoute() {
  if (busy.value || !testConfirm.value) return
  const { name, profile } = testConfirm.value
  busy.value = 'test'; error.value = ''; testResult.value = null
  try { testResult.value = { name, ...await api('/api/models/test', { method: 'POST', body: JSON.stringify(profile) }) }; testConfirm.value = null }
  catch (e) { error.value = e.message }
  finally { busy.value = '' }
}
watch(() => route.name, load, { immediate: true })
</script>
<template>
  <div class="page-stack">
    <PageHeader title="模型设置" description="三个职责显式配置，每次运行固定提供商、模型和推理强度。"><v-btn variant="outlined" :loading="loading" @click="load">刷新</v-btn><v-btn v-if="tab==='roles'" color="primary" :disabled="!loaded" @click="editRouting">编辑职责配置</v-btn><v-btn v-else color="primary" @click="editProvider()">添加供应商</v-btn></PageHeader>
    <v-alert v-if="error" type="error" variant="tonal">{{ error }}<span v-if="readAt"> · 上次读取 {{ fmtTime(readAt) }}</span></v-alert><v-alert v-if="message" type="success" variant="tonal" closable @click:close="message=''">{{ message }}</v-alert>
    <v-card v-if="loaded" class="pa-4 retrieval-card"><div class="role-title"><h2>语义检索</h2><div class="actions"><v-chip size="small" :color="data.retrieval?.embedding ? 'primary' : 'default'">{{ data.retrieval?.embedding ? '已绑定' : '未启用' }}</v-chip><v-btn variant="outlined" size="small" :disabled="!!busy" @click="editRetrieval">编辑</v-btn></div></div><p class="muted">只在 Agent 主动查询认识时使用；索引失败不会回滚已提交认识。Embedding 与 rerank 请求单独计量。</p><p v-if="data.retrieval?.embedding" class="auxiliary">Embedding：{{ data.retrieval.embedding.provider_id }} · {{ data.retrieval.embedding.model }}<span v-if="data.retrieval.embedding.dimension"> · {{ data.retrieval.embedding.dimension }} 维</span></p><p v-if="data.retrieval?.rerank" class="auxiliary">Rerank：{{ data.retrieval.rerank.provider_id }} · {{ data.retrieval.rerank.model }}</p></v-card>
    <v-tabs :model-value="tab" color="primary" @update:model-value="value=>router.push({name:'models',query:{tab:value}})"><v-tab value="roles">职责配置</v-tab><v-tab value="providers">供应商</v-tab></v-tabs>
    <v-progress-linear v-if="loading" indeterminate />
    <template v-if="tab==='roles'">
      <div v-if="loaded" class="role-grid"><v-card v-for="role in roles" :key="role.key" class="pa-5 role-card"><div class="role-title"><h2>{{ role.name }}</h2><v-chip size="small" :color="data.routing?.[role.key] ? 'primary' : 'default'">{{ data.routing?.[role.key] ? '已配置' : '未配置' }}</v-chip></div><p class="muted role-description">{{ role.description }}</p><template v-if="data.routing?.[role.key]"><dl><dt>供应商</dt><dd>{{ data.routing[role.key].provider_id }}</dd><dt>模型</dt><dd>{{ data.routing[role.key].model }}</dd><dt>推理强度</dt><dd>{{ data.routing[role.key].reasoning_effort || '模型默认' }}</dd></dl><v-alert v-if="!providerById(data.routing[role.key].provider_id)?.enabled" type="warning" variant="tonal" density="compact">当前供应商未启用</v-alert><v-btn class="mt-auto" variant="outlined" :disabled="!!busy || !providerById(data.routing[role.key].provider_id)?.enabled" @click="testConfirm={name:role.name,profile:{...data.routing[role.key]}}">主动检查能力</v-btn></template><p v-else class="muted">{{ role.key==='maintenance' ? '维护未配置，历史维护与工作压缩尚未就绪。' : '该职责尚未配置。' }}</p></v-card></div>
      <v-alert type="info" variant="tonal">浏览、刷新和选择模型不会发起模型请求。能力检查需要你主动确认；模型目录中的名称不代表已通过检查。</v-alert>
      <v-card v-if="testResult" class="pa-5"><div class="role-title"><h2>{{ testResult.name }}能力检查</h2><v-chip :color="testResult.success?'success':'error'">{{ testResult.success?'检查通过':'检查失败' }}</v-chip></div><p class="my-3">{{ testResult.model }} · {{ testResult.latency_ms }} 毫秒</p><div class="check-list"><p v-for="(name,key) in {image_reading:'原图识别',forced_tool:'指定工具调用',tool_continuation:'原生工具续接'}" :key="key">{{ name }}：{{ testResult.checks[key] ? '通过' : '未通过或未执行' }}</p></div><v-alert v-if="testResult.error" type="error" variant="tonal" class="mt-3">{{ testResult.error }}</v-alert></v-card>
      <RouterLink :to="{name:'activity',query:{tab:'calls'}}">前往运行记录查看持久调用账与用量</RouterLink>
    </template>
    <template v-else><p class="muted">密钥只在后台保存，编辑时留空保留。获取接口目录不会生成模型回答。</p><v-card v-for="provider in data.providers" :key="provider.id" class="pa-5 provider-card"><div class="provider-heading"><div class="provider-name"><h2>{{ provider.id }}</h2><p class="provider-url muted">{{ provider.base_url }}</p></div><StatusBadge domain="provider" :status="provider.enabled?'enabled':'disabled'" /></div><div class="provider-meta"><span>密钥 {{ provider.api_key_masked || '未设置' }}</span><span>超时 {{ provider.timeout_seconds }} 秒</span><span v-if="inUse(provider.id)">当前职责正在使用</span></div><div class="actions"><v-btn variant="outlined" :disabled="!!busy" @click="editProvider(provider)">编辑接口</v-btn><v-btn color="error" variant="text" :disabled="!!busy || inUse(provider.id)" @click="deleteProvider(provider)">删除</v-btn></div><v-divider class="my-4" /><h3 class="mb-3">常用模型目录</h3><template v-if="catalogs[provider.id]"><v-autocomplete v-model="selectedModels[provider.id]" :items="[...new Set([...catalogs[provider.id],...provider.models])]" label="搜索并选择常用模型" multiple chips closable-chips clearable /><div class="actions"><v-btn color="primary" :loading="busy===`models:${provider.id}`" :disabled="!!busy" @click="saveModels(provider)">保存常用模型</v-btn><v-btn variant="text" :disabled="!!busy" @click="delete catalogs[provider.id]">取消选择</v-btn></div></template><template v-else><div class="model-tags"><v-chip v-for="model in provider.models" :key="model" size="small">{{ model }}</v-chip><span v-if="!provider.models.length" class="muted">尚未保存常用模型</span></div><v-btn class="mt-4" variant="tonal" :loading="busy===`catalog:${provider.id}`" :disabled="!!busy" @click="fetchModels(provider)">获取接口模型目录</v-btn></template></v-card><v-card v-if="loaded&&!error&&!data.providers.length" class="pa-8 text-center muted">还没有供应商，点击“添加供应商”开始配置。</v-card></template>
    <v-dialog :model-value="providerOpen" max-width="650" :persistent="!!busy" @update:model-value="value=>!value&&closeProvider()">
      <v-card>
        <v-card-title class="dialog-title">{{ editingProvider?'编辑供应商':'添加供应商' }}<v-btn variant="text" :disabled="!!busy" @click="closeProvider">关闭</v-btn></v-card-title>
        <v-card-text>
          <v-form v-if="providerForm" :disabled="!!busy" class="config-form" @submit.prevent="saveProvider">
            <v-alert v-if="error" type="error" variant="tonal">{{ error }}</v-alert>
            <v-text-field v-model="providerForm.id" label="供应商名称" :disabled="!!editingProvider" required />
            <v-select v-model="providerForm.api_style" label="接口协议" :items="[{title:'OpenAI 兼容接口',value:'openai'}]" required />
            <v-text-field v-model="providerForm.base_url" label="接口地址" placeholder="https://example.com/v1" required />
            <p v-if="editingProvider" class="muted">已保存密钥：{{ providerById(editingProvider)?.api_key_masked || '未设置' }}</p>
            <v-text-field v-model="providerForm.api_key" label="接口密钥" type="password" autocomplete="new-password" :placeholder="editingProvider?'留空保留原密钥':'填写供应商密钥'" />
            <v-text-field v-model.number="providerForm.timeout_seconds" type="number" min="0.1" step="0.1" label="超时时间（秒）" required />
            <v-textarea v-model="providerForm.models" label="常用模型名称（每行一项，可留空）" rows="4" />
            <v-switch v-model="providerForm.enabled" label="启用供应商" color="primary" />
            <v-btn type="submit" color="primary" :loading="busy==='provider'" :disabled="!!busy||!canSaveProvider">保存供应商</v-btn>
          </v-form>
        </v-card-text>
      </v-card>
    </v-dialog>
    <v-dialog :model-value="routesOpen" max-width="900" scrollable :persistent="!!busy" @update:model-value="value=>!value&&closeRouting()">
      <v-card>
        <v-card-title class="dialog-title">编辑职责配置<v-btn variant="text" :disabled="!!busy" @click="closeRouting">关闭</v-btn></v-card-title>
        <v-card-text>
          <v-alert v-if="error" type="error" variant="tonal" class="mb-4">{{ error }}</v-alert>
          <v-form v-if="routingForm" :disabled="!!busy" @submit.prevent="saveRouting">
            <section v-for="role in roles" :key="role.key" class="routing-section">
              <div class="role-title"><h3>{{ role.name }}</h3><v-switch v-model="roleEnabled[role.key]" :label="`配置${role.name}模型`" color="primary" hide-details /></div>
              <template v-if="roleEnabled[role.key]">
                <div class="route-fields">
                  <v-select v-model="routingForm[role.key].provider_id" :items="data.providers.map(p=>({title:`${p.id}${p.enabled?'':'（停用）'}`,value:p.id}))" label="供应商" @update:model-value="routingForm[role.key].model=''" />
                  <v-combobox v-model="routingForm[role.key].model" :items="choicesFor(routingForm[role.key].provider_id)" label="模型名称" />
                  <v-text-field v-model="routingForm[role.key].reasoning_effort" label="推理强度（留空使用模型默认）" />
                </div>
                <v-btn variant="text" color="primary" :disabled="!!busy||!providerById(routingForm[role.key].provider_id)?.enabled||!routingForm[role.key].model?.trim()" @click="testConfirm={name:role.name,profile:profile(role.key)}">检查当前选择（不保存）</v-btn>
              </template>
              <p v-else class="muted mt-3">{{ role.name }}职责保持未配置。</p>
            </section>
            <v-alert v-if="testResult" :type="testResult.success?'success':'error'" variant="tonal" class="mb-4">{{ testResult.name }} · {{ testResult.model }}：{{ testResult.success?'能力检查通过':testResult.error||'能力检查未通过' }}</v-alert>
            <v-btn type="submit" color="primary" :loading="busy==='routing'" :disabled="!!busy||!canSaveRouting||!routingDirty">保存职责配置</v-btn>
          </v-form>
        </v-card-text>
      </v-card>
    </v-dialog>
    <v-dialog :model-value="retrievalOpen" max-width="700" :persistent="!!busy" @update:model-value="value=>!value&&closeRetrieval()"><v-card><v-card-title class="dialog-title">编辑语义检索配置<v-btn variant="text" :disabled="!!busy" @click="closeRetrieval">关闭</v-btn></v-card-title><v-card-text><v-form :disabled="!!busy" class="config-form" @submit.prevent="saveRetrieval"><v-alert type="info" variant="tonal">留空表示关闭该能力；模型 ID、维度和 rerank 协议必须以供应商实际文档为准。</v-alert><v-divider /><h3>Embedding</h3><div class="route-fields"><v-select v-model="retrievalForm.embedding.provider_id" :items="data.providers.map(p=>({title:`${p.id}${p.enabled?'':'（停用）'}`,value:p.id}))" label="供应商" clearable /><v-combobox v-model="retrievalForm.embedding.model" :items="choicesFor(retrievalForm.embedding.provider_id)" label="模型名称" clearable /><v-text-field v-model.number="retrievalForm.embedding.dimension" type="number" min="1" label="已确认维度（可留空）" /></div><v-divider /><h3>Rerank（可选）</h3><div class="route-fields"><v-select v-model="retrievalForm.rerank.provider_id" :items="data.providers.map(p=>({title:`${p.id}${p.enabled?'':'（停用）'}`,value:p.id}))" label="供应商" clearable /><v-combobox v-model="retrievalForm.rerank.model" :items="choicesFor(retrievalForm.rerank.provider_id)" label="模型名称" clearable /><v-select v-model="retrievalForm.rerank.protocol" :items="[{title:'Cohere v1（需确认供应商兼容）',value:'cohere_v1'}]" label="已确认协议" clearable /></div><v-btn type="submit" color="primary" :loading="busy==='retrieval'" :disabled="!!busy">保存语义检索配置</v-btn></v-form></v-card-text></v-card></v-dialog>
    <v-dialog :model-value="!!testConfirm" max-width="560" :persistent="busy==='test'" @update:model-value="value=>!value&&(testConfirm=null)"><v-card v-if="testConfirm" title="主动能力检查"><v-card-text><p>检查 {{ testConfirm.name }}：{{ testConfirm.profile.provider_id }} / {{ testConfirm.profile.model }}。</p><p class="mt-3">将进行最多 2 次真实模型请求，检查原图、指定工具与原生续接，可能产生供应商费用。检查使用合成资料，不向群聊发送消息。</p><v-alert v-if="error" type="error" variant="tonal" class="mt-3">{{ error }}</v-alert></v-card-text><v-card-actions><v-spacer /><v-btn :disabled="busy==='test'" @click="testConfirm=null">取消</v-btn><v-btn color="primary" :loading="busy==='test'" @click="testRoute">确认发起检查</v-btn></v-card-actions></v-card></v-dialog>
  </div>
</template>
<style scoped>
.role-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.role-card{display:flex;flex-direction:column;gap:16px;min-width:0}.role-title,.provider-heading,.dialog-title{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}.role-title h2,.provider-heading h2{font-size:19px}.role-description{min-height:3.5em;line-height:1.7}.role-card dl{display:grid;grid-template-columns:75px minmax(0,1fr);gap:12px;font-size:14px}.role-card dt{color:#64748b}.role-card dd{margin:0;overflow-wrap:anywhere}.provider-card{min-width:0}.provider-name{min-width:0}.provider-url{overflow-wrap:anywhere;margin-top:8px}.provider-meta,.actions,.model-tags{display:flex;flex-wrap:wrap;gap:10px 16px}.provider-meta{font-size:13px;color:#64748b;margin:16px 0}.model-tags .v-chip{max-width:100%;height:auto;min-height:26px;white-space:normal;overflow-wrap:anywhere}.config-form{display:grid;gap:8px}.config-form>.v-btn{justify-self:start}.routing-section{padding:18px 0;border-bottom:1px solid #e2e8f0;margin-bottom:18px}.route-fields{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.5fr);gap:12px;margin-top:16px}.route-fields>:last-child{grid-column:1/-1}.check-list{display:flex;flex-wrap:wrap;gap:8px 20px}@media(max-width:1100px){.role-grid{grid-template-columns:minmax(0,1fr)}}@media(max-width:600px){.route-fields{grid-template-columns:minmax(0,1fr)}.role-description{min-height:0}}
</style>
