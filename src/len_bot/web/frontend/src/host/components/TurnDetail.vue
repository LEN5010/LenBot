<script setup>
// What happened in one reply: what the mind thought, which tools it used and
// what came back, and what was actually expressed. Raw exchanges stay in developer mode.
import { computed } from 'vue'
import { callRoleLabel, toolLabel, turnLabel } from '../labels.js'
import { formatTime } from '../time.js'
import ErrorNote from './ErrorNote.vue'
import DevOnly from './DevOnly.vue'

const props = defineProps({ detail: { type: Object, required: true }, timezone: { type: String, required: true } })
const turn = computed(() => props.detail.turn)
const steps = computed(() => props.detail.calls.map(call => ({
  ...call,
  thought: call.response?.message?.content || '',
  tools: call.native_tool_calls.map(native => ({
    id: native.id, name: native.function.name, arguments: readable(native.function.arguments),
    result: call.tool_results?.find(saved => saved.tool_call_id === native.id) || null,
  })),
})))
function readable(text) {
  try {
    const value = JSON.parse(text)
    if (value && typeof value === 'object' && !Array.isArray(value)) return Object.entries(value)
      .map(([key, item]) => `${key}：${typeof item === 'string' ? item : JSON.stringify(item)}`).join('\n')
  } catch { /* not JSON: show the original text */ }
  return text
}
const seconds = value => `${Math.round(value * 10) / 10} 秒`
</script>

<template>
  <div class="turn-detail">
    <p class="turn-summary"><strong>{{ turnLabel(turn.status) }}</strong>
      <span class="muted">{{ formatTime(turn.started, timezone) }}<template v-if="turn.ended"> · 用时 {{ seconds(turn.ended - turn.started) }}</template></span></p>
    <ErrorNote v-if="turn.error" title="这次回复出错了" :error="turn.error" />
    <p v-if="!steps.length" class="muted">这次没有调用模型。</p>
    <ol class="steps">
      <li v-for="step in steps" :key="step.id">
        <div class="step-head"><strong>{{ callRoleLabel(step.role) }}</strong>
          <span class="muted">{{ step.ended === null ? '进行中' : '' }}<template v-if="step.cost"> {{ step.cost.amount }} {{ step.cost.currency }}</template></span></div>
        <ErrorNote v-if="step.error" title="这一步出错了" :error="step.error" />
        <p v-if="step.thought" class="thought">{{ step.thought }}</p>
        <div v-for="tool in step.tools" :key="tool.id" class="tool">
          <strong>{{ toolLabel(tool.name) }}</strong>
          <pre>{{ tool.arguments }}</pre>
          <details v-if="tool.result"><summary>返回结果</summary><pre>{{ tool.result.content }}</pre></details>
        </div>
        <p v-if="step.snapshot_expired_at" class="muted">请求原文已过保留期。</p>
        <DevOnly label="原始请求与响应">
          <p v-if="step.usage">用量：<code>{{ JSON.stringify(step.usage) }}</code></p>
          <h5>请求</h5><pre>{{ JSON.stringify(step.request, null, 2) }}</pre>
          <h5>响应</h5><pre>{{ JSON.stringify(step.response, null, 2) }}</pre>
        </DevOnly>
      </li>
    </ol>
    <DevOnly label="本轮原始记录"><pre>{{ JSON.stringify(turn, null, 2) }}</pre></DevOnly>
  </div>
</template>

<style scoped>
.turn-summary{display:flex;gap:10px;align-items:baseline;margin:0 0 8px}
.steps{list-style:none;margin:8px 0 0;padding:0;display:grid;gap:10px}
.steps li{border:1px solid var(--line);border-radius:10px;padding:12px;min-width:0;background:var(--surface)}
.step-head{display:flex;justify-content:space-between;gap:12px}
.thought{white-space:pre-wrap;overflow-wrap:anywhere;margin:8px 0 0;padding:8px 10px;background:var(--selected-bg);border-radius:8px}
.tool{border-left:3px solid var(--primary);padding:4px 10px;margin-top:10px}
.tool pre,.steps details pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;margin:4px 0 0;max-height:360px;overflow:auto}
.tool summary{cursor:pointer;font-size:13px;color:var(--primary)}
h5{margin:8px 0 4px;font-size:12px}
</style>
