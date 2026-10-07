<script setup>
import { computed, reactive, ref } from 'vue'
import { mdiMessageTextOutline, mdiSendOutline, mdiChatProcessingOutline, mdiAlarm, mdiCounter, mdiRefresh, mdiLanConnect,
  mdiLanDisconnect, mdiCheckCircleOutline, mdiArrowRight, mdiMagnify } from '@mdi/js'
import { api, sceneName, sceneNumber } from '../../../api.js'
import { useAction } from '../../../composables/useResource.js'
import { confirm } from '../../../composables/useConfirm.js'
import { useHostEvents } from '../../events.js'
import { host, readHostState, readOverview } from '../../store.js'
import { attentionItems, connectionLabel } from '../../attention.js'
import { formatAgo } from '../../time.js'
import HostPage from '../../ui/HostPage.vue'
import Panel from '../../ui/Panel.vue'
import ErrorNote from '../../ui/ErrorNote.vue'
import LiveStatus from '../../ui/LiveStatus.vue'
import StatGrid from '../../ui/StatGrid.vue'
import StatusBadge from '../../ui/StatusBadge.vue'
import SceneAvatar from '../../ui/SceneAvatar.vue'
import markUrl from '../../../assets/lenbot-mark-tile.svg'
import EmptyState from '../../ui/EmptyState.vue'
import Fold from '../../ui/Fold.vue'
import DevOnly from '../../ui/DevOnly.vue'

const day = computed(() => host.overview)
const state = computed(() => host.state)
const connect = useAction()
const events = useHostEvents(() => Promise.all([readHostState(), readOverview()]))
const online = computed(() => state.value?.connection.connected && state.value.connection.accepting)
const loading = ref(false)

async function refresh() {
  loading.value = true
  try { await Promise.all([readHostState(), readOverview()]) } finally { loading.value = false }
}

async function connectQQ() {
  await connect.run(() => api('/api/host/connection/connect', { method: 'POST' }))
  await readHostState()
}

const disconnect = useAction()
async function disconnectQQ() {
  if (!await confirm({ title: '断开 QQ 连接？', danger: true, confirmLabel: '立即断开',
    text: 'Bot 马上停止收发消息，正在进行的回复和任务会中断。要重新连上，需要重启 LenBot。' })) return
  await disconnect.run(() => api('/api/host/connection/disconnect', { method: 'POST' }))
  await readHostState()
}

const switching = reactive({})
const switchError = useAction()
async function setChat(scene, enabled) {
  switching[scene] = true
  try {
    await switchError.run(() => api(`/api/host/scenes/${encodeURIComponent(scene)}/control/chat`,
      { method: 'PUT', body: JSON.stringify({ enabled }) }))
    await readHostState()
  } finally {
    switching[scene] = false
  }
}
const search = ref('')
const scenes = computed(() => {
  const words = search.value.trim().toLowerCase()
  return (state.value?.scenes || []).filter(item => !words
    || sceneName(item.scene).toLowerCase().includes(words) || sceneNumber(item.scene).includes(words))
})

const effectLabels = { agree: '认同', continue: '接着聊', correct: '纠正', negative: '反感', unrelated: '没接话' }
const count = values => Object.values(values || {}).reduce((sum, value) => sum + value, 0)
const number = value => value.toLocaleString('zh-CN')
const effects = computed(() => Object.entries(effectLabels)
  .filter(([key]) => day.value?.reply_effects[key]).map(([key, label]) => `${label} ${day.value.reply_effects[key]}`))
const todo = computed(() => attentionItems(state.value, day.value))

const stats = computed(() => {
  const hourly = day.value.hourly
  const tokens = day.value.tokens
  return [
    { label: '收到消息', value: number(day.value.messages.received || 0), icon: mdiMessageTextOutline,
      series: hourly.received, seriesLabel: '近 24 小时每小时收到的消息' },
    { label: 'Bot 发言', value: number((day.value.messages.sent || 0) + (day.value.messages.simulated || 0)), icon: mdiSendOutline,
      series: hourly.spoke, seriesLabel: '近 24 小时每小时 Bot 发言' },
    { label: '回复轮次', value: number(count(day.value.turns)), icon: mdiChatProcessingOutline, to: { name: 'host-logs' },
      series: hourly.turns, seriesLabel: '近 24 小时每小时回复轮次' },
    { label: '待执行提醒', value: number(day.value.pending_schedules), icon: mdiAlarm, to: { name: 'host-tasks', query: { tab: 'schedules' } },
      hint: '所有群合计' },
    { label: '今日 token', value: number(tokens.input + tokens.output), icon: mdiCounter, to: { name: 'host-models', query: { tab: 'usage' } },
      hint: day.value.unknown_token_calls ? `另有 ${day.value.unknown_token_calls} 次调用没有报告 token` : `输入 ${number(tokens.input)} · 输出 ${number(tokens.output)}`,
      series: hourly.tokens, seriesLabel: '近 24 小时每小时 token' },
  ]
})

const activityText = {
  turn: item => item.status === 'manual_compaction' ? `${sceneName(item.scene)} 压缩了一次上下文` : `${sceneName(item.scene)} 回复了一轮`,
  task: item => `${sceneName(item.scene)} 的任务结束：${item.goal}`,
  send: item => `${sceneName(item.scene)} 有一条消息${item.status === 'failed' ? '没发出去' : '不确定是否发出'}`,
}
const activityStatus = { turn: 'turn', task: 'task', send: 'message' }
const activityLink = item => item.kind === 'turn' ? { name: 'host-logs', query: { scene: item.scene, turn: item.id } }
  : item.kind === 'task' ? { name: 'host-tasks', query: { scene: item.scene } }
    : { name: 'host-scenes', query: { scene: item.scene } }

const connectionNote = computed(() => state.value.connection.status === 'stopped' && state.value.connection.disconnect_requested
  ? connectionNotes.disconnected : connectionNotes[state.value.connection.status])
const connectionNotes = {
  connection_failed: 'QQ 连接失败。确认 OneBot 已经启动、地址和令牌正确，然后手动连接。',
  failed: 'Bot 已停止工作。按下面的错误处理好原因后，重新启动 LenBot。',
  stopped: 'Bot 已停止工作。按下面的错误处理好原因后，重新启动 LenBot。',
  disconnected: '已手动断开 QQ。要重新连上，在右上角账号菜单里重启。',
  waiting_connection: '正在等待 OneBot 连上来。',
}
</script>

<template>
  <HostPage title="首页">
    <template #actions><v-btn variant="outlined" :prepend-icon="mdiRefresh" :loading="loading" @click="refresh">刷新</v-btn></template>
    <ErrorNote v-if="host.stateError" title="读取运行状态失败" :error="host.stateError" @retry="readHostState" />
    <ErrorNote v-if="host.overviewError" title="读取今日统计失败" :error="host.overviewError" @retry="readOverview" />

    <section v-if="state" class="status-card">
      <div class="status-main">
        <span class="status-mark" :class="{ ok: online }"><img :src="markUrl" alt="" /><span class="status-dot" /></span>
        <div class="status-text">
          <h2>{{ online ? 'Bot 在线' : connectionLabel(state.connection) }}</h2>
          <p>{{ state.bot_id }} · {{ state.delivery === 'onebot' ? '真实发送到 QQ' : '模拟发送，不会发到 QQ' }} · {{ state.scenes.length }} 个群聊</p>
          <LiveStatus :status="events.status.value" @reconnect="events.reconnect" />
        </div>
      </div>
      <div class="status-actions">
        <v-btn v-if="state.connection.can_connect || connect.busy.value" color="primary" :prepend-icon="mdiLanConnect" :loading="connect.busy.value" @click="connectQQ">手动连接 QQ</v-btn>
        <v-btn v-if="state.connection.can_disconnect || disconnect.busy.value" variant="outlined" class="text-error" :prepend-icon="mdiLanDisconnect" :loading="disconnect.busy.value" @click="disconnectQQ">断开 QQ</v-btn>
        <v-btn :to="{ name: 'host-system', query: { tab: 'connection' } }" variant="text">连接设置</v-btn>
      </div>
      <p v-if="connectionNote" class="status-note">{{ connectionNote }}</p>
    </section>
    <ErrorNote v-if="connect.error.value" title="手动连接没有成功" :error="connect.error.value" />
    <ErrorNote v-if="disconnect.error.value" title="断开 QQ 没有成功" :error="disconnect.error.value" />
    <ErrorNote v-if="state?.connection.last_error" title="最近一次连接或运行失败" :error="state.connection.last_error" />

    <StatGrid v-if="day" :items="stats" />
    <p v-if="day" class="muted small trend-note">数字是今天的合计，下方小图是最近 24 小时每小时的变化。</p>

    <div class="columns">
        <Panel title="需要处理的事" flush class="area-todo">
          <template #actions><span v-if="todo.length" class="todo-count">{{ todo.length }}</span></template>
          <p v-if="!state || !day" class="muted pad">读取中…</p>
          <div v-else-if="!todo.length" class="all-good pad"><v-icon :icon="mdiCheckCircleOutline" size="20" />一切正常，没有需要处理的事</div>
          <ul v-else class="plain-list rows">
            <li v-for="item in todo" :key="item.key" class="row">
              <div class="row-text"><span>{{ item.text }}</span>
                <Fold v-if="item.error" label="错误原文" code>{{ item.error }}</Fold></div>
              <v-btn :to="item.to" size="small" variant="outlined">{{ item.action }}</v-btn>
            </li>
          </ul>
          <template v-if="effects.length" #footer><span class="muted small">群友对 Bot 发言的反应：{{ effects.join(' · ') }}</span></template>
        </Panel>

        <Panel title="最近活动" flush class="area-activity">
          <p v-if="!day" class="muted pad">读取中…</p>
          <p v-else-if="!day.activity.length" class="muted pad">还没有活动。</p>
          <ul v-else class="plain-list rows">
            <li v-for="item in day.activity" :key="`${item.kind}:${item.id}`" class="row">
              <RouterLink :to="activityLink(item)" class="row-text activity">
                <span class="activity-text">{{ activityText[item.kind](item) }}</span>
                <span class="muted small">{{ formatAgo(item.at) }}</span>
              </RouterLink>
              <StatusBadge :kind="activityStatus[item.kind]" :value="item.status" />
            </li>
          </ul>
        </Panel>

      <Panel v-if="state" title="群聊" flush class="area-scenes">
        <template #actions><v-btn variant="text" size="small" :append-icon="mdiArrowRight" :to="{ name: 'host-scenes' }">管理</v-btn></template>
        <div v-if="state.scenes.length > 4" class="pad search">
          <v-text-field v-model="search" :prepend-inner-icon="mdiMagnify" placeholder="搜索群名或群号" density="compact" hide-details clearable aria-label="搜索群聊" />
        </div>
        <EmptyState v-if="!state.scenes.length" text="还没有群聊"><v-btn color="primary" :to="{ name: 'host-scenes' }">添加群聊</v-btn></EmptyState>
        <ErrorNote v-if="switchError.error.value" title="聊天开关没有改成" :error="switchError.error.value" class="pad" />
        <ul class="plain-list rows">
          <li v-for="item in scenes" :key="item.scene" class="row scene" :class="{ off: !item.chat_enabled }">
            <RouterLink :to="{ name: 'host-scenes', query: { scene: item.scene } }" class="scene-link">
              <SceneAvatar :scene="item.scene" :size="36" />
              <span class="scene-text"><strong>{{ sceneName(item.scene) }}</strong>
                <span>{{ item.chat_enabled
                  ? [sceneNumber(item.scene), host.members[item.scene] !== undefined ? `${host.members[item.scene]} 人` : '', item.persona.name].filter(Boolean).join(' · ')
                  : '聊天已关闭，只记录消息' }}</span></span>
            </RouterLink>
            <v-switch :model-value="item.chat_enabled" density="compact" hide-details inset
              :aria-label="`${sceneName(item.scene)} 聊天`" :loading="switching[item.scene]"
              :disabled="switching[item.scene] || !state.connection.accepting" @update:model-value="value => setChat(item.scene, value)" />
          </li>
          <li v-if="state.scenes.length && !scenes.length" class="row muted">没有找到匹配的群。</li>
        </ul>
      </Panel>
    </div>
    <DevOnly label="连接详情" :json="state?.connection" />
    <DevOnly v-if="Object.keys(host.titleErrors).length" label="群名读取失败" :json="host.titleErrors" />
    <DevOnly label="今日统计原始数据" :json="day" />
  </HostPage>
</template>

<style scoped>
.status-card{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:var(--sp-4);align-items:center;padding:var(--sp-5);
  border-radius:var(--radius-lg);border:1px solid var(--line);background:var(--surface)}
.status-main{display:flex;align-items:center;gap:var(--sp-4);min-width:0}
.status-mark{position:relative;flex:none}
.status-mark img{width:48px;height:48px;border-radius:var(--radius-lg);display:block}
.status-dot{position:absolute;right:-3px;bottom:-3px;width:14px;height:14px;border-radius:50%;border:2px solid var(--surface);background:var(--warning)}
.status-mark.ok .status-dot{background:var(--success);color:var(--success);animation:pulse 2s var(--ease-out) infinite}
.status-text{display:grid;gap:2px;min-width:0;justify-items:start}
.status-text h2{font-size:var(--fs-xl);font-weight:650}
.status-text p{margin:0;color:var(--muted);overflow-wrap:anywhere}
.status-actions{display:flex;gap:var(--sp-2);flex-wrap:wrap;justify-content:flex-end}
.status-note{grid-column:1/-1;margin:0}
.trend-note{margin:calc(-1 * var(--sp-2)) 0 0}
.columns{display:grid;grid-template-columns:minmax(0,3fr) minmax(320px,2fr);grid-template-areas:'todo scenes' 'activity scenes';grid-template-rows:auto 1fr;gap:var(--sp-4);align-items:start}
.area-todo{grid-area:todo}
.area-activity{grid-area:activity}
.area-scenes{grid-area:scenes}
.pad{padding:0 var(--sp-5) var(--sp-4);margin:0}
.todo-count{min-width:22px;height:22px;padding:0 6px;border-radius:11px;background:var(--error-bg);color:var(--error);font-size:var(--fs-xs);font-weight:600;line-height:22px;text-align:center}
.all-good{display:flex;align-items:center;gap:var(--sp-2);color:var(--success);font-weight:600}
.rows{border-top:1px solid var(--line)}
.row{display:flex;align-items:center;gap:var(--sp-3);padding:var(--sp-3) var(--sp-5);border-bottom:1px solid var(--line);min-width:0}
.row:last-child{border-bottom:0}
.row-text{display:grid;gap:var(--sp-1);flex:1;min-width:0}
.activity{display:flex;align-items:baseline;gap:var(--sp-3);color:inherit}
.activity:hover{text-decoration:none}
.activity:hover .activity-text{text-decoration:underline;text-underline-offset:3px}
.activity-text{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.activity .muted{flex:none}
.search{padding-top:0}
.scene{padding-block:var(--sp-2)}
.scene.off .scene-text strong{color:var(--muted)}
.scene-link{display:flex;align-items:center;gap:var(--sp-3);flex:1;min-width:0;color:inherit}
.scene-link:hover{text-decoration:none}
.scene-link:hover strong{text-decoration:underline;text-underline-offset:3px}
.scene :deep(.v-switch){flex:none}
.scene-text{display:grid;min-width:0;flex:1}
.scene-text strong,.scene-text span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.scene-text strong{font-weight:600}
.scene-text span{font-size:var(--fs-sm);color:var(--muted)}
@media(max-width:1100px){.columns{grid-template-columns:minmax(0,1fr);grid-template-areas:'todo' 'scenes' 'activity';grid-template-rows:none}}
@media(max-width:700px){.status-card{grid-template-columns:minmax(0,1fr);padding:var(--sp-4)}.status-actions{justify-content:flex-start}}
</style>
