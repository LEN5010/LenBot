<script setup>
import { computed } from 'vue'
import { fmtTime } from '../api.js'
import EntityLink from './EntityLink.vue'
import StatusBadge from './StatusBadge.vue'
const props = defineProps({
  files: { type: Array, required: true },
  sceneId: String,
  jobId: String,
  focusId: String
})
defineEmits(['clear-focus'])
const visible = computed(() => props.focusId ? props.files.filter(file => file.asset_id === props.focusId) : props.files)
const explanations = {
  prepared: '资产已登记；没有关联到上传提交或尝试，不表示已经发到群。',
  submitted: '已有持久提交，但该行动尚无上传尝试或终态记录；不据此判断队列是否仍在运行。',
  uploaded: '已有非模拟上传回执及平台 file_id；这是文件上传，不是文字通知或对方已阅读。',
  failed: '已有明确未上传或拒绝记录。资产保留，失败原因与对应行动分别列在下方。',
  unknown: '至少一条上传行动结局未确认，或回执缺少真实 file_id；不要重传或重新执行来补回执。',
  shadow: '只保存了 Shadow 观察，未据此确认平台上传。',
  simulated: '只保存了模拟回执，不能作为真实上传证据。',
}
const downloadUrl = file => `/api/cockpit/jobs/${encodeURIComponent(props.jobId)}/files/${encodeURIComponent(file.asset_id)}/download?scene_id=${encodeURIComponent(props.sceneId)}`
</script>

<template>
  <section v-if="files.length || focusId" class="file-assets" aria-label="文件资产与上传链路">
    <header class="file-heading">
      <h3>文件资产与上传链路</h3>
      <v-btn v-if="focusId" variant="text" size="small" @click="$emit('clear-focus')">查看此工作全部文件</v-btn>
    </header>
    <p class="muted-copy">生成、登记、提交、上传尝试和真实回执分别记录。下载只读取已保存副本，不会上传；过期与旧修订仍可回查，不因此取得新发送资格。</p>
    <v-alert v-if="focusId && !visible.length" type="warning" variant="tonal">当前工作没有关联到指定文件资产 {{ focusId }}，不按文件名或时间猜测其他资产。</v-alert>
    <article v-for="file in visible" :key="file.asset_id" class="file-card">
      <header class="file-heading">
        <h4>{{ file.display_name }}</h4>
        <StatusBadge domain="file_upload" :status="file.upload_state.status" />
      </header>
      <p>{{ explanations[file.upload_state.status] }}</p>
      <p v-if="file.upload_state.uploaded && file.upload_state.unknown" class="file-issue">另有已确认的上传成功记录，但它不能结清其他行动的未知尝试。</p>
      <div class="file-meta">
        <span>{{ file.size_bytes.toLocaleString() }} 字节 · {{ file.mime_type }}</span>
        <span>来源工作 v{{ file.job_revision }}</span>
        <v-chip v-if="!file.current_revision" size="small" color="warning" variant="tonal">旧工作修订</v-chip>
        <v-chip v-if="file.expired" size="small" color="warning" variant="tonal">上传有效期已过</v-chip>
      </div>
      <div class="file-meta">
        <span>登记 {{ fmtTime(file.created_at) }}</span>
        <span>有效至 {{ fmtTime(file.expires_at) }}</span>
        <span>状态采样 {{ fmtTime(file.sampled_at) }}</span>
      </div>
      <div class="file-links">
        <a
          class="v-btn v-btn--size-small v-btn--variant-outlined"
          :href="downloadUrl(file)"
          target="_blank"
          rel="noopener"
        >下载已保存资产</a>
        <EntityLink
          type="file"
          :id="file.asset_id"
          :job-id="jobId"
          :scene-id="sceneId"
          label="定位此资产"
        />
        <EntityLink
          v-if="file.review_trace_id"
          type="trace"
          :id="file.review_trace_id"
          :scene-id="sceneId"
          label="查看关联上传审查"
        />
      </div>
      <details>
        <summary>来源与提交、尝试、回执</summary>
        <dl class="file-facts">
          <dt>资产身份</dt>
          <dd>{{ file.asset_id }}</dd>
          <dt>原执行</dt>
          <dd>{{ file.execution_id }}</dd>
          <dt>不可变产物</dt>
          <dd>{{ file.artifact_id }}</dd>
          <dt>工作区相对文件</dt>
          <dd>{{ file.source_path }}</dd>
          <dt>审查身份</dt>
          <dd>{{ file.review_action_id || '未关联上传审查；不是上传许可' }}</dd>
        </dl>
        <p class="muted-copy">关联审查不代替发送时的当前授权、工作修订、期限和额度核对。上方执行身份可在本工作“执行记录”中回查。</p>
        <h5>持久提交</h5>
        <p v-if="!file.upload_submissions.length" class="muted-copy">未找到此资产的已保存表达提交。</p>
        <div
          v-for="submission in file.upload_submissions"
          :key="submission.event_id + ':' + submission.action_id"
          class="file-record"
        >
          <EntityLink type="event" :id="submission.event_id" :scene-id="sceneId" label="查看文件表达提交" />
          <span>工作 v{{ submission.job_revision ?? '未记录' }} · {{ fmtTime(submission.timestamp) }}
          </span>
          <span>行动 {{ submission.action_id || '未记录' }}</span>
        </div>
        <h5>上传尝试</h5>
        <p v-if="!file.upload_attempts.length" class="muted-copy">没有上传尝试记录；只有提交或资产不等于平台已接收。</p>
        <div v-for="attempt in file.upload_attempts" :key="attempt.event_id" class="file-record">
          <EntityLink type="event" :id="attempt.event_id" :scene-id="sceneId" label="查看上传尝试" />
          <span>{{ fmtTime(attempt.timestamp) }} · 行动 {{ attempt.action_id || '未记录' }}</span>
        </div>
        <h5>每条行动的当前已知结局</h5>
        <div
          v-for="action in file.upload_state.actions"
          :key="action.action_id"
          class="file-record"
        >
          <div class="file-meta">
            <StatusBadge domain="file_upload" :status="action.status" />
            <span>{{ action.action_id }}</span>
          </div>
          <EntityLink
            v-if="action.receipt_event_id"
            type="event"
            :id="action.receipt_event_id"
            :scene-id="sceneId"
            label="查看此行动的回执"
          />
        </div>
        <h5>原始回执与观察</h5>
        <p v-if="!file.upload_receipts.length" class="muted-copy">尚无上传回执；已登记的尝试仍保留未知结局。</p>
        <div v-for="receipt in file.upload_receipts" :key="receipt.event_id" class="file-record">
          <div class="file-meta">
            <StatusBadge domain="file_upload" :status="receipt.file_status" />
            <span>{{ fmtTime(receipt.timestamp) }}</span>
            <EntityLink type="event" :id="receipt.event_id" :scene-id="sceneId" label="读取原记录" />
          </div>
          <span>行动 {{ receipt.action_id || '未记录，不能关闭其他行动的尝试' }}</span>
          <span v-if="receipt.file_id">平台文件 ID：{{ receipt.file_id }}</span>
          <p v-if="receipt.error" class="file-issue">{{ receipt.error }}</p>
        </div>
      </details>
    </article>
  </section>
</template>

<style scoped>
.file-assets{display:grid;gap:14px;min-width:0;margin:24px 0}
.file-heading,.file-meta,.file-links{display:flex;align-items:center;flex-wrap:wrap;gap:10px 14px}
.file-heading{justify-content:space-between}
.file-heading h3,.file-heading h4{margin:0!important;min-width:0;overflow-wrap:anywhere}
.file-heading h4{font-size:16px}
.file-card{border:1px solid var(--line);border-radius:10px;padding:18px;min-width:0}
.file-card>p{white-space:pre-wrap;overflow-wrap:anywhere}
.file-meta{font-size:12px;color:var(--muted);margin-top:10px}
.file-meta span{overflow-wrap:anywhere}
.file-links{margin:14px 0}
.file-card details{margin-top:14px}
.file-card summary{cursor:pointer}
.file-facts{display:grid;grid-template-columns:130px minmax(0,1fr);gap:8px 14px;margin:16px 0;font-size:13px}
.file-facts dt{color:var(--muted)}
.file-facts dd{margin:0;overflow-wrap:anywhere}
.file-card h5{font-size:14px;margin:18px 0 10px}
.file-record{display:grid;gap:7px;padding:12px 0;border-bottom:1px solid var(--line);font-size:13px;min-width:0}
.file-record>span{overflow-wrap:anywhere}
.file-record .file-meta{margin:0}
.file-issue{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}
.muted-copy{color:var(--muted);font-size:13px;line-height:1.7}
@media(max-width:650px){
  .file-card{padding:14px}
  .file-facts{grid-template-columns:minmax(0,1fr);gap:3px}
  .file-facts dd{margin-bottom:10px}
  .file-links>a{width:100%}
}
</style>
