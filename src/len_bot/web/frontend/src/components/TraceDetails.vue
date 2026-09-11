<script setup>
import { computed } from 'vue'
import { fmtTime } from '../api.js'
import ResourceViewer from './ResourceViewer.vue'
import EntityLink from './EntityLink.vue'
import StatusBadge from './StatusBadge.vue'
import ObservationDetails from './ObservationDetails.vue'
import BudgetDetails from './BudgetDetails.vue'
import OperationReceipts from './OperationReceipts.vue'
import SourceOutcomes from './SourceOutcomes.vue'
import { publicationActionLabel,traceRuns } from '../domain/activity.js'
import PluginOrigin from './PluginOrigin.vue'
const props=defineProps({trace:{type:Object,required:true}})
const isConversation=computed(()=>['conversation','conversation_error'].includes(props.trace.kind))
const isPlugin=computed(()=>props.trace.kind.startsWith('plugin_'))
const isMemoryRetrieval=computed(()=>props.trace.kind==='memory_retrieval')
const runs=computed(()=>traceRuns(props.trace.payload))
const directTools=computed(()=>runs.value.flatMap(run=>run.tool_results || []))
const hooks=computed(()=>runs.value.flatMap(run=>run.hooks || []))
const pluginCommits=computed(()=>runs.value.flatMap(run=>run.commits || []))
const steps=computed(()=>runs.value.flatMap((run,index)=>(run.steps || []).map(step=>({...step,run_revision:run.job_revision,run_index:index+1,run_origin:run.plugin_origin,run_references:run.references}))))
const candidate=computed(()=>[...steps.value].reverse().find(step=>step.terminal_candidate)?.terminal_candidate || runs.value[0]?.terminal_candidate)
const messages=computed(()=>props.trace.payload.result?.message_proposals || candidate.value?.messages || [])
const checkpoints=computed(()=>runs.value.flatMap(run=>run.checkpoints || []))
const decision=computed(()=>props.trace.payload.gate?.committed===true?'对话事务已提交':props.trace.payload.gate?.accepted===true?'Actor / Gate 已接受':props.trace.payload.gate?.accepted===false?'Actor / Gate 已拒绝':'没有提交回执')
const publication=computed(()=>props.trace.payload.gate?.publication)
const publicationState=computed(()=>({pending:'等待发布',completed:'发布步骤已完成',failed:'已提交，发布失败',interrupted:'已提交，发布中断',not_repeated:'此前已提交，本次未重复发布'})[publication.value?.status] || publication.value?.status)
const publicationPhase=computed(()=>({not_started:'尚未开始',commit_acknowledgement:'等待提交回执时已取消',awaiting_publication:'等待前一轮完成发布',action_preparation:'组织待发消息',scheduler_schedule:'登记调度任务',scheduler_sync:'同步调度任务',action_enqueue:'消息入队',completed:'已完成',previous_commit:'已有提交'})[publication.value?.phase] || publication.value?.phase)
const failure=computed(()=>props.trace.payload.error || props.trace.payload.failure_reason || runs.value.find(run=>run.failure_reason)?.failure_reason)
const references=computed(()=>runs.value.map((run,index)=>({index:index+1,...(run.references || {})})).filter(run=>run.messages || run.results))
const budgets=computed(()=>{
  const values=runs.value.flatMap((run,index)=>(run.budget_snapshot || run.requested_budget)?[{index:index+1,budget:run.budget_snapshot || run.requested_budget}]:[])
  return values.length?values:props.trace.payload.budget_snapshot?[{index:1,budget:props.trace.payload.budget_snapshot}]:[]
})
const estimateLabels={system_reference:'系统与稳定资料',history:'原话与历史交换',tool_results:'工具观察',tool_definitions:'工具定义',images:'图片估算'}
function observationStatus(call){return call.observation_status ?? call.observation?.status}
function messageSource(message){return message.source_event_id || (message.source ? [...steps.value].reverse().find(step=>step.terminal_candidate)?.run_references?.messages?.[message.source] : null)}
function argumentSummary(argumentsValue){return argumentsValue ? Object.entries(argumentsValue).map(([name,value])=>`${name}=${typeof value==='string'?value:JSON.stringify(value)}`).join('；') : '未记录参数'}
function messageText(message){return (message.segments || []).map(part=>part.text ?? (part.at || part.type==='at'?`[提及 ${part.at || part.qq_uid}]`:part.type==='at_all'?'[全体成员]':`[${part.type || '媒体'} ${part.image || part.video || part.audio || part.asset_id}]`)).join('')}
</script>
<template>
  <div class="trace-details">
    <v-alert v-if="failure" type="error" variant="tonal">{{ failure }}</v-alert>
    <div class="trace-facts"><span v-if="isConversation">{{ decision }}</span><span v-else-if="isPlugin">插件状态：{{ trace.payload.state || trace.payload.operation || '见记录' }}</span><span v-else-if="isMemoryRetrieval">认识召回 · {{ trace.payload.index_partial ? '索引未完全覆盖' : '索引覆盖完整' }}</span><span v-else-if="trace.kind==='history_maintenance'">摘要与认识已提交</span><span v-else-if="trace.kind==='history_maintenance_error'">历史维护未完成</span><span v-else>执行记录 · 交付状态见行动回执</span><span class="muted">{{ fmtTime(trace.created_at) }}</span></div>
    <p v-if="trace.payload.gate?.reason" class="readable-copy">{{ trace.payload.gate.reason }}</p>
    <section v-if="publication"><h3>{{ publicationState }}</h3>
      <EntityLink v-if="trace.payload.gate.commit_event_id" type="event" :id="trace.payload.gate.commit_event_id" :scene-id="trace.scene_id" label="读取已提交事务" />
      <p>发布阶段：{{ publicationPhase }}。实际送达以行动回执为准。</p>
      <v-alert v-if="publication.error" type="error" variant="tonal">{{ publication.error_type }}：{{ publication.error }}</v-alert>
      <p v-if="publication.scheduled_task_ids?.length">本次已登记的调度任务：{{ publication.scheduled_task_ids.join('、') }}</p>
      <article v-for="action in publication.actions || []" :key="action.action_id" class="candidate-message">
        <strong>第 {{ action.batch_index+1 }} 条 · {{ publicationActionLabel(action.status) }}</strong>
        <div class="trace-links"><code>{{ action.action_id }}</code><EntityLink type="episode" :id="trace.payload.gate.commit_event_id?.slice(5)" :scene-id="trace.scene_id" label="同轮行动与回执" /><EntityLink v-if="action.origin_event_id" type="event" :id="action.origin_event_id" :scene-id="trace.scene_id" label="本条请求来源" /></div>
      </article>
    </section>
    <BudgetDetails v-for="item in budgets" :key="item.index" :budget="item.budget" :title="`执行段 ${item.index} 的预算快照`" />
    <OperationReceipts :items="trace.operation_receipts || []" :scene-id="trace.scene_id" />
    <section v-if="checkpoints.length"><h3>逐阶段提交</h3><article v-for="checkpoint in checkpoints" :key="checkpoint.index" class="candidate-message"><strong>Checkpoint {{ checkpoint.index }} · {{ {end:'本轮结束',continue:'继续执行',wait:'等待外部回应'}[checkpoint.result.next_action] }}</strong><div class="trace-links"><EntityLink type="event" :id="checkpoint.gate.commit_event_id" :scene-id="trace.scene_id" label="查看本阶段提交与回执" /><span>{{ checkpoint.gate.actions_enqueued }} 条已入队</span></div><p v-if="checkpoint.gate.publication?.error" class="text-error">{{ checkpoint.gate.publication.error }}</p><p v-for="(message,index) in checkpoint.result.message_proposals" :key="index">{{ messageText(message) }}</p><SourceOutcomes :items="checkpoint.result.source_outcomes" :scene-id="trace.scene_id" /></article></section>
    <SourceOutcomes v-else :items="trace.payload.result?.source_outcomes" :scene-id="trace.scene_id" />
    <section v-if="isConversation"><h3>终结候选</h3><p class="muted">候选表达与真实送达分别记录，下方内容不代表已经发到群聊。</p><article v-for="(message,index) in messages" :key="index" class="candidate-message"><span class="muted">第 {{ index+1 }} 条候选</span><p>{{ messageText(message) }}</p><div class="trace-links"><EntityLink v-if="messageSource(message)" type="event" :id="messageSource(message)" :scene-id="trace.scene_id" label="本条请求来源" /><span v-else-if="message.source" class="muted">本轮来源引用 {{ message.source }}，没有保存可回查的原话映射</span><span v-else class="muted">本条请求来源未记录</span><span v-if="message.requester_qq_uid">请求者 QQ {{ message.requester_qq_uid }}</span><span v-if="message.addressed_to?.length">回应对象 {{ message.addressed_to.join('、') }}</span><span v-if="message.expect_reply">等待 {{ message.reply_target || message.expect_reply.target }} 回应</span><v-chip v-if="message.operation_ref" size="small" variant="tonal">操作确认引用 {{ message.operation_ref }}</v-chip><span v-if="message.job_revision">消息绑定版本 {{ message.job_revision }}</span></div></article><p v-if="!messages.length">{{ candidate && Array.isArray(candidate.messages)?'本候选没有消息，表示模型选择沉默。':'没有可确认的消息候选。' }}</p><ResourceViewer v-if="trace.payload.result?.handled_source_event_ids" title="本轮提交处理的原话 ID" :content="trace.payload.result.handled_source_event_ids" /></section>
    <section v-else-if="isMemoryRetrieval"><h3>认识召回过程</h3><dl class="context-estimate"><dt>场景</dt><dd>{{ trace.scene_id }}</dd><dt>查询</dt><dd>{{ trace.payload.query }}</dd><dt>主体筛选</dt><dd>{{ trace.payload.subject || '未指定' }}</dd><dt>词面候选</dt><dd>{{ trace.payload.lexical_candidates }}</dd><dt>语义候选</dt><dd>{{ trace.payload.semantic_candidates }}</dd><dt>重排</dt><dd>{{ trace.payload.rerank ? '已执行' : '未执行' }}</dd><dt>覆盖</dt><dd>{{ trace.payload.index_partial ? 'partial' : '完整' }}</dd></dl><ResourceViewer title="最终呈现的认识 ID" :content="trace.payload.presented_memory_ids" /></section>
    <section v-else-if="isPlugin">
      <h3>插件执行</h3>
      <PluginOrigin :origin="trace.plugin_origin" :name="trace.plugin_name" :scene-id="trace.scene_id" />
      <div v-if="trace.payload.job_id" class="trace-links"><EntityLink type="job" :id="trace.payload.job_id" :scene-id="trace.scene_id" label="所属工作与当前交付" /><EntityLink v-if="trace.payload.artifact_result_id" type="result" :id="trace.payload.artifact_result_id" :scene-id="trace.scene_id" label="本次交付成品" /></div>
      <p>认领、完成处理与真实送达分别记录；以下提交仍需沿行动回执核对。</p>
      <article v-for="(commit,index) in pluginCommits" :key="index" class="candidate-message">
        <EntityLink v-if="commit.commit_event_id" type="event" :id="commit.commit_event_id" :scene-id="trace.scene_id" label="已提交阶段与回执" />
        <p>{{ commit.reason }} · 入队 {{ commit.actions_enqueued }} 条</p>
        <p v-if="commit.publication?.error" class="text-error">{{ commit.publication.error }}</p>
      </article>
      <ResourceViewer v-if="candidate" title="插件 Agent 的终结候选" :content="candidate" />
    </section>
    <section v-else><h3>{{ trace.kind.startsWith('agent_job')?'工作执行结果':'已保存结果' }}</h3><p class="muted">执行、提交和送达分别保存，实际发送见关联回执。</p><ResourceViewer v-if="trace.payload.result" title="已记录结果" :content="trace.payload.result" /><ResourceViewer v-else-if="candidate" title="终结候选" :content="candidate" /><p v-else class="muted">没有保存可读取的结果，具体执行过程见下方记录。</p></section>
    <section v-if="directTools.length"><h3>直接工具读取</h3>
      <article v-for="(call,index) in directTools" :key="index" class="tool-step">
        <strong>{{ call.name }}</strong><StatusBadge domain="observation" :status="call.status" />
        <EntityLink type="result" :id="call.result_id" :scene-id="trace.scene_id" label="读取原始工具资料" />
        <ObservationDetails v-if="call.stored_observation" :observation="call.stored_observation" :scene-id="trace.scene_id" />
      </article>
    </section>
    <section v-if="hooks.length"><h3>插件钩子</h3><article v-for="(hook,index) in hooks" :key="index" class="tool-step">
      <strong>{{ hook.plugin_id }} / {{ hook.id }} · {{ hook.phase }}</strong><p>{{ hook.state }} · {{ hook.scope }}</p>
      <p v-if="hook.error" class="text-error">{{ hook.error }}</p><ResourceViewer v-if="hook.before" title="处理前" :content="hook.before" /><ResourceViewer v-if="hook.after" title="处理后" :content="hook.after" />
    </article></section>
    <section v-if="references.length"><h3>本轮实际读取与资料定位</h3><article v-for="reference in references" :key="reference.index" class="reference-record"><p>第 {{ reference.index }} 段执行：完整读取 {{ reference.read_messages?.length ?? '未记录' }} 条原话；已登记 {{ Object.keys(reference.messages || {}).length }} 条原话位置。</p><ResourceViewer v-if="reference.read_ranges" title="实际原话读取范围" :content="reference.read_ranges" /><div class="trace-links"><EntityLink v-for="id in reference.read_messages || []" :key="id" type="event" :id="id" :scene-id="trace.scene_id" label="已读原话" /><EntityLink v-for="(id,ref) in reference.results || {}" :key="ref" type="result" :id="id" :scene-id="trace.scene_id" :label="ref + ' · 资料定位'" /></div><p class="muted">资料位置只说明可以回读；正文采用范围见各步骤的展示记录。</p></article></section>
    <section><h3>模型与工具步骤</h3><v-expansion-panels variant="accordion"><v-expansion-panel v-for="(step,index) in steps" :key="index"><v-expansion-panel-title><div class="step-title"><strong>第 {{ index+1 }} 步</strong><span v-if="step.run_revision">目标版本 {{ step.run_revision }}</span><span>{{ step.model || '型号未记录' }}</span><span class="muted">{{ step.latency_ms ?? '—' }} ms</span><StatusBadge v-for="call in (step.tool_calls || []).filter(call=>['error','unsupported'].includes(observationStatus(call)))" :key="call.id" domain="observation" :status="observationStatus(call)" /></div></v-expansion-panel-title><v-expansion-panel-text>
      <EntityLink v-if="step.call_id" type="call" :id="step.call_id" :scene-id="trace.scene_id" label="本次模型请求与实际用量" />
      <p v-if="step.failure_reason" class="readable-copy text-error">{{ step.failure_reason }}</p>
      <BudgetDetails v-if="step.budget" :budget="step.budget" title="本次模型请求使用的额度" />
      <section v-if="step.local_estimate" class="context-estimate"><h4>本次请求的上下文估算</h4><p>输入 {{ step.local_estimate.input_tokens }} tokens · 图片 {{ step.local_estimate.image_count }} 张。估算与供应商实际用量分别记录。</p><dl><template v-for="(value,key) in step.local_estimate.parts" :key="key"><dt>{{ estimateLabels[key] || key }}</dt><dd>{{ value }} tokens</dd></template></dl></section>
      <ResourceViewer v-if="step.context_plan" title="实际上下文分配与省略原因" :content="step.context_plan" />
      <ResourceViewer v-if="step.presentations || step.presented_ranges" title="本次模型请求实际采用范围" :content="step.presentations || step.presented_ranges" />
      <article v-for="call in step.tool_calls || []" :key="call.id" class="tool-step"><div class="tool-heading"><strong>{{ call.name }}</strong><span>{{ call.status==='completed'?'调用已返回':call.status || '调用状态未记录' }}</span><StatusBadge v-if="observationStatus(call)" domain="observation" :status="observationStatus(call)" /><code v-if="call.error_code || call.observation?.error_code">{{ call.error_code || call.observation.error_code }}</code></div><p class="argument-summary">参数：{{ argumentSummary(call.arguments) }}</p><v-chip v-if="call.committed===false" size="small" variant="tonal">此候选未提交</v-chip><p v-if="call.failure_reason && !call.stored_observation" class="text-error readable-copy">{{ call.failure_reason }}</p><EntityLink v-if="call.observation?.result_id" type="result" :id="call.observation.result_id" :scene-id="trace.scene_id" label="读取已保存的完整工具资料" /><ObservationDetails v-if="call.stored_observation" :observation="call.stored_observation" :scene-id="trace.scene_id" /><ObservationDetails v-else-if="call.observation && ['error','unsupported'].includes(observationStatus(call))" :observation="call.observation" :scene-id="trace.scene_id" /><p v-else-if="call.observation?.result_id" class="muted">已记录资料 ID，但该场景中的保存正文目前不可读取。</p><ResourceViewer v-if="call.presentations || call.presented_ranges" title="本条观察实际采用范围" :content="call.presentations || call.presented_ranges" /><ResourceViewer v-if="call.receipt" title="工具暂存回执（不代表操作已提交）" :content="call.receipt" /><details><summary>参数与调用原始记录</summary><ResourceViewer title="工具调用" :content="call" /></details></article>
      <PluginOrigin v-if="step.run_origin" :origin="step.run_origin" :scene-id="trace.scene_id" /><details><summary>完整步骤记录</summary><ResourceViewer title="步骤原始记录" :content="step" /></details>
    </v-expansion-panel-text></v-expansion-panel><p v-if="!steps.length" class="muted">这份记录没有模型步骤。</p></v-expansion-panels></section>
    <v-expansion-panels variant="accordion"><v-expansion-panel title="完整脱敏记录"><v-expansion-panel-text><ResourceViewer title="Trace" :content="trace.payload" /></v-expansion-panel-text></v-expansion-panel></v-expansion-panels>
  </div>
</template>
<style scoped>.trace-details{display:grid;gap:24px;min-width:0}.trace-facts,.trace-links{display:flex;gap:12px;flex-wrap:wrap;font-size:13px}.trace-links{margin-top:12px}.trace-details h3{font-size:15px;margin:0 0 10px}.trace-details p{font-size:13px;line-height:1.7}.candidate-message{border:1px solid var(--line);border-radius:8px;padding:12px;margin-top:12px}.candidate-message p{white-space:pre-wrap;overflow-wrap:anywhere;margin:8px 0 0}.step-title{display:flex;gap:12px;flex-wrap:wrap;min-width:0;font-size:12px;overflow-wrap:anywhere}.tool-step{display:grid;gap:12px;font-size:12px;padding:18px 0;margin-bottom:12px;border-bottom:1px solid var(--line)}.tool-heading{display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px}.tool-step p{margin:0}.argument-summary{overflow-wrap:anywhere;white-space:pre-wrap;max-height:7.5em;overflow:auto}.context-estimate{margin:20px 0;padding:14px;background:rgb(var(--v-theme-surface-variant));border-radius:8px}.context-estimate h4{font-size:13px}.context-estimate dl{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px 16px;font-size:12px}.context-estimate dd{margin:0}.context-estimate dt{overflow-wrap:anywhere}.trace-details summary{cursor:pointer;color:rgb(var(--v-theme-primary));font-size:13px}.trace-details details[open]>summary{margin-bottom:12px}.reference-record{display:grid;gap:10px;padding:12px 0}</style>
