<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter, onBeforeRouteUpdate } from 'vue-router'
import { api, fmtTime } from '../api.js'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'
import PageHeader from '../components/PageHeader.vue'
import StatusBadge from '../components/StatusBadge.vue'
import ScopeSelect from '../components/ScopeSelect.vue'
import ResourceViewer from '../components/ResourceViewer.vue'

const route=useRoute(),router=useRouter()
const plugins=ref([]),loading=ref(false),loaded=ref(false),readAt=ref(null),error=ref(''),message=ref(''),busy=ref('')
const selected=ref(null),draft=ref(null),original=ref('')
const dirty=computed(()=>!!draft.value&&JSON.stringify(draft.value)!==original.value)
const {confirmLeave}=useUnsavedChanges(dirty)
onBeforeRouteUpdate((to,from)=>to.query.id===from.query.id||confirmLeave())
let requestId=0
const permissionLabels={emit_event:'提交观察事件',register_tool:'提供只读查询工具',intercept_action:'检查待发行动'}
const eventLabels={LIVE_STARTED:'发现直播开始',LIVE_ENDED:'发现直播结束'}
const toolLabels={web_search:'搜索网页',read_page:'读取网页',get_video_info:'查询视频信息',search_bilibili:'搜索哔哩哔哩',get_dynamic_feed:'查询用户动态'}
const labels=(values,dictionary)=>values.map(value=>dictionary[value]||value).join('、')
const fields=computed(()=>selected.value?Object.entries(selected.value.config_schema.properties||{}).map(([key,schema])=>({key,schema})):[])
function setDraft(plugin){
  const values={...plugin.default_config,...plugin.config}
  for(const [key,schema] of Object.entries(plugin.config_schema.properties||{})){
    if(plugin.secret_fields.includes(key))values[key]=''
    else if(schema.type==='array')values[key]=(values[key]||[]).join(', ')
  }
  draft.value=values;original.value=JSON.stringify(values)
}
function selectFromRoute(){
  const plugin=plugins.value.find(item=>item.id===route.query.id)||null
  if(!plugin){selected.value=null;draft.value=null;original.value='';return}
  const preserve=selected.value?.id===plugin.id&&dirty.value
  selected.value=plugin;if(!preserve)setDraft(plugin)
}
async function load(){
  const request=++requestId;loading.value=true;error.value=''
  try{const result=await api('/api/plugins/list');if(request!==requestId)return;plugins.value=result;loaded.value=true;readAt.value=Date.now()/1000;selectFromRoute()}
  catch(e){if(request===requestId)error.value=e.message}finally{if(request===requestId)loading.value=false}
}
function close(){router.push({name:'plugins'})}
async function toggle(plugin){
  if(busy.value||!window.confirm(`${plugin.enabled?'停用':'启用'}「${plugin.name}」？这将改变后续可用的感知或查询能力。`))return
  busy.value=`toggle:${plugin.id}`;error.value='';message.value=''
  try{await api('/api/plugins/toggle',{method:'POST',body:JSON.stringify({plugin_id:plugin.id,enabled:!plugin.enabled})});message.value='插件状态已保存';await load()}
  catch(e){error.value=e.message}finally{busy.value=''}
}
async function save(){
  if(busy.value||!selected.value)return
  if(!window.confirm(`保存「${selected.value.name}」的配置？留空的凭据保留已保存值。`))return
  busy.value='config';error.value='';message.value=''
  try{
    const config={...draft.value}
    for(const {key,schema} of fields.value){
      if(selected.value.secret_fields.includes(key)){if(!config[key].trim())delete config[key]}
      else if(schema.type==='array')config[key]=config[key].split(/[,\s]+/).filter(Boolean).map(value=>schema.items?.type==='integer'?Number(value):value)
    }
    await api('/api/plugins/config',{method:'POST',body:JSON.stringify({plugin_id:selected.value.id,config})})
    original.value=JSON.stringify(draft.value);message.value=`「${selected.value.name}」的配置已保存`;await load()
  }catch(e){error.value=e.message}finally{busy.value=''}
}
watch(()=>route.query.id,selectFromRoute)
load()
</script>
<template>
  <div class="page-stack">
    <PageHeader title="扩展能力" description="查看已经装载的感知与查询插件。插件提供工具和事实，不直接决定发言。"><v-btn variant="outlined" :loading="loading" @click="load">刷新</v-btn></PageHeader>
    <v-alert v-if="error" type="error" variant="tonal">{{ error }}<span v-if="readAt"> · 上次读取 {{ fmtTime(readAt) }}</span></v-alert><v-alert v-if="message" type="success" variant="tonal" closable @click:close="message=''">{{ message }}</v-alert>
    <v-progress-linear v-if="loading" indeterminate />
    <div class="plugin-list"><v-card v-for="plugin in plugins" :key="plugin.id" class="pa-5"><div class="plugin-heading"><div class="plugin-title"><p class="muted mb-2">{{ ({sensory:'信息监测',tool:'查询工具',scheduled:'计划能力',interceptor:'行动检查',hybrid:'组合能力'})[plugin.plugin_type]||plugin.plugin_type }}</p><h2>{{ plugin.name }}</h2></div><StatusBadge domain="plugin" :status="plugin.state" /></div><p class="clamp-2 plugin-description">{{ plugin.description }}</p><div class="plugin-meta"><span>v{{ plugin.version }}</span><span>错误 {{ plugin.error_count }} 次</span><span v-if="plugin.last_run_at">最近使用 {{ fmtTime(plugin.last_run_at) }}</span></div><div class="actions"><v-btn color="primary" variant="tonal" :to="{name:'plugins',query:{id:plugin.id}}">详情与配置</v-btn><v-btn :color="plugin.enabled?'error':'primary'" variant="outlined" :disabled="!!busy" :loading="busy===`toggle:${plugin.id}`" @click="toggle(plugin)">{{ plugin.enabled?'停用':'启用' }}</v-btn></div></v-card></div>
    <v-card v-if="loaded&&!error&&!plugins.length" class="pa-8 text-center muted">当前没有已装载插件</v-card>
    <v-dialog :model-value="!!route.query.id" max-width="860" scrollable :persistent="!!busy" @update:model-value="value=>!value&&close()"><v-card><v-card-title class="dialog-title">扩展能力详情<v-btn variant="text" :disabled="!!busy" @click="close">关闭</v-btn></v-card-title><v-card-text><v-progress-linear v-if="loading" indeterminate /><v-alert v-if="error" type="error" variant="tonal" class="mb-4">{{ error }}</v-alert><v-alert v-if="loaded&&!selected&&!error" type="warning" variant="tonal">此插件不存在或已经卸载。</v-alert><template v-if="selected"><div class="plugin-heading"><h2>{{ selected.name }}</h2><StatusBadge domain="plugin" :status="selected.state" /></div><p class="entity-id my-3">{{ selected.id }} · v{{ selected.version }}</p><p class="full-text mb-5">{{ selected.description }}</p><dl class="facts"><dt>已获权限</dt><dd>{{ labels(selected.permissions,permissionLabels)||'无特殊权限' }}</dd><dt>感知事件</dt><dd>{{ labels(selected.emitted_events,eventLabels)||'无' }}</dd><dt>查询工具</dt><dd>{{ labels(selected.registered_tools,toolLabels)||'无' }}</dd><dt>最近发现</dt><dd>{{ fmtTime(selected.last_event_at) }}</dd><dt>最近使用</dt><dd>{{ fmtTime(selected.last_run_at) }}</dd><dt>错误次数</dt><dd>{{ selected.error_count }}</dd></dl><v-alert v-if="selected.last_error" type="error" variant="tonal" class="my-4">{{ selected.last_error }}</v-alert><v-divider class="my-5" /><v-form v-if="fields.length&&draft" :disabled="!!busy" @submit.prevent="save"><h3 class="mb-4">插件配置</h3><div class="config-grid"><template v-for="field in fields" :key="field.key"><v-text-field v-if="selected.secret_fields.includes(field.key)" v-model="draft[field.key]" :label="field.schema.title||field.key" type="password" autocomplete="new-password" :placeholder="selected.config_set[field.key]?'已保存，留空保留':'尚未配置，留空保持'" :hint="field.schema.description" /><ScopeSelect v-else-if="field.key==='scene_id'" v-model="draft[field.key]" :label="field.schema.title||'投递场景'" clearable /><v-select v-else-if="field.schema.enum" v-model="draft[field.key]" :items="field.schema.enum" :label="field.schema.title||field.key" /><v-switch v-else-if="field.schema.type==='boolean'" v-model="draft[field.key]" :label="field.schema.title||field.key" color="primary" /><v-text-field v-else-if="['number','integer'].includes(field.schema.type)" v-model.number="draft[field.key]" :label="field.schema.title||field.key" type="number" :min="field.schema.minimum" :max="field.schema.maximum" :step="field.schema.type==='integer'?1:'any'" :hint="field.schema.description" /><v-text-field v-else v-model="draft[field.key]" :label="field.schema.title||field.key" :hint="field.schema.type==='array'?'多个值用逗号分隔':field.schema.description" /></template></div><p v-if="selected.secret_fields.length" class="muted mb-4">凭据仅显示是否已保存。密码框留空时不提交该字段，保留原值。</p><v-btn type="submit" color="primary" :loading="busy==='config'" :disabled="!!busy||!dirty">保存插件配置</v-btn></v-form><p v-else class="muted">此插件没有可配置参数。</p><v-expansion-panels class="mt-5"><v-expansion-panel title="诊断信息"><v-expansion-panel-text><ResourceViewer title="已声明能力与配置结构" :content="{id:selected.id,version:selected.version,permissions:selected.permissions,emitted_events:selected.emitted_events,registered_tools:selected.registered_tools,config_schema:selected.config_schema}" /></v-expansion-panel-text></v-expansion-panel></v-expansion-panels></template></v-card-text></v-card></v-dialog>
  </div>
</template>
<style scoped>
.plugin-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.plugin-heading,.dialog-title{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}.plugin-title{min-width:0}.plugin-title h2,.plugin-heading h2{font-size:19px;overflow-wrap:anywhere}.plugin-description{margin:16px 0;line-height:1.7;min-height:3.4em}.plugin-meta,.actions{display:flex;gap:10px 16px;flex-wrap:wrap;align-items:center}.plugin-meta{font-size:13px;color:#64748b;margin-bottom:18px}.full-text{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.7}.facts{display:grid;grid-template-columns:90px minmax(0,1fr);gap:12px;line-height:1.7}.facts dt{color:#64748b}.facts dd{margin:0;overflow-wrap:anywhere}.config-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}@media(max-width:850px){.plugin-list{grid-template-columns:minmax(0,1fr)}}@media(max-width:550px){.config-grid{grid-template-columns:minmax(0,1fr)}.plugin-description{min-height:0}}
</style>
