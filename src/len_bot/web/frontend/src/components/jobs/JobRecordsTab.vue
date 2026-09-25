<script setup>
import { computed } from 'vue'
import { withReturn } from '../../router/navigation.js'
import AnswerBasisDetails from '../AnswerBasisDetails.vue'
import EntityLink from '../EntityLink.vue'
import ExecutionDetails from '../ExecutionDetails.vue'
import OperationReceipts from '../OperationReceipts.vue'
import StatusBadge from '../StatusBadge.vue'
const props = defineProps({state: {type: Object, required: true}, page: {type: Object, required: true}})
const { job, jobId, route } = props.page
const { records, recordsLoading, recordsError } = props.state
const jobOperations = computed(() => (records.value?.operation_receipts || []).filter(item => item.kind==='work' && item.target_id===jobId.value))
const jobActions = computed(() => (records.value?.actions || []).filter(action => action.job_id === jobId.value || action.acknowledges_task_id === jobId.value || action.fulfils_task_id === jobId.value || action.operation_receipt?.kind==='work' && action.operation_receipt.target_id===jobId.value || [job.value?.ack_action_id,job.value?.delivery_action_id].includes(action.id)))
</script>
<template>
  <v-card-text class="detail-body">
    <h3>外部执行与停止事实</h3>
    <p class="muted-copy">工作取消不会替代容器停止回执。展开查看当次输入来源、错误与原事件；停止未知时需在执行服务核对，不在此重跑。</p>
    <ExecutionDetails
      v-for="run in job.executions || []"
      :key="run.execution_id"
      :run="run"
      :job-id="job.id"
      :scene-id="job.scene_id"
    />
    <p v-if="!job.executions?.length">没有已记录的外部执行；宿主 worker 的输入和输出仍沿原工具观察查看，不补造 Gateway 身份。</p>
    <v-progress-linear v-if="recordsLoading" indeterminate />
    <v-alert v-if="recordsError" type="error" variant="tonal">{{ recordsError }}</v-alert>
    <template v-if="records">
      <p class="muted-copy">只展示已持久化的明确关联。各类记录最多 50 项。</p>
      <v-alert
        v-if="Object.values(records.truncated).some(Boolean)"
        type="info"
        variant="tonal"
      >部分关联超出本页范围，可从对应对象继续查看。</v-alert>
      <OperationReceipts :items="jobOperations" :scene-id="job.scene_id" />
      <h3>表达行动与真实回执</h3>
      <article v-for="action in jobActions" :key="action.id" class="delivery-record">
        <code class="breakable">{{ action.id }}</code>
        <div class="status-line">
          <v-chip v-if="action.acknowledges_task_id" size="small" variant="tonal">创建确认</v-chip>
          <v-chip v-if="action.fulfils_task_id" size="small" variant="tonal">结果交付</v-chip>
          <v-chip v-if="action.operation_ref" size="small" variant="tonal">操作确认 {{ action.operation_ref }}
          </v-chip>
          <StatusBadge domain="delivery" :status="action.delivery_status" />
          <span v-if="action.job_revision">发送依据工作 v{{ action.job_revision }}</span>
        </div>
        <EntityLink
          v-if="action.origin_event_id"
          type="event"
          :id="action.origin_event_id"
          :scene-id="job.scene_id"
          label="本条表达对应的来源"
        />
        <div class="link-list">
          <EntityLink
            v-for="id in action.receipt_event_ids"
            :key="id"
            type="event"
            :id="id"
            :scene-id="job.scene_id"
            label="查看发送回执"
          />
        </div>
        <p v-if="!action.receipt_event_ids.length" class="muted-copy">这条行动尚无已保存回执。</p>
        <EntityLink
          v-if="action.file_asset_id"
          type="file"
          :id="action.file_asset_id"
          :job-id="job.id"
          :scene-id="job.scene_id"
          label="此文件资产的完整交付链路"
        />
        <p v-if="action.file_id">平台文件 ID：{{ action.file_id }}</p>
        <AnswerBasisDetails :basis="action.answer_basis" :scene-id="job.scene_id" />
      </article>
      <p v-if="!jobActions.length" class="muted-copy">未找到关联的表达行动。</p>
      <h3>执行轨迹</h3>
      <div class="link-list">
        <RouterLink
          v-for="item in records.traces"
          :key="item.id"
          :to="withReturn(route, { name: 'activity', query: { tab: 'turns', id: item.id, scene: job.scene_id } })"
        >
          {{ item.kind }} · {{ item.id }}<span v-if="item.tool_outcomes?.errors"> · {{ item.tool_outcomes.errors }} 条工具错误</span>
        </RouterLink>
      </div>
      <p v-if="!records.traces.length" class="muted-copy">没有关联轨迹。</p>
      <h3>模型请求</h3>
      <div class="link-list">
        <EntityLink
          v-for="item in records.calls"
          :key="item.id"
          type="call"
          :id="item.id"
          :scene-id="job.scene_id"
          :label="item.purpose + ' · ' + item.id"
        />
      </div>
      <h3>原始事件与回执</h3>
      <div class="link-list">
        <EntityLink
          v-for="item in records.events"
          :key="item.id"
          type="event"
          :id="item.id"
          :scene-id="job.scene_id"
          :label="item.event_type + ' · ' + item.id"
        />
      </div>
    </template>
  </v-card-text>
</template>
<style scoped>
.delivery-record,.adopted-range{display:grid;gap:10px;min-width:0;padding:14px 0;border-bottom:1px solid var(--line)}
.delivery-record .breakable{white-space:normal}
.field-label,.work-usage,.muted-copy,.read-time{font-size:13px;color:rgb(var(--v-theme-on-surface-variant))}
.identity-line,.detail-status,.action-row,.status-line{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.identity-line>*,.link-list>*{min-width:0;overflow-wrap:anywhere}
.detail-body{min-width:0;line-height:1.65}
.detail-body h3{font-size:17px;margin:24px 0 12px}
.detail-body h3:first-child{margin-top:0}
.detail-body p{margin:10px 0}
.link-list{display:grid;gap:10px}
.breakable{overflow-wrap:anywhere}
</style>
