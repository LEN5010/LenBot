<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { onBeforeRouteUpdate } from 'vue-router'
import { api, fmtTime } from '../api.js'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'
import PluginConfigFields from './PluginConfigFields.vue'
import ResourceViewer from './ResourceViewer.vue'
import {blankConfigDraft,configDraft,configValue} from '../lib/pluginConfig.js'

const props = defineProps({sceneId:{type:String,required:true}})
const emit = defineEmits(['saved'])
const record = ref(null), draft = ref(null), original = ref('null')
const loading = ref(false), saving = ref(false), error = ref(''), message = ref(''), readAt = ref(null)
let requestId = 0
const dirty = computed(()=>draft.value!==null&&JSON.stringify(draft.value)!==original.value)
const {confirmLeave} = useUnsavedChanges(dirty)
onBeforeRouteUpdate((to,from)=>to.params.sceneId===from.params.sceneId&&to.query.tab===from.query.tab||confirmLeave())
const isGroup = computed(()=>/^group:[1-9]\d*$/.test(props.sceneId))
function makeDraft(settings) {
  if (!settings) return null
  return {...settings,plugins:Object.fromEntries(Object.entries(settings.plugins).map(([id,item])=>[id,
    {...item,config:configDraft(item.config,record.value.plugins.find(plugin=>plugin.id===id).scene_config_schema)}]))}
}
function addPlugin(plugin) {
  draft.value.plugins[plugin.id]={enabled:false,config:blankConfigDraft(plugin.scene_config_schema)}
}
const draftEffect = computed(()=>{
  if (!draft.value||!dirty.value) return record.value?.effect||''
  if (!draft.value.enabled) return '本群停用，不产生新认知、命令回复或公告；历史记录保留。'
  return draft.value.chat?'普通成员可正常互动，命令与公告按下方选项执行。':'普通成员闲聊仅保存；QQ 白名单可正常提问，命令与公告按下方选项执行。'
})
const endpoint = () => `/api/cockpit/scenes/${encodeURIComponent(props.sceneId)}/settings`
async function load() {
  const own = ++requestId
  if (!isGroup.value) return
  loading.value=true; error.value=''
  try {
    const data=await api(endpoint())
    if (own!==requestId) return
    record.value=data; readAt.value=Date.now()/1000
    if (!dirty.value) { draft.value=makeDraft(data.settings); original.value=JSON.stringify(draft.value) }
  } catch(e) { if (own===requestId) error.value=e.message }
  finally { if (own===requestId) loading.value=false }
}
function beginConfiguration() {
  draft.value={enabled:false,chat:false,plugins:{}}
}
function setChat(enabled) {
  draft.value.enabled=true
  draft.value.chat=enabled
}
async function save() {
  if (saving.value||!draft.value) return
  const id=props.sceneId
  saving.value=true; error.value=''; message.value=''
  try {
    const values={...draft.value,plugins:Object.fromEntries(Object.entries(draft.value.plugins).map(([id,item])=>[id,
      {...item,config:configValue(item.config,record.value.plugins.find(plugin=>plugin.id===id).scene_config_schema)}]))}
    const data=await api(endpoint(),{method:'PUT',body:JSON.stringify(values)})
    if (id!==props.sceneId) return
    record.value=data; draft.value=makeDraft(data.settings); original.value=JSON.stringify(draft.value)
    message.value=data.message; readAt.value=Date.now()/1000; emit('saved')
  } catch(e) { if (id===props.sceneId) error.value=e.message }
  finally { saving.value=false }
}
watch(()=>props.sceneId,()=>{++requestId;record.value=null;draft.value=null;original.value='null';message.value='';readAt.value=null;load()},{immediate:true})
onBeforeUnmount(()=>{++requestId})
</script>

<template>
  <div class="scene-settings">
    <v-alert v-if="!isGroup" type="info" variant="tonal">分群设置只用于 QQ 群；私聊不使用这些字段。</v-alert>
    <template v-else>
      <div class="settings-heading"><h3>本群设置</h3><v-btn variant="text" :loading="loading" :disabled="saving" @click="load">刷新设置</v-btn></div>
      <p class="muted-copy mb-4">此处保存本群的真实字段，不另建聊天群／播报群模式。全局 Shadow 仍决定是否实际发送。</p>
      <v-alert v-if="error" type="error" variant="tonal" class="mb-4">{{ error }}<div v-if="readAt">上次读取 {{ fmtTime(readAt) }}</div></v-alert>
      <v-alert v-if="message" type="success" variant="tonal" class="mb-4">{{ message }}</v-alert>
      <v-progress-linear v-if="loading" indeterminate class="mb-4" />
      <template v-if="record">
        <v-alert type="info" variant="tonal" class="mb-4">{{ dirty?'未保存草稿：':'' }}{{ draftEffect }}</v-alert>
        <v-btn v-if="!draft" color="primary" variant="tonal" @click="beginConfiguration">为本群填写设置</v-btn>
        <v-form v-if="draft" :disabled="saving" @submit.prevent="save">
          <div class="settings-actions mb-4"><v-btn variant="tonal" :disabled="saving" @click="setChat(true)">填为聊天群</v-btn><v-btn variant="tonal" :disabled="saving" @click="setChat(false)">填为仅播报群</v-btn><span class="muted-copy">只修改启用与聊天字段，仍需保存。</span></div>
          <div class="settings-grid">
            <v-switch v-model="draft.enabled" label="启用本群" color="primary" />
            <v-switch v-model="draft.chat" label="允许普通成员聊天" color="primary" />
          </div>
          <div v-for="plugin in record.plugins" :key="plugin.id" class="plugin-setting">
            <div class="settings-heading"><h4>{{ plugin.name }}</h4><v-btn v-if="!draft.plugins[plugin.id]" variant="tonal" :disabled="!plugin.configured" @click="addPlugin(plugin)">添加本群设置</v-btn></div>
            <p class="muted-copy mb-3">{{ !plugin.configured?'尚未配置全局参数':!plugin.enabled?'全局已停用':'全局已启用' }}</p>
            <template v-if="draft.plugins[plugin.id]">
              <v-switch v-model="draft.plugins[plugin.id].enabled" label="在本群启用此插件" color="primary" />
              <PluginConfigFields v-model="draft.plugins[plugin.id].config" :schema="plugin.scene_config_schema" />
              <v-expansion-panels class="mt-3"><v-expansion-panel title="插件场景参数说明"><v-expansion-panel-text><ResourceViewer title="场景配置 Schema" :content="plugin.scene_config_schema" /></v-expansion-panel-text></v-expansion-panel></v-expansion-panels>
            </template>
          </div>
          <ResourceViewer v-if="record.members.length" class="mt-4" title="已登记成员名称与身份" :content="record.members" />
          <div class="settings-actions mt-5"><v-btn type="submit" color="primary" :loading="saving" :disabled="saving||!dirty">保存本群设置</v-btn><span v-if="dirty" class="muted-copy">有未保存修改；保存会影响本群后续输入与发送资格。</span></div>
        </v-form>
      </template>
    </template>
  </div>
</template>

<style scoped>
.plugin-setting{padding:20px 0;border-top:1px solid rgba(var(--v-border-color),var(--v-border-opacity))}
.settings-heading,.settings-actions{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.settings-actions{justify-content:flex-start}.settings-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.wide{grid-column:1/-1}.muted-copy{font-size:13px;color:rgb(var(--v-theme-on-surface-variant));line-height:1.7}@media(max-width:650px){.settings-grid{grid-template-columns:minmax(0,1fr)}.wide{grid-column:auto}}
</style>
