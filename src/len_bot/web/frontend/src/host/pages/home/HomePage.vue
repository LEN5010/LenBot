<script setup>
import { computed } from 'vue'
import { api, sceneName } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { useHostEvents } from '../../events.js'
import { host, readHostState } from '../../store.js'
import { runtimeLabel, turnFailed } from '../../labels.js'
import { formatAgo } from '../../time.js'
import HostPage from '../../ui/HostPage.vue'
import Panel from '../../ui/Panel.vue'
import ErrorNote from '../../ui/ErrorNote.vue'
import LiveStatus from '../../ui/LiveStatus.vue'
import StatGrid from '../../ui/StatGrid.vue'
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
  for (const [kind, tab, list] of [['插件', 'plugins', state.value?.plugins || []], ['MCP 服务', 'mcp', state.value?.mcp || []]]) {
    for (const item of list) {
      if (item.status === 'failed') items.push({ key: `${kind}:${item.name}`, text: `${kind} ${item.name} 没有启动成功`,
        error: item.error, to: { name: 'host-capabilities', query: { tab } }, action: '查看' })
      else if (item.latest_error) items.push({ key: `${kind}:${item.name}`, text: `${kind} ${item.name} ${formatAgo(item.latest_error.at)}报错`,
        error: item.latest_error.error, to: { name: 'host-capabilities', query: { tab } }, action: '查看' })
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
  { label: '收到消息', value: day.value.messages.received || 0 },
  { label: 'Bot 发言', value: spoke.value },
  { label: '回复轮次', value: count(day.value.turns) },
  { label: '待执行提醒', value: day.value.pending_schedules },
])
const connectionNotes = {
  connection_failed: 'QQ 连接失败。确认 OneBot 已经启动、地址和令牌正确，然后手动连接。',
  failed: 'Bot 已停止工作。按下面的错误处理好原因后，重新启动 LenBot。',
  stopped: 'Bot 已停止工作。按下面的错误处理好原因后，重新启动 LenBot。',
  waiting_connection: '正在等待 OneBot 连上来。',
}
</script>

<template>
  <HostPage title="首页">
    <template #actions><v-btn variant="tonal" :loading="today.loading.value" @click="refresh">刷新</v-btn></template>
    <ErrorNote v-if="host.stateError" title="读取运行状态失败" :error="host.stateError" @retry="readHostState" />
    <ErrorNote v-if="today.error.value" title="读取今日统计失败" :error="today.error.value" @retry="today.reload()" />

    <Panel v-if="state">
      <template #title>
        <div class="status-main" :class="{ ok: online }">
          <span class="dot" />
          <div><h2>{{ online ? 'Bot 在线' : runtimeLabel(state.connection.status) }}</h2>
            <span class="muted small">QQ {{ state.bot_qq }} · {{ state.delivery === 'onebot' ? '真实发送到 QQ' : '模拟发送，不会发到 QQ' }}</span></div>
        </div>
      </template>
      <template #actions>
        <LiveStatus :status="events.status.value" @reconnect="events.reconnect" />
        <v-btn v-if="state.connection.can_connect || connect.busy.value" color="primary" :loading="connect.busy.value" @click="connectQQ">手动连接 QQ</v-btn>
        <v-btn :to="{ name: 'host-system', query: { tab: 'connection' } }" variant="text">连接设置</v-btn>
      </template>
      <p v-if="connectionNotes[state.connection.status]" class="note">{{ connectionNotes[state.connection.status] }}</p>
      <ErrorNote v-if="connect.error.value" title="手动连接没有成功" :error="connect.error.value" />
      <ErrorNote v-if="state.connection.last_error" title="最近一次连接或运行失败" :error="state.connection.last_error" />
      <DevOnly label="连接详情" :json="state.connection" />
    </Panel>

    <Panel title="需要处理的事" flush>
      <div class="todo">
        <p v-if="!state || !day" class="muted">读取中…</p>
        <p v-else-if="!todo.length" class="all-good">暂无待处理项</p>
        <ObjectList v-else divided>
          <ObjectRow v-for="item in todo" :key="item.key" :title="item.text">
            <Fold v-if="item.error" label="错误原文" code>{{ item.error }}</Fold>
            <template #actions><v-btn :to="item.to" size="small" variant="tonal" color="primary">{{ item.action }}</v-btn></template>
          </ObjectRow>
        </ObjectList>
      </div>
    </Panel>

    <Panel v-if="day" title="今天">
      <StatGrid :items="stats" />
      <p class="note">花费：{{ costs.length ? costs.join(' · ') : '暂无' }}<template v-if="day.unknown_cost_calls">，另有 {{ day.unknown_cost_calls }} 次调用费用未知</template>
        <RouterLink :to="{ name: 'host-models', query: { tab: 'usage' } }" class="ml-2">模型与价格</RouterLink></p>
      <p v-if="effects.length" class="note">群友对 Bot 发言的反应：{{ effects.join(' · ') }}</p>
      <DevOnly label="今日统计原始数据" :json="day" />
    </Panel>

    <Panel v-if="state" title="群聊">
      <EmptyState v-if="!state.scenes.length" text="还没有群聊"><v-btn color="primary" :to="{ name: 'host-scenes' }">添加群聊</v-btn></EmptyState>
      <div class="scene-grid">
        <RouterLink v-for="item in state.scenes" :key="item.scene" :to="{ name: 'host-scenes', query: { scene: item.scene } }" class="scene-card">
          <strong>{{ sceneName(item.scene) }}</strong><span>{{ item.persona.name }}</span>
        </RouterLink>
      </div>
    </Panel>
  </HostPage>
</template>

<style scoped>
.status-main{display:flex;align-items:center;gap:var(--sp-3)}
.status-main h2{font-size:var(--fs-xl)}
.status-main .dot{width:12px;height:12px;border-radius:50%;background:var(--warning);flex:none}
.status-main.ok .dot{background:var(--success)}
.note{margin:0;overflow-wrap:anywhere}
.todo{padding:0 var(--sp-4) var(--sp-3)}
.todo p{margin:0}
.all-good{color:var(--success);font-weight:600}
.scene-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,200px),1fr));gap:var(--sp-3)}
.scene-card{border:1px solid var(--line);border-radius:var(--radius);padding:var(--sp-3);color:inherit;display:grid;gap:2px}
.scene-card:hover{border-color:var(--primary);background:var(--hover);text-decoration:none}
.scene-card span{color:var(--muted);font-size:var(--fs-sm)}
</style>
