<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter, onBeforeRouteUpdate } from 'vue-router'
import { api, fmtTime } from '../api.js'
import { roles } from '../domain/roles.js'
import PageHeader from '../components/PageHeader.vue'
import EntityLink from '../components/EntityLink.vue'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'
import { useGuardedRead } from '../composables/useGuardedRead.js'
import { useConfigConflicts } from '../composables/useConfigConflicts.js'
import { hasConfigDraftChanges, rebaseConfigDraft } from '../lib/configDraft.js'
import ConfigConflictBanner from '../components/ConfigConflictBanner.vue'
import ResourceViewer from '../components/ResourceViewer.vue'
import ProviderDialog from '../components/models/ProviderDialog.vue'
import ProvidersTab from '../components/models/ProvidersTab.vue'
import RolesTab from '../components/models/RolesTab.vue'
import RoutingDialog from '../components/models/RoutingDialog.vue'
import TestConfirmDialog from '../components/models/TestConfirmDialog.vue'
import { useProviderSettings } from '../components/models/useProviderSettings.js'
import { useRoutingSettings } from '../components/models/useRoutingSettings.js'

const route = useRoute(), router = useRouter()
const tab = computed(() => route.query.tab === 'providers' ? 'providers' : 'roles')
const data = ref({ providers: [], routing: null }),
  loading = ref(false),
  loaded = ref(false),
  readAt = ref(null)
const error = ref(''),
  message = ref(''),
  busy = ref('')
const retrievalBaseline=ref(null)
const conflicts = useConfigConflicts()
const clone=value=>JSON.parse(JSON.stringify(value))
const reservations = ref(null),
  reservationError = ref(''),
  reservationLoading = ref(false),
  reservationReadAt = ref(null)
const selection = () => `${route.name}:${tab.value}`
const readGuard = useRequestGuard(selection),
  operationGuard = useRequestGuard(selection),
  reservationGuard = useRequestGuard(selection)
const readbackPending = ref('')
const saveOutcome=ref(null), outcomeReadAt=ref(null)
const writeHeld=computed(()=>!!readbackPending.value||!!saveOutcome.value)
const retrievalConflict = computed(() => conflicts.entries.retrieval)
const deleteConflicts = computed(() => Object.entries(conflicts.entries).filter(([key]) => key.startsWith('delete:')))
const reservationRows = computed(() => reservations.value?.items || [])
const reservationAccounts = computed(() => reservations.value?.accounts || [])
const readReservations = useGuardedRead(reservationGuard, reservationLoading, reservationError)
function loadReservations() {
  return readReservations(() => api('/api/models/reservations'), result => {
    reservations.value = result;
    reservationReadAt.value = Date.now()/1000
  })
}
// An absent number is only "no limit" when a policy actually said so.  A day
// spanning several grants has no single admission balance, and a reference
// whose policy no longer resolves is not the same as the default's numbers.
const admissionText = account => {
  const state = account.admission?.state
  if (state === 'mixed_scope_required') return '需先选择范围'
  if (state === 'policy_unresolved') return '策略引用失效'
  return account.available === null ? '不设上限' : account.available.toLocaleString()
}
const admissionReason = account => {
  const state = account.admission?.state
  if (state === 'mixed_scope_required') return `当日涉及 ${account.admission.scope_required.length} 个授予引用`
  if (state === 'policy_unresolved') return '引用的具名策略已不存在'
  if (state === 'resolved') return `按授予 ${account.grant_reference} 指向的策略`
  return '按默认策略'
}
const emptyRetrievalProfile = () => ({provider_id:'', model:'', dimension:null, protocol:null})
const retrievalOpen = ref(false),
  retrievalForm = ref({embedding:emptyRetrievalProfile(), rerank:emptyRetrievalProfile()}),
  retrievalOriginal = ref('')
const applicationPending = computed(()=>data.value.effective && ['providers','routing','retrieval'].some(key=>JSON.stringify(data.value[key])!==JSON.stringify(data.value.effective[key])))
const retrievalDirty = computed(() => retrievalOpen.value && JSON.stringify(retrievalForm.value) !== retrievalOriginal.value)
function beginOperation(kind) {
  const fresh = operationGuard()
  readGuard();
  loading.value = false
  busy.value = kind;
  error.value = '';
  message.value = ''
  return fresh
}
async function readSaved(result, kind, fresh) {
  if (!fresh()) return
  if(result?.success!==true||result?.config_saved!==true||typeof result.message!=='string')
    throw new Error('模型配置响应缺少本次写入确认，结果待核对，未采用新基线。')
  message.value = result.message
  readbackPending.value = kind
  await load({ accept: fresh })
}
async function submitConfiguration(path,options,progress) {
  progress.submitted=true
  return api(path,options)
}
async function saveFailure(problem, kind, fresh, progress) {
  if (!fresh()) return
  if (problem.details?.config_saved === true) {
    error.value = problem.message;
    readbackPending.value = kind
  } else if (problem.details?.config_saved===false && conflicts.mark(kind, problem)) await load({ accept: fresh })
  else {
    if (kind.startsWith('models:') && problem.status === 404) catalogInvalid.value[kind.slice(7)] = problem.message
    const rejected=problem.details?.config_saved===false || (problem.status===422&&Array.isArray(problem.details))
    if(progress.submitted&&!rejected){
      saveOutcome.value={kind,providerId:progress.providerId||null,message:problem.message}
      outcomeReadAt.value=null
      await load({accept:fresh})
    }
    if (fresh()) error.value = problem.message+(error.value?'；当前读取：'+error.value:'')
  }
}
function adoptUnknownOutcome() {
  if(busy.value||loading.value||!saveOutcome.value||outcomeReadAt.value===null)return
  if(!window.confirm('放弃这次操作的原草稿（含未确认密钥替换或目录选择），采用当前读取值？这不重发保存或删除、不重试应用，也不追认原请求成功。'))return
  const {kind}=saveOutcome.value
  if(kind==='provider'){
    providerOpen.value=false;
    providerForm.value=null;
    providerBaseline.value=null;
    editingProvider.value=''
  }else if(kind==='routing'){
    routesOpen.value=false;
    initialiseRouting()
  }else if(kind==='retrieval'){
    retrievalOpen.value=false;
    adoptRetrieval(data.value.retrieval)
  }else clearCatalog(kind.slice(kind.indexOf(':')+1))
  conflicts.clear(kind);
  saveOutcome.value=null;
  outcomeReadAt.value=null;
  error.value=''
  message.value='已采用当前读取值，原操作结果未被追认；如需新操作，请从当前对象重新进入编辑。'
}
const providerById = id => data.value.providers.find(provider => provider.id === id)
const choicesFor = id => providerById(id)?.models || []
// Each tab's editor state is created here so it outlives tab switches and
// every response still answers to this page's guards.
const tabContext = {data, busy, loading, loaded, error, message, conflicts, writeHeld, clone, beginOperation,
  submitConfiguration, readSaved, saveFailure, load, providerById}
const providerTab = useProviderSettings(tabContext)
const routingTab = useRoutingSettings(tabContext)
const shared = {busy, loading, loaded, error, message, writeHeld, data, conflicts, refresh, resolveConflict,
  providerById, choicesFor, saveOutcome, outcomeReadAt, adoptUnknownOutcome, readbackPending}
const { selectedModels, modelOriginal, catalogInvalid, providerOpen, editingProvider, providerForm,
  providerRemoved, providerBaseline, providerKeepBlocked, providerDirty, catalogDirty, modelNames, providerFields,
  providerValues, writeProvider, adoptProvider, clearCatalog, reconcileCatalogs, editProvider } = providerTab
const { routesOpen, testConfirm, routingBaseline, routingDirty, initialiseRouting, writeRouting, routingValues,
  editRouting } = routingTab
useUnsavedChanges(computed(() => providerDirty.value || routingDirty.value || catalogDirty.value || retrievalDirty.value))
onBeforeRouteUpdate((to, from) => to.query.tab === from.query.tab
  || !(providerDirty.value || routingDirty.value || retrievalDirty.value)
  || window.confirm('放弃当前弹窗内未保存的配置并切换标签？常用模型目录的选择会保留。'))
function writeRetrieval(retrieval) {
  retrievalForm.value = Object.fromEntries(['embedding', 'rerank'].map(key => [key,
    retrieval?.[key] ? {...retrieval[key]} : emptyRetrievalProfile()]))
}
function adoptRetrieval(retrieval) {
  retrievalBaseline.value=clone(retrieval);
  writeRetrieval(retrieval)
  retrievalOriginal.value=JSON.stringify(retrievalForm.value)
}
function changeRetrievalBinding(key, field, value) {
  const previous = retrievalForm.value[key][field]
  retrievalForm.value[key][field] = value
  if ((previous || '').trim() === (value || '').trim()) return
  if (field === 'provider_id') retrievalForm.value[key].model = ''
  retrievalForm.value[key].dimension = null;
  retrievalForm.value[key].protocol = null
}
function retrievalValues(checkProviders = true) {
  return Object.fromEntries(['embedding', 'rerank'].map(key => {
    const value = retrievalForm.value[key],
      provider = value.provider_id?.trim() || '',
      model = value.model?.trim() || ''
    const dimension = value.dimension === '' || value.dimension == null ? null : Number(value.dimension)
    if (!provider && !model && dimension === null && !value.protocol) return [key, null]
    if (!provider || !model) throw new Error(`${key} 需要同时选择供应商和模型；要关闭，请清空该项绑定和参数。`)
    if (dimension !== null && (!Number.isInteger(dimension) || dimension < 1)) throw new Error(`${key} 维度必须是正整数或留空。`)
    if (checkProviders && !providerById(provider)) throw new Error(`${key} 引用的供应商已不在当前保存列表中，请重新选择。`)
    return [key, {provider_id:provider, model, dimension, protocol:value.protocol || null}]
  }))
}
const retrievalIssue = computed(() => {
  try {
    retrievalValues();
    return ''
  } catch(problem) {
    return problem.message
  }
})
function rebaseBinding(original, draft, current, name) {
  if (!hasConfigDraftChanges(original, draft)) return clone(current)
  if (original && draft) {
    const identity = value => value && ({provider_id:value.provider_id, model:value.model})
    if (hasConfigDraftChanges(identity(original), identity(draft))) return clone(draft)
    if (hasConfigDraftChanges(identity(original), identity(current))) {
      throw new Error(`${name} 的供应商或模型绑定已改变；请先采用现值后重新编辑，或明确重新选择本项绑定，不能把旧参数接到新模型上。`)
    }
  }
  return rebaseConfigDraft(original, draft, current)
}
function resolveConflict(kind, keep) {
  const snapshot = conflicts.entries[kind]?.snapshot
  if (busy.value || loading.value || writeHeld.value || !snapshot) return
  if (!keep && !window.confirm('放弃此项未保存的修改，采用本次读取的保存值？其他配置草稿不变。')) return
  try {
    if (kind === 'provider') {
      if (keep && providerKeepBlocked.value) return
      const key = {
        api_key:providerForm.value.api_key,
        api_key_action:providerForm.value.api_key_action
      }
      const next = keep ? rebaseConfigDraft(providerFields(providerBaseline.value), providerValues(), providerFields(snapshot.provider)) : null
      if (snapshot.provider) {
        adoptProvider(snapshot.provider)
        if (keep) writeProvider(next, key)
      } else if (!keep) {
        providerOpen.value=false;
        providerForm.value=null;
        providerBaseline.value=null;
        editingProvider.value=''
      }
    } else if (kind === 'routing' || kind === 'retrieval') {
      const current = snapshot.values,
        original = kind === 'routing' ? routingBaseline.value : retrievalBaseline.value
      const draft = keep ? (kind === 'routing' ? routingValues() : retrievalValues(false)) : null
      const keys = kind === 'routing' ? roles.map(role => role.key) : ['embedding','rerank']
      const next = keep ? Object.fromEntries(keys.map(key => [key,
        rebaseBinding(original?.[key] ?? null, draft[key], current?.[key] ?? null, key)])) : null
      if (kind === 'routing') {
        initialiseRouting(current);
        if(keep)writeRouting(next)
      }
      else {
        adoptRetrieval(current);
        if(keep)writeRetrieval(next)
      }
    } else if (kind.startsWith('models:')) {
      const id=kind.slice(7)
      if (catalogInvalid.value[id] || !snapshot.provider) return
      const current=[...snapshot.provider.models]
      const next=keep?rebaseConfigDraft(modelNames(JSON.parse(modelOriginal.value[id])),modelNames(selectedModels.value[id]),current):current
      selectedModels.value[id]=next;
      modelOriginal.value[id]=JSON.stringify(current)
    }
    conflicts.clear(kind);
    error.value=''
    message.value=keep?'已保留本项实际改动，其余采用现值；请核对后明确保存。':'已采用本次读取的保存值，没有提交保存。'
  } catch(problem) {
    error.value=problem.message
  }
}
function editRetrieval() {
  if (busy.value || loading.value || writeHeld.value || !loaded.value) return
  conflicts.clear('retrieval');
  error.value='';
  message.value=''
  adoptRetrieval(data.value.retrieval);
  retrievalOpen.value = true
}
function closeRetrieval() {
  if (busy.value) return;
  if (retrievalDirty.value && !window.confirm('放弃尚未保存的语义检索配置？')) return;
  retrievalOpen.value = false;
  conflicts.clear('retrieval')
}
async function saveRetrieval() {
  if (busy.value || loading.value || writeHeld.value || retrievalConflict.value || !retrievalForm.value || retrievalIssue.value) return
  const fresh = beginOperation('retrieval')
  const progress={submitted:false,providerId:null}
  try {
    const result = await submitConfiguration('/api/models/retrieval',{
      method:'POST',
      body:JSON.stringify({baseline:retrievalBaseline.value,values:retrievalValues()})
    },progress);
    await readSaved(result, 'retrieval', fresh)
  }
  catch(e){
    await saveFailure(e, 'retrieval', fresh, progress)
  } finally {
    if(fresh())busy.value=''
  }
}
async function load({ accept = () => true } = {}) {
  const current = readGuard(), fresh = () => current() && accept()
  const pending = Object.entries(conflicts.entries), providerId = providerForm.value?.id.trim()
  for (const [kind] of pending) conflicts.beginRead(kind)
  loading.value = true;
  error.value = ''
  if(saveOutcome.value)outcomeReadAt.value=null
  try {
    const result = await api('/api/models/providers')
    if (!fresh()) return false
    data.value = result;
    loaded.value = true;
    readAt.value = Date.now()/1000
    if (providerOpen.value && editingProvider.value && !providerById(editingProvider.value)) providerRemoved.value = true
    if (readbackPending.value === 'provider') {
      providerOpen.value = false;
      providerForm.value = null;
      providerBaseline.value = null;
      editingProvider.value = ''
    }
    if (readbackPending.value === 'routing') routesOpen.value = false
    if (readbackPending.value === 'retrieval') retrievalOpen.value = false
    if (readbackPending.value.startsWith('models:')) clearCatalog(readbackPending.value.slice(7))
    if (readbackPending.value.startsWith('delete:')) {
      const id = readbackPending.value.slice(7)
      clearCatalog(id)
    }
    conflicts.clear(readbackPending.value)
    readbackPending.value = ''
    reconcileCatalogs()
    for (const [kind, entry] of pending) {
      if (conflicts.entries[kind] !== entry) continue
      if (kind === 'provider') {
        const provider = providerById(providerId)
        if (editingProvider.value && !provider) providerRemoved.value = true
        conflicts.capture(kind, {id:providerId, provider:clone(provider || null)})
      }
      else if (kind === 'routing' || kind === 'retrieval') conflicts.capture(kind, {values:clone(result[kind])})
      else {
        const id = kind.slice(kind.indexOf(':')+1)
        conflicts.capture(kind, {id, provider:clone(providerById(id) || null)})
      }
    }
    if (!routesOpen.value) initialiseRouting()
    if(saveOutcome.value)outcomeReadAt.value=readAt.value
    return true
  } catch (e) {
    if (fresh()) {
      error.value = e.message
      for (const [kind, entry] of pending) if(conflicts.entries[kind]===entry)conflicts.readFailed(kind,e)
    }
  }
  finally {
    if (fresh()) loading.value = false
  }
  return false
}
async function refresh() {
  if (!busy.value) await Promise.all([load(), loadReservations()])
}
watch(tab, () => {
  operationGuard();
  busy.value = '';
  message.value = ''
  providerOpen.value = false;
  providerForm.value = null;
  routesOpen.value = false;
  retrievalOpen.value = false;
  testConfirm.value = null
  providerBaseline.value = null;
  editingProvider.value = ''
  for (const kind of ['provider','routing','retrieval']) conflicts.clear(kind)
  if(loaded.value)initialiseRouting()
  if(route.name==='models')refresh()
}, { immediate: true, flush: 'sync' })
watch(() => providerForm.value?.id.trim(), () => {
  if (editingProvider.value || !providerOpen.value) return
  conflicts.clear('provider');
  providerBaseline.value = null;
  error.value = '';
  message.value = ''
}, { flush:'sync' })
</script>
<template>
  <div class="page-stack">
    <PageHeader title="模型设置" description="三个职责显式配置，每次运行固定提供商、模型和推理强度。">
      <v-btn
        variant="outlined"
        :loading="loading||reservationLoading"
        :disabled="!!busy"
        @click="refresh"
      >刷新</v-btn>
      <v-btn
        v-if="tab==='roles'"
        color="primary"
        :disabled="!loaded||loading||!!busy||writeHeld"
        @click="editRouting"
      >编辑职责配置</v-btn>
      <v-btn
        v-else
        color="primary"
        :disabled="!loaded||loading||!!busy||writeHeld"
        @click="editProvider()"
      >添加供应商</v-btn>
    </PageHeader>
    <v-alert v-if="error" type="error" variant="tonal">
      {{ error }}<span v-if="readAt"> · 上次读取 {{ fmtTime(readAt) }}</span>
    </v-alert>
    <v-alert v-if="message" type="success" variant="tonal" closable @click:close="message=''">
      {{ message }}
    </v-alert>
    <p class="muted">切换标签或离页只停止本页跟踪，不取消已提交操作；回到原配置刷新核对后再保存。</p>
    <v-alert v-if="saveOutcome" type="warning" variant="tonal" class="mb-4">
      <p>模型配置操作 {{ saveOutcome.kind }}<span v-if="saveOutcome.providerId"> · {{ saveOutcome.providerId }}</span> 结果未知：{{ saveOutcome.message }}不能直接重交原草稿或删除。</p>
      <p v-if="outcomeReadAt!==null">当前保存与运行值已于 {{ fmtTime(outcomeReadAt) }} 读取；这不是旧操作回执。明确采用后关闭原编辑，重新进入当前对象。</p>
      <ResourceViewer v-if="outcomeReadAt!==null" title="当前保存与运行投影（不是原草稿）" :content="data" />
      <v-btn variant="text" :disabled="!!busy||loading" @click="refresh">读取当前保存值</v-btn>
      <v-btn
        variant="text"
        :disabled="!!busy||loading||outcomeReadAt===null"
        @click="adoptUnknownOutcome"
      >采用当前值继续操作</v-btn>
    </v-alert>
    <v-alert v-if="readbackPending" type="warning" variant="tonal">配置已写入，但尚未读回保存值；暂不再次提交。请刷新核对保存与运行状态。</v-alert>
    <v-alert v-if="applicationPending" type="warning" variant="tonal">已保存配置与当前运行值存在差异。下面表单编辑已保存值；尚未应用的配置不能视为运行中可用。</v-alert>
    <v-alert v-for="[kind,conflict] in deleteConflicts" :key="kind" type="warning" variant="tonal">
      <p>删除供应商「{{ kind.slice(7) }}」发生冲突：{{ conflict.problem.message }}</p>
      <p v-if="conflict.snapshot" class="mt-2">已重读保存值（{{ fmtTime(conflict.readAt) }}）。{{ conflict.snapshot.provider ? '核对后可回到该供应商再次点击删除，仍需确认；不会重试原删除。' : '该供应商当前已不存在，没有再次删除。' }}
      </p>
      <p v-else class="mt-2">先重读当前供应商，再决定是否提出新的删除操作。</p>
      <p v-if="conflict.readError" role="alert">重读失败：{{ conflict.readError }}</p>
      <details v-if="conflict.snapshot?.provider" class="mt-2">
        <summary>查看本次读取的供应商</summary>
        <ResourceViewer :content="conflict.snapshot.provider" title="已保存供应商（不含密钥）" />
      </details>
      <div class="actions mt-3">
        <v-btn variant="text" :disabled="!!busy||loading" @click="refresh">重读供应商</v-btn>
        <v-btn variant="text" :disabled="!!busy" @click="conflicts.clear(kind)">放弃本次删除</v-btn>
      </div>
    </v-alert>
    <v-card v-if="loaded" class="pa-4 retrieval-card">
      <div class="role-title">
        <h2>语义检索</h2>
        <div class="actions">
          <v-chip size="small" :color="data.retrieval?.embedding ? 'primary' : 'default'">
            {{ data.retrieval?.embedding ? '已绑定' : '未启用' }}
          </v-chip>
          <v-btn
            variant="outlined"
            size="small"
            :disabled="!!busy||writeHeld"
            @click="editRetrieval"
          >编辑</v-btn>
        </div>
      </div>
      <p class="muted">只在 Agent 主动查询认识时使用；索引失败不会回滚已提交认识。Embedding 与 rerank 请求单独计量。</p>
      <p v-if="data.retrieval?.embedding" class="auxiliary">Embedding：{{ data.retrieval.embedding.provider_id }} · {{ data.retrieval.embedding.model }}<span v-if="data.retrieval.embedding.dimension"> · {{ data.retrieval.embedding.dimension }} 维</span>
      </p>
      <p v-if="data.retrieval?.rerank" class="auxiliary">Rerank：{{ data.retrieval.rerank.provider_id }} · {{ data.retrieval.rerank.model }}
      </p>
    </v-card>
    <v-card class="pa-4 usage-card">
      <div class="role-title">
        <h2>工作额度预占</h2>
        <v-btn size="small" variant="text" :loading="reservationLoading" @click="loadReservations">刷新额度账</v-btn>
        <v-chip size="small" :color="reservationAccounts.length ? 'primary' : 'default'">
          {{ reservations?.day_key || '未读取' }}
        </v-chip>
      </div>
      <p class="muted">账务日 {{ reservations?.day_key || '未读取' }}{{ reservations?.timezone ? ` · ${reservations.timezone}` : '' }}。账户合计按全日预占与已结算用量聚合，含结果已提交但仍有在途调用的占用；下面明细最多列出最近 100 条，不能代替合计。本地估算与供应商 usage 分开统计。</p>
      <v-alert v-if="reservationError" type="error" variant="tonal" class="my-3">读取失败：{{ reservationError }}
      </v-alert>
      <v-progress-linear v-if="reservationLoading" indeterminate />
      <p v-if="reservationReadAt" class="muted">额度读取于 {{ fmtTime(reservationReadAt) }}{{ reservationError ? '；当前刷新失败，保留该次样本' : '' }}
      </p>
      <template v-if="reservations">
        <div class="limit-row">
          <span>单工作上限</span>
          <strong>
            {{ reservations.limits.work_token_limit === null ? '不设上限' : reservations.limits.work_token_limit.toLocaleString() }}
          </strong>
          <span>每账号每日上限</span>
          <strong>
            {{ reservations.limits.daily_user_token_limit === null ? '不设上限' : reservations.limits.daily_user_token_limit.toLocaleString() }}
          </strong>
          <span>每群每日上限</span>
          <strong>
            {{ reservations.limits.daily_scene_token_limit === null ? '未配置' : reservations.limits.daily_scene_token_limit.toLocaleString() }}
          </strong>
        </div>
        <p v-if="!reservationAccounts.length" class="muted py-3">该次读取的账务日没有工作预占记录。</p>
        <div v-else class="reservation-table-wrap">
          <table class="reservation-table">
            <thead>
              <tr>
                <th scope="col">账户</th>
                <th scope="col">预占中</th>
                <th scope="col">已结算</th>
                <th scope="col">准入余量</th>
                <th scope="col">依据</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="account in reservationAccounts" :key="account.subject">
                <th scope="row">{{ account.subject }}</th>
                <td>{{ account.held.toLocaleString() }}</td>
                <td>{{ account.used.toLocaleString() }}</td>
                <td>{{ admissionText(account) }}</td>
                <td class="muted">{{ admissionReason(account) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="muted mt-2">“预占中／已结算”是账户当日已占用的事实；“准入余量”是当前主体与范围下下一次工作可能获批的结果，两者不是同一个数。账户当日跨多个授予时不给单一余量。</p>
        <div v-if="reservationRows.length" class="reservation-table-wrap mt-4">
          <table class="reservation-table">
            <caption>最近预占明细（最多 100 条）</caption>
            <thead>
              <tr>
                <th scope="col">工作</th>
                <th scope="col">账户</th>
                <th scope="col">状态</th>
                <th scope="col">预占</th>
                <th scope="col">实际</th>
                <th scope="col">本地估算</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in reservationRows" :key="item.job_id">
                <td><EntityLink type="job" :id="item.job_id" :scene-id="item.scene_id" /></td>
                <td>{{ item.subject }}</td>
                <td>
                  {{ {held:'预占中',settling:'待收口',settled:'已结算',released:'已释放'}[item.status] || item.status }}
                </td>
                <td>{{ item.reserved_tokens.toLocaleString() }}</td>
                <td>
                  {{ item.usage_tokens === null ? '未结算' : item.usage_tokens.toLocaleString() }}
                </td>
                <td>
                  {{ item.estimated_tokens === null ? '未结算' : item.estimated_tokens.toLocaleString() }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
      <RouterLink :to="{name:'activity',query:{tab:'calls'}}">前往运行记录查看逐次调用与原始 usage</RouterLink>
    </v-card>
    <v-tabs
      :model-value="tab"
      color="primary"
      @update:model-value="value=>router.push({name:'models',query:{tab:value}})"
    >
      <v-tab value="roles">职责配置</v-tab>
      <v-tab value="providers">供应商</v-tab>
    </v-tabs>
    <v-progress-linear v-if="loading" indeterminate />
    <RolesTab v-if="tab==='roles'" :state="routingTab" :page="shared" />
    <ProvidersTab v-else :state="providerTab" :page="shared" />
    <ProviderDialog :state="providerTab" :page="shared" />
    <RoutingDialog :state="routingTab" :page="shared" />
    <v-dialog
      :model-value="retrievalOpen"
      max-width="700"
      scrollable
      :persistent="!!busy"
      @update:model-value="value=>!value&&closeRetrieval()"
    >
      <v-card>
        <v-card-title class="dialog-title">编辑语义检索配置<v-btn variant="text" :disabled="!!busy" @click="closeRetrieval">关闭</v-btn>
        </v-card-title>
        <v-card-text>
          <v-alert v-if="saveOutcome" type="warning" variant="tonal" class="mb-4">
            <p>模型配置操作 {{ saveOutcome.kind }}<span v-if="saveOutcome.providerId"> · {{ saveOutcome.providerId }}</span> 结果未知：{{ saveOutcome.message }}不能直接重交原草稿或删除。</p>
            <p v-if="outcomeReadAt!==null">当前保存与运行值已于 {{ fmtTime(outcomeReadAt) }} 读取；这不是旧操作回执。明确采用后关闭原编辑，重新进入当前对象。</p>
            <ResourceViewer v-if="outcomeReadAt!==null" title="当前保存与运行投影（不是原草稿）" :content="data" />
            <v-btn variant="text" :disabled="!!busy||loading" @click="refresh">读取当前保存值</v-btn>
            <v-btn
              variant="text"
              :disabled="!!busy||loading||outcomeReadAt===null"
              @click="adoptUnknownOutcome"
            >采用当前值继续操作</v-btn>
          </v-alert>
          <v-alert v-if="message" type="info" variant="tonal" class="mb-4">{{ message }}</v-alert>
          <v-alert v-if="readbackPending" type="warning" variant="tonal" class="mb-4">已写入配置，待读回真实值。<v-btn variant="text" :disabled="!!busy" :loading="loading" @click="refresh">重新读取保存值</v-btn>
          </v-alert>
          <v-alert v-if="error" type="error" variant="tonal" class="mb-4">{{ error }}</v-alert>
          <ConfigConflictBanner
            :conflict="retrievalConflict?.problem"
            :current="retrievalConflict?.snapshot"
            :path-label="retrievalConflict?.problem.path.join(' → ')"
            :read-at="retrievalConflict?.readAt"
            :read-error="retrievalConflict?.readError"
            :busy="!!busy||loading||writeHeld"
            @keep="resolveConflict('retrieval',true)"
            @take="resolveConflict('retrieval',false)"
            @reload="refresh"
          >
            <template #current>
              <ResourceViewer :content="retrievalConflict?.snapshot?.values" title="本次保存的检索绑定" />
            </template>
          </ConfigConflictBanner>
          <v-form
            :disabled="!!busy||loading||writeHeld"
            class="config-form"
            @submit.prevent="saveRetrieval"
          >
            <v-alert type="info" variant="tonal">要关闭一项能力，请清空该项供应商、模型及参数。切换供应商或模型会清空旧维度和协议，须按实际接口重新填写；冲突中明确改选的绑定按整项保留，不把旧参数拼到新模型上。</v-alert>
            <section v-for="key in ['embedding','rerank']" :key="key">
              <v-divider class="my-3" />
              <h3>{{ key==='embedding'?'Embedding':'Rerank（可选）' }}</h3>
              <div class="route-fields">
                <v-select
                  :model-value="retrievalForm[key].provider_id"
                  :items="data.providers.map(p=>({title:`${p.id}${p.enabled?'':'（停用）'}`,value:p.id}))"
                  label="供应商"
                  clearable
                  @update:model-value="value=>changeRetrievalBinding(key,'provider_id',value)"
                />
                <v-combobox
                  :model-value="retrievalForm[key].model"
                  :items="choicesFor(retrievalForm[key].provider_id)"
                  label="模型名称"
                  clearable
                  @update:model-value="value=>changeRetrievalBinding(key,'model',value)"
                />
                <v-text-field
                  v-if="key==='embedding'"
                  v-model.number="retrievalForm[key].dimension"
                  type="number"
                  min="1"
                  label="已确认维度（可留空）"
                />
                <v-select
                  v-else
                  v-model="retrievalForm[key].protocol"
                  :items="[{title:'Cohere v1（需确认供应商兼容）',value:'cohere_v1'}]"
                  label="已确认协议"
                  clearable
                />
              </div>
            </section>
            <p v-if="retrievalIssue" class="text-error" role="alert">{{ retrievalIssue }}</p>
            <v-btn
              type="submit"
              color="primary"
              :loading="busy==='retrieval'"
              :disabled="!!busy||loading||writeHeld||!!retrievalConflict||!!retrievalIssue||!retrievalDirty"
            >保存语义检索配置</v-btn>
          </v-form>
        </v-card-text>
      </v-card>
    </v-dialog>
    <TestConfirmDialog :state="routingTab" :page="shared" />
  </div>
</template>
<style scoped>
.usage-card{display:grid;gap:12px}
.limit-row{display:flex;flex-wrap:wrap;gap:8px 20px;align-items:baseline;font-size:13px}
.limit-row span{color:var(--text-secondary)}
.reservation-table-wrap{overflow-x:auto}
.reservation-table{width:100%;border-collapse:collapse;text-align:left;font-size:13px}
.reservation-table caption{text-align:left;font-weight:600;padding:4px 0 10px}
.reservation-table th,.reservation-table td{padding:10px 12px;border-bottom:1px solid var(--line);white-space:nowrap}
.reservation-table thead{background:rgb(var(--v-theme-surface-variant))}
.reservation-table tbody th{font-weight:500}
.role-title,.provider-heading,.dialog-title{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
.role-title h2,.provider-heading h2{font-size:19px}
.provider-meta,.actions,.model-tags{display:flex;flex-wrap:wrap;gap:10px 16px}
.config-form{display:grid;gap:8px}
.config-form>.v-btn{justify-self:start}
.route-fields{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.5fr);gap:12px;margin-top:16px}
.route-fields>:last-child{grid-column:1/-1}
@media(max-width:600px){
  .route-fields{grid-template-columns:minmax(0,1fr)}
}
</style>
