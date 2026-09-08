<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter, onBeforeRouteUpdate } from 'vue-router'
import { api, fmtTime } from '../api.js'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'
import PageHeader from '../components/PageHeader.vue'
import StatusBadge from '../components/StatusBadge.vue'
import ResourceViewer from '../components/ResourceViewer.vue'

const route=useRoute(), router=useRouter()
const plugins=ref([]), loading=ref(false), loaded=ref(false), readAt=ref(null), error=ref(''), message=ref(''), busy=ref('')
const selected=ref(null), draft=ref(null), original=ref('null')
const dirty=computed(()=>draft.value!==null&&JSON.stringify(draft.value)!==original.value)
const {confirmLeave}=useUnsavedChanges(dirty)
onBeforeRouteUpdate((to,from)=>to.query.id===from.query.id||confirmLeave())
let requestId=0
const permissionLabels={emit_event:'提交观察事件',register_tool:'提供原生工具',intercept_action:'检查待发行动'}
const eventLabels={LIVE_STARTED:'发现直播开始',LIVE_ENDED:'发现直播结束'}
const toolLabels={web_search:'搜索网页',read_page:'读取网页',get_video_info:'查询视频信息',search_bilibili:'搜索哔哩哔哩',get_dynamic_feed:'查询用户动态',get_live_schedule:'查询直播日程',get_live_status:'查询实际直播状态',get_asoul_dynamics:'该源已抓取的最新动态',search_asoul_dynamics:'搜索动态',read_asoul_dynamic:'读取动态详情',get_asoul_on_this_day:'历史同日',search_asoul_fanart:'搜索二创',get_random_asoul_fanart:'随机查询二创',summarize_group_chat:'创建本群总结工作',read_group_chat_window:'读取总结范围原话'}
const commandFields=[{key:'calendar_today',title:'今日范围命令词'},{key:'calendar_tomorrow',title:'明日范围命令词'},{key:'calendar_week',title:'自然周范围命令词'}]
const labels=(values,dictionary)=>values.map(value=>dictionary[value]||value).join('、')
const fields=computed(()=>selected.value?Object.entries(selected.value.config_schema.properties).filter(([key])=>selected.value.id!=='asoul_calendar'||!['commands','avatar_paths'].includes(key)).map(([key,schema])=>({key,schema})):[])
function setDraft(plugin) {
  if (plugin.config===null) { draft.value=null; original.value='null'; return }
  const values=structuredClone(plugin.config)
  for (const [key,schema] of Object.entries(plugin.config_schema.properties)) {
    if (plugin.secret_fields.includes(key)) values[key]=''
    else if (schema.type==='array') values[key]=values[key].join('\n')
  }
  if (plugin.id==='asoul_calendar') {
    values.commands=Object.fromEntries(commandFields.map(({key})=>[key,(values.commands[key]||[]).join('\n')]))
    values.avatarRows=Object.entries(values.avatar_paths).map(([name,path])=>({name,path}))
    delete values.avatar_paths
  }
  draft.value=values; original.value=JSON.stringify(values)
}
function beginConfiguration() {
  const values={}
  for (const {key,schema} of fields.value) {
    if (schema.const!==undefined) values[key]=schema.const
    else if (['integer','number','boolean'].includes(schema.type)) values[key]=null
    else values[key]=''
  }
  if (selected.value.id==='asoul_calendar') { values.commands=Object.fromEntries(commandFields.map(({key})=>[key,''])); values.avatarRows=[] }
  draft.value=values
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
    message.value=result.requires_restart?'插件启用状态已保存，需手动重启后生效':'插件启用状态已保存'
    await load()
  } catch(e) { error.value=e.message }
  finally { busy.value='' }
}
const lines=value=>value.split(/[\n,，]+/).map(item=>item.trim()).filter(Boolean)
async function save() {
  if (busy.value||!selected.value||!draft.value) return
  busy.value='config'; error.value=''; message.value=''
  try {
    const config=structuredClone(draft.value)
    for (const {key,schema} of fields.value) {
      if (selected.value.secret_fields.includes(key)) {
        config[key]=config[key].trim()
        if (!config[key]&&selected.value.configured) delete config[key]
      }
      else if (schema.type==='array') config[key]=lines(config[key]).map(value=>schema.items.type==='integer'?Number(value):value)
    }
    if (selected.value.id==='asoul_calendar') {
      config.commands=Object.fromEntries(commandFields.map(({key})=>[key,lines(config.commands[key])]))
      const avatars={}
      for (const item of config.avatarRows) {
        const name=item.name.trim(), path=item.path.trim()
        if (!name||!path) throw new Error('头像资源须填写实际名称和文件路径')
        if (Object.hasOwn(avatars,name)) throw new Error(`头像资源名称重复：${name}`)
        avatars[name]=path
      }
      config.avatar_paths=avatars
      delete config.avatarRows
    }
    const result=await api('/api/plugins/config',{method:'POST',body:JSON.stringify({plugin_id:selected.value.id,config})})
    original.value=JSON.stringify(draft.value)
    message.value=result.requires_restart?'插件参数已写入根文件，需手动重启后完整生效':'插件参数已保存'
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
        <div class="plugin-heading"><div class="plugin-title"><p class="muted mb-2">{{ ({sensory:'信息监测',tool:'查询与工作工具',scheduled:'计划能力',interceptor:'行动检查',hybrid:'组合能力'})[plugin.plugin_type]||plugin.plugin_type }}</p><h2>{{ plugin.name }}</h2></div><StatusBadge domain="plugin" :status="plugin.state" /></div>
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
            <dl class="facts"><dt>全局开关</dt><dd>{{ selected.enabled?'已保存为启用':'已保存为停用' }} · {{ selected.active_enabled?'当前已启用':'当前未启用' }}</dd><dt>已获权限</dt><dd>{{ labels(selected.permissions,permissionLabels)||'尚未装载或无特殊权限' }}</dd><dt>声明事件</dt><dd>{{ labels(selected.emitted_events,eventLabels)||'无' }}</dd><dt>声明工具</dt><dd>{{ labels(selected.registered_tools,toolLabels)||'无' }}</dd><dt>最近使用</dt><dd>{{ fmtTime(selected.last_run_at) }}</dd><dt>运行错误</dt><dd>{{ selected.error_count }} 次</dd></dl>
            <v-alert v-if="selected.last_error" type="error" variant="tonal" class="my-4">{{ selected.last_error }}</v-alert>
            <v-divider class="my-5" />
            <h3 class="mb-4">源状态</h3>
            <p class="muted mb-4">此处只展示已经取得的状态。缓存到期刷新失败时，本次查询失败；旧快照不延长有效期。</p>
            <dl class="facts"><dt>最近成功</dt><dd>{{ fmtTime(selected.source_status.last_success_at) }}</dd><dt>最近失败</dt><dd>{{ fmtTime(selected.source_status.last_error_at) }}</dd><dt v-if="selected.source_status.source_url">来源</dt><dd v-if="selected.source_status.source_url" class="full-text">{{ selected.source_status.source_url }}</dd><dt v-if="selected.source_status.source_updated_at">源更新时间</dt><dd v-if="selected.source_status.source_updated_at">{{ selected.source_status.source_updated_at }}</dd></dl>
            <v-alert v-if="selected.source_status.last_error" type="error" variant="tonal" class="mt-4">{{ selected.source_status.last_error }}<div>这是最近一次失败记录，当前新鲜度须结合成功取得时间判断。</div></v-alert>
            <ResourceViewer v-if="selected.source_status.data_scope" title="已取得资料范围" :content="selected.source_status.data_scope" class="mt-4" />
            <v-divider class="my-5" />
            <h3 class="mb-3">配置开放的群</h3>
            <div class="open-scenes"><v-btn v-for="scene in selected.open_scenes" :key="scene.scene_id" variant="text" :to="{name:'scene',params:{sceneId:scene.scene_id},query:{tab:'settings'}}">{{ scene.scene_id }} · {{ scene.enabled?'群已启用':'群已停用' }}</v-btn><p v-if="!selected.open_scenes.length" class="muted">尚未向任何群开放此插件。</p></div>
            <p class="muted mt-3">全局启用后，仍按本群聊天、命令或公告选项执行；主播订阅在“本群设置”选择。</p>
            <v-divider class="my-5" />
            <v-alert v-if="!selected.configured" type="info" variant="tonal" class="mb-4">尚未配置，当前不装载此插件或建立源连接。参数由运营实际填写后保存，保存不会自动启用。</v-alert>
            <v-btn v-if="!draft&&fields.length" color="primary" variant="tonal" :disabled="!!busy" @click="beginConfiguration">填写插件参数</v-btn>
            <v-form v-if="draft" :disabled="!!busy" @submit.prevent="save">
              <h3 class="mb-4">插件参数</h3>
              <div class="config-grid">
                <template v-for="field in fields" :key="field.key">
                  <v-text-field v-if="selected.secret_fields.includes(field.key)" v-model="draft[field.key]" :label="field.schema.title||field.key" type="password" autocomplete="new-password" :placeholder="selected.config_set[field.key]?'已保存，留空保留':'尚未配置'" :hint="field.schema.description" />
                  <v-text-field v-else-if="field.schema.const!==undefined" :model-value="field.schema.const" :label="field.schema.title||field.key" readonly :hint="field.schema.description" />
                  <v-select v-else-if="field.schema.enum" v-model="draft[field.key]" :items="field.schema.enum" :label="field.schema.title||field.key" />
                  <v-select v-else-if="field.schema.type==='boolean'" v-model="draft[field.key]" :items="[{title:'是',value:true},{title:'否',value:false}]" :label="field.schema.title||field.key" />
                  <v-text-field v-else-if="['number','integer'].includes(field.schema.type)" v-model.number="draft[field.key]" :label="field.schema.title||field.key" type="number" :min="field.schema.minimum" :max="field.schema.maximum" :step="field.schema.type==='integer'?1:'any'" :hint="field.schema.description" />
                  <v-textarea v-else-if="field.schema.type==='array'||field.key.endsWith('_instructions')" v-model="draft[field.key]" :label="field.schema.title||field.key" rows="3" auto-grow :hint="field.schema.type==='array'?'每行一个值，空列表保持为空':field.schema.description" />
                  <v-text-field v-else v-model="draft[field.key]" :label="field.schema.title||field.key" :hint="field.schema.description" />
                </template>
              </div>
              <template v-if="selected.id==='asoul_calendar'">
                <h4 class="mt-5 mb-3">精确日程命令词</h4>
                <div class="config-grid"><v-textarea v-for="field in commandFields" :key="field.key" v-model="draft.commands[field.key]" :label="field.title" rows="3" hint="每行一个精确命令词；留空表示不提供该类命令。" persistent-hint /></div>
                <div class="plugin-heading mt-5 mb-3"><h4>本地头像资源</h4><v-btn variant="tonal" :disabled="!!busy" @click="draft.avatarRows.push({name:'',path:''})">添加资源</v-btn></div>
                <p class="muted mb-3">填写源署名与实际本地文件路径，不自动选图或替换缺失资源。</p>
                <div v-for="(avatar,index) in draft.avatarRows" :key="index" class="avatar-row"><v-text-field v-model="avatar.name" label="源署名" /><v-text-field v-model="avatar.path" label="本地资源路径" /><v-btn variant="text" color="error" :disabled="!!busy" @click="draft.avatarRows.splice(index,1)">移除</v-btn></div>
              </template>
              <p v-if="selected.secret_fields.length" class="muted my-4">凭据仅显示是否已保存。已保存的凭据留空时保留；首次配置必需凭据须实际填写。</p>
              <div class="actions mt-5"><v-btn type="submit" color="primary" :loading="busy==='config'" :disabled="!!busy||!dirty">保存插件参数</v-btn><span v-if="dirty" class="muted">有未保存修改</span></div>
            </v-form>
            <v-expansion-panels class="mt-5"><v-expansion-panel title="参数结构与声明"><v-expansion-panel-text><ResourceViewer title="声明与配置结构" :content="{id:selected.id,emitted_events:selected.emitted_events,registered_tools:selected.registered_tools,config_schema:selected.config_schema}" /></v-expansion-panel-text></v-expansion-panel></v-expansion-panels>
          </template>
        </v-card-text>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped>
.plugin-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.plugin-heading,.dialog-title{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}.plugin-title{min-width:0}.plugin-title h2,.plugin-heading h2{font-size:19px;overflow-wrap:anywhere}.plugin-description{margin:16px 0;line-height:1.7;min-height:3.4em}.plugin-meta,.actions,.open-scenes{display:flex;gap:10px 16px;flex-wrap:wrap;align-items:center}.plugin-meta,.source-summary{font-size:13px;color:#64748b;margin-bottom:12px}.source-summary{display:grid;gap:6px}.full-text{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.7}.facts{display:grid;grid-template-columns:100px minmax(0,1fr);gap:12px;line-height:1.7}.facts dt{color:#64748b}.facts dd{margin:0;overflow-wrap:anywhere}.config-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.avatar-row{display:grid;grid-template-columns:minmax(120px,1fr) minmax(180px,2fr) auto;gap:12px;align-items:start}@media(max-width:850px){.plugin-list{grid-template-columns:minmax(0,1fr)}}@media(max-width:550px){.config-grid,.avatar-row{grid-template-columns:minmax(0,1fr)}.plugin-description{min-height:0}.avatar-row{padding-bottom:12px;border-bottom:1px solid #e2e8f0;margin-bottom:12px}}
</style>
