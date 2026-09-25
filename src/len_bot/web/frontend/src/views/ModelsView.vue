<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter, onBeforeRouteUpdate } from 'vue-router'
import { api, fmtTime } from '../api.js'
import { roles } from '../domain/roles.js'
import PageHeader from '../components/PageHeader.vue'
import StatusBadge from '../components/StatusBadge.vue'
import EntityLink from '../components/EntityLink.vue'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'
import { useConfigConflicts } from '../composables/useConfigConflicts.js'
import { hasConfigDraftChanges, rebaseConfigDraft } from '../lib/configDraft.js'
import ConfigConflictBanner from '../components/ConfigConflictBanner.vue'
import ResourceViewer from '../components/ResourceViewer.vue'

const route = useRoute(), router = useRouter()
const tab = computed(() => route.query.tab === 'providers' ? 'providers' : 'roles')
const data = ref({ providers: [], routing: null }),
  loading = ref(false),
  loaded = ref(false),
  readAt = ref(null)
const error = ref(''),
  message = ref(''),
  busy = ref(''),
  catalogs = ref({}),
  selectedModels = ref({}),
  modelOriginal = ref({})
const providerBaseline=ref(null),routingBaseline=ref(null),retrievalBaseline=ref(null)
const conflicts = useConfigConflicts()
const catalogProviders = ref({}), catalogInvalid = ref({})
const clone=value=>JSON.parse(JSON.stringify(value))
const providerOpen = ref(false),
  editingProvider = ref(''),
  providerForm = ref(null),
  providerOriginal = ref('')
const providerRemoved = ref(false)
const routesOpen = ref(false),
  routingForm = ref(null),
  routingOriginal = ref(''),
  roleEnabled = ref({})
const testConfirm = ref(null), testResult = ref(null)
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
const providerConflict = computed(() => conflicts.entries.provider)
const routingConflict = computed(() => conflicts.entries.routing)
const retrievalConflict = computed(() => conflicts.entries.retrieval)
const deleteConflicts = computed(() => Object.entries(conflicts.entries).filter(([key]) => key.startsWith('delete:')))
const orphanCatalogs = computed(() => Object.keys(catalogs.value).filter(id => !providerById(id)))
const providerKeepBlocked = computed(() => !!providerConflict.value?.snapshot
  && ((!!editingProvider.value !== !!providerConflict.value.snapshot.provider) || (!!editingProvider.value && providerRemoved.value)))
const reservationRows = computed(() => reservations.value?.items || [])
const reservationAccounts = computed(() => reservations.value?.accounts || [])
async function loadReservations() {
  const fresh = reservationGuard()
  reservationError.value = '';
  reservationLoading.value = true
  try {
    const result = await api('/api/models/reservations')
    if (fresh()) {
      reservations.value = result;
      reservationReadAt.value = Date.now()/1000
    }
  }
  catch (e) {
    if (fresh()) reservationError.value = e.message
  }
  finally {
    if (fresh()) reservationLoading.value = false
  }
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
const emptyProfile = () => ({ provider_id: '', model: '', reasoning_effort: '', supports_vision: false })
const applicationPending = computed(()=>data.value.effective && ['providers','routing','retrieval'].some(key=>JSON.stringify(data.value[key])!==JSON.stringify(data.value.effective[key])))
const providerDirty = computed(() => providerOpen.value && JSON.stringify(providerForm.value) !== providerOriginal.value)
const routingSnapshot = () => JSON.stringify({ profiles: routingForm.value, enabled: roleEnabled.value })
const routingDirty = computed(() => routesOpen.value && routingSnapshot() !== routingOriginal.value)
const catalogDirty = computed(() => Object.keys(catalogs.value).some(id => JSON.stringify(selectedModels.value[id]) !== modelOriginal.value[id]))
const retrievalDirty = computed(() => retrievalOpen.value && JSON.stringify(retrievalForm.value) !== retrievalOriginal.value)
useUnsavedChanges(computed(() => providerDirty.value || routingDirty.value || catalogDirty.value || retrievalDirty.value))
onBeforeRouteUpdate((to, from) => to.query.tab === from.query.tab
  || !(providerDirty.value || routingDirty.value || retrievalDirty.value)
  || window.confirm('放弃当前弹窗内未保存的配置并切换标签？常用模型目录的选择会保留。'))
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
const inUse = id => roles.some(({ key }) => data.value.routing?.[key]?.provider_id === id)
  || [data.value.retrieval?.embedding, data.value.retrieval?.rerank].some(profile => profile?.provider_id === id)
const choicesFor = id => providerById(id)?.models || []
const canSaveProvider = computed(() => providerForm.value && providerForm.value.id.trim()
  && !providerRemoved.value
  && providerForm.value.base_url.trim() && providerForm.value.api_style
  && Number.isFinite(providerForm.value.timeout_seconds) && providerForm.value.timeout_seconds > 0
  && (providerForm.value.api_key_action !== 'replace' || providerForm.value.api_key.trim()))
const modelNames = names => [...new Set(names.map(model => model.trim()).filter(Boolean))].sort()
const providerFields = provider => provider && ({ id: provider.id.trim(), base_url: provider.base_url.trim(), api_style: provider.api_style,
  enabled: provider.enabled, timeout_seconds: provider.timeout_seconds, models: modelNames(provider.models) })
function providerValues() {
  return providerFields({ ...providerForm.value, models: providerForm.value.models.split('\n') })
}
function writeProvider(provider, key = {api_key:'', api_key_action:'keep'}) {
  providerForm.value = { ...providerFields(provider), ...key, models: provider.models.join('\n') }
}
function adoptProvider(provider) {
  editingProvider.value = provider.id;
  providerBaseline.value = clone(provider);
  providerRemoved.value = false
  writeProvider(provider);
  providerOriginal.value = JSON.stringify(providerForm.value)
}
const catalogSource = provider => provider && Object.fromEntries(
  ['id', 'base_url', 'api_style', 'enabled', 'credential_revision'].map(key => [key, provider[key]]))
const effectiveProvider = id => data.value.effective?.providers?.find(provider => provider.id === id)
function catalogUnavailable(provider) {
  if (!provider?.enabled || !provider.api_key_masked) return '须先明确保存并启用供应商及其密钥。'
  if (hasConfigDraftChanges(catalogSource(provider), catalogSource(effectiveProvider(provider.id)))) return '已保存接口与当前运行接口不同，不能把当前运行接口的目录用于该草稿。'
  return ''
}
function clearCatalog(id) {
  delete catalogs.value[id];
  delete selectedModels.value[id];
  delete modelOriginal.value[id]
  delete catalogProviders.value[id];
  delete catalogInvalid.value[id];
  conflicts.clear(`models:${id}`)
}
function cancelCatalog(id) {
  if (busy.value || writeHeld.value) return
  if (JSON.stringify(selectedModels.value[id]) !== modelOriginal.value[id]
      && !window.confirm(`放弃供应商「${id}」尚未保存的目录选择？`)) return
  clearCatalog(id)
}
function reconcileCatalogs() {
  for (const id of Object.keys(catalogs.value)) {
    const provider = providerById(id)
    if (!provider) catalogInvalid.value[id] = '该供应商已不在本次保存列表中；目录草稿不能用于重新创建供应商。'
    else if (hasConfigDraftChanges(catalogSource(catalogProviders.value[id]), catalogSource(provider))) {
      catalogInvalid.value[id] = '供应商接口、启用状态或凭据修订已改变；这份目录不再代表当前接口。请核对并取消旧选择，再明确获取目录。'
    }
  }
}
function writeRouting(routing) {
  routingForm.value = Object.fromEntries(roles.map(({ key }) => [key,
    routing?.[key] ? { ...routing[key] } : emptyProfile()]))
  roleEnabled.value = Object.fromEntries(roles.map(({ key }) => [key, routing?.[key] != null]))
}
function initialiseRouting(routing = data.value.routing) {
  routingBaseline.value=clone(routing);
  writeRouting(routing)
  routingOriginal.value = routingSnapshot()
}
const routingValues = () => Object.fromEntries(roles.map(({key}) => [key, roleEnabled.value[key] ? profile(key) : null]))
function changeRoutingBinding(key, field, value) {
  const previous = routingForm.value[key][field]
  routingForm.value[key][field] = value
  if ((previous || '').trim() === (value || '').trim()) return
  if (field === 'provider_id') routingForm.value[key].model = ''
  routingForm.value[key].reasoning_effort = '';
  routingForm.value[key].supports_vision = false
}
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
function editProvider(provider = null) {
  if (busy.value || loading.value || writeHeld.value || !loaded.value) return
  conflicts.clear('provider');
  error.value='';
  message.value=''
  providerRemoved.value = false
  providerBaseline.value=clone(provider)
  editingProvider.value = provider?.id || ''
  providerForm.value = provider ? {
    id: provider.id, base_url: provider.base_url, api_style: provider.api_style, api_key: '',api_key_action:'keep',
    enabled: provider.enabled, timeout_seconds: provider.timeout_seconds, models: provider.models.join('\n'),
  } : {
    id: '',
    base_url: '',
    api_style: 'openai',
    api_key: '',
    api_key_action:'replace',
    enabled: false,
    timeout_seconds: null,
    models: ''
  }
  providerOriginal.value = JSON.stringify(providerForm.value)
  providerOpen.value = true
}
function closeProvider() {
  if (busy.value) return
  if (providerDirty.value && !window.confirm('放弃尚未保存的供应商修改？')) return
  providerOpen.value = false;
  providerForm.value = null;
  providerBaseline.value = null;
  editingProvider.value = '';
  conflicts.clear('provider')
}
function editRouting() {
  if (busy.value || loading.value || writeHeld.value || !loaded.value) return
  conflicts.clear('routing');
  error.value='';
  message.value='';
  initialiseRouting();
  routesOpen.value = true
}
function closeRouting() {
  if (busy.value) return
  if (routingDirty.value && !window.confirm('放弃尚未保存的职责配置？')) return
  routesOpen.value = false;
  conflicts.clear('routing');
  initialiseRouting()
}
async function saveProvider() {
  if (busy.value || loading.value || writeHeld.value || providerConflict.value || !canSaveProvider.value) return
  if (!window.confirm('保存此供应商配置？接口和启用状态将用于后续运行，密钥按所选保留、替换或清除操作处理。')) return
  const fresh = beginOperation('provider')
  const progress={submitted:false,providerId:providerForm.value.id.trim()}
  try {
    const body = { ...providerValues(), api_key_action:providerForm.value.api_key_action,
      api_key: providerForm.value.api_key_action === 'replace' ? providerForm.value.api_key.trim() : null,
    }
    const result = await submitConfiguration('/api/models/providers', {
      method: 'POST',
      body: JSON.stringify({baseline:providerBaseline.value,values:body})
    },progress)
    await readSaved(result, 'provider', fresh)
  } catch (e) {
    await saveFailure(e, 'provider', fresh, progress)
  }
  finally {
    if (fresh()) busy.value = ''
  }
}
async function deleteProvider(provider) {
  const conflict = conflicts.entries[`delete:${provider.id}`]
  if (busy.value || loading.value || writeHeld.value || (conflict && !conflict.snapshot)
      || !window.confirm(`删除供应商「${provider.id}」及保存的密钥？${conflict ? '这是重新核对保存值后的新删除操作。' : ''}`)) return
  const fresh = beginOperation(`delete:${provider.id}`)
  const progress={submitted:false,providerId:provider.id}
  try {
    const result = await submitConfiguration(`/api/models/providers/${encodeURIComponent(provider.id)}`, { method: 'DELETE',body:JSON.stringify({baseline:provider,values:null}) },progress)
    if (!fresh()) return
    await readSaved(result, `delete:${provider.id}`, fresh)
  } catch (e) {
    await saveFailure(e, `delete:${provider.id}`, fresh, progress)
  }
  finally {
    if (fresh()) busy.value = ''
  }
}
async function fetchModels(provider) {
  if (busy.value || loading.value || writeHeld.value || catalogs.value[provider.id] || catalogUnavailable(provider)) return
  const fresh = beginOperation(`catalog:${provider.id}`)
  const source = clone(provider)
  try {
    const result = await api(`/api/models/providers/${encodeURIComponent(provider.id)}/models`)
    if (!fresh()) return
    if (result.provider_id !== provider.id) throw new Error('返回目录不属于本次选择的供应商，未采用。')
    if (!await load({ accept:fresh }) || !fresh()) return
    const current = providerById(provider.id)
    if (!current || hasConfigDraftChanges(catalogSource(source),catalogSource(current)) || catalogUnavailable(current)) {
      throw new Error('读取目录期间供应商已删除、接口已改变或尚未应用；未将返回目录设为草稿。请核对当前接口后再明确获取。')
    }
    catalogs.value[provider.id] = result.models
    catalogProviders.value[provider.id] = clone(current)
    selectedModels.value[provider.id] = [...current.models]
    modelOriginal.value[provider.id] = JSON.stringify(selectedModels.value[provider.id])
  } catch (e) {
    if (fresh()) error.value = e.message
  }
  finally {
    if (fresh()) busy.value = ''
  }
}
async function saveModels(provider) {
  if (busy.value || loading.value || writeHeld.value || !catalogs.value[provider.id]
      || catalogInvalid.value[provider.id] || conflicts.entries[`models:${provider.id}`]) return
  const fresh = beginOperation(`models:${provider.id}`)
  const progress={submitted:false,providerId:provider.id}
  try {
    const result = await submitConfiguration(`/api/models/providers/${encodeURIComponent(provider.id)}/models`, {
      method: 'POST',
      body: JSON.stringify({
        baseline:JSON.parse(modelOriginal.value[provider.id]),
        values:{models:modelNames(selectedModels.value[provider.id])}
      })
    },progress)
    await readSaved(result, `models:${provider.id}`, fresh)
  } catch (e) {
    await saveFailure(e, `models:${provider.id}`, fresh, progress)
  }
  finally {
    if (fresh()) busy.value = ''
  }
}
function profile(key) {
  const value = routingForm.value[key]
  return {
    provider_id: value.provider_id?.trim() || '',
    model: value.model?.trim() || '',
    reasoning_effort: value.reasoning_effort?.trim() || null,
    supports_vision: !!value.supports_vision
  }
}
const canSaveRouting = computed(() => routingForm.value && roles.every(({key}) =>
  !roleEnabled.value[key] || providerById(routingForm.value[key].provider_id) && routingForm.value[key].model?.trim()))
async function saveRouting() {
  if (busy.value || loading.value || writeHeld.value || routingConflict.value || !canSaveRouting.value) return
  if (!window.confirm('保存对话、工作与维护配置？后续运行将使用所选模型；正在进行的运行保留原绑定。')) return
  const fresh = beginOperation('routing')
  const progress={submitted:false,providerId:null}
  try {
    const routing = routingValues()
    const result = await submitConfiguration('/api/models/routing', {
      method: 'POST',
      body: JSON.stringify({baseline:routingBaseline.value,values:routing})
    },progress)
    await readSaved(result, 'routing', fresh)
  } catch (e) {
    await saveFailure(e, 'routing', fresh, progress)
  }
  finally {
    if (fresh()) busy.value = ''
  }
}
async function testRoute() {
  if (busy.value || loading.value || writeHeld.value || !testConfirm.value) return
  const { name, profile } = testConfirm.value
  const fresh = beginOperation('test');
  testResult.value = null
  try {
    const result = await api('/api/models/test', { method: 'POST', body: JSON.stringify(profile) });
    if(fresh()){
      testResult.value = { name, ...result };
      testConfirm.value = null
    }
  }
  catch (e) {
    if(fresh())error.value = e.message
  }
  finally {
    if(fresh())busy.value = ''
  }
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
    <template v-if="tab==='roles'">
      <div v-if="loaded" class="role-grid">
        <v-card v-for="role in roles" :key="role.key" class="pa-5 role-card">
          <div class="role-title">
            <h2>{{ role.name }}</h2>
            <v-chip size="small" :color="data.routing?.[role.key] ? 'primary' : 'default'">
              {{ data.routing?.[role.key] ? '已配置' : '未配置' }}
            </v-chip>
          </div>
          <p class="muted role-description">{{ role.description }}</p>
          <template v-if="data.routing?.[role.key]">
            <dl>
              <dt>供应商</dt>
              <dd>{{ data.routing[role.key].provider_id }}</dd>
              <dt>模型</dt>
              <dd>{{ data.routing[role.key].model }}</dd>
              <dt>推理强度</dt>
              <dd>{{ data.routing[role.key].reasoning_effort || '模型默认' }}</dd>
              <dt>当前运行</dt>
              <dd>{{ data.effective?.routing?.[role.key]?.model || '未绑定' }}</dd>
            </dl>
            <v-alert
              v-if="!providerById(data.routing[role.key].provider_id)?.enabled"
              type="warning"
              variant="tonal"
              density="compact"
            >当前供应商未启用</v-alert>
            <v-btn
              class="mt-auto"
              variant="outlined"
              :disabled="!!busy || writeHeld || !providerById(data.routing[role.key].provider_id)?.enabled"
              @click="testConfirm={name:role.name,profile:{...data.routing[role.key]}}"
            >主动检查能力</v-btn>
          </template>
          <p v-else class="muted">
            {{ role.key==='maintenance' ? '维护未配置，历史维护与工作压缩尚未就绪。' : '该职责尚未配置。' }}
          </p>
        </v-card>
      </div>
      <v-alert type="info" variant="tonal">浏览、刷新和选择模型不会发起模型请求。能力检查需要你主动确认；模型目录中的名称不代表已通过检查。</v-alert>
      <v-card v-if="testResult" class="pa-5">
        <div class="role-title">
          <h2>{{ testResult.name }}能力检查</h2>
          <v-chip :color="testResult.success?'success':'error'">
            {{ testResult.success?'检查通过':'检查失败' }}
          </v-chip>
        </div>
        <p class="my-3">{{ testResult.model }} · {{ testResult.latency_ms }} 毫秒</p>
        <div class="check-list">
          <p
            v-for="(name,key) in {image_reading:'原图识别',forced_tool:'指定工具调用',tool_continuation:'原生工具续接'}"
            :key="key"
          >
            {{ name }}：{{ testResult.checks[key] ? '通过' : '未通过或未执行' }}
          </p>
        </div>
        <v-alert v-if="testResult.error" type="error" variant="tonal" class="mt-3">
          {{ testResult.error }}
        </v-alert>
      </v-card>
      <RouterLink :to="{name:'activity',query:{tab:'calls'}}">前往运行记录查看持久调用账与用量</RouterLink>
    </template>
    <template v-else>
      <p class="muted">密钥只在后台保存，编辑时明确选择保留、替换或清除。获取接口目录会访问当前运行接口，但不会生成模型回答。</p>
      <v-card v-for="id in orphanCatalogs" :key="`missing:${id}`" class="pa-5 provider-card">
        <h2>{{ id }} · 目录草稿失效</h2>
        <p class="my-3">{{ catalogInvalid[id] }}</p>
        <ResourceViewer :content="selectedModels[id]" title="未保存的目录选择（仅供核对）" />
        <v-btn
          class="mt-3"
          variant="outlined"
          :disabled="!!busy||writeHeld"
          @click="cancelCatalog(id)"
        >放弃这份目录草稿</v-btn>
      </v-card>
      <v-card v-for="provider in data.providers" :key="provider.id" class="pa-5 provider-card">
        <div class="provider-heading">
          <div class="provider-name">
            <h2>{{ provider.id }}</h2>
            <p class="provider-url muted">{{ provider.base_url }}</p>
          </div>
          <StatusBadge domain="provider" :status="provider.enabled?'enabled':'disabled'" />
        </div>
        <div class="provider-meta">
          <span>密钥 {{ provider.api_key_masked || '未设置' }}</span>
          <span>超时 {{ provider.timeout_seconds }} 秒</span>
          <span v-if="inUse(provider.id)">已保存的职责或检索绑定引用</span>
        </div>
        <div class="actions">
          <v-btn
            variant="outlined"
            :disabled="!!busy||loading||writeHeld"
            @click="editProvider(provider)"
          >编辑接口</v-btn>
          <v-btn
            color="error"
            variant="text"
            :disabled="!!busy||loading||writeHeld||inUse(provider.id)||(!!conflicts.entries[`delete:${provider.id}`]&&!conflicts.entries[`delete:${provider.id}`].snapshot)"
            @click="deleteProvider(provider)"
          >删除</v-btn>
        </div>
        <v-divider class="my-4" />
        <h3 class="mb-3">常用模型目录</h3>
        <template v-if="catalogs[provider.id]">
          <v-alert v-if="catalogInvalid[provider.id]" type="warning" variant="tonal" class="mb-3">
            {{ catalogInvalid[provider.id] }}<p v-if="conflicts.entries[`models:${provider.id}`]">
              {{ conflicts.entries[`models:${provider.id}`].problem.message }}
            </p>
          </v-alert>
          <ConfigConflictBanner
            v-else
            :conflict="conflicts.entries[`models:${provider.id}`]?.problem"
            :current="conflicts.entries[`models:${provider.id}`]?.snapshot"
            :path-label="conflicts.entries[`models:${provider.id}`]?.problem.path.join(' → ')"
            :read-at="conflicts.entries[`models:${provider.id}`]?.readAt"
            :read-error="conflicts.entries[`models:${provider.id}`]?.readError"
            :busy="!!busy||loading||writeHeld"
            @keep="resolveConflict(`models:${provider.id}`,true)"
            @take="resolveConflict(`models:${provider.id}`,false)"
            @reload="refresh"
          >
            <template #current>
              <ResourceViewer
                :content="conflicts.entries[`models:${provider.id}`]?.snapshot?.provider?.models"
                title="本次保存的常用模型"
              />
            </template>
          </ConfigConflictBanner>
          <v-autocomplete
            :disabled="!!busy||loading||writeHeld||!!catalogInvalid[provider.id]"
            v-model="selectedModels[provider.id]"
            :items="[...new Set([...catalogs[provider.id],...provider.models,...selectedModels[provider.id]])]"
            label="搜索并选择常用模型"
            multiple
            chips
            closable-chips
            clearable
          />
          <div class="actions">
            <v-btn
              color="primary"
              :loading="busy===`models:${provider.id}`"
              :disabled="!!busy||loading||writeHeld||!!catalogInvalid[provider.id]||!!conflicts.entries[`models:${provider.id}`]"
              @click="saveModels(provider)"
            >保存常用模型</v-btn>
            <v-btn variant="text" :disabled="!!busy||writeHeld" @click="cancelCatalog(provider.id)">取消选择</v-btn>
          </div>
        </template>
        <template v-else>
          <div class="model-tags">
            <v-chip v-for="model in provider.models" :key="model" size="small">{{ model }}</v-chip>
            <span v-if="!provider.models.length" class="muted">尚未保存常用模型</span>
          </div>
          <p v-if="catalogUnavailable(provider)" class="muted mt-3">
            {{ catalogUnavailable(provider) }}
          </p>
          <v-btn
            class="mt-4"
            variant="tonal"
            :loading="busy===`catalog:${provider.id}`"
            :disabled="!!busy||loading||writeHeld||!!catalogUnavailable(provider)"
            @click="fetchModels(provider)"
          >获取接口模型目录</v-btn>
        </template>
      </v-card>
      <v-card v-if="loaded&&!error&&!data.providers.length" class="pa-8 text-center muted">还没有供应商，点击“添加供应商”开始配置。</v-card>
    </template>
    <v-dialog
      :model-value="providerOpen"
      max-width="650"
      scrollable
      :persistent="!!busy"
      @update:model-value="value=>!value&&closeProvider()"
    >
      <v-card>
        <v-card-title class="dialog-title">
          {{ editingProvider?'编辑供应商':'添加供应商' }}<v-btn variant="text" :disabled="!!busy" @click="closeProvider">关闭</v-btn>
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
          <v-alert
            v-if="providerRemoved&&!providerConflict"
            type="warning"
            variant="tonal"
            class="mb-4"
          >原供应商已从保存列表移除，旧草稿不能继续保存。请核对后关闭此编辑；确需新建时明确添加，后来出现的同名供应商也须重新进入编辑。</v-alert>
          <v-alert v-if="readbackPending" type="warning" variant="tonal" class="mb-4">已写入配置，待读回真实值。<v-btn variant="text" :disabled="!!busy" :loading="loading" @click="refresh">重新读取保存值</v-btn>
          </v-alert>
          <ConfigConflictBanner
            :conflict="providerConflict?.problem"
            :current="providerConflict?.snapshot"
            :path-label="providerConflict?.problem.path.join(' → ')"
            :read-at="providerConflict?.readAt"
            :read-error="providerConflict?.readError"
            :keep-disabled="providerKeepBlocked"
            :busy="!!busy||loading||writeHeld"
            @keep="resolveConflict('provider',true)"
            @take="resolveConflict('provider',false)"
            @reload="refresh"
          >
            <template #current>
              <ResourceViewer
                v-if="providerConflict?.snapshot?.provider"
                :content="providerConflict.snapshot.provider"
                title="本次保存的供应商（不含密钥）"
              />
              <p v-else>这个名称当前没有供应商。</p>
            </template>
          </ConfigConflictBanner>
          <p v-if="providerKeepBlocked" class="muted mb-4">
            {{ editingProvider ? '原供应商已从保存列表移除，旧编辑不能重建或接到后来出现的同名供应商。请采用现值重新编辑，或关闭后明确添加。' : '此名称已有供应商。请改用新名称，或采用现值进入该供应商编辑；不会用新建草稿直接覆盖已有接口。' }}
          </p>
          <v-form
            v-if="providerForm"
            :disabled="!!busy||loading||writeHeld"
            class="config-form"
            @submit.prevent="saveProvider"
          >
            <v-alert v-if="error" type="error" variant="tonal">{{ error }}</v-alert>
            <v-text-field
              v-model="providerForm.id"
              label="供应商名称"
              :disabled="!!editingProvider||!!busy||loading||writeHeld"
              required
            />
            <v-select
              v-model="providerForm.api_style"
              label="接口协议"
              :items="[{title:'OpenAI 兼容接口',value:'openai'}]"
              required
            />
            <v-text-field
              v-model="providerForm.base_url"
              label="接口地址"
              placeholder="https://example.com/v1"
              required
            />
            <p v-if="editingProvider" class="muted">已保存密钥：{{ providerById(editingProvider)?.api_key_masked || '未设置' }}
            </p>
            <v-select
              v-model="providerForm.api_key_action"
              label="密钥操作"
              :items="[{title:'保留当前密钥',value:'keep'},{title:'替换密钥',value:'replace'},{title:'清除密钥',value:'clear'}]"
            />
            <v-text-field
              v-if="providerForm.api_key_action==='replace'"
              v-model="providerForm.api_key"
              label="接口密钥"
              type="password"
              autocomplete="new-password"
              placeholder="替换时必须填写新密钥；留空不会保存"
            />
            <v-text-field
              v-model.number="providerForm.timeout_seconds"
              type="number"
              min="0.1"
              step="0.1"
              label="超时时间（秒）"
              required
            />
            <v-textarea v-model="providerForm.models" label="常用模型名称（每行一项，可留空）" rows="4" />
            <v-switch v-model="providerForm.enabled" label="启用供应商" color="primary" />
            <v-btn
              type="submit"
              color="primary"
              :loading="busy==='provider'"
              :disabled="!!busy||loading||writeHeld||!!providerConflict||!canSaveProvider"
            >保存供应商</v-btn>
          </v-form>
        </v-card-text>
      </v-card>
    </v-dialog>
    <v-dialog
      :model-value="routesOpen"
      max-width="900"
      scrollable
      :persistent="!!busy"
      @update:model-value="value=>!value&&closeRouting()"
    >
      <v-card>
        <v-card-title class="dialog-title">编辑职责配置<v-btn variant="text" :disabled="!!busy" @click="closeRouting">关闭</v-btn>
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
          <v-alert v-if="error" type="error" variant="tonal" class="mb-4">{{ error }}</v-alert>
          <v-alert v-if="readbackPending" type="warning" variant="tonal" class="mb-4">已写入配置，待读回真实值。<v-btn variant="text" :disabled="!!busy" :loading="loading" @click="refresh">重新读取保存值</v-btn>
          </v-alert>
          <ConfigConflictBanner
            :conflict="routingConflict?.problem"
            :current="routingConflict?.snapshot"
            :path-label="routingConflict?.problem.path.join(' → ')"
            :read-at="routingConflict?.readAt"
            :read-error="routingConflict?.readError"
            :busy="!!busy||loading||writeHeld"
            @keep="resolveConflict('routing',true)"
            @take="resolveConflict('routing',false)"
            @reload="refresh"
          >
            <template #current>
              <ResourceViewer
                v-if="routingConflict?.snapshot?.values"
                :content="routingConflict.snapshot.values"
                title="本次保存的职责绑定"
              />
              <p v-else>当前没有配置职责路由。</p>
            </template>
          </ConfigConflictBanner>
          <p class="muted mb-3">切换供应商或模型会清除旧绑定的推理强度和视觉确认。冲突后不会将旧参数拼到他人刚更换的模型；明确改选绑定时，该项按整项保留。</p>
          <v-form
            v-if="routingForm"
            :disabled="!!busy||loading||writeHeld"
            @submit.prevent="saveRouting"
          >
            <section v-for="role in roles" :key="role.key" class="routing-section">
              <div class="role-title">
                <h3>{{ role.name }}</h3>
                <v-switch
                  v-model="roleEnabled[role.key]"
                  :label="`配置${role.name}模型`"
                  color="primary"
                  hide-details
                />
              </div>
              <template v-if="roleEnabled[role.key]">
                <div class="route-fields">
                  <v-select
                    :model-value="routingForm[role.key].provider_id"
                    :items="data.providers.map(p=>({title:`${p.id}${p.enabled?'':'（停用）'}`,value:p.id}))"
                    label="供应商"
                    @update:model-value="value=>changeRoutingBinding(role.key,'provider_id',value)"
                  />
                  <v-combobox
                    :model-value="routingForm[role.key].model"
                    :items="choicesFor(routingForm[role.key].provider_id)"
                    label="模型名称"
                    @update:model-value="value=>changeRoutingBinding(role.key,'model',value)"
                  />
                  <v-text-field
                    v-model="routingForm[role.key].reasoning_effort"
                    label="推理强度（留空使用模型默认）"
                  />
                  <v-switch
                    v-model="routingForm[role.key].supports_vision"
                    label="已确认此绑定支持图片输入"
                    hint="视频采样帧只装配给已确认支持视觉的绑定；默认关闭。"
                    persistent-hint
                  />
                </div>
                <p
                  v-if="routingForm[role.key].provider_id&&!providerById(routingForm[role.key].provider_id)"
                  class="text-error mb-3"
                  role="alert"
                >原供应商 {{ routingForm[role.key].provider_id }} 已不在当前保存列表中；请明确选择，不自动迁移到其他接口。</p>
                <v-btn
                  variant="text"
                  color="primary"
                  :disabled="!!busy||writeHeld||!providerById(routingForm[role.key].provider_id)?.enabled||!routingForm[role.key].model?.trim()"
                  @click="testConfirm={name:role.name,profile:profile(role.key)}"
                >检查当前选择（不保存）</v-btn>
              </template>
              <p v-else class="muted mt-3">{{ role.name }}职责保持未配置。</p>
            </section>
            <v-alert
              v-if="testResult"
              :type="testResult.success?'success':'error'"
              variant="tonal"
              class="mb-4"
            >
              {{ testResult.name }} · {{ testResult.model }}：{{ testResult.success?'能力检查通过':testResult.error||'能力检查未通过' }}
            </v-alert>
            <v-btn
              type="submit"
              color="primary"
              :loading="busy==='routing'"
              :disabled="!!busy||loading||writeHeld||!!routingConflict||!canSaveRouting||!routingDirty"
            >保存职责配置</v-btn>
          </v-form>
        </v-card-text>
      </v-card>
    </v-dialog>
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
    <v-dialog
      :model-value="!!testConfirm"
      max-width="560"
      :persistent="busy==='test'"
      @update:model-value="value=>!value&&(testConfirm=null)"
    >
      <v-card v-if="testConfirm" title="主动能力检查">
        <v-card-text>
          <p>检查 {{ testConfirm.name }}：{{ testConfirm.profile.provider_id }} / {{ testConfirm.profile.model }}。</p>
          <p class="mt-3">将进行最多 2 次真实模型请求，检查原图、指定工具与原生续接，可能产生供应商费用。检查使用合成资料，不向群聊发送消息。</p>
          <v-alert v-if="error" type="error" variant="tonal" class="mt-3">{{ error }}</v-alert>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn :disabled="busy==='test'" @click="testConfirm=null">取消</v-btn>
          <v-btn color="primary" :loading="busy==='test'" :disabled="writeHeld" @click="testRoute">确认发起检查</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>
<style scoped>
.role-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}
.role-card{display:flex;flex-direction:column;gap:16px;min-width:0}
.usage-card{display:grid;gap:12px}
.limit-row{display:flex;flex-wrap:wrap;gap:8px 20px;align-items:baseline;font-size:13px}
.limit-row span{color:#64748b}
.reservation-table-wrap{overflow-x:auto}
.reservation-table{width:100%;border-collapse:collapse;text-align:left;font-size:13px}
.reservation-table caption{text-align:left;font-weight:600;padding:4px 0 10px}
.reservation-table th,.reservation-table td{padding:10px 12px;border-bottom:1px solid #e2e8f0;white-space:nowrap}
.reservation-table thead{background:rgb(var(--v-theme-surface-variant))}
.reservation-table tbody th{font-weight:500}
.role-title,.provider-heading,.dialog-title{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
.role-title h2,.provider-heading h2{font-size:19px}
.role-description{min-height:3.5em;line-height:1.7}
.role-card dl{display:grid;grid-template-columns:75px minmax(0,1fr);gap:12px;font-size:14px}
.role-card dt{color:#64748b}
.role-card dd{margin:0;overflow-wrap:anywhere}
.provider-card{min-width:0}
.provider-name{min-width:0}
.provider-url{overflow-wrap:anywhere;margin-top:8px}
.provider-meta,.actions,.model-tags{display:flex;flex-wrap:wrap;gap:10px 16px}
.provider-meta{font-size:13px;color:#64748b;margin:16px 0}
.model-tags .v-chip{max-width:100%;height:auto;min-height:26px;white-space:normal;overflow-wrap:anywhere}
.config-form{display:grid;gap:8px}
.config-form>.v-btn{justify-self:start}
.routing-section{padding:18px 0;border-bottom:1px solid #e2e8f0;margin-bottom:18px}
.route-fields{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.5fr);gap:12px;margin-top:16px}
.route-fields>:last-child{grid-column:1/-1}
.check-list{display:flex;flex-wrap:wrap;gap:8px 20px}
@media(max-width:1100px){
  .role-grid{grid-template-columns:minmax(0,1fr)}
}
@media(max-width:600px){
  .route-fields{grid-template-columns:minmax(0,1fr)}
  .role-description{min-height:0}
}
</style>
