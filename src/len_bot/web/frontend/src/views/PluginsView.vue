<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute, useRouter, onBeforeRouteUpdate } from 'vue-router'
import { api, fmtTime } from '../api.js'
import { loadScopes, useAppState } from '../composables/useAppState.js'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'
import PageHeader from '../components/PageHeader.vue'
import StatusBadge from '../components/StatusBadge.vue'
import ResourceViewer from '../components/ResourceViewer.vue'
import PluginConfigFields from '../components/PluginConfigFields.vue'
import {blankConfigDraft,configDraft,draftProblems,configValue} from '../lib/pluginConfig.js'

const route=useRoute(), router=useRouter()
const appState=useAppState()
// The scene list is already loaded for the group pages; a scene name is a
// fact about the group, so it is read from there instead of kept in a second
// place or inferred from the id.
const sceneName=id=>appState.scenes.find(item=>item.scene_id===id)?.display_name||''
const plugins=ref([]), loading=ref(false), loaded=ref(false), readAt=ref(null), error=ref(''), message=ref(''), busy=ref('')
const selected=ref(null), draft=ref(null), original=ref('null')
const search=ref(''), filter=ref('all')
const detailTab=ref('config')
const dirty=computed(()=>draft.value!==null&&JSON.stringify(draft.value)!==original.value)
const {confirmLeave}=useUnsavedChanges(dirty)
onBeforeRouteUpdate((to,from)=>to.query.id===from.query.id||confirmLeave())
let requestId=0
const permissionLabels={emit_event:'提交观察事件',register_tool:'提供原生工具'}
const labels=(values,dictionary)=>values.map(value=>dictionary[value]||value).join('、')
// A purpose label answers "what is this for"; it is not a second plugin
// taxonomy and it does not merge or split any plugin.
const purposes={sensory:'来源监测',tool:'查询与工作工具',scheduled:'按时间执行',hybrid:'组合能力'}
const purpose=plugin=>purposes[plugin.plugin_type]||plugin.plugin_type
// Five independent facts.  Collapsing them into one green switch is how a
// saved-but-not-loaded plugin comes to look like an available one.  Which
// tool the model picks stays an Agent decision and is not one of these.
const entries=plugin=>plugin.tools.length+plugin.handlers.length+plugin.hooks.length
const states=plugin=>[
  {label:'配置',ok:plugin.configured,text:plugin.configured?'已填写':'未配置'},
  {label:'全局保存',ok:plugin.enabled,text:plugin.enabled?'已保存为启用':'已保存为停用'},
  {label:'运行时',ok:plugin.active_enabled,text:plugin.active_enabled?'当前已加载并启用':(plugin.state==='error'?'加载失败':'当前未启用')},
  {label:'开放群',ok:plugin.open_scenes.filter(scene=>scene.enabled).length>0,text:`${plugin.open_scenes.filter(scene=>scene.enabled).length} 个已启用群`},
  {label:'提供能力',ok:plugin.active_enabled&&entries(plugin)>0,text:!plugin.active_enabled?'当前未加载，未注册入口':(entries(plugin)?`${entries(plugin)} 项入口已注册`:'已加载但没有注册工具或钩子')},
]
const problem=plugin=>{
  if (!plugin.configured) return '尚未填写参数'
  if (plugin.last_error) return plugin.last_error
  if (plugin.enabled && !plugin.active_enabled) return `已保存为启用，但运行时状态为 ${plugin.state}`
  if (plugin.enabled && !plugin.open_scenes.filter(scene=>scene.enabled).length) return '全局已启用，但还没有加入任何群'
  return ''
}
const needsAttention=plugin=>Boolean(problem(plugin))
const filtered=computed(()=>plugins.value.filter(plugin=>{
  if (filter.value==='attention' && !needsAttention(plugin)) return false
  if (filter.value==='available' && !plugin.active_enabled) return false
  if (filter.value==='unconfigured' && plugin.configured) return false
  const text=`${plugin.name} ${plugin.id} ${plugin.description}`.toLowerCase()
  return text.includes((search.value||'').toLowerCase())
}))
const counts=computed(()=>({all:plugins.value.length,attention:plugins.value.filter(needsAttention).length,
  available:plugins.value.filter(plugin=>plugin.active_enabled).length,
  unconfigured:plugins.value.filter(plugin=>!plugin.configured).length}))
const filters=[{value:'all',title:'全部'},{value:'attention',title:'需要处理'},{value:'available',title:'当前可用'},{value:'unconfigured',title:'未配置'}]
const detailTabs=[{value:'config',title:'配置'},{value:'scenes',title:'开放群'},{value:'diagnostics',title:'诊断'}]
const configFieldsRef=ref(null)
// The dotted paths the schema declares as credentials.  A nested credential
// (the Gateway service token) is the same secret as a top-level one, so the
// form is told the path and not just the leaf name.
const secretPaths=computed(()=>selected.value?.secret_paths||[])
// A summary that names a field is only a summary if the field can be reached
// from it; the field itself carries the same explanation.
const configuredSaved=computed(()=>Boolean(selected.value?.configured)&&!dirty.value)
const saveHint=computed(()=>{
  if (!selected.value) return ''
  return selected.value.config_apply==='restart_plugin'
    ? '保存先写入根配置，然后重新装载该插件；重新装载完成前页面显示的还是上一次的运行状态。'
    : '保存先写入根配置，再由该插件原位应用；应用失败时保存仍在，页面会同时显示两种结果。'
})
const localProblems=computed(()=>draft.value
  ? draftProblems(selected.value.config_schema,draft.value,{secrets:secretPaths.value,configSet:selected.value.config_set})
  : [])
function focusField(key) { configFieldsRef.value?.focus(key) }
// A server rejection keeps the draft and points at the field it names; the
// same sentence appears next to that field through PluginConfigFields.
const serverProblems=ref([])
const problems=computed(()=>[...localProblems.value,...serverProblems.value])
function locate(error) {
  const detail=Array.isArray(error.details)?error.details:[]
  const found=[]
  for (const item of detail) {
    // The server names a field by its path; the form matches on the same
    // dotted path so a nested backend field is reached, not just its parent.
    const path=(item.loc||[]).filter(part=>typeof part==='string'&&part!=='body'&&part!=='config')
    found.push({key:path.join('.'),message:path.length?`${path.join(' → ')}：${item.msg}`:item.msg})
  }
  return found
}
function setDraft(plugin) {
  draft.value=plugin.config===null?null:configDraft(plugin.config,plugin.config_schema,plugin.secret_paths||[])
  original.value=JSON.stringify(draft.value)
}
function beginConfiguration() {
  draft.value=blankConfigDraft(selected.value.config_schema,selected.value.secret_paths||[])
}
function selectFromRoute() {
  const plugin=plugins.value.find(item=>item.id===route.query.id)||null
  if (!plugin) { selected.value=null; draft.value=null; original.value='null'; return }
  const preserve=selected.value?.id===plugin.id&&dirty.value
  selected.value=plugin
  detailTab.value=['config','scenes','diagnostics'].includes(route.query.pane)?route.query.pane:'config'
  if (!preserve) setDraft(plugin)
}
function setPane(value) { router.replace({query:{...route.query,pane:value==='config'?undefined:value}}) }
async function load() {
  const request=++requestId
  loading.value=true; error.value=''
  try {
    const result=await api('/api/plugins/list')
    if (request!==requestId) return
    plugins.value=result; loaded.value=true; readAt.value=Date.now()/1000; selectFromRoute()
    return true
  } catch(e) { if (request===requestId) error.value=e.message }
  finally { if (request===requestId) loading.value=false }
  return false
}
async function showApplyError(problem) {
  serverProblems.value=locate(problem)
  const refreshed=await load()
  if(refreshed && problem.details?.config_saved && selected.value) setDraft(selected.value)
  error.value=problem.message+(refreshed?'':'；当前状态刷新失败：'+error.value)
}
function close() {
  const openedFrom=selected.value?.id
  router.push({name:'plugins'})
  // The dialog is opened from a card in the list behind it, so the trigger is
  // still there to receive focus back.
  nextTick(()=>document.querySelector(`[data-plugin-trigger="${openedFrom}"]`)?.focus?.())
}
async function toggle(plugin,enabled=!plugin.enabled) {
  if (busy.value||!plugin.configured) return
  busy.value=`toggle:${plugin.id}`; error.value=''; message.value=''
  try {
    await api('/api/plugins/toggle',{method:'POST',body:JSON.stringify({plugin_id:plugin.id,enabled})})
    message.value=enabled?'启用状态已写入根配置并应用到运行时':'停用状态已写入根配置并从运行时卸下'
    await load()
  } catch(e) { await showApplyError(e) }
  finally { busy.value='' }
}
async function save() {
  if (busy.value||!selected.value||!draft.value) return
  error.value=''; message.value=''
  serverProblems.value=[]
  if (localProblems.value.length) {
    error.value='参数尚未通过本地检查，未提交保存；请修正下列字段后重试。'
    focusField(localProblems.value[0].key)
    return
  }
  busy.value='config'
  const restart=selected.value.config_apply==='restart_plugin'
  const savedEnabled=selected.value.enabled
  try {
    const config=configValue(draft.value,selected.value.config_schema,
      {secrets:selected.value.secret_paths||[],preserveSecrets:selected.value.configured})
    // A branch that this save is actually submitting has to carry its own
    // required fields.  The same holds for a first configuration: every field
    // there is being defined, so an unfilled required one is a gap the
    // operator can still fix, not a stored value to leave alone.
    const problems=draftProblems(selected.value.config_schema,config,
      {secrets:selected.value.secret_paths||[],requirePresent:!selected.value.configured})
    if (problems.length) {
      serverProblems.value=problems
      error.value='参数尚未通过本地检查，未提交保存；请修正下列字段后重试。'
      focusField(problems[0].key)
      return
    }
    await api('/api/plugins/config',{method:'POST',body:JSON.stringify({plugin_id:selected.value.id,config})})
    original.value=JSON.stringify(draft.value)
    // Reaching here means the file was written and the runtime apply did not
    // raise; whether the plugin is actually loaded still depends on the saved
    // enable flag, which the refreshed 运行时 row states.
    message.value=!savedEnabled
      ? '参数已写入根配置；该插件当前保存为停用，没有装载，也不建立源连接'
      : restart
        ? '参数已写入根配置，该插件已按新参数重新装载；加载结果见下方运行时一行'
        : '参数已写入根配置，该插件已原位应用新参数；加载结果见下方运行时一行'
    serverProblems.value=[]
    await load()
  } catch(e) { await showApplyError(e) }
  finally { busy.value='' }
}
watch(()=>route.query.id,selectFromRoute)
load()
loadScopes()
</script>

<template>
  <div class="page-stack">
    <PageHeader title="插件" description="按用途查找，逐项确认“已配置、已保存、已加载、已开放群”。启用不代表来源可用，刷新页面不会抓取源数据或调用模型。"><v-btn variant="outlined" :loading="loading" @click="load">刷新</v-btn></PageHeader>
    <v-alert v-if="error" type="error" variant="tonal">{{ error }}<span v-if="readAt"> · 上次读取 {{ fmtTime(readAt) }}</span></v-alert>
    <v-alert v-if="message" type="success" variant="tonal" closable @click:close="message=''">{{ message }}</v-alert>
    <div class="plugin-toolbar">
      <v-text-field v-model="search" label="查找插件" hide-details clearable density="comfortable" class="toolbar-search" />
      <v-chip-group v-model="filter" mandatory class="toolbar-filters">
        <v-chip v-for="item in filters" :key="item.value" :value="item.value" filter variant="tonal" size="small">{{ item.title }} · {{ counts[item.value] }}</v-chip>
      </v-chip-group>
    </div>
    <v-progress-linear v-if="loading" indeterminate />
    <div class="plugin-list">
      <v-card v-for="plugin in filtered" :key="plugin.id" class="pa-5">
        <div class="plugin-heading"><div class="plugin-title"><p class="muted mb-2">{{ purpose(plugin) }}</p><h2>{{ plugin.name }}</h2></div><StatusBadge domain="plugin" :status="plugin.state" /></div>
        <p class="clamp-2 plugin-description">{{ plugin.description }}</p>
        <ul class="state-list"><li v-for="item in states(plugin)" :key="item.label"><span class="state-label">{{ item.label }}</span><span :class="item.ok?'state-ok':'state-warn'">{{ item.text }}</span></li></ul>
        <v-alert v-if="problem(plugin)" type="warning" variant="tonal" density="compact" class="my-3">{{ problem(plugin) }}</v-alert>
        <div class="actions mt-4"><v-btn :data-plugin-trigger="plugin.id" color="primary" variant="tonal" :to="{name:'plugins',query:{id:plugin.id}}">详情与配置</v-btn><v-btn :color="plugin.enabled?'error':'primary'" variant="outlined" :disabled="!!busy||!plugin.configured" :loading="busy===`toggle:${plugin.id}`" @click="toggle(plugin)">{{ !plugin.configured?'先填写配置':plugin.enabled?'停用':'启用' }}</v-btn><v-btn v-if="plugin.enabled&&!plugin.active_enabled" color="primary" variant="outlined" :disabled="!!busy" @click="toggle(plugin,true)">重新启用</v-btn></div>
      </v-card>
    </div>
    <v-card v-if="loaded&&!error&&!filtered.length" class="pa-8 text-center muted">{{ plugins.length?'没有符合当前筛选的插件。':'当前没有已声明插件' }}</v-card>
    <v-dialog :model-value="!!route.query.id" max-width="900" scrollable :persistent="!!busy" @update:model-value="value=>!value&&close()">
      <v-card>
        <v-card-title class="dialog-title">插件详情<v-btn variant="text" :disabled="!!busy" @click="close">关闭</v-btn></v-card-title>
        <v-card-text>
          <v-progress-linear v-if="loading" indeterminate />
          <v-alert v-if="error" type="error" variant="tonal" class="mb-4">{{ error }}</v-alert>
          <v-alert v-if="loaded&&!selected&&!error" type="warning" variant="tonal">此插件不在当前声明目录中。</v-alert>
          <template v-if="selected">
            <div class="plugin-heading"><div><h2>{{ selected.name }}</h2><p class="muted mt-2">{{ purpose(selected) }} · {{ selected.id }}<span v-if="selected.version"> · v{{ selected.version }}</span></p></div><StatusBadge domain="plugin" :status="selected.state" /></div>
            <p class="full-text mb-4">{{ selected.description }}</p>
            <ul class="state-list"><li v-for="item in states(selected)" :key="item.label"><span class="state-label">{{ item.label }}</span><span :class="item.ok?'state-ok':'state-warn'">{{ item.text }}</span></li></ul>
            <v-alert v-if="problem(selected)" type="warning" variant="tonal" class="my-4">{{ problem(selected) }}</v-alert>
            <v-tabs :model-value="detailTab" color="primary" @update:model-value="setPane"><v-tab v-for="item in detailTabs" :key="item.value" :value="item.value">{{ item.title }}</v-tab></v-tabs>
            <template v-if="detailTab==='config'">
              <p class="muted my-4">{{ selected.config_apply==='restart_plugin'?'该插件保存后会重新装载：未提交的工作可能中断；其他插件继续运行。':'保存后由本插件原位应用新参数。' }}</p>
              <v-alert v-if="!selected.configured" type="info" variant="tonal" class="mb-4">尚未配置，当前不装载此插件或建立源连接。参数由运营实际填写后保存，保存不会自动启用。</v-alert>
              <v-btn v-if="!draft" color="primary" variant="tonal" :disabled="!!busy" @click="beginConfiguration">填写插件参数</v-btn>
              <v-form v-if="draft" :disabled="!!busy" @submit.prevent="save">
                <v-alert v-if="problems.length" type="error" variant="tonal" class="mb-4">
                  <p class="mb-2">请先修正以下参数；修正前不会提交保存。</p>
                  <ul class="error-summary"><li v-for="item in problems" :key="item.key+item.message"><button type="button" class="error-link" @click="focusField(item.key)">{{ item.message }}</button></li></ul>
                </v-alert>
                <PluginConfigFields ref="configFieldsRef" v-model="draft" :schema="selected.config_schema" :secrets="selected.secret_paths" :config-set="selected.config_set" :problems="problems" />
                <p v-if="selected.secret_paths.length" class="muted my-4">凭据仅显示是否已保存。已保存的凭据留空时保留；首次配置必需凭据须实际填写。</p>
                <div class="actions mt-5"><v-btn type="submit" color="primary" :loading="busy==='config'" :disabled="!!busy">{{ selected.config_apply==='restart_plugin'?'保存并重新装载该插件':'保存插件参数' }}</v-btn><span v-if="dirty" class="muted">有未保存的修改，保存后会写入根配置</span><span v-else-if="configuredSaved" class="muted">当前显示的是已写入根配置的值</span></div>
                <p class="muted mt-3">{{ saveHint }}</p>
              </v-form>
            </template>
            <template v-else-if="detailTab==='scenes'">
              <h3 class="my-4">配置开放的群</h3>
              <div class="open-scenes"><v-btn v-for="scene in selected.open_scenes" :key="scene.scene_id" variant="text" :to="{name:'scene',params:{sceneId:scene.scene_id},query:{tab:'settings'}}">{{ sceneName(scene.scene_id)||scene.scene_id }}<span v-if="sceneName(scene.scene_id)" class="scene-id">{{ scene.scene_id }}</span> · {{ scene.enabled?'群已启用':'群已停用' }}</v-btn><p v-if="!selected.open_scenes.length" class="muted">尚未向任何群开放此插件。</p></div>
              <p class="muted mt-3">本群的开关与业务参数在“本群设置”里保存，与全局参数分开；全局启用后仍需在目标群加入该插件。全局未就绪时可以保存停用草稿，但那不会让它变得可用。这里不维护第二份本群开关，链接到的是同一个群设置表单。</p>
              <RouterLink :to="{name:'scenes'}">打开群列表</RouterLink>
            </template>
            <template v-else>
              <dl class="facts my-4"><dt>已获权限</dt><dd>{{ labels(selected.permissions,permissionLabels)||'尚未装载或无特殊权限' }}</dd><dt>来源目录</dt><dd>{{ selected.directory }}</dd><dt>最近使用</dt><dd>{{ fmtTime(selected.last_run_at) }}</dd><dt>运行错误</dt><dd>{{ selected.error_count }} 次</dd></dl>
              <v-alert v-if="selected.last_error" type="error" variant="tonal" class="my-4">{{ selected.last_error }}</v-alert>
              <h3 class="mb-4">源状态</h3>
              <p class="muted mb-4">此处只展示已经取得的状态。缓存到期刷新失败时，本次查询失败；旧快照不延长有效期。Python 计算类插件没有源，不因为没有“最近成功获取源”而算故障。</p>
              <dl class="facts"><dt>最近成功</dt><dd>{{ fmtTime(selected.source_status.last_success_at) }}</dd><dt>最近失败</dt><dd>{{ fmtTime(selected.source_status.last_error_at) }}</dd><dt v-if="selected.source_status.source_url">来源</dt><dd v-if="selected.source_status.source_url" class="full-text">{{ selected.source_status.source_url }}</dd><dt v-if="selected.source_status.source_updated_at">源更新时间</dt><dd v-if="selected.source_status.source_updated_at">{{ selected.source_status.source_updated_at }}</dd></dl>
              <v-alert v-if="selected.source_status.last_error" type="error" variant="tonal" class="mt-4">{{ selected.source_status.last_error }}<div>这是最近一次失败记录，当前新鲜度须结合成功取得时间判断。</div></v-alert>
              <ResourceViewer v-if="selected.source_status.data_scope" title="已取得资料范围" :content="selected.source_status.data_scope" class="mt-4" />
              <h3 class="mt-5 mb-3">已注册入口</h3>
              <div class="entry-list">
                <div v-for="tool in selected.tools" :key="tool.name" class="entry-row"><strong>{{ tool.purpose }}</strong><p class="entity-id">{{ tool.name }} · {{ tool.kind }} · {{ tool.roles.join(' / ') }}</p><p>{{ tool.description }}</p></div>
                <div v-for="handler in selected.handlers" :key="handler.id" class="entry-row"><strong>{{ handler.description }}</strong><p class="entity-id">{{ handler.id }} · 优先级 {{ handler.priority }} · {{ handler.consume?'消费消息':'继续传播' }} · {{ handler.require_to_me?'需要提及':'无需提及' }}</p><p>来源 {{ handler.sources.join(' / ') }} · {{ handler.event_types.join(' / ') }}</p><ResourceViewer title="匹配规则" :content="handler.match" /></div>
                <div v-for="hook in selected.hooks" :key="'hook:'+hook.id" class="entry-row"><strong>{{ hook.phase }}</strong><p>{{ hook.id }} · 作用范围 {{ hook.scope }} · 优先级 {{ hook.priority }}</p></div>
                <p v-if="!selected.tools.length&&!selected.handlers.length&&!selected.hooks.length" class="muted">当前未装载入口。启用时按插件声明注册。</p>
              </div>
              <p class="muted mt-3">模型是否会调用某个工具由 Agent 决定；入口存在不等于一定被使用。</p>
              <ResourceViewer v-if="selected.work" title="已声明的长期工作" :content="selected.work" class="my-4" /><ResourceViewer v-if="selected.active_tasks.length" title="当前所属任务" :content="selected.active_tasks" class="my-4" />
              <v-expansion-panels class="mt-4"><v-expansion-panel title="声明与配置结构"><v-expansion-panel-text><ResourceViewer title="声明与配置结构" :content="{id:selected.id,emitted_events:selected.emitted_events,registered_tools:selected.registered_tools,config_schema:selected.config_schema,scene_config_schema:selected.scene_config_schema}" /></v-expansion-panel-text></v-expansion-panel></v-expansion-panels>
            </template>
          </template>
        </v-card-text>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped>
.plugin-toolbar{display:flex;gap:16px;align-items:center;flex-wrap:wrap}.toolbar-search{max-width:320px;min-width:200px}
.scene-id{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px;color:#64748b;margin-left:6px}
.entry-list{display:grid;gap:12px}.entry-row{border:1px solid #e2e8f0;border-radius:8px;padding:14px;overflow-wrap:anywhere}.entry-row p{margin-top:6px;line-height:1.6}
.state-list{list-style:none;display:grid;gap:6px;margin:14px 0;padding:0;font-size:13px}.state-list li{display:flex;gap:12px}.state-label{color:#64748b;min-width:64px}.state-ok{color:#16845c}.state-warn{color:#9a5514}
.error-summary{list-style:none;padding:0;margin:0;display:grid;gap:4px}.error-link{background:none;border:0;padding:0;color:inherit;text-align:left;text-decoration:underline;cursor:pointer;font:inherit}
.plugin-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.plugin-heading,.dialog-title{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}.plugin-title{min-width:0}.plugin-title h2,.plugin-heading h2{font-size:19px;overflow-wrap:anywhere}.plugin-description{margin:16px 0;line-height:1.7;min-height:3.4em}.plugin-meta,.actions,.open-scenes{display:flex;gap:10px 16px;flex-wrap:wrap;align-items:center}.plugin-meta,.source-summary{font-size:13px;color:#64748b;margin-bottom:12px}.source-summary{display:grid;gap:6px}.full-text{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.7}.facts{display:grid;grid-template-columns:100px minmax(0,1fr);gap:12px;line-height:1.7}.facts dt{color:#64748b}.facts dd{margin:0;overflow-wrap:anywhere}@media(max-width:850px){.plugin-list{grid-template-columns:minmax(0,1fr)}}@media(max-width:550px){.plugin-description{min-height:0}.toolbar-search{max-width:none;width:100%}}
</style>
