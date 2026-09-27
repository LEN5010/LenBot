<script setup>
import { computed, onMounted, ref } from 'vue'
import { api, sceneName } from '../api.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'

const overview = ref(null), host = ref(null)
const overviewError = ref(''), hostError = ref(''), loading = ref(false)
const beginRead = useRequestGuard()
const messageLabels = { received: '平台入站 · 已保存', sent: '平台已确认发送', simulated: '模拟表达 · 未发往 QQ', failed: '发送失败', unconfirmed: '发送结果未确认' }
const turnLabels = { queued: '等待执行', running: '执行中', settling: '即将结束', settled: '已结束', error: '失败', timeout: '超时', cancelled: '已取消', interrupted: '已中断', step_limit: '达到轮次上限' }
const messages = computed(() => Object.entries(overview.value?.messages || {}))
const turns = computed(() => Object.entries(overview.value?.turns || {}))
const costs = computed(() => Object.entries(overview.value?.estimated_costs || {}))
const messageTotal = computed(() => messages.value.reduce((sum, [, count]) => sum + count, 0))
const turnTotal = computed(() => turns.value.reduce((sum, [, count]) => sum + count, 0))
function time(value, timezone) {
  return new Date(value * 1000).toLocaleString('zh-CN', { timeZone: timezone, hour12: false, timeZoneName: 'short' })
}
function runtimeLabel(value) {
  return ({ created: '已装配', starting: '启动中', waiting_connection: '等待 OneBot 连接',
    running: '运行中', stopping: '停止中', stopped: '已停止' })[value] || value
}
async function refresh() {
  const fresh = beginRead()
  loading.value = true
  const [day, state] = await Promise.allSettled([api('/api/host/overview'), api('/api/host/state')])
  if (!fresh()) return
  if (day.status === 'fulfilled') { overview.value = day.value; overviewError.value = '' }
  else overviewError.value = day.reason.message
  if (state.status === 'fulfilled') { host.value = state.value; hostError.value = '' }
  else hostError.value = state.reason.message
  loading.value = false
}
onMounted(refresh)
</script>

<template>
  <div class="page-stack host-overview">
    <header class="page-intro"><div><p class="eyebrow">独立多场景宿主</p><h1>今日概览</h1>
      <p class="muted">只汇总当前配置场景中已保存的事实。进入和手动刷新时读取，不监听每条消息重算；下方时间、计数与连接状态均以各自最近成功读取为准。</p></div>
      <v-btn variant="outlined" :loading="loading" :disabled="loading" @click="refresh">手动刷新</v-btn></header>
    <v-alert v-if="overviewError" type="error" variant="tonal" role="alert" :title="overview?'今日统计读取失败 · 保留上次结果':'今日统计读取失败'">{{ overviewError }}</v-alert>
    <v-alert v-if="hostError" type="error" variant="tonal" role="alert" :title="host?'连接状态读取失败 · 保留上次结果':'连接状态读取失败'">{{ hostError }}</v-alert>
    <div v-if="loading && !overview && !host" class="surface empty-state" role="status">正在读取宿主今日统计与连接现场…</div>
    <section v-if="host" class="surface"><div class="section-heading"><h2>宿主现场</h2><v-chip variant="tonal" :color="hostError?'warning':host.connection.connected?'success':'warning'">{{ hostError?'连接状态读取失败':`最近读取：${runtimeLabel(host.connection.status)}` }}</v-chip></div>
      <div class="fact-grid"><div><span class="fact-label">OneBot · 最近读取</span><strong>{{ host.connection.connected?'当时连接已建立':'当时未连接' }} · {{ host.connection.mode==='reverse_ws'?'反向':'正向' }}</strong></div>
        <div><span class="fact-label">消息出口</span><strong>{{ host.delivery==='onebot'?'OneBot 实际发送':'模拟发送' }}</strong></div>
        <div><span class="fact-label">配置场景</span><strong>{{ host.scenes.length }} 个</strong></div></div>
      <p class="muted">WebSocket 连接不代表 QQ 客户端已登录；最近平台错误不因重连而被视作已解决。</p>
      <details v-if="host.connection.last_error"><summary>最近一次平台错误原文</summary><pre>{{ host.connection.last_error }}</pre></details>
      <div class="scene-links"><RouterLink v-for="item in host.scenes" :key="item.scene" :to="{name:'host',query:{scene:item.scene}}" class="scene-link">
        <strong>{{ sceneName(item.scene) }}</strong><span>{{ item.persona.name }} · 查看观察记录</span></RouterLink></div>
    </section>
    <template v-if="overview">
      <section class="surface"><div class="section-heading"><h2>今日保存事实</h2><span class="muted">{{ time(overview.since,overview.timezone) }} 至 {{ time(overview.until,overview.timezone) }}（不含终点）</span></div>
        <p class="muted">统计采样于 {{ time(overview.sampled_at,overview.timezone) }}；时段按 {{ overview.timezone }} 当地日历日，不按浏览器时区。</p>
        <div class="metric-grid"><div class="metric"><span>已保存消息</span><strong>{{ messageTotal }}</strong><small>按实际状态分组</small></div>
          <div class="metric"><span>已开启轮次</span><strong>{{ turnTotal }}</strong><small>按实际轮次状态分组</small></div>
          <div class="metric"><span>聊天模型调用</span><strong>{{ overview.model_calls }}</strong><small>其中 {{ overview.unfinished_calls }} 次尚未结束</small></div>
          <div class="metric"><span>当前待执行提醒</span><strong>{{ overview.pending_schedules }}</strong><small>当前状态，不限定今日创建</small></div></div>
        <div class="paired"><div><h3>消息状态</h3><dl v-if="messages.length" class="breakdown"><div v-for="[status,count] in messages" :key="status"><dt>{{ messageLabels[status] || status }}</dt><dd>{{ count }}</dd></div></dl><p v-else class="muted">本时段没有已保存消息。</p></div>
          <div><h3>轮次状态</h3><dl v-if="turns.length" class="breakdown"><div v-for="[status,count] in turns" :key="status"><dt>{{ turnLabels[status] || status }}</dt><dd>{{ count }}</dd></div></dl><p v-else class="muted">本时段没有已开启轮次。</p></div></div>
      </section>
      <section class="surface"><div class="section-heading"><h2>聊天模型费用估算</h2><span class="muted">不含记忆抽取及嵌入，非账单</span></div>
        <p><strong>{{ overview.unknown_cost_calls }}</strong> 次调用费用未知；未知不并入 0，不与币种金额相加。</p>
        <dl v-if="costs.length" class="breakdown costs"><div v-for="[currency,amount] in costs" :key="currency"><dt>{{ currency }}</dt><dd>{{ amount }}</dd></div></dl>
        <p v-else class="muted">本时段没有可汇总的已知费用金额。</p>
        <RouterLink :to="{name:'host-models'}">查看模型绑定与配置价格</RouterLink>
      </section>
      <section class="surface"><div class="section-heading"><h2>最近轮次错误</h2><span class="muted">最多 10 条 · 不限定今日 · 未标记已解决</span></div>
        <p v-if="!overview.recent_errors.length" class="muted">当前配置场景中没有已保存的轮次错误。</p>
        <ul v-else class="error-list"><li v-for="item in overview.recent_errors" :key="item.id">
          <div class="error-heading"><RouterLink :to="{name:'host',query:{scene:item.scene}}">{{ sceneName(item.scene) }}</RouterLink>
            <span>{{ turnLabels[item.status] || item.status }} · {{ time(item.started,overview.timezone) }}</span></div>
          <details><summary>查看错误原文</summary><pre>{{ item.error }}</pre></details>
        </li></ul>
      </section>
    </template>
    <section class="surface"><h2>继续查看</h2><div class="quick-links"><RouterLink :to="{name:'host'}">多场景观察</RouterLink>
      <RouterLink :to="{name:'host-settings'}">群聊设置与角色</RouterLink><RouterLink :to="{name:'host-system'}">连接与运行设置</RouterLink></div></section>
  </div>
</template>

<style scoped>
.host-overview{max-width:1200px;margin-inline:auto}
.page-intro,.section-heading,.error-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:14px;flex-wrap:wrap}
.page-intro>div{min-width:0;flex:1 1 480px}.page-intro h1{margin:0 0 10px}
.eyebrow{font-size:12px;letter-spacing:.08em;color:var(--primary);font-weight:700;margin:0 0 5px}
.surface{min-width:0;overflow-wrap:anywhere}.surface h2{font-size:18px;margin:0 0 14px}.surface h3{font-size:15px;margin:20px 0 9px}
.fact-grid,.metric-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,190px),1fr));gap:12px}.fact-grid>div,.metric{border:1px solid var(--line);border-radius:10px;padding:12px;min-width:0}
.fact-label,.metric span,.metric small{display:block;color:var(--muted)}.fact-grid strong{display:block;margin-top:4px;overflow-wrap:anywhere}.metric strong{display:block;font-size:26px;margin:3px 0;color:var(--primary)}.metric small{font-size:12px}
.paired{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,260px),1fr));gap:20px}.breakdown{margin:0}.breakdown>div{display:flex;justify-content:space-between;gap:12px;padding:9px 0;border-bottom:1px solid var(--line)}.breakdown dd{margin:0;font-weight:700}
.scene-links{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:10px;margin-top:16px}.scene-link{border:1px solid var(--line);border-radius:10px;padding:12px;min-height:58px;overflow-wrap:anywhere}.scene-link span{display:block;color:var(--muted);font-size:13px;margin-top:3px}
.error-list{list-style:none;margin:0;padding:0;display:grid;gap:10px}.error-list li{border:1px solid var(--line);border-radius:10px;padding:12px;min-width:0}.error-heading span{color:var(--muted)}
.host-overview details{margin-top:8px}.host-overview summary{cursor:pointer;min-height:44px}.host-overview pre{white-space:pre-wrap;overflow-wrap:anywhere}.quick-links{display:flex;gap:14px;flex-wrap:wrap}
.host-overview :deep(.v-btn){min-height:44px}.host-overview :deep(.v-alert),.host-overview .muted{overflow-wrap:anywhere}
@media(max-width:600px){.page-intro{display:grid}.page-intro>.v-btn{width:100%}.surface{padding:16px}}
</style>
