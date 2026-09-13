<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, fmtTime } from '../api.js'
import { logout } from '../composables/useAuth.js'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'
import PageHeader from '../components/PageHeader.vue'
import ScopeSelect from '../components/ScopeSelect.vue'
import EntityLink from '../components/EntityLink.vue'
import ResourceViewer from '../components/ResourceViewer.vue'

const route = useRoute(), router = useRouter()
const tabs = [{value:'persona',title:'人格与表达'},{value:'attention',title:'注意力'},{value:'access',title:'QQ 回复资格'},{value:'time',title:'业务时间'},{value:'members',title:'成员'},{value:'connection',title:'连接'},{value:'delivery',title:'发送'},{value:'runtime',title:'运行参数'},{value:'account',title:'账户'}]
const tab = computed(() => tabs.some(item=>item.value===route.query.tab) ? route.query.tab : 'persona')
const loading = ref(false), error = ref(''), message = ref(''), readAt = ref({}), busy = ref('')
const persona = ref(null), personaOriginal = ref(''), attention = ref(null), attentionOriginal = ref('')
const runtimeText = ref(null), runtimeOriginal = ref(''), runtimeRestart = ref(false)
const runtimeSavedBudgets = ref({}), runtimeEffectiveBudgets = ref({})
const executionBudgets = [{key:'conversation_max_steps',label:'每轮对话模型调用',unit:'次'},
  {key:'conversation_max_tool_calls',label:'每轮对话工具调用',unit:'次'},
  {key:'job_max_steps',label:'同一工作累计模型调用',unit:'次'},
  {key:'job_max_tool_calls',label:'同一工作累计工具调用',unit:'次'},
  {key:'job_max_seconds',label:'同一工作累计执行时间',unit:'秒'}]
const onebot = ref(null), connection = ref(null), connectionOriginal = ref(''), shadow = ref(null)
const accessText = ref(null), accessOriginal = ref('')
const grants = ref([]), grantsOriginal = ref('')
const timeDraft = ref(null), timeOriginal = ref(''), timeConfigured = ref(false), timeLoaded = ref(false), timeRestart = ref(false)
const members = ref(null), membersOriginal = ref(''), membersRestart = ref(false)
const weekdays = [{title:'周一',value:0},{title:'周二',value:1},{title:'周三',value:2},{title:'周四',value:3},{title:'周五',value:4},{title:'周六',value:5},{title:'周日',value:6}]
const me = ref(null), passwords = ref({current_password:'',new_password:''}), leavingAfterLogout = ref(false)
const exemplars = ref([]), exampleOpen = ref(false), editingExample = ref(''), example = ref(null), exampleOriginal = ref('')
const sourceExample = ref({scene_id:'', event_id:'', context:'', tag:''})
const preset = ref(null), presetLoading = ref(false), resetConfirm = ref(false)
const selectedPresetFields = ref([]), selectedPresetExamples = ref([]), presetExampleResults = ref({}), presetMessage = ref('')
const mediaOpen = ref(false), mediaRows = ref([]), mediaTotal = ref(0), mediaPage = ref(1), mediaQuery = ref(''), mediaSearch = ref(''), mediaLoading = ref(false), mediaError = ref(''), mediaPart = ref(0), mediaScope = ref('global-safe')
const personaLabels = {identity_name:'机器人名字',identity_persona:'身份背景',identity_core:'性格与相处方式',character_context:'角色资料与梗',conversation_style:'说话方式'}
const personaDirty = computed(()=>!!persona.value&&JSON.stringify(persona.value)!==personaOriginal.value)
const attentionDirty = computed(()=>!!attention.value&&JSON.stringify(attention.value)!==attentionOriginal.value)
const runtimeDirty = computed(()=>runtimeText.value!==null&&runtimeText.value!==runtimeOriginal.value)
const connectionDirty = computed(()=>!!connection.value&&JSON.stringify(connection.value)!==connectionOriginal.value)
const accessDirty = computed(()=>accessText.value!==null&&(accessText.value!==accessOriginal.value||JSON.stringify(grants.value)!==grantsOriginal.value))
const timeDirty = computed(()=>timeDraft.value!==null&&JSON.stringify(timeDraft.value)!==timeOriginal.value)
const membersDirty = computed(()=>members.value!==null&&JSON.stringify(members.value)!==membersOriginal.value)
const exampleDirty = computed(()=>exampleOpen.value&&JSON.stringify(example.value)!==exampleOriginal.value)
const dirty = computed(()=>!leavingAfterLogout.value&&(personaDirty.value||attentionDirty.value||runtimeDirty.value||connectionDirty.value||accessDirty.value||timeDirty.value||membersDirty.value||exampleDirty.value||!!passwords.value.current_password||!!passwords.value.new_password))
const { confirmLeave } = useUnsavedChanges(dirty)
let requestId = 0, mediaRequest = 0, presetRequest = 0
const clone = value => JSON.parse(JSON.stringify(value))
const imageUrl = (assetId,scene='') => `/api/media/${encodeURIComponent(assetId)}/file?scene_id=${encodeURIComponent(scene||'global-safe')}`
async function load() {
  const currentTab = tab.value, request = ++requestId
  loading.value = true; error.value = ''
  try {
    if (currentTab==='persona') {
      const [settings, samples] = await Promise.all([api('/api/settings/persona'),api('/api/voice/exemplars')])
      if (request!==requestId) return
      if (!personaDirty.value) {
        persona.value = Object.fromEntries(Object.keys(personaLabels).map(key=>[key,settings[key]]))
        persona.value.addressNames = settings.address_names.join('、'); personaOriginal.value = JSON.stringify(persona.value)
      }
      exemplars.value = samples.exemplars
    } else if (currentTab==='attention') {
      const settings = await api('/api/settings/attention'); if (request!==requestId) return
      if (!attentionDirty.value) { const {attention_keywords,...rest}=settings; attention.value={...rest,keywords:attention_keywords.join('\n')}; attentionOriginal.value=JSON.stringify(attention.value) }
    } else if (currentTab==='access') {
      const settings = await api('/api/settings/access'); if (request!==requestId) return
      if (!accessDirty.value) {
        accessText.value=settings.qq_reply_whitelist.join('\n'); accessOriginal.value=accessText.value
        grants.value=(settings.capability_grants||[]).map(grant=>({...grant,capabilityText:grant.capabilities.join(', ')}))
        grantsOriginal.value=JSON.stringify(grants.value)
      }
    } else if (currentTab==='time') {
      const settings = await api('/api/settings/time'); if (request!==requestId) return
      timeLoaded.value = true
      timeConfigured.value = settings !== null
      if (!timeDirty.value) { timeDraft.value=settings; timeOriginal.value=JSON.stringify(settings) }
    } else if (currentTab==='members') {
      const settings = await api('/api/settings/members'); if (request!==requestId) return
      if (!membersDirty.value) { members.value=settings.map(item=>({...item,aliasText:item.aliases.join('、')})); membersOriginal.value=JSON.stringify(members.value) }
    } else if (currentTab==='connection') {
      const settings = await api('/api/websocket/status'); if (request!==requestId) return
      onebot.value=settings
      if (!connectionDirty.value) { connection.value={connection_mode:settings.connection_mode,action_transport:settings.action_transport,ws_url:settings.ws_url,http_url:settings.http_url,host:settings.host,port:settings.port,access_token:''}; connectionOriginal.value=JSON.stringify(connection.value) }
    } else if (currentTab==='delivery') {
      const settings=await api('/api/cockpit/shadow'); if(request!==requestId)return
      shadow.value=settings
    } else if (currentTab==='runtime') {
      const result=await api('/api/settings/runtime');if(request!==requestId)return
      if(!runtimeDirty.value){runtimeText.value=JSON.stringify(result.settings,null,2);runtimeOriginal.value=runtimeText.value}
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
async function savePersona() {
  if(busy.value)return
  busy.value='persona';error.value='';message.value=''
  try {
    const {addressNames,...fields}=persona.value
    const result=await api('/api/settings/persona',{method:'POST',body:JSON.stringify({...fields,address_names:[...new Set(addressNames.split(/[\n,，、]+/).map(item=>item.trim()).filter(Boolean))]})})
    personaOriginal.value=JSON.stringify(persona.value);message.value=result.message;await load()
  }catch(e){error.value=e.message}finally{busy.value=''}
}
async function saveAttention() {
  if(busy.value)return
  busy.value='attention';error.value='';message.value=''
  try {
    const {keywords,...fields}=attention.value
    const result=await api('/api/settings/attention',{method:'PATCH',body:JSON.stringify({...fields,attention_keywords:[...new Set(keywords.split('\n').map(item=>item.trim()).filter(Boolean))]})})
    const {attention_keywords,...rest}=result.settings;attention.value={...rest,keywords:attention_keywords.join('\n')};attentionOriginal.value=JSON.stringify(attention.value);message.value=result.message
  }catch(e){error.value=e.message}finally{busy.value=''}
}
function beginTimeConfiguration() {
  timeDraft.value = {timezone:'',week_start:null,afternoon_start:'',afternoon_end:''}
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
  busy.value='access'; error.value=''; message.value=''
  try {
    const values = accessText.value.split(/[,，\s]+/).filter(Boolean).map(value=>positiveInteger(value,'QQ 账号'))
    const result = await api('/api/settings/access',{method:'PUT',body:JSON.stringify({qq_reply_whitelist:values,capability_grants:grants.value.map(grant=>({
      grant_id:grant.grant_id,revision:grant.revision,operator_id:grant.operator_id,
      principal_type:grant.principal_type,principal_id:grant.principal_id,
      scene_id:grant.scene_id||null,system_scope:grant.system_scope||null,
      capabilities:grant.capabilityText.split(/[,，\s]+/).filter(Boolean),
      expires_at:grant.expires_at||null,resource_policy:grant.resource_policy||null,
      concurrency:grant.concurrency||null,enabled:!!grant.enabled}))})})
    const settings=result.settings
    accessText.value=settings.qq_reply_whitelist.join('\n'); accessOriginal.value=accessText.value
    grants.value=(settings.capability_grants||[]).map(grant=>({...grant,capabilityText:grant.capabilities.join(', ')}))
    grantsOriginal.value=JSON.stringify(grants.value); message.value=result.message
  } catch(e) { error.value=e.message } finally { busy.value='' }
}
function addGrant() {
  grants.value.push({grant_id:'',revision:1,operator_id:'',principal_type:'human',principal_id:'',
    scene_id:'',system_scope:'',capabilityText:'',expires_at:null,resource_policy:'',concurrency:null,enabled:false})
}
async function saveTime() {
  if (busy.value || !timeDraft.value) return
  busy.value='time'; error.value=''; message.value=''
  try {
    const result = await api('/api/settings/time',{method:'PUT',body:JSON.stringify(timeDraft.value)})
    timeDraft.value=result.settings; timeOriginal.value=JSON.stringify(result.settings); timeConfigured.value=true; timeRestart.value=result.requires_restart; message.value=result.message
  } catch(e) { error.value=e.message } finally { busy.value='' }
}
async function saveMembers() {
  if (busy.value || !members.value) return
  busy.value='members'; error.value=''; message.value=''
  try {
    const values=members.value.map(item=>({name:item.name.trim(),aliases:item.aliasText.split(/[\n,，、]+/).map(value=>value.trim()).filter(Boolean),bilibili_uid:positiveInteger(item.bilibili_uid,'B 站 UID'),room_id:positiveInteger(item.room_id,'直播间号')}))
    const result=await api('/api/settings/members',{method:'PUT',body:JSON.stringify(values)})
    members.value=result.settings.map(item=>({...item,aliasText:item.aliases.join('、')})); membersOriginal.value=JSON.stringify(members.value); membersRestart.value=result.requires_restart; message.value=result.message
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
    const result = await api('/api/settings/runtime', {method:'PATCH',body:JSON.stringify(settings)})
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
    const result = await api('/api/websocket/config', {method:'POST',body:JSON.stringify(body)})
    connection.value.access_token = ''
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
function editExample(item=null) {
  editingExample.value=item?.id||''
  example.value=item?{context:item.context,scene_id:item.scene_id,tag:item.tag,segments:clone(item.segments)}:{context:'',scene_id:'',tag:'',segments:[{type:'text',text:''}]}
  exampleOriginal.value=JSON.stringify(example.value);exampleOpen.value=true
}
function closeExample() {
  if(busy.value)return
  if(exampleDirty.value&&!window.confirm('放弃尚未保存的表达样例？'))return
  exampleOpen.value=false;example.value=null
}
function changePart(index,type){example.value.segments[index]=type==='text'?{type,text:''}:{type,asset_id:''}}
function addPart(type){if(example.value.segments.length<20)example.value.segments.push(type==='text'?{type,text:''}:{type,asset_id:''})}
function movePart(index,direction){const parts=example.value.segments,target=index+direction;if(target>=0&&target<parts.length)[parts[index],parts[target]]=[parts[target],parts[index]]}
async function saveExample(){
  if(busy.value)return
  busy.value='example';error.value='';message.value=''
  try{await api('/api/voice/exemplars'+(editingExample.value?'/'+encodeURIComponent(editingExample.value):''),{method:editingExample.value?'PUT':'POST',body:JSON.stringify(example.value)});exampleOpen.value=false;example.value=null;message.value='表达样例已保存';await load()}
  catch(e){error.value=e.message}finally{busy.value=''}
}
async function changeExample(item,remove=false){
  if(busy.value)return
  if(!window.confirm(remove?'删除这条表达样例？':`${item.enabled?'停用':'启用'}这条表达样例？后续对话将按新的状态携带样例。`))return
  busy.value=`example:${item.id}`;error.value='';message.value=''
  try{await api(remove?'/api/voice/exemplars/'+encodeURIComponent(item.id):'/api/voice/exemplars/toggle',{method:remove?'DELETE':'POST',body:remove?undefined:JSON.stringify({example_id:item.id,enabled:!item.enabled})});message.value=remove?'表达样例已删除':'表达样例状态已保存';await load()}
  catch(e){error.value=e.message}finally{busy.value=''}
}
async function createFromSentMessage(){
  if(busy.value || !sourceExample.value.scene_id.trim() || !sourceExample.value.event_id.trim()) return
  busy.value='example-source'; error.value=''; message.value=''
  try {
    await api('/api/voice/exemplars/from-message',{method:'POST',body:JSON.stringify({...sourceExample.value,scene_id:sourceExample.value.scene_id.trim(),event_id:sourceExample.value.event_id.trim(),context:sourceExample.value.context.trim(),tag:sourceExample.value.tag.trim()})})
    sourceExample.value={scene_id:'',event_id:'',context:'',tag:''}; message.value='已从真实送达消息创建表达样例，请继续编辑或停用'; await load()
  } catch(e){ error.value=e.message } finally { busy.value='' }
}
function openMedia(index){mediaPart.value=index;mediaScope.value=example.value.scene_id||'global-safe';mediaQuery.value='';mediaSearch.value='';mediaPage.value=1;mediaOpen.value=true;loadMedia()}
async function loadMedia(){
  const request=++mediaRequest;mediaLoading.value=true;mediaError.value=''
  try{const result=await api('/api/media?'+new URLSearchParams({scene_id:mediaScope.value,query:mediaSearch.value,curated:'true',enabled:'true',page:String(mediaPage.value),page_size:'48'}));if(request!==mediaRequest)return;mediaRows.value=result.items;mediaTotal.value=result.total}
  catch(e){if(request===mediaRequest)mediaError.value=e.message}finally{if(request===mediaRequest)mediaLoading.value=false}
}
function chooseMedia(asset){example.value.segments[mediaPart.value].asset_id=asset.id;mediaOpen.value=false}
async function previewPreset(){
  const request = ++presetRequest
  presetLoading.value = true
  error.value = ''
  try {
    const result = await api('/api/settings/persona/diana')
    if (request !== presetRequest) return
    preset.value = result
    selectedPresetFields.value = []
    selectedPresetExamples.value = []
    presetExampleResults.value = {}
    presetMessage.value = ''
  } catch (e) {
    if (request === presetRequest) error.value = e.message
  } finally {
    if (request === presetRequest) presetLoading.value = false
  }
}
function fillPresetFields(){
  if (busy.value || !preset.value || !persona.value) return
  for (const key of selectedPresetFields.value) {
    persona.value[key] = preset.value.fields[key]
  }
  presetMessage.value = `已将 ${selectedPresetFields.value.length} 个字段填入人格草稿，请关闭预览后保存人格。`
  selectedPresetFields.value = []
}
async function savePresetExamples(){
  if (busy.value || !preset.value) return
  const selected = preset.value.examples.filter(item=>selectedPresetExamples.value.includes(item.id))
  busy.value = 'preset-examples'
  error.value = ''
  try {
    for (const item of selected) {
      const body = {scene_id:item.scene_id,context:item.context,tag:item.tag,segments:item.segments}
      presetExampleResults.value[item.id] = {status:'saving',message:'正在保存'}
      try {
        const result = await api('/api/voice/exemplars', {method:'POST',body:JSON.stringify(body)})
        presetExampleResults.value[item.id] = {status:'saved',message:`已添加样例 ${result.exemplar.id}`}
        selectedPresetExamples.value = selectedPresetExamples.value.filter(id=>id!==item.id)
      } catch (e) {
        presetExampleResults.value[item.id] = {status:'error',message:e.message}
      }
    }
    await load()
  } finally {
    busy.value = ''
  }
}
async function resetData(){
  if(busy.value)return
  busy.value='reset';error.value='';message.value=''
  try{await api('/api/settings/reset',{method:'POST'});resetConfirm.value=false;message.value='全部对话数据已清空，运行配置与运营资料已保留';await load()}
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
    <template v-if="tab==='persona'&&persona">
      <v-card class="pa-5 form-card"><div class="section-header"><div><h2>人格与说话方式</h2><p class="muted mt-2">角色资料用于表达，不能作为群友事实或现实能力的依据。</p></div><v-btn variant="tonal" :loading="presetLoading" :disabled="!!busy" @click="previewPreset">查看嘉然模板</v-btn></div><v-form :disabled="!!busy" class="form-grid mt-5" @submit.prevent="savePersona"><v-text-field v-model="persona.identity_name" label="机器人名字" /><v-text-field v-model="persona.addressNames" label="呼唤昵称" hint="用逗号或顿号分隔；呼唤提供观察机会，是否回应由模型决定。" persistent-hint /><v-textarea v-for="key in ['identity_persona','identity_core','character_context','conversation_style']" :key="key" v-model="persona[key]" :label="personaLabels[key]" :rows="key==='character_context'?6:4" auto-grow class="wide" /><v-btn type="submit" color="primary" :loading="busy==='persona'" :disabled="!!busy||!personaDirty">保存人格并立即生效</v-btn><span v-if="personaDirty" class="muted">有未保存修改</span></v-form></v-card>
      <v-card class="pa-5"><div class="section-header"><div><h2>表达样例</h2><p class="muted mt-2">按保存顺序提供，可使用文字、单图或混排。这些是人工表达示范。</p></div><v-btn color="primary" variant="tonal" :disabled="!!busy" @click="editExample()">添加样例</v-btn></div><v-card variant="tonal" class="pa-4 mb-5"><h3>从真实送达消息创建</h3><p class="muted my-2">只接受已确认真实发送的 Bot 消息；Shadow、草稿和 unknown 回执会被拒绝。</p><div class="form-grid"><v-text-field v-model="sourceExample.scene_id" label="场景 ID" placeholder="group:123" /><v-text-field v-model="sourceExample.event_id" label="MESSAGE_SENT 事件 ID" /><v-text-field v-model="sourceExample.context" label="表达语境" /><v-text-field v-model="sourceExample.tag" label="标签" /><v-btn color="primary" variant="outlined" :loading="busy==='example-source'" :disabled="!!busy||!sourceExample.scene_id.trim()||!sourceExample.event_id.trim()" @click="createFromSentMessage">创建并进入样例列表</v-btn></div></v-card><p v-if="!exemplars.length" class="muted py-6">尚无人工表达样例</p><article v-for="(item,index) in exemplars" :key="item.id" class="example-row"><div class="example-main"><div class="meta mb-3"><v-chip size="small">第 {{ index+1 }} 条</v-chip><v-chip size="small" :color="item.enabled?'success':'default'">{{ item.enabled?'已启用':'已停用' }}</v-chip><span>{{ item.scene_id||'所有场景' }}</span><span v-if="item.tag">{{ item.tag }}</span></div><p class="example-context clamp-2">{{ item.context||'通用表达' }}</p><div class="example-body"><template v-for="(part,partIndex) in item.segments" :key="partIndex"><p v-if="part.type==='text'">{{ part.text }}</p><img v-else :src="imageUrl(part.asset_id,item.scene_id)" alt="运营表达样例" loading="lazy" /></template></div><v-alert v-if="item.available===false" type="warning" variant="tonal" density="compact">{{ item.unavailable_reason }}</v-alert></div><div class="actions"><v-btn variant="outlined" :disabled="!!busy" @click="editExample(item)">编辑</v-btn><v-btn variant="text" :disabled="!!busy" @click="changeExample(item)">{{ item.enabled?'停用':'启用' }}</v-btn><v-btn color="error" variant="text" :disabled="!!busy" @click="changeExample(item,true)">删除</v-btn></div></article></v-card>
    </template>
    <v-card v-if="tab==='attention'&&attention" class="pa-5 form-card"><h2>注意力与旁听</h2><p class="muted my-3">关键词和低频抽样提供观察机会。明确 @、回复 Bot、受托工作和有效等待关系继续提供机会；仅处于关注窗口的普通发言与弱机会回复不自动续期。</p><v-form :disabled="!!busy" class="form-grid" @submit.prevent="saveAttention"><v-textarea v-model="attention.keywords" label="运营关键词（每行一项）" rows="4" class="wide" /><v-text-field v-model.number="attention.attention_sample_window_seconds" type="number" min="1" step="1" label="抽样窗口（秒）" required /><v-text-field v-model.number="attention.attention_sample_probability" type="number" min="0" max="1" step="0.01" label="每窗口抽样概率（0—1）" required /><v-expansion-panels class="wide"><v-expansion-panel title="高级参数"><v-expansion-panel-text><div class="form-grid"><v-text-field v-model.number="attention.attention_keyword_cooldown_seconds" type="number" min="0" step="1" label="关键词观察冷却（秒）" required /><v-text-field v-model.number="attention.attention_focus_seconds" type="number" min="1" step="1" label="有效互动送达后的关注窗口（秒）" required /><v-text-field v-model.number="attention.conversation_recent_tokens" type="number" min="500" step="100" label="近期原话预算（文本 token）" required class="wide" /></div></v-expansion-panel-text></v-expansion-panel></v-expansion-panels><v-btn type="submit" color="primary" :loading="busy==='attention'" :disabled="!!busy||!attentionDirty">保存注意力参数</v-btn></v-form></v-card>
    <v-card v-if="tab==='connection'&&onebot&&connection" class="pa-5 form-card"><div class="section-header"><h2>连接 OneBot</h2><v-chip :color="onebot.connected?'success':'warning'">{{ onebot.connected?'已连接':'未连接' }}</v-chip></div><p class="muted my-3">{{ onebot.connected?'已取得 OneBot 连接。':onebot.connection_mode==='forward_ws'?'当前未连接，请查看端点与最近错误。':'等待 OneBot 主动接入。' }}<span v-if="onebot.self_id"> 已识别账号：{{ onebot.self_id }}</span></p><v-alert v-if="onebot.last_error" type="error" variant="tonal" class="mb-4">{{ onebot.last_error }}</v-alert><v-form :disabled="!!busy" class="form-grid" @submit.prevent="saveConnection()"><v-select v-model="connection.connection_mode" label="消息连接方式" :items="[{title:'主动连接 OneBot',value:'forward_ws'},{title:'等待 OneBot 连接',value:'reverse_ws'}]" class="wide" /><v-text-field v-if="connection.connection_mode==='forward_ws'" v-model="connection.ws_url" label="WebSocket 端点" placeholder="ws://127.0.0.1:13001/" class="wide" required /><template v-else><v-text-field v-model="connection.host" label="监听地址" required /><v-text-field v-model.number="connection.port" type="number" min="1" max="65535" label="监听端口" required /></template><v-select v-model="connection.action_transport" label="发送传输" :items="[{title:'使用 WebSocket',value:'websocket'},{title:'使用 HTTP',value:'http'}]" /><v-text-field v-model="connection.http_url" label="HTTP 接口地址" :required="connection.action_transport==='http'" /><v-text-field v-model="connection.access_token" type="password" autocomplete="new-password" label="访问令牌" :placeholder="onebot.access_token_set?'已保存，留空保留':'填写 OneBot 访问令牌'" class="wide" /><div class="actions wide"><v-btn type="submit" color="primary" :loading="busy==='connection'" :disabled="!!busy||!connectionDirty">保存连接配置</v-btn><v-btn variant="outlined" :loading="busy==='http'" :disabled="!!busy" @click="checkHttp">检查当前 HTTP 连接</v-btn></div><p class="muted wide">连接配置保存后需手动重启服务生效。HTTP 检查只读取当前运行连接的状态。</p></v-form></v-card>
    <v-card v-if="tab==='access'&&accessText!==null" class="pa-5 form-card">
      <h2>QQ 回复白名单</h2>
      <p class="muted my-3">在已启用但关闭普通聊天的群中，白名单成员仍可正常提问和继续互动。白名单不会强制每条消息回复，也不授予管理员、跨群读取或 @全体权限；日程命令及引用评论仍保持安静。</p>
      <v-form :disabled="!!busy" @submit.prevent="saveAccess">
        <v-textarea v-model="accessText" label="QQ 账号" rows="6" hint="每行一个 QQ 账号，或用逗号分隔。这里填写 QQ 账号，不是 B 站 UID。空列表表示没有额外回复资格。" persistent-hint />
        <v-divider class="my-5" />
        <div class="section-header"><div><h2>能力授予</h2><p class="muted mt-2">只影响本计划新增的自主能力；普通聊天不需要这里的任何一条。未配置、已停用或已过期的授予一律不放行，撤销只阻止后续操作，已发出的字节无法撤回。</p></div><v-btn variant="tonal" color="primary" :disabled="!!busy" @click="addGrant">添加授予</v-btn></div>
        <p v-if="!grants.length" class="muted py-4">当前没有任何能力授予；新增自主能力保持关闭。</p>
        <article v-for="(grant,index) in grants" :key="index" class="mb-5">
          <div class="form-grid">
            <v-text-field v-model="grant.grant_id" label="授予 ID" required />
            <v-text-field v-model.number="grant.revision" type="number" min="1" label="版本号" required />
            <v-text-field v-model="grant.operator_id" label="签发运营者" hint="保存时按当前登录账号记录，不需要手工填写。" persistent-hint readonly />
            <v-select v-model="grant.principal_type" label="主体类型" :items="[{title:'人类',value:'human'},{title:'系统',value:'system'},{title:'插件',value:'plugin'}]" />
            <v-text-field v-model="grant.principal_id" label="主体标识" hint="人类填 QQ 账号，系统填 runtime/scheduler/operator:账号，插件填插件 ID；不是显示名。" persistent-hint required />
            <v-text-field v-if="grant.principal_type!=='system'" v-model="grant.scene_id" label="生效场景" placeholder="group:123" required />
            <v-text-field v-else v-model="grant.system_scope" label="系统范围" hint="明确的系统用途，例如 heartbeat。" required />
            <v-text-field v-model="grant.capabilityText" label="能力" hint="用逗号分隔：long_work、public_research、network_python、proactive_chat、interest_share、send_file、bilibili_authenticated_read、bilibili_like、bilibili_favorite。" persistent-hint required />
            <v-text-field v-model.number="grant.expires_at" type="number" label="有效期（绝对 Unix 时间）" hint="留空表示长期有效。" />
            <v-text-field v-model="grant.resource_policy" label="资源策略引用" hint="引用既有资源策略名称，不在这里填写额度数值。" />
            <v-text-field v-model.number="grant.concurrency" type="number" min="1" label="并发上限" />
            <v-switch v-model="grant.enabled" label="启用" color="primary" /></div>
          <v-btn variant="text" color="error" :disabled="!!busy" @click="grants.splice(index,1)">删除这条授予</v-btn>
        </article>
        <v-btn type="submit" color="primary" :loading="busy==='access'" :disabled="!!busy||!accessDirty">保存白名单与能力授予</v-btn>
      </v-form>
    </v-card>
    <v-card v-if="tab==='time'&&timeLoaded" class="pa-5 form-card">
      <h2>业务时间口径</h2>
      <p class="muted my-3">日程与群总结按照这里填写的时区、自然周和下午范围解释日期，不自动选择时区或补全天段。</p>
      <v-alert v-if="!timeConfigured" type="info" variant="tonal" class="mb-4">尚未保存业务时间；需要时间口径的新插件不能启用。</v-alert>
      <v-alert v-if="timeRestart" type="info" variant="tonal" class="mb-4">已保存，需手动重启后用于新查询。</v-alert>
      <v-btn v-if="!timeDraft&&!loading" variant="tonal" color="primary" :disabled="!!busy" @click="beginTimeConfiguration">填写业务时间</v-btn>
      <v-form v-if="timeDraft" :disabled="!!busy" class="form-grid" @submit.prevent="saveTime">
        <v-text-field v-model="timeDraft.timezone" label="IANA 时区" hint="填写业务实际采用的 IANA 时区名称。" persistent-hint required />
        <v-select v-model="timeDraft.week_start" label="自然周第一天" :items="weekdays" required />
        <v-text-field v-model="timeDraft.afternoon_start" label="下午开始" type="time" required />
        <v-text-field v-model="timeDraft.afternoon_end" label="下午结束（不含）" type="time" required />
        <v-btn type="submit" color="primary" :loading="busy==='time'" :disabled="!!busy||!timeDirty">保存业务时间</v-btn>
      </v-form>
    </v-card>
    <v-card v-if="tab==='members'&&members!==null" class="pa-5 form-card">
      <div class="section-header"><h2>成员与 B 站身份</h2><v-btn variant="tonal" color="primary" :disabled="!!busy" @click="addMember">添加成员</v-btn></div>
      <p class="muted my-3">成员名称与别名用于查询，B 站 UID 和直播间号用于确认实际对象。团体署名保持团体含义，不在这里自动展开成员。</p>
      <v-alert v-if="membersRestart" type="info" variant="tonal" class="mb-4">成员已保存，需手动重启后用于查询与采集。</v-alert>
      <p v-if="!members.length" class="muted py-4">尚未填写成员；动态与开播插件保持未就绪。</p>
      <v-form :disabled="!!busy" @submit.prevent="saveMembers">
        <v-card v-for="(member,index) in members" :key="index" variant="outlined" class="pa-4 mb-4">
          <div class="section-header mb-3"><h3>成员 {{ index+1 }}</h3><v-btn variant="text" color="error" :disabled="!!busy" @click="members.splice(index,1)">移除</v-btn></div>
          <div class="form-grid"><v-text-field v-model="member.name" label="成员名称" required /><v-text-field v-model="member.aliasText" label="别名（逗号或顿号分隔）" /><v-text-field v-model="member.bilibili_uid" label="B 站 UID" inputmode="numeric" required /><v-text-field v-model="member.room_id" label="直播间号" inputmode="numeric" required /></div>
        </v-card>
        <p class="muted mb-4">已被群订阅的成员需先在相应群中取消订阅，再移除或改名。</p>
        <v-btn type="submit" color="primary" :loading="busy==='members'" :disabled="!!busy||!membersDirty">保存成员</v-btn>
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
      <p class="muted my-3">下面对照根配置已保存值与运行时当前发布值。编辑中的 JSON 尚未保存，不计入这两列。</p>
      <div class="budget-table-wrap"><table class="budget-table"><caption>执行预算</caption><thead><tr><th scope="col">范围</th><th scope="col">已保存</th><th scope="col">当前发布</th></tr></thead><tbody><tr v-for="item in executionBudgets" :key="item.key"><th scope="row">{{ item.label }}</th><td>{{ runtimeSavedBudgets[item.key] ?? '未提供' }} {{ item.unit }}</td><td>{{ runtimeEffectiveBudgets[item.key] ?? '未提供' }} {{ item.unit }}</td></tr></tbody></table></div>
      <p class="muted my-4">新对话与新的工作执行段采用当前发布预算；已开始的一轮使用其预算快照。工作恢复保留累计用量，改变上限不会自动重开已有结果或失败工作。</p>
      <v-alert v-if="runtimeRestart" type="info" variant="tonal" class="mb-4">另有需重建组件的配置等待手动重启；上表单独显示这五项预算的当前发布值。</v-alert>
      <p class="muted my-3">预算、并发、媒体和维护等参数仍通过下方完整 JSON 保存。</p>
      <v-form :disabled="!!busy" @submit.prevent="saveRuntime">
        <v-textarea v-model="runtimeText" label="运行参数 JSON" rows="24" spellcheck="false" class="runtime-json" />
        <div class="actions"><v-btn type="submit" color="primary" :loading="busy==='runtime'" :disabled="!!busy||!runtimeDirty">保存运行参数</v-btn><span v-if="runtimeDirty" class="muted">有未保存修改</span></div>
      </v-form>
    </v-card>
    <v-card v-if="tab==='account'&&me" class="pa-5 form-card"><div class="section-header"><h2>登录账户</h2><v-chip :color="me.is_default_password?'warning':'default'">{{ me.is_default_password?'仍使用初始密码':'已修改初始密码' }}</v-chip></div><p class="my-4">{{ me.username }} · 上次登录 {{ fmtTime(me.last_login_at) }}</p><v-form :disabled="!!busy" class="form-grid" @submit.prevent="changePassword"><v-text-field v-model="passwords.current_password" type="password" autocomplete="current-password" label="当前密码" required /><v-text-field v-model="passwords.new_password" type="password" autocomplete="new-password" label="新密码（至少 6 位）" minlength="6" required /><div class="actions wide"><v-btn type="submit" color="primary" :loading="busy==='password'" :disabled="!!busy||!passwords.current_password||passwords.new_password.length<6">更新密码</v-btn><v-btn variant="outlined" :disabled="!!busy" @click="signOut">退出登录</v-btn></div></v-form></v-card>
    <v-expansion-panels class="danger-zone"><v-expansion-panel title="危险操作：重置全部对话数据"><v-expansion-panel-text><p class="mb-4">删除全部群聊和私聊的对话、认识、任务与工作、工具资料、聊天图片和场景上下文。保留运行配置、人工表达样例和运营表情库（含来源）。</p><v-btn color="error" variant="outlined" :disabled="!!busy" @click="resetConfirm=true">Reset 对话数据</v-btn></v-expansion-panel-text></v-expansion-panel></v-expansion-panels>

    <v-dialog :model-value="exampleOpen" max-width="880" scrollable :persistent="!!busy" @update:model-value="value=>!value&&closeExample()"><v-card><v-card-title class="section-header">{{ editingExample?'编辑表达样例':'添加表达样例' }}<v-btn variant="text" :disabled="!!busy" @click="closeExample">关闭</v-btn></v-card-title><v-card-text><v-alert v-if="error" type="error" variant="tonal" class="mb-4">{{ error }}</v-alert><v-form v-if="example" :disabled="!!busy" @submit.prevent="saveExample"><div class="form-grid"><v-textarea v-model="example.context" label="前文与语境" maxlength="8000" rows="3" class="wide" /><ScopeSelect v-model="example.scene_id" clearable /><v-text-field v-model="example.tag" label="样例标签" maxlength="200" /></div><p class="muted mb-4">范围留空适用于所有场景，只能使用公共运营素材。本群样例也可使用本群运营素材。</p><div class="section-header"><h3>表达片段</h3><div class="actions"><v-btn size="small" variant="tonal" :disabled="example.segments.length>=20" @click="addPart('text')">添加文字</v-btn><v-btn size="small" variant="tonal" :disabled="example.segments.length>=20" @click="addPart('image')">添加图片</v-btn></div></div><v-card v-for="(part,index) in example.segments" :key="index" variant="outlined" class="pa-4 my-3"><div class="part-toolbar"><span class="muted">第 {{ index+1 }} 段</span><v-select :model-value="part.type" :items="[{title:'文字',value:'text'},{title:'图片',value:'image'}]" label="片段类型" hide-details @update:model-value="value=>changePart(index,value)" /><div class="actions"><v-btn size="small" variant="text" :disabled="index===0" @click="movePart(index,-1)">上移</v-btn><v-btn size="small" variant="text" :disabled="index===example.segments.length-1" @click="movePart(index,1)">下移</v-btn><v-btn size="small" color="error" variant="text" @click="example.segments.splice(index,1)">移除</v-btn></div></div><v-textarea v-if="part.type==='text'" v-model="part.text" label="要说的话" rows="3" auto-grow required /><template v-else><v-btn variant="outlined" class="my-3" @click="openMedia(index)">{{ part.asset_id?'重新选择运营素材':'选择运营素材' }}</v-btn><div v-if="part.asset_id" class="part-image"><img :src="imageUrl(part.asset_id,example.scene_id)" alt="当前样例图片" /><EntityLink type="media" :id="part.asset_id" :scene-id="example.scene_id||'global-safe'" label="查看素材来源" /></div></template></v-card><v-btn type="submit" color="primary" :loading="busy==='example'" :disabled="!!busy||!example.segments.length||!exampleDirty">{{ editingExample?'保存样例修改':'添加样例' }}</v-btn></v-form></v-card-text></v-card></v-dialog>
    <v-dialog v-model="mediaOpen" max-width="900" scrollable><v-card><v-card-title class="section-header">选择运营素材<v-btn variant="text" @click="mediaOpen=false">关闭</v-btn></v-card-title><v-card-text><p class="muted mb-4">可用范围：{{ mediaScope }}{{ mediaScope!=='global-safe'?' 与公共素材':'' }}</p><v-form class="media-filter" @submit.prevent="mediaSearch=mediaQuery;mediaPage=1;loadMedia()"><v-text-field v-model="mediaQuery" label="描述或标签" hide-details clearable /><v-btn type="submit" color="primary">查询</v-btn></v-form><v-progress-linear v-if="mediaLoading" indeterminate class="my-3" /><v-alert v-if="mediaError" type="error" variant="tonal" class="my-3">{{ mediaError }}</v-alert><div class="media-picker mt-4"><v-card v-for="asset in mediaRows" :key="asset.id" tag="article" variant="outlined"><img :src="imageUrl(asset.id,asset.scope)" :alt="asset.description||'运营素材'" loading="lazy" /><div class="pa-3"><p class="clamp-2 mb-3">{{ asset.description||asset.id }}</p><v-btn size="small" color="primary" variant="tonal" @click="chooseMedia(asset)">选用这张</v-btn></div></v-card></div><p v-if="!mediaLoading&&!mediaError&&!mediaRows.length" class="muted py-6">没有匹配的已启用运营素材</p><v-pagination v-if="mediaTotal>48" :model-value="mediaPage" :length="Math.ceil(mediaTotal/48)" :total-visible="5" @update:model-value="value=>{mediaPage=value;loadMedia()}" /></v-card-text></v-card></v-dialog>
    <v-dialog :model-value="!!preset" max-width="920" scrollable :persistent="!!busy" @update:model-value="value=>!value&&(preset=null)">
      <v-card v-if="preset">
        <v-card-title class="section-header">嘉然模板<v-btn variant="text" :disabled="!!busy" @click="preset=null">关闭</v-btn></v-card-title>
        <v-card-text>
          <p class="mb-4">选择要填入人格草稿的字段，再使用“保存人格”。表达样例按选择逐条新增，各自显示保存结果。</p>
          <v-alert v-if="presetMessage" type="info" variant="tonal" class="mb-4">{{ presetMessage }}</v-alert>
          <h3 class="mb-3">人格字段</h3>
          <div v-for="(value,key) in preset.fields" :key="key" class="preset-field">
            <v-checkbox v-model="selectedPresetFields" :value="key" :label="personaLabels[key]" :disabled="!!busy" hide-details />
            <v-expansion-panels><v-expansion-panel title="查看模板与当前草稿"><v-expansion-panel-text>
              <ResourceViewer title="模板内容" :content="value" />
              <ResourceViewer title="当前草稿" :content="persona[key]" />
            </v-expansion-panel-text></v-expansion-panel></v-expansion-panels>
          </div>
          <v-btn color="primary" variant="tonal" class="mt-4" :disabled="!!busy||!selectedPresetFields.length" @click="fillPresetFields">将所选字段填入草稿</v-btn>
          <v-divider class="my-6" />
          <h3>表达样例</h3>
          <v-alert v-if="preset.missing_media.length" type="warning" variant="tonal" class="mt-4">固定目录缺少素材：{{ preset.missing_media.join('、') }}。补充素材后重新查看模板，可选用对应图文样例。</v-alert>
          <article v-for="(item,index) in preset.examples" :key="item.id" class="example-row">
            <div class="example-main">
              <v-checkbox v-model="selectedPresetExamples" :value="item.id" :label="`新增第 ${index+1} 组样例`" :disabled="!!busy||item.missing_media_refs.length>0||presetExampleResults[item.id]?.status==='saved'" hide-details />
              <p class="example-context mt-3">{{ item.context }}</p>
              <div class="example-body"><template v-for="(part,partIndex) in item.segments" :key="partIndex"><p v-if="part.type==='text'">{{ part.text }}</p><img v-else :src="imageUrl(part.asset_id)" alt="模板样例图片" /></template></div>
              <p v-if="item.missing_media_refs.length" class="muted">{{ item.content }}</p>
              <v-alert v-if="presetExampleResults[item.id]" :type="presetExampleResults[item.id].status==='saved'?'success':presetExampleResults[item.id].status==='error'?'error':'info'" variant="tonal" density="compact">{{ presetExampleResults[item.id].message }}</v-alert>
            </div>
          </article>
          <v-btn color="primary" variant="tonal" class="mt-4" :loading="busy==='preset-examples'" :disabled="!!busy||!selectedPresetExamples.length" @click="savePresetExamples">逐条添加所选样例</v-btn>
        </v-card-text>
        <v-card-actions><v-spacer /><v-btn :disabled="!!busy" @click="preset=null">关闭预览</v-btn></v-card-actions>
      </v-card>
    </v-dialog>
    <v-dialog v-model="resetConfirm" max-width="620" :persistent="busy==='reset'"><v-card title="确认 Reset 全部对话数据"><v-card-text><v-alert type="error" variant="tonal" class="mb-4">此操作会清空全部群聊和私聊的会话数据，无法从页面撤销。</v-alert><p>先停止认知、维护、工作和投递，再删除对话、认识、任务、工作、工具资料、聊天图片和场景上下文。</p><p class="mt-3">保留运行配置、人工表达样例与运营表情库（含来源记录），包括模型、OneBot、人格、登录、Shadow、QQ 白名单和各群设置。</p><v-alert v-if="error" type="error" variant="tonal" class="mt-3">{{ error }}</v-alert></v-card-text><v-card-actions><v-spacer /><v-btn :disabled="!!busy" @click="resetConfirm=false">取消</v-btn><v-btn color="error" :loading="busy==='reset'" :disabled="!!busy" @click="resetData">确认清空对话数据</v-btn></v-card-actions></v-card></v-dialog>
  </div>
</template>
<style scoped>
.budget-table-wrap{overflow-x:auto}.budget-table{width:100%;border-collapse:collapse;text-align:left;font-size:14px}.budget-table caption{text-align:left;font-weight:600;padding:8px 0 12px}.budget-table th,.budget-table td{padding:12px;border-bottom:1px solid var(--line);white-space:nowrap}.budget-table thead{background:rgb(var(--v-theme-surface-variant))}.budget-table tbody th{font-weight:500}.preset-field{margin-bottom:12px}.runtime-json :deep(textarea){font-family:monospace;font-size:13px;line-height:1.6}

.form-card{max-width:1000px;width:100%}.section-header{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}.section-header h2,.form-card>h2{font-size:20px}.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;align-items:start}.wide{grid-column:1/-1}.form-grid>.v-btn{justify-self:start}.actions,.meta,.delivery-state,.saved-scenes{display:flex;gap:8px 12px;flex-wrap:wrap;align-items:center}.meta{font-size:13px;color:#64748b}.example-row{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;padding:24px 0;border-bottom:1px solid #e2e8f0}.example-row:last-child{border:0;padding-bottom:0}.example-main{min-width:0;flex:1}.example-row>.actions{max-width:220px;justify-content:flex-end}.example-context{white-space:pre-wrap;line-height:1.65;color:#64748b;overflow-wrap:anywhere}.example-body{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0;align-items:flex-start}.example-body p{flex-basis:100%;white-space:pre-wrap;line-height:1.8;overflow-wrap:anywhere}.example-body img{max-width:180px;max-height:180px;object-fit:contain}.part-toolbar{display:flex;gap:12px;align-items:center;margin-bottom:16px;flex-wrap:wrap}.part-toolbar>.v-input{flex:1;min-width:140px;max-width:180px}.part-image{display:flex;gap:16px;align-items:center;flex-wrap:wrap}.part-image img{max-width:100%;height:170px;object-fit:contain}.media-filter{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:center}.media-picker{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}.media-picker img{width:100%;height:150px;object-fit:contain;background:#f4f6f9}.media-picker p{overflow-wrap:anywhere;min-height:3em}.danger-zone{max-width:1000px;margin-top:12px}.settings-view p{line-height:1.7}@media(max-width:650px){.form-grid{grid-template-columns:minmax(0,1fr)}.example-row{flex-direction:column}.example-row>.actions{max-width:none;justify-content:flex-start}.section-header{align-items:flex-start}.media-picker{grid-template-columns:repeat(2,minmax(0,1fr))}.part-toolbar>.actions{width:100%}.example-body img{max-width:140px;max-height:140px}}
</style>
