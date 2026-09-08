<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { onBeforeRouteUpdate } from 'vue-router'
import { api, fmtTime } from '../api.js'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'

const props = defineProps({sceneId:{type:String,required:true}})
const emit = defineEmits(['saved'])
const record = ref(null), draft = ref(null), original = ref('null')
const loading = ref(false), saving = ref(false), error = ref(''), message = ref(''), readAt = ref(null)
let requestId = 0
const dirty = computed(()=>draft.value!==null&&JSON.stringify(draft.value)!==original.value)
const {confirmLeave} = useUnsavedChanges(dirty)
onBeforeRouteUpdate((to,from)=>to.params.sceneId===from.params.sceneId&&to.query.tab===from.query.tab||confirmLeave())
const isGroup = computed(()=>/^group:[1-9]\d*$/.test(props.sceneId))
const pluginItems = computed(()=>(record.value?.plugins||[]).map(item=>({value:item.id,title:`${item.name}${!item.configured?' · 未配置':!item.enabled?' · 全局停用':''}`})))
const memberItems = computed(()=>(record.value?.members||[]).map(item=>({value:item.name,title:`${item.name} · 房间 ${item.room_id}`})))
const commandItems = [{title:'今日直播',value:'calendar_today'},{title:'明日直播',value:'calendar_tomorrow'},{title:'本周直播',value:'calendar_week'}]
const announcementItems = [{title:'真实新开播邀请',value:'live_started'}]
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
    if (!dirty.value) { draft.value=data.settings?structuredClone(data.settings):null; original.value=JSON.stringify(draft.value) }
  } catch(e) { if (own===requestId) error.value=e.message }
  finally { if (own===requestId) loading.value=false }
}
function beginConfiguration() {
  draft.value={enabled:false,chat:false,plugins:[],commands:[],announcements:[],live_subscriptions:[],mention_all:false}
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
    const data=await api(endpoint(),{method:'PUT',body:JSON.stringify(draft.value)})
    if (id!==props.sceneId) return
    record.value=data; draft.value=structuredClone(data.settings); original.value=JSON.stringify(draft.value)
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
            <v-select v-model="draft.plugins" :items="pluginItems" label="本群开放插件" multiple chips closable-chips class="wide" hint="还需对应插件全局启用并完成配置。" persistent-hint />
            <v-select v-model="draft.commands" :items="commandItems" label="日程命令" multiple chips closable-chips hint="须开放日程插件；精确命令词在源配置中填写，引用评论保持安静。" persistent-hint />
            <v-select v-model="draft.announcements" :items="announcementItems" label="公告类型" multiple chips closable-chips hint="开播公告须开放直播插件。" persistent-hint />
            <v-select v-model="draft.live_subscriptions" :items="memberItems" label="订阅主播" multiple chips closable-chips class="wide" hint="只从系统设置中已明确身份的成员选择。" persistent-hint />
            <v-switch v-model="draft.mention_all" label="开播公告 @全体" color="primary" class="wide" hint="由 Runtime 添加真实协议提及；账号需具备该群 @全体条件。发送失败不会去掉提及重发。" persistent-hint />
          </div>
          <p v-if="!memberItems.length" class="muted-copy my-4">尚未配置成员。先在系统设置填写成员与真实 B 站身份，再选择订阅。</p>
          <div class="settings-actions mt-5"><v-btn type="submit" color="primary" :loading="saving" :disabled="saving||!dirty">保存本群设置</v-btn><span v-if="dirty" class="muted-copy">有未保存修改；保存会影响本群后续输入与发送资格。</span></div>
        </v-form>
      </template>
    </template>
  </div>
</template>

<style scoped>
.settings-heading,.settings-actions{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.settings-actions{justify-content:flex-start}.settings-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.wide{grid-column:1/-1}.muted-copy{font-size:13px;color:rgb(var(--v-theme-on-surface-variant));line-height:1.7}@media(max-width:650px){.settings-grid{grid-template-columns:minmax(0,1fr)}.wide{grid-column:auto}}
</style>
