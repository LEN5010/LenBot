<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { onBeforeRouteUpdate, useRoute } from 'vue-router'
import { api, fmtTime } from '../api.js'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'
import PluginConfigFields from './PluginConfigFields.vue'
import ConfigConflictBanner from './ConfigConflictBanner.vue'
import AdvancedSection from './AdvancedSection.vue'
import HelpHint from './HelpHint.vue'
import CapabilityCards from './CapabilityCards.vue'
import { withReturn } from '../router/navigation.js'
import {blankConfigDraft,configDraft,configValue,draftProblems,rebasePluginDraft} from '../lib/pluginConfig.js'
import { rebaseConfigDraft } from '../lib/configDraft.js'

const ATTENTION_HELP = `普通周期观察开启时，第一条待观察消息会安排实际截止时间；不需要等下一条消息来唤醒。

原话按条数和文本预算分批提供，未覆盖范围仍保留。间隔只控制普通观察调度，不是回复延迟承诺，也不是总模型调用上限。

真实 @、回复 Bot 和私聊使用短合并等待；短时观察期内第三人的接话也能被读取。名称和关键词有独立机会，冷却只限制提速，不丢弃已获准的周期观察输入。关闭普通周期观察不关闭这些入口。

读到不等于会说。沉默可以结束本次处理并保留有限观察期；无新输入时不调用模型。睡眠、权限、额度和每群单轮执行仍生效。`

const props = defineProps({sceneId:{type:String,required:true}})
const emit = defineEmits(['saved', 'loaded'])
const route = useRoute()
const record = ref(null), draft = ref(null), original = ref('null')
const sendFile = ref([]), sendFileOriginal = ref('[]'), extraUid = ref('')
const loading = ref(false), saving = ref(false), error = ref(''), message = ref(''), readAt = ref(null)
const pluginProblems = ref({}), baseline = ref(null), conflict = ref(null)
const conflictCurrent = ref(null), conflictReadError = ref(''), conflictReadAt = ref(null)
const saveOutcome = ref(null), outcomeRecord = ref(null), outcomeReadAt = ref(null)
const runtimeFacts = ref(null), factsLoading = ref(false), factsError = ref('')
const selection = () => JSON.stringify([props.sceneId, route.name, route.params.sceneId, route.query.tab])
const readGuard = useRequestGuard(selection), factsGuard = useRequestGuard(selection), operationGuard = useRequestGuard(selection)
const focusGuard = useRequestGuard(selection), pluginPanels = ref({}), pluginRows = new Map(), pluginFields = new Map()
const setPluginRef = (collection,id,value) => value ? collection.set(id,value) : collection.delete(id)
const pluginErrors = computed(()=>Object.entries(pluginProblems.value).flatMap(([id,items])=>items.map(item=>({id,...item}))))
async function focusPlugin(id, path) {
  const fresh=focusGuard();pluginPanels.value[id]=0
  await nextTick()
  if(!fresh())return
  pluginRows.get(id)?.scrollIntoView({block:'center'})
  await pluginFields.get(id)?.focus(path)
}
const dirty = computed(()=>draft.value!==null&&(JSON.stringify(draft.value)!==original.value||JSON.stringify(sendFile.value)!==sendFileOriginal.value))
const {confirmLeave} = useUnsavedChanges(dirty)
onBeforeRouteUpdate((to,from)=>to.params.sceneId===from.params.sceneId&&to.query.tab===from.query.tab||confirmLeave())
const isGroup = computed(()=>/^group:[1-9]\d*$/.test(props.sceneId))
const pluginById = computed(()=>Object.fromEntries((record.value?.plugins||[]).map(item=>[item.id,item])))
const pluginSections = computed(() => {
  const seen = new Set(), sections = []
  for (const card of record.value?.capability_cards || []) {
    const plugins = card.plugins.filter(plugin => {
      if (seen.has(plugin.id)) return false
      seen.add(plugin.id); return true
    })
    if (plugins.length || ['files', 'account'].includes(card.id)) sections.push({...card, plugins})
  }
  const others = (record.value?.plugins || []).filter(plugin => !seen.has(plugin.id))
  if (others.length) sections.push({id:'other-plugins', title:'其他已声明插件', plugins:others})
  return sections
})
function sceneSchema(id, source = record.value) {
  const plugin = source?.plugins?.find(item => item.id === id)
  if (!plugin?.scene_config_schema) throw new Error(`当前目录缺少 ${id} 的群参数定义；保留原草稿，不用空参数替代。请先核对插件目录。`)
  return plugin.scene_config_schema
}
function pluginFact(plugin) {
  if (!plugin?.configured) return '全局尚缺保存参数；本群开关不能补齐缺项'
  if (!plugin.enabled) return '全局保存为停用；本群开关不改变全局设置'
  return '全局已保存启用；当前装载与使用资格见运行事实'
}
const readyToAdd = plugin => Boolean(plugin?.configured)
function makeDraft(settings, source = record.value) {
  if (!settings) return null
  return {
    enabled: settings.enabled, chat: settings.chat, listen: settings.listen,
    semantic_retrieval: settings.semantic_retrieval,
    attention: settings.attention ? {...settings.attention} : null,
    expression: settings.expression ? {...settings.expression} : null,
    plugins: Object.fromEntries(Object.entries(settings.plugins||{}).map(([id,item])=>[id,
      {...item,config:configDraft(item.config,sceneSchema(id, source))}])),
  }
}
const filePrincipals = source => (source.send_file_grants||[]).filter(item=>item.enabled).map(item=>item.principal_id)
function adoptRecord(source) {
  const next = makeDraft(source.settings, source), principals = filePrincipals(source)
  baseline.value={settings:source.settings,send_file_grants:source.send_file_grants}
  draft.value=next;original.value=JSON.stringify(next)
  sendFile.value=principals;sendFileOriginal.value=JSON.stringify(principals)
  extraUid.value='';pluginProblems.value={}
}
function clearConflict() { conflict.value=null;conflictCurrent.value=null;conflictReadError.value='';conflictReadAt.value=null }
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
  const global = record.value?.attention?.global
  if (!global) return null
  const current = draft.value?.attention
  const p = current?.observation_enabled ?? global.observation_enabled
  const w = current?.observation_interval_seconds ?? global.observation_interval_seconds
  // This is only a periodic scheduling frequency, not a model-call budget.
  return {p, w, density: p && w ? 3600/w : 0}
})
function applyRaise() {
  const raised = record.value?.attention?.raise_two_steps
  if (!raised || !draft.value) return
  draft.value.attention = {
    observation_enabled: raised.observation_enabled,
    observation_interval_seconds: raised.observation_interval_seconds,
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
async function load({ accept = () => true } = {}) {
  const current = readGuard(), fresh = () => current() && accept(), id = props.sceneId
  if (!isGroup.value) return
  loading.value=true; error.value=''
  if(conflict.value){conflictCurrent.value=null;conflictReadError.value='';conflictReadAt.value=null}
  if(saveOutcome.value){outcomeRecord.value=null;outcomeReadAt.value=null}
  try {
    const data=await api(`/api/setup/group-quick?scene_id=${encodeURIComponent(id)}`)
    if (!fresh()) return
    if(data.scene_id!==id)throw new Error('返回的本群设置身份与读取目标不一致，未采用。')
    record.value=data; readAt.value=Date.now()/1000
    if(conflict.value){conflictCurrent.value=data;conflictReadAt.value=readAt.value}
    else if(saveOutcome.value){outcomeRecord.value=data;outcomeReadAt.value=readAt.value}
    else if (!dirty.value) adoptRecord(data)
  } catch(e) { if(fresh()){error.value=e.message;if(conflict.value)conflictReadError.value=e.message} }
  finally { if (fresh()) { loading.value=false; emit('loaded', {sceneId:id,ok:!error.value}) } }
}
async function loadRuntimeFacts({ accept = () => true } = {}) {
  const current = factsGuard(), fresh = () => current() && accept(), id = props.sceneId
  if (!isGroup.value) return
  factsLoading.value = true; factsError.value = ''
  try {
    const data = await api(`/api/overview/capabilities?scene_id=${encodeURIComponent(id)}`)
    if (!fresh()) return
    if(data.scene_id!==id)throw new Error('返回的能力事实不属于本群，未采用。')
    runtimeFacts.value = data
  } catch (problem) { if (fresh()) factsError.value = problem.message }
  finally { if (fresh()) factsLoading.value = false }
}
function refresh() { if(!saving.value)return Promise.all([load(), loadRuntimeFacts()]) }
function beginConfiguration() {
  if(saving.value||saveOutcome.value||record.value?.settings!==null)return
  draft.value={enabled:false,chat:false,listen:false,semantic_retrieval:false,attention:null,expression:null,plugins:{}}
}
async function save() {
  if (saving.value||saveOutcome.value||conflict.value||!draft.value) return
  const id=props.sceneId, fresh=operationGuard()
  readGuard();loading.value=false;factsGuard();factsLoading.value=false
  saving.value=true; error.value=''; message.value=''; clearConflict()
  let submitted=false, confirmed=false
  try {
    pluginProblems.value=Object.fromEntries(Object.entries(draft.value.plugins).map(([pluginId,item])=>[pluginId,
      draftProblems(sceneSchema(pluginId),item.config,{requirePresent:true})]))
    if (Object.values(pluginProblems.value).some(items=>items.length)) {
      error.value='本群插件参数尚未填写完整，请修正对应字段后保存。'
      return
    }
    const settings={...draft.value,plugins:Object.fromEntries(Object.entries(draft.value.plugins).map(([pluginId,item])=>[pluginId,
      {...item,config:configValue(item.config,sceneSchema(pluginId))}]))}
    submitted=true
    const data=await api('/api/setup/group-quick',{method:'PUT',body:JSON.stringify({
      scene_id:id, baseline:baseline.value, values:{settings,
        ...(JSON.stringify(sendFile.value)!==sendFileOriginal.value?{send_file_principals:sendFile.value}:{})}})})
    if (!fresh()) return
    if(data.scene_id!==id||data.config_saved!==true)throw new Error('保存响应缺少本群写入确认或群身份与提交目标不一致；结果须回原群核对，未采用返回草稿。')
    confirmed=true
    adoptRecord(data);record.value=data
    message.value=data.message; readAt.value=Date.now()/1000; emit('saved')
    await loadRuntimeFacts({accept:fresh})
  } catch(e) {
    if (!fresh()) return
    if (e.status===409 && e.details?.config_saved===false) {
      conflict.value={message:e.message,path:e.details.path}
      await load({accept:fresh})
    } else {
      const saved=e.details?.scene_id===id&&e.details?.config_saved===true
      const rejected=e.details?.config_saved===false || (e.status===422&&Array.isArray(e.details))
      if(submitted&&!rejected){
        saveOutcome.value=confirmed?'readback':saved&&['apply','readback'].includes(e.details.stage)?e.details.stage:'unknown'
        outcomeRecord.value=null;outcomeReadAt.value=null
        await load({accept:fresh})
        if(!fresh())return
      }
      error.value=e.message+(error.value?'；读取当前值：'+error.value:'')
    }
  }
  finally { if(fresh())saving.value=false }
}
function keepMine() {
  if(saving.value||loading.value||saveOutcome.value||conflictCurrent.value?.scene_id!==props.sceneId)return
  try {
    const source=conflictCurrent.value, savedDraft=makeDraft(source.settings,source), principals=filePrincipals(source)
    const previous=JSON.parse(original.value), next=rebaseConfigDraft(previous,draft.value,savedDraft)
    for(const id of Object.keys(next?.plugins||{})){
      if(previous?.plugins?.[id]&&draft.value?.plugins?.[id]&&savedDraft?.plugins?.[id]){
        next.plugins[id].config=rebasePluginDraft(previous.plugins[id].config,draft.value.plugins[id].config,savedDraft.plugins[id].config,sceneSchema(id,source))
      }
    }
    const nextPrincipals=rebaseConfigDraft(JSON.parse(sendFileOriginal.value),sendFile.value,principals)
    record.value=source;adoptRecord(source)
    draft.value=next;sendFile.value=nextPrincipals
    clearConflict();error.value='';message.value='已保留实际改过的字段，未改字段采用当前值；请核对草稿后保存本群设置。'
  }catch(e){error.value=e.message}
}
function takeCurrent() {
  if(saving.value||loading.value)return
  const source=saveOutcome.value?outcomeRecord.value:conflict.value?conflictCurrent.value:record.value
  if(source?.scene_id!==props.sceneId)return
  if((dirty.value||saveOutcome.value)&&!window.confirm(saveOutcome.value?'放弃原本群设置及文件申请者草稿，采用本次读取值继续编辑？这不重发保存、不重试运行应用，也不证明旧请求的结果。':'放弃本页未保存的修改，采用已读取的本群保存值？'))return
  try{record.value=source;adoptRecord(source);clearConflict();saveOutcome.value=null;outcomeRecord.value=null;outcomeReadAt.value=null;error.value='';message.value='已采用本次读取的保存值，没有再次保存、重试运行应用或启用。'}catch(e){error.value=e.message}
}
watch(()=>props.sceneId,()=>{
  readGuard();factsGuard();operationGuard();focusGuard();saving.value=false;loading.value=false
  record.value=null;draft.value=null;baseline.value=null;original.value='null';sendFile.value=[];sendFileOriginal.value='[]';extraUid.value=''
  error.value='';message.value='';pluginProblems.value={};pluginPanels.value={};readAt.value=null;clearConflict()
  runtimeFacts.value=null;factsError.value='';factsLoading.value=false
  saveOutcome.value=null;outcomeRecord.value=null;outcomeReadAt.value=null;refresh()
},{immediate:true,flush:'sync'})
</script>

<template>
  <div class="scene-settings">
    <v-alert v-if="!isGroup" type="info" variant="tonal">分群设置只用于 QQ 群；私聊不使用这些字段。</v-alert>
    <template v-else>
      <div class="settings-heading"><div><h3>本群设置</h3><p class="muted-copy">编辑只改变草稿；保存本群规则不会自动执行工具、重启或发送消息。</p></div><v-btn variant="text" :loading="loading||factsLoading" :disabled="saving" @click="refresh">刷新设置与事实</v-btn></div>
      <div v-if="record" class="status-row mb-4">
        <v-chip size="small" :color="record.joined?'success':record.joined===false?'warning':'default'">{{ record.joined?'已加入':record.joined===false?'待确认加入':'加入状态未知' }}</v-chip>
        <v-chip size="small" :color="record.configured?'primary':'default'">{{ record.configured?'已保存':'未配置' }}</v-chip>
        <v-chip v-if="dirty" size="small" color="warning">有未保存草稿</v-chip>
        <span class="muted-copy">群号 {{ sceneId.replace('group:','') }}</span>
      </div>
      <v-alert v-if="record?.discovery?.error" type="warning" variant="tonal" class="mb-4">群列表读取失败，保留已知列表：{{ record.discovery.error }}<span v-if="record.discovery.sampled_at"> · 样本 {{ fmtTime(record.discovery.sampled_at) }}</span></v-alert>
      <ConfigConflictBanner exclusive-backend :conflict="conflict" :current="conflictCurrent" :path-label="conflict?.path?.join('.') || '本群设置或文件授予'" :busy="saving||loading||!!saveOutcome" :read-error="conflictReadError" :read-at="conflictReadAt" @keep="keepMine" @take="takeCurrent" @reload="refresh" />
      <v-alert v-if="error" type="error" variant="tonal" class="mb-4">{{ error }}<div v-if="readAt">上次读取 {{ fmtTime(readAt) }}</div></v-alert>
      <v-alert v-if="pluginErrors.length" type="error" variant="tonal" class="mb-4"><p>点击具体字段定位并修正；修正前没有提交保存。</p><ul class="field-errors"><li v-for="item in pluginErrors" :key="item.id+item.key"><button type="button" :disabled="saving" @click="focusPlugin(item.id,item.key)">{{ pluginById[item.id]?.name || item.id }} · {{ item.message }}</button></li></ul></v-alert>
      <v-alert v-if="message" type="success" variant="tonal" class="mb-4">{{ message }}</v-alert>
      <v-alert v-if="saveOutcome" type="warning" variant="tonal" class="mb-4">
        <p>{{ saveOutcome==='apply'?'本群设置已写入，但运行应用未完成。':saveOutcome==='readback'?'本群设置已写入并完成运行应用，但保存值未能采用。':'本次保存结果未知。' }}请先核对，不重复提交原草稿；读取当前值不恢复失败步骤，也不追认旧请求。</p>
        <p v-if="outcomeRecord">已读取本群 {{ fmtTime(outcomeReadAt) }} 的保存值，可明确采用后继续编辑。当前运行事实仍在下方独立显示。</p>
        <v-btn variant="text" :disabled="saving||loading" @click="refresh">读取当前保存值</v-btn>
        <v-btn variant="text" :disabled="saving||loading||!outcomeRecord" @click="takeCurrent">采用当前值继续编辑</v-btn>
      </v-alert>
      <v-progress-linear v-if="loading" indeterminate class="mb-4" />
      <v-expansion-panels class="mb-4"><v-expansion-panel title="当前运行、能力缺项与最近结果"><v-expansion-panel-text>
        <p class="muted-copy mb-3">下面是独立只读事实，不使用当前草稿推算。开关、装载、部署、申请者资格和实际结果分别显示。</p>
        <v-progress-linear v-if="factsLoading" indeterminate aria-label="正在读取能力事实" />
        <v-alert v-if="factsError" type="error" variant="tonal" class="my-3">运行事实读取失败：{{ factsError }}<p v-if="runtimeFacts">保留 {{ fmtTime(runtimeFacts.sampled_at) }} 的记录。</p></v-alert>
        <template v-if="runtimeFacts"><p class="muted-copy mb-3">采样于 {{ fmtTime(runtimeFacts.sampled_at) }} · {{ runtimeFacts.evidence_note }}</p><v-alert v-if="runtimeFacts.requires_restart" type="info" variant="tonal" class="mb-3">另有已保存配置等待手动重启；各插件当前装载情况如下，保存成功不代表全部运行条件已满足。</v-alert><CapabilityCards :data="runtimeFacts" /></template>
        <v-btn variant="text" class="mt-3" :to="withReturn(route,{name:'capabilities',query:{scene:sceneId}})">选择本群发起者，核对其使用资格</v-btn>
      </v-expansion-panel-text></v-expansion-panel></v-expansion-panels>
      <template v-if="record">
        <v-alert type="info" variant="tonal" class="mb-4">{{ dirty?'未保存草稿：':'已保存规则：' }}{{ draftEffect }}</v-alert>
        <v-btn v-if="!draft&&record.settings===null" color="primary" variant="tonal" :disabled="saving||loading||!!saveOutcome" @click="beginConfiguration">为本群填写设置</v-btn>
        <v-form v-if="draft" :disabled="saving||!!saveOutcome" @submit.prevent="save">
          <section class="config-block">
            <h4>本群参与</h4>
            <div class="settings-actions mb-3"><v-btn variant="tonal" :disabled="saving||!!saveOutcome" @click="setMode('chat')">填为聊天群</v-btn><v-btn variant="tonal" :disabled="saving||!!saveOutcome" @click="setMode('listen')">填为跟读群</v-btn><v-btn variant="tonal" :disabled="saving||!!saveOutcome" @click="setMode('broadcast')">填为仅播报群</v-btn></div>
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
                已保存规则下，普通周期观察{{ record.attention.effective.observation_enabled ? '开启' : '关闭' }}，间隔
                {{ record.attention.effective.observation_interval_seconds }} 秒，冷却
                {{ record.attention.effective.keyword_cooldown_seconds }} 秒 · 间隔折算约
                {{ (record.attention.raise_two_steps.density.current_per_hour||0).toFixed(1) }} 次/小时
              </p>
              <AdvancedSection title="仅本群覆盖旁听参数" note="会让本群偏离统一聊天参数">
                <p class="muted-copy">聊天参数默认全局统一，新群自动继承。只有这个群确实需要不同节奏时才覆盖。</p>
                <div class="settings-actions">
                  <v-btn size="small" color="primary" variant="tonal" :disabled="saving||!!saveOutcome" @click="applyRaise">按已保存值开启并将观察间隔减半</v-btn>
                  <v-btn size="small" variant="text" :disabled="saving||!!saveOutcome" @click="inheritAttention">恢复继承全局</v-btn>
                </div>
                <div v-if="draft.attention" class="form-grid mt-3">
                  <v-select v-model="draft.attention.observation_enabled" label="本群普通周期观察" :items="[{title:'继承全局',value:null},{title:'开启',value:true},{title:'关闭',value:false}]" />
                  <v-text-field v-model.number="draft.attention.observation_interval_seconds" type="number" min="0.1" step="0.1" label="本群观察间隔（秒）" />
                  <v-text-field v-model.number="draft.attention.keyword_cooldown_seconds" type="number" min="0" step="1" label="名称与关键词提速冷却（秒）" />
                </div>
                <v-alert v-if="record.attention.raise_two_steps.enables_observation" type="warning" variant="tonal">当前普通周期观察关闭，保存 +2 将开启。真实搭话和短时观察独立生效。</v-alert>
                <p v-for="note in record.attention.raise_two_steps.notes" :key="note" class="muted-copy">{{ note }}</p>
                <p v-if="attentionPreview && draft.attention" class="muted-copy">草稿将保存为普通周期观察{{ attentionPreview.p ? '开启' : '关闭' }}、间隔 {{ attentionPreview.w }} 秒（间隔折算约 {{ attentionPreview.density.toFixed(1) }} 次/小时，不是调用上限）。</p>
              </AdvancedSection>
            </div>
            <p v-if="record.allowance" class="muted-copy">
              本群这一小时已发 {{ record.allowance.scene_used }} 条{{ record.allowance.scene_limit ? '（上限 ' + record.allowance.scene_limit + '）' : '（不限）' }}{{ record.allowance.scene_exhausted ? ' · 已达上限，闲聊与主动分享暂不进入模型，不会自动发提示；日程命令与直播推送不受影响' : '' }}。单人上限 {{ record.allowance.user_limit || '不限' }} 条/小时，按实际送达滚动计算。
            </p>
            <v-select v-model="sticker" label="表情倾向" :items="[{title:'继承自然',value:'inherit'},{title:'自然',value:'natural'},{title:'稍多',value:'slightly_more'}]" />
            <p v-if="record.sleep" class="muted-copy">睡眠：{{ record.sleep.configured ? (record.sleep.in_window ? '当前处于全局睡眠窗口' : '当前不在睡眠窗口') : '未配置睡眠' }}。叫醒状态不在本页修改。</p>
          </section>
          <section class="config-block">
            <h4>能力与来源</h4>
            <p class="muted-copy mb-3">每行只改本群是否使用已配置插件。全局缺失条件不会被一个开关补齐。</p>
            <div v-for="card in pluginSections" :key="card.id" class="plugin-setting">
              <h5>{{ card.title }}</h5>
              <p v-if="card.id==='files'" class="muted-copy">生成文件走工作空间；上传还需要下方申请者和已核对的平台协议。当前：{{ record.file_delivery.blocked_reason || (record.file_delivery.can_upload_to_target ? '上传条件已具备（仍取决于申请者）' : '尚未具备上传条件') }}</p>
              <p v-if="card.id==='account'" class="muted-copy">账号动作需要独立授予，本页不会因为打开本群而授予点赞/收藏。</p>
              <div v-for="plugin in card.plugins" :key="plugin.id" :ref="node=>setPluginRef(pluginRows,plugin.id,node)" class="plugin-row">
                <div class="settings-heading">
                  <div>
                    <strong>{{ plugin.name }}</strong>
                    <p class="muted-copy">{{ pluginFact(pluginById[plugin.id]||plugin) }}</p>
                  </div>
                  <v-switch v-if="draft.plugins[plugin.id]" v-model="draft.plugins[plugin.id].enabled" :disabled="saving||!!saveOutcome||(!plugin.configured&&!draft.plugins[plugin.id].enabled)" label="在本群启用" color="primary" hide-details />
                  <v-btn v-else variant="tonal" size="small" :disabled="saving||!!saveOutcome||!readyToAdd(plugin)" @click="setPluginEnabled(plugin,true)">{{ readyToAdd(plugin)?'添加到本群草稿并启用':plugin.missing?'当前目录无此实现':'需先填全局参数' }}</v-btn>
                </div>
                <RouterLink v-if="pluginById[plugin.id]" :to="withReturn(route,{name:'plugins',query:{id:plugin.id}})">查看 {{ plugin.name }} 的全局配置与装载状态</RouterLink>
                <template v-if="draft.plugins[plugin.id]">
                  <p v-if="!Object.keys(plugin.scene_config_schema.properties||{}).length" class="muted-copy">没有额外群参数。</p>
                  <v-expansion-panels v-else v-model="pluginPanels[plugin.id]" class="mt-2">
                    <v-expansion-panel title="本群参数">
                      <v-expansion-panel-text>
                        <PluginConfigFields :ref="node=>setPluginRef(pluginFields,plugin.id,node)" :disabled="saving||!!saveOutcome" v-model="draft.plugins[plugin.id].config" :schema="plugin.scene_config_schema" :problems="pluginProblems[plugin.id]||[]" />
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
            <div class="settings-actions mt-2"><v-text-field v-model="extraUid" label="追加 QQ 号" inputmode="numeric" hide-details @keyup.enter.prevent="addUid" /><v-btn variant="tonal" :disabled="saving||!!saveOutcome" @click="addUid">添加</v-btn></div>
            <p class="muted-copy mt-2">不会把本群文件开关翻译成所有人已授权，也不会创建通配主体。兴趣分享和研究仍使用各自授予，本页不代为创建系统研究。</p>
          </section>
          <div class="save-bar">
            <div>
              <p v-if="changeList.length" class="muted-copy">将写入：{{ changeList.join('、') }}。只影响本群；旁听 +2 保存具体数值，不会每次再乘一次。</p>
              <p v-if="record.file_delivery.blocked_reason" class="muted-copy">文件平台：{{ record.file_delivery.blocked_reason }}</p>
            </div>
            <div class="settings-actions"><v-btn variant="text" :disabled="saving||!!saveOutcome||loading||!dirty||(!!conflict&&!conflictCurrent)" @click="takeCurrent">取消本次修改</v-btn><v-btn type="submit" color="primary" :loading="saving" :disabled="saving||!!saveOutcome||!!conflict||!dirty">保存本群设置</v-btn></div>
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
.field-errors{padding-left:20px;margin-top:8px}.field-errors button{text-align:left;text-decoration:underline;overflow-wrap:anywhere;padding:4px 0}.field-errors button:focus-visible{outline:2px solid currentColor;outline-offset:3px}
h4{font-size:16px;margin:16px 0 8px}h5{font-size:14px;margin:8px 0}
@media(max-width:650px){.settings-grid{grid-template-columns:minmax(0,1fr)}.save-bar{position:static}}
</style>
