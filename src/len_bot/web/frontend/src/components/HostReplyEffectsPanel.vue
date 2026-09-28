<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { api, queryString } from '../api.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'

const props = defineProps({ scene: { type:String, required:true }, timezone: { type:String, required:true } })
const emit = defineEmits(['busy'])
const REACTIONS = [
  ['agree', '认同'], ['continue', '继续话题'], ['correct', '纠正'], ['negative', '否定或反感'],
  ['unrelated', '与 Bot 发言无明确关系'], ['uncertain', '无法判断'],
]
const STATES = [
  ['no_messages', '未观察到群友消息'], ['waiting', '待判断'], ['failed', '判断失败，待显式重做'], ['observing', '仍在观察'],
]
const CHANNELS = {
  direct:'被 @、被回复或私聊', named:'被叫到名字', focus:'对话延续', ambient:'主动插话',
  schedule:'到期安排', task:'任务通知', resume:'重启后恢复', in_turn:'轮中新消息', quiet_notice:'安静时段固定表达', proactive:'主动开话题', plugin:'插件事件',
}
const LABELS = Object.fromEntries([...REACTIONS, ...STATES])
const state = ref(null), items = ref(null), item = ref(null), calls = ref(null), call = ref(null)
const filter = ref('all'), channel = ref('all'), days = ref(7)
const stateLoading = ref(false), itemsLoading = ref(false), itemLoading = ref(false), callsLoading = ref(false), callLoading = ref(false)
const requesting = ref('')
const stateError = ref(''), itemsError = ref(''), itemError = ref(''), callsError = ref(''), callError = ref('')
const actionError = ref(''), actionNotice = ref('')
const beginState = useRequestGuard(() => `${props.scene}\u0000${days.value}`)
const beginItems = useRequestGuard(() => `${props.scene}\u0000${filter.value}\u0000${channel.value}\u0000${days.value}`)
const beginItem = useRequestGuard(() => props.scene)
const beginCalls = useRequestGuard(() => props.scene)
const beginCall = useRequestGuard(() => props.scene)
const beginAction = useRequestGuard(() => props.scene)
const busy = computed(() => Boolean(requesting.value))
watch(busy, value => emit('busy', value), { immediate:true })
onBeforeUnmount(() => emit('busy', false))
const distribution = computed(() => state.value?.distribution || null)
const judged = computed(() => distribution.value ? REACTIONS.reduce((sum, [key]) => sum + distribution.value.states[key], 0) : 0)
function endpoint(suffix = '') { return `/api/host/scenes/${encodeURIComponent(props.scene)}/reply-effects${suffix}` }
function localTime(value) {
  if (value === null || value === undefined) return '—'
  return new Date(value * 1000).toLocaleString('zh-CN', { timeZone:props.timezone, timeZoneName:'short', hour12:false })
}
function channelText(values) { return values.length ? values.map(value => CHANNELS[value] || value).join(' + ') : '没有记录到唤醒通道' }
function callStatus(value) { return { running:'运行中', complete:'已完成', failed:'失败', interrupted:'已中断' }[value] || value }
function errorMessage(error, verb) {
  return error.status >= 400 && error.status < 500 ? `${verb}未被接受：${error.message}`
    : `${verb}结果未确认：${error.message} 请手动重读核对，不会自动重试。`
}
async function readState() {
  const fresh = beginState(); stateLoading.value = true
  try { const value = await api(`${endpoint()}?${queryString({ days:days.value })}`); if (fresh()) { state.value = value; stateError.value = '' } }
  catch (error) { if (fresh()) stateError.value = error.message }
  finally { if (fresh()) stateLoading.value = false }
}
async function readItems(more = false, force = false) {
  if ((!force && itemsLoading.value) || (more && (!items.value || items.value.items.length >= items.value.total))) return
  const offset = more ? items.value.items.length : 0, fresh = beginItems()
  itemsLoading.value = true
  try {
    const page = await api(`${endpoint('/items')}?${queryString({
      state:filter.value === 'all' ? null : filter.value, channel:channel.value === 'all' ? null : channel.value,
      days:days.value, limit:20, offset })}`)
    if (fresh()) { items.value = more ? { ...page, items:[...items.value.items, ...page.items] } : page; itemsError.value = '' }
  } catch (error) { if (fresh()) itemsError.value = error.message }
  finally { if (fresh()) itemsLoading.value = false }
}
async function openItem(id) {
  if (item.value?.id === id) { beginItem(); item.value = null; itemLoading.value = false; return }
  const fresh = beginItem(); itemLoading.value = true; item.value = null; itemError.value = ''
  try { const value = await api(endpoint(`/items/${id}`)); if (fresh()) item.value = value }
  catch (error) { if (fresh()) itemError.value = error.message }
  finally { if (fresh()) itemLoading.value = false }
}
async function readCalls(more = false, force = false) {
  if ((!force && callsLoading.value) || (more && (!calls.value || calls.value.items.length >= calls.value.total))) return
  const offset = more ? calls.value.items.length : 0, fresh = beginCalls()
  callsLoading.value = true
  try {
    const page = await api(`${endpoint('/calls')}?${queryString({ limit:20, offset })}`)
    if (fresh()) { calls.value = more ? { ...page, items:[...calls.value.items, ...page.items] } : page; callsError.value = '' }
  } catch (error) { if (fresh()) callsError.value = error.message }
  finally { if (fresh()) callsLoading.value = false }
}
async function openCall(id) {
  if (call.value?.id === id) { beginCall(); call.value = null; callLoading.value = false; return }
  const fresh = beginCall(); callLoading.value = true; call.value = null; callError.value = ''
  try { const value = await api(endpoint(`/calls/${id}`)); if (fresh()) call.value = value }
  catch (error) { if (fresh()) callError.value = error.message }
  finally { if (fresh()) callLoading.value = false }
}
async function request(action, id = null) {
  if (!state.value?.enabled || busy.value) return
  const fresh = beginAction(); requesting.value = action; actionError.value = ''; actionNotice.value = ''
  try {
    const result = await api(endpoint(action === 'retry' ? `/calls/${id}/retry` : '/request'), { method:'POST' })
    if (!fresh()) return
    state.value = { ...state.value, service_state:result.state }
    actionNotice.value = action === 'retry' ? `已请求重做判断 #${id} 中仍待判断的样本；不代表已完成。`
      : '已请求判断当前已关闭且待判断的样本；不代表模型已返回结果。'
  } catch (error) { if (fresh()) actionError.value = errorMessage(error, '提交判断请求') }
  finally { if (fresh()) requesting.value = '' }
}
function refresh() {
  beginItem(); beginCall(); item.value = null; call.value = null
  readState(); readItems(false, true); readCalls(false, true)
}
watch([filter, channel, days], () => { beginItems(); items.value = null; itemsLoading.value = false; itemsError.value = ''; readItems() })
watch(days, readState)
onMounted(refresh)
</script>

<template>
  <section class="surface effects-panel" aria-labelledby="effects-title">
    <header class="section-heading"><div><h2 id="effects-title">回复效果</h2>
      <p class="muted">Bot 每次真实发出的表达之后，观察本群接下来 5 条群友消息或 3 分钟，由 learner 批量判断反应。分布只是已记录样本上的模型判断，不是因果证据，也不会自动调整人格、提示词或主动策略。</p></div>
      <v-btn variant="outlined" :loading="stateLoading || itemsLoading || callsLoading" :disabled="busy" @click="refresh">手动重读回复效果</v-btn></header>
    <v-alert v-if="stateError" type="error" variant="tonal" role="alert" :title="state?'读取失败 · 保留上次快照':'读取失败'">{{ stateError }}</v-alert>
    <p v-if="stateLoading && !state" role="status">正在读取回复效果…</p>
    <template v-if="state">
      <p><strong>{{ state.enabled ? '当前运行已启用记录与判断' : '当前运行未启用' }}</strong>
        <span v-if="state.delivery === 'simulated'"> · 当前为模拟发送，不会产生样本</span>。
        开启前的发言不会补记；统计只覆盖开启后已记录的样本。</p>
      <v-select v-model="days" label="统计范围" :items="[{title:'最近 1 天',value:1},{title:'最近 7 天',value:7},{title:'最近 30 天',value:30}]" hide-details class="filter" :disabled="busy" />
      <div v-if="distribution" class="distribution">
        <p>样本 {{ distribution.samples }} 个，其中已判断 {{ judged }} 个；只发出一部分的 {{ distribution.partial }} 个；观察期间连接中断过（可能漏收群消息）的 {{ distribution.input_gap }} 个。</p>
        <ul class="bars"><li v-for="[key, label] in [...REACTIONS, ...STATES]" :key="key">
          <button type="button" class="bar-row" :class="{active:filter===key}" :disabled="!distribution.states[key]" @click="filter=key">
            <span>{{ label }}</span><strong>{{ distribution.states[key] }}</strong>
            <span class="bar" :style="{width: distribution.samples ? `${100*distribution.states[key]/distribution.samples}%` : '0'}" aria-hidden="true"></span>
          </button></li></ul>
        <p class="muted">“未观察到群友消息”只表示窗口里没有收到群友发言，不等于没人理或反感；样本少时比例波动很大。</p>
        <details v-if="distribution.channel_sets.length"><summary>按发言时的唤醒通道看样本数</summary>
          <ul><li v-for="entry in distribution.channel_sets" :key="entry.channels.join()">{{ channelText(entry.channels) }}：{{ entry.count }}</li></ul>
          <p class="muted">通道是这次表达发送时本轮实际收到的唤醒原因；混合或重启恢复不硬分主动与被动。</p></details>
      </div>
      <div class="actions"><v-btn color="primary" :loading="requesting==='request'" :disabled="!state.enabled || busy || !state.service_state?.waiting" @click="request('request')">请求判断待判断样本</v-btn></div>
      <p v-if="state.service_state" class="muted">观察中 {{ state.service_state.open }} 个，已关闭待判断 {{ state.service_state.waiting }} 个；工作器{{ state.service_state.running ? '正在运行' : '未运行' }}。
        <template v-if="state.service_state.worker_error">后台已停止：{{ state.service_state.worker_error }}</template></p>
    </template>
    <v-alert v-if="actionError" type="error" variant="tonal" role="alert">{{ actionError }}</v-alert>
    <p v-if="actionNotice" class="success-note" role="status">{{ actionNotice }}</p>

    <div class="subsection"><div class="section-heading"><h3>样本</h3><span class="muted">负面与纠正可单独筛出</span></div>
      <div class="filters"><v-select v-model="filter" label="反应" hide-details class="filter" :disabled="busy"
          :items="[{title:'全部',value:'all'},{title:'纠正与否定',value:'attention'},...[...REACTIONS, ...STATES].map(([value,title])=>({title,value}))]" />
        <v-select v-model="channel" label="唤醒通道包含" hide-details class="filter" :disabled="busy"
          :items="[{title:'全部',value:'all'},...Object.entries(CHANNELS).map(([value,title])=>({title,value}))]" /></div>
      <v-alert v-if="itemsError" type="error" variant="tonal" role="alert" :title="items?'读取失败 · 保留上次列表':'读取失败'">{{ itemsError }}</v-alert>
      <p v-if="itemsLoading && !items" role="status">正在读取样本…</p>
      <p v-if="items && !items.items.length" class="muted">此筛选下没有样本。</p>
      <ul v-if="items?.items.length" class="records"><li v-for="entry in items.items" :key="entry.id" class="record">
        <div class="record-heading"><strong>{{ LABELS[entry.state] || entry.state }}</strong><span>{{ localTime(entry.first_sent_at) }}</span></div>
        <p class="muted">{{ channelText(entry.channels) }}；已确认发出 {{ entry.message_seqs.length }}/{{ entry.planned_parts }} 段{{ entry.partial ? '（只发出一部分）' : '' }}；
          观察到群友消息 {{ entry.observed_seqs === null ? '尚未冻结' : `${entry.observed_seqs.length} 条` }}{{ entry.input_gap ? '；观察期间连接中断过' : '' }}</p>
        <p v-if="entry.reason" class="original-text">判断理由：{{ entry.reason }}</p>
        <v-btn variant="text" :disabled="itemLoading" @click="openItem(entry.id)">{{ item?.id===entry.id ? '收起' : '查看原话' }}</v-btn>
        <div v-if="item?.id===entry.id" class="detail">
          <p v-if="item.turn_id"><RouterLink :to="{name:'host', query:{scene, turn:item.turn_id}}">查看所属轮次</RouterLink></p>
          <p v-else class="muted">这条是安静时段固定表达，没有模型轮次。</p>
          <h4>发言前本群原话</h4>
          <p v-if="!item.before.length" class="muted">之前没有已保存的原话。</p>
          <ol class="lines"><li v-for="line in item.before" :key="line.record" class="original-text">{{ line.rendered }}</li></ol>
          <h4>Bot 已确认发出的原话</h4>
          <ol class="lines"><li v-for="line in item.expression" :key="line.record" class="original-text">{{ line.rendered ?? `原消息位置 ${line.record} 当前不可用` }}</li></ol>
          <h4>观察到的群友原话</h4>
          <p v-if="item.observed_seqs === null" class="muted">观察窗口尚未关闭（截止 {{ localTime(item.deadline) }}）。</p>
          <p v-else-if="!item.followups.length" class="muted">窗口内没有收到群友消息。</p>
          <ol class="lines"><li v-for="line in item.followups" :key="line.record" class="original-text">{{ line.rendered ?? `原消息位置 ${line.record} 当前不可用` }}
            <span class="muted">（宿主收到 {{ localTime(line.received_at) }}）</span></li></ol>
          <p v-if="item.call" class="muted">判断调用 #{{ item.call.id }} · {{ callStatus(item.call.status) }}{{ item.call.error ? `：${item.call.error}` : '' }}</p>
        </div>
      </li></ul>
      <v-alert v-if="itemError" type="error" variant="tonal" role="alert">{{ itemError }}</v-alert>
      <v-btn v-if="items && items.items.length < items.total" variant="outlined" :loading="itemsLoading" :disabled="itemsLoading" @click="readItems(true)">读取更多样本</v-btn>
    </div>

    <div class="subsection"><div class="section-heading"><h3>实际判断调用</h3><span class="muted">失败不自动重试，只能对该批显式重做</span></div>
      <v-alert v-if="callsError" type="error" variant="tonal" role="alert" :title="calls?'读取失败 · 保留上次列表':'读取失败'">{{ callsError }}</v-alert>
      <p v-if="callsLoading && !calls" role="status">正在读取判断调用…</p>
      <p v-if="calls && !calls.items.length" class="muted">此群还没有判断调用。</p>
      <ul v-if="calls?.items.length" class="records"><li v-for="entry in calls.items" :key="entry.id" class="record">
        <div class="record-heading"><strong>#{{ entry.id }} · {{ callStatus(entry.status) }} · {{ entry.effect_ids.length }} 个样本</strong><span>{{ localTime(entry.started) }}</span></div>
        <p class="muted">模型开始 {{ localTime(entry.model_started) }}；费用 {{ entry.model_started===null ? '尚未调用模型' : entry.cost===null ? '未知' : JSON.stringify(entry.cost) }}</p>
        <p v-if="entry.error" class="original-text">{{ entry.error }}</p>
        <div class="actions"><v-btn variant="text" :disabled="callLoading" @click="openCall(entry.id)">{{ call?.id===entry.id ? '收起详情' : '查看真实请求与响应' }}</v-btn>
          <v-btn v-if="['failed','interrupted'].includes(entry.status)" variant="outlined" :loading="requesting==='retry'" :disabled="!state?.enabled || busy" @click="request('retry', entry.id)">重做这一批</v-btn></div>
        <div v-if="call?.id===entry.id" class="detail"><p>实际用量：{{ call.usage===null ? '未知' : JSON.stringify(call.usage) }}；结束 {{ localTime(call.ended) }}</p>
          <details><summary>请求原文</summary><pre>{{ JSON.stringify(call.request,null,2) }}</pre></details>
          <details><summary>原响应</summary><pre>{{ JSON.stringify(call.response,null,2) }}</pre></details></div>
      </li></ul>
      <v-alert v-if="callError" type="error" variant="tonal" role="alert">{{ callError }}</v-alert>
      <v-btn v-if="calls && calls.items.length < calls.total" variant="outlined" :loading="callsLoading" :disabled="callsLoading" @click="readCalls(true)">读取更多调用</v-btn>
    </div>
  </section>
</template>

<style scoped>
.effects-panel{min-width:0;overflow-wrap:anywhere}.section-heading,.record-heading,.actions,.filters{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap}
.section-heading h2{font-size:18px;margin:0 0 8px}.section-heading h3{font-size:16px;margin:0}.section-heading>div{min-width:0;flex:1 1 450px}.actions,.filters{justify-content:flex-start;margin:14px 0}
.subsection{border-top:1px solid var(--line);padding-top:18px;margin-top:22px}.filter{max-width:280px;margin:12px 0}
.bars{list-style:none;padding:0;margin:12px 0;display:grid;gap:6px}.bar-row{position:relative;display:flex;gap:12px;align-items:center;width:100%;min-height:40px;padding:6px 10px;border:1px solid var(--line);border-radius:8px;background:none;color:inherit;text-align:left;overflow:hidden;cursor:pointer}
.bar-row:disabled{cursor:default;opacity:.7}.bar-row.active{border-color:var(--primary)}.bar-row strong{margin-left:auto;z-index:1}.bar-row span:first-child{z-index:1}
.bar{position:absolute;left:0;top:0;bottom:0;background:var(--selected-bg)}
.records{list-style:none;padding:0;margin:14px 0;display:grid;gap:12px}.record{border:1px solid var(--line);border-radius:10px;padding:14px;min-width:0}
.record-heading span{font-size:13px;color:var(--muted)}.lines{padding-left:20px;display:grid;gap:6px}.original-text{white-space:pre-wrap;overflow-wrap:anywhere}
.detail{background:var(--list-heading-bg);border-radius:8px;padding:12px;margin-top:10px}.detail h4{margin:12px 0 6px}
.effects-panel pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:360px;overflow:auto;font:inherit;font-size:13px}
.effects-panel summary{cursor:pointer;min-height:44px}.success-note{border-left:3px solid var(--primary);background:var(--selected-bg);padding:8px 12px}
.effects-panel :deep(.v-btn){min-height:44px}
@media(max-width:600px){.section-heading>.v-btn{width:100%}.filter{max-width:none;width:100%}.record{padding:12px}}
</style>
