<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { mdiClose } from '@mdi/js'
import { fmtTime, attentionReason } from '../api.js'
import EntityLink from './EntityLink.vue'
import StatusBadge from './StatusBadge.vue'
import ResourceViewer from './ResourceViewer.vue'

const props = defineProps({ event: Object, relations: Object, loading: Boolean, error: String, readAt: Number, active: Boolean, modal: Boolean })
defineEmits(['close', 'retry'])
const container = ref(null)
let returnFocus
function restoreFocus() { if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true }); returnFocus = null }
watch(() => props.active, async active => {
  if (!active) { restoreFocus(); return }
  returnFocus = document.activeElement
  await nextTick()
  if (props.active) container.value?.querySelector('button')?.focus({ preventScroll: true })
}, { immediate: true })
onBeforeUnmount(restoreFocus)
function trapFocus(event) {
  if (!props.modal || !props.active || event.key !== 'Tab') return
  const targets = [...container.value.querySelectorAll('button:not([disabled]), a[href], input:not([disabled]), [tabindex]:not([tabindex="-1"])')].filter(item => item.getClientRects().length)
  const first = targets[0], last = targets.at(-1)
  if (!first) { event.preventDefault(); container.value.focus(); return }
  if (event.shiftKey && (document.activeElement === first || document.activeElement === container.value)) { event.preventDefault(); last.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
}
const hasAttention = computed(() => props.event && Object.hasOwn(props.event.attention, 'attention_reasons'))
const readTurns = computed(() => (props.relations?.events || []).filter(item => item.event_type === 'CONVERSATION_COMMITTED' && item.payload.source_event_ids?.includes(props.event?.id)))
const truncated = computed(() => Object.entries(props.relations?.truncated || {}).filter(([, value]) => value).map(([key]) => ({ events: '事件', traces: '轨迹', calls: '模型请求', jobs: '工作', actions: '行动', tool_results: '工具资料' }[key])))
const disposition = value => ({ SILENCE: 'silence', ACTION: 'expression' }[value] || value)
</script>

<template>
  <section ref="container" class="scene-inspector" :role="modal ? 'dialog' : 'region'" :aria-modal="modal && active ? 'true' : undefined" tabindex="-1" aria-label="消息关联检查" @keydown="trapFocus" @keydown.esc.stop.prevent="$emit('close')">
    <header class="inspector-header"><h2>消息与关联</h2><v-btn :icon="mdiClose" variant="text" aria-label="关闭关联检查，返回消息" @click="$emit('close')" /></header>
    <div class="inspector-body">
      <v-progress-linear v-if="loading" indeterminate aria-label="正在读取消息关联" />
      <v-alert v-if="error" type="error" variant="tonal">{{ error }}<div class="mt-3"><v-btn variant="outlined" size="small" :loading="loading" @click="$emit('retry')">重新读取</v-btn></div></v-alert>
      <template v-if="event">
        <div class="inspector-identity"><strong>{{ event.display_name || event.actor_id }}</strong><time>{{ fmtTime(event.timestamp) }}</time><EntityLink type="event" :id="event.id" :scene-id="event.scene_id" /><span>原始位置 {{ event.rowid }} · {{ event.event_type }}</span></div>
        <div class="inspector-badges"><StatusBadge v-if="event.delivery_status" domain="delivery" :status="event.delivery_status" /><StatusBadge v-if="event.simulated" domain="delivery" status="simulated" /></div>
        <ResourceViewer title="消息全文" :content="event.payload.raw_text ?? event.payload.content ?? '此事件没有文字正文。'" />
        <h3>注意力与本轮读取</h3>
        <template v-if="hasAttention"><p v-if="event.attention.attention_reasons.length">{{ event.attention.attention_reasons.map(attentionReason).join(' · ') }}</p><p v-else><StatusBadge domain="attention" status="stored_only" /> 未产生独立唤醒</p><v-chip v-if="event.attention.attention_reasons.length" size="small" variant="tonal">{{ Object.hasOwn(event.attention, 'attention_certain') ? (event.attention.attention_certain ? '确定唤醒来源' : '观察机会') : '未记录唤醒确定性' }}</v-chip></template>
        <p v-else class="muted-copy">没有保存注意力判定。</p>
        <article v-for="turn in readTurns" :key="turn.id" class="relation-record"><div class="inspector-badges"><StatusBadge domain="attention" status="read" /><StatusBadge domain="attention" :status="disposition(turn.payload.outcome?.disposition)" /></div><p class="preserve-lines">{{ turn.payload.outcome?.decision_reason }}</p><EntityLink type="event" :id="turn.id" :scene-id="event.scene_id" label="查看已提交的本轮结果" /></article>
        <p v-if="relations && !readTurns.length" class="muted-copy">本次关联结果中没有已提交的原话读取记录。</p>
        <template v-if="relations">
          <v-alert v-if="truncated.length" type="info" variant="tonal" class="mt-4">{{ truncated.join('、') }}超过本次最多 50 项的读取范围。</v-alert>
          <h3>已有工作 <span>{{ relations.jobs.length }}</span></h3><div class="inspector-links"><div v-for="job in relations.jobs" :key="job.id"><EntityLink type="job" :id="job.id" :scene-id="event.scene_id" :label="job.goal" /><div class="inspector-badges"><StatusBadge domain="job_execution" :status="job.execution_status" /><StatusBadge domain="job_delivery" :status="job.status" /></div></div></div>
          <h3>表达行动与回执 <span>{{ relations.actions.length }}</span></h3><article v-for="action in relations.actions" :key="action.id" class="relation-record"><code class="breakable">{{ action.id }}</code><div class="inspector-badges"><StatusBadge domain="delivery" :status="action.delivery_status" /><StatusBadge v-if="action.simulated" domain="delivery" status="simulated" /></div><p v-if="!action.receipt_event_ids.length" class="muted-copy">尚无关联回执。</p><div class="inspector-links"><EntityLink v-for="id in action.receipt_event_ids" :key="id" type="event" :id="id" :scene-id="event.scene_id" label="查看原始回执" /></div></article>
          <h3>轮次与执行轨迹 <span>{{ relations.traces.length }}</span></h3><div class="inspector-links"><RouterLink v-for="trace in relations.traces" :key="trace.id" :to="{ name: 'activity', query: { tab: 'turns', id: trace.id, scene: event.scene_id } }">{{ trace.kind }} · {{ trace.id }}</RouterLink></div>
          <h3>模型请求 <span>{{ relations.calls.length }}</span></h3><div class="inspector-links"><EntityLink v-for="call in relations.calls" :key="call.id" type="call" :id="call.id" :scene-id="event.scene_id" :label="`${call.purpose} · ${call.id}`" /></div>
          <h3>工具资料 <span>{{ relations.tool_results.length }}</span></h3><div class="inspector-links"><EntityLink v-for="result in relations.tool_results" :key="result.id" type="result" :id="result.id" :scene-id="event.scene_id" :label="`${result.tool_name} · ${result.id}`" /></div>
          <h3>原始事件 <span>{{ relations.events.length }}</span></h3><div class="inspector-links"><EntityLink v-for="item in relations.events" :key="item.id" type="event" :id="item.id" :scene-id="event.scene_id" :label="`${item.event_type} · ${item.id}`" /></div>
        </template>
        <v-expansion-panels variant="accordion" class="mt-5"><v-expansion-panel title="原始载荷与来源字段"><v-expansion-panel-text><ResourceViewer title="事件记录" :content="event" /></v-expansion-panel-text></v-expansion-panel></v-expansion-panels>
        <p class="muted-copy inspector-read">读取于 {{ fmtTime(readAt) }}。仅展示已保存的明确关联。</p>
      </template>
    </div>
  </section>
</template>

<style scoped>
.scene-inspector{min-width:0;background:rgb(var(--v-theme-surface));height:100%;display:flex;flex-direction:column}.inspector-header{display:flex;align-items:center;justify-content:space-between;padding:12px 16px;border-bottom:1px solid var(--line);gap:8px;flex:none}.inspector-header h2{font-size:17px;line-height:1.5}.inspector-body{padding:16px;min-width:0;overflow:auto}.inspector-identity{display:grid;gap:6px;margin-bottom:16px;min-width:0}.inspector-identity>span,.inspector-identity>time,.muted-copy{font-size:12px;color:var(--muted);line-height:1.7;overflow-wrap:anywhere}.inspector-identity strong{overflow-wrap:anywhere}.inspector-badges{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}.inspector-body h3{font-size:14px;margin:24px 0 10px}.inspector-body h3 span{color:var(--muted);font-weight:400;margin-left:4px}.inspector-body p{margin:10px 0;line-height:1.7}.inspector-links{display:grid;gap:10px;min-width:0;font-size:13px}.inspector-links>a{overflow-wrap:anywhere}.relation-record{padding:10px 0;border-bottom:1px solid var(--line);min-width:0}.preserve-lines{white-space:pre-wrap;overflow-wrap:anywhere}.breakable{overflow-wrap:anywhere;font-size:12px}.inspector-read{margin-top:20px!important}
</style>
