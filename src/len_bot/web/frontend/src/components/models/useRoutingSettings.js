import { computed, ref } from 'vue'
import { api } from '../../api.js'
import { roles } from '../../domain/roles.js'

// Roles tab: the routing editor and the explicit capability check. The page
// owns guards, the shared read and conflict flow.
export function useRoutingSettings({
  data, busy, loading, loaded, error, message, conflicts, writeHeld, clone, beginOperation,
  submitConfiguration, readSaved, saveFailure, providerById
}) {
  const routingBaseline=ref(null)
  const routesOpen = ref(false),
    routingForm = ref(null),
    routingOriginal = ref(''),
    roleEnabled = ref({})
  const testConfirm = ref(null), testResult = ref(null)
  const routingConflict = computed(() => conflicts.entries.routing)
  const emptyProfile = () => ({ provider_id: '', model: '', reasoning_effort: '', supports_vision: false })
  const routingSnapshot = () => JSON.stringify({ profiles: routingForm.value, enabled: roleEnabled.value })
  const routingDirty = computed(() => routesOpen.value && routingSnapshot() !== routingOriginal.value)
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
  return {
    routesOpen, routingForm, routingOriginal, roleEnabled, testConfirm, testResult,
    routingBaseline, routingConflict, routingDirty, canSaveRouting, emptyProfile, routingSnapshot,
    writeRouting, initialiseRouting, routingValues, changeRoutingBinding, editRouting,
    closeRouting, profile, saveRouting, testRoute
  }
}
