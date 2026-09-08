<script setup>
import { fmtTime } from '../api.js'
import EntityLink from './EntityLink.vue'
import StatusBadge from './StatusBadge.vue'

defineProps({ items: { type: Array, required: true }, sceneId: String })
const kinds = { work: '信息工作', reminder: '提醒', memory: '认识' }
const operations = { create: '新建', revise: '修订', resume: '恢复', cancel: '取消', update: '修改', refute: '撤销', supersede: '替换' }
const targets = { work: 'job', reminder: 'task', memory: 'memory' }
const domains = { work: 'job_delivery', reminder: 'task', memory: 'memory' }
</script>

<template>
  <section v-if="items.length" class="operation-receipts" aria-label="已提交操作的真实回执">
    <h3>操作提交回执</h3>
    <p class="muted">这里记录操作事务的结果；随附确认消息是否送达，仍看行动回执。</p>
    <article v-for="item in items" :key="`${item.commit_event_id}:${item.proposal_ref}`">
      <div class="receipt-heading"><strong>{{ operations[item.operation] || item.operation }}{{ kinds[item.kind] || item.kind }}</strong><v-chip size="small" :color="item.status==='committed'?'success':undefined" variant="tonal">{{ item.status==='committed'?'操作已提交':item.status || '状态未记录' }}</v-chip><code>{{ item.proposal_ref }}</code><span v-if="item.revision!==null && item.revision!==undefined">提交后版本 {{ item.revision }}</span><StatusBadge v-if="item.result_status && domains[item.kind]" :domain="domains[item.kind]" :status="item.result_status" /></div>
      <div class="receipt-links"><EntityLink v-if="targets[item.kind]" :type="targets[item.kind]" :id="item.target_id" :scene-id="sceneId" :label="`${kinds[item.kind]} ${item.target_id}`" /><span v-else>{{ item.target_id }}</span><EntityLink v-if="item.commit_event_id" type="event" :id="item.commit_event_id" :scene-id="sceneId" label="对应事务提交事件" /><span v-if="item.committed_at">{{ fmtTime(item.committed_at) }}</span></div>
      <p v-if="item.action_id" class="action-id">随附确认行动：{{ item.action_id }}</p><p v-else class="muted">本次操作没有随附确认消息。</p>
      <p v-if="item.kind==='reminder' && item.reminder_description" class="action-id">本次提交的提醒内容：{{ item.reminder_description }}</p>
      <p v-if="item.kind==='reminder' && item.reminder_due_at!==null && item.reminder_due_at!==undefined">本次提交的提醒时间：{{ fmtTime(item.reminder_due_at) }}</p>
      <div v-if="item.source_event_ids?.length" class="receipt-links"><EntityLink v-for="id in item.source_event_ids" :key="id" type="event" :id="id" :scene-id="sceneId" label="操作依据原话" /></div>
    </article>
  </section>
</template>

<style scoped>
.operation-receipts{display:grid;gap:12px;font-size:13px;line-height:1.65;min-width:0}.operation-receipts h3{font-size:15px;margin:0}.operation-receipts p{margin:0}.operation-receipts article{padding:14px;border:1px solid var(--line);border-radius:8px;display:grid;gap:10px;min-width:0}.receipt-heading,.receipt-links{display:flex;align-items:center;flex-wrap:wrap;gap:8px 12px}.receipt-links>span{color:var(--muted)}.action-id{overflow-wrap:anywhere}.receipt-heading code{overflow-wrap:anywhere}
</style>
