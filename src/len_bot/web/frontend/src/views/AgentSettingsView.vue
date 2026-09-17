<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, fmtTime } from '../api.js'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'
import PageHeader from '../components/PageHeader.vue'
import ScopeSelect from '../components/ScopeSelect.vue'
import EntityLink from '../components/EntityLink.vue'
import ResourceViewer from '../components/ResourceViewer.vue'
const route = useRoute()
const router = useRouter()
const tabs = [{value:'persona',title:'人格与表达'},{value:'attention',title:'参与方式'},{value:'time',title:'睡眠与时间'}]
const tab = computed(() => tabs.some(item=>item.value===route.query.tab) ? route.query.tab : 'persona')
const baselines = ref({})
const saveDraft = (domain,path,values,method) => api(path,{method,body:JSON.stringify({baseline:baselines.value[domain],values})})
const loading = ref(false)
const error = ref('')
const message = ref('')
const readAt = ref({})
const busy = ref('')
const persona = ref(null)
const personaOriginal = ref('')
const attention = ref(null)
const attentionOriginal = ref('')
const timeDraft = ref(null)
const timeOriginal = ref('')
const timeConfigured = ref(false)
const timeLoaded = ref(false)
const timeRestart = ref(false)
const weekdays = [{title:'周一',value:0},{title:'周二',value:1},{title:'周三',value:2},{title:'周四',value:3},{title:'周五',value:4},{title:'周六',value:5},{title:'周日',value:6}]
const exemplars = ref([])
const exampleOpen = ref(false)
const editingExample = ref('')
const example = ref(null)
const exampleOriginal = ref('')
const sourceExample = ref({scene_id:'', event_id:'', context:'', tag:''})
const preset = ref(null)
const presetLoading = ref(false)
const selectedPresetFields = ref([])
const selectedPresetExamples = ref([])
const presetExampleResults = ref({})
const presetMessage = ref('')
const mediaOpen = ref(false)
const mediaRows = ref([])
const mediaTotal = ref(0)
const mediaPage = ref(1)
const mediaQuery = ref('')
const mediaSearch = ref('')
const mediaLoading = ref(false)
const mediaError = ref('')
const mediaPart = ref(0)
const mediaScope = ref('global-safe')
const personaLabels = {identity_name:'机器人名字',identity_persona:'身份背景',identity_core:'性格与相处方式',character_context:'角色资料与梗',conversation_style:'说话方式'}
const personaDirty = computed(()=>!!persona.value&&JSON.stringify(persona.value)!==personaOriginal.value)
const attentionDirty = computed(()=>!!attention.value&&JSON.stringify(attention.value)!==attentionOriginal.value)
const timeDirty = computed(()=>timeDraft.value!==null&&JSON.stringify(timeDraft.value)!==timeOriginal.value)
const exampleDirty = computed(()=>exampleOpen.value&&JSON.stringify(example.value)!==exampleOriginal.value)
const dirty = computed(()=>personaDirty.value||attentionDirty.value||timeDirty.value||exampleDirty.value)
const { confirmLeave } = useUnsavedChanges(dirty)
let requestId = 0
let mediaRequest = 0
let presetRequest = 0
const clone = value => JSON.parse(JSON.stringify(value))
const imageUrl = (assetId,scene='') => `/api/media/${encodeURIComponent(assetId)}/file?scene_id=${encodeURIComponent(scene||'global-safe')}`
async function load() {
  const currentTab = tab.value, request = ++requestId
  loading.value = true; error.value = ''
  try {
    if (currentTab==='persona') {
      const [snapshot, samples] = await Promise.all([api('/api/settings/draft/persona'),api('/api/voice/exemplars')])
      const settings=snapshot.saved
      if (request!==requestId) return
      if (!personaDirty.value) {
        baselines.value.persona=snapshot.baseline
        persona.value = Object.fromEntries(Object.keys(personaLabels).map(key=>[key,settings[key]]))
        persona.value.addressNames = settings.address_names.join('、'); personaOriginal.value = JSON.stringify(persona.value)
      }
      exemplars.value = samples.exemplars
    } else if (currentTab==='attention') {
      const snapshot = await api('/api/settings/draft/attention'); const settings=snapshot.saved; if (request!==requestId) return
      if (!attentionDirty.value) { baselines.value.attention=snapshot.baseline; const {attention_keywords,...rest}=settings; attention.value={...rest,keywords:attention_keywords.join('\n')}; attentionOriginal.value=JSON.stringify(attention.value) }
    } else if (currentTab==='time') {
      const snapshot = await api('/api/settings/draft/time'); const settings=snapshot.saved; if (request!==requestId) return
      timeLoaded.value = true
      timeConfigured.value = settings !== null
      if (!timeDirty.value) { baselines.value.time=snapshot.baseline; timeDraft.value=settings; timeOriginal.value=JSON.stringify(settings) }
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
    const result=await saveDraft('persona','/api/settings/persona',{...fields,address_names:[...new Set(addressNames.split(/[\n,，、]+/).map(item=>item.trim()).filter(Boolean))]},'POST')
    personaOriginal.value=JSON.stringify(persona.value);message.value=result.message;await load()
  }catch(e){error.value=e.message}finally{busy.value=''}
}
async function saveAttention() {
  if(busy.value)return
  busy.value='attention';error.value='';message.value=''
  try {
    const {keywords,...fields}=attention.value
    const result=await saveDraft('attention','/api/settings/attention',{...fields,attention_keywords:[...new Set(keywords.split('\n').map(item=>item.trim()).filter(Boolean))]},'PATCH')
    baselines.value.attention=result.settings;const {attention_keywords,...rest}=result.settings;attention.value={...rest,keywords:attention_keywords.join('\n')};attentionOriginal.value=JSON.stringify(attention.value);message.value=result.message
  }catch(e){error.value=e.message}finally{busy.value=''}
}
function beginTimeConfiguration() {
  timeDraft.value = {timezone:'',week_start:null,afternoon_start:'',afternoon_end:'',sleep_start:null,sleep_end:null}
}
async function saveTime() {
  if (busy.value || !timeDraft.value) return
  busy.value='time'; error.value=''; message.value=''
  try {
    const payload = {...timeDraft.value,
      sleep_start: timeDraft.value.sleep_start || null,
      sleep_end: timeDraft.value.sleep_end || null}
    const result = await saveDraft('time','/api/settings/time',payload,'PUT')
    baselines.value.time=result.settings;timeDraft.value=result.settings; timeOriginal.value=JSON.stringify(result.settings); timeConfigured.value=true; timeRestart.value=result.requires_restart; message.value=result.message
  } catch(e) { error.value=e.message } finally { busy.value='' }
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
watch(tab,load,{immediate:true})
</script>
<template>
  <div class="page-stack settings-view">
    <PageHeader title="Agent 设置" description="各配置节分别保存到根参数文件，刷新保留未保存的草稿。"><v-btn variant="outlined" :loading="loading" @click="load">刷新当前设置</v-btn></PageHeader>
    <v-alert v-if="error" type="error" variant="tonal">{{ error }}<span v-if="readAt[tab]"> · 上次读取 {{ fmtTime(readAt[tab]) }}</span></v-alert><v-alert v-if="message" type="success" variant="tonal" closable @click:close="message=''">{{ message }}</v-alert>
    <v-tabs :model-value="tab" color="primary" show-arrows @update:model-value="value=>router.push({name:'agent-settings',query:{tab:value}})"><v-tab v-for="item in tabs" :key="item.value" :value="item.value">{{ item.title }}</v-tab></v-tabs>
    <v-progress-linear v-if="loading" indeterminate />
    <template v-if="tab==='persona'&&persona">
      <v-card class="pa-5 form-card"><div class="section-header"><div><h2>人格与说话方式</h2><p class="muted mt-2">角色资料用于表达，不能作为群友事实或现实能力的依据。</p></div><v-btn variant="tonal" :loading="presetLoading" :disabled="!!busy" @click="previewPreset">查看嘉然模板</v-btn></div><v-form :disabled="!!busy" class="form-grid mt-5" @submit.prevent="savePersona"><v-text-field v-model="persona.identity_name" label="机器人名字" /><v-text-field v-model="persona.addressNames" label="呼唤昵称" hint="用逗号或顿号分隔；呼唤提供观察机会，是否回应由模型决定。" persistent-hint /><v-textarea v-for="key in ['identity_persona','identity_core','character_context','conversation_style']" :key="key" v-model="persona[key]" :label="personaLabels[key]" :rows="key==='character_context'?6:4" auto-grow class="wide" /><v-btn type="submit" color="primary" :loading="busy==='persona'" :disabled="!!busy||!personaDirty">保存人格并立即生效</v-btn><span v-if="personaDirty" class="muted">有未保存修改</span></v-form></v-card>
      <v-card class="pa-5"><div class="section-header"><div><h2>表达样例</h2><p class="muted mt-2">按保存顺序提供，可使用文字、单图或混排。这些是人工表达示范。</p></div><v-btn color="primary" variant="tonal" :disabled="!!busy" @click="editExample()">添加样例</v-btn></div><v-card variant="tonal" class="pa-4 mb-5"><h3>从真实送达消息创建</h3><p class="muted my-2">只接受已确认真实发送的 Bot 消息；Shadow、草稿和 unknown 回执会被拒绝。</p><div class="form-grid"><v-text-field v-model="sourceExample.scene_id" label="场景 ID" placeholder="group:123" /><v-text-field v-model="sourceExample.event_id" label="MESSAGE_SENT 事件 ID" /><v-text-field v-model="sourceExample.context" label="表达语境" /><v-text-field v-model="sourceExample.tag" label="标签" /><v-btn color="primary" variant="outlined" :loading="busy==='example-source'" :disabled="!!busy||!sourceExample.scene_id.trim()||!sourceExample.event_id.trim()" @click="createFromSentMessage">创建并进入样例列表</v-btn></div></v-card><p v-if="!exemplars.length" class="muted py-6">尚无人工表达样例</p><article v-for="(item,index) in exemplars" :key="item.id" class="example-row"><div class="example-main"><div class="meta mb-3"><v-chip size="small">第 {{ index+1 }} 条</v-chip><v-chip size="small" :color="item.enabled?'success':'default'">{{ item.enabled?'已启用':'已停用' }}</v-chip><span>{{ item.scene_id||'所有场景' }}</span><span v-if="item.tag">{{ item.tag }}</span></div><p class="example-context clamp-2">{{ item.context||'通用表达' }}</p><div class="example-body"><template v-for="(part,partIndex) in item.segments" :key="partIndex"><p v-if="part.type==='text'">{{ part.text }}</p><img v-else :src="imageUrl(part.asset_id,item.scene_id)" alt="运营表达样例" loading="lazy" /></template></div><v-alert v-if="item.available===false" type="warning" variant="tonal" density="compact">{{ item.unavailable_reason }}</v-alert></div><div class="actions"><v-btn variant="outlined" :disabled="!!busy" @click="editExample(item)">编辑</v-btn><v-btn variant="text" :disabled="!!busy" @click="changeExample(item)">{{ item.enabled?'停用':'启用' }}</v-btn><v-btn color="error" variant="text" :disabled="!!busy" @click="changeExample(item,true)">删除</v-btn></div></article></v-card>
    </template>
    <v-card v-if="tab==='attention'&&attention" class="pa-5 form-card"><h2>注意力与旁听</h2><p class="muted my-3">关键词和低频抽样提供观察机会。明确 @、回复 Bot、受托工作和有效等待关系继续提供机会；仅处于关注窗口的普通发言与弱机会回复不自动续期。</p><v-form :disabled="!!busy" class="form-grid" @submit.prevent="saveAttention"><v-textarea v-model="attention.keywords" label="运营关键词（每行一项）" rows="4" class="wide" /><v-text-field v-model.number="attention.attention_sample_window_seconds" type="number" min="1" step="1" label="抽样窗口（秒）" required /><v-text-field v-model.number="attention.attention_sample_probability" type="number" min="0" max="1" step="0.01" label="每窗口抽样概率（0—1）" required /><v-expansion-panels class="wide"><v-expansion-panel title="高级参数"><v-expansion-panel-text><div class="form-grid"><v-text-field v-model.number="attention.attention_keyword_cooldown_seconds" type="number" min="0" step="1" label="关键词观察冷却（秒）" required /><v-text-field v-model.number="attention.attention_focus_seconds" type="number" min="1" step="1" label="有效互动送达后的关注窗口（秒）" required /><v-text-field v-model.number="attention.conversation_recent_tokens" type="number" min="500" step="100" label="近期原话预算（文本 token）" required class="wide" /></div></v-expansion-panel-text></v-expansion-panel></v-expansion-panels><v-btn type="submit" color="primary" :loading="busy==='attention'" :disabled="!!busy||!attentionDirty">保存注意力参数</v-btn></v-form></v-card>

    <v-card v-if="tab==='time'&&timeLoaded" class="pa-5 form-card">
      <h2>业务时间口径</h2>
      <p class="muted my-3">日程与群总结按照这里填写的时区、自然周和下午范围解释日期，不自动选择时区或补全天段。睡眠窗口成对填写；留空表示不启用睡眠。</p>
      <v-alert v-if="!timeConfigured" type="info" variant="tonal" class="mb-4">尚未保存业务时间；需要时间口径的新插件不能启用。</v-alert>
      <v-alert v-if="timeRestart" type="info" variant="tonal" class="mb-4">已保存，需手动重启后用于新查询。</v-alert>
      <v-btn v-if="!timeDraft&&!loading" variant="tonal" color="primary" :disabled="!!busy" @click="beginTimeConfiguration">填写业务时间</v-btn>
      <v-form v-if="timeDraft" :disabled="!!busy" class="form-grid" @submit.prevent="saveTime">
        <v-text-field v-model="timeDraft.timezone" label="IANA 时区" hint="填写业务实际采用的 IANA 时区名称。" persistent-hint required />
        <v-select v-model="timeDraft.week_start" label="自然周第一天" :items="weekdays" required />
        <v-text-field v-model="timeDraft.afternoon_start" label="下午开始" type="time" required />
        <v-text-field v-model="timeDraft.afternoon_end" label="下午结束（不含）" type="time" required />
        <v-text-field v-model="timeDraft.sleep_start" label="睡眠开始（可选）" type="time" clearable hint="与睡眠结束成对；跨日窗口允许开始晚于结束。" persistent-hint />
        <v-text-field v-model="timeDraft.sleep_end" label="睡眠结束（可选）" type="time" clearable hint="到点后各群按叫醒状态决定是否恢复普通发送。" persistent-hint />
        <v-btn type="submit" color="primary" :loading="busy==='time'" :disabled="!!busy||!timeDirty">保存业务时间</v-btn>
      </v-form>
    </v-card>

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
  </div>
</template>
<style scoped>
.preset-field{margin-bottom:12px}

.form-card{max-width:1000px;width:100%}.section-header{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}.section-header h2,.form-card>h2{font-size:20px}.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;align-items:start}.wide{grid-column:1/-1}.form-grid>.v-btn{justify-self:start}.actions,.meta{display:flex;gap:8px 12px;flex-wrap:wrap;align-items:center}.meta{font-size:13px;color:#64748b}.example-row{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;padding:24px 0;border-bottom:1px solid #e2e8f0}.example-row:last-child{border:0;padding-bottom:0}.example-main{min-width:0;flex:1}.example-row>.actions{max-width:220px;justify-content:flex-end}.example-context{white-space:pre-wrap;line-height:1.65;color:#64748b;overflow-wrap:anywhere}.example-body{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0;align-items:flex-start}.example-body p{flex-basis:100%;white-space:pre-wrap;line-height:1.8;overflow-wrap:anywhere}.example-body img{max-width:180px;max-height:180px;object-fit:contain}.part-toolbar{display:flex;gap:12px;align-items:center;margin-bottom:16px;flex-wrap:wrap}.part-toolbar>.v-input{flex:1;min-width:140px;max-width:180px}.part-image{display:flex;gap:16px;align-items:center;flex-wrap:wrap}.part-image img{max-width:100%;height:170px;object-fit:contain}.media-filter{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:center}.media-picker{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}.media-picker img{width:100%;height:150px;object-fit:contain;background:#f4f6f9}.media-picker p{overflow-wrap:anywhere;min-height:3em}
.settings-view p{line-height:1.7}@media(max-width:650px){.form-grid{grid-template-columns:minmax(0,1fr)}.example-row{flex-direction:column}.example-row>.actions{max-width:none;justify-content:flex-start}.section-header{align-items:flex-start}.media-picker{grid-template-columns:repeat(2,minmax(0,1fr))}.part-toolbar>.actions{width:100%}.example-body img{max-width:140px;max-height:140px}}
</style>
