<script setup>
import { computed } from 'vue'
import { fmtTime } from '../api.js'
import ResourceViewer from './ResourceViewer.vue'
import EntityLink from './EntityLink.vue'
import StatusBadge from './StatusBadge.vue'
import ObservationDetails from './ObservationDetails.vue'
const props=defineProps({trace:{type:Object,required:true}})
const isConversation=computed(()=>['conversation','conversation_error'].includes(props.trace.kind))
const isNative=computed(()=>['calendar_command','live_announcement'].includes(props.trace.kind))
const nativeState=computed(()=>({generating:'正在生成',committed:'已提交，实际送达见回执',failed:'本次失败',interrupted:'本次已中断'})[props.trace.payload.state]||props.trace.payload.state)
const runs=computed(()=>props.trace.kind.startsWith('agent_job')?props.trace.payload.runs || []:props.trace.kind.startsWith('history_maintenance')?[props.trace.payload.cognition || {}]:[props.trace.payload.conversation || props.trace.payload])
const steps=computed(()=>runs.value.flatMap((run,index)=>(run.steps || []).map(step=>({...step,run_revision:run.job_revision,run_index:index+1}))))
const candidate=computed(()=>[...steps.value].reverse().find(step=>step.terminal_candidate)?.terminal_candidate || runs.value[0]?.terminal_candidate)
const messages=computed(()=>props.trace.payload.result?.message_proposals || candidate.value?.messages || [])
const decision=computed(()=>props.trace.payload.gate?.accepted===true?'Actor / Gate 已接受':props.trace.payload.gate?.accepted===false?'Actor / Gate 已拒绝':'没有提交回执')
const failure=computed(()=>props.trace.payload.error || props.trace.payload.failure_reason || runs.value.find(run=>run.failure_reason)?.failure_reason)
const references=computed(()=>runs.value.map((run,index)=>({index:index+1,...(run.references || {})})).filter(run=>run.messages || run.results))
const estimateLabels={system_reference:'系统与稳定资料',history:'原话与历史交换',tool_results:'工具观察',tool_definitions:'工具定义',images:'图片估算'}
function observationStatus(call){return call.observation_status ?? call.observation?.status}
function messageSource(message){return message.source_event_id || (message.source ? props.trace.payload.conversation?.references?.messages?.[message.source] : null)}
function argumentSummary(argumentsValue){return argumentsValue ? Object.entries(argumentsValue).map(([name,value])=>`${name}=${typeof value==='string'?value:JSON.stringify(value)}`).join('；') : '未记录参数'}
function messageText(message){return (message.segments || []).map(part=>part.text ?? `[图片 ${part.image || part.asset_id}]`).join('')}
</script>
<template>
  <div class="trace-details">
    <v-alert v-if="failure" type="error" variant="tonal">{{ failure }}</v-alert>
    <div class="trace-facts"><span v-if="isConversation">{{ decision }}</span><span v-else-if="isNative">{{ nativeState }}</span><span v-else-if="trace.kind==='history_maintenance'">摘要与认识已提交</span><span v-else-if="trace.kind==='history_maintenance_error'">历史维护未完成</span><span v-else>执行记录 · 交付状态见行动回执</span><span class="muted">{{ fmtTime(trace.created_at) }}</span></div>
    <p v-if="trace.payload.gate?.reason" class="readable-copy">{{ trace.payload.gate.reason }}</p>
    <section v-if="isConversation"><h3>终结候选</h3><p class="muted">候选表达与真实送达分别记录，下方内容不代表已经发到群聊。</p><article v-for="(message,index) in messages" :key="index" class="candidate-message"><span class="muted">第 {{ index+1 }} 条候选</span><p>{{ messageText(message) }}</p><div class="trace-links"><EntityLink v-if="messageSource(message)" type="event" :id="messageSource(message)" :scene-id="trace.scene_id" label="本条请求来源" /><span v-else-if="message.source" class="muted">本轮来源引用 {{ message.source }}，没有保存可回查的原话映射</span><span v-else class="muted">本条请求来源未记录</span><span v-if="message.requester_qq_uid">请求者 QQ {{ message.requester_qq_uid }}</span></div></article><p v-if="!messages.length">{{ candidate && Array.isArray(candidate.messages)?'本候选没有消息，表示模型选择沉默。':'没有可确认的消息候选。' }}</p><ResourceViewer v-if="trace.payload.result?.handled_source_event_ids" title="本轮提交处理的原话 ID" :content="trace.payload.result.handled_source_event_ids" /></section>
    <section v-else-if="isNative">
      <h3>{{ trace.kind==='calendar_command'?'确定性日程交互':'订阅开播邀请' }}</h3>
      <p class="muted">由真实命令或开播事项绑定本群，通过原提交与发送链；不建立普通聊天关注，失败或未知回执不自动重发。</p>
      <EntityLink v-if="trace.payload.source_event_id" type="event" :id="trace.payload.source_event_id" :scene-id="trace.scene_id" label="查看原始命令或开播事实" />
      <p v-if="trace.payload.member">实际订阅成员：{{ trace.payload.member }}</p>
      <p v-if="trace.payload.command_id">命令类型：{{ trace.payload.command_id }}</p>
      <template v-if="trace.payload.schedule">
        <p>请求范围：{{ trace.payload.schedule.start_at }}（含）至 {{ trace.payload.schedule.end_at }}（不含）。</p>
        <p>源取得时间 {{ fmtTime(trace.payload.schedule.fetched_at) }}；源更新时间 {{ trace.payload.schedule.source_updated_at||'源未提供' }}。</p>
        <p class="readable-copy">来源：{{ trace.payload.schedule.source_url }}</p>
        <ResourceViewer title="实际源日程与覆盖说明" :content="trace.payload.schedule" />
      </template>
      <img v-if="trace.payload.asset_id" style="display:block;max-width:100%;height:auto;margin-top:16px" :src="`/api/media/${encodeURIComponent(trace.payload.asset_id)}/file?scene_id=${encodeURIComponent(trace.scene_id)}`" alt="此命令已生成的日程图片" />
      <ResourceViewer v-if="trace.kind==='live_announcement'&&candidate" title="公告候选，送达另看回执" :content="candidate" />
    </section>
    <section v-else><h3>{{ trace.kind.startsWith('agent_job')?'工作执行结果':'维护结果' }}</h3><p class="muted">工作与维护产生资料，不等于群聊沉默或已发送表达。</p><ResourceViewer v-if="trace.payload.result" title="已记录结果" :content="trace.payload.result" /><ResourceViewer v-else-if="candidate" title="终结候选" :content="candidate" /><p v-else class="muted">没有保存可读取的结果，具体执行过程见下方记录。</p></section>
    <section v-if="references.length"><h3>本轮实际读取与资料定位</h3><article v-for="reference in references" :key="reference.index" class="reference-record"><p>第 {{ reference.index }} 段执行：完整读取 {{ reference.read_messages?.length ?? '未记录' }} 条原话；已登记 {{ Object.keys(reference.messages || {}).length }} 条原话位置。</p><ResourceViewer v-if="reference.read_ranges" title="实际原话读取范围" :content="reference.read_ranges" /><div class="trace-links"><EntityLink v-for="id in reference.read_messages || []" :key="id" type="event" :id="id" :scene-id="trace.scene_id" label="已读原话" /><EntityLink v-for="(id,ref) in reference.results || {}" :key="ref" type="result" :id="id" :scene-id="trace.scene_id" :label="ref + ' · 资料定位'" /></div><p class="muted">资料位置只说明可以回读；正文采用范围见各步骤的展示记录。</p></article></section>
    <section><h3>模型与工具步骤</h3><v-expansion-panels variant="accordion"><v-expansion-panel v-for="(step,index) in steps" :key="index"><v-expansion-panel-title><div class="step-title"><strong>第 {{ index+1 }} 步</strong><span v-if="step.run_revision">目标版本 {{ step.run_revision }}</span><span>{{ step.model || '型号未记录' }}</span><span class="muted">{{ step.latency_ms ?? '—' }} ms</span><StatusBadge v-for="call in (step.tool_calls || []).filter(call=>['error','unsupported'].includes(observationStatus(call)))" :key="call.id" domain="observation" :status="observationStatus(call)" /></div></v-expansion-panel-title><v-expansion-panel-text>
      <EntityLink v-if="step.call_id" type="call" :id="step.call_id" :scene-id="trace.scene_id" label="本次模型请求与实际用量" />
      <p v-if="step.failure_reason" class="readable-copy text-error">{{ step.failure_reason }}</p>
      <section v-if="step.local_estimate" class="context-estimate"><h4>本次请求的上下文估算</h4><p>输入 {{ step.local_estimate.input_tokens }} tokens · 图片 {{ step.local_estimate.image_count }} 张。估算与供应商实际用量分别记录。</p><dl><template v-for="(value,key) in step.local_estimate.parts" :key="key"><dt>{{ estimateLabels[key] || key }}</dt><dd>{{ value }} tokens</dd></template></dl></section>
      <ResourceViewer v-if="step.context_plan" title="实际上下文分配与省略原因" :content="step.context_plan" />
      <ResourceViewer v-if="step.presentations || step.presented_ranges" title="本次模型请求实际采用范围" :content="step.presentations || step.presented_ranges" />
      <article v-for="call in step.tool_calls || []" :key="call.id" class="tool-step"><div class="tool-heading"><strong>{{ call.name }}</strong><span>{{ call.status==='completed'?'调用已返回':call.status || '调用状态未记录' }}</span><StatusBadge v-if="observationStatus(call)" domain="observation" :status="observationStatus(call)" /><code v-if="call.error_code || call.observation?.error_code">{{ call.error_code || call.observation.error_code }}</code></div><p class="argument-summary">参数：{{ argumentSummary(call.arguments) }}</p><p v-if="call.failure_reason" class="text-error readable-copy">{{ call.failure_reason }}</p><EntityLink v-if="call.observation?.result_id" type="result" :id="call.observation.result_id" :scene-id="trace.scene_id" label="读取已保存的完整工具资料" /><ObservationDetails v-if="call.stored_observation" :observation="call.stored_observation" :scene-id="trace.scene_id" /><p v-else-if="call.observation?.result_id" class="muted">已记录资料 ID，但该场景中的保存正文目前不可读取。</p><ResourceViewer v-if="call.presentations || call.presented_ranges" title="本条观察实际采用范围" :content="call.presentations || call.presented_ranges" /><ResourceViewer v-if="call.receipt" title="暂存提案回执" :content="call.receipt" /><details><summary>参数与调用原始记录</summary><ResourceViewer title="工具调用" :content="call" /></details></article>
      <details><summary>完整步骤记录</summary><ResourceViewer title="步骤原始记录" :content="step" /></details>
    </v-expansion-panel-text></v-expansion-panel><p v-if="!steps.length" class="muted">这份记录没有模型步骤。</p></v-expansion-panels></section>
    <v-expansion-panels variant="accordion"><v-expansion-panel title="完整脱敏记录"><v-expansion-panel-text><ResourceViewer title="Trace" :content="trace.payload" /></v-expansion-panel-text></v-expansion-panel></v-expansion-panels>
  </div>
</template>
<style scoped>.trace-details{display:grid;gap:24px;min-width:0}.trace-facts,.trace-links{display:flex;gap:12px;flex-wrap:wrap;font-size:13px}.trace-links{margin-top:12px}.trace-details h3{font-size:15px;margin:0 0 10px}.trace-details p{font-size:13px;line-height:1.7}.candidate-message{border:1px solid var(--line);border-radius:8px;padding:12px;margin-top:12px}.candidate-message p{white-space:pre-wrap;overflow-wrap:anywhere;margin:8px 0 0}.step-title{display:flex;gap:12px;flex-wrap:wrap;min-width:0;font-size:12px;overflow-wrap:anywhere}.tool-step{display:grid;gap:12px;font-size:12px;padding:18px 0;margin-bottom:12px;border-bottom:1px solid var(--line)}.tool-heading{display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px}.tool-step p{margin:0}.argument-summary{overflow-wrap:anywhere;white-space:pre-wrap;max-height:7.5em;overflow:auto}.context-estimate{margin:20px 0;padding:14px;background:rgb(var(--v-theme-surface-variant));border-radius:8px}.context-estimate h4{font-size:13px}.context-estimate dl{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px 16px;font-size:12px}.context-estimate dd{margin:0}.context-estimate dt{overflow-wrap:anywhere}.trace-details summary{cursor:pointer;color:rgb(var(--v-theme-primary));font-size:13px}.trace-details details[open]>summary{margin-bottom:12px}.reference-record{display:grid;gap:10px;padding:12px 0}</style>
