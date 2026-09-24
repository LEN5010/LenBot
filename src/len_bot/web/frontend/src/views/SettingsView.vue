<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, fmtTime } from '../api.js'
import { logout, useAuth } from '../composables/useAuth.js'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'
import { useConfigConflicts } from '../composables/useConfigConflicts.js'
import { hasConfigDraftChanges, rebaseConfigDraft } from '../lib/configDraft.js'
import ConfigConflictBanner from '../components/ConfigConflictBanner.vue'
import AdvancedSection from '../components/AdvancedSection.vue'
import HelpHint from '../components/HelpHint.vue'
import PageHeader from '../components/PageHeader.vue'
import ResourceViewer from '../components/ResourceViewer.vue'
const route = useRoute()
const router = useRouter()
const tabs = [{value:'access',title:'QQ 回复资格'},{value:'resources',title:'额度策略'},{value:'members',title:'主播与订阅对象'},{value:'connection',title:'连接'},{value:'delivery',title:'发送'},{value:'runtime',title:'运行参数'},{value:'account',title:'账户'}]
const tab = computed(() => tabs.some(item=>item.value===route.query.tab) ? route.query.tab : 'connection')
const baselines = ref({})
const conflicts = useConfigConflicts()
const currentConflict = computed(()=>conflicts.entries[tab.value])
const saveOutcomes=ref({}), currentSaveOutcome=computed(()=>saveOutcomes.value[tab.value])
const clone = value => JSON.parse(JSON.stringify(value))
async function saveDraft(domain,path,values,method,progress) {
  const body=JSON.stringify({baseline:baselines.value[domain],values})
  progress.submitted=true
  const result=await api(path,{method,body})
  if(!result||result.config_saved!==true||typeof result.message!=='string'||
    (domain==='connection'?(result.success!==true||typeof result.requires_restart!=='boolean'):!Object.hasOwn(result,'settings')))
    throw new Error('配置响应缺少本次保存所需字段，结果待核对，未采用新基线。')
  progress.confirmed=true
  return result
}
const loading = ref(false)
const error = ref('')
const message = ref('')
const readAt = ref({})
const busy = ref('')
const selection = () => `${route.name}:${tab.value}`
const readGuard = useRequestGuard(selection), operationGuard = useRequestGuard(selection), pageGuard = useRequestGuard(selection)
let currentPage = () => false
const connectionNeedsReadback = ref(false)
function beginOperation(kind) {
  const fresh = operationGuard()
  readGuard(); loading.value = false
  busy.value = kind; error.value = ''; message.value = ''
  return fresh
}
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
  try { const obj=JSON.parse(runtimeText.value||'{}'); return obj[key] ?? '' } catch { return '' }
}
const setRuntimeBudget = (key, value) => {
  let obj
  try { obj=JSON.parse(runtimeText.value||'{}') } catch { return }
  if (value==='' || value===null || value===undefined) obj[key]=null
  else {
    const text=String(value).trim()
    if (!/^-?\d+(\.\d+)?$/.test(text)) return
    obj[key]=Number(text)
  }
  runtimeText.value=JSON.stringify(obj,null,2)
}
const onebot = ref(null)
const platform = ref(null)
const connection = ref(null)
const connectionOriginal = ref('')
const shadow = ref(null)
const heartbeatDraft = computed(() => {
  try { return JSON.parse(runtimeText.value || '{}') } catch { return {} }
})
function setHeartbeat(key, value) {
  let draft
  try { draft = JSON.parse(runtimeText.value || '{}') } catch { return }
  draft[key] = value
  runtimeText.value = JSON.stringify(draft, null, 2)
}
// The backend refuses a mismatched pair outright, so the panel derives the
// config/receipt label instead of offering it as a second thing to get wrong.
const UPLOAD_PROTOCOLS = {napcat:'upload_group_file_data_file_id', snowluma:'upload_group_file'}
const UPLOAD_HELP = `implementation 决定内部配置／回执标签：napcat 用 upload_group_file_data_file_id，snowluma 用 upload_group_file。两者实际都发送 upload_group_file，不切换实现或重传。

version 可选，仅记录当前连接报告的版本；不按 SnowLuma 或 NapCat 的具体发行版本准入。

deployment_verified 表示你已人工核对所选实现、upload_group_file 动作与「仅文件资产目录只读挂到 /lenbot-files」；不要求先有过一次成功上传。真实 file_id 只从 FILE_UPLOADED 回执派生。

改动保存后需重启。`
const fileUpload = computed(() => {
  try { return JSON.parse(runtimeText.value || '{}').onebot_file_upload || null } catch { return null }
})
function setFileUpload(patch) {
  let draft
  try { draft = JSON.parse(runtimeText.value || '{}') } catch { return }
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
const RESET_HELP = `Reset 是独立的破坏性管理动作，需要当次明确授权。

会删除：全部群聊和私聊的原话与会话、摘要与自动认识、自动技能与其候选、工作与检查点、任务与等待、工具资料、调用账、聊天图片和场景上下文。执行前先停止认知、维护、工作和投递，并在管理记录里追加一条操作事件。

会保留：登录、根配置、人工表达样例、运营表情库及其来源、人格、Shadow、QQ 回复白名单、各群设置与能力授予。人工样例只重置使用计数。

不处理：usage_reservations、execution_runs、execution_events 不在清理表列表内。既有额度预占和执行记录会留下，页面也不能证明外部容器已经结束；这些行需要另行核对归属与处置。`
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
  } catch(e) { if(fresh())referenceError.value=e.message }
}
async function loadCapabilityChoices(fresh) {
  try {
    const vocabulary=await api('/api/settings/capabilities')
    if(fresh())capabilities.value=vocabulary.items
  }catch(e){if(fresh())referenceError.value=`能力声明读取失败：${e.message}`}
}
const qqUid = id => String(id||'').startsWith('user:') ? String(id).slice(5) : String(id||'')
const participantsFor = grant => participantCache.value[grant.scene_id] || []
async function loadParticipants(sceneId, fresh = currentPage) {
  if (!/^group:[1-9]\d*$/.test(sceneId || '')) return
  const request = (participantRequests[sceneId] = (participantRequests[sceneId] || 0) + 1)
  try {
    const detail = await api(`/api/cockpit/scenes/${encodeURIComponent(sceneId)}`)
    if (!fresh() || request !== participantRequests[sceneId]) return
    participantCache.value = {...participantCache.value, [sceneId]: Object.entries(detail.session.participants || {})
      .map(([id,item])=>{ const uid=qqUid(id); return {title:`${item.card || item.nickname || uid} · ${uid}`,value:uid} })}
  } catch(e) { if (fresh() && request === participantRequests[sceneId]) referenceError.value=e.message }
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
const quotaText = ref(null)
const quotaOriginal = ref('')
const quotaDirty = computed(()=>quotaText.value!==null&&JSON.stringify(quotaRows.value)!==quotaOriginal.value)
const policyRows = policies => Object.entries(policies || {}).map(([name,item])=>({name,
  work:item.work_token_limit ?? null, user:item.daily_user_token_limit ?? null, scene:item.daily_scene_token_limit ?? null}))
const quotaRows = ref([])
const quotaProblems = ref([])
const quotaRaw = ref(false)
const policyEditable = item => item && ['work_token_limit','daily_user_token_limit','daily_scene_token_limit']
  .every(key=>item[key]===undefined || item[key]===null || Number.isInteger(item[key]))
const rawPolicies = computed(()=>Object.fromEntries(Object.entries(quotaRecord.value).filter(([,item])=>!policyEditable(item))))
const quotaRecord = ref({})
const quotaProblemsFor = index => quotaProblems.value.filter(item=>item.key===`policy:${index}`)
const focusPolicy = key => document.querySelector(`[data-policy="${key}"]`)?.scrollIntoView({block:'center',behavior:'smooth'})
const toggleQuotaRaw = () => { quotaRaw.value=!quotaRaw.value }
const quotaNumber = (value,label,key,{minimum=0}={}) => {
  if (value===null || value===undefined || value==='') return null
  const text = String(value).trim()
  if (!/^-?\d+$/.test(text)) {
    quotaProblems.value=[...quotaProblems.value,{key, message:`${label}：请填写整数，不能截断小数或改写非法值`}]
    return undefined
  }
  const number = Number(text)
  if (!Number.isSafeInteger(number) || number<minimum) {
    quotaProblems.value=[...quotaProblems.value,{key,
      message:minimum>0?`${label}：请填 ${minimum} 或更大的整数；不设该维度上限请留空`
        :`${label}：请填 0 或更大的整数；留空表示不设该维度上限`}]
    return undefined
  }
  return number
}
const members = ref(null)
const membersOriginal = ref('')
const membersRestart = ref(false)
const me = ref(null)
const passwords = ref({current_password:'',new_password:''})
const leavingAfterLogout = ref(false)
const resetConfirm = ref(false)
const runtimeDirty = computed(()=>runtimeText.value!==null&&runtimeText.value!==runtimeOriginal.value)
const connectionDirty = computed(()=>!!connection.value&&JSON.stringify(connection.value)!==connectionOriginal.value)
const accessDirty = computed(()=>accessText.value!==null&&(accessText.value!==accessOriginal.value||JSON.stringify(grants.value)!==grantsOriginal.value))
const membersDirty = computed(()=>members.value!==null&&JSON.stringify(members.value)!==membersOriginal.value)
const dirty = computed(()=>!leavingAfterLogout.value&&(runtimeDirty.value||connectionDirty.value||accessDirty.value||quotaDirty.value||membersDirty.value||!!passwords.value.current_password||!!passwords.value.new_password))
const { confirmLeave } = useUnsavedChanges(dirty)
const connectionForm = settings => ({connection_mode:settings.connection_mode,action_transport:settings.action_transport,
  ws_url:settings.ws_url,http_url:settings.http_url,host:settings.host,port:settings.port,access_token:'',access_token_action:'keep'})
const grantFields = grant => ({grant_id:grant.grant_id||'',principal_type:grant.principal_type,principal_id:grant.principal_id,
  scene_id:grant.scene_id??null,system_scope:grant.system_scope??null,capabilities:[...grant.capabilities],
  expires_at:grant.expires_at??null,resource_policy:grant.resource_policy||null,concurrency:grant.concurrency??null,enabled:!!grant.enabled})
const grantEditor = grant => ({...grantFields(grant),revision:grant.revision,operator_id:grant.operator_id,expiresInput:toLocalInput(grant.expires_at)})
const editableAccess = settings => ({qq_reply_whitelist:[...settings.qq_reply_whitelist],capability_grants:settings.capability_grants.map(grantFields)})
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
function quotaValues() {
  quotaProblems.value=[]
  const policies=new Map(Object.entries(rawPolicies.value))
  for(const [index,row] of quotaRows.value.entries()) {
    const name=row.name.trim()
    if(!name){quotaProblems.value=[{key:`policy:${index}`,message:`第 ${index+1} 项：策略名不能为空`}];return null}
    if(policies.has(name)){quotaProblems.value=[{key:`policy:${index}`,message:`第 ${index+1} 项：策略名“${name}”已有同名策略`}];return null}
    const work=quotaNumber(row.work,'单工作累计 token',`policy:${index}`,{minimum:1})
    const user=quotaNumber(row.user,'主体日额度',`policy:${index}`),scene=quotaNumber(row.scene,'群日额度',`policy:${index}`)
    if(quotaProblems.value.length||work===undefined||user===undefined||scene===undefined)return null
    policies.set(name,{work_token_limit:work,daily_user_token_limit:user,daily_scene_token_limit:scene})
  }
  return {policies:Object.fromEntries(policies)}
}
function formValues(domain) {
  if(domain==='access')return {qq_reply_whitelist:accessText.value.split(/[,，\s]+/).filter(Boolean).map(value=>positiveInteger(value,'QQ 账号')),capability_grants:grants.value.map(editedGrant)}
  if(domain==='resources')return quotaValues()
  if(domain==='members')return members.value.map(item=>({name:item.name.trim(),aliases:item.aliasText.split(/[\n,，、]+/).map(value=>value.trim()).filter(Boolean),bilibili_uid:positiveInteger(item.bilibili_uid,'B 站 UID'),room_id:positiveInteger(item.room_id,'直播间号')}))
  if(domain==='connection')return clone(connection.value)
  const settings=JSON.parse(runtimeText.value)
  if(settings===null||Array.isArray(settings)||typeof settings!=='object')throw new Error('运行参数须填写 JSON 对象')
  return settings
}
function writeForm(domain, values) {
  if(domain==='access'){accessText.value=values.qq_reply_whitelist.join('\n');grants.value=values.capability_grants.map(grantEditor)}
  else if(domain==='resources')quotaRows.value=policyRows(values.policies)
  else if(domain==='members')members.value=values.map(item=>({...item,aliases:[...item.aliases],aliasText:item.aliases.join('、')}))
  else if(domain==='connection')connection.value=clone(values)
  else runtimeText.value=JSON.stringify(values,null,2)
}
function adoptSnapshot(domain, snapshot) {
  writeForm(domain,domain==='connection'?connectionForm(snapshot.saved):snapshot.saved)
  baselines.value[domain]=clone(snapshot.baseline)
  if(domain==='access'){accessOriginal.value=accessText.value;grantsOriginal.value=JSON.stringify(grants.value);accessProblems.value=[]}
  else if(domain==='resources'){
    quotaRecord.value=clone(snapshot.saved.policies);quotaText.value=JSON.stringify(snapshot.saved.policies,null,2)
    quotaOriginal.value=JSON.stringify(quotaRows.value);quotaProblems.value=[]
  } else if(domain==='members')membersOriginal.value=JSON.stringify(members.value)
  else if(domain==='connection'){connectionOriginal.value=JSON.stringify(connection.value);connectionNeedsReadback.value=false}
  else runtimeOriginal.value=runtimeText.value
}
function captureSaveOutcome(domain,snapshot) {
  const outcome=saveOutcomes.value[domain]
  if(!outcome)return false
  outcome.snapshot=snapshot;outcome.readAt=Date.now()/1000
  return true
}
async function saveError(problem, domain, fresh, progress) {
  if(!fresh())return
  if(problem.details?.config_saved===false&&conflicts.mark(domain,problem)){
    await load({accept:fresh});return
  }
  const rejected=problem.details?.config_saved===false || (problem.status===422&&Array.isArray(problem.details))
  if(progress.submitted&&!rejected){
    saveOutcomes.value[domain]={confirmed:progress.confirmed||problem.details?.config_saved===true,
      message:problem.message,snapshot:null,readAt:null}
    await load({accept:fresh})
    if(!fresh())return
  }
  error.value=problem.message+(error.value?'；读取当前值：'+error.value:'')
}
function adoptSaveOutcome() {
  const domain=tab.value, outcome=currentSaveOutcome.value
  if(busy.value||loading.value||!outcome?.snapshot)return
  if(!window.confirm('放弃本标签原草稿（包括未确认的令牌替换或新增授予），采用本次读取值继续编辑？这不重发、撤销或追认原请求，也不重试运行应用。'))return
  try {
    adoptSnapshot(domain,outcome.snapshot)
    delete saveOutcomes.value[domain];conflicts.clear(domain);error.value=''
    message.value='已采用当前保存值继续编辑；没有再次保存或重试应用。原操作结果仍按原回执与运行记录核对。'
  }catch(problem){error.value=problem.message}
}
function resolveConflict(keep) {
  const domain=tab.value,snapshot=currentConflict.value?.snapshot
  if(busy.value||loading.value||currentSaveOutcome.value||!snapshot)return
  if(!keep&&!window.confirm('放弃本标签未保存的配置，采用本次读取的保存值？其他标签草稿不变。'))return
  try {
    let next
    if(keep){
      const own=formValues(domain)
      if(domain==='resources'&&own===null)throw new Error('请先修正额度策略字段，再选择保留改动；原草稿与基线未变。')
      const previous=domain==='access'?editableAccess(baselines.value.access):domain==='connection'?JSON.parse(connectionOriginal.value):baselines.value[domain]
      const current=domain==='access'?editableAccess(snapshot.saved):domain==='connection'?connectionForm(snapshot.saved):snapshot.saved
      next=rebaseConfigDraft(previous,own,current)
      if(domain==='access')next=withCurrentGrants(next,snapshot.saved)
    }
    adoptSnapshot(domain,snapshot)
    if(keep)writeForm(domain,next)
    conflicts.clear(domain);error.value=''
    message.value=keep?'已保留实际改动，其余字段采用现值；请核对后保存本标签配置。':'已采用本次读取的保存值，没有再次保存。'
  }catch(problem){error.value=problem.message}
}
async function load({accept=()=>true}={}) {
  const currentTab = tab.value, current = readGuard(), fresh=()=>current()&&accept()
  loading.value = true; error.value = ''
  conflicts.beginRead(currentTab)
  if(saveOutcomes.value[currentTab]){saveOutcomes.value[currentTab].snapshot=null;saveOutcomes.value[currentTab].readAt=null}
  try {
    if (currentTab==='access') {
      referenceError.value = ''
      const [snapshot] = await Promise.all([api('/api/settings/draft/access'),loadCapabilityChoices(fresh),loadReferences(fresh)])
      if (!fresh()) return
      if (!captureSaveOutcome('access',snapshot)&&!conflicts.capture('access',snapshot)&&!accessDirty.value)adoptSnapshot('access',snapshot)
      await Promise.all([...new Set(grants.value.filter(grant=>grant.principal_type!=='system').map(grant=>grant.scene_id))]
        .map(sceneId=>loadParticipants(sceneId, fresh)))
    } else if (currentTab==='resources') {
      const snapshot = await api('/api/settings/draft/resources'); if (!fresh()) return
      if (!captureSaveOutcome('resources',snapshot)&&!conflicts.capture('resources',snapshot)&&!quotaDirty.value)adoptSnapshot('resources',snapshot)
    } else if (currentTab==='members') {
      const snapshot = await api('/api/settings/draft/members'); if (!fresh()) return
      membersRestart.value=snapshot.requires_restart
      if (!captureSaveOutcome('members',snapshot)&&!conflicts.capture('members',snapshot)&&!membersDirty.value)adoptSnapshot('members',snapshot)
    } else if (currentTab==='connection') {
      const settings = await api('/api/websocket/status'); if (!fresh()) return
      onebot.value=settings
      const snapshot={saved:settings,baseline:settings}
      if (!captureSaveOutcome('connection',snapshot)&&!conflicts.capture('connection',snapshot)&&(!connectionDirty.value||connectionNeedsReadback.value))adoptSnapshot('connection',snapshot)
    } else if (currentTab==='delivery') {
      const settings=await api('/api/cockpit/shadow'); if(!fresh())return
      shadow.value=settings
    } else if (currentTab==='runtime') {
      const snapshot=await api('/api/settings/draft/runtime');if(!fresh())return
      if(!captureSaveOutcome('runtime',snapshot)&&!conflicts.capture('runtime',snapshot)&&!runtimeDirty.value)adoptSnapshot('runtime',snapshot)
      runtimeRestart.value=snapshot.requires_restart
      runtimeSavedBudgets.value=snapshot.saved
      runtimeEffectiveBudgets.value=snapshot.effective || {}
    } else {
      const result=await api('/api/auth/me');if(!fresh())return;me.value=result
    }
    if(fresh())readAt.value[currentTab]=Date.now()/1000
  } catch(e){if(fresh()){error.value=e.message;conflicts.readFailed(currentTab,e)}}
  finally{if(fresh())loading.value=false}
}
function addMember() {
  members.value.push({name:'',aliases:[],aliasText:'',bilibili_uid:null,room_id:null})
}
function positiveInteger(value, label) {
  const text = String(value ?? '').trim()
  const number = Number(text)
  if (!/^[1-9]\d*$/.test(text) || !Number.isSafeInteger(number)) throw new Error(`${label}须填写有效正整数`)
  return number
}
async function saveAccess() {
  if (busy.value||saveOutcomes.value.access||conflicts.entries.access) return
  const problems=[]
  accessText.value.split(/[,，\s]+/).filter(Boolean).forEach((value,index)=>{
    try { positiveInteger(value,'QQ 账号') } catch(e) { problems.push({key:'whitelist',message:`第 ${index+1} 项：${e.message}（${value}）`}) }
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
    adoptSnapshot('access',{saved:result.settings,baseline:result.settings});readAt.value.access=Date.now()/1000;message.value=result.message
  } catch(e) {
    if (!fresh()) return
    accessProblems.value=(Array.isArray(e.details)?e.details:[]).map(item=>{
      const parts=(item.loc||[]).filter(part=>part!=='body'&&part!=='capability_grants')
      if (typeof parts[0]==='number') {
        const field=parts[1]==='capabilities'?'capability':(parts[1]||'grant')
        return {key:`${field}:${parts[0]}`,message:`第 ${parts[0]+1} 条授予 · ${parts.slice(1).join(' → ')||'字段'}：${item.msg}`}
      }
      return {key:parts[0]==='qq_reply_whitelist'?'whitelist':'',message:`${parts.join(' → ')||'提交内容'}：${item.msg}`}
    })
    await saveError(e,'access',fresh,progress)
  } finally { if(fresh())busy.value='' }
}
function addGrant() {
  grants.value.push(grantEditor({grant_id:'',revision:1,operator_id:'',principal_type:'human',principal_id:'',
    scene_id:null,system_scope:null,capabilities:[],expires_at:null,resource_policy:null,concurrency:null,enabled:false}))
}
const grantImpact = computed(()=>grants.value.filter(grant=>grant.enabled&&grantCapabilities(grant).length)
  .map(grant=>`${grant.principal_type==='system'?grant.system_scope:grant.scene_id||'未指定范围'} · ${grant.principal_id} · ${grantCapabilities(grant).join('、')}`))
async function saveQuota() {
  if (busy.value||saveOutcomes.value.resources||conflicts.entries.resources) return
  const fresh = beginOperation('resources'); quotaProblems.value=[]
  const progress={submitted:false,confirmed:false}
  try {
    const values=quotaValues()
    if(values===null)return
    const result = await saveDraft('resources','/api/settings/resources',values,'PUT',progress)
    if (!fresh()) return
    adoptSnapshot('resources',{saved:result.settings,baseline:result.settings});readAt.value.resources=Date.now()/1000;message.value=result.message
  } catch(e) {
    if (!fresh()) return
    // A server rejection names the policy it belongs to; the same sentence is
    // shown beside that policy with a position the operator can act on.
    quotaProblems.value=(Array.isArray(e.details)?e.details:[]).map(item=>{
      const name=(item.loc||[]).filter(part=>typeof part==='string'&&part!=='body'&&part!=='policies')[0]
      const index=quotaRows.value.findIndex(row=>row.name.trim()===name)
      return {key:index>=0?`policy:${index}`:'',message:`${name?`策略“${name}” · `:''}${item.msg}`}
    })
    await saveError(e,'resources',fresh,progress)
  } finally { if(fresh())busy.value='' }
}
async function saveMembers() {
  if (busy.value || saveOutcomes.value.members||conflicts.entries.members || !members.value) return
  const fresh = beginOperation('members')
  const progress={submitted:false,confirmed:false}
  try {
    const values=formValues('members')
    const result=await saveDraft('members','/api/settings/members',values,'PUT',progress)
    if (!fresh()) return
    adoptSnapshot('members',{saved:result.settings,baseline:result.settings});readAt.value.members=Date.now()/1000;membersRestart.value=result.requires_restart;message.value=result.message
  } catch(e) { await saveError(e,'members',fresh,progress) } finally { if(fresh())busy.value='' }
}
async function saveRuntime() {
  if (busy.value||saveOutcomes.value.runtime||conflicts.entries.runtime) return
  const fresh = beginOperation('runtime')
  const progress={submitted:false,confirmed:false}
  try {
    const settings = formValues('runtime')
    const result = await saveDraft('runtime','/api/settings/runtime',settings,'PATCH',progress)
    if (!fresh()) return
    adoptSnapshot('runtime',{saved:result.settings,baseline:result.settings});readAt.value.runtime=Date.now()/1000
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
async function saveConnection() {
  if (busy.value || connectionNeedsReadback.value || saveOutcomes.value.connection||conflicts.entries.connection) return
  const fresh = beginOperation('connection')
  const progress={submitted:false,confirmed:false}
  try {
    const body = {...connection.value, access_token:connection.value.access_token.trim() || null}
    const result = await saveDraft('connection','/api/websocket/config',body,'POST',progress)
    if (!fresh()) return
    connectionNeedsReadback.value = true
    message.value = result.requires_restart ? `${result.message}；请手动重启服务后使用新连接配置` : result.message
    await load({accept:fresh})
  } catch (e) {
    await saveError(e,'connection',fresh,progress)
  } finally {
    if(fresh())busy.value = ''
  }
}
async function checkHttp() {
  if (busy.value) return
  const fresh = beginOperation('http')
  try {
    const result = await api('/api/websocket/test-http', {method:'POST'})
    if(fresh())message.value = `当前运行连接：${result.message}`
  } catch (e) {
    if(fresh())error.value = e.message
  } finally {
    if(fresh())busy.value = ''
  }
}
async function readVersion() {
  if (busy.value) return
  const fresh = beginOperation('version')
  platform.value = null
  try {
    const result = await api('/api/websocket/read-version', {method:'POST'})
    if(fresh())platform.value = result
  } catch (e) {
    if(fresh())error.value = e.message
  } finally {
    if(fresh())busy.value = ''
  }
}
async function toggleShadow() {
  if(busy.value||!shadow.value)return
  const enabled=!shadow.value.enabled
  if(!window.confirm(enabled?'开启 Shadow？保留原观察行为，但不再真实发送消息。':'关闭 Shadow？机器人将按各群当前启用、聊天、命令和公告配置实际发送。'))return
  const fresh = beginOperation('shadow')
  try{
    const result=await api('/api/cockpit/shadow/toggle',{method:'POST',body:JSON.stringify({enabled})})
    if (!fresh()) return
    shadow.value={...shadow.value,enabled:result.shadow_mode};message.value=result.shadow_mode?'已开启 Shadow，不实际发送':'已关闭 Shadow，按当前群规则发送'
  }catch(e){if(fresh())error.value=e.message}finally{if(fresh())busy.value=''}
}
async function changePassword() {
  if(busy.value)return
  const fresh = beginOperation('password')
  try{await api('/api/auth/change_password',{method:'POST',body:JSON.stringify(passwords.value)});if(!fresh())return;passwords.value={current_password:'',new_password:''};message.value='访问密码已修改';await load()}
  catch(e){if(fresh())error.value=e.message}finally{if(fresh())busy.value=''}
}
async function signOut() {
  if(busy.value||!confirmLeave())return
  const fresh = beginOperation('logout')
  try{await logout();if(useAuth().status!=='unauthenticated')return;leavingAfterLogout.value=true;await router.replace({name:'login'})}catch(e){if(fresh())error.value=e.message}finally{if(fresh())busy.value=''}
}
async function resetData(){
  if(busy.value)return
  const fresh = beginOperation('reset')
  try{await api('/api/settings/reset',{method:'POST'});if(!fresh())return;resetConfirm.value=false;message.value='对话数据已按上面列出的范围清空；配置与运营资料保留，额度预占和执行记录仍待单独核对';await load()}
  catch(e){if(fresh())error.value=e.message}finally{if(fresh())busy.value=''}
}
watch(tab,()=>{
  operationGuard(); currentPage=pageGuard(); busy.value=''; message.value=''; referenceError.value=''; resetConfirm.value=false
  if(route.name==='settings')load()
},{immediate:true,flush:'sync'})
</script>
<template>
  <div class="page-stack settings-view">
    <PageHeader title="系统设置" description="各配置节分别保存到根参数文件，刷新保留未保存的草稿。"><v-btn variant="outlined" :loading="loading" :disabled="!!busy" @click="load">刷新当前设置</v-btn></PageHeader>
    <v-alert v-if="error" type="error" variant="tonal">{{ error }}<span v-if="readAt[tab]"> · 上次读取 {{ fmtTime(readAt[tab]) }}</span></v-alert><v-alert v-if="message" type="success" variant="tonal" closable @click:close="message=''">{{ message }}</v-alert>
    <p class="muted">切换标签保留本页配置草稿，但不继续跟踪旧操作；服务器可能已保存，请回到原标签刷新核对后再提交。</p>
    <v-alert v-if="tab==='connection'&&connectionNeedsReadback" type="warning" variant="tonal">连接配置已写入，但保存值尚未读回；请刷新核对后再编辑，不要重复提交令牌。</v-alert>
    <v-alert v-if="currentSaveOutcome" type="warning" variant="tonal">
      <p>{{ currentSaveOutcome.confirmed?'已取得写入确认，但后续结果仍需核对。':'本次配置保存结果未知。' }}{{ currentSaveOutcome.message }}不要重复提交原草稿，尤其是令牌替换或新增授予。</p>
      <p v-if="currentSaveOutcome.snapshot">当前保存值读取于 {{ fmtTime(currentSaveOutcome.readAt) }}；这不是原操作回执。</p>
      <ResourceViewer v-if="currentSaveOutcome.snapshot" title="当前读取值（不是原草稿）" :content="currentSaveOutcome.snapshot.saved" />
      <v-btn variant="text" :disabled="!!busy||loading" @click="load">读取当前保存值</v-btn>
      <v-btn variant="text" :disabled="!!busy||loading||!currentSaveOutcome.snapshot" @click="adoptSaveOutcome">采用当前值继续编辑</v-btn>
    </v-alert>
    <ConfigConflictBanner :conflict="currentConflict?.problem" :current="currentConflict?.snapshot" :path-label="currentConflict?.problem.path?.join('.')" :busy="!!busy||loading||!!currentSaveOutcome" :read-error="currentConflict?.readError" :read-at="currentConflict?.readAt" @keep="resolveConflict(true)" @take="resolveConflict(false)" @reload="load"><template #current><p v-if="currentConflict?.snapshot?.saved===null">本次读取明确为未配置，不是读取失败。</p><ResourceViewer v-else title="本次读取的保存值（不是草稿）" :content="currentConflict?.snapshot?.saved" /></template></ConfigConflictBanner>
    <p v-if="tab==='access'&&currentConflict" class="muted">白名单与授予列表按整组核对。明确保留后，仍使用当前授予 ID 和修订；已经删除的旧 ID 不会被改成新授予重新签发。</p>
    <v-tabs :model-value="tab" color="primary" show-arrows @update:model-value="value=>router.push({name:'settings',query:{tab:value}})"><v-tab v-for="item in tabs" :key="item.value" :value="item.value">{{ item.title }}<span v-if="conflicts.entries[item.value]"> · 待处理冲突</span><span v-if="saveOutcomes[item.value]"> · 保存待核对</span></v-tab></v-tabs>
    <v-progress-linear v-if="loading" indeterminate />
    <v-card v-if="tab==='connection'&&onebot&&connection" class="pa-5 form-card"><div class="section-header"><h2>连接 OneBot</h2><v-chip :color="onebot.connected?'success':'warning'">{{ onebot.connected?'已连接':'未连接' }}</v-chip></div><p class="muted my-3">{{ onebot.connected?'已取得 OneBot 连接。':onebot.active_connection?.connection_mode==='forward_ws'?'当前运行方式为主动连接；尚未连接，请核对最近错误。':onebot.active_connection?.connection_mode==='reverse_ws'?'当前运行方式等待 OneBot 主动接入。':'尚未取得当前运行连接方式；下方仅是已保存配置。' }}<span v-if="onebot.self_id"> 已识别账号：{{ onebot.self_id }}</span></p><v-alert v-if="onebot.last_error" type="error" variant="tonal" class="mb-4">{{ onebot.last_error }}</v-alert><v-form :disabled="!!currentSaveOutcome||!!busy||connectionNeedsReadback" class="form-grid" @submit.prevent="saveConnection()"><v-select v-model="connection.connection_mode" label="消息连接方式" :items="[{title:'主动连接 OneBot',value:'forward_ws'},{title:'等待 OneBot 连接',value:'reverse_ws'}]" class="wide" /><v-text-field v-if="connection.connection_mode==='forward_ws'" v-model="connection.ws_url" label="WebSocket 端点" placeholder="ws://127.0.0.1:13001/" class="wide" required /><template v-else><v-text-field v-model="connection.host" label="监听地址" required /><v-text-field v-model.number="connection.port" type="number" min="1" max="65535" label="监听端口" required /></template><v-select v-model="connection.action_transport" label="发送传输" :items="[{title:'使用 WebSocket',value:'websocket'},{title:'使用 HTTP',value:'http'}]" /><v-text-field v-model="connection.http_url" label="HTTP 接口地址" :required="connection.action_transport==='http'" /><v-select v-model="connection.access_token_action" :items="[{title:'保留当前令牌',value:'keep'},{title:'替换令牌',value:'replace'},{title:'清除令牌',value:'clear'}]" label="访问令牌操作" class="wide" /><v-text-field v-if="connection.access_token_action==='replace'" v-model="connection.access_token" type="password" autocomplete="new-password" label="访问令牌" :placeholder="onebot.access_token_set?'已保存，留空保留':'填写 OneBot 访问令牌'" class="wide" /><div class="actions wide"><v-btn type="submit" color="primary" :loading="busy==='connection'" :disabled="!!currentSaveOutcome||!!busy||connectionNeedsReadback||!!conflicts.entries.connection||!connectionDirty">保存连接配置</v-btn><v-btn variant="outlined" :loading="busy==='http'" :disabled="!!busy" @click="checkHttp">检查当前 HTTP 连接</v-btn><v-btn variant="outlined" :loading="busy==='version'" :disabled="!!busy" @click="readVersion">读取平台实现与版本</v-btn></div><p class="muted wide">连接配置保存后需手动重启服务生效。HTTP 检查只读取当前运行连接的状态。版本读取走当前发送传输，只读，不发送任何群消息。</p><div v-if="platform" class="wide"><v-alert type="info" variant="tonal"><p>当前连接报告：{{ platform.app_name || '未提供实现名' }} · {{ platform.app_version || '未提供版本' }} · 协议 {{ platform.protocol_version ?? '未提供' }}（经 {{ platform.transport === 'http' ? 'HTTP' : 'WebSocket' }}）</p><p v-if="!platform.configured_upload" class="mt-2">根配置尚未声明 onebot_file_upload；先核对当前实现与所选文件动作，版本仅作可选现场记录。</p><template v-else><p class="mt-2">已声明：{{ platform.configured_upload.implementation }} · 现场版本 {{ platform.configured_upload.version || '未记录' }} · 配置标签 {{ platform.configured_upload.protocol }} · 部署核验标记 {{ platform.configured_upload.deployment_verified }}</p><p v-if="!platform.configured_upload.name_matches" class="mt-2">实现名与现场报告不一致，不要把配置标签改成另一实现。</p><p v-else class="mt-2">实现名一致；文件动作和资产目录只读挂载仍需在主机侧核对，版本仅供现场记录。</p></template><p class="mt-2">{{ platform.message }}</p></v-alert></div></v-form></v-card>
    <v-card v-if="tab==='access'&&accessText!==null" class="pa-5 form-card">
      <v-alert v-if="referenceError" type="error" variant="tonal" class="mb-4">群、成员、插件或额度策略参考读取失败：{{ referenceError }}；已有草稿保留，未自动选择替代项。</v-alert>
      <h2>QQ 回复白名单</h2>
      <p class="muted my-3">在已启用但关闭普通聊天的群中，白名单成员仍可正常提问和继续互动。白名单不会强制每条消息回复，也不授予管理员、跨群读取或 @全体权限；日程命令及引用评论仍保持安静。</p>
      <v-form :disabled="!!currentSaveOutcome||!!busy" @submit.prevent="saveAccess">
        <v-alert v-if="accessProblems.length" type="error" variant="tonal" class="mb-4">
          <p class="mb-2">请先修正以下内容；修正前不会提交保存。</p>
          <ul class="error-summary"><li v-for="(item,index) in accessProblems" :key="index+item.message">{{ item.message }}</li></ul>
        </v-alert>
        <v-textarea v-model="accessText" data-field="whitelist" label="QQ 账号" rows="6" :error="accessProblems.some(item=>item.key==='whitelist')" :error-messages="accessProblems.filter(item=>item.key==='whitelist').map(item=>item.message)" hint="每行一个 QQ 账号，或用逗号分隔。这里填写 QQ 账号，不是 B 站 UID。空列表表示没有额外回复资格。" persistent-hint />
        <v-divider class="my-5" />
        <div class="section-header"><div><h2>能力授予</h2><p class="muted mt-2">只影响本计划新增的自主能力；普通聊天不需要这里的任何一条。未配置、已停用或已过期的授予一律不放行，撤销只阻止后续操作，已发出的字节无法撤回。</p></div><v-btn variant="tonal" color="primary" :disabled="!!currentSaveOutcome||!!busy" @click="addGrant">添加授予</v-btn></div>
        <p v-if="!grants.length" class="muted py-4">当前没有任何能力授予；新增自主能力保持关闭。</p>
        <section v-for="(grant,index) in grants" :key="index" class="grant-card">
          <h3>{{ index+1 }}. 谁 · 什么范围 · 允许什么</h3>
          <v-alert v-if="grantCardProblems(index).length" type="error" variant="tonal" density="compact" class="mb-3">
            <ul class="error-summary"><li v-for="item in grantCardProblems(index)" :key="item.key+item.message">{{ item.message }}</li></ul>
          </v-alert>
          <div class="form-grid">
            <v-select v-model="grant.principal_type" label="谁" :items="[{title:'一个群友（人类）',value:'human'},{title:'系统用途',value:'system'},{title:'一个插件',value:'plugin'}]" @update:model-value="value=>{grant.principal_type=value; if(value==='system') grant.scene_id=''; else grant.system_scope=''}" />
            <v-combobox v-if="grant.principal_type==='human'" v-model="grant.principal_id" :data-field="`principal_id:${index}`" :items="participantsFor(grant)" label="主体标识" :error="accessProblems.some(item=>item.key===`principal_id:${index}`)" :error-messages="accessProblems.filter(item=>item.key===`principal_id:${index}`).map(item=>item.message)" hint="从本群已记录成员中选择，或直接填 QQ 账号；保存的是 QQ 账号，不是 actor ID。" persistent-hint required />
            <v-select v-else-if="grant.principal_type==='plugin'" v-model="grant.principal_id" :data-field="`principal_id:${index}`" :items="plugins" label="哪个插件" :error="accessProblems.some(item=>item.key===`principal_id:${index}`)" :error-messages="accessProblems.filter(item=>item.key===`principal_id:${index}`).map(item=>item.message)" hint="从当前已声明插件中选择。" persistent-hint required />
            <v-text-field v-else v-model="grant.principal_id" :data-field="`principal_id:${index}`" label="主体标识" :error="accessProblems.some(item=>item.key===`principal_id:${index}`)" :error-messages="accessProblems.filter(item=>item.key===`principal_id:${index}`).map(item=>item.message)" hint="填明确的系统用途标识，例如 heartbeat。" persistent-hint required />
            <v-select v-if="grant.principal_type!=='system'" v-model="grant.scene_id" :data-field="`scene_id:${index}`" :items="scopeOptions" label="在哪个场景生效" :error="accessProblems.some(item=>item.key===`scene_id:${index}`)" :error-messages="accessProblems.filter(item=>item.key===`scene_id:${index}`).map(item=>item.message)" hint="从已保存的场景中选择；这里不新建群。" persistent-hint required @update:model-value="loadParticipants($event)" />
            <v-text-field v-else v-model="grant.system_scope" :data-field="`system_scope:${index}`" label="系统用途" :error="accessProblems.some(item=>item.key===`system_scope:${index}`)" :error-messages="accessProblems.filter(item=>item.key===`system_scope:${index}`).map(item=>item.message)" hint="明确的系统范围，例如 heartbeat；该词表不是登记表，需要人工填写。" persistent-hint required />
            <v-select v-model="grant.capabilities" :data-field="`capability:${index}`" multiple chips :items="capabilityItems" item-title="title" item-value="value" label="允许什么" class="wide" :error="accessProblems.some(item=>item.key===`capability:${index}`)" :error-messages="accessProblems.filter(item=>item.key===`capability:${index}`).map(item=>item.message)" required />
            <v-text-field v-model="grant.expiresInput" :data-field="`expires:${index}`" type="datetime-local" step="1" label="有效期（本机时区）" :error="accessProblems.some(item=>item.key===`expires:${index}`)" :error-messages="accessProblems.filter(item=>item.key===`expires:${index}`).map(item=>item.message)" :hint="`留空表示长期有效。这里按你这台机器的时区填写，保存时换算成绝对时间：${expiryPreview(index)}`" persistent-hint />
            <v-select v-model="grant.resource_policy" :items="policyOptions" label="使用哪项额度策略" clearable hint="从已保存的策略中选择；留空使用默认策略。没有可选策略时先去“额度策略”页保存。" persistent-hint />
            <v-text-field v-model.number="grant.concurrency" type="number" min="1" label="并发上限（可留空）" />
            <v-switch v-model="grant.enabled" label="启用这条授予" color="primary" /></div>
          <p class="muted mt-2">授予 ID 与版本由服务端负责：保存时按内容自动递增，签发者取当前登录账号。{{ grant.grant_id?`当前 ID ${grant.grant_id} · 第 ${grant.revision} 版；修改内容后版本自动加一。`:'新建的授予由服务端生成 ID。' }}</p>
          <v-btn variant="text" color="error" :disabled="!!currentSaveOutcome||!!busy" @click="grants.splice(index,1)">删除这条授予</v-btn>
        </section>
        <div v-if="grantImpact.length" class="impact-summary">
          <p><strong>保存后的影响</strong>：这些主体在各自范围内将获准下列能力，下一次执行按新授予判断。</p>
          <p v-for="line in grantImpact" :key="line" class="muted">{{ line }}</p>
          <p class="muted">撤销或停用只阻止后续操作；已经发出的消息无法撤回，也不会改动其他未编辑的授予。</p>
        </div>
        <p class="muted mb-3">保存把白名单与能力授予一起写入根配置；它不发送消息、不调用模型，也不改动未编辑的授予。</p>
        <v-btn type="submit" color="primary" :loading="busy==='access'" :disabled="!!currentSaveOutcome||!!busy||!!conflicts.entries.access">保存白名单与能力授予</v-btn>
      </v-form>
    </v-card>
    <v-card v-if="tab==='resources'&&quotaText!==null" class="pa-5 form-card">
      <h2>额度策略</h2>
      <p class="muted my-3">这里定义命名的额度策略；能力授予的“使用哪项额度策略”填写这里的名称，不在授予里复制额度数值。<strong>token 不是货币</strong>：上限按 token 计，费用另看调用账。未配置策略时各维度不设 token 上限，由期限和消息上限结束；留空表示该维度不设上限，写出的数字才是限制。引用已失效的策略名称会拒绝，不会改用默认值。并发上限在创建准入时生效。修改默认策略不会改动已在执行的工作，它们仍按创建时的快照。</p>
      <v-form :disabled="!!currentSaveOutcome||!!busy" class="form-grid" @submit.prevent="saveQuota">
        <template v-if="!quotaRaw">
          <div v-for="(row,index) in quotaRows" :key="index" class="wide policy-row" :data-policy="`policy:${index}`">
            <div class="policy-heading"><h3>策略 {{ index+1 }}</h3><v-btn variant="text" color="error" size="small" :disabled="!!currentSaveOutcome||!!busy" @click="quotaRows.splice(index,1)">删除这项策略</v-btn></div>
            <div class="policy-fields">
              <v-text-field v-model="row.name" label="策略名称" hint="能力授予按这个名字引用；改名等于新建一项策略" persistent-hint required />
              <v-text-field v-model.number="row.work" label="单工作累计 token" type="number" min="1" hint="单个工作累计模型 token 上限，至少为 1；留空表示不设该维度" persistent-hint />
              <v-text-field v-model.number="row.user" label="主体日额度（token）" type="number" min="0" hint="同一账号在账务日内的上限，可填 0；留空表示不设该维度" persistent-hint />
              <v-text-field v-model.number="row.scene" label="群日额度（token，可选）" type="number" min="0" hint="同一场景在账务日内的上限，可填 0；留空表示本场景未配置该维度" persistent-hint />
            </div>
            <p v-for="problem in quotaProblemsFor(index)" :key="problem.message" class="policy-error">{{ problem.message }}</p>
          </div>
          <div v-if="!quotaRows.length" class="wide muted">当前没有具名策略；不配置时各维度不设 token 上限，由期限和消息上限结束。</div>
          <div class="wide actions"><v-btn variant="tonal" :disabled="!!currentSaveOutcome||!!busy" @click="quotaRows.push({name:'',work:null,user:null,scene:null})">添加一项策略</v-btn></div>
          <p class="wide muted">这里改的是往后新建工作的上限；已在执行的工作保留创建时的快照。已保存的精确取值在下方 JSON 里逐字对照。</p>
          <ul v-if="quotaProblems.length" class="wide error-summary">
            <li v-for="problem in quotaProblems" :key="problem.message"><button class="error-link" type="button" @click="focusPolicy(problem.key)">{{ problem.message }}</button></li>
          </ul>
        </template>
        <template v-if="quotaRaw"><v-textarea :model-value="quotaText" readonly label="策略（JSON）" rows="10" class="wide runtime-json" hint="已保存取值的只读对照，没有保存入口；改数值请返回表单。切换视图不会丢掉未保存的表单草稿。" persistent-hint /><p class="wide muted">这是已保存取值的只读视图，没有保存按钮；改数值请返回表单编辑。</p></template>
        <p v-if="Object.keys(rawPolicies).length" class="wide muted">有 {{ Object.keys(rawPolicies).length }} 项策略的形状不是这三个字段（{{ Object.keys(rawPolicies).join('、') }}），表单原样保留它们，只在保存时一起写回。</p>
        <ResourceViewer v-if="!quotaRaw" class="wide" title="已保存的精确取值（只读对照）" :content="quotaText" />
        <v-btn v-if="!Object.keys(rawPolicies).length" class="wide" variant="text" :disabled="!!currentSaveOutcome||!!busy" @click="toggleQuotaRaw">{{ quotaRaw?'返回表单编辑':'查看已保存 JSON' }}</v-btn>
        <v-btn v-if="!quotaRaw" type="submit" color="primary" :loading="busy==='resources'" :disabled="!!currentSaveOutcome||!!busy||!!conflicts.entries.resources||!quotaDirty">保存额度策略</v-btn>
        <span v-if="quotaDirty" class="muted">有未保存修改</span>
      </v-form>
    </v-card>
    <v-card v-if="tab==='members'&&members!==null" class="pa-5 form-card">
      <div class="section-header"><h2>主播与订阅对象</h2><v-btn variant="tonal" color="primary" :disabled="!!currentSaveOutcome||!!busy" @click="addMember">添加对象</v-btn></div>
      <p class="muted my-3">这里登记的是 B 站主播与订阅对象，不是群详情里的 QQ 参与者。名称与别名用于查询，B 站 UID 和直播间号确认实际对象。团体署名保持团体含义，不在这里自动展开。</p>
      <v-alert v-if="membersRestart" type="info" variant="tonal" class="mb-4">系统另有已保存配置等待手动重启。本页编辑已保存的订阅对象，不能据全局重启标记断言当前采集已切换；各插件运行状态另行核对。</v-alert>
      <p v-if="!members.length" class="muted py-4">尚未填写主播与订阅对象；动态与开播插件保持未就绪。</p>
      <v-form :disabled="!!currentSaveOutcome||!!busy" @submit.prevent="saveMembers">
        <v-card v-for="(member,index) in members" :key="index" variant="outlined" class="pa-4 mb-4">
          <div class="section-header mb-3"><h3>对象 {{ index+1 }}</h3><v-btn variant="text" color="error" :disabled="!!currentSaveOutcome||!!busy" @click="members.splice(index,1)">移除</v-btn></div>
          <div class="form-grid"><v-text-field v-model="member.name" label="显示名称" required /><v-text-field v-model="member.aliasText" label="别名（逗号或顿号分隔）" /><v-text-field v-model="member.bilibili_uid" label="B 站 UID" inputmode="numeric" required /><v-text-field v-model="member.room_id" label="直播间号" inputmode="numeric" required /></div>
        </v-card>
        <p class="muted mb-4">已被群订阅的对象需先在相应群中取消订阅，再移除或改名。</p>
        <v-btn type="submit" color="primary" :loading="busy==='members'" :disabled="!!currentSaveOutcome||!!busy||!!conflicts.entries.members||!membersDirty">保存主播与订阅对象</v-btn>
      </v-form>
    </v-card>
    <v-card v-if="tab==='delivery'&&shadow" class="pa-5 form-card">
      <h2>全局发送控制</h2>
      <div class="delivery-state my-4"><v-chip :color="shadow.enabled?'warning':'primary'">{{ shadow.enabled?'Shadow · 不实际发送':'按各群规则发送' }}</v-chip><v-btn :color="shadow.enabled?'warning':'primary'" variant="outlined" :loading="busy==='shadow'" :disabled="!!currentSaveOutcome||!!busy" @click="toggleShadow">{{ shadow.enabled?'关闭 Shadow':'开启 Shadow' }}</v-btn></div>
      <p class="muted mb-5">Shadow 只控制是否实际发送。群启用、普通聊天、命令、公告、主播订阅和 @全体统一在“本群设置”中保存。</p>
      <v-btn variant="tonal" color="primary" :to="{name:'scenes'}">管理各群设置</v-btn>
    </v-card>
    <v-card v-if="tab==='runtime'&&runtimeText!==null" class="pa-5 form-card">
      <h2>运行参数</h2>
      <section class="upload-block">
        <div class="section-header">
          <h3>群文件上传平台<HelpHint :text="UPLOAD_HELP" /></h3>
          <v-chip size="small" :color="fileUpload?.deployment_verified ? 'success' : 'warning'">
            {{ fileUpload ? (fileUpload.deployment_verified ? '已核对' : '未核对，无法上传') : '未声明' }}
          </v-chip>
        </div>
        <p v-if="!fileUpload" class="muted my-3">
          未声明上传平台，群文件只能在工作面板下载。
          <v-btn size="small" variant="tonal" color="primary" class="ml-2"
                 :disabled="!!currentSaveOutcome||!!busy" @click="setFileUpload({})">声明上传平台</v-btn>
        </p>
        <template v-else>
          <div class="form-grid my-3">
            <v-select :model-value="fileUpload.implementation" label="实现"
              :items="[{title:'SnowLuma',value:'snowluma'},{title:'NapCat',value:'napcat'}]"
              @update:model-value="value=>setFileUpload({implementation:value})" />
            <v-text-field :model-value="fileUpload.version || ''" label="现场版本（可选记录）"
              placeholder="可从连接页读取" hint="仅供现场记录，不作为协议准入条件" persistent-hint
              @update:model-value="value=>setFileUpload({version:value.trim() || null})" />
            <v-text-field :model-value="fileUpload.protocol" label="配置／回执标签（由实现决定）" readonly />
            <v-text-field :model-value="fileUpload.export_mount_path" label="只读挂载点" readonly />
            <p class="muted wide">两种实现均调用 upload_group_file；标签和部署核验标记不等于平台取得文件，成功仍看真实 FILE_UPLOADED 与 file_id。</p>
          </div>
          <v-switch :model-value="fileUpload.deployment_verified"
            label="已人工核对实现、文件动作与只读挂载"
            hint="打开后仍需真实授权、资产审查与平台 file_id 回执"
            persistent-hint
            @update:model-value="value=>setFileUpload({deployment_verified:!!value})" />
          <div class="actions"><v-btn size="small" variant="text" color="error"
            :disabled="!!currentSaveOutcome||!!busy" @click="setFileUpload(null)">取消声明</v-btn></div>
        </template>
      </section>
      <p class="muted my-3">下面对照根配置已保存值与运行时当前发布值。编辑中的 JSON 尚未保存，不计入这两列。</p>
      <div class="budget-table-wrap"><table class="budget-table"><caption>执行预算</caption><thead><tr><th scope="col">范围</th><th scope="col">已保存</th><th scope="col">当前发布</th></tr></thead><tbody><tr v-for="item in executionBudgets" :key="item.key"><th scope="row">{{ item.label }}</th><td>{{ budgetText(runtimeSavedBudgets[item.key], item.unit) }}</td><td>{{ budgetText(runtimeEffectiveBudgets[item.key], item.unit) }}</td></tr></tbody></table></div>
      <p class="muted my-4">新对话与新建工作采用当前发布预算；已有工作及其恢复保留创建时的上限、期限和累计用量。改变设置不会重开已有结果或失败工作。</p>
      <v-alert v-if="runtimeRestart" type="info" variant="tonal" class="mb-4">另有需重建组件的配置等待手动重启；上表分别显示已保存值与本次读取到的运行值，未提供项不能据此判断已生效。</v-alert>
      <p class="muted my-3">常用执行预算用下面的数字框改；其余字段仍通过完整 JSON。留空表示该维度不设限。改这里会写进同一份草稿。</p>
      <v-form :disabled="!!currentSaveOutcome||!!busy" @submit.prevent="saveRuntime">
        <v-switch :model-value="heartbeatDraft.heartbeat_enabled || false" label="启用公共研究心跳" color="primary" @update:model-value="value=>setHeartbeat('heartbeat_enabled',value)" />
        <v-textarea :model-value="(heartbeatDraft.heartbeat_topics || []).join('\n')" label="公共研究主题（每行一项）" rows="3" hint="最多 20 项，每项 200 字；没有主题或有效兴趣时允许零研究。只保存研究结果与兴趣，不发布群消息。保存后需手动重启。" persistent-hint @update:model-value="value=>setHeartbeat('heartbeat_topics',value.split('\n').map(item=>item.trim()).filter(Boolean))" />
        <div class="form-grid mb-4">
          <v-text-field v-for="item in executionBudgets" :key="item.key" :model-value="runtimeBudgetValue(item.key)"
            :label="item.label+'（'+item.unit+'）'" type="number" :hint="'留空即不设限'" persistent-hint
            @update:model-value="value=>setRuntimeBudget(item.key,value)" />
        </div>
        <AdvancedSection title="其余运行参数（原始 JSON）" note="上面没有控件的字段在这里改">
          <v-textarea v-model="runtimeText" label="运行参数 JSON" rows="16" spellcheck="false" class="runtime-json" />
        </AdvancedSection>
        <div class="actions"><v-btn type="submit" color="primary" :loading="busy==='runtime'" :disabled="!!currentSaveOutcome||!!busy||!!conflicts.entries.runtime||!runtimeDirty">保存运行参数</v-btn><span v-if="runtimeDirty" class="muted">有未保存修改</span></div>
      </v-form>
    </v-card>
    <v-card v-if="tab==='account'&&me" class="pa-5 form-card"><div class="section-header"><h2>登录账户</h2><v-chip :color="me.is_default_password?'warning':'default'">{{ me.is_default_password?'仍使用初始密码':'已修改初始密码' }}</v-chip></div><p class="my-4">{{ me.username }} · 上次登录 {{ fmtTime(me.last_login_at) }}</p><v-form :disabled="!!currentSaveOutcome||!!busy" class="form-grid" @submit.prevent="changePassword"><v-text-field v-model="passwords.current_password" type="password" autocomplete="current-password" label="当前密码" required /><v-text-field v-model="passwords.new_password" type="password" autocomplete="new-password" label="新密码（至少 6 位）" minlength="6" required /><div class="actions wide"><v-btn type="submit" color="primary" :loading="busy==='password'" :disabled="!!currentSaveOutcome||!!busy||!passwords.current_password||passwords.new_password.length<6">更新密码</v-btn><v-btn variant="outlined" :disabled="!!currentSaveOutcome||!!busy" @click="signOut">退出登录</v-btn></div></v-form></v-card>
    <v-expansion-panels v-if="tab==='account'&&me" class="danger-zone">
      <v-expansion-panel title="破坏性操作：重置全部对话数据">
        <v-expansion-panel-text>
          <p class="mb-3">
            清空全部对话、认识、工作与任务；运行配置、人工样例与表情库保留。
            <strong>不要把它当作修复路径。</strong>
            <HelpHint :text="RESET_HELP" />
          </p>
          <v-btn color="error" variant="outlined" :disabled="!!currentSaveOutcome||!!busy" @click="resetConfirm=true">Reset 对话数据</v-btn>
        </v-expansion-panel-text>
      </v-expansion-panel>
    </v-expansion-panels>

    <v-dialog v-model="resetConfirm" max-width="620" :persistent="busy==='reset'"><v-card title="确认 Reset 全部对话数据"><v-card-text><v-alert type="error" variant="tonal" class="mb-4">此操作会清空全部群聊和私聊的会话数据，无法从页面撤销。</v-alert><p>先停止认知、维护、工作和投递，再删除对话、认识、任务、工作、工具资料、调用账、聊天图片和场景上下文。</p><p class="mt-3">保留运行配置、人工表达样例与运营表情库（含来源记录），包括模型、OneBot、人格、登录、Shadow、QQ 白名单、各群设置与能力授予。</p><p class="mt-3">额度预占与执行记录不在清理范围内，执行后仍需单独核对归属；页面的成功结果不表示外部容器已经结束。</p><v-alert v-if="error" type="error" variant="tonal" class="mt-3">{{ error }}</v-alert></v-card-text><v-card-actions><v-spacer /><v-btn :disabled="!!currentSaveOutcome||!!busy" @click="resetConfirm=false">取消</v-btn><v-btn color="error" :loading="busy==='reset'" :disabled="!!currentSaveOutcome||!!busy" @click="resetData">确认清空对话数据</v-btn></v-card-actions></v-card></v-dialog>
  </div>
</template>
<style scoped>
.budget-table-wrap{overflow-x:auto}.budget-table{width:100%;border-collapse:collapse;text-align:left;font-size:14px}.budget-table caption{text-align:left;font-weight:600;padding:8px 0 12px}.budget-table th,.budget-table td{padding:12px;border-bottom:1px solid var(--line);white-space:nowrap}.budget-table thead{background:rgb(var(--v-theme-surface-variant))}.budget-table tbody th{font-weight:500}.preset-field{margin-bottom:12px}.runtime-json :deep(textarea){font-family:monospace;font-size:13px;line-height:1.6}
.upload-block{border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:16px 0}.upload-block h3{display:flex;align-items:center;gap:2px;font-size:15px;font-weight:650;margin:0}

.form-card{max-width:1000px;width:100%}.grant-card{border:1px solid #e2e8f0;border-radius:8px;padding:16px;margin-bottom:18px}.grant-card h3{font-size:14px;font-weight:650;margin-bottom:12px}.impact-summary{border-left:3px solid rgb(var(--v-theme-primary));padding:12px 14px;margin:16px 0;background:rgb(var(--v-theme-surface-variant));max-width:1000px}.impact-summary p{margin:4px 0;font-size:13px;line-height:1.7}.section-header{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}.section-header h2,.form-card>h2{font-size:20px}.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;align-items:start}.wide{grid-column:1/-1}.form-grid>.v-btn{justify-self:start}.actions,.meta,.delivery-state,.saved-scenes{display:flex;gap:8px 12px;flex-wrap:wrap;align-items:center}.meta{font-size:13px;color:#64748b}.example-row{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;padding:24px 0;border-bottom:1px solid #e2e8f0}.example-row:last-child{border:0;padding-bottom:0}.example-main{min-width:0;flex:1}.example-row>.actions{max-width:220px;justify-content:flex-end}.example-context{white-space:pre-wrap;line-height:1.65;color:#64748b;overflow-wrap:anywhere}.example-body{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0;align-items:flex-start}.example-body p{flex-basis:100%;white-space:pre-wrap;line-height:1.8;overflow-wrap:anywhere}.example-body img{max-width:180px;max-height:180px;object-fit:contain}.part-toolbar{display:flex;gap:12px;align-items:center;margin-bottom:16px;flex-wrap:wrap}.part-toolbar>.v-input{flex:1;min-width:140px;max-width:180px}.part-image{display:flex;gap:16px;align-items:center;flex-wrap:wrap}.part-image img{max-width:100%;height:170px;object-fit:contain}.media-filter{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:center}.media-picker{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}.media-picker img{width:100%;height:150px;object-fit:contain;background:#f4f6f9}.media-picker p{overflow-wrap:anywhere;min-height:3em}.danger-zone{max-width:1000px;margin-top:12px}
.error-summary{list-style:none;padding:0;margin:0;display:grid;gap:4px}.policy-row{border:1px solid #e2e8f0;border-radius:8px;padding:16px}.policy-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.policy-heading h3{font-size:14px;font-weight:650}.policy-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.policy-error{color:#b3261e;font-size:13px;margin:8px 0 0}.settings-view p{line-height:1.7}@media(max-width:650px){.form-grid{grid-template-columns:minmax(0,1fr)}.policy-fields{grid-template-columns:minmax(0,1fr)}.example-row{flex-direction:column}.example-row>.actions{max-width:none;justify-content:flex-start}.section-header{align-items:flex-start}.media-picker{grid-template-columns:repeat(2,minmax(0,1fr))}.part-toolbar>.actions{width:100%}.example-body img{max-width:140px;max-height:140px}}
</style>
