<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, fmtTime } from '../api.js'
import { logout } from '../composables/useAuth.js'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'
import PageHeader from '../components/PageHeader.vue'
import ResourceViewer from '../components/ResourceViewer.vue'
const route = useRoute()
const router = useRouter()
const tabs = [{value:'access',title:'QQ 回复资格'},{value:'resources',title:'额度策略'},{value:'members',title:'主播与订阅对象'},{value:'connection',title:'连接'},{value:'delivery',title:'发送'},{value:'runtime',title:'运行参数'},{value:'account',title:'账户'}]
const tab = computed(() => tabs.some(item=>item.value===route.query.tab) ? route.query.tab : 'connection')
const baselines = ref({})
const saveDraft = (domain,path,values,method) => api(path,{method,body:JSON.stringify({baseline:baselines.value[domain],values})})
const loading = ref(false)
const error = ref('')
const message = ref('')
const readAt = ref({})
const busy = ref('')
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
async function loadReferences() {
  try {
    const [scenes, catalog, policies] = await Promise.all([
      api('/api/cockpit/scenes'), api('/api/plugins/list'), api('/api/settings/resources')])
    scopeOptions.value = scenes.scenes.map(scene=>({title:`${scene.display_name} · ${scene.scene_id}`,value:scene.scene_id}))
    plugins.value = catalog.map(item=>({title:`${item.name} · ${item.id}`,value:item.id}))
    policyOptions.value = Object.keys(policies.policies || {}).map(name=>({title:name,value:name}))
  } catch(e) { referenceError.value=e.message }
}
const qqUid = id => String(id||'').startsWith('user:') ? String(id).slice(5) : String(id||'')
const participantsFor = grant => participantCache.value[grant.scene_id] || []
async function loadParticipants(sceneId) {
  if (!/^group:[1-9]\d*$/.test(sceneId || '')) return
  const request = (participantRequests[sceneId] = (participantRequests[sceneId] || 0) + 1)
  try {
    const detail = await api(`/api/cockpit/scenes/${encodeURIComponent(sceneId)}`)
    if (request !== participantRequests[sceneId]) return
    participantCache.value = {...participantCache.value, [sceneId]: Object.entries(detail.session.participants || {})
      .map(([id,item])=>{ const uid=qqUid(id); return {title:`${item.card || item.nickname || uid} · ${uid}`,value:uid} })}
  } catch(e) { if (request === participantRequests[sceneId]) referenceError.value=e.message }
}
const capabilityItems = computed(()=>capabilities.value.map(item=>({...item,
  title:item.implemented?item.title:`${item.title}`,subtitle:item.value})))
const grantCapabilities = grant => Array.isArray(grant.capabilities) ? grant.capabilities.filter(Boolean) : []
const toLocalInput = seconds => {
  if (seconds === null || seconds === undefined) return ''
  const date = new Date(seconds*1000)
  const pad = value => String(value).padStart(2,'0')
  return `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}
const fromLocalInput = value => value ? new Date(value).getTime()/1000 : null
const grantExpiry = grant => grant.expiresInput === toLocalInput(grant.expires_at)
  ? (grant.expires_at ?? null) : fromLocalInput(grant.expiresInput)
const expiryPreview = index => {
  const seconds = grantExpiry(grants.value[index] || {})
  if (!seconds) return '长期有效'
  return `保存后为 ${new Date(seconds*1000).toISOString()}（UTC）`
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
let requestId = 0
async function load() {
  const currentTab = tab.value, request = ++requestId
  loading.value = true; error.value = ''
  try {
    if (currentTab==='access') {
      const [snapshot, vocabulary] = await Promise.all([api('/api/settings/draft/access'),api('/api/settings/capabilities'),loadReferences()])
      if (request!==requestId) return
      const settings=snapshot.saved
      capabilities.value = vocabulary.items
      if (!accessDirty.value) {
        baselines.value.access=snapshot.baseline
        accessText.value=settings.qq_reply_whitelist.join('\n'); accessOriginal.value=accessText.value
        grants.value=(settings.capability_grants||[]).map(grant=>({...grant,
          capabilities:[...grant.capabilities], expiresInput:toLocalInput(grant.expires_at)}))
        grantsOriginal.value=JSON.stringify(grants.value)
      }
      await Promise.all([...new Set(grants.value.filter(grant=>grant.principal_type!=='system').map(grant=>grant.scene_id))]
        .map(sceneId=>loadParticipants(sceneId)))
    } else if (currentTab==='resources') {
      const snapshot = await api('/api/settings/draft/resources'); const settings=snapshot.saved; if (request!==requestId) return
      if (!quotaDirty.value) {
        quotaRecord.value=settings.policies||{}
        quotaText.value=JSON.stringify(settings.policies,null,2)
        baselines.value.resources=snapshot.baseline
        quotaRows.value=policyRows(settings.policies); quotaOriginal.value=JSON.stringify(quotaRows.value)
      }
    } else if (currentTab==='members') {
      const snapshot = await api('/api/settings/draft/members'); const settings=snapshot.saved; if (request!==requestId) return
      if (!membersDirty.value) { baselines.value.members=snapshot.baseline; members.value=settings.map(item=>({...item,aliasText:item.aliases.join('、')})); membersOriginal.value=JSON.stringify(members.value) }
    } else if (currentTab==='connection') {
      const settings = await api('/api/websocket/status'); if (request!==requestId) return
      onebot.value=settings
      if (!connectionDirty.value) { baselines.value.connection=settings; connection.value={connection_mode:settings.connection_mode,action_transport:settings.action_transport,ws_url:settings.ws_url,http_url:settings.http_url,host:settings.host,port:settings.port,access_token:'',access_token_action:'keep'}; connectionOriginal.value=JSON.stringify(connection.value) }
    } else if (currentTab==='delivery') {
      const settings=await api('/api/cockpit/shadow'); if(request!==requestId)return
      shadow.value=settings
    } else if (currentTab==='runtime') {
      const snapshot=await api('/api/settings/draft/runtime');const result={settings:snapshot.saved,effective_budgets:snapshot.effective,requires_restart:snapshot.requires_restart};if(request!==requestId)return
      if(!runtimeDirty.value){baselines.value.runtime=snapshot.baseline;runtimeText.value=JSON.stringify(result.settings,null,2);runtimeOriginal.value=runtimeText.value}
      runtimeRestart.value=result.requires_restart
      runtimeSavedBudgets.value=result.settings
      runtimeEffectiveBudgets.value=result.effective_budgets || {}
    } else {
      const result=await api('/api/auth/me');if(request!==requestId)return;me.value=result
    }
    readAt.value[currentTab]=Date.now()/1000
  } catch(e){if(request===requestId)error.value=e.message}
  finally{if(request===requestId)loading.value=false}
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
  if (busy.value) return
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
  busy.value='access'; error.value=''; message.value=''
  try {
    const values = accessText.value.split(/[,，\s]+/).filter(Boolean).map(value=>positiveInteger(value,'QQ 账号'))
    const result = await saveDraft('access','/api/settings/access',{
      ...(accessText.value!==accessOriginal.value ? {qq_reply_whitelist:values} : {}),
      ...(JSON.stringify(grants.value)!==grantsOriginal.value ? {capability_grants:grants.value.map(grant=>({
      grant_id:grant.grant_id||'',revision:grant.revision||1,
      principal_type:grant.principal_type,principal_id:qqUid(grant.principal_id && typeof grant.principal_id==='object'?grant.principal_id.value:grant.principal_id),
      scene_id:grant.principal_type==='system'?null:(grant.scene_id||null),
      system_scope:grant.principal_type==='system'?(grant.system_scope||null):null,
      capabilities:grantCapabilities(grant),
      expires_at:grantExpiry(grant),resource_policy:grant.resource_policy||null,
      concurrency:grant.concurrency||null,enabled:!!grant.enabled}))} : {})},'PUT')
    const settings=result.settings
    baselines.value.access=settings
    accessText.value=settings.qq_reply_whitelist.join('\n'); accessOriginal.value=accessText.value
    grants.value=(settings.capability_grants||[]).map(grant=>({...grant,
      capabilities:[...grant.capabilities], expiresInput:toLocalInput(grant.expires_at)}))
    grantsOriginal.value=JSON.stringify(grants.value); message.value=result.message; accessProblems.value=[]
  } catch(e) {
    accessProblems.value=(Array.isArray(e.details)?e.details:[]).map(item=>{
      const parts=(item.loc||[]).filter(part=>part!=='body'&&part!=='capability_grants')
      if (typeof parts[0]==='number') {
        const field=parts[1]==='capabilities'?'capability':(parts[1]||'grant')
        return {key:`${field}:${parts[0]}`,message:`第 ${parts[0]+1} 条授予 · ${parts.slice(1).join(' → ')||'字段'}：${item.msg}`}
      }
      return {key:parts[0]==='qq_reply_whitelist'?'whitelist':'',message:`${parts.join(' → ')||'提交内容'}：${item.msg}`}
    })
    error.value=e.message
  } finally { busy.value='' }
}
function addGrant() {
  grants.value.push({grant_id:'',revision:1,operator_id:'',principal_type:'human',principal_id:'',
    scene_id:'',system_scope:'',capabilities:[],expiresInput:'',resource_policy:'',concurrency:null,enabled:false})
}
const grantImpact = computed(()=>grants.value.filter(grant=>grant.enabled&&grantCapabilities(grant).length)
  .map(grant=>`${grant.principal_type==='system'?grant.system_scope:grant.scene_id||'未指定范围'} · ${grant.principal_id} · ${grantCapabilities(grant).join('、')}`))
async function saveQuota() {
  if (busy.value) return
  busy.value='resources'; error.value=''; message.value=''; quotaProblems.value=[]
  try {
    const policies={...rawPolicies.value}
    for (const [index,row] of quotaRows.value.entries()) {
      const name=row.name.trim()
      if (!name) { quotaProblems.value=[{key:`policy:${index}`,message:`第 ${index+1} 项：策略名不能为空`}]; return }
      if (name in policies) { quotaProblems.value=[{key:`policy:${index}`,message:`第 ${index+1} 项：策略名“${name}”已有同名策略`}]; return }
      const work=quotaNumber(row.work,'单工作累计 token',`policy:${index}`,{minimum:1})
      const user=quotaNumber(row.user,'主体日额度',`policy:${index}`)
      const scene=quotaNumber(row.scene,'群日额度',`policy:${index}`)
      if (quotaProblems.value.length || work===undefined || user===undefined || scene===undefined) return
      policies[name]={work_token_limit:work, daily_user_token_limit:user, daily_scene_token_limit:scene}
    }
    quotaProblems.value=[]
    const result = await saveDraft('resources','/api/settings/resources',{policies},'PUT')
    baselines.value.resources=result.settings
    quotaRecord.value=result.settings.policies||{}
    quotaText.value=JSON.stringify(result.settings.policies,null,2)
    quotaRows.value=policyRows(result.settings.policies); quotaOriginal.value=JSON.stringify(quotaRows.value)
    message.value=result.message
  } catch(e) {
    // A server rejection names the policy it belongs to; the same sentence is
    // shown beside that policy with a position the operator can act on.
    quotaProblems.value=(Array.isArray(e.details)?e.details:[]).map(item=>{
      const name=(item.loc||[]).filter(part=>typeof part==='string'&&part!=='body'&&part!=='policies')[0]
      const index=quotaRows.value.findIndex(row=>row.name.trim()===name)
      return {key:index>=0?`policy:${index}`:'',message:`${name?`策略“${name}” · `:''}${item.msg}`}
    })
    error.value=e.message
  } finally { busy.value='' }
}
async function saveMembers() {
  if (busy.value || !members.value) return
  busy.value='members'; error.value=''; message.value=''
  try {
    const values=members.value.map(item=>({name:item.name.trim(),aliases:item.aliasText.split(/[\n,，、]+/).map(value=>value.trim()).filter(Boolean),bilibili_uid:positiveInteger(item.bilibili_uid,'B 站 UID'),room_id:positiveInteger(item.room_id,'直播间号')}))
    const result=await saveDraft('members','/api/settings/members',values,'PUT')
    baselines.value.members=result.settings;members.value=result.settings.map(item=>({...item,aliasText:item.aliases.join('、')})); membersOriginal.value=JSON.stringify(members.value); membersRestart.value=result.requires_restart; message.value=result.message
  } catch(e) { error.value=e.message } finally { busy.value='' }
}
async function saveRuntime() {
  if (busy.value) return
  busy.value = 'runtime'
  error.value = ''
  message.value = ''
  try {
    const settings = JSON.parse(runtimeText.value)
    if (settings === null || Array.isArray(settings) || typeof settings !== 'object') {
      throw new Error('运行参数须填写 JSON 对象')
    }
    const result = await saveDraft('runtime','/api/settings/runtime',settings,'PATCH')
    baselines.value.runtime=result.settings
    runtimeText.value = JSON.stringify(result.settings, null, 2)
    runtimeOriginal.value = runtimeText.value
    runtimeRestart.value = result.requires_restart
    runtimeSavedBudgets.value = result.settings
    runtimeEffectiveBudgets.value = result.effective_budgets || {}
    message.value = result.message
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = ''
  }
}
async function saveConnection() {
  if (busy.value) return
  busy.value = 'connection'
  error.value = ''
  message.value = ''
  try {
    const body = {...connection.value, access_token:connection.value.access_token.trim() || null}
    const result = await saveDraft('connection','/api/websocket/config',body,'POST')
    connection.value.access_token = ''
    connection.value.access_token_action = 'keep'
    connectionOriginal.value = JSON.stringify(connection.value)
    message.value = result.requires_restart ? `${result.message}；请手动重启服务后使用新连接配置` : result.message
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = ''
  }
}
async function checkHttp() {
  if (busy.value) return
  busy.value = 'http'
  error.value = ''
  message.value = ''
  try {
    const result = await api('/api/websocket/test-http', {method:'POST'})
    message.value = `当前运行连接：${result.message}`
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = ''
  }
}
async function toggleShadow() {
  if(busy.value||!shadow.value)return
  const enabled=!shadow.value.enabled
  if(!window.confirm(enabled?'开启 Shadow？保留原观察行为，但不再真实发送消息。':'关闭 Shadow？机器人将按各群当前启用、聊天、命令和公告配置实际发送。'))return
  busy.value='shadow';error.value='';message.value=''
  try{
    const result=await api('/api/cockpit/shadow/toggle',{method:'POST',body:JSON.stringify({enabled})})
    shadow.value={...shadow.value,enabled:result.shadow_mode};message.value=result.shadow_mode?'已开启 Shadow，不实际发送':'已关闭 Shadow，按当前群规则发送'
  }catch(e){error.value=e.message}finally{busy.value=''}
}
async function changePassword() {
  if(busy.value)return
  busy.value='password';error.value='';message.value=''
  try{await api('/api/auth/change_password',{method:'POST',body:JSON.stringify(passwords.value)});passwords.value={current_password:'',new_password:''};message.value='访问密码已修改';await load()}
  catch(e){error.value=e.message}finally{busy.value=''}
}
async function signOut() {
  if(busy.value||!confirmLeave())return
  busy.value='logout';error.value=''
  try{await logout();leavingAfterLogout.value=true;await router.replace({name:'login'})}catch(e){error.value=e.message}finally{busy.value=''}
}
async function resetData(){
  if(busy.value)return
  busy.value='reset';error.value='';message.value=''
  try{await api('/api/settings/reset',{method:'POST'});resetConfirm.value=false;message.value='对话数据已按上面列出的范围清空；配置与运营资料保留，额度预占和执行记录仍待单独核对';await load()}
  catch(e){error.value=e.message}finally{busy.value=''}
}
watch(tab,load,{immediate:true})
</script>
<template>
  <div class="page-stack settings-view">
    <PageHeader title="系统设置" description="各配置节分别保存到根参数文件，刷新保留未保存的草稿。"><v-btn variant="outlined" :loading="loading" @click="load">刷新当前设置</v-btn></PageHeader>
    <v-alert v-if="error" type="error" variant="tonal">{{ error }}<span v-if="readAt[tab]"> · 上次读取 {{ fmtTime(readAt[tab]) }}</span></v-alert><v-alert v-if="message" type="success" variant="tonal" closable @click:close="message=''">{{ message }}</v-alert>
    <v-tabs :model-value="tab" color="primary" show-arrows @update:model-value="value=>router.push({name:'settings',query:{tab:value}})"><v-tab v-for="item in tabs" :key="item.value" :value="item.value">{{ item.title }}</v-tab></v-tabs>
    <v-progress-linear v-if="loading" indeterminate />
    <v-card v-if="tab==='connection'&&onebot&&connection" class="pa-5 form-card"><div class="section-header"><h2>连接 OneBot</h2><v-chip :color="onebot.connected?'success':'warning'">{{ onebot.connected?'已连接':'未连接' }}</v-chip></div><p class="muted my-3">{{ onebot.connected?'已取得 OneBot 连接。':onebot.connection_mode==='forward_ws'?'当前未连接，请查看端点与最近错误。':'等待 OneBot 主动接入。' }}<span v-if="onebot.self_id"> 已识别账号：{{ onebot.self_id }}</span></p><v-alert v-if="onebot.last_error" type="error" variant="tonal" class="mb-4">{{ onebot.last_error }}</v-alert><v-form :disabled="!!busy" class="form-grid" @submit.prevent="saveConnection()"><v-select v-model="connection.connection_mode" label="消息连接方式" :items="[{title:'主动连接 OneBot',value:'forward_ws'},{title:'等待 OneBot 连接',value:'reverse_ws'}]" class="wide" /><v-text-field v-if="connection.connection_mode==='forward_ws'" v-model="connection.ws_url" label="WebSocket 端点" placeholder="ws://127.0.0.1:13001/" class="wide" required /><template v-else><v-text-field v-model="connection.host" label="监听地址" required /><v-text-field v-model.number="connection.port" type="number" min="1" max="65535" label="监听端口" required /></template><v-select v-model="connection.action_transport" label="发送传输" :items="[{title:'使用 WebSocket',value:'websocket'},{title:'使用 HTTP',value:'http'}]" /><v-text-field v-model="connection.http_url" label="HTTP 接口地址" :required="connection.action_transport==='http'" /><v-select v-model="connection.access_token_action" :items="[{title:'保留当前令牌',value:'keep'},{title:'替换令牌',value:'replace'},{title:'清除令牌',value:'clear'}]" label="访问令牌操作" class="wide" /><v-text-field v-if="connection.access_token_action==='replace'" v-model="connection.access_token" type="password" autocomplete="new-password" label="访问令牌" :placeholder="onebot.access_token_set?'已保存，留空保留':'填写 OneBot 访问令牌'" class="wide" /><div class="actions wide"><v-btn type="submit" color="primary" :loading="busy==='connection'" :disabled="!!busy||!connectionDirty">保存连接配置</v-btn><v-btn variant="outlined" :loading="busy==='http'" :disabled="!!busy" @click="checkHttp">检查当前 HTTP 连接</v-btn></div><p class="muted wide">连接配置保存后需手动重启服务生效。HTTP 检查只读取当前运行连接的状态。</p></v-form></v-card>
    <v-card v-if="tab==='access'&&accessText!==null" class="pa-5 form-card">
      <h2>QQ 回复白名单</h2>
      <p class="muted my-3">在已启用但关闭普通聊天的群中，白名单成员仍可正常提问和继续互动。白名单不会强制每条消息回复，也不授予管理员、跨群读取或 @全体权限；日程命令及引用评论仍保持安静。</p>
      <v-form :disabled="!!busy" @submit.prevent="saveAccess">
        <v-alert v-if="accessProblems.length" type="error" variant="tonal" class="mb-4">
          <p class="mb-2">请先修正以下内容；修正前不会提交保存。</p>
          <ul class="error-summary"><li v-for="(item,index) in accessProblems" :key="index+item.message">{{ item.message }}</li></ul>
        </v-alert>
        <v-textarea v-model="accessText" data-field="whitelist" label="QQ 账号" rows="6" :error="accessProblems.some(item=>item.key==='whitelist')" :error-messages="accessProblems.filter(item=>item.key==='whitelist').map(item=>item.message)" hint="每行一个 QQ 账号，或用逗号分隔。这里填写 QQ 账号，不是 B 站 UID。空列表表示没有额外回复资格。" persistent-hint />
        <v-divider class="my-5" />
        <div class="section-header"><div><h2>能力授予</h2><p class="muted mt-2">只影响本计划新增的自主能力；普通聊天不需要这里的任何一条。未配置、已停用或已过期的授予一律不放行，撤销只阻止后续操作，已发出的字节无法撤回。</p></div><v-btn variant="tonal" color="primary" :disabled="!!busy" @click="addGrant">添加授予</v-btn></div>
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
          <v-btn variant="text" color="error" :disabled="!!busy" @click="grants.splice(index,1)">删除这条授予</v-btn>
        </section>
        <div v-if="grantImpact.length" class="impact-summary">
          <p><strong>保存后的影响</strong>：这些主体在各自范围内将获准下列能力，下一次执行按新授予判断。</p>
          <p v-for="line in grantImpact" :key="line" class="muted">{{ line }}</p>
          <p class="muted">撤销或停用只阻止后续操作；已经发出的消息无法撤回，也不会改动其他未编辑的授予。</p>
        </div>
        <p class="muted mb-3">保存把白名单与能力授予一起写入根配置；它不发送消息、不调用模型，也不改动未编辑的授予。</p>
        <v-btn type="submit" color="primary" :loading="busy==='access'" :disabled="!!busy">保存白名单与能力授予</v-btn>
      </v-form>
    </v-card>
    <v-card v-if="tab==='resources'&&quotaText!==null" class="pa-5 form-card">
      <h2>额度策略</h2>
      <p class="muted my-3">这里定义命名的额度策略；能力授予的“使用哪项额度策略”填写这里的名称，不在授予里复制额度数值。<strong>token 不是货币</strong>：上限按 token 计，费用另看调用账。未配置策略时各维度不设 token 上限，由期限和消息上限结束；留空表示该维度不设上限，写出的数字才是限制。引用已失效的策略名称会拒绝，不会改用默认值。并发上限在创建准入时生效。修改默认策略不会改动已在执行的工作，它们仍按创建时的快照。</p>
      <v-form :disabled="!!busy" class="form-grid" @submit.prevent="saveQuota">
        <template v-if="!quotaRaw">
          <div v-for="(row,index) in quotaRows" :key="index" class="wide policy-row" :data-policy="`policy:${index}`">
            <div class="policy-heading"><h3>策略 {{ index+1 }}</h3><v-btn variant="text" color="error" size="small" :disabled="!!busy" @click="quotaRows.splice(index,1)">删除这项策略</v-btn></div>
            <div class="policy-fields">
              <v-text-field v-model="row.name" label="策略名称" hint="能力授予按这个名字引用；改名等于新建一项策略" persistent-hint required />
              <v-text-field v-model.number="row.work" label="单工作累计 token" type="number" min="1" hint="单个工作累计模型 token 上限，至少为 1；留空表示不设该维度" persistent-hint />
              <v-text-field v-model.number="row.user" label="主体日额度（token）" type="number" min="0" hint="同一账号在账务日内的上限，可填 0；留空表示不设该维度" persistent-hint />
              <v-text-field v-model.number="row.scene" label="群日额度（token，可选）" type="number" min="0" hint="同一场景在账务日内的上限，可填 0；留空表示本场景未配置该维度" persistent-hint />
            </div>
            <p v-for="problem in quotaProblemsFor(index)" :key="problem.message" class="policy-error">{{ problem.message }}</p>
          </div>
          <div v-if="!quotaRows.length" class="wide muted">当前没有具名策略；不配置时各维度不设 token 上限，由期限和消息上限结束。</div>
          <div class="wide actions"><v-btn variant="tonal" :disabled="!!busy" @click="quotaRows.push({name:'',work:null,user:null,scene:null})">添加一项策略</v-btn></div>
          <p class="wide muted">这里改的是往后新建工作的上限；已在执行的工作保留创建时的快照。已保存的精确取值在下方 JSON 里逐字对照。</p>
          <ul v-if="quotaProblems.length" class="wide error-summary">
            <li v-for="problem in quotaProblems" :key="problem.message"><button class="error-link" type="button" @click="focusPolicy(problem.key)">{{ problem.message }}</button></li>
          </ul>
        </template>
        <template v-if="quotaRaw"><v-textarea :model-value="quotaText" readonly label="策略（JSON）" rows="10" class="wide runtime-json" hint="已保存取值的只读对照，没有保存入口；改数值请返回表单。切换视图不会丢掉未保存的表单草稿。" persistent-hint /><p class="wide muted">这是已保存取值的只读视图，没有保存按钮；改数值请返回表单编辑。</p></template>
        <p v-if="Object.keys(rawPolicies).length" class="wide muted">有 {{ Object.keys(rawPolicies).length }} 项策略的形状不是这三个字段（{{ Object.keys(rawPolicies).join('、') }}），表单原样保留它们，只在保存时一起写回。</p>
        <ResourceViewer v-if="!quotaRaw" class="wide" title="已保存的精确取值（只读对照）" :content="quotaText" />
        <v-btn v-if="!Object.keys(rawPolicies).length" class="wide" variant="text" :disabled="!!busy" @click="toggleQuotaRaw">{{ quotaRaw?'返回表单编辑':'查看已保存 JSON' }}</v-btn>
        <v-btn v-if="!quotaRaw" type="submit" color="primary" :loading="busy==='resources'" :disabled="!!busy||!quotaDirty">保存额度策略</v-btn>
        <span v-if="quotaDirty" class="muted">有未保存修改</span>
      </v-form>
    </v-card>
    <v-card v-if="tab==='members'&&members!==null" class="pa-5 form-card">
      <div class="section-header"><h2>主播与订阅对象</h2><v-btn variant="tonal" color="primary" :disabled="!!busy" @click="addMember">添加对象</v-btn></div>
      <p class="muted my-3">这里登记的是 B 站主播与订阅对象，不是群详情里的 QQ 参与者。名称与别名用于查询，B 站 UID 和直播间号确认实际对象。团体署名保持团体含义，不在这里自动展开。</p>
      <v-alert v-if="membersRestart" type="info" variant="tonal" class="mb-4">主播与订阅对象已保存，需手动重启后用于查询与采集。</v-alert>
      <p v-if="!members.length" class="muted py-4">尚未填写主播与订阅对象；动态与开播插件保持未就绪。</p>
      <v-form :disabled="!!busy" @submit.prevent="saveMembers">
        <v-card v-for="(member,index) in members" :key="index" variant="outlined" class="pa-4 mb-4">
          <div class="section-header mb-3"><h3>对象 {{ index+1 }}</h3><v-btn variant="text" color="error" :disabled="!!busy" @click="members.splice(index,1)">移除</v-btn></div>
          <div class="form-grid"><v-text-field v-model="member.name" label="显示名称" required /><v-text-field v-model="member.aliasText" label="别名（逗号或顿号分隔）" /><v-text-field v-model="member.bilibili_uid" label="B 站 UID" inputmode="numeric" required /><v-text-field v-model="member.room_id" label="直播间号" inputmode="numeric" required /></div>
        </v-card>
        <p class="muted mb-4">已被群订阅的对象需先在相应群中取消订阅，再移除或改名。</p>
        <v-btn type="submit" color="primary" :loading="busy==='members'" :disabled="!!busy||!membersDirty">保存主播与订阅对象</v-btn>
      </v-form>
    </v-card>
    <v-card v-if="tab==='delivery'&&shadow" class="pa-5 form-card">
      <h2>全局发送控制</h2>
      <div class="delivery-state my-4"><v-chip :color="shadow.enabled?'warning':'primary'">{{ shadow.enabled?'Shadow · 不实际发送':'按各群规则发送' }}</v-chip><v-btn :color="shadow.enabled?'warning':'primary'" variant="outlined" :loading="busy==='shadow'" :disabled="!!busy" @click="toggleShadow">{{ shadow.enabled?'关闭 Shadow':'开启 Shadow' }}</v-btn></div>
      <p class="muted mb-5">Shadow 只控制是否实际发送。群启用、普通聊天、命令、公告、主播订阅和 @全体统一在“本群设置”中保存。</p>
      <v-btn variant="tonal" color="primary" :to="{name:'scenes'}">管理各群设置</v-btn>
    </v-card>
    <v-card v-if="tab==='runtime'&&runtimeText!==null" class="pa-5 form-card">
      <h2>运行参数</h2>
      <p class="muted mt-2">普通文件交付由 file_delivery 和独立 send_file 授权控制。onebot_file_upload 默认为 null；当前仅提供 NapCat 的 upload_group_file_data_file_id 协议。填写实际版本，并核对仅文件资产目录挂到 /lenbot-files 的只读权限及真实 file_id 回执后，才能开启 deployment_verified。保存后需重启。</p>
      <p class="muted my-3">下面对照根配置已保存值与运行时当前发布值。编辑中的 JSON 尚未保存，不计入这两列。</p>
      <div class="budget-table-wrap"><table class="budget-table"><caption>执行预算</caption><thead><tr><th scope="col">范围</th><th scope="col">已保存</th><th scope="col">当前发布</th></tr></thead><tbody><tr v-for="item in executionBudgets" :key="item.key"><th scope="row">{{ item.label }}</th><td>{{ budgetText(runtimeSavedBudgets[item.key], item.unit) }}</td><td>{{ budgetText(runtimeEffectiveBudgets[item.key], item.unit) }}</td></tr></tbody></table></div>
      <p class="muted my-4">新对话与新建工作采用当前发布预算；已有工作及其恢复保留创建时的上限、期限和累计用量。改变设置不会重开已有结果或失败工作。</p>
      <v-alert v-if="runtimeRestart" type="info" variant="tonal" class="mb-4">另有需重建组件的配置等待手动重启；上表单独显示这五项预算的当前发布值。</v-alert>
      <p class="muted my-3">常用执行预算用下面的数字框改；其余字段仍通过完整 JSON。留空表示该维度不设限。改这里会写进同一份草稿。</p>
      <v-form :disabled="!!busy" @submit.prevent="saveRuntime">
        <v-switch :model-value="heartbeatDraft.heartbeat_enabled || false" label="启用公共研究心跳" color="primary" @update:model-value="value=>setHeartbeat('heartbeat_enabled',value)" />
        <v-textarea :model-value="(heartbeatDraft.heartbeat_topics || []).join('\n')" label="公共研究主题（每行一项）" rows="3" hint="最多 20 项，每项 200 字；没有主题或有效兴趣时允许零研究。只保存研究结果与兴趣，不发布群消息。保存后需手动重启。" persistent-hint @update:model-value="value=>setHeartbeat('heartbeat_topics',value.split('\n').map(item=>item.trim()).filter(Boolean))" />
        <div class="form-grid mb-4">
          <v-text-field v-for="item in executionBudgets" :key="item.key" :model-value="runtimeBudgetValue(item.key)"
            :label="item.label+'（'+item.unit+'）'" type="number" :hint="'留空即不设限'" persistent-hint
            @update:model-value="value=>setRuntimeBudget(item.key,value)" />
        </div>
        <v-textarea v-model="runtimeText" label="运行参数 JSON（含其余字段）" rows="16" spellcheck="false" class="runtime-json" />
        <div class="actions"><v-btn type="submit" color="primary" :loading="busy==='runtime'" :disabled="!!busy||!runtimeDirty">保存运行参数</v-btn><span v-if="runtimeDirty" class="muted">有未保存修改</span></div>
      </v-form>
    </v-card>
    <v-card v-if="tab==='account'&&me" class="pa-5 form-card"><div class="section-header"><h2>登录账户</h2><v-chip :color="me.is_default_password?'warning':'default'">{{ me.is_default_password?'仍使用初始密码':'已修改初始密码' }}</v-chip></div><p class="my-4">{{ me.username }} · 上次登录 {{ fmtTime(me.last_login_at) }}</p><v-form :disabled="!!busy" class="form-grid" @submit.prevent="changePassword"><v-text-field v-model="passwords.current_password" type="password" autocomplete="current-password" label="当前密码" required /><v-text-field v-model="passwords.new_password" type="password" autocomplete="new-password" label="新密码（至少 6 位）" minlength="6" required /><div class="actions wide"><v-btn type="submit" color="primary" :loading="busy==='password'" :disabled="!!busy||!passwords.current_password||passwords.new_password.length<6">更新密码</v-btn><v-btn variant="outlined" :disabled="!!busy" @click="signOut">退出登录</v-btn></div></v-form></v-card>
    <v-expansion-panels v-if="tab==='account'&&me" class="danger-zone">
      <v-expansion-panel title="高级危险区：重置全部对话数据">
        <v-expansion-panel-text>
          <p class="mb-3">Reset 是独立的破坏性管理动作，需要当次明确授权。执行记录与额度记录的清理口径没有核定前，<strong>不要把它当作修复路径</strong>。</p>
          <p class="mb-2"><strong>会删除</strong>：全部群聊和私聊的原话与会话、摘要与自动认识、自动技能与其候选、工作与检查点、任务与等待、工具资料、调用账、聊天图片和场景上下文；先停止认知、维护、工作和投递再执行，并在管理记录里追加一条操作事件。</p>
          <p class="mb-2"><strong>会保留</strong>：登录、根配置、人工表达样例、运营表情库及其来源、人格、Shadow、QQ 回复白名单、各群设置与能力授予。人工样例只重置使用计数。</p>
          <p class="mb-3"><strong>不处理</strong>：<code>usage_reservations</code>、<code>execution_runs</code>、<code>execution_events</code> 不在清理表列表内。既有额度预占和执行记录会留下，页面也不能证明外部容器已经结束；这些行需要另行核对归属与处置，不能直接删除残留。</p>
          <v-btn color="error" variant="outlined" :disabled="!!busy" @click="resetConfirm=true">Reset 对话数据</v-btn>
        </v-expansion-panel-text>
      </v-expansion-panel>
    </v-expansion-panels>

    <v-dialog v-model="resetConfirm" max-width="620" :persistent="busy==='reset'"><v-card title="确认 Reset 全部对话数据"><v-card-text><v-alert type="error" variant="tonal" class="mb-4">此操作会清空全部群聊和私聊的会话数据，无法从页面撤销。</v-alert><p>先停止认知、维护、工作和投递，再删除对话、认识、任务、工作、工具资料、调用账、聊天图片和场景上下文。</p><p class="mt-3">保留运行配置、人工表达样例与运营表情库（含来源记录），包括模型、OneBot、人格、登录、Shadow、QQ 白名单、各群设置与能力授予。</p><p class="mt-3">额度预占与执行记录不在清理范围内，执行后仍需单独核对归属；页面的成功结果不表示外部容器已经结束。</p><v-alert v-if="error" type="error" variant="tonal" class="mt-3">{{ error }}</v-alert></v-card-text><v-card-actions><v-spacer /><v-btn :disabled="!!busy" @click="resetConfirm=false">取消</v-btn><v-btn color="error" :loading="busy==='reset'" :disabled="!!busy" @click="resetData">确认清空对话数据</v-btn></v-card-actions></v-card></v-dialog>
  </div>
</template>
<style scoped>
.budget-table-wrap{overflow-x:auto}.budget-table{width:100%;border-collapse:collapse;text-align:left;font-size:14px}.budget-table caption{text-align:left;font-weight:600;padding:8px 0 12px}.budget-table th,.budget-table td{padding:12px;border-bottom:1px solid var(--line);white-space:nowrap}.budget-table thead{background:rgb(var(--v-theme-surface-variant))}.budget-table tbody th{font-weight:500}.preset-field{margin-bottom:12px}.runtime-json :deep(textarea){font-family:monospace;font-size:13px;line-height:1.6}

.form-card{max-width:1000px;width:100%}.grant-card{border:1px solid #e2e8f0;border-radius:8px;padding:16px;margin-bottom:18px}.grant-card h3{font-size:14px;font-weight:650;margin-bottom:12px}.impact-summary{border-left:3px solid rgb(var(--v-theme-primary));padding:12px 14px;margin:16px 0;background:rgb(var(--v-theme-surface-variant));max-width:1000px}.impact-summary p{margin:4px 0;font-size:13px;line-height:1.7}.section-header{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}.section-header h2,.form-card>h2{font-size:20px}.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;align-items:start}.wide{grid-column:1/-1}.form-grid>.v-btn{justify-self:start}.actions,.meta,.delivery-state,.saved-scenes{display:flex;gap:8px 12px;flex-wrap:wrap;align-items:center}.meta{font-size:13px;color:#64748b}.example-row{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;padding:24px 0;border-bottom:1px solid #e2e8f0}.example-row:last-child{border:0;padding-bottom:0}.example-main{min-width:0;flex:1}.example-row>.actions{max-width:220px;justify-content:flex-end}.example-context{white-space:pre-wrap;line-height:1.65;color:#64748b;overflow-wrap:anywhere}.example-body{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0;align-items:flex-start}.example-body p{flex-basis:100%;white-space:pre-wrap;line-height:1.8;overflow-wrap:anywhere}.example-body img{max-width:180px;max-height:180px;object-fit:contain}.part-toolbar{display:flex;gap:12px;align-items:center;margin-bottom:16px;flex-wrap:wrap}.part-toolbar>.v-input{flex:1;min-width:140px;max-width:180px}.part-image{display:flex;gap:16px;align-items:center;flex-wrap:wrap}.part-image img{max-width:100%;height:170px;object-fit:contain}.media-filter{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:center}.media-picker{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}.media-picker img{width:100%;height:150px;object-fit:contain;background:#f4f6f9}.media-picker p{overflow-wrap:anywhere;min-height:3em}.danger-zone{max-width:1000px;margin-top:12px}
.error-summary{list-style:none;padding:0;margin:0;display:grid;gap:4px}.policy-row{border:1px solid #e2e8f0;border-radius:8px;padding:16px}.policy-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.policy-heading h3{font-size:14px;font-weight:650}.policy-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.policy-error{color:#b3261e;font-size:13px;margin:8px 0 0}.settings-view p{line-height:1.7}@media(max-width:650px){.form-grid{grid-template-columns:minmax(0,1fr)}.policy-fields{grid-template-columns:minmax(0,1fr)}.example-row{flex-direction:column}.example-row>.actions{max-width:none;justify-content:flex-start}.section-header{align-items:flex-start}.media-picker{grid-template-columns:repeat(2,minmax(0,1fr))}.part-toolbar>.actions{width:100%}.example-body img{max-width:140px;max-height:140px}}
</style>
