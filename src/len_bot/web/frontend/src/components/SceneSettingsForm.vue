<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { onBeforeRouteUpdate } from 'vue-router'
import { api, fmtTime } from '../api.js'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'
import PluginConfigFields from './PluginConfigFields.vue'
import ConfigConflictBanner from './ConfigConflictBanner.vue'
import AdvancedSection from './AdvancedSection.vue'
import HelpHint from './HelpHint.vue'
import {blankConfigDraft,configDraft,configValue,draftProblems} from '../lib/pluginConfig.js'

const ATTENTION_HELP = `旁听决定 Bot 隔多久读一次这个群，不决定它一定说话。

窗口是两次主动观察之间的最小间隔，一次观察会带上自上次以来到达的全部消息，所以没有消息会因为运气不好而被跳过，最多只是晚一点被读到。p 已经不是概率，只是开关：0 表示完全不观察这个群，大于 0 表示按间隔观察。冷却是关键词两次触发之间的最小间隔。

被 @、被回复、私聊和明确委托不走这条路，永远立即响应，不受这些参数影响。

读到不等于会说：进入一次判断而已，是否发言由模型决定，也不增加模型预算或关注时长。`

const props = defineProps({sceneId:{type:String,required:true}})
const emit = defineEmits(['saved'])
const record = ref(null), draft = ref(null), original = ref('null')
const sendFile = ref([]), sendFileOriginal = ref('[]'), extraUid = ref('')
const loading = ref(false), saving = ref(false), error = ref(''), message = ref(''), readAt = ref(null)
const pluginProblems = ref({}), baseline = ref(null), conflict = ref(null)
let requestId = 0
const dirty = computed(()=>draft.value!==null&&(JSON.stringify(draft.value)!==original.value||JSON.stringify(sendFile.value)!==sendFileOriginal.value))
const {confirmLeave} = useUnsavedChanges(dirty)
onBeforeRouteUpdate((to,from)=>to.params.sceneId===from.params.sceneId&&to.query.tab===from.query.tab||confirmLeave())
const isGroup = computed(()=>/^group:[1-9]\d*$/.test(props.sceneId))
const pluginById = computed(()=>Object.fromEntries((record.value?.plugins||[]).map(item=>[item.id,item])))
function pluginFact(plugin) {
  if (!plugin?.configured) return '全局参数尚未填写，本群开关保存后也不会装载'
  if (!plugin.enabled) return '全局已停用，本群开关保存后不会生效'
  return '全局已配置并启用；这里决定本群是否使用'
}
const readyToAdd = plugin => Boolean(plugin?.configured)
function makeDraft(settings) {
  if (!settings) return null
  return {
    enabled: settings.enabled, chat: settings.chat, listen: settings.listen,
    semantic_retrieval: settings.semantic_retrieval,
    attention: settings.attention ? {...settings.attention} : null,
    expression: settings.expression ? {...settings.expression} : null,
    plugins: Object.fromEntries(Object.entries(settings.plugins||{}).map(([id,item])=>[id,
      {...item,config:configDraft(item.config,pluginById.value[id]?.scene_config_schema||{})}])),
  }
}
function addPlugin(plugin, enabled=false) {
  draft.value.plugins[plugin.id]={enabled,config:blankConfigDraft(plugin.scene_config_schema)}
}
function setPluginEnabled(plugin, enabled) {
  if (!draft.value.plugins[plugin.id]) addPlugin(plugin, enabled)
  else draft.value.plugins[plugin.id].enabled = enabled
}
// 聊天 / 跟读 / 仅播报是三档，不是两个独立开关；按钮一次填全，避免出现
// 「不聊天也不跟读却以为在跟读」这种看不出来的中间态。
function setMode(mode) {
  draft.value.enabled=true
  draft.value.chat=mode==='chat'
  draft.value.listen=mode==='listen'
}
const draftEffect = computed(()=>{
  if (!draft.value) return record.value?.effect||''
  if (!draft.value.enabled) return '本群停用，不产生新认知、命令回复或公告；历史记录保留。'
  if (draft.value.chat) return '普通成员可正常互动，命令与公告按下方选项执行。'
  if (draft.value.listen) return '本群只跟读：闲聊不回话，但持续总结成历史与记忆；白名单可正常提问，命令与公告照常。'
  return '普通成员闲聊仅保存原话，不总结也不形成记忆；白名单可正常提问，命令与公告照常。'
})
const attentionPreview = computed(()=>{
  const effective = record.value?.attention?.effective
  if (!effective) return null
  const current = draft.value?.attention
  const p = current?.sample_probability ?? effective.sample_probability
  const w = current?.sample_window_seconds ?? effective.sample_window_seconds
  // The probability is a switch now, so the rate is the interval alone.
  return {p, w, density: p && w ? 3600/w : 0}
})
function applyRaise() {
  const raised = record.value?.attention?.raise_two_steps
  if (!raised || !draft.value) return
  draft.value.attention = {
    sample_probability: raised.sample_probability,
    sample_window_seconds: raised.sample_window_seconds,
    keyword_cooldown_seconds: raised.keyword_cooldown_seconds,
  }
}
function inheritAttention() { draft.value.attention = null }
const sticker = computed({
  get: () => draft.value?.expression?.sticker_preference || 'inherit',
  set: value => { draft.value.expression = value==='inherit' ? null : {sticker_preference: value} },
})
const sendChoices = computed(()=>{
  const seen = new Set()
  const items = []
  for (const person of record.value?.participants||[]) {
    seen.add(person.qq_uid)
    items.push({title: `${person.name}（${person.qq_uid}）`, value: person.qq_uid})
  }
  for (const grant of record.value?.send_file_grants||[]) {
    if (!seen.has(grant.principal_id)) items.push({title: grant.principal_id, value: grant.principal_id})
  }
  for (const uid of sendFile.value) {
    if (!seen.has(uid)) items.push({title: uid, value: uid})
  }
  return items
})
function addUid() {
  const uid = extraUid.value.trim()
  if (!/^[1-9]\d*$/.test(uid)) return
  if (!sendFile.value.includes(uid)) sendFile.value = [...sendFile.value, uid]
  extraUid.value = ''
}
const changeList = computed(()=>{
  if (!dirty.value) return []
  const items = []
  if (JSON.stringify(draft.value)!==original.value) items.push('本群启用、聊天、跟读、插件、旁听或表情')
  if (JSON.stringify(sendFile.value)!==sendFileOriginal.value) items.push('本群文件申请者')
  return items
})
async function load() {
  const own = ++requestId
  if (!isGroup.value) return
  loading.value=true; error.value=''
  try {
    const data=await api(`/api/setup/group-quick?scene_id=${encodeURIComponent(props.sceneId)}`)
    if (own!==requestId) return
    record.value=data; readAt.value=Date.now()/1000
    if (!dirty.value) {
      baseline.value={settings:data.settings, send_file_grants:data.send_file_grants}
      draft.value=makeDraft(data.settings); original.value=JSON.stringify(draft.value)
      sendFile.value=(data.send_file_grants||[]).filter(item=>item.enabled).map(item=>item.principal_id)
      sendFileOriginal.value=JSON.stringify(sendFile.value)
      conflict.value=null
    }
  } catch(e) { if (own===requestId) error.value=e.message }
  finally { if (own===requestId) loading.value=false }
}
function beginConfiguration() {
  draft.value={enabled:false,chat:false,listen:false,semantic_retrieval:false,attention:null,expression:null,plugins:{}}
  sendFile.value=[]
}
async function save() {
  if (saving.value||!draft.value) return
  pluginProblems.value=Object.fromEntries(Object.entries(draft.value.plugins).map(([id,item])=>[id,
    draftProblems(pluginById.value[id]?.scene_config_schema||{},item.config,{requirePresent:true})]))
  if (Object.values(pluginProblems.value).some(items=>items.length)) {
    error.value='本群插件参数尚未填写完整，请修正对应字段后保存。'
    return
  }
  const id=props.sceneId
  saving.value=true; error.value=''; message.value=''; conflict.value=null
  try {
    const settings={...draft.value,plugins:Object.fromEntries(Object.entries(draft.value.plugins).map(([pluginId,item])=>[pluginId,
      {...item,config:configValue(item.config,pluginById.value[pluginId]?.scene_config_schema||{})}]))}
    const data=await api('/api/setup/group-quick',{method:'PUT',body:JSON.stringify({
      scene_id:id, baseline:baseline.value, values:{settings, send_file_principals:sendFile.value}})})
    if (id!==props.sceneId) return
    record.value=data; baseline.value={settings:data.settings, send_file_grants:data.send_file_grants}
    draft.value=makeDraft(data.settings); original.value=JSON.stringify(draft.value)
    sendFile.value=(data.send_file_grants||[]).filter(item=>item.enabled).map(item=>item.principal_id)
    sendFileOriginal.value=JSON.stringify(sendFile.value)
    message.value=data.message; readAt.value=Date.now()/1000; emit('saved')
  } catch(e) {
    if (id!==props.sceneId) return
    if (e.status===409) {
      conflict.value=e.details||{message:e.message, path:e.details?.path}
      try {
        const current=await api(`/api/setup/group-quick?scene_id=${encodeURIComponent(id)}`)
        record.value=current
      } catch {}
      error.value='配置已被其他操作修改；草稿未保存。'
    } else error.value=e.message
  }
  finally { saving.value=false }
}
function keepMine() { conflict.value=null; baseline.value={settings:record.value.settings, send_file_grants:record.value.send_file_grants} }
function takeCurrent() {
  baseline.value={settings:record.value.settings, send_file_grants:record.value.send_file_grants}
  draft.value=makeDraft(record.value.settings); original.value=JSON.stringify(draft.value)
  sendFile.value=(record.value.send_file_grants||[]).filter(item=>item.enabled).map(item=>item.principal_id)
  sendFileOriginal.value=JSON.stringify(sendFile.value)
  conflict.value=null
}
watch(()=>props.sceneId,()=>{++requestId;record.value=null;draft.value=null;original.value='null';sendFile.value=[];sendFileOriginal.value='[]';message.value='';pluginProblems.value={};readAt.value=null;conflict.value=null;load()},{immediate:true})
onBeforeUnmount(()=>{++requestId})
</script>

<template>
  <div class="scene-settings">
    <v-alert v-if="!isGroup" type="info" variant="tonal">分群设置只用于 QQ 群；私聊不使用这些字段。</v-alert>
    <template v-else>
      <div class="settings-heading"><div><h3>本群快速配置</h3><p class="muted-copy">加入群后在本页开关能力、旁听和申请者；高级插件字段仍可展开。</p></div><v-btn variant="text" :loading="loading" :disabled="saving" @click="load">刷新设置</v-btn></div>
      <div v-if="record" class="status-row mb-4">
        <v-chip size="small" :color="record.joined?'success':record.joined===false?'warning':'default'">{{ record.joined?'已加入':record.joined===false?'待确认加入':'加入状态未知' }}</v-chip>
        <v-chip size="small" :color="record.configured?'primary':'default'">{{ record.configured?'已保存':'未配置' }}</v-chip>
        <span class="muted-copy">群号 {{ sceneId.replace('group:','') }}</span>
      </div>
      <v-alert v-if="record?.discovery?.error" type="warning" variant="tonal" class="mb-4">群列表读取失败，保留已知列表：{{ record.discovery.error }}<span v-if="record.discovery.sampled_at"> · 样本 {{ fmtTime(record.discovery.sampled_at) }}</span></v-alert>
      <ConfigConflictBanner :conflict="conflict" :current="record?.settings" path-label="本群设置或文件授予" @keep="keepMine" @take="takeCurrent" />
      <v-alert v-if="error" type="error" variant="tonal" class="mb-4">{{ error }}<div v-if="readAt">上次读取 {{ fmtTime(readAt) }}</div></v-alert>
      <v-alert v-if="message" type="success" variant="tonal" class="mb-4">{{ message }}</v-alert>
      <v-progress-linear v-if="loading" indeterminate class="mb-4" />
      <template v-if="record">
        <v-alert type="info" variant="tonal" class="mb-4">{{ dirty?'未保存草稿：':'' }}{{ draftEffect }}</v-alert>
        <v-btn v-if="!draft" color="primary" variant="tonal" @click="beginConfiguration">为本群填写设置</v-btn>
        <v-form v-if="draft" :disabled="saving" @submit.prevent="save">
          <section class="config-block">
            <h4>本群参与</h4>
            <div class="settings-actions mb-3"><v-btn variant="tonal" :disabled="saving" @click="setMode('chat')">填为聊天群</v-btn><v-btn variant="tonal" :disabled="saving" @click="setMode('listen')">填为跟读群</v-btn><v-btn variant="tonal" :disabled="saving" @click="setMode('broadcast')">填为仅播报群</v-btn></div>
            <div class="settings-grid">
              <v-switch v-model="draft.enabled" label="启用本群" color="primary" />
              <v-switch v-model="draft.chat" label="允许普通成员聊天" color="primary" />
              <v-switch v-model="draft.listen" :disabled="draft.chat" label="不聊天时仍跟读本群" color="primary" :hint="draft.chat?'聊天已包含跟读':'不回话，但持续总结成历史与记忆'" persistent-hint />
              <v-switch v-model="draft.semantic_retrieval" label="允许语义检索本群认识" color="primary" />
            </div>
            <p class="muted-copy">开启聊天不会自动开放外发、文件或账号动作。白名单例外：{{ (record.whitelist||[]).join('、') || '未设置' }}。</p>
            <div class="attention-box">
              <div class="settings-heading">
                <h5>旁听<HelpHint :text="ATTENTION_HELP" /></h5>
                <v-chip size="small" :color="draft.attention ? 'warning' : undefined">
                  {{ draft.attention ? '本群已覆盖' : '继承全局' }}
                </v-chip>
              </div>
              <p class="muted-copy">
                观察{{ record.attention.effective.sample_probability ? '开启' : '关闭' }}，间隔
                {{ record.attention.effective.sample_window_seconds }} 秒，冷却
                {{ record.attention.effective.keyword_cooldown_seconds }} 秒 · 最多约
                {{ (record.attention.raise_two_steps.density.current_per_hour||0).toFixed(1) }} 次/小时
              </p>
              <AdvancedSection title="仅本群覆盖旁听参数" note="会让本群偏离统一聊天参数">
                <p class="muted-copy">聊天参数默认全局统一，新群自动继承。只有这个群确实需要不同节奏时才覆盖。</p>
                <div class="settings-actions">
                  <v-btn size="small" color="primary" variant="tonal" @click="applyRaise">旁听 +2 档</v-btn>
                  <v-btn size="small" variant="text" @click="inheritAttention">恢复继承全局</v-btn>
                </div>
                <v-alert v-if="record.attention.raise_two_steps.enables_sampling" type="warning" variant="tonal">当前该群完全不观察，保存 +2 将按间隔开始观察。</v-alert>
                <p v-for="note in record.attention.raise_two_steps.notes" :key="note" class="muted-copy">{{ note }}</p>
                <p v-if="attentionPreview && draft.attention" class="muted-copy">草稿将保存为观察{{ attentionPreview.p ? '开启' : '关闭' }}、间隔 {{ attentionPreview.w }} 秒（最多约 {{ attentionPreview.density.toFixed(1) }} 次/小时）。</p>
              </AdvancedSection>
            </div>
            <v-select v-model="sticker" label="表情倾向" :items="[{title:'继承自然',value:'inherit'},{title:'自然',value:'natural'},{title:'稍多',value:'slightly_more'}]" />
            <p v-if="record.sleep" class="muted-copy">睡眠：{{ record.sleep.configured ? (record.sleep.in_window ? '当前处于全局睡眠窗口' : '当前不在睡眠窗口') : '未配置睡眠' }}。叫醒状态不在本页修改。</p>
          </section>
          <section class="config-block">
            <h4>能力与来源</h4>
            <p class="muted-copy mb-3">每行只改本群是否使用已配置插件。全局缺失条件不会被一个开关补齐。</p>
            <div v-for="card in record.capability_cards" :key="card.id" class="plugin-setting">
              <h5>{{ card.title }}</h5>
              <p v-if="card.id==='files'" class="muted-copy">生成文件走工作空间；上传还需要下方申请者和已核对的平台协议。当前：{{ record.file_delivery.blocked_reason || (record.file_delivery.can_upload_to_target ? '上传条件已具备（仍取决于申请者）' : '尚未具备上传条件') }}</p>
              <p v-if="card.id==='account'" class="muted-copy">账号动作需要独立授予，本页不会因为打开本群而授予点赞/收藏。</p>
              <div v-for="plugin in card.plugins" :key="plugin.id" class="plugin-row">
                <div class="settings-heading">
                  <div>
                    <strong>{{ plugin.name }}</strong>
                    <p class="muted-copy">{{ pluginFact(pluginById[plugin.id]||plugin) }}</p>
                  </div>
                  <v-switch v-if="draft.plugins[plugin.id]" v-model="draft.plugins[plugin.id].enabled" :disabled="!plugin.configured" label="在本群启用" color="primary" hide-details />
                  <v-btn v-else variant="tonal" size="small" :disabled="!readyToAdd(plugin)" @click="setPluginEnabled(plugin,true)">{{ readyToAdd(plugin)?'添加并启用':'需先填全局参数' }}</v-btn>
                </div>
                <template v-if="draft.plugins[plugin.id]">
                  <p v-if="!Object.keys(plugin.scene_config_schema.properties||{}).length" class="muted-copy">没有额外群参数。</p>
                  <v-expansion-panels v-else class="mt-2">
                    <v-expansion-panel title="本群参数">
                      <v-expansion-panel-text>
                        <PluginConfigFields v-model="draft.plugins[plugin.id].config" :schema="plugin.scene_config_schema" :problems="pluginProblems[plugin.id]||[]" />
                      </v-expansion-panel-text>
                    </v-expansion-panel>
                  </v-expansion-panels>
                </template>
              </div>
            </div>
          </section>
          <section class="config-block">
            <h4>申请者与资料范围</h4>
            <v-select v-model="sendFile" :items="sendChoices" label="可申请发送文件的成员" multiple chips closable-chips />
            <div class="settings-actions mt-2"><v-text-field v-model="extraUid" label="追加 QQ 号" inputmode="numeric" hide-details @keyup.enter.prevent="addUid" /><v-btn variant="tonal" @click="addUid">添加</v-btn></div>
            <p class="muted-copy mt-2">不会把本群文件开关翻译成所有人已授权，也不会创建通配主体。兴趣分享和研究仍使用各自授予，本页不代为创建系统研究。</p>
          </section>
          <div class="save-bar">
            <div>
              <p v-if="changeList.length" class="muted-copy">将写入：{{ changeList.join('、') }}。只影响本群；旁听 +2 保存具体数值，不会每次再乘一次。</p>
              <p v-if="record.file_delivery.blocked_reason" class="muted-copy">文件平台：{{ record.file_delivery.blocked_reason }}</p>
            </div>
            <div class="settings-actions"><v-btn variant="text" :disabled="saving||!dirty" @click="takeCurrent">取消本次修改</v-btn><v-btn type="submit" color="primary" :loading="saving" :disabled="saving||!dirty">保存本群全部改动</v-btn></div>
          </div>
        </v-form>
      </template>
    </template>
  </div>
</template>

<style scoped>
.config-block{padding:8px 0 20px;border-top:1px solid rgba(var(--v-border-color),var(--v-border-opacity))}
.plugin-setting{padding:12px 0}
.plugin-row{padding:8px 0}
.settings-heading,.settings-actions{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.settings-actions{justify-content:flex-start}
.settings-heading h5{display:flex;align-items:center;gap:2px}
.settings-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.status-row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.attention-box{margin:12px 0;padding:12px;border:1px solid rgba(var(--v-border-color),var(--v-border-opacity));border-radius:8px}
.save-bar{position:sticky;bottom:0;padding:16px 0 8px;background:rgb(var(--v-theme-surface));display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;border-top:1px solid rgba(var(--v-border-color),var(--v-border-opacity))}
.muted-copy{font-size:13px;color:rgb(var(--v-theme-on-surface-variant));line-height:1.7}
h4{font-size:16px;margin:16px 0 8px}h5{font-size:14px;margin:8px 0}
@media(max-width:650px){.settings-grid{grid-template-columns:minmax(0,1fr)}.save-bar{position:static}}
</style>
