<script setup>
import { computed } from 'vue'
import { fmtTime } from '../api.js'
import ResourceViewer from './ResourceViewer.vue'
const props=defineProps({trace:{type:Object,required:true}})
const isConversation=computed(()=>['conversation','conversation_error'].includes(props.trace.kind))
const runs=computed(()=>props.trace.kind.startsWith('agent_job')?props.trace.payload.runs || []:props.trace.kind.startsWith('history_maintenance')?[props.trace.payload.cognition || {}]:[props.trace.payload.conversation || props.trace.payload])
const steps=computed(()=>runs.value.flatMap(run=>run.steps || []))
const candidate=computed(()=>[...steps.value].reverse().find(step=>step.terminal_candidate)?.terminal_candidate || runs.value[0]?.terminal_candidate)
const messages=computed(()=>candidate.value?.messages || props.trace.payload.result?.message_proposals || [])
const decision=computed(()=>props.trace.payload.gate?.accepted===true?'Actor / Gate 已接受':props.trace.payload.gate?.accepted===false?'Actor / Gate 已拒绝':'没有提交回执')
const failure=computed(()=>props.trace.payload.error || props.trace.payload.failure_reason || runs.value.find(run=>run.failure_reason)?.failure_reason)
function messageText(message){return (message.segments || []).map(part=>part.text ?? `[图片 ${part.image || part.asset_id}]`).join('')}
</script>
<template>
  <div class="trace-details">
    <v-alert v-if="failure" type="error" variant="tonal">{{ failure }}</v-alert>
    <div class="trace-facts"><span v-if="isConversation">{{ decision }}</span><span v-else-if="trace.kind==='history_maintenance'">摘要与认识已提交</span><span v-else-if="trace.kind==='history_maintenance_error'">历史维护未完成</span><span v-else>执行记录 · 交付状态见行动回执</span><span class="muted">{{ fmtTime(trace.created_at) }} · 北京时间</span></div>
    <p v-if="trace.payload.gate?.reason" class="readable-copy">{{ trace.payload.gate.reason }}</p>
    <section v-if="isConversation"><h3>终结候选</h3><p class="muted">候选表达与真实送达分别记录，下方内容不代表已经发到群聊。</p><article v-for="(message,index) in messages" :key="index" class="candidate-message"><span class="muted">第 {{ index+1 }} 条候选</span><p>{{ messageText(message) }}</p></article><p v-if="!messages.length">{{ candidate && Array.isArray(candidate.messages)?'本候选没有消息，表示模型选择沉默。':'没有可确认的消息候选。' }}</p></section>
    <section v-else><h3>{{ trace.kind.startsWith('agent_job')?'工作执行结果':'维护结果' }}</h3><p class="muted">工作与维护产生资料，不等于群聊沉默或已发送表达。</p><ResourceViewer v-if="trace.payload.result" title="已记录结果" :content="trace.payload.result" /><ResourceViewer v-else-if="candidate" title="终结候选" :content="candidate" /><p v-else class="muted">没有保存可读取的结果，具体执行过程见下方记录。</p></section>
    <section><h3>模型与工具步骤</h3><v-expansion-panels variant="accordion"><v-expansion-panel v-for="(step,index) in steps" :key="index"><v-expansion-panel-title><div class="step-title"><strong>第 {{ index+1 }} 步</strong><span>{{ step.model || '型号未记录' }}</span><span class="muted">{{ step.latency_ms ?? '—' }} ms</span></div></v-expansion-panel-title><v-expansion-panel-text><p v-if="step.failure_reason" class="readable-copy text-error">{{ step.failure_reason }}</p><div v-for="call in step.tool_calls || []" :key="call.id" class="tool-step"><strong>{{ call.name }}</strong><span>{{ call.status==='completed'?'工具调用结束':call.status }}</span><span v-if="call.observation" class="muted">资料：{{ call.observation.status }} {{ call.observation.error_code }}</span></div><ResourceViewer title="步骤原始记录" :content="step" /></v-expansion-panel-text></v-expansion-panel><p v-if="!steps.length" class="muted">这份记录没有模型步骤。</p></v-expansion-panels></section>
    <v-expansion-panels variant="accordion"><v-expansion-panel title="完整脱敏记录"><v-expansion-panel-text><ResourceViewer title="Trace" :content="trace.payload" /></v-expansion-panel-text></v-expansion-panel></v-expansion-panels>
  </div>
</template>
<style scoped>.trace-details{display:grid;gap:24px;min-width:0}.trace-facts{display:flex;gap:12px;flex-wrap:wrap;font-size:13px}.trace-details h3{font-size:15px;margin:0 0 10px}.trace-details p{font-size:13px;line-height:1.7}.candidate-message{border:1px solid var(--line);border-radius:8px;padding:12px;margin-top:12px}.candidate-message p{white-space:pre-wrap;overflow-wrap:anywhere;margin:8px 0 0}.step-title{display:flex;gap:12px;flex-wrap:wrap;min-width:0;font-size:12px;overflow-wrap:anywhere}.tool-step{display:flex;flex-wrap:wrap;gap:10px;font-size:12px;padding:10px 0;margin-bottom:12px;border-bottom:1px solid var(--line)}</style>
