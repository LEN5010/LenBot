<script setup>
import { computed } from 'vue'
import { messageProgress, supportsMessageProgress } from '../domain/messageProgress.js'
import { fmtTime } from '../api.js'
import EntityLink from './EntityLink.vue'
import StatusBadge from './StatusBadge.vue'
import AnswerBasisDetails from './AnswerBasisDetails.vue'
const props = defineProps({ event: Object, relations: Object, loading: Boolean })
const applicable = computed(() => supportsMessageProgress(props.event))
const progress = computed(() => messageProgress(props.event, props.relations))
const states = {
  recorded: { text: '已有记录', color: 'info' }, waiting: { text: '等待中', color: 'info' },
  skipped: { text: '正常分支', color: 'default' }, partial: { text: '未完成', color: 'warning' },
  failed: { text: '有失败', color: 'error' }, unknown: { text: '未确认', color: 'default' },
}
const phaseLabel = value => ({ pre_commit: '首次提交前', after_checkpoint: '阶段提交之后', post_commit: '事务提交之后' }[value] || value || '阶段未单独记录')
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
    <details v-if="progress.problems.length" class="progress-problems"><summary>本页另有 {{ progress.problems.length }} 份关联轮次问题记录</summary><p class="progress-note">这些轮次明确包含本条来源或表达提交；整轮错误不等于本条最终失败，也不撤销先前的提交或送达。按原记录逐次核对，不按时间猜因果。</p><article v-for="problem in progress.problems" :key="problem.id"><strong>{{ fmtTime(problem.created_at) }} · {{ phaseLabel(problem.error_phase) }}</strong><p v-if="problem.error">{{ problem.error }}</p><p v-if="problem.publication_error">发布步骤 {{ problem.publication_phase || '未记录' }}：{{ problem.publication_error }}</p><EntityLink type="trace" :id="problem.id" :scene-id="event.scene_id" label="查看这次轮次的问题与调用" /></article></details>
  </section>
</template>

<style scoped>
.message-progress{min-width:0;margin:18px 0}.message-progress h3{font-size:15px;margin:0 0 10px}.progress-note{font-size:12px;color:var(--muted);line-height:1.7;margin:8px 0}.progress-steps{list-style:none;padding:0;margin:16px 0}.progress-steps>li{display:flex;gap:12px;padding:0 0 18px;min-width:0}.step-number{display:grid;place-items:center;flex:none;width:24px;height:24px;border:1px solid var(--line);border-radius:50%;font-size:12px;color:var(--muted)}.step-body{min-width:0;flex:1}.step-heading{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.step-heading h4{font-size:12px;margin:0;color:var(--muted)}.step-summary{display:block;font-size:13px;line-height:1.7;margin-top:6px;overflow-wrap:anywhere}.step-body p{font-size:12px;color:var(--muted);line-height:1.7;white-space:pre-wrap;overflow-wrap:anywhere;margin:6px 0}.step-body :deep(.entity-link){font-size:12px}
.message-progress summary{cursor:pointer;font-size:12px;line-height:1.7}.message-progress details{margin-top:10px}.progress-work,.progress-problems article{border-top:1px solid var(--line);padding:12px 0;margin-top:10px;font-size:12px;min-width:0}.progress-work .step-heading{margin-top:8px}.progress-links{display:grid;gap:8px;margin:10px 0;min-width:0}.progress-problems p{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;line-height:1.7}.progress-problems strong{font-size:12px}
.delivery-problem{border-left:2px solid var(--line);padding-left:12px;margin:10px 0;font-size:12px}.delivery-problem .step-heading>span{color:var(--muted)}
</style>
