import { computed, ref } from 'vue'
import { api } from '../../api.js'
import { hasConfigDraftChanges } from '../../lib/configDraft.js'
import { positiveInteger } from './fields.js'

// QQ reply whitelist and capability grants: the draft, its reference
// choices and the save. The page owns guards and the shared read flow.
export function useAccessSettings({
  busy, error, message, readAt, baselines, saveOutcomes, conflicts, beginOperation, saveDraft,
  saveError, adoptSnapshot, formValues, currentPage
}) {
  const accessText = ref(null)
  const accessOriginal = ref('')
  const grants = ref([])
  const grantsOriginal = ref('')
  const capabilities = ref([])
  const plugins = ref([])
  const scopeOptions = ref([])
  const policyOptions = ref([])
  const referenceError = ref('')
  const participantCache = ref({})
  const participantRequests = {}
  async function loadReferences(fresh) {
    try {
      const [scenes, catalog, policies] = await Promise.all([
        api('/api/cockpit/scenes'), api('/api/plugins/list'), api('/api/settings/resources')])
      if (!fresh()) return
      scopeOptions.value = scenes.scenes.map(scene=>({title:`${scene.display_name} · ${scene.scene_id}`,value:scene.scene_id}))
      plugins.value = catalog.map(item=>({title:`${item.name} · ${item.id}`,value:item.id}))
      policyOptions.value = Object.keys(policies.policies || {}).map(name=>({title:name,value:name}))
    } catch(e) {
      if(fresh())referenceError.value=e.message
    }
  }
  async function loadCapabilityChoices(fresh) {
    try {
      const vocabulary=await api('/api/settings/capabilities')
      if(fresh())capabilities.value=vocabulary.items
    }catch(e){
      if(fresh())referenceError.value=`能力声明读取失败：${e.message}`
    }
  }
  const qqUid = id => String(id||'').startsWith('user:') ? String(id).slice(5) : String(id||'')
  const participantsFor = grant => participantCache.value[grant.scene_id] || []
  // currentPage() gives the guard of the tab visit current at call time.
  async function loadParticipants(sceneId, fresh = currentPage()) {
    if (!/^group:[1-9]\d*$/.test(sceneId || '')) return
    const request = (participantRequests[sceneId] = (participantRequests[sceneId] || 0) + 1)
    try {
      const detail = await api(`/api/cockpit/scenes/${encodeURIComponent(sceneId)}`)
      if (!fresh() || request !== participantRequests[sceneId]) return
      participantCache.value = {...participantCache.value, [sceneId]: Object.entries(detail.session.participants || {})
        .map(([id,item])=>{
          const uid=qqUid(id);
          return {title:`${item.card || item.nickname || uid} · ${uid}`,value:uid}
        })}
    } catch(e) {
      if (fresh() && request === participantRequests[sceneId]) referenceError.value=e.message
    }
  }
  const capabilityItems = computed(()=>capabilities.value.map(item=>({...item,
    title:item.implemented?item.title:`${item.title}`,subtitle:item.value})))
  const grantCapabilities = grant => Array.isArray(grant.capabilities) ? grant.capabilities.filter(Boolean) : []
  const toLocalInput = seconds => {
    if (seconds === null || seconds === undefined) return ''
    const date = new Date(seconds*1000)
    if(Number.isNaN(date.getTime()))return ''
    const pad = value => String(value).padStart(2,'0')
    return `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
  }
  const fromLocalInput = value => value ? new Date(value).getTime()/1000 : null
  const grantExpiry = grant => grant.expiresInput === toLocalInput(grant.expires_at)
    ? (grant.expires_at ?? null) : fromLocalInput(grant.expiresInput)
  const expiryPreview = index => {
    const seconds = grantExpiry(grants.value[index] || {})
    if (seconds===null) return '长期有效'
    if (!Number.isFinite(seconds)) return '有效期格式无效，未改为长期有效，请修正后再保存'
    const date=new Date(seconds*1000)
    if(Number.isNaN(date.getTime()))return `到期值 ${seconds} 超出日期控件可显示范围；未修改时保留原值`
    return `保存后为 ${date.toISOString()}（UTC）`
  }
  const accessProblems = ref([])
  const grantCardProblems = index => accessProblems.value.filter(item=>item.key===`grant:${index}` || item.key.endsWith(`:${index}`))
  const accessDirty = computed(()=>accessText.value!==null&&(accessText.value!==accessOriginal.value||JSON.stringify(grants.value)!==grantsOriginal.value))
  const grantFields = grant => ({grant_id:grant.grant_id||'',principal_type:grant.principal_type,principal_id:grant.principal_id,
    scene_id:grant.scene_id??null,system_scope:grant.system_scope??null,capabilities:[...grant.capabilities],
    expires_at:grant.expires_at??null,resource_policy:grant.resource_policy||null,concurrency:grant.concurrency??null,enabled:!!grant.enabled})
  const grantEditor = grant => ({
    ...grantFields(grant),
    revision:grant.revision,
    operator_id:grant.operator_id,
    expiresInput:toLocalInput(grant.expires_at)
  })
  const editableAccess = settings => ({
    qq_reply_whitelist:[...settings.qq_reply_whitelist],
    capability_grants:settings.capability_grants.map(grantFields)
  })
  function editedGrant(grant) {
    const expires_at=grantExpiry(grant)
    if(expires_at!==null&&!Number.isFinite(expires_at))throw new Error('授予有效期不是有效时间；原草稿保留，未改为长期有效。')
    return grantFields({...grant,
      principal_id:qqUid(grant.principal_id&&typeof grant.principal_id==='object'?grant.principal_id.value:grant.principal_id),
      scene_id:grant.principal_type==='system'?null:(grant.scene_id||null),system_scope:grant.principal_type==='system'?(grant.system_scope||null):null,
      capabilities:grantCapabilities(grant),expires_at,concurrency:grant.concurrency===''?null:grant.concurrency})
  }
  function withCurrentGrants(values, current) {
    const byId=new Map(current.capability_grants.map(grant=>[grant.grant_id,grant]))
    return {...values,capability_grants:values.capability_grants.map(grant=>{
      if(!grant.grant_id)return {...grant,revision:1,operator_id:''}
      const saved=byId.get(grant.grant_id)
      if(!saved)throw new Error(`授予 ${grant.grant_id} 已不在当前列表，不能将旧身份改成新授予。请从草稿移除该项，或采用现值后明确重新添加。`)
      if(!Number.isInteger(saved.revision)||saved.revision<1)throw new Error(`授予 ${grant.grant_id} 未提供有效修订；未重建基线，请重新读取。`)
      return {...grant,revision:saved.revision,operator_id:saved.operator_id}
    })}
  }
  async function saveAccess() {
    if (busy.value||saveOutcomes.value.access||conflicts.entries.access) return
    const problems=[]
    accessText.value.split(/[,，\s]+/).filter(Boolean).forEach((value,index)=>{
      try {
        positiveInteger(value,'QQ 账号')
      } catch(e) {
        problems.push({key:'whitelist',message:`第 ${index+1} 项：${e.message}（${value}）`})
      }
    })
    grants.value.forEach((grant,index)=>{
      if (!grant.principal_type) return
      if (!String(grant.principal_id||'').trim()) problems.push({key:`principal_id:${index}`,message:`第 ${index+1} 条授予：主体标识未填写`})
      if (grant.principal_type==='system'&&!String(grant.system_scope||'').trim()) problems.push({key:`system_scope:${index}`,message:`第 ${index+1} 条授予：系统授予必须填写系统范围`})
      if (grant.principal_type!=='system'&&!String(grant.scene_id||'').trim()) problems.push({key:`scene_id:${index}`,message:`第 ${index+1} 条授予：非系统授予必须填写场景`})
      if (!grantCapabilities(grant).length) problems.push({key:`capability:${index}`,message:`第 ${index+1} 条授予：未选择任何能力`})
      if (grant.expiresInput&&Number.isNaN(new Date(grant.expiresInput).getTime())) problems.push({key:`expires:${index}`,message:`第 ${index+1} 条授予：有效期不是有效时间`})
    })
    accessProblems.value=problems
    if (problems.length) {
      error.value='QQ 回复资格尚未通过本地检查，未提交保存；请修正下列字段后重试。'
      document.querySelector(`[data-field="${problems[0].key}"]`)?.scrollIntoView({block:'center'})
      return
    }
    const fresh = beginOperation('access')
    const progress={submitted:false,confirmed:false}
    try {
      const values=formValues('access'),previous=editableAccess(baselines.value.access)
      const editedGrants=values.capability_grants.map((grant,index)=>{
        const revision=grants.value[index].revision
        if(!Number.isInteger(revision)||revision<1)throw new Error(`第 ${index+1} 条授予缺少有效修订，请核对已保存值；没有按初始修订猜测。`)
        return {...grant,revision}
      })
      const result = await saveDraft('access','/api/settings/access',{
        ...(hasConfigDraftChanges(previous.qq_reply_whitelist,values.qq_reply_whitelist)?{qq_reply_whitelist:values.qq_reply_whitelist}:{}),
        ...(hasConfigDraftChanges(previous.capability_grants,values.capability_grants)?{capability_grants:editedGrants}:{})},'PUT',progress)
      if (!fresh()) return
      adoptSnapshot('access',{saved:result.settings,baseline:result.settings});
      readAt.value.access=Date.now()/1000;
      message.value=result.message
    } catch(e) {
      if (!fresh()) return
      accessProblems.value=(Array.isArray(e.details)?e.details:[]).map(item=>{
        const parts=(item.loc||[]).filter(part=>part!=='body'&&part!=='capability_grants')
        if (typeof parts[0]==='number') {
          const field=parts[1]==='capabilities'?'capability':(parts[1]||'grant')
          return {
            key:`${field}:${parts[0]}`,
            message:`第 ${parts[0]+1} 条授予 · ${parts.slice(1).join(' → ')||'字段'}：${item.msg}`
          }
        }
        return {
          key:parts[0]==='qq_reply_whitelist'?'whitelist':'',
          message:`${parts.join(' → ')||'提交内容'}：${item.msg}`
        }
      })
      await saveError(e,'access',fresh,progress)
    } finally {
      if(fresh())busy.value=''
    }
  }
  function addGrant() {
    grants.value.push(grantEditor({grant_id:'',revision:1,operator_id:'',principal_type:'human',principal_id:'',
      scene_id:null,system_scope:null,capabilities:[],expires_at:null,resource_policy:null,concurrency:null,enabled:false}))
  }
  const grantImpact = computed(()=>grants.value.filter(grant=>grant.enabled&&grantCapabilities(grant).length)
    .map(grant=>`${grant.principal_type==='system'?grant.system_scope:grant.scene_id||'未指定范围'} · ${grant.principal_id} · ${grantCapabilities(grant).join('、')}`))
  return {
    accessText, accessOriginal, grants, grantsOriginal, plugins, scopeOptions, policyOptions,
    referenceError, loadReferences, loadCapabilityChoices, participantsFor, loadParticipants,
    capabilityItems, expiryPreview, accessProblems, grantCardProblems, accessDirty, grantEditor,
    editableAccess, editedGrant, withCurrentGrants, saveAccess, addGrant, grantImpact
  }
}
