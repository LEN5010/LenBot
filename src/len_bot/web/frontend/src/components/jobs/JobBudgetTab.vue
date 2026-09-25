<script setup>
import { computed } from 'vue'
import { fmtTime } from '../../api.js'
import { purposeLabel } from '../../domain/activity.js'
import CacheUsageSummary from '../CacheUsageSummary.vue'
import EntityLink from '../EntityLink.vue'
import ResourceViewer from '../ResourceViewer.vue'
import StatusBadge from '../StatusBadge.vue'
const props = defineProps({state: {type: Object, required: true}, page: {type: Object, required: true}})
const { job, openResource } = props.page
const { usage, usageLoading, usageError, usageReadAt, usagePage, loadUsage } = props.state
const limitText = value => value === undefined ? '未记录' : value === null ? '不设限' : value.toLocaleString()
const workBudget = computed(() => job.value?.budget.work_snapshot)
</script>
<template>
  <v-card-text class="detail-body">
    <h3>本工作累计用量与原上限</h3>
    <v-alert v-if="!workBudget" type="info" variant="tonal">
      {{ job.budget.work_snapshot_note }}
    </v-alert>
    <div class="budget-grid">
      <div>
        <span>模型额度计次 / 原次数上限</span>
        <strong>
          {{ job.model_steps }} / {{ limitText(workBudget?.max_model_steps) }}
        </strong>
      </div>
      <div>
        <span>工具累计计次 / 原次数上限</span>
        <strong>{{ job.tool_calls }} / {{ limitText(workBudget?.max_tool_calls) }}</strong>
      </div>
      <div><span>累计活动时长</span><strong>{{ job.elapsed_seconds.toFixed(1) }} 秒</strong></div>
    </div>
    <p>原时间窗口：{{ limitText(workBudget?.max_seconds) }} 秒；绝对期限：{{ workBudget?.deadline_at ? fmtTime(workBudget.deadline_at) : '未记录' }}。</p>
    <p>累计 token 上限：{{ limitText(workBudget?.token_limit) }}。</p>
    <p class="muted-copy">修订、暂停和恢复不重置累计账。有绝对期限时，等待和停机也计入窗口，不能用累计活动时长推算剩余时间。计次是准入账，不等于成功返回的请求数；实际请求见下方调用账。不设限仅指已明确记录为 null 的次数或 token 维度，不是免费或无限执行。</p>
    <details class="section-gap">
      <summary>原预算快照与账户记录</summary>
      <ResourceViewer
        title="本工作创建时的预算快照"
        :content="workBudget || job.budget.work_snapshot_note"
      />
      <ResourceViewer title="本工作账户预占与结算" :content="job.reservation || '未记录'" />
    </details>
    <h3>归属本工作的完整调用账</h3>
    <p class="muted-copy">按 job_id 读取所有目标版本，不受“执行记录”最多 50 项的范围限制。汇总按用途及处置分组，明细分页；压缩、方法整理与子调用均按原工作归属显示。</p>
    <v-progress-linear v-if="usageLoading" indeterminate aria-label="正在读取本工作调用账" />
    <v-alert v-if="usageError" type="error" variant="tonal">
      {{ usageError }}<v-btn variant="text" size="small" @click="loadUsage(usagePage)">重新读取账目</v-btn>
    </v-alert>
    <template v-if="usage">
      <CacheUsageSummary :cache="usage.cache" :phases="usage.request_phases" />
      <p class="muted-copy">共 {{ usage.total }} 条调用记录 · 读取于 {{ fmtTime(usageReadAt) }}。缓存是输入的子项，推理是输出的子项，不重复相加；未知 usage 不按零消耗或零成本处理。</p>
      <article
        v-for="group in usage.totals"
        :key="`${group.purpose}:${group.disposition}`"
        class="adopted-range"
      >
        <strong>
          {{ purposeLabel(group.purpose) }} · {{ group.disposition || '处置未记录' }} · {{ group.calls }} 次</strong>
        <p>已记录输入 {{ group.prompt_tokens.toLocaleString() }}（含已记录缓存 {{ group.cached_tokens.toLocaleString() }}）；已记录输出 {{ group.completion_tokens.toLocaleString() }}（含已记录推理 {{ group.reasoning_tokens.toLocaleString() }}）。</p>
        <p>usage 未完整记录 {{ group.unknown_usage }} 次；失败 {{ group.failed }}，取消 {{ group.cancelled }}，请求未确认 {{ group.unconfirmed }}。<template v-if="group.audio_seconds">另有转写时长 {{ group.audio_seconds }} 秒。</template>
        </p>
      </article>
      <p v-if="!usage.items.length" class="muted-copy">没有关联到该工作身份的模型调用记录；不能据此推断其他未关联请求没有发生。</p>
      <div class="link-list">
        <div v-for="call in usage.items" :key="call.id">
          <EntityLink
            type="call"
            :id="call.id"
            :scene-id="job.scene_id"
            :label="`${purposeLabel(call.purpose)} · ${call.id}`"
          />
          <StatusBadge domain="call" :status="call.status" />
          <span> · {{ fmtTime(call.started_at) }}</span>
        </div>
      </div>
      <v-pagination
        v-if="usage.total > usage.page_size"
        :model-value="usagePage"
        :length="Math.ceil(usage.total / usage.page_size)"
        :total-visible="5"
        :disabled="usageLoading"
        @update:model-value="loadUsage"
      />
    </template>
    <h3>固定模型绑定</h3>
    <p v-if="job.model_binding" class="breakable">
      {{ job.model_binding.provider_id }} / {{ job.model_binding.model }} · 推理 {{ job.model_binding.reasoning_effort || '模型默认' }}
    </p>
    <p v-else class="muted-copy">尚未绑定模型。</p>
    <h3>最后完整检查点</h3>
    <p v-if="job.checkpoint">
      {{ job.checkpoint.exchange_count }} 组工具交换 · 目标版本 {{ job.checkpoint.goal_revision }} · {{ fmtTime(job.checkpoint.updated_at) }}
    </p>
    <p v-else class="muted-copy">尚无完整检查点。</p>
    <h3>本工作上下文与已保存压缩</h3>
    <p>原有效输入 {{ limitText(workBudget?.effective_input_tokens) }} token；原总窗口 {{ limitText(workBudget?.context_tokens) }}，原输出预留 {{ limitText(workBudget?.output_tokens) }}。</p>
    <p>原维护窗口 {{ limitText(workBudget?.maintenance_context_tokens) }} token；原维护输出预留 {{ limitText(workBudget?.maintenance_output_tokens) }}。</p>
    <details class="section-gap">
      <summary>当前运行默认值（不替代旧工作的额度）</summary>
      <p>新工作模型 {{ limitText(job.budget.max_model_steps) }} 次、工具 {{ limitText(job.budget.max_tool_calls) }} 次、窗口 {{ limitText(job.budget.max_seconds) }} 秒；上下文 {{ limitText(job.budget.context_tokens) }} token，输出 {{ limitText(job.budget.output_tokens) }}。</p>
      <p>当前压缩触发比例 {{ Math.round(job.budget.compression_trigger * 100) }}%，目标比例 {{ Math.round(job.budget.compression_target * 100) }}%；历史请求的实际装配与比例以对应 Trace 为准。</p>
    </details>
    <template v-if="job.compression">
      <p>压缩原状态：{{ job.compression.status }}</p>
      <v-alert v-if="job.compression.error" type="error" variant="tonal">
        {{ job.compression.error }}
      </v-alert>
      <v-expansion-panels variant="accordion" class="mt-4">
        <v-expansion-panel
          v-for="(segment, index) in job.compression.segments"
          :key="index"
          :title="`工具交换 ${segment.start_exchange}—${segment.end_exchange}`"
        >
          <v-expansion-panel-text>
            <ResourceViewer title="区间摘要" :content="segment.summary" />
            <ul v-if="segment.unresolved">
              <li v-for="(item, itemIndex) in segment.unresolved" :key="itemIndex">
                {{ item }}
              </li>
            </ul>
            <div class="action-row">
              <v-btn
                v-for="id in segment.result_ids"
                :key="id"
                variant="text"
                @click="openResource(id)"
              >回读资料 · {{ id.slice(0, 10) }}
              </v-btn>
            </div>
          </v-expansion-panel-text>
        </v-expansion-panel>
      </v-expansion-panels>
    </template>
    <p v-else class="muted-copy">尚无已保存的压缩区间。</p>
  </v-card-text>
</template>
<style scoped>
.delivery-record,.adopted-range{display:grid;gap:10px;min-width:0;padding:14px 0;border-bottom:1px solid var(--line)}
.adopted-range .v-btn{justify-self:start}
.adopted-range p{margin:0;font-size:13px;overflow-wrap:anywhere}
.section-gap{margin-top:20px}
.field-label,.work-usage,.muted-copy,.read-time{font-size:13px;color:rgb(var(--v-theme-on-surface-variant))}
.identity-line,.detail-status,.action-row,.status-line{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.action-row{margin-top:16px}
.identity-line>*,.link-list>*{min-width:0;overflow-wrap:anywhere}
.detail-body{min-width:0;line-height:1.65}
.detail-body h3{font-size:17px;margin:24px 0 12px}
.detail-body h3:first-child{margin-top:0}
.detail-body ul,.detail-body ol{padding-left:24px;margin:8px 0}
.detail-body li{margin:8px 0;overflow-wrap:anywhere}
.detail-body p{margin:10px 0}
.link-list{display:grid;gap:10px}
.budget-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}
.budget-grid>div{padding:16px;background:rgb(var(--v-theme-surface-variant));border-radius:8px}
.budget-grid span,.budget-grid strong{display:block}
.budget-grid strong{font-size:20px;margin-top:8px}
.breakable{overflow-wrap:anywhere}
@media(max-width:650px){
  .budget-grid{grid-template-columns:1fr}
  .action-row>.v-btn{flex-grow:1}
}
</style>
