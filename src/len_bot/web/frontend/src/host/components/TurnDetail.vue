<script setup>
// What happened in one reply: what the mind thought, which tools it used and
// what came back, and what was actually expressed. Raw exchanges stay in developer mode.
import { computed } from 'vue'
import { callRoleLabel, toolLabel } from '../labels.js'
import { formatTime } from '../time.js'
import ErrorNote from '../ui/ErrorNote.vue'
import StatusBadge from '../ui/StatusBadge.vue'
import CodeBlock from '../ui/CodeBlock.vue'
import Fold from '../ui/Fold.vue'
import DevOnly from '../ui/DevOnly.vue'

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
    <div class="inline"><StatusBadge kind="turn" :value="turn.status" />
      <span class="muted small">{{ formatTime(turn.started, timezone) }}<template v-if="turn.ended"> · 用时 {{ seconds(turn.ended - turn.started) }}</template></span></div>
    <ErrorNote v-if="turn.error" title="这次回复出错了" :error="turn.error" />
    <p v-if="!steps.length" class="muted">这次没有调用模型。</p>
    <ol class="steps">
      <li v-for="step in steps" :key="step.id">
        <div class="step-head"><strong>{{ callRoleLabel(step.role) }}</strong>
          <span class="muted small">{{ step.ended === null ? '进行中' : '' }}<template v-if="step.tokens"> 输入 {{ step.tokens.input }} · 输出 {{ step.tokens.output }} token</template></span></div>
        <ErrorNote v-if="step.error" title="这一步出错了" :error="step.error" />
        <p v-if="step.thought" class="thought">{{ step.thought }}</p>
        <div v-for="tool in step.tools" :key="tool.id" class="tool">
          <strong>{{ toolLabel(tool.name) }}</strong>
          <CodeBlock :text="tool.arguments" />
          <Fold v-if="tool.result" label="返回结果" code>{{ tool.result.content }}</Fold>
        </div>
        <p v-if="step.snapshot_expired_at" class="muted small">请求原文已过保留期。</p>
        <DevOnly label="原始请求与响应">
          <p v-if="step.usage" class="small">用量：<code>{{ JSON.stringify(step.usage) }}</code></p>
          <h5>请求</h5><CodeBlock :text="JSON.stringify(step.request, null, 2)" />
          <h5>响应</h5><CodeBlock :text="JSON.stringify(step.response, null, 2)" />
        </DevOnly>
      </li>
    </ol>
    <DevOnly label="本轮原始记录" :json="turn" />
  </div>
</template>

<style scoped>
.turn-detail{display:grid;gap:var(--sp-3);min-width:0}
.turn-detail p{margin:0}
.steps{list-style:none;margin:0;padding:0;display:grid;gap:var(--sp-3)}
.steps li{border:1px solid var(--line);border-radius:var(--radius);padding:var(--sp-3);min-width:0;background:var(--surface);display:grid;gap:var(--sp-2)}
.step-head{display:flex;justify-content:space-between;gap:var(--sp-3)}
.thought{white-space:pre-wrap;overflow-wrap:anywhere;padding:var(--sp-2) var(--sp-3);background:var(--hover);border-radius:var(--radius)}
.tool{border-left:2px solid var(--primary);padding-left:var(--sp-3);display:grid;gap:var(--sp-1)}
</style>
