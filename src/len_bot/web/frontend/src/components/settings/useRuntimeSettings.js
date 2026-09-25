import { computed, ref } from 'vue'

// The backend refuses a mismatched pair outright, so the panel derives the
// config/receipt label instead of offering it as a second thing to get wrong.
const UPLOAD_PROTOCOLS = {napcat:'upload_group_file_data_file_id', snowluma:'upload_group_file'}
const UPLOAD_HELP = `implementation 决定内部配置／回执标签：napcat 用 upload_group_file_data_file_id，snowluma 用 upload_group_file。两者实际都发送 upload_group_file，不切换实现或重传。

version 可选，仅记录当前连接报告的版本；不按 SnowLuma 或 NapCat 的具体发行版本准入。

deployment_verified 表示你已人工核对所选实现、upload_group_file 动作与「仅文件资产目录只读挂到 /lenbot-files」；不要求先有过一次成功上传。真实 file_id 只从 FILE_UPLOADED 回执派生。

改动保存后需重启。`

// Runtime parameters: one JSON draft, with form controls that write into it.
// The page owns guards and the shared read flow.
export function useRuntimeSettings({
  busy, message, readAt, saveOutcomes, conflicts, beginOperation, saveDraft, saveError,
  adoptSnapshot, formValues
}) {
  const runtimeText = ref(null)
  const runtimeOriginal = ref('')
  const runtimeRestart = ref(false)
  const runtimeSavedBudgets = ref({})
  const runtimeEffectiveBudgets = ref({})
  const executionBudgets = [{key:'conversation_max_steps',label:'每轮对话模型调用',unit:'次'},
    {key:'conversation_max_tool_calls',label:'每轮对话工具调用',unit:'次'},
    {key:'conversation_window_seconds',label:'每轮对话绝对期限',unit:'秒'},
    {key:'job_max_steps',label:'同一工作累计模型调用',unit:'次'},
    {key:'job_max_tool_calls',label:'同一工作累计工具调用',unit:'次'},
    {key:'job_max_seconds',label:'同一工作累计执行时间',unit:'秒'},
    {key:'maintenance_max_tool_calls',label:'一次历史维护工具调用',unit:'次'}]
  const budgetText = (value, unit) => value===undefined ? '未提供'
    : value===null ? '不设限（由其他维度停止）' : `${value} ${unit}`
  const runtimeBudgetValue = key => {
    try {
      const obj=JSON.parse(runtimeText.value||'{}');
      return obj[key] ?? ''
    } catch {
      return ''
    }
  }
  const setRuntimeBudget = (key, value) => {
    let obj
    try {
      obj=JSON.parse(runtimeText.value||'{}')
    } catch {
      return
    }
    if (value==='' || value===null || value===undefined) obj[key]=null
    else {
      const text=String(value).trim()
      if (!/^-?\d+(\.\d+)?$/.test(text)) return
      obj[key]=Number(text)
    }
    runtimeText.value=JSON.stringify(obj,null,2)
  }
  const heartbeatDraft = computed(() => {
    try {
      return JSON.parse(runtimeText.value || '{}')
    } catch {
      return {}
    }
  })
  function setHeartbeat(key, value) {
    let draft
    try {
      draft = JSON.parse(runtimeText.value || '{}')
    } catch {
      return
    }
    draft[key] = value
    runtimeText.value = JSON.stringify(draft, null, 2)
  }
  const fileUpload = computed(() => {
    try {
      return JSON.parse(runtimeText.value || '{}').onebot_file_upload || null
    } catch {
      return null
    }
  })
  function setFileUpload(patch) {
    let draft
    try {
      draft = JSON.parse(runtimeText.value || '{}')
    } catch {
      return
    }
    if (patch === null) draft.onebot_file_upload = null
    else {
      const current = draft.onebot_file_upload || {implementation:'snowluma', version:null,
        protocol:UPLOAD_PROTOCOLS.snowluma, deployment_verified:false, export_mount_path:'/lenbot-files'}
      const next = {...current, ...patch}
      next.protocol = UPLOAD_PROTOCOLS[next.implementation] || next.protocol
      // A different implementation needs its own deployment check; a version
      // note is not a compatibility decision and does not retire that check.
      const retargeted = patch.implementation && patch.implementation !== current.implementation
      if (retargeted && patch.deployment_verified === undefined) next.deployment_verified = false
      draft.onebot_file_upload = next
    }
    runtimeText.value = JSON.stringify(draft, null, 2)
  }
  const runtimeDirty = computed(()=>runtimeText.value!==null&&runtimeText.value!==runtimeOriginal.value)
  async function saveRuntime() {
    if (busy.value||saveOutcomes.value.runtime||conflicts.entries.runtime) return
    const fresh = beginOperation('runtime')
    const progress={submitted:false,confirmed:false}
    try {
      const settings = formValues('runtime')
      const result = await saveDraft('runtime','/api/settings/runtime',settings,'PATCH',progress)
      if (!fresh()) return
      adoptSnapshot('runtime',{saved:result.settings,baseline:result.settings});
      readAt.value.runtime=Date.now()/1000
      runtimeRestart.value = result.requires_restart
      runtimeSavedBudgets.value = result.settings
      runtimeEffectiveBudgets.value = result.effective_budgets || {}
      message.value = result.message
    } catch (e) {
      await saveError(e,'runtime',fresh,progress)
    } finally {
      if(fresh())busy.value = ''
    }
  }
  return {
    runtimeText, runtimeOriginal, runtimeRestart, runtimeSavedBudgets, runtimeEffectiveBudgets,
    executionBudgets, budgetText, runtimeBudgetValue, setRuntimeBudget, heartbeatDraft,
    setHeartbeat, UPLOAD_HELP, fileUpload, setFileUpload, runtimeDirty, saveRuntime
  }
}
