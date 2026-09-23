<script setup>
import { computed } from 'vue'
import { messageProgress, supportsMessageProgress } from '../domain/messageProgress.js'
import { fmtTime } from '../api.js'
import EntityLink from './EntityLink.vue'
import StatusBadge from './StatusBadge.vue'
import AnswerBasisDetails from './AnswerBasisDetails.vue'
import TraceTimings from './TraceTimings.vue'
import { purposeLabel, formatDurationMs } from '../domain/activity.js'
const props = defineProps({ event: Object, relations: Object, loading: Boolean })
const applicable = computed(() => supportsMessageProgress(props.event))
const progress = computed(() => messageProgress(props.event, props.relations))
const states = {
  recorded: { text: '已有记录', color: 'info' }, waiting: { text: '等待中', color: 'info' },
  skipped: { text: '正常分支', color: 'default' }, partial: { text: '未完成', color: 'warning' },
  failed: { text: '有失败', color: 'error' }, unknown: { text: '未确认', color: 'default' },
}
const phaseLabel = value => ({ pre_commit: '首次提交前', after_checkpoint: '阶段提交之后', post_commit: '事务提交之后' }[value] || value || '阶段未单独记录')
const number = value => Number.isFinite(value) && value >= 0 ? value.toLocaleString() : '未记录'
const duration = call => Number.isFinite(call.started_at) && Number.isFinite(call.ended_at) && call.ended_at >= call.started_at
  ? `${(call.ended_at - call.started_at).toFixed(2)} 秒` : '未记录完整起止'
const requests = call => progress.value.requestRecords.filter(record => record.call_id === call.id)
</script>

<template>
  <section v-if="applicable" class="message-progress" aria-label="消息处理阶段">
    <h3>这条消息到了哪一步</h3>
    <p v-if="loading" class="progress-note" role="status">正在读取关联，以下仅按已经取得的记录显示。</p>
    <p class="progress-note">按明确来源与行动身份显示；可能含多个处理轮次，不把同轮其他请求混入。没有记录不等于没有执行。</p>
    <EntityLink v-if="progress.sourceId && progress.sourceId!==event?.id" type="event" :id="progress.sourceId" :scene-id="event.scene_id" label="本条表达的直接来源" />
    <p v-if="progress.limited" class="progress-note">关联已截断；这里只统计本页记录，不代表完整历史。</p>
    <ol class="progress-steps"><li v-for="(item,index) in progress.steps" :key="item.name">
      <span class="step-number" aria-hidden="true">{{ index+1 }}</span>
      <div class="step-body"><div class="step-heading"><h4>{{ item.name }}</h4><v-chip size="x-small" variant="tonal" :color="states[item.state].color">{{ states[item.state].text }}</v-chip></div><strong class="step-summary">{{ item.summary }}</strong><p v-if="item.detail">{{ item.detail }}</p><EntityLink v-if="index===2 && progress.handlingTurn" type="event" :id="progress.handlingTurn.event_id" :scene-id="event.scene_id" label="查看对应处理提交" />
        <template v-if="index===3">
          <div v-for="job in progress.jobs" :key="job.id" class="progress-work"><EntityLink type="job" :id="job.id" :scene-id="event.scene_id" :label="job.goal" /><div class="step-heading"><StatusBadge domain="job_execution" :status="job.execution_status" /><StatusBadge domain="job_delivery" :status="job.delivery_required === false ? 'not_required' : job.status" /></div><p>工作当前版本 v{{ job.revision }}，不是答复当时采用版本。</p></div>
          <details v-if="!progress.isReceipt && progress.actions.length"><summary>查看对应表达的依据（{{ progress.actions.length }}）</summary><article v-for="(action,position) in progress.actions" :key="action.id"><p>关联表达 {{ position+1 }}</p><AnswerBasisDetails :basis="action.answer_basis" :scene-id="event.scene_id" /><EntityLink v-if="action.commit_event_id" type="event" :id="action.commit_event_id" :scene-id="event.scene_id" label="查看本条表达提交" /></article></details>
        </template>
        <details v-if="index===5 && progress.actions.some(action=>action.receipt_event_ids?.length)"><summary>查看对应回执</summary><div v-for="(action,position) in progress.actions" :key="action.id" class="progress-links"><EntityLink v-for="id in action.receipt_event_ids || []" :key="id" type="event" :id="id" :scene-id="event.scene_id" :label="`表达 ${position+1}：查看已保存记录`" /></div></details>
        <template v-if="index===5 && progress.deliveryProblems.length"><p>本页保存的未成功或未知记录（不覆盖后续回执）：</p><article v-for="receipt in progress.deliveryProblems" :key="receipt.id" class="delivery-problem"><div class="step-heading"><StatusBadge domain="delivery" :status="receipt.delivery_status" /><StatusBadge v-if="receipt.simulated" domain="delivery" status="simulated" /><span>{{ fmtTime(receipt.timestamp) }}</span></div><p>{{ receipt.payload.error }}</p><EntityLink type="event" :id="receipt.id" :scene-id="event.scene_id" label="查看这份发送记录" /></article></template>
      </div>
    </li></ol>
    <section class="progress-calls" aria-label="关联处理与发送耗时">
      <h4>关联处理与发送耗时</h4>
      <p class="progress-note">处理计时属于关联轮次，同轮可能包含多条来源。等待、并行与嵌套阶段不能相加；没有记录不代表零耗时。</p>
      <p v-if="!progress.attempts.length" class="progress-note">本页未取得明确关联的处理轨迹，阶段耗时未确认。</p>
      <details v-for="attempt in progress.attempts" :key="attempt.id">
        <summary>{{ fmtTime(attempt.created_at) }} · {{ attempt.kind === 'conversation_error' ? '有异常的处理轨迹' : '处理轨迹' }}</summary>
        <TraceTimings :timings="attempt.timings" :scene-id="event.scene_id" />
        <EntityLink type="trace" :id="attempt.id" :scene-id="event.scene_id" label="查看原轨迹与阶段状态" />
      </details>
      <p class="progress-note">发送排队从本次入队计至开始发送，包含准备、校验与等待发送名额；发送处理计时包含尝试登记和适配器返回。原记录起点可能是该轮最早来源，不是本条消息的独占起点，也不证明成功送达。</p>
      <p v-if="!progress.deliveryReceipts.length" class="progress-note">本页未取得归属这些行动的发送结果，发送计时未确认。</p>
      <article v-for="receipt in progress.deliveryReceipts" :key="receipt.id" class="progress-call">
        <div class="step-heading"><StatusBadge domain="delivery" :status="receipt.delivery_status" /><StatusBadge v-if="receipt.simulated" domain="delivery" status="simulated" /><time>{{ fmtTime(receipt.timestamp) }}</time></div>
        <dl class="call-metrics">
          <div><dt>发送前排队与准备</dt><dd>{{ formatDurationMs(receipt.payload.queue_ms) }}</dd></div>
          <div><dt>发送处理至适配器返回</dt><dd>{{ formatDurationMs(receipt.payload.send_ms) }}</dd></div>
          <div><dt>原记录起点至本次回执</dt><dd>{{ formatDurationMs(receipt.payload.event_to_delivery_ms) }}</dd></div>
        </dl>
        <EntityLink type="event" :id="receipt.id" :scene-id="event.scene_id" label="核对原回执与计时字段" />
      </article>
    </section>
    <section class="progress-calls" aria-label="关联轮次的模型调用">
      <h4>关联轮次的模型调用 <span>{{ progress.calls.length }}</span></h4>
      <p class="progress-note">按本条来源的处理轮次或表达所属轮次关联，按调用开始时间排列。同轮可能处理多条消息，用量不能归为本条独占；后台工作的调用请从工作详情查看。</p>
      <p v-if="!progress.calls.length" class="progress-note">本页未取得明确归属轮次的调用记录，不能据此判断没有调用或没有费用。</p>
      <p v-else class="progress-note">耗时来自调用账的起止时间，不是消息到送达的总延迟。供应商用量与本地估算分别显示；缓存是输入子项，缺失值不按零计。金额未核实。</p>
      <article v-for="call in progress.calls" :key="call.id" class="progress-call">
        <div class="step-heading"><strong>{{ purposeLabel(call.purpose) }}</strong><StatusBadge domain="call" :status="call.status" /><time>{{ fmtTime(call.started_at) }}</time></div>
        <p class="progress-note">{{ call.provider_id }} / {{ call.model }}</p>
        <dl class="call-metrics">
          <div><dt>调用耗时</dt><dd>{{ duration(call) }}</dd></div>
          <div><dt>供应商输入 tokens</dt><dd>{{ number(call.usage?.prompt_tokens) }}</dd></div>
          <div><dt>供应商输出 tokens</dt><dd>{{ number(call.usage?.completion_tokens) }}</dd></div>
          <div><dt>其中缓存输入 tokens</dt><dd>{{ number(call.usage?.prompt_tokens_details?.cached_tokens) }}</dd></div>
          <div><dt>本地估算输入 tokens</dt><dd>{{ number(call.estimate?.input_tokens) }}</dd></div>
          <div v-if="call.usage?.type==='duration'"><dt>供应商音频用量</dt><dd>{{ number(call.usage.seconds) }} 秒</dd></div>
        </dl>
        <p v-if="call.error_type" class="call-error">{{ call.error_type }}</p>
        <details><summary>轮次轨迹中的装配摘要</summary>
          <p class="progress-note">仅展示轨迹中与本次调用编号明确关联的装配清单；位置、图像和工具数量不证明逐项采用，也不能逐字还原请求。提示与工具定义版本尚未完整留存。</p>
          <p v-if="!requests(call).length" class="progress-note">轮次轨迹未提供该调用的装配摘要；不以本轮最后一份清单替代。调用登记时的独立材料请从调用详情查看。</p>
          <div v-for="(request,index) in requests(call)" :key="`${request.trace_id}:${index}`" class="progress-links">
            <p class="progress-note">消息位置 {{ number(request.message_count) }} · 标记省略 {{ number(request.omitted_message_count) }} · 图像资产 {{ number(request.pixel_asset_count) }} · 工具定义 {{ number(request.tool_count) }}</p>
            <EntityLink type="trace" :id="request.trace_id" :scene-id="event.scene_id" label="查看已保存的清单与来源范围" />
          </div>
        </details>
        <div class="progress-links"><EntityLink type="call" :id="call.id" :scene-id="event.scene_id" label="查看调用与登记材料" /><EntityLink type="episode" :id="call.episode_id" :scene-id="event.scene_id" label="查看所属轮次" /></div>
      </article>
    </section>
    <details v-if="progress.problems.length" class="progress-problems"><summary>本页另有 {{ progress.problems.length }} 份关联轮次问题记录</summary><p class="progress-note">这些轮次明确包含本条来源或表达提交；整轮错误不等于本条最终失败，也不撤销先前的提交或送达。按原记录逐次核对，不按时间猜因果。</p><article v-for="problem in progress.problems" :key="problem.id"><strong>{{ fmtTime(problem.created_at) }} · {{ phaseLabel(problem.error_phase) }}</strong><p v-if="problem.error">{{ problem.error }}</p><p v-if="problem.publication_error">发布步骤 {{ problem.publication_phase || '未记录' }}：{{ problem.publication_error }}</p><EntityLink type="trace" :id="problem.id" :scene-id="event.scene_id" label="查看这次轮次的问题与调用" /></article></details>
  </section>
</template>

<style scoped>
.message-progress{min-width:0;margin:18px 0}.message-progress h3{font-size:15px;margin:0 0 10px}.progress-note{font-size:12px;color:var(--muted);line-height:1.7;margin:8px 0}.progress-steps{list-style:none;padding:0;margin:16px 0}.progress-steps>li{display:flex;gap:12px;padding:0 0 18px;min-width:0}.step-number{display:grid;place-items:center;flex:none;width:24px;height:24px;border:1px solid var(--line);border-radius:50%;font-size:12px;color:var(--muted)}.step-body{min-width:0;flex:1}.step-heading{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.step-heading h4{font-size:12px;margin:0;color:var(--muted)}.step-summary{display:block;font-size:13px;line-height:1.7;margin-top:6px;overflow-wrap:anywhere}.step-body p{font-size:12px;color:var(--muted);line-height:1.7;white-space:pre-wrap;overflow-wrap:anywhere;margin:6px 0}.step-body :deep(.entity-link){font-size:12px}
.message-progress summary{cursor:pointer;font-size:12px;line-height:1.7}.message-progress details{margin-top:10px}.progress-work,.progress-problems article{border-top:1px solid var(--line);padding:12px 0;margin-top:10px;font-size:12px;min-width:0}.progress-work .step-heading{margin-top:8px}.progress-links{display:grid;gap:8px;margin:10px 0;min-width:0}.progress-problems p{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;line-height:1.7}.progress-problems strong{font-size:12px}
.delivery-problem{border-left:2px solid var(--line);padding-left:12px;margin:10px 0;font-size:12px}.delivery-problem .step-heading>span{color:var(--muted)}
.progress-calls{border-top:1px solid var(--line);padding-top:16px}.progress-calls h4{font-size:13px;margin:0}.progress-calls h4 span,.progress-call time{color:var(--muted);font-weight:400}.progress-call{border-top:1px solid var(--line);padding:14px 0;font-size:12px;min-width:0}.call-metrics{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin:12px 0}.call-metrics dt{color:var(--muted);font-size:11px}.call-metrics dd{margin:4px 0 0;overflow-wrap:anywhere}.call-error{color:rgb(var(--v-theme-error));white-space:pre-wrap;overflow-wrap:anywhere}.progress-call .progress-note{overflow-wrap:anywhere}.progress-call :deep(.entity-link){font-size:12px}
@media(max-width:600px){.call-metrics{grid-template-columns:minmax(0,1fr)}}
</style>
