<script setup>
import { computed } from 'vue'
import { api, sceneName } from '../../../api.js'
import { useResource } from '../../../composables/useResource.js'
import { host, readHostState } from '../../store.js'
import { runtimeLabel, turnFailed } from '../../labels.js'
import { formatAgo } from '../../time.js'
import HostPage from '../../components/HostPage.vue'
import ErrorNote from '../../components/ErrorNote.vue'
import DevOnly from '../../components/DevOnly.vue'

const today = useResource(() => api('/api/host/overview'))
const day = computed(() => today.data.value)
const state = computed(() => host.state)

const effectLabels = { agree: '认同', continue: '接着聊', correct: '纠正', negative: '反感', unrelated: '没接话' }
const count = values => Object.values(values || {}).reduce((sum, value) => sum + value, 0)
const spoke = computed(() => (day.value?.messages.sent || 0) + (day.value?.messages.simulated || 0))
const costs = computed(() => Object.entries(day.value?.estimated_costs || {}).map(([currency, amount]) => `${amount} ${currency}`))
const effects = computed(() => Object.entries(effectLabels)
  .filter(([key]) => day.value?.reply_effects[key]).map(([key, label]) => `${label} ${day.value.reply_effects[key]}`))

// Only things a person needs to act on, one line and one link each.
const todo = computed(() => {
  const items = []
  for (const [kind, list] of [['插件', state.value?.plugins || []], ['MCP 服务', state.value?.mcp || []]]) {
    for (const item of list) {
      if (item.status === 'failed') items.push({ key: `${kind}:${item.name}`, text: `${kind} ${item.name} 没有启动成功`,
        error: item.error, to: { name: 'host-capabilities' }, action: '查看' })
      else if (item.latest_error) items.push({ key: `${kind}:${item.name}`, text: `${kind} ${item.name} ${formatAgo(item.latest_error.at)}报错`,
        error: item.latest_error.error, to: { name: 'host-capabilities' }, action: '查看' })
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
    const parts = [['expressions', '条表达'], ['jargon', '个黑话'], ['stickers', '张表情']]
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
</script>

<template>
  <HostPage title="首页">
    <template #actions><v-btn variant="outlined" :loading="today.loading.value" @click="refresh">刷新</v-btn></template>
    <ErrorNote v-if="host.stateError" title="读取运行状态失败" :error="host.stateError" />
    <ErrorNote v-if="today.error.value" title="读取今日统计失败" :error="today.error.value" />

    <section v-if="state" class="surface status-card">
      <div class="status-main" :class="{ ok: state.connection.connected }">
        <span class="dot" />
        <div>
          <strong>{{ state.connection.connected ? 'Bot 在线' : state.connection.status === 'running' ? 'QQ 还没连上' : runtimeLabel(state.connection.status) }}</strong>
          <span class="muted">QQ {{ state.bot_qq }} · {{ state.delivery === 'onebot' ? '真实发送到 QQ' : '模拟发送，不会发到 QQ' }}</span>
        </div>
      </div>
      <ErrorNote v-if="!state.connection.connected && state.connection.last_error" title="最近一次连接出错" :error="state.connection.last_error" />
      <DevOnly label="连接详情"><pre>{{ JSON.stringify(state.connection, null, 2) }}</pre></DevOnly>
    </section>

    <section class="surface">
      <h2>需要处理的事</h2>
      <p v-if="!todo.length" class="all-good">一切正常</p>
      <ul v-else class="todo-list">
        <li v-for="item in todo" :key="item.key">
          <div><span>{{ item.text }}</span>
            <details v-if="item.error" class="todo-error"><summary>错误原文</summary><pre>{{ item.error }}</pre></details></div>
          <v-btn :to="item.to" size="small" variant="tonal" color="primary">{{ item.action }}</v-btn>
        </li>
      </ul>
    </section>

    <section v-if="day" class="surface">
      <h2>今天</h2>
      <div class="metrics">
        <div><span>收到消息</span><strong>{{ day.messages.received || 0 }}</strong></div>
        <div><span>Bot 发言</span><strong>{{ spoke }}</strong></div>
        <div><span>回复轮次</span><strong>{{ count(day.turns) }}</strong></div>
        <div><span>待执行提醒</span><strong>{{ day.pending_schedules }}</strong></div>
      </div>
      <p class="day-line">花费：{{ costs.length ? costs.join(' · ') : '暂无' }}<template v-if="day.unknown_cost_calls">，另有 {{ day.unknown_cost_calls }} 次调用费用未知</template>
        <RouterLink :to="{ name: 'host-models' }" class="ml-2">模型与价格</RouterLink></p>
      <p v-if="effects.length" class="day-line">群友对 Bot 发言的反应：{{ effects.join(' · ') }}</p>
      <DevOnly label="今日统计原始数据"><pre>{{ JSON.stringify(day, null, 2) }}</pre></DevOnly>
    </section>

    <section v-if="state" class="surface">
      <h2>群聊</h2>
      <div class="scene-grid">
        <RouterLink v-for="item in state.scenes" :key="item.scene" :to="{ name: 'host-scenes', query: { scene: item.scene } }" class="scene-card">
          <strong>{{ sceneName(item.scene) }}</strong><span>{{ item.persona.name }}</span>
        </RouterLink>
      </div>
    </section>
  </HostPage>
</template>

<style scoped>
.status-card{display:grid;gap:12px}
.status-main{display:flex;align-items:center;gap:14px}
.status-main strong{display:block;font-size:20px}
.status-main .dot{width:12px;height:12px;border-radius:50%;background:var(--status-warning);flex:none}
.status-main.ok .dot{background:var(--success)}
.all-good{margin:8px 0 0;color:var(--success);font-weight:600}
.todo-list{list-style:none;margin:8px 0 0;padding:0;display:grid}
.todo-list li{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;padding:10px 0;border-bottom:1px solid var(--line)}
.todo-list li:last-child{border-bottom:0}
.todo-error summary{cursor:pointer;font-size:13px;color:var(--muted)}
.todo-error pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;margin:6px 0 0}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,150px),1fr));gap:12px;margin:12px 0}
.metrics>div{border:1px solid var(--line);border-radius:10px;padding:12px}
.metrics span{display:block;color:var(--muted);font-size:13px}
.metrics strong{font-size:26px;color:var(--primary)}
.day-line{margin:8px 0 0}
.scene-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,200px),1fr));gap:10px;margin-top:12px}
.scene-card{border:1px solid var(--line);border-radius:10px;padding:12px;color:inherit}
.scene-card:hover{border-color:var(--primary);text-decoration:none}
.scene-card span{display:block;color:var(--muted);font-size:13px}
</style>
