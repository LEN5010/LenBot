import { computed, ref } from 'vue'
import { api } from '../../api.js'
import { roles } from '../../domain/roles.js'
import { hasConfigDraftChanges } from '../../lib/configDraft.js'

// Providers tab: the provider editor, deletion and each provider's model
// catalog draft. The page owns guards, the shared read and conflict flow.
export function useProviderSettings({
  data, busy, loading, loaded, error, message, conflicts, writeHeld, clone, beginOperation,
  submitConfiguration, readSaved, saveFailure, load, providerById
}) {
  const catalogs = ref({}),
    selectedModels = ref({}),
    modelOriginal = ref({})
  const providerBaseline=ref(null)
  const catalogProviders = ref({}), catalogInvalid = ref({})
  const providerOpen = ref(false),
    editingProvider = ref(''),
    providerForm = ref(null),
    providerOriginal = ref('')
  const providerRemoved = ref(false)
  const providerConflict = computed(() => conflicts.entries.provider)
  const orphanCatalogs = computed(() => Object.keys(catalogs.value).filter(id => !providerById(id)))
  const providerKeepBlocked = computed(() => !!providerConflict.value?.snapshot
    && ((!!editingProvider.value !== !!providerConflict.value.snapshot.provider) || (!!editingProvider.value && providerRemoved.value)))
  const providerDirty = computed(() => providerOpen.value && JSON.stringify(providerForm.value) !== providerOriginal.value)
  const catalogDirty = computed(() => Object.keys(catalogs.value).some(id => JSON.stringify(selectedModels.value[id]) !== modelOriginal.value[id]))
  const inUse = id => roles.some(({ key }) => data.value.routing?.[key]?.provider_id === id)
    || [data.value.retrieval?.embedding, data.value.retrieval?.rerank].some(profile => profile?.provider_id === id)
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
  return {
    catalogs, selectedModels, modelOriginal, catalogProviders, catalogInvalid, providerOpen,
    editingProvider, providerForm, providerOriginal, providerRemoved, providerBaseline,
    providerConflict, orphanCatalogs, providerKeepBlocked, providerDirty, catalogDirty, inUse,
    canSaveProvider, modelNames, providerFields, providerValues, writeProvider, adoptProvider,
    catalogUnavailable, clearCatalog, cancelCatalog, reconcileCatalogs, editProvider,
    closeProvider, saveProvider, deleteProvider, fetchModels, saveModels
  }
}
