<script setup>
import { computed, reactive } from 'vue'
import { mdiMessageTextOutline, mdiSendOutline, mdiChatProcessingOutline, mdiAlarm, mdiCurrencyCny, mdiRefresh, mdiLanConnect,
  mdiLanDisconnect, mdiCheckCircleOutline, mdiArrowRight, mdiAlertCircleOutline, mdiAccountGroupOutline } from '@mdi/js'
import { api, sceneName, sceneNumber } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { confirm } from '../../../composables/useConfirm.js'
import { useHostEvents } from '../../events.js'
import { host, readHostState } from '../../store.js'
import { runtimeLabel, turnFailed } from '../../labels.js'
import { formatAgo } from '../../time.js'
import HostPage from '../../ui/HostPage.vue'
import Panel from '../../ui/Panel.vue'
import ErrorNote from '../../ui/ErrorNote.vue'
import LiveStatus from '../../ui/LiveStatus.vue'
import StatGrid from '../../ui/StatGrid.vue'
import SceneAvatar from '../../ui/SceneAvatar.vue'
import markUrl from '../../../assets/lenbot-mark.svg'
import ObjectList from '../../ui/ObjectList.vue'
import ObjectRow from '../../ui/ObjectRow.vue'
import EmptyState from '../../ui/EmptyState.vue'
import Fold from '../../ui/Fold.vue'
import DevOnly from '../../ui/DevOnly.vue'

const today = useResource(() => api('/api/host/overview'))
const day = computed(() => today.data.value)
const state = computed(() => host.state)
const connect = useAction()
const events = useHostEvents(readHostState)
const online = computed(() => state.value?.connection.connected && state.value.connection.accepting)

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

// One switch per group: off means messages are still saved but the Bot does not reply.
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

const effectLabels = { agree: '认同', continue: '接着聊', correct: '纠正', negative: '反感', unrelated: '没接话' }
const count = values => Object.values(values || {}).reduce((sum, value) => sum + value, 0)
const spoke = computed(() => (day.value?.messages.sent || 0) + (day.value?.messages.simulated || 0))
const costs = computed(() => Object.entries(day.value?.estimated_costs || {}).map(([currency, amount]) => `${amount} ${currency}`))
const effects = computed(() => Object.entries(effectLabels)
  .filter(([key]) => day.value?.reply_effects[key]).map(([key, label]) => `${label} ${day.value.reply_effects[key]}`))

// Only things a person needs to act on, one line and one link each.
const todo = computed(() => {
  const items = []
  if (state.value && !online.value) {
    items.push({ key: 'connection', text: runtimeLabel(state.value.connection.status),
      to: { name: 'host-system', query: { tab: 'connection' } }, action: '连接设置' })
  }
  const places = { 插件: name => ({ name: 'host-plugins', query: { item: name } }),
    'MCP 服务': name => ({ name: 'host-capabilities', query: { tab: 'mcp', item: name } }) }
  for (const [kind, list] of [['插件', state.value?.plugins || []], ['MCP 服务', state.value?.mcp || []]]) {
    for (const item of list) {
      if (item.status === 'failed') items.push({ key: `${kind}:${item.name}`, text: `${kind} ${item.name} 没有启动成功`,
        error: item.error, to: places[kind](item.name), action: '查看' })
      else if (item.latest_error) items.push({ key: `${kind}:${item.name}`, text: `${kind} ${item.name} ${formatAgo(item.latest_error.at)}报错`,
        error: item.latest_error.error, to: places[kind](item.name), action: '查看' })
    }
  }
  if (!day.value) return items
  for (const [scene, total] of Object.entries(day.value.undelivered)) {
    items.push({ key: `send:${scene}`, text: `${sceneName(scene)} 今天有 ${total} 条消息没发出去`,
      to: { name: 'host-scenes', query: { scene } }, action: '查看' })
  }
  const failures = {}
  for (const turn of day.value.recent_errors) {
    if (turn.started >= day.value.since && turnFailed(turn.status)) failures[turn.scene] = (failures[turn.scene] || 0) + 1
  }
  for (const [scene, total] of Object.entries(failures)) {
    items.push({ key: `turn:${scene}`, text: `${sceneName(scene)} 今天有 ${total} 次回复出错`,
      to: { name: 'host-logs', query: { scene } }, action: '查看' })
  }
  for (const [scene, reviews] of Object.entries(day.value.pending_reviews)) {
    const parts = [['expressions', '条表达'], ['stickers', '张表情']]
      .filter(([key]) => reviews[key]).map(([key, unit]) => `${reviews[key]} ${unit}`)
    items.push({ key: `review:${scene}`, text: `${sceneName(scene)} 新学到 ${parts.join('、')}，等你审核`,
      to: { name: 'host-scenes', query: { scene, tab: 'learning' } }, action: '去审核' })
  }
  return items
})

function refresh() {
  readHostState()
  today.reload()
}
const stats = computed(() => [
  { label: '收到消息', value: day.value.messages.received || 0, icon: mdiMessageTextOutline },
  { label: 'Bot 发言', value: spoke.value, icon: mdiSendOutline },
  { label: '回复轮次', value: count(day.value.turns), icon: mdiChatProcessingOutline, to: { name: 'host-logs' } },
  { label: '待执行提醒', value: day.value.pending_schedules, icon: mdiAlarm, to: { name: 'host-tasks', query: { tab: 'schedules' } } },
  { label: '今日花费', value: costs.value.length ? costs.value.join(' · ') : '暂无', icon: mdiCurrencyCny,
    hint: day.value.unknown_cost_calls ? `另有 ${day.value.unknown_cost_calls} 次调用费用未知` : '', to: { name: 'host-models', query: { tab: 'usage' } } },
])
const connectionNote = computed(() => state.value.connection.status === 'stopped' && state.value.connection.disconnect_requested
  ? connectionNotes.disconnected : connectionNotes[state.value.connection.status])
const connectionNotes = {
  connection_failed: 'QQ 连接失败。确认 OneBot 已经启动、地址和令牌正确，然后手动连接。',
  failed: 'Bot 已停止工作。按下面的错误处理好原因后，重新启动 LenBot。',
  stopped: 'Bot 已停止工作。按下面的错误处理好原因后，重新启动 LenBot。',
  disconnected: '已手动断开 QQ。要重新连上，点顶栏的重启。',
  waiting_connection: '正在等待 OneBot 连上来。',
}
</script>

<template>
  <HostPage title="首页">
    <template #actions><v-btn variant="tonal" color="primary" :prepend-icon="mdiRefresh" :loading="today.loading.value" @click="refresh">刷新</v-btn></template>
    <ErrorNote v-if="host.stateError" title="读取运行状态失败" :error="host.stateError" @retry="readHostState" />
    <ErrorNote v-if="today.error.value" title="读取今日统计失败" :error="today.error.value" @retry="today.reload()" />

    <section v-if="state" class="hero rise" :class="{ ok: online }">
      <div class="hero-main">
        <span class="hero-mark"><img :src="markUrl" alt="" /><span class="hero-dot" /></span>
        <div class="hero-text">
          <h2>{{ online ? 'Bot 在线' : runtimeLabel(state.connection.status) }}</h2>
          <p>{{ state.bot_id }} · {{ state.delivery === 'onebot' ? '真实发送到 QQ' : '模拟发送，不会发到 QQ' }} · {{ state.scenes.length }} 个群聊</p>
          <LiveStatus :status="events.status.value" @reconnect="events.reconnect" />
        </div>
      </div>
      <div class="hero-actions">
        <v-btn v-if="state.connection.can_connect || connect.busy.value" color="primary" :prepend-icon="mdiLanConnect" :loading="connect.busy.value" @click="connectQQ">手动连接 QQ</v-btn>
        <v-btn v-if="state.connection.can_disconnect || disconnect.busy.value" variant="tonal" color="error" :prepend-icon="mdiLanDisconnect" :loading="disconnect.busy.value" @click="disconnectQQ">断开 QQ</v-btn>
        <v-btn :to="{ name: 'host-system', query: { tab: 'connection' } }" variant="text">连接设置</v-btn>
      </div>
      <p v-if="connectionNote" class="hero-note">{{ connectionNote }}</p>
    </section>
    <ErrorNote v-if="connect.error.value" title="手动连接没有成功" :error="connect.error.value" />
    <ErrorNote v-if="disconnect.error.value" title="断开 QQ 没有成功" :error="disconnect.error.value" />
    <ErrorNote v-if="state?.connection.last_error" title="最近一次连接或运行失败" :error="state.connection.last_error" />

    <StatGrid v-if="day" :items="stats" />

    <div class="columns">
      <Panel title="需要处理的事" :icon="todo.length ? mdiAlertCircleOutline : mdiCheckCircleOutline" flush class="rise" style="--i: 2">
        <template #actions><v-chip v-if="todo.length" color="primary">{{ todo.length }}</v-chip></template>
        <div class="todo">
          <p v-if="!state || !day" class="muted">读取中…</p>
          <div v-else-if="!todo.length" class="all-good"><v-icon :icon="mdiCheckCircleOutline" size="20" />一切正常，暂无待处理项</div>
          <ObjectList v-else divided>
            <ObjectRow v-for="item in todo" :key="item.key" :title="item.text">
              <Fold v-if="item.error" label="错误原文" code>{{ item.error }}</Fold>
              <template #actions><v-btn :to="item.to" size="small" variant="tonal" color="primary">{{ item.action }}</v-btn></template>
            </ObjectRow>
          </ObjectList>
        </div>
        <p v-if="effects.length" class="effects muted small">群友对 Bot 发言的反应：{{ effects.join(' · ') }}</p>
      </Panel>

      <Panel v-if="state" title="群聊" :icon="mdiAccountGroupOutline" class="rise" style="--i: 3">
        <template #actions><v-btn variant="text" size="small" :append-icon="mdiArrowRight" :to="{ name: 'host-scenes' }">管理</v-btn></template>
        <EmptyState v-if="!state.scenes.length" text="还没有群聊"><v-btn color="primary" :to="{ name: 'host-scenes' }">添加群聊</v-btn></EmptyState>
        <ErrorNote v-if="switchError.error.value" title="聊天开关没有改成" :error="switchError.error.value" />
        <div class="scene-list">
          <div v-for="item in state.scenes" :key="item.scene" class="scene-card" :class="{ off: !item.chat_enabled }">
            <RouterLink :to="{ name: 'host-scenes', query: { scene: item.scene } }" class="scene-link">
              <SceneAvatar :scene="item.scene" :size="40" />
              <span class="scene-text"><strong>{{ sceneName(item.scene) }}</strong>
                <span>{{ item.chat_enabled ? `${sceneNumber(item.scene)} · ${item.persona.name}` : '聊天已关闭，只记录消息' }}</span></span>
            </RouterLink>
            <v-switch :model-value="item.chat_enabled" color="primary" density="compact" hide-details inset
              :aria-label="`${sceneName(item.scene)} 聊天`" :loading="switching[item.scene]"
              :disabled="switching[item.scene] || !state.connection.accepting" @update:model-value="value => setChat(item.scene, value)" />
          </div>
        </div>
      </Panel>
    </div>
    <DevOnly label="连接详情" :json="state?.connection" />
    <DevOnly v-if="Object.keys(host.titleErrors).length" label="群名读取失败" :json="host.titleErrors" />
    <DevOnly label="今日统计原始数据" :json="day" />
  </HostPage>
</template>

<style scoped>
.hero{position:relative;overflow:hidden;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:var(--sp-4);align-items:center;padding:var(--sp-5) var(--sp-6);
  border-radius:var(--radius-xl);border:1px solid var(--line);background:linear-gradient(120deg,var(--brand-soft),var(--surface) 70%);box-shadow:var(--shadow-card)}
.hero::after{content:'';position:absolute;right:-60px;top:-80px;width:260px;height:260px;border-radius:50%;background:radial-gradient(circle,var(--selected),transparent 70%);animation:float 12s ease-in-out infinite;pointer-events:none}
.hero-main{display:flex;align-items:center;gap:var(--sp-4);min-width:0;position:relative;z-index:1}
.hero-mark{position:relative;flex:none}
.hero-mark img{width:64px;height:64px;border-radius:30%;box-shadow:var(--shadow-brand);display:block}
.hero-dot{position:absolute;right:-3px;bottom:-3px;width:18px;height:18px;border-radius:50%;border:3px solid var(--surface);background:var(--warning)}
.hero.ok .hero-dot{background:var(--success);color:var(--success);animation:pulse 2s var(--ease-out) infinite}
.hero-text{display:grid;gap:2px;min-width:0;justify-items:start}
.hero-text h2{font-size:var(--fs-xl);font-weight:700}
.hero-text p{margin:0;color:var(--muted);overflow-wrap:anywhere}
.hero-actions{display:flex;gap:var(--sp-2);flex-wrap:wrap;position:relative;z-index:1}
.hero-note{grid-column:1/-1;margin:0;position:relative;z-index:1}
.columns{display:grid;grid-template-columns:minmax(0,3fr) minmax(300px,2fr);gap:var(--sp-4);align-items:start}
.todo{padding:0 var(--sp-3) var(--sp-3)}
.todo p{margin:0;padding:0 var(--sp-2)}
.all-good{display:flex;align-items:center;gap:var(--sp-2);padding:var(--sp-3) var(--sp-2);color:var(--success);font-weight:600}
.effects{margin:0;padding:var(--sp-3) var(--sp-5);border-top:1px solid var(--line)}
.scene-list{display:grid;gap:var(--sp-2)}
.scene-card{display:flex;align-items:center;gap:var(--sp-3);padding:var(--sp-2) var(--sp-3);border:1px solid var(--line);border-radius:var(--radius);background:var(--surface);
  transition:transform var(--dur-2) var(--ease-out),box-shadow var(--dur-2) var(--ease-out),border-color var(--dur-1)}
.scene-card:hover{transform:translateY(-2px);box-shadow:var(--shadow-hover);border-color:var(--line-strong)}
.scene-card.off{background:var(--page)}
.scene-card.off .scene-text strong{color:var(--muted)}
.scene-link{display:flex;align-items:center;gap:var(--sp-3);flex:1;min-width:0;color:inherit}
.scene-link:hover{text-decoration:none}
.scene-card :deep(.v-switch){flex:none}
.scene-text{display:grid;min-width:0;flex:1}
.scene-text strong,.scene-text span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.scene-text span{font-size:var(--fs-sm);color:var(--muted)}
@media(max-width:1100px){.columns{grid-template-columns:minmax(0,1fr)}}
@media(max-width:700px){.hero{grid-template-columns:minmax(0,1fr);padding:var(--sp-4)}.hero-mark img{width:52px;height:52px}}
</style>
