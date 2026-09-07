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
const tabs = [{value:'persona',title:'人格与表达'},{value:'attention',title:'注意力'},{value:'connection',title:'连接'},{value:'delivery',title:'发送'},{value:'account',title:'账户'}]
const tab = computed(() => tabs.some(item=>item.value===route.query.tab) ? route.query.tab : 'persona')
const loading = ref(false), error = ref(''), message = ref(''), readAt = ref({}), busy = ref('')
const persona = ref(null), personaOriginal = ref(''), attention = ref(null), attentionOriginal = ref('')
const onebot = ref(null), connection = ref(null), connectionOriginal = ref(''), shadow = ref(null), groups = ref(''), groupsOriginal = ref('')
const me = ref(null), passwords = ref({current_password:'',new_password:''}), leavingAfterLogout = ref(false)
const exemplars = ref([]), exampleOpen = ref(false), editingExample = ref(''), example = ref(null), exampleOriginal = ref('')
const preset = ref(null), presetLoading = ref(false), resetConfirm = ref(false)
const mediaOpen = ref(false), mediaRows = ref([]), mediaTotal = ref(0), mediaPage = ref(1), mediaQuery = ref(''), mediaSearch = ref(''), mediaLoading = ref(false), mediaError = ref(''), mediaPart = ref(0), mediaScope = ref('global-safe')
const personaLabels = {identity_name:'机器人名字',identity_persona:'身份背景',identity_core:'性格与相处方式',character_context:'角色资料与梗',conversation_style:'说话方式'}
const personaDirty = computed(()=>!!persona.value&&JSON.stringify(persona.value)!==personaOriginal.value)
const attentionDirty = computed(()=>!!attention.value&&JSON.stringify(attention.value)!==attentionOriginal.value)
const connectionDirty = computed(()=>!!connection.value&&JSON.stringify(connection.value)!==connectionOriginal.value)
const groupsDirty = computed(()=>groups.value!==groupsOriginal.value)
const exampleDirty = computed(()=>exampleOpen.value&&JSON.stringify(example.value)!==exampleOriginal.value)
const dirty = computed(()=>!leavingAfterLogout.value&&(personaDirty.value||attentionDirty.value||connectionDirty.value||groupsDirty.value||exampleDirty.value||!!passwords.value.current_password||!!passwords.value.new_password))
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
    } else if (currentTab==='connection') {
      const settings = await api('/api/websocket/status'); if (request!==requestId) return
      onebot.value=settings
      if (!connectionDirty.value) { connection.value={connection_mode:settings.connection_mode,action_transport:settings.action_transport,ws_url:settings.ws_url,http_url:settings.http_url,host:settings.host,port:settings.port,access_token:''}; connectionOriginal.value=JSON.stringify(connection.value) }
    } else if (currentTab==='delivery') {
      const settings=await api('/api/cockpit/shadow'); if(request!==requestId)return
      shadow.value=settings
      if(!groupsDirty.value){groups.value=settings.allowed_scenes.map(scene=>scene.replace(/^group:/,'')).join('\n');groupsOriginal.value=groups.value}
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
async function saveConnection(check=false) {
  if(busy.value)return
  if(!window.confirm(check?'保存当前 OneBot 配置并重新连接，然后检查 HTTP 接口？':'保存当前 OneBot 配置并重新连接？'))return
  busy.value='connection';error.value='';message.value=''
  try {
    const body={...connection.value,access_token:connection.value.access_token.trim()||null}
    const result=await api('/api/websocket/config',{method:'POST',body:JSON.stringify(body)})
    connection.value.access_token='';connectionOriginal.value=JSON.stringify(connection.value);message.value=result.message
    if(check){const checked=await api('/api/websocket/test-http',{method:'POST'});message.value=`连接配置已保存。${checked.message}`}
    await load()
  }catch(e){error.value=e.message}finally{busy.value=''}
}
async function toggleShadow() {
  if(busy.value||!shadow.value)return
  const enabled=!shadow.value.enabled
  if(!window.confirm(enabled?'开启 Shadow？机器人继续观察和思考，但不再真实发送消息。':'关闭 Shadow？机器人将可以向已保存名单内的群真实发送消息。'))return
  busy.value='shadow';error.value='';message.value=''
  try{
    const result=await api('/api/cockpit/shadow/toggle',{method:'POST',body:JSON.stringify({enabled})})
    shadow.value={...shadow.value,enabled:result.shadow_mode};message.value=result.shadow_mode?'已开启 Shadow，仅观察':'已关闭 Shadow，名单内允许实发'
  }catch(e){error.value=e.message}finally{busy.value=''}
}
async function saveGroups() {
  if(busy.value)return
  const values=[...new Set(groups.value.split(/[,，\s]+/).filter(Boolean))]
  if(values.some(group=>!/^[1-9]\d*$/.test(group))){error.value='请填写有效 QQ 群号，多个群号用逗号或换行分隔';return}
  if(!window.confirm(values.length?`保存 ${values.length} 个允许实发的群？Shadow 关闭时，这些群可以收到真实消息。`:'保存空名单？所有群将仅观察。'))return
  busy.value='groups';error.value='';message.value=''
  try{
    const result=await api('/api/cockpit/shadow/scenes',{method:'POST',body:JSON.stringify({scene_ids:values.map(group=>'group:'+group)})})
    shadow.value={...shadow.value,allowed_scenes:result.allowed_scenes};groups.value=result.allowed_scenes.map(scene=>scene.replace(/^group:/,'')).join('\n');groupsOriginal.value=groups.value;message.value='允许实发的群名单已保存'
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
function openMedia(index){mediaPart.value=index;mediaScope.value=example.value.scene_id||'global-safe';mediaQuery.value='';mediaSearch.value='';mediaPage.value=1;mediaOpen.value=true;loadMedia()}
async function loadMedia(){
  const request=++mediaRequest;mediaLoading.value=true;mediaError.value=''
  try{const result=await api('/api/media?'+new URLSearchParams({scene_id:mediaScope.value,query:mediaSearch.value,curated:'true',enabled:'true',page:String(mediaPage.value),page_size:'48'}));if(request!==mediaRequest)return;mediaRows.value=result.items;mediaTotal.value=result.total}
  catch(e){if(request===mediaRequest)mediaError.value=e.message}finally{if(request===mediaRequest)mediaLoading.value=false}
}
function chooseMedia(asset){example.value.segments[mediaPart.value].asset_id=asset.id;mediaOpen.value=false}
async function previewPreset(){const request=++presetRequest;presetLoading.value=true;error.value='';try{const result=await api('/api/settings/persona/diana');if(request===presetRequest)preset.value=result}catch(e){if(request===presetRequest)error.value=e.message}finally{if(request===presetRequest)presetLoading.value=false}}
async function applyPreset(){
  if(busy.value||!preset.value||personaDirty.value||exampleDirty.value)return
  busy.value='preset';error.value='';message.value=''
  try{const result=await api('/api/settings/persona/diana',{method:'POST',body:JSON.stringify({preview_token:preset.value.preview_token})});preset.value=null;message.value=result.message;await load()}
  catch(e){error.value=e.message}finally{busy.value=''}
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
    <PageHeader title="系统设置" description="人格、注意力、连接与发送分别保存，刷新不会覆盖未保存的草稿。"><v-btn variant="outlined" :loading="loading" @click="load">刷新当前设置</v-btn></PageHeader>
    <v-alert v-if="error" type="error" variant="tonal">{{ error }}<span v-if="readAt[tab]"> · 上次读取 {{ fmtTime(readAt[tab]) }}</span></v-alert><v-alert v-if="message" type="success" variant="tonal" closable @click:close="message=''">{{ message }}</v-alert>
    <v-tabs :model-value="tab" color="primary" show-arrows @update:model-value="value=>router.push({name:'settings',query:{tab:value}})"><v-tab v-for="item in tabs" :key="item.value" :value="item.value">{{ item.title }}</v-tab></v-tabs>
    <v-progress-linear v-if="loading" indeterminate />
    <template v-if="tab==='persona'&&persona">
      <v-card class="pa-5 form-card"><div class="section-header"><div><h2>人格与说话方式</h2><p class="muted mt-2">角色资料用于表达，不能作为群友事实或现实能力的依据。</p></div><v-btn variant="tonal" :loading="presetLoading" :disabled="!!busy" @click="previewPreset">预览嘉然预设</v-btn></div><v-form :disabled="!!busy" class="form-grid mt-5" @submit.prevent="savePersona"><v-text-field v-model="persona.identity_name" label="机器人名字" /><v-text-field v-model="persona.addressNames" label="呼唤昵称" hint="用逗号或顿号分隔；呼唤提供观察机会，是否回应由模型决定。" persistent-hint /><v-textarea v-for="key in ['identity_persona','identity_core','character_context','conversation_style']" :key="key" v-model="persona[key]" :label="personaLabels[key]" :rows="key==='character_context'?6:4" auto-grow class="wide" /><v-btn type="submit" color="primary" :loading="busy==='persona'" :disabled="!!busy||!personaDirty">保存人格并立即生效</v-btn><span v-if="personaDirty" class="muted">有未保存修改</span></v-form></v-card>
      <v-card class="pa-5"><div class="section-header"><div><h2>表达样例</h2><p class="muted mt-2">按保存顺序提供，可使用文字、单图或混排。这些是人工表达示范。</p></div><v-btn color="primary" variant="tonal" :disabled="!!busy" @click="editExample()">添加样例</v-btn></div><p v-if="!exemplars.length" class="muted py-6">尚无人工表达样例</p><article v-for="(item,index) in exemplars" :key="item.id" class="example-row"><div class="example-main"><div class="meta mb-3"><v-chip size="small">第 {{ index+1 }} 条</v-chip><v-chip size="small" :color="item.enabled?'success':'default'">{{ item.enabled?'已启用':'已停用' }}</v-chip><span>{{ item.scene_id||'所有场景' }}</span><span v-if="item.tag">{{ item.tag }}</span></div><p class="example-context clamp-2">{{ item.context||'通用表达' }}</p><div class="example-body"><template v-for="(part,partIndex) in item.segments" :key="partIndex"><p v-if="part.type==='text'">{{ part.text }}</p><img v-else :src="imageUrl(part.asset_id,item.scene_id)" alt="运营表达样例" loading="lazy" /></template></div><v-alert v-if="item.available===false" type="warning" variant="tonal" density="compact">{{ item.unavailable_reason }}</v-alert></div><div class="actions"><v-btn variant="outlined" :disabled="!!busy" @click="editExample(item)">编辑</v-btn><v-btn variant="text" :disabled="!!busy" @click="changeExample(item)">{{ item.enabled?'停用':'启用' }}</v-btn><v-btn color="error" variant="text" :disabled="!!busy" @click="changeExample(item,true)">删除</v-btn></div></article></v-card>
    </template>
    <v-card v-if="tab==='attention'&&attention" class="pa-5 form-card"><h2>注意力与旁听</h2><p class="muted my-3">关键词和低频抽样提供观察机会。明确呼唤、连续互动和有效工作事件独立唤醒。</p><v-form :disabled="!!busy" class="form-grid" @submit.prevent="saveAttention"><v-textarea v-model="attention.keywords" label="运营关键词（每行一项）" rows="4" class="wide" /><v-text-field v-model.number="attention.attention_sample_window_seconds" type="number" min="1" step="1" label="抽样窗口（秒）" required /><v-text-field v-model.number="attention.attention_sample_probability" type="number" min="0" max="1" step="0.01" label="每窗口抽样概率（0—1）" required /><v-expansion-panels class="wide"><v-expansion-panel title="高级参数"><v-expansion-panel-text><div class="form-grid"><v-text-field v-model.number="attention.attention_keyword_cooldown_seconds" type="number" min="0" step="1" label="关键词观察冷却（秒）" required /><v-text-field v-model.number="attention.attention_focus_seconds" type="number" min="1" step="1" label="送达后连续关注（秒）" required /><v-text-field v-model.number="attention.conversation_recent_tokens" type="number" min="500" step="100" label="近期原话预算（文本 token）" required class="wide" /></div></v-expansion-panel-text></v-expansion-panel></v-expansion-panels><v-btn type="submit" color="primary" :loading="busy==='attention'" :disabled="!!busy||!attentionDirty">保存注意力参数</v-btn></v-form></v-card>
    <v-card v-if="tab==='connection'&&onebot&&connection" class="pa-5 form-card"><div class="section-header"><h2>连接 OneBot</h2><v-chip :color="onebot.connected?'success':'warning'">{{ onebot.connected?'已连接':'未连接' }}</v-chip></div><p class="muted my-3">{{ onebot.connected?'已取得 OneBot 连接。':onebot.connection_mode==='forward_ws'?'当前未连接，请查看端点与最近错误。':'等待 OneBot 主动接入。' }}<span v-if="onebot.self_id"> 已识别账号：{{ onebot.self_id }}</span></p><v-alert v-if="onebot.last_error" type="error" variant="tonal" class="mb-4">{{ onebot.last_error }}</v-alert><v-form :disabled="!!busy" class="form-grid" @submit.prevent="saveConnection()"><v-select v-model="connection.connection_mode" label="消息连接方式" :items="[{title:'主动连接 OneBot',value:'forward_ws'},{title:'等待 OneBot 连接',value:'reverse_ws'}]" class="wide" /><v-text-field v-if="connection.connection_mode==='forward_ws'" v-model="connection.ws_url" label="WebSocket 端点" placeholder="ws://127.0.0.1:13001/" class="wide" required /><template v-else><v-text-field v-model="connection.host" label="监听地址" required /><v-text-field v-model.number="connection.port" type="number" min="1" max="65535" label="监听端口" required /></template><v-select v-model="connection.action_transport" label="发送传输" :items="[{title:'使用 WebSocket',value:'websocket'},{title:'使用 HTTP',value:'http'}]" /><v-text-field v-model="connection.http_url" label="HTTP 接口地址" :required="connection.action_transport==='http'" /><v-text-field v-model="connection.access_token" type="password" autocomplete="new-password" label="访问令牌" :placeholder="onebot.access_token_set?'已保存，留空保留':'填写 OneBot 访问令牌'" class="wide" /><div class="actions wide"><v-btn type="submit" color="primary" :loading="busy==='connection'" :disabled="!!busy">保存并重新连接</v-btn><v-btn variant="outlined" :disabled="!!busy" @click="saveConnection(true)">保存并检查 HTTP</v-btn></div><p class="muted wide">两个操作都会先保存当前连接配置并重新连接。“检查 HTTP”随后请求 OneBot 状态，不发送群消息。</p></v-form></v-card>
    <v-card v-if="tab==='delivery'&&shadow" class="pa-5 form-card"><h2>发送控制</h2><div class="delivery-state my-4"><v-chip :color="shadow.enabled?'warning':'primary'">{{ shadow.enabled?'Shadow · 仅观察':'名单内允许实发' }}</v-chip><v-btn :color="shadow.enabled?'warning':'primary'" variant="outlined" :loading="busy==='shadow'" :disabled="!!busy" @click="toggleShadow">{{ shadow.enabled?'关闭 Shadow':'开启 Shadow' }}</v-btn></div><p class="muted mb-5">Shadow 开启时继续观察与思考，不真实发送。关闭后，仅允许向已保存群名单发送；提交返回前保持显示原保存状态。</p><h3>当前已保存名单</h3><div class="saved-scenes my-3"><EntityLink v-for="sceneId in shadow.allowed_scenes" :key="sceneId" type="scene" :id="sceneId" /><span v-if="!shadow.allowed_scenes.length" class="muted">空名单，所有群仅观察</span></div><v-divider class="my-5" /><v-form :disabled="!!busy" class="form-grid" @submit.prevent="saveGroups"><v-textarea v-model="groups" label="允许实发的 QQ 群号" rows="4" hint="多个群号用逗号或换行分隔。留空表示所有群仅观察。" persistent-hint class="wide" /><v-btn type="submit" color="primary" :loading="busy==='groups'" :disabled="!!busy||!groupsDirty">保存群名单</v-btn></v-form></v-card>
    <v-card v-if="tab==='account'&&me" class="pa-5 form-card"><div class="section-header"><h2>登录账户</h2><v-chip :color="me.is_default_password?'warning':'default'">{{ me.is_default_password?'仍使用初始密码':'已修改初始密码' }}</v-chip></div><p class="my-4">{{ me.username }} · 上次登录 {{ fmtTime(me.last_login_at) }}</p><v-form :disabled="!!busy" class="form-grid" @submit.prevent="changePassword"><v-text-field v-model="passwords.current_password" type="password" autocomplete="current-password" label="当前密码" required /><v-text-field v-model="passwords.new_password" type="password" autocomplete="new-password" label="新密码（至少 6 位）" minlength="6" required /><div class="actions wide"><v-btn type="submit" color="primary" :loading="busy==='password'" :disabled="!!busy||!passwords.current_password||passwords.new_password.length<6">更新密码</v-btn><v-btn variant="outlined" :disabled="!!busy" @click="signOut">退出登录</v-btn></div></v-form></v-card>
    <v-expansion-panels class="danger-zone"><v-expansion-panel title="危险操作：重置全部对话数据"><v-expansion-panel-text><p class="mb-4">删除全部群聊和私聊的对话、认识、任务与工作、工具资料、聊天图片和场景上下文。保留运行配置、人工表达样例和运营表情库（含来源）。</p><v-btn color="error" variant="outlined" :disabled="!!busy" @click="resetConfirm=true">Reset 对话数据</v-btn></v-expansion-panel-text></v-expansion-panel></v-expansion-panels>

    <v-dialog :model-value="exampleOpen" max-width="880" scrollable :persistent="!!busy" @update:model-value="value=>!value&&closeExample()"><v-card><v-card-title class="section-header">{{ editingExample?'编辑表达样例':'添加表达样例' }}<v-btn variant="text" :disabled="!!busy" @click="closeExample">关闭</v-btn></v-card-title><v-card-text><v-alert v-if="error" type="error" variant="tonal" class="mb-4">{{ error }}</v-alert><v-form v-if="example" :disabled="!!busy" @submit.prevent="saveExample"><div class="form-grid"><v-textarea v-model="example.context" label="前文与语境" maxlength="8000" rows="3" class="wide" /><ScopeSelect v-model="example.scene_id" clearable /><v-text-field v-model="example.tag" label="样例标签" maxlength="200" /></div><p class="muted mb-4">范围留空适用于所有场景，只能使用公共运营素材。本群样例也可使用本群运营素材。</p><div class="section-header"><h3>表达片段</h3><div class="actions"><v-btn size="small" variant="tonal" :disabled="example.segments.length>=20" @click="addPart('text')">添加文字</v-btn><v-btn size="small" variant="tonal" :disabled="example.segments.length>=20" @click="addPart('image')">添加图片</v-btn></div></div><v-card v-for="(part,index) in example.segments" :key="index" variant="outlined" class="pa-4 my-3"><div class="part-toolbar"><span class="muted">第 {{ index+1 }} 段</span><v-select :model-value="part.type" :items="[{title:'文字',value:'text'},{title:'图片',value:'image'}]" label="片段类型" hide-details @update:model-value="value=>changePart(index,value)" /><div class="actions"><v-btn size="small" variant="text" :disabled="index===0" @click="movePart(index,-1)">上移</v-btn><v-btn size="small" variant="text" :disabled="index===example.segments.length-1" @click="movePart(index,1)">下移</v-btn><v-btn size="small" color="error" variant="text" @click="example.segments.splice(index,1)">移除</v-btn></div></div><v-textarea v-if="part.type==='text'" v-model="part.text" label="要说的话" rows="3" auto-grow required /><template v-else><v-btn variant="outlined" class="my-3" @click="openMedia(index)">{{ part.asset_id?'重新选择运营素材':'选择运营素材' }}</v-btn><div v-if="part.asset_id" class="part-image"><img :src="imageUrl(part.asset_id,example.scene_id)" alt="当前样例图片" /><EntityLink type="media" :id="part.asset_id" :scene-id="example.scene_id||'global-safe'" label="查看素材来源" /></div></template></v-card><v-btn type="submit" color="primary" :loading="busy==='example'" :disabled="!!busy||!example.segments.length||!exampleDirty">{{ editingExample?'保存样例修改':'添加样例' }}</v-btn></v-form></v-card-text></v-card></v-dialog>
    <v-dialog v-model="mediaOpen" max-width="900" scrollable><v-card><v-card-title class="section-header">选择运营素材<v-btn variant="text" @click="mediaOpen=false">关闭</v-btn></v-card-title><v-card-text><p class="muted mb-4">可用范围：{{ mediaScope }}{{ mediaScope!=='global-safe'?' 与公共素材':'' }}</p><v-form class="media-filter" @submit.prevent="mediaSearch=mediaQuery;mediaPage=1;loadMedia()"><v-text-field v-model="mediaQuery" label="描述或标签" hide-details clearable /><v-btn type="submit" color="primary">查询</v-btn></v-form><v-progress-linear v-if="mediaLoading" indeterminate class="my-3" /><v-alert v-if="mediaError" type="error" variant="tonal" class="my-3">{{ mediaError }}</v-alert><div class="media-picker mt-4"><v-card v-for="asset in mediaRows" :key="asset.id" tag="article" variant="outlined"><img :src="imageUrl(asset.id,asset.scope)" :alt="asset.description||'运营素材'" loading="lazy" /><div class="pa-3"><p class="clamp-2 mb-3">{{ asset.description||asset.id }}</p><v-btn size="small" color="primary" variant="tonal" @click="chooseMedia(asset)">选用这张</v-btn></div></v-card></div><p v-if="!mediaLoading&&!mediaError&&!mediaRows.length" class="muted py-6">没有匹配的已启用运营素材</p><v-pagination v-if="mediaTotal>48" :model-value="mediaPage" :length="Math.ceil(mediaTotal/48)" :total-visible="5" @update:model-value="value=>{mediaPage=value;loadMedia()}" /></v-card-text></v-card></v-dialog>
    <v-dialog :model-value="!!preset" max-width="920" scrollable :persistent="busy==='preset'" @update:model-value="value=>!value&&(preset=null)"><v-card v-if="preset"><v-card-title class="section-header">嘉然预设预览<v-btn variant="text" :disabled="!!busy" @click="preset=null">关闭</v-btn></v-card-title><v-card-text><p class="mb-4">{{ preset.applied?'已应用过，后续人工编辑保留。':`将停用 ${preset.disable_example_ids.length} 条未修改的旧预设样例，添加 ${preset.example_count} 组新样例。已保存的人工修改保留。` }}</p><v-alert v-if="personaDirty||exampleDirty" type="warning" variant="tonal" class="mb-4">请先保存或取消未保存的人格/样例修改，再应用预设。</v-alert><v-alert v-if="error" type="error" variant="tonal" class="mb-4">{{ error }}</v-alert><v-expansion-panels multiple><v-expansion-panel v-for="field in preset.fields" :key="field.key" :title="`${personaLabels[field.key]} · ${field.action==='update'?'更新':field.action==='preserve'?'保留人工修改':'保持不变'}`"><v-expansion-panel-text><ResourceViewer title="原保存内容" :content="field.current||'未设置'" /><ResourceViewer title="应用后内容" :content="field.next" /></v-expansion-panel-text></v-expansion-panel><v-expansion-panel title="预设表达样例"><v-expansion-panel-text><article v-for="item in preset.examples" :key="item.id" class="example-row"><div class="example-main"><p class="example-context">{{ item.context }}</p><div class="example-body"><template v-for="(part,index) in item.segments" :key="index"><p v-if="part.type==='text'">{{ part.text }}</p><img v-else :src="imageUrl(part.asset_id)" alt="预设样例图片" /></template></div><p v-if="item.missing_media_refs?.length" class="muted">{{ item.content }}</p></div></article></v-expansion-panel-text></v-expansion-panel></v-expansion-panels><v-alert v-if="preset.missing_media?.length" type="warning" variant="tonal" class="mt-4">固定目录缺少素材：{{ preset.missing_media.join('、') }}。补充素材后重新预览。</v-alert></v-card-text><v-card-actions><v-spacer /><v-btn :disabled="!!busy" @click="preset=null">取消</v-btn><v-btn v-if="!preset.applied" color="primary" :loading="busy==='preset'" :disabled="!!busy||personaDirty||exampleDirty||!!preset.missing_media?.length" @click="applyPreset">确认应用预设</v-btn></v-card-actions></v-card></v-dialog>
    <v-dialog v-model="resetConfirm" max-width="620" :persistent="busy==='reset'"><v-card title="确认 Reset 全部对话数据"><v-card-text><v-alert type="error" variant="tonal" class="mb-4">此操作会清空全部群聊和私聊的会话数据，无法从页面撤销。</v-alert><p>先停止旧认知、反思、工作和投递，再删除对话、认识、任务、工作、工具资料、聊天图片和场景上下文。</p><p class="mt-3">保留运行配置、人工表达样例与运营表情库（含来源记录），包括模型、OneBot、人格、登录、Shadow 和实发群名单。</p><v-alert v-if="error" type="error" variant="tonal" class="mt-3">{{ error }}</v-alert></v-card-text><v-card-actions><v-spacer /><v-btn :disabled="!!busy" @click="resetConfirm=false">取消</v-btn><v-btn color="error" :loading="busy==='reset'" :disabled="!!busy" @click="resetData">确认清空对话数据</v-btn></v-card-actions></v-card></v-dialog>
  </div>
</template>
<style scoped>
.form-card{max-width:1000px;width:100%}.section-header{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}.section-header h2,.form-card>h2{font-size:20px}.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;align-items:start}.wide{grid-column:1/-1}.form-grid>.v-btn{justify-self:start}.actions,.meta,.delivery-state,.saved-scenes{display:flex;gap:8px 12px;flex-wrap:wrap;align-items:center}.meta{font-size:13px;color:#64748b}.example-row{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;padding:24px 0;border-bottom:1px solid #e2e8f0}.example-row:last-child{border:0;padding-bottom:0}.example-main{min-width:0;flex:1}.example-row>.actions{max-width:220px;justify-content:flex-end}.example-context{white-space:pre-wrap;line-height:1.65;color:#64748b;overflow-wrap:anywhere}.example-body{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0;align-items:flex-start}.example-body p{flex-basis:100%;white-space:pre-wrap;line-height:1.8;overflow-wrap:anywhere}.example-body img{max-width:180px;max-height:180px;object-fit:contain}.part-toolbar{display:flex;gap:12px;align-items:center;margin-bottom:16px;flex-wrap:wrap}.part-toolbar>.v-input{flex:1;min-width:140px;max-width:180px}.part-image{display:flex;gap:16px;align-items:center;flex-wrap:wrap}.part-image img{max-width:100%;height:170px;object-fit:contain}.media-filter{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:center}.media-picker{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}.media-picker img{width:100%;height:150px;object-fit:contain;background:#f4f6f9}.media-picker p{overflow-wrap:anywhere;min-height:3em}.danger-zone{max-width:1000px;margin-top:12px}.settings-view p{line-height:1.7}@media(max-width:650px){.form-grid{grid-template-columns:minmax(0,1fr)}.example-row{flex-direction:column}.example-row>.actions{max-width:none;justify-content:flex-start}.section-header{align-items:flex-start}.media-picker{grid-template-columns:repeat(2,minmax(0,1fr))}.part-toolbar>.actions{width:100%}.example-body img{max-width:140px;max-height:140px}}
</style>
