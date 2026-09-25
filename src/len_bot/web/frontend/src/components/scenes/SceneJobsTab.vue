<script setup>
import { fmtTime } from '../../api.js'
import StatusBadge from '../StatusBadge.vue'
const props = defineProps({page: {type: Object, required: true}})
const { sceneId, related, aux, auxError } = props.page
</script>
<template>
  <h3>本会话的工作与交付</h3>
  <p class="muted-copy">执行结果和发送回执分别记录。工作插件已开启不代表具备当前执行或上传资格。</p>
  <div class="scene-quick-links">
    <v-btn
      size="small"
      variant="tonal"
      :to="related({name:'jobs',query:{scene:sceneId}})"
    >筛选工作与交付</v-btn>
    <v-btn
      size="small"
      variant="text"
      :to="related({name:'tasks',query:{scene:sceneId}})"
    >提醒与等待</v-btn>
  </div>
  <article
    v-for="job in aux?.items || []"
    :key="job.id"
    :data-scene-record="'job:' + job.id"
    tabindex="-1"
    class="detail-record"
  >
    <RouterLink
      class="two-lines job-title"
      :to="related({ name: 'job', params: { jobId: job.id }, query: { scene: sceneId } })"
    >
      {{ job.goal }}
    </RouterLink>
    <div class="record-meta mt-3">
      <StatusBadge domain="job_execution" :status="job.execution_status" />
      <StatusBadge
        domain="job_delivery"
        :status="job.delivery_required === false ? 'not_required' : job.status"
      />
      <span>目标版本 {{ job.revision }}</span>
      <time>{{ fmtTime(job.updated_at) }}</time>
    </div>
  </article>
  <p v-if="aux && !aux.items.length && !auxError" class="empty-copy">暂无信息工作。</p>
</template>
<style scoped>
.detail-record:focus-visible{outline:2px solid rgb(var(--v-theme-primary));outline-offset:4px}
.scene-quick-links{display:flex;gap:8px;flex-wrap:wrap;margin-top:16px}
.scene-quick-links>.v-btn{max-width:100%}
@media(max-width:650px){
  .scene-quick-links{gap:4px}
}
.scene-detail-body h3{font-size:17px;margin:24px 0 10px;line-height:1.6}
.scene-detail-body h3:first-child{margin-top:0}
.scene-detail-body p{margin:10px 0;overflow-wrap:anywhere}
.muted-copy{font-size:13px;color:var(--muted);line-height:1.8}
.detail-record{padding:18px 0;border-bottom:1px solid var(--line);min-width:0}
.detail-record:last-child{border-bottom:0}
.record-meta{display:flex;gap:8px 12px;align-items:center;flex-wrap:wrap;font-size:12px;color:var(--muted)}
.record-meta>*{min-width:0}
.two-lines{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere;white-space:pre-wrap}
.job-title{font-weight:600;font-size:15px;line-height:1.7}
.empty-copy{padding:28px 16px;text-align:center;font-size:13px;color:var(--muted);line-height:1.8}
</style>
