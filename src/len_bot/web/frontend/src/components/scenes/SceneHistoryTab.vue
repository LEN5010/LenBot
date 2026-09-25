<script setup>
import { fmtTime } from '../../api.js'
import EntityLink from '../EntityLink.vue'
import ResourceViewer from '../ResourceViewer.vue'
import StatusBadge from '../StatusBadge.vue'
const props = defineProps({state: {type: Object, required: true}, page: {type: Object, required: true}})
const { sceneId, aux, detail, expandedRecords } = props.page
const { retrySaving, askRetry } = props.state
</script>
<template>
  <h3>历史摘要覆盖</h3>
  <v-alert
    :type="detail.maintenance?.ready ? 'info' : 'warning'"
    variant="tonal"
    class="my-4"
  >
    {{ detail.maintenance?.reason }}
  </v-alert>
  <p class="muted-copy">摘要按原始范围保存，未成功的区间保留原文。摘要提供定位，工作和认识仍须实际读取证据。</p>
  <p v-if="detail.history_status.initial_history_boundary" class="muted-copy">初始历史边界 {{ detail.history_status.initial_history_boundary }}，边界之前的原文未据此标为已总结。</p>
  <p>尚未成功覆盖 {{ detail.history_status.unsuccessful_count }} 个批次</p>
  <article
    v-for="batch in aux?.items || []"
    :key="batch.id"
    :data-scene-record="'history:' + batch.id"
    tabindex="-1"
    class="detail-record"
  >
    <div class="record-meta">
      <StatusBadge domain="summary" :status="batch.status" />
      <strong>
        {{ batch.start_rowid }}:{{ batch.start_offset }} → {{ batch.end_rowid }}:{{ batch.end_offset }}
      </strong>
      <span>版本 {{ batch.generation_version }}</span>
      <time>{{ fmtTime(batch.completed_at || batch.created_at) }}</time>
    </div>
    <v-alert
      v-if="batch.sources_available === false"
      type="warning"
      variant="tonal"
      class="my-3"
    >本批次的原话来源不完整，或已不在原本群范围；摘要仅保留审计，不再进入新的摘要候选或请求。原完成状态不代表来源仍可读取。</v-alert>
    <p v-if="batch.error_type" class="error-copy">
      {{ batch.failure_detail || batch.error_type }} · 此区间尚未成功覆盖</p>
    <p class="muted-copy">
      {{ batch.candidate_review?.adopted_operations == null ? '本记录未提供已采用认识操作清单' : `本次提交采用 ${batch.candidate_review.adopted_operations.length} 条认识操作` }}；摘要覆盖与认识采用分别记录。</p>
    <v-alert
      v-if="batch.candidate_review?.status==='needs_review'"
      type="warning"
      variant="tonal"
      class="my-3"
    >摘要已保存；{{ batch.candidate_review.candidates.length }} 条认识候选因相关认识变化未采用，需人工核对。不会自动重跑维护。<EntityLink
        type="event"
        :id="batch.candidate_review.event_id"
        :scene-id="sceneId"
        label="查看候选、原版本与提交时版本"
      />
    </v-alert>
    <p v-else-if="batch.candidate_review?.status==='not_recorded'" class="muted-copy">此维护回执未单独记录候选冲突情况，不能据此认定全部采用。</p>
    <p class="two-lines">{{ batch.summary || '尚无摘要正文。' }}</p>
    <v-btn
      v-if="['failed', 'pending'].includes(batch.status)"
      variant="outlined"
      :disabled="!detail.maintenance?.ready || retrySaving"
      @click="askRetry(batch)"
    >重试此区间</v-btn>
    <v-expansion-panels
      v-model="expandedRecords['history:' + batch.id]"
      variant="accordion"
      class="mt-3"
    >
      <v-expansion-panel title="摘要全文、已采用操作与原文定位">
        <v-expansion-panel-text>
          <ResourceViewer title="完整摘要" :content="batch.summary" />
          <h4>本次提交采用的认识操作</h4>
          <p class="muted-copy">下列版本和状态属于当时的提交；打开认识详情查看当前修订链，不从摘要推断已经记住。</p>
          <div
            v-for="(receipt,index) in batch.candidate_review?.adopted_operations || []"
            :key="index"
            class="detail-record"
          >
            <div class="record-meta">
              <strong>
                {{ {create:'创建',refute:'撤销',supersede:'替代'}[receipt.operation] || receipt.operation }}
              </strong>
              <span>当时版本 {{ receipt.revision ?? '未记录' }}</span>
              <StatusBadge domain="memory" :status="receipt.status" />
            </div>
            <p class="two-lines">{{ receipt.statement }}</p>
            <EntityLink
              type="memory"
              :id="receipt.id"
              :scene-id="sceneId"
              label="查看当前认识与修订"
            />
          </div>
          <EntityLink
            v-if="batch.candidate_review?.event_id"
            type="event"
            :id="batch.candidate_review.event_id"
            :scene-id="sceneId"
            label="查看完整维护回执"
          />
          <h4>来源原话</h4>
          <div class="detail-links">
            <EntityLink
              v-for="id in batch.source_event_ids"
              :key="id"
              type="event"
              :id="id"
              :scene-id="sceneId"
            />
          </div>
          <h4>关键原话</h4>
          <div class="detail-links">
            <EntityLink
              v-for="id in batch.key_event_ids"
              :key="id"
              type="event"
              :id="id"
              :scene-id="sceneId"
            />
          </div>
        </v-expansion-panel-text>
      </v-expansion-panel>
    </v-expansion-panels>
  </article>
  <p v-if="aux && !aux.items.length" class="empty-copy">尚未生成历史摘要，原文保留。</p>
</template>
<style scoped>
.detail-record:focus-visible{outline:2px solid rgb(var(--v-theme-primary));outline-offset:4px}
.scene-detail-body h3{font-size:17px;margin:24px 0 10px;line-height:1.6}
.scene-detail-body h3:first-child{margin-top:0}
.scene-detail-body h4{font-size:14px;margin:16px 0 10px}
.scene-detail-body p{margin:10px 0;overflow-wrap:anywhere}
.muted-copy{font-size:13px;color:var(--muted);line-height:1.8}
.detail-record{padding:18px 0;border-bottom:1px solid var(--line);min-width:0}
.detail-record:last-child{border-bottom:0}
.record-meta{display:flex;gap:8px 12px;align-items:center;flex-wrap:wrap;font-size:12px;color:var(--muted)}
.record-meta>*{min-width:0}
.record-meta strong{color:var(--ink);overflow-wrap:anywhere}
.detail-links{display:grid;gap:10px;min-width:0}
.two-lines{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere;white-space:pre-wrap}
.error-copy{color:rgb(var(--v-theme-error));font-size:13px}
.empty-copy{padding:28px 16px;text-align:center;font-size:13px;color:var(--muted);line-height:1.8}
</style>
