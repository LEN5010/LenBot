<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter, onBeforeRouteUpdate } from 'vue-router'
import { api, fmtTime } from '../api.js'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'
import PageHeader from '../components/PageHeader.vue'
import StatusBadge from '../components/StatusBadge.vue'
import ResourceViewer from '../components/ResourceViewer.vue'
import PluginConfigFields from '../components/PluginConfigFields.vue'
import {blankConfigDraft,configDraft,configValue} from '../lib/pluginConfig.js'

const route=useRoute(), router=useRouter()
const plugins=ref([]), loading=ref(false), loaded=ref(false), readAt=ref(null), error=ref(''), message=ref(''), busy=ref('')
const selected=ref(null), draft=ref(null), original=ref('null')
const dirty=computed(()=>draft.value!==null&&JSON.stringify(draft.value)!==original.value)
const {confirmLeave}=useUnsavedChanges(dirty)
onBeforeRouteUpdate((to,from)=>to.query.id===from.query.id||confirmLeave())
let requestId=0
const permissionLabels={emit_event:'提交观察事件',register_tool:'提供原生工具'}
const labels=(values,dictionary)=>values.map(value=>dictionary[value]||value).join('、')
function setDraft(plugin) {
  draft.value=plugin.config===null?null:configDraft(plugin.config,plugin.config_schema,plugin.secret_fields)
  original.value=JSON.stringify(draft.value)
}
function beginConfiguration() {
  draft.value=blankConfigDraft(selected.value.config_schema,selected.value.secret_fields)
}
function selectFromRoute() {
  const plugin=plugins.value.find(item=>item.id===route.query.id)||null
  if (!plugin) { selected.value=null; draft.value=null; original.value='null'; return }
  const preserve=selected.value?.id===plugin.id&&dirty.value
  selected.value=plugin
  if (!preserve) setDraft(plugin)
}
async function load() {
  const request=++requestId
  loading.value=true; error.value=''
  try {
    const result=await api('/api/plugins/list')
    if (request!==requestId) return
    plugins.value=result; loaded.value=true; readAt.value=Date.now()/1000; selectFromRoute()
  } catch(e) { if (request===requestId) error.value=e.message }
  finally { if (request===requestId) loading.value=false }
}
function close() { router.push({name:'plugins'}) }
async function toggle(plugin) {
  if (busy.value||!plugin.configured) return
  busy.value=`toggle:${plugin.id}`; error.value=''; message.value=''
  try {
    const result=await api('/api/plugins/toggle',{method:'POST',body:JSON.stringify({plugin_id:plugin.id,enabled:!plugin.enabled})})
    message.value='插件启用状态已保存并应用；当前状态见插件详情'
    await load()
  } catch(e) { error.value=e.message }
  finally { busy.value='' }
}
async function save() {
  if (busy.value||!selected.value||!draft.value) return
  busy.value='config'; error.value=''; message.value=''
  try {
    const config=configValue(draft.value,selected.value.config_schema,
      {secrets:selected.value.secret_fields,preserveSecrets:selected.value.configured})
    const result=await api('/api/plugins/config',{method:'POST',body:JSON.stringify({plugin_id:selected.value.id,config})})
    original.value=JSON.stringify(draft.value)
    message.value='插件参数已保存，当前运行状态：'+result.state
    await load()
  } catch(e) { error.value=e.message }
  finally { busy.value='' }
}
watch(()=>route.query.id,selectFromRoute)
load()
</script>

<template>
  <div class="page-stack">
    <PageHeader title="扩展能力" description="全局启用、源配置和开放群分别显示。启用不代表源可用，刷新页面不会抓取源数据或调用模型。"><v-btn variant="outlined" :loading="loading" @click="load">刷新</v-btn></PageHeader>
    <v-alert v-if="error" type="error" variant="tonal">{{ error }}<span v-if="readAt"> · 上次读取 {{ fmtTime(readAt) }}</span></v-alert>
    <v-alert v-if="message" type="success" variant="tonal" closable @click:close="message=''">{{ message }}</v-alert>
    <v-progress-linear v-if="loading" indeterminate />
    <div class="plugin-list">
      <v-card v-for="plugin in plugins" :key="plugin.id" class="pa-5">
        <div class="plugin-heading"><div class="plugin-title"><p class="muted mb-2">{{ ({sensory:'信息监测',tool:'查询与工作工具',scheduled:'计划能力',hybrid:'组合能力'})[plugin.plugin_type]||plugin.plugin_type }}</p><h2>{{ plugin.name }}</h2></div><StatusBadge domain="plugin" :status="plugin.state" /></div>
        <p class="clamp-2 plugin-description">{{ plugin.description }}</p>
        <div class="plugin-meta"><span>{{ plugin.configured?'已配置':'未配置' }}</span><span>全局{{ plugin.enabled?'启用':'停用' }}</span><span>开放 {{ plugin.open_scenes.filter(scene=>scene.enabled).length }} 个已启用群</span></div>
        <div class="source-summary"><span>最近成功获取 {{ fmtTime(plugin.source_status.last_success_at) }}</span><span>最近失败 {{ fmtTime(plugin.source_status.last_error_at) }}</span></div>
        <div class="actions mt-4"><v-btn color="primary" variant="tonal" :to="{name:'plugins',query:{id:plugin.id}}">详情与配置</v-btn><v-btn :color="plugin.enabled?'error':'primary'" variant="outlined" :disabled="!!busy||!plugin.configured" :loading="busy===`toggle:${plugin.id}`" @click="toggle(plugin)">{{ plugin.enabled?'停用':plugin.configured?'启用':'先填写配置' }}</v-btn></div>
      </v-card>
    </div>
    <v-card v-if="loaded&&!error&&!plugins.length" class="pa-8 text-center muted">当前没有已声明插件</v-card>
    <v-dialog :model-value="!!route.query.id" max-width="900" scrollable :persistent="!!busy" @update:model-value="value=>!value&&close()">
      <v-card>
        <v-card-title class="dialog-title">扩展能力详情<v-btn variant="text" :disabled="!!busy" @click="close">关闭</v-btn></v-card-title>
        <v-card-text>
          <v-progress-linear v-if="loading" indeterminate />
          <v-alert v-if="error" type="error" variant="tonal" class="mb-4">{{ error }}</v-alert>
          <v-alert v-if="loaded&&!selected&&!error" type="warning" variant="tonal">此插件不在当前声明目录中。</v-alert>
          <template v-if="selected">
            <div class="plugin-heading"><h2>{{ selected.name }}</h2><StatusBadge domain="plugin" :status="selected.state" /></div>
            <p class="entity-id my-3">{{ selected.id }}<span v-if="selected.version"> · v{{ selected.version }}</span></p>
            <p class="full-text mb-5">{{ selected.description }}</p>
            <dl class="facts"><dt>全局开关</dt><dd>{{ selected.enabled?'已保存为启用':'已保存为停用' }} · {{ selected.active_enabled?'当前已启用':'当前未启用' }}</dd><dt>已获权限</dt><dd>{{ labels(selected.permissions,permissionLabels)||'尚未装载或无特殊权限' }}</dd><dt>声明事件</dt><dd>{{ selected.emitted_events.join('、')||'无' }}</dd><dt>来源目录</dt><dd>{{ selected.directory }}</dd><dt>配置应用</dt><dd>{{ selected.config_apply==='restart_plugin'?'保存后正常停用并重新启用本插件':'保存后由插件原位应用' }}</dd><dt>最近使用</dt><dd>{{ fmtTime(selected.last_run_at) }}</dd><dt>运行错误</dt><dd>{{ selected.error_count }} 次</dd></dl>
            <v-alert v-if="selected.last_error" type="error" variant="tonal" class="my-4">{{ selected.last_error }}</v-alert>
            <v-divider class="my-5" />
            <h3 class="mb-3">已注册入口</h3>
            <div class="entry-list">
              <div v-for="tool in selected.tools" :key="tool.name" class="entry-row"><strong>{{ tool.purpose }}</strong><p class="entity-id">{{ tool.name }} · {{ tool.kind }} · {{ tool.roles.join(' / ') }}</p><p>{{ tool.description }}</p></div>
              <div v-for="handler in selected.handlers" :key="handler.id" class="entry-row"><strong>{{ handler.description }}</strong><p class="entity-id">{{ handler.id }} · 优先级 {{ handler.priority }} · {{ handler.consume?'消费消息':'继续传播' }} · {{ handler.require_to_me?'需要提及':'无需提及' }}</p><p>来源 {{ handler.sources.join(' / ') }} · {{ handler.event_types.join(' / ') }}</p><ResourceViewer title="匹配规则" :content="handler.match" /></div>
              <div v-for="hook in selected.hooks" :key="'hook:'+hook.id" class="entry-row"><strong>{{ hook.phase }}</strong><p>{{ hook.id }} · 作用范围 {{ hook.scope }} · 优先级 {{ hook.priority }}</p></div>
              <p v-if="!selected.tools.length&&!selected.handlers.length&&!selected.hooks.length" class="muted">当前未装载入口。启用时按插件声明注册。</p>
            </div>
            <ResourceViewer v-if="selected.active_tasks.length" title="当前所属任务" :content="selected.active_tasks" class="my-4" />
            <v-divider class="my-5" />
            <h3 class="mb-4">源状态</h3>
            <p class="muted mb-4">此处只展示已经取得的状态。缓存到期刷新失败时，本次查询失败；旧快照不延长有效期。</p>
            <dl class="facts"><dt>最近成功</dt><dd>{{ fmtTime(selected.source_status.last_success_at) }}</dd><dt>最近失败</dt><dd>{{ fmtTime(selected.source_status.last_error_at) }}</dd><dt v-if="selected.source_status.source_url">来源</dt><dd v-if="selected.source_status.source_url" class="full-text">{{ selected.source_status.source_url }}</dd><dt v-if="selected.source_status.source_updated_at">源更新时间</dt><dd v-if="selected.source_status.source_updated_at">{{ selected.source_status.source_updated_at }}</dd></dl>
            <v-alert v-if="selected.source_status.last_error" type="error" variant="tonal" class="mt-4">{{ selected.source_status.last_error }}<div>这是最近一次失败记录，当前新鲜度须结合成功取得时间判断。</div></v-alert>
            <ResourceViewer v-if="selected.source_status.data_scope" title="已取得资料范围" :content="selected.source_status.data_scope" class="mt-4" />
            <v-divider class="my-5" />
            <h3 class="mb-3">配置开放的群</h3>
            <div class="open-scenes"><v-btn v-for="scene in selected.open_scenes" :key="scene.scene_id" variant="text" :to="{name:'scene',params:{sceneId:scene.scene_id},query:{tab:'settings'}}">{{ scene.scene_id }} · {{ scene.enabled?'群已启用':'群已停用' }}</v-btn><p v-if="!selected.open_scenes.length" class="muted">尚未向任何群开放此插件。</p></div>
            <p class="muted mt-3">全局启用后，仍按“本群设置”中该插件的启用与业务参数执行。</p>
            <v-divider class="my-5" />
            <v-alert v-if="!selected.configured" type="info" variant="tonal" class="mb-4">尚未配置，当前不装载此插件或建立源连接。参数由运营实际填写后保存，保存不会自动启用。</v-alert>
            <v-btn v-if="!draft" color="primary" variant="tonal" :disabled="!!busy" @click="beginConfiguration">填写插件参数</v-btn>
            <v-form v-if="draft" :disabled="!!busy" @submit.prevent="save">
              <h3 class="mb-4">插件参数</h3>
              <p class="muted mb-4">{{ selected.config_apply==='restart_plugin'?'保存后将结束本插件未提交的运行，并按新参数重新启用；其他插件继续运行。':'保存后由本插件原位应用新参数。' }}</p>
              <PluginConfigFields v-model="draft" :schema="selected.config_schema" :secrets="selected.secret_fields" :config-set="selected.config_set" />
              <p v-if="selected.secret_fields.length" class="muted my-4">凭据仅显示是否已保存。已保存的凭据留空时保留；首次配置必需凭据须实际填写。</p>
              <div class="actions mt-5"><v-btn type="submit" color="primary" :loading="busy==='config'" :disabled="!!busy||!dirty">保存插件参数</v-btn><span v-if="dirty" class="muted">有未保存修改</span></div>
            </v-form>
            <v-expansion-panels class="mt-5"><v-expansion-panel title="参数结构与声明"><v-expansion-panel-text><ResourceViewer title="声明与配置结构" :content="{id:selected.id,emitted_events:selected.emitted_events,registered_tools:selected.registered_tools,config_schema:selected.config_schema,scene_config_schema:selected.scene_config_schema}" /></v-expansion-panel-text></v-expansion-panel></v-expansion-panels>
          </template>
        </v-card-text>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped>
.entry-list{display:grid;gap:12px}.entry-row{border:1px solid #e2e8f0;border-radius:8px;padding:14px;overflow-wrap:anywhere}.entry-row p{margin-top:6px;line-height:1.6}
.plugin-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.plugin-heading,.dialog-title{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}.plugin-title{min-width:0}.plugin-title h2,.plugin-heading h2{font-size:19px;overflow-wrap:anywhere}.plugin-description{margin:16px 0;line-height:1.7;min-height:3.4em}.plugin-meta,.actions,.open-scenes{display:flex;gap:10px 16px;flex-wrap:wrap;align-items:center}.plugin-meta,.source-summary{font-size:13px;color:#64748b;margin-bottom:12px}.source-summary{display:grid;gap:6px}.full-text{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.7}.facts{display:grid;grid-template-columns:100px minmax(0,1fr);gap:12px;line-height:1.7}.facts dt{color:#64748b}.facts dd{margin:0;overflow-wrap:anywhere}.config-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.avatar-row{display:grid;grid-template-columns:minmax(120px,1fr) minmax(180px,2fr) auto;gap:12px;align-items:start}@media(max-width:850px){.plugin-list{grid-template-columns:minmax(0,1fr)}}@media(max-width:550px){.config-grid,.avatar-row{grid-template-columns:minmax(0,1fr)}.plugin-description{min-height:0}.avatar-row{padding-bottom:12px;border-bottom:1px solid #e2e8f0;margin-bottom:12px}}
</style>
