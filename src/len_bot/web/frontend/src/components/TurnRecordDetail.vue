<script setup>
import { developerDetails } from '../composables/useDeveloperMode.js'
import { computed } from 'vue'

const props = defineProps({
  detail: { type: Object, required: true },
  timezone: { type: String, required: true },
})

const detailCalls = computed(() => props.detail.calls.map(call => {
  const nativeCalls = call.native_tool_calls
  const tools = nativeCalls.map(native => ({
    id: native.id,
    name: native.function.name,
    arguments: native.function.arguments,
    result: call.tool_results === null ? null
      : call.tool_results.find(saved => saved.tool_call_id === native.id),
    hasDirectAssociation: call.tool_results !== null
  }))
  return { ...call, tools }
}))

function localTime(value) {
  if (value == null) return '尚未结束'
  return new Date(value * 1000).toLocaleString('zh-CN', {
    timeZone: props.timezone, timeZoneName: 'short', hour12: false
  })
}
function turnLabel(status) {
  return ({ queued: '等待执行', running: '正在执行', settling: '即将结束', settled: '已结束',
    error: '失败', timeout: '超时', cancelled: '已取消', interrupted: '已中断',
    step_limit: '达到轮次上限' })[status] || status
}
function expressionDeliveryLabel(delivery) {
  return ({ simulated: '模拟表达已落库', sent: '平台已确认发送' })[delivery] || delivery
}
function raw(value) {
  return value == null ? '没有保存的内容' : JSON.stringify(value, null, 2)
}
</script>

<template>
  <div class="turn-record-detail">
    <p>状态：{{ turnLabel(detail.turn.status) }} · 开始 {{ localTime(detail.turn.started) }} · 结束 {{ localTime(detail.turn.ended) }}</p>
    <div class="timing-facts">
      <p>首条表达：<template v-if="detail.turn.first_expression_at != null">{{ expressionDeliveryLabel(detail.turn.first_expression_delivery) }} · {{ localTime(detail.turn.first_expression_at) }}</template><template v-else>未记录</template></p>
      <p>轮开始至首条表达：{{ detail.turn.turn_to_first_expression_seconds == null ? '未知' : `${detail.turn.turn_to_first_expression_seconds} 秒` }}</p>
      <p>唤醒机会至首条表达：{{ detail.turn.wake_to_first_expression_seconds == null ? '未知' : `${detail.turn.wake_to_first_expression_seconds} 秒` }}</p>
    </div>
    <p v-if="detail.turn.error" class="turn-error">{{ detail.turn.error }}</p>
    <p v-if="!detailCalls.length" class="muted">这轮尚未保存模型请求。</p>
    <article v-for="call in detailCalls" :key="call.id" class="call-card">
      <div class="call-heading"><h4>{{ ({mind:'大脑',voice:'表达器',recap:'回想',vision:'视觉'})[call.role] || call.role }}</h4>
        <span>{{ localTime(call.started) }} · {{ call.ended==null?'请求中':'已结束' }}</span></div>
      <p v-if="call.snapshot_expired_at" class="muted">请求与响应快照已过保留期；费用、用量及仍保存的原生工具结果保留。</p>
      <p v-if="call.error" class="turn-error">{{ call.error }}</p>
      <p v-if="call.cost == null" class="muted">按配置估算费用未知；不计为 0。</p>
      <p v-else class="cost-fact">按调用时配置估算：<strong>{{ call.cost.currency }} {{ call.cost.amount }}</strong> <span class="muted">· 非供应商账单</span></p>
      <p v-if="developerDetails && call.usage == null" class="muted">提供方用量未知；不按 0 费用显示。</p>
      <p v-else-if="developerDetails" class="usage">提供方返回用量：<code>{{ raw(call.usage) }}</code></p>
      <section v-if="call.tools.length" class="call-tools" aria-label="本次原生工具调用与已保存结果">
        <h5>原生工具调用与已保存结果</h5>
        <div v-for="tool in call.tools" :key="tool.id" class="tool-entry">
          <strong class="tool-name">{{ tool.name }}</strong>
          <details class="tool-text"><summary>查看参数原文</summary><pre>{{ tool.arguments }}</pre></details>
          <p v-if="!tool.hasDirectAssociation" class="muted">没有直接关联，无法确认本次调用的工具结果。</p>
          <p v-else-if="!tool.result" class="muted">尚无已保存结果；不能据此判断是否执行。</p>
          <details v-else class="tool-text"><summary>已保存工具结果 · 不代表执行完成</summary><pre>{{ tool.result.content }}</pre></details>
        </div>
      </section>
      <details v-if="developerDetails"><summary>查看原始请求与响应</summary>
        <h5>请求</h5><pre>{{ raw(call.request) }}</pre>
        <h5>响应</h5><pre>{{ raw(call.response) }}</pre>
      </details>
    </article>
  </div>
</template>

<style scoped>
.turn-record-detail{min-width:0}
.turn-record-detail p{overflow-wrap:anywhere}
.turn-error{color:var(--error-text);white-space:pre-wrap;overflow-wrap:anywhere;margin:8px 0}
.timing-facts{border-left:3px solid var(--primary);padding:8px 12px;margin:12px 0;background:var(--selected-bg)}
.timing-facts p{margin:4px 0}
.call-card{border:1px solid var(--line);border-radius:8px;background:var(--surface);padding:14px;margin-top:12px;min-width:0}
.call-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
.call-heading h4{font-size:14px;margin:0}
.call-heading span{font-size:12px;color:var(--muted)}
.usage code{white-space:pre-wrap;overflow-wrap:anywhere}
.cost-fact strong{overflow-wrap:anywhere}
.call-tools{border-top:1px solid var(--line);margin-top:14px;padding-top:12px;min-width:0}
.call-tools h5{font-size:13px;margin:0 0 10px}
.tool-entry{border:1px solid var(--line);border-radius:8px;padding:10px;margin-top:10px;min-width:0}
.tool-name{display:block;overflow-wrap:anywhere}
.tool-entry p{margin:10px 0 0}
.tool-text{min-width:0}
.call-card details{min-width:0}
.call-card summary{cursor:pointer;color:var(--primary);font-weight:600;min-height:44px;box-sizing:border-box;padding:10px 0}
.call-card summary:focus-visible{outline:2px solid var(--primary);outline-offset:2px}
.call-card h5{font-size:12px;margin:12px 0 6px}
.call-card pre{max-height:320px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;background:var(--code-bg);padding:12px;border-radius:8px;font-size:12px;line-height:1.6}
</style>
